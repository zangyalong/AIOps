from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
import unittest
from collections.abc import Callable
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
APPLIER = PROJECT_ROOT / "scripts/target-server/apply-incoming-config.sh"


@unittest.skipUnless(
    os.name == "posix" and hasattr(os, "getuid") and shutil.which("flock"),
    "需要 Linux flock 和 Unix 文件权限",
)
class DeploymentConfigConcurrencyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.runtime = self.root / "runtime"
        self.state = self.root / "state"
        self.incoming = self.state / "incoming"
        self.runtime.mkdir()
        (self.runtime / "metrics").mkdir()
        self.incoming.mkdir(parents=True)
        self.started = self.root / "started"
        self.release = self.root / "release"
        self.validated = self.root / "validated"
        self.config = self.incoming / "deploy.conf"
        self.manifest = self.incoming / "deployment.manifest"
        # 只在临时副本中将 root 约束映射到测试用户，不运行真实网关或部署程序。
        source = APPLIER.read_text()
        source = source.replace("/opt/aiops-target-server", str(self.runtime))
        source = source.replace("/var/lib/aiops", str(self.state))
        source = source.replace(
            "/run/lock/aiops-target-deploy.lock", str(self.root / "deploy.lock")
        )
        source = source.replace("[[ $EUID -eq 0 ]]", f"[[ $EUID -eq {os.getuid()} ]]")
        source = source.replace(
            '[[ "$owner" == "0" ]]', f'[[ "$owner" == "{os.getuid()}" ]]'
        )
        source = source.replace(
            '"$owner" == "$SUDO_UID"', f'"$owner" == "{os.getuid()}"'
        )
        source = source.replace("install -o root -g root ", "install ")
        marker = 'verify_upload "$INCOMING_MANIFEST" "部署清单" 1024'
        source = source.replace(
            marker, marker + f"\nprintf '%s\\n' \"$$\" >> '{self.validated}'", 1
        )
        self.applier = self.runtime / "apply-incoming-config.sh"
        self.applier.write_text(source)
        self.applier.chmod(0o755)
        deploy = self.runtime / "deploy.sh"
        deploy.write_text(
            "#!/bin/bash\nset -euo pipefail\n"
            f"touch '{self.started}'\n"
            f"while [[ ! -f '{self.release}' ]]; do sleep 0.02; done\n"
        )
        deploy.chmod(0o755)
        (self.runtime / "deploy.conf").write_text("previous-config\n")
        self.environment = os.environ | {"SUDO_UID": str(os.getuid() or 1000)}

    def upload(self, content: str) -> None:
        self.config.write_text(content)
        self.config.chmod(0o600)
        self.manifest.write_text(
            "MYSQL_SECRET=delete\nPOSTGRES_SECRET=delete\nREDIS_SECRET=delete\n"
        )
        self.manifest.chmod(0o600)

    def start(self) -> subprocess.Popen[str]:
        process = subprocess.Popen(
            ["bash", str(self.applier)],
            env=self.environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.addCleanup(self.finish, process)
        return process

    def finish(self, process: subprocess.Popen[str]) -> None:
        self.release.touch()
        if process.poll() is None:
            try:
                process.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate(timeout=3)

    def wait_for(self, condition: Callable[[], bool]) -> None:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if condition():
                return
            time.sleep(0.02)
        self.fail("等待隔离部署测试条件超时")

    def test_concurrent_apply_rejects_before_shared_upload_validation(self) -> None:
        self.upload("first-config\n")
        first = self.start()
        self.wait_for(self.started.exists)
        self.upload("second-config\n")
        second = self.start()
        # 旧程序先验证再等锁；新程序会直接返回忙碌，不读取第二份上传。
        self.wait_for(
            lambda: (
                second.poll() is not None
                or len(self.validated.read_text().splitlines()) >= 2
            )
        )
        self.release.touch()
        first.communicate(timeout=3)
        stdout, stderr = second.communicate(timeout=3)
        self.assertNotEqual(second.returncode, 0)
        self.assertIn("已有环境部署正在执行", stdout + stderr)
        self.assertNotIn("cannot stat", stdout + stderr)
        self.assertEqual((self.runtime / "deploy.conf").read_text(), "first-config\n")
        # 第一次部署退出不能删除后来上传的配置。
        self.assertEqual(self.config.read_text(), "second-config\n")
        self.assertTrue(self.manifest.exists())

    def test_apply_consumes_upload_before_starting_long_running_deploy(self) -> None:
        self.upload("first-config\n")
        first = self.start()
        self.wait_for(self.started.exists)
        try:
            self.assertFalse(self.config.exists())
            self.assertFalse(self.manifest.exists())
        finally:
            self.release.touch()
            stdout, stderr = first.communicate(timeout=3)
        self.assertEqual(first.returncode, 0, stdout + stderr)

    def test_failed_deploy_still_restores_previous_config(self) -> None:
        deploy = self.runtime / "deploy.sh"
        deploy.write_text(
            "#!/bin/bash\nset -euo pipefail\n"
            f"if grep -q first-config '{self.runtime / 'deploy.conf'}'; then exit 7; fi\n"
        )
        self.upload("first-config\n")
        process = self.start()
        stdout, stderr = process.communicate(timeout=3)
        self.assertNotEqual(process.returncode, 0)
        self.assertIn("已恢复并重新应用上一版配置", stdout + stderr)
        self.assertEqual(
            (self.runtime / "deploy.conf").read_text(), "previous-config\n"
        )
        self.assertFalse(self.config.exists())


if __name__ == "__main__":
    unittest.main()
