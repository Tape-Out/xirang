package SpiRegs;

// 由 regmap.yaml 生成，勿手改。改 regmap.yaml 后重新生成。

import RegIf::*;
import Vector::*;

interface SpiRegsIfc#(numeric type aw, numeric type dw, numeric type fifoDepth, numeric type csWidth, numeric type lines);
  interface RegIf#(aw, dw) regs;
  (* always_ready *) method Bit#(12) sckdiv;
  (* always_ready *) method Bit#(1) sckmode_pha;
  (* always_ready *) method Bit#(1) sckmode_pol;
  (* always_ready *) method Bit#(csWidth) csid;
  (* always_ready *) method Bit#(csWidth) csdef;
  (* always_ready *) method Bit#(2) csmode;
  (* always_ready *) method Bit#(8) delay0_cssck;
  (* always_ready *) method Bit#(8) delay0_sckcs;
  (* always_ready *) method Bit#(8) delay1_intercs;
  (* always_ready *) method Bit#(8) delay1_interxfr;
  (* always_ready *) method Bit#(2) fmt_proto;
  (* always_ready *) method Bit#(1) fmt_endian;
  (* always_ready *) method Bit#(1) fmt_dir;
  (* always_ready *) method Bit#(4) fmt_len;
  (* always_ready *) method Bit#(8) txdata_data;
  (* always_ready *) method Bool txdata_data_wr;   // 软件写过一次
  (* always_ready *) method Action txdata_full_in(Bit#(1) v);
  (* always_ready *) method Action rxdata_data_in(Bit#(8) v);
  (* always_ready *) method Bool rxdata_data_rd;   // 软件读过一次
  (* always_ready *) method Action rxdata_empty_in(Bit#(1) v);
  (* always_ready *) method Bit#(3) txmark;
  (* always_ready *) method Bit#(3) rxmark;
  (* always_ready *) method Bit#(1) ie_txwm;
  (* always_ready *) method Bit#(1) ie_rxwm;
  (* always_ready *) method Action ip_txwm_in(Bit#(1) v);
  (* always_ready *) method Action ip_rxwm_in(Bit#(1) v);
endinterface

module mkSpiRegs(SpiRegsIfc#(aw, dw, fifoDepth, csWidth, lines))
    provisos (Mul#(TDiv#(dw, 8), 8, dw), Add#(_a, 8, aw), Add#(_w0, csWidth, dw), Add#(_w1, 1, dw), Add#(_w2, 12, dw), Add#(_w3, 2, dw), Add#(_w4, 3, dw), Add#(_w5, 4, dw), Add#(_w6, 8, dw));

  Reg#(Bit#(12)) sckdiv_r <- mkReg(3);
  Reg#(Bit#(1)) sckmode_pha_r <- mkReg(0);
  Reg#(Bit#(1)) sckmode_pol_r <- mkReg(0);
  Reg#(Bit#(csWidth)) csid_r <- mkReg(0);
  Reg#(Bit#(csWidth)) csdef_r <- mkReg(-1);
  Reg#(Bit#(2)) csmode_r <- mkReg(0);
  Reg#(Bit#(8)) delay0_cssck_r <- mkReg(1);
  Reg#(Bit#(8)) delay0_sckcs_r <- mkReg(1);
  Reg#(Bit#(8)) delay1_intercs_r <- mkReg(1);
  Reg#(Bit#(8)) delay1_interxfr_r <- mkReg(0);
  Reg#(Bit#(2)) fmt_proto_r <- mkReg(0);
  Reg#(Bit#(1)) fmt_endian_r <- mkReg(0);
  Reg#(Bit#(1)) fmt_dir_r <- mkReg(0);
  Reg#(Bit#(4)) fmt_len_r <- mkReg(8);
  Reg#(Bit#(8)) txdata_data_r <- mkReg(0);
  Wire#(Bit#(1)) txdata_full_r <- mkDWire(0);
  Wire#(Bit#(8)) rxdata_data_r <- mkDWire(0);
  Wire#(Bit#(1)) rxdata_empty_r <- mkDWire(0);
  Reg#(Bit#(3)) txmark_r <- mkReg(0);
  Reg#(Bit#(3)) rxmark_r <- mkReg(0);
  Reg#(Bit#(1)) ie_txwm_r <- mkReg(0);
  Reg#(Bit#(1)) ie_rxwm_r <- mkReg(0);
  Wire#(Bit#(1)) ip_txwm_r <- mkDWire(0);
  Wire#(Bit#(1)) ip_rxwm_r <- mkDWire(0);
  PulseWire txdata_data_mod <- mkPulseWire;
  PulseWire rxdata_data_acc <- mkPulseWire;

  function Bool legal_fmt_len(Bit#(4) v);
    return (v <= 8);
  endfunction

  RegIf#(aw, dw) rf = interface RegIf;
    method ActionValue#(RegRsp#(dw)) access(RegReq#(aw, dw) r);
      Bit#(dw) rd = 0;
      Bool err = True;   // 先假定未命中
      Bit#(8) off = truncate(r.addr);
      Bit#(16) offw = zeroExtend(off);
      Bit#(dw) wd = r.wdata;
      case (off)
        8'h00: begin err = False; if (r.write) sckdiv_r <= truncate(applyStrb(zeroExtend(sckdiv_r), wd, r.wstrb));
               else rd = zeroExtend(sckdiv_r); end
        8'h04: begin err = False; Bit#(dw) cur = (zeroExtend(sckmode_pha_r) << 0) |
                      (zeroExtend(sckmode_pol_r) << 1);
               Bit#(dw) nw = applyStrb(cur, wd, r.wstrb);
               if (r.write) begin
                 sckmode_pha_r <= nw[0:0];
                 sckmode_pol_r <= nw[1:1];
               end else begin
                 rd = cur;
               end end
        8'h10: begin err = False; if (r.write) csid_r <= truncate(applyStrb(zeroExtend(csid_r), wd, r.wstrb));
               else rd = zeroExtend(csid_r); end
        8'h14: begin err = False; if (r.write) csdef_r <= truncate(applyStrb(zeroExtend(csdef_r), wd, r.wstrb));
               else rd = zeroExtend(csdef_r); end
        8'h18: begin err = False; if (r.write) csmode_r <= truncate(applyStrb(zeroExtend(csmode_r), wd, r.wstrb));
               else rd = zeroExtend(csmode_r); end
        8'h28: begin err = False; Bit#(dw) cur = (zeroExtend(delay0_cssck_r) << 0) |
                      (zeroExtend(delay0_sckcs_r) << 16);
               Bit#(dw) nw = applyStrb(cur, wd, r.wstrb);
               if (r.write) begin
                 delay0_cssck_r <= nw[7:0];
                 delay0_sckcs_r <= nw[23:16];
               end else begin
                 rd = cur;
               end end
        8'h2C: begin err = False; Bit#(dw) cur = (zeroExtend(delay1_intercs_r) << 0) |
                      (zeroExtend(delay1_interxfr_r) << 16);
               Bit#(dw) nw = applyStrb(cur, wd, r.wstrb);
               if (r.write) begin
                 delay1_intercs_r <= nw[7:0];
                 delay1_interxfr_r <= nw[23:16];
               end else begin
                 rd = cur;
               end end
        8'h40: if ((valueOf(lines) == 2 || valueOf(lines) == 4 || valueOf(lines) == 8)) begin
                 err = False;
                 Bit#(dw) cur = (zeroExtend(fmt_proto_r) << 0) |
                      (zeroExtend(fmt_endian_r) << 2) |
                      (zeroExtend(fmt_dir_r) << 3) |
                      (zeroExtend(fmt_len_r) << 16);
               Bit#(dw) nw = applyStrb(cur, wd, r.wstrb);
               if (r.write) begin
                 fmt_proto_r <= nw[1:0];
                 fmt_endian_r <= nw[2:2];
                 fmt_dir_r <= nw[3:3];
                 if (legal_fmt_len(nw[19:16])) fmt_len_r <= nw[19:16];
               end else begin
                 rd = cur;
               end
               end else err = True;
        8'h48: begin err = False; Bit#(dw) cur = 0 |
                      (zeroExtend(txdata_full_r) << 31);
               Bit#(dw) nw = applyStrb(cur, wd, r.wstrb);
               if (r.write) begin
                 txdata_data_r <= nw[7:0];
                 txdata_data_mod.send();
               end else begin
                 rd = cur;
               end end
        8'h4C: begin err = False; Bit#(dw) cur = (zeroExtend(rxdata_data_r) << 0) |
                      (zeroExtend(rxdata_empty_r) << 31);
               Bit#(dw) nw = applyStrb(cur, wd, r.wstrb);
               if (r.write) begin
               end else begin
                 rd = cur;
                 rxdata_data_acc.send();
               end end
        8'h50: begin err = False; if (r.write) txmark_r <= truncate(applyStrb(zeroExtend(txmark_r), wd, r.wstrb));
               else rd = zeroExtend(txmark_r); end
        8'h54: begin err = False; if (r.write) rxmark_r <= truncate(applyStrb(zeroExtend(rxmark_r), wd, r.wstrb));
               else rd = zeroExtend(rxmark_r); end
        8'h70: begin err = False; Bit#(dw) cur = (zeroExtend(ie_txwm_r) << 0) |
                      (zeroExtend(ie_rxwm_r) << 1);
               Bit#(dw) nw = applyStrb(cur, wd, r.wstrb);
               if (r.write) begin
                 ie_txwm_r <= nw[0:0];
                 ie_rxwm_r <= nw[1:1];
               end else begin
                 rd = cur;
               end end
        8'h74: begin err = False; Bit#(dw) cur = (zeroExtend(ip_txwm_r) << 0) |
                      (zeroExtend(ip_rxwm_r) << 1);
               Bit#(dw) nw = applyStrb(cur, wd, r.wstrb);
               if (r.write) begin
               end else begin
                 rd = cur;
               end end
        default: noAction;
      endcase
      return RegRsp { rdata: rd, err: err };
    endmethod
  endinterface;

  interface regs = rf;
  method Bit#(12) sckdiv = sckdiv_r;
  method Bit#(1) sckmode_pha = sckmode_pha_r;
  method Bit#(1) sckmode_pol = sckmode_pol_r;
  method Bit#(csWidth) csid = csid_r;
  method Bit#(csWidth) csdef = csdef_r;
  method Bit#(2) csmode = csmode_r;
  method Bit#(8) delay0_cssck = delay0_cssck_r;
  method Bit#(8) delay0_sckcs = delay0_sckcs_r;
  method Bit#(8) delay1_intercs = delay1_intercs_r;
  method Bit#(8) delay1_interxfr = delay1_interxfr_r;
  method Bit#(2) fmt_proto = fmt_proto_r;
  method Bit#(1) fmt_endian = fmt_endian_r;
  method Bit#(1) fmt_dir = fmt_dir_r;
  method Bit#(4) fmt_len = fmt_len_r;
  method Bit#(8) txdata_data = txdata_data_r;
  method Bool txdata_data_wr = txdata_data_mod;
  method Action txdata_full_in(Bit#(1) v); txdata_full_r <= v; endmethod
  method Action rxdata_data_in(Bit#(8) v); rxdata_data_r <= v; endmethod
  method Bool rxdata_data_rd = rxdata_data_acc;
  method Action rxdata_empty_in(Bit#(1) v); rxdata_empty_r <= v; endmethod
  method Bit#(3) txmark = txmark_r;
  method Bit#(3) rxmark = rxmark_r;
  method Bit#(1) ie_txwm = ie_txwm_r;
  method Bit#(1) ie_rxwm = ie_rxwm_r;
  method Action ip_txwm_in(Bit#(1) v); ip_txwm_r <= v; endmethod
  method Action ip_rxwm_in(Bit#(1) v); ip_rxwm_r <= v; endmethod
endmodule

endpackage
