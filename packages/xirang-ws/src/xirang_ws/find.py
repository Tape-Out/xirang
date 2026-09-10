"""工作区：包在哪、有哪些。

`workspace.yaml` 在的时候，成员清单说了算；不在的时候退回「扫搜索路径」，
与从前一样。
"""
import pathlib

from xirang_core.manifest import Bad, Pkg

from .manifest import Workspace, find as find_ws


def roots(paths) -> list[pathlib.Path]:
    return [pathlib.Path(p).resolve() for p in (paths or ["."])]


def _scan(search: list[pathlib.Path], strict: bool) -> dict[str, Pkg]:
    out: dict[str, Pkg] = {}
    for d in search:
        for p in sorted(d.iterdir()) if strict else d.iterdir():
            if not (p / "ip.yaml").exists():
                continue
            pk = Pkg(p)
            if strict and pk.name in out and out[pk.name].root != p:
                raise Bad(f"包名 {pk.name} 出现两次：{out[pk.name].root} 与 {p}")
            out[pk.name] = pk
    return out


def index(search: list[pathlib.Path], ws: Workspace | None = None) -> dict[str, Pkg]:
    got = _scan(search, strict=True)
    return {n: p for n, p in got.items() if ws.wanted(n)} if ws else got


def load_all(search: list[pathlib.Path]) -> dict[str, Pkg]:
    return _scan(search, strict=False)


def open_(paths) -> tuple[list[pathlib.Path], Workspace | None, dict[str, Pkg]]:
    """一次把搜索路径、工作区清单与包索引都拿到。命令入口用这一个就够。"""
    search = roots(paths)
    ws = find_ws(search)
    return search, ws, index(search, ws)
