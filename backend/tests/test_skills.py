from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from langchain_core.messages import HumanMessage, ToolMessage

from app.agents.graphs import chat_graph
from app.agents.prompts import build_ops_system_prompt
from app.agents.skills import load_skills, select_skills, skill_selection_text
from app.api.skills import list_skills
from app.core.config import settings


def _write_skill(
    root: Path,
    directory: str,
    *,
    name: str,
    description: str = "测试 Skill",
    triggers: tuple[str, ...] = ("docker",),
) -> None:
    skill_dir = root / directory
    skill_dir.mkdir(parents=True)
    trigger_yaml = "\n".join(f"  - {trigger}" for trigger in triggers)
    (skill_dir / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                f"name: {name}",
                f"description: {description}",
                "version: 1.0.0",
                "category: 测试",
                "triggers:",
                trigger_yaml,
                "tools:",
                "  - investigate",
                "---",
                "# 测试说明",
                "只执行测试范围内的方法。",
            ]
        ),
        encoding="utf-8",
    )


class SkillLoaderTest(unittest.TestCase):
    def test_loads_valid_skill_and_reports_duplicate_name(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            _write_skill(root, "docker-operations", name="docker-operations")
            _write_skill(root, "duplicate", name="docker-operations")

            result = load_skills(root)

        self.assertEqual([skill.name for skill in result.skills], ["docker-operations"])
        self.assertTrue(
            any("已由" in diagnostic.message for diagnostic in result.diagnostics)
        )

    def test_rejects_missing_frontmatter(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            skill_dir = root / "broken"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text("# 没有元数据", encoding="utf-8")

            result = load_skills(root)

        self.assertFalse(result.skills)
        self.assertEqual(result.diagnostics[0].level, "error")
        self.assertIn("frontmatter", result.diagnostics[0].message)

    def test_selects_only_matching_enabled_skills(self) -> None:
        result = load_skills(settings.skills_dir)

        selected = select_skills(result.skills, "检查 Docker Compose 的端口映射")

        names = {skill.name for skill in selected}
        self.assertIn("docker-operations", names)
        self.assertIn("docker-compose-operations", names)
        self.assertNotIn("systemd-operations", names)

    def test_ascii_trigger_does_not_match_inside_another_word(self) -> None:
        selected = select_skills(
            load_skills(settings.skills_dir).skills,
            "check the community health page",
        )

        self.assertNotIn("systemd-operations", {skill.name for skill in selected})

    def test_tool_evidence_can_activate_skill_after_initial_investigation(self) -> None:
        text = skill_selection_text(
            [
                HumanMessage("把 sub2api 的端口改成 8081 并生效"),
                ToolMessage(
                    "发现目标由 Docker Compose 管理，端口使用 SERVER_PORT 插值",
                    tool_call_id="investigate-1",
                ),
            ]
        )
        selected = select_skills(load_skills(settings.skills_dir).skills, text)

        self.assertIn("docker-compose-operations", {skill.name for skill in selected})


class RuntimePromptTest(unittest.TestCase):
    def test_runtime_prompt_keeps_core_and_safety_but_limits_tool_catalog(self) -> None:
        assembly = build_ops_system_prompt(
            messages=[HumanMessage("查看 systemd 的 nginx.service 状态")],
            tools=chat_graph._ops_tools(False),
            allow_write_tools=False,
            run_mode="unattended",
        )

        self.assertIn("# 固定运维核心", assembly.text)
        self.assertIn("# 固定安全边界", assembly.text)
        self.assertIn("本轮是只读模式", assembly.text)
        self.assertIn('<tool name="investigate"', assembly.text)
        self.assertNotIn('<tool name="restart_service"', assembly.text)
        self.assertEqual(
            [skill.name for skill in assembly.active_skills], ["systemd-operations"]
        )

    def test_prompt_is_reassembled_from_current_skill_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            _write_skill(root, "custom-check", name="custom-check", triggers=("redis",))
            with patch.object(settings, "skills_dir", root):
                first = build_ops_system_prompt(
                    messages=[HumanMessage("检查 Redis")],
                    tools=chat_graph._ops_tools(False),
                    allow_write_tools=False,
                    run_mode="interactive",
                )
                _write_skill(
                    root,
                    "second-check",
                    name="second-check",
                    triggers=("postgres",),
                )
                second = build_ops_system_prompt(
                    messages=[HumanMessage("检查 Postgres")],
                    tools=chat_graph._ops_tools(False),
                    allow_write_tools=False,
                    run_mode="interactive",
                )

        self.assertEqual(
            [skill.name for skill in first.active_skills], ["custom-check"]
        )
        self.assertEqual(
            [skill.name for skill in second.active_skills], ["second-check"]
        )
        self.assertIn("second-check", second.text)


class SkillApiTest(unittest.IsolatedAsyncioTestCase):
    async def test_lists_the_same_installed_skills(self) -> None:
        response = await list_skills()

        self.assertGreaterEqual(len(response.skills), 4)
        self.assertIn("docker-operations", {skill.name for skill in response.skills})
        self.assertFalse(response.diagnostics)
