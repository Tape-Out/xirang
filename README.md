<div align="center">

<br>

<img src="docs/banner.svg" width="430" alt="息壤 XiRang">

<br><br>

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

<sub>长命令 <samp>xirang</samp>，短命令 <samp>ran</samp>。</sub>
<hr>

### 常用命令

```console
$ ran new mytimer -t ip/regmap     # 铺一个新仓
$ ran config uart -s fifoDepth=16  # 旋钮的最终取值与面积
$ ran test uart                    # 跑整张测试矩阵
$ ran build soc-mcu                # 生成并综合
$ ran export soc-mcu -f rdl        # 导成 SystemRDL
```

<details>
<summary><sub>配置来历与矩阵输出</sub></summary>

<br />

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

| <sub>文件</sub> | <sub>内容</sub> |
| :--: | :--: |
| <sub><samp>ip.yaml</samp></sub> | <sub>身份 · 契约 · 旋钮 · 价目表 · 依赖；有 <samp>instances</samp> 即为装配</sub> |
| <sub><samp>regmap.yaml</samp></sub> | <sub>寄存器与字段，属性名同 SystemRDL 2.0</sub> |
| <sub><samp>workspace.yaml</samp></sub> | <sub>这次用到哪些包</sub> |
| <sub><samp>xirang.lock</samp></sub> | <sub>解析结果与内容摘要，跟着装配走</sub> |

<details>
<summary><sub>清单样例</sub></summary>

<br />

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

<sub>装配自己不写 RTL。字段细则见 <a href="https://github.com/Tape-Out/spec/blob/main/regmap.md">spec/regmap.md</a>。</sub>

</details>
<hr>

### 导出目标

| <sub>目标</sub> | <sub>是什么</sub> |
| :--: | :--: |
| <sub><samp>rdl</samp></sub> | <sub>SystemRDL 2.0</sub> |
| <sub><samp>ipxact</samp></sub> | <sub>IP-XACT 1685</sub> |
| <sub><samp>core</samp></sub> | <sub>FuseSoC CAPI2</sub> |
| <sub><samp>kconfig</samp></sub> | <sub>menuconfig 菜单</sub> |
| <sub><samp>resolved</samp></sub> | <sub>解出的配置与来历</sub> |
| <sub><samp>tar</samp></sub> | <sub>自足源码包</sub> |
<hr>

### 门禁清单

<details>
<summary><sub>十道门禁</sub></summary>

<br />

| <sub>门禁</sub> | <sub>拦下什么</sub> |
| :--: | :--: |
| <sub>死输入</sub> | <sub>引脚驱进来的值，模块里一次也没读</sub> |
| <sub>未用方法</sub> | <sub>寄存器接口暴露的方法，实现里没有用到</sub> |
| <sub>地址重叠</sub> | <sub>某个合法配置下，两个寄存器占同一个地址</sub> |
| <sub>调度</sub> | <sub>装配生成 Verilog 时 <samp>bsc</samp> 报出的规则冲突</sub> |
| <sub>价目表失效</sub> | <sub>价目表量的是另一份生成产物</sub> |
| <sub>未计价</sub> | <sub>改这个旋钮，面积预测不动</sub> |
| <sub>未实测</sub> | <sub>标了价，却没有一行实测打开过这个特性</sub> |
| <sub>曲线平坦</sub> | <sub>参数改了，量出来的面积不变</sub> |
| <sub>低估</sub> | <sub>预测面积低于实测</sub> |
| <sub>工作区</sub> | <sub>清单、源码与锁不一致</sub> |

</details>
<hr>

### 子包说明

<details>
<summary><sub>八个包，与各自的改动来源</sub></summary>

<br />

| <sub>包</sub> | <sub>管什么</sub> | <sub>什么变了会逼它改</sub> |
| :--: | :--: | :--: |
| <sub><samp>xirang-core</samp></sub> | <sub>清单 schema · 层叠与求解 · 依赖与锁 · 矩阵派生</sub> | <sub>规范涨版本</sub> |
| <sub><samp>xirang-gen</samp></sub> | <sub>寄存器图 → BSV / C 头 / 测试台 · 装配 → 顶层</sub> | <sub>目标语言、契约形态</sub> |
| <sub><samp>xirang-area</samp></sub> | <sub>价目表 · 面积预测 · 回填</sub> | <sub>面积模型</sub> |
| <sub><samp>xirang-back</samp></sub> | <sub>驱动综合与仿真工具</sub> | <sub>EDA 工具与版本</sub> |
| <sub><samp>xirang-out</samp></sub> | <sub>六种导出目标与读回</sub> | <sub>别人的格式</sub> |
| <sub><samp>xirang-ws</samp></sub> | <sub>工作区：成员与 <samp>status</samp></sub> | <sub>产品怎么组装</sub> |
| <sub><samp>xirang-flow</samp></sub> | <sub>编排：叶子 · 装配 · 矩阵</sub> | <sub>质量策略</sub> |
| <sub><samp>xirang</samp></sub> | <sub>命令行</sub> | <sub>用户界面</sub> |

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
