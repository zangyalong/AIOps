<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  fetchActiveChatRun,
  resumeApproval,
  startChatMessage,
  streamChatRun,
  type ChatStreamEvent,
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
  | { kind: 'tool'; key: string; id: string; tool: string; done: boolean; failed?: boolean }
  | { kind: 'run-error'; key: string; content: string }
  | {
      // 写操作审批行：pending 时右侧带执行/不执行按钮；裁决后翻成 approved（已批准）/rejected（已拒绝）
      kind: 'approval'
      key: string
      threadId: string // 挂起恢复用；历史重建的已决定项为 '' （无需也无法再恢复）
      command: string // 展示的命令 / 动作摘要
      label: string // 副标题（在哪台执行等），可空
      riskLevel?: 'medium' | 'high' | 'critical'
      status: 'pending' | 'approved' | 'rejected' | 'failed'
    }

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
const pendingApproval = ref<{ runId: string; threadId: string } | null>(null)
const activeRunId = ref<string | null>(null)
const lastEventSequence = ref(0)
let subscriptionController: AbortController | null = null
let loadVersion = 0

let uidCounter = 0
function uid(): string {
  return `i${uidCounter++}`
}

const TOOL_LABELS: Record<string, string> = {
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
  restart_service: '重启服务',
  run_whitelisted_command: '执行远程命令（旧记录）',
}
function toolLabel(name: string): string {
  return TOOL_LABELS[name] ?? name
}

// 目录注册和服务重启是当前写工具；旧名称仅用于正确还原已有历史会话，不会再绑定给 Agent。
const WRITE_TOOLS = new Set([
  'register_server_services',
  'restart_service',
  'run_whitelisted_command',
])

function historyApprovalCommand(name: string, args: Record<string, unknown>): string {
  if (name === 'restart_service') {
    return `重启服务 ${String(args.service_name ?? '?')}`
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
  return String(args.command ?? name)
}

function riskLevelLabel(level: 'medium' | 'high' | 'critical'): string {
  if (level === 'medium') return '目录写入'
  if (level === 'critical') return '严重风险'
  return '高风险'
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

// 当前是否有一个"打开的"助手气泡可以继续追加 token。最终综合只执行一次，输出 token 持续
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

// 最终综合开始输出前显示等待状态。工具正在执行时已有动态工具行，不重复显示。
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
    } else if (event.type === 'tool_start') {
      // 工具过程与最终回答分块展示；正常情况下最终回答尚未出现，这里关闭气泡是防御性处理。
      assistantOpen = false
      timeline.value.push({ kind: 'tool', key: uid(), id: event.id, tool: event.tool, done: false })
    } else if (event.type === 'tool_end') {
      const item = timeline.value.find((t) => t.kind === 'tool' && t.id === event.id)
      if (item && item.kind === 'tool') item.done = true
    } else if (event.type === 'approval_required') {
      // 图挂在写操作审批门上：每个待批动作画一条紧凑审批行（浅底 + 右侧执行/不执行按钮），门控
      // 输入框，等用户点按钮由 decide() 续跑。记下 conversation（可能这轮新建的）+ thread_id。
      if (event.conversation_id) conversationId.value = event.conversation_id
      assistantOpen = false
      for (const a of event.actions) {
        timeline.value.push({
          kind: 'approval',
          key: uid(),
          threadId: event.thread_id,
          command: a.command ?? (a.args ? JSON.stringify(a.args) : a.title),
          label: [a.server_name, a.environment].filter(Boolean).join(' · '),
          riskLevel: a.risk_level,
          status: 'pending',
        })
      }
      pendingApproval.value = {
        runId: activeRunId.value as string,
        threadId: event.thread_id,
      }
    } else if (event.type === 'approval_resolved') {
      for (const item of timeline.value) {
        if (
          item.kind === 'approval' &&
          item.threadId === event.thread_id &&
          item.status === 'pending'
        ) {
          item.status = event.approved ? 'approved' : 'rejected'
        }
      }
      if (pendingApproval.value?.threadId === event.thread_id) {
        pendingApproval.value = null
      }
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
async function decide(approved: boolean) {
  const pending = pendingApproval.value
  if (!pending || sending.value) return

  sending.value = true
  error.value = null
  assistantOpen = false
  const after = lastEventSequence.value

  try {
    await resumeApproval({
      runId: pending.runId,
      conversationId: conversationId.value as string,
      threadId: pending.threadId,
      approved,
    })
    void attachRun(pending.runId, after)
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
    sending.value = false
  }
}

function resetConversation() {
  stopSubscription()
  timeline.value = []
  conversationId.value = null
  activeRunId.value = null
  lastEventSequence.value = 0
  pendingApproval.value = null
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
        items.push({ kind: 'message', key: uid(), role: 'assistant', content: message.content })
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
            command: historyApprovalCommand(toolCall.name, args),
            label: [args.server_name, args.environment].filter(Boolean).map(String).join(' · '),
            riskLevel:
              toolCall.name === 'register_server_services'
                ? 'medium'
                : toolCall.name === 'restart_service'
                  ? 'high'
                  : undefined,
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
          <h1>运维助手</h1>
          <button type="button" class="new-chat" @click="newConversation">新对话</button>
        </div>
      </div>

      <div ref="messagesEl" class="messages">
        <div class="messages-content">
          <p v-if="timeline.length === 0" class="hint">
            试着问一句"现在有哪些服务器？"
          </p>
          <template v-for="item in timeline" :key="item.key">
            <div v-if="item.kind === 'message'" class="message" :class="item.role">
              <MarkdownContent
                v-if="item.role === 'assistant'"
                :content="item.content"
              />
              <span v-else class="plain-message">{{ item.content }}</span>
            </div>
            <div v-else-if="item.kind === 'tool'" class="tool-line" :class="{ done: item.done, failed: item.failed }">
              {{ item.failed ? '✕' : item.done ? '✓' : '›' }}
              {{ item.failed ? '调用失败：' : item.done ? '已' : '正在' }}{{ toolLabel(item.tool) }}{{ item.done || item.failed ? '' : '…' }}
            </div>
            <p v-else-if="item.kind === 'run-error'" class="run-error">本轮失败：{{ item.content }}</p>
            <div v-else-if="item.kind === 'approval'" class="approval-line" :class="item.status">
              <span class="approval-icon">{{
                item.status === 'pending' ? '›' : item.status === 'approved' ? '✓' : '✕'
              }}</span>
              <span class="approval-text">
                <span class="approval-verb">{{
                  item.status === 'pending'
                    ? '待确认'
                    : item.status === 'approved'
                      ? '已批准'
                      : item.status === 'failed'
                        ? '未完成'
                        : '已拒绝'
                }}</span>
                <span v-if="item.riskLevel" class="approval-risk">{{ riskLevelLabel(item.riskLevel) }}</span>
                <code class="approval-cmd">{{ item.command }}</code>
                <span v-if="item.label" class="approval-loc">· {{ item.label }}</span>
              </span>
              <span v-if="item.status === 'pending'" class="approval-btns">
                <button type="button" class="approve" :disabled="sending" @click="decide(true)">执行</button>
                <button type="button" class="reject" :disabled="sending" @click="decide(false)">不执行</button>
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
            :placeholder="pendingApproval ? '请先处理上面的确认…' : '输入消息…'"
            :disabled="sending || !!pendingApproval"
          />
          <button type="submit" :disabled="sending || !!pendingApproval">发送</button>
        </form>
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

.approval-line.approved .approval-icon {
  color: var(--color-accent);
}

.approval-line.rejected .approval-icon,
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
</style>
