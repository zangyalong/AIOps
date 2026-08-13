from __future__ import annotations

import tempfile
import unittest
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import yaml
from pydantic import ValidationError

from app.schemas.alert_rule import AlertRuleCreate, AlertRuleUpdate
from app.services.alert_rule_service import (
    _prometheus_runtime,
    _render_rules_document,
    _rule_revision,
    _wait_for_managed_rules,
    _write_rules_file,
    get_overview,
)


def _rule(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "name": "NodeCpuUsageHigh",
        "expression": 'node_load1{instance="web-01:9100"} > 4',
        "duration": "5m",
        "severity": "warning",
        "summary": "服务器 CPU 负载持续偏高",
        "description": "请检查高负载进程。",
        "labels": {},
        "enabled": True,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class AlertRuleSchemaTests(unittest.TestCase):
    def test_create_normalizes_text_and_accepts_prometheus_duration(self) -> None:
        rule = AlertRuleCreate(
            name="  NodeCpuUsageHigh  ",
            expression="  up == 0  ",
            duration=" 10m ",
            summary="  目标不可用  ",
        )

        self.assertEqual(rule.name, "NodeCpuUsageHigh")
        self.assertEqual(rule.expression, "up == 0")
        self.assertEqual(rule.duration, "10m")
        self.assertEqual(rule.summary, "目标不可用")

    def test_invalid_name_and_duration_are_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            AlertRuleCreate(
                name="CPU 使用率",
                expression="up == 0",
                duration="five minutes",
                summary="目标不可用",
            )

    def test_update_rejects_explicit_null_for_required_field(self) -> None:
        with self.assertRaises(ValidationError):
            AlertRuleUpdate(name=None)

    def test_labels_reject_severity_and_invalid_names(self) -> None:
        with self.assertRaises(ValidationError):
            AlertRuleCreate(
                name="DiskWillFillSoon",
                expression="vector(1)",
                summary="磁盘空间不足",
                labels={"severity": "critical"},
            )
        with self.assertRaises(ValidationError):
            AlertRuleCreate(
                name="DiskWillFillSoon",
                expression="vector(1)",
                summary="磁盘空间不足",
                labels={"bad-label": "value"},
            )


class AlertRuleFileTests(unittest.TestCase):
    def test_render_only_includes_enabled_rules(self) -> None:
        document = yaml.safe_load(
            _render_rules_document([_rule(), _rule(name="DisabledRule", enabled=False)])
        )

        rendered = document["groups"][0]["rules"]
        self.assertEqual([item["alert"] for item in rendered], ["NodeCpuUsageHigh"])
        self.assertEqual(rendered[0]["for"], "5m")
        self.assertEqual(rendered[0]["labels"]["severity"], "warning")

    def test_render_preserves_custom_labels(self) -> None:
        document = yaml.safe_load(
            _render_rules_document(
                [_rule(labels={"instance": "db-01:9100", "team": "infra"})]
            )
        )

        labels = document["groups"][0]["rules"][0]["labels"]
        self.assertEqual(labels["instance"], "db-01:9100")
        self.assertEqual(labels["team"], "infra")
        self.assertEqual(labels["severity"], "warning")

    def test_rule_revision_changes_with_expression(self) -> None:
        original = _rule_revision(_rule(expression="up == 0"))
        changed = _rule_revision(_rule(expression="up == 1"))

        self.assertNotEqual(original, changed)

    def test_render_includes_revision_annotation(self) -> None:
        rule = _rule()
        document = yaml.safe_load(_render_rules_document([rule]))

        annotations = document["groups"][0]["rules"][0]["annotations"]
        self.assertEqual(annotations["aiops_revision"], _rule_revision(rule))

    def test_write_replaces_rule_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "managed_alert_rules.yml"
            _write_rules_file(path, "groups: []\n")
            _write_rules_file(path, "groups:\n  - name: managed\n")

            self.assertEqual(path.read_text(), "groups:\n  - name: managed\n")
            self.assertFalse(list(path.parent.glob(f".{path.name}.*")))

    def test_runtime_groups_are_indexed_by_rule_name(self) -> None:
        runtime = _prometheus_runtime(
            [
                {
                    "rules": [
                        {"name": "NodeCpuUsageHigh", "state": "firing"},
                        {"name": "DiskUsageHigh", "state": "inactive"},
                    ]
                }
            ]
        )

        self.assertEqual(runtime["NodeCpuUsageHigh"]["state"], "firing")
        self.assertEqual(runtime["DiskUsageHigh"]["state"], "inactive")


class AlertRuleReloadWaitTests(unittest.IsolatedAsyncioTestCase):
    async def test_waits_until_revision_and_runtime_state_are_ready(self) -> None:
        expected = {"NodeCpuUsageHigh": "new-version"}
        old_group = [
            {
                "name": "aiops-managed-alerts",
                "rules": [
                    {
                        "name": "NodeCpuUsageHigh",
                        "state": "firing",
                        "annotations": {"aiops_revision": "old-version"},
                    }
                ],
            }
        ]
        new_group_before_evaluation = [
            {
                "name": "aiops-managed-alerts",
                "rules": [
                    {
                        "name": "NodeCpuUsageHigh",
                        "state": "unknown",
                        "annotations": {"aiops_revision": "new-version"},
                    }
                ],
            }
        ]
        new_group_after_evaluation = [
            {
                "name": "aiops-managed-alerts",
                "rules": [
                    {
                        "name": "NodeCpuUsageHigh",
                        "state": "firing",
                        "annotations": {"aiops_revision": "new-version"},
                    }
                ],
            }
        ]

        with patch(
            "app.services.alert_rule_service.prom_rule_groups",
            AsyncMock(
                side_effect=[
                    old_group,
                    new_group_before_evaluation,
                    new_group_after_evaluation,
                ]
            ),
        ) as query_rules:
            await _wait_for_managed_rules(
                expected,
                timeout_seconds=1,
                poll_interval=0,
            )

        self.assertEqual(query_rules.await_count, 3)

    async def test_disabled_rule_has_explicit_disabled_runtime_state(self) -> None:
        now = datetime.now(UTC)
        rule = _rule(
            id=uuid.uuid4(),
            enabled=False,
            created_at=now,
            updated_at=now,
        )
        with (
            patch(
                "app.services.alert_rule_service.alert_rule_repository.list_all",
                AsyncMock(return_value=[rule]),
            ),
            patch(
                "app.services.alert_rule_service.prom_rule_groups",
                AsyncMock(return_value=[]),
            ),
            patch(
                "app.services.alert_rule_service.am_get_alerts",
                AsyncMock(return_value=[]),
            ),
        ):
            overview = await get_overview(AsyncMock())

        self.assertEqual(overview.rules[0].runtime_state, "disabled")
        self.assertEqual(overview.rules[0].health, "disabled")
