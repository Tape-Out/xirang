"""单字段寄存器的位域不从第 0 位起时，读写都要落在声明的那几位上。

`hart` 的 `mepc` 只支持 IALIGN=32，低两位恒为零，于是写成单个 `val` 字段占 31:2。原来单字段那两条
路径（标量、数组）不看 `lo`：读是 `zeroExtend(字段)`，写是 `truncate(applyStrb(...))` 取低 30 位，
写全一读回 0x3FFFFFFF 而不是 0xFFFFFFFC。生成的一致性测试查得出，但它要编译仿真；这里在生成文本上
直接查读写两头，并查从第 0 位起的单字段没被改走多字段那条路（信号名与写法都不变）。

`uv run python tests/test_fieldlo.py`
"""
import sys
from types import SimpleNamespace

from xirang_gen.regmap import bsv

F = {"name": "val", "bits": "31:2", "sw": "rw", "hw": "rw", "reset": 0}
ARR = {"count": 4, "stride": 4}

# 第三项是硬件那一侧读方法的声明：标量是一个字段值，数组是一个 Vector
CASES = [
    ("标量", {"name": "epc", "offset": 0, "fields": [F]}, "method Bit#(30) epc;"),
    ("数组", {"name": "epc", "offset": 0, "array": ARR, "fields": [F]},
     "method Vector#(4, Bit#(30)) epc;"),
]


def gen(reg: dict) -> str:
    spec = {"ip": "t", "contract": {"aw": 8, "dw": 32}, "regs": [reg]}
    return bsv(SimpleNamespace(regmap=spec, name="t"))


def main() -> int:
    bad: list[str] = []
    for what, reg, decl in CASES:
        txt = gen(reg)
        if decl not in txt:
            bad.append(f"{what}：单个 val 字段应仍叫 epc、宽 30 位，测试本身的前提不成立")
            continue
        if "<< 2)" not in txt:
            bad.append(f"{what}：读出没有左移 2 位，字段落在了第 0 位")
        if "nw[31:2]" not in txt:
            bad.append(f"{what}：写入没有取 31:2，取的是低 30 位")
    txt = gen({"name": "epc", "offset": 0, "fields": [{**F, "bits": "29:0"}]})
    if "method Bit#(30) epc;" not in txt or "nw[" in txt:
        bad.append("从第 0 位起的单字段应照旧走单字段写法，不该改走多字段那条路")
    for line in bad:
        print(f"✘ {line}")
    if not bad:
        print(f"✔ {len(CASES)} 条单字段路径的读写都落在 31:2，从第 0 位起的照旧")
    return 1 if bad else 0


def test_fieldlo():
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
