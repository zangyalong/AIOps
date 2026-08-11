<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  fetchActiveChatRun,
  resumeApproval,
  startChatMessage,
  streamChatRun,
  type ChatStreamEvent,
  type OperationApprovalDecision,
} from '../api/chat'
import { fetchConversationDetail } from '../api/conversations'
import ConversationList from '../components/ConversationList.vue'
import MarkdownContent from '../components/MarkdownContent.vue'
import type { ChatMessage, ChatRunHistory } from '../types/chat'

// 会话时间线：消息、工具行、写操作审批行按发生顺序混在一个数组里，这样它们能显示在实际
// 发生的那个位置（夹在文本流中间）。工具行和审批行都会随历史重建：工具行来自 assistant 的
// tool_calls；审批行来自写工具的 tool_call + 它配对的返回（据此还原当初是执行了还是被拒）。
type TimelineItem =
  | { kind: 'message'; key: string; role: 'user' | 'assistant'; content: string }
  | { kind: 'narration'; key: string; content: string }
  | { kind: 'tool'; key: string; id: string; tool: string; done: boolean; failed?: boolean }
  | { kind: 'run-error'; key: string; content: string }
  | {
      // 写操作审批行：pending 时右侧带执行/不执行按钮；裁决后翻成 approved（已批准）/rejected（已拒绝）
      kind: 'approval'
      key: string
      threadId: string // 挂起恢复用；历史重建的已决定项为 '' （无需也无法再恢复）
      toolCallId: string
      operationId: string
      command: string // 展示的命令 / 动作摘要
      label: string // 副标题（在哪台执行等），可空
      riskLevel?: 'medium' | 'high' | 'critical'
      status: 'pending' | 'selected_approved' | 'selected_rejected' | 'approved' | 'rejected' | 'failed'
    }
type ApprovalItem = Extract<TimelineItem, { kind: 'approval' }>

const timeline = ref<TimelineItem[]>([])
const input = ref('')
const sending = ref(false)
const error = ref<string | null>(null)
// 同一个 conversationId 下的多轮对话，每次发消息后端都从 messages 表把历史加载出来；
// 换一个 conversationId（或不传）就是开新对话，互相不共享上下文。
const conversationId = ref<string | null>(null)
const route = useRoute()
const router = useRouter()
const conversationListRef = ref<InstanceType<typeof ConversationList> | null>(null)
const messagesEl = ref<HTMLElement | null>(null)
// 当前是否有一个写操作挂在审批门上等裁决。非空时输入框禁用（先处理确认再继续对话），
// 记住要恢复哪条挂起（threadId）——裁决时按 threadId 把这次挂起的所有待批行一起翻状态。
const pendingApproval = ref<{
  runId: string
  threadId: string
  decisions: Record<string, OperationApprovalDecision>
} | null>(null)
// 高风险操作第一次点击“执行”时只打开详情弹窗；弹窗确认后才真正恢复后端运行。
const confirmationApproval = ref<ApprovalItem | null>(null)
const activeRunId = ref<string | null>(null)
const lastEventSequence = ref(0)
let subscriptionController: AbortController | null = null
let loadVersion = 0

let uidCounter = 0
function uid(): string {
  return `i${uidCounter++}`
}

const TOOL_LABELS: Record<string, string> = {
  investigate: '调查运维证据',
  // 以下旧名称只用于兼容已经落库的历史对话，新 Agent 不再直接调用。
  query_servers: '查询服务器列表',
  query_server_metrics: '查询服务器指标',
  query_metrics: '查询 Prometheus 指标',
  list_metric_instances: '查询监控实例',
  search_logs: '检索日志',
  query_alerts: '查询告警',
  query_server_services: '查询服务器上的服务',
  discover_server_services: '发现服务器运行服务',
  register_server_services: '注册服务目录',
  run_diagnostic_command: '执行只读诊断',
  run_root_read_command: '执行宿主文件只读调查',
  find_server_files: '查找服务器文件',
  read_server_file: '读取服务器文件',
  preview_server_file_update: '预览服务器文件变更',
  preview_server_config_patch: '预览结构化配置补丁',
  update_server_file: '更新服务器文件',
  patch_server_config: '修改服务器配置项',
  start_service: '启动服务',
  stop_service: '停止服务',
  restart_service: '重启服务',
  create_alert_rule: '创建预警规则',
  apply_server_log_config: '应用服务器日志配置',
  deploy_server_environment: '自动部署服务器环境',
  run_whitelisted_command: '执行远程命令（旧记录）',
}
function toolLabel(name: string): string {
  return TOOL_LABELS[name] ?? name
}

// 写工具都必须还原为审批行；旧名称仅用于正确显示已有历史，不会再绑定给 Agent。
const WRITE_TOOLS = new Set([
  'register_server_services',
  'create_alert_rule',
  'start_service',
  'stop_service',
  'restart_service',
  'apply_server_log_config',
  'deploy_server_environment',
  'update_server_file',
  'patch_server_config',
  'run_whitelisted_command',
])

const MEDIUM_RISK_TOOLS = new Set([
  'register_server_services',
  'create_alert_rule',
  'start_service',
  'patch_server_config',
])
const HIGH_RISK_TOOLS = new Set([
  'stop_service',
  'restart_service',
  'apply_server_log_config',
  'deploy_server_environment',
  'update_server_file',
])

function historyApprovalCommand(name: string, args: Record<string, unknown>): string {
  if (name === 'start_service' || name === 'stop_service' || name === 'restart_service') {
    const action = { start_service: '启动', stop_service: '停止', restart_service: '重启' }[name]
    return `${action} ${String(args.runtime_type ?? 'systemd')} 服务 ${String(args.service_name ?? '?')}`
  }
  if (name === 'create_alert_rule') {
    return `创建预警规则 ${String(args.name ?? '?')}：${String(args.expression ?? '?')}`
  }
  if (name === 'register_server_services') {
    const services = Array.isArray(args.services) ? args.services : []
    const names = services
      .map((item) =>
        item && typeof item === 'object' && 'service_name' in item
          ? String(item.service_name)
          : '?',
      )
      .join('、')
    return `注册到 ${String(args.environment ?? 'prod')} 环境：${names || '（无服务明细）'}`
  }
  if (name === 'deploy_server_environment') {
    return `自动部署 ${String(args.server_name ?? '?')} 的环境配置`
  }
  if (name === 'update_server_file') {
    return String(args.preview_diff ?? `更新 ${String(args.path ?? '?')}`)
  }
  if (name === 'patch_server_config') {
    return String(
      args.preview_diff ??
        `修改 ${String(args.path ?? '?')} 的 ${String(args.selector ?? '?')}`,
    )
  }
  return String(args.command ?? name)
}

function historyRiskLevel(name: string): 'medium' | 'high' | undefined {
  if (MEDIUM_RISK_TOOLS.has(name)) return 'medium'
  if (HIGH_RISK_TOOLS.has(name)) return 'high'
  return undefined
}

function riskLevelLabel(level: 'medium' | 'high' | 'critical'): string {
  if (level === 'medium') return '中风险'
  if (level === 'critical') return '严重风险'
  return '高风险'
}

function requiresSecondConfirmation(item: ApprovalItem): boolean {
  return item.riskLevel === 'high' || item.riskLevel === 'critical'
}

function requestApproval(item: ApprovalItem) {
  if (sending.value || item.status !== 'pending') return
  if (requiresSecondConfirmation(item)) {
    confirmationApproval.value = item
    return
  }
  void selectApproval(item, true, false)
}

function closeConfirmation() {
  if (!sending.value) confirmationApproval.value = null
}

async function confirmHighRiskApproval() {
  if (confirmationApproval.value) {
    await selectApproval(confirmationApproval.value, true, true)
  }
  if (!error.value) confirmationApproval.value = null
}

function scrollToBottom() {
  const el = messagesEl.value
  if (el) el.scrollTop = el.scrollHeight
}

// 时间线变化（新消息、流式 token、工具卡片）时自动滚到底部；但只在用户本来就贴着底部
// 时才滚，避免用户向上翻看历史时被强行拽回来。默认 flush:'pre'，回调里读到的是更新前的
// 滚动位置，正好用来判断"这次更新前用户在不在底部"，判断后再等 DOM 更新完滚下去。
watch(
  timeline,
  () => {
    const el = messagesEl.value
    if (!el) return
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 120
    if (nearBottom) nextTick(scrollToBottom)
  },
  { deep: true },
)

// 当前是否有一个"打开的"助手气泡可以继续追加 token。最终回答只执行一次，输出 token 持续
// 追加到同一个气泡；工具卡片仍与最终回答分开显示。
let assistantOpen = false

function appendToken(content: string) {
  if (!assistantOpen) {
    timeline.value.push({ kind: 'message', key: uid(), role: 'assistant', content: '' })
    assistantOpen = true
  }
  const last = timeline.value[timeline.value.length - 1]
  if (last.kind === 'message') last.content += content
}

// 最终回答开始输出前显示等待状态。工具正在执行时已有动态工具行，不重复显示。
const showThinking = computed(() => {
  if (!sending.value || pendingApproval.value) return false
  const last = timeline.value[timeline.value.length - 1]
  if (!last) return true
  if (last.kind === 'message' && last.role === 'assistant') return false
  return last.kind !== 'tool' || last.done
})

// 消费 Chat Run 的可重放事件。切页后重新订阅会从 seq=0 重建当前运行轨迹；审批后则从已消费的
// seq 继续，避免把前半段重复画一遍。
async function consume(stream: AsyncGenerator<ChatStreamEvent>) {
  for await (const event of stream) {
    lastEventSequence.value = Math.max(lastEventSequence.value, event.seq)
    if (event.type === 'token') {
      appendToken(event.content)
    } else if (event.type === 'narration') {
      assistantOpen = false
      timeline.value.push({ kind: 'narration', key: uid(), content: event.content })
    } else if (event.type === 'tool_start') {
      // 工具过程与最终回答分块展示；正常情况下最终回答尚未出现，这里关闭气泡是防御性处理。
      assistantOpen = false
      timeline.value.push({ kind: 'tool', key: uid(), id: event.id, tool: event.tool, done: false })
    } else if (event.type === 'tool_end') {
      const item = timeline.value.find((t) => t.kind === 'tool' && t.id === event.id)
      if (item && item.kind === 'tool') item.done = true
    } else if (event.type === 'approval_required') {
      // 图挂在写操作审批门上：每个待批动作画一条紧凑审批行（浅底 + 右侧执行/不执行按钮），门控
      // 输入框，等用户逐项裁决后续跑。记下 conversation（可能这轮新建的）+ thread_id。
      if (event.conversation_id) conversationId.value = event.conversation_id
      assistantOpen = false
      for (const a of event.actions) {
        timeline.value.push({
          kind: 'approval',
          key: uid(),
          threadId: event.thread_id,
          toolCallId: a.tool_call_id,
          operationId: a.operation_id ?? '',
          command: a.command ?? (a.args ? JSON.stringify(a.args) : a.title),
          label: [a.server_name, a.environment].filter(Boolean).join(' · '),
          riskLevel: a.risk_level,
          status: 'pending',
        })
      }
      pendingApproval.value = {
        runId: activeRunId.value as string,
        threadId: event.thread_id,
        decisions: {},
      }
    } else if (event.type === 'approval_resolved') {
      const decisions = new Map(event.decisions.map((item) => [item.tool_call_id, item]))
      for (const item of timeline.value) {
        if (
          item.kind === 'approval' &&
          item.threadId === event.thread_id &&
          item.status !== 'failed'
        ) {
          const decision = decisions.get(item.toolCallId)
          if (decision) item.status = decision.approved ? 'approved' : 'rejected'
        }
      }
      if (pendingApproval.value?.threadId === event.thread_id) {
        pendingApproval.value = null
      }
      confirmationApproval.value = null
      assistantOpen = false
    } else if (event.type === 'done') {
      conversationId.value = event.conversation_id
      activeRunId.value = null
      sending.value = false
      conversationListRef.value?.refresh()
    } else if (event.type === 'error') {
      error.value = event.message
      activeRunId.value = null
      sending.value = false
    }
  }
}

function stopSubscription() {
  subscriptionController?.abort()
  subscriptionController = null
}

async function attachRun(runId: string, after = 0) {
  stopSubscription()
  const controller = new AbortController()
  subscriptionController = controller
  activeRunId.value = runId
  lastEventSequence.value = after
  sending.value = true
  assistantOpen = false

  try {
    await consume(streamChatRun(runId, after, controller.signal))
  } catch (e) {
    if (!(e instanceof DOMException && e.name === 'AbortError')) {
      error.value = e instanceof Error ? e.message : String(e)
    }
  } finally {
    if (subscriptionController === controller) {
      subscriptionController = null
      // 运行完成、失败或停在审批门时订阅都会结束；审批期间由 pendingApproval 单独禁用输入框，
      // sending 必须恢复为 false，才能让“执行/不执行”按钮可点击。
      sending.value = false
    }
  }
}

async function send() {
  const text = input.value.trim()
  // 有待批操作时先不让发新消息——另起一轮会换新 thread、把这次挂起丢在一边
  if (!text || sending.value || pendingApproval.value) return

  timeline.value.push({ kind: 'message', key: uid(), role: 'user', content: text })
  input.value = ''
  sending.value = true
  error.value = null
  assistantOpen = false
  nextTick(scrollToBottom)

  try {
    const run = await startChatMessage(text, conversationId.value)
    activeRunId.value = run.run_id
    conversationId.value = run.conversation_id
    conversationListRef.value?.refresh()

    if (route.params.conversationId !== run.conversation_id) {
      // 新对话拿到后端生成的 UUID 后立刻写进地址栏；route watcher 会加载用户消息并订阅运行。
      await router.replace({
        name: 'chat-conversation',
        params: { conversationId: run.conversation_id },
      })
    } else {
      await loadConversation(run.conversation_id)
    }
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
    sending.value = false
  }
}

// 对当前挂起的写操作作出裁决：批准就真去执行，拒绝就回喂"已拒绝"让 agent 改口。两种都从挂起点
// 续跑同一轮对话（可能又冒出下一张待批卡片，consume 里会照样处理）。
async function selectApproval(
  item: ApprovalItem,
  approved: boolean,
  secondConfirmation = false,
) {
  const pending = pendingApproval.value
  if (!pending || sending.value || item.status !== 'pending') return

  pending.decisions[item.toolCallId] = {
    tool_call_id: item.toolCallId,
    operation_id: item.operationId,
    approved,
    second_confirmation: secondConfirmation,
  }
  item.status = approved ? 'selected_approved' : 'selected_rejected'
  const pendingItems = timeline.value.filter(
    (candidate): candidate is ApprovalItem =>
      candidate.kind === 'approval' && candidate.threadId === pending.threadId,
  )
  if (pendingItems.some((candidate) => candidate.status === 'pending')) return

  sending.value = true
  error.value = null
  assistantOpen = false
  const after = lastEventSequence.value

  try {
    await resumeApproval({
      runId: pending.runId,
      conversationId: conversationId.value as string,
      threadId: pending.threadId,
      decisions: Object.values(pending.decisions),
    })
    void attachRun(pending.runId, after)
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
    sending.value = false
    pending.decisions = {}
    for (const candidate of pendingItems) {
      if (candidate.status === 'selected_approved' || candidate.status === 'selected_rejected') {
        candidate.status = 'pending'
      }
    }
  }
}

function resetConversation() {
  stopSubscription()
  timeline.value = []
  conversationId.value = null
  activeRunId.value = null
  lastEventSequence.value = 0
  pendingApproval.value = null
  confirmationApproval.value = null
  sending.value = false
  error.value = null
  assistantOpen = false
}

async function newConversation() {
  if (route.name === 'chat') {
    resetConversation()
  } else {
    await router.push({ name: 'chat' })
  }
}

function onConversationDeleted(id: string) {
  // 删掉的正好是当前打开的这个对话 → 清空聊天区，回到"新对话"状态
  if (id === conversationId.value) void newConversation()
}

function buildTimeline(
  messages: ChatMessage[],
  runs: ChatRunHistory[],
  replayRunId: string | null,
): TimelineItem[] {
  const runById = new Map(runs.map((run) => [run.id, run]))
  // 当前内存 Run 会从 seq=0 重放 SSE；它已逐步落库的 assistant/tool 暂不从历史画，避免重复。
  // 用户消息不在 SSE 中，始终保留。
  const visibleMessages = messages.filter(
    (message) => message.role === 'user' || !replayRunId || message.run_id !== replayRunId,
  )
  const toolResultById = new Map<string, string>()
  const resultKey = (runId: string | null | undefined, toolCallId: string) =>
    `${runId ?? 'legacy'}:${toolCallId}`
  const lastMessageIndexByRun = new Map<string, number>()
  visibleMessages.forEach((message, index) => {
    if (message.run_id) lastMessageIndexByRun.set(message.run_id, index)
  })
  for (const message of visibleMessages) {
    if (message.role === 'tool' && message.tool_call_id) {
      toolResultById.set(resultKey(message.run_id, message.tool_call_id), message.content)
    }
  }

  return visibleMessages.flatMap((message, index): TimelineItem[] => {
    const items: TimelineItem[] = []
    const run = message.run_id ? runById.get(message.run_id) : undefined
    if (message.role === 'assistant' && message.tool_calls?.length) {
      if (message.content) {
        items.push({ kind: 'narration', key: uid(), content: message.content })
      }
      for (const toolCall of message.tool_calls) {
        const toolResultKey = toolCall.id
          ? resultKey(message.run_id, toolCall.id)
          : ''
        if (WRITE_TOOLS.has(toolCall.name)) {
          const result = (toolResultKey && toolResultById.get(toolResultKey)) || ''
          const args = (toolCall.args ?? {}) as Record<string, unknown>
          items.push({
            kind: 'approval',
            key: uid(),
            threadId: '',
            toolCallId: toolCall.id ?? '',
            operationId: '',
            command: historyApprovalCommand(toolCall.name, args),
            label: [args.server_name, args.environment].filter(Boolean).map(String).join(' · '),
            riskLevel: historyRiskLevel(toolCall.name),
            status: !result
              ? run?.status === 'failed'
                ? 'failed'
                : 'pending'
              : result.startsWith('用户拒绝执行')
                ? 'rejected'
                : 'approved',
          })
        } else {
          const done = Boolean(toolResultKey && toolResultById.has(toolResultKey))
          items.push({
            kind: 'tool',
            key: uid(),
            id: uid(),
            tool: toolCall.name,
            done,
            failed: !done && run?.status === 'failed',
          })
        }
      }
    } else if (message.role !== 'tool' && message.content) {
      items.push({ kind: 'message', key: uid(), role: message.role, content: message.content })
    }

    if (
      message.run_id &&
      run?.status === 'failed' &&
      lastMessageIndexByRun.get(message.run_id) === index
    ) {
      items.push({
        kind: 'run-error',
        key: uid(),
        content: run.error_message || '本轮执行失败，未生成完整回答。',
      })
    }
    return items
  })
}

async function loadConversation(id: string) {
  const version = ++loadVersion
  stopSubscription()
  conversationId.value = id
  activeRunId.value = null
  pendingApproval.value = null
  confirmationApproval.value = null
  lastEventSequence.value = 0
  sending.value = false
  error.value = null
  assistantOpen = false

  try {
    let detail = await fetchConversationDetail(id)
    const run = await fetchActiveChatRun(id)
    // active 查询返回 404 代表后台已经完成并落库；再读一次可避开第一次查询正好撞在落库前。
    if (!run) detail = await fetchConversationDetail(id)
    if (version !== loadVersion) return

    // 完整一轮的最后一条一定是无 tool_calls 的最终 assistant。极小概率下数据库已经落完，
    // 但 Chat Run 尚未来得及从 active 索引移除；此时直接使用历史，避免再重放一遍造成重复。
    const lastMessage = detail.messages.at(-1)
    const alreadyPersisted =
      lastMessage?.role === 'assistant' && !lastMessage.tool_calls?.length
    const replayRunId = run && !alreadyPersisted ? run.run_id : null
    timeline.value = buildTimeline(detail.messages, detail.runs, replayRunId)
    nextTick(scrollToBottom)
    if (replayRunId) void attachRun(replayRunId)
  } catch (e) {
    if (version === loadVersion) {
      error.value = e instanceof Error ? e.message : String(e)
    }
  }
}

function selectConversation(id: string) {
  if (id !== route.params.conversationId) {
    void router.push({ name: 'chat-conversation', params: { conversationId: id } })
  }
}

watch(
  () => route.params.conversationId,
  (id) => {
    if (typeof id === 'string') {
      void loadConversation(id)
    } else {
      loadVersion += 1
      resetConversation()
    }
  },
  { immediate: true },
)

// 服务目录的启停按钮只负责把明确的运维意图带到对话框；不会自动发送，更不会绕过写工具审批。
watch(
  () => route.query.prompt,
  (prompt) => {
    if (typeof prompt === 'string' && prompt.trim() && !input.value.trim()) {
      input.value = prompt.trim()
    }
  },
  { immediate: true },
)

onBeforeUnmount(() => {
  loadVersion += 1
  stopSubscription()
})
</script>

<template>
  <section class="chat-view">
    <ConversationList
      ref="conversationListRef"
      :selected-conversation-id="conversationId"
      @select="selectConversation"
      @deleted="onConversationDeleted"
    />

    <div class="chat-main">
      <div class="chat-header">
        <div class="header-row">
          <div>
            <span class="view-kicker">AI OPERATIONS / COPILOT</span>
            <h1>运维助手</h1>
          </div>
          <span class="agent-state"><i /> Agent 就绪</span>
          <button type="button" class="new-chat" @click="newConversation">新对话</button>
        </div>
      </div>

      <div ref="messagesEl" class="messages">
        <div class="messages-content">
          <div v-if="timeline.length === 0" class="chat-welcome">
            <div class="welcome-signal" aria-hidden="true">
              <span />
              <svg viewBox="0 0 180 48" fill="none">
                <path d="M2 26h36l8-17 13 33 12-27 10 11h25l7-10 9 19 8-9h48" />
              </svg>
            </div>
            <span class="welcome-label">安全、可审计的运维入口</span>
            <h2>今天要检查什么？</h2>
            <p>描述目标和现象，我会先查询现状；涉及变更的操作会停下来等你确认。</p>
            <div class="prompt-suggestions" aria-label="示例问题">
              <button type="button" @click="input = '现在有哪些服务器？'">盘点服务器</button>
              <button type="button" @click="input = '检查当前有哪些活跃告警'">查看活跃告警</button>
              <button type="button" @click="input = '帮我分析最近的运维事件'">分析近期事件</button>
            </div>
          </div>
          <template v-for="item in timeline" :key="item.key">
            <div v-if="item.kind === 'message'" class="message" :class="item.role">
              <MarkdownContent
                v-if="item.role === 'assistant'"
                :content="item.content"
              />
              <span v-else class="plain-message">{{ item.content }}</span>
            </div>
            <div v-else-if="item.kind === 'narration'" class="expert-narration">
              <span class="narration-label">专家旁白</span>
              <span>{{ item.content }}</span>
            </div>
            <div v-else-if="item.kind === 'tool'" class="tool-line" :class="{ done: item.done, failed: item.failed }">
              {{ item.failed ? '✕' : item.done ? '✓' : '›' }}
              {{ item.failed ? '调用失败：' : item.done ? '已' : '正在' }}{{ toolLabel(item.tool) }}{{ item.done || item.failed ? '' : '…' }}
            </div>
            <p v-else-if="item.kind === 'run-error'" class="run-error">本轮失败：{{ item.content }}</p>
            <div v-else-if="item.kind === 'approval'" class="approval-line" :class="item.status">
              <span class="approval-icon">{{
                item.status === 'pending'
                  ? '›'
                  : item.status === 'approved' || item.status === 'selected_approved'
                    ? '✓'
                    : '✕'
              }}</span>
              <span class="approval-text">
                <span class="approval-verb">{{
                  item.status === 'pending'
                    ? '待确认'
                    : item.status === 'approved'
                      ? '已批准'
                      : item.status === 'selected_approved'
                        ? '已选择执行'
                        : item.status === 'selected_rejected'
                          ? '已选择不执行'
                      : item.status === 'failed'
                        ? '未完成'
                        : '已拒绝'
                }}</span>
                <span v-if="item.riskLevel" class="approval-risk">{{ riskLevelLabel(item.riskLevel) }}</span>
                <code class="approval-cmd">{{ item.command }}</code>
                <span v-if="item.label" class="approval-loc">· {{ item.label }}</span>
              </span>
              <span v-if="item.status === 'pending'" class="approval-btns">
                <button type="button" class="approve" :disabled="sending" @click="requestApproval(item)">执行</button>
                <button type="button" class="reject" :disabled="sending" @click="selectApproval(item, false)">不执行</button>
              </span>
            </div>
          </template>
          <p v-if="showThinking" class="hint">正在生成回答…</p>
          <p v-if="error" class="error">{{ error }}</p>
        </div>
      </div>

      <div class="input-panel">
        <form class="input-row" @submit.prevent="send">
          <input
            v-model="input"
            :placeholder="pendingApproval ? '请先处理上面的确认…' : '描述目标、服务器或异常现象…'"
            :disabled="sending || !!pendingApproval"
          />
          <button type="submit" :disabled="sending || !!pendingApproval">发送</button>
        </form>
      </div>
    </div>

    <div
      v-if="confirmationApproval"
      class="confirm-backdrop"
      role="presentation"
      @click.self="closeConfirmation"
    >
      <div class="confirm-dialog" role="alertdialog" aria-modal="true" aria-labelledby="confirm-title">
        <div class="confirm-header">
          <span class="confirm-warning" aria-hidden="true">!</span>
          <div>
            <h2 id="confirm-title">再次确认高风险操作</h2>
            <p>这一步确认后才会向目标服务器的固定写网关提交操作。</p>
          </div>
        </div>
        <dl class="confirm-details">
          <div>
            <dt>风险等级</dt>
            <dd>{{ riskLevelLabel(confirmationApproval.riskLevel ?? 'high') }}</dd>
          </div>
          <div v-if="confirmationApproval.label">
            <dt>目标</dt>
            <dd>{{ confirmationApproval.label }}</dd>
          </div>
          <div>
            <dt>具体操作</dt>
            <dd><code>{{ confirmationApproval.command }}</code></dd>
          </div>
        </dl>
        <p class="confirm-tip">请核对目标、具体变更和影响。确认后操作可能造成服务中断或配置变化。</p>
        <p v-if="error" class="confirm-error">{{ error }}</p>
        <div class="confirm-actions">
          <button type="button" class="confirm-cancel" :disabled="sending" @click="closeConfirmation">取消</button>
          <button type="button" class="confirm-submit" :disabled="sending" @click="confirmHighRiskApproval">
            {{ sending ? '提交中…' : '确认执行' }}
          </button>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.chat-view {
  display: flex;
  height: 100%;
  min-height: 0;
  background: #ffffff;
}

.chat-main {
  flex: 1;
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  background: #ffffff;
}

.chat-header {
  flex-shrink: 0;
  padding: 1.5rem 2rem 0;
}

.header-row {
  width: 100%;
  max-width: 760px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin: 0 auto 1rem;
}

h1 {
  font-size: 1.4rem;
  margin: 0;
}

.new-chat {
  padding: 0.45rem 0.95rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--color-surface);
  color: var(--color-text);
  font-size: 0.85rem;
  transition: background 0.15s;
}

.new-chat:hover {
  background: var(--color-bg);
}

.messages {
  flex: 1;
  min-width: 0;
  min-height: 0;
  overflow-y: auto;
  overflow-x: hidden;
  scrollbar-gutter: stable;
  padding: 0 2rem;
  background: #ffffff;
  scrollbar-width: thin;
  scrollbar-color: #c7c9ce transparent;
}

.messages-content {
  width: 100%;
  max-width: 760px;
  min-height: 100%;
  margin: 0 auto;
  padding-bottom: 1rem;
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.messages::-webkit-scrollbar {
  width: 12px;
}

.messages::-webkit-scrollbar-track {
  border-left: 1px solid #eceef1;
  background: var(--color-surface);
}

.messages::-webkit-scrollbar-thumb {
  min-height: 36px;
  border: 3px solid var(--color-surface);
  border-radius: 999px;
  background: #c7c9ce;
}

.messages::-webkit-scrollbar-thumb:hover {
  background: #aeb1b7;
}

/* 外层列表负责纵向滚动；内容列里的每条消息保持真实高度，不参与 flex 压缩。 */
.messages-content > * {
  flex-shrink: 0;
}

.message {
  min-width: 0;
  max-width: min(80%, 100%);
  padding: 0;
  border: 0;
  border-radius: 0;
  background: transparent;
  overflow-wrap: anywhere;
}

.message.assistant {
  align-self: stretch;
  max-width: 100%;
}

.plain-message {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.message.user {
  align-self: flex-end;
  padding: 0.55rem 0.85rem;
  border-radius: var(--radius);
  background: #eaf1fe;
  color: var(--color-text);
}

.expert-narration {
  align-self: stretch;
  display: flex;
  align-items: baseline;
  gap: 0.65rem;
  padding: 0.55rem 0.7rem;
  border-left: 2px solid #8ba7d3;
  background: #f6f8fc;
  color: var(--color-text-muted);
  font-size: 0.86rem;
  line-height: 1.55;
}

.narration-label {
  flex-shrink: 0;
  color: #526b92;
  font-size: 0.72rem;
  font-weight: 700;
  letter-spacing: 0.06em;
}

/* 工具调用渲染成单行文本：运行时黑色，完成后变灰以区分。不是气泡/胶囊。 */
.tool-line {
  align-self: flex-start;
  font-size: 0.85rem;
  padding: 0 0.15rem;
  color: var(--color-text); /* 运行中：黑色 */
}

.tool-line:not(.done) {
  animation: blink 1.4s ease-in-out infinite;
}

.tool-line.done {
  color: var(--color-text-muted); /* 完成后：灰色 */
  animation: none;
}

.tool-line.failed {
  color: var(--color-status-unreachable);
  animation: none;
}

@keyframes blink {
  50% {
    opacity: 0.5;
  }
}

/* 写操作审批行：一整行浅色底，左侧图标+动作文字（命令用等宽），右侧执行/不执行按钮。
   pending 时带按钮；裁决后（approved/rejected）按钮消失、图标与措辞随状态变，且随历史持久重建。 */
.approval-line {
  align-self: stretch;
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.4rem 0.6rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--color-bg);
  font-size: 0.85rem;
}

.approval-icon {
  color: var(--color-text-muted);
  flex-shrink: 0;
}

.approval-line.approved .approval-icon,
.approval-line.selected_approved .approval-icon {
  color: var(--color-accent);
}

.approval-line.rejected .approval-icon,
.approval-line.selected_rejected .approval-icon,
.approval-line.failed .approval-icon {
  color: var(--color-status-unreachable);
}

.approval-text {
  flex: 1;
  min-width: 0;
  color: var(--color-text);
}

.approval-verb {
  margin-right: 0.35rem;
}

.approval-risk {
  margin-right: 0.35rem;
  padding: 0.05rem 0.3rem;
  border-radius: 4px;
  background: color-mix(in srgb, var(--color-status-unreachable) 12%, transparent);
  color: var(--color-status-unreachable);
  font-size: 0.75rem;
}

.approval-cmd {
  padding: 0.05rem 0.35rem;
  border: 1px solid var(--color-border);
  border-radius: 4px;
  background: var(--color-surface);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.82rem;
  white-space: pre-wrap;
  word-break: break-all;
}

.approval-loc {
  margin-left: 0.35rem;
  color: var(--color-text-muted);
}

.approval-btns {
  display: flex;
  gap: 0.4rem;
  flex-shrink: 0;
}

.approval-btns button {
  padding: 0.25rem 0.7rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  font-size: 0.8rem;
}

.approval-btns .approve {
  background: var(--color-accent);
  color: var(--color-accent-contrast);
  border-color: var(--color-accent);
}

.approval-btns .reject {
  background: var(--color-surface);
  color: var(--color-text);
}

.approval-btns button:disabled {
  opacity: 0.6;
  cursor: default;
}

.confirm-backdrop {
  position: fixed;
  inset: 0;
  z-index: 1000;
  display: grid;
  place-items: center;
  padding: 1.5rem;
  background: rgb(15 23 42 / 42%);
}

.confirm-dialog {
  width: min(480px, 100%);
  padding: 1.25rem;
  border: 1px solid var(--color-border);
  border-radius: 14px;
  background: #ffffff;
  box-shadow: 0 18px 50px rgb(15 23 42 / 18%);
}

.confirm-header {
  display: flex;
  align-items: flex-start;
  gap: 0.75rem;
}

.confirm-warning {
  display: grid;
  flex: 0 0 2rem;
  width: 2rem;
  height: 2rem;
  place-items: center;
  border-radius: 50%;
  background: #fff1f0;
  color: var(--color-status-unreachable);
  font-weight: 700;
}

.confirm-header h2 {
  margin: 0;
  font-size: 1.05rem;
}

.confirm-header p {
  margin: 0.3rem 0 0;
  color: var(--color-text-muted);
  font-size: 0.85rem;
  line-height: 1.5;
}

.confirm-details {
  margin: 1rem 0;
  padding: 0.75rem;
  border-radius: var(--radius);
  background: #f8f9fa;
}

.confirm-details > div {
  display: grid;
  grid-template-columns: 5rem minmax(0, 1fr);
  gap: 0.6rem;
  padding: 0.3rem 0;
}

.confirm-details dt {
  color: var(--color-text-muted);
  font-size: 0.82rem;
}

.confirm-details dd {
  min-width: 0;
  margin: 0;
  overflow-wrap: anywhere;
  font-size: 0.85rem;
}

.confirm-details code {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  white-space: pre-wrap;
}

.confirm-tip {
  margin: 0;
  color: var(--color-status-unreachable);
  font-size: 0.82rem;
}

.confirm-error {
  margin: 0.5rem 0 0;
  color: var(--color-status-unreachable);
  font-size: 0.82rem;
}

.confirm-actions {
  display: flex;
  justify-content: flex-end;
  gap: 0.6rem;
  margin-top: 1.25rem;
}

.confirm-actions button {
  padding: 0.5rem 1rem;
  border-radius: var(--radius);
  font-size: 0.85rem;
}

.confirm-cancel {
  border: 1px solid var(--color-border);
  background: #ffffff;
  color: var(--color-text);
}

.confirm-submit {
  border: 1px solid var(--color-status-unreachable);
  background: var(--color-status-unreachable);
  color: #ffffff;
}

.confirm-actions button:disabled {
  cursor: default;
  opacity: 0.6;
}

.hint {
  color: var(--color-text-muted);
}

.error {
  color: var(--color-status-unreachable);
}

.run-error {
  margin: 0;
  padding: 0.45rem 0.65rem;
  border-left: 3px solid var(--color-status-unreachable);
  background: color-mix(in srgb, var(--color-status-unreachable) 8%, transparent);
  color: var(--color-status-unreachable);
  font-size: 0.85rem;
}

.input-row {
  width: 100%;
  max-width: 760px;
  display: flex;
  gap: 0.5rem;
  margin: 0 auto;
}

.input-panel {
  flex-shrink: 0;
  padding: 0.5rem 2rem 1.5rem;
  background: #ffffff;
}

.input-row input {
  flex: 1;
  padding: 0.55rem 0.85rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius);
}

.input-row button {
  padding: 0.55rem 1.1rem;
  border: none;
  border-radius: var(--radius);
  background: var(--color-accent);
  color: var(--color-accent-contrast);
}

.input-row button:disabled {
  opacity: 0.6;
  cursor: default;
}

/* 视觉重塑：把对话区做成运维工作流，而不是通用聊天窗口。 */
.chat-view,
.chat-main,
.messages,
.input-panel {
  background: var(--color-surface);
}

.chat-header {
  padding: 1.35rem 2rem 0;
  background: rgba(249, 251, 252, 0.94);
}

.header-row {
  max-width: 860px;
  margin-bottom: 0;
  padding-bottom: 1rem;
  border-bottom: 1px solid var(--color-border);
}

.view-kicker {
  display: block;
  margin-bottom: 0.18rem;
  color: var(--color-accent);
  font-family: var(--font-data);
  font-size: 0.62rem;
  font-weight: 600;
  letter-spacing: 0.12em;
}

.header-row h1 {
  font-family: var(--font-display);
  font-size: 1.62rem;
  font-weight: 650;
  letter-spacing: 0.01em;
}

.agent-state {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  margin-left: auto;
  color: var(--color-text-muted);
  font-size: 0.73rem;
}

.agent-state i {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--color-status-active);
  box-shadow: 0 0 0 4px rgba(35, 133, 109, 0.1);
}

.new-chat {
  margin-left: 1rem;
  min-height: 38px;
  border-color: var(--color-border-strong);
  background: transparent;
  font-weight: 500;
}

.messages {
  padding: 0 2rem;
  scrollbar-color: #acb8bd transparent;
}

.messages-content {
  max-width: 860px;
  padding-top: 1.15rem;
  gap: 0.9rem;
}

.chat-welcome {
  width: min(100%, 620px);
  margin: auto;
  padding: 2.5rem 1rem 4rem;
  text-align: center;
}

.welcome-signal {
  position: relative;
  width: 84px;
  height: 84px;
  margin: 0 auto 1.4rem;
  display: grid;
  place-items: center;
  border: 1px solid var(--color-border);
  border-radius: 50%;
  background: #ffffff;
  box-shadow: 0 0 0 8px rgba(22, 137, 154, 0.045);
}

.welcome-signal::before,
.welcome-signal::after {
  content: '';
  position: absolute;
  background: var(--color-border);
}

.welcome-signal::before { width: 112px; height: 1px; }
.welcome-signal::after { width: 1px; height: 112px; }

.welcome-signal span {
  position: absolute;
  width: 9px;
  height: 9px;
  border: 2px solid #ffffff;
  border-radius: 50%;
  background: var(--color-accent);
  box-shadow: 0 0 0 5px rgba(22, 137, 154, 0.14);
}

.welcome-signal svg {
  position: absolute;
  z-index: 1;
  width: 116px;
}

.welcome-signal path {
  stroke: var(--color-accent);
  stroke-width: 2;
  stroke-linecap: round;
  stroke-linejoin: round;
}

.welcome-label {
  color: var(--color-accent);
  font-family: var(--font-data);
  font-size: 0.65rem;
  letter-spacing: 0.1em;
}

.chat-welcome h2 {
  margin: 0.45rem 0 0.55rem;
  font-family: var(--font-display);
  font-size: clamp(1.9rem, 4vw, 2.45rem);
  font-weight: 600;
  letter-spacing: -0.015em;
}

.chat-welcome p {
  max-width: 490px;
  margin: 0 auto;
  color: var(--color-text-muted);
  line-height: 1.75;
}

.prompt-suggestions {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 8px;
  margin-top: 1.5rem;
}

.prompt-suggestions button {
  min-height: 38px;
  padding: 0.45rem 0.75rem;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  color: var(--color-text-muted);
  background: rgba(255, 255, 255, 0.75);
  font-size: 0.78rem;
  transition: color 150ms ease, border-color 150ms ease, transform 150ms ease;
}

.prompt-suggestions button:hover {
  color: var(--color-accent);
  border-color: rgba(22, 137, 154, 0.55);
  transform: translateY(-1px);
}

.message.user {
  padding: 0.65rem 0.9rem;
  border: 1px solid #c9e2e6;
  border-radius: 11px 11px 3px 11px;
  background: #e5f2f4;
}

.tool-line {
  padding: 0.38rem 0.6rem;
  border-left: 2px solid var(--color-accent);
  color: var(--color-text-muted);
  background: linear-gradient(90deg, rgba(22, 137, 154, 0.07), transparent 75%);
  font-family: var(--font-data);
  font-size: 0.74rem;
}

.input-panel {
  padding: 0.75rem 2rem 1.35rem;
}

.input-row {
  max-width: 860px;
  gap: 8px;
  padding: 6px;
  border: 1px solid var(--color-border-strong);
  border-radius: 12px;
  background: #ffffff;
  box-shadow: 0 8px 28px rgba(16, 39, 51, 0.08);
}

.input-row input {
  min-height: 42px;
  border: 0;
  background: transparent;
  box-shadow: none;
}

.input-row button {
  min-width: 74px;
  border-radius: 8px;
  font-weight: 600;
}

@media (max-width: 760px) {
  .chat-header,
  .messages,
  .input-panel {
    padding-left: 1rem;
    padding-right: 1rem;
  }

  .agent-state {
    display: none;
  }

  .chat-welcome {
    padding-top: 2rem;
  }
}
</style>
