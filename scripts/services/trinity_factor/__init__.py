"""三位一体双层评分的纯逻辑核心。"""

from .scoring import score_factor, score_sector
from .selection import select_dominant_factors
from .validation import (
    TrinityValidationError,
    parse_factor_response,
    parse_sector_response,
)

__all__ = [
    "TrinityValidationError",
    "parse_factor_response",
    "parse_sector_response",
    "score_factor",
    "score_sector",
    "select_dominant_factors",
]
