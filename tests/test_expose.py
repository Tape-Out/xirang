"""`expose: all`：那份上游文件既是模板，也是旋钮的唯一来源。

手抄一张「旋钮 → 字段名」的映射表，迟早与上游对不上；上游加了字段我们也不会
知道。反过来读那份文件就没有这两个问题。

**认不出就不暴露**：引用别的常量、枚举、表达式，宁可少给，不给错的。

`uv run pytest tests/test_expose.py`
"""
import pathlib

import yaml

from xirang_core.manifest import Pkg
from xirang_gen import foreign


class V:
    def __init__(self, v):
        self.value = v


TPL = [
    "package cfg;",
    "  localparam Plain = 64;",
    "  localparam Flag = 1'b0;",
    "  localparam Hex = 32'h20;",
    "  localparam Dec = 16'd12;",
    "  localparam Derived = Plain;",          # 引用别的常量：认不出
    "  localparam pkg::kind_t Kind = pkg::WT;",  # 枚举：认不出
    "  localparam Secret = 7;",
    "endpackage",
]


def mk(root: pathlib.Path, **g) -> Pkg:
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
          "emit": [{"kind": "foreign", "lang": "verilog", "top": "top",
                    "rtl": ["hwsrc/top.v", "gen/out.sv"],
                    "generate": [{"out": "gen/out.sv", "from": "up/tpl.sv",
                                  "expose": "all", **g}],
                    "clock": {"port": "clk"},
                    "reset": {"port": "rst_n", "active": "low",
                              "sync": True},
                    "ports": [{"endpoint": "pins", "kind": "physical",
                               "type": "P", "map": {"a": "a"}}]}]}
    (root / "ip.yaml").write_text(yaml.safe_dump(ip, allow_unicode=True),
                                  encoding="utf-8")
    return Pkg(root)


def test_the_file_is_the_only_source_of_knobs(tmp_path):
    k = mk(tmp_path).knobs()
    assert set(k) == {"Plain", "Flag", "Hex", "Dec", "Secret"}
    assert k["Plain"] == {"type": "int", "default": 64, "kind": "param"}
    assert k["Flag"] == {"type": "bool", "default": False, "kind": "feature"}


def test_radix_literals_are_read_as_numbers(tmp_path):
    k = mk(tmp_path).knobs()
    assert k["Hex"]["default"] == 32, "32'h20 是 32，不是 20"
    assert k["Dec"]["default"] == 12


def test_what_cannot_be_read_is_not_exposed(tmp_path):
    """引用别的常量、枚举：宁可少给，不给错的。"""
    k = mk(tmp_path).knobs()
    assert "Derived" not in k and "Kind" not in k


def test_skip_takes_a_field_out(tmp_path):
    k = mk(tmp_path, skip=["Secret"]).knobs()
    assert "Secret" not in k and "Plain" in k


def test_domain_narrows_a_field(tmp_path):
    k = mk(tmp_path, domain={"Plain": [32, 64]}).knobs()
    assert k["Plain"] == {"type": "choice", "values": [32, 64],
                          "default": 64, "kind": "param"}


def test_every_exposed_field_is_actually_substituted(tmp_path):
    pk = mk(tmp_path)
    got = foreign.generate(pk, {"Plain": V(32), "Flag": V(True),
                                "Hex": V(1), "Dec": V(2), "Secret": V(3)})
    assert got == [("gen/out.sv", 5)]
    txt = (tmp_path / "gen/out.sv").read_text(encoding="utf-8")
    assert "localparam Plain = 32;" in txt
    assert "localparam Flag = 1;" in txt
    assert "localparam Derived = Plain;" in txt, "没暴露的原样不动"
