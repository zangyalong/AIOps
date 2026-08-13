<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import type { Server, ServerStatus } from '../types/server'

interface QuickCommand {
  name: string
  title: string
  description: string
  prompt: string
  tone: 'inventory' | 'runtime' | 'observe' | 'change'
}

const QUICK_COMMANDS: readonly QuickCommand[] = [
  {
    name: 'servers',
    title: '查看服务器状态',
    description: '列出已纳管服务器及最近连接状态',
    prompt: '查看所有已纳管服务器的状态，并指出无法连接或尚未配置的服务器。',
    tone: 'inventory',
  },
  {
    name: 'services',
    title: '查看服务列表',
    description: '检查默认服务器的目录与实时运行状态',
    prompt: '查看默认服务器的服务目录和实时运行状态；如果没有默认服务器，请先列出可选服务器。',
    tone: 'runtime',
  },
  {
    name: 'discover',
    title: '发现未登记服务',
    description: '扫描 systemd、Docker 与监听服务',
    prompt: '扫描默认服务器上的 systemd、Docker 和监听服务，列出尚未登记到服务目录的候选项；先不要注册。',
    tone: 'inventory',
  },
  {
    name: 'register',
    title: '注册服务器服务',
    description: '整理候选项并进入服务目录审批',
    prompt: '扫描默认服务器上尚未登记的服务，整理候选项，并在确认目标和环境后发起注册到服务目录的审批。',
    tone: 'change',
  },
  {
    name: 'health',
    title: '检查资源状态',
    description: '核对 CPU、内存、磁盘和负载',
    prompt: '检查默认服务器当前的 CPU、内存、磁盘和负载状态，并标出需要关注的指标。',
    tone: 'observe',
  },
  {
    name: 'trend',
    title: '生成资源趋势',
    description: '生成最近 24 小时的指标序列图',
    prompt: '生成默认服务器最近 24 小时的 CPU、内存和磁盘趋势图，并提供工具生成的原始观测入口。',
    tone: 'observe',
  },
  {
    name: 'logs',
    title: '排查服务日志',
    description: '读取近期日志并归纳异常证据',
    prompt: '查看默认服务器上指定服务的近期日志并分析异常；如果服务不明确，请先列出服务供我选择。',
    tone: 'observe',
  },
  {
    name: 'alerts',
    title: '查看活跃告警',
    description: '汇总当前告警、级别与关联对象',
    prompt: '查看当前活跃告警，按严重级别汇总，并指出关联的服务器或服务。',
    tone: 'observe',
  },
]

const props = defineProps<{
  modelValue: string
  disabled: boolean
  pendingApproval: boolean
  servers: Server[]
  serversLoading: boolean
  serversError: string | null
  selectedServerId: string | null
}>()

const emit = defineEmits<{
  'update:modelValue': [value: string]
  'select-server': [serverId: string | null]
  'request-servers': []
  submit: []
}>()

const rootEl = ref<HTMLElement | null>(null)
const inputEl = ref<HTMLTextAreaElement | null>(null)
const serverSearchEl = ref<HTMLInputElement | null>(null)
const commandIndex = ref(0)
const commandDismissed = ref(false)
const serverMenuOpen = ref(false)
const serverSearch = ref('')

const slashQuery = computed(() => {
  if (!props.modelValue.startsWith('/') || /\s/.test(props.modelValue)) return null
  return props.modelValue.slice(1).toLocaleLowerCase().trim()
})

const filteredCommands = computed(() => {
  const query = slashQuery.value
  if (query === null) return []
  if (!query) return [...QUICK_COMMANDS]
  return QUICK_COMMANDS.filter((command) =>
    [command.name, command.title, command.description].some((value) =>
      value.toLocaleLowerCase().includes(query),
    ),
  )
})

const commandMenuOpen = computed(
  () => !props.disabled && !commandDismissed.value && slashQuery.value !== null,
)

const selectedServer = computed(
  () => props.servers.find((server) => server.id === props.selectedServerId) ?? null,
)

const filteredServers = computed(() => {
  const query = serverSearch.value.trim().toLocaleLowerCase()
  if (!query) return props.servers
  return props.servers.filter((server) =>
    [server.name, server.ip_address, server.description ?? ''].some((value) =>
      value.toLocaleLowerCase().includes(query),
    ),
  )
})

const statusLabels: Record<ServerStatus, string> = {
  active: '连接正常',
  unreachable: '无法连接',
  unconfigured: '未配置连接',
  unknown: '尚未检测',
}

function resizeInput() {
  nextTick(() => {
    const element = inputEl.value
    if (!element) return
    element.style.height = 'auto'
    const height = Math.min(element.scrollHeight, 144)
    element.style.height = `${Math.max(height, 44)}px`
    element.style.overflowY = element.scrollHeight > 144 ? 'auto' : 'hidden'
  })
}

function updateInput(event: Event) {
  emit('update:modelValue', (event.target as HTMLTextAreaElement).value)
}

function applyCommand(command: QuickCommand) {
  emit('update:modelValue', command.prompt)
  commandDismissed.value = true
  commandIndex.value = 0
  nextTick(() => inputEl.value?.focus())
}

function onComposerKeydown(event: KeyboardEvent) {
  if (event.isComposing) return
  if (commandMenuOpen.value) {
    if (event.key === 'ArrowDown') {
      event.preventDefault()
      if (filteredCommands.value.length > 0) {
        commandIndex.value = (commandIndex.value + 1) % filteredCommands.value.length
      }
      return
    }
    if (event.key === 'ArrowUp') {
      event.preventDefault()
      if (filteredCommands.value.length > 0) {
        commandIndex.value =
          (commandIndex.value - 1 + filteredCommands.value.length) %
          filteredCommands.value.length
      }
      return
    }
    if (event.key === 'Escape') {
      event.preventDefault()
      commandDismissed.value = true
      return
    }
    if (event.key === 'Enter' && !event.shiftKey && filteredCommands.value.length > 0) {
      event.preventDefault()
      applyCommand(filteredCommands.value[commandIndex.value])
      return
    }
  }
  if (event.key !== 'Enter' || event.shiftKey) return
  event.preventDefault()
  submitIfReady()
}

function submitIfReady() {
  if (
    props.disabled ||
    !props.modelValue.trim() ||
    (props.selectedServerId !== null && selectedServer.value === null)
  ) {
    return
  }
  emit('submit')
}

function toggleServerMenu() {
  if (props.disabled) return
  serverMenuOpen.value = !serverMenuOpen.value
  commandDismissed.value = true
  if (!serverMenuOpen.value) return
  serverSearch.value = ''
  emit('request-servers')
  nextTick(() => serverSearchEl.value?.focus())
}

function selectServer(serverId: string | null) {
  emit('select-server', serverId)
  serverMenuOpen.value = false
  nextTick(() => inputEl.value?.focus())
}

function onDocumentPointerDown(event: PointerEvent) {
  if (!rootEl.value?.contains(event.target as Node)) {
    serverMenuOpen.value = false
    commandDismissed.value = true
  }
}

watch(
  () => props.modelValue,
  () => {
    commandDismissed.value = false
    commandIndex.value = 0
    resizeInput()
  },
)

watch(filteredCommands, (commands) => {
  if (commandIndex.value >= commands.length) commandIndex.value = 0
})

watch(
  () => props.disabled,
  (disabled) => {
    if (disabled) serverMenuOpen.value = false
  },
)

document.addEventListener('pointerdown', onDocumentPointerDown)
onBeforeUnmount(() => document.removeEventListener('pointerdown', onDocumentPointerDown))
</script>

<template>
  <div ref="rootEl" class="composer-wrap">
    <div
      v-if="commandMenuOpen"
      id="quick-command-menu"
      class="composer-menu command-menu"
      role="listbox"
      aria-label="运维快捷功能"
    >
      <div class="menu-heading">
        <div>
          <span>快捷功能</span>
          <small>选择后仍可编辑再发送</small>
        </div>
        <span class="keyboard-help"><kbd>↑↓</kbd> 选择 <kbd>Enter</kbd> 应用</span>
      </div>
      <div v-if="filteredCommands.length" class="command-list">
        <button
          v-for="(command, index) in filteredCommands"
          :id="`quick-command-${command.name}`"
          :key="command.name"
          type="button"
          role="option"
          class="command-option"
          :class="{ active: index === commandIndex }"
          :aria-selected="index === commandIndex"
          @mouseenter="commandIndex = index"
          @click="applyCommand(command)"
        >
          <span class="command-signal" :class="`tone-${command.tone}`" aria-hidden="true" />
          <span class="command-copy">
            <strong>{{ command.title }}</strong>
            <small>{{ command.description }}</small>
          </span>
          <code>/{{ command.name }}</code>
        </button>
      </div>
      <p v-else class="menu-empty">没有匹配的快捷功能。继续输入可作为普通消息发送。</p>
    </div>

    <div v-if="serverMenuOpen" class="composer-menu server-menu" aria-label="选择默认服务器">
      <div class="menu-heading server-heading">
        <div>
          <span>默认服务器</span>
          <small>未明确点名其他目标时优先使用</small>
        </div>
        <button type="button" class="menu-close" aria-label="关闭服务器选择" @click="serverMenuOpen = false">×</button>
      </div>
      <label class="server-search">
        <span class="visually-hidden">搜索服务器</span>
        <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <circle cx="11" cy="11" r="6" />
          <path d="m16 16 4 4" />
        </svg>
        <input ref="serverSearchEl" v-model="serverSearch" type="search" placeholder="搜索名称或 IP" />
      </label>
      <div class="server-list" role="listbox">
        <button
          type="button"
          class="server-option clear-server-option"
          :class="{ selected: selectedServerId === null }"
          role="option"
          :aria-selected="selectedServerId === null"
          @click="selectServer(null)"
        >
          <span class="server-glyph none" aria-hidden="true">—</span>
          <span><strong>不指定服务器</strong><small>每次根据消息确认目标</small></span>
          <span v-if="selectedServerId === null" class="selection-check" aria-hidden="true">✓</span>
        </button>
        <p v-if="serversLoading" class="menu-empty">正在读取服务器目录…</p>
        <div v-else-if="serversError" class="menu-error">
          <span>{{ serversError }}</span>
          <button type="button" @click="$emit('request-servers')">重试</button>
        </div>
        <p v-else-if="filteredServers.length === 0" class="menu-empty">
          {{ servers.length === 0 ? '还没有已纳管服务器。' : '没有匹配的服务器。' }}
        </p>
        <template v-else>
          <button
            v-for="server in filteredServers"
            :key="server.id"
            type="button"
            class="server-option"
            :class="{ selected: server.id === selectedServerId }"
            role="option"
            :aria-selected="server.id === selectedServerId"
            @click="selectServer(server.id)"
          >
            <span class="server-glyph" aria-hidden="true">
              <i :class="`status-${server.status}`" />
            </span>
            <span class="server-copy">
              <strong>{{ server.name }}</strong>
              <small>{{ server.ip_address }} · {{ statusLabels[server.status] }}</small>
            </span>
            <span v-if="server.id === selectedServerId" class="selection-check" aria-hidden="true">✓</span>
          </button>
        </template>
      </div>
    </div>

    <form class="composer" @submit.prevent="submitIfReady">
      <div v-if="selectedServerId" class="target-strip">
        <span class="target-label">默认目标</span>
        <span class="target-chip" :class="{ unresolved: !selectedServer }">
          <i :class="selectedServer ? `status-${selectedServer.status}` : 'status-unknown'" aria-hidden="true" />
          <strong v-if="selectedServer">{{ selectedServer.name }}</strong>
          <strong v-else>
            {{ serversLoading ? '正在确认服务器…' : serversError ? '服务器目录读取失败' : '服务器已不可用' }}
          </strong>
          <small v-if="selectedServer">{{ selectedServer.ip_address }}</small>
          <button
            type="button"
            :disabled="disabled"
            :aria-label="`移除默认服务器${selectedServer ? ` ${selectedServer.name}` : ''}`"
            @click="selectServer(null)"
          >×</button>
        </span>
      </div>
      <textarea
        ref="inputEl"
        :value="modelValue"
        rows="1"
        :placeholder="pendingApproval ? '请先处理上面的确认…' : '描述目标、服务器或异常现象…'"
        :disabled="disabled"
        aria-label="给运维助手发送消息"
        aria-autocomplete="list"
        :aria-expanded="commandMenuOpen"
        :aria-controls="commandMenuOpen ? 'quick-command-menu' : undefined"
        :aria-activedescendant="
          commandMenuOpen && filteredCommands[commandIndex]
            ? `quick-command-${filteredCommands[commandIndex].name}`
            : undefined
        "
        @input="updateInput"
        @keydown="onComposerKeydown"
      />
      <div class="composer-toolbar">
        <div class="composer-tools">
          <button
            type="button"
            class="add-target"
            :class="{ active: serverMenuOpen || !!selectedServerId }"
            :disabled="disabled"
            aria-label="选择默认服务器"
            :aria-expanded="serverMenuOpen"
            @click="toggleServerMenu"
          >
            <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
              <path d="M12 5v14M5 12h14" />
            </svg>
          </button>
          <span><kbd>/</kbd> 快捷功能</span>
        </div>
        <span class="send-help">Enter 发送 · Shift + Enter 换行</span>
        <button
          type="submit"
          class="send-button"
          :disabled="disabled || !modelValue.trim() || (!!selectedServerId && !selectedServer)"
          :title="selectedServerId && !selectedServer ? '正在确认默认服务器，请稍候或移除该目标' : undefined"
          aria-label="发送消息"
        >
          <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
            <path d="M12 19V5M6.5 10.5 12 5l5.5 5.5" />
          </svg>
        </button>
      </div>
    </form>
  </div>
</template>

<style scoped>
.composer-wrap {
  position: relative;
  width: 100%;
  max-width: 860px;
  margin: 0 auto;
}

.composer {
  width: 100%;
  padding: 7px;
  border: 1px solid var(--color-border-strong);
  border-radius: 14px;
  background: #ffffff;
  box-shadow: 0 8px 28px rgba(16, 39, 51, 0.08);
  transition: border-color 160ms ease, box-shadow 160ms ease;
}

.composer:focus-within {
  border-color: color-mix(in srgb, var(--color-accent) 65%, var(--color-border));
  box-shadow: 0 0 0 3px rgba(22, 137, 154, 0.1), 0 10px 32px rgba(16, 39, 51, 0.1);
}

.composer textarea {
  display: block;
  width: 100%;
  min-height: 44px;
  max-height: 144px;
  padding: 0.62rem 0.72rem 0.35rem;
  border: 0;
  border-radius: 8px;
  resize: none;
  background: transparent;
  box-shadow: none;
  font: inherit;
  line-height: 1.55;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  outline: none;
}

.composer textarea:focus,
.composer textarea:hover {
  border: 0;
  background: transparent;
  box-shadow: none;
}

.composer textarea:disabled {
  color: var(--color-text-muted);
}

.composer-toolbar {
  min-height: 36px;
  display: flex;
  align-items: center;
  gap: 0.65rem;
  padding: 0 2px 0 3px;
}

.composer-tools {
  display: flex;
  align-items: center;
  gap: 0.55rem;
}

.composer-tools > span,
.send-help {
  color: var(--color-text-muted);
  font-size: 0.68rem;
}

kbd {
  min-width: 18px;
  display: inline-grid;
  place-items: center;
  padding: 1px 4px;
  border: 1px solid var(--color-border);
  border-radius: 4px;
  background: #f5f7f8;
  color: #53636c;
  font-family: var(--font-data);
  font-size: 0.64rem;
  box-shadow: 0 1px 0 #d9e0e3;
}

.add-target,
.send-button {
  display: grid;
  width: 34px;
  height: 34px;
  flex: 0 0 34px;
  place-items: center;
  border-radius: 9px;
}

.add-target {
  border: 1px solid var(--color-border);
  background: #f8fafb;
  color: var(--color-text);
}

.add-target:hover,
.add-target.active {
  border-color: color-mix(in srgb, var(--color-accent) 48%, var(--color-border));
  background: var(--color-interactive-bg);
  color: var(--color-accent);
}

.add-target svg,
.send-button svg {
  width: 18px;
  height: 18px;
  stroke: currentColor;
  stroke-width: 1.8;
  stroke-linecap: round;
  stroke-linejoin: round;
}

.send-help {
  margin-left: auto;
}

.send-button {
  border: 0;
  background: var(--color-accent);
  color: #ffffff;
}

.send-button:hover:not(:disabled) {
  background: var(--color-accent-hover);
}

.send-button:disabled,
.add-target:disabled {
  opacity: 0.48;
}

.target-strip {
  display: flex;
  align-items: center;
  gap: 0.55rem;
  padding: 3px 5px 2px;
}

.target-label {
  color: var(--color-text-muted);
  font-family: var(--font-data);
  font-size: 0.61rem;
  letter-spacing: 0.06em;
  text-transform: uppercase;
}

.target-chip {
  min-width: 0;
  display: inline-flex;
  align-items: center;
  gap: 0.42rem;
  padding: 0.28rem 0.35rem 0.28rem 0.55rem;
  border: 1px solid #b9dadd;
  border-radius: 7px;
  background: #edf7f8;
  font-size: 0.74rem;
}

.target-chip.unresolved {
  border-color: color-mix(in srgb, var(--color-status-unconfigured) 45%, var(--color-border));
  background: color-mix(in srgb, var(--color-status-unconfigured) 8%, #ffffff);
}

.target-chip > i,
.server-glyph i {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--color-status-untested);
}

.target-chip small {
  color: var(--color-text-muted);
  font-family: var(--font-data);
  font-size: 0.64rem;
}

.target-chip button {
  width: 22px;
  height: 22px;
  display: grid;
  place-items: center;
  padding: 0;
  border: 0;
  border-radius: 5px;
  background: transparent;
  color: var(--color-text-muted);
  line-height: 1;
}

.target-chip button:hover {
  background: rgba(20, 35, 44, 0.08);
  color: var(--color-text);
}

.composer-menu {
  position: absolute;
  z-index: 50;
  bottom: calc(100% + 10px);
  overflow: hidden;
  border: 1px solid var(--color-border-strong);
  border-radius: 12px;
  background: rgba(255, 255, 255, 0.98);
  box-shadow: var(--shadow-lg);
  backdrop-filter: blur(14px);
}

.command-menu {
  inset-inline: 0;
}

.server-menu {
  left: 0;
  width: min(390px, 100%);
}

.menu-heading {
  min-height: 54px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  padding: 0.72rem 0.9rem;
  border-bottom: 1px solid var(--color-border);
  background: #f7f9fa;
}

.menu-heading > div {
  min-width: 0;
  display: flex;
  flex-direction: column;
}

.menu-heading span:first-child {
  font-size: 0.82rem;
  font-weight: 650;
}

.menu-heading small {
  color: var(--color-text-muted);
  font-size: 0.68rem;
}

.keyboard-help {
  flex-shrink: 0;
  color: var(--color-text-muted);
  font-size: 0.65rem;
}

.command-list,
.server-list {
  max-height: min(420px, 52vh);
  overflow-y: auto;
  padding: 5px;
}

.command-option,
.server-option {
  width: 100%;
  display: grid;
  align-items: center;
  gap: 0.75rem;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: var(--color-text);
  text-align: left;
}

.command-option {
  grid-template-columns: 9px minmax(0, 1fr) auto;
  min-height: 51px;
  padding: 0.45rem 0.68rem;
}

.command-option:hover,
.command-option.active,
.server-option:hover,
.server-option.selected {
  background: #edf2f4;
}

.command-signal {
  width: 7px;
  height: 28px;
  border-radius: 99px;
  background: #74878f;
}

.command-signal.tone-runtime { background: #287d70; }
.command-signal.tone-observe { background: var(--color-accent); }
.command-signal.tone-change { background: #d9872c; }

.command-copy,
.server-copy,
.server-option > span:nth-child(2) {
  min-width: 0;
  display: flex;
  flex-direction: column;
}

.command-copy strong,
.server-option strong {
  font-size: 0.81rem;
  font-weight: 620;
}

.command-copy small,
.server-option small {
  overflow: hidden;
  color: var(--color-text-muted);
  font-size: 0.69rem;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.command-option code {
  color: #6b7e87;
  font-size: 0.67rem;
}

.server-heading {
  padding-right: 0.65rem;
}

.menu-close {
  width: 28px;
  height: 28px;
  padding: 0;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--color-text-muted);
  font-size: 1.05rem;
}

.menu-close:hover {
  background: #e7ecee;
  color: var(--color-text);
}

.server-search {
  position: relative;
  display: block;
  padding: 0.65rem 0.7rem 0.25rem;
}

.server-search svg {
  position: absolute;
  top: 1.22rem;
  left: 1.35rem;
  width: 15px;
  height: 15px;
  stroke: var(--color-text-muted);
  stroke-width: 1.7;
  stroke-linecap: round;
}

.server-search input {
  width: 100%;
  min-height: 36px;
  padding: 0.42rem 0.65rem 0.42rem 2rem;
  border-color: var(--color-border);
  border-radius: 7px;
  font-size: 0.76rem;
}

.server-option {
  grid-template-columns: 32px minmax(0, 1fr) 20px;
  min-height: 54px;
  padding: 0.42rem 0.58rem;
}

.server-glyph {
  width: 30px;
  height: 30px;
  display: grid;
  place-items: center;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: #ffffff;
}

.server-glyph.none {
  color: var(--color-text-muted);
  font-family: var(--font-data);
}

.selection-check {
  color: var(--color-accent);
  font-weight: 700;
  text-align: center;
}

.status-active { background: var(--color-status-active) !important; }
.status-unreachable { background: var(--color-status-unreachable) !important; }
.status-unconfigured { background: var(--color-status-unconfigured) !important; }
.status-unknown { background: var(--color-status-untested) !important; }

.menu-empty,
.menu-error {
  margin: 0;
  padding: 1.1rem;
  color: var(--color-text-muted);
  font-size: 0.75rem;
  text-align: center;
}

.menu-error {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
  color: var(--color-status-unreachable);
  text-align: left;
}

.menu-error button {
  padding: 0.3rem 0.55rem;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  background: #ffffff;
  color: var(--color-text);
  font-size: 0.7rem;
}

.visually-hidden {
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

@media (max-width: 620px) {
  .keyboard-help,
  .send-help,
  .target-chip small {
    display: none;
  }

  .command-option {
    grid-template-columns: 8px minmax(0, 1fr);
  }

  .command-option code {
    display: none;
  }
}
</style>
