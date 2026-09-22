"""依赖解析与 xirang.lock。

照搬 cargo 的来源模型：`path` / `git`+`rev` / 版本区间。锁文件记下解析到了什么、
以及每个包的内容摘要——摘要变了就说明源码动过，面积与对拍结果都要重新看。

**本版明确不做**：注册表与网络拉取（只支持 path）· 版本求解的回溯（冲突即报错，
不去试别的组合）· 传递依赖的版本统一。这三样都要等真有多版本共存的场景才谈得上。

与 git submodule 的边界：`.gitmodules` 管「目录从哪来」，锁文件管「解析到哪个目录」。
同一个依赖**不得同时**以 submodule 与 `git:` 出现——那才是两个真相源。
"""
import hashlib
import pathlib
import subprocess
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
    # 与清单里声明的目录对齐。**改这个集合会让全组织的摘要失效**——
    # 2026-09-21 改名那次就是这样，四颗装配全部重锁
    for d in ("hwsrc", "swsrc", "rtl", "src"):
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


def _git(root: pathlib.Path, rev: str) -> str | None:
    """检出里 rev 指的那个提交。不是 git 仓或解不出就返回 None。"""
    r = subprocess.run(["git", "-C", str(root), "rev-parse", f"{rev}^{{commit}}"],
                       capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None


def locate(by: Pkg, name: str, spec: dict, index: dict[str, Pkg]) -> Pkg:
    """依赖从哪来。

    从前这里只按名字在搜索路径上扫，`path:` 与 `git:` 写了也没人读——清单说得出
    「这个依赖来自哪」，工具却做不到，两边各说各的。现在来源真的用于定位：写了
    `path:` 的依赖不必在搜索路径上也找得到，写了 `git:` 的要核对检出停在哪个提交。

    本版不联网取：`git:` 认的是本机已有的检出。取不取得到是另一件事，**核对得对不对
    是这一件**。
    """
    if p := spec.get("path"):
        root = (by.root / p).resolve()
        if not (root / "ip.yaml").is_file():
            raise Bad(f"XR-DEP-001 {by.name} 的依赖 {name}：path 指向 {root}，"
                      f"那里没有 ip.yaml")
        got = Pkg(root)
        if got.name != name:
            raise Bad(f"XR-DEP-002 {by.name} 的依赖 {name}：path 指向的包叫 "
                      f"{got.name}，不是 {name}")
        return got
    if url := spec.get("git"):
        got = index.get(name)
        if got is None:
            raise Bad(f"XR-DEP-003 {by.name} 的依赖 {name} 声明了 git: {url}，"
                      f"但本机没有它的检出。本版不联网取，先克隆到搜索路径上")
        want, head = _git(got.root, spec["rev"]), _git(got.root, "HEAD")
        if want is None:
            raise Bad(f"XR-DEP-004 {by.name} 的依赖 {name}：{got.root} 里解不出 "
                      f"rev {spec['rev']}")
        if want != head:
            raise Bad(f"XR-DEP-004 {by.name} 的依赖 {name}：声明 rev {spec['rev']}"
                      f"（{want[:12]}），检出停在 {head[:12]}")
        return got
    got = index.get(name)
    if got is None:
        raise Bad(f"{by.name} 需要 {name}，但搜索路径里没有这个包")
    return got


def resolve_deps(top: Pkg, index: dict[str, Pkg],
                 sources: dict | None = None) -> dict[str, Pkg]:
    """从顶层走一遍依赖图。

    依赖有两种来路：`deps` 声明的，和 `instances` 例化的。**两种都要走同一条
    校验路径**——之前写成两个循环，第二个漏了版本检查，于是经由实例到达的包
    版本要求形同虚设。合成一个工作表就不会漏。

    冲突即报错，不回溯——本版没有版本求解器。
    """
    out: dict[str, Pkg] = {top.name: top}
    seen_req: dict[str, tuple[str, str]] = {}
    stack = [top]

    def want(by: Pkg, name: str, req: str, spec: dict | None = None):
        got = locate(by, name, spec or {}, index)
        if sources is not None and name not in sources:
            sources[name] = dict(spec or {})
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
            want(cur, name, spec.get("req") or spec.get("version") or "*", spec)
        for inst in cur.ip.get("instances", []) or []:
            want(cur, inst["of"], "*")      # 例化不带版本区间，但一样要存在
    return out


def make_lock(top: Pkg, resolved: dict[str, Pkg], root: pathlib.Path,
              sources: dict | None = None) -> dict:
    """锁里记的来源要与清单声明的一致。

    从前一律记成路径，于是一个 `git:` 依赖锁完看不出它本来是按提交钉住的——
    照着锁重建的人拿到的是「某台机器上的某个目录」。
    """
    pkgs = []
    for name in sorted(resolved):
        p = resolved[name]
        spec = (sources or {}).get(name) or {}
        if spec.get("git"):
            src = {"git": spec["git"], "rev": spec["rev"]}
        else:
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
