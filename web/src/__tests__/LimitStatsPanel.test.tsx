import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import LimitStatsPanel from '../components/market/LimitStatsPanel'
import type { MarketFullData } from '../lib/types'

const market: MarketFullData = {
  available: true,
  date: '2026-04-03',
  sh_index_close: 3200,
  sh_index_change_pct: 1.2,
  sz_index_close: 10000,
  sz_index_change_pct: 2.1,
  total_amount: 11800,
  advance_count: 3500,
  decline_count: 1500,
  sh_above_ma5w: true,
  sz_above_ma5w: true,
  chinext_above_ma5w: false,
  star50_above_ma5w: true,
  avg_price_above_ma5w: true,
  limit_up_count: 96,
  limit_down_count: 4,
  seal_rate: 82.5,
  broken_rate: 17.5,
  highest_board: 6,
  continuous_board_counts: null,
  premium_10cm: 2.1,
  premium_20cm: 3.4,
  premium_30cm: 1.2,
  premium_second_board: 4.5,
  northbound_net: 86.5,
  margin_balance: null,
}

describe('LimitStatsPanel', () => {
  it('renders stats, premium cards, high-mark rows and expandable board ladder', () => {
    render(
      <LimitStatsPanel
        market={market}
        boards={[
          { board: 6, count: 1, stocks: ['高标A'] },
          { board: 4, count: 2, stocks: ['高标B', '高标C'] },
        ]}
        maxBoardCount={2}
        highMarkRows={[
          { ts_code: '000001.SZ', name: '高标A', nums: '6' },
          { ts_code: '000002.SZ', name: '高标B', nums: '4' },
        ]}
      />
    )

    expect(screen.getByText('涨跌停统计')).toBeInTheDocument()
    expect(screen.getByText('溢价率')).toBeInTheDocument()
    expect(screen.getByText('96')).toBeInTheDocument()
    expect(screen.getByText('82.5 %')).toBeInTheDocument()
    expect(screen.getByText('10cm首板')).toBeInTheDocument()
    expect(screen.getByText('2.10 %')).toBeInTheDocument()
    expect(screen.getByText('高标明细')).toBeInTheDocument()
    expect(screen.getAllByText('高标A').length).toBeGreaterThan(0)

    fireEvent.click(screen.getAllByRole('button', { name: '▼' })[0])
    expect(screen.getAllByText('高标A').length).toBeGreaterThan(1)
  })
})
