"""价目表：实测过的配置直接查表，没测过的按「基线 + 每特性」叠加。

叠加不是白来的。gpio 全网格（6 个合法组合 × 4 个位宽）实测下来，特性之间会
互相借用逻辑：irq+bidir 在 8 针处比两项相加**多** 9.57%，在 32 针处又少 4.68%，
符号两边都有。所以叠加的结果要按实测的最差欠估抬一道余量，才谈得上「恒为高估」。
落在实测网格上的配置不走这条路——直接给实测值，既精确又不必抬。

装配那一层的承诺不同，见 annotate。
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


def measured(pkg: Pkg, vals) -> float | None:
    """这套旋钮实测过吗。实测过就不必再猜，也不必抬余量。"""
    for row in (pkg.ip.get("area") or {}).get("measured", []) or []:
        at = row.get("at") or {}
        if all(k in vals and vals[k].value == v for k, v in at.items()):
            return float(row["um2"])
    return None


def price(pkg: Pkg, vals, lift: bool = True) -> tuple[float, dict[str, float]]:
    """返回 (总面积, 每个旋钮各摊多少)。后者是面板第五问的答案。

    `lift` 决定要不要抬上界。叶子独立综合时要抬——那是上界承诺；装配里不抬，
    因为装配另有自己的比例项，两者叠在一起会保守到没法用来比较配置。
    """
    area = pkg.ip.get("area") or {}
    per_knob: dict[str, float] = {}
    hit = measured(pkg, vals)
    if hit is not None:
        return hit, per_knob
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
    if lift and m:
        total *= (1.0 + m)
    return total, per_knob


def annotate(res: Resolved, pkgs: dict[str, Pkg]) -> Resolved:
    """把面积填进每个实例，并逐层累加。"""
    def rec(insts: list[Instance]) -> float:
        s = 0.0
        for i in insts:
            own, per_knob = price(pkgs[i.of], i.values, lift=False)
            for k, c in per_knob.items():
                i.values[k].area_um2 = c
            i.area_um2 = own + rec(i.children)
            s += i.area_um2
        return s

    root = pkgs.get(res.top)
    own = price(root, {})[0] if root and (root.ip.get("area") or {}).get("base") else 0.0
    # 库里的模块整颗芯片只例化一次（总线绑定器、交换网），所以按包记一次。
    own += sum(price(p, {})[0] for n, p in pkgs.items()
               if n != res.top and p.is_library
               and (p.ip.get("area") or {}).get("base"))
    # 装配不是各实例之和：综合会跨边界优化，独立综合时保住的端口在装配里被并掉，
    # 而嵌套的握手又比独立边界贵。实测这个系数在 0.94 到 1.29 之间，取中并给双侧误差。
    # 叶子的价目表仍是上界，装配这一层只是估计——两件事的承诺不同。
    fac = 1.0
    for p in pkgs.values():
        if p.is_library:
            fac = max(fac, ((p.ip.get("area") or {}).get("assembly") or {})
                      .get("factor", 1.0))
    res.area_um2 = own + rec(res.instances) * fac
    return res


def gen_digest(pkg: Pkg) -> str | None:
    """当前生成器会为这个包吐出什么——对产物取摘要。

    手工维护版本号两头不讨好：忘了升是漏报，升了没改输出是误报（0.4 只加了
    一条校验，输出与 0.3 逐字节相同，却报了过期）。对**产物**取摘要，两种错都没有。
    """
    if not pkg.regmap:
        return None
    import hashlib

    from xirang_core.resolve import resolve_pkg
    from xirang_gen.regmap import bsv
    from xirang_gen.wrap import flat_emit, wrap
    parts = [bsv(pkg)]
    # 包装层也是生成的，也进面积。只取默认配置那一份即可——
    # 生成器一变，这一份就跟着变。
    if flat_emit(pkg) is not None:
        parts.append(wrap(pkg, resolve_pkg(pkg, {}, "digest", None, {})))
    # 手写的源码也要进摘要。只看生成物的话，改了 IP 自己的逻辑（uart 的取数、
    # spi 的采样、timer 的比较）面积明明变了却没人报警——那正是这套机制
    # 当初要防的「悄悄失效」。
    src = pkg.root / "bsv"
    if src.is_dir():
        for f in sorted(list(src.glob("*.bsv")) + list(src.glob("*.bs"))):
            parts.append(f.read_text(encoding="utf-8"))
    return "sha256:" + hashlib.sha256("".join(parts).encode()).hexdigest()[:16]


def stale(pkg: Pkg) -> str | None:
    """价目表是不是对着另一份生成产物量的。是的话它已经悄悄失效了。"""
    c = (pkg.ip.get("area") or {}).get("corner") or {}
    if not pkg.regmap:
        return None
    got = c.get("gen_digest")
    now = gen_digest(pkg)
    if got is None:
        return f"{pkg.name} 的价目表没记生成产物摘要，无法判断是否失效"
    if str(got) != now:
        return (f"{pkg.name} 的价目表是对着另一份生成产物量的（记的 {got}，"
                f"现在 {now}）——生成的逻辑变了，面积已失效，重测再用")
    return None


def _shape(spec) -> str:
    return "points" if spec and "points" in spec else "affine"


def model_note(pkg: Pkg) -> str:
    a = pkg.ip.get("area") or {}
    e = a.get("error") or {}
    c = a.get("corner") or {}
    if not a:
        return "无价目表"
    gen = c.get("gen_digest", "?")
    return (f"{a.get('model', '?')}/{_shape(a.get('base'))}，生成器 {gen}，"
            f"误差界 {e.get('bound', '?')}"
            f"（{'恒为高估' if e.get('sign') == 'over' else e.get('sign', '?')}），"
            f"口径 {c.get('tool', '?')}/{c.get('pdk', '?')}@{c.get('freq_mhz', '?')}MHz"
            f" 测于 {c.get('measured', '?')}")
