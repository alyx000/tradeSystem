// market-timing 变盘点/底分型 文案与配色（指数卡片 + 择时汇总条共享，避免两处漂移）
import type { MarketTimingSignal } from '../../lib/types'

// 底分型「徽章」形态（emoji + 状态词），用于明细表 fractal 列。
// 注：汇总条的「N 成型 · M 确认 · K 破坏」是计数短语、确认/破坏高亮是整句叙述——
// 与本徽章是不同粒度的呈现，刻意不共用此常量（避免为统一而引入无谓间接层）。
export const FRACTAL_LABEL: Record<string, string> = {
  forming: '🟡 成型',
  confirmed: '🟢 确认',
  invalid: '⚪ 破坏',
  none: '—',
}

// 与最近斐波那契窗口的距离（命中=0；无 day_count 时 '?'）
function nearDiff(s: MarketTimingSignal): number | '?' {
  if (s.fib_near == null) return '?'
  return s.fib_day_count != null ? Math.abs(s.fib_day_count - s.fib_near) : '?'
}

export function fibTone(s: MarketTimingSignal): string {
  if (s.fib_hit != null) return 'text-red-600 font-medium'
  if (s.fib_near != null) return 'text-amber-600'
  return 'text-gray-400'
}

// 卡片内嵌的紧凑文案
export function fibTextCompact(s: MarketTimingSignal): string {
  if (s.fib_hit != null) return `🎯 变盘窗口 ${s.fib_hit}日`
  if (s.fib_near != null) return `⏳ 临近 ${s.fib_near}（差${nearDiff(s)}）`
  if (s.fib_day_count != null) return `未到（${s.fib_day_count}日）`
  return '—'
}

// 汇总条/明细表的完整文案
export function fibTextFull(s: MarketTimingSignal): string {
  if (s.fib_hit != null) return `🎯 变盘窗口·第${s.fib_hit}交易日`
  if (s.fib_near != null) return `⏳ 临近变盘窗口（斐波那契${s.fib_near}，差${nearDiff(s)}日）`
  if (s.fib_day_count != null) return `未到变盘窗口（第${s.fib_day_count}日）`
  return '—'
}

export function pivotText(s: MarketTimingSignal): string {
  if (!s.swing_pivot_date) return '—'
  const label = s.swing_pivot_type === 'high' ? '高点' : s.swing_pivot_type === 'low' ? '低点' : s.swing_pivot_type ?? ''
  return `${label} ${s.swing_pivot_date}${s.swing_pivot_price != null ? `（${s.swing_pivot_price}）` : ''}`
}
