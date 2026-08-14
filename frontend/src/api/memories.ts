import type {
  MemoryCreateInput,
  MemoryRecord,
  MemoryScopeType,
} from '../types/memory'

async function memoryApiError(response: Response, fallback: string): Promise<Error> {
  const payload = (await response.json().catch(() => null)) as
    | { detail?: string | Array<{ msg?: string }> }
    | null
  if (typeof payload?.detail === 'string') return new Error(payload.detail)
  if (Array.isArray(payload?.detail)) {
    const message = payload.detail.map((item) => item.msg).filter(Boolean).join('；')
    if (message) return new Error(message)
  }
  return new Error(`${fallback}：${response.status}`)
}

export async function fetchMemories(
  scopeType: MemoryScopeType,
  serverId: string | null = null,
): Promise<MemoryRecord[]> {
  const params = new URLSearchParams({ scope_type: scopeType })
  if (scopeType === 'server' && serverId) params.set('server_id', serverId)
  const response = await fetch(`/api/memories?${params.toString()}`)
  if (!response.ok) throw await memoryApiError(response, '获取记忆失败')
  return response.json()
}

export async function createMemory(data: MemoryCreateInput): Promise<MemoryRecord> {
  const response = await fetch('/api/memories', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  if (!response.ok) throw await memoryApiError(response, '新增记忆失败')
  return response.json()
}

export async function deleteMemory(memoryId: string): Promise<void> {
  const response = await fetch(`/api/memories/${memoryId}`, { method: 'DELETE' })
  if (!response.ok) throw await memoryApiError(response, '删除记忆失败')
}
