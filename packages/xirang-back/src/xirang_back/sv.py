"""问 slang：这份 RTL 展开之后到底长什么样。

黑盒声明写的是「我们以为它长什么样」。没有这一步，写错一个端口名要等到装配
接线时才炸，那时报出来的是一堆对不上的 Verilog 标识符，指不回清单。

pyslang 是可选的（`pip install xirang[sv]`）。装不上就报「没法核对」——
**不是「核对过了」**。没量出来的说成过了，比不查更糟。
"""
import dataclasses
import shlex
import typing


@dataclasses.dataclass(frozen=True)
class Port:
    name: str
    direction: str      # in / out / inout
    width: int          # 位数；解不出来就是 0
    count: int | None = None    # 非打包数组的元素个数；不是数组为 None


class Elab(typing.NamedTuple):
    ports: dict
    params: dict
    errs: list          # [(检查号, 诊断原文)]；slang 的写成 slang:<诊断名>
    probes: dict = {}


FAIL = "XR-FGN-001"


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


def _unpacked(t) -> int | None:
    if not getattr(t, "isUnpackedArray", False):
        return None
    n = 1
    while getattr(t, "isUnpackedArray", False):
        n *= int(t.range.width)
        t = t.elementType
    return n


def elaborate(files, top: str, params: dict, defines=(), includes=(), *,
              libdirs=(), libext=(), flags=(), probes=()) -> "Elab | None":
    """展开一次，返回 Elab（端口表、参数表、出错的诊断、探针取值）。没装 pyslang 就返回 None。

    参数表是 {名字: Param}。位宽与枚举档位都要跟着出来——按取值猜类型会出错，
    而枚举参数用整数去覆盖不会报错，只会悄悄没生效。

    `libdirs` 按模块名找文件（`-y`）；`flags` 是诊断闸门放宽后要开的 slang 开关；
    `probes` 写 `包::名字` 或顶层参数名，找不到的不进结果。
    """
    if not available():
        return None
    from pyslang import DiagnosticEngine, DiagnosticSeverity, TextDiagnosticClient, ast, driver

    # 一份设计里有的文件写了 `timescale 有的没写，slang 就把「没写」当错误报。
    # 那是仿真的事，与「这组配置展不展得开」无关——pulp 的 hwpe 系列全是这样。
    # 给个默认值，缺的就按它算
    args = ["slang", "--top", top, "--timescale", "1ns/1ps", *flags]
    # translate_off 那段上游明写不进综合，yosys 那边抹掉了（verilog.strip_sim），这边也跳过
    for w in ("synopsys", "synthesis", "pragma"):
        args += ["--translate-off-format", f"{w},translate_off,translate_on"]
    for k, v in params.items():
        args += ["-G", f"{k}={_lit(v)}"]
    # 宏要跟 yosys 给的是同一套，否则两边看到的端口表不是同一份：picorv32 的
    # rvfi 那 177 根端口在 `RISCV_FORMAL` 里，不给宏它们压根不存在
    for d in defines:
        args += ["-D", str(d)]
    # `include 找不到文件不是警告是错误：ibex 的 prim_assert.sv 住在
    # vendored 的 prim 目录里，不给路径整棵树都编不过
    for x in includes:
        args += ["-I", str(x)]
    for x in libdirs:
        args += ["-y", str(x)]
    for x in libext:
        args += ["--libext", str(x)]
    args += [str(f) for f in files]
    drv = driver.Driver()
    drv.addStandardArgs()
    # 解析没过就建编译，pyslang 会段错误，整个进程跟着没了
    if not (drv.parseCommandLine(shlex.join(args), driver.CommandLineOptions())
            and drv.processOptions()):
        return Elab({}, {}, [(FAIL, f"slang 不认这组参数：{shlex.join(args[1:])}")])
    if not drv.parseAllSources():
        return Elab({}, {}, [(FAIL, f"slang 读不进源文件：{shlex.join(args[1:])}")])
    c = drv.createCompilation()
    sm = drv.sourceManager
    tops = list(c.getRoot().topInstances)
    if not tops:
        return Elab({}, {}, [(FAIL, f"slang 展开不出顶层 {top}")])
    body = tops[0].body
    diags = list(c.getAllDiagnostics())
    zero = any(str(d.code).endswith("(ValueMustBePositive)") for d in diags)
    ports = {}
    for m in body:
        if isinstance(m, ast.PortSymbol):
            t = m.type
            n = 0 if zero and getattr(t, "isError", False) else _unpacked(t)
            w = _width(t)
            if n:
                e = t
                while getattr(e, "isUnpackedArray", False):
                    e = e.elementType
                w = _width(e) * n
            ports[m.name] = Port(m.name, str(m.direction).split(".")[-1].lower(), w, n)
    found = {}
    for s in probes:
        pkg, _, name = s.rpartition("::")
        scope = c.getPackage(pkg) if pkg else body
        sym = scope.find(name) if scope is not None else None
        if sym is not None and hasattr(sym, "value"):
            found[s] = _num(sym.value)
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
    for d in diags:
        # `isError` 对警告也为真；严重级要问引擎。上游的风格警告（未命名的
        # generate、case 少 default）不是我们的事，真编不过才是
        if eng.getSeverity(d.code, d.location) != DiagnosticSeverity.Error:
            continue
        client.clear()
        eng.issue(d)
        name = str(d.code).removeprefix("DiagCode(").removesuffix(")")
        errs.append((f"slang:{name}", client.getString().strip()))
    return Elab(ports, got, errs, found)
