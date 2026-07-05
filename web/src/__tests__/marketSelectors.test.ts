import { describe, expect, it } from 'vitest'
import {
  getEmotionSignals,
  getLimitStepRows,
  getMarketMoneyflowSummary,
  getMarketSignals,
  getSectorMoneyflowRows,
  parseBoardCounts,
} from '../components/market/marketSelectors'

describe('marketSelectors', () => {
  it('getLimitStepRows excludes ST/*ST/delisted stocks and sorts by nums desc', () => {
    const rows = getLimitStepRows({
      limit_step: {
        data: [
          { ts_code: '1', name: '*ST雅博', nums: '6' },
          { ts_code: '6', name: '国华退', nums: '5' },
          { ts_code: '2', name: '粤电力A', nums: '4' },
          { ts_code: '3', name: 'ST海王', nums: '3' },
          { ts_code: '4', name: '合锻智能', nums: '4' },
          { ts_code: '5', name: '深南电A', nums: '2' },
        ],
      },
    })
    // ST/*ST/退市股全剔除(与板梯队过滤口径一致),余下按连板数降序
    expect(rows.map((r) => r.name)).toEqual(['粤电力A', '合锻智能', '深南电A'])
  })

  it('parses board counts from json object and sorts by board desc', () => {
    const rows = parseBoardCounts(JSON.stringify({ 3: ['A', 'B'], 6: ['C'] }))

    expect(rows).toEqual([
      { board: 6, count: 1, stocks: ['C'] },
      { board: 3, count: 2, stocks: ['A', 'B'] },
    ])
  })

  it('normalizes DC sector moneyflow to yi and sorts descending', () => {
    const rows = getSectorMoneyflowRows({
      sector_moneyflow_dc: {
        data: [
          { name: '机器人', net_amount: 800000000 },
          { name: '人工智能', net_amount: 1800000000 },
        ],
      },
    }, 'dc')

    expect(rows.map((row) => ({ name: row.name, net_amount_yi: row.net_amount_yi }))).toEqual([
      { name: '人工智能', net_amount_yi: 18 },
      { name: '机器人', net_amount_yi: 8 },
    ])
  })

  it('extracts market moneyflow summary in yi', () => {
    const summary = getMarketMoneyflowSummary({
      market_moneyflow_dc: {
        data: [
          { net_amount: 2500000000, net_amount_rate: 2.8, buy_elg_amount: 1200000000, buy_lg_amount: 800000000 },
        ],
      },
    })

    expect(summary).toEqual({
      netAmountYi: 25,
      netAmountRate: 2.8,
      superLargeYi: 12,
      largeYi: 8,
    })
  })

  it('builds market status signals from amount, breadth, moneyflow and ma states', () => {
    const signals = getMarketSignals({
      date: '2026-04-03',
      total_amount: 11800,
      advance_count: 3500,
      decline_count: 1500,
      sh_above_ma5w: true,
      sz_above_ma5w: true,
      chinext_above_ma5w: false,
      star50_above_ma5w: true,
      avg_price_above_ma5w: true,
      market_moneyflow_dc: {
        data: [
          { net_amount: 2500000000 },
        ],
      },
    }, [
      { date: '2026-04-02', total_amount: 11200 },
    ])

    expect(signals.map((signal) => signal.value)).toEqual(['放量', '普涨', '净流入', '线上占优'])
  })

  it('builds emotion status signals from limit stats and strongest sectors', () => {
    const signals = getEmotionSignals(
      {
        limit_up_count: 96,
        limit_down_count: 4,
        seal_rate: 82.5,
        highest_board: 6,
      },
      [
        { name: '人工智能', up_nums: 12, cons_nums: 4 },
      ],
      [
        { name: '高标A', nums: '6' },
      ],
    )

    expect(signals.map((signal) => signal.value)).toEqual(['涨停扩散', '封板稳', '高标打开', '主线集中'])
  })
})
