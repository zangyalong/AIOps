from __future__ import annotations

import subprocess
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.agents.tools import actions
from app.operations.executors import compose_recreate

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ACTIONS_DIR = PROJECT_ROOT / "scripts" / "target-server" / "actions"


class _SessionContext:
    async def __aenter__(self) -> object:
        return object()

    async def __aexit__(self, *args: object) -> None:
        del args


class _Connection:
    def __init__(self) -> None:
        self.commands: list[str] = []

    async def run(self, command: str, **kwargs: object) -> SimpleNamespace:
        self.commands.append(command)
        self.kwargs = kwargs
        return SimpleNamespace(
            exit_status=0,
            stdout=(
                "已受控重建 Compose 服务 sub2api/sub2api（容器 sub2api）。\n"
                "当前状态：running；健康状态：none。\n"
                "8080/tcp -> 127.0.0.1:8081\n"
            ),
            stderr="",
        )


class _ConnectionContext:
    def __init__(self, connection: _Connection) -> None:
        self.connection = connection

    async def __aenter__(self) -> _Connection:
        return self.connection

    async def __aexit__(self, *args: object) -> None:
        del args


class ComposeRecreateToolTest(unittest.IsolatedAsyncioTestCase):
    async def test_rejects_unregistered_container_before_ssh(self) -> None:
        server = SimpleNamespace(id="server-id", name="web-01")
        with (
            patch.object(
                compose_recreate, "AsyncSessionLocal", return_value=_SessionContext()
            ),
            patch.object(
                compose_recreate.server_service,
                "get_server_by_name_or_ip",
                AsyncMock(return_value=server),
            ),
            patch.object(
                compose_recreate.service_catalog_service,
                "get_instance_by_runtime_identity",
                AsyncMock(return_value=None),
            ),
            patch.object(compose_recreate.asyncssh, "connect") as connect,
        ):
            result = await actions.recreate_compose_service.ainvoke(
                {"server_name": "web-01", "container_name": "sub2api"}
            )

        self.assertFalse(result.ok)
        self.assertEqual(result.code, "service_not_registered")
        connect.assert_not_called()

    async def test_uses_fixed_gateway_for_authorized_docker_instance(self) -> None:
        server = SimpleNamespace(id="server-id", name="web-01")
        instance = SimpleNamespace(control_enabled=True)
        connection = _Connection()
        with (
            patch.object(
                compose_recreate, "AsyncSessionLocal", return_value=_SessionContext()
            ),
            patch.object(
                compose_recreate.server_service,
                "get_server_by_name_or_ip",
                AsyncMock(return_value=server),
            ),
            patch.object(
                compose_recreate.service_catalog_service,
                "get_instance_by_runtime_identity",
                AsyncMock(return_value=instance),
            ) as get_instance,
            patch.object(
                compose_recreate,
                "build_connect_params",
                return_value=SimpleNamespace(
                    host="192.0.2.10",
                    port=22,
                    username="aioms",
                    client_keys=["test-key"],
                ),
            ),
            patch.object(
                compose_recreate.asyncssh,
                "connect",
                return_value=_ConnectionContext(connection),
            ),
        ):
            result = await actions.recreate_compose_service.ainvoke(
                {"server_name": "web-01", "container_name": "sub2api"}
            )

        self.assertEqual(get_instance.await_args.kwargs["runtime_type"], "docker")
        self.assertEqual(get_instance.await_args.kwargs["runtime_name"], "sub2api")
        self.assertEqual(
            connection.commands,
            ["sudo -n -- /usr/local/sbin/aioms-compose-recreate sub2api"],
        )
        self.assertTrue(result.ok)
        self.assertIn("127.0.0.1:8081", result.message)


class ComposeRecreateGatewayContractTest(unittest.TestCase):
    def test_gateway_is_fixed_and_does_not_accept_paths_or_flags(self) -> None:
        gateway = (ACTIONS_DIR / "aioms-compose-recreate").read_text()
        sudoers = (ACTIONS_DIR / "aioms-service-control.sudoers").read_text()
        self.assertIn("[[ $# -eq 1 ]]", gateway)
        self.assertIn('grep -Fxq -- "docker:$CONTAINER_NAME"', gateway)
        self.assertIn("com.docker.compose.project.working_dir", gateway)
        self.assertIn("com.docker.compose.project.config_files", gateway)
        self.assertIn('candidate_path="$working_dir/$raw_path"', gateway)
        self.assertEqual(gateway.count("ps --all --quiet --no-trunc"), 2)
        self.assertIn("受控单服务重建只支持单副本", gateway)
        self.assertIn("当前容器与已授权容器", gateway)
        self.assertIn("--no-deps --force-recreate", gateway)
        self.assertIn("--no-build --pull never", gateway)
        self.assertIn("COMPOSE_IGNORE_ORPHANS=true", gateway)
        self.assertIn("runuser -u", gateway)
        self.assertNotIn("eval ", gateway)
        self.assertNotIn("NOPASSWD: ALL", sudoers)
        self.assertIn("/usr/local/sbin/aioms-compose-recreate *", sudoers)

    def test_action_shell_scripts_parse(self) -> None:
        for script_name in (
            "aioms-service-control",
            "aioms-service-policy",
            "aioms-compose-recreate",
            "install.sh",
        ):
            with self.subTest(script=script_name):
                result = subprocess.run(
                    ["bash", "-n", str(ACTIONS_DIR / script_name)],
                    capture_output=True,
                    check=False,
                    text=True,
                )
                self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
