from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from pydantic import ValidationError

from app.agents.tools import root_read
from app.operations.schemas import OperationResult


class _SessionContext:
    async def __aenter__(self) -> object:
        return object()

    async def __aexit__(self, *args: object) -> None:
        del args


class _FakeConnection:
    def __init__(self, response: dict[str, object] | str) -> None:
        self.response = response
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def run(self, command: str, **kwargs: object) -> SimpleNamespace:
        self.calls.append((command, kwargs))
        stdout = (
            self.response
            if isinstance(self.response, str)
            else json.dumps(self.response, ensure_ascii=False)
        )
        return SimpleNamespace(exit_status=0, stdout=stdout, stderr="")


class _ConnectionContext:
    def __init__(self, connection: _FakeConnection) -> None:
        self.connection = connection

    async def __aenter__(self) -> _FakeConnection:
        return self.connection

    async def __aexit__(self, *args: object) -> None:
        del args


class RootReadInputTest(unittest.TestCase):
    def test_accepts_shell_composition_without_semantic_allowlist(self) -> None:
        for command in (
            'find /host/www -type f | xargs rg "redis"',
            "cat /host/etc/nginx/nginx.conf",
            "rm /host/etc/hosts",
            "systemctl restart nginx",
        ):
            with self.subTest(command=command):
                parsed = root_read.RootReadInput(
                    server_name="web-01",
                    command=command,
                )
                self.assertEqual(parsed.command, command)

    def test_rejects_control_characters_and_oversized_commands(self) -> None:
        for command in ("cat /host/etc/hosts\x00", "id\rwhoami", "id\x1b"):
            with self.subTest(command=command), self.assertRaises(ValidationError):
                root_read.RootReadInput(server_name="web-01", command=command)
        with self.assertRaises(ValidationError):
            root_read.RootReadInput(
                server_name="web-01",
                command="x" * (root_read.MAX_COMMAND_CHARS + 1),
            )


class RootReadExecutionTest(unittest.IsolatedAsyncioTestCase):
    async def _invoke(
        self,
        response: dict[str, object] | str,
        *,
        host: bool = False,
    ) -> tuple[OperationResult, _FakeConnection]:
        server = SimpleNamespace(name="web-01", ip_address="127.0.0.1")
        params = SimpleNamespace(
            host="127.0.0.1",
            port=22,
            username="aiops",
            client_keys=["test-key"],
        )
        connection = _FakeConnection(response)
        with (
            patch.object(
                root_read,
                "AsyncSessionLocal",
                return_value=_SessionContext(),
            ),
            patch.object(
                root_read.server_service,
                "get_server_by_name_or_ip",
                AsyncMock(return_value=server),
            ),
            patch.object(root_read, "build_connect_params", return_value=params),
            patch.object(
                root_read.asyncssh,
                "connect",
                return_value=_ConnectionContext(connection),
            ),
        ):
            if host:
                result = await root_read.inspect_host(
                    "web-01", "python --version; ls -la /tmp"
                )
            else:
                result = await root_read.run_root_read_command.ainvoke(
                    {
                        "server_name": "web-01",
                        "command": "rg -n redis /host/etc | head",
                    }
                )
        self.assertIsInstance(result, OperationResult)
        return result, connection

    async def test_uses_fixed_remote_command_and_stdin_json(self) -> None:
        result, connection = await self._invoke(
            {
                "exit_code": 0,
                "timed_out": False,
                "truncated": False,
                "output": "/host/etc/redis.conf:bind 127.0.0.1",
            }
        )

        self.assertTrue(result.ok)
        self.assertEqual(
            result.metadata,
            {
                "exit_code": 0,
                "timed_out": False,
                "truncated": False,
                "source": "file_toolbox",
            },
        )
        self.assertIn("Shell、PATH、程序版本", result.message)
        self.assertIn("宿主机文件根是 /host", result.message)
        self.assertIn("AIOPS_PATH_MASKED", result.message)
        self.assertEqual(len(connection.calls), 1)
        remote_command, kwargs = connection.calls[0]
        self.assertEqual(remote_command, root_read.ROOT_READ_REMOTE_COMMAND)
        self.assertNotIn("rg -n", remote_command)
        self.assertEqual(kwargs["check"], False)
        self.assertEqual(kwargs["timeout"], root_read.SSH_TIMEOUT_SECONDS)
        request = json.loads(str(kwargs["input"]))
        self.assertEqual(request["command"], "rg -n redis /host/etc | head")
        self.assertEqual(request["timeout"], root_read.ROOT_READ_TIMEOUT_SECONDS)
        self.assertEqual(request["scope"], "toolbox")

    async def test_redacts_output_and_preserves_failure_metadata(self) -> None:
        result, _ = await self._invoke(
            {
                "exit_code": 1,
                "timed_out": True,
                "truncated": False,
                "output": (
                    '"password": "plain-text"\n'
                    "Authorization: Bearer abc\n"
                    "postgresql://user:db-secret@db.internal/app\n"
                    "-----BEGIN PRIVATE KEY-----\nsecret\n"
                    "-----END PRIVATE KEY-----"
                ),
            }
        )

        self.assertFalse(result.ok)
        self.assertEqual(
            result.metadata,
            {
                "exit_code": 1,
                "timed_out": True,
                "truncated": False,
                "source": "file_toolbox",
            },
        )
        self.assertNotIn("plain-text", result.message)
        self.assertNotIn("Bearer abc", result.message)
        self.assertNotIn("db-secret", result.message)
        self.assertNotIn("BEGIN PRIVATE KEY", result.message)
        self.assertIn("[已脱敏]", result.message)

    async def test_rejects_invalid_gateway_protocol(self) -> None:
        result, _ = await self._invoke("not-json")

        self.assertFalse(result.ok)
        self.assertEqual(result.code, "invalid_gateway_response")

    async def test_host_inspection_uses_real_host_scope(self) -> None:
        result, connection = await self._invoke(
            {
                "exit_code": 0,
                "timed_out": False,
                "truncated": False,
                "output": "Python 3.6.8",
            },
            host=True,
        )

        self.assertTrue(result.ok)
        self.assertIn("/、PATH 和可执行程序均来自目标服务器", result.message)
        self.assertIn("700/777 等权限位不能用来判定", result.message)
        self.assertIn("AIOps 是否获准读取", result.message)
        self.assertIn("本次调查也不可写", result.message)
        self.assertNotIn("宿主机文件根是 /host", result.message)
        request = json.loads(str(connection.calls[0][1]["input"]))
        self.assertEqual(request["scope"], "host")
        self.assertEqual(request["command"], "python --version; ls -la /tmp")
