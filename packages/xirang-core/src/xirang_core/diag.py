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
    _c("XR-GEN-001", Level.error, "生成物与源描述漂移"),
    _c("XR-CONV-001", Level.error, "位宽转换"),
    _c("XR-CONV-002", Level.error, "突发或原子性被摊平"),
    _c("XR-CDC-001", Level.error, "两端时钟域不同而没写 cdc"),
    _c("XR-FGN-001", Level.error, "黑盒声明里的端口，展开之后并不存在"),
    _c("XR-FGN-002", Level.error, "投影过去的参数，展开之后不是那个值"),
    _c("XR-FGN-003", Level.info, "核对不了黑盒声明：没装 pyslang"),
    _c("XR-AREA-001", Level.info, "价目表量的是另一份生成产物"),
    _c("XR-AREA-002", Level.info, "参数改了，量出来的面积不变"),
    _c("XR-AREA-003", Level.info, "改这个旋钮，面积预测不动"),
    _c("XR-AREA-004", Level.info, "预测面积低于实测"),
    _c("XR-AREA-005", Level.info, "标了价，却没有一行实测打开过这个特性"),
    _c("XR-AREA-006", Level.info, "没有价目表"),
])


def level_of(name) -> Level | None:
    """写在清单里的那个词。不认识就返回 None，由调用方报错。"""
    if isinstance(name, Level):
        return name
    return Level.__members__.get(str(name))


def resolve(code: str, layers) -> tuple[Level, str]:
    """这道检查此刻是哪一级，以及这一级从哪来。

    `layers` 是 [(来源, {检查号: 级别})]，靠后的优先——与旋钮取值同一套层叠。
    """
    lv, why = CHECKS[code].level, "默认"
    for src, over in layers:
        got = level_of((over or {}).get(code))
        if got is not None:
            lv, why = got, src
    return lv, why


def blocks(lv: Level) -> bool:
    """只有 error 挡住动作。"""
    return lv >= Level.error
