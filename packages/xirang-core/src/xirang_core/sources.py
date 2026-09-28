"""黑盒的源码条目展开成一份清单：文件、宏、include 目录、库目录。

五种写法：文件、带 `when` 的文件、`glob`、`regex`、`flist`。各工具拿同一份展开结果。
"""
import dataclasses
import pathlib
import re

from xirang_core.manifest import Bad

KINDS = ("path", "glob", "regex", "flist")
EXTRA = {"path": set(), "glob": {"exclude"}, "regex": set(), "flist": {"env"}}
VAR = re.compile(r"\$\{(\w+)\}|\$(\w+)")


@dataclasses.dataclass
class Sources:
    files: list = dataclasses.field(default_factory=list)
    defines: list = dataclasses.field(default_factory=list)
    incdirs: list = dataclasses.field(default_factory=list)
    libdirs: list = dataclasses.field(default_factory=list)
    libext: list = dataclasses.field(default_factory=list)
    origin: dict = dataclasses.field(default_factory=dict)
    ignored: list = dataclasses.field(default_factory=list)

    def add(self, f: pathlib.Path, why: str):
        if f not in self.origin:
            self.files.append(f)
            self.origin[f] = why


def kind(entry) -> str:
    if isinstance(entry, str):
        return "path"
    got = [k for k in KINDS if k in entry]
    if len(got) != 1:
        raise Bad(f"源码条目要且只要 {'/'.join(KINDS)} 之一：{entry}")
    k = got[0]
    bad = set(entry) - {k, "when"} - EXTRA[k]
    if bad:
        raise Bad(f"{k} 条目不认 {sorted(bad)}：{entry}")
    return k


def expand(root: pathlib.Path, entries, knobs=None) -> Sources:
    """`knobs` 为 None 时不看 `when`，全取：起草与静态核对时还没有解出的配置。"""
    root = pathlib.Path(root)
    out = Sources()
    for e in entries or []:
        k = kind(e)
        if isinstance(e, dict) and knobs is not None and any(
                knobs.get(n) != v for n, v in (e.get("when") or {}).items()):
            continue
        if k == "path":
            p = e if isinstance(e, str) else e["path"]
            if not (root / p).is_file():
                raise Bad(f"XR-SRC-004 {p} 不在 {root} 下")
            out.add(root / p, p)
        elif k == "glob":
            ex = e.get("exclude") or []
            hit = sorted(f for f in root.glob(e["glob"]) if f.is_file()
                         and not any(f.relative_to(root).full_match(x) for x in ex))
            if not hit:
                raise Bad(f"XR-SRC-001 glob {e['glob']} 在 {root} 下一个文件都没匹配到")
            for f in hit:
                out.add(f, f"glob {e['glob']}")
        elif k == "regex":
            rx = re.compile(e["regex"])
            hit = sorted(f for f in root.rglob("*") if f.is_file() and ".git" not in f.parts
                         and rx.search(f.relative_to(root).as_posix()))
            if not hit:
                raise Bad(f"XR-SRC-001 regex {e['regex']} 在 {root} 下一个文件都没匹配到")
            for f in hit:
                out.add(f, f"regex {e['regex']}")
        else:
            flist(root / e["flist"], e.get("env") or {}, root, root, out)
    return out


def _sub(tok: str, env: dict, where: pathlib.Path) -> str:
    def one(m):
        name = m.group(1) or m.group(2)
        if name not in env:
            raise Bad(f"XR-SRC-002 {where} 用了 ${{{name}}}，清单的 env 里没有给值")
        return str(env[name])
    return VAR.sub(one, tok)


def flist(path: pathlib.Path, env: dict, root: pathlib.Path, base: pathlib.Path,
          out: Sources, seen=None) -> Sources:
    """读一份 Flist。`base` 是这份里相对路径的基准：`-f` 是包根，`-F` 是它自己的目录。"""
    seen = set() if seen is None else seen
    if path in seen:
        raise Bad(f"Flist {path} 嵌套成环")
    if not path.is_file():
        raise Bad(f"XR-SRC-004 Flist {path} 不存在")
    seen = seen | {path}
    text = re.sub(r"//[^\n]*|^\s*#[^\n]*", "", path.read_text(), flags=re.M)
    toks = [_sub(t, env, path) for t in text.split()]
    res = lambda p: pathlib.Path(p) if pathlib.Path(p).is_absolute() else base / p
    i = 0
    while i < len(toks):
        t = toks[i]
        if t.startswith("+incdir+"):
            out.incdirs += [res(d) for d in t[len("+incdir+"):].split("+") if d]
        elif t.startswith("+define+"):
            out.defines += [d for d in t[len("+define+"):].split("+") if d]
        elif t.startswith("+libext+"):
            out.libext += [x for x in t[len("+libext+"):].split("+") if x]
        elif t in ("-f", "-F", "-y", "-v") and i + 1 < len(toks):
            arg = toks[i + 1]
            i += 1
            if t == "-f":
                flist(res(arg), env, root, root, out, seen)
            elif t == "-F":
                sub = res(arg)
                flist(sub, env, root, sub.parent, out, seen)
            elif t == "-y":
                out.libdirs.append(res(arg))
            else:
                out.add(res(arg), f"flist {path.name}")
        elif t.startswith(("-", "+")):
            out.ignored.append(f"{path.name}: {t}")
        else:
            out.add(res(t), f"flist {path.name}")
        i += 1
    return out
