import type { ChatMessage, ConversationSummary } from '../types/chat'

export async function fetchConversations(): Promise<ConversationSummary[]> {
  const res = await fetch('/api/conversations')
  if (!res.ok) {
    throw new Error(`获取对话列表失败：${res.status}`)
  }
  return res.json()
}

export async function fetchConversationDetail(
  conversationId: string,
): Promise<{ conversation: ConversationSummary; messages: ChatMessage[] }> {
  const res = await fetch(`/api/conversations/${conversationId}`)
  if (!res.ok) {
    throw new Error(`获取对话内容失败：${res.status}`)
  }
  return res.json()
}
