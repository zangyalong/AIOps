from __future__ import annotations

import re
from html import escape

from langchain_core.messages import BaseMessage, HumanMessage

from app.agents.skills.models import SkillDefinition

_SKILL_INVOCATION = re.compile(
    r"^\s*/skill:([a-z0-9]+(?:-[a-z0-9]+)*)(?=\s|$)\s*(.*)$",
    re.DOTALL | re.IGNORECASE,
)


def format_skill_content(skill: SkillDefinition) -> str:
    """把完整 Skill 包装成有明确来源的低优先级方法说明。"""

    location = skill.path.as_posix()
    return (
        f'<skill name="{escape(skill.name)}" location="{escape(location)}">\n'
        f"相对引用以 {escape(skill.path.parent.as_posix())} 为基准。\n\n"
        f"{skill.body}\n"
        "</skill>"
    )


def expand_explicit_skill_invocation(
    messages: list[BaseMessage],
    skills: tuple[SkillDefinition, ...],
) -> tuple[list[BaseMessage], SkillDefinition | None, str | None]:
    """将最新用户消息中的 `/skill:name` 展开，提供可验证的强制使用方式。"""

    message_index: int | None = None
    invocation: re.Match[str] | None = None
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if not isinstance(message, HumanMessage) or not isinstance(
            message.content, str
        ):
            continue
        message_index = index
        invocation = _SKILL_INVOCATION.match(message.content)
        break

    if message_index is None or invocation is None:
        return list(messages), None, None

    requested_name = invocation.group(1).casefold()
    skill = next(
        (item for item in skills if item.name.casefold() == requested_name),
        None,
    )
    if skill is None:
        available = "、".join(item.name for item in skills) or "无"
        return (
            list(messages),
            None,
            f"用户显式请求的 Skill `{requested_name}` 未安装；当前可用：{available}。",
        )

    request = invocation.group(2).strip() or "请按这个 Skill 的方法处理当前请求。"
    expanded = [*messages]
    expanded[message_index] = messages[message_index].model_copy(
        update={"content": f"{format_skill_content(skill)}\n\n{request}"}
    )
    return expanded, skill, None
