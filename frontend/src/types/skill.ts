export type SkillDiagnosticLevel = 'warning' | 'error'

export interface InstalledSkill {
  name: string
  description: string
  instructions: string
  location: string
  version: string
  category: string
  triggers: string[]
  tools: string[]
  enabled: boolean
  source: string
}
export interface SkillDiagnostic {
  level: SkillDiagnosticLevel
  location: string
  message: string
}

export interface SkillListResponse {
  skills: InstalledSkill[]
  diagnostics: SkillDiagnostic[]
}
