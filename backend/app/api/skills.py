from __future__ import annotations

from fastapi import APIRouter

from app.agents.skills import load_skills
from app.core.config import settings
from app.schemas.skill import SkillListRead, SkillRead

router = APIRouter(prefix="/skills", tags=["skills"])


@router.get("", response_model=SkillListRead)
async def list_skills() -> SkillListRead:
    """只返回用户需要阅读的 Skill 名称、描述与正文。"""

    result = load_skills(settings.skills_dir)
    return SkillListRead(
        skills=[
            SkillRead(
                name=skill.name,
                description=skill.description,
                instructions=skill.body,
            )
            for skill in result.skills
        ],
    )
