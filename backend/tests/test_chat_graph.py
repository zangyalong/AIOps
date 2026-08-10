from __future__ import annotations

import unittest
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableLambda
from langchain_core.tools import tool
from langgraph.types import Command

from app.agents.graphs import chat_graph
from app.operations.schemas import OperationResult


class _SessionContext:
    async def __aenter__(self) -> AsyncMock:
        return AsyncMock()

    async def __aexit__(self, *args: object) -> None:
        del args


def _initial_state(*, allow_write_tools: bool) -> dict:
    operation_context = (
        {
            "actor_id": "test-admin",
            "actor_role": "admin",
            "run_mode": "interactive",
            "environment": "unknown",
            "auto_approve_non_production_medium": False,
        }
        if allow_write_tools
        else {
            "actor_id": "test-worker",
            "actor_role": "system",
            "run_mode": "unattended",
            "environment": "production",
            "auto_approve_non_production_medium": False,
        }
    )
    return {
        "messages": [HumanMessage("检查测试服务器")],
        "work_messages": [],
        "reports": {},
        "specialist_instruction": "",
        "tool_iterations": 0,
        "supervisor_rounds": 0,
        "allow_write_tools": allow_write_tools,
        "approval": None,
        "operation_context": operation_context,
    }


class _FakeBaseLLM:
    """按预设路线模拟 Supervisor，并记录最终综合模型的调用次数。"""

    def __init__(self, routes: list[str]) -> None:
        self.routes = iter(routes)
        self.synthesis_calls = 0

    def with_structured_output(self, schema: type, method: str) -> RunnableLambda:
        del method
        if schema is not chat_graph.SupervisorDecision:
            raise AssertionError(f"不应再调用额外的结构化质检模型：{schema}")

        async def invoke(_: object) -> object:
            route = next(self.routes)
            return chat_graph.SupervisorDecision(
                next_agent=route,
                instruction="执行测试调查",
            )

        return RunnableLambda(invoke)

    async def ainvoke(self, _: object) -> AIMessage:
        self.synthesis_calls += 1
        return AIMessage("最终综合回答")


def _fake_specialist_factory(
    responses: list[AIMessage],
) -> Callable[[str, str, bool], RunnableLambda]:
    items = iter(responses)

    def factory(_: str, __: str, ___: bool) -> RunnableLambda:
        async def invoke(_: object) -> AIMessage:
            return next(items)

        return RunnableLambda(invoke)

    return factory


@tool("query_server_metrics")
async def _fake_query_server_metrics(server: str, metric: str) -> str:
    """返回不访问 Prometheus 的测试指标。"""
    return f"{server}: {metric}=12%"


@tool("run_diagnostic_command")
async def _fake_run_diagnostic_command(server_name: str, command: str) -> str:
    """返回不访问 SSH 的只读诊断结果。"""
    return f"{server_name}: {command}: ok"


@tool("run_root_read_command")
async def _fake_run_root_read_command(
    server_name: str,
    command: str,
) -> OperationResult:
    """返回带退出元数据、但不访问 SSH 的 root 只读结果。"""
    return OperationResult.success(
        f"{server_name}: {command}: root-read-ok",
        metadata={"exit_code": 0, "timed_out": False},
    )


_write_calls: list[tuple[str, str]] = []
_catalog_write_calls: list[tuple[str, int, str]] = []


@tool("restart_service")
async def _fake_restart_service(server_name: str, service_name: str) -> str:
    """记录写工具是否真的越过审批门执行。"""
    _write_calls.append((server_name, service_name))
    return f"已重启 {server_name} 上的 {service_name}"


@tool("restart_service")
async def _fake_failed_restart_service(
    server_name: str, service_name: str
) -> OperationResult:
    """返回结构化失败，验证图和审计不会把普通错误文本记成成功。"""
    return OperationResult.failure(
        f"重启 {server_name} 上的 {service_name} 失败", code="gateway_failed"
    )


@tool("discover_server_services")
async def _fake_failing_discovery(server_name: str) -> str:
    """模拟一个抛异常、但不能破坏同批其他工具协议的读 Tool。"""
    raise RuntimeError(f"{server_name} discovery failed")


@tool("register_server_services")
async def _fake_register_server_services(
    server_name: str,
    services: list[dict],
    environment: str = "prod",
) -> str:
    """记录审批后的批量目录写入是否继续执行。"""
    _catalog_write_calls.append((server_name, len(services), environment))
    return "目录注册完成"


class SupervisorGraphTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.original_base = chat_graph._base_llm
        self.original_specialist = chat_graph._specialist_llm
        self.original_metrics_tools = chat_graph.SPECIALIST_READ_TOOLS["metrics_agent"]
        self.original_infra_read_tools = chat_graph.SPECIALIST_READ_TOOLS[
            "infrastructure_agent"
        ]
        self.original_infra_write_tools = chat_graph.SPECIALIST_WRITE_TOOLS[
            "infrastructure_agent"
        ]
        _write_calls.clear()
        _catalog_write_calls.clear()

    def tearDown(self) -> None:
        chat_graph._base_llm = self.original_base
        chat_graph._specialist_llm = self.original_specialist
        chat_graph.SPECIALIST_READ_TOOLS["metrics_agent"] = self.original_metrics_tools
        chat_graph.SPECIALIST_READ_TOOLS["infrastructure_agent"] = (
            self.original_infra_read_tools
        )
        chat_graph.SPECIALIST_WRITE_TOOLS["infrastructure_agent"] = (
            self.original_infra_write_tools
        )

    async def test_supervisor_delegates_to_specialist_and_synthesizes(self) -> None:
        """父图 handoff 到指标子图，工具结果和报告都能正确合并回父图。"""
        chat_graph.SPECIALIST_READ_TOOLS["metrics_agent"] = [_fake_query_server_metrics]
        base = _FakeBaseLLM(["metrics_agent", "finish"])
        chat_graph._base_llm = lambda: base
        chat_graph._specialist_llm = _fake_specialist_factory(
            [
                AIMessage(
                    "",
                    tool_calls=[
                        {
                            "name": "query_server_metrics",
                            "args": {"server": "demo", "metric": "cpu"},
                            "id": "metrics-call",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage("指标证据报告"),
            ]
        )

        graph = chat_graph.build_chat_graph()
        result = await graph.ainvoke(
            _initial_state(allow_write_tools=False),
            config={"configurable": {"thread_id": "metrics-test"}},
        )

        self.assertEqual(result["reports"]["metrics_agent"], "指标证据报告")
        self.assertEqual(result["messages"][-1].content, "最终综合回答")
        self.assertEqual(base.synthesis_calls, 1)
        self.assertEqual(len(result["messages"]), 4)
        self.assertEqual(len(result["work_messages"]), 4)
        self.assertTrue(
            any(
                isinstance(message, ToolMessage) and "cpu=12%" in str(message.content)
                for message in result["messages"]
            )
        )

    async def test_write_tool_interrupts_and_rejection_does_not_execute(self) -> None:
        """SSH 工具必须先挂起；拒绝恢复后只产生拒绝 ToolMessage，不触碰真实 SSH。"""
        base = _FakeBaseLLM(["infrastructure_agent", "finish"])
        chat_graph._base_llm = lambda: base
        chat_graph._specialist_llm = _fake_specialist_factory(
            [
                AIMessage(
                    "",
                    tool_calls=[
                        {
                            "name": "restart_service",
                            "args": {"server_name": "demo", "service_name": "nginx"},
                            "id": "ssh-call",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage("命令被用户拒绝，未执行"),
            ]
        )

        graph = chat_graph.build_chat_graph()
        config = {"configurable": {"thread_id": "approval-test"}}
        await graph.ainvoke(_initial_state(allow_write_tools=True), config=config)
        snapshot = await graph.aget_state(config)
        self.assertTrue(snapshot.interrupts)
        self.assertEqual(_write_calls, [])

        result = await graph.ainvoke(
            Command(
                resume={
                    "decisions": {
                        "ssh-call": {
                            "approved": False,
                            "reason": "测试拒绝",
                            "approver_id": "approver-1",
                            "approver_role": "admin",
                        }
                    }
                }
            ),
            config=config,
        )

        self.assertEqual(result["messages"][-1].content, "最终综合回答")
        self.assertTrue(
            any(
                isinstance(message, ToolMessage) and "拒绝" in str(message.content)
                for message in result["messages"]
            )
        )
        self.assertEqual(_write_calls, [])

    async def test_service_registration_interrupts_with_catalog_details(self) -> None:
        """服务目录注册必须停在审批门，卡片要列出服务器、环境和运行时映射。"""
        base = _FakeBaseLLM(["infrastructure_agent"])
        chat_graph._base_llm = lambda: base
        chat_graph._specialist_llm = _fake_specialist_factory(
            [
                AIMessage(
                    "",
                    tool_calls=[
                        {
                            "name": "register_server_services",
                            "args": {
                                "server_name": "web01",
                                "environment": "prod",
                                "services": [
                                    {
                                        "service_name": "uvicorn",
                                        "runtime_type": "process",
                                        "runtime_name": "uvicorn:tcp-8010",
                                    }
                                ],
                            },
                            "id": "catalog-registration",
                            "type": "tool_call",
                        }
                    ],
                )
            ]
        )

        graph = chat_graph.build_chat_graph()
        config = {"configurable": {"thread_id": "catalog-approval-test"}}
        run_id = uuid.uuid4()
        state = _initial_state(allow_write_tools=True)
        state["operation_run_id"] = str(run_id)
        operation_id = uuid.uuid4()
        ensure_pending = AsyncMock(
            return_value=SimpleNamespace(
                id=operation_id,
                request_hash="a" * 64,
                expires_at=datetime.now(UTC) + timedelta(minutes=15),
            )
        )
        with (
            patch.object(
                chat_graph,
                "AsyncSessionLocal",
                return_value=_SessionContext(),
            ),
            patch.object(
                chat_graph.operation_record_service,
                "ensure_pending",
                ensure_pending,
            ),
        ):
            await graph.ainvoke(state, config=config)
        snapshot = await graph.aget_state(config)

        self.assertTrue(snapshot.interrupts)
        action = snapshot.interrupts[0].value["actions"][0]
        self.assertEqual(action["kind"], "service_catalog_registration")
        self.assertEqual(action["server_name"], "web01")
        self.assertEqual(action["environment"], "prod")
        self.assertIn("uvicorn ← process/uvicorn:tcp-8010", action["command"])
        self.assertEqual(action["operation_id"], str(operation_id))
        self.assertEqual(action["request_hash"], "a" * 64)
        ensure_pending.assert_awaited_once()

    async def test_unattended_worker_cannot_reach_write_approval(self) -> None:
        """即使假模型越权提出 SSH，allow_write_tools=False 也会在工具层硬拒绝且不挂起。"""
        base = _FakeBaseLLM(["infrastructure_agent", "finish"])
        chat_graph._base_llm = lambda: base
        chat_graph._specialist_llm = _fake_specialist_factory(
            [
                AIMessage(
                    "",
                    tool_calls=[
                        {
                            "name": "restart_service",
                            "args": {"server_name": "demo", "service_name": "nginx"},
                            "id": "blocked-ssh-call",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage("无人值守写工具被安全策略拒绝"),
            ]
        )

        graph = chat_graph.build_chat_graph()
        config = {"configurable": {"thread_id": "unattended-test"}}
        result = await graph.ainvoke(
            _initial_state(allow_write_tools=False), config=config
        )
        snapshot = await graph.aget_state(config)

        self.assertFalse(snapshot.interrupts)
        self.assertTrue(
            any(
                isinstance(message, ToolMessage)
                and "无人值守任务禁止" in str(message.content)
                for message in result["messages"]
            )
        )
        self.assertEqual(_write_calls, [])

    async def test_read_diagnostic_tool_does_not_interrupt(self) -> None:
        """只读 SSH 诊断直接执行，不应误入写操作审批门。"""
        chat_graph.SPECIALIST_READ_TOOLS["infrastructure_agent"] = [
            _fake_run_diagnostic_command
        ]
        base = _FakeBaseLLM(["infrastructure_agent", "finish"])
        chat_graph._base_llm = lambda: base
        chat_graph._specialist_llm = _fake_specialist_factory(
            [
                AIMessage(
                    "",
                    tool_calls=[
                        {
                            "name": "run_diagnostic_command",
                            "args": {"server_name": "demo", "command": "uptime"},
                            "id": "diagnostic-call",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage("只读诊断完成"),
            ]
        )

        graph = chat_graph.build_chat_graph()
        config = {"configurable": {"thread_id": "read-tool-test"}}
        result = await graph.ainvoke(
            _initial_state(allow_write_tools=True), config=config
        )
        snapshot = await graph.aget_state(config)

        self.assertFalse(snapshot.interrupts)
        self.assertTrue(
            any(
                isinstance(message, ToolMessage)
                and "uptime: ok" in str(message.content)
                for message in result["messages"]
            )
        )

    async def test_root_read_does_not_interrupt_but_is_audited(self) -> None:
        """root-read 无需审批，但必须先落审计，完成后写入退出元数据。"""
        chat_graph.SPECIALIST_READ_TOOLS["infrastructure_agent"] = [
            _fake_run_root_read_command
        ]
        base = _FakeBaseLLM(["infrastructure_agent", "finish"])
        chat_graph._base_llm = lambda: base
        chat_graph._specialist_llm = _fake_specialist_factory(
            [
                AIMessage(
                    "",
                    tool_calls=[
                        {
                            "name": "run_root_read_command",
                            "args": {
                                "server_name": "demo",
                                "command": "rg redis /host/etc",
                            },
                            "id": "root-read-call",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage("root 只读证据已获取"),
            ]
        )

        state = _initial_state(allow_write_tools=True)
        state["operation_run_id"] = str(uuid.uuid4())
        begin = AsyncMock(return_value=SimpleNamespace(status="executing"))
        finish = AsyncMock()
        with (
            patch.object(
                chat_graph,
                "AsyncSessionLocal",
                return_value=_SessionContext(),
            ),
            patch.object(
                chat_graph.operation_record_service,
                "begin_read_execution",
                begin,
            ),
            patch.object(
                chat_graph.operation_record_service,
                "finish_execution",
                finish,
            ),
        ):
            graph = chat_graph.build_chat_graph()
            result = await graph.ainvoke(
                state,
                config={"configurable": {"thread_id": "root-read-test"}},
            )

        self.assertFalse(
            (
                await graph.aget_state(
                    {"configurable": {"thread_id": "root-read-test"}}
                )
            ).interrupts
        )
        self.assertTrue(
            any(
                isinstance(message, ToolMessage)
                and "root-read-ok" in str(message.content)
                for message in result["messages"]
            )
        )
        begin.assert_awaited_once()
        finish.assert_awaited_once()
        self.assertEqual(
            finish.await_args.kwargs["metadata"],
            {"exit_code": 0, "timed_out": False},
        )

    async def test_approved_write_tool_executes_once(self) -> None:
        """写工具在批准前不执行，批准恢复后只执行一次。"""
        chat_graph.SPECIALIST_WRITE_TOOLS["infrastructure_agent"] = [
            _fake_restart_service
        ]
        base = _FakeBaseLLM(["infrastructure_agent", "finish"])
        chat_graph._base_llm = lambda: base
        chat_graph._specialist_llm = _fake_specialist_factory(
            [
                AIMessage(
                    "",
                    tool_calls=[
                        {
                            "name": "restart_service",
                            "args": {"server_name": "demo", "service_name": "nginx"},
                            "id": "approved-restart",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage("服务重启完成"),
            ]
        )

        graph = chat_graph.build_chat_graph()
        config = {"configurable": {"thread_id": "approved-write-test"}}
        await graph.ainvoke(_initial_state(allow_write_tools=True), config=config)
        self.assertEqual(_write_calls, [])

        await graph.ainvoke(
            Command(
                resume={
                    "decisions": {
                        "approved-restart": {
                            "approved": True,
                            "second_confirmation": True,
                            "reason": "测试批准",
                            "approver_id": "approver-1",
                            "approver_role": "admin",
                        }
                    }
                }
            ),
            config=config,
        )
        self.assertEqual(_write_calls, [("demo", "nginx")])

    async def test_persisted_write_is_authorized_before_executor_and_finished(
        self,
    ) -> None:
        chat_graph.SPECIALIST_WRITE_TOOLS["infrastructure_agent"] = [
            _fake_restart_service
        ]
        run_id = uuid.uuid4()
        ensure_pending = AsyncMock()
        authorize = AsyncMock(return_value=SimpleNamespace(status="executing"))
        finish = AsyncMock()
        state = _initial_state(allow_write_tools=True)
        state.update(
            {
                "work_messages": [
                    AIMessage(
                        "",
                        tool_calls=[
                            {
                                "name": "restart_service",
                                "args": {
                                    "server_name": "demo",
                                    "service_name": "nginx",
                                },
                                "id": "persisted-restart",
                                "type": "tool_call",
                            }
                        ],
                    )
                ],
                "approval": {
                    "decisions": {
                        "persisted-restart": {
                            "approved": True,
                            "second_confirmation": True,
                            "reason": "值班员确认",
                            "approver_id": "approver-1",
                            "approver_role": "admin",
                        }
                    }
                },
                "operation_run_id": str(run_id),
            }
        )

        with (
            patch.object(
                chat_graph,
                "AsyncSessionLocal",
                return_value=_SessionContext(),
            ),
            patch.object(
                chat_graph.operation_record_service,
                "ensure_pending",
                ensure_pending,
            ),
            patch.object(
                chat_graph.operation_record_service,
                "authorize_execution",
                authorize,
            ),
            patch.object(
                chat_graph.operation_record_service,
                "finish_execution",
                finish,
            ),
        ):
            result = await chat_graph.specialist_tools_node(
                "infrastructure_agent",
                state,
            )

        self.assertEqual(_write_calls, [("demo", "nginx")])
        ensure_pending.assert_awaited_once()
        authorize.assert_awaited_once()
        finish.assert_awaited_once()
        self.assertEqual(result["messages"][0].status, "success")

    async def test_auto_approved_non_production_write_keeps_audit_flow(self) -> None:
        """自动放行只跳过人工中断，仍要冻结请求、授权并完成审计。"""
        chat_graph.SPECIALIST_WRITE_TOOLS["infrastructure_agent"] = [
            _fake_register_server_services
        ]
        run_id = uuid.uuid4()
        state = _initial_state(allow_write_tools=True)
        state["operation_context"]["auto_approve_non_production_medium"] = True
        state.update(
            {
                "work_messages": [
                    AIMessage(
                        "",
                        tool_calls=[
                            {
                                "name": "register_server_services",
                                "args": {
                                    "server_name": "demo",
                                    "services": [],
                                    "environment": "dev",
                                },
                                "id": "auto-approved-register",
                                "type": "tool_call",
                            }
                        ],
                    )
                ],
                "operation_run_id": str(run_id),
            }
        )
        authorize = AsyncMock(return_value=SimpleNamespace(status="executing"))
        finish = AsyncMock()

        async def ensure_pending(*_args: object, **kwargs: object) -> None:
            prepared = kwargs["prepared"]
            self.assertEqual(prepared.decision.result, "allow")
            self.assertIsNotNone(prepared.approval_view)

        self.assertEqual(chat_graph.route_after_specialist(state), "tools")
        with (
            patch.object(
                chat_graph,
                "AsyncSessionLocal",
                return_value=_SessionContext(),
            ),
            patch.object(
                chat_graph.operation_record_service,
                "ensure_pending",
                side_effect=ensure_pending,
            ) as pending,
            patch.object(
                chat_graph.operation_record_service,
                "authorize_execution",
                authorize,
            ),
            patch.object(
                chat_graph.operation_record_service,
                "finish_execution",
                finish,
            ),
        ):
            result = await chat_graph.specialist_tools_node(
                "infrastructure_agent",
                state,
            )

        self.assertEqual(result["messages"][0].status, "success")
        self.assertEqual(_catalog_write_calls, [("demo", 0, "dev")])
        pending.assert_awaited_once()
        authorize.assert_awaited_once()
        finish.assert_awaited_once()

    async def test_structured_executor_failure_marks_tool_and_audit_failed(
        self,
    ) -> None:
        chat_graph.SPECIALIST_WRITE_TOOLS["infrastructure_agent"] = [
            _fake_failed_restart_service
        ]
        run_id = uuid.uuid4()
        state = _initial_state(allow_write_tools=True)
        state.update(
            {
                "work_messages": [
                    AIMessage(
                        "",
                        tool_calls=[
                            {
                                "name": "restart_service",
                                "args": {
                                    "server_name": "demo",
                                    "service_name": "nginx.service",
                                },
                                "id": "failed-restart",
                                "type": "tool_call",
                            }
                        ],
                    )
                ],
                "approval": {
                    "decisions": {
                        "failed-restart": {
                            "approved": True,
                            "second_confirmation": True,
                            "approver_id": "approver-1",
                            "approver_role": "admin",
                        }
                    }
                },
                "operation_run_id": str(run_id),
            }
        )
        finish = AsyncMock()
        with (
            patch.object(
                chat_graph,
                "AsyncSessionLocal",
                return_value=_SessionContext(),
            ),
            patch.object(chat_graph.operation_record_service, "ensure_pending"),
            patch.object(
                chat_graph.operation_record_service,
                "authorize_execution",
                AsyncMock(return_value=SimpleNamespace(status="executing")),
            ),
            patch.object(
                chat_graph.operation_record_service,
                "finish_execution",
                finish,
            ),
        ):
            result = await chat_graph.specialist_tools_node(
                "infrastructure_agent",
                state,
            )

        self.assertEqual(result["messages"][0].status, "error")
        self.assertIn("重启 demo", str(result["messages"][0].content))
        self.assertEqual(
            finish.await_args.kwargs["error"], "重启 demo 上的 nginx.service 失败"
        )
        self.assertIsNone(finish.await_args.kwargs["result"])

    async def test_high_risk_write_rejects_missing_second_confirmation(self) -> None:
        """即使第一次批准，高风险写工具没有详情弹窗二次确认也不能执行。"""
        chat_graph.SPECIALIST_WRITE_TOOLS["infrastructure_agent"] = [
            _fake_restart_service
        ]
        result = await chat_graph.specialist_tools_node(
            "infrastructure_agent",
            {
                "messages": [],
                "work_messages": [
                    AIMessage(
                        "",
                        tool_calls=[
                            {
                                "name": "restart_service",
                                "args": {
                                    "server_name": "demo",
                                    "service_name": "nginx.service",
                                },
                                "id": "missing-second-confirmation",
                                "type": "tool_call",
                            }
                        ],
                    )
                ],
                "reports": {},
                "specialist_instruction": "",
                "tool_iterations": 0,
                "allow_write_tools": True,
                "operation_context": _initial_state(allow_write_tools=True)[
                    "operation_context"
                ],
                "approval": {
                    "decisions": {
                        "missing-second-confirmation": {
                            "approved": True,
                            "reason": "只做了第一次确认",
                            "approver_id": "approver-1",
                            "approver_role": "admin",
                        }
                    }
                },
            },
        )

        self.assertIn("缺少详情弹窗二次确认", str(result["messages"][0].content))
        self.assertEqual(_write_calls, [])

    async def test_batch_write_calls_use_their_own_approval_decisions(self) -> None:
        """同批多个写操作可以混合批准/拒绝，批准一项不能放行另一项。"""
        chat_graph.SPECIALIST_WRITE_TOOLS["infrastructure_agent"] = [
            _fake_restart_service
        ]
        result = await chat_graph.specialist_tools_node(
            "infrastructure_agent",
            {
                "messages": [],
                "work_messages": [
                    AIMessage(
                        "",
                        tool_calls=[
                            {
                                "name": "restart_service",
                                "args": {
                                    "server_name": "demo",
                                    "service_name": "approved.service",
                                },
                                "id": "approved-write",
                                "type": "tool_call",
                            },
                            {
                                "name": "restart_service",
                                "args": {
                                    "server_name": "demo",
                                    "service_name": "rejected.service",
                                },
                                "id": "rejected-write",
                                "type": "tool_call",
                            },
                        ],
                    )
                ],
                "reports": {},
                "specialist_instruction": "",
                "tool_iterations": 0,
                "allow_write_tools": True,
                "operation_context": _initial_state(allow_write_tools=True)[
                    "operation_context"
                ],
                "approval": {
                    "decisions": {
                        "approved-write": {
                            "approved": True,
                            "second_confirmation": True,
                            "approver_id": "approver-1",
                            "approver_role": "admin",
                        },
                        "rejected-write": {
                            "approved": False,
                            "reason": "只批准第一项",
                            "approver_id": "approver-1",
                            "approver_role": "admin",
                        },
                    }
                },
            },
        )

        self.assertEqual(_write_calls, [("demo", "approved.service")])
        self.assertEqual(result["messages"][0].status, "success")
        self.assertEqual(result["messages"][1].status, "error")
        self.assertIn("只批准第一项", str(result["messages"][1].content))

    async def test_write_tool_node_rejects_missing_approval_decision(self) -> None:
        """即使绕过图路由直达执行节点，没有审批决议也必须硬拒绝。"""
        chat_graph.SPECIALIST_WRITE_TOOLS["infrastructure_agent"] = [
            _fake_restart_service
        ]
        result = await chat_graph.specialist_tools_node(
            "infrastructure_agent",
            {
                "messages": [],
                "work_messages": [
                    AIMessage(
                        "",
                        tool_calls=[
                            {
                                "name": "restart_service",
                                "args": {
                                    "server_name": "demo",
                                    "service_name": "nginx",
                                },
                                "id": "missing-approval",
                                "type": "tool_call",
                            }
                        ],
                    )
                ],
                "reports": {},
                "specialist_instruction": "",
                "tool_iterations": 0,
                "allow_write_tools": True,
                "approval": None,
            },
        )

        self.assertIn("无人值守", str(result["messages"][0].content))
        self.assertEqual(_write_calls, [])

    async def test_flat_legacy_approval_cannot_bypass_per_operation_binding(
        self,
    ) -> None:
        chat_graph.SPECIALIST_WRITE_TOOLS["infrastructure_agent"] = [
            _fake_restart_service
        ]
        state = _initial_state(allow_write_tools=True)
        state.update(
            {
                "work_messages": [
                    AIMessage(
                        "",
                        tool_calls=[
                            {
                                "name": "restart_service",
                                "args": {
                                    "server_name": "demo",
                                    "service_name": "nginx.service",
                                },
                                "id": "legacy-flat-approval",
                                "type": "tool_call",
                            }
                        ],
                    )
                ],
                "approval": {
                    "approved": True,
                    "second_confirmation": True,
                    "approver_id": "approver-1",
                    "approver_role": "admin",
                },
            }
        )

        result = await chat_graph.specialist_tools_node(
            "infrastructure_agent",
            state,
        )

        self.assertEqual(result["messages"][0].status, "error")
        self.assertIn("缺少人工审批决议", str(result["messages"][0].content))
        self.assertEqual(_write_calls, [])

    async def test_tool_exception_still_completes_every_tool_call_id(self) -> None:
        """同批某个 Tool 抛错时也要逐一回消息，不能留下 DeepSeek 无法接受的残缺协议。"""
        chat_graph.SPECIALIST_READ_TOOLS["infrastructure_agent"] = [
            _fake_failing_discovery
        ]
        chat_graph.SPECIALIST_WRITE_TOOLS["infrastructure_agent"] = [
            _fake_register_server_services
        ]
        result = await chat_graph.specialist_tools_node(
            "infrastructure_agent",
            {
                "messages": [],
                "work_messages": [
                    AIMessage(
                        "",
                        tool_calls=[
                            {
                                "name": "discover_server_services",
                                "args": {"server_name": "web01"},
                                "id": "read-call",
                                "type": "tool_call",
                            },
                            {
                                "name": "register_server_services",
                                "args": {
                                    "server_name": "web01",
                                    "environment": "prod",
                                    "services": [
                                        {
                                            "service_name": "uvicorn",
                                            "runtime_type": "process",
                                            "runtime_name": "uvicorn:tcp-8010",
                                        }
                                    ],
                                },
                                "id": "write-call",
                                "type": "tool_call",
                            },
                        ],
                    )
                ],
                "reports": {},
                "specialist_instruction": "",
                "tool_iterations": 0,
                "allow_write_tools": True,
                "operation_context": _initial_state(allow_write_tools=True)[
                    "operation_context"
                ],
                "approval": {
                    "decisions": {
                        "write-call": {
                            "approved": True,
                            "approver_id": "approver-1",
                            "approver_role": "admin",
                        }
                    }
                },
            },
        )

        messages = result["messages"]
        self.assertEqual(
            [message.tool_call_id for message in messages],
            ["read-call", "write-call"],
        )
        self.assertEqual([message.status for message in messages], ["error", "success"])
        self.assertIn("RuntimeError", str(messages[0].content))
        self.assertEqual(_catalog_write_calls, [("web01", 1, "prod")])

    def test_duplicate_tool_call_ids_are_normalized(self) -> None:
        message = AIMessage(
            "",
            tool_calls=[
                {
                    "name": "query_servers",
                    "args": {},
                    "id": "duplicate",
                    "type": "tool_call",
                },
                {
                    "name": "discover_server_services",
                    "args": {"server_name": "web01"},
                    "id": "duplicate",
                    "type": "tool_call",
                },
            ],
        )

        normalized = chat_graph._normalize_tool_call_ids(message)

        ids = [call["id"] for call in normalized.tool_calls]
        self.assertEqual(ids[0], "duplicate")
        self.assertNotEqual(ids[0], ids[1])

    def test_missing_tail_tool_result_gets_failure_repair(self) -> None:
        messages = [
            AIMessage(
                "",
                tool_calls=[
                    {
                        "name": "query_servers",
                        "args": {},
                        "id": "complete-call",
                        "type": "tool_call",
                    },
                    {
                        "name": "register_server_services",
                        "args": {},
                        "id": "missing-call",
                        "type": "tool_call",
                    },
                ],
            ),
            ToolMessage(
                "查询完成",
                tool_call_id="complete-call",
                name="query_servers",
            ),
        ]

        repairs = chat_graph._trailing_protocol_repairs(messages)

        self.assertEqual(len(repairs), 1)
        self.assertEqual(repairs[0].tool_call_id, "missing-call")
        self.assertEqual(repairs[0].status, "error")
        self.assertIn("不要假定操作成功", str(repairs[0].content))
