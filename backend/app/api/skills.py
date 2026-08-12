from __future__ import annotations

from fastapi import APIRouter

from app.agents.skills import load_skills
from app.core.config import settings
from app.schemas.skill import SkillDiagnosticRead, SkillListRead, SkillRead

router = APIRouter(prefix="/skills", tags=["skills"])


@router.get("", response_model=SkillListRead)
async def list_skills() -> SkillListRead:
    """返回 Agent 运行时实际扫描到的 Skill 与加载诊断。"""

    result = load_skills(settings.skills_dir)
    return SkillListRead(
        skills=[
            SkillRead(
                name=skill.name,
                description=skill.description,
                instructions=skill.body,
                location=skill.location,
                version=skill.version,
                category=skill.category,
                triggers=list(skill.triggers),
                tools=list(skill.tools),
                enabled=skill.enabled,
                source=skill.source,
            )
            for skill in result.skills
        ],
        diagnostics=[
            SkillDiagnosticRead(
                level=diagnostic.level,
                location=diagnostic.location,
                message=diagnostic.message,
            )
            for diagnostic in result.diagnostics
        ],
    )
