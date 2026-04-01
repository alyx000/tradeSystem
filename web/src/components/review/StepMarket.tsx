import { type StepProps, get, set, Section, Row, PrefillBanner, Metric, SelectField, TextField, NumberField, TextareaField } from './widgets'

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

export default function StepMarket({ data, onChange, prefill }: StepProps) {
  const d = data || {}
  const g = (p: string, fb: any = '') => get(d, p, fb)
  const s = (p: string, v: any) => onChange(set(d, p, v))
  const m = prefill?.market

  return (
    <div className="space-y-6">
      {m && (
        <PrefillBanner>
          <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
            <Metric label="上证" value={m.sh_index_close} change={m.sh_index_change_pct} />
            <Metric label="深证" value={m.sz_index_close} change={m.sz_index_change_pct} />
            <Metric label="成交额" value={m.total_amount} suffix="亿" />
            <Metric label="涨停" value={m.limit_up_count} />
            <Metric label="跌停" value={m.limit_down_count} />
          </div>
        </PrefillBanner>
      )}

      <Section title="成交量对比">
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
