"""涨停来源身份：区分真实连板字段与旧provider的默认首板。"""
import math
from numbers import Real


def positive_streak(value):
    if isinstance(value,bool) or not isinstance(value,(Real,str)):
        return None
    try:
        number=float(value)
        return int(number) if math.isfinite(number) and number.is_integer() and number>0 else None
    except (ValueError,TypeError,OverflowError):
        return None


def verified_streak(row,source):
    value=positive_streak(row.get('limit_times'))
    if 'limit_times_verified' in row:
        return (value,'provider_field') if row['limit_times_verified'] is True and value else (None,'unknown')
    # 旧tushare/akshare缺字段默认1，不能把归档的1本身视为已核验。
    # >=2不可能来自该默认值；1仅在未默认生成的N天M板字段1/1交叉印证时采用。
    # 其他来源/矛盾证据保持未知；不把缺字段偷偷改成None影响既有梯队消费者。
    if source in ('tushare:limit_list_d','akshare:stock_zt_pool_em'):
        stat=str(row.get('limit_stat') or '').strip()
        if value and value>=2 and stat!='1/1':
            return value,'legacy_nondefault'
        if value==1 and stat=='1/1':
            return value,'legacy_one_of_one'
    return None,'unknown'
