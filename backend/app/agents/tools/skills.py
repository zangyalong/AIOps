from __future__ import annotations

from langchain_core.tools import tool

from app.agents.skills import format_skill_content, load_skills
from app.core.config import settings


@tool("read_skill")
async def read_skill(name: str) -> str:
    """按名称读取已安装 Skill 的完整教程。

    系统提示词只提供 Skill 名称与描述。当当前任务明显符合某项描述时，先调用本工具读取
    正文，再按其中的方法工作。name 必须来自 available_skills，不能传文件路径。
    """

    normalized_name = name.strip().casefold()
    result = load_skills(settings.skills_dir)
    skill = next(
        (item for item in result.skills if item.name.casefold() == normalized_name),
        None,
    )
    if skill is None:
        available = "、".join(item.name for item in result.skills)
        raise ValueError(
            f"Skill `{normalized_name or name}` 未安装；当前可用：{available or '无'}"
        )
    return format_skill_content(skill)
