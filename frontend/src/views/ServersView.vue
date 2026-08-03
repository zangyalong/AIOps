<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import {
  createServer,
  deleteServer,
  fetchServers,
  testServerConnection,
  updateServer,
} from '../api/servers'
import type { Server, ServerCreateInput, ServerStatus } from '../types/server'

const servers = ref<Server[]>([])
const loading = ref(true)
const error = ref<string | null>(null)

const showModal = ref(false)
const editingServer = ref<Server | null>(null)
const saving = ref(false)
const formError = ref<string | null>(null)
const form = reactive<ServerCreateInput>({
  name: '',
  ip_address: '',
  description: '',
  ssh_port: null,
  ssh_user: '',
  ssh_key_filename: '',
  deploy_ssh_user: '',
  deploy_ssh_key_filename: '',
})

// 'testing' 是纯前端的临时态（测试请求还在飞的时候显示），后端不会返回这个
type DisplayStatus = ServerStatus | 'testing'
// 每台服务器本次会话里点"测试连接"后的即时结果（含 testing 和刚测出来的 detail）；
// 页面一刷新这个表就空了，届时状态回落到库里持久化的 server.status
const liveResults = reactive<Record<string, { status: DisplayStatus; detail: string }>>({})

async function loadServers() {
  loading.value = true
  error.value = null
  try {
    servers.value = await fetchServers()
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    loading.value = false
  }
}

onMounted(loadServers)

function resetForm() {
  form.name = ''
  form.ip_address = ''
  form.description = ''
  form.ssh_port = null
  form.ssh_user = ''
  form.ssh_key_filename = ''
  form.deploy_ssh_user = ''
  form.deploy_ssh_key_filename = ''
  formError.value = null
}

function openCreateModal() {
  resetForm()
  editingServer.value = null
  showModal.value = true
}

function openEditModal(server: Server) {
  form.name = server.name
  form.ip_address = server.ip_address
  form.description = server.description ?? ''
  form.ssh_port = server.ssh_port
  form.ssh_user = server.ssh_user ?? ''
  form.ssh_key_filename = server.ssh_key_filename ?? ''
  form.deploy_ssh_user = server.deploy_ssh_user ?? ''
  form.deploy_ssh_key_filename = server.deploy_ssh_key_filename ?? ''
  formError.value = null
  editingServer.value = server
  showModal.value = true
}

function closeModal() {
  showModal.value = false
  editingServer.value = null
}

async function submitForm() {
  if (!form.name.trim() || !form.ip_address.trim()) {
    formError.value = '名称和 IP 地址是必填的'
    return
  }
  saving.value = true
  formError.value = null
  try {
    const payload: ServerCreateInput = {
      name: form.name.trim(),
      ip_address: form.ip_address.trim(),
      description: form.description?.trim() || null,
      ssh_port: form.ssh_port || null,
      ssh_user: form.ssh_user?.trim() || null,
      ssh_key_filename: form.ssh_key_filename?.trim() || null,
      deploy_ssh_user: form.deploy_ssh_user?.trim() || null,
      deploy_ssh_key_filename: form.deploy_ssh_key_filename?.trim() || null,
    }
    if (editingServer.value) {
      await updateServer(editingServer.value.id, payload)
    } else {
      await createServer(payload)
    }
    closeModal()
    await loadServers()
  } catch (e) {
    formError.value = e instanceof Error ? e.message : String(e)
  } finally {
    saving.value = false
  }
}

async function remove(server: Server) {
  if (!confirm(`确定删除 ${server.name}？`)) return
  try {
    await deleteServer(server.id)
    delete liveResults[server.id]
    await loadServers()
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  }
}

async function testConnection(server: Server) {
  liveResults[server.id] = { status: 'testing', detail: '' }
  try {
    const result = await testServerConnection(server.id)
    liveResults[server.id] = result
    // 后端已经把新状态写回库了，本地这条也同步一下，省得重新拉整张列表
    server.status = result.status
  } catch (e) {
    liveResults[server.id] = {
      status: 'unreachable',
      detail: e instanceof Error ? e.message : String(e),
    }
  }
}

// 展示状态：本次会话点过测试就用即时结果（含 testing / 真实 detail），否则用库里持久化的 status
function displayState(server: Server): { status: DisplayStatus; detail: string } {
  return liveResults[server.id] ?? { status: server.status, detail: '' }
}

const statusLabel: Record<DisplayStatus, string> = {
  active: '可连接',
  unreachable: '连接失败',
  unconfigured: '未配置',
  unknown: '未测试',
  testing: '测试中…',
}

const sortedServers = computed(() =>
  [...servers.value]
    .sort((a, b) => a.name.localeCompare(b.name))
    .map((server) => ({ server, state: displayState(server) })),
)
</script>

<template>
  <div class="servers-view">
    <header class="view-header">
      <div>
        <h1>服务器</h1>
        <p class="subtitle">管理被 AIOMS 纳管的服务器，配置 SSH 后可测试连通性</p>
      </div>
      <button type="button" class="btn-primary" @click="openCreateModal">+ 新增服务器</button>
    </header>

    <p v-if="loading" class="hint">加载中…</p>
    <p v-else-if="error" class="error">{{ error }}</p>
    <div v-else-if="sortedServers.length === 0" class="empty">
      <p>还没有服务器</p>
      <button type="button" class="btn-primary" @click="openCreateModal">新增第一台</button>
    </div>

    <ul v-else class="server-grid">
      <li v-for="{ server, state } in sortedServers" :key="server.id" class="server-card">
        <div class="card-top">
          <span class="status-dot" :class="`status-${state.status}`" :title="statusLabel[state.status]" />
          <h2>{{ server.name }}</h2>
          <span class="status-badge" :class="`status-${state.status}`">{{ statusLabel[state.status] }}</span>
        </div>
        <dl class="meta">
          <div><dt>地址</dt><dd>{{ server.ip_address }}<template v-if="server.ssh_port">:{{ server.ssh_port }}</template></dd></div>
          <div v-if="server.ssh_user"><dt>SSH</dt><dd>{{ server.ssh_user }}</dd></div>
          <div><dt>描述</dt><dd>{{ server.description || '无描述' }}</dd></div>
        </dl>
        <p v-if="state.detail" class="detail">{{ state.detail }}</p>
        <div class="card-actions">
          <button type="button" class="btn-ghost" @click="testConnection(server)" :disabled="state.status === 'testing'">
            {{ state.status === 'testing' ? '测试中…' : '测试连接' }}
          </button>
          <button type="button" class="btn-ghost" @click="openEditModal(server)">编辑</button>
          <button type="button" class="btn-ghost danger" @click="remove(server)">删除</button>
        </div>
      </li>
    </ul>

    <!-- 新增和编辑共用同一个表单，编辑时会预填当前配置 -->
    <div v-if="showModal" class="modal-backdrop" @click.self="closeModal">
      <div class="modal" role="dialog" aria-modal="true">
        <div class="modal-header">
          <h2>{{ editingServer ? '编辑服务器' : '新增服务器' }}</h2>
          <button type="button" class="close-btn" @click="closeModal" aria-label="关闭">×</button>
        </div>
        <form class="modal-body" @submit.prevent="submitForm">
          <div class="form-row">
            <label>名称 *<input v-model="form.name" placeholder="比如 web-01" /></label>
            <label>IP 地址 *<input v-model="form.ip_address" placeholder="比如 10.0.1.11" /></label>
          </div>
          <label>描述<input v-model="form.description" placeholder="可选" /></label>

          <div class="ssh-group">
            <p class="group-title">日常只读 SSH</p>
            <p class="group-hint">可选。填全后用于测试连接、服务发现和只读诊断。</p>
            <div class="form-row">
              <label>端口<input v-model.number="form.ssh_port" type="number" placeholder="22" /></label>
              <label>用户名<input v-model="form.ssh_user" placeholder="比如 opsuser" /></label>
            </div>
            <label>私钥文件名<input v-model="form.ssh_key_filename" placeholder="ssh/ 目录下的文件名，比如 aioms-test" /></label>
          </div>

          <div class="ssh-group">
            <p class="group-title">审批后部署 SSH</p>
            <p class="group-hint">可选。只供经过审批的配置写 Tool 使用，不会回退到日常只读密钥。</p>
            <label>部署用户名<input v-model="form.deploy_ssh_user" placeholder="比如 aioms-deploy" /></label>
            <label>部署私钥文件名<input v-model="form.deploy_ssh_key_filename" placeholder="比如 web01-aioms-deploy" /></label>
          </div>

          <p v-if="formError" class="error">{{ formError }}</p>
          <div class="modal-actions">
            <button type="button" class="btn-ghost" @click="closeModal">取消</button>
            <button type="submit" class="btn-primary" :disabled="saving">
              {{ saving ? '保存中…' : '保存' }}
            </button>
          </div>
        </form>
      </div>
    </div>
  </div>
</template>

<style scoped>
.servers-view {
  max-width: 1000px;
  margin: 0 auto;
  padding: 2rem;
}

.view-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 1rem;
  margin-bottom: 1.75rem;
}

h1 {
  font-size: 1.5rem;
  margin: 0;
}

.subtitle {
  color: var(--color-text-muted);
  font-size: 0.9rem;
  margin: 0.25rem 0 0;
}

.btn-primary {
  background: var(--color-accent);
  color: var(--color-accent-contrast);
  border: none;
  border-radius: var(--radius-sm);
  padding: 0.55rem 1.1rem;
  font-size: 0.9rem;
  font-weight: 500;
  white-space: nowrap;
  transition: background 0.15s;
}

.btn-primary:hover {
  background: var(--color-accent-hover);
}

.btn-primary:disabled {
  opacity: 0.6;
  cursor: default;
}

.btn-ghost {
  flex: 1;
  padding: 0.5rem 0.7rem;
  border-radius: var(--radius-sm);
  border: 1px solid var(--color-border);
  background: var(--color-surface);
  color: var(--color-text);
  font-size: 0.85rem;
  transition: background 0.15s, border-color 0.15s;
}

.btn-ghost:hover {
  background: var(--color-bg);
}

.btn-ghost:disabled {
  opacity: 0.6;
  cursor: default;
}

.btn-ghost.danger {
  color: var(--color-status-unreachable);
}

.btn-ghost.danger:hover {
  border-color: var(--color-status-unreachable);
}

.server-grid {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 1rem;
}

.server-card {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius);
  padding: 1.1rem 1.2rem;
  box-shadow: var(--shadow-sm);
  transition: box-shadow 0.15s;
}

.server-card:hover {
  box-shadow: var(--shadow-md);
}

.card-top {
  display: flex;
  align-items: center;
  gap: 0.55rem;
}

.card-top h2 {
  font-size: 1.02rem;
  margin: 0;
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.status-dot {
  width: 9px;
  height: 9px;
  border-radius: 50%;
  flex-shrink: 0;
  background: var(--color-status-untested);
}

.status-badge {
  font-size: 0.72rem;
  padding: 0.15rem 0.5rem;
  border-radius: 999px;
  background: var(--color-bg);
  color: var(--color-text-muted);
  flex-shrink: 0;
}

.status-active,
.status-dot.status-active {
  --s: var(--color-status-active);
}
.status-unreachable,
.status-dot.status-unreachable {
  --s: var(--color-status-unreachable);
}
.status-unconfigured,
.status-dot.status-unconfigured {
  --s: var(--color-status-unconfigured);
}

.status-dot.status-active,
.status-dot.status-unreachable,
.status-dot.status-unconfigured {
  background: var(--s);
}

.status-badge.status-active,
.status-badge.status-unreachable,
.status-badge.status-unconfigured {
  background: color-mix(in srgb, var(--s) 14%, transparent);
  color: var(--s);
}

.status-dot.status-testing {
  animation: pulse 1s ease-in-out infinite;
}

@keyframes pulse {
  50% {
    opacity: 0.3;
  }
}

.meta {
  margin: 0.85rem 0 0;
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
}

.meta > div {
  display: flex;
  gap: 0.5rem;
  font-size: 0.85rem;
}

.meta dt {
  color: var(--color-text-muted);
  width: 2.6rem;
  flex-shrink: 0;
}

.meta dd {
  margin: 0;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.detail {
  margin: 0.7rem 0 0;
  font-size: 0.78rem;
  color: var(--color-text-muted);
  background: var(--color-bg);
  border-radius: var(--radius-sm);
  padding: 0.4rem 0.55rem;
  overflow-wrap: break-word;
}

.card-actions {
  display: flex;
  gap: 0.5rem;
  margin-top: 1rem;
}

.hint {
  color: var(--color-text-muted);
}

.error {
  color: var(--color-status-unreachable);
  font-size: 0.85rem;
  margin: 0;
}

.empty {
  text-align: center;
  padding: 3rem 1rem;
  color: var(--color-text-muted);
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 1rem;
}

.empty p {
  margin: 0;
}

/* 弹窗 */
.modal-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(24, 24, 27, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 1rem;
  z-index: 50;
}

.modal {
  background: var(--color-surface);
  border-radius: var(--radius);
  box-shadow: var(--shadow-lg);
  width: 100%;
  max-width: 480px;
  max-height: 90vh;
  overflow-y: auto;
}

.modal-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 1.1rem 1.4rem;
  border-bottom: 1px solid var(--color-border);
}

.modal-header h2 {
  font-size: 1.1rem;
  margin: 0;
}

.close-btn {
  border: none;
  background: transparent;
  font-size: 1.5rem;
  line-height: 1;
  color: var(--color-text-muted);
  padding: 0 0.25rem;
}

.close-btn:hover {
  color: var(--color-text);
}

.modal-body {
  padding: 1.4rem;
  display: flex;
  flex-direction: column;
  gap: 0.9rem;
}

.form-row {
  display: flex;
  gap: 0.8rem;
}

.form-row label {
  flex: 1;
}

label {
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
  font-size: 0.82rem;
  color: var(--color-text-muted);
}

.ssh-group {
  border-top: 1px solid var(--color-border);
  padding-top: 1rem;
  display: flex;
  flex-direction: column;
  gap: 0.7rem;
}

.group-title {
  margin: 0;
  font-size: 0.9rem;
  font-weight: 500;
  color: var(--color-text);
}

.group-hint {
  margin: 0;
  font-size: 0.78rem;
  color: var(--color-text-muted);
}

.modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 0.6rem;
  margin-top: 0.4rem;
}

.modal-actions .btn-ghost {
  flex: 0 0 auto;
  padding: 0.55rem 1.1rem;
}
</style>
