import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import IngestWorkbench from '../pages/IngestWorkbench'

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/ingest']}>
        <Routes>
          <Route path="/ingest" element={<IngestWorkbench />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  )
}

describe('IngestWorkbench', () => {
  beforeEach(() => {
    globalThis.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const ok = (body: unknown) =>
        Promise.resolve(
          new Response(JSON.stringify(body), {
            status: 200,
            headers: { 'Content-Type': 'application/json' },
          })
        )

      if (url.endsWith('/api/ingest/interfaces')) {
        return ok([
          { interface_name: 'moneyflow_hsgt', provider_method: 'get_northbound', stage: 'post_core' },
          { interface_name: 'margin', provider_method: 'get_margin_data', stage: 'post_extended' },
        ])
      }
      if (url.includes('/api/ingest/inspect?date=')) {
        return ok({
          date: '2026-04-05',
          run_count: 1,
          error_count: 1,
          runs: [
            { run_id: 'run_1', interface_name: 'moneyflow_hsgt', provider: 'tushare:moneyflow_hsgt', stage: 'post_core', status: 'success', row_count: 1 },
          ],
          errors: [
            { id: 1, run_id: 'run_2', interface_name: 'daily_basic', error_type: 'provider', error_message: '未实现' },
          ],
        })
      }
      if (url.endsWith('/api/ingest/retry')) {
        return ok({
          retryable_count: 2,
          groups: [{ interface_name: 'daily_basic', biz_date: '2026-04-05', error_count: 2 }],
        })
      }
      if (url.endsWith('/api/ingest/run') && init?.method === 'POST') {
        return ok({ stage: 'post_core', recorded_runs: 1 })
      }
      if (url.endsWith('/api/ingest/run-interface') && init?.method === 'POST') {
        return ok({ name: 'moneyflow_hsgt', run: { status: 'success' } })
      }
      return Promise.resolve(new Response('not found', { status: 404 }))
    }) as any
  })

  it('renders ingest dashboard data', async () => {
    renderPage()
    expect(screen.getByText('采集诊断工作台')).toBeInTheDocument()
    expect((await screen.findAllByText('moneyflow_hsgt')).length).toBeGreaterThan(0)
    expect(screen.getByText('未解决可重试错误数')).toBeInTheDocument()
    expect(screen.getAllByText('daily_basic').length).toBeGreaterThan(0)
  })

  it('runs stage and single interface', async () => {
    renderPage()
    await screen.findByText('接口注册表')

    fireEvent.click(screen.getByRole('button', { name: '执行 Stage' }))
    await waitFor(() => {
      expect(screen.getByText(/已执行 stage/)).toBeInTheDocument()
    })

    fireEvent.change(screen.getByLabelText('单接口'), { target: { value: 'moneyflow_hsgt' } })
    fireEvent.click(screen.getByRole('button', { name: '执行单接口' }))
    await waitFor(() => {
      expect(screen.getByText(/已执行接口/)).toBeInTheDocument()
    })
  })
})
