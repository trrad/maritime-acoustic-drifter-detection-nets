// PF Memory — SPRAM wrapper with dual-bank addressing + register file
//
// SPRAM layout (16K x 16-bit, using one iCE40 SPRAM block):
//   Bank A (active):  addr 0..1023    — 128 particles x 8 words each
//   Bank B (shadow):  addr 1024..2047 — 128 particles x 8 words each
//   Linear weights:   addr 2048..2175 — 128 individual weights (for estimator)
//   Cumulative sums:  addr 2176..2303 — 128 cumsum entries (for resampler step 4)
//
// Per particle (8 consecutive words, stride = 8):
//   Word 0: [15:0] signed FP (8.8)  X position offset
//   Word 1: [15:0] signed FP (8.8)  Y position offset
//   Word 2: [15:0] signed FP (8.8)  Z position offset
//   Word 3: [8]=sign, [7:0]=mag     VX velocity (LNS8)
//   Word 4: [8]=sign, [7:0]=mag     VY velocity (LNS8)
//   Word 5: [8]=sign, [7:0]=mag     VZ velocity (LNS8)
//   Word 6: [8]=sign, [7:0]=mag     log-weight  (LNS8)
//   Word 7: (padding)
//
// Bank select is a 1-bit toggle that flips address bit 10.
// After resampling, banks swap.
//
// Register file stores constants and temporaries as flip-flops.

`include "lns8_pkg.v"

module pf_memory (
    input  wire        clk,
    input  wire        rst_n,

    // SPRAM interface
    input  wire [13:0] spram_addr,  // 14-bit address (16K words)
    input  wire [15:0] spram_wdata,
    input  wire        spram_wen,
    input  wire        spram_ren,
    output reg  [15:0] spram_rdata,

    // Bank control
    input  wire        bank_sel,    // 0=bank A active, 1=bank B active

    // Register file write port
    input  wire [3:0]  reg_waddr,
    input  wire        reg_wsign,
    input  wire [7:0]  reg_wmag,
    input  wire        reg_wen,

    // Register file read port
    input  wire [3:0]  reg_raddr,
    output reg         reg_rsign,
    output reg  [7:0]  reg_rmag
);

    // -----------------------------------------------------------------------
    // SPRAM model (behavioral — maps to SB_SPRAM256KA in synthesis)
    // -----------------------------------------------------------------------
    // iCE40UP5K SPRAM: 16K x 16-bit = 256Kbit
    // Single-port: read OR write per cycle (not both)
    reg [15:0] spram [0:16383];

    // Bank-swapped address: XOR bit 10 with bank_sel for particle banks
    // Addresses 0..2047 are particle banks (bit 10 selects A vs B)
    // Addresses 2048+ are weight array (no bank swapping)
    wire [13:0] phys_addr;
    assign phys_addr = (spram_addr < 14'd2048)
                     ? {spram_addr[13:11], spram_addr[10] ^ bank_sel, spram_addr[9:0]}
                     : spram_addr;

    always @(posedge clk) begin
        if (spram_wen) begin
            spram[phys_addr] <= spram_wdata;
        end else if (spram_ren) begin
            spram_rdata <= spram[phys_addr];
        end
    end

    // -----------------------------------------------------------------------
    // Register file (flip-flop based, ~120 LCs)
    // -----------------------------------------------------------------------
    // 16 registers x 9 bits (sign + mag)
    //
    // Register map (matches microcode source/dest encoding):
    //   0: (not used — PARTICLE_DIM comes from SPRAM)
    //   1: (not used — WEIGHT comes from SPRAM)
    //   2: DT           — timestep constant
    //   3: NOISE_SCALE  — position process noise scaling
    //   4: SENSOR_Z     — current sensor measurement (overwritten per sensor)
    //   5: TWO_SIGMA_SQ — current sensor parameter (overwritten per sensor)
    //   6: TEMP0
    //   7: TEMP1
    //   8: TEMP2        — also used for preloaded velocity
    //   9: TEMP3
    //  10: (NOISE — sourced from LFSR, not stored here)
    //  11: MAX_WEIGHT   — max log-weight for resampling
    //  12: CUM_SUM      — cumulative sum for resampling
    //  13: ESTIMATE_ACC — estimate accumulator
    //  14: CONST_N      — constant ln(128) in LNS8
    //  15: NOISE_SCALE_VEL — velocity process noise scaling

    reg        reg_sign [0:15];
    reg  [7:0] reg_mag  [0:15];

    integer i;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            for (i = 0; i < 16; i = i + 1) begin
                reg_sign[i] <= 1'b0;
                reg_mag[i]  <= `ZERO_LOG_MAG;
            end
        end else if (reg_wen) begin
            reg_sign[reg_waddr] <= reg_wsign;
            reg_mag[reg_waddr]  <= reg_wmag;
        end
    end

    // Combinational read
    always @(*) begin
        reg_rsign = reg_sign[reg_raddr];
        reg_rmag  = reg_mag[reg_raddr];
    end

endmodule
