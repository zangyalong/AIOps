import type {
  AlertOccurrence,
  Incident,
  IncidentPage,
  IncidentStatus,
} from '../types/incident'

async function parseIncidentResponse(res: Response, fallbackMessage: string): Promise<Incident> {
  if (!res.ok) {
    throw new Error(`${fallbackMessage}：${res.status}`)
  }
  return res.json()
}

export async function fetchIncidents(options: {
  status?: IncidentStatus
  page: number
  pageSize: number
}): Promise<IncidentPage> {
  const params = new URLSearchParams()
  if (options.status) params.set('status', options.status)
  params.set('page', String(options.page))
  params.set('page_size', String(options.pageSize))

  const res = await fetch(`/api/incidents?${params.toString()}`, { cache: 'no-store' })
  if (!res.ok) {
    throw new Error(`获取事件列表失败：${res.status}`)
  }
  return res.json()
}

export async function fetchIncidentAlertOccurrences(id: string): Promise<AlertOccurrence[]> {
  const res = await fetch(`/api/incidents/${id}/alert-occurrences`)
  if (!res.ok) throw new Error(`获取事件原始告警失败：${res.status}`)
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

export async function deleteIncident(id: string): Promise<void> {
  const res = await fetch(`/api/incidents/${id}`, { method: 'DELETE' })
  if (!res.ok) {
    let detail = ''
    try {
      const body = (await res.json()) as { detail?: string }
      detail = body.detail ? `：${body.detail}` : ''
    } catch {
      // 非 JSON 错误响应使用状态码兜底。
    }
    throw new Error(`删除事件失败${detail || `：${res.status}`}`)
  }
}
