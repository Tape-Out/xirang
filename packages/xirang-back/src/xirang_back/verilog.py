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
import re
import subprocess

from .tools import ToolError, need


def _tail(r) -> str:
    """工具挂了就把它最后几行原样端出来，别自己复述。"""
    return chr(10) + chr(10).join((r.stderr or r.stdout).splitlines()[-12:])


def _under(paths, root: pathlib.Path | None):
    """能相对就相对：递给 yosys 的路径会原样出现在产物里。"""
    if root is None:
        return list(paths)
    out = []
    for p in paths:
        p = pathlib.Path(p)
        try:
            out.append(p.resolve().relative_to(root.resolve()))
        except ValueError:
            out.append(p)
    return out


def script(files, top: str, params: dict, out: pathlib.Path,
           defines=(), includes=()) -> str:
    """生成的 yosys 脚本。单独一个函数，好让判据不必真的跑 yosys 就能查。

    宏要跟着一起给：picorv32 的 rvfi 那 177 根端口在 `RISCV_FORMAL` 里，不给宏
    它们压根不存在——而「端口少了一组」不会报错，只会在接线时变成对不上的名字。
    """
    d = ("".join(f" -D{x}" for x in defines)
         + "".join(f" -I{x}" for x in includes))
    lines = [f"read_verilog{d} {f}" for f in files]
    if params:
        sets = " ".join(f"-set {k} {v}" for k, v in sorted(params.items()))
        lines.append(f"chparam {sets} {top}")
    lines += [f"hierarchy -top {top} -check", "proc", "opt_clean",
              f"write_verilog -noattr {out}"]
    return "; ".join(lines)


def elaborate(files, top: str, params: dict, out: pathlib.Path,
              defines=(), includes=(), root: pathlib.Path | None = None
              ) -> pathlib.Path:
    """写出展开后的 Verilog，返回它的路径。

    yosys 读不动 SystemVerilog 的包与结构（`package` 一行就是 syntax error），
    所以 .sv 先过 sv2v 翻成 2005 再消参数。两件事互补：sv2v 不碰 parameter，
    yosys 不认 SystemVerilog。
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    files = list(files)
    here = root
    if any(str(f).endswith(".sv") for f in files):
        files, cut = strip_sim(files, out.parent / "_synth")
        if cut:
            print(f"  抹掉 {cut} 段 translate_off（上游标明不进综合）")
        files = [to_v2005(files, out.with_suffix(".sv2v.v"), top, defines, includes)]
        defines = includes = ()   # 宏与 include 在翻译那一步就处理掉了
        here = out.parent
    # yosys 把源文件路径写进函数局部线的名字（`$func$<路径>:<行>$<序号>`），
    # 绝对路径于是漏进产物：同一份配置换个输出目录就生成不同的 Verilog，摘要对不上。
    # 在一个固定的基准目录下跑，只递相对路径，产物才跟目录无关
    rel = _under(files, here)
    r = subprocess.run([need("yosys"), "-q", "-p",
                        script(rel, top, params, out.resolve(),
                               defines, _under(includes, here))],
                       capture_output=True, text=True,
                       cwd=str(here) if here else None)
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


SIM_OFF = re.compile(r"//\s*(?:synopsys|synthesis|pragma)\s+translate_off(?![A-Za-z0-9_])")
SIM_ON = re.compile(r"//\s*(?:synopsys|synthesis|pragma)\s+translate_on(?![A-Za-z0-9_])")


def strip_sim(files, work: pathlib.Path) -> tuple[list[pathlib.Path], int]:
    """抹掉 `translate_off` 与 `translate_on` 之间的代码，行号照旧。

    这对 pragma 是上游明写的「这一段不进综合」。商用综合器认它，sv2v 不认——
    它把注释连同 pragma 一起去掉，于是仿真专用的东西原样流到 yosys 面前。CVA6 的
    指令追踪器正是这样：`ifndef VERILATOR` 一支用 SystemVerilog 的类，`else` 一支用
    `string` 开文件，两支都不是要流片的那份，而给不给宏都躲不开。

    抹掉的行换成空行，报错里的行号才还对得上源文件。
    """
    work.mkdir(parents=True, exist_ok=True)
    out, cut = [], 0
    for f in files:
        f = pathlib.Path(f)
        txt = f.read_text(encoding="utf-8", errors="replace")
        if not SIM_OFF.search(txt):
            out.append(f)
            continue
        keep, off = [], False
        for line in txt.splitlines():
            if not off and SIM_OFF.search(line):
                off, cut = True, cut + 1
            if off:
                keep.append("")
                if SIM_ON.search(line):
                    off = False
            else:
                keep.append(line)
        # 同名会撞（vendor 里好几个 fifo_v3.sv），按来源路径造唯一名
        tag = str(f).strip("/").replace("/", "_").replace("\\", "_")
        dst = work / tag
        dst.write_text(chr(10).join(keep) + chr(10), encoding="utf-8")
        out.append(dst)
    return out, cut


def to_v2005(files, out: pathlib.Path, top: str | None = None,
             defines=(), includes=()) -> pathlib.Path:
    """SystemVerilog 翻成 Verilog-2005。

    **它不展开参数**——实测 `--top` 只删没被例化的模块，`parameter` 原样保留。
    消参数是 `elaborate` 的事，这里只管让只吃 2005 的前端读得懂。
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [need("sv2v"), *[f"-D{d}" for d in defines],
           *[f"-I{i}" for i in includes]]
    if top:
        cmd.append(f"--top={top}")
    cmd += [str(f) for f in files]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise ToolError("sv2v 翻译失败：" + _tail(r))
    out.write_text(r.stdout, encoding="utf-8")
    return out
