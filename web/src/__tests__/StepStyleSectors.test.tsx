/**
 * StepStyle 与 StepSectors 最小渲染测试
 * 覆盖：摘要展示、可推导项自动预填、空数据时不报错
 */
import { render, screen, within } from '@testing-library/react'
import { describe, it, expect, vi } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import StepStyle from '../components/review/StepStyle'
import StepSectors from '../components/review/StepSectors'

// ── helpers ──────────────────────────────────────────────────

const noop = () => {}

function renderStyle(prefill: any, data: any = {}) {
  return render(
    <StepStyle data={data} onChange={noop} prefill={prefill} />
  )
}

function renderSectors(prefill: any, data: any = {}) {
  return render(
    <MemoryRouter>
      <StepSectors data={data} onChange={noop} prefill={prefill} />
    </MemoryRouter>
  )
}

// ── 测试数据 ──────────────────────────────────────────────────

const stylePrefill = {
  date: '2026-05-20',
  market: {
    premium_10cm: 0.95,
    premium_20cm: 1.59,
    premium_30cm: null,
    premium_second_board: 7.18,
    style_factors: {
      cap_preference: { relative: '偏大盘', spread: -0.77, csi300_chg: -1.04, csi1000_chg: -1.81 },
      board_preference: { dominant_type: '10cm', pct_10cm: 90.9, pct_20cm: 4.5, pct_30cm: 4.5 },
      premium_trend: { direction: '震荡', first_board_median_5d: [0.95, 0.92] },
      switch_signals: ['大盘股跑赢小盘股，审美偏向容量票'],
    },
  },
}

const sectorsPrefill = {
  date: '2026-05-20',
  main_themes: [],
  teacher_notes: [],
  market: {
    sector_industry: {
      data: [{ name: '油服工程', change_pct: 4.68 }, { name: '养殖业', change_pct: 2.31 }],
      bottom: [{ name: 'IT服务', change_pct: -3.59 }],
    },
    sector_rhythm_industry: [
      { name: '油服工程', phase: '启动', rank_today: 1, change_today: 4.68, confidence: '中' },
      { name: '养殖业', phase: '启动', rank_today: 2, change_today: 2.31, confidence: '高' },
    ],
  },
  industry_info: [
    { sector_name: '油服', info_type: 'news', content: '油服资金流入', date: '2026-05-20', confidence: '高' },
    { sector_name: '储能', info_type: 'analysis', content: '储能政策利好', date: '2026-05-19' },
    { sector_name: '锂电', info_type: 'news', content: '锂电回调', date: '2026-05-18' },
    { sector_name: '新能源车', info_type: 'news', content: '销量超预期', date: '2026-05-17' },
  ],
}

// ══════════════════════════════════════════════════════════════
// StepStyle 测试
// ══════════════════════════════════════════════════════════════

describe('StepStyle', () => {
  it('空 prefill 时不报错，能正常渲染', () => {
    expect(() => renderStyle(undefined)).not.toThrow()
    expect(screen.getByText('各风格赚钱效应')).toBeInTheDocument()
  })

  it('有溢价率时展示摘要 banner', () => {
    renderStyle(stylePrefill)
    // 标签文字在 banner 和表单里各出现一次，用 getAllByText
    expect(screen.getAllByText('10cm首板溢价').length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText('20cm首板溢价').length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText('二板溢价').length).toBeGreaterThanOrEqual(1)
  })

  it('展示 cap_preference 摘要（偏大盘）', () => {
    renderStyle(stylePrefill)
    // 验证 cap_preference 的值"偏大盘"出现在页面中（banner 特有的值，非 label）
    expect(screen.getByText('偏大盘')).toBeInTheDocument()
    expect(screen.getByText('沪深300')).toBeInTheDocument()
  })

  it('展示 board_preference 摘要（10cm）', () => {
    renderStyle(stylePrefill)
    expect(screen.getByText('板型主导')).toBeInTheDocument()
    expect(screen.getByText('10cm')).toBeInTheDocument()
  })

  it('展示溢价趋势方向', () => {
    renderStyle(stylePrefill)
    expect(screen.getByText(/溢价趋势/)).toBeInTheDocument()
    expect(screen.getByText(/震荡/)).toBeInTheDocument()
  })

  it('展示 switch_signals', () => {
    renderStyle(stylePrefill)
    expect(screen.getByText(/大盘股跑赢小盘股/)).toBeInTheDocument()
  })

  it('无 style_factors 时仅展示溢价行，不显示 cap_preference 值', () => {
    const p = { market: { premium_10cm: 1.2, premium_20cm: null, premium_30cm: null, premium_second_board: null } }
    renderStyle(p)
    expect(screen.getAllByText('10cm首板溢价').length).toBeGreaterThanOrEqual(1)
    // "偏大盘" / "沪深300" 是 cap_preference 特有的值，无 style_factors 时不应出现
    expect(screen.queryByText('偏大盘')).not.toBeInTheDocument()
    expect(screen.queryByText('沪深300')).not.toBeInTheDocument()
  })

  it('market 为 null 时不渲染 banner', () => {
    renderStyle({ market: null })
    expect(screen.queryByText('10cm首板溢价')).not.toBeInTheDocument()
  })
})

// ══════════════════════════════════════════════════════════════
// StepSectors 测试
// ══════════════════════════════════════════════════════════════

describe('StepSectors', () => {
  it('空 prefill 时不报错，能正常渲染', () => {
    expect(() => renderSectors(undefined)).not.toThrow()
    expect(screen.getByText('主线板块')).toBeInTheDocument()
  })

  it('展示行业排行涨幅前列', () => {
    renderSectors(sectorsPrefill)
    expect(screen.getByText('行业板块排行（申万）')).toBeInTheDocument()
    // 油服工程和养殖业在排行和节奏中都会出现，用 getAllByText
    expect(screen.getAllByText('油服工程').length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText('养殖业').length).toBeGreaterThanOrEqual(1)
  })

  it('展示行业排行跌幅前列', () => {
    renderSectors(sectorsPrefill)
    expect(screen.getByText('IT服务')).toBeInTheDocument()
  })

  it('展示行业节奏信号', () => {
    renderSectors(sectorsPrefill)
    expect(screen.getByText('行业节奏信号（当日前列）')).toBeInTheDocument()
    expect(screen.getAllByText('启动').length).toBeGreaterThan(0)
  })

  it('展示 industry_info 列表', () => {
    renderSectors(sectorsPrefill)
    expect(screen.getByText('近期行业信息（4 条）')).toBeInTheDocument()
    expect(screen.getByText('油服')).toBeInTheDocument()
    expect(screen.getByText('油服资金流入')).toBeInTheDocument()
  })

  it('industry_info 超过 3 条时有展开按钮，收起时只显示前 3 条内容', () => {
    renderSectors(sectorsPrefill)
    // 第 4 条「销量超预期」默认不可见
    expect(screen.queryByText('销量超预期')).not.toBeInTheDocument()
    const expandBtn = screen.getByText(/展开全部/)
    expect(expandBtn).toBeInTheDocument()
  })

  it('无行业信息时不渲染 industry_info 区块', () => {
    const p = { ...sectorsPrefill, industry_info: [] }
    renderSectors(p)
    expect(screen.queryByText(/近期行业信息/)).not.toBeInTheDocument()
  })

  it('sector_industry 为空时不渲染排行区块', () => {
    const p = { ...sectorsPrefill, market: { ...sectorsPrefill.market, sector_industry: undefined } }
    renderSectors(p)
    expect(screen.queryByText('行业板块排行（申万）')).not.toBeInTheDocument()
  })

  it('sector_rhythm_industry 为空时不渲染节奏区块', () => {
    const p = { ...sectorsPrefill, market: { ...sectorsPrefill.market, sector_rhythm_industry: [] } }
    renderSectors(p)
    expect(screen.queryByText('行业节奏信号（当日前列）')).not.toBeInTheDocument()
  })

  it('显示「完整市场数据」链接并指向正确日期', () => {
    renderSectors(sectorsPrefill)
    const link = screen.getByText('完整市场数据 →')
    expect(link).toBeInTheDocument()
    expect(link.closest('a')).toHaveAttribute('href', '/market/2026-05-20')
  })
})
