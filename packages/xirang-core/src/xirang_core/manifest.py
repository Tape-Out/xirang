"""读 ip.yaml 与 regmap.yaml，带行号。行号是 computed 面板第二问的答案来源。"""
import pathlib

import yaml


class Bad(Exception):
    pass


class Loc(dict):
    """记住每个顶层键在第几行的 dict。面板要报 '文件:行'。"""
    lines: dict


class _LineLoader(yaml.SafeLoader):
    pass


def _mapping(loader, node):
    m = loader.construct_mapping(node, deep=True)
    out = Loc(m)
    out.lines = {}
    for k, _v in node.value:
        out.lines[k.value] = k.start_mark.line + 1
    return out


_LineLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def load(path: pathlib.Path) -> Loc:
    if not path.exists():
        raise Bad(f"找不到 {path}")
    d = yaml.load(path.read_text(encoding="utf-8"), _LineLoader)
    if not isinstance(d, dict):
        raise Bad(f"{path} 顶层不是映射")
    d.setdefault("__path__", str(path))
    return d


def where(node, key: str, path: str) -> str:
    """某个键的来源位置，给面板用。"""
    ln = getattr(node, "lines", {}).get(key)
    return f"{path}:{ln}" if ln else path


CTRL_SHAPES = {"flat", "server", "none"}

# 规范对源语言是开放的，实现目前只有 BSV 与 BH 两个前端。写成序列表示混用。
LANGS = {"bsv", "bh", "verilog", "sv", "vhdl", "chisel", "spinal"}

# 每种语言在树上长什么样。有了它，`lang` 才不只是一句自述。
SRC_EXT = {"bsv": ".bsv", "bh": ".bs", "verilog": ".v", "sv": ".sv",
           "vhdl": ".vhd", "chisel": ".scala", "spinal": ".scala"}

# 顶层键的白名单。写错一个键就被默默忽略，比报错糟得多——
# 「area」写成「areas」，价目表整个失效而没人知道。
TOP_KEYS = {"name", "version", "spec", "kind", "lang", "identity", "contract",
            "params", "features", "constraints", "area", "emit", "deps",
            "bus", "instances", "connect", "pipe", "test", "__path__"}


class Pkg:
    """一个包的清单。有 instances 就是装配，没有就是叶子 IP。"""

    def __init__(self, root: pathlib.Path):
        self.root = root
        self.ip = load(root / "ip.yaml")
        self.name = self.ip.get("name") or root.name
        self.path = str(root / "ip.yaml")
        rm = root / "regmap.yaml"
        self.regmap = load(rm) if rm.exists() else None
        self._check()

    @property
    def kind(self) -> str:
        """ip 会被例化、有契约与面积；library 只贡献 BSV 源。"""
        return self.ip.get("kind", "ip")

    @property
    def is_library(self) -> bool:
        return self.kind == "library"

    @property
    def is_assembly(self) -> bool:
        return bool(self.ip.get("instances"))

    def _check_lang(self, langs: list[str]):
        """声明哪种语言就得真的写哪种。

        `lang: bh` 配一棵全是 `.bsv` 的树，此前一路放行——**认识的键被默默忽略，
        比不认识的键更难查**：读清单的人会照着去找 `.bs`，找不到才知道被骗。
        """
        src = self.root / "bsv"
        if not src.is_dir():
            return
        want = {SRC_EXT[x] for x in langs if x in SRC_EXT}
        have = {f.suffix for f in src.iterdir() if f.is_file()} & set(SRC_EXT.values())
        if not have:
            return
        if extra := have - want:
            raise Bad(f"{self.path}: lang 写的是 {langs}，树上却有 {sorted(extra)} "
                      f"的源码——要么改 lang，要么改源码")
        if miss := want - have:
            raise Bad(f"{self.path}: lang 写的是 {langs}，树上一个 {sorted(miss)} "
                      f"的源码都没有")

    def _check(self):
        ip = self.ip
        unknown = set(ip) - TOP_KEYS
        if unknown:
            raise Bad(f"{self.path}: 不认识的顶层键 {sorted(unknown)}")
        lang = ip.get("lang", "bsv")
        langs = lang if isinstance(lang, list) else [lang]
        bad = set(langs) - LANGS
        if bad:
            raise Bad(f"{self.path}: 不认识的 lang {sorted(bad)}")
        self._check_lang(langs)
        for k in ("name", "version", "spec"):
            if k not in ip:
                raise Bad(f"{self.path} 缺 {k}")
        if self.kind not in ("ip", "library"):
            raise Bad(f"{self.path}: kind={self.kind} 只能是 ip 或 library")
        if self.is_library:
            # 库包不进地址图，所以这些字段没有意义，写了反而误导
            for k in ("contract", "params", "features", "instances"):
                if k in ip:
                    raise Bad(f"{self.path}: 库包不该有 {k}——它不进地址图")
            if self.regmap:
                raise Bad(f"{self.path}: 库包不该有 regmap.yaml")
            # 但库里的模块确实会被例化（总线绑定器每个总线端口一个），
            # 那笔面积就该记在实现它的包上。库没有旋钮，所以只能是定值。
            a = ip.get("area") or {}
            if a and set(a.get("base") or {}) - {"fixed"}:
                raise Bad(f"{self.path}: 库包的 area 只能是定值——它没有旋钮可依")
            if set(a) - {"base", "model", "error", "corner", "assembly",
                         "probe"}:
                raise Bad(f"{self.path}: 库包的 area 有不认识的键")
            return
        feats = ip.get("features", {}) or {}
        params = ip.get("params", {}) or {}
        for fn, f in feats.items():
            t = f.get("type", "bool")
            if t not in ("bool", "choice"):
                raise Bad(f"{self.path}: feature {fn} 的 type={t} 不支持")
            if t == "choice":
                if not f.get("values"):
                    raise Bad(f"{self.path}: choice {fn} 没有 values")
                if f.get("default") not in f["values"]:
                    raise Bad(f"{self.path}: choice {fn} 的默认值不在 values 里")
            for dep in f.get("depends", []) or []:
                if dep not in feats:
                    raise Bad(f"{self.path}: {fn} 依赖了不存在的 {dep}")
        for pn, p in params.items():
            r = p.get("range")
            if r and not (r[0] <= p.get("default", r[0]) <= r[1]):
                raise Bad(f"{self.path}: param {pn} 的默认值超出 range")
        # area 的键也进白名单。顶层键早就查了（D121），里面这一层一直没查——
        # 而写错一个键的后果跟写错 area 一样：整条价目静默失效。
        a = ip.get("area") or {}
        unknown = set(a) - {"base", "params", "margin", "model", "error",
                            "measured", "corner", "assembly"}
        if unknown:
            raise Bad(f"{self.path}: area 有不认识的键 {sorted(unknown)}")
        for pn in (a.get("params") or {}):
            if pn not in params:
                raise Bad(f"{self.path}: area.params 提到清单里没有的旋钮 {pn}")

        # regmap 里出现的 feature 必须在 ip.yaml 声明过
        if self.regmap:
            for reg in self.regmap.get("regs", []) or []:
                fn = reg.get("feature")
                if fn and fn not in feats:
                    raise Bad(f"regmap 的 {reg['name']} 挂了未声明的 feature {fn}")
            c = self.regmap.get("contract", {})
            ic = (ip.get("contract") or {}).get("ctrl", {})
            for k in ("aw", "dw"):
                if k in c and k in ic and c[k] != ic[k]:
                    raise Bad(f"regmap 与 ip.yaml 的 contract.{k} 不一致：{c[k]} vs {ic[k]}")

    def bsv_emit(self) -> dict:
        """kind: bsv 的 emit 段。装配器要靠它知道 BSV 侧叫什么名字。"""
        for e in self.ip.get("emit", []) or []:
            if e.get("kind") == "bsv":
                missing = [k for k in ("package", "module", "config_type", "interface")
                           if k not in e]
                if missing:
                    raise Bad(f"{self.path}: emit 的 bsv 段缺 {missing}——"
                              f"装配器要靠它生成 import 与例化")
                extra = set(e) - {"kind", "package", "module", "config_type",
                                  "interface", "ctrl", "pins",
                                  "ctrl_slow", "slow_when"}
                if extra:
                    raise Bad(f"{self.path}: emit 的 bsv 段有不认识的键 "
                              f"{sorted(extra)}——写错的键会被默默忽略")
                # 会停顿的控制口要说清「哪个特性开着时用它」，否则没法选
                if ("ctrl_slow" in e) != ("slow_when" in e):
                    raise Bad(f"{self.path}: emit 的 ctrl_slow 与 slow_when 要成对出现"
                              f"——只给一个，选口的条件就没了")
                if "slow_when" in e and e["slow_when"] not in (self.ip.get("features") or {}):
                    raise Bad(f"{self.path}: emit.slow_when 指的 {e['slow_when']!r} "
                              f"不是这个包的特性")
                for s in e.get("pins") or []:
                    miss = [k for k in ("name", "type") if k not in s]
                    if miss:
                        raise Bad(f"{self.path}: emit.pins 的某一项缺 {miss}")
                return e
        raise Bad(f"{self.path}: 没有 kind: bsv 的 emit 段")

    def knobs(self) -> dict[str, dict]:
        """参数与特性合成一张表，层叠与求解都对着它做。"""
        out = {}
        for n, p in (self.ip.get("params") or {}).items():
            out[n] = {**p, "kind": "param", "type": p.get("type", "int")}
        for n, f in (self.ip.get("features") or {}).items():
            out[n] = {**f, "kind": "feature", "type": f.get("type", "bool")}
        return out
