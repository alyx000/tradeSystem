"""堆量观察的部分覆盖行业背景；只比较两日均有成交额的固定组成。"""
from collections import defaultdict
import math
import re

from analyzers.theme_launch import _amounts


def calculate(block, day):
    previous = block["previous_date"]
    amounts, _, _ = _amounts(block, day, previous)
    industries = {r["ts_code"]: r["industry"] for r in block["components"]}
    totals = defaultdict(lambda: [0., 0.])
    common = defaultdict(list)
    for code, (before, after) in amounts.items():
        name = industries[code]
        if not isinstance(name, str) or not name.strip():
            raise ValueError("成交额成分缺少行业")
        all_amounts = totals[name]
        if before is not None:
            all_amounts[0] += before
        if after is not None:
            all_amounts[1] += after
        if before is not None and after is not None:
            common[name].append((before, after))
    rows = {r["industry"]: r for r in block["rows"]}
    if set(rows) != set(totals):
        raise ValueError("成交额成分与行业名单不符")
    for name, sums in totals.items():
        for actual, key in zip(sums, ("previous_amount_yi", "amount_yi")):
            if not math.isclose(actual, rows[name][key], rel_tol=1e-9, abs_tol=1e-7):
                raise ValueError("逐股成交额与行业合计不符")
    coverage = {}
    for column, d in enumerate((previous, day)):
        source = block["coverage"][d]
        missing = source["missing_codes"]
        present = {c for c, values in amounts.items() if values[column] is not None}
        if (not isinstance(missing, list) or len(missing) != len(set(missing))
                or any(not isinstance(c, str) or not re.fullmatch(r"\d{6}\.(SH|SZ)", c) for c in missing)
                or present.intersection(missing) or len(present) != source["quote_count"]
                or len(present) + len(missing) != source["universe_count"]):
            raise ValueError("成交额逐股名单与覆盖不符")
        proofs = block.get("missing_quote_suspensions", {}).get(d, [])
        verified = set()
        for proof in proofs:
            c = proof["code"]
            if (c not in missing or c in verified or proof["date"] != d or proof["source"] != "tushare:suspend_d"
                    or proof["full_day"] is not True or proof["suspend_type"] != "S"
                    or type(proof["raw_payload_id"]) is not int or proof["raw_payload_id"] <= 0):
                raise ValueError("缺行情的停牌证据不符")
            verified.add(c)
        coverage[d] = dict(quote_count=len(present), universe_count=source["universe_count"],
                           verified_full_day=sorted(verified), unexplained=sorted(set(missing)-verified))
    samples = {}
    for name, pairs in common.items():
        if name == "未分类":
            continue
        before, after = (sum(values) for values in zip(*pairs))
        samples[name] = dict(previous_date=previous, member_count=len(pairs), previous_amount_yi=before,
                             amount_yi=after, delta_yi=after-before)
    return samples, {c[:6]: name for c, name in industries.items()}, coverage
