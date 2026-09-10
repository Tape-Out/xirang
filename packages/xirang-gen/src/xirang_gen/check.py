"""IP 的实现有没有兑现清单——认识 BSV 的只有这个包，所以这两条门禁住在这里。
"""
import pathlib
import re

from xirang_core.manifest import Bad, Pkg


def dead_inputs(pkg: Pkg) -> list[str]:
    """引脚驱进来的值，模块里得真的读。

    `i2c` 的 `scl_in` 就这么躺着：接口上有、`always_enabled` 每拍都驱、
    模块里一次也没读——于是时钟延展完全不认，而调度门禁与寄存器一致性
    都查不出来。同一类的还有 `aclint` 那根没人接的 SSWI 出线。

    判据很直接：`mkBypassWire` / `mkDWire` 声明出来的线，除了声明那一行
    与写它的那一行之外，还得在别处出现过。

    与 test.unused、test.noarea 一样双向成立：写进 test.deadread 的名字
    如果其实已经被读了，同样报错——豁免名单不许留着过期的条目。
    """
    bsv = pkg.root / "bsv"
    if not bsv.is_dir():
        return []
    out: list[str] = []
    dead: list[str] = []
    for f in sorted(bsv.glob("*.bsv")) + sorted(bsv.glob("*.bs")):
        src = f.read_text(encoding="utf-8", errors="ignore")
        for m in re.finditer(r"^\s*Wire#\([^;]*?\)\s+(\w+)\s*<-\s*mk(?:Bypass|D)Wire",
                             src, re.M):
            name = m.group(1)
            uses = 0
            for line in src.splitlines():
                bare = line.split("//")[0]
                if re.search(rf"\b{name}\b", bare) is None:
                    continue
                if re.search(rf"\b{name}\s*<-\s*mk", bare):
                    continue          # 声明
                if re.search(rf"\b{name}\s*(?:\._write\(|<=)", bare):
                    continue          # 只是在写它
                uses += 1
            if uses == 0:
                dead.append(name)
    declared = list((pkg.ip.get("test") or {}).get("deadread") or [])
    for name in dead:
        if name not in declared:
            out.append(f"{name} 只写不读——引脚驱进来了，逻辑里一次也没用过。"
                       f"确实不需要就写进 ip.yaml 的 test.deadread 并说明理由")
    stale = [x for x in declared if x not in dead]
    if stale:
        out.append(f"test.deadread 里这几个其实已经被读了，删掉 {sorted(stale)}")
    return out


def unused_methods(pkg: Pkg, gen_file: pathlib.Path) -> list[str]:
    """寄存器接口暴露的方法，实现里得真的用到。

    只在清单里、实现里没人读的字段是最贵的错：不报错、不告警，面板还会
    认真地把它标价为零。`emac` 的 `ctrl.loop` 就这么躺着——寄存器写得进去，
    环回一次也没发生过。

    确实用不到的，写进 ip.yaml 的 test.unused 并说明理由。那份名单反过来
    也要成立：名单里的方法一旦被用上了，或者压根不存在，同样报错。
    """
    src = "".join(
        f.read_text(encoding="utf-8", errors="ignore")
        for f in sorted((pkg.root / "bsv").glob("*.bsv"))
        + sorted((pkg.root / "bsv").glob("*.bs")))
    # 只看本包这一份。输出目录是跨包复用的，通配一扫就把上一个包留下的
    # 寄存器组也算进来，报出一长串别人的方法。
    if not gen_file.is_file():
        raise Bad(f"{pkg.name} 有寄存器图，却没生成出 {gen_file.name}")
    out: list[str] = []
    names: list[str] = []
    ifc = gen_file.read_text(encoding="utf-8").split("endinterface")[0]
    # 返回类型里有空格的方法（Bit#(TLog#(TAdd#(contexts, 1))) 这种）不能用
    # 「method 类型 名字」去套——原来的正则把它们整个漏掉，plic 的两个下标
    # 方法于是从来没被查过。改成：method 之后第一个「标识符紧跟 ; 或 (」。
    for line in ifc.splitlines():
        m = re.search(r"\bmethod\b(.*)", line.split("//")[0])
        if not m:
            continue
        g = re.search(r"(\w+)\s*[;(]", m.group(1))
        if g and g.group(1) != "regs" and g.group(1) not in names:
            names.append(g.group(1))
    declared = list((pkg.ip.get("test") or {}).get("unused") or [])
    bogus = [n for n in declared if n not in names]
    if bogus:
        out.append(f"test.unused 提到寄存器接口里没有的方法 {sorted(bogus)}")
    dead = [n for n in names if f".{n}" not in src]
    stale = [n for n in declared if n in names and n not in dead]
    if stale:
        out.append(f"test.unused 里这几个其实已经用上了，删掉 {sorted(stale)}")
    left = [n for n in dead if n not in declared]
    if left:
        out.append(f"寄存器图声明了、实现里没人用：{sorted(left)}"
                   f"——要么实现，要么写进 ip.yaml 的 test.unused 并说明为什么")
    return out
