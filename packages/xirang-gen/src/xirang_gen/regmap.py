"""regmap.yaml -> BSV 寄存器组 + C 头。

规范见 Tape-Out/spec 的 regmap.md。语义内核沿用 SystemRDL 2.0，属性名原样不改。
本版覆盖 gpio 竖切用得到的子集：sw · hw · onwrite(woclr) · hwset · stickybit · reset。

对拍结论（V1）：生成件与手写件时序结构 20/20 逐位一致，行为 5 配置 × 2 万拍零失配。
"""
from __future__ import annotations

import pathlib

from xirang_core.manifest import Bad, Pkg

SW = {"rw", "r", "w"}
HW = {"rw", "r", "w", "na"}


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


def _rows(spec: dict, params: list[str]) -> list[dict]:
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
        fs = r.get("fields") or []
        if not fs:
            raise Bad(f"{r['name']}: 没有字段")
        multi = len(fs) > 1
        if multi and any("bits" not in f for f in fs):
            raise Bad(f"{r['name']}: 多字段时每个字段都要写 bits")
        span = []
        flds = []
        for f in fs:
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
            })
        out.append({"name": r["name"], "offset": off, "desc": r.get("desc", ""),
                    "feat": r.get("feature"), "multi": multi, "fields": flds})
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
    rs = _rows(spec, params)
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
            if f["hw"] in ("r", "rw"):
                L.append(f"  (* always_ready *) method Bit#({f['w']}) {s};")
            if f["hw"] in ("w", "rw"):
                L.append(f"  (* always_ready *) method Action {s}_in(Bit#({f['w']}) v);")
            if f["hwset"]:
                L.append(f"  (* always_ready *) method Action {s}_set(Bit#({f['w']}) v);")
    L += ["endinterface", ""]

    cfgarg = f"#({C}RegsCfg cfg)" if feats else ""
    L.append(f"module mk{C}Regs{cfgarg}({C}RegsIfc#({targs}))")
    prov = ["Mul#(TDiv#(dw, 8), 8, dw)", f"Add#(_a, {aw}, aw)"]
    # zeroExtend 到 dw 的每个位宽都要一条 proviso，字面值也不例外
    widths = sorted({f["w"] for r in rs for f in r["fields"]},
                    key=lambda w: (w.isdigit(), w))
    for i, w in enumerate(widths):
        prov.append(f"Add#(_w{i}, {w}, dw)")
    L += ["    provisos (" + ", ".join(prov) + ");", ""]

    for reg in rs:
        for f in reg["fields"]:
            s = _sig(reg, f) + "_r"
            init = "0" if f["reset"] is None else str(f["reset"])
            if f["hwset"] and f["woclr"]:
                # 硬件置位与软件写1清除两处写，须 CReg 定序：端口0给规则、端口1给总线方法
                L.append(f"  Reg#(Bit#({f['w']})) {s}[2] <- mkCReg(2, {init});")
            else:
                L.append(f"  Reg#(Bit#({f['w']})) {s} <- mkReg({init});")
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
          "      Bit#(dw) rd = 0;", "      Bool err = False;",
          f"      Bit#({aw}) off = truncate(r.paddr);", "      Bit#(dw) wd = r.pwdata;",
          "      case (off)"]
    for reg in rs:
        body = []
        if reg["multi"]:
            # 先拼当前值、套完字节选通再切回各字段——逐字段套选通会算错，
            # 因为选通按字节给，字段边界不一定对齐字节。
            body.append(f"Bit#(dw) cur = {read_expr(reg)};")
            body.append("Bit#(dw) nw = applyStrb(cur, wd, r.pstrb);")
            body.append("if (r.pwrite) begin")
            for f in reg["fields"]:
                if f["sw"] in ("rw", "w"):
                    tgt = port(reg, f, 1)
                    asn = f"{tgt} <= nw[{f['hi']}:{f['lo']}];"
                    if f["feat"] and f["feat"] != reg["feat"]:
                        body.append(f"  if (cfg.{f['feat']}) {asn}")
                    else:
                        body.append(f"  {asn}")
            body.append("end else rd = cur;")
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
                  f"                 {arm}", "               end else err = True;"]
        else:
            L.append(f"        {aw}'h{reg['offset']:0{hexw}X}: begin {arm} end")
    L += ["        default: err = True;", "      endcase",
          "      return Apb4Rsp { prdata: rd, pslverr: err };",
          "    endmethod", "  endinterface;", "", "  interface regs = rf;"]

    for reg in rs:
        for f in reg["fields"]:
            s = _sig(reg, f)
            if f["hw"] in ("r", "rw"):
                L.append(f"  method Bit#({f['w']}) {s} = {port(reg, f, 0)};")
            if f["hw"] in ("w", "rw"):
                L.append(f"  method Action {s}_in(Bit#({f['w']}) v);"
                         f" {port(reg, f, 0)} <= v; endmethod")
            if f["hwset"]:
                L.append(f"  method Action {s}_set(Bit#({f['w']}) v);"
                         f" {port(reg, f, 0)} <= {port(reg, f, 0)} | v; endmethod")
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
