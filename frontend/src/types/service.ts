export interface ServiceRecord {
  id: string
  name: string
  environment: string
  owner: string | null
  description: string | null
  repository_url: string | null
  dashboard_url: string | null
  created_at: string
  updated_at: string
}

export interface ServiceInput {
  name: string
  environment: string
  owner?: string | null
  description?: string | null
  repository_url?: string | null
  dashboard_url?: string | null
}

export interface ServiceInstance {
  id: string
  service_id: string
  server_id: string | null
  name: string
  runtime_type: RuntimeType | null
  runtime_name: string | null
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

export type RuntimeType = 'systemd' | 'docker' | 'process'

export type DependencyType = 'service' | 'database' | 'cache' | 'queue' | 'external'

export interface ServiceDependency {
  id: string
  upstream_service_id: string
  downstream_service_id: string
  dependency_type: DependencyType
  description: string | null
  created_at: string
  updated_at: string
}

export interface ServiceDependencyInput {
  upstream_service_id: string
  downstream_service_id: string
  dependency_type: DependencyType
  description?: string | null
}

export interface ServiceTopology {
  focus_service_id: string
  nodes: ServiceRecord[]
  edges: ServiceDependency[]
}
