"""门禁的分流：只有 error 挡路，其余照报不照挡，而且答得出这一级从哪来。

计划里那句话的意思在这里：问题从来不是「某道检查该不该存在」，而是严重级被硬写死了。
面积那三道写死成挡路的，没有综合流程的人连行为仿真都跑不了。

`uv run python tests/test_gate.py`
"""
import sys

from xirang_flow.matrix import CHECKS, route

FOUND = [("XR-AREA-006", "没有价目表"),
         ("XR-AREA-003", "价目表没提到 lines"),
         ("XR-WIRE-001", "rx 只写不读")]


def main() -> int:
    bad: list[str] = []

    stop, note = route(FOUND, [])
    if len(stop) != 1 or "XR-WIRE-001" not in stop[0]:
        bad.append(f"默认该只挡连线那一道：{stop}")
    if len(note) != 2:
        bad.append(f"面积那两道该只报不挡：{note}")

    # 包里把它调上去就挡住
    stop, note = route(FOUND, [("包 ip.yaml", {"XR-AREA-006": "error"})])
    if len(stop) != 2 or not any("XR-AREA-006" in x for x in stop):
        bad.append(f"调到 error 没挡住：{stop}")
    if not any("来自包 ip.yaml" in x for x in stop):
        bad.append("没说这一级是从哪来的")

    # 调下去就挡不住，noshow 连报都不报
    stop, note = route(FOUND, [("包 ip.yaml", {"XR-WIRE-001": "warn"})])
    if stop:
        bad.append(f"调到 warn 还在挡：{stop}")
    stop, note = route(FOUND, [("包 ip.yaml", {"XR-WIRE-001": "noshow"})])
    if any("XR-WIRE-001" in x for x in stop + note):
        bad.append("noshow 还露面了")

    # 每道判据都挂了号，加一道只加一行
    codes = {c for c, _ in CHECKS}
    if len(codes) != len(CHECKS):
        bad.append("同一个号挂了两个判据")

    for line in bad:
        print(f"✘ {line}")
    if not bad:
        print(f"✔ {len(CHECKS)} 道判据各有号；error 挡路、warn 不挡、noshow 不露面、来历说得出")
    return 1 if bad else 0


def test_gate():
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
