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
FIELD_KEYS = {"name", "bits", "width", "desc", "sw", "hw", "onwrite", "hwset",
              "stickybit", "reset", "feature", "volatile", "swacc", "swmod"}
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

    def port(reg, f, p):
        s = _sig(reg, f) + "_r"
        return f"{s}[{p}]" if (f["hwset"] and f["woclr"]) else s

    L: list[str] = [f"package {C}Regs;", "",
                    "// 由 regmap.yaml 生成，勿手改。改 regmap.yaml 后重新生成。", "",
                    "import Apb4::*;", "import Vector::*;", ""]
    if feats:
        L.append("typedef struct {")
        L += [f"  Bool {f};" for f in feats]
        L += [f"}} {C}RegsCfg;", ""]

    L.append(f"interface {C}RegsIfc#({tparams});")
    L.append("  interface Apb4RegFile#(aw, dw) regs;")
    for reg in rs:
        for f in reg["fields"]:
            s = _sig(reg, f)
            if reg["arr"]:
                n = reg["arr"]["count"]
                if f["hw"] in ("r", "rw"):
                    L.append(f"  (* always_ready *) method Vector#({n}, Bit#({f['w']})) {s};")
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
    L += ["    provisos (" + ", ".join(prov) + ");", ""]

    for reg in rs:
        for f in reg["fields"]:
            s = _sig(reg, f) + "_r"
            init = "0" if f["reset"] is None else str(f["reset"])
            if f["vol"]:
                # 没有存储：硬件每拍驱动，总线只是读它
                L.append(f"  Wire#(Bit#({f['w']})) {s} <- mkDWire(0);")
                continue
            if reg["arr"]:
                n = reg["arr"]["count"]
                L.append(f"  Vector#({n}, Reg#(Bit#({f['w']}))) {s} <- "
                         f"replicateM(mkReg({init}));")
                continue
            if f["hwset"] and f["woclr"]:
                # 硬件置位与软件写1清除两处写，须 CReg 定序：端口0给规则、端口1给总线方法
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
            if f["swmod"]:
                L.append(f"  PulseWire {s}_mod <- mkPulseWire;")
    L.append("")

    def _fld_read(reg, f):
        e = f"(zeroExtend({port(reg, f, 1)}) << {f['lo']})"
        # 字段级 feature：关掉时该位读回 0，寄存器随之被优化掉
        if f["feat"] and f["feat"] != reg["feat"]:
            return f"(cfg.{f['feat']} ? {e} : 0)"
        return e

    def read_expr(reg):
        if not reg["multi"]:
            f = reg["fields"][0]
            return f"zeroExtend({port(reg, f, 1)})"
        parts = [_fld_read(reg, f) for f in reg["fields"]]
        return " |\n                      ".join(parts)

    L += ["  Apb4RegFile#(aw, dw) rf = interface Apb4RegFile;",
          "    method ActionValue#(Apb4Rsp#(dw)) access(Apb4Req#(aw, dw) r);",
          "      Bit#(dw) rd = 0;", "      Bool err = True;   // 先假定未命中",
          f"      Bit#({aw}) off = truncate(r.paddr);", "      Bit#(dw) wd = r.pwdata;"]
    # 数组与宽寄存器：份数可能是参数，Python 展不开，只能生成动态索引
    for reg in [x for x in rs if x["arr"] or x["rw"] > dw_i]:
        f = reg["fields"][0]
        base = reg["offset"]
        words = reg["rw"] // dw_i
        stride = reg["arr"]["stride"] if reg["arr"] else reg["rw"] // 8
        cnt = reg["arr"]["count"] if reg["arr"] else 1
        span = f"fromInteger(valueOf({cnt}))*{stride}" if isinstance(cnt, str) else f"{cnt*stride}"
        idx = f"((off - {aw}'h{base:0{hexw}X}) / {stride})"
        sub = f"((off - {aw}'h{base:0{hexw}X}) % {stride})" if words > 1 else "0"
        tgt = f"{_sig(reg, f)}_r[{idx}]" if reg["arr"] else _sig(reg, f) + "_r"
        L += [f"      if (off >= {aw}'h{base:0{hexw}X} && off < {aw}'h{base:0{hexw}X} + {span}) begin",
              "        err = False;"]
        if words == 1:
            L += [f"        if (r.pwrite) {tgt} <= truncate(wd);",
                  f"        else rd = zeroExtend({tgt});"]  # 两侧都已过渡
        else:
            hi = f"{tgt}[{reg['rw']-1}:{dw_i}]"
            lo = f"{tgt}[{dw_i-1}:0]"
            # 寄存器是字面宽度，总线是类型参数，边界上必须过渡
            wdn = f"Bit#({dw_i})' (truncate(wd))"
            L += [f"        Bool isHi = ({sub} >= {dw_i//8});",
                  "        if (r.pwrite) begin",
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
            body.append("Bit#(dw) nw = applyStrb(cur, wd, r.pstrb);")
            body.append("if (r.pwrite) begin")
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
                body = [f"if (r.pwrite) {wr} <= {wr} & ~truncate(wd);",
                        f"else rd = zeroExtend({wr});"]
            elif f["sw"] == "rw":
                body = [f"if (r.pwrite) {wr} <= truncate(applyStrb(zeroExtend({wr}), wd, r.pstrb));",
                        f"else rd = zeroExtend({wr});"]
            elif f["sw"] == "r":
                body = [f"rd = zeroExtend({wr});"]
            else:
                body = [f"if (r.pwrite) {wr} <= truncate(applyStrb(zeroExtend({wr}), wd, r.pstrb));"]
        arm = "\n               ".join(body)
        if reg["feat"]:
            L += [f"        {aw}'h{reg['offset']:0{hexw}X}: if (cfg.{reg['feat']}) begin",
                  "                 err = False;",
                  f"                 {arm}", "               end else err = True;"]
        else:
            L.append(f"        {aw}'h{reg['offset']:0{hexw}X}: begin err = False; {arm} end")
    L += ["        default: noAction;", "      endcase",
          "      return Apb4Rsp { prdata: rd, pslverr: err };",
          "    endmethod", "  endinterface;", "", "  interface regs = rf;"]

    for reg in rs:
        for f in reg["fields"]:
            s = _sig(reg, f)
            if reg["arr"]:
                if f["hw"] in ("r", "rw"):
                    L.append(f"  method {s} = readVReg({s}_r);")
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
