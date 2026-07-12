import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { useState } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import ReviewWorkbench from '../pages/ReviewWorkbench'
import { api } from '../lib/api'
import type { ReviewPrefillData, ReviewRecord, TrinityFactorScoreRun } from '../lib/types'

vi.mock('../lib/api', () => ({
  api: {
    getPrefill: vi.fn(),
    getReview: vi.fn(),
    saveReview: vi.fn(),
    reviewToDraft: vi.fn(),
    scoreReviewFactors: vi.fn(),
  },
}))

function makeStepMock(name: string) {
  function StepMock({ data, onChange, factorScore, onFactorScore, factorScoreError }: {
    data: Record<string, unknown>
    onChange: (value: Record<string, unknown>) => void
    factorScore?: { score_run_id?: string }
    onFactorScore?: () => void
    factorScoreError?: string | null
  }) {
    const [localDraft, setLocalDraft] = useState('')
    return (
      <div>
        <div>{name} mock</div>
        <div data-testid={`${name}-data`}>{JSON.stringify(data || {})}</div>
        <button type="button" onClick={() => onChange({ note: `${name}-filled` })}>
          fill-{name}
        </button>
        <button type="button" onClick={() => onChange({ note: `${name}-changed` })}>
          change-{name}
        </button>
        <button type="button" onClick={() => setLocalDraft(`${name}-local`)}>
          local-{name}
        </button>
        <div data-testid={`${name}-local`}>{localDraft}</div>
        {name === 'StepPlan' && (
          <button type="button" onClick={() => onChange({
            factor_decision: {
              score_run_id: factorScore?.score_run_id,
              status: 'accepted',
              primary_factor: 'sector_rhythm',
              supporting_factors: ['leader_signal'],
              input_by: 'web',
            },
            key_factor: 'sector_rhythm',
            secondary_factors: ['leader_signal'],
          })}>
            confirm-factor
          </button>
        )}
        {onFactorScore && (
          <button type="button" onClick={onFactorScore}>run-factor-score</button>
        )}
        {factorScore && <div data-testid={`${name}-factor-score`}>{factorScore.score_run_id}</div>}
        {factorScoreError && <div data-testid={`${name}-factor-error`}>{factorScoreError}</div>}
      </div>
    )
  }
  return {
    default: StepMock,
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
  const view = render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[`/review/${date}`]}>
        <Routes>
          <Route path="/review/:date" element={<ReviewWorkbench />} />
          <Route path="/plans/:date" element={<div>PlanWorkbench mock</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  )
  return { ...view, qc }
}

function scoreResult(scoreRunId = 'factor-run-web'): TrinityFactorScoreRun {
  return {
    score_run_id: scoreRunId,
    trade_date: '2026-04-03',
    status: 'success',
    cache_hit: false,
    is_cacheable: true,
    factor_scores: [],
    sector_scores: [],
    system_recommendation: {
      primary: null,
      supporting: [],
      confidence: null,
      undetermined_reason: 'undetermined_weak',
      recommendation_source: 'llm_program_recompute',
      failure_reason: null,
      sector_scores: [],
      sector_fallback: [],
      notice: 'LLM 相对重要度评分，非胜率',
    },
    rule_gate: {},
    diagnostics: {},
    provider: 'antigravity',
    requested_model: 'model-x',
    prompt_versions: {},
    schema_version: 'v1',
    ruleset_version: 'v1',
  }
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
  vi.mocked(api.reviewToDraft).mockResolvedValue({
    review_date: '2026-04-03',
    trade_date: '2026-04-06',
    draft: { draft_id: 'draft_x', trade_date: '2026-04-06' },
    observation: { observation_id: 'obs_x', source_type: 'review' },
  })
  vi.mocked(api.scoreReviewFactors).mockResolvedValue(scoreResult())
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
    expect(await screen.findByText('StepMarket mock')).toBeInTheDocument()
  })

  it('scores the current unsaved steps 1-6 and keeps result scoped to the date', async () => {
    renderPage()

    fireEvent.click(await screen.findByText('1.大盘'))
    fireEvent.click(await screen.findByRole('button', { name: 'fill-StepMarket' }))
    fireEvent.click(screen.getByRole('button', { name: /8\.计划/ }))
    fireEvent.click(await screen.findByRole('button', { name: 'run-factor-score' }))

    await waitFor(() => {
      expect(api.scoreReviewFactors).toHaveBeenCalledWith('2026-04-03', {
        input_by: 'web',
        steps: expect.objectContaining({
          step1_market: { note: 'StepMarket-filled' },
        }),
      })
    })
    const body = vi.mocked(api.scoreReviewFactors).mock.calls[0][1]
    expect(body.steps).not.toHaveProperty('step8_plan')
    expect(await screen.findByTestId('StepPlan-factor-score')).toHaveTextContent('factor-run-web')

    fireEvent.click(screen.getByRole('button', { name: 'confirm-factor' }))
    expect(screen.getByTestId('StepPlan-data')).toHaveTextContent('factor_decision')

    fireEvent.click(screen.getByText('1.大盘 ✓'))
    fireEvent.click(await screen.findByRole('button', { name: 'change-StepMarket' }))
    fireEvent.click(screen.getByRole('button', { name: /8\.计划/ }))
    expect(screen.queryByTestId('StepPlan-factor-score')).not.toBeInTheDocument()
    expect(screen.getByTestId('StepPlan-factor-error')).toHaveTextContent('评分输入已变化')
    expect(screen.getByTestId('StepPlan-data')).toHaveTextContent('"factor_decision":null')
    expect(screen.getByTestId('StepPlan-data')).toHaveTextContent('"key_factor":""')

    fireEvent.click(screen.getByRole('button', { name: 'local-StepPlan' }))
    expect(screen.getByTestId('StepPlan-local')).toHaveTextContent('StepPlan-local')

    fireEvent.change(screen.getByDisplayValue('2026-04-03'), {
      target: { value: '2026-04-04' },
    })
    await waitFor(() => {
      expect(api.getPrefill).toHaveBeenCalledWith('2026-04-04')
    })
    expect(screen.queryByTestId('StepPlan-factor-score')).not.toBeInTheDocument()
    expect(screen.getByTestId('StepPlan-local')).toBeEmptyDOMElement()
  })

  it('invalidates a visible score when same-day prefill facts change', async () => {
    const { qc } = renderPage()

    vi.mocked(api.scoreReviewFactors)
      .mockResolvedValueOnce(scoreResult('factor-run-r1'))
      .mockResolvedValueOnce(scoreResult('factor-run-r2'))

    fireEvent.click(await screen.findByRole('button', { name: /8\.计划/ }))
    fireEvent.click(await screen.findByRole('button', { name: 'run-factor-score' }))
    expect(await screen.findByTestId('StepPlan-factor-score')).toHaveTextContent('factor-run-r1')
    fireEvent.click(screen.getByRole('button', { name: 'confirm-factor' }))
    expect(screen.getByTestId('StepPlan-data')).toHaveTextContent('factor_decision')

    qc.setQueryData(['prefill', '2026-04-03'], {
      date: '2026-04-03',
      market: null,
      prev_market: null,
      avg_5d_amount: 9999,
      avg_20d_amount: null,
      teacher_notes: [],
      holdings: [],
      calendar_events: [],
      main_themes: [],
    } satisfies ReviewPrefillData)

    await waitFor(() => {
      expect(screen.queryByTestId('StepPlan-factor-score')).not.toBeInTheDocument()
    })
    expect(screen.getByTestId('StepPlan-factor-error')).toHaveTextContent('评分输入已变化')
    expect(screen.getByTestId('StepPlan-data')).toHaveTextContent('"factor_decision":null')
    expect(screen.getByTestId('StepPlan-data')).toHaveTextContent('"key_factor":""')

    fireEvent.click(screen.getByRole('button', { name: 'run-factor-score' }))
    expect(await screen.findByTestId('StepPlan-factor-score')).toHaveTextContent('factor-run-r2')
    expect(screen.getByTestId('StepPlan-data')).toHaveTextContent('"factor_decision":null')

    fireEvent.click(screen.getByRole('button', { name: '保存' }))
    await waitFor(() => {
      expect(api.saveReview).toHaveBeenCalledWith(
        '2026-04-03',
        expect.objectContaining({
          step8_plan: expect.objectContaining({
            factor_decision: null,
            key_factor: '',
            secondary_factors: [],
          }),
        }),
      )
    })
  })

  it('does not recreate a local draft after save succeeds', async () => {
    renderPage()

    fireEvent.click(await screen.findByRole('button', { name: 'fill-StepMarket' }))
    fireEvent.click(screen.getByRole('button', { name: '保存' }))
    await waitFor(() => expect(api.saveReview).toHaveBeenCalled())

    await new Promise(resolve => setTimeout(resolve, 2200))
    expect(localStorage.getItem('review_draft_2026-04-03')).toBeNull()
  }, 5000)

  it('preserves edits made while a save request is pending', async () => {
    let resolveSave: ((value: ReviewRecord) => void) | undefined
    vi.mocked(api.saveReview).mockImplementationOnce(() => new Promise<ReviewRecord>((resolve) => {
      resolveSave = resolve
    }))
    renderPage()

    fireEvent.click(await screen.findByRole('button', { name: 'fill-StepMarket' }))
    fireEvent.click(screen.getByRole('button', { name: '保存' }))
    await waitFor(() => expect(api.saveReview).toHaveBeenCalled())
    fireEvent.click(screen.getByRole('button', { name: 'change-StepMarket' }))
    expect(screen.getByTestId('StepMarket-data')).toHaveTextContent('StepMarket-changed')

    resolveSave?.({ exists: true, ok: true } satisfies ReviewRecord)
    expect(await screen.findByText('保存成功')).toBeInTheDocument()
    await waitFor(() => {
      expect(screen.getByTestId('StepMarket-data')).toHaveTextContent('StepMarket-changed')
    })
  })

  it('shows an actionable message when freshness validation rejects save', async () => {
    vi.mocked(api.saveReview).mockRejectedValueOnce(
      new Error('API 422: score input has changed; rerun scoring before confirmation'),
    )
    renderPage()

    fireEvent.click(await screen.findByRole('button', { name: '保存' }))

    expect(await screen.findByText(/评分证据已变化，请重新运行 LLM 评分后再确认/)).toBeInTheDocument()
  })

  it('keeps a factor-decision tombstone when review data arrives after editing', async () => {
    const resolver: { current: ((value: ReviewRecord) => void) | null } = { current: null }
    vi.mocked(api.getReview).mockImplementation(() => new Promise<ReviewRecord>((resolve) => {
      resolver.current = resolve
    }))
    renderPage()

    fireEvent.click(await screen.findByRole('button', { name: 'change-StepMarket' }))
    resolver.current?.({
      exists: true,
      step8_plan: JSON.stringify({
        factor_decision: {
          score_run_id: 'saved-run',
          status: 'accepted',
          primary_factor: 'sector_rhythm',
          supporting_factors: ['leader_signal'],
          input_by: 'web',
        },
        key_factor: 'sector_rhythm',
        secondary_factors: ['leader_signal'],
      }),
    })

    await waitFor(() => {
      expect(screen.getByText('8.计划 ✓')).toBeInTheDocument()
    })
    fireEvent.click(screen.getByText('8.计划 ✓'))
    expect(screen.getByTestId('StepPlan-data')).toHaveTextContent('"factor_decision":null')
    expect(screen.getByTestId('StepPlan-data')).toHaveTextContent('"key_factor":""')
    expect(screen.getByTestId('StepPlan-data')).toHaveTextContent('"secondary_factors":[]')
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
    expect(await screen.findByText('StepEmotion mock')).toBeInTheDocument()
    expect(screen.getByTestId('StepEmotion-data')).toHaveTextContent('"cycle":"发酵"')
  })

  it('merges local draft with existing saved review instead of fully overriding it', async () => {
    vi.mocked(api.getReview).mockResolvedValue({
      exists: true,
      step1_market: JSON.stringify({ trend: 'up' }),
      step2_sectors: JSON.stringify({ main: 'AI' }),
      step3_emotion: JSON.stringify({ cycle: '发酵' }),
      step4_style: JSON.stringify({ style: '趋势' }),
      step5_leaders: JSON.stringify({ leader: '协创数据' }),
      step6_nodes: JSON.stringify({ node: '分歧点' }),
      step7_positions: JSON.stringify({ stock: '西部材料' }),
      step8_plan: JSON.stringify({ tomorrow: '低吸主线' }),
    })
    localStorage.setItem('review_draft_2026-04-03', JSON.stringify({
      step1_market: { trend: 'draft-only' },
    }))

    renderPage()

    await waitFor(() => {
      expect(screen.getByText('8/8 已填写')).toBeInTheDocument()
    })
    expect(screen.getByTestId('StepMarket-data')).toHaveTextContent('"trend":"draft-only"')
    expect(screen.getByText('2.板块 ✓')).toBeInTheDocument()
    expect(screen.getByText('8.计划 ✓')).toBeInTheDocument()
  })

  it('warns when a local draft may override saved review and can switch back to server data', async () => {
    vi.mocked(api.getReview).mockResolvedValue({
      exists: true,
      step1_market: JSON.stringify({ trend: 'server' }),
    })
    localStorage.setItem('review_draft_2026-04-03', JSON.stringify({
      step1_market: { trend: 'draft' },
    }))

    renderPage()

    expect(await screen.findByText('当前存在本地草稿，可能覆盖服务端版本')).toBeInTheDocument()
    expect(screen.getByTestId('StepMarket-data')).toHaveTextContent('"trend":"draft"')

    fireEvent.click(screen.getByRole('button', { name: '使用服务端版本' }))

    await waitFor(() => {
      expect(screen.getByTestId('StepMarket-data')).toHaveTextContent('"trend":"server"')
    })
    expect(localStorage.getItem('review_draft_2026-04-03')).toBeNull()
  })

  it('preserves nested saved step data when local draft contains an older partial structure', async () => {
    vi.mocked(api.getReview).mockResolvedValue({
      exists: true,
      step5_leaders: JSON.stringify({
        top_leaders: [
          {
            stock: '协创数据',
            sector: '算力租赁',
            attribute_type: '走势引领',
            attribute: '主线最票',
            clarity: '一眼看出',
            position: '主升',
            is_new: true,
            is_prefilled: false,
          },
        ],
        transition: {
          old: '高位AI硬件后排',
          new: '协创数据',
          reason: '切回算力租赁主线',
        },
      }),
    })
    localStorage.setItem('review_draft_2026-04-03', JSON.stringify({
      step5_leaders: {
        emotion_anchor: '旧结构字段',
      },
    }))

    renderPage()

    await waitFor(() => {
      expect(screen.getByText('5.龙头 ✓')).toBeInTheDocument()
    })
    fireEvent.click(screen.getByText('5.龙头 ✓'))
    expect(await screen.findByText('StepLeaders mock')).toBeInTheDocument()
    expect(screen.getByTestId('StepLeaders-data')).toHaveTextContent('"stock":"协创数据"')
    expect(screen.getByTestId('StepLeaders-data')).toHaveTextContent('"old":"高位AI硬件后排"')
    expect(screen.getByTestId('StepLeaders-data')).toHaveTextContent('"emotion_anchor":"旧结构字段"')
  })

  it('replaces prefilled step5 candidates with saved leaders after review data loads', async () => {
    // 用 ref 容器保存 resolve，避免 TS 闭包流分析把 `let` 外部再读到时 narrow 成 never。
    const resolver: { current: ((value: ReviewRecord) => void) | null } = { current: null }
    vi.mocked(api.getReview).mockImplementation(() => new Promise<ReviewRecord>((resolve) => {
      resolver.current = resolve
    }))
    vi.mocked(api.getPrefill).mockResolvedValue({
      date: '2026-04-16',
      market: null,
      prev_market: null,
      avg_5d_amount: null,
      avg_20d_amount: null,
      teacher_notes: [],
      holdings: [],
      calendar_events: [],
      main_themes: [],
      step5_leaders: {
        top_leaders: [
          {
            stock: '品高股份',
            sector: 'IT服务',
            attribute_type: '走势引领',
            attribute: '系统候选',
            clarity: '需要辨别',
            position: '启动',
            is_new: false,
            is_prefilled: true,
          },
        ],
      },
    } as ReviewPrefillData)

    renderPage('2026-04-16')

    fireEvent.click(await screen.findByText('5.龙头'))
    expect(await screen.findByText('StepLeaders mock')).toBeInTheDocument()

    resolver.current?.({
      exists: true,
      step5_leaders: JSON.stringify({
        top_leaders: [
          {
            stock: '协创数据',
            sector: '算力租赁',
            attribute_type: '走势引领',
            attribute: '主线最票',
            clarity: '一眼看出',
            position: '主升',
            is_new: true,
            is_prefilled: false,
          },
        ],
      }),
    } as ReviewRecord)

    await waitFor(() => {
      expect(screen.getByTestId('StepLeaders-data')).toHaveTextContent('协创数据')
    })
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

  it('saves review and generates next-day draft', async () => {
    renderPage()

    await waitFor(() => {
      expect(screen.getByText('StepMarket mock')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole('button', { name: 'fill-StepMarket' }))
    fireEvent.click(screen.getByRole('button', { name: '生成次日计划草稿' }))

    await waitFor(() => {
      expect(api.saveReview).toHaveBeenCalledWith('2026-04-03', {
        step1_market: { note: 'StepMarket-filled' },
      })
      expect(api.reviewToDraft).toHaveBeenCalledWith('2026-04-03', { input_by: 'web' })
    })

    expect(await screen.findByText('PlanWorkbench mock')).toBeInTheDocument()
  })
})
