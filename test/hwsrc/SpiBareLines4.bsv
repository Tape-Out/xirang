package SpiBareLines4;

// 由 ip.yaml 的 emit 段生成，勿手改。中立顶层：价目表量的就是这一层。

import RegIf::*;
import Spi::*;

interface SpiBareLines4Ifc;
  interface RegIf#(8, 32) regs;
  interface SpiPins#(1, 4) pins;
  (* always_ready *) method Bool irq;
endinterface

(* synthesize *)
(* default_clock_osc = "clk", default_reset = "rst_n" *)
module mkSpiBareLines4_8_1_4(SpiBareLines4Ifc);
  SpiIfc#(8, 32, 8, 1, 4) m <- mkSpi(SpiCfg {  });
  interface regs = m.regs;
  interface pins = m.pins;
  method Bool irq = m.irq;
endmodule

endpackage
