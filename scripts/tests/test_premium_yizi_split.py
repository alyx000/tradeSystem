"""
PremiumCollector.collect 的一字首开/一字延续拆分测试（H 修复回归）。

背景：2026-07-27 爱丽家居 T-1（07-24）一字 4 板、T 日继续一字 +10%，
旧口径被算进 yizi_first_open 并以涨停价当"开盘溢价"，系统性抬高该组均值。
新口径：T 日 high==low（一字，无价差成交）→ yizi_continued；
high>low（真实打开）→ yizi_first_open；high/low 缺失或脏 → 两桶都不进，只计 yizi_undetermined。
"""
from unittest.mock import MagicMock

import pytest
import yaml

import collectors.premium as premium_mod
from collectors.premium import PremiumCollector


def _daily_result(open_, close, high, low, pct_chg=0.0):
    r = MagicMock()
    r.success = True
    r.data = {"open": open_, "close": close, "high": high, "low": low, "pct_chg": pct_chg}
    return r


def _registry(daily_map):
    """daily_map: {code: (open, close, high, low)}；其余接口一律失败（走空分支）"""
    registry = MagicMock()

    def _call(method, *args, **kwargs):
        if method == "get_stock_daily":
            code = args[0]
            if code in daily_map:
                o, c, h, low = daily_map[code]
                return _daily_result(o, c, h, low)
        r = MagicMock()
        r.success = False
        r.data = None
        r.error = "no data"
        return r

    registry.call = _call
    return registry


def _yizi_stock(code, name, close, limit_times):
    """T-1 一字连板：first_time == last_time"""
    return {
        "code": code, "name": name, "close": close, "limit_times": limit_times,
        "pct_chg": 10.0, "first_time": "09:25", "last_time": "09:25",
        "amount_billion": 1.0,
    }


def _open_board_stock(code, name, close, limit_times):
    """T-1 非一字涨停（盘中封板）"""
    return {
        "code": code, "name": name, "close": close, "limit_times": limit_times,
        "pct_chg": 10.0, "first_time": "10:00", "last_time": "14:00",
        "amount_billion": 1.0,
    }


@pytest.fixture
def daily_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(premium_mod, "DAILY_DIR", tmp_path)
    return tmp_path


def _write_prev_yaml(daily_dir, prev_date, stocks):
    d = daily_dir / prev_date
    d.mkdir(parents=True)
    payload = {"raw_data": {"limit_up": {"stocks": stocks}}}
    with open(d / "post-market.yaml", "w", encoding="utf-8") as f:
        yaml.dump(payload, f, allow_unicode=True)


class TestYiziSplit:
    def test_continued_yizi_goes_to_continued_not_first_open(self, daily_dir):
        # 爱丽家居场景：T-1 一字 4 板 10 元收盘，T 日一字 +10%（open=high=low=close=11）
        _write_prev_yaml(daily_dir, "2026-07-24", [_yizi_stock("603221", "爱丽家居", 10.0, 4)])
        collector = PremiumCollector(_registry({"603221": (11.0, 11.0, 11.0, 11.0)}))
        result = collector.collect("2026-07-27", "2026-07-24")
        assert result["yizi_first_open"]["count"] == 0
        assert result["yizi_continued"]["count"] == 1
        assert result["yizi_continued"]["premium_median"] == pytest.approx(10.0)
        assert result["yizi_continued"]["detail"][0]["t_opened"] is False

    def test_truly_opened_yizi_goes_to_first_open(self, daily_dir):
        # 顺钠场景：T-1 一字 2 板，T 日高开 +5.65% 后有价差成交
        _write_prev_yaml(daily_dir, "2026-07-24", [_yizi_stock("000533", "顺钠股份", 10.0, 2)])
        collector = PremiumCollector(_registry({"000533": (10.57, 11.0, 11.0, 10.30)}))
        result = collector.collect("2026-07-27", "2026-07-24")
        assert result["yizi_first_open"]["count"] == 1
        assert result["yizi_continued"]["count"] == 0
        assert result["yizi_first_open"]["premium_median"] == pytest.approx(5.7)
        assert result["yizi_first_open"]["detail"][0]["t_opened"] is True

    def test_missing_high_low_fail_closed(self, daily_dir):
        _write_prev_yaml(daily_dir, "2026-07-24", [_yizi_stock("600000", "样例", 10.0, 2)])
        collector = PremiumCollector(_registry({"600000": (10.5, 10.8, None, None)}))
        result = collector.collect("2026-07-27", "2026-07-24")
        assert result["yizi_first_open"]["count"] == 0
        assert result["yizi_continued"]["count"] == 0
        assert result["yizi_undetermined"] == 1

    def test_dirty_high_below_low_fail_closed(self, daily_dir):
        _write_prev_yaml(daily_dir, "2026-07-24", [_yizi_stock("600001", "样例二", 10.0, 3)])
        collector = PremiumCollector(_registry({"600001": (10.5, 10.8, 10.2, 10.9)}))
        result = collector.collect("2026-07-27", "2026-07-24")
        assert result["yizi_first_open"]["count"] == 0
        assert result["yizi_continued"]["count"] == 0
        assert result["yizi_undetermined"] == 1

    def test_non_yizi_and_first_board_unaffected(self, daily_dir):
        # 盘中板二板不进一字桶；一字首板只进 first_board_yizi，两个连板一字桶不收
        _write_prev_yaml(daily_dir, "2026-07-24", [
            _open_board_stock("000001", "盘中二板", 10.0, 2),
            _yizi_stock("000002", "一字首板", 10.0, 1),
        ])
        collector = PremiumCollector(_registry({
            "000001": (10.3, 11.0, 11.0, 10.1),
            "000002": (11.0, 11.0, 11.0, 11.0),
        }))
        result = collector.collect("2026-07-27", "2026-07-24")
        assert result["yizi_first_open"]["count"] == 0
        assert result["yizi_continued"]["count"] == 0
        assert result["yizi_undetermined"] == 0
        assert result["second_board"]["count"] == 1
        assert result["first_board_yizi"]["count"] == 1

    def test_mixed_cohort_median_only_from_opened(self, daily_dir):
        # 07-27 真实结构缩样：2 只继续一字（+20/+10）+ 3 只真实打开（+5.65/0/-0.92）
        _write_prev_yaml(daily_dir, "2026-07-24", [
            _yizi_stock("301234", "五洲医疗", 10.0, 3),
            _yizi_stock("603221", "爱丽家居", 10.0, 4),
            _yizi_stock("000533", "顺钠股份", 10.0, 2),
            _yizi_stock("000668", "荣丰控股", 10.0, 2),
            _yizi_stock("002498", "汉缆股份", 10.0, 2),
        ])
        collector = PremiumCollector(_registry({
            "301234": (12.0, 12.0, 12.0, 12.0),
            "603221": (11.0, 11.0, 11.0, 11.0),
            "000533": (10.565, 11.0, 11.0, 10.3),
            "000668": (10.0, 10.76, 10.9, 9.9),
            "002498": (9.908, 10.92, 11.0, 9.85),
        }))
        result = collector.collect("2026-07-27", "2026-07-24")
        assert result["yizi_continued"]["count"] == 2
        assert result["yizi_first_open"]["count"] == 3
        # 中位数只由真实打开的 3 只决定（+5.65 / 0.0 / -0.92 → 0.0），不再被一字延续抬高
        assert result["yizi_first_open"]["premium_median"] == pytest.approx(0.0)
        assert result["yizi_continued"]["premium_median"] == pytest.approx(15.0)

    def test_one_price_limit_down_excluded_from_continued(self, daily_dir):
        # 一字连板次日一字跌停（天地一字单价，高低同价但溢价为负）：
        # 既非首开也非涨停延续，fail-closed 计入 undetermined
        _write_prev_yaml(daily_dir, "2026-07-24", [_yizi_stock("600002", "反核样例", 10.0, 3)])
        collector = PremiumCollector(_registry({"600002": (9.0, 9.0, 9.0, 9.0)}))
        result = collector.collect("2026-07-27", "2026-07-24")
        assert result["yizi_first_open"]["count"] == 0
        assert result["yizi_continued"]["count"] == 0
        assert result["yizi_undetermined"] == 1

    def test_bucket_conservation(self, daily_dir):
        # 守恒：T-1 一字连板总数 = 首开 + 延续 + undetermined
        _write_prev_yaml(daily_dir, "2026-07-24", [
            _yizi_stock("603221", "延续", 10.0, 4),
            _yizi_stock("000533", "打开", 10.0, 2),
            _yizi_stock("600000", "缺高低", 10.0, 2),
            _yizi_stock("600002", "一字跌停", 10.0, 3),
        ])
        collector = PremiumCollector(_registry({
            "603221": (11.0, 11.0, 11.0, 11.0),
            "000533": (10.57, 11.0, 11.0, 10.3),
            "600000": (10.5, 10.8, None, None),
            "600002": (9.0, 9.0, 9.0, 9.0),
        }))
        result = collector.collect("2026-07-27", "2026-07-24")
        total = (
            result["yizi_first_open"]["count"]
            + result["yizi_continued"]["count"]
            + result["yizi_undetermined"]
        )
        assert total == 4
        assert result["yizi_first_open"]["count"] == 1
        assert result["yizi_continued"]["count"] == 1
        assert result["yizi_undetermined"] == 2

    def test_string_prices_parsed(self, daily_dir):
        # yaml 源字段可能是字符串价：_to_price 须能转换并正确分桶
        _write_prev_yaml(daily_dir, "2026-07-24", [_yizi_stock("603221", "串价延续", 10.0, 4)])
        collector = PremiumCollector(_registry({"603221": ("11.0", "11.0", "11.0", "11.0")}))
        result = collector.collect("2026-07-27", "2026-07-24")
        assert result["yizi_continued"]["count"] == 1
        assert result["yizi_first_open"]["count"] == 0

    def test_report_contains_both_groups(self, daily_dir):
        _write_prev_yaml(daily_dir, "2026-07-24", [
            _yizi_stock("603221", "爱丽家居", 10.0, 4),
            _yizi_stock("000533", "顺钠股份", 10.0, 2),
        ])
        collector = PremiumCollector(_registry({
            "603221": (11.0, 11.0, 11.0, 11.0),
            "000533": (10.57, 11.0, 11.0, 10.3),
        }))
        result = collector.collect("2026-07-27", "2026-07-24")
        report = collector.format_report(result)
        assert "一字首开（连板，T日真实打开）" in report
        assert "一字延续（连板，T日仍未开）" in report
