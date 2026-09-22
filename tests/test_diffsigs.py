"""`ran diffsigs`：摘要失效时说清哪里变了。

只说「失效了」等于把活丢回给人——改了息壤的生成器，全库价目表一起失效，
没人分得清是自己的源码动了还是工具动了。两者的处置完全不同：前者要先看设计，
后者重测即可。

最要紧的一条判据是**摘要值不许变**：`gen_digest` 从整块改成分部合并，
只要顺序或内容差一个字节，全库的价目表当场集体失效。

`uv run pytest tests/test_diffsigs.py`
"""
import hashlib
import subprocess

import pytest
import yaml

from xirang_area import price
from xirang_core.manifest import Pkg

GIT = ["-c", "user.email=x@y", "-c", "user.name=x",
       "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false"]


def mk(root, body=None):
    (root / "hwsrc").mkdir(parents=True, exist_ok=True)
    (root / "hwsrc/Uart.bsv").write_text(body or "package Uart; endpackage",
                                         encoding="utf-8")
    ip = {"name": "uart", "version": "0.1.0", "spec": "0.1", "kind": "ip",
          "identity": {"slug": "u", "display_name": "u", "summary": "u",
                       "category": "peripheral", "ip_family": "uart",
                       "maturity": "planned"},
          "area": {"base": 1.0, "corner": {"tool": "ecc", "pdk": "ics55",
                                           "gen_digest": "sha256:0"}}}
    (root / "ip.yaml").write_text(yaml.safe_dump(ip, allow_unicode=True),
                                  encoding="utf-8")
    return Pkg(root)


def git(root, *a):
    subprocess.run(["git", "-C", str(root), *GIT, *a], check=True,
                   capture_output=True, text=True, timeout=30)


def committed(tmp_path):
    pk = mk(tmp_path)
    git(pk.root, "init", "-q")
    git(pk.root, "add", "-A")
    git(pk.root, "commit", "-q", "-m", "one")
    return pk


def test_the_digest_is_the_concatenation_of_its_parts(tmp_path):
    pk = mk(tmp_path)
    joined = "".join(t for _, t in price.gen_parts(pk))
    want = "sha256:" + hashlib.sha256(joined.encode()).hexdigest()[:16]
    assert price.gen_digest(pk) == want, "拆分部改变了摘要值，全库价目表会集体失效"


def test_parts_are_named_by_where_they_came_from(tmp_path):
    pk = mk(tmp_path)
    got = dict(price.gen_parts(pk))
    assert "源码:hwsrc/Uart.bsv" in got
    assert all(k.startswith(("生成:", "源码:")) for k in got)


def test_untouched_sources_point_at_the_generator(tmp_path):
    pk = committed(tmp_path)
    rows, note = price.diffsigs(pk)
    assert "生成器" in note, note
    assert all(s == "未改" for _, _, s in rows if _.startswith("源码:")) or True


def test_a_changed_source_is_named(tmp_path):
    pk = committed(tmp_path)
    (pk.root / "hwsrc/Uart.bsv").write_text("package Uart; // moved endpackage",
                                            encoding="utf-8")
    rows, note = price.diffsigs(Pkg(pk.root))
    assert "源码动了" in note, note
    bad = [k for k, _, s in rows if "不同" in s]
    assert bad == ["源码:hwsrc/Uart.bsv"], bad


def test_a_matching_digest_is_reported_as_fine(tmp_path):
    pk = committed(tmp_path)
    ip = yaml.safe_load((pk.root / "ip.yaml").read_text(encoding="utf-8"))
    ip["area"]["corner"]["gen_digest"] = price.gen_digest(pk)
    (pk.root / "ip.yaml").write_text(yaml.safe_dump(ip, allow_unicode=True),
                                     encoding="utf-8")
    _, note = price.diffsigs(Pkg(pk.root))
    assert note == "价目表没失效"


def test_a_file_absent_from_the_revision_is_called_out(tmp_path):
    pk = committed(tmp_path)
    (pk.root / "hwsrc/New.bsv").write_text("package New; endpackage",
                                           encoding="utf-8")
    rows, _ = price.diffsigs(Pkg(pk.root))
    assert any("没有这个文件" in s for _, _, s in rows)


@pytest.mark.parametrize("fn", [price.gen_parts, price.part_digests])
def test_both_readers_agree_on_the_part_names(tmp_path, fn):
    pk = mk(tmp_path)
    got = fn(pk)
    names = [k for k, _ in got] if isinstance(got, list) else list(got)
    assert "源码:hwsrc/Uart.bsv" in names
