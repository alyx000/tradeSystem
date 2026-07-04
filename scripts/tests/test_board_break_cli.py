"""scripts/tests/test_board_break_cli.py —— 运行模式副作用（全 mock，不外网）

monkeypatch cli.board_break 模块内符号（scanner/scorer/pk/renderer + get_connection +
_push_to_dingtalk），用 argparse.Namespace 直接调 `_run_daily`，验证六种运行模式的
落盘/推送副作用是否符合 spec：

- dry_run 不落盘不推
- no_push 落盘不推
- 默认 落盘+推
- source_failed 不落盘不推正常清单
- no_llm 不调 pk.run_pk
- 非交易日直接 return（scanner.run_daily 不被调用）
"""
from __future__ import annotations

import argparse

import main as main_module
from cli import board_break as bb


class _Conn:
    def close(self):
        pass


class _Registry:
    def initialize_all(self):
        pass


def _daily_args(**over):
    base = dict(date="2026-07-04", dry_run=False, no_push=False, no_llm=False)
    base.update(over)
    return argparse.Namespace(**base)


def _ok_result():
    return {
        "status": "ok", "date": "2026-07-04", "prev_trade_date": "2026-07-03",
        "candidates": [{"code": "600002", "name": "某票"}], "rejects": {},
        "sources": {}, "empty_kind": None, "main_sectors": [], "main_sector_degraded": False,
    }


def _wire(monkeypatch, *, result=None, non_trading=False):
    """接线：setup_providers→FakeRegistry，get_connection→假连接，scanner/scorer/pk/renderer
    全 mock，_push_to_dingtalk→记账。返回记账容器供断言。
    """
    monkeypatch.setattr(main_module, "setup_providers", lambda config: _Registry())
    monkeypatch.setattr(bb, "get_connection", lambda: _Conn())

    import utils.trade_date as trade_date
    monkeypatch.setattr(trade_date, "is_non_trading_day", lambda conn, registry, date: non_trading)

    scan_calls = []

    def fake_run_daily(conn, registry, date):
        scan_calls.append(date)
        return result if result is not None else _ok_result()

    monkeypatch.setattr(bb.scanner, "run_daily", fake_run_daily)

    fact_cards = [{"code": "600002", "name": "某票"}]
    scored = [{"code": "600002", "name": "某票", "total": 1.0, "rank_score": 1, "evidences": []}]
    monkeypatch.setattr(bb.scorer, "build_fact_cards", lambda conn, registry, res: fact_cards)
    monkeypatch.setattr(bb.scorer, "score_all", lambda cards: scored)

    monkeypatch.setattr(bb.renderer, "render_daily", lambda res, sc, pkres: "# 断板反包观察清单 md")
    monkeypatch.setattr(bb.renderer, "render_source_failed", lambda res: "# 断板反包 数据失败 md")

    saves = []
    monkeypatch.setattr(bb.renderer, "save_report", lambda md, date: saves.append((md, date)))

    pushes = []
    monkeypatch.setattr(bb, "_push_to_dingtalk", lambda title, md: pushes.append((title, md)))

    pk_calls = []
    monkeypatch.setattr(bb.pk, "run_pk", lambda cards, sc, runner: (pk_calls.append(1), {"status": "ok"})[1])
    monkeypatch.setattr(bb.pk, "build_llm_runner", lambda: object())

    return {"saves": saves, "pushes": pushes, "pk_calls": pk_calls, "scan_calls": scan_calls}


def test_dry_run_does_not_save_or_push(monkeypatch, capsys):
    w = _wire(monkeypatch)
    bb._run_daily({}, _daily_args(dry_run=True))
    out = capsys.readouterr().out
    assert "断板反包观察清单" in out
    assert w["saves"] == []
    assert w["pushes"] == []


def test_no_push_saves_without_pushing(monkeypatch, capsys):
    w = _wire(monkeypatch)
    bb._run_daily({}, _daily_args(no_push=True))
    assert len(w["saves"]) == 1
    assert w["pushes"] == []


def test_default_saves_and_pushes(monkeypatch, capsys):
    w = _wire(monkeypatch)
    bb._run_daily({}, _daily_args())
    assert len(w["saves"]) == 1
    assert len(w["pushes"]) == 1
    assert w["pushes"][0][0] == "断板反包观察清单 · 2026-07-04"


def test_source_failed_does_not_save_or_push(monkeypatch, capsys):
    failed = {"status": "source_failed", "date": "2026-07-04",
              "failed_sources": {"today_limit_up": "接口超时"}}
    w = _wire(monkeypatch, result=failed)
    bb._run_daily({}, _daily_args())
    out = capsys.readouterr().out
    assert "数据失败" in out
    assert w["saves"] == []
    assert w["pushes"] == []


def test_no_llm_skips_pk_run(monkeypatch):
    w = _wire(monkeypatch)
    bb._run_daily({}, _daily_args(no_llm=True))
    assert w["pk_calls"] == []
    # 仍照常落盘+推送，只是跳过 PK 层
    assert len(w["saves"]) == 1
    assert len(w["pushes"]) == 1


def test_non_trading_day_returns_early(monkeypatch, capsys):
    w = _wire(monkeypatch, non_trading=True)
    bb._run_daily({}, _daily_args())
    assert w["scan_calls"] == []          # scanner.run_daily 未被调用
    assert w["saves"] == []
    assert w["pushes"] == []
