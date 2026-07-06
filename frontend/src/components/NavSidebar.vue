<script setup lang="ts">
export type NavTab = 'chat' | 'servers'

defineProps<{
  modelValue: NavTab
}>()

const emit = defineEmits<{
  'update:modelValue': [tab: NavTab]
}>()

const items: { key: NavTab; label: string; icon: string }[] = [
  { key: 'chat', label: '对话', icon: '💬' },
  { key: 'servers', label: '服务器', icon: '🖥️' },
]
</script>

<template>
  <nav class="nav-sidebar">
    <div class="brand">AIOMS</div>
    <ul>
      <li
        v-for="item in items"
        :key="item.key"
        :class="{ active: modelValue === item.key }"
        @click="emit('update:modelValue', item.key)"
      >
        <span class="icon">{{ item.icon }}</span>
        <span>{{ item.label }}</span>
      </li>
    </ul>
  </nav>
</template>

<style scoped>
.nav-sidebar {
  width: 200px;
  flex-shrink: 0;
  height: 100vh;
  background: var(--color-surface);
  border-right: 1px solid var(--color-border);
  padding: 1.25rem 0.75rem;
  display: flex;
  flex-direction: column;
  gap: 1.5rem;
}

.brand {
  font-weight: 600;
  font-size: 1.1rem;
  padding: 0 0.5rem;
  letter-spacing: 0.02em;
}

ul {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}

li {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  padding: 0.55rem 0.75rem;
  border-radius: var(--radius);
  color: var(--color-text-muted);
  font-size: 0.95rem;
}

li:hover {
  background: var(--color-bg);
  color: var(--color-text);
}

li.active {
  background: var(--color-accent);
  color: var(--color-accent-contrast);
}

.icon {
  font-size: 1rem;
}
</style>
