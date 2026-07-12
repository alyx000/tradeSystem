import { lazy, Suspense, useState, useEffect, useCallback, useMemo, useRef } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../lib/api'
import type { StepProps } from '../components/review/widgets'
import type {
  ReviewFormData,
  ReviewRecord,
  ReviewStepKey,
  ReviewStepValue,
  TrinityFactorScoreRun,
} from '../lib/types'

type StepComponent = React.ComponentType<StepProps>
type FactorScoreState = {
  result: TrinityFactorScoreRun
  inputKey: string
}

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
const FACTOR_SCORING_STEPS: ReviewStepKey[] = [
  'step1_market',
  'step2_sectors',
  'step3_emotion',
  'step4_style',
  'step5_leaders',
  'step6_nodes',
]

function pickFactorScoringSteps(formData: ReviewFormData): ReviewFormData {
  const steps: ReviewFormData = {}
  FACTOR_SCORING_STEPS.forEach(key => {
    if (formData[key] !== undefined) steps[key] = formData[key]
  })
  return steps
}

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
    if (
      key === 'secondary_factors'
      && draft.factor_decision === null
      && Array.isArray(value)
    ) {
      merged[key] = value
      return
    }
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

function clearFactorDecision(step8: ReviewStepValue | undefined): ReviewStepValue {
  const next: ReviewStepValue = isPlainObject(step8) ? { ...step8 } : {}
  const decision = isPlainObject(next.factor_decision) ? next.factor_decision : null
  next.factor_decision = null
  if (decision) {
    if (next.key_factor === decision.primary_factor) next.key_factor = ''
    const mirroredSupporting = Array.isArray(decision.supporting_factors)
      ? decision.supporting_factors
      : []
    if (
      Array.isArray(next.secondary_factors)
      && JSON.stringify(next.secondary_factors) === JSON.stringify(mirroredSupporting)
    ) {
      next.secondary_factors = []
    }
  }
  return next
}

function factorInputKey(formData: ReviewFormData, prefill: unknown): string {
  return JSON.stringify({
    steps: pickFactorScoringSteps(formData),
    prefill: prefill ?? null,
  })
}

function reviewMutationErrorMessage(error: unknown, action: '保存' | '生成草稿'): string {
  const message = error instanceof Error ? error.message : '未知错误'
  if (message.includes('score input has changed')) {
    return '保存失败：评分证据已变化，请重新运行 LLM 评分后再确认。'
  }
  return `${action}失败：${message}`
}

function hydrateReviewFormData(
  date: string | undefined,
  existing?: ReviewRecord,
  _draftRevision = 0
): ReviewFormData {
  void _draftRevision
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
  const [draftRevisionByDate, setDraftRevisionByDate] = useState<Record<string, number>>({})
  const [dismissedDraftWarningByDate, setDismissedDraftWarningByDate] = useState<Record<string, boolean>>({})
  const [factorScoreByDate, setFactorScoreByDate] = useState<Record<string, FactorScoreState>>({})
  const [factorInputsEditedByDate, setFactorInputsEditedByDate] = useState<Record<string, boolean>>({})
  const latestFormDataByDateRef = useRef<Record<string, ReviewFormData>>({})

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

  const draftRevision = date ? draftRevisionByDate[date] : undefined
  const hydratedFormData = useMemo(
    () => hydrateReviewFormData(date, existing, draftRevision),
    [date, existing, draftRevision]
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
    const merged = current
      ? mergeFormData(hydratedFormData, current)
      : hydratedFormData
    const storedScore = factorScoreByDate[date]
    const scoreInputChanged = Boolean(
      storedScore && storedScore.inputKey !== factorInputKey(merged, prefill),
    )
    const mergedDecision = isPlainObject(merged.step8_plan)
      && isPlainObject(merged.step8_plan.factor_decision)
      ? merged.step8_plan.factor_decision
      : null
    const decisionRunChanged = Boolean(
      storedScore
      && mergedDecision
      && mergedDecision.score_run_id !== storedScore.result.score_run_id,
    )
    const shouldInvalidateDecision = (
      factorInputsEditedByDate[date] || scoreInputChanged || decisionRunChanged
    )
    const step8 = merged.step8_plan
    if (
      shouldInvalidateDecision
      && isPlainObject(step8)
      && isPlainObject(step8.factor_decision)
    ) {
      return {
        ...merged,
        step8_plan: clearFactorDecision(step8),
      }
    }
    return merged
  }, [
    date,
    factorInputsEditedByDate,
    factorScoreByDate,
    formDataByDate,
    hydratedFormData,
    prefill,
  ])

  useEffect(() => {
    if (!date) return
    latestFormDataByDateRef.current[date] = formData
  }, [date, formData])

  useEffect(() => {
    if (!date) return
    const edited = formDataByDate[date]
    if (!edited || Object.keys(edited).length === 0) return
    const timer = setTimeout(() => {
      localStorage.setItem(DRAFT_KEY(date), JSON.stringify(formData))
    }, 2000)
    return () => clearTimeout(timer)
  }, [date, formData, formDataByDate])

  useEffect(() => {
    if (prefill && prefill.is_trading_day === false && prefill.prev_trade_date) {
      navigate(`/review/${prefill.prev_trade_date}`, { replace: true })
    }
  }, [prefill, navigate])

  const finishSubmittedSave = useCallback((
    saveDate: string,
    submittedData: ReviewFormData,
  ) => {
    const latest = latestFormDataByDateRef.current[saveDate] ?? {}
    if (JSON.stringify(latest) !== JSON.stringify(submittedData)) {
      localStorage.setItem(DRAFT_KEY(saveDate), JSON.stringify(latest))
      return
    }
    localStorage.removeItem(DRAFT_KEY(saveDate))
    setFormDataByDate(prev => {
      const next = { ...prev }
      delete next[saveDate]
      return next
    })
  }, [])

  const saveMutation = useMutation({
    mutationFn: ({ saveDate, data }: { saveDate: string; data: ReviewFormData }) => (
      api.saveReview(saveDate, data)
    ),
    onSuccess: (_result, variables) => {
      queryClient.invalidateQueries({ queryKey: ['review', variables.saveDate] })
      finishSubmittedSave(variables.saveDate, variables.data)
    },
  })

  const draftMutation = useMutation({
    mutationFn: async ({ saveDate, data }: { saveDate: string; data: ReviewFormData }) => {
      await api.saveReview(saveDate, data)
      return {
        saveDate,
        result: await api.reviewToDraft(saveDate, { input_by: 'web' }),
      }
    },
    onSuccess: ({ saveDate, result }, variables) => {
      queryClient.invalidateQueries({ queryKey: ['review', saveDate] })
      queryClient.invalidateQueries({ queryKey: ['prefill', saveDate] })
      finishSubmittedSave(saveDate, variables.data)
      navigate(`/plans/${result.trade_date}`)
    },
  })

  const factorScoreMutation = useMutation({
    mutationFn: ({ scoreDate, steps }: {
      scoreDate: string
      steps: ReviewFormData
      inputKey: string
    }) => (
      api.scoreReviewFactors(scoreDate, { steps, input_by: 'web' })
    ),
    onSuccess: (result, variables) => {
      setFactorScoreByDate(prev => ({
        ...prev,
        [variables.scoreDate]: {
          result,
          inputKey: variables.inputKey,
        },
      }))
    },
  })

  const step = STEPS[activeStep]
  const stepData = formData[step.key] || {}
  const hasLocalDraft = !!date && localStorage.getItem(DRAFT_KEY(date)) !== null
  const showDraftWarning = hasLocalDraft && !dismissedDraftWarningByDate[date!]

  const useServerVersion = useCallback(() => {
    if (!date) return
    localStorage.removeItem(DRAFT_KEY(date))
    setFormDataByDate(prev => {
      const next = { ...prev }
      delete next[date]
      return next
    })
    setDismissedDraftWarningByDate(prev => {
      const next = { ...prev }
      delete next[date]
      return next
    })
    setDraftRevisionByDate(prev => ({ ...prev, [date]: (prev[date] ?? 0) + 1 }))
    setFactorInputsEditedByDate(prev => {
      const next = { ...prev }
      delete next[date]
      return next
    })
  }, [date])

  const keepLocalDraft = useCallback(() => {
    if (!date) return
    setDismissedDraftWarningByDate(prev => ({ ...prev, [date]: true }))
  }, [date])

  const handleChange = useCallback((value: ReviewStepValue) => {
    if (!date) return
    if (FACTOR_SCORING_STEPS.includes(step.key)) {
      setFactorInputsEditedByDate(edited => ({ ...edited, [date]: true }))
    } else if (
      step.key === 'step8_plan'
      && isPlainObject(value.factor_decision)
    ) {
      setFactorInputsEditedByDate(edited => ({ ...edited, [date]: false }))
    }
    const nextForDate: ReviewFormData = {
      ...formData,
      [step.key]: value,
    }
    latestFormDataByDateRef.current[date] = nextForDate
    setFormDataByDate(prev => ({ ...prev, [date]: nextForDate }))
  }, [date, formData, step.key])

  const handleFactorScore = useCallback(() => {
    if (!date || !prefill) return
    const steps = pickFactorScoringSteps(formData)
    factorScoreMutation.mutate({
      scoreDate: date,
      steps,
      inputKey: factorInputKey(formData, prefill),
    })
  }, [date, factorScoreMutation, formData, prefill])

  const currentFactorInputKey = factorInputKey(formData, prefill)
  const storedFactorScore = date ? factorScoreByDate[date] : undefined
  const factorScoreIsStale = Boolean(
    storedFactorScore && storedFactorScore.inputKey !== currentFactorInputKey,
  )
  const factorScore = factorScoreIsStale ? undefined : storedFactorScore?.result
  const factorScorePending = (
    factorScoreMutation.isPending
    && factorScoreMutation.variables?.scoreDate === date
  )
  const factorScoreRequestError = (
    factorScoreMutation.isError
    && factorScoreMutation.variables?.scoreDate === date
  )
    ? factorScoreMutation.error instanceof Error
      ? factorScoreMutation.error.message
      : '未知错误'
    : null
  const factorScoreError = factorScoreRequestError
    || (factorScoreIsStale ? '评分输入已变化，请重新运行 LLM 评分。' : null)

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
          <button onClick={() => date && saveMutation.mutate({ saveDate: date, data: formData })} disabled={saveMutation.isPending}
            className="bg-blue-600 text-white px-4 py-2 rounded text-sm hover:bg-blue-700 disabled:opacity-50">
            {saveMutation.isPending ? '保存中...' : '保存'}
          </button>
          <button onClick={() => date && draftMutation.mutate({ saveDate: date, data: formData })} disabled={draftMutation.isPending}
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
      {saveMutation.isError && (
        <div className="bg-red-50 text-red-700 px-4 py-2 rounded text-sm">
          {reviewMutationErrorMessage(saveMutation.error, '保存')}
        </div>
      )}
      {draftMutation.isError && (
        <div className="bg-red-50 text-red-700 px-4 py-2 rounded text-sm">
          {reviewMutationErrorMessage(draftMutation.error, '生成草稿')}
        </div>
      )}

      {showDraftWarning && (
        <div className="rounded border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800 flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
          <div>
            <div className="font-medium">当前存在本地草稿，可能覆盖服务端版本</div>
            <div className="text-xs text-amber-700 mt-0.5">页面已合并本地草稿；如果刚由 Agent 或 API 写入，请切回服务端版本。</div>
          </div>
          <div className="flex gap-2 shrink-0">
            <button
              type="button"
              onClick={useServerVersion}
              className="rounded bg-amber-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-amber-700"
            >
              使用服务端版本
            </button>
            <button
              type="button"
              onClick={keepLocalDraft}
              className="rounded border border-amber-300 bg-white px-3 py-1.5 text-xs font-medium text-amber-700 hover:bg-amber-100"
            >
              继续使用本地草稿
            </button>
          </div>
        </div>
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
          <step.Component
            key={`${date}:${step.key}:${step.key === 'step8_plan' ? factorScore?.score_run_id || 'no-score' : ''}`}
            data={stepData}
            onChange={handleChange}
            prefill={prefill}
            factorScore={factorScore}
            factorScorePending={factorScorePending}
            factorScoreError={factorScoreError}
            onFactorScore={prefill ? handleFactorScore : undefined}
          />
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
