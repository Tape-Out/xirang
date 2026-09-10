"""bsc 的仿真与调度门禁。

调度门禁认哪几个 G 编号是工具的知识，不该抄在每个流水线的 grep 里——
抄一份就多一处会漏改的地方。G0021 尤其要盯：bsc 只把「规则永不触发」
报成警告，而它几乎总意味着某条隐式条件被提到了整条规则头上。
"""
import pathlib
import re
import subprocess

# 这几个一出现就是错，不是风格问题：
#   G0004 并行冲突   G0021 规则永不触发   G0006 循环   G0035 方法被多次调用
FATAL = ("G0004", "G0021", "G0006", "G0035")

# 大设计会把 bsc 的默认栈撑爆（eswitch 四口十六条学习表），加栈是唯一的办法
RTS = ["+RTS", "-K512m", "-RTS"]


def _run(cmd, cwd=None, timeout=900):
    try:
        return subprocess.run(cmd, cwd=cwd, capture_output=True,
                              text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd, 124, "", "超时")


def _dirs(work: pathlib.Path):
    work.mkdir(parents=True, exist_ok=True)
    return ["-bdir", str(work), "-info-dir", str(work), "-simdir", str(work)]


def typecheck(src: pathlib.Path, path: str, work: pathlib.Path) -> tuple[bool, str]:
    r = _run(["bsc", *RTS, "-u", *_dirs(work)[:4], "-p", path, str(src)])
    return r.returncode == 0, (r.stdout + r.stderr)


def schedule(top: str, src: pathlib.Path, path: str,
             work: pathlib.Path) -> tuple[bool, list[str], str]:
    """跑到 Verilog 并要调度表。只在这里才看得见隐式条件被提升。"""
    rtl = work / "rtl"
    rtl.mkdir(parents=True, exist_ok=True)
    r = _run(["bsc", *RTS, "-verilog", "-u", "-show-schedule", "-vdir", str(rtl),
              *_dirs(work)[:4], "-p", path, "-g", top, str(src)])
    log = r.stdout + r.stderr
    hits = sorted({g for g in FATAL if f"({g})" in log})
    return r.returncode == 0 and not hits, hits, log


def sim(top: str, src: pathlib.Path, path: str,
        work: pathlib.Path) -> tuple[bool, str]:
    """编译并跑一个测试台。返回是否通过与输出。"""
    d = _dirs(work)
    r = _run(["bsc", *RTS, "-sim", "-u", *d, "-p", path, "-g", top, str(src)])
    if r.returncode != 0:
        return False, (r.stdout + r.stderr)[-3000:]
    exe = work / f"run_{top}"
    r = _run(["bsc", "-sim", "-e", top, "-o", str(exe), *d])
    if r.returncode != 0:
        return False, (r.stdout + r.stderr)[-3000:]
    r = _run([str(exe)], timeout=600)
    out = (r.stdout + r.stderr).strip()
    # $finish(1) 也好，测试台自己打 FAILED 也好，都算没过
    ok = r.returncode == 0 and not re.search(r"\b(FAIL|FAILED|TIMEOUT)\b", out)
    return ok, out
