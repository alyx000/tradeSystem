import { type ReactNode, useState } from 'react'

export interface StepProps {
  data: Record<string, any>
  onChange: (data: Record<string, any>) => void
  prefill?: any
}

export function get(obj: any, path: string, fallback: any = ''): any {
  return path.split('.').reduce((o, k) => o?.[k], obj) ?? fallback
}

export function set(obj: any, path: string, value: any): Record<string, any> {
  const result = { ...(obj || {}) }
  const keys = path.split('.')
  let cur: any = result
  for (let i = 0; i < keys.length - 1; i++) {
    cur[keys[i]] = { ...(cur[keys[i]] || {}) }
    cur = cur[keys[i]]
  }
  cur[keys[keys.length - 1]] = value
  return result
}

/* ── Layout ───────────────────────────────────────── */

export function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div>
      <h3 className="text-sm font-semibold text-gray-700 border-b border-gray-200 pb-1 mb-3">{title}</h3>
      {children}
    </div>
  )
}

export function Row({ children, cols = 2 }: { children: ReactNode; cols?: 2 | 3 | 4 }) {
  const cls = cols === 4 ? 'md:grid-cols-4' : cols === 3 ? 'md:grid-cols-3' : 'md:grid-cols-2'
  return <div className={`grid grid-cols-1 ${cls} gap-4`}>{children}</div>
}

export function PrefillBanner({ children }: { children: ReactNode }) {
  return (
    <div className="bg-gray-50 rounded-lg p-4 text-sm text-gray-500">
      <div className="text-xs font-medium text-gray-400 mb-2">自动预填充 · 仅供参考</div>
      {children}
    </div>
  )
}

export function Metric({ label, value, change, suffix }: {
  label: string; value: any; change?: number | null; suffix?: string
}) {
  return (
    <div>
      <div className="text-xs text-gray-400">{label}</div>
      <div className="text-sm font-medium text-gray-700">
        {value ?? '-'}{suffix && ` ${suffix}`}
        {change != null && (
          <span className={`ml-1 ${change >= 0 ? 'text-red-500' : 'text-green-600'}`}>
            {change >= 0 ? '+' : ''}{change}%
          </span>
        )}
      </div>
    </div>
  )
}

/* ── Fields ───────────────────────────────────────── */

export function TextField({ label, value, onChange, placeholder }: {
  label: string; value: string; onChange: (v: string) => void; placeholder?: string
}) {
  return (
    <label className="block">
      {label && <span className="block text-sm font-medium text-gray-600 mb-1">{label}</span>}
      <input type="text" value={value || ''} onChange={e => onChange(e.target.value)}
        placeholder={placeholder}
        className="w-full border border-gray-300 rounded px-3 py-1.5 text-sm focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none" />
    </label>
  )
}

export function NumberField({ label, value, onChange, placeholder, suffix }: {
  label: string; value: number | null; onChange: (v: number | null) => void; placeholder?: string; suffix?: string
}) {
  return (
    <label className="block">
      {label && <span className="block text-sm font-medium text-gray-600 mb-1">{label}</span>}
      <div className="flex items-center gap-1">
        <input type="number" value={value ?? ''} onChange={e => onChange(e.target.value ? Number(e.target.value) : null)}
          placeholder={placeholder} step="any"
          className="w-full border border-gray-300 rounded px-3 py-1.5 text-sm focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none" />
        {suffix && <span className="text-sm text-gray-500 shrink-0">{suffix}</span>}
      </div>
    </label>
  )
}

export function SelectField({ label, value, onChange, options, placeholder }: {
  label: string; value: string; onChange: (v: string) => void
  options: { value: string; label: string }[]; placeholder?: string
}) {
  return (
    <label className="block">
      {label && <span className="block text-sm font-medium text-gray-600 mb-1">{label}</span>}
      <select value={value || ''} onChange={e => onChange(e.target.value)}
        className="w-full border border-gray-300 rounded px-3 py-1.5 text-sm bg-white focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none">
        <option value="">{placeholder || '请选择'}</option>
        {options.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>
    </label>
  )
}

export function TextareaField({ label, value, onChange, placeholder, rows = 3 }: {
  label: string; value: string; onChange: (v: string) => void; placeholder?: string; rows?: number
}) {
  return (
    <label className="block">
      {label && <span className="block text-sm font-medium text-gray-600 mb-1">{label}</span>}
      <textarea value={value || ''} onChange={e => onChange(e.target.value)}
        placeholder={placeholder} rows={rows}
        className="w-full border border-gray-300 rounded px-3 py-2 text-sm resize-y focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none" />
    </label>
  )
}

export function CheckField({ label, checked, onChange }: {
  label: string; checked: boolean; onChange: (v: boolean) => void
}) {
  return (
    <label className="inline-flex items-center gap-2 text-sm text-gray-700 cursor-pointer">
      <input type="checkbox" checked={!!checked} onChange={e => onChange(e.target.checked)}
        className="rounded border-gray-300 text-blue-600 focus:ring-blue-500" />
      {label}
    </label>
  )
}

export function TagsField({ label, value, onChange, placeholder }: {
  label: string; value: string[]; onChange: (v: string[]) => void; placeholder?: string
}) {
  return (
    <label className="block">
      {label && <span className="block text-sm font-medium text-gray-600 mb-1">{label}</span>}
      <input type="text" value={(value || []).join('，')}
        onChange={e => onChange(e.target.value ? e.target.value.split(/[,，]/).map(s => s.trim()).filter(Boolean) : [])}
        placeholder={placeholder || '中文逗号分隔'}
        className="w-full border border-gray-300 rounded px-3 py-1.5 text-sm focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none" />
    </label>
  )
}

/* ── Teacher Notes Panel ──────────────────────────── */

const FIELD_LABELS: Record<string, string> = {
  core_view: '核心观点',
  sectors: '关注板块',
  key_points: '要点',
  position_advice: '仓位建议',
  avoid: '回避',
}

export function TeacherNotesPanel({
  notes,
  fields = ['core_view', 'sectors', 'key_points', 'position_advice', 'avoid'],
}: {
  notes: any[]
  fields?: string[]
}) {
  const [collapsed, setCollapsed] = useState(false)
  if (!notes?.length) return null

  return (
    <div className="bg-amber-50 border border-amber-200 rounded-lg p-3 text-sm">
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs font-medium text-amber-700">老师观点参考</span>
        <button
          type="button"
          onClick={() => setCollapsed(c => !c)}
          className="text-xs text-amber-500 hover:text-amber-700"
        >
          {collapsed ? '展开' : '收起'}
        </button>
      </div>
      {!collapsed && (
        <div className="space-y-3">
          {notes.map((n: any) => (
            <div key={n.id} className="border-l-2 border-amber-300 pl-3">
              <div className="font-medium text-gray-800 text-xs mb-1">
                {n.teacher_name} · {n.title}
              </div>
              {fields.map(f => n[f] ? (
                <div key={f} className="text-gray-600 text-xs mt-0.5">
                  <span className="text-amber-600 font-medium">{FIELD_LABELS[f] ?? f}：</span>
                  {n[f]}
                </div>
              ) : null)}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

/* ── Dynamic List ─────────────────────────────────── */

export function DynamicList<T extends Record<string, any>>({ title, items, onChange, defaultItem, renderItem }: {
  title: string
  items: T[]
  onChange: (items: T[]) => void
  defaultItem: T
  renderItem: (item: T, update: (field: string, value: any) => void) => ReactNode
}) {
  const list = items || []
  const add = () => onChange([...list, { ...defaultItem }])
  const remove = (i: number) => onChange(list.filter((_, idx) => idx !== i))
  const update = (i: number) => (field: string, value: any) => {
    const next = [...list]
    next[i] = { ...next[i], [field]: value }
    onChange(next)
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-2">
        <h3 className="text-sm font-semibold text-gray-700">{title}</h3>
        <button type="button" onClick={add} className="text-sm text-blue-600 hover:text-blue-800 font-medium">+ 添加</button>
      </div>
      {list.length === 0 ? (
        <div className="text-sm text-gray-400 text-center py-6 border border-dashed border-gray-300 rounded-lg">
          暂无数据，点击「+ 添加」新增
        </div>
      ) : (
        <div className="space-y-3">
          {list.map((item, i) => (
            <div key={i} className="border border-gray-200 rounded-lg p-3 relative group bg-white">
              <button type="button" onClick={() => remove(i)}
                className="absolute top-2 right-2 text-xs text-gray-300 hover:text-red-500 opacity-0 group-hover:opacity-100 transition-opacity">
                删除
              </button>
              {renderItem(item, update(i))}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
