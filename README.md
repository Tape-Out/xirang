<div align="center">

<br>

<img src="docs/logo.svg" width="116" alt="息壤">

### 息壤 · XiRang

<sub><b>硬件的包管理器与装配器</b></sub>

<br>

<a href="https://github.com/Tape-Out/xirang/actions/workflows/ci.yml"><img alt="CI" src="https://img.shields.io/github/actions/workflow/status/Tape-Out/xirang/ci.yml?branch=main&label=ci&style=flat-square&labelColor=2E3440&color=4C6A92"></a>
<a href="pyproject.toml"><img alt="Python" src="https://img.shields.io/badge/python-3.14+-4C6A92?style=flat-square&labelColor=2E3440"></a>
<a href="https://github.com/Tape-Out/spec"><img alt="spec" src="https://img.shields.io/badge/spec-v0.2.3-5E81AC?style=flat-square&labelColor=2E3440"></a>
<a href="https://github.com/B-Lang-org/bsc"><img alt="BSV" src="https://img.shields.io/badge/BSV%20%C2%B7%20BH-8FC6D6?style=flat-square&labelColor=2E3440"></a>
<img alt="license" src="https://img.shields.io/badge/MIT%20%C2%B7%20Apache--2.0%20%C2%B7%20MulanPSL--2.0-6D8CB5?style=flat-square&labelColor=2E3440">

<br><br>

<sub>
  <a href="#装">装</a> ·
  <a href="#用">用</a> ·
  <a href="#清单">清单</a> ·
  <a href="#门禁">门禁</a> ·
  <a href="#八个包">八个包</a> ·
  <a href="#许可">许可</a>
</sub>

<br>

<sub>一个数据模型，一条执行路径。<b>每个旋钮查得到来历，每个特性标着实测面积。</b></sub>

<br>

</div>

<hr>

### 装

```console
$ uv tool install --from 'git+https://github.com/Tape-Out/xirang#subdirectory=packages/xirang' xirang
```

长命令 `xirang`，短命令 `ran`，同一个入口。

<table>
<tr><td width="150"><samp>解析 · 层叠 · 计价 · 导出</samp></td><td>只要 Python 3.14</td></tr>
<tr><td><samp>测试矩阵 · 生成 Verilog</samp></td><td>另需 <a href="https://github.com/B-Lang-org/bsc"><samp>bsc</samp></a></td></tr>
<tr><td><samp>量面积</samp></td><td>再加 <samp>ecc</samp>、一份 PDK，与 <samp>ecc</samp> 调用的 <samp>yosys</samp></td></tr>
</table>

<hr>

### 用

在带 `workspace.yaml` 的目录里跑。工作区清单列出这次流片用到的包。

```console
$ ran new mytimer -t ip/regmap     # 从模板铺一个新仓
$ ran status                       # 清单、源码、锁三者对不对得上
$ ran tree soc-mcu                 # 装配层次，每个实例标着面积
$ ran config uart -s fifoDepth=16  # 每个旋钮的最终取值与合计面积
$ ran test uart                    # 在整张测试矩阵上验一遍
$ ran build soc-mcu                # 生成并综合
$ ran export soc-mcu -f rdl        # 寄存器图导成 SystemRDL
```

**旋钮的来历答得出**，按层叠从低到高列，打勾的那一层生效：

```console
$ ran config soc-mcu --why gpiob.numPins
gpiob.numPins = 8

  层叠（低到高，越靠下越优先）：
      bsv-default  32         gpio/ip.yaml (default)
    ✔ instance     8          soc-mcu/ip.yaml:12
```

**测试矩阵由旋钮派生**，解析到同一配置的点只跑一次：

```console
$ ran test emac
emac  矩阵 7 点
  ✔ Default                bufWords=512 promisc=False
  ✔ BufWords64PromiscOff   bufWords=64 promisc=False
  ✔ BufWords1024PromiscOn  bufWords=1024 promisc=True
  ✔ PromiscOn              bufWords=512 promisc=True
    同 PromiscOff          解析下来与 Default 是同一点
    同 BufWords64          解析下来与 BufWords64PromiscOff 是同一点
  ✔ BufWords1024           bufWords=1024 promisc=False

5 点实测，0 点不过
```

<hr>

### 清单

叶子 IP 一份 `ip.yaml`：参数带范围，特性带实测价。

```yaml
name: uart
kind: ip
lang: bsv
params:
  fifoDepth: { type: int, default: 8, range: [1, 64] }
features:
  parity:
    type: bool
    default: false
    area: { points: { '1': 23.52 }, per: fifoDepth }
```

装配也是一份 `ip.yaml`：只列实例、取值与地址，**自己不写 RTL**。

```yaml
name: soc-switch
bus: apb4
instances:
  - name: sw0
    of: eswitch
    with: { ports: 4, macEntries: 16, vlan: false }
    addr: 0x10000000
deps: { hwcore: ^0.1, amba: ^0.1 }
```

寄存器写在 `regmap.yaml`，**属性名沿用 SystemRDL 2.0**（`sw`、`hw`、`onwrite`、`hwset`），由它生成寄存器组、C 头与一致性测试。字段细则见 [`spec/regmap.md`](https://github.com/Tape-Out/spec/blob/main/regmap.md)。

<hr>

### 导出

别人的格式一律是**导出目标**，不在执行路径上。加一种格式是往注册表加一行。

<table>
<tr><td width="110"><samp>rdl</samp></td><td>SystemRDL 2.0 — 整条 PeakRDL 生态的入口</td></tr>
<tr><td><samp>ipxact</samp></td><td>IP-XACT（IEEE 1685-2014）的寄存器视图</td></tr>
<tr><td><samp>core</samp></td><td>FuseSoC CAPI2，上游 <samp>fusesoc</samp> 直接吃</td></tr>
<tr><td><samp>kconfig</samp></td><td>嵌套菜单，喂 <samp>menuconfig</samp>；有回程</td></tr>
<tr><td><samp>resolved</samp></td><td>解出来的配置：每个旋钮的最终值与来历</td></tr>
<tr><td><samp>tar</samp></td><td>零工具依赖的源码包，解开只要 <samp>bsc</samp></td></tr>
</table>

<hr>

### 门禁

<details>
<summary><sub>推之前 <samp>ran status</samp>、<samp>ran lint</samp> 与 <samp>ran test</samp> 拦下的十件事 — 点开</sub></summary>

<br>

<table>
<tr><th align="left" width="130">门禁</th><th align="left">拦下什么</th></tr>
<tr><td>死输入</td><td>引脚驱进来的值，模块里一次也没读</td></tr>
<tr><td>未用方法</td><td>寄存器接口暴露的方法，实现里没有用到</td></tr>
<tr><td>地址重叠</td><td>某个合法配置下，两个寄存器占同一个地址</td></tr>
<tr><td>调度</td><td>装配生成 Verilog 时 <samp>bsc</samp> 报出的规则冲突</td></tr>
<tr><td>价目表失效</td><td>价目表量的是另一份生成产物</td></tr>
<tr><td>未计价</td><td>改这个旋钮，面积预测不动</td></tr>
<tr><td>未实测</td><td>标了价，却没有一行实测打开过这个特性</td></tr>
<tr><td>曲线平坦</td><td>参数改了，量出来的面积不变</td></tr>
<tr><td>低估</td><td>预测面积低于实测</td></tr>
<tr><td>工作区</td><td>清单、源码与锁不一致</td></tr>
</table>

</details>

<hr>

### 八个包

<details>
<summary><sub>各管一件事，分包的理由是<b>什么变了会逼它改</b> — 点开</sub></summary>

<br>

<table>
<tr><th align="left" width="130">包</th><th align="left" width="300">管什么</th><th align="left">什么变了会逼它改</th></tr>
<tr><td><samp>xirang-core</samp></td><td>清单 schema · 层叠与约束求解 · 依赖与锁 · 矩阵派生</td><td>规范涨版本</td></tr>
<tr><td><samp>xirang-gen</samp></td><td>寄存器图 → BSV / C 头 / 测试台 · 装配 → 顶层</td><td>目标语言、契约形态、总线种类</td></tr>
<tr><td><samp>xirang-area</samp></td><td>价目表 · 面积预测 · 回填</td><td>面积模型、测量流程</td></tr>
<tr><td><samp>xirang-back</samp></td><td>驱动综合与仿真工具</td><td>EDA 工具与版本</td></tr>
<tr><td><samp>xirang-out</samp></td><td>投影与读回：六种导出目标</td><td>别人的格式</td></tr>
<tr><td><samp>xirang-ws</samp></td><td>工作区：<samp>workspace.yaml</samp> · 成员 · <samp>status</samp></td><td>产品怎么组装</td></tr>
<tr><td><samp>xirang-flow</samp></td><td>编排：建一个叶子 · 过一个装配 · 跑一张矩阵</td><td>质量策略</td></tr>
<tr><td><samp>xirang</samp></td><td>命令行</td><td>用户界面</td></tr>
</table>

<sub>依赖无环，<samp>xirang-core</samp> 与 <samp>xirang-back</samp> 在底，没有包反向依赖命令行。<samp>xirang-back</samp> 不认识息壤的数据模型，可以单独拿去驱动工具。</sub>

</details>

<hr>

### 开发

```console
$ git clone --recurse-submodules https://github.com/Tape-Out/xirang && cd xirang
$ uv sync --all-packages
$ uv run pytest -q
$ uv run ruff check .
```

模板仓 [`xrskel`](https://github.com/Tape-Out/xrskel) 以子模块挂在包内，于是 `ran new` 运行时零网络零 git；发版时自动跟到它的最新一版。

<hr>

### 相关

<table>
<tr><td width="180"><a href="https://github.com/Tape-Out/spec"><samp>Tape-Out/spec</samp></a></td><td><samp>ip.yaml</samp>、<samp>regmap.yaml</samp> 与契约的规范</td></tr>
<tr><td><a href="https://github.com/Tape-Out/xrskel"><samp>Tape-Out/xrskel</samp></a></td><td><samp>ran new</samp> 用的模板</td></tr>
<tr><td><a href="https://github.com/B-Lang-org/bsc"><samp>B-Lang-org/bsc</samp></a></td><td>BSV 与 BH 的编译器</td></tr>
<tr><td><a href="https://github.com/olofk/fusesoc"><samp>olofk/fusesoc</samp></a></td><td>这个仓的历史始于它的一份 fork，提交全部保留</td></tr>
</table>

<hr>

### 许可

任选其一：
<a href="LICENSE-MIT">MIT</a> ·
<a href="LICENSE-APACHE">Apache 2.0</a> ·
<a href="LICENSE-MULAN">木兰宽松许可证 第2版</a>

<sub><samp>SPDX-License-Identifier: MIT OR Apache-2.0 OR MulanPSL-2.0</samp></sub>

<sub>除非另行说明，你提交的贡献按上述三者同时授权，不附加其他条件。</sub>

<div align="center">
<br>
<sub><a href="https://github.com/Tape-Out">Tape-Out</a> · 开源开放 IP 库</sub>
<br><br>
</div>
