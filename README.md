<div align="center">

<img src="docs/banner.svg" width="382" alt="息壤 XiRang">

<a href="pyproject.toml"><img alt="python" src="https://img.shields.io/badge/python%203.14+-2E3440?style=flat-square"></a>
<a href="https://github.com/Tape-Out/xrspec"><img alt="spec" src="https://img.shields.io/badge/spec%20v0.2.3-4C6A92?style=flat-square"></a>
<a href="LICENSE-MIT"><img alt="MIT" src="https://img.shields.io/badge/MIT-5E81AC?style=flat-square"></a>
<a href="LICENSE-APACHE"><img alt="Apache-2.0" src="https://img.shields.io/badge/Apache--2.0-6D8CB5?style=flat-square"></a>
<a href="LICENSE-MULAN"><img alt="MulanPSL-2.0" src="https://img.shields.io/badge/MulanPSL--2.0-8FC6D6?style=flat-square"></a>

<sub>选件 · 接线 · 成片</sub>

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

<div align="center">
<table>
<tr><th align="center" valign="middle"><small><small>文件</small></small></th><th align="center" valign="middle"><small><small>内容</small></small></th></tr>
<tr><td align="center" valign="middle"><small><small><code>ip.yaml</code></small></small></td><td align="center" valign="middle"><small><small>身份 · 契约 · 旋钮 · 价目表 · 依赖；有 <code>instances</code> 即为装配</small></small></td></tr>
<tr><td align="center" valign="middle"><small><small><code>regmap.yaml</code></small></small></td><td align="center" valign="middle"><small><small>寄存器与字段，属性名同 SystemRDL 2.0</small></small></td></tr>
<tr><td align="center" valign="middle"><small><small><code>workspace.yaml</code></small></small></td><td align="center" valign="middle"><small><small>这次用到哪些包</small></small></td></tr>
<tr><td align="center" valign="middle"><small><small><code>xirang.lock</code></small></small></td><td align="center" valign="middle"><small><small>解析结果与内容摘要，跟着装配走</small></small></td></tr>
</table>
</div>

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

装配自己不写 RTL。字段细则见 [`xrspec/regmap.md`](https://github.com/Tape-Out/xrspec/blob/main/regmap.md)。

</details>
<hr>

### 导出目标

<div align="center">
<table>
<tr><th align="center" valign="middle"><small><small>目标</small></small></th><th align="center" valign="middle"><small><small>是什么</small></small></th></tr>
<tr><td align="center" valign="middle"><small><small><code>rdl</code></small></small></td><td align="center" valign="middle"><small><small>SystemRDL 2.0</small></small></td></tr>
<tr><td align="center" valign="middle"><small><small><code>ipxact</code></small></small></td><td align="center" valign="middle"><small><small>IP-XACT 1685</small></small></td></tr>
<tr><td align="center" valign="middle"><small><small><code>core</code></small></small></td><td align="center" valign="middle"><small><small>FuseSoC CAPI2</small></small></td></tr>
<tr><td align="center" valign="middle"><small><small><code>kconfig</code></small></small></td><td align="center" valign="middle"><small><small>menuconfig 菜单</small></small></td></tr>
<tr><td align="center" valign="middle"><small><small><code>resolved</code></small></small></td><td align="center" valign="middle"><small><small>解出的配置与来历</small></small></td></tr>
<tr><td align="center" valign="middle"><small><small><code>tar</code></small></small></td><td align="center" valign="middle"><small><small>自足源码包</small></small></td></tr>
</table>
</div>
<hr>

### 门禁清单

<details>
<summary><sub>十道门禁</sub></summary>

<br />

<div align="center">
<table>
<tr><th align="center" valign="middle"><small><small>门禁</small></small></th><th align="center" valign="middle"><small><small>拦下什么</small></small></th></tr>
<tr><td align="center" valign="middle"><small><small>死输入</small></small></td><td align="center" valign="middle"><small><small>引脚驱进来的值，模块里一次也没读</small></small></td></tr>
<tr><td align="center" valign="middle"><small><small>未用方法</small></small></td><td align="center" valign="middle"><small><small>寄存器接口暴露的方法，实现里没有用到</small></small></td></tr>
<tr><td align="center" valign="middle"><small><small>地址重叠</small></small></td><td align="center" valign="middle"><small><small>某个合法配置下，两个寄存器占同一个地址</small></small></td></tr>
<tr><td align="center" valign="middle"><small><small>调度</small></small></td><td align="center" valign="middle"><small><small>装配生成 Verilog 时 <code>bsc</code> 报出的规则冲突</small></small></td></tr>
<tr><td align="center" valign="middle"><small><small>价目表失效</small></small></td><td align="center" valign="middle"><small><small>价目表量的是另一份生成产物</small></small></td></tr>
<tr><td align="center" valign="middle"><small><small>未计价</small></small></td><td align="center" valign="middle"><small><small>改这个旋钮，面积预测不动</small></small></td></tr>
<tr><td align="center" valign="middle"><small><small>未实测</small></small></td><td align="center" valign="middle"><small><small>标了价，却没有一行实测打开过这个特性</small></small></td></tr>
<tr><td align="center" valign="middle"><small><small>曲线平坦</small></small></td><td align="center" valign="middle"><small><small>参数改了，量出来的面积不变</small></small></td></tr>
<tr><td align="center" valign="middle"><small><small>低估</small></small></td><td align="center" valign="middle"><small><small>预测面积低于实测</small></small></td></tr>
<tr><td align="center" valign="middle"><small><small>工作区</small></small></td><td align="center" valign="middle"><small><small>清单、源码与锁不一致</small></small></td></tr>
</table>
</div>

</details>
<hr>

### 子包说明

<details>
<summary><sub>八个包，与各自的改动来源</sub></summary>

<br />

<div align="center">
<table>
<tr><th align="center" valign="middle"><small><small>包</small></small></th><th align="center" valign="middle"><small><small>管什么</small></small></th><th align="center" valign="middle"><small><small>什么变了会逼它改</small></small></th></tr>
<tr><td align="center" valign="middle"><small><small><code>xirang-core</code></small></small></td><td align="center" valign="middle"><small><small>清单 schema · 层叠与求解 · 依赖与锁 · 矩阵派生</small></small></td><td align="center" valign="middle"><small><small>规范涨版本</small></small></td></tr>
<tr><td align="center" valign="middle"><small><small><code>xirang-gen</code></small></small></td><td align="center" valign="middle"><small><small>寄存器图 → BSV / C 头 / 测试台 · 装配 → 顶层</small></small></td><td align="center" valign="middle"><small><small>目标语言、契约形态</small></small></td></tr>
<tr><td align="center" valign="middle"><small><small><code>xirang-area</code></small></small></td><td align="center" valign="middle"><small><small>价目表 · 面积预测 · 回填</small></small></td><td align="center" valign="middle"><small><small>面积模型</small></small></td></tr>
<tr><td align="center" valign="middle"><small><small><code>xirang-back</code></small></small></td><td align="center" valign="middle"><small><small>驱动综合与仿真工具</small></small></td><td align="center" valign="middle"><small><small>EDA 工具与版本</small></small></td></tr>
<tr><td align="center" valign="middle"><small><small><code>xirang-out</code></small></small></td><td align="center" valign="middle"><small><small>六种导出目标与读回</small></small></td><td align="center" valign="middle"><small><small>别人的格式</small></small></td></tr>
<tr><td align="center" valign="middle"><small><small><code>xirang-ws</code></small></small></td><td align="center" valign="middle"><small><small>工作区：成员与 <code>status</code></small></small></td><td align="center" valign="middle"><small><small>产品怎么组装</small></small></td></tr>
<tr><td align="center" valign="middle"><small><small><code>xirang-flow</code></small></small></td><td align="center" valign="middle"><small><small>编排：叶子 · 装配 · 矩阵</small></small></td><td align="center" valign="middle"><small><small>质量策略</small></small></td></tr>
<tr><td align="center" valign="middle"><small><small><code>xirang</code></small></small></td><td align="center" valign="middle"><small><small>命令行</small></small></td><td align="center" valign="middle"><small><small>用户界面</small></small></td></tr>
</table>
</div>

</details>
<hr>

### 本地开发

```console
$ git clone --recurse-submodules https://github.com/Tape-Out/xirang && cd xirang
$ uv sync --all-packages && uv run pytest -q && uv run ruff check .
```

<hr>

### 相关项目

[`xrspec`](https://github.com/Tape-Out/xrspec) 规范 · [`xrskel`](https://github.com/Tape-Out/xrskel) 模板
<hr>

### 许可证

任选其一：
<a href="LICENSE-MIT">MIT</a> ·
<a href="LICENSE-APACHE">Apache 2.0</a> ·
<a href="LICENSE-MULAN">木兰宽松许可证 第2版</a>
