"""导出 SystemRDL 与 IP-XACT 的判据。

判据不是「出了文件」，是**参考实现认不认**：`.rdl` 要被 systemrdl-compiler
（MIT）编过并细化，`.xml` 要被 XML 解析器接受。语法自称对不算数——第一版
一次就撞出三类错：CSR 号当字节地址导致寄存器重叠、字段名撞上保留字 `wr`、
复位值 -1 没按位宽取模被读成 2^64-1。

每条反例要有只有它拦得住的一项：改窄一个字段就该报位宽装不下复位值，
把保留字的转义去掉就该解析失败。
"""
import pathlib
import sys
import tempfile

import pytest
import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]
                       / "packages/xirang-out/src"))

from xirang_out import rdl as rdlmod      # noqa: E402
from xirang_out.regview import Reg        # noqa: E402

systemrdl = pytest.importorskip("systemrdl")


def _compile(text: str) -> None:
    from systemrdl import RDLCompiler
    with tempfile.TemporaryDirectory() as d:
        f = pathlib.Path(d) / "x.rdl"
        f.write_text(text, encoding="utf-8")
        c = RDLCompiler()
        c.compile_file(str(f))
        c.elaborate()


def _one(fields) -> str:
    regs = [Reg(name="ctrl", offset=0, desc="a register", width=32, fields=fields)]
    return rdlmod.HEAD + "\naddrmap probe {\n" + \
        "\n".join(rdlmod._regs(regs)) + "\n};\n"


BASE = dict(sw="rw", hw="r", woclr=False, hwset=False, onread=None,
            swacc=False, swmod=False, legal=None, reset=0)


def test_plain_compiles():
    _compile(_one([{**BASE, "name": "enable", "lo": 0, "hi": 0, "w": 1}]))


def test_keyword_name_is_escaped():
    """`wr` 是 SystemRDL 的保留字。不转义就解析失败——i2c 撞的就是这个。"""
    txt = _one([{**BASE, "name": "wr", "lo": 4, "hi": 4, "w": 1}])
    assert "\\wr[4:4]" in txt
    _compile(txt)


def test_unescaped_keyword_would_fail():
    """反例：把转义拿掉，参考编译器必须拒。"""
    txt = _one([{**BASE, "name": "wr", "lo": 4, "hi": 4, "w": 1}]).replace("\\wr", "wr")
    with pytest.raises(Exception):
        _compile(txt)


def test_reset_is_masked_to_width():
    """-1 是「全一」的写法。不按位宽取模就会被读成 2^64-1——spi 撞的就是这个。"""
    from xirang_out.regview import read

    class P:
        ip = {"contract": {"ctrl": {"shape": "flat", "aw": 8, "dw": 32}}}

    with tempfile.TemporaryDirectory() as d:
        root = pathlib.Path(d)
        (root / "regmap.yaml").write_text(yaml.safe_dump({
            "ip": "probe", "contract": {"aw": 8, "dw": 32},
            "base": 0, "size": 0x100,
            "regs": [{"name": "ctrl", "offset": 0, "fields": [
                {"name": "val", "bits": "0:0", "sw": "rw", "hw": "r", "reset": -1}]}],
        }), encoding="utf-8")
        P.root = root
        regs, _ = read(P, {})
    assert regs[0].fields[0]["reset"] == 1


def test_shape_none_is_not_exported():
    """CSR 空间不是内存映射：hart 的偏移是 CSR 号，当地址会重叠。"""
    from xirang_out.regview import read

    class P:
        ip = {"contract": {"ctrl": {"shape": "none"}}}
        root = pathlib.Path("/nonexistent")

    regs, head = read(P, {})
    assert regs == [] and head.get("skip") == "shape-none"


def test_dollar_braces_are_not_placeholders():
    """GitHub Actions 的 ${{ }} 不是占位符——早先把它当占位符会让工作流全报错。"""
    from xirang.new import PH
    assert PH.search("${{ github.token }}") is None
    assert PH.search("{{ name }}") is not None
