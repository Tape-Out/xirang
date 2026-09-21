"""装配与库包这两条门禁。

它们都要「先解析、再生成、再交给外部工具、再看结果」——跨了 core / gen / back
三个包，所以升到编排这一层，而不是挂在其中任何一个身上。
"""
import pathlib
import shutil
import subprocess
import sys

from xirang_back.sim import schedule, sim
from xirang_core.manifest import GEN_HW, GEN_SW, GEN_TEST, Pkg
from xirang_core.resolve import resolve
from xirang_gen.assemble import assemble
from xirang_gen.regmap import generate as gen_regmap

from .logs import tail
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
    (out / GEN_HW).mkdir(parents=True, exist_ok=True)
    (out / "sw").mkdir(parents=True, exist_ok=True)
    res = resolve(pkg.name, roots, cli={})
    pkgs = {n: index[n] for n in {i.of for _, i in res.walk()} if n in index}
    pkgs |= index
    for name in sorted({i.of for _, i in res.walk()}):
        if pkgs[name].regmap:
            gen_regmap(pkgs[name], out / GEN_HW, out / GEN_SW)
    top_mod = "".join(w.capitalize()
                      for w in res.top.replace("-", "_").split("_"))
    src = out / GEN_HW / f"{top_mod}Pkg.bsv"
    src.write_text(assemble(res, pkgs, top_mod), encoding="utf-8")
    dirs = [str(out / GEN_HW)]
    dirs += [str(d) for q in index.values() for d in q.dirs("hwsrc")]
    work = out / "b"
    work.mkdir(parents=True, exist_ok=True)
    ok, hits, log = schedule(f"mk{top_mod}", src, ":".join(dirs) + ":+", work)
    lines = [] if ok else [
        ln.strip() for ln in log.splitlines()
        if any(g in ln for g in hits) or ln.startswith("Error")]
    rows = _self_tests(pkg, out, dirs) if ok else []
    return Gate(top=f"mk{top_mod}", ok=ok, hits=list(hits), lines=lines, rows=rows)


def _self_tests(pkg: Pkg, out: pathlib.Path, dirs: list[str]) -> list[Row]:
    """装配自带的测试台：先跑 `htest/mk*.py` 生成，再逐个编译运行。

    组织流水线一直在跑这一步，本地 `ran test` 原来只到调度门禁为止，于是一处
    只有自检看得见的错（比如核读错了自己的 hartid）本地全绿，要推上去才现形。
    生成物写进测试输出目录，不碰包自己的 `htest/`。
    """
    tb = next((x for x in pkg.dirs("htest") if x.is_dir()), pkg.root / GEN_TEST)
    gens = sorted(tb.glob("mk*.py")) if tb.is_dir() else []
    if not gens:
        return []
    dest = out / GEN_TEST
    dest.mkdir(parents=True, exist_ok=True)
    for g in gens:
        r = subprocess.run([sys.executable, str(g), str(dest)], cwd=tb,
                           capture_output=True, text=True, timeout=300)
        if r.returncode:
            tail = (r.stdout + r.stderr).strip().splitlines()
            return [Row(label=g.name, mark=Mark.bad, note=tail[-1] if tail else "生成失败")]
    path = ":".join([*dirs, str(dest), str(tb), "+"])
    rows = []
    for f in sorted(dest.glob("*Tb.bsv")):
        top = "mk" + f.stem
        passed, log = sim(top, f, path, out / "sim")
        rows.append(Row(label=top, mark=Mark.ok if passed else Mark.bad, note=_note(passed, log)))
    return rows


def _note(ok: bool, log: str) -> str:
    """过了就取最后一行；没过先挑 FAIL、TIMEOUT、Error 那几行。

    原来一律取最后一行，编译失败时那往往是半截源码（`soc-switch` 少 import 时
    只显示了「Apb4::*;'」），看不出错在哪。
    """
    if ok:
        return log.strip().splitlines()[-1] if log.strip() else "没有输出"
    return tail(log)


def library(pkg: Pkg, index: dict[str, Pkg], *,
            out: pathlib.Path, clean: bool = False) -> Lib:
    """库包的行为测试：没有旋钮就没有矩阵，`htest/*Tb.bsv` 直接编直接跑。

    地址图、写选通合并、总线绑定器都住在库包里，错了会影响每一个 IP——
    此前它们一条行为测试都没有，只做了类型检查。
    """
    tb = next((x for x in pkg.dirs("htest") if x.is_dir()), pkg.root / GEN_TEST)
    tbs = sorted(tb.glob("*Tb.bsv")) if tb.is_dir() else []
    rep = Lib(name=pkg.name)
    if not tbs:
        return rep
    _fresh(out, clean)
    (out / "b").mkdir(parents=True, exist_ok=True)
    srcs = [str(d) for p in index.values() for d in p.dirs("hwsrc")]
    for f in tbs:
        top = "mk" + f.stem
        path = ":".join([str(tb), *srcs, "+"])
        ok, log = sim(top, f, path, out / "b")
        rep.rows.append(Row(label=top, mark=Mark.ok if ok else Mark.bad, note=_note(ok, log)))
    return rep
