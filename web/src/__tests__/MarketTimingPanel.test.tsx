import { render, screen, fireEvent } from '@testing-library/react'
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
      index_code: '000001.SH', index_name: '上证综指', close: 4031.51, change_pct: 1.12,
      swing_pivot_date: '2026-05-14', swing_pivot_type: 'high', swing_pivot_price: 4258.86,
      fib_day_count: 21, fib_hit: 21, fib_near: null,
      fractal_status: 'forming', fractal_low_date: '2026-06-11', fractal_low_price: 3958.44,
      fractal_confirm_date: null,
    },
    {
      index_code: '932000.CSI', index_name: '中证2000', close: 3313.78, change_pct: 0.82,
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

describe('MarketTimingPanel（择时汇总条）', () => {
  it('汇总：共振 + 成交额地量分位 + 场外指数变盘窗口（cardCodes 外）', () => {
    // 上证已上顶部卡片(cardCodes)，故只剩中证2000 作为「场外」在汇总条出现
    render(<MarketTimingPanel payload={payload} history={history} cardCodes={['000001.SH']} />)
    expect(screen.getByText(/大盘择时观察 · 2026-06-12/)).toBeInTheDocument()
    expect(screen.getByText(/共振变盘点 3 个指数/)).toBeInTheDocument()
    expect(screen.getByText(/成交额 32150 亿（分位 10% 地量）/)).toBeInTheDocument()
    expect(screen.getByText(/中证2000 ⏳ 临近变盘窗口（斐波那契8，差1日）/)).toBeInTheDocument()
    // 上证在卡片上 → 不重复出现在场外
    expect(screen.queryByText(/上证综指 🎯/)).not.toBeInTheDocument()
    expect(screen.getByText(/不预判涨跌/)).toBeInTheDocument()
  })

  it('底分型摘要 + 确认高亮（折叠决策保留）', () => {
    render(<MarketTimingPanel payload={payload} cardCodes={['000001.SH']} />)
    expect(screen.getByText(/🔻 底分型：1 成型（低点 2026-06-11） · 1 确认 · 0 破坏/)).toBeInTheDocument()
    expect(screen.getByText(/🟢 中证2000 底分型确认（放量中阳突破） · 确认日 2026-06-12 · 结构低点 2026-06-10（3280）/)).toBeInTheDocument()
  })

  it('展开明细：默认折叠、点开后全部指数的拐点/底分型可见，不丢信息', () => {
    render(<MarketTimingPanel payload={payload} cardCodes={['000001.SH']} />)
    expect(screen.queryByText('起算拐点 [事实]')).not.toBeInTheDocument()
    fireEvent.click(screen.getByText(/展开全部 2 指数明细/))
    expect(screen.getByText('起算拐点 [事实]')).toBeInTheDocument()
    expect(screen.getByText(/高点 2026-05-14/)).toBeInTheDocument()  // 上证拐点(事实)，即便已上卡片，明细仍保留
    expect(screen.getByText(/🎯 变盘窗口·第21交易日/)).toBeInTheDocument()
  })

  it('cardCodes 默认空 → 全部指数都作为场外列出', () => {
    render(<MarketTimingPanel payload={payload} />)
    expect(screen.getByText(/上证综指 🎯 变盘窗口·第21交易日/)).toBeInTheDocument()
    expect(screen.getByText(/中证2000 ⏳ 临近变盘窗口（斐波那契8/)).toBeInTheDocument()
  })

  it('全部指数都上卡（cardCodes 覆盖全 payload）→ 「场外」段整段不渲染', () => {
    const allCodes = payload.signals.map((s) => s.index_code)
    render(<MarketTimingPanel payload={payload} cardCodes={allCodes} />)
    expect(screen.getByText(/共振变盘点/)).toBeInTheDocument()  // 汇总条其余部分仍在
    expect(screen.queryByText(/场外：/)).not.toBeInTheDocument()
    // 场外指数文案（完整 fibTextFull）也不应残留
    expect(screen.queryByText(/中证2000 ⏳ 临近变盘窗口/)).not.toBeInTheDocument()
  })

  it('守红线：声明存在且无买卖/方向/价位字样（含展开明细）', () => {
    const { container } = render(<MarketTimingPanel payload={payload} cardCodes={['000001.SH']} />)
    expect(screen.getByText(/不构成买卖建议、不预测方向、不出价位/)).toBeInTheDocument()
    fireEvent.click(screen.getByText(/展开全部/))
    const text = container.textContent ?? ''
    for (const forbidden of ['买入价', '卖出价', '目标价', '建议买', '建议卖', '止损位']) {
      expect(text).not.toContain(forbidden)
    }
  })

  it('available=false → 暂无提示，不渲染汇总', () => {
    render(<MarketTimingPanel payload={{ ...payload, available: false }} />)
    expect(screen.getByText(/暂无 2026-06-12 的大盘择时数据/)).toBeInTheDocument()
    expect(screen.queryByText(/共振变盘点/)).not.toBeInTheDocument()
  })

  it('无 history 时汇总仍渲染、趋势图隐藏', () => {
    render(<MarketTimingPanel payload={payload} cardCodes={['000001.SH']} />)
    expect(screen.getByText(/共振变盘点/)).toBeInTheDocument()
    expect(screen.queryByText(/成交额地量分位 趋势/)).not.toBeInTheDocument()
  })

  it('asOfDate 防前瞻：全为未来点时趋势图隐藏，正文仍在', () => {
    const future: MarketTimingHistoryPayload = {
      requested_days: 30,
      series: [
        { date: '2026-06-20', date_short: '06-20', resonance_count: 9, amount_pctile_20d: 0.99 },
        { date: '2026-06-21', date_short: '06-21', resonance_count: 5, amount_pctile_20d: 0.88 },
      ],
    }
    render(<MarketTimingPanel payload={payload} history={future} asOfDate="2026-06-12" cardCodes={['000001.SH']} />)
    expect(screen.getByText(/共振变盘点/)).toBeInTheDocument()
    expect(screen.queryByText(/成交额地量分位 趋势/)).not.toBeInTheDocument()
    render(<MarketTimingPanel payload={payload} history={future} cardCodes={['000001.SH']} />)
    expect(screen.getByText(/成交额地量分位 趋势/)).toBeInTheDocument()
  })
})
