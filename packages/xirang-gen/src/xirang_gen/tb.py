"""从 regmap.yaml 生成寄存器一致性测试。

类型检查看的是「能不能建」，调度门禁看的是「会不会卡」，这一份看的是
「读写对不对」——而生成的译码、掩码、字段落位正是最容易悄悄错的那部分。
`hart` 的译码错误说明前两道全过也可能是错的。

测什么：
  · 软件可写、非 volatile、非 woclr 的字段：写全一读回等于字段掩码，写全零读回零
  · 保留位与关掉的特性位：一律读回零
  · woclr 字段：只验写一清零
  · volatile 字段：跳过——它们的值来自硬件，要各 IP 自己的测试台驱动
  · hwset 字段：同一拍里硬件置位撞上软件写清，置位必须压过写清（事件不许丢）

不涉及 IP 的行为，只涉及寄存器组。行为归各仓自己的 tb。
"""
import copy

from xirang_core.manifest import Bad, Pkg
from xirang_gen.regmap import _cap, _rows, _sig


def _mask(f) -> int:
    return ((1 << int(f["w"])) - 1) << f["lo"] if str(f["w"]).isdigit() else 0


def regs_tb(pkg: Pkg, vals, suffix: str = "") -> str:
    spec = pkg.regmap
    if not spec:
        raise Bad(f"{pkg.name} 没有 regmap.yaml")
    C = _cap(spec.get("ip", pkg.name))
    ctrl = spec.get("contract") or {}
    aw, dw = int(ctrl.get("aw", 8)), int(ctrl.get("dw", 32))
    # 寄存器组是参数化的，测试台要具体数：把解析后的旋钮值代进 regmap 的副本，
    # 位宽与份数于是都变成常量，`_rows` 再算出来的就是这一份配置的真实布局。
    nums = {k: v.value for k, v in vals.items() if isinstance(v.value, int)}
    spec = copy.deepcopy(spec)
    for r in spec.get("regs", []) or []:
        for f in r.get("fields", []) or []:
            if isinstance(f.get("width"), str) and f["width"] in nums:
                f["width"] = nums[f["width"]]
        a = r.get("array")
        if a and isinstance(a.get("count"), str) and a["count"] in nums:
            a["count"] = nums[a["count"]]
    rs = _rows(spec, [], dw)
    feats = {k: v.value for k, v in vals.items()
             if pkg.knobs().get(k, {}).get("kind") == "feature"}

    cases = []
    for reg in rs:
        # 数组与宽寄存器的地址是算出来的，第一版只测标量
        if reg["arr"] or reg["rw"] > dw:
            continue
        if reg["feat"] and not feats.get(reg["feat"], False):
            # 特性关掉：整个寄存器应当读回零
            cases.append((reg["offset"], 0, 0, f"{reg['name']} (feature off)"))
            continue
        wr = 0     # 写全一之后应当读回什么
        for f in reg["fields"]:
            if str(f["w"]).isdigit() is False:
                wr = None
                break
            on = (not f["feat"]) or feats.get(f["feat"], False)
            keep = (f["sw"] in ("rw", "w") and not f["vol"] and not f["woclr"]
                    and f["sw"] != "w")
            # sw: w 的字段读回零；woclr 写一即清；volatile 由硬件驱动
            if on and keep:
                wr |= _mask(f)
        if wr is None:
            continue
        cases.append((reg["offset"], wr, 0, reg["name"]))

    # hwset 的字段单列一张表：置位与写清同拍，读回来必须还在。
    # 只收标量——数组本版不生成 _set。
    races = []
    for reg in rs:
        if reg["arr"] or reg["rw"] > dw:
            continue
        if reg["feat"] and not feats.get(reg["feat"], False):
            continue
        for f in reg["fields"]:
            if not f["hwset"] or not str(f["w"]).isdigit():
                continue
            if f["feat"] and not feats.get(f["feat"], False):
                continue
            m = _mask(f)
            # woclr 是写一清零，其余可写字段是写零清零
            races.append((reg["offset"], m if f["woclr"] else 0, m,
                          _sig(reg, f), f"{reg['name']}.{f['name']}"))

    if not cases:
        # 全是数组或宽寄存器（pinmux、aclint 就是），本版测不了。不是错。
        return ""

    hexw = (aw + 3) // 4
    rbody = chr(10).join(
        f"      {i}: return Rc {{ off: {aw}'h{off:0{hexw}X}, "
        f"clr: {dw}'h{clr:0{dw // 4}X}, msk: {dw}'h{msk:0{dw // 4}X} }};   // {nm}"
        for i, (off, clr, msk, _sg, nm) in enumerate(races))
    rsets = chr(10).join(
        f"    if (n == {i}) r.{sg}_set('1);"
        for i, (_o, _c, _m, sg, _nm) in enumerate(races))
    body = []
    for i, (off, want1, want0, name) in enumerate(cases):
        body.append(f"      {i}: return Chk {{ off: {aw}'h{off:0{hexw}X}, "
                    f"ones: {dw}'h{want1:0{dw // 4}X}, "
                    f"zeros: {dw}'h{want0:0{dw // 4}X} }};   // {name}")

    # Cfg 里只有寄存器图真的用到的特性——IP 的其它开关不在寄存器组的视野里
    used = sorted({f["feat"] for r in rs for f in r["fields"] if f["feat"]}
                  | {r["feat"] for r in rs if r["feat"]})
    args = ", ".join(f"{k}: {'True' if vals[k].value else 'False'}" for k in used)
    # 类型参数按**寄存器图**声明的那几个来。IP 还有别的参数（cache 的行数与
    # 行宽只影响实现），寄存器接口里没有它们的位置，照 ip.yaml 填就多出几个。
    rmp = list((pkg.regmap or {}).get("params") or [])
    nums = [str(vals[k].value) for k in rmp if k in vals]
    miss = [k for k in rmp if k not in vals]
    if miss:
        raise Bad(f"{pkg.name} 的寄存器图用了清单里没有的参数 {miss}")
    targs = ", ".join([str(aw), str(dw)] + nums)
    cfg = f"{C}RegsCfg {{ {args} }}" if args else ""

    return f"""package {C}RegsTb{suffix};

// 由 xirang 从 regmap.yaml 生成，勿手改。

import RegIf::*;
import {C}Regs::*;

typedef struct {{
  Bit#({aw}) off;
  Bit#({dw}) ones;
  Bit#({dw}) zeros;
}} Chk deriving (Bits);

// 硬件置位撞上软件写清那一组：clr 是能清掉它的写值，msk 是必须留下的位
typedef struct {{
  Bit#({aw}) off;
  Bit#({dw}) clr;
  Bit#({dw}) msk;
}} Rc deriving (Bits);

Integer nchk  = {len(cases)};
Integer nrace = {len(races)};

function Rc rc(Integer i);
  case (i)
{rbody}
    default: return Rc {{ off: 0, clr: 0, msk: 0 }};
  endcase
endfunction

function Chk chk(Integer i);
  case (i)
{chr(10).join(body)}
    default: return Chk {{ off: 0, ones: 0, zeros: 0 }};
  endcase
endfunction

(* synthesize *)
module mk{C}RegsTb{suffix}(Empty);
  {C}RegsIfc#({targs}) r <- mk{C}Regs({cfg});

  Reg#(Bit#(16)) step <- mkReg(0);
  Reg#(Bit#(16)) rstp <- mkReg(0);
  Reg#(Bool)     bad  <- mkReg(False);
  Reg#(Bool)     ph2  <- mkReg(False);

  rule run (!ph2);
    Integer i = 0;
    Bit#(16) n = step >> 2;
    Bit#(2)  ph = truncate(step);
    if (n >= fromInteger(nchk)) begin
      ph2 <= True;
    end else begin
      Chk c = chk(0);
      for (Integer j = 0; j < nchk; j = j + 1)
        if (n == fromInteger(j)) c = chk(j);
      case (ph)
        0: begin
          let _ <- r.regs.access(RegReq {{ addr: zeroExtend(c.off), write: True,
                                          wdata: '1, wstrb: '1 }});
        end
        1: begin
          let x <- r.regs.access(RegReq {{ addr: zeroExtend(c.off), write: False,
                                          wdata: 0, wstrb: '1 }});
          if (x.rdata != c.ones) begin
            $display("FAIL ones at %0h: got %08h want %08h",
                     c.off, x.rdata, c.ones);
            bad <= True;
          end
        end
        2: begin
          let _ <- r.regs.access(RegReq {{ addr: zeroExtend(c.off), write: True,
                                          wdata: 0, wstrb: '1 }});
        end
        default: begin
          let x <- r.regs.access(RegReq {{ addr: zeroExtend(c.off), write: False,
                                          wdata: 0, wstrb: '1 }});
          if (x.rdata != c.zeros) begin
            $display("FAIL zeros at %0h: got %08h want %08h",
                     c.off, x.rdata, c.zeros);
            bad <= True;
          end
        end
      endcase
      step <= step + 1;
    end
  endrule

  // 置位单列一条规则：寄存器组是内联的，与总线写放同一条规则就是同时用
  // CReg 的两个端口（G0004）。分开也更像真实形状——IP 自己的规则在置位，
  // 总线同时在写清。
  rule setRace (ph2 && rstp[0] == 0);
    Bit#(16) n = rstp >> 1;
{rsets}
  endrule

  // 第二段：硬件置位与软件写清同一拍。置位排在总线之前，若写回时不把
  // 这一拍的置位 OR 回去，事件就被抹掉了——而 stickybit 的含义正是不会丢。
  rule race (ph2);
    Bit#(16) n = rstp >> 1;
    Bit#(1)  p = truncate(rstp);
    if (n >= fromInteger(nrace)) begin
      if (bad) $display("FAILED");
      else $display("PASS all %0d register checks and %0d set-vs-clear races",
                    nchk, nrace);
      $finish(bad ? 1 : 0);
    end else begin
      Rc c = rc(0);
      for (Integer j = 0; j < nrace; j = j + 1)
        if (n == fromInteger(j)) c = rc(j);
      if (p == 0) begin
        let _ <- r.regs.access(RegReq {{ addr: zeroExtend(c.off), write: True,
                                        wdata: c.clr, wstrb: '1 }});
      end else begin
        let x <- r.regs.access(RegReq {{ addr: zeroExtend(c.off), write: False,
                                        wdata: 0, wstrb: '1 }});
        if ((x.rdata & c.msk) != c.msk) begin
          $display("FAIL a hwset racing the clear at %0h was swallowed: %08h",
                   c.off, x.rdata);
          bad <= True;
        end
      end
      rstp <= rstp + 1;
    end
  endrule
endmodule

endpackage
"""
