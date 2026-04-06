import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import KnowledgeWorkbench from '../pages/KnowledgeWorkbench'
import { api } from '../lib/api'

vi.mock('../lib/api', () => ({
  api: {
    listKnowledgeAssets: vi.fn(),
    getNotes: vi.fn(),
    getTeachers: vi.fn(),
    createKnowledgeAsset: vi.fn(),
    createTeacherNote: vi.fn(),
    draftFromAsset: vi.fn(),
    draftFromTeacherNote: vi.fn(),
    deleteNote: vi.fn(),
    deleteKnowledgeAsset: vi.fn(),
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
  vi.mocked(api.getNotes).mockResolvedValue([])
  vi.mocked(api.listKnowledgeAssets).mockResolvedValue([])
  vi.mocked(api.getTeachers).mockResolvedValue([])
})

describe('KnowledgeWorkbench', () => {
  it('renders headings and form fields', async () => {
    renderPage()
    expect(screen.getByText('资料工作台')).toBeInTheDocument()
    expect(screen.getByText('新建资料')).toBeInTheDocument()
    expect(screen.getByPlaceholderText('资料标题')).toBeInTheDocument()
    expect(screen.getByPlaceholderText(/观点、笔记/)).toBeInTheDocument()
  })

  it('shows 暂无资料 when both lists are empty', async () => {
    renderPage()
    await waitFor(() => {
      expect(screen.getByText('暂无资料')).toBeInTheDocument()
    })
  })

  it('shows knowledge asset from API when list is non-empty', async () => {
    vi.mocked(api.listKnowledgeAssets).mockResolvedValue([
      {
        asset_id: 'asset_1',
        asset_type: 'manual_note',
        title: '手动资料一条',
        content: '正文',
        source: '测试',
        tags: ['标签'],
        created_at: '2026-04-10T10:00:00',
      },
    ])
    renderPage()
    await waitFor(() => {
      expect(screen.getByText('手动资料一条')).toBeInTheDocument()
    })
    const badges = screen.getAllByText('手动笔记')
    expect(badges.length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText('生成草稿').length).toBeGreaterThan(0)
  })

  it('shows teacher note from getNotes merged list', async () => {
    vi.mocked(api.getNotes).mockResolvedValue([
      {
        id: 42,
        teacher_id: 1,
        teacher_name: '小鲍',
        date: '2026-04-10',
        title: 'AI算力主线分析',
        core_view: '主线仍在发酵',
        tags: null,
        sectors: null,
        created_at: '2026-04-10T10:00:00',
      },
    ])
    renderPage()
    await waitFor(() => {
      expect(screen.getByText('AI算力主线分析')).toBeInTheDocument()
    })
    expect(screen.getByText('小鲍')).toBeInTheDocument()
    expect(screen.getAllByText('老师观点').length).toBeGreaterThan(0)
  })

  it('shows validation error when title is empty and form submitted', async () => {
    renderPage()
    fireEvent.click(screen.getByRole('button', { name: '录入资料' }))
    await waitFor(() => {
      expect(screen.getByText('标题不能为空')).toBeInTheDocument()
    })
  })

  it('shows validation error when content is empty', async () => {
    renderPage()
    fireEvent.change(screen.getByPlaceholderText('资料标题'), { target: { value: '测试标题' } })
    fireEvent.click(screen.getByRole('button', { name: '录入资料' }))
    await waitFor(() => {
      expect(screen.getByText('正文不能为空')).toBeInTheDocument()
    })
  })

  it('calls createKnowledgeAsset and shows success on valid submission', async () => {
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
      expect(screen.getByText('已录入')).toBeInTheDocument()
    })
  })

  it('requires teacher name when type is 老师观点', async () => {
    renderPage()
    const typeSelect = screen.getByLabelText('新建资料类型')
    fireEvent.change(typeSelect, { target: { value: 'teacher_note' } })

    fireEvent.change(screen.getByPlaceholderText('资料标题'), {
      target: { value: '标题' },
    })
    fireEvent.change(screen.getByPlaceholderText(/观点、笔记/), {
      target: { value: '正文内容' },
    })
    fireEvent.click(screen.getByRole('button', { name: '录入资料' }))

    await waitFor(() => {
      expect(screen.getByText('老师姓名不能为空')).toBeInTheDocument()
    })
  })

  it('calls createTeacherNote when type is 老师观点 and teacher filled', async () => {
    vi.mocked(api.createTeacherNote).mockResolvedValue({ id: 99 })
    renderPage()
    const typeSelect = screen.getByLabelText('新建资料类型')
    fireEvent.change(typeSelect, { target: { value: 'teacher_note' } })

    fireEvent.change(screen.getByPlaceholderText('如：小鲍'), {
      target: { value: '小鲍' },
    })
    fireEvent.change(screen.getByPlaceholderText('资料标题'), {
      target: { value: '测试笔记' },
    })
    fireEvent.change(screen.getByPlaceholderText(/观点、笔记/), {
      target: { value: '笔记正文' },
    })
    fireEvent.click(screen.getByRole('button', { name: '录入资料' }))

    await waitFor(() => {
      expect(api.createTeacherNote).toHaveBeenCalledOnce()
    })
    const arg = vi.mocked(api.createTeacherNote).mock.calls[0][0]
    expect(arg.teacher_name).toBe('小鲍')
    expect(arg.title).toBe('测试笔记')
    expect(arg.raw_content).toBe('笔记正文')
  })

  it('opens draft modal and shows draft_id after generation for asset', async () => {
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
    const generateButtons = screen.getAllByText('生成草稿')
    fireEvent.click(generateButtons[generateButtons.length - 1])

    await waitFor(() => {
      expect(screen.getByText('从资料生成草稿')).toBeInTheDocument()
    })

    const allGenBtns = screen.getAllByRole('button', { name: '生成草稿' })
    fireEvent.click(allGenBtns[allGenBtns.length - 1])

    await waitFor(() => {
      expect(screen.getByText('草稿已生成')).toBeInTheDocument()
    })
    expect(screen.getByText('draft_k_abc')).toBeInTheDocument()
    expect(screen.getByText('进入计划工作台')).toBeInTheDocument()
  })

  it('opens teacher draft modal and calls draftFromTeacherNote', async () => {
    vi.mocked(api.getNotes).mockResolvedValue([
      {
        id: 7,
        teacher_id: 1,
        teacher_name: '小张',
        date: '2026-04-09',
        title: '情绪周期',
        core_view: null,
        tags: null,
        sectors: null,
        created_at: '2026-04-09T12:00:00',
      },
    ])
    vi.mocked(api.draftFromTeacherNote).mockResolvedValue({
      observation: { observation_id: 'obs_t', source_type: 'teacher_note' },
      draft: { draft_id: 'draft_tn', trade_date: '2026-04-12' },
      teacher_note: {},
    })

    renderPage()
    await waitFor(() => expect(screen.getByText('情绪周期')).toBeInTheDocument())
    const genBtns = screen.getAllByText('生成草稿')
    fireEvent.click(genBtns[0])

    await waitFor(() => {
      expect(screen.getByText('从老师笔记生成草稿')).toBeInTheDocument()
    })

    const modalGen = screen.getAllByRole('button', { name: '生成草稿' })
    fireEvent.click(modalGen[modalGen.length - 1])

    await waitFor(() => {
      expect(api.draftFromTeacherNote).toHaveBeenCalledWith(7, expect.objectContaining({ input_by: 'web' }))
    })
  })
})
