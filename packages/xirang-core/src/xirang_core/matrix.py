"""测试矩阵：一个 IP 该在哪些配置下被验。

只做线性覆盖，不做全组合。全组合是价目表的活——价目表要给出上界，非穷尽
不可；测试要问的是「每个开关的门控真的生效吗」，那是线性的。默认配置永远
在矩阵里，它是大多数人拿到手就用的那一份。

写在 ip.yaml 的 test 段，全都可省：

    test:
      matrix: auto                    # auto（默认）| full | none
      extra:                          # 额外要测的交叉点
        - {fifoDepth: 3, parity: true}
      skip:                           # 派生出来但不该测的
        - {fifoDepth: 64}

auto 派生出来的点若被约束判为不合法，跳过并说明；extra 里手写的点不合法
就是错误——那是人写错了，不是派生的副产品。

装配自己没有旋钮，要写 `axes` 说扫哪几条，键是指到实例旋钮的点号键：

    test:
      axes: [cpu.mul, mem.words]
      extra:
        - {mem.words: 4096, cpu.mul: false}
"""
import itertools

from .manifest import Bad, Pkg

MODES = ("auto", "full", "none")
TEST_KEYS = {"matrix", "extra", "skip", "unused", "noarea", "deadread", "upstream", "axes"}
FULL_CAP = 64


def _tag(v) -> str:
    if isinstance(v, bool):
        return "On" if v else "Off"
    return "".join(ch if ch.isalnum() else "_" for ch in str(v))


def label(ov: dict) -> str:
    """点的名字要能当 BSV 包名后缀用，所以只留字母数字。"""
    if not ov:
        return "Default"
    return "".join("".join(s[:1].upper() + s[1:] for s in k.split(".")) + _tag(v)
                   for k, v in sorted(ov.items()))


def _closure(knobs: dict, name: str) -> dict:
    """打开一个特性，连它依赖的一起打开——否则约束会把它压回去，这一点就白测。"""
    out: dict = {}
    stack = [name]
    while stack:
        n = stack.pop()
        if n in out:
            continue
        out[n] = True
        stack += list(knobs.get(n, {}).get("depends") or [])
    return out


def _axes(knobs: dict) -> list[tuple[str, list]]:
    out = []
    for k, s in knobs.items():
        if s["type"] == "bool":
            out.append((k, [False, True]))
        elif s["type"] == "choice":
            out.append((k, list(s["values"])))
        elif s["type"] == "int" and s.get("range"):
            r = s["range"]
            d = s.get("default")
            vs = {r[0], r[1]} | ({d} if isinstance(d, int) else set())
            out.append((k, sorted(vs)))
    return out


def _auto(knobs: dict) -> list[dict]:
    bools = [k for k, s in knobs.items() if s["type"] == "bool"]
    choices = {k: s["values"] for k, s in knobs.items() if s["type"] == "choice"}
    ints = {k: s["range"] for k, s in knobs.items()
            if s["type"] == "int" and s.get("range")}

    out: list[dict] = [{}]
    lo = {k: False for k in bools}
    lo.update({k: v[0] for k, v in choices.items()})
    lo.update({k: r[0] for k, r in ints.items()})
    hi = {k: True for k in bools}
    hi.update({k: v[-1] for k, v in choices.items()})
    hi.update({k: r[1] for k, r in ints.items()})
    out += [p for p in (lo, hi) if p]
    # 每个特性单独开一次、单独关一次。只测「开」是不够的：门控写漏了的
    # 表现恰恰是「关掉了硬件还在」，那要在其余旋钮都正常时才看得出来——
    # Min 那一点所有旋钮同时走极端，出了事分不清是谁的。
    out += [_closure(knobs, k) for k in bools]
    out += [{k: False} for k in bools]
    out += [{k: v} for k, vs in choices.items() for v in vs]
    out += [{k: v} for k, r in ints.items() for v in (r[0], r[1])]
    return out


def _full(knobs: dict, name: str) -> list[dict]:
    axes = _axes(knobs)
    n = 1
    for _, vs in axes:
        n *= len(vs)
    if n > FULL_CAP:
        raise Bad(f"{name}: matrix: full 会展开成 {n} 个点，超过 {FULL_CAP}。"
                  f"改回 auto，需要哪几个交叉点就写进 extra")
    keys = [k for k, _ in axes]
    return [dict(zip(keys, combo)) for combo in itertools.product(*[v for _, v in axes])]


def _matches(skip: dict, ov: dict) -> bool:
    """skip 是模式：写出来的键值全都对上就算命中，没写的键不管。"""
    return all(k in ov and ov[k] == v for k, v in skip.items())


def knob_at(pkg: Pkg, key: str, index: dict[str, Pkg]) -> dict:
    """装配的点号键 `实例.旋钮`（装配套装配就接着点）落到哪个旋钮上。

    返回那个旋钮的定义，`depends` 换成同一实例下的点号键，好让 `_closure`
    照叶子的规矩把依赖一起打开。落不到就报错，不静默丢掉。
    """
    *path, k = key.split(".")
    if not path:
        raise Bad(f"{pkg.path}: {key} 要写成 实例.旋钮")
    here = pkg
    for name in path:
        spec = next((s for s in here.ip.get("instances") or []
                     if s.get("name") == name), None)
        if spec is None:
            raise Bad(f"{pkg.path}: {key} 里的 {name} 不是 {here.name} 的实例")
        if spec["of"] not in index:
            raise Bad(f"{pkg.path}: 找不到实例 {name} 的包 {spec['of']}")
        here = index[spec["of"]]
    knobs = here.knobs()
    if k not in knobs:
        raise Bad(f"{pkg.path}: {key} 里的 {k} 不是 {here.name} 的旋钮")
    d = dict(knobs[k])
    if d.get("depends"):
        d["depends"] = [".".join(path) + "." + x for x in d["depends"]]
    return d


def check(pkg: Pkg, index: dict[str, Pkg] | None = None):
    """test 段的键必须都认识——写错的键会被默默忽略，那比报错糟得多。"""
    t = pkg.ip.get("test")
    if t is None:
        return
    if not isinstance(t, dict):
        raise Bad(f"{pkg.path}: test 段应是映射")
    unknown = set(t) - TEST_KEYS
    if unknown:
        raise Bad(f"{pkg.path}: test 段有不认识的键 {sorted(unknown)}")
    if t.get("matrix", "auto") not in MODES:
        raise Bad(f"{pkg.path}: test.matrix 只能是 {list(MODES)}")
    if not isinstance(t.get("unused", []), list):
        raise Bad(f"{pkg.path}: test.unused 应是列表")
    if "axes" in t:
        if not pkg.is_assembly:
            raise Bad(f"{pkg.path}: test.axes 只给装配用，叶子的轴就是它全部的旋钮")
        if not isinstance(t["axes"], list) or not all(isinstance(a, str) for a in t["axes"]):
            raise Bad(f"{pkg.path}: test.axes 应是点号键的列表")
    if pkg.is_assembly:
        for key in ("extra", "skip"):
            if any(not isinstance(pt, dict) for pt in t.get(key) or []):
                raise Bad(f"{pkg.path}: test.{key} 的每一项都该是映射")
        if index is not None:
            for k in [*(t.get("axes") or []),
                      *(k for key in ("extra", "skip") for pt in t.get(key) or [] for k in pt)]:
                knob_at(pkg, k, index)
        return
    knobs = pkg.knobs()
    for key in ("extra", "skip"):
        for pt in t.get(key) or []:
            if not isinstance(pt, dict):
                raise Bad(f"{pkg.path}: test.{key} 的每一项都该是映射")
            bad = set(pt) - set(knobs)
            if bad:
                raise Bad(f"{pkg.path}: test.{key} 提到没有的旋钮 {sorted(bad)}")


def points(pkg: Pkg, index: dict[str, Pkg] | None = None) -> list[tuple[str, dict, bool]]:
    """返回 (名字, 旋钮覆盖, 是不是手写的)。手写的点不合法要报错，派生的可以跳过。"""
    check(pkg, index)
    t = pkg.ip.get("test") or {}
    if pkg.is_assembly:
        return _asm_points(pkg, t, index or {})
    knobs = pkg.knobs()
    mode = t.get("matrix", "auto")
    if not knobs or mode == "none":
        derived: list[dict] = [{}]
    elif mode == "full":
        derived = _full(knobs, pkg.name)
    else:
        derived = _auto(knobs)

    skips = [dict(s) for s in (t.get("skip") or [])]
    held: list[tuple[str, str, str]] = []
    fixed: list[tuple[str, dict]] = []
    out: list[tuple[str, dict, bool]] = []
    seen: set = set()
    for ov, hand in ([(p, False) for p in derived]
                     + [(dict(e), True) for e in (t.get("extra") or [])]):
        if not hand and any(_matches(s, ov) for s in skips):
            continue
        # 守卫先修正，修不动才不提供。换了国家是重填省份，不是关掉表单——
        # 把 RV32 那个点整个删掉，等于因为一个联动字段丢掉了半个架构的覆盖
        name = label(ov)
        full = pkg.settled(ov)
        if not hand:
            full, fix = _repair(pkg, full, set(ov), knobs)
            if hit := pkg.offends(full):
                held.append((name, hit[0], hit[1]))
                continue
            if fix:
                ov = {**ov, **fix}
                fixed.append((name, fix))
        key = tuple(sorted(ov.items(), key=lambda kv: kv[0]))
        if key in seen:
            continue
        seen.add(key)
        out.append((name if not hand else label(ov), ov, hand))
    _held[pkg.path] = held
    _fixed[pkg.path] = fixed
    return out


def _asm_points(pkg: Pkg, t: dict, index: dict[str, Pkg]) -> list[tuple[str, dict, bool]]:
    """装配的点：每条轴照叶子 auto 的规则取值。

    守卫不在这里修：装配的守卫要看整棵实例树解出来的值，那是解析的事，
    撞上的派生点由调用方标「略」。
    """
    knobs = {a: knob_at(pkg, a, index) for a in t.get("axes") or []}
    mode = t.get("matrix", "auto")
    if not knobs or mode == "none":
        derived: list[dict] = [{}]
    elif mode == "full":
        derived = _full(knobs, pkg.name)
    else:
        derived = _auto(knobs)
    skips = [dict(s) for s in (t.get("skip") or [])]
    out: list[tuple[str, dict, bool]] = []
    seen: set = set()
    for ov, hand in ([(p, False) for p in derived]
                     + [(dict(e), True) for e in (t.get("extra") or [])]):
        if not hand and any(_matches(s, ov) for s in skips):
            continue
        key = tuple(sorted(ov.items(), key=lambda kv: kv[0]))
        if key in seen:
            continue
        seen.add(key)
        out.append((label(ov), ov, hand))
    return out


_held: dict[str, list[tuple[str, str, str]]] = {}
_fixed: dict[str, list[tuple[str, dict]]] = {}


def _repair(pkg, full: dict, varied: set, knobs: dict) -> tuple[dict, dict]:
    """把联动字段挪进允许的取值域。本点正在变的那个字段不动——
    那个点存在的意义就是试它，替它改值等于没测。"""
    full, fix = dict(full), {}
    for _ in range(len(pkg.guards()) + 1):
        hit = pkg.offends(full)
        if hit is None or hit[0] in varied:
            break
        k = hit[0]
        keep = [v for g in pkg.guards() if all(full.get(a) == b
                                               for a, b in g["when"].items())
                for v in g["narrow"].get(k, [])]
        d = full.get(k, knobs.get(k, {}).get("default"))
        full[k] = fix[k] = d if d in keep else (
            min(keep, key=lambda v: abs(v - d))
            if all(isinstance(v, int) and not isinstance(v, bool) for v in keep)
            and isinstance(d, int) and not isinstance(d, bool) else keep[0])
    return full, fix


def adjusted(pkg) -> list[tuple[str, dict]]:
    """为了满足守卫被顺手改掉的联动字段：(点名, 改成什么)。"""
    if pkg.path not in _fixed and not pkg.is_assembly:
        points(pkg)
    return _fixed.get(pkg.path, [])


def withheld(pkg) -> list[tuple[str, str, str]]:
    """守卫挡下来、没有进矩阵的点：(点名, 旋钮, 为什么)。"""
    if pkg.path not in _held and not pkg.is_assembly:
        points(pkg)
    return _held.get(pkg.path, [])
