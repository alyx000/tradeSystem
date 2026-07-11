import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import StepPlan from '../components/review/StepPlan'
import type { StepProps } from '../components/review/widgets'
import type { ReviewPrefillData, ReviewStepValue, TrinityFactorScoreRun } from '../lib/types'

function renderStep(
  data: ReviewStepValue = {},
  prefill?: ReviewPrefillData,
  extra: Partial<StepProps> = {},
) {
  const onChange = vi.fn()
  const onFactorScore = vi.fn()
  render(
    <StepPlan
      data={data}
      onChange={onChange}
      prefill={prefill}
      onFactorScore={onFactorScore}
      {...extra}
    />
  )
  return { onChange, onFactorScore }
}

const factorScore: TrinityFactorScoreRun = {
  score_run_id: 'factor-run-1',
  trade_date: '2026-04-03',
  status: 'success',
  cache_hit: false,
  is_cacheable: true,
  factor_scores: [
    {
      factor_code: 'sector_rhythm',
      model_scores: {},
      normalized_scores: {
        current_dominance: 5,
        cross_layer_alignment: 4,
        rhythm_clarity: 4,
        next_stage_relevance: 4,
        counterevidence: 1,
      },
      evidence_quality: 4,
      critical_missing: false,
      total_score: 83,
      reason: '[判断]板块节奏是当日最清晰的结构连接。',
    },
    {
      factor_code: 'leader_signal',
      model_scores: {},
      normalized_scores: {
        current_dominance: 3,
        cross_layer_alignment: 3,
        rhythm_clarity: 3,
        next_stage_relevance: 3,
        counterevidence: 1,
      },
      evidence_quality: 4,
      critical_missing: false,
      total_score: 60,
      reason: '[判断]龙头信号提供辅助印证。',
    },
  ],
  sector_scores: [],
  system_recommendation: {
    primary: { factor_code: 'sector_rhythm', total_score: 83 },
    supporting: [{ factor_code: 'leader_signal', total_score: 60 }],
    confidence: 'high',
    undetermined_reason: null,
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

const prefill: ReviewPrefillData = {
  date: '2026-04-03',
  market: null,
  prev_market: null,
  avg_5d_amount: null,
  avg_20d_amount: null,
  teacher_notes: [
    {
      id: 1,
      teacher_id: 1,
      teacher_name: '小鲍',
      date: '2026-04-03',
      title: '计划提醒',
      core_view: '先看主线分歧后的承接',
      tags: null,
      sectors: null,
      position_advice: '仓位先控制在3成内',
      avoid: '避免一致性高开的跟风票',
      created_at: '2026-04-03T07:00:00',
    },
  ],
  holdings: [],
  calendar_events: [
    {
      id: 1,
      date: '2026-04-03',
      event: '美国非农就业数据',
      impact: 'high',
      category: 'macro',
    },
  ],
  main_themes: [],
}

describe('StepPlan', () => {
  it('keeps teacher notes as context without auto-filling key factor', () => {
    renderStep({}, prefill)

    expect(screen.getByText('老师观点参考')).toBeInTheDocument()
    expect(screen.getByLabelText('当前最重要的因子')).toHaveValue('')
    expect(screen.getByText('先看主线分歧后的承接')).toBeInTheDocument()
    expect(screen.getByDisplayValue('【小鲍】避免一致性高开的跟风票')).toBeInTheDocument()
    expect(screen.getByText('当日投资日历事件')).toBeInTheDocument()
    expect(screen.getByText('美国非农就业数据')).toBeInTheDocument()
    expect(screen.getByText('high')).toBeInTheDocument()
  })

  it('emits nested payload when user edits conclusion summary', () => {
    const { onChange } = renderStep({}, prefill)

    fireEvent.change(screen.getByLabelText('一句话总结'), { target: { value: '主线强但分歧临近' } })

    expect(onChange).toHaveBeenCalledWith({
      summary: { one_sentence: '主线强但分歧临近' },
    })
  })

  it('runs scoring, shows normalized recommendation and accepts it explicitly', () => {
    const { onChange, onFactorScore } = renderStep({}, prefill, { factorScore })

    expect(screen.getByText('LLM 相对重要度评分，非胜率')).toBeInTheDocument()
    expect(screen.getByText(/板块节奏.*83/)).toBeInTheDocument()
    expect(screen.getByText(/龙头信号.*60/)).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '运行 LLM 评分' }))
    expect(onFactorScore).toHaveBeenCalledTimes(1)

    fireEvent.click(screen.getByRole('button', { name: '接受建议' }))
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({
      key_factor: 'sector_rhythm',
      secondary_factors: ['leader_signal'],
      factor_decision: expect.objectContaining({
        score_run_id: 'factor-run-1',
        status: 'accepted',
        primary_factor: 'sector_rhythm',
        supporting_factors: ['leader_signal'],
        input_by: 'web',
      }),
    }))
  })

  it('requires an explicit reason when overriding and supports 看不懂', () => {
    const { onChange } = renderStep({}, prefill, { factorScore })

    fireEvent.click(screen.getByRole('button', { name: '改选' }))
    fireEvent.change(screen.getByLabelText('人工主导因子'), {
      target: { value: 'market_node' },
    })
    expect(screen.getByRole('button', { name: '确认改选' })).toBeDisabled()
    fireEvent.change(screen.getByLabelText('改选理由'), {
      target: { value: '系统性风险锁定大盘节点' },
    })
    fireEvent.click(screen.getByRole('button', { name: '确认改选' }))
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({
      key_factor: 'market_node',
      factor_decision: expect.objectContaining({
        status: 'overridden',
        primary_factor: 'market_node',
        override_reason: '系统性风险锁定大盘节点',
      }),
    }))

    fireEvent.click(screen.getByRole('button', { name: '看不懂' }))
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({
      key_factor: '',
      secondary_factors: [],
      factor_decision: expect.objectContaining({ status: 'undetermined' }),
    }))
  })

  it('shows a persisted decision without exposing editable legacy mirrors', () => {
    renderStep({
      factor_decision: {
        score_run_id: 'saved-run',
        status: 'accepted',
        primary_factor: 'sector_rhythm',
        supporting_factors: ['leader_signal'],
        input_by: 'web',
      },
      key_factor: 'sector_rhythm',
      secondary_factors: ['leader_signal'],
    }, prefill)

    expect(screen.getByText(/已确认因子决定.*saved-run/)).toBeInTheDocument()
    expect(screen.queryByLabelText('当前最重要的因子')).not.toBeInTheDocument()
    expect(screen.getByText(/兼容镜像由因子决定自动维护/)).toBeInTheDocument()
  })
})
