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


def mk(root: pathlib.Path, files: dict, diagnostics=None, rtl=None, top=None, **emit) -> Pkg:
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
    ip |= top or {}
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


UNIT = {"hw/c.sv": "parameter W = 4;\n",
        "hw/top.sv": "module top #(parameter int N = 1) (input logic clk, input logic [W-1:0] x);\n"
                     "endmodule\n"}


def test_unit_scope_declarations_need_a_single_unit(tmp_path):
    """VeriGPU 把位宽写在 $unit 层的 parameter 里，别的文件直接用。"""
    pk = mk(tmp_path, UNIT, rtl=["hw/c.sv", "hw/top.sv"])
    assert codes(foreign.receipt(pk, {"n": V(1)})) == ["slang:UndeclaredIdentifier"]
    pk = mk(tmp_path, UNIT, rtl=["hw/c.sv", "hw/top.sv"], unit="single")
    assert foreign.receipt(pk, {"n": V(1)}) == []
    with pytest.raises(Bad, match="unit"):
        mk(tmp_path, UNIT, rtl=["hw/c.sv", "hw/top.sv"], unit="many").foreign_emit()


def test_an_upstream_test_can_be_a_task(tmp_path):
    from xirang_flow import matrix
    ok = {"tasks": {"t": "true"}, "test": {"upstream": [{"name": "u", "task": "t"}]}}
    pk = mk(tmp_path, {"hw/top.sv": TOP}, top=ok)
    rep = matrix.run(pk, {pk.name: pk}, out=tmp_path / "o")
    assert rep.rows and all(r.mark.name == "ok" for r in rep.rows)
    assert all("跑了展开、上游 u" in r.note for r in rep.rows), "只打勾不说跑了什么，没跑也看不出来"
    ok["tasks"]["t"] = "false"
    pk = mk(tmp_path, {"hw/top.sv": TOP}, top=ok)
    rep = matrix.run(pk, {pk.name: pk}, out=tmp_path / "o2")
    assert any("XR-TASK-004" in r.note for r in rep.rows)
    with pytest.raises(Bad, match="tasks 里没有"):
        mk(tmp_path, {"hw/top.sv": TOP},
           top={"test": {"upstream": [{"name": "u", "task": "nope"}]}})
    with pytest.raises(Bad, match="不能再写"):
        mk(tmp_path, {"hw/top.sv": TOP},
           top={"tasks": {"t": "true"},
                "test": {"upstream": [{"name": "u", "task": "t", "dut": "x"}]}})


def test_a_setup_task_runs_before_its_sources_exist(tmp_path):
    """生成器类上游：glob 指向的文件要 setup 跑完才有，跑 setup 本身不能先要它们。"""
    from xirang_flow import tasks
    top = {"tasks": {"mk": "bash -c 'mkdir -p gen && touch gen/g.sv'"}}
    pk = mk(tmp_path, {"hw/top.sv": TOP}, rtl=["hw/top.sv", {"glob": "gen/*.sv"}],
            setup="mk", top=top)
    with pytest.raises(Bad, match="XR-SRC-001"):
        foreign.files(pk)
    tasks.run(pk, "mk", {"n": V(1)}, tmp_path / "o")
    assert [f.name for f in foreign.files(pk)] == ["top.sv", "g.sv"]


def test_probes_run_at_every_point_of_the_matrix(tmp_path):
    """投影写死成字面量：默认那一点照样对得上，只有换一档才露出来。"""
    from xirang_flow import matrix
    rc = [{"symbol": "p::PORTS", "expect": {"eq": "{{n}}"}}]
    pk = mk(tmp_path, {"hw/top.sv": TOP}, receipt=rc, defines={"N_PORTS": 1},
            top={"params": {"n": {"type": "int", "default": 1, "range": [1, 4]}}})
    assert foreign.receipt(pk, {"n": V(1)}) == []
    rep = matrix.run(pk, {pk.name: pk}, out=tmp_path / "o")
    bad = [r for r in rep.rows if r.mark.name == "bad"]
    assert bad and all("XR-RCPT-002" in r.note for r in bad)


OFF = """module top #(parameter int N = 1) (input logic clk);
// synthesis translate_off
wire unused = &{1'b0, SW[16:7]};
// synthesis translate_on
endmodule
"""


def test_translate_off_is_skipped_like_the_synthesis_path(tmp_path):
    """ao486 在 translate_off 里引用了一个根本不存在的 SW；yosys 那边本来就抹掉了。"""
    pk = mk(tmp_path, {"hw/top.sv": OFF})
    assert foreign.receipt(pk, {"n": V(1)}) == []
    (tmp_path / "hw/top.sv").write_text(OFF.replace("// synthesis translate_off", ""))
    assert codes(foreign.receipt(Pkg(tmp_path), {"n": V(1)})) == ["slang:UndeclaredIdentifier"]


VENDOR = """module top #(parameter int N = 1) (input logic clk, output logic [7:0] q);
  xpm_memory_sdpram #(.MEMORY_SIZE(64)) u(.clka(clk), .doutb(q));
endmodule
"""


def test_vendor_primitives_pass_as_black_boxes_when_allowed(tmp_path):
    """nop-plus 生成的核例化 Xilinx 的 xpm_memory：没有源码，只能当黑盒。"""
    pk = mk(tmp_path, {"hw/top.sv": VENDOR})
    assert codes(foreign.receipt(pk, {"n": V(1)})) == ["slang:UnknownModule"]
    pk = mk(tmp_path, {"hw/top.sv": VENDOR}, diagnostics={"allow": ["slang:UnknownModule"]})
    got = foreign.receipt(pk, {"n": V(1)})
    assert codes(got) == ["slang:UnknownModule"] and "--ignore-unknown-modules" in got[0][1]


def test_relaxing_what_the_front_end_cannot_relax_is_refused(tmp_path):
    with pytest.raises(Bad, match="XR-DIAG-001"):
        mk(tmp_path, {"hw/top.sv": TOP}, diagnostics={"allow": ["slang:UndeclaredIdentifier"]})
