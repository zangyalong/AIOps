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
        self.assertEqual(rows["api"].status, "Up 2 hours")
        self.assertEqual(rows["api"].ports, ("0.0.0.0:80->80/tcp",))
        self.assertEqual(rows["db"].status, "Exited (1) 2 minutes ago")
        self.assertEqual(rows["db"].ports, ())

    def test_systemd_ports_are_mapped_from_listener_pid_unit(self) -> None:
        listeners = service_runtime_service._parse_listener_endpoints(
            'tcp LISTEN 0 128 0.0.0.0:8010 0.0.0.0:* users:(("python",pid=301,fd=7))\n'
            'tcp LISTEN 0 128 127.0.0.1:8011 0.0.0.0:* users:(("python",pid=302,fd=8))\n'
        )

        ports = service_runtime_service._parse_systemd_ports(
            "301 1 ops 7200 pixel-forge-backend.service python -m uvicorn\n"
            "302 1 ops 7200 pixel-forge-backend.service python worker.py\n",
            listeners,
        )

        self.assertEqual(
            ports["pixel-forge-backend.service"],
            ("tcp/8010", "tcp/8011"),
        )

    def test_systemd_main_pid_is_used_when_process_unit_is_missing(self) -> None:
        ports = service_runtime_service._parse_systemd_ports(
            "101 1 root 7200 - nginx: master process\n",
            {101: {"tcp/80", "tcp/443"}},
            {"nginx.service": 101},
        )

        self.assertEqual(ports["nginx.service"], ("tcp/80", "tcp/443"))

    def test_docker_top_returns_host_pids_for_host_network_ports(self) -> None:
        pids = service_runtime_service._parse_docker_top_pids(
            "UID PID PPID C STIME TTY TIME CMD\n"
            "nobody 4321 1 0 10:00 ? 00:00:00 /bin/mysqld_exporter\n"
        )

        self.assertEqual(pids, {4321})


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
