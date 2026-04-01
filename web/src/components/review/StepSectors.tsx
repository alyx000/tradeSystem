import { type StepProps, get, set, Section, Row, PrefillBanner, SelectField, TextField, NumberField, TagsField, TextareaField, DynamicList } from './widgets'

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

export default function StepSectors({ data, onChange, prefill }: StepProps) {
  const d = data || {}
  const themes = prefill?.main_themes || []
  const firstTheme = themes[0]

  const g = (p: string, fb: any = '') => {
    const val = get(d, p, undefined)
    if (val !== undefined && val !== '') return val

    if (firstTheme) {
      if (p === 'main_theme.name') return firstTheme.theme_name || ''
      if (p === 'main_theme.status') return firstTheme.status === 'active' ? '持续' : ''
      if (p === 'main_theme.duration_days') return firstTheme.duration_days ?? null
      if (p === 'main_theme.key_stocks') {
        if (typeof firstTheme.key_stocks === 'string') {
          try { return JSON.parse(firstTheme.key_stocks) } catch { return [] }
        }
        return firstTheme.key_stocks || []
      }
      if (p === 'main_theme.node') return firstTheme.phase || ''
    }
    return fb
  }
  const s = (p: string, v: any) => onChange(set(d, p, v))

  return (
    <div className="space-y-6">
      {themes.length > 0 && (
        <PrefillBanner>
          <div className="text-xs text-gray-500 mb-1">当前活跃主线（{themes.length} 条）</div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
            {themes.slice(0, 4).map((t: any) => (
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
        title="当日最强板块"
        items={d.strongest || []}
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
        items={d.unusual || []}
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

      <TextareaField label="补充备注" value={g('notes')} onChange={v => s('notes', v)} placeholder="板块相关补充观察..." rows={2} />
    </div>
  )
}
