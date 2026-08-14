# 参与贡献

感谢你愿意帮助改进 AIOps。仓库同时保留工程决策与演进记录；贡献应保持改动小、边界清楚，并让后来者能理解为什么这样设计。

## 开始之前

1. 先阅读根目录 [`README.md`](README.md) 了解项目阶段和安全边界。
2. 后端改动阅读 [`backend/README.md`](backend/README.md)，目标服务器权限改动还要阅读 [`scripts/target-server/README.md`](scripts/target-server/README.md)。
3. 较大的功能或架构调整请先开 Issue 说明问题、范围和验收方法，避免双方做出不兼容实现。
4. 安全漏洞不要公开提交，请按 [`SECURITY.md`](SECURITY.md) 私密报告。

## 开发约定

- Python 使用 3.12+、完整类型注解、Pydantic v2 和唯一的 SQLAlchemy `Base`。
- FastAPI 路由不直接访问 ORM；查询写入放在 `repositories/`，业务编排放在 `services/`。
- Vue 使用 Composition API 与 `<script setup>`；API 请求统一放在 `frontend/src/api/`。
- 不提交 `.env`、密钥、真实服务器地址、内部服务名、生成规则或本地抓取目标。
- 新增远端写能力必须使用结构化参数、Operation Broker、人工审批和目标机固定网关；不接受任意宿主机写 Shell。
- 提交标题建议使用 Conventional Commits：`feat(scope): ...`、`fix(scope): ...`、`docs(scope): ...`。

更完整的代码约定见 [`AGENTS.md`](AGENTS.md)。

## 提交前检查

后端：

```bash
cd backend
uv run python -m unittest discover -s tests -v
uv run python -m compileall -q app tests
uv run ruff format --check app tests
uv run ruff check app tests
```

前端：

```bash
cd frontend
npm run build
```

涉及 Shell 脚本、Docker 配置或真实运行链路时，还应说明你完成了哪些一次性静态检查、哪些运行时验证仍需要维护者执行。不要把“代码存在”写成“生产环境已验证”。

## Pull Request

- 一个 PR 聚焦一个问题，说明动机、关键改动、验证命令和剩余风险。
- UI 改动请附不含真实资产信息的截图；运维链路改动不要附带真实 IP、主机名、日志或凭据。
- 不要顺手重构无关代码，也不要用格式化覆盖其他人的在途改动。

## 贡献许可

提交贡献即表示你同意将该贡献按仓库的 [MIT License](LICENSE) 发布。
