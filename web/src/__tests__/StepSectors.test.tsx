import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import StepSectors from '../components/review/StepSectors'
import type { ReviewPrefillData, ReviewStepValue } from '../lib/types'

function renderStep(data: ReviewStepValue = {}, prefill?: ReviewPrefillData) {
  const onChange = vi.fn()
  render(
    <MemoryRouter>
      <StepSectors data={data} onChange={onChange} prefill={prefill} />
    </MemoryRouter>
  )
  return { onChange }
}

const prefill: ReviewPrefillData = {
  date: '2026-04-03',
  market: {
    available: true,
    date: '2026-04-03',
    sh_index_close: 3210,
    sh_index_change_pct: 1.1,
    sz_index_close: 10020,
    sz_index_change_pct: 1.9,
    total_amount: 12000,
    advance_count: 3100,
    decline_count: 1600,
    sh_above_ma5w: true,
    sz_above_ma5w: true,
    chinext_above_ma5w: false,
    star50_above_ma5w: false,
    avg_price_above_ma5w: true,
    limit_up_count: 88,
    limit_down_count: 6,
    seal_rate: 81,
    broken_rate: 19,
    highest_board: 5,
    continuous_board_counts: null,
    premium_10cm: 2.1,
    premium_20cm: 3.2,
    premium_30cm: 1.1,
    premium_second_board: 4.4,
    northbound_net: 56.2,
    margin_balance: null,
    sector_industry: {
      data: [
        { name: 'AI算力', change_pct: 5.2 },
        { name: '电力', change_pct: 3.1 },
      ],
      bottom: [{ name: '医药', change_pct: -1.8 }],
    },
    sector_rhythm_industry: [
      { name: 'AI算力', phase: '主升', change_today: 5.2, rank_today: 1, confidence: '高' },
    ],
  },
  prev_market: null,
  avg_5d_amount: null,
  avg_20d_amount: null,
  teacher_notes: [
    {
      id: 1,
      teacher_id: 1,
      teacher_name: '小鲍',
      date: '2026-04-03',
      title: '板块备注',
      core_view: null,
      tags: null,
      sectors: '继续看AI算力',
      key_points: '主线没有切换',
      created_at: '2026-04-03T07:00:00',
    },
  ],
  holdings: [],
  calendar_events: [],
  main_themes: [
    {
      date: '2026-04-03',
      theme_name: 'AI算力',
      phase: '主升',
      duration_days: 5,
      key_stocks: ['高标A', '中军B'],
      status: 'active',
    },
  ],
  industry_info: [
    {
      id: 1,
      sector_name: 'AI算力',
      date: '2026-04-03',
      content: '服务器链订单继续强化',
      info_type: 'analysis',
      confidence: '高',
      timeliness: '近期',
      source: '盘后整理',
    },
  ],
  review_signals: {
    market: {
      moneyflow_summary: null,
      market_structure_rows: [],
    },
    sectors: {
      strongest_rows: [
        { rank: 1, name: '可控核聚变', up_nums: 12, cons_nums: 3, pct_chg: 4.5, up_stat: '3板2家' },
      ],
      industry_moneyflow_rows: [
        { name: 'AI算力', net_amount_yi: 1.88, pct_change: 5.2, lead_stock: '高标A' },
      ],
      concept_moneyflow_rows: [
        { name: '可控核聚变', net_amount_yi: 2.56, pct_change: 4.3, lead_stock: null },
      ],
      projection_candidates: [
        {
          sector_name: 'AI算力',
          source_tags: ['main_theme', 'moneyflow', 'teacher_note'],
          facts: {
            phase_hint: '主升',
            duration_days: 5,
            pct_chg: 5.2,
            limit_up_count: 3,
            emotion_leader: '高标A',
            capacity_leader: '中军B',
            lead_stock: '高标A',
            net_amount_yi: 1.88,
            teacher_note_refs: [{ note_id: 1, teacher_name: '小鲍', title: '板块备注' }],
          },
          key_stocks: ['高标A', '中军B'],
          evidence_text: '活跃主线，资金继续流入，老师观点仍强调主线未切换。',
        },
      ],
    },
    emotion: {
      ladder_rows: [],
    },
  },
}

describe('StepSectors', () => {
  it('renders theme, sector rhythm and industry info from prefill', () => {
    renderStep({}, prefill)

    expect(screen.getByDisplayValue('AI算力')).toBeInTheDocument()
    expect(screen.getByDisplayValue('持续')).toBeInTheDocument()
    expect(screen.getByText('行业板块排行（申万）')).toBeInTheDocument()
    expect(screen.getByText('行业节奏信号（当日前列）')).toBeInTheDocument()
    expect(screen.getByText('近期行业信息（1 条）')).toBeInTheDocument()
    expect(screen.getAllByText('当日最强板块').length).toBeGreaterThan(0)
    expect(screen.getByText('板块资金确认')).toBeInTheDocument()
    expect(screen.getAllByText('可控核聚变').length).toBeGreaterThan(0)
    expect(screen.getByText('行业资金流')).toBeInTheDocument()
    expect(screen.getByText('概念资金流')).toBeInTheDocument()
    expect(screen.getByText('服务器链订单继续强化')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /完整市场数据/i })).toHaveAttribute('href', '/market/2026-04-03')
    expect(screen.getByText('系统预填候选')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '加入推演卡' })).toBeInTheDocument()
    expect(screen.getByText('情绪龙头：高标A')).toBeInTheDocument()
    expect(screen.getByText('容量中军：中军B')).toBeInTheDocument()
    expect(screen.getByText('领涨股：高标A')).toBeInTheDocument()
  })

  it('emits nested payload when user edits main theme name', () => {
    const { onChange } = renderStep({}, prefill)

    fireEvent.change(screen.getByLabelText('主线名称'), { target: { value: '电力' } })

    expect(onChange).toHaveBeenCalledWith({
      main_theme: { name: '电力' },
    })
  })

  it('adds a projection card from candidate', () => {
    const { onChange } = renderStep({}, prefill)

    fireEvent.click(screen.getByRole('button', { name: '加入推演卡' }))

    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({
      projections: [
        expect.objectContaining({
          sector_name: 'AI算力',
          big_cycle_stage: '主升',
          key_stocks: ['高标A', '中军B'],
        }),
      ],
    }))
  })

  it('collapses projection candidates when more than 6', () => {
    const manyCandidates = Array.from({ length: 8 }, (_, i) => ({
      sector_name: `板块${i + 1}`,
      source_tags: ['main_theme'] as string[],
      facts: { phase_hint: '主升', pct_chg: 1.0 + i },
      key_stocks: [],
      evidence_text: '',
    }))
    const manyPrefill: ReviewPrefillData = {
      ...prefill,
      review_signals: {
        ...prefill.review_signals!,
        sectors: {
          ...prefill.review_signals!.sectors,
          projection_candidates: manyCandidates,
        },
      },
    }
    renderStep({}, manyPrefill)

    expect(screen.getByText('板块1')).toBeInTheDocument()
    expect(screen.getByText('板块6')).toBeInTheDocument()
    expect(screen.queryByText('板块7')).not.toBeInTheDocument()
    expect(screen.getByText('展开全部 (8)')).toBeInTheDocument()

    const expandBtn = screen.getByText('展开全部 (8)')
    fireEvent.click(expandBtn)
    expect(screen.getByText('板块7')).toBeInTheDocument()
    expect(screen.getByText('板块8')).toBeInTheDocument()

    const collapseBtn = screen.getByText((content, element) =>
      content === '收起' && element?.classList.contains('text-blue-600') === true
    )
    fireEvent.click(collapseBtn)
    expect(screen.queryByText('板块7')).not.toBeInTheDocument()
  })

  it('maps rhythm phase hint into valid big cycle stage options', () => {
    const startupPrefill: ReviewPrefillData = {
      ...prefill,
      review_signals: {
        market: prefill.review_signals!.market,
        emotion: prefill.review_signals!.emotion,
        sectors: {
          ...prefill.review_signals!.sectors,
          projection_candidates: [
            {
              sector_name: '机器人',
              source_tags: ['rhythm'],
              facts: {
                phase_hint: '启动',
                pct_chg: 3.1,
              },
              key_stocks: ['高标A'],
              evidence_text: '节奏启动。',
            },
          ],
        },
      },
    }
    const { onChange } = renderStep({}, startupPrefill)

    fireEvent.click(screen.getByRole('button', { name: '加入推演卡' }))

    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({
      projections: [
        expect.objectContaining({
          sector_name: '机器人',
          big_cycle_stage: '将成龙',
        }),
      ],
    }))
  })
})
