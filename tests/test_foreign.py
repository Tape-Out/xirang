"""黑盒声明：别人的 RTL 只靠这一份声明接进来，所以对不上的地方只能在这里查出来。

`kind: foreign` 不解析源码，声明就是它的全部形状。少写一项、写错一个旋钮名、
事务端点忘了写 profile，都要当场报——这些在装配阶段才发现的话，报出来的是
一堆 Verilog 的端口名对不上，指不回清单。

`uv run python tests/test_foreign.py`
"""
import pathlib
import sys
import tempfile

import yaml

from xirang_core.manifest import Bad, Pkg

OK = {
    "name": "extuart", "version": "0.1.0", "spec": "0.1", "kind": "ip",
    "lang": "verilog",
    "params": {"dataBits": {"type": "int", "default": 8, "range": [5, 8]}},
    "contract": {"version": 1, "ctrl": {"shape": "flat", "aw": 8, "dw": 32}},
    "emit": [{
        "kind": "foreign", "lang": "verilog", "top": "uart_core",
        "rtl": ["hwsrc/uart_core.v"],
        "params": {"DATA_BITS": "dataBits", "FIFO_DEPTH": 8},
        "clock": {"port": "clk"},
        "reset": {"port": "rst_n", "active": "low", "sync": True},
        "ports": [
            {"endpoint": "ctrl", "kind": "transaction", "role": "target",
             "profile": "apb4", "prefix": "p"},
            {"endpoint": "pins", "kind": "physical", "type": "UartPins",
             "map": {"tx": "o_tx", "rx": "i_rx"}},
        ],
        "limits": ["数据位固定 8"],
    }],
}


def mk(ip: dict, root: pathlib.Path) -> Pkg:
    (root / "hwsrc").mkdir(parents=True, exist_ok=True)
    (root / "hwsrc/uart_core.v").write_text("module uart_core; endmodule\n")
    (root / "ip.yaml").write_text(yaml.safe_dump(ip, allow_unicode=True))
    return Pkg(root)


def drop(path: list, val=None):
    """照 OK 改一处：val 是 None 就删掉那一项。"""
    import copy
    ip = copy.deepcopy(OK)
    cur = ip
    for k in path[:-1]:
        cur = cur[k]
    if val is None:
        cur.pop(path[-1], None)
    else:
        cur[path[-1]] = val
    return ip


E = OK["emit"][0]
CASES = [
    ("缺 top", drop(["emit", 0, "top"])),
    ("rtl 指向树上没有的文件", drop(["emit", 0, "rtl"], ["hwsrc/nope.v"])),
    ("参数投影到不存在的旋钮", drop(["emit", 0, "params"], {"DATA_BITS": "nosuch"})),
    ("事务端点没写 profile", drop(["emit", 0, "ports"],
                                 [{"endpoint": "ctrl", "kind": "transaction",
                                   "role": "target", "prefix": "p"}])),
    ("端点既无 prefix 也无 map", drop(["emit", 0, "ports"],
                                     [{"endpoint": "irq", "kind": "event"}])),
    ("端点 kind 不认识", drop(["emit", 0, "ports"],
                             [{"endpoint": "x", "kind": "wire", "prefix": "p"}])),
    ("reset.active 写了别的", drop(["emit", 0, "reset"],
                                  {"port": "rst_n", "active": "maybe"})),
]


def main() -> int:
    bad: list[str] = []
    with tempfile.TemporaryDirectory() as d:
        root = pathlib.Path(d) / "extuart"
        got = mk(OK, root).foreign_emit()
        if not got or got["top"] != "uart_core":
            bad.append("合法的黑盒声明没被收下")

        for what, ip in CASES:
            try:
                mk(ip, root)
                bad.append(f"{what}：没报错")
            except Bad:
                pass

    # 我们自己写的包一个都不该被当成黑盒
    ours = pathlib.Path.home() / "work/xr/ws/uart"
    if ours.is_dir() and Pkg(ours).foreign_emit() is not None:
        bad.append("我们自己的 BSV 包被认成了黑盒")

    for line in bad:
        print(f"✘ {line}")
    if not bad:
        print(f"✔ 黑盒声明收得下，{len(CASES)} 种写坏的各报各的")
    return 1 if bad else 0


def test_foreign():
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
