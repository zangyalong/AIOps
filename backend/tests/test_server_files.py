from __future__ import annotations

import json
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.agents.tools import server_files
from app.services import server_file_policy_service


class ServerFileToolValidationTest(unittest.IsolatedAsyncioTestCase):
    def test_snapshot_parser_preserves_exact_trailing_newline(self) -> None:
        output = (
            "PATH=/etc/demo.conf\n"
            f"SHA256={'a' * 64}\n"
            "REDACTED=false\n"
            "---\n"
            "port=3306\n"
        )
        snapshot = server_files._parse_snapshot(output)
        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        self.assertEqual(snapshot.content, "port=3306\n")
        self.assertFalse(snapshot.redacted)

    def test_new_content_rejects_secrets_and_redaction_placeholder(self) -> None:
        self.assertIsNotNone(server_files._validate_new_content("password=hunter2\n"))
        self.assertIsNotNone(
            server_files._validate_new_content('{"password": "hunter2"}\n')
        )
        self.assertIsNotNone(
            server_files._validate_new_content("dsn=mysql://app:pw@db:3306/app\n")
        )
        self.assertIsNotNone(server_files._validate_new_content("token=[已脱敏]\n"))
        self.assertIsNone(server_files._validate_new_content("port=3306\n"))

    async def test_preview_refuses_redacted_source_file(self) -> None:
        server = SimpleNamespace(name="web-01")
        snapshot = server_files.RemoteFileSnapshot(
            path="/opt/app/.env",
            sha256="a" * 64,
            redacted=True,
            content="PASSWORD=[已脱敏]\nPORT=3306\n",
        )
        with (
            patch.object(
                server_files,
                "_resolve_server",
                AsyncMock(return_value=(server, None)),
            ),
            patch.object(
                server_files,
                "_read_snapshot",
                AsyncMock(return_value=(snapshot, None)),
            ),
        ):
            result = await server_files.preview_server_file_update.ainvoke(
                {
                    "server_name": "web-01",
                    "path": "/opt/app/.env",
                    "new_content": "PORT=3307\n",
                }
            )
        self.assertIn("禁止整文件覆盖", result)

    async def test_preview_returns_hash_and_exact_diff(self) -> None:
        server = SimpleNamespace(name="web-01")
        snapshot = server_files.RemoteFileSnapshot(
            path="/opt/app/app.conf",
            sha256="b" * 64,
            redacted=False,
            content="port=3306\n",
        )
        with (
            patch.object(
                server_files,
                "_resolve_server",
                AsyncMock(return_value=(server, None)),
            ),
            patch.object(
                server_files,
                "_read_snapshot",
                AsyncMock(return_value=(snapshot, None)),
            ),
        ):
            result = await server_files.preview_server_file_update.ainvoke(
                {
                    "server_name": "web-01",
                    "path": "/opt/app/app.conf",
                    "new_content": "port=3307\n",
                }
            )
        payload = json.loads(result)
        self.assertEqual(payload["expected_sha256"], "b" * 64)
        self.assertIn("-port=3306", payload["preview_diff"])
        self.assertIn("+port=3307", payload["preview_diff"])
        self.assertFalse(payload["diff_truncated"])


class ServerFilePolicyReferenceTest(unittest.IsolatedAsyncioTestCase):
    async def test_shared_server_policy_is_not_removed_until_last_reference(self) -> None:
        server_id = uuid.uuid4()
        current_id = uuid.uuid4()
        other_id = uuid.uuid4()
        current = SimpleNamespace(
            id=current_id,
            service_instance_id=uuid.uuid4(),
            access_level="read",
            path="/opt/shared",
        )
        other = SimpleNamespace(
            id=other_id,
            service_instance_id=uuid.uuid4(),
            access_level="read",
            path="/opt/shared",
        )
        instance = SimpleNamespace(server_id=server_id)
        server = SimpleNamespace(id=server_id)
        remote_policy = AsyncMock()
        delete_record = AsyncMock()
        with (
            patch.object(
                server_file_policy_service.service_catalog_service,
                "get_instance",
                AsyncMock(return_value=instance),
            ),
            patch.object(
                server_file_policy_service,
                "_instance_server",
                AsyncMock(return_value=server),
            ),
            patch.object(
                server_file_policy_service.service_catalog_repository,
                "list_file_accesses",
                AsyncMock(return_value=[current, other]),
            ),
            patch.object(
                server_file_policy_service,
                "_apply_remote_policy",
                remote_policy,
            ),
            patch.object(
                server_file_policy_service.service_catalog_repository,
                "delete_file_access",
                delete_record,
            ),
        ):
            await server_file_policy_service.delete_file_access(object(), current)

        remote_policy.assert_not_awaited()
        delete_record.assert_awaited_once()
