"""可再导入的完整配置：模型 <-> YAML。round-trip 判据靠这一对。
"""
from xirang_core.model import Resolved


def to_doc(res: Resolved) -> dict:
    def one(i):
        d = {"name": i.name, "of": i.of,
             "with": {k: v.value for k, v in i.values.items()}}
        if i.addr is not None:
            d["addr"] = f"{i.addr:#010x}"
        if i.bus:
            d["bus"] = i.bus
        if i.children:
            d["instances"] = [one(c) for c in i.children]
        return d
    return {"xirang": 1, "top": res.top, "bus": res.bus,
            "instances": [one(i) for i in res.instances]}


def from_doc(doc, pkgs: dict) -> Resolved:
    """从导出的 resolved 配置重建模型。round-trip 判据靠它。"""
    from xirang_core.model import Candidate, Instance, Value

    def one(d) -> Instance:
        vals = {k: Value(name=k, value=v,
                         winner=Candidate("instance", v, "<resolved>"))
                for k, v in (d.get("with") or {}).items()}
        return Instance(name=d["name"], of=d["of"], values=vals,
                        addr=int(str(d["addr"]), 0) if d.get("addr") else None,
                        bus=d.get("bus"),
                        children=[one(c) for c in d.get("instances", [])])

    res = Resolved(top=doc["top"], bus=doc["bus"],
                   instances=[one(i) for i in doc["instances"]])
    # 地址与大小从各自的 regmap 补回
    for _, i in res.walk():
        p = pkgs.get(i.of)
        if not (p and p.regmap):
            continue
        # 没有控制口的实例（核）不进地址图：它的 regmap 描述的是自己的 CSR
        # 空间，跟片上地址空间无关。解析那一路一直这么判，导入这一路没判，
        # 于是往返一圈核就多了个 0 地址——soc-mcu 补上核之后 V4 当场变红。
        shape = ((p.ip.get("contract") or {}).get("ctrl") or {}).get(
            "shape", "flat")
        if shape == "none":
            continue
        if i.addr is None:
            i.addr = int(str(p.regmap.get("base")), 0)
        i.size = int(str(p.regmap.get("size")), 0)
    return res
