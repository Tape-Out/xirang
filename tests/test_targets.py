"""构建目标：写出来，工具能答，CI 不必猜。

今天 CI 靠 `grep ip.yaml` 四路分派（library / 有 instances / verilog-flat / 其余算 regs）。
`rvdbg` 那种只出 BSV、既没有寄存器图也不扁平化的包落进「其余」，跑的是它根本没有的
寄存器一致性测试。推断本身没问题，**把推断藏在 CI 的 grep 里才有问题**。

一个包可以有好几个目标：`uart` 既出寄存器组也出扁平端口顶层。

`uv run python tests/test_targets.py`
"""
import copy
import pathlib
import sys
import tempfile

import yaml

from xirang_core.manifest import Bad, Pkg

BASE = {"name": "t", "version": "0.1.0", "spec": "0.1", "kind": "ip", "lang": "bsv",
        "contract": {"version": 1, "ctrl": {"shape": "flat", "aw": 8, "dw": 32}},
        "emit": [{"kind": "bsv", "package": "T", "module": "mkT",
                  "config_type": "TCfg", "interface": "TIfc"}]}
REGMAP = {"ip": "t", "contract": {"aw": 8, "dw": 32}, "regs": [
    {"name": "r", "offset": 0,
     "fields": [{"name": "val", "width": 4, "sw": "rw", "hw": "r", "reset": 0}]}]}


def mk(root: pathlib.Path, ip: dict, regmap: dict | None = None) -> Pkg:
    for f in root.glob("*.yaml"):
        f.unlink()
    (root / "hwsrc").mkdir(parents=True, exist_ok=True)
    (root / "hwsrc/T.bsv").write_text("package T; endpackage\n")
    (root / "ip.yaml").write_text(yaml.safe_dump(ip, allow_unicode=True))
    if regmap:
        (root / "regmap.yaml").write_text(yaml.safe_dump(regmap, allow_unicode=True))
    return Pkg(root)


def drivers(pk: Pkg) -> set[str]:
    return {v["driver"] for v in pk.targets().values()}


def main() -> int:
    bad: list[str] = []
    with tempfile.TemporaryDirectory() as d:
        root = pathlib.Path(d) / "t"

        # 推断：一个包可以有好几个目标
        cases = [
            ("只出 BSV", BASE, None, {"bsv"}),
            ("带寄存器图", BASE, REGMAP, {"regs"}),
            ("寄存器图 + 扁平", {**BASE, "emit": BASE["emit"] + [
                {"kind": "verilog-flat", "bus": "apb4"}]}, REGMAP, {"regs", "flat"}),
            ("库", {k: v for k, v in BASE.items()
                    if k not in ("contract", "emit")} | {"kind": "library"}, None, {"library"}),
            ("装配", {**BASE, "instances": [{"name": "a", "of": "t"}], "bus": "apb4"},
             None, {"assembly"}),
            ("什么都不出", {k: v for k, v in BASE.items() if k != "emit"}, None, {"none"}),
        ]
        for what, ip, rm, want in cases:
            got = drivers(mk(root, copy.deepcopy(ip), rm))
            if got != want:
                bad.append(f"{what}：推断成 {got}，应是 {want}")

        # 写出来的照收
        pk = mk(root, {**BASE, "targets": {"sim": {"driver": "bsv"}}}, None)
        if drivers(pk) != {"bsv"}:
            bad.append("显式写的目标没被收下")

        # 写错的当场报：声明要对得上树上真有的东西
        wrong = [
            ("说自己是 assembly 却没有 instances", {"a": {"driver": "assembly"}}, None),
            ("说自己是 regs 却没有 regmap.yaml", {"a": {"driver": "regs"}}, None),
            ("说自己是 flat 却没有 verilog-flat 段", {"a": {"driver": "flat"}}, None),
            ("说自己是 library 却不是库", {"a": {"driver": "library"}}, None),
            ("不认识的 driver", {"a": {"driver": "magic"}}, None),
            ("不认识的键", {"a": {"driver": "bsv", "extra": 1}}, None),
        ]
        for what, tg, rm in wrong:
            try:
                mk(root, {**BASE, "targets": tg}, rm)
                bad.append(f"{what}：没报错")
            except Bad:
                pass

    for line in bad:
        print(f"✘ {line}")
    if not bad:
        print(f"✔ {len(cases)} 种推断对，显式声明收得下，{len(wrong)} 种写错的各报各的")
    return 1 if bad else 0


def test_targets():
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
