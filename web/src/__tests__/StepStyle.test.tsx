import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import StepStyle from '../components/review/StepStyle'
import type { ReviewPrefillData, ReviewStepValue } from '../lib/types'

function renderStep(data: ReviewStepValue = {}, prefill?: ReviewPrefillData) {
  const onChange = vi.fn()
  render(<StepStyle data={data} onChange={onChange} prefill={prefill} />)
  return { onChange }
}

const prefill: ReviewPrefillData = {
  date: '2026-04-03',
  market: {
    available: true,
    date: '2026-04-03',
    sh_index_close: 3210,
    sh_index_change_pct: 1.1,
    sz_index_close: 10020,
    sz_index_change_pct: 1.9,
    total_amount: 12000,
    advance_count: 3100,
    decline_count: 1600,
    sh_above_ma5w: true,
    sz_above_ma5w: true,
    chinext_above_ma5w: false,
    star50_above_ma5w: false,
    avg_price_above_ma5w: true,
    limit_up_count: 88,
    limit_down_count: 6,
    seal_rate: 81,
    broken_rate: 19,
    highest_board: 5,
    continuous_board_counts: null,
    premium_10cm: 2.1,
    premium_20cm: 3.2,
    premium_30cm: 1.1,
    premium_second_board: 4.4,
    northbound_net: 56.2,
    margin_balance: null,
    style_factors: {
      cap_preference: {
        relative: '小盘占优',
        csi300_chg: -0.4,
        csi1000_chg: 1.2,
        spread: 1.6,
      },
      board_preference: {
        dominant_type: '连板票主导',
        pct_10cm: 62,
        pct_20cm: 28,
        pct_30cm: 10,
      },
      premium_snapshot: {
        capacity_top10: { premium_median: 5.3 },
      },
      premium_trend: {
        direction: '回暖',
        first_board_median_5d: [1.1, 1.8, 2.4],
      },
      switch_signals: ['20cm开始扩散'],
    },
  },
  prev_market: null,
  avg_5d_amount: null,
  avg_20d_amount: null,
  teacher_notes: [],
  holdings: [],
  calendar_events: [],
  main_themes: [],
  prev_review: {
    date: '2026-04-02',
    step4_style: JSON.stringify({
      preference: { style: '情绪面', trend_or_board: '连板票主导' },
      effects: { consecutive: { effect: '正', note: '高标抱团延续' } },
    }),
  },
}

describe('StepStyle', () => {
  it('renders derived style preferences and previous review fallbacks', () => {
    renderStep({}, prefill)

    expect(screen.getByText(/溢价率与效应方向已从盘后数据自动预填/)).toBeInTheDocument()
    expect(screen.getByDisplayValue('小盘股')).toBeInTheDocument()
    expect(screen.getByDisplayValue('情绪面')).toBeInTheDocument()
    expect(screen.getByDisplayValue('连板票主导')).toBeInTheDocument()
    expect(screen.getAllByDisplayValue('正').length).toBeGreaterThan(0)
    expect(screen.getByDisplayValue('2.1')).toBeInTheDocument()
    expect(screen.getByDisplayValue('高标抱团延续')).toBeInTheDocument()
    expect(screen.getByText('⚡ 20cm开始扩散')).toBeInTheDocument()
  })

  it('emits nested payload when user edits regulatory feedback', () => {
    const { onChange } = renderStep({}, prefill)

    fireEvent.change(screen.getByLabelText('异动监管反馈'), { target: { value: '监管偏温和' } })

    expect(onChange).toHaveBeenCalledWith({
      regulatory_feedback: '监管偏温和',
    })
  })
})
