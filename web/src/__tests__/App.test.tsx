import { render, screen } from '@testing-library/react'
import { describe, it, expect, vi, beforeAll } from 'vitest'
import App from '../App'

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
    expect(screen.getByText('持仓任务')).toBeInTheDocument()
    expect(screen.getByText('关注池')).toBeInTheDocument()
    expect(screen.getByText('日历')).toBeInTheDocument()
  })

  it('renders at least 10 nav links', () => {
    render(<App />)
    const links = screen.getAllByRole('link')
    expect(links.length).toBeGreaterThanOrEqual(10)
  })
})
