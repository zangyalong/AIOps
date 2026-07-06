// 服务端持久化的服务器状态：unknown=还没测过，其余是最近一次测试连接的结果
export type ServerStatus = 'unknown' | 'active' | 'unreachable' | 'unconfigured'

export interface Server {
  id: string
  name: string
  ip_address: string
  description: string | null
  ssh_port: number | null
  ssh_user: string | null
  ssh_key_filename: string | null
  status: ServerStatus
  created_at: string
  updated_at: string
}

export interface ServerCreateInput {
  name: string
  ip_address: string
  description?: string | null
  ssh_port?: number | null
  ssh_user?: string | null
  ssh_key_filename?: string | null
}

// 测试连接接口的返回：只会是这三种（后端探测的真实结果），不含 unknown
export type ConnectionStatus = 'active' | 'unreachable' | 'unconfigured'

export interface ConnectionTestResult {
  status: ConnectionStatus
  detail: string
}
