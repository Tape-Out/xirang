"""`ran show` 与 `inspect --deps` / `--pins`：三种新视图各回答一个问题。

`list` 回答「有哪些包」，`show` 回答「这一个包长什么样」；`inspect` 默认按实例，
`--deps` 按依赖——同一个包被三个实例用到，那边三次，这边一次；`--pins` 只出物理
端点，给封装与板级。

黑盒的端点从前一个都显示不出来（端点表只认 `kind: bsv`），五个上游核接进来之后
这一条就是「打印不出来说明清单没说清楚」的反面：清单说清楚了，打印却不认。

`uv run pytest tests/test_views2.py`
"""
from types import SimpleNamespace

import pytest

from xirang_core.model import Candidate, Instance, Resolved, Value
from xirang_out import view


def val(v):
    return Value(name="k", value=v, winner=Candidate("ip-default", v, "x:1"))


def fake(name, *, asm=False, lib=False, knobs=None, ip=None, targets=None,
         regmap=None, root="/x"):
    return SimpleNamespace(
        guards=lambda: [],
        name=name, root=root, is_assembly=asm, is_library=lib,
        knobs=lambda: knobs or {}, ip=ip or {"version": "0.1.0"},
        regmap=regmap, targets=lambda: targets or {"regs": {"driver": "regs"}})


FOREIGN = fake("ibex", ip={
    "version": "0.1.0",
    "identity": {"maturity": "planned", "summary": "black box"},
    "contract": {"ctrl": {"shape": "none"}},
    "emit": [{"kind": "foreign", "top": "ibex_top", "ports": [
        {"endpoint": "instr", "kind": "transaction", "role": "manager",
         "profile": "ibex-mem", "prefix": "instr_"},
        {"endpoint": "irq", "kind": "event", "role": "sink", "prefix": "irq_"},
        {"endpoint": "ram", "kind": "physical", "type": "IbexRamCfg"},
    ]}]},
    knobs={"rv32M": {"type": "choice",
                     "values": ["RV32MNone", "RV32MFast"],
                     "default": "RV32MFast"}})


def test_endpoints_read_a_black_box():
    """黑盒的端点自带 kind 与 role，不必猜——从前这一支整个不认。"""
    got = view.endpoints(FOREIGN)
    assert ("instr", "txn/manager") == got[0][:2]
    assert ("irq", "event/sink") == got[1][:2]
    assert got[2][:2] == ("ram", "physical")
    assert "ibex-mem" in got[0][2] and "instr_" in got[0][2]


def test_show_says_what_a_package_is():
    t = view.show(FOREIGN, {"ibex": FOREIGN})
    for want in ("ibex", "planned", "rv32M", "RV32MFast",
                 "txn/manager", "未知（XR-AREA-006）"):
        assert want in t, want


def test_show_does_not_call_a_missing_price_table_zero():
    """没量出来就是未知。写成 0 µm² 是「把没量说成过了」的同一族错。"""
    t = view.show(fake("x"), {})
    assert "未知" in t and "0.00 µm²" not in t


def test_show_renders_a_curve_not_just_a_number():
    pk = fake("uart", ip={"version": "0.1.0", "area": {
        "base": {"per": "fifoDepth", "points": {"1": 1891.68, "8": 3530.24}},
        "margin": 0.047}})
    t = view.show(pk, {})
    assert "按 fifoDepth 的曲线" in t and "2 个点" in t and "4.7%" in t


def test_show_lists_who_uses_it():
    lib = fake("hwcore", lib=True)
    soc = fake("soc", asm=True, ip={"version": "0.1.0",
                                    "instances": [{"of": "hwcore", "name": "h"}]})
    uart = fake("uart", ip={"version": "0.1.0", "deps": {"hwcore": "^0.1"}})
    t = view.show(lib, {"hwcore": lib, "soc": soc, "uart": uart})
    assert "被谁用" in t and "soc" in t and "uart" in t


def test_show_says_where_a_dependency_comes_from():
    pk = fake("soc", ip={"version": "0.1.0", "deps": {
        "bus": {"path": "../bus", "req": "^0.1"},
        "far": {"git": "https://example.invalid/far", "rev": "a" * 40}}})
    t = view.show(pk, {})
    assert "path ../bus" in t
    assert "git https://example.invalid/far @" + "a" * 12 in t


DEPS_RES = Resolved(top="soc", bus="apb4", area_um2=0.0, instances=[
    Instance(name="u0", of="uart", values={}, area_um2=1.0),
    Instance(name="u1", of="uart", values={}, area_um2=1.0),
])
DEPS_PKGS = {
    "soc": fake("soc", asm=True, ip={"version": "0.1.0", "instances": [
        {"of": "uart", "name": "u0"}, {"of": "uart", "name": "u1"}]}),
    "uart": fake("uart", ip={"version": "0.1.0", "deps": {"hwcore": "^0.1"}}),
    "hwcore": fake("hwcore", lib=True),
}


def test_deps_counts_a_package_once():
    """两个实例同一个包，依赖树上只出现一次——这是它与 `tree` 的分工。"""
    t = view.deps(DEPS_RES, DEPS_PKGS)
    assert t.count("uart  0.1.0") == 1
    assert "3 个包" in t
    assert "hwcore" in t


def test_deps_says_when_a_package_is_missing():
    t = view.deps(DEPS_RES, {k: v for k, v in DEPS_PKGS.items() if k != "hwcore"})
    assert "找不到" in t


PINS_RES = Resolved(top="soc", bus="apb4", area_um2=0.0, instances=[
    Instance(name="cpu", of="hart", values={}, area_um2=1.0),
    Instance(name="uart0", of="uart", values={}, area_um2=1.0),
])
PINS_PKGS = {
    "uart": SimpleNamespace(guards=lambda: [], ip={
        "contract": {"ctrl": {"shape": "flat", "aw": 8, "dw": 32}},
        "emit": [{"kind": "bsv", "pins": [{"name": "pins", "type": "UartPins"}]}]}),
    # 发起口接进交换网，不引到顶层，所以不该出现在引脚表里
    "hart": SimpleNamespace(guards=lambda: [], ip={
        "contract": {"ctrl": {"shape": "none"}},
        "emit": [{"kind": "bsv", "pins": [{"name": "imem", "type": "RegManager"}]}]}),
}


def test_pins_leaves_out_what_does_not_reach_the_top():
    t = view.pins(PINS_RES, PINS_PKGS)
    assert "UartPins" in t and "RegManager" not in t
    assert "1 组物理端点" in t


def test_pins_says_so_when_there_are_none():
    t = view.pins(PINS_RES, {"hart": PINS_PKGS["hart"], "uart": PINS_PKGS["hart"]})
    assert "没有引到顶层的物理端点" in t


@pytest.mark.parametrize("name", ["text", "json", "mermaid", "deps", "pins"])
def test_every_view_is_reachable(name):
    assert callable(view.VIEWS[name])
