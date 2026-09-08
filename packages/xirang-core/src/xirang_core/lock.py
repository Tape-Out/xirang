"""依赖解析与 xirang.lock。

照搬 cargo 的来源模型：`path` / `git`+`rev` / 版本区间。锁文件记下解析到了什么、
以及每个包的内容摘要——摘要变了就说明源码动过，面积与对拍结果都要重新看。

**本版明确不做**：注册表与网络拉取（只支持 path）· 版本求解的回溯（冲突即报错，
不去试别的组合）· 传递依赖的版本统一。这三样都要等真有多版本共存的场景才谈得上。

与 git submodule 的边界：`.gitmodules` 管「目录从哪来」，锁文件管「解析到哪个目录」。
同一个依赖**不得同时**以 submodule 与 `git:` 出现——那才是两个真相源。
"""
from __future__ import annotations

import hashlib
import pathlib
import re

import yaml

from .manifest import Bad, Pkg

LOCK_VERSION = 1
SEMVER = re.compile(r"^(\d+)(?:\.(\d+))?(?:\.(\d+))?")


def _parse(v: str) -> tuple[int, int, int]:
    """缺的段补零。cargo 接受 ^0.1，等同 ^0.1.0——区间写法不该逼人写满三段。"""
    m = SEMVER.match(str(v).strip())
    if not m:
        raise Bad(f"版本号 {v!r} 不是 semver")
    return tuple(int(x or 0) for x in m.groups())


def satisfies(version: str, req: str) -> bool:
    """只实现 cargo 的插入符与精确匹配。够用即止。"""
    req = str(req).strip()
    v = _parse(version)
    if req in ("*", ""):
        return True
    if req.startswith("^"):
        r = _parse(req[1:])
        if r[0] > 0:                       # ^1.2.3 -> >=1.2.3, <2.0.0
            return v[0] == r[0] and v >= r
        if r[1] > 0:                       # ^0.2.3 -> >=0.2.3, <0.3.0
            return v[0] == 0 and v[1] == r[1] and v >= r
        return v[0] == 0 and v[1] == 0 and v[2] >= r[2]
    if req.startswith("="):
        return v == _parse(req[1:])
    return v == _parse(req)


def digest(pkg: Pkg) -> str:
    """包的内容摘要：清单加所有源文件。变了就说明面积与对拍都要重看。"""
    h = hashlib.sha256()
    files = [pkg.root / "ip.yaml"]
    rm = pkg.root / "regmap.yaml"
    if rm.exists():
        files.append(rm)
    for d in ("bsv", "bh", "rtl", "src"):
        p = pkg.root / d
        if p.is_dir():
            files += sorted(p.rglob("*"))
    for f in sorted(files):
        if f.is_file():
            h.update(f.name.encode())
            h.update(f.read_bytes())
    return "sha256:" + h.hexdigest()[:32]


def _sources(pkg: Pkg) -> dict[str, dict]:
    """把 deps 归一成 {名字: 来源描述}。裸字符串即版本区间。"""
    out = {}
    for name, spec in (pkg.ip.get("deps") or {}).items():
        if isinstance(spec, str):
            out[name] = {"req": spec}
        elif isinstance(spec, dict):
            unknown = set(spec) - {"path", "git", "rev", "version"}
            if unknown:
                raise Bad(f"{pkg.name} 的依赖 {name}：不认识的键 {sorted(unknown)}")
            if "git" in spec and "rev" not in spec:
                raise Bad(f"{pkg.name} 的依赖 {name}：给了 git 就必须给 rev，"
                          f"否则锁不住")
            out[name] = dict(spec)
        else:
            raise Bad(f"{pkg.name} 的依赖 {name} 写法不认识")
    return out


def check_submodules(root: pathlib.Path, resolved: dict[str, Pkg]) -> list[str]:
    """同一依赖不得既是 submodule 又写 git:。这是唯一会造成两个真相源的情形。"""
    gm = root / ".gitmodules"
    if not gm.exists():
        return []
    subs = set(re.findall(r"path\s*=\s*(\S+)", gm.read_text(encoding="utf-8")))
    bad = []
    for name, pkg in resolved.items():
        for dep, spec in _sources(pkg).items():
            if "git" in spec and any(pathlib.Path(s).name == dep for s in subs):
                bad.append(f"{name} 的依赖 {dep} 既是 submodule 又写了 git:——"
                           f"是 submodule 就声明成 path:")
    return bad


def resolve_deps(top: Pkg, index: dict[str, Pkg]) -> dict[str, Pkg]:
    """从顶层走一遍依赖图。

    依赖有两种来路：`deps` 声明的，和 `instances` 例化的。**两种都要走同一条
    校验路径**——之前写成两个循环，第二个漏了版本检查，于是经由实例到达的包
    版本要求形同虚设。合成一个工作表就不会漏。

    冲突即报错，不回溯——本版没有版本求解器。
    """
    out: dict[str, Pkg] = {top.name: top}
    seen_req: dict[str, tuple[str, str]] = {}
    stack = [top]

    def want(by: Pkg, name: str, req: str):
        got = index.get(name)
        if got is None:
            raise Bad(f"{by.name} 需要 {name}，但搜索路径里没有这个包")
        if not satisfies(got.ip["version"], req):
            raise Bad(f"{by.name} 要 {name} {req}，但找到的是 {got.ip['version']}")
        if name in seen_req:
            prev_by, prev_req = seen_req[name]
            if prev_req != req and not satisfies(got.ip["version"], prev_req):
                raise Bad(f"{name} 的版本要求冲突：{prev_by} 要 {prev_req}，"
                          f"{by.name} 要 {req}。本版不回溯，请手动统一")
        seen_req[name] = (by.name, req)
        if name not in out:
            out[name] = got
            stack.append(got)

    while stack:
        cur = stack.pop()
        for name, spec in _sources(cur).items():
            want(cur, name, spec.get("req") or spec.get("version") or "*")
        for inst in cur.ip.get("instances", []) or []:
            want(cur, inst["of"], "*")      # 例化不带版本区间，但一样要存在
    return out


def make_lock(top: Pkg, resolved: dict[str, Pkg], root: pathlib.Path) -> dict:
    pkgs = []
    for name in sorted(resolved):
        p = resolved[name]
        try:
            src = {"path": str(p.root.relative_to(root))}
        except ValueError:
            src = {"path": str(p.root)}
        pkgs.append({"name": name, "version": p.ip["version"],
                     "kind": p.kind, "source": src, "digest": digest(p)})
    return {"xirang-lock": LOCK_VERSION, "top": top.name, "packages": pkgs}


def write_lock(lock: dict, path: pathlib.Path):
    path.write_text(yaml.safe_dump(lock, sort_keys=False, allow_unicode=True),
                    encoding="utf-8")


def verify_lock(lock: dict, resolved: dict[str, Pkg]) -> list[str]:
    """锁文件与当前源码对不对得上。摘要变了说明源码动过。"""
    if lock.get("xirang-lock") != LOCK_VERSION:
        return [f"锁文件版本 {lock.get('xirang-lock')} 不是 {LOCK_VERSION}"]
    out = []
    locked = {p["name"]: p for p in lock.get("packages", [])}
    for name, p in resolved.items():
        if name not in locked:
            out.append(f"{name} 不在锁文件里——依赖图变了，重新解析")
            continue
        if locked[name]["version"] != p.ip["version"]:
            out.append(f"{name} 锁的是 {locked[name]['version']}，"
                       f"现在是 {p.ip['version']}")
        d = digest(p)
        if locked[name]["digest"] != d:
            out.append(f"{name} 的源码变了（摘要不符）——面积与对拍结果都要重看")
    for name in locked:
        if name not in resolved:
            out.append(f"{name} 还锁着，但已经不在依赖图里")
    return out
