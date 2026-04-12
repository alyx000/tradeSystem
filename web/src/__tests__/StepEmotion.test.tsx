import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import StepEmotion from '../components/review/StepEmotion'
import type { ReviewPrefillData, ReviewStepValue } from '../lib/types'

vi.mock('../lib/api', () => ({
  api: { getMarketHistory: vi.fn().mockResolvedValue([]) },
}))

function renderStep(data: ReviewStepValue = {}, prefill?: ReviewPrefillData) {
  const onChange = vi.fn()
  render(<StepEmotion data={data} onChange={onChange} prefill={prefill} />)
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
  },
  prev_market: null,
  avg_5d_amount: null,
  avg_20d_amount: null,
  teacher_notes: [],
  holdings: [],
  calendar_events: [],
  main_themes: [],
  emotion_cycle: {
    phase: '发酵',
    sub_cycle: 2,
    strength_trend: '持续走强',
    confidence: '高',
  },
  review_signals: {
    market: {
      moneyflow_summary: null,
      market_structure_rows: [],
    },
    sectors: {
      strongest_rows: [],
      industry_moneyflow_rows: [],
      concept_moneyflow_rows: [],
    },
    emotion: {
      ladder_rows: [
        { name: '高标A', nums: 6 },
        { name: '中位B', nums: 4 },
      ],
    },
  },
}

describe('StepEmotion', () => {
  it('renders emotion prefill and derived defaults', () => {
    renderStep({}, prefill)

    expect(screen.getByLabelText('整体情绪')).toHaveValue('发酵')
    expect(screen.getByLabelText('子周期')).toHaveValue('2')
    expect(screen.getByText(/趋势方向:/)).toBeInTheDocument()
    expect(screen.getByText('持续走强')).toBeInTheDocument()
    expect(screen.getByText(/置信度: 高/)).toBeInTheDocument()
    expect(screen.getByText('88')).toBeInTheDocument()
    expect(screen.getByText('81 %')).toBeInTheDocument()
    expect(screen.getByText('连板天梯')).toBeInTheDocument()
    expect(screen.getByText('高标A')).toBeInTheDocument()
    expect(screen.getByText('6板')).toBeInTheDocument()
  })

  it('emits nested payload when user edits transition reason', () => {
    const { onChange } = renderStep({}, prefill)

    fireEvent.change(screen.getByLabelText('判断依据'), { target: { value: '高标与封板率同步修复' } })

    expect(onChange).toHaveBeenCalledWith({
      transition: { reason: '高标与封板率同步修复' },
    })
  })
})
