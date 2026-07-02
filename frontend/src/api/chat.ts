export async function sendChatMessage(message: string): Promise<string> {
  const res = await fetch('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message }),
  })
  if (!res.ok) {
    throw new Error(`对话请求失败：${res.status}`)
  }
  const data: { reply: string } = await res.json()
  return data.reply
}
