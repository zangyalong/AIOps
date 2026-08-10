import type { PendingAction } from '../types/chat'

export interface ChatRun {
  run_id: string
  conversation_id: string
  status: 'running' | 'waiting_approval' | 'completed' | 'failed'
  title?: string | null
}

export type ChatStreamEvent =
  | { type: 'narration'; seq: number; content: string }
  | { type: 'token'; seq: number; content: string }
  | { type: 'tool_start'; seq: number; id: string; tool: string }
  | { type: 'tool_end'; seq: number; id: string }
  | {
      type: 'approval_required'
      seq: number
      thread_id: string
      conversation_id: string
      actions: PendingAction[]
      title?: string
    }
  | {
      type: 'approval_resolved'
      seq: number
      thread_id: string
      decisions: OperationApprovalDecision[]
    }
  | { type: 'done'; seq: number; conversation_id: string; title?: string }
  | { type: 'error'; seq: number; message: string }

/**
 * 读一个 SSE 响应体（标准 data: <json>\n\n），边收边通过 async generator 吐出事件。
 * 使用 fetch + ReadableStream 是为了支持 AbortSignal 和事件序号续传；按 \n\n 切事件、
 * 再从 data: 行解析 Chat Run 事件。
 */
async function* readSseStream(res: Response): AsyncGenerator<ChatStreamEvent> {
  if (!res.ok || !res.body) {
    throw new Error(`对话请求失败：${res.status}`)
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })

    let boundary = buffer.indexOf('\n\n')
    while (boundary !== -1) {
      const rawEvent = buffer.slice(0, boundary)
      buffer = buffer.slice(boundary + 2)
      const dataLine = rawEvent
        .split('\n')
        .find((line) => line.startsWith('data: '))
      if (dataLine) {
        yield JSON.parse(dataLine.slice('data: '.length)) as ChatStreamEvent
      }
      boundary = buffer.indexOf('\n\n')
    }
  }
}

async function responseError(res: Response, fallback: string): Promise<Error> {
  const payload = (await res.json().catch(() => null)) as { detail?: string } | null
  return new Error(payload?.detail || `${fallback}：${res.status}`)
}

/** 创建独立后台运行；返回后即使浏览器断开，Agent 也会继续执行。 */
export async function startChatMessage(
  message: string,
  conversationId: string | null,
): Promise<ChatRun> {
  const res = await fetch('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message, conversation_id: conversationId }),
  })
  if (!res.ok) throw await responseError(res, '创建对话任务失败')
  return res.json()
}

/** 查询某个对话是否仍有运行中或等待审批的任务。 */
export async function fetchActiveChatRun(conversationId: string): Promise<ChatRun | null> {
  const res = await fetch(`/api/chat/runs/active/${conversationId}`)
  if (res.status === 404) return null
  if (!res.ok) throw await responseError(res, '查询对话任务失败')
  return res.json()
}

/** 订阅或重连一个后台运行；after 用于审批后只接收新的事件。 */
export async function* streamChatRun(
  runId: string,
  after = 0,
  signal?: AbortSignal,
): AsyncGenerator<ChatStreamEvent> {
  const res = await fetch(`/api/chat/runs/${runId}/events?after=${after}`, { signal })
  yield* readSseStream(res)
}

/**
 * 对一次挂在写操作审批门上的运行作出裁决。恢复接口仍使用原 runId，随后由调用方从
 * 已消费的事件序号继续订阅；threadId 必须和 approval_required 原样对应。
 */
export async function resumeApproval(params: {
  runId: string
  conversationId: string
  threadId: string
  decisions: OperationApprovalDecision[]
}): Promise<ChatRun> {
  const res = await fetch('/api/chat/resume', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      run_id: params.runId,
      conversation_id: params.conversationId,
      thread_id: params.threadId,
      decisions: params.decisions,
    }),
  })
  if (!res.ok) throw await responseError(res, '恢复审批任务失败')
  return res.json()
}

export interface OperationApprovalDecision {
  tool_call_id: string
  operation_id: string
  approved: boolean
  second_confirmation: boolean
  reason?: string | null
}
