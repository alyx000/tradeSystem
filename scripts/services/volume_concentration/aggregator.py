"""成交额 top20 → 按申万二级行业聚合集中度(纯函数,无 IO)。

share_in_top_n = 行业合计成交额 / topN 合计(TopN 内部集中度,dec-3 定稿口径);
「未分类」作为一个正常桶进入 sector_summary,但 industry_coverage 单独反映归类率
(报告层据此把「未分类」排除出"前3行业集中度",见阶段4 formatter)。
"""
from __future__ import annotations

UNCLASSIFIED = "未分类"


def aggregate_sectors(stocks: list[dict]) -> dict:
    """聚合 topN 个股为行业集中度。

    入参 stocks:每只含 code / industry(已由 collector 打标,缺失为「未分类」)/
    amount_billion。返回 {top_n, total_amount_billion, sector_summary, industry_coverage}。
    sector_summary 按行业合计成交额降序。
    """
    total = sum(float(s.get("amount_billion") or 0) for s in stocks)

    buckets: dict[str, dict] = {}
    for s in stocks:
        industry = s.get("industry") or UNCLASSIFIED
        bucket = buckets.setdefault(
            industry, {"count": 0, "amount": 0.0, "codes": []}
        )
        bucket["count"] += 1
        bucket["amount"] += float(s.get("amount_billion") or 0)
        bucket["codes"].append(s.get("code"))

    sector_summary = [
        {
            "industry": industry,
            "count": bucket["count"],
            "amount_billion": round(bucket["amount"], 2),
            "share_in_top_n": (bucket["amount"] / total) if total else 0.0,
            "codes": bucket["codes"],
        }
        for industry, bucket in buckets.items()
    ]
    # 成交额降序;同额按行业名升序,保证报告/测试确定性
    sector_summary.sort(key=lambda x: (-x["amount_billion"], x["industry"]))

    classified = sum(
        1 for s in stocks if (s.get("industry") or UNCLASSIFIED) != UNCLASSIFIED
    )
    coverage = (classified / len(stocks)) if stocks else 0.0

    return {
        "top_n": len(stocks),
        "total_amount_billion": round(total, 2),
        "sector_summary": sector_summary,
        "industry_coverage": coverage,
    }
