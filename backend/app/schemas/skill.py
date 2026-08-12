from __future__ import annotations

from pydantic import BaseModel


class SkillRead(BaseModel):
    name: str
    description: str
    instructions: str


class SkillListRead(BaseModel):
    skills: list[SkillRead]
