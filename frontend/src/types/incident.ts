export type IncidentStatus = 'firing' | 'acknowledged' | 'resolved'

export interface Incident {
  id: string
  group_key: string
  status: IncidentStatus
  alertname: string
  severity: string
  instance: string | null
  summary: string | null
  diagnosis: string | null
  triaged_at: string | null
  acked_by: string | null
  acked_at: string | null
  resolved_at: string | null
  created_at: string
  updated_at: string
}
