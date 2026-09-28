"""诊断闸门：稳定检查号，加一个四处可覆盖的严重级。

文案会改，号不改——写脚本、写豁免、在群里说「又撞上那个」，指的都是号。
只有 `error` 挡住动作，其余照跑并留在报告里。

这里只做注册表与查表：报错由调用方抛，免得与 `manifest` 互相 import。
"""
import dataclasses
import enum


class Level(enum.IntEnum):
    noshow = 0
    trace = 1
    debug = 2
    info = 3
    warn = 4
    error = 5


@dataclasses.dataclass(frozen=True)
class Check:
    code: str
    level: Level
    what: str


def _c(code: str, level: Level, what: str) -> tuple[str, Check]:
    return code, Check(code, level, what)


# 默认级见 xrspec/ip.md 八之三。面积那一族是 info：没有综合流程的人要能跑行为仿真
CHECKS: dict[str, Check] = dict([
    _c("XR-WIRE-001", Level.error, "引脚驱进来的值，模块里一次也没读"),
    _c("XR-WIRE-002", Level.error, "引脚没处置：连接、导出、接成常量、声明不用，四选一"),
    _c("XR-REG-001", Level.error, "寄存器接口暴露的方法，实现里没有用到"),
    _c("XR-ADDR-001", Level.error, "某个合法配置下两个寄存器占同一个地址"),
    _c("XR-SCHED-001", Level.error, "生成 Verilog 时 bsc 报出的规则冲突"),
    _c("XR-LOCK-001", Level.error, "清单、源码与锁对不上"),
    _c("XR-DEP-001", Level.error, "依赖的 path 指过去，那里没有包"),
    _c("XR-DEP-002", Level.error, "依赖的来源指过去，包名不是要的那个"),
    _c("XR-DEP-003", Level.error, "依赖声明了 git，本机却没有它的检出"),
    _c("XR-DEP-004", Level.error, "依赖的检出不在声明的 rev 上"),
    _c("XR-TASK-001", Level.error, "清单里没有这个任务"),
    _c("XR-TASK-002", Level.error, "任务的 needs 成环"),
    _c("XR-TASK-003", Level.error, "任务写得不对：键、名字或占位符"),
    _c("XR-TASK-004", Level.error, "任务跑挂了"),
    _c("XR-GEN-001", Level.error, "生成物与源描述漂移"),
    _c("XR-CONV-001", Level.error, "位宽转换"),
    _c("XR-CONV-002", Level.error, "突发或原子性被摊平"),
    _c("XR-CDC-001", Level.error, "两端时钟域不同而没写 cdc"),
    _c("XR-FGN-001", Level.error, "黑盒声明里的端口，展开之后并不存在"),
    _c("XR-FGN-002", Level.error, "投影过去的参数，展开之后不是那个值"),
    _c("XR-FGN-003", Level.info, "核对不了黑盒声明：没装 pyslang"),
    _c("XR-SRC-001", Level.error, "glob 或正则一个文件都没匹配到"),
    _c("XR-SRC-002", Level.error, "Flist 里的变量没有值"),
    _c("XR-SRC-003", Level.info, "Flist 的宏或目录被清单覆盖"),
    _c("XR-SRC-004", Level.error, "源码条目指向的文件不在"),
    _c("XR-VIEW-001", Level.error, "视图名不是 rtl、sim、syn"),
    _c("XR-MACRO-001", Level.error, "占位符指向不存在的旋钮，或不是档位旋钮"),
    _c("XR-MACRO-002", Level.error, "逐档映射漏了合法档位"),
    _c("XR-RCPT-001", Level.error, "顶层非打包数组端口长度为 0"),
    _c("XR-RCPT-002", Level.error, "探针的期望不满足"),
    _c("XR-RCPT-003", Level.error, "探针找不到符号"),
    _c("XR-DIAG-001", Level.error, "放宽了一条前端放不宽的诊断"),
    _c("XR-CFG-001", Level.error, "导入的值不在取值域里"),
    _c("XR-CFG-002", Level.warn, "导入的键不认识"),
    _c("XR-CFG-003", Level.error, "有新旋钮要回答，但不在终端里"),
    _c("XR-SPEC-001", Level.error, "用了规范里有、本版工具还没实现的写法"),
    _c("XR-AREA-001", Level.info, "价目表量的是另一份生成产物"),
    _c("XR-AREA-002", Level.info, "参数改了，量出来的面积不变"),
    _c("XR-AREA-003", Level.info, "改这个旋钮，面积预测不动"),
    _c("XR-AREA-004", Level.info, "预测面积低于实测"),
    _c("XR-AREA-005", Level.info, "标了价，却没有一行实测打开过这个特性"),
    _c("XR-AREA-006", Level.info, "没有价目表"),
])


SLANG = "slang:"

SLANG_COMPAT = {
    "UsedBeforeDeclared": "--allow-use-before-declare",
    "SysFuncHierarchicalNotAllowed": "--allow-hierarchical-const",
    "ConstEvalHierarchicalName": "--allow-hierarchical-const",
}

SUGAR = {"allow": "info", "warn": "warn", "deny": "error"}


def level_of(name) -> Level | None:
    """写在清单里的那个词。不认识就返回 None，由调用方报错。"""
    if isinstance(name, Level):
        return name
    return Level.__members__.get(str(name))


def known(code: str) -> bool:
    return code in CHECKS or code.startswith(SLANG)


def check(code: str) -> Check:
    return CHECKS.get(code) or Check(code, Level.error, "外来 RTL 的语言问题，名字照 slang")


def flatten(over) -> dict:
    """`allow`／`warn`／`deny` 三个列表摊成逐条的级别；逐条写的优先。"""
    out = {}
    for key, lv in SUGAR.items():
        for code in (over or {}).get(key) or []:
            out[code] = lv
    out.update({k: v for k, v in (over or {}).items() if k not in SUGAR})
    return out


def resolve(code: str, layers) -> tuple[Level, str]:
    """这道检查此刻是哪一级，以及这一级从哪来。

    `layers` 是 [(来源, {检查号: 级别})]，靠后的优先——与旋钮取值同一套层叠。
    """
    lv, why = (CHECKS[code].level if code in CHECKS else Level.error), "默认"
    for src, over in layers:
        got = level_of(flatten(over).get(code))
        if got is not None:
            lv, why = got, src
    return lv, why


def slang_flags(layers) -> tuple[list[str], list[str]]:
    """放宽到 error 以下的 slang 诊断对应哪些开关；没有开关的另列出来。"""
    codes = {c for _, over in layers for c in flatten(over) if c.startswith(SLANG)}
    flags, bad = [], []
    for code in sorted(codes):
        if blocks(resolve(code, layers)[0]):
            continue
        flag = SLANG_COMPAT.get(code[len(SLANG):])
        if flag is None:
            bad.append(code)
        elif flag not in flags:
            flags.append(flag)
    return flags, bad


def blocks(lv: Level) -> bool:
    """只有 error 挡住动作。"""
    return lv >= Level.error
