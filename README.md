# XiRang · 息壤

Package manager and assembler for hardware. Cargo-style manifests, kconfig-style options,
and a measured area price on every feature.

![status](https://img.shields.io/badge/status-mvp-yellow) ![license](https://img.shields.io/badge/license-BSD--2--Clause-blue)

息壤是自己生长的土壤，越用越厚。仓库是土壤，IP 在上面长。

```
xirang build soc-mcu          # 两份 YAML 走到 GDS-ready 的网表与面积
xirang config soc-mcu         # computed 面板：每个旋钮最终值、来历、花了多少面积
xirang config --why gpio0.irq # 单条旋钮的完整来源链
xirang tree soc-mcu           # 装配层次
xirang export -o resolved.yaml
```

短命令 `ran` 与 `xirang` 等价。

## 它解决什么

配置层叠在别的构建系统里是个黑箱——最终值是多少、被谁决定的，没人说得清。浏览器的
computed style 面板早就把这件事解决了，我们照搬：**每个旋钮都答得出「最终值 · 谁定的
（层/文件/行）· 被压掉的候选 · 是否被约束强制 · 花了多少面积」**。最后一列是硬件独有的。

设计取自八个来源，各管一层，互不覆盖：cargo 的包与依赖模型、kconfig 的旋钮约束、
meson 的「清单只准是数据」、uv 与 venv 的复现与工具链锁定、git 的获取、make 与 cmake
的增量与消费。三条原则让它们协调而不是打架：

- **一个数据模型**：所有面向外部的格式都是它的投影，不是并列的第二真相
- **一条执行路径**：别人的格式一律是导出目标，不在运行时路径上
- **钩子只能往下游加产物，不能往上游改取值**——否则面板那五问就答不了

## 规范与实现是分开的

清单格式定义在 [`Tape-Out/spec`](https://github.com/Tape-Out/spec)，独立定版。
本仓是它的一个实现，任何人都可以写另一个。IP 仓依赖的是规范，不是工具。

## 包

| 包 | 管什么 |
| :--: | :-- |
| `xirang-core` | 清单 · 层叠 · 约束求解 · 依赖与锁 |
| `xirang-gen` | 生成器：regmap→BSV/C头 · 装配→顶层 BSV |
| `xirang-area` | 价目表 · 预测 · CI 回填 |
| `xirang-back` | 后端：ecc · fusesoc/kconfig/tar 导出 |
| `xirang` | CLI |

## 现状

MVP 竖切已通：`xirang build soc-mcu` 从两份 YAML 走到 `ecc` 出面积，四条判据全绿——
网表出得来 · `config --why` 答得出五问 · export 再 build 产物逐位相同 ·
装配包里零自有 RTL。面积预测与实测差 **+1.25%，且偏保守**。

尚未实现：注册表 · 并行 DAG 与增量构建 · `wrap/` 生成 · fusesoc/kconfig/tar 三个导出 · CI。
