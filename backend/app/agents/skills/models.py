from dataclasses import dataclass
from pathlib import Path
from typing import Literal

DiagnosticLevel = Literal["warning", "error"]


@dataclass(frozen=True, slots=True)
class SkillDefinition:
    """一个通过校验、可以被 Agent 按需读取的教程型 Skill。"""

    name: str
    description: str
    body: str
    path: Path
    location: str


@dataclass(frozen=True, slots=True)
class SkillDiagnostic:
    """Skill 发现诊断；无效 Skill 不会进入模型上下文。"""

    level: DiagnosticLevel
    location: str
    message: str


@dataclass(frozen=True, slots=True)
class SkillLoadResult:
    skills: tuple[SkillDefinition, ...]
    diagnostics: tuple[SkillDiagnostic, ...]
