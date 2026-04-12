const STAGE_LABELS: Record<string, string> = {
  pre_core: '盘前核心',
  post_core: '盘后核心',
  post_extended: '盘后扩展',
  watchlist: '关注池',
  backfill: '低频回填',
}

const STATUS_LABELS: Record<string, string> = {
  success: '成功',
  failed: '失败',
  empty: '空结果',
  partial: '部分成功',
  running: '执行中',
}

const ERROR_TYPE_LABELS: Record<string, string> = {
  provider: '数据源失败',
  network: '网络异常',
  validation: '参数校验',
  storage: '写库失败',
}

const INTERFACE_FALLBACK_LABELS: Record<string, string> = {
  daily_basic: '日线基础指标',
  adj_factor: '复权因子',
  moneyflow_hsgt: '北向资金',
  daily_info: '市场交易统计',
  limit_step: '连板天梯',
  limit_cpt_list: '最强板块统计',
  moneyflow_ind_ths: '同花顺行业资金流',
  moneyflow_ind_dc: '东财板块资金流',
  moneyflow_cnt_ths: 'THS概念资金流',
  moneyflow_concept_dc: 'DC概念资金流',
  moneyflow_mkt_dc: '大盘资金流向',
  margin: '融资融券汇总',
  margin_detail: '融资融券明细',
  block_trade: '大宗交易',
  top_inst: '龙虎榜机构席位',
  stock_st: 'ST股票名单',
  anns_d: '全市场公告',
  disclosure_date: '财报披露计划',
  stk_limit: '涨跌停价格',
  ths_index: '同花顺板块主数据',
  ths_member: '同花顺板块成分',
  index_classify: '申万行业分类',
  stock_basic: 'A股主数据',
  trade_cal: '交易日历',
}

export function providerLabel(provider?: string | null) {
  if (!provider) return '未知来源'
  if (provider === 'tushare') return 'Tushare'
  if (provider === 'akshare') return 'AkShare'
  if (provider.startsWith('tushare:')) return `Tushare · ${provider.split(':')[1]}`
  if (provider.startsWith('akshare:')) return `AkShare · ${provider.split(':')[1]}`
  if (provider === 'registry') return '自动降级链路'
  if (provider.startsWith('get_')) return `Provider 方法 · ${provider}`
  return provider
}

export function fallbackInterfaceMeaning(interfaceName?: string | null) {
  const raw = String(interfaceName || '').trim()
  if (!raw) return '暂无中文说明'
  if (INTERFACE_FALLBACK_LABELS[raw]) return INTERFACE_FALLBACK_LABELS[raw]
  return raw
    .split('_')
    .filter(Boolean)
    .map((part) => part.toUpperCase())
    .join(' / ')
}

export function shortInterfaceMeaning(interfaceName?: string | null, note?: string | null) {
  const raw = String(note || '').trim()
  if (!raw) return fallbackInterfaceMeaning(interfaceName)
  const cut = raw.split(/[，。]/)[0]?.trim()
  return cut || raw
}

export function stageLabel(stage?: string | null) {
  if (!stage) return '未知阶段'
  return STAGE_LABELS[stage] || stage
}

export function statusLabel(status?: string | null) {
  if (!status) return '未知状态'
  return STATUS_LABELS[status] || status
}

export function errorTypeLabel(errorType?: string | null) {
  if (!errorType) return '错误'
  return ERROR_TYPE_LABELS[errorType] || errorType
}

export function boolLabel(value?: boolean | null) {
  if (value == null) return '未知'
  return value ? '是' : '否'
}
