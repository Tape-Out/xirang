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
from xirang_gen.regmap import (_BOUND, _cap, _rows, _sig, feat_refs,
                               feat_on, legal_at)
from xirang_gen.wrap import _lit


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
            for e in f.get("legal") or []:
                for k in ("min", "max") if isinstance(e, dict) else ():
                    m = _BOUND.fullmatch(str(e.get(k)))
                    if m and m[1] in nums:
                        e[k] = nums[m[1]] + int(m[3] or 0) * (-1 if m[2] == "-" else 1)
        a = r.get("array")
        if a and isinstance(a.get("count"), str) and a["count"] in nums:
            a["count"] = nums[a["count"]]
    rs = _rows(spec, [], dw)
    # 门控可以挂在 param 上（定宽的档位旋钮就是 param），所以整张旋钮表都要在
    feats = {k: v.value for k, v in vals.items()}

    cases = []
    for reg in rs:
        # 数组与宽寄存器的地址是算出来的，第一版只测标量
        if reg["arr"] or reg["rw"] > dw:
            continue
        if reg["feat"] and not feat_on(reg["feat"], feats.get(reg["feat"][0])):
            # 特性关掉：整个寄存器应当读回零
            cases.append((reg["offset"], 0, 0, f"{reg['name']} (feature off)"))
            continue
        wr, wz = 0, 0     # 写全一、再写全零之后应当读回什么
        for f in reg["fields"]:
            if str(f["w"]).isdigit() is False:
                wr = None
                break
            on = (not f["feat"]) or feat_on(f["feat"], feats.get(f["feat"][0]))
            keep = (f["sw"] in ("rw", "w") and not f["vol"] and not f["woclr"]
                    and f["sw"] != "w")
            # sw: w 的字段读回零；woclr 写一即清；volatile 由硬件驱动
            if on and keep:
                if f["legal"] is None:
                    wr |= _mask(f)
                    continue
                # 非法写保持原值：全一不合法就还是复位值，全零不合法就还是上一步留下的
                top = (1 << int(f["w"])) - 1
                one = top if legal_at(f["legal"], top, feats.get) else int(f["reset"]) & top
                wr |= one << f["lo"]
                wz |= (0 if legal_at(f["legal"], 0, feats.get) else one) << f["lo"]
        if wr is None:
            continue
        cases.append((reg["offset"], wr, wz, reg["name"]))

    # hwset 的字段单列一张表：置位与写清同拍，读回来必须还在。
    # 只收标量——数组本版不生成 _set。
    races = []
    for reg in rs:
        if reg["arr"] or reg["rw"] > dw:
            continue
        if reg["feat"] and not feat_on(reg["feat"], feats.get(reg["feat"][0])):
            continue
        for f in reg["fields"]:
            if not f["hwset"] or not str(f["w"]).isdigit():
                continue
            if f["feat"] and not feat_on(f["feat"], feats.get(f["feat"][0])):
                continue
            m = _mask(f)
            # woclr 是写一清零，其余可写字段是写零清零
            races.append((reg["offset"], m if f["woclr"] else 0, m,
                          _sig(reg, f), f"{reg['name']}.{f['name']}"))

    # 非法写那一组：先写一个合法的界，再写紧挨着它的非法值，读回必须还是那个界。
    # 只看全一全零分不出「保持原值」与「退回复位值」，也分不出端点算没算进区间
    bads = []
    for reg in rs:
        if reg["arr"] or reg["rw"] > dw or reg["alias"]:
            continue
        if reg["feat"] and not feat_on(reg["feat"], feats.get(reg["feat"][0])):
            continue
        for f in reg["fields"]:
            if f["legal"] is None or f["sw"] != "rw" or not str(f["w"]).isdigit():
                continue
            if f["feat"] and not feat_on(f["feat"], feats.get(f["feat"][0])):
                continue
            top = (1 << int(f["w"])) - 1
            for lo, hi, ft in f["legal"]:
                if ft and not feat_on(ft, feats.get(ft[0])):
                    continue
                for good, bad in ((hi, hi + 1), (lo, lo - 1)):
                    row = (reg["offset"], good << f["lo"], bad << f["lo"], _mask(f),
                           f"{reg['name']}.{f['name']} {good} then {bad}")
                    if (0 <= bad <= top and not legal_at(f["legal"], bad, feats.get)
                            and row not in bads):
                        bads.append(row)

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
    # 类型参数按**寄存器图**声明的那几个来。IP 还有别的参数（cache 的行数与
    # 行宽只影响实现），寄存器接口里没有它们的位置，照 ip.yaml 填就多出几个。
    rmp = list((pkg.regmap or {}).get("params") or [])
    # 门控旋钮里写在 params 的那些已经是类型参数了，不进 Cfg
    used = [k for k in feat_refs(rs) if k not in rmp]
    args = ", ".join(f"{k}: {_lit(vals[k].value)}" for k in used)
    nums = [str(vals[k].value) for k in rmp if k in vals]
    miss = [k for k in rmp if k not in vals]
    if miss:
        raise Bad(f"{pkg.name} 的寄存器图用了清单里没有的参数 {miss}")
    targs = ", ".join([str(aw), str(dw)] + nums)
    cfg = f"{C}RegsCfg {{ {args} }}" if args else ""

    # 没有非法写可测时一个字都不多：没写 legal 的包，测试台与改之前逐字节相同
    wcount = wfunc = wregs = wrule = ""
    race_guard = "ph2"
    race_done = ('      if (bad) $display("FAILED");\n'
                 '      else $display("PASS all %0d register checks and %0d set-vs-clear races",\n'
                 '                    nchk, nrace);\n'
                 '      $finish(bad ? 1 : 0);')
    if bads:
        wbody = chr(10).join(
            f"      {i}: return Wl {{ off: {aw}'h{off:0{hexw}X}, good: {dw}'h{g:0{dw // 4}X}, "
            f"bad: {dw}'h{b:0{dw // 4}X}, msk: {dw}'h{m:0{dw // 4}X} }};   // {nm}"
            for i, (off, g, b, m, nm) in enumerate(bads))
        wcount = f"\nInteger nwl   = {len(bads)};"
        wfunc = f"""
typedef struct {{
  Bit#({aw}) off;
  Bit#({dw}) good;
  Bit#({dw}) bad;
  Bit#({dw}) msk;
}} Wl deriving (Bits);

function Wl wl(Integer i);
  case (i)
{wbody}
    default: return Wl {{ off: 0, good: 0, bad: 0, msk: 0 }};
  endcase
endfunction
"""
        wregs = "\n  Reg#(Bool)     ph3  <- mkReg(False);\n  Reg#(Bit#(16)) wstp <- mkReg(0);"
        race_guard = "ph2 && !ph3"
        race_done = "      ph3 <= True;"
        wrule = """

  // 第三段：非法写保持原值。先写合法的界，再写紧挨着它的非法值，读回必须还是那个界
  rule warl (ph3);
    Bit#(16) n = wstp / 3;
    Bit#(16) p = wstp % 3;
    if (n >= fromInteger(nwl)) begin
      if (bad) $display("FAILED");
      else $display("PASS all %0d register checks, %0d set-vs-clear races and %0d illegal writes",
                    nchk, nrace, nwl);
      $finish(bad ? 1 : 0);
    end else begin
      Wl c = wl(0);
      for (Integer j = 0; j < nwl; j = j + 1)
        if (n == fromInteger(j)) c = wl(j);
      if (p == 2) begin
        let x <- r.regs.access(RegReq { addr: zeroExtend(c.off), write: False,
                                        wdata: 0, wstrb: '1 });
        if ((x.rdata & c.msk) != c.good) begin
          $display("FAIL an illegal write at %0h was taken: %08h after %08h read back %08h",
                   c.off, c.bad, c.good, x.rdata);
          bad <= True;
        end
      end else begin
        let _ <- r.regs.access(RegReq { addr: zeroExtend(c.off), write: True,
                                        wdata: p == 0 ? c.good : c.bad, wstrb: '1 });
      end
      wstp <= wstp + 1;
    end
  endrule"""

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
Integer nrace = {len(races)};{wcount}

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
{wfunc}
(* synthesize *)
module mk{C}RegsTb{suffix}(Empty);
  {C}RegsIfc#({targs}) r <- mk{C}Regs({cfg});

  Reg#(Bit#(16)) step <- mkReg(0);
  Reg#(Bit#(16)) rstp <- mkReg(0);
  Reg#(Bool)     bad  <- mkReg(False);
  Reg#(Bool)     ph2  <- mkReg(False);{wregs}

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
  rule race ({race_guard});
    Bit#(16) n = rstp >> 1;
    Bit#(1)  p = truncate(rstp);
    if (n >= fromInteger(nrace)) begin
{race_done}
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
  endrule{wrule}
endmodule

endpackage
"""
