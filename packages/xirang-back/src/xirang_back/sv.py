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


@dataclasses.dataclass(frozen=True)
class Param:
    """一个参数：取值、位宽、如果是枚举还有它的档位名。

    三样都要：位宽决定它是开关还是取值，枚举决定能不能用整数覆盖——
    `RV32M=2` 传给一个枚举类型的参数，slang 只会把它置成 <unset>，而那不报错。
    """
    name: str
    value: object
    width: int
    enum: tuple[tuple[str, int], ...] = ()


def available() -> bool:
    try:
        import pyslang  # noqa: F401
    except ImportError:
        return False
    return True


def _lit(v) -> str:
    """参数覆盖的字面量。

    slang 不收超出有符号 32 位的裸十进制：`LATCHED_IRQ=4294967295` 会被判成
    「不是合法的覆盖写法」。写成有基数的形式它才认。
    """
    if isinstance(v, bool):
        return str(int(v))
    if isinstance(v, int) and not -(2 ** 31) <= v < 2 ** 31:
        return f"'h{v:x}"
    return str(v)


def _num(v):
    """slang 的常量转成数。转不成就原样留着字符串。

    它给的是 ConstantValue，`int()` 直接用会抛——枚举成员的值就是这么被我
    悄悄吞掉的，结果整条枚举通路看着像没实现。
    """
    txt = str(v).replace("_", "").strip()
    if "'" in txt:
        # 32'h10 是十六进制的 16，不是十进制的 10。基数写在引号后面那个字母上，
        # 把它剥掉再按十进制读，读出来的是另一个数而且不会报错
        tail = txt.split("'")[-1]
        if tail[:1].lower() == "s":      # 32'sd3：有符号的 s 排在基数字母前面
            tail = tail[1:]
        base = {"b": 2, "o": 8, "d": 10, "h": 16}.get(tail[:1].lower())
        digits = tail if base is None else tail[1:]
        try:
            return int(digits, base or 10)
        except ValueError:
            return txt
    try:
        return int(txt)
    except ValueError:
        return txt


def _width(t) -> int:
    try:
        return int(t.bitWidth)
    except (AttributeError, TypeError, ValueError):
        return 0


def elaborate(files, top: str, params: dict, defines=(), includes=()):
    """展开一次，返回 (端口表, 参数表, 出错的诊断)。没装 pyslang 就返回 None。

    参数表是 {名字: Param}。位宽与枚举档位都要跟着出来——按取值猜类型会出错，
    而枚举参数用整数去覆盖不会报错，只会悄悄没生效。
    """
    if not available():
        return None
    from pyslang import TimeScale  # noqa: F401
    from pyslang import (Bag, DiagnosticEngine, DiagnosticSeverity,
                         SourceManager, TextDiagnosticClient, ast, syntax)
    from pyslang.parsing import PreprocessorOptions

    o = ast.CompilationOptions()
    o.topModules = {top}
    o.paramOverrides = [f"{k}={_lit(v)}" for k, v in params.items()]
    # 一份设计里有的文件写了 `timescale 有的没写，slang 就把「没写」当错误报。
    # 那是仿真的事，与「这组配置展不展得开」无关——pulp 的 hwpe 系列全是这样。
    # 给个默认值，缺的就按它算
    o.defaultTimeScale = TimeScale.fromString("1ns/1ps")
    c = ast.Compilation(Bag([o]))
    # 宏要跟 yosys 给的是同一套，否则两边看到的端口表不是同一份：picorv32 的
    # rvfi 那 177 根端口在 `RISCV_FORMAL` 里，不给宏它们压根不存在
    sm = SourceManager()
    bag = Bag()
    if defines or includes:
        po = PreprocessorOptions()
        if defines:
            po.predefines = list(defines)
        if includes:
            # `include 找不到文件不是警告是错误：ibex 的 prim_assert.sv 住在
            # vendored 的 prim 目录里，不给路径整棵树都编不过
            po.additionalIncludePaths = [str(x) for x in includes]
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
            v = _num(m.value)
            t = m.type
            mem: tuple[tuple[str, int], ...] = ()
            if getattr(t, "isEnum", False):
                try:
                    mem = tuple((x.name, _num(x.value)) for x in t.canonicalType)
                except TypeError:
                    mem = ()
            got[m.name] = Param(m.name, v, _width(t), mem)
    # `str(d)` 给的是对象的 repr，不是消息；拿它去匹配「error」永远不命中，
    # 于是展开报错被整条丢掉——「什么都没查却报绿」正是这么来的
    eng = DiagnosticEngine(sm)
    client = TextDiagnosticClient()
    client.showColors(False)
    eng.addClient(client)
    errs = []
    for d in c.getAllDiagnostics():
        # `isError` 对警告也为真；严重级要问引擎。上游的风格警告（未命名的
        # generate、case 少 default）不是我们的事，真编不过才是
        if eng.getSeverity(d.code, d.location) != DiagnosticSeverity.Error:
            continue
        client.clear()
        eng.issue(d)
        errs.append(client.getString().strip())
    return ports, got, errs
