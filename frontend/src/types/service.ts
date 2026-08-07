export interface ServiceRecord {
  id: string
  name: string
  environment: string
  description: string | null
  created_at: string
  updated_at: string
}

export interface ServiceInput {
  name: string
  environment: string
  description?: string | null
}

export interface ServiceInstance {
  id: string
  service_id: string
  server_id: string | null
  name: string
  runtime_type: RuntimeType | null
  runtime_name: string | null
  control_enabled: boolean
  metric_instance: string | null
  log_service: string | null
  description: string | null
  created_at: string
  updated_at: string
}

export interface ServiceInstanceInput {
  service_id: string
  server_id?: string | null
  name: string
  runtime_type?: RuntimeType | null
  runtime_name?: string | null
  metric_instance?: string | null
  log_service?: string | null
  description?: string | null
}

export interface ServiceFileAccess {
  id: string
  service_instance_id: string
  access_level: 'read' | 'write'
  path: string
  created_at: string
  updated_at: string
}

export type RuntimeType = 'systemd' | 'docker' | 'process'

export type RuntimeStatus = 'running' | 'stopped' | 'error' | 'unknown'

export interface ServiceInstanceStatus {
  instance_id: string
  status: RuntimeStatus
  label: string
  detail: string
  ports: string[]
  checked_at: string
}

export interface ServiceMetric {
  key: string
  label: string
  value: number
  unit: 'state' | 'percent' | 'bytes' | 'count' | 'seconds' | string
}

export interface ServiceLog {
  timestamp: string
  level: 'error' | 'warn' | 'info' | 'unknown'
  line: string
}

export interface ServiceObservability {
  instance_id: string
  range_minutes: number
  metric_instance: string | null
  log_service: string | null
  metrics: ServiceMetric[]
  logs: ServiceLog[]
  metric_message: string | null
  log_message: string | null
}
