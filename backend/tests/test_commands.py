from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.agents.tools import commands


class _SessionContext:
    async def __aenter__(self) -> object:
        return object()

    async def __aexit__(self, *args: object) -> None:
        del args


class _FakeConnection:
    def __init__(self, output: str = "ok") -> None:
        self.output = output
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def run(self, command: str, **kwargs: object) -> SimpleNamespace:
        self.calls.append((command, kwargs))
        return SimpleNamespace(
            exit_status=0,
            stdout=self.output,
            stderr="",
        )


class _ConnectionContext:
    def __init__(self, connection: _FakeConnection) -> None:
        self.connection = connection

    async def __aenter__(self) -> _FakeConnection:
        return self.connection

    async def __aexit__(self, *args: object) -> None:
        del args


class DiagnosticCommandValidationTest(unittest.TestCase):
    def test_common_readonly_commands_are_allowed(self) -> None:
        for command in (
            "whoami",
            "id",
            "id -nG",
            "systemctl list-units",
            "systemctl list-units --type=service --state=running",
            "docker ps",
            "docker ps -a",
            "docker images",
            "docker stats --no-stream",
            "docker system df",
            "docker version",
            "docker network ls",
            "docker volume ls",
            "docker top web-01",
            "docker logs --tail 100 web-01",
            "ps aux",
            "ss -lntup",
            "ss -lxnp",
            "ps -p 12345 -o pid,ppid,user,lstart,cmd",
            "systemctl status 12345 --no-pager",
            "journalctl -u nginx.service -n 100 --no-pager",
            "kubectl get pods -A -o wide",
        ):
            with self.subTest(command=command):
                self.assertIsNotNone(commands._validated_command(command))

    def test_write_secret_and_shell_composition_commands_are_rejected(self) -> None:
        for command in (
            "systemctl restart nginx",
            "rm -rf /tmp/example",
            "cat /etc/shadow",
            "docker inspect web-01",
            "docker top web-01 --format pid",
            "docker logs --tail 100 web-01 --follow",
            "docker logs --tail 50 web-01",
            "kubectl get secrets -A -o yaml",
            "ps aux | grep nginx",
            "ps -p abc -o pid,ppid,user,lstart,cmd",
            "ps -p 12345 -o pid,cmd",
            "systemctl status 12345 --no-pager --full",
            "ss -K dst 127.0.0.1",
            "docker ps && touch /tmp/pwned",
            "df -h > /tmp/df.txt",
            "journalctl -u nginx.service -f",
            "docker logs --tail 100 web-01; id",
        ):
            with self.subTest(command=command):
                self.assertIsNone(commands._validated_command(command))

    def test_parameterized_command_rejects_unsafe_resource_name(self) -> None:
        self.assertIsNone(commands._validated_command("systemctl status nginx;id"))
        self.assertEqual(
            commands._validated_command("systemctl status nginx.service"),
            "systemctl status nginx.service --no-pager",
        )

    def test_docker_command_is_rewritten_to_noninteractive_sudo(self) -> None:
        self.assertEqual(
            commands._validated_command("docker ps"),
            "sudo -n -- /usr/local/sbin/aiops-docker-read ps",
        )
        self.assertEqual(
            commands._validated_command("docker logs --tail 100 web-01"),
            "sudo -n -- /usr/local/sbin/aiops-docker-read logs web-01",
        )
        self.assertEqual(
            commands._validated_command("docker top web-01"),
            "sudo -n -- /usr/local/sbin/aiops-docker-read top web-01",
        )

    def test_socket_and_pid_queries_use_readonly_gateway(self) -> None:
        self.assertEqual(
            commands._validated_command("ss -lntup"),
            "sudo -n -- /usr/local/sbin/aiops-service-read inet-sockets",
        )
        self.assertEqual(
            commands._validated_command("ss -lxnp"),
            "sudo -n -- /usr/local/sbin/aiops-service-read unix-sockets",
        )
        self.assertEqual(
            commands._validated_command("ps -p 12345 -o pid,ppid,user,lstart,cmd"),
            "sudo -n -- /usr/local/sbin/aiops-service-read process 12345",
        )
        self.assertEqual(
            commands._validated_command("systemctl status 12345 --no-pager"),
            "sudo -n -- /usr/local/sbin/aiops-service-read unit 12345",
        )


class DiagnosticCommandExecutionTest(unittest.IsolatedAsyncioTestCase):
    async def test_executes_canonical_command_with_limits(self) -> None:
        server = SimpleNamespace(name="web-01", ip_address="127.0.0.1")
        params = SimpleNamespace(
            host="127.0.0.1",
            port=22,
            username="ops",
            client_keys=["test-key"],
        )
        connection = _FakeConnection("x" * (commands._MAX_OUTPUT_CHARS + 10))

        with (
            patch.object(commands, "AsyncSessionLocal", return_value=_SessionContext()),
            patch.object(
                commands.server_service,
                "list_servers",
                AsyncMock(return_value=[server]),
            ),
            patch.object(commands, "build_connect_params", return_value=params),
            patch.object(
                commands.asyncssh,
                "connect",
                return_value=_ConnectionContext(connection),
            ),
        ):
            result = await commands.run_diagnostic_command.ainvoke(
                {"server_name": "web-01", "command": "systemctl list-units"}
            )

        self.assertEqual(
            connection.calls,
            [
                (
                    "systemctl list-units --type=service --all --no-pager --no-legend",
                    {"check": False, "timeout": commands._COMMAND_TIMEOUT_SECONDS},
                )
            ],
        )
        self.assertIn("输出过长，已截断", result)
        self.assertIn("退出码 0", result)
