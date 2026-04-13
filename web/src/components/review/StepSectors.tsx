import { useState } from 'react'
import { Link } from 'react-router-dom'
import { type StepProps, Section, Row, PrefillBanner, SelectField, TextField, NumberField, TagsField, TextareaField, DynamicList, TeacherNotesPanel } from './widgets'
import { get, set } from './formState'
import type {
  IndustryInfoItem,
  MainThemeItem,
  ReviewNextDayFocus,
  ReviewProjectionCandidate,
  ReviewSectorProjection,
  SectorIndustryPrefill,
  SectorRhythmItem,
  TeacherNote,
} from '../../lib/types'

// 行业信息首屏展示条数
const INDUSTRY_INFO_INITIAL_COUNT = 3

// 节奏阶段颜色映射
const PHASE_COLOR: Record<string, string> = {
  '启动': 'bg-green-50 text-green-700',
  '信不信加速': 'bg-blue-50 text-blue-700',
  '主升': 'bg-blue-100 text-blue-800',
  '首次分歧': 'bg-yellow-50 text-yellow-700',
  '高潮': 'bg-orange-50 text-orange-700',
  '震荡': 'bg-gray-50 text-gray-600',
  '轮动': 'bg-purple-50 text-purple-700',
  '衰退': 'bg-red-50 text-red-600',
}

const CONFIDENCE_COLOR: Record<string, string> = {
  '高': 'text-green-600',
  '中': 'text-yellow-600',
  '低': 'text-gray-400',
}

const SOURCE_TAG_LABEL: Record<string, string> = {
  main_theme: '活跃主线',
  rhythm: '节奏信号',
  strongest: '最强榜',
  moneyflow: '资金流',
  teacher_note: '老师观点',
  industry_info: '行业信息',
}

const SOURCE_TAG_CLASS: Record<string, string> = {
  main_theme: 'bg-blue-50 text-blue-700',
  rhythm: 'bg-indigo-50 text-indigo-700',
  strongest: 'bg-orange-50 text-orange-700',
  moneyflow: 'bg-emerald-50 text-emerald-700',
  teacher_note: 'bg-amber-50 text-amber-700',
  industry_info: 'bg-slate-100 text-slate-700',
}

const PHASE_TO_BIG_CYCLE_STAGE: Record<string, string> = {
  '启动': '将成龙',
  '信不信加速': '将成龙',
  '主升': '主升',
  '首次分歧': '主升',
  '高潮': '主升',
  '震荡': '震荡',
  '轮动': '震荡',
  '衰退': '衰退',
}

function phaseClass(phase: string) {
  return PHASE_COLOR[phase] ?? 'bg-gray-50 text-gray-600'
}

type RhythmSortKey = 'change_today' | 'cumulative_pct_5d' | 'cumulative_pct_10d'

const RHYTHM_SORT_OPTIONS: { key: RhythmSortKey; label: string }[] = [
  { key: 'change_today', label: '当日' },
  { key: 'cumulative_pct_5d', label: '5日' },
  { key: 'cumulative_pct_10d', label: '10日' },
]

function sortRhythm(items: SectorRhythmItem[], key: RhythmSortKey): SectorRhythmItem[] {
  return [...items].sort((a, b) => {
    const av = a[key] ?? -Infinity
    const bv = b[key] ?? -Infinity
    return (bv as number) - (av as number)
  })
}

function RhythmSortedBlock({ sectorRhythm }: { sectorRhythm: SectorRhythmItem[] }) {
  const [sortKey, setSortKey] = useState<RhythmSortKey>('change_today')
  const sorted = sortRhythm(sectorRhythm, sortKey).slice(0, 10)

  return (
    <>
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs font-medium text-gray-600">行业节奏信号（当日前列）</span>
        <div className="flex gap-1">
          {RHYTHM_SORT_OPTIONS.map(opt => (
            <button
              key={opt.key}
              onClick={() => setSortKey(opt.key)}
              className={`px-2 py-0.5 rounded text-xs transition-colors ${
                sortKey === opt.key
                  ? 'bg-blue-100 text-blue-700 font-medium'
                  : 'text-gray-400 hover:text-gray-600'
              }`}
            >
              {opt.label}
            </button>
          ))}
        </div>
      </div>
      <div className="space-y-1.5">
        {sorted.map((item, i) => (
          <div key={i} className="flex flex-wrap items-center gap-2 text-xs">
            <span className="font-medium text-gray-800 min-w-[5rem]">{item.name}</span>
            <span className={`px-1.5 py-0.5 rounded text-xs font-medium ${phaseClass(item.phase ?? '')}`}>{item.phase}</span>
            <span className={`font-medium ${Number(item.change_today) >= 0 ? 'text-green-600' : 'text-red-500'}`}>
              {Number(item.change_today) >= 0 ? '+' : ''}{Number(item.change_today ?? 0).toFixed(2)}%
            </span>
            <span className="text-gray-400">#{item.rank_today}</span>
            {item.confidence && (
              <span className={`${CONFIDENCE_COLOR[item.confidence] ?? 'text-gray-400'}`}>置信:{item.confidence}</span>
            )}
            {item.cumulative_pct_5d != null && (
              <span className={`font-medium ${item.cumulative_pct_5d >= 0 ? 'text-green-600' : 'text-red-500'}`}>
                5日{item.cumulative_pct_5d >= 0 ? '+' : ''}{item.cumulative_pct_5d.toFixed(2)}%
              </span>
            )}
            {item.cumulative_pct_10d != null && (
              <span className={`font-medium ${item.cumulative_pct_10d >= 0 ? 'text-green-600' : 'text-red-500'}`}>
                10日{item.cumulative_pct_10d >= 0 ? '+' : ''}{item.cumulative_pct_10d.toFixed(2)}%
              </span>
            )}
            {item.consecutive_in_top30 != null && item.consecutive_in_top30 > 0 && (
              <span className="text-gray-500">连榜{item.consecutive_in_top30}日</span>
            )}
          </div>
        ))}
      </div>
    </>
  )
}

function mapPhaseHintToBigCycleStage(phaseHint: string | null | undefined) {
  const normalized = (phaseHint || '').trim()
  return PHASE_TO_BIG_CYCLE_STAGE[normalized] || ''
}

function InfoTypeBadge({ type }: { type: string }) {
  const color = type === 'news' ? 'bg-blue-50 text-blue-600'
    : type === 'analysis' ? 'bg-purple-50 text-purple-600'
    : 'bg-gray-50 text-gray-500'
  return <span className={`inline-block text-xs px-1.5 py-0.5 rounded ${color}`}>{type}</span>
}

const STATUS = [
  { value: '持续', label: '持续' },
  { value: '分歧', label: '分歧' },
  { value: '切换中', label: '切换中' },
]
const SECTOR_NODE = [
  { value: '超跌', label: '超跌' },
  { value: '启动', label: '启动' },
  { value: '信不信加速', label: '信不信加速' },
  { value: '主升', label: '主升' },
  { value: '首次分歧', label: '首次分歧' },
  { value: '震荡', label: '震荡' },
  { value: '轮动', label: '轮动' },
]
const MARKET_STYLE = [
  { value: '趋势行情', label: '趋势行情' },
  { value: '连板行情', label: '连板行情' },
]
const INCR_STOCK = [
  { value: '增量', label: '增量' },
  { value: '存量', label: '存量' },
]
const STRENGTH = [
  { value: '强', label: '强' },
  { value: '走弱', label: '走弱' },
  { value: '弱', label: '弱' },
]
const VS_INDEX = [
  { value: '顺指数', label: '顺指数' },
  { value: '逆指数', label: '逆指数' },
]
const VOL_TREND = [
  { value: '放量', label: '放量' },
  { value: '缩量', label: '缩量' },
  { value: '持平', label: '持平' },
]
const RECOGNITION = [
  { value: '高', label: '高' },
  { value: '中', label: '中' },
  { value: '低', label: '低' },
]
const SECTOR_TYPE = [
  { value: 'industry_logic', label: '行业逻辑' },
  { value: 'stage_reason', label: '阶段归因' },
  { value: 'sentiment_core', label: '情绪核心' },
]
const BIG_CYCLE_STAGE = [
  { value: '将成龙', label: '将成龙' },
  { value: '主升', label: '主升' },
  { value: '震荡', label: '震荡' },
  { value: '二波', label: '二波' },
  { value: '衰退', label: '衰退' },
]
const CONNECTION_BIAS = [
  { value: '加强', label: '加强' },
  { value: '减弱', label: '减弱' },
  { value: '不清楚', label: '不清楚' },
]
const MARKET_FIT = [
  { value: '匹配大势节奏', label: '匹配大势节奏' },
  { value: '一般', label: '一般' },
  { value: '不匹配', label: '不匹配' },
]
const ROLE_EXPECTATION = [
  { value: '趋势主线', label: '趋势主线' },
  { value: '当日最强', label: '当日最强' },
  { value: '轮动', label: '轮动' },
  { value: '活跃震荡', label: '活跃震荡' },
]
const RETURN_FLOW_VIEW = [
  { value: '预期回流', label: '预期回流' },
  { value: '仅跟踪', label: '仅跟踪' },
  { value: '放弃', label: '放弃' },
]
const FULLY_PRICED_RISK = [
  { value: '低', label: '低' },
  { value: '中', label: '中' },
  { value: '高', label: '高' },
]

interface StrongestSectorItem {
  name: string
  reason: string
  vs_index: string
  node: string
  volume_trend: string
  recognition: string
  key_stocks: string[]
}

interface UnusualSectorItem {
  name: string
  trigger: string
  start_position: string
  volume: string
  key_stocks: string[]
}

export default function StepSectors({ data, onChange, prefill }: StepProps) {
  const d = data || {}
  const themes: MainThemeItem[] = prefill?.main_themes || []
  const firstTheme = themes[0]
  const teacherNotes: TeacherNote[] = prefill?.teacher_notes || []
  const [industryInfoExpanded, setIndustryInfoExpanded] = useState(false)
  const [showAllCandidates, setShowAllCandidates] = useState(false)
  const strongest = (d.strongest as StrongestSectorItem[] | undefined) || []
  const unusual = (d.unusual as UnusualSectorItem[] | undefined) || []
  const projections = (d.projections as ReviewSectorProjection[] | undefined) || []
  const nextDayFocus = (d.next_day_focus as ReviewNextDayFocus[] | undefined) || []

  const date = prefill?.date as string | undefined
  const sectorIndustry: SectorIndustryPrefill | undefined = prefill?.market?.sector_industry
  const sectorRhythm: SectorRhythmItem[] | undefined = prefill?.market?.sector_rhythm_industry
  const industryInfoList: IndustryInfoItem[] = prefill?.industry_info || []
  const sectorSignals = prefill?.review_signals?.sectors
  const projectionCandidates: ReviewProjectionCandidate[] = sectorSignals?.projection_candidates || []

  const g = <T = string,>(p: string, fb?: T) => {
    const fallback = (fb ?? '') as T
    const val = get<T | undefined>(d, p, undefined)
    if (val !== undefined && val !== '') return val

    if (firstTheme) {
      if (p === 'main_theme.name') return (firstTheme.theme_name || '') as T
      if (p === 'main_theme.status') return (firstTheme.status === 'active' ? '持续' : '') as T
      if (p === 'main_theme.duration_days') return (firstTheme.duration_days ?? null) as T
      if (p === 'main_theme.key_stocks') {
        if (typeof firstTheme.key_stocks === 'string') {
          try { return JSON.parse(firstTheme.key_stocks) as T } catch { return [] as T }
        }
        return ((firstTheme.key_stocks as string[] | null | undefined) || []) as T
      }
      if (p === 'main_theme.node') return (firstTheme.phase || '') as T
    }
    if (p === 'notes' && teacherNotes.length) {
      const parts = teacherNotes.flatMap((n) => [
        n.sectors ? `【${n.teacher_name} 板块】${n.sectors}` : null,
        n.key_points ? `【${n.teacher_name} 要点】${n.key_points}` : null,
      ]).filter(Boolean)
      if (parts.length) return parts.join('\n') as T
    }
    return fallback
  }
  const s = (p: string, v: unknown) => onChange(set(d, p, v))
  const fmtYi = (v: number | null | undefined) => (v != null ? `${v >= 0 ? '+' : ''}${v.toFixed(2)}亿` : '-')
  const fmtPct = (v: number | null | undefined) => (v != null ? `${v >= 0 ? '+' : ''}${v.toFixed(2)}%` : '-')

  const addProjectionFromCandidate = (candidate: ReviewProjectionCandidate) => {
    const exists = projections.some((item) => item.sector_name === candidate.sector_name)
    if (exists) return
    onChange({
      ...d,
      projections: [
        ...projections,
        {
          sector_name: candidate.sector_name,
          sector_type: '',
          big_cycle_stage: mapPhaseHintToBigCycleStage(candidate.facts?.phase_hint),
          connection_bias: '',
          market_fit: '',
          role_expectation: '',
          return_flow_view: '',
          fully_priced_risk: '',
          key_stocks: candidate.key_stocks || [],
          supporting_facts: candidate.evidence_text ? [candidate.evidence_text] : [],
          logic_aesthetic: '',
          judgement_notes: '',
        },
      ],
    })
  }

  return (
    <div className="space-y-6">
      <TeacherNotesPanel notes={teacherNotes} fields={['sectors', 'key_points']} />
      {themes.length > 0 && (
        <PrefillBanner>
          <div className="text-xs text-gray-500 mb-1">当前活跃主线（{themes.length} 条）</div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
            {themes.slice(0, 4).map((t) => (
              <div key={`${t.date}-${t.theme_name}`} className="flex items-center gap-2">
                <span className="font-medium text-gray-700">{t.theme_name}</span>
                {t.duration_days && <span className="text-xs text-gray-400">{t.duration_days}天</span>}
                {t.phase && <span className="text-xs px-1.5 py-0.5 bg-blue-50 text-blue-600 rounded">{t.phase}</span>}
              </div>
            ))}
          </div>
        </PrefillBanner>
      )}

      <Section title="主线板块">
        <div className="space-y-4">
          <Row cols={3}>
            <TextField label="主线名称" value={g('main_theme.name')} onChange={v => s('main_theme.name', v)} />
            <SelectField label="状态" value={g('main_theme.status')} onChange={v => s('main_theme.status', v)} options={STATUS} />
            <NumberField label="持续天数" value={g('main_theme.duration_days', null)} onChange={v => s('main_theme.duration_days', v)} suffix="天" />
          </Row>
          <Row>
            <SelectField label="板块节奏" value={g('main_theme.node')} onChange={v => s('main_theme.node', v)} options={SECTOR_NODE} />
            <TagsField label="核心票" value={g('main_theme.key_stocks', [])} onChange={v => s('main_theme.key_stocks', v)} placeholder="如：宁德时代，比亚迪" />
          </Row>
        </div>
      </Section>

      <Section title="行情类型">
        <Row cols={3}>
          <SelectField label="行情风格" value={g('market_type.style')} onChange={v => s('market_type.style', v)} options={MARKET_STYLE} />
          <SelectField label="增量/存量" value={g('market_type.incremental')} onChange={v => s('market_type.incremental', v)} options={INCR_STOCK} />
          <SelectField label="中军强度" value={g('market_type.mid_cap_strength')} onChange={v => s('market_type.mid_cap_strength', v)} options={STRENGTH} />
        </Row>
      </Section>

      <DynamicList
        title="板块推演卡"
        items={projections}
        onChange={v => onChange({ ...d, projections: v })}
        defaultItem={{
          sector_name: '',
          sector_type: '',
          big_cycle_stage: '',
          connection_bias: '',
          market_fit: '',
          role_expectation: '',
          return_flow_view: '',
          fully_priced_risk: '',
          key_stocks: [],
          supporting_facts: [],
          logic_aesthetic: '',
          judgement_notes: '',
        }}
        renderItem={(item, upd) => (
          <div className="space-y-3">
            <Row cols={3}>
              <TextField label="板块名称" value={item.sector_name || ''} onChange={v => upd('sector_name', v)} />
              <SelectField label="板块类型" value={item.sector_type || ''} onChange={v => upd('sector_type', v)} options={SECTOR_TYPE} />
              <SelectField label="所处阶段" value={item.big_cycle_stage || ''} onChange={v => upd('big_cycle_stage', v)} options={BIG_CYCLE_STAGE} />
            </Row>
            <Row cols={4}>
              <SelectField label="连接点判断" value={item.connection_bias || ''} onChange={v => upd('connection_bias', v)} options={CONNECTION_BIAS} />
              <SelectField label="与大势匹配度" value={item.market_fit || ''} onChange={v => upd('market_fit', v)} options={MARKET_FIT} />
              <SelectField label="角色预期" value={item.role_expectation || ''} onChange={v => upd('role_expectation', v)} options={ROLE_EXPECTATION} />
              <SelectField label="回流预期" value={item.return_flow_view || ''} onChange={v => upd('return_flow_view', v)} options={RETURN_FLOW_VIEW} />
            </Row>
            <Row cols={3}>
              <SelectField label="充分演绎风险" value={item.fully_priced_risk || ''} onChange={v => upd('fully_priced_risk', v)} options={FULLY_PRICED_RISK} />
              <TagsField label="核心票" value={item.key_stocks || []} onChange={v => upd('key_stocks', v)} />
              <TagsField label="支撑事实" value={item.supporting_facts || []} onChange={v => upd('supporting_facts', v)} />
            </Row>
            <Row cols={2}>
              <TextareaField label="逻辑审美" value={item.logic_aesthetic || ''} onChange={v => upd('logic_aesthetic', v)} placeholder="增量、落地、容量、谁主导变化" rows={2} />
              <TextareaField label="主观判断备注" value={item.judgement_notes || ''} onChange={v => upd('judgement_notes', v)} placeholder="回流、结束风险、预期差等" rows={2} />
            </Row>
          </div>
        )}
      />

      {(sectorSignals?.strongest_rows?.length ?? 0) > 0 && (
        (() => {
          const strongestRows = sectorSignals?.strongest_rows ?? []
          return (
            <PrefillBanner>
              <div className="text-xs font-medium text-gray-600 mb-2">最强板块参考数据</div>
              <div className="overflow-x-auto">
                <table className="min-w-full text-xs text-gray-600">
                  <thead>
                    <tr className="text-left text-gray-400">
                      <th className="py-1 pr-4 font-medium">排名</th>
                      <th className="py-1 pr-4 font-medium">板块</th>
                      <th className="py-1 pr-4 font-medium text-right">涨停家数</th>
                      <th className="py-1 pr-4 font-medium text-right">连板家数</th>
                      <th className="py-1 pr-4 font-medium text-right">涨跌幅</th>
                      <th className="py-1 font-medium">连板结构</th>
                    </tr>
                  </thead>
                  <tbody>
                    {strongestRows.map((row) => (
                      <tr key={`${row.name}-${row.rank ?? 'na'}`} className="border-t border-gray-200/70">
                        <td className="py-1.5 pr-4">{row.rank ?? '-'}</td>
                        <td className="py-1.5 pr-4 font-medium text-gray-700">{row.name}</td>
                        <td className="py-1.5 pr-4 text-right">{row.up_nums ?? '-'}</td>
                        <td className="py-1.5 pr-4 text-right">{row.cons_nums ?? '-'}</td>
                        <td className="py-1.5 pr-4 text-right">{fmtPct(row.pct_chg)}</td>
                        <td className="py-1.5">{row.up_stat || '-'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </PrefillBanner>
          )
        })()
      )}

      {/* ── 行业节奏分析（sector_rhythm_industry） ── */}
      {sectorRhythm && sectorRhythm.length > 0 && (
        <PrefillBanner>
          <RhythmSortedBlock sectorRhythm={sectorRhythm} />
        </PrefillBanner>
      )}

      {((sectorSignals?.industry_moneyflow_rows?.length ?? 0) > 0 || (sectorSignals?.concept_moneyflow_rows?.length ?? 0) > 0) && (
        <PrefillBanner>
          <div className="text-xs font-medium text-gray-600 mb-2">板块资金确认</div>
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            {(sectorSignals?.industry_moneyflow_rows?.length ?? 0) > 0 && (
              <div>
                <div className="text-xs text-blue-600 font-medium mb-1.5">行业资金流</div>
                <div className="space-y-1.5">
                  {sectorSignals!.industry_moneyflow_rows.map((row) => (
                    <div key={`${row.name}-${row.lead_stock ?? 'na'}`} className="rounded border border-gray-200 bg-white px-3 py-2">
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-xs font-medium text-gray-700">{row.name}</span>
                        <span className={`text-xs font-medium ${(row.net_amount_yi ?? 0) >= 0 ? 'text-red-600' : 'text-green-600'}`}>
                          {fmtYi(row.net_amount_yi)}
                        </span>
                      </div>
                      <div className="mt-0.5 flex items-center justify-between gap-2 text-xs text-gray-500">
                        <span>涨跌幅 {fmtPct(row.pct_change)}</span>
                        {row.lead_stock && <span>领涨股：{row.lead_stock}</span>}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
            {(sectorSignals?.concept_moneyflow_rows?.length ?? 0) > 0 && (
              <div>
                <div className="text-xs text-purple-600 font-medium mb-1.5">概念资金流</div>
                <div className="space-y-1.5">
                  {sectorSignals!.concept_moneyflow_rows.map((row) => (
                    <div key={`${row.name}-concept`} className="rounded border border-gray-200 bg-white px-3 py-2">
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-xs font-medium text-gray-700">{row.name}</span>
                        <span className={`text-xs font-medium ${(row.net_amount_yi ?? 0) >= 0 ? 'text-red-600' : 'text-green-600'}`}>
                          {fmtYi(row.net_amount_yi)}
                        </span>
                      </div>
                      <div className="mt-0.5 flex items-center justify-between gap-2 text-xs text-gray-500">
                        <span>涨跌幅 {fmtPct(row.pct_change)}</span>
                        {row.lead_stock && <span>领涨股：{row.lead_stock}</span>}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        </PrefillBanner>
      )}

      {projectionCandidates.length > 0 && (
        <Section title="系统预填候选">
          <PrefillBanner>
            <div className="text-xs text-gray-500 mb-3">
              系统已根据主线、最强板块、资金流、节奏、老师观点和行业信息，预先挑出值得推演的候选板块。
            </div>
            <div className="grid grid-cols-1 xl:grid-cols-2 gap-3">
              {(showAllCandidates ? projectionCandidates : projectionCandidates.slice(0, 6)).map((candidate) => (
                <div key={candidate.sector_name} className="rounded-lg border border-amber-200 bg-white p-3">
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <div className="text-sm font-semibold text-gray-800">{candidate.sector_name}</div>
                      <div className="mt-1 flex flex-wrap gap-1">
                        {(candidate.source_tags || []).map((tag) => (
                          <span
                            key={`${candidate.sector_name}-${tag}`}
                            className={`rounded px-1.5 py-0.5 text-xs font-medium ${SOURCE_TAG_CLASS[tag] || 'bg-gray-100 text-gray-700'}`}
                          >
                            {SOURCE_TAG_LABEL[tag] || tag}
                          </span>
                        ))}
                      </div>
                    </div>
                    <button
                      type="button"
                      onClick={() => addProjectionFromCandidate(candidate)}
                      className="shrink-0 rounded border border-blue-200 bg-blue-50 px-2.5 py-1 text-xs font-medium text-blue-700 hover:bg-blue-100"
                    >
                      加入推演卡
                    </button>
                  </div>
                  <div className="mt-3 grid grid-cols-2 gap-2 text-xs text-gray-600">
                    <div>阶段提示：{candidate.facts?.phase_hint || '-'}</div>
                    <div>持续天数：{candidate.facts?.duration_days ?? '-'}</div>
                    <div>涨跌幅：{fmtPct(candidate.facts?.pct_chg ?? null)}</div>
                    {candidate.facts?.cumulative_pct_5d != null && (
                      <div>5日累计：{candidate.facts.cumulative_pct_5d >= 0 ? '+' : ''}{candidate.facts.cumulative_pct_5d.toFixed(2)}%</div>
                    )}
                    {candidate.facts?.cumulative_pct_10d != null && (
                      <div>10日累计：{candidate.facts.cumulative_pct_10d >= 0 ? '+' : ''}{candidate.facts.cumulative_pct_10d.toFixed(2)}%</div>
                    )}
                    <div>涨停家数：{candidate.facts?.limit_up_count ?? '-'}</div>
                    <div>资金流：{fmtYi(candidate.facts?.net_amount_yi ?? null)}</div>
                    <div>情绪龙头：{candidate.facts?.emotion_leader || '-'}</div>
                    <div>容量中军：{candidate.facts?.capacity_leader || '-'}</div>
                  </div>
                  {candidate.key_stocks && candidate.key_stocks.length > 0 && (
                    <div className="mt-2 text-xs text-gray-600">
                      核心票：{candidate.key_stocks.join('、')}
                    </div>
                  )}
                  {candidate.evidence_text && (
                    <p className="mt-2 text-xs leading-relaxed text-gray-600">{candidate.evidence_text}</p>
                  )}
                </div>
              ))}
            </div>
            {projectionCandidates.length > 6 && (
              <button
                type="button"
                onClick={() => setShowAllCandidates(v => !v)}
                className="text-xs text-blue-600 hover:text-blue-800 cursor-pointer mt-2"
              >
                {showAllCandidates ? '收起' : `展开全部 (${projectionCandidates.length})`}
              </button>
            )}
          </PrefillBanner>
        </Section>
      )}

      <DynamicList
        title="当日最强板块"
        items={strongest}
        onChange={v => onChange({ ...d, strongest: v })}
        defaultItem={{ name: '', reason: '', vs_index: '', node: '', volume_trend: '', recognition: '', key_stocks: [] }}
        renderItem={(item, upd) => (
          <div className="space-y-3">
            <Row cols={3}>
              <TextField label="板块" value={item.name} onChange={v => upd('name', v)} />
              <TextField label="强势原因" value={item.reason} onChange={v => upd('reason', v)} />
              <SelectField label="vs指数" value={item.vs_index} onChange={v => upd('vs_index', v)} options={VS_INDEX} />
            </Row>
            <Row cols={4}>
              <SelectField label="节奏" value={item.node} onChange={v => upd('node', v)} options={SECTOR_NODE} />
              <SelectField label="成交量" value={item.volume_trend} onChange={v => upd('volume_trend', v)} options={VOL_TREND} />
              <SelectField label="辨识度" value={item.recognition} onChange={v => upd('recognition', v)} options={RECOGNITION} />
              <TagsField label="核心票" value={item.key_stocks || []} onChange={v => upd('key_stocks', v)} />
            </Row>
          </div>
        )}
      />

      <DynamicList
        title="异动板块"
        items={unusual}
        onChange={v => onChange({ ...d, unusual: v })}
        defaultItem={{ name: '', trigger: '', start_position: '', volume: '', key_stocks: [] }}
        renderItem={(item, upd) => (
          <div className="space-y-3">
            <Row cols={3}>
              <TextField label="板块" value={item.name} onChange={v => upd('name', v)} />
              <TextField label="触发原因" value={item.trigger} onChange={v => upd('trigger', v)} />
              <TextField label="启动位置" value={item.start_position} onChange={v => upd('start_position', v)} placeholder="低位首板 / 趋势加速 / 超跌反弹..." />
            </Row>
            <Row>
              <SelectField label="成交量" value={item.volume} onChange={v => upd('volume', v)} options={VOL_TREND} />
              <TagsField label="核心票" value={item.key_stocks || []} onChange={v => upd('key_stocks', v)} />
            </Row>
          </div>
        )}
      />

      <Section title="次日聚焦结论">
        <div className="space-y-4">
          <TextareaField
            label="最值得跟踪的板块"
            value={g('selection_summary')}
            onChange={v => s('selection_summary', v)}
            placeholder="一句话总结次日最值得跟踪的板块及原因"
            rows={2}
          />
          <DynamicList
            title="次日关注核心票"
            items={nextDayFocus}
            onChange={v => onChange({ ...d, next_day_focus: v })}
            defaultItem={{ sector_name: '', key_stocks: [], focus_reason: '' }}
            renderItem={(item, upd) => (
              <div className="space-y-3">
                <Row cols={3}>
                  <TextField label="板块" value={item.sector_name || ''} onChange={v => upd('sector_name', v)} />
                  <TagsField label="核心票" value={item.key_stocks || []} onChange={v => upd('key_stocks', v)} />
                  <TextField label="关注原因" value={item.focus_reason || ''} onChange={v => upd('focus_reason', v)} />
                </Row>
              </div>
            )}
          />
        </div>
      </Section>

      {/* ── 行业排行（来自盘后数据） ── */}
      {sectorIndustry && ((sectorIndustry.data?.length ?? 0) > 0 || (sectorIndustry.bottom?.length ?? 0) > 0) && (
        <PrefillBanner>
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs font-medium text-gray-600">行业板块排行（申万）</span>
            {date && (
              <Link to={`/market/${date}`} className="text-xs text-blue-500 hover:text-blue-700">
                完整市场数据 →
              </Link>
            )}
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            {(sectorIndustry.data?.length ?? 0) > 0 && (
              <div>
                <div className="text-xs text-green-600 font-medium mb-1">涨幅前列</div>
                <div className="space-y-0.5">
                  {sectorIndustry.data!.slice(0, 8).map((row, i) => (
                    <div key={i} className="flex justify-between text-xs text-gray-700">
                      <span>{row.name}</span>
                      <span className="text-green-600 font-medium">+{Number(row.change_pct ?? 0).toFixed(2)}%</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
            {(sectorIndustry.bottom?.length ?? 0) > 0 && (
              <div>
                <div className="text-xs text-red-500 font-medium mb-1">跌幅前列</div>
                <div className="space-y-0.5">
                  {sectorIndustry.bottom!.slice(0, 5).map((row, i) => (
                    <div key={i} className="flex justify-between text-xs text-gray-700">
                      <span>{row.name}</span>
                      <span className="text-red-500 font-medium">{Number(row.change_pct ?? 0).toFixed(2)}%</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        </PrefillBanner>
      )}

      {/* ── 行业信息/行业笔记（industry_info 表） ── */}
      {industryInfoList.length > 0 && (
        <PrefillBanner>
          <div className="text-xs font-medium text-gray-600 mb-2">近期行业信息（{industryInfoList.length} 条）</div>
          <div className="space-y-2">
            {(industryInfoExpanded ? industryInfoList : industryInfoList.slice(0, INDUSTRY_INFO_INITIAL_COUNT)).map((info, i) => (
              <div key={i} className="border-b border-amber-100 pb-2 last:border-0">
                <div className="flex flex-wrap items-center gap-2 mb-0.5">
                  <span className="text-xs font-medium text-gray-700">{info.sector_name}</span>
                  {info.info_type && <InfoTypeBadge type={info.info_type} />}
                  {info.date && <span className="text-xs text-gray-400">{info.date}</span>}
                  {info.confidence && <span className="text-xs text-gray-400">置信:{info.confidence}</span>}
                  {info.timeliness && <span className="text-xs text-gray-400">[{info.timeliness}]</span>}
                </div>
                <p className="text-xs text-gray-600 leading-relaxed">{info.content}</p>
                {info.source && <p className="text-xs text-gray-400 mt-0.5">来源：{info.source}</p>}
              </div>
            ))}
          </div>
          {industryInfoList.length > INDUSTRY_INFO_INITIAL_COUNT && (
            <button
              type="button"
              onClick={() => setIndustryInfoExpanded(v => !v)}
              className="mt-2 text-xs text-blue-500 hover:text-blue-700"
            >
              {industryInfoExpanded ? '收起' : `展开全部（共 ${industryInfoList.length} 条）`}
            </button>
          )}
        </PrefillBanner>
      )}

      <TextareaField label="补充备注" value={g('notes')} onChange={v => s('notes', v)} placeholder="板块相关补充观察..." rows={2} />
    </div>
  )
}
