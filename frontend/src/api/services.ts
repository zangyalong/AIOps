import type {
  ServiceInput,
  ServiceInstance,
  ServiceInstanceInput,
  ServiceInstanceStatus,
  ServiceObservability,
  ServiceRecord,
} from '../types/service'

async function parseJson<T>(res: Response, fallback: string): Promise<T> {
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    throw new Error(body?.detail || `${fallback}：${res.status}`)
  }
  return res.json()
}

export async function fetchServices(): Promise<ServiceRecord[]> {
  return parseJson(await fetch('/api/services'), '获取服务目录失败')
}

export async function createService(data: ServiceInput): Promise<ServiceRecord> {
  return parseJson(
    await fetch('/api/services', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    }),
    '新增服务失败',
  )
}

export async function updateService(
  id: string,
  data: Partial<ServiceInput>,
): Promise<ServiceRecord> {
  return parseJson(
    await fetch(`/api/services/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    }),
    '更新服务失败',
  )
}

export async function deleteService(id: string): Promise<void> {
  const res = await fetch(`/api/services/${id}`, { method: 'DELETE' })
  if (!res.ok) throw new Error(`删除服务失败：${res.status}`)
}

export async function fetchServiceInstances(): Promise<ServiceInstance[]> {
  return parseJson(await fetch('/api/service-instances'), '获取服务实例失败')
}

export async function fetchServiceInstanceStatuses(filters: {
  serviceId?: string
  serverId?: string
} = {}): Promise<ServiceInstanceStatus[]> {
  const params = new URLSearchParams()
  if (filters.serviceId) params.set('service_id', filters.serviceId)
  if (filters.serverId) params.set('server_id', filters.serverId)
  const query = params.size ? `?${params.toString()}` : ''
  return parseJson(
    await fetch(`/api/service-instances/statuses${query}`),
    '检测服务状态失败',
  )
}

export async function fetchServiceObservability(
  instanceId: string,
  minutes = 1440,
): Promise<ServiceObservability> {
  const params = new URLSearchParams({ minutes: String(minutes) })
  return parseJson(
    await fetch(`/api/service-instances/${instanceId}/observability?${params.toString()}`),
    '获取服务可观测性数据失败',
  )
}

export async function createServiceInstance(
  data: ServiceInstanceInput,
): Promise<ServiceInstance> {
  return parseJson(
    await fetch('/api/service-instances', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    }),
    '新增服务实例失败',
  )
}

export async function updateServiceInstance(
  id: string,
  data: Partial<ServiceInstanceInput>,
): Promise<ServiceInstance> {
  return parseJson(
    await fetch(`/api/service-instances/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    }),
    '更新服务实例失败',
  )
}

export async function updateServiceControlAuthorization(
  id: string,
  enabled: boolean,
): Promise<ServiceInstance> {
  return parseJson(
    await fetch(`/api/service-instances/${id}/control-authorization`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled }),
    }),
    '更新服务控制授权失败',
  )
}

export async function deleteServiceInstance(id: string): Promise<void> {
  const res = await fetch(`/api/service-instances/${id}`, { method: 'DELETE' })
  if (!res.ok) throw new Error(`删除服务实例失败：${res.status}`)
}
