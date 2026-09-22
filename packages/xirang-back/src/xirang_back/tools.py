"""外部工具：在不在、哪个版本、拿它做什么。

**只驱动，不打包。** bsc、yosys、ecc 从一开始就是这么处理的，sv2v 与其余一样：
息壤把解出来的配置投影成它们的命令行，版本由 `doctor` 报出来并钉进价目表口径。
把别人的构建模型搬进来是另一回事，我们不做。

加一种工具只加一行。
"""
import dataclasses
import re
import shutil
import subprocess


class ToolError(RuntimeError):
    """外部工具不在，或者跑挂了。back 不认识我们的数据模型，所以自带一个。"""


@dataclasses.dataclass(frozen=True)
class Tool:
    name: str
    what: str
    args: tuple[str, ...] = ("--version",)
    pick: str = r"(\d+\.\d+[\w.+-]*)"


TOOLS: dict[str, Tool] = {t.name: t for t in (
    Tool("bsc", "BSV 与 BH 的编译器", ("-v",), r"version ([\w.]+)"),
    Tool("yosys", "综合；也用它把参数展开成没有参数的 Verilog", ("-V",)),
    Tool("ecc", "ICS55 的后端流程"),
    Tool("iverilog", "跑上游自带的 Verilog 测试台"),
    Tool("vvp", "iverilog 的解释器"),
    Tool("verilator", "快速仿真与 lint"),
    Tool("sv2v", "SystemVerilog 翻成 Verilog-2005，给只吃 2005 的后端", ("--numeric-version",)),
    Tool("slang", "SystemVerilog 前端，用来核对端口与层次"),
    Tool("ghdl", "VHDL 前端"),
    Tool("dot", "画电路拓扑图：yosys show 出 dot，它出 svg", ("-V",)),
    Tool("netlistsvg", "把 yosys 的 json 网表画成好看的原理图", ("--help",), r"()"),
    Tool("sby", "形式化验证的驱动"),
    Tool("gtkwave", "看波形"),
)}


# 系统里没有原生的那一份时，pip 上有几样是现成的兜底。原生优先——WASM 慢一截，
# 但「装不上就用不了」比「慢一点」糟得多。加一样只加一行。
PIP_FALLBACK = {"yosys": "yowasp-yosys", "nextpnr-ice40": "yowasp-nextpnr-ice40"}


def find(name: str) -> str | None:
    """先找系统里的，再找 pip 装的那份。"""
    if exe := shutil.which(name):
        return exe
    alt = PIP_FALLBACK.get(name)
    return shutil.which(f"yowasp-{name}") if alt else None


def version(name: str) -> str | None:
    t = TOOLS.get(name)
    exe = find(name)
    if not t or not exe:
        return None
    try:
        r = subprocess.run([exe, *t.args], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return "?"
    m = re.search(t.pick, (r.stdout or "") + (r.stderr or ""))
    return (m.group(1) if m and m.groups() and m.group(1) else "在")


def need(name: str) -> str:
    exe = find(name)
    if not exe:
        t = TOOLS.get(name)
        tip = (f"，或 pip install {PIP_FALLBACK[name]}"
               if name in PIP_FALLBACK else "")
        raise ToolError(f"找不到 {name}——{t.what if t else '这一步要它'}。"
                        f"装一个并挂到 PATH{tip}")
    return exe


def survey() -> list[tuple[str, str, str]]:
    """(名字, 版本或「缺」, 拿它做什么)。"""
    return [(n, version(n) or "缺", t.what) for n, t in TOOLS.items()]
