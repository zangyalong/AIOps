from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class SkillRead(BaseModel):
    name: str
    description: str
    instructions: str
    location: str
    version: str
    category: str
    triggers: list[str]
    tools: list[str]
    enabled: bool
    source: str


class SkillDiagnosticRead(BaseModel):
    level: Literal["warning", "error"]
    location: str
    message: str


class SkillListRead(BaseModel):
    skills: list[SkillRead]
    diagnostics: list[SkillDiagnosticRead]
