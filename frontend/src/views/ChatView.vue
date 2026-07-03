<script setup lang="ts">
import { ref } from 'vue'
import { streamChatMessage } from '../api/chat'
import { fetchConversationDetail } from '../api/conversations'
import ConversationList from '../components/ConversationList.vue'
import type { ChatMessage } from '../types/chat'

const messages = ref<ChatMessage[]>([])
const input = ref('')
const sending = ref(false)
const error = ref<string | null>(null)
// 同一个 conversationId 下的多轮对话，每次发消息后端都从 messages 表把历史加载出来；
// 换一个 conversationId（或不传）就是开新对话，互相不共享上下文。
const conversationId = ref<string | null>(null)
const conversationListRef = ref<InstanceType<typeof ConversationList> | null>(null)

async function send() {
  const text = input.value.trim()
  if (!text || sending.value) return

  messages.value.push({ role: 'user', content: text })
  messages.value.push({ role: 'assistant', content: '' })
  const assistantIndex = messages.value.length - 1
  input.value = ''
  sending.value = true
  error.value = null

  try {
    for await (const event of streamChatMessage(text, conversationId.value)) {
      if (event.type === 'token') {
        messages.value[assistantIndex].content += event.content
      } else if (event.type === 'reset') {
        // 反思环节打回重试：之前流出去的是被否决的草稿，清空重新显示
        messages.value[assistantIndex].content = ''
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
  messages.value = []
  conversationId.value = null
  error.value = null
}

async function selectConversation(id: string) {
  if (sending.value) return
  error.value = null
  try {
    const detail = await fetchConversationDetail(id)
    conversationId.value = detail.conversation.id
    messages.value = detail.messages
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
    />

    <div class="chat-main">
      <div class="header-row">
        <h1>运维助手</h1>
        <button type="button" class="new-chat" @click="newConversation">新对话</button>
      </div>

      <div class="messages">
        <p v-if="messages.length === 0" class="hint">
          试着问一句"现在有哪些服务器？"
        </p>
        <div
          v-for="(msg, i) in messages"
          :key="i"
          class="message"
          :class="msg.role"
        >
          <strong>{{ msg.role === 'user' ? '我' : '助手' }}：</strong>
          <span style="white-space: pre-wrap">{{ msg.content }}</span>
        </div>
        <p v-if="sending && !messages[messages.length - 1]?.content" class="hint">
          思考中…
        </p>
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
  max-width: 960px;
  margin: 2rem auto 0;
  padding: 0 2rem 2rem;
}

.chat-main {
  flex: 1;
  min-width: 0;
}

.header-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.new-chat {
  padding: 0.4rem 0.75rem;
}

.messages {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
  min-height: 120px;
  margin-bottom: 1rem;
}

.message {
  padding: 0.5rem 0.75rem;
  border-radius: 6px;
  background: #8882;
}

.message.user {
  align-self: flex-end;
}

.hint {
  color: #888;
}

.error {
  color: #e5484d;
}

.input-row {
  display: flex;
  gap: 0.5rem;
}

.input-row input {
  flex: 1;
  padding: 0.5rem 0.75rem;
}
</style>
