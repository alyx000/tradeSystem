import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import SectorRankingPanel from '../components/market/SectorRankingPanel'

describe('SectorRankingPanel', () => {
  it('renders sections and forwards tab, sort and expand actions', () => {
    const onSectorTabChange = vi.fn()
    const onSortOrderChange = vi.fn()
    const onToggleShowAll = vi.fn()

    render(
      <SectorRankingPanel
        sectorTab="industry"
        sortOrder="gain"
        showAllSectors={false}
        strongestSectors={[
          { rank: 1, name: '人工智能', up_nums: 12, cons_nums: 4, pct_chg: 3.6, up_stat: '6天5板' },
        ]}
        thsMoneyflowRows={[
          { ts_code: '881001.TI', industry: '软件开发', net_amount: 22.5, pct_change: 4.8, lead_stock: '高标A' },
        ]}
        dcMoneyflowRows={[
          { ts_code: 'BK1234', name: '人工智能', content_type: '概念', net_amount_yi: 18, buy_sm_amount_stock: '高标A' },
        ]}
        sectorData={Array.from({ length: 12 }, (_, i) => ({ name: `板块${i + 1}`, change_pct: i + 1 }))}
        visibleSectors={Array.from({ length: 10 }, (_, i) => ({ name: `板块${i + 1}`, change_pct: i + 1 }))}
        onSectorTabChange={onSectorTabChange}
        onSortOrderChange={onSortOrderChange}
        onToggleShowAll={onToggleShowAll}
      />
    )

    expect(screen.getByText('最强板块')).toBeInTheDocument()
    expect(screen.getByText('THS 行业资金流前列')).toBeInTheDocument()
    expect(screen.getByText('DC 板块资金流前列')).toBeInTheDocument()
    expect(screen.getByText('展开全部 12 条')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '概念' }))
    fireEvent.click(screen.getByRole('button', { name: '跌幅↓' }))
    fireEvent.click(screen.getByRole('button', { name: '展开全部 12 条' }))

    expect(onSectorTabChange).toHaveBeenCalledWith('concept')
    expect(onSortOrderChange).toHaveBeenCalledWith('loss')
    expect(onToggleShowAll).toHaveBeenCalled()
  })
})
