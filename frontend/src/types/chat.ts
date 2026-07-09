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

// 一个待人工审批的写操作（后端 approval_required 事件带过来）。带 kind——审批是通用机制，
// 不只认命令：shell_command 渲染成命令块，将来的结构化入库（写告警入表等）渲染成表名+列值。
export interface PendingAction {
  tool_call_id: string
  tool: string
  kind: string
  title: string
  command?: string
  server_name?: string
  args?: Record<string, unknown>
}
