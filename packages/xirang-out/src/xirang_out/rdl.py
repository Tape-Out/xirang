"""导出 SystemRDL 2.0。

我们的寄存器语义内核本来就是 SystemRDL，属性名一个没改，所以这份导出基本是
换一层壳。**导的是已解出配置**：特性关掉的字段按构造不存在，位宽已经取值。

一个导出器把整条 PeakRDL 生态变成下游——IP-XACT、UVM 寄存器模型、HTML 文档、
C 头、SV regblock 都能从这份 `.rdl` 再生成。它们是 LGPL-3.0，当独立进程跑在
我们的输出上，不构成链接。

**不追求往返。** 与 OpenAPI 导 JSON Schema 一样，映射得上的单向导出即可。
`legal:`（WARL）SystemRDL 没有对应属性，发成用户自定义属性 `xr_legal`，
消费者不认就忽略，地址与字段不受影响。
"""
import json

from .regview import read
from .target import Ctx, target

HEAD = """// 由 xirang 生成，勿手改。导的是已解出的那一份配置：
// 关掉的特性在这里按构造不存在，参数已经取值。
// WARL 走用户自定义属性 xr_legal；SystemRDL 2.0 没有管取值范围的属性。

property xr_legal { type = string; component = field; };
"""


# SystemRDL 2.0 的保留字。我们的字段名撞上它就得转义——`i2c` 的 `wr` 是第一个。
# 转义用反斜杠，与 Verilog 同一招，解析回来还是原名。
KEYWORDS = frozenset("""
abstract accesswidth activehigh activelow addressing addrmap alias alignment all
anded arbiter async bigendian bothedge bridge clock compact component componentwidth
constraint counter cpuif_reset default desc encode enum errextbus external false
field field_reset fullalign halt haltenable haltmask hdl_path hdl_path_gate
hdl_path_gate_slice hdl_path_slice hw hwclr hwenable hwmask hwset incr incrsaturate
incrthreshold incrvalue incrwidth internal intr level littleendian lsb0 mask mem
msb0 na name negedge next nonsticky number onread onwrite ored overflow posedge
precedence property r ref reg regalign regfile regwidth reset resetsignal rclr rset
rsvdset rsvdsetX rw rw1 saturate shared sharedextbus signal signalwidth singlepulse
sticky stickybit string struct sw swacc swmod swwe swwel sync threshold true
underflow we wel woclr woset wot wr wzc wzs wzt xored
""".split())


def _id(name: str) -> str:
    return "\\" + name if name in KEYWORDS else name

SW = {"rw": "rw", "r": "r", "w": "w"}
HW = {"rw": "rw", "r": "r", "w": "w", "na": "na"}


def _q(x: str) -> str:
    """RDL 的字符串字面量：内层引号要转义，不然生成的是坏语法。"""
    return x.replace("\\", "\\\\").replace('"', '\\"')


def _field(f: dict) -> str:
    p = [f"sw = {SW.get(f['sw'], 'rw')};", f"hw = {HW.get(f['hw'], 'na')};"]
    if f.get("woclr"):
        p.append("onwrite = woclr;")
    if f.get("onread") in ("rclr", "rset"):
        p.append(f"onread = {f['onread']};")
    if f.get("swmod"):
        p.append("swmod = true;")
    if f.get("swacc"):
        p.append("swacc = true;")
    if f.get("hwset"):
        p.append("hwset = true;")
    if f.get("legal"):
        p.append(f'xr_legal = "{_q(json.dumps(f["legal"], separators=(",", ":")))}";')
    body = " ".join(p)
    rst = f" = {int(f['reset'])}" if f.get("reset") is not None else ""
    return f"field {{ {body} }} {_id(f['name'])}[{f['hi']}:{f['lo']}]{rst};"


def _regs(regs) -> list[str]:
    L = []
    for r in regs:
        L.append("  reg {")
        if r.desc:
            L.append(f'    desc = "{_q(r.desc)}";')
        L.append(f"    regwidth = {r.width};")
        for f in r.fields:
            L.append("    " + _field(f))
        L.append(f"  }} {_id(r.name)} @ {r.offset:#x};")
    return L


def _one(name: str, pkg, knobs: dict, disp: str) -> list[str]:
    regs, head = read(pkg, knobs)
    if not regs:
        return []
    L = [f"addrmap {_id(name)} {{"]
    if disp:
        L.append(f'  name = "{disp}";')
    if head.get("size"):
        L.append(f"  // size {head['size']:#x}")
    L += _regs(regs)
    L.append("};")
    return L


@target(name="rdl", ext=".rdl", desc="SystemRDL 2.0，PeakRDL 全家的输入",
        needs=("regmap",))
def to_rdl(ctx: Ctx) -> str:
    L = [HEAD]
    tops = []
    insts = [i for _, i in ctx.res.walk()]
    solo = len(insts) == 1 and insts[0].of == ctx.res.top
    for i in insts:
        p = ctx.pkgs.get(i.of)
        if p is None:
            continue
        knobs = {k: v.value for k, v in i.values.items()}
        disp = (p.ip.get("identity") or {}).get("display_name", i.of)
        tname = i.name if solo else f"{i.name}_t"
        one = _one(tname, p, knobs, disp)
        if one:
            L += one + [""]
            tops.append((i.name, i.addr))
    if solo and tops:
        return "\n".join(L).rstrip() + "\n"

    top = ctx.pkgs[ctx.res.top]
    if not tops:                       # 叶子 IP：它自己就是那一份
        knobs = {}
        one = _one(ctx.res.top.replace("-", "_"), top, knobs,
                   (top.ip.get("identity") or {}).get("display_name", ctx.res.top))
        return "\n".join(L + one) + "\n"

    L.append(f"addrmap {ctx.res.top.replace('-', '_')} {{")
    for name, addr in tops:
        at = f" @ {addr:#x}" if addr is not None else ""
        L.append(f"  {_id(name)}_t {_id(name)}{at};")
    L.append("};")
    return "\n".join(L) + "\n"
