from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from langchain_core.messages import HumanMessage

from app.agents.graphs import chat_graph
from app.agents.prompts import build_ops_system_prompt
from app.agents.skills import expand_explicit_skill_invocation, load_skills
from app.agents.tools.skills import read_skill
from app.api.skills import list_skills
from app.core.config import settings


def _write_skill(
    root: Path,
    directory: str,
    *,
    name: str,
    description: str = "测试 Skill",
    body: str = "# 测试说明\n\n按证据顺序完成调查。",
) -> None:
    skill_dir = root / directory
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                f"name: {name}",
                f"description: {description}",
                "---",
                body,
            ]
        ),
        encoding="utf-8",
    )


class SkillLoaderTest(unittest.TestCase):
    def test_loads_tutorial_skill_and_reports_duplicate_name(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            _write_skill(root, "docker-operations", name="docker-operations")
            _write_skill(root, "duplicate", name="docker-operations")

            result = load_skills(root)

        self.assertEqual([skill.name for skill in result.skills], ["docker-operations"])
        self.assertEqual(result.skills[0].description, "测试 Skill")
        self.assertIn("按证据顺序完成调查", result.skills[0].body)
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

    def test_regular_request_does_not_deterministically_expand_a_skill(self) -> None:
        installed = load_skills(settings.skills_dir).skills
        original = HumanMessage("检查 Docker Compose 的端口映射")

        messages, skill, error = expand_explicit_skill_invocation([original], installed)

        self.assertIsNone(skill)
        self.assertIsNone(error)
        self.assertEqual(messages, [original])

    def test_explicit_invocation_expands_full_skill_and_user_request(self) -> None:
        installed = load_skills(settings.skills_dir).skills

        messages, skill, error = expand_explicit_skill_invocation(
            [HumanMessage("/skill:docker-operations 检查 orders-api 为什么退出")],
            installed,
        )

        self.assertEqual(skill.name if skill else None, "docker-operations")
        self.assertIsNone(error)
        content = str(messages[0].content)
        self.assertIn('<skill name="docker-operations"', content)
        self.assertIn("# Docker 容器排障", content)
        self.assertIn("检查 orders-api 为什么退出", content)

    def test_unknown_explicit_invocation_reports_available_names(self) -> None:
        installed = load_skills(settings.skills_dir).skills

        messages, skill, error = expand_explicit_skill_invocation(
            [HumanMessage("/skill:not-installed 检查服务")], installed
        )

        self.assertIsNone(skill)
        self.assertEqual(messages[0].content, "/skill:not-installed 检查服务")
        self.assertIn("未安装", error or "")
        self.assertIn("systemd-operations", error or "")


class RuntimePromptTest(unittest.TestCase):
    def test_prompt_keeps_core_and_safety_but_only_indexes_skills(self) -> None:
        assembly = build_ops_system_prompt(
            tools=chat_graph._ops_tools(False),
            allow_write_tools=False,
            run_mode="unattended",
        )

        self.assertIn("# 固定运维核心", assembly.text)
        self.assertIn("# 固定安全边界", assembly.text)
        self.assertIn("本轮是只读模式", assembly.text)
        self.assertIn('<tool name="investigate"', assembly.text)
        self.assertIn('<tool name="read_skill"', assembly.text)
        self.assertNotIn('<tool name="restart_service"', assembly.text)
        self.assertIn("<available_skills>", assembly.text)
        self.assertIn("<name>systemd-operations</name>", assembly.text)
        self.assertIn("先调用 `read_skill`", assembly.text)
        self.assertNotIn("# systemd 服务排障", assembly.text)
        self.assertNotIn("<active_skills>", assembly.text)

    def test_prompt_is_reassembled_from_current_skill_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            _write_skill(root, "custom-check", name="custom-check")
            with patch.object(settings, "skills_dir", root):
                first = build_ops_system_prompt(
                    tools=chat_graph._ops_tools(False),
                    allow_write_tools=False,
                    run_mode="interactive",
                )
                _write_skill(root, "second-check", name="second-check")
                second = build_ops_system_prompt(
                    tools=chat_graph._ops_tools(False),
                    allow_write_tools=False,
                    run_mode="interactive",
                )

        self.assertEqual(
            [skill.name for skill in first.installed_skills], ["custom-check"]
        )
        self.assertEqual(
            [skill.name for skill in second.installed_skills],
            ["custom-check", "second-check"],
        )
        self.assertNotIn("second-check", first.text)
        self.assertIn("second-check", second.text)
        self.assertNotIn("# 测试说明", second.text)


class ReadSkillToolTest(unittest.IsolatedAsyncioTestCase):
    async def test_reads_full_content_by_registered_name(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            _write_skill(root, "custom-check", name="custom-check")
            with patch.object(settings, "skills_dir", root):
                result = await read_skill.ainvoke({"name": "custom-check"})

        self.assertIn('<skill name="custom-check"', result)
        self.assertIn("# 测试说明", result)
        self.assertIn("按证据顺序完成调查", result)


class SkillApiTest(unittest.IsolatedAsyncioTestCase):
    async def test_lists_only_name_description_and_content(self) -> None:
        response = await list_skills()

        self.assertGreaterEqual(len(response.skills), 4)
        docker = next(
            skill for skill in response.skills if skill.name == "docker-operations"
        )
        self.assertIn("Docker", docker.description)
        self.assertIn("# Docker 容器排障", docker.instructions)
        self.assertEqual(
            set(docker.model_dump()), {"name", "description", "instructions"}
        )
