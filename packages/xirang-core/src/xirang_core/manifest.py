"""读 ip.yaml 与 regmap.yaml，带行号。行号是 computed 面板第二问的答案来源。"""
import pathlib

import yaml

from xirang_core import diag


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

# 源码与测试住在哪：键名与目录同名，缺省就是那个同名目录（`hwsrc: [hwsrc]`）。
# 不写死目录名，是因为规范对语言开放、黑盒的源码可能在别处、三个操作系统还可能各要各的文件——
# 三件事落在同一个机制上，比散着写死三处强。
DIR_KEYS = ("hwsrc", "swsrc", "htest", "stest")

# 生成产物在 build 下的目录名。与包里的源码目录不是一回事：那边由用户声明，
# 这边不归用户管——但名字要与内容相称，且只此一处，将来要改只改这里。
GEN_HW = "hwsrc"
GEN_SW = "sw"
GEN_TEST = "htest"

# 构建目标认哪几种驱动。名字说的是「怎么构建」，不是「构建出什么」。
DRIVERS = {"regs", "flat", "bsv", "assembly", "library", "foreign", "none"}

# 黑盒声明认的键。黑盒不解析源码，这份声明就是它的全部形状。
FOREIGN_KEYS = {"kind", "lang", "top", "rtl", "sim", "params", "defines",
                "clock", "reset", "ports", "limits"}
PORT_KEYS = {"endpoint", "kind", "role", "profile", "prefix", "type", "map"}
EP_KINDS = {"transaction", "stream", "event", "physical"}

# `when` 认的平台名。不认识的值要报错：写 `when: win` 而被默默忽略，
# 比写错键更难查——那一条会在所有平台上都生效。
PLATS = {"linux", "macos", "windows"}

# 顶层键的白名单。写错一个键就被默默忽略，比报错糟得多——
# 「area」写成「areas」，价目表整个失效而没人知道。
TOP_KEYS = {*DIR_KEYS,
            "name", "version", "spec", "kind", "lang", "identity", "contract",
            "params", "features", "constraints", "area", "emit", "deps",
            "bus", "instances", "connect", "pipe", "test", "diagnostics",
            "targets", "__path__"}


def slow_ctrl(emit: dict, vals) -> bool:
    """这一点走不走会停顿的那个控制口。

    清单校验、顶层生成与装配都要回答这一问，规则只写在这里，免得三处各认各的。
    `slow_when: true` 是给片外存储控制器这类每一笔都要等的 IP：没有同拍答的形态可选，
    不必为它编一个恒开的特性。
    """
    if not emit.get("ctrl_slow"):
        return False
    sw = emit.get("slow_when")
    return sw is True or bool(vals[sw].value)


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

    def dirs(self, key: str, plat: str | None = None) -> list[pathlib.Path]:
        """某一类源码/测试的位置。缺省是与键同名的目录；只返回真实存在的。

        返回顺序照清单写的顺序——搜索路径的先后是有意义的，不排序。
        """
        return [self.root / e for e, _ in self._entries(key, plat)
                if (self.root / e).exists()]

    def dir_why(self, key: str, plat: str | None = None) -> list[tuple[str, str]]:
        """每个位置是怎么来的：`约定` 还是 `ip.yaml 声明`。给 config --why 用。"""
        return self._entries(key, plat)

    def _entries(self, key: str, plat: str | None = None) -> list[tuple[str, str]]:
        import platform as _p
        assert key in DIR_KEYS, key
        cur = plat or {"Linux": "linux", "Darwin": "macos",
                       "Windows": "windows"}.get(_p.system(), "linux")
        raw = self.ip.get(key)
        if raw is None:
            return [(key, "约定")]
        out = []
        for e in raw:
            if isinstance(e, str):
                out.append((e, f"{self.path} 的 {key}"))
                continue
            when = e.get("when")
            if when is not None and when not in PLATS:
                raise Bad(f"{self.path}: {key} 里 when 写的是 {when!r}，"
                          f"只认 {sorted(PLATS)}")
            if when in (None, cur):
                out.append((e["path"], f"{self.path} 的 {key}"
                                       + (f"（{when} 专用）" if when else "")))
        return out

    def _check_dirs(self):
        for k in DIR_KEYS:
            raw = self.ip.get(k)
            if raw is None:
                continue
            if not isinstance(raw, list):
                raise Bad(f"{self.path}: {k} 应是列表")
            for e in raw:
                if isinstance(e, str):
                    continue
                if not isinstance(e, dict) or "path" not in e:
                    raise Bad(f"{self.path}: {k} 的每一条要么是路径，"
                              f"要么是带 path 的表，现在是 {e!r}")
                if extra := set(e) - {"path", "when"}:
                    raise Bad(f"{self.path}: {k} 里不认识的键 {sorted(extra)}")
            # 声明了就得真的在。写了个不存在的路径而被默默跳过，等于这条声明没写
            for d, _ in self._entries(k):
                if not (self.root / d).exists():
                    raise Bad(f"{self.path}: {k} 指向的 {d} 不在树上")

    def _check_lang(self, langs: list[str]):
        """声明哪种语言就得真的写哪种。

        `lang: bh` 配一棵全是 `.bsv` 的树，此前一路放行——**认识的键被默默忽略，
        比不认识的键更难查**：读清单的人会照着去找 `.bs`，找不到才知道被骗。
        """
        srcs = [d for d in self.dirs("hwsrc") if d.is_dir()]
        if not srcs:
            return
        want = {SRC_EXT[x] for x in langs if x in SRC_EXT}
        have = {f.suffix for d in srcs for f in d.rglob("*") if f.is_file()}
        have &= set(SRC_EXT.values())
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
        self._check_dirs()
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
            for dep in f.get("depends", []) or []:
                if dep not in feats:
                    raise Bad(f"{self.path}: {fn} 依赖了不存在的 {dep}")
        for pn, p in params.items():
            if p.get("type", "int") not in ("int", "choice"):
                raise Bad(f"{self.path}: param {pn} 的 type={p['type']} 不支持")
            r = p.get("range")
            if r and not (r[0] <= p.get("default", r[0]) <= r[1]):
                raise Bad(f"{self.path}: param {pn} 的默认值超出 range")
        # 档位旋钮两处都能写：定宽的写 params（变成数值类型参数），开关的写 features
        for kn, k in {**feats, **params}.items():
            if k.get("type") != "choice":
                continue
            if not k.get("values"):
                raise Bad(f"{self.path}: choice {kn} 没有 values")
            if k.get("default") not in k["values"]:
                raise Bad(f"{self.path}: choice {kn} 的默认值不在 values 里")
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

        # 黑盒声明与构建目标没人主动去读就等于没写，所以在这里查
        self.foreign_emit()
        self._check_targets()
        self._check_upstream()

        # 检查号与级别写错了要当场报：写错一个号，那道门禁的覆盖就静默失效
        for code, lv in (ip.get("diagnostics") or {}).items():
            if code not in diag.CHECKS:
                raise Bad(f"{self.path}: diagnostics 里不认识的检查号 {code}")
            if diag.level_of(lv) is None:
                raise Bad(f"{self.path}: {code} 的级别 {lv} 不认识，"
                          f"只有 {list(diag.Level.__members__)}")

        # regmap 里出现的门控旋钮必须在 ip.yaml 声明过。档位旋钮写成
        # {名字: [档...]}，而定宽的档位写在 params，所以两边都认
        if self.regmap:
            known = {**feats, **params}
            for reg in self.regmap.get("regs", []) or []:
                fn = reg.get("feature")
                if isinstance(fn, dict):
                    fn = next(iter(fn), None)
                if fn and fn not in known:
                    raise Bad(f"regmap 的 {reg['name']} 挂了未声明的旋钮 {fn}")
            c = self.regmap.get("contract", {})
            ic = (ip.get("contract") or {}).get("ctrl", {})
            for k in ("aw", "dw"):
                if k in c and k in ic and c[k] != ic[k]:
                    raise Bad(f"regmap 与 ip.yaml 的 contract.{k} 不一致：{c[k]} vs {ic[k]}")

    def upstream_tests(self) -> list[dict]:
        """上游自带的测试台。接别人的核，最有说服力的是它自己的测试还过。

        `params` 是「测试台的参数名 -> 我们的旋钮名」，`fixed` 是写死的值。
        两者分开写，是因为测试台里的被测模块常常**不是**综合顶层那一个：SERV 的
        测试台测的是 `servant`，参数名与 `serv_rf_top` 对不上。混成一个键，
        「这是旋钮名还是字面值」就只能靠猜，猜错了不报错。
        """
        return list((self.ip.get("test") or {}).get("upstream") or [])

    def _check_upstream(self):
        for t in self.upstream_tests():
            unknown = set(t) - {"name", "files", "dut", "params", "fixed",
                                "plusargs", "expect", "timeout", "when"}
            if unknown:
                raise Bad(f"{self.path}: test.upstream 有不认识的键 {sorted(unknown)}")
            for k in ("name", "files", "dut"):
                if not t.get(k):
                    raise Bad(f"{self.path}: test.upstream 的每一项都要写 {k}")
            for f in t["files"]:
                if not (self.root / f).is_file():
                    raise Bad(f"{self.path}: test.upstream {t['name']} "
                              f"列了树上没有的 {f}")

    def targets(self) -> dict[str, dict]:
        """怎么构建这个包。清单没写就按今天的规则推断。

        推断这件事本身没问题，**把推断藏在 CI 的 grep 里才有问题**：`rvdbg` 那种
        没有控制口的调试模块落进「regs」那一档，跑的是它根本没有的寄存器一致性测试。
        写出来之后，工具能答、CI 不必猜，而且写错了当场就报。
        """
        want = self.ip.get("targets")
        if want:
            return {k: dict(v or {}) for k, v in want.items()}
        return self._guess()

    def _guess(self) -> dict[str, dict]:
        """一个包可以有好几个目标：`uart` 既出寄存器组，也出扁平端口顶层。"""
        if self.is_library:
            return {"library": {"driver": "library"}}
        if self.is_assembly:
            return {"assembly": {"driver": "assembly"}}
        kinds = {e.get("kind") for e in self.ip.get("emit", []) or []}
        out: dict[str, dict] = {}
        if self.regmap:
            out["regs"] = {"driver": "regs"}
        if "verilog-flat" in kinds:
            out["flat"] = {"driver": "flat"}
        if "foreign" in kinds:
            out["foreign"] = {"driver": "foreign"}
        # 只出 BSV、既没有寄存器图也不扁平化的那一类：rvdbg 就是
        if not out and "bsv" in kinds:
            out["bsv"] = {"driver": "bsv"}
        return out or {"none": {"driver": "none"}}

    def _check_targets(self):
        want = self.ip.get("targets")
        if not want:
            return
        if not isinstance(want, dict) or not want:
            raise Bad(f"{self.path}: targets 是「名字 -> {{driver: …}}」的表")
        for name, t in want.items():
            t = t or {}
            unknown = set(t) - {"driver"}
            if unknown:
                raise Bad(f"{self.path}: 目标 {name} 有不认识的键 {sorted(unknown)}")
            d = t.get("driver")
            if d not in DRIVERS:
                raise Bad(f"{self.path}: 目标 {name} 的 driver={d} 不认识，"
                          f"只有 {sorted(DRIVERS)}")
            # 声明要对得上树上真有的东西，否则这句声明比猜还糟
            if d == "assembly" and not self.is_assembly:
                raise Bad(f"{self.path}: 目标 {name} 说自己是 assembly，但没有 instances")
            if d == "library" and not self.is_library:
                raise Bad(f"{self.path}: 目标 {name} 说自己是 library，但 kind 不是 library")
            if d == "regs" and not self.regmap:
                raise Bad(f"{self.path}: 目标 {name} 说自己是 regs，但没有 regmap.yaml")
            if d == "bsv" and not any(e.get("kind") == "bsv"
                                      for e in self.ip.get("emit", []) or []):
                raise Bad(f"{self.path}: 目标 {name} 说自己是 bsv，但 emit 里没有 bsv 段")
            if d == "flat" and not any(e.get("kind") == "verilog-flat"
                                       for e in self.ip.get("emit", []) or []):
                raise Bad(f"{self.path}: 目标 {name} 说自己是 flat，"
                          f"但 emit 里没有 verilog-flat 段")
            if d == "foreign" and not any(e.get("kind") == "foreign"
                                          for e in self.ip.get("emit", []) or []):
                raise Bad(f"{self.path}: 目标 {name} 说自己是 foreign，"
                          f"但 emit 里没有 foreign 段")

    def foreign_emit(self) -> dict | None:
        """`kind: foreign` 的 emit 段。我们自己写的包没有这一段，返回 None。

        黑盒不解析源码，所以这份声明就是它的全部形状：对不上的地方只能在这里查出来。
        """
        for e in self.ip.get("emit", []) or []:
            if e.get("kind") != "foreign":
                continue
            unknown = set(e) - FOREIGN_KEYS
            if unknown:
                raise Bad(f"{self.path}: emit 的 foreign 段有不认识的键 {sorted(unknown)}")
            missing = [k for k in ("lang", "top", "rtl", "ports") if k not in e]
            if missing:
                raise Bad(f"{self.path}: emit 的 foreign 段缺 {missing}——"
                          f"少一项就接不上：顶层名与文件给源码闭包，ports 给端口到端点的对应")
            if e["lang"] not in LANGS:
                raise Bad(f"{self.path}: foreign 的 lang={e['lang']} 不认识")
            for k in ("rtl", "sim"):
                fs = e.get(k)
                if fs is None:
                    continue
                if not isinstance(fs, list) or not fs:
                    raise Bad(f"{self.path}: foreign 的 {k} 是非空的文件列表")
                for f in fs:
                    if not (self.root / f).is_file():
                        raise Bad(f"{self.path}: foreign 的 {k} 列了树上没有的 {f}")
            knobs = self.knobs()
            for pn, v in (e.get("params") or {}).items():
                if isinstance(v, str) and v not in knobs:
                    raise Bad(f"{self.path}: foreign 的参数 {pn} 投影到了不存在的旋钮 {v}")
            for k in ("clock", "reset"):
                c = e.get(k)
                if c is not None and "port" not in c:
                    raise Bad(f"{self.path}: foreign 的 {k} 要写 port")
            r = e.get("reset") or {}
            if r.get("active") not in (None, "high", "low"):
                raise Bad(f"{self.path}: foreign 的 reset.active 只能是 high 或 low")
            for pt in e["ports"]:
                bad = set(pt) - PORT_KEYS
                if bad:
                    raise Bad(f"{self.path}: foreign 的 ports 有不认识的键 {sorted(bad)}")
                if "endpoint" not in pt or "kind" not in pt:
                    raise Bad(f"{self.path}: foreign 的 ports 每一项都要写 endpoint 与 kind")
                if pt["kind"] not in EP_KINDS:
                    raise Bad(f"{self.path}: foreign 的端点 {pt['endpoint']} "
                              f"kind={pt['kind']} 不认识，只有 {sorted(EP_KINDS)}")
                if pt["kind"] == "transaction" and not pt.get("profile"):
                    raise Bad(f"{self.path}: 事务端点 {pt['endpoint']} 要写 profile——"
                              f"外人不讲我们的契约，只讲 apb4 或 axi")
                if not pt.get("prefix") and not pt.get("map"):
                    raise Bad(f"{self.path}: 端点 {pt['endpoint']} 既没有 prefix 也没有 map，"
                              f"对不到它的端口上")
            return e
        return None

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
                # 会停顿的控制口要说清什么时候用它：写特性名是那个特性开着时，写 true 是一直用
                if ("ctrl_slow" in e) != ("slow_when" in e):
                    raise Bad(f"{self.path}: emit 的 ctrl_slow 与 slow_when 要成对出现"
                              f"——只给一个，选口的条件就没了")
                sw = e.get("slow_when")
                if "slow_when" in e and sw is not True and not (
                        isinstance(sw, str) and sw in (self.ip.get("features") or {})):
                    raise Bad(f"{self.path}: emit.slow_when 只能写 true 或这个包的特性名，"
                              f"写的是 {sw!r}")
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
