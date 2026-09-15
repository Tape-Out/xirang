"""wrdata 要真的把写进来的值交给硬件。

四条写路径（标量单字段、标量多字段、数组单字段、数组多字段）各生成一份寄存器组，
`<名>_mod_v` 那根线必须在写分支里被赋值。原来只有数组那两条赋了值，标量那两条只发写脉冲，
`_wr_val` 恒为零：crc 的数据寄存器喂进去九个字节，引擎看到的全是 0。生成的一致性测试只从
总线那一侧读写，查不到硬件那一侧的方法，所以这一条单独测。

`uv run python tests/test_wrdata.py`
"""
import sys
from types import SimpleNamespace

from xirang_gen.regmap import bsv

F = {"name": "val", "sw": "w", "hw": "na", "swmod": True, "wrdata": True, "reset": 0}
HI = {"name": "hi", "bits": "15:8", "sw": "rw", "hw": "r", "reset": 0}
ARR = {"count": 4, "stride": 4}

CASES = [
    ("标量单字段", {"name": "data", "offset": 0, "fields": [{**F, "bits": "7:0"}]}, "data"),
    ("标量多字段", {"name": "data", "offset": 0,
                    "fields": [{**F, "name": "lo", "bits": "7:0"}, HI]}, "data_lo"),
    ("数组单字段", {"name": "data", "offset": 0, "array": ARR,
                    "fields": [{**F, "bits": "7:0"}]}, "data"),
    ("数组多字段", {"name": "data", "offset": 0, "array": ARR,
                    "fields": [{**F, "name": "lo", "bits": "7:0"}, HI]}, "data_lo"),
]


def main() -> int:
    bad: list[str] = []
    for what, reg, sig in CASES:
        spec = {"ip": "t", "contract": {"aw": 8, "dw": 32}, "regs": [reg]}
        txt = bsv(SimpleNamespace(regmap=spec, name="t"))
        if f"method Bit#(8) {sig}_wr_val = {sig}_mod_v;" not in txt:
            bad.append(f"{what}：没生成 {sig}_wr_val，测试本身的前提不成立")
        elif f"{sig}_mod_v <=" not in txt:
            bad.append(f"{what}：{sig}_mod_v 接到了 {sig}_wr_val，写分支里却从没给它赋值")
    for line in bad:
        print(f"✘ {line}")
    if not bad:
        print(f"✔ {len(CASES)} 条写路径都把写进来的值交给了硬件")
    return 1 if bad else 0


def test_wrdata():
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
