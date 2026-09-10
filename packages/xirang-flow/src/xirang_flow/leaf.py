"""一个叶子 IP 的构建：生成扁平（或中立）顶层，综合，与价目表对账。"""
import pathlib

from xirang_area.price import price
from xirang_back.ecc import synth
from xirang_core.manifest import Bad, Pkg
from xirang_core.resolve import resolve_pkg
from xirang_gen.regmap import generate as gen_regmap
from xirang_gen.wrap import BUSES, flat_emit, neutral, wrap

from .report import Leaf


def bus_price(pkg: Pkg, index: dict[str, Pkg]) -> float:
    """总线绑定器的面积记在实现它的包上（apb4 -> amba），装配只付一次。"""
    e = flat_emit(pkg)
    if e is None:
        return 0.0
    m = index.get(BUSES[e["bus"]]["manifest"])
    return price(m, {})[0] if m and (m.ip.get("area") or {}).get("base") else 0.0


def build(pkg: Pkg, index: dict[str, Pkg], *, out: pathlib.Path,
          overrides: dict[str, object] | None = None, bare: bool = False,
          synthesise: bool = True, extra_src: list[str] | None = None) -> Leaf:
    """扁平顶层就是它独立流片的样子，综合的也是这一层。"""
    vals = resolve_pkg(pkg, {}, f"{pkg.path} (default)", None, overrides or {})
    (out / "bsv").mkdir(parents=True, exist_ok=True)
    (out / "sw").mkdir(parents=True, exist_ok=True)
    cap = pkg.name[:1].upper() + pkg.name[1:]
    src_f = out / "bsv" / (f"{cap}Bare.bsv" if bare else f"{cap}Wrap.bsv")
    src_f.write_text((neutral if bare else wrap)(pkg, vals), encoding="utf-8")
    if pkg.regmap:
        gen_regmap(pkg, out / "bsv", out / "sw")
    nums = [str(vals[k].value) for k, d in pkg.knobs().items()
            if d["kind"] == "param"]
    kind = "Bare" if bare else "Wrap"
    top = f"mk{cap}{kind}_{'_'.join(nums) if nums else '0'}"

    # 还没有价目表的包也得能量——不然就成了「想量它先得有它」。
    # 装配那一层才不许缺价目表：那里是在报总数，缺一块就是在骗人。
    try:
        want, _ = price(pkg, vals)
        if not bare:
            # 扁平顶层比 IP 本体多一个总线绑定器，那笔钱记在实现它的包上
            want += bus_price(pkg, index)
    except Bad:
        want = None

    rep = Leaf(out=str(out), top=top, want=want)
    if not synthesise:
        return rep
    rep.synthesised = True
    src = [str(p.root / "bsv") for p in index.values() if (p.root / "bsv").exists()]
    rep.got = synth(out, top, pkg.name,
                    extra_src=src + (extra_src or []), top_src=src_f)
    return rep
