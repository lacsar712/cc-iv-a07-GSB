"""铭牌功率折算与对表。

开路电压 × 短路电流先折成估算功率，取整成当量（W）后再跟对照表查标准填充因子。
左侧可交电压电流折算当量，也可直接手填当量；填充因子可直填（第二条路）。
电压电流与直填填充因子两条路都给时必须对得上，对不上整笔退、什么都不写。
"""
from dataclasses import dataclass

ROUND_TOLERANCE = 0.02  # 直填 FF 与表内标准 FF 的允许误差


@dataclass
class Resolved:
    est_power_w: float | None
    power_equiv_w: int
    ff_from_table: float
    ff_direct: float | None
    ff_used: float
    input_mode: str  # vi：电压电流折算；equiv：手填当量；vi+ff / equiv+ff：两条路都交
    match: str       # table_only：只走对表；exact：两路一致；mismatch：对不上


class LookupError(Exception):
    """对不上或信息不足，整笔退回。"""


def estimate_power(voc: float, isc: float) -> float:
    return round(voc * isc, 2)


def to_equivalent(est_power_w: float) -> int:
    return int(round(est_power_w))


def resolve(voc, isc, equiv_in, ff_direct, table_ff, table_equiv):
    """根据两条入口折算并核对。table_ff/table_equiv 为命中行，未命中由调用方拒绝。"""
    has_vi = voc is not None and isc is not None
    has_equiv = equiv_in is not None
    has_ff = ff_direct is not None

    if not has_vi and not has_equiv:
        raise LookupError("要么交开路电压与短路电流折算，要么左填铭牌当量")

    est = estimate_power(voc, isc) if has_vi else None
    if has_vi:
        equiv = to_equivalent(est)
        if has_equiv and equiv_in != equiv:
            raise LookupError(
                f"手填当量 {equiv_in}W 与电压电流折算当量 {equiv}W 对不上，整笔退回"
            )
    else:
        equiv = equiv_in

    if equiv != table_equiv:
        raise LookupError(f"当量 {equiv}W 与对照表行对不上，整笔退回")

    if has_vi:
        mode = "vi+ff" if has_ff else "vi"
    else:
        mode = "equiv+ff" if has_ff else "equiv"

    if has_ff:
        if round(abs(ff_direct - table_ff), 6) <= ROUND_TOLERANCE:
            match = "exact"
        else:
            raise LookupError(
                f"直填填充因子 {ff_direct} 与对照表 {equiv}W 档 {table_ff} 对不上，整笔退回"
            )
        ff_used = ff_direct
    else:
        match = "table_only"
        ff_used = table_ff

    return Resolved(est, equiv, table_ff, ff_direct, ff_used, mode, match)
