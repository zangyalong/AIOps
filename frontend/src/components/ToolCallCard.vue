<script setup lang="ts">
import { computed, ref } from 'vue'
import DiffViewer from './DiffViewer.vue'

const props = defineProps<{
  tool: string
  args?: Record<string, unknown>
  result?: string
  status: 'running' | 'completed' | 'failed'
}>()

const expanded = ref(false)

const areaLabels: Record<string, string> = {
  servers: '查询服务器',
  services: '检查服务',
  runtime: '检查运行状态',
  host: '检查宿主机',
  metrics: '查询指标',
  logs: '检索日志',
  alerts: '查询告警',
  incident: '关联事件证据',
  visualization: '生成观测链接',
  change: '准备变更预览',
}

const legacyLabels: Record<string, string> = {
  read_skill: '读取 Skill',
  query_servers: '查询服务器列表',
  query_server_metrics: '查询服务器指标',
  query_metrics: '查询 Prometheus 指标',
  search_logs: '检索日志',
  query_alerts: '查询告警',
  run_diagnostic_command: '执行只读诊断',
  run_root_read_command: '执行宿主机只读调查',
  read_server_file: '读取服务器文件',
  preview_server_file_update: '预览文件变更',
  preview_server_config_patch: '预览配置补丁',
}

const label = computed(() => {
  if (props.tool === 'investigate') {
    return areaLabels[String(props.args?.area ?? '')] ?? '执行运维调查'
  }
  return legacyLabels[props.tool] ?? props.tool
})

const summary = computed(() => {
  const args = props.args ?? {}
  const parameters =
    args.parameters && typeof args.parameters === 'object'
      ? (args.parameters as Record<string, unknown>)
      : {}
  return [args.name, args.target, args.query, parameters.path, args.server_name, args.path]
    .filter((value, index, values) => value && values.indexOf(value) === index)
    .map(String)
    .join(' · ')
})

const statusText = computed(() => {
  if (props.status === 'running') return '运行中'
  if (props.status === 'failed') return '失败'
  return '已完成'
})

const formattedArgs = computed(() => JSON.stringify(props.args ?? {}, null, 2))

function parsedResult(): Record<string, unknown> | null {
  const text = props.result?.trim()
  if (!text) return null
  for (const candidate of [text, text.slice(Math.max(0, text.indexOf('{')))]) {
    try {
      const value = JSON.parse(candidate)
      if (value && typeof value === 'object' && !Array.isArray(value)) return value
    } catch {
      // 普通文本结果直接显示，不把解析失败当成界面错误。
    }
  }
  return null
}

const structuredResult = computed(parsedResult)
const resultDiff = computed(() => {
  const value = structuredResult.value?.preview_diff
  return typeof value === 'string' ? value : ''
})
const formattedResult = computed(() => {
  if (!props.result) return props.status === 'running' ? '等待工具返回…' : '没有返回内容'
  return structuredResult.value
    ? JSON.stringify(structuredResult.value, null, 2)
    : props.result
})
</script>

<template>
  <article class="tool-card" :class="status">
    <button
      type="button"
      class="tool-summary"
      :aria-expanded="expanded"
      @click="expanded = !expanded"
    >
      <span class="status-dot" aria-hidden="true" />
      <span class="tool-heading">
        <span class="tool-label">{{ label }}</span>
        <span v-if="summary" class="tool-target">{{ summary }}</span>
      </span>
      <code class="tool-name">{{ tool }}</code>
      <span class="tool-status">{{ statusText }}</span>
      <span class="expand-label">{{ expanded ? '收起' : '展开' }}</span>
    </button>

    <div v-if="expanded" class="tool-details">
      <section>
        <h4>调用参数</h4>
        <pre>{{ formattedArgs }}</pre>
      </section>
      <section>
        <h4>工具输出</h4>
        <DiffViewer v-if="resultDiff" :diff="resultDiff" />
        <pre v-else>{{ formattedResult }}</pre>
      </section>
    </div>
  </article>
</template>

<style scoped>
.tool-card {
  overflow: hidden;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.72);
}

.tool-card.failed {
  border-color: color-mix(in srgb, var(--color-status-unreachable) 35%, var(--color-border));
}

.tool-summary {
  width: 100%;
  min-height: 44px;
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto auto auto;
  align-items: center;
  gap: 0.65rem;
  padding: 0.55rem 0.7rem;
  border: 0;
  background: transparent;
  color: var(--color-text);
  text-align: left;
}

.tool-summary:hover {
  background: rgba(22, 137, 154, 0.045);
}

.tool-summary:focus-visible {
  outline: 2px solid var(--color-accent);
  outline-offset: -2px;
}

.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--color-status-active);
}

.running .status-dot {
  background: var(--color-accent);
  box-shadow: 0 0 0 4px rgba(22, 137, 154, 0.12);
  animation: pulse 1.4s ease-in-out infinite;
}

.failed .status-dot {
  background: var(--color-status-unreachable);
}

.tool-heading {
  min-width: 0;
  display: flex;
  align-items: baseline;
  gap: 0.55rem;
}

.tool-label {
  flex-shrink: 0;
  font-size: 0.82rem;
  font-weight: 650;
}

.tool-target {
  min-width: 0;
  overflow: hidden;
  color: var(--color-text-muted);
  font-family: var(--font-data);
  font-size: 0.7rem;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tool-name {
  color: #65727a;
  font-family: var(--font-data);
  font-size: 0.68rem;
}

.tool-status,
.expand-label {
  color: var(--color-text-muted);
  font-size: 0.7rem;
}

.failed .tool-status {
  color: var(--color-status-unreachable);
}

.tool-details {
  display: grid;
  gap: 0.85rem;
  padding: 0.8rem;
  border-top: 1px solid var(--color-border);
  background: #f8fafb;
}

.tool-details h4 {
  margin: 0 0 0.35rem;
  color: var(--color-text-muted);
  font-size: 0.68rem;
  font-weight: 650;
  letter-spacing: 0.06em;
}

.tool-details pre {
  max-height: 20rem;
  margin: 0;
  overflow: auto;
  padding: 0.65rem;
  border: 1px solid #dfe4e7;
  border-radius: 6px;
  background: #ffffff;
  color: #2b373d;
  font-family: var(--font-data);
  font-size: 0.72rem;
  line-height: 1.55;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

@keyframes pulse {
  50% { opacity: 0.45; }
}

@media (max-width: 720px) {
  .tool-summary {
    grid-template-columns: auto minmax(0, 1fr) auto;
  }

  .tool-name,
  .tool-status {
    display: none;
  }
}
</style>
