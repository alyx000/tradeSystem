import { render, screen, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import type { ReactNode } from 'react'
import MarketOverview from '../pages/MarketOverview'
import { api } from '../lib/api'
import type { MainThemeItem, MarketChartItem, MarketFullData, PostMarketPayload } from '../lib/types'

vi.mock('../lib/api', () => ({
  api: {
    getMarket: vi.fn(),
    getMarketHistory: vi.fn(),
    getPostMarket: vi.fn(),
    getMainThemes: vi.fn(),
    getConcentrationHistory: vi.fn(),
    getMarketTiming: vi.fn(),
    getMarketTimingHistory: vi.fn(),
  },
}))

vi.mock('recharts', () => {
  const Mock = ({ children }: { children?: ReactNode }) => <div>{children}</div>
  return {
    ResponsiveContainer: Mock,
    ComposedChart: Mock,
    Line: () => null,
    Bar: () => null,
    XAxis: () => null,
    YAxis: () => null,
    CartesianGrid: () => null,
    Tooltip: () => null,
    Legend: () => null,
  }
})

function renderPage(date = '2026-04-03') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[`/market/${date}`]}>
        <Routes>
          <Route path="/market/:date" element={<MarketOverview />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  const history: MarketChartItem[] = [
    {
      date: '2026-04-02',
      date_short: '04-02',
      sh_index_close: null,
      sh_index_change_pct: null,
      sz_index_close: null,
      sz_index_change_pct: null,
      total_amount: 11200,
      advance_count: 3200,
      decline_count: 1800,
      limit_up_count: 88,
      limit_down_count: null,
      seal_rate: null,
      broken_rate: null,
      highest_board: null,
      premium_10cm: null,
      premium_20cm: null,
      premium_30cm: null,
      premium_second_board: null,
    },
    {
      date: '2026-04-03',
      date_short: '04-03',
      sh_index_close: null,
      sh_index_change_pct: null,
      sz_index_close: null,
      sz_index_change_pct: null,
      total_amount: 11800,
      advance_count: 3500,
      decline_count: 1500,
      limit_up_count: 96,
      limit_down_count: null,
      seal_rate: null,
      broken_rate: null,
      highest_board: null,
      premium_10cm: null,
      premium_20cm: null,
      premium_30cm: null,
      premium_second_board: null,
    },
  ]
  const postMarket: PostMarketPayload = { available: false }
  const mainThemes: MainThemeItem[] = []
  vi.mocked(api.getMarketHistory).mockResolvedValue(history)
  vi.mocked(api.getPostMarket).mockResolvedValue(postMarket)
  vi.mocked(api.getMainThemes).mockResolvedValue(mainThemes)
  // 默认无择时/集中度数据（null）：现有用例不渲染择时卡片/面板，行为与改动前一致
  vi.mocked(api.getConcentrationHistory).mockResolvedValue(null as never)
  vi.mocked(api.getMarketTiming).mockResolvedValue(null as never)
  vi.mocked(api.getMarketTimingHistory).mockResolvedValue(null as never)
})

describe('MarketOverview', () => {
  it('renders market status, emotion status, limit_step, strongest sectors and dual-source sector moneyflow sections in summary view', async () => {
    const market: MarketFullData = {
      available: true,
      date: '2026-04-03',
      sh_index_close: 3200,
      sh_index_change_pct: 1.2,
      sz_index_close: 10000,
      sz_index_change_pct: 2.1,
      total_amount: 11800,
      northbound_net: 86.5,
      advance_count: 3500,
      decline_count: 1500,
      sh_above_ma5w: true,
      sz_above_ma5w: true,
      chinext_above_ma5w: false,
      star50_above_ma5w: true,
      avg_price_above_ma5w: true,
      limit_up_count: 96,
      limit_down_count: 4,
      highest_board: 6,
      seal_rate: 82.5,
      broken_rate: 17.5,
      continuous_board_counts: JSON.stringify({ 6: ['高标A'], 4: ['高标B', '高标C'] }),
      premium_10cm: 2.1,
      premium_20cm: 3.4,
      premium_30cm: 1.2,
      premium_second_board: 4.5,
      sector_industry: { data: [{ name: '算力', change_pct: 5.1 }] },
      sector_concept: { data: [{ name: 'AI应用', change_pct: 6.2 }] },
      sector_fund_flow: { data: [{ name: '软件开发', change_pct: 4.8, net_inflow: 22.5 }] },
      sector_moneyflow_ths: {
        data: [
          { ts_code: '881001.TI', industry: '软件开发', net_amount: 22.5, pct_change: 4.8, lead_stock: '高标A' },
        ],
      },
      sector_moneyflow_dc: {
        data: [
          { ts_code: 'BK1234', name: '人工智能', content_type: '概念', net_amount: 1800000000, buy_sm_amount_stock: '高标A' },
        ],
      },
      market_moneyflow_dc: {
        data: [
          { net_amount: 2500000000, net_amount_rate: 2.8, buy_elg_amount: 1200000000, buy_lg_amount: 800000000 },
        ],
      },
      limit_step: {
        data: [
          { ts_code: '000001.SZ', name: '高标A', nums: '6' },
          { ts_code: '000002.SZ', name: '高标B', nums: '4' },
        ],
      },
      limit_cpt_list: {
        data: [
          { ts_code: '885001.TI', rank: 1, name: '人工智能', up_nums: 12, cons_nums: 4, pct_chg: 3.6, up_stat: '6天5板' },
          { ts_code: '885002.TI', rank: 2, name: '机器人', up_nums: 9, cons_nums: 3, pct_chg: 2.9, up_stat: '4天3板' },
        ],
      },
      indices: {
        chinext: { close: 2000, change_pct: 1.8 },
        star50: { close: 900, change_pct: 0.9 },
      },
      margin_balance: null,
    }
    vi.mocked(api.getMarket).mockResolvedValue(market)

    renderPage()

    await waitFor(() => {
      expect(screen.getByText('成交与资金')).toBeInTheDocument()
    })
    expect(screen.getByText('市场状态观察')).toBeInTheDocument()
    expect(screen.getByText('放量')).toBeInTheDocument()
    expect(screen.getByText('普涨')).toBeInTheDocument()
    expect(screen.getByText('净流入')).toBeInTheDocument()
    expect(screen.getByText('线上占优')).toBeInTheDocument()
    // 5周均线状态已从独立卡并入指数卡片：原面板消失，各卡显示「5周线 线上/线下」
    expect(screen.queryByText('5周均线状态')).not.toBeInTheDocument()
    expect(screen.getAllByText('5周线').length).toBe(5)   // 上证/深证/创业板/科创50/平均股价（中证2000 无 MA5W）
    expect(screen.getByText('线下')).toBeInTheDocument()   // 创业板 chinext_above_ma5w=false
    expect(screen.getByText('情绪状态观察')).toBeInTheDocument()
    expect(screen.getByText('涨停扩散')).toBeInTheDocument()
    expect(screen.getByText('封板稳')).toBeInTheDocument()
    expect(screen.getByText('高标打开')).toBeInTheDocument()
    expect(screen.getByText('主线集中')).toBeInTheDocument()
    // 北向净额已下线(口径存疑),即便 northbound_net=86.5 也不渲染
    expect(screen.queryByText('北向净额')).not.toBeInTheDocument()
    expect(screen.getByText('大盘资金流向')).toBeInTheDocument()
    expect(screen.getAllByText('+25.00亿').length).toBeGreaterThan(0)
    expect(screen.getByText('+2.80%')).toBeInTheDocument()
    expect(screen.getByText('高标明细')).toBeInTheDocument()
    expect(screen.getAllByText('高标A').length).toBeGreaterThan(0)
    expect(screen.getByText('最强板块')).toBeInTheDocument()
    expect(screen.getAllByText('人工智能').length).toBeGreaterThan(0)
    expect(screen.getByText('THS 行业资金流前列')).toBeInTheDocument()
    expect(screen.getByText('DC 板块资金流前列')).toBeInTheDocument()
    expect(screen.getAllByText('软件开发').length).toBeGreaterThan(0)
  })

  it('中证2000 / 平均股价：daily_market 未采集，卡片点位取自 market-timing payload', async () => {
    const market: MarketFullData = {
      available: true, date: '2026-04-03',
      sh_index_close: 3200, sh_index_change_pct: 1.2, sz_index_close: 10000, sz_index_change_pct: 2.1,
      total_amount: 11800, northbound_net: null, advance_count: 3500, decline_count: 1500,
      sh_above_ma5w: null, sz_above_ma5w: null, chinext_above_ma5w: null, star50_above_ma5w: null,
      avg_price_above_ma5w: null, limit_up_count: null, limit_down_count: null, highest_board: null,
      seal_rate: null, broken_rate: null, continuous_board_counts: null,
      premium_10cm: null, premium_20cm: null, premium_30cm: null, premium_second_board: null,
      margin_balance: null,
    }
    vi.mocked(api.getMarket).mockResolvedValue(market)
    vi.mocked(api.getMarketTiming).mockResolvedValue({
      date: '2026-04-03', available: true, resonance_count: 1,
      context: { market_amount_yi: 11800, amount_pctile_20d: 0.5, advance: 3500, decline: 1500, limit_down_count: 0 },
      signals: [
        {
          index_code: '932000.CSI', index_name: '中证2000', close: 3313.78, change_pct: 0.82,
          swing_pivot_date: '2026-03-10', swing_pivot_type: 'high', swing_pivot_price: 3400,
          fib_day_count: 21, fib_hit: 21, fib_near: null,
          fractal_status: 'forming', fractal_low_date: null, fractal_low_price: null, fractal_confirm_date: null,
        },
        {
          index_code: 'avg_price', index_name: '平均股价', close: 29.7, change_pct: 1.05,
          swing_pivot_date: '2026-03-12', swing_pivot_type: 'high', swing_pivot_price: 31,
          fib_day_count: 16, fib_hit: null, fib_near: null,
          fractal_status: 'forming', fractal_low_date: null, fractal_low_price: null, fractal_confirm_date: null,
        },
      ],
    } as never)

    renderPage()

    await waitFor(() => expect(screen.getByText('中证2000')).toBeInTheDocument())
    expect(screen.getByText('3313.78')).toBeInTheDocument()        // 点位来自 timing.close
    expect(screen.getByText('平均股价')).toBeInTheDocument()
    expect(screen.getByText('29.7')).toBeInTheDocument()
    // 变盘窗口内嵌到卡片：中证2000 命中、平均股价未到
    expect(screen.getByText(/🎯 变盘窗口 21日/)).toBeInTheDocument()
    expect(screen.getByText(/未到（16日）/)).toBeInTheDocument()
  })
})
