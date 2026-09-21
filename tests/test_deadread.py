"""死输入门禁要认得两种语法。

`dead_inputs` 原来只匹配 BSV 的 `Wire#(..) x <- mkBypassWire`，`.bs` 里的线一根都看不见——
而这一轮要写的 IP 有一半是 BH。造几份最小的源码：BSV 里只写不读的线照旧报；BH 里只写不读的
线也要报（两种声明写法都要认）；BH 里真的读过的线不报；`x := v` 是写，不算读。

`uv run python tests/test_deadread.py`
"""
import pathlib
import sys
import tempfile
from types import SimpleNamespace

from xirang_gen.check import dead_inputs

BSV_DEAD = """package A;
module mkA(Empty);
  Wire#(Bit#(8)) pinA <- mkBypassWire;
  rule drive; pinA <= 1; endrule
endmodule
endpackage
"""

BH_DEAD = """package B where
mkB :: (IsModule m c) => m Empty
mkB = module
    pinB :: Wire (Bit 8) <- mkBypassWire
    rules
      "drive": when True ==> pinB := 1
"""

BH_SPLIT = """package C where
mkC :: (IsModule m c) => m Empty
mkC = module
    pinC :: Wire (Bit 8)
    pinC <- mkDWire 0
    rules
      "drive": when True ==> pinC := 1
"""

BH_READ = """package D where
mkD :: (IsModule m c) => m Empty
mkD = module
    pinD :: Wire (Bit 8) <- mkBypassWire
    r :: Reg (Bit 8) <- mkReg 0
    rules
      "use": when True ==> r := pinD   -- pinD 在这里被读
"""


def dead(files: dict[str, str]) -> list[str]:
    with tempfile.TemporaryDirectory() as d:
        root = pathlib.Path(d)
        (root / "hwsrc").mkdir()
        for name, src in files.items():
            (root / "hwsrc" / name).write_text(src, encoding="utf-8")
        # 假包也得会答「源码在哪」——这一问现在由清单声明，缺省是同名目录
        return dead_inputs(SimpleNamespace(root=root, ip={},
                                           dirs=lambda k: [root / k]))


def main() -> int:
    bad: list[str] = []
    for what, files, name, want in [
        ("BSV 只写不读", {"A.bsv": BSV_DEAD}, "pinA", True),
        ("BH 一行声明、只写不读", {"B.bs": BH_DEAD}, "pinB", True),
        ("BH 先签名后例化、只写不读", {"C.bs": BH_SPLIT}, "pinC", True),
        ("BH 读过", {"D.bs": BH_READ}, "pinD", False),
    ]:
        hit = any(m.startswith(f"{name} ") for m in dead(files))
        if hit != want:
            bad.append(f"{what}：{'该报没报' if want else '不该报却报了'} {name}")
    for line in bad:
        print(f"✘ {line}")
    if not bad:
        print("✔ 两种语法的死输入都认得，读过的线不误报")
    return 1 if bad else 0


def test_deadread():
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
