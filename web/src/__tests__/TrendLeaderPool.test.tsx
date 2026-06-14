import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import TrendLeaderPool from '../pages/TrendLeaderPool'
import { api } from '../lib/api'
import type { TrendLeaderRow } from '../lib/types'

vi.mock('../lib/api', () => ({
  api: { getTrendLeaders: vi.fn() },
}))

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <TrendLeaderPool />
      </MemoryRouter>
    </QueryClientProvider>
  )
}

const ROWS: TrendLeaderRow[] = [
  {
    code: '600552', name: '凯盛科技', sw_l2: '玻璃玻纤',
    first_limit_date: '2026-06-09', entered_date: '2026-06-09', last_seen_date: '2026-06-14',
    days_in_pool: 6, status: 'active', exit_date: null, exit_reason: null,
    entry_trigger: '涨停', branch_concepts: ['CPO'],
    signal_hits: { shrink_pullback_buy: true, near_ma5: false, overheat: false },
  },
  {
    code: '605358', name: '立昂微', sw_l2: '半导体',
    first_limit_date: '2026-06-14', entered_date: '2026-06-14', last_seen_date: '2026-06-14',
    days_in_pool: 1, status: 'active', exit_date: null, exit_reason: null,
    entry_trigger: '双创15%加速', branch_concepts: [],
    signal_hits: null, // Pass1 未维护
  },
  {
    code: '000999', name: '某退池股', sw_l2: '软件开发',
    first_limit_date: '2026-06-08', entered_date: '2026-06-08', last_seen_date: '2026-06-11',
    days_in_pool: 3, status: 'exited', exit_date: '2026-06-11', exit_reason: '收盘跌破MA10',
    entry_trigger: '涨停', branch_concepts: [],
    signal_hits: null,
  },
]

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(api.getTrendLeaders).mockResolvedValue(ROWS)
})

describe('TrendLeaderPool', () => {
  it('展示红线提示 + [判断] 标记', async () => {
    renderPage()
    expect(await screen.findByText(/不构成买卖建议、不含价位/)).toBeTruthy()
    expect(screen.getByText('[判断]')).toBeTruthy()
  })

  it('在池 Tab 默认展示 active 行、命中信号 chip 与概念分支标注', async () => {
    renderPage()
    expect(await screen.findByText('凯盛科技')).toBeTruthy()
    // 命中信号 chip
    expect(screen.getByText('缩量阴线回踩')).toBeTruthy()
    // sw_l2·分支 标注
    expect(screen.getByText('玻璃玻纤·分支:CPO')).toBeTruthy()
    // 触发 badge
    expect(screen.getByText('涨停')).toBeTruthy()
  })

  it('Pass1 未维护行显示「待维护」而非 chip', async () => {
    renderPage()
    expect(await screen.findByText('立昂微')).toBeTruthy()
    expect(screen.getByText('待维护')).toBeTruthy()
  })

  it('Tab 计数正确，切到历史退池展示退出原因', async () => {
    renderPage()
    expect(await screen.findByText('在池 (2)')).toBeTruthy()
    expect(screen.getByText('历史退池 (1)')).toBeTruthy()
    // 默认在池 Tab 不显示退池股
    expect(screen.queryByText('某退池股')).toBeNull()
    fireEvent.click(screen.getByText('历史退池 (1)'))
    await waitFor(() => expect(screen.getByText('某退池股')).toBeTruthy())
    expect(screen.getByText('收盘跌破MA10')).toBeTruthy()
  })

  it('空池展示空态', async () => {
    vi.mocked(api.getTrendLeaders).mockResolvedValue([])
    renderPage()
    expect(await screen.findByText('观察池为空')).toBeTruthy()
  })
})
