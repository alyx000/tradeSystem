"""取既有盘后块或旧日的独立逐股回验，供正式复盘③展示。"""
import json
from pathlib import Path

from analyzers import limit_down_touch as touch


def load(daily_dir, fact_date, envelope):
    primary_gap = None
    try:
        if isinstance(envelope, dict):
            raw = envelope.get("raw_data")
            if isinstance(raw, dict) and touch.KEY in raw:
                try:
                    if raw.get("date") != fact_date or envelope.get("date") != fact_date:
                        raise ValueError("盘后信封事实日错位")
                    return touch.verified(raw[touch.KEY], fact_date)
                except (ValueError, KeyError, TypeError, IndexError) as exc:
                    primary_gap = str(exc)
        path = Path(daily_dir).parent / "data/reports/limit-down-touch" / (fact_date + ".json")
        if not path.exists():
            raise ValueError(primary_gap or "该日尚无可核验的跌停触价归档")
        obj = json.loads(path.read_text())
        if obj.get("trade_date") != fact_date or obj.get("mode") != "archived_sources":
            raise ValueError("独立回验事实日或模式错误")
        return touch.verified(obj[touch.KEY], fact_date)
    except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
        return touch.unavailable(fact_date, str(exc))
