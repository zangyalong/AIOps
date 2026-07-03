export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
}

export interface ConversationSummary {
  id: string
  title: string
  created_at: string
  updated_at: string
}
