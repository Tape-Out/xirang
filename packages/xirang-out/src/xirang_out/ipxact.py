"""导出 IP-XACT（IEEE 1685-2014）的寄存器视图。

只导寄存器视图（`memoryMaps`）与身份，不导 `busInterfaces` 与抽象定义——
那一层我们的契约是总线中立的，硬塞一个总线定义进去就是在编。

自己写而不是靠 PeakRDL-ipxact 转，是为了 `ran export -f ipxact` 开箱即用，
不要求装一个 LGPL-3.0 的工具。两条路的产物可以互相对账，那是 CI 的事。
"""
import xml.etree.ElementTree as ET

from .regview import read
from .target import Ctx, target

NS = "http://www.accellera.org/XMLSchema/IPXACT/1685-2014"
ACCESS = {"rw": "read-write", "r": "read-only", "w": "write-only"}


def _e(parent, tag: str, text=None):
    x = ET.SubElement(parent, f"{{{NS}}}{tag}")
    if text is not None:
        x.text = str(text)
    return x


def _field(parent, f: dict) -> None:
    x = _e(parent, "field")
    _e(x, "name", f["name"])
    _e(x, "bitOffset", f["lo"])
    if f.get("reset") is not None:
        r = _e(x, "resets")
        rr = _e(r, "reset")
        _e(rr, "value", f"'h{int(f['reset']):X}")
    _e(x, "bitWidth", f["w"])
    _e(x, "access", ACCESS.get(f["sw"], "read-write"))
    if f.get("woclr"):
        _e(x, "modifiedWriteValue", "oneToClear")
    if f.get("onread") == "rclr":
        _e(x, "readAction", "clear")
    elif f.get("onread") == "rset":
        _e(x, "readAction", "set")


def _block(parent, name: str, base: int, head: dict, regs) -> None:
    b = _e(parent, "addressBlock")
    _e(b, "name", name)
    _e(b, "baseAddress", f"'h{base:X}")
    _e(b, "range", f"'h{head.get('size') or (1 << head.get('aw', 8)):X}")
    _e(b, "width", head.get("dw", 32))
    # 次序照 IEEE 1685 的 schema：usage 在 register 之前，放后面校验器会拒
    _e(b, "usage", "register")
    for r in regs:
        x = _e(b, "register")
        _e(x, "name", r.name)
        if r.desc:
            _e(x, "description", r.desc)
        _e(x, "addressOffset", f"'h{r.offset:X}")
        _e(x, "size", r.width)
        for f in r.fields:
            _field(x, f)


@target(name="ipxact", ext=".xml", desc="IP-XACT (IEEE 1685-2014) 的寄存器视图",
        needs=("regmap",))
def to_ipxact(ctx: Ctx) -> str:
    ET.register_namespace("ipxact", NS)
    top = ctx.pkgs[ctx.res.top]
    root = ET.Element(f"{{{NS}}}component")
    _e(root, "vendor", "tape-out")
    _e(root, "library", (top.ip.get("identity") or {}).get("category") or "ip")
    _e(root, "name", ctx.res.top)
    _e(root, "version", top.ip.get("version", "0.1.0"))

    maps = _e(root, "memoryMaps")
    mm = _e(maps, "memoryMap")
    _e(mm, "name", "regs")

    n = 0
    for _, i in ctx.res.walk():
        p = ctx.pkgs.get(i.of)
        if p is None:
            continue
        regs, head = read(p, {k: v.value for k, v in i.values.items()})
        if regs:
            _block(mm, i.name, i.addr if i.addr is not None else head.get("base", 0),
                   head, regs)
            n += 1
    if n == 0:                                  # 叶子 IP
        regs, head = read(top, {})
        if regs:
            _block(mm, ctx.res.top, head.get("base", 0), head, regs)
    _e(mm, "addressUnitBits", 8)

    ET.indent(root, space="  ")
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            + ET.tostring(root, encoding="unicode") + "\n")
