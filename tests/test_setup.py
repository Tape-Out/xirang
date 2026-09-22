"""生成器类的上游：RTL 要先跑一遍对方的脚本才存在。

乘影是 Chisel，`./mill ventus.run` 出 `GPGPU_top.v`；Vortex 的两份配置头由它自己的
`gen_config.py` 生成。这类包的 `rtl:` 列的是**还不存在的文件**——报「树上没有」
等于把人晾在那里，要说清先跑哪条任务。

**但不代跑**：任务不是隐藏的构建步骤，这条规矩在 tasks 那边立过。

`uv run pytest tests/test_setup.py`
"""
import pathlib

import pytest
import yaml

from xirang_core.manifest import Bad, Pkg


def mk(root: pathlib.Path, *, setup=None, tasks=None, make_it=False) -> Pkg:
    (root / "hwsrc").mkdir(parents=True, exist_ok=True)
    if make_it:
        (root / "hwsrc/gen.v").write_text("module top; endmodule", encoding="utf-8")
    e = {"kind": "foreign", "lang": "verilog", "top": "top",
         "rtl": ["hwsrc/gen.v"],
         "clock": {"port": "clk"},
         "reset": {"port": "rst_n", "active": "low", "sync": True},
         "ports": [{"endpoint": "pins", "kind": "physical",
                    "type": "Pins", "map": {"a": "a"}}]}
    if setup:
        e["setup"] = setup
    ip = {"name": "vx", "version": "0.1.0", "spec": "0.1", "kind": "ip",
          "lang": "verilog",
          "identity": {"slug": "v", "display_name": "v", "summary": "v",
                       "category": "processor", "ip_family": "v",
                       "maturity": "planned"},
          "contract": {"version": 1, "ctrl": {"shape": "none", "aw": 32,
                                              "dw": 32}},
          "emit": [e]}
    if tasks:
        ip["tasks"] = tasks
    (root / "ip.yaml").write_text(yaml.safe_dump(ip, allow_unicode=True),
                                  encoding="utf-8")
    return Pkg(root)


def test_a_missing_generated_file_points_at_the_task(tmp_path):
    """清单一读就报——不必等到构建那一步才发现 RTL 还没生成。"""
    with pytest.raises(Bad) as e:
        mk(tmp_path, setup="verilog", tasks={"verilog": "./mill ventus.run"})
    assert "ran run vx verilog" in str(e.value)


def test_without_setup_the_message_is_the_plain_one(tmp_path):
    with pytest.raises(Bad) as e:
        mk(tmp_path)
    assert "树上没有" in str(e.value)
    assert "ran run" not in str(e.value), "没写 setup 就别瞎指"


def test_setup_must_name_a_real_task(tmp_path):
    with pytest.raises(Bad, match="tasks 里没有这条"):
        mk(tmp_path, setup="nope", tasks={"verilog": "true"}, make_it=True)


def test_once_the_file_is_there_nothing_complains(tmp_path):
    pk = mk(tmp_path, setup="verilog", tasks={"verilog": "true"}, make_it=True)
    assert pk.foreign_emit()["top"] == "top"
