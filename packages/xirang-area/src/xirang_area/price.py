"""价目表：按「基线 + 每特性(固定项, 每单位斜率)」累加。

不按 2ⁿ 组合存表，因为实测证明可以线性叠加：gpio 三特性八组合 × 两位宽，
叠加预测的偏差恒在 −0.6% ~ −6.9%，**符号一律为负**——特性之间共享的逻辑被
优化器合并掉了。所以预测恒为高估，预算工具只会偏保守，不会承诺不了。
"""
from __future__ import annotations

from xirang_core.manifest import Bad, Pkg
from xirang_core.model import Instance, Resolved


def _interp(points: dict, x: float) -> float:
    """按实测点插值。落在点之间就线性插，落在两端之外就沿最近一段外推。

    存点而不是拟直线，是因为参数曲线可能有结构断点——uart 的 FIFO 在深度 3 以上
    换了实现，一条直线怎么拟都会把中间低估。
    """
    xs = sorted(float(k) for k in points)
    ys = [float(points[k]) for k in sorted(points, key=lambda k: float(k))]
    if len(xs) == 1:
        return ys[0]
    if x <= xs[0]:
        lo, hi = 0, 1
    elif x >= xs[-1]:
        lo, hi = len(xs) - 2, len(xs) - 1
    else:
        hi = next(i for i, v in enumerate(xs) if v >= x)
        lo = hi - 1
    if xs[hi] == xs[lo]:
        return ys[lo]
    frac = (x - xs[lo]) / (xs[hi] - xs[lo])
    return ys[lo] + frac * (ys[hi] - ys[lo])


def _term(spec: dict, vals) -> float:
    """一条价目折算成面积。两种形态：

      {fixed, per, k}   仿射，用于特性叠加
      {points, per}     实测点插值，用于有结构断点的参数曲线
    """
    if not spec:
        return 0.0
    per = spec.get("per")
    if "points" in spec:
        if not per:
            raise Bad("points 形态必须给 per，指明这条曲线沿哪个旋钮")
        if per not in vals:
            raise Bad(f"价目表引用了不存在的旋钮 {per}")
        a = _interp(spec["points"], float(vals[per].value))
        # 凹曲线的弦在曲线之下，点间插值系统性偏低（uart 实测最多低 3.54%）。
        # 按实测残差加一个余量，让「恒为高估」由构造保证，而不是碰运气。
        return a * (1.0 + float(spec.get("margin", 0.0)))
    a = float(spec.get("fixed", 0.0))
    k = float(spec.get("k", 0.0))
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

    m = float((pkg.ip.get("area") or {}).get("margin", 0.0))
    if m:
        total *= (1.0 + m)
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

    root = pkgs.get(res.top)
    own = price(root, {})[0] if root and (root.ip.get("area") or {}).get("base") else 0.0
    res.area_um2 = own + rec(res.instances)
    return res


def stale(pkg: Pkg) -> str | None:
    """价目表是不是在别的生成器版本下量的。是的话，它可能已经悄悄失效了。"""
    from xirang_gen.regmap import GEN_VERSION
    c = (pkg.ip.get("area") or {}).get("corner") or {}
    got = c.get("generator")
    if got is None:
        return f"{pkg.name} 的价目表没记生成器版本，无法判断是否失效"
    if str(got) != GEN_VERSION:
        return (f"{pkg.name} 的价目表是生成器 {got} 量的，现在是 {GEN_VERSION}——"
                f"生成的逻辑变了，面积很可能已失效，重测再用")
    return None


def _shape(spec) -> str:
    return "points" if spec and "points" in spec else "affine"


def model_note(pkg: Pkg) -> str:
    a = pkg.ip.get("area") or {}
    e = a.get("error") or {}
    c = a.get("corner") or {}
    if not a:
        return "无价目表"
    gen = c.get("generator", "?")
    return (f"{a.get('model', '?')}/{_shape(a.get('base'))}，生成器 {gen}，"
            f"误差界 {e.get('bound', '?')}"
            f"（{'恒为高估' if e.get('sign') == 'over' else e.get('sign', '?')}），"
            f"口径 {c.get('tool', '?')}/{c.get('pdk', '?')}@{c.get('freq_mhz', '?')}MHz"
            f" 测于 {c.get('measured', '?')}")
