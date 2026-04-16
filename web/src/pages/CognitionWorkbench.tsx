import { Fragment, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  listCognitions,
  listInstances,
  listReviews,
  type CognitionItem,
  type InstanceItem,
  type ReviewItem,
  type CognitionListFilters,
  type InstanceListFilters,
  type ReviewListFilters,
} from '../lib/api'

type Tab = 'cognitions' | 'instances' | 'reviews'

const TAB_OPTIONS: Array<{ value: Tab; label: string }> = [
  { value: 'cognitions', label: '认知库' },
  { value: 'instances', label: '实例' },
  { value: 'reviews', label: '复盘' },
]

const CATEGORY_LABEL: Record<string, string> = {
  signal: '信号',
  sentiment: '情绪',
  structure: '结构',
  cycle: '周期',
  position: '仓位',
  sizing: '规模',
  synthesis: '综合',
  fundamental: '基本面',
  macro: '宏观',
  valuation: '估值',
  execution: '执行',
}

const COG_STATUS_LABEL: Record<string, string> = {
  candidate: '候选',
  active: '生效',
  deprecated: '弃用',
  merged: '合并',
}

const OUTCOME_LABEL: Record<string, string> = {
  pending: '待验证',
  validated: '已验证',
  invalidated: '已证伪',
  partial: '部分成立',
  not_applicable: '不适用',
}

const REVIEW_STATUS_LABEL: Record<string, string> = {
  draft: '草稿',
  confirmed: '已确认',
}

const PERIOD_TYPE_LABEL: Record<string, string> = {
  weekly: '周',
  monthly: '月',
  quarterly: '季',
  yearly: '年',
}

const EVIDENCE_LEVEL_LABEL: Record<string, string> = {
  observation: '观察',
  hypothesis: '假设',
  principle: '原则',
}

const REVIEW_SCOPE_LABEL: Record<string, string> = {
  calendar_period: '日历周期',
  event_window: '事件窗口',
  regime_window: '状态窗口',
}

const ACTION_BIAS_LABEL: Record<string, string> = {
  buy_dip: '买跌（回调介入）',
  buy_breakout: '买突破',
  sell_rally: '反弹卖出',
  trim: '减仓',
  hold: '持有观望',
  avoid: '回避',
  wait: '等待确认',
  hedge: '对冲',
  rotate: '切换方向',
}

function zhLabel(dict: Record<string, string>, value: string | null | undefined): string {
  if (!value) return '—'
  return dict[value] ?? value
}

function withOriginalTitle(value: string | null | undefined): string | undefined {
  return value || undefined
}

function buildOptions(
  allLabel: string,
  dict: Record<string, string>,
): Array<{ value: string; label: string }> {
  return [
    { value: '', label: allLabel },
    ...Object.entries(dict).map(([value, zh]) => ({ value, label: zh })),
  ]
}

const CATEGORY_OPTIONS = buildOptions('全部类别', CATEGORY_LABEL)
const STATUS_OPTIONS = buildOptions('全部状态', COG_STATUS_LABEL)
const OUTCOME_OPTIONS = buildOptions('全部结果', OUTCOME_LABEL)
const REVIEW_STATUS_OPTIONS = buildOptions('全部状态', REVIEW_STATUS_LABEL)
const PERIOD_TYPE_OPTIONS = buildOptions('全部周期', PERIOD_TYPE_LABEL)

function cogStatusBadge(status: string): string {
  if (status === 'active') return 'bg-green-50 text-green-700 border-green-200'
  if (status === 'candidate') return 'bg-yellow-50 text-yellow-700 border-yellow-200'
  if (status === 'deprecated') return 'bg-gray-100 text-gray-500 border-gray-200'
  if (status === 'merged') return 'bg-blue-50 text-blue-700 border-blue-200'
  return 'bg-gray-50 text-gray-600 border-gray-200'
}

function outcomeBadge(outcome: string): string {
  if (outcome === 'validated') return 'bg-green-50 text-green-700 border-green-200'
  if (outcome === 'invalidated') return 'bg-red-50 text-red-700 border-red-200'
  if (outcome === 'partial') return 'bg-orange-50 text-orange-700 border-orange-200'
  if (outcome === 'pending') return 'bg-yellow-50 text-yellow-700 border-yellow-200'
  return 'bg-gray-50 text-gray-600 border-gray-200'
}

function reviewStatusBadge(status: string): string {
  if (status === 'confirmed') return 'bg-green-50 text-green-700 border-green-200'
  if (status === 'draft') return 'bg-yellow-50 text-yellow-700 border-yellow-200'
  return 'bg-gray-50 text-gray-600 border-gray-200'
}

function toList(value: unknown): string[] {
  if (value == null) return []
  if (Array.isArray(value)) return value.map(v => (typeof v === 'string' ? v : JSON.stringify(v)))
  if (typeof value === 'string') return value ? [value] : []
  return [JSON.stringify(value)]
}

function prettyJson(value: unknown): string {
  if (value == null) return '—'
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

function copy(text: string) {
  if (typeof navigator !== 'undefined' && navigator.clipboard) {
    void navigator.clipboard.writeText(text).catch(() => {})
  }
}

export default function CognitionWorkbench() {
  const [tab, setTab] = useState<Tab>('cognitions')
  const [instanceFilterCognitionId, setInstanceFilterCognitionId] = useState<string>('')

  const jumpToInstances = (cognitionId: string) => {
    setInstanceFilterCognitionId(cognitionId)
    setTab('instances')
  }

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold text-gray-800">交易认知看板</h1>
      <p className="text-sm text-gray-500">
        只读视图：认知库、实例库与复盘记录均由 CLI/Agent 写入，本页用于检索与回溯。
      </p>
      <div className="flex gap-2 border-b border-gray-200">
        {TAB_OPTIONS.map(opt => (
          <button
            key={opt.value}
            type="button"
            onClick={() => setTab(opt.value)}
            className={`px-4 py-2 text-sm border-b-2 -mb-px transition-colors ${
              tab === opt.value
                ? 'border-blue-600 text-blue-600 font-medium'
                : 'border-transparent text-gray-500 hover:text-gray-700'
            }`}
          >
            {opt.label}
          </button>
        ))}
      </div>
      {tab === 'cognitions' && <CognitionsPanel onViewInstances={jumpToInstances} />}
      {tab === 'instances' && (
        <InstancesPanel
          cognitionId={instanceFilterCognitionId}
          onChangeCognitionId={setInstanceFilterCognitionId}
        />
      )}
      {tab === 'reviews' && <ReviewsPanel />}
    </div>
  )
}

function ErrorNotice({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  return (
    <div className="bg-red-50 border border-red-200 rounded-lg px-4 py-3 text-sm text-red-700 flex items-center justify-between gap-3">
      <span className="break-all">加载失败：{(error as Error).message}</span>
      <button
        type="button"
        onClick={onRetry}
        className="px-3 py-1 rounded-md border border-red-300 text-red-700 text-xs hover:bg-red-100"
      >
        重试
      </button>
    </div>
  )
}

function CognitionsPanel({ onViewInstances }: { onViewInstances: (id: string) => void }) {
  const [filters, setFilters] = useState<CognitionListFilters>({ limit: 100, offset: 0 })
  const [expandedId, setExpandedId] = useState<string | null>(null)

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['cognition-list', filters],
    queryFn: () => listCognitions(filters),
  })

  const rows = useMemo(() => data?.cognitions ?? [], [data])
  const conflictCounts = useMemo(() => {
    const counts: Record<string, number> = {}
    for (const r of rows) {
      if (r.conflict_group) counts[r.conflict_group] = (counts[r.conflict_group] ?? 0) + 1
    }
    return counts
  }, [rows])

  const update = (patch: Partial<CognitionListFilters>) => {
    setFilters(prev => ({ ...prev, ...patch }))
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end gap-3 bg-white rounded-xl border border-gray-200 p-4">
        <FilterSelect
          label="类别"
          value={filters.category ?? ''}
          options={CATEGORY_OPTIONS}
          onChange={v => update({ category: v || undefined })}
        />
        <FilterSelect
          label="状态"
          value={filters.status ?? ''}
          options={STATUS_OPTIONS}
          onChange={v => update({ status: v || undefined })}
        />
        <FilterInput
          label="冲突组"
          value={filters.conflict_group ?? ''}
          onChange={v => update({ conflict_group: v || undefined })}
          placeholder="如 sentiment.topN"
        />
        <FilterInput
          label="关键字"
          value={filters.keyword ?? ''}
          onChange={v => update({ keyword: v || undefined })}
          placeholder="模糊匹配标题/描述"
        />
        <div className="text-xs text-gray-500 ml-auto">
          共 {data?.total ?? 0} 条{rows.length ? `，当前展示 ${rows.length}` : ''}
        </div>
      </div>

      {isLoading && <p className="text-gray-500 text-sm">加载中…</p>}
      {error && <ErrorNotice error={error} onRetry={() => refetch()} />}

      {!isLoading && !error && rows.length === 0 && (
        <p className="text-gray-500 text-sm bg-white rounded-xl border border-gray-200 px-4 py-6 text-center">
          暂无记录
        </p>
      )}

      {!isLoading && rows.length > 0 && (
        <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-left text-gray-600">
              <tr>
                <th className="px-3 py-2 font-medium w-8" />
                <th className="px-3 py-2 font-medium">标题</th>
                <th className="px-3 py-2 font-medium">类别</th>
                <th className="px-3 py-2 font-medium">状态</th>
                <th className="px-3 py-2 font-medium">证据</th>
                <th className="px-3 py-2 font-medium whitespace-nowrap">实例/验证/证伪</th>
                <th className="px-3 py-2 font-medium">置信</th>
                <th className="px-3 py-2 font-medium whitespace-nowrap">首次观察</th>
                <th className="px-3 py-2 font-medium">冲突组</th>
                <th className="px-3 py-2 font-medium w-24" />
              </tr>
            </thead>
            <tbody>
              {rows.map(row => {
                const rk = row.cognition_id
                const isConflict = !!row.conflict_group && (conflictCounts[row.conflict_group] ?? 0) > 1
                return (
                  <Fragment key={rk}>
                    <tr
                      className={`border-t border-gray-100 cursor-pointer ${
                        isConflict ? 'bg-red-50/40 hover:bg-red-50' : 'hover:bg-gray-50/80'
                      }`}
                      onClick={() => setExpandedId(expandedId === rk ? null : rk)}
                    >
                      <td className="px-3 py-2">
                        <button type="button" className="text-blue-600 text-xs">
                          {expandedId === rk ? '▼' : '▶'}
                        </button>
                      </td>
                      <td className="px-3 py-2 text-gray-900">
                        <div className="font-medium">{row.title}</div>
                        <div className="font-mono text-[11px] text-gray-400">{row.cognition_id}</div>
                      </td>
                      <td
                        className="px-3 py-2 text-gray-700 whitespace-nowrap"
                        title={
                          row.category
                            ? `${row.category}${row.sub_category ? ` / ${row.sub_category}` : ''}`
                            : undefined
                        }
                      >
                        {zhLabel(CATEGORY_LABEL, row.category)}
                        {row.sub_category ? ` / ${row.sub_category}` : ''}
                      </td>
                      <td className="px-3 py-2">
                        <span
                          className={`inline-block px-2 py-0.5 text-xs rounded border ${cogStatusBadge(row.status)}`}
                          title={withOriginalTitle(row.status)}
                        >
                          {zhLabel(COG_STATUS_LABEL, row.status)}
                        </span>
                      </td>
                      <td
                        className="px-3 py-2 text-gray-700"
                        title={withOriginalTitle(row.evidence_level)}
                      >
                        {zhLabel(EVIDENCE_LEVEL_LABEL, row.evidence_level)}
                      </td>
                      <td className="px-3 py-2 text-gray-700 whitespace-nowrap">
                        {row.instance_count} / {row.validated_count} / {row.invalidated_count}
                      </td>
                      <td className="px-3 py-2 text-gray-700">{row.confidence?.toFixed?.(2) ?? row.confidence}</td>
                      <td className="px-3 py-2 text-gray-700 whitespace-nowrap">
                        {row.first_observed_date ?? '—'}
                      </td>
                      <td className="px-3 py-2 text-gray-700">
                        {row.conflict_group ? (
                          <span
                            className={`text-xs ${isConflict ? 'text-red-600 font-medium' : 'text-gray-600'}`}
                            title={isConflict ? '同组存在多条' : undefined}
                          >
                            {row.conflict_group}
                          </span>
                        ) : (
                          '—'
                        )}
                      </td>
                      <td className="px-3 py-2 text-right" onClick={e => e.stopPropagation()}>
                        <button
                          type="button"
                          className="text-blue-600 text-xs hover:underline"
                          onClick={() => onViewInstances(row.cognition_id)}
                        >
                          查看实例
                        </button>
                      </td>
                    </tr>
                    {expandedId === rk && (
                      <tr className="bg-gray-50 border-t border-gray-100">
                        <td colSpan={10} className="px-4 py-3">
                          <CognitionDetail row={row} />
                        </td>
                      </tr>
                    )}
                  </Fragment>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

function CognitionDetail({ row }: { row: CognitionItem }) {
  const conditions = toList(row.conditions_json)
  const exceptions = toList(row.exceptions_json)
  const invalidations = toList(row.invalidation_conditions_json)
  const tags = toList(row.tags)
  return (
    <div className="space-y-3 text-sm">
      <div className="flex items-center gap-2">
        <span className="text-xs text-gray-500" title="cognition_id">认知 ID</span>
        <code className="bg-white border border-gray-200 rounded px-2 py-0.5 font-mono text-xs text-gray-800">
          {row.cognition_id}
        </code>
        <button
          type="button"
          onClick={() => copy(row.cognition_id)}
          className="text-xs text-blue-600 hover:underline"
        >
          复制
        </button>
        {row.supersedes && (
          <span className="text-xs text-gray-500" title="supersedes">
            取代 <code className="bg-white border border-gray-200 rounded px-1">{row.supersedes}</code>
          </span>
        )}
        <span className="text-xs text-gray-500">版本 v{row.version}</span>
      </div>
      {row.description && (
        <div>
          <div className="text-xs font-medium text-gray-500 mb-1">描述</div>
          <p className="text-gray-800 whitespace-pre-wrap">{row.description}</p>
        </div>
      )}
      {row.pattern && (
        <div>
          <div className="text-xs font-medium text-gray-500 mb-1" title="pattern">模式 pattern</div>
          <p className="text-gray-800 whitespace-pre-wrap">{row.pattern}</p>
        </div>
      )}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
        <DetailField label="时间视角" title="time_horizon" value={row.time_horizon} />
        <DetailField label="动作模板" title="action_template" value={row.action_template} />
        <DetailField label="仓位模板" title="position_template" value={row.position_template} />
      </div>
      {conditions.length > 0 && <ListSection title="成立条件" subtitle="conditions" items={conditions} />}
      {exceptions.length > 0 && <ListSection title="例外情形" subtitle="exceptions" items={exceptions} />}
      {invalidations.length > 0 && (
        <ListSection title="失效条件" subtitle="invalidation_conditions" items={invalidations} tone="red" />
      )}
      {tags.length > 0 && (
        <div>
          <div className="text-xs font-medium text-gray-500 mb-1" title="tags">标签</div>
          <div className="flex flex-wrap gap-1">
            {tags.map(t => (
              <span
                key={t}
                className="inline-block px-2 py-0.5 text-xs rounded border border-gray-200 bg-white text-gray-700"
              >
                {t}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

function DetailField({
  label,
  title,
  value,
}: {
  label: string
  title?: string
  value: string | null | undefined
}) {
  return (
    <div>
      <div className="text-xs font-medium text-gray-500 mb-1" title={title}>
        {label}
      </div>
      <div className="text-gray-800 text-sm break-words">{value ?? '—'}</div>
    </div>
  )
}

function ListSection({
  title,
  subtitle,
  items,
  tone,
}: {
  title: string
  subtitle?: string
  items: string[]
  tone?: 'red'
}) {
  const color = tone === 'red' ? 'text-red-700' : 'text-gray-800'
  return (
    <div>
      <div className="text-xs font-medium text-gray-500 mb-1" title={subtitle}>
        {title}
        {subtitle && <span className="ml-1 text-[10px] text-gray-400">{subtitle}</span>}
      </div>
      <ul className={`list-disc pl-5 space-y-0.5 ${color}`}>
        {items.map((t, i) => (
          <li key={`${i}-${t}`} className="whitespace-pre-wrap break-words">
            {t}
          </li>
        ))}
      </ul>
    </div>
  )
}

function InstancesPanel({
  cognitionId,
  onChangeCognitionId,
}: {
  cognitionId: string
  onChangeCognitionId: (v: string) => void
}) {
  const [otherFilters, setOtherFilters] = useState<Omit<InstanceListFilters, 'cognition_id'>>({
    limit: 100,
    offset: 0,
  })
  const [expandedId, setExpandedId] = useState<string | null>(null)

  const filters: InstanceListFilters = useMemo(
    () => ({ ...otherFilters, cognition_id: cognitionId || undefined }),
    [otherFilters, cognitionId],
  )

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['cognition-instances', filters],
    queryFn: () => listInstances(filters),
  })

  const rows = useMemo(() => data?.instances ?? [], [data])

  const update = (patch: Partial<InstanceListFilters>) => {
    if ('cognition_id' in patch) {
      onChangeCognitionId(patch.cognition_id ?? '')
    }
    const { cognition_id: _ignored, ...rest } = patch
    void _ignored
    if (Object.keys(rest).length > 0) {
      setOtherFilters(prev => ({ ...prev, ...rest }))
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end gap-3 bg-white rounded-xl border border-gray-200 p-4">
        <FilterInput
          label="认知 ID"
          value={filters.cognition_id ?? ''}
          onChange={v => update({ cognition_id: v || undefined })}
          placeholder="支持粘贴完整 cognition_id"
          width="w-64"
        />
        <FilterSelect
          label="结果"
          value={filters.outcome ?? ''}
          options={OUTCOME_OPTIONS}
          onChange={v => update({ outcome: v || undefined })}
        />
        <FilterInput
          label="起始日期"
          type="date"
          value={filters.date_from ?? ''}
          onChange={v => update({ date_from: v || undefined })}
        />
        <FilterInput
          label="截止日期"
          type="date"
          value={filters.date_to ?? ''}
          onChange={v => update({ date_to: v || undefined })}
        />
        <div className="text-xs text-gray-500 ml-auto">
          共 {data?.total ?? 0} 条{rows.length ? `，当前展示 ${rows.length}` : ''}
        </div>
      </div>

      {isLoading && <p className="text-gray-500 text-sm">加载中…</p>}
      {error && <ErrorNotice error={error} onRetry={() => refetch()} />}

      {!isLoading && !error && rows.length === 0 && (
        <p className="text-gray-500 text-sm bg-white rounded-xl border border-gray-200 px-4 py-6 text-center">
          暂无记录
        </p>
      )}

      {!isLoading && rows.length > 0 && (
        <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-left text-gray-600">
              <tr>
                <th className="px-3 py-2 font-medium w-8" />
                <th className="px-3 py-2 font-medium whitespace-nowrap">观察日期</th>
                <th className="px-3 py-2 font-medium">老师</th>
                <th className="px-3 py-2 font-medium">认知 ID</th>
                <th className="px-3 py-2 font-medium">结果</th>
                <th className="px-3 py-2 font-medium">动作倾向</th>
                <th className="px-3 py-2 font-medium">事实来源</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(row => {
                const rk = row.instance_id
                return (
                  <Fragment key={rk}>
                    <tr
                      className="border-t border-gray-100 hover:bg-gray-50/80 cursor-pointer"
                      onClick={() => setExpandedId(expandedId === rk ? null : rk)}
                    >
                      <td className="px-3 py-2">
                        <button type="button" className="text-blue-600 text-xs">
                          {expandedId === rk ? '▼' : '▶'}
                        </button>
                      </td>
                      <td className="px-3 py-2 text-gray-700 whitespace-nowrap">{row.observed_date ?? '—'}</td>
                      <td className="px-3 py-2 text-gray-700">{row.teacher_name_snapshot ?? '—'}</td>
                      <td className="px-3 py-2" onClick={e => e.stopPropagation()}>
                        <button
                          type="button"
                          className="font-mono text-xs text-blue-600 hover:underline break-all"
                          onClick={() => update({ cognition_id: row.cognition_id })}
                          title="点击按此认知过滤"
                        >
                          {row.cognition_id}
                        </button>
                      </td>
                      <td className="px-3 py-2">
                        <span
                          className={`inline-block px-2 py-0.5 text-xs rounded border ${outcomeBadge(row.outcome)}`}
                          title={withOriginalTitle(row.outcome)}
                        >
                          {zhLabel(OUTCOME_LABEL, row.outcome)}
                        </span>
                      </td>
                      <td
                        className="px-3 py-2 text-gray-700"
                        title={withOriginalTitle(row.action_bias)}
                      >
                        {zhLabel(ACTION_BIAS_LABEL, row.action_bias)}
                      </td>
                      <td className="px-3 py-2 text-gray-500 text-xs">{row.outcome_fact_source ?? '—'}</td>
                    </tr>
                    {expandedId === rk && (
                      <tr className="bg-gray-50 border-t border-gray-100">
                        <td colSpan={7} className="px-4 py-3">
                          <InstanceDetail row={row} />
                        </td>
                      </tr>
                    )}
                  </Fragment>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

function InstanceDetail({ row }: { row: InstanceItem }) {
  return (
    <div className="space-y-3 text-sm">
      {row.context_summary && (
        <div>
          <div className="text-xs font-medium text-gray-500 mb-1" title="context_summary">情景摘要</div>
          <p className="text-gray-800 whitespace-pre-wrap">{row.context_summary}</p>
        </div>
      )}
      {row.teacher_original_text && (
        <div>
          <div className="text-xs font-medium text-gray-500 mb-1" title="teacher_original_text">老师原文</div>
          <p className="text-gray-800 whitespace-pre-wrap">{row.teacher_original_text}</p>
        </div>
      )}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
        <DetailField label="时间视角" title="time_horizon" value={row.time_horizon} />
        <DetailField label="仓位上限" title="position_cap" value={row.position_cap} />
        <DetailField label="市场状态" title="market_regime" value={row.market_regime} />
        <DetailField label="回避动作" title="avoid_action" value={row.avoid_action} />
        <DetailField label="跨市场锚" title="cross_market_anchor" value={row.cross_market_anchor} />
        <DetailField label="共识键" title="consensus_key" value={row.consensus_key} />
      </div>
      {row.outcome_detail && (
        <div>
          <div className="text-xs font-medium text-gray-500 mb-1" title="outcome_detail">验证详情</div>
          <p className="text-gray-800 whitespace-pre-wrap">{row.outcome_detail}</p>
        </div>
      )}
      {row.lesson && (
        <div>
          <div className="text-xs font-medium text-gray-500 mb-1" title="lesson">心得</div>
          <p className="text-gray-800 whitespace-pre-wrap">{row.lesson}</p>
        </div>
      )}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <div>
          <div className="text-xs font-medium text-gray-500 mb-1" title="regime_tags_json">状态标签</div>
          <pre className="text-xs text-gray-700 whitespace-pre-wrap overflow-x-auto max-h-48 overflow-y-auto border border-gray-200 rounded-lg p-2 bg-white">
            {prettyJson(row.regime_tags_json)}
          </pre>
        </div>
        <div>
          <div className="text-xs font-medium text-gray-500 mb-1" title="parameters_json">参数</div>
          <pre className="text-xs text-gray-700 whitespace-pre-wrap overflow-x-auto max-h-48 overflow-y-auto border border-gray-200 rounded-lg p-2 bg-white">
            {prettyJson(row.parameters_json)}
          </pre>
        </div>
      </div>
    </div>
  )
}

function ReviewsPanel() {
  const [filters, setFilters] = useState<ReviewListFilters>({ limit: 100, offset: 0 })
  const [expandedId, setExpandedId] = useState<string | null>(null)

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['cognition-reviews', filters],
    queryFn: () => listReviews(filters),
  })

  const rows = useMemo(() => data?.reviews ?? [], [data])

  const update = (patch: Partial<ReviewListFilters>) => {
    setFilters(prev => ({ ...prev, ...patch }))
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end gap-3 bg-white rounded-xl border border-gray-200 p-4">
        <FilterSelect
          label="周期"
          value={filters.period_type ?? ''}
          options={PERIOD_TYPE_OPTIONS}
          onChange={v => update({ period_type: v || undefined })}
        />
        <FilterSelect
          label="状态"
          value={filters.status ?? ''}
          options={REVIEW_STATUS_OPTIONS}
          onChange={v => update({ status: v || undefined })}
        />
        <FilterInput
          label="起始日期"
          type="date"
          value={filters.date_from ?? ''}
          onChange={v => update({ date_from: v || undefined })}
        />
        <FilterInput
          label="截止日期"
          type="date"
          value={filters.date_to ?? ''}
          onChange={v => update({ date_to: v || undefined })}
        />
        <div className="text-xs text-gray-500 ml-auto">
          共 {data?.total ?? 0} 条{rows.length ? `，当前展示 ${rows.length}` : ''}
        </div>
      </div>

      {isLoading && <p className="text-gray-500 text-sm">加载中…</p>}
      {error && <ErrorNotice error={error} onRetry={() => refetch()} />}

      {!isLoading && !error && rows.length === 0 && (
        <p className="text-gray-500 text-sm bg-white rounded-xl border border-gray-200 px-4 py-6 text-center">
          暂无记录
        </p>
      )}

      {!isLoading && rows.length > 0 && (
        <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-left text-gray-600">
              <tr>
                <th className="px-3 py-2 font-medium w-8" />
                <th className="px-3 py-2 font-medium">周期</th>
                <th className="px-3 py-2 font-medium whitespace-nowrap">区间</th>
                <th className="px-3 py-2 font-medium">范围</th>
                <th className="px-3 py-2 font-medium">状态</th>
                <th className="px-3 py-2 font-medium whitespace-nowrap">活跃认知</th>
                <th className="px-3 py-2 font-medium whitespace-nowrap">验证实例</th>
                <th className="px-3 py-2 font-medium whitespace-nowrap">生成时间</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(row => {
                const rk = row.review_id
                const activeCount = Array.isArray(row.active_cognitions_json)
                  ? row.active_cognitions_json.length
                  : 0
                const stats = (row.validation_stats_json && typeof row.validation_stats_json === 'object'
                  ? (row.validation_stats_json as Record<string, unknown>)
                  : {}) as Record<string, unknown>
                const validated = Number(stats.validated ?? stats.validated_instances ?? 0) || 0
                return (
                  <Fragment key={rk}>
                    <tr
                      className="border-t border-gray-100 hover:bg-gray-50/80 cursor-pointer"
                      onClick={() => setExpandedId(expandedId === rk ? null : rk)}
                    >
                      <td className="px-3 py-2">
                        <button type="button" className="text-blue-600 text-xs">
                          {expandedId === rk ? '▼' : '▶'}
                        </button>
                      </td>
                      <td
                        className="px-3 py-2 text-gray-800"
                        title={withOriginalTitle(row.period_type)}
                      >
                        {zhLabel(PERIOD_TYPE_LABEL, row.period_type)}
                      </td>
                      <td className="px-3 py-2 text-gray-700 whitespace-nowrap">
                        {row.period_start ?? '—'} ~ {row.period_end ?? '—'}
                      </td>
                      <td
                        className="px-3 py-2 text-gray-700"
                        title={withOriginalTitle(row.review_scope)}
                      >
                        {zhLabel(REVIEW_SCOPE_LABEL, row.review_scope)}
                      </td>
                      <td className="px-3 py-2">
                        <span
                          className={`inline-block px-2 py-0.5 text-xs rounded border ${reviewStatusBadge(row.status)}`}
                          title={withOriginalTitle(row.status)}
                        >
                          {zhLabel(REVIEW_STATUS_LABEL, row.status)}
                        </span>
                      </td>
                      <td className="px-3 py-2 text-gray-700 whitespace-nowrap">{activeCount}</td>
                      <td className="px-3 py-2 text-gray-700 whitespace-nowrap">{validated}</td>
                      <td className="px-3 py-2 text-gray-500 text-xs whitespace-nowrap">
                        {row.generated_at ?? '—'}
                      </td>
                    </tr>
                    {expandedId === rk && (
                      <tr className="bg-gray-50 border-t border-gray-100">
                        <td colSpan={8} className="px-4 py-3">
                          <ReviewDetail row={row} />
                        </td>
                      </tr>
                    )}
                  </Fragment>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

function ReviewDetail({ row }: { row: ReviewItem }) {
  const keyLessons = toList(row.key_lessons_json)
  const actionItems = toList(row.action_items_json)
  return (
    <div className="space-y-3 text-sm">
      {keyLessons.length > 0 && (
        <ListSection title="关键教训" subtitle="key_lessons" items={keyLessons} />
      )}
      {row.user_reflection && (
        <div>
          <div className="text-xs font-medium text-gray-500 mb-1" title="user_reflection">用户反思</div>
          <p className="text-gray-800 whitespace-pre-wrap">{row.user_reflection}</p>
        </div>
      )}
      {actionItems.length > 0 && (
        <ListSection title="后续行动" subtitle="action_items" items={actionItems} />
      )}
      <div>
        <div className="text-xs font-medium text-gray-500 mb-1" title="validation_stats_json">验证统计</div>
        <pre className="text-xs text-gray-700 whitespace-pre-wrap overflow-x-auto max-h-64 overflow-y-auto border border-gray-200 rounded-lg p-2 bg-white">
          {prettyJson(row.validation_stats_json)}
        </pre>
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <div>
          <div className="text-xs font-medium text-gray-500 mb-1" title="active_cognitions_json">活跃认知</div>
          <pre className="text-xs text-gray-700 whitespace-pre-wrap overflow-x-auto max-h-48 overflow-y-auto border border-gray-200 rounded-lg p-2 bg-white">
            {prettyJson(row.active_cognitions_json)}
          </pre>
        </div>
        <div>
          <div className="text-xs font-medium text-gray-500 mb-1" title="teacher_participation_json">老师参与</div>
          <pre className="text-xs text-gray-700 whitespace-pre-wrap overflow-x-auto max-h-48 overflow-y-auto border border-gray-200 rounded-lg p-2 bg-white">
            {prettyJson(row.teacher_participation_json)}
          </pre>
        </div>
      </div>
    </div>
  )
}

function FilterSelect({
  label,
  value,
  options,
  onChange,
}: {
  label: string
  value: string
  options: Array<{ value: string; label: string }>
  onChange: (v: string) => void
}) {
  return (
    <div>
      <label className="block text-xs text-gray-500 mb-1">{label}</label>
      <select
        value={value}
        onChange={e => onChange(e.target.value)}
        className="border border-gray-300 rounded-lg px-3 py-2 text-sm bg-white"
      >
        {options.map(opt => (
          <option key={opt.value} value={opt.value}>
            {opt.label}
          </option>
        ))}
      </select>
    </div>
  )
}

function FilterInput({
  label,
  value,
  onChange,
  placeholder,
  type = 'text',
  width,
}: {
  label: string
  value: string
  onChange: (v: string) => void
  placeholder?: string
  type?: string
  width?: string
}) {
  return (
    <div>
      <label className="block text-xs text-gray-500 mb-1">{label}</label>
      <input
        type={type}
        value={value}
        placeholder={placeholder}
        onChange={e => onChange(e.target.value)}
        className={`border border-gray-300 rounded-lg px-3 py-2 text-sm ${width ?? ''}`}
      />
    </div>
  )
}
