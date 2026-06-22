import { useQuery } from '@tanstack/react-query'
import { api } from '../../lib/api'
import type {
  MarginIndexCorrelationPayload,
  MicBalance,
  MicDivergence,
  MicPairing,
} from '../../lib/types'
import { Section } from './widgets'

// 全 [判断] 派生信号：两融余额日变化率与指数涨跌幅的统计联动，守红线不出价位/不给买卖建议。
const REDLINE =
  '两融×指数联动 · 全 [判断]（两融余额日变化率 vs 指数涨跌幅统计联动）· 不含价位目标、不构成买卖建议。'

const MARGIN_LABEL: Record<string, string> = {
  total: '两融合计',
  sse: '沪市两融',
  szse: '深市两融',
}
const UNEVALUATED = new Set(['日期缺口', '样本不足', '无法评估'])

function fmtYi(v: number | null | undefined): string {
  return v == null ? '—' : `${v.toLocaleString('zh-CN', { maximumFractionDigits: 0 })} 亿`
}
function fmtPct(v: number | null | undefined): string {
  return v == null ? '—' : `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`
}
function fmtCorr(v: number | null | undefined): string {
  return v == null ? '—' : v.toFixed(2)
}
// 本仓库约定：两融余额/资金 增=绿 / 降=红（与板块涨跌一致）。
function signClass(v: number | null | undefined): string {
  if (v == null) return 'text-gray-400'
  return v >= 0 ? 'text-green-600' : 'text-red-500'
}
function pairLabel(p: MicPairing): string {
  const m = p.margin_label ?? MARGIN_LABEL[p.margin_key] ?? p.margin_key
  return `${m} × ${p.index_name}`
}

function DivergenceBlock({ data, pairs }: { data: MarginIndexCorrelationPayload; pairs: MicPairing[] }) {
  const pmap = new Map(pairs.map((p) => [p.pair_key, p]))
  const hits: string[] = []
  const gaps: string[] = []
  let evaluated = 0
  for (const [key, byWin] of Object.entries(data.divergence ?? {})) {
    const name = pmap.get(key) ? pairLabel(pmap.get(key) as MicPairing) : key
    for (const [win, d] of Object.entries(byWin as Record<string, MicDivergence>)) {
      if (d.diverged) {
        hits.push(
          `${name}（近${win}日）：${d.type} ｜指数累计 ${fmtPct(d.index_cum)}、两融累计 ${fmtPct(d.margin_cum)}`,
        )
      } else if (UNEVALUATED.has(d.type)) {
        gaps.push(`${name}(近${win}日)${d.type}`)
      } else {
        evaluated += 1
      }
    }
  }
  return (
    <div className="mb-4">
      <div className="text-xs font-semibold text-gray-600 mb-1.5">⚠️ 背离预警 [判断]</div>
      {hits.length > 0 ? (
        <ul className="space-y-1">
          {hits.map((h, i) => (
            <li key={i} className="text-xs text-red-600 bg-red-50 border border-red-100 rounded px-2 py-1">
              {h}
            </li>
          ))}
        </ul>
      ) : evaluated > 0 ? (
        <div className="text-xs text-gray-500">已评估口径近窗内未见背离，两融与指数方向一致。</div>
      ) : (
        gaps.length === 0 && <div className="text-xs text-gray-400">暂无可评估的对照口径。</div>
      )}
      {gaps.length > 0 && (
        <div className="mt-1 text-xs text-amber-700">
          数据质量提示：以下窗口<strong>未评估</strong>（不等于无背离）：{gaps.join('｜')}
        </div>
      )}
    </div>
  )
}

function BalanceBlock({ balance }: { balance: Record<string, MicBalance> }) {
  const keys = ['total', 'sse', 'szse'].filter((k) => balance[k])
  if (keys.length === 0) return null
  return (
    <div className="mb-4">
      <div className="text-xs font-semibold text-gray-600 mb-1.5">余额水位 + 趋势 [判断]</div>
      <div className="overflow-x-auto">
        <table className="min-w-full text-xs text-gray-600">
          <thead>
            <tr className="text-left text-gray-400">
              <th className="py-1 pr-4 font-medium">口径</th>
              <th className="py-1 pr-4 font-medium text-right">余额</th>
              <th className="py-1 pr-4 font-medium text-right">日环比</th>
              <th className="py-1 pr-4 font-medium text-right">近20日分位</th>
              <th className="py-1 pr-4 font-medium text-right">偏离MA20</th>
              <th className="py-1 font-medium">连续</th>
            </tr>
          </thead>
          <tbody>
            {keys.map((k) => {
              const b = balance[k]
              const streak =
                b.up_streak >= 2 ? `连增 ${b.up_streak} 日` : b.down_streak >= 2 ? `连降 ${b.down_streak} 日` : '—'
              return (
                <tr key={k} className="border-t border-gray-200/70">
                  <td className="py-1.5 pr-4 font-medium text-gray-700">{MARGIN_LABEL[k]}</td>
                  <td className="py-1.5 pr-4 text-right text-gray-700">{fmtYi(b.latest_yi)}</td>
                  <td className={`py-1.5 pr-4 text-right ${signClass(b.dod_pct)}`}>{fmtPct(b.dod_pct)}</td>
                  <td className="py-1.5 pr-4 text-right text-gray-600">
                    {b.pctile_20d == null ? '—' : `${(b.pctile_20d * 100).toFixed(0)}%`}
                  </td>
                  <td className={`py-1.5 pr-4 text-right ${signClass(b.vs_ma20)}`}>{fmtPct(b.vs_ma20)}</td>
                  <td className="py-1.5 text-gray-500">{streak}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function LagBlock({ data, pairs }: { data: MarginIndexCorrelationPayload; pairs: MicPairing[] }) {
  const lag = data.lag ?? {}
  const rows = pairs.filter((p) => lag[p.pair_key])
  if (rows.length === 0) return null
  return (
    <div className="mb-4">
      <div className="text-xs font-semibold text-gray-600 mb-1.5">两融对指数的领先/滞后 [判断]</div>
      <ul className="space-y-0.5">
        {rows.map((p) => {
          const l = lag[p.pair_key]
          const detail =
            l.best_lag == null
              ? l.relation
              : `${l.relation}${l.best_lag === 0 ? '' : ` ${Math.abs(l.best_lag)} 日`}（corr ${fmtCorr(l.best_corr)}）`
          return (
            <li key={p.pair_key} className="text-xs text-gray-600">
              <span className="text-gray-700">{pairLabel(p)}</span>：{detail}
            </li>
          )
        })}
      </ul>
    </div>
  )
}

function SyncBlock({ data, pairs }: { data: MarginIndexCorrelationPayload; pairs: MicPairing[] }) {
  const sync = data.sync_corr ?? {}
  const wins = (data.windows ?? []).map((w) => String(w))
  const rows = pairs.filter((p) => sync[p.pair_key])
  if (rows.length === 0 || wins.length === 0) return null
  return (
    <div>
      <div className="text-xs font-semibold text-gray-600 mb-1.5">同步相关系数（多窗） [判断]</div>
      <div className="overflow-x-auto">
        <table className="min-w-full text-xs text-gray-600">
          <thead>
            <tr className="text-left text-gray-400">
              <th className="py-1 pr-4 font-medium">对照</th>
              {wins.map((w) => (
                <th key={w} className="py-1 pr-4 font-medium text-right">
                  {w}日
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((p) => {
              const byWin = sync[p.pair_key]
              return (
                <tr key={p.pair_key} className="border-t border-gray-200/70">
                  <td className="py-1.5 pr-4 text-gray-700">{pairLabel(p)}</td>
                  {wins.map((w) => {
                    const cell = byWin[w]
                    return (
                      <td key={w} className="py-1.5 pr-4 text-right">
                        <span className="text-gray-700">{fmtCorr(cell?.corr)}</span>
                        <span className="text-gray-400"> {cell?.label ?? ''}</span>
                      </td>
                    )
                  })}
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}

/** 两融余额与指数联动性（复盘「1.大盘」四维：背离/水位趋势/领先滞后/同步相关；date 缺失不渲染）。 */
export default function MarginIndexCorrelation({ date }: { date: string | undefined }) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ['margin-index-correlation', date],
    queryFn: () => api.getMarginIndexCorrelation(date as string),
    enabled: !!date,
  })

  if (!date) return null

  const pairs = data?.indices ?? []

  return (
    <Section title="两融×指数联动性">
      <div className="bg-amber-50 border border-amber-100 text-amber-800 text-xs rounded px-3 py-2 mb-3">
        {REDLINE}
      </div>
      {isLoading && <div className="px-3 py-6 text-center text-xs text-gray-400">加载中...</div>}
      {isError && <div className="px-3 py-6 text-center text-xs text-gray-400">加载失败</div>}
      {!isLoading && !isError && (!data?.available ? (
        <div className="px-3 py-6 text-center text-xs text-gray-400">
          暂无两融联动数据（需先跑 main.py post 盘后采集，或 margin-index-correlation daily --date 补采）
        </div>
      ) : (
        <>
          {data.meta?.stale && (
            <div className="text-xs text-amber-700 mb-2">
              ⏱ 两融数据为 <strong>{data.data_trade_date}</strong>（非当日 T-1，交易所盘后发布滞后），
              分析以该真实日为脊柱对齐。
            </div>
          )}
          <DivergenceBlock data={data} pairs={pairs} />
          {data.balance && <BalanceBlock balance={data.balance} />}
          <LagBlock data={data} pairs={pairs} />
          <SyncBlock data={data} pairs={pairs} />
        </>
      ))}
    </Section>
  )
}
