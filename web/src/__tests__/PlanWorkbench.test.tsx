import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import PlanWorkbench from '../pages/PlanWorkbench'
import { api } from '../lib/api'

vi.mock('../lib/api', () => ({
  api: {
    listPlanObservations: vi.fn(),
    updatePlanObservation: vi.fn(),
    listPlanDrafts: vi.fn(),
    listPlans: vi.fn(),
    createPlanDraft: vi.fn(),
    getPlanDraft: vi.fn(),
    updatePlanDraft: vi.fn(),
    confirmPlan: vi.fn(),
    getPlan: vi.fn(),
    updatePlan: vi.fn(),
    getPlanDiagnostics: vi.fn(),
    reviewPlan: vi.fn(),
  },
}))

function renderPage(date = '2026-04-10') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[`/plans/${date}`]}>
        <Routes>
          <Route path="/plans/:date" element={<PlanWorkbench />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  )
}

const mockDraft = {
  draft_id: 'draft_test1',
  trade_date: '2026-04-10',
  title: '2026-04-10 次日计划草稿',
  summary: '市场偏震荡，关注AI板块',
  market_view_json: JSON.stringify({ bias: '震荡' }),
  sector_view_json: JSON.stringify({ main_themes: ['AI算力'] }),
  stock_focus_json: JSON.stringify([]),
  watch_items_json: JSON.stringify([
    {
      subject_code: '300750.SZ',
      subject_name: '宁德时代',
      reason: '回流观察',
      fact_checks: [],
      judgement_checks: [{ label: '主线确认' }],
    },
  ]),
  fact_check_candidates_json: JSON.stringify([{ check_type: 'ret_1d_gte', label: '单日涨幅不低于2%', params: { value: 2 } }]),
  judgement_check_candidates_json: JSON.stringify([{ label: '主线确认', notes: '需盘中确认' }]),
  status: 'ready_for_confirm',
}

const mockPlan = {
  plan_id: 'plan_test1',
  trade_date: '2026-04-10',
  title: '2026-04-10 交易计划',
  market_bias: '震荡',
  status: 'confirmed',
  watch_items_json: JSON.stringify([]),
}

const mockDiagnostics = {
  plan_id: 'plan_test1',
  trade_date: '2026-04-10',
  watch_item_count: 2,
  fact_check_count: 3,
  judgement_check_count: 1,
  data_ready_count: 1,
  missing_data_count: 1,
  unsupported_check_count: 1,
  summary_json: { ready_items: 1, missing_data_items: 1, unsupported_checks: 1 },
  items_json: [
    {
      subject_code: '300750.SZ',
      subject_name: '宁德时代',
      data_ready: false,
      fact_check_results: [
        { check_type: 'northbound_net_positive', label: '北向净买入为正', result: 'missing_data', evidence_json: {} },
        { check_type: 'margin_balance_change_positive', label: '融资余额变化为正', result: 'pass', evidence_json: { total_rzrqye_yi: 12000 } },
        { check_type: 'some_unsupported', label: '某不支持检查', result: 'unsupported', evidence_json: {} },
      ],
      judgement_checks: [{ label: '主线确认' }],
      missing_dependencies: ['北向净买入为正'],
      unsupported_checks: ['某不支持检查'],
    },
  ],
  generated_at: '2026-04-10T20:00:00',
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(api.listPlanObservations).mockResolvedValue([])
  vi.mocked(api.updatePlanObservation).mockResolvedValue({
    observation_id: 'obs_1',
    title: '已修改观察',
    source_type: 'manual',
    judgements_json: JSON.stringify(['情绪偏分歧']),
  })
  vi.mocked(api.listPlanDrafts).mockResolvedValue([])
  vi.mocked(api.listPlans).mockResolvedValue([])
  vi.mocked(api.updatePlanDraft).mockResolvedValue(mockDraft)
  vi.mocked(api.updatePlan).mockResolvedValue(mockPlan)
})

describe('PlanWorkbench', () => {
  it('renders 草稿 step heading and form on initial load', () => {
    renderPage()
    expect(screen.getByText('计划工作台')).toBeInTheDocument()
    expect(screen.getByText('填写观察输入，生成草稿')).toBeInTheDocument()
  })

  it('shows all 4 step indicators', () => {
    renderPage()
    expect(screen.getByText('草稿')).toBeInTheDocument()
    expect(screen.getByText('确认计划')).toBeInTheDocument()
    expect(screen.getByText('诊断')).toBeInTheDocument()
    expect(screen.getByText('复盘')).toBeInTheDocument()
  })

  it('shows draft detail after draft creation', async () => {
    vi.mocked(api.createPlanDraft).mockResolvedValue(mockDraft)
    vi.mocked(api.updatePlanDraft).mockResolvedValue(mockDraft)
    renderPage()

    fireEvent.click(screen.getByText('生成草稿'))
    await waitFor(() => {
      expect(screen.getByText('草稿详情')).toBeInTheDocument()
    })
    expect(screen.getByText('2026-04-10 次日计划草稿')).toBeInTheDocument()
    expect(screen.getByText('draft_test1')).toBeInTheDocument()
  })

  it('shows recent draft and plan cards from list endpoints', async () => {
    vi.mocked(api.listPlanObservations).mockResolvedValue([
      { observation_id: 'obs_1', title: '盘后观察', source_type: 'manual' },
    ])
    vi.mocked(api.listPlanDrafts).mockResolvedValue([mockDraft])
    vi.mocked(api.listPlans).mockResolvedValue([mockPlan])

    renderPage()

    await waitFor(() => {
      expect(api.listPlanObservations).toHaveBeenCalled()
      expect(api.listPlanDrafts).toHaveBeenCalled()
      expect(api.listPlans).toHaveBeenCalled()
    })
    await waitFor(() => {
      expect(screen.getByText('盘后观察')).toBeInTheDocument()
    })
    expect(screen.getByText('2026-04-10 次日计划草稿')).toBeInTheDocument()
    expect(screen.getByText('2026-04-10 交易计划')).toBeInTheDocument()
  })

  it('saves edited observation title and judgements', async () => {
    vi.mocked(api.listPlanObservations).mockResolvedValue([
      {
        observation_id: 'obs_1',
        title: '盘后观察',
        source_type: 'manual',
        judgements_json: JSON.stringify(['原始判断']),
      },
    ])

    renderPage()

    await waitFor(() => {
      expect(screen.getByText('盘后观察')).toBeInTheDocument()
    })
    fireEvent.click(screen.getByText('盘后观察'))
    await waitFor(() => {
      expect(screen.getByText('编辑 observation')).toBeInTheDocument()
    })

    fireEvent.change(screen.getByDisplayValue('盘后观察'), {
      target: { value: '已修改观察' },
    })
    fireEvent.change(screen.getByDisplayValue('原始判断'), {
      target: { value: '情绪偏分歧' },
    })
    fireEvent.click(screen.getByRole('button', { name: '保存 observation' }))

    await waitFor(() => {
      expect(api.updatePlanObservation).toHaveBeenCalledWith('obs_1', expect.objectContaining({
        title: '已修改观察',
        judgements: ['情绪偏分歧'],
      }))
    })
  })

  it('shows diagnostics with missing_data label in orange context', async () => {
    vi.mocked(api.createPlanDraft).mockResolvedValue(mockDraft)
    vi.mocked(api.updatePlanDraft).mockResolvedValue(mockDraft)
    vi.mocked(api.confirmPlan).mockResolvedValue(mockPlan)
    vi.mocked(api.updatePlan).mockResolvedValue(mockPlan)
    vi.mocked(api.getPlanDiagnostics).mockResolvedValue(mockDiagnostics)

    renderPage()

    fireEvent.click(screen.getByText('生成草稿'))
    await waitFor(() => expect(screen.getByText('确认计划')).toBeInTheDocument())

    fireEvent.click(screen.getByRole('button', { name: '确认计划' }))
    await waitFor(() => {
      expect(screen.getByText('诊断面板')).toBeInTheDocument()
    })

    await waitFor(() => {
      expect(screen.getAllByText('数据缺失').length).toBeGreaterThan(0)
    })
    expect(screen.getAllByText('暂不支持').length).toBeGreaterThan(0)
    expect(screen.getByText('通过')).toBeInTheDocument()
  })

  it('shows judgement_checks labeled as 需人工判断, not pass/fail', async () => {
    vi.mocked(api.createPlanDraft).mockResolvedValue(mockDraft)
    vi.mocked(api.updatePlanDraft).mockResolvedValue(mockDraft)
    vi.mocked(api.confirmPlan).mockResolvedValue(mockPlan)
    vi.mocked(api.updatePlan).mockResolvedValue(mockPlan)
    vi.mocked(api.getPlanDiagnostics).mockResolvedValue(mockDiagnostics)

    renderPage()

    fireEvent.click(screen.getByText('生成草稿'))
    await waitFor(() => expect(screen.getByRole('button', { name: '确认计划' })).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: '确认计划' }))

    await waitFor(() => {
      expect(screen.getByText('需人工判断')).toBeInTheDocument()
    })
    expect(screen.getByText('主线确认')).toBeInTheDocument()
    expect(screen.getAllByText('需人工判断').length).toBeGreaterThan(0)
  })

  it('submits review and shows success message', async () => {
    vi.mocked(api.createPlanDraft).mockResolvedValue(mockDraft)
    vi.mocked(api.updatePlanDraft).mockResolvedValue(mockDraft)
    vi.mocked(api.confirmPlan).mockResolvedValue(mockPlan)
    vi.mocked(api.updatePlan).mockResolvedValue(mockPlan)
    vi.mocked(api.getPlanDiagnostics).mockResolvedValue(mockDiagnostics)
    vi.mocked(api.reviewPlan).mockResolvedValue({
      review_id: 'plan_review_x',
      plan_id: 'plan_test1',
    })

    renderPage()

    fireEvent.click(screen.getByText('生成草稿'))
    await waitFor(() => expect(screen.getByRole('button', { name: '确认计划' })).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: '确认计划' }))

    await waitFor(() => expect(screen.getByText('进入复盘 →')).toBeInTheDocument())
    fireEvent.click(screen.getByText('进入复盘 →'))

    // "复盘" 在步骤指示器和标题中都存在，只需确认页面有"提交复盘"按钮
    await waitFor(() => expect(screen.getByRole('button', { name: '提交复盘' })).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: '提交复盘' }))

    await waitFor(() => {
      expect(screen.getByText(/复盘已写入/)).toBeInTheDocument()
    })
  })

  it('saves edited draft summary', async () => {
    vi.mocked(api.createPlanDraft).mockResolvedValue(mockDraft)
    vi.mocked(api.updatePlanDraft).mockResolvedValue({
      ...mockDraft,
      summary: '更新后的草稿摘要',
    })

    renderPage()

    fireEvent.click(screen.getByText('生成草稿'))
    await waitFor(() => expect(screen.getByText('草稿详情')).toBeInTheDocument())

    fireEvent.change(screen.getByDisplayValue('市场偏震荡，关注AI板块'), {
      target: { value: '更新后的草稿摘要' },
    })
    fireEvent.click(screen.getByRole('button', { name: '保存草稿' }))

    await waitFor(() => {
      expect(api.updatePlanDraft).toHaveBeenCalledWith('draft_test1', expect.objectContaining({
        summary: '更新后的草稿摘要',
      }))
    })
  })

  it('saves edited draft watch items and candidate checks', async () => {
    vi.mocked(api.createPlanDraft).mockResolvedValue(mockDraft)
    vi.mocked(api.updatePlanDraft).mockResolvedValue({
      ...mockDraft,
      watch_items_json: JSON.stringify([
        {
          subject_code: '002594.SZ',
          subject_name: '比亚迪',
          reason: '趋势观察',
        },
      ]),
    })

    renderPage()

    fireEvent.click(screen.getByText('生成草稿'))
    await waitFor(() => expect(screen.getByText('草稿详情')).toBeInTheDocument())

    fireEvent.change(screen.getByLabelText('Draft Watch Items JSON'), {
      target: {
        value: JSON.stringify([
          {
            subject_code: '002594.SZ',
            subject_name: '比亚迪',
            reason: '趋势观察',
          },
        ], null, 2),
      },
    })
    fireEvent.change(screen.getByLabelText('Fact Check Candidates JSON'), {
      target: {
        value: JSON.stringify([
          { check_type: 'ret_5d_gte', label: '五日涨幅不低于5%', params: { value: 5 } },
        ], null, 2),
      },
    })
    fireEvent.change(screen.getByLabelText('Judgement Check Candidates JSON'), {
      target: {
        value: JSON.stringify([
          { label: '是否具备带动性', notes: '需结合承接判断' },
        ], null, 2),
      },
    })
    fireEvent.click(screen.getByRole('button', { name: '保存草稿' }))

    await waitFor(() => {
      expect(api.updatePlanDraft).toHaveBeenCalledWith('draft_test1', expect.objectContaining({
        watch_items: [
          expect.objectContaining({
            subject_code: '002594.SZ',
            subject_name: '比亚迪',
          }),
        ],
        fact_check_candidates: [
          expect.objectContaining({
            check_type: 'ret_5d_gte',
          }),
        ],
        judgement_check_candidates: [
          expect.objectContaining({
            label: '是否具备带动性',
          }),
        ],
      }))
    })
  })

  it('edits draft candidates from structured editor', async () => {
    vi.mocked(api.createPlanDraft).mockResolvedValue(mockDraft)
    vi.mocked(api.updatePlanDraft).mockResolvedValue(mockDraft)

    renderPage()

    fireEvent.click(screen.getByText('生成草稿'))
    await waitFor(() => expect(screen.getByText('草稿详情')).toBeInTheDocument())

    fireEvent.click(screen.getByRole('button', { name: '新增观察项' }))
    fireEvent.change(screen.getByLabelText('draft-watch-item-code-1'), {
      target: { value: '002594.SZ' },
    })
    fireEvent.change(screen.getByLabelText('draft-watch-item-name-1'), {
      target: { value: '比亚迪' },
    })
    fireEvent.change(screen.getByLabelText('draft-watch-item-reason-1'), {
      target: { value: '趋势观察' },
    })

    fireEvent.click(screen.getByRole('button', { name: '新增候选项' }))
    fireEvent.change(screen.getByLabelText('draft-fact-check-type-1'), {
      target: { value: 'ret_5d_gte' },
    })
    fireEvent.change(screen.getByLabelText('draft-fact-check-label-1'), {
      target: { value: '五日涨幅不低于5%' },
    })
    fireEvent.change(screen.getByLabelText('draft-fact-check-1-value'), {
      target: { value: '5' },
    })

    fireEvent.click(screen.getByRole('button', { name: '新增候选判断' }))
    fireEvent.change(screen.getByLabelText('draft-judgement-check-template-1'), {
      target: { value: '是否具备带动性' },
    })
    fireEvent.change(screen.getByLabelText('draft-judgement-check-notes-1'), {
      target: { value: '需结合承接判断' },
    })

    fireEvent.click(screen.getByRole('button', { name: '保存草稿' }))

    await waitFor(() => {
      expect(api.updatePlanDraft).toHaveBeenCalledWith('draft_test1', expect.objectContaining({
        watch_items: expect.arrayContaining([
          expect.objectContaining({
            subject_code: '002594.SZ',
            subject_name: '比亚迪',
          }),
        ]),
        fact_check_candidates: expect.arrayContaining([
          expect.objectContaining({
            check_type: 'ret_5d_gte',
            label: '五日涨幅不低于5%',
            params: expect.objectContaining({ value: 5 }),
          }),
        ]),
        judgement_check_candidates: expect.arrayContaining([
          expect.objectContaining({
            label: '是否具备带动性',
            notes: '需结合承接判断',
          }),
        ]),
      }))
    })
  })

  it('saves edited plan title and market bias', async () => {
    vi.mocked(api.createPlanDraft).mockResolvedValue(mockDraft)
    vi.mocked(api.updatePlanDraft).mockResolvedValue(mockDraft)
    vi.mocked(api.confirmPlan).mockResolvedValue(mockPlan)
    vi.mocked(api.updatePlan).mockResolvedValue({
      ...mockPlan,
      title: '更新后的正式计划',
      market_bias: '分歧',
    })
    vi.mocked(api.getPlanDiagnostics).mockResolvedValue(mockDiagnostics)

    renderPage()

    fireEvent.click(screen.getByText('生成草稿'))
    await waitFor(() => expect(screen.getByRole('button', { name: '确认计划' })).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: '确认计划' }))

    await waitFor(() => expect(screen.getByRole('button', { name: '保存计划' })).toBeInTheDocument())
    fireEvent.change(screen.getByDisplayValue('2026-04-10 交易计划'), {
      target: { value: '更新后的正式计划' },
    })
    fireEvent.change(screen.getByDisplayValue('震荡'), {
      target: { value: '分歧' },
    })
    fireEvent.click(screen.getByRole('button', { name: '保存计划' }))

    await waitFor(() => {
      expect(api.updatePlan).toHaveBeenCalledWith('plan_test1', expect.objectContaining({
        title: '更新后的正式计划',
        market_bias: '分歧',
      }))
    })
  })

  it('saves edited watch_items json in plan editor', async () => {
    vi.mocked(api.createPlanDraft).mockResolvedValue(mockDraft)
    vi.mocked(api.updatePlanDraft).mockResolvedValue(mockDraft)
    vi.mocked(api.confirmPlan).mockResolvedValue({
      ...mockPlan,
      watch_items_json: JSON.stringify([
        {
          subject_code: '300750.SZ',
          subject_name: '宁德时代',
          reason: '回流观察',
          fact_checks: [],
          judgement_checks: [],
        },
      ]),
    })
    vi.mocked(api.updatePlan).mockResolvedValue({
      ...mockPlan,
      watch_items_json: JSON.stringify([
        {
          subject_code: '300750.SZ',
          subject_name: '宁德时代',
          reason: '更新后的观察',
          fact_checks: [{ check_type: 'ret_1d_gte', label: '单日涨幅不低于2%', params: { value: 2 } }],
          judgement_checks: [],
        },
      ]),
    })
    vi.mocked(api.getPlanDiagnostics).mockResolvedValue(mockDiagnostics)

    renderPage()

    fireEvent.click(screen.getByText('生成草稿'))
    await waitFor(() => expect(screen.getByRole('button', { name: '确认计划' })).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: '确认计划' }))

    await waitFor(() => expect(screen.getByLabelText('Watch Items JSON')).toBeInTheDocument())
    fireEvent.change(screen.getByLabelText('Watch Items JSON'), {
      target: {
        value: JSON.stringify([
          {
            subject_code: '300750.SZ',
            subject_name: '宁德时代',
            reason: '更新后的观察',
            fact_checks: [{ check_type: 'ret_1d_gte', label: '单日涨幅不低于2%', params: { value: 2 } }],
            judgement_checks: [],
          },
        ], null, 2),
      },
    })
    fireEvent.click(screen.getByRole('button', { name: '保存计划' }))

    await waitFor(() => {
      expect(api.updatePlan).toHaveBeenCalledWith('plan_test1', expect.objectContaining({
        watch_items: [
          expect.objectContaining({
            reason: '更新后的观察',
          }),
        ],
      }))
    })
  })

  it('saves edited watch item reason from structured editor', async () => {
    vi.mocked(api.createPlanDraft).mockResolvedValue(mockDraft)
    vi.mocked(api.updatePlanDraft).mockResolvedValue(mockDraft)
    vi.mocked(api.confirmPlan).mockResolvedValue({
      ...mockPlan,
      watch_items_json: JSON.stringify([
        {
          subject_code: '300750.SZ',
          subject_name: '宁德时代',
          reason: '回流观察',
          fact_checks: [],
          judgement_checks: [],
        },
      ]),
    })
    vi.mocked(api.updatePlan).mockResolvedValue(mockPlan)
    vi.mocked(api.getPlanDiagnostics).mockResolvedValue(mockDiagnostics)

    renderPage()

    fireEvent.click(screen.getByText('生成草稿'))
    await waitFor(() => expect(screen.getByRole('button', { name: '确认计划' })).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: '确认计划' }))

    await waitFor(() => expect(screen.getByLabelText('watch-item-reason-0')).toBeInTheDocument())
    fireEvent.change(screen.getByLabelText('watch-item-reason-0'), {
      target: { value: '更新后的结构化理由' },
    })
    fireEvent.click(screen.getByRole('button', { name: '保存计划' }))

    await waitFor(() => {
      expect(api.updatePlan).toHaveBeenCalledWith('plan_test1', expect.objectContaining({
        watch_items: [
          expect.objectContaining({
            reason: '更新后的结构化理由',
          }),
        ],
      }))
    })
  })

  it('adds a watch item from structured editor', async () => {
    vi.mocked(api.createPlanDraft).mockResolvedValue(mockDraft)
    vi.mocked(api.updatePlanDraft).mockResolvedValue(mockDraft)
    vi.mocked(api.confirmPlan).mockResolvedValue({
      ...mockPlan,
      watch_items_json: JSON.stringify([]),
    })
    vi.mocked(api.updatePlan).mockResolvedValue(mockPlan)
    vi.mocked(api.getPlanDiagnostics).mockResolvedValue(mockDiagnostics)

    renderPage()

    fireEvent.click(screen.getByText('生成草稿'))
    await waitFor(() => expect(screen.getByRole('button', { name: '确认计划' })).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: '确认计划' }))

    await waitFor(() => expect(screen.getByRole('button', { name: '新增条目' })).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: '新增条目' }))
    fireEvent.change(screen.getByLabelText('watch-item-code-0'), {
      target: { value: '002594.SZ' },
    })
    fireEvent.change(screen.getByLabelText('watch-item-name-0'), {
      target: { value: '比亚迪' },
    })
    fireEvent.change(screen.getByLabelText('watch-item-reason-0'), {
      target: { value: '趋势观察' },
    })
    fireEvent.click(screen.getByRole('button', { name: '保存计划' }))

    await waitFor(() => {
      expect(api.updatePlan).toHaveBeenCalledWith('plan_test1', expect.objectContaining({
        watch_items: [
          expect.objectContaining({
            subject_code: '002594.SZ',
            subject_name: '比亚迪',
            reason: '趋势观察',
          }),
        ],
      }))
    })
  })

  it('reorders watch items and updates priority', async () => {
    vi.mocked(api.createPlanDraft).mockResolvedValue(mockDraft)
    vi.mocked(api.updatePlanDraft).mockResolvedValue(mockDraft)
    vi.mocked(api.confirmPlan).mockResolvedValue({
      ...mockPlan,
      watch_items_json: JSON.stringify([
        {
          subject_code: '300750.SZ',
          subject_name: '宁德时代',
          reason: '回流观察',
          priority: 1,
          fact_checks: [],
          judgement_checks: [],
        },
        {
          subject_code: '002594.SZ',
          subject_name: '比亚迪',
          reason: '趋势观察',
          priority: 2,
          fact_checks: [],
          judgement_checks: [],
        },
      ]),
    })
    vi.mocked(api.updatePlan).mockResolvedValue(mockPlan)
    vi.mocked(api.getPlanDiagnostics).mockResolvedValue(mockDiagnostics)

    renderPage()

    fireEvent.click(screen.getByText('生成草稿'))
    await waitFor(() => expect(screen.getByRole('button', { name: '确认计划' })).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: '确认计划' }))

    await waitFor(() => expect(screen.getByLabelText('watch-item-move-up-1')).toBeInTheDocument())
    fireEvent.click(screen.getByLabelText('watch-item-move-up-1'))
    fireEvent.change(screen.getByLabelText('watch-item-priority-0'), {
      target: { value: '5' },
    })
    fireEvent.click(screen.getByRole('button', { name: '保存计划' }))

    await waitFor(() => {
      expect(api.updatePlan).toHaveBeenCalledWith('plan_test1', expect.objectContaining({
        watch_items: [
          expect.objectContaining({
            subject_code: '002594.SZ',
            priority: 5,
          }),
          expect.objectContaining({
            subject_code: '300750.SZ',
          }),
        ],
      }))
    })
  })

  it('adds and edits a fact check from structured editor', async () => {
    vi.mocked(api.createPlanDraft).mockResolvedValue(mockDraft)
    vi.mocked(api.updatePlanDraft).mockResolvedValue(mockDraft)
    vi.mocked(api.confirmPlan).mockResolvedValue({
      ...mockPlan,
      watch_items_json: JSON.stringify([
        {
          subject_code: '300750.SZ',
          subject_name: '宁德时代',
          reason: '回流观察',
          fact_checks: [],
          judgement_checks: [],
        },
      ]),
    })
    vi.mocked(api.updatePlan).mockResolvedValue(mockPlan)
    vi.mocked(api.getPlanDiagnostics).mockResolvedValue(mockDiagnostics)

    renderPage()

    fireEvent.click(screen.getByText('生成草稿'))
    await waitFor(() => expect(screen.getByRole('button', { name: '确认计划' })).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: '确认计划' }))

    await waitFor(() => expect(screen.getByRole('button', { name: '新增检查项' })).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: '新增检查项' }))
    fireEvent.change(screen.getByLabelText('fact-check-type-0-0'), {
      target: { value: 'ret_1d_gte' },
    })
    fireEvent.change(screen.getByLabelText('fact-check-label-0-0'), {
      target: { value: '单日涨幅不低于2%' },
    })
    fireEvent.change(screen.getByLabelText('fact-check-0-0-value'), {
      target: { value: '2' },
    })
    fireEvent.click(screen.getByRole('button', { name: '保存计划' }))

    await waitFor(() => {
      expect(api.updatePlan).toHaveBeenCalledWith('plan_test1', expect.objectContaining({
        watch_items: [
          expect.objectContaining({
            fact_checks: [
              expect.objectContaining({
                check_type: 'ret_1d_gte',
                label: '单日涨幅不低于2%',
                params: expect.objectContaining({ value: 2 }),
              }),
            ],
          }),
        ],
      }))
    })
  })

  it('reorders fact checks and updates fact check priority', async () => {
    vi.mocked(api.createPlanDraft).mockResolvedValue(mockDraft)
    vi.mocked(api.updatePlanDraft).mockResolvedValue(mockDraft)
    vi.mocked(api.confirmPlan).mockResolvedValue({
      ...mockPlan,
      watch_items_json: JSON.stringify([
        {
          subject_code: '300750.SZ',
          subject_name: '宁德时代',
          reason: '回流观察',
          fact_checks: [
            { check_type: 'ret_1d_gte', label: '单日涨幅', params: { ts_code: '300750.SZ', value: 2 }, priority: 1 },
            { check_type: 'ret_5d_gte', label: '五日涨幅', params: { ts_code: '300750.SZ', value: 5 }, priority: 2 },
          ],
          judgement_checks: [],
        },
      ]),
    })
    vi.mocked(api.updatePlan).mockResolvedValue(mockPlan)
    vi.mocked(api.getPlanDiagnostics).mockResolvedValue(mockDiagnostics)

    renderPage()

    fireEvent.click(screen.getByText('生成草稿'))
    await waitFor(() => expect(screen.getByRole('button', { name: '确认计划' })).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: '确认计划' }))

    await waitFor(() => expect(screen.getByLabelText('fact-check-move-up-0-1')).toBeInTheDocument())
    fireEvent.click(screen.getByLabelText('fact-check-move-up-0-1'))
    fireEvent.change(screen.getByLabelText('fact-check-priority-0-0'), {
      target: { value: '9' },
    })
    fireEvent.click(screen.getByRole('button', { name: '保存计划' }))

    await waitFor(() => {
      expect(api.updatePlan).toHaveBeenCalledWith('plan_test1', expect.objectContaining({
        watch_items: [
          expect.objectContaining({
            fact_checks: [
              expect.objectContaining({
                check_type: 'ret_5d_gte',
                priority: 9,
              }),
              expect.objectContaining({
                check_type: 'ret_1d_gte',
              }),
            ],
          }),
        ],
      }))
    })
  })

  it('adds and edits a judgement check from structured editor', async () => {
    vi.mocked(api.createPlanDraft).mockResolvedValue(mockDraft)
    vi.mocked(api.updatePlanDraft).mockResolvedValue(mockDraft)
    vi.mocked(api.confirmPlan).mockResolvedValue({
      ...mockPlan,
      watch_items_json: JSON.stringify([
        {
          subject_code: '300750.SZ',
          subject_name: '宁德时代',
          reason: '回流观察',
          fact_checks: [],
          judgement_checks: [],
        },
      ]),
    })
    vi.mocked(api.updatePlan).mockResolvedValue(mockPlan)
    vi.mocked(api.getPlanDiagnostics).mockResolvedValue(mockDiagnostics)

    renderPage()

    fireEvent.click(screen.getByText('生成草稿'))
    await waitFor(() => expect(screen.getByRole('button', { name: '确认计划' })).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: '确认计划' }))

    await waitFor(() => expect(screen.getByRole('button', { name: '新增判断项' })).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: '新增判断项' }))
    fireEvent.change(screen.getByLabelText('judgement-check-template-0-0'), {
      target: { value: '是否具备带动性' },
    })
    fireEvent.change(screen.getByLabelText('judgement-check-notes-0-0'), {
      target: { value: '结合板块反馈人工确认' },
    })
    fireEvent.click(screen.getByRole('button', { name: '保存计划' }))

    await waitFor(() => {
      expect(api.updatePlan).toHaveBeenCalledWith('plan_test1', expect.objectContaining({
        watch_items: [
          expect.objectContaining({
            judgement_checks: [
              expect.objectContaining({
                label: '是否具备带动性',
                notes: '结合板块反馈人工确认',
              }),
            ],
          }),
        ],
      }))
    })
  })

  it('shows type-specific fact check params for sector checks', async () => {
    vi.mocked(api.createPlanDraft).mockResolvedValue(mockDraft)
    vi.mocked(api.updatePlanDraft).mockResolvedValue(mockDraft)
    vi.mocked(api.confirmPlan).mockResolvedValue({
      ...mockPlan,
      watch_items_json: JSON.stringify([
        {
          subject_code: '300750.SZ',
          subject_name: '宁德时代',
          reason: '回流观察',
          fact_checks: [],
          judgement_checks: [],
        },
      ]),
    })
    vi.mocked(api.updatePlan).mockResolvedValue(mockPlan)
    vi.mocked(api.getPlanDiagnostics).mockResolvedValue(mockDiagnostics)

    renderPage()

    fireEvent.click(screen.getByText('生成草稿'))
    await waitFor(() => expect(screen.getByRole('button', { name: '确认计划' })).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: '确认计划' }))

    await waitFor(() => expect(screen.getByRole('button', { name: '新增检查项' })).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: '新增检查项' }))
    fireEvent.change(screen.getByLabelText('fact-check-type-0-0'), {
      target: { value: 'sector_limit_up_count_gte' },
    })
    fireEvent.change(screen.getByLabelText('fact-check-0-0-sector_name'), {
      target: { value: '机器人' },
    })
    fireEvent.change(screen.getByLabelText('fact-check-0-0-value'), {
      target: { value: '3' },
    })
    fireEvent.click(screen.getByRole('button', { name: '保存计划' }))

    await waitFor(() => {
      expect(api.updatePlan).toHaveBeenCalledWith('plan_test1', expect.objectContaining({
        watch_items: [
          expect.objectContaining({
            fact_checks: [
              expect.objectContaining({
                check_type: 'sector_limit_up_count_gte',
                params: expect.objectContaining({
                  sector_name: '机器人',
                  value: 3,
                }),
              }),
            ],
          }),
        ],
      }))
    })
  })

  it('adds trigger and invalidation conditions from structured editor', async () => {
    vi.mocked(api.createPlanDraft).mockResolvedValue(mockDraft)
    vi.mocked(api.updatePlanDraft).mockResolvedValue(mockDraft)
    vi.mocked(api.confirmPlan).mockResolvedValue({
      ...mockPlan,
      watch_items_json: JSON.stringify([
        {
          subject_code: '300750.SZ',
          subject_name: '宁德时代',
          reason: '回流观察',
          fact_checks: [],
          judgement_checks: [],
          trigger_conditions: [],
          invalidations: [],
        },
      ]),
    })
    vi.mocked(api.updatePlan).mockResolvedValue(mockPlan)
    vi.mocked(api.getPlanDiagnostics).mockResolvedValue(mockDiagnostics)

    renderPage()

    fireEvent.click(screen.getByText('生成草稿'))
    await waitFor(() => expect(screen.getByRole('button', { name: '确认计划' })).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: '确认计划' }))

    await waitFor(() => expect(screen.getByRole('button', { name: '新增触发条件' })).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: '新增触发条件' }))
    fireEvent.change(screen.getByLabelText('trigger-condition-0-0'), {
      target: { value: '站稳20日线后放量' },
    })
    fireEvent.click(screen.getByRole('button', { name: '新增失效条件' }))
    fireEvent.change(screen.getByLabelText('invalidation-0-0'), {
      target: { value: '跌破昨日低点' },
    })
    fireEvent.click(screen.getByRole('button', { name: '保存计划' }))

    await waitFor(() => {
      expect(api.updatePlan).toHaveBeenCalledWith('plan_test1', expect.objectContaining({
        watch_items: [
          expect.objectContaining({
            trigger_conditions: ['站稳20日线后放量'],
            invalidations: ['跌破昨日低点'],
          }),
        ],
      }))
    })
  })

  it('shows validation error when watch_items json is invalid', async () => {
    vi.mocked(api.createPlanDraft).mockResolvedValue(mockDraft)
    vi.mocked(api.updatePlanDraft).mockResolvedValue(mockDraft)
    vi.mocked(api.confirmPlan).mockResolvedValue(mockPlan)
    vi.mocked(api.getPlanDiagnostics).mockResolvedValue(mockDiagnostics)

    renderPage()

    fireEvent.click(screen.getByText('生成草稿'))
    await waitFor(() => expect(screen.getByRole('button', { name: '确认计划' })).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: '确认计划' }))

    await waitFor(() => expect(screen.getByLabelText('Watch Items JSON')).toBeInTheDocument())
    fireEvent.change(screen.getByLabelText('Watch Items JSON'), {
      target: { value: '{invalid json' },
    })
    fireEvent.click(screen.getByRole('button', { name: '保存计划' }))

    await waitFor(() => {
      expect(screen.getByText('watch_items JSON 格式无效')).toBeInTheDocument()
    })
    expect(api.updatePlan).not.toHaveBeenCalled()
  })
})
