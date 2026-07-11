import { useState } from 'react'
import { type StepProps, Section, Row, PrefillBanner, SelectField, TextField, TagsField, TextareaField, DynamicList, TeacherNotesPanel } from './widgets'
import CognitionPanel from './CognitionPanel'
import { get, set } from './formState'
import type { CalendarEvent, ReviewFactorDecision, TrinityFactorCode } from '../../lib/types'

interface WatchDirectionItem {
  direction: string
  reason: string
  target_stocks: string[]
  entry_condition: string
}

interface RiskItem {
  description: string
  impact: string
}

const IMPACT = [
  { value: '高', label: '高' },
  { value: '中', label: '中' },
  { value: '低', label: '低' },
]
const CONFIDENCE = [
  { value: '高', label: '高' },
  { value: '中', label: '中' },
  { value: '低', label: '低' },
  { value: '看不懂', label: '看不懂' },
]

const FACTOR_LABELS: Record<TrinityFactorCode, string> = {
  market_node: '大盘节点',
  sector_rhythm: '板块节奏',
  style_regime: '风格状态',
  leader_signal: '龙头信号',
}
const FACTOR_OPTIONS = (Object.entries(FACTOR_LABELS) as Array<[TrinityFactorCode, string]>)
  .map(([value, label]) => ({ value, label }))

const UNDETERMINED_LABELS: Record<string, string> = {
  undetermined_weak: '证据强度不足',
  undetermined_competing: '候选因子竞争，分差不足',
  undetermined_conflicted: '反证冲突较强',
  undetermined_missing_data: '关键数据缺失',
}

export default function StepPlan({
  data,
  onChange,
  prefill,
  factorScore,
  factorScorePending = false,
  factorScoreError,
  onFactorScore,
}: StepProps) {
  const d = data || {}
  const teacherNotes = prefill?.teacher_notes || []
  const watchDirections = (d.watch_directions as WatchDirectionItem[] | undefined) || []
  const risks = (d.risks as RiskItem[] | undefined) || []
  const [showOverride, setShowOverride] = useState(false)
  const [overridePrimary, setOverridePrimary] = useState<TrinityFactorCode | ''>('')
  const [overrideSupporting, setOverrideSupporting] = useState<TrinityFactorCode[]>([])
  const [overrideReason, setOverrideReason] = useState('')
  const recommendation = factorScore?.system_recommendation
  const recommendedPrimary = recommendation?.primary?.factor_code
  const recommendedSupporting = (recommendation?.supporting || [])
    .map(item => item.factor_code)
    .filter((code): code is TrinityFactorCode => Boolean(code))
    .slice(0, 2)
  const savedDecision = d.factor_decision as ReviewFactorDecision | undefined

  const g = <T = string,>(p: string, fb?: T) => {
    const fallback = (fb ?? '') as T
    const val = get<T | undefined>(d, p, undefined)
    if (val !== undefined && val !== '') return val
    if (p === 'discipline.note' && teacherNotes.length) {
      const avoids = teacherNotes
        .map((n) => n.avoid)
        .filter((avoid): avoid is string => Boolean(avoid))
      return avoids.length ? avoids.map((a, i) => `【${teacherNotes[i].teacher_name}】${a}`).join('；') as T : fallback
    }
    return fallback
  }
  const s = (p: string, v: unknown) => onChange(set(d, p, v))
  const calEvents: CalendarEvent[] = prefill?.calendar_events || []

  const writeDecision = (
    status: ReviewFactorDecision['status'],
    primary: TrinityFactorCode | null,
    supporting: TrinityFactorCode[],
    reason?: string,
  ) => {
    if (!factorScore) return
    const decision: ReviewFactorDecision = {
      score_run_id: factorScore.score_run_id,
      status,
      primary_factor: primary,
      supporting_factors: supporting.slice(0, 2),
      override_reason: reason?.trim() || null,
      input_by: 'web',
    }
    onChange({
      ...d,
      factor_decision: decision,
      key_factor: primary || '',
      secondary_factors: supporting.slice(0, 2),
    })
  }

  const toggleOverrideSupporting = (code: TrinityFactorCode) => {
    setOverrideSupporting(current => {
      if (current.includes(code)) return current.filter(item => item !== code)
      if (current.length >= 2) return current
      return [...current, code]
    })
  }

  return (
    <div className="space-y-6">
      <CognitionPanel
        stepKey="step8_plan"
        cognitions={prefill?.cognitions_by_step?.step8_plan}
      />
      <TeacherNotesPanel notes={teacherNotes} fields={['core_view', 'position_advice', 'avoid']} />
      <Section title="三位一体重点因子">
        <div className="space-y-4">
          <div className="rounded-lg border border-indigo-200 bg-indigo-50 p-4 space-y-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <div className="text-sm font-semibold text-indigo-900">双层评分辅助</div>
                <div className="text-xs text-indigo-700">LLM 相对重要度评分，非胜率</div>
              </div>
              <button
                type="button"
                onClick={onFactorScore}
                disabled={!onFactorScore || factorScorePending}
                className="rounded bg-indigo-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
              >
                {factorScorePending ? '评分中...' : '运行 LLM 评分'}
              </button>
            </div>

            {factorScoreError && (
              <div className="text-xs text-red-600">评分失败：{factorScoreError}</div>
            )}

            {savedDecision && (
              <div className="rounded border border-green-200 bg-green-50 p-2 text-xs text-green-800">
                已确认因子决定：{savedDecision.status} · run {savedDecision.score_run_id}。重新评分并确认后才能替换。
              </div>
            )}

            {factorScore && (
              <div className="space-y-3">
                <div className="text-xs text-gray-600">
                  运行 {factorScore.score_run_id}
                  {factorScore.cache_hit ? ' · 已命中同输入缓存' : ''}
                  {recommendation?.confidence ? ` · 置信度 ${recommendation.confidence}` : ''}
                </div>

                {(factorScore.factor_scores || []).length > 0 ? (
                  <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
                    {(factorScore.factor_scores || []).map(item => (
                      <div key={item.factor_code} className="rounded border border-indigo-100 bg-white p-2">
                        <div className="text-sm font-medium text-gray-800">
                          {FACTOR_LABELS[item.factor_code]} · {item.total_score ?? '-'} 分
                        </div>
                        {item.reason && <div className="mt-1 text-xs text-gray-500">{item.reason}</div>}
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="text-xs text-gray-600">
                    {recommendation?.recommendation_source === 'rule_fallback'
                      ? 'LLM 数字评分不可用，当前仅展示规则降级建议。'
                      : '暂无数字评分。'}
                  </div>
                )}

                {recommendedPrimary ? (
                  <div className="rounded bg-white p-3 text-sm text-gray-700">
                    <span className="font-medium">系统建议：</span>
                    主导 {FACTOR_LABELS[recommendedPrimary]}
                    {recommendedSupporting.length > 0 && (
                      <>；辅助 {recommendedSupporting.map(code => FACTOR_LABELS[code]).join('、')}</>
                    )}
                  </div>
                ) : (
                  <div className="rounded bg-white p-3 text-sm text-amber-700">
                    未确定主导因子：{
                      UNDETERMINED_LABELS[recommendation?.undetermined_reason || '']
                      || recommendation?.undetermined_reason
                      || '证据不足'
                    }
                  </div>
                )}

                <div className="flex flex-wrap gap-2">
                  <button
                    type="button"
                    disabled={!recommendedPrimary}
                    onClick={() => recommendedPrimary && writeDecision(
                      'accepted', recommendedPrimary, recommendedSupporting,
                    )}
                    className="rounded bg-green-600 px-3 py-1.5 text-xs font-medium text-white disabled:opacity-40"
                  >
                    接受建议
                  </button>
                  <button
                    type="button"
                    onClick={() => setShowOverride(value => !value)}
                    className="rounded border border-indigo-300 bg-white px-3 py-1.5 text-xs font-medium text-indigo-700"
                  >
                    改选
                  </button>
                  <button
                    type="button"
                    onClick={() => writeDecision('undetermined', null, [])}
                    className="rounded border border-gray-300 bg-white px-3 py-1.5 text-xs font-medium text-gray-700"
                  >
                    看不懂
                  </button>
                </div>

                {showOverride && (
                  <div className="rounded border border-indigo-100 bg-white p-3 space-y-3">
                    <SelectField
                      label="人工主导因子"
                      value={overridePrimary}
                      onChange={value => {
                        const next = value as TrinityFactorCode | ''
                        setOverridePrimary(next)
                        setOverrideSupporting(current => current.filter(code => code !== next))
                      }}
                      options={FACTOR_OPTIONS}
                    />
                    <div>
                      <div className="mb-1 text-sm font-medium text-gray-600">人工辅助因子（最多2个）</div>
                      <div className="flex flex-wrap gap-3">
                        {FACTOR_OPTIONS.filter(option => option.value !== overridePrimary).map(option => (
                          <label key={option.value} className="inline-flex items-center gap-1.5 text-sm text-gray-700">
                            <input
                              type="checkbox"
                              checked={overrideSupporting.includes(option.value)}
                              onChange={() => toggleOverrideSupporting(option.value)}
                            />
                            {option.label}
                          </label>
                        ))}
                      </div>
                    </div>
                    <TextareaField
                      label="改选理由"
                      value={overrideReason}
                      onChange={setOverrideReason}
                      rows={2}
                    />
                    <button
                      type="button"
                      disabled={!overridePrimary || !overrideReason.trim()}
                      onClick={() => overridePrimary && writeDecision(
                        'overridden', overridePrimary, overrideSupporting, overrideReason,
                      )}
                      className="rounded bg-indigo-600 px-3 py-1.5 text-xs font-medium text-white disabled:opacity-40"
                    >
                      确认改选
                    </button>
                  </div>
                )}
              </div>
            )}
          </div>
          {savedDecision ? (
            <div className="rounded border border-gray-200 bg-gray-50 p-3 text-sm text-gray-700">
              <div className="font-medium">兼容镜像由因子决定自动维护，不可单独覆盖。</div>
              <div className="mt-1 text-xs text-gray-500">
                主导：{savedDecision.primary_factor || '未确定'}；辅助：{
                  (savedDecision.supporting_factors || []).join('、') || '无'
                }
              </div>
            </div>
          ) : (
            <>
              <TextField label="当前最重要的因子" value={g('key_factor')} onChange={v => s('key_factor', v)} placeholder="大盘节点 / 板块轮动 / 风格切换..." />
              <TagsField label="次要因子" value={g('secondary_factors', [])} onChange={v => s('secondary_factors', v)} />
            </>
          )}
        </div>
      </Section>

      <DynamicList
        title="关注方向"
        items={watchDirections}
        onChange={v => onChange({ ...d, watch_directions: v })}
        defaultItem={{ direction: '', reason: '', target_stocks: [], entry_condition: '' }}
        renderItem={(item, upd) => (
          <div className="space-y-3">
            <Row>
              <TextField label="方向" value={item.direction} onChange={v => upd('direction', v)} />
              <TextField label="原因" value={item.reason} onChange={v => upd('reason', v)} />
            </Row>
            <Row>
              <TagsField label="目标票" value={item.target_stocks || []} onChange={v => upd('target_stocks', v)} />
              <TextField label="介入条件" value={item.entry_condition} onChange={v => upd('entry_condition', v)} />
            </Row>
          </div>
        )}
      />

      <DynamicList
        title="风险提示"
        items={risks}
        onChange={v => onChange({ ...d, risks: v })}
        defaultItem={{ description: '', impact: '' }}
        renderItem={(item, upd) => (
          <Row>
            <TextField label="风险描述" value={item.description} onChange={v => upd('description', v)} />
            <SelectField label="影响程度" value={item.impact} onChange={v => upd('impact', v)} options={IMPACT} />
          </Row>
        )}
      />

      <Section title="操作纪律">
        <Row cols={3}>
          <TextField label="最大仓位" value={g('discipline.max_position')} onChange={v => s('discipline.max_position', v)} />
          <TextField label="止损规则" value={g('discipline.stop_loss_rule')} onChange={v => s('discipline.stop_loss_rule', v)} />
          <TextField label="备注" value={g('discipline.note')} onChange={v => s('discipline.note', v)} />
        </Row>
      </Section>

      <Section title="综合结论">
        <div className="space-y-4">
          <TextField label="一句话总结" value={g('summary.one_sentence')} onChange={v => s('summary.one_sentence', v)} placeholder="一句话概括今日市场..." />
          <TextareaField label="三位一体结论" value={g('summary.trinity')} onChange={v => s('summary.trinity', v)} rows={3} />
          <SelectField label="次日判断信心" value={g('summary.confidence')} onChange={v => s('summary.confidence', v)} options={CONFIDENCE} />
        </div>
      </Section>

      {calEvents.length > 0 && (
        <PrefillBanner>
          <div className="text-xs text-gray-500 mb-1">当日投资日历事件</div>
          <ul className="space-y-1">
            {calEvents.map((e) => (
              <li key={e.id} className="flex items-center gap-2 text-sm">
                <span className={`px-1.5 py-0.5 rounded text-xs font-medium ${
                  e.impact === 'high' ? 'bg-red-100 text-red-700' :
                  e.impact === 'medium' ? 'bg-amber-100 text-amber-700' :
                  'bg-gray-100 text-gray-600'
                }`}>
                  {e.impact || '一般'}
                </span>
                <span className="text-gray-700">{e.event}</span>
              </li>
            ))}
          </ul>
        </PrefillBanner>
      )}
    </div>
  )
}
