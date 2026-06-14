import { describe, expect, it } from 'vitest'
import { fibTextCompact, fibTextFull, fibTone, pivotText } from '../components/market/marketTimingFormat'
import type { MarketTimingSignal } from '../lib/types'

function sig(over: Partial<MarketTimingSignal>): MarketTimingSignal {
  return {
    index_code: 'X', index_name: 'X指数', close: null, change_pct: null,
    swing_pivot_date: null, swing_pivot_type: null, swing_pivot_price: null,
    fib_day_count: null, fib_hit: null, fib_near: null,
    fractal_status: 'none', fractal_low_date: null, fractal_low_price: null, fractal_confirm_date: null,
    ...over,
  }
}

describe('marketTimingFormat', () => {
  it('命中：紧凑/完整文案 + 红色强调', () => {
    const s = sig({ fib_hit: 21, fib_day_count: 21 })
    expect(fibTextCompact(s)).toBe('🎯 变盘窗口 21日')
    expect(fibTextFull(s)).toBe('🎯 变盘窗口·第21交易日')
    expect(fibTone(s)).toContain('text-red-600')
  })

  it('临近：差值由 day_count 与 near 计算 + 琥珀色', () => {
    const s = sig({ fib_near: 8, fib_day_count: 7 })
    expect(fibTextCompact(s)).toBe('⏳ 临近 8（差1）')
    expect(fibTextFull(s)).toBe('⏳ 临近变盘窗口（斐波那契8，差1日）')
    expect(fibTone(s)).toContain('text-amber-600')
  })

  it('未到：仅有 day_count + 灰色', () => {
    const s = sig({ fib_day_count: 45 })
    expect(fibTextCompact(s)).toBe('未到（45日）')
    expect(fibTextFull(s)).toBe('未到变盘窗口（第45日）')
    expect(fibTone(s)).toContain('text-gray-400')
  })

  it('缺 day_count 时差值降级为 ?，不抛错', () => {
    const s = sig({ fib_near: 13, fib_day_count: null })
    expect(fibTextCompact(s)).toBe('⏳ 临近 13（差?）')
    expect(fibTextFull(s)).toBe('⏳ 临近变盘窗口（斐波那契13，差?日）')
  })

  it('pivotText：高/低点 + 价格事实；缺日期为 —', () => {
    expect(pivotText(sig({ swing_pivot_date: '2026-05-14', swing_pivot_type: 'high', swing_pivot_price: 4258.86 })))
      .toBe('高点 2026-05-14（4258.86）')
    expect(pivotText(sig({ swing_pivot_date: '2026-05-20', swing_pivot_type: 'low', swing_pivot_price: 3300 })))
      .toBe('低点 2026-05-20（3300）')
    expect(pivotText(sig({}))).toBe('—')
  })
})
