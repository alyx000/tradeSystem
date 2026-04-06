import { useState } from 'react'
import { Link } from 'react-router-dom'
import { type StepProps, Section, Row, PrefillBanner, SelectField, TextField, NumberField, TagsField, TextareaField, DynamicList, TeacherNotesPanel } from './widgets'
import { get, set } from './formState'
import type { IndustryInfoItem, MainThemeItem, SectorIndustryPrefill, SectorRhythmItem, TeacherNote } from '../../lib/types'

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

function phaseClass(phase: string) {
  return PHASE_COLOR[phase] ?? 'bg-gray-50 text-gray-600'
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
  const strongest = (d.strongest as StrongestSectorItem[] | undefined) || []
  const unusual = (d.unusual as UnusualSectorItem[] | undefined) || []

  const date = prefill?.date as string | undefined
  const sectorIndustry: SectorIndustryPrefill | undefined = prefill?.market?.sector_industry
  const sectorRhythm: SectorRhythmItem[] | undefined = prefill?.market?.sector_rhythm_industry
  const industryInfoList: IndustryInfoItem[] = prefill?.industry_info || []
  const sectorSignals = prefill?.review_signals?.sectors

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

      {(sectorSignals?.strongest_rows?.length ?? 0) > 0 && (
        (() => {
          const strongestRows = sectorSignals?.strongest_rows ?? []
          return (
            <PrefillBanner>
              <div className="text-xs font-medium text-gray-600 mb-2">当日最强板块</div>
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

      {((sectorSignals?.ths_moneyflow_rows?.length ?? 0) > 0 || (sectorSignals?.dc_moneyflow_rows?.length ?? 0) > 0) && (
        <PrefillBanner>
          <div className="text-xs font-medium text-gray-600 mb-2">板块资金确认</div>
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            {(sectorSignals?.ths_moneyflow_rows?.length ?? 0) > 0 && (
              <div>
                <div className="text-xs text-blue-600 font-medium mb-1.5">THS 行业资金流</div>
                <div className="space-y-1.5">
                  {sectorSignals!.ths_moneyflow_rows.map((row) => (
                    <div key={`${row.name}-${row.lead_stock ?? 'na'}`} className="rounded border border-gray-200 bg-white px-3 py-2">
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-xs font-medium text-gray-700">{row.name}</span>
                        <span className={`text-xs font-medium ${(row.net_amount ?? 0) >= 0 ? 'text-red-600' : 'text-green-600'}`}>
                          {fmtYi(row.net_amount != null ? row.net_amount / 1e8 : null)}
                        </span>
                      </div>
                      <div className="mt-0.5 flex items-center justify-between gap-2 text-xs text-gray-500">
                        <span>涨跌幅 {fmtPct(row.pct_change)}</span>
                        <span>{row.lead_stock ? `领涨股：${row.lead_stock}` : '领涨股：-'}</span>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
            {(sectorSignals?.dc_moneyflow_rows?.length ?? 0) > 0 && (
              <div>
                <div className="text-xs text-purple-600 font-medium mb-1.5">DC 板块资金流</div>
                <div className="space-y-1.5">
                  {sectorSignals!.dc_moneyflow_rows.map((row) => (
                    <div key={`${row.name}-${row.content_type ?? 'na'}`} className="rounded border border-gray-200 bg-white px-3 py-2">
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-xs font-medium text-gray-700">{row.name}</span>
                        <span className={`text-xs font-medium ${(row.net_amount_yi ?? 0) >= 0 ? 'text-red-600' : 'text-green-600'}`}>
                          {fmtYi(row.net_amount_yi)}
                        </span>
                      </div>
                      <div className="mt-0.5 flex items-center justify-between gap-2 text-xs text-gray-500">
                        <span>{row.content_type || '板块'}</span>
                        <span>涨跌幅 {fmtPct(row.pct_change)}</span>
                      </div>
                      <div className="mt-0.5 text-xs text-gray-500">
                        {row.lead_stock ? `领涨股：${row.lead_stock}` : '领涨股：-'}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        </PrefillBanner>
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

      {/* ── 行业节奏分析（sector_rhythm_industry） ── */}
      {sectorRhythm && sectorRhythm.length > 0 && (
        <PrefillBanner>
          <div className="text-xs font-medium text-gray-600 mb-2">行业节奏信号（当日前列）</div>
          <div className="space-y-1.5">
            {sectorRhythm.slice(0, 10).map((item, i) => (
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
              </div>
            ))}
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
