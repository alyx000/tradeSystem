import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import MarginIndexCorrelation from '../components/review/MarginIndexCorrelation'
import { api } from '../lib/api'
import type { MarginIndexCorrelationPayload } from '../lib/types'

vi.mock('../lib/api', () => ({
  api: { getMarginIndexCorrelation: vi.fn() },
}))

function renderComp(date: string | undefined) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MarginIndexCorrelation date={date} />
    </QueryClientProvider>
  )
}

const PAYLOAD: MarginIndexCorrelationPayload = {
  date: '2026-06-19',
  available: true,
  data_trade_date: '2026-06-18',
  windows: [5, 20, 60],
  base_index: '000001.SH',
  indices: [
    { pair_key: 'total:000001.SH', margin_key: 'total', index_code: '000001.SH', index_name: '上证指数', group: 'broad' },
    { pair_key: 'sse:000001.SH', margin_key: 'sse', index_code: '000001.SH', index_name: '上证指数', group: 'cross', margin_label: '沪市两融' },
  ],
  lag: {
    'total:000001.SH': { best_lag: 2, best_corr: 0.61, relation: '两融滞后' },
  },
  sync_corr: {
    'total:000001.SH': { '5': { corr: -0.12, label: '独立' }, '20': { corr: 0.57, label: '弱同向' }, '60': { corr: 0.5, label: '弱同向' } },
  },
  divergence: {
    'total:000001.SH': { '5': { index_cum: 4.1, margin_cum: -1.5, diverged: true, type: '涨指两融降', magnitude: 5.6 } },
  },
  balance: {
    total: { latest_yi: 29655.12, dod_pct: 0.43, pctile_20d: 1.0, up_streak: 4, down_streak: 0, ma20: 29100, vs_ma20: 1.84 },
    sse: { latest_yi: 15092.75, dod_pct: 0.45, pctile_20d: 1.0, up_streak: 6, down_streak: 0, ma20: 14800, vs_ma20: 1.69 },
  },
  meta: { source: 'tushare:margin', market_scope: 'BSE+SSE+SZSE', analysis_trade_date: '2026-06-18', stale: true },
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(api.getMarginIndexCorrelation).mockResolvedValue(PAYLOAD)
})

describe('MarginIndexCorrelation', () => {
  it('date 缺失时不渲染、不发请求', () => {
    renderComp(undefined)
    expect(api.getMarginIndexCorrelation).not.toHaveBeenCalled()
    expect(screen.queryByText('两融×指数联动性')).toBeNull()
  })

  it('available=false 展示空态', async () => {
    vi.mocked(api.getMarginIndexCorrelation).mockResolvedValue({ date: '2026-06-19', available: false })
    renderComp('2026-06-19')
    expect(await screen.findByText(/暂无两融联动数据/)).toBeTruthy()
  })

  it('渲染背离头条 + stale 提示 + 余额水位 + 同步相关', async () => {
    renderComp('2026-06-19')
    // 背离命中
    expect(await screen.findByText(/涨指两融降/)).toBeTruthy()
    // stale T-1 提示带真实日
    expect(screen.getByText('2026-06-18')).toBeTruthy()
    // 余额水位（合计 + 沪市）
    expect(screen.getByText('两融合计')).toBeTruthy()
    expect(screen.getByText('沪市两融')).toBeTruthy()
    expect(screen.getByText(/29,655/)).toBeTruthy()
    // 领先/滞后
    expect(screen.getByText(/两融滞后/)).toBeTruthy()
    // 同步相关多窗表头
    expect(screen.getByText('5日')).toBeTruthy()
    expect(screen.getByText('60日')).toBeTruthy()
  })

  it('未评估窗口不渲染成无背离', async () => {
    vi.mocked(api.getMarginIndexCorrelation).mockResolvedValue({
      ...PAYLOAD,
      divergence: { 'total:000001.SH': { '5': { index_cum: null, margin_cum: null, diverged: false, type: '日期缺口', magnitude: null } } },
    })
    renderComp('2026-06-19')
    expect(await screen.findByText(/数据质量提示/)).toBeTruthy()
    expect(screen.getByText(/未评估/)).toBeTruthy()
  })
})
