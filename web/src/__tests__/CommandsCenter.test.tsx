import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import CommandsCenter from '../pages/CommandsCenter'
import { api } from '../lib/api'
import { localDateString } from '../lib/date'
import type { CommandIndexPayload } from '../lib/types'

vi.mock('../lib/api', () => ({
  api: {
    getCommandIndex: vi.fn(),
  },
}))

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <CommandsCenter />
      </MemoryRouter>
    </QueryClientProvider>
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  Object.assign(navigator, {
    clipboard: {
      writeText: vi.fn().mockResolvedValue(undefined),
    },
  })
  vi.mocked(api.getCommandIndex).mockResolvedValue({
    generated_by: 'python3 scripts/generate_command_index.py',
    summary: 'summary',
    daily_quickstart: [
      { command: 'make bootstrap', description: '首次安装依赖并启用本地 hooks' },
      { command: 'make check', description: '执行命令索引校验 + 前后端完整检查' },
      { command: 'make today-ingest-health', description: '查看今日采集健康摘要' },
    ],
    sections: [
      {
        title: '开发与页面',
        items: [
          { target: 'dev', command: 'make dev', description: 'run api + web dev servers' },
          { target: 'plan-open', command: 'make plan-open', description: 'open plan workbench in browser' },
        ],
      },
    ],
  } as CommandIndexPayload)
})

describe('CommandsCenter', () => {
  it('renders daily quickstart and grouped sections', async () => {
    renderPage()

    await waitFor(() => {
      expect(screen.getByText('命令中心')).toBeInTheDocument()
    })

    expect(screen.getByText('每日高频')).toBeInTheDocument()
    expect(screen.getByText('Web 入口速查')).toBeInTheDocument()
    expect(screen.getByText('make bootstrap')).toBeInTheDocument()
    expect(screen.getByText('make today-ingest-health')).toBeInTheDocument()
    expect(screen.getByText('开发与页面')).toBeInTheDocument()
    expect(screen.getByText('make dev')).toBeInTheDocument()
    const today = localDateString()
    expect(screen.getByRole('link', { name: /盘后核心诊断/ })).toHaveAttribute('href', `/ingest?date=${today}`)
  })

  it('filters commands by keyword and copies a command', async () => {
    renderPage()

    await waitFor(() => {
      expect(screen.getByText('命令中心')).toBeInTheDocument()
    })

    fireEvent.change(screen.getByLabelText('关键词过滤'), { target: { value: 'plan' } })
    expect(screen.getByText('make plan-open')).toBeInTheDocument()
    expect(screen.queryByText('make bootstrap')).not.toBeInTheDocument()
    expect(screen.queryByText('盘后核心诊断')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '复制' }))

    await waitFor(() => {
      expect(navigator.clipboard.writeText).toHaveBeenCalledWith('make plan-open')
    })
  })

  it('filters web shortcuts by keyword', async () => {
    renderPage()

    await waitFor(() => {
      expect(screen.getByText('Web 入口速查')).toBeInTheDocument()
    })

    fireEvent.change(screen.getByLabelText('关键词过滤'), { target: { value: 'streak' } })
    expect(screen.getByRole('link', { name: /连续失败视图/ })).toBeInTheDocument()
    expect(screen.queryByText('盘后核心诊断')).not.toBeInTheDocument()
  })

  it('collapses and expands grouped sections', async () => {
    renderPage()

    await waitFor(() => {
      expect(screen.getByText('开发与页面')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole('button', { name: '折叠' }))
    expect(screen.queryByText('make dev')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '展开' }))
    expect(screen.getByText('make dev')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '全部折叠' }))
    expect(screen.queryByText('make dev')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '全部展开' }))
    expect(screen.getByText('make dev')).toBeInTheDocument()
  })
})
