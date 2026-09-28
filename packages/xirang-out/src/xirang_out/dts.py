"""导出设备树源（`.dts`）与整颗芯片的 C 头。

寿木 SDK 的 Linux BSP 与 C 那一支从这里来。每个实例的节点名、附加的
`compatible` 与属性写在它自己清单的 `dt:` 里，缺省节点名就是包名，`compatible`
总带一条 `tape-out,<包名>`。**中断本版不导**：今天的中断连线在 `connect:` 里是
BSV 表达式，从那里猜出来的中断号不可信，等连线闭合给出结构化的连接再补。
"""
from .chip import devices, ident, props
from .target import Ctx, target


def _val(v) -> str:
    if v is True:
        return ""
    if isinstance(v, int):
        return f" = <{v:#x}>" if v > 9 else f" = <{v}>"
    if isinstance(v, list):
        return " = " + ", ".join(f'"{x}"' for x in v)
    return f' = "{v}"'


def _node(d, lines: list[str], ind: str, cpu_reg: int | None = None) -> None:
    name = d.dt.get("node", d.of)
    unit = cpu_reg if cpu_reg is not None else d.addr
    compat = [f"tape-out,{d.of}", *(d.dt.get("compatible") or [])]
    label = ident(d.name)
    head = f"{label}: {name}@{unit:x} {{" if unit is not None else f"{label}: {name} {{"
    lines.append(ind + head)
    lines.append(f"{ind}\tcompatible = " + ", ".join(f'"{c}"' for c in compat) + ";")
    if cpu_reg is not None:
        lines += [f'{ind}\tdevice_type = "cpu";', f"{ind}\treg = <{cpu_reg}>;"]
    elif d.addr is not None:
        lines.append(f"{ind}\treg = <{d.addr:#x} {d.size:#x}>;")
    for k, v in props(d.dt, d.knobs):
        lines.append(f"{ind}\t{k}{_val(v)};")
    # 属性必须写在子节点前面，dtc 否则拒收
    lines.append(f'{ind}\tstatus = "okay";')
    if cpu_reg is not None and "riscv" in (d.dt.get("compatible") or []):
        lines += [f"{ind}\t{label}_intc: interrupt-controller {{",
                  f"{ind}\t\t#interrupt-cells = <1>;",
                  f"{ind}\t\tinterrupt-controller;",
                  f'{ind}\t\tcompatible = "riscv,cpu-intc";',
                  f"{ind}\t}};"]
    lines.append(ind + "};")


@target(name="dts", ext=".dts", desc="设备树源，dtc 编成 .dtb 给 Linux")
def to_dts(ctx: Ctx) -> str:
    devs = devices(ctx)
    cpus = [d for d in devs if d.dt.get("node") == "cpu"]
    mems = [d for d in devs if d.dt.get("node") == "memory" and d.addr is not None]
    rest = [d for d in devs if d not in cpus and d not in mems and d.addr is not None]
    L = ["// 由 xirang 生成，勿手改。导的是已解出的那一份配置；中断本版不导", "/dts-v1/;", "",
         "/ {", "\t#address-cells = <1>;", "\t#size-cells = <1>;",
         f'\tcompatible = "tape-out,{ctx.res.top}";', f'\tmodel = "Tape-Out {ctx.res.top}";', ""]
    if cpus:
        L += ["\tcpus {", "\t\t#address-cells = <1>;", "\t\t#size-cells = <0>;"]
        for n, d in enumerate(cpus):
            _node(d, L, "\t\t", cpu_reg=n)
        L += ["\t};", ""]
    for d in mems:
        L += [f"\tmemory@{d.addr:x} {{", '\t\tdevice_type = "memory";',
              f"\t\treg = <{d.addr:#x} {d.size:#x}>;", "\t};", ""]
    if rest:
        L += ["\tsoc {", "\t\t#address-cells = <1>;", "\t\t#size-cells = <1>;",
              '\t\tcompatible = "simple-bus";', "\t\tranges;"]
        for d in sorted(rest, key=lambda x: x.addr or 0):
            _node(d, L, "\t\t")
        L += ["\t};"]
    L += ["};", ""]
    return "\n".join(L)


@target(name="h", ext=".h", desc="整颗芯片的 C 头：各实例的基址，并引入各 IP 的寄存器头")
def to_header(ctx: Ctx) -> str:
    guard = ident(ctx.res.top).upper() + "_H"
    L = ["/* 由 xirang 生成，勿手改。各 IP 的寄存器偏移在它自己的头里 */",
         f"#ifndef {guard}", f"#define {guard}", "", "#include <stdint.h>"]
    devs = [d for d in devices(ctx) if d.addr is not None]
    seen = []
    for d in devs:
        rm = getattr(d.pkg, "regmap", None)
        ip = rm.get("ip", d.of) if rm else None
        if ip and ip not in seen:
            seen.append(ip)
            L.append(f'#include "{ip}.h"')
    L += ["", "#define REG32(base, off) (*(volatile uint32_t *)((uintptr_t)(base) + (off)))", ""]
    for d in sorted(devs, key=lambda x: x.addr or 0):
        if ident(d.name) in seen:        # 实例与 IP 同名：IP 头里那个 _BASE 是寄存器图自己的基址
            L.append(f"#undef {ident(d.name).upper()}_BASE")
        L.append(f"#define {ident(d.name).upper()}_BASE {d.addr:#010x}u")
        L.append(f"#define {ident(d.name).upper()}_SIZE {d.size:#x}u")
    L += ["", "#endif", ""]
    return "\n".join(L)
