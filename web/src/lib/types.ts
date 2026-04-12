export interface DailyMarket {
  date: string
  sh_index_close: number | null
  sh_index_change_pct: number | null
  sz_index_close: number | null
  sz_index_change_pct: number | null
  total_amount: number | null
  advance_count: number | null
  decline_count: number | null
  sh_above_ma5w: boolean | null
  sz_above_ma5w: boolean | null
  chinext_above_ma5w: boolean | null
  star50_above_ma5w: boolean | null
  avg_price_above_ma5w: boolean | null
  limit_up_count: number | null
  limit_down_count: number | null
  seal_rate: number | null
  broken_rate: number | null
  highest_board: number | null
  continuous_board_counts: string | null
  premium_10cm: number | null
  premium_20cm: number | null
  premium_30cm: number | null
  premium_second_board: number | null
  northbound_net: number | null
  margin_balance: number | null
}

export interface CommandDocItem {
  command: string
  description: string
}

export interface CommandDocSectionItem extends CommandDocItem {
  target: string
}

export interface CommandDocSection {
  title: string
  items: CommandDocSectionItem[]
}

export interface CommandIndexPayload {
  generated_by: string
  summary: string
  daily_quickstart: CommandDocItem[]
  sections: CommandDocSection[]
}

export interface IngestHealthFailedInterface {
  interface_name: string | null
  interface_label?: string | null
  interface_note?: string | null
  failure_count: number | null
  unresolved_count: number | null
  consecutive_failure_days?: number | null
  days_since_last_success?: number | null
  last_success_biz_date?: string | null
  last_failure_biz_date?: string | null
}

export interface IngestHealthDailyFailure {
  biz_date: string
  error_count: number | null
}

export interface IngestHealthSummary {
  start_date: string
  end_date: string
  days: number
  stage?: string | null
  total_runs: number
  total_failures: number
  unresolved_failures: number
  failed_interface_count: number
  never_succeeded_count: number
  failure_rate: number
  status_label?: '稳定' | '有波动' | '承压' | '需处理' | null
  status_reason?: string | null
  top_failed_interfaces: IngestHealthFailedInterface[]
  daily_failures: IngestHealthDailyFailure[]
}

export interface IngestDashboardHealthSummary {
  core: IngestHealthSummary
  extended: IngestHealthSummary
}

export type SectorTab = 'industry' | 'concept' | 'fund_flow'
export type SortOrder = 'gain' | 'loss'

export interface MarketSection<T> {
  data: T[]
}

export interface MarketIndexEntry {
  close: number | null
  change_pct: number | null
}

export interface SectorSnapshotRow {
  name?: string | null
  sector_name?: string | null
  change_pct?: number | null
  pct_change?: number | null
  net_inflow?: number | null
}

export interface DailyInfoRow {
  ts_code?: string | null
  ts_name?: string | null
  market?: string | null
  amount?: number | null
  total_mv?: number | null
  tr?: number | null
  vol?: number | null
}

export interface LimitStepRow {
  ts_code?: string | null
  name?: string | null
  nums?: string | number | null
}

export interface StrongestSectorRow {
  ts_code?: string | null
  rank?: number | null
  name?: string | null
  up_nums?: number | null
  cons_nums?: number | null
  pct_chg?: number | null
  up_stat?: string | null
}

export interface SectorMoneyflowThsRow {
  ts_code?: string | null
  industry?: string | null
  name?: string | null
  net_amount?: number | null
  pct_change?: number | null
  lead_stock?: string | null
}

export interface SectorMoneyflowDcRow {
  ts_code?: string | null
  name?: string | null
  content_type?: string | null
  net_amount?: number | null
  net_amount_yi?: number | null
  pct_change?: number | null
  buy_sm_amount_stock?: string | null
}

export interface MarketMoneyflowDcRow {
  net_amount?: number | null
  net_amount_rate?: number | null
  buy_elg_amount?: number | null
  buy_lg_amount?: number | null
}

export interface MarketMoneyflowSummary {
  netAmountYi: number | null
  netAmountRate: number | null
  superLargeYi: number | null
  largeYi: number | null
}

export interface BoardCountItem {
  board: number
  count: number
  stocks: string[]
}

export interface MarketFullData extends DailyMarket {
  available: boolean
  indices?: Record<string, MarketIndexEntry>
  sector_industry?: MarketSection<SectorSnapshotRow>
  sector_concept?: MarketSection<SectorSnapshotRow>
  sector_fund_flow?: MarketSection<SectorSnapshotRow>
  sector_moneyflow_ths?: MarketSection<SectorMoneyflowThsRow>
  sector_moneyflow_dc?: MarketSection<SectorMoneyflowDcRow>
  market_moneyflow_dc?: MarketSection<MarketMoneyflowDcRow>
  daily_info?: MarketSection<DailyInfoRow>
  limit_step?: MarketSection<LimitStepRow>
  limit_cpt_list?: MarketSection<StrongestSectorRow>
}

export interface MarketHistoryItem {
  date: string
  sh_index_close: number | null
  sh_index_change_pct: number | null
  sz_index_close: number | null
  sz_index_change_pct: number | null
  total_amount: number | null
  advance_count: number | null
  decline_count: number | null
  limit_up_count: number | null
  limit_down_count: number | null
  seal_rate: number | null
  broken_rate: number | null
  highest_board: number | null
  premium_10cm: number | null
  premium_20cm: number | null
  premium_30cm: number | null
  premium_second_board: number | null
  northbound_net: number | null
}

export interface MarketChartItem extends MarketHistoryItem {
  date_short: string
}

export interface MainThemeItem {
  date?: string
  theme_name: string
  phase?: string | null
  duration_days?: number | null
  key_stocks?: string[] | string | null
  note?: string | null
  status?: string | null
}

export interface TeacherNoteAttachment {
  url?: string | null
  file_path?: string | null
  file_type?: string | null
  description?: string | null
}

export interface TeacherNote {
  id: number
  teacher_id: number
  teacher_name?: string
  date: string
  title: string
  core_view: string | null
  tags: string | null
  sectors: string | null
  key_points?: string | null
  position_advice?: string | null
  avoid?: string | null
  raw_content?: string | null
  attachments?: TeacherNoteAttachment[]
  created_at: string
}

export interface TeacherRecord {
  id: number
  name: string
  platform?: string | null
}

export interface TeacherTimelineItem extends TeacherNote {
  platform?: string | null
}

export interface CalendarEvent {
  id: number
  date: string
  event: string
  impact: string | null
  category: string | null
  country?: string | null
  time?: string | null
}

export interface Holding {
  id: number
  stock_code: string
  stock_name: string
  entry_price: number | null
  current_price: number | null
  stop_loss?: number | null
  target_price?: number | null
  position_ratio?: number | null
  prefill_pnl_pct?: number | null
  shares: number | null
  status: string
  sector?: string | null
  entry_reason?: string | null
  note?: string | null
}

export interface HoldingSignalPriceSnapshot {
  entry_price: number | null
  current_price: number | null
  pnl_pct: number | null
  up_limit: number | null
  down_limit: number | null
  pre_close: number | null
}

export interface HoldingTechnicalSignals {
  ma5: number | null
  ma10: number | null
  ma20: number | null
  above_ma5: boolean | null
  above_ma10: boolean | null
  above_ma20: boolean | null
  volume_vs_ma5: '以上' | '以下' | null
  turnover_rate: number | null
  turnover_status: '活跃' | '正常' | '偏低' | null
  sector_change_pct: number | null
}

export interface HoldingThemeSignals {
  is_main_theme: boolean
  main_theme_name: string | null
  is_strongest_sector: boolean
  strongest_sector_name: string | null
  sector_flow_confirmed: boolean
  sector_flow_source: 'ths' | 'dc' | null
}

export interface HoldingEventSignals {
  has_recent_announcement: boolean
  recent_announcements: Array<{
    ann_date: string | null
    title: string | null
  }>
  has_disclosure_plan: boolean
  disclosure_dates: Array<{
    ann_date: string | null
    report_end: string | null
  }>
  is_st: boolean
  share_float_upcoming: Array<{
    ann_date?: string | null
    float_date?: string | null
    shares?: number | null
  }>
}

export interface HoldingRiskFlag {
  level: 'high' | 'medium' | 'low'
  label: string
  reason: string
}

export interface HoldingTaskItem {
  id?: number
  trade_date: string
  stock_code: string
  stock_name?: string | null
  action_plan: string
  source?: string | null
  status?: 'open' | 'done' | 'ignored' | null
}

export interface HoldingTaskUpdateInput {
  status?: 'open' | 'done' | 'ignored'
  action_plan?: string
}

export interface HoldingInfoSignals {
  investor_qa: Array<{ question?: string; answer?: string; date?: string }>
  research_reports: Array<{ institution?: string; rating?: string; target_price?: number; date?: string }>
  news: Array<{ title?: string; time?: string }>
}

export interface HoldingSignalItem {
  stock_code: string
  stock_name: string
  sector?: string | null
  price_snapshot: HoldingSignalPriceSnapshot
  technical_signals: HoldingTechnicalSignals
  theme_signals: HoldingThemeSignals
  event_signals: HoldingEventSignals
  info_signals?: HoldingInfoSignals
  latest_task?: HoldingTaskItem | null
  risk_flags: HoldingRiskFlag[]
}

export interface HoldingSignalsPayload {
  date: string
  items: HoldingSignalItem[]
}

export interface HoldingCreateInput {
  stock_code: string
  stock_name: string
  entry_price?: number
  shares?: number
  sector?: string
  stop_loss?: number
  target_price?: number
  position_ratio?: number
  entry_reason?: string
  note?: string
}

export interface HoldingUpdateInput {
  stop_loss?: number | null
  target_price?: number | null
  position_ratio?: number | null
  entry_reason?: string | null
  note?: string | null
}

export interface WatchlistItem {
  id: number
  stock_code: string
  stock_name: string
  tier: string
  sector: string | null
  add_reason: string | null
  status: string
  add_date?: string | null
  trigger_condition?: string | null
  entry_condition?: string | null
  role?: string | null
  note?: string | null
}

export interface WatchlistCreateInput {
  stock_code: string
  stock_name: string
  tier?: string
  sector?: string
  add_date?: string
  add_reason?: string
  trigger_condition?: string
  entry_condition?: string
  role?: string
  note?: string
}

/** 异动监管 API：Type1/2 来自 stock_regulatory_monitor；Type3 来自 stk_alert（重点监控） */
export interface RegulatoryMonitorRecord {
  id: number
  ts_code: string
  name: string
  regulatory_type: number
  risk_level: number
  reason: string
  publish_date: string
  source: string
  risk_score: number | null
  detail_json: string | Record<string, unknown> | null
  created_at?: string | null
  updated_at?: string | null
  /** regulatory_type=3 时有值：监控期起止（YYYY-MM-DD） */
  monitor_start_date?: string | null
  monitor_end_date?: string | null
  alert_type?: string | null
}

export interface PrefillData {
  date: string
  market: DailyMarket | null
  prev_market: DailyMarket | null
  avg_5d_amount: number | null
  avg_20d_amount: number | null
  teacher_notes: TeacherNote[]
  emotion_cycle: unknown
  main_themes: MainThemeItem[]
  holdings: Holding[]
  calendar_events: CalendarEvent[]
}

export type ReviewStepKey =
  | 'step1_market'
  | 'step2_sectors'
  | 'step3_emotion'
  | 'step4_style'
  | 'step5_leaders'
  | 'step6_nodes'
  | 'step7_positions'
  | 'step8_plan'

export type ReviewStepValue = Record<string, unknown>
export type ReviewFormData = Partial<Record<ReviewStepKey, ReviewStepValue>>

export interface ReviewEmotionCycle {
  phase?: string | null
  sub_cycle?: number | null
  strength_trend?: string | null
  confidence?: string | null
}

export interface ReviewPrevReview {
  date?: string
  step4_style?: string | null
}

export interface IndustryInfoItem {
  id?: number
  sector_name?: string | null
  date?: string | null
  title?: string | null
  content?: string | null
  info_type?: string | null
  confidence?: string | null
  timeliness?: string | null
  source?: string | null
}

export interface IndustryInfoCreateInput {
  date?: string
  sector_name: string
  info_type?: string
  content: string
  source?: string
  confidence?: string
  timeliness?: string
}

export interface TradeRecord {
  id: number
  trade_date?: string | null
  stock_code?: string | null
  stock_name?: string | null
  action?: string | null
  price?: number | null
  shares?: number | null
  note?: string | null
}

export interface TradeCreateInput {
  trade_date?: string
  stock_code?: string
  stock_name?: string
  action?: string
  price?: number
  shares?: number
  note?: string
}

export interface TeacherNoteCreateInput {
  teacher_id?: number
  teacher_name?: string
  title: string
  date: string
  core_view?: string
  raw_content?: string
  tags?: string[] | string
  sectors?: string[] | string
  key_points?: string[] | string
  position_advice?: string
  avoid?: string
  source_type?: string
  input_by?: string
}

export interface CalendarEventCreateInput {
  date: string
  event: string
  impact?: string
  category?: string
  country?: string
  time?: string
}

export interface PostMarketPayload {
  available: boolean
  [key: string]: unknown
}

export interface StyleFactorSeriesItem {
  date?: string
  metric?: string
  value?: number | null
  [key: string]: string | number | null | undefined
}

export interface SearchEntityItem {
  id?: number | string
  title?: string | null
  date?: string | null
  teacher_name?: string | null
  sector_name?: string | null
  event?: string | null
  core_view?: string | null
  content?: string | null
}

export type UnifiedSearchResult = Record<string, SearchEntityItem[]>

export interface IngestInterfaceRecord {
  interface_name: string
  provider_method?: string | null
  stage?: string | null
  stage_label?: string | null
  notes?: string | null
  enabled_by_default?: boolean | null
  enabled_by_default_label?: string | null
  params_policy?: string | null
  interface_label?: string | null
}

export interface IngestRunRecord {
  run_id: string
  interface_name?: string | null
  interface_label?: string | null
  interface_note?: string | null
  provider?: string | null
  provider_label?: string | null
  stage?: string | null
  stage_label?: string | null
  status?: string | null
  status_label?: string | null
  row_count?: number | null
  notes?: string | null
  started_at?: string | null
  finished_at?: string | null
  duration_ms?: number | null
}

export interface IngestErrorRecord {
  id: number | string
  run_id?: string | null
  interface_name?: string | null
  interface_label?: string | null
  interface_note?: string | null
  stage?: string | null
  stage_label?: string | null
  error_type?: string | null
  error_type_label?: string | null
  error_message?: string | null
  retryable?: number | null
  retryable_label?: string | null
  restriction_label?: string | null
  restriction_reason?: string | null
  action_hint?: string | null
}

export interface IngestInspectRecord {
  date?: string
  interface_name?: string | null
  run_count?: number
  error_count?: number
  runs?: IngestRunRecord[]
  errors?: IngestErrorRecord[]
}

export interface IngestRetryGroup {
  interface_name?: string | null
  interface_label?: string | null
  interface_note?: string | null
  biz_date?: string | null
  stage?: string | null
  stage_label?: string | null
  error_count?: number | null
}

export interface IngestRetrySummary {
  interface_name?: string | null
  retryable_count?: number
  groups?: IngestRetryGroup[]
}

export interface IngestReconcileInput {
  stale_minutes?: number
}

export interface IngestReconcileRun {
  run_id?: string | null
  interface_name?: string | null
  biz_date?: string | null
  stage?: string | null
  started_at?: string | null
  finished_at?: string | null
}

export interface IngestReconcileResult {
  stale_minutes: number
  reconciled_count?: number
  runs?: IngestReconcileRun[]
}

export interface IngestRetryRunInput {
  limit?: number
  input_by?: string
}

export interface IngestRetryRunResult {
  requested_groups?: number
  attempted_groups?: number
  resolved_errors?: number
  runs?: IngestRunRecord[]
}

export interface IngestRunStageInput {
  stage: string
  date: string
  input_by?: string
}

export interface IngestRunStageResult {
  stage: string
  stage_label?: string | null
  recorded_runs?: number
}

export interface IngestRunInterfaceInput {
  name: string
  date: string
  input_by?: string
}

export interface IngestRunInterfaceResult {
  name: string
  run: IngestRunRecord
}

export interface KnowledgeAssetRecord {
  asset_id: string
  asset_type?: string | null
  title?: string | null
  content?: string | null
  source?: string | null
  tags?: string[] | string | null
  created_at?: string | null
}

export interface KnowledgeAssetCreateInput {
  asset_type: string
  title: string
  content: string
  source?: string
  tags?: string[]
}

export interface KnowledgeDraftInput {
  trade_date?: string
  input_by?: string
}

export interface KnowledgeDraftResult {
  observation?: {
    observation_id?: string
    source_type?: string
  }
  draft: {
    draft_id?: string
    trade_date?: string
  }
  /** 从老师笔记生成草稿时返回，替代 asset */
  teacher_note?: Record<string, unknown>
}

export interface SectorIndustryPrefill {
  data?: SectorSnapshotRow[]
  bottom?: SectorSnapshotRow[]
}

export interface SectorRhythmItem {
  name?: string | null
  phase?: string | null
  change_today?: number | null
  rank_today?: number | null
  confidence?: string | null
}

export interface StyleFactorCapPreference {
  relative?: string | null
  csi300_chg?: number | null
  csi1000_chg?: number | null
  spread?: number | null
}

export interface StyleFactorBoardPreference {
  dominant_type?: string | null
  pct_10cm?: number | null
  pct_20cm?: number | null
  pct_30cm?: number | null
}

export interface StyleFactorPremiumSnapshotItem {
  premium_median?: number | null
}

export interface StyleFactorPremiumTrend {
  direction?: string | null
  first_board_median_5d?: Array<string | number>
}

export interface ReviewStyleFactors {
  cap_preference?: StyleFactorCapPreference
  board_preference?: StyleFactorBoardPreference
  premium_snapshot?: Record<string, StyleFactorPremiumSnapshotItem>
  premium_trend?: StyleFactorPremiumTrend
  switch_signals?: string[]
}

export interface ReviewPrefillMarket extends Omit<MarketFullData, 'sector_industry'> {
  style_factors?: ReviewStyleFactors
  sector_industry?: SectorIndustryPrefill
  sector_rhythm_industry?: SectorRhythmItem[]
}

export interface ReviewMarketSignals {
  moneyflow_summary: {
    net_amount_yi: number | null
    net_amount_rate: number | null
    super_large_yi: number | null
    large_yi: number | null
  } | null
  market_structure_rows: Array<{
    name: string
    amount: string | number | null
    volume: string | number | null
    pe: number | null
    turnover_rate: number | null
    com_count: number | null
  }>
}

export interface ReviewSectorSignals {
  strongest_rows: Array<{
    rank: number | null
    name: string
    up_nums: number | null
    cons_nums: number | null
    pct_chg: number | null
    up_stat: string | null
  }>
  industry_moneyflow_rows: Array<{
    name: string
    net_amount_yi: number | null
    pct_change: number | null
    lead_stock: string | null
  }>
  concept_moneyflow_rows: Array<{
    name: string
    net_amount_yi: number | null
    pct_change: number | null
    lead_stock: string | null
  }>
  projection_candidates?: ReviewProjectionCandidate[]
}

export interface ReviewProjectionCandidate {
  sector_name: string
  source_tags: string[]
  facts: {
    phase_hint?: string | null
    duration_days?: number | null
    pct_chg?: number | null
    limit_up_count?: number | null
    emotion_leader?: string | null
    capacity_leader?: string | null
    lead_stock?: string | null
    net_amount_yi?: number | null
    teacher_note_refs?: Array<{
      note_id?: number | null
      teacher_name?: string | null
      title?: string | null
    }>
  }
  key_stocks?: string[]
  evidence_text?: string | null
}

export interface ReviewSectorProjection {
  sector_name?: string
  sector_type?: string
  big_cycle_stage?: string
  connection_bias?: string
  market_fit?: string
  role_expectation?: string
  return_flow_view?: string
  fully_priced_risk?: string
  key_stocks?: string[]
  supporting_facts?: string[]
  logic_aesthetic?: string
  judgement_notes?: string
}

export interface ReviewNextDayFocus {
  sector_name?: string
  key_stocks?: string[]
  focus_reason?: string
}

export interface ReviewEmotionSignals {
  ladder_rows: Array<{
    name: string
    nums: number | string | null
  }>
}

export interface ReviewPrefillSignals {
  market: ReviewMarketSignals
  sectors: ReviewSectorSignals
  emotion: ReviewEmotionSignals
}

export interface ReviewPrefillData extends Omit<PrefillData, 'market' | 'main_themes' | 'emotion_cycle'> {
  market: ReviewPrefillMarket | null
  emotion_cycle?: ReviewEmotionCycle | null
  main_themes: MainThemeItem[]
  prev_review?: ReviewPrevReview | null
  industry_info?: IndustryInfoItem[]
  review_signals?: ReviewPrefillSignals
  holding_signals?: HoldingSignalsPayload
  is_trading_day?: boolean
}

export interface ReviewRecord extends Partial<Record<ReviewStepKey, string | ReviewStepValue>> {
  exists: boolean
  ok?: boolean
}

export interface PlanFactCheck {
  check_type: string
  label?: string
  params?: Record<string, string | number>
  priority?: number | string
  result?: string
  evidence_json?: unknown
}

export interface PlanJudgementCheck {
  label: string
  notes?: string
}

export interface PlanWatchItem {
  subject_type?: string
  subject_code?: string
  subject_name?: string
  reason?: string
  priority?: number | string
  fact_checks?: PlanFactCheck[]
  judgement_checks?: PlanJudgementCheck[]
  trigger_conditions?: string[]
  invalidations?: string[]
}

export interface PlanMarketView {
  bias?: string
}

export interface PlanSectorView {
  main_themes?: string[]
}

export interface PlanDraftRecord {
  draft_id: string
  trade_date: string
  title?: string
  summary?: string
  market_view_json?: string
  sector_view_json?: string
  stock_focus_json?: string
  watch_items_json?: string
  fact_check_candidates_json?: string
  judgement_check_candidates_json?: string
  status?: string
}

export interface PlanRecord {
  plan_id: string
  trade_date: string
  title?: string
  market_bias?: string
  status?: string
  watch_items_json?: string
}

export interface PlanFactCheckResult extends PlanFactCheck {
  result: string
}

export interface PlanDiagnosticsItem {
  subject_code?: string
  subject_name?: string
  data_ready?: boolean
  fact_check_results?: PlanFactCheckResult[]
  judgement_checks?: Array<PlanJudgementCheck | string>
  missing_dependencies?: string[]
  unsupported_checks?: string[]
}

export interface PlanDiagnosticsRecord {
  plan_id: string
  trade_date: string
  watch_item_count: number
  fact_check_count: number
  judgement_check_count: number
  data_ready_count: number
  missing_data_count: number
  unsupported_check_count: number
  summary_json?: unknown
  items_json?: PlanDiagnosticsItem[]
  generated_at?: string
}

export interface PlanReviewRecord {
  review_id?: string
  plan_id: string
}

export interface ReviewToDraftResult {
  review_date: string
  trade_date: string
  observation?: PlanObservationRecord
  draft: PlanDraftRecord
}

export interface PlanObservationRecord {
  observation_id: string
  title?: string
  source_type?: string
  judgements_json?: string
}

export interface PlanObservationUpdateInput {
  title?: string
  judgements?: string[]
  input_by?: string
}

export interface PlanDraftCreateInput {
  trade_date: string
  market_facts?: PlanMarketView
  sector_facts?: PlanSectorView
  stock_facts?: Array<Partial<PlanWatchItem>>
  judgements?: string[]
  input_by?: string
}

export interface PlanDraftUpdateInput {
  summary?: string
  watch_items?: PlanWatchItem[]
  fact_check_candidates?: PlanFactCheck[]
  judgement_check_candidates?: PlanJudgementCheck[]
  input_by?: string
}

export interface PlanConfirmInput {
  trade_date: string
  input_by?: string
}

export interface PlanUpdateInput {
  title?: string
  market_bias?: string
  watch_items?: PlanWatchItem[]
  input_by?: string
}

export interface PlanReviewInput {
  trade_date?: string
  outcome_summary?: string
  input_by?: string
}
