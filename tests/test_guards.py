"""条件守卫：一个字段的取值域随别的字段变，像表单那样。

`constraints` 是「不满足就报错」，守卫是「这些取值根本不提供」——两件事。上游有
一组互锁字段（cva6 的浮点那组就是），逐个翻会生成编不过的 RTL；矩阵跑了再红，等于
让每个用它的人去读一遍上游文档。

**判据不自己写，交给 kconfiglib**：守卫投影成 Kconfig 的 `depends on` / `range … if`，
再用 kconfiglib 把生成的 Kconfig 读回来，问它「这个条件下这个符号还可选吗」。它认得
`depends on` 的传递与 choice 的语义，自己判只会在第一层就翻车。

反例：把守卫的 when 改成不会成立的条件，只有「条件成立时该符号不可选」这一条会红。

`uv run pytest tests/test_guards.py`
"""
import pathlib
import tempfile

import kconfiglib
import yaml

from xirang_core import matrix
from xirang_core.manifest import Bad, Pkg

BASE = {
    "name": "t", "version": "0.1.0", "spec": "0.1", "kind": "ip", "lang": "bsv",
    "contract": {"version": 1, "ctrl": {"shape": "flat", "aw": 8, "dw": 32}},
    "params": {"flen": {"default": 64, "range": [0, 64]}},
    "features": {"rvf": {"default": True}, "rvd": {"default": True}},
    "guards": [{"when": {"rvf": False}, "narrow": {"rvd": [False]},
                "why": "没有单精度就谈不上双精度，FLen 会算成负数"},
               {"when": {"rvd": False}, "narrow": {"flen": [0, 32]},
                "why": "只有单精度时浮点位宽最多 32"}],
}


def mk(root: pathlib.Path, ip=None) -> Pkg:
    (root / "hwsrc").mkdir(parents=True, exist_ok=True)
    (root / "hwsrc/T.bsv").write_text("package T; endpackage", encoding="utf-8")
    (root / "ip.yaml").write_text(yaml.safe_dump(ip or BASE, allow_unicode=True), encoding="utf-8")
    return Pkg(root)


def visible(txt: str, sym: str, setting: dict) -> bool:
    """在这组取值下，kconfiglib 认为这个符号还可选吗。"""
    with tempfile.TemporaryDirectory() as d:
        kf = pathlib.Path(d) / "Kconfig"
        kf.write_text(txt, encoding="utf-8")
        kc = kconfiglib.Kconfig(str(kf), warn=False)
        for k, v in setting.items():
            kc.syms[k].set_value(v)
        return kc.syms[sym].visibility > 0


def active_range(txt: str, sym: str, setting: dict) -> tuple[int, int] | None:
    """在这组取值下，kconfiglib 自己算出来的区间。区间守卫不让字段消失，
    它让字段的有效区间跟着变——正是表单那个行为。"""
    with tempfile.TemporaryDirectory() as d:
        kf = pathlib.Path(d) / "Kconfig"
        kf.write_text(txt, encoding="utf-8")
        kc = kconfiglib.Kconfig(str(kf), warn=False)
        for k, v in setting.items():
            kc.syms[k].set_value(v)
        for low, high, cond in kc.syms[sym].ranges:
            if kconfiglib.expr_value(cond):
                return int(low.str_value), int(high.str_value)
    return None


def kconfig_of(pkg: Pkg) -> str:
    from xirang_core.resolve import resolve
    from xirang_out.export import to_kconfig
    res = resolve(pkg.name, [pkg.root.parent])
    return to_kconfig(res, {pkg.name: pkg})


def main() -> int:
    bad: list[str] = []
    with tempfile.TemporaryDirectory() as d:
        root = pathlib.Path(d) / "t"
        pkg = mk(root)

        # 一、矩阵先修正联动字段，而不是把这个点整个丢掉。换了国家重填省份，
        # 不是关掉表单——为一个联动字段丢掉 rvf=n 那条腿等于少测半个配置空间
        pts = matrix.points(pkg)
        for name, ov, _ in pts:
            full = {"rvf": True, "rvd": True, "flen": 64} | ov
            if full["rvf"] is False and full["rvd"] is True:
                bad.append(f"矩阵仍在提供被守卫排除的点 {name}")
        if not any(n == "RvfOff" for n, _, _ in pts):
            bad.append("rvf 那条腿被整条丢掉了，本该修正 rvd 之后留下")
        fix = dict(matrix.adjusted(pkg)).get("RvfOff", {})
        # 联动是会串的：rvd 关掉之后 flen 的域跟着收窄，默认的 64 也得跟着挪
        if fix != {"rvd": False, "flen": 32}:
            bad.append(f"联动没一路修到底：{matrix.adjusted(pkg)}")

        # 二、修不动就不提供：全展开时每个字段都是本点要试的，替谁改值都没意义
        fl = mk(pathlib.Path(d) / "f", {**BASE, "test": {"matrix": "full"}})
        why = [w for _, k, w in matrix.withheld(fl) if k == "rvd"]
        if not why or "双精度" not in why[0]:
            bad.append(f"全展开时该挡的没挡，或没说清理由：{matrix.withheld(fl)}")

        # 三、显式指定被排除的值，当场报错并带上 why
        from xirang_core.resolve import resolve
        try:
            resolve(pkg.name, [pkg.root.parent], cli={"rvf": False, "rvd": True})
            bad.append("显式踩守卫没报错")
        except Bad as e:
            if "双精度" not in str(e):
                bad.append(f"报错没引用 why：{e}")

        # 四、判据交给 kconfiglib：条件成立时那个符号不可选
        txt = kconfig_of(pkg)
        if visible(txt, "T_RVD", {"T_RVF": 0}):
            bad.append("rvf=n 时 kconfiglib 仍认为 rvd 可选")
        if not visible(txt, "T_RVD", {"T_RVF": 2}):
            bad.append("rvf=y 时 rvd 反倒不可选了，守卫的条件写反了")
        if active_range(txt, "T_FLEN", {"T_RVD": 0}) != (0, 32):
            bad.append(f"rvd=n 时 flen 的有效区间没跟着收窄："
                       f"{active_range(txt, 'T_FLEN', {'T_RVD': 0})}")
        if active_range(txt, "T_FLEN", {"T_RVD": 2}) != (0, 64):
            bad.append("rvd=y 时 flen 的区间被连累了")

        # 五、清单自己要站得住
        for ip, want in [
            ({**BASE, "guards": [{"when": {"rvf": False},
                                  "narrow": {"rvd": [False]}}]}, "why"),
            ({**BASE, "guards": [{"when": {"nope": False},
                                  "narrow": {"rvd": [False]}, "why": "x"}]}, "不存在"),
            ({**BASE, "guards": [{"when": {"rvf": False},
                                  "narrow": {"rvd": [7]}, "why": "x"}]}, "取值域之外"),
        ]:
            try:
                mk(pathlib.Path(d) / "u", ip).guards()
                bad.append(f"清单里 {want} 那一条没被拦住")
            except Bad as e:
                if want not in str(e):
                    bad.append(f"拦住了但话没说对：{e}")

        # 反例：守卫的条件永不成立，第三组判据必须红
        mut = {**BASE, "guards": [{**BASE["guards"][0], "when": {"rvf": True}}]}
        if not visible(kconfig_of(mk(pathlib.Path(d) / "m", mut)), "T_RVD",
                       {"T_RVF": 0}):
            bad.append("反例没生效：改掉 when 之后 rvd 仍然不可选")

    for line in bad:
        print(f"✘ {line}")
    if not bad:
        print("✔ 守卫收窄取值域、矩阵不提供、显式指定报错，"
              "且 kconfiglib 认这套 depends on")
    return 1 if bad else 0


def test_guards():
    assert main() == 0


if __name__ == "__main__":
    raise SystemExit(main())
