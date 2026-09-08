"""ecc 后端：BSV -> Verilog -> ecc syn_sta -> 面积。

⚠ 铁律修正：原定「直接调 ecc 的 Python API，不 shell 解析文本」在当前发行形态下
做不到——`ecc` 是编译好的 ELF 二进制，没有可导入的 `chipcompiler` 包。可用的结构化
接口是 `ecc status --json` 与 `ecc rpc serve`；面积仍只能从综合日志取。
本版走命令行，把这条记在任务单里，等 ecc 出可导入形态或 RPC 文档再改。
"""
from __future__ import annotations

import os
import pathlib
import re
import shutil
import subprocess

BSC_LIB = "/home/heke/tools/bsc-2026.01-ubuntu-26.04/lib/Verilog"
PDK_ROOT = "/home/heke/.local/share/ecc/pdks/icsprout55/v1.10.102"

ECC_TOML = """[design]
name = "{name}"
top = "{top}"
rtl = ["rtl/files.f"]
clock_port = "clk"
frequency_mhz = 100.0

[pdk]
name = "ics55"
root = "{pdk}"

[flow]
preset = "syn_sta"
run = "default"
"""


def _run(cmd, cwd=None, env=None):
    return subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)


def bsv_to_verilog(out: pathlib.Path, top: str, src_dirs: list[str],
                   top_src: pathlib.Path | None = None) -> pathlib.Path | None:
    """把 out/bsv 连同额外源目录一起编成 Verilog。"""
    rtl = out / "rtl"
    build = out / ".bsc"
    rtl.mkdir(parents=True, exist_ok=True)
    build.mkdir(parents=True, exist_ok=True)
    path = ":".join([str(out / "bsv"), *src_dirs]) + ":+"
    if top_src is None:
        srcs = sorted((out / "bsv").glob("*.bsv"))
        top_src = next((s for s in srcs if s.stem.endswith("Pkg")), None)
    if top_src is None:
        return None
    r = _run(["bsc", "-verilog", "-u", "-vdir", str(rtl), "-bdir", str(build),
              "-info-dir", str(build), "-p", path, "-g", top, str(top_src)])
    if r.returncode != 0:
        print(r.stdout[-2500:] or r.stderr[-2500:])
        return None
    return rtl


def _pull_bsc_libs(rtl: pathlib.Path):
    """bsc 的库模块不落在 vdir，按实例名递归补齐。"""
    lib = pathlib.Path(BSC_LIB)
    if not lib.exists():
        return
    for _ in range(4):
        have = set()
        want = set()
        for f in rtl.glob("*.v"):
            txt = f.read_text(errors="ignore")
            have |= set(re.findall(r"^module\s+(\w+)", txt, re.M))
            want |= set(re.findall(r"^\s*(\w+)\s+(?:#\(|\w+\s*\()", txt, re.M))
        added = 0
        for m in want - have:
            src = lib / f"{m}.v"
            if src.exists() and not (rtl / f"{m}.v").exists():
                shutil.copy(src, rtl)
                added += 1
        if not added:
            break


def synth(out: pathlib.Path, top: str, name: str,
          extra_src: list[str] | None = None,
          top_src: pathlib.Path | None = None) -> float | None:
    rtl = bsv_to_verilog(out, top, extra_src or [], top_src)
    if rtl is None:
        return None
    _pull_bsc_libs(rtl)
    (rtl / "files.f").write_text("\n".join(sorted(p.name for p in rtl.glob("*.v"))) + "\n")
    (out / "ecc.toml").write_text(ECC_TOML.format(name=name, top=top, pdk=PDK_ROOT))

    env = dict(os.environ)
    env["CHIPCOMPILER_OSS_CAD_DIR"] = "/home/heke/tools/oss-cad-suite"
    env["PATH"] = ("/home/heke/tools/bsc-2026.01-ubuntu-26.04/bin:"
                   "/home/heke/tools/oss-cad-suite/bin:/home/heke/.local/bin:"
                   + env.get("PATH", ""))
    r = _run(["ecc", "run"], cwd=str(out), env=env)
    log = out / "runs" / "default" / "Synthesis_yosys" / "log" / "Synthesis.log"
    if not log.exists():
        print((r.stdout or r.stderr)[-1500:])
        return None
    m = re.findall(r"Chip area for module .*?:\s*([\d.]+)", log.read_text(errors="ignore"))
    return float(m[-1]) if m else None
