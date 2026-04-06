import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import StepSectors from '../components/review/StepSectors'
import type { ReviewPrefillData, ReviewStepValue } from '../lib/types'

function renderStep(data: ReviewStepValue = {}, prefill?: ReviewPrefillData) {
  const onChange = vi.fn()
  render(
    <MemoryRouter>
      <StepSectors data={data} onChange={onChange} prefill={prefill} />
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
    sector_industry: {
      data: [
        { name: 'AI算力', change_pct: 5.2 },
        { name: '电力', change_pct: 3.1 },
      ],
      bottom: [{ name: '医药', change_pct: -1.8 }],
    },
    sector_rhythm_industry: [
      { name: 'AI算力', phase: '主升', change_today: 5.2, rank_today: 1, confidence: '高' },
    ],
  },
  prev_market: null,
  avg_5d_amount: null,
  avg_20d_amount: null,
  teacher_notes: [
    {
      id: 1,
      teacher_id: 1,
      teacher_name: '小鲍',
      date: '2026-04-03',
      title: '板块备注',
      core_view: null,
      tags: null,
      sectors: '继续看AI算力',
      key_points: '主线没有切换',
      created_at: '2026-04-03T07:00:00',
    },
  ],
  holdings: [],
  calendar_events: [],
  main_themes: [
    {
      date: '2026-04-03',
      theme_name: 'AI算力',
      phase: '主升',
      duration_days: 5,
      key_stocks: ['高标A', '中军B'],
      status: 'active',
    },
  ],
  industry_info: [
    {
      id: 1,
      sector_name: 'AI算力',
      date: '2026-04-03',
      content: '服务器链订单继续强化',
      info_type: 'analysis',
      confidence: '高',
      timeliness: '近期',
      source: '盘后整理',
    },
  ],
}

describe('StepSectors', () => {
  it('renders theme, sector rhythm and industry info from prefill', () => {
    renderStep({}, prefill)

    expect(screen.getByDisplayValue('AI算力')).toBeInTheDocument()
    expect(screen.getByDisplayValue('持续')).toBeInTheDocument()
    expect(screen.getByText('行业板块排行（申万）')).toBeInTheDocument()
    expect(screen.getByText('行业节奏信号（当日前列）')).toBeInTheDocument()
    expect(screen.getByText('近期行业信息（1 条）')).toBeInTheDocument()
    expect(screen.getByText('服务器链订单继续强化')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /完整市场数据/i })).toHaveAttribute('href', '/market/2026-04-03')
  })

  it('emits nested payload when user edits main theme name', () => {
    const { onChange } = renderStep({}, prefill)

    fireEvent.change(screen.getByLabelText('主线名称'), { target: { value: '电力' } })

    expect(onChange).toHaveBeenCalledWith({
      main_theme: { name: '电力' },
    })
  })
})
