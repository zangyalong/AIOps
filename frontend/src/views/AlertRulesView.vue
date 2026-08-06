<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import {
  applyAlertRules,
  createAlertRule,
  deleteAlertRule,
  fetchAlertRules,
  updateAlertRule,
} from '../api/alertRules'
import type {
  AlertRule,
  AlertRuleInput,
  AlertRuleOverview,
  AlertRuleRuntimeState,
  AlertRuleSeverity,
} from '../types/alertRule'

const rules = ref<AlertRule[]>([])
const activeAlertsTotal = ref(0)
const prometheusMessage = ref<string | null>(null)
const alertmanagerMessage = ref<string | null>(null)
const loading = ref(true)
const pending = ref<string | null>(null)
const error = ref<string | null>(null)
const success = ref<string | null>(null)
const editorOpen = ref(false)
const editingRule = ref<AlertRule | null>(null)
const labelsText = ref('{}')

const emptyForm = (): AlertRuleInput => ({
  name: '',
  expression: '',
  duration: '5m',
  severity: 'warning',
  summary: '',
  description: null,
  labels: {},
  enabled: true,
})
const form = reactive<AlertRuleInput>(emptyForm())

const enabledCount = computed(() => rules.value.filter((rule) => rule.enabled).length)
const firingCount = computed(
  () => rules.value.filter((rule) => rule.runtime_state === 'firing').length,
)

const stateLabels: Record<AlertRuleRuntimeState, string> = {
  disabled: '已停用',
  inactive: '正常',
  pending: '等待触发',
  firing: '触发中',
  unknown: '状态未知',
}
const severityLabels: Record<AlertRuleSeverity, string> = {
  info: '提示',
  warning: '警告',
  critical: '严重',
}

function applyOverview(overview: AlertRuleOverview) {
  rules.value = overview.rules
  activeAlertsTotal.value = overview.active_alerts_total
  prometheusMessage.value = overview.prometheus_message
  alertmanagerMessage.value = overview.alertmanager_message
}

async function loadRules(preserveFeedback = false) {
  loading.value = true
  if (!preserveFeedback) {
    error.value = null
    success.value = null
  }
  try {
    const overview = await fetchAlertRules()
    applyOverview(overview)
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    loading.value = false
  }
}

function openCreate() {
  editingRule.value = null
  Object.assign(form, emptyForm())
  labelsText.value = '{}'
  editorOpen.value = true
}

function openEdit(rule: AlertRule) {
  editingRule.value = rule
  Object.assign(form, {
    name: rule.name,
    expression: rule.expression,
    duration: rule.duration,
    severity: rule.severity,
    summary: rule.summary,
    description: rule.description,
    labels: rule.labels,
    enabled: rule.enabled,
  })
  labelsText.value = JSON.stringify(rule.labels, null, 2)
  editorOpen.value = true
}

function closeEditor() {
  if (pending.value === 'save') return
  editorOpen.value = false
}

async function saveRule() {
  let labels: Record<string, string>
  try {
    const parsed: unknown = JSON.parse(labelsText.value.trim() || '{}')
    if (parsed === null || Array.isArray(parsed) || typeof parsed !== 'object') {
      throw new Error('附加标签必须是 JSON 对象')
    }
    if (Object.values(parsed).some((value) => typeof value !== 'string')) {
      throw new Error('附加标签的值必须全部是字符串')
    }
    labels = parsed as Record<string, string>
  } catch (e) {
    error.value = e instanceof Error ? e.message : '附加标签 JSON 格式不正确'
    return
  }
  pending.value = 'save'
  error.value = null
  success.value = null
  const data: AlertRuleInput = {
    ...form,
    description: form.description?.trim() || null,
    labels,
  }
  try {
    const result = editingRule.value
      ? await updateAlertRule(editingRule.value.id, data)
      : await createAlertRule(data)
    success.value = result.message
    applyOverview(result.overview)
    editorOpen.value = false
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    pending.value = null
  }
}

async function toggleRule(rule: AlertRule) {
  pending.value = rule.id
  error.value = null
  success.value = null
  try {
    const result = await updateAlertRule(rule.id, { enabled: !rule.enabled })
    success.value = result.message
    applyOverview(result.overview)
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    pending.value = null
  }
}

async function removeRule(rule: AlertRule) {
  if (!confirm(`确定删除预警规则“${rule.name}”？`)) return
  pending.value = rule.id
  error.value = null
  success.value = null
  try {
    const result = await deleteAlertRule(rule.id)
    success.value = result.message
    applyOverview(result.overview)
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    pending.value = null
  }
}

async function syncRules() {
  pending.value = 'apply'
  error.value = null
  success.value = null
  try {
    const result = await applyAlertRules()
    success.value = result.message
    applyOverview(result.overview)
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    pending.value = null
  }
}

onMounted(loadRules)
</script>

<template>
  <div class="alert-rules-view">
    <header class="view-header">
      <div>
        <h1>预警规则</h1>
        <p>手动配置 PromQL 判定条件；Prometheus 负责评估，Alertmanager 负责通知和告警分发。</p>
      </div>
      <div class="header-actions">
        <button
          type="button"
          class="btn-secondary"
          :disabled="pending !== null"
          @click="syncRules"
        >
          {{ pending === 'apply' ? '同步中…' : '同步到 Prometheus' }}
        </button>
        <button type="button" class="btn-primary" @click="openCreate">新增规则</button>
      </div>
    </header>

    <section class="summary-grid" aria-label="预警规则摘要">
      <article>
        <span>规则总数</span>
        <strong>{{ rules.length }}</strong>
      </article>
      <article>
        <span>已启用</span>
        <strong>{{ enabledCount }}</strong>
      </article>
      <article>
        <span>触发中的规则</span>
        <strong class="danger-number">{{ firingCount }}</strong>
      </article>
      <article>
        <span>Alertmanager 活跃告警</span>
        <strong>{{ activeAlertsTotal }}</strong>
      </article>
    </section>

    <p v-if="error" class="feedback error" role="alert">{{ error }}</p>
    <p v-if="success" class="feedback success" role="status">{{ success }}</p>
    <div v-if="prometheusMessage || alertmanagerMessage" class="connection-warnings">
      <p v-if="prometheusMessage">
        <strong>Prometheus 状态暂不可用：</strong>{{ prometheusMessage }}
      </p>
      <p v-if="alertmanagerMessage">
        <strong>Alertmanager 状态暂不可用：</strong>{{ alertmanagerMessage }}
      </p>
    </div>

    <section class="rules-panel">
      <div class="panel-heading">
        <div>
          <h2>AIOMS 管理的规则</h2>
          <p>保存、启停或删除后会自动重载 Prometheus；“同步”用于重新应用数据库中的全部规则。</p>
        </div>
        <button type="button" class="text-button" :disabled="loading" @click="loadRules()">
          {{ loading ? '刷新中…' : '刷新状态' }}
        </button>
      </div>

      <div v-if="loading && rules.length === 0" class="empty-state">正在加载规则…</div>
      <div v-else-if="rules.length === 0" class="empty-state">
        <strong>还没有手动配置的预警规则</strong>
        <span>点击“新增规则”，填写 PromQL 表达式和持续时间。</span>
      </div>
      <div v-else class="rule-list">
        <article v-for="rule in rules" :key="rule.id" class="rule-card">
          <div class="rule-main">
            <div class="rule-title-row">
              <span
                class="state-badge"
                :class="`state-${rule.runtime_state}`"
              >
                {{ stateLabels[rule.runtime_state] }}
              </span>
              <span class="severity-badge" :class="`severity-${rule.severity}`">
                {{ severityLabels[rule.severity] }}
              </span>
              <h3>{{ rule.name }}</h3>
            </div>
            <p class="rule-summary">{{ rule.summary }}</p>
            <code>{{ rule.expression }}</code>
            <div class="rule-meta">
              <span>持续 {{ rule.duration }}</span>
              <span>Prometheus 健康状态：{{ rule.health }}</span>
              <span v-if="rule.active_alerts > 0">活跃告警 {{ rule.active_alerts }} 条</span>
              <span v-for="(value, name) in rule.labels" :key="name">
                {{ name }}={{ value }}
              </span>
            </div>
            <p v-if="rule.last_error" class="rule-error">{{ rule.last_error }}</p>
          </div>
          <div class="rule-actions">
            <button type="button" class="text-button" @click="openEdit(rule)">编辑</button>
            <button
              type="button"
              class="text-button"
              :disabled="pending === rule.id"
              @click="toggleRule(rule)"
            >
              {{ rule.enabled ? '停用' : '启用' }}
            </button>
            <button
              type="button"
              class="text-button danger"
              :disabled="pending === rule.id"
              @click="removeRule(rule)"
            >
              删除
            </button>
          </div>
        </article>
      </div>
    </section>

    <div v-if="editorOpen" class="modal-backdrop" @mousedown.self="closeEditor">
      <form class="rule-editor" @submit.prevent="saveRule">
        <header>
          <div>
            <h2>{{ editingRule ? '编辑预警规则' : '新增预警规则' }}</h2>
            <p>表达式会先由 Prometheus 校验，通过后才保存。</p>
          </div>
          <button type="button" class="close-button" aria-label="关闭" @click="closeEditor">×</button>
        </header>

        <div class="form-grid">
          <label class="wide-field">
            <span>规则名</span>
            <input
              v-model="form.name"
              required
              maxlength="200"
              pattern="[A-Za-z_][A-Za-z0-9_]*"
              placeholder="例如 NodeCpuUsageHigh"
            />
            <small>Prometheus 标识符，只能使用字母、数字和下划线，不能以数字开头。</small>
          </label>

          <label>
            <span>严重级别</span>
            <select v-model="form.severity">
              <option value="info">提示</option>
              <option value="warning">警告</option>
              <option value="critical">严重</option>
            </select>
          </label>

          <label>
            <span>持续时间</span>
            <input v-model="form.duration" required placeholder="例如 5m、1h" />
          </label>

          <label class="wide-field">
            <span>PromQL 表达式</span>
            <textarea
              v-model="form.expression"
              required
              rows="4"
              maxlength="5000"
              placeholder='例如 up{job="web01-node"} == 0'
            />
          </label>

          <label class="wide-field">
            <span>告警摘要</span>
            <input
              v-model="form.summary"
              required
              maxlength="500"
              placeholder="例如 web-01 节点连续 5 分钟不可用"
            />
          </label>

          <label class="wide-field">
            <span>处理说明（可选）</span>
            <textarea
              v-model="form.description"
              rows="3"
              maxlength="2000"
              placeholder="补充排查建议或影响范围"
            />
          </label>

          <label class="wide-field">
            <span>附加标签（JSON，可选）</span>
            <textarea
              v-model="labelsText"
              rows="3"
              placeholder='例如 {"instance": "db-01:9100", "team": "backend"}'
            />
            <small>严重级别由上方字段管理，这里不要重复填写 severity。</small>
          </label>

          <label class="toggle-field wide-field">
            <input v-model="form.enabled" type="checkbox" />
            <span>保存后立即启用这条规则</span>
          </label>
        </div>

        <footer>
          <button type="button" class="btn-secondary" @click="closeEditor">取消</button>
          <button type="submit" class="btn-primary" :disabled="pending === 'save'">
            {{ pending === 'save' ? '保存中…' : '保存并应用' }}
          </button>
        </footer>
      </form>
    </div>
  </div>
</template>

<style scoped>
.alert-rules-view {
  width: min(1180px, calc(100% - 3rem));
  margin: 0 auto;
  padding: 2rem 0 3rem;
}

.view-header,
.panel-heading,
.rule-card,
.rule-title-row,
.rule-actions,
.header-actions,
.rule-editor header,
.rule-editor footer {
  display: flex;
  align-items: center;
}

.view-header,
.panel-heading,
.rule-card,
.rule-editor header,
.rule-editor footer {
  justify-content: space-between;
}

h1,
h2,
h3,
p {
  margin-top: 0;
}

h1 {
  margin-bottom: 0.25rem;
  font-size: 1.65rem;
}

.view-header > div > p,
.panel-heading p,
.rule-editor header p {
  margin-bottom: 0;
  color: var(--color-text-muted);
}

.header-actions,
.rule-actions {
  gap: 0.65rem;
}

.btn-primary,
.btn-secondary {
  border-radius: var(--radius-sm);
  padding: 0.55rem 0.85rem;
}

.btn-primary {
  color: var(--color-accent-contrast);
  background: var(--color-accent);
  border: 1px solid var(--color-accent);
}

.btn-primary:hover {
  background: var(--color-accent-hover);
}

.btn-secondary {
  color: var(--color-text);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
}

button:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}

.summary-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 1rem;
  margin: 1.5rem 0;
}

.summary-grid article,
.rules-panel {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius);
  box-shadow: var(--shadow-sm);
}

.summary-grid article {
  padding: 1rem 1.1rem;
}

.summary-grid span {
  display: block;
  color: var(--color-text-muted);
  font-size: 0.85rem;
}

.summary-grid strong {
  display: block;
  margin-top: 0.15rem;
  font-size: 1.65rem;
}

.summary-grid .danger-number {
  color: var(--color-status-unreachable);
}

.feedback,
.connection-warnings {
  border-radius: var(--radius-sm);
  padding: 0.7rem 0.9rem;
}

.feedback.error,
.connection-warnings {
  color: #991b1b;
  background: #fef2f2;
  border: 1px solid #fecaca;
}

.feedback.success {
  color: #166534;
  background: #f0fdf4;
  border: 1px solid #bbf7d0;
}

.connection-warnings {
  margin-bottom: 1rem;
}

.connection-warnings p {
  margin-bottom: 0.3rem;
}

.connection-warnings p:last-child {
  margin-bottom: 0;
}

.rules-panel {
  overflow: hidden;
}

.panel-heading {
  padding: 1rem 1.2rem;
  border-bottom: 1px solid var(--color-border);
}

.panel-heading h2 {
  margin-bottom: 0.1rem;
  font-size: 1.08rem;
}

.panel-heading p {
  font-size: 0.85rem;
}

.text-button,
.close-button {
  color: var(--color-accent);
  background: transparent;
  border: 0;
  padding: 0.3rem;
}

.text-button.danger,
.rule-error {
  color: var(--color-status-unreachable);
}

.rule-card {
  align-items: flex-start;
  gap: 1rem;
  padding: 1.1rem 1.2rem;
  border-bottom: 1px solid var(--color-border);
}

.rule-card:last-child {
  border-bottom: 0;
}

.rule-main {
  min-width: 0;
  flex: 1;
}

.rule-title-row {
  flex-wrap: wrap;
  gap: 0.45rem;
}

.rule-title-row h3 {
  margin: 0;
  font-size: 1rem;
}

.state-badge,
.severity-badge {
  border-radius: 999px;
  padding: 0.1rem 0.5rem;
  font-size: 0.75rem;
  font-weight: 600;
}


.state-inactive {
  color: #166534;
  background: #dcfce7;
}

.state-pending {
  color: #92400e;
  background: #fef3c7;
}

.state-firing {
  color: #991b1b;
  background: #fee2e2;
}

.state-unknown,
.state-disabled {
  color: #4b5563;
  background: #e5e7eb;
}

.severity-info {
  color: #1e40af;
  background: #dbeafe;
}

.severity-warning {
  color: #92400e;
  background: #fef3c7;
}

.severity-critical {
  color: #991b1b;
  background: #fee2e2;
}

.rule-summary {
  margin: 0.55rem 0;
}

code {
  display: block;
  overflow-x: auto;
  color: #3730a3;
  background: #f5f3ff;
  border-radius: var(--radius-sm);
  padding: 0.55rem 0.7rem;
  font-size: 0.84rem;
}

.rule-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 1rem;
  margin-top: 0.55rem;
  color: var(--color-text-muted);
  font-size: 0.8rem;
}

.rule-error {
  margin: 0.5rem 0 0;
  font-size: 0.82rem;
}


.empty-state {
  display: flex;
  min-height: 240px;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 0.35rem;
  color: var(--color-text-muted);
}

.modal-backdrop {
  position: fixed;
  z-index: 30;
  inset: 0;
  display: grid;
  place-items: center;
  padding: 1.5rem;
  background: rgba(17, 24, 39, 0.5);
}

.rule-editor {
  width: min(720px, 100%);
  max-height: calc(100vh - 3rem);
  overflow-y: auto;
  background: var(--color-surface);
  border-radius: var(--radius);
  box-shadow: var(--shadow-lg);
}

.rule-editor header,
.rule-editor footer {
  padding: 1rem 1.2rem;
}

.rule-editor header {
  border-bottom: 1px solid var(--color-border);
}

.rule-editor header h2 {
  margin-bottom: 0.1rem;
  font-size: 1.15rem;
}

.rule-editor header p {
  font-size: 0.84rem;
}

.rule-editor footer {
  justify-content: flex-end;
  gap: 0.65rem;
  border-top: 1px solid var(--color-border);
}

.close-button {
  color: var(--color-text-muted);
  font-size: 1.5rem;
}

.form-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 1rem;
  padding: 1.2rem;
}

.form-grid label {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 0.35rem;
}

.form-grid label > span {
  font-weight: 600;
  font-size: 0.86rem;
}

.form-grid small {
  color: var(--color-text-muted);
}

.wide-field {
  grid-column: 1 / -1;
}

.toggle-field {
  flex-direction: row !important;
  align-items: center;
}

.toggle-field input {
  width: 1rem;
  height: 1rem;
}

@media (max-width: 800px) {
  .alert-rules-view {
    width: min(100% - 1.5rem, 1180px);
    padding-top: 1rem;
  }

  .view-header,
  .rule-card {
    align-items: stretch;
    flex-direction: column;
  }

  .header-actions {
    justify-content: flex-end;
  }

  .summary-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .rule-actions {
    justify-content: flex-end;
  }
}

@media (max-width: 520px) {
  .summary-grid,
  .form-grid {
    grid-template-columns: 1fr;
  }

  .wide-field {
    grid-column: auto;
  }
}
</style>
