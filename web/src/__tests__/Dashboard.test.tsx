import { render, screen, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import Dashboard from '../pages/Dashboard'
import { api } from '../lib/api'
import type {
  CalendarEvent,
  Holding,
  HoldingTaskItem,
  IngestDashboardHealthSummary,
  IngestHealthSummary,
  MarketFullData,
  ReviewRecord,
} from '../lib/types'

vi.mock('../lib/api', () => ({
  api: {
    getReview: vi.fn(),
    getHoldings: vi.fn(),
    getCalendarRange: vi.fn(),
    getMarket: vi.fn(),
    listHoldingTasks: vi.fn(),
    getIngestDashboardHealthSummary: vi.fn(),
  },
}))

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <Dashboard />
      </MemoryRouter>
    </QueryClientProvider>
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(api.getReview).mockResolvedValue({ exists: false } as ReviewRecord)
  vi.mocked(api.getHoldings).mockResolvedValue([{ id: 1, stock_code: '300750', stock_name: '宁德时代', entry_price: 100, current_price: 101, shares: 100, status: 'active' }] as Holding[])
  vi.mocked(api.getCalendarRange).mockResolvedValue([{ id: 1, date: '2026-04-05', event: 'CPI数据', impact: 'high', category: 'macro' }] as CalendarEvent[])
  vi.mocked(api.getMarket).mockResolvedValue({
    available: true,
    date: '2026-04-05',
    sh_index_close: 3300,
    sh_index_change_pct: 1.2,
    sz_index_close: 10500,
    sz_index_change_pct: 0.8,
    total_amount: 12345,
    advance_count: 3000,
    decline_count: 1800,
    sh_above_ma5w: true,
    sz_above_ma5w: true,
    chinext_above_ma5w: true,
    star50_above_ma5w: true,
    avg_price_above_ma5w: true,
    limit_up_count: 82,
    limit_down_count: 4,
    seal_rate: 79,
    broken_rate: 21,
    highest_board: 5,
    continuous_board_counts: null,
    premium_10cm: 1,
    premium_20cm: 2,
    premium_30cm: 3,
    premium_second_board: 4,
    northbound_net: 10,
    margin_balance: 20,
  } as MarketFullData)
  vi.mocked(api.listHoldingTasks).mockResolvedValue([
    {
      id: 1,
      trade_date: '2026-04-03',
      stock_code: '300750.SZ',
      stock_name: '宁德时代',
      action_plan: '若冲高回落则减仓',
      source: 'review_step7',
      status: 'open',
    },
  ] as HoldingTaskItem[])
  vi.mocked(api.getIngestDashboardHealthSummary).mockResolvedValue({
    core: {
      start_date: '2026-03-31',
      end_date: '2026-04-06',
      days: 7,
      stage: 'post_core',
      total_runs: 18,
      total_failures: 5,
      unresolved_failures: 2,
      failed_interface_count: 2,
      never_succeeded_count: 1,
      failure_rate: 0.2778,
      status_label: '需处理',
      status_reason: '存在从未成功过的接口，建议优先排查权限、配置或实现缺口。',
      top_failed_interfaces: [
        {
          interface_name: 'anns_d',
          interface_label: '全市场公告',
          failure_count: 2,
          unresolved_count: 2,
          consecutive_failure_days: 2,
          days_since_last_success: null,
          last_success_biz_date: null,
          last_failure_biz_date: '2026-04-05',
        },
      ],
      daily_failures: [{ biz_date: '2026-04-05', error_count: 4 }],
    } as IngestHealthSummary,
    extended: {
      start_date: '2026-03-31',
      end_date: '2026-04-06',
      days: 7,
      stage: 'post_extended',
      total_runs: 12,
      total_failures: 3,
      unresolved_failures: 1,
      failed_interface_count: 1,
      never_succeeded_count: 0,
      failure_rate: 0.25,
      status_label: '承压',
      status_reason: '存在连续失败 3 天的接口，阶段稳定性已明显承压。',
      top_failed_interfaces: [
        {
          interface_name: 'block_trade',
          interface_label: '大宗交易',
          failure_count: 1,
          unresolved_count: 1,
          consecutive_failure_days: 3,
          days_since_last_success: 8,
          last_success_biz_date: '2026-03-28',
          last_failure_biz_date: '2026-04-05',
        },
      ],
      daily_failures: [{ biz_date: '2026-04-05', error_count: 2 }],
    } as IngestHealthSummary,
  } as IngestDashboardHealthSummary)
})

describe('Dashboard', () => {
  it('renders command quickstart cards from meta commands api', async () => {
    renderPage()

    await waitFor(() => {
      expect(screen.getByText('命令速查')).toBeInTheDocument()
    })

    expect(screen.getByText('make bootstrap')).toBeInTheDocument()
    expect(screen.getByText('执行今日盘后流程')).toBeInTheDocument()
    expect(screen.getByText('make today-ingest-health')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /打开健康视图/ })).toHaveAttribute('href', '/ingest?date=2026-04-06')
  })

  it('renders ingest health summary cards for core and extended', async () => {
    renderPage()

    await waitFor(() => {
      expect(screen.getByText('采集健康 · 盘后核心')).toBeInTheDocument()
    })

    expect(screen.getByText('采集健康 · 盘后扩展')).toBeInTheDocument()
    expect(screen.getByText('需处理')).toBeInTheDocument()
    expect(screen.getByText('承压')).toBeInTheDocument()
    expect(screen.getByText('存在从未成功过的接口，建议优先排查权限、配置或实现缺口。')).toBeInTheDocument()
    expect(screen.getByText('存在连续失败 3 天的接口，阶段稳定性已明显承压。')).toBeInTheDocument()
    expect(screen.getByText('全市场公告 · 连续失败 2 天')).toBeInTheDocument()
    expect(screen.getByText('大宗交易 · 连续失败 3 天')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /采集健康 · 盘后核心/ })).toHaveAttribute('href', '/ingest?date=2026-04-06&health_sort=streak')
    expect(screen.getByRole('link', { name: /采集健康 · 盘后扩展/ })).toHaveAttribute('href', '/ingest?date=2026-04-06&stage=post_extended&health_sort=streak')
  })

  it('renders pending holding tasks card', async () => {
    renderPage()

    await waitFor(() => {
      expect(screen.getByText('未完成持仓计划')).toBeInTheDocument()
    })

    expect(screen.getByText('宁德时代')).toBeInTheDocument()
    expect(screen.getByText('2026-04-03 · 若冲高回落则减仓')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /打开任务页/ })).toHaveAttribute('href', '/holding-tasks?date=2026-04-06&status=open')
  })
})
