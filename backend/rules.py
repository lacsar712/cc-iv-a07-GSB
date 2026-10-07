FF_MIN = 0.72
# 两条路（电压电流推算 vs 直填）允许的填充因子最大偏差，超出即整笔退
FF_TOLERANCE = 0.05


def estimate_power(voc_v: float, isc_a: float) -> float:
    """开路电压乘短路电流折成估算功率。"""
    return voc_v * isc_a


def derive_ff(est_power_w: float, rating_w: float) -> float:
    """估算功率对照铭牌当量推算填充因子。"""
    if rating_w <= 0:
        raise ValueError("铭牌当量必须为正数")
    return est_power_w / rating_w


def paths_consistent(ff_submitted: float, ff_derived: float) -> bool:
    """直填的填充因子与推算值必须对得上。"""
    return abs(ff_submitted - ff_derived) <= FF_TOLERANCE


def judge(fill_factor: float) -> tuple[str, str]:
    if fill_factor >= FF_MIN:
        return "合格", f"填充因子 {fill_factor} 不低于 {FF_MIN}"
    return "衰减", f"填充因子 {fill_factor} 低于 {FF_MIN}"
