import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import StepPositions from '../components/review/StepPositions'
import type { ReviewPrefillData, ReviewStepValue } from '../lib/types'

function renderStep(data: ReviewStepValue = {}, prefill?: ReviewPrefillData) {
  const onChange = vi.fn()
  render(<StepPositions data={data} onChange={onChange} prefill={prefill} />)
  return { onChange }
}

const holdingsPrefill: ReviewPrefillData = {
  date: '2026-04-03',
  market: null,
  prev_market: null,
  avg_5d_amount: null,
  avg_20d_amount: null,
  teacher_notes: [],
  holdings: [
    {
      id: 1,
      stock_code: '300750.SZ',
      stock_name: '宁德时代',
      entry_price: 180,
      current_price: 192,
      prefill_pnl_pct: 6.67,
      shares: 100,
      status: 'holding',
    },
  ],
  calendar_events: [],
  main_themes: [],
  holding_signals: {
    date: '2026-04-03',
    items: [
      {
        stock_code: '300750.SZ',
        stock_name: '宁德时代',
        sector: '电池',
        price_snapshot: {
          entry_price: 180,
          current_price: 192,
          pnl_pct: 6.67,
          up_limit: 211,
          down_limit: 173,
          pre_close: 192,
        },
        technical_signals: {
          ma5: 188,
          ma10: 185,
          ma20: 180,
          above_ma5: true,
          above_ma10: true,
          above_ma20: true,
          volume_vs_ma5: '以上',
          turnover_rate: 6.2,
          turnover_status: '活跃',
          sector_change_pct: 3.2,
        },
        theme_signals: {
          is_main_theme: true,
          main_theme_name: '电池',
          is_strongest_sector: true,
          strongest_sector_name: '电池',
          sector_flow_confirmed: true,
          sector_flow_source: 'ths',
        },
        event_signals: {
          has_recent_announcement: true,
          recent_announcements: [{ ann_date: '20260402', title: '回购公告' }],
          has_disclosure_plan: true,
          disclosure_dates: [{ ann_date: '20260420', report_end: '20260331' }],
          is_st: false,
          share_float_upcoming: [],
        },
        latest_task: {
          trade_date: '2026-04-02',
          stock_code: '300750.SZ',
          stock_name: '宁德时代',
          action_plan: '若冲高回落则减仓',
          source: 'review_step7',
          status: 'open',
        },
        risk_flags: [{ level: 'high', label: '财报临近', reason: '20260420 有披露计划' }],
      },
    ],
  },
}

describe('StepPositions', () => {
  it('renders holdings prefill and pnl hint', () => {
    renderStep({}, holdingsPrefill)

    expect(screen.getByText(/已从持仓池自动导入 1 只股票/)).toBeInTheDocument()
    expect(screen.getByDisplayValue('宁德时代(300750.SZ)')).toBeInTheDocument()
    expect(screen.getByDisplayValue('180')).toBeInTheDocument()
    expect(screen.getByDisplayValue('192')).toBeInTheDocument()
    expect(screen.getByText('浮动盈亏（参考） 6.67%')).toBeInTheDocument()
    expect(screen.getByLabelText('在热点板块')).toBeChecked()
    expect(screen.getByText('主线归属：')).toBeInTheDocument()
    expect(screen.getByText('主线：电池')).toBeInTheDocument()
    expect(screen.getByText('技术位置：')).toBeInTheDocument()
    expect(screen.getByText(/站上MA5 \/ 站上MA10 \/ 量能以上均量 \/ 换手率 6.20%（活跃）/)).toBeInTheDocument()
    expect(screen.getByText(/披露计划 20260420/)).toBeInTheDocument()
    expect(screen.getByText('昨日计划：')).toBeInTheDocument()
    expect(screen.getByText('若冲高回落则减仓')).toBeInTheDocument()
  })

  it('fills latest holding values into existing draft rows', async () => {
    const { onChange } = renderStep(
      {
        positions: [
          {
            stock: '宁德时代(300750.SZ)',
            cost: null,
            current_price: null,
            prefill_pnl_pct: null,
            position_pct: null,
            in_hot_sector: false,
            price_trend: '',
            volume_vs_avg: '',
            amplitude_ok: false,
            action_plan: '',
          },
        ],
      },
      holdingsPrefill
    )

    await waitFor(() => {
      expect(onChange).toHaveBeenCalledWith({
        positions: [
          {
            stock: '宁德时代(300750.SZ)',
            cost: 180,
            current_price: 192,
            prefill_pnl_pct: 6.67,
            position_pct: null,
            in_hot_sector: true,
            price_trend: '',
            volume_vs_avg: '以上',
            amplitude_ok: false,
            action_plan: '',
          },
        ],
      })
    })
  })

  it('emits positions payload when user edits action plan', () => {
    const { onChange } = renderStep({}, holdingsPrefill)

    fireEvent.change(screen.getByLabelText('操作计划'), { target: { value: '继续持有观察' } })

    expect(onChange).toHaveBeenCalledWith({
      positions: [
        {
          stock: '宁德时代(300750.SZ)',
          cost: 180,
          current_price: 192,
          prefill_pnl_pct: 6.67,
          position_pct: null,
          in_hot_sector: true,
          price_trend: '',
          volume_vs_avg: '以上',
          amplitude_ok: false,
          action_plan: '继续持有观察',
        },
      ],
    })
  })
})
