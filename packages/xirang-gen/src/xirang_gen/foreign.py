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
