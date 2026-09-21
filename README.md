<table align="center" border="0"><tr>
<td width="120" align="center"><img src="docs/logo.svg" width="96" alt=""></td>
<td align="left">
<h3>息壤</h3>
<sub>XiRang · 硬件的包管理器与装配器</sub>
</td>
</tr></table>

<div align="center">

<a href="pyproject.toml"><img alt="python" src="https://img.shields.io/badge/python%203.14+-2E3440?style=flat-square"></a>
<a href="https://github.com/Tape-Out/spec"><img alt="spec" src="https://img.shields.io/badge/spec%20v0.2.3-4C6A92?style=flat-square"></a>
<a href="LICENSE-MIT"><img alt="MIT" src="https://img.shields.io/badge/MIT-5E81AC?style=flat-square"></a>
<a href="LICENSE-APACHE"><img alt="Apache-2.0" src="https://img.shields.io/badge/Apache--2.0-6D8CB5?style=flat-square"></a>
<a href="LICENSE-MULAN"><img alt="MulanPSL-2.0" src="https://img.shields.io/badge/MulanPSL--2.0-8FC6D6?style=flat-square"></a>

<br>

<sub>一个数据模型，一条执行路径</sub>

<br>

</div>

<hr>

### 安装

```console
$ uv tool install --from 'git+https://github.com/Tape-Out/xirang#subdirectory=packages/xirang' xirang
```

长命令 `xirang`，短命令 `ran`。解析与计价只要 Python 3.14；跑矩阵另需 [`bsc`](https://github.com/B-Lang-org/bsc)；量面积再加 `ecc` 与一份 PDK。

<hr>

### 常用命令

```console
$ ran new mytimer -t ip/regmap     # 从模板铺一个新仓
$ ran config uart -s fifoDepth=16  # 每个旋钮的最终取值与合计面积
$ ran test uart                    # 在整张测试矩阵上验一遍
$ ran build soc-mcu                # 生成并综合
$ ran export soc-mcu -f rdl        # 寄存器图导成 SystemRDL
```

<details>
<summary><sub>配置来历与矩阵输出</sub></summary>

```console
$ ran config soc-mcu --why gpiob.numPins
gpiob.numPins = 8
  层叠（低到高，越靠下越优先）：
      bsv-default  32   gpio/ip.yaml (default)
    ✔ instance     8    soc-mcu/ip.yaml:12

$ ran test emac
emac  矩阵 7 点
  ✔ Default                bufWords=512  promisc=False
  ✔ BufWords1024PromiscOn  bufWords=1024 promisc=True
    同 PromiscOff          解析下来与 Default 是同一点
5 点实测，0 点不过
```

</details>

<hr>

### 清单格式

<table>
<tr><td width="150"><samp>ip.yaml</samp></td><td>身份 · 契约 · 旋钮与约束 · 面积价目表 · 依赖；有 <samp>instances</samp> 即为装配</td></tr>
<tr><td><samp>regmap.yaml</samp></td><td>寄存器与字段，属性名沿用 <b>SystemRDL 2.0</b></td></tr>
<tr><td><samp>workspace.yaml</samp></td><td>这次流片用到哪些包</td></tr>
<tr><td><samp>xirang.lock</samp></td><td>解析结果与内容摘要，跟着装配走</td></tr>
</table>

<details>
<summary><sub>清单样例</sub></summary>

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

装配自己不写 RTL。字段细则见 [`spec/regmap.md`](https://github.com/Tape-Out/spec/blob/main/regmap.md)。

</details>

<hr>

### 导出目标

<table align="center">
<tr>
<td align="right"><samp>rdl</samp></td><td><sub>SystemRDL 2.0</sub></td>
<td align="right"><samp>core</samp></td><td><sub>FuseSoC CAPI2</sub></td>
<td align="right"><samp>resolved</samp></td><td><sub>解出的配置与来历</sub></td>
</tr>
<tr>
<td align="right"><samp>ipxact</samp></td><td><sub>IP-XACT 1685</sub></td>
<td align="right"><samp>kconfig</samp></td><td><sub>menuconfig 菜单</sub></td>
<td align="right"><samp>tar</samp></td><td><sub>自足源码包</sub></td>
</tr>
</table>
<hr>

### 门禁清单

<details>
<summary><sub>十道门禁</sub></summary>

<br>

<table>
<tr><td width="120">死输入</td><td>引脚驱进来的值，模块里一次也没读</td></tr>
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

### 子包说明

<details>
<summary><sub>八个包，与各自的改动来源</sub></summary>

<br>

<table>
<tr><td width="130"><samp>xirang-core</samp></td><td width="330">清单 schema · 层叠与求解 · 依赖与锁 · 矩阵派生</td><td>规范涨版本</td></tr>
<tr><td><samp>xirang-gen</samp></td><td>寄存器图 → BSV / C 头 / 测试台 · 装配 → 顶层</td><td>目标语言、契约形态</td></tr>
<tr><td><samp>xirang-area</samp></td><td>价目表 · 面积预测 · 回填</td><td>面积模型</td></tr>
<tr><td><samp>xirang-back</samp></td><td>驱动综合与仿真工具</td><td>EDA 工具与版本</td></tr>
<tr><td><samp>xirang-out</samp></td><td>六种导出目标与读回</td><td>别人的格式</td></tr>
<tr><td><samp>xirang-ws</samp></td><td>工作区：成员与 <samp>status</samp></td><td>产品怎么组装</td></tr>
<tr><td><samp>xirang-flow</samp></td><td>编排：叶子 · 装配 · 矩阵</td><td>质量策略</td></tr>
<tr><td><samp>xirang</samp></td><td>命令行</td><td>用户界面</td></tr>
</table>

<sub>依赖无环，没有包反向依赖命令行。</sub>

</details>

<hr>

### 本地开发

```console
$ git clone --recurse-submodules https://github.com/Tape-Out/xirang && cd xirang
$ uv sync --all-packages && uv run pytest -q && uv run ruff check .
```

<hr>

### 相关项目

[`spec`](https://github.com/Tape-Out/spec) 规范 · [`xrskel`](https://github.com/Tape-Out/xrskel) 模板
<hr>

### 许可证

任选其一：
<a href="LICENSE-MIT">MIT</a> ·
<a href="LICENSE-APACHE">Apache 2.0</a> ·
<a href="LICENSE-MULAN">木兰宽松许可证 第2版</a>
