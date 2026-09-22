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


def elaborate(files, top: str, params: dict, defines=()):
    """展开一次，返回 (端口表, 参数表, 出错的诊断)。没装 pyslang 就返回 None。

    参数表是 {名字: (取值, 位宽)}。位宽要跟着出来——按取值猜类型会出错。
    """
    if not available():
        return None
    from pyslang import Bag, SourceManager, ast, syntax
    from pyslang.parsing import PreprocessorOptions

    o = ast.CompilationOptions()
    o.topModules = {top}
    o.paramOverrides = [f"{k}={v}" for k, v in params.items()]
    c = ast.Compilation(Bag([o]))
    # 宏要跟 yosys 给的是同一套，否则两边看到的端口表不是同一份：picorv32 的
    # rvfi 那 177 根端口在 `RISCV_FORMAL` 里，不给宏它们压根不存在
    sm = SourceManager()
    bag = Bag()
    if defines:
        po = PreprocessorOptions()
        po.predefines = list(defines)
        bag = Bag([po])
    for f in files:
        c.addSyntaxTree(syntax.SyntaxTree.fromFile(str(f), sm, bag))
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
            # localparam 也是 ParameterSymbol，但它不可覆盖。把它当旋钮，
            # yosys 的 chparam 会当场报「没有这个参数」，而报错指的是我们的清单
            if getattr(m, "isLocalParam", False):
                continue
            try:
                txt = str(m.value)
                v = (int(txt.split("'")[-1].lstrip("bdhox") or 0, 0)
                     if "'" in txt else int(txt))
            except ValueError:
                v = str(m.value)
            # 位宽比取值可靠：`parameter RESET_PC = 32'd0` 的值是 0，但它是
            # 32 位地址不是开关。按取值猜类型会把它判成布尔
            got[m.name] = (v, _width(m.type))
    errs = [str(d) for d in c.getAllDiagnostics()
            if "error" in str(getattr(d, "severity", "")).lower()]
    return ports, got, errs
