"""装配与库包这两条门禁。

它们都要「先解析、再生成、再交给外部工具、再看结果」——跨了 core / gen / back
三个包，所以升到编排这一层，而不是挂在其中任何一个身上。
"""
import pathlib
import shutil

from xirang_back.sim import schedule, sim
from xirang_core.manifest import Pkg
from xirang_core.resolve import resolve
from xirang_gen.assemble import assemble
from xirang_gen.regmap import generate as gen_regmap

from .report import Gate, Lib, Mark, Row


def _fresh(out: pathlib.Path, clean: bool) -> None:
    if out.exists() and clean:
        shutil.rmtree(out)


def assembly(pkg: Pkg, index: dict[str, Pkg], roots: list[pathlib.Path], *,
             out: pathlib.Path, clean: bool = False) -> Gate:
    """装配的调度门禁：默认那一点生成到 Verilog，看 G 编号。

    矩阵不做——那是各实例矩阵的乘积，怎么取样才不爆炸还没想清楚。但默认
    那一点必须过：`plic` 的完成规则单独综合时调度干净，接进 SoC 就与总线
    方法首尾相接、被整条丢掉（G0021）。这一类只在装配一级才现形。
    """
    _fresh(out, clean)
    (out / "bsv").mkdir(parents=True, exist_ok=True)
    (out / "sw").mkdir(parents=True, exist_ok=True)
    res = resolve(pkg.name, roots, cli={})
    pkgs = {n: index[n] for n in {i.of for _, i in res.walk()} if n in index}
    pkgs |= index
    for name in sorted({i.of for _, i in res.walk()}):
        if pkgs[name].regmap:
            gen_regmap(pkgs[name], out / "bsv", out / "sw")
    top_mod = "".join(w.capitalize()
                      for w in res.top.replace("-", "_").split("_"))
    src = out / "bsv" / f"{top_mod}Pkg.bsv"
    src.write_text(assemble(res, pkgs, top_mod), encoding="utf-8")
    dirs = [str(out / "bsv")]
    dirs += [str(q.root / "bsv") for q in index.values()
             if (q.root / "bsv").exists()]
    work = out / "b"
    work.mkdir(parents=True, exist_ok=True)
    ok, hits, log = schedule(f"mk{top_mod}", src, ":".join(dirs) + ":+", work)
    lines = [] if ok else [
        ln.strip() for ln in log.splitlines()
        if any(g in ln for g in hits) or ln.startswith("Error")]
    return Gate(top=f"mk{top_mod}", ok=ok, hits=list(hits), lines=lines)


def library(pkg: Pkg, index: dict[str, Pkg], *,
            out: pathlib.Path, clean: bool = False) -> Lib:
    """库包的行为测试：没有旋钮就没有矩阵，`tb/*Tb.bsv` 直接编直接跑。

    地址图、写选通合并、总线绑定器都住在库包里，错了会影响每一个 IP——
    此前它们一条行为测试都没有，只做了类型检查。
    """
    tb = pkg.root / "tb"
    tbs = sorted(tb.glob("*Tb.bsv")) if tb.is_dir() else []
    rep = Lib(name=pkg.name)
    if not tbs:
        return rep
    _fresh(out, clean)
    (out / "b").mkdir(parents=True, exist_ok=True)
    srcs = [str(p.root / "bsv") for p in index.values()
            if (p.root / "bsv").is_dir()]
    for f in tbs:
        top = "mk" + f.stem
        path = ":".join([str(tb), *srcs, "+"])
        ok, log = sim(top, f, path, out / "b")
        last = log.strip().splitlines()[-1] if log.strip() else "没有输出"
        rep.rows.append(Row(label=top, mark=Mark.ok if ok else Mark.bad, note=last))
    return rep
