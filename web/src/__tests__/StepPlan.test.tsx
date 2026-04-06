import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import StepPlan from '../components/review/StepPlan'
import type { ReviewPrefillData, ReviewStepValue } from '../lib/types'

function renderStep(data: ReviewStepValue = {}, prefill?: ReviewPrefillData) {
  const onChange = vi.fn()
  render(<StepPlan data={data} onChange={onChange} prefill={prefill} />)
  return { onChange }
}

const prefill: ReviewPrefillData = {
  date: '2026-04-03',
  market: null,
  prev_market: null,
  avg_5d_amount: null,
  avg_20d_amount: null,
  teacher_notes: [
    {
      id: 1,
      teacher_id: 1,
      teacher_name: '小鲍',
      date: '2026-04-03',
      title: '计划提醒',
      core_view: '先看主线分歧后的承接',
      tags: null,
      sectors: null,
      position_advice: '仓位先控制在3成内',
      avoid: '避免一致性高开的跟风票',
      created_at: '2026-04-03T07:00:00',
    },
  ],
  holdings: [],
  calendar_events: [
    {
      id: 1,
      date: '2026-04-03',
      event: '美国非农就业数据',
      impact: 'high',
      category: 'macro',
    },
  ],
  main_themes: [],
}

describe('StepPlan', () => {
  it('renders teacher-note and calendar fallbacks', () => {
    renderStep({}, prefill)

    expect(screen.getByText('老师观点参考')).toBeInTheDocument()
    expect(screen.getByDisplayValue('先看主线分歧后的承接')).toBeInTheDocument()
    expect(screen.getByDisplayValue('【小鲍】避免一致性高开的跟风票')).toBeInTheDocument()
    expect(screen.getByText('当日投资日历事件')).toBeInTheDocument()
    expect(screen.getByText('美国非农就业数据')).toBeInTheDocument()
    expect(screen.getByText('high')).toBeInTheDocument()
  })

  it('emits nested payload when user edits conclusion summary', () => {
    const { onChange } = renderStep({}, prefill)

    fireEvent.change(screen.getByLabelText('一句话总结'), { target: { value: '主线强但分歧临近' } })

    expect(onChange).toHaveBeenCalledWith({
      summary: { one_sentence: '主线强但分歧临近' },
    })
  })
})
