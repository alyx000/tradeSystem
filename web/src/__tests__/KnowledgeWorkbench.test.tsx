import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import KnowledgeWorkbench from '../pages/KnowledgeWorkbench'
import { api } from '../lib/api'

vi.mock('../lib/api', () => ({
  api: {
    listKnowledgeAssets: vi.fn(),
    createKnowledgeAsset: vi.fn(),
    draftFromAsset: vi.fn(),
  },
}))

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <KnowledgeWorkbench />
      </MemoryRouter>
    </QueryClientProvider>
  )
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('KnowledgeWorkbench', () => {
  it('renders headings and form fields', async () => {
    vi.mocked(api.listKnowledgeAssets).mockResolvedValue([])
    renderPage()
    expect(screen.getByText('资料工作台')).toBeInTheDocument()
    expect(screen.getByText('新建资料')).toBeInTheDocument()
    expect(screen.getByPlaceholderText('资料标题')).toBeInTheDocument()
    expect(screen.getByPlaceholderText(/观点、笔记/)).toBeInTheDocument()
  })

  it('shows 暂无资料 when asset list is empty', async () => {
    vi.mocked(api.listKnowledgeAssets).mockResolvedValue([])
    renderPage()
    await waitFor(() => {
      expect(screen.getByText('暂无资料')).toBeInTheDocument()
    })
  })

  it('shows assets from API when list is non-empty', async () => {
    vi.mocked(api.listKnowledgeAssets).mockResolvedValue([
      {
        asset_id: 'asset_1',
        asset_type: 'teacher_note',
        title: 'AI算力主线分析',
        content: '主线仍在发酵',
        source: '小鲍直播',
        tags: ['AI'],
        created_at: '2026-04-10T10:00:00',
      },
    ])
    renderPage()
    await waitFor(() => {
      expect(screen.getByText('AI算力主线分析')).toBeInTheDocument()
    })
    // 资料列表中出现资产类型标签（可能有多个"老师观点"，如下拉和列表）
    expect(screen.getAllByText('老师观点').length).toBeGreaterThan(0)
    // 列表中有生成草稿按钮
    expect(screen.getAllByText('生成草稿').length).toBeGreaterThan(0)
  })

  it('shows validation error when title is empty and form submitted', async () => {
    vi.mocked(api.listKnowledgeAssets).mockResolvedValue([])
    renderPage()
    fireEvent.click(screen.getByRole('button', { name: '录入资料' }))
    await waitFor(() => {
      expect(screen.getByText('标题不能为空')).toBeInTheDocument()
    })
  })

  it('shows validation error when content is empty', async () => {
    vi.mocked(api.listKnowledgeAssets).mockResolvedValue([])
    renderPage()
    fireEvent.change(screen.getByPlaceholderText('资料标题'), { target: { value: '测试标题' } })
    fireEvent.click(screen.getByRole('button', { name: '录入资料' }))
    await waitFor(() => {
      expect(screen.getByText('正文不能为空')).toBeInTheDocument()
    })
  })

  it('calls createKnowledgeAsset and shows success on valid submission', async () => {
    vi.mocked(api.listKnowledgeAssets).mockResolvedValue([])
    vi.mocked(api.createKnowledgeAsset).mockResolvedValue({
      asset_id: 'asset_new',
      title: '测试资料',
    })
    renderPage()

    fireEvent.change(screen.getByPlaceholderText('资料标题'), {
      target: { value: '测试资料' },
    })
    fireEvent.change(screen.getByPlaceholderText(/观点、笔记/), {
      target: { value: '机器人回流，关注趋势' },
    })
    fireEvent.click(screen.getByRole('button', { name: '录入资料' }))

    await waitFor(() => {
      expect(api.createKnowledgeAsset).toHaveBeenCalledOnce()
    })
    const callArg = vi.mocked(api.createKnowledgeAsset).mock.calls[0][0]
    expect(callArg.title).toBe('测试资料')
    expect(callArg.content).toBe('机器人回流，关注趋势')

    await waitFor(() => {
      expect(screen.getByText('资料已录入')).toBeInTheDocument()
    })
  })

  it('opens draft modal and shows draft_id after generation', async () => {
    vi.mocked(api.listKnowledgeAssets).mockResolvedValue([
      {
        asset_id: 'asset_abc',
        asset_type: 'manual_note',
        title: '机器人观察',
        content: '机器人回流',
        tags: [],
        created_at: '2026-04-10T09:00:00',
      },
    ])
    vi.mocked(api.draftFromAsset).mockResolvedValue({
      observation: { observation_id: 'obs_1', source_type: 'knowledge_asset' },
      draft: { draft_id: 'draft_k_abc', trade_date: '2026-04-10' },
    })

    renderPage()
    await waitFor(() => expect(screen.getAllByText('生成草稿').length).toBeGreaterThan(0))
    // 点击资料列表中的"生成草稿"按钮（在资料卡片内）
    const generateButtons = screen.getAllByText('生成草稿')
    fireEvent.click(generateButtons[generateButtons.length - 1])

    await waitFor(() => {
      expect(screen.getByText('从资料生成草稿')).toBeInTheDocument()
    })

    // 弹窗中的「生成草稿」按钮（弹窗出现后取最后一个，因为列表按钮仍在 DOM 中）
    const allGenBtns = screen.getAllByRole('button', { name: '生成草稿' })
    fireEvent.click(allGenBtns[allGenBtns.length - 1])

    await waitFor(() => {
      expect(screen.getByText('草稿已生成')).toBeInTheDocument()
    })
    expect(screen.getByText('draft_k_abc')).toBeInTheDocument()
    expect(screen.getByText('进入计划工作台')).toBeInTheDocument()
  })
})
