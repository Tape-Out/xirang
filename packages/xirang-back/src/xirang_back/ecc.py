"""ecc 后端：BSV -> Verilog -> ecc syn_sta -> 面积。

⚠ 铁律修正：原定「直接调 ecc 的 Python API，不 shell 解析文本」在当前发行形态下
做不到——`ecc` 是编译好的 ELF 二进制，没有可导入的 `chipcompiler` 包。可用的结构化
接口是 `ecc status --json` 与 `ecc rpc serve`；面积仍只能从综合日志取。
本版走命令行，把这条记在任务单里，等 ecc 出可导入形态或 RPC 文档再改。
"""
import glob
import os
import pathlib
import re
import shutil
import subprocess

# 工具的位置一律现找，不写死。写死的代价已经付过一次：CI 的面积回填从上线那天起
# 就在 `pdk.root is not a directory: /home/heke/...` 上失败，而失败被 `|| true`
# 吞掉，area 分支照样每次提交一个只有时间戳的空目录，流水线照样绿。
# 顺序是「环境变量 -> 从 PATH 上的可执行文件反推 -> 认输并说清楚」。


def _near(exe: str, rel: str = "") -> str | None:
    """从 PATH 上的可执行文件反推它的安装树。`bin/x` 的上一级就是根。"""
    if not (w := shutil.which(exe)):
        return None
    root = pathlib.Path(w).resolve().parent.parent
    q = root / rel if rel else root
    return str(q) if q.exists() else None


def bsc_lib() -> str | None:
    """bsc 的 Verilog 库模块目录。"""
    return os.environ.get("XR_BSC_LIB") or _near("bsc", "lib/Verilog")


def oss_cad() -> str | None:
    """oss-cad-suite 的根，ecc 靠它找 yosys。"""
    return os.environ.get("CHIPCOMPILER_OSS_CAD_DIR") or _near("yosys")


def pdk_root() -> str | None:
    """PDK 按版本各占一个目录，取版本号最大的那个。"""
    if v := os.environ.get("XR_PDK_ROOT"):
        return v
    hits = sorted(glob.glob(os.path.expanduser(
        "~/.local/share/ecc/pdks/*/v*")))
    return hits[-1] if hits else None

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
    # bsc 的默认栈按小设计定的：eswitch 四口十六条学习表就把它撑爆了
    # （Stack space overflow，报在代码生成阶段）。加栈是唯一的办法。
    r = _run(["bsc", "+RTS", "-K512m", "-RTS",
              "-verilog", "-u", "-vdir", str(rtl), "-bdir", str(build),
              "-info-dir", str(build), "-p", path, "-g", top, str(top_src)])
    if r.returncode != 0:
        print(r.stdout[-2500:] or r.stderr[-2500:])
        return None
    return rtl


def _pull_bsc_libs(rtl: pathlib.Path):
    """bsc 的库模块不落在 vdir，按实例名递归补齐。"""
    if (where := bsc_lib()) is None:
        # 少了库模块，yosys 会在几分钟后报「找不到模块」，那时已经很难回溯到这里
        print("找不到 bsc 的 Verilog 库：设 XR_BSC_LIB，或把 bsc 放进 PATH")
        return
    lib = pathlib.Path(where)
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
    if (pdk := pdk_root()) is None:
        print("找不到 PDK：设 XR_PDK_ROOT，或让 ecc 装进 "
              "~/.local/share/ecc/pdks/<名>/<版本>")
        return None
    (out / "ecc.toml").write_text(ECC_TOML.format(name=name, top=top, pdk=pdk))

    env = dict(os.environ)
    if (cad := oss_cad()) is not None:
        env["CHIPCOMPILER_OSS_CAD_DIR"] = cad
    r = _run(["ecc", "run"], cwd=str(out), env=env)
    log = out / "runs" / "default" / "Synthesis_yosys" / "log" / "Synthesis.log"
    if not log.exists():
        print((r.stdout or r.stderr)[-1500:])
        return None
    m = re.findall(r"Chip area for module .*?:\s*([\d.]+)", log.read_text(errors="ignore"))
    return float(m[-1]) if m else None
