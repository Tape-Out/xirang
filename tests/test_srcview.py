"""黑盒的三个视图：`rtl` 打底，`sim`、`syn` 叠上去。

`uv run pytest tests/test_srcview.py`
"""
import pathlib

import pytest
import yaml

from xirang_core.manifest import Bad, Pkg
from xirang_gen import foreign


def mk(root: pathlib.Path, **emit) -> Pkg:
    for f in ("hw/top.v", "hw/syn.v", "hw/sim.v"):
        (root / f).parent.mkdir(parents=True, exist_ok=True)
        (root / f).write_text("module top; endmodule", encoding="utf-8")
    ip = {"name": "vx", "version": "0.1.0", "spec": "0.1", "kind": "ip",
          "lang": "verilog",
          "identity": {"slug": "v", "display_name": "v", "summary": "v",
                       "category": "processor", "ip_family": "v",
                       "maturity": "planned"},
          "contract": {"version": 1, "ctrl": {"shape": "none", "aw": 32, "dw": 32}},
          "emit": [{"kind": "foreign", "lang": "verilog", "top": "top",
                    "rtl": ["hw/top.v"],
                    "ports": [{"endpoint": "pins", "kind": "physical",
                               "type": "Pins", "map": {"a": "a"}}], **emit}]}
    (root / "ip.yaml").write_text(yaml.safe_dump(ip, allow_unicode=True), encoding="utf-8")
    return Pkg(root)


def rel(pk, fs):
    return [f.relative_to(pk.root).as_posix() for f in fs]


def test_a_view_appends_and_its_macro_wins(tmp_path):
    pk = mk(tmp_path, defines=["A", "W=1"], includes=["hw"],
            views={"syn": {"defines": ["W=2", "SYN"], "rtl": ["hw/syn.v"]}})
    assert foreign.defines(pk, view="syn") == ["A", "W=2", "SYN"]
    assert foreign.defines(pk) == ["A", "W=1"]
    assert rel(pk, foreign.files(pk, "syn")) == ["hw/top.v", "hw/syn.v"]
    assert rel(pk, foreign.files(pk, "sim")) == ["hw/top.v"], "不写就同 rtl"


def test_replace_swaps_the_whole_list(tmp_path):
    pk = mk(tmp_path, includes=["hw"],
            views={"sim": {"rtl": ["hw/sim.v"], "replace": ["rtl"], "includes": ["dpi"]}})
    assert rel(pk, foreign.files(pk, "sim")) == ["hw/sim.v"]
    assert rel(pk, foreign.includes(pk, "sim")) == ["hw", "dpi"]


def test_the_old_sim_list_still_means_replace(tmp_path):
    pk = mk(tmp_path, sim=["hw/sim.v"])
    assert rel(pk, foreign.files(pk, "sim")) == ["hw/sim.v"]
    assert rel(pk, foreign.files(pk, "syn")) == ["hw/top.v"]


def test_a_made_up_view_name_is_refused(tmp_path):
    with pytest.raises(Bad, match="XR-VIEW-001"):
        mk(tmp_path, views={"fpga": {}}).foreign_emit()


def test_a_view_takes_only_its_four_keys(tmp_path):
    with pytest.raises(Bad, match="不认"):
        mk(tmp_path, views={"syn": {"top": "x"}}).foreign_emit()
    with pytest.raises(Bad, match="replace"):
        mk(tmp_path, views={"syn": {"replace": ["top"]}}).foreign_emit()


def test_glob_entries_pass_the_manifest(tmp_path):
    pk = mk(tmp_path, rtl=[{"glob": "hw/*.v", "exclude": ["hw/sim.v"]}])
    assert rel(pk, foreign.files(pk)) == ["hw/syn.v", "hw/top.v"]
