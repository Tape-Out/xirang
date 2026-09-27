"""回执的两层检查、放宽的 slang 诊断、库目录与被盖掉的 Flist 宏。

`uv run pytest tests/test_receipt.py`
"""
import pathlib

import pytest
import yaml

from xirang_back import sv
from xirang_core.manifest import Bad, Pkg
from xirang_flow.matrix import route
from xirang_gen import foreign

pytestmark = pytest.mark.skipif(not sv.available(), reason="要 pyslang")


class V:
    def __init__(self, v):
        self.value = v


TOP = """package p;
  localparam int PORTS = `N_PORTS;
endpackage
module top #(parameter int N = 1) (input logic clk, input logic [7:0] mem [N]);
endmodule
"""


def mk(root: pathlib.Path, files: dict, diagnostics=None, rtl=None, **emit) -> Pkg:
    for f, text in files.items():
        (root / f).parent.mkdir(parents=True, exist_ok=True)
        (root / f).write_text(text, encoding="utf-8")
    ip = {"name": "vx", "version": "0.1.0", "spec": "0.1", "kind": "ip",
          "lang": "verilog",
          "identity": {"slug": "v", "display_name": "v", "summary": "v",
                       "category": "processor", "ip_family": "v",
                       "maturity": "planned"},
          "contract": {"version": 1, "ctrl": {"shape": "none", "aw": 32, "dw": 32}},
          "params": {"n": {"type": "int", "default": 1}},
          "emit": [{"kind": "foreign", "lang": "sv", "top": "top",
                    "rtl": rtl or ["hw/top.sv"], "params": {"N": "n"},
                    "defines": {"N_PORTS": "n"},
                    "clock": {"port": "clk"},
                    "ports": [{"endpoint": "pins", "kind": "physical",
                               "type": "Pins", "map": {"clk": "clk"}}], **emit}]}
    if diagnostics:
        ip["diagnostics"] = diagnostics
    (root / "ip.yaml").write_text(yaml.safe_dump(ip, allow_unicode=True), encoding="utf-8")
    return Pkg(root)


def codes(found):
    return [c for c, _ in found]


def test_a_zero_length_port_array_is_caught(tmp_path):
    pk = mk(tmp_path, {"hw/top.sv": TOP})
    assert foreign.receipt(pk, {"n": V(2)}) == []
    assert "XR-RCPT-001" in codes(foreign.receipt(pk, {"n": V(0)}))


def test_probes_read_package_constants(tmp_path):
    rc = [{"symbol": "p::PORTS", "expect": {"eq": "{{n}}", "ge": 1}}]
    pk = mk(tmp_path, {"hw/top.sv": TOP}, receipt=rc)
    assert foreign.receipt(pk, {"n": V(3)}) == []


def test_a_failed_expectation_is_reported(tmp_path):
    rc = [{"symbol": "p::PORTS", "expect": {"ge": 4}}]
    pk = mk(tmp_path, {"hw/top.sv": TOP}, receipt=rc)
    assert codes(foreign.receipt(pk, {"n": V(2)})) == ["XR-RCPT-002"]


def test_a_probe_that_finds_nothing_is_an_error(tmp_path):
    pk = mk(tmp_path, {"hw/top.sv": TOP}, receipt=[{"symbol": "p::NOPE"}])
    assert codes(foreign.receipt(pk, {"n": V(2)})) == ["XR-RCPT-003"]


def test_a_probe_with_an_unknown_op_is_refused(tmp_path):
    with pytest.raises(Bad, match="expect"):
        mk(tmp_path, {"hw/top.sv": TOP},
           receipt=[{"symbol": "p::PORTS", "expect": {"gt": 1}}]).foreign_emit()


UBD = """module top #(parameter int N = 1) (input logic clk, output logic o);
  assign o = w;
  logic w;
  assign w = clk;
endmodule
"""


def test_an_allowed_slang_diagnostic_runs_and_stays_in_the_report(tmp_path):
    pk = mk(tmp_path, {"hw/top.sv": UBD})
    got = foreign.receipt(pk, {"n": V(1)})
    assert codes(got) == ["slang:UsedBeforeDeclared"] and "top.sv:2" in got[0][1]
    stop, _ = route(got, [])
    assert stop, "不写 allow 就挡住"
    pk = mk(tmp_path, {"hw/top.sv": UBD},
            diagnostics={"allow": ["slang:UsedBeforeDeclared"]})
    got = foreign.receipt(pk, {"n": V(1)})
    assert codes(got) == ["slang:UsedBeforeDeclared"]
    stop, note = route(got, [("包 ip.yaml", pk.ip.get("diagnostics"))])
    assert not stop and "--allow-use-before-declare" in note[0]


LEAF = "module leaf(input logic clk); endmodule\n"
USE = """module top #(parameter int N = 1) (input logic clk);
  leaf u(.clk);
endmodule
"""


def test_library_dirs_from_a_flist_resolve_modules(tmp_path):
    files = {"hw/top.sv": USE, "lib/leaf.sv": LEAF, "hw/all.f": "-y lib\n+libext+.sv\n"}
    pk = mk(tmp_path, files, rtl=["hw/top.sv", {"flist": "hw/all.f"}])
    assert foreign.receipt(pk, {"n": V(1)}) == []
    pk = mk(tmp_path, files, rtl=["hw/top.sv"])
    assert codes(foreign.receipt(pk, {"n": V(1)})) == ["slang:UnknownModule"]


def test_a_flist_macro_overridden_by_the_manifest_is_noted(tmp_path):
    files = {"hw/top.sv": TOP, "hw/all.f": "+define+N_PORTS=9\n"}
    pk = mk(tmp_path, files, rtl=["hw/top.sv", {"flist": "hw/all.f"}])
    assert foreign.defines(pk, {"n": V(2)}) == ["N_PORTS=2"]
    assert codes(foreign.receipt(pk, {"n": V(2)})) == ["XR-SRC-003"]
