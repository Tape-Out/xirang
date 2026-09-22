"""黑盒的参数投影：把解出来的旋钮值摆成上游 Verilog 参数的值。"""
import pathlib

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
