import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useSearchParams } from 'react-router-dom'
import { api } from '../lib/api'
import { localDateString } from '../lib/date'
import {
  getIngestHealthStatus,
  getIngestHealthStatusClasses,
  getIngestHealthStatusReason,
} from '../lib/ingestHealthStatus'
import {
  boolLabel,
  errorTypeLabel,
  providerLabel,
  shortInterfaceMeaning,
  stageLabel,
  statusLabel,
} from '../lib/ingestLabels'
import type {
  IngestErrorRecord,
  IngestInterfaceRecord,
  IngestReconcileResult,
  IngestRetryRunResult,
  IngestRetryGroup,
  IngestRunInterfaceResult,
  IngestRunRecord,
  IngestRunStageResult,
} from '../lib/types'

const STAGES = ['pre_core', 'post_core', 'post_extended', 'watchlist', 'backfill'] as const
type IngestStage = (typeof STAGES)[number]
type FailedRankingSort = 'failure' | 'streak'

interface DetailPopoverProps {
  id: string
  openId: string | null
  onToggle: (id: string) => void
  lines: Array<{ label: string; value: string }>
}

function DetailPopover({ id, openId, onToggle, lines }: DetailPopoverProps) {
  const isOpen = openId === id
  return (
    <div className="relative group">
      <button
        type="button"
        onClick={() => onToggle(id)}
        className="text-xs text-blue-600 hover:text-blue-700 shrink-0"
        aria-expanded={isOpen}
      >
        详情
      </button>
      <div
        className={`mt-2 rounded-md border border-gray-200 bg-white p-3 shadow-sm text-xs text-gray-600 space-y-1 sm:absolute sm:right-0 sm:top-full sm:z-10 sm:mt-1 sm:w-72 ${
          isOpen ? 'block' : 'hidden sm:group-hover:block sm:group-focus-within:block'
        }`}
      >
        {lines.map((line) => (
          <div key={`${id}-${line.label}`}>
            <span className="text-gray-400">{line.label}：</span>
            <span>{line.value}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

function todayString() {
  return localDateString()
}

export default function IngestWorkbench() {
  const queryClient = useQueryClient()
  const [searchParams, setSearchParams] = useSearchParams()
  const initialDate = searchParams.get('date') || todayString()
  const initialStage = STAGES.includes((searchParams.get('stage') || '') as IngestStage)
    ? (searchParams.get('stage') as IngestStage)
    : 'post_core'
  const initialFocusedInterface = searchParams.get('interface')
  const initialFailedRankingSort =
    searchParams.get('health_sort') === 'streak' ? 'streak' : 'failure'
  const [date, setDate] = useState(initialDate)
  const [stage, setStage] = useState<IngestStage>(initialStage)
  const [selectedInterface, setSelectedInterface] = useState('')
  const [focusedInterface, setFocusedInterface] = useState<string | null>(initialFocusedInterface)
  const [failedRankingSort, setFailedRankingSort] =
    useState<FailedRankingSort>(initialFailedRankingSort)
  const [detailOpenId, setDetailOpenId] = useState<string | null>(null)
  const [copiedLinkKey, setCopiedLinkKey] = useState<string | null>(null)
  const [feedback, setFeedback] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const { data: interfaces = [], isLoading: interfacesLoading } = useQuery({
    queryKey: ['ingest-interfaces'],
    queryFn: () => api.listIngestInterfaces(),
  })

  const { data: inspectData, isLoading: inspectLoading } = useQuery({
    queryKey: ['ingest-inspect', date, stage, focusedInterface],
    queryFn: () => api.inspectIngest(date, focusedInterface, stage),
  })

  const { data: retrySummary } = useQuery({
    queryKey: ['ingest-retry', stage, focusedInterface],
    queryFn: () => api.getIngestRetrySummary(focusedInterface, stage),
  })

  const { data: healthSummary } = useQuery({
    queryKey: ['ingest-health', date, stage],
    queryFn: () => api.getIngestHealthSummary(date, 7, stage),
  })

  const filteredInterfaces = useMemo(
    () => interfaces.filter((item: IngestInterfaceRecord) => item.stage === stage),
    [interfaces, stage]
  )
  const interfaceMap = useMemo(
    () =>
      new Map(
        interfaces.map((item: IngestInterfaceRecord) => [item.interface_name, item] as const)
      ),
    [interfaces]
  )

  const runStageMutation = useMutation({
    mutationFn: () => api.runIngestStage({ stage, date, input_by: 'web' }),
    onSuccess: (result: IngestRunStageResult) => {
      setError(null)
      setFeedback(`已执行阶段：${result.stage_label || stageLabel(result.stage)}，记录 ${result.recorded_runs} 条运行`)
      queryClient.invalidateQueries({ queryKey: ['ingest-inspect'] })
      queryClient.invalidateQueries({ queryKey: ['ingest-retry'] })
    },
    onError: (e: Error) => {
      setFeedback(null)
      setError(e.message)
    },
  })

  const runInterfaceMutation = useMutation({
    mutationFn: () => api.runIngestInterface({ name: selectedInterface, date, input_by: 'web' }),
    onSuccess: (result: IngestRunInterfaceResult) => {
      setError(null)
      const meta = interfaceMap.get(result.name)
      setFeedback(
        `已执行接口：${result.run.interface_label || shortInterfaceMeaning(result.name, meta?.notes)}（${result.name}），状态 ${result.run.status_label || statusLabel(result.run.status)}`
      )
      queryClient.invalidateQueries({ queryKey: ['ingest-inspect'] })
      queryClient.invalidateQueries({ queryKey: ['ingest-retry'] })
    },
    onError: (e: Error) => {
      setFeedback(null)
      setError(e.message)
    },
  })

  const rerunInterfaceMutation = useMutation({
    mutationFn: (name: string) => api.runIngestInterface({ name, date, input_by: 'web' }),
    onSuccess: (result: IngestRunInterfaceResult) => {
      setError(null)
      const meta = interfaceMap.get(result.name)
      setFeedback(
        `已重跑接口：${result.run.interface_label || shortInterfaceMeaning(result.name, meta?.notes)}（${result.name}），状态 ${result.run.status_label || statusLabel(result.run.status)}`
      )
      queryClient.invalidateQueries({ queryKey: ['ingest-inspect'] })
      queryClient.invalidateQueries({ queryKey: ['ingest-retry'] })
    },
    onError: (e: Error) => {
      setFeedback(null)
      setError(e.message)
    },
  })

  const reconcileMutation = useMutation({
    mutationFn: () => api.reconcileIngestRuns({ stale_minutes: 5 }),
    onSuccess: (result: IngestReconcileResult) => {
      setError(null)
      setFeedback(`已清理陈旧运行：${result.reconciled_count ?? 0} 条（阈值 ${result.stale_minutes} 分钟）`)
      queryClient.invalidateQueries({ queryKey: ['ingest-inspect'] })
      queryClient.invalidateQueries({ queryKey: ['ingest-retry'] })
    },
    onError: (e: Error) => {
      setFeedback(null)
      setError(e.message)
    },
  })

  const retryGroupsMutation = useMutation({
    mutationFn: () => api.retryIngestGroups({ input_by: 'web' }),
    onSuccess: (result: IngestRetryRunResult) => {
      setError(null)
      setFeedback(
        `已批量重跑待重试项：尝试 ${result.attempted_groups ?? 0} 组，关闭旧错误 ${result.resolved_errors ?? 0} 条`
      )
      queryClient.invalidateQueries({ queryKey: ['ingest-inspect'] })
      queryClient.invalidateQueries({ queryKey: ['ingest-retry'] })
    },
    onError: (e: Error) => {
      setFeedback(null)
      setError(e.message)
    },
  })

  const sortedFailedInterfaces = useMemo(() => {
    const items = [...(healthSummary?.top_failed_interfaces ?? [])]
    if (failedRankingSort === 'streak') {
      items.sort((a, b) => {
        const streakDiff = (b.consecutive_failure_days ?? 0) - (a.consecutive_failure_days ?? 0)
        if (streakDiff !== 0) return streakDiff
        const unresolvedDiff = (b.unresolved_count ?? 0) - (a.unresolved_count ?? 0)
        if (unresolvedDiff !== 0) return unresolvedDiff
        const failureDiff = (b.failure_count ?? 0) - (a.failure_count ?? 0)
        if (failureDiff !== 0) return failureDiff
        return String(a.interface_name ?? '').localeCompare(String(b.interface_name ?? ''))
      })
      return items
    }
    items.sort((a, b) => {
      const failureDiff = (b.failure_count ?? 0) - (a.failure_count ?? 0)
      if (failureDiff !== 0) return failureDiff
      const unresolvedDiff = (b.unresolved_count ?? 0) - (a.unresolved_count ?? 0)
      if (unresolvedDiff !== 0) return unresolvedDiff
      const streakDiff = (b.consecutive_failure_days ?? 0) - (a.consecutive_failure_days ?? 0)
      if (streakDiff !== 0) return streakDiff
      return String(a.interface_name ?? '').localeCompare(String(b.interface_name ?? ''))
    })
    return items
  }, [failedRankingSort, healthSummary?.top_failed_interfaces])
  const dailyFailures = healthSummary?.daily_failures || []
  const healthStageText = stageLabel(healthSummary?.stage || stage)
  const failureRatePct = `${((healthSummary?.failure_rate ?? 0) * 100).toFixed(1)}%`
  const healthStatusLabel = healthSummary ? getIngestHealthStatus(healthSummary) : '稳定'
  const healthStatusReason = healthSummary
    ? getIngestHealthStatusReason(healthSummary)
    : '近 7 天没有未解决失败，当前阶段采集链路稳定。'
  const visibleRetryGroups = useMemo(
    () =>
      (retrySummary?.groups || []).filter((item) =>
        focusedInterface ? item.interface_name === focusedInterface : true
      ),
    [retrySummary?.groups, focusedInterface]
  )
  const visibleRuns = useMemo(() => {
    const list = inspectData?.runs ?? []
    return list.filter((item) => (focusedInterface ? item.interface_name === focusedInterface : true))
  }, [inspectData?.runs, focusedInterface])
  const visibleErrors = useMemo(() => {
    const list = inspectData?.errors ?? []
    return list.filter((item) => (focusedInterface ? item.interface_name === focusedInterface : true))
  }, [inspectData?.errors, focusedInterface])
  const currentViewHref = useMemo(() => {
    const params = new URLSearchParams()
    if (date) params.set('date', date)
    if (stage && stage !== 'post_core') params.set('stage', stage)
    if (focusedInterface) params.set('interface', focusedInterface)
    if (failedRankingSort !== 'failure') params.set('health_sort', failedRankingSort)
    const path = `/ingest${params.toString() ? `?${params.toString()}` : ''}`
    if (typeof window === 'undefined') return path
    return `${window.location.origin}${path}`
  }, [date, stage, focusedInterface, failedRankingSort])
  const buildViewHref = (targetInterface?: string | null) => {
    const params = new URLSearchParams()
    if (date) params.set('date', date)
    if (stage && stage !== 'post_core') params.set('stage', stage)
    if (targetInterface) params.set('interface', targetInterface)
    if (failedRankingSort !== 'failure') params.set('health_sort', failedRankingSort)
    const path = `/ingest${params.toString() ? `?${params.toString()}` : ''}`
    if (typeof window === 'undefined') return path
    return `${window.location.origin}${path}`
  }
  const focusedMeta = focusedInterface ? interfaceMap.get(focusedInterface) : null
  const focusedLabel = focusedInterface
    ? focusedMeta?.interface_label || shortInterfaceMeaning(focusedInterface, focusedMeta?.notes)
    : null

  function toggleDetail(id: string) {
    setDetailOpenId((current) => (current === id ? null : id))
  }

  async function copyViewLink(targetHref: string, copiedKey: string, successMessage: string) {
    try {
      await navigator.clipboard.writeText(targetHref)
      setError(null)
      setCopiedLinkKey(copiedKey)
      setFeedback(successMessage)
    } catch (e) {
      setCopiedLinkKey(null)
      setFeedback(null)
      setError(e instanceof Error ? e.message : '复制链接失败')
    }
  }

  async function copyCurrentViewLink() {
    await copyViewLink(
      currentViewHref,
      'current-view',
      `已复制当前视图链接：${currentViewHref}`
    )
  }

  useEffect(() => {
    const next = new URLSearchParams()
    if (date) next.set('date', date)
    if (stage && stage !== 'post_core') next.set('stage', stage)
    if (focusedInterface) next.set('interface', focusedInterface)
    if (failedRankingSort !== 'failure') next.set('health_sort', failedRankingSort)
    if (searchParams.toString() !== next.toString()) {
      setSearchParams(next, { replace: true })
    }
  }, [date, stage, focusedInterface, failedRankingSort, searchParams, setSearchParams])

  useEffect(() => {
    if (!copiedLinkKey) return undefined
    const timer = window.setTimeout(() => setCopiedLinkKey(null), 1500)
    return () => window.clearTimeout(timer)
  }, [copiedLinkKey])

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-800">采集诊断工作台</h1>
          <p className="text-sm text-gray-500 mt-1">
            查看接口注册表、执行采集、检查 run / error / retry 摘要。
          </p>
        </div>
        <button
          type="button"
          onClick={() => void copyCurrentViewLink()}
          className={`rounded border px-3 py-2 text-sm ${
            copiedLinkKey === 'current-view'
              ? 'border-green-300 bg-green-50 text-green-700'
              : 'border-gray-300 text-gray-700 hover:bg-gray-50'
          }`}
        >
          {copiedLinkKey === 'current-view' ? '已复制' : '复制当前视图链接'}
        </button>
      </div>

      <div className="bg-white border border-gray-200 rounded-lg p-4">
        <div className="flex items-center justify-between gap-3 mb-3">
          <div>
            <div className="flex items-center gap-2">
              <h2 className="text-base font-semibold text-gray-800">近 7 天采集健康</h2>
              <span
                className={`inline-flex rounded-full px-2 py-0.5 text-[11px] font-medium ${getIngestHealthStatusClasses(healthStatusLabel)}`}
              >
                {healthStatusLabel}
              </span>
            </div>
            <p className="text-xs text-gray-500 mt-1">
              {healthSummary?.start_date || date} 至 {healthSummary?.end_date || date}
            </p>
            <p className="text-xs text-gray-500 mt-1">{healthStatusReason}</p>
          </div>
          <div className="rounded-full border border-blue-200 bg-blue-50 px-3 py-1 text-xs font-medium text-blue-700">
            当前视角：{healthStageText}
          </div>
        </div>
        <div className="mb-4 grid grid-cols-1 gap-2 text-xs text-gray-600 md:grid-cols-3">
          <div className="rounded border border-gray-100 bg-gray-50 px-3 py-2">
            失败接口数 <span className="ml-1 font-semibold text-gray-800">{healthSummary?.failed_interface_count ?? 0}</span>
          </div>
          <div className="rounded border border-gray-100 bg-gray-50 px-3 py-2">
            失败率 <span className="ml-1 font-semibold text-gray-800">{failureRatePct}</span>
          </div>
          <div className="rounded border border-gray-100 bg-gray-50 px-3 py-2">
            从未成功接口 <span className="ml-1 font-semibold text-gray-800">{healthSummary?.never_succeeded_count ?? 0}</span>
          </div>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          <div className="rounded border border-gray-100 bg-gray-50 px-3 py-3">
            <div className="text-xs text-gray-500">总运行数</div>
            <div className="mt-1 text-2xl font-semibold text-gray-800">{healthSummary?.total_runs ?? 0}</div>
          </div>
          <div className="rounded border border-red-100 bg-red-50/50 px-3 py-3">
            <div className="text-xs text-gray-500">失败总数</div>
            <div className="mt-1 text-2xl font-semibold text-red-700">{healthSummary?.total_failures ?? 0}</div>
          </div>
          <div className="rounded border border-orange-100 bg-orange-50/50 px-3 py-3">
            <div className="text-xs text-gray-500">未解决失败</div>
            <div className="mt-1 text-2xl font-semibold text-orange-700">{healthSummary?.unresolved_failures ?? 0}</div>
          </div>
        </div>
        <div className="mt-4 grid grid-cols-1 xl:grid-cols-2 gap-4">
          <div>
            <div className="mb-2 flex items-center justify-between gap-3">
              <h3 className="text-sm font-medium text-gray-700">失败接口排行</h3>
              <div className="flex items-center gap-2 text-xs">
                <button
                  type="button"
                  onClick={() => setFailedRankingSort('failure')}
                  className={`rounded border px-2 py-1 ${
                    failedRankingSort === 'failure'
                      ? 'border-red-300 bg-red-50 text-red-700'
                      : 'border-gray-300 text-gray-600 hover:bg-gray-50'
                  }`}
                >
                  按失败次数
                </button>
                <button
                  type="button"
                  onClick={() => setFailedRankingSort('streak')}
                  className={`rounded border px-2 py-1 ${
                    failedRankingSort === 'streak'
                      ? 'border-orange-300 bg-orange-50 text-orange-700'
                      : 'border-gray-300 text-gray-600 hover:bg-gray-50'
                  }`}
                >
                  按连续失败
                </button>
              </div>
            </div>
            <div className="space-y-2">
              {sortedFailedInterfaces.slice(0, 5).map((item) => {
                const itemLabel =
                  item.interface_label || shortInterfaceMeaning(item.interface_name, item.interface_note)
                const copyKey = `interface:${item.interface_name}`
                return (
                <div
                  key={item.interface_name}
                  onClick={() => setFocusedInterface(item.interface_name || null)}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter' || event.key === ' ') {
                      event.preventDefault()
                      setFocusedInterface(item.interface_name || null)
                    }
                  }}
                  role="button"
                  tabIndex={0}
                  className={`w-full rounded border px-3 py-2 text-left hover:bg-gray-50 ${
                    focusedInterface && item.interface_name === focusedInterface
                      ? 'border-blue-300 bg-blue-50/50'
                      : 'border-gray-100'
                  }`}
                >
                  <div className="flex items-center justify-between gap-3">
                    <div>
                      <div className="text-sm font-medium text-gray-800">
                        {itemLabel}
                      </div>
                      <div className="text-xs text-gray-500">{item.interface_name}</div>
                    </div>
                    <div className="flex items-start gap-3">
                      <div className="text-right text-xs">
                        <div className="text-red-700">失败 {item.failure_count ?? 0}</div>
                        <div className="text-orange-700">未解 {item.unresolved_count ?? 0}</div>
                      </div>
                      <button
                        type="button"
                        onClick={(event) => {
                          event.stopPropagation()
                          void copyViewLink(
                            buildViewHref(item.interface_name),
                            copyKey,
                            `已复制接口排障链接：${buildViewHref(item.interface_name)}`
                          )
                        }}
                        className={`rounded border px-2 py-1 text-xs ${
                          copiedLinkKey === copyKey
                            ? 'border-green-300 bg-green-50 text-green-700'
                            : 'border-gray-300 text-gray-600 hover:bg-gray-50'
                        }`}
                        aria-label={`复制${itemLabel}排障链接`}
                      >
                        {copiedLinkKey === copyKey ? '已复制' : '复制链接'}
                      </button>
                    </div>
                  </div>
                  <div className="mt-1 text-xs text-gray-500">
                    最近成功：{item.last_success_biz_date || '暂无'} · 最近失败：{item.last_failure_biz_date || '暂无'}
                  </div>
                  <div className="mt-1 text-xs text-gray-500">
                    连续失败：{item.consecutive_failure_days ?? 0} 天 ·
                    {item.days_since_last_success != null
                      ? ` 距最近成功：${item.days_since_last_success} 天`
                      : ' 从未成功'}
                  </div>
                </div>
              )})}
              {sortedFailedInterfaces.length === 0 && (
                <p className="text-sm text-gray-500">近 7 天暂无失败接口</p>
              )}
            </div>
          </div>
          <div>
            <h3 className="text-sm font-medium text-gray-700 mb-2">每日失败数</h3>
            <div className="space-y-2">
              {dailyFailures.map((item) => (
                <div key={item.biz_date} className="flex items-center justify-between rounded border border-gray-100 px-3 py-2 text-sm">
                  <span className="text-gray-600">{item.biz_date}</span>
                  <span className="font-medium text-red-700">{item.error_count ?? 0}</span>
                </div>
              ))}
              {dailyFailures.length === 0 && (
                <p className="text-sm text-gray-500">近 7 天暂无失败记录</p>
              )}
            </div>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {focusedInterface && (
          <div className="lg:col-span-3 flex items-center justify-between rounded-lg border border-blue-200 bg-blue-50 px-4 py-3">
            <div className="text-sm text-blue-900">
              当前仅查看接口：<span className="font-medium">{focusedLabel}</span>
              <span className="ml-2 text-xs text-blue-700">{focusedInterface}</span>
            </div>
            <button
              type="button"
              onClick={() => setFocusedInterface(null)}
              className="text-xs rounded border border-blue-300 px-2.5 py-1 text-blue-700 hover:bg-white"
            >
              清除筛选
            </button>
          </div>
        )}
        <div className="bg-white border border-gray-200 rounded-lg p-4 space-y-3">
          <h2 className="text-base font-semibold text-gray-800">运行采集</h2>
          <div>
            <label htmlFor="ingest-date" className="block text-sm font-medium text-gray-700 mb-1">日期</label>
            <input
              id="ingest-date"
              type="date"
              value={date}
              onChange={(e) => setDate(e.target.value)}
              className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm"
            />
          </div>
          <div>
            <label htmlFor="ingest-stage" className="block text-sm font-medium text-gray-700 mb-1">采集阶段</label>
            <select
              id="ingest-stage"
              value={stage}
              onChange={(e) => {
                setStage(e.target.value as IngestStage)
                setSelectedInterface('')
              }}
              className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm"
            >
              {STAGES.map((item) => (
                <option key={item} value={item}>
                  {stageLabel(item)} ({item})
                </option>
              ))}
            </select>
          </div>
          <button
            onClick={() => runStageMutation.mutate()}
            disabled={runStageMutation.isPending}
            className="w-full px-4 py-2 bg-blue-600 text-white rounded-md text-sm font-medium hover:bg-blue-700 disabled:opacity-50"
          >
            {runStageMutation.isPending ? '执行中...' : '执行阶段'}
          </button>

          <div className="pt-2 border-t border-gray-100">
            <label htmlFor="ingest-interface" className="block text-sm font-medium text-gray-700 mb-1">单接口</label>
            <select
              id="ingest-interface"
              value={selectedInterface}
              onChange={(e) => setSelectedInterface(e.target.value)}
              className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm"
            >
              <option value="">请选择接口</option>
              {filteredInterfaces.map((item: IngestInterfaceRecord) => (
                <option key={item.interface_name} value={item.interface_name}>
                  {item.interface_label || shortInterfaceMeaning(item.interface_name, item.notes)} ({item.interface_name})
                </option>
              ))}
            </select>
            <button
              onClick={() => runInterfaceMutation.mutate()}
              disabled={!selectedInterface || runInterfaceMutation.isPending}
              className="mt-3 w-full px-4 py-2 bg-green-600 text-white rounded-md text-sm font-medium hover:bg-green-700 disabled:opacity-50"
            >
              {runInterfaceMutation.isPending ? '执行中...' : '执行单接口'}
            </button>
          </div>

          {feedback && <p className="text-sm text-green-700 bg-green-50 border border-green-200 rounded px-3 py-2">{feedback}</p>}
          {error && <p className="text-sm text-red-700 bg-red-50 border border-red-200 rounded px-3 py-2">{error}</p>}
        </div>

        <div className="bg-white border border-gray-200 rounded-lg p-4">
          <div className="mb-3 flex items-center justify-between gap-3">
            <h2 className="text-base font-semibold text-gray-800">重试摘要</h2>
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => retryGroupsMutation.mutate()}
                disabled={retryGroupsMutation.isPending || (retrySummary?.retryable_count ?? 0) === 0}
                className="text-xs rounded border border-orange-300 px-2.5 py-1 text-orange-700 hover:bg-orange-50 disabled:opacity-50"
              >
                {retryGroupsMutation.isPending ? '批量重跑中...' : '重跑全部待重试'}
              </button>
              <button
                type="button"
                onClick={() => reconcileMutation.mutate()}
                disabled={reconcileMutation.isPending}
                className="text-xs rounded border border-gray-300 px-2.5 py-1 text-gray-600 hover:bg-gray-50 disabled:opacity-50"
              >
                {reconcileMutation.isPending ? '清理中...' : '清理陈旧运行'}
              </button>
            </div>
          </div>
          <p className="text-3xl font-bold text-orange-600">
            {retrySummary?.retryable_count ?? 0}
          </p>
          <p className="text-sm text-gray-500 mt-1">未解决可重试错误数</p>
            <div className="mt-3 space-y-2">
            {visibleRetryGroups.slice(0, 5).map((item: IngestRetryGroup) => (
              <div key={`${item.biz_date}-${item.interface_name}`} className="text-sm bg-gray-50 rounded px-3 py-2">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <div className="font-medium text-gray-800">
                      {item.interface_label || shortInterfaceMeaning(item.interface_name, interfaceMap.get(item.interface_name || '')?.notes)}
                    </div>
                    <div className="text-xs text-gray-500 mt-0.5">{item.interface_name}</div>
                    <span className="text-gray-500">{item.biz_date}</span>
                    <span className="text-orange-600 ml-2">× {item.error_count}</span>
                  </div>
                  <button
                    type="button"
                    onClick={() => item.interface_name && rerunInterfaceMutation.mutate(item.interface_name)}
                    disabled={!item.interface_name || rerunInterfaceMutation.isPending}
                    className="shrink-0 text-xs rounded border border-orange-300 px-2.5 py-1 text-orange-700 hover:bg-orange-50 disabled:opacity-50"
                  >
                    {rerunInterfaceMutation.isPending ? '重跑中...' : '重跑接口'}
                  </button>
                </div>
              </div>
            ))}
            {visibleRetryGroups.length === 0 && (
              <p className="text-sm text-gray-500">暂无待重试项</p>
            )}
          </div>
        </div>

        <div className="bg-white border border-gray-200 rounded-lg p-4">
          <h2 className="text-base font-semibold text-gray-800 mb-3">接口注册表</h2>
          {interfacesLoading ? (
            <p className="text-sm text-gray-500">加载中...</p>
          ) : (
            <div className="space-y-2 max-h-80 overflow-y-auto">
              {interfaces.map((item: IngestInterfaceRecord) => (
                <div key={item.interface_name} className="border border-gray-100 rounded px-3 py-2">
                  <div className="flex items-center justify-between gap-3">
                    <div>
                      <div className="text-sm font-medium text-gray-800">{item.interface_label || shortInterfaceMeaning(item.interface_name, item.notes)}</div>
                      <code className="text-xs text-gray-500">{item.interface_name}</code>
                    </div>
                    <div className="flex items-center gap-2">
                      <span className="text-xs px-2 py-0.5 rounded bg-gray-100 text-gray-600">
                        {item.stage_label || stageLabel(item.stage)}
                      </span>
                      <DetailPopover
                        id={`iface-${item.interface_name}`}
                        openId={detailOpenId}
                        onToggle={toggleDetail}
                        lines={[
                          { label: '中文说明', value: item.notes || item.interface_label || '暂无说明' },
                          { label: 'Provider 方法', value: item.provider_method || '未知' },
                          { label: '参数策略', value: item.params_policy || '未知' },
                          { label: '默认启用', value: item.enabled_by_default_label || boolLabel(item.enabled_by_default) },
                        ]}
                      />
                    </div>
                  </div>
                  <p className="text-xs text-gray-500 mt-1">{item.notes || '暂无说明'}</p>
                  <p className="text-xs text-gray-400 mt-1">{item.provider_method}</p>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        <div className="bg-white border border-gray-200 rounded-lg p-4">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-base font-semibold text-gray-800">运行记录</h2>
            <span className="text-xs text-gray-500">{date}</span>
          </div>
          {inspectLoading ? (
            <p className="text-sm text-gray-500">加载中...</p>
          ) : visibleRuns.length === 0 ? (
            <p className="text-sm text-gray-500">该日期暂无采集记录</p>
          ) : (
            <div className="space-y-2 max-h-[28rem] overflow-y-auto">
              {visibleRuns.map((run: IngestRunRecord) => (
                <div key={run.run_id} className="border border-gray-100 rounded px-3 py-2">
                  <div className="flex items-center justify-between gap-3">
                    <div>
                      <div className="text-sm font-medium text-gray-800">
                        {run.interface_label || shortInterfaceMeaning(run.interface_name, interfaceMap.get(run.interface_name || '')?.notes)}
                      </div>
                      <code className="text-xs text-gray-500">{run.interface_name}</code>
                    </div>
                    <span
                      className={`text-xs px-2 py-0.5 rounded ${
                        run.status === 'success'
                          ? 'bg-green-100 text-green-700'
                          : run.status === 'failed'
                          ? 'bg-red-100 text-red-700'
                          : 'bg-gray-100 text-gray-600'
                      }`}
                    >
                      {run.status_label || statusLabel(run.status)}
                    </span>
                  </div>
                  <div className="mt-1 flex items-start justify-between gap-3">
                    <p className="text-xs text-gray-500">
                      {run.stage_label || stageLabel(run.stage)} · {run.provider_label || providerLabel(run.provider)} · {run.row_count ?? 0} 行
                    </p>
                    <DetailPopover
                      id={`run-${run.run_id}`}
                      openId={detailOpenId}
                      onToggle={toggleDetail}
                      lines={[
                        { label: '接口说明', value: run.interface_note || interfaceMap.get(run.interface_name || '')?.notes || run.interface_label || '暂无说明' },
                        { label: '状态', value: run.status_label || statusLabel(run.status) },
                        { label: '开始时间', value: run.started_at || '未知' },
                        { label: '结束时间', value: run.finished_at || '未结束' },
                        { label: '耗时', value: run.duration_ms != null ? `${run.duration_ms} ms` : '未知' },
                      ]}
                    />
                  </div>
                  {run.notes && <p className="text-xs text-gray-400 mt-1">{run.notes}</p>}
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="bg-white border border-gray-200 rounded-lg p-4">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-base font-semibold text-gray-800">错误记录</h2>
            <span className="text-xs text-gray-500">{date}</span>
          </div>
          {inspectLoading ? (
            <p className="text-sm text-gray-500">加载中...</p>
          ) : visibleErrors.length === 0 ? (
            <p className="text-sm text-gray-500">该日期暂无错误</p>
          ) : (
            <div className="space-y-2 max-h-[28rem] overflow-y-auto">
              {visibleErrors.map((item: IngestErrorRecord) => (
                <div key={`${item.run_id}-${item.id}`} className="border border-red-100 rounded px-3 py-2 bg-red-50/40">
                  <div className="flex items-center justify-between gap-3">
                    <div>
                      <div className="text-sm font-medium text-gray-800">
                        {item.interface_label || shortInterfaceMeaning(item.interface_name, interfaceMap.get(item.interface_name || '')?.notes)}
                      </div>
                      <code className="text-xs text-gray-500">{item.interface_name}</code>
                    </div>
                    <div className="text-right">
                      <div className="flex items-center justify-end gap-2">
                        <span className="text-xs px-2 py-0.5 rounded bg-red-100 text-red-700">
                          {item.error_type_label || errorTypeLabel(item.error_type)}
                        </span>
                        {item.restriction_label && (
                          <span className="text-xs px-2 py-0.5 rounded bg-amber-100 text-amber-800">
                            {item.restriction_label}
                          </span>
                        )}
                      </div>
                      <div className="text-[11px] text-gray-500 mt-1">
                        {item.retryable_label || (item.retryable ? '可重试' : '不可重试')}
                      </div>
                    </div>
                  </div>
                  <div className="mt-1 flex items-start justify-between gap-3">
                    <div className="flex items-start justify-between gap-3 w-full">
                      {item.stage && <p className="text-xs text-gray-500">{item.stage_label || stageLabel(item.stage)}</p>}
                      <div className="flex items-center gap-2">
                        <button
                          type="button"
                          onClick={() => item.interface_name && rerunInterfaceMutation.mutate(item.interface_name)}
                          disabled={!item.interface_name || rerunInterfaceMutation.isPending}
                          className="text-xs rounded border border-red-300 px-2.5 py-1 text-red-700 hover:bg-red-50 disabled:opacity-50"
                        >
                          {rerunInterfaceMutation.isPending ? '重跑中...' : '重跑接口'}
                        </button>
                        <DetailPopover
                          id={`error-${item.run_id}-${item.id}`}
                          openId={detailOpenId}
                          onToggle={toggleDetail}
                          lines={[
                            { label: '接口说明', value: item.interface_note || interfaceMap.get(item.interface_name || '')?.notes || item.interface_label || '暂无说明' },
                            { label: '错误类型', value: item.error_type_label || errorTypeLabel(item.error_type) },
                            { label: '限制类型', value: item.restriction_label || '无' },
                            { label: '限制说明', value: item.restriction_reason || '无' },
                            { label: '重试策略', value: item.retryable_label || (item.retryable ? '可重试' : '不可重试') },
                            { label: '处理建议', value: item.action_hint || '先查看原始错误与接口说明，再决定是否重试。' },
                            { label: '原始错误', value: item.error_message || '未知' },
                          ]}
                        />
                      </div>
                    </div>
                  </div>
                  <p className="text-sm text-red-700 mt-1">{item.error_message}</p>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
