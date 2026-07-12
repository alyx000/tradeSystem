"""三位一体双层评分的纯逻辑核心。"""

from .scoring import score_factor, score_sector
from .runner import (
    FACTOR_PROMPT,
    FACTOR_PROMPT_VERSION,
    SECTOR_PROMPT,
    SECTOR_PROMPT_VERSION,
    AntigravityStructuredRunner,
    StructuredRunResult,
    prompt_template_sha256,
)
from .selection import select_dominant_factors
from .validation import (
    TrinityValidationError,
    parse_factor_response,
    parse_sector_response,
)

__all__ = [
    "TrinityValidationError",
    "AntigravityStructuredRunner",
    "StructuredRunResult",
    "FACTOR_PROMPT",
    "FACTOR_PROMPT_VERSION",
    "SECTOR_PROMPT",
    "SECTOR_PROMPT_VERSION",
    "prompt_template_sha256",
    "parse_factor_response",
    "parse_sector_response",
    "score_factor",
    "score_sector",
    "select_dominant_factors",
]
