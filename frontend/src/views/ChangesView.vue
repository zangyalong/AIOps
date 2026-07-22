<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { createChange, deleteChange, fetchChanges, updateChange } from '../api/changes'
import type { ChangeRecord, ChangeRecordInput, ChangeType } from '../types/change'

interface ChangeForm {
  service_name: string
  environment: string
  version: string
  commit_hash: string
  change_type: ChangeType
  occurred_at: string
  description: string
}

const records = ref<ChangeRecord[]>([])
const loading = ref(true)
const error = ref<string | null>(null)
const showModal = ref(false)
const saving = ref(false)
const formError = ref<string | null>(null)
const editingRecord = ref<ChangeRecord | null>(null)
const filters = reactive({ service: '', environment: '' })
const form = reactive<ChangeForm>({
  service_name: '',
  environment: 'prod',
  version: '',
  commit_hash: '',
  change_type: 'deployment',
  occurred_at: '',
  description: '',
})

const changeTypes: { value: ChangeType; label: string }[] = [
  { value: 'deployment', label: '发布' },
  { value: 'rollback', label: '回滚' },
  { value: 'scaling', label: '扩缩容' },
  { value: 'configuration', label: '配置变更' },
]

const typeLabels = Object.fromEntries(
  changeTypes.map((item) => [item.value, item.label]),
) as Record<ChangeType, string>

const filteredRecords = computed(() => {
  const service = filters.service.trim().toLowerCase()
  const environment = filters.environment.trim().toLowerCase()
  return records.value.filter(
    (record) =>
      (!service || record.service_name.toLowerCase().includes(service)) &&
      (!environment || record.environment.toLowerCase().includes(environment)),
  )
})

async function loadChanges() {
  loading.value = true
  error.value = null
  try {
    records.value = await fetchChanges()
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    loading.value = false
  }
}

onMounted(loadChanges)

function toDateTimeLocal(value: string | Date): string {
  const date = value instanceof Date ? value : new Date(value)
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60_000)
  return local.toISOString().slice(0, 16)
}

function resetForm() {
  editingRecord.value = null
  form.service_name = ''
  form.environment = 'prod'
  form.version = ''
  form.commit_hash = ''
  form.change_type = 'deployment'
  form.occurred_at = toDateTimeLocal(new Date())
  form.description = ''
  formError.value = null
}

function openCreate() {
  resetForm()
  showModal.value = true
}

function openEdit(record: ChangeRecord) {
  editingRecord.value = record
  form.service_name = record.service_name
  form.environment = record.environment
  form.version = record.version ?? ''
  form.commit_hash = record.commit_hash ?? ''
  form.change_type = record.change_type
  form.occurred_at = toDateTimeLocal(record.occurred_at)
  form.description = record.description ?? ''
  formError.value = null
  showModal.value = true
}

function closeModal() {
  if (!saving.value) showModal.value = false
}

async function submitForm() {
  if (!form.service_name.trim() || !form.environment.trim() || !form.occurred_at) {
    formError.value = '服务名、环境和发生时间是必填项'
    return
  }
  const occurredAt = new Date(form.occurred_at)
  if (Number.isNaN(occurredAt.getTime())) {
    formError.value = '发生时间格式不正确'
    return
  }

  const payload: ChangeRecordInput = {
    service_name: form.service_name.trim(),
    environment: form.environment.trim(),
    version: form.version.trim() || null,
    commit_hash: form.commit_hash.trim() || null,
    change_type: form.change_type,
    occurred_at: occurredAt.toISOString(),
    description: form.description.trim() || null,
  }

  saving.value = true
  formError.value = null
  try {
    const saved = editingRecord.value
      ? await updateChange(editingRecord.value.id, payload)
      : await createChange(payload)
    const index = records.value.findIndex((record) => record.id === saved.id)
    if (index === -1) records.value.unshift(saved)
    else records.value[index] = saved
    records.value.sort(
      (left, right) => Date.parse(right.occurred_at) - Date.parse(left.occurred_at),
    )
    showModal.value = false
  } catch (e) {
    formError.value = e instanceof Error ? e.message : String(e)
  } finally {
    saving.value = false
  }
}

async function removeRecord(record: ChangeRecord) {
  if (!confirm(`确定删除 ${record.service_name} 的这条${typeLabels[record.change_type]}记录？`)) {
    return
  }
  error.value = null
  try {
    await deleteChange(record.id)
    records.value = records.value.filter((item) => item.id !== record.id)
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  }
}

function formatTime(value: string): string {
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(new Date(value))
}
</script>

<template>
  <div class="changes-view">
    <header class="view-header">
      <div>
        <h1>变更记录</h1>
        <p class="subtitle">记录发布、回滚、扩缩容和配置变更，为事件诊断提供时间线证据</p>
      </div>
      <button type="button" class="btn-primary" @click="openCreate">+ 新增变更</button>
    </header>

    <div class="filter-bar">
      <label>
        <span>服务</span>
        <input v-model="filters.service" placeholder="筛选服务名" />
      </label>
      <label>
        <span>环境</span>
        <input v-model="filters.environment" placeholder="例如 prod" />
      </label>
      <button type="button" class="btn-ghost" :disabled="loading" @click="loadChanges">
        {{ loading ? '刷新中…' : '刷新' }}
      </button>
    </div>

    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <p v-if="loading && records.length === 0" class="hint">加载中…</p>
    <div v-else-if="filteredRecords.length === 0" class="empty">
      <p>{{ records.length === 0 ? '还没有变更记录' : '没有符合筛选条件的记录' }}</p>
      <span>发布或配置发生变化时，在这里留下可关联的事实记录。</span>
    </div>

    <div v-else class="change-list">
      <article v-for="record in filteredRecords" :key="record.id" class="change-card">
        <time :datetime="record.occurred_at">{{ formatTime(record.occurred_at) }}</time>
        <div class="change-main">
          <div class="change-heading">
            <span class="type-badge" :class="`type-${record.change_type}`">
              {{ typeLabels[record.change_type] }}
            </span>
            <h2>{{ record.service_name }}</h2>
            <span class="environment-badge">{{ record.environment }}</span>
          </div>
          <p class="identity">
            <span v-if="record.version">版本 {{ record.version }}</span>
            <code v-if="record.commit_hash">{{ record.commit_hash }}</code>
            <span v-if="!record.version && !record.commit_hash">未填写版本或 commit</span>
          </p>
          <p v-if="record.description" class="description">{{ record.description }}</p>
        </div>
        <div class="card-actions">
          <button type="button" class="btn-ghost" @click="openEdit(record)">编辑</button>
          <button type="button" class="btn-ghost danger" @click="removeRecord(record)">删除</button>
        </div>
      </article>
    </div>

    <div v-if="showModal" class="modal-backdrop" @click.self="closeModal">
      <div class="modal" role="dialog" aria-modal="true" aria-label="变更记录表单">
        <div class="modal-header">
          <h2>{{ editingRecord ? '编辑变更记录' : '新增变更记录' }}</h2>
          <button type="button" class="close-button" aria-label="关闭" @click="closeModal">×</button>
        </div>
        <form class="modal-body" @submit.prevent="submitForm">
          <div class="form-row">
            <label>服务名 *<input v-model="form.service_name" placeholder="例如 orders-api" /></label>
            <label>环境 *<input v-model="form.environment" placeholder="例如 prod" /></label>
          </div>
          <div class="form-row">
            <label>
              操作类型 *
              <select v-model="form.change_type">
                <option v-for="item in changeTypes" :key="item.value" :value="item.value">
                  {{ item.label }}
                </option>
              </select>
            </label>
            <label>发生时间 *<input v-model="form.occurred_at" type="datetime-local" /></label>
          </div>
          <div class="form-row">
            <label>版本号<input v-model="form.version" placeholder="例如 v2.8.1" /></label>
            <label>Commit<input v-model="form.commit_hash" placeholder="例如 a1b2c3d" /></label>
          </div>
          <label>操作说明<textarea v-model="form.description" rows="4" placeholder="说明本次变更的范围和目的" /></label>
          <p v-if="formError" class="error" role="alert">{{ formError }}</p>
          <div class="modal-actions">
            <button type="button" class="btn-ghost" :disabled="saving" @click="closeModal">取消</button>
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
.changes-view {
  max-width: 1100px;
  margin: 0 auto;
  padding: 2rem;
}

.view-header,
.filter-bar,
.change-heading,
.card-actions,
.modal-header,
.modal-actions {
  display: flex;
  align-items: center;
}

.view-header {
  align-items: flex-start;
  justify-content: space-between;
  gap: 1rem;
  margin-bottom: 1.35rem;
}

h1,
h2,
p {
  margin-top: 0;
}

h1 {
  margin-bottom: 0;
  font-size: 1.5rem;
}

.subtitle {
  margin: 0.25rem 0 0;
  color: var(--color-text-muted);
  font-size: 0.9rem;
}

.btn-primary,
.btn-ghost {
  border-radius: var(--radius-sm);
  padding: 0.5rem 0.85rem;
  font-size: 0.85rem;
  white-space: nowrap;
}

.btn-primary {
  border: 1px solid var(--color-accent);
  color: var(--color-accent-contrast);
  background: var(--color-accent);
}

.btn-primary:hover {
  background: var(--color-accent-hover);
}

.btn-ghost {
  border: 1px solid var(--color-border);
  color: var(--color-text);
  background: var(--color-surface);
}

.btn-ghost:hover {
  background: var(--color-bg);
}

.btn-ghost.danger {
  color: #b42318;
}

button:disabled {
  cursor: default;
  opacity: 0.55;
}

.filter-bar {
  align-items: flex-end;
  gap: 0.75rem;
  margin-bottom: 1.25rem;
  padding-bottom: 1rem;
  border-bottom: 1px solid var(--color-border);
}

.filter-bar label,
.modal-body label {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 0.3rem;
  color: var(--color-text-muted);
  font-size: 0.8rem;
}

.filter-bar input {
  min-width: 0;
}

.error {
  padding: 0.65rem 0.8rem;
  border-radius: var(--radius-sm);
  color: #b42318;
  background: #fef3f2;
}

.hint,
.empty {
  color: var(--color-text-muted);
}

.empty {
  padding: 4rem 1rem;
  border: 1px dashed var(--color-border);
  border-radius: var(--radius);
  text-align: center;
}

.empty p {
  margin-bottom: 0.25rem;
  color: var(--color-text);
  font-weight: 600;
}

.empty span {
  font-size: 0.85rem;
}

.change-list {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.change-card {
  display: grid;
  grid-template-columns: 145px minmax(0, 1fr) auto;
  align-items: center;
  gap: 1rem;
  padding: 1rem 1.1rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius);
  background: var(--color-surface);
  box-shadow: var(--shadow-sm);
}

.change-card time {
  color: var(--color-text-muted);
  font-size: 0.78rem;
}

.change-main {
  min-width: 0;
}

.change-heading {
  flex-wrap: wrap;
  gap: 0.45rem;
}

.change-heading h2 {
  overflow: hidden;
  margin: 0;
  font-size: 1rem;
  text-overflow: ellipsis;
}

.type-badge,
.environment-badge {
  border-radius: 999px;
  padding: 0.15rem 0.5rem;
  font-size: 0.7rem;
  font-weight: 600;
}

.type-badge {
  color: #3730a3;
  background: #eef2ff;
}

.type-rollback {
  color: #b42318;
  background: #fef3f2;
}

.type-scaling {
  color: #175cd3;
  background: #eff8ff;
}

.type-configuration {
  color: #b54708;
  background: #fffaeb;
}

.environment-badge {
  color: var(--color-text-muted);
  background: var(--color-bg);
}

.identity,
.description {
  overflow-wrap: anywhere;
}

.identity {
  display: flex;
  flex-wrap: wrap;
  gap: 0.35rem 0.75rem;
  margin: 0.45rem 0 0;
  color: var(--color-text-muted);
  font-size: 0.78rem;
}

.identity code {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}

.description {
  margin: 0.4rem 0 0;
  font-size: 0.86rem;
}

.card-actions {
  gap: 0.4rem;
}

.modal-backdrop {
  position: fixed;
  z-index: 30;
  inset: 0;
  display: grid;
  place-items: center;
  padding: 1rem;
  background: rgba(24, 24, 27, 0.3);
}

.modal {
  width: min(620px, 96vw);
  max-height: 92vh;
  overflow-y: auto;
  border-radius: var(--radius);
  background: var(--color-surface);
  box-shadow: var(--shadow-lg);
}

.modal-header {
  justify-content: space-between;
  padding: 1rem 1.25rem;
  border-bottom: 1px solid var(--color-border);
}

.modal-header h2 {
  margin: 0;
  font-size: 1.05rem;
}

.close-button {
  border: 0;
  color: var(--color-text-muted);
  background: transparent;
  font-size: 1.4rem;
}

.modal-body {
  display: flex;
  flex-direction: column;
  gap: 1rem;
  padding: 1.25rem;
}

.form-row {
  display: flex;
  gap: 0.9rem;
}

.modal-body input,
.modal-body select,
.modal-body textarea {
  width: 100%;
}

.modal-actions {
  justify-content: flex-end;
  gap: 0.5rem;
  padding-top: 0.25rem;
}

@media (max-width: 780px) {
  .changes-view {
    padding: 1.25rem;
  }

  .filter-bar,
  .form-row {
    align-items: stretch;
    flex-direction: column;
  }

  .change-card {
    grid-template-columns: 1fr;
    gap: 0.7rem;
  }

  .card-actions {
    justify-content: flex-start;
  }
}
</style>
