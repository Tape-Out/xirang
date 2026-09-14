<h1 align="center">息壤 XiRang</h1>

<div align="right"><sub>硬件的包管理器与装配器</sub></div>

---

<br />

<p align="center">
  <a href="https://github.com/Tape-Out/xirang/blob/main/pyproject.toml"><img alt="Python" src="https://img.shields.io/badge/3.14+-3776AB?logo=python&logoColor=white"></a>
  <a href="https://github.com/Tape-Out/xirang/actions/workflows/ci.yml"><img alt="CI" src="https://img.shields.io/github/actions/workflow/status/Tape-Out/xirang/ci.yml?branch=main&label=CI&logo=githubactions&logoColor=white"></a>
  <a href="https://github.com/Tape-Out/spec"><img alt="Spec" src="https://img.shields.io/badge/Spec-0.1-006DE0?logo=readthedocs&logoColor=white"></a>
  <a href="https://github.com/B-Lang-org/bsc"><img alt="BSV / BH" src="https://img.shields.io/badge/BSV%20%2F%20BH-5C2D91"></a>
  <a href="https://github.com/Tape-Out/xirang/blob/main/LICENSE"><img alt="License" src="https://img.shields.io/badge/Apache--2.0-D22128?logo=apache&logoColor=white"></a>
</p>

<div align=center>
  <sub>一个数据模型，一条执行路径；每个旋钮查得到来历，每个特性标着实测面积。</sub>
</div>

---

<sub>规范：[`Tape-Out/spec`](https://github.com/Tape-Out/spec) · IP 库：[`Tape-Out`](https://github.com/Tape-Out) · 编译器：[`bsc`](https://github.com/B-Lang-org/bsc) · 出处：[`FuseSoC`](https://github.com/olofk/fusesoc)</sub>

<br />

## 安装

    uv tool install --from 'git+https://github.com/Tape-Out/xirang#subdirectory=packages/xirang' xirang

长命令 `xirang`，短命令 `ran`，指向同一个入口。

解析、层叠、计价与导出只要 Python 3.14；跑测试矩阵、生成 Verilog 另需 `bsc`；量面积再加 `ecc`、一份 PDK，以及 `ecc` 调用的 `yosys`。

## 用法

在带 `workspace.yaml` 的目录里跑。工作区清单列出这次流片用到的包：

    xirang-workspace: 1
    members:
    - gpio
    - uart
    - soc-mcu

常用的几条：

    ran status                              # 清单、源码、锁三者对不对得上
    ran tree soc-mcu                        # 装配层次，每个实例标着面积
    ran config uart -s fifoDepth=16         # 每个旋钮的最终取值与合计面积
    ran config soc-mcu --why gpiob.numPins  # 一个旋钮由哪一层定下
    ran test uart                           # 在整张测试矩阵上验一遍
    ran lock soc-mcu                        # 解析依赖，钉进 xirang.lock
    ran recal uart --apply                  # 重测价目表并写回 ip.yaml
    ran build soc-mcu                       # 生成并综合
    ran export soc-mcu -f core              # 导出成 FuseSoC 的 .core

旋钮的来历按层叠从低到高列出，打勾的那一层生效：

    $ ran config soc-mcu --why gpiob.numPins
    gpiob.numPins = 8

      层叠（低到高，越靠下越优先）：
          bsv-default  32         gpio/ip.yaml (default)
        ✔ instance     8          soc-mcu/ip.yaml:12

测试矩阵由参数范围与特性开关派生，解析到同一配置的点只跑一次：

    $ ran test emac
    emac  矩阵 7 点
      ✔ Default                bufWords=512 promisc=False
      ✔ BufWords64PromiscOff   bufWords=64 promisc=False
      ✔ BufWords1024PromiscOn  bufWords=1024 promisc=True
      ✔ PromiscOn              bufWords=512 promisc=True
      同 PromiscOff             解析下来与 Default 是同一点
      同 BufWords64             解析下来与 BufWords64PromiscOff 是同一点
      ✔ BufWords1024           bufWords=1024 promisc=False

    5 点实测，0 点不过

## 清单

叶子 IP 写一份 `ip.yaml`，参数带范围，特性带实测价：

    name: uart
    version: 0.1.0
    kind: ip
    lang: bsv
    params:
      fifoDepth:
        type: int
        default: 8
        range: [1, 64]
    features:
      parity:
        type: bool
        default: false
        area:
          points:
            '1': 23.52

装配也是一份 `ip.yaml`，只列实例、取值与地址，自己不写 RTL：

    name: soc-switch
    bus: apb4
    instances:
    - name: sw0
      of: eswitch
      with: {ports: 4, macEntries: 16, vlan: false}
      addr: 0x10000000
    deps:
      hwcore: ^0.1
      amba: ^0.1

寄存器写在 `regmap.yaml` 里，属性名沿用 SystemRDL 2.0（`sw`、`hw`、`onwrite`、`hwset` 等），由它生成寄存器组、C 头与测试台。字段细则见 [`spec/regmap.md`](https://github.com/Tape-Out/spec/blob/main/regmap.md)。

## 门禁

推之前，`ran status`、`ran lint` 与 `ran test` 会拦下这些：

| 门禁 | 拦下什么 |
|:--:|:--:|
| 死输入 | 引脚驱进来的值，模块里一次也没读 |
| 未用方法 | 寄存器接口暴露的方法，实现里没有用到 |
| 地址重叠 | 某个合法配置下，两个寄存器占同一个地址 |
| 调度 | 装配生成 Verilog 时 `bsc` 报出的规则冲突 |
| 价目表失效 | 价目表量的是另一份生成产物 |
| 未计价 | 改这个旋钮，面积预测不动 |
| 未实测 | 标了价，却没有一行实测打开过这个特性 |
| 曲线平坦 | 参数改了，量出来的面积不变 |
| 低估 | 预测面积低于实测 |
| 工作区 | 清单、源码与锁不一致 |

## 八个包

| 包 | 管什么 | 什么变了会逼它改 |
|:--:|:--:|:--:|
| `xirang-core` | 清单 schema · 层叠与约束求解 · 依赖与锁 · 测试矩阵派生 | 规范涨版本 |
| `xirang-gen` | 寄存器图 → BSV / C 头 / 测试台 · 契约 → 扁平顶层 · 装配 → 顶层 | 目标语言、契约形态、总线种类 |
| `xirang-area` | 价目表 · 面积预测 · 回填 | 面积模型、测量流程 |
| `xirang-back` | 驱动综合与仿真工具 | EDA 工具与版本 |
| `xirang-out` | 投影与读回：`resolved` · FuseSoC `.core` · Kconfig · tar | 别人的格式 |
| `xirang-ws` | 工作区：`workspace.yaml` · 成员 · `status` | 产品怎么组装 |
| `xirang-flow` | 编排：建一个叶子 · 过一个装配 · 跑一张矩阵 | 质量策略 |
| `xirang` | 命令行 | 用户界面 |

依赖无环，`xirang-core` 与 `xirang-back` 在底，没有包反向依赖命令行。`xirang-back` 不认识息壤的数据模型，可以单独拿去驱动工具。

## 开发

    git clone https://github.com/Tape-Out/xirang && cd xirang
    uv sync
    uv run ruff check packages tests
    uv run python tests/test_structure.py

CI 跑的是同样的检查，另外确认两个命令都起得来。

## 相关项目

- [Tape-Out/spec](https://github.com/Tape-Out/spec)：`ip.yaml`、`regmap.yaml` 与契约的规范。
- [B-Lang-org/bsc](https://github.com/B-Lang-org/bsc)：BSV 与 BH 的编译器。
- [olofk/fusesoc](https://github.com/olofk/fusesoc)：这个仓的历史始于它的一份 fork，提交全部保留。工作树已经重写，导出的 `.core` 仍能被上游 FuseSoC 直接读入。

## 许可证

Apache-2.0。
