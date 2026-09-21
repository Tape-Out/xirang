package SpiRegsTbLines4;

// 由 xirang 从 regmap.yaml 生成，勿手改。

import RegIf::*;
import SpiRegs::*;

typedef struct {
  Bit#(8) off;
  Bit#(32) ones;
  Bit#(32) zeros;
} Chk deriving (Bits);

// 硬件置位撞上软件写清那一组：clr 是能清掉它的写值，msk 是必须留下的位
typedef struct {
  Bit#(8) off;
  Bit#(32) clr;
  Bit#(32) msk;
} Rc deriving (Bits);

Integer nchk  = 14;
Integer nrace = 0;
Integer nwl   = 1;

function Rc rc(Integer i);
  case (i)

    default: return Rc { off: 0, clr: 0, msk: 0 };
  endcase
endfunction

function Chk chk(Integer i);
  case (i)
      0: return Chk { off: 8'h00, ones: 32'h00000FFF, zeros: 32'h00000000 };   // sckdiv
      1: return Chk { off: 8'h04, ones: 32'h00000003, zeros: 32'h00000000 };   // sckmode
      2: return Chk { off: 8'h10, ones: 32'h00000001, zeros: 32'h00000000 };   // csid
      3: return Chk { off: 8'h14, ones: 32'h00000001, zeros: 32'h00000000 };   // csdef
      4: return Chk { off: 8'h18, ones: 32'h00000003, zeros: 32'h00000000 };   // csmode
      5: return Chk { off: 8'h28, ones: 32'h00FF00FF, zeros: 32'h00000000 };   // delay0
      6: return Chk { off: 8'h2C, ones: 32'h00FF00FF, zeros: 32'h00000000 };   // delay1
      7: return Chk { off: 8'h40, ones: 32'h0008000F, zeros: 32'h00000000 };   // fmt
      8: return Chk { off: 8'h48, ones: 32'h00000000, zeros: 32'h00000000 };   // txdata
      9: return Chk { off: 8'h4C, ones: 32'h00000000, zeros: 32'h00000000 };   // rxdata
      10: return Chk { off: 8'h50, ones: 32'h00000007, zeros: 32'h00000000 };   // txmark
      11: return Chk { off: 8'h54, ones: 32'h00000007, zeros: 32'h00000000 };   // rxmark
      12: return Chk { off: 8'h70, ones: 32'h00000003, zeros: 32'h00000000 };   // ie
      13: return Chk { off: 8'h74, ones: 32'h00000000, zeros: 32'h00000000 };   // ip
    default: return Chk { off: 0, ones: 0, zeros: 0 };
  endcase
endfunction

typedef struct {
  Bit#(8) off;
  Bit#(32) good;
  Bit#(32) bad;
  Bit#(32) msk;
} Wl deriving (Bits);

function Wl wl(Integer i);
  case (i)
      0: return Wl { off: 8'h40, good: 32'h00080000, bad: 32'h00090000, msk: 32'h000F0000 };   // fmt.len 8 then 9
    default: return Wl { off: 0, good: 0, bad: 0, msk: 0 };
  endcase
endfunction

(* synthesize *)
module mkSpiRegsTbLines4(Empty);
  SpiRegsIfc#(8, 32, 8, 1, 4) r <- mkSpiRegs();

  Reg#(Bit#(16)) step <- mkReg(0);
  Reg#(Bit#(16)) rstp <- mkReg(0);
  Reg#(Bool)     bad  <- mkReg(False);
  Reg#(Bool)     ph2  <- mkReg(False);
  Reg#(Bool)     ph3  <- mkReg(False);
  Reg#(Bit#(16)) wstp <- mkReg(0);

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
          let _ <- r.regs.access(RegReq { addr: zeroExtend(c.off), write: True,
                                          wdata: '1, wstrb: '1 });
        end
        1: begin
          let x <- r.regs.access(RegReq { addr: zeroExtend(c.off), write: False,
                                          wdata: 0, wstrb: '1 });
          if (x.rdata != c.ones) begin
            $display("FAIL ones at %0h: got %08h want %08h",
                     c.off, x.rdata, c.ones);
            bad <= True;
          end
        end
        2: begin
          let _ <- r.regs.access(RegReq { addr: zeroExtend(c.off), write: True,
                                          wdata: 0, wstrb: '1 });
        end
        default: begin
          let x <- r.regs.access(RegReq { addr: zeroExtend(c.off), write: False,
                                          wdata: 0, wstrb: '1 });
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

  endrule

  // 第二段：硬件置位与软件写清同一拍。置位排在总线之前，若写回时不把
  // 这一拍的置位 OR 回去，事件就被抹掉了——而 stickybit 的含义正是不会丢。
  rule race (ph2 && !ph3);
    Bit#(16) n = rstp >> 1;
    Bit#(1)  p = truncate(rstp);
    if (n >= fromInteger(nrace)) begin
      ph3 <= True;
    end else begin
      Rc c = rc(0);
      for (Integer j = 0; j < nrace; j = j + 1)
        if (n == fromInteger(j)) c = rc(j);
      if (p == 0) begin
        let _ <- r.regs.access(RegReq { addr: zeroExtend(c.off), write: True,
                                        wdata: c.clr, wstrb: '1 });
      end else begin
        let x <- r.regs.access(RegReq { addr: zeroExtend(c.off), write: False,
                                        wdata: 0, wstrb: '1 });
        if ((x.rdata & c.msk) != c.msk) begin
          $display("FAIL a hwset racing the clear at %0h was swallowed: %08h",
                   c.off, x.rdata);
          bad <= True;
        end
      end
      rstp <= rstp + 1;
    end
  endrule

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
  endrule
endmodule

endpackage
