"""烘焙前抹掉仿真专用区段：上游明写 `translate_off` 的那些。

商用综合器认这对 pragma，sv2v 不认——它把注释连 pragma 一起去掉，于是仿真代码
原样流到 yosys 面前。CVA6 的指令追踪器两支都在里面：一支用 SystemVerilog 的类，
一支用 `string` 开文件，给不给宏都躲不开，因为问题不在宏。

行号必须不变：抹掉的行换成空行，报错才指得回源文件。

`uv run pytest tests/test_stripsim.py`
"""
import pathlib

from xirang_back.verilog import SIM_OFF, SIM_ON, strip_sim


def w(d: pathlib.Path, name: str, lines) -> pathlib.Path:
    p = d / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(chr(10).join(lines) + chr(10), encoding="utf-8")
    return p


def test_the_pragma_is_recognised_in_every_spelling():
    for x in ("// pragma translate_off", "//pragma translate_off",
              "  // synopsys translate_off", "// synthesis translate_off"):
        assert SIM_OFF.search(x), x
    assert SIM_ON.search("// pragma translate_on")
    assert not SIM_OFF.search("// pragma translate_offX"), "别把前缀当命中"


def test_the_region_goes_and_the_line_numbers_stay(tmp_path):
    src = [
        "module m;",
        "  // pragma translate_off",
        "  initial begin string fn; $fopen(fn); end",
        "  // pragma translate_on",
        "  assign y = a;",
        "endmodule",
    ]
    f = w(tmp_path / "in", "m.sv", src)
    got, cut = strip_sim([f], tmp_path / "work")
    assert cut == 1
    lines = got[0].read_text(encoding="utf-8").splitlines()
    assert len(lines) == len(src), "行数变了，报错的行号就指不回源文件"
    assert "string fn" not in chr(10).join(lines)
    assert lines[0] == "module m;" and lines[4] == "  assign y = a;"
    assert lines[1] == "" and lines[3] == "", "pragma 那两行自己也要抹掉"


def test_a_file_without_the_pragma_is_passed_through(tmp_path):
    f = w(tmp_path / "in", "n.sv", ["module n; endmodule"])
    got, cut = strip_sim([f], tmp_path / "work")
    assert cut == 0 and got == [f], "没有 pragma 就不该复制一份"


def test_two_files_with_the_same_name_do_not_collide(tmp_path):
    a = w(tmp_path / "x", "fifo_v3.sv", ["module a;", "  // pragma translate_off",
                                         "  bad_a", "  // pragma translate_on", "endmodule"])
    b = w(tmp_path / "y", "fifo_v3.sv", ["module b;", "  // pragma translate_off",
                                         "  bad_b", "  // pragma translate_on", "endmodule"])
    got, cut = strip_sim([a, b], tmp_path / "work")
    assert cut == 2 and len({str(p) for p in got}) == 2
    txt = [p.read_text(encoding="utf-8") for p in got]
    assert "module a;" in txt[0] and "module b;" in txt[1]
    assert "bad_a" not in txt[0] and "bad_b" not in txt[1]


def test_an_unterminated_region_runs_to_the_end(tmp_path):
    f = w(tmp_path / "in", "u.sv", ["module u;", "  // pragma translate_off",
                                    "  never_closed", "endmodule"])
    got, _ = strip_sim([f], tmp_path / "work")
    txt = got[0].read_text(encoding="utf-8")
    assert "never_closed" not in txt and "endmodule" not in txt


def test_preprocessor_directives_survive(tmp_path):
    """区段里常套着条件编译。连指令一起抹会把边界搞错——CVA6 那段抹完 sv2v 吐了 505 MB。"""
    src = [
        "module m;",
        "  // pragma translate_off",
        "`ifndef VERILATOR",
        "  int f; initial f = $fopen(\"x\");",
        "`else",
        "  initial begin string fn; end",
        "`endif",
        "  // pragma translate_on",
        "  assign y = a;",
        "endmodule",
    ]
    f = w(tmp_path / "in", "m.sv", src)
    got, cut = strip_sim([f], tmp_path / "work")
    lines = got[0].read_text(encoding="utf-8").splitlines()
    assert cut == 1 and len(lines) == len(src)
    kept = [x for x in lines if x.strip()]
    assert kept == ["module m;", "`ifndef VERILATOR", "`else", "`endif",
                    "  assign y = a;", "endmodule"], kept
