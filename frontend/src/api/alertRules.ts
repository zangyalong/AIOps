import type {
  AlertRuleInput,
  AlertRuleMutationResult,
  AlertRuleOverview,
} from '../types/alertRule'

async function errorMessage(response: Response, fallback: string): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: string | Array<{ msg?: string }> }
    if (typeof body.detail === 'string') return body.detail
    if (Array.isArray(body.detail)) {
      return body.detail.map((item) => item.msg).filter(Boolean).join('；') || fallback
    }
  } catch {
    // 非 JSON 响应使用通用错误。
  }
  return `${fallback}（HTTP ${response.status}）`
}

async function expectJson<T>(response: Response, fallback: string): Promise<T> {
  if (!response.ok) throw new Error(await errorMessage(response, fallback))
  return response.json() as Promise<T>
}

export async function fetchAlertRules(): Promise<AlertRuleOverview> {
  return expectJson(
    await fetch('/api/alert-rules', { cache: 'no-store' }),
    '获取预警规则失败',
  )
}

export async function createAlertRule(data: AlertRuleInput): Promise<AlertRuleMutationResult> {
  return expectJson(
    await fetch('/api/alert-rules', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    }),
    '创建预警规则失败',
  )
}

export async function updateAlertRule(
  id: string,
  data: Partial<AlertRuleInput>,
): Promise<AlertRuleMutationResult> {
  return expectJson(
    await fetch(`/api/alert-rules/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    }),
    '更新预警规则失败',
  )
}

export async function deleteAlertRule(id: string): Promise<AlertRuleMutationResult> {
  return expectJson(
    await fetch(`/api/alert-rules/${id}`, { method: 'DELETE' }),
    '删除预警规则失败',
  )
}

export async function applyAlertRules(): Promise<AlertRuleMutationResult> {
  return expectJson(
    await fetch('/api/alert-rules/apply', { method: 'POST' }),
    '同步预警规则失败',
  )
}
