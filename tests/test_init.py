"""`ran recal --init` 的规划：新包第一次量价目表，量哪几行、离格点在哪、余量怎么定。

规划是纯函数，不综合也能测；真的去量要 PDK，在本机跑。

`uv run python tests/test_init.py`
"""
import sys

from xirang_core.manifest import Bad
from xirang_area.recal import grid, lift, plan

# 照 wdt 的形状：一个参数（默认在量程上端）、一个 bool 特性
WDT = {
    "params": {"width": {"type": "int", "default": 32, "range": [16, 32]}},
    "features": {"window": {"type": "bool", "default": True}},
}

# 两个参数、两个特性且一个依赖另一个：默认值在量程中间
TWO = {
    "params": {"depth": {"type": "int", "default": 8, "range": [1, 64]},
               "ports": {"type": "int", "default": 2, "range": [1, 4]}},
    "features": {"smode": {"type": "bool", "default": False},
                 "mmu": {"type": "bool", "default": False, "depends": ["smode"]}},
}


def main() -> int:
    bad: list[str] = []

    def want(what, got, exp):
        if got != exp:
            bad.append(f"{what}：得到 {got}，应当 {exp}")

    want("量程两端加默认值", grid({"default": 8, "range": [1, 64]}), [1, 8, 64])
    want("默认值与上端重合", grid({"default": 32, "range": [16, 32]}), [16, 32])
    want("没有量程只量默认值", grid({"default": 4}), [4])

    p = plan(WDT)
    want("wdt 的实测行", sorted(map(lambda r: (r["width"], r["window"]), p["rows"])),
         [(16, False), (16, True), (32, False), (32, True)])
    want("wdt 的基线曲线", p["area"]["base"], {"per": "width", "points": {"16": 0.0, "32": 0.0}})
    want("wdt 的特性曲线", p["features"]["window"], {"per": "width", "points": {"16": 0.0, "32": 0.0}})
    # 离格点落在两个格点中间，特性全关与全开各一行
    want("wdt 的离格点", p["probes"], [{"width": 24, "window": False}, {"width": 24, "window": True}])

    p = plan(TWO)
    rows = p["rows"]
    if any(set(r) != {"depth", "ports", "smode", "mmu"} for r in rows):
        bad.append("实测行没有把旋钮写全——少写一个就等于声称那个旋钮取什么值都是这个面积")
    on = [r for r in rows if r["mmu"]]
    if not on or any(not r["smode"] for r in on):
        bad.append(f"mmu 依赖 smode，它开着的行 smode 也得开：{on}")
    want("第二个参数的增量曲线", p["area"]["params"], {"ports": {"points": {"1": 0.0, "2": 0.0, "4": 0.0}}})
    want("离格点", sorted(tuple(sorted(x.items())) for x in p["probes"]),
         sorted(tuple(sorted(x.items())) for x in [
             {"depth": 4, "ports": 2, "smode": False, "mmu": False},
             {"depth": 4, "ports": 2, "smode": True, "mmu": True},
             {"depth": 36, "ports": 2, "smode": False, "mmu": False},
             {"depth": 36, "ports": 2, "smode": True, "mmu": True},
             {"depth": 8, "ports": 3, "smode": False, "mmu": False},
             {"depth": 8, "ports": 3, "smode": True, "mmu": True},
             {"depth": 8, "ports": 2, "smode": True, "mmu": True},
         ]))

    try:
        plan({"params": {}, "features": {"bus": {"type": "choice", "values": ["a", "b"], "default": "a"}}})
        bad.append("choice 特性没拦住：recal 只会给 fixed 与 points 两种形态回填")
    except Bad:
        pass

    want("预测偏高不抵扣", lift([(100.0, 90.0)]), 0.0)
    want("取最坏的欠估并向上取到千分之一", lift([(100.0, 103.21), (200.0, 201.0)]), 0.033)
    want("没有离格点就不抬", lift([]), 0.0)

    for line in bad:
        print(f"✘ {line}")
    if not bad:
        print("✔ 取点、实测行、曲线形态、离格点与余量都对")
    return 1 if bad else 0


def test_init():
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
