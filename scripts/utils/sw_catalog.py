"""申万 SW2021 二级分类目录的来源校验。"""

# 2026-07-24 实测全目录 134 码，当前发布 124 码。沿用 sector-crowding 的完整性门槛。
SW2021_L2_CATALOG_COUNT_FLOOR = 134
SW2021_L2_PUBLISHED_COUNT_FLOOR = 120


def binary_flag(value):
    """来源中的 0/1 或其字符串；字段未知时保留缺口。"""
    if value == 1 or value == "1":
        return True
    if value == 0 or value == "0":
        return False
    return None


def l2_publication_flags(rows):
    """从 index_classify(level=L2, src=SW2021) 读取逐码发布状态；拒绝未知状态与冲突。"""
    catalog = {}
    for row in rows:
        code = row.get("index_code")
        published = binary_flag(row.get("is_pub"))
        if not isinstance(code, str) or not code.strip() or published is None:
            raise ValueError("index_classify L2 含非法 index_code/is_pub")
        code = code.strip()
        if code in catalog and catalog[code] != published:
            raise ValueError(f"index_classify L2 重复码状态冲突:{code}")
        catalog[code] = published
    return catalog
