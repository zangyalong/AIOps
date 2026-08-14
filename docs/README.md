# AIOps 文档索引

根目录 [`README.md`](../README.md) 是项目总入口。本目录保存实现之外仍值得长期维护的架构说明、设计取舍、调研记录和验收材料；运行命令与配置细节优先放在对应子项目 README，避免同一事实维护多份。

## 使用与部署

- [后端运行、配置、API 与安全边界](../backend/README.md)
- [前端页面、设计约定与运行时验收](../frontend/README.md)
- [目标服务器安装、权限与固定网关](../scripts/target-server/README.md)
- [本地 SSH 测试沙箱](local-ssh-sandbox.md)

## 架构与设计

- [Agent 操作执行架构](operation-execution-architecture.md)
- [Agent Memory 设计](agent-memory-design.md)
- [Agent 工具入参 Schema 设计](agent-tool-schema.md)

## 调研与阶段记录

- [AIOps 功能源码调研](aiops-feature-survey.md)
- [竞品对比与差距分析](competitive-comparison.md)
- [开发计划与人工验收状态](dev-plan.md)

`dev-plan.md` 同时包含历史计划和当前待验收项。判断项目现状时，以根 README、当前代码和文件内最新说明为准，不要把早期背景当成当前能力。

## 质量验收

- [SKILLS 页面设计验收](qa/skills-page.md)
