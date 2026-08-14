export type MemoryScopeType = 'global' | 'server'
export type MemoryCategory = 'preference' | 'environment' | 'decision' | 'lesson'

export interface MemoryRecord {
  id: string
  memory_key: string
  scope_type: MemoryScopeType
  server_id: string | null
  category: MemoryCategory
  content: string
  source_conversation_id: string | null
  source_run_id: string | null
  is_active: boolean
  created_at: string
  updated_at: string
}

export interface MemoryCreateInput {
  scope_type: MemoryScopeType
  server_id: string | null
  category: MemoryCategory
  content: string
}
