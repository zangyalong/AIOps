export interface InstalledSkill {
  name: string
  description: string
  instructions: string
}

export interface SkillListResponse {
  skills: InstalledSkill[]
}
