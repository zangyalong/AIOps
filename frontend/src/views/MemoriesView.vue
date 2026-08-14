<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { createMemory, deleteMemory, fetchMemories } from '../api/memories'
import { fetchServers } from '../api/servers'
import type {
  MemoryCategory,
  MemoryRecord,
  MemoryScopeType,
} from '../types/memory'
import type { Server } from '../types/server'

const categoryOptions: Array<{
  value: MemoryCategory
  label: string
  hint: string
}> = [
  { value: 'preference', label: '偏好', hint: '回答方式与工作习惯' },
  { value: 'environment', label: '环境', hint: '稳定身份、路径与拓扑' },
  { value: 'decision', label: '决策', hint: '已经确认的长期选择' },
  { value: 'lesson', label: '经验', hint: '可复用的排障教训' },
]

const servers = ref<Server[]>([])
const memories = ref<MemoryRecord[]>([])
const scopeType = ref<MemoryScopeType>('global')
const selectedServerId = ref<string | null>(null)
const query = ref('')
const category = ref<MemoryCategory>('preference')
const content = ref('')
const loading = ref(true)
const saving = ref(false)
const deletingId = ref<string | null>(null)
const confirmingDeleteId = ref<string | null>(null)
const error = ref<string | null>(null)
const formError = ref<string | null>(null)
const notice = ref<string | null>(null)
let requestSequence = 0

const currentServer = computed(
  () => servers.value.find((server) => server.id === selectedServerId.value) ?? null,
)

const availableCategoryOptions = computed(() =>
  scopeType.value === 'server'
    ? categoryOptions.filter((item) => item.value !== 'preference')
    : categoryOptions,
)

const scopeKey = computed(() =>
  scopeType.value === 'global'
    ? 'global'
    : `server:${selectedServerId.value ?? 'none'}`,
)

const filteredMemories = computed(() => {
  const normalized = query.value.trim().toLocaleLowerCase()
  if (!normalized) return memories.value
  return memories.value.filter((memory) =>
    `${memory.content} ${memory.memory_key} ${memory.category}`
      .toLocaleLowerCase()
      .includes(normalized),
  )
})

const scopeTitle = computed(() =>
  scopeType.value === 'global'
    ? '全局长期记忆'
    : currentServer.value?.name ?? '服务器长期记忆',
)

const scopeDescription = computed(() =>
  scopeType.value === 'global'
    ? '在所有会话和服务器之间都可能被检索。'
    : currentServer.value
      ? `只在本轮可信目标是 ${currentServer.value.name} 时参与检索。`
      : '先选择一台已纳管服务器。',
)

function categoryLabel(value: MemoryCategory): string {
  return categoryOptions.find((item) => item.value === value)?.label ?? value
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(value))
}

function selectScope(nextScope: MemoryScopeType) {
  scopeType.value = nextScope
  notice.value = null
  confirmingDeleteId.value = null
  if (nextScope === 'server') {
    selectedServerId.value ??= servers.value[0]?.id ?? null
    if (category.value === 'preference') category.value = 'environment'
  }
}

async function loadServers() {
  try {
    servers.value = await fetchServers()
    if (
      selectedServerId.value &&
      !servers.value.some((server) => server.id === selectedServerId.value)
    ) {
      selectedServerId.value = null
    }
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : String(reason)
  }
}

async function loadMemories() {
  const sequence = ++requestSequence
  if (scopeType.value === 'server' && !selectedServerId.value) {
    memories.value = []
    loading.value = false
    return
  }
  loading.value = true
  error.value = null
  try {
    const result = await fetchMemories(scopeType.value, selectedServerId.value)
    if (sequence === requestSequence) memories.value = result
  } catch (reason) {
    if (sequence === requestSequence) {
      error.value = reason instanceof Error ? reason.message : String(reason)
    }
  } finally {
    if (sequence === requestSequence) loading.value = false
  }
}

async function saveMemory() {
  const normalized = content.value.trim()
  if (!normalized) {
    formError.value = '写下需要长期记住的稳定信息。'
    return
  }
  if (scopeType.value === 'server' && !selectedServerId.value) {
    formError.value = '服务器记忆必须先选择一台服务器。'
    return
  }

  saving.value = true
  formError.value = null
  notice.value = null
  try {
    const created = await createMemory({
      scope_type: scopeType.value,
      server_id: scopeType.value === 'server' ? selectedServerId.value : null,
      category: category.value,
      content: normalized,
    })
    memories.value = [created, ...memories.value]
    content.value = ''
    notice.value = `已加入${scopeType.value === 'global' ? '全局' : currentServer.value?.name ?? '服务器'}记忆。`
  } catch (reason) {
    formError.value = reason instanceof Error ? reason.message : String(reason)
  } finally {
    saving.value = false
  }
}

async function forgetMemory(memory: MemoryRecord) {
  deletingId.value = memory.id
  error.value = null
  try {
    await deleteMemory(memory.id)
    memories.value = memories.value.filter((item) => item.id !== memory.id)
    confirmingDeleteId.value = null
    notice.value = '这条记忆已删除，后续对话不会再检索它。'
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : String(reason)
  } finally {
    deletingId.value = null
  }
}

watch(scopeKey, () => void loadMemories())

onMounted(async () => {
  await loadServers()
  await loadMemories()
})
</script>

<template>
  <div class="memories-view">
    <header class="memory-header">
      <div>
        <p class="eyebrow">AGENT CONTEXT LEDGER</p>
        <h1>MEMORY</h1>
        <p class="subtitle">查看和管理 Agent 会在后续对话中带回来的稳定信息</p>
      </div>
      <div class="memory-count" aria-live="polite">
        <strong>{{ memories.length }}</strong>
        <span>当前作用域</span>
      </div>
    </header>

    <section class="scope-route" aria-label="记忆检索顺序">
      <div class="scope-node active">
        <span class="node-mark">G</span>
        <span><strong>全局</strong><small>每轮始终检索</small></span>
      </div>
      <span class="route-line" aria-hidden="true"><i /></span>
      <div class="scope-node" :class="{ active: scopeType === 'server' && !!currentServer }">
        <span class="node-mark">S</span>
        <span>
          <strong>{{ currentServer?.name ?? '服务器' }}</strong>
          <small>{{ currentServer ? '目标唯一时追加' : '按目标条件追加' }}</small>
        </span>
      </div>
      <p>实时状态不进入长期记忆；涉及服务器当前情况时，Agent 仍会重新调查。</p>
    </section>

    <div class="memory-workbench">
      <aside class="memory-controls">
        <section class="scope-panel" aria-labelledby="scope-panel-title">
          <p class="panel-kicker">作用域</p>
          <h2 id="scope-panel-title">记在哪里</h2>
          <div class="scope-switch" role="group" aria-label="选择记忆作用域">
            <button
              type="button"
              :class="{ selected: scopeType === 'global' }"
              @click="selectScope('global')"
            >
              全局
            </button>
            <button
              type="button"
              :class="{ selected: scopeType === 'server' }"
              @click="selectScope('server')"
            >
              服务器
            </button>
          </div>

          <label v-if="scopeType === 'server'" class="server-field">
            <span>已纳管服务器</span>
            <select v-model="selectedServerId" :disabled="servers.length === 0">
              <option :value="null" disabled>
                {{ servers.length ? '选择服务器' : '还没有服务器' }}
              </option>
              <option v-for="server in servers" :key="server.id" :value="server.id">
                {{ server.name }} · {{ server.ip_address }}
              </option>
            </select>
          </label>

          <p class="scope-copy">{{ scopeDescription }}</p>
        </section>

        <form class="new-memory-panel" @submit.prevent="saveMemory">
          <div class="panel-heading">
            <div>
              <p class="panel-kicker">手动添加</p>
              <h2>新增记忆</h2>
            </div>
            <span>{{ content.length }}/1000</span>
          </div>

          <label>
            <span>类别</span>
            <select v-model="category">
              <option
                v-for="item in availableCategoryOptions"
                :key="item.value"
                :value="item.value"
              >
                {{ item.label }} · {{ item.hint }}
              </option>
            </select>
          </label>

          <label>
            <span>稳定信息</span>
            <textarea
              v-model="content"
              maxlength="1000"
              rows="5"
              placeholder="例如：回答先给结论，再列调查依据。"
            />
          </label>

          <p v-if="formError" class="form-feedback error" role="alert">{{ formError }}</p>
          <p v-else class="form-hint">凭据、瞬时指标和当前告警会被后端拒绝保存。</p>

          <button class="save-button" type="submit" :disabled="saving">
            {{ saving ? '正在保存…' : `保存到${scopeType === 'global' ? '全局' : '服务器'}` }}
          </button>
        </form>
      </aside>

      <main class="memory-ledger">
        <div class="ledger-heading">
          <div>
            <p class="panel-kicker">当前台账</p>
            <h2>{{ scopeTitle }}</h2>
          </div>
          <label class="memory-search">
            <span class="sr-only">搜索记忆</span>
            <input v-model="query" type="search" placeholder="搜索内容或类别" />
          </label>
        </div>

        <p v-if="notice" class="ledger-notice" role="status">{{ notice }}</p>
        <div v-if="error" class="ledger-feedback error" role="alert">
          <strong>无法读取记忆</strong>
          <span>{{ error }}</span>
          <button type="button" @click="loadMemories">重试</button>
        </div>
        <div v-else-if="loading" class="ledger-feedback" role="status">
          <strong>正在读取记忆</strong>
          <span>正在查询当前作用域的有效记录…</span>
        </div>
        <ol v-else-if="filteredMemories.length" class="memory-list">
          <li v-for="memory in filteredMemories" :key="memory.id" class="memory-row">
            <span class="memory-index" aria-hidden="true" />
            <div class="memory-body">
              <div class="memory-meta">
                <span :class="`category category-${memory.category}`">
                  {{ categoryLabel(memory.category) }}
                </span>
                <span>{{ memory.source_conversation_id ? '对话提炼' : '手动添加' }}</span>
                <time :datetime="memory.updated_at">更新 {{ formatDate(memory.updated_at) }}</time>
              </div>
              <p>{{ memory.content }}</p>
            </div>
            <div class="memory-actions">
              <template v-if="confirmingDeleteId === memory.id">
                <span>确定遗忘？</span>
                <button type="button" class="cancel-button" @click="confirmingDeleteId = null">
                  取消
                </button>
                <button
                  type="button"
                  class="forget-button confirm"
                  :disabled="deletingId === memory.id"
                  @click="forgetMemory(memory)"
                >
                  {{ deletingId === memory.id ? '删除中…' : '确定' }}
                </button>
              </template>
              <button
                v-else
                type="button"
                class="forget-button"
                @click="confirmingDeleteId = memory.id"
              >
                遗忘
              </button>
            </div>
          </li>
        </ol>
        <div v-else class="ledger-feedback empty">
          <strong>{{ memories.length ? '没有匹配的记忆' : '这个作用域还是空的' }}</strong>
          <span>
            {{ memories.length ? '换个关键词试试。' : '可以在左侧手动添加，或让 Agent 从后续对话中提炼。' }}
          </span>
        </div>

        <footer class="compression-note">
          <span class="compression-glyph" aria-hidden="true">≋</span>
          <div>
            <strong>会话历史会自动压缩，但不会删除原文</strong>
            <p>当单个会话过长时，旧完整轮次会滚入摘要；页面中的原始消息仍永久保留。</p>
          </div>
        </footer>
      </main>
    </div>
  </div>
</template>

<style scoped>
.memories-view {
  width: min(1180px, 100%);
  min-height: 100%;
  margin: 0 auto;
  padding: 42px clamp(20px, 4vw, 58px) 64px;
}

.memory-header {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 24px;
}

.eyebrow,
.panel-kicker {
  margin: 0 0 7px;
  color: var(--color-accent);
  font-family: var(--font-data);
  font-size: 0.66rem;
  font-weight: 700;
  letter-spacing: 0.14em;
}

h1,
h2,
p {
  margin-top: 0;
}

h1 {
  margin-bottom: 0;
  font-family: var(--font-display);
  font-size: clamp(2.1rem, 4vw, 2.8rem);
  line-height: 1;
  letter-spacing: 0.06em;
}

.subtitle {
  margin: 10px 0 0;
  color: var(--color-text-muted);
}

.memory-count {
  min-width: 104px;
  padding-left: 20px;
  text-align: right;
  border-left: 1px solid var(--color-border);
}

.memory-count strong {
  display: block;
  font-family: var(--font-data);
  font-size: 1.8rem;
  font-weight: 500;
  line-height: 1;
}

.memory-count span {
  color: var(--color-text-muted);
  font-size: 0.72rem;
}

.scope-route {
  display: grid;
  grid-template-columns: max-content minmax(40px, 120px) max-content 1fr;
  align-items: center;
  gap: 14px;
  margin: 28px 0 30px;
  padding: 14px 18px;
  border: 1px solid var(--color-border);
  border-radius: 11px;
  background: #f1f6f7;
  box-shadow: 0 1px 0 rgba(255, 255, 255, 0.95) inset;
}

.scope-node {
  display: flex;
  align-items: center;
  gap: 9px;
  color: #839199;
}

.node-mark {
  width: 30px;
  height: 30px;
  display: grid;
  place-items: center;
  border: 1px solid #bccbd0;
  border-radius: 50%;
  font-family: var(--font-data);
  font-size: 0.68rem;
  background: #ffffff;
}

.scope-node strong,
.scope-node small {
  display: block;
}

.scope-node strong {
  font-size: 0.82rem;
}

.scope-node small {
  margin-top: 1px;
  font-size: 0.67rem;
}

.scope-node.active {
  color: #126f7c;
}

.scope-node.active .node-mark {
  color: #ffffff;
  border-color: var(--color-accent);
  background: var(--color-accent);
  box-shadow: 0 0 0 4px rgba(22, 137, 154, 0.1);
}

.route-line {
  height: 1px;
  overflow: visible;
  background: #b8c8cd;
}

.route-line i {
  display: block;
  width: 34%;
  height: 3px;
  margin-top: -1px;
  background: var(--color-accent);
}

.scope-route > p {
  margin: 0 0 0 auto;
  color: var(--color-text-muted);
  font-size: 0.76rem;
  text-align: right;
}

.memory-workbench {
  display: grid;
  grid-template-columns: 292px minmax(0, 1fr);
  gap: 32px;
  align-items: start;
}

.memory-controls {
  display: grid;
  gap: 14px;
}

.scope-panel,
.new-memory-panel {
  padding: 20px;
  border: 1px solid var(--color-border);
  border-radius: 12px;
  background: var(--color-surface-strong);
  box-shadow: var(--shadow-sm);
}

h2 {
  margin-bottom: 0;
  font-size: 1.05rem;
}

.scope-switch {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 4px;
  margin-top: 16px;
  padding: 4px;
  border-radius: 9px;
  background: var(--color-surface-inset);
}

.scope-switch button {
  min-height: 36px;
  color: var(--color-text-muted);
  border: 0;
  border-radius: 7px;
  background: transparent;
}

.scope-switch button.selected {
  color: #0f6572;
  font-weight: 650;
  background: #ffffff;
  box-shadow: 0 1px 5px rgba(16, 39, 51, 0.1);
}

.server-field,
.new-memory-panel label {
  display: grid;
  gap: 6px;
  margin-top: 16px;
}

.server-field > span,
.new-memory-panel label > span {
  color: var(--color-text-muted);
  font-size: 0.73rem;
  font-weight: 650;
}

.scope-copy,
.form-hint,
.form-feedback {
  margin: 13px 0 0;
  color: var(--color-text-muted);
  font-size: 0.73rem;
  line-height: 1.5;
}

.panel-heading,
.ledger-heading {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 18px;
}

.panel-heading > span {
  color: #8a979d;
  font-family: var(--font-data);
  font-size: 0.64rem;
}

.new-memory-panel textarea {
  min-height: 116px;
}

.form-feedback.error,
.ledger-feedback.error {
  color: #a73f3c;
}

.save-button {
  width: 100%;
  min-height: 42px;
  margin-top: 15px;
  color: var(--color-accent-contrast);
  font-weight: 650;
  border: 1px solid var(--color-accent);
  border-radius: 8px;
  background: var(--color-accent);
}

.save-button:hover:not(:disabled) {
  background: var(--color-accent-hover);
}

.save-button:disabled {
  opacity: 0.58;
}

.memory-ledger {
  min-width: 0;
}

.ledger-heading {
  min-height: 53px;
  padding-bottom: 14px;
  border-bottom: 1px solid var(--color-border-strong);
}

.memory-search {
  width: min(260px, 45%);
}

.memory-search input {
  width: 100%;
  min-height: 38px;
  border-radius: 20px;
}

.ledger-notice {
  margin: 12px 0 0;
  padding: 9px 12px;
  color: #176d60;
  font-size: 0.76rem;
  border-left: 3px solid #43a58d;
  background: #ebf6f2;
}

.memory-list {
  list-style: none;
  margin: 0;
  padding: 0;
}

.memory-row {
  position: relative;
  display: grid;
  grid-template-columns: 12px minmax(0, 1fr) max-content;
  gap: 14px;
  align-items: start;
  padding: 19px 4px 19px 0;
  border-bottom: 1px solid var(--color-border);
}

.memory-index {
  width: 7px;
  height: 7px;
  margin-top: 9px;
  border: 1px solid #88a2ac;
  border-radius: 50%;
  background: #ffffff;
}

.memory-row::before {
  content: '';
  position: absolute;
  left: 3px;
  top: 0;
  bottom: 0;
  width: 1px;
  z-index: -1;
  background: #d5e0e3;
}

.memory-row:first-child::before {
  top: 27px;
}

.memory-row:last-child::before {
  bottom: calc(100% - 27px);
}

.memory-body {
  min-width: 0;
}

.memory-meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 7px 12px;
  color: #839097;
  font-family: var(--font-data);
  font-size: 0.63rem;
}

.category {
  padding: 2px 7px;
  color: #236f79;
  border: 1px solid #c4dde0;
  border-radius: 999px;
  background: #edf7f8;
}

.category-decision {
  color: #765f27;
  border-color: #e6d9b7;
  background: #faf6e9;
}

.category-lesson {
  color: #77506d;
  border-color: #e1cfdd;
  background: #f8f0f6;
}

.category-environment {
  color: #4d6c46;
  border-color: #ceddc9;
  background: #f1f7ef;
}

.memory-body > p {
  margin: 9px 0 0;
  font-size: 0.91rem;
  line-height: 1.7;
  overflow-wrap: anywhere;
}

.memory-actions {
  min-height: 31px;
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 6px;
  color: #8d6d6c;
  font-size: 0.7rem;
}

.forget-button,
.cancel-button,
.ledger-feedback button {
  min-height: 30px;
  padding: 0 9px;
  color: #687780;
  font-size: 0.72rem;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  background: #ffffff;
}

.forget-button:hover,
.forget-button.confirm {
  color: #a73f3c;
  border-color: #e0b9b7;
  background: #fff4f3;
}

.ledger-feedback {
  min-height: 180px;
  display: grid;
  place-content: center;
  justify-items: center;
  gap: 5px;
  color: var(--color-text-muted);
  text-align: center;
}

.ledger-feedback strong {
  color: var(--color-text);
}

.ledger-feedback button {
  margin-top: 8px;
}

.compression-note {
  display: flex;
  gap: 13px;
  margin-top: 24px;
  padding: 15px 17px;
  border: 1px dashed #bdcdd2;
  border-radius: 10px;
  background: #f5f8f9;
}

.compression-glyph {
  color: var(--color-accent);
  font-family: var(--font-data);
  font-size: 1.35rem;
  line-height: 1;
}

.compression-note strong {
  font-size: 0.78rem;
}

.compression-note p {
  margin: 3px 0 0;
  color: var(--color-text-muted);
  font-size: 0.72rem;
}

.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}

@media (max-width: 900px) {
  .scope-route {
    grid-template-columns: max-content 40px max-content;
  }

  .scope-route > p {
    grid-column: 1 / -1;
    margin-left: 0;
    text-align: left;
  }

  .memory-workbench {
    grid-template-columns: 1fr;
  }

  .memory-controls {
    grid-template-columns: minmax(0, 0.8fr) minmax(0, 1.2fr);
  }
}

@media (max-width: 620px) {
  .memories-view {
    padding: 28px 16px 48px;
  }

  .memory-header {
    align-items: flex-start;
  }

  .memory-count {
    min-width: 70px;
    padding-left: 12px;
  }

  .scope-route {
    grid-template-columns: 1fr;
  }

  .route-line {
    width: 1px;
    height: 20px;
    margin-left: 15px;
  }

  .route-line i {
    width: 3px;
    height: 35%;
    margin: 0 0 0 -1px;
  }

  .memory-controls {
    grid-template-columns: 1fr;
  }

  .ledger-heading {
    align-items: stretch;
    flex-direction: column;
  }

  .memory-search {
    width: 100%;
  }

  .memory-row {
    grid-template-columns: 12px minmax(0, 1fr);
  }

  .memory-actions {
    grid-column: 2;
    justify-content: flex-start;
  }
}
</style>
