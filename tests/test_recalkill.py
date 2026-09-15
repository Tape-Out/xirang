"""价目表重测起的构建会再起子进程（xirang build 起 ecc，ecc 起 yosys），超时时要整组杀掉。

`subprocess.run` 的 timeout 只杀它直接起的那个进程：gzip 的第一份价目表在 winBits=12 那一点综合超过 7200 秒，
`xirang.cli` 被杀了，`ecc run` 与它的 yosys 成了孤儿，睡着占 736 MB 两个小时。这里起一个会再起孙进程的假命令，
一秒超时，查孙进程已经不在。

`uv run python tests/test_recalkill.py`
"""
import os
import pathlib
import subprocess
import sys
import tempfile
import time

from xirang_area.recal import _run


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # 僵尸算不在：它已经退出，只是没人收
    try:
        return pathlib.Path(f"/proc/{pid}/stat").read_text().split()[2] != "Z"
    except FileNotFoundError:
        return False


def main() -> int:
    with tempfile.TemporaryDirectory() as d:
        pidf = pathlib.Path(d) / "grandchild"
        cmd = ["sh", "-c", f"sleep 60 & echo $! > {pidf}; wait"]
        t0 = time.monotonic()
        try:
            _run(cmd, 1)
        except subprocess.TimeoutExpired:
            pass
        else:
            print("✘ 假命令要跑 60 秒，1 秒超时却没报超时，测试本身的前提不成立")
            return 1
        took = time.monotonic() - t0
        if not pidf.exists():
            print("✘ 孙进程没起来，测试本身的前提不成立")
            return 1
        pid = int(pidf.read_text().strip())
        time.sleep(0.2)
        if alive(pid):
            os.kill(pid, 9)
            print(f"✘ 超时之后孙进程 {pid} 还活着：只杀了直接子进程")
            return 1
        if took > 10:
            print(f"✘ 超时之后 {took:.1f} 秒才返回")
            return 1
    print("✔ 超时之后整组进程都不在了")
    return 0


def test_recalkill():
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
