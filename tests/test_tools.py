"""外部工具：只驱动，不打包；参数在交给后端之前就展开掉。

ecc 与 yosys-sta 不接受从外面传进来的 Verilog 参数——它们读文件、指定顶层，参数
就取默认值。于是「息壤配好的那套旋钮」到了后端会悄悄变回上游默认，而没有任何一步
会报错：面积与时序量的是另一颗核。所以配置解完就把参数展开掉。

sv2v 做不了这件事（实测 `--top` 只删没被例化的模块，`parameter` 原样保留），
它是 SystemVerilog 到 Verilog-2005 的翻译器，与展开参数是两件事。

`uv run python tests/test_tools.py`
"""
import pathlib
import sys
from types import SimpleNamespace

from xirang_back import tools
from xirang_back.iv import _short
from xirang_back.verilog import script
from xirang_core.manifest import Bad
from xirang_gen.foreign import _camel, bake


def val(v):
    return SimpleNamespace(value=v)


PKG = SimpleNamespace(
    name="pv",
    foreign_emit=lambda: {"params": {"ENABLE_MUL": "enableMul",
                                     "PROGADDR_RESET": "progaddrReset",
                                     "FIFO": 8}})


def main() -> int:
    bad: list[str] = []

    # 注册表：加一样只加一行，每样都说得出拿它做什么
    rows = tools.survey()
    if len(rows) < 10:
        bad.append(f"工具表只有 {len(rows)} 样")
    for n, _, what in rows:
        if not what:
            bad.append(f"{n} 没写拿它做什么")
    if "sv2v" not in tools.TOOLS or "dot" not in tools.TOOLS:
        bad.append("sv2v 或 dot 不在表里")
    try:
        tools.need("nosuchtool")
        bad.append("找不到的工具没报错")
    except tools.ToolError as e:
        if "PATH" not in str(e):
            bad.append(f"报错没说怎么办：{e}")

    # yosys 脚本：chparam 必须在 hierarchy 之前，proc 不能少
    sc = script(["a.v", "b.v"], "top", {"W": 16, "F": 1}, pathlib.Path("o.v"))
    for want in ("read_verilog a.v", "read_verilog b.v",
                 "chparam -set F 1 -set W 16 top", "hierarchy -top top -check",
                 "proc", "write_verilog -noattr o.v"):
        if want not in sc:
            bad.append(f"脚本里少了 {want}")
    if sc.index("chparam") > sc.index("hierarchy"):
        bad.append("chparam 排到了 hierarchy 后面，那时参数已经定死了")
    if sc.index("proc") > sc.index("write_verilog"):
        bad.append("proc 排到了写出之后")
    # 只 hierarchy 不 proc，yosys 自己会警告「进程不一定映射得回 always 块」
    if "proc" not in sc:
        bad.append("没有 proc")

    # 宏要跟着一起给：picorv32 的 rvfi 端口在 `RISCV_FORMAL` 里，不给宏它们
    # 压根不存在——而「端口少了一组」不会报错，只会在接线时变成对不上的名字
    sc = script(["a.v"], "top", {}, pathlib.Path("o.v"), ("RISCV_FORMAL", "SYNTHESIS"))
    if "-DRISCV_FORMAL" not in sc or "-DSYNTHESIS" not in sc:
        bad.append(f"脚本里没给宏：{sc[:80]}")
    if "read_verilog-D" in sc:
        bad.append("宏与 read_verilog 之间少了空格")

    # 矩阵点的标签是把旋钮名串起来的。picorv32 有 25 个旋钮，全开那一点的标签
    # 三百多字符，直接当文件名会撞上 255 字节的上限——而 iverilog 报出来的是
    # 「文件名过长」，看着像上游的核有问题
    long = "".join(f"Knob{i}On" for i in range(40))
    if len(_short(long)) > 48:
        bad.append(f"长标签没截短：{len(_short(long))} 字符")
    if _short("Default") != "Default":
        bad.append("短标签不该被动")
    if _short(long) == _short(long + "x"):
        bad.append("截短之后两个不同的点撞名了")

    # 旋钮名由上游参数名机械推出来，不需要一张要人维护的对照表。两种上游命名都有：
    # picorv32 全大写加下划线，ibex 本来就是驼峰。一律先小写会把后者毁掉
    for src, want in (("ENABLE_MUL", "enableMul"), ("PROGADDR_RESET", "progaddrReset"),
                      ("BusSizeECC", "busSizeECC"), ("RV32E", "rv32E"),
                      ("MHPMCounterNum", "mhpmCounterNum"), ("ICache", "iCache"),
                      ("PMPEnable", "pmpEnable")):
        if _camel(src) != want:
            bad.append(f"{src} 推成了 {_camel(src)}，应是 {want}")

    # 参数投影：布尔按 1/0，字面值照用
    got = bake(PKG, {"enableMul": val(True), "progaddrReset": val(0x10000)})
    if got != {"ENABLE_MUL": 1, "PROGADDR_RESET": 65536, "FIFO": 8}:
        bad.append(f"投影错了：{got}")
    try:
        bake(PKG, {"enableMul": val(True)})
        bad.append("旋钮没解出取值时没报错")
    except Bad:
        pass

    for line in bad:
        print(f"✘ {line}")
    if not bad:
        print(f"✔ {len(rows)} 样工具各有用途；展开脚本的次序对；长标签截得住；参数投影对，漏一个就报")
    return 1 if bad else 0


def test_tools():
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
