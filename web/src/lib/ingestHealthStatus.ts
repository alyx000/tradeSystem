import type { IngestHealthSummary } from './types'

export type IngestHealthStatus = '稳定' | '有波动' | '承压' | '需处理'

export function getIngestHealthStatus(health: IngestHealthSummary): IngestHealthStatus {
  if (health.status_label) return health.status_label

  const unresolved = health.unresolved_failures ?? 0
  const failedInterfaces = health.failed_interface_count ?? 0
  const neverSucceeded = health.never_succeeded_count ?? 0
  const topStreak = health.top_failed_interfaces?.[0]?.consecutive_failure_days ?? 0

  if (unresolved === 0 && failedInterfaces === 0) return '稳定'
  if (neverSucceeded > 0 || unresolved >= 2) return '需处理'
  if (topStreak >= 3 || unresolved > 0) return '承压'
  if ((health.total_failures ?? 0) > 0) return '有波动'
  return '稳定'
}

export function getIngestHealthStatusReason(health: IngestHealthSummary): string {
  if (health.status_reason) return health.status_reason

  const unresolved = health.unresolved_failures ?? 0
  const failedInterfaces = health.failed_interface_count ?? 0
  const neverSucceeded = health.never_succeeded_count ?? 0
  const topStreak = health.top_failed_interfaces?.[0]?.consecutive_failure_days ?? 0
  const failures = health.total_failures ?? 0

  if (unresolved === 0 && failedInterfaces === 0) return '近 7 天没有未解决失败，当前阶段采集链路稳定。'
  if (neverSucceeded > 0) return '存在从未成功过的接口，建议优先排查权限、配置或实现缺口。'
  if (unresolved >= 2) return `当前仍有 ${unresolved} 条未解决失败，建议优先处理主链路异常。`
  if (topStreak >= 3) return `存在连续失败 ${topStreak} 天的接口，阶段稳定性已明显承压。`
  if (unresolved > 0) return '当前仍有未解决失败，建议尽快重试或排查数据源状态。'
  if (failures > 0) return '近 7 天出现过失败，但暂未形成持续性异常。'
  return '近 7 天没有未解决失败，当前阶段采集链路稳定。'
}

export function getIngestHealthStatusClasses(status: IngestHealthStatus) {
  if (status === '稳定') return 'bg-green-100 text-green-700'
  if (status === '有波动') return 'bg-amber-100 text-amber-700'
  if (status === '承压') return 'bg-orange-100 text-orange-700'
  return 'bg-red-100 text-red-700'
}
