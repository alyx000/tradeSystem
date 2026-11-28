import { cloneElement, type ReactElement } from 'react'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it, vi, beforeEach } from 'vitest'

// jsdom 没有容器布局，用固定画布检查 Recharts 实际绘制的折线和端点。
vi.mock('recharts', async (load) => {
  const actual = await load<typeof import('recharts')>()
  return { ...actual, ResponsiveContainer: ({ children }: { children: ReactElement<{ width: number; height: number }> }) =>
    cloneElement(children, { width: 900, height: 280 }) }
})

const getStyleFactors = vi.fn()
vi.mock('../lib/api', () => ({
  api: { getStyleFactors: (...a: unknown[]) => getStyleFactors(...a) },
}))

import StylePremiumTrendPanel from '../components/review/StylePremiumTrendPanel'

describe('StylePremiumTrendPanel', () => {
  beforeEach(() => getStyleFactors.mockReset())

  it('renders title once series loaded', async () => {
    getStyleFactors.mockResolvedValue([
      { date: '2026-05-29', premium_10cm: 1.71, premium_capacity: 2.18, premium_first_open: 8.02 },
      { date: '2026-06-01', premium_10cm: 0.21, premium_capacity: 1.79, premium_first_open: -0.71 },
    ])
    render(<StylePremiumTrendPanel date="2026-06-01" />)
    await waitFor(() => expect(screen.getByText(/各风格赚钱效应/)).toBeInTheDocument())
  })

  it('keeps the July 27 and 28 endpoints on separate paths and reports daily samples', async () => {
    getStyleFactors.mockResolvedValue([
      { date: '2026-07-24', premium_first_open_basis: 'legacy_unsplit', premium_first_open_count: 9, premium_first_open: 8.73 },
      { date: '2026-07-27', premium_first_open_basis: 'legacy_unsplit', premium_first_open_count: 7, premium_first_open: 1.57 },
      { date: '2026-07-28', premium_first_open_basis: 'opened_only', premium_first_open_break_before: true, premium_first_open_count: 1, premium_first_open: 9.92 },
      { date: '2026-07-29', premium_first_open_basis: 'opened_only', premium_first_open_count: 8, premium_first_open: 2.88 },
      { date: '2026-07-31', premium_first_open_basis: 'opened_only', premium_first_open_break_before: true, premium_first_open_count: 1, premium_first_open: 0 },
      { date: '2026-08-03', premium_first_open_basis: 'unverified', premium_first_open_count: null, premium_first_open: 2.55 },
      { date: '2026-08-04', premium_first_open_basis: 'opened_only', premium_first_open_count: 0, premium_first_open: null },
      { date: '2026-08-05', premium_first_open_basis: 'opened_only', premium_first_open_count: null, premium_first_open: 3.14 },
      { date: '2026-08-06', premium_first_open_basis: 'opened_only', premium_first_open_count: 1, premium_first_open: -1.23 },
    ])
    const { container } = render(<StylePremiumTrendPanel date="2026-08-06" />)
    fireEvent.click(await screen.findByText(/逐日口径与样本数/))
    const row = (date: string) => within(screen.getByText(date, { selector: 'td' }).closest('tr')!)
    expect(row('2026-07-27').getByText('7 只')).toBeInTheDocument()
    expect(row('2026-07-27').getByText('+1.57%')).toBeInTheDocument()
    expect(row('2026-07-28').getByText('真实首开')).toBeInTheDocument()
    expect(row('2026-07-28').getByText('1 只')).toBeInTheDocument()
    expect(row('2026-07-28').getByText('+9.92%')).toBeInTheDocument()
    expect(row('2026-07-28').getByText('另起一段')).toBeInTheDocument()
    expect(row('2026-07-31').getByText('另起一段')).toBeInTheDocument()
    expect(row('2026-07-31').getByText('0.00%')).toBeInTheDocument()
    expect(row('2026-08-03').getByText('口径待核')).toBeInTheDocument()
    expect(row('2026-08-03').getByText('+2.55%')).toBeInTheDocument()
    expect(row('2026-08-04').getByText('无样本')).toBeInTheDocument()
    expect(row('2026-08-05').getByText('待核')).toBeInTheDocument()
    expect(row('2026-08-05').getByText('+3.14%')).toBeInTheDocument()
    const curves = [...container.querySelectorAll('.recharts-line-curve')].filter(path => path.getAttribute('d')?.includes('L'))
    expect(curves).toHaveLength(2)
    expect(curves.map(path => path.getAttribute('d')!.match(/L/g)?.length)).toEqual([1, 1])
    expect(curves[0]).toHaveAttribute('stroke-dasharray', '4 3')
    expect(curves[1]).not.toHaveAttribute('stroke-dasharray')
    expect(container.querySelectorAll('.recharts-line-dot')).toHaveLength(6)
  })

  it('breaks the curve across partial samples and distinguishes observed zero from a confirmed empty group', async () => {
    const common = { premium_first_open_basis: 'opened_only', premium_first_open_count: 1 }
    getStyleFactors.mockResolvedValue([
      { ...common, date: '2026-08-03', premium_first_open: 1 },
      { ...common, date: '2026-08-04', premium_first_open_partial: true, premium_first_open_count: 2, premium_first_open: 5.25 },
      { ...common, date: '2026-08-05', premium_first_open: 2 },
      { ...common, date: '2026-08-06', premium_first_open_partial: true, premium_first_open_count: 0, premium_first_open: null },
      { ...common, date: '2026-08-07', premium_first_open_count: 0, premium_first_open: null },
    ])
    const { container } = render(<StylePremiumTrendPanel date="2026-08-07" />)
    fireEvent.click(await screen.findByText(/逐日口径与样本数/))
    const observed = within(screen.getByText('2026-08-04', { selector: 'td' }).closest('tr')!)
    expect(observed.getByText('已核 2 只 / 总样本待核')).toBeInTheDocument()
    expect(observed.getByText('+5.25%')).toBeInTheDocument()
    const partialZero = within(screen.getByText('2026-08-06', { selector: 'td' }).closest('tr')!)
    expect(partialZero.getByText('已核 0 只 / 总样本待核')).toBeInTheDocument()
    expect(partialZero.getByText('待核')).toBeInTheDocument()
    expect(screen.getByText('无样本').closest('tr')).toHaveTextContent('2026-08-07')
    expect(container.querySelectorAll('.recharts-line-dot')).toHaveLength(2)
    const curves = [...container.querySelectorAll('.recharts-line-curve')]
    expect(curves.some(path => path.getAttribute('d')?.includes('L'))).toBe(false)
  })

  it('clears the later window on a date change and recovers from a failed request', async () => {
    getStyleFactors.mockResolvedValueOnce([{ date: '2026-07-28', premium_first_open: 9.92 }])
      .mockRejectedValueOnce(new Error('unavailable')).mockResolvedValueOnce([])
    const { rerender } = render(<StylePremiumTrendPanel date="2026-07-28" />)
    expect(await screen.findByText('+9.92%')).toBeInTheDocument()
    rerender(<StylePremiumTrendPanel date="2026-07-27" />)
    expect(screen.queryByText('+9.92%')).not.toBeInTheDocument()
    expect(await screen.findByText('当期溢价趋势读取失败')).toBeInTheDocument()
    rerender(<StylePremiumTrendPanel date="2026-07-24" />)
    expect(await screen.findByText('暂无各风格赚钱效应趋势数据')).toBeInTheDocument()
  })

  it('shows empty state when no datapoints', async () => {
    getStyleFactors.mockResolvedValue([])
    render(<StylePremiumTrendPanel date="2026-06-01" />)
    await waitFor(() => expect(screen.getByText(/暂无/)).toBeInTheDocument())
  })

  it('requests the six premium metrics over a window ending at date', async () => {
    getStyleFactors.mockResolvedValue([])
    render(<StylePremiumTrendPanel date="2026-06-01" />)
    await waitFor(() => expect(getStyleFactors).toHaveBeenCalled())
    const [metrics, from, to] = getStyleFactors.mock.calls[0]
    expect(metrics).toContain('premium_capacity')
    expect(metrics).toContain('premium_first_open')
    expect(to).toBe('2026-06-01')
    expect(String(from) < String(to)).toBe(true)
  })
})
