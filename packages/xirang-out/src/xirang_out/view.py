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
        if e.get("kind") != "bsv":
            continue
        for p in e.get("pins") or []:
            t = p.get("type", "")
            manager = t in MANAGER_TYPES
            out.append((p.get("name", "pins"),
                        "txn/manager" if manager else f"phys/{t}",
                        "接进交换网" if manager else "↗ 顶层"))
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
    L += [rule, f"实例 {n} · 引到顶层的物理端点 {loose} · 合计 {res.area_um2:,.2f} µm²"]
    return "\n".join(L)


def as_json(res: Resolved, pkgs: dict[str, Pkg]) -> str:
    doc = {"top": res.top, "bus": res.bus, "area_um2": res.area_um2, "instances": []}
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


VIEWS = {"text": summary, "json": as_json, "mermaid": as_mermaid}
