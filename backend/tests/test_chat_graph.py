from __future__ import annotations

import unittest
from collections.abc import Callable

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableLambda
from langchain_core.tools import tool
from langgraph.types import Command

import app.agents.graphs.chat_graph as chat_graph


def _initial_state(*, allow_write_tools: bool) -> dict:
    return {
        "messages": [HumanMessage("检查测试服务器")],
        "work_messages": [],
        "reports": {},
        "specialist_instruction": "",
        "tool_iterations": 0,
        "supervisor_rounds": 0,
        "allow_write_tools": allow_write_tools,
        "approval": None,
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


class SupervisorGraphTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.original_base = chat_graph._base_llm
        self.original_specialist = chat_graph._specialist_llm
        self.original_metrics_tools = chat_graph.SPECIALIST_TOOLS["metrics_agent"]

    def tearDown(self) -> None:
        chat_graph._base_llm = self.original_base
        chat_graph._specialist_llm = self.original_specialist
        chat_graph.SPECIALIST_TOOLS["metrics_agent"] = self.original_metrics_tools

    async def test_supervisor_delegates_to_specialist_and_synthesizes(self) -> None:
        """父图 handoff 到指标子图，工具结果和报告都能正确合并回父图。"""
        chat_graph.SPECIALIST_TOOLS["metrics_agent"] = [_fake_query_server_metrics]
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
                            "name": "run_whitelisted_command",
                            "args": {"server_name": "demo", "command": "uptime"},
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

        result = await graph.ainvoke(
            Command(resume={"approved": False, "reason": "测试拒绝"}),
            config=config,
        )

        self.assertEqual(result["messages"][-1].content, "最终综合回答")
        self.assertTrue(
            any(
                isinstance(message, ToolMessage) and "拒绝" in str(message.content)
                for message in result["messages"]
            )
        )

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
                            "name": "run_whitelisted_command",
                            "args": {"server_name": "demo", "command": "uptime"},
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
