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
        if len(fs) != 1:
            raise Bad(f"{r['name']}: 本版每个寄存器只支持单字段")
        f = fs[0]
        if f.get("sw", "rw") not in SW:
            raise Bad(f"{r['name']}: sw={f.get('sw')} 不在 {sorted(SW)}")
        if f.get("hw", "na") not in HW:
            raise Bad(f"{r['name']}: hw={f.get('hw')} 不在 {sorted(HW)}")
        if f.get("onwrite") not in (None, "woclr"):
            raise Bad(f"{r['name']}: 本版 onwrite 只支持 woclr")
        out.append({
            "name": r["name"], "offset": off, "desc": r.get("desc", ""),
            "w": _width(f.get("width", 32), params),
            "sw": f.get("sw", "rw"), "hw": f.get("hw", "na"),
            "woclr": f.get("onwrite") == "woclr",
            "hwset": bool(f.get("hwset")), "reset": f.get("reset"),
            "feat": r.get("feature"),
        })
    return out


def bsv(pkg: Pkg) -> str:
    spec = pkg.regmap
    ip = spec.get("ip", pkg.name)
    params: list[str] = list(spec.get("params") or [])
    rs = _rows(spec, params)
    feats = sorted({r["feat"] for r in rs if r["feat"]})
    tparams = ", ".join(["numeric type aw", "numeric type dw"]
                        + [f"numeric type {p}" for p in params])
    targs = ", ".join(["aw", "dw"] + params)
    C = _cap(ip)

    def port(r, p):
        return f"{r['name']}_r[{p}]" if (r["hwset"] and r["woclr"]) else f"{r['name']}_r"

    L: list[str] = [f"package {C}Regs;", "",
                    "// 由 regmap.yaml 生成，勿手改。改 regmap.yaml 后重新生成。", "",
                    "import Apb4::*;", "import Vector::*;", ""]
    if feats:
        L.append("typedef struct {")
        L += [f"  Bool {f};" for f in feats]
        L += [f"}} {C}RegsCfg;", ""]

    L.append(f"interface {C}RegsIfc#({tparams});")
    L.append("  interface Apb4RegFile#(aw, dw) regs;")
    for r in rs:
        if r["hw"] in ("r", "rw"):
            L.append(f"  (* always_ready *) method Bit#({r['w']}) {r['name']};")
        if r["hw"] in ("w", "rw"):
            L.append(f"  (* always_ready *) method Action {r['name']}_in(Bit#({r['w']}) v);")
        if r["hwset"]:
            L.append(f"  (* always_ready *) method Action {r['name']}_set(Bit#({r['w']}) v);")
    L += ["endinterface", ""]

    cfgarg = f"#({C}RegsCfg cfg)" if feats else ""
    L.append(f"module mk{C}Regs{cfgarg}({C}RegsIfc#({targs}))")
    prov = ["Mul#(TDiv#(dw, 8), 8, dw)", "Add#(_a, 8, aw)"]
    for i, p in enumerate(sorted({r["w"] for r in rs if not r["w"].isdigit()})):
        prov.append(f"Add#(_w{i}, {p}, dw)")
    L += ["    provisos (" + ", ".join(prov) + ");", ""]

    for r in rs:
        init = "0" if r["reset"] is None else str(r["reset"])
        if r["hwset"] and r["woclr"]:
            # 硬件置位与软件写 1 清除两处写，须 CReg 定序：端口 0 给规则、端口 1 给总线方法
            L.append(f"  Reg#(Bit#({r['w']})) {r['name']}_r[2] <- mkCReg(2, {init});")
        else:
            L.append(f"  Reg#(Bit#({r['w']})) {r['name']}_r <- mkReg({init});")
    L.append("")

    L += ["  Apb4RegFile#(aw, dw) rf = interface Apb4RegFile;",
          "    method ActionValue#(Apb4Rsp#(dw)) access(Apb4Req#(aw, dw) r);",
          "      Bit#(dw) rd = 0;", "      Bool err = False;",
          "      Bit#(8) off = truncate(r.paddr);", "      Bit#(dw) wd = r.pwdata;",
          "      case (off)"]
    for r in rs:
        wr = port(r, 1)
        if r["woclr"]:
            body = [f"if (r.pwrite) {wr} <= {wr} & ~truncate(wd);",
                    f"else rd = zeroExtend({wr});"]
        elif r["sw"] == "rw":
            body = [f"if (r.pwrite) {wr} <= truncate(applyStrb(zeroExtend({wr}), wd, r.pstrb));",
                    f"else rd = zeroExtend({wr});"]
        elif r["sw"] == "r":
            body = [f"rd = zeroExtend({wr});"]
        else:
            body = [f"if (r.pwrite) {wr} <= truncate(applyStrb(zeroExtend({wr}), wd, r.pstrb));"]
        arm = "\n               ".join(body)
        if r["feat"]:
            L += [f"        8'h{r['offset']:02X}: if (cfg.{r['feat']}) begin",
                  f"                 {arm}", "               end else err = True;"]
        else:
            L.append(f"        8'h{r['offset']:02X}: {arm}")
    L += ["        default: err = True;", "      endcase",
          "      return Apb4Rsp { prdata: rd, pslverr: err };",
          "    endmethod", "  endinterface;", "", "  interface regs = rf;"]

    for r in rs:
        if r["hw"] in ("r", "rw"):
            L.append(f"  method Bit#({r['w']}) {r['name']} = {port(r, 0)};")
        if r["hw"] in ("w", "rw"):
            L.append(f"  method Action {r['name']}_in(Bit#({r['w']}) v);"
                     f" {port(r, 0)} <= v; endmethod")
        if r["hwset"]:
            L.append(f"  method Action {r['name']}_set(Bit#({r['w']}) v);"
                     f" {port(r, 0)} <= {port(r, 0)} | v; endmethod")
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
