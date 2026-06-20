"""个股 → 同花顺概念 反查 + 容器概念过滤（trend_leader / volume_concentration 共用）。

从 trend_leader/scanner 下沉为共享 util,避免 volume_concentration → trend_leader 的反向依赖。
反查走 tushare get_ths_member（逐概念拉成员),返回「裸码 → 概念名集合」+「概念名 → 去重成员数」。
"""
from __future__ import annotations

# 成员 > 此值 = 容器概念(融资融券/深股通/华为概念 等资格类标签,覆盖几千只),剔除;
# 真正的窄题材分支(CPO/MLCC/大硅片)仅几十到一两百只。0522 真实数据验证 cap=300 干净分离。
CONTAINER_MAX_MEMBERS = 300


def _clean_code(raw) -> str | None:
    """规范个股代码为非空字符串；None/空/nan/非字符串(如 int 600552) 统一处理 → None 或规范串。
    防 provider/schema drift 用非字符串 code 击穿 .split() 行级防御。"""
    if raw is None:
        return None
    s = str(raw).strip()
    return s if s and s.lower() != "nan" else None


def build_stock_concept_map(registry, date: str) -> tuple[dict, dict, bool]:
    """个股 → 同花顺概念 反向映射 {裸码: set(概念名)} + 概念成员数 {概念名: 去重个股数}。

    成员数供容器概念过滤。返回 (map, member_count, ok)；失败 → ({}, {}, False) 由上层记 source_errors。
    含美股/全球成员（con_code 如 CAT.N），裸码归一后 A 股候选查不到则不命中,无害。
    """
    r = registry.call("get_ths_member", date)
    if not (getattr(r, "success", False) and isinstance(r.data, list)):
        return {}, {}, False
    out: dict[str, set] = {}
    member_count: dict[str, int] = {}                   # 概念 → 去重个股数（每只票每概念计一次）
    for row in r.data:
        if not isinstance(row, dict):
            continue
        code = _clean_code(row.get("con_code"))
        concept = row.get("index_name")
        if code and concept:
            concept = str(concept)
            s = out.setdefault(code.split(".")[0], set())  # 键=裸码,与候选查询口径一致
            if concept not in s:                        # 同一票同概念去重后才计数,与 set 语义一致
                s.add(concept)
                member_count[concept] = member_count.get(concept, 0) + 1
    return out, member_count, True


def stock_real_concepts(bare_code: str, concept_map: dict, member_count: dict,
                        max_members: int = CONTAINER_MAX_MEMBERS) -> list[str]:
    """某票的「非容器」概念名 list(按名升序,确定性);剔成员 > max_members 的容器概念。"""
    concepts = concept_map.get(bare_code) or set()
    return sorted(c for c in concepts if 0 < member_count.get(c, 0) <= max_members)


def hot_concepts(registry, date: str, top_m: int, member_count: dict,
                 max_members: int | None = None) -> tuple[set, bool, bool]:
    """资金净流入 Top-M 热概念(鞠磊「主线或其分支」)。返回 (热概念名集合, ok, coverage_ok)。

    先按成员数闸过滤「容器概念」(融资融券/深股通/华为概念 等覆盖几千只的资格类标签,聚合净流入
    天然霸榜但非窄分支;0522 实测 cap=300 干净分离)再按净流入降序填满 top_m。取数失败 → (set(), False, True)。
    coverage_ok=False:入选窗口内出现 member_count==0(疑似 ths_member 静默截断,上层据此记警示)。
    name 与 member_count 跨 concept_moneyflow_ths.name / ths_member.index_name 字符串相等匹配
    (同源 THS 概念母表,0522 实测逐字命中,命名分歧漏网面=0)。
    max_members=None 时取 CONTAINER_MAX_MEMBERS(哨兵在调用时取值,避免默认参数 import 期绑定致 monkeypatch 失效)。
    """
    cap = CONTAINER_MAX_MEMBERS if max_members is None else max_members
    r = registry.call("get_concept_moneyflow_ths", date)
    if not (getattr(r, "success", False) and isinstance(r.data, list)):
        return set(), False, True
    parsed = []
    for row in r.data:
        if not isinstance(row, dict):
            continue
        name = row.get("name")
        if not name:
            continue
        try:                                            # 身份过后再解析数值(提前短路垃圾行)
            amt = float(row.get("net_amount"))
        except (TypeError, ValueError):
            continue
        parsed.append((str(name), amt))
    parsed.sort(key=lambda x: x[1], reverse=True)
    kept: list[str] = []
    coverage_ok = True
    for name, _ in parsed:                              # 净流入降序逐个判,填满 top_m 即停
        mc = member_count.get(name, 0)
        if mc > cap:                                    # 容器概念,正常剔除(非覆盖缺失)
            continue
        if mc == 0:                                     # 排在入选窗口内却无成员 → 疑似部分覆盖缺失
            coverage_ok = False
            continue
        kept.append(name)
        if len(kept) >= top_m:
            break
    return set(kept), True, coverage_ok
