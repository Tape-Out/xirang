"""档位特性：`feature: {名字: [档...]}` 的门控落在每一处，写漏一档就拦不住那一档。

原先 `regmap.py` 把每个特性一律发成 `Bool`，于是 `lines: choice [1,2,4,8]` 这种
「几线」的旋钮根本没法引用——生成的 BSV 拿 `cfg.lines` 当布尔用，编不过。这里查
四件事：档位特性发 `Integer`、门控展开成逐档比较、布尔特性原样不变、同名两种写法当场报。

反例在 `MUT`：把 8 从档位表里去掉，只有「8 那一档的门控没了」这一条会红。

`uv run python tests/test_choice.py`
"""
import sys
from types import SimpleNamespace

from xirang_core.manifest import Bad
from xirang_gen.regmap import bsv

LINES = {"name": "fmt", "offset": 0, "feature": {"lines": [2, 4, 8]},
         "fields": [{"name": "val", "width": 4, "sw": "rw", "hw": "r", "reset": 0}]}
QUAD = {"name": "fmt", "offset": 0, "feature": "quad",
        "fields": [{"name": "val", "width": 4, "sw": "rw", "hw": "r", "reset": 0}]}
GATE = "(cfg.lines == 2 || cfg.lines == 4 || cfg.lines == 8)"


def gen(*regs) -> str:
    spec = {"ip": "t", "contract": {"aw": 8, "dw": 32}, "regs": list(regs)}
    return bsv(SimpleNamespace(regmap=spec, name="t"))


def main() -> int:
    bad: list[str] = []

    txt = gen(LINES)
    if "Integer lines;" not in txt:
        bad.append("档位特性没发成 Integer，Cfg 里仍是布尔")
    if f"if ({GATE}) begin" not in txt:
        bad.append("写译码没按档位门控")

    # 字段级档位：整个寄存器还在，只有这一段位读回零
    fld = {"name": "fmt", "offset": 0,
           "fields": [{"name": "val", "bits": "3:0", "sw": "rw", "hw": "r", "reset": 0},
                      {"name": "wide", "bits": "7:4", "sw": "rw", "hw": "r",
                       "reset": 0, "feature": {"lines": [2, 4, 8]}}]}
    if f"({GATE} ? " not in gen(fld):
        bad.append("字段级档位没门控读回")

    # 布尔特性一个字不变
    txt = gen(QUAD)
    if "Bool quad;" not in txt or "cfg.quad" not in txt:
        bad.append("布尔特性被连累了")

    # 反例：档位表写漏 8
    mut = {**LINES, "feature": {"lines": [2, 4]}}
    if "cfg.lines == 8" in gen(mut):
        bad.append("反例没生效：档位表里没有 8，门控却还拦得住 8")

    # 同名两种写法是矛盾
    try:
        gen({**LINES, "offset": 0},
            {**QUAD, "name": "g", "offset": 4, "feature": {"quad": [1]},
             "fields": [{"name": "v", "width": 1, "sw": "rw", "hw": "r",
                         "reset": 0, "feature": "quad"}]})
        bad.append("同一个特性两种写法没报错")
    except Bad:
        pass

    for line in bad:
        print(f"✘ {line}")
    if not bad:
        print("✔ 档位特性发 Integer、门控逐档展开、布尔特性不变、漏档拦不住、写法矛盾当场报")
    return 1 if bad else 0


def test_choice():
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
