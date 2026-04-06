import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Routes, Route, useLocation } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import IngestWorkbench from '../pages/IngestWorkbench'

function LocationProbe() {
  const location = useLocation()
  return <div data-testid="location-probe">{`${location.pathname}${location.search}`}</div>
}

function renderPage(initialEntry = '/ingest') {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[initialEntry]}>
        <Routes>
          <Route path="/ingest" element={<><LocationProbe /><IngestWorkbench /></>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  )
}

describe('IngestWorkbench', () => {
  beforeEach(() => {
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: {
        writeText: vi.fn().mockResolvedValue(undefined),
      },
    })
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
          { interface_name: 'moneyflow_hsgt', provider_method: 'get_northbound', stage: 'post_core', notes: '北向资金核心数据，属于盘后主流程硬依赖。', params_policy: 'trade_date', enabled_by_default: true },
          { interface_name: 'margin', provider_method: 'get_margin_data', stage: 'post_extended', notes: '融资融券数据，属于扩展事实层。', params_policy: 'trade_date', enabled_by_default: true },
        ])
      }
      if (url.includes('/api/ingest/inspect?date=')) {
        if (url.includes('interface=anns_d')) {
          return ok({
            date: '2026-04-05',
            interface_name: 'anns_d',
            run_count: 0,
            error_count: 1,
            runs: [],
            errors: [
              { id: 1, run_id: 'run_2', interface_name: 'anns_d', error_type: 'provider', error_type_label: '数据源失败', error_message: '您的权限不足', retryable: 0, retryable_label: '不可重试', restriction_label: '权限受限', restriction_reason: '当前账号对该接口没有调用权限，或积分不足。', action_hint: '检查 Tushare Token、接口权限或积分；确认当前账号已开通该接口。', stage: 'post_core', stage_label: '盘后核心', interface_label: '全市场公告' },
            ],
          })
        }
        return ok({
          date: '2026-04-05',
          run_count: 1,
          error_count: 1,
          runs: [
            { run_id: 'run_1', interface_name: 'moneyflow_hsgt', provider: 'tushare:moneyflow_hsgt', stage: 'post_core', status: 'success', row_count: 1, notes: 'provider execution complete', started_at: '2026-04-05T20:00:00', finished_at: '2026-04-05T20:00:01', duration_ms: 1000 },
          ],
          errors: [
            { id: 1, run_id: 'run_2', interface_name: 'anns_d', error_type: 'provider', error_type_label: '数据源失败', error_message: '您的权限不足', retryable: 0, retryable_label: '不可重试', restriction_label: '权限受限', restriction_reason: '当前账号对该接口没有调用权限，或积分不足。', action_hint: '检查 Tushare Token、接口权限或积分；确认当前账号已开通该接口。', stage: 'post_core', stage_label: '盘后核心', interface_label: '全市场公告' },
          ],
        })
      }
      if (url.includes('/api/ingest/health?date=')) {
        if (url.includes('stage=post_extended')) {
          return ok({
            start_date: '2026-03-30',
            end_date: '2026-04-05',
            days: 7,
            stage: 'post_extended',
            total_runs: 6,
            total_failures: 1,
            unresolved_failures: 0,
            failed_interface_count: 0,
            never_succeeded_count: 0,
            failure_rate: 0.1667,
            status_label: '稳定',
            status_reason: '近 7 天没有未解决失败，当前阶段采集链路稳定。',
            top_failed_interfaces: [],
            daily_failures: [],
          })
        }
        return ok({
          start_date: '2026-03-30',
          end_date: '2026-04-05',
          days: 7,
          stage: 'post_core',
          total_runs: 18,
          total_failures: 5,
          unresolved_failures: 3,
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
          daily_failures: [
            { biz_date: '2026-04-04', error_count: 1 },
            { biz_date: '2026-04-05', error_count: 4 },
          ],
        })
      }
      if (url.includes('/api/ingest/retry')) {
        if (url.includes('interface=anns_d')) {
          return ok({
            interface_name: 'anns_d',
            retryable_count: 0,
            groups: [],
          })
        }
        return ok({
          retryable_count: 2,
          groups: [{ interface_name: 'daily_basic', biz_date: '2026-04-05', error_count: 2 }],
        })
      }
      if (url.endsWith('/api/ingest/run') && init?.method === 'POST') {
        return ok({ stage: 'post_core', stage_label: '盘后核心', recorded_runs: 1 })
      }
      if (url.endsWith('/api/ingest/run-interface') && init?.method === 'POST') {
        return ok({ name: 'moneyflow_hsgt', run: { interface_name: 'moneyflow_hsgt', interface_label: '北向资金核心数据', status: 'success', status_label: '成功' } })
      }
      if (url.endsWith('/api/ingest/reconcile') && init?.method === 'POST') {
        return ok({ stale_minutes: 5, reconciled_count: 1, runs: [{ run_id: 'run_old_1', interface_name: 'ths_member' }] })
      }
      if (url.endsWith('/api/ingest/retry-run') && init?.method === 'POST') {
        return ok({ requested_groups: 1, attempted_groups: 1, resolved_errors: 1, runs: [{ run_id: 'run_retry_1', interface_name: 'daily_basic', status: 'success' }] })
      }
      return Promise.resolve(new Response('not found', { status: 404 }))
    }) as unknown as typeof fetch
  })

  it('renders ingest dashboard data', async () => {
    renderPage('/ingest?date=2026-04-05&stage=post_extended')
    expect(screen.getByText('采集诊断工作台')).toBeInTheDocument()
    expect((await screen.findAllByText('北向资金核心数据')).length).toBeGreaterThan(0)
    expect(screen.getByTestId('location-probe')).toHaveTextContent('/ingest?date=2026-04-05&stage=post_extended')
    expect(screen.getAllByText('全市场公告').length).toBeGreaterThan(0)
    expect(screen.getByText('盘后核心 (post_core)')).toBeInTheDocument()
    expect(screen.getByText('未解决可重试错误数')).toBeInTheDocument()
    expect(screen.getByText('近 7 天采集健康')).toBeInTheDocument()
    expect(screen.getByText('6')).toBeInTheDocument()
    expect(screen.getByText('1')).toBeInTheDocument()
    expect(screen.getByText('当前视角：盘后扩展')).toBeInTheDocument()
    expect(screen.getByText('失败接口数')).toBeInTheDocument()
    expect(screen.getByText('失败率')).toBeInTheDocument()
    expect(screen.getByText('16.7%')).toBeInTheDocument()
    expect(screen.getByText('从未成功接口')).toBeInTheDocument()
    expect(screen.getByText('稳定')).toBeInTheDocument()
    expect(screen.getByText('近 7 天没有未解决失败，当前阶段采集链路稳定。')).toBeInTheDocument()
    expect(screen.getAllByText('0').length).toBeGreaterThan(0)
    expect(screen.getByText('近 7 天暂无失败接口')).toBeInTheDocument()
    expect(screen.getByText('近 7 天暂无失败记录')).toBeInTheDocument()
    expect(screen.getAllByText('数据源失败').length).toBeGreaterThan(0)
    expect(screen.getAllByText('权限受限').length).toBeGreaterThan(0)
    expect(screen.getAllByText('不可重试').length).toBeGreaterThan(0)

    fireEvent.click(screen.getAllByRole('button', { name: '详情' })[0])
    expect(screen.getAllByText(/Provider 方法：/).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/参数策略：/).length).toBeGreaterThan(0)

    fireEvent.click(screen.getAllByRole('button', { name: '详情' })[2])
    expect(screen.getAllByText(/处理建议：/).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/限制类型：/).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/权限受限/).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/接口权限或积分/).length).toBeGreaterThan(0)

    await waitFor(() => {
      const calls = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls.map(([input]) => String(input))
      expect(calls.some((url) => url.includes('/api/ingest/health?') && url.includes('stage=post_extended'))).toBe(true)
    })

    fireEvent.change(screen.getByLabelText('采集阶段'), { target: { value: 'post_core' } })
    await waitFor(() => {
      expect(screen.getByTestId('location-probe')).toHaveTextContent('/ingest?date=2026-04-05')
      expect(screen.getByTestId('location-probe')).not.toHaveTextContent('stage=')
      expect(screen.getByText('当前视角：盘后核心')).toBeInTheDocument()
      expect(screen.getByText('需处理')).toBeInTheDocument()
      expect(screen.getByText('存在从未成功过的接口，建议优先排查权限、配置或实现缺口。')).toBeInTheDocument()
      expect(screen.getAllByRole('button', { name: /全市场公告/ })[0]).toBeInTheDocument()
      expect(screen.getByText(/连续失败：2 天/)).toBeInTheDocument()
      expect(
        screen.getByText((_, element) => element?.textContent === '连续失败：2 天 · 从未成功')
      ).toBeInTheDocument()
    })

    const rankingButtons = screen.getAllByRole('button', {
      name: /全市场公告|大宗交易/,
    })
    expect(rankingButtons[0]).toHaveTextContent('全市场公告')

    fireEvent.click(screen.getByRole('button', { name: '按连续失败' }))
    await waitFor(() => {
      expect(screen.getByTestId('location-probe')).toHaveTextContent('health_sort=streak')
      const streakButtons = screen.getAllByRole('button', {
        name: /全市场公告|大宗交易/,
      })
      expect(streakButtons[0]).toHaveTextContent('大宗交易')
      expect(screen.getByText(/连续失败：3 天/)).toBeInTheDocument()
      expect(screen.getByText(/距最近成功：8 天/)).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole('button', { name: '复制当前视图链接' }))
    await waitFor(() => {
      expect(navigator.clipboard.writeText).toHaveBeenCalledWith(
        expect.stringContaining('/ingest?date=2026-04-05&health_sort=streak')
      )
      expect(screen.getByRole('button', { name: '已复制' })).toBeInTheDocument()
      expect(screen.getByText(/已复制当前视图链接/)).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole('button', { name: '复制大宗交易排障链接' }))
    await waitFor(() => {
      expect(navigator.clipboard.writeText).toHaveBeenCalledWith(
        expect.stringContaining('/ingest?date=2026-04-05&interface=block_trade&health_sort=streak')
      )
      expect(screen.getByText(/已复制接口排障链接/)).toBeInTheDocument()
    })
    expect(screen.queryByText(/当前仅查看接口/)).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '按失败次数' }))
    await waitFor(() => {
      expect(screen.getByTestId('location-probe')).not.toHaveTextContent('health_sort=')
      const failureButtons = screen.getAllByRole('button', {
        name: /全市场公告|大宗交易/,
      })
      expect(failureButtons[0]).toHaveTextContent('全市场公告')
    })

    fireEvent.click(screen.getAllByRole('button', { name: /全市场公告/ })[0])
    expect(screen.getByText(/当前仅查看接口/)).toBeInTheDocument()
    expect(screen.getByTestId('location-probe')).toHaveTextContent('interface=anns_d')
    await waitFor(() => {
      const calls = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls.map(([input]) => String(input))
      expect(calls.some((url) => url.includes('/api/ingest/inspect?') && url.includes('interface=anns_d'))).toBe(true)
      expect(calls.some((url) => url.includes('/api/ingest/retry') && url.includes('interface=anns_d'))).toBe(true)
      expect(calls.some((url) => url.includes('/api/ingest/inspect?') && url.includes('stage=post_core'))).toBe(true)
      expect(calls.some((url) => url.includes('/api/ingest/retry') && url.includes('stage=post_core'))).toBe(true)
      expect(calls.some((url) => url.includes('/api/ingest/health?') && url.includes('stage=post_core'))).toBe(true)
    })

    fireEvent.click(screen.getByRole('button', { name: '清除筛选' }))
    expect(screen.queryByText(/当前仅查看接口/)).not.toBeInTheDocument()
    expect(screen.getByTestId('location-probe')).not.toHaveTextContent('interface=anns_d')
  })

  it('runs stage and single interface', async () => {
    renderPage()
    await screen.findByText('接口注册表')

    fireEvent.click(screen.getByRole('button', { name: '执行阶段' }))
    await waitFor(() => {
      expect(screen.getByText(/已执行阶段/)).toBeInTheDocument()
    })

    fireEvent.change(screen.getByLabelText('单接口'), { target: { value: 'moneyflow_hsgt' } })
    fireEvent.click(screen.getByRole('button', { name: '执行单接口' }))
    await waitFor(() => {
      expect(screen.getByText(/已执行接口/)).toBeInTheDocument()
    })

    fireEvent.click(screen.getAllByRole('button', { name: '重跑接口' })[0])
    await waitFor(() => {
      expect(screen.getByText(/已重跑接口/)).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole('button', { name: '重跑全部待重试' }))
    await waitFor(() => {
      expect(screen.getByText(/已批量重跑待重试项/)).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole('button', { name: '清理陈旧运行' }))
    await waitFor(() => {
      expect(screen.getByText(/已清理陈旧运行/)).toBeInTheDocument()
    })
  })
})
