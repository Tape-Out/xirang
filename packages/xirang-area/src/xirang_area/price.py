"""价目表：实测过的配置直接查表，没测过的按「基线 + 每参数 + 每特性」叠加。

叠加不是白来的。gpio 全网格（6 个合法组合 × 4 个位宽）实测下来，特性之间会
互相借用逻辑：irq+bidir 在 8 针处比两项相加**多** 9.57%，在 32 针处又少 4.68%，
符号两边都有。所以叠加的结果要按实测的最差欠估抬一道余量，才谈得上「恒为高估」。
落在实测网格上的配置不走这条路——直接给实测值，既精确又不必抬。

装配那一层的承诺不同，见 annotate。
"""
from __future__ import annotations

from xirang_core.manifest import Bad, Pkg
from xirang_core.model import Instance, Resolved


def _interp(points: dict, x: float, delta: bool = False) -> float:
    """按实测点插值。落在点之间就线性插，落在两端之外的规矩见下。

    存点而不是拟直线，是因为参数曲线可能有结构断点——uart 的 FIFO 在深度 3 以上
    换了实现，一条直线怎么拟都会把中间低估。

    格点之外，两种曲线的规矩不同。绝对量的曲线有真实趋势，沿最近一段外推是对的，
    但不许推到比那一侧最近的实测点还低。增量曲线（特性与参数的代价）在大规模上被
    综合噪声主导，下降的尾巴不是趋势：`sram` 的 `sync` 四点量下来 1,030.96、
    1,249.64、1,866.76、282.80，沿最后一段外推到 1024 字得 −2,885.12，一个要花钱的
    特性成了省钱的，上界承诺当场作废。所以增量曲线出了格点一律取整条曲线的最大值。
    """
    xs = sorted(float(k) for k in points)
    ys = [float(points[k]) for k in sorted(points, key=lambda k: float(k))]
    if len(xs) == 1:
        return ys[0]
    out = x < xs[0] or x > xs[-1]
    if out and delta:
        return max(ys)
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
    y = ys[lo] + frac * (ys[hi] - ys[lo])
    if out:
        y = max(y, ys[0] if x < xs[0] else ys[-1])
    return y


def _term(spec: dict, vals, delta: bool = False) -> float:
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
        a = _interp(spec["points"], float(vals[per].value), delta)
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
    """这套旋钮实测过吗。实测过就不必再猜，也不必抬余量。

    每一行必须把旋钮写全。少写一个，那一行就把「那个旋钮取任意值」都认成
    自己量过的点——plic 原来只写 sources，于是 16 个上下文的配置也被当成
    2 个上下文那次实测，直接返回一个小了五倍的数，还不抬余量。
    """
    knobs = set(pkg.ip.get("params") or {}) | set(pkg.ip.get("features") or {})
    for row in (pkg.ip.get("area") or {}).get("measured", []) or []:
        at = row.get("at") or {}
        miss = sorted(knobs - set(at))
        if miss:
            raise Bad(f"{pkg.name} 的实测点 {at} 没写全旋钮 {miss}——"
                      f"少写一个就等于声称那个旋钮取什么值都是这个面积")
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
    # 没有价目表的包此前静默算成零。soc-linux 的核就这么被漏掉了——
    # 面板报 9,801，实际近 60,000，而它一声不吭。「不认识的键必须报错」
    # 同样适用于「没量过的面积」：答不出来就得说答不出来。
    # 装配自己没有 RTL，面积来自它的实例，所以不必有 base。
    # 库包同理：它的固定项写了就用，没写就是零。
    if not area.get("base") and not pkg.is_assembly and not pkg.is_library:
        raise Bad(f"{pkg.name} 没有价目表，面积算不出来——"
                  f"先跑一次 xirang build --neutral 把它量出来")
    total = _term(area.get("base"), vals)

    # 第二个、第三个参数。基线曲线只沿一个旋钮走，别的参数各给一条**增量**
    # 曲线：默认值处必须是 0，否则基线被算两次。没有这一段的时候，
    # plic 的 contexts 从 1 调到 16 预测纹丝不动，而叶子价目表说自己是上界。
    for pn, spec in (area.get("params") or {}).items():
        if pn not in vals:
            raise Bad(f"价目表引用了不存在的旋钮 {pn}")
        pts = spec.get("points")
        if not pts:
            raise Bad(f"{pkg.name} 的 params.{pn} 得给一条 points 曲线")
        # 参数的代价可以挂在某个特性上：mbox 的锁数只在自旋锁开着时算数，
        # 关掉之后一把锁也不例化，曲线照加就高估三倍多。
        gate = spec.get("when")
        if gate is not None:
            if gate not in (pkg.ip.get("features") or {}):
                raise Bad(f"{pkg.name} 的 params.{pn}.when 挂了不存在的特性 {gate}")
            if not vals[gate].value:
                per_knob[pn] = 0.0
                continue
        dflt = ((pkg.ip.get("params") or {}).get(pn) or {}).get("default")
        if dflt is not None and abs(_interp(pts, float(dflt), True)) > 1e-6:
            raise Bad(f"{pkg.name} 的 params.{pn} 是增量曲线，"
                      f"默认值 {dflt} 处必须为 0，否则基线被算两次")
        cost = _interp(pts, float(vals[pn].value), True)
        if cost < 0:
            raise Bad(f"{pkg.name} 的 params.{pn} 在 {vals[pn].value} 处算出 "
                      f"{cost:,.2f}——增量为负就是把这个旋钮算成省面积，"
                      f"叶子价目表的上界承诺不成立")
        per_knob[pn] = cost
        total += cost

    for fn, f in (pkg.ip.get("features") or {}).items():
        v = vals[fn].value
        spec = f.get("area")
        if not spec:
            continue
        if f.get("type", "bool") == "choice":
            # choice 的价目按档位取
            cost = _term(spec.get(v), vals, True) if isinstance(spec, dict) else 0.0
        else:
            cost = _term(spec, vals, True) if v else 0.0
        if cost < 0:
            raise Bad(f"{pkg.name} 的特性 {fn} 算出 {cost:,.2f}——增量为负就是把这个"
                      f"特性算成省面积，叶子价目表的上界承诺不成立")
        per_knob[fn] = cost
        total += cost

    m = float((pkg.ip.get("area") or {}).get("margin", 0.0))
    if lift and m:
        total *= (1.0 + m)
    return total, per_knob


# 装配层的偏差口径：由上面三个实测点定，留一点余头。超出就是模型该重看了。
ASM_BAND = 0.20


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
    # 顶层自己的面积只有装配才另算。叶子做顶层时它就是那唯一的实例，
    # 上面的 rec 已经按真实旋钮算过了；这里再算一次既重复计价，
    # 又因为拿的是空旋钮表而直接报「价目表引用了不存在的旋钮」。
    own = (price(root, {})[0]
           if root and root.is_assembly and (root.ip.get("area") or {}).get("base")
           else 0.0)
    # 库里的模块整颗芯片只例化一次（总线绑定器、交换网），所以按包记一次。
    own += sum(price(p, {})[0] for n, p in pkgs.items()
               if n != res.top and p.is_library
               and (p.ip.get("area") or {}).get("base"))
    # 装配不是各实例之和：综合会跨边界优化，独立综合时保住的端口在装配里被并掉，
    # 而嵌套的握手又比独立边界贵。实测这个系数在 0.94 到 1.29 之间，取中。
    #
    # 三个装配实测下来，预测都偏保守，幅度 13.59% / 14.53% / 18.51%
    # （soc-mcu 无核那版 / soc-linux 128 字 / soc-mcu 有核 256 字）。
    # 所以口径是「偏保守，但不超过 ASM_BAND」——两侧都查，见 cli 的 build。
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
    import hashlib

    from xirang_core.resolve import resolve_pkg
    from xirang_gen.regmap import bsv
    from xirang_gen.wrap import flat_emit, wrap
    # 没有寄存器图的 IP 也要有摘要。原来一见 regmap 为空就返回 None，
    # 于是 sram 这种纯手写的 IP 完全没有失效检测——改了实现，价目表照旧。
    parts = [bsv(pkg)] if pkg.regmap else []
    # 包装层也是生成的，也进面积。只取默认配置那一份即可——
    # 生成器一变，这一份就跟着变。
    if pkg.regmap and flat_emit(pkg) is not None:
        parts.append(wrap(pkg, resolve_pkg(pkg, {}, "digest", None, {})))
    # 手写的源码也要进摘要。只看生成物的话，改了 IP 自己的逻辑（uart 的取数、
    # spi 的采样、timer 的比较）面积明明变了却没人报警——那正是这套机制
    # 当初要防的「悄悄失效」。
    src = pkg.root / "bsv"
    if src.is_dir():
        for f in sorted(list(src.glob("*.bsv")) + list(src.glob("*.bs"))):
            parts.append(f.read_text(encoding="utf-8"))
    if not parts:
        return None
    return "sha256:" + hashlib.sha256("".join(parts).encode()).hexdigest()[:16]


def stale(pkg: Pkg) -> str | None:
    """价目表是不是对着另一份生成产物量的。是的话它已经悄悄失效了。"""
    c = (pkg.ip.get("area") or {}).get("corner") or {}
    now = gen_digest(pkg)
    if now is None:
        return None       # 既没有寄存器图也没有源码，没什么可摘要的
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
