from __future__ import annotations

import re
from collections.abc import Iterable
from urllib.parse import urlsplit

MAX_NARRATION_CHARS = 96

_HTTP_URL_PATTERN = re.compile(r'https?://[^\s<>"\']+')
_MARKDOWN_LINK_PATTERN = re.compile(r"\[([^\]\n]+)\]\((https?://[^\s)]+)\)")
_TRUNCATED_URL_PATTERN = re.compile(r"https?://[^\s<>\])]*(?:\.\.\.|…)[^\s<>\])]*")
_TRUNCATION_MARKERS = ("...", "…")
_TRAILING_URL_PUNCTUATION = ".,;!?，。；！？"


def compact_narration(content: str) -> str:
    """把工具调用前文字收束成一条可展示的专家进度。"""
    value = " ".join(content.split())
    if not value:
        return ""
    sentence_end = next(
        (index + 1 for index, char in enumerate(value) if char in "。！？!?"),
        None,
    )
    if sentence_end is not None:
        value = value[:sentence_end]
    if len(value) > MAX_NARRATION_CHARS:
        value = value[: MAX_NARRATION_CHARS - 1].rstrip("，,；;：: ") + "…"
    return value


def extract_http_urls(contents: Iterable[str]) -> tuple[str, ...]:
    """从可信的工具结果中提取完整 HTTP(S) URL，并保持原始顺序去重。"""
    urls: list[str] = []
    seen: set[str] = set()
    for content in contents:
        for match in _HTTP_URL_PATTERN.finditer(content):
            url = match.group(0).rstrip(_TRAILING_URL_PUNCTUATION)
            if url not in seen:
                urls.append(url)
                seen.add(url)
    return tuple(urls)


def _matching_original_url(
    truncated_url: str, originals: tuple[str, ...]
) -> str | None:
    marker_positions = [
        position
        for marker in _TRUNCATION_MARKERS
        if (position := truncated_url.find(marker)) >= 0
    ]
    if not marker_positions:
        return None

    prefix = truncated_url[: min(marker_positions)]
    prefix_matches = [url for url in originals if url.startswith(prefix)]
    if len(prefix_matches) == 1:
        return prefix_matches[0]

    # 模型有时会在省略前顺手改写 query 编码。此时只在同源、同 path 且候选唯一时恢复，
    # 避免一个工具结果含多条链接时误指向另一张图。
    parsed = urlsplit(prefix)
    path_matches = [
        url
        for url in originals
        if (
            (candidate := urlsplit(url)).scheme,
            candidate.netloc,
            candidate.path,
        )
        == (parsed.scheme, parsed.netloc, parsed.path)
    ]
    return path_matches[0] if len(path_matches) == 1 else None


def restore_truncated_tool_links(content: str, tool_outputs: Iterable[str]) -> str:
    """用工具原始输出恢复模型以省略号截断的链接。

    模型仍负责文字总结，但不能成为长 URL 的数据源。只有截断链接能和某一条工具原始
    URL 唯一匹配时才替换；普通链接和无法判定的内容原样保留。
    """
    originals = extract_http_urls(tool_outputs)
    if not originals or not any(marker in content for marker in _TRUNCATION_MARKERS):
        return content

    def replace_markdown(match: re.Match[str]) -> str:
        original = _matching_original_url(match.group(2), originals)
        if original is None:
            return match.group(0)
        return f"[打开原始链接]({original})"

    restored = _MARKDOWN_LINK_PATTERN.sub(replace_markdown, content)

    def replace_plain(match: re.Match[str]) -> str:
        return _matching_original_url(match.group(0), originals) or match.group(0)

    return _TRUNCATED_URL_PATTERN.sub(replace_plain, restored)
