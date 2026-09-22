"""XiRang / 息壤 —— 硬件的包管理器与装配器。

长命令 xirang，短命令 ran。
"""
import argparse
import json
import pathlib
import sys

import yaml

from xirang_gen import foreign
from xirang_back import tools, verilog
from xirang_core import diag
from xirang_core.manifest import GEN_HW, GEN_SW
from xirang_area import check as area_check
from xirang_area.price import ASM_BAND, annotate, model_note, stale
from xirang_area.recal import init as do_init
from xirang_area.recal import recal as do_recal
from xirang_back.ecc import synth
import xirang_out.export  # noqa: F401  让所有导出目标完成注册
from xirang_out.target import TARGETS, Ctx, check, listing, pick

from xirang.new import cmd_new
from xirang_out import kconf
from xirang_out import view
from xirang_out.doc import from_doc, to_doc
from xirang_ws import find
from xirang_ws import manifest as wsman
from xirang_flow import gate, matrix
from xirang_flow.leaf import build as build_leaf
from xirang_flow.report import Mark
from xirang_core.lock import (check_submodules, make_lock, resolve_deps,
                              verify_lock, write_lock)
from xirang_core.manifest import Bad, Pkg
from xirang_core.model import LAYERS, Resolved
from xirang_core.resolve import resolve, resolve_pkg
from xirang_gen.assemble import addr_map, assemble
from xirang_gen.regmap import generate as gen_regmap
from xirang_gen.tb import regs_tb
from xirang_gen.wrap import flat_emit, wrap

BOLD, DIM, OFF = "\033[1m", "\033[2m", "\033[0m"


def _resolve(args) -> tuple[Resolved, dict[str, Pkg]]:
    search = find.roots(args.path)
    cli = {}
    for kv in args.set or []:
        k, _, v = kv.partition("=")
        cli[k] = yaml.safe_load(v)
    ws = wsman.find(search)
    res = resolve(args.top, search, cli=cli, ws=ws)
    pkgs = find.load_all(search)
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


def _why_check(res: Resolved, pkgs, code: str) -> int:
    """一道检查此刻是哪一级，以及这一级从哪来。与旋钮取值同一个面板。"""
    c = diag.CHECKS[code]
    layers = [("包 ip.yaml", (pkgs[res.top].ip.get("diagnostics") if res.top in pkgs else None))]
    lv, why = diag.resolve(code, layers)
    print(f"{BOLD}{code}{OFF} = {BOLD}{lv.name}{OFF}"
          f"   {'挡住动作' if diag.blocks(lv) else '只报不挡'}")
    print()
    print(f"  拦下什么：{c.what}")
    print()
    print("  层叠（低到高，越靠下越优先）：")
    print(f"    {'✔ ' if why == '默认' else '  '}默认        {c.level.name}")
    for src, over in layers:
        got = diag.level_of((over or {}).get(code))
        mark = "✔ " if (got is not None and why == src) else "  "
        print(f"    {mark}{src:<11} {got.name if got is not None else '—'}")
    print()
    print(f"  {DIM}工作区、装配与单条链接三层还没接上{OFF}")
    return 0


def _why(res: Resolved, pkgs, path: str) -> int:
    """CSS 那种 computed 面板：一条属性的完整来历。"""
    if path.upper() in diag.CHECKS:
        return _why_check(res, pkgs, path.upper())
    hit = res.find(path)
    if not hit:
        print(f"没有 {path}——旋钮写 gpio0.numPins，检查号写 XR-AREA-003", file=sys.stderr)
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

def _top_pkg(args, index) -> Pkg:
    if args.top not in index:
        raise Bad(f"找不到包 {args.top}")
    return index[args.top]


def cmd_lock(args) -> int:
    search = find.roots(args.path)
    index = find.index(search)
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
    search = find.roots(args.path)
    index = find.index(search)
    top = _top_pkg(args, index)
    resolved = resolve_deps(top, index)
    problems = check_submodules(search[0], resolved)
    problems += area_check.outdated(top) + area_check.unmeasured(top)
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

def cmd_doctor(args) -> int:
    """外部工具在不在、哪个版本。价目表的口径钉的就是这些版本。"""
    rows = [(n, v, w) for n, v, w in tools.survey()]
    print(view.table(("工具", "版本", "拿它做什么"), rows))
    miss = [n for n, v, _ in rows if v == "缺"]
    print()
    if miss:
        print(f"缺 {len(miss)} 个：{' '.join(miss)}")
        print(f"{DIM}缺的那几样只影响用到它们的那一步，别的照跑{OFF}")
    else:
        print("都在")
    return 0


def cmd_list(args) -> int:
    """搜索路径上有哪些包。像 pip list 那样先看见，才谈得上用。"""
    _, ws, idx = find.open_(args.path)
    rows = view.catalog(idx, args.kind)
    if not rows:
        print("没有包")
        return 0
    print(view.table(("名字", "版本", "类别", "成熟度", "旋钮", "契约", "目标"), rows))
    print()
    print(f"{len(rows)} 个包" + (f"，工作区 {ws.path.name}" if ws else "，没有工作区清单"))
    return 0


def cmd_inspect(args) -> int:
    """把一颗解出来的芯片打印出来。打印的是解出来的那份，不是清单。"""
    res, pkgs = _resolve(args)
    print(view.VIEWS[args.format](res, pkgs))
    return 0


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
    index = find.index(find.roots(args.path))
    if args.top not in index:
        raise Bad(f"找不到包 {args.top}")
    pkg = index[args.top]
    if not pkg.regmap:
        raise Bad(f"{args.top} 没有 regmap.yaml，没什么可生成的")
    out = pathlib.Path(args.out or "gen")
    (out / GEN_HW).mkdir(parents=True, exist_ok=True)
    (out / "sw").mkdir(parents=True, exist_ok=True)
    gen_regmap(pkg, out / GEN_HW, out / GEN_SW)
    if getattr(args, "tb", False):
        cli = {}
        for kv in args.set or []:
            k, _, v = kv.partition("=")
            cli[k] = yaml.safe_load(v)
        vals = resolve_pkg(pkg, {}, f"{pkg.path} (default)", None, cli)
        cap = (pkg.regmap.get("ip", pkg.name))[:1].upper()             + (pkg.regmap.get("ip", pkg.name))[1:]
        txt = regs_tb(pkg, vals)
        if txt:
            (out / GEN_HW / f"{cap}RegsTb.bsv").write_text(txt, encoding="utf-8")
        else:
            print("  （寄存器全是数组或宽寄存器，本版的一致性测试测不了）")
    for p in sorted((out / GEN_HW).glob("*.bsv")) + sorted((out / GEN_SW).glob("*")):
        print(f"  {p}")
    return 0


def cmd_wrap(args) -> int:
    """给一个叶子 IP 生成扁平端口顶层。装配没有这一层——它本身就是顶层。"""
    search = find.roots(args.path)
    index = find.index(search)
    if args.top not in index:
        raise Bad(f"找不到包 {args.top}")
    pkg = index[args.top]
    if pkg.is_assembly:
        raise Bad(f"{args.top} 是装配，它本身就是顶层，不需要 wrap")
    if pkg.is_library:
        raise Bad(f"{args.top} 是库包，不会被例化，也就没有端口")

    cli = {}
    for kv in args.set or []:
        k, _, v = kv.partition("=")
        cli[k] = yaml.safe_load(v)

    # 黑盒：源码是别人的，我们不生成顶层，只把参数按解出来的配置展开掉。
    # ecc 与 yosys-sta 不接受从外面传参数，不展开就等于拿上游默认值去量面积。
    if (fe := pkg.foreign_emit()) is not None:
        vals = resolve_pkg(pkg, {}, f"{pkg.path} (default)", None, cli)
        out = pathlib.Path(args.out or (pkg.root / "wrap"))
        got = verilog.elaborate(foreign.files(pkg), fe["top"],
                                foreign.bake(pkg, vals),
                                out / f"{fe['top']}.v")
        print(f"{got}")
        baked = ", ".join(f"{k}={v}" for k, v in sorted(foreign.bake(pkg, vals).items()))
        print(f"  {DIM}{fe['top']}  参数已展开：{baked}{OFF}")
        return 0

    if flat_emit(pkg) is None:
        raise Bad(f"{args.top} 的 emit 里没有 verilog-flat——"
                  f"想要独立可流片就把它加上，并选一种 bus")

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

def cmd_export(args) -> int:
    """导出：查表 -> 校验 needs -> 渲染 -> 写。加格式不必动这里。"""
    if args.list:
        print("导出目标：")
        print(listing())
        return 0

    if not args.top:
        raise Bad("XR-EXP-005 要导哪个包？给个名字，或者用 --list 看有哪些目标")
    t = pick(args.format)
    res, pkgs = _resolve(args)
    ctx = Ctx(res=res, pkgs=pkgs,
              build=pathlib.Path(args.build or "build").resolve(),
              out=pathlib.Path(args.out) if args.out else None)
    check(t, ctx)

    data = t.render(ctx)
    out = ctx.out or (pathlib.Path(f"{res.top}{t.ext}")
                      if isinstance(data, bytes) else None)
    if out is None:
        sys.stdout.write(data)
    else:
        out.write_bytes(data if isinstance(data, bytes) else data.encode())
        print(out)
    return 0



# ---------------------------------------------------------------- build





def cmd_recal(args) -> int:
    """价目表回填：重测记着的每一种配置，再重算曲线。

    改了生成器或 IP 源码之后价目表就失效了（`xirang lint` 会报）。这条命令是
    组织级 CI 那一步的实现，本地也能跑。默认只看不写，`--apply` 才落盘。
    """
    idx = find.index(find.roots(args.path))
    if args.top not in idx:
        raise Bad(f"找不到包 {args.top}")
    pkg = idx[args.top]
    print(f"{BOLD}{pkg.name}{OFF}  {'第一次量价目表' if args.init else '回填价目表'}"
          f"{'' if args.apply else '（只看不写，加 --apply 才落盘）'}")
    run = do_init if args.init else do_recal
    for line in run(pkg, find.roots(args.path), args.apply):
        if not line.startswith("{"):
            print(f"  {line}")
    if args.apply:
        w = stale(idx[args.top].__class__(pkg.root))
        print("  摘要已盖" if not w else f"  {w}")
    return 0


def cmd_build(args) -> int:
    search = find.roots(args.path)
    doc = kdot = None
    if args.config:
        txt = pathlib.Path(args.config).read_text(encoding="utf-8")
        try:
            d = yaml.safe_load(txt)
        except yaml.YAMLError:
            d = None
        # 我们自己导出的那份带 xirang: 1；其余当作 menuconfig 存下来的 .config。
        # 后者不带 top，所以 top 仍从命令行来。
        if isinstance(d, dict) and d.get("xirang") == 1:
            doc, args.top = d, d["top"]
        elif "CONFIG_" in txt or txt.lstrip().startswith("#"):
            kdot = args.config
        else:
            raise Bad(f"{args.config} 既不是 xirang 导出的配置，也不像 .config")
    # 叶子那条路两条入口共用。分开写过一次，结果是同一份配置直接 build 出
    # `GpioBare.bsv`、按导出的配置 build 出 `GpioPkg.bsv`——V4 当场抓到。
    idx = find.index(search)
    if args.top in idx and not idx[args.top].is_assembly:
        if doc is not None:
            one = (doc.get("instances") or [{}])[0]
            args.set = list(args.set or []) + [
                f"{k}={yaml.safe_dump(v).strip()}"
                for k, v in (one.get("with") or {}).items()]
        elif kdot:
            r0, p0 = _resolve(args)
            args.set = list(args.set or []) + [
                f"{k}={yaml.safe_dump(v.value).strip()}" for k, v
                in kconf.read(kdot, r0, p0).instances[0].values.items()]
        over = {}
        for kv in args.set or []:
            k, _, v = kv.partition("=")
            over[k] = yaml.safe_load(v)
        return _print_leaf(build_leaf(
            idx[args.top], idx,
            out=pathlib.Path(args.out or "build").resolve(),
            overrides=over, bare=getattr(args, "neutral", False),
            synthesise=not args.no_synth, extra_src=args.bsv_path or []))
    if doc is not None:
        res = from_doc(doc, find.load_all(search))
        pkgs = find.load_all(search)
        annotate(res, pkgs)
    elif kdot:
        # 先按默认解析出整棵树，再把 .config 里改过的贴回去
        res, pkgs = _resolve(args)
        res = kconf.read(kdot, res, pkgs)
        annotate(res, pkgs)
    else:
        res, pkgs = _resolve(args)

    if getattr(args, "locked", False):
        index = find.index(search)
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
    (out / GEN_HW).mkdir(parents=True, exist_ok=True)
    (out / "sw").mkdir(parents=True, exist_ok=True)

    # 1 每个用到的包，从 regmap.yaml 生成寄存器组
    for name in sorted({i.of for _, i in res.walk()}):
        p = pkgs[name]
        if p.regmap:
            gen_regmap(p, out / GEN_HW, out / GEN_SW)

    # 2 装配出顶层
    top_mod = "".join(w.capitalize() for w in res.top.replace("-", "_").split("_"))
    (out / GEN_HW / f"{top_mod}Pkg.bsv").write_text(
        assemble(res, pkgs, top_mod), encoding="utf-8")

    # 3 地址图与解析结果落盘
    (out / "regmap.json").write_text(
        json.dumps(to_doc(res), indent=2, ensure_ascii=False), encoding="utf-8")
    (out / "addr_map.md").write_text(addr_map(res), encoding="utf-8")

    print(f"生成于 {out}")
    print(f"  顶层 mk{top_mod}   预测面积 {res.area_um2:,.2f} µm²")

    if args.no_synth:
        print("  (--no-synth，跳过综合)")
        return 0
    # 每个用到的包各自的 hwsrc/ 都进 bsc 搜索路径
    src = [str(d) for n in sorted({i.of for _, i in res.walk()})
           for d in pkgs[n].dirs("hwsrc")]
    for p in pkgs.values():                      # 依赖包（hwcore 之类）也带上
        for d in p.dirs("hwsrc"):
            if str(d) not in src:
                src.append(str(d))
    got = synth(out, f"mk{top_mod}", res.top,
                extra_src=src + (args.bsv_path or []), gen=GEN_HW)
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











def cmd_test(args) -> int:
    """把一个包验一遍。库包跑行为测试，装配过调度门禁，叶子走整张矩阵。"""
    index = find.index(find.roots(args.path))
    if args.top not in index:
        raise Bad(f"找不到包 {args.top}")
    pkg = index[args.top]
    out = pathlib.Path(args.out or "test").resolve()

    if pkg.is_library:
        rep = gate.library(pkg, index, out=out, clean=args.clean)
        print(f"{BOLD}{pkg.name}{OFF}  库包，{len(rep.rows)} 份行为测试")
        if not rep.rows:
            print("  没有 htest/，只做类型检查")
            return 0
        for r in rep.rows:
            print(f"  {r.mark if r.mark is Mark.ok else BOLD + r.mark + OFF}"
                  f" {r.label}  {r.note}")
        print(f"\n{len(rep.rows)} 份，{rep.failed} 份不过")
        return rep.rc

    if pkg.is_assembly:
        rep = gate.assembly(pkg, index, find.roots(args.path),
                            out=out, clean=args.clean)
        print(f"{BOLD}{pkg.name}{OFF}  装配调度门禁与自检（默认那一点）")
        if rep.ok:
            print(f"  ✔ {rep.top}")
            for r in rep.rows:
                print(f"  {r.mark if r.mark is Mark.ok else BOLD + r.mark + OFF}"
                      f" {r.label}  {r.note}")
            return rep.rc
        print(f"  {BOLD}✘{OFF} {rep.top}  "
              f"{' '.join(rep.hits) if rep.hits else '编译失败'}")
        for ln in rep.lines:
            print(f"      {ln}")
        return rep.rc

    rep = matrix.run(pkg, index, out=out, clean=args.clean,
                     point=args.point, self_tb=not args.no_self)
    for q in rep.problems:
        print(f"  {BOLD}✘{OFF} {q}")
    if rep.problems:
        return 1
    print(f"{BOLD}{pkg.name}{OFF}  矩阵 {rep.points} 点")
    w = max(len(r.label) for r in rep.rows)
    for r in rep.rows:
        quiet = not r.mark.counted
        col = "" if quiet else (BOLD if r.mark is Mark.bad else "")
        print(f"  {col}{r.mark}{OFF} {r.label:<{w}}  "
              f"{DIM if quiet else ''}{r.note}{OFF}")
    print()
    for q in rep.notes:
        print(f"  {DIM}· {q}{OFF}")
    print()
    print(f"{rep.ran} 点实测，{rep.failed} 点不过")
    return rep.rc


def _print_leaf(rep) -> int:
    print(f"生成于 {rep.out}")
    if rep.want is None:
        print(f"  顶层 {rep.top}   还没有价目表，这次只测不比")
    else:
        print(f"  顶层 {rep.top}   预测面积 {rep.want:,.2f} µm²")
    if not rep.synthesised:
        print("  (--no-synth，跳过综合)")
        return 0
    if rep.got is None:
        print("  综合未跑通", file=sys.stderr)
        return 1
    if rep.want is None:
        print(f"  实测面积 {rep.got:,.2f} µm²")
        return 0
    print(f"  实测面积 {rep.got:,.2f} µm²   预测偏差 {rep.err_pct:+.2f}%")
    return 0






# ---------------------------------------------------------------- main

def cmd_status(args) -> int:
    """推之前该看的那一眼：清单、源码、锁三者对不对得上。

    今天推一半仓、已发布的状态不自洽，是因为没有任何一处会在推之前说话。
    """
    search = find.roots(args.path)
    ws = wsman.find(search)
    idx = find.index(search, ws)
    if ws:
        print(f"{BOLD}{ws.path}{OFF}  {len(idx)} 个包")
    problems = wsman.status(ws, idx)
    for p in problems:
        print(f"  {BOLD}✘{OFF} {p}")
    if not problems:
        print("  干净")
    return 1 if problems else 0


def main(argv=None) -> int:
    # 长命令 xirang、短命令 ran 是同一个入口，用哪个名字调就报哪个名字
    ap = argparse.ArgumentParser(prog=pathlib.Path(sys.argv[0]).name or "xirang",
                                 description="XiRang / 息壤")
    ap.add_argument("-p", "--path", action="append", help="包搜索路径，可多次给")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("top")
        p.add_argument("-s", "--set", action="append", help="覆盖旋钮，如 -s numPins=8")

    c = sub.add_parser("config", help="computed 面板")
    common(c); c.add_argument("--why", help="一条旋钮或一道检查的来历，如 gpio0.numPins、XR-AREA-003")
    c.set_defaults(fn=cmd_config)

    doc = sub.add_parser("doctor", help="外部工具在不在、哪个版本")
    doc.set_defaults(fn=cmd_doctor)

    ls = sub.add_parser("list", help="搜索路径上有哪些包")
    ls.add_argument("-k", "--kind", choices=["ip", "lib", "asm"])
    ls.set_defaults(fn=cmd_list)

    ins = sub.add_parser("inspect", help="打印实例、旋钮、端点、地址与面积")
    common(ins)
    ins.add_argument("-f", "--format", choices=list(view.VIEWS), default="text")
    ins.set_defaults(fn=cmd_inspect)

    t = sub.add_parser("tree", help="装配层次")
    common(t); t.set_defaults(fn=cmd_tree)

    wr = sub.add_parser("wrap", help="给叶子 IP 生成扁平端口顶层")
    common(wr); wr.add_argument("-o", "--out"); wr.set_defaults(fn=cmd_wrap)

    n = sub.add_parser("new", help="从模板铺一个新仓")

    n.add_argument("name", nargs="?", help="包名，省略则交互问")

    n.add_argument("-t", "--template", help="模板，如 ip/regmap")

    n.add_argument("--dir", help="铺到哪，默认当前目录下的同名目录")

    n.add_argument("--list", action="store_true", help="只列模板")

    n.add_argument("--vcs", default="git", choices=["git", "svn", "none"],

                   help="初始化哪种版本库，默认 git")

    n.set_defaults(fn=cmd_new)


    e = sub.add_parser("export", help="导出可再导入的完整配置")
    # --list 只是列表，不该逼人先给一个包名
    e.add_argument("top", nargs="?")
    e.add_argument("-s", "--set", action="append",
                   help="覆盖旋钮，如 -s numPins=8")
    e.add_argument("-o", "--out")
    e.add_argument("-f", "--format",
                   choices=sorted(TARGETS),
                   help="导出目标。别人的格式一律是导出目标，不在执行路径上")
    e.add_argument("--build", help="tar 与 core 要读的生成物目录")
    e.add_argument("--list", action="store_true", help="只列导出目标")
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

    st = sub.add_parser("status", help="工作区：清单、源码、锁对不对得上")
    st.set_defaults(fn=cmd_status)

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
    b.add_argument("--init", action="store_true",
                   help="没有价目表的新包：按量程两端与默认值生成实测行，量完用离格点定余量")
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
