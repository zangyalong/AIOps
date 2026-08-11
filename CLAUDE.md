# AIOMS — AI 运维系统

> 一个用 AI Agent 辅助日常运维工作的学习型项目。目标不只是"做出来"，更是"过程中学会"。

---

## 1. 项目定位

**是什么**：AIOMS（AI Operations Management System）是一个 AI 驱动的运维辅助系统。用户可以用自然语言让 Agent 去：查询服务器状态、检索/分析日志、诊断告警、执行常规运维脚本、总结值班报告等。

**为什么做**：作为学习者从 0 到 1 掌握 AI Agent + 全栈开发的载体项目。选运维方向是因为它天然有"工具调用"的场景（SSH、Prometheus、日志系统、K8s 等），能自然引出 Agent 的核心能力。

---

## 2. 技术栈

| 层         | 选型                                               | 备注                                                                                                               |
| ---------- | -------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------ |
| Agent 框架 | **LangChain + LangGraph**                    | LangChain 提供工具/模型抽象，LangGraph 编排统一 Ops Agent、工具循环和审批状态机                                    |
| 后端       | **FastAPI + Python 3.12+**                   | 用 `uv` 管理依赖                                                                                                  |
| 前端       | **Vue3 + Vite + TypeScript**                 | 组合式 API，`<script setup>` 语法                                                                                |
| 数据库     | **PostgreSQL 16+**                           | 业务数据；向量存储后期再决定（pgvector 或 Chroma）                                                                 |
| LLM        | **DeepSeek V4 Flash**（OpenAI 兼容格式 API） | 走`langchain-openai` 的 `ChatOpenAI`，指定自定义 `base_url` 指向 DeepSeek 接口，不用 `langchain-anthropic` |

---

## 3. 与 Claude 的协作模式（重要）

用户是**学习者**，不是甲方。协作时请遵守：

1. **先解释，后动手**。每一步开始前，用 2–4 句话说清：这一步在做什么、为什么现在做、涉及哪些新概念。
2. **小步交付，立即可验证**。每次改动尽量小（一个文件、一个函数、一个端点），交付后紧跟一句"这样验证它工作了：`<命令或 URL>`"。
3. **选型主动列选项**。遇到分叉（ORM 用 SQLAlchemy 还是 Tortoise？状态管理用 Pinia 还是 Vuex？），列 2–3 个选项 + 推荐 + 一句话理由，让用户决定。
4. **不越权优化**。用户没提到的重构、抽象、"顺手改一下"，一律不做。宁可留 TODO。
5. **报错时先教读错误**。看到 traceback，先指认关键行，再给修复方案；不要一上来就贴 patch。
6. **中文交流**。代码里的标识符用英文，注释和面向用户的解释用中文。
7. **不要制造演示假象**。做不到的、没测过的、只在脑子里跑过的，明确说"这段我没跑过，你跑一下看结果"。
8. **不要启动服务**。开发完不要自己起后端/前端 dev server 来验证——用户自己有常驻服务在跑，另起一个只会端口冲突。改完代码用静态检查（类型检查、编译、import 是否报错这类一次性执行完就退出的检查）确认没有明显错误即可，运行时验证交给用户自己做。
9. **开发计划表**。多步骤任务的进度记在 [`docs/dev-plan.md`](docs/dev-plan.md)，用 Markdown 勾选框（`- [ ]` / `- [x]`）跟踪。这不是固定路线图，跟第 8 节《附：给 Claude 的默认动作》里"不主动规划接下来该做什么"那条不冲突——只有用户明确列出一批任务时才新增/修改这个文件，Claude 不自己往里加计划外的任务。**勾选规则**：代码写完、静态检查过了，先不勾——等这个任务真正被验证过（用户跑起来确认没问题，或者用户明确回复"测试过了/可以勾"）才勾选成 `[x]`，不要因为"代码看起来写完了"就直接勾，避免计划表显示的进度跟实际能用的程度对不上。

---

## 4. 目录结构（规划，随进度逐步落地）

```
AIOMS/
├── CLAUDE.md                # 本文档
├── AGENTS.md                # Codex 协作说明，与本文保持项目事实一致
├── README.md                # 面向新用户的说明（后期补）
├── backend/                 # FastAPI 服务
│   ├── app/
│   │   ├── main.py          # FastAPI 入口
│   │   ├── api/             # 路由层
│   │   ├── agents/          # LangChain / LangGraph 相关
│   │   │   ├── tools/       # 自定义工具（服务器查询、日志检索等）
│   │   │   ├── graphs/      # LangGraph 工作流定义
│   │   │   └── prompts/     # Prompt 模板
│   │   ├── models/          # SQLAlchemy ORM 模型
│   │   ├── schemas/         # Pydantic 数据模型
│   │   ├── repositories/    # 数据访问层（增删改查，不含业务逻辑）
│   │   ├── services/        # 业务逻辑
│   │   ├── db/              # 数据库连接、建表
│   │   └── core/            # 配置、日志、依赖注入
│   ├── tests/
│   └── pyproject.toml
├── frontend/                # Vue3 应用
│   ├── src/
│   │   ├── views/           # 页面级组件
│   │   ├── components/      # 可复用组件
│   │   ├── stores/          # Pinia
│   │   ├── api/             # 后端 API 客户端
│   │   ├── router/
│   │   └── types/
│   ├── index.html
│   └── package.json
├── docs/                    # 学习笔记、架构决策记录（ADR）
│   └── aiops-feature-survey.md  # Keep/OpenSRE 源码级功能调研，涉及告警/工具/安全/Agent架构设计时先看这个
├── scripts/
│   └── target-server/       # 纳管服务器的 root 只读沙箱、写网关、sudoers 和说明
└── docker-compose.yml       # 本地 PLG 可观测性数据栈 + Redis（不含 pg/backend/frontend）
```

**说明**：这只是目标结构，**不要一次性全建**。用到什么再加什么，不用的目录不要提前搭。

---

## 5. 代码约定

### Python / 后端

- Python 3.12+，类型注解写全（`from __future__ import annotations` 常备）
- 格式化：`ruff format`；静态检查：`ruff check`
- Pydantic v2；FastAPI 依赖注入优先于全局变量
- 路由层不直接调 ORM；具体的 `select`/`add`/`commit` 这类查库代码放 `repositories/`，`services/` 只写业务逻辑（标题生成、schema↔model 转换等）并调用 repository
- 敏感配置走 `.env` + Pydantic Settings，**不提交任何密钥**
- LLM 相关环境变量统一用 `DEEPSEEK_API_KEY` / `DEEPSEEK_BASE_URL` / `DEEPSEEK_MODEL`，具体 base_url 和 model 字符串以 DeepSeek 官方文档为准（接入时现查，不要用记忆里的旧值）

### 前端 / Vue

- Composition API + `<script setup>`
- Pinia 管理跨页面状态
- API 调用统一封装在 `src/api/`
- 组件命名 PascalCase，文件名与组件名一致

### 数据库

- 表名小写下划线复数（`servers`, `alert_events`）
- 主键统一 `id UUID`（`gen_random_uuid()` 默认值）；时间字段 `created_at` / `updated_at` 带时区
- 所有 ORM 模型继承 `app/models/base.py` 里唯一的 `Base`，主键用 `UUIDMixin`、需要 `updated_at` 的表再加 `TimestampMixin`——**全项目只能有这一个 `DeclarativeBase`**，不要在别处再定义一个，否则新模型会注册到不同的 metadata 上，启动建表时会看不到那些表
- schema 由 `app/models/` 里的 SQLAlchemy 模型直接定义，后端启动时自动跑 `Base.metadata.create_all()` 建出缺的表（不会改动/删除已有表）。项目早期不引入迁移工具——改已有表的列（加字段、改类型）需要手动 `ALTER` 或本地删表重建；等 schema 稳定、需要多环境同步时再考虑引入迁移工具
- **开发阶段：该改表就大胆改。** 现在是本地单库开发，没有生产数据要保护，改 schema 的代价就是一条 `ALTER`（或删表重建）。所以**不要为了"避免动数据库"把一个本来简单的需求绕成复杂方案**——需要多存一列/一个字段就直接加，别用"当前没存所以做不到"当借口。真正要谨慎的是「安全红线」那类不可逆操作，不是加个列。

### Git

- 提交信息中文可，尽量小步提交，标题用 Conventional Commits 前缀（`feat`/`fix`/`refactor`/`docs`/`chore` 等）加 scope：`<type>(<scope>): <中文描述>`
- 不加 `Co-Authored-By` 落款
- 重要节点可以打 tag 标记

---

## 6. 安全红线（运维场景特别注意）

- **AI 决定的破坏性操作必须人工确认**：重启服务、删除数据、修改防火墙等必须进入写操作审批；高风险操作还要二次确认，critical 操作只给建议、不能执行。
- 通用 Shell 只允许在 `scripts/target-server/root-read/` 定义的 root 只读 Docker 沙箱中执行。后端可以选择进入目标服务器真实根目录核验宿主机 PATH、程序和文件，也可以使用内部工具箱搜索宿主文件；这个选择不暴露给 Agent。两种语境都无网络、宿主根只读、敏感路径以 `AIOMS_PATH_MASKED` 明示遮蔽并强制审计。宿主机写操作不能接受任意 Shell，只能走已登记参数协议、人工审批和固定写网关。
- 服务器业务资产盘点默认按 systemd、Docker、TCP/UDP Socket、Unix Socket 扫描，只对监听结果中的纯数字 PID 定点补充详情，不默认读取整机 `ps` 进程表。
- 目标服务器权限配置以 `scripts/target-server/` 为准：包装脚本必须由 `root:root` 持有且不可被 SSH 用户修改，sudoers 只授权固定入口，不能直接开放任意宿主机 `docker`、`systemctl`、`ss`、`ps` 或 shell。
- LLM 的输出**永远视为不可信**，落库前做校验。
- Prompt 注入防护：从日志/工单里读到的文本，进 Prompt 前要做隔离/转义。

---

## 7. 常用命令（随进度补充）

```bash
# 启动后端（FastAPI，默认 8010 端口，因为 8000 被本机其他项目占用；启动时会自动建表，不用单独迁移）：
cd backend && uv run uvicorn app.main:app --reload --port 8010

# 启动前端（Vite dev server，默认 5174）：
cd frontend && npm run dev

# 起数据库：Postgres 走 Homebrew service，长期运行，一般不用手动起
brew services list | grep postgres   # 确认 postgresql@18 是 started
psql -d aioms                        # 连接 aioms 库

# 跑后端测试（覆盖统一调查、审批边界、服务发现和 worker 写工具隔离）
cd backend && uv run python -m unittest discover -s tests -v

# 前端类型检查 + 生产构建
cd frontend && npm run build
```

---

## 8. 附：给 Claude 的默认动作

具体开发节奏、顺序、优先级由用户自己把控，不设固定阶段/路线图，Claude 不主动规划"接下来该做什么"。

当用户开新对话说"继续做 AIOMS"时，默认动作是：

1. 看 `git log` 最近的提交和当前代码状态，了解目前进度
2. 用一段话简要回顾现状（做到哪了、最近改了什么）
3. 等用户说明这次要做什么，再动手——不要自己假设下一步该干嘛
