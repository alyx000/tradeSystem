import { lazy, Suspense, useState, useEffect, useCallback, useMemo } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../lib/api'
import type { StepProps } from '../components/review/widgets'
import type { ReviewFormData, ReviewRecord, ReviewStepKey, ReviewStepValue } from '../lib/types'

type StepComponent = React.ComponentType<StepProps>

const StepMarket = lazy(() => import('../components/review/StepMarket'))
const StepSectors = lazy(() => import('../components/review/StepSectors'))
const StepEmotion = lazy(() => import('../components/review/StepEmotion'))
const StepStyle = lazy(() => import('../components/review/StepStyle'))
const StepLeaders = lazy(() => import('../components/review/StepLeaders'))
const StepNodes = lazy(() => import('../components/review/StepNodes'))
const StepPositions = lazy(() => import('../components/review/StepPositions'))
const StepPlan = lazy(() => import('../components/review/StepPlan'))

const STEPS: { key: ReviewStepKey; label: string; Component: StepComponent }[] = [
  { key: 'step1_market', label: '1.大盘', Component: StepMarket },
  { key: 'step2_sectors', label: '2.板块', Component: StepSectors },
  { key: 'step3_emotion', label: '3.情绪', Component: StepEmotion },
  { key: 'step4_style', label: '4.风格', Component: StepStyle },
  { key: 'step5_leaders', label: '5.龙头', Component: StepLeaders },
  { key: 'step6_nodes', label: '6.节点', Component: StepNodes },
  { key: 'step7_positions', label: '7.持仓', Component: StepPositions },
  { key: 'step8_plan', label: '8.计划', Component: StepPlan },
]

const DRAFT_KEY = (date: string) => `review_draft_${date}`

function tryParseJSON(v: unknown): ReviewStepValue {
  if (typeof v !== 'string') {
    return typeof v === 'object' && v !== null ? (v as ReviewStepValue) : {}
  }
  try {
    const parsed = JSON.parse(v)
    return typeof parsed === 'object' && parsed !== null ? (parsed as ReviewStepValue) : { notes: v }
  } catch {
    return { notes: v }
  }
}

function reviewRecordToFormData(existing?: ReviewRecord): ReviewFormData {
  if (!existing?.exists) return {}
  const data: ReviewFormData = {}
  STEPS.forEach((s) => {
    if (existing[s.key]) data[s.key] = tryParseJSON(existing[s.key])
  })
  return data
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function isArrayOfPrefilledItems(value: unknown): value is Array<Record<string, unknown>> {
  return Array.isArray(value) && value.length > 0 && value.every(
    item => isPlainObject(item) && item.is_prefilled === true
  )
}

function mergeFieldValue(base: unknown, draft: unknown): unknown {
  if (Array.isArray(base) && Array.isArray(draft)) {
    if (draft.length === 0) return base
    if (isArrayOfPrefilledItems(draft)) return base.length > 0 ? base : draft
    return draft
  }

  if (isPlainObject(base) && isPlainObject(draft)) {
    return mergeStepValue(base, draft)
  }

  return draft
}

function mergeStepValue(base: unknown, draft: unknown): ReviewStepValue {
  if (!isPlainObject(base)) return tryParseJSON(draft)
  if (!isPlainObject(draft)) return base as ReviewStepValue

  const merged: Record<string, unknown> = { ...base }
  Object.entries(draft).forEach(([key, value]) => {
    const prev = merged[key]
    merged[key] = mergeFieldValue(prev, value)
  })
  return merged as ReviewStepValue
}

function mergeFormData(base: ReviewFormData, draft: ReviewFormData): ReviewFormData {
  const merged: ReviewFormData = { ...base }
  STEPS.forEach((s) => {
    if (draft[s.key] !== undefined) {
      merged[s.key] = mergeStepValue(base[s.key], draft[s.key])
    }
  })
  return merged
}

function hydrateReviewFormData(date: string | undefined, existing?: ReviewRecord): ReviewFormData {
  if (!date) return {}
  const base = reviewRecordToFormData(existing)
  const draft = localStorage.getItem(DRAFT_KEY(date))
  if (draft) {
    try {
      const parsed = JSON.parse(draft) as ReviewFormData
      return mergeFormData(base, parsed)
    } catch {
      return base
    }
  }
  return base
}

export default function ReviewWorkbench() {
  const { date } = useParams<{ date: string }>()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [activeStep, setActiveStep] = useState(0)
  const [formDataByDate, setFormDataByDate] = useState<Record<string, ReviewFormData>>({})

  const { data: prefill } = useQuery({
    queryKey: ['prefill', date],
    queryFn: () => api.getPrefill(date!),
    enabled: !!date,
  })

  const { data: existing } = useQuery({
    queryKey: ['review', date],
    queryFn: () => api.getReview(date!),
    enabled: !!date,
  })

  const hydratedFormData = useMemo(
    () => hydrateReviewFormData(date, existing),
    [date, existing]
  )
  // `formData` 派生自两处：
  //   1. `hydratedFormData`：服务端 review + localStorage draft 合并后的基准
  //   2. `formDataByDate[date]`：用户本轮编辑的增量
  // 当 1 随着 `existing` 异步返回而更新时，这里用 `mergeFormData` 重算合并结果，
  // 无需在 `useEffect` 里 `setState` 回写（避免 `react-hooks/set-state-in-effect`
  // 的级联渲染告警，同时保留嵌套 step 值按字段合并的既有语义）。
  const formData = useMemo(() => {
    if (!date) return {}
    const current = formDataByDate[date]
    if (!current) return hydratedFormData
    return mergeFormData(hydratedFormData, current)
  }, [date, formDataByDate, hydratedFormData])

  useEffect(() => {
    if (!date || Object.keys(formData).length === 0) return
    const timer = setTimeout(() => {
      localStorage.setItem(DRAFT_KEY(date), JSON.stringify(formData))
    }, 2000)
    return () => clearTimeout(timer)
  }, [formData, date])

  useEffect(() => {
    if (prefill && prefill.is_trading_day === false && prefill.prev_trade_date) {
      navigate(`/review/${prefill.prev_trade_date}`, { replace: true })
    }
  }, [prefill, navigate])

  const saveMutation = useMutation({
    mutationFn: () => api.saveReview(date!, formData),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['review', date] })
      localStorage.removeItem(DRAFT_KEY(date!))
    },
  })

  const draftMutation = useMutation({
    mutationFn: async () => {
      await api.saveReview(date!, formData)
      return api.reviewToDraft(date!)
    },
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ['review', date] })
      queryClient.invalidateQueries({ queryKey: ['prefill', date] })
      localStorage.removeItem(DRAFT_KEY(date!))
      navigate(`/plans/${result.trade_date}`)
    },
  })

  const step = STEPS[activeStep]
  const stepData = formData[step.key] || {}

  const handleChange = useCallback((value: ReviewStepValue) => {
    if (!date) return
    setFormDataByDate((prev) => ({
      ...prev,
      [date]: {
        ...(prev[date] ?? hydratedFormData),
        [step.key]: value,
      },
    }))
  }, [date, hydratedFormData, step.key])

  const filledCount = STEPS.filter(s => {
    const v = formData[s.key]
    return v && typeof v === 'object' && Object.keys(v).length > 0
  }).length

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-4">
          <h1 className="text-xl font-bold text-gray-800">八步复盘</h1>
          <input type="date" value={date} onChange={e => navigate(`/review/${e.target.value}`)}
            className="border rounded px-2 py-1 text-sm" />
          <span className="text-xs text-gray-400">{filledCount}/8 已填写</span>
        </div>
        <div className="flex gap-2">
          <button onClick={() => saveMutation.mutate()} disabled={saveMutation.isPending}
            className="bg-blue-600 text-white px-4 py-2 rounded text-sm hover:bg-blue-700 disabled:opacity-50">
            {saveMutation.isPending ? '保存中...' : '保存'}
          </button>
          <button onClick={() => draftMutation.mutate()} disabled={draftMutation.isPending}
            className="bg-amber-500 text-white px-4 py-2 rounded text-sm hover:bg-amber-600 disabled:opacity-50">
            {draftMutation.isPending ? '生成中...' : '生成次日计划草稿'}
          </button>
          <button onClick={() => {
            const blob = new Blob([JSON.stringify(formData, null, 2)], { type: 'application/json' })
            const a = document.createElement('a')
            a.href = URL.createObjectURL(blob)
            a.download = `review-${date}.json`
            a.click()
          }}
            className="border border-gray-300 text-gray-700 px-4 py-2 rounded text-sm hover:bg-gray-50">
            导出
          </button>
        </div>
      </div>

      {saveMutation.isSuccess && (
        <div className="bg-green-50 text-green-700 px-4 py-2 rounded text-sm">保存成功</div>
      )}
      {draftMutation.isError && (
        <div className="bg-red-50 text-red-700 px-4 py-2 rounded text-sm">生成草稿失败，请先检查复盘内容是否已保存。</div>
      )}

      <div className="flex gap-1 border-b overflow-x-auto">
        {STEPS.map((s, i) => {
          const stepValue = formData[s.key]
          const filled = !!(stepValue && typeof stepValue === 'object' && Object.keys(stepValue).length > 0)
          return (
            <button key={s.key} onClick={() => setActiveStep(i)}
              className={`px-3 py-2 text-sm font-medium border-b-2 transition-colors whitespace-nowrap ${
                i === activeStep
                  ? 'border-blue-600 text-blue-600'
                  : filled
                    ? 'border-transparent text-gray-700'
                    : 'border-transparent text-gray-400 hover:text-gray-600'
              }`}>
              {s.label}{filled && ' \u2713'}
            </button>
          )
        })}
      </div>

      <div className="bg-white rounded-lg shadow p-5">
        <Suspense fallback={<StepLoadingFallback label={step.label} />}>
          <step.Component data={stepData} onChange={handleChange} prefill={prefill} />
        </Suspense>
      </div>

      <div className="flex justify-between">
        <button onClick={() => setActiveStep(i => Math.max(0, i - 1))} disabled={activeStep === 0}
          className="text-sm text-gray-500 hover:text-gray-700 disabled:opacity-30">
          &larr; 上一步
        </button>
        <button onClick={() => setActiveStep(i => Math.min(STEPS.length - 1, i + 1))} disabled={activeStep === STEPS.length - 1}
          className="text-sm text-blue-600 hover:text-blue-800 disabled:opacity-30">
          下一步 &rarr;
        </button>
      </div>

    </div>
  )
}

function StepLoadingFallback({ label }: { label: string }) {
  return (
    <div className="rounded border border-dashed border-gray-200 bg-gray-50 px-4 py-10 text-center text-sm text-gray-400">
      {label} 加载中...
    </div>
  )
}
