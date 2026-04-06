import type { MainThemeItem } from '../../lib/types'

const PHASE_STYLE: Record<string, string> = {
  '超跌': 'bg-gray-100 text-gray-600',
  '启动': 'bg-blue-100 text-blue-700',
  '信不信加速': 'bg-blue-200 text-blue-800',
  '主升': 'bg-red-100 text-red-700',
  '首次分歧': 'bg-orange-100 text-orange-700',
  '震荡': 'bg-orange-100 text-orange-700',
  '轮动': 'bg-purple-100 text-purple-700',
}

export default function ThemeRhythmPanel({
  activeThemes,
}: {
  activeThemes: MainThemeItem[]
}) {
  if (activeThemes.length === 0) return null

  return (
    <div className="bg-white rounded-lg shadow p-4">
      <h2 className="text-sm font-semibold text-gray-700 mb-3">主线板块节奏</h2>
      <div className="space-y-2">
        {activeThemes.map((theme, i) => {
          let keyStocks: string[] = []
          try {
            const parsed = typeof theme.key_stocks === 'string' ? JSON.parse(theme.key_stocks) : theme.key_stocks
            if (Array.isArray(parsed)) keyStocks = parsed
          } catch {
            /* ignore malformed theme payloads */
          }
          const phaseStyle = theme.phase ? (PHASE_STYLE[theme.phase] || 'bg-gray-100 text-gray-600') : 'bg-gray-100 text-gray-600'
          return (
            <div key={i} className="flex flex-wrap items-center gap-2 py-1.5 border-b border-gray-50 last:border-0">
              <span className="font-medium text-gray-800 text-sm min-w-[6rem]">{theme.theme_name}</span>
              {theme.phase && (
                <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${phaseStyle}`}>
                  {theme.phase}
                </span>
              )}
              {theme.duration_days != null && (
                <span className="text-xs text-gray-400">{theme.duration_days}天</span>
              )}
              {keyStocks.length > 0 && (
                <div className="flex gap-1 flex-wrap">
                  {keyStocks.slice(0, 4).map((stock: string) => (
                    <span key={stock} className="bg-gray-100 text-gray-600 px-1.5 py-0.5 rounded text-xs">{stock}</span>
                  ))}
                </div>
              )}
              {theme.note && <span className="text-xs text-gray-400 flex-1">{theme.note}</span>}
            </div>
          )
        })}
      </div>
    </div>
  )
}
