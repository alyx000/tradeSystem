"""Apply the MA30 rule to the dated report scripts before they run."""

import argparse
from pathlib import Path
from datetime import date


RUNS = Path(__file__).resolve().parents[2] / "data/reports/tgb-wide-range/runs"
MARKER = "# wide-range MA30 rule"


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f"未找到唯一的脚本锚点 {old.strip()}")
    return text.replace(old, new, 1)


def collection_script(text):
    if MARKER in text:
        if "ok=legacy_match and ma30_pass" not in text:
            raise ValueError("MA30规则标记与筛选不一致")
        return text
    text = replace_once(text, "\n ok=amp>=10", "\n legacy_match=amp>=10")
    text = replace_once(text, "\n screened.append(", """
 # wide-range MA30 rule
 # g already contains date-ordered, forward-adjusted OHLC.
 recent_closes = g.close.tail(30)
 close_ma30 = float(recent_closes.iloc[-1])
 valid_ma30 = len(recent_closes) == 30 and all(math.isfinite(p) and p > 0 for p in recent_closes)
 ma30 = math.fsum(recent_closes) / 30 if valid_ma30 else None
 ma30_pass = valid_ma30 and (close_ma30 >= ma30 or math.isclose(close_ma30, ma30, rel_tol=1e-12))
 ma30_fields = dict(close_qfq=close_ma30, ma30_qfq=ma30, ma30_pass=ma30_pass, legacy_match=legacy_match)
 ok=legacy_match and ma30_pass
 screened.append(""")
    text = replace_once(text, "\n reason['calculated']+=1", "\n screened[-1].update(ma30_fields)\n reason['calculated']+=1")
    text = replace_once(text, "\nx=pd.DataFrame(result)", "\n result[-1].update(ma30_fields)\nx=pd.DataFrame(result)")
    return replace_once(text, "columns=['code','name','amp5']", "columns=['code','name','amp5','close_qfq','ma30_qfq','ma30_pass','legacy_match']")


def finalization_script(text):
    if MARKER in text:
        if "收盘低于30日均线" not in text:
            raise ValueError("MA30规则标记与排除原因不一致")
        return text
    text = replace_once(text, "\n return '；'.join(reasons)", """
 # wide-range MA30 rule
 if not z.ma30_pass:
  reasons.append('MA30有效数据不足' if pd.isna(z.ma30_qfq) else '收盘低于30日均线')
 return '；'.join(reasons)""")
    return replace_once(text, "\np=r/'report.md';", """
ma30_only = s[s.legacy_match & ~s.ma30_pass]
lines += ['', f'[事实] 收盘须不低于含当日的近30根有效日线前复权收盘均值。其余条件通过但因MA30门槛被排除{len(ma30_only)}只。个股收盘、MA30及判断见full-screen.csv。',
          '[判断] MA30是额外筛选条件。原文的箱体边界与堆量起点成本支撑仍需单独核对。']
p=r/'report.md';""")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-date", required=True, type=date.fromisoformat)
    parser.add_argument("--input-by", required=True)
    args = parser.parse_args()
    run = RUNS / args.run_date.isoformat()
    started = ("collect.log", "attempts.txt", "source_receipt.pkl", "daily.pkl", "screened.pkl",
               "report.md", "verification.txt", "SHA256SUMS")
    if not args.input_by.strip() or any((run / name).exists() for name in started):
        parser.error("仅可用于未运行的新日期目录，须提供请求者")
    scripts = {run / "collect.py": collection_script, run / "finalize.py": finalization_script}
    updated = {path: transform(path.read_text()) for path, transform in scripts.items()}
    for path, content in updated.items():
        compile(content, str(path), "exec")
    for path, content in updated.items():
        if path.read_text() != content:
            path.write_text(content)
    print(f"MA30规则已确认 {args.run_date} input_by={args.input_by}")


if __name__ == "__main__":
    main()
