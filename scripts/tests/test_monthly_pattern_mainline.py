from services.monthly_pattern.mainline import stable_main_sectors


def _record(date: str, sectors: list[str]) -> dict:
    return {
        "date": date,
        "sector_summary": [{"industry": name} for name in sectors],
    }


def test_stable_main_sectors_requires_two_hits_when_history_is_available() -> None:
    records = [
        _record("2026-06-26", ["半导体", "银行"]),
        _record("2026-06-27", ["半导体", "消费电子"]),
        _record("2026-06-30", ["半导体", "银行"]),
    ]

    sectors, meta = stable_main_sectors(records, top_k=2)

    assert sectors == ["半导体", "银行"]
    assert meta["required_hits"] == 2
    assert meta["snapshot_count"] == 3


def test_single_snapshot_is_labeled_low_depth_and_requires_one_hit() -> None:
    sectors, meta = stable_main_sectors(
        [_record("2026-06-30", ["半导体"])],
        top_k=8,
    )

    assert sectors == ["半导体"]
    assert meta["required_hits"] == 1
    assert meta["status"] == "limited_history"


def test_empty_or_unclassified_snapshots_do_not_create_a_false_mainline() -> None:
    sectors, meta = stable_main_sectors(
        [_record("2026-06-30", ["UNCLASSIFIED", ""])],
        top_k=8,
    )

    assert sectors == []
    assert meta["status"] == "missing"
