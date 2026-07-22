# AIOMS Frontend

AIOMS 的 Vue 3 前端，用于多 Agent 运维对话、事件处理和服务器管理。

当前界面采用单页签状态切换，尚未引入 Vue Router 或 Pinia；跨后端交互统一封装在 `src/api/`。

## 当前页面

### 对话

- 新建、切换和删除历史会话
- SSE 实时显示工具过程，并流式展示一次生成的最终回答
- 工具调用前的 Agent 旁白会实时显示，刷新历史后保持相同顺序
- 展示指标、日志、告警等工具调用状态；服务器指标查询合并为一张工具卡片
- SSH 只读诊断显示普通工具状态；服务重启等高风险写操作显示人工审批卡片和风险标识
- 批准或拒绝后通过 `/chat/resume` 恢复同一条 LangGraph 线程

专业 Agent 的内部报告不会直接显示，用户看到的是 Supervisor 汇总后的最终回答。

### 事件中心

- 展示关联归组后的运维事件
- 按待处理、已认领、已解决筛选
- 查看事件摘要和 AI 自动诊断详情
- 填写认领人并认领事件
- 人工标记事件为已解决

### 服务器

- 查看和新增纳管服务器
- 配置 SSH 用户、端口和私钥文件名
- 测试 SSH 连通性
- 删除服务器记录

## 技术栈

- Vue 3.5
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

因此前端代码不需要硬编码后端域名，也不会在本地开发时遇到跨域问题。生产构建后需要由反向代理提供等价的 `/api` 转发规则。

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
├── api/          # chat、conversations、incidents、servers API 客户端
├── components/   # 导航和会话列表等复用组件
├── types/        # 与后端 schema 对应的 TypeScript 类型
├── views/
│   ├── ChatView.vue
│   ├── IncidentsView.vue
│   └── ServersView.vue
├── App.vue       # 当前页签状态和页面切换
├── main.ts       # Vue 入口
└── style.css     # 全局浅色设计变量和基础样式
```

## 数据流说明

### 普通对话

```text
ChatView → POST /api/chat → SSE 工具事件实时展示 → 一次生成并流式展示最终回答 → done
```

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
3. 在“对话”询问当前指标、日志和告警，观察工具卡片与最终汇总。
4. 请求在测试服务器执行 `uptime`，确认它作为只读诊断直接执行，不出现审批卡片。
5. 请求重启一个服务，确认先出现高风险审批卡片；拒绝后不执行，批准后仍需通过后端服务白名单。
6. 刷新会话，确认消息、工具轨迹和审批结果可以从历史记录恢复。

## 当前限制

- 没有登录、用户身份和前端 RBAC。
- 页面切换不是 URL 路由，刷新后会回到“对话”页。
- 事件认领人暂时由用户自由输入，后续应由登录身份提供。
- 生产部署需要自行配置静态文件托管和 `/api` 反向代理。
