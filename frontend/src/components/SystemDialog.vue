<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import {
  settleSystemDialog,
  systemDialogState,
} from '../composables/systemDialog'

const panel = ref<HTMLElement | null>(null)
const cancelButton = ref<HTMLButtonElement | null>(null)
const primaryButton = ref<HTMLButtonElement | null>(null)
const textInput = ref<HTMLInputElement | null>(null)
const inputValue = ref('')
const validationError = ref<string | null>(null)
let previousFocus: HTMLElement | null = null

const kindLabel = computed(() => {
  const dialog = systemDialogState.value
  if (!dialog) return ''
  if (dialog.kind === 'prompt') return 'AIOPS / 需要输入'
  if (dialog.kind === 'message') return 'AIOPS / 系统提示'
  if (dialog.tone === 'danger') return 'AIOPS / 危险操作确认'
  if (dialog.tone === 'warning') return 'AIOPS / 变更确认'
  return 'AIOPS / 操作确认'
})

const defaultConfirmLabel = computed(() => {
  const dialog = systemDialogState.value
  if (!dialog) return '确认'
  if (dialog.kind === 'message') return '知道了'
  if (dialog.kind === 'prompt') return '确认输入'
  return '确认'
})

watch(
  systemDialogState,
  async (dialog) => {
    if (!dialog) {
      previousFocus?.focus()
      previousFocus = null
      return
    }
    previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null
    inputValue.value = dialog.initialValue
    validationError.value = null
    await nextTick()
    if (dialog.kind === 'prompt') {
      textInput.value?.focus()
      textInput.value?.select()
    } else if (dialog.tone === 'danger' && dialog.kind !== 'message') {
      cancelButton.value?.focus()
    } else {
      primaryButton.value?.focus()
    }
  },
)

onBeforeUnmount(() => previousFocus?.focus())

function cancelDialog() {
  const dialog = systemDialogState.value
  if (!dialog) return
  settleSystemDialog(dialog.kind === 'confirm' ? false : null)
}

function submitDialog() {
  const dialog = systemDialogState.value
  if (!dialog) return
  if (dialog.kind === 'prompt') {
    if (dialog.required && !inputValue.value.trim()) {
      validationError.value = `${dialog.inputLabel}不能为空`
      textInput.value?.focus()
      return
    }
    settleSystemDialog(inputValue.value)
    return
  }
  settleSystemDialog(true)
}

function handleEnter(event: KeyboardEvent) {
  if (event.target instanceof HTMLButtonElement) {
    event.preventDefault()
    event.target.click()
    return
  }
  event.preventDefault()
  submitDialog()
}

function trapFocus(event: KeyboardEvent) {
  if (event.key !== 'Tab' || !panel.value) return
  const focusable = Array.from(
    panel.value.querySelectorAll<HTMLElement>(
      'button:not(:disabled), input:not(:disabled), [tabindex]:not([tabindex="-1"])',
    ),
  )
  if (focusable.length === 0) return
  const first = focusable[0]
  const last = focusable[focusable.length - 1]
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault()
    last?.focus()
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault()
    first?.focus()
  }
}
</script>

<template>
  <Teleport to="body">
    <Transition name="system-dialog">
      <div
        v-if="systemDialogState"
        class="system-dialog-backdrop"
        role="presentation"
        @mousedown.self="cancelDialog"
      >
        <section
          ref="panel"
          class="system-dialog-panel"
          :class="`tone-${systemDialogState.tone}`"
          :role="systemDialogState.tone === 'danger' ? 'alertdialog' : 'dialog'"
          aria-modal="true"
          aria-labelledby="system-dialog-title"
          aria-describedby="system-dialog-message"
          @keydown.esc.prevent="cancelDialog"
          @keydown.enter="handleEnter"
          @keydown="trapFocus"
        >
          <div class="dialog-rail" aria-hidden="true" />
          <header class="dialog-header">
            <div>
              <span class="dialog-kicker">{{ kindLabel }}</span>
              <h2 id="system-dialog-title">{{ systemDialogState.title }}</h2>
            </div>
            <button type="button" class="dialog-close" aria-label="取消并关闭" @click="cancelDialog">
              ×
            </button>
          </header>

          <div class="dialog-body">
            <p id="system-dialog-message" class="dialog-message">
              {{ systemDialogState.message }}
            </p>
            <div v-if="systemDialogState.details" class="dialog-scope">
              <span>{{ systemDialogState.detailsLabel || '操作范围' }}</span>
              <code>{{ systemDialogState.details }}</code>
            </div>
            <label v-if="systemDialogState.kind === 'prompt'" class="dialog-field">
              <span>{{ systemDialogState.inputLabel }}</span>
              <input
                ref="textInput"
                v-model="inputValue"
                type="text"
                :placeholder="systemDialogState.placeholder"
                :maxlength="systemDialogState.maxLength"
                :aria-invalid="!!validationError"
                @input="validationError = null"
              />
              <small v-if="validationError" role="alert">{{ validationError }}</small>
            </label>
          </div>

          <footer class="dialog-actions">
            <button
              v-if="systemDialogState.kind !== 'message'"
              ref="cancelButton"
              type="button"
              class="dialog-cancel"
              @click="cancelDialog"
            >
              {{ systemDialogState.cancelLabel }}
            </button>
            <button
              ref="primaryButton"
              type="button"
              class="dialog-confirm"
              @click="submitDialog"
            >
              {{ systemDialogState.confirmLabel || defaultConfirmLabel }}
            </button>
          </footer>
        </section>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.system-dialog-backdrop {
  position: fixed;
  z-index: 1000;
  inset: 0;
  display: grid;
  place-items: center;
  padding: 1.25rem;
  background: rgba(10, 30, 40, 0.48);
  backdrop-filter: blur(5px);
}

.system-dialog-panel {
  position: relative;
  width: min(520px, 100%);
  overflow: hidden;
  border: 1px solid var(--color-border-strong);
  border-radius: 14px;
  background: var(--color-surface-strong);
  box-shadow: var(--shadow-lg);
}

.dialog-rail {
  position: absolute;
  top: 0;
  bottom: 0;
  left: 0;
  width: 4px;
  background: var(--color-accent);
}

.tone-warning .dialog-rail {
  background: var(--color-status-unconfigured);
}

.tone-danger .dialog-rail {
  background: var(--color-status-unreachable);
}

.dialog-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 1rem;
  padding: 1.35rem 1.4rem 1rem 1.55rem;
  border-bottom: 1px solid var(--color-border);
  background: #f6f9fa;
}

.dialog-kicker {
  display: block;
  margin-bottom: 0.28rem;
  color: var(--color-accent);
  font-family: var(--font-data);
  font-size: 0.62rem;
  font-weight: 650;
  letter-spacing: 0.1em;
}

.tone-warning .dialog-kicker {
  color: #a15c17;
}

.tone-danger .dialog-kicker {
  color: #b42318;
}

.dialog-header h2 {
  margin: 0;
  color: var(--color-text);
  font-family: var(--font-display);
  font-size: 1.28rem;
  font-weight: 650;
  line-height: 1.25;
}

.dialog-close {
  flex: 0 0 auto;
  width: 32px;
  height: 32px;
  border: 1px solid transparent;
  border-radius: 7px;
  color: var(--color-text-muted);
  background: transparent;
  font-size: 1.25rem;
  line-height: 1;
}

.dialog-close:hover {
  border-color: var(--color-border);
  color: var(--color-text);
  background: var(--color-surface-inset);
}

.dialog-body {
  padding: 1.25rem 1.4rem 0.25rem 1.55rem;
}

.dialog-message {
  margin: 0;
  color: var(--color-text);
  font-size: 0.92rem;
  line-height: 1.7;
  white-space: pre-line;
}

.dialog-scope {
  display: grid;
  gap: 0.38rem;
  margin-top: 1rem;
  border: 1px solid var(--color-border);
  border-left: 3px solid var(--color-border-strong);
  border-radius: 8px;
  padding: 0.7rem 0.8rem;
  background: var(--color-surface-inset);
}

.dialog-scope span,
.dialog-field > span {
  color: var(--color-text-muted);
  font-family: var(--font-data);
  font-size: 0.64rem;
  font-weight: 600;
  letter-spacing: 0.06em;
}

.dialog-scope code {
  overflow-wrap: anywhere;
  color: var(--color-text);
  font-size: 0.78rem;
  line-height: 1.55;
  white-space: pre-wrap;
}

.dialog-field {
  display: grid;
  gap: 0.4rem;
  margin-top: 1rem;
}

.dialog-field input {
  width: 100%;
}

.dialog-field small {
  color: #b42318;
  font-size: 0.74rem;
}

.dialog-actions {
  display: flex;
  justify-content: flex-end;
  gap: 0.55rem;
  padding: 1.15rem 1.4rem 1.3rem 1.55rem;
}

.dialog-actions button {
  min-width: 92px;
  min-height: 40px;
  border-radius: 7px;
  padding: 0.52rem 0.9rem;
  font-size: 0.84rem;
  font-weight: 600;
}

.dialog-cancel {
  border: 1px solid var(--color-border-strong);
  color: var(--color-text);
  background: var(--color-surface-strong);
}

.dialog-cancel:hover {
  border-color: #9cabb2;
  background: var(--color-surface-inset);
}

.dialog-confirm {
  border: 1px solid var(--color-accent);
  color: var(--color-accent-contrast);
  background: var(--color-accent);
}

.dialog-confirm:hover {
  border-color: var(--color-accent-hover);
  background: var(--color-accent-hover);
}

.tone-warning .dialog-confirm {
  border-color: #c9731d;
  background: #c9731d;
}

.tone-warning .dialog-confirm:hover {
  border-color: #a95d14;
  background: #a95d14;
}

.tone-danger .dialog-confirm {
  border-color: #d92d20;
  background: #d92d20;
}

.tone-danger .dialog-confirm:hover {
  border-color: #b42318;
  background: #b42318;
}

.system-dialog-enter-active,
.system-dialog-leave-active {
  transition: opacity 150ms ease;
}

.system-dialog-enter-active .system-dialog-panel,
.system-dialog-leave-active .system-dialog-panel {
  transition: transform 170ms ease, opacity 150ms ease;
}

.system-dialog-enter-from,
.system-dialog-leave-to {
  opacity: 0;
}

.system-dialog-enter-from .system-dialog-panel,
.system-dialog-leave-to .system-dialog-panel {
  opacity: 0;
  transform: translateY(8px) scale(0.985);
}

@media (max-width: 560px) {
  .system-dialog-backdrop {
    align-items: end;
    padding: 0.65rem;
  }

  .system-dialog-panel {
    border-radius: 13px;
  }

  .dialog-header,
  .dialog-body,
  .dialog-actions {
    padding-right: 1rem;
    padding-left: 1.15rem;
  }

  .dialog-actions {
    display: grid;
    grid-template-columns: 1fr 1fr;
  }

  .dialog-actions button:only-child {
    grid-column: 1 / -1;
  }
}
</style>
