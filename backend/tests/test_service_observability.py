from __future__ import annotations

import unittest
from types import SimpleNamespace

from app.services import service_observability_service, service_runtime_service


class ServiceRuntimeStatusTests(unittest.TestCase):
    def test_systemd_states_use_three_user_facing_statuses(self) -> None:
        self.assertEqual(
            service_runtime_service.classify_systemd_state("active\n"),
            ("running", "运行中"),
        )
        self.assertEqual(
            service_runtime_service.classify_systemd_state("inactive"),
            ("stopped", "已停止"),
        )
        self.assertEqual(
            service_runtime_service.classify_systemd_state("failed"),
            ("error", "运行异常"),
        )

    def test_docker_exit_zero_is_stopped_and_nonzero_is_error(self) -> None:
        self.assertEqual(
            service_runtime_service.classify_docker_state("Exited (0) 2 hours ago"),
            ("stopped", "已停止"),
        )
        self.assertEqual(
            service_runtime_service.classify_docker_state("Exited (137) 1 minute ago"),
            ("error", "异常退出"),
        )
        self.assertEqual(
            service_runtime_service.classify_docker_state("Up 2 hours (healthy)"),
            ("running", "运行中"),
        )

    def test_parse_docker_inventory_keeps_status_column(self) -> None:
        rows = service_runtime_service.parse_docker_inventory(
            "api\timage:v1\tUp 2 hours\t0.0.0.0:80->80/tcp\n"
            "db\tpostgres:18\tExited (1) 2 minutes ago\t\n"
        )
        self.assertEqual(rows["api"], "Up 2 hours")
        self.assertEqual(rows["db"], "Exited (1) 2 minutes ago")


class ServiceMetricDefinitionTests(unittest.TestCase):
    def test_database_service_uses_catalog_service_label(self) -> None:
        service = SimpleNamespace(name="sub2api-postgres")
        instance = SimpleNamespace(
            runtime_name="sub2api-postgres",
            runtime_type="docker",
            metric_instance=None,
        )
        server = SimpleNamespace(name="web-01")

        definitions = service_observability_service._metric_definitions(
            service, instance, server
        )

        pg_up = next(item for item in definitions if item.key == "pg_up")
        self.assertIn('server="web-01"', pg_up.promql)
        self.assertIn('service="sub2api-postgres"', pg_up.promql)
        self.assertNotIn("runtime_name", pg_up.promql)

    def test_exporter_service_uses_exporter_runtime_label(self) -> None:
        service = SimpleNamespace(name="aioms-redis-exporter")
        instance = SimpleNamespace(
            runtime_name="aioms-redis-exporter",
            runtime_type="docker",
            metric_instance=None,
        )
        server = SimpleNamespace(name="web-01")

        definitions = service_observability_service._metric_definitions(
            service, instance, server
        )

        redis_up = next(item for item in definitions if item.key == "redis_up")
        self.assertIn('runtime_name="aioms-redis-exporter"', redis_up.promql)

    def test_systemd_service_uses_its_cgroup_metrics(self) -> None:
        service = SimpleNamespace(name="pixel-forge-frontend")
        instance = SimpleNamespace(
            runtime_name="pixel-forge-frontend.service",
            runtime_type="systemd",
            metric_instance=None,
        )
        server = SimpleNamespace(name="web-01")

        definitions = service_observability_service._metric_definitions(
            service, instance, server
        )

        self.assertEqual(
            [definition.key for definition in definitions],
            ["service_cpu", "service_memory"],
        )
        for definition in definitions:
            self.assertIn('server="web-01"', definition.promql)
            self.assertIn(
                'id="/system.slice/pixel-forge-frontend.service"',
                definition.promql,
            )
            self.assertNotIn('instance="web-01:9100"', definition.promql)


if __name__ == "__main__":
    unittest.main()
