from __future__ import annotations

import unittest
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.schemas.alert import AlertmanagerAlert
from app.services import alert_event_service


def _payload(status: str, ends_at: datetime | None = None) -> AlertmanagerAlert:
    return AlertmanagerAlert(
        status=status,
        labels={
            "alertname": "NodeMemoryUsageHigh",
            "severity": "warning",
            "instance": "web-01:9100",
        },
        annotations={"summary": "内存使用率偏高"},
        startsAt=datetime(2026, 8, 6, tzinfo=timezone.utc),
        endsAt=ends_at,
        fingerprint="fingerprint-1",
    )


class AlertOccurrenceIngestionTests(unittest.IsolatedAsyncioTestCase):
    async def test_firing_creates_occurrence_for_selected_incident(self) -> None:
        incident_id = uuid.uuid4()
        alert = SimpleNamespace(
            id=uuid.uuid4(),
            fingerprint="fingerprint-1",
            alertname="NodeMemoryUsageHigh",
            severity="warning",
            status="firing",
            summary="内存使用率偏高",
            labels={},
            instance="web-01:9100",
            starts_at=datetime(2026, 8, 6, tzinfo=timezone.utc),
            ends_at=None,
            incident_id=None,
        )
        db = AsyncMock()

        with (
            patch.object(
                alert_event_service.alert_event_repository,
                "upsert_by_fingerprint",
                AsyncMock(return_value=(alert, True)),
            ),
            patch.object(
                alert_event_service.incident_repository,
                "get_active_by_group_key",
                AsyncMock(return_value=None),
            ),
            patch.object(
                alert_event_service.incident_repository,
                "create",
                AsyncMock(return_value=SimpleNamespace(id=incident_id)),
            ),
            patch.object(
                alert_event_service.alert_occurrence_repository,
                "upsert_for_incident",
                AsyncMock(),
            ) as upsert_occurrence,
        ):
            created = await alert_event_service.ingest_alerts(db, [_payload("firing")])

        self.assertEqual(created, [incident_id])
        self.assertEqual(alert.incident_id, incident_id)
        upsert_occurrence.assert_awaited_once_with(
            db,
            alert=alert,
            incident_id=incident_id,
            status="firing",
        )
        db.commit.assert_awaited_once()

    async def test_resolved_updates_occurrence_and_resolves_incident(self) -> None:
        incident_id = uuid.uuid4()
        ended_at = datetime(2026, 8, 6, 1, tzinfo=timezone.utc)
        alert = SimpleNamespace(
            id=uuid.uuid4(),
            fingerprint="fingerprint-1",
            alertname="NodeMemoryUsageHigh",
            severity="warning",
            status="resolved",
            summary="内存使用率偏高",
            labels={},
            instance="web-01:9100",
            starts_at=datetime(2026, 8, 6, tzinfo=timezone.utc),
            ends_at=ended_at,
            incident_id=incident_id,
        )
        incident = SimpleNamespace(status="firing", resolved_at=None)
        db = AsyncMock()

        with (
            patch.object(
                alert_event_service.alert_event_repository,
                "upsert_by_fingerprint",
                AsyncMock(return_value=(alert, False)),
            ),
            patch.object(
                alert_event_service.alert_occurrence_repository,
                "upsert_for_incident",
                AsyncMock(),
            ) as upsert_occurrence,
            patch.object(
                alert_event_service.incident_repository,
                "count_firing_alerts",
                AsyncMock(return_value=0),
            ),
            patch.object(
                alert_event_service.incident_repository,
                "get",
                AsyncMock(return_value=incident),
            ),
        ):
            created = await alert_event_service.ingest_alerts(
                db, [_payload("resolved", ended_at)]
            )

        self.assertEqual(created, [])
        upsert_occurrence.assert_awaited_once_with(
            db,
            alert=alert,
            incident_id=incident_id,
            status="resolved",
            ends_at=ended_at,
        )
        self.assertEqual(incident.status, "resolved")
        self.assertIsNotNone(incident.resolved_at)
