<script setup lang="ts">
import { computed, ref } from 'vue'
import DiffViewer from './DiffViewer.vue'

type OperationStatus =
  | 'pending'
  | 'selected_approved'
  | 'selected_rejected'
  | 'approved'
  | 'completed'
  | 'rejected'
  | 'failed'

const props = defineProps<{
  tool: string
  title: string
  command: string
  label?: string
  riskLevel?: 'medium' | 'high' | 'critical'
  status: OperationStatus
  args?: Record<string, unknown>
  diff?: string
  result?: string
  disabled?: boolean
}>()

const emit = defineEmits<{
  approve: []
  reject: []
}>()

const expanded = ref(false)

const statusText = computed(() => {
  const labels: Record<OperationStatus, string> = {
    pending: '等待确认',
    selected_approved: '已选择执行',
    selected_rejected: '已选择不执行',
    approved: '已批准，执行中',
    completed: '执行完成',
    rejected: '已拒绝',
    failed: '执行失败',
  }
  return labels[props.status]
})

const riskText = computed(() => {
  if (props.riskLevel === 'medium') return '中风险'
  if (props.riskLevel === 'critical') return '严重风险'
  if (props.riskLevel === 'high') return '高风险'
  return ''
})

const formattedArgs = computed(() => JSON.stringify(props.args ?? {}, null, 2))
</script>

<template>
  <article class="operation-card" :class="status">
    <div class="operation-header">
      <button
        type="button"
        class="operation-summary"
        :aria-expanded="expanded"
        @click="expanded = !expanded"
      >
        <span class="operation-marker" aria-hidden="true" />
        <span class="operation-heading">
          <span class="operation-title">{{ title }}</span>
          <span v-if="label" class="operation-target">{{ label }}</span>
        </span>
        <code class="operation-tool">{{ tool }}</code>
        <span v-if="riskText" class="operation-risk">{{ riskText }}</span>
        <span class="operation-status">{{ statusText }}</span>
        <span class="expand-label">{{ expanded ? '收起' : '详情' }}</span>
      </button>

      <div v-if="status === 'pending'" class="operation-actions">
        <button type="button" class="approve" :disabled="disabled" @click="emit('approve')">
          执行
        </button>
        <button type="button" class="reject" :disabled="disabled" @click="emit('reject')">
          不执行
        </button>
      </div>
    </div>

    <section v-if="expanded && diff" class="operation-diff">
      <h4>文件差异</h4>
      <DiffViewer :diff="diff" />
    </section>

    <div v-if="expanded" class="operation-details">
      <section v-if="command">
        <h4>受控动作</h4>
        <code class="operation-command">{{ command }}</code>
      </section>
      <div class="operation-data">
        <section>
          <h4>调用参数</h4>
          <pre>{{ formattedArgs }}</pre>
        </section>
        <section v-if="result">
          <h4>执行输出</h4>
          <pre>{{ result }}</pre>
        </section>
      </div>
    </div>
  </article>
</template>

<style scoped>
.operation-card {
  overflow: hidden;
  border: 1px solid var(--color-border-strong);
  border-left: 3px solid #c28a25;
  border-radius: 8px;
  background: #ffffff;
}

.operation-card.completed,
.operation-card.approved,
.operation-card.selected_approved {
  border-left-color: var(--color-status-active);
}

.operation-card.rejected,
.operation-card.failed,
.operation-card.selected_rejected {
  border-left-color: var(--color-status-unreachable);
}

.operation-header {
  display: flex;
  align-items: center;
}

.operation-summary {
  min-width: 0;
  min-height: 48px;
  flex: 1;
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto auto auto auto;
  align-items: center;
  gap: 0.55rem;
  padding: 0.55rem 0.7rem;
  border: 0;
  background: transparent;
  color: var(--color-text);
  text-align: left;
}

.operation-summary:hover {
  background: rgba(22, 137, 154, 0.035);
}

.operation-summary:focus-visible,
.operation-actions button:focus-visible {
  outline: 2px solid var(--color-accent);
  outline-offset: -2px;
}

.operation-marker {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #c28a25;
}

.completed .operation-marker,
.approved .operation-marker,
.selected_approved .operation-marker {
  background: var(--color-status-active);
}

.rejected .operation-marker,
.failed .operation-marker,
.selected_rejected .operation-marker {
  background: var(--color-status-unreachable);
}

.operation-heading {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 0.12rem;
}

.operation-title {
  overflow: hidden;
  font-size: 0.82rem;
  font-weight: 650;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.operation-target,
.operation-tool,
.operation-status,
.expand-label {
  color: var(--color-text-muted);
  font-size: 0.68rem;
}

.operation-target {
  font-family: var(--font-data);
}

.operation-tool {
  font-family: var(--font-data);
}

.operation-risk {
  padding: 0.08rem 0.34rem;
  border-radius: 4px;
  background: #fff3db;
  color: #8a5a08;
  font-size: 0.68rem;
}

.failed .operation-status,
.rejected .operation-status {
  color: var(--color-status-unreachable);
}

.operation-actions {
  display: flex;
  gap: 0.4rem;
  padding-right: 0.65rem;
}

.operation-actions button {
  min-height: 32px;
  padding: 0.3rem 0.7rem;
  border-radius: 6px;
  font-size: 0.75rem;
  white-space: nowrap;
}

.operation-actions .approve {
  border: 1px solid var(--color-accent);
  background: var(--color-accent);
  color: var(--color-accent-contrast);
}

.operation-actions .reject {
  border: 1px solid var(--color-border-strong);
  background: #ffffff;
  color: var(--color-text);
}

.operation-actions button:disabled {
  cursor: default;
  opacity: 0.55;
}

.operation-diff,
.operation-details {
  display: grid;
  gap: 0.85rem;
  padding: 0.85rem;
  border-top: 1px solid var(--color-border);
  background: #f8fafb;
}

.operation-diff h4,
.operation-details h4 {
  margin: 0 0 0.35rem;
  color: var(--color-text-muted);
  font-size: 0.68rem;
  letter-spacing: 0.06em;
}

.operation-command {
  display: block;
  padding: 0.55rem 0.65rem;
  border: 1px solid #dfe4e7;
  border-radius: 6px;
  background: #ffffff;
  font-family: var(--font-data);
  font-size: 0.72rem;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.operation-data {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0.75rem;
}

.operation-data pre {
  max-height: 16rem;
  margin: 0;
  overflow: auto;
  padding: 0.65rem;
  border: 1px solid #dfe4e7;
  border-radius: 6px;
  background: #ffffff;
  font-family: var(--font-data);
  font-size: 0.7rem;
  line-height: 1.5;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

@media (max-width: 760px) {
  .operation-header {
    align-items: stretch;
    flex-direction: column;
  }

  .operation-summary {
    grid-template-columns: auto minmax(0, 1fr) auto;
  }

  .operation-tool,
  .operation-status,
  .operation-risk {
    display: none;
  }

  .operation-actions {
    justify-content: flex-end;
    padding: 0 0.65rem 0.65rem;
  }

  .operation-data {
    grid-template-columns: 1fr;
  }
}
</style>
