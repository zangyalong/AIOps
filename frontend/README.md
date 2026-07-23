# AIOMS Frontend

AIOMS 的 Vue 3 前端，用于多 Agent 运维对话、事件处理、变更记录、服务拓扑和服务器管理。

当前界面使用 Vue Router 管理真实 URL，尚未引入 Pinia；跨后端交互统一封装在 `src/api/`。

## 当前页面

### 对话

- 新建、切换和删除历史会话
- 新对话使用 `/chat`，历史对话使用 `/chat/:conversationId`；刷新、前进/后退和复制链接可以恢复同一会话
- SSE 实时显示工具过程，并流式展示一次生成的最终回答
- 切换内部页面或关闭浏览器不会取消后端 Agent；返回生成中的会话会自动重新订阅并重放进度
- AI 回复使用 markdown-it 渲染 Markdown，并由 DOMPurify 清理 HTML；长链接、代码块和表格不会撑出消息区域
- 工具调用前的 Agent 旁白会实时显示，刷新历史后保持相同顺序
- 展示指标、日志、告警等工具调用状态；服务器指标查询合并为一张工具卡片
- SSH 只读诊断显示普通工具状态；服务重启等高风险写操作显示人工审批卡片和风险标识
- 批准或拒绝后通过 `/chat/resume` 恢复同一条 LangGraph 线程

专业 Agent 的内部报告不会直接显示，用户看到的是 Supervisor 汇总后的最终回答。

### 事件中心

- 展示关联归组后的运维事件
- 按待处理、已认领、已解决筛选
- 查看事件摘要和 AI 自动诊断详情；诊断正文使用与对话一致的安全 Markdown 渲染
- 查看事件发生前后各 30 分钟的同服务变更时间线
- 填写认领人并认领事件
- 人工标记事件为已解决

### 变更记录

- 新增、编辑和删除发布、回滚、扩缩容、配置变更记录
- 记录服务、环境、版本 / commit、发生时间和操作说明
- 按服务和环境在页面内筛选；记录会作为事件诊断的只读事实证据

### 服务目录

- 新增、编辑和删除逻辑服务，维护负责人、环境、仓库和 Grafana 仪表盘
- 将服务部署实例绑定到已有服务器，并分别配置 Prometheus `instance` 与 Loki `service_name`
- 维护“调用方 → 被调用方”的有向依赖关系和依赖类型
- 点击服务查看三层关联拓扑，点击图中节点可切换服务详情
- 事件成功关联规范化服务后，可以从事件时间线直接跳到对应服务拓扑

### 服务器

- 查看和新增纳管服务器
- 配置 SSH 用户、端口和私钥文件名
- 测试 SSH 连通性
- 删除服务器记录

## 技术栈

- Vue 3.5
- Vue Router 4
- markdown-it + DOMPurify
- TypeScript 6
- Vite 8
- Composition API + `<script setup>`
- 原生 `fetch` 和 `ReadableStream` 处理 API/SSE

## 环境要求

- Node.js `^20.19.0` 或 `>=22.12.0`
- npm
- AIOMS 后端运行在 `http://127.0.0.1:8010`

## 安装与启动

```bash
cd frontend
npm install
npm run dev
```

Vite 固定使用 5174 端口：<http://127.0.0.1:5174>。

如果该端口已占用，Vite 会直接退出而不是自动换端口；需要先处理占用，或临时设置：

```bash
PORT=5175 npm run dev
```

## 后端代理

浏览器里的所有后端请求都写成 `/api/...`。开发服务器按照 `vite.config.ts` 转发：

```text
浏览器 /api/incidents
    ↓ 去掉 /api 前缀
FastAPI http://127.0.0.1:8010/incidents
```

因此前端代码不需要硬编码后端域名，也不会在本地开发时遇到跨域问题。生产构建后需要由反向代理
提供等价的 `/api` 转发规则，并把 `/chat/*`、`/incidents`、`/changes`、`/services/*`、`/servers` 等前端路由回退到
`index.html`，否则直接刷新历史对话 URL 会得到 404。

## 常用命令

```bash
# 开发服务器
npm run dev

# TypeScript 类型检查 + 生产构建
npm run build

# 本地预览 dist 构建结果
npm run preview
```

`npm run build` 会先执行 `vue-tsc -b`，类型错误会直接让构建失败。

## 目录说明

```text
src/
├── api/          # chat、conversations、incidents、changes、services、servers API 客户端
├── components/   # 导航和会话列表等复用组件
│   ├── MarkdownContent.vue # AI Markdown 安全渲染与防横向溢出样式
│   └── ServiceTopology.vue # 服务依赖 SVG 拓扑图
├── router/       # /chat、/chat/:conversationId、/incidents、/changes、/services、/servers
├── types/        # 与后端 schema 对应的 TypeScript 类型
├── views/
│   ├── ChatView.vue
│   ├── ChangesView.vue
│   ├── IncidentsView.vue
│   ├── ServicesView.vue
│   └── ServersView.vue
├── App.vue       # 应用布局和 RouterView 出口
├── main.ts       # Vue 入口
└── style.css     # 全局浅色设计变量和基础样式
```

## 数据流说明

### 普通对话

```text
ChatView → POST /api/chat 创建 Chat Run
         → GET /api/chat/runs/{run_id}/events 订阅/重连
         → SSE 工具事件实时展示 → 一次生成并流式展示最终回答 → done
```

Chat Run 在后端独立执行，浏览器的 SSE 只是订阅者。对话页卸载时会主动关闭当前订阅以节省连接，
但后台任务继续；再次打开 `/chat/:conversationId` 时先加载数据库历史，再查询活跃运行并重放未完成轨迹。

### 写操作审批

```text
Agent 提议重启白名单内的 systemd 服务
    ↓
approval_required（带 thread_id）
    ↓
用户批准或拒绝
    ↓
POST /api/chat/resume
    ↓
恢复原 LangGraph 执行并继续 SSE
```

在审批完成前，输入框会被禁用，防止用户开启新一轮后遗留未恢复的挂起线程。

## 运行时验收

后端和前端均已运行时，可以按下面顺序检查：

1. 打开“服务器”，确认列表能够加载并测试一台已配置服务器。
2. 打开“事件中心”，确认事件筛选、诊断详情、认领和解决操作。
3. 打开“变更记录”新增一条与事件同服务、时间相近的发布记录，再回到事件详情确认时间线。
4. 打开“服务目录”创建两个服务，将其中一个绑定服务器并添加另一项为依赖，确认列表和箭头方向一致。
5. 在“对话”询问某服务依赖、故障影响范围和根因候选，确认事件专家调用对应只读工具。
6. 请求在测试服务器执行 `uptime`，确认它作为只读诊断直接执行，不出现审批卡片。
7. 请求重启一个服务，确认先出现高风险审批卡片；拒绝后不执行，批准后仍需通过后端服务白名单。
8. 刷新会话，确认消息、工具轨迹和审批结果可以从历史记录恢复。
9. AI 生成途中切到“事件中心”或“服务器”，再点击“对话”，确认 URL 回到原会话并继续显示进度。
10. AI 生成途中关闭页面，再用同一 `/chat/:conversationId` 地址打开，确认任务继续并最终入库。

## 当前限制

- 没有登录、用户身份和前端 RBAC。
- 事件认领人暂时由用户自由输入，后续应由登录身份提供。
- 运行中的事件重放依赖当前 FastAPI 进程；后端重启后无法恢复尚未完成的 Chat Run。
- 生产部署需要自行配置静态文件托管和 `/api` 反向代理。
