"""解析后的模型。整套工具只有这一份真相，面板与所有导出都是它的投影。"""
from __future__ import annotations

import dataclasses
from typing import Any

# 层叠六层，低到高。约束求解不是一层，它横切所有层（见 Value.forced）。
LAYERS = ["bsv-default", "pdk", "ip-default", "workspace", "instance", "cli"]


@dataclasses.dataclass
class Candidate:
    """某一层给出的取值。被压掉的候选也留着，面板要显示它们。"""
    layer: str
    value: Any
    origin: str          # 文件:行，或 "<cli>"


@dataclasses.dataclass
class Value:
    """一个旋钮的最终取值，连同它的来历。"""
    name: str
    value: Any
    winner: Candidate
    shadowed: list[Candidate] = dataclasses.field(default_factory=list)
    forced_by: str | None = None    # 非空即"被约束强制"，与"被上层覆盖"是两回事
    area_um2: float = 0.0

    @property
    def layer(self) -> str:
        return "constraint" if self.forced_by else self.winner.layer


@dataclasses.dataclass
class Instance:
    """装配里的一个实例。叶子 IP 的 children 为空。"""
    name: str
    of: str
    values: dict[str, Value]
    addr: int | None = None
    size: int | None = None
    bus: str | None = None
    area_um2: float = 0.0
    children: list["Instance"] = dataclasses.field(default_factory=list)


@dataclasses.dataclass
class Resolved:
    """一次构建的完整解析结果。export 写它，build --config 读它，两者必须等价。"""
    top: str
    bus: str
    instances: list[Instance]
    area_um2: float = 0.0

    def walk(self):
        def rec(insts, depth):
            for i in insts:
                yield depth, i
                yield from rec(i.children, depth + 1)
        yield from rec(self.instances, 0)

    def find(self, path: str) -> tuple[Instance, Value] | None:
        """按 'gpio0.numPins' 定位一个旋钮。"""
        if "." not in path:
            return None
        iname, knob = path.rsplit(".", 1)
        for _, inst in self.walk():
            if inst.name == iname and knob in inst.values:
                return inst, inst.values[knob]
        return None
