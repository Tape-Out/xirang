"""问 slang：这份 RTL 展开之后到底长什么样。

黑盒声明写的是「我们以为它长什么样」。没有这一步，写错一个端口名要等到装配
接线时才炸，那时报出来的是一堆对不上的 Verilog 标识符，指不回清单。

pyslang 是可选的（`pip install xirang[sv]`）。装不上就报「没法核对」——
**不是「核对过了」**。没量出来的说成过了，比不查更糟。
"""
import dataclasses


@dataclasses.dataclass(frozen=True)
class Port:
    name: str
    direction: str      # in / out / inout
    width: int          # 位数；解不出来就是 0


def available() -> bool:
    try:
        import pyslang  # noqa: F401
    except ImportError:
        return False
    return True


def _width(t) -> int:
    try:
        return int(t.bitWidth)
    except (AttributeError, TypeError, ValueError):
        return 0


def elaborate(files, top: str, params: dict):
    """展开一次，返回 (端口表, 参数表, 出错的诊断)。没装 pyslang 就返回 None。"""
    if not available():
        return None
    from pyslang import Bag, ast, syntax

    o = ast.CompilationOptions()
    o.topModules = {top}
    o.paramOverrides = [f"{k}={v}" for k, v in params.items()]
    c = ast.Compilation(Bag([o]))
    for f in files:
        c.addSyntaxTree(syntax.SyntaxTree.fromFile(str(f)))
    tops = list(c.getRoot().topInstances)
    if not tops:
        return ({}, {}, [f"slang 展开不出顶层 {top}"])
    body = tops[0].body
    ports = {}
    for m in body:
        if isinstance(m, ast.PortSymbol):
            ports[m.name] = Port(m.name, str(m.direction).split(".")[-1].lower(),
                                 _width(m.type))
    got = {}
    for m in body:
        if isinstance(m, ast.ParameterSymbol):
            try:
                got[m.name] = int(str(m.value).split("'")[-1].lstrip("bdhox") or 0, 0) \
                    if "'" in str(m.value) else int(str(m.value))
            except ValueError:
                got[m.name] = str(m.value)
    errs = [str(d) for d in c.getAllDiagnostics()
            if "error" in str(getattr(d, "severity", "")).lower()]
    return ports, got, errs
