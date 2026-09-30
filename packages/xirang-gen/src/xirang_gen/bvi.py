"""黑盒进装配：照 `emit.ports` 的声明与展开后的端口表，生成一层 BSV `import "BVI"`。

包好之后黑盒在装配里与自家的 IP 没有区别：它的发起口经协议适配器进仲裁，中断走
`connect`，物理端点导出到顶层。bsc 出的 Verilog 按名字例化上游的顶层，展平时把上游
的源码一起交给 yosys。

端口按端点归组：写 `prefix` 的取这个前缀开头的全部端口，方法名是去掉前缀的剩余部分；
写 `map` 的照表。除时钟与复位，每个端口都要落在某个端点里，漏了当场报。

协议适配器是库里的模块，不在这里：`PROFILES` 记着每种 profile 用哪个接口、哪个适配器，
以及端口表里可能缺的信号取什么值（Hazard3 的取指口就没有 `hexcl`）。加一种协议只加一行。
"""
import dataclasses
import re

from xirang_core.manifest import Bad


@dataclasses.dataclass(frozen=True)
class Profile:
    pkg: str            # 接口与适配器所在的 BSV 包
    pins: str           # 标准的引脚接口
    adapter: str        # 引脚接口 -> RegManager#(32, 32)
    signals: tuple      # (名字, 方向, 位宽, 缺了取什么)；方向从黑盒看，out 是它驱的
    addr: str = ""      # 地址线比 32 位宽时截到低 32 位的那一根


_AHB = (("haddr", "out", 32, None), ("hwrite", "out", 1, None), ("htrans", "out", 2, None),
        ("hsize", "out", 3, None), ("hburst", "out", 3, 0), ("hprot", "out", 4, 0),
        ("hmastlock", "out", 1, 0), ("hmaster", "out", 8, 0), ("hexcl", "out", 1, 0),
        ("hwdata", "out", 32, None), ("hready", "in", 1, None), ("hresp", "in", 1, None),
        ("hexokay", "in", 1, 0), ("hrdata", "in", 32, None))

PROFILES = {
    "ahb5": Profile("Ahb", "AhbMgrPins", "mkAhbMgr", _AHB),
}

RESERVED = {"module", "interface", "method", "rule", "begin", "end", "input", "output",
            "reg", "wire", "type", "case", "default", "if", "else", "return", "let"}


@dataclasses.dataclass
class Endpoint:
    name: str
    kind: str
    role: str | None
    profile: str | None
    methods: list          # [(方法名, 端口名, 方向 in/out, 位宽)]


def camel(name: str) -> str:
    return "".join(w[:1].upper() + w[1:] for w in re.split(r"[-_]", name) if w)


def ident(s: str) -> str:
    s = re.sub(r"\W", "_", s)
    s = s[:1].lower() + s[1:]
    return s + "_" if s in RESERVED else s


def endpoints(e: dict, ports: dict) -> list[Endpoint]:
    """把展开后的端口按清单的端点归组。归不进去的端口、写了却不存在的端口都报。"""
    skip = {(e.get("clock") or {}).get("port"), (e.get("reset") or {}).get("port")}
    skip |= set((e.get("clock") or {}).get("also") or [])
    owned: dict[str, str] = {}
    out = []
    for pt in e.get("ports") or []:
        ms = []
        if m := pt.get("map"):
            for meth, port in m.items():
                if port not in ports:
                    raise Bad(f"XR-FGN-001 端点 {pt['endpoint']} 映射到 {port}，展开之后没有这个端口")
                ms.append((ident(meth), port))
        elif pre := pt.get("prefix"):
            ms = [(ident(n[len(pre):]), n) for n in ports if n.startswith(pre) and n not in skip]
            if not ms:
                raise Bad(f"XR-FGN-001 端点 {pt['endpoint']} 的前缀 {pre} 一个端口都没匹配到")
        rows = []
        for meth, port in ms:
            if port in owned:
                raise Bad(f"XR-FGN-001 端口 {port} 同时归在端点 {owned[port]} 与 {pt['endpoint']}")
            owned[port] = pt["endpoint"]
            p = ports[port]
            if p.direction == "inout":
                raise Bad(f"XR-FGN-001 端口 {port} 是 inout，要拆成输入、输出、使能三根")
            rows.append((meth, port, "in" if p.direction == "in" else "out", max(p.width, 1)))
        out.append(Endpoint(pt["endpoint"], pt["kind"], pt.get("role"), pt.get("profile"), rows))
    left = [n for n in ports if n not in owned and n not in skip]
    if left:
        raise Bad(f"XR-WIRE-002 黑盒 {e['top']} 的端口 {left[:6]} 不在任何端点里")
    return out


def names(pkg_name: str) -> tuple[str, str, str]:
    """(BSV 包名, 模块名, 接口名)"""
    m = camel(pkg_name)
    return f"{m}Bvi", f"mk{m}Bvi", f"{m}Bvi"


def generate(pkg_name: str, e: dict, ports: dict, params: dict) -> tuple[str, list[Endpoint]]:
    """BVI 包的全文，以及端点表（装配要按它接线）。"""
    clk = (e.get("clock") or {}).get("port")
    rst = e.get("reset") or {}
    if not clk:
        raise Bad(f"{pkg_name}: 黑盒进装配要写 clock.port")
    if rst.get("port") and rst.get("active", "low") != "low":
        raise Bad(f"XR-SPEC-001 {pkg_name}: 高有效的复位本版还不能直接进装配")
    also = list((e.get("clock") or {}).get("also") or [])
    eps = endpoints(e, ports)
    bpkg, mod, ifc = names(pkg_name)
    m = camel(pkg_name)
    imports = sorted({PROFILES[x.profile].pkg for x in eps if x.profile in PROFILES})
    L = [f"package {bpkg};", "",
         f"// 由息壤照 {pkg_name} 的 ip.yaml 与展开后的端口表生成，勿手改。", ""]
    L += [f"import {p}::*;" for p in imports] + ([""] if imports else [])
    for x in eps:
        L.append(f"interface {m}{camel(x.name)};")
        for meth, _, d, w in x.methods:
            if d == "out":
                L.append(f"  (* always_ready *) method Bit#({w}) {meth};")
            else:
                L.append(f"  (* always_ready, always_enabled *) method Action {meth}(Bit#({w}) v);")
        L += ["endinterface", ""]
    L.append(f"interface {ifc};")
    L += [f"  interface {m}{camel(x.name)} {ident(x.name)};" for x in eps]
    L += ["endinterface", ""]
    args = ", ".join(f"Clock c{i}" for i in range(len(also)))
    L.append(f'import "BVI" {e["top"]} =')
    L.append(f"module {mod}{'#(' + args + ')' if args else ''}({ifc});")
    for k, v in sorted(params.items()):
        L.append(f"  parameter {k} = {int(v)};")
    L.append(f"  default_clock clk({clk}, (*unused*) clk_gate);")
    L.append(f"  default_reset rst({rst['port']}) clocked_by(clk);" if rst.get("port")
             else "  default_reset no_reset;")
    for i, port in enumerate(also):
        # 上游的另一根时钟（Hazard3 的 clk_always_on）：这颗芯片只有一个时钟域，接同一根
        L.append(f"  input_clock aon{i}({port}, (*unused*) aon{i}_gate) = c{i};")
    every = []
    for x in eps:
        L.append(f"  interface {m}{camel(x.name)} {ident(x.name)};")
        for meth, port, d, w in x.methods:
            if d == "out":
                L.append(f"    method {port} {meth};")
            else:
                L.append(f"    method {meth}({port}) enable((*inhigh*) EN_{port});")
            every.append(f"{ident(x.name)}.{meth}")
        L.append("  endinterface")
    L.append(f"  schedule ({', '.join(every)}) CF ({', '.join(every)});")
    L += ["endmodule", ""]
    for x in eps:
        if x.profile in PROFILES:
            L += view(m, x, PROFILES[x.profile]) + [""]
    L += ["endpackage", ""]
    return "\n".join(L), eps


def view(m: str, x: Endpoint, pf: Profile) -> list[str]:
    """原始端点转成协议的标准接口：缺的输出取默认值，缺的输入不接。"""
    have = {meth: (d, w) for meth, _, d, w in x.methods}
    L = [f"function {pf.pins} {ident(x.name)}View({m}{camel(x.name)} r);",
         f"  return (interface {pf.pins};"]
    for sig, d, w, dflt in pf.signals:
        got = have.get(sig)
        if got is None and dflt is None:
            raise Bad(f"XR-FGN-001 端点 {x.name} 按 {x.profile} 要有 {sig}，端口表里没有")
        if got is not None and got[0] != d:
            raise Bad(f"XR-FGN-001 端点 {x.name} 的 {sig} 方向不对：{x.profile} 要 {d}")
        if d == "out":
            src = f"truncate(r.{sig})" if got and got[1] > w else (
                f"zeroExtend(r.{sig})" if got and got[1] < w else (f"r.{sig}" if got else str(dflt)))
            L.append(f"    method Bit#({w}) {sig} = {src};")
        else:
            body = f" r.{sig}(truncate(v));" if got and got[1] < w else (
                f" r.{sig}(v);" if got else "")
            L.append(f"    method Action {sig}(Bit#({w}) v);{body} endmethod")
    L += ["  endinterface);", "endfunction"]
    return L
