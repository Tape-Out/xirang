"""XiRang / 息壤 —— 硬件的包管理器与装配器。

长命令 xirang，短命令 ran。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

import yaml

from xirang_area.price import annotate, model_note, price, stale
from xirang_back.ecc import synth
from xirang_back.export import to_core, to_kconfig, to_tar
from xirang_core.lock import (check_submodules, make_lock, resolve_deps,
                              verify_lock, write_lock)
from xirang_core.manifest import Bad, Pkg
from xirang_core.model import LAYERS, Resolved
from xirang_core.resolve import resolve, resolve_pkg
from xirang_gen.assemble import addr_map, assemble
from xirang_gen.regmap import generate as gen_regmap
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
    for name in sorted({i.of for _, i in res.walk()}):
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
    out = pathlib.Path(args.out or (root / "xirang.lock"))
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
    want, _ = price(pkg, vals)
    if not bare:
        # 扁平顶层比 IP 本体多一个总线绑定器，那笔钱记在实现它的包上
        want += _bus_price(pkg, _index(_search(args)))
    print(f"生成于 {out}")
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
    err = (got - want) / got * 100 if got else 0
    print(f"  实测面积 {got:,.2f} µm²   预测偏差 {err:+.2f}%")
    return 0


def cmd_build(args) -> int:
    search = _search(args)
    if not args.config:
        idx = _index(search)
        if args.top in idx and not idx[args.top].is_assembly:
            return _build_leaf(args, idx[args.top])
    if args.config:
        doc = yaml.safe_load(pathlib.Path(args.config).read_text(encoding="utf-8"))
        if doc.get("xirang") != 1:
            raise Bad(f"{args.config} 不是 xirang 导出的配置")
        args.top = doc["top"]
        res = _from_doc(doc, search)
        pkgs = _load_all(res, search)
        annotate(res, pkgs)
    else:
        res, pkgs = _resolve(args)

    if getattr(args, "locked", False):
        index = _index(search)
        problems = verify_lock(
            yaml.safe_load((search[0] / "xirang.lock").read_text(encoding="utf-8")),
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
    print(f"  实测面积 {got:,.2f} µm²   预测偏差 {err:+.2f}%"
          f"   {'✔ 预测偏保守' if res.area_um2 >= got else '✘ 预测低于实测，模型作废'}")
    return 0


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
        if p and p.regmap:
            if i.addr is None:
                i.addr = int(str(p.regmap.get("base")), 0)
            i.size = int(str(p.regmap.get("size")), 0)
    return res


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

    lk = sub.add_parser("lock", help="解析依赖并钉住")
    common(lk); lk.add_argument("-o", "--out"); lk.set_defaults(fn=cmd_lock)

    li = sub.add_parser("lint", help="检查依赖与锁文件")
    common(li); li.set_defaults(fn=cmd_lint)

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
