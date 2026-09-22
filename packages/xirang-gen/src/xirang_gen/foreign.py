"""黑盒的参数投影：把解出来的旋钮值摆成上游 Verilog 参数的值。"""
import pathlib
import re

from xirang_back import sv
from xirang_core.manifest import Bad, Pkg


def bake(pkg: Pkg, vals) -> dict[str, int]:
    """`emit.foreign.params` 说哪个旋钮喂哪个参数。布尔按 1/0 走。

    一个参数都没投影不是「没事」而是「这颗核在我们这儿只有一种配置」——
    息壤配得出的比上游支持的少，而外人看不出少在哪。
    """
    e = pkg.foreign_emit()
    if e is None:
        raise Bad(f"{pkg.name} 不是黑盒包，没有可展开的参数")
    out: dict[str, int] = {}
    for pname, src in (e.get("params") or {}).items():
        v = src if not isinstance(src, str) else (
            vals[src].value if src in vals else None)
        if v is None:
            raise Bad(f"{pkg.name}: 参数 {pname} 投影的旋钮 {src} 没有解出取值")
        if isinstance(v, bool):
            out[pname] = int(v)
        elif isinstance(v, int):
            out[pname] = v
        else:
            # 枚举档位（RV32MFast 这种）原样传给工具，别硬转成数
            out[pname] = v
    return out


def files(pkg: Pkg, view: str = "rtl") -> list[pathlib.Path]:
    """综合视图或仿真视图的文件，按包根解成绝对路径。"""
    e = pkg.foreign_emit()
    if e is None:
        raise Bad(f"{pkg.name} 不是黑盒包")
    got = e.get(view) or e.get("rtl")
    return [pkg.root / f for f in got]


def receipt(pkg: Pkg, vals) -> list[tuple[str, str]]:
    """拿展开之后的端口表回来核对声明。返回 [(检查号, 说了什么)]。

    没有这一步，端口名写错要等到装配接线时才炸，那时报出来的是一堆对不上的
    Verilog 标识符，指不回清单。参数那一条更要紧：投影没生效是**静默**的，
    后端照上游默认值去量，面积与时序量的是另一颗核。
    """
    e = pkg.foreign_emit()
    if e is None:
        return []
    if not sv.available():
        return [("XR-FGN-003", f"{pkg.name}：装 pyslang 才核对得了黑盒声明"
                               f"（pip install xirang[sv]）")]
    want = bake(pkg, vals)
    got = sv.elaborate(files(pkg), e["top"], {k: str(v) for k, v in want.items()})
    ports, pars, errs = got
    out: list[tuple[str, str]] = []
    if errs:
        out.append(("XR-FGN-001", f"{e['top']} 展开时 slang 报错：{errs[0]}"))
        return out

    named: list[str] = []
    for c in ("clock", "reset"):
        if (spec := e.get(c)) and spec.get("port"):
            named.append(spec["port"])
    for pt in e.get("ports") or []:
        named += list((pt.get("map") or {}).values())
        if pre := pt.get("prefix"):
            # 与归组同一条规则：先剥掉 i_／o_／io_ 这类方向前缀再比。
            # SERV 的一组总线叫 i_dbus_ack 与 o_dbus_adr，共有的是 dbus_ 而不是 i_
            if not any(_stem(n).startswith(pre) or n.startswith(pre) for n in ports):
                out.append(("XR-FGN-001",
                            f"端点 {pt['endpoint']} 说端口都以 {pre} 打头，"
                            f"展开之后一个都没有"))
    for n in named:
        if n not in ports:
            near = [p for p in ports if p.startswith(n[:4])][:3]
            out.append(("XR-FGN-001",
                        f"{e['top']} 没有端口 {n}" + (f"，像的有 {near}" if near else "")))

    for k, v in want.items():
        if k not in pars:
            out.append(("XR-FGN-002", f"{e['top']} 没有参数 {k}"))
        elif pars[k][0] != v:
            out.append(("XR-FGN-002",
                        f"参数 {k} 要的是 {v}，展开之后是 {pars[k][0]}"))
    return out


def _camel(name: str) -> str:
    """ENABLE_MUL -> enableMul；BusSizeECC -> busSizeECC。

    上游两种命名都有：picorv32 用全大写加下划线，ibex 本来就是驼峰。一律先小写
    再拼会把后者毁掉（BusSizeECC -> bussizeecc），那样旋钮名与上游参数对不上，
    读的人得回去查表。
    """
    if "_" not in name:
        # 开头连着几个大写要整段降格，否则 RV32E 变成 rV32E、MHPMCounterNum 变成
        # mHPMCounterNum。最后一个大写若后面跟着小写，那它是下一个词的头，留着
        m = re.match(r"[A-Z]+", name)
        if not m:
            return name
        run = m.group(0)
        tail = name[len(run):]
        if len(run) > 1 and tail[:1].islower():
            return run[:-1].lower() + run[-1] + tail
        return run.lower() + tail
    a, *rest = name.split("_")
    return a.lower() + "".join(w[:1].upper() + w[1:].lower() for w in rest)


DIRS = ("i_", "o_", "io_")


def _stem(n: str) -> str:
    """剥掉方向前缀再归组。

    SERV 的线叫 `o_dbus_adr` 与 `i_dbus_ack`，按 `o_`／`i_` 归组会把不相干的
    凑成一堆——真正成组的是 `dbus`。
    """
    for d in DIRS:
        if n.startswith(d):
            return n[len(d):]
    return n


def _groups(ports: dict, skip: set) -> tuple[dict[str, list[str]], list[str]]:
    """按公共前缀把端口归组。整组协议写 prefix，零散的线逐根 map。

    前缀是数出来的，不是猜的：`mem_axi_awvalid` 与 `mem_axi_rdata` 共有 `mem_axi_`，
    而 `trap` 谁也不跟。三根以上才算一组——两根凑一组多半是巧合。
    """
    names = [n for n in ports if n not in skip]
    best: dict[str, list[str]] = {}
    for n in names:
        parts = _stem(n).split("_")
        for k in range(len(parts) - 1, 0, -1):
            pre = "_".join(parts[:k]) + "_"
            best.setdefault(pre, []).append(n)
    groups: dict[str, list[str]] = {}
    taken: set[str] = set()
    for pre in sorted(best, key=lambda p: (-len(best[p]), p)):
        rest = [n for n in best[pre] if n not in taken]
        if len(rest) >= 3:
            groups[pre] = sorted(rest)
            taken |= set(rest)
    return groups, sorted(n for n in names if n not in taken)


def _string_params(files, top: str) -> dict[str, str]:
    """源码里默认值是字符串字面量的参数。

    Verilog 没有字符串类型，`parameter RESET_STRATEGY = "MINI"` 打包成 32 位整数，
    展开之后看到的是 1296649801。把它当整数投影回去，模块里的字符串比较就永远不等。
    """
    out: dict[str, str] = {}
    for f in files:
        txt = pathlib.Path(f).read_text(encoding="utf-8", errors="ignore")
        i = txt.find(f"module {top}")
        if i < 0:
            continue
        head = txt[i:txt.find(") (", i) if ") (" in txt[i:i + 8000] else i + 8000]
        for m in re.finditer(r"parameter[^=;]*?(\w+)\s*=\s*\"([^\"]*)\"", head):
            out[m.group(1)] = m.group(2)
    return out


def draft(files, top: str, clock: str = "clk", reset: str = "rst_n") -> str:
    """从别人的 RTL 出一份声明草稿：全部参数、按前缀归好的端点。

    草稿是起点不是终点——端点的 kind 与 role 要人去判，profile 要人去认。
    但「参数漏了一个」「端口名抄错了」这两类错，草稿一出来就不存在了。
    """
    from xirang_back import sv
    if not sv.available():
        raise Bad("出草稿要 pyslang：pip install xirang[sv]")
    got = sv.elaborate(files, top, {})
    if got is None:
        raise Bad("出草稿要 pyslang")
    ports, pars, errs = got
    if errs:
        raise Bad(f"{top} 展开不了：{errs[0]}")

    L = ["params:"]
    feats = ["features:"]
    proj = []
    skipped = []
    strs = _string_params(files, top)
    for k, (v, w) in sorted(pars.items()):
        kn = _camel(k)
        if k in strs:
            # Verilog 把字符串默认值打包成整数（"MINI" -> 0x4D494E49），slang 看到的
            # 也是整数。只有源码的声明文本看得出它本来是字符串
            skipped.append(f'{k} = "{strs[k]}"  （字符串，写成 choice 并列出取值域）')
            continue
        if not isinstance(v, int):
            # 数组、结构、枚举：不是标量，做不成旋钮。留在默认值上，并说出来——
            # 「没做」写出来，比让人以为「已经全支持了」诚实
            skipped.append(f"{k} = {str(v)[:40]}")
            continue
        proj.append(f"    {k}: {kn}")
        if w == 1:
            feats += [f"  {kn}:", "    type: bool", f"    default: {str(bool(v)).lower()}"]
        else:
            L += [f"  {kn}:", "    type: int", f"    default: {v}"]
    if skipped:
        L.insert(1, f"  # 这 {len(skipped)} 个不是标量，本版留在默认值上：")
        L[2:2] = [f"  #   {x}" for x in skipped]
    groups, loose = _groups(ports, {clock, reset})
    P = ["emit:", "- kind: foreign", "  lang: verilog", f"  top: {top}",
         "  rtl: []      # 填上综合视图的文件", "  params:"] + proj + [
        "  clock:", f"    port: {clock}", "  reset:", f"    port: {reset}",
        "    active: low", "    sync: true", "  ports:"]
    for pre, ns in groups.items():
        P += [f"  # {len(ns)} 根：{' '.join(ns[:4])}{' …' if len(ns) > 4 else ''}",
              f"  - endpoint: {pre.rstrip('_')}", "    kind: transaction",
              "    role: manager", "    profile: 填上它讲哪种协议",
              f"    prefix: {pre}"]
    if loose:
        P += ["  - endpoint: pins", "    kind: physical", "    type: 填个类型名",
              "    map:"] + [f"      {_camel(n)}: {n}" for n in loose]
    return chr(10).join(L + feats + P)
