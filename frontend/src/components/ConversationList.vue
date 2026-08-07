<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { deleteConversation, fetchConversations } from '../api/conversations'
import type { ConversationSummary } from '../types/chat'

defineProps<{
  selectedConversationId: string | null
}>()

const emit = defineEmits<{
  select: [conversationId: string]
  // 删完某个对话后通知父组件——如果删的正好是当前打开的那个，父组件要清空聊天区
  deleted: [conversationId: string]
}>()

const conversations = ref<ConversationSummary[]>([])
const loading = ref(true)

async function refresh() {
  loading.value = true
  try {
    conversations.value = await fetchConversations()
  } finally {
    loading.value = false
  }
}

async function remove(c: ConversationSummary) {
  if (!confirm(`删除对话「${c.title}」？此操作不可恢复。`)) return
  try {
    await deleteConversation(c.id)
    emit('deleted', c.id)
    await refresh()
  } catch (e) {
    alert(e instanceof Error ? e.message : String(e))
  }
}

defineExpose({ refresh })

onMounted(refresh)
</script>

<template>
  <aside class="conversation-list">
    <h2>历史对话</h2>
    <p v-if="loading" class="hint">加载中…</p>
    <p v-else-if="conversations.length === 0" class="hint">还没有对话</p>
    <ul v-else>
      <li
        v-for="c in conversations"
        :key="c.id"
        :class="{ active: c.id === selectedConversationId }"
        @click="emit('select', c.id)"
      >
        <span class="title">{{ c.title }}</span>
        <button type="button" class="del-btn" title="删除对话" @click.stop="remove(c)">×</button>
      </li>
    </ul>
  </aside>
</template>

<style scoped>
.conversation-list {
  width: 242px;
  flex-shrink: 0;
  height: 100%;
  border-right: 1px solid var(--color-border);
  padding: 1.45rem 0.8rem 1rem 1rem;
  overflow-y: auto;
  background: #f1f5f6;
}

h2 {
  padding: 0 0.55rem;
  font-family: var(--font-data);
  font-size: 0.66rem;
  font-weight: 600;
  letter-spacing: 0.1em;
  color: var(--color-text-muted);
  margin: 0 0 0.75rem;
}

.hint {
  color: var(--color-text-muted);
  font-size: 0.9rem;
}

ul {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
}

li {
  display: flex;
  align-items: center;
  gap: 0.3rem;
  padding: 0.5rem 0.6rem;
  min-height: 40px;
  border: 1px solid transparent;
  border-radius: 8px;
  cursor: pointer;
  font-size: 0.9rem;
}

.title {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.del-btn {
  flex-shrink: 0;
  border: none;
  background: transparent;
  color: inherit;
  cursor: pointer;
  font-size: 1.1rem;
  line-height: 1;
  padding: 0 0.25rem;
  border-radius: 4px;
  opacity: 0; /* 平时隐藏，悬停整条时才露出来 */
}

li:hover {
  background: var(--color-bg);
}

li:hover .del-btn {
  opacity: 0.55;
}

.del-btn:hover {
  opacity: 1;
  background: rgba(0, 0, 0, 0.08);
}

li.active {
  border-color: #c6dfe3;
  background: var(--color-interactive-bg);
  color: var(--color-text);
  font-weight: 500;
}

/* 选中项是深色底，删除按钮悬停时给个浅色底而不是黑色 */
li.active:hover .del-btn:hover {
  background: rgba(255, 255, 255, 0.25);
}

@media (max-width: 960px) {
  .conversation-list {
    width: 190px;
  }
}

@media (max-width: 640px) {
  .conversation-list {
    display: none;
  }
}
</style>
