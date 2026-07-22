import type { Incident, IncidentChangeContext, IncidentStatus } from '../types/incident'

async function parseIncidentResponse(res: Response, fallbackMessage: string): Promise<Incident> {
  if (!res.ok) {
    throw new Error(`${fallbackMessage}：${res.status}`)
  }
  return res.json()
}

export async function fetchIncidents(status?: IncidentStatus): Promise<Incident[]> {
  const params = new URLSearchParams()
  if (status) params.set('status', status)

  const query = params.toString()
  const res = await fetch(`/api/incidents${query ? `?${query}` : ''}`)
  if (!res.ok) {
    throw new Error(`获取事件列表失败：${res.status}`)
  }
  return res.json()
}

export async function fetchIncidentChanges(
  id: string,
  windowMinutes = 30,
): Promise<IncidentChangeContext> {
  const params = new URLSearchParams({ window_minutes: String(windowMinutes) })
  const res = await fetch(`/api/incidents/${id}/changes?${params}`)
  if (!res.ok) throw new Error(`获取事件关联变更失败：${res.status}`)
  return res.json()
}

export async function acknowledgeIncident(id: string, ackedBy: string): Promise<Incident> {
  const res = await fetch(`/api/incidents/${id}/ack`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ acked_by: ackedBy }),
  })
  return parseIncidentResponse(res, '认领事件失败')
}

export async function resolveIncident(id: string): Promise<Incident> {
  const res = await fetch(`/api/incidents/${id}/resolve`, { method: 'POST' })
  return parseIncidentResponse(res, '解决事件失败')
}
