"""标量单字段寄存器上的 volatile 字段，软件写只发脉冲、交写进来的值，不往那根线上写。

volatile 字段落在 mkDWire 上，值由硬件的 `_in` 每拍驱动。原来标量单字段那条路径 `sw: rw` 时不看 volatile，
总线写也生成 `x_r <= truncate(applyStrb(...))`：两边抢同一个 wset，bsc 报 G0010、让驱动规则更紧迫，
访问这个寄存器的总线动作永远排不上（sdhci 的 PIO 数据口，2026-09-16）。生成的一致性测试不驱 `_in`，查不出；
这里在生成文本上直接查：写线的语句不能有，读、写脉冲、写入值、读脉冲都还在；同一个字段去掉 volatile 照旧写存储。

`uv run python tests/test_volscalar.py`
"""
import sys
from types import SimpleNamespace

from xirang_gen.regmap import bsv

VOL = {"name": "val", "bits": "31:0", "sw": "rw", "hw": "w", "volatile": True,
       "swacc": True, "swmod": True, "wrdata": True}


def gen(reg: dict) -> str:
    spec = {"ip": "t", "contract": {"aw": 8, "dw": 32}, "regs": [reg]}
    return bsv(SimpleNamespace(regmap=spec, name="t"))


def main() -> int:
    bad: list[str] = []
    txt = gen({"name": "dport", "offset": 0, "fields": [VOL]})
    if "method Action dport_in(Bit#(32) v);" not in txt or "mkDWire" not in txt:
        bad.append("前提不成立：标量单字段的 volatile 字段应生成 dport_in 与一根 mkDWire")
    if "dport_r <= truncate(applyStrb" in txt:
        bad.append("volatile：总线写仍往 dport_r 那根线上写，与硬件的 dport_in 抢同一个 wset")
    for want, what in [("rd = zeroExtend(dport_r)", "软件读不到硬件驱动的值"),
                       ("dport_mod.send()", "写脉冲没了"),
                       ("dport_mod_v <= truncate(wd)", "写进来的值没交出去"),
                       ("dport_acc.send()", "读脉冲没了")]:
        if want not in txt:
            bad.append(f"volatile：{what}（找不到 {want}）")
    wo = gen({"name": "dport", "offset": 0, "fields": [{**VOL, "sw": "w", "swacc": False}]})
    if "dport_r <= truncate(applyStrb" in wo:
        bad.append("volatile 且只写：总线写仍往那根线上写")
    stored = {k: v for k, v in VOL.items() if k != "volatile"}
    st = gen({"name": "dport", "offset": 0, "fields": [{**stored, "reset": 0}]})
    if "applyStrb" not in st:
        bad.append("对照：不带 volatile 的同一个字段应照旧把软件写进存储")
    for line in bad:
        print(f"✘ {line}")
    if not bad:
        print("✔ 标量单字段的 volatile 字段：软件写只发脉冲与写入值，读仍取硬件驱动的值；带存储的照旧写")
    return 1 if bad else 0


def test_volscalar():
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
