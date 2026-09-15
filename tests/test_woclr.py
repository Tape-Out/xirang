"""onwrite: woclr 在每条写路径上都得是「写一清零」。

四条写路径（标量单字段、标量多字段、数组单字段、数组多字段）各生成一份寄存器组，写分支里
对这个字段的赋值必须拿旧值去与写进来的值取反。原来只有标量单字段那一条是对的，其余三条
写什么存什么——onew 的 status.done 写 1 去清，结果反而置上，中断清不掉。全组织原有的
woclr 字段恰好都在标量单字段寄存器上，所以一直没露。

`uv run python tests/test_woclr.py`
"""
import re
import sys
from types import SimpleNamespace

from xirang_gen.regmap import bsv

W1C = {"name": "done", "bits": "2:2", "sw": "rw", "onwrite": "woclr", "hw": "r", "reset": 0}
BUSY = {"name": "busy", "bits": "0:0", "sw": "r", "hw": "w", "volatile": True}
ARR = {"count": 4, "stride": 4}

CASES = [
    ("标量单字段", {"name": "st", "offset": 0,
                    "fields": [{**W1C, "name": "val", "bits": "7:0", "hwset": True, "stickybit": True}]}, "st"),
    ("标量多字段", {"name": "st", "offset": 0,
                    "fields": [BUSY, {**W1C, "hwset": True, "stickybit": True}]}, "st_done"),
    ("数组单字段", {"name": "st", "offset": 0, "array": ARR,
                    "fields": [{**W1C, "name": "val", "bits": "7:0"}]}, "st"),
    ("数组多字段", {"name": "st", "offset": 0, "array": ARR,
                    "fields": [BUSY, W1C]}, "st_done"),
]


def main() -> int:
    bad: list[str] = []
    for what, reg, sig in CASES:
        spec = {"ip": "t", "contract": {"aw": 8, "dw": 32}, "regs": [reg]}
        txt = bsv(SimpleNamespace(regmap=spec, name="t"))
        writes = [ln for ln in txt.splitlines()
                  if re.search(rf"\b{sig}_r\b[^<;]*<=", ln) and "method" not in ln]
        if not writes:
            bad.append(f"{what}：找不到总线写 {sig} 的那一行，测试本身的前提不成立")
        elif not any("& ~" in ln for ln in writes):
            bad.append(f"{what}：{sig} 是 woclr，总线写它却不清零：{writes[0].strip()}")
    for line in bad:
        print(f"✘ {line}")
    if not bad:
        print(f"✔ {len(CASES)} 条写路径上的 woclr 都是写一清零")
    return 1 if bad else 0


def test_woclr():
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
