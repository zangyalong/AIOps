<script setup lang="ts">
import { computed } from 'vue'
import type { ServiceDependency, ServiceRecord } from '../types/service'

const props = defineProps<{
  nodes: ServiceRecord[]
  edges: ServiceDependency[]
  focusId: string
}>()

const emit = defineEmits<{
  select: [serviceId: string]
}>()

const NODE_WIDTH = 176
const NODE_HEIGHT = 66
const COLUMN_GAP = 245
const ROW_GAP = 96
const MARGIN = 36

const layout = computed(() => {
  const nodeIds = new Set(props.nodes.map((node) => node.id))
  const layers = new Map<string, number>([[props.focusId, 0]])
  for (let pass = 0; pass < props.nodes.length; pass += 1) {
    let changed = false
    for (const edge of props.edges) {
      if (!nodeIds.has(edge.upstream_service_id) || !nodeIds.has(edge.downstream_service_id)) {
        continue
      }
      const upstreamLayer = layers.get(edge.upstream_service_id)
      const downstreamLayer = layers.get(edge.downstream_service_id)
      if (upstreamLayer !== undefined && downstreamLayer === undefined) {
        layers.set(edge.downstream_service_id, upstreamLayer + 1)
        changed = true
      } else if (downstreamLayer !== undefined && upstreamLayer === undefined) {
        layers.set(edge.upstream_service_id, downstreamLayer - 1)
        changed = true
      }
    }
    if (!changed) break
  }
  for (const node of props.nodes) {
    if (!layers.has(node.id)) layers.set(node.id, 0)
  }

  const grouped = new Map<number, ServiceRecord[]>()
  for (const node of props.nodes) {
    const layer = layers.get(node.id) ?? 0
    const group = grouped.get(layer) ?? []
    group.push(node)
    grouped.set(layer, group)
  }
  for (const group of grouped.values()) {
    group.sort((left, right) => left.name.localeCompare(right.name))
  }

  const layerNumbers = [...grouped.keys()].sort((left, right) => left - right)
  const minLayer = layerNumbers[0] ?? 0
  const maxLayer = layerNumbers.at(-1) ?? 0
  const maxRows = Math.max(1, ...[...grouped.values()].map((group) => group.length))
  const width = Math.max(560, (maxLayer - minLayer) * COLUMN_GAP + NODE_WIDTH + MARGIN * 2)
  const height = Math.max(230, (maxRows - 1) * ROW_GAP + NODE_HEIGHT + MARGIN * 2)
  const positions = new Map<string, { x: number; y: number }>()

  for (const layer of layerNumbers) {
    const group = grouped.get(layer) ?? []
    const groupHeight = (group.length - 1) * ROW_GAP + NODE_HEIGHT
    const startY = (height - groupHeight) / 2
    group.forEach((node, index) => {
      positions.set(node.id, {
        x: MARGIN + (layer - minLayer) * COLUMN_GAP,
        y: startY + index * ROW_GAP,
      })
    })
  }

  const nodes = props.nodes.map((node) => ({
    node,
    ...(positions.get(node.id) ?? { x: MARGIN, y: MARGIN }),
  }))
  const edges = props.edges.flatMap((edge) => {
    const source = positions.get(edge.upstream_service_id)
    const target = positions.get(edge.downstream_service_id)
    if (!source || !target) return []
    const sourceX = source.x + NODE_WIDTH
    const sourceY = source.y + NODE_HEIGHT / 2
    const targetX = target.x
    const targetY = target.y + NODE_HEIGHT / 2
    const bend = Math.max(45, Math.abs(targetX - sourceX) * 0.45)
    return [
      {
        edge,
        path: `M ${sourceX} ${sourceY} C ${sourceX + bend} ${sourceY}, ${targetX - bend} ${targetY}, ${targetX} ${targetY}`,
      },
    ]
  })
  return { width, height, nodes, edges }
})

function shortText(value: string, max = 22): string {
  return value.length > max ? `${value.slice(0, max - 1)}…` : value
}
</script>

<template>
  <div v-if="nodes.length" class="topology-shell">
    <svg
      class="topology-svg"
      :viewBox="`0 0 ${layout.width} ${layout.height}`"
      role="img"
      aria-label="服务依赖拓扑图"
    >
      <defs>
        <marker
          id="topology-arrow"
          markerWidth="8"
          markerHeight="8"
          refX="7"
          refY="4"
          orient="auto"
        >
          <path d="M 0 0 L 8 4 L 0 8 z" class="arrow-head" />
        </marker>
      </defs>

      <g class="edges">
        <path
          v-for="item in layout.edges"
          :key="item.edge.id"
          :d="item.path"
          class="edge-line"
          marker-end="url(#topology-arrow)"
        />
      </g>

      <g
        v-for="item in layout.nodes"
        :key="item.node.id"
        class="topology-node"
        :class="{ focus: item.node.id === focusId }"
        :transform="`translate(${item.x} ${item.y})`"
        role="button"
        tabindex="0"
        @click="emit('select', item.node.id)"
        @keydown.enter="emit('select', item.node.id)"
      >
        <rect :width="NODE_WIDTH" :height="NODE_HEIGHT" rx="10" />
        <text x="14" y="25" class="node-name">{{ shortText(item.node.name) }}</text>
        <text x="14" y="47" class="node-meta">
          {{ shortText(`${item.node.environment}${item.node.owner ? ` · ${item.node.owner}` : ''}`) }}
        </text>
      </g>
    </svg>
    <p class="topology-legend">箭头方向表示“左侧服务依赖右侧服务”；点击节点可切换详情。</p>
  </div>
  <p v-else class="topology-empty">当前服务还没有可展示的拓扑数据。</p>
</template>

<style scoped>
.topology-shell {
  min-width: 0;
  overflow: hidden;
  border: 1px solid var(--color-border);
  border-radius: var(--radius);
  background: linear-gradient(135deg, #fafafa, #f5f6f8);
}

.topology-svg {
  display: block;
  width: 100%;
  min-height: 230px;
}

.edge-line {
  fill: none;
  stroke: #98a2b3;
  stroke-width: 1.6;
}

.arrow-head {
  fill: #98a2b3;
}

.topology-node {
  cursor: pointer;
  outline: none;
}

.topology-node rect {
  fill: var(--color-surface);
  stroke: var(--color-border);
  stroke-width: 1.5;
  filter: drop-shadow(0 2px 3px rgba(24, 24, 27, 0.08));
}

.topology-node:hover rect,
.topology-node:focus-visible rect {
  stroke: var(--color-accent);
}

.topology-node.focus rect {
  fill: #eef2ff;
  stroke: var(--color-accent);
  stroke-width: 2;
}

.node-name {
  fill: var(--color-text);
  font-size: 13px;
  font-weight: 600;
}

.node-meta {
  fill: var(--color-text-muted);
  font-size: 10.5px;
}

.topology-legend,
.topology-empty {
  margin: 0;
  color: var(--color-text-muted);
  font-size: 0.72rem;
}

.topology-legend {
  padding: 0.45rem 0.75rem 0.65rem;
  border-top: 1px solid var(--color-border);
}

.topology-empty {
  padding: 2rem;
  border: 1px dashed var(--color-border);
  border-radius: var(--radius);
  text-align: center;
}
</style>
