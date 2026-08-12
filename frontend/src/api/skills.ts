import type { SkillListResponse } from '../types/skill'

export async function fetchSkills(): Promise<SkillListResponse> {
  const response = await fetch('/api/skills')
  if (!response.ok) {
    const body = await response.json().catch(() => null)
    throw new Error(body?.detail || `获取 Skills 失败：${response.status}`)
  }
  return response.json()
}
