<script setup lang="ts">
import { computed } from 'vue'
import type { ServerMetricPoint } from '../types/server'

const props = defineProps<{
  chartId: string
  label: string
  unit: string
  points: ServerMetricPoint[]
}>()

const width = 640
const height = 156
const paddingY = 10

const validPoints = computed(() =>
  props.points.filter(
    (point) => Number.isFinite(point.value) && Number.isFinite(Date.parse(point.timestamp)),
  ),
)

const maximumValue = computed(() =>
  validPoints.value.length
    ? Math.max(...validPoints.value.map((point) => point.value))
    : 0,
)

const chartMaximum = computed(() => {
  const rounded = Math.ceil(maximumValue.value / 10) * 10
  if (props.unit === 'percent') return Math.max(10, Math.min(100, rounded || 10))
  return Math.max(1, rounded || 1)
})

const coordinates = computed(() => {
  if (!validPoints.value.length) return []
  const timestamps = validPoints.value.map((point) => Date.parse(point.timestamp))
  const first = timestamps[0]
  const last = timestamps[timestamps.length - 1]
  const span = Math.max(1, last - first)
  return validPoints.value.map((point, index) => {
    const timestamp = timestamps[index]
    const x = validPoints.value.length === 1 ? width / 2 : ((timestamp - first) / span) * width
    const ratio = Math.max(0, Math.min(1, point.value / chartMaximum.value))
    const y = height - paddingY - ratio * (height - paddingY * 2)
    return { x, y }
  })
})

const linePoints = computed(() =>
  coordinates.value.map((point) => `${point.x.toFixed(2)},${point.y.toFixed(2)}`).join(' '),
)

const areaPoints = computed(() => {
  if (!linePoints.value) return ''
  return `0,${height} ${linePoints.value} ${width},${height}`
})

const latestValue = computed(() => validPoints.value.at(-1)?.value ?? 0)
const firstTime = computed(() => validPoints.value.at(0)?.timestamp ?? '')
const lastTime = computed(() => validPoints.value.at(-1)?.timestamp ?? '')
const lastCoordinate = computed(() => coordinates.value.at(-1) ?? null)

function formatValue(value: number): string {
  if (props.unit === 'percent') return `${value.toFixed(1)}%`
  return Number.isInteger(value) ? String(value) : value.toFixed(2)
}

function formatTime(value: string): string {
  if (!value) return ''
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(new Date(value))
}
</script>

<template>
  <article class="trend-card">
    <header>
      <div>
        <span>{{ label }}</span>
        <strong>{{ formatValue(latestValue) }}</strong>
      </div>
      <small>峰值 {{ formatValue(maximumValue) }}</small>
    </header>

    <div v-if="validPoints.length" class="chart-canvas">
      <svg :viewBox="`0 0 ${width} ${height}`" role="img" :aria-label="`${label}趋势图`">
        <defs>
          <linearGradient :id="`trend-fill-${chartId}`" x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stop-color="#4f46e5" stop-opacity="0.24" />
            <stop offset="100%" stop-color="#4f46e5" stop-opacity="0.02" />
          </linearGradient>
        </defs>
        <line v-for="y in [20, 58, 96, 134]" :key="y" x1="0" :y1="y" :x2="width" :y2="y" />
        <polygon :points="areaPoints" :fill="`url(#trend-fill-${chartId})`" />
        <polyline :points="linePoints" />
        <circle
          v-if="lastCoordinate"
          :cx="lastCoordinate.x"
          :cy="lastCoordinate.y"
          r="4"
        />
      </svg>
      <div class="chart-time-axis">
        <span>{{ formatTime(firstTime) }}</span>
        <span>{{ formatTime(lastTime) }}</span>
      </div>
    </div>
    <p v-else>当前时间范围没有数据</p>
  </article>
</template>

<style scoped>
.trend-card {
  min-width: 0;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  padding: 0.8rem;
  background: var(--color-surface);
}

header,
header > div,
.chart-time-axis {
  display: flex;
}

header {
  align-items: flex-start;
  justify-content: space-between;
  gap: 0.75rem;
  margin-bottom: 0.65rem;
}

header > div {
  flex-direction: column;
  gap: 0.1rem;
}

header span,
header small,
.chart-time-axis,
p {
  color: var(--color-text-muted);
  font-size: 0.68rem;
}

header strong {
  font-size: 1.15rem;
}

.chart-canvas {
  min-width: 0;
}

svg {
  display: block;
  width: 100%;
  height: 150px;
  overflow: visible;
}

line {
  stroke: var(--color-border);
  stroke-width: 1;
  vector-effect: non-scaling-stroke;
}

polyline {
  fill: none;
  stroke: var(--color-accent);
  stroke-linecap: round;
  stroke-linejoin: round;
  stroke-width: 2;
  vector-effect: non-scaling-stroke;
}

circle {
  fill: var(--color-surface);
  stroke: var(--color-accent);
  stroke-width: 2;
  vector-effect: non-scaling-stroke;
}

.chart-time-axis {
  justify-content: space-between;
  gap: 0.5rem;
  margin-top: 0.2rem;
  font-variant-numeric: tabular-nums;
}

p {
  margin: 2rem 0;
  text-align: center;
}
</style>
