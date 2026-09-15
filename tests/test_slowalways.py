"""emit.slow_when 可以写 true：控制口永远是会停顿的那个。

sdram 这类片外存储的控制器每一笔都要等片子，没有「同拍答」的形态可选。原来 slow_when 只能写一个
特性名、开着才走会停顿的口，永远停顿的 IP 只好编一个恒开的假特性。认这条规则的有三处：清单校验、
扁平顶层与中立顶层（wrap._shape）、装配的地址图（assemble），所以规则收在 core 的 slow_ctrl 里，
生成器里不许再自己按 slow_when 去取旋钮。

`uv run python tests/test_slowalways.py`
"""
import itertools
import pathlib
import re
import sys
import tempfile
from types import SimpleNamespace

from xirang_core.manifest import Bad, Pkg

try:
    from xirang_core.manifest import slow_ctrl
except ImportError:
    slow_ctrl = None

ROOT = pathlib.Path(__file__).resolve().parent.parent / "packages"
SEQ = itertools.count()

IP = """name: t
version: 0.1.0
spec: '0.1'
kind: ip
contract:
  version: 1
  ctrl:
    shape: flat
    aw: 8
    dw: 32
emit:
- kind: bsv
  package: T
  module: mkT
  config_type: TCfg
  interface: TIfc
  ctrl: regs
  ctrl_slow: slow
  slow_when: {when}
"""


def pkg(tmp: str, when: str) -> Pkg:
    d = pathlib.Path(tmp) / f"t{next(SEQ)}"
    d.mkdir()
    (d / "ip.yaml").write_text(IP.format(when=when), encoding="utf-8")
    return Pkg(d)


def main() -> int:
    bad: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        p = None
        try:
            p = pkg(tmp, "true")
            p.bsv_emit()
        except Bad as e:
            p = None
            bad.append(f"slow_when: true 的清单读不进来：{e}")
        for when, why in (("nosuch", "写一个不存在的特性名"),
                          ("false", "写 false（永远不停顿就不该写 ctrl_slow）")):
            try:
                pkg(tmp, when).bsv_emit()
                bad.append(f"slow_when {why}，清单照样读进来了")
            except Bad:
                pass

        if slow_ctrl is None:
            bad.append("core 里没有 slow_ctrl，选口规则没有收在一处")
        else:
            on, off = SimpleNamespace(value=True), SimpleNamespace(value=False)
            always = {"ctrl_slow": "slow", "slow_when": True}
            byfeat = {"ctrl_slow": "slow", "slow_when": "sync"}
            if not slow_ctrl(always, {}):
                bad.append("slow_when: true 时 slow_ctrl 说不停顿")
            if not slow_ctrl(byfeat, {"sync": on}) or slow_ctrl(byfeat, {"sync": off}):
                bad.append("slow_when 是特性名时 slow_ctrl 没跟着特性走")
            if slow_ctrl({"ctrl": "regs"}, {}):
                bad.append("没有 ctrl_slow 时 slow_ctrl 说停顿")

        if p is not None:
            from xirang_gen.wrap import _shape
            try:
                s = _shape(p, {})
                if (s["ctrl_type"], s["ctrl_name"]) != ("RegTarget", "slow"):
                    bad.append(f"slow_when: true 时顶层的控制口是 {s['ctrl_type']} {s['ctrl_name']}")
            except Exception as e:
                bad.append(f"slow_when: true 时 wrap._shape 出错：{type(e).__name__} {e}")

    for f in sorted((ROOT / "xirang-gen" / "src").rglob("*.py")):
        txt = f.read_text(encoding="utf-8")
        if re.search(r"slow_when[\"']\]\]|values\[sw\]", txt):
            bad.append(f"{f.name} 还在自己按 slow_when 取旋钮，没走 slow_ctrl")

    for line in bad:
        print(f"✘ {line}")
    if not bad:
        print("✔ slow_when 认 true 与特性名，清单、顶层、装配三处走同一条规则")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
