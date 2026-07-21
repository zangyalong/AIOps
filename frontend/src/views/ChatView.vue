<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { resumeApproval, streamChatMessage, type ChatStreamEvent } from '../api/chat'
import { fetchConversationDetail } from '../api/conversations'
import ConversationList from '../components/ConversationList.vue'
import type { ChatMessage } from '../types/chat'

// 会话时间线：消息、工具行、写操作审批行按发生顺序混在一个数组里，这样它们能显示在实际
// 发生的那个位置（夹在文本流中间）。工具行和审批行都会随历史重建：工具行来自 assistant 的
// tool_calls；审批行来自写工具的 tool_call + 它配对的返回（据此还原当初是执行了还是被拒）。
type TimelineItem =
  | { kind: 'message'; key: string; role: 'user' | 'assistant'; content: string }
  | { kind: 'tool'; key: string; id: string; tool: string; done: boolean }
  | {
      // 写操作审批行：pending 时右侧带执行/不执行按钮；裁决后翻成 approved（已执行）/rejected（已拒绝）
      kind: 'approval'
      key: string
      threadId: string // 挂起恢复用；历史重建的已决定项为 '' （无需也无法再恢复）
      command: string // 展示的命令 / 动作摘要
      label: string // 副标题（在哪台执行等），可空
      status: 'pending' | 'approved' | 'rejected'
    }

const timeline = ref<TimelineItem[]>([])
const input = ref('')
const sending = ref(false)
const error = ref<string | null>(null)
// 同一个 conversationId 下的多轮对话，每次发消息后端都从 messages 表把历史加载出来；
// 换一个 conversationId（或不传）就是开新对话，互相不共享上下文。
const conversationId = ref<string | null>(null)
const conversationListRef = ref<InstanceType<typeof ConversationList> | null>(null)
const messagesEl = ref<HTMLElement | null>(null)
// 当前是否有一个写操作挂在审批门上等裁决。非空时输入框禁用（先处理确认再继续对话），
// 记住要恢复哪条挂起（threadId）——裁决时按 threadId 把这次挂起的所有待批行一起翻状态。
const pendingApproval = ref<{ threadId: string } | null>(null)

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
  run_whitelisted_command: '执行远程命令',
}
function toolLabel(name: string): string {
  return TOOL_LABELS[name] ?? name
}

// 与后端 chat_graph.py 的 WRITE_TOOLS 对应：这些工具的调用要走审批、渲染成带按钮的审批行
// （而非普通工具行），历史重建时也据此还原成审批行。加新写工具时两边都要更新。
const WRITE_TOOLS = new Set(['run_whitelisted_command'])

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

// 消费一段 SSE 事件流（初次发消息、以及审批后恢复，都走这里）。approval_required 会画出一张
// 待批卡片并门控输入框，等 decide() 续跑。
async function consume(stream: AsyncGenerator<ChatStreamEvent>) {
  for await (const event of stream) {
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
          label: a.server_name ?? '',
          status: 'pending',
        })
      }
      pendingApproval.value = { threadId: event.thread_id }
    } else if (event.type === 'done') {
      conversationId.value = event.conversation_id
    } else if (event.type === 'error') {
      error.value = event.message
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
    await consume(streamChatMessage(text, conversationId.value))
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    sending.value = false
    // 新对话产生了标题、或者已有对话的 updated_at 刷新了排序，都需要重新拉一下列表
    conversationListRef.value?.refresh()
  }
}

// 对当前挂起的写操作作出裁决：批准就真去执行，拒绝就回喂"已拒绝"让 agent 改口。两种都从挂起点
// 续跑同一轮对话（可能又冒出下一张待批卡片，consume 里会照样处理）。
async function decide(approved: boolean) {
  const pending = pendingApproval.value
  if (!pending || sending.value) return

  // 乐观翻转这次挂起的所有待批行（批准→已执行 / 拒绝→已拒绝），按钮随之消失
  const next = approved ? 'approved' : 'rejected'
  for (const t of timeline.value) {
    if (t.kind === 'approval' && t.threadId === pending.threadId && t.status === 'pending') {
      t.status = next
    }
  }
  pendingApproval.value = null
  sending.value = true
  error.value = null
  assistantOpen = false

  try {
    await consume(
      resumeApproval({
        conversationId: conversationId.value as string,
        threadId: pending.threadId,
        approved,
      }),
    )
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    sending.value = false
    conversationListRef.value?.refresh()
  }
}

function newConversation() {
  timeline.value = []
  conversationId.value = null
  error.value = null
  assistantOpen = false
}

function onConversationDeleted(id: string) {
  // 删掉的正好是当前打开的这个对话 → 清空聊天区，回到"新对话"状态
  if (id === conversationId.value) newConversation()
}

async function selectConversation(id: string) {
  if (sending.value) return
  error.value = null
  try {
    const detail = await fetchConversationDetail(id)
    conversationId.value = detail.conversation.id
    // 先建 tool_call_id → 该工具返回内容 的映射：写工具的审批行要据此还原当初是执行了还是被拒。
    const toolResultById = new Map<string, string>()
    for (const m of detail.messages) {
      if (m.role === 'tool' && m.tool_call_id) toolResultById.set(m.tool_call_id, m.content)
    }
    // 从 canonical 消息行重建时间线，跟实时时一致：
    // - tool 行（工具结果）不单独显示——由它前面 assistant 的 tool_calls 渲染
    // - 带 tool_calls 的 assistant：先旁白气泡（如有文字），再每个 tool_call 一行：写工具→审批行
    //   （据配对返回还原已执行/已拒绝），只读工具→普通工具行（历史一律 done）
    // - 普通 user 提问 / assistant 最终答案 → 气泡
    timeline.value = detail.messages.flatMap((m: ChatMessage): TimelineItem[] => {
      if (m.role === 'tool') return []
      const items: TimelineItem[] = []
      if (m.role === 'assistant' && m.tool_calls?.length) {
        if (m.content) {
          items.push({ kind: 'message', key: uid(), role: 'assistant', content: m.content })
        }
        for (const tc of m.tool_calls) {
          if (WRITE_TOOLS.has(tc.name)) {
            // 写工具重建成审批行，持久显示当初的裁决：返回以"用户拒绝执行"开头 = 被拒，否则已执行。
            const result = (tc.id && toolResultById.get(tc.id)) || ''
            const args = (tc.args ?? {}) as Record<string, unknown>
            items.push({
              kind: 'approval',
              key: uid(),
              threadId: '',
              command: String(args.command ?? tc.name),
              label: String(args.server_name ?? ''),
              status: result.startsWith('用户拒绝执行') ? 'rejected' : 'approved',
            })
          } else {
            items.push({ kind: 'tool', key: uid(), id: uid(), tool: tc.name, done: true })
          }
        }
        return items
      }
      if (m.content) {
        items.push({ kind: 'message', key: uid(), role: m.role, content: m.content })
      }
      return items
    })
    assistantOpen = false
    nextTick(scrollToBottom)
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  }
}
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
      <div class="header-row">
        <h1>运维助手</h1>
        <button type="button" class="new-chat" @click="newConversation">新对话</button>
      </div>

      <div ref="messagesEl" class="messages">
        <p v-if="timeline.length === 0" class="hint">
          试着问一句"现在有哪些服务器？"
        </p>
        <template v-for="item in timeline" :key="item.key">
          <div v-if="item.kind === 'message'" class="message" :class="item.role">
            <strong>{{ item.role === 'user' ? '我' : '助手' }}：</strong>
            <span style="white-space: pre-wrap">{{ item.content }}</span>
          </div>
          <div v-else-if="item.kind === 'tool'" class="tool-line" :class="{ done: item.done }">
            {{ item.done ? '✓' : '›' }} {{ item.done ? '已' : '正在' }}{{ toolLabel(item.tool) }}{{ item.done ? '' : '…' }}
          </div>
          <div v-else-if="item.kind === 'approval'" class="approval-line" :class="item.status">
            <span class="approval-icon">{{
              item.status === 'pending' ? '›' : item.status === 'approved' ? '✓' : '✕'
            }}</span>
            <span class="approval-text">
              <span class="approval-verb">{{
                item.status === 'pending' ? '待确认' : item.status === 'approved' ? '已执行' : '已拒绝'
              }}</span>
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

      <form class="input-row" @submit.prevent="send">
        <input
          v-model="input"
          :placeholder="pendingApproval ? '请先处理上面的确认…' : '输入消息…'"
          :disabled="sending || !!pendingApproval"
        />
        <button type="submit" :disabled="sending || !!pendingApproval">发送</button>
      </form>
    </div>
  </section>
</template>

<style scoped>
.chat-view {
  display: flex;
  gap: 1.5rem;
  height: 100vh;
  padding: 1.5rem 2rem;
}

.chat-main {
  flex: 1;
  min-width: 0;
  max-width: 760px;
  display: flex;
  flex-direction: column;
}

.header-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 1rem;
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
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
  margin-bottom: 1rem;
}

.message {
  max-width: 80%;
  padding: 0.55rem 0.85rem;
  border-radius: var(--radius);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
}

.message.user {
  align-self: flex-end;
  background: var(--color-accent);
  color: var(--color-accent-contrast);
  border-color: var(--color-accent);
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

.approval-line.rejected .approval-icon {
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

.input-row {
  display: flex;
  gap: 0.5rem;
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
