"""装配与库包这两条门禁。

它们都要「先解析、再生成、再交给外部工具、再看结果」——跨了 core / gen / back
三个包，所以升到编排这一层，而不是挂在其中任何一个身上。
"""
import json
import pathlib
import shutil
import subprocess
import sys

from xirang_back.sim import schedule, sim
from xirang_core.manifest import GEN_HW, GEN_SW, GEN_TEST, Bad, Pkg
from xirang_core.matrix import points
from xirang_core.resolve import resolve, resolve_pkg
from xirang_gen.assemble import assemble
from xirang_gen.regmap import generate as gen_regmap

from . import tasks
from .logs import first_err, tail
from .matrix import run_gens
from .report import Lib, Mark, Matrix, Row


def _fresh(out: pathlib.Path, clean: bool) -> None:
    if out.exists() and clean:
        shutil.rmtree(out)


def assembly(pkg: Pkg, index: dict[str, Pkg], roots: list[pathlib.Path], *,
             out: pathlib.Path, clean: bool = False, point: str | None = None) -> Matrix:
    """装配的矩阵：每一点生成到 Verilog 看 G 编号，再跑装配自己的测试台。

    默认那一点必须过：`plic` 的完成规则单独综合时调度干净，接进 SoC 就与总线
    方法首尾相接、被整条丢掉（G0021）。这一类只在装配一级才现形。别的点照
    `test.axes` 派生，没写就只有默认那一点。
    """
    _fresh(out, clean)
    rep = Matrix(name=pkg.name)
    pts = points(pkg, index)
    if point:
        pts = [x for x in pts if x[0] == point]
        if not pts:
            raise Bad(f"矩阵里没有叫 {point} 的点")
    rep.points = len(pts)
    regs = out / GEN_HW
    regs.mkdir(parents=True, exist_ok=True)
    (out / GEN_SW).mkdir(parents=True, exist_ok=True)
    top_mod = "".join(w.capitalize() for w in pkg.name.replace("-", "_").split("_"))
    srcs = [str(d) for q in index.values() for d in q.dirs("hwsrc")]
    made: set[str] = set()
    seen: dict[tuple, str] = {}
    for lbl, ov, hand in pts:
        try:
            res = resolve(pkg.name, roots, cli={}, over=ov, over_from=f"测试点 {lbl}")
        except Bad as ex:
            # 派生出来的点撞上守卫是意料之中；手写的点撞上就是人写错了
            if hand:
                raise
            rep.rows.append(Row(lbl, Mark.skip, f"约束不允许：{ex}"))
            continue
        flat = _dotted(res)
        key = tuple(sorted((k, repr(v)) for k, v in flat.items()))
        if key in seen:
            rep.rows.append(Row(lbl, Mark.same, f"解析下来与 {seen[key]} 是同一点"))
            continue
        seen[key] = lbl
        for name in sorted({i.of for _, i in res.walk()} - made):
            if index[name].regmap:
                gen_regmap(index[name], regs, out / GEN_SW)
            made.add(name)
        here = out / lbl
        hw = here / GEN_HW
        hw.mkdir(parents=True, exist_ok=True)
        src = hw / f"{top_mod}Pkg.bsv"
        src.write_text(assemble(res, index, top_mod), encoding="utf-8")
        dirs = [str(hw), str(regs), *srcs]
        (here / "b").mkdir(parents=True, exist_ok=True)
        ok, hits, log = schedule(f"mk{top_mod}", src, ":".join(dirs) + ":+", here / "b")
        did, notes = ["调度"], []
        if not ok:
            notes.append("调度：" + (",".join(hits) if hits else first_err(log)))
        else:
            rows = _self_tests(pkg, here, dirs, lbl, flat)
            if rows:
                did.append(f"自检 {len(rows)} 个")
            notes += [f"{r.label}：{r.note}" for r in rows if r.mark is Mark.bad]
            notes += _task_tests(pkg, here, lbl, flat, did)
        ks = " ".join(f"{k}={v}" for k, v in sorted(ov.items()))
        rep.rows.append(Row(lbl, Mark.bad if notes else Mark.ok,
                            "；".join(notes) if notes else
                            " · ".join(x for x in (ks, "跑了" + "、".join(did)) if x)))
    return rep


def _task_tests(pkg: Pkg, here: pathlib.Path, lbl: str, flat: dict,
                did: list[str]) -> list[str]:
    """装配写的任务形式的测试：整片测试要在交付的那份 Verilog 上跑，不在 BSV 里跑。

    退出码就是判据。取值按点号路径给占位符（`{{knob.sw0.ports}}`）。
    """
    bad = []
    for u in pkg.upstream_tests():
        if not u.get("task"):
            bad.append(f"{u.get('name')}：装配的 test.upstream 只认任务形式")
            continue
        if any(flat.get(k) != v for k, v in (u.get("when") or {}).items()):
            continue
        did.append(u["name"])
        try:
            tasks.run(pkg, u["task"], flat, here / "up" / u["name"],
                      secs=u.get("timeout"))
        except Bad as ex:
            bad.append(f"{u['name']}：{ex}")
    return bad


def _dotted(res) -> dict[str, object]:
    """整棵实例树的取值，键是点号路径：生成脚本拿它改期望。"""
    out: dict[str, object] = {}

    def rec(insts, pre):
        for i in insts:
            for k, v in i.values.items():
                out[f"{pre}{i.name}.{k}"] = v.value
            rec(i.children, f"{pre}{i.name}.")
    rec(res.instances, "")
    return out


def _self_tests(pkg: Pkg, out: pathlib.Path, dirs: list[str], lbl: str,
                knobs: dict[str, object]) -> list[Row]:
    """装配自带的测试台：先跑 `htest/mk*.py` 生成，再逐个编译运行。

    生成脚本收到这一点的 `{label, knobs}`，与叶子同一个约定。测试台逐字节相同
    也照跑：被测的是这一点生成的 SoC，不是测试台。生成物写进这一点的输出目录，
    不碰包自己的 `htest/`。
    """
    tb = next((x for x in pkg.dirs("htest") if x.is_dir()), pkg.root / GEN_TEST)
    gens = sorted(tb.glob("mk*.py")) if tb.is_dir() else []
    if not gens:
        return []
    dest = out / GEN_TEST
    dest.mkdir(parents=True, exist_ok=True)
    arg = json.dumps({"label": lbl, "knobs": knobs}, ensure_ascii=False)
    for g in gens:
        r = subprocess.run([sys.executable, str(g), str(dest), arg], cwd=tb,
                           capture_output=True, text=True, timeout=300)
        if r.returncode:
            tail_ = (r.stdout + r.stderr).strip().splitlines()
            return [Row(label=g.name, mark=Mark.bad, note=tail_[-1] if tail_ else "生成失败")]
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
    """库包的行为测试，逐个矩阵点跑。

    地址图、写选通合并、总线绑定器都住在库包里，错了会影响每一个 IP。而库包
    恰恰是全库参数化最彻底的东西——`Apb4` 从头到尾按 `aw`/`dw` 写，测试台却
    钉死在一种位宽上。**没有旋钮就没有矩阵**这句话原本是循环的：它没有旋钮，
    正因为工具从来没给过它矩阵。

    没有旋钮的库包只有一个点，与从前逐字节相同。
    """
    tb = next((x for x in pkg.dirs("htest") if x.is_dir()), pkg.root / GEN_TEST)
    tbs = sorted(tb.glob("*Tb.bsv")) if tb.is_dir() else []
    rep = Lib(name=pkg.name)
    if not tbs:
        return rep
    _fresh(out, clean)
    srcs = [str(d) for p in index.values() for d in p.dirs("hwsrc")]
    pts = points(pkg)
    many = len(pts) > 1
    for label, ov, _ in pts:
        vals = resolve_pkg(pkg, {}, f"{pkg.path} (matrix)", None, dict(ov))
        here = out / (label if many else "b")
        gen = here / GEN_HW
        gen.mkdir(parents=True, exist_ok=True)
        if err := run_gens(pkg, gen, label, {k: v.value for k, v in vals.items()}):
            rep.rows.append(Row(label=label, mark=Mark.bad, note=err))
            continue
        path = ":".join([str(gen), str(tb), *srcs, "+"])
        for f in tbs:
            top = "mk" + f.stem
            ok, log = sim(top, f, path, here / "b")
            rep.rows.append(Row(label=f"{top} @ {label}" if many else top,
                                mark=Mark.ok if ok else Mark.bad,
                                note=_note(ok, log)))
    return rep
