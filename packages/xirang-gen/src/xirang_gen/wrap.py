"""`wrap/`：扁平端口顶层，独立可综合、独立可流片。

它是三件事的组合：**契约 + 选一种总线 + 扁平化**。外人不讲我们的契约，只讲
APB4 或 AXI，所以 `emit` 的这一项必须带 `bus`。

手写的话是 **IP 数 × 总线数** 份样板，正是立项时要消灭的那个矩阵。
"""
from __future__ import annotations

from xirang_core.manifest import Bad, Pkg

BUSES = {"apb4"}


def _lit(v) -> str:
    if isinstance(v, bool):
        return "True" if v else "False"
    return str(v)


def flat_emit(pkg: Pkg) -> dict | None:
    for e in pkg.ip.get("emit", []) or []:
        if e.get("kind") == "verilog-flat":
            if "bus" not in e:
                raise Bad(f"{pkg.name}: verilog-flat 必须带 bus——"
                          f"扁平化要选一种总线，外人不讲我们的契约")
            if e["bus"] not in BUSES:
                raise Bad(f"{pkg.name}: 本版扁平化只支持 {sorted(BUSES)}，"
                          f"收到 {e['bus']}")
            return e
    return None


def wrap(pkg: Pkg, vals) -> str:
    e = flat_emit(pkg)
    if e is None:
        raise Bad(f"{pkg.name} 的 emit 里没有 verilog-flat")
    shape = ((pkg.ip.get("contract") or {}).get("ctrl") or {}).get("shape", "flat")
    if shape != "flat":
        raise Bad(f"{pkg.name} 的契约形态是 {shape}，本版只给 flat 生成扁平顶层。"
                  f"server 形态要先接一个绑定器，代价另计")
    b = pkg.bsv_emit()
    knobs = pkg.knobs()
    ctrl = (pkg.ip.get("contract") or {}).get("ctrl") or {}
    nums = [str(vals[k].value) for k in knobs if knobs[k]["kind"] == "param"]
    targs = ", ".join([str(ctrl.get("aw", 8)), str(ctrl.get("dw", 32))] + nums)
    feats = ", ".join(f"{k}: {_lit(vals[k].value)}"
                      for k in knobs if knobs[k]["kind"] == "feature")
    cap = pkg.name[:1].upper() + pkg.name[1:]
    tag = "_".join(nums) if nums else "0"

    pkgs = {b["package"]}
    regs = f"{cap}Regs"
    L = [
        f"package {cap}Wrap;",
        "",
        "// 由 ip.yaml 的 emit 段生成，勿手改。",
        f"// 扁平端口顶层：契约 {shape} + 总线 {e['bus']} + 扁平化。可独立综合与独立流片。",
        "",
        "import Apb4::*;",
        f"import {regs}::*;",
        *[f"import {p}::*;" for p in sorted(pkgs)],
        "",
        "(* synthesize *)",
        '(* default_clock_osc = "clk", default_reset = "rst_n" *)',
        f"module mk{cap}Wrap_{tag}({b['interface']}#({targs}));",
        f"  let m <- {b['module']}({b['config_type']} {{ {feats} }});",
        "  return m;",
        "endmodule",
        "",
        "endpackage",
    ]
    return "\n".join(L) + "\n"
