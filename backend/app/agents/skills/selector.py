from __future__ import annotations

from langchain_core.messages import BaseMessage, HumanMessage, ToolMessage

from app.agents.skills.models import SkillDefinition


def _ascii_word_character(character: str) -> bool:
    return character.isascii() and (character.isalnum() or character == "_")


def _trigger_matches(text: str, trigger: str) -> bool:
    """英文触发词按边界匹配；中文短语允许自然地嵌在句子中。"""

    if not trigger.isascii():
        return trigger in text

    offset = 0
    while (index := text.find(trigger, offset)) >= 0:
        end = index + len(trigger)
        left_matches = index == 0 or not _ascii_word_character(text[index - 1])
        right_matches = end == len(text) or not _ascii_word_character(text[end])
        if left_matches and right_matches:
            return True
        offset = index + 1
    return False


def latest_user_text(messages: list[BaseMessage]) -> str:
    """只用最近一轮用户意图选 Skill，避免旧话题污染当前上下文。"""

    for message in reversed(messages):
        if not isinstance(message, HumanMessage):
            continue
        if isinstance(message.content, str):
            return message.content
        text_blocks: list[str] = []
        for block in message.content:
            if isinstance(block, str):
                text_blocks.append(block)
            elif isinstance(block, dict) and isinstance(block.get("text"), str):
                text_blocks.append(block["text"])
        return "\n".join(text_blocks)
    return ""


def skill_selection_text(messages: list[BaseMessage]) -> str:
    """组合最近用户意图和本轮工具事实，让后续步骤可以激活新发现的 Skill。"""

    user_text = latest_user_text(messages)
    trailing_tool_text: list[str] = []
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            break
        if not isinstance(message, ToolMessage):
            continue
        if isinstance(message.content, str):
            trailing_tool_text.append(message.content[:4000])
    trailing_tool_text.reverse()
    return "\n".join([user_text, *trailing_tool_text])


def select_skills(
    skills: tuple[SkillDefinition, ...], user_text: str, *, limit: int = 3
) -> tuple[SkillDefinition, ...]:
    """按显式触发词做确定性选择，不额外消耗一次 LLM 调用。"""

    normalized_text = user_text.casefold()
    scored: list[tuple[int, SkillDefinition]] = []
    for skill in skills:
        if not skill.enabled:
            continue
        matched = {
            trigger.casefold()
            for trigger in skill.triggers
            if _trigger_matches(normalized_text, trigger.casefold())
        }
        if not matched:
            continue
        score = sum(len(trigger) for trigger in matched) + len(matched) * 8
        scored.append((score, skill))

    scored.sort(key=lambda item: (-item[0], item[1].name))
    return tuple(skill for _, skill in scored[:limit])
