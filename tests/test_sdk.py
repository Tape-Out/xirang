"""寿木 SDK 要的三种导出：SVD、设备树、整颗芯片的 C 头。

判据照 `.rdl` 那套，参考实现认不认：SVD 要被 XML 解析器接受、字段与寄存器图一致；
`.dts` 装了 dtc 就要编得过。存储节点报的是实际装了多少，不是译码窗口。

`uv run pytest tests/test_sdk.py`
"""
import pathlib
import shutil
import subprocess
import xml.etree.ElementTree as ET
from types import SimpleNamespace

import pytest

from xirang_core.model import Candidate, Instance, Resolved, Value
from xirang_out import dts, svd
from xirang_out.target import Ctx

RNG = """ip: rng
contract: { aw: 8, dw: 32 }
base: 0x0
size: 0x100
regs:
  - name: ctrl
    offset: 0x00
    desc: enable
    fields:
      - { name: en,  bits: "0:0", sw: rw, hw: r, reset: 1 }
      - { name: ien, bits: "1:1", sw: rw, hw: r, reset: 0 }
  - name: data
    offset: 0x08
    desc: random word
    fields: [ { name: val, width: 32, sw: r, hw: w, volatile: true } ]
"""


def val(v):
    return Value(name="k", value=v, winner=Candidate("ip-default", v, "x:1"))


def chip(tmp_path: pathlib.Path) -> Ctx:
    (tmp_path / "rng").mkdir()
    (tmp_path / "rng/regmap.yaml").write_text(RNG, encoding="utf-8")
    ctrl = {"contract": {"ctrl": {"shape": "flat", "aw": 8, "dw": 32}}}
    pkgs = {
        "soc": SimpleNamespace(ip={"version": "0.1.0", "identity": {"summary": "a chip"}},
                               root=tmp_path, regmap=None),
        "rvcore": SimpleNamespace(root=tmp_path, regmap=None, ip={
            "contract": {"ctrl": {"shape": "none"}},
            "dt": {"node": "cpu", "compatible": ["riscv"], "props": [
                {"name": "riscv,isa-base", "value": "rv32i"},
                {"name": "mmu-type", "value": "riscv,sv32", "when": {"mmu": True}},
                {"name": "mmu-type", "value": "riscv,none", "when": {"mmu": False}}]}}),
        "sram": SimpleNamespace(root=tmp_path, regmap=None, ip={
            "contract": {"ctrl": {"shape": "flat", "aw": 16, "dw": 32}},
            "dt": {"node": "memory", "size": {"knob": "words", "scale": 4}}}),
        "rng": SimpleNamespace(root=tmp_path / "rng", regmap={"ip": "rng"},
                               ip={**ctrl, "dt": {"node": "rng"}}),
    }
    res = Resolved(top="soc", bus="apb4", instances=[
        Instance(name="cpu", of="rvcore", values={"mmu": val(False)}),
        Instance(name="mem", of="sram", values={"words": val(256)}, addr=0x80000000, size=0x10000),
        Instance(name="rng0", of="rng", values={}, addr=0x10013000, size=0x100),
    ])
    return Ctx(res=res, pkgs=pkgs)


def test_svd_lists_the_mapped_peripherals_with_their_fields(tmp_path):
    root = ET.fromstring(svd.to_svd(chip(tmp_path)))
    ps = root.findall("./peripherals/peripheral")
    assert [p.findtext("name") for p in ps] == ["RNG0"], "核与存储不是寄存器外设"
    assert ps[0].findtext("baseAddress") == "0x10013000"
    regs = {r.findtext("name"): r for r in ps[0].findall("./registers/register")}
    assert set(regs) == {"CTRL", "DATA"}
    assert regs["CTRL"].findtext("resetValue") == "0x1"
    assert regs["DATA"].findtext("access") == "read-only"
    f = {x.findtext("name"): x for x in regs["CTRL"].findall("./fields/field")}
    assert f["IEN"].findtext("bitOffset") == "1" and f["IEN"].findtext("bitWidth") == "1"


def test_dts_reports_populated_memory_and_the_knob_dependent_props(tmp_path):
    text = dts.to_dts(chip(tmp_path))
    assert "memory@80000000" in text and "reg = <0x80000000 0x400>;" in text, \
        "256 字 × 4 = 1 KiB，不是 64 KiB 的译码窗口"
    assert 'mmu-type = "riscv,none";' in text and "riscv,sv32" not in text
    assert 'compatible = "tape-out,rng";' in text and "rng@10013000" in text
    assert "cpu_intc: interrupt-controller" in text
    if dtc := shutil.which("dtc"):
        r = subprocess.run([dtc, "-I", "dts", "-O", "dtb", "-o", "/dev/null", "-"],
                           input=text, capture_output=True, text=True)
        assert r.returncode == 0, r.stderr


def test_header_gives_each_instance_its_base(tmp_path):
    text = dts.to_header(chip(tmp_path))
    assert '#include "rng.h"' in text
    assert "#define RNG0_BASE 0x10013000u" in text
    assert "#define MEM_SIZE 0x400u" in text
    assert "CPU_BASE" not in text, "没有总线地址的实例不出基址"


def test_a_bad_dt_block_is_refused(tmp_path):
    import yaml

    from xirang_core.manifest import Bad, Pkg
    ip = {"name": "x", "version": "0.1.0", "spec": "0.1", "kind": "ip", "lang": "bsv",
          "identity": {"slug": "x", "display_name": "x", "summary": "x", "category": "peripheral",
                       "ip_family": "x", "maturity": "planned"},
          "contract": {"version": 1, "ctrl": {"shape": "none", "aw": 8, "dw": 32}},
          "dt": {"node": "x", "irq": 3}}
    (tmp_path / "ip.yaml").write_text(yaml.safe_dump(ip), encoding="utf-8")
    with pytest.raises(Bad, match="dt 只认"):
        Pkg(tmp_path)
