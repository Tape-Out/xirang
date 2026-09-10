# 息壤 / XiRang

硬件的包管理器与装配器。读 `ip.yaml` 与 `regmap.yaml`，解析出一份模型，
生成 BSV、对拍、算面积、导出成别人的格式。

长命令 `xirang`，短命令 `ran`。

## 一句话说清它跟别家的关系

**一个数据模型 + 一条执行路径 + N 个投影。** 内部只有一条路走到底；
fusesoc 的 `.core`、`Kconfig`、tar 一律是**导出目标**，不在执行路径上。

## 八个包

| 包 | 管什么 | 什么变了会逼它改 |
|:--|:--|:--|
| `xirang-core` | 清单 schema · 层叠与约束求解 · 依赖与锁 · 测试矩阵派生 | 规范涨版本 |
| `xirang-gen` | 生成器：寄存器图 → BSV/C 头/测试台 · 契约 → 扁平顶层 · 装配 → 顶层 | 目标语言、契约形态、总线种类 |
| `xirang-area` | 价目表 · 面积预测 · 回填 | 面积模型、测量流程 |
| `xirang-back` | 驱动外部工具：综合与仿真。**不认识我们的数据模型** | EDA 工具与版本 |
| `xirang-out` | 投影与读回：`resolved` · fusesoc `.core` · Kconfig · tar | 别人的格式 |
| `xirang-ws` | 工作区：`workspace.yaml` · 成员 · `status` | 产品怎么组装 |
| `xirang-flow` | 编排：建一个叶子 · 过一个装配 · 跑一张矩阵 | 质量策略 |
| `xirang` | 命令行 | 用户界面 |

依赖是一张无环图，`xirang-core` 与 `xirang-back` 在底，没有任何包反向依赖命令行。

## 跑起来

```sh
uv sync
uv run xirang --help
uv run xirang status            # 清单、源码、锁对不对得上
uv run xirang config soc-mcu    # 每个旋钮最终是多少、被谁定的、花多少面积
```

## 出处

这个仓的历史始于 [FuseSoC](https://github.com/olofk/fusesoc) 的一份 fork——
息壤源于它，也留着它的全部提交作为出处。工作树已经完整重写，
产物仍然可以被**上游** FuseSoC 直接消费（`xirang export -f core`）。

## 许可证

Apache-2.0。
