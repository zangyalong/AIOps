<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { acknowledgeIncident, fetchIncidents, resolveIncident } from '../api/incidents'
import MarkdownContent from '../components/MarkdownContent.vue'
import type { Incident, IncidentStatus } from '../types/incident'

type IncidentFilter = 'all' | IncidentStatus

const incidents = ref<Incident[]>([])
const activeFilter = ref<IncidentFilter>('all')
const selectedIncident = ref<Incident | null>(null)
const loading = ref(true)
const error = ref<string | null>(null)
const pendingAction = ref<string | null>(null)

const filters: { value: IncidentFilter; label: string }[] = [
  { value: 'all', label: '全部' },
  { value: 'firing', label: '待处理' },
  { value: 'acknowledged', label: '已认领' },
  { value: 'resolved', label: '已解决' },
]

const statusLabel: Record<IncidentStatus, string> = {
  firing: '待处理',
  acknowledged: '已认领',
  resolved: '已解决',
}

const filteredIncidents = computed(() => {
  if (activeFilter.value === 'all') return incidents.value
  return incidents.value.filter((incident) => incident.status === activeFilter.value)
})

const statusCounts = computed<Record<IncidentFilter, number>>(() => ({
  all: incidents.value.length,
  firing: incidents.value.filter((incident) => incident.status === 'firing').length,
  acknowledged: incidents.value.filter((incident) => incident.status === 'acknowledged').length,
  resolved: incidents.value.filter((incident) => incident.status === 'resolved').length,
}))

async function loadIncidents() {
  loading.value = true
  error.value = null
  try {
    incidents.value = await fetchIncidents()
    if (selectedIncident.value) {
      selectedIncident.value =
        incidents.value.find((incident) => incident.id === selectedIncident.value?.id) ?? null
    }
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    loading.value = false
  }
}

onMounted(loadIncidents)

function formatTime(value: string | null): string {
  if (!value) return '—'
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(new Date(value))
}

function severityLabel(severity: string): string {
  const labels: Record<string, string> = {
    critical: '严重',
    warning: '警告',
    info: '提示',
  }
  return labels[severity.toLowerCase()] ?? severity
}

function replaceIncident(updated: Incident) {
  const index = incidents.value.findIndex((incident) => incident.id === updated.id)
  if (index !== -1) incidents.value[index] = updated
  if (selectedIncident.value?.id === updated.id) selectedIncident.value = updated
}

async function acknowledge(incident: Incident) {
  const operator = prompt('请输入认领人名称')?.trim()
  if (!operator) return

  pendingAction.value = incident.id
  error.value = null
  try {
    replaceIncident(await acknowledgeIncident(incident.id, operator))
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    pendingAction.value = null
  }
}

async function resolve(incident: Incident) {
  if (!confirm(`确定将事件“${incident.alertname}”标记为已解决？`)) return

  pendingAction.value = incident.id
  error.value = null
  try {
    replaceIncident(await resolveIncident(incident.id))
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    pendingAction.value = null
  }
}
</script>

<template>
  <div class="incidents-view">
    <header class="view-header">
      <div>
        <h1>事件中心</h1>
        <p class="subtitle">查看告警归组后的运维事件、AI 诊断结论与处理状态</p>
      </div>
      <button type="button" class="btn-refresh" :disabled="loading" @click="loadIncidents">
        {{ loading ? '刷新中…' : '刷新' }}
      </button>
    </header>

    <div class="filter-bar" role="tablist" aria-label="事件状态筛选">
      <button
        v-for="filter in filters"
        :key="filter.value"
        type="button"
        class="filter-button"
        :class="{ active: activeFilter === filter.value }"
        :aria-selected="activeFilter === filter.value"
        role="tab"
        @click="activeFilter = filter.value"
      >
        {{ filter.label }}
        <span class="filter-count">{{ statusCounts[filter.value] }}</span>
      </button>
    </div>

    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <p v-if="loading && incidents.length === 0" class="hint">加载中…</p>
    <div v-else-if="filteredIncidents.length === 0" class="empty">
      <p>{{ incidents.length === 0 ? '还没有事件' : '当前筛选条件下没有事件' }}</p>
      <span>收到新的 Alertmanager 告警后，关联事件会自动出现在这里。</span>
    </div>

    <div v-else class="incident-list">
      <article
        v-for="incident in filteredIncidents"
        :key="incident.id"
        class="incident-card"
        tabindex="0"
        @click="selectedIncident = incident"
        @keydown.enter="selectedIncident = incident"
      >
        <div class="incident-main">
          <div class="incident-heading">
            <span class="severity-badge" :class="`severity-${incident.severity.toLowerCase()}`">
              {{ severityLabel(incident.severity) }}
            </span>
            <h2>{{ incident.alertname }}</h2>
            <span class="status-badge" :class="`status-${incident.status}`">
              {{ statusLabel[incident.status] }}
            </span>
          </div>
          <p class="summary">{{ incident.summary || '暂无事件摘要' }}</p>
          <div class="incident-meta">
            <span>实例：{{ incident.instance || '未关联' }}</span>
            <span>更新：{{ formatTime(incident.updated_at) }}</span>
            <span v-if="incident.acked_by">认领人：{{ incident.acked_by }}</span>
          </div>
        </div>

        <div class="diagnosis-state" :class="{ ready: incident.diagnosis }">
          <span class="diagnosis-dot" />
          {{ incident.diagnosis ? '诊断完成' : '等待诊断' }}
        </div>

        <div v-if="incident.status !== 'resolved'" class="card-actions" @click.stop>
          <button
            v-if="incident.status === 'firing'"
            type="button"
            class="btn-secondary"
            :disabled="pendingAction === incident.id"
            @click="acknowledge(incident)"
          >
            认领
          </button>
          <button
            type="button"
            class="btn-resolve"
            :disabled="pendingAction === incident.id"
            @click="resolve(incident)"
          >
            {{ pendingAction === incident.id ? '处理中…' : '标记解决' }}
          </button>
        </div>
      </article>
    </div>

    <div v-if="selectedIncident" class="drawer-backdrop" @click.self="selectedIncident = null">
      <aside class="detail-drawer" aria-modal="true" role="dialog" aria-label="事件详情">
        <div class="drawer-header">
          <div>
            <span class="severity-badge" :class="`severity-${selectedIncident.severity.toLowerCase()}`">
              {{ severityLabel(selectedIncident.severity) }}
            </span>
            <h2>{{ selectedIncident.alertname }}</h2>
          </div>
          <button type="button" class="close-button" aria-label="关闭" @click="selectedIncident = null">×</button>
        </div>

        <p class="drawer-summary">{{ selectedIncident.summary || '暂无事件摘要' }}</p>

        <dl class="detail-grid">
          <div><dt>状态</dt><dd>{{ statusLabel[selectedIncident.status] }}</dd></div>
          <div><dt>实例</dt><dd>{{ selectedIncident.instance || '未关联' }}</dd></div>
          <div><dt>创建时间</dt><dd>{{ formatTime(selectedIncident.created_at) }}</dd></div>
          <div><dt>最后更新</dt><dd>{{ formatTime(selectedIncident.updated_at) }}</dd></div>
          <div v-if="selectedIncident.acked_by"><dt>认领人</dt><dd>{{ selectedIncident.acked_by }}</dd></div>
          <div v-if="selectedIncident.resolved_at"><dt>解决时间</dt><dd>{{ formatTime(selectedIncident.resolved_at) }}</dd></div>
        </dl>

        <section class="diagnosis-panel">
          <div class="section-heading">
            <h3>AI 诊断结论</h3>
            <span v-if="selectedIncident.triaged_at">{{ formatTime(selectedIncident.triaged_at) }}</span>
          </div>
          <MarkdownContent
            v-if="selectedIncident.diagnosis"
            class="diagnosis-markdown"
            :content="selectedIncident.diagnosis"
          />
          <p v-else class="no-diagnosis">自动诊断尚未完成，稍后刷新查看。</p>
        </section>

        <div v-if="selectedIncident.status !== 'resolved'" class="drawer-actions">
          <button
            v-if="selectedIncident.status === 'firing'"
            type="button"
            class="btn-secondary"
            :disabled="pendingAction === selectedIncident.id"
            @click="acknowledge(selectedIncident)"
          >
            认领事件
          </button>
          <button
            type="button"
            class="btn-resolve"
            :disabled="pendingAction === selectedIncident.id"
            @click="resolve(selectedIncident)"
          >
            {{ pendingAction === selectedIncident.id ? '处理中…' : '标记为已解决' }}
          </button>
        </div>
      </aside>
    </div>
  </div>
</template>

<style scoped>
.incidents-view {
  max-width: 1100px;
  margin: 0 auto;
  padding: 2rem;
}

.view-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 1rem;
  margin-bottom: 1.35rem;
}

h1,
h2,
h3,
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

.btn-refresh,
.btn-secondary,
.btn-resolve {
  border-radius: var(--radius-sm);
  padding: 0.5rem 0.85rem;
  font-size: 0.85rem;
}

.btn-refresh,
.btn-secondary {
  border: 1px solid var(--color-border);
  background: var(--color-surface);
  color: var(--color-text);
}

.btn-refresh:hover,
.btn-secondary:hover {
  background: var(--color-bg);
}

button:disabled {
  cursor: default;
  opacity: 0.55;
}

.filter-bar {
  display: flex;
  gap: 0.4rem;
  margin-bottom: 1.25rem;
  padding-bottom: 0.85rem;
  border-bottom: 1px solid var(--color-border);
}

.filter-button {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  border: 0;
  border-radius: 999px;
  padding: 0.38rem 0.72rem;
  color: var(--color-text-muted);
  background: transparent;
  font-size: 0.85rem;
}

.filter-button:hover,
.filter-button.active {
  background: var(--color-surface);
  color: var(--color-text);
  box-shadow: var(--shadow-sm);
}

.filter-count {
  min-width: 1.25rem;
  padding: 0 0.32rem;
  border-radius: 999px;
  color: var(--color-text-muted);
  background: var(--color-bg);
  font-size: 0.72rem;
  text-align: center;
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

.incident-list {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.incident-card {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto auto;
  align-items: center;
  gap: 1.2rem;
  padding: 1rem 1.1rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius);
  background: var(--color-surface);
  box-shadow: var(--shadow-sm);
  cursor: pointer;
  transition: border-color 0.15s, box-shadow 0.15s, transform 0.15s;
}

.incident-card:hover,
.incident-card:focus-visible {
  border-color: color-mix(in srgb, var(--color-accent) 40%, var(--color-border));
  box-shadow: var(--shadow-md);
  outline: none;
  transform: translateY(-1px);
}

.incident-main {
  min-width: 0;
}

.incident-heading {
  display: flex;
  align-items: center;
  gap: 0.55rem;
}

.incident-heading h2 {
  overflow: hidden;
  margin: 0;
  font-size: 1rem;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.severity-badge,
.status-badge {
  flex-shrink: 0;
  border-radius: 999px;
  font-size: 0.7rem;
  font-weight: 600;
}

.severity-badge {
  padding: 0.15rem 0.45rem;
  color: #475467;
  background: #f2f4f7;
}

.severity-critical {
  color: #b42318;
  background: #fee4e2;
}

.severity-warning {
  color: #b54708;
  background: #fef0c7;
}

.severity-info {
  color: #175cd3;
  background: #dbeafe;
}

.status-badge {
  padding: 0.15rem 0.55rem;
}

.status-firing {
  color: #b42318;
  background: #fef3f2;
}

.status-acknowledged {
  color: #175cd3;
  background: #eff8ff;
}

.status-resolved {
  color: #067647;
  background: #ecfdf3;
}

.summary {
  overflow: hidden;
  margin: 0.45rem 0 0.35rem;
  color: var(--color-text-muted);
  font-size: 0.87rem;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.incident-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 0.35rem 1rem;
  color: var(--color-text-muted);
  font-size: 0.76rem;
}

.diagnosis-state {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  color: var(--color-text-muted);
  font-size: 0.78rem;
  white-space: nowrap;
}

.diagnosis-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--color-status-untested);
}

.diagnosis-state.ready {
  color: #067647;
}

.diagnosis-state.ready .diagnosis-dot {
  background: var(--color-status-active);
}

.card-actions,
.drawer-actions {
  display: flex;
  gap: 0.45rem;
}

.btn-resolve {
  border: 1px solid #a6f4c5;
  color: #067647;
  background: #ecfdf3;
}

.btn-resolve:hover {
  border-color: #6ce9a6;
  background: #dcfae6;
}

.drawer-backdrop {
  position: fixed;
  z-index: 20;
  inset: 0;
  display: flex;
  justify-content: flex-end;
  background: rgba(24, 24, 27, 0.25);
}

.detail-drawer {
  width: min(540px, 92vw);
  height: 100%;
  overflow-y: auto;
  padding: 1.5rem;
  background: var(--color-surface);
  box-shadow: var(--shadow-lg);
}

.drawer-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 1rem;
}

.drawer-header > div {
  display: flex;
  align-items: center;
  gap: 0.55rem;
}

.drawer-header h2 {
  margin: 0;
  font-size: 1.2rem;
}

.close-button {
  border: 0;
  color: var(--color-text-muted);
  background: transparent;
  font-size: 1.45rem;
  line-height: 1;
}

.drawer-summary {
  margin: 1.1rem 0;
  color: var(--color-text-muted);
}

.detail-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0.75rem;
  margin: 0;
  padding: 1rem;
  border-radius: var(--radius);
  background: var(--color-bg);
}

.detail-grid div {
  min-width: 0;
}

.detail-grid dt {
  color: var(--color-text-muted);
  font-size: 0.72rem;
}

.detail-grid dd {
  overflow: hidden;
  margin: 0.1rem 0 0;
  font-size: 0.86rem;
  text-overflow: ellipsis;
}

.diagnosis-panel {
  min-width: 0;
  margin-top: 1.4rem;
}

.section-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
}

.section-heading h3 {
  margin-bottom: 0.65rem;
  font-size: 0.95rem;
}

.section-heading span {
  color: var(--color-text-muted);
  font-size: 0.72rem;
}

.diagnosis-markdown,
.no-diagnosis {
  margin: 0;
  border: 1px solid var(--color-border);
  border-radius: var(--radius);
  padding: 1rem;
  font-size: 0.86rem;
}

.diagnosis-markdown {
  overflow-x: hidden;
}

.no-diagnosis {
  color: var(--color-text-muted);
  background: var(--color-bg);
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-word;
}

.drawer-actions {
  justify-content: flex-end;
  margin-top: 1.25rem;
  padding-top: 1rem;
  border-top: 1px solid var(--color-border);
}

@media (max-width: 780px) {
  .incidents-view {
    padding: 1.25rem;
  }

  .incident-card {
    grid-template-columns: 1fr;
    gap: 0.75rem;
  }

  .card-actions {
    justify-content: flex-start;
  }

  .diagnosis-state {
    order: 3;
  }
}
</style>
