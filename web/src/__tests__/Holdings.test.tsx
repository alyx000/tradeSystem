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
    updateHolding: vi.fn(),
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
      stop_loss: 175,
      target_price: 210,
      position_ratio: 30,
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
        info_signals: {
          investor_qa: [{ question: '公司产能规划如何', answer: '已规划新增100GWh', date: '2026-04-04' }],
          research_reports: [{ institution: '中金', rating: '买入', target_price: 220, date: '2026-04-04' }],
          news: [{ title: '宁德时代发布新一代电池', time: '2026-04-05 10:30' }],
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
  vi.mocked(api.updateHolding).mockResolvedValue({ ok: true })
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
    expect(screen.getByText('止损 / 止盈')).toBeInTheDocument()
    expect(screen.getByText('仓位')).toBeInTheDocument()
    expect(screen.getByText('止损 175')).toBeInTheDocument()
    expect(screen.getByText('止盈 210')).toBeInTheDocument()
    expect(screen.getByText('30%')).toBeInTheDocument()
    expect(screen.getByText('公告 / 披露')).toBeInTheDocument()
    expect(screen.getByText('公告：回购公告（20260405）')).toBeInTheDocument()
    expect(screen.getByText('披露：20260420 · 20260331')).toBeInTheDocument()
    expect(screen.getByText('信息面')).toBeInTheDocument()
    expect(screen.getByText(/互动易（\d+ 条）/)).toBeInTheDocument()
    expect(screen.getByText(/公司产能规划如何/)).toBeInTheDocument()
    expect(screen.getByText(/研报：中金「买入」/)).toBeInTheDocument()
    expect(screen.getByText(/新闻：宁德时代发布新一代电池/)).toBeInTheDocument()
    expect(screen.getByText('昨日计划')).toBeInTheDocument()
    expect(screen.getAllByText('若冲高回落则减仓')).toHaveLength(2)
    expect(screen.getByText('计划任务')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '未完成' })).toBeInTheDocument()
  })

  it('submits stop loss, target price and position ratio when creating holding', async () => {
    vi.mocked(api.createHolding).mockResolvedValue({ id: 2 } as unknown as Holding)
    renderPage()

    fireEvent.click(screen.getByRole('button', { name: '添加持仓' }))
    fireEvent.change(screen.getByPlaceholderText('代码'), { target: { value: '600000' } })
    fireEvent.change(screen.getByPlaceholderText('名称'), { target: { value: '浦发银行' } })
    fireEvent.change(screen.getByPlaceholderText('成本价'), { target: { value: '10.5' } })
    fireEvent.change(screen.getByPlaceholderText('数量'), { target: { value: '1000' } })
    fireEvent.change(screen.getByPlaceholderText('止损价'), { target: { value: '9.8' } })
    fireEvent.change(screen.getByPlaceholderText('止盈价'), { target: { value: '12.0' } })
    fireEvent.change(screen.getByPlaceholderText('仓位占比%'), { target: { value: '25' } })

    fireEvent.click(screen.getByRole('button', { name: '确认' }))

    await waitFor(() => {
      expect(api.createHolding).toHaveBeenCalledWith({
        stock_code: '600000',
        stock_name: '浦发银行',
        entry_price: 10.5,
        shares: 1000,
        sector: undefined,
        stop_loss: 9.8,
        target_price: 12,
        position_ratio: 25,
      })
    })
  })

  it('supports inline editing for stop loss, target price and position ratio', async () => {
    renderPage()

    await waitFor(() => {
      expect(screen.getByRole('button', { name: '编辑' })).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole('button', { name: '编辑' }))

    fireEvent.change(screen.getByLabelText('止损价-300750'), { target: { value: '178.5' } })
    fireEvent.change(screen.getByLabelText('止盈价-300750'), { target: { value: '220' } })
    fireEvent.change(screen.getByLabelText('仓位占比-300750'), { target: { value: '35' } })
    fireEvent.click(screen.getByRole('button', { name: '保存' }))

    await waitFor(() => {
      expect(api.updateHolding).toHaveBeenCalledWith(1, {
        stop_loss: 178.5,
        target_price: 220,
        position_ratio: 35,
      })
    })
  })

  it('does not call API when saving with no field changes', async () => {
    renderPage()

    await waitFor(() => {
      expect(screen.getByRole('button', { name: '编辑' })).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole('button', { name: '编辑' }))
    fireEvent.click(screen.getByRole('button', { name: '保存' }))

    expect(api.updateHolding).not.toHaveBeenCalled()
    await waitFor(() => {
      expect(screen.queryByLabelText('止损价-300750')).not.toBeInTheDocument()
    })
  })

  it('shows validation error and does not call API when numeric fields are invalid', async () => {
    renderPage()

    await waitFor(() => {
      expect(screen.getByRole('button', { name: '编辑' })).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole('button', { name: '编辑' }))
    // number 输入在测试环境下不会保留非法字符串；用负数触发「非负」校验失败
    fireEvent.change(screen.getByLabelText('止损价-300750'), { target: { value: '-1' } })
    fireEvent.click(screen.getByRole('button', { name: '保存' }))

    expect(api.updateHolding).not.toHaveBeenCalled()
    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent(/止损、止盈、仓位/)
    })
    expect(screen.getByLabelText('止损价-300750')).toBeInTheDocument()
  })

  it('only sends note when entry_reason unchanged', async () => {
    vi.mocked(api.getHoldings).mockResolvedValue([
      {
        id: 1,
        stock_code: '300750',
        stock_name: '宁德时代',
        entry_price: 180,
        current_price: 192,
        stop_loss: 175,
        target_price: 210,
        position_ratio: 30,
        shares: 100,
        status: 'active',
        entry_reason: '原原因',
        note: '旧备注',
      },
    ] as Holding[])

    renderPage()

    await waitFor(() => {
      expect(screen.getByRole('button', { name: '编辑' })).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole('button', { name: '编辑' }))
    fireEvent.change(screen.getByLabelText('备注-300750'), { target: { value: '新备注内容' } })
    fireEvent.click(screen.getByRole('button', { name: '保存' }))

    await waitFor(() => {
      expect(api.updateHolding).toHaveBeenCalledWith(1, { note: '新备注内容' })
    })
  })

  it('only sends changed numeric fields when texts are unchanged', async () => {
    vi.mocked(api.getHoldings).mockResolvedValue([
      {
        id: 1,
        stock_code: '300750',
        stock_name: '宁德时代',
        entry_price: 180,
        current_price: 192,
        stop_loss: 175,
        target_price: 210,
        position_ratio: 30,
        shares: 100,
        status: 'active',
        entry_reason: '主线仓',
        note: '跟踪',
      },
    ] as Holding[])

    renderPage()

    await waitFor(() => {
      expect(screen.getByRole('button', { name: '编辑' })).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole('button', { name: '编辑' }))
    fireEvent.change(screen.getByLabelText('止损价-300750'), { target: { value: '170' } })
    fireEvent.click(screen.getByRole('button', { name: '保存' }))

    await waitFor(() => {
      expect(api.updateHolding).toHaveBeenCalledWith(1, { stop_loss: 170 })
    })
  })

  it('submits entry_reason null when clearing reason text only', async () => {
    vi.mocked(api.getHoldings).mockResolvedValue([
      {
        id: 1,
        stock_code: '300750',
        stock_name: '宁德时代',
        entry_price: 180,
        current_price: 192,
        stop_loss: 175,
        target_price: 210,
        position_ratio: 30,
        shares: 100,
        status: 'active',
        entry_reason: '将被清空',
        note: '保留',
      },
    ] as Holding[])

    renderPage()

    await waitFor(() => {
      expect(screen.getByRole('button', { name: '编辑' })).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole('button', { name: '编辑' }))
    fireEvent.change(screen.getByLabelText('买入原因-300750'), { target: { value: '' } })
    fireEvent.click(screen.getByRole('button', { name: '保存' }))

    await waitFor(() => {
      expect(api.updateHolding).toHaveBeenCalledWith(1, { entry_reason: null })
    })
  })

  it('cancels inline editing without saving', async () => {
    renderPage()

    await waitFor(() => {
      expect(screen.getByRole('button', { name: '编辑' })).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole('button', { name: '编辑' }))
    fireEvent.change(screen.getByLabelText('止损价-300750'), { target: { value: '170' } })
    fireEvent.click(screen.getByRole('button', { name: '取消' }))

    expect(api.updateHolding).not.toHaveBeenCalled()
    expect(screen.queryByLabelText('止损价-300750')).not.toBeInTheDocument()
    expect(screen.getByText('止损 175')).toBeInTheDocument()
  })

  it('supports inline editing for entry reason and note', async () => {
    vi.mocked(api.getHoldings).mockResolvedValue([
      {
        id: 1,
        stock_code: '300750',
        stock_name: '宁德时代',
        entry_price: 180,
        current_price: 192,
        stop_loss: 175,
        target_price: 210,
        position_ratio: 30,
        shares: 100,
        status: 'active',
        entry_reason: '旧原因',
        note: '旧备注',
      },
    ] as Holding[])

    renderPage()

    await waitFor(() => {
      expect(screen.getByRole('button', { name: '编辑' })).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole('button', { name: '编辑' }))

    fireEvent.change(screen.getByLabelText('买入原因-300750'), { target: { value: '新主线龙头' } })
    fireEvent.change(screen.getByLabelText('备注-300750'), { target: { value: '减仓观察' } })
    fireEvent.click(screen.getByRole('button', { name: '保存' }))

    await waitFor(() => {
      expect(api.updateHolding).toHaveBeenCalledWith(1, {
        entry_reason: '新主线龙头',
        note: '减仓观察',
      })
    })
  })

  it('submits null when clearing inline edit fields', async () => {
    renderPage()

    await waitFor(() => {
      expect(screen.getByRole('button', { name: '编辑' })).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole('button', { name: '编辑' }))
    fireEvent.change(screen.getByLabelText('止损价-300750'), { target: { value: '' } })
    fireEvent.change(screen.getByLabelText('止盈价-300750'), { target: { value: '' } })
    fireEvent.change(screen.getByLabelText('仓位占比-300750'), { target: { value: '' } })
    fireEvent.click(screen.getByRole('button', { name: '保存' }))

    await waitFor(() => {
      expect(api.updateHolding).toHaveBeenCalledWith(1, {
        stop_loss: null,
        target_price: null,
        position_ratio: null,
      })
    })
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
