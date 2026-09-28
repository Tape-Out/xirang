"""整颗芯片的软件视图：每个实例在哪、多大、在设备树里叫什么。

SVD、设备树、C 头读的是同一张表，各自只管写成自己的格式。
"""
import dataclasses

from .target import Ctx


@dataclasses.dataclass(frozen=True)
class Dev:
    name: str
    of: str
    addr: int | None
    size: int
    knobs: dict
    pkg: object
    dt: dict


def _when(cond: dict | None, knobs: dict) -> bool:
    return all(knobs.get(k) == v for k, v in (cond or {}).items())


def props(dt: dict, knobs: dict) -> list[tuple[str, object]]:
    """`dt.props` 里这一档成立的那些，按写的次序。"""
    return [(p["name"], p.get("value", True)) for p in dt.get("props") or []
            if _when(p.get("when"), knobs)]


def _size(pkg, dt: dict, knobs: dict, window: int | None) -> int:
    """存储要报实际装了多少，不是译码窗口——设备树里写大了，Linux 会去用不存在的内存。"""
    s = dt.get("size")
    if isinstance(s, int):
        return s
    if isinstance(s, dict):
        return int(knobs[s["knob"]]) * int(s.get("scale", 1))
    if window:
        return window
    aw = int(((pkg.ip.get("contract") or {}).get("ctrl") or {}).get("aw", 0))
    return 1 << aw if aw else 0


def devices(ctx: Ctx) -> list[Dev]:
    out = []
    for _, i in ctx.res.walk():
        p = ctx.pkgs.get(i.of)
        if p is None:
            continue
        knobs = {k: v.value for k, v in i.values.items()}
        dt = p.ip.get("dt") or {}
        out.append(Dev(i.name, i.of, i.addr, _size(p, dt, knobs, i.size), knobs, p, dt))
    return out


def ident(name: str) -> str:
    return name.replace("-", "_")
