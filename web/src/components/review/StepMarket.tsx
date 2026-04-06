import { Link } from 'react-router-dom'
import { type StepProps, Section, Row, PrefillBanner, Metric, SelectField, TextField, NumberField, TextareaField, TeacherNotesPanel } from './widgets'
import { get, set } from './formState'

const TREND = [
  { value: '主升', label: '主升' },
  { value: '震荡', label: '震荡' },
  { value: '下降', label: '下降' },
]
const MA5W = [
  { value: '线上', label: '5周均线上' },
  { value: '线下', label: '5周均线下' },
]
const VOL_CHANGE = [
  { value: '放量', label: '放量' },
  { value: '缩量', label: '缩量' },
  { value: '持平', label: '持平' },
]
const VOL_VS = [
  { value: '高于', label: '高于' },
  { value: '低于', label: '低于' },
  { value: '持平', label: '持平' },
]
const POSITION = [
  { value: '空仓', label: '空仓' },
  { value: '1成', label: '1成' },
  { value: '2成', label: '2成' },
  { value: '3成', label: '3成' },
  { value: '5成', label: '5成' },
  { value: '7成', label: '7成' },
  { value: '满仓', label: '满仓' },
]

const AMOUNT_THRESHOLD = 0.05

function deriveVolChange(cur: number | null | undefined, prev: number | null | undefined): string {
  if (cur == null || prev == null || prev === 0) return ''
  const ratio = (cur - prev) / prev
  if (ratio > AMOUNT_THRESHOLD) return '放量'
  if (ratio < -AMOUNT_THRESHOLD) return '缩量'
  return '持平'
}

function deriveVolVs(cur: number | null | undefined, avg: number | null | undefined): string {
  if (cur == null || avg == null || avg === 0) return ''
  const ratio = (cur - avg) / avg
  if (ratio > AMOUNT_THRESHOLD) return '高于'
  if (ratio < -AMOUNT_THRESHOLD) return '低于'
  return '持平'
}

export default function StepMarket({ data, onChange, prefill }: StepProps) {
  const d = data || {}
  const m = prefill?.market
  const pm = prefill?.prev_market
  const marketSignals = prefill?.review_signals?.market

  const teacherNotes = prefill?.teacher_notes || []

  const g = <T = string,>(p: string, fb?: T) => {
    const fallback = (fb ?? '') as T
    const val = get<T | undefined>(d, p, undefined)
    if (val !== undefined && val !== '') return val

    if (m) {
      if (p === 'volume.vs_yesterday') return deriveVolChange(m.total_amount, pm?.total_amount) as T
      if (p === 'volume.vs_5day_avg') return deriveVolVs(m.total_amount, prefill?.avg_5d_amount) as T
      if (p === 'volume.vs_20day_avg') return deriveVolVs(m.total_amount, prefill?.avg_20d_amount) as T
      if (p === 'direction.ma5w') return (m.sh_above_ma5w ? '线上' : m.sh_above_ma5w === false ? '线下' : '') as T
    }
    if (p === 'notes' && teacherNotes.length) {
      const views = teacherNotes
        .map((n) => n.core_view)
        .filter((view): view is string => Boolean(view))
      if (views.length) return views.map((v, i) => `【${teacherNotes[i].teacher_name}】${v}`).join('\n') as T
    }
    return fallback
  }
  const s = (p: string, v: unknown) => onChange(set(d, p, v))

  const fmtAmount = (v: number | null | undefined) =>
    v != null ? (v >= 10000 ? `${(v / 10000).toFixed(2)}万亿` : `${v.toFixed(0)}亿`) : '-'
  const fmtSignedYi = (v: number | null | undefined) => {
    if (v == null) return '-'
    const sign = v >= 0 ? '+' : ''
    return `${sign}${v.toFixed(2)}亿`
  }
  const fmtPercent = (v: number | null | undefined) => (v != null ? `${v.toFixed(2)}%` : '-')

  return (
    <div className="space-y-6">
      <TeacherNotesPanel notes={teacherNotes} fields={['core_view']} />
      {m && (
        <PrefillBanner>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <Metric label="上证" value={m.sh_index_close} change={m.sh_index_change_pct} />
            <Metric label="深证" value={m.sz_index_close} change={m.sz_index_change_pct} />
            <Metric label="成交额" value={fmtAmount(m.total_amount)} />
            <Metric label="北向净额" value={m.northbound_net} suffix="亿" />
          </div>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mt-2">
            <Metric label="涨" value={m.advance_count} />
            <Metric label="跌" value={m.decline_count} />
            <Metric label="涨停" value={m.limit_up_count} />
            <Metric label="跌停" value={m.limit_down_count} />
          </div>
          {(prefill?.avg_5d_amount || prefill?.avg_20d_amount) && (
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mt-2">
              <Metric label="5日均额" value={fmtAmount(prefill.avg_5d_amount)} />
              <Metric label="20日均额" value={fmtAmount(prefill.avg_20d_amount)} />
            </div>
          )}
          {prefill?.date && (
            <div className="mt-2 text-right">
              <Link to={`/market/${prefill.date}`} className="text-xs text-blue-500 hover:text-blue-700">
                查看完整市场数据 &rarr;
              </Link>
            </div>
          )}
        </PrefillBanner>
      )}

      {marketSignals?.moneyflow_summary && (
        <PrefillBanner>
          <div className="text-xs font-medium text-gray-600 mb-2">主力资金流向</div>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <Metric label="主力净额" value={fmtSignedYi(marketSignals.moneyflow_summary.net_amount_yi)} />
            <Metric label="净占比" value={fmtPercent(marketSignals.moneyflow_summary.net_amount_rate)} />
            <Metric label="超大单" value={fmtSignedYi(marketSignals.moneyflow_summary.super_large_yi)} />
            <Metric label="大单" value={fmtSignedYi(marketSignals.moneyflow_summary.large_yi)} />
          </div>
        </PrefillBanner>
      )}

      {(marketSignals?.market_structure_rows?.length ?? 0) > 0 && (
        (() => {
          const structureRows = marketSignals?.market_structure_rows ?? []
          return (
            <PrefillBanner>
              <div className="text-xs font-medium text-gray-600 mb-2">市场交易结构</div>
              <div className="overflow-x-auto">
                <table className="min-w-full text-xs text-gray-600">
                  <thead>
                    <tr className="text-left text-gray-400">
                      <th className="py-1 pr-4 font-medium">市场</th>
                      <th className="py-1 pr-4 font-medium text-right">成交额</th>
                      <th className="py-1 font-medium text-right">成交量</th>
                    </tr>
                  </thead>
                  <tbody>
                    {structureRows.map((row) => (
                      <tr key={row.name} className="border-t border-gray-200/70">
                        <td className="py-1.5 pr-4 font-medium text-gray-700">{row.name}</td>
                        <td className="py-1.5 pr-4 text-right">{row.amount ?? '-'}</td>
                        <td className="py-1.5 text-right">{row.volume ?? '-'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </PrefillBanner>
          )
        })()
      )}

      <Section title="成交量对比">
        {m && (
          <p className="text-xs text-amber-600 mb-2">
            已根据行情数据自动推算，可直接修改
          </p>
        )}
        <Row cols={3}>
          <SelectField label="较昨日" value={g('volume.vs_yesterday')} onChange={v => s('volume.vs_yesterday', v)} options={VOL_CHANGE} />
          <SelectField label="较5日均量" value={g('volume.vs_5day_avg')} onChange={v => s('volume.vs_5day_avg', v)} options={VOL_VS} />
          <SelectField label="较20日均量" value={g('volume.vs_20day_avg')} onChange={v => s('volume.vs_20day_avg', v)} options={VOL_VS} />
        </Row>
      </Section>

      <Section title="大盘方向">
        <Row cols={4}>
          <SelectField label="趋势" value={g('direction.trend')} onChange={v => s('direction.trend', v)} options={TREND} />
          <SelectField label="5周均线" value={g('direction.ma5w')} onChange={v => s('direction.ma5w', v)} options={MA5W} />
          <NumberField label="支撑位" value={g('direction.support', null)} onChange={v => s('direction.support', v)} />
          <NumberField label="压力位" value={g('direction.resistance', null)} onChange={v => s('direction.resistance', v)} />
        </Row>
      </Section>

      <Section title="节点判断">
        <Row>
          <TextField label="当前节点" value={g('node.current')} onChange={v => s('node.current', v)} placeholder="止跌反弹 / 突破 / 回踩 / 高位震荡..." />
          <TextField label="下一步预期" value={g('node.expectation')} onChange={v => s('node.expectation', v)} />
        </Row>
      </Section>

      <Section title="仓位锚定">
        <Row>
          <SelectField label="建议仓位" value={g('position.suggested')} onChange={v => s('position.suggested', v)} options={POSITION} />
          <TextField label="原因" value={g('position.reason')} onChange={v => s('position.reason', v)} />
        </Row>
      </Section>

      <TextareaField label="补充备注" value={g('notes')} onChange={v => s('notes', v)} placeholder="其他大盘观察..." rows={2} />
    </div>
  )
}
