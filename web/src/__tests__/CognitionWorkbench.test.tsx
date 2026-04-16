import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import CognitionWorkbench from '../pages/CognitionWorkbench'
import * as apiModule from '../lib/api'

vi.mock('../lib/api', async () => {
  const actual = await vi.importActual<typeof import('../lib/api')>('../lib/api')
  return {
    ...actual,
    listCognitions: vi.fn(),
    listInstances: vi.fn(),
    listReviews: vi.fn(),
    getCognitionById: vi.fn(),
    getCognitionReview: vi.fn(),
  }
})

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <CognitionWorkbench />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

const sampleCognition: apiModule.CognitionItem = {
  cognition_id: 'cog_test_001',
  category: 'sentiment',
  sub_category: 'topN',
  title: '大盘情绪衰退信号',
  description: '连板高度不断降低意味着合力减弱',
  pattern: '连板数 < 3 且 前排亏钱',
  time_horizon: '1-2 days',
  action_template: '降仓',
  position_template: '≤30%',
  conditions_json: ['高度<3', '前排亏钱'],
  exceptions_json: [],
  invalidation_conditions_json: ['次日出现5板核按钮'],
  evidence_level: 'B',
  conflict_group: 'sentiment.topN',
  first_source_note_id: 42,
  first_observed_date: '2026-04-01',
  version: 1,
  supersedes: null,
  instance_count: 5,
  validated_count: 3,
  invalidated_count: 1,
  confidence: 0.62,
  status: 'active',
  tags: ['情绪', '顶部'],
  created_at: '2026-04-01T10:00:00',
  updated_at: '2026-04-10T10:00:00',
}

const sampleInstance: apiModule.InstanceItem = {
  instance_id: 'inst_test_001',
  cognition_id: 'cog_test_001',
  observed_date: '2026-04-15',
  source_type: 'teacher_note',
  source_note_id: 100,
  teacher_id: 7,
  teacher_name_snapshot: '张老师',
  context_summary: '当日高度回落',
  regime_tags_json: { regime: 'retreat' },
  time_horizon: '1 day',
  action_bias: '降仓',
  position_cap: '30%',
  avoid_action: '追高',
  market_regime: 'retreat',
  cross_market_anchor: null,
  consensus_key: null,
  parameters_json: {},
  teacher_original_text: '今日竞价要降仓',
  outcome: 'validated',
  outcome_detail: '次日指数跳水',
  outcome_fact_source: 'ingest:market_daily',
  outcome_fact_refs_json: [],
  outcome_date: '2026-04-16',
  lesson: '警惕高度回落后的连续下跌',
  created_at: '2026-04-15T10:00:00',
}

const sampleReview: apiModule.ReviewItem = {
  review_id: 'rev_test_001',
  period_type: 'weekly',
  review_scope: 'all',
  regime_label: 'volatile',
  period_start: '2026-04-07',
  period_end: '2026-04-11',
  active_cognitions_json: ['cog_test_001', 'cog_test_002'],
  validation_stats_json: { validated: 3, invalidated: 1 },
  teacher_participation_json: { '张老师': 2 },
  key_lessons_json: ['情绪衰退后不要摸底'],
  user_reflection: '本周仓位控制偏紧，错过部分反弹',
  action_items_json: ['下周关注龙头修复'],
  status: 'confirmed',
  generated_at: '2026-04-12T20:00:00',
  confirmed_at: '2026-04-12T21:00:00',
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(apiModule.listCognitions).mockResolvedValue({
    total: 1,
    cognitions: [sampleCognition],
  })
  vi.mocked(apiModule.listInstances).mockResolvedValue({
    total: 1,
    instances: [sampleInstance],
  })
  vi.mocked(apiModule.listReviews).mockResolvedValue({
    total: 1,
    reviews: [sampleReview],
  })
})

describe('CognitionWorkbench', () => {
  it('renders three tabs and loads cognitions by default', async () => {
    renderPage()

    expect(screen.getByText('交易认知看板')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '认知库' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '实例' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '复盘' })).toBeInTheDocument()

    await waitFor(() => {
      expect(screen.getByText('大盘情绪衰退信号')).toBeInTheDocument()
    })

    expect(apiModule.listCognitions).toHaveBeenCalledTimes(1)
    expect(apiModule.listInstances).not.toHaveBeenCalled()
    expect(apiModule.listReviews).not.toHaveBeenCalled()
  })

  it('switches to instances tab and triggers listInstances', async () => {
    renderPage()

    await waitFor(() => {
      expect(apiModule.listCognitions).toHaveBeenCalledTimes(1)
    })

    fireEvent.click(screen.getByRole('button', { name: '实例' }))

    await waitFor(() => {
      expect(apiModule.listInstances).toHaveBeenCalledTimes(1)
    })

    await waitFor(() => {
      expect(screen.getByText('张老师')).toBeInTheDocument()
    })
    expect(screen.getByTitle('validated')).toHaveTextContent('已验证')
  })

  it('switches to reviews tab and shows review rows', async () => {
    renderPage()

    fireEvent.click(screen.getByRole('button', { name: '复盘' }))

    await waitFor(() => {
      expect(apiModule.listReviews).toHaveBeenCalledTimes(1)
    })

    await waitFor(() => {
      expect(screen.getByText('2026-04-07 ~ 2026-04-11')).toBeInTheDocument()
    })
    expect(screen.getByTitle('confirmed')).toHaveTextContent('已确认')
  })

  it('shows empty state when list is empty', async () => {
    vi.mocked(apiModule.listCognitions).mockResolvedValue({ total: 0, cognitions: [] })
    renderPage()

    await waitFor(() => {
      expect(screen.getByText('暂无记录')).toBeInTheDocument()
    })
  })
})
