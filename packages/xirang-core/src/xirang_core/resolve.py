"""层叠与约束求解。

层叠六层，取值高层压低层；约束横切所有层，它不是"更高的一层"，
而是另一种来历——面板必须把"被上层覆盖"和"被约束强制"分开显示，
否则用户会以为是自己写错了。

钩子不参与这里：它们只能往下游加产物，改不了取值。这条纪律不是洁癖，
是面板的前提——值一旦可能来自某次探测，"谁定的"就答不了。
"""
from __future__ import annotations

import pathlib

from .manifest import Bad, Pkg, where
from .model import Candidate, Instance, Resolved, Value


class Cascade:
    """一个实例的层叠：按层收集候选，最后挑最高层。"""

    def __init__(self, knobs: dict[str, dict]):
        self.knobs = knobs
        self.cand: dict[str, list[Candidate]] = {k: [] for k in knobs}

    def offer(self, layer: str, values: dict, origin_of):
        """values 的值可以是裸值，也可以是 (值, 来源) —— 后者用于祖先传下来的覆盖。"""
        for k, v in (values or {}).items():
            if k not in self.cand:
                raise Bad(f"{origin_of(k)}: 没有名为 {k} 的旋钮")
            if isinstance(v, tuple) and len(v) == 2 and isinstance(v[1], str):
                val, org = v
            else:
                val, org = v, origin_of(k)
            self.cand[k].append(Candidate(layer, val, org))

    def settle(self) -> dict[str, Value]:
        from .model import LAYERS
        out = {}
        for k, cs in self.cand.items():
            cs = sorted(cs, key=lambda c: LAYERS.index(c.layer))
            if not cs:
                raise Bad(f"旋钮 {k} 没有任何取值，连默认值都没有")
            out[k] = Value(name=k, value=cs[-1].value, winner=cs[-1], shadowed=cs[:-1])
        return out


def _check_domain(knobs, vals):
    for k, v in vals.items():
        spec = knobs[k]
        if spec["type"] == "bool" and not isinstance(v.value, bool):
            raise Bad(f"{k} 应是布尔，得到 {v.value!r}（来自 {v.winner.origin}）")
        if spec["type"] == "choice" and v.value not in spec["values"]:
            raise Bad(f"{k}={v.value!r} 不在 {spec['values']}（来自 {v.winner.origin}）")
        if spec["type"] == "int":
            if not isinstance(v.value, int):
                raise Bad(f"{k} 应是整数，得到 {v.value!r}（来自 {v.winner.origin}）")
            r = spec.get("range")
            if r and not (r[0] <= v.value <= r[1]):
                raise Bad(f"{k}={v.value} 超出 range {r}（来自 {v.winner.origin}）")


def _apply_constraints(pkg: Pkg, knobs, vals):
    """kconfig 的 depends on + 我们的 constraints。

    与 kconfig 不同的是：约束不满足时**报错**，不像 select 那样硬开一个
    依赖没满足的符号。select 是 kconfig 公认最烂的部分，不借。
    """
    # depends：依赖关掉，自己也必须关。这一条会改值，且要标成"被强制"。
    changed = True
    rounds = 0
    while changed:
        changed = False
        rounds += 1
        if rounds > len(knobs) + 2:
            raise Bad("约束求解不收敛，检查 depends 是否成环")
        for k, spec in knobs.items():
            if spec["type"] != "bool":
                continue
            for dep in spec.get("depends", []) or []:
                if vals[dep].value is False and vals[k].value is not False:
                    if vals[k].winner.layer in ("instance", "cli"):
                        raise Bad(
                            f"{k} 依赖 {dep}，但 {dep} 是关的；"
                            f"而 {k} 是在 {vals[k].winner.origin} 显式打开的。"
                            f"要么打开 {dep}，要么别开 {k}")
                    vals[k].value = False
                    vals[k].forced_by = f"depends on {dep}"
                    changed = True

    for c in pkg.ip.get("constraints", []) or []:
        when, then = c.get("when", {}), c.get("then", {})
        if all(vals[k].value == v for k, v in when.items() if k in vals):
            for k, v in then.items():
                if k in vals and vals[k].value != v:
                    raise Bad(c.get("msg") or
                              f"约束不满足：{when} 要求 {k}={v}，实际是 {vals[k].value}")


def resolve_pkg(pkg: Pkg, overrides: dict, override_origin: str,
                pdk: dict | None = None, cli: dict | None = None) -> dict[str, Value]:
    knobs = pkg.knobs()
    c = Cascade(knobs)
    # 1 bsv-default：本版由 ip.yaml 的 default 代表（结构仍由 BSV 类型权威）
    c.offer("bsv-default", {k: v.get("default") for k, v in knobs.items()
                            if v.get("default") is not None},
            lambda k: f"{pkg.path} (default)")
    # 2 pdk：工艺事实。只覆盖同名旋钮，通常为空。
    if pdk:
        c.offer("pdk", {k: v for k, v in pdk.items() if k in knobs},
                lambda k: "pdk/ics55")
    # 3 ip-default 与 1 同源，本版合并
    # 4 workspace：本版不做
    # 5 instance
    c.offer("instance", overrides, lambda k: override_origin)
    # 6 cli
    if cli:
        c.offer("cli", {k: v for k, v in cli.items() if k in knobs}, lambda k: "<cli>")

    vals = c.settle()
    _check_domain(knobs, vals)
    _apply_constraints(pkg, knobs, vals)
    _check_domain(knobs, vals)
    return vals


def _tagged(v) -> bool:
    return isinstance(v, tuple) and len(v) == 2 and isinstance(v[1], str)


def _find_pkg(name: str, search: list[pathlib.Path]) -> Pkg:
    for d in search:
        p = d / name
        if (p / "ip.yaml").exists():
            return Pkg(p)
    raise Bad(f"找不到包 {name}，搜索路径 {[str(s) for s in search]}")


def resolve(top: str, search: list[pathlib.Path],
            pdk: dict | None = None, cli: dict | None = None) -> Resolved:
    root = _find_pkg(top, search)
    if not root.is_assembly:
        raise Bad(f"{top} 没有 instances 段，不是装配")
    bus = root.ip.get("bus", "apb4")

    def _split(with_: dict, org: str) -> tuple[dict, dict]:
        """把 with 拆成「自己的」与「后代的」，两边都带上来源。

        后代那份要一路传下去，所以来源必须跟着走——否则面板会把祖先写的值
        算在直接父层头上。
        """
        own, desc = {}, {}
        for k, v in (with_ or {}).items():
            if "." in k:
                head, _, rest = k.partition(".")
                desc.setdefault(head, {})[rest] = (v, org) if not _tagged(v) else v
            else:
                own[k] = (v, org) if not _tagged(v) else v
        return own, desc

    def build(pkg: Pkg, depth: int, inherited: dict | None = None) -> list[Instance]:
        out = []
        inherited = inherited or {}
        for spec in pkg.ip.get("instances", []) or []:
            sub = _find_pkg(spec["of"], search)
            if sub.is_library:
                raise Bad(f"{spec['name']} 例化了库包 {spec['of']}——"
                          f"库包只贡献源码，不会被例化")
            origin = where(pkg.ip, "instances", pkg.path)
            own, desc = _split(spec.get("with", {}), origin)
            # 祖先传下来的覆盖优先于本层的 with——它来自更高的层，且自带来源
            anc_own, anc_desc = _split(inherited.get(spec["name"], {}), origin)
            for k, v in anc_desc.items():
                desc.setdefault(k, {}).update(v)
            own.update(anc_own)
            vals = resolve_pkg(sub, own, origin, pdk, cli)
            addr = spec.get("addr")
            if addr is None and sub.regmap:
                addr = sub.regmap.get("base")
            size = sub.regmap.get("size") if sub.regmap else None
            inst = Instance(name=spec["name"], of=spec["of"], values=vals,
                            addr=int(str(addr), 0) if addr is not None else None,
                            size=int(str(size), 0) if size is not None else None,
                            bus=spec.get("bus", bus))
            if sub.is_assembly:
                inst.children = build(sub, depth + 1, desc)
            elif desc:
                raise Bad(f"{spec['name']} 不是装配，配不了后代：{sorted(desc)}")
            out.append(inst)
        return out

    insts = build(root, 0)
    _check_addr(insts)
    return Resolved(top=top, bus=bus, instances=insts)


def _check_addr(insts: list[Instance]):
    """地址重叠在展开期报错，不留到仿真。"""
    spans = [(i.addr, i.addr + (i.size or 0), i.name) for i in insts if i.addr is not None]
    spans.sort()
    for (a0, a1, n0), (b0, b1, n1) in zip(spans, spans[1:]):
        if b0 < a1:
            raise Bad(f"地址重叠：{n0} [{a0:#x},{a1:#x}) 与 {n1} [{b0:#x},{b1:#x})")
