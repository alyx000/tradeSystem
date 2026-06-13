import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import MarketTimingPanel from '../components/market/MarketTimingPanel'
import type { MarketTimingPayload, MarketTimingHistoryPayload } from '../lib/types'

const payload: MarketTimingPayload = {
  date: '2026-06-12',
  available: true,
  resonance_count: 3,
  context: { market_amount_yi: 32150, amount_pctile_20d: 0.1, advance: 4500, decline: 900, limit_down_count: 12 },
  signals: [
    {
      index_code: '000001.SH', index_name: '上证综指',
      swing_pivot_date: '2026-05-14', swing_pivot_type: 'high', swing_pivot_price: 4258.86,
      fib_day_count: 21, fib_hit: 21, fib_near: null,
      fractal_status: 'forming', fractal_low_date: '2026-06-11', fractal_low_price: 3958.44,
      fractal_confirm_date: null,
    },
    {
      index_code: '932000.CSI', index_name: '中证2000',
      swing_pivot_date: '2026-05-20', swing_pivot_type: 'low', swing_pivot_price: 3300,
      fib_day_count: 7, fib_hit: null, fib_near: 8,
      fractal_status: 'confirmed', fractal_low_date: '2026-06-10', fractal_low_price: 3280,
      fractal_confirm_date: '2026-06-12',
    },
  ],
}

const history: MarketTimingHistoryPayload = {
  requested_days: 30,
  series: [
    { date: '2026-06-11', date_short: '06-11', resonance_count: 0, amount_pctile_20d: 0.1 },
    { date: '2026-06-12', date_short: '06-12', resonance_count: 3, amount_pctile_20d: 0.8 },
  ],
}

describe('MarketTimingPanel', () => {
  it('renders 三段 + 命中/临近 + 底分型徽章 + 共振', () => {
    render(<MarketTimingPanel payload={payload} history={history} />)
    expect(screen.getByText(/大盘择时观察 · 2026-06-12/)).toBeInTheDocument()
    expect(screen.getByText(/时间周期 · 变盘点/)).toBeInTheDocument()
    expect(screen.getByText(/命中斐波那契 21/)).toBeInTheDocument()
    expect(screen.getByText(/临近 8/)).toBeInTheDocument()
    expect(screen.getByText('🟢 底分型确认')).toBeInTheDocument()
    expect(screen.getByText(/共振变盘点/)).toBeInTheDocument()
    expect(screen.getByText(/3 个指数/)).toBeInTheDocument()
    expect(screen.getByText(/10% 地量/)).toBeInTheDocument()  // pctile 0.1 ≤ 0.2 → 地量标注
    expect(screen.getByText(/共振 \/ 成交额地量分位 趋势/)).toBeInTheDocument()
  })

  it('守红线：声明存在且无买卖/方向/价位字样', () => {
    const { container } = render(<MarketTimingPanel payload={payload} history={history} />)
    expect(screen.getByText(/不构成买卖建议、不预测方向、不出价位/)).toBeInTheDocument()
    const text = container.textContent ?? ''
    for (const forbidden of ['买入价', '卖出价', '目标价', '建议买', '建议卖', '止损位']) {
      expect(text).not.toContain(forbidden)
    }
  })

  it('available=false → 暂无提示，不渲染表格', () => {
    render(<MarketTimingPanel payload={{ ...payload, available: false }} />)
    expect(screen.getByText(/暂无 2026-06-12 的大盘择时数据/)).toBeInTheDocument()
    expect(screen.queryByText(/时间周期 · 变盘点/)).not.toBeInTheDocument()
  })

  it('无 history 时表格仍渲染、趋势图隐藏', () => {
    render(<MarketTimingPanel payload={payload} />)
    expect(screen.getByText(/时间周期 · 变盘点/)).toBeInTheDocument()
    expect(screen.queryByText(/成交额地量分位 趋势/)).not.toBeInTheDocument()
  })

  it('asOfDate 防前瞻：窗口内仍渲染、全为未来点时趋势图隐藏', () => {
    const future: MarketTimingHistoryPayload = {
      requested_days: 30,
      series: [
        { date: '2026-06-20', date_short: '06-20', resonance_count: 9, amount_pctile_20d: 0.99 },
        { date: '2026-06-21', date_short: '06-21', resonance_count: 5, amount_pctile_20d: 0.88 },
      ],
    }
    // 复盘 06-12，趋势点全在其后 → 全滤除 → 趋势图隐藏（表格仍在）；杜绝历史复盘看到未来
    render(<MarketTimingPanel payload={payload} history={future} asOfDate="2026-06-12" />)
    expect(screen.getByText(/时间周期 · 变盘点/)).toBeInTheDocument()
    expect(screen.queryByText(/成交额地量分位 趋势/)).not.toBeInTheDocument()
    // 同 history 不带 asOfDate（实时）→ 趋势图正常渲染，证明是 asOfDate 在过滤
    render(<MarketTimingPanel payload={payload} history={future} />)
    expect(screen.getByText(/成交额地量分位 趋势/)).toBeInTheDocument()
  })
})
