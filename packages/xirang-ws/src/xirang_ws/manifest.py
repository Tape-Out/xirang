"""工作区清单：这次流片用哪些包，各自什么默认值。

对应 cargo 的 workspace 与 bitbake 的 bblayers.conf。今天包搜索路径唯一来源是
命令行 -p，于是没有 status 可言——推了一半仓、已发布的状态不自洽，也没人拦。
"""
import fnmatch
import pathlib
from dataclasses import dataclass

import yaml

from xirang_core.lock import resolve_deps, verify_lock
from xirang_core.manifest import Bad, Loc, Pkg, load, where

NAME = "workspace.yaml"
TOP_KEYS = {"xirang-workspace", "members", "defaults", "__path__"}
VERSION = 1


@dataclass(slots=True)
class Workspace:
    root: pathlib.Path
    members: list[str]
    defaults: dict[str, object]      # 键写成 <包名>.<旋钮>
    doc: Loc

    @property
    def path(self) -> str:
        return str(self.root / NAME)

    def origin(self, key: str) -> str:
        return where(self.doc.get("defaults"), key, self.path)

    def wanted(self, name: str) -> bool:
        return any(fnmatch.fnmatchcase(name, m) for m in self.members)

    def knobs(self, pkg: str) -> dict[str, object]:
        """这个包在工作区那一层拿到的取值。"""
        return {k.partition(".")[2]: v for k, v in self.defaults.items()
                if k.partition(".")[0] == pkg}


def read(root: pathlib.Path) -> Workspace:
    doc = load(root / NAME)
    if unknown := set(doc) - TOP_KEYS:
        raise Bad(f"{root / NAME}: 不认识的键 {sorted(unknown)}——"
                  f"写错的键会被默默忽略")
    if doc.get("xirang-workspace") != VERSION:
        raise Bad(f"{root / NAME}: 头一行要写 xirang-workspace: {VERSION}")
    members = doc.get("members") or ["*"]
    if not isinstance(members, list) or not all(isinstance(m, str) for m in members):
        raise Bad(f"{root / NAME}: members 要是一串名字或通配")
    defaults = doc.get("defaults") or {}
    for k in defaults:
        if "." not in k:
            raise Bad(f"{root / NAME}: defaults 的键要写成 <包名>.<旋钮>，"
                      f"收到 {k!r}——不带包名就不知道该压给谁")
    return Workspace(root=root, members=members, defaults=dict(defaults), doc=doc)


def find(roots: list[pathlib.Path]) -> Workspace | None:
    return next((read(d) for d in roots if (d / NAME).exists()), None)


def status(ws: Workspace | None, index: dict[str, Pkg]) -> list[str]:
    """推之前该看的那一眼：清单、源码、锁三者对不对得上。"""
    out: list[str] = []
    if ws is None:
        return ["没有 workspace.yaml——包靠命令行 -p 找，没有清单就没有 status"]

    for m in ws.members:
        if not any(c in m for c in "*?[") and m not in index:
            out.append(f"清单里写着 {m}，工作区里找不到")
    out += [f"{n} 在工作区里，清单却没写它" for n in sorted(index)
            if not ws.wanted(n)]
    out += [f"defaults 压的 {k} 不在工作区里" for k in sorted(ws.defaults)
            if k.partition(".")[0] not in index]

    for name, pkg in sorted(index.items()):
        lock = pkg.root / "xirang.lock"
        if not lock.exists():
            continue
        doc = yaml.safe_load(lock.read_text(encoding="utf-8"))
        out += [f"{name}: {p}" for p in verify_lock(doc, resolve_deps(pkg, index))]
    return out
