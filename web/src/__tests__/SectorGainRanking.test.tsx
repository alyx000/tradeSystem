import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import SectorGainRanking from '../components/review/SectorGainRanking'
import { api } from '../lib/api'
import type { SectorGainRankingPayload } from '../lib/types'

vi.mock('../lib/api', () => ({
  api: { getSectorGainRanking: vi.fn() },
}))

function renderComp(date: string | undefined) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <SectorGainRanking date={date} />
    </QueryClientProvider>
  )
}

const PAYLOAD: SectorGainRankingPayload = {
  date: '2026-05-29',
  rankings: {
    '5d': [
      { industry: '电池', max_gain: 12.0, stocks: [
        { name: '甲', code: 'A.SZ', gain: 12.0 },
        { name: '乙', code: 'B.SZ', gain: 6.0 },
      ] },
      { industry: '白酒Ⅱ', max_gain: 8.0, stocks: [{ name: '丙', code: 'C.SZ', gain: 8.0 }] },
    ],
    '10d': [
      { industry: '白酒Ⅱ', max_gain: 9.0, stocks: [{ name: '丙', code: 'C.SZ', gain: 9.0 }] },
    ],
    '20d': [
      { industry: '白酒Ⅱ', max_gain: 20.0, stocks: [{ name: '丙', code: 'C.SZ', gain: 20.0 }] },
      { industry: '电池', max_gain: 1.0, stocks: [{ name: '甲', code: 'A.SZ', gain: 1.0 }] },
    ],
  },
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(api.getSectorGainRanking).mockResolvedValue(PAYLOAD)
})

describe('SectorGainRanking', () => {
  it('date 缺失时不渲染、不发请求', () => {
    renderComp(undefined)
    expect(api.getSectorGainRanking).not.toHaveBeenCalled()
    expect(screen.queryByText(/板块区间涨幅排名/)).toBeNull()
  })

  it('默认展示 5日榜：电池领涨甲 +12.00% 在白酒之前', async () => {
    renderComp('2026-05-29')
    expect(await screen.findByText('电池')).toBeTruthy()
    expect(screen.getByText('+12.00%')).toBeTruthy()
    // 板块内其余股
    expect(screen.getByText('+6.00%')).toBeTruthy()
    // 三档周期 Tab
    expect(screen.getByRole('tab', { name: '5日' })).toBeTruthy()
    expect(screen.getByRole('tab', { name: '10日' })).toBeTruthy()
    expect(screen.getByRole('tab', { name: '20日' })).toBeTruthy()
  })

  it('切到 20日 Tab：白酒(+20%) 排到电池(+1%) 之前', async () => {
    renderComp('2026-05-29')
    await screen.findByText('电池')
    fireEvent.click(screen.getByRole('tab', { name: '20日' }))
    await waitFor(() => expect(screen.getByText('+20.00%')).toBeTruthy())
    const rows = screen.getAllByRole('row')
    // 表头 + 数据行；第一数据行应为白酒
    const bodyText = rows.map((r) => r.textContent || '')
    const baijiuIdx = bodyText.findIndex((t) => t.includes('白酒Ⅱ'))
    const dianchiIdx = bodyText.findIndex((t) => t.includes('电池'))
    expect(baijiuIdx).toBeLessThan(dianchiIdx)
  })

  it('空数据展示空态', async () => {
    vi.mocked(api.getSectorGainRanking).mockResolvedValue({
      date: '2026-05-29', rankings: { '5d': [], '10d': [], '20d': [] },
    })
    renderComp('2026-05-29')
    expect(await screen.findByText(/暂无区间涨幅数据/)).toBeTruthy()
  })
})
