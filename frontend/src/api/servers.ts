import type { Server } from '../types/server'

export async function fetchServers(): Promise<Server[]> {
  const res = await fetch('/api/servers')
  if (!res.ok) {
    throw new Error(`获取服务器列表失败：${res.status}`)
  }
  return res.json()
}
