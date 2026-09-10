"""XiRang / 息壤 —— 硬件的包管理器与装配器。

长命令 xirang，短命令 ran。
"""
from __future__ import annotations

import argparse
import hashlib
import re
import json
import pathlib
import shutil
import subprocess
import sys

import yaml

from xirang_area.price import ASM_BAND, annotate, model_note, price, stale
from xirang_area.recal import recal as do_recal, stamp as do_stamp
from xirang_back.ecc import synth
from xirang_back.sim import schedule, sim
from xirang_back.export import to_core, to_kconfig, to_tar
from xirang_core.lock import (check_submodules, make_lock, resolve_deps,
                              verify_lock, write_lock)
from xirang_core.manifest import Bad, Pkg
from xirang_core.matrix import points
from xirang_core.model import LAYERS, Resolved
from xirang_core.resolve import resolve, resolve_pkg
from xirang_gen.assemble import addr_map, assemble
from xirang_gen.regmap import generate as gen_regmap
from xirang_gen.tb import regs_tb
from xirang_gen.wrap import BUSES, flat_emit, neutral, wrap

BOLD, DIM, OFF = "\033[1m", "\033[2m", "\033[0m"


def _search(args) -> list[pathlib.Path]:
    return [pathlib.Path(p).resolve() for p in (args.path or ["."])]


def _load_all(res: Resolved, search) -> dict[str, Pkg]:
    out = {}
    for d in search:
        for p in d.iterdir():
            if (p / "ip.yaml").exists():
                pk = Pkg(p)
                out[pk.name] = pk
    return out


def _resolve(args) -> tuple[Resolved, dict[str, Pkg]]:
    search = _search(args)
    cli = {}
    for kv in args.set or []:
        k, _, v = kv.partition("=")
        cli[k] = yaml.safe_load(v)
    res = resolve(args.top, search, cli=cli)
    pkgs = _load_all(res, search)
    annotate(res, pkgs)
    # 库包也要查。原来只查作为实例出现的包，于是 hwcore 与 amba 改了源码
    # 也没人报警——而它们是全库踩着的那一层，改一行影响每一个 IP。
    for name in sorted(pkgs):
        w = stale(pkgs[name])
        if w:
            print(f"\033[33m警告\033[0m {w}", file=sys.stderr)
    return res, pkgs


# ---------------------------------------------------------------- config

def cmd_config(args) -> int:
    res, pkgs = _resolve(args)
    if args.why:
        return _why(res, pkgs, args.why)
    print(f"{BOLD}{res.top}{OFF}  bus={res.bus}  合计 {res.area_um2:,.2f} µm²")
    for depth, inst in res.walk():
        pad = "  " * depth
        a = f"{inst.addr:#010x}" if inst.addr is not None else "-"
        print(f"\n{pad}{BOLD}{inst.name}{OFF} : {inst.of} @ {a}"
              f"   {inst.area_um2:,.2f} µm²")
        for k, v in inst.values.items():
            mark = "!" if v.forced_by else " "
            cost = f"{v.area_um2:>10,.2f}" if v.area_um2 else " " * 10
            print(f"{pad}  {mark} {k:<12} = {str(v.value):<8}"
                  f" {cost}  {DIM}{v.layer}{OFF}")
    print(f"\n{DIM}! = 被约束强制，不是被上层覆盖{OFF}")
    return 0


def _why(res: Resolved, pkgs, path: str) -> int:
    """CSS 那种 computed 面板：一条属性的完整来历。"""
    hit = res.find(path)
    if not hit:
        print(f"没有 {path}", file=sys.stderr)
        return 1
    inst, v = hit
    pkg = pkgs[inst.of]
    print(f"{BOLD}{path}{OFF} = {BOLD}{v.value}{OFF}")
    print()
    print("  层叠（低到高，越靠下越优先）：")
    order = sorted([*v.shadowed, v.winner], key=lambda c: LAYERS.index(c.layer))
    for c in order:
        won = c is v.winner and not v.forced_by
        tick = f"{BOLD}✔{OFF}" if won else " "
        strike = "" if won else DIM
        print(f"    {tick} {strike}{c.layer:<12} {str(c.value):<10} {c.origin}{OFF}")
    if v.forced_by:
        print()
        print(f"  {BOLD}最终值由约束强制{OFF}：{v.forced_by}")
        print(f"  {DIM}这不是被上层覆盖——层叠给出的是 {v.winner.value}，"
              f"约束把它压成了 {v.value}{OFF}")
    print()
    print(f"  面积：{v.area_um2:,.2f} µm²    模型：{model_note(pkg)}")
    return 0


# ---------------------------------------------------------------- lock

def _index(search) -> dict[str, Pkg]:
    out = {}
    for d in search:
        for p in sorted(d.iterdir()):
            if (p / "ip.yaml").exists():
                pk = Pkg(p)
                if pk.name in out and out[pk.name].root != p:
                    raise Bad(f"包名 {pk.name} 出现两次："
                              f"{out[pk.name].root} 与 {p}")
                out[pk.name] = pk
    return out


def _top_pkg(args, index) -> Pkg:
    if args.top not in index:
        raise Bad(f"找不到包 {args.top}")
    return index[args.top]


def cmd_lock(args) -> int:
    search = _search(args)
    index = _index(search)
    top = _top_pkg(args, index)
    resolved = resolve_deps(top, index)
    root = search[0]
    lock = make_lock(top, resolved, root)
    # 锁跟着装配走，不是跟着工作区走：装配是交付物，锁是它的一部分
    out = pathlib.Path(args.out or (top.root / "xirang.lock"))
    write_lock(lock, out)
    print(f"{out}  锁定 {len(lock['packages'])} 个包")
    for p in lock["packages"]:
        print(f"  {p['name']:<10} {p['version']:<8} {p['kind']:<8} {p['digest']}")
    return 0


def cmd_lint(args) -> int:
    search = _search(args)
    index = _index(search)
    top = _top_pkg(args, index)
    resolved = resolve_deps(top, index)
    problems = check_submodules(search[0], resolved)
    lockf = top.root / "xirang.lock"
    if not lockf.exists():
        lockf = search[0] / "xirang.lock"
    notes = []
    lock = yaml.safe_load(lockf.read_text(encoding="utf-8")) if lockf.exists() else None
    if lock and lock.get("top") == top.name:
        problems += verify_lock(lock, resolved)
    elif lock:
        notes.append(f"工作区的锁钉的是 {lock.get('top')}，这次不查")
    elif top.is_assembly:
        # 装配是交付物，必须钉死解析结果；叶子 IP 是被别人依赖的库，
        # 钉死反而会跟使用者的解析冲突（cargo 对库与二进制的区分同理）
        problems.append("装配没有 xirang.lock——跑一次 xirang lock 把解析结果钉住")
    elif top.ip.get("deps"):
        notes.append("叶子 IP 不带锁，由使用它的装配去钉")
    if not problems:
        print("干净" + ("；" + "，".join(notes) if notes else ""))
        return 0
    for p in problems:
        print(f"  {p}")
    return 1


# ---------------------------------------------------------------- tree

def cmd_tree(args) -> int:
    res, _ = _resolve(args)
    print(f"{res.top}  {res.area_um2:,.2f} µm²")
    items = list(res.walk())
    for n, (depth, inst) in enumerate(items):
        last = n == len(items) - 1 or items[n + 1][0] <= depth
        stem = "  " * depth + ("└─ " if last else "├─ ")
        print(f"{stem}{inst.name} : {inst.of}   {inst.area_um2:,.2f} µm²")
    return 0


# ---------------------------------------------------------------- wrap

def cmd_gen(args) -> int:
    """只从 regmap.yaml 生成寄存器组与 C 头。

    还没写 BSV 的仓也能过门禁——寄存器图本身就该被检查，不必等实现。
    """
    index = _index(_search(args))
    if args.top not in index:
        raise Bad(f"找不到包 {args.top}")
    pkg = index[args.top]
    if not pkg.regmap:
        raise Bad(f"{args.top} 没有 regmap.yaml，没什么可生成的")
    out = pathlib.Path(args.out or "gen")
    (out / "bsv").mkdir(parents=True, exist_ok=True)
    (out / "sw").mkdir(parents=True, exist_ok=True)
    gen_regmap(pkg, out / "bsv", out / "sw")
    if getattr(args, "tb", False):
        cli = {}
        for kv in args.set or []:
            k, _, v = kv.partition("=")
            cli[k] = yaml.safe_load(v)
        vals = resolve_pkg(pkg, {}, f"{pkg.path} (default)", None, cli)
        cap = (pkg.regmap.get("ip", pkg.name))[:1].upper()             + (pkg.regmap.get("ip", pkg.name))[1:]
        txt = regs_tb(pkg, vals)
        if txt:
            (out / "bsv" / f"{cap}RegsTb.bsv").write_text(txt, encoding="utf-8")
        else:
            print("  （寄存器全是数组或宽寄存器，本版的一致性测试测不了）")
    for p in sorted((out / "bsv").glob("*.bsv")) + sorted((out / "sw").glob("*")):
        print(f"  {p}")
    return 0


def cmd_wrap(args) -> int:
    """给一个叶子 IP 生成扁平端口顶层。装配没有这一层——它本身就是顶层。"""
    search = _search(args)
    index = _index(search)
    if args.top not in index:
        raise Bad(f"找不到包 {args.top}")
    pkg = index[args.top]
    if pkg.is_assembly:
        raise Bad(f"{args.top} 是装配，它本身就是顶层，不需要 wrap")
    if pkg.is_library:
        raise Bad(f"{args.top} 是库包，不会被例化，也就没有端口")
    if flat_emit(pkg) is None:
        raise Bad(f"{args.top} 的 emit 里没有 verilog-flat——"
                  f"想要独立可流片就把它加上，并选一种 bus")

    cli = {}
    for kv in args.set or []:
        k, _, v = kv.partition("=")
        cli[k] = yaml.safe_load(v)
    vals = resolve_pkg(pkg, {}, f"{pkg.path} (default)", None, cli)

    out = pathlib.Path(args.out or (pkg.root / "wrap"))
    out.mkdir(parents=True, exist_ok=True)
    cap = pkg.name[:1].upper() + pkg.name[1:]
    txt = wrap(pkg, vals)
    f = out / f"{cap}Wrap.bsv"
    f.write_text(txt, encoding="utf-8")
    # 寄存器组也要一并放过去，否则这一层独立编不了
    gen_regmap(pkg, out, out)
    print(f"{f}")
    knobs = ", ".join(f"{k}={v.value}" for k, v in vals.items())
    print(f"  {knobs}")
    return 0


# ---------------------------------------------------------------- export

def _to_doc(res: Resolved) -> dict:
    def one(i):
        d = {"name": i.name, "of": i.of,
             "with": {k: v.value for k, v in i.values.items()}}
        if i.addr is not None:
            d["addr"] = f"{i.addr:#010x}"
        if i.bus:
            d["bus"] = i.bus
        if i.children:
            d["instances"] = [one(c) for c in i.children]
        return d
    return {"xirang": 1, "top": res.top, "bus": res.bus,
            "instances": [one(i) for i in res.instances]}


def cmd_export(args) -> int:
    res, pkgs = _resolve(args)
    fmt = args.format or "resolved"

    if fmt == "tar":
        # tar 要有生成物才装得进去，所以先跑一遍不综合的 build
        build = pathlib.Path(args.build or "build").resolve()
        if not (build / "bsv").is_dir():
            raise Bad(f"{build} 里没有生成物——先跑一次 xirang build --no-synth")
        top_mod = "mk" + "".join(w.capitalize()
                                 for w in res.top.replace("-", "_").split("_"))
        out = pathlib.Path(args.out or f"{res.top}.tar.gz")
        n = to_tar(res, pkgs, build, out, top_mod)
        print(f"{out}  {n} 个源文件，解开后只要 bsc 就能重跑")
        return 0

    if fmt == "core":
        build = pathlib.Path(args.build or "build").resolve()
        files = sorted(f.name for f in (build / "rtl").glob("*.v")) \
            if (build / "rtl").is_dir() else []
        # 空的 fileset 是一份没法用的 .core，而它长得像一份能用的。
        # 「不许静默忽略」在这里就是：没有 Verilog 就说没有，
        # 别照样吐一份出来让人以为能喂给 fusesoc。
        if not files:
            raise Bad(f"{build}/rtl 里没有 Verilog，导不出能用的 .core"
                      f"——先跑一次不带 --no-synth 的 build")
        txt = to_core(res, pkgs, files)
    elif fmt == "kconfig":
        txt = to_kconfig(res, pkgs)
    else:
        txt = yaml.safe_dump(_to_doc(res), sort_keys=False, allow_unicode=True)

    if args.out:
        pathlib.Path(args.out).write_text(txt, encoding="utf-8")
        print(args.out)
    else:
        sys.stdout.write(txt)
    return 0


# ---------------------------------------------------------------- build

def _bus_price(pkg, index) -> float:
    """总线绑定器的面积记在实现它的包上（apb4 -> amba），装配只付一次。"""
    e = flat_emit(pkg)
    if e is None:
        return 0.0
    m = index.get(BUSES[e["bus"]]["manifest"])
    return price(m, {})[0] if m and (m.ip.get("area") or {}).get("base") else 0.0


def _build_leaf(args, pkg) -> int:
    """叶子 IP 的 build：扁平顶层就是它独立流片的样子，综合的也是这一层。"""
    cli = {}
    for kv in args.set or []:
        k, _, v = kv.partition("=")
        cli[k] = yaml.safe_load(v)
    vals = resolve_pkg(pkg, {}, f"{pkg.path} (default)", None, cli)
    out = pathlib.Path(args.out or "build").resolve()
    (out / "bsv").mkdir(parents=True, exist_ok=True)
    (out / "sw").mkdir(parents=True, exist_ok=True)
    cap = pkg.name[:1].upper() + pkg.name[1:]
    bare = getattr(args, "neutral", False)
    src_f = out / "bsv" / (f"{cap}Bare.bsv" if bare else f"{cap}Wrap.bsv")
    src_f.write_text((neutral if bare else wrap)(pkg, vals), encoding="utf-8")
    if pkg.regmap:
        gen_regmap(pkg, out / "bsv", out / "sw")
    nums = [str(vals[k].value) for k, d in pkg.knobs().items() if d["kind"] == "param"]
    kind = "Bare" if bare else "Wrap"
    top_mod = f"mk{cap}{kind}_{'_'.join(nums) if nums else '0'}"
    # 还没有价目表的包也得能量——不然就成了「想量它先得有它」。
    # 装配那一层才不许缺价目表：那里是在报总数，缺一块就是在骗人。
    try:
        want, _ = price(pkg, vals)
        if not bare:
            # 扁平顶层比 IP 本体多一个总线绑定器，那笔钱记在实现它的包上
            want += _bus_price(pkg, _index(_search(args)))
    except Bad:
        want = None
    print(f"生成于 {out}")
    if want is None:
        print(f"  顶层 {top_mod}   还没有价目表，这次只测不比")
    else:
        print(f"  顶层 {top_mod}   预测面积 {want:,.2f} µm²")
    if args.no_synth:
        print("  (--no-synth，跳过综合)")
        return 0
    idx = _index(_search(args))
    src = [str(p.root / "bsv") for p in idx.values() if (p.root / "bsv").exists()]
    got = synth(out, top_mod, pkg.name, extra_src=src + (args.bsv_path or []),
                top_src=src_f)
    if got is None:
        print("  综合未跑通", file=sys.stderr)
        return 1
    if want is None:
        print(f"  实测面积 {got:,.2f} µm²")
        return 0
    err = (got - want) / got * 100 if got else 0
    print(f"  实测面积 {got:,.2f} µm²   预测偏差 {err:+.2f}%")
    return 0


def cmd_recal(args) -> int:
    """价目表回填：重测记着的每一种配置，再重算曲线。

    改了生成器或 IP 源码之后价目表就失效了（`xirang lint` 会报）。这条命令是
    组织级 CI 那一步的实现，本地也能跑。默认只看不写，`--apply` 才落盘。
    """
    idx = _index(_search(args))
    if args.top not in idx:
        raise Bad(f"找不到包 {args.top}")
    pkg = idx[args.top]
    print(f"{BOLD}{pkg.name}{OFF}  回填价目表"
          f"{'' if args.apply else '（只看不写，加 --apply 才落盘）'}")
    for line in do_recal(pkg, _search(args), args.apply):
        if not line.startswith("{"):
            print(f"  {line}")
    if args.apply:
        w = stale(idx[args.top].__class__(pkg.root))
        print("  摘要已盖" if not w else f"  {w}")
    return 0


def cmd_build(args) -> int:
    search = _search(args)
    doc = None
    if args.config:
        doc = yaml.safe_load(pathlib.Path(args.config).read_text(encoding="utf-8"))
        if doc.get("xirang") != 1:
            raise Bad(f"{args.config} 不是 xirang 导出的配置")
        args.top = doc["top"]
    # 叶子那条路两条入口共用。分开写过一次，结果是同一份配置直接 build 出
    # `GpioBare.bsv`、按导出的配置 build 出 `GpioPkg.bsv`——V4 当场抓到。
    idx = _index(search)
    if args.top in idx and not idx[args.top].is_assembly:
        if doc is not None:
            one = (doc.get("instances") or [{}])[0]
            args.set = list(args.set or []) + [
                f"{k}={yaml.safe_dump(v).strip()}"
                for k, v in (one.get("with") or {}).items()]
        return _build_leaf(args, idx[args.top])
    if args.config:
        res = _from_doc(doc, search)
        pkgs = _load_all(res, search)
        annotate(res, pkgs)
    else:
        res, pkgs = _resolve(args)

    if getattr(args, "locked", False):
        index = _index(search)
        lf = index[res.top].root / "xirang.lock"
        if not lf.exists():
            lf = search[0] / "xirang.lock"
        problems = verify_lock(
            yaml.safe_load(lf.read_text(encoding="utf-8")),
            resolve_deps(index[res.top], index))
        if problems:
            for p in problems:
                print(f"xirang: {p}", file=sys.stderr)
            return 1

    out = pathlib.Path(args.out or "build").resolve()
    (out / "bsv").mkdir(parents=True, exist_ok=True)
    (out / "sw").mkdir(parents=True, exist_ok=True)

    # 1 每个用到的包，从 regmap.yaml 生成寄存器组
    for name in sorted({i.of for _, i in res.walk()}):
        p = pkgs[name]
        if p.regmap:
            gen_regmap(p, out / "bsv", out / "sw")

    # 2 装配出顶层
    top_mod = "".join(w.capitalize() for w in res.top.replace("-", "_").split("_"))
    (out / "bsv" / f"{top_mod}Pkg.bsv").write_text(
        assemble(res, pkgs, top_mod), encoding="utf-8")

    # 3 地址图与解析结果落盘
    (out / "regmap.json").write_text(
        json.dumps(_to_doc(res), indent=2, ensure_ascii=False), encoding="utf-8")
    (out / "addr_map.md").write_text(addr_map(res), encoding="utf-8")

    print(f"生成于 {out}")
    print(f"  顶层 mk{top_mod}   预测面积 {res.area_um2:,.2f} µm²")

    if args.no_synth:
        print("  (--no-synth，跳过综合)")
        return 0
    # 每个用到的包各自的 bsv/ 都进 bsc 搜索路径
    src = [str(pkgs[n].root / "bsv") for n in sorted({i.of for _, i in res.walk()})
           if (pkgs[n].root / "bsv").exists()]
    for p in pkgs.values():                      # 依赖包（hwcore 之类）也带上
        if (p.root / "bsv").exists() and str(p.root / "bsv") not in src:
            src.append(str(p.root / "bsv"))
    got = synth(out, f"mk{top_mod}", res.top, extra_src=src + (args.bsv_path or []))
    if got is None:
        print("  综合未跑通", file=sys.stderr)
        return 1
    err = (got - res.area_um2) / got * 100 if got else 0
    # 两侧都查。装配层自称是「双侧估计」，只查一侧的话那句话没人管：
    # 保守到两成开外，面板就没法用来比较配置了，那是它另一半用途。
    if res.area_um2 < got:
        verdict = "✘ 预测低于实测，模型作废"
    elif abs(err) > ASM_BAND * 100:
        verdict = f"✘ 保守过头，超出模型声明的 {ASM_BAND:.0%}"
    else:
        verdict = "✔ 预测偏保守，在口径之内"
    print(f"  实测面积 {got:,.2f} µm²   预测偏差 {err:+.2f}%   {verdict}")
    return 0 if res.area_um2 >= got and abs(err) <= ASM_BAND * 100 else 1


def _from_doc(doc, search) -> Resolved:
    """从导出的 resolved 配置重建模型。round-trip 判据靠它。"""
    from xirang_core.model import Candidate, Instance, Value

    def one(d) -> Instance:
        vals = {k: Value(name=k, value=v,
                         winner=Candidate("instance", v, "<resolved>"))
                for k, v in (d.get("with") or {}).items()}
        return Instance(name=d["name"], of=d["of"], values=vals,
                        addr=int(str(d["addr"]), 0) if d.get("addr") else None,
                        bus=d.get("bus"),
                        children=[one(c) for c in d.get("instances", [])])

    res = Resolved(top=doc["top"], bus=doc["bus"],
                   instances=[one(i) for i in doc["instances"]])
    # 地址与大小从各自的 regmap 补回
    pkgs = _load_all(res, search)
    for _, i in res.walk():
        p = pkgs.get(i.of)
        if not (p and p.regmap):
            continue
        # 没有控制口的实例（核）不进地址图：它的 regmap 描述的是自己的 CSR
        # 空间，跟片上地址空间无关。解析那一路一直这么判，导入这一路没判，
        # 于是往返一圈核就多了个 0 地址——soc-mcu 补上核之后 V4 当场变红。
        shape = ((p.ip.get("contract") or {}).get("ctrl") or {}).get(
            "shape", "flat")
        if shape == "none":
            continue
        if i.addr is None:
            i.addr = int(str(p.regmap.get("base")), 0)
        i.size = int(str(p.regmap.get("size")), 0)
    return res


# ---------------------------------------------------------------- test

def _tb_gens(pkg: Pkg) -> list[pathlib.Path]:
    d = pkg.root / "tb"
    return sorted(d.glob("mk*.py")) if d.is_dir() else []


def _run_gens(pkg: Pkg, dest: pathlib.Path, lbl: str, knobs: dict) -> str | None:
    """跑各仓自己的测试台生成脚本。

    第二个参数是这一点的旋钮，认矩阵的脚本会照它改名与改期望，不认的照旧
    生成同一份——内容一模一样的点不重复跑，于是「把测试台升级成认矩阵的」
    是一处纯局部的改动，不牵动工具。
    """
    dest.mkdir(parents=True, exist_ok=True)
    arg = json.dumps({"label": lbl, "knobs": knobs}, ensure_ascii=False)
    for g in _tb_gens(pkg):
        r = subprocess.run([sys.executable, str(g), str(dest), arg],
                           cwd=str(g.parent), capture_output=True, text=True)
        if r.returncode != 0:
            return f"{g.name} 没跑成：{(r.stdout + r.stderr)[-800:]}"
    return None


def _digest(d: pathlib.Path) -> str:
    h = hashlib.sha256()
    for f in sorted(d.glob("*.bsv")):
        h.update(f.name.encode())
        h.update(f.read_bytes())
    return h.hexdigest()[:16]


def _dead_inputs(pkg: Pkg) -> list[str]:
    """引脚驱进来的值，模块里得真的读。

    `i2c` 的 `scl_in` 就这么躺着：接口上有、`always_enabled` 每拍都驱、
    模块里一次也没读——于是时钟延展完全不认，而调度门禁与寄存器一致性
    都查不出来。同一类的还有 `aclint` 那根没人接的 SSWI 出线。

    判据很直接：`mkBypassWire` / `mkDWire` 声明出来的线，除了声明那一行
    与写它的那一行之外，还得在别处出现过。

    与 test.unused、test.noarea 一样双向成立：写进 test.deadread 的名字
    如果其实已经被读了，同样报错——豁免名单不许留着过期的条目。
    """
    bsv = pkg.root / "bsv"
    if not bsv.is_dir():
        return []
    out: list[str] = []
    dead: list[str] = []
    for f in sorted(bsv.glob("*.bsv")) + sorted(bsv.glob("*.bs")):
        src = f.read_text(encoding="utf-8", errors="ignore")
        for m in re.finditer(r"^\s*Wire#\([^;]*?\)\s+(\w+)\s*<-\s*mk(?:Bypass|D)Wire",
                             src, re.M):
            name = m.group(1)
            uses = 0
            for line in src.splitlines():
                bare = line.split("//")[0]
                if re.search(rf"\b{name}\b", bare) is None:
                    continue
                if re.search(rf"\b{name}\s*<-\s*mk", bare):
                    continue          # 声明
                if re.search(rf"\b{name}\s*(?:\._write\(|<=)", bare):
                    continue          # 只是在写它
                uses += 1
            if uses == 0:
                dead.append(name)
    declared = list((pkg.ip.get("test") or {}).get("deadread") or [])
    for name in dead:
        if name not in declared:
            out.append(f"{name} 只写不读——引脚驱进来了，逻辑里一次也没用过。"
                       f"确实不需要就写进 ip.yaml 的 test.deadread 并说明理由")
    stale = [x for x in declared if x not in dead]
    if stale:
        out.append(f"test.deadread 里这几个其实已经被读了，删掉 {sorted(stale)}")
    return out


def _unused_methods(pkg: Pkg, gen_file: pathlib.Path) -> list[str]:
    """寄存器接口暴露的方法，实现里得真的用到。

    只在清单里、实现里没人读的字段是最贵的错：不报错、不告警，面板还会
    认真地把它标价为零。`emac` 的 `ctrl.loop` 就这么躺着——寄存器写得进去，
    环回一次也没发生过。

    确实用不到的，写进 ip.yaml 的 test.unused 并说明理由。那份名单反过来
    也要成立：名单里的方法一旦被用上了，或者压根不存在，同样报错。
    """
    src = "".join(
        f.read_text(encoding="utf-8", errors="ignore")
        for f in sorted((pkg.root / "bsv").glob("*.bsv"))
        + sorted((pkg.root / "bsv").glob("*.bs")))
    # 只看本包这一份。输出目录是跨包复用的，通配一扫就把上一个包留下的
    # 寄存器组也算进来，报出一长串别人的方法。
    if not gen_file.is_file():
        raise Bad(f"{pkg.name} 有寄存器图，却没生成出 {gen_file.name}")
    out: list[str] = []
    names: list[str] = []
    ifc = gen_file.read_text(encoding="utf-8").split("endinterface")[0]
    # 返回类型里有空格的方法（Bit#(TLog#(TAdd#(contexts, 1))) 这种）不能用
    # 「method 类型 名字」去套——原来的正则把它们整个漏掉，plic 的两个下标
    # 方法于是从来没被查过。改成：method 之后第一个「标识符紧跟 ; 或 (」。
    for line in ifc.splitlines():
        m = re.search(r"\bmethod\b(.*)", line.split("//")[0])
        if not m:
            continue
        g = re.search(r"(\w+)\s*[;(]", m.group(1))
        if g and g.group(1) != "regs" and g.group(1) not in names:
            names.append(g.group(1))
    declared = list((pkg.ip.get("test") or {}).get("unused") or [])
    bogus = [n for n in declared if n not in names]
    if bogus:
        out.append(f"test.unused 提到寄存器接口里没有的方法 {sorted(bogus)}")
    dead = [n for n in names if f".{n}" not in src]
    stale = [n for n in declared if n in names and n not in dead]
    if stale:
        out.append(f"test.unused 里这几个其实已经用上了，删掉 {sorted(stale)}")
    left = [n for n in dead if n not in declared]
    if left:
        out.append(f"寄存器图声明了、实现里没人用：{sorted(left)}"
                   f"——要么实现，要么写进 ip.yaml 的 test.unused 并说明为什么")
    return out

def _flat_param(pkg: Pkg) -> list[str]:
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

def _uncosted(pkg: Pkg) -> list[str]:
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


def _asm_gate(args, index, pkg: Pkg) -> int:
    """装配的调度门禁：默认那一点生成到 Verilog，看 G 编号。

    矩阵不做——那是各实例矩阵的乘积，怎么取样才不爆炸还没想清楚。但默认
    那一点必须过：`plic` 的完成规则单独综合时调度干净，接进 SoC 就与总线
    方法首尾相接、被整条丢掉（G0021）。这一类只在装配一级才现形，而这条
    命令原来见到装配直接拒收，于是只能等组织 CI 去撞。
    """
    out = pathlib.Path(args.out or "test").resolve()
    if out.exists() and args.clean:
        shutil.rmtree(out)
    (out / "bsv").mkdir(parents=True, exist_ok=True)
    (out / "sw").mkdir(parents=True, exist_ok=True)
    res = resolve(pkg.name, _search(args), cli={})
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
    print(f"{BOLD}{res.top}{OFF}  装配调度门禁（默认那一点）")
    ok, hits, log = schedule(f"mk{top_mod}", src, ":".join(dirs) + ":+", work)
    if ok:
        print(f"  ✔ mk{top_mod}")
        return 0
    print(f"  {BOLD}✘{OFF} mk{top_mod}  "
          f"{' '.join(hits) if hits else '编译失败'}")
    for ln in log.splitlines():
        if any(g in ln for g in hits) or ln.startswith("Error"):
            print(f"      {ln.strip()}")
    return 1

def _lib_test(args, index, pkg) -> int:
    """库包的行为测试：没有旋钮就没有矩阵，`tb/*Tb.bsv` 直接编直接跑。

    地址图、写选通合并、总线绑定器都住在库包里，错了会影响每一个 IP——
    此前它们一条行为测试都没有，只做了类型检查。
    """
    tbs = sorted((pkg.root / "tb").glob("*Tb.bsv")) if (pkg.root / "tb").is_dir() else []
    print(f"{BOLD}{pkg.name}{OFF}  库包，{len(tbs)} 份行为测试")
    if not tbs:
        print("  没有 tb/，只做类型检查")
        return 0
    out = pathlib.Path(args.out or "test").resolve()
    if out.exists() and args.clean:
        shutil.rmtree(out)
    (out / "b").mkdir(parents=True, exist_ok=True)
    srcs = [str(p.root / "bsv") for p in index.values()
            if (p.root / "bsv").is_dir()]
    fail = 0
    for f in tbs:
        top = "mk" + f.stem
        path = ":".join([str(pkg.root / "tb")] + srcs + ["+"])
        ok, log = sim(top, f, path, out / "b")
        last = log.strip().splitlines()[-1] if log.strip() else "没有输出"
        print(f"  {'✔' if ok else BOLD + '✘' + OFF} {top}  {last}")
        if not ok:
            fail += 1
    print(f"\n{len(tbs)} 份，{fail} 份不过")
    return 1 if fail else 0


def cmd_test(args) -> int:
    """把一个 IP 在整张矩阵上验一遍：调度门禁、寄存器一致性、各仓自己的行为测试。"""
    index = _index(_search(args))
    if args.top not in index:
        raise Bad(f"找不到包 {args.top}")
    pkg = index[args.top]
    if pkg.is_library:
        return _lib_test(args, index, pkg)
    if pkg.is_assembly:
        return _asm_gate(args, index, pkg)

    out = pathlib.Path(args.out or "test").resolve()
    if out.exists() and args.clean:
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

    problems = _flat_param(pkg) + _uncosted(pkg) + _dead_inputs(pkg)
    if pkg.regmap:
        problems += _unused_methods(pkg, out / "bsv" / f"{cap}Regs.bsv")
    for q in problems:
        print(f"  {BOLD}✘{OFF} {q}")
    if problems:
        return 1

    pts = points(pkg)
    if args.point:
        pts = [x for x in pts if x[0] == args.point]
        if not pts:
            raise Bad(f"矩阵里没有叫 {args.point} 的点")
    print(f"{BOLD}{pkg.name}{OFF}  矩阵 {len(pts)} 点")

    rows, failed, seen, tbseen = [], 0, {}, {}
    for lbl, ov, hand in pts:
        try:
            vals = resolve_pkg(pkg, {}, f"{pkg.path} (default)", None, ov)
        except Bad as ex:
            # 派生出来的点撞上约束是意料之中；手写的点撞上就是人写错了
            if hand:
                raise
            rows.append((lbl, "略", f"约束不允许：{ex}"))
            continue
        key = tuple(sorted((k, repr(v.value)) for k, v in vals.items()))
        if key in seen:
            rows.append((lbl, "同", f"解析下来与 {seen[key]} 是同一点"))
            continue
        seen[key] = lbl

        knobs = {k: v.value for k, v in vals.items()}
        notes, bad, ran = [], False, 0

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
                                         else _first_err(log)))

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
                    notes.append("寄存器：" + _tail(o))

        if _tb_gens(pkg) and not args.no_self:
            d = out / "tb" / lbl
            err = _run_gens(pkg, d, lbl, knobs)
            if err:
                bad, _ = True, notes.append(err)
            else:
                dg = _digest(d)
                if dg in tbseen:
                    notes.append(f"行为测试与 {tbseen[dg]} 逐字节相同，不重跑")
                else:
                    tbseen[dg] = lbl
                    path = ":".join([str(out / "bsv"), str(d), *src]) + ":+"
                    for f in sorted(d.glob("*Tb.bsv")):
                        ok, o = sim(f"mk{f.stem}", f, path, work)
                        if not ok:
                            bad = True
                            notes.append(f"{f.stem}：" + _tail(o))

        if not ran:
            # 什么都没跑却报绿，比报红还糟——那是在骗人
            notes.append("这个包既没有实现也没有寄存器图，没有可跑的检查")
        failed += bad
        rows.append((lbl, "✘" if bad else "✔",
                     "；".join(notes) if notes else
                     " ".join(f"{k}={v}" for k, v in sorted(knobs.items()))))

    w = max(len(r[0]) for r in rows)
    for lbl, mark, note in rows:
        col = "" if mark in ("略", "同") else (BOLD if mark == "✘" else "")
        print(f"  {col}{mark}{OFF} {lbl:<{w}}  {DIM if mark in ('略', '同') else ''}"
              f"{note}{OFF}")
    ran = sum(1 for r in rows if r[1] in ("✔", "✘"))
    print()
    print(f"{ran} 点实测，{failed} 点不过")
    return 1 if failed else 0


def _first_err(log: str) -> str:
    for line in log.splitlines():
        if "Error" in line or "Warning" in line:
            return line.strip()[:160]
    return "编译没过"


def _tail(o: str) -> str:
    # 先挑失败那几行。只取末尾会把更早的失败截掉——一次跑出三条，
    # 报告里只剩最后一条，人就以为只错了那一处。
    lines = [x.strip() for x in o.strip().splitlines() if x.strip()]
    hits = [x for x in lines if x.startswith(("FAIL", "TIMEOUT", "Error"))]
    pick = hits[:3] if hits else lines[-2:]
    s = " / ".join(pick)
    if hits and len(hits) > 3:
        s += f"（另有 {len(hits) - 3} 条）"
    return s[:400] if lines else "没有输出"


# ---------------------------------------------------------------- main

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="xirang", description="XiRang / 息壤")
    ap.add_argument("-p", "--path", action="append", help="包搜索路径，可多次给")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("top")
        p.add_argument("-s", "--set", action="append", help="覆盖旋钮，如 -s numPins=8")

    c = sub.add_parser("config", help="computed 面板")
    common(c); c.add_argument("--why", help="单条旋钮的来历，如 gpio0.numPins")
    c.set_defaults(fn=cmd_config)

    t = sub.add_parser("tree", help="装配层次")
    common(t); t.set_defaults(fn=cmd_tree)

    wr = sub.add_parser("wrap", help="给叶子 IP 生成扁平端口顶层")
    common(wr); wr.add_argument("-o", "--out"); wr.set_defaults(fn=cmd_wrap)

    e = sub.add_parser("export", help="导出可再导入的完整配置")
    common(e)
    e.add_argument("-o", "--out")
    e.add_argument("-f", "--format",
                   choices=["resolved", "core", "kconfig", "tar"],
                   help="导出目标。别人的格式一律是导出目标，不在执行路径上")
    e.add_argument("--build", help="tar 与 core 要读的生成物目录")
    e.set_defaults(fn=cmd_export)

    ge = sub.add_parser("gen", help="只生成寄存器组与 C 头")
    common(ge); ge.add_argument("-o", "--out")
    ge.add_argument("--tb", action="store_true",
                    help="连寄存器一致性测试一起生成")
    ge.set_defaults(fn=cmd_gen)

    lk = sub.add_parser("lock", help="解析依赖并钉住")
    common(lk); lk.add_argument("-o", "--out"); lk.set_defaults(fn=cmd_lock)

    li = sub.add_parser("lint", help="检查依赖与锁文件")
    common(li); li.set_defaults(fn=cmd_lint)

    ts = sub.add_parser("test", help="把 IP 在整张测试矩阵上验一遍")
    common(ts)
    ts.add_argument("-o", "--out")
    ts.add_argument("--point", help="只跑矩阵里的某一个点")
    ts.add_argument("--no-self", action="store_true", help="跳过各仓自己的行为测试")
    ts.add_argument("--clean", action="store_true", help="先清掉输出目录")
    ts.set_defaults(fn=cmd_test)

    b = sub.add_parser("recal", help="重测价目表并回填")
    b.add_argument("top")
    b.add_argument("--apply", action="store_true", help="真的写回 ip.yaml")
    b.set_defaults(fn=cmd_recal)

    b = sub.add_parser("build", help="生成并综合")
    common(b)
    b.add_argument("-o", "--out")
    b.add_argument("--config", help="从导出的配置构建，用于 round-trip 判据")
    b.add_argument("--no-synth", action="store_true")
    b.add_argument("--bsv-path", action="append", help="额外的 BSV 源目录")
    b.add_argument("--neutral", action="store_true",
                   help="综合中立顶层（不含总线绑定器）——价目表量的就是这一层")
    b.add_argument("--locked", action="store_true",
                   help="要求锁文件与当前源码一致，不一致即失败")
    b.set_defaults(fn=cmd_build)

    args = ap.parse_args(argv)
    try:
        return args.fn(args)
    except Bad as ex:
        print(f"xirang: {ex}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
