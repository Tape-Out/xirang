"""来源：每个用到的包在哪个仓、哪个提交，黑盒再往下记到上游子模块。

流片说明里「这颗芯片是从哪份源码出来的」要答得出，而且要能让别人照着取回同一份。
不是 git 检出的目录（工作副本、解开的 tar）照实写 None，不去猜。
"""
import pathlib
import re
import subprocess


def _git(root: pathlib.Path, *args) -> str | None:
    try:
        r = subprocess.run(["git", "-C", str(root), *args],
                           capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def web(url: str | None) -> str | None:
    """`git@github.com:o/r.git` 与 `https://github.com/o/r.git` 记成同一个网址。"""
    if not url:
        return url
    if m := re.fullmatch(r"(?:ssh://)?git@([^:/]+)[:/](.+?)(?:\.git)?/?", url):
        return f"https://{m.group(1)}/{m.group(2)}"
    return re.sub(r"\.git/?$", "", url)


def repo(root: pathlib.Path) -> dict:
    """这个目录所在的仓：网址、提交、是否有未提交的改动、它在仓里的相对位置。"""
    top = _git(root, "rev-parse", "--show-toplevel")
    if top is None:
        return {"repo": None, "ref": None, "dirty": None}
    out = {"repo": web(_git(root, "remote", "get-url", "origin")),
           "ref": _git(root, "rev-parse", "HEAD"),
           "dirty": bool(_git(root, "status", "--porcelain", "--untracked-files=no"))}
    rel = pathlib.Path(root).resolve().relative_to(pathlib.Path(top).resolve())
    if str(rel) != ".":
        out["subdir"] = str(rel)
    return out


def submodules(root: pathlib.Path) -> list[dict]:
    """递归列出子模块的路径、网址与检出的提交。没检出的提交前面是 `-`，照记并标出来。"""
    st = _git(root, "submodule", "status", "--recursive")
    if not st:
        return []
    urls = {}
    for line in (_git(root, "config", "-f", ".gitmodules", "--get-regexp",
                      r"^submodule\..*\.url$") or "").splitlines():
        k, _, v = line.partition(" ")
        urls[k[len("submodule."):-len(".url")]] = v
    paths = {}
    for line in (_git(root, "config", "-f", ".gitmodules", "--get-regexp",
                      r"^submodule\..*\.path$") or "").splitlines():
        k, _, v = line.partition(" ")
        paths[v] = k[len("submodule."):-len(".path")]
    out = []
    for line in st.splitlines():
        m = re.match(r"^([ +\-U])([0-9a-f]{7,64}) (\S+)", line)
        if not m:
            continue
        flag, sha, path = m.groups()
        sub = pathlib.Path(root) / path
        url = urls.get(paths.get(path, ""))
        if url is None:
            url = _git(sub, "remote", "get-url", "origin")
        out.append({"path": path, "repo": web(url), "ref": sha,
                    "checked_out": flag != "-", "modified": flag == "+"})
    return out


def sources(names, pkgs) -> list[dict]:
    """装配闭包里每个包一行。"""
    out = []
    for n in sorted(names):
        p = pkgs[n]
        row = {"pkg": n, "version": p.ip.get("version"), "kind": p.kind,
               **repo(p.root)}
        if p.foreign_emit() is not None:
            row["upstream"] = submodules(p.root)
        out.append(row)
    return out
