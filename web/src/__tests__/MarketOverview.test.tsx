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
      northbound_net: null,
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
      northbound_net: null,
    },
  ]
  const postMarket: PostMarketPayload = { available: false }
  const mainThemes: MainThemeItem[] = []
  vi.mocked(api.getMarketHistory).mockResolvedValue(history)
  vi.mocked(api.getPostMarket).mockResolvedValue(postMarket)
  vi.mocked(api.getMainThemes).mockResolvedValue(mainThemes)
})

describe('MarketOverview', () => {
  it('renders market status, emotion status, daily_info, limit_step, strongest sectors and dual-source sector moneyflow sections in summary view', async () => {
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
      daily_info: {
        data: [
          { ts_code: 'SH_MARKET', ts_name: '上海市场', amount: 5234.5, total_mv: 620000, tr: 1.82 },
          { ts_code: 'SZ_MARKET', ts_name: '深圳市场', amount: 6042.8, total_mv: 580000, tr: 2.16 },
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
      expect(screen.getByText('市场交易结构')).toBeInTheDocument()
    })
    expect(screen.getByText('市场状态观察')).toBeInTheDocument()
    expect(screen.getByText('放量')).toBeInTheDocument()
    expect(screen.getByText('普涨')).toBeInTheDocument()
    expect(screen.getByText('净流入')).toBeInTheDocument()
    expect(screen.getByText('线上占优')).toBeInTheDocument()
    expect(screen.getByText('情绪状态观察')).toBeInTheDocument()
    expect(screen.getByText('涨停扩散')).toBeInTheDocument()
    expect(screen.getByText('封板稳')).toBeInTheDocument()
    expect(screen.getByText('高标打开')).toBeInTheDocument()
    expect(screen.getByText('主线集中')).toBeInTheDocument()
    expect(screen.getByText('大盘资金流向')).toBeInTheDocument()
    expect(screen.getAllByText('+25.00亿').length).toBeGreaterThan(0)
    expect(screen.getByText('+2.80%')).toBeInTheDocument()
    expect(screen.getByText('上海市场')).toBeInTheDocument()
    expect(screen.getByText('高标明细')).toBeInTheDocument()
    expect(screen.getAllByText('高标A').length).toBeGreaterThan(0)
    expect(screen.getByText('最强板块')).toBeInTheDocument()
    expect(screen.getAllByText('人工智能').length).toBeGreaterThan(0)
    expect(screen.getByText('THS 行业资金流前列')).toBeInTheDocument()
    expect(screen.getByText('DC 板块资金流前列')).toBeInTheDocument()
    expect(screen.getAllByText('软件开发').length).toBeGreaterThan(0)
  })
})
