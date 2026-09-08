"""价目表：按「基线 + 每特性(固定项, 每单位斜率)」累加。

不按 2ⁿ 组合存表，因为实测证明可以线性叠加：gpio 三特性八组合 × 两位宽，
叠加预测的偏差恒在 −0.6% ~ −6.9%，**符号一律为负**——特性之间共享的逻辑被
优化器合并掉了。所以预测恒为高估，预算工具只会偏保守，不会承诺不了。
"""
from __future__ import annotations

from xirang_core.manifest import Bad, Pkg
from xirang_core.model import Instance, Resolved


def _term(spec: dict, vals) -> float:
    """一条 {fixed, per, k} 折算成面积。per 指向某个数值旋钮。"""
    if not spec:
        return 0.0
    a = float(spec.get("fixed", 0.0))
    per, k = spec.get("per"), float(spec.get("k", 0.0))
    if per:
        if per not in vals:
            raise Bad(f"价目表引用了不存在的旋钮 {per}")
        a += k * float(vals[per].value)
    return a


def price(pkg: Pkg, vals) -> tuple[float, dict[str, float]]:
    """返回 (总面积, 每个旋钮各摊多少)。后者是面板第五问的答案。"""
    area = pkg.ip.get("area") or {}
    per_knob: dict[str, float] = {}
    total = _term(area.get("base"), vals)

    for fn, f in (pkg.ip.get("features") or {}).items():
        v = vals[fn].value
        spec = f.get("area")
        if not spec:
            continue
        if f.get("type", "bool") == "choice":
            # choice 的价目按档位取
            cost = _term(spec.get(v), vals) if isinstance(spec, dict) else 0.0
        else:
            cost = _term(spec, vals) if v else 0.0
        per_knob[fn] = cost
        total += cost

    return total, per_knob


def annotate(res: Resolved, pkgs: dict[str, Pkg]) -> Resolved:
    """把面积填进每个实例，并逐层累加。"""
    def rec(insts: list[Instance]) -> float:
        s = 0.0
        for i in insts:
            own, per_knob = price(pkgs[i.of], i.values)
            for k, c in per_knob.items():
                i.values[k].area_um2 = c
            i.area_um2 = own + rec(i.children)
            s += i.area_um2
        return s

    res.area_um2 = rec(res.instances)
    return res


def model_note(pkg: Pkg) -> str:
    a = pkg.ip.get("area") or {}
    e = a.get("error") or {}
    c = a.get("corner") or {}
    if not a:
        return "无价目表"
    return (f"{a.get('model', '?')}，误差界 {e.get('bound', '?')}"
            f"（{'恒为高估' if e.get('sign') == 'over' else e.get('sign', '?')}），"
            f"口径 {c.get('tool', '?')}/{c.get('pdk', '?')}@{c.get('freq_mhz', '?')}MHz"
            f" 测于 {c.get('measured', '?')}")
