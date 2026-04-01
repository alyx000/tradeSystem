"""SQLite Schema 定义：全部表 + FTS 虚拟表 + 触发器 + 索引。"""
from __future__ import annotations

import sqlite3

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
    title, core_view, key_points, sectors, avoid, raw_content,
    content=teacher_notes, content_rowid=id,
    tokenize='unicode61'
);
"""

_SQL_TEACHER_NOTES_FTS_TRIGGERS = [
    """
    CREATE TRIGGER IF NOT EXISTS teacher_notes_fts_insert
    AFTER INSERT ON teacher_notes BEGIN
        INSERT INTO teacher_notes_fts(rowid, title, core_view, key_points, sectors, avoid, raw_content)
        VALUES (new.id, new.title, new.core_view, new.key_points, new.sectors, new.avoid, new.raw_content);
    END;
    """,
    """
    CREATE TRIGGER IF NOT EXISTS teacher_notes_fts_delete
    AFTER DELETE ON teacher_notes BEGIN
        INSERT INTO teacher_notes_fts(teacher_notes_fts, rowid, title, core_view, key_points, sectors, avoid, raw_content)
        VALUES ('delete', old.id, old.title, old.core_view, old.key_points, old.sectors, old.avoid, old.raw_content);
    END;
    """,
    """
    CREATE TRIGGER IF NOT EXISTS teacher_notes_fts_update
    AFTER UPDATE ON teacher_notes BEGIN
        INSERT INTO teacher_notes_fts(teacher_notes_fts, rowid, title, core_view, key_points, sectors, avoid, raw_content)
        VALUES ('delete', old.id, old.title, old.core_view, old.key_points, old.sectors, old.avoid, old.raw_content);
        INSERT INTO teacher_notes_fts(rowid, title, core_view, key_points, sectors, avoid, raw_content)
        VALUES (new.id, new.title, new.core_view, new.key_points, new.sectors, new.avoid, new.raw_content);
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
    note TEXT,
    updated_at TEXT
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
    sh_index_close REAL,
    sh_index_change_pct REAL,
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
# 索引
# ──────────────────────────────────────────────────────────────
_SQL_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_teacher_notes_date ON teacher_notes(date);",
    "CREATE INDEX IF NOT EXISTS idx_teacher_notes_teacher_date ON teacher_notes(teacher_id, date);",
    "CREATE INDEX IF NOT EXISTS idx_calendar_date ON calendar_events(date);",
    "CREATE INDEX IF NOT EXISTS idx_calendar_impact ON calendar_events(impact);",
    "CREATE INDEX IF NOT EXISTS idx_emotion_cycle_date ON emotion_cycle(date);",
    "CREATE INDEX IF NOT EXISTS idx_main_themes_date ON main_themes(date);",
    "CREATE INDEX IF NOT EXISTS idx_main_themes_status ON main_themes(status);",
    "CREATE INDEX IF NOT EXISTS idx_trades_date ON trades(date);",
    "CREATE INDEX IF NOT EXISTS idx_trades_stock ON trades(stock_code);",
    "CREATE INDEX IF NOT EXISTS idx_industry_info_date ON industry_info(date);",
    "CREATE INDEX IF NOT EXISTS idx_macro_info_date ON macro_info(date);",
]

# ──────────────────────────────────────────────────────────────
# 全部 DDL 的执行顺序
# ──────────────────────────────────────────────────────────────
_ALL_TABLE_SQL = [
    _SQL_TEACHERS,
    _SQL_TEACHER_NOTES,
    _SQL_NOTE_ATTACHMENTS,
    _SQL_CALENDAR_EVENTS,
    _SQL_HOLDINGS,
    _SQL_WATCHLIST,
    _SQL_BLACKLIST,
    _SQL_INDUSTRY_INFO,
    _SQL_MACRO_INFO,
    _SQL_DAILY_MARKET,
    _SQL_DAILY_REVIEWS,
    _SQL_EMOTION_CYCLE,
    _SQL_MAIN_THEMES,
    _SQL_TRADES,
]

_ALL_FTS_SQL = [
    _SQL_TEACHER_NOTES_FTS,
    _SQL_INDUSTRY_INFO_FTS,
    _SQL_MACRO_INFO_FTS,
]

_ALL_TRIGGER_SQL = (
    _SQL_TEACHER_NOTES_FTS_TRIGGERS
    + [_SQL_HOLDINGS_TRIGGER, _SQL_WATCHLIST_TRIGGER, _SQL_DAILY_REVIEWS_TRIGGER]
    + _SQL_INDUSTRY_INFO_FTS_TRIGGERS
    + _SQL_MACRO_INFO_FTS_TRIGGERS
)

EXPECTED_TABLES = [
    "teachers", "teacher_notes", "note_attachments",
    "calendar_events",
    "holdings", "watchlist", "blacklist",
    "industry_info", "macro_info",
    "daily_market", "daily_reviews",
    "emotion_cycle", "main_themes",
    "trades",
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
