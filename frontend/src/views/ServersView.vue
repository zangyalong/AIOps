<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { fetchServers } from '../api/servers'
import type { Server } from '../types/server'

const servers = ref<Server[]>([])
const loading = ref(true)
const error = ref<string | null>(null)

onMounted(async () => {
  try {
    servers.value = await fetchServers()
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <main class="servers-view">
    <h1>服务器列表</h1>

    <p v-if="loading">加载中…</p>
    <p v-else-if="error" class="error">{{ error }}</p>
    <table v-else>
      <thead>
        <tr>
          <th>ID</th>
          <th>名称</th>
          <th>IP 地址</th>
          <th>描述</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="server in servers" :key="server.id">
          <td>{{ server.id }}</td>
          <td>{{ server.name }}</td>
          <td>{{ server.ip_address }}</td>
          <td>{{ server.description ?? '-' }}</td>
        </tr>
      </tbody>
    </table>
  </main>
</template>

<style scoped>
.servers-view {
  max-width: 720px;
  margin: 0 auto;
  padding: 2rem;
}

table {
  width: 100%;
  border-collapse: collapse;
}

th,
td {
  border: 1px solid #8888;
  padding: 0.5rem 0.75rem;
  text-align: left;
}

.error {
  color: #e5484d;
}
</style>
