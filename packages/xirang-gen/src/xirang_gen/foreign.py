"""黑盒的参数投影：把解出来的旋钮值摆成上游 Verilog 参数的值。"""
import pathlib

from xirang_back import sv
from xirang_core.manifest import Bad, Pkg


def bake(pkg: Pkg, vals) -> dict[str, int]:
    """`emit.foreign.params` 说哪个旋钮喂哪个参数。布尔按 1/0 走。

    一个参数都没投影不是「没事」而是「这颗核在我们这儿只有一种配置」——
    息壤配得出的比上游支持的少，而外人看不出少在哪。
    """
    e = pkg.foreign_emit()
    if e is None:
        raise Bad(f"{pkg.name} 不是黑盒包，没有可展开的参数")
    out: dict[str, int] = {}
    for pname, src in (e.get("params") or {}).items():
        v = src if not isinstance(src, str) else (
            vals[src].value if src in vals else None)
        if v is None:
            raise Bad(f"{pkg.name}: 参数 {pname} 投影的旋钮 {src} 没有解出取值")
        out[pname] = int(v) if not isinstance(v, bool) else int(bool(v))
    return out


def files(pkg: Pkg, view: str = "rtl") -> list[pathlib.Path]:
    """综合视图或仿真视图的文件，按包根解成绝对路径。"""
    e = pkg.foreign_emit()
    if e is None:
        raise Bad(f"{pkg.name} 不是黑盒包")
    got = e.get(view) or e.get("rtl")
    return [pkg.root / f for f in got]


def receipt(pkg: Pkg, vals) -> list[tuple[str, str]]:
    """拿展开之后的端口表回来核对声明。返回 [(检查号, 说了什么)]。

    没有这一步，端口名写错要等到装配接线时才炸，那时报出来的是一堆对不上的
    Verilog 标识符，指不回清单。参数那一条更要紧：投影没生效是**静默**的，
    后端照上游默认值去量，面积与时序量的是另一颗核。
    """
    e = pkg.foreign_emit()
    if e is None:
        return []
    if not sv.available():
        return [("XR-FGN-003", f"{pkg.name}：装 pyslang 才核对得了黑盒声明"
                               f"（pip install xirang[sv]）")]
    want = bake(pkg, vals)
    got = sv.elaborate(files(pkg), e["top"], {k: str(v) for k, v in want.items()})
    ports, pars, errs = got
    out: list[tuple[str, str]] = []
    if errs:
        out.append(("XR-FGN-001", f"{e['top']} 展开时 slang 报错：{errs[0]}"))
        return out

    named: list[str] = []
    for c in ("clock", "reset"):
        if (spec := e.get(c)) and spec.get("port"):
            named.append(spec["port"])
    for pt in e.get("ports") or []:
        named += list((pt.get("map") or {}).values())
        if pre := pt.get("prefix"):
            if not any(n.startswith(pre) for n in ports):
                out.append(("XR-FGN-001",
                            f"端点 {pt['endpoint']} 说端口都以 {pre} 打头，"
                            f"展开之后一个都没有"))
    for n in named:
        if n not in ports:
            near = [p for p in ports if p.startswith(n[:4])][:3]
            out.append(("XR-FGN-001",
                        f"{e['top']} 没有端口 {n}" + (f"，像的有 {near}" if near else "")))

    for k, v in want.items():
        if k not in pars:
            out.append(("XR-FGN-002", f"{e['top']} 没有参数 {k}"))
        elif pars[k] != v:
            out.append(("XR-FGN-002",
                        f"参数 {k} 要的是 {v}，展开之后是 {pars[k]}"))
    return out


def _camel(name: str) -> str:
    a, *rest = name.lower().split("_")
    return a + "".join(w[:1].upper() + w[1:] for w in rest)


def _groups(ports: dict, skip: set) -> tuple[dict[str, list[str]], list[str]]:
    """按公共前缀把端口归组。整组协议写 prefix，零散的线逐根 map。

    前缀是数出来的，不是猜的：`mem_axi_awvalid` 与 `mem_axi_rdata` 共有 `mem_axi_`，
    而 `trap` 谁也不跟。三根以上才算一组——两根凑一组多半是巧合。
    """
    names = [n for n in ports if n not in skip]
    best: dict[str, list[str]] = {}
    for n in names:
        parts = n.split("_")
        for k in range(len(parts) - 1, 0, -1):
            pre = "_".join(parts[:k]) + "_"
            best.setdefault(pre, []).append(n)
    groups: dict[str, list[str]] = {}
    taken: set[str] = set()
    for pre in sorted(best, key=lambda p: (-len(best[p]), p)):
        rest = [n for n in best[pre] if n not in taken]
        if len(rest) >= 3:
            groups[pre] = sorted(rest)
            taken |= set(rest)
    return groups, sorted(n for n in names if n not in taken)


def draft(files, top: str, clock: str = "clk", reset: str = "rst_n") -> str:
    """从别人的 RTL 出一份声明草稿：全部参数、按前缀归好的端点。

    草稿是起点不是终点——端点的 kind 与 role 要人去判，profile 要人去认。
    但「参数漏了一个」「端口名抄错了」这两类错，草稿一出来就不存在了。
    """
    from xirang_back import sv
    if not sv.available():
        raise Bad("出草稿要 pyslang：pip install xirang[sv]")
    got = sv.elaborate(files, top, {})
    if got is None:
        raise Bad("出草稿要 pyslang")
    ports, pars, errs = got
    if errs:
        raise Bad(f"{top} 展开不了：{errs[0]}")

    L = ["params:"]
    feats = ["features:"]
    proj = []
    for k, v in sorted(pars.items()):
        kn = _camel(k)
        proj.append(f"    {k}: {kn}")
        if isinstance(v, int) and v in (0, 1):
            feats += [f"  {kn}:", "    type: bool", f"    default: {str(bool(v)).lower()}"]
        else:
            L += [f"  {kn}:", "    type: int",
                  f"    default: {v if isinstance(v, int) else 0}"]
    groups, loose = _groups(ports, {clock, reset})
    P = ["emit:", "- kind: foreign", "  lang: verilog", f"  top: {top}",
         "  rtl: []      # 填上综合视图的文件", "  params:"] + proj + [
        "  clock:", f"    port: {clock}", "  reset:", f"    port: {reset}",
        "    active: low", "    sync: true", "  ports:"]
    for pre, ns in groups.items():
        P += [f"  # {len(ns)} 根：{' '.join(ns[:4])}{' …' if len(ns) > 4 else ''}",
              f"  - endpoint: {pre.rstrip('_')}", "    kind: transaction",
              "    role: manager", "    profile: 填上它讲哪种协议",
              f"    prefix: {pre}"]
    if loose:
        P += ["  - endpoint: pins", "    kind: physical", "    type: 填个类型名",
              "    map:"] + [f"      {_camel(n)}: {n}" for n in loose]
    return chr(10).join(L + feats + P)
