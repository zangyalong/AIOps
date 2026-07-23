import type {
  ServiceDependency,
  ServiceDependencyInput,
  ServiceInput,
  ServiceInstance,
  ServiceInstanceInput,
  ServiceRecord,
  ServiceTopology,
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

export async function deleteServiceInstance(id: string): Promise<void> {
  const res = await fetch(`/api/service-instances/${id}`, { method: 'DELETE' })
  if (!res.ok) throw new Error(`删除服务实例失败：${res.status}`)
}

export async function fetchServiceDependencies(): Promise<ServiceDependency[]> {
  return parseJson(await fetch('/api/service-dependencies'), '获取服务依赖失败')
}

export async function createServiceDependency(
  data: ServiceDependencyInput,
): Promise<ServiceDependency> {
  return parseJson(
    await fetch('/api/service-dependencies', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    }),
    '新增服务依赖失败',
  )
}

export async function updateServiceDependency(
  id: string,
  data: Partial<ServiceDependencyInput>,
): Promise<ServiceDependency> {
  return parseJson(
    await fetch(`/api/service-dependencies/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    }),
    '更新服务依赖失败',
  )
}

export async function deleteServiceDependency(id: string): Promise<void> {
  const res = await fetch(`/api/service-dependencies/${id}`, { method: 'DELETE' })
  if (!res.ok) throw new Error(`删除服务依赖失败：${res.status}`)
}

export async function fetchServiceTopology(id: string): Promise<ServiceTopology> {
  return parseJson(await fetch(`/api/services/${id}/topology`), '获取服务拓扑失败')
}
