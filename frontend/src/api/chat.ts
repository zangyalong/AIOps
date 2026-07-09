import type { PendingAction } from '../types/chat'

export type ChatStreamEvent =
  | { type: 'token'; content: string }
  | { type: 'reset' }
  | { type: 'tool_start'; id: string; tool: string }
  | { type: 'tool_end'; id: string }
  | {
      type: 'approval_required'
      thread_id: string
      conversation_id: string
      actions: PendingAction[]
      title?: string
    }
  | { type: 'done'; conversation_id: string; title?: string }
  | { type: 'error'; message: string }

/**
 * 读一个 SSE 响应体（标准 data: <json>\n\n），边收边通过 async generator 吐出事件。
 * POST 请求原生 EventSource 用不了，所以自己用 fetch + ReadableStream 读，按 \n\n 切事件、
 * 按 data: 行取 JSON。/chat 和 /chat/resume 两个流共用这段。
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

/** 发一条消息，流式返回后端事件。 */
export async function* streamChatMessage(
  message: string,
  conversationId: string | null,
): AsyncGenerator<ChatStreamEvent> {
  const res = await fetch('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message, conversation_id: conversationId }),
  })
  yield* readSseStream(res)
}

/**
 * 对一次挂在写操作审批门上的对话作出裁决（批准/拒绝），流式返回从挂起点续跑的事件。
 * threadId 是 approval_required 事件里带过来的那个，必须原样回传才能接上同一次挂起。
 */
export async function* resumeApproval(params: {
  conversationId: string
  threadId: string
  approved: boolean
  reason?: string
}): AsyncGenerator<ChatStreamEvent> {
  const res = await fetch('/api/chat/resume', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      conversation_id: params.conversationId,
      thread_id: params.threadId,
      approved: params.approved,
      reason: params.reason ?? null,
    }),
  })
  yield* readSseStream(res)
}
