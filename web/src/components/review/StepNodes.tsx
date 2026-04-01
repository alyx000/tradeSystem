import { type StepProps, get, set, Section, TextField, TextareaField } from './widgets'

export default function StepNodes({ data, onChange }: StepProps) {
  const d = data || {}
  const g = (p: string, fb: any = '') => get(d, p, fb)
  const s = (p: string, v: any) => onChange(set(d, p, v))

  return (
    <div className="space-y-6">
      <Section title="各维度节点">
        <div className="space-y-4">
          <TextField label="大盘节点" value={g('market_node')} onChange={v => s('market_node', v)} placeholder="止跌反弹 / 突破前高 / 回踩确认..." />
          <TextField label="板块节点" value={g('sector_node')} onChange={v => s('sector_node', v)} placeholder="主线启动日 / 首次分歧 / 高潮日..." />
          <TextField label="风格化节点" value={g('style_node')} onChange={v => s('style_node', v)} placeholder="风格切换点 / 审美偏好转变..." />
        </div>
      </Section>

      <TextareaField label="综合节点评估" value={g('overall')} onChange={v => s('overall', v)} rows={4}
        placeholder="综合各维度节点判断当前市场处于什么阶段..." />
    </div>
  )
}
