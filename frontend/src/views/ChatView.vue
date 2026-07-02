<script setup lang="ts">
import { ref } from 'vue'
import { sendChatMessage } from '../api/chat'
import type { ChatMessage } from '../types/chat'

const messages = ref<ChatMessage[]>([])
const input = ref('')
const sending = ref(false)
const error = ref<string | null>(null)

async function send() {
  const text = input.value.trim()
  if (!text || sending.value) return

  messages.value.push({ role: 'user', content: text })
  input.value = ''
  sending.value = true
  error.value = null

  try {
    const reply = await sendChatMessage(text)
    messages.value.push({ role: 'assistant', content: reply })
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    sending.value = false
  }
}
</script>

<template>
  <section class="chat-view">
    <h1>运维助手</h1>

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
      <p v-if="sending" class="hint">思考中…</p>
      <p v-if="error" class="error">{{ error }}</p>
    </div>

    <form class="input-row" @submit.prevent="send">
      <input v-model="input" placeholder="输入消息…" :disabled="sending" />
      <button type="submit" :disabled="sending">发送</button>
    </form>
  </section>
</template>

<style scoped>
.chat-view {
  max-width: 720px;
  margin: 2rem auto 0;
  padding: 0 2rem 2rem;
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
