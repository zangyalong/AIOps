from __future__ import annotations

import unittest
import uuid
from datetime import UTC, datetime
from unittest.mock import ANY, AsyncMock, patch

from sqlalchemy.dialects import postgresql

from app.models.incident import FIRING, RESOLVED, Incident
from app.repositories import incident_repository
from app.services import incident_service


def _incident(*, status: str) -> Incident:
    now = datetime.now(UTC)
    return Incident(
        id=uuid.uuid4(),
        group_key="server:web01",
        status=status,
        alertname="NodeMemoryHigh",
        severity="warning",
        instance="web01:9100",
        summary="内存占用过高",
        diagnosis=None,
        triaged_at=None,
        acked_by=None,
        acked_at=None,
        resolved_at=now if status == RESOLVED else None,
        created_at=now,
        updated_at=now,
    )


class IncidentServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_list_incidents_returns_page_metadata_and_global_counts(self) -> None:
        item = _incident(status=RESOLVED)
        with (
            patch.object(
                incident_service.incident_repository,
                "list_page",
                AsyncMock(return_value=([item], 21)),
            ) as list_page,
            patch.object(
                incident_service.incident_repository,
                "count_by_status",
                AsyncMock(
                    return_value={"firing": 2, "acknowledged": 3, "resolved": 21}
                ),
            ),
        ):
            result = await incident_service.list_incidents(
                AsyncMock(),
                status=RESOLVED,
                page=2,
                page_size=10,
            )

        self.assertEqual([item.id], [row.id for row in result.items])
        self.assertEqual(result.total, 21)
        self.assertEqual(result.total_pages, 3)
        self.assertEqual(result.status_counts.all, 26)
        list_page.assert_awaited_once_with(
            ANY,
            status=RESOLVED,
            page=2,
            page_size=10,
        )

    async def test_repository_page_query_applies_status_limit_and_offset(self) -> None:
        item = _incident(status=RESOLVED)
        db = AsyncMock()
        db.scalar.return_value = 21
        db.scalars.return_value = [item]

        items, total = await incident_repository.list_page(
            db,
            status=RESOLVED,
            page=3,
            page_size=10,
        )

        statement = db.scalars.await_args.args[0]
        sql = str(
            statement.compile(
                dialect=postgresql.dialect(),
                compile_kwargs={"literal_binds": True},
            )
        )
        self.assertEqual(items, [item])
        self.assertEqual(total, 21)
        self.assertIn("WHERE incidents.status = 'resolved'", sql)
        self.assertIn("LIMIT 10 OFFSET 20", sql)

    async def test_delete_resolved_incident(self) -> None:
        item = _incident(status=RESOLVED)
        delete = AsyncMock()
        with (
            patch.object(
                incident_service.incident_repository,
                "get",
                AsyncMock(return_value=item),
            ),
            patch.object(incident_service.incident_repository, "delete", delete),
        ):
            deleted = await incident_service.delete_resolved(AsyncMock(), item.id)

        self.assertTrue(deleted)
        delete.assert_awaited_once()

    async def test_delete_active_incident_is_rejected(self) -> None:
        item = _incident(status=FIRING)
        delete = AsyncMock()
        with (
            patch.object(
                incident_service.incident_repository,
                "get",
                AsyncMock(return_value=item),
            ),
            patch.object(incident_service.incident_repository, "delete", delete),
        ):
            with self.assertRaises(incident_service.IncidentDeleteConflict):
                await incident_service.delete_resolved(AsyncMock(), item.id)

        delete.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
