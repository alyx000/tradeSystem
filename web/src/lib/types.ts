export interface DailyMarket {
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
  northbound_net: number | null
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
  created_at: string
}

export interface CalendarEvent {
  id: number
  date: string
  event: string
  impact: string | null
  category: string | null
}

export interface Holding {
  id: number
  stock_code: string
  stock_name: string
  entry_price: number | null
  current_price: number | null
  shares: number | null
  status: string
}

export interface WatchlistItem {
  id: number
  stock_code: string
  stock_name: string
  tier: string
  sector: string | null
  add_reason: string | null
  status: string
}

export interface PrefillData {
  date: string
  market: DailyMarket | null
  teacher_notes: TeacherNote[]
  emotion_cycle: any
  main_themes: any[]
  holdings: Holding[]
  calendar_events: CalendarEvent[]
}
