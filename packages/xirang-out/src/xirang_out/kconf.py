"""Kconfig 的回程：`menuconfig` 改完的 `.config` 读回模型。

去程在 `export.py`。名字**不可逆解**——`_sym` 用下划线拼接、把连字符也换成下划线、
还全大写，`SUB_GPIOA_NUMPINS` 切不出唯一解，`numPins` 也恢复不了驼峰。所以回来的
时候不解析名字，而是把去程再走一遍、边走边查 `symtab` 那张表。

解析 `.config` 交给 `kconfiglib`——它认得 `depends on`、range 与 choice 的语义，
自己写一个只会在第一个 `depends` 上翻车。
"""
import pathlib
import tempfile

from xirang_core.manifest import Bad
from xirang_core.model import Candidate, Resolved

from .export import symtab, to_kconfig, walk


def read(dotconfig, res: Resolved, pkgs) -> Resolved:
    """把 `.config` 里的取值贴回 `res`，原地改，并把来源记成那个文件。"""
    try:
        import kconfiglib
    except ImportError as e:
        raise Bad("读 .config 要 kconfiglib——把它加进 xirang-out 的依赖") from e

    dotconfig = pathlib.Path(dotconfig).resolve()
    if not dotconfig.is_file():
        raise Bad(f"找不到 {dotconfig}")

    picked: dict[tuple[tuple[str, ...], str], object] = {}
    with tempfile.TemporaryDirectory() as d:
        kf = pathlib.Path(d) / "Kconfig"
        kf.write_text(to_kconfig(res, pkgs), encoding="utf-8")
        kc = kconfiglib.Kconfig(str(kf), warn=False)
        kc.load_config(str(dotconfig))
        for sym, (path, knob, val) in symtab(res, pkgs).items():
            s = kc.syms.get(sym)
            if s is None:
                continue
            match s.type:
                case kconfiglib.BOOL if val:
                    if s.tri_value:          # choice 选中的那一档
                        picked[path, knob] = val
                case kconfiglib.BOOL:
                    picked[path, knob] = bool(s.tri_value)
                case kconfiglib.INT:
                    picked[path, knob] = int(s.str_value)

    seen = {path: inst for path, inst, _, _ in walk(res, pkgs)}
    for (path, knob), v in picked.items():
        inst = seen.get(path)
        if inst is None or knob not in inst.values:
            continue
        cur = inst.values[knob]
        if cur.value == v:
            continue
        cur.value = v
        cur.winner = Candidate("cli", v, str(dotconfig))
    return res
