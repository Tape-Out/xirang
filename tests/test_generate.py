"""`generate:`：拿上游的一份常量文件当模板，只换我们暴露出去的那几项。

有一类上游的配置既不是 Verilog 参数、也不是宏，而是一份写死的常量文件——
CVA6 的 `cva6_config_pkg.sv` 是 49 个 `localparam`，`-D` 碰不到。它自己的扩展点
就是「编哪一个配置包」，所以我们生成第十四个，而不是去改上游的十三个。

最要紧的判据是**必须恰好命中一次**：名字写错、或者上游换了写法，都要当场报，
不能悄悄什么都没换。实测它拦下过一次——32 位那份模板没有 `CVA6ConfigBExtEn`。

`uv run pytest tests/test_generate.py`
"""
import pathlib

import pytest
import yaml

from xirang_core.manifest import Bad, Pkg
from xirang_gen import foreign


class V:
    def __init__(self, v):
        self.value = v


TPL = [
    "package cfg_pkg;",
    "  localparam Xlen = 64;",
    "  localparam config_pkg::cache_type_t DcacheType = config_pkg::WT;",
    "  localparam RVF = 1;",
    "  localparam Derived = Xlen;",
    "endpackage",
]


def mk(root: pathlib.Path, gen, knobs=None, feats=None) -> Pkg:
    (root / "up").mkdir(parents=True, exist_ok=True)
    (root / "up/tpl.sv").write_text(chr(10).join(TPL) + chr(10), encoding="utf-8")
    (root / "hwsrc").mkdir(exist_ok=True)
    (root / "hwsrc/top.v").write_text("module top; endmodule", encoding="utf-8")
    ip = {"name": "c", "version": "0.1.0", "spec": "0.1", "kind": "ip",
          "lang": "verilog",
          "identity": {"slug": "c", "display_name": "c", "summary": "c",
                       "category": "processor", "ip_family": "c",
                       "maturity": "planned"},
          "contract": {"version": 1, "ctrl": {"shape": "none", "aw": 32,
                                              "dw": 32}},
          "params": knobs or {"dcache": {"type": "int", "default": 8}},
          "features": feats or {},
          "emit": [{"kind": "foreign", "lang": "verilog", "top": "top",
                    "rtl": ["hwsrc/top.v", "gen/out.sv"],
                    "generate": gen,
                    "clock": {"port": "clk"},
                    "reset": {"port": "rst_n", "active": "low",
                              "sync": True},
                    "ports": [{"endpoint": "pins", "kind": "physical",
                               "type": "P", "map": {"a": "a"}}]}]}
    (root / "ip.yaml").write_text(yaml.safe_dump(ip, allow_unicode=True),
                                  encoding="utf-8")
    return Pkg(root)


def one(**kw):
    return [{"out": "gen/out.sv", "from": "up/tpl.sv",
             "set": {"dcache": "Xlen"}, **kw}]


def test_the_output_need_not_exist_yet(tmp_path):
    """它由息壤写出来，落盘之前当然不在树上——清单不该因此报错。"""
    pk = mk(tmp_path, one())
    assert pk.foreign_emit()["top"] == "top"


def test_only_the_named_field_changes(tmp_path):
    pk = mk(tmp_path, one())
    got = foreign.generate(pk, {"dcache": V(32)})
    assert got == [("gen/out.sv", 1)]
    txt = (tmp_path / "gen/out.sv").read_text(encoding="utf-8")
    assert "localparam Xlen = 32;" in txt
    assert "localparam RVF = 1;" in txt, "没点名的要保持上游值"
    assert "localparam Derived = Xlen;" in txt, "引用它的地方原样不动"
    assert "息壤" in txt.splitlines()[0], "产物要标明是生成的"


def test_a_name_that_is_not_there_is_refused(tmp_path):
    """命中 0 次就是名字写错，或者上游换了写法——不能悄悄什么都没换。"""
    pk = mk(tmp_path, one(set={"dcache": "NoSuchField"}))
    with pytest.raises(Bad, match="命中 0 次"):
        foreign.generate(pk, {"dcache": V(1)})


def test_when_gates_which_template_is_used(tmp_path):
    pk = mk(tmp_path, [{"out": "gen/out.sv", "from": "up/tpl.sv",
                        "when": {"dcache": 64}, "set": {"dcache": "Xlen"}}])
    assert foreign.generate(pk, {"dcache": V(8)}) == [], "条件不满足就不生成"
    assert foreign.generate(pk, {"dcache": V(64)}) == [("gen/out.sv", 1)]


def test_a_boolean_becomes_one_or_zero(tmp_path):
    pk = mk(tmp_path, one(set={"rvf": "RVF"}),
            knobs={"dcache": {"type": "int", "default": 8}},
            feats={"rvf": {"type": "bool", "default": True}})
    foreign.generate(pk, {"rvf": V(False)})
    assert "localparam RVF = 0;" in (tmp_path / "gen/out.sv").read_text(
        encoding="utf-8")


def test_an_unknown_key_is_refused(tmp_path):
    with pytest.raises(Bad, match="只认 out/from/when/set/syntax"):
        mk(tmp_path, one(into="x"))


def test_a_knob_that_does_not_exist_is_refused(tmp_path):
    with pytest.raises(Bad, match="不存在的旋钮"):
        mk(tmp_path, one(set={"nope": "Xlen"}))


def test_an_unknown_syntax_is_refused(tmp_path):
    with pytest.raises(Bad, match="syntax"):
        mk(tmp_path, one(syntax="define"))


def test_a_missing_template_is_named(tmp_path):
    pk = mk(tmp_path, one(**{"from": "up/gone.sv"}))
    with pytest.raises(Bad, match="不在树上"):
        foreign.generate(pk, {"dcache": V(1)})
