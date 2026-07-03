export type ChatStreamEvent =
  | { type: 'token'; content: string }
  | { type: 'reset' }
  | { type: 'done'; conversation_id: string; title?: string }
  | { type: 'error'; message: string }

/**
 * 流式调用 /api/chat，边收边通过 async generator 吐出事件。
 * 后端发的是标准 SSE（data: <json>\n\n），POST 请求原生 EventSource 用不了，
 * 所以自己用 fetch + ReadableStream 读，按 \n\n 切事件、按 data: 行取 JSON。
 */
export async function* streamChatMessage(
  message: string,
  conversationId: string | null,
): AsyncGenerator<ChatStreamEvent> {
  const res = await fetch('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message, conversation_id: conversationId }),
  })
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
