import type { ChangeRecord, ChangeRecordInput } from '../types/change'

async function parseChangeResponse(res: Response, fallback: string): Promise<ChangeRecord> {
  if (!res.ok) throw new Error(`${fallback}：${res.status}`)
  return res.json()
}

export async function fetchChanges(): Promise<ChangeRecord[]> {
  const res = await fetch('/api/changes')
  if (!res.ok) throw new Error(`获取变更记录失败：${res.status}`)
  return res.json()
}

export async function createChange(data: ChangeRecordInput): Promise<ChangeRecord> {
  const res = await fetch('/api/changes', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  return parseChangeResponse(res, '新增变更记录失败')
}

export async function updateChange(
  id: string,
  data: Partial<ChangeRecordInput>,
): Promise<ChangeRecord> {
  const res = await fetch(`/api/changes/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  return parseChangeResponse(res, '更新变更记录失败')
}

export async function deleteChange(id: string): Promise<void> {
  const res = await fetch(`/api/changes/${id}`, { method: 'DELETE' })
  if (!res.ok) throw new Error(`删除变更记录失败：${res.status}`)
}
