"""跑上游自带的 Verilog 测试台。

接一颗别人的核，最有说服力的证据是**它自己的测试在我们配出来的每一组参数下还过**。
我们重写一份测试只能证明我们理解的那部分；上游的测试覆盖的是他们知道、我们还不知道的那些。

参数用 iverilog 的 `-P<层次名>.<参数>=<值>` 覆盖——测试台里那个实例叫什么，
由清单的 `dut` 说。
"""
import hashlib
import pathlib
import re
import subprocess

from .tools import need

BAD = re.compile(r"\b(FAIL|FAILED|ERROR|TIMEOUT)\b")


def _short(name: str) -> str:
    """矩阵点的标签是把旋钮名串起来的，旋钮一多就超过文件名 255 字节的上限。

    截断加摘要：还看得出是哪一点，又不会让 `iverilog -o` 在一条看不懂的
    「文件名过长」上失败——那条错误看着像上游的核有问题，其实是我们的。
    """
    if len(name) <= 48:
        return name
    return name[:32] + "_" + hashlib.sha1(name.encode()).hexdigest()[:8]


def run(files, dut: str, params: dict, work: pathlib.Path, name: str,
        cwd: pathlib.Path | None = None, plusargs=(), expect: str = "",
        secs: int = 300) -> tuple[bool, str]:
    """编译并跑一次。返回 (过没过, 输出)。

    **在包根下跑**：上游测试台里的 `$readmemh("sw/x.hex")` 是相对进程当前目录的。
    在别处跑，内存读不进来全是 X，核永远转不完——表现成仿真挂住，而挂住这件事
    看不出是路径问题。

    **`expect` 才是判据**：`+timeout` 逼停之后仿真也会「正常结束」，光看退出码
    等于把「没量」说成「过了」。要看它真的打出了该打的东西。
    """
    work.mkdir(parents=True, exist_ok=True)
    vvp_file = work / f"{_short(name)}.vvp"
    cmd = [need("iverilog"), "-o", str(vvp_file)]
    cmd += [f"-P{dut}.{k}={v}" for k, v in sorted(params.items())]
    cmd += [str(f) for f in files]
    here = str(cwd) if cwd else None
    r = subprocess.run(cmd, capture_output=True, text=True,
                       errors="replace", cwd=here)
    if r.returncode != 0:
        return False, (r.stderr or r.stdout)
    try:
        # 上游测试台打的是原始串口字节，不都是合法 UTF-8。不给 errors 会让
        # 整轮矩阵死在一个解码错误上，而那与被测的核毫无关系
        r = subprocess.run([need("vvp"), "-N", str(vvp_file), *plusargs],
                           capture_output=True, text=True, errors="replace",
                           timeout=secs, cwd=here)
    except subprocess.TimeoutExpired:
        return False, f"仿真跑了 {secs} 秒还没结束"
    out = (r.stdout or "") + (r.stderr or "")
    if r.returncode != 0 or BAD.search(out):
        return False, out
    if expect and expect not in out:
        return False, ("输出里没有「" + expect + "」——仿真结束了，但它没做该做的事"
                       + chr(10) + out[-400:])
    return True, out
