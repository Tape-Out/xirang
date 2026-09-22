"""`tasks:`：清单里写命令，但只许消费已解出的配置。

每条规矩配一个只有它拦得住的反例：成环、指向不存在的目标、认不得的键、
认不得的占位符、跑挂了。还有一条是**没有回写通道**——任务的输出不进配置。

`uv run pytest tests/test_tasks.py`
"""
import pathlib

import pytest
import yaml

from xirang_core.manifest import Bad, Pkg
from xirang_flow import tasks


def mk(root: pathlib.Path, ts) -> Pkg:
    ip = {"name": "u", "version": "0.1.0", "spec": "0.1", "kind": "library",
          "identity": {"slug": "u", "display_name": "u", "summary": "u",
                       "category": "bus", "ip_family": "u",
                       "maturity": "planned"},
          "tasks": ts}
    root.mkdir(parents=True, exist_ok=True)
    (root / "ip.yaml").write_text(yaml.safe_dump(ip, allow_unicode=True),
                                  encoding="utf-8")
    return Pkg(root)


def test_a_bare_string_is_a_command(tmp_path):
    pk = mk(tmp_path, {"wave": "gtkwave dump.vcd"})
    assert tasks.all_of(pk)["wave"]["run"] == "gtkwave dump.vcd"


def test_needs_may_not_form_a_cycle(tmp_path):
    pk = mk(tmp_path, {"a": {"run": "true", "needs": ["b"]},
                       "b": {"run": "true", "needs": ["a"]}})
    with pytest.raises(Bad, match="XR-TASK-002"):
        tasks.all_of(pk)


def test_needs_must_point_somewhere_real(tmp_path):
    pk = mk(tmp_path, {"a": {"run": "true", "needs": ["nope"]}})
    with pytest.raises(Bad, match="XR-TASK-003"):
        tasks.all_of(pk)


def test_needs_may_point_at_a_builtin_stage(tmp_path):
    pk = mk(tmp_path, {"bench": {"run": "true", "needs": ["build"]}})
    assert tasks.plan(pk, "bench") == ["build", "bench"]


def test_an_unknown_key_is_refused(tmp_path):
    pk = mk(tmp_path, {"a": {"run": "true", "after": ["b"]}})
    with pytest.raises(Bad, match="XR-TASK-003"):
        tasks.all_of(pk)


def test_a_task_may_not_be_named_after_a_stage(tmp_path):
    pk = mk(tmp_path, {"build": "true"})
    with pytest.raises(Bad, match="XR-TASK-003"):
        tasks.all_of(pk)


def test_asking_for_a_task_that_is_not_there(tmp_path):
    pk = mk(tmp_path, {"wave": "true"})
    with pytest.raises(Bad, match="XR-TASK-001"):
        tasks.plan(pk, "waveform")


def test_the_plan_is_depth_first_and_lists_each_step_once(tmp_path):
    pk = mk(tmp_path, {"a": "true",
                       "b": {"run": "true", "needs": ["a"]},
                       "c": {"run": "true", "needs": ["a", "b"]}})
    assert tasks.plan(pk, "c") == ["a", "b", "c"]


def test_placeholders_are_read_only_and_known(tmp_path):
    pk = mk(tmp_path, {"a": "echo {{name}} {{knob.w}} {{out}}"})

    class V:
        value = 8

    out = tmp_path / "build"
    hole = tasks.holes(pk, {"w": V()}, out)
    got = tasks.fill(tasks.all_of(pk)["a"]["run"], hole, "a")
    assert got == f"echo u 8 {out}"


def test_an_unknown_placeholder_is_refused(tmp_path):
    pk = mk(tmp_path, {"a": "echo {{nope}}"})
    with pytest.raises(Bad, match="XR-TASK-003"):
        tasks.fill(tasks.all_of(pk)["a"]["run"], tasks.holes(pk, {}, tmp_path), "a")


def test_a_failing_task_is_reported_with_its_exit_code(tmp_path):
    pk = mk(tmp_path, {"a": "false"})
    with pytest.raises(Bad, match="XR-TASK-004"):
        tasks.run(pk, "a", {}, tmp_path)


def test_a_missing_program_is_named(tmp_path):
    pk = mk(tmp_path, {"a": "definitely-not-a-program-xyz"})
    with pytest.raises(Bad, match="XR-TASK-004"):
        tasks.run(pk, "a", {}, tmp_path)


def test_plan_mode_runs_nothing(tmp_path):
    mark = tmp_path / "touched"
    pk = mk(tmp_path, {"a": f"touch {mark}"})
    got = tasks.run(pk, "a", {}, tmp_path, dry=True)
    assert not mark.exists(), "--plan 不该真的动手"
    assert got[0][0] == "a" and str(mark) in got[0][1]


def test_a_task_really_runs_and_cannot_write_back(tmp_path):
    pk = mk(tmp_path, {"a": "echo name=hijacked"})
    got = tasks.run(pk, "a", {}, tmp_path)
    assert got == [("a", "过了（1 行输出）")]
    assert Pkg(pk.root).name == "u", "任务的输出不许回流进配置"


def test_tools_are_listed_for_doctor(tmp_path):
    pk = mk(tmp_path, {"a": "vivado -mode batch", "b": "make -C swsrc bench"})
    assert tasks.tools(pk) == ["make", "vivado"]
