"""价目表自己的门禁：已失效、曲线平坦、旋钮没计价。
"""
from xirang_core.manifest import Pkg
from xirang_area.price import stale


def outdated(pkg: Pkg) -> list[str]:
    """价目表是对着另一份生成产物量的。

    判据本身早就写好了，只是**没有任何命令调用它**：叶子 IP 改了 BSV、自己的门禁
    全绿、价目表当场失效而无人出声，要等别人 lint 一颗装配时才由锁的摘要间接照出来。
    这与「声明了没人实现」是同一形状——存在的检查不调用，比没有这条检查更糟，
    因为读代码的人会以为它在把关。

    这条要跑一次生成器（几百毫秒），所以摆在 lint 里而不是每次解析都做。
    """
    return [m] if (m := stale(pkg)) else []


def flat_param(pkg: Pkg) -> list[str]:
    """价目表里曲线平坦的参数：它什么也没改变。

    综合器对同一份 RTL 的重现性在百分之三上下，而一个真的进了数据通路的
    参数不可能几个格点分毫不差。`i2c` 的 fifoDepth 就是这么露的馅——
    从 1 到 8 都是 1029.56，回去看代码，队列一个都没例化。

    这条判据比扫源码还便宜：数据早就躺在 ip.yaml 里。
    """
    base = ((pkg.ip.get("area") or {}).get("base") or {})
    pts, per = base.get("points"), base.get("per")
    if not pts or not per or len(pts) < 2:
        return []
    vals = [float(v) for v in pts.values()]
    lo, hi = min(vals), max(vals)
    if hi <= 0 or (hi - lo) / hi >= 0.005:
        return []
    return [f"价目表里 {per} 的曲线是平的（{len(pts)} 个格点，"
            f"{lo:,.2f} 到 {hi:,.2f}）——这个参数什么也没改变。"
            f"要么实现它，要么把它从清单里去掉"]


def unmeasured(pkg: Pkg) -> list[str]:
    """标了价、却没有一行实测是它开着的特性。

    `uncosted` 只看价目表提没提到这个旋钮，于是 `fixed: 0.0` 这种占位价也算计价。
    `hart` 的 `mmu` 就这么过了门禁：两份 16 条的 CAM，价目表说它不要钱。
    包里既然有实测行，每个标了价的特性就得有一行是它开着的，否则这个价没有来处。
    """
    area = pkg.ip.get("area") or {}
    rows = area.get("measured") or []
    if not rows:
        return []
    dflt = {k: (v or {}).get("default")
            for sec in ("params", "features") for k, v in (pkg.ip.get(sec) or {}).items()}
    out = []
    for f, spec in (pkg.ip.get("features") or {}).items():
        if not (spec or {}).get("area"):
            continue
        if not any({**dflt, **dict(r.get("at") or {})}.get(f) is True for r in rows):
            out.append(f"特性 {f} 标了价，却没有一行实测是它开着的，价钱没有来处。"
                       f"在 area.measured 里加上它开着的配置（um2 先写 0），再跑 recal")
    return out


def noprice(pkg: Pkg) -> list[str]:
    """压根没有价目表。

    这不是缺陷，是一种状态：两块积木拼出来的 USB 转串口没有寄存器图、没有实测面积，
    照样该在我们自己的工具里当一等公民。所以它默认只报不挡（`XR-AREA-006` 是 info），
    要挡的人自己把级别调上去。
    """
    if pkg.is_library or pkg.is_assembly or (pkg.ip.get("area") or {}):
        return []
    return ["没有价目表——面积未知，不是面积为零"]


def uncosted(pkg: Pkg) -> list[str]:
    """价目表压根没提到的旋钮：改它，预测纹丝不动。

    平坦曲线那条判据只管「有曲线但曲线是平的」。更隐蔽的是**连曲线都没有**：
    `plic` 的 contexts 从 1 调到 16，每个上下文都要多一组阈值、使能与仲裁，
    而预测三次都是 6,028.96。叶子价目表承诺自己是上界，这种情况下它不是。

    与 test.unused 一样双向成立：写进 test.noarea 的旋钮如果其实已经计价，
    或者压根不存在，同样报错。
    """
    area = pkg.ip.get("area") or {}
    if not area:
        return []
    covered = set(area.get("params") or {})
    base = area.get("base") or {}
    if base.get("per"):
        covered.add(base["per"])
    for n, ft in (pkg.ip.get("features") or {}).items():
        a = ft.get("area") or {}
        if a:
            covered.add(n)
            if a.get("per"):
                covered.add(a["per"])
    knobs = set(pkg.ip.get("params") or {}) | set(pkg.ip.get("features") or {})
    declared = list((pkg.ip.get("test") or {}).get("noarea") or [])
    out = []
    bogus = [n for n in declared if n not in knobs]
    if bogus:
        out.append(f"test.noarea 提到清单里没有的旋钮 {sorted(bogus)}")
    stale = [n for n in declared if n in covered]
    if stale:
        out.append(f"test.noarea 里这几个其实已经计价，删掉 {sorted(stale)}")
    miss = sorted(knobs - covered - set(declared))
    if miss:
        out.append(f"价目表没提到这些旋钮 {miss}——改它们预测纹丝不动，"
                   f"而叶子价目表说自己是上界。要么量一条曲线，"
                   f"要么写进 ip.yaml 的 test.noarea 并说明为什么")
    return out
