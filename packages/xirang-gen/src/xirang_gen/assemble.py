"""装配：由 instances 段生成顶层 BSV。

判据是装配包里**零自有 RTL**——生成器只要还需要一句手写胶水，就说明 schema
缺字段，该补的是字段，不是把胶水塞进仓里。
"""
from __future__ import annotations

from xirang_core.manifest import Bad, Pkg
from xirang_core.model import Resolved


def _bsv_bool(v) -> str:
    return "True" if v else "False"


def _lit(v) -> str:
    if isinstance(v, bool):
        return _bsv_bool(v)
    if isinstance(v, int):
        return str(v)
    return f'"{v}"'




def assemble(res: Resolved, pkgs: dict[str, Pkg], top_module: str) -> str:
    root = pkgs[res.top]
    if res.bus != "apb4":
        raise Bad(f"本版只生成 apb4 装配，收到 {res.bus}")

    imports, decls, wires, pins_if, pins_impl = [], [], [], [], []
    seen_pkgs = set()

    for inst in res.instances:
        p = pkgs[inst.of]
        e = p.bsv_emit()
        if e["package"] not in seen_pkgs:
            imports.append(f"import {e['package']}::*;")
            seen_pkgs.add(e["package"])
        knobs = p.knobs()
        args = ", ".join(f"{k}: {_lit(inst.values[k].value)}"
                         for k in knobs if knobs[k]["kind"] == "feature")
        # 数值旋钮走类型参数
        nums = [str(inst.values[k].value) for k in knobs if knobs[k]["kind"] == "param"]
        targs = ", ".join(["8", "32"] + nums)
        decls.append(f"  {e['interface']}#({targs}) {inst.name} <- "
                     f"{e['module']}({e['config_type']} {{ {args} }});")

        if inst.addr is None:
            raise Bad(f"{inst.name} 没有地址，且它的包没有 regmap.yaml 的 base")
        hi = inst.addr >> 8
        wires.append((inst.name, hi))
        pins_if.append(f"  interface GpioPins#({nums[0] if nums else '32'}) {inst.name}_pins;")
        pins_impl.append(f"  interface {inst.name}_pins = {inst.name}.pins;")

    sel = "\n".join(
        f"    Bool sel_{n} = psel && (paddr[31:8] == 24'h{h:06X});" for n, h in wires)
    fan = "\n".join(
        f"    {n}.apb.req(truncate(paddr), pprot, sel_{n}, penable, pwrite, pwdata, pstrb);"
        for n, _ in wires)
    rd = " |\n            ".join(
        f"(sel_r_{n} ? {n}.apb.prdata : 0)" for n, _ in wires)
    er = " || ".join(f"(sel_r_{n} && {n}.apb.pslverr)" for n, _ in wires)
    rdy = " && ".join(f"(!sel_r_{n} || {n}.apb.pready)" for n, _ in wires)
    selr = "\n".join(
        f"  Reg#(Bool) sel_r_{n} <- mkReg(False);" for n, _ in wires)
    selr_set = "\n".join(f"    sel_r_{n} <= sel_{n};" for n, _ in wires)

    L = [
        f"package {top_module}Pkg;",
        "",
        "// 由 ip.yaml 的 instances 段生成，勿手改。这个包里没有一行自有 RTL。",
        "",
        "import Apb4::*;",
        *imports,
        "",
        f"interface {top_module}Ifc;",
        "  interface Apb4SlavePins#(32, 32) apb;",
        *pins_if,
        "endinterface",
        "",
        "(* synthesize *)",
        '(* default_clock_osc = "clk", default_reset = "rst_n" *)',
        f"module mk{top_module}({top_module}Ifc);",
        "",
        *decls,
        "",
        "  // 译码结果打一拍，供响应回选。APB4 的响应在 ACCESS 拍之后。",
        selr,
        "",
        "  interface Apb4SlavePins apb;",
        "    method Action req(paddr, pprot, psel, penable, pwrite, pwdata, pstrb);",
        sel,
        selr_set,
        fan,
        "    endmethod",
        f"    method Bit#(32) prdata  = {rd};",
        f"    method Bool     pslverr = {er};",
        f"    method Bool     pready  = {rdy};",
        "  endinterface",
        *pins_impl,
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
