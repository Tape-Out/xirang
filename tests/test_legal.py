"""`legal:`（WARL）的校验：每一条拦截都要自己拦得住，合法的写法要放得过去。

`uv run python tests/test_legal.py`
"""
import sys

from xirang_core.manifest import Bad
from xirang_gen.regmap import _rows, legal_at


def reg(field: dict, **kw) -> dict:
    return {"regs": [{"name": "a", "offset": 0, "fields": [{"name": "val", **field}], **kw}]}


AGE = {"width": 20, "sw": "rw", "reset": 300, "legal": [{"min": 10, "max": 1000000}]}

# (说明, 寄存器表, 参数, 报错里必须出现的字)
REJECT = [
    ("空列表", reg({**AGE, "legal": []}), [], "非空列表"),
    ("元素是布尔", reg({**AGE, "legal": [True]}), [], "既不是整数"),
    ("区间里多一个键", reg({**AGE, "legal": [{"min": 10, "max": 20, "step": 2}]}), [], "不认识的键"),
    ("区间缺 max", reg({**AGE, "legal": [{"min": 10}]}), [], "同时写 min 与 max"),
    ("min 大于 max", reg({**AGE, "legal": [{"min": 20, "max": 10}]}), [], "大于"),
    ("界是表达式", reg({"width": 4, "sw": "rw", "reset": 0,
                        "legal": [{"min": 0, "max": "funcs * 2"}]}), ["funcs"], "只能是整数"),
    ("界是没声明的参数", reg({"width": 4, "sw": "rw", "reset": 0,
                              "legal": [{"min": 0, "max": "funcs - 1"}]}), [], "只能是整数"),
    ("软件只读", reg({**AGE, "sw": "r"}), [], "谈不上 legal"),
    ("与 woclr 同用", reg({**AGE, "onwrite": "woclr"}), [], "不与 volatile"),
    ("与 hwset 同用", reg({**AGE, "hwset": True}), [], "不与 volatile"),
    ("没给复位", reg({k: v for k, v in AGE.items() if k != "reset"}), [], "必须给 reset"),
    ("复位值不合法", reg({**AGE, "reset": 5}), [], "复位值"),
    ("复位值只在特性开着时合法", reg({"bits": "1:0", "sw": "rw", "reset": 0,
                                      "legal": [3, {"min": 0, "max": 1, "feature": "smode"}]}),
     [], "复位值"),
    ("界放不进字段", reg({"width": 4, "sw": "rw", "reset": 8,
                          "legal": [{"min": 0, "max": 20}]}), [], "放不进"),
    ("比总线宽的寄存器", reg({"width": 64, "sw": "rw", "reset": 300,
                              "legal": [{"min": 10, "max": 1000000}]}, width=64), [], "分两半写"),
    ("写在别名上", {"regs": [
        {"name": "m", "offset": 0, "fields": [{"name": "val", "width": 8, "sw": "rw", "reset": 0}]},
        {"name": "s", "offset": 4, "alias": "m",
         "fields": [{"name": "val", "width": 8, "sw": "rw", "reset": 0, "legal": [0, 1]}]},
    ]}, [], "别名共用"),
]


def main() -> int:
    bad: list[str] = []

    for what, spec, params, frag in REJECT:
        try:
            _rows(spec, params)
        except Bad as e:
            if frag not in str(e):
                bad.append(f"{what}：报了错，但不是这一条（{e}）")
            continue
        except Exception as e:
            bad.append(f"{what}：没拦住，后面的代码撞上了 {type(e).__name__}: {e}")
            continue
        bad.append(f"{what}：没拦住")

    rs = _rows(reg(AGE), [])
    if rs[0]["fields"][0]["legal"] != [(10, 1000000, None)]:
        bad.append(f"区间没按 (lo, hi, feature) 收：{rs[0]['fields'][0]['legal']}")

    rs = _rows(reg({"width": 4, "sw": "rw", "reset": 0,
                    "legal": [{"min": 0, "max": "funcs - 1"}]}), ["funcs"])
    if rs[0]["fields"][0]["legal"] != [(0, ("funcs", -1), None)]:
        bad.append(f"「参数 - 1」没留成符号：{rs[0]['fields'][0]['legal']}")

    mpp = [(3, 3, None), (0, 1, ("smode", None))]
    on, off = {"smode": True}.get, {}.get
    for v, want_on, want_off in ((0, True, False), (2, False, False), (3, True, True)):
        if legal_at(mpp, v, on) != want_on or legal_at(mpp, v, off) != want_off:
            bad.append(f"mpp={v} 的合法性算错了")

    for line in bad:
        print(f"✘ {line}")
    if not bad:
        print(f"✔ {len(REJECT)} 条拦截各自拦得住，合法写法放得过去")
    return 1 if bad else 0


def test_legal():
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
