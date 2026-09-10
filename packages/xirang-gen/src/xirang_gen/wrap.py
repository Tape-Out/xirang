"""`wrap/`：扁平端口顶层，独立可综合、独立可流片。

它是三件事的组合：**契约 + 选一种总线 + 扁平化**。IP 自己只讲中立的 `RegIf`，
外人不讲我们的契约、只讲 APB4 或 AXI，所以 `emit` 的这一项必须带 `bus`，
而认识总线的代码只有 `amba` 一处。

手写的话是 **IP 数 × 总线数** 份样板，正是立项时要消灭的那个矩阵。
"""
from __future__ import annotations

from xirang_core.manifest import Bad, Pkg

# 每种总线：BSV 包名、引脚接口、绑定器、以及实现它的那个仓（面积记在那里）。
# 加一种总线是往这张表加一行。
BUSES = {
    "apb4": {"pkg": "Apb4", "pins": "Apb4SlavePins", "bind": "mkApb4Bind",
             "bindt": "mkApb4BindT", "manifest": "amba"},
}


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


def sub_targs(sub: dict, vals, name: str) -> str:
    """子接口的类型参数：写旋钮名就取求解后的值，写数字就照用。"""
    out = []
    for a in sub.get("targs", []) or []:
        if isinstance(a, int):
            out.append(str(a))
        elif a in vals:
            out.append(str(vals[a].value))
        else:
            raise Bad(f"{name}: 子接口 {sub.get('name')} 的类型参数 {a} "
                      f"既不是数字也不是这个包的旋钮")
    return f"#({', '.join(out)})" if out else ""


def irq_type(w, vals) -> str:
    """一根中断线是 Bool，多根是 Bit#(n)。n 可以写旋钮名，求解后取值。"""
    if w is None:
        return "Bool"
    return f"Bit#({vals[w].value if w in vals else w})"


def _shape(pkg: Pkg, vals):
    """两种顶层共用的那部分：类型参数、特性实参、子接口、中断。"""
    b = pkg.bsv_emit()
    knobs = pkg.knobs()
    ctrl = (pkg.ip.get("contract") or {}).get("ctrl") or {}
    aw, dw = ctrl.get("aw", 8), ctrl.get("dw", 32)
    nums = [str(vals[k].value) for k in knobs if knobs[k]["kind"] == "param"]
    return {
        "b": b,
        "aw": aw, "dw": dw,
        "targs": ", ".join([str(aw), str(dw)] + nums),
        "feats": ", ".join(f"{k}: {_lit(vals[k].value)}"
                           for k in knobs if knobs[k]["kind"] == "feature"),
        "cap": pkg.name[:1].upper() + pkg.name[1:],
        "tag": "_".join(nums) if nums else "0",
        "subs": b.get("pins") or [],
        # 控制口按特性选：`slow_when` 那个特性开着就走会停顿的那个口。
        # 中立顶层是「独立流片的样子」，同步存储的样子就是会停顿的那个口。
        "ctrl_name": (b["ctrl_slow"]
                      if b.get("ctrl_slow") and vals[b["slow_when"]].value
                      else b.get("ctrl", "regs")),
        "ctrl_type": ("RegTarget"
                      if b.get("ctrl_slow") and vals[b["slow_when"]].value
                      else "RegIf"),
        "irqs": [(i["name"], i.get("width"))
                 for i in (pkg.ip.get("contract") or {}).get("irq", []) or []],
    }


def wrap(pkg: Pkg, vals) -> str:
    e = flat_emit(pkg)
    if e is None:
        raise Bad(f"{pkg.name} 的 emit 里没有 verilog-flat")
    shape = ((pkg.ip.get("contract") or {}).get("ctrl") or {}).get("shape", "flat")
    if shape == "none":
        raise Bad(f"{pkg.name} 没有控制口，扁平化无从谈起——"
                  f"它不是总线从设备。要独立综合就跑 build --neutral")
    if shape != "flat":
        raise Bad(f"{pkg.name} 的契约形态是 {shape}，本版只给 flat 生成扁平顶层。"
                  f"server 形态要先接一个绑定器，代价另计")
    bus = BUSES[e["bus"]]
    s = _shape(pkg, vals)
    b, cap = s["b"], s["cap"]

    ifc = [f"interface {cap}WrapIfc;",
           f"  interface {bus['pins']}#({s['aw']}, {s['dw']}) bus;"]
    ifc += [f"  interface {x['type']}{sub_targs(x, vals, pkg.name)} {x['name']};"
            for x in s["subs"]]
    ifc += [f'  (* always_ready, result = "{n}" *) '
            f'method {irq_type(w, vals)} {n};'
            for n, w in s["irqs"]]
    ifc.append("endinterface")

    body = [
        f"  {b['interface']}#({s['targs']}) m <- {b['module']}"
        f"({b['config_type']} {{ {s['feats']} }});",
        f"  {bus['pins']}#({s['aw']}, {s['dw']}) sl <- "
        f"{bus['bindt' if s['ctrl_type'] == 'RegTarget' else 'bind']}"
        f"(m.{s['ctrl_name']});",
        "",
        "  interface bus = sl;",
    ]
    body += [f"  interface {x['name']} = m.{x['name']};" for x in s["subs"]]
    body += [f"  method {irq_type(w, vals)} {n} = m.{n};" for n, w in s["irqs"]]

    L = [
        f"package {cap}Wrap;",
        "",
        "// 由 ip.yaml 的 emit 段生成，勿手改。",
        f"// 扁平端口顶层：中立契约 + {e['bus']} 绑定 + 扁平化。可独立综合与独立流片。",
        "",
        f"import {bus['pkg']}::*;",
        "import RegIf::*;",
        f"import {b['package']}::*;",
        "",
        *ifc,
        "",
        "(* synthesize *)",
        '(* default_clock_osc = "clk", default_reset = "rst_n" *)',
        f"module mk{cap}Wrap_{s['tag']}({cap}WrapIfc);",
        *body,
        "endmodule",
        "",
        "endpackage",
    ]
    return "\n".join(L) + "\n"


def neutral(pkg: Pkg, vals, suffix: str = "") -> str:
    """中立顶层：只有契约与自有引脚，不含总线。

    价目表量的就是这一层。量包装层会把绑定器算进每一个 IP，而装配里整颗芯片
    只有一个绑定器，于是译码项算出负数——那是模型错了，不是工具错了。

    suffix 给矩阵测试用：同一个 IP 的多个配置点要共处一次编译，包名就得各不相同。
    """
    s = _shape(pkg, vals)
    b, cap = s["b"], s["cap"]

    # 核这类只发起、不被访问的 IP 没有控制口。它的 CSR 空间是自己用的，
    # 引到顶层会让「规则用」与「外面用」抢同一个方法，规则于是永不触发。
    none = (((pkg.ip.get("contract") or {}).get("ctrl") or {})
            .get("shape") == "none")
    ifc = [f"interface {cap}Bare{suffix}Ifc;"]
    if not none:
        ifc.append(f"  interface {s['ctrl_type']}#({s['aw']}, {s['dw']}) "
                   f"{s['ctrl_name']};")
    ifc += [f"  interface {x['type']}{sub_targs(x, vals, pkg.name)} {x['name']};"
            for x in s["subs"]]
    ifc += [f"  (* always_ready *) method {irq_type(w, vals)} {n};"
            for n, w in s["irqs"]]
    ifc.append("endinterface")

    body = [f"  {b['interface']}#({s['targs']}) m <- {b['module']}"
            f"({b['config_type']} {{ {s['feats']} }});"]
    if not none:
        body.append(f"  interface {s['ctrl_name']} = m.{s['ctrl_name']};")
    body += [f"  interface {x['name']} = m.{x['name']};" for x in s["subs"]]
    body += [f"  method {irq_type(w, vals)} {n} = m.{n};" for n, w in s["irqs"]]

    L = [
        f"package {cap}Bare{suffix};",
        "",
        "// 由 ip.yaml 的 emit 段生成，勿手改。中立顶层：价目表量的就是这一层。",
        "",
        "import RegIf::*;",
        f"import {b['package']}::*;",
        "",
        *ifc,
        "",
        "(* synthesize *)",
        '(* default_clock_osc = "clk", default_reset = "rst_n" *)',
        f"module mk{cap}Bare{suffix}_{s['tag']}({cap}Bare{suffix}Ifc);",
        *body,
        "endmodule",
        "",
        "endpackage",
    ]
    return "\n".join(L) + "\n"
