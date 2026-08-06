<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { RouterLink, useRoute } from 'vue-router'

const route = useRoute()
const lastChatPath = ref(localStorage.getItem('aioms:last-chat-path') || '/chat')

watch(
  () => route.fullPath,
  (path) => {
    if (path === '/chat' || path.startsWith('/chat/')) {
      lastChatPath.value = path
      localStorage.setItem('aioms:last-chat-path', path)
    }
  },
  { immediate: true },
)

const items = computed(() => [
  { to: lastChatPath.value, label: '对话', icon: '💬' },
  { to: '/incidents', label: '事件中心', icon: '🚨' },
  { to: '/alert-rules', label: '预警规则', icon: '🔔' },
  { to: '/services', label: '服务目录', icon: '🧩' },
  { to: '/servers', label: '服务器', icon: '🖥️' },
])
</script>

<template>
  <nav class="nav-sidebar">
    <div class="brand">AIOMS</div>
    <ul>
      <li v-for="item in items" :key="item.to">
        <RouterLink :to="item.to" class="nav-item">
          <span class="icon">{{ item.icon }}</span>
          <span>{{ item.label }}</span>
        </RouterLink>
      </li>
    </ul>
  </nav>
</template>

<style scoped>
.nav-sidebar {
  width: 200px;
  flex-shrink: 0;
  height: 100%;
  overflow-y: auto;
  background: var(--color-surface);
  border-right: 1px solid var(--color-border);
  padding: 0 0.75rem 1.25rem;
  display: flex;
  flex-direction: column;
  gap: 0;
}

.brand {
  display: flex;
  flex: 0 0 64px;
  align-items: center;
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

.nav-item {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  padding: 0.55rem 0.75rem;
  border-radius: var(--radius);
  color: var(--color-text-muted);
  font-size: 0.95rem;
  text-decoration: none;
}

.nav-item:hover {
  background: var(--color-bg);
  color: var(--color-text);
}

.nav-item.router-link-active {
  background: var(--color-interactive-bg);
  color: var(--color-text);
}

.icon {
  font-size: 1rem;
}
</style>
