"""`tasks:`：清单里写命令，但划一条线。

> **命令只许消费已解出的配置，不许回头改它。**

`build.rs` 危险的地方不是「跑了个脚本」，而是它能反向注入配置——那样「每个值最终
是多少、被谁决定」就答不出来了，而那正是 computed 面板的全部价值。所以这里：

1. **在配置解出之后运行**，只读 `{{…}}` 占位符，**没有任何回写通道**；任务的
   stdout/stderr 不被解析成配置。
2. **`needs:` 只能指向内建阶段或别的任务**，成环在检查期报错，不在运行时死等。
3. **默认不进构建图**：`ran build` 不会替你跑 `fpga`，要 `ran run fpga`。
4. **任务不改变产物身份**：构建结果记录不把任务输出算进闭包。要算进去的东西
   应该是一个 `kind: foreign` 的包，不是一条任务。
5. **可复现性照报**：没钉版本的任务在报告里标 `unknown`，不伪装成通过。

这一件解开的是「生成器类上游」：Vortex 自带 `configure`、乘影是 Chisel 出 Verilog、
LiteX 与 retroSoC 都要先跑一遍它们自己的脚本。没有 `tasks:`，这几个上游只能手工
跑完再接，那一步没人记得住，也没人验得了。
"""
import graphlib
import json
import os
import re
import shlex
import signal
import subprocess
import sys

from xirang_core.manifest import Bad, Pkg

# 内建阶段：任务可以要求它们先发生，但我们不在这里替它们跑
STAGES = ("check", "gen", "build", "test")
KEYS = {"run", "needs", "env", "cwd", "desc", "timeout"}
SECS = 1800
NAME = re.compile(r"[a-z][a-z0-9-]*$")
# 旋钮名多是驼峰（numCores、fifoDepth）。原来只认小写，`{{knob.numCores}}` 对不上就原样
# 留在命令里照跑；现在认驼峰，写得像占位符却对不上的一律报错
# 依赖名照包名，带短横（`{{dep.ttsky25a-tinyqv}}`），只在 dep. 后面放开
HOLE = re.compile(r"{{\s*(dep\.[a-z][a-z0-9-]*|[a-z][A-Za-z0-9_.]*)\s*}}")
LOOKS = re.compile(r"{{[^{}]*}}")


def _one(name: str, spec) -> dict:
    if isinstance(spec, str):
        return {"run": spec}
    if not isinstance(spec, dict):
        raise Bad(f"XR-TASK-003 任务 {name} 只能写成一条命令，或带 run 的表")
    bad = set(spec) - KEYS
    if bad:
        raise Bad(f"XR-TASK-003 任务 {name} 有不认识的键 {sorted(bad)}"
                  f"（认 {sorted(KEYS)}）")
    if not spec.get("run"):
        raise Bad(f"XR-TASK-003 任务 {name} 没写 run")
    t = spec.get("timeout")
    if t is not None and (type(t) is not int or t <= 0):
        raise Bad(f"XR-TASK-003 任务 {name} 的 timeout 要写成正整数秒，写的是 {t!r}")
    return dict(spec)


def all_of(pkg: Pkg) -> dict[str, dict]:
    """清单里的任务，归一成 {名字: 表}。顺带把能在检查期查出来的都查掉。"""
    got = pkg.ip.get("tasks") or {}
    if not isinstance(got, dict):
        raise Bad(f"{pkg.path}: tasks 要写成 {{名字: 命令}}")
    out = {n: _one(n, s) for n, s in got.items()}
    for n in out:
        if not NAME.match(n):
            raise Bad(f"XR-TASK-003 任务名 {n} 不合规：小写字母开头，只许字母数字与短横")
        if n in STAGES:
            raise Bad(f"XR-TASK-003 任务名 {n} 与内建阶段同名")
    edges = {}
    for n, t in out.items():
        needs = t.get("needs") or []
        if isinstance(needs, str):
            needs = [needs]
        for d in needs:
            if d not in out and d not in STAGES:
                raise Bad(f"XR-TASK-003 任务 {n} 的 needs 指向 {d}，"
                          f"它既不是任务也不是内建阶段 {list(STAGES)}")
        edges[n] = {d for d in needs if d in out}
        out[n]["needs"] = list(needs)
    try:
        graphlib.TopologicalSorter(edges).prepare()
    except graphlib.CycleError as e:
        raise Bad(f"XR-TASK-002 任务的 needs 成环：{' -> '.join(e.args[1])}") from None
    return out


def plan(pkg: Pkg, name: str) -> list[str]:
    """跑这个任务之前要先发生什么。成环在这一步之前就报掉了。"""
    got = all_of(pkg)
    if name not in got:
        near = [n for n in got if n.startswith(name[:2])]
        raise Bad(f"XR-TASK-001 {pkg.name} 没有任务 {name}"
                  + (f"，是不是 {near[0]}" if near else "")
                  + (f"（有 {', '.join(sorted(got))}）" if got else "（它一个任务都没写）"))
    order, seen = [], set()

    def walk(n: str):
        if n in seen:
            return
        seen.add(n)
        for d in got[n].get("needs") or []:
            if d in got:
                walk(d)
            elif d not in order:
                order.append(d)          # 内建阶段：列出来给人看，不代跑
        order.append(n)

    walk(name)
    return order


def _dep(pkgs: dict[str, Pkg] | None, n: str) -> str:
    if not pkgs or n not in pkgs:
        raise Bad(f"XR-TASK-003 {{{{dep.{n}}}}}：这次的工作区里没有包 {n}")
    return str(pkgs[n].root)


def holes(pkg: Pkg, vals, out, pkgs: dict[str, Pkg] | None = None) -> dict:
    """占位符只读，且只有这几个。给不了的就报错，不静默留原样。

    `defines` 用到才算：它要展开源码，而生成器类上游的 setup 任务跑之前源码还不存在。
    """
    d = {"name": pkg.name, "root": str(pkg.root), "out": str(out)}
    # 依赖在哪由这次的工作区定：CI 里依赖克隆在别处，不一定是本仓的邻居。只认清单里声明过的依赖
    for n in pkg.ip.get("deps") or {}:
        d[f"dep.{n}"] = lambda n=n: _dep(pkgs, n)
    for k, v in (vals or {}).items():
        d[f"knob.{k}"] = str(getattr(v, "value", v))
    # 生成器类的上游要的是它自己那套 `-D` 串：Vortex 的 gen_config.py 收
    # `--cflags "-DVX_CFG_NUM_CORES=4 …"`，我们的旋钮投影出来正好是这个形状。
    # 这样「配置从哪来」仍然只有一个源头——清单，而不是两处各写一遍
    from xirang_gen import foreign
    if pkg.foreign_emit() is not None:
        d["defines"] = lambda: " ".join(f"-D{x}" for x in foreign.defines(pkg, vals))
    # 有些上游的配置改不了宏：CVA6 的字段全是 localparam，`-D` 碰不到它们，
    # 唯一的口子是它自己的 TARGET_CFG——换一个包。这类生成器要的是一份
    # 「解出来的配置」文件，不是一串 -D。落盘一次，把路径给它
    if vals:
        out.mkdir(parents=True, exist_ok=True)
        f = out / "resolved-knobs.json"
        f.write_text(json.dumps({k: getattr(v, "value", v) for k, v in vals.items()},
                                ensure_ascii=False, indent=2, sort_keys=True),
                     encoding="utf-8")
        d["resolved"] = str(f)
    return d


def fill(cmd: str, hole: dict[str, str], who: str) -> str:
    def sub(m):
        k = m.group(1)
        if k not in hole:
            raise Bad(f"XR-TASK-003 任务 {who} 用了认不得的占位符 {{{{{k}}}}}"
                      f"（有 {', '.join(sorted(hole))}）")
        v = hole[k]
        return v() if callable(v) else v
    for m in LOOKS.finditer(cmd):
        if not HOLE.fullmatch(m.group(0)):
            raise Bad(f"XR-TASK-003 任务 {who} 的 {m.group(0)} 不是合法的占位符"
                      f"（名字以小写字母开头，只含字母、数字、下划线与点）")
    return HOLE.sub(sub, cmd)


def run(pkg: Pkg, name: str, vals, out, dry: bool = False,
        secs: int | None = None, pkgs: dict[str, Pkg] | None = None) -> list[tuple[str, str]]:
    """跑任务。返回 [(名字, 结果)]，`dry` 只打印不跑。

    时限：调用方给的（`test.upstream` 那一条的 timeout）优先，其次任务自己的，都没有是 1800 秒。
    """
    got = all_of(pkg)
    hole = holes(pkg, vals, out, pkgs)
    done = []
    for step in plan(pkg, name):
        if step not in got:
            done.append((step, "内建阶段：本命令不代跑，需要就先自己跑一遍"))
            continue
        t = got[step]
        cmd = fill(t["run"], hole, step)
        if dry:
            done.append((step, cmd))
            continue
        env = dict(os.environ)
        # 任务里要回头调息壤（整片测试先 ran asic 出 .v）就用它：同一个解释器、同一份包
        env.setdefault("XIRANG", f"{sys.executable} -m xirang.cli")
        env.update({k: fill(str(v), hole, step) for k, v in (t.get("env") or {}).items()})
        cwd = pkg.root / fill(t.get("cwd") or ".", hole, step)
        lim = secs or t.get("timeout") or SECS
        try:
            # 自成一个进程组：超时要连它底下的 make、仿真器一起停。只停它自己的话，那些进程
            # 成了孤儿接着跑，还握着输出管道，communicate 要等它们自己退出
            p = subprocess.Popen(shlex.split(cmd), cwd=str(cwd), env=env,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 text=True, errors="replace", start_new_session=True)
        except FileNotFoundError as e:
            raise Bad(f"XR-TASK-004 任务 {step} 要的程序不在：{e.filename}") from None
        try:
            so, se = p.communicate(timeout=lim)
        except subprocess.TimeoutExpired:
            if hasattr(os, "killpg"):
                os.killpg(p.pid, signal.SIGKILL)
            else:
                p.kill()
            p.communicate()
            raise Bad(f"XR-TASK-004 任务 {step} 超过 {lim} 秒还没结束") from None
        if p.returncode != 0:
            tail = (se or so or "").strip().splitlines()[-8:]
            raise Bad(f"XR-TASK-004 任务 {step} 退出码 {p.returncode}："
                      + chr(10) + chr(10).join(tail))
        done.append((step, f"过了（{len(so.splitlines())} 行输出）"))
    return done


def tools(pkg: Pkg) -> list[str]:
    """任务们要用到的外部程序。doctor 拿它去查版本——没钉版本的标 unknown。"""
    out = set()
    for t in all_of(pkg).values():
        try:
            argv = shlex.split(t["run"])
        except ValueError:
            continue
        if argv:
            out.add(argv[0])
    return sorted(out)
