export interface ToolCall {
  name: string
  args?: Record<string, unknown>
  id?: string
  type?: string
}

export interface ChatMessage {
  role: 'user' | 'assistant' | 'tool'
  content: string
  // assistant 调工具时带上它的 tool_calls；tool 消息带它回应的 tool_call_id。刷新后据此重建过程。
  tool_calls?: ToolCall[] | null
  tool_call_id?: string | null
}

export interface ConversationSummary {
  id: string
  title: string
  created_at: string
  updated_at: string
}
