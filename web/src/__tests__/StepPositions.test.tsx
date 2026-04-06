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
}

describe('StepPositions', () => {
  it('renders holdings prefill and pnl hint', () => {
    renderStep({}, holdingsPrefill)

    expect(screen.getByText(/已从持仓池自动导入 1 只股票/)).toBeInTheDocument()
    expect(screen.getByDisplayValue('宁德时代(300750.SZ)')).toBeInTheDocument()
    expect(screen.getByDisplayValue('180')).toBeInTheDocument()
    expect(screen.getByDisplayValue('192')).toBeInTheDocument()
    expect(screen.getByText('浮动盈亏（参考） 6.67%')).toBeInTheDocument()
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
            in_hot_sector: false,
            price_trend: '',
            volume_vs_avg: '',
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
          in_hot_sector: false,
          price_trend: '',
          volume_vs_avg: '',
          amplitude_ok: false,
          action_plan: '继续持有观察',
        },
      ],
    })
  })
})
