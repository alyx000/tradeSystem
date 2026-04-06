import { useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../lib/api'
import type {
  PlanDiagnosticsItem,
  PlanDraftRecord,
  PlanFactCheck,
  PlanFactCheckResult,
  PlanJudgementCheck,
  PlanMarketView,
  PlanObservationRecord,
  PlanRecord,
  PlanReviewRecord,
  PlanSectorView,
  PlanWatchItem,
} from '../lib/types'

type Step = 'draft' | 'confirm' | 'diagnose' | 'review'

const STEPS: { key: Step; label: string }[] = [
  { key: 'draft', label: '草稿' },
  { key: 'confirm', label: '确认计划' },
  { key: 'diagnose', label: '诊断' },
  { key: 'review', label: '复盘' },
]

const BIAS_OPTIONS = ['主升', '震荡', '分歧', '退潮', '混沌']
const FACT_CHECK_OPTIONS = [
  'price_above_ma5',
  'price_above_ma10',
  'price_above_ma20',
  'ret_1d_gte',
  'ret_5d_gte',
  'announcement_exists',
  'sector_change_positive',
  'sector_limit_up_count_gte',
  'market_amount_gte_prev_day',
  'northbound_net_positive',
  'margin_balance_change_positive',
]
const JUDGEMENT_CHECK_TEMPLATES = [
  '主线确认',
  '是否具备带动性',
  '是否具备诚意反包',
  '首阴是否有价值',
  '情绪是否加强',
]

const FACT_CHECK_META: Record<
  string,
  {
    defaultLabel: string
    paramFields: Array<{ key: string; label: string; type: 'text' | 'number' }>
  }
> = {
  price_above_ma5: {
    defaultLabel: '站稳5日线',
    paramFields: [{ key: 'ts_code', label: '标的代码', type: 'text' }],
  },
  price_above_ma10: {
    defaultLabel: '站稳10日线',
    paramFields: [{ key: 'ts_code', label: '标的代码', type: 'text' }],
  },
  price_above_ma20: {
    defaultLabel: '站稳20日线',
    paramFields: [{ key: 'ts_code', label: '标的代码', type: 'text' }],
  },
  ret_1d_gte: {
    defaultLabel: '单日涨幅不低于阈值',
    paramFields: [
      { key: 'ts_code', label: '标的代码', type: 'text' },
      { key: 'value', label: '阈值', type: 'number' },
    ],
  },
  ret_5d_gte: {
    defaultLabel: '五日涨幅不低于阈值',
    paramFields: [
      { key: 'ts_code', label: '标的代码', type: 'text' },
      { key: 'value', label: '阈值', type: 'number' },
    ],
  },
  announcement_exists: {
    defaultLabel: '存在公告',
    paramFields: [{ key: 'ts_code', label: '标的代码', type: 'text' }],
  },
  sector_change_positive: {
    defaultLabel: '板块涨跌幅为正',
    paramFields: [{ key: 'sector_name', label: '板块名称', type: 'text' }],
  },
  sector_limit_up_count_gte: {
    defaultLabel: '板块涨停家数不少于阈值',
    paramFields: [
      { key: 'sector_name', label: '板块名称', type: 'text' },
      { key: 'value', label: '阈值', type: 'number' },
    ],
  },
  market_amount_gte_prev_day: {
    defaultLabel: '市场成交额不低于前一日',
    paramFields: [],
  },
  northbound_net_positive: {
    defaultLabel: '北向净流入为正',
    paramFields: [],
  },
  margin_balance_change_positive: {
    defaultLabel: '融资余额变化为正',
    paramFields: [],
  },
}

function normalizeFactCheck(check: Partial<PlanFactCheck> | undefined, nextType?: string): PlanFactCheck {
  const type = nextType || check?.check_type || 'ret_1d_gte'
  const meta = FACT_CHECK_META[type] || { defaultLabel: '', paramFields: [] }
  const params: Record<string, string | number> = { ...(check?.params || {}) }
  for (const field of meta.paramFields) {
    if (!(field.key in params)) {
      params[field.key] = field.type === 'number' ? 0 : ''
    }
  }
  return {
    ...check,
    check_type: type,
    label: check?.label || meta.defaultLabel,
    params,
  }
}

function normalizeWatchItems(items: PlanWatchItem[]): PlanWatchItem[] {
  return items.map((item, index) => ({
    ...item,
    priority: Number(item?.priority || index + 1),
    fact_checks: (item?.fact_checks || []).map((check, checkIndex: number) => ({
      ...normalizeFactCheck(check),
      priority: Number(check?.priority || checkIndex + 1),
    })),
    judgement_checks: (item?.judgement_checks || []).map((check) =>
      typeof check === 'string' ? { label: check, notes: '' } : { label: check.label || '', notes: check.notes || '' }
    ),
    trigger_conditions: (item?.trigger_conditions || []).map((condition) => String(condition ?? '')),
    invalidations: (item?.invalidations || []).map((condition) => String(condition ?? '')),
  }))
}

function moveItem<T>(items: T[], from: number, to: number) {
  const next = [...items]
  const [moved] = next.splice(from, 1)
  next.splice(to, 0, moved)
  return next
}

function FactCheckFields({
  prefix,
  check,
  onChange,
}: {
  prefix: string
  check: PlanFactCheck
  onChange: (next: PlanFactCheck) => void
}) {
  const meta = FACT_CHECK_META[check.check_type] || { paramFields: [] }
  if (meta.paramFields.length === 0) return null

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-2 md:col-span-2">
      {meta.paramFields.map(field => (
        <input
          key={field.key}
          aria-label={`${prefix}-${field.key}`}
          value={check.params?.[field.key] ?? ''}
          onChange={e =>
            onChange({
              ...check,
              params: {
                ...(check.params || {}),
                [field.key]:
                  field.type === 'number'
                    ? e.target.value === ''
                      ? ''
                      : Number(e.target.value)
                    : e.target.value,
              },
            })
          }
          placeholder={field.label}
          className="border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
        />
      ))}
    </div>
  )
}

const RESULT_CONFIG: Record<string, { label: string; className: string }> = {
  pass: { label: '通过', className: 'bg-green-100 text-green-800' },
  fail: { label: '未通过', className: 'bg-red-100 text-red-800' },
  missing_data: { label: '数据缺失', className: 'bg-orange-100 text-orange-800' },
  unsupported: { label: '暂不支持', className: 'bg-gray-100 text-gray-600' },
}

function StepIndicator({ current, planId }: { current: Step; planId?: string }) {
  const activeIdx = STEPS.findIndex(s => s.key === current)
  return (
    <div className="flex items-center gap-2 mb-6">
      {STEPS.map((s, i) => {
        const done = i < activeIdx
        const active = s.key === current
        const disabled = s.key === 'diagnose' && !planId
        return (
          <div key={s.key} className="flex items-center gap-2">
            <span
              className={`px-3 py-1 rounded-full text-sm font-medium ${
                active
                  ? 'bg-blue-600 text-white'
                  : done
                  ? 'bg-green-500 text-white'
                  : disabled
                  ? 'bg-gray-100 text-gray-400'
                  : 'bg-gray-200 text-gray-600'
              }`}
            >
              {s.label}
            </span>
            {i < STEPS.length - 1 && (
              <span className="text-gray-300 text-sm">›</span>
            )}
          </div>
        )
      })}
    </div>
  )
}

function DraftStep({
  date,
  onDraftCreated,
}: {
  date: string
  onDraftCreated: (draft: PlanDraftRecord) => void
}) {
  const [bias, setBias] = useState('混沌')
  const [themes, setThemes] = useState('')
  const [stocks, setStocks] = useState('')
  const [error, setError] = useState<string | null>(null)

  const createMutation = useMutation({
    mutationFn: () =>
      api.createPlanDraft({
        trade_date: date,
        market_facts: { bias },
        sector_facts: {
          main_themes: themes
            .split(/[,，]/)
            .map(s => s.trim())
            .filter(Boolean),
        },
        stock_facts: stocks
          .split(/[,，]/)
          .map(s => s.trim())
          .filter(Boolean)
          .map(name => ({ subject_name: name, reason: '关注' })),
        judgements: [],
        input_by: 'web',
      }),
    onSuccess: (draft) => {
      setError(null)
      onDraftCreated(draft)
    },
    onError: (e: Error) => setError(e.message),
  })

  return (
    <div className="space-y-4">
      <h2 className="text-lg font-semibold text-gray-800">填写观察输入，生成草稿</h2>
      <div className="grid grid-cols-1 gap-4 max-w-lg">
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">大盘偏向</label>
          <select
            value={bias}
            onChange={e => setBias(e.target.value)}
            className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
          >
            {BIAS_OPTIONS.map(opt => (
              <option key={opt} value={opt}>
                {opt}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">
            主线板块（逗号分隔）
          </label>
          <input
            type="text"
            value={themes}
            onChange={e => setThemes(e.target.value)}
            placeholder="如：AI算力, 机器人"
            className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">
            关注个股（逗号分隔，名称或代码）
          </label>
          <input
            type="text"
            value={stocks}
            onChange={e => setStocks(e.target.value)}
            placeholder="如：宁德时代, 英伟达"
            className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
        </div>
      </div>
      {error && <p className="text-red-600 text-sm">{error}</p>}
      <button
        onClick={() => createMutation.mutate()}
        disabled={createMutation.isPending}
        className="px-4 py-2 bg-blue-600 text-white rounded-md text-sm font-medium hover:bg-blue-700 disabled:opacity-50"
      >
        {createMutation.isPending ? '生成中...' : '生成草稿'}
      </button>
    </div>
  )
}

function DraftView({
  draft,
  onConfirm,
  onUpdated,
}: {
  draft: PlanDraftRecord
  onConfirm: (plan: PlanRecord) => void
  onUpdated: (draft: PlanDraftRecord) => void
}) {
  const [error, setError] = useState<string | null>(null)
  const [summary, setSummary] = useState(draft.summary || '')
  const initialWatchItems: PlanWatchItem[] = (() => {
    try {
      return JSON.parse(draft.watch_items_json || '[]') as PlanWatchItem[]
    } catch {
      return []
    }
  })()
  const marketView: PlanMarketView = (() => {
    try {
      return JSON.parse(draft.market_view_json || '{}') as PlanMarketView
    } catch {
      return {}
    }
  })()
  const sectorView: PlanSectorView = (() => {
    try {
      return JSON.parse(draft.sector_view_json || '{}') as PlanSectorView
    } catch {
      return {}
    }
  })()
  const [watchItemsText, setWatchItemsText] = useState(() => {
    try {
      return JSON.stringify(initialWatchItems, null, 2)
    } catch {
      return draft.watch_items_json || '[]'
    }
  })
  const initialFactCandidates: PlanFactCheck[] = (() => {
    try {
      return JSON.parse(draft.fact_check_candidates_json || '[]') as PlanFactCheck[]
    } catch {
      return []
    }
  })()
  const [factCandidatesText, setFactCandidatesText] = useState(() => {
    try {
      return JSON.stringify(initialFactCandidates, null, 2)
    } catch {
      return draft.fact_check_candidates_json || '[]'
    }
  })
  const initialJudgementCandidates: PlanJudgementCheck[] = (() => {
    try {
      return JSON.parse(draft.judgement_check_candidates_json || '[]') as PlanJudgementCheck[]
    } catch {
      return []
    }
  })()
  const [judgementCandidatesText, setJudgementCandidatesText] = useState(() => {
    try {
      return JSON.stringify(initialJudgementCandidates, null, 2)
    } catch {
      return draft.judgement_check_candidates_json || '[]'
    }
  })
  const [watchItems, setWatchItems] = useState<PlanWatchItem[]>(initialWatchItems)
  const [factCandidates, setFactCandidates] = useState<PlanFactCheck[]>(initialFactCandidates)
  const [judgementCandidates, setJudgementCandidates] = useState<PlanJudgementCheck[]>(initialJudgementCandidates)

  function syncDraftWatchItems(nextItems: PlanWatchItem[]) {
    setWatchItems(nextItems)
    setWatchItemsText(JSON.stringify(nextItems, null, 2))
  }

  function syncFactCandidates(nextItems: PlanFactCheck[]) {
    setFactCandidates(nextItems)
    setFactCandidatesText(JSON.stringify(nextItems, null, 2))
  }

  function syncJudgementCandidates(nextItems: PlanJudgementCheck[]) {
    setJudgementCandidates(nextItems)
    setJudgementCandidatesText(JSON.stringify(nextItems, null, 2))
  }

  const confirmMutation = useMutation({
    mutationFn: () =>
      api.confirmPlan(draft.draft_id, {
        trade_date: draft.trade_date,
        input_by: 'web',
      }),
    onSuccess: (plan) => {
      setError(null)
      onConfirm(plan)
    },
    onError: (e: Error) => setError(e.message),
  })

  const updateMutation = useMutation({
    mutationFn: () => {
      let parsedWatchItems
      let parsedFactCandidates
      let parsedJudgementCandidates
      try {
        parsedWatchItems = JSON.parse(watchItemsText || '[]')
      } catch {
        throw new Error('draft watch_items JSON 格式无效')
      }
      try {
        parsedFactCandidates = JSON.parse(factCandidatesText || '[]')
      } catch {
        throw new Error('draft fact_check_candidates JSON 格式无效')
      }
      try {
        parsedJudgementCandidates = JSON.parse(judgementCandidatesText || '[]')
      } catch {
        throw new Error('draft judgement_check_candidates JSON 格式无效')
      }
      return api.updatePlanDraft(draft.draft_id, {
        summary,
        watch_items: parsedWatchItems,
        fact_check_candidates: parsedFactCandidates,
        judgement_check_candidates: parsedJudgementCandidates,
        input_by: 'web',
      })
    },
    onSuccess: (nextDraft) => {
      setError(null)
      try {
        setWatchItems(JSON.parse(nextDraft.watch_items_json || '[]'))
      } catch {
        setWatchItems([])
      }
      try {
        setFactCandidates(JSON.parse(nextDraft.fact_check_candidates_json || '[]'))
      } catch {
        setFactCandidates([])
      }
      try {
        setJudgementCandidates(JSON.parse(nextDraft.judgement_check_candidates_json || '[]'))
      } catch {
        setJudgementCandidates([])
      }
      onUpdated(nextDraft)
    },
    onError: (e: Error) => setError(e.message),
  })

  return (
    <div className="space-y-4">
      <h2 className="text-lg font-semibold text-gray-800">草稿详情</h2>
      <div className="bg-gray-50 rounded-lg p-4 space-y-2">
        <p className="text-sm">
          <span className="font-medium text-gray-600">标题：</span>
          {draft.title}
        </p>
        <div>
          <label className="block text-sm font-medium text-gray-600 mb-1">摘要：</label>
          <textarea
            value={summary}
            onChange={e => setSummary(e.target.value)}
            rows={3}
            className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm bg-white focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
        </div>
        <p className="text-sm">
          <span className="font-medium text-gray-600">大盘偏向：</span>
          {marketView.bias || '—'}
        </p>
        <p className="text-sm">
          <span className="font-medium text-gray-600">主线板块：</span>
          {(sectorView.main_themes || []).join('、') || '—'}
        </p>
        <p className="text-sm">
          <span className="font-medium text-gray-600">草稿 ID：</span>
          <code className="text-xs bg-white px-1 py-0.5 rounded border">{draft.draft_id}</code>
        </p>
      </div>
      {watchItems.length > 0 && (
        <div>
          <p className="text-sm font-medium text-gray-700 mb-2">观察清单（{watchItems.length} 项）</p>
          <ul className="space-y-1">
            {watchItems.map((item, i: number) => (
              <li key={i} className="text-sm text-gray-700 bg-white border rounded px-3 py-2">
                {item.subject_name || item.subject_code || `条目 ${i + 1}`}
                {item.reason && (
                  <span className="text-gray-500 ml-2">— {item.reason}</span>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
      <div className="border rounded-lg bg-white p-3 space-y-3">
        <div className="flex items-center justify-between">
          <p className="text-sm font-medium text-gray-700">草稿观察清单</p>
          <button
            type="button"
            onClick={() =>
              syncDraftWatchItems([
                ...watchItems,
                {
                  subject_type: 'stock',
                  subject_code: '',
                  subject_name: '',
                  reason: '',
                },
              ])
            }
            className="text-xs px-2 py-1 bg-white border border-gray-300 rounded hover:border-gray-400"
          >
            新增观察项
          </button>
        </div>
        {watchItems.map((item, index: number) => (
          <div key={index} className="grid grid-cols-1 md:grid-cols-[1fr_1fr_2fr_auto] gap-2 items-center">
            <input
              aria-label={`draft-watch-item-code-${index}`}
              value={item.subject_code || ''}
              onChange={e => {
                const next = [...watchItems]
                next[index] = { ...item, subject_code: e.target.value }
                syncDraftWatchItems(next)
              }}
              placeholder="标的代码"
              className="border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
            <input
              aria-label={`draft-watch-item-name-${index}`}
              value={item.subject_name || ''}
              onChange={e => {
                const next = [...watchItems]
                next[index] = { ...item, subject_name: e.target.value }
                syncDraftWatchItems(next)
              }}
              placeholder="标的名称"
              className="border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
            <input
              aria-label={`draft-watch-item-reason-${index}`}
              value={item.reason || ''}
              onChange={e => {
                const next = [...watchItems]
                next[index] = { ...item, reason: e.target.value }
                syncDraftWatchItems(next)
              }}
              placeholder="观察理由"
              className="border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
            <button
              type="button"
              onClick={() => syncDraftWatchItems(watchItems.filter((_, i) => i !== index))}
              className="text-xs text-red-600 hover:underline"
            >
              删除
            </button>
          </div>
        ))}
        {watchItems.length === 0 && (
          <p className="text-xs text-gray-400">暂无草稿观察项</p>
        )}
      </div>
      <div className="border rounded-lg bg-white p-3 space-y-3">
        <div className="flex items-center justify-between">
          <p className="text-sm font-medium text-gray-700">候选客观检查项</p>
          <button
            type="button"
            onClick={() =>
              syncFactCandidates([
                ...factCandidates,
                { check_type: 'ret_1d_gte', label: '', params: { value: 0 } },
              ])
            }
            className="text-xs px-2 py-1 bg-white border border-gray-300 rounded hover:border-gray-400"
          >
            新增候选项
          </button>
        </div>
                {factCandidates.map((check, index: number) => (
          <div key={index} className="grid grid-cols-1 md:grid-cols-[1fr_1fr_auto] gap-2 items-start">
            <select
              aria-label={`draft-fact-check-type-${index}`}
              value={check.check_type || 'ret_1d_gte'}
              onChange={e => {
                const next = [...factCandidates]
                next[index] = normalizeFactCheck(check, e.target.value)
                syncFactCandidates(next)
              }}
              className="border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            >
              {FACT_CHECK_OPTIONS.map(opt => (
                <option key={opt} value={opt}>
                  {opt}
                </option>
              ))}
            </select>
            <input
              aria-label={`draft-fact-check-label-${index}`}
              value={check.label || ''}
              onChange={e => {
                const next = [...factCandidates]
                next[index] = { ...check, label: e.target.value }
                syncFactCandidates(next)
              }}
              placeholder="标签"
              className="border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
            <button
              type="button"
              onClick={() => syncFactCandidates(factCandidates.filter((_, i) => i !== index))}
              className="text-xs text-red-600 hover:underline"
            >
              删除
            </button>
            <FactCheckFields
              prefix={`draft-fact-check-${index}`}
              check={normalizeFactCheck(check)}
              onChange={nextCheck => {
                const next = [...factCandidates]
                next[index] = nextCheck
                syncFactCandidates(next)
              }}
            />
          </div>
        ))}
        {factCandidates.length === 0 && (
          <p className="text-xs text-gray-400">暂无候选客观检查项</p>
        )}
      </div>
      <div className="border rounded-lg bg-white p-3 space-y-3">
        <div className="flex items-center justify-between">
          <p className="text-sm font-medium text-gray-700">候选主观判断项</p>
          <button
            type="button"
            onClick={() =>
              syncJudgementCandidates([
                ...judgementCandidates,
                { label: '', notes: '' },
              ])
            }
            className="text-xs px-2 py-1 bg-white border border-gray-300 rounded hover:border-gray-400"
          >
            新增候选判断
          </button>
        </div>
        {judgementCandidates.map((check, index: number) => (
          <div key={index} className="grid grid-cols-1 md:grid-cols-[1fr_2fr_auto] gap-2 items-center">
            <select
              aria-label={`draft-judgement-check-template-${index}`}
              value={check.label || ''}
              onChange={e => {
                const next = [...judgementCandidates]
                next[index] = { ...check, label: e.target.value }
                syncJudgementCandidates(next)
              }}
              className="border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            >
              <option value="">选择模板</option>
              {JUDGEMENT_CHECK_TEMPLATES.map(opt => (
                <option key={opt} value={opt}>
                  {opt}
                </option>
              ))}
            </select>
            <input
              aria-label={`draft-judgement-check-label-${index}`}
              value={check.label || ''}
              onChange={e => {
                const next = [...judgementCandidates]
                next[index] = { ...check, label: e.target.value }
                syncJudgementCandidates(next)
              }}
              placeholder="判断项"
              className="border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
            <input
              aria-label={`draft-judgement-check-notes-${index}`}
              value={check.notes || ''}
              onChange={e => {
                const next = [...judgementCandidates]
                next[index] = { ...check, notes: e.target.value }
                syncJudgementCandidates(next)
              }}
              placeholder="备注"
              className="border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
            <button
              type="button"
              onClick={() => syncJudgementCandidates(judgementCandidates.filter((_, i) => i !== index))}
              className="text-xs text-red-600 hover:underline"
            >
              删除
            </button>
          </div>
        ))}
        {judgementCandidates.length === 0 && (
          <p className="text-xs text-gray-400">暂无候选主观判断项</p>
        )}
      </div>
      <details className="border rounded-lg bg-gray-50 p-3">
        <summary className="cursor-pointer text-sm font-medium text-gray-700">
          高级 JSON 编辑
        </summary>
        <div className="mt-3 space-y-3">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Draft Watch Items JSON</label>
            <textarea
              aria-label="Draft Watch Items JSON"
              value={watchItemsText}
              onChange={e => setWatchItemsText(e.target.value)}
              rows={8}
              className="w-full border border-gray-300 rounded-md px-3 py-2 text-xs font-mono bg-white focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Fact Check Candidates JSON</label>
            <textarea
              aria-label="Fact Check Candidates JSON"
              value={factCandidatesText}
              onChange={e => setFactCandidatesText(e.target.value)}
              rows={6}
              className="w-full border border-gray-300 rounded-md px-3 py-2 text-xs font-mono bg-white focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Judgement Check Candidates JSON</label>
            <textarea
              aria-label="Judgement Check Candidates JSON"
              value={judgementCandidatesText}
              onChange={e => setJudgementCandidatesText(e.target.value)}
              rows={6}
              className="w-full border border-gray-300 rounded-md px-3 py-2 text-xs font-mono bg-white focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>
        </div>
      </details>
      {error && <p className="text-red-600 text-sm">{error}</p>}
      <div className="flex gap-2">
        <button
          onClick={() => updateMutation.mutate()}
          disabled={updateMutation.isPending}
          className="px-4 py-2 bg-white border border-gray-300 text-gray-700 rounded-md text-sm font-medium hover:border-gray-400 disabled:opacity-50"
        >
          {updateMutation.isPending ? '保存中...' : '保存草稿'}
        </button>
        <button
          onClick={() => confirmMutation.mutate()}
          disabled={confirmMutation.isPending}
          className="px-4 py-2 bg-green-600 text-white rounded-md text-sm font-medium hover:bg-green-700 disabled:opacity-50"
        >
          {confirmMutation.isPending ? '确认中...' : '确认计划'}
        </button>
      </div>
    </div>
  )
}

function PlanEditor({
  plan,
  onUpdated,
}: {
  plan: PlanRecord
  onUpdated: (plan: PlanRecord) => void
}) {
  const initialWatchItems = normalizeWatchItems((() => {
    try {
      return JSON.parse(plan.watch_items_json || '[]')
    } catch {
      return []
    }
  })())
  const [title, setTitle] = useState(plan.title || '')
  const [marketBias, setMarketBias] = useState(plan.market_bias || '混沌')
  const [watchItems, setWatchItems] = useState<PlanWatchItem[]>(initialWatchItems)
  const [watchItemsText, setWatchItemsText] = useState(() => {
    try {
      return JSON.stringify(initialWatchItems, null, 2)
    } catch {
      return plan.watch_items_json || '[]'
    }
  })
  const [error, setError] = useState<string | null>(null)

  function syncWatchItems(nextItems: PlanWatchItem[]) {
    const normalized = normalizeWatchItems(nextItems)
    setWatchItems(normalized)
    setWatchItemsText(JSON.stringify(normalized, null, 2))
  }

  const updateMutation = useMutation({
    mutationFn: () => {
      let watchItems
      try {
        watchItems = JSON.parse(watchItemsText || '[]')
      } catch {
        throw new Error('watch_items JSON 格式无效')
      }
      return api.updatePlan(plan.plan_id, {
        title,
        market_bias: marketBias,
        watch_items: watchItems,
        input_by: 'web',
      })
    },
    onSuccess: (nextPlan) => {
      setError(null)
      onUpdated(nextPlan)
    },
    onError: (e: Error) => setError(e.message),
  })

  return (
    <div className="bg-gray-50 rounded-lg p-4 space-y-3">
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">计划标题</label>
        <input
          value={title}
          onChange={e => setTitle(e.target.value)}
          className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm bg-white focus:outline-none focus:ring-2 focus:ring-blue-500"
        />
      </div>
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">大盘偏向</label>
        <select
          value={marketBias}
          onChange={e => setMarketBias(e.target.value)}
          className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm bg-white focus:outline-none focus:ring-2 focus:ring-blue-500"
        >
          {BIAS_OPTIONS.map(opt => (
            <option key={opt} value={opt}>
              {opt}
            </option>
          ))}
        </select>
      </div>
      <div>
        <div className="flex items-center justify-between mb-2">
          <label className="block text-sm font-medium text-gray-700">Watch Items</label>
          <button
            type="button"
            onClick={() =>
              syncWatchItems([
                ...watchItems,
                {
                  subject_type: 'stock',
                  subject_code: '',
                  subject_name: '',
                  reason: '',
                  fact_checks: [],
                  judgement_checks: [],
                  trigger_conditions: [],
                  invalidations: [],
                  priority: watchItems.length + 1,
                },
              ])
            }
            className="text-xs px-2 py-1 bg-white border border-gray-300 rounded hover:border-gray-400"
          >
            新增条目
          </button>
        </div>
        <div className="space-y-3">
          {watchItems.length === 0 && (
            <p className="text-xs text-gray-500">暂无 watch item，可新增后再细化。</p>
          )}
          {watchItems.map((item, index: number) => (
            <div key={index} className="border border-gray-200 rounded-lg bg-white p-3 space-y-2">
              <div className="flex items-center justify-between">
                <p className="text-xs font-semibold text-gray-700">条目 {index + 1}</p>
                <div className="flex items-center gap-3">
                  <label className="flex items-center gap-1 text-xs text-gray-500">
                    优先级
                    <input
                      aria-label={`watch-item-priority-${index}`}
                      type="number"
                      min={1}
                      value={item.priority || index + 1}
                      onChange={e => {
                        const next = [...watchItems]
                        next[index] = {
                          ...item,
                          priority: e.target.value === '' ? '' : Number(e.target.value),
                        }
                        syncWatchItems(next)
                      }}
                      className="w-16 border border-gray-300 rounded px-2 py-1 text-xs"
                    />
                  </label>
                  <button
                    type="button"
                    aria-label={`watch-item-move-up-${index}`}
                    disabled={index === 0}
                    onClick={() => syncWatchItems(moveItem(watchItems, index, index - 1))}
                    className="text-xs text-gray-600 hover:underline disabled:text-gray-300"
                  >
                    上移
                  </button>
                  <button
                    type="button"
                    aria-label={`watch-item-move-down-${index}`}
                    disabled={index === watchItems.length - 1}
                    onClick={() => syncWatchItems(moveItem(watchItems, index, index + 1))}
                    className="text-xs text-gray-600 hover:underline disabled:text-gray-300"
                  >
                    下移
                  </button>
                  <button
                    type="button"
                    onClick={() => syncWatchItems(watchItems.filter((_, i) => i !== index))}
                    className="text-xs text-red-600 hover:underline"
                  >
                    删除
                  </button>
                </div>
              </div>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                <input
                  aria-label={`watch-item-code-${index}`}
                  value={item.subject_code || ''}
                  onChange={e => {
                    const next = [...watchItems]
                    next[index] = { ...item, subject_code: e.target.value }
                    syncWatchItems(next)
                  }}
                  placeholder="标的代码"
                  className="border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
                <input
                  aria-label={`watch-item-name-${index}`}
                  value={item.subject_name || ''}
                  onChange={e => {
                    const next = [...watchItems]
                    next[index] = { ...item, subject_name: e.target.value }
                    syncWatchItems(next)
                  }}
                  placeholder="标的名称"
                  className="border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
              </div>
              <textarea
                aria-label={`watch-item-reason-${index}`}
                value={item.reason || ''}
                onChange={e => {
                  const next = [...watchItems]
                  next[index] = { ...item, reason: e.target.value }
                  syncWatchItems(next)
                }}
                rows={2}
                placeholder="观察理由"
                className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
              <div className="text-xs text-gray-500">
                fact_checks: {(item.fact_checks || []).length} 项，judgement_checks: {(item.judgement_checks || []).length} 项
              </div>
              <div className="border-t pt-2 space-y-2">
                <div className="flex items-center justify-between">
                  <p className="text-xs font-medium text-gray-700">客观检查项</p>
                  <button
                    type="button"
                    onClick={() => {
                      const next = [...watchItems]
                      next[index] = {
                        ...item,
                        fact_checks: [
                          ...(item.fact_checks || []),
                          { check_type: 'ret_1d_gte', label: '', params: { value: 0 } },
                        ],
                      }
                      syncWatchItems(next)
                    }}
                    className="text-xs px-2 py-1 bg-white border border-gray-300 rounded hover:border-gray-400"
                  >
                    新增检查项
                  </button>
                </div>
                {(item.fact_checks || []).length === 0 && (
                  <p className="text-xs text-gray-400">暂无 fact check</p>
                )}
                {(item.fact_checks || []).map((check, checkIndex: number) => (
                  <div key={checkIndex} className="grid grid-cols-1 md:grid-cols-[1fr_1fr_auto] gap-2 items-start">
                    <select
                      aria-label={`fact-check-type-${index}-${checkIndex}`}
                      value={check.check_type || 'ret_1d_gte'}
                      onChange={e => {
                        const next = [...watchItems]
                        const nextChecks = [...(item.fact_checks || [])]
                        nextChecks[checkIndex] = normalizeFactCheck(check, e.target.value)
                        next[index] = { ...item, fact_checks: nextChecks }
                        syncWatchItems(next)
                      }}
                      className="border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                    >
                      {FACT_CHECK_OPTIONS.map(opt => (
                        <option key={opt} value={opt}>
                          {opt}
                        </option>
                      ))}
                    </select>
                    <input
                      aria-label={`fact-check-label-${index}-${checkIndex}`}
                      value={check.label || ''}
                      onChange={e => {
                        const next = [...watchItems]
                        const nextChecks = [...(item.fact_checks || [])]
                        nextChecks[checkIndex] = { ...check, label: e.target.value }
                        next[index] = { ...item, fact_checks: nextChecks }
                        syncWatchItems(next)
                      }}
                      placeholder="标签"
                      className="border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                    />
                    <button
                      type="button"
                      onClick={() => {
                        const next = [...watchItems]
                        const nextChecks = (item.fact_checks || []).filter((_, i: number) => i !== checkIndex)
                        next[index] = { ...item, fact_checks: nextChecks }
                        syncWatchItems(next)
                      }}
                      className="text-xs text-red-600 hover:underline"
                    >
                      删除
                    </button>
                    <div className="flex items-center gap-3 text-xs text-gray-500 md:col-span-3">
                      <label className="flex items-center gap-1">
                        检查项优先级
                        <input
                          aria-label={`fact-check-priority-${index}-${checkIndex}`}
                          type="number"
                          min={1}
                          value={check.priority || checkIndex + 1}
                          onChange={e => {
                            const next = [...watchItems]
                            const nextChecks = [...(item.fact_checks || [])]
                            nextChecks[checkIndex] = {
                              ...check,
                              priority: e.target.value === '' ? '' : Number(e.target.value),
                            }
                            next[index] = { ...item, fact_checks: nextChecks }
                            syncWatchItems(next)
                          }}
                          className="w-16 border border-gray-300 rounded px-2 py-1 text-xs"
                        />
                      </label>
                      <button
                        type="button"
                        aria-label={`fact-check-move-up-${index}-${checkIndex}`}
                        disabled={checkIndex === 0}
                        onClick={() => {
                          const next = [...watchItems]
                          const nextChecks = moveItem(item.fact_checks || [], checkIndex, checkIndex - 1)
                          next[index] = { ...item, fact_checks: nextChecks }
                          syncWatchItems(next)
                        }}
                        className="text-xs text-gray-600 hover:underline disabled:text-gray-300"
                      >
                        上移
                      </button>
                      <button
                        type="button"
                        aria-label={`fact-check-move-down-${index}-${checkIndex}`}
                        disabled={checkIndex === (item.fact_checks || []).length - 1}
                        onClick={() => {
                          const next = [...watchItems]
                          const nextChecks = moveItem(item.fact_checks || [], checkIndex, checkIndex + 1)
                          next[index] = { ...item, fact_checks: nextChecks }
                          syncWatchItems(next)
                        }}
                        className="text-xs text-gray-600 hover:underline disabled:text-gray-300"
                      >
                        下移
                      </button>
                    </div>
                    <FactCheckFields
                      prefix={`fact-check-${index}-${checkIndex}`}
                      check={normalizeFactCheck(check)}
                      onChange={nextCheck => {
                        const next = [...watchItems]
                        const nextChecks = [...(item.fact_checks || [])]
                        nextChecks[checkIndex] = nextCheck
                        next[index] = { ...item, fact_checks: nextChecks }
                        syncWatchItems(next)
                      }}
                    />
                  </div>
                ))}
              </div>
              <div className="border-t pt-2 space-y-2">
                <div className="flex items-center justify-between">
                  <p className="text-xs font-medium text-gray-700">主观判断项</p>
                  <button
                    type="button"
                    onClick={() => {
                      const next = [...watchItems]
                      next[index] = {
                        ...item,
                        judgement_checks: [
                          ...(item.judgement_checks || []),
                          { label: '', notes: '' },
                        ],
                      }
                      syncWatchItems(next)
                    }}
                    className="text-xs px-2 py-1 bg-white border border-gray-300 rounded hover:border-gray-400"
                  >
                    新增判断项
                  </button>
                </div>
                {(item.judgement_checks || []).length === 0 && (
                  <p className="text-xs text-gray-400">暂无 judgement check</p>
                )}
                {(item.judgement_checks || []).map((check, checkIndex: number) => (
                  <div key={checkIndex} className="grid grid-cols-1 md:grid-cols-[1fr_2fr_auto] gap-2 items-center">
                    <select
                      aria-label={`judgement-check-template-${index}-${checkIndex}`}
                      value={check.label || ''}
                      onChange={e => {
                        const next = [...watchItems]
                        const nextChecks = [...(item.judgement_checks || [])]
                        nextChecks[checkIndex] = { ...check, label: e.target.value }
                        next[index] = { ...item, judgement_checks: nextChecks }
                        syncWatchItems(next)
                      }}
                      className="border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                    >
                      <option value="">选择模板</option>
                      {JUDGEMENT_CHECK_TEMPLATES.map(opt => (
                        <option key={opt} value={opt}>
                          {opt}
                        </option>
                      ))}
                    </select>
                    <input
                      aria-label={`judgement-check-label-${index}-${checkIndex}`}
                      value={check.label || ''}
                      onChange={e => {
                        const next = [...watchItems]
                        const nextChecks = [...(item.judgement_checks || [])]
                        nextChecks[checkIndex] = { ...check, label: e.target.value }
                        next[index] = { ...item, judgement_checks: nextChecks }
                        syncWatchItems(next)
                      }}
                      placeholder="判断项"
                      className="border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                    />
                    <input
                      aria-label={`judgement-check-notes-${index}-${checkIndex}`}
                      value={check.notes || ''}
                      onChange={e => {
                        const next = [...watchItems]
                        const nextChecks = [...(item.judgement_checks || [])]
                        nextChecks[checkIndex] = { ...check, notes: e.target.value }
                        next[index] = { ...item, judgement_checks: nextChecks }
                        syncWatchItems(next)
                      }}
                      placeholder="备注"
                      className="border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                    />
                    <button
                      type="button"
                      onClick={() => {
                        const next = [...watchItems]
                        const nextChecks = (item.judgement_checks || []).filter((_, i: number) => i !== checkIndex)
                        next[index] = { ...item, judgement_checks: nextChecks }
                        syncWatchItems(next)
                      }}
                      className="text-xs text-red-600 hover:underline"
                    >
                      删除
                    </button>
                  </div>
                ))}
              </div>
              <div className="border-t pt-2 space-y-2">
                <div className="flex items-center justify-between">
                  <p className="text-xs font-medium text-gray-700">触发条件</p>
                  <button
                    type="button"
                    onClick={() => {
                      const next = [...watchItems]
                      next[index] = {
                        ...item,
                        trigger_conditions: [...(item.trigger_conditions || []), ''],
                      }
                      syncWatchItems(next)
                    }}
                    className="text-xs px-2 py-1 bg-white border border-gray-300 rounded hover:border-gray-400"
                  >
                    新增触发条件
                  </button>
                </div>
                {(item.trigger_conditions || []).length === 0 && (
                  <p className="text-xs text-gray-400">暂无 trigger condition</p>
                )}
                {(item.trigger_conditions || []).map((condition, conditionIndex: number) => (
                  <div key={conditionIndex} className="grid grid-cols-1 md:grid-cols-[1fr_auto] gap-2 items-center">
                    <input
                      aria-label={`trigger-condition-${index}-${conditionIndex}`}
                      value={condition}
                      onChange={e => {
                        const next = [...watchItems]
                        const nextConditions = [...(item.trigger_conditions || [])]
                        nextConditions[conditionIndex] = e.target.value
                        next[index] = { ...item, trigger_conditions: nextConditions }
                        syncWatchItems(next)
                      }}
                      placeholder="触发条件"
                      className="border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                    />
                    <button
                      type="button"
                      onClick={() => {
                        const next = [...watchItems]
                        const nextConditions = (item.trigger_conditions || []).filter((_, i: number) => i !== conditionIndex)
                        next[index] = { ...item, trigger_conditions: nextConditions }
                        syncWatchItems(next)
                      }}
                      className="text-xs text-red-600 hover:underline"
                    >
                      删除
                    </button>
                  </div>
                ))}
              </div>
              <div className="border-t pt-2 space-y-2">
                <div className="flex items-center justify-between">
                  <p className="text-xs font-medium text-gray-700">失效条件</p>
                  <button
                    type="button"
                    onClick={() => {
                      const next = [...watchItems]
                      next[index] = {
                        ...item,
                        invalidations: [...(item.invalidations || []), ''],
                      }
                      syncWatchItems(next)
                    }}
                    className="text-xs px-2 py-1 bg-white border border-gray-300 rounded hover:border-gray-400"
                  >
                    新增失效条件
                  </button>
                </div>
                {(item.invalidations || []).length === 0 && (
                  <p className="text-xs text-gray-400">暂无 invalidation</p>
                )}
                {(item.invalidations || []).map((condition, conditionIndex: number) => (
                  <div key={conditionIndex} className="grid grid-cols-1 md:grid-cols-[1fr_auto] gap-2 items-center">
                    <input
                      aria-label={`invalidation-${index}-${conditionIndex}`}
                      value={condition}
                      onChange={e => {
                        const next = [...watchItems]
                        const nextConditions = [...(item.invalidations || [])]
                        nextConditions[conditionIndex] = e.target.value
                        next[index] = { ...item, invalidations: nextConditions }
                        syncWatchItems(next)
                      }}
                      placeholder="失效条件"
                      className="border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                    />
                    <button
                      type="button"
                      onClick={() => {
                        const next = [...watchItems]
                        const nextConditions = (item.invalidations || []).filter((_, i: number) => i !== conditionIndex)
                        next[index] = { ...item, invalidations: nextConditions }
                        syncWatchItems(next)
                      }}
                      className="text-xs text-red-600 hover:underline"
                    >
                      删除
                    </button>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>
      <details className="border rounded-lg bg-gray-50 p-3">
        <summary className="cursor-pointer text-sm font-medium text-gray-700">
          高级 JSON 编辑
        </summary>
        <div className="mt-3">
          <label className="block text-sm font-medium text-gray-700 mb-1">
            Watch Items JSON
          </label>
          <textarea
            aria-label="Watch Items JSON"
            value={watchItemsText}
            onChange={e => setWatchItemsText(e.target.value)}
            rows={12}
            className="w-full border border-gray-300 rounded-md px-3 py-2 text-xs font-mono bg-white focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
          <p className="text-xs text-gray-500 mt-1">
            直接编辑执行清单，包含 `fact_checks` / `judgement_checks`。
          </p>
        </div>
      </details>
      {error && <p className="text-red-600 text-sm">{error}</p>}
      <button
        onClick={() => updateMutation.mutate()}
        disabled={updateMutation.isPending}
        className="px-4 py-2 bg-white border border-gray-300 text-gray-700 rounded-md text-sm font-medium hover:border-gray-400 disabled:opacity-50"
      >
        {updateMutation.isPending ? '保存中...' : '保存计划'}
      </button>
    </div>
  )
}

function DiagnoseView({ plan }: { plan: PlanRecord }) {
  const { data: diagnostics, isLoading } = useQuery({
    queryKey: ['plan-diagnostics', plan.plan_id],
    queryFn: () => api.getPlanDiagnostics(plan.plan_id),
  })

  if (isLoading) return <p className="text-sm text-gray-500">诊断加载中...</p>
  if (!diagnostics) return <p className="text-sm text-gray-500">无诊断数据</p>

  const items: PlanDiagnosticsItem[] = diagnostics.items_json || []

  return (
    <div className="space-y-4">
      <h2 className="text-lg font-semibold text-gray-800">诊断面板</h2>
      <div className="grid grid-cols-3 gap-3">
        <div className="bg-green-50 rounded-lg p-3 text-center">
          <p className="text-2xl font-bold text-green-700">{diagnostics.data_ready_count}</p>
          <p className="text-xs text-green-600 mt-1">数据就绪</p>
        </div>
        <div className="bg-orange-50 rounded-lg p-3 text-center">
          <p className="text-2xl font-bold text-orange-700">{diagnostics.missing_data_count}</p>
          <p className="text-xs text-orange-600 mt-1">数据缺失</p>
        </div>
        <div className="bg-gray-50 rounded-lg p-3 text-center">
          <p className="text-2xl font-bold text-gray-600">{diagnostics.unsupported_check_count}</p>
          <p className="text-xs text-gray-500 mt-1">暂不支持</p>
        </div>
      </div>

      {items.length > 0 && (
        <div className="space-y-3">
          <p className="text-sm font-medium text-gray-700">逐项诊断</p>
          {items.map((item, i: number) => {
            const factResults: PlanFactCheckResult[] = item.fact_check_results || []
            const judgementChecks: Array<PlanJudgementCheck | string> = item.judgement_checks || []
            const missingDependencies = item.missing_dependencies || []
            const unsupportedChecks = item.unsupported_checks || []
            return (
              <div key={i} className="border rounded-lg p-4 bg-white space-y-3">
                <div className="flex items-center gap-2">
                  <span
                    className={`w-2 h-2 rounded-full ${
                      item.data_ready ? 'bg-green-500' : 'bg-orange-400'
                    }`}
                  />
                  <span className="font-medium text-sm text-gray-800">
                    {item.subject_name || item.subject_code || `条目 ${i + 1}`}
                  </span>
                </div>

                {factResults.length > 0 && (
                  <div>
                    <p className="text-xs text-gray-500 mb-1 uppercase tracking-wide">
                      客观核查项
                    </p>
                    <div className="space-y-1">
                      {factResults.map((fc, j: number) => {
                        const cfg = RESULT_CONFIG[fc.result] ?? {
                          label: fc.result,
                          className: 'bg-gray-100 text-gray-600',
                        }
                        return (
                          <div key={j} className="flex items-center gap-2 text-sm">
                            <span className={`px-2 py-0.5 rounded text-xs font-medium ${cfg.className}`}>
                              {cfg.label}
                            </span>
                            <span className="text-gray-700">{fc.label}</span>
                          </div>
                        )
                      })}
                    </div>
                  </div>
                )}

                {judgementChecks.length > 0 && (
                  <div>
                    <p className="text-xs text-gray-500 mb-1 uppercase tracking-wide">
                      主观判断项（需人工判断）
                    </p>
                    <div className="space-y-1">
                      {judgementChecks.map((jc, j: number) => (
                        <div key={j} className="flex items-center gap-2 text-sm">
                          <span className="px-2 py-0.5 rounded text-xs font-medium bg-purple-50 text-purple-700 border border-purple-200">
                            需人工判断
                          </span>
                          <span className="text-gray-700">{typeof jc === 'string' ? jc : jc.label}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {missingDependencies.length > 0 && (
                  <p className="text-xs text-orange-600">
                    缺失数据：{missingDependencies.join('、')}
                  </p>
                )}
                {unsupportedChecks.length > 0 && (
                  <p className="text-xs text-gray-500">
                    暂不支持：{unsupportedChecks.join('、')}
                  </p>
                )}
              </div>
            )
          })}
        </div>
      )}

      <div className="bg-blue-50 rounded p-3 text-xs text-blue-700">
        诊断生成于 {diagnostics.generated_at}，共 {diagnostics.watch_item_count} 个观察标的，
        客观核查项 {diagnostics.fact_check_count} 个，主观判断项 {diagnostics.judgement_check_count} 个。
      </div>
    </div>
  )
}

function ReviewStep({
  plan,
  onReviewed,
}: {
  plan: PlanRecord
  onReviewed: (review: PlanReviewRecord) => void
}) {
  const [summary, setSummary] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState(false)

  const reviewMutation = useMutation({
    mutationFn: () =>
      api.reviewPlan(plan.plan_id, {
        trade_date: plan.trade_date,
        outcome_summary: summary || '待补充',
        input_by: 'web',
      }),
    onSuccess: (review) => {
      setError(null)
      setDone(true)
      onReviewed(review)
    },
    onError: (e: Error) => setError(e.message),
  })

  if (done) {
    return (
      <div className="space-y-4">
        <h2 className="text-lg font-semibold text-gray-800">复盘</h2>
        <div className="bg-green-50 border border-green-200 rounded-lg p-4 text-green-800 text-sm">
          复盘已写入，计划状态已更新为「已复盘」。
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-4">
      <h2 className="text-lg font-semibold text-gray-800">复盘</h2>
      <div className="bg-gray-50 rounded-lg p-4 text-sm space-y-1">
        <p>
          <span className="font-medium text-gray-600">计划 ID：</span>
          <code className="text-xs bg-white px-1 py-0.5 rounded border">{plan.plan_id}</code>
        </p>
        <p>
          <span className="font-medium text-gray-600">交易日：</span>
          {plan.trade_date}
        </p>
        <p>
          <span className="font-medium text-gray-600">状态：</span>
          {plan.status}
        </p>
      </div>
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">
          复盘总结（outcome_summary）
        </label>
        <textarea
          value={summary}
          onChange={e => setSummary(e.target.value)}
          rows={4}
          placeholder="计划整体执行情况、命中/偏差、下次改进方向..."
          className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
        />
      </div>
      {error && <p className="text-red-600 text-sm">{error}</p>}
      <button
        onClick={() => reviewMutation.mutate()}
        disabled={reviewMutation.isPending}
        className="px-4 py-2 bg-indigo-600 text-white rounded-md text-sm font-medium hover:bg-indigo-700 disabled:opacity-50"
      >
        {reviewMutation.isPending ? '提交中...' : '提交复盘'}
      </button>
    </div>
  )
}

function ObservationEditor({
  observation,
  onUpdated,
}: {
  observation: PlanObservationRecord
  onUpdated: (observation: PlanObservationRecord) => void
}) {
  const initialJudgements = (() => {
    try {
      const value = JSON.parse(observation.judgements_json || '[]')
      return Array.isArray(value) ? value.join('\n') : ''
    } catch {
      return ''
    }
  })()
  const [title, setTitle] = useState(observation.title || '')
  const [judgements, setJudgements] = useState(initialJudgements)
  const [error, setError] = useState<string | null>(null)

  const updateMutation = useMutation({
    mutationFn: () =>
      api.updatePlanObservation(observation.observation_id, {
        title,
        judgements: judgements
          .split('\n')
          .map(item => item.trim())
          .filter(Boolean),
        input_by: 'web',
      }),
    onSuccess: (nextObservation) => {
      setError(null)
      onUpdated(nextObservation)
    },
    onError: (e: Error) => setError(e.message),
  })

  return (
    <div className="bg-white border rounded-lg p-3 space-y-3">
      <p className="text-xs font-semibold text-gray-700">编辑 observation</p>
      <div>
        <label className="block text-xs font-medium text-gray-600 mb-1">标题</label>
        <input
          value={title}
          onChange={e => setTitle(e.target.value)}
          className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
        />
      </div>
      <div>
        <label className="block text-xs font-medium text-gray-600 mb-1">判断项（每行一条）</label>
        <textarea
          value={judgements}
          onChange={e => setJudgements(e.target.value)}
          rows={4}
          className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
        />
      </div>
      {error && <p className="text-red-600 text-xs">{error}</p>}
      <button
        onClick={() => updateMutation.mutate()}
        disabled={updateMutation.isPending}
        className="px-3 py-2 bg-white border border-gray-300 text-gray-700 rounded-md text-sm font-medium hover:border-gray-400 disabled:opacity-50"
      >
        {updateMutation.isPending ? '保存中...' : '保存 observation'}
      </button>
    </div>
  )
}

function RecentObjectsPanel({
  date,
  onSelectDraft,
  onSelectPlan,
  onObservationUpdated,
}: {
  date: string
  onSelectDraft: (draft: PlanDraftRecord) => void
  onSelectPlan: (plan: PlanRecord) => void
  onObservationUpdated: (observation: PlanObservationRecord) => void
}) {
  const [selectedObservation, setSelectedObservation] = useState<PlanObservationRecord | null>(null)
  const { data: observations } = useQuery({
    queryKey: ['plan-observations', date],
    queryFn: () => api.listPlanObservations(date, 8),
  })
  const { data: drafts } = useQuery({
    queryKey: ['plan-drafts', date],
    queryFn: () => api.listPlanDrafts(date, 8),
  })
  const { data: plans } = useQuery({
    queryKey: ['plans', date],
    queryFn: () => api.listPlans(date, 8),
  })

  return (
    <aside className="bg-gray-50 border border-gray-200 rounded-lg p-4 space-y-5">
      <div>
        <h2 className="text-sm font-semibold text-gray-800 mb-2">当日观察</h2>
        <div className="space-y-2">
          {(observations || []).length === 0 && <p className="text-xs text-gray-500">暂无 observation</p>}
          {(observations || []).map((obs) => (
            <button
              key={obs.observation_id}
              onClick={() => setSelectedObservation(obs)}
              className={`w-full text-left text-xs bg-white border rounded px-3 py-2 hover:border-blue-400 hover:bg-blue-50 ${
                selectedObservation?.observation_id === obs.observation_id ? 'border-blue-400 bg-blue-50' : ''
              }`}
            >
              <p className="font-medium text-gray-800">{obs.title || obs.source_type}</p>
              <p className="text-gray-500 mt-1">{obs.source_type}</p>
            </button>
          ))}
          {selectedObservation && (
            <ObservationEditor
              key={selectedObservation.observation_id}
              observation={selectedObservation}
              onUpdated={(nextObservation) => {
                setSelectedObservation(nextObservation)
                onObservationUpdated(nextObservation)
              }}
            />
          )}
        </div>
      </div>

      <div>
        <h2 className="text-sm font-semibold text-gray-800 mb-2">最近草稿</h2>
        <div className="space-y-2">
          {(drafts || []).length === 0 && <p className="text-xs text-gray-500">暂无 draft</p>}
          {(drafts || []).map((item) => (
            <button
              key={item.draft_id}
              onClick={() => onSelectDraft(item)}
              className="w-full text-left text-xs bg-white border rounded px-3 py-2 hover:border-blue-400 hover:bg-blue-50"
            >
              <p className="font-medium text-gray-800">{item.title || item.draft_id}</p>
              <p className="text-gray-500 mt-1">{item.status}</p>
            </button>
          ))}
        </div>
      </div>

      <div>
        <h2 className="text-sm font-semibold text-gray-800 mb-2">最近计划</h2>
        <div className="space-y-2">
          {(plans || []).length === 0 && <p className="text-xs text-gray-500">暂无 plan</p>}
          {(plans || []).map((item) => (
            <button
              key={item.plan_id}
              onClick={() => onSelectPlan(item)}
              className="w-full text-left text-xs bg-white border rounded px-3 py-2 hover:border-green-400 hover:bg-green-50"
            >
              <p className="font-medium text-gray-800">{item.title || item.plan_id}</p>
              <p className="text-gray-500 mt-1">{item.status}</p>
            </button>
          ))}
        </div>
      </div>
    </aside>
  )
}

export default function PlanWorkbench() {
  const { date } = useParams<{ date: string }>()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const today = new Date().toISOString().slice(0, 10)
  const activeDate = date || today

  const [step, setStep] = useState<Step>('draft')
  const [draft, setDraft] = useState<PlanDraftRecord | null>(null)
  const [plan, setPlan] = useState<PlanRecord | null>(null)

  function handleDraftCreated(d: PlanDraftRecord) {
    setDraft(d)
    queryClient.invalidateQueries({ queryKey: ['plan-draft', activeDate] })
    queryClient.invalidateQueries({ queryKey: ['plan-observations', activeDate] })
    queryClient.invalidateQueries({ queryKey: ['plan-drafts', activeDate] })
  }

  function handleConfirmed(p: PlanRecord) {
    setPlan(p)
    setStep('diagnose')
    queryClient.invalidateQueries({ queryKey: ['plans', activeDate] })
  }

  function handleReviewed() {
    // 计划已复盘，留在 review 步骤展示完成提示
  }

  function goToDate(d: string) {
    navigate(`/plans/${d}`)
    setDraft(null)
    setPlan(null)
    setStep('draft')
  }

  return (
    <div>
      <div className="flex items-center gap-4 mb-6">
        <h1 className="text-2xl font-bold text-gray-900">计划工作台</h1>
        <div className="flex items-center gap-2">
          <label className="text-sm text-gray-600">交易日：</label>
          <input
            type="date"
            value={activeDate}
            onChange={e => goToDate(e.target.value)}
            className="border border-gray-300 rounded-md px-2 py-1 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
        </div>
      </div>

      <StepIndicator current={step} planId={plan?.plan_id} />

      <div className="grid grid-cols-1 xl:grid-cols-[minmax(0,1fr)_320px] gap-6">
        <div className="bg-white rounded-lg border border-gray-200 p-6">
          {step === 'draft' && !draft && (
            <DraftStep date={activeDate} onDraftCreated={handleDraftCreated} />
          )}
          {step === 'draft' && draft && (
            <div className="space-y-4">
              <DraftView
                key={draft.draft_id}
                draft={draft}
                onUpdated={setDraft}
                onConfirm={(p) => {
                  handleConfirmed(p)
                }}
              />
              <button
                onClick={() => setStep('confirm')}
                className="text-sm text-blue-600 hover:underline"
              >
                重新查看草稿 →
              </button>
            </div>
          )}
          {step === 'confirm' && draft && (
            <DraftView
              key={draft.draft_id}
              draft={draft}
              onUpdated={setDraft}
              onConfirm={(p) => {
                handleConfirmed(p)
              }}
            />
          )}
          {step === 'confirm' && !draft && (
            <p className="text-sm text-gray-500">
              请先在「草稿」步骤生成草稿。
            </p>
          )}
          {step === 'diagnose' && plan && (
            <div className="space-y-4">
              <PlanEditor key={plan.plan_id} plan={plan} onUpdated={setPlan} />
              <DiagnoseView plan={plan} />
              <button
                onClick={() => setStep('review')}
                className="px-4 py-2 bg-indigo-600 text-white rounded-md text-sm font-medium hover:bg-indigo-700"
              >
                进入复盘 →
              </button>
            </div>
          )}
          {step === 'diagnose' && !plan && (
            <p className="text-sm text-gray-500">请先确认计划后再查看诊断。</p>
          )}
          {step === 'review' && plan && (
            <div className="space-y-4">
              <PlanEditor key={plan.plan_id} plan={plan} onUpdated={setPlan} />
              <ReviewStep plan={plan} onReviewed={handleReviewed} />
            </div>
          )}
          {step === 'review' && !plan && (
            <p className="text-sm text-gray-500">请先确认计划后再提交复盘。</p>
          )}
        </div>

        <RecentObjectsPanel
          date={activeDate}
          onObservationUpdated={() => {
            queryClient.invalidateQueries({ queryKey: ['plan-observations', activeDate] })
          }}
          onSelectDraft={(selected) => {
            setDraft(selected)
            setPlan(null)
            setStep('confirm')
          }}
          onSelectPlan={(selected) => {
            setPlan(selected)
            setDraft(null)
            setStep(selected.status === 'reviewed' ? 'review' : 'diagnose')
          }}
        />
      </div>

      <div className="mt-4 flex gap-2">
        {STEPS.map((s, i) => {
          const canGo =
            s.key === 'draft' ||
            (s.key === 'confirm' && !!draft) ||
            (s.key === 'diagnose' && !!plan) ||
            (s.key === 'review' && !!plan)
          return (
            <button
              key={s.key}
              onClick={() => canGo && setStep(s.key)}
              disabled={!canGo}
              className={`text-xs px-3 py-1 rounded border transition-colors ${
                step === s.key
                  ? 'border-blue-500 text-blue-600 bg-blue-50'
                  : canGo
                  ? 'border-gray-300 text-gray-600 hover:border-gray-400'
                  : 'border-gray-200 text-gray-300 cursor-not-allowed'
              }`}
            >
              {i + 1}. {s.label}
            </button>
          )
        })}
      </div>
    </div>
  )
}
