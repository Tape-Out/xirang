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


def run(files, dut: str, params: dict, work: pathlib.Path,
        name: str) -> tuple[bool, str]:
    """编译并跑一次。返回 (过没过, 输出)。"""
    work.mkdir(parents=True, exist_ok=True)
    vvp_file = work / f"{_short(name)}.vvp"
    cmd = [need("iverilog"), "-o", str(vvp_file)]
    cmd += [f"-P{dut}.{k}={v}" for k, v in sorted(params.items())]
    cmd += [str(f) for f in files]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        return False, (r.stderr or r.stdout)
    r = subprocess.run([need("vvp"), "-N", str(vvp_file)],
                       capture_output=True, text=True, timeout=600)
    out = (r.stdout or "") + (r.stderr or "")
    # 与 bluesim 同一条判据：$finish 不设退出码，所以还要看输出里有没有失败字样
    return r.returncode == 0 and not BAD.search(out), out
