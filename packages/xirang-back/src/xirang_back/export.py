"""三个导出目标。

总原则：**别人的格式一律是导出目标，不是执行路径。** `xirang` 内部只有一条路
走到底，这些只是同一份解析模型的投影。

  core     fusesoc CAPI2。加的段必须是**上游会忽略而不是报错**的，否则
           「产出可被原版 fusesoc 消费」那条铁律就断了
  kconfig  嵌套菜单。装配的每一层都是一个 menu，子包的旋钮自动嵌进去
  tar      零工具依赖的包：生成好的源码 + 一个 Makefile
"""
from __future__ import annotations

import io
import pathlib
import tarfile

import yaml

from xirang_core.model import Instance, Resolved


def _sym(*parts) -> str:
    return "_".join(p.upper().replace("-", "_") for p in parts if p)


# ---------------------------------------------------------------- fusesoc

def to_core(res: Resolved, pkgs, files: list[str]) -> str:
    top = pkgs[res.top]
    d = {
        "name": f"::{res.top}:{top.ip['version']}",
        "description": (top.ip.get("identity") or {}).get("summary", res.top),
        "filesets": {
            "rtl": {"files": sorted(files), "file_type": "verilogSource"},
        },
        "targets": {
            "default": {"filesets": ["rtl"]},
        },
        # 我们的增量。上游 fusesoc 不认识顶层未知键，会忽略而不是报错——
        # 这正是「产出可被原版消费」那条铁律的落点，T5 测的就是它。
        "xirang": {
            "spec": top.ip.get("spec"),
            "bus": res.bus,
            "area_um2_predicted": round(res.area_um2, 2),
            "instances": [
                {"name": i.name, "of": i.of,
                 "addr": (f"{i.addr:#010x}" if i.addr is not None else None),
                 "with": {k: v.value for k, v in i.values.items()}}
                for _, i in res.walk()
            ],
        },
    }
    return "CAPI=2:\n" + yaml.safe_dump(d, sort_keys=False, allow_unicode=True)


# ---------------------------------------------------------------- kconfig

def _knob_entries(inst: Instance, pkg, prefix: str) -> list[str]:
    L = []
    knobs = pkg.knobs()
    for name, v in inst.values.items():
        spec = knobs.get(name, {})
        sym = _sym(prefix, name)
        desc = spec.get("desc") or name
        if spec.get("type") == "bool":
            L += [f'config {sym}', f'\tbool "{desc}"',
                  f'\tdefault {"y" if v.value else "n"}']
            for dep in spec.get("depends", []) or []:
                L.append(f"\tdepends on {_sym(prefix, dep)}")
            if v.area_um2:
                L.append(f'\thelp\n\t  Costs about {v.area_um2:.0f} um2 at this setting.')
        elif spec.get("type") == "choice":
            L += [f'choice', f'\tprompt "{desc}"']
            for val in spec["values"]:
                L.append(f'\tconfig {_sym(prefix, name, val)}')
                L.append(f'\t\tbool "{val}"')
            L.append("endchoice")
        else:
            L += [f'config {sym}', f'\tint "{desc}"', f'\tdefault {v.value}']
            r = spec.get("range")
            if r:
                L.append(f"\trange {r[0]} {r[1]}")
        L.append("")
    return L


def to_kconfig(res: Resolved, pkgs) -> str:
    """装配的每一层是一个 menu，子包的旋钮自动嵌进去，深度不限。"""
    L = [f"# 由 xirang 生成。菜单层次与装配层次一一对应。",
         f'mainmenu "{res.top}"', ""]

    def rec(insts, depth, prefix):
        for i in insts:
            p = pkgs[i.of]
            title = (p.ip.get("identity") or {}).get("display_name", i.of)
            addr = f"  @ {i.addr:#010x}" if i.addr is not None else ""
            L.append(f'menu "{i.name} — {title}{addr}"')
            L.extend(_knob_entries(i, p, _sym(prefix, i.name)))
            if i.children:
                rec(i.children, depth + 1, _sym(prefix, i.name))
            L.append("endmenu")
            L.append("")

    rec(res.instances, 0, "")
    return "\n".join(L)


# ---------------------------------------------------------------- tar

MAKEFILE = """# 由 xirang 生成。这个包不依赖 xirang，只要 bsc 就能重跑。
TOP  ?= {top}
BSC  ?= bsc
VDIR ?= rtl

all: $(VDIR)/$(TOP).v

$(VDIR)/$(TOP).v: $(wildcard bsv/*.bsv)
\tmkdir -p $(VDIR) build
\t$(BSC) -verilog -u -vdir $(VDIR) -bdir build -info-dir build -p bsv:+ -g $(TOP) bsv/{topsrc}

clean:
\trm -rf $(VDIR) build

.PHONY: all clean
"""


def to_tar(res: Resolved, pkgs, build_dir: pathlib.Path, out: pathlib.Path,
           top_module: str) -> int:
    """零工具依赖的包。判据是：解开之后只要 bsc 就能重跑，不需要 xirang。"""
    n = 0
    with tarfile.open(out, "w:gz") as tf:
        for f in sorted((build_dir / "bsv").glob("*.bsv")):
            tf.add(f, arcname=f"{res.top}/bsv/{f.name}")
            n += 1
        for name in sorted({i.of for _, i in res.walk()}) + [res.top]:
            p = pkgs.get(name)
            if not p:
                continue
            src = p.root / "bsv"
            if src.is_dir():
                for f in sorted(list(src.glob("*.bsv"))
                                + list(src.glob("*.bs"))):
                    tf.add(f, arcname=f"{res.top}/bsv/{f.name}")
                    n += 1
            for m in ("ip.yaml", "regmap.yaml"):
                q = p.root / m
                if q.exists():
                    tf.add(q, arcname=f"{res.top}/manifests/{name}-{m}")
        for extra in ("regmap.json", "addr_map.md"):
            q = build_dir / extra
            if q.exists():
                tf.add(q, arcname=f"{res.top}/{extra}")
        mk = MAKEFILE.format(top=top_module, topsrc=f"{top_module[2:]}Pkg.bsv")
        info = tarfile.TarInfo(f"{res.top}/Makefile")
        data = mk.encode()
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    return n
