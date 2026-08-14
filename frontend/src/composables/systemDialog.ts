import { readonly, shallowRef } from 'vue'

export type SystemDialogKind = 'confirm' | 'prompt' | 'message'
export type SystemDialogTone = 'neutral' | 'warning' | 'danger'

interface SystemDialogBaseOptions {
  title: string
  message: string
  details?: string
  detailsLabel?: string
  confirmLabel?: string
  tone?: SystemDialogTone
}

export interface ConfirmDialogOptions extends SystemDialogBaseOptions {
  cancelLabel?: string
}

export interface PromptDialogOptions extends SystemDialogBaseOptions {
  cancelLabel?: string
  inputLabel: string
  initialValue?: string
  placeholder?: string
  required?: boolean
  maxLength?: number
}

export type MessageDialogOptions = SystemDialogBaseOptions

export interface SystemDialogState extends SystemDialogBaseOptions {
  id: number
  kind: SystemDialogKind
  tone: SystemDialogTone
  cancelLabel: string
  inputLabel: string
  initialValue: string
  placeholder: string
  required: boolean
  maxLength: number
}

type DialogResult = boolean | string | null

interface PendingDialog {
  state: SystemDialogState
  resolve: (result: DialogResult) => void
}

const activeDialog = shallowRef<SystemDialogState | null>(null)
const queue: PendingDialog[] = []
let activeResolver: PendingDialog['resolve'] | null = null
let nextId = 1

export const systemDialogState = readonly(activeDialog)

function makeState(
  kind: SystemDialogKind,
  options: SystemDialogBaseOptions & Partial<PromptDialogOptions & ConfirmDialogOptions>,
): SystemDialogState {
  return {
    id: nextId++,
    kind,
    title: options.title,
    message: options.message,
    details: options.details,
    detailsLabel: options.detailsLabel,
    confirmLabel: options.confirmLabel,
    tone: options.tone ?? 'neutral',
    cancelLabel: options.cancelLabel ?? '取消',
    inputLabel: options.inputLabel ?? '',
    initialValue: options.initialValue ?? '',
    placeholder: options.placeholder ?? '',
    required: options.required ?? false,
    maxLength: options.maxLength ?? 200,
  }
}

function openNextDialog() {
  if (activeDialog.value || queue.length === 0) return
  const next = queue.shift()
  if (!next) return
  activeResolver = next.resolve
  activeDialog.value = next.state
}

function enqueueDialog(
  state: SystemDialogState,
  resolve: PendingDialog['resolve'],
) {
  queue.push({ state, resolve })
  openNextDialog()
}

export function settleSystemDialog(result: DialogResult) {
  if (!activeDialog.value || !activeResolver) return
  const resolve = activeResolver
  activeResolver = null
  activeDialog.value = null
  resolve(result)
  queueMicrotask(openNextDialog)
}

export function confirmDialog(options: ConfirmDialogOptions): Promise<boolean> {
  return new Promise((resolve) => {
    enqueueDialog(makeState('confirm', options), (result) => resolve(result === true))
  })
}

export function promptDialog(options: PromptDialogOptions): Promise<string | null> {
  return new Promise((resolve) => {
    enqueueDialog(makeState('prompt', options), (result) => {
      resolve(typeof result === 'string' ? result : null)
    })
  })
}

export function messageDialog(options: MessageDialogOptions): Promise<void> {
  return new Promise((resolve) => {
    enqueueDialog(makeState('message', options), () => resolve())
  })
}
