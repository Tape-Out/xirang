"""诊断闸门：号是稳定的，级别四处可覆盖，只有 error 挡住动作。

问题从来不是「某道检查该不该存在」，而是**严重级被硬写死了**。面积那一族写死成
挡路的，没有综合流程的人就跑不了行为仿真；两块积木拼出来的 USB 转串口没有价目表，
在自己的工具里反倒成不了一等公民。

`uv run python tests/test_diag.py`
"""
import pathlib
import re
import sys
import tempfile

import yaml

from xirang_core import diag
from xirang_core.manifest import Bad, Pkg

BASE = {"name": "t", "version": "0.1.0", "spec": "0.1", "kind": "ip", "lang": "bsv",
        "contract": {"version": 1, "ctrl": {"shape": "flat", "aw": 8, "dw": 32}}}


def mk(extra: dict, root: pathlib.Path) -> Pkg:
    (root / "hwsrc").mkdir(parents=True, exist_ok=True)
    (root / "hwsrc/T.bsv").write_text("package T; endpackage\n")
    (root / "ip.yaml").write_text(yaml.safe_dump({**BASE, **extra}, allow_unicode=True))
    return Pkg(root)


def main() -> int:
    bad: list[str] = []

    for code, c in diag.CHECKS.items():
        if not re.fullmatch(r"XR-[A-Z]+-\d{3}", code):
            bad.append(f"检查号不成体系：{code}")
        if not c.what:
            bad.append(f"{code} 没写拦下什么")

    # 面积一族默认不挡路，连线与转换一族默认挡
    for code in ("XR-AREA-001", "XR-AREA-003", "XR-AREA-006"):
        if diag.blocks(diag.CHECKS[code].level):
            bad.append(f"{code} 默认挡住了动作——没有综合流程的人就跑不了仿真")
    for code in ("XR-WIRE-002", "XR-CONV-001", "XR-CDC-001"):
        if not diag.blocks(diag.CHECKS[code].level):
            bad.append(f"{code} 默认没挡住")

    # 就近生效，且答得出从哪来
    lv, why = diag.resolve("XR-AREA-003", [])
    if (lv, why) != (diag.Level.info, "默认"):
        bad.append(f"没有覆盖时该报默认：{lv} {why}")
    lv, why = diag.resolve("XR-AREA-003", [("工作区", {"XR-AREA-003": "error"}),
                                           ("包", {"XR-AREA-003": "warn"})])
    if (lv, why) != (diag.Level.warn, "包"):
        bad.append(f"靠后的那层没赢：{lv} {why}")
    lv, why = diag.resolve("XR-AREA-003", [("工作区", {"XR-AREA-003": "error"}),
                                           ("包", {})])
    if (lv, why) != (diag.Level.error, "工作区"):
        bad.append(f"没提到这个号的层不该顶掉上一层：{lv} {why}")

    with tempfile.TemporaryDirectory() as d:
        root = pathlib.Path(d) / "t"
        mk({"diagnostics": {"XR-AREA-003": "info"}}, root)      # 合法的收下
        for what, extra in (("不认识的检查号", {"diagnostics": {"XR-NOPE-001": "info"}}),
                            ("不认识的级别", {"diagnostics": {"XR-AREA-003": "loud"}})):
            try:
                mk(extra, root)
                bad.append(f"{what}：没报错")
            except Bad:
                pass

    for line in bad:
        print(f"✘ {line}")
    if not bad:
        print(f"✔ {len(diag.CHECKS)} 个号成体系，面积一族不挡路，覆盖就近生效且答得出来历")
    return 1 if bad else 0


def test_diag():
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
