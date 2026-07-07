<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { streamChatMessage } from '../api/chat'
import { fetchConversationDetail } from '../api/conversations'
import ConversationList from '../components/ConversationList.vue'
import type { ChatMessage } from '../types/chat'

// 会话时间线：消息和工具调用按发生顺序混在一个数组里，这样工具卡片能显示在它实际被
// 调用的那个位置（夹在文本流中间），而不是全部堆在回答下面。工具项是纯实时展示——
// 不落库、不随历史加载，切换对话就没了。
type TimelineItem =
  | { kind: 'message'; key: string; role: 'user' | 'assistant'; content: string }
  | { kind: 'tool'; key: string; id: string; tool: string; done: boolean }

const timeline = ref<TimelineItem[]>([])
const input = ref('')
const sending = ref(false)
const error = ref<string | null>(null)
// 同一个 conversationId 下的多轮对话，每次发消息后端都从 messages 表把历史加载出来；
// 换一个 conversationId（或不传）就是开新对话，互相不共享上下文。
const conversationId = ref<string | null>(null)
const conversationListRef = ref<InstanceType<typeof ConversationList> | null>(null)
const messagesEl = ref<HTMLElement | null>(null)

let uidCounter = 0
function uid(): string {
  return `i${uidCounter++}`
}

const TOOL_LABELS: Record<string, string> = {
  query_servers: '查询服务器列表',
  search_logs: '检索日志',
  query_alerts: '查询告警',
  run_whitelisted_command: '执行远程命令',
}
function toolLabel(name: string): string {
  return TOOL_LABELS[name] ?? name
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

// 当前是否有一个"打开的"助手气泡可以继续追加 token。调用工具后会关闭它，下一段文字会
// 另起一个新气泡，从而让工具卡片自然地夹在两段文字之间。
let assistantOpen = false

function appendToken(content: string) {
  if (!assistantOpen) {
    timeline.value.push({ kind: 'message', key: uid(), role: 'assistant', content: '' })
    assistantOpen = true
  }
  const last = timeline.value[timeline.value.length - 1]
  if (last.kind === 'message') last.content += content
}

function discardDraft() {
  // Reflexion 质检打回重试：被否决那一整次（旁白 + 工具行 + 答案）都作废——把最近一条 user
  // 消息之后的全部删掉，等重试从头流入。后端那边被否决的尝试也被图内 RemoveMessage 从状态里
  // 剪掉、不会落库，所以实时稳定态 == 刷新态（都只留通过的那一次）。
  let lastUserIdx = -1
  for (let i = timeline.value.length - 1; i >= 0; i--) {
    const item = timeline.value[i]
    if (item.kind === 'message' && item.role === 'user') {
      lastUserIdx = i
      break
    }
  }
  timeline.value.splice(lastUserIdx + 1)
  assistantOpen = false
}

// 还在"思考中"（agent 首次调用还没吐出工具或文字）：最后一项还是用户那条消息
const showThinking = computed(() => {
  if (!sending.value) return false
  const last = timeline.value[timeline.value.length - 1]
  return !last || (last.kind === 'message' && last.role === 'user')
})

async function send() {
  const text = input.value.trim()
  if (!text || sending.value) return

  timeline.value.push({ kind: 'message', key: uid(), role: 'user', content: text })
  input.value = ''
  sending.value = true
  error.value = null
  assistantOpen = false
  nextTick(scrollToBottom)

  try {
    for await (const event of streamChatMessage(text, conversationId.value)) {
      if (event.type === 'token') {
        appendToken(event.content)
      } else if (event.type === 'reset') {
        discardDraft()
      } else if (event.type === 'tool_start') {
        // 调工具前的旁白气泡保留下来（档位 B 会持久化它，刷新后也能重建）；这里只是关闭当前
        // 气泡，让工具行、以及工具之后的最终答案各自成为独立的块。
        assistantOpen = false
        timeline.value.push({ kind: 'tool', key: uid(), id: event.id, tool: event.tool, done: false })
      } else if (event.type === 'tool_end') {
        const item = timeline.value.find((t) => t.kind === 'tool' && t.id === event.id)
        if (item && item.kind === 'tool') item.done = true
      } else if (event.type === 'done') {
        conversationId.value = event.conversation_id
      } else if (event.type === 'error') {
        error.value = event.message
      }
    }
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    sending.value = false
    // 新对话产生了标题、或者已有对话的 updated_at 刷新了排序，都需要重新拉一下列表
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
    // 从 canonical 消息行重建时间线，跟实时时一致：
    // - tool 行（工具结果）不单独显示——工具行由它前面 assistant 的 tool_calls 渲染
    // - 带 tool_calls 的 assistant：先旁白气泡（如果有文字），再每个 tool_call 一条工具行（历史一律 done）
    // - 普通 user 提问 / assistant 最终答案 → 气泡
    timeline.value = detail.messages.flatMap((m: ChatMessage): TimelineItem[] => {
      if (m.role === 'tool') return []
      const items: TimelineItem[] = []
      if (m.role === 'assistant' && m.tool_calls?.length) {
        if (m.content) {
          items.push({ kind: 'message', key: uid(), role: 'assistant', content: m.content })
        }
        for (const tc of m.tool_calls) {
          items.push({ kind: 'tool', key: uid(), id: uid(), tool: tc.name, done: true })
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
          <div v-else class="tool-line" :class="{ done: item.done }">
            {{ item.done ? '✓' : '›' }} {{ item.done ? '已' : '正在' }}{{ toolLabel(item.tool) }}{{ item.done ? '' : '…' }}
          </div>
        </template>
        <p v-if="showThinking" class="hint">思考中…</p>
        <p v-if="error" class="error">{{ error }}</p>
      </div>

      <form class="input-row" @submit.prevent="send">
        <input v-model="input" placeholder="输入消息…" :disabled="sending" />
        <button type="submit" :disabled="sending">发送</button>
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
