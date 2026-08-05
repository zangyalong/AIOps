// 服务端持久化的服务器状态：unknown=还没测过，其余是最近一次测试连接的结果
export type ServerStatus = 'unknown' | 'active' | 'unreachable' | 'unconfigured'

export interface TargetDeploymentConfig {
  environment: string
  enable_alloy: boolean
  loki_push_url: string
  allow_docker_socket: boolean
  enable_node_exporter: boolean
  node_exporter_image: string
  enable_cadvisor: boolean
  cadvisor_image: string
  docker_root_dir: string
  enable_mysql_exporter: boolean
  mysql_exporter_image: string
  mysql_exporter_user: string
  mysql_exporter_host: string
  mysql_exporter_port: number
  mysql_exporter_network: string
  enable_postgres_exporter: boolean
  postgres_exporter_image: string
  postgres_exporter_uri: string
  postgres_exporter_user: string
  postgres_exporter_network: string
  enable_redis_exporter: boolean
  redis_exporter_image: string
  redis_exporter_address: string
  redis_exporter_user: string
  redis_exporter_network: string
  start_exporters: boolean
}

export interface DeploymentBundleSecrets {
  mysql_password: string | null
  postgres_password: string | null
  redis_password: string | null
}

export function createDefaultDeploymentConfig(): TargetDeploymentConfig {
  return {
    environment: 'prod',
    enable_alloy: true,
    loki_push_url: 'http://localhost:3100/loki/api/v1/push',
    allow_docker_socket: false,
    enable_node_exporter: true,
    node_exporter_image: 'quay.io/prometheus/node-exporter:v1.11.1',
    enable_cadvisor: true,
    cadvisor_image: 'ghcr.io/google/cadvisor:0.57.0',
    docker_root_dir: '/var/lib/docker',
    enable_mysql_exporter: false,
    mysql_exporter_image: 'prom/mysqld-exporter:v0.19.0',
    mysql_exporter_user: 'aioms_exporter',
    mysql_exporter_host: '127.0.0.1',
    mysql_exporter_port: 3306,
    mysql_exporter_network: 'host',
    enable_postgres_exporter: false,
    postgres_exporter_image: 'quay.io/prometheuscommunity/postgres-exporter:v0.19.1',
    postgres_exporter_uri: '127.0.0.1:5432/postgres?sslmode=disable',
    postgres_exporter_user: 'aioms_exporter',
    postgres_exporter_network: 'host',
    enable_redis_exporter: false,
    redis_exporter_image: 'quay.io/oliver006/redis_exporter:v1.88.0',
    redis_exporter_address: 'redis://127.0.0.1:6379',
    redis_exporter_user: '',
    redis_exporter_network: 'host',
    start_exporters: true,
  }
}

export function createEmptyDeploymentSecrets(): DeploymentBundleSecrets {
  return {
    mysql_password: null,
    postgres_password: null,
    redis_password: null,
  }
}

export interface Server {
  id: string
  name: string
  ip_address: string
  description: string | null
  ssh_port: number | null
  ssh_user: string | null
  ssh_key_filename: string | null
  deploy_ssh_user: string | null
  deploy_ssh_key_filename: string | null
  deployment_config: TargetDeploymentConfig | null
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
  deploy_ssh_user?: string | null
  deploy_ssh_key_filename?: string | null
  deployment_config?: TargetDeploymentConfig | null
}

// 测试连接接口的返回：只会是这三种（后端探测的真实结果），不含 unknown
export type ConnectionStatus = 'active' | 'unreachable' | 'unconfigured'

export interface ConnectionTestResult {
  status: ConnectionStatus
  detail: string
}
