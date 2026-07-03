interface ChatApiResponse {
  reply: string
  thread_id: string
}

export async function sendChatMessage(
  message: string,
  threadId: string | null,
): Promise<ChatApiResponse> {
  const res = await fetch('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message, thread_id: threadId }),
  })
  if (!res.ok) {
    throw new Error(`对话请求失败：${res.status}`)
  }
  return res.json()
}
