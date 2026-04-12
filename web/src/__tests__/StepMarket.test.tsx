import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import StepMarket from '../components/review/StepMarket'
import type { ReviewPrefillData, ReviewStepValue } from '../lib/types'

function renderStep(data: ReviewStepValue = {}, prefill?: ReviewPrefillData) {
  const onChange = vi.fn()
  render(
    <MemoryRouter>
      <StepMarket data={data} onChange={onChange} prefill={prefill} />
    </MemoryRouter>
  )
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
  prev_market: {
    date: '2026-04-02',
    sh_index_close: 3170,
    sh_index_change_pct: -0.4,
    sz_index_close: 9890,
    sz_index_change_pct: -0.6,
    total_amount: 10000,
    advance_count: 2400,
    decline_count: 2200,
    sh_above_ma5w: true,
    sz_above_ma5w: true,
    chinext_above_ma5w: false,
    star50_above_ma5w: false,
    avg_price_above_ma5w: true,
    limit_up_count: 72,
    limit_down_count: 8,
    seal_rate: 76,
    broken_rate: 24,
    highest_board: 4,
    continuous_board_counts: null,
    premium_10cm: 1.2,
    premium_20cm: 2,
    premium_30cm: 0.7,
    premium_second_board: 3.6,
    northbound_net: 12.1,
    margin_balance: null,
  },
  avg_5d_amount: 10500,
  avg_20d_amount: 9800,
  teacher_notes: [
    {
      id: 1,
      teacher_id: 1,
      teacher_name: '小鲍',
      date: '2026-04-03',
      title: '早评',
      core_view: '量能配合较好',
      tags: null,
      sectors: null,
      created_at: '2026-04-03T07:00:00',
    },
  ],
  holdings: [],
  calendar_events: [],
  main_themes: [],
  review_signals: {
    market: {
      moneyflow_summary: {
        net_amount_yi: 6.5,
        net_amount_rate: 1.32,
        super_large_yi: 4.2,
        large_yi: 1.8,
      },
      market_structure_rows: [
        { name: '上海A股', amount: 6200, volume: 41000000, pe: 15.2, turnover_rate: 1.3, com_count: 1700 },
        { name: '深圳A股', amount: 5100, volume: 36000000, pe: 28.5, turnover_rate: 2.1, com_count: 2800 },
      ],
    },
    sectors: {
      strongest_rows: [],
      industry_moneyflow_rows: [],
      concept_moneyflow_rows: [],
    },
    emotion: {
      ladder_rows: [],
    },
  },
}

describe('StepMarket', () => {
  it('renders market prefill and derived defaults', () => {
    renderStep({}, prefill)

    expect(screen.getByText('老师观点参考')).toBeInTheDocument()
    expect(screen.getByText('量能配合较好')).toBeInTheDocument()
    expect(screen.getByLabelText('较昨日')).toHaveValue('放量')
    expect(screen.getByLabelText('较5日均量')).toHaveValue('高于')
    expect(screen.getByLabelText('较20日均量')).toHaveValue('高于')
    expect(screen.getByLabelText('5周均线')).toHaveValue('线上')
    expect(screen.getByDisplayValue('【小鲍】量能配合较好')).toBeInTheDocument()
    expect(screen.getByText('主力资金流向')).toBeInTheDocument()
    expect(screen.getByText('A股市场结构')).toBeInTheDocument()
    expect(screen.getByText('上海A股')).toBeInTheDocument()
    expect(screen.getByText('+6.50亿')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /查看完整市场数据/i })).toHaveAttribute('href', '/market/2026-04-03')
  })

  it('shows non-trading-day warning when is_trading_day is false', () => {
    renderStep({}, { ...prefill, is_trading_day: false })
    expect(screen.getByText('当前日期为非交易日，市场数据可能为空或不准确')).toBeInTheDocument()
  })

  it('hides non-trading-day warning when is_trading_day is true', () => {
    renderStep({}, { ...prefill, is_trading_day: true })
    expect(screen.queryByText('当前日期为非交易日，市场数据可能为空或不准确')).not.toBeInTheDocument()
  })

  it('hides non-trading-day warning when is_trading_day is undefined', () => {
    renderStep({}, prefill)
    expect(screen.queryByText('当前日期为非交易日，市场数据可能为空或不准确')).not.toBeInTheDocument()
  })

  it('emits nested payload when user edits a field', () => {
    const { onChange } = renderStep({}, prefill)

    fireEvent.change(screen.getByLabelText('当前节点'), { target: { value: '突破前高' } })

    expect(onChange).toHaveBeenCalledWith({
      node: { current: '突破前高' },
    })
  })
})
