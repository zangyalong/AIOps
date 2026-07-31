export interface ToolCall {
  name: string
  args?: Record<string, unknown>
  id?: string
  type?: string
}

export interface ChatMessage {
  role: 'user' | 'assistant' | 'tool'
  content: string
  run_id?: string | null
  // assistant 调工具时带上它的 tool_calls；tool 消息带它回应的 tool_call_id。刷新后据此重建过程。
  tool_calls?: ToolCall[] | null
  tool_call_id?: string | null
}

export interface ChatRunHistory {
  id: string
  status: 'running' | 'waiting_approval' | 'completed' | 'failed'
  error_message: string | null
  started_at: string
  finished_at: string | null
}

export interface ConversationSummary {
  id: string
  title: string
  created_at: string
  updated_at: string
}

// 一个待人工审批的写操作（后端 approval_required 事件带过来）。带 kind——审批是通用机制，
// 不只认命令：service_restart 渲染服务重启动作，将来的结构化写入也能携带自己的展示字段。
export interface PendingAction {
  tool_call_id: string
  tool: string
  kind: string
  title: string
  command?: string
  server_name?: string
  service_name?: string
  environment?: string
  service_names?: string[]
  risk_level?: 'medium' | 'high' | 'critical'
  args?: Record<string, unknown>
}
