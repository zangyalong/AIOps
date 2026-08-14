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


def _initial_state(
    *,
    allow_write_tools: bool,
    with_run_id: bool = False,
) -> dict:
    execution_id = str(uuid.uuid4()) if with_run_id else None
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
        "allow_write_tools": allow_write_tools,
        "approval": None,
        "operation_execution_id": execution_id,
        "operation_chat_run_id": (
            execution_id if with_run_id and allow_write_tools else None
        ),
        "operation_context": operation_context,
    }


def _fake_ops_factory(
    responses: list[AIMessage],
    calls: list[bool] | None = None,
) -> Callable[[bool], RunnableLambda]:
    items = iter(responses)

    def factory(allow_write_tools: bool) -> RunnableLambda:
        if calls is not None:
            calls.append(allow_write_tools)

        async def invoke(_: object) -> AIMessage:
            return next(items)

        return RunnableLambda(invoke)

    return factory


@tool("investigate")
async def _fake_investigate(
    area: str,
    target: str = "",
    query: str = "",
    parameters: dict | None = None,
) -> OperationResult:
    """返回不访问外部数据源的统一调查证据。"""
    del parameters
    return OperationResult.success(
        f"{area}:{target}:{query}:ok",
        metadata={
            "exit_code": 0,
            "timed_out": False,
            "evidence_source": area,
        },
    )


@tool("investigate")
async def _sometimes_failing_investigate(
    area: str,
    target: str = "",
    query: str = "",
    parameters: dict | None = None,
) -> str:
    """第一类请求抛错，其余请求成功，用于验证工具协议完整性。"""
    del target, parameters
    if query == "fail":
        raise RuntimeError(f"{area} failed")
    return f"{area}:{query}:ok"


_write_calls: list[tuple[str, str]] = []
_catalog_write_calls: list[tuple[str, int, str]] = []
_file_access_grants: list[tuple[str, str, str]] = []


@tool("restart_service")
async def _fake_restart_service(
    server_name: str,
    service_name: str,
    runtime_type: str = "systemd",
) -> str:
    """记录写工具是否真的越过审批门执行。"""
    del runtime_type
    _write_calls.append((server_name, service_name))
    return f"已重启 {server_name} 上的 {service_name}"


@tool("restart_service")
async def _fake_failed_restart_service(
    server_name: str,
    service_name: str,
    runtime_type: str = "systemd",
) -> OperationResult:
    """返回结构化失败，验证审计不会误记成功。"""
    del runtime_type
    return OperationResult.failure(
        f"重启 {server_name} 上的 {service_name} 失败",
        code="gateway_failed",
    )


@tool("register_server_services")
async def _fake_register_server_services(
    server_name: str,
    services: list[dict],
    environment: str = "prod",
) -> str:
    """记录审批后的服务目录写入。"""
    _catalog_write_calls.append((server_name, len(services), environment))
    return "目录注册完成"


@tool("grant_server_file_write_access")
async def _fake_grant_server_file_write_access(
    server_name: str,
    service_name: str,
    path: str,
    environment: str = "",
    instance_name: str = "",
) -> str:
    """记录批准后的精确文件写授权。"""
    del environment, instance_name
    _file_access_grants.append((server_name, service_name, path))
    return f"已授权精确写入 {path}"


class OpsAgentGraphTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.original_ops_llm = chat_graph._ops_llm
        self.original_read_tools = chat_graph.OPS_READ_TOOLS
        self.original_write_tools = chat_graph.OPS_WRITE_TOOLS
        chat_graph.OPS_READ_TOOLS = (_fake_investigate,)
        chat_graph.OPS_WRITE_TOOLS = (
            _fake_restart_service,
            _fake_register_server_services,
            _fake_grant_server_file_write_access,
        )
        _write_calls.clear()
        _catalog_write_calls.clear()
        _file_access_grants.clear()

    def tearDown(self) -> None:
        chat_graph._ops_llm = self.original_ops_llm
        chat_graph.OPS_READ_TOOLS = self.original_read_tools
        chat_graph.OPS_WRITE_TOOLS = self.original_write_tools

    async def test_more_than_default_recursion_limit_can_finish_normally(self) -> None:
        model_calls: list[bool] = []
        responses = [
            AIMessage(
                f"核对第 {index} 项证据。",
                tool_calls=[
                    {
                        "name": "investigate",
                        "args": {
                            "area": "host",
                            "target": "web-01",
                            "query": f"check-{index}",
                        },
                        "id": f"investigate-{index}",
                        "type": "tool_call",
                    }
                ],
            )
            for index in range(1, 14)
        ]
        responses.append(AIMessage("十三项证据均已核对完成。"))
        chat_graph._ops_llm = _fake_ops_factory(
            responses,
            model_calls,
        )
        graph = chat_graph.build_chat_graph()
        with (
            patch.object(
                chat_graph, "AsyncSessionLocal", return_value=_SessionContext()
            ),
            patch.object(
                chat_graph.operation_record_service,
                "begin_read_execution",
                AsyncMock(return_value=SimpleNamespace(status="executing")),
            ),
            patch.object(
                chat_graph.operation_record_service,
                "finish_execution",
                AsyncMock(),
            ),
        ):
            result = await graph.ainvoke(
                _initial_state(allow_write_tools=False, with_run_id=True),
                config=chat_graph.graph_run_config("more-than-default-limit"),
            )

        self.assertEqual(result["messages"][-1].content, "十三项证据均已核对完成。")
        self.assertEqual(model_calls, [False] * 14)
        self.assertEqual(
            sum(isinstance(message, ToolMessage) for message in result["messages"]),
            13,
        )

    async def test_single_agent_investigates_then_answers_without_handoff(self) -> None:
        model_calls: list[bool] = []
        chat_graph._ops_llm = _fake_ops_factory(
            [
                AIMessage(
                    "我先核对 web-01 的真实 Python 版本。",
                    tool_calls=[
                        {
                            "name": "investigate",
                            "args": {
                                "area": "host",
                                "target": "web-01",
                                "query": "python --version",
                            },
                            "id": "investigate-python",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage("web-01 的 Python 版本已经核验。"),
            ],
            model_calls,
        )

        graph = chat_graph.build_chat_graph()
        with (
            patch.object(
                chat_graph, "AsyncSessionLocal", return_value=_SessionContext()
            ),
            patch.object(
                chat_graph.operation_record_service,
                "begin_read_execution",
                AsyncMock(return_value=SimpleNamespace(status="executing")),
            ),
            patch.object(
                chat_graph.operation_record_service,
                "finish_execution",
                AsyncMock(),
            ),
        ):
            result = await graph.ainvoke(
                _initial_state(allow_write_tools=False, with_run_id=True),
                config={"configurable": {"thread_id": "single-agent-read"}},
            )

        self.assertEqual(
            result["messages"][-1].content, "web-01 的 Python 版本已经核验。"
        )
        self.assertEqual(model_calls, [False, False])
        self.assertNotIn("reports", result)
        self.assertNotIn("supervisor_rounds", result)
        self.assertTrue(
            any(
                isinstance(message, ToolMessage)
                and "host:web-01:python --version:ok" in str(message.content)
                for message in result["messages"]
            )
        )

    async def test_final_answer_restores_current_turn_tool_url_before_persistence(
        self,
    ) -> None:
        original = (
            "http://localhost:3000/explore?schemaVersion=1&"
            "panes=%7B%22aiops%22%3A%7B%22datasource%22%3A%22prometheus%22%7D%7D"
            "&orgId=1"
        )
        chat_graph._ops_llm = _fake_ops_factory(
            [
                AIMessage(
                    "图表入口：[查看]"
                    "(http://localhost:3000/explore?schemaVersion=1&panes=%7B...orgId=1)"
                )
            ]
        )
        state = _initial_state(allow_write_tools=False)
        state["messages"].extend(
            [
                AIMessage(
                    "生成观测链接。",
                    tool_calls=[
                        {
                            "name": "investigate",
                            "args": {
                                "area": "visualization",
                                "target": "web-01",
                                "query": "memory",
                            },
                            "id": "visualization-link",
                            "type": "tool_call",
                        }
                    ],
                ),
                ToolMessage(
                    f"已生成 Grafana 指标走势图链接：\n{original}",
                    tool_call_id="visualization-link",
                    name="investigate",
                ),
            ]
        )

        result = await chat_graph.ops_agent_node(state)

        self.assertEqual(
            result["messages"][-1].content,
            f"图表入口：[打开原始链接]({original})",
        )

    async def test_investigation_never_interrupts_and_is_audited(self) -> None:
        state = _initial_state(allow_write_tools=True, with_run_id=True)
        state["messages"].append(
            AIMessage(
                "",
                tool_calls=[
                    {
                        "name": "investigate",
                        "args": {
                            "area": "runtime",
                            "target": "web-01",
                            "query": "overview",
                        },
                        "id": "investigate-runtime",
                        "type": "tool_call",
                    }
                ],
            )
        )
        begin = AsyncMock(return_value=SimpleNamespace(status="executing"))
        finish = AsyncMock()
        with (
            patch.object(
                chat_graph, "AsyncSessionLocal", return_value=_SessionContext()
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
            result = await chat_graph.tools_node(state)

        self.assertEqual(result["messages"][0].status, "success")
        begin.assert_awaited_once()
        finish.assert_awaited_once()
        self.assertEqual(
            begin.await_args.kwargs["execution_id"],
            uuid.UUID(state["operation_execution_id"]),
        )
        self.assertEqual(
            begin.await_args.kwargs["chat_run_id"],
            uuid.UUID(state["operation_chat_run_id"]),
        )
        self.assertEqual(
            finish.await_args.kwargs["metadata"],
            {
                "exit_code": 0,
                "timed_out": False,
                "evidence_source": "runtime",
            },
        )

    async def test_write_interrupts_and_rejection_never_executes(self) -> None:
        chat_graph._ops_llm = _fake_ops_factory(
            [
                AIMessage(
                    "准备按你的要求重启目标服务。",
                    tool_calls=[
                        {
                            "name": "restart_service",
                            "args": {
                                "server_name": "demo",
                                "service_name": "nginx.service",
                            },
                            "id": "rejected-restart",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage("操作已拒绝，服务未重启。"),
            ]
        )
        state = _initial_state(allow_write_tools=True, with_run_id=True)
        record = SimpleNamespace(
            id=uuid.uuid4(),
            request_hash="a" * 64,
            expires_at=datetime.now(UTC) + timedelta(minutes=15),
            status="denied",
        )
        ensure_pending = AsyncMock(return_value=record)
        authorize = AsyncMock(return_value=record)

        graph = chat_graph.build_chat_graph()
        config = {"configurable": {"thread_id": "rejected-write"}}
        with (
            patch.object(
                chat_graph, "AsyncSessionLocal", return_value=_SessionContext()
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
        ):
            await graph.ainvoke(state, config=config)
            snapshot = await graph.aget_state(config)
            self.assertTrue(snapshot.interrupts)
            result = await graph.ainvoke(
                Command(
                    resume={
                        "decisions": {
                            "rejected-restart": {
                                "approved": False,
                                "reason": "值班员拒绝",
                                "approver_id": "approver-1",
                                "approver_role": "admin",
                            }
                        }
                    }
                ),
                config=config,
            )

        self.assertEqual(_write_calls, [])
        self.assertEqual(result["messages"][-1].content, "操作已拒绝，服务未重启。")
        self.assertTrue(
            any(
                isinstance(message, ToolMessage)
                and "值班员拒绝" in str(message.content)
                for message in result["messages"]
            )
        )

    async def test_approved_write_executes_once_and_finishes_audit(self) -> None:
        chat_graph._ops_llm = _fake_ops_factory(
            [
                AIMessage(
                    "目标已核对，等待你确认重启。",
                    tool_calls=[
                        {
                            "name": "restart_service",
                            "args": {
                                "server_name": "demo",
                                "service_name": "nginx.service",
                            },
                            "id": "approved-restart",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage("nginx.service 已重启。"),
            ]
        )
        state = _initial_state(allow_write_tools=True, with_run_id=True)
        record = SimpleNamespace(
            id=uuid.uuid4(),
            request_hash="b" * 64,
            expires_at=datetime.now(UTC) + timedelta(minutes=15),
            status="executing",
        )
        ensure_pending = AsyncMock(return_value=record)
        authorize = AsyncMock(return_value=record)
        finish = AsyncMock()

        graph = chat_graph.build_chat_graph()
        config = {"configurable": {"thread_id": "approved-write"}}
        with (
            patch.object(
                chat_graph, "AsyncSessionLocal", return_value=_SessionContext()
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
            await graph.ainvoke(state, config=config)
            await graph.ainvoke(
                Command(
                    resume={
                        "decisions": {
                            "approved-restart": {
                                "approved": True,
                                "second_confirmation": True,
                                "reason": "值班员确认",
                                "approver_id": "approver-1",
                                "approver_role": "admin",
                            }
                        }
                    }
                ),
                config=config,
            )

        self.assertEqual(_write_calls, [("demo", "nginx.service")])
        authorize.assert_awaited_once()
        finish.assert_awaited_once()

    async def test_approved_file_grant_returns_to_preview_and_finishes(self) -> None:
        path = "/home/example-app/deploy/docker-compose.yml"
        chat_graph._ops_llm = _fake_ops_factory(
            [
                AIMessage(
                    "该文件尚未授权，我先提交精确写授权审批。",
                    tool_calls=[
                        {
                            "name": "grant_server_file_write_access",
                            "args": {
                                "server_name": "web-01",
                                "service_name": "example-api",
                                "environment": "prod",
                                "instance_name": "example-api",
                                "path": path,
                            },
                            "id": "grant-example-api-compose",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(
                    "授权已生效，我重新生成文件修改预览。",
                    tool_calls=[
                        {
                            "name": "investigate",
                            "args": {
                                "area": "change",
                                "target": "web-01",
                                "query": "file-update-preview",
                                "parameters": {
                                    "path": path,
                                    "new_content": "services: {}\n",
                                },
                            },
                            "id": "preview-example-api-compose",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage("写授权已生效，文件修改预览已经生成。"),
            ]
        )
        state = _initial_state(allow_write_tools=True, with_run_id=True)
        record = SimpleNamespace(
            id=uuid.uuid4(),
            request_hash="d" * 64,
            expires_at=datetime.now(UTC) + timedelta(minutes=15),
            status="executing",
        )
        graph = chat_graph.build_chat_graph()
        config = chat_graph.graph_run_config("grant-then-preview")
        with (
            patch.object(
                chat_graph, "AsyncSessionLocal", return_value=_SessionContext()
            ),
            patch.object(
                chat_graph.operation_record_service,
                "ensure_pending",
                AsyncMock(return_value=record),
            ),
            patch.object(
                chat_graph.operation_record_service,
                "authorize_execution",
                AsyncMock(return_value=record),
            ),
            patch.object(
                chat_graph.operation_record_service,
                "begin_read_execution",
                AsyncMock(return_value=record),
            ),
            patch.object(
                chat_graph.operation_record_service,
                "finish_execution",
                AsyncMock(),
            ),
        ):
            await graph.ainvoke(state, config=config)
            snapshot = await graph.aget_state(config)
            self.assertTrue(snapshot.interrupts)
            result = await graph.ainvoke(
                Command(
                    resume={
                        "decisions": {
                            "grant-example-api-compose": {
                                "approved": True,
                                "approver_id": "approver-1",
                                "approver_role": "admin",
                            }
                        }
                    }
                ),
                config=config,
            )

        self.assertEqual(_file_access_grants, [("web-01", "example-api", path)])
        self.assertEqual(
            result["messages"][-1].content,
            "写授权已生效，文件修改预览已经生成。",
        )
        self.assertTrue(
            any(
                isinstance(message, ToolMessage)
                and message.tool_call_id == "preview-example-api-compose"
                for message in result["messages"]
            )
        )

    async def test_unattended_mode_hard_rejects_write_tool(self) -> None:
        state = _initial_state(allow_write_tools=False)
        state["messages"].append(
            AIMessage(
                "",
                tool_calls=[
                    {
                        "name": "restart_service",
                        "args": {
                            "server_name": "demo",
                            "service_name": "nginx.service",
                        },
                        "id": "unattended-write",
                        "type": "tool_call",
                    }
                ],
            )
        )

        result = await chat_graph.tools_node(state)

        self.assertEqual(result["messages"][0].status, "error")
        self.assertIn("无权调用", str(result["messages"][0].content))
        self.assertEqual(_write_calls, [])

    async def test_write_without_persisted_run_fails_closed(self) -> None:
        state = _initial_state(allow_write_tools=True)
        state["messages"].append(
            AIMessage(
                "",
                tool_calls=[
                    {
                        "name": "restart_service",
                        "args": {
                            "server_name": "demo",
                            "service_name": "nginx.service",
                        },
                        "id": "missing-run",
                        "type": "tool_call",
                    }
                ],
            )
        )
        state["approval"] = {
            "decisions": {
                "missing-run": {
                    "approved": True,
                    "second_confirmation": True,
                    "approver_id": "approver-1",
                    "approver_role": "admin",
                }
            }
        }

        result = await chat_graph.tools_node(state)

        self.assertIn("缺少持久化聊天运行标识", str(result["messages"][0].content))
        self.assertEqual(_write_calls, [])

    async def test_high_risk_write_requires_second_confirmation(self) -> None:
        state = _initial_state(allow_write_tools=True, with_run_id=True)
        state["messages"].append(
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
        )
        state["approval"] = {
            "decisions": {
                "missing-second-confirmation": {
                    "approved": True,
                    "approver_id": "approver-1",
                    "approver_role": "admin",
                }
            }
        }
        authorize = AsyncMock(return_value=SimpleNamespace(status="denied"))
        with (
            patch.object(
                chat_graph, "AsyncSessionLocal", return_value=_SessionContext()
            ),
            patch.object(chat_graph.operation_record_service, "ensure_pending"),
            patch.object(
                chat_graph.operation_record_service,
                "authorize_execution",
                authorize,
            ),
        ):
            result = await chat_graph.tools_node(state)

        self.assertIn("缺少详情弹窗二次确认", str(result["messages"][0].content))
        self.assertEqual(_write_calls, [])

    async def test_each_write_uses_its_own_approval_decision(self) -> None:
        state = _initial_state(allow_write_tools=True, with_run_id=True)
        state["messages"].append(
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
        )
        state["approval"] = {
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
        }

        async def authorize(*_: object, **kwargs: object) -> SimpleNamespace:
            prepared = kwargs["prepared"]
            return SimpleNamespace(
                status=(
                    "executing" if prepared.decision.result == "allow" else "denied"
                )
            )

        with (
            patch.object(
                chat_graph, "AsyncSessionLocal", return_value=_SessionContext()
            ),
            patch.object(chat_graph.operation_record_service, "ensure_pending"),
            patch.object(
                chat_graph.operation_record_service,
                "authorize_execution",
                side_effect=authorize,
            ),
            patch.object(chat_graph.operation_record_service, "finish_execution"),
        ):
            result = await chat_graph.tools_node(state)

        self.assertEqual(_write_calls, [("demo", "approved.service")])
        self.assertEqual(
            [message.status for message in result["messages"]],
            ["success", "error"],
        )
        self.assertIn("只批准第一项", str(result["messages"][1].content))

    async def test_structured_executor_failure_marks_audit_failed(self) -> None:
        chat_graph.OPS_WRITE_TOOLS = (_fake_failed_restart_service,)
        state = _initial_state(allow_write_tools=True, with_run_id=True)
        state["messages"].append(
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
        )
        state["approval"] = {
            "decisions": {
                "failed-restart": {
                    "approved": True,
                    "second_confirmation": True,
                    "approver_id": "approver-1",
                    "approver_role": "admin",
                }
            }
        }
        finish = AsyncMock()
        with (
            patch.object(
                chat_graph, "AsyncSessionLocal", return_value=_SessionContext()
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
            result = await chat_graph.tools_node(state)

        self.assertEqual(result["messages"][0].status, "error")
        self.assertEqual(
            finish.await_args.kwargs["error"],
            "重启 demo 上的 nginx.service 失败",
        )
        self.assertIsNone(finish.await_args.kwargs["result"])

    async def test_tool_exception_completes_every_tool_call_id(self) -> None:
        chat_graph.OPS_READ_TOOLS = (_sometimes_failing_investigate,)
        state = _initial_state(allow_write_tools=False, with_run_id=True)
        state["messages"].append(
            AIMessage(
                "",
                tool_calls=[
                    {
                        "name": "investigate",
                        "args": {"area": "host", "query": "fail"},
                        "id": "failed-read",
                        "type": "tool_call",
                    },
                    {
                        "name": "investigate",
                        "args": {"area": "metrics", "query": "up"},
                        "id": "successful-read",
                        "type": "tool_call",
                    },
                ],
            )
        )

        with (
            patch.object(
                chat_graph, "AsyncSessionLocal", return_value=_SessionContext()
            ),
            patch.object(
                chat_graph.operation_record_service,
                "begin_read_execution",
                AsyncMock(return_value=SimpleNamespace(status="executing")),
            ) as begin,
            patch.object(
                chat_graph.operation_record_service,
                "finish_execution",
                AsyncMock(),
            ),
        ):
            result = await chat_graph.tools_node(state)

        self.assertEqual(
            [message.tool_call_id for message in result["messages"]],
            ["failed-read", "successful-read"],
        )
        self.assertEqual(
            [message.status for message in result["messages"]],
            ["error", "success"],
        )
        self.assertEqual(begin.await_count, 2)
        self.assertTrue(
            all(call.kwargs["chat_run_id"] is None for call in begin.await_args_list)
        )

    def test_duplicate_tool_call_ids_are_normalized(self) -> None:
        message = AIMessage(
            "",
            tool_calls=[
                {
                    "name": "investigate",
                    "args": {"area": "servers"},
                    "id": "duplicate",
                    "type": "tool_call",
                },
                {
                    "name": "investigate",
                    "args": {"area": "alerts"},
                    "id": "duplicate",
                    "type": "tool_call",
                },
            ],
        )

        normalized = chat_graph._normalize_tool_call_ids(message)

        ids = [call["id"] for call in normalized.tool_calls]
        self.assertEqual(ids[0], "duplicate")
        self.assertNotEqual(ids[0], ids[1])
        self.assertTrue(ids[1].startswith("call_aiops_"))

    def test_repeated_invalid_investigation_is_stopped_before_execution(self) -> None:
        history = [
            AIMessage(
                "查询服务器。",
                tool_calls=[
                    {
                        "name": "investigate",
                        "args": {"area": "servers"},
                        "id": "servers-1",
                        "type": "tool_call",
                    }
                ],
            ),
            ToolMessage(
                "调查请求不完整：servers 不支持 parameters.at。",
                tool_call_id="servers-1",
                name="investigate",
                status="error",
            ),
        ]
        repeated = AIMessage(
            "调整调用方式。",
            tool_calls=[
                {
                    "name": "investigate",
                    "args": {"area": "servers"},
                    "id": "servers-2",
                    "type": "tool_call",
                }
            ],
        )

        stopped = chat_graph._stop_repeated_invalid_investigation(repeated, history)

        self.assertEqual(stopped.tool_calls, [])
        self.assertIn("系统已停止重复执行", stopped.content)

    def test_corrected_investigation_can_run_after_parameter_error(self) -> None:
        history = [
            AIMessage(
                "查询指标。",
                tool_calls=[
                    {
                        "name": "investigate",
                        "args": {
                            "area": "metrics",
                            "target": "web-01",
                            "query": "memory",
                            "parameters": {"time": "1h"},
                        },
                        "id": "metrics-1",
                        "type": "tool_call",
                    }
                ],
            ),
            ToolMessage(
                "调查请求不完整：metrics 不支持 parameters.time。",
                tool_call_id="metrics-1",
                name="investigate",
                status="error",
            ),
        ]
        corrected = AIMessage(
            "改用趋势图协议。",
            tool_calls=[
                {
                    "name": "investigate",
                    "args": {
                        "area": "visualization",
                        "target": "web-01",
                        "query": "memory",
                        "parameters": {"kind": "metrics", "minutes": 60},
                    },
                    "id": "metrics-2",
                    "type": "tool_call",
                }
            ],
        )

        result = chat_graph._stop_repeated_invalid_investigation(corrected, history)

        self.assertEqual(result.tool_calls, corrected.tool_calls)

    def test_missing_tail_tool_result_gets_failure_repair(self) -> None:
        repairs = chat_graph._trailing_protocol_repairs(
            [
                HumanMessage("测试"),
                AIMessage(
                    "",
                    tool_calls=[
                        {
                            "name": "investigate",
                            "args": {"area": "servers"},
                            "id": "missing-result",
                            "type": "tool_call",
                        }
                    ],
                ),
            ]
        )

        self.assertEqual(len(repairs), 1)
        self.assertEqual(repairs[0].tool_call_id, "missing-result")
        self.assertEqual(repairs[0].status, "error")

    async def test_service_registration_card_keeps_frozen_details(self) -> None:
        chat_graph._ops_llm = _fake_ops_factory(
            [
                AIMessage(
                    "准备登记已经发现的运行对象。",
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
        state = _initial_state(allow_write_tools=True, with_run_id=True)
        record = SimpleNamespace(
            id=uuid.uuid4(),
            request_hash="c" * 64,
            expires_at=datetime.now(UTC) + timedelta(minutes=15),
        )
        with (
            patch.object(
                chat_graph, "AsyncSessionLocal", return_value=_SessionContext()
            ),
            patch.object(
                chat_graph.operation_record_service,
                "ensure_pending",
                AsyncMock(return_value=record),
            ),
        ):
            graph = chat_graph.build_chat_graph()
            config = {"configurable": {"thread_id": "catalog-card"}}
            await graph.ainvoke(state, config=config)
            snapshot = await graph.aget_state(config)

        action = snapshot.interrupts[0].value["actions"][0]
        self.assertEqual(action["kind"], "service_catalog_registration")
        self.assertEqual(action["server_name"], "web01")
        self.assertIn("uvicorn ← process/uvicorn:tcp-8010", action["command"])
        self.assertEqual(action["operation_id"], str(record.id))


if __name__ == "__main__":
    unittest.main()
