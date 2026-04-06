import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import HoldingTasks from '../pages/HoldingTasks'
import { api } from '../lib/api'
import { localDateString } from '../lib/date'
import type { HoldingTaskItem } from '../lib/types'

vi.mock('../lib/api', () => ({
  api: {
    listHoldingTasks: vi.fn(),
    updateHoldingTask: vi.fn(),
  },
}))

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/holding-tasks?date=2026-04-05&status=open']}>
        <HoldingTasks />
      </MemoryRouter>
    </QueryClientProvider>
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(api.listHoldingTasks).mockImplementation(async (_date?: string, status = 'open') => {
    const tasksByStatus: Record<string, HoldingTaskItem[]> = {
      open: [
        {
          id: 1,
          trade_date: '2026-04-05',
          stock_code: '300750.SZ',
          stock_name: '宁德时代',
          action_plan: '若冲高回落则减仓',
          source: 'review_step7',
          status: 'open',
        },
      ],
      done: [
        {
          id: 2,
          trade_date: '2026-04-04',
          stock_code: '002594.SZ',
          stock_name: '比亚迪',
          action_plan: '冲高兑现半仓',
          source: 'review_step7',
          status: 'done',
        },
      ],
      ignored: [
        {
          id: 3,
          trade_date: '2026-04-03',
          stock_code: '000333.SZ',
          stock_name: '美的集团',
          action_plan: '弱于预期则不再跟踪',
          source: 'review_step7',
          status: 'ignored',
        },
      ],
    }
    return tasksByStatus[status] || []
  })
  vi.mocked(api.updateHoldingTask).mockResolvedValue({ ok: true })
})

describe('HoldingTasks', () => {
  it('renders open tasks and supports actions', async () => {
    renderPage()

    await waitFor(() => {
      expect(screen.getByText('若冲高回落则减仓')).toBeInTheDocument()
    })

    expect(screen.getByText('若冲高回落则减仓')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '标记完成' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '忽略' })).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '标记完成' }))

    await waitFor(() => {
      expect(api.updateHoldingTask).toHaveBeenCalledWith(1, { status: 'done' })
    })
  })

  it('switches task status filters', async () => {
    renderPage()

    await waitFor(() => {
      expect(screen.getByText('若冲高回落则减仓')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole('button', { name: '已完成' }))

    await waitFor(() => {
      expect(api.listHoldingTasks).toHaveBeenCalledWith(expect.any(String), 'done')
      expect(screen.getByText('冲高兑现半仓')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole('button', { name: '已忽略' }))

    await waitFor(() => {
      expect(api.listHoldingTasks).toHaveBeenCalledWith(expect.any(String), 'ignored')
      expect(screen.getByText('弱于预期则不再跟踪')).toBeInTheDocument()
    })
  })

  it('supports date filter and reset to today', async () => {
    renderPage()

    await waitFor(() => {
      expect(api.listHoldingTasks).toHaveBeenCalledWith('2026-04-05', 'open')
    })

    fireEvent.change(screen.getByLabelText('计划日期'), { target: { value: '2026-04-03' } })

    await waitFor(() => {
      expect(api.listHoldingTasks).toHaveBeenCalledWith('2026-04-03', 'open')
    })

    fireEvent.click(screen.getByRole('button', { name: '回到今天' }))

    await waitFor(() => {
      expect(api.listHoldingTasks).toHaveBeenCalledWith(localDateString(), 'open')
    })
  })
})
