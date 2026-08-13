from __future__ import annotations

import hashlib
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.agents.tools import catalog, server_log_config
from app.operations.executors import log_config as log_config_executor
from app.operations.schemas import OperationCall, OperationContext


def _snapshot() -> server_log_config.LogConfigSnapshot:
    mappings = (
        server_log_config.LogMappingItem(
            service_name="orders-api",
            environment="prod",
            runtime_type="docker",
            runtime_name="orders-api",
            log_source="docker",
        ),
        server_log_config.LogMappingItem(
            service_name="worker",
            environment="prod",
            runtime_type="process",
            runtime_name="worker",
            log_source="none",
        ),
    )
    content = server_log_config._render_services_map(list(mappings))
    return server_log_config.LogConfigSnapshot(
        server_name="web01",
        mappings=mappings,
        services_map=content,
        preview_hash=hashlib.sha256(content.encode()).hexdigest(),
        source_count=1,
        requires_docker_socket=True,
        warnings=("1 个未纳管进程不会采集日志",),
    )


class _RemoteFile:
    def __init__(self, sftp: "_SFTP") -> None:
        self.sftp = sftp

    async def __aenter__(self) -> "_RemoteFile":
        return self

    async def __aexit__(self, *args: object) -> None:
        del args

    async def write(self, content: str) -> None:
        self.sftp.content = content


class _SFTP:
    def __init__(self) -> None:
        self.content = ""
        self.opened_path = ""
        self.chmod_call: tuple[str, int] | None = None
        self.rename_call: tuple[str, str] | None = None

    async def __aenter__(self) -> "_SFTP":
        return self

    async def __aexit__(self, *args: object) -> None:
        del args

    def open(self, path: str, mode: str) -> _RemoteFile:
        self.opened_path = path
        assert mode == "w"
        return _RemoteFile(self)

    async def chmod(self, path: str, mode: int) -> None:
        self.chmod_call = (path, mode)

    async def posix_rename(self, oldpath: str, newpath: str) -> None:
        self.rename_call = (oldpath, newpath)


class _Connection:
    def __init__(self) -> None:
        self.sftp = _SFTP()
        self.commands: list[str] = []

    async def __aenter__(self) -> "_Connection":
        return self

    async def __aexit__(self, *args: object) -> None:
        del args

    def start_sftp_client(self) -> _SFTP:
        return self.sftp

    async def run(self, command: str, **kwargs: object) -> SimpleNamespace:
        del kwargs
        self.commands.append(command)
        return SimpleNamespace(exit_status=0, stdout="applied", stderr="")


class ServerLogConfigTest(unittest.IsolatedAsyncioTestCase):
    async def test_preview_returns_hash_mappings_and_generated_map(self) -> None:
        snapshot = _snapshot()
        with patch.object(
            server_log_config,
            "_build_snapshot",
            AsyncMock(return_value=(SimpleNamespace(name="web01"), snapshot)),
        ):
            result = await server_log_config.preview_server_log_config.ainvoke(
                {"server_name": "web01"}
            )

        payload = json.loads(result)
        self.assertEqual(payload["preview_hash"], snapshot.preview_hash)
        self.assertEqual(payload["source_count"], 1)
        self.assertIn(
            "orders-api|prod|docker|orders-api|docker|-", payload["services_map"]
        )

    async def test_apply_rejects_changed_preview_before_connecting(self) -> None:
        snapshot = _snapshot()
        with (
            patch.object(
                server_log_config,
                "_build_snapshot",
                AsyncMock(return_value=(SimpleNamespace(name="web01"), snapshot)),
            ),
            patch.object(log_config_executor.asyncssh, "connect") as connect,
        ):
            result = await server_log_config.apply_server_log_config.ainvoke(
                {
                    "server_name": "web01",
                    "preview_hash": "0" * 64,
                    "mappings": [item.model_dump() for item in snapshot.mappings],
                }
            )

        self.assertFalse(result.ok)
        self.assertIn("预览不一致", result.message)
        connect.assert_not_called()

    async def test_broker_freezes_hash_for_selected_mapping_subset(self) -> None:
        """全量目录 14 项、用户只选 9 项时，审批哈希必须绑定所选子集。"""
        snapshot = _snapshot()
        selected = [snapshot.mappings[0]]
        selected_snapshot = server_log_config._snapshot_from_mappings(
            snapshot.server_name,
            selected,
        )
        with patch.object(
            server_log_config,
            "_build_snapshot",
            AsyncMock(return_value=(SimpleNamespace(name="web01"), snapshot)),
        ):
            prepared = await catalog.OPERATION_BROKER.aprepare(
                OperationCall(
                    id="apply-selected-logs",
                    name="apply_server_log_config",
                    args={
                        "server_name": "web01",
                        "mappings": [item.model_dump() for item in selected],
                    },
                ),
                OperationContext(
                    actor_id="operator-1",
                    actor_role="operator",
                    run_mode="interactive",
                ),
            )

        self.assertEqual(prepared.decision.result, "approval_required")
        self.assertNotEqual(snapshot.preview_hash, selected_snapshot.preview_hash)
        self.assertEqual(
            prepared.call.args["preview_hash"], selected_snapshot.preview_hash
        )
        self.assertEqual(
            prepared.call.normalized_args()["mappings"],
            [item.model_dump() for item in selected_snapshot.mappings],
        )
        self.assertIn(
            "orders-api ← docker/orders-api", prepared.approval_view["command"]
        )
        self.assertNotIn("worker", prepared.approval_view["command"])

    async def test_selected_mapping_still_rejects_real_catalog_change(self) -> None:
        snapshot = _snapshot()
        requested = snapshot.mappings[0]
        changed = server_log_config.LogMappingItem(
            service_name=requested.service_name,
            environment=requested.environment,
            runtime_type="docker",
            runtime_name="orders-api-v2",
            log_source="docker",
        )

        with self.assertRaisesRegex(ValueError, "已不在当前服务目录"):
            server_log_config._select_snapshot(snapshot, [changed])

    async def test_apply_uploads_only_fixed_map_and_runs_fixed_gateway(self) -> None:
        snapshot = _snapshot()
        server = SimpleNamespace(name="web01")
        connection = _Connection()
        params = SimpleNamespace(
            host="192.0.2.10",
            port=22,
            username="aioms",
            client_keys=["aioms-key"],
        )
        with (
            patch.object(
                server_log_config,
                "_build_snapshot",
                AsyncMock(return_value=(server, snapshot)),
            ),
            patch.object(
                log_config_executor, "build_connect_params", return_value=params
            ),
            patch.object(
                log_config_executor.asyncssh, "connect", return_value=connection
            ),
        ):
            result = await server_log_config.apply_server_log_config.ainvoke(
                {
                    "server_name": "web01",
                    "preview_hash": snapshot.preview_hash,
                    "mappings": [item.model_dump() for item in snapshot.mappings],
                }
            )

        self.assertEqual(connection.sftp.content, snapshot.services_map)
        self.assertEqual(
            connection.sftp.opened_path, log_config_executor.REMOTE_TEMP_MAP
        )
        self.assertEqual(
            connection.sftp.rename_call,
            (log_config_executor.REMOTE_TEMP_MAP, log_config_executor.REMOTE_MAP),
        )
        self.assertEqual(connection.commands, [log_config_executor.APPLY_COMMAND])
        self.assertTrue(result.ok)
        self.assertIn("已在 web01 应用 1 个日志源", result.message)

    async def test_apply_selected_subset_ignores_unselected_catalog_entries(
        self,
    ) -> None:
        full_snapshot = _snapshot()
        selected_snapshot = server_log_config._snapshot_from_mappings(
            full_snapshot.server_name,
            [full_snapshot.mappings[0]],
        )
        server = SimpleNamespace(name="web01")
        connection = _Connection()
        params = SimpleNamespace(
            host="192.0.2.10",
            port=22,
            username="aioms",
            client_keys=["aioms-key"],
        )
        with (
            patch.object(
                server_log_config,
                "_build_snapshot",
                AsyncMock(return_value=(server, full_snapshot)),
            ),
            patch.object(
                log_config_executor, "build_connect_params", return_value=params
            ),
            patch.object(
                log_config_executor.asyncssh, "connect", return_value=connection
            ),
        ):
            result = await server_log_config.apply_server_log_config.ainvoke(
                {
                    "server_name": "web01",
                    "preview_hash": selected_snapshot.preview_hash,
                    "mappings": [
                        item.model_dump() for item in selected_snapshot.mappings
                    ],
                }
            )

        self.assertTrue(result.ok)
        self.assertEqual(connection.sftp.content, selected_snapshot.services_map)
        self.assertIn(
            "orders-api|prod|docker|orders-api|docker|-", connection.sftp.content
        )
        self.assertNotIn("worker", connection.sftp.content)
