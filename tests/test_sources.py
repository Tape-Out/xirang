"""黑盒源码的五种写法：文件、带 when 的文件、glob、regex、Flist。

`uv run pytest tests/test_sources.py`
"""
import pathlib

import pytest

from xirang_core.manifest import Bad
from xirang_core.sources import expand


def tree(root: pathlib.Path, files: dict):
    for p, text in files.items():
        f = root / p
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)


@pytest.fixture
def pkg(tmp_path):
    tree(tmp_path, {
        "hw/rtl/core/a.sv": "", "hw/rtl/core/b.sv": "", "hw/rtl/cache/c.sv": "",
        "hw/rtl/afu/x.sv": "", "hw/rtl/core/note.md": "",
    })
    return tmp_path


def rel(src, root):
    return [f.relative_to(root).as_posix() for f in src.files]


def test_glob_exclude(pkg):
    s = expand(pkg, [{"glob": "hw/rtl/**/*.sv", "exclude": ["hw/rtl/afu/**"]}])
    assert rel(s, pkg) == ["hw/rtl/cache/c.sv", "hw/rtl/core/a.sv", "hw/rtl/core/b.sv"]


def test_glob_none(pkg):
    with pytest.raises(Bad, match="XR-SRC-001"):
        expand(pkg, [{"glob": "hw/**/*.vhd"}])


def test_regex(pkg):
    s = expand(pkg, [{"regex": r"^hw/rtl/core/.*\.sv$"}])
    assert rel(s, pkg) == ["hw/rtl/core/a.sv", "hw/rtl/core/b.sv"]
    with pytest.raises(Bad, match="XR-SRC-001"):
        expand(pkg, [{"regex": r"\.vhd$"}])


def test_when_and_dedup(pkg):
    ents = ["hw/rtl/core/a.sv", {"path": "hw/rtl/cache/c.sv", "when": {"l2": True}},
            {"glob": "hw/rtl/core/*.sv"}]
    s = expand(pkg, ents, knobs={"l2": False})
    assert rel(s, pkg) == ["hw/rtl/core/a.sv", "hw/rtl/core/b.sv"]
    assert s.origin[pkg / "hw/rtl/core/a.sv"] == "hw/rtl/core/a.sv"
    assert len(expand(pkg, ents, knobs=None).files) == 3


def test_flist(pkg):
    tree(pkg, {
        "lists/top.f": "// 注释\n+incdir+inc\n+define+A=1+B\n-F lists/sub/part.f\n"
                       "-f lists/root.f\n-y ${LIB}\n+libext+.v+.sv\n-sv\nhw/rtl/core/a.sv\n",
        "lists/sub/part.f": "local.sv\n# 也是注释\n",
        "lists/sub/local.sv": "",
        "lists/root.f": "hw/rtl/cache/c.sv\n",
    })
    s = expand(pkg, [{"flist": "lists/top.f", "env": {"LIB": "vendor/libs"}}])
    assert rel(s, pkg) == ["lists/sub/local.sv", "hw/rtl/cache/c.sv", "hw/rtl/core/a.sv"]
    assert s.defines == ["A=1", "B"]
    assert s.incdirs == [pkg / "inc"]
    assert s.libdirs == [pkg / "vendor/libs"]
    assert s.libext == [".v", ".sv"]
    assert s.ignored == ["top.f: -sv"]


def test_flist_env_missing(pkg):
    tree(pkg, {"a.f": "${ROOT}/x.sv\n"})
    with pytest.raises(Bad, match="XR-SRC-002"):
        expand(pkg, [{"flist": "a.f"}])


def test_flist_cycle(pkg):
    tree(pkg, {"a.f": "-f b.f\n", "b.f": "-f a.f\n"})
    with pytest.raises(Bad, match="成环"):
        expand(pkg, [{"flist": "a.f"}])


@pytest.mark.parametrize("bad", [
    {"path": "a.sv", "glob": "*.sv"},
    {"glob": "*.sv", "env": {}},
    {"when": {"x": 1}},
])
def test_entry_shape(pkg, bad):
    with pytest.raises(Bad):
        expand(pkg, [bad])
