"""SQLite Schema 定义：全部表 + FTS 虚拟表 + 触发器 + 索引。"""
from __future__ import annotations

import sqlite3


def holding_code_norm_sql(column: str = "stock_code") -> str:
    """返回股票代码归一化 SQL：忽略交易所后缀并转大写。"""
    upper_col = f"UPPER({column})"
    return (
        "CASE "
        f"WHEN {upper_col} LIKE '%.SZ' THEN SUBSTR({upper_col}, 1, LENGTH({upper_col}) - 3) "
        f"WHEN {upper_col} LIKE '%.SH' THEN SUBSTR({upper_col}, 1, LENGTH({upper_col}) - 3) "
        f"WHEN {upper_col} LIKE '%.BJ' THEN SUBSTR({upper_col}, 1, LENGTH({upper_col}) - 3) "
        f"ELSE {upper_col} END"
    )


# ──────────────────────────────────────────────────────────────
# 1. 老师复盘观点
# ──────────────────────────────────────────────────────────────
_SQL_TEACHERS = """
CREATE TABLE IF NOT EXISTS teachers (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    platform TEXT,
    schedule TEXT
);
"""

_SQL_TEACHER_NOTES = """
CREATE TABLE IF NOT EXISTS teacher_notes (
    id INTEGER PRIMARY KEY,
    teacher_id INTEGER REFERENCES teachers(id),
    date TEXT NOT NULL,
    title TEXT NOT NULL,
    source_type TEXT DEFAULT 'text',
    input_by TEXT,
    core_view TEXT,
    position_advice TEXT,
    obsidian_path TEXT,
    tags TEXT,
    key_points TEXT,
    sectors TEXT,
    avoid TEXT,
    raw_content TEXT,
    mentioned_stocks TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
"""

_SQL_NOTE_ATTACHMENTS = """
CREATE TABLE IF NOT EXISTS note_attachments (
    id INTEGER PRIMARY KEY,
    note_id INTEGER REFERENCES teacher_notes(id) ON DELETE CASCADE,
    file_path TEXT NOT NULL,
    file_type TEXT,
    description TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
"""

_SQL_TEACHER_NOTES_FTS = """
CREATE VIRTUAL TABLE IF NOT EXISTS teacher_notes_fts USING fts5(
    title, core_view, key_points, sectors, avoid, raw_content, mentioned_stocks,
    content=teacher_notes, content_rowid=id,
    tokenize='unicode61'
);
"""

_SQL_TEACHER_NOTES_FTS_TRIGGERS = [
    """
    CREATE TRIGGER IF NOT EXISTS teacher_notes_fts_insert
    AFTER INSERT ON teacher_notes BEGIN
        INSERT INTO teacher_notes_fts(rowid, title, core_view, key_points, sectors, avoid, raw_content, mentioned_stocks)
        VALUES (new.id, new.title, new.core_view, new.key_points, new.sectors, new.avoid, new.raw_content, new.mentioned_stocks);
    END;
    """,
    """
    CREATE TRIGGER IF NOT EXISTS teacher_notes_fts_delete
    AFTER DELETE ON teacher_notes BEGIN
        INSERT INTO teacher_notes_fts(teacher_notes_fts, rowid, title, core_view, key_points, sectors, avoid, raw_content, mentioned_stocks)
        VALUES ('delete', old.id, old.title, old.core_view, old.key_points, old.sectors, old.avoid, old.raw_content, old.mentioned_stocks);
    END;
    """,
    """
    CREATE TRIGGER IF NOT EXISTS teacher_notes_fts_update
    AFTER UPDATE ON teacher_notes BEGIN
        INSERT INTO teacher_notes_fts(teacher_notes_fts, rowid, title, core_view, key_points, sectors, avoid, raw_content, mentioned_stocks)
        VALUES ('delete', old.id, old.title, old.core_view, old.key_points, old.sectors, old.avoid, old.raw_content, old.mentioned_stocks);
        INSERT INTO teacher_notes_fts(rowid, title, core_view, key_points, sectors, avoid, raw_content, mentioned_stocks)
        VALUES (new.id, new.title, new.core_view, new.key_points, new.sectors, new.avoid, new.raw_content, new.mentioned_stocks);
    END;
    """,
]

# ──────────────────────────────────────────────────────────────
# 2. 投资日历
# ──────────────────────────────────────────────────────────────
_SQL_CALENDAR_EVENTS = """
CREATE TABLE IF NOT EXISTS calendar_events (
    id INTEGER PRIMARY KEY,
    date TEXT NOT NULL CHECK(date GLOB '????-??-??'),
    time TEXT,
    event TEXT NOT NULL,
    impact TEXT,
    category TEXT,
    source TEXT,
    country TEXT,
    prior TEXT,
    expected TEXT,
    actual TEXT,
    note TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
"""

# ──────────────────────────────────────────────────────────────
# 3. 持仓池 / 关注池 / 黑名单
# ──────────────────────────────────────────────────────────────
_SQL_HOLDINGS = """
CREATE TABLE IF NOT EXISTS holdings (
    id INTEGER PRIMARY KEY,
    stock_code TEXT NOT NULL,
    stock_name TEXT NOT NULL,
    market TEXT DEFAULT 'A股',
    sector TEXT,
    shares INTEGER,
    entry_date TEXT,
    entry_price REAL,
    current_price REAL,
    stop_loss REAL,
    target_price REAL,
    position_ratio REAL,
    status TEXT DEFAULT 'active',
    entry_reason TEXT,
    note TEXT,
    updated_at TEXT
);
"""

_SQL_HOLDING_TASKS = """
CREATE TABLE IF NOT EXISTS holding_tasks (
    id INTEGER PRIMARY KEY,
    trade_date TEXT NOT NULL CHECK(trade_date GLOB '????-??-??'),
    stock_code TEXT NOT NULL,
    stock_name TEXT,
    action_plan TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'review_step7',
    status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open', 'done', 'ignored')),
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
"""

_SQL_HOLDING_QUOTE_SNAPSHOTS = """
CREATE TABLE IF NOT EXISTS holding_quote_snapshots (
    id INTEGER PRIMARY KEY,
    trade_date TEXT NOT NULL CHECK(trade_date GLOB '????-??-??'),
    stock_code TEXT NOT NULL,
    stock_name TEXT,
    close REAL,
    pnl_pct REAL,
    turnover_rate REAL,
    ma5 REAL,
    ma10 REAL,
    ma20 REAL,
    volume_vs_ma5 TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
"""

_SQL_WATCHLIST = """
CREATE TABLE IF NOT EXISTS watchlist (
    id INTEGER PRIMARY KEY,
    stock_code TEXT NOT NULL,
    stock_name TEXT NOT NULL,
    tier TEXT NOT NULL,
    sector TEXT,
    add_date TEXT,
    add_reason TEXT,
    trigger_condition TEXT,
    entry_condition TEXT,
    entry_mode TEXT,
    position_plan TEXT,
    volume_status TEXT,
    current_status TEXT,
    leader_type TEXT,
    successor TEXT,
    role TEXT,
    status TEXT DEFAULT 'watching',
    note TEXT,
    source_note_id INTEGER,
    updated_at TEXT
);
"""

_SQL_BLACKLIST = """
CREATE TABLE IF NOT EXISTS blacklist (
    id INTEGER PRIMARY KEY,
    stock_code TEXT NOT NULL,
    stock_name TEXT NOT NULL,
    reason TEXT,
    until TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
"""

_SQL_HOLDINGS_TRIGGER = """
CREATE TRIGGER IF NOT EXISTS holdings_updated
AFTER UPDATE ON holdings BEGIN
    UPDATE holdings SET updated_at = datetime('now') WHERE id = new.id;
END;
"""

_SQL_HOLDING_TASKS_TRIGGER = """
CREATE TRIGGER IF NOT EXISTS holding_tasks_updated
AFTER UPDATE ON holding_tasks BEGIN
    UPDATE holding_tasks SET updated_at = datetime('now') WHERE id = new.id;
END;
"""

_SQL_WATCHLIST_TRIGGER = """
CREATE TRIGGER IF NOT EXISTS watchlist_updated
AFTER UPDATE ON watchlist BEGIN
    UPDATE watchlist SET updated_at = datetime('now') WHERE id = new.id;
END;
"""

# ──────────────────────────────────────────────────────────────
# 4. 行业 / 宏观信息 + FTS
# ──────────────────────────────────────────────────────────────
_SQL_INDUSTRY_INFO = """
CREATE TABLE IF NOT EXISTS industry_info (
    id INTEGER PRIMARY KEY,
    date TEXT NOT NULL,
    sector_name TEXT NOT NULL,
    info_type TEXT,
    content TEXT NOT NULL,
    source TEXT,
    confidence TEXT,
    timeliness TEXT,
    tags TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
"""

_SQL_MACRO_INFO = """
CREATE TABLE IF NOT EXISTS macro_info (
    id INTEGER PRIMARY KEY,
    date TEXT NOT NULL,
    category TEXT,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    source TEXT,
    impact_assessment TEXT,
    confidence TEXT,
    tags TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
"""

_SQL_INDUSTRY_INFO_FTS = """
CREATE VIRTUAL TABLE IF NOT EXISTS industry_info_fts USING fts5(
    sector_name, content, tags,
    content=industry_info, content_rowid=id,
    tokenize='unicode61'
);
"""

_SQL_INDUSTRY_INFO_FTS_TRIGGERS = [
    """
    CREATE TRIGGER IF NOT EXISTS industry_info_fts_insert
    AFTER INSERT ON industry_info BEGIN
        INSERT INTO industry_info_fts(rowid, sector_name, content, tags) VALUES (new.id, new.sector_name, new.content, new.tags);
    END;
    """,
    """
    CREATE TRIGGER IF NOT EXISTS industry_info_fts_delete
    AFTER DELETE ON industry_info BEGIN
        INSERT INTO industry_info_fts(industry_info_fts, rowid, sector_name, content, tags)
        VALUES ('delete', old.id, old.sector_name, old.content, old.tags);
    END;
    """,
    """
    CREATE TRIGGER IF NOT EXISTS industry_info_fts_update
    AFTER UPDATE ON industry_info BEGIN
        INSERT INTO industry_info_fts(industry_info_fts, rowid, sector_name, content, tags)
        VALUES ('delete', old.id, old.sector_name, old.content, old.tags);
        INSERT INTO industry_info_fts(rowid, sector_name, content, tags) VALUES (new.id, new.sector_name, new.content, new.tags);
    END;
    """,
]

_SQL_MACRO_INFO_FTS = """
CREATE VIRTUAL TABLE IF NOT EXISTS macro_info_fts USING fts5(
    title, content, tags,
    content=macro_info, content_rowid=id,
    tokenize='unicode61'
);
"""

_SQL_MACRO_INFO_FTS_TRIGGERS = [
    """
    CREATE TRIGGER IF NOT EXISTS macro_info_fts_insert
    AFTER INSERT ON macro_info BEGIN
        INSERT INTO macro_info_fts(rowid, title, content, tags) VALUES (new.id, new.title, new.content, new.tags);
    END;
    """,
    """
    CREATE TRIGGER IF NOT EXISTS macro_info_fts_delete
    AFTER DELETE ON macro_info BEGIN
        INSERT INTO macro_info_fts(macro_info_fts, rowid, title, content, tags)
        VALUES ('delete', old.id, old.title, old.content, old.tags);
    END;
    """,
    """
    CREATE TRIGGER IF NOT EXISTS macro_info_fts_update
    AFTER UPDATE ON macro_info BEGIN
        INSERT INTO macro_info_fts(macro_info_fts, rowid, title, content, tags)
        VALUES ('delete', old.id, old.title, old.content, old.tags);
        INSERT INTO macro_info_fts(rowid, title, content, tags) VALUES (new.id, new.title, new.content, new.tags);
    END;
    """,
]

# ──────────────────────────────────────────────────────────────
# 5. 每日行情
# ──────────────────────────────────────────────────────────────
_SQL_DAILY_MARKET = """
CREATE TABLE IF NOT EXISTS daily_market (
    date TEXT PRIMARY KEY CHECK(date GLOB '????-??-??'),
    sh_index_open REAL,
    sh_index_high REAL,
    sh_index_low REAL,
    sh_index_close REAL,
    sh_index_change_pct REAL,
    sz_index_open REAL,
    sz_index_high REAL,
    sz_index_low REAL,
    sz_index_close REAL,
    sz_index_change_pct REAL,
    total_amount REAL,
    advance_count INTEGER,
    decline_count INTEGER,
    sh_above_ma5w BOOLEAN,
    sz_above_ma5w BOOLEAN,
    chinext_above_ma5w BOOLEAN,
    star50_above_ma5w BOOLEAN,
    avg_price_above_ma5w BOOLEAN,
    limit_up_count INTEGER,
    limit_down_count INTEGER,
    seal_rate REAL,
    broken_rate REAL,
    highest_board INTEGER,
    continuous_board_counts TEXT,
    premium_10cm REAL,
    premium_20cm REAL,
    premium_30cm REAL,
    premium_second_board REAL,
    northbound_net REAL,
    margin_balance REAL,
    market_breadth TEXT,
    raw_data TEXT,
    node_signals TEXT,
    top_volume_stocks TEXT,
    etf_flow TEXT,
    hk_indices TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
"""

# ──────────────────────────────────────────────────────────────
# 6. 八步复盘
# ──────────────────────────────────────────────────────────────
_SQL_DAILY_REVIEWS = """
CREATE TABLE IF NOT EXISTS daily_reviews (
    date TEXT PRIMARY KEY CHECK(date GLOB '????-??-??'),
    market TEXT DEFAULT 'A股',
    step1_market TEXT,
    step2_sectors TEXT,
    step3_emotion TEXT,
    step4_style TEXT,
    step5_leaders TEXT,
    step6_nodes TEXT,
    step7_positions TEXT,
    step8_plan TEXT,
    summary TEXT,
    completion_status TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
"""

_SQL_DAILY_REVIEWS_TRIGGER = """
CREATE TRIGGER IF NOT EXISTS daily_reviews_updated
AFTER UPDATE ON daily_reviews BEGIN
    UPDATE daily_reviews SET updated_at = datetime('now') WHERE date = new.date;
END;
"""

# ──────────────────────────────────────────────────────────────
# 7. 情绪周期 / 主线跟踪
# ──────────────────────────────────────────────────────────────
_SQL_EMOTION_CYCLE = """
CREATE TABLE IF NOT EXISTS emotion_cycle (
    id INTEGER PRIMARY KEY,
    date TEXT NOT NULL UNIQUE CHECK(date GLOB '????-??-??'),
    phase TEXT,
    sub_cycle INTEGER,
    started_date TEXT,
    days_in_phase INTEGER,
    strength_trend TEXT,
    confidence TEXT,
    sentiment_leaders TEXT,
    profit_loss_effect TEXT,
    indicators_snapshot TEXT,
    note TEXT
);
"""

_SQL_MAIN_THEMES = """
CREATE TABLE IF NOT EXISTS main_themes (
    id INTEGER PRIMARY KEY,
    date TEXT NOT NULL CHECK(date GLOB '????-??-??'),
    theme_name TEXT NOT NULL,
    status TEXT,
    phase TEXT,
    started_date TEXT,
    duration_days INTEGER,
    vs_index TEXT,
    incremental_or_stock TEXT,
    key_stocks TEXT,
    continuation_signals TEXT,
    risk_signals TEXT,
    note TEXT,
    UNIQUE(date, theme_name)
);
"""

# ──────────────────────────────────────────────────────────────
# 8. 交易记录
# ──────────────────────────────────────────────────────────────
_SQL_TRADES = """
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY,
    date TEXT NOT NULL CHECK(date GLOB '????-??-??'),
    stock_code TEXT NOT NULL,
    stock_name TEXT NOT NULL,
    market TEXT DEFAULT 'A股',
    direction TEXT NOT NULL,
    time TEXT,
    price REAL NOT NULL,
    shares INTEGER,
    amount REAL,
    market_context TEXT,
    sector TEXT,
    sector_node TEXT,
    stock_role TEXT,
    stock_attribute TEXT,
    leader_type TEXT,
    entry_mode TEXT,
    entry_reason TEXT,
    exit_reason TEXT,
    holding_days INTEGER,
    pnl_pct REAL,
    pnl_amount REAL,
    was_correct BOOLEAN,
    lesson TEXT,
    trinity_alignment TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
"""

# ──────────────────────────────────────────────────────────────
# 9. 原始事实层 / 采集审计
# ──────────────────────────────────────────────────────────────
_SQL_RAW_INTERFACE_PAYLOADS = """
CREATE TABLE IF NOT EXISTS raw_interface_payloads (
    id INTEGER PRIMARY KEY,
    interface_name TEXT NOT NULL,
    provider TEXT NOT NULL,
    stage TEXT NOT NULL,
    biz_date TEXT NOT NULL CHECK(biz_date GLOB '????-??-??'),
    target_date TEXT CHECK(target_date IS NULL OR target_date GLOB '????-??-??'),
    raw_table TEXT NOT NULL,
    dedupe_key TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    row_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL CHECK(status IN ('success', 'empty', 'partial')),
    params_json TEXT NOT NULL,
    source_meta_json TEXT,
    inserted_at TEXT DEFAULT (datetime('now'))
);
"""

_SQL_MARKET_FACT_SNAPSHOTS = """
CREATE TABLE IF NOT EXISTS market_fact_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    biz_date TEXT NOT NULL CHECK(biz_date GLOB '????-??-??'),
    fact_type TEXT NOT NULL,
    subject_type TEXT NOT NULL CHECK(subject_type IN ('market', 'sector', 'stock', 'index')),
    subject_code TEXT,
    subject_name TEXT,
    facts_json TEXT NOT NULL,
    source_interfaces_json TEXT NOT NULL,
    confidence TEXT NOT NULL DEFAULT 'high' CHECK(confidence IN ('high', 'medium', 'low')),
    inserted_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    UNIQUE(biz_date, fact_type, subject_type, subject_code)
);
"""

_SQL_FACT_ENTITIES = """
CREATE TABLE IF NOT EXISTS fact_entities (
    id INTEGER PRIMARY KEY,
    biz_date TEXT NOT NULL CHECK(biz_date GLOB '????-??-??'),
    interface_name TEXT NOT NULL,
    entity_type TEXT NOT NULL CHECK(entity_type IN ('stock', 'sector', 'index', 'theme')),
    entity_code TEXT,
    entity_name TEXT NOT NULL,
    role TEXT NOT NULL,
    attributes_json TEXT,
    inserted_at TEXT DEFAULT (datetime('now'))
);
"""

_SQL_INGEST_RUNS = """
CREATE TABLE IF NOT EXISTS ingest_runs (
    run_id TEXT PRIMARY KEY,
    interface_name TEXT NOT NULL,
    provider TEXT NOT NULL,
    stage TEXT NOT NULL,
    biz_date TEXT NOT NULL CHECK(biz_date GLOB '????-??-??'),
    target_date TEXT CHECK(target_date IS NULL OR target_date GLOB '????-??-??'),
    params_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('running', 'success', 'empty', 'partial', 'failed')),
    row_count INTEGER NOT NULL DEFAULT 0,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    duration_ms INTEGER,
    triggered_by TEXT NOT NULL CHECK(triggered_by IN ('cli', 'api', 'system')),
    input_by TEXT,
    notes TEXT
);
"""

_SQL_INGEST_ERRORS = """
CREATE TABLE IF NOT EXISTS ingest_errors (
    id INTEGER PRIMARY KEY,
    run_id TEXT NOT NULL,
    interface_name TEXT NOT NULL,
    biz_date TEXT NOT NULL CHECK(biz_date GLOB '????-??-??'),
    stage TEXT NOT NULL,
    error_type TEXT NOT NULL CHECK(error_type IN ('network', 'provider', 'validation', 'storage')),
    error_message TEXT NOT NULL,
    retryable INTEGER NOT NULL DEFAULT 1,
    context_json TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    resolved_at TEXT,
    FOREIGN KEY(run_id) REFERENCES ingest_runs(run_id)
);
"""

_SQL_STOCK_REGULATORY_MONITOR = """
CREATE TABLE IF NOT EXISTS stock_regulatory_monitor (
    id INTEGER PRIMARY KEY,
    ts_code TEXT NOT NULL,
    name TEXT NOT NULL,
    regulatory_type INTEGER NOT NULL CHECK(regulatory_type IN (1, 2)),
    risk_level INTEGER NOT NULL DEFAULT 1 CHECK(risk_level IN (1, 2, 3)),
    reason TEXT NOT NULL,
    publish_date TEXT NOT NULL CHECK(publish_date GLOB '????-??-??'),
    source TEXT NOT NULL,
    risk_score REAL,
    detail_json TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    UNIQUE(ts_code, regulatory_type, publish_date)
);
"""

_SQL_STOCK_REGULATORY_MONITOR_TRIGGER = """
CREATE TRIGGER IF NOT EXISTS stock_regulatory_monitor_updated
AFTER UPDATE ON stock_regulatory_monitor BEGIN
    UPDATE stock_regulatory_monitor SET updated_at = datetime('now') WHERE id = new.id;
END;
"""

_SQL_STOCK_REGULATORY_STK_ALERT = """
CREATE TABLE IF NOT EXISTS stock_regulatory_stk_alert (
    id INTEGER PRIMARY KEY,
    ts_code TEXT NOT NULL,
    name TEXT NOT NULL,
    monitor_start TEXT NOT NULL CHECK(monitor_start GLOB '????-??-??'),
    monitor_end TEXT NOT NULL CHECK(monitor_end GLOB '????-??-??'),
    alert_type TEXT NOT NULL DEFAULT '',
    snapshot_date TEXT NOT NULL CHECK(snapshot_date GLOB '????-??-??'),
    source TEXT NOT NULL,
    detail_json TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
"""

_SQL_STOCK_REGULATORY_STK_ALERT_TRIGGER = """
CREATE TRIGGER IF NOT EXISTS stock_regulatory_stk_alert_updated
AFTER UPDATE ON stock_regulatory_stk_alert BEGIN
    UPDATE stock_regulatory_stk_alert SET updated_at = datetime('now') WHERE id = new.id;
END;
"""

# ──────────────────────────────────────────────────────────────
# 10. 交易计划层
# ──────────────────────────────────────────────────────────────
_SQL_MARKET_OBSERVATIONS = """
CREATE TABLE IF NOT EXISTS market_observations (
    observation_id TEXT PRIMARY KEY,
    trade_date TEXT NOT NULL CHECK(trade_date GLOB '????-??-??'),
    source_type TEXT NOT NULL CHECK(source_type IN ('review', 'knowledge_asset', 'manual', 'system_prefill', 'agent_assisted', 'teacher_note')),
    title TEXT,
    market_facts_json TEXT,
    sector_facts_json TEXT,
    stock_facts_json TEXT,
    judgements_json TEXT,
    source_refs_json TEXT,
    source_agent TEXT,
    created_by TEXT,
    input_by TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
"""

_SQL_TRADE_DRAFTS = """
CREATE TABLE IF NOT EXISTS trade_drafts (
    draft_id TEXT PRIMARY KEY,
    trade_date TEXT NOT NULL CHECK(trade_date GLOB '????-??-??'),
    title TEXT,
    summary TEXT NOT NULL,
    market_view_json TEXT NOT NULL,
    sector_view_json TEXT NOT NULL,
    stock_focus_json TEXT NOT NULL,
    style_view_json TEXT NOT NULL,
    assumptions_json TEXT NOT NULL,
    ambiguities_json TEXT NOT NULL,
    missing_fields_json TEXT NOT NULL,
    watch_items_json TEXT NOT NULL,
    fact_check_candidates_json TEXT NOT NULL,
    judgement_check_candidates_json TEXT NOT NULL,
    source_observation_ids_json TEXT NOT NULL,
    source_asset_ids_json TEXT,
    created_from_review_date TEXT,
    status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft', 'ready_for_confirm', 'archived')),
    created_by TEXT,
    input_by TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
"""

_SQL_TRADE_PLANS = """
CREATE TABLE IF NOT EXISTS trade_plans (
    plan_id TEXT PRIMARY KEY,
    trade_date TEXT NOT NULL CHECK(trade_date GLOB '????-??-??'),
    title TEXT NOT NULL,
    market_bias TEXT NOT NULL CHECK(market_bias IN ('主升', '震荡', '分歧', '退潮', '混沌')),
    main_themes_json TEXT NOT NULL,
    focus_style TEXT NOT NULL CHECK(focus_style IN ('趋势', '连板', '容量', '反包', '轮动')),
    watch_items_json TEXT NOT NULL,
    risk_notes_json TEXT NOT NULL,
    invalidations_json TEXT NOT NULL,
    execution_notes_json TEXT NOT NULL,
    source_draft_id TEXT,
    status TEXT NOT NULL CHECK(status IN ('draft', 'confirmed', 'reviewed')),
    confirmed_by TEXT,
    input_by TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
"""

_SQL_PLAN_REVIEWS = """
CREATE TABLE IF NOT EXISTS plan_reviews (
    review_id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL,
    trade_date TEXT NOT NULL CHECK(trade_date GLOB '????-??-??'),
    outcome_summary TEXT NOT NULL,
    market_result_json TEXT NOT NULL,
    theme_result_json TEXT NOT NULL,
    watch_item_reviews_json TEXT NOT NULL,
    missed_points_json TEXT NOT NULL,
    lessons_json TEXT NOT NULL,
    input_by TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY(plan_id) REFERENCES trade_plans(plan_id)
);
"""

# ──────────────────────────────────────────────────────────────
# 11. 资料层
# ──────────────────────────────────────────────────────────────
_SQL_KNOWLEDGE_ASSETS = """
CREATE TABLE IF NOT EXISTS knowledge_assets (
    asset_id TEXT PRIMARY KEY,
    asset_type TEXT NOT NULL CHECK(asset_type IN ('teacher_note', 'news_note', 'course_note', 'manual_note')),
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    source TEXT,
    tags TEXT,
    summary TEXT,
    trade_clues TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
"""

# ──────────────────────────────────────────────────────────────
# 索引
# ──────────────────────────────────────────────────────────────
_SQL_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_teacher_notes_date ON teacher_notes(date);",
    "CREATE INDEX IF NOT EXISTS idx_teacher_notes_teacher_date ON teacher_notes(teacher_id, date);",
    "CREATE INDEX IF NOT EXISTS idx_calendar_date ON calendar_events(date);",
    "CREATE INDEX IF NOT EXISTS idx_calendar_impact ON calendar_events(impact);",
    f"CREATE UNIQUE INDEX IF NOT EXISTS idx_holdings_active_norm_unique "
    f"ON holdings ({holding_code_norm_sql()}) WHERE status = 'active';",
    f"CREATE INDEX IF NOT EXISTS idx_holding_tasks_norm_date ON holding_tasks ({holding_code_norm_sql('stock_code')}, trade_date DESC);",
    "CREATE INDEX IF NOT EXISTS idx_holding_tasks_status_date ON holding_tasks(status, trade_date DESC);",
    f"CREATE UNIQUE INDEX IF NOT EXISTS idx_holding_quotes_date_norm_unique ON holding_quote_snapshots(trade_date, {holding_code_norm_sql('stock_code')});",
    f"CREATE INDEX IF NOT EXISTS idx_holding_quotes_norm_date ON holding_quote_snapshots({holding_code_norm_sql('stock_code')}, trade_date DESC);",
    "CREATE INDEX IF NOT EXISTS idx_emotion_cycle_date ON emotion_cycle(date);",
    "CREATE INDEX IF NOT EXISTS idx_main_themes_date ON main_themes(date);",
    "CREATE INDEX IF NOT EXISTS idx_main_themes_status ON main_themes(status);",
    "CREATE INDEX IF NOT EXISTS idx_trades_date ON trades(date);",
    "CREATE INDEX IF NOT EXISTS idx_trades_stock ON trades(stock_code);",
    "CREATE INDEX IF NOT EXISTS idx_industry_info_date ON industry_info(date);",
    "CREATE INDEX IF NOT EXISTS idx_macro_info_date ON macro_info(date);",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_raw_payloads_iface_dedupe ON raw_interface_payloads(interface_name, dedupe_key);",
    "CREATE INDEX IF NOT EXISTS idx_raw_payloads_biz_iface ON raw_interface_payloads(biz_date, interface_name);",
    "CREATE INDEX IF NOT EXISTS idx_raw_payloads_stage_biz ON raw_interface_payloads(stage, biz_date);",
    "CREATE INDEX IF NOT EXISTS idx_raw_payloads_table_biz ON raw_interface_payloads(raw_table, biz_date);",
    "CREATE INDEX IF NOT EXISTS idx_fact_entities_biz_type_code ON fact_entities(biz_date, entity_type, entity_code);",
    "CREATE INDEX IF NOT EXISTS idx_fact_entities_name_biz ON fact_entities(entity_name, biz_date);",
    "CREATE INDEX IF NOT EXISTS idx_fact_entities_iface_biz ON fact_entities(interface_name, biz_date);",
    "CREATE INDEX IF NOT EXISTS idx_ingest_runs_biz_stage ON ingest_runs(biz_date, stage);",
    "CREATE INDEX IF NOT EXISTS idx_ingest_runs_iface_biz ON ingest_runs(interface_name, biz_date);",
    "CREATE INDEX IF NOT EXISTS idx_ingest_runs_status_started ON ingest_runs(status, started_at);",
    "CREATE INDEX IF NOT EXISTS idx_ingest_errors_run_id ON ingest_errors(run_id);",
    "CREATE INDEX IF NOT EXISTS idx_regulatory_monitor_date ON stock_regulatory_monitor(publish_date);",
    "CREATE INDEX IF NOT EXISTS idx_regulatory_monitor_code ON stock_regulatory_monitor(ts_code);",
    "CREATE INDEX IF NOT EXISTS idx_stk_alert_snapshot ON stock_regulatory_stk_alert(snapshot_date);",
    "CREATE INDEX IF NOT EXISTS idx_stk_alert_code ON stock_regulatory_stk_alert(ts_code);",
    "CREATE INDEX IF NOT EXISTS idx_stk_alert_period ON stock_regulatory_stk_alert(monitor_start, monitor_end);",
    "CREATE INDEX IF NOT EXISTS idx_market_observations_trade_date ON market_observations(trade_date);",
    "CREATE INDEX IF NOT EXISTS idx_market_observations_source_type ON market_observations(source_type);",
    "CREATE INDEX IF NOT EXISTS idx_trade_drafts_trade_date ON trade_drafts(trade_date);",
    "CREATE INDEX IF NOT EXISTS idx_trade_drafts_status ON trade_drafts(status);",
    "CREATE INDEX IF NOT EXISTS idx_trade_plans_trade_date ON trade_plans(trade_date);",
    "CREATE INDEX IF NOT EXISTS idx_trade_plans_status ON trade_plans(status);",
    "CREATE INDEX IF NOT EXISTS idx_plan_reviews_plan_id ON plan_reviews(plan_id);",
    "CREATE INDEX IF NOT EXISTS idx_knowledge_assets_type_created ON knowledge_assets(asset_type, created_at DESC);",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_trade_calendar_date ON trade_calendar(date);",
    "CREATE INDEX IF NOT EXISTS idx_leader_tracking_active ON leader_tracking(is_active, last_seen_date DESC);",
    "CREATE INDEX IF NOT EXISTS idx_trading_cognitions_category ON trading_cognitions(category);",
    "CREATE INDEX IF NOT EXISTS idx_trading_cognitions_status ON trading_cognitions(status);",
    "CREATE INDEX IF NOT EXISTS idx_trading_cognitions_evidence_level ON trading_cognitions(evidence_level);",
    "CREATE INDEX IF NOT EXISTS idx_trading_cognitions_supersedes ON trading_cognitions(supersedes);",
    "CREATE INDEX IF NOT EXISTS idx_cognition_instances_cognition_id ON cognition_instances(cognition_id);",
    "CREATE INDEX IF NOT EXISTS idx_cognition_instances_observed_date ON cognition_instances(observed_date);",
    "CREATE INDEX IF NOT EXISTS idx_cognition_instances_outcome ON cognition_instances(outcome);",
    "CREATE INDEX IF NOT EXISTS idx_periodic_reviews_period ON periodic_reviews(period_type, period_start, period_end);",
    "CREATE INDEX IF NOT EXISTS idx_periodic_reviews_scope_label ON periodic_reviews(review_scope, regime_label);",
]

# ──────────────────────────────────────────────────────────────
# 12. 最票跟踪
# ──────────────────────────────────────────────────────────────
_SQL_LEADER_TRACKING = """
CREATE TABLE IF NOT EXISTS leader_tracking (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_code TEXT NOT NULL,
    stock_name TEXT NOT NULL,
    sector TEXT NOT NULL,
    attribute_type TEXT NOT NULL,
    first_seen_date TEXT NOT NULL CHECK(first_seen_date GLOB '????-??-??'),
    last_seen_date TEXT NOT NULL CHECK(last_seen_date GLOB '????-??-??'),
    consecutive_days INTEGER DEFAULT 1,
    current_phase TEXT,
    is_active BOOLEAN DEFAULT 1,
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(stock_code, sector, attribute_type)
);
"""

_SQL_LEADER_TRACKING_TRIGGER = """
CREATE TRIGGER IF NOT EXISTS leader_tracking_updated
AFTER UPDATE ON leader_tracking BEGIN
    UPDATE leader_tracking SET updated_at = datetime('now') WHERE id = new.id;
END;
"""

# ──────────────────────────────────────────────────────────────
# 交易日历
# ──────────────────────────────────────────────────────────────
_SQL_TRADE_CALENDAR = """
CREATE TABLE IF NOT EXISTS trade_calendar (
    date TEXT PRIMARY KEY CHECK(date GLOB '????-??-??'),
    is_open INTEGER NOT NULL DEFAULT 0,
    exchange TEXT NOT NULL DEFAULT 'SSE',
    updated_at TEXT DEFAULT (datetime('now'))
);
"""

# ──────────────────────────────────────────────────────────────
# 13. 交易认知层：trading_cognitions / cognition_instances / periodic_reviews
#     （方案 §4.1 / §4.2 / §4.3，schema v21 引入）
# ──────────────────────────────────────────────────────────────
_SQL_TRADING_COGNITIONS = """
CREATE TABLE IF NOT EXISTS trading_cognitions (
    cognition_id TEXT PRIMARY KEY,
    category TEXT NOT NULL,
    sub_category TEXT,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    pattern TEXT,
    time_horizon TEXT,
    action_template TEXT,
    position_template TEXT,
    conditions_json TEXT,
    exceptions_json TEXT,
    invalidation_conditions_json TEXT,
    evidence_level TEXT NOT NULL DEFAULT 'observation'
        CHECK(evidence_level IN ('observation', 'hypothesis', 'principle')),
    conflict_group TEXT,
    first_source_note_id INTEGER REFERENCES teacher_notes(id),
    first_observed_date TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    supersedes TEXT,
    instance_count INTEGER NOT NULL DEFAULT 0,
    validated_count INTEGER NOT NULL DEFAULT 0,
    invalidated_count INTEGER NOT NULL DEFAULT 0,
    confidence REAL NOT NULL DEFAULT 0.5,
    status TEXT NOT NULL DEFAULT 'candidate'
        CHECK(status IN ('candidate', 'active', 'deprecated', 'merged')),
    tags TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

_SQL_COGNITION_INSTANCES = """
CREATE TABLE IF NOT EXISTS cognition_instances (
    instance_id TEXT PRIMARY KEY,
    cognition_id TEXT NOT NULL REFERENCES trading_cognitions(cognition_id),
    observed_date TEXT NOT NULL CHECK(observed_date GLOB '????-??-??'),
    source_type TEXT NOT NULL,
    source_note_id INTEGER REFERENCES teacher_notes(id),
    teacher_id INTEGER REFERENCES teachers(id),
    teacher_name_snapshot TEXT,
    source_plan_review_id TEXT,
    source_daily_review_date TEXT,
    trade_id INTEGER,
    context_summary TEXT,
    regime_tags_json TEXT,
    time_horizon TEXT,
    action_bias TEXT,
    position_cap REAL,
    avoid_action TEXT,
    market_regime TEXT,
    cross_market_anchor TEXT,
    consensus_key TEXT,
    parameters_json TEXT,
    teacher_original_text TEXT,
    outcome TEXT NOT NULL DEFAULT 'pending'
        CHECK(outcome IN ('pending', 'validated', 'invalidated', 'partial', 'not_applicable')),
    outcome_detail TEXT,
    outcome_fact_source TEXT,
    outcome_fact_refs_json TEXT,
    outcome_date TEXT,
    lesson TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(cognition_id, observed_date, source_type, source_note_id)
);
"""

_SQL_PERIODIC_REVIEWS = """
CREATE TABLE IF NOT EXISTS periodic_reviews (
    review_id TEXT PRIMARY KEY,
    period_type TEXT NOT NULL
        CHECK(period_type IN ('weekly', 'monthly', 'quarterly', 'yearly')),
    review_scope TEXT NOT NULL DEFAULT 'calendar_period'
        CHECK(review_scope IN ('calendar_period', 'event_window', 'regime_window')),
    regime_label TEXT,
    period_start TEXT NOT NULL CHECK(period_start GLOB '????-??-??'),
    period_end TEXT NOT NULL CHECK(period_end GLOB '????-??-??'),
    trading_day_count INTEGER,
    active_cognitions_json TEXT,
    validation_stats_json TEXT,
    teacher_participation_json TEXT,
    consensus_summary_json TEXT,
    disagreement_summary_json TEXT,
    new_cognitions_json TEXT,
    refined_cognitions_json TEXT,
    deprecated_cognitions_json TEXT,
    key_lessons_json TEXT,
    evolving_views_json TEXT,
    performance_notes TEXT,
    user_reflection TEXT,
    action_items_json TEXT,
    status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft', 'confirmed')),
    generated_at TEXT NOT NULL DEFAULT (datetime('now')),
    confirmed_at TEXT,
    UNIQUE(period_type, period_start, period_end)
);
"""

# 触发器：INSERT 实例后重算父表 instance_count / validated_count / invalidated_count / confidence
# 有效样本数 < 3 时 confidence 固定为 0.5，否则 = validated / max(validated + invalidated, 1)
_SQL_COG_INST_AFTER_INSERT_TRIGGER = """
CREATE TRIGGER IF NOT EXISTS trg_cog_inst_after_insert
AFTER INSERT ON cognition_instances
BEGIN
    UPDATE trading_cognitions
    SET instance_count = instance_count + 1,
        validated_count = (SELECT COUNT(*) FROM cognition_instances
                           WHERE cognition_id = NEW.cognition_id AND outcome='validated'),
        invalidated_count = (SELECT COUNT(*) FROM cognition_instances
                             WHERE cognition_id = NEW.cognition_id AND outcome='invalidated'),
        confidence = CASE
                       WHEN ((SELECT COUNT(*) FROM cognition_instances
                              WHERE cognition_id = NEW.cognition_id
                                AND outcome IN ('validated','invalidated'))) < 3 THEN 0.5
                       ELSE (SELECT COUNT(*) FROM cognition_instances
                             WHERE cognition_id = NEW.cognition_id AND outcome='validated') * 1.0
                            / MAX((SELECT COUNT(*) FROM cognition_instances
                                   WHERE cognition_id = NEW.cognition_id
                                     AND outcome IN ('validated','invalidated')), 1)
                     END,
        updated_at = datetime('now')
    WHERE cognition_id = NEW.cognition_id;
END;
"""

# 触发器：UPDATE outcome 后重算 validated_count / invalidated_count / confidence（不动 instance_count）
_SQL_COG_INST_AFTER_UPDATE_OUTCOME_TRIGGER = """
CREATE TRIGGER IF NOT EXISTS trg_cog_inst_after_update_outcome
AFTER UPDATE OF outcome ON cognition_instances
BEGIN
    UPDATE trading_cognitions
    SET validated_count = (SELECT COUNT(*) FROM cognition_instances
                           WHERE cognition_id = NEW.cognition_id AND outcome='validated'),
        invalidated_count = (SELECT COUNT(*) FROM cognition_instances
                             WHERE cognition_id = NEW.cognition_id AND outcome='invalidated'),
        confidence = CASE
                       WHEN ((SELECT COUNT(*) FROM cognition_instances
                              WHERE cognition_id = NEW.cognition_id
                                AND outcome IN ('validated','invalidated'))) < 3 THEN 0.5
                       ELSE (SELECT COUNT(*) FROM cognition_instances
                             WHERE cognition_id = NEW.cognition_id AND outcome='validated') * 1.0
                            / MAX((SELECT COUNT(*) FROM cognition_instances
                                   WHERE cognition_id = NEW.cognition_id
                                     AND outcome IN ('validated','invalidated')), 1)
                     END,
        updated_at = datetime('now')
    WHERE cognition_id = NEW.cognition_id;
END;
"""

# ──────────────────────────────────────────────────────────────
# 全部 DDL 的执行顺序
# ──────────────────────────────────────────────────────────────
_ALL_TABLE_SQL = [
    _SQL_TEACHERS,
    _SQL_TEACHER_NOTES,
    _SQL_NOTE_ATTACHMENTS,
    _SQL_CALENDAR_EVENTS,
    _SQL_HOLDINGS,
    _SQL_HOLDING_TASKS,
    _SQL_HOLDING_QUOTE_SNAPSHOTS,
    _SQL_WATCHLIST,
    _SQL_BLACKLIST,
    _SQL_INDUSTRY_INFO,
    _SQL_MACRO_INFO,
    _SQL_DAILY_MARKET,
    _SQL_DAILY_REVIEWS,
    _SQL_EMOTION_CYCLE,
    _SQL_MAIN_THEMES,
    _SQL_TRADES,
    _SQL_RAW_INTERFACE_PAYLOADS,
    _SQL_MARKET_FACT_SNAPSHOTS,
    _SQL_FACT_ENTITIES,
    _SQL_INGEST_RUNS,
    _SQL_INGEST_ERRORS,
    _SQL_STOCK_REGULATORY_MONITOR,
    _SQL_STOCK_REGULATORY_STK_ALERT,
    _SQL_MARKET_OBSERVATIONS,
    _SQL_TRADE_DRAFTS,
    _SQL_TRADE_PLANS,
    _SQL_PLAN_REVIEWS,
    _SQL_KNOWLEDGE_ASSETS,
    _SQL_LEADER_TRACKING,
    _SQL_TRADE_CALENDAR,
    _SQL_TRADING_COGNITIONS,
    _SQL_COGNITION_INSTANCES,
    _SQL_PERIODIC_REVIEWS,
]

_ALL_FTS_SQL = [
    _SQL_TEACHER_NOTES_FTS,
    _SQL_INDUSTRY_INFO_FTS,
    _SQL_MACRO_INFO_FTS,
]

_ALL_TRIGGER_SQL = (
    _SQL_TEACHER_NOTES_FTS_TRIGGERS
    + [_SQL_HOLDINGS_TRIGGER, _SQL_HOLDING_TASKS_TRIGGER, _SQL_WATCHLIST_TRIGGER, _SQL_DAILY_REVIEWS_TRIGGER]
    + [_SQL_STOCK_REGULATORY_MONITOR_TRIGGER, _SQL_STOCK_REGULATORY_STK_ALERT_TRIGGER, _SQL_LEADER_TRACKING_TRIGGER]
    + _SQL_INDUSTRY_INFO_FTS_TRIGGERS
    + _SQL_MACRO_INFO_FTS_TRIGGERS
    + [_SQL_COG_INST_AFTER_INSERT_TRIGGER, _SQL_COG_INST_AFTER_UPDATE_OUTCOME_TRIGGER]
)

EXPECTED_TABLES = [
    "teachers", "teacher_notes", "note_attachments",
    "calendar_events",
    "holdings", "holding_tasks", "holding_quote_snapshots", "watchlist", "blacklist",
    "industry_info", "macro_info",
    "daily_market", "daily_reviews",
    "emotion_cycle", "main_themes",
    "trades",
    "raw_interface_payloads", "market_fact_snapshots", "fact_entities",
    "ingest_runs", "ingest_errors",
    "stock_regulatory_monitor",
    "stock_regulatory_stk_alert",
    "market_observations", "trade_drafts", "trade_plans", "plan_reviews",
    "knowledge_assets",
    "leader_tracking",
    "trading_cognitions", "cognition_instances", "periodic_reviews",
    "teacher_notes_fts", "industry_info_fts", "macro_info_fts",
]


def init_schema(conn: sqlite3.Connection) -> None:
    """创建全部表、FTS 虚拟表、触发器和索引。幂等操作。"""
    for sql in _ALL_TABLE_SQL:
        conn.executescript(sql)

    for sql in _ALL_FTS_SQL:
        conn.executescript(sql)

    for sql in _ALL_TRIGGER_SQL:
        conn.executescript(sql)

    for sql in _SQL_INDEXES:
        conn.execute(sql)

    conn.commit()
