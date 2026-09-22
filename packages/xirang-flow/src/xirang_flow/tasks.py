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
import os
import re
import shlex
import subprocess

from xirang_core.manifest import Bad, Pkg

# 内建阶段：任务可以要求它们先发生，但我们不在这里替它们跑
STAGES = ("check", "gen", "build", "test")
KEYS = {"run", "needs", "env", "cwd", "desc"}
NAME = re.compile(r"[a-z][a-z0-9-]*$")
HOLE = re.compile(r"{{([a-z][a-z0-9_.]*)}}")


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


def holes(pkg: Pkg, vals, out) -> dict[str, str]:
    """占位符只读，且只有这几个。给不了的就报错，不静默留原样。"""
    d = {"name": pkg.name, "root": str(pkg.root), "out": str(out)}
    for k, v in (vals or {}).items():
        d[f"knob.{k}"] = str(getattr(v, "value", v))
    return d


def fill(cmd: str, hole: dict[str, str], who: str) -> str:
    def sub(m):
        k = m.group(1)
        if k not in hole:
            raise Bad(f"XR-TASK-003 任务 {who} 用了认不得的占位符 {{{{{k}}}}}"
                      f"（有 {', '.join(sorted(hole))}）")
        return hole[k]
    return HOLE.sub(sub, cmd)


def run(pkg: Pkg, name: str, vals, out, dry: bool = False,
        secs: int = 1800) -> list[tuple[str, str]]:
    """跑任务。返回 [(名字, 结果)]，`dry` 只打印不跑。"""
    got = all_of(pkg)
    hole = holes(pkg, vals, out)
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
        env.update({k: fill(str(v), hole, step) for k, v in (t.get("env") or {}).items()})
        cwd = pkg.root / fill(t.get("cwd") or ".", hole, step)
        try:
            r = subprocess.run(shlex.split(cmd), cwd=str(cwd), env=env,
                               capture_output=True, text=True, errors="replace",
                               timeout=secs)
        except FileNotFoundError as e:
            raise Bad(f"XR-TASK-004 任务 {step} 要的程序不在：{e.filename}") from None
        except subprocess.TimeoutExpired:
            raise Bad(f"XR-TASK-004 任务 {step} 超过 {secs} 秒还没结束") from None
        if r.returncode != 0:
            tail = (r.stderr or r.stdout or "").strip().splitlines()[-8:]
            raise Bad(f"XR-TASK-004 任务 {step} 退出码 {r.returncode}："
                      + chr(10) + chr(10).join(tail))
        done.append((step, f"过了（{len(r.stdout.splitlines())} 行输出）"))
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
