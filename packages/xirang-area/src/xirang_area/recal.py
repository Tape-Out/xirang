"""价目表回填：把 `area.measured` 里记着的每一种配置重测一遍，再重算曲线。

改了生成器或 IP 的源码之后，价目表就是对着另一份产物量的（`stale()` 会报）。
手搓几十次综合不现实，所以做成一条命令——组织级 CI 的「面积写另一分支」用它。

**每个配置必须用干净的构建目录**：复用同一个目录时 bsc 判定旧产物还新，
几个配置会量出同一个数，表现为「这个旋钮没进数据通路」，与另一条门禁的
症状一模一样，极易误判。

约定（对着全库的价目表逐个核过）：
  · `base.points[v]`       = 该参数取 v、**所有特性关掉**那一行
  · 特性 F 的 `points[v]`   = （F 开）−（F 关），其余特性关掉；F 有 `depends`
                             时依赖要递归打开——依赖没开的配置根本不合法
  · `params.P.points[v]`   = （P 取 v）−（P 取默认），有 `when: F` 时两边都开 F

`margin` 与 `error` 不动：它们靠离格证伪校准，重算要另跑一轮交叉证伪。
回填之后跑 `xirang test`，「预测不得低于实测」那条门禁会把余量不够的地方顶出来。

写回用 ruamel 的往返模式。`safe_dump` 会把 `ip.yaml` 里的注释全抹掉，而那些
注释正是「为什么这么定」的唯一去处（`test.deadread` 的理由就在里面）。
"""
import datetime
import json
import math
import os
import pathlib
import re
import shutil
import signal
import subprocess
import sys
import tempfile
from types import SimpleNamespace

from xirang_core.manifest import GEN_HW, Bad, Pkg

from xirang_area.price import gen_digest, price


def _defaults(doc) -> dict:
    d = {}
    for section in ("params", "features"):
        for k, v in (doc.get(section) or {}).items():
            d[k] = v.get("default")
    return d


def _canon(at, dflt: dict) -> str:
    full = dict(dflt)
    full.update(dict(at))
    return json.dumps(full, sort_keys=True, ensure_ascii=False, default=str)


def _num(kv):
    s = str(kv)
    return int(s) if s.lstrip("-").isdigit() else kv


def _run(cmd: list[str], timeout: float) -> subprocess.CompletedProcess:
    """起一次构建，超时时整组杀掉。

    构建会再起子进程（`xirang build` 起 ecc，ecc 起 yosys），`subprocess.run` 的 timeout 只杀它直接起的那一个：
    gzip 的第一份价目表在 winBits=12 那一点综合超过 7200 秒，`xirang.cli` 被杀了，ecc 与 yosys 成了孤儿，
    睡着占 736 MB 两个小时。新会话起进程，超时时按进程组杀，孙辈一起走。
    """
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            start_new_session=True)
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.communicate()
        raise
    return subprocess.CompletedProcess(cmd, proc.returncode, out, err)


def measure(pkg: Pkg, knobs: dict, search: list[pathlib.Path]) -> float:
    """量一个配置。中立顶层——价目表量的就是这一层。"""
    d = pathlib.Path(tempfile.mkdtemp(prefix="xirang-recal-"))
    try:
        cmd = [sys.executable, "-m", "xirang.cli"]
        for s in search:
            cmd += ["-p", str(s)]
        cmd += ["build", pkg.name, "--neutral", "-o", str(d)]
        for k, v in knobs.items():
            cmd += ["-s", f"{k}={json.dumps(v) if isinstance(v, bool) else v}"]
        r = _run(cmd, 7200)
        m = re.search(r"实测面积\s+([\d,]+\.\d+)", r.stdout)
        if not m:
            raise Bad(f"{pkg.name} {knobs} 量不出来：{(r.stdout + r.stderr)[-400:]}")
        return float(m.group(1).replace(",", ""))
    finally:
        shutil.rmtree(d, ignore_errors=True)


def recal(pkg: Pkg, search: list[pathlib.Path], apply: bool,
          say=print) -> list[str]:
    """重测并重算。返回每一步的说明，便于逐条核对。"""
    try:
        from ruamel.yaml import YAML
    except ImportError as ex:                       # pragma: no cover
        raise Bad("回填要 ruamel.yaml（往返写回才留得住注释）：pip install ruamel.yaml") from ex

    Y = YAML()
    Y.preserve_quotes = True
    path = pkg.root / "ip.yaml"
    with path.open(encoding="utf-8") as fh:
        doc = Y.load(fh)
    area = doc.get("area") or {}
    if probe := area.get("probe"):
        return _reprobe(pkg, doc, area, probe, path, Y, apply, say)
    rows = area.get("measured") or []
    if not rows:
        return [f"{pkg.name} 没有实测行，不必回填"]

    dflt = _defaults(doc)
    log: list[str] = []
    val: dict[str, float] = {}
    for row in rows:
        at = {k: (bool(v) if isinstance(v, bool) else v)
              for k, v in dict(row["at"]).items()}
        new, old = measure(pkg, at, search), float(row["um2"])
        row["um2"] = new
        val[_canon(at, dflt)] = new
        # 新加的行旧值是 0（占位），别拿它做分母
        pct = f"  ({(new - old) / old * 100:+.2f}%)" if old else "  （新行）"
        line = f"{at}  {old:,.2f} -> {new:,.2f}{pct}"
        log.append(line)
        say(f"  {line}")

    def look(req: dict):
        return val.get(_canon(req, dflt))

    featdoc = doc.get("features") or {}
    allo: dict = {f: False for f in sorted(featdoc)}

    def deps_on(f, acc=None):
        acc = acc if acc is not None else {}
        for d in ((featdoc.get(f) or {}).get("depends") or []):
            acc[d] = True
            deps_on(d, acc)
        return acc

    b = area.get("base") or {}
    if b.get("per") and isinstance(b.get("points"), dict):
        for kv in list(b["points"]):
            v = look(dict(allo, **{b["per"]: _num(kv)}))
            if v is None:
                raise Bad(f"{pkg.name} 的 base[{kv}] 找不到对应实测行")
            log.append(f"base[{kv}] -> {v:,.2f}")
            b["points"][kv] = v
    elif "fixed" in b:
        # 基线不随任何旋钮走的包：量「特性全关、参数取默认」那一点。
        # 没有这一支的时候，这类包的基线永远停在第一次手工填的数上。
        cfg = dict(allo)
        for pn, ps in (doc.get("params") or {}).items():
            if (ps or {}).get("default") is not None:
                cfg[pn] = ps["default"]
        v = look(cfg)
        if v is None:
            raise Bad(f"{pkg.name} 的 base 找不到对应实测行"
                      f"（要有一行是特性全关、参数取默认）")
        log.append(f"base -> {v:,.2f}")
        b["fixed"] = round(v, 2)

    for f, spec in featdoc.items():
        fa = (spec or {}).get("area") or {}
        if "fixed" in fa:
            # 原来这一支直接跳过：定价的特性从不重算，价钱停在第一次手填的数上，
            # 连占位的 0 也原样留着（hart 的 mmu）。取大不取新：手填的数可能是跨参数
            # 取的上界（cache 的 stats 带三个参数），按默认参数量出的一点压不低它。
            on, off = dict(allo, **deps_on(f)), dict(allo, **deps_on(f))
            on[f] = True
            a1, a0 = look(on), look(off)
            if a1 is None or a0 is None:
                raise Bad(f"{pkg.name} 的特性 {f} 缺实测行 {on if a1 is None else off}")
            old, new = float(fa["fixed"] or 0), round(a1 - a0, 2)
            fa["fixed"] = max(old, new)
            keep = "" if new >= old else f"（记的 {old:,.2f} 更大，保留）"
            log.append(f"feat {f} -> {new:,.2f}{keep}")
            continue
        if not isinstance(fa.get("points"), dict):
            continue
        for kv in list(fa["points"]):
            on, off = dict(allo, **deps_on(f)), dict(allo, **deps_on(f))
            on[f] = True
            if fa.get("per"):
                on[fa["per"]] = off[fa["per"]] = _num(kv)
            a1, a0 = look(on), look(off)
            if a1 is None or a0 is None:
                raise Bad(f"{pkg.name} 的特性 {f}[{kv}] 缺实测行")
            log.append(f"feat {f}[{kv}] -> {a1 - a0:,.2f}")
            fa["points"][kv] = round(a1 - a0, 2)

    for pn, spec in (area.get("params") or {}).items():
        pts = (spec or {}).get("points")
        if not isinstance(pts, dict):
            continue
        base_cfg = dict(allo)
        if spec.get("when"):
            base_cfg[spec["when"]] = True
        for kv in list(pts):
            a1, a0 = look(dict(base_cfg, **{pn: _num(kv)})), look(base_cfg)
            if a1 is None or a0 is None:
                raise Bad(f"{pkg.name} 的参数 {pn}[{kv}] 缺实测行")
            log.append(f"param {pn}[{kv}] -> {a1 - a0:,.2f}")
            pts[kv] = round(a1 - a0, 2)

    if apply:
        with path.open("w", encoding="utf-8") as fh:
            Y.dump(doc, fh)
        stamp(pkg)
        log.append(f"写回 {path}")
    return log


def _reprobe(pkg: Pkg, doc, area, probe, path, Y, apply: bool, say) -> list[str]:
    """库包的价钱：照 `area.probe` 记下的配方重量一次。

    库包没有旋钮，也就没有 `measured` 那张表，于是原来这一支直接返回「不必回填」——
    **价钱量过一次就再没人能重现**，源码一改摘要过期，而没有任何命令能把它测回来。
    配方记的是量它的模块与探针文件，探针把多态模块钉在一个具体位宽上。
    """
    from xirang_back import ecc

    mod = probe.get("module")
    src = pkg.root / probe.get("src", "htest/Probe.bsv")
    if not mod or not src.exists():
        raise Bad(f"{pkg.name} 的 area.probe 指的模块或文件不在：{mod} {src}")

    d = pathlib.Path(tempfile.mkdtemp(prefix="xirang-probe-"))
    try:
        (d / GEN_HW).mkdir()
        srcs = [str(x) for x in pkg.dirs("hwsrc")]
        for dep in (doc.get("deps") or {}):
            # 依赖在同级目录下，这里拿不到它的 Pkg，所以只能按约定找。
            # 依赖自己声明了别处的源码时，靠装配那条路径（index 里有 Pkg）覆盖
            q = pkg.root.parent / dep / "hwsrc"
            if q.is_dir():
                srcs.append(str(q))
        um2 = ecc.synth(d, mod, pkg.name, extra_src=srcs, top_src=src, gen=GEN_HW)
    finally:
        shutil.rmtree(d, ignore_errors=True)
    if um2 is None:
        raise Bad(f"{pkg.name} 的探针量不出来——见上面的日志")

    b = area.get("base") or {}
    old = float(b.get("fixed", 0) or 0)
    # `check: true` 的探针**只核对、不写回**。库包的价钱要覆盖的是绑定器在**真实
    # 上下文**里的代价（包装层实测减中立顶层实测），而探针量的是它配一个桩、
    # 孤立综合出来的代价——那是**下界**。两者差得远：本机实测 uart +85.40、
    # gpio +71.96、wdt +96.04，而 rtc 是 **−242.20**（中立顶层要把整个 RegIf 引到
    # 端口上，综合器动不了；包装之后藏起来反而能优化）。一个定值只能取上界，
    # 探针的数拿来当价钱会让「预测不得低于实测」当场破功。
    checking = bool(probe.get("check"))
    pct = f"  ({(um2 - old) / old * 100:+.2f}%)" if old else "  （新）"
    verb = "核对" if checking else "回填"
    line = f"probe {mod}（{verb}）  记的 {old:,.2f}   孤立实测 {um2:,.2f}{pct}"
    say(f"  {line}")
    log = [line]
    if checking:
        if um2 > old:
            log.append(f"{pkg.name}: 孤立实测已经超过记着的价钱，上界不再成立，要重算")
        elif apply and stamp(pkg):
            # 核对通过即对着当前源码重新确认了上界；摘要连 hwsrc/ 下的手写源码也算进去，
            # 这里不盖，库包改了源码就再也盖不回去
            log.append(f"核对通过，重盖摘要 {path}")
        return log
    if apply:
        b["fixed"] = round(um2, 2)
        with path.open("w", encoding="utf-8") as fh:
            Y.dump(doc, fh)
        stamp(pkg)
        log.append(f"写回 {path}")
    return log


def toolchain() -> str:
    """量这一次用的是哪几版工具。

    价目表的口径记了 tool / pdk / freq_mhz，唯独没记版本——换一版 yosys 数就变，
    而摘要照旧说自己有效。这一轮只记不判：历史数据没有版本可比，先让新测的带上。
    """
    import shutil
    import subprocess

    # 每样工具只留版本号那一截。整行留着会把 git sha1 与编译日期也记进去，
    # 那些一变数未必变，反而让「版本变了」这条将来的门禁天天误报。
    probes = (("bsc", ["-v"], r"version ([^\s(]+)"),
              ("yosys", ["-V"], r"Yosys (\S+)"),
              ("ecc", ["--version"], r"(\d[\w.]*)"))
    out = []
    for name, args, pat in probes:
        if not shutil.which(name):
            continue
        try:
            r = subprocess.run([name, *args], capture_output=True, text=True,
                               timeout=30)
        except (OSError, subprocess.SubprocessError):
            continue
        if m := re.search(pat, r.stdout + r.stderr):
            out.append(f"{name} {m.group(1)}")
    return " · ".join(out)


def stamp(pkg: Pkg, date: str | None = None) -> bool:
    """盖上新的产物摘要、日期与工具链版本。只改那几行文本，YAML 不重排。"""
    import datetime

    path = pkg.root / "ip.yaml"
    now = gen_digest(Pkg(pkg.root))
    if now is None:
        return False
    day = date or datetime.date.today().isoformat()
    t = path.read_text(encoding="utf-8")
    t, n1 = re.subn(r"(gen_digest:\s*)sha256:[0-9a-f]+", rf"\g<1>{now}", t)
    t, n2 = re.subn(r"(\n    measured: )'[\d-]+'", rf"\g<1>'{day}'", t)
    n3 = 0
    if tc := toolchain():
        t, n3 = re.subn(r"\n    toolchain: .*", f"\n    toolchain: {tc}", t)
        if not n3 and n2:
            t, n3 = re.subn(r"(\n    measured: '[\d-]+')",
                            rf"\g<1>\n    toolchain: {tc}", t)
    if n1 or n2 or n3:
        path.write_text(t, encoding="utf-8")
    return bool(n1 or n2 or n3)


def grid(spec: dict) -> list:
    """一个参数去量哪几个值：档位旋钮每一档都量，区间旋钮量两端与默认值。

    档位旋钮漏了这一条的后果是只留下一个格点：曲线是平的，而「未计价」那道门禁
    看的是价目表提没提这个旋钮，提了就放行——一个点的曲线恰好两头都糊弄过去。
    """
    if spec.get("values"):
        return sorted(spec["values"])
    return sorted({v for v in (spec.get("default"), *(spec.get("range") or [])) if v is not None})


def plan(doc) -> dict:
    """新包第一次量价目表：要量的行、曲线的形态、离格点。纯函数，不综合。

    曲线形态照全库已有的价目表：基线沿第一个参数走，特性沿同一个参数给增量，其余参数
    各给一条以默认值为零点的增量曲线。离格点取每两个相邻格点的中点（特性全关），特性
    多于一个时再加一行全开——点间插值与特性叠加，低估就出在这两处。
    """
    params = dict(doc.get("params") or {})
    feats = dict(doc.get("features") or {})
    for f, s in feats.items():
        if (s or {}).get("type", "bool") != "bool":
            raise Bad(f"--init 本版只给 bool 特性定价，{f} 是 {(s or {}).get('type')}"
                      f"——recal 只会回填 fixed 与 points 两种形态")
    dflt = {k: (v or {}).get("default") for k, v in params.items()}
    off = {f: False for f in feats}

    def deps(f, acc=None):
        acc = {} if acc is None else acc
        for d in (feats.get(f) or {}).get("depends") or []:
            acc[d] = True
            deps(d, acc)
        return acc

    rows: list[dict] = []

    def add(cfg):
        full = {**dflt, **off, **cfg}
        if full not in rows:
            rows.append(full)

    per = next(iter(params), None)
    at = grid(params[per]) if per else [None]

    def here(v):
        return {per: v} if per else {}

    # 每条曲线各建一份：同一个对象挂两处，ruamel 会写出 YAML 锚点
    def curve():
        return {"per": per, "points": {str(v): 0.0 for v in at}} if per else {"fixed": 0.0}

    for v in at:
        add(here(v))
    for f in feats:
        for v in at:
            add({**here(v), **deps(f)})
            add({**here(v), **deps(f), f: True})
    for q in list(params)[1:]:
        for v in grid(params[q]):
            add({q: v})

    # 中点上特性全关与全开各量一行：只量全关的话，点间插值与特性叠加的低估碰在一起就看不见。
    # wdt 的格点只取 16 与 32 时，width 24 窗口开着那一点低估 4.2%，全关那一点只低估 1.5%
    on = {f: True for f in feats}
    probes = []
    for q, s in params.items():
        # 档位旋钮没有格点之间：每一种配置都落在格点上，中点不是合法取值
        if s.get("values"):
            continue
        g = grid(s)
        for a, b in zip(g, g[1:]):
            mid = (a + b) // 2
            if a < mid < b:
                probes.append({**dflt, **off, q: mid})
                if feats:
                    probes.append({**dflt, **on, q: mid})
    if len(feats) > 1:
        probes.append({**dflt, **on})

    return {
        "rows": rows,
        "probes": probes,
        "area": {"base": curve(),
                 "params": {q: {"points": {str(v): 0.0 for v in grid(params[q])}}
                            for q in list(params)[1:]}},
        "features": {f: curve() for f in feats},
    }


def lift(pairs) -> float:
    """离格点上最坏的欠估（实测高出预测的比例），向上取到千分之一。预测偏高不抵扣。"""
    worst = max(((act - pred) / pred for pred, act in pairs if pred > 0), default=0.0)
    return math.ceil(round(max(worst, 0.0) * 1000, 6)) / 1000


def init(pkg: Pkg, search: list[pathlib.Path], apply: bool, say=print) -> list[str]:
    """没有价目表的新包：照 plan 量一遍、回填曲线、用离格点定余量、盖摘要。

    recal 只会重测已有的实测行，没行就「不必回填」而且不盖摘要，lint 却对每个带 hwsrc/ 的包
    都要摘要——新包原来没有一条命令过得了这一关，只能手工搓价目表。
    """
    if pkg.is_library or pkg.is_assembly:
        raise Bad(f"{pkg.name}：--init 只给叶子 IP 用（库包的价钱走 area.probe，装配的来自实例）")
    try:
        from ruamel.yaml import YAML
    except ImportError as ex:                       # pragma: no cover
        raise Bad("回填要 ruamel.yaml（往返写回才留得住注释）：pip install ruamel.yaml") from ex
    Y = YAML()
    Y.preserve_quotes = True
    path = pkg.root / "ip.yaml"
    with path.open(encoding="utf-8") as fh:
        doc = Y.load(fh)
    if (doc.get("area") or {}).get("measured"):
        raise Bad(f"{pkg.name} 已经有实测行，重测用 recal，不带 --init")

    p = plan(doc)
    head = [f"量 {len(p['rows'])} 行，离格核对 {len(p['probes'])} 行"]
    head += [f"  {r}" for r in p["rows"]] + [f"  离格 {r}" for r in p["probes"]]
    if not apply:
        return head
    # 量一次要几分钟，先把要量什么说出来
    for line in head:
        say(f"  {line}")
    log: list[str] = []

    area = {"base": p["area"]["base"]}
    if p["area"]["params"]:
        area["params"] = p["area"]["params"]
    area.update({
        "margin": 0.0,
        "model": "points-additive",
        "error": {"bound": 0.0, "sign": "over", "note": "placeholder until the off-grid probes are measured"},
        "measured": [{"at": dict(r), "um2": 0.0} for r in p["rows"]],
        "corner": {"tool": "ecc", "pdk": "ics55", "freq_mhz": 100,
                   "measured": datetime.date.today().isoformat(), "gen_digest": "sha256:0"},
    })
    doc["area"] = area
    for f, spec in p["features"].items():
        doc["features"][f]["area"] = spec
    with path.open("w", encoding="utf-8") as fh:
        Y.dump(doc, fh)
    log += recal(Pkg(pkg.root), search, True, say)

    fresh = Pkg(pkg.root)
    pairs, extra = [], []
    for cfg in p["probes"]:
        pred, _ = price(fresh, {k: SimpleNamespace(value=v) for k, v in cfg.items()}, lift=False)
        act = measure(fresh, cfg, search)
        pairs.append((pred, act))
        extra.append({"at": dict(cfg), "um2": act})
        log.append(f"离格 {cfg}  预测 {pred:,.2f}  实测 {act:,.2f}  ({(act - pred) / pred * 100:+.2f}%)")
    m = lift(pairs)

    with path.open(encoding="utf-8") as fh:
        doc = Y.load(fh)
    doc["area"]["margin"] = m
    doc["area"]["error"] = {"bound": m, "sign": "over", "note": (
        f"first measured by ran recal --init at the range ends and the default of each parameter; "
        f"the margin is the worst underestimate at {len(pairs)} off-grid probes" if pairs else
        "first measured by ran recal --init; every configuration is on the grid")}
    doc["area"]["measured"].extend(extra)
    with path.open("w", encoding="utf-8") as fh:
        Y.dump(doc, fh)
    stamp(Pkg(pkg.root))
    log.append(f"余量 {m}，写回 {path}")
    return log
