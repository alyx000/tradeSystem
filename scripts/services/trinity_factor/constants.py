"""三位一体评分契约常量。"""

MAX_REASON_LENGTH = 240
FACTOR_SCHEMA_VERSION = "trinity_factor_score_v1"
SECTOR_SCHEMA_VERSION = "trinity_sector_score_v1"
SECTOR_CANDIDATE_MAX = 6

FACTOR_CODES = frozenset({
    "market_node",
    "sector_rhythm",
    "style_regime",
    "leader_signal",
})

FACTOR_WEIGHTS = {
    "current_dominance": 30,
    "cross_layer_alignment": 25,
    "rhythm_clarity": 20,
    "next_stage_relevance": 15,
    "counterevidence": -20,
}

SECTOR_WEIGHTS = {
    "primary_factor_alignment": 25,
    "stage_connection": 20,
    "market_linkage": 20,
    "leader_clarity": 15,
    "logic_aesthetic": 10,
    "expectation_gap": 10,
    "fully_priced_penalty": -20,
}
