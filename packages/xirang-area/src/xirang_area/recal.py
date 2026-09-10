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
import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

from xirang_core.manifest import Bad, Pkg

from xirang_area.price import gen_digest


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
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=7200)
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
    src = pkg.root / probe.get("src", "tb/Probe.bsv")
    if not mod or not src.exists():
        raise Bad(f"{pkg.name} 的 area.probe 指的模块或文件不在：{mod} {src}")

    d = pathlib.Path(tempfile.mkdtemp(prefix="xirang-probe-"))
    try:
        (d / "bsv").mkdir()
        srcs = [str(pkg.root / "bsv")]
        for dep in (doc.get("deps") or {}):
            q = pkg.root.parent / dep / "bsv"
            if q.is_dir():
                srcs.append(str(q))
        um2 = ecc.synth(d, mod, pkg.name, extra_src=srcs, top_src=src)
    finally:
        shutil.rmtree(d, ignore_errors=True)
    if um2 is None:
        raise Bad(f"{pkg.name} 的探针量不出来——见上面的日志")

    b = area.get("base") or {}
    old = float(b.get("fixed", 0) or 0)
    pct = f"  ({(um2 - old) / old * 100:+.2f}%)" if old else "  （新）"
    line = f"probe {mod}  {old:,.2f} -> {um2:,.2f}{pct}"
    say(f"  {line}")
    log = [line]
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
