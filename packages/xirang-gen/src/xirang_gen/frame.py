"""五口顶层：把设计的端口排进 MPC-Frame 的 66 位 payload。

契约照 `mpc-frame/docs/cn/io-map.md`：`io_oe[n]` 为 1 时设计驱动 `io_out[n]`，为 0 时
释放；每一位都要有确定值。端口怎么排只看清单的 `asic.pads`，不按名字猜——gpio 叫
`gpio_dir`、spi 叫 `io_oe`、i2c 是开漏的 `scl_pull`，猜不齐。
"""
import dataclasses
import fnmatch

from xirang_core.manifest import Bad

WIDTH = 66


@dataclasses.dataclass
class Slot:
    bit: int
    kind: str                      # in | out | io | od
    i: tuple[str, int] | None = None
    o: tuple[str, int] | None = None
    oe: tuple[str, int] | None = None

    def signal(self) -> str:
        refs = [r for r in (self.i, self.o, self.oe) if r]
        return " / ".join(f"{p}[{b}]" for p, b in refs)


@dataclasses.dataclass
class Plan:
    slots: list[Slot]
    tie: dict[str, int]
    unused: list[str]
    clock: str
    reset: str
    reset_low: bool


def _match(pat: str, ports, want: str | None, what: str) -> list:
    got = [p for p in ports if fnmatch.fnmatchcase(p.name, pat)]
    if not got:
        raise Bad(f"XR-ASIC-004 {what} 写的 {pat} 一个端口都没匹配到")
    if want:
        bad = [p.name for p in got if p.dir != want]
        if bad:
            raise Bad(f"XR-ASIC-004 {what} 的 {pat} 匹配到了不是 {want} 的端口 {bad[:4]}")
    return got


def plan(spec: dict, ports, clock: str, reset: str, reset_low: bool,
         width: int = WIDTH) -> Plan:
    """按清单排位。排不下、重叠、漏掉、写错，一律在写任何文件之前报。"""
    by = {p.name: p for p in ports}
    for n in (clock, reset):
        if n not in by:
            raise Bad(f"XR-ASIC-004 设计没有端口 {n}（时钟或复位）")
    body = [p for p in ports if p.name not in (clock, reset)]
    owner: dict[str, str] = {}

    def own(name: str, where: str):
        if name in owner:
            raise Bad(f"XR-ASIC-004 端口 {name} 同时落在 {owner[name]} 与 {where}")
        owner[name] = where

    slots: list[Slot] = []
    taken: dict[int, str] = {}
    cur = 0

    def put(at, n, make, what):
        nonlocal cur
        start = cur if at is None else at
        if start + n > width:
            raise Bad(f"XR-ASIC-002 {what} 要占 {start}..{start + n - 1}，payload 只有 {width} 位")
        for b in range(start, start + n):
            if b in taken:
                raise Bad(f"XR-ASIC-002 第 {b} 位同时分给了 {taken[b]} 与 {what}")
            taken[b] = what
            slots.append(make(b, b - start))
        cur = start + n

    for k, e in enumerate(spec.get("pads") or []):
        if isinstance(e, str):
            e = {"port": e}
        at = e.get("at")
        if "port" in e:
            for p in _match(e["port"], body, None, f"pads 第 {k + 1} 条"):
                if p.dir == "inout":
                    raise Bad(f"XR-ASIC-004 {p.name} 是 inout，要拆成 in/out/oe 三个端口再排")
                own(p.name, "pads")
                kind = "in" if p.dir == "input" else "out"
                put(at, p.width, lambda b, j, p=p, kind=kind: Slot(
                    b, kind, i=(p.name, j) if kind == "in" else None,
                    o=(p.name, j) if kind == "out" else None), p.name)
                at = None
            continue
        grp = {r: e[r] for r in ("in", "out", "oe") if e.get(r)}
        if "oe" not in grp:
            raise Bad(f"XR-ASIC-004 pads 第 {k + 1} 条是三态组，却没写 oe")
        got = {}
        for r, n in grp.items():
            if n not in by:
                raise Bad(f"XR-ASIC-004 pads 第 {k + 1} 条的 {r} 指向不存在的端口 {n}")
            want = "input" if r == "in" else "output"
            if by[n].dir != want:
                raise Bad(f"XR-ASIC-004 pads 第 {k + 1} 条的 {r} 要是 {want}，{n} 是 {by[n].dir}")
            got[r] = by[n]
        ws = {p.width for p in got.values()}
        if len(ws) != 1:
            raise Bad(f"XR-ASIC-004 pads 第 {k + 1} 条三态组位宽不一："
                      + ", ".join(f"{r}={p.name}[{p.width}]" for r, p in got.items()))
        for r, p in got.items():
            own(p.name, "pads")
        kind = "io" if "out" in got else "od"
        put(at, ws.pop(), lambda b, j: Slot(
            b, kind, i=(got["in"].name, j) if "in" in got else None,
            o=(got["out"].name, j) if "out" in got else None,
            oe=(got["oe"].name, j)), "/".join(p.name for p in got.values()))

    tie: dict[str, int] = {}
    for pat, v in (spec.get("tie") or {}).items():
        for p in _match(pat, body, "input", "tie"):
            own(p.name, "tie")
            if not isinstance(v, int) or isinstance(v, bool) or not 0 <= v < (1 << p.width):
                raise Bad(f"XR-ASIC-004 tie 给 {p.name} 的 {v!r} 放不进 {p.width} 位")
            tie[p.name] = v
    unused: list[str] = []
    for pat in spec.get("unused") or []:
        for p in _match(pat, body, "output", "unused"):
            own(p.name, "unused")
            unused.append(p.name)

    left = [p.name for p in body if p.name not in owner]
    if left:
        raise Bad(f"XR-ASIC-003 {len(left)} 个端口没有处置（pads、tie、unused 三选一）："
                  + ", ".join(left[:8]) + (" …" if len(left) > 8 else ""))
    slots.sort(key=lambda s: s.bit)
    return Plan(slots, tie, unused, clock, reset, reset_low)


def _ref(port, bit: int, width: int) -> str:
    return port if width == 1 else f"{port}[{bit}]"


def _cat(items: list[str]) -> str:
    """高位在前的一串位表达式：同一信号的连续位合成切片，连着的同一常量合成一段。"""
    runs: list[list] = []
    for x in items:
        base, _, idx = x.partition("[")
        if idx:
            b = int(idx[:-1])
            if runs and runs[-1][3] == "ref" and runs[-1][0] == base and runs[-1][2] == b + 1:
                runs[-1][2] = b
                continue
            runs.append([base, b, b, "ref"])
        elif x in ("1'b0", "1'b1"):
            if runs and runs[-1][3] == "const" and runs[-1][0] == x:
                runs[-1][1] += 1
                continue
            runs.append([x, 1, None, "const"])
        else:
            runs.append([x, None, None, "scalar"])
    out = []
    for base, a, b, kind in runs:
        if kind == "ref":
            out.append(f"{base}[{a}]" if a == b else f"{base}[{a}:{b}]")
        elif kind == "const":
            out.append(f"{a}'b{base[-1] * a}")
        else:
            out.append(base)
    return out[0] if len(out) == 1 else "{" + ", ".join(out) + "}"


def render(pl: Plan, top: str, core: str, ports, width: int = WIDTH) -> str:
    by = {p.name: p for p in ports}
    ins: dict[str, list[str]] = {}
    outs = ["1'b0"] * width
    oes = ["1'b0"] * width
    for s in pl.slots:
        if s.i:
            ins.setdefault(s.i[0], ["1'b0"] * by[s.i[0]].width)[s.i[1]] = f"io_in[{s.bit}]"
        if s.o:
            outs[s.bit] = _ref(f"w_{s.o[0]}", s.o[1], by[s.o[0]].width)
        if s.oe:
            oes[s.bit] = _ref(f"w_{s.oe[0]}", s.oe[1], by[s.oe[0]].width)
        elif s.kind == "out":
            oes[s.bit] = "1'b1"
    driven = sorted({s.o[0] for s in pl.slots if s.o} | {s.oe[0] for s in pl.slots if s.oe},
                    key=[p.name for p in ports].index)

    L = ["// 由 ran asic 生成：MPC-Frame 五口顶层。payload 位表在 report.json 的 frame 一节。",
         f"module {top} (",
         "  input  wire        clock,",
         "  input  wire        reset,",
         f"  input  wire [{width - 1}:0] io_in,",
         f"  output wire [{width - 1}:0] io_out,",
         f"  output wire [{width - 1}:0] io_oe",
         ");"]
    for n in driven:
        w = by[n].width
        L.append(f"  wire {'' if w == 1 else f'[{w - 1}:0] '}w_{n};")
    # 不再同步 reset：FrameTop 的 FrameDesignControl 已按时钟放开它（两拍 release_count）
    conns = [f"    .{pl.clock}(clock)",
             f"    .{pl.reset}({'~reset' if pl.reset_low else 'reset'})"]
    for p in ports:
        if p.name in (pl.clock, pl.reset):
            continue
        if p.name in pl.tie:
            conns.append(f"    .{p.name}({p.width}'d{pl.tie[p.name]})")
        elif p.name in ins:
            conns.append(f"    .{p.name}({_cat(ins[p.name][::-1])})")
        elif p.name in driven:
            conns.append(f"    .{p.name}(w_{p.name})")
        else:
            conns.append(f"    .{p.name}()")
    L.append(f"  {core} core (")
    L.append(",\n".join(conns))
    L.append("  );")
    L.append(f"  assign io_out = {_cat(outs[::-1])};")
    L.append(f"  assign io_oe  = {_cat(oes[::-1])};")
    L.append("endmodule")
    return "\n".join(L) + "\n"


def table(pl: Plan) -> list[dict]:
    """给报告的位表：每一位的方向与接到哪。"""
    return [{"bit": s.bit, "kind": s.kind, "signal": s.signal()} for s in pl.slots]
