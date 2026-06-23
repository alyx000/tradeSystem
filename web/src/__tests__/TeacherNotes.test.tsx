import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import TeacherNotes from '../pages/TeacherNotes'
import { api } from '../lib/api'
import type { TeacherNote } from '../lib/types'

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <TeacherNotes />
    </QueryClientProvider>
  )
}

const noteWithRaw: TeacherNote = {
  id: 42,
  teacher_id: 1,
  teacher_name: '小鲍',
  date: '2026-05-02',
  title: '有全文的笔记',
  core_view: '结构化核心观点',
  tags: null,
  sectors: null,
  // 列表端点已剔除 raw_content，仅给轻量布尔标记
  has_raw_content: true,
  created_at: '2026-05-02 09:00:00',
  attachments: [],
}

describe('TeacherNotes 原始全文懒加载', () => {
  beforeEach(() => {
    vi.spyOn(api, 'getTeachers').mockResolvedValue([])
    vi.spyOn(api, 'getNotes').mockResolvedValue([noteWithRaw])
    vi.spyOn(api, 'getNote').mockResolvedValue({
      ...noteWithRaw,
      raw_content: '这是按需拉取的原始全文',
    })
  })

  afterEach(() => vi.restoreAllMocks())

  it('初始加载不携带 raw_content，也不调用详情端点', async () => {
    renderPage()
    await screen.findByText('有全文的笔记')
    // 入口存在（has_raw_content 为 true）
    expect(screen.getByText('原始观点全文')).toBeInTheDocument()
    // 但全文未拉取
    expect(api.getNote).not.toHaveBeenCalled()
    expect(screen.queryByText('这是按需拉取的原始全文')).not.toBeInTheDocument()
  })

  it('展开「原始观点全文」时才按需 GET 详情拉全文', async () => {
    renderPage()
    await screen.findByText('有全文的笔记')

    const rawDetails = screen.getByText('原始观点全文').closest('details') as HTMLDetailsElement
    rawDetails.open = true
    fireEvent(rawDetails, new Event('toggle', { bubbles: true }))

    await waitFor(() => expect(api.getNote).toHaveBeenCalledWith(42))
    expect(await screen.findByText('这是按需拉取的原始全文')).toBeInTheDocument()
  })

  it('展开后详情请求失败时显示错误与重试，而非空内容', async () => {
    vi.spyOn(api, 'getNote').mockRejectedValue(new Error('API 500'))
    renderPage()
    await screen.findByText('有全文的笔记')

    const rawDetails = screen.getByText('原始观点全文').closest('details') as HTMLDetailsElement
    rawDetails.open = true
    fireEvent(rawDetails, new Event('toggle', { bubbles: true }))

    expect(await screen.findByText('重试')).toBeInTheDocument()
    expect(screen.getByText(/全文加载失败/)).toBeInTheDocument()
  })
})
