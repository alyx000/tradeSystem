from __future__ import annotations

LEADER_ROLES = {"趋势中军", "连板核心", "前排活跃", "弹性前排"}
STATUS_ROLES = {"备选", "剔除"}
BOARD_TYPES = {"10cm", "20cm", "30cm", "非涨停"}
MAX_CONFIRMATION_CANDIDATES = 15
MAX_LLM_REVIEW_CANDIDATES = 30

LEADER_FIELDS = ("stock", "sector", "attribute_type", "attribute", "clarity", "position", "is_new")
CLARITY_HIGH = "高"
CLARITY_MEDIUM = "中"
CLARITY_LOW = "低"
TEACHER_SUPPORT = "支持"
TEACHER_CONFLICT = "冲突"
TEACHER_UNMENTIONED = "未提及"
