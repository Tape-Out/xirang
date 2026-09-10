"""解耦是有判据的：依赖图必须无环，核心必须在底，命令行不许被任何人依赖。

写成脚本，结构才不会随时间烂掉。`uv run python tests/test_structure.py`
"""
import graphlib
import pathlib
import re
import sys
from collections import defaultdict

ROOT = pathlib.Path(__file__).resolve().parent.parent / "packages"
IMPORT = re.compile(r"^\s*(?:from|import)\s+(xirang[a-z_]*)", re.M)


def graph() -> dict[str, set[str]]:
    edges: dict[str, set[str]] = defaultdict(set)
    for d in sorted(p for p in ROOT.iterdir() if (p / "src").is_dir()):
        own = d.name.replace("-", "_")
        edges.setdefault(own, set())
        for f in (d / "src").rglob("*.py"):
            for m in IMPORT.finditer(f.read_text(encoding="utf-8")):
                if m.group(1) != own:
                    edges[own].add(m.group(1))
    return edges


def main() -> int:
    edges = graph()
    bad: list[str] = []

    try:
        order = list(graphlib.TopologicalSorter(edges).static_order())
    except graphlib.CycleError as e:
        print(f"依赖图有环：{e}")
        return 1

    if edges["xirang_core"]:
        bad.append(f"core 依赖了 {sorted(edges['xirang_core'])}——它该在最底下")
    if users := [n for n, deps in edges.items() if "xirang" in deps]:
        bad.append(f"{users} 依赖了命令行——命令行只该被人用，不该被包用")
    if edges["xirang_back"]:
        bad.append(f"back 依赖了 {sorted(edges['xirang_back'])}——"
                   f"它只驱动外部工具，不认识我们的数据模型")

    for line in bad:
        print(f"✘ {line}")
    if not bad:
        print("✔ " + " < ".join(order))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
