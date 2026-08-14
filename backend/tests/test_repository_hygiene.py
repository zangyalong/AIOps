from __future__ import annotations

import ipaddress
import re
import subprocess
import unittest
from pathlib import Path
from urllib.parse import unquote, urlsplit

REPO_ROOT = Path(__file__).resolve().parents[2]
IPV4_PATTERN = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
LOCAL_HOME_PATTERNS = (
    re.compile("/" + r"Users/[^/\s]+/"),
    re.compile(r"[A-Za-z]:\\" + r"Users\\[^\\\s]+\\"),
)
PRIVATE_KEY_PATTERN = re.compile(
    r"^-----BEGIN (?:RSA |OPENSSH |EC |DSA )?PRIVATE KEY-----$",
    re.MULTILINE,
)
HIGH_CONFIDENCE_TOKEN_PATTERNS = (
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]{24,}=*\b", re.IGNORECASE),
)
MARKDOWN_LINK_PATTERN = re.compile(r"!?\[[^\]]*\]\((?P<target><[^>]+>|[^)\s]+)")
HTML_LINK_PATTERN = re.compile(
    r'<(?:a|img)\b[^>]*\b(?:href|src)="(?P<target>[^"]+)"', re.IGNORECASE
)


def _repository_files() -> list[Path]:
    """返回已跟踪和准备加入 Git 的文件；遵守 .gitignore。"""
    result = subprocess.run(
        [
            "git",
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
            "-z",
        ],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
    )
    candidates = [
        REPO_ROOT / item.decode() for item in result.stdout.split(b"\0") if item
    ]
    # `--cached` 也会返回工作树中已删除、尚未暂存的旧路径；移动文件时跳过这些路径。
    return [path for path in candidates if path.is_file()]


def _read_text(path: Path) -> str | None:
    content = path.read_bytes()
    if b"\0" in content:
        return None
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        return None


class RepositoryHygieneTest(unittest.TestCase):
    def test_git_files_do_not_contain_sensitive_environment_data(self) -> None:
        findings: list[str] = []
        for path in _repository_files():
            text = _read_text(path)
            if text is None:
                continue
            relative = path.relative_to(REPO_ROOT)

            for match in IPV4_PATTERN.finditer(text):
                try:
                    address = ipaddress.ip_address(match.group())
                except ValueError:
                    continue
                if address.is_global and not address.is_multicast:
                    line = text.count("\n", 0, match.start()) + 1
                    findings.append(
                        f"{relative}:{line} 包含公网 IPv4；请改用环境配置、Git 忽略文件或 RFC 5737 文档地址"
                    )

            for pattern in LOCAL_HOME_PATTERNS:
                for match in pattern.finditer(text):
                    line = text.count("\n", 0, match.start()) + 1
                    findings.append(f"{relative}:{line} 包含开发者本机绝对路径")

            if PRIVATE_KEY_PATTERN.search(text):
                findings.append(f"{relative} 包含私钥正文")

            if any(pattern.search(text) for pattern in HIGH_CONFIDENCE_TOKEN_PATTERNS):
                findings.append(f"{relative} 包含疑似真实访问令牌")

        self.assertEqual(findings, [], "\n" + "\n".join(findings))

    def test_markdown_relative_links_resolve_inside_repository(self) -> None:
        findings: list[str] = []
        repository_root = REPO_ROOT.resolve()
        for path in _repository_files():
            if path.suffix.lower() != ".md":
                continue
            text = _read_text(path)
            if text is None:
                continue
            relative = path.relative_to(REPO_ROOT)

            for pattern in (MARKDOWN_LINK_PATTERN, HTML_LINK_PATTERN):
                for match in pattern.finditer(text):
                    target = match.group("target").strip("<>")
                    parsed = urlsplit(target)
                    if parsed.scheme or parsed.netloc or not parsed.path:
                        continue
                    resolved = (path.parent / unquote(parsed.path)).resolve()
                    line = text.count("\n", 0, match.start()) + 1
                    try:
                        resolved.relative_to(repository_root)
                    except ValueError:
                        findings.append(
                            f"{relative}:{line} 的相对链接指向仓库外：{target}"
                        )
                        continue
                    if not resolved.exists():
                        findings.append(f"{relative}:{line} 的相对链接不存在：{target}")

        self.assertEqual(findings, [], "\n" + "\n".join(findings))


if __name__ == "__main__":
    unittest.main()
