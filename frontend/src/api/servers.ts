import type { ConnectionTestResult, Server, ServerCreateInput } from '../types/server'

export async function fetchServers(): Promise<Server[]> {
  const res = await fetch('/api/servers')
  if (!res.ok) {
    throw new Error(`获取服务器列表失败：${res.status}`)
  }
  return res.json()
}

export async function createServer(data: ServerCreateInput): Promise<Server> {
  const res = await fetch('/api/servers', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  if (!res.ok) {
    throw new Error(`新增服务器失败：${res.status}`)
  }
  return res.json()
}

export async function updateServer(
  id: string,
  data: Partial<ServerCreateInput>,
): Promise<Server> {
  const res = await fetch(`/api/servers/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  if (!res.ok) {
    throw new Error(`更新服务器失败：${res.status}`)
  }
  return res.json()
}

export async function deleteServer(id: string): Promise<void> {
  const res = await fetch(`/api/servers/${id}`, { method: 'DELETE' })
  if (!res.ok) {
    throw new Error(`删除服务器失败：${res.status}`)
  }
}

export async function testServerConnection(id: string): Promise<ConnectionTestResult> {
  const res = await fetch(`/api/servers/${id}/test-connection`, { method: 'POST' })
  if (!res.ok) {
    throw new Error(`测试连接失败：${res.status}`)
  }
  return res.json()
}
