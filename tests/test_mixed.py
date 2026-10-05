"""混着 .sv 与 .v 的上游：只有 .sv 过 sv2v，.v 直接给 yosys。

sv2v 把非 ANSI 端口上带初值的寄存器翻成一条恒为初值的连续赋值，那个寄存器从此不动，而且哪儿都不报错：
TinyQV 的 MLP 引擎里 `resp__valid` 就是这样写的，交付的文件上读结果永远等不到应答。
"""
import re
import shutil

import pytest
from xirang_back.verilog import elaborate, script

TOP = """module top (input logic clk, input logic d, output logic q);
  leaf u (.clk(clk), .d(d), .q(q));
endmodule
"""
LEAF = """module leaf (clk, d, q);
  input clk;
  input d;
  output q;
  reg q = 1'h0;
  function same;
    input x;
    same = x;
  endfunction
  wire n = same(d);
  always @(posedge clk) q <= n;
endmodule
"""


def test_only_sv_is_marked_for_sv_mode():
    sc = script(["a.sv2v.v", "_src/b.v"], "top", {}, "o.v", sv=("_src/b.v",))
    assert "read_verilog a.sv2v.v" in sc and "read_verilog -sv _src/b.v" in sc


@pytest.mark.skipif(not (shutil.which("yosys") and shutil.which("sv2v")), reason="要 yosys 与 sv2v")
def test_verilog_files_bypass_sv2v(tmp_path):
    root = tmp_path / "pkg"
    root.mkdir()
    (root / "top.sv").write_text(TOP)
    (root / "leaf.v").write_text(LEAF)
    out = elaborate([root / "top.sv", root / "leaf.v"], "top", {}, tmp_path / "w" / "o.v", root=root)
    text = out.read_text()
    assert "sv2v_tmp" not in text
    # 那个寄存器还是由时钟沿驱动，没有变成常量
    leaf = text[text.index("module leaf"):]
    assert re.search(r"always @\(posedge clk\)\s+q <= ", leaf) and "assign q = 1'h0" not in leaf
    # yosys 把源文件路径写进函数局部线的名字：经工作目录里的链接给，产物里才没有绝对路径
    assert "$func$" in text and str(tmp_path) not in text
