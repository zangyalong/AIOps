from __future__ import annotations

import json
import runpy
import subprocess
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ROOT_READ_DIR = PROJECT_ROOT / "scripts" / "target-server" / "root-read"
ENTRYPOINT = ROOT_READ_DIR / "entrypoint.py"


class RootReadSandboxContractTest(unittest.TestCase):
    def _run_entrypoint(self, request: bytes) -> tuple[int, dict[str, object]]:
        result = subprocess.run(
            [sys.executable, str(ENTRYPOINT)],
            input=request,
            capture_output=True,
            check=False,
            timeout=5,
        )
        return result.returncode, json.loads(result.stdout)

    def test_launcher_fixes_the_docker_security_boundary(self) -> None:
        launcher = (ROOT_READ_DIR / "aioms-root-read").read_text()
        for fragment in (
            "--network none",
            "--cgroupns private",
            "--read-only",
            "--cap-drop ALL",
            "--cap-add DAC_READ_SEARCH",
            "--security-opt no-new-privileges:true",
            "--security-opt seccomp=builtin",
            "--pids-limit 128",
            "--memory 256m",
            "--cpus 0.5",
            "bind-recursive=disabled",
            'readonly IMAGE_ID_FILE="/etc/aioms/root-read.image-id"',
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, launcher)
        self.assertNotIn("--privileged", launcher)
        self.assertNotIn("docker.sock", launcher)
        self.assertNotIn("eval ", launcher)

    def test_dockerfile_uses_the_daocloud_base_image_proxy(self) -> None:
        dockerfile = (ROOT_READ_DIR / "Dockerfile").read_text()
        self.assertIn(
            "ARG BASE_IMAGE=m.daocloud.io/docker.io/library/debian:bookworm-slim",
            dockerfile,
        )
        self.assertIn("FROM ${BASE_IMAGE}", dockerfile)

    def test_dockerfile_uses_a_bounded_domestic_apt_source(self) -> None:
        dockerfile = (ROOT_READ_DIR / "Dockerfile").read_text()
        for fragment in (
            "http://mirrors.tuna.tsinghua.edu.cn/debian",
            'Acquire::Retries "3";',
            'Acquire::http::Timeout "30";',
            'Acquire::https::Timeout "30";',
            'Acquire::http::Pipeline-Depth "0";',
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, dockerfile)
        self.assertNotIn("mirrors.tuna.tsinghua.edu.cn/debian-security", dockerfile)

    def test_launcher_masks_control_and_secret_paths(self) -> None:
        launcher = (ROOT_READ_DIR / "aioms-root-read").read_text()
        for path in (
            "/run",
            "/dev",
            "/proc",
            "/sys",
            "/tmp",
            "/var/tmp",
            "/root /home/*",
            "/etc/ssh/ssh_host_*_key",
            "/etc/ssl/private",
            "/var/lib/docker",
            "/var/lib/kubelet",
            "/var/lib/mysql",
        ):
            with self.subTest(path=path):
                self.assertIn(path, launcher)

    def test_host_mount_parser_decodes_and_deduplicates_mountinfo(self) -> None:
        module_path = ROOT_READ_DIR / "host_mounts.py"
        module = runpy.run_path(str(module_path), run_name="root_read_host_mounts_test")
        parsed = module["_parse_mountinfo"](
            b"36 25 8:1 / / rw - ext4 /dev/root rw\n"
            b"40 36 8:2 / /data\\040disk rw - xfs /dev/sdb rw\n"
            b"41 36 8:3 / /data\\040disk rw - ext4 /dev/sdc rw\n"
        )
        self.assertEqual(
            parsed,
            [(b"/", b"ext4"), (b"/data disk", b"ext4")],
        )

    def test_host_mount_parser_uses_explicit_python_311(self) -> None:
        helper = (ROOT_READ_DIR / "host_mounts.py").read_text()
        launcher = (ROOT_READ_DIR / "aioms-root-read").read_text()
        installer = (ROOT_READ_DIR / "install.sh").read_text()
        self.assertTrue(helper.startswith("#!/usr/bin/python3.11\n"))
        self.assertIn('readonly HOST_PYTHON="/usr/bin/python3.11"', launcher)
        self.assertIn('"$HOST_PYTHON" "$HOST_MOUNTS_HELPER"', launcher)
        self.assertIn('readonly HOST_PYTHON="/usr/bin/python3.11"', installer)
        self.assertIn('"$HOST_PYTHON" "$SCRIPT_DIR/host_mounts.py"', installer)

    def test_sudoers_allows_only_the_no_argument_gateway(self) -> None:
        sudoers = (ROOT_READ_DIR / "aioms-root-read.sudoers").read_text()
        self.assertIn('/usr/local/sbin/aioms-root-read ""', sudoers)
        self.assertNotIn("NOPASSWD: ALL", sudoers)

    def test_entrypoint_accepts_pipeline_and_returns_json(self) -> None:
        return_code, payload = self._run_entrypoint(
            json.dumps({"command": "printf hello | tr a-z A-Z", "timeout": 2}).encode()
        )
        self.assertEqual(return_code, 0)
        self.assertEqual(payload["exit_code"], 0)
        self.assertEqual(payload["output"], "HELLO")

    def test_entrypoint_rejects_nul_after_json_decoding(self) -> None:
        return_code, payload = self._run_entrypoint(
            json.dumps({"command": "id\x00whoami", "timeout": 2}).encode()
        )
        self.assertEqual(return_code, 126)
        self.assertIn("控制字符", str(payload["output"]))

    def test_entrypoint_times_out_and_redacts_output(self) -> None:
        return_code, payload = self._run_entrypoint(
            json.dumps(
                {
                    "command": 'printf \'"password": "plain-text"\\n\'; sleep 2',
                    "timeout": 1,
                }
            ).encode()
        )
        self.assertEqual(return_code, 124)
        self.assertTrue(payload["timed_out"])
        self.assertNotIn("plain-text", str(payload["output"]))
