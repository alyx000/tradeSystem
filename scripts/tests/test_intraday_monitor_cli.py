from __future__ import annotations

import argparse
from datetime import datetime

import pytest

import main
from cli import intraday_monitor
from services.intraday_monitor.rules import (
    CHANGCHUN_GAS_NEAR_MA20_20260914_22,
    CHANGCHUN_GAS_RECLAIM_MA5_20260914_22,
    FULONGMA_BREAKOUT_14_36_20260916_24,
    SHUANGXING_MATERIALS_BREAKOUT_12_98_20260917_1016,
    SUNWODA_BREAKOUT_19_94_20260917_28,
    FEILONG_BREAKOUT_57_16_20260917_1008,
    FEILONG_RECLAIM_MA5_20260921_23,
    KEXIANG_BELOW_108_30_20260922_1008,
    YOUYAN_SILICON_BREAKOUT_57_96_20260922_1008,
    NANHUA_BIO_BOARD_BREAK_20260923,
    YOUYAN_SILICON_BREAKOUT_46_14_20260916_30,
    DAJIN_HEAVY_BREAKOUT_41_96_20260917_28,
    FANGSHENG_REACH_11_11_20260921_1012,
    HAOXIANGNI_BREAKOUT_11_24_20260909_22,
    LIANGPIN_STORE_BREAKOUT_10_17_20260909_22,
    MEDICILON_BELOW_87_65_20260907_1006,
    PINWO_FOODS_BREAKOUT_25_89_20260909_22,
)


def _args(*, confirm_real_push: bool = True, rule_id: str | None = None) -> argparse.Namespace:
    return argparse.Namespace(
        intraday_monitor_command="e2e-test",
        input_by="pytest",
        confirm_real_push=confirm_real_push,
        rule_id=rule_id or intraday_monitor.DEFAULT_RULES[0].rule_id,
        json=True,
    )


def _check_args() -> argparse.Namespace:
    return argparse.Namespace(
        intraday_monitor_command="check",
        dry_run=False,
        json=True,
    )


@pytest.mark.parametrize("rule_id", [
    YOUYAN_SILICON_BREAKOUT_46_14_20260916_30.rule_id,
    "fulongma-breakout-14-15-20260916-24",
    "dajin-heavy-breakout-35-95-20260912-18",
    "fangsheng-reach-11-11-20260903-16",
])
def test_retired_rule_is_not_selectable_for_real_e2e(rule_id):
    parser = argparse.ArgumentParser()
    intraday_monitor.register_subparser(parser.add_subparsers())
    with pytest.raises(SystemExit) as error:
        parser.parse_args(["intraday-monitor", "e2e-test", "--rule-id", rule_id,
                           "--input-by", "pytest", "--confirm-real-push"])
    assert error.value.code == 2


def test_check_cli_initializes_provider_for_active_rule(monkeypatch, capsys):
    registry = object()
    monkeypatch.setattr(main, "setup_providers", lambda config: registry)
    calls = []

    def fake_run_all_checks(registry, *, dry_run):
        calls.append((registry, dry_run))
        return {
            "status": "complete",
            "events": [],
            "errors": [],
            "quotes_checked": 1,
            "pending_count": 0,
            "pushed": False,
        }

    monkeypatch.setattr(intraday_monitor, "run_all_checks", fake_run_all_checks)

    assert intraday_monitor.handle_command({}, _check_args()) == 0
    output = capsys.readouterr().out
    assert '"status": "complete"' in output
    assert '"pushed": false' in output
    assert calls == [(registry, False)]


def test_e2e_cli_initializes_provider_for_active_rule(monkeypatch, capsys):
    registry = object()
    monkeypatch.setattr(main, "setup_providers", lambda config: registry)
    calls = []
    monkeypatch.setattr(
        intraday_monitor,
        "run_e2e_test",
        lambda got, input_by, confirm_real_push, rule: calls.append(
            (got, input_by, confirm_real_push, rule)
        ) or {
            "status": "complete",
            "events": [{}],
            "errors": [],
            "pushed": True,
        },
    )

    assert intraday_monitor.handle_command({}, _args()) == 0
    output = capsys.readouterr().out
    assert '"status": "complete"' in output
    assert '"pushed": true' in output
    assert calls == [(registry, "pytest", True, intraday_monitor.DEFAULT_RULES[0])]


def test_e2e_cli_rejects_inactive_rule_before_provider_setup(monkeypatch, capsys):
    monkeypatch.setattr(
        main,
        "setup_providers",
        lambda config: (_ for _ in ()).throw(
            AssertionError("过期规则不得初始化行情 provider")
        ),
    )
    selected = intraday_monitor.DEFAULT_RULES[1]

    assert intraday_monitor.handle_command({}, _args(rule_id=selected.rule_id)) == 1
    assert '"status": "inactive_rule"' in capsys.readouterr().out


def test_e2e_cli_selects_active_star50_rule(monkeypatch, capsys):
    registry = object()
    monkeypatch.setattr(main, "setup_providers", lambda config: registry)
    monkeypatch.setattr(
        intraday_monitor,
        "shanghai_now",
        lambda: datetime(2026, 8, 21, 10, 0),
    )
    calls = []
    monkeypatch.setattr(
        intraday_monitor,
        "run_e2e_test",
        lambda got, input_by, confirm_real_push, rule: calls.append(rule) or {
            "status": "complete",
            "events": [{}],
            "errors": [],
            "pushed": True,
        },
    )
    selected = intraday_monitor.DEFAULT_RULES[2]

    assert intraday_monitor.handle_command({}, _args(rule_id=selected.rule_id)) == 0
    assert calls == [selected]
    assert '"status": "complete"' in capsys.readouterr().out


def test_e2e_cli_selects_active_kailaiying_rule(monkeypatch, capsys):
    registry = object()
    monkeypatch.setattr(main, "setup_providers", lambda config: registry)
    monkeypatch.setattr(
        intraday_monitor,
        "shanghai_now",
        lambda: datetime(2026, 8, 21, 10, 0),
    )
    calls = []
    monkeypatch.setattr(
        intraday_monitor,
        "run_e2e_test",
        lambda got, input_by, confirm_real_push, rule: calls.append(rule) or {
            "status": "complete",
            "events": [{}],
            "errors": [],
            "pushed": True,
        },
    )
    selected = intraday_monitor.DEFAULT_RULES[3]

    assert intraday_monitor.handle_command({}, _args(rule_id=selected.rule_id)) == 0
    assert calls == [selected]
    assert '"status": "complete"' in capsys.readouterr().out


def test_e2e_cli_selects_new_guoci_zhongke_and_ths_rules(monkeypatch, capsys):
    registry = object()
    monkeypatch.setattr(main, "setup_providers", lambda config: registry)
    monkeypatch.setattr(
        intraday_monitor,
        "shanghai_now",
        lambda: datetime(2026, 8, 31, 10, 0),
    )
    calls = []
    monkeypatch.setattr(
        intraday_monitor,
        "run_e2e_test",
        lambda got, input_by, confirm_real_push, rule: calls.append(rule) or {
            "status": "complete",
            "events": [{}],
            "errors": [],
            "pushed": True,
        },
    )

    for selected in intraday_monitor.DEFAULT_RULES[4:7]:
        assert intraday_monitor.handle_command({}, _args(rule_id=selected.rule_id)) == 0

    assert calls == list(intraday_monitor.DEFAULT_RULES[4:7])
    assert capsys.readouterr().out.count('"status": "complete"') == 3


def test_e2e_cli_selects_fangsheng_rule(monkeypatch, capsys):
    registry = object()
    monkeypatch.setattr(main, "setup_providers", lambda config: registry)
    monkeypatch.setattr(intraday_monitor, "shanghai_now", lambda: datetime(2026, 9, 21, 10))
    calls = []
    monkeypatch.setattr(
        intraday_monitor, "run_e2e_test",
        lambda got, input_by, confirm_real_push, rule: calls.append((got, rule)) or {
            "status": "complete", "events": [{}], "errors": [], "pushed": True,
        },
    )
    rule = FANGSHENG_REACH_11_11_20260921_1012
    assert intraday_monitor.handle_command({}, _args(rule_id=rule.rule_id)) == 0
    assert calls == [(registry, rule)]


def test_e2e_cli_selects_medicilon_rule(monkeypatch, capsys):
    registry = object()
    monkeypatch.setattr(main, "setup_providers", lambda config: registry)
    monkeypatch.setattr(intraday_monitor, "shanghai_now", lambda: datetime(2026, 9, 7, 10))
    calls = []
    monkeypatch.setattr(
        intraday_monitor, "run_e2e_test",
        lambda got, input_by, confirm_real_push, rule: calls.append((got, rule)) or {
            "status": "complete", "events": [{}], "errors": [], "pushed": True,
        },
    )
    rule = MEDICILON_BELOW_87_65_20260907_1006
    assert intraday_monitor.handle_command({}, _args(rule_id=rule.rule_id)) == 0
    assert calls == [(registry, rule)]


@pytest.mark.parametrize(
    "selected_rule",
    (
        HAOXIANGNI_BREAKOUT_11_24_20260909_22,
        PINWO_FOODS_BREAKOUT_25_89_20260909_22,
        LIANGPIN_STORE_BREAKOUT_10_17_20260909_22,
        DAJIN_HEAVY_BREAKOUT_41_96_20260917_28,
        CHANGCHUN_GAS_NEAR_MA20_20260914_22,
        CHANGCHUN_GAS_RECLAIM_MA5_20260914_22,
        FULONGMA_BREAKOUT_14_36_20260916_24,
        SHUANGXING_MATERIALS_BREAKOUT_12_98_20260917_1016,
        SUNWODA_BREAKOUT_19_94_20260917_28,
        FEILONG_BREAKOUT_57_16_20260917_1008,
        FEILONG_RECLAIM_MA5_20260921_23,
        KEXIANG_BELOW_108_30_20260922_1008,
        YOUYAN_SILICON_BREAKOUT_57_96_20260922_1008,
        NANHUA_BIO_BOARD_BREAK_20260923,
    ),
)
def test_e2e_cli_selects_new_two_week_breakout_rules(monkeypatch, capsys, selected_rule):
    registry = object()
    monkeypatch.setattr(main, "setup_providers", lambda config: registry)
    check_day = 23 if selected_rule == NANHUA_BIO_BOARD_BREAK_20260923 else 22
    monkeypatch.setattr(intraday_monitor, "shanghai_now", lambda: datetime(2026, 9, check_day, 10))
    calls = []
    monkeypatch.setattr(
        intraday_monitor, "run_e2e_test",
        lambda got, input_by, confirm_real_push, rule: calls.append((got, rule)) or {
            "status": "complete", "events": [{}], "errors": [], "pushed": True,
        },
    )
    assert intraday_monitor.handle_command({}, _args(rule_id=selected_rule.rule_id)) == 0
    assert calls == [(registry, selected_rule)]


def test_e2e_cli_requires_explicit_real_push_confirmation_before_provider_setup(
    monkeypatch,
    capsys,
):
    monkeypatch.setattr(
        main,
        "setup_providers",
        lambda config: (_ for _ in ()).throw(
            AssertionError("缺少显式确认时不得初始化行情 provider")
        ),
    )
    calls = []
    monkeypatch.setattr(
        intraday_monitor,
        "run_e2e_test",
        lambda registry, input_by, confirm_real_push, rule: calls.append(
            (registry, input_by, confirm_real_push, rule)
        ) or {
            "status": "authorization_required",
            "events": [],
            "errors": ["必须显式确认"],
            "pushed": False,
        },
    )

    denied_values = (False, None, 1, "false", "true")
    for denied_value in denied_values:
        assert (
            intraday_monitor.handle_command(
                {},
                _args(confirm_real_push=denied_value),
            )
            == 1
        )
    output = capsys.readouterr().out
    assert '"status": "authorization_required"' in output
    assert calls == [
        (None, "pytest", False, intraday_monitor.DEFAULT_RULES[0])
    ] * len(denied_values)


def test_help_describes_current_rules_at_every_command_level():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    intraday_monitor.register_subparser(subparsers)
    root_choices = next(
        action.choices
        for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)
    )
    intraday_parser = root_choices["intraday-monitor"]
    command_choices = next(
        action.choices
        for action in intraday_parser._actions
        if isinstance(action, argparse._SubParsersAction)
    )

    root_help = "".join(intraday_parser.format_help().split())
    assert "上证指数从3955点下方" in root_help
    assert "2026年8月21日与24日监控科创50严格突破1700点" in root_help
    assert "凯莱英严格突破172.26元" in root_help
    assert "8月31日监控国瓷材料严格跌破67.22元" in root_help
    assert "中科飞测严格跌破前5个已收盘交易日的前复权MA5" in root_help
    assert "同花顺全A（沪深）单日跌幅严格超过4.00%" in root_help
    assert "2026年9月21日至10月12日监控方盛制药达到或高于11.11元" in root_help
    assert "2026年9月9日至22日监控好想你严格突破11.24元" in root_help
    assert "品渥食品严格突破25.89元" in root_help
    assert "良品铺子严格突破10.17元" in root_help
    assert "每3分钟扫描" in root_help
    assert "2026年9月14日至22日（7个交易日）监控长春燃气进入动态前复权MA20±1%范围" in root_help
    assert "MA20使用前19个已收盘交易日与当日最新价" in root_help
    assert "长春燃气重新站上动态前复权MA5" in root_help
    assert "MA5使用前4个已收盘交易日与当日最新价" in root_help
    assert "2026年9月17日至28日（7个交易日）监控大金重工严格突破41.96元" in root_help
    assert "当日累计成交额不少于100亿元" in root_help
    check_help = "".join(command_choices["check"].format_help().split())
    assert "历史已退役规则保持下线" in check_help
    assert "科创50严格高于1700点" in check_help
    assert "凯莱英严格高于172.26元时推送" in check_help
    assert "国瓷材料严格低于67.22元" in check_help
    assert "中科飞测严格低于前5个已收盘交易日MA5时推送" in check_help
    assert "同花顺全A（沪深）单日涨跌幅严格低于-4.00%时推送" in check_help
    assert "2026年9月21日至10月12日方盛制药达到或高于11.11元时推送" in check_help
    assert "2026年9月7日至10月6日美迪西严格低于87.65元时推送" in check_help
    assert "2026年9月9日至22日好想你严格高于11.24元" in check_help
    assert "品渥食品严格高于25.89元" in check_help
    assert "良品铺子严格高于10.17元时推送" in check_help
    assert "2026年9月17日至28日大金重工严格高于41.96元时推送" in check_help
    assert "旧35.95元规则已停用" in check_help
    assert "持续命中去重" in check_help
    assert "长春燃气进入动态前复权MA20±1%范围（含边界）时推送" in check_help
    assert "每日首次采样已在线上不补报" in check_help
    assert "福龙马严格高于14.36元时推送" in check_help
    assert "双星新材严格高于12.98元时推送" in check_help
    assert "欣旺达严格高于19.94元时推送" in check_help
    assert "飞龙股份严格高于57.16元时推送" in check_help
    assert "2026年9月22日至10月8日（7个交易日）监控科翔股份严格跌破108.30元" in root_help
    assert "科翔股份严格低于108.30元时推送；等于不触发，首次已跌破提醒" in check_help
    assert "2026年9月21日至23日（3个交易日）另监控飞龙股份重新站上动态前复权MA5" in root_help
    assert "飞龙股份从不高于动态前复权MA5变为严格高于时推送" in check_help
    assert "每日首次采样已在线上不补报，57.16元规则保留" in check_help
    assert "2026年9月17日至10月8日（10个交易日）监控飞龙股份严格突破57.16元" in root_help
    assert "2026年9月17日至28日（7个交易日）监控欣旺达严格突破19.94元" in root_help
    assert "2026年9月17日至10月16日（一个月）监控双星新材严格突破12.98元" in root_help
    assert "2026年9月22日至10月8日（7个交易日）监控有研硅严格突破57.96元" in root_help
    assert "有研硅严格高于57.96元时推送；等于不触发，首次已突破提醒" in check_help
    assert "旧46.14元规则保持下线" in root_help
    assert "旧46.14元规则保持下线" in check_help
    assert "2026年9月23日仅一天监控南华生物断板风险" in root_help
    assert "盘中未封涨停提醒，收盘低于当日涨停价才确认断板" in root_help
    assert "南华生物严格低于当日动态涨停价时推送" in check_help
    assert "收盘仍未封板另发断板确认，9月24日起停用" in check_help
    assert "2026年9月16日至24日（7个交易日）另监控福龙马严格突破14.36元" in root_help
    assert "恢复后再次命中可重推" in check_help
    assert "10点前百亿成交额涨停板" in check_help
    e2e_help = command_choices["e2e-test"].format_help()
    assert "默认上证指数3955规则" in e2e_help
    assert "不读写正式监控状态" in e2e_help
    assert "--confirm-real-push" in e2e_help
    assert "--rule-id" in e2e_help


def test_e2e_cli_returns_nonzero_when_verification_did_not_complete(monkeypatch, capsys):
    monkeypatch.setattr(main, "setup_providers", lambda config: object())
    monkeypatch.setattr(
        intraday_monitor,
        "run_e2e_test",
        lambda registry, input_by, confirm_real_push, rule: {
            "status": "outside_session",
            "events": [],
            "errors": [],
        },
    )

    assert intraday_monitor.handle_command({}, _args()) == 1
    assert '"status": "outside_session"' in capsys.readouterr().out


def test_e2e_cli_returns_zero_only_for_complete_verification(monkeypatch, capsys):
    monkeypatch.setattr(main, "setup_providers", lambda config: object())
    monkeypatch.setattr(
        intraday_monitor,
        "run_e2e_test",
        lambda registry, input_by, confirm_real_push, rule: {
            "status": "complete",
            "events": [{}],
            "errors": [],
            "pushed": True,
        },
    )

    assert intraday_monitor.handle_command({}, _args()) == 0
    assert '"status": "complete"' in capsys.readouterr().out


def test_check_cli_returns_nonzero_for_partial_monitoring(monkeypatch, capsys):
    monkeypatch.setattr(main, "setup_providers", lambda config: object())
    monkeypatch.setattr(
        intraday_monitor,
        "run_all_checks",
        lambda registry, dry_run: {
            "status": "partial",
            "events": [],
            "errors": ["目标行情缺失"],
            "pushed": False,
        },
    )

    assert intraday_monitor.handle_command({}, _check_args()) == 1
    assert '"status": "partial"' in capsys.readouterr().out
