from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ACTIONS_DIR = PROJECT_ROOT / "scripts" / "target-server" / "actions"


def _replace_once(content: str, old: str, new: str) -> str:
    if content.count(old) != 1:
        raise AssertionError(f"测试夹具无法唯一替换：{old}")
    return content.replace(old, new, 1)


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


class ServiceControlGatewayTest(unittest.TestCase):
    def _prepare_script(
        self,
        script_name: str,
        temporary_root: Path,
        *,
        allowlist: Path,
    ) -> Path:
        content = (ACTIONS_DIR / script_name).read_text(encoding="utf-8")
        content = _replace_once(
            content,
            "PATH=/usr/sbin:/usr/bin:/usr/local/bin:/sbin:/bin",
            f"PATH={temporary_root / 'bin'}:/usr/bin:/bin",
        )
        content = _replace_once(
            content,
            '[[ $EUID -eq 0 ]] || fail "必须由 root 通过 sudoers 调用"',
            ": # 测试在临时目录中运行，不需要 root",
        )
        if script_name == "aioms-service-policy":
            content = _replace_once(
                content,
                'readonly POLICY_DIR="/etc/aioms"',
                f'readonly POLICY_DIR="{allowlist.parent}"',
            )
            content = _replace_once(
                content,
                'readonly LOCK_FILE="/run/lock/aioms-service-policy.lock"',
                f'readonly LOCK_FILE="{temporary_root / "policy.lock"}"',
            )
            content = _replace_once(
                content,
                "read -r dir_owner dir_mode < <(stat -c '%u %a' \"$POLICY_DIR\")",
                "dir_owner=0; dir_mode=755",
            )
        else:
            content = _replace_once(
                content,
                'readonly ALLOWLIST="/etc/aioms/service-control.allowlist"',
                f'readonly ALLOWLIST="{allowlist}"',
            )
        content = _replace_once(
            content,
            "read -r owner mode < <(stat -c '%u %a' \"$ALLOWLIST\")",
            "owner=0; mode=644",
        )
        if script_name == "aioms-service-policy":
            content = _replace_once(content, 'chown root:root "$tmp"', ":")

        script = temporary_root / script_name
        _write_executable(script, content)
        return script

    def test_control_accepts_canonical_typed_systemd_entry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            state = root / "systemd.state"
            allowlist = root / "aioms" / "service-control.allowlist"
            allowlist.parent.mkdir()
            allowlist.write_text(
                "systemd:pixel-forge-frontend.service\n", encoding="utf-8"
            )
            _write_executable(
                bin_dir / "systemctl",
                """#!/bin/bash
if [[ "$1" == "show" ]]; then
    echo loaded
elif [[ "$1" == "is-active" ]]; then
    [[ -f "$AIOMS_TEST_STATE" ]] && echo active || echo inactive
elif [[ "$1" == "start" ]]; then
    touch "$AIOMS_TEST_STATE"
else
    exit 1
fi
""",
            )
            script = self._prepare_script(
                "aioms-service-control", root, allowlist=allowlist
            )

            result = subprocess.run(
                [
                    "bash",
                    str(script),
                    "start",
                    "systemd",
                    "pixel-forge-frontend.service",
                ],
                capture_output=True,
                check=False,
                env={
                    **os.environ,
                    "AIOMS_TEST_STATE": str(state),
                    "LC_ALL": "C",
                },
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("当前状态：active", result.stdout)

    def test_policy_normalizes_repeated_systemd_prefixes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            allowlist = root / "aioms" / "service-control.allowlist"
            allowlist.parent.mkdir()
            allowlist.write_text(
                "\n".join(
                    (
                        "systemd:pixel-forge-frontend.service",
                        "systemd:systemd:systemd:mysqld.service",
                        "systemd:systemd:nginx.service",
                        "systemd:systemd:pixel-forge-frontend.service",
                    )
                )
                + "\n",
                encoding="utf-8",
            )
            _write_executable(
                bin_dir / "systemctl",
                "#!/bin/bash\necho loaded\n",
            )
            _write_executable(bin_dir / "flock", "#!/bin/bash\nexit 0\n")
            script = self._prepare_script(
                "aioms-service-policy", root, allowlist=allowlist
            )

            result = subprocess.run(
                [
                    "bash",
                    str(script),
                    "add",
                    "systemd",
                    "pixel-forge-frontend.service",
                ],
                capture_output=True,
                check=False,
                env={**os.environ, "LC_ALL": "C"},
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                allowlist.read_text(encoding="utf-8").splitlines(),
                [
                    "systemd:mysqld.service",
                    "systemd:nginx.service",
                    "systemd:pixel-forge-frontend.service",
                ],
            )

    def test_installer_parses_typed_entries_before_legacy_entries(self) -> None:
        installer = (ACTIONS_DIR / "install.sh").read_text(encoding="utf-8")
        typed_branch = 'if [[ "$target" =~ ^systemd:(.+)$ ]]; then'
        legacy_branch = (
            'elif [[ "$target" =~ ^[A-Za-z0-9][A-Za-z0-9_.@:-]*\\.service$ ]]; then'
        )
        self.assertLess(installer.index(typed_branch), installer.index(legacy_branch))
        self.assertIn('target="systemd:$runtime_name"', installer)


if __name__ == "__main__":
    unittest.main()
