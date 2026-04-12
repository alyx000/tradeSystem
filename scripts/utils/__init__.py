"""脚本内公共工具"""

import re


def is_st_stock(name) -> bool:
    """判断是否为 ST 股（统一实现，供 collector / API 共用）"""
    if not name:
        return False
    n = re.sub(r"\s+", "", str(name)).upper()
    return n.startswith(("ST", "*ST", "S*ST", "SST"))
