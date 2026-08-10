from __future__ import annotations

MAX_NARRATION_CHARS = 96


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
