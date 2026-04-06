import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import ReviewWorkbench from '../pages/ReviewWorkbench'
import { api } from '../lib/api'
import type { ReviewPrefillData, ReviewRecord } from '../lib/types'

vi.mock('../lib/api', () => ({
  api: {
    getPrefill: vi.fn(),
    getReview: vi.fn(),
    saveReview: vi.fn(),
  },
}))

function makeStepMock(name: string) {
  return {
    default: ({ data, onChange }: { data: Record<string, unknown>; onChange: (value: Record<string, unknown>) => void }) => (
      <div>
        <div>{name} mock</div>
        <div data-testid={`${name}-data`}>{JSON.stringify(data || {})}</div>
        <button type="button" onClick={() => onChange({ note: `${name}-filled` })}>
          fill-{name}
        </button>
      </div>
    ),
  }
}

vi.mock('../components/review/StepMarket', () => makeStepMock('StepMarket'))
vi.mock('../components/review/StepSectors', () => makeStepMock('StepSectors'))
vi.mock('../components/review/StepEmotion', () => makeStepMock('StepEmotion'))
vi.mock('../components/review/StepStyle', () => makeStepMock('StepStyle'))
vi.mock('../components/review/StepLeaders', () => makeStepMock('StepLeaders'))
vi.mock('../components/review/StepNodes', () => makeStepMock('StepNodes'))
vi.mock('../components/review/StepPositions', () => makeStepMock('StepPositions'))
vi.mock('../components/review/StepPlan', () => makeStepMock('StepPlan'))

function renderPage(date = '2026-04-03') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[`/review/${date}`]}>
        <Routes>
          <Route path="/review/:date" element={<ReviewWorkbench />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  vi.mocked(api.getPrefill).mockResolvedValue({
    date: '2026-04-03',
    market: null,
    prev_market: null,
    avg_5d_amount: null,
    avg_20d_amount: null,
    teacher_notes: [],
    holdings: [],
    calendar_events: [],
    main_themes: [],
  } satisfies ReviewPrefillData)
  vi.mocked(api.getReview).mockResolvedValue({ exists: false } satisfies ReviewRecord)
  vi.mocked(api.saveReview).mockResolvedValue({ exists: true, ok: true } satisfies ReviewRecord)
})

describe('ReviewWorkbench', () => {
  it('renders all 8 review steps and the first step content', async () => {
    renderPage()

    await waitFor(() => {
      expect(api.getPrefill).toHaveBeenCalledWith('2026-04-03')
      expect(api.getReview).toHaveBeenCalledWith('2026-04-03')
    })

    expect(screen.getByText('八步复盘')).toBeInTheDocument()
    expect(screen.getByText('0/8 已填写')).toBeInTheDocument()
    expect(screen.getByText('1.大盘')).toBeInTheDocument()
    expect(screen.getByText('2.板块')).toBeInTheDocument()
    expect(screen.getByText('3.情绪')).toBeInTheDocument()
    expect(screen.getByText('4.风格')).toBeInTheDocument()
    expect(screen.getByText('5.龙头')).toBeInTheDocument()
    expect(screen.getByText('6.节点')).toBeInTheDocument()
    expect(screen.getByText('7.持仓')).toBeInTheDocument()
    expect(screen.getByText('8.计划')).toBeInTheDocument()
    expect(screen.getByText('StepMarket mock')).toBeInTheDocument()
  })

  it('loads existing review data, updates filled count, and switches steps', async () => {
    vi.mocked(api.getReview).mockResolvedValue({
      exists: true,
      step1_market: JSON.stringify({ trend: 'up' }),
      step3_emotion: JSON.stringify({ cycle: '发酵' }),
    })

    renderPage()

    await waitFor(() => {
      expect(screen.getByText('2/8 已填写')).toBeInTheDocument()
    })
    expect(screen.getByText('1.大盘 ✓')).toBeInTheDocument()
    expect(screen.getByText('3.情绪 ✓')).toBeInTheDocument()
    expect(screen.getByTestId('StepMarket-data')).toHaveTextContent('"trend":"up"')

    fireEvent.click(screen.getByText('3.情绪 ✓'))
    expect(screen.getByText('StepEmotion mock')).toBeInTheDocument()
    expect(screen.getByTestId('StepEmotion-data')).toHaveTextContent('"cycle":"发酵"')
  })

  it('updates current step data and saves review payload', async () => {
    renderPage()

    await waitFor(() => {
      expect(screen.getByText('StepMarket mock')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole('button', { name: 'fill-StepMarket' }))
    fireEvent.click(screen.getByRole('button', { name: '保存' }))

    await waitFor(() => {
      expect(api.saveReview).toHaveBeenCalledWith('2026-04-03', {
        step1_market: { note: 'StepMarket-filled' },
      })
    })
    expect(screen.getByText('保存成功')).toBeInTheDocument()
  })
})
