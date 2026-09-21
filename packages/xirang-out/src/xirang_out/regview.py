"""按解出来的配置看一个包的寄存器。

导出 SystemRDL 与 IP-XACT 都要这一份：**特性关掉的字段按构造不存在**，
参数已经取值，位宽是数字不是旋钮名。定案是「导已解出配置」，所以门控在这里
就消失了，不必在两个导出器里各写一遍。
"""
import dataclasses

import yaml
from xirang_gen.regmap import _rows


@dataclasses.dataclass(frozen=True)
class Reg:
    name: str
    offset: int
    desc: str
    width: int
    fields: list[dict]


def read(pkg, knobs: dict) -> tuple[list[Reg], dict]:
    """返回（寄存器表, 头部元信息）。没得导就返回空表。

    **`shape: none` 的包不导。** 规范写明它不是总线从设备，`regmap.yaml` 的
    `base` 描述的是 CSR 空间、与片上地址无关；`hart` 的偏移就是 CSR 号
    （`0xF12`、`0xF14`），当成字节地址会重叠。内存映射的导出容不下它，
    硬导出来的是一份看着像真的的假地址图。
    """
    if ((pkg.ip.get("contract") or {}).get("ctrl") or {}).get("shape") == "none":
        return [], {"skip": "shape-none"}
    f = pkg.root / "regmap.yaml"
    if not f.is_file():
        return [], {}
    spec = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
    dw = int((spec.get("contract") or {}).get("dw", 32))
    rows = _rows(spec, spec.get("params") or [], dw)

    def on(feat) -> bool:
        return feat is None or bool(knobs.get(feat))

    def width(w) -> int:
        s = str(w)
        return int(s) if s.lstrip("-").isdigit() else int(knobs.get(s, 32))

    out = []
    for r in rows:
        if not on(r.get("feat")):
            continue
        fs = []
        for x in r["fields"]:
            if not on(x.get("feat")):
                continue
            w = width(x["w"])
            lo = int(x["lo"]) if x.get("lo") is not None else 0
            rst = x.get("reset")
            if rst is not None:      # -1 是「全一」的写法，按位宽取模才是那个值
                rst = int(rst) & ((1 << w) - 1)
            fs.append({**x, "w": w, "lo": lo, "hi": lo + w - 1, "reset": rst})
        if fs:
            out.append(Reg(name=r["name"], offset=int(r["offset"]),
                           desc=r.get("desc") or "", width=int(r.get("rw") or dw),
                           fields=fs))
    head = {"dw": dw, "aw": int((spec.get("contract") or {}).get("aw", 8)),
            "base": int(str(spec.get("base", 0)), 0),
            "size": int(str(spec.get("size", 0)), 0)}
    return out, head
