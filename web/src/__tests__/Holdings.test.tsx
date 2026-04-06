import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import Holdings from '../pages/Holdings'
import { api } from '../lib/api'
import type { Holding, HoldingSignalsPayload, HoldingTaskItem } from '../lib/types'

vi.mock('../lib/api', () => ({
  api: {
    getHoldings: vi.fn(),
    getHoldingSignals: vi.fn(),
    listHoldingTasks: vi.fn(),
    updateHoldingTask: vi.fn(),
    createHolding: vi.fn(),
    deleteHolding: vi.fn(),
  },
}))

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <Holdings />
      </MemoryRouter>
    </QueryClientProvider>
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(api.getHoldings).mockResolvedValue([
    {
      id: 1,
      stock_code: '300750',
      stock_name: '宁德时代',
      entry_price: 180,
      current_price: 192,
      shares: 100,
      status: 'active',
    },
  ] as Holding[])
  vi.mocked(api.getHoldingSignals).mockResolvedValue({
    date: '2026-04-06',
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
          main_theme_name: 'AI算力',
          is_strongest_sector: true,
          strongest_sector_name: '电池',
          sector_flow_confirmed: true,
          sector_flow_source: 'ths',
        },
        event_signals: {
          has_recent_announcement: true,
          recent_announcements: [{ ann_date: '20260405', title: '回购公告' }],
          has_disclosure_plan: true,
          disclosure_dates: [{ ann_date: '20260420', report_end: '20260331' }],
          is_st: false,
          share_float_upcoming: [],
        },
        latest_task: {
          id: 1,
          trade_date: '2026-04-05',
          stock_code: '300750.SZ',
          stock_name: '宁德时代',
          action_plan: '若冲高回落则减仓',
          source: 'review_step7',
          status: 'open',
        },
        risk_flags: [
          { level: 'high', label: '财报临近', reason: '20260420 有披露计划' },
          { level: 'medium', label: '跌破 MA5', reason: '现价位于 MA5 下方' },
        ],
      },
    ],
  } as HoldingSignalsPayload)
  vi.mocked(api.listHoldingTasks).mockImplementation(async (_date?: string, status = 'open') => {
    const baseTask: HoldingTaskItem = {
      id: 1,
      trade_date: '2026-04-05',
      stock_code: '300750.SZ',
      stock_name: '宁德时代',
      action_plan: '若冲高回落则减仓',
      source: 'review_step7',
      status: 'open',
    }
    const doneTask: HoldingTaskItem = {
      id: 2,
      trade_date: '2026-04-04',
      stock_code: '300750.SZ',
      stock_name: '宁德时代',
      action_plan: '冲高兑现半仓',
      source: 'review_step7',
      status: 'done',
    }
    const ignoredTask: HoldingTaskItem = {
      id: 3,
      trade_date: '2026-04-03',
      stock_code: '300750.SZ',
      stock_name: '宁德时代',
      action_plan: '若走弱则离场',
      source: 'review_step7',
      status: 'ignored',
    }
    if (status === 'done') return [doneTask]
    if (status === 'ignored') return [ignoredTask]
    return [baseTask]
  })
  vi.mocked(api.updateHoldingTask).mockResolvedValue({ ok: true })
})

describe('Holdings', () => {
  it('renders risk, theme and technical signals for holdings', async () => {
    renderPage()

    await waitFor(() => {
      expect(screen.getByText('财报临近')).toBeInTheDocument()
    })

    expect(screen.getByText('风险')).toBeInTheDocument()
    expect(screen.getByText('主线归属')).toBeInTheDocument()
    expect(screen.getByText('技术位')).toBeInTheDocument()
    expect(screen.getByText('财报临近')).toBeInTheDocument()
    expect(screen.getByText('跌破 MA5')).toBeInTheDocument()
    expect(screen.getByText('主线: AI算力')).toBeInTheDocument()
    expect(screen.getByText('最强板块: 电池')).toBeInTheDocument()
    expect(screen.getByText('资金确认: THS')).toBeInTheDocument()
    expect(screen.getByText('站上 MA5')).toBeInTheDocument()
    expect(screen.getByText('站上 MA10')).toBeInTheDocument()
    expect(screen.getByText('量在均量以上')).toBeInTheDocument()
    expect(screen.getByText('换手 6.20%（活跃）')).toBeInTheDocument()
    expect(screen.getByText('昨日计划')).toBeInTheDocument()
    expect(screen.getAllByText('若冲高回落则减仓')).toHaveLength(2)
    expect(screen.getByText('计划任务')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '未完成' })).toBeInTheDocument()
  })

  it('marks holding task as done', async () => {
    renderPage()

    await waitFor(() => {
      expect(screen.getAllByRole('button', { name: '标记完成' })).toHaveLength(2)
    })

    fireEvent.click(screen.getAllByRole('button', { name: '标记完成' })[0])

    await waitFor(() => {
      expect(api.updateHoldingTask).toHaveBeenCalledWith(1, { status: 'done' })
    })
  })

  it('switches holding task filters', async () => {
    renderPage()

    await waitFor(() => {
      expect(screen.getAllByText('若冲高回落则减仓')).toHaveLength(2)
    })

    fireEvent.click(screen.getByRole('button', { name: '已完成' }))

    await waitFor(() => {
      expect(api.listHoldingTasks).toHaveBeenCalledWith(expect.any(String), 'done')
      expect(screen.getByText('冲高兑现半仓')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole('button', { name: '已忽略' }))

    await waitFor(() => {
      expect(api.listHoldingTasks).toHaveBeenCalledWith(expect.any(String), 'ignored')
      expect(screen.getByText('若走弱则离场')).toBeInTheDocument()
    })
  })
})
