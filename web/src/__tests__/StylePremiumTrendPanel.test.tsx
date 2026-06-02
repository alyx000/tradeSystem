import { render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi, beforeEach } from 'vitest'

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
