"""把一颗解出来的芯片打印出来：实例、旋钮、端点、地址、面积，以及它们怎么连。

打印的是**解出来的那一份**，不是清单。清单说「省略则用默认」，这里显示最终值；
面积从价目表取。端点一律标出 `种类/角色`——打印不出来就说明清单没把它说清楚。
"""
import json
import unicodedata

from xirang_core.manifest import Pkg
from xirang_core.model import Resolved

# 引脚里这些类型是发起口，不引到顶层，接进交换网
MANAGER_TYPES = {"RegManager", "MemManager"}


def _w(s) -> int:
    """中日韩字符占两格。按字符数排版会错位，中文表头尤其明显。"""
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in str(s))


def _pad(s, n: int) -> str:
    return f"{s}{' ' * max(0, n - _w(s))}"


def _lpad(s, n: int) -> str:
    return f"{' ' * max(0, n - _w(s))}{s}"


def _knobs(inst) -> str:
    def lit(v):
        # Python 里 1 == True，用字典查会把 csWidth=1 显示成 T
        return {True: "T", False: "F"}[v] if isinstance(v, bool) else v
    return " ".join(f"{k}={lit(v.value)}" for k, v in inst.values.items())


def endpoints(pkg: Pkg) -> list[tuple[str, str, str]]:
    """(名字, 种类/角色, 去哪)。今天的清单只说得出这些，说不出的就别编。"""
    out: list[tuple[str, str, str]] = []
    ctrl = (pkg.ip.get("contract") or {}).get("ctrl") or {}
    if ctrl.get("shape") and ctrl["shape"] != "none":
        out.append(("ctrl", "txn/target",
                    f"{ctrl.get('aw', '?')} 位地址 {ctrl.get('dw', '?')} 位数据"))
    for irq in (pkg.ip.get("contract") or {}).get("irq") or []:
        out.append((irq.get("name", "irq"), "event/source", irq.get("kind", "")))
    for e in pkg.ip.get("emit", []) or []:
        if e.get("kind") == "bsv":
            for p in e.get("pins") or []:
                t = p.get("type", "")
                manager = t in MANAGER_TYPES
                out.append((p.get("name", "pins"),
                            "txn/manager" if manager else f"phys/{t}",
                            "接进交换网" if manager else "↗ 顶层"))
        elif e.get("kind") == "foreign":
            # 黑盒的端点自己就带 kind 与 role，不必猜
            for p in e.get("ports") or []:
                k, role = p.get("kind", "?"), p.get("role")
                shape = f"{ {'transaction': 'txn'}.get(k, k)}/{role}" if role else k
                note = p.get("profile") or p.get("type") or ""
                if p.get("prefix"):
                    note += f"  前缀 {p['prefix']}"
                out.append((p.get("endpoint", "?"), shape, note))
    return out


def summary(res: Resolved, pkgs: dict[str, Pkg]) -> str:
    rows = []
    for _, inst in res.walk():
        pkg = pkgs.get(inst.of)
        eps = endpoints(pkg) if pkg else []
        addr = f"{inst.addr:#010x}" if inst.addr is not None else "—"
        rows.append((inst.name, inst.of, _knobs(inst), eps, addr, inst.area_um2))

    w0 = max([_w(r[0]) for r in rows] + [4])
    w1 = max([_w(r[1]) for r in rows] + [4])
    w2 = min(max([_w(r[2]) for r in rows] + [4]), 40)
    we = max([_w(e[0]) for r in rows for e in r[3]] + [4])

    n = len(rows)
    head = (f"{res.top}   {'装配' if n > 1 else '叶子'} · 总线 {res.bus}"
            f" · 实例 {n} · 合计 {res.area_um2:,.2f} µm²")
    cols = (f"{_pad('实例', w0)} {_pad('来自', w1)} {_pad('配置', w2)} "
            f"{_pad('端点', we)} {_pad('形态', 15)} {_pad('地址', 11)} "
            f"{_lpad('面积 µm²', 12)}  说明")
    rule = "─" * max(_w(head), _w(cols))
    L = [head, rule, cols]
    loose = 0
    for name, of, kn, eps, addr, area in rows:
        kn = kn if _w(kn) <= w2 else kn[:w2 - 1] + "…"
        first = eps[0] if eps else ("—", "", "")
        L.append(f"{_pad(name, w0)} {_pad(of, w1)} {_pad(kn, w2)} "
                 f"{_pad(first[0], we)} {_pad(first[1], 15)} {_pad(addr, 11)} "
                 f"{_lpad(f'{area:,.2f}', 12)}  {first[2]}")
        pad = " " * (w0 + w1 + w2 + 3)
        for e in eps[1:]:
            L.append(f"{pad}{_pad(e[0], we)} {_pad(e[1], 15)} {' ' * 25}  {e[2]}")
        loose += sum(1 for e in eps if e[1].startswith("phys/"))
    tail = f"实例 {n} · 引到顶层的物理端点 {loose} · 合计 {res.area_um2:,.2f} µm²"
    if res.unpriced:
        # 没量出来就说没量出来，还要点名——不点名的话，读者会把合计里别处来的
        # 数当成它的面积
        tail += "（" + "、".join(sorted(set(res.unpriced))) + " 没有价目表，未计入）"
    L += [rule, tail]
    return "\n".join(L)


def as_json(res: Resolved, pkgs: dict[str, Pkg]) -> str:
    top = pkgs.get(res.top)
    doc = {"top": res.top, "bus": res.bus, "area_um2": res.area_um2,
           "unpriced": sorted(set(res.unpriced)),
           # 构建目标在这里出，CI 就不必再 grep ip.yaml 猜
           "targets": top.targets() if top else {},
           "instances": []}
    for _, inst in res.walk():
        pkg = pkgs.get(inst.of)
        doc["instances"].append({
            "name": inst.name, "of": inst.of,
            "addr": inst.addr, "area_um2": inst.area_um2,
            "knobs": {k: v.value for k, v in inst.values.items()},
            "endpoints": [{"name": a, "shape": b, "note": c}
                          for a, b, c in (endpoints(pkg) if pkg else [])],
        })
    return json.dumps(doc, ensure_ascii=False, indent=2)


def as_mermaid(res: Resolved, pkgs: dict[str, Pkg]) -> str:
    L = ["```mermaid", "graph LR"]
    bus = f"bus_{res.bus}".replace("-", "_")
    L.append(f'  {bus}["{res.bus}"]')
    for _, inst in res.walk():
        pkg = pkgs.get(inst.of)
        node = inst.name.replace("-", "_")
        L.append(f'  {node}["{inst.name}<br/>{inst.of}"]')
        for a, b, _ in (endpoints(pkg) if pkg else []):
            if b == "txn/target":
                L.append(f"  {bus} --> {node}")
            elif b == "txn/manager":
                L.append(f"  {node} --> {bus}")
            elif b.startswith("phys/"):
                L.append(f'  {node} --- {node}_{a}(["{a}"])')
    L.append("```")
    return "\n".join(L)


def deps(res: Resolved, pkgs: dict[str, Pkg]) -> str:
    """按依赖画树，不是按实例。

    `ran tree` 回答「这颗芯片里有哪些实例」，这里回答「要构建它得有哪些包」——
    同一个包被三个实例用到，那边出现三次，这边一次。
    """
    idx = dict(pkgs)
    seen: set[str] = set()
    L = []

    def walk(name: str, prefix: str, last: bool, top: bool):
        pk = idx.get(name)
        ver = pk.ip.get("version", "?") if pk else "缺"
        kind = ("装配" if pk and pk.is_assembly else
                "库" if pk and pk.is_library else "IP" if pk else "找不到")
        bar = "" if top else ("└─ " if last else "├─ ")
        again = " …" if name in seen else ""
        L.append(f"{prefix}{bar}{name}  {ver}  {kind}{again}")
        if name in seen or pk is None:
            return
        seen.add(name)
        kids = sorted(set((pk.ip.get("deps") or {}))
                      | {i["of"] for i in (pk.ip.get("instances") or [])})
        step = "" if top else ("   " if last else "│  ")
        for i, k in enumerate(kids):
            walk(k, prefix + step, i == len(kids) - 1, False)

    walk(res.top, "", True, True)
    L += ["", f"{len(seen)} 个包"]
    return chr(10).join(L)


def pins(res: Resolved, pkgs: dict[str, Pkg]) -> str:
    """只出物理端点：给封装与板级的人看的那一张。"""
    rows = []
    for _, inst in res.walk():
        pkg = pkgs.get(inst.of)
        for name, shape, note in (endpoints(pkg) if pkg else []):
            if shape.startswith("phys/") or shape == "physical":
                rows.append((inst.name, inst.of, name,
                             shape.removeprefix("phys/"), note))
    if not rows:
        return f"{res.top} 没有引到顶层的物理端点"
    return (table(("实例", "来自", "端点", "类型", "说明"), rows)
            + chr(10) * 2 + f"{len(rows)} 组物理端点")


def show(pk: Pkg, idx: dict[str, Pkg]) -> str:
    """一个包的详情。像 pip show：先看得清一个，再谈装配。"""
    ident = pk.ip.get("identity") or {}
    kind = "装配" if pk.is_assembly else ("库" if pk.is_library else "IP")
    L = [f"{pk.name}  {pk.ip.get('version', '?')}  {kind} · "
         f"成熟度 {ident.get('maturity', '—')}",
         f"  {ident.get('summary', '')}",
         f"  {pk.root}"]

    knobs = pk.knobs()
    if knobs:
        L.append(f"旋钮 {len(knobs)}")
        for k, spec in sorted(knobs.items()):
            dom = (f"[{', '.join(str(x) for x in spec['values'])}]"
                   if spec.get("values") else
                   f"{spec['range'][0]}..{spec['range'][1]}"
                   if spec.get("range") else spec.get("type", ""))
            L.append(f"  {_pad(k, 28)} {_pad(spec.get('type', ''), 8)} "
                     f"{_pad(dom, 40)} 默认 {spec.get('default', '—')}")

    if prof := (pk.ip.get("profiles") or {}):
        sets = prof.get("sets") or {}
        L.append(f"档位 {len(sets)}  由 {prof.get('by')} 选")
        for name, vals in sorted(sets.items()):
            L.append(f"  {_pad(name, 28)} 改 {len(vals)} 项默认值")

    if gs := pk.guards():
        L.append(f"守卫 {len(gs)}")
        for g in gs:
            cond = " 且 ".join(f"{k}={v}" for k, v in g["when"].items())
            for k, keep in g["narrow"].items():
                L.append(f"  {_pad(cond, 28)} → "
                         f"{_pad(k + ' 只能是 ' + str(keep), 40)} {g['why']}")

    eps = endpoints(pk)
    if eps:
        L.append(f"端点 {len(eps)}")
        for a, b, c in eps:
            L.append(f"  {_pad(a, 28)} {_pad(b, 16)} {c}")

    if pk.regmap:
        regs = (pk.regmap.get("registers") or pk.regmap.get("regs") or [])
        L.append(f"寄存器 {len(regs)} 个")

    src = pk.ip.get("deps") or {}
    if src:
        L.append("依赖")
        for name, spec in sorted(src.items()):
            how = (f"path {spec['path']}" if isinstance(spec, dict) and spec.get("path")
                   else f"git {spec['git']} @{spec['rev'][:12]}"
                   if isinstance(spec, dict) and spec.get("git") else "搜索路径")
            req = spec if isinstance(spec, str) else (
                spec.get("req") or spec.get("version") or "*")
            L.append(f"  {_pad(name, 28)} {_pad(str(req), 12)} {how}")

    users = sorted(n for n, other in idx.items()
                   if n != pk.name and (pk.name in (other.ip.get("deps") or {})
                                        or any(i["of"] == pk.name for i
                                               in (other.ip.get("instances") or []))))
    if users:
        L.append("被谁用   " + " ".join(users))

    area = pk.ip.get("area") or {}
    if not area:
        L.append("面积     未知（XR-AREA-006），不是零")
    else:
        base = area.get("base")
        # base 可以是一个数，也可以是一条按旋钮取值的曲线
        if isinstance(base, dict) and base.get("points"):
            pts = base["points"]
            head = (f"按 {base.get('per', '?')} 的曲线，{len(pts)} 个点"
                    f"（{min(pts.values()):,.0f}–{max(pts.values()):,.0f} µm²）")
        elif isinstance(base, (int, float)):
            head = f"基线 {float(base):,.2f} µm²"
        else:
            head = "有 area 段但没有基线"
        priced = len(area.get("knobs") or area.get("features") or {})
        L.append(f"面积     {head}"
                 + (f" · 旋钮计价 {priced} 项" if priced else "")
                 + (f" · 余量 {area['margin']:.1%}" if area.get("margin") else ""))

    tg = pk.targets()
    L.append("目标     " + " ".join(f"{k}→{(v or {}).get('driver', '?')}"
                                    for k, v in sorted(tg.items())))
    return chr(10).join(L)


VIEWS = {"text": summary, "json": as_json, "mermaid": as_mermaid,
         "deps": deps, "pins": pins}

KINDS = {"装配": "asm", "库": "lib", "IP": "ip"}


def catalog(idx: dict[str, Pkg], kind: str | None = None) -> list[tuple[str, ...]]:
    """搜索路径上有哪些包，一行一个。先看得见，才谈得上用。"""
    rows = []
    for name, pk in sorted(idx.items()):
        k = "装配" if pk.is_assembly else ("库" if pk.is_library else "IP")
        if kind and KINDS[k] != kind:
            continue
        ident = pk.ip.get("identity") or {}
        ctrl = (pk.ip.get("contract") or {}).get("ctrl") or {}
        sig = (f"{ctrl['aw']}/{ctrl['dw']}"
               if ctrl.get("shape") and ctrl["shape"] != "none" else "—")
        drv = "+".join(sorted({(v or {}).get("driver", "?")
                               for v in pk.targets().values()}))
        rows.append((name, str(pk.ip.get("version", "—")), k,
                     str(ident.get("maturity", "—")), str(len(pk.knobs())), sig, drv))
    return rows


def table(head: tuple[str, ...], rows: list[tuple[str, ...]]) -> str:
    """等宽表格：中文按两格算，列才对得齐。"""
    if not rows:
        return ""
    w = [max([_w(r[i]) for r in rows] + [_w(head[i])]) for i in range(len(head))]
    L = ["  ".join(_pad(h, x) for h, x in zip(head, w))]
    L += ["  ".join(_pad(c, x) for c, x in zip(r, w)) for r in rows]
    return "\n".join(L)
