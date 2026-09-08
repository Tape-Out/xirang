"""装配：由 instances 段生成顶层 BSV。

判据是装配包里**零自有 RTL**——生成器只要还需要一句手写胶水，就说明 schema
缺字段，该补的是字段，不是把胶水塞进仓里。

译码用 `hwcore` 的 `mkFabric`（编译期完全展开），总线绑定用 `amba` 的
`mkApb4Bind`，**整颗 SoC 只有一个绑定器**，而不是每个 IP 各带一个。
"""
from __future__ import annotations

from xirang_core.manifest import Bad, Pkg
from xirang_core.model import Resolved
from xirang_gen.wrap import BUSES, sub_targs


def _lit(v) -> str:
    if isinstance(v, bool):
        return "True" if v else "False"
    if isinstance(v, int):
        return str(v)
    return f'"{v}"'


def assemble(res: Resolved, pkgs: dict[str, Pkg], top_module: str) -> str:
    if res.bus not in BUSES:
        raise Bad(f"本版装配只支持 {sorted(BUSES)}，收到 {res.bus}")
    bus = BUSES[res.bus]
    root = pkgs[res.top]
    ctrl = (root.ip.get("contract") or {}).get("ctrl") or {}
    aw, dw = ctrl.get("aw", 32), ctrl.get("dw", 32)
    hexw = (aw + 3) // 4

    imports, decls, devs, pins_if, pins_impl, irq_if, irq_impl = [], [], [], [], [], [], []
    seen = set()

    for k, inst in enumerate(res.instances):
        p = pkgs[inst.of]
        e = p.bsv_emit()
        if e["package"] not in seen:
            imports.append(f"import {e['package']}::*;")
            seen.add(e["package"])
        knobs = p.knobs()
        args = ", ".join(f"{n}: {_lit(inst.values[n].value)}"
                         for n in knobs if knobs[n]["kind"] == "feature")
        nums = [str(inst.values[n].value) for n in knobs if knobs[n]["kind"] == "param"]
        # IP 按它自己声明的位宽例化——价目表就是照这个宽度量的。
        # 撑到片上的 32 位会多出一截地址比较，账就对不上了。
        ic = (p.ip.get("contract") or {}).get("ctrl") or {}
        iaw, idw = ic.get("aw", 8), ic.get("dw", 32)
        if idw != dw:
            raise Bad(f"{inst.name} 的数据宽 {idw} 与片上 {dw} 不一致，本版不做宽度转换")
        if iaw > aw:
            raise Bad(f"{inst.name} 的地址宽 {iaw} 比片上 {aw} 还宽")
        decls.append(f"  {e['interface']}#({', '.join([str(iaw), str(idw)] + nums)}) "
                     f"{inst.name} <- {e['module']}({e['config_type']} {{ {args} }});")

        if inst.addr is None:
            raise Bad(f"{inst.name} 没有地址，且它的包没有 regmap.yaml 的 base")
        span = inst.size or (1 << iaw)
        ip_irqs = (p.ip.get("contract") or {}).get("irq", []) or []
        # Device 只带一根线。多根的 IP（每核一根、每通道一根）在这里合成一根送译码，
        # 完整的向量另行引到顶层，交给 PLIC 逐根接。
        if not ip_irqs:
            one = "tagged Invalid"
        elif ip_irqs[0].get("width"):
            one = f"tagged Valid ({inst.name}.{ip_irqs[0]['name']} != 0)"
        else:
            one = f"tagged Valid {inst.name}.{ip_irqs[0]['name']}"
        devs.append(f"  devs[{k}] = device({aw}'h{inst.addr:0{hexw}X}, "
                    f"{aw}'h{span:0{hexw}X}, "
                    f"narrow({inst.name}.{e.get('ctrl', 'regs')}), {one});")

        for s in e.get("pins") or []:
            nm = f"{inst.name}_{s['name']}"
            pins_if.append(f"  interface {s['type']}"
                           f"{sub_targs(s, inst.values, p.name)} {nm};")
            pins_impl.append(f"  interface {nm} = {inst.name}.{s['name']};")

    n = len(res.instances)
    if not n:
        raise Bad(f"{res.top} 的 instances 是空的")
    irq_if.append(f"  (* always_ready, result = \"irqs\" *) method Bit#({n}) irqs;")
    irq_impl.append("  method Bit#(%d) irqs = pack(irqsOf(devs));" % n)

    L = [
        f"package {top_module}Pkg;",
        "",
        "// 由 ip.yaml 的 instances 段生成，勿手改。这个包里没有一行自有 RTL。",
        "",
        "import Vector::*;",
        "import RegIf::*;",
        "import Fabric::*;",
        f"import {bus['pkg']}::*;",
        *imports,
        "",
        f"interface {top_module}Ifc;",
        f"  interface {bus['pins']}#({aw}, {dw}) bus;",
        *pins_if,
        *irq_if,
        "endinterface",
        "",
        "(* synthesize *)",
        '(* default_clock_osc = "clk", default_reset = "rst_n" *)',
        f"module mk{top_module}({top_module}Ifc);",
        "",
        *decls,
        "",
        f"  Vector#({n}, Device#({aw}, {dw})) devs = newVector;",
        *devs,
        "",
        f"  RegIf#({aw}, {dw}) fab <- mkFabric(devs);",
        f"  {bus['pins']}#({aw}, {dw}) sl <- {bus['bind']}(fab);",
        "",
        "  interface bus = sl;",
        *pins_impl,
        *irq_impl,
        "endmodule",
        "",
        "endpackage",
    ]
    return "\n".join(L) + "\n"


def addr_map(res: Resolved) -> str:
    L = ["| 实例 | 包 | 基地址 | 大小 |", "|:--|:--|--:|--:|"]
    for _, i in res.walk():
        a = f"{i.addr:#010x}" if i.addr is not None else "-"
        s = f"{i.size:#x}" if i.size is not None else "-"
        L.append(f"| `{i.name}` | `{i.of}` | {a} | {s} |")
    return "\n".join(L)
