# AIOMS Skills

每个 Skill 放在独立目录的 `SKILL.md` 中。后端会在每轮 Agent 调用和 `GET /skills` 请求时重新扫描该目录，因此新增或修改 Skill 不需要再维护一份 Python 注册表。

```text
backend/skills/
└── docker-operations/
    └── SKILL.md
```

## 文件格式

```markdown
---
name: docker-operations
description: 说明这个 Skill 解决什么运维问题。
---

# 给 Agent 的完整方法说明
```

- `name` 必须使用小写字母、数字和单个连字符，最长 64 个字符，并建议与目录名一致。
- `description` 既是 SKILLS 页摘要，也是模型在运行时判断是否需要读取正文的依据，应写清适用任务。
- 正文是一份教程或方法说明，可以引用同目录下的补充资料，但不会注册工具、设置触发词或授予权限。
- YAML、正文、重名或越界符号链接校验失败时，Skill 不会加载，并在后端诊断中记录原因。

## 运行时边界

后端只把全部 Skill 的名称、描述和路径索引拼进 system prompt。模型判断任务明显匹配时调用 `read_skill(name)` 读取正文；用户也可以用 `/skill:docker-operations` 强制展开全文。

这种自动匹配是模型的语义选择，不是确定性工作流。固定运维核心、安全边界和必须执行的审批流程不会放进普通 Skill，也不会被 Skill 覆盖；需要强制保证的流程仍由 LangGraph、Operation Broker 和策略层执行。
