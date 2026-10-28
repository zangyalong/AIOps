from __future__ import annotations

import json
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FILES_DIR = PROJECT_ROOT / "scripts" / "target-server" / "files"
PATCH_HELPER = FILES_DIR / "config_patch.py"


class ConfigPatchHelperTest(unittest.TestCase):
    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(PATCH_HELPER), *args],
            capture_output=True,
            check=False,
            text=True,
            timeout=5,
        )

    def test_dotenv_patch_preserves_unrelated_secrets_and_format(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / ".env"
            output = Path(directory) / "updated"
            source.write_text(
                "POSTGRES_PASSWORD=keep-real-secret\n"
                "export SERVER_PORT = 8080\n"
                "REDIS_PASSWORD=also-keep\n"
            )
            output.touch()

            preview = self._run(
                "preview", str(source), "dotenv", "SERVER_PORT", '"8081"'
            )
            applied = self._run(
                "apply",
                str(source),
                str(output),
                "dotenv",
                "SERVER_PORT",
                '"8081"',
            )

            self.assertEqual(preview.returncode, 0, preview.stderr)
            self.assertEqual(applied.returncode, 0, applied.stderr)
            self.assertNotIn("keep-real-secret", preview.stdout)
            self.assertNotIn("also-keep", preview.stdout)
            self.assertEqual(json.loads(preview.stdout)["before"], "8080")
            self.assertEqual(json.loads(preview.stdout)["line"], 2)
            self.assertEqual(
                output.read_text(),
                "POSTGRES_PASSWORD=keep-real-secret\n"
                "export SERVER_PORT = 8081\n"
                "REDIS_PASSWORD=also-keep\n",
            )

    def test_json_pointer_replaces_only_the_selected_scalar_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "app.json"
            output = Path(directory) / "updated"
            content = (
                '{\n  "server": { "port": 8080, "host": "127.0.0.1" },\n'
                '  "password": "keep-real-secret"\n}\n'
            )
            source.write_text(content)
            output.touch()

            applied = self._run(
                "apply",
                str(source),
                str(output),
                "json",
                "/server/port",
                "8081",
            )

            self.assertEqual(applied.returncode, 0, applied.stderr)
            self.assertNotIn("keep-real-secret", applied.stdout)
            self.assertEqual(json.loads(applied.stdout)["line"], 2)
            self.assertEqual(output.read_text(), content.replace("8080", "8081"))

    def test_yaml_pointer_replaces_compose_port_without_returning_secrets(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "docker-compose.yml"
            output = Path(directory) / "updated"
            content = (
                "services:\n"
                "  example-api:\n"
                "    # 只修改宿主机端口，容器端口保持 8080\n"
                "    ports:\n"
                '      - "${SERVER_PORT:-8080}:8080"\n'
                "    environment:\n"
                "      POSTGRES_PASSWORD: keep-real-secret\n"
            )
            source.write_text(content)
            output.touch()

            preview = self._run(
                "preview",
                str(source),
                "yaml",
                "/services/example-api/ports/0",
                '"${SERVER_PORT:-8081}:8080"',
            )
            applied = self._run(
                "apply",
                str(source),
                str(output),
                "yaml",
                "/services/example-api/ports/0",
                '"${SERVER_PORT:-8081}:8080"',
            )

            self.assertEqual(preview.returncode, 0, preview.stderr)
            self.assertEqual(applied.returncode, 0, applied.stderr)
            self.assertNotIn("keep-real-secret", preview.stdout)
            self.assertEqual(
                json.loads(preview.stdout)["before"],
                "${SERVER_PORT:-8080}:8080",
            )
            self.assertEqual(json.loads(preview.stdout)["line"], 5)
            self.assertEqual(
                output.read_text(),
                content.replace(
                    '"${SERVER_PORT:-8080}:8080"',
                    '"${SERVER_PORT:-8081}:8080"',
                ),
            )

    def test_sensitive_selector_and_ambiguous_keys_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            env_file.write_text("JWT_SECRET=old\n")
            json_file = Path(directory) / "app.json"
            json_file.write_text('{"port": 8080, "port": 8081}\n')

            sensitive = self._run(
                "preview", str(env_file), "dotenv", "JWT_SECRET", '"new"'
            )
            duplicate = self._run("preview", str(json_file), "json", "/port", "8082")

            self.assertNotEqual(sensitive.returncode, 0)
            self.assertIn("敏感字段", sensitive.stderr)
            self.assertNotEqual(duplicate.returncode, 0)
            self.assertIn("重复键", duplicate.stderr)

    def test_yaml_sensitive_selector_duplicate_key_and_alias_are_rejected(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp_dir = Path(directory)
            sensitive_file = temp_dir / "sensitive.yaml"
            sensitive_file.write_text("services:\n  app:\n    password: old\n")
            duplicate_file = temp_dir / "duplicate.yaml"
            duplicate_file.write_text("services:\n  app: one\n  app: two\n")
            alias_file = temp_dir / "alias.yaml"
            alias_file.write_text("port: &port 8080\nother_port: *port\n")

            sensitive = self._run(
                "preview",
                str(sensitive_file),
                "yaml",
                "/services/app/password",
                '"new"',
            )
            duplicate = self._run(
                "preview",
                str(duplicate_file),
                "yaml",
                "/services/app",
                '"three"',
            )
            alias = self._run("preview", str(alias_file), "yaml", "/port", "8081")

            self.assertNotEqual(sensitive.returncode, 0)
            self.assertIn("敏感字段", sensitive.stderr)
            self.assertNotEqual(duplicate.returncode, 0)
            self.assertIn("重复键", duplicate.stderr)
            self.assertNotEqual(alias.returncode, 0)
            self.assertIn("别名", alias.stderr)


class FileGatewayContractTest(unittest.TestCase):
    def test_read_gateway_accepts_exact_read_arity(self) -> None:
        """实际执行 read 分支，防止 set -u 再次读取不存在的位置参数。"""
        with tempfile.TemporaryDirectory() as directory:
            temp_dir = Path(directory)
            source = temp_dir / "app.conf"
            source.write_text("port=8080\n")

            common = temp_dir / "file-common"
            common.write_text(
                "MAX_FILE_BYTES=65536\n"
                'file_fail() { echo "aiops-file-read: $*" >&2; exit 1; }\n'
                "require_root_gateway() { :; }\n"
                "validate_policy_file() { :; }\n"
                "canonical_existing_path() { printf '%s\\n' \"$1\"; }\n"
                "reject_sensitive_path() { :; }\n"
                "policy_allows_read() { return 0; }\n"
                "validate_text_file() { :; }\n"
            )
            gateway = temp_dir / "aiops-file-read"
            gateway.write_text(
                (FILES_DIR / "aiops-file-read")
                .read_text()
                .replace(
                    'readonly COMMON="/usr/local/libexec/aiops/file-common"',
                    f"readonly COMMON={shlex.quote(str(common))}",
                )
            )

            result = subprocess.run(
                ["bash", str(gateway), "read", str(source)],
                capture_output=True,
                check=False,
                text=True,
                timeout=5,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"PATH={source}", result.stdout)
        self.assertIn("REDACTED=false", result.stdout)
        self.assertTrue(result.stdout.endswith("---\nport=8080\n"))

    def test_fixed_gateways_expose_structured_patch_without_service_actions(
        self,
    ) -> None:
        reader = (FILES_DIR / "aiops-file-read").read_text()
        writer = (FILES_DIR / "aiops-file-write").read_text()
        installer = (FILES_DIR / "install.sh").read_text()
        sudoers = (FILES_DIR / "aiops-files.sudoers").read_text()

        self.assertIn("config-preview", reader)
        self.assertIn("policy_allows_write", reader)
        self.assertIn("config-apply", writer)
        self.assertIn('"$CONFIG_PATCH_HELPER"', writer)
        self.assertIn('cp -a -- "$target" "$backup"', writer)
        self.assertIn('mv -f -- "$tmp" "$target"', writer)
        self.assertNotIn("apply-nginx", writer)
        self.assertNotIn("nginx", writer.lower())
        self.assertNotIn("reload", writer.lower())
        self.assertIn("config_patch.py", installer)
        self.assertIn("import yaml", installer)
        common = (FILES_DIR / "aiops-file-common").read_text()
        helper = PATCH_HELPER.read_text()
        self.assertIn('readonly HOST_PYTHON="/usr/bin/python3.12"', installer)
        self.assertIn('CONFIG_PATCH_PYTHON="/usr/bin/python3.12"', common)
        self.assertTrue(helper.startswith("#!/usr/bin/python3.12\n"))
        self.assertNotIn("NOPASSWD: ALL", sudoers)

    def test_gateway_shell_scripts_parse(self) -> None:
        for script_name in (
            "aiops-file-common",
            "aiops-file-read",
            "aiops-file-write",
            "install.sh",
        ):
            with self.subTest(script=script_name):
                result = subprocess.run(
                    ["bash", "-n", str(FILES_DIR / script_name)],
                    capture_output=True,
                    check=False,
                    text=True,
                )
                self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
