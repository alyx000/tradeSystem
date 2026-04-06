import type {
  SectorMoneyflowDcRow,
  SectorMoneyflowThsRow,
  SectorSnapshotRow,
  SectorTab,
  SortOrder,
  StrongestSectorRow,
} from '../../lib/types'

function fmtPct(v: number | null | undefined) {
  if (v == null) return '-'
  const sign = v >= 0 ? '+' : ''
  return `${sign}${v.toFixed(2)}%`
}

function pctColor(v: number | null | undefined) {
  if (v == null) return 'text-gray-500'
  return v >= 0 ? 'text-red-600' : 'text-green-600'
}

export default function SectorRankingPanel({
  sectorTab,
  sortOrder,
  showAllSectors,
  strongestSectors,
  thsMoneyflowRows,
  dcMoneyflowRows,
  sectorData,
  visibleSectors,
  onSectorTabChange,
  onSortOrderChange,
  onToggleShowAll,
}: {
  sectorTab: SectorTab
  sortOrder: SortOrder
  showAllSectors: boolean
  strongestSectors: StrongestSectorRow[]
  thsMoneyflowRows: SectorMoneyflowThsRow[]
  dcMoneyflowRows: SectorMoneyflowDcRow[]
  sectorData: SectorSnapshotRow[]
  visibleSectors: SectorSnapshotRow[]
  onSectorTabChange: (tab: SectorTab) => void
  onSortOrderChange: (order: SortOrder) => void
  onToggleShowAll: () => void
}) {
  return (
    <div className="bg-white rounded-lg shadow p-4">
      <div className="flex flex-wrap items-center gap-2 mb-3">
        <h2 className="text-sm font-semibold text-gray-700">板块排行</h2>
        <div className="flex gap-1">
          {([['industry', '行业'], ['concept', '概念'], ['fund_flow', '资金流向']] as const).map(([key, label]) => (
            <button
              key={key}
              onClick={() => onSectorTabChange(key)}
              className={`px-3 py-1 text-xs rounded-full transition-colors ${
                sectorTab === key ? 'bg-blue-600 text-white' : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
              }`}
            >
              {label}
            </button>
          ))}
        </div>
        <div className="flex gap-1 ml-auto">
          <button
            onClick={() => onSortOrderChange('gain')}
            className={`px-3 py-1 text-xs rounded-full transition-colors ${
              sortOrder === 'gain' ? 'bg-red-500 text-white' : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
            }`}
          >
            涨幅↑
          </button>
          <button
            onClick={() => onSortOrderChange('loss')}
            className={`px-3 py-1 text-xs rounded-full transition-colors ${
              sortOrder === 'loss' ? 'bg-green-600 text-white' : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
            }`}
          >
            跌幅↓
          </button>
        </div>
      </div>

      {strongestSectors.length > 0 && (
        <div className="mb-4">
          <div className="text-xs font-medium text-gray-500 mb-2">最强板块</div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b text-left text-gray-500">
                  <th className="py-2 pr-4">排名</th>
                  <th className="py-2 pr-4">板块</th>
                  <th className="py-2 pr-4 text-right">涨停家数</th>
                  <th className="py-2 pr-4 text-right">连板家数</th>
                  <th className="py-2 pr-4 text-right">涨跌幅</th>
                  <th className="py-2 pr-0 text-right">连板高度</th>
                </tr>
              </thead>
              <tbody>
                {strongestSectors.slice(0, 8).map((row) => (
                  <tr key={row.ts_code || row.name} className="border-b border-gray-50 hover:bg-gray-50">
                    <td className="py-1.5 pr-4 text-gray-400">{row.rank ?? '-'}</td>
                    <td className="py-1.5 pr-4 font-medium text-gray-800">{row.name || row.ts_code || '-'}</td>
                    <td className="py-1.5 pr-4 text-right text-gray-700">{row.up_nums ?? '-'}</td>
                    <td className="py-1.5 pr-4 text-right text-gray-700">{row.cons_nums ?? '-'}</td>
                    <td className={`py-1.5 pr-4 text-right ${pctColor(row.pct_chg)}`}>{fmtPct(row.pct_chg)}</td>
                    <td className="py-1.5 pr-0 text-right text-gray-700">{row.up_stat ?? '-'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {(thsMoneyflowRows.length > 0 || dcMoneyflowRows.length > 0) && (
        <div className="grid grid-cols-1 xl:grid-cols-2 gap-4 mb-4">
          <div className="border border-gray-100 rounded-lg p-3">
            <div className="text-xs font-medium text-gray-500 mb-2">THS 行业资金流前列</div>
            {thsMoneyflowRows.length > 0 ? (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b text-left text-gray-500">
                      <th className="py-2 pr-4">板块</th>
                      <th className="py-2 pr-4 text-right">净额(亿)</th>
                      <th className="py-2 pr-4 text-right">涨跌幅</th>
                      <th className="py-2 pr-0 text-right">领涨股</th>
                    </tr>
                  </thead>
                  <tbody>
                    {thsMoneyflowRows.slice(0, 8).map((row) => (
                      <tr key={row.ts_code || row.industry} className="border-b border-gray-50 hover:bg-gray-50">
                        <td className="py-1.5 pr-4 font-medium text-gray-800">{row.industry || row.name || '-'}</td>
                        <td className={`py-1.5 pr-4 text-right ${Number(row.net_amount || 0) >= 0 ? 'text-red-600' : 'text-green-600'}`}>
                          {row.net_amount != null ? Number(row.net_amount).toFixed(2) : '-'}
                        </td>
                        <td className={`py-1.5 pr-4 text-right ${pctColor(row.pct_change)}`}>{fmtPct(row.pct_change)}</td>
                        <td className="py-1.5 pr-0 text-right text-gray-700">{row.lead_stock || '-'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="text-sm text-gray-400 py-6 text-center">暂无 THS 行业资金流</div>
            )}
          </div>

          <div className="border border-gray-100 rounded-lg p-3">
            <div className="text-xs font-medium text-gray-500 mb-2">DC 板块资金流前列</div>
            {dcMoneyflowRows.length > 0 ? (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b text-left text-gray-500">
                      <th className="py-2 pr-4">板块</th>
                      <th className="py-2 pr-4">类型</th>
                      <th className="py-2 pr-4 text-right">净额(亿)</th>
                      <th className="py-2 pr-0 text-right">龙头</th>
                    </tr>
                  </thead>
                  <tbody>
                    {dcMoneyflowRows.slice(0, 8).map((row) => (
                      <tr key={row.ts_code || row.name} className="border-b border-gray-50 hover:bg-gray-50">
                        <td className="py-1.5 pr-4 font-medium text-gray-800">{row.name || '-'}</td>
                        <td className="py-1.5 pr-4 text-gray-500">{row.content_type || '-'}</td>
                        <td className={`py-1.5 pr-4 text-right ${Number(row.net_amount_yi || 0) >= 0 ? 'text-red-600' : 'text-green-600'}`}>
                          {row.net_amount_yi != null ? Number(row.net_amount_yi).toFixed(2) : '-'}
                        </td>
                        <td className="py-1.5 pr-0 text-right text-gray-700">{row.buy_sm_amount_stock || '-'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="text-sm text-gray-400 py-6 text-center">暂无 DC 板块资金流</div>
            )}
          </div>
        </div>
      )}

      {sectorData.length > 0 ? (
        <>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b text-left text-gray-500">
                  <th className="py-2 pr-4">排名</th>
                  <th className="py-2 pr-4">板块名称</th>
                  <th className="py-2 pr-4 text-right">涨跌幅</th>
                  {sectorTab === 'fund_flow' && <th className="py-2 pr-4 text-right">净流入(亿)</th>}
                </tr>
              </thead>
              <tbody>
                {visibleSectors.map((s, i) => (
                  <tr key={i} className="border-b border-gray-50 hover:bg-gray-50">
                    <td className="py-1.5 pr-4 text-gray-400">{i + 1}</td>
                    <td className="py-1.5 pr-4 font-medium text-gray-800">{s.name || s.sector_name || '-'}</td>
                    <td className={`py-1.5 pr-4 text-right ${pctColor(s.pct_change ?? s.change_pct)}`}>
                      {fmtPct(s.pct_change ?? s.change_pct)}
                    </td>
                    {sectorTab === 'fund_flow' && (
                      <td className={`py-1.5 pr-4 text-right ${(s.net_inflow ?? 0) >= 0 ? 'text-red-600' : 'text-green-600'}`}>
                        {s.net_inflow != null ? s.net_inflow.toFixed(2) : '-'}
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {sectorData.length > 10 && (
            <button
              onClick={onToggleShowAll}
              className="mt-2 w-full text-xs text-blue-500 hover:text-blue-700 py-1.5 border-t border-gray-100"
            >
              {showAllSectors ? '收起' : `展开全部 ${sectorData.length} 条`}
            </button>
          )}
        </>
      ) : (
        <div className="text-sm text-gray-400 text-center py-6">暂无板块数据</div>
      )}
    </div>
  )
}
