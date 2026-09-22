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


# 怎么认「一处定义」。上游的常量文件就这几种写法
SYNTAX = {
    "localparam": r"(localparam\s+(?:[A-Za-z_][\w:]*\s+)?{name}\s*=\s*)[^;]+",
    "parameter": r"(parameter\s+(?:[A-Za-z_][\w:]*\s+)?{name}\s*=\s*)[^;]+",
}


def _lit(v) -> str:
    """布尔写成 1/0：上游这些字段多是 0/1 的整数，`bit'(x)` 也吃得下。"""
    if isinstance(v, bool):
        return "1" if v else "0"
    return str(v)


def generate(pkg: Pkg, vals) -> list[tuple[str, int]]:
    """按解出来的旋钮，从上游的常量文件生成一份我们自己的。

    有一类上游的配置既不是 Verilog 参数、也不是宏，而是**一份写死的常量文件**：
    CVA6 的 `cva6_config_pkg.sv` 是 49 个 `localparam`，`-D` 碰不到；它自己的扩展点
    就是「编哪一个配置包」。这里做的正是那件事——拿上游的一份当模板，只换我们
    暴露出去的那几项，其余保持上游值。**上游加了字段也不会漏**。

    返回 [(产物路径, 换掉几处)]。
    """
    e = pkg.foreign_emit() or {}
    out = []
    knobs = {k: getattr(v, "value", v) for k, v in (vals or {}).items()}
    for g in e.get("generate") or []:
        if any(knobs.get(k) != v for k, v in (g.get("when") or {}).items()):
            continue
        src = pkg.root / g["from"]
        if not src.is_file():
            raise Bad(f"{pkg.name}: generate 的模板 {g['from']} 不在树上")
        pat = SYNTAX[g.get("syntax", "localparam")]
        txt = src.read_text(encoding="utf-8")
        n = 0
        pairs = dict(g.get("set") or {})
        if g.get("expose") == "all":
            # 暴露出来的旋钮名就是文件里的标识符，一一对应。**按这一条自己的模板取**：
            # 几份模板的字段集不一样（CVA6 的 32 位那份没有 BExtEn），取并集会让
            # 「必须恰好命中一次」在另一份上炸掉
            from xirang_core.manifest import FIND
            mine = {m[0] for m in re.findall(FIND[g.get("syntax", "localparam")], txt)}
            pairs |= {k: k for k in knobs if k in mine}
        for knob, name in pairs.items():
            if knob not in knobs:
                raise Bad(f"{pkg.name}: generate 用了没解出取值的旋钮 {knob}")
            txt, hit = re.subn(pat.format(name=re.escape(name)),
                               lambda m: m.group(1) + _lit(knobs[knob]), txt)
            if hit != 1:
                raise Bad(f"{pkg.name}: 模板 {g['from']} 里 {name} 命中 {hit} 次，"
                          f"要恰好一次——名字写错了，或者上游改了写法")
            n += 1
        dst = pkg.root / g["out"]
        dst.parent.mkdir(parents=True, exist_ok=True)
        head = (f"// 息壤按解出来的旋钮生成，别手改。模板 {g['from']}"
                + chr(10) + f"// 换了 {n} 处" + chr(10) * 2)
        dst.write_text(head + txt, encoding="utf-8")
        out.append((g["out"], n))
    return out


def defines(pkg: Pkg, vals=None) -> list[str]:
    """这份黑盒要带哪些宏。

    两种写法。列表是固定的宏（`RISCV_FORMAL`）。表是**投影**：宏的值取自旋钮，
    Vortex 整份配置就是这样给的（`-DVX_CFG_NUM_CORES=4`），没有一个 Verilog
    参数。布尔旋钮写成 `{when: 旋钮}`，开了才定义这个宏、且不带值——
    `ifdef` 判的是「定义没定义」，给它 `=0` 反而是打开。
    """
    e = pkg.foreign_emit() or {}
    got = e.get("defines") or []
    if isinstance(got, list):
        return [str(x) for x in got]
    out = []
    for macro, src in got.items():
        if isinstance(src, dict):
            k = src.get("when")
            if k is None:
                raise Bad(f"{pkg.name}: 宏 {macro} 的表里只认 when")
            v = _knob(pkg, vals, k, macro)
            if v:
                out.append(str(macro))
            continue
        v = src if not isinstance(src, str) else _knob(pkg, vals, src, macro)
        out.append(f"{macro}={int(v) if isinstance(v, bool) else v}")
    return out


def _knob(pkg: Pkg, vals, name: str, macro: str):
    if name not in pkg.knobs():
        raise Bad(f"{pkg.name}: 宏 {macro} 投影到了不存在的旋钮 {name}")
    if vals is None or name not in vals:
        # 没解出配置时（起草、lint）用默认值，不假装有值
        d = pkg.knobs()[name].get("default")
        if d is None:
            raise Bad(f"{pkg.name}: 宏 {macro} 要的旋钮 {name} 没有默认值")
        return d
    return getattr(vals[name], "value", vals[name])


def includes(pkg: Pkg) -> list[pathlib.Path]:
    """`include 的搜索路径，按包根解成绝对路径。"""
    e = pkg.foreign_emit()
    return [pkg.root / d for d in (e or {}).get("includes") or []]


def files(pkg: Pkg, view: str = "rtl", knobs=None) -> list[pathlib.Path]:
    """综合视图或仿真视图的文件，按包根解成绝对路径。

    条目可以写成 `{path: …, when: {旋钮: 值}}`：上游的某些源码只在某档配置下
    才编得动。cv32e40p 的 fpnew 就是这样——它在常量函数里写 `$fatal`，yosys
    直接拒绝，而那段只有 fpu=1 才走得到。`knobs` 不给就全要，用于还没解出配置
    的场合（起草声明、核对端口）。
    """
    e = pkg.foreign_emit()
    if e is None:
        raise Bad(f"{pkg.name} 不是黑盒包")
    out = []
    for f in e.get(view) or e.get("rtl"):
        if isinstance(f, dict):
            if knobs is not None and any(knobs.get(k) != v
                                         for k, v in (f.get("when") or {}).items()):
                continue
            f = f["path"]
        out.append(pkg.root / f)
    return out


def knobs_of(vals) -> dict:
    """解出来的旋钮摊成普通的名字到值，给 `when` 比对用。"""
    return {k: getattr(v, "value", v) for k, v in vals.items()}


def numeric(pkg: Pkg, vals) -> dict:
    """给 yosys 的参数值：枚举档位换成数。

    sv2v 翻完之后枚举名就不存在了，`chparam -set BaseIsa BaseIsaRV32I` 只会得到
    「Can't decode value」。档位对应的数从 slang 的类型信息里取，写死一张表迟早
    与上游对不上。
    """
    want = bake(pkg, vals)
    if all(not isinstance(v, str) for v in want.values()):
        return want
    e = pkg.foreign_emit()
    if not sv.available():
        raise Bad(f"{pkg.name}: 有枚举档位的参数要装 pyslang 才展得成数"
                  f"（pip install xirang[sv]）")
    _, pars, _ = sv.elaborate(files(pkg, knobs=knobs_of(vals)), e["top"], {},
                              defines(pkg), includes(pkg))
    out = {}
    for k, v in want.items():
        if isinstance(v, str):
            mem = dict((pars[k].enum if k in pars else ()) or ())
            if v not in mem:
                raise Bad(f"{pkg.name}: 参数 {k} 的档位 {v} 不在上游枚举里"
                          f"（有 {sorted(mem) or '空'}）")
            v = mem[v]
        out[k] = v
    return out


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
    generate(pkg, vals)
    want = bake(pkg, vals)
    got = sv.elaborate(files(pkg, knobs=knobs_of(vals)), e["top"], want,
                       defines(pkg, vals), includes(pkg))
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
        elif (got := pars[k]).enum:
            # 枚举按档位名比。名字对不上就是没生效，而 <unset> 正是没生效的样子
            cur = next((a for a, b in got.enum if b == got.value), str(got.value))
            if str(v) != cur:
                names = [a for a, _ in got.enum]
                out.append(("XR-FGN-002",
                            f"参数 {k} 要的是 {v}，展开之后是 {cur}"
                            + (f"（档位只有 {names}）" if str(v) not in names else "")))
        elif got.value != v:
            out.append(("XR-FGN-002",
                        f"参数 {k} 要的是 {v}，展开之后是 {got.value}"))
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


def draft(files, top: str, clock: str = "clk", reset: str = "rst_n",
          defines=(), includes=()) -> str:
    """从别人的 RTL 出一份声明草稿：全部参数、按前缀归好的端点。

    草稿是起点不是终点——端点的 kind 与 role 要人去判，profile 要人去认。
    但「参数漏了一个」「端口名抄错了」这两类错，草稿一出来就不存在了。
    """
    from xirang_back import sv
    if not sv.available():
        raise Bad("出草稿要 pyslang：pip install xirang[sv]")
    got = sv.elaborate(files, top, {}, defines, includes)
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
    for k, p in sorted(pars.items()):
        v, w = p.value, p.width
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
        if p.enum:
            # 枚举参数只能用档位名覆盖。传整数 slang 会把它置成 <unset>，
            # 而 <unset> 不报错——配置静默没生效，后端量的是默认那一档
            cur = next((a for a, b in p.enum if b == v), p.enum[0][0])
            L += [f"  {kn}:", "    type: choice", "    values:"]
            L += [f"    - {a}" for a, _ in p.enum]
            L.append(f"    default: {cur}")
        elif w == 1:
            feats += [f"  {kn}:", "    type: bool", f"    default: {str(bool(v)).lower()}"]
        else:
            L += [f"  {kn}:", "    type: int", f"    default: {v}"]
    if skipped:
        L.insert(1, f"  # 这 {len(skipped)} 个不是标量，本版留在默认值上：")
        L[2:2] = [f"  #   {x}" for x in skipped]
    groups, loose = _groups(ports, {clock, reset})
    P = ["emit:", "- kind: foreign", "  lang: verilog", f"  top: {top}",
         "  rtl: []      # 填上综合视图的文件",
         "  # defines: []   # 要开的宏，如 RISCV_FORMAL",
         "  params:"] + proj + [
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


def elaborates(pkg: Pkg, vals) -> list[str]:
    """这一组参数展开得开吗。展不开就是这组取值非法，而清单没拦住它。

    用 slang 而不是 yosys：矩阵有几十个点，slang 快一个量级，而这一步要的只是
    「编得过」。真正要出网表时才轮到 yosys。
    """
    e = pkg.foreign_emit()
    if e is None or not sv.available():
        return []
    generate(pkg, vals)
    got = sv.elaborate(files(pkg, knobs=knobs_of(vals)), e["top"], bake(pkg, vals),
                       defines(pkg, vals), includes(pkg))
    if got is None:
        return []
    _, _, errs = got
    return [errs[0][:200]] if errs else []
