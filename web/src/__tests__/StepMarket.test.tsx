import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import StepMarket from '../components/review/StepMarket'
import { api } from '../lib/api'
import type { ReviewPrefillData, ReviewStepValue } from '../lib/types'

function renderStep(data: ReviewStepValue = {}, prefill?: ReviewPrefillData) {
  const onChange = vi.fn()
  render(
    <MemoryRouter>
      <StepMarket data={data} onChange={onChange} prefill={prefill} />
    </MemoryRouter>
  )
  return { onChange }
}

const prefill: ReviewPrefillData = {
  date: '2026-04-03',
  market: {
    available: true,
    date: '2026-04-03',
    sh_index_close: 3210,
    sh_index_change_pct: 1.1,
    sz_index_close: 10020,
    sz_index_change_pct: 1.9,
    total_amount: 12000,
    advance_count: 3100,
    decline_count: 1600,
    sh_above_ma5w: true,
    sz_above_ma5w: true,
    chinext_above_ma5w: false,
    star50_above_ma5w: false,
    avg_price_above_ma5w: true,
    limit_up_count: 88,
    limit_down_count: 6,
    seal_rate: 81,
    broken_rate: 19,
    highest_board: 5,
    continuous_board_counts: null,
    premium_10cm: 2.1,
    premium_20cm: 3.2,
    premium_30cm: 1.1,
    premium_second_board: 4.4,
    northbound_net: 56.2,
    margin_balance: null,
    indices: {
      chinext: { close: 2333.1, change_pct: -2.15 },
      star50: { close: 1663.69, change_pct: -5.0 },
    },
    moving_averages: {
      avg_price: { ma5w: 32.01, above_ma5w: false },
    },
  },
  prev_market: {
    date: '2026-04-02',
    sh_index_close: 3170,
    sh_index_change_pct: -0.4,
    sz_index_close: 9890,
    sz_index_change_pct: -0.6,
    total_amount: 10000,
    advance_count: 2400,
    decline_count: 2200,
    sh_above_ma5w: true,
    sz_above_ma5w: true,
    chinext_above_ma5w: false,
    star50_above_ma5w: false,
    avg_price_above_ma5w: true,
    limit_up_count: 72,
    limit_down_count: 8,
    seal_rate: 76,
    broken_rate: 24,
    highest_board: 4,
    continuous_board_counts: null,
    premium_10cm: 1.2,
    premium_20cm: 2,
    premium_30cm: 0.7,
    premium_second_board: 3.6,
    northbound_net: 12.1,
    margin_balance: null,
  },
  avg_5d_amount: 10500,
  avg_20d_amount: 9800,
  teacher_notes: [
    {
      id: 1,
      teacher_id: 1,
      teacher_name: '小鲍',
      date: '2026-04-03',
      title: '早评',
      core_view: '量能配合较好',
      tags: null,
      sectors: null,
      created_at: '2026-04-03T07:00:00',
    },
  ],
  holdings: [],
  calendar_events: [],
  main_themes: [],
  review_signals: {
    market: {
      moneyflow_summary: {
        net_amount_yi: 6.5,
        net_amount_rate: 1.32,
        super_large_yi: 4.2,
        large_yi: 1.8,
      },
      market_structure_rows: [
        { name: '上海A股', amount: 6200, volume: 41000000, pe: 15.2, turnover_rate: 1.3, com_count: 1700 },
        { name: '深圳A股', amount: 5100, volume: 36000000, pe: 28.5, turnover_rate: 2.1, com_count: 2800 },
      ],
    },
    sectors: {
      strongest_rows: [],
      industry_moneyflow_rows: [],
      concept_moneyflow_rows: [],
    },
    emotion: {
      ladder_rows: [],
    },
  },
}

describe('StepMarket', () => {
  it('renders market prefill and derived defaults', () => {
    renderStep({}, prefill)

    expect(screen.getByText('老师观点参考')).toBeInTheDocument()
    expect(screen.getByText('量能配合较好')).toBeInTheDocument()
    expect(screen.getByLabelText('较昨日')).toHaveValue('放量')
    expect(screen.getByLabelText('较5日均量')).toHaveValue('高于')
    expect(screen.getByLabelText('较20日均量')).toHaveValue('高于')
    expect(screen.getByLabelText('5周均线')).toHaveValue('线上')
    expect(screen.getByDisplayValue('【小鲍】量能配合较好')).toBeInTheDocument()
    expect(screen.getByText('主力资金流向')).toBeInTheDocument()
    // 创业板指 / 科创50:取自 indices,与上证/深证同样式(close + 涨跌%)
    expect(screen.getByText('创业板指')).toBeInTheDocument()
    expect(screen.getByText('2333.1')).toBeInTheDocument()
    expect(screen.getByText('科创50')).toBeInTheDocument()
    expect(screen.getByText('1663.69')).toBeInTheDocument()
    // 平均股价:当日数值未落库,展示 5 周线 ma5w + 线上/线下
    expect(screen.getByText('平均股价')).toBeInTheDocument()
    expect(screen.getByText('32.01')).toBeInTheDocument()
    expect(screen.getByText('线下')).toBeInTheDocument()
    // A股市场结构块已移除(用户:无意义),market_structure_rows 即便存在也不渲染
    expect(screen.queryByText('A股市场结构')).not.toBeInTheDocument()
    expect(screen.queryByText('上海A股')).not.toBeInTheDocument()
    // 北向净额已下线(口径存疑),即便 northbound_net 有值也不渲染
    expect(screen.queryByText('北向净额')).not.toBeInTheDocument()
    expect(screen.getByText('+6.50亿')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /查看完整市场数据/i })).toHaveAttribute('href', '/market/2026-04-03')
  })

  it('degrades gracefully when indices and moving_averages are absent', () => {
    const noIdx: ReviewPrefillData = {
      ...prefill,
      market: { ...prefill.market!, indices: undefined, moving_averages: undefined },
    }
    renderStep({}, noIdx)
    // 创业板指/科创50 标签仍渲染,close 缺失时 Metric 降级为 '-'
    expect(screen.getByText('创业板指')).toBeInTheDocument()
    expect(screen.getByText('科创50')).toBeInTheDocument()
    // 平均股价整块在 ma5w 缺失时不渲染(不会出现裸 '线下')
    expect(screen.queryByText('平均股价')).not.toBeInTheDocument()
    expect(screen.queryByText('线下')).not.toBeInTheDocument()
  })

  it('shows non-trading-day warning when is_trading_day is false', () => {
    renderStep({}, { ...prefill, is_trading_day: false })
    expect(screen.getByText('当前日期为非交易日，市场数据可能为空或不准确')).toBeInTheDocument()
  })

  it('hides non-trading-day warning when is_trading_day is true', () => {
    renderStep({}, { ...prefill, is_trading_day: true })
    expect(screen.queryByText('当前日期为非交易日，市场数据可能为空或不准确')).not.toBeInTheDocument()
  })

  it('hides non-trading-day warning when is_trading_day is undefined', () => {
    renderStep({}, prefill)
    expect(screen.queryByText('当前日期为非交易日，市场数据可能为空或不准确')).not.toBeInTheDocument()
  })

  it('emits nested payload when user edits a field', () => {
    const { onChange } = renderStep({}, prefill)

    fireEvent.change(screen.getByLabelText('当前节点'), { target: { value: '突破前高' } })

    expect(onChange).toHaveBeenCalledWith({
      node: { current: '突破前高' },
    })
  })

  function withCoverage(rows: NonNullable<NonNullable<ReviewPrefillData['review_signals']>['market']>['research_coverage_top']): ReviewPrefillData {
    return {
      ...prefill,
      review_signals: {
        ...prefill.review_signals!,
        market: { ...prefill.review_signals!.market!, research_coverage_top: rows },
      },
    }
  }

  it('renders expanded research coverage rows with direction badge and viewpoint', () => {
    renderStep({}, withCoverage([
      { stock_code: '300999', stock_name: '新覆盖股', report_count: 1, expanded: true, rating_direction: '首次覆盖', viewpoint: '再融资落地，业务全面高增' },
      { stock_code: '000001', stock_name: '平安银行', report_count: 2 },
    ]))
    expect(screen.getByText('研报覆盖排行')).toBeInTheDocument()
    // 展开项：方向徽章 + 观点标题
    expect(screen.getByText(/首次覆盖/)).toBeInTheDocument()
    expect(screen.getByText(/再融资落地/)).toBeInTheDocument()
    // 长尾药丸：名称 + 篇数
    expect(screen.getByText('平安银行')).toBeInTheDocument()
  })

  it('degrades old coverage rows (only base fields) to plain pills', () => {
    renderStep({}, withCoverage([
      { stock_code: '000001', stock_name: '平安银行', report_count: 2 },
      { stock_code: '000002', stock_name: '万科A', report_count: 1 },
    ]))
    expect(screen.getByText('平安银行')).toBeInTheDocument()
    expect(screen.getByText('万科A')).toBeInTheDocument()
    // 无富化字段 → 不出现徽章/观点结构
    expect(screen.queryByText('首次覆盖')).not.toBeInTheDocument()
  })

  function withIndustry(
    rows: NonNullable<NonNullable<ReviewPrefillData['review_signals']>['market']>['research_coverage_top'],
    industry: NonNullable<NonNullable<ReviewPrefillData['review_signals']>['market']>['research_coverage_industry'],
  ): ReviewPrefillData {
    return {
      ...prefill,
      review_signals: {
        ...prefill.review_signals!,
        market: { ...prefill.review_signals!.market!, research_coverage_top: rows, research_coverage_industry: industry },
      },
    }
  }

  it('renders industry heat bar above the stock list', () => {
    renderStep({}, withIndustry(
      [{ stock_code: '601398', stock_name: '青岛银行', report_count: 2 }],
      [
        { industry: '银行', stock_count: 2, report_count: 6 },
        { industry: '机械设备', stock_count: 3, report_count: 3 },
      ],
    ))
    // 行业名与「N只/M篇」分属不同 span（深/浅灰分层），逐元素断言
    expect(screen.getByText('行业热度')).toBeInTheDocument()
    expect(screen.getByText('银行')).toBeInTheDocument()
    expect(screen.getByText('机械设备')).toBeInTheDocument()
    expect(screen.getByText(/2只\/6篇/)).toBeInTheDocument()
    expect(screen.getByText(/3只\/3篇/)).toBeInTheDocument()
  })

  it('does not render industry bar when summary empty (explicit [] with stocks present)', () => {
    renderStep({}, withIndustry([{ stock_code: '601398', stock_name: '青岛银行', report_count: 2 }], []))
    expect(screen.getByText('青岛银行')).toBeInTheDocument()  // 有个股
    expect(screen.queryByText('行业热度')).not.toBeInTheDocument()  // 但行业空 → 不渲染行业条
  })

  it('does not render industry bar when summary undefined (old prefill)', () => {
    renderStep({}, withCoverage([{ stock_code: '601398', stock_name: '青岛银行', report_count: 2 }]))
    expect(screen.queryByText('行业热度')).not.toBeInTheDocument()
  })

  it('loads range industry + items when switching to 近5日 tab', async () => {
    const spy = vi.spyOn(api, 'getResearchCoverage').mockResolvedValue({
      days: 5,
      covered_days: 4,
      items: [{ stock_code: '600519', stock_name: '贵州茅台', report_count: 5 }],
      industry: [{ industry: '食品饮料', stock_count: 1, report_count: 5 }],
    })
    renderStep({}, withIndustry(
      [{ stock_code: '601398', stock_name: '青岛银行', report_count: 2 }],
      [{ industry: '银行', stock_count: 2, report_count: 6 }],
    ))
    fireEvent.click(screen.getByText('近5日'))
    // range 分支：异步加载后渲染 res.industry + res.items（驱动三元 + res.industry ?? [] 兜底）
    expect(await screen.findByText('食品饮料')).toBeInTheDocument()
    expect(screen.getByText('贵州茅台')).toBeInTheDocument()
    expect(spy).toHaveBeenCalledWith(5)
  })
})

afterEach(() => {
  vi.restoreAllMocks()
})
