"""把别人的 Verilog 按解出来的配置展开成一份没有参数的 Verilog。

ecc 与 yosys-sta 的流程不接受从外面传进来的 Verilog 参数：它们读文件、指定顶层，
参数就只能取默认值。于是「息壤配好的那套旋钮」到了后端会悄悄变回上游默认——
面积与时序量的是另一颗核，而没有任何一步会报错。

所以配置解完就把参数**展开掉**：`chparam` 定值、`hierarchy` 展开层次、`proc` 把
RTLIL 进程变回寄存器与逻辑，再写出来。只 `hierarchy` 不 `proc` 也能消掉参数，但
yosys 会警告「进程不一定映射得回 always 块，仿真行为可能变」——那条警告不能忽略。

sv2v 做不了这件事：实测 `--top` 只删没被例化的模块，`parameter` 原样保留。它是
SystemVerilog 到 Verilog-2005 的翻译器，与展开参数是两件事。
"""
import pathlib
import subprocess

from .tools import ToolError, need


def _tail(r) -> str:
    """工具挂了就把它最后几行原样端出来，别自己复述。"""
    return chr(10) + chr(10).join((r.stderr or r.stdout).splitlines()[-12:])


def script(files, top: str, params: dict, out: pathlib.Path) -> str:
    """生成的 yosys 脚本。单独一个函数，好让判据不必真的跑 yosys 就能查。"""
    lines = [f"read_verilog {f}" for f in files]
    if params:
        sets = " ".join(f"-set {k} {v}" for k, v in sorted(params.items()))
        lines.append(f"chparam {sets} {top}")
    lines += [f"hierarchy -top {top} -check", "proc", "opt_clean",
              f"write_verilog -noattr {out}"]
    return "; ".join(lines)


def elaborate(files, top: str, params: dict, out: pathlib.Path) -> pathlib.Path:
    """写出展开后的 Verilog，返回它的路径。"""
    out.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run([need("yosys"), "-q", "-p", script(files, top, params, out)],
                       capture_output=True, text=True)
    if r.returncode != 0 or not out.is_file():
        tail = "\n".join((r.stderr or r.stdout).splitlines()[-12:])
        raise ToolError(f"展开 {top} 失败：\n{tail}")
    left = [ln for ln in out.read_text(encoding="utf-8").splitlines()
            if ln.strip().startswith("parameter ")]
    if left:
        raise ToolError(f"展开后仍有 {len(left)} 处 parameter，后端会把它们当默认值：{left[:3]}")
    return out

def schematic(files, top: str, params: dict, out: pathlib.Path,
              flat: bool = False) -> pathlib.Path:
    """画一张电路拓扑图。默认只画顶层那一层——整颗核摊平了没人看得懂。

    yosys 的 `show` 出 dot，graphviz 出 svg。两样都要在。
    """
    need("dot")
    out.parent.mkdir(parents=True, exist_ok=True)
    stem = out.with_suffix("")
    steps = [f"read_verilog {f}" for f in files]
    if params:
        sets = " ".join(f"-set {k} {v}" for k, v in sorted(params.items()))
        steps.append(f"chparam {sets} {top}")
    steps += [f"hierarchy -top {top} -check", "proc", "opt_clean"]
    if flat:
        steps.append("flatten")
    steps.append(f"show -format svg -prefix {stem} -notitle {top}")
    r = subprocess.run([need("yosys"), "-q", "-p", "; ".join(steps)],
                       capture_output=True, text=True)
    svg = stem.with_suffix(".svg")
    if r.returncode != 0 or not svg.is_file():
        raise ToolError(f"画 {top} 的拓扑图失败：" + _tail(r))
    return svg


def to_v2005(files, out: pathlib.Path, top: str | None = None,
             defines=()) -> pathlib.Path:
    """SystemVerilog 翻成 Verilog-2005。

    **它不展开参数**——实测 `--top` 只删没被例化的模块，`parameter` 原样保留。
    消参数是 `elaborate` 的事，这里只管让只吃 2005 的前端读得懂。
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [need("sv2v"), *[f"-D{d}" for d in defines]]
    if top:
        cmd.append(f"--top={top}")
    cmd += [str(f) for f in files]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise ToolError("sv2v 翻译失败：" + _tail(r))
    out.write_text(r.stdout, encoding="utf-8")
    return out
