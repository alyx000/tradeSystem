from __future__ import annotations

from db import queries as Q
from db.connection import get_connection
from db.migrate import migrate


def _exec_row(**overrides):
    base = dict(
        account_id="default",
        biz_date="2026-06-18",
        exec_time="10:00:00",
        stock_code_raw="601696",
        stock_code="601696",
        stock_name="中银证券",
        market="SH",
        market_raw="上海Ａ股",
        direction="buy",
        direction_raw="买入",
        shares=1500,
        price=13.03,
        amount=19545.0,
        net_amount=-19550.2,
        balance_after=1500,
        commission=3.95,
        stamp_duty=0.0,
        transfer_fee=0.2,
        exchange_fee=0.66,
        regulatory_fee=0.39,
        other_fees=0.0,
        total_fees=5.2,
        broker_contract_no="12046",
        broker_trade_no="24862783",
        currency="CNY",
        raw_payload_json="{}",
        source_file="test.tsv",
        source_format="tsv-gbk",
        input_by="broker_export",
        import_run_id="run_before_thesis",
    )
    base.update(overrides)
    return base


def _make_thesis(**overrides):
    from services.trade_thesis.models import Thesis

    base = dict(
        stock_code="601696",
        stock_name="中银证券",
        account_id="default",
        opened_at="2026-06-18",
        entry_reason="券商趋势低吸",
        failure_condition="5日均线",
        trade_mode="dip",
        market_region="a-share",
        sector="券商",
        planned_position_pct=0.2,
        input_by="cursor",
    )
    base.update(overrides)
    return Thesis(**base)


def test_repair_reconcile_links_existing_executions_and_syncs_holdings(tmp_path):
    from services.broker_executions.repair import repair_reconcile
    from services.trade_thesis import repository

    conn = get_connection(tmp_path / "trade.db")
    migrate(conn)
    try:
        Q.insert_broker_execution(conn, **_exec_row())
        Q.insert_broker_execution(
            conn,
            **_exec_row(
                biz_date="2026-06-16",
                exec_time="11:27:28",
                stock_code_raw="300209",
                stock_code="300209",
                stock_name="行云科技",
                shares=600,
                price=26.03,
                amount=15618.0,
                net_amount=-15623.0,
                balance_after=600,
                broker_contract_no="19646",
                broker_trade_no="0104000053520441",
            ),
        )
        Q.insert_broker_execution(
            conn,
            **_exec_row(
                biz_date="2026-06-17",
                exec_time="09:37:57",
                stock_code_raw="300209",
                stock_code="300209",
                stock_name="行云科技",
                direction="sell",
                direction_raw="卖出",
                shares=600,
                price=26.46,
                amount=15876.0,
                net_amount=15863.06,
                balance_after=0,
                broker_contract_no="5652",
                broker_trade_no="0105000010087030",
            ),
        )
        conn.commit()

        zhongyin_id = repository.create(conn, _make_thesis())
        xingyun_id = repository.create(
            conn,
            _make_thesis(
                stock_code="300209",
                stock_name="行云科技",
                opened_at="2026-06-16",
                entry_reason="算力租赁事件驱动低吸",
                sector="算力租赁",
            ),
        )
        Q.upsert_holding(
            conn,
            stock_code="300209",
            stock_name="行云科技",
            shares=600,
            status="active",
            thesis_id=xingyun_id,
        )
        conn.commit()

        dry = repair_reconcile(
            conn,
            account_id="default",
            date_from="2026-06-01",
            date_to="2026-06-21",
            dry_run=True,
        )
        assert dry["dry_run"] is True
        assert dry["linked_execution_rows"] == 3
        assert conn.execute(
            "SELECT COUNT(*) FROM broker_executions WHERE thesis_id IS NOT NULL"
        ).fetchone()[0] == 0

        result = repair_reconcile(
            conn,
            account_id="default",
            date_from="2026-06-01",
            date_to="2026-06-21",
            dry_run=False,
        )

        assert result["linked_execution_rows"] == 3
        assert result["active_holdings_upserted"] == 1
        assert result["holdings_closed"] == 1
        assert result["thesis_closed"] == 1

        linked = conn.execute(
            """
            SELECT stock_code, COUNT(*) AS n, MIN(thesis_id) AS thesis_id
              FROM broker_executions
             GROUP BY stock_code
             ORDER BY stock_code
            """
        ).fetchall()
        assert [(r["stock_code"], r["n"], r["thesis_id"]) for r in linked] == [
            ("300209", 2, xingyun_id),
            ("601696", 1, zhongyin_id),
        ]

        active = Q.get_holdings(conn, status="active")
        assert [(h["stock_code"], h["shares"], h["thesis_id"]) for h in active] == [
            ("601696", 1500, zhongyin_id),
        ]
        xingyun = conn.execute(
            "SELECT status, closed_at FROM trade_thesis WHERE id = ?", (xingyun_id,)
        ).fetchone()
        assert xingyun["status"] == "closed"
        assert xingyun["closed_at"] == "2026-06-17"

        second = repair_reconcile(
            conn,
            account_id="default",
            date_from="2026-06-01",
            date_to="2026-06-21",
            dry_run=True,
        )
        assert second["linked_execution_rows"] == 0
        assert second["active_holdings_upserted"] == 0
        assert second["holdings_closed"] == 0
        assert second["thesis_closed"] == 0
    finally:
        conn.close()


def test_repair_reconcile_voids_semantic_duplicate_executions(tmp_path):
    from services.broker_executions.repair import repair_reconcile
    from services.trade_thesis import repository

    conn = get_connection(tmp_path / "trade.db")
    migrate(conn)
    try:
        thesis_id = repository.create(
            conn,
            _make_thesis(
                stock_code="588000",
                stock_name="科创50",
                opened_at="2026-06-01",
                entry_reason="ETF swing",
                sector="ETF",
            ),
        )
        # exact duplicate: keep broker-balance row, void no-balance daily row
        Q.insert_broker_execution(
            conn,
            **_exec_row(
                biz_date="2026-06-08",
                exec_time="13:45:55",
                stock_code_raw="588000",
                stock_code="588000",
                stock_name="科创50",
                shares=12200,
                price=1.666,
                amount=20325.2,
                balance_after=None,
                broker_contract_no="7017157",
                broker_trade_no="0000000060849553",
                thesis_id=thesis_id,
            ),
        )
        Q.insert_broker_execution(
            conn,
            **_exec_row(
                biz_date="2026-06-08",
                exec_time="13:45:55",
                stock_code_raw="588000",
                stock_code="588000",
                stock_name="科创50",
                shares=12200,
                price=1.666,
                amount=20325.2,
                balance_after=12200,
                broker_contract_no="26133",
                broker_trade_no="60849553",
                thesis_id=thesis_id,
            ),
        )
        # aggregate duplicate: keep balance row 5500, void component rows 200+5300
        for shares, amount, trade_no in [
            (200, 362.0, "0000000014836613"),
            (5300, 9593.0, "0000000014838682"),
        ]:
            Q.insert_broker_execution(
                conn,
                **_exec_row(
                    biz_date="2026-06-03",
                    exec_time="09:45:34",
                    stock_code_raw="588000",
                    stock_code="588000",
                    stock_name="科创50",
                    direction="sell",
                    direction_raw="卖出",
                    shares=shares,
                    price=1.81,
                    amount=amount,
                    balance_after=None,
                    broker_contract_no="7005153",
                    broker_trade_no=trade_no,
                    thesis_id=thesis_id,
                ),
            )
        Q.insert_broker_execution(
            conn,
            **_exec_row(
                biz_date="2026-06-03",
                exec_time="09:45:34",
                stock_code_raw="588000",
                stock_code="588000",
                stock_name="科创50",
                direction="sell",
                direction_raw="卖出",
                shares=5500,
                price=1.81,
                amount=9955.0,
                balance_after=0,
                broker_contract_no="7657",
                broker_trade_no="14836613",
                thesis_id=thesis_id,
            ),
        )
        conn.commit()

        result = repair_reconcile(
            conn,
            account_id="default",
            date_from="2026-06-01",
            date_to="2026-06-21",
            dry_run=False,
        )

        assert result["voided_execution_rows"] == 3
        rows = conn.execute(
            """
            SELECT shares, amount, balance_after, is_void, void_reason
              FROM broker_executions
             ORDER BY biz_date, exec_time, id
            """
        ).fetchall()
        active = [(r["shares"], r["amount"], r["balance_after"]) for r in rows if r["is_void"] == 0]
        voided = [(r["shares"], r["amount"], r["void_reason"]) for r in rows if r["is_void"] == 1]
        assert active == [
            (5500, 9955.0, 0),
            (12200, 20325.2, 12200),
        ]
        assert voided == [
            (200, 362.0, "semantic_duplicate_component"),
            (5300, 9593.0, "semantic_duplicate_component"),
            (12200, 20325.2, "semantic_duplicate_exact"),
        ]
    finally:
        conn.close()
