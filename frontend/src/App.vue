<script setup lang="ts">
import { RouterView } from 'vue-router'
import NavSidebar from './components/NavSidebar.vue'
</script>

<template>
  <div class="app-shell">
    <NavSidebar />
    <main class="app-content">
      <RouterView />
    </main>
  </div>
</template>

<style scoped>
.app-shell {
  display: flex;
  height: 100vh;
  height: 100dvh;
  min-height: 0;
  overflow: hidden;
  background: var(--color-bg);
}

.app-content {
  flex: 1;
  min-width: 0;
  min-height: 0;
  overflow-y: auto;
  margin: 10px 10px 10px 0;
  border: 1px solid rgba(190, 203, 209, 0.72);
  border-radius: 16px;
  background: var(--color-surface);
  box-shadow: 0 1px 0 rgba(255, 255, 255, 0.9) inset;
}

@media (max-width: 760px) {
  .app-shell {
    flex-direction: column;
  }

  .app-content {
    order: 1;
    margin: 6px 6px 0;
    border-radius: 12px 12px 0 0;
  }
}
</style>
