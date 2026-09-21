"""`ran inspect`：把解出来的那颗芯片打印出来，三种视图读同一份数据。

打印的是**解出来的**那份，不是清单——清单说「省略则用默认」，这里必须是最终值。
端点一律标 `种类/角色`：打印不出来，就说明清单没把它说清楚。

两个坑各有一条判据：Python 里 `1 == True`，拿字典查字面量会把 `csWidth=1` 印成 `T`；
中日韩字符占两格，按字符数排版中文表头会错位。

`uv run python tests/test_view.py`
"""
import json
import sys
from types import SimpleNamespace

from xirang_core.model import Candidate, Instance, Resolved, Value
from xirang_out import view


def val(v):
    return Value(name="k", value=v, winner=Candidate("ip-default", v, "x:1"))


PKGS = {
    "uart": SimpleNamespace(ip={
        "contract": {"ctrl": {"shape": "flat", "aw": 8, "dw": 32},
                     "irq": [{"name": "irq", "kind": "level"}]},
        "emit": [{"kind": "bsv", "pins": [{"name": "pins", "type": "UartPins"}]}],
    }),
    "hart": SimpleNamespace(ip={
        "contract": {"ctrl": {"shape": "none"}},
        "emit": [{"kind": "bsv", "pins": [{"name": "imem", "type": "RegManager"}]}],
    }),
}

RES = Resolved(top="soc", bus="apb4", area_um2=1234.5, instances=[
    Instance(name="cpu", of="hart", values={"mul": val(True)}, area_um2=1000.0),
    Instance(name="uart0", of="uart",
             values={"fifoDepth": val(8), "csWidth": val(1), "parity": val(False)},
             addr=0x10001000, area_um2=234.5),
])


def main() -> int:
    bad: list[str] = []

    if view._w("实例") != 4 or view._w("ab") != 2:
        bad.append("中日韩字符没按两格算，中文表头会错位")

    t = view.summary(RES, PKGS)
    if "csWidth=1" not in t:
        bad.append("csWidth=1 被印成了别的——Python 里 1 == True，字典查字面量会踩这个")
    if "parity=F" not in t or "mul=T" not in t:
        bad.append("布尔没印成 T/F")
    if "0x10001000" not in t:
        bad.append("地址没印出来")
    for want in ("txn/target", "txn/manager", "event/source", "phys/UartPins"):
        if want not in t:
            bad.append(f"端点形态少了 {want}")
    if "引到顶层的物理端点 1" not in t:
        bad.append("未接端点数不是一行数字")
    # 填出来的就得是那么宽，中文与西文一视同仁——列才对得齐
    for x in ("实例", "ab", "µm²"):
        if view._w(view._pad(x, 12)) != 12 or view._w(view._lpad(x, 12)) != 12:
            bad.append(f"把 {x} 填到 12 格没填对")

    d = json.loads(view.as_json(RES, PKGS))
    if len(d["instances"]) != 2 or d["instances"][1]["knobs"]["csWidth"] != 1:
        bad.append(f"json 视图与文本视图读的不是同一份：{d['instances'][1]['knobs']}")

    def fake(asm, lib, knobs, ip, targets):
        return SimpleNamespace(is_assembly=asm, is_library=lib, knobs=lambda: knobs,
                               ip=ip, targets=lambda: targets)

    idx = {
        "uart": fake(False, False, {"a": 1},
                     {"version": "0.1.0", "identity": {"maturity": "simulated"},
                      "contract": {"ctrl": {"shape": "flat", "aw": 8, "dw": 32}}},
                     {"regs": {"driver": "regs"}, "flat": {"driver": "flat"}}),
        "hwcore": fake(False, True, {}, {"version": "0.1.0"},
                       {"library": {"driver": "library"}}),
        "soc": fake(True, False, {}, {"version": "0.2.0"},
                    {"assembly": {"driver": "assembly"}}),
    }
    cat = view.catalog(idx)
    if [r[0] for r in cat] != ["hwcore", "soc", "uart"]:
        bad.append(f"目录没按名字排：{[r[0] for r in cat]}")
    if [r[2] for r in cat] != ["库", "装配", "IP"]:
        bad.append(f"类别认错了：{[r[2] for r in cat]}")
    if [r[0] for r in view.catalog(idx, "lib")] != ["hwcore"]:
        bad.append("按类别筛没生效")
    head = ("名字", "版本", "类别", "成熟度", "旋钮", "契约", "目标")
    row = dict(zip(head, cat[2]))
    if row["契约"] != "8/32":
        bad.append(f"契约签名没印出来：{cat[2]}")
    if row["目标"] != "flat+regs":
        bad.append(f"一个包的几个目标没都印出来：{row['目标']}")
    tb = view.table(head, cat)
    if len({view._w(ln) for ln in tb.splitlines()}) != 1:
        bad.append("目录表每行不等宽")

    m = view.as_mermaid(RES, PKGS)
    if m.count('["') < 3 or "graph LR" not in m:
        bad.append("mermaid 视图不成图")

    for line in bad:
        print(f"✘ {line}")
    if not bad:
        print("✔ 三种视图同源；字面量、端点形态、地址、未接端点数、列宽、包目录都对")
    return 1 if bad else 0


def test_view():
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
