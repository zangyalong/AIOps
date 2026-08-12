<script setup lang="ts">
import { computed } from 'vue'

type DiffKind = 'file' | 'hunk' | 'context' | 'addition' | 'deletion' | 'note'
type Segment = { text: string; changed: boolean }
type DiffRow = {
  kind: DiffKind
  oldLine: number | null
  newLine: number | null
  content: string
  segments?: Segment[]
}

const props = defineProps<{
  diff: string
}>()

function changedSegments(oldText: string, newText: string): [Segment[], Segment[]] {
  let prefix = 0
  const maxPrefix = Math.min(oldText.length, newText.length)
  while (prefix < maxPrefix && oldText[prefix] === newText[prefix]) prefix += 1

  let suffix = 0
  const maxSuffix = Math.min(oldText.length - prefix, newText.length - prefix)
  while (
    suffix < maxSuffix &&
    oldText[oldText.length - 1 - suffix] === newText[newText.length - 1 - suffix]
  ) {
    suffix += 1
  }

  const split = (text: string): Segment[] => {
    const middleEnd = suffix ? text.length - suffix : text.length
    return [
      { text: text.slice(0, prefix), changed: false },
      { text: text.slice(prefix, middleEnd), changed: true },
      { text: suffix ? text.slice(-suffix) : '', changed: false },
    ].filter((segment) => segment.text)
  }
  return [split(oldText), split(newText)]
}

function parseDiff(diff: string): DiffRow[] {
  const rows: DiffRow[] = []
  let oldLine: number | null = null
  let newLine: number | null = null

  for (const line of diff.replace(/\r\n/g, '\n').split('\n')) {
    if (!line && rows.length > 0 && rows.at(-1)?.content === '') continue
    if (line.startsWith('--- ') || line.startsWith('+++ ')) {
      rows.push({ kind: 'file', oldLine: null, newLine: null, content: line })
      continue
    }

    const hunk = line.match(/^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$/)
    if (hunk) {
      oldLine = Number(hunk[1])
      newLine = Number(hunk[3])
      rows.push({ kind: 'hunk', oldLine: null, newLine: null, content: line })
      continue
    }
    if (line.startsWith('@@')) {
      rows.push({ kind: 'hunk', oldLine: null, newLine: null, content: line })
      continue
    }
    if (line.startsWith('-')) {
      rows.push({ kind: 'deletion', oldLine, newLine: null, content: line.slice(1) })
      if (oldLine !== null) oldLine += 1
      continue
    }
    if (line.startsWith('+')) {
      rows.push({ kind: 'addition', oldLine: null, newLine, content: line.slice(1) })
      if (newLine !== null) newLine += 1
      continue
    }
    if (line.startsWith(' ')) {
      rows.push({ kind: 'context', oldLine, newLine, content: line.slice(1) })
      if (oldLine !== null) oldLine += 1
      if (newLine !== null) newLine += 1
      continue
    }
    rows.push({ kind: 'note', oldLine: null, newLine: null, content: line })
  }

  for (let index = 0; index < rows.length - 1; index += 1) {
    const removed = rows[index]
    const added = rows[index + 1]
    if (removed.kind === 'deletion' && added.kind === 'addition') {
      const [oldSegments, newSegments] = changedSegments(removed.content, added.content)
      removed.segments = oldSegments
      added.segments = newSegments
    }
  }
  return rows
}

const rows = computed(() => parseDiff(props.diff))
</script>

<template>
  <div class="diff-viewer" aria-label="文件差异预览">
    <div
      v-for="(row, index) in rows"
      :key="`${index}-${row.kind}`"
      class="diff-row"
      :class="row.kind"
    >
      <template v-if="row.kind === 'file' || row.kind === 'hunk' || row.kind === 'note'">
        <span class="diff-meta">{{ row.content || ' ' }}</span>
      </template>
      <template v-else>
        <span class="line-number">{{ row.oldLine ?? '' }}</span>
        <span class="line-number">{{ row.newLine ?? '' }}</span>
        <span class="diff-sign" aria-hidden="true">{{ row.kind === 'deletion' ? '−' : row.kind === 'addition' ? '+' : ' ' }}</span>
        <code class="diff-code">
          <template v-if="row.segments">
            <span
              v-for="(segment, segmentIndex) in row.segments"
              :key="segmentIndex"
              :class="{ changed: segment.changed }"
            >{{ segment.text }}</span>
          </template>
          <template v-else>{{ row.content }}</template>
        </code>
      </template>
    </div>
  </div>
</template>

<style scoped>
.diff-viewer {
  overflow: auto;
  border: 1px solid #d8dee4;
  border-radius: 7px;
  background: #ffffff;
  color: #27323a;
  font-family: var(--font-data, ui-monospace, SFMono-Regular, Menlo, monospace);
  font-size: 0.76rem;
  line-height: 1.65;
}

.diff-row {
  display: grid;
  min-width: max-content;
  grid-template-columns: 3.2rem 3.2rem 1.5rem minmax(28rem, 1fr);
}

.diff-row + .diff-row {
  border-top: 1px solid rgba(216, 222, 228, 0.42);
}

.line-number {
  padding: 0 0.65rem;
  border-right: 1px solid rgba(216, 222, 228, 0.8);
  color: #8b949e;
  text-align: right;
  user-select: none;
}

.diff-sign {
  padding-left: 0.45rem;
  font-weight: 700;
  user-select: none;
}

.diff-code {
  padding-right: 1rem;
  white-space: pre;
}

.diff-meta {
  grid-column: 1 / -1;
  padding: 0.25rem 0.75rem;
  white-space: pre-wrap;
}

.diff-row.file {
  background: #f6f8fa;
  color: #57606a;
}

.diff-row.hunk {
  background: #ddf4ff;
  color: #0969da;
}

.diff-row.addition {
  background: #dafbe1;
}

.diff-row.addition .diff-sign {
  color: #1a7f37;
}

.diff-row.deletion {
  background: #ffebe9;
}

.diff-row.deletion .diff-sign {
  color: #cf222e;
}

.diff-row.addition .changed {
  background: #aceebb;
}

.diff-row.deletion .changed {
  background: #ffcecb;
}

.diff-row.note {
  color: #6e7781;
  font-style: italic;
}
</style>
