<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { fetchConversations } from '../api/conversations'
import type { ConversationSummary } from '../types/chat'

defineProps<{
  selectedConversationId: string | null
}>()

const emit = defineEmits<{
  select: [conversationId: string]
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
        {{ c.title }}
      </li>
    </ul>
  </aside>
</template>

<style scoped>
.conversation-list {
  width: 200px;
  flex-shrink: 0;
  border-right: 1px solid var(--color-border);
  padding-right: 1rem;
  overflow-y: auto;
}

h2 {
  font-size: 0.85rem;
  font-weight: 500;
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
  padding: 0.5rem 0.6rem;
  border-radius: var(--radius);
  cursor: pointer;
  font-size: 0.9rem;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

li:hover {
  background: var(--color-bg);
}

li.active {
  background: var(--color-accent);
  color: var(--color-accent-contrast);
  font-weight: 500;
}
</style>
