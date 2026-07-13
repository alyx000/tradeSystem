"""股票中心同花顺概念归属 Provider 契约测试（全 mock，无真实网络）。"""
from __future__ import annotations

import math

import pandas as pd
import pytest

from providers.akshare_provider import AkshareProvider
from providers.registry import ProviderRegistry
from providers.sina_provider import SinaProvider
from providers.tdx_provider import TdxProvider
from providers.tushare_provider import TushareProvider


class _ConceptPro:
    def __init__(
        self,
        *,
        catalog=None,
        member_frames: dict[str, pd.DataFrame | None] | None = None,
        member_errors: dict[str, Exception] | None = None,
        catalog_error: Exception | None = None,
    ):
        self.catalog = catalog
        self.member_frames = member_frames or {}
        self.member_errors = member_errors or {}
        self.catalog_error = catalog_error
        self.ths_index_calls: list[dict] = []
        self.ths_member_calls: list[dict] = []

    def ths_index(self, **kwargs):
        self.ths_index_calls.append(kwargs)
        if self.catalog_error is not None:
            raise self.catalog_error
        return self.catalog

    def ths_member(self, **kwargs):
        self.ths_member_calls.append(kwargs)
        code = kwargs["con_code"]
        if code in self.member_errors:
            raise self.member_errors[code]
        return self.member_frames.get(code, pd.DataFrame())


class _CatalogWithBrokenRecords:
    empty = False

    def to_dict(self, **_kwargs):
        raise RuntimeError("catalog records failed")


class _SecretStr:
    def __str__(self):
        raise RuntimeError("secret-member-payload")


def _catalog(*rows: dict) -> pd.DataFrame:
    if rows:
        return pd.DataFrame(list(rows))
    return pd.DataFrame(
        [
            {
                "ts_code": "885372.TI",
                "name": "页岩气",
                "type": "N",
                "count": 40,
            }
        ]
    )


def _members(*concept_codes: str) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"ts_code": code, "con_code": "605090.SH", "is_new": "Y"}
            for code in concept_codes
        ]
    )


def _provider(
    pro: _ConceptPro | None,
    *,
    initialized: bool = True,
) -> TushareProvider:
    provider = TushareProvider({})
    provider.pro = pro
    provider._initialized = initialized
    return provider


def test_stock_concept_memberships_queries_one_catalog_and_each_stock_once():
    pro = _ConceptPro(
        catalog=_catalog(),
        member_frames={
            "605090.SH": _members("885372.TI"),
            "600428.SH": _members("885372.TI"),
        },
    )
    provider = _provider(pro)

    result = provider.get_stock_concept_memberships(
        [" 605090 ", "600428.sh", "605090.SH", None, ""]
    )

    assert result.success
    assert list(result.data["stocks"]) == ["605090.SH", "600428.SH"]
    assert pro.ths_index_calls == [{"type": "N"}]
    assert [call["con_code"] for call in pro.ths_member_calls] == [
        "605090.SH",
        "600428.SH",
    ]
    assert all(
        "is_new" in call["fields"].split(",") for call in pro.ths_member_calls
    )


@pytest.mark.parametrize(
    "invalid_input",
    [
        pytest.param("not-a-stock-code", id="text"),
        pytest.param("60509", id="five-digits"),
        pytest.param("6050900", id="seven-digits"),
        pytest.param("605090.SZ", id="wrong-sh-suffix"),
        pytest.param("000001.SH", id="wrong-sz-suffix"),
        pytest.param("430001.SH", id="wrong-bj-suffix"),
        pytest.param("605090.XX", id="unknown-suffix"),
        pytest.param(_SecretStr(), id="str-raises"),
    ],
)
def test_stock_concept_memberships_rejects_noncanonical_input_without_query(
    invalid_input,
):
    pro = _ConceptPro(catalog=_catalog())

    result = _provider(pro).get_stock_concept_memberships([invalid_input])

    assert result.success
    assert result.data == {"stocks": {}}
    assert pro.ths_index_calls == []
    assert pro.ths_member_calls == []
    assert "secret-member-payload" not in repr(result.data)


def test_stock_concept_memberships_keeps_only_current_member_rows():
    pro = _ConceptPro(
        catalog=_catalog(
            {
                "ts_code": "885372.TI",
                "name": "页岩气",
                "type": "N",
                "count": 40,
            },
            {
                "ts_code": "885373.TI",
                "name": "旧概念",
                "type": "N",
                "count": 20,
            },
        ),
        member_frames={
            "605090.SH": pd.DataFrame(
                [
                    {
                        "ts_code": "885372.TI",
                        "con_code": "605090.SH",
                        "is_new": " y ",
                    },
                    {
                        "ts_code": "885373.TI",
                        "con_code": "605090.SH",
                        "is_new": "N",
                    },
                ]
            )
        },
    )

    result = _provider(pro).get_stock_concept_memberships(["605090.SH"])

    assert result.success
    assert result.data["stocks"]["605090.SH"] == {
        "status": "ok",
        "concepts": [
            {
                "concept_code": "885372.TI",
                "name": "页岩气",
                "member_count": 40,
            }
        ],
    }


@pytest.mark.parametrize(
    "bad_con_code",
    [
        pytest.param("600428.SH", id="other-stock"),
        pytest.param(None, id="none"),
        pytest.param("", id="empty"),
        pytest.param("not-a-stock-code", id="invalid"),
    ],
)
def test_stock_concept_memberships_rejects_member_row_with_invalid_con_code(
    bad_con_code,
):
    pro = _ConceptPro(
        catalog=_catalog(),
        member_frames={
            "605090.SH": pd.DataFrame(
                [
                    {
                        "ts_code": "885372.TI",
                        "con_code": bad_con_code,
                        "is_new": "Y",
                    }
                ]
            )
        },
    )

    result = _provider(pro).get_stock_concept_memberships(["605090.SH"])

    assert result.success
    assert result.data["stocks"]["605090.SH"] == {
        "status": "source_failed",
        "concepts": [],
        "error": "member row contract violation",
    }


def test_stock_concept_memberships_rejects_member_row_without_con_code():
    pro = _ConceptPro(
        catalog=_catalog(),
        member_frames={
            "605090.SH": pd.DataFrame(
                [{"ts_code": "885372.TI", "is_new": "Y"}]
            )
        },
    )

    result = _provider(pro).get_stock_concept_memberships(["605090.SH"])

    assert result.success
    assert result.data["stocks"]["605090.SH"] == {
        "status": "source_failed",
        "concepts": [],
        "error": "member row contract violation",
    }


def test_stock_concept_memberships_rejects_entire_stock_when_any_row_has_wrong_con_code():
    pro = _ConceptPro(
        catalog=_catalog(),
        member_frames={
            "605090.SH": pd.DataFrame(
                [
                    {
                        "ts_code": "885372.TI",
                        "con_code": "605090.SH",
                        "is_new": "Y",
                    },
                    {
                        "ts_code": "885373.TI",
                        "con_code": "600428.SH",
                        "is_new": "Y",
                    },
                ]
            )
        },
    )

    result = _provider(pro).get_stock_concept_memberships(["605090.SH"])

    assert result.success
    assert result.data["stocks"]["605090.SH"] == {
        "status": "source_failed",
        "concepts": [],
        "error": "member row contract violation",
    }


def test_stock_concept_memberships_validates_con_code_before_is_new_filtering():
    pro = _ConceptPro(
        catalog=_catalog(),
        member_frames={
            "605090.SH": pd.DataFrame(
                [
                    {
                        "ts_code": "885372.TI",
                        "con_code": "600428.SH",
                        "is_new": "N",
                    }
                ]
            )
        },
    )

    result = _provider(pro).get_stock_concept_memberships(["605090.SH"])

    assert result.success
    assert result.data["stocks"]["605090.SH"] == {
        "status": "source_failed",
        "concepts": [],
        "error": "member row contract violation",
    }


def test_stock_concept_memberships_accepts_normalized_bare_member_con_code():
    pro = _ConceptPro(
        catalog=_catalog(),
        member_frames={
            "605090.SH": pd.DataFrame(
                [
                    {
                        "ts_code": "885372.TI",
                        "con_code": " 605090 ",
                        "is_new": " y ",
                    }
                ]
            )
        },
    )

    result = _provider(pro).get_stock_concept_memberships(["605090.sh"])

    assert result.success
    assert result.data["stocks"]["605090.SH"] == {
        "status": "ok",
        "concepts": [
            {
                "concept_code": "885372.TI",
                "name": "页岩气",
                "member_count": 40,
            }
        ],
    }


def test_stock_concept_memberships_contains_bad_con_code_scalar_to_one_stock():
    pro = _ConceptPro(
        catalog=_catalog(),
        member_frames={
            "605090.SH": pd.DataFrame(
                [
                    {
                        "ts_code": "885372.TI",
                        "con_code": _SecretStr(),
                        "is_new": "Y",
                    }
                ]
            ),
            "600428.SH": pd.DataFrame(
                [
                    {
                        "ts_code": "885372.TI",
                        "con_code": " 600428.sh ",
                        "is_new": "Y",
                    }
                ]
            ),
        },
    )

    result = _provider(pro).get_stock_concept_memberships(
        ["605090.SH", "600428.SH"]
    )

    assert result.success
    assert result.data["stocks"]["605090.SH"] == {
        "status": "source_failed",
        "concepts": [],
        "error": "member row contract violation",
    }
    assert result.data["stocks"]["600428.SH"]["status"] == "ok"
    assert "secret-member-payload" not in repr(result.data)


@pytest.mark.parametrize("dirty_field", ["is_new", "ts_code"])
def test_stock_concept_memberships_contains_bad_member_scalar_to_one_stock(
    dirty_field,
):
    dirty_row = {
        "ts_code": "885372.TI",
        "con_code": "605090.SH",
        "is_new": "Y",
    }
    dirty_row[dirty_field] = _SecretStr()
    pro = _ConceptPro(
        catalog=_catalog(),
        member_frames={
            "605090.SH": pd.DataFrame([dirty_row]),
            "600428.SH": pd.DataFrame(
                [
                    {
                        "ts_code": "885372.TI",
                        "con_code": "600428.SH",
                        "is_new": "Y",
                    }
                ]
            ),
        },
    )

    result = _provider(pro).get_stock_concept_memberships(
        ["605090.SH", "600428.SH"]
    )

    assert result.success
    assert result.data["stocks"]["605090.SH"] == {
        "status": "source_failed",
        "concepts": [],
        "error": "member row contract violation",
    }
    assert result.data["stocks"]["600428.SH"]["status"] == "ok"
    assert "secret-member-payload" not in repr(result.data)


@pytest.mark.parametrize(
    "member_row",
    [
        {"ts_code": "885372.TI", "con_code": "605090.SH"},
        {"ts_code": "885372.TI", "con_code": "605090.SH", "is_new": None},
        {"ts_code": "885372.TI", "con_code": "605090.SH", "is_new": ""},
        {"ts_code": "885372.TI", "con_code": "605090.SH", "is_new": "invalid"},
    ],
    ids=["missing", "none", "empty", "invalid"],
)
def test_stock_concept_memberships_rejects_unknown_current_state(member_row):
    pro = _ConceptPro(
        catalog=_catalog(),
        member_frames={"605090.SH": pd.DataFrame([member_row])},
    )

    result = _provider(pro).get_stock_concept_memberships(["605090.SH"])

    assert result.success
    assert result.data["stocks"]["605090.SH"] == {
        "status": "missing",
        "concepts": [],
    }


def test_stock_concept_memberships_filters_non_concept_rows_and_maps_count():
    pro = _ConceptPro(
        catalog=_catalog(
            {
                "ts_code": "885372.TI",
                "name": " 页岩气 ",
                "type": "N",
                "count": 40,
            },
            {
                "ts_code": "885900.TI",
                "name": "申万行业标签",
                "type": "I",
                "count": 20,
            },
        ),
        member_frames={
            "605090.SH": _members(
                "885372.TI",
                "885900.TI",
                "999999.TI",
                "885372.TI",
            )
        },
    )

    result = _provider(pro).get_stock_concept_memberships(["605090.SH"])

    assert result.success
    assert result.data["stocks"]["605090.SH"] == {
        "status": "ok",
        "concepts": [
            {
                "concept_code": "885372.TI",
                "name": "页岩气",
                "member_count": 40,
            }
        ],
    }


def test_stock_concept_memberships_keeps_zero_and_wide_counts_for_context_filtering():
    raw_counts = [
        None,
        "",
        float("nan"),
        float("inf"),
        -1,
        0,
        True,
        1.5,
        40,
        301,
    ]
    rows = [
        {
            "ts_code": f"885{i:03d}.TI",
            "name": f"概念{i}",
            "type": "N",
            "count": count,
        }
        for i, count in enumerate(raw_counts, start=1)
    ]
    codes = [row["ts_code"] for row in rows]
    pro = _ConceptPro(
        catalog=_catalog(*rows),
        member_frames={"605090.SH": _members(*codes)},
    )

    result = _provider(pro).get_stock_concept_memberships(["605090.SH"])

    assert result.success
    concepts = result.data["stocks"]["605090.SH"]["concepts"]
    assert [item["member_count"] for item in concepts] == [
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        40,
        301,
    ]
    assert all(math.isfinite(item["member_count"]) for item in concepts)
    assert all("nan" not in str(value).lower() for item in concepts for value in item.values())


def test_stock_concept_memberships_isolates_single_stock_failure_and_cleans_error():
    pro = _ConceptPro(
        catalog=_catalog(),
        member_frames={"605090.SH": _members("885372.TI")},
        member_errors={"600428.SH": RuntimeError("  member request failed  ")},
    )

    result = _provider(pro).get_stock_concept_memberships(
        ["605090.SH", "600428.SH"]
    )

    assert result.success
    assert result.data["stocks"]["605090.SH"]["status"] == "ok"
    assert result.data["stocks"]["600428.SH"] == {
        "status": "source_failed",
        "concepts": [],
        "error": "member request failed",
    }


def test_stock_concept_memberships_none_member_response_is_source_failed():
    pro = _ConceptPro(
        catalog=_catalog(),
        member_frames={"605090.SH": None},
    )

    result = _provider(pro).get_stock_concept_memberships(["605090.SH"])

    assert result.success
    assert result.data["stocks"]["605090.SH"] == {
        "status": "source_failed",
        "concepts": [],
        "error": "member response is None",
    }


def test_stock_concept_memberships_empty_member_frame_is_missing():
    pro = _ConceptPro(
        catalog=_catalog(),
        member_frames={"605090.SH": pd.DataFrame()},
    )

    result = _provider(pro).get_stock_concept_memberships(["605090.SH"])

    assert result.success
    assert result.data["stocks"]["605090.SH"] == {
        "status": "missing",
        "concepts": [],
    }


def test_stock_concept_memberships_empty_input_does_not_require_initialization():
    provider = _provider(None, initialized=False)

    result = provider.get_stock_concept_memberships([None, "", "   "])

    assert result.success
    assert result.source == "tushare:ths_member:by_stock"
    assert result.data == {"stocks": {}}


def test_stock_concept_memberships_uninitialized_provider_is_top_level_failure():
    pro = _ConceptPro(catalog=_catalog())
    provider = _provider(pro, initialized=False)

    result = provider.get_stock_concept_memberships(["605090.SH"])

    assert not result.success
    assert result.data is None
    assert result.error == "provider_not_initialized: get_stock_concept_memberships"
    assert pro.ths_index_calls == []
    assert pro.ths_member_calls == []


def test_stock_concept_memberships_dirty_catalog_is_top_level_failure():
    pro = _ConceptPro(
        catalog=_catalog(
            {"ts_code": None, "name": "页岩气", "type": "N", "count": 40},
            {"ts_code": float("nan"), "name": "nan", "type": "N", "count": 40},
            {"ts_code": "885372.TI", "name": float("nan"), "type": "N", "count": 40},
            {"ts_code": "885900.TI", "name": "行业标签", "type": "I", "count": 20},
        )
    )

    result = _provider(pro).get_stock_concept_memberships(["605090.SH"])

    assert not result.success
    assert result.data is None
    assert result.error == "同花顺概念目录清洗后为空"
    assert pro.ths_member_calls == []


@pytest.mark.parametrize(
    "catalog_row",
    [
        {"ts_code": "885372.TI", "name": "页岩气", "type": None, "count": 40},
        {"ts_code": "885372.TI", "name": "页岩气", "type": "", "count": 40},
        {"ts_code": "885372.TI", "name": "页岩气", "count": 40},
    ],
    ids=["none-type", "empty-type", "missing-type"],
)
def test_stock_concept_memberships_requires_explicit_type_n(catalog_row):
    pro = _ConceptPro(catalog=_catalog(catalog_row))

    result = _provider(pro).get_stock_concept_memberships(["605090.SH"])

    assert not result.success
    assert result.data is None
    assert result.error == "同花顺概念目录清洗后为空"
    assert pro.ths_member_calls == []


def test_stock_concept_memberships_none_or_empty_catalog_is_top_level_failure():
    for catalog in (None, pd.DataFrame()):
        pro = _ConceptPro(catalog=catalog)

        result = _provider(pro).get_stock_concept_memberships(["605090.SH"])

        assert not result.success
        assert result.data is None
        assert result.error == "同花顺概念目录为空"
        assert pro.ths_member_calls == []


def test_stock_concept_memberships_catalog_exception_is_top_level_failure():
    pro = _ConceptPro(
        catalog=_catalog(),
        catalog_error=RuntimeError("  catalog request failed  "),
    )

    result = _provider(pro).get_stock_concept_memberships(["605090.SH"])

    assert not result.success
    assert result.data is None
    assert result.error == "catalog request failed"
    assert pro.ths_member_calls == []


@pytest.mark.parametrize(
    ("catalog", "error_fragment"),
    [
        (object(), "empty"),
        (_CatalogWithBrokenRecords(), "catalog records failed"),
    ],
    ids=["missing-empty", "record-conversion-failed"],
)
def test_stock_concept_memberships_malformed_catalog_is_top_level_failure(
    catalog,
    error_fragment,
):
    pro = _ConceptPro(catalog=catalog)

    result = _provider(pro).get_stock_concept_memberships(["605090.SH"])

    assert not result.success
    assert result.data is None
    assert result.source == "tushare"
    assert error_fragment in result.error
    assert pro.ths_member_calls == []


def test_capability_is_registered_only_by_tushare():
    capability = "get_stock_concept_memberships"

    assert capability in TushareProvider({}).get_capabilities()
    assert capability not in AkshareProvider({}).get_capabilities()
    assert capability not in SinaProvider({}).get_capabilities()
    assert capability not in TdxProvider({}).get_capabilities()


def test_registry_without_available_provider_returns_top_level_failure():
    registry = ProviderRegistry()
    registry.register(_provider(None, initialized=False))

    result = registry.call("get_stock_concept_memberships", ["605090.SH"])

    assert not result.success
    assert result.data is None
    assert result.source == "registry"
    assert "tushare" in result.error
    assert "provider_not_initialized: get_stock_concept_memberships" in result.error
