"""装配：由 instances 段生成顶层 BSV。

判据是装配包里**零自有 RTL**——生成器只要还需要一句手写胶水，就说明 schema
缺字段，该补的是字段，不是把胶水塞进仓里。

译码用 `hwcore` 的 `mkFabric`（编译期完全展开），总线绑定用 `amba` 的
`mkApb4Bind`，**整颗 SoC 只有一个绑定器**，而不是每个 IP 各带一个。

实例声明的 `RegManager` 子接口是**发起口**：核要取指与访存、DMA 要搬数据。
它们不接到顶层，而是接进 `mkXbar` 与外部总线一起排队。
"""
from __future__ import annotations

from xirang_core.manifest import Bad, Pkg
from xirang_core.model import Resolved
from xirang_gen.wrap import BUSES, sub_targs

MANAGER = "RegManager"


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

    imports, decls, devs, pins_if, pins_impl = [], [], [], [], []
    irq_if, irq_impl, mgrs = [], [], []
    seen = set()
    k = 0

    for inst in res.instances:
        p = pkgs[inst.of]
        e = p.bsv_emit()
        if e["package"] not in seen:
            imports.append(f"import {e['package']}::*;")
            seen.add(e["package"])
        knobs = p.knobs()
        args = ", ".join(f"{n}: {_lit(inst.values[n].value)}"
                         for n in knobs if knobs[n]["kind"] == "feature")
        nums = [str(inst.values[n].value) for n in knobs
                if knobs[n]["kind"] == "param"]
        # IP 按它自己声明的位宽例化——价目表就是照这个宽度量的。
        # 撑到片上的 32 位会多出一截地址比较，账就对不上了。
        ic = (p.ip.get("contract") or {}).get("ctrl") or {}
        iaw, idw = ic.get("aw", 8), ic.get("dw", 32)
        shape = ic.get("shape", "flat")
        if idw != dw:
            raise Bad(f"{inst.name} 的数据宽 {idw} 与片上 {dw} 不一致，本版不做宽度转换")
        if shape != "none" and iaw > aw:
            raise Bad(f"{inst.name} 的地址宽 {iaw} 比片上 {aw} 还宽")
        decls.append(f"  {e['interface']}#({', '.join([str(iaw), str(idw)] + nums)}) "
                     f"{inst.name} <- {e['module']}({e['config_type']} {{ {args} }});")

        # 没有控制口的实例（核）不进地址图，只出现在发起方那一侧
        if shape != "none":
            if inst.addr is None:
                raise Bad(f"{inst.name} 没有地址，且它的包没有 regmap.yaml 的 base")
            span = inst.size or (1 << iaw)
            ip_irqs = (p.ip.get("contract") or {}).get("irq", []) or []
            if not ip_irqs:
                one = "tagged Invalid"
            elif ip_irqs[0].get("width"):
                one = f"tagged Valid ({inst.name}.{ip_irqs[0]['name']} != 0)"
            else:
                one = f"tagged Valid {inst.name}.{ip_irqs[0]['name']}"
            devs.append(f"  devs[{k}] = device({aw}'h{inst.addr:0{hexw}X}, "
                        f"{aw}'h{span:0{hexw}X}, "
                        f"narrow({inst.name}.{e.get('ctrl', 'regs')}), {one});")
            k += 1

        for s in e.get("pins") or []:
            if s["type"] == MANAGER:
                mgrs.append(f"{inst.name}.{s['name']}")
                continue
            nm = f"{inst.name}_{s['name']}"
            pins_if.append(f"  interface {s['type']}"
                           f"{sub_targs(s, inst.values, p.name)} {nm};")
            pins_impl.append(f"  interface {nm} = {inst.name}.{s['name']};")

    if not k:
        raise Bad(f"{res.top} 的地址图是空的——至少要有一个带控制口的实例")
    n = len(res.instances)
    irq_if.append(f"  (* always_ready, result = \"irqs\" *) method Bit#({k}) irqs;")
    irq_impl.append(f"  method Bit#({k}) irqs = pack(irqsOf(devs));")

    m = len(mgrs)
    if m:
        # 不造仲裁器：外部总线的绑定器与仲裁规则都调 fab.access，而一个动作方法
        # 一拍只能被调一次——**调度器**天然保证了互斥，外部口优先、片内发起方
        # 捡它不用的拍。片内之间的公平由 turn 轮转，固定优先级会让 DMA 一忙
        # 起来核就饿死，那是仿真里偶尔看得见、流片后天天看得见的毛病。
        #
        # 但仲裁与驱动必须**分成两条规则**：ready 与 resp 是 always_enabled 的
        # 输入，每拍都得给；而仲裁那条会被总线方法挡住，挡住的拍就给不出。
        # 中间用线传，没轮到就是 ready 为假。
        # 外部总线不再走零等待的绑定器：它在方法里直接调 access，而那个方法
        # 是 always_enabled 的，会把仲裁规则永久挡住。改用会等待的那个，
        # 外部口于是变成第 m 个发起方，与片内的一视同仁。
        mgrs.append("extbus.mgr")
        m += 1
        fabric = [f"  RegIf#({aw}, {dw}) fab <- mkFabric(devs);",
                  f"  Apb4Manager#({aw}, {dw}) extbus <- mkApb4Manager;",
                  f"  Reg#(Bit#(TLog#(TAdd#({m}, 1)))) turn <- mkReg(0);",
                  f"  Vector#({m}, Wire#(Bool)) gnt <- replicateM(mkDWire(False));",
                  f"  Vector#({m}, Wire#(RegRsp#({dw}))) mrsp <- replicateM(",
                  f"      mkDWire(RegRsp {{ rdata: 0, err: False }}));",
                  "",
                  "  rule arbitrate;",
                  f"    Vector#({m}, Bool) v = newVector;",
                  f"    Vector#({m}, RegReq#({aw}, {dw})) q = newVector;"]
        for i2, g in enumerate(mgrs):
            fabric += [f"    v[{i2}] = {g}.valid;", f"    q[{i2}] = {g}.req;"]
        fabric += [
            f"    Bit#(TLog#(TAdd#({m}, 1))) s = 0;",
            "    Bool any = False;",
            f"    for (Integer i = 0; i < {m}; i = i + 1) begin",
            f"      Bit#(TLog#(TAdd#({m}, 1))) w = turn + fromInteger(i);",
            f"      if (w >= {m}) w = w - {m};",
            "      if (!any && v[w]) begin s = w; any = True; end",
            "    end",
            "    if (any) begin",
            "      let y <- fab.access(q[s]);",
            f"      for (Integer i = 0; i < {m}; i = i + 1)",
            "        if (s == fromInteger(i)) begin",
            "          gnt[i] <= True;",
            "          mrsp[i] <= y;",
            "        end",
            f"      turn <= (s + 1 >= {m}) ? 0 : s + 1;",
            "    end",
            "  endrule",
            "",
            "  rule drive_managers;"]
        for i2, g in enumerate(mgrs):
            fabric += [f"    {g}.ready(gnt[{i2}]);",
                       f"    {g}.resp(gnt[{i2}], mrsp[{i2}]);"]
        fabric.append("  endrule")
    else:
        fabric = [f"  RegIf#({aw}, {dw}) fab <- mkFabric(devs);",
                  f"  {bus['pins']}#({aw}, {dw}) sl <- {bus['bind']}(fab);"]

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
        f"  Vector#({k}, Device#({aw}, {dw})) devs = newVector;",
        *devs,
        "",
        *fabric,
        "",
        f"  interface bus = {'extbus.pins' if mgrs else 'sl'};",
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
