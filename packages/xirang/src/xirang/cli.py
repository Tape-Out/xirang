"""XiRang / 息壤 —— 硬件的包管理器与装配器。

长命令 xirang，短命令 ran。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

import yaml

from xirang_area.price import annotate, model_note
from xirang_back.ecc import synth
from xirang_core.manifest import Bad, Pkg
from xirang_core.model import LAYERS, Resolved
from xirang_core.resolve import resolve
from xirang_gen.assemble import addr_map, assemble
from xirang_gen.regmap import generate as gen_regmap

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
    res, _ = _resolve(args)
    doc = _to_doc(res)
    txt = yaml.safe_dump(doc, sort_keys=False, allow_unicode=True)
    if args.out:
        pathlib.Path(args.out).write_text(txt, encoding="utf-8")
        print(args.out)
    else:
        sys.stdout.write(txt)
    return 0


# ---------------------------------------------------------------- build

def cmd_build(args) -> int:
    search = _search(args)
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

    e = sub.add_parser("export", help="导出可再导入的完整配置")
    common(e); e.add_argument("-o", "--out"); e.set_defaults(fn=cmd_export)

    b = sub.add_parser("build", help="生成并综合")
    common(b)
    b.add_argument("-o", "--out")
    b.add_argument("--config", help="从导出的配置构建，用于 round-trip 判据")
    b.add_argument("--no-synth", action="store_true")
    b.add_argument("--bsv-path", action="append", help="额外的 BSV 源目录")
    b.set_defaults(fn=cmd_build)

    args = ap.parse_args(argv)
    try:
        return args.fn(args)
    except Bad as ex:
        print(f"xirang: {ex}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
