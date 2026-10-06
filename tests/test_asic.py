"""`ran asic`：清单的 asic 段、五口顶层的排位、展平改名、ecc 报告与来源。

判据照规范六之四：排不下、重叠、漏掉、写错都要在写文件之前报，检查号对得上；
五口顶层用 iverilog 真跑一遍，看每一位的方向与取值；展平后只有顶层不带前缀，
yosys 独立展开不缺模块；时序为负默认挡住，按检查号放宽后照跑。

`uv run pytest tests/test_asic.py`
"""
import json
import pathlib
import shutil
import subprocess

import pytest
import yaml

from xirang_back import asic as back
from xirang_back.tools import ToolError
from xirang_core import manifest
from xirang_core.manifest import Bad, Pkg
from xirang_core.resolve import resolve
from xirang_flow.asic import gate
from xirang_gen import frame
from xirang_gen.assemble import assemble
from xirang_out import prov

BASE = {"version": "0.1.0", "spec": "0.1", "kind": "ip", "lang": "bsv",
        "contract": {"version": 1, "ctrl": {"shape": "flat", "aw": 8, "dw": 32}}}


def put(root: pathlib.Path, name: str, **ip) -> Pkg:
    (root / name / "hwsrc").mkdir(parents=True, exist_ok=True)
    (root / name / "hwsrc/A.bsv").write_text("package A; endpackage", encoding="utf-8")
    (root / name / "ip.yaml").write_text(
        yaml.safe_dump({**BASE, "name": name, **ip}, allow_unicode=True), encoding="utf-8")
    return Pkg(root / name)


# ---------------------------------------------------------------- 清单

def test_asic_defaults(tmp_path):
    pk = put(tmp_path, "to-x", asic={"mhz": 50})
    a = pk.asic()
    assert a["top"] == "to_x" and a["frame"] == "mpc" and a["flow"] == "syn_sta"
    assert put(tmp_path, "y").asic() is None


@pytest.mark.parametrize("bad", [
    {"mhz": 50, "frame": "caravel"},
    {"mhz": 50, "flow": "gds"},
    {"mhz": 50, "util": 0},
    {"mhz": 50, "util": 1.5},
    {"mhz": 50, "util": True},
    {"frame": "mpc"},
    {"mhz": True},
    {"mhz": 0},
    {"mhz": 50, "pins": []},
    {"mhz": 50, "top": "9x"},
    {"mhz": 50, "pads": [{"port": "a", "in": "b"}]},
    {"mhz": 50, "pads": [{"port": "a", "at": -1}]},
    {"mhz": 50, "pads": [{"port": "a", "side": 1}]},
    {"mhz": 50, "frame": "none", "pads": ["a"]},
    {"mhz": 50, "clock": "clk"},
    {"mhz": 50, "core": "nosuch"},
    {"mhz": 50, "core": 3},
])
def test_asic_refuses(tmp_path, bad):
    with pytest.raises(Bad):
        put(tmp_path, "t", asic=bad)


def test_core_must_be_a_dep(tmp_path):
    assert put(tmp_path, "t", deps={"x": "^0.1"}, asic={"mhz": 50, "core": "x"}).asic()["core"] == "x"


def test_flow_tables_agree():
    # core 不能 import back，同一张表两处各写一份，由这里守住
    assert tuple(manifest.ASIC_FLOWS) == tuple(back.FLOWS)


# ---------------------------------------------------------------- 排位

P = back.Port
PORTS = [P("clk", "input", 1), P("rst_n", "input", 1), P("a", "input", 2),
         P("b", "output", 1), P("c_i", "input", 3), P("c_o", "output", 3),
         P("c_oe", "output", 3), P("d_i", "input", 1), P("d_pull", "output", 1),
         P("e", "input", 1), P("f", "output", 4)]
SPEC = {"pads": ["a", {"in": "c_i", "out": "c_o", "oe": "c_oe"},
                 {"in": "d_i", "oe": "d_pull"}, {"port": "b", "at": 10}],
        "tie": {"e": 1}, "unused": ["f"]}


def pl(spec=SPEC, ports=PORTS):
    return frame.plan(spec, ports, "clk", "rst_n", True)


def test_plan_places_in_order():
    got = {r["bit"]: r for r in frame.table(pl())}
    assert got[0] == {"bit": 0, "kind": "in", "in": "a[0]", "out": None, "oe": None}
    assert got[1]["in"] == "a[1]"
    assert [got[b]["kind"] for b in (2, 3, 4)] == ["io"] * 3
    assert got[3] == {"bit": 3, "kind": "io", "in": "c_i[1]", "out": "c_o[1]", "oe": "c_oe[1]"}
    assert got[5] == {"bit": 5, "kind": "od", "in": "d_i[0]", "out": None, "oe": "d_pull[0]"}
    assert got[10]["kind"] == "out" and got[10]["out"] == "b[0]"
    assert sorted(got) == [0, 1, 2, 3, 4, 5, 10]


def test_glob_takes_declaration_order():
    ports = [P("clk", "input", 1), P("rst_n", "input", 1),
             P("x_1", "input", 1), P("x_0", "input", 2)]
    got = frame.plan({"pads": ["x_*"]}, ports, "clk", "rst_n", True)
    assert [s.i for s in got.slots] == [("x_1", 0), ("x_0", 0), ("x_0", 1)]


@pytest.mark.parametrize("code,edit", [
    ("XR-ASIC-002", lambda s: {**s, "pads": s["pads"][:-1] + [{"port": "b", "at": 1}]}),
    ("XR-ASIC-002", lambda s: {**s, "pads": s["pads"][:-1] + [{"port": "b", "at": 66}]}),
    ("XR-ASIC-003", lambda s: {**s, "unused": []}),
    ("XR-ASIC-004", lambda s: {**s, "pads": s["pads"] + ["nosuch*"]}),
    ("XR-ASIC-004", lambda s: {**s, "tie": {"e": 1, "f": 0}, "unused": []}),
    ("XR-ASIC-004", lambda s: {**s, "pads": s["pads"] + ["e"]}),
    ("XR-ASIC-004", lambda s: {**s, "tie": {"e": 2}}),
    ("XR-ASIC-004", lambda s: {**s, "pads": [s["pads"][0],
                                             {"in": "c_i", "out": "b", "oe": "c_oe"}]}),
    ("XR-ASIC-004", lambda s: {**s, "pads": [s["pads"][0], {"in": "c_i", "out": "c_o"}]}),
])
def test_plan_refuses(code, edit):
    with pytest.raises(Bad, match=code):
        pl(edit(SPEC))


def test_payload_is_66_bits():
    ports = [P("clk", "input", 1), P("rst_n", "input", 1), P("w", "input", 67)]
    with pytest.raises(Bad, match="XR-ASIC-002"):
        frame.plan({"pads": ["w"]}, ports, "clk", "rst_n", True)


CORE_V = """module core(clk, rst_n, a, b, c_i, c_o, c_oe, d_i, d_pull, e, f);
  input clk; input rst_n; input [1:0] a; output b; input [2:0] c_i;
  output [2:0] c_o; output [2:0] c_oe; input d_i; output d_pull; input e; output [3:0] f;
  assign b = a[0] ^ a[1];
  assign c_o = {a, e};
  assign c_oe = 3'b101;
  assign d_pull = a[1];
  assign f = {c_i, d_i};
endmodule
"""

TB_V = """module tb;
  reg clock = 0, reset = 1;
  reg [65:0] io_in = 0;
  wire [65:0] io_out, io_oe;
  chip dut(.clock(clock), .reset(reset), .io_in(io_in), .io_out(io_out), .io_oe(io_oe));
  initial begin
    io_in[1:0] = 2'b10;
    #1;
    if (io_oe !== 66'h0) begin $display("FAIL oe in reset %h", io_oe); $finish; end
    reset = 0;
    #1;
    if (io_oe !== 66'h434) begin $display("FAIL oe %h", io_oe); $finish; end
    if (io_out !== 66'h414) begin $display("FAIL out %h", io_out); $finish; end
    io_in[1:0] = 2'b01;
    #1;
    if (io_oe[5] !== 1'b0 || io_out[10] !== 1'b1 || io_out[4:2] !== 3'b011)
      begin $display("FAIL second %h %h", io_out, io_oe); $finish; end
    $display("PASS");
    $finish;
  end
endmodule
"""


@pytest.mark.skipif(not shutil.which("iverilog"), reason="要 iverilog")
def test_frame_behaves(tmp_path):
    txt = frame.render(pl(), "chip", "core", PORTS) + CORE_V
    (tmp_path / "chip.v").write_text(txt)
    (tmp_path / "tb.v").write_text(TB_V)
    r = subprocess.run(["iverilog", "-o", str(tmp_path / "a"), str(tmp_path / "chip.v"),
                        str(tmp_path / "tb.v")], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    r = subprocess.run(["vvp", str(tmp_path / "a")], capture_output=True, text=True)
    assert "PASS" in r.stdout, r.stdout + txt


def test_frame_ties_and_reset():
    txt = frame.render(pl(), "chip", "core", PORTS)
    assert ".e(1'd1)" in txt and ".rst_n(~reset)" in txt and ".f()" in txt
    hi = frame.render(frame.plan(SPEC, PORTS, "clk", "rst_n", False), "chip", "core", PORTS)
    assert ".rst_n(reset)" in hi


# ---------------------------------------------------------------- 展平

def test_rename_prefixes_all_but_top():
    src = ("module \\$paramod\\F\\w=8 (a);\n  input a;\nendmodule\n"
           "module \\$paramod\\F\\w=16 (a);\n  input a;\nendmodule\n"
           "module leaf(a);\n  input a;\nendmodule\n"
           "module top(a);\n  input a;\n"
           "  \\$paramod\\F\\w=8  u0 (\n    .a(a)\n  );\n"
           "  \\$paramod\\F\\w=16  u1 (\n    .a(a)\n  );\n"
           "  leaf u2 (\n    .a(a)\n  );\nendmodule\n")
    out, names = back.rename(src, "top", "chip_", top_as="chip")
    mods = back.MODULE.findall(out)
    assert sorted(mods) == ["chip", "chip_F_0", "chip_F_1", "chip_leaf"]
    w8 = names["\\$paramod\\F\\w=8"]
    assert w8.startswith("chip_F_") and f"  {w8}  u0 (" in out
    assert "  chip_leaf u2 (" in out and names["top"] == "chip"
    # 同一份输入每次改出同样的名字
    assert back.rename(src, "top", "chip_", top_as="chip")[0] == out


def test_ports_parse_both_styles():
    txt = ("module m(clk, a, b);\n  input clk;\n  input  [1 : 0] a;\n"
           "  output reg [31:0] b;\nendmodule\n")
    assert back.ports(txt, "m") == [P("clk", "input", 1), P("a", "input", 2),
                                    P("b", "output", 32)]
    with pytest.raises(ToolError):
        back.ports("module m(x);\nendmodule\n", "m")


@pytest.mark.skipif(not shutil.which("yosys"), reason="要 yosys")
def test_flatten_is_self_contained(tmp_path):
    (tmp_path / "a.v").write_text(
        "module sub #(parameter W = 1) (input [W-1:0] i, output [W-1:0] o);"
        " assign o = ~i; endmodule\n"
        "module top(input [7:0] x, output [7:0] y, output z);\n"
        "  sub #(.W(8)) s8(.i(x), .o(y));\n  sub s1(.i(x[0]), .o(z));\nendmodule\n")
    v, top = back.flatten([tmp_path / "a.v"], "top", tmp_path / "out.v", "chip_",
                          top_as="chip")
    assert top == "chip"
    mods = back.check(v, "chip", "chip_")
    assert len(mods) == 3 and all(m == "chip" or m.startswith("chip_sub") for m in mods)
    assert "parameter" not in v.read_text()


# ---------------------------------------------------------------- ecc

def test_ecc_toml():
    t = back.ecc_toml("to-x", "to_x", "to_x.v", "clock", 50, "rtl2gds", "/pdk")
    assert 'clock_port = "clock"' in t and "frequency_mhz = 50.0" in t
    assert 'preset = "rtl2gds"' in t and 'rtl = ["to_x.v"]' in t
    # alpha.12 不认 [flow].run，工作区名改由命令行给
    assert "run =" not in t
    # harden 与 rcx 是 alpha.12 之前的预设名，现在的全流程叫 rtl2gds
    for old in ("harden", "rcx"):
        assert 'preset = "rtl2gds"' in back.ecc_toml("x", "x", "x.v", "clock", 50, old, "/pdk")
    with pytest.raises(ToolError):
        back.ecc_toml("x", "x", "x.v", "clock", 50, "gds", "/pdk")


def fake_run(out: pathlib.Path, wns: float, hold: float = 0.2, state="Success", base="default"):
    base = out / base
    (base / "home").mkdir(parents=True)
    (base / "home/flow.json").write_text(json.dumps({"steps": [
        {"name": "Synthesis", "tool": "yosys", "state": state, "runtime": "0:1:0"}]}))
    (base / "home/checklist.json").write_text(json.dumps(
        {"status": "ready", "summary": {"passed": 1, "blocked": 0}}))
    f = base / "Synthesis_yosys/feature"
    (f / "post_synthesis").mkdir(parents=True)
    (f / "Synthesis_stat.json").write_text(json.dumps({"modules": {
        "\\to_x": {"num_cells": 1234, "area": 5678.5, "sequential_area": 100.0}}}))
    (f / "post_synthesis/qor_summary.json").write_text(json.dumps({"summary": {
        "setup": {"wns": wns, "tns": min(wns, 0), "nvp": int(wns < 0), "frequency_mhz": 60},
        "hold": {"wns": hold, "tns": 0, "nvp": 0}}}))
    (f / "post_synthesis/power_summary.json").write_text(json.dumps(
        {"schema_version": 1, "dynamic_uw": 10.0, "leakage_uw": 1.0}))


def test_ecc_report(tmp_path):
    fake_run(tmp_path, 3.5)
    r = back.ecc_report(tmp_path, "to_x")
    assert r["ok"] and r["cells"] == 1234 and r["area_um2"] == 5678.5
    assert r["timing"]["setup"]["wns"] == 3.5 and r["timing"]["step"] == "Synthesis"
    assert r["power_uw"] == {"dynamic_uw": 10.0, "leakage_uw": 1.0}
    assert r["checklist"]["status"] == "ready"
    # alpha.12 之前的 ecc 把工作区放在 runs/ 下，照样读得到
    fake_run(tmp_path / "old", -1.0, base="runs/default")
    assert back.ecc_report(tmp_path / "old", "to_x")["timing"]["setup"]["wns"] == -1.0


def test_gate_blocks_negative_slack(tmp_path):
    fake_run(tmp_path / "r", -0.4, hold=-0.1)
    rep = back.ecc_report(tmp_path / "r", "to_x")
    pk = put(tmp_path, "t", asic={"mhz": 50})
    got = {c: b for c, _, b in gate(pk, rep)}
    assert got == {"XR-ASIC-006": True, "XR-ASIC-007": True}
    pk = put(tmp_path, "t", asic={"mhz": 50}, diagnostics={"XR-ASIC-006": "warn"})
    got = {c: b for c, _, b in gate(pk, rep)}
    assert got == {"XR-ASIC-006": False, "XR-ASIC-007": True}


def test_gate_unproven_lec_is_not_a_failed_run(tmp_path):
    fake_run(tmp_path / "r", 1.0)
    base = tmp_path / "r" / "default"
    flow = json.loads((base / "home/flow.json").read_text())
    flow["steps"] += [{"name": "lec", "tool": "yosys_lec", "state": "Incomplete", "runtime": "0:4:8"},
                      {"name": "Floorplan", "tool": "ecc", "state": "Success", "runtime": "0:0:7"}]
    (base / "home/flow.json").write_text(json.dumps(flow))
    (base / "lec_yosys_lec/report").mkdir(parents=True)
    (base / "lec_yosys_lec/report/equiv_status.rpt").write_text(
        "Found 132 $equiv cells in equiv:\n  Of those cells 67 are proven and 65 are unproven.\n")
    rep = back.ecc_report(tmp_path / "r", "to_x")
    assert rep["ok"] and rep["lec"] == [{"step": "lec", "proven": 67, "unproven": 65}]
    pk = put(tmp_path, "t", asic={"mhz": 50})
    assert [(c, b) for c, _, b in gate(pk, rep)] == [("XR-ASIC-008", False)]
    pk = put(tmp_path, "t", asic={"mhz": 50}, diagnostics={"XR-ASIC-008": "error"})
    assert [(c, b) for c, _, b in gate(pk, rep)] == [("XR-ASIC-008", True)]
    # 没留下统计的比对步骤停了，就是普通的没跑通
    (base / "lec_yosys_lec/report/equiv_status.rpt").unlink()
    rep = back.ecc_report(tmp_path / "r", "to_x")
    assert not rep["ok"] and [c for c, _, b in gate(pk, rep) if b] == ["XR-ASIC-005"]
    # 停在比对上、后面还没跑：接着跑的那一步是它后面的那个
    flow["steps"][-1]["state"] = "Unstart"
    (base / "home/flow.json").write_text(json.dumps(flow))
    assert back._after_unproven(tmp_path / "r") is None
    (base / "lec_yosys_lec/report/equiv_status.rpt").write_text(
        "  Of those cells 67 are proven and 65 are unproven.\n")
    assert back._after_unproven(tmp_path / "r") == "Floorplan"


def test_gate_failed_step(tmp_path):
    fake_run(tmp_path / "r", 1.0, state="Failed")
    rep = back.ecc_report(tmp_path / "r", "to_x")
    pk = put(tmp_path, "t", asic={"mhz": 50})
    assert [c for c, _, b in gate(pk, rep) if b] == ["XR-ASIC-005"]


# ---------------------------------------------------------------- 来源

def test_web_url():
    assert prov.web("git@github.com:Tape-Out/eswitch.git") == "https://github.com/Tape-Out/eswitch"
    assert prov.web("https://github.com/Tape-Out/eswitch.git") == "https://github.com/Tape-Out/eswitch"
    assert prov.web(None) is None


@pytest.mark.skipif(not shutil.which("git"), reason="要 git")
def test_repo_ref_and_dirty(tmp_path):
    def g(*a):
        subprocess.run(["git", "-C", str(tmp_path), *a], check=True, capture_output=True)
    g("init", "-q")
    g("remote", "add", "origin", "git@github.com:o/r.git")
    (tmp_path / "f").write_text("1")
    g("add", "f")
    g("-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false",
      "commit", "-qm", "x")
    head = subprocess.run(["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout.strip()
    r = prov.repo(tmp_path)
    assert r == {"repo": "https://github.com/o/r", "ref": head, "dirty": False}
    (tmp_path / "f").write_text("2")
    assert prov.repo(tmp_path)["dirty"] is True
    assert prov.repo(tmp_path.parent)["ref"] is None


# ---------------------------------------------------------------- bus: none

def tree(root, bus, managers=True):
    put(root, "mgr", contract={"version": 1, "ctrl": {"shape": "none", "aw": 8, "dw": 32}},
        emit=[{"kind": "bsv", "package": "Mgr", "module": "mkMgr", "config_type": "MgrCfg",
               "interface": "MgrIfc",
               "pins": [{"name": "m", "type": "RegManager", "targs": [32, 32]},
                        {"name": "pins", "type": "MgrPins"}]}])
    put(root, "dev", emit=[{"kind": "bsv", "package": "Dev", "module": "mkDev",
                            "config_type": "DevCfg", "interface": "DevIfc", "ctrl": "regs"}])
    insts = [{"name": "d0", "of": "dev", "addr": 0x100}]
    if managers:
        insts.insert(0, {"name": "m0", "of": "mgr"})
    put(root, "soc", bus=bus, instances=insts,
        contract={"version": 1, "ctrl": {"shape": "flat", "aw": 32, "dw": 32}})
    res = resolve("soc", [root])
    return res, {n: Pkg(root / n) for n in ("mgr", "dev", "soc")}


def test_bus_none_has_no_external_port(tmp_path):
    res, pkgs = tree(tmp_path, "none")
    txt = assemble(res, pkgs, "Soc")
    assert "Apb4" not in txt and "interface bus" not in txt
    assert "m0.m.valid" in txt and "extbus" not in txt
    res, pkgs = tree(tmp_path / "b", "apb4")
    txt = assemble(res, pkgs, "Soc")
    assert "extbus.mgr" in txt and "interface bus = extbus.pins;" in txt


def test_bus_none_needs_a_manager(tmp_path):
    res, pkgs = tree(tmp_path, "none", managers=False)
    with pytest.raises(Bad, match="bus: none"):
        assemble(res, pkgs, "Soc")


# ---------------------------------------------------------------- 整片测试任务

def test_task_sees_xirang(tmp_path):
    from xirang_flow import tasks
    pk = put(tmp_path, "t", tasks={"env": 'sh -c "test -n \\"$XIRANG\\""'})
    assert tasks.run(pk, "env", {}, tmp_path / "o")[0][1].startswith("过了")


def test_xirang_in_a_task_keeps_the_search_path(tmp_path):
    """CI 里依赖克隆在别处：任务回调时要是按仓的上一级去找，就找不到它们。"""
    import os
    from xirang.cli import main
    ws, out = tmp_path / "ws", tmp_path / "o"
    ws.mkdir()
    put(ws, "t", tasks={"env": f'sh -c "echo $XIRANG > {tmp_path}/got"'})
    assert "XIRANG" not in os.environ
    assert main(["-p", str(ws), "run", "t", "env", "-o", str(out)]) == 0
    assert f"-p {ws.resolve()}" in (tmp_path / "got").read_text()
    assert "XIRANG" not in os.environ, "只在这一次调用里生效，不留给同一进程里的下一次"


def test_assembly_runs_task_tests(tmp_path):
    from xirang_flow.gate import _task_tests
    pk = put(tmp_path, "soc", tasks={"ok": "true", "no": "false"},
             test={"upstream": [{"name": "a", "task": "ok"}, {"name": "b", "task": "no"},
                                {"name": "c", "task": "no", "when": {"x.n": 2}}]})
    did = []
    bad = _task_tests(pk, tmp_path / "o", "Default", {"x.n": 1}, did)
    assert did == ["a", "b"] and len(bad) == 1 and bad[0].startswith("b：")


def test_refuses_foreign_out_dir(tmp_path):
    from xirang_flow import asic
    (tmp_path / "o").mkdir()
    (tmp_path / "o" / "keep.txt").write_text("mine")
    pk = put(tmp_path, "t", asic={"mhz": 50})
    with pytest.raises(Bad, match="不动它"):
        asic.run(pk, None, {}, tmp_path / "o")
    assert (tmp_path / "o" / "keep.txt").is_file()


def test_readmem_files_follow_their_source(tmp_path):
    from xirang_back.verilog import readmem
    (tmp_path / "src/rom").mkdir(parents=True)
    (tmp_path / "src/rom/rom.v").write_text('module rom; initial $readmemb("font.bin", m); endmodule\n')
    (tmp_path / "src/rom/font.bin").write_text("0101\n")
    (tmp_path / "work").mkdir()
    got = readmem([tmp_path / "src/rom/rom.v"], [], tmp_path / "work")
    assert got == [tmp_path / "work/font.bin"] and got[0].read_text() == "0101\n"
    (tmp_path / "src/rom/font.bin").unlink()
    (tmp_path / "work/font.bin").unlink()
    with pytest.raises(ToolError, match="font.bin"):
        readmem([tmp_path / "src/rom/rom.v"], [], tmp_path / "work")


def test_util_reaches_ecc():
    from xirang_back import asic as back
    plain = back.ecc_toml("x", "x", "x.v", "clock", 50, "rtl2gds", "/pdk")
    assert "core_util" not in plain
    s = back.ecc_toml("x", "x", "x.v", "clock", 50, "rtl2gds", "/pdk", 0.25)
    assert s.startswith(plain) and "[params.floorplan]\ncore_util = 0.25\n" in s
