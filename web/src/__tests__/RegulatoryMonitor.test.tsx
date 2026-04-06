import { render, screen, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import RegulatoryMonitor from '../pages/RegulatoryMonitor'
import { api } from '../lib/api'
import type { RegulatoryMonitorRecord } from '../lib/types'

vi.mock('../lib/api', () => ({
  api: {
    getRegulatoryMonitor: vi.fn(),
  },
}))

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <RegulatoryMonitor />
      </MemoryRouter>
    </QueryClientProvider>
  )
}

describe('RegulatoryMonitor', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(api.getRegulatoryMonitor).mockResolvedValue([
      {
        id: 1,
        ts_code: '300750.SZ',
        name: '宁德时代',
        regulatory_type: 1,
        risk_level: 3,
        reason: '连续异动，停牌核查',
        publish_date: '2026-04-06',
        source: 'regulatory:suspend',
        risk_score: 88.6,
        detail_json: {
          suspend_api: {
            change_reason: '连续异动停牌核查',
            suspend_date: '20260406',
            resume_date: '20260407',
          },
        },
        monitor_start_date: null,
        monitor_end_date: null,
        alert_type: null,
      },
    ] as RegulatoryMonitorRecord[])
  })

  it('renders regulatory rows and expandable detail', async () => {
    renderPage()

    await waitFor(() => {
      expect(screen.getByText('宁德时代')).toBeInTheDocument()
    })

    expect(screen.getByText('监管原因')).toBeInTheDocument()
    expect(screen.getByText('连续异动，停牌核查')).toBeInTheDocument()
    expect(screen.getAllByText('已监管').length).toBeGreaterThanOrEqual(2)

    screen.getByRole('button', { name: '▶' }).click()

    await waitFor(() => {
      expect(screen.getByText('停牌接口字段（suspend）')).toBeInTheDocument()
    })

    expect(screen.getByText('停牌原因')).toBeInTheDocument()
    expect(screen.getByText('连续异动停牌核查')).toBeInTheDocument()
    expect(screen.getByText('复牌日期')).toBeInTheDocument()
    expect(screen.getByText('20260407')).toBeInTheDocument()
  })
})
