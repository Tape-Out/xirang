"""宏也能是旋钮的投影。

有些上游一个 Verilog 参数都没有，整份配置靠带值的宏给：Vortex 的综合流程写的是
`-DVX_CFG_NUM_CORES=4 -DVX_CFG_L2_ENABLE`。同一套投影还要能喂给上游自己的生成器
（`{{defines}}`），这样「配置从哪来」仍然只有清单一个源头。

布尔那条要当心：`ifdef` 判的是定义没定义，给它 `=0` 反而是打开。

`uv run pytest tests/test_macros.py`
"""
import pathlib

import pytest
import yaml

from xirang_core.manifest import Bad, Pkg
from xirang_flow import tasks
from xirang_gen import foreign


class V:
    def __init__(self, v):
        self.value = v


def mk(root: pathlib.Path, defines, knobs=None, feats=None) -> Pkg:
    (root / "hwsrc").mkdir(parents=True, exist_ok=True)
    (root / "hwsrc/top.v").write_text("module top; endmodule", encoding="utf-8")
    ip = {"name": "vx", "version": "0.1.0", "spec": "0.1", "kind": "ip",
          "lang": "verilog",
          "identity": {"slug": "v", "display_name": "v", "summary": "v",
                       "category": "processor", "ip_family": "v",
                       "maturity": "planned"},
          "contract": {"version": 1, "ctrl": {"shape": "none", "aw": 32,
                                              "dw": 32}},
          "params": knobs or {"cores": {"type": "int", "default": 1}},
          # 定宽的是 param，开关是 feature——bool 写进 params 会被清单挡掉
          "features": feats or {},
          "emit": [{"kind": "foreign", "lang": "verilog", "top": "top",
                    "rtl": ["hwsrc/top.v"], "defines": defines,
                    "clock": {"port": "clk"},
                    "reset": {"port": "rst_n", "active": "low", "sync": True},
                    "ports": [{"endpoint": "pins", "kind": "physical",
                               "type": "Pins", "map": {"a": "a"}}]}]}
    (root / "ip.yaml").write_text(yaml.safe_dump(ip, allow_unicode=True),
                                  encoding="utf-8")
    return Pkg(root)


def test_a_list_still_means_plain_macros(tmp_path):
    pk = mk(tmp_path, ["RISCV_FORMAL", "SYNTHESIS"])
    assert foreign.defines(pk) == ["RISCV_FORMAL", "SYNTHESIS"]


def test_a_table_projects_a_knob_into_a_value(tmp_path):
    pk = mk(tmp_path, {"VX_CFG_NUM_CORES": "cores"})
    assert foreign.defines(pk, {"cores": V(4)}) == ["VX_CFG_NUM_CORES=4"]


def test_without_resolved_values_the_default_is_used(tmp_path):
    pk = mk(tmp_path, {"VX_CFG_NUM_CORES": "cores"})
    assert foreign.defines(pk) == ["VX_CFG_NUM_CORES=1"]


def test_a_literal_needs_no_knob(tmp_path):
    pk = mk(tmp_path, {"VX_CFG_XLEN": 32})
    assert foreign.defines(pk) == ["VX_CFG_XLEN=32"]


def test_a_boolean_knob_defines_the_macro_or_leaves_it_out(tmp_path):
    """`ifdef 判的是定义没定义。给它 =0 反而是打开——所以关掉时不能出现。"""
    pk = mk(tmp_path, {"VX_CFG_L2_ENABLE": {"when": "l2"}},
            feats={"l2": {"type": "bool", "default": False}})
    assert foreign.defines(pk, {"l2": V(True)}) == ["VX_CFG_L2_ENABLE"]
    assert foreign.defines(pk, {"l2": V(False)}) == []


def test_a_macro_pointing_at_no_knob_is_refused(tmp_path):
    with pytest.raises(Bad, match="不存在的旋钮"):
        mk(tmp_path, {"VX_CFG_NUM_CORES": "nope"}).foreign_emit()


def test_only_when_is_allowed_in_the_table(tmp_path):
    with pytest.raises(Bad, match="只认 when"):
        mk(tmp_path, {"M": {"unless": "l2"}}).foreign_emit()


def test_the_same_projection_feeds_the_upstream_generator(tmp_path):
    """一处定义两处使用：展开要它，上游自己的生成器也要它。"""
    pk = mk(tmp_path, {"VX_CFG_NUM_CORES": "cores", "VX_CFG_XLEN": 32})
    hole = tasks.holes(pk, {"cores": V(4)}, tmp_path / "build")
    assert hole["defines"] == "-DVX_CFG_NUM_CORES=4 -DVX_CFG_XLEN=32"
    got = tasks.fill("gen --cflags=\"{{defines}}\"", hole, "setup")
    assert got == 'gen --cflags="-DVX_CFG_NUM_CORES=4 -DVX_CFG_XLEN=32"'


FPU = {"fpu": {"type": "choice", "values": ["STD", "DPI"], "default": "STD"},
       "tcu": {"type": "choice", "values": ["TFR", "DSP", "BHF"], "default": "TFR"}}


def test_a_select_macro_carries_the_level_in_its_name(tmp_path):
    """RTL 按 `ifdef VX_CFG_FPU_TYPE_DPI 选实现，只给值宏选不中。"""
    pk = mk(tmp_path, {"VX_CFG_FPU_TYPE": "fpu", "VX_CFG_FPU_TYPE_{{fpu}}": True},
            knobs=FPU)
    assert foreign.defines(pk, {"fpu": V("DPI")}) == ["VX_CFG_FPU_TYPE=DPI",
                                                      "VX_CFG_FPU_TYPE_DPI"]


def test_a_map_gives_each_level_its_value_and_null_leaves_it_out(tmp_path):
    m = {"VX_CFG_TCU": {"from": "tcu", "map": {"TFR": "tfr", "DSP": "dsp", "BHF": None}}}
    pk = mk(tmp_path, m, knobs=FPU)
    assert foreign.defines(pk, {"tcu": V("DSP")}) == ["VX_CFG_TCU=dsp"]
    assert foreign.defines(pk, {"tcu": V("BHF")}) == []


def test_a_map_missing_a_level_is_refused(tmp_path):
    m = {"VX_CFG_TCU": {"from": "tcu", "map": {"TFR": "tfr"}}}
    with pytest.raises(Bad, match=r"XR-MACRO-002.*DSP"):
        mk(tmp_path, m, knobs=FPU).foreign_emit()


def test_a_placeholder_must_name_a_choice_knob(tmp_path):
    with pytest.raises(Bad, match="XR-MACRO-001"):
        mk(tmp_path, {"N_{{cores}}": True}).foreign_emit()
    with pytest.raises(Bad, match="XR-MACRO-001.*不存在"):
        mk(tmp_path, {"N_{{nope}}": True}, knobs=FPU).foreign_emit()


def test_a_switch_macro_needs_a_boolean_knob(tmp_path):
    with pytest.raises(Bad, match="不是布尔旋钮"):
        mk(tmp_path, {"VX_CFG_L2_ENABLE": {"when": "cores"}}).foreign_emit()
