"""编排的结果是数据，不是打印。

CLI 只负责印，CI 直接拿结构化结果——组织级流水线今天靠 grep 输出文字判成败，
是因为除了 CLI 没有别的口。
"""
from dataclasses import dataclass, field
from enum import StrEnum


class Mark(StrEnum):
    ok = "✔"
    bad = "✘"
    skip = "略"      # 约束不允许
    same = "同"      # 解析下来与别处是同一点

    @property
    def counted(self) -> bool:
        """略与同没有真跑，不进分母。"""
        return self in (Mark.ok, Mark.bad)


@dataclass(slots=True)
class Row:
    label: str
    mark: Mark
    note: str


@dataclass(slots=True)
class Matrix:
    name: str
    rows: list[Row] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    points: int = 0

    @property
    def failed(self) -> int:
        return sum(r.mark is Mark.bad for r in self.rows)

    @property
    def ran(self) -> int:
        return sum(r.mark.counted for r in self.rows)

    @property
    def rc(self) -> int:
        return int(bool(self.problems or self.failed))


@dataclass(slots=True)
class Gate:
    top: str
    ok: bool
    hits: list[str] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)

    @property
    def rc(self) -> int:
        return int(not self.ok)


@dataclass(slots=True)
class Lib:
    name: str
    rows: list[Row] = field(default_factory=list)

    @property
    def failed(self) -> int:
        return sum(r.mark is Mark.bad for r in self.rows)

    @property
    def rc(self) -> int:
        return int(bool(self.failed))


@dataclass(slots=True)
class Leaf:
    out: str
    top: str
    want: float | None = None
    got: float | None = None
    synthesised: bool = False

    @property
    def err_pct(self) -> float | None:
        if self.want is None or not self.got:
            return None
        return (self.got - self.want) / self.got * 100

    @property
    def rc(self) -> int:
        return int(self.synthesised and self.got is None)
