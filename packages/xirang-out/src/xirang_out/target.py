"""导出目标的注册表。

**别人的格式一律是导出目标，不是执行路径。** 这里定下所有导出器共用的形状，
于是加一种格式只往注册表加一行，不必动 `cmd_export`——「新增同类只加数据不改
结构」是这套接口的判据。

`needs` 在一处校验：要生成物、要 Verilog、要寄存器图，各自报自己的编号并说清
该先跑哪条命令。放进每个导出器里重复写，就会有一个忘了写。
"""
import dataclasses
import pathlib
from collections.abc import Callable

from xirang_core.manifest import Bad, Pkg
from xirang_core.model import Resolved


@dataclasses.dataclass(frozen=True)
class Ctx:
    """一次导出看得见的全部东西。导出器只读它，不回头解析清单。"""

    res: Resolved
    pkgs: dict[str, Pkg]
    build: pathlib.Path | None = None
    out: pathlib.Path | None = None


@dataclasses.dataclass(frozen=True)
class Target:
    name: str
    ext: str
    desc: str
    render: Callable[[Ctx], str | bytes]
    needs: frozenset[str] = frozenset()


TARGETS: dict[str, Target] = {}


def target(name: str, ext: str, desc: str, needs: tuple[str, ...] = ()):
    def deco(fn):
        TARGETS[name] = Target(name=name, ext=ext, desc=desc,
                               render=fn, needs=frozenset(needs))
        return fn
    return deco


def pick(name: str | None) -> Target:
    t = TARGETS.get(name or "resolved")
    if t is None:
        raise Bad(f"XR-EXP-001 不认识的导出格式 {name!r}。"
                  f"可选：{', '.join(sorted(TARGETS))}")
    return t


def check(t: Target, ctx: Ctx) -> None:
    """needs 只在这里校验一次。"""
    from xirang_core.manifest import GEN_HW

    if "build" in t.needs:
        if ctx.build is None or not (ctx.build / GEN_HW).is_dir():
            raise Bad(f"XR-EXP-002 {t.name} 要生成物，而 {ctx.build} 里没有——"
                      f"先跑一次 `xirang build {ctx.res.top} --no-synth`")
    if "rtl" in t.needs:
        rtl = (ctx.build / "rtl") if ctx.build else None
        if rtl is None or not rtl.is_dir() or not any(rtl.glob("*.v")):
            raise Bad(f"XR-EXP-003 {t.name} 要 Verilog，而 {rtl} 里没有——"
                      f"先跑一次不带 --no-synth 的 `xirang build {ctx.res.top}`")
    if "regmap" in t.needs:
        def mapped(p) -> bool:
            # shape: none 的包不是总线从设备，它的偏移是 CSR 号不是地址
            if ((p.ip.get("contract") or {}).get("ctrl") or {}).get("shape") == "none":
                return False
            return (p.root / "regmap.yaml").is_file()

        has = any(mapped(ctx.pkgs[i.of]) for _, i in ctx.res.walk()
                  if i.of in ctx.pkgs) or mapped(ctx.pkgs[ctx.res.top])
        if not has:
            why = "一个寄存器图都没有"
            if ((ctx.pkgs[ctx.res.top].ip.get("contract") or {})
                    .get("ctrl") or {}).get("shape") == "none":
                why = ("契约写的是 shape: none——它不是总线从设备，"
                       "regmap 里的偏移是 CSR 号不是字节地址")
            raise Bad(f"XR-EXP-004 {ctx.res.top} {why}，{t.name} 是内存映射的格式，"
                      f"无从导起。要看它的寄存器用 `xirang gen {ctx.res.top}`")


def listing() -> str:
    w = max(len(n) for n in TARGETS)
    return "\n".join(f"  {t.name:<{w}}  {t.ext:<8} {t.desc}"
                      for t in sorted(TARGETS.values(), key=lambda x: x.name))
