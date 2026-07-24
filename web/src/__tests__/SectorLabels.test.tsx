import { fireEvent, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import SectorLabels from '../components/review/SectorLabels'
import { api } from '../lib/api'
import type { SectorLabelsPayload } from '../lib/types'

vi.mock('../lib/api', () => ({
  api: { getSectorLabels: vi.fn() },
}))

const PAYLOAD: SectorLabelsPayload = {
  date: '2026-07-23',
  available: true,
  status: 'success',
  definitions: {
    half_year_ma_window: 144,
    year_ma_window: 233,
    resonance_lookback_days: 10,
    resonance_breakout_window: 20,
    resonance_rule: 'close_and_amount_strictly_above_prior_window_highs',
    window_unit: 'trading_snapshot_days',
  },
  summary: {
    total_l2: 5,
    missing_l2_count: 0,
    above_half_year_ma: 3,
    above_year_ma: 2,
    recent_resonance: 2,
    year_and_resonance: 1,
    half_year_ma_insufficient: 1,
    year_ma_insufficient: 1,
    resonance_insufficient: 1,
  },
  items: [
    {
      code: '801081.SI',
      name: '半导体',
      present_on_target: true,
      close: 101,
      amount_billion: 11,
      half_year_ma: 99,
      above_half_year_ma: true,
      year_ma: 100,
      above_year_ma: true,
      recent_price_volume_resonance: true,
      resonance_age_snapshot_days: 0,
      last_resonance: {
        date: '2026-07-23',
        close: 101,
        prior_close_high: 100,
        amount_billion: 11,
        prior_amount_high_billion: 10,
      },
    },
    {
      code: '801102.SI',
      name: '通信设备',
      present_on_target: true,
      close: 99,
      amount_billion: 8,
      half_year_ma: 97,
      above_half_year_ma: true,
      year_ma: 98,
      above_year_ma: true,
      recent_price_volume_resonance: false,
      resonance_age_snapshot_days: null,
      last_resonance: null,
    },
    {
      code: '801034.SI',
      name: '化学制品',
      present_on_target: true,
      close: null,
      amount_billion: null,
      half_year_ma: null,
      above_half_year_ma: null,
      year_ma: null,
      above_year_ma: null,
      recent_price_volume_resonance: null,
      resonance_age_snapshot_days: null,
      last_resonance: null,
    },
    {
      code: '801053.SI',
      name: '贵金属',
      present_on_target: true,
      close: 102,
      amount_billion: 12,
      half_year_ma: 101,
      above_half_year_ma: true,
      year_ma: 103,
      above_year_ma: false,
      recent_price_volume_resonance: true,
      resonance_age_snapshot_days: 1,
      last_resonance: {
        date: '2026-07-22',
        close: 104,
        prior_close_high: 103,
        amount_billion: 13,
        prior_amount_high_billion: 12,
      },
    },
    {
      code: '801040.SI',
      name: '钢铁',
      present_on_target: true,
      close: 99,
      amount_billion: 7,
      half_year_ma: 100,
      above_half_year_ma: false,
      year_ma: 101,
      above_year_ma: false,
      recent_price_volume_resonance: false,
      resonance_age_snapshot_days: null,
      last_resonance: null,
    },
  ],
}

function renderLabels(date?: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <SectorLabels date={date} />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.mocked(api.getSectorLabels).mockReset()
  vi.mocked(api.getSectorLabels).mockResolvedValue(PAYLOAD)
})

describe('SectorLabels', () => {
  it('无日期时不渲染也不请求', () => {
    const { container } = renderLabels()
    expect(container).toBeEmptyDOMElement()
    expect(api.getSectorLabels).not.toHaveBeenCalled()
  })

  it('默认展示全部板块、标签和事实证据', async () => {
    renderLabels('2026-07-23')

    expect(await screen.findByText('半导体')).toBeInTheDocument()
    expect(screen.getByText('通信设备')).toBeInTheDocument()
    expect(screen.getByText('化学制品')).toBeInTheDocument()
    expect(screen.getByText('指数 101.00 > 100.00')).toBeInTheDocument()
    expect(screen.getByText('年线数据不足')).toBeInTheDocument()
    expect(screen.getByText('半年线数据不足')).toBeInTheDocument()
    expect(screen.getByText('共振数据不足')).toBeInTheDocument()
    expect(screen.getByText('未在半年线上')).toBeInTheDocument()
    expect(screen.getAllByText('未在年线上')).toHaveLength(2)
    expect(screen.getByRole('button', { name: '全部 5' })).toHaveAttribute('aria-pressed', 'true')
  })

  it('严格比较证据不会被两位小数吞成表面相等', async () => {
    vi.mocked(api.getSectorLabels).mockResolvedValueOnce({
      ...PAYLOAD,
      summary: {
        ...PAYLOAD.summary,
        total_l2: 1,
        above_half_year_ma: 1,
        above_year_ma: 1,
        recent_resonance: 1,
        year_and_resonance: 1,
      },
      items: [{
        ...PAYLOAD.items[0],
        close: 100,
        year_ma: 99.9999570815,
        last_resonance: {
          date: '2026-07-23',
          close: 100.0002,
          prior_close_high: 100.0001,
          amount_billion: 10.0002,
          prior_amount_high_billion: 10.0001,
        },
      }],
    })

    renderLabels('2026-07-23')

    expect(await screen.findByText('收盘 100.00 / MA233 99.99995708')).toBeInTheDocument()
    expect(screen.getByText('指数 100.0002 > 100.0001')).toBeInTheDocument()
    expect(screen.getByText('成交额 10.0002亿 > 10.0001亿')).toBeInTheDocument()
  })

  it('半年线上、年线上、近期共振和年线共振筛选使用严格 true 与 AND', async () => {
    renderLabels('2026-07-23')
    await screen.findByText('半导体')

    fireEvent.click(screen.getByRole('button', { name: '半年线上 3' }))
    expect(screen.getByText('半导体')).toBeInTheDocument()
    expect(screen.getByText('通信设备')).toBeInTheDocument()
    expect(screen.getByText('贵金属')).toBeInTheDocument()
    expect(screen.queryByText('化学制品')).not.toBeInTheDocument()
    expect(screen.queryByText('钢铁')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '年线上 2' }))
    expect(screen.getByText('半导体')).toBeInTheDocument()
    expect(screen.getByText('通信设备')).toBeInTheDocument()
    expect(screen.queryByText('化学制品')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '近期共振 2' }))
    expect(screen.getByText('半导体')).toBeInTheDocument()
    expect(screen.getByText('贵金属')).toBeInTheDocument()
    expect(screen.queryByText('通信设备')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '年线+共振 1' }))
    expect(screen.getByText('半导体')).toBeInTheDocument()
    expect(screen.queryByText('通信设备')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '年线+共振 1' })).toHaveAttribute('aria-pressed', 'true')
  })

  it('区分缺快照与筛选结果为空', async () => {
    vi.mocked(api.getSectorLabels).mockResolvedValueOnce({
      ...PAYLOAD,
      available: false,
      status: 'missing_snapshot',
      summary: { ...PAYLOAD.summary, total_l2: 0 },
      items: [],
    })
    const { unmount } = renderLabels('2099-01-01')
    expect(await screen.findByText(/暂无当日拥挤度快照/)).toBeInTheDocument()
    unmount()

    vi.mocked(api.getSectorLabels).mockResolvedValueOnce({
      ...PAYLOAD,
      available: false,
      status: 'missing_l2',
      summary: { ...PAYLOAD.summary, total_l2: 0 },
      items: [],
    })
    const missingL2 = renderLabels('2026-07-21')
    expect(await screen.findByText('当日快照缺少申万二级板块数据')).toBeInTheDocument()
    missingL2.unmount()

    vi.mocked(api.getSectorLabels).mockResolvedValueOnce({
      ...PAYLOAD,
      summary: { ...PAYLOAD.summary, year_and_resonance: 0 },
      items: PAYLOAD.items.filter(item => item.code !== '801081.SI'),
    })
    renderLabels('2026-07-22')
    await screen.findByText('通信设备')
    fireEvent.click(screen.getByRole('button', { name: '年线+共振 0' }))
    expect(await screen.findByText('当前筛选暂无板块')).toBeInTheDocument()
  })

  it('部分 L2 覆盖会告警并保留当日缺行板块', async () => {
    vi.mocked(api.getSectorLabels).mockResolvedValueOnce({
      ...PAYLOAD,
      status: 'partial',
      summary: {
        ...PAYLOAD.summary,
        missing_l2_count: 1,
        half_year_ma_insufficient: 1,
        year_ma_insufficient: 1,
        resonance_insufficient: 1,
      },
      items: PAYLOAD.items.map(item => (
        item.code === '801034.SI'
          ? { ...item, present_on_target: false }
          : item
      )),
    })

    renderLabels('2026-07-23')

    expect(await screen.findByRole('alert')).toHaveTextContent(
      '当日申万二级覆盖不完整：缺失 1 个板块',
    )
    expect(screen.getByText('化学制品')).toBeInTheDocument()
    expect(screen.getByText('当日缺行')).toBeInTheDocument()
  })

  it('目标日 L2 全缺时仍展示上一快照的审计清单', async () => {
    const previousItem = {
      ...PAYLOAD.items[2],
      present_on_target: false,
    }
    vi.mocked(api.getSectorLabels).mockResolvedValueOnce({
      ...PAYLOAD,
      available: false,
      status: 'missing_l2',
      summary: {
        ...PAYLOAD.summary,
        total_l2: 1,
        missing_l2_count: 1,
        above_half_year_ma: 0,
        above_year_ma: 0,
        recent_resonance: 0,
        year_and_resonance: 0,
        half_year_ma_insufficient: 1,
        year_ma_insufficient: 1,
        resonance_insufficient: 1,
      },
      items: [previousItem],
    })

    renderLabels('2026-07-23')

    expect(await screen.findByRole('alert')).toHaveTextContent(
      /保留预期板块清单 1 个并按数据不足处理/,
    )
    expect(screen.getByText('化学制品')).toBeInTheDocument()
    expect(screen.getByText('当日缺行')).toBeInTheDocument()
  })

  it('请求失败显示 alert', async () => {
    vi.mocked(api.getSectorLabels).mockRejectedValueOnce(new Error('boom'))
    renderLabels('2026-07-23')
    expect(await screen.findByRole('alert')).toHaveTextContent('板块标签加载失败')
  })
})
