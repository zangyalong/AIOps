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
  min-height: 100vh;
}

.app-content {
  flex: 1;
  min-width: 0;
  overflow-y: auto;
}
</style>
