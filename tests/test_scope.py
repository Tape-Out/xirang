"""芯片作用域：旋钮自己声明继不继承，值沿实例树向下扩散。

照 CSS 的模型：不是所有属性都继承，而**继不继承是属性自己的性质**。`xlen` 一颗
芯片只该有一个说法；`aw` 则是同一个 IP 一处八位一处三十二位各配各的。全局变量
若是隐式的（BitBake 那种），「这个值谁定的」就答不出来，而那正是我们的卖点。

就近生效：祖先扩散下来的输给后代自己写的，赢过包默认。`lock` 是 `!important`。

反例在最后一条：把 `scope: chip` 去掉，扩散那几条判据必须全红。

`uv run pytest tests/test_scope.py`
"""
import pathlib
import tempfile

import yaml

from xirang_core.manifest import Bad, Pkg
from xirang_core.resolve import resolve

BASE = {"version": "0.1.0", "spec": "0.1", "kind": "ip", "lang": "bsv",
        "contract": {"version": 1,
                     "ctrl": {"shape": "flat", "aw": 8, "dw": 32}}}


def leaf(root: pathlib.Path, name: str, chip=True) -> None:
    (root / name / "hwsrc").mkdir(parents=True, exist_ok=True)
    (root / name / "hwsrc/L.bsv").write_text("package L; endpackage",
                                             encoding="utf-8")
    xlen = {"type": "choice", "values": [32, 64], "default": 64}
    if chip:
        xlen["scope"] = "chip"
    (root / name / "ip.yaml").write_text(yaml.safe_dump({
        **BASE, "name": name,
        "params": {"xlen": xlen,
                   "aw": {"type": "choice", "values": [8, 32], "default": 8}},
    }, allow_unicode=True), encoding="utf-8")


def asm(root: pathlib.Path, name: str, insts: list, chip=None, extra=None) -> None:
    (root / name / "hwsrc").mkdir(parents=True, exist_ok=True)
    (root / name / "hwsrc/A.bsv").write_text("package A; endpackage",
                                             encoding="utf-8")
    ip = {**BASE, "name": name, "instances": insts}
    if chip:
        ip["chip"] = chip
    if extra:
        ip.update(extra)
    (root / name / "ip.yaml").write_text(
        yaml.safe_dump(ip, allow_unicode=True), encoding="utf-8")


def vals(d: pathlib.Path, top: str, **kw):
    r = resolve(top, [d], **kw)
    return {i.name: i.values for i in r.walk_flat()} if hasattr(r, "walk_flat") \
        else {i.name: i.values for _, i in r.walk()}


def main() -> int:
    bad: list[str] = []
    with tempfile.TemporaryDirectory() as td:
        d = pathlib.Path(td)
        leaf(d, "cpu")
        leaf(d, "dev")
        asm(d, "soc", [{"name": "cpu0", "of": "cpu", "addr": 0},
                       {"name": "dev0", "of": "dev", "addr": 4096}],
            chip={"xlen": 32})

        # 一、芯片级的扩散到每一个后代
        v = vals(d, "soc")
        got = {n: v[n]["xlen"].value for n in ("cpu0", "dev0")}
        if got != {"cpu0": 32, "dev0": 32}:
            bad.append(f"芯片级没扩散下去：{got}")
        if v["cpu0"]["xlen"].winner.layer != "inherit":
            bad.append(f"扩散下来的值来历不对：{v['cpu0']['xlen'].winner.layer}")

        # 二、实例级的不扩散，各配各的
        asm(d, "soc2", [{"name": "cpu0", "of": "cpu", "addr": 0,
                         "with": {"aw": 32}},
                        {"name": "dev0", "of": "dev", "addr": 4096}],
            chip={"xlen": 32})
        v = vals(d, "soc2")
        if (v["cpu0"]["aw"].value, v["dev0"]["aw"].value) != (32, 8):
            bad.append("实例级的旋钮也跟着扩散了——那是 BitBake 的坑")

        # 三、就近生效：后代自己写的赢过扩散，扩散赢过包默认
        asm(d, "soc3", [{"name": "cpu0", "of": "cpu", "addr": 0,
                         "with": {"xlen": 64}},
                        {"name": "dev0", "of": "dev", "addr": 4096}],
            chip={"xlen": 32})
        v = vals(d, "soc3")
        if (v["cpu0"]["xlen"].value, v["dev0"]["xlen"].value) != (64, 32):
            bad.append(f"就近生效不对：{v['cpu0']['xlen'].value} "
                       f"{v['dev0']['xlen'].value}")

        # 四、套娃：中间层改了，孙子跟中间层，不跟顶层
        leaf(d, "gpu")
        asm(d, "tile", [{"name": "gpu0", "of": "gpu", "addr": 0}])
        (d / "tile/ip.yaml").write_text(yaml.safe_dump({
            **BASE, "name": "tile",
            "params": {"xlen": {"type": "choice", "values": [32, 64],
                                "default": 64, "scope": "chip"}},
            "instances": [{"name": "gpu0", "of": "gpu", "addr": 0}],
        }, allow_unicode=True), encoding="utf-8")
        asm(d, "big", [{"name": "t0", "of": "tile", "addr": 0,
                        "with": {"xlen": 64}},
                       {"name": "dev0", "of": "dev", "addr": 65536}],
            chip={"xlen": 32})
        v = vals(d, "big")
        if v["gpu0"]["xlen"].value != 64:
            bad.append(f"套娃：孙子没跟中间层，拿到 {v['gpu0']['xlen'].value}")
        if v["dev0"]["xlen"].value != 32:
            bad.append("套娃：中间层的改动漏到了它的兄弟身上")

        # 五、命令行给芯片级定值
        v = vals(d, "soc", cli={"chip.xlen": 64})
        if v["cpu0"]["xlen"].value != 64:
            bad.append("命令行的 chip.xlen 没生效")

        # 六、lock 是 !important：就近覆盖当场报错并点名
        leaf(d, "lcpu")
        y = yaml.safe_load((d / "lcpu/ip.yaml").read_text(encoding="utf-8"))
        y["params"]["xlen"]["lock"] = True
        (d / "lcpu/ip.yaml").write_text(yaml.safe_dump(y, allow_unicode=True),
                                        encoding="utf-8")
        asm(d, "soc4", [{"name": "c0", "of": "lcpu", "addr": 0,
                         "with": {"xlen": 64}}], chip={"xlen": 32})
        try:
            vals(d, "soc4")
            bad.append("锁住的芯片级旋钮被就近改掉了")
        except Bad as e:
            if "锁住" not in str(e):
                bad.append(f"报错没说清是锁：{e}")

        # 七、清单自己要站得住
        y["params"]["aw"]["lock"] = True
        y["params"]["xlen"].pop("lock")
        (d / "lcpu/ip.yaml").write_text(yaml.safe_dump(y, allow_unicode=True),
                                        encoding="utf-8")
        try:
            Pkg(d / "lcpu").inherits()
            bad.append("本地旋钮写了 lock 却没被拦住")
        except Bad as e:
            if "没有可锁的对象" not in str(e):
                bad.append(f"拦住了但话没说对：{e}")
        try:
            Pkg(d / "cpu").chip() if False else None
            y2 = yaml.safe_load((d / "dev/ip.yaml").read_text(encoding="utf-8"))
            y2["chip"] = {"xlen": 32}
            (d / "dev/ip.yaml").write_text(yaml.safe_dump(y2, allow_unicode=True),
                                           encoding="utf-8")
            Pkg(d / "dev").chip()
            bad.append("叶子包也能声明 chip——全局就此没了出处")
        except Bad as e:
            if "只有装配" not in str(e):
                bad.append(f"拦住了但话没说对：{e}")

        # 八、menuconfig 里也要看得见继承：父值自动取到，自己也改得了
        import kconfiglib
        from xirang_out.export import to_kconfig

        def kc(top, **kw):
            r = resolve(top, [d], **kw)
            txt = to_kconfig(r, {n: Pkg(d / n) for n in
                                 {i.of for _, i in r.walk()} | {top}})
            f = d / "K"
            f.write_text(txt, encoding="utf-8")
            return kconfiglib.Kconfig(str(f), warn=False)

        k = kc("soc")
        if not k.syms["CPU0_XLEN_32"].tri_value:
            bad.append("kconfig 里后代没取到父值")
        k.syms["CHIP_XLEN_64"].set_value(2)
        if not k.syms["CPU0_XLEN_64"].tri_value:
            bad.append("改了顶上的芯片级档位，后代没跟着变")
        k.syms["CPU0_XLEN_32"].set_value(2)
        if not k.syms["CPU0_XLEN_32"].tri_value:
            bad.append("后代自己改了之后没有就近生效")
        if not k.syms["DEV0_XLEN_64"].tri_value:
            bad.append("后代改自己连累了兄弟")
        if k.syms["CPU0_XLEN_32"].visibility == 0:
            bad.append("没锁的芯片级旋钮在 menuconfig 里反倒不可编辑")

        # 锁住的那个不给提示语：看得见值，改不动
        asm(d, "soc6", [{"name": "c0", "of": "lcpu", "addr": 0}],
            chip={"xlen": 32})
        y["params"]["aw"].pop("lock")
        y["params"]["xlen"]["lock"] = True
        (d / "lcpu/ip.yaml").write_text(yaml.safe_dump(y, allow_unicode=True),
                                        encoding="utf-8")
        k = kc("soc6")
        if not k.syms["C0_XLEN_32"].tri_value:
            bad.append("锁住的符号没取到父值")

        # 反例：把 scope: chip 去掉，扩散必须失效
        leaf(d, "ncpu", chip=False)
        asm(d, "soc5", [{"name": "n0", "of": "ncpu", "addr": 0}],
            chip={"xlen": 32})
        if vals(d, "soc5")["n0"]["xlen"].value != 64:
            bad.append("反例没生效：去掉 scope: chip 之后还在扩散")

    for line in bad:
        print("\u2718 " + line)
    if not bad:
        print("\u2714 芯片级扩散、实例级不扩散、就近生效、套娃跟中间层、"
              "lock 报错并点名，且 kconfiglib 认这套继承")
    return 1 if bad else 0


def test_scope():
    assert main() == 0


if __name__ == "__main__":
    raise SystemExit(main())
