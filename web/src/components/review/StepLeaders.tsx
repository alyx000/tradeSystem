import { type StepProps, Section, Row, SelectField, TextField, CheckField, TextareaField, DynamicList } from './widgets'
import { get, set } from './formState'

interface TopLeaderItem {
  stock: string
  sector: string
  attribute: string
  clarity: string
  position: string
  is_new: boolean
}

const CLARITY = [
  { value: '一眼看出', label: '一眼看出' },
  { value: '需要辨别', label: '需要辨别' },
  { value: '不清晰', label: '不清晰' },
]
const POSITION = [
  { value: '启动', label: '启动' },
  { value: '主升', label: '主升' },
  { value: '高位', label: '高位' },
  { value: '分歧', label: '分歧' },
]

export default function StepLeaders({ data, onChange }: StepProps) {
  const d = data || {}
  const g = <T = string,>(p: string, fb?: T) => get<T>(d, p, (fb ?? '') as T)
  const s = (p: string, v: unknown) => onChange(set(d, p, v))
  const topLeaders = (d.top_leaders as TopLeaderItem[] | undefined) || []

  return (
    <div className="space-y-6">
      <DynamicList
        title="当前辨识度最高的最票"
        items={topLeaders}
        onChange={v => onChange({ ...d, top_leaders: v })}
        defaultItem={{ stock: '', sector: '', attribute: '', clarity: '', position: '', is_new: false }}
        renderItem={(item, upd) => (
          <div className="space-y-3">
            <Row cols={3}>
              <TextField label="股票" value={item.stock} onChange={v => upd('stock', v)} />
              <TextField label="所属板块" value={item.sector} onChange={v => upd('sector', v)} />
              <TextField label="最的属性" value={item.attribute} onChange={v => upd('attribute', v)} placeholder="走势引领 / 最先板 / 最高标..." />
            </Row>
            <Row cols={3}>
              <SelectField label="清晰度" value={item.clarity} onChange={v => upd('clarity', v)} options={CLARITY} />
              <SelectField label="当前位置" value={item.position} onChange={v => upd('position', v)} options={POSITION} />
              <div className="flex items-end pb-1">
                <CheckField label="新最" checked={item.is_new} onChange={v => upd('is_new', v)} />
              </div>
            </Row>
          </div>
        )}
      />

      <Section title="龙头更替">
        <Row cols={3}>
          <TextField label="旧龙头" value={g('transition.old')} onChange={v => s('transition.old', v)} />
          <TextField label="新龙头" value={g('transition.new')} onChange={v => s('transition.new', v)} />
          <TextField label="更替原因" value={g('transition.reason')} onChange={v => s('transition.reason', v)} />
        </Row>
      </Section>

      <TextareaField label="补充备注" value={g('notes')} onChange={v => s('notes', v)} placeholder="龙头相关补充..." rows={2} />
    </div>
  )
}
