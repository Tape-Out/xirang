"""档位旋钮的价目表要逐档量，不能只留一个格点。

「未计价」那道门禁看的是价目表提没提这个旋钮。档位旋钮若只量了默认那一档，曲线是平的，
而门禁照样放行——两头都被一个点糊弄过去。`spi` 的 `lines` 第一次量出来就是这样。

档位旋钮也没有「格点之间」：每一种配置都落在格点上，所以不取中点当离格探针，
取了也是非法取值。

`uv run python tests/test_grid.py`
"""
import sys

from xirang_area.recal import grid, plan

RANGE = {"default": 8, "range": [1, 64]}
CHOICE = {"type": "choice", "values": [1, 2, 4, 8], "default": 1}


def main() -> int:
    bad: list[str] = []

    if grid(RANGE) != [1, 8, 64]:
        bad.append(f"区间旋钮的格点变了：{grid(RANGE)}")
    if grid(CHOICE) != [1, 2, 4, 8]:
        bad.append(f"档位旋钮没有逐档量：{grid(CHOICE)}")

    p = plan({"params": {"fifoDepth": RANGE, "lines": CHOICE}})
    pts = sorted(int(k) for k in p["area"]["params"]["lines"]["points"])
    if pts != [1, 2, 4, 8]:
        bad.append(f"价目表里 lines 的格点是 {pts}")
    if any(pr.get("lines") not in (1, None) for pr in p["probes"]):
        bad.append(f"档位旋钮不该有离格探针：{p['probes']}")
    if not any(r.get("lines") == 8 for r in p["rows"]):
        bad.append("最高那一档没安排实测行")

    for line in bad:
        print(f"✘ {line}")
    if not bad:
        print("✔ 档位逐档量、区间量两端与默认、档位不取中点")
    return 1 if bad else 0


def test_grid():
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
