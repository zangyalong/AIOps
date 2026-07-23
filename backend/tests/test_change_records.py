from __future__ import annotations

import unittest
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from pydantic import ValidationError

from app.agents.tools import changes, correlate
from app.schemas.change_record import ChangeRecordCreate
from app.services import change_record_service


class _SessionContext:
    async def __aenter__(self) -> object:
        return object()

    async def __aexit__(self, *args: object) -> None:
        del args


class ChangeRecordSchemaTest(unittest.TestCase):
    def test_create_requires_timezone(self) -> None:
        with self.assertRaises(ValidationError):
            ChangeRecordCreate(
                service_name="api",
                environment="prod",
                change_type="deployment",
                occurred_at=datetime(2026, 7, 22, 14, 0),
            )


class IncidentChangeContextTest(unittest.IsolatedAsyncioTestCase):
    async def test_prefers_normalized_service_instance_mapping(self) -> None:
        anchor = datetime(2026, 7, 22, 14, 5, tzinfo=timezone.utc)
        incident = SimpleNamespace(created_at=anchor, instance="orders:9100")
        service = SimpleNamespace(
            id=uuid.uuid4(), name="orders-api", environment="prod"
        )
        resolved = SimpleNamespace(service=service, instance=SimpleNamespace())
        query = AsyncMock(return_value=[])
        legacy_lookup = AsyncMock()

        with (
            patch.object(
                change_record_service.service_catalog_service,
                "resolve_by_metric_instance",
                AsyncMock(return_value=resolved),
            ),
            patch.object(
                change_record_service.server_service,
                "list_servers",
                legacy_lookup,
            ),
            patch.object(change_record_service, "list_change_records", query),
        ):
            context = await change_record_service.get_incident_change_context(
                object(), incident
            )

        self.assertEqual(context.service_id, service.id)
        self.assertEqual(context.service_name, "orders-api")
        self.assertEqual(context.environment, "prod")
        legacy_lookup.assert_not_awaited()
        query.assert_awaited_once()

    async def test_maps_instance_to_service_and_uses_symmetric_window(self) -> None:
        anchor = datetime(2026, 7, 22, 14, 5, tzinfo=timezone.utc)
        incident = SimpleNamespace(created_at=anchor, instance="api:9100")
        server = SimpleNamespace(
            monitor_labels={
                "instance": "api:9100",
                "service": "orders-api",
                "environment": "prod",
            }
        )
        records = [SimpleNamespace(service_name="orders-api")]
        query = AsyncMock(return_value=records)

        with (
            patch.object(
                change_record_service.service_catalog_service,
                "resolve_by_metric_instance",
                AsyncMock(return_value=None),
            ),
            patch.object(
                change_record_service.server_service,
                "list_servers",
                AsyncMock(return_value=[server]),
            ),
            patch.object(change_record_service, "list_change_records", query),
        ):
            context = await change_record_service.get_incident_change_context(
                object(), incident, window_minutes=30
            )

        self.assertEqual(context.service_name, "orders-api")
        self.assertIsNone(context.service_id)
        self.assertEqual(context.environment, "prod")
        self.assertEqual(context.window_start, anchor - timedelta(minutes=30))
        self.assertEqual(context.window_end, anchor + timedelta(minutes=30))
        self.assertEqual(context.changes, records)
        query.assert_awaited_once_with(
            unittest.mock.ANY,
            service_name="orders-api",
            environment="prod",
            start_at=anchor - timedelta(minutes=30),
            end_at=anchor + timedelta(minutes=30),
            limit=100,
        )

    async def test_unmapped_instance_returns_empty_context_without_query(self) -> None:
        incident = SimpleNamespace(
            created_at=datetime(2026, 7, 22, 14, 5, tzinfo=timezone.utc),
            instance="unknown:9100",
        )
        query = AsyncMock()

        with (
            patch.object(
                change_record_service.service_catalog_service,
                "resolve_by_metric_instance",
                AsyncMock(return_value=None),
            ),
            patch.object(
                change_record_service.server_service,
                "list_servers",
                AsyncMock(return_value=[]),
            ),
            patch.object(change_record_service, "list_change_records", query),
        ):
            context = await change_record_service.get_incident_change_context(
                object(), incident
            )

        self.assertIsNone(context.service_name)
        self.assertEqual(context.changes, [])
        query.assert_not_awaited()


class QueryRecentChangesToolTest(unittest.IsolatedAsyncioTestCase):
    async def test_filters_and_formats_change_records(self) -> None:
        record = SimpleNamespace(
            occurred_at=datetime(2026, 7, 22, 14, 3, tzinfo=timezone.utc),
            change_type="deployment",
            service_name="orders-api",
            environment="prod",
            version="v2.8.1",
            commit_hash="abc123",
            description="调整数据库连接池",
        )
        query = AsyncMock(return_value=[record])

        with (
            patch.object(changes, "AsyncSessionLocal", return_value=_SessionContext()),
            patch.object(changes.change_record_service, "list_change_records", query),
        ):
            result = await changes.query_recent_changes.ainvoke(
                {
                    "service": "orders-api",
                    "environment": "prod",
                    "at": "2026-07-22T14:05:00+00:00",
                    "window_minutes": 30,
                }
            )

        self.assertIn("[发布] orders-api/prod v2.8.1", result)
        self.assertIn("调整数据库连接池", result)
        kwargs = query.await_args.kwargs
        self.assertEqual(kwargs["service_name"], "orders-api")
        self.assertEqual(kwargs["environment"], "prod")
        self.assertEqual(kwargs["end_at"] - kwargs["start_at"], timedelta(minutes=60))

    async def test_invalid_anchor_returns_actionable_message(self) -> None:
        result = await changes.query_recent_changes.ainvoke(
            {"at": "不是时间", "window_minutes": 30}
        )
        self.assertIn("ISO8601", result)


class CorrelateChangesTest(unittest.IsolatedAsyncioTestCase):
    async def test_correlation_includes_change_evidence_with_entity_labels(
        self,
    ) -> None:
        server = SimpleNamespace(
            id=uuid.uuid4(),
            name="api-01",
            monitor_labels={
                "instance": "api:9100",
                "service": "orders-api",
                "environment": "prod",
            },
        )
        pull_changes = AsyncMock(return_value="- 14:03 [发布] orders-api v2.8.1")

        with (
            patch.object(
                correlate, "AsyncSessionLocal", return_value=_SessionContext()
            ),
            patch.object(
                correlate.server_service,
                "list_servers",
                AsyncMock(return_value=[server]),
            ),
            patch.object(
                correlate.service_catalog_service,
                "list_instances",
                AsyncMock(return_value=[]),
            ),
            patch.object(
                correlate, "_pull_metrics", AsyncMock(return_value="指标证据")
            ),
            patch.object(correlate, "_pull_logs", AsyncMock(return_value="日志证据")),
            patch.object(correlate, "_pull_alerts", AsyncMock(return_value="告警证据")),
            patch.object(correlate, "_pull_changes", pull_changes),
        ):
            result = await correlate.correlate_incident.ainvoke(
                {
                    "server": "api-01",
                    "at": "2026-07-22T14:05:00+00:00",
                    "window_minutes": 30,
                }
            )

        self.assertIn("### 变更记录", result)
        self.assertIn("orders-api v2.8.1", result)
        args = pull_changes.await_args.args
        self.assertEqual(args[0:2], ("orders-api", "prod"))
        self.assertEqual(args[3] - args[2], timedelta(minutes=60))
