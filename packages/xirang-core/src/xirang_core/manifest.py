"""读 ip.yaml 与 regmap.yaml，带行号。行号是 computed 面板第二问的答案来源。"""
from __future__ import annotations

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
    def is_assembly(self) -> bool:
        return bool(self.ip.get("instances"))

    def _check(self):
        ip = self.ip
        for k in ("name", "version", "spec"):
            if k not in ip:
                raise Bad(f"{self.path} 缺 {k}")
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

    def knobs(self) -> dict[str, dict]:
        """参数与特性合成一张表，层叠与求解都对着它做。"""
        out = {}
        for n, p in (self.ip.get("params") or {}).items():
            out[n] = {**p, "kind": "param", "type": p.get("type", "int")}
        for n, f in (self.ip.get("features") or {}).items():
            out[n] = {**f, "kind": "feature", "type": f.get("type", "bool")}
        return out
