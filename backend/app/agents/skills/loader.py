from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from app.agents.skills.models import (
    SkillDefinition,
    SkillDiagnostic,
    SkillLoadResult,
)

_SKILL_NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_MAX_SKILL_BYTES = 128 * 1024


def _location(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root.parent).as_posix()
    except ValueError:
        return path.name


def _split_frontmatter(text: str) -> tuple[Mapping[str, Any], str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("缺少起始 YAML frontmatter 分隔符 `---`")
    try:
        closing_index = next(
            index
            for index, line in enumerate(lines[1:], start=1)
            if line.strip() == "---"
        )
    except StopIteration as exc:
        raise ValueError("缺少结束 YAML frontmatter 分隔符 `---`") from exc

    try:
        raw_metadata = yaml.safe_load("\n".join(lines[1:closing_index])) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"YAML frontmatter 无法解析：{exc}") from exc
    if not isinstance(raw_metadata, Mapping):
        raise ValueError("YAML frontmatter 必须是键值对象")

    body = "\n".join(lines[closing_index + 1 :]).strip()
    if not body:
        raise ValueError("Skill 正文不能为空")
    return raw_metadata, body


def _required_text(metadata: Mapping[str, Any], field: str, maximum: int) -> str:
    value = metadata.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"字段 `{field}` 必须是非空字符串")
    normalized = value.strip()
    if len(normalized) > maximum:
        raise ValueError(f"字段 `{field}` 不能超过 {maximum} 个字符")
    return normalized


def _optional_text(
    metadata: Mapping[str, Any], field: str, default: str, maximum: int
) -> str:
    value = metadata.get(field, default)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"字段 `{field}` 必须是非空字符串")
    normalized = value.strip()
    if len(normalized) > maximum:
        raise ValueError(f"字段 `{field}` 不能超过 {maximum} 个字符")
    return normalized


def _string_tuple(metadata: Mapping[str, Any], field: str) -> tuple[str, ...]:
    value = metadata.get(field, [])
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"字段 `{field}` 必须是字符串数组")
    return tuple(dict.fromkeys(item.strip() for item in value if item.strip()))


def _parse_skill(path: Path, root: Path) -> SkillDefinition:
    if path.stat().st_size > _MAX_SKILL_BYTES:
        raise ValueError(f"文件超过 {_MAX_SKILL_BYTES // 1024} KiB 上限")
    metadata, body = _split_frontmatter(path.read_text(encoding="utf-8"))

    name = _required_text(metadata, "name", 64)
    if not _SKILL_NAME_PATTERN.fullmatch(name):
        raise ValueError("字段 `name` 只能使用小写字母、数字和单个连字符")
    description = _required_text(metadata, "description", 1024)
    disabled = metadata.get("disable-model-invocation", False)
    if not isinstance(disabled, bool):
        raise ValueError("字段 `disable-model-invocation` 必须是布尔值")

    return SkillDefinition(
        name=name,
        description=description,
        body=body,
        path=path,
        location=_location(path, root),
        version=_optional_text(metadata, "version", "1.0.0", 32),
        category=_optional_text(metadata, "category", "通用", 32),
        triggers=_string_tuple(metadata, "triggers"),
        tools=_string_tuple(metadata, "tools"),
        enabled=not disabled,
    )


def load_skills(root: Path) -> SkillLoadResult:
    """从独立目录发现 Skills；每次调用都读取磁盘，便于运行时更新。"""

    resolved_root = root.resolve()
    if not resolved_root.is_dir():
        return SkillLoadResult(
            skills=(),
            diagnostics=(
                SkillDiagnostic(
                    level="error",
                    location=root.as_posix(),
                    message="Skill 目录不存在",
                ),
            ),
        )

    skills: list[SkillDefinition] = []
    diagnostics: list[SkillDiagnostic] = []
    names: dict[str, str] = {}
    for path in sorted(resolved_root.rglob("SKILL.md")):
        location = _location(path, resolved_root)
        resolved_path = path.resolve()
        if not resolved_path.is_relative_to(resolved_root):
            diagnostics.append(
                SkillDiagnostic(
                    level="error",
                    location=location,
                    message="Skill 符号链接越出了配置目录，已忽略",
                )
            )
            continue
        try:
            skill = _parse_skill(resolved_path, resolved_root)
        except (OSError, UnicodeError, ValueError) as exc:
            diagnostics.append(
                SkillDiagnostic(level="error", location=location, message=str(exc))
            )
            continue

        if skill.name in names:
            diagnostics.append(
                SkillDiagnostic(
                    level="error",
                    location=location,
                    message=f"Skill 名称 `{skill.name}` 已由 {names[skill.name]} 注册",
                )
            )
            continue
        names[skill.name] = skill.location
        if path.parent.name != skill.name:
            diagnostics.append(
                SkillDiagnostic(
                    level="warning",
                    location=location,
                    message=f"目录名建议与 Skill 名称 `{skill.name}` 保持一致",
                )
            )
        skills.append(skill)

    return SkillLoadResult(skills=tuple(skills), diagnostics=tuple(diagnostics))
