"""长期宽基指数分时成交额同比规则；独立于价格阈值。"""
from dataclasses import dataclass

INDEX_NAMES = {"000001.SH":"上证指数", "399001.SZ":"深证成指",
               "399006.SZ":"创业板指", "000688.SH":"科创50"}


@dataclass(frozen=True)
class IndexVolumeRule:
    rule_id: str
    growth_pct: int = 50
    periods: tuple[int,...] = (15,30)
    codes: tuple[str,...] = tuple(INDEX_NAMES)

    def __post_init__(self):
        if (not isinstance(self.rule_id,str) or not self.rule_id.strip()
                or type(self.growth_pct) is not int or not 0 < self.growth_pct <= 1000
                or not isinstance(self.periods,tuple) or not self.periods
                or any(type(p) is not int or p not in (15,30) for p in self.periods)
                or len(set(self.periods)) != len(self.periods)
                or not isinstance(self.codes,tuple) or not self.codes
                or any(c not in INDEX_NAMES for c in self.codes)
                or len(set(self.codes)) != len(self.codes)):
            raise ValueError("指数成交额规则配置非法")


INDEX_VOLUME_GROWTH = IndexVolumeRule(rule_id="index-volume-growth-50pct")
DEFAULT_INDEX_VOLUME_RULES = (INDEX_VOLUME_GROWTH,)
