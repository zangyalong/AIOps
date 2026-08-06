export type AlertRuleSeverity = 'info' | 'warning' | 'critical'
export type AlertRuleRuntimeState = 'disabled' | 'inactive' | 'pending' | 'firing' | 'unknown'

export interface AlertRule {
  id: string
  name: string
  expression: string
  duration: string
  severity: AlertRuleSeverity
  summary: string
  description: string | null
  labels: Record<string, string>
  enabled: boolean
  runtime_state: AlertRuleRuntimeState
  health: string
  last_error: string | null
  active_alerts: number
  created_at: string
  updated_at: string
}

export interface AlertRuleInput {
  name: string
  expression: string
  duration: string
  severity: AlertRuleSeverity
  summary: string
  description: string | null
  labels: Record<string, string>
  enabled: boolean
}

export interface AlertRuleOverview {
  rules: AlertRule[]
  active_alerts_total: number
  prometheus_message: string | null
  alertmanager_message: string | null
}

export interface AlertRuleMutationResult {
  message: string
  overview: AlertRuleOverview
}
