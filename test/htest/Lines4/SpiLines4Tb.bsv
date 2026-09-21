package SpiLines4Tb;

import ConfigReg::*;
import RegIf::*;
import Spi::*;

// 由 tb/mkspitb.py 生成，勿手改。
// 这一点：fifoDepth=8 csWidth=1 lines=4

Integer nbytes = 4;

function Bit#(8) want(Bit#(8) i);
  case (i)
      0: return 8'hA5;
      1: return 8'h00;
      2: return 8'hFF;
      3: return 8'h3C;
    default: return 0;
  endcase
endfunction

Bit#(8) rSCKDIV  = 8'h00;
Bit#(8) rSCKMODE = 8'h04;
Bit#(8) rCSID    = 8'h10;
Bit#(8) rCSDEF   = 8'h14;
Bit#(8) rCSMODE  = 8'h18;
Bit#(8) rDELAY0  = 8'h28;
Bit#(8) rDELAY1  = 8'h2C;
Bit#(8) rTXMARK  = 8'h50;
Bit#(8) rRXMARK  = 8'h54;
Bit#(8) rIP      = 8'h74;
Bit#(8) rFMT     = 8'h40;
Bit#(8) rTXDATA  = 8'h48;
Bit#(8) rRXDATA  = 8'h4C;

typedef enum { Setup, Send, Recv, WmA, WmB, WmC, WmD, WmE,
               CsOffA, CsOffB, CsOffC, CsHoldA, CsHoldB, CsHoldC, CsHoldD, Flush,
               PhaA, PhaB, PhaC, PhaD, CsdA, CsdB, CsdC, CsdD, CsdE, CsdF,
               HoldA, HoldB, HoldC, HoldD, HoldE, HoldF, HoldG, HoldH, HoldI,
               HoldJ, HoldJ2, HoldJ3, HoldK, HoldL, HoldM, Flush2,
               R0W, R0S, R0Q, R0M, R1W, R1S, R1Q, R1M, R2W, R2S, R2Q, R2M, R3W, R3S, R3Q, R3M, R4W, R4S, R4Q, R4M, R5W, R5S, R5Q, R5M, R6W, R6S, R6Q, R6M, R7W, R7S, R7Q, R7M, R8W, R8S, R8Q, R8M, R9W, R9S, R9Q, R9M, R10W, R10S, R10Q, R10M, R11W, R11S, R11Q, R11M, TmChk, Flush3, OvfM, OvfA, OvfB, OvfC, Flush4,
               DirSet, DirWait, DirCheck, LsbSet, LsbWait, LsbCheck, Done }
  Phase deriving (Bits, Eq);

(* synthesize *)
module mkSpiLines4Tb(Empty);
  SpiIfc#(8, 32, 8, 1, 4) sp <- mkSpi(SpiCfg { none: 0 });

  Reg#(Phase)    ph   <- mkReg(Setup);
  Reg#(Bit#(16)) s    <- mkReg(0);
  Reg#(Bit#(8))  sent <- mkReg(0);
  Reg#(Bit#(8))  got  <- mkReg(0);
  // 监视器与超时规则都读它。用普通寄存器会与各阶段规则绕成环，bsc 把超时规则
  // 整条挡掉，cyc 恒为零，量出来的时序全是零
  Reg#(Bit#(32)) cyc  <- mkConfigReg(0);
  Reg#(Bool)     bad  <- mkReg(False);
  Reg#(Bool)     sawCs <- mkReg(False);

  // 自环：IO0 出去的接回 IO1
  Reg#(Bool) csNow  <- mkReg(False);
  Reg#(Bool) csSaw[2] <- mkCReg(2, False);
  // 片选引脚的整组快照，与第 0 根是否翻高过
  Reg#(Bit#(1)) csPins <- mkReg('1);
  Reg#(Bool) csHi[2] <- mkCReg(2, False);

  // 时序监视器：只看引脚。第 0 根片选的按下与放开、SCK 的每一次翻转
  Reg#(Bool)     onWas    <- mkReg(False);
  Reg#(Bit#(1))  sckWas   <- mkReg(0);
  Reg#(Bool)     leadPend <- mkReg(False);
  Reg#(Bit#(32)) tOn      <- mkReg(0);
  Reg#(Bit#(32)) tOff     <- mkReg(0);
  Reg#(Bit#(32)) tEdge    <- mkReg(0);
  Reg#(Bit#(32)) mLead    <- mkReg(0);
  Reg#(Bit#(32)) mTail    <- mkReg(0);
  Reg#(Bit#(32)) mInact   <- mkReg(0);
  Reg#(Bit#(32)) mGap     <- mkReg(0);
  Reg#(Bit#(32)) nOn      <- mkReg(0);
  Reg#(Bit#(32)) onMark   <- mkReg(0);
  Reg#(Bit#(8))  ovfN     <- mkReg(0);
  // 吞吐：第一帧片选按下期间 SCK 翻转几次。线数翻倍，翻转次数减半——
  // 数的是线上的位，不是寄存器，所以「四线只是名义支持」这种事瞒不过去
  Reg#(Bit#(32)) nEdgeF   <- mkReg(0);
  Reg#(Bit#(32)) mPerF    <- mkReg(0);
  Reg#(Bit#(32)) vleadA <- mkReg(0);
  Reg#(Bit#(32)) vleadB <- mkReg(0);
  Reg#(Bit#(32)) vleadC <- mkReg(0);
  Reg#(Bit#(32)) vtailA <- mkReg(0);
  Reg#(Bit#(32)) vtailB <- mkReg(0);
  Reg#(Bit#(32)) vtailC <- mkReg(0);
  Reg#(Bit#(32)) vinactA <- mkReg(0);
  Reg#(Bit#(32)) vinactC <- mkReg(0);
  Reg#(Bit#(32)) vgapA <- mkReg(0);
  Reg#(Bit#(32)) vgapB <- mkReg(0);
  Reg#(Bit#(32)) vautoA <- mkReg(0);
  Reg#(Bit#(32)) vautoB <- mkReg(0);

  rule loop;
    Bit#(4) o = sp.pins.io_o;
    sp.pins.io_i(o);
    if (sp.pins.cs_n != '1) sawCs <= True;
    // 片选的快照：读 cs_n 的规则不能同时写寄存器（会被判永不触发），
    // 所以在这里打一拍，检查规则只看寄存器
    csNow <= (sp.pins.cs_n != '1);
    if (sp.pins.cs_n != '1) csSaw[0] <= True;
    csPins <= sp.pins.cs_n;
    if (sp.pins.cs_n[0] == 1) csHi[0] <= True;

    Bool on = sp.pins.cs_n[0] == 0;
    Bit#(1) k = sp.pins.sck;
    onWas  <= on;
    sckWas <= k;
    if (on && !onWas) begin
      tOn <= cyc;
      nOn <= nOn + 1;
      mInact <= cyc - tOff;
      leadPend <= k == sckWas;
      if (k != sckWas) mLead <= 0;
    end else if (leadPend && k != sckWas) begin
      mLead <= cyc - tOn;
      leadPend <= False;
    end
    if (!on && onWas) begin
      tOff <= cyc;
      mTail <= cyc - tEdge;
    end
    // 第一帧按下期间数 SCK 翻转：线数翻倍，翻转数减半
    if (on && !onWas) nEdgeF <= 0;
    else if (on && k != sckWas) nEdgeF <= nEdgeF + 1;
    if (!on && onWas && mPerF == 0) mPerF <= nEdgeF;
    // 一帧之内相邻两沿恰好隔 H 拍，比它长的只能是帧与帧之间
    if (k != sckWas) begin
      tEdge <= cyc;
      if (cyc - tEdge > 5) mGap <= cyc - tEdge;
    end
  endrule

  rule timeout;
    cyc <= cyc + 1;
    if (cyc > 400000) begin
      $display("TIMEOUT in phase %0d", pack(ph));
      $finish(1);
    end
  endrule

  function Action wr(Bit#(8) a, Bit#(32) d) = action
    let _ <- sp.regs.access(RegReq { addr: a, write: True,
                                      wdata: d, wstrb: 4'hF });
  endaction;

  rule setup (ph == Setup);
    case (s)
      0: wr(rSCKDIV, 2);
      1: wr(rCSMODE, 0);
      default: ph <= Send;
    endcase
    if (s < 2) s <= s + 1; else s <= 0;
  endrule

  rule send (ph == Send && sent < fromInteger(nbytes));
    wr(rTXDATA, zeroExtend(want(sent)));
    sent <= sent + 1;
  endrule

  rule sendDone (ph == Send && sent == fromInteger(nbytes));
    ph <= Recv;
  endrule

  rule recv (ph == Recv);
    let x <- sp.regs.access(RegReq { addr: rRXDATA, write: False,
                                      wdata: 0, wstrb: 4'hF });
    if (x.rdata[31] == 0) begin
      if (x.rdata[7:0] != want(got)) begin
        $display("FAIL byte %0d: got %02h want %02h",
                 got, x.rdata[7:0], want(got));
        bad <= True;
      end
      got <= got + 1;
      if (got + 1 == fromInteger(nbytes)) begin ph <= WmA; s <= 0; end
    end
  endrule

  // dir = 1 是只发：线上回来的必须丢掉
  rule dirSet (ph == DirSet);
    case (s)
      0: wr(rFMT, 32'h00000008);      // proto 单线、dir = 1
      1: wr(rTXDATA, 32'h000000A5);
      default: begin ph <= DirWait; end
    endcase
    if (s < 2) s <= s + 1; else s <= 0;
  endrule

  rule dirWait (ph == DirWait);
    if (s > 600) begin ph <= DirCheck; s <= 0; end
    else s <= s + 1;
  endrule

  rule dirCheck (ph == DirCheck);
    let x <- sp.regs.access(RegReq { addr: rRXDATA, write: False,
                                      wdata: 0, wstrb: 4'hF });
    // 位 31 为一表示队列空——只发的那一帧不该留下任何东西
    if (x.rdata[31] == 0) begin
      $display("FAIL fmt.dir says send only but a byte was received: %02h",
               x.rdata[7:0]);
      bad <= True;
    end
    ph <= LsbSet;
    s  <= 0;
  endrule

  // 低位先出（19.10 表 78）。移位方向必须跟着 endian 走：取的是第 0 位就得右移。
  // 取第 0 位却左移的话，线上只出得来真正的第 0 位，后面七位全是零——
  // 而这一档此前一次都没被走过。
  rule lsbSet (ph == LsbSet);
    case (s)
      0: wr(rFMT, 32'h00080004);      // len = 8、endian = 1（低位先出）、dir = 0
      1: wr(rTXDATA, 32'h000000A5);   // 0xA5 高低位不对称，转了看得出来
      default: begin ph <= LsbWait; end
    endcase
    if (s < 2) s <= s + 1; else s <= 0;
  endrule

  rule lsbWait (ph == LsbWait);
    if (s > 600) begin ph <= LsbCheck; s <= 0; end
    else s <= s + 1;
  endrule

  rule lsbCheck (ph == LsbCheck);
    let x <- sp.regs.access(RegReq { addr: rRXDATA, write: False,
                                      wdata: 0, wstrb: 4'hF });
    if (x.rdata[31] == 1) begin
      $display("FAIL nothing came back with lsb first");
      bad <= True;
    end else if (x.rdata[7:0] != 8'hA5) begin
      $display("FAIL lsb first loops back %02h, want a5", x.rdata[7:0]);
      bad <= True;
    end
    ph <= Done;
  endrule

  // 19.15：rxwm 在接收队列**严格多于** rxmark 时才抬。门限取 1：
  // 收到一个字节时不该抬（1 不大于 1），收到两个才该抬。
  // 队列只装得下一个的那一点跳过——两个字节根本放不下。
  rule wmA (ph == WmA);
    if (fromInteger(nbytes) < 2) ph <= CsOffA;
    else begin wr(rRXMARK, 1); ph <= WmB; s <= 0; end
  endrule

  rule wmB (ph == WmB);
    wr(rTXDATA, 32'h0000005A);       // 自环回来一个字节
    ph <= WmC;
    s  <= 0;
  endrule

  rule wmC (ph == WmC);
    if (s > 600) begin ph <= WmD; s <= 0; end else s <= s + 1;
  endrule

  rule wmD (ph == WmD);
    case (s)
      0: action
           let x <- sp.regs.access(RegReq { addr: rIP, write: False,
                                             wdata: 0, wstrb: 4'hF });
           if (x.rdata[1] != 0) begin
             $display("FAIL one entry with rxmark=1 already raises rxwm");
             bad <= True;
           end
         endaction
      1: wr(rTXDATA, 32'h0000005A);   // 再来一个，凑到两条
      default: begin ph <= WmE; s <= 0; end
    endcase
    if (s < 2) s <= s + 1;
  endrule

  rule wmE (ph == WmE);
    if (s <= 600) s <= s + 1;
    else begin
      let x <- sp.regs.access(RegReq { addr: rIP, write: False,
                                        wdata: 0, wstrb: 4'hF });
      if (x.rdata[1] != 1) begin
        $display("FAIL two entries with rxmark=1 but rxwm stays low");
        bad <= True;
      end
      ph <= CsOffA;
      s  <= 0;
    end
  endrule

  // 19.8 表 73：csmode = 3 是 OFF，硬件完全不碰片选
  rule csOffA (ph == CsOffA);
    wr(rCSMODE, 3);
    csSaw[1] <= False;
    ph <= CsOffB;
  endrule

  rule csOffB (ph == CsOffB);
    wr(rTXDATA, 32'h0000005A);
    ph <= CsOffC;
    s  <= 0;
  endrule

  rule csOffC (ph == CsOffC);
    if (s <= 600) s <= s + 1;
    else begin
      if (csSaw[1]) begin
        $display("FAIL csmode is OFF but cs was asserted");
        bad <= True;
      end
      ph <= CsHoldA;
    end
  endrule

  // csmode = 2 是 HOLD：第一帧之后片选一直按住，不随帧起落
  rule csHoldA (ph == CsHoldA);
    wr(rCSMODE, 2);
    ph <= CsHoldB;
  endrule

  rule csHoldB (ph == CsHoldB);
    wr(rTXDATA, 32'h0000005A);
    ph <= CsHoldC;
    s  <= 0;
  endrule

  rule csHoldC (ph == CsHoldC);
    if (s <= 600) s <= s + 1;
    else begin
      // 这一条只看快照、不碰总线：既读 cs_n 的快照又写 csmode 的话，
      // 「排在采样规则之前」与「之后」会同时成立，bsc 把整条规则丢掉。
      if (!csNow) begin
        $display("FAIL csmode is HOLD but cs was released after the frame");
        bad <= True;
      end
      ph <= CsHoldD;
      s  <= 0;
    end
  endrule

  rule csHoldD (ph == CsHoldD);
    wr(rCSMODE, 0);                   // 放回 AUTO
    ph <= Flush;
  endrule

  // 上面几段往接收队列里塞了字节。不读空的话，后面 quad 的 dir 检查
  // 会把它们当成「只发却收到了」——那是这一台自己造出来的假失败。
  rule flush (ph == Flush);
    let x <- sp.regs.access(RegReq { addr: rRXDATA, write: False,
                                      wdata: 0, wstrb: 4'hF });
    if (x.rdata[31] == 1) ph <= PhaA;
  endrule

  // 19.6 表 69：pha = 1 是前沿移位、后沿采样。此前所有判据都跑在 pha = 0 上
  rule phaA (ph == PhaA);
    case (s)
      0: wr(rSCKMODE, 1);
      1: wr(rTXDATA, 32'h000000A5);
      default: ph <= PhaB;
    endcase
    if (s < 2) s <= s + 1; else s <= 0;
  endrule

  rule phaB (ph == PhaB);
    if (s > 600) begin ph <= PhaC; s <= 0; end
    else s <= s + 1;
  endrule

  rule phaC (ph == PhaC);
    let x <- sp.regs.access(RegReq { addr: rRXDATA, write: False,
                                      wdata: 0, wstrb: 4'hF });
    if (x.rdata[31] == 1) begin
      $display("FAIL nothing came back with pha=1");
      bad <= True;
    end else if (x.rdata[7:0] != 8'hA5) begin
      $display("FAIL with pha=1 the loop returns %02h, want a5", x.rdata[7:0]);
      bad <= True;
    end
    ph <= PhaD;
  endrule

  rule phaD (ph == PhaD);
    wr(rSCKMODE, 0);
    ph <= CsdA;
  endrule

  // 19.7：csdef 复位全一
  rule csdA (ph == CsdA);
    let x <- sp.regs.access(RegReq { addr: rCSDEF, write: False,
                                      wdata: 0, wstrb: 4'hF });
    if (x.rdata != 32'h00000001) begin
      $display("FAIL csdef reads %08h after reset, want 00000001", x.rdata);
      bad <= True;
    end
    ph <= CsdB;
  endrule

  // 非活动电平跟着 csdef 走：写零以后空闲时全低，传输时选中那一根翻高
  rule csdB (ph == CsdB);
    wr(rCSDEF, 0);
    ph <= CsdC;
    s  <= 0;
  endrule

  rule csdC (ph == CsdC);
    if (s < 64) s <= s + 1;
    else begin
      if (csPins != 0) begin
        $display("FAIL with csdef=0 the idle chip select pins read %b, want all low", csPins);
        bad <= True;
      end
      ph <= CsdD;
    end
  endrule

  rule csdD (ph == CsdD);
    wr(rTXDATA, 32'h0000005A);
    csHi[1] <= False;
    ph <= CsdE;
    s  <= 0;
  endrule

  rule csdE (ph == CsdE);
    if (s <= 600) s <= s + 1;
    else begin
      if (!csHi[1]) begin
        $display("FAIL with csdef=0 the selected chip select never went high during a frame");
        bad <= True;
      end
      ph <= CsdF;
    end
  endrule

  rule csdF (ph == CsdF);
    wr(rCSDEF, 32'h00000001);
    ph <= HoldA;
  endrule

  // 19.8：HOLD 只在值真的变了时放开
  rule holdA (ph == HoldA);
    wr(rCSMODE, 2);
    ph <= HoldB;
  endrule

  rule holdB (ph == HoldB);
    wr(rTXDATA, 32'h0000005A);
    ph <= HoldC;
    s  <= 0;
  endrule

  rule holdC (ph == HoldC);
    if (s <= 600) s <= s + 1;
    else begin
      if (csPins[0] != 0) begin
        $display("FAIL the chip select was not held before the same-value writes");
        bad <= True;
      end
      ph <= HoldD;
    end
  endrule

  rule holdD (ph == HoldD);
    wr(rCSMODE, 2);
    ph <= HoldE;
    s  <= 0;
  endrule

  rule holdE (ph == HoldE);
    if (s < 64) s <= s + 1;
    else begin
      if (csPins[0] != 0) begin
        $display("FAIL writing the same value to csmode released the held chip select");
        bad <= True;
      end
      ph <= HoldF;
    end
  endrule

  rule holdF (ph == HoldF);
    wr(rCSID, 0);
    ph <= HoldG;
    s  <= 0;
  endrule

  rule holdG (ph == HoldG);
    if (s < 64) s <= s + 1;
    else begin
      if (csPins[0] != 0) begin
        $display("FAIL writing the same value to csid released the held chip select");
        bad <= True;
      end
      ph <= HoldJ;
      s  <= 0;
    end
  endrule


  // 改选中那一根的默认电平就放开。按住时引脚锁在按下那一刻，放开与否在引脚上
  // 看不出来（两边都是低），要看下一帧：放开了才会按新的极性重新按下，第 0 根翻高
  rule holdJ (ph == HoldJ);
    wr(rCSDEF, 32'h00000000);
    ph <= HoldJ2;
    s  <= 0;
  endrule

  rule holdJ2 (ph == HoldJ2);
    if (s < 64) s <= s + 1;
    else ph <= HoldJ3;
  endrule

  rule holdJ3 (ph == HoldJ3);
    wr(rTXDATA, 32'h0000005A);
    csHi[1] <= False;
    ph <= HoldK;
    s  <= 0;
  endrule

  rule holdK (ph == HoldK);
    if (s <= 600) s <= s + 1;
    else begin
      if (!csHi[1]) begin
        $display("FAIL a csdef write that flips the selected pin did not release it: the next frame ran under the old chip select");
        bad <= True;
      end
      ph <= HoldL;
    end
  endrule

  rule holdL (ph == HoldL);
    wr(rCSDEF, 32'h00000001);
    ph <= HoldM;
  endrule

  rule holdM (ph == HoldM);
    wr(rCSMODE, 0);
    ph <= Flush2;
    s  <= 0;
  endrule

  // 上面几帧又往接收队列里塞了字节，量延时之前读空
  rule flush2 (ph == Flush2);
    let x <- sp.regs.access(RegReq { addr: rRXDATA, write: False,
                                      wdata: 0, wstrb: 4'hF });
    if (x.rdata[31] == 1) begin ph <= R0W; s <= 0; end
  endrule

  rule r0w (ph == R0W);
    case (s)
      0: wr(rSCKDIV, 32'h00000004);
      1: wr(rSCKMODE, 32'h00000000);
      2: wr(rCSMODE, 32'h00000000);
      3: wr(rDELAY0, 32'h00010001);
      4: wr(rDELAY1, 32'h00000001);
      default: ph <= R0S;
    endcase
    if (s < 5) s <= s + 1; else s <= 0;
  endrule

  rule r0s (ph == R0S);
    wr(rTXDATA, 32'h0000005A);
    if (s + 1 >= 1) begin ph <= R0Q; s <= 0; end else s <= s + 1;
  endrule

  rule r0q (ph == R0Q);
    if (s > 600) begin ph <= R0M; s <= 0; end else s <= s + 1;
  endrule

  rule r0m (ph == R0M);
    vleadA <= mLead;
    ph <= R1W;
  endrule

  rule r1w (ph == R1W);
    case (s)
      0: wr(rSCKMODE, 32'h00000001);
      default: ph <= R1S;
    endcase
    if (s < 1) s <= s + 1; else s <= 0;
  endrule

  rule r1s (ph == R1S);
    wr(rTXDATA, 32'h0000005A);
    if (s + 1 >= 1) begin ph <= R1Q; s <= 0; end else s <= s + 1;
  endrule

  rule r1q (ph == R1Q);
    if (s > 600) begin ph <= R1M; s <= 0; end else s <= s + 1;
  endrule

  rule r1m (ph == R1M);
    vleadB <= mLead;
    ph <= R2W;
  endrule

  rule r2w (ph == R2W);
    case (s)
      0: wr(rSCKMODE, 32'h00000000);
      1: wr(rDELAY0, 32'h00010003);
      default: ph <= R2S;
    endcase
    if (s < 2) s <= s + 1; else s <= 0;
  endrule

  rule r2s (ph == R2S);
    wr(rTXDATA, 32'h0000005A);
    if (s + 1 >= 1) begin ph <= R2Q; s <= 0; end else s <= s + 1;
  endrule

  rule r2q (ph == R2Q);
    if (s > 600) begin ph <= R2M; s <= 0; end else s <= s + 1;
  endrule

  rule r2m (ph == R2M);
    vleadC <= mLead;
    ph <= R3W;
  endrule

  rule r3w (ph == R3W);
    case (s)
      0: wr(rDELAY0, 32'h00010001);
      default: ph <= R3S;
    endcase
    if (s < 1) s <= s + 1; else s <= 0;
  endrule

  rule r3s (ph == R3S);
    wr(rTXDATA, 32'h0000005A);
    if (s + 1 >= 1) begin ph <= R3Q; s <= 0; end else s <= s + 1;
  endrule

  rule r3q (ph == R3Q);
    if (s > 600) begin ph <= R3M; s <= 0; end else s <= s + 1;
  endrule

  rule r3m (ph == R3M);
    vtailA <= mTail;
    ph <= R4W;
  endrule

  rule r4w (ph == R4W);
    case (s)
      0: wr(rSCKMODE, 32'h00000001);
      default: ph <= R4S;
    endcase
    if (s < 1) s <= s + 1; else s <= 0;
  endrule

  rule r4s (ph == R4S);
    wr(rTXDATA, 32'h0000005A);
    if (s + 1 >= 1) begin ph <= R4Q; s <= 0; end else s <= s + 1;
  endrule

  rule r4q (ph == R4Q);
    if (s > 600) begin ph <= R4M; s <= 0; end else s <= s + 1;
  endrule

  rule r4m (ph == R4M);
    vtailB <= mTail;
    ph <= R5W;
  endrule

  rule r5w (ph == R5W);
    case (s)
      0: wr(rSCKMODE, 32'h00000000);
      1: wr(rDELAY0, 32'h00030001);
      default: ph <= R5S;
    endcase
    if (s < 2) s <= s + 1; else s <= 0;
  endrule

  rule r5s (ph == R5S);
    wr(rTXDATA, 32'h0000005A);
    if (s + 1 >= 1) begin ph <= R5Q; s <= 0; end else s <= s + 1;
  endrule

  rule r5q (ph == R5Q);
    if (s > 600) begin ph <= R5M; s <= 0; end else s <= s + 1;
  endrule

  rule r5m (ph == R5M);
    vtailC <= mTail;
    ph <= R6W;
  endrule

  rule r6w (ph == R6W);
    case (s)
      0: wr(rDELAY0, 32'h00010001);
      1: wr(rDELAY1, 32'h00000001);
      default: ph <= R6S;
    endcase
    if (s < 2) s <= s + 1; else s <= 0;
  endrule

  rule r6s (ph == R6S);
    wr(rTXDATA, 32'h0000005A);
    if (s + 1 >= 2) begin ph <= R6Q; s <= 0; end else s <= s + 1;
  endrule

  rule r6q (ph == R6Q);
    if (s > 600) begin ph <= R6M; s <= 0; end else s <= s + 1;
  endrule

  rule r6m (ph == R6M);
    vinactA <= mInact;
    ph <= R7W;
  endrule

  rule r7w (ph == R7W);
    case (s)
      0: wr(rDELAY1, 32'h00000003);
      default: ph <= R7S;
    endcase
    if (s < 1) s <= s + 1; else s <= 0;
  endrule

  rule r7s (ph == R7S);
    wr(rTXDATA, 32'h0000005A);
    if (s + 1 >= 2) begin ph <= R7Q; s <= 0; end else s <= s + 1;
  endrule

  rule r7q (ph == R7Q);
    if (s > 600) begin ph <= R7M; s <= 0; end else s <= s + 1;
  endrule

  rule r7m (ph == R7M);
    vinactC <= mInact;
    ph <= R8W;
  endrule

  rule r8w (ph == R8W);
    case (s)
      0: wr(rDELAY1, 32'h00000001);
      1: wr(rCSMODE, 32'h00000002);
      default: ph <= R8S;
    endcase
    if (s < 2) s <= s + 1; else s <= 0;
  endrule

  rule r8s (ph == R8S);
    wr(rTXDATA, 32'h0000005A);
    if (s + 1 >= 2) begin ph <= R8Q; s <= 0; end else s <= s + 1;
  endrule

  rule r8q (ph == R8Q);
    if (s > 600) begin ph <= R8M; s <= 0; end else s <= s + 1;
  endrule

  rule r8m (ph == R8M);
    vgapA <= mGap;
    ph <= R9W;
  endrule

  rule r9w (ph == R9W);
    case (s)
      0: wr(rDELAY1, 32'h00020001);
      default: ph <= R9S;
    endcase
    if (s < 1) s <= s + 1; else s <= 0;
  endrule

  rule r9s (ph == R9S);
    wr(rTXDATA, 32'h0000005A);
    if (s + 1 >= 2) begin ph <= R9Q; s <= 0; end else s <= s + 1;
  endrule

  rule r9q (ph == R9Q);
    if (s > 600) begin ph <= R9M; s <= 0; end else s <= s + 1;
  endrule

  rule r9m (ph == R9M);
    vgapB <= mGap;
    ph <= R10W;
  endrule

  rule r10w (ph == R10W);
    case (s)
      0: wr(rCSMODE, 32'h00000000);
      1: wr(rDELAY1, 32'h00000001);
      default: ph <= R10S;
    endcase
    if (s < 2) s <= s + 1; else s <= 0;
  endrule

  rule r10s (ph == R10S);
    wr(rTXDATA, 32'h0000005A);
    if (s + 1 >= 2) begin ph <= R10Q; s <= 0; end else s <= s + 1;
  endrule

  rule r10q (ph == R10Q);
    if (s > 600) begin ph <= R10M; s <= 0; end else s <= s + 1;
  endrule

  rule r10m (ph == R10M);
    vautoA <= mGap;
    ph <= R11W;
  endrule

  rule r11w (ph == R11W);
    case (s)
      0: wr(rDELAY1, 32'h00020001);
      default: ph <= R11S;
    endcase
    if (s < 1) s <= s + 1; else s <= 0;
  endrule

  rule r11s (ph == R11S);
    wr(rTXDATA, 32'h0000005A);
    if (s + 1 >= 2) begin ph <= R11Q; s <= 0; end else s <= s + 1;
  endrule

  rule r11q (ph == R11Q);
    if (s > 600) begin ph <= R11M; s <= 0; end else s <= s + 1;
  endrule

  rule r11m (ph == R11M);
    vautoB <= mGap;
    ph <= TmChk;
  endrule

  rule tmChk (ph == TmChk);
    Bool wrong = False;
    if (vleadA - vleadB != 5) begin
      $display("FAIL with pha=0 the first clock edge should trail cs by half a period more than with pha=1: %0d and %0d cycles", vleadA, vleadB);
      wrong = True;
    end
    if (vleadB < 10) begin
      $display("FAIL cssck=1 put only %0d cycles between cs and the first clock edge, less than a period", vleadB);
      wrong = True;
    end
    if (vleadC - vleadA != 20) begin
      $display("FAIL cssck=3 should put the first clock edge two periods later than cssck=1: %0d and %0d cycles", vleadC, vleadA);
      wrong = True;
    end
    if (vtailB - vtailA != 5) begin
      $display("FAIL with pha=1 cs should be released half a period later after the last clock edge than with pha=0: %0d and %0d cycles", vtailB, vtailA);
      wrong = True;
    end
    if (vtailC - vtailA != 20) begin
      $display("FAIL sckcs=3 should release cs two periods later than sckcs=1: %0d and %0d cycles", vtailC, vtailA);
      wrong = True;
    end
    if (vinactA < 10) begin
      $display("FAIL intercs=1 kept cs inactive for only %0d cycles, less than a period", vinactA);
      wrong = True;
    end
    if (vinactC - vinactA != 20) begin
      $display("FAIL intercs=3 should keep cs inactive two periods longer than intercs=1: %0d and %0d cycles", vinactC, vinactA);
      wrong = True;
    end
    if (vgapB - vgapA != 20) begin
      $display("FAIL in HOLD, interxfr=2 should put two more periods between frames than interxfr=0: %0d and %0d cycles", vgapB, vgapA);
      wrong = True;
    end
    if (vautoB != vautoA) begin
      $display("FAIL in AUTO the gap between frames moved with interxfr, which applies only to HOLD and OFF: %0d and %0d cycles", vautoB, vautoA);
      wrong = True;
    end
    if (mPerF != 4) begin
      $display("FAIL lines=4 moves 4 bits a beat, so a frame should take 4 sck edges, not %0d", mPerF);
      wrong = True;
    end
    if (wrong) bad <= True;
    ph <= Flush3;
  endrule

  rule flush3 (ph == Flush3);
    let x <- sp.regs.access(RegReq { addr: rRXDATA, write: False,
                                      wdata: 0, wstrb: 4'hF });
    if (x.rdata[31] == 1) ph <= OvfM;
  endrule

  // 只发不读：接收队列满了以后帧照样得发出去。原来接收入队带守卫，队列一满
  // 整条移位规则停住，发满 fifoDepth 帧就卡死。AUTO 下一帧按一次片选，数按下的次数
  rule ovfM (ph == OvfM);
    onMark <= nOn;
    ovfN   <= 0;
    ph     <= OvfA;
  endrule

  rule ovfA (ph == OvfA);
    wr(rTXDATA, 32'h0000005A);
    ph <= OvfB;
    s  <= 0;
  endrule

  rule ovfB (ph == OvfB);
    if (s < 300) s <= s + 1;
    else begin
      s    <= 0;
      ovfN <= ovfN + 1;
      ph   <= (ovfN + 1 == 10) ? OvfC : OvfA;
    end
  endrule

  rule ovfC (ph == OvfC);
    if (nOn - onMark != 10) begin
      $display("FAIL only %0d of 10 frames went out while rxdata was never read", nOn - onMark);
      bad <= True;
    end
    ph <= Flush4;
  endrule

  rule flush4 (ph == Flush4);
    let x <- sp.regs.access(RegReq { addr: rRXDATA, write: False,
                                      wdata: 0, wstrb: 4'hF });
    if (x.rdata[31] == 1) begin ph <= DirSet; s <= 0; end
  endrule

  rule fin (ph == Done);
    if (!sawCs) begin
      $display("FAIL chip select never went low");
      bad <= True;
    end
    if (bad || !sawCs) $display("FAILED");
    else $display("PASS spi: loopback both ways round and at pha=1, chip select default level and hold release, the four delays, a send only frame keeps nothing, and the frame takes 2 beats on 4 lines");
    $finish((bad || !sawCs) ? 1 : 0);
  endrule
endmodule

endpackage
