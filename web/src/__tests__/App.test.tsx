import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeAll } from 'vitest'
import App from '../App'

function localDateString(date = new Date()): string {
  const y = date.getFullYear()
  const m = String(date.getMonth() + 1).padStart(2, '0')
  const d = String(date.getDate()).padStart(2, '0')
  return `${y}-${m}-${d}`
}

beforeAll(() => {
  globalThis.fetch = vi.fn().mockResolvedValue({
    ok: true,
    headers: new Headers({ 'content-type': 'application/json' }),
    json: () => Promise.resolve([]),
    text: () => Promise.resolve(''),
  })
})

describe('App', () => {
  it('renders navigation', () => {
    render(<App />)
    expect(screen.getByText('交易复盘系统')).toBeInTheDocument()
    expect(screen.getByText('仪表盘')).toBeInTheDocument()
    expect(screen.getByText('市场')).toBeInTheDocument()
    expect(screen.getByText('复盘')).toBeInTheDocument()
    expect(screen.getByText('计划')).toBeInTheDocument()
    expect(screen.getByText('资料')).toBeInTheDocument()
    expect(screen.getByText('采集')).toBeInTheDocument()
    expect(screen.getByText('查询')).toBeInTheDocument()
    expect(screen.getByText('命令')).toBeInTheDocument()
    expect(screen.getByText('老师观点')).toBeInTheDocument()
    expect(screen.getByText('持仓')).toBeInTheDocument()
    expect(screen.getByText('关注池')).toBeInTheDocument()
    expect(screen.getByText('日历')).toBeInTheDocument()
  })

  it('renders at least 10 nav links', () => {
    render(<App />)
    const links = screen.getAllByRole('link')
    expect(links.length).toBeGreaterThanOrEqual(10)
    const today = localDateString()
    expect(screen.getByRole('link', { name: '市场' })).toHaveAttribute('href', `/market/${today}`)
    expect(screen.getByRole('link', { name: '复盘' })).toHaveAttribute('href', `/review/${today}`)
    expect(screen.getByRole('link', { name: '计划' })).toHaveAttribute('href', `/plans/${today}`)
  })

  it('navigates when clicking top nav links', async () => {
    window.history.pushState({}, '', '/')
    render(<App />)

    fireEvent.click(screen.getByRole('link', { name: '命令' }))

    await waitFor(() => {
      expect(window.location.pathname).toBe('/commands')
    })
  })
})
