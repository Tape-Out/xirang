"""模板不能被当成真货。

两条落盘规矩（`.in` 后缀、`dot.` 前缀）是为了让模板在仓里不被发现成包、不触发
组织的门禁。规矩靠约定维持不住，所以写成判据：哪天有人把 `ip.yaml.in` 改回
`ip.yaml`，这里就红。

发现机制只扫一层目录，所以今天即使改回去也未必立刻出事——正因为不会立刻出事，
才更需要一条判据把规矩钉住。
"""
import pathlib
import re

import pytest

SKEL = (pathlib.Path(__file__).resolve().parents[1]
        / "packages/xirang/src/xirang/skel")

pytestmark = pytest.mark.skipif(not (SKEL / "skel.yaml").is_file(),
                                reason="子模块没拉：git submodule update --init")


def _files():
    return [f for f in SKEL.rglob("*") if f.is_file() and ".git" not in f.parts]


def test_no_bare_manifest_in_templates():
    """模板里不许有字面叫 ip.yaml / regmap.yaml 的文件。"""
    bad = [f for f in _files()
           if f.name in ("ip.yaml", "regmap.yaml")
           and "templates" in f.parts]
    assert not bad, f"XR-NEW-004 这些该带 .in 后缀：{[str(f) for f in bad]}"


def test_no_dot_github_landed():
    """模板里不许有真的 .github/，它会触发组织的门禁。"""
    bad = [f for f in _files() if ".github" in f.parts]
    assert not bad, f"XR-NEW-004 .github 要存成 dot.github：{[str(f) for f in bad]}"


def test_no_bare_tb_generator():
    """mk*.py 会被门禁当测试台逐个跑，模板里的那份必须带 .in。"""
    bad = [f for f in _files()
           if re.fullmatch(r"mk.*\.py", f.name) and "templates" in f.parts]
    assert not bad, f"XR-NEW-004 这些该带 .in 后缀：{[str(f) for f in bad]}"


def test_placeholders_are_all_known():
    """出现表外的占位符，渲染时会报 XR-NEW-003；这里提前拦住。"""
    import yaml
    doc = yaml.safe_load((SKEL / "skel.yaml").read_text(encoding="utf-8"))
    known = set(doc["rules"]["placeholders"])
    ph = re.compile(r"(?<!\$)\{\{\s*(\w+)\s*\}\}")
    bad = []
    for f in _files():
        try:
            txt = f.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for m in ph.finditer(txt + " " + f.name):
            if m.group(1) not in known:
                bad.append(f"{f}: {m.group(0)}")
    assert not bad, f"XR-NEW-003 表外的占位符：{bad}"


def test_every_template_renders_clean(tmp_path):
    """每个模板都渲染一遍：不许留下 {{、.in 或 dot.。"""
    import yaml
    from xirang.new import PH as ph, plan_files
    doc = yaml.safe_load((SKEL / "skel.yaml").read_text(encoding="utf-8"))
    for t in doc["templates"]:
        files, dirs = plan_files(t, "probe")
        for rel, data in files.items():
            s = str(rel)
            assert ".in" not in pathlib.Path(s).suffixes, f"{t}: {s} 还带 .in"
            assert "dot." not in s, f"{t}: {s} 还带 dot."
            try:
                # 查的是「没渲染掉的占位符」，不是「有没有花括号」：生成出来的
                # 测试台是 Python f-string，里面的 {{ }} 是 BSV 花括号的转义，
                # 本来就该在。
                left = ph.search(data.decode())
                assert left is None, f"{t}: {s} 里还剩 {left.group(0)}"
            except UnicodeDecodeError:
                pass
        assert files, f"{t} 渲染出来是空的"
