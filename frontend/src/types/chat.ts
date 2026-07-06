// assistant 消息"最终答案之前"的有序过程步骤：一段旁白文字，或一次工具调用
export type ChatStep =
  | { type: 'text'; content: string }
  | { type: 'tool'; tool: string }

export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
  // 最终答案之前的有序过程，刷新后据此原样重建；user 消息/无中间过程的回答为 null
  steps?: ChatStep[] | null
}

export interface ConversationSummary {
  id: string
  title: string
  created_at: string
  updated_at: string
}
