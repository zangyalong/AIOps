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
version: 1.0.0
category: 容器运行时
triggers:
  - docker
  - 容器
tools:
  - investigate
disable-model-invocation: false
---

# 给 Agent 的完整方法说明
```

- `name` 必须使用小写字母、数字和单个连字符，最长 64 个字符，并建议与目录名一致。
- `description` 是设置页和运行时轻量目录中的摘要；`triggers` 用于从最近用户请求与本轮调查结果中确定性选择 Skill。
- `tools` 只声明该 Skill 会指导使用哪些现有工具，方便阅读和检查；它不会注册工具或授予权限。
- `disable-model-invocation: true` 会保留 Skill 的安装信息，但阻止它进入模型上下文。
- YAML、正文、重名或越界符号链接校验失败时，Skill 不会加载，诊断会显示在设置页。

## 运行时边界

固定运维核心与安全边界始终先于 Skill 注入。每轮最多激活三个匹配 Skill；其他已启用 Skill 只以名称、摘要和位置出现在轻量目录中。Skill 不能新增任意 Shell、绕过审批、扩大白名单或改变无人值守任务的只读模式。
