import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import ConcentrationTrendPanel from '../components/market/ConcentrationTrendPanel'
import type { ConcentrationTrendPayload } from '../lib/types'

const payload: ConcentrationTrendPayload = {
  requested_days: 30,
  series: [
    { date: '2026-05-29', date_short: '05-29', cr3: 74.5, total_amount_billion: 4278.6,
      market_share_pct: 12.89, sectors: { 半导体: 31.2, 通信设备: 18.0, 其他: 50.8 } },
    { date: '2026-06-01', date_short: '06-01', cr3: 73.3, total_amount_billion: 3718.3,
      market_share_pct: 12.92, sectors: { 半导体: 29.3, 通信设备: 23.9, 其他: 46.8 } },
  ],
  sector_keys: ['半导体', '通信设备', '其他'],
  snapshot: {
    date: '2026-06-01',
    retention: [{ name: '中际旭创', streak: 20 }],
    rotation: {
      new: [{ name: '生益科技', industry: '元件', change_pct: -3.55 }],
      dropped: [{ name: '中天科技' }],
    },
  },
}

describe('ConcentrationTrendPanel', () => {
  it('renders nothing when series empty', () => {
    const { container } = render(
      <ConcentrationTrendPanel payload={{ requested_days: 30, series: [], sector_keys: [], snapshot: null }} />
    )
    expect(container).toBeEmptyDOMElement()
  })

  it('renders three chart titles + 异动概览 with enriched badges', () => {
    render(<ConcentrationTrendPanel payload={payload} />)
    // 三张趋势图标题
    expect(screen.getByText(/集中度 CR3 趋势/)).toBeInTheDocument()
    expect(screen.getByText('头部成交额 / 占两市')).toBeInTheDocument()
    expect(screen.getByText(/板块占比构成/)).toBeInTheDocument()
    // 图4 异动概览(纯 DOM)
    expect(screen.getByText(/头部异动/)).toBeInTheDocument()
    expect(screen.getByText('中际旭创')).toBeInTheDocument()    // 连续在榜
    expect(screen.getByText('20天')).toBeInTheDocument()
    expect(screen.getByText('生益科技')).toBeInTheDocument()     // 今日新进:名称
    expect(screen.getByText('元件')).toBeInTheDocument()         //          行业
    expect(screen.getByText('-3.55%')).toBeInTheDocument()       //          带符号涨跌
    expect(screen.getByText('中天科技')).toBeInTheDocument()     // 今日退出
  })

  it('hides 头部异动 when snapshot null but keeps charts', () => {
    render(<ConcentrationTrendPanel payload={{ ...payload, snapshot: null }} />)
    expect(screen.getByText(/集中度 CR3 趋势/)).toBeInTheDocument()
    expect(screen.queryByText(/头部异动/)).not.toBeInTheDocument()
  })
})
