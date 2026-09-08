"""regmap.yaml -> BSV 寄存器组 + C 头。

规范见 Tape-Out/spec 的 regmap.md。语义内核沿用 SystemRDL 2.0，属性名原样不改。
本版覆盖 gpio 竖切用得到的子集：sw · hw · onwrite(woclr) · hwset · stickybit · reset。

对拍结论（V1）：生成件与手写件时序结构 20/20 逐位一致，行为 5 配置 × 2 万拍零失配。
"""
from __future__ import annotations

import pathlib

from xirang_core.manifest import Bad, Pkg

# 改动会改变生成的逻辑，从而让已回填的面积失效。改生成逻辑就要进版本。
GEN_VERSION = "0.4"   # 仅供人读；失效判定看生成产物的摘要，不看这个

# BSV 与 Verilog 的保留字。寄存器或字段叫这些名字，会一路生成到编译才炸，
# 而且报的是语法错、指不回 regmap.yaml。在生成时就拦住。
RESERVED = {
    "time", "reg", "wire", "input", "output", "inout", "module", "endmodule",
    "begin", "end", "if", "else", "case", "endcase", "default", "function",
    "endfunction", "interface", "endinterface", "method", "rule", "endrule",
    "return", "let", "match", "action", "endaction", "package", "endpackage",
    "type", "typedef", "struct", "enum", "union", "provisos", "instance",
    "parameter", "assign", "always", "posedge", "negedge", "initial", "and",
    "or", "not", "xor", "nand", "nor", "buf", "real", "integer", "signed",
    "bit", "logic", "int", "void", "clock", "reset", "port", "for", "while",
}

SW = {"rw", "r", "w"}
HW = {"rw", "r", "w", "na"}
# 字段允许的键。不在表里的一律报错——静默忽略过三次，每次都生成出默默错了的硬件。
FIELD_KEYS = {"name", "bits", "width", "desc", "sw", "hw", "onwrite", "onread",
              "hwset", "stickybit", "reset", "feature", "volatile", "swacc",
              "swmod"}
# 读带副作用。硬件自旋锁只能这么做：取锁必须与读回同一拍完成，
# 拆成「读一次再写一次」就有窗口，两个核会同时拿到锁。
ONREAD = {"rset", "rclr"}
REG_KEYS = {"name", "offset", "desc", "feature", "fields", "array", "width", "atomic"}
ATOMIC = {"latch-on-low"}


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:]


def _width(w, params: list[str]) -> str:
    """位宽 -> BSV 类型实参。裸参数名保持符号化，让 bsc 去参数化。"""
    if isinstance(w, int):
        return str(w)
    w = str(w).strip()
    if w in params or w.isdigit():
        return w
    raise Bad(f"位宽 {w!r} 既不是整数也不是已声明的参数；本版不支持表达式")


def _bits(f, params):
    """返回 (hi, lo, 宽度表达式)。单字段可用 width，多字段用 bits。"""
    if "bits" in f:
        hi, _, lo = str(f["bits"]).partition(":")
        hi, lo = int(hi), int(lo)
        return hi, lo, str(hi - lo + 1)
    w = _width(f.get("width", 32), params)
    return None, 0, w


def _rows(spec: dict, params: list[str], dw: int = 32) -> list[dict]:
    out = []
    seen: dict[int, str] = {}
    for r in spec.get("regs", []) or []:
        if "offset" not in r:
            raise Bad(f"寄存器 {r.get('name')} 缺 offset"
                      f"（注意 YAML 1.1 里 off 是布尔字面量，字段名是 offset）")
        off = int(str(r["offset"]), 0)
        if off in seen:
            raise Bad(f"偏移 {off:#x} 被 {seen[off]} 与 {r['name']} 同时占用")
        seen[off] = r["name"]
        if r.get("name") in RESERVED:
            raise Bad(f"寄存器名 {r['name']!r} 是 BSV/Verilog 保留字，换一个"
                      f"（生成出来会是语法错，且指不回 regmap.yaml）")
        unknown = set(r) - REG_KEYS
        if unknown:
            raise Bad(f"{r['name']}: 不认识的寄存器键 {sorted(unknown)}"
                      f"（允许 {sorted(REG_KEYS)}）")
        fs = r.get("fields") or []
        if not fs:
            raise Bad(f"{r['name']}: 没有字段")
        arr = r.get("array")
        if arr is not None:
            unknown = set(arr) - {"count", "stride"}
            if unknown:
                raise Bad(f"{r['name']}: array 里不认识的键 {sorted(unknown)}")
            for k in ("count", "stride"):
                if k not in arr:
                    raise Bad(f"{r['name']}: array 缺 {k}")
        rw = r.get("width")
        if rw is not None and int(rw) not in (dw, dw * 2):
            raise Bad(f"{r['name']}: 本版寄存器宽度只支持 {dw} 或 {dw*2} 位，收到 {rw}")
        if r.get("atomic") and r["atomic"] not in ATOMIC:
            raise Bad(f"{r['name']}: atomic 只支持 {sorted(ATOMIC)}")
        if r.get("atomic") and int(rw or dw) <= dw:
            raise Bad(f"{r['name']}: 不比总线宽的寄存器不需要 atomic")
        multi = len(fs) > 1
        if multi and any("bits" not in f for f in fs):
            raise Bad(f"{r['name']}: 多字段时每个字段都要写 bits")
        span = []
        flds = []
        for f in fs:
            if f.get("name") in RESERVED:
                raise Bad(f"{r['name']}.{f['name']}: 字段名是 BSV/Verilog 保留字，换一个")
            unknown = set(f) - FIELD_KEYS
            if unknown:
                raise Bad(f"{r['name']}.{f.get('name')}: 不认识的字段键 {sorted(unknown)}"
                          f"（允许 {sorted(FIELD_KEYS)}）")
            if f.get("volatile") and f.get("reset") is not None:
                raise Bad(f"{r['name']}.{f['name']}: volatile 字段没有存储，不该给 reset")
            if f.get("volatile") and f.get("hw", "na") not in ("w", "rw"):
                raise Bad(f"{r['name']}.{f['name']}: volatile 字段的值来自硬件，hw 须是 w 或 rw")
            if f.get("sw", "rw") not in SW:
                raise Bad(f"{r['name']}.{f['name']}: sw={f.get('sw')} 不在 {sorted(SW)}")
            if f.get("hw", "na") not in HW:
                raise Bad(f"{r['name']}.{f['name']}: hw={f.get('hw')} 不在 {sorted(HW)}")
            if f.get("onwrite") not in (None, "woclr"):
                raise Bad(f"{r['name']}.{f['name']}: 本版 onwrite 只支持 woclr")
            if f.get("onread") is not None and f["onread"] not in ONREAD:
                raise Bad(f"{r['name']}.{f['name']}: onread 只支持 {sorted(ONREAD)}")
            if f.get("onread") and f.get("volatile"):
                raise Bad(f"{r['name']}.{f['name']}: volatile 字段没有存储，"
                          f"读它不可能有副作用")
            hi, lo, w = _bits(f, params)
            if multi:
                for a, b, nm in span:
                    if not (hi < a or lo > b):
                        raise Bad(f"{r['name']}: 字段 {f['name']} 与 {nm} 位域重叠")
                span.append((hi, lo, f["name"]))
            flds.append({
                "name": f["name"], "hi": hi, "lo": lo, "w": w,
                "sw": f.get("sw", "rw"), "hw": f.get("hw", "na"),
                "woclr": f.get("onwrite") == "woclr",
                "hwset": bool(f.get("hwset")), "reset": f.get("reset"),
                "feat": f.get("feature") or r.get("feature"),
                "vol": bool(f.get("volatile")),
                "swacc": bool(f.get("swacc")), "swmod": bool(f.get("swmod")),
                "onread": f.get("onread"),
            })
        out.append({"name": r["name"], "offset": off, "desc": r.get("desc", ""),
                    "feat": r.get("feature"), "multi": multi, "fields": flds,
                    "arr": arr, "rw": int(rw) if rw else dw,
                    "atomic": r.get("atomic")})
    return out


def _sig(reg, f) -> str:
    """字段的信号名。单字段且叫 val 时省掉后缀，读起来干净。"""
    if not reg["multi"] and f["name"] == "val":
        return reg["name"]
    return f"{reg['name']}_{f['name']}"


def bsv(pkg: Pkg) -> str:
    spec = pkg.regmap
    ip = spec.get("ip", pkg.name)
    params: list[str] = list(spec.get("params") or [])
    dw_i = int((spec.get("contract") or {}).get("dw", 32))
    rs = _rows(spec, params, dw_i)
    feats = sorted({f["feat"] for r in rs for f in r["fields"] if f["feat"]}
                   | {r["feat"] for r in rs if r["feat"]})
    tparams = ", ".join(["numeric type aw", "numeric type dw"]
                        + [f"numeric type {p}" for p in params])
    targs = ", ".join(["aw", "dw"] + params)
    C = _cap(ip)
    aw = int((spec.get("contract") or {}).get("aw", 8))
    hexw = (aw + 3) // 4          # 十六进制位数，够写下最大偏移

    for _r in rs:
        if _r["offset"] >= (1 << aw):
            raise Bad(f"{_r['name']} 的 offset {_r['offset']:#x} 放不进 {aw} 位地址")

    def dual(f) -> bool:
        """软硬两侧都会写这个字段吗。是的话要 CReg 定序：端口 0 给规则、端口 1 给总线。

        原来只认 `hwset + woclr` 一种（中断状态）。`rtc` 的计数器逼出了第二种：
        硬件每拍加一、软件又能设时间，两边都写。不定序的话两条路互相要求排在
        对方之前，bsc 判定规则永不触发——而那只是一句告警，仿真里表现为时间不走。
        """
        if f["vol"]:
            return False
        return (f["hwset"] and f["woclr"]) or (
            f["hw"] in ("w", "rw") and f["sw"] in ("w", "rw"))

    def port(reg, f, p, ix=""):
        s = _sig(reg, f) + "_r" + ix
        return f"{s}[{p}]" if dual(f) else s

    L: list[str] = [f"package {C}Regs;", "",
                    "// 由 regmap.yaml 生成，勿手改。改 regmap.yaml 后重新生成。", "",
                    "import RegIf::*;", "import Vector::*;", ""]
    if feats:
        L.append("typedef struct {")
        L += [f"  Bool {f};" for f in feats]
        L += [f"}} {C}RegsCfg;", ""]

    L.append(f"interface {C}RegsIfc#({tparams});")
    L.append("  interface RegIf#(aw, dw) regs;")
    for reg in rs:
        for f in reg["fields"]:
            s = _sig(reg, f)
            if reg["arr"]:
                n = reg["arr"]["count"]
                if f["vol"] and f["hw"] in ("w", "rw"):
                    # 没有存储的数组：硬件每拍把整条向量驱动上去
                    L.append(f"  (* always_ready *) method Action {s}_in("
                             f"Vector#({n}, Bit#({f['w']})) v);")
                if f["swacc"]:
                    L += [f"  (* always_ready *) method Bool {s}_rd;"
                          f"   // 软件读过一次",
                          f"  (* always_ready *) method "
                          f"Bit#(TLog#(TAdd#({n}, 1))) {s}_rd_i;   // 读的是哪一个"]
                if f["swmod"]:
                    L += [f"  (* always_ready *) method Bool {s}_wr;"
                          f"   // 软件写过一次",
                          f"  (* always_ready *) method "
                          f"Bit#(TLog#(TAdd#({n}, 1))) {s}_wr_i;   // 写的是哪一个"]
                if f["hw"] in ("r", "rw") and not f["vol"]:
                    L.append(f"  (* always_ready *) method Vector#({n}, Bit#({f['w']})) {s};")
                if f["hw"] in ("w", "rw") and not f["vol"]:
                    # 数组的硬件写要带下标。timer 的捕获寄存器逼出了这一处：
                    # 「数组 + 硬件写」两个键各自合法，组合却一直没实现。
                    L.append(f"  (* always_ready *) method Action {s}_in("
                             f"Bit#(TLog#(TAdd#({n}, 1))) i, Bit#({f['w']}) v);")
                continue
            if f["hw"] in ("r", "rw") and not f["vol"]:
                L.append(f"  (* always_ready *) method Bit#({f['w']}) {s};")
            if f["hw"] in ("w", "rw"):
                L.append(f"  (* always_ready *) method Action {s}_in(Bit#({f['w']}) v);")
            if f["hwset"]:
                L.append(f"  (* always_ready *) method Action {s}_set(Bit#({f['w']}) v);")
            if f["swacc"]:
                L.append(f"  (* always_ready *) method Bool {s}_rd;   // 软件读过一次")
            if f["swmod"]:
                L.append(f"  (* always_ready *) method Bool {s}_wr;   // 软件写过一次")
    L += ["endinterface", ""]

    cfgarg = f"#({C}RegsCfg cfg)" if feats else ""
    L.append(f"module mk{C}Regs{cfgarg}({C}RegsIfc#({targs}))")
    prov = ["Mul#(TDiv#(dw, 8), 8, dw)", f"Add#(_a, {aw}, aw)"]
    # zeroExtend 到 dw 的每个位宽都要一条 proviso，字面值也不例外
    widths = sorted({f["w"] for r in rs for f in r["fields"]
                     if r["rw"] <= dw_i},
                    key=lambda w: (w.isdigit(), w))
    for i, w in enumerate(widths):
        prov.append(f"Add#(_w{i}, {w}, dw)")
    if any(r["rw"] > dw_i for r in rs):
        prov.append(f"Add#(_h, {dw_i}, dw)")
    # 数组的读写脉冲要把偏移截成下标，得声明下标放得下
    seen_idx = set()
    for r in rs:
        for fl in r["fields"]:
            if r["arr"] and (fl["swacc"] or fl["swmod"]):
                n = r["arr"]["count"]
                if n not in seen_idx:
                    prov.append(f"Add#(_i{len(seen_idx)}, "
                                f"TLog#(TAdd#({n}, 1)), {aw})")
                    seen_idx.add(n)
    L += ["    provisos (" + ", ".join(prov) + ");", ""]

    for reg in rs:
        for f in reg["fields"]:
            s = _sig(reg, f) + "_r"
            init = "0" if f["reset"] is None else str(f["reset"])
            # 先看是不是数组，再看有没有存储——两者可以同时成立
            # （mbox 的硬件自旋锁就是「每把锁一份、值由硬件驱动」）。
            # 白名单管得住不认识的键，管不住不支持的组合，这一处是组合漏了。
            if reg["arr"]:
                n = reg["arr"]["count"]
                if f["vol"]:
                    L.append(f"  Vector#({n}, Wire#(Bit#({f['w']}))) {s} <- "
                             f"replicateM(mkDWire(0));")
                elif dual(f):
                    # 数组一样要定序。少了这一步，规则与总线方法同写一个元素，
                    # bsc 判定规则永不触发——mbox 的门铃就是这么不响的。
                    L.append(f"  Vector#({n}, Array#(Reg#(Bit#({f['w']})))) {s} <- "
                             f"replicateM(mkCReg(2, {init}));")
                else:
                    L.append(f"  Vector#({n}, Reg#(Bit#({f['w']}))) {s} <- "
                             f"replicateM(mkReg({init}));")
                continue
            if f["vol"]:
                # 没有存储：硬件每拍驱动，总线只是读它
                L.append(f"  Wire#(Bit#({f['w']})) {s} <- mkDWire(0);")
                continue
            if dual(f):
                # 两处写，须 CReg 定序：端口0给规则、端口1给总线方法
                L.append(f"  Reg#(Bit#({f['w']})) {s}[2] <- mkCReg(2, {init});")
            else:
                L.append(f"  Reg#(Bit#({f['w']})) {s} <- mkReg({init});")
    for reg in rs:
        if reg["atomic"] == "latch-on-low":
            # 读低半时把高半锁进影子，读高半返回影子——否则两次读之间计数器会走，读出撕裂值
            L.append(f"  Reg#(Bit#({dw_i})) {reg['name']}_shadow <- mkReg(0);")
    for reg in rs:
        for f in reg["fields"]:
            s = _sig(reg, f)
            if f["swacc"]:
                L.append(f"  PulseWire {s}_acc <- mkPulseWire;")
                if reg["arr"]:
                    L.append(f"  Wire#(Bit#(TLog#(TAdd#({reg['arr']['count']}, 1))))"
                             f" {s}_acc_i <- mkDWire(0);")
            if f["swmod"]:
                L.append(f"  PulseWire {s}_mod <- mkPulseWire;")
                if reg["arr"]:
                    L.append(f"  Wire#(Bit#(TLog#(TAdd#({reg['arr']['count']}, 1))))"
                             f" {s}_mod_i <- mkDWire(0);")
    L.append("")

    def _fld_read(reg, f, ix=""):
        # 只写字段读回零。存是要存的（硬件那一侧要用），但声明说了软件读不到，
        # 那就不能把写进去的值漏回去——不然 sw: w 这个声明等于没写。
        if f["sw"] == "w":
            return "0"
        e = f"(zeroExtend({port(reg, f, 1, ix)}) << {f['lo']})"
        # 字段级 feature：关掉时该位读回 0，寄存器随之被优化掉
        if f["feat"] and f["feat"] != reg["feat"]:
            return f"(cfg.{f['feat']} ? {e} : 0)"
        return e

    def read_expr(reg, ix=""):
        if not reg["multi"]:
            f = reg["fields"][0]
            if f["sw"] == "w":
                return "0"
            return f"zeroExtend({port(reg, f, 1, ix)})"
        parts = [_fld_read(reg, f, ix) for f in reg["fields"]]
        return " |\n                      ".join(parts)

    L += ["  RegIf#(aw, dw) rf = interface RegIf;",
          "    method ActionValue#(RegRsp#(dw)) access(RegReq#(aw, dw) r);",
          "      Bit#(dw) rd = 0;", "      Bool err = True;   // 先假定未命中",
          f"      Bit#({aw}) off = truncate(r.addr);", "      Bit#(dw) wd = r.wdata;"]
    # 数组与宽寄存器：份数可能是参数，Python 展不开，只能生成动态索引
    for reg in [x for x in rs if x["arr"] or x["rw"] > dw_i]:
        f = reg["fields"][0]
        base = reg["offset"]
        words = reg["rw"] // dw_i
        stride = reg["arr"]["stride"] if reg["arr"] else reg["rw"] // 8
        cnt = reg["arr"]["count"] if reg["arr"] else 1
        span = f"fromInteger(valueOf({cnt}))*{stride}" if isinstance(cnt, str) else f"{cnt*stride}"
        idx = f"((off - {aw}'h{base:0{hexw}X}) / {stride})"
        sub = (f"((off - {aw}'h{base:0{hexw}X}) % {stride})"
               if words > 1 or reg["arr"] else "0")
        # 总线走 CReg 的端口 1，规则走端口 0——软硬双写的字段靠这个定序
        ix = f"[{idx}]" if reg["arr"] else ""
        tgt = port(reg, f, 1, ix)
        # 步长比元素宽的数组，元素只占步长的头一段——只查外层范围的话，
        # 它会把整个步长都吃掉。PLIC 的 thresh（步长 4096）与 claim（base+4，
        # 同样步长 4096）就是这么撞在一起的：写 claim 实际写进了 thresh。
        elemBytes = (reg["rw"] // 8) if reg["arr"] else stride
        inElem = (f" && {sub} < {elemBytes}"
                  if reg["arr"] and stride > elemBytes else "")
        # 特性关掉的数组要跟标量一样整个消失。这条路径原来根本没查 feature，
        # 于是 rtc 的 alarm、aclint 的 ssip 在特性关掉时照样能读能写——
        # 而一致性测试只走标量寄存器，正好看不见。
        gate = f" && cfg.{reg['feat']}" if reg["feat"] else ""
        L += [f"      if (off >= {aw}'h{base:0{hexw}X} && "
              f"off < {aw}'h{base:0{hexw}X} + {span}{inElem}{gate}) begin",
              "        err = False;"]
        if words > 1 and reg["multi"]:
            raise Bad(f"{reg['name']}：比总线宽的寄存器本版只支持单字段")
        if words == 1 and reg["multi"]:
            # 数组也可以是多字段的（pinmux 的 pad 有五个）。原来这条路径只译
            # fields[0]，另外四个字段有寄存器、有方法，总线上却根本访问不到。
            L += [f"        Bit#(dw) cur = {read_expr(reg, ix)};",
                  "        Bit#(dw) nw = applyStrb(cur, wd, r.wstrb);",
                  "        if (r.write) begin"]
            for g in reg["fields"]:
                if g["sw"] in ("rw", "w") and not g["vol"]:
                    asn = f"{port(reg, g, 1, ix)} <= nw[{g['hi']}:{g['lo']}];"
                    if g["feat"] and g["feat"] != reg["feat"]:
                        L.append(f"          if (cfg.{g['feat']}) {asn}")
                    else:
                        L.append(f"          {asn}")
                if g["swmod"]:
                    L.append(f"          {_sig(reg, g)}_mod.send();")
                    L.append(f"          {_sig(reg, g)}_mod_i <= truncate({idx});")
            L += ["        end else begin", "          rd = cur;"]
            for g in reg["fields"]:
                if g["swacc"]:
                    L.append(f"          {_sig(reg, g)}_acc.send();")
                    L.append(f"          {_sig(reg, g)}_acc_i <= truncate({idx});")
            L += ["        end"]
        elif words == 1:
            if f["vol"]:
                L += [f"        rd = zeroExtend({tgt});   // 硬件驱动，总线只读"]
            elif f["onread"]:
                v = "'1" if f["onread"] == "rset" else "0"
                L += [f"        rd = zeroExtend({tgt});",
                      f"        if (r.write) {tgt} <= truncate(wd);",
                      f"        else {tgt} <= {v};"]
            elif f["sw"] == "w":
                L += [f"        if (r.write) {tgt} <= truncate(wd);"]
            else:
                L += [f"        if (r.write) {tgt} <= truncate(wd);",
                      f"        else rd = zeroExtend({tgt});"]
            ix = f" {_sig(reg, f)}_acc_i <= truncate({idx});" if reg["arr"] else ""
            if f["swacc"]:
                L += [f"        if (!r.write) begin"
                      f" {_sig(reg, f)}_acc.send();{ix} end"]
            ix = f" {_sig(reg, f)}_mod_i <= truncate({idx});" if reg["arr"] else ""
            if f["swmod"]:
                L += [f"        if (r.write) begin"
                      f" {_sig(reg, f)}_mod.send();{ix} end"]
        else:
            hi = f"{tgt}[{reg['rw']-1}:{dw_i}]"
            lo = f"{tgt}[{dw_i-1}:0]"
            # 寄存器是字面宽度，总线是类型参数，边界上必须过渡
            wdn = f"Bit#({dw_i})' (truncate(wd))"
            L += [f"        Bool isHi = ({sub} >= {dw_i//8});",
                  "        if (r.write) begin",
                  f"          if (isHi) {tgt} <= {{{wdn}, {lo}}};",
                  f"          else      {tgt} <= {{{hi}, {wdn}}};",
                  "        end else begin"]
            if reg["atomic"] == "latch-on-low":
                L += [f"          if (isHi) rd = zeroExtend({reg['name']}_shadow);",
                      f"          else begin rd = zeroExtend({lo});"
                      f" {reg['name']}_shadow <= {hi}; end"]
            else:
                L += [f"          rd = isHi ? zeroExtend({hi}) : zeroExtend({lo});"]
            L += ["        end"]
        L += ["      end"]
    L += ["      case (off)"]
    for reg in rs:
        if reg["arr"] or reg["rw"] > dw_i:
            continue
        body = []
        if reg["multi"]:
            # 先拼当前值、套完字节选通再切回各字段——逐字段套选通会算错，
            # 因为选通按字节给，字段边界不一定对齐字节。
            body.append(f"Bit#(dw) cur = {read_expr(reg)};")
            body.append("Bit#(dw) nw = applyStrb(cur, wd, r.wstrb);")
            body.append("if (r.write) begin")
            for f in reg["fields"]:
                if f["sw"] in ("rw", "w") and not f["vol"]:
                    tgt = port(reg, f, 1)
                    asn = f"{tgt} <= nw[{f['hi']}:{f['lo']}];"
                    if f["feat"] and f["feat"] != reg["feat"]:
                        body.append(f"  if (cfg.{f['feat']}) {asn}")
                    else:
                        body.append(f"  {asn}")
                if f["swmod"]:
                    body.append(f"  {_sig(reg, f)}_mod.send();")
            body.append("end else begin")
            body.append("  rd = cur;")
            for f in reg["fields"]:
                if f["swacc"]:
                    body.append(f"  {_sig(reg, f)}_acc.send();")
            body.append("end")
        else:
            f = reg["fields"][0]
            wr = port(reg, f, 1)
            if f["woclr"]:
                body = [f"if (r.write) {wr} <= {wr} & ~truncate(wd);",
                        f"else rd = zeroExtend({wr});"]
            elif f["sw"] == "rw":
                body = [f"if (r.write) {wr} <= truncate(applyStrb(zeroExtend({wr}), wd, r.wstrb));",
                        f"else rd = zeroExtend({wr});"]
            elif f["sw"] == "r":
                body = [f"rd = zeroExtend({wr});"]
            elif f["onread"]:
                # 读回旧值，同一拍把字段置位（取锁）或清零（读后清）
                v = "'1" if f["onread"] == "rset" else "0"
                body = [f"rd = zeroExtend({wr});",
                        f"if (r.write) {wr} <= truncate(applyStrb(zeroExtend({wr}), wd, r.wstrb));",
                        f"else {wr} <= {v};"]
            else:
                body = [f"if (r.write) {wr} <= truncate(applyStrb(zeroExtend({wr}), wd, r.wstrb));"]
            # 单字段寄存器也要发脉冲。之前只有多字段那一支发了，于是
            # wdt 的喂狗、i2c 的收发、emac 的收发长度全都收不到通知——
            # 硬件那一侧永远等不到「软件写过了」。
            if f["swmod"]:
                body.append(f"if (r.write) {_sig(reg, f)}_mod.send();")
            if f["swacc"]:
                body.append(f"if (!r.write) {_sig(reg, f)}_acc.send();")
        arm = "\n               ".join(body)
        if reg["feat"]:
            L += [f"        {aw}'h{reg['offset']:0{hexw}X}: if (cfg.{reg['feat']}) begin",
                  "                 err = False;",
                  f"                 {arm}", "               end else err = True;"]
        else:
            L.append(f"        {aw}'h{reg['offset']:0{hexw}X}: begin err = False; {arm} end")
    L += ["        default: noAction;", "      endcase",
          "      return RegRsp { rdata: rd, err: err };",
          "    endmethod", "  endinterface;", "", "  interface regs = rf;"]

    for reg in rs:
        for f in reg["fields"]:
            s = _sig(reg, f)
            if reg["arr"]:
                n = reg["arr"]["count"]
                el = f"{s}_r[i][0]" if dual(f) else f"{s}_r[i]"
                if f["vol"] and f["hw"] in ("w", "rw"):
                    L += [f"  method Action {s}_in(Vector#({n}, Bit#({f['w']})) v);",
                          f"    for (Integer i = 0; i < valueOf({n}); i = i + 1)",
                          f"      {s}_r[i] <= v[i];",
                          "  endmethod"]
                if f["swacc"]:
                    L += [f"  method Bool {s}_rd = {s}_acc;",
                          f"  method {s}_rd_i = {s}_acc_i;"]
                if f["swmod"]:
                    L += [f"  method Bool {s}_wr = {s}_mod;",
                          f"  method {s}_wr_i = {s}_mod_i;"]
                if f["hw"] in ("r", "rw") and not f["vol"]:
                    if dual(f):
                        L += [f"  method Vector#({n}, Bit#({f['w']})) {s};",
                              f"    Vector#({n}, Bit#({f['w']})) o = newVector;",
                              f"    for (Integer i = 0; i < valueOf({n}); i = i + 1)",
                              f"      o[i] = {s}_r[i][0];",
                              "    return o;", "  endmethod"]
                    else:
                        L.append(f"  method {s} = readVReg({s}_r);")
                if f["hw"] in ("w", "rw") and not f["vol"]:
                    L += [f"  method Action {s}_in("
                          f"Bit#(TLog#(TAdd#({n}, 1))) i, Bit#({f['w']}) v);",
                          f"    {el} <= v;",
                          "  endmethod"]
                continue
            if f["hw"] in ("r", "rw") and not f["vol"]:
                L.append(f"  method Bit#({f['w']}) {s} = {port(reg, f, 0)};")
            if f["hw"] in ("w", "rw"):
                L.append(f"  method Action {s}_in(Bit#({f['w']}) v);"
                         f" {port(reg, f, 0)} <= v; endmethod")
            if f["hwset"]:
                L.append(f"  method Action {s}_set(Bit#({f['w']}) v);"
                         f" {port(reg, f, 0)} <= {port(reg, f, 0)} | v; endmethod")
            if f["swacc"]:
                L.append(f"  method Bool {s}_rd = {s}_acc;")
            if f["swmod"]:
                L.append(f"  method Bool {s}_wr = {s}_mod;")
    L += ["endmodule", "", "endpackage"]
    return "\n".join(L) + "\n"


def header(pkg: Pkg) -> str:
    spec = pkg.regmap
    ip = spec.get("ip", pkg.name)
    U = ip.upper()
    L = ["/* 由 regmap.yaml 生成，勿手改。 */", f"#ifndef {U}_H", f"#define {U}_H", "",
         f"#define {U}_BASE {spec['base']}"]
    for r in spec.get("regs", []) or []:
        off = int(str(r["offset"]), 0)
        c = f"  /* {r['desc']} */" if r.get("desc") else ""
        L.append(f"#define {U}_{r['name'].upper()} {off:#06x}{c}")
    L += ["", "#endif", ""]
    return "\n".join(L)


def generate(pkg: Pkg, bsv_dir: pathlib.Path, sw_dir: pathlib.Path):
    if not pkg.regmap:
        return
    bsv_dir.mkdir(parents=True, exist_ok=True)
    sw_dir.mkdir(parents=True, exist_ok=True)
    ip = pkg.regmap.get("ip", pkg.name)
    (bsv_dir / f"{_cap(ip)}Regs.bsv").write_text(bsv(pkg), encoding="utf-8")
    (sw_dir / f"{ip}.h").write_text(header(pkg), encoding="utf-8")
