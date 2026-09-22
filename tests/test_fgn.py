"""接别人的 RTL：归组、判类型、认字符串，三件事都不能靠猜。

接第二个、第三个上游时才看得见的问题，都在这里立judge：

- **按取值猜类型是错的**：`parameter RESET_PC = 32'd0` 的值是 0，看着像开关，
  其实是 32 位地址。要按**声明的位宽**判。
- **方向前缀会把归组带偏**：SERV 的线叫 `o_dbus_adr` 与 `i_dbus_ack`，按 `o_`／`i_`
  归组会把不相干的凑一堆；剥掉之后真正成组的是 `dbus`。
- **Verilog 没有字符串类型**：`RESET_STRATEGY = "MINI"` 打包成整数 1296649801，
  展开之后看不出本来是字符串。把它当整数投影回去，模块里的比较永远不等。

`uv run python tests/test_fgn.py`
"""
import sys

from xirang_back.sv import Param, _num
from xirang_gen.foreign import _groups, _stem


def main() -> int:
    bad: list[str] = []

    for src, want in (("o_dbus_adr", "dbus_adr"), ("i_dbus_ack", "dbus_ack"),
                      ("io_pad", "pad"), ("trap", "trap"), ("mem_axi_awaddr", "mem_axi_awaddr")):
        if _stem(src) != want:
            bad.append(f"{src} 剥成了 {_stem(src)}，应是 {want}")

    # SERV 那一组：按方向前缀归会凑成 i_/o_ 两堆，按词根归才是 dbus/ibus
    serv = {n: None for n in (
        "o_dbus_adr", "o_dbus_cyc", "o_dbus_dat", "o_dbus_sel", "o_dbus_we",
        "i_dbus_ack", "i_dbus_rdt",
        "o_ibus_adr", "o_ibus_cyc", "i_ibus_ack", "i_ibus_rdt",
        "i_timer_irq", "clk", "i_rst")}
    groups, loose = _groups(serv, {"clk", "i_rst"})
    if set(groups) != {"dbus_", "ibus_"}:
        bad.append(f"归出来的是 {sorted(groups)}，应是 dbus_ 与 ibus_")
    if loose != ["i_timer_irq"]:
        bad.append(f"零散的应只有 i_timer_irq，得到 {loose}")

    # 两根凑不成一组：那多半是巧合
    tiny = {n: None for n in ("a_x", "a_y", "b_z")}
    g2, l2 = _groups(tiny, set())
    if g2:
        bad.append(f"两根就归了组：{g2}")
    if len(l2) != 3:
        bad.append(f"三根都该算零散：{l2}")

    # 枚举参数只能用档位名覆盖。传整数 slang 会把它置成 <unset>，而 <unset> 不报错——
    # 配置静默没生效，后端量的是默认那一档
    p = Param("RV32M", 2, 32,
              (("RV32MNone", 0), ("RV32MSlow", 1), ("RV32MFast", 2)))
    cur = next((a for a, b in p.enum if b == p.value), None)
    if cur != "RV32MFast":
        bad.append(f"枚举当前档位认错了：{cur}")
    if not p.enum or p.width != 32:
        bad.append("Param 少了枚举或位宽")

    # slang 给的是 ConstantValue，int() 直接用会抛。吞掉这个异常，整条枚举通路
    # 看着像没实现——枚举列表会静默变成空
    for src, want in (("2", 2), ("32'd65536", 65536), ("1'b1", 1), ("32'h10", 16)):
        if _num(src) != want:
            bad.append(f"{src} 解成了 {_num(src)}，应是 {want}")
    if _num("MINI") != "MINI":
        bad.append("解不成数的应原样留着")

    for line in bad:
        print(f"✘ {line}")
    if not bad:
        print("✔ 方向前缀剥得掉，归组按词根，两根不成组；枚举档位与常量解析都对")
    return 1 if bad else 0


def test_fgn():
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
