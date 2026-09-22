"""烘焙链：别人的参数化 RTL 要变成一份没有参数、可复现的纯 Verilog。

这一路上每一步出错都是静默的：旋钮名写错拿到默认配置、枚举档位递成字符串、
输出目录漏进线名，产物都照样生成，只是描述的不是我们配的那颗核。

`uv run pytest tests/test_bake.py`
"""
import copy
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from test_foreign import OK, mk  # noqa: E402

from xirang_back import verilog  # noqa: E402
from xirang_back.sv import _lit  # noqa: E402
from xirang_core.manifest import Bad, Pkg  # noqa: E402
from xirang_gen import foreign  # noqa: E402


def test_big_values_get_a_base():
    """slang 不收超出有符号 32 位的裸十进制。picorv32 的 LATCHED_IRQ 就是全 1。"""
    assert _lit(0) == "0"
    assert _lit(2 ** 31 - 1) == "2147483647"
    assert _lit(2 ** 32 - 1) == "'hffffffff"
    assert _lit(True) == "1"
    assert _lit("RV32MFast") == "RV32MFast"


def gated(tmp_path) -> Pkg:
    ip = copy.deepcopy(OK)
    ip["params"]["fpu"] = {"type": "int", "default": 0, "range": [0, 1]}
    ip["emit"][0]["rtl"] = ["hwsrc/uart_core.v",
                            {"path": "hwsrc/fpu.v", "when": {"fpu": 1}}]
    (tmp_path / "hwsrc").mkdir(parents=True, exist_ok=True)
    (tmp_path / "hwsrc/fpu.v").write_text("module fpu; endmodule" + chr(10))
    return mk(ip, tmp_path)


def test_when_picks_the_file_set(tmp_path):
    """上游的某些源码只在某档配置下才编得动，清单要说得出来。"""
    pkg = gated(tmp_path)
    assert len(foreign.files(pkg)) == 2, "不给旋钮就全要"
    assert len(foreign.files(pkg, knobs={"fpu": 0})) == 1
    assert len(foreign.files(pkg, knobs={"fpu": 1})) == 2


def test_when_on_a_knob_that_does_not_exist(tmp_path):
    ip = copy.deepcopy(OK)
    ip["emit"][0]["rtl"] = [{"path": "hwsrc/uart_core.v", "when": {"nope": 1}}]
    with pytest.raises(Bad, match="不存在的旋钮"):
        mk(ip, tmp_path).foreign_emit()


def test_paths_go_in_relative(tmp_path):
    """yosys 把递进去的路径写进函数局部线的名字，绝对路径会让产物跟着目录变。"""
    got = verilog._under([tmp_path / "a/b.v", pathlib.Path("/elsewhere/c.v")], tmp_path)
    assert str(got[0]) == "a/b.v"
    assert str(got[1]) == "/elsewhere/c.v", "出了基准目录就只能原样递"
    assert verilog._under([tmp_path / "a.v"], None)[0].is_absolute()


def test_includes_reach_both_tools(tmp_path):
    s = verilog.script([tmp_path / "a.v"], "top", {}, tmp_path / "o.v",
                       defines=["SYNTHESIS"], includes=[tmp_path / "inc"])
    assert "-DSYNTHESIS" in s and f"-I{tmp_path / 'inc'}" in s
