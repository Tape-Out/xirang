"""流片交付：展平成一个 .v、跑 ecc、读它的报告。

这一层只驱动外部工具，不认识清单。顶层怎么包、payload 怎么排由 `xirang_gen.frame`
决定，这里拿到的是现成的文件、名字与数字。
"""
import dataclasses
import json
import os
import pathlib
import re
import subprocess
import time

from .ecc import WORKSPACE, oss_cad, pdk_root, workspace
from .tools import ToolError, need
from .verilog import elaborate

# 清单里写的流程。syn_sta 只到综合后时序；rtl2gds 走完布局布线、寄生提取、布线后时序、DRC、LVS 到 Harden。
# ecc 自 alpha.12 起预设只剩这两个；harden 与 rcx 是它之前给全流程与「加寄生」起的名字，照 rtl2gds 跑
FLOWS = ("syn_sta", "rtl2gds", "harden", "rcx")
PRESET = {"harden": "rtl2gds", "rcx": "rtl2gds"}

_ID = r"(?:\\\S+|[A-Za-z_][\w$]*)"
MODULE = re.compile(rf"^module\s+({_ID})\s*\(", re.M)
# write_verilog 的例化恒是「类型 实例名 (」独占一行，参数在 hierarchy 那一步已经并进类型名
INST = re.compile(rf"^(\s+)({_ID})(\s+{_ID}\s*\()", re.M)
DECL = re.compile(r"^\s*(input|output|inout)\s+(?:wire\s+|reg\s+|signed\s+)*"
                  r"(?:\[\s*(-?\d+)\s*:\s*(-?\d+)\s*\]\s*)?(" + _ID + r")\s*;", re.M)


@dataclasses.dataclass(frozen=True)
class Port:
    name: str
    dir: str      # input | output | inout
    width: int


def _base(name: str) -> str:
    """`\\$paramod\\FIFO2\\width=...` 取 `FIFO2`；普通名字原样。"""
    if name.startswith("\\$paramod"):
        parts = name.split("\\")
        name = parts[2] if len(parts) > 2 else name
    return re.sub(r"\W", "_", name.lstrip("\\")).strip("_") or "m"


def rename(text: str, top: str, prefix: str, top_as: str | None = None) -> tuple[str, dict]:
    """除 `top` 外的模块名一律加前缀，`top` 改成 `top_as`（省略也加前缀）。

    同一片 MPC 上的几颗设计都带着 bsc 的 `FIFO2`，不改名就撞。参数化出来的变体
    按原名排序后编号，同一份输入改出来的名字每次都一样。
    """
    names = MODULE.findall(text)
    if top not in names:
        raise ToolError(f"展平后的文件里没有顶层 {top}")
    new, used = {}, set()
    groups: dict[str, list[str]] = {}
    for n in sorted(names):
        groups.setdefault(_base(n), []).append(n)
    for b, ns in sorted(groups.items()):
        for i, n in enumerate(ns):
            cand = f"{prefix}{b}" if len(ns) == 1 else f"{prefix}{b}_{i}"
            while cand in used:
                cand += "_"
            new[n] = cand
            used.add(cand)
    if top_as is not None:
        if top_as in used and new[top] != top_as:
            raise ToolError(f"顶层要叫 {top_as}，但这个名字已经分给了别的模块")
        new[top] = top_as

    def mod(m):
        s = m.group(0)
        return s[:m.start(1) - m.start(0)] + new[m.group(1)] + s[m.end(1) - m.start(0):]

    def inst(m):
        t = m.group(2)
        return m.group(0) if t not in new else m.group(1) + new[t] + m.group(3)

    out = MODULE.sub(mod, text)
    out = INST.sub(inst, out)
    return out, new


# 只过 proc 的话，存储阵列写出来是逐位的写口：带 TLB 的核在 Verilator 上慢四十倍，交付的那份要仿真得动
# 上游写在 initial 里的 $display 会被 yosys 留成 $print 单元，一路带进综合后的网表；断言单元同理
TIDY = ("opt", "memory_share", "opt_mem", "delete t:$print t:$check")


def flatten(files, top: str, out: pathlib.Path, prefix: str, top_as: str | None = None,
            params: dict | None = None, defines=(), includes=(), root=None,
            libdirs=(), secs: int = 1800) -> tuple[pathlib.Path, str]:
    """所有模块写进一个文件：参数展开、层次保留、名字加前缀。返回文件与顶层的新名字。"""
    raw = out.with_suffix(".raw.v")
    elaborate(files, top, params or {}, raw, defines, includes, root, secs, libdirs, TIDY)
    txt, names = rename(raw.read_text(encoding="utf-8"), top, prefix, top_as)
    out.write_text(txt, encoding="utf-8")
    raw.unlink()
    return out, names[top]


def ports(text: str, module: str) -> list[Port]:
    """模块的端口，按声明次序。只认 write_verilog 与 bsc 写出来的非 ANSI 形式。"""
    m = re.search(rf"^module\s+{re.escape(module)}\s*\((.*?)\);(.*?)^endmodule",
                  text, re.M | re.S)
    if not m:
        raise ToolError(f"找不到模块 {module}")
    order = [p.strip() for p in m.group(1).replace("\n", " ").split(",") if p.strip()]
    decl = {}
    for d in DECL.finditer(m.group(2)):
        hi, lo = d.group(2), d.group(3)
        w = abs(int(hi) - int(lo)) + 1 if hi is not None else 1
        decl[d.group(4)] = Port(d.group(4), d.group(1), w)
    miss = [p for p in order if p not in decl]
    if miss:
        raise ToolError(f"{module} 的端口 {miss[:5]} 没有方向声明")
    return [decl[p] for p in order]


def check(v: pathlib.Path, top: str, prefix: str) -> list[str]:
    """交付的 .v 自己能不能站住：只有顶层不带前缀，yosys 按它展开不缺模块。"""
    txt = v.read_text(encoding="utf-8")
    stray = [n for n in MODULE.findall(txt) if n != top and not n.startswith(prefix)]
    if stray:
        raise ToolError(f"{v.name} 里有没改名的模块 {stray[:5]}")
    r = subprocess.run([need("yosys"), "-q", "-p",
                        f"read_verilog {v}; hierarchy -top {top} -check"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        tail = "\n".join((r.stderr or r.stdout).splitlines()[-8:])
        raise ToolError(f"{v.name} 独立展开失败：\n{tail}")
    return MODULE.findall(txt)


ECC_TOML = """[design]
name = "{name}"
top = "{top}"
rtl = ["{rtl}"]
clock_port = "{clock}"
frequency_mhz = {mhz}

[pdk]
name = "ics55"
root = "{pdk}"

[flow]
preset = "{flow}"
"""


def ecc_toml(name: str, top: str, rtl: str, clock: str, mhz: float, flow: str,
             pdk: str) -> str:
    """ecc 按 clock_port 与 frequency_mhz 自己生成 create_clock 与输入输出延迟。"""
    if flow not in FLOWS:
        raise ToolError(f"ecc 没有预设 {flow}，只有 {FLOWS}")
    return ECC_TOML.format(name=name, top=top, rtl=rtl, clock=clock,
                           mhz=float(mhz), flow=PRESET.get(flow, flow), pdk=pdk)


def run_ecc(out: pathlib.Path, secs: int = 6 * 3600) -> subprocess.CompletedProcess:
    if pdk_root() is None:
        raise ToolError("找不到 PDK：设 XR_PDK_ROOT，或让 ecc 装进 "
                        "~/.local/share/ecc/pdks/<名>/<版本>")
    env = dict(os.environ)
    if (cad := oss_cad()) is not None:
        env["CHIPCOMPILER_OSS_CAD_DIR"] = cad
    cmd = [need("ecc"), "run", "--workspace", WORKSPACE]
    end = time.monotonic() + secs
    try:
        r = subprocess.run(cmd, cwd=str(out), env=env, capture_output=True, text=True, timeout=secs)
        # 等价比对没证完时 ecc 停在那一步。那是「没证出来」而不是「证出不等价」，照样把后面的步骤跑完，
        # 比对的结果原样留在报告里由闸门去判
        for _ in range(4):
            nxt = _after_unproven(out)
            if nxt is None:
                break
            r = subprocess.run([*cmd, "--from", nxt], cwd=str(out), env=env, capture_output=True,
                               text=True, timeout=max(1, end - time.monotonic()))
        return r
    except subprocess.TimeoutExpired:
        raise ToolError(f"ecc 超过 {secs} 秒还没结束") from None


UNPROVEN = re.compile(r"Of those cells (\d+) are proven and (\d+) are unproven")


def _lec(base: pathlib.Path, step: dict) -> dict | None:
    """一步等价比对停下来的原因：证出与没证出的比对点各几个。不是比对步骤、或没留下这份统计的，返回 None。"""
    if "lec" not in str(step.get("tool", "")).lower():
        return None
    try:
        txt = (base / f"{step['name']}_{step['tool']}" / "report" / "equiv_status.rpt").read_text(
            encoding="utf-8", errors="replace")
    except OSError:
        return None
    m = UNPROVEN.search(txt)
    return {"step": step["name"], "proven": int(m.group(1)), "unproven": int(m.group(2))} if m else None


def _after_unproven(out: pathlib.Path) -> str | None:
    """流程若停在一步没证完的等价比对上，返回它后面那一步的名字。"""
    base = workspace(out)
    steps = (_json(base / "home" / "flow.json") or {}).get("steps", [])
    for k, s in enumerate(steps):
        if s.get("state") == "Success":
            continue
        if s.get("state") == "Incomplete" and _lec(base, s) and k + 1 < len(steps) \
                and steps[k + 1].get("state") == "Unstart":
            return steps[k + 1]["name"]
        return None
    return None


def _json(p: pathlib.Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def ecc_report(out: pathlib.Path, top: str) -> dict:
    """从 ecc 的结构化产物取数，不读日志。时序取最后一个出了时序的步骤。"""
    base = workspace(out)
    flow = _json(base / "home" / "flow.json") or {}
    steps = [{"name": s.get("name"), "tool": s.get("tool"), "state": s.get("state"),
              "runtime": s.get("runtime"), "peak_mb": s.get("peak memory (mb)")}
             for s in flow.get("steps", [])]
    lec = [x for s in steps if s["state"] != "Success" and (x := _lec(base, s))]
    soft = {x["step"] for x in lec}
    rep: dict = {"steps": steps, "ok": bool(steps) and all(
        s["state"] == "Success" or s["name"] in soft for s in steps)}
    if lec:
        rep["lec"] = lec
    for s in steps:
        d = base / f"{s['name']}_{s['tool']}"
        if (st := _json(d / "feature" / f"{s['name']}_stat.json")) is not None:
            mod = (st.get("modules") or {}).get(f"\\{top}") or {}
            if mod:
                rep["cells"] = mod.get("num_cells")
                rep["area_um2"] = mod.get("area")
                rep["seq_area_um2"] = mod.get("sequential_area")
        for q in sorted(d.glob("feature/**/qor_summary.json")):
            if (j := _json(q)) and "summary" in j:
                rep["timing"] = {"step": s["name"], **j["summary"]}
        for p in sorted(d.glob("feature/**/power_summary.json")):
            if j := _json(p):
                rep["power_uw"] = {k: v for k, v in j.items() if k != "schema_version"}
    ck = _json(base / "home" / "checklist.json") or {}
    if ck:
        rep["checklist"] = {"status": ck.get("status"), **(ck.get("summary") or {})}
    return rep
