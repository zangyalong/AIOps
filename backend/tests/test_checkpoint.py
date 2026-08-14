from __future__ import annotations

import unittest

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.types import Interrupt

from app.agents.checkpoint import checkpoint_connection_string


class CheckpointConfigurationTest(unittest.TestCase):
    def test_sqlalchemy_psycopg_url_is_converted_for_native_pool(self) -> None:
        result = checkpoint_connection_string()

        self.assertNotIn("+psycopg", result)
        self.assertTrue(result.startswith("postgresql://"))

    def test_strict_serializer_round_trips_only_required_graph_types(self) -> None:
        serializer = JsonPlusSerializer(allowed_msgpack_modules=None)
        state = {
            "messages": [
                HumanMessage("重启服务"),
                AIMessage(
                    "准备执行",
                    tool_calls=[
                        {
                            "name": "restart_service",
                            "args": {"server_name": "web-01"},
                            "id": "write-1",
                            "type": "tool_call",
                        }
                    ],
                ),
                ToolMessage("已拒绝", tool_call_id="write-1"),
            ],
            "interrupt": Interrupt(
                value={"actions": [{"tool_call_id": "write-1"}]},
                id="interrupt-1",
            ),
        }

        kind, payload = serializer.dumps_typed(state)
        restored = serializer.loads_typed((kind, payload))

        self.assertEqual(
            [type(message).__name__ for message in restored["messages"]],
            ["HumanMessage", "AIMessage", "ToolMessage"],
        )
        self.assertIsInstance(restored["interrupt"], Interrupt)
        self.assertEqual(
            restored["interrupt"].value["actions"][0]["tool_call_id"],
            "write-1",
        )
