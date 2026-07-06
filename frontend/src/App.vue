<script setup lang="ts">
import { ref } from 'vue'
import NavSidebar, { type NavTab } from './components/NavSidebar.vue'
import ChatView from './views/ChatView.vue'
import ServersView from './views/ServersView.vue'

const activeTab = ref<NavTab>('chat')
</script>

<template>
  <div class="app-shell">
    <NavSidebar v-model="activeTab" />
    <main class="app-content">
      <ChatView v-if="activeTab === 'chat'" />
      <ServersView v-else />
    </main>
  </div>
</template>

<style scoped>
.app-shell {
  display: flex;
  min-height: 100vh;
}

.app-content {
  flex: 1;
  min-width: 0;
  overflow-y: auto;
}
</style>
