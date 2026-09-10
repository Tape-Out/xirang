"""把一个叶子 IP 在整张矩阵上验一遍。

一个 IP 在默认配置下过了，不等于它在别的配置下也过——门控写漏了的表现恰恰是
「关掉了硬件还在」，那只有把那一个开关单独关掉才看得见。
"""
import hashlib
import json
import pathlib
import shutil
import subprocess
import sys

from xirang_area.check import flat_param, uncosted
from xirang_back.sim import schedule, sim
from xirang_core.manifest import Bad, Pkg
from xirang_core.matrix import points
from xirang_core.resolve import resolve_pkg
from xirang_gen.check import dead_inputs, unused_methods
from xirang_gen.regmap import generate as gen_regmap
from xirang_gen.tb import regs_tb
from xirang_gen.wrap import neutral

from .logs import first_err, tail
from .report import Mark, Matrix, Row


def tb_gens(pkg: Pkg) -> list[pathlib.Path]:
    d = pkg.root / "tb"
    return sorted(d.glob("mk*.py")) if d.is_dir() else []


def run_gens(pkg: Pkg, dest: pathlib.Path, lbl: str,
             knobs: dict[str, object]) -> str | None:
    """跑各仓自己的测试台生成脚本。

    第二个参数是这一点的旋钮，认矩阵的脚本会照它改名与改期望，不认的照旧
    生成同一份——内容一模一样的点不重复跑，于是「把测试台升级成认矩阵的」
    是一处纯局部的改动，不牵动工具。
    """
    dest.mkdir(parents=True, exist_ok=True)
    arg = json.dumps({"label": lbl, "knobs": knobs}, ensure_ascii=False)
    for g in tb_gens(pkg):
        r = subprocess.run([sys.executable, str(g), str(dest), arg],
                           cwd=str(g.parent), capture_output=True, text=True)
        if r.returncode != 0:
            return f"{g.name} 没跑成：{(r.stdout + r.stderr)[-800:]}"
    return None


def digest(d: pathlib.Path) -> str:
    h = hashlib.sha256()
    for f in sorted(d.glob("*.bsv")):
        h.update(f.name.encode())
        h.update(f.read_bytes())
    return h.hexdigest()[:16]


def run(pkg: Pkg, index: dict[str, Pkg], *, out: pathlib.Path,
        clean: bool = False, point: str | None = None,
        self_tb: bool = True) -> Matrix:
    if out.exists() and clean:
        shutil.rmtree(out)
    (out / "bsv").mkdir(parents=True, exist_ok=True)
    (out / "sw").mkdir(parents=True, exist_ok=True)
    # 寄存器组本身不随配置变——配置是例化时给的，所以只生成一次
    if pkg.regmap:
        gen_regmap(pkg, out / "bsv", out / "sw")
    src = [str(q.root / "bsv") for q in index.values() if (q.root / "bsv").exists()]
    work = out / "b"
    work.mkdir(parents=True, exist_ok=True)
    has_bsv = (pkg.root / "bsv").is_dir()
    cap = pkg.name[:1].upper() + pkg.name[1:]

    rep = Matrix(name=pkg.name)
    rep.problems = flat_param(pkg) + uncosted(pkg) + dead_inputs(pkg)
    if pkg.regmap:
        rep.problems += unused_methods(pkg, out / "bsv" / f"{cap}Regs.bsv")
    if rep.problems:
        return rep

    pts = points(pkg)
    if point:
        pts = [x for x in pts if x[0] == point]
        if not pts:
            raise Bad(f"矩阵里没有叫 {point} 的点")
    rep.points = len(pts)

    seen: dict[tuple, str] = {}
    tbseen: dict[str, str] = {}
    for lbl, ov, hand in pts:
        try:
            vals = resolve_pkg(pkg, {}, f"{pkg.path} (default)", None, ov)
        except Bad as ex:
            # 派生出来的点撞上约束是意料之中；手写的点撞上就是人写错了
            if hand:
                raise
            rep.rows.append(Row(lbl, Mark.skip, f"约束不允许：{ex}"))
            continue
        key = tuple(sorted((k, repr(v.value)) for k, v in vals.items()))
        if key in seen:
            rep.rows.append(Row(lbl, Mark.same, f"解析下来与 {seen[key]} 是同一点"))
            continue
        seen[key] = lbl

        knobs = {k: v.value for k, v in vals.items()}
        notes: list[str] = []
        bad = False
        ran = 0

        if has_bsv:
            ran += 1
            f = out / "bsv" / f"{cap}Bare{lbl}.bsv"
            f.write_text(neutral(pkg, vals, lbl), encoding="utf-8")
            nums = [str(vals[k].value) for k, d in pkg.knobs().items()
                    if d["kind"] == "param"]
            top = f"mk{cap}Bare{lbl}_{'_'.join(nums) if nums else '0'}"
            path = ":".join([str(out / "bsv"), *src]) + ":+"
            ok, hits, log = schedule(top, f, path, work)
            if not ok:
                bad = True
                notes.append("调度：" + (",".join(hits) if hits
                                         else first_err(log)))

        if pkg.regmap:
            ran += 1
            txt = regs_tb(pkg, vals, lbl)
            if txt:
                f = out / "bsv" / f"{cap}RegsTb{lbl}.bsv"
                f.write_text(txt, encoding="utf-8")
                path = ":".join([str(out / "bsv"), *src]) + ":+"
                ok, o = sim(f"mk{cap}RegsTb{lbl}", f, path, work)
                if not ok:
                    bad = True
                    notes.append("寄存器：" + tail(o))

        if tb_gens(pkg) and self_tb:
            d = out / "tb" / lbl
            if err := run_gens(pkg, d, lbl, knobs):
                bad = True
                notes.append(err)
            else:
                dg = digest(d)
                if dg in tbseen:
                    notes.append(f"行为测试与 {tbseen[dg]} 逐字节相同，不重跑")
                else:
                    tbseen[dg] = lbl
                    path = ":".join([str(out / "bsv"), str(d), *src]) + ":+"
                    for f in sorted(d.glob("*Tb.bsv")):
                        ok, o = sim(f"mk{f.stem}", f, path, work)
                        if not ok:
                            bad = True
                            notes.append(f"{f.stem}：" + tail(o))

        if not ran:
            # 什么都没跑却报绿，比报红还糟——那是在骗人
            notes.append("这个包既没有实现也没有寄存器图，没有可跑的检查")
        rep.rows.append(Row(lbl, Mark.bad if bad else Mark.ok,
                            "；".join(notes) if notes else
                            " ".join(f"{k}={v}" for k, v in sorted(knobs.items()))))
    return rep
