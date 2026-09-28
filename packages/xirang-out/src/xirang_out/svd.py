"""导出 CMSIS-SVD 1.3：整颗芯片的外设、寄存器与字段。

寿木 SDK 的 Rust 与 Zig 两支都从这一份来：`svd2rust` 出 PAC，microzig 的 `regz`
出 Zig 绑定。与 `.rdl` 一样导的是已解出配置，关掉的字段按构造不存在。
"""
from xml.sax.saxutils import escape

from .chip import devices
from .regview import read
from .target import Ctx, target

ACCESS = {"rw": "read-write", "r": "read-only", "w": "write-only"}


def _field(f: dict) -> list[str]:
    L = ["          <field>",
         f"            <name>{escape(f['name'].upper())}</name>"]
    if f.get("desc"):
        L.append(f"            <description>{escape(str(f['desc']))}</description>")
    L += [f"            <bitOffset>{f['lo']}</bitOffset>",
          f"            <bitWidth>{f['w']}</bitWidth>",
          f"            <access>{ACCESS.get(f.get('sw') or 'rw', 'read-write')}</access>"]
    if f.get("woclr"):
        L.append("            <modifiedWriteValues>oneToClear</modifiedWriteValues>")
    if f.get("onread") == "rclr":
        L.append("            <readAction>clear</readAction>")
    elif f.get("onread") == "rset":
        L.append("            <readAction>set</readAction>")
    L.append("          </field>")
    return L


def _register(r) -> list[str]:
    reset = sum((int(f["reset"] or 0) << f["lo"]) for f in r.fields)
    acc = {ACCESS.get(f.get("sw") or "rw", "read-write") for f in r.fields}
    L = ["      <register>",
         f"        <name>{escape(r.name.upper())}</name>"]
    if r.desc:
        L.append(f"        <description>{escape(r.desc)}</description>")
    L += [f"        <addressOffset>{r.offset:#x}</addressOffset>",
          f"        <size>{r.width}</size>",
          f"        <access>{acc.pop() if len(acc) == 1 else 'read-write'}</access>",
          f"        <resetValue>{reset:#x}</resetValue>",
          "        <fields>"]
    for f in r.fields:
        L += _field(f)
    L += ["        </fields>", "      </register>"]
    return L


@target(name="svd", ext=".svd", desc="CMSIS-SVD 1.3，svd2rust 与 regz 的输入", needs=("regmap",))
def to_svd(ctx: Ctx) -> str:
    top = ctx.pkgs[ctx.res.top]
    ident = top.ip.get("identity") or {}
    L = ['<?xml version="1.0" encoding="utf-8"?>',
         "<!-- 由 xirang 生成，勿手改。导的是已解出的那一份配置 -->",
         '<device schemaVersion="1.3" xmlns:xs="http://www.w3.org/2001/XMLSchema-instance"'
         ' xs:noNamespaceSchemaLocation="CMSIS-SVD.xsd">',
         "  <vendor>Tape-Out</vendor>",
         f"  <name>{escape(ctx.res.top.upper().replace('-', '_'))}</name>",
         f"  <version>{escape(str(top.ip.get('version', '0.0.0')))}</version>",
         f"  <description>{escape(ident.get('summary') or ctx.res.top)}</description>",
         "  <addressUnitBits>8</addressUnitBits>",
         "  <width>32</width>",
         "  <size>32</size>",
         "  <peripherals>"]
    for d in devices(ctx):
        regs, head = read(d.pkg, d.knobs)
        if not regs or d.addr is None:
            continue
        L += ["    <peripheral>",
              f"      <name>{escape(d.name.upper().replace('-', '_'))}</name>",
              f"      <groupName>{escape(d.of.upper().replace('-', '_'))}</groupName>",
              f"      <baseAddress>{d.addr:#x}</baseAddress>",
              "      <addressBlock>",
              "        <offset>0x0</offset>",
              f"        <size>{(head.get('size') or d.size):#x}</size>",
              "        <usage>registers</usage>",
              "      </addressBlock>",
              "      <registers>"]
        for r in regs:
            L += _register(r)
        L += ["      </registers>", "    </peripheral>"]
    L += ["  </peripherals>", "</device>", ""]
    return "\n".join(L)
