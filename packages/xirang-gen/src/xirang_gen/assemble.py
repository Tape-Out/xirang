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
TARGET = "RegTarget"


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
    # 会停顿的设备走另一条向量。中断号照实例次序单记一份：设备分了两条向量，
    # 位号就不能再靠 devs 的下标了。
    slows, irqs = [], []
    # 装配里接过的子接口不能再往顶层透传：一个 always_enabled 方法
    # 既被片内规则调、又从顶层露出去，就是两个调用方，冲突。
    wired = set()
    # pipe 接掉的发起口不再进仲裁器：它已经被目标吃掉，真正上总线的是
    # 目标自己的下游口。
    piped = set()
    for c in root.ip.get("pipe") or []:
        bits = str(c.get("manager") or "").split(".")
        if len(bits) == 2:
            piped.add((bits[0], bits[1]))
    for c in root.ip.get("connect") or []:
        bits = str(c.get("to") or "").split(".")
        if len(bits) >= 2:
            wired.add((bits[0], bits[1]))
    irq_if, irq_impl, irq_decl, mgrs = [], [], [], []
    seen = set()
    k = ks = 0

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
            irqs.append("False" if not ip_irqs else one[len("tagged Valid "):])
            cs, sw = e.get("ctrl_slow"), e.get("slow_when")
            if cs and sw and inst.values[sw].value:
                slows.append(f"  slowv[{ks}] = slowDevice({aw}'h{inst.addr:0{hexw}X}, "
                             f"{aw}'h{span:0{hexw}X}, "
                             f"narrowT({inst.name}.{cs}), {one});")
                ks += 1
            else:
                devs.append(f"  devs[{k}] = device({aw}'h{inst.addr:0{hexw}X}, "
                            f"{aw}'h{span:0{hexw}X}, "
                            f"narrow({inst.name}.{e.get('ctrl', 'regs')}), {one});")
                k += 1

        for s in e.get("pins") or []:
            if s["type"] == MANAGER:
                if (inst.name, s["name"]) not in piped:
                    mgrs.append(f"{inst.name}.{s['name']}")
                continue
            if s["type"] == TARGET:
                # 会停顿的目标由 pipe 接，不往顶层透传
                continue
            if (inst.name, s["name"]) in wired:
                continue
            nm = f"{inst.name}_{s['name']}"
            pins_if.append(f"  interface {s['type']}"
                           f"{sub_targs(s, inst.values, p.name)} {nm};")
            pins_impl.append(f"  interface {nm} = {inst.name}.{s['name']};")

    if not k + ks:
        raise Bad(f"{res.top} 的地址图是空的——至少要有一个带控制口的实例")

    # 片内连线。工具不认得信号的含义，只认「哪个实例的哪个方法」，路由写在
    # 装配清单的 connect 段里，这里只查名字、落成一条规则。名字认不出来就报错。
    wires = []
    names = {i.name for i in res.instances}
    # 发起口接会停顿的目标：四个方法的握手，是 mkPipe 一个模块的事，
    # 不是一条方法调用，所以跟 connect 分开写。
    for n3, c in enumerate(root.ip.get("pipe") or []):
        unknown = set(c) - {"manager", "target", "desc"}
        if unknown:
            raise Bad(f"pipe 第 {n3 + 1} 条有不认识的键 {sorted(unknown)}")
        for k2 in ("manager", "target"):
            if not c.get(k2):
                raise Bad(f"pipe 第 {n3 + 1} 条没写 {k2}")
            who2 = str(c[k2]).split(".")[0]
            if who2 not in names:
                raise Bad(f"pipe 的 {k2} 指向不存在的实例 {who2}")
        if c.get("desc"):
            wires.append(f"  // {c['desc']}")
        wires.append(f"  Empty pipe{n3} <- mkPipe({c['manager']}, "
                     f"{c['target']});")
        wires.append("")
    for n2, c in enumerate(root.ip.get("connect") or []):
        unknown = set(c) - {"to", "args", "desc"}
        if unknown:
            raise Bad(f"connect 第 {n2 + 1} 条有不认识的键 {sorted(unknown)}")
        tgt = str(c.get("to") or "")
        if not tgt:
            raise Bad(f"connect 第 {n2 + 1} 条没写 to")
        who = tgt.split(".")[0]
        if who not in names:
            raise Bad(f"connect 的 to 指向不存在的实例 {who}")
        # 只有写成 实例.方法 或 实例[下标] 的才算引用，其余当字面量
        # （接地就写 False，接常数就写数）。这样一来引用写错必报，
        # 而字面量不必编进白名单。
        for a in c.get("args") or []:
            s2 = str(a)
            if "." not in s2 and "[" not in s2:
                continue
            ref = s2.split(".")[0].split("[")[0].strip()
            if ref not in names:
                raise Bad(f"connect 的 args 提到不存在的实例 {ref}")
        call = ", ".join(str(a) for a in (c.get("args") or []))
        if c.get("desc"):
            wires.append(f"  // {c['desc']}")
        wires += [f"  rule wire{n2}_{who};",
                  f"    {tgt}({call});",
                  "  endrule", ""]
    n = len(res.instances)
    ni = k + ks
    irq_if.append(f"  (* always_ready, result = \"irqs\" *) method Bit#({ni}) irqs;")
    if ks:
        # 设备分两条向量之后 irqsOf 只看得见一半，位号改由实例次序直接铺开。
        # 没有慢设备时仍走原式：一处等价改写会让全库装配的价目表统统作废。
        # 这几行是语句不是子接口，得排在规则之前（P0032）。
        irq_decl.append(f"  Vector#({ni}, Bool) irqv = newVector;")
        irq_decl += [f"  irqv[{i}] = {x};" for i, x in enumerate(irqs)]
        irq_impl.append(f"  method Bit#({ni}) irqs = pack(irqv);")
    else:
        irq_impl.append(f"  method Bit#({ni}) irqs = pack(irqsOf(devs));")

    ftype = "RegTarget" if ks else "RegIf"
    mkfab = (f"  RegTarget#({aw}, {dw}) fab <- mkFabricT(devs, slowv);" if ks
             else f"  RegIf#({aw}, {dw}) fab <- mkFabric(devs);")
    m = len(mgrs)
    if m and ks:
        # 会停顿的织体：仲裁住在 hwcore 的 mkArb 里，不在这里铺开。
        # 生成的顶层里不该有四十行自有 RTL——那既没人测，也违背「零自有 RTL」。
        mgrs.append("extbus.mgr")
        m += 1
        fabric = [mkfab,
                  f"  Apb4Manager#({aw}, {dw}) extbus <- mkApb4Manager;",
                  f"  Vector#({m}, RegManager#({aw}, {dw})) mgrs = newVector;"]
        fabric += [f"  mgrs[{i2}] = {g};" for i2, g in enumerate(mgrs)]
        fabric += ["  Empty arb <- mkArb(mgrs, fab);"]
    elif m:
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
        fabric = [mkfab,
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
            "    // 轮转要在更宽的类型里算：turn + i 最大 2*(m-1)，在索引",
            "    // 自己的位宽里会回绕，回绕之后 `w >= m` 那句就救不回来，",
            "    // 某个发起方于是一次也轮不到——而 turn 只在授予时前进，",
            "    // 结果是它举着手、仲裁每拍判空闲，谁也不动。",
            f"    for (Integer i = 0; i < {m}; i = i + 1) begin",
            f"      Bit#(8) w8 = zeroExtend(turn) + fromInteger(i);",
            f"      if (w8 >= {m}) w8 = w8 - {m};",
            f"      Bit#(TLog#(TAdd#({m}, 1))) w = truncate(w8);",
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
        # 片上只有这一个发起方，慢织体可以直接接到 PREADY 上，不必再排一次队
        b2 = bus["bindt"] if ks else bus["bind"]
        fabric = [mkfab,
                  f"  {bus['pins']}#({aw}, {dw}) sl <- {b2}(fab);"]

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
        *([f"  Vector#({ks}, SlowDevice#({aw}, {dw})) slowv = newVector;"] + slows
          if ks else []),
        "",
        *fabric,
        "",
        *irq_decl,
        *([""] if irq_decl else []),
        # 规则要在方法与子接口之前：BSV 规定它们必须在块末（P0032）
        *wires,
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
