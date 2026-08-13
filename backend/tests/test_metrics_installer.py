from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
INSTALLER = PROJECT_ROOT / "scripts" / "target-server" / "metrics" / "install.sh"


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


class MetricsInstallerImageTest(unittest.TestCase):
    def _prepare_harness(self, root: Path) -> Path:
        installer = INSTALLER.read_text(encoding="utf-8")
        start = installer.index("canonical_image_name() {")
        end = installer.index("\nfor image in \\\n", start)
        functions = installer[start:end]
        harness = root / "image-pull-harness.sh"
        _write_executable(
            harness,
            """#!/bin/bash
set -euo pipefail
fail() {
    echo "metrics/install.sh: $*" >&2
    exit 1
}
IMAGE_MIRROR_PREFIX="m.daocloud.io"
"""
            + functions
            + '\npull_exporter_image "$1"\n',
        )
        return harness

    def _run_case(
        self,
        *,
        mirror_pull_ok: bool,
        local_image_exists: bool,
    ) -> tuple[subprocess.CompletedProcess[str], list[str]]:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            docker_log = root / "docker.log"
            _write_executable(
                bin_dir / "docker",
                """#!/bin/bash
echo "$*" >>"$AIOMS_DOCKER_LOG"
if [[ "$1" == "pull" ]]; then
    [[ "$AIOMS_MIRROR_PULL_OK" == "true" ]]
elif [[ "$1" == "image" && "$2" == "inspect" ]]; then
    [[ "$AIOMS_LOCAL_IMAGE_EXISTS" == "true" ]]
elif [[ "$1" == "tag" || ( "$1" == "image" && "$2" == "rm" ) ]]; then
    exit 0
else
    exit 1
fi
""",
            )
            harness = self._prepare_harness(root)
            result = subprocess.run(
                ["bash", str(harness), "quay.io/oliver006/redis_exporter:v1.88.0"],
                capture_output=True,
                check=False,
                env={
                    **os.environ,
                    "AIOMS_DOCKER_LOG": str(docker_log),
                    "AIOMS_LOCAL_IMAGE_EXISTS": str(local_image_exists).lower(),
                    "AIOMS_MIRROR_PULL_OK": str(mirror_pull_ok).lower(),
                    "LC_ALL": "C",
                    "PATH": f"{bin_dir}:/usr/bin:/bin",
                },
                text=True,
            )
            commands = docker_log.read_text(encoding="utf-8").splitlines()
            return result, commands

    def test_reuses_exact_local_image_when_mirror_pull_fails(self) -> None:
        result, commands = self._run_case(
            mirror_pull_ok=False,
            local_image_exists=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("复用目标机已有镜像", result.stderr)
        self.assertEqual(
            commands,
            [
                "pull m.daocloud.io/quay.io/oliver006/redis_exporter:v1.88.0",
                "image inspect quay.io/oliver006/redis_exporter:v1.88.0",
            ],
        )

    def test_fails_when_mirror_and_local_image_are_both_unavailable(self) -> None:
        result, commands = self._run_case(
            mirror_pull_ok=False,
            local_image_exists=False,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("目标机不存在可复用镜像", result.stderr)
        self.assertEqual(len(commands), 2)

    def test_successful_mirror_pull_keeps_refresh_and_tag_flow(self) -> None:
        result, commands = self._run_case(
            mirror_pull_ok=True,
            local_image_exists=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            commands,
            [
                "pull m.daocloud.io/quay.io/oliver006/redis_exporter:v1.88.0",
                "tag m.daocloud.io/quay.io/oliver006/redis_exporter:v1.88.0 "
                "quay.io/oliver006/redis_exporter:v1.88.0",
                "image rm m.daocloud.io/quay.io/oliver006/redis_exporter:v1.88.0",
            ],
        )


if __name__ == "__main__":
    unittest.main()
