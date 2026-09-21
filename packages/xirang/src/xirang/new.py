"""`ran new`：从 xrskel 铺一个新仓。

模板是包数据（`xirang/skel/`，xrskel 的 submodule），所以运行时零网络零 git。
两条落盘规矩写在 `skel.yaml` 里，这里只照着执行：`.in` 后缀脱掉，路径里 `dot.`
开头的那一段还原成 `.`。

占位符只认 `{{…}}` 且前面不是 `$`——GitHub Actions 的 `${{ }}` 不是占位符，
早先把它当占位符会让每个工作流文件都报「不认识的占位符」。
"""
import datetime
import pathlib
import re
import subprocess
import sys

import yaml

from xirang_core.manifest import Bad

PH = re.compile(r"(?<!\$)\{\{\s*(\w+)\s*\}\}")
NAME_OK = re.compile(r"[a-z][a-z0-9]*\Z")
ORG = "https://github.com/Tape-Out"


def skel_root() -> pathlib.Path:
    """模板在哪。包数据优先，源码树次之。"""
    here = pathlib.Path(__file__).resolve().parent / "skel"
    if (here / "skel.yaml").is_file():
        return here
    raise Bad("XR-NEW-001 找不到模板。装的是源码树时先跑 "
              "`git submodule update --init packages/xirang/src/xirang/skel`；"
              "装的是发布包时这条不该出现，请报一个 issue")


def load() -> tuple[pathlib.Path, dict]:
    root = skel_root()
    return root, yaml.safe_load((root / "skel.yaml").read_text(encoding="utf-8"))


def vars_for(name: str) -> dict:
    if not NAME_OK.match(name):
        raise Bad(f"XR-NEW-006 包名 {name!r} 不合规。只收小写字母与数字、以字母开头，"
                  f"因为它同时是 BSV 包名的词根与仓名")
    return {
        "name": name,
        "Name": name[0].upper() + name[1:],
        "NAME": name.upper(),
        "org": ORG,
        "year": str(datetime.date.today().year),
    }


def _sub(text: str, v: dict, where: str) -> str:
    def one(m):
        k = m.group(1)
        if k not in v:
            raise Bad(f"XR-NEW-003 {where}：占位符 {{{{{k}}}}} 不在表里。"
                      f"可用的是 {', '.join(sorted(v))}")
        return v[k]
    return PH.sub(one, text)


def _land(rel: pathlib.Path, v: dict) -> pathlib.Path:
    parts = []
    for p in rel.parts:
        p = _sub(p, v, f"路径 {rel}")
        if p.startswith("dot."):
            p = "." + p[4:]
        parts.append(p)
    out = pathlib.Path(*parts)
    return out.with_name(out.name[:-3]) if out.name.endswith(".in") else out


def plan_files(template: str, name: str) -> tuple[dict, set]:
    root, doc = load()
    if template not in doc["templates"]:
        raise Bad(f"XR-NEW-002 没有模板 {template!r}。可选："
                  f"{', '.join(sorted(doc['templates']))}")
    v = vars_for(name)
    roots = [root / doc["includes"][i] for i in doc["templates"][template]["include"]]
    roots.append(root / "templates" / template)

    files: dict[pathlib.Path, bytes] = {}
    dirs: set[pathlib.Path] = set()
    for r in roots:
        if not r.is_dir():
            raise Bad(f"XR-NEW-001 模板里没有 {r.name}，这份 skel 不完整")
        for f in sorted(r.rglob("*")):
            if not f.is_file():
                continue
            out = _land(f.relative_to(r), v)
            if out.name == ".gitkeep":
                dirs.add(out.parent)      # 占位文件不落盘，目录要留
                continue
            try:
                files[out] = _sub(f.read_text(encoding="utf-8"),
                                  v, f"{r.name}/{f.relative_to(r)}").encode()
            except UnicodeDecodeError:
                files[out] = f.read_bytes()
    return files, dirs


def write(files: dict, dirs: set, dest: pathlib.Path) -> None:
    if dest.exists() and any(dest.iterdir()):
        raise Bad(f"XR-NEW-005 {dest} 已存在且非空，一个文件都没写。"
                  f"换个目录，或者清空它")
    for d in dirs:
        (dest / d).mkdir(parents=True, exist_ok=True)
    for rel, data in files.items():
        f = dest / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(data)


def init_vcs(dest: pathlib.Path, vcs: str) -> str:
    if vcs == "none":
        return "没有初始化版本库"
    if vcs == "git":
        r = subprocess.run(["git", "init", "-q", "-b", "main", str(dest)],
                           capture_output=True, text=True)
        return "git init" if r.returncode == 0 else f"git init 没成：{r.stderr.strip()}"
    if vcs == "svn":
        r = subprocess.run(["svnadmin", "create", str(dest / ".svnrepo")],
                           capture_output=True, text=True)
        return "svnadmin create" if r.returncode == 0 else f"svn 没成：{r.stderr.strip()}"
    raise Bad(f"XR-NEW-007 不认识的版本库 {vcs!r}，可选 git、svn、none")


def ask(doc: dict, name: str | None, template: str | None) -> tuple[str, str]:
    """vite 式两级：先问大类，IP 再问变体。非交互时信息不全就报错，绝不挂着等。"""
    tty = sys.stdin.isatty() and sys.stdout.isatty()
    if not tty and (not name or not template):
        raise Bad("XR-NEW-002 非交互环境下 NAME 与 -t 都要给全。\n"
                  f"  可选模板：{', '.join(sorted(doc['templates']))}\n"
                  f"  例如：ran new myip -t ip/regmap")

    if not name:
        name = input("包名（小写字母与数字）: ").strip()
    vars_for(name)

    if not template:
        groups: dict[str, list[str]] = {}
        for k, t in doc["templates"].items():
            groups.setdefault(t["group"], []).append(k)
        gs = sorted(groups)
        print("\n建什么：")
        for i, g in enumerate(gs, 1):
            one = groups[g][0]
            title = doc["templates"][one]["title"].split("，")[0]
            print(f"  {i}) {title}")
        g = gs[_pick(len(gs)) - 1]

        opts = sorted(groups[g])
        if len(opts) == 1:
            template = opts[0]
        else:
            print()
            for i, k in enumerate(opts, 1):
                t = doc["templates"][k]
                print(f"  {i}) {t['title']}    {t['hint']}")
            template = opts[_pick(len(opts)) - 1]
    return name, template


def _pick(n: int) -> int:
    while True:
        s = input(f"选 1-{n}: ").strip()
        if s.isdigit() and 1 <= int(s) <= n:
            return int(s)


def cmd_new(args) -> int:
    _, doc = load()

    if args.list:
        print("模板：")
        for k in sorted(doc["templates"]):
            t = doc["templates"][k]
            print(f"  {k:<12} {t['title']}")
            print(f"  {'':<12} {t['hint']}")
        return 0

    name, template = ask(doc, args.name, args.template)
    dest = pathlib.Path(args.dir) if args.dir else pathlib.Path.cwd() / name

    files, dirs = plan_files(template, name)     # 全渲染到内存
    write(files, dirs, dest)                     # 全过才落盘
    note = init_vcs(dest, args.vcs)

    print(f"\n{name} 就位：{dest}（{len(files)} 个文件，{note}）")
    print("下一步：")
    print(f"  cd {dest}")
    if template.startswith("ip/") and "regmap" in template:
        print(f"  ran gen {name} --tb -o build    # 寄存器组与一致性测试")
    print(f"  ran test {name}                # 头一次会说没有价目表，那是实话")
    print(f"  ran recal {name} --init --apply  # 量第一份价目表，要 PDK")
    return 0
