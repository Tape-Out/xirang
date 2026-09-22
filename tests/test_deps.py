"""依赖来源：清单说得出「这个依赖来自哪」，工具就得照着去找。

从前 `path:` 与 `git:` 写了也没人读——一律按名字在搜索路径上扫。于是搬了目录、
换了检出，构建照样绿，而用的根本不是声明的那一份。

`uv run pytest tests/test_deps.py`
"""
import os
import pathlib
import subprocess

import pytest
import yaml

from xirang_core.lock import locate, resolve_deps
from xirang_core.manifest import Bad, Pkg


def mk(root: pathlib.Path, name: str, deps=None) -> Pkg:
    root.mkdir(parents=True, exist_ok=True)
    ip = {"name": name, "version": "0.1.0", "spec": "0.1", "kind": "library",
          "identity": {"slug": f"{name}-x", "display_name": name,
                       "summary": name, "category": "bus",
                       "ip_family": name, "maturity": "planned"},
          "deps": deps or {}}
    (root / "ip.yaml").write_text(yaml.safe_dump(ip, allow_unicode=True),
                                  encoding="utf-8")
    return Pkg(root)


def test_path_reaches_outside_the_search_path(tmp_path):
    """判据：搜索路径上没有它，靠 path 也要找得到——这是 path 真的在定位的证明。"""
    mk(tmp_path / "far/bus", "bus")
    top = mk(tmp_path / "here/soc", "soc", {"bus": {"path": "../../far/bus"}})
    got = resolve_deps(top, {})          # 索引空着
    assert set(got) == {"soc", "bus"}
    assert got["bus"].root == (tmp_path / "far/bus").resolve()


def test_path_pointing_nowhere(tmp_path):
    top = mk(tmp_path / "soc", "soc", {"bus": {"path": "../nope"}})
    with pytest.raises(Bad, match="XR-DEP-001"):
        resolve_deps(top, {})


def test_path_pointing_at_another_package(tmp_path):
    mk(tmp_path / "other", "uart")
    top = mk(tmp_path / "soc", "soc", {"bus": {"path": "../other"}})
    with pytest.raises(Bad, match="XR-DEP-002"):
        resolve_deps(top, {})


def test_git_without_a_checkout(tmp_path):
    top = mk(tmp_path / "soc", "soc",
             {"bus": {"git": "https://example.invalid/bus", "rev": "a" * 40}})
    with pytest.raises(Bad, match="XR-DEP-003"):
        resolve_deps(top, {})


# 夹具里的仓不能沾用户的 git 配置：本机全局开着 commit.gpgsign，
# 一提交就去要 GPG 口令，缓存过期时整个测试挂在那里等 tty
FIXTURE = ["-c", "user.email=x@y", "-c", "user.name=x",
           "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false",
           "-c", "gpg.format=openpgp"]


def git(root, *a):
    subprocess.run(["git", "-C", str(root), *FIXTURE, *a], check=True,
                   capture_output=True, text=True, timeout=30,
                   env={**os.environ, "GIT_TERMINAL_PROMPT": "0",
                        "GPG_TTY": "", "GIT_CONFIG_NOSYSTEM": "1"})


def test_git_checkout_on_another_commit(tmp_path):
    bus = mk(tmp_path / "bus", "bus")
    git(bus.root, "init", "-q")
    git(bus.root, "commit", "-q", "--allow-empty", "-m", "one")
    first = subprocess.run(["git", "-C", str(bus.root), "rev-parse", "HEAD"],
                           capture_output=True, text=True).stdout.strip()
    git(bus.root, "commit", "-q", "--allow-empty", "-m", "two")
    top = mk(tmp_path / "soc", "soc",
             {"bus": {"git": "https://example.invalid/bus", "rev": first}})
    with pytest.raises(Bad, match="XR-DEP-004"):
        resolve_deps(top, {"bus": bus})
    # 停回声明的那一版就该过
    git(bus.root, "checkout", "-q", first)
    assert set(resolve_deps(top, {"bus": Pkg(bus.root)})) == {"soc", "bus"}


def test_no_source_still_uses_the_search_path(tmp_path):
    bus = mk(tmp_path / "bus", "bus")
    top = mk(tmp_path / "soc", "soc", {"bus": "*"})
    assert locate(top, "bus", {}, {"bus": bus}).root == bus.root


def test_sources_are_recorded_for_the_lock(tmp_path):
    mk(tmp_path / "far/bus", "bus")
    top = mk(tmp_path / "here/soc", "soc", {"bus": {"path": "../../far/bus"}})
    got = {}
    resolve_deps(top, {}, got)
    assert got["bus"] == {"path": "../../far/bus"}
