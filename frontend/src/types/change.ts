export type ChangeType = 'deployment' | 'rollback' | 'scaling' | 'configuration'

export interface ChangeRecord {
  id: string
  service_name: string
  environment: string
  version: string | null
  commit_hash: string | null
  change_type: ChangeType
  occurred_at: string
  description: string | null
  created_at: string
  updated_at: string
}

export interface ChangeRecordInput {
  service_name: string
  environment: string
  version?: string | null
  commit_hash?: string | null
  change_type: ChangeType
  occurred_at: string
  description?: string | null
}
