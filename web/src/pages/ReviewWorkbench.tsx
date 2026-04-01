import { useState, useEffect, useCallback } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../lib/api'
import StepMarket from '../components/review/StepMarket'
import StepSectors from '../components/review/StepSectors'
import StepEmotion from '../components/review/StepEmotion'
import StepStyle from '../components/review/StepStyle'
import StepLeaders from '../components/review/StepLeaders'
import StepNodes from '../components/review/StepNodes'
import StepPositions from '../components/review/StepPositions'
import StepPlan from '../components/review/StepPlan'
import type { StepProps } from '../components/review/widgets'

type StepComponent = (props: StepProps) => React.ReactNode

const STEPS: { key: string; label: string; Component: StepComponent }[] = [
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

function tryParseJSON(v: any): any {
  if (typeof v !== 'string') return v || {}
  try {
    const parsed = JSON.parse(v)
    return typeof parsed === 'object' && parsed !== null ? parsed : { notes: v }
  } catch {
    return { notes: v }
  }
}

export default function ReviewWorkbench() {
  const { date } = useParams<{ date: string }>()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [activeStep, setActiveStep] = useState(0)
  const [formData, setFormData] = useState<Record<string, any>>({})

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

  useEffect(() => {
    if (!date) return
    const draft = localStorage.getItem(DRAFT_KEY(date))
    if (draft) {
      try { setFormData(JSON.parse(draft)) } catch { /* ignore corrupt draft */ }
    } else if (existing?.exists) {
      const data: Record<string, any> = {}
      STEPS.forEach(s => {
        if (existing[s.key]) data[s.key] = tryParseJSON(existing[s.key])
      })
      setFormData(data)
    }
  }, [date, existing])

  useEffect(() => {
    if (!date || Object.keys(formData).length === 0) return
    const timer = setTimeout(() => {
      localStorage.setItem(DRAFT_KEY(date), JSON.stringify(formData))
    }, 2000)
    return () => clearTimeout(timer)
  }, [formData, date])

  const saveMutation = useMutation({
    mutationFn: () => api.saveReview(date!, formData),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['review', date] })
      localStorage.removeItem(DRAFT_KEY(date!))
    },
  })

  const step = STEPS[activeStep]
  const stepData = formData[step.key] || {}

  const handleChange = useCallback((value: any) => {
    setFormData(prev => ({ ...prev, [step.key]: value }))
  }, [step.key])

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

      <div className="flex gap-1 border-b overflow-x-auto">
        {STEPS.map((s, i) => {
          const filled = formData[s.key] && typeof formData[s.key] === 'object' && Object.keys(formData[s.key]).length > 0
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
        <step.Component data={stepData} onChange={handleChange} prefill={prefill} />
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

      {prefill?.teacher_notes?.length > 0 && (
        <div className="bg-white rounded-lg shadow p-4">
          <h3 className="text-sm font-medium text-gray-500 mb-2">当日老师观点参考</h3>
          {prefill.teacher_notes.map((n: any) => (
            <div key={n.id} className="border-l-2 border-blue-200 pl-3 py-1 mb-2 text-sm">
              <div className="font-medium text-gray-800">{n.teacher_name} - {n.title}</div>
              {n.core_view && <div className="text-gray-600 mt-1">{n.core_view}</div>}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
