"""`ran asic`：从清单一条命令走到能交的 `.v`、ecc 的结果与流片说明要的数据。

    生成核 → 展平改名 → 套五口顶层 → 自检 → ecc → report.json

报告里的三节是流片说明的原料，由它生成、不手填：参数怎么解出来（每个旋钮的值与来历），
怎么包装（payload 位表、接成常量的输入、不出芯片的输出），从哪来（仓与提交号）。
"""
import datetime
import importlib.metadata
import json
import pathlib
import shutil

from xirang_area.price import closure
from xirang_back import asic as back
from xirang_back import ecc, tools
from xirang_back.tools import ToolError
from xirang_core import diag
from xirang_core.manifest import GEN_HW, GEN_SW, Bad, Pkg
from xirang_core.model import Resolved
from xirang_gen import foreign, frame
from xirang_gen.assemble import addr_map, assemble
from xirang_gen.regmap import generate as gen_regmap
from xirang_out import prov
from xirang_out.doc import to_doc

from . import tasks

# bsc 的库模块与生成物里的 initial 块只给仿真填初值，流片的触发器没有初值
BSC_DEFINES = ("BSV_NO_INITIAL_BLOCKS",)
MARK = ".ran-asic"


def _mod(name: str) -> str:
    return "".join(w.capitalize() for w in name.replace("-", "_").split("_"))


def core_asm(res: Resolved, pkgs: dict[str, Pkg], out: pathlib.Path, extra=()) -> dict:
    """装配：生成寄存器组与顶层，bsc 编成 Verilog。复位是装配顶层的 `rst_n`，低有效。"""
    (out / GEN_HW).mkdir(parents=True, exist_ok=True)
    (out / GEN_SW).mkdir(parents=True, exist_ok=True)
    for name in sorted({i.of for _, i in res.walk()}):
        if pkgs[name].regmap:
            gen_regmap(pkgs[name], out / GEN_HW, out / GEN_SW)
    top = _mod(res.top)
    (out / GEN_HW / f"{top}Pkg.bsv").write_text(assemble(res, pkgs, top), encoding="utf-8")
    (out / "addr_map.md").write_text(addr_map(res), encoding="utf-8")
    src = []
    for p in [pkgs[n] for n in sorted({i.of for _, i in res.walk()})] + list(pkgs.values()):
        for d in p.dirs("hwsrc"):
            if str(d) not in src:
                src.append(str(d))
    rtl = ecc.rtl(out, f"mk{top}", src + list(extra), GEN_HW)
    if rtl is None:
        raise Bad(f"bsc 没把 mk{top} 编出来")
    return {"files": sorted(rtl.glob("*.v")), "top": f"mk{top}", "clock": "clk",
            "reset": "rst_n", "low": True, "params": {}, "defines": list(BSC_DEFINES),
            "includes": [], "root": None, "libdirs": []}


def core_foreign(pkg: Pkg, vals, out: pathlib.Path, pkgs: dict[str, Pkg] | None = None) -> dict:
    """黑盒：先跑它自己的 setup（生成器类上游的源码要先生成才有），再按解出的配置取视图。"""
    e = pkg.foreign_emit()
    if s := e.get("setup"):
        tasks.run(pkg, s, vals, pkg.root / "build", pkgs=pkgs)
    knobs = foreign.knobs_of(vals)
    src = foreign.sources(pkg, "syn", knobs)
    rst = e.get("reset") or {}
    return {"files": src.files, "top": e["top"],
            "clock": (e.get("clock") or {}).get("port", "clk"),
            "reset": rst.get("port", "rst_n"), "low": rst.get("active", "low") == "low",
            "params": foreign.numeric(pkg, vals), "defines": foreign.defines(pkg, vals, "syn"),
            "includes": foreign.includes(pkg, "syn", knobs), "root": pkg.root,
            "libdirs": src.libdirs}


def config_rows(res: Resolved) -> list[dict]:
    """每个实例的每个旋钮：解出的值、赢的那一层、写在哪。"""
    rows = []

    def rec(insts, path):
        for i in insts:
            here = f"{path}.{i.name}" if path else i.name
            for k, v in i.values.items():
                rows.append({"inst": here, "of": i.of, "knob": k, "value": v.value,
                             "layer": v.layer, "origin": v.forced_by or v.winner.origin})
            rec(i.children, here)
    rec(res.instances, "")
    return rows


def _toolchain() -> dict:
    names = ("bsc", "yosys", "sv2v", "ecc")
    got = {n: tools.version(n) for n in names}
    try:
        got["xirang"] = importlib.metadata.version("xirang")
    except importlib.metadata.PackageNotFoundError:
        got["xirang"] = None
    got["pdk"] = ecc.pdk_root()
    return {k: v for k, v in got.items() if v}


def gate(pkg: Pkg, rep: dict) -> list[tuple[str, str, bool]]:
    """ecc 的结果过诊断闸门。返回 [(检查号, 说明, 挡不挡)]。"""
    layers = [("包 ip.yaml", pkg.ip.get("diagnostics") or {})]
    hits = []
    soft = {x["step"] for x in rep.get("lec") or []}
    if not rep.get("ok"):
        bad = [s["name"] for s in rep.get("steps", [])
               if s.get("state") != "Success" and s["name"] not in soft]
        hits.append(("XR-ASIC-005", f"ecc 没跑通：{bad or '没有步骤记录'}"))
    for x in rep.get("lec") or []:
        hits.append(("XR-ASIC-008", f"{x['step']} 没证完：{x['unproven']} 个比对点未证出，"
                                    f"{x['proven']} 个证出；后面的步骤照跑了"))
    t = rep.get("timing") or {}
    wns = (t.get("setup") or {}).get("wns")
    if wns is not None and wns < 0:
        hits.append(("XR-ASIC-006", f"{t.get('step')} 之后建立时间最差裕量 {wns} ns，"
                                    f"总负裕量 {(t.get('setup') or {}).get('tns')} ns"))
    hold = (t.get("hold") or {}).get("wns")
    if hold is not None and hold < 0:
        hits.append(("XR-ASIC-007", f"{t.get('step')} 之后保持时间最差裕量 {hold} ns"))
    out = []
    for code, what in hits:
        lv, _ = diag.resolve(code, layers)
        out.append((code, what, diag.blocks(lv)))
    return out


def run(pkg: Pkg, res: Resolved, pkgs: dict[str, Pkg], out: pathlib.Path,
        mhz: float | None = None, flow: str | None = None, go: bool = True,
        extra=()) -> dict:
    """`res` 是交付的那个包解出来的：清单写了 `asic.core` 就是它，否则是 `pkg` 自己。"""
    spec = pkg.asic()
    if spec is None:
        raise Bad(f"XR-ASIC-001 {pkg.name} 没有 asic 段：流片顶层、主频与 ecc 预设都写在那里")
    mhz = mhz or spec["mhz"]
    flow = flow or spec["flow"]
    top = spec["top"]
    if out.exists():
        # 只清上一次 asic 留下的目录：-o 写错成别的目录，不能把它整个删了
        # 开跑先放标记：半路失败留下的目录没有 report.json，下次也认得出是自己的
        if any(out.iterdir()) and not (out / MARK).is_file():
            raise Bad(f"XR-ASIC-005 {out} 不是空的，也不是 ran asic 的输出，不动它")
        shutil.rmtree(out)
    work = out / "core"
    work.mkdir(parents=True)
    (out / MARK).write_text("ran asic\n", encoding="utf-8")

    cp = pkgs[res.top]
    if cp.is_assembly:
        core = core_asm(res, pkgs, work, extra)
    elif cp.foreign_emit() is not None:
        core = core_foreign(cp, res.instances[0].values, work, pkgs)
    else:
        raise Bad(f"XR-SPEC-001 {cp.name} 是叶子 IP：本版 asic 只收装配与黑盒，"
                  f"叶子先套一层装配")

    mpc = spec["frame"] == "mpc"
    prefix = f"{top}_"
    try:
        core_v, core_top = back.flatten(
            core["files"], core["top"], work / "core.v", prefix,
            top_as=None if mpc else top, params=core["params"],
            defines=core["defines"], includes=core["includes"], root=core["root"],
            libdirs=core["libdirs"])
        text = core_v.read_text(encoding="utf-8")
        ports = back.ports(text, core_top)
    except ToolError as ex:
        raise Bad(f"XR-ASIC-005 {ex}") from None

    doc = {"design": pkg.name, "delivers": cp.name, "top": top, "frame": spec["frame"],
           "mhz": mhz, "flow": flow,
           "generated": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
           "core": {"module": core_top, "from": core["top"], "clock": core["clock"],
                    "reset": core["reset"], "reset_active": "low" if core["low"] else "high"}}
    if mpc:
        pl = frame.plan(spec, ports, core["clock"], core["reset"], core["low"])
        text = frame.render(pl, top, core_top, ports) + "\n" + text
        doc["pads"] = {"width": frame.WIDTH, "used": len(pl.slots),
                       "bits": frame.table(pl), "tie": pl.tie, "unused": pl.unused}
        clock = "clock"
    else:
        clock = spec["clock"] or core["clock"]
        doc["ports"] = [{"name": p.name, "dir": p.dir, "width": p.width} for p in ports]
    v = out / f"{top}.v"
    v.write_text(text, encoding="utf-8")
    try:
        mods = back.check(v, top, prefix)
    except ToolError as ex:
        raise Bad(f"XR-ASIC-005 {ex}") from None
    doc["rtl"] = {"file": v.name, "modules": len(mods), "bytes": v.stat().st_size}

    doc["config"] = config_rows(res)
    doc["resolved"] = to_doc(res)
    doc["sources"] = prov.sources(closure(pkg, pkgs) | closure(cp, pkgs) | {pkg.name, cp.name},
                                  pkgs)
    doc["toolchain"] = _toolchain()

    if (pdk := ecc.pdk_root()) is not None:
        (out / "ecc.toml").write_text(back.ecc_toml(pkg.name, top, v.name, clock, mhz,
                                                    flow, pdk, spec["util"], spec["skip"]), encoding="utf-8")
    elif go:
        raise Bad("XR-ASIC-005 找不到 PDK：设 XR_PDK_ROOT，或让 ecc 装进 "
                  "~/.local/share/ecc/pdks/<名>/<版本>")
    if go:
        try:
            r = back.run_ecc(out)
        except ToolError as ex:
            raise Bad(f"XR-ASIC-005 {ex}") from None
        rep = back.ecc_report(out, top)
        if not rep.get("steps"):
            rep["tail"] = (r.stdout or r.stderr or "")[-2000:]
        doc["ecc"] = rep
        doc["gate"] = [{"code": c, "what": w, "blocks": b} for c, w, b in gate(pkg, rep)]
    (out / "report.json").write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n",
                                     encoding="utf-8")
    return doc
