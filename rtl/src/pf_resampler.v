// PF Resampler — systematic resampling engine for 6D particle filter
//
// Steps:
//   1. Find max log-weight — scan weights, signed comparison (no ALU)
//   2. SUB(w, max) + EXP → LNS8 linear weights, decoded to 16-bit
//      fixed-point (8.8 format), stored at SPRAM 2048+i
//   3. Cumulative sum — integer addition, written to SPRAM 2176+i
//      (individual weights at 2048+i preserved for estimator)
//   4. Systematic resampling — integer threshold scan, copy 6 state
//      words per particle to shadow bank
//
// Steps 3-4 operate entirely in fixed-point, eliminating LNS8 ADD
// precision loss from the cumulative sum (the dominant error source
// in the old all-LNS8 resampler).
//
// Per particle: 8 words (stride 8), weight at offset 6, dims 0..5
// After completion, bank_sel should be toggled.

`include "lns8_pkg.v"

module pf_resampler (
    input  wire        clk,
    input  wire        rst_n,

    input  wire        start,
    input  wire [6:0]  n_particles,  // number of particles - 1
    output reg         done,
    output reg         busy,
    output wire [15:0] weight_sum,   // total linear weight sum (valid after done)

    // ALU interface (used only for step 2: SUB + EXP)
    output reg         alu_a_sign,
    output reg  [7:0]  alu_a_mag,
    output reg         alu_b_sign,
    output reg  [7:0]  alu_b_mag,
    output reg  [2:0]  alu_op,
    output reg         alu_op_valid,
    input  wire        alu_r_sign,
    input  wire [7:0]  alu_r_mag,
    input  wire        alu_r_valid,
    input  wire        alu_busy,

    // SPRAM interface
    output reg  [13:0] mem_addr,
    output reg  [15:0] mem_wdata,
    output reg         mem_wen,
    output reg         mem_ren,
    input  wire [15:0] mem_rdata,

    input  wire        bank_sel,
    output reg         bank_swap,

    input  wire [31:0] lfsr_raw
);

    localparam [4:0]
        R_IDLE     = 5'd0,
        // Step 1: find max
        R_FM_RD    = 5'd1,
        R_FM_W1    = 5'd2,
        R_FM_CMP   = 5'd3,
        // Step 2: SUB + EXP → fixed-point
        R_SE_RD    = 5'd4,
        R_SE_W1    = 5'd5,
        R_SE_W2    = 5'd6,
        R_SE_SUB   = 5'd7,
        R_SE_SUBW  = 5'd8,
        R_SE_EXP   = 5'd9,
        R_SE_EXPW  = 5'd10,
        // Step 3: fixed-point cumsum (no ALU)
        R_CS_INIT  = 5'd11,
        R_CS_INITW = 5'd12,
        R_CS_RD    = 5'd13,
        R_CS_W1    = 5'd14,
        R_CS_ADD   = 5'd15,  // integer add + write back
        // Step 4: systematic copy (no ALU)
        R_CP_SETUP = 5'd16,  // compute step, threshold
        R_CP_RDCUM = 5'd17,  // read cumsum[src]
        R_CP_RCUMW = 5'd18,
        R_CP_CMP   = 5'd19,  // compare vs threshold (integer)
        R_CP_RDSRC = 5'd20,  // read source particle word
        R_CP_RSRCW = 5'd21,
        R_CP_WRST  = 5'd22,  // write word to shadow
        R_CP_WRWT  = 5'd23,  // write zero weight to shadow
        R_DONE     = 5'd24;

    reg [4:0] rstate;
    reg [7:0] idx;

    // Step 1
    reg        max_sign;
    reg [7:0]  max_mag;

    // Step 3: fixed-point accumulator (8.8 format, 16-bit unsigned)
    reg [15:0] cum_linear;

    // Step 4: fixed-point step and threshold
    reg [15:0] step_linear;
    reg [15:0] thresh_linear;
    reg [6:0]  src_idx, dst_idx;
    reg [2:0]  copy_word_idx;

    // -----------------------------------------------------------------------
    // LNS8 → 16-bit fixed-point (8.8) conversion
    // -----------------------------------------------------------------------
    // Converts an LNS8 magnitude (representing a positive value in (0,1])
    // to 8.8 unsigned fixed-point. Used for EXP output in step 2.
    //
    // LNS8 mag m: value = 2^(m/16) = 2^(I + F/16) = 2^I × 2^(F/16)
    //   where I = m[7:4] (signed integer part), F = m[3:0] (fractional)
    // Fixed-point = round(value × 256) = frac_rom[F] >> (-I)

    reg [15:0] lns_to_lin_rom [0:15];  // 2^(F/16) × 256, rounded
    initial begin
        lns_to_lin_rom[0]  = 16'd256;  // 2^(0/16)  × 256 = 256.0
        lns_to_lin_rom[1]  = 16'd267;  // 2^(1/16)  × 256 = 267.3
        lns_to_lin_rom[2]  = 16'd279;  // 2^(2/16)  × 256 = 279.2
        lns_to_lin_rom[3]  = 16'd292;  // 2^(3/16)  × 256 = 291.5
        lns_to_lin_rom[4]  = 16'd305;  // 2^(4/16)  × 256 = 304.4
        lns_to_lin_rom[5]  = 16'd318;  // 2^(5/16)  × 256 = 317.9
        lns_to_lin_rom[6]  = 16'd332;  // 2^(6/16)  × 256 = 332.0
        lns_to_lin_rom[7]  = 16'd347;  // 2^(7/16)  × 256 = 346.7
        lns_to_lin_rom[8]  = 16'd362;  // 2^(8/16)  × 256 = 362.0
        lns_to_lin_rom[9]  = 16'd378;  // 2^(9/16)  × 256 = 378.1
        lns_to_lin_rom[10] = 16'd395;  // 2^(10/16) × 256 = 394.8
        lns_to_lin_rom[11] = 16'd412;  // 2^(11/16) × 256 = 412.3
        lns_to_lin_rom[12] = 16'd431;  // 2^(12/16) × 256 = 430.5
        lns_to_lin_rom[13] = 16'd450;  // 2^(13/16) × 256 = 449.6
        lns_to_lin_rom[14] = 16'd470;  // 2^(14/16) × 256 = 469.5
        lns_to_lin_rom[15] = 16'd490;  // 2^(15/16) × 256 = 490.3
    end

    // Combinational LNS8 → fixed-point decode (used in R_SE_EXPW)
    wire [3:0]  exp_frac = alu_r_mag[3:0];
    wire signed [3:0] exp_int = alu_r_mag[7:4];
    wire [3:0]  exp_shift = (exp_int >= 0) ? 4'd0 : (~exp_int + 4'd1);
    wire [15:0] exp_rom_val = lns_to_lin_rom[exp_frac];
    wire [15:0] exp_linear = (alu_r_mag == `ZERO_LOG_MAG) ? 16'd0
                           : (exp_rom_val >> exp_shift);

    // -----------------------------------------------------------------------
    // Signed LNS8 comparison (used only for step 1: find max)
    // -----------------------------------------------------------------------
    function is_greater;
        input a_sign;
        input [7:0] a_mag;
        input b_sign;
        input [7:0] b_mag;
        begin
            if (a_sign == 1'b0 && b_sign == 1'b1)
                is_greater = 1;
            else if (a_sign == 1'b1 && b_sign == 1'b0)
                is_greater = 0;
            else if (a_sign == 1'b1 && b_sign == 1'b1)
                is_greater = ($signed(a_mag) < $signed(b_mag)) ? 1 : 0;
            else
                is_greater = ($signed(a_mag) > $signed(b_mag)) ? 1 : 0;
        end
    endfunction

    // Stride-8 address functions
    function [13:0] weight_addr;
        input [6:0] i;
        weight_addr = {4'b0, i, 3'b110};
    endfunction

    function [13:0] lin_addr;
        input [6:0] i;
        lin_addr = 14'd2048 + {7'b0, i};
    endfunction

    function [13:0] shadow_state_addr;
        input [6:0] i;
        input [2:0] word;
        shadow_state_addr = {3'b0, 1'b1, i, 3'b0} + {11'b0, word};
    endfunction

    function [13:0] shadow_weight_addr;
        input [6:0] i;
        shadow_weight_addr = {3'b0, 1'b1, i, 3'b110};
    endfunction

    function [13:0] cumsum_addr;
        input [6:0] i;
        cumsum_addr = 14'd2176 + {7'b0, i};
    endfunction

    function [13:0] active_state_addr;
        input [6:0] i;
        input [2:0] word;
        active_state_addr = {4'b0, i, 3'b0} + {11'b0, word};
    endfunction

    assign weight_sum = cum_linear;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            rstate       <= R_IDLE;
            done         <= 1'b0;
            busy         <= 1'b0;
            bank_swap    <= 1'b0;
            alu_op_valid <= 1'b0;
            mem_wen      <= 1'b0;
            mem_ren      <= 1'b0;
            idx          <= 8'd0;
            max_sign     <= 1'b0;
            max_mag      <= `ZERO_LOG_MAG;
            cum_linear   <= 16'd0;
            copy_word_idx <= 3'd0;
        end else begin
            done         <= 1'b0;
            bank_swap    <= 1'b0;
            alu_op_valid <= 1'b0;
            mem_wen      <= 1'b0;
            mem_ren      <= 1'b0;

            case (rstate)
                R_IDLE: begin
                    if (start) begin
                        busy     <= 1'b1;
                        idx      <= 8'd0;
                        max_sign <= 1'b0;
                        max_mag  <= `ZERO_LOG_MAG;
                        rstate   <= R_FM_RD;
                    end
                end

                // ====================== Step 1: Find max ======================
                R_FM_RD: begin
                    mem_addr <= weight_addr(idx);
                    mem_ren  <= 1'b1;
                    rstate   <= R_FM_W1;
                end
                R_FM_W1: rstate <= R_FM_CMP;
                R_FM_CMP: begin
                    if (idx == 8'd0 || is_greater(mem_rdata[8], mem_rdata[7:0], max_sign, max_mag)) begin
                        max_sign <= mem_rdata[8];
                        max_mag  <= mem_rdata[7:0];
                    end
                    if (idx < n_particles) begin
                        idx <= idx + 8'd1; rstate <= R_FM_RD;
                    end else begin
                        idx <= 8'd0; rstate <= R_SE_RD;
                    end
                end

                // =========== Step 2: SUB+EXP → fixed-point linear weight ===========
                R_SE_RD: begin
                    mem_addr <= weight_addr(idx);
                    mem_ren  <= 1'b1;
                    rstate   <= R_SE_W1;
                end
                R_SE_W1: rstate <= R_SE_W2;
                R_SE_W2: begin
                    if (!alu_busy) begin
                        alu_a_sign   <= mem_rdata[8];
                        alu_a_mag    <= mem_rdata[7:0];
                        alu_b_sign   <= max_sign;
                        alu_b_mag    <= max_mag;
                        alu_op       <= `LNS8_OP_SUB;
                        alu_op_valid <= 1'b1;
                        rstate       <= R_SE_SUBW;
                    end else
                        rstate <= R_SE_SUB;
                end
                R_SE_SUB: begin
                    if (!alu_busy) begin
                        alu_a_sign   <= mem_rdata[8];
                        alu_a_mag    <= mem_rdata[7:0];
                        alu_b_sign   <= max_sign;
                        alu_b_mag    <= max_mag;
                        alu_op       <= `LNS8_OP_SUB;
                        alu_op_valid <= 1'b1;
                        rstate       <= R_SE_SUBW;
                    end
                end
                R_SE_SUBW: begin
                    if (alu_r_valid) begin
                        alu_a_sign   <= alu_r_sign;
                        alu_a_mag    <= alu_r_mag;
                        alu_b_sign   <= 1'b0;
                        alu_b_mag    <= `ZERO_LOG_MAG;
                        alu_op       <= `LNS8_OP_EXP;
                        alu_op_valid <= 1'b1;
                        rstate       <= R_SE_EXP;
                    end
                end
                R_SE_EXP: rstate <= R_SE_EXPW;
                R_SE_EXPW: begin
                    if (alu_r_valid) begin
                        // Convert EXP result from LNS8 to 16-bit fixed-point
                        // and store at lin_addr
                        mem_addr  <= lin_addr(idx);
                        mem_wdata <= exp_linear;
                        mem_wen   <= 1'b1;
                        if (idx < n_particles) begin
                            idx <= idx + 8'd1; rstate <= R_SE_RD;
                        end else begin
                            rstate <= R_CS_INIT;
                        end
                    end
                end

                // =========== Step 3: Fixed-point cumulative sum (no ALU) ===========
                // Writes cumsum to cumsum_addr (2176+i), preserving individual
                // weights at lin_addr (2048+i) for the estimator.
                R_CS_INIT: begin
                    mem_addr   <= lin_addr(7'd0);
                    mem_ren    <= 1'b1;
                    idx        <= 8'd0;
                    cum_linear <= 16'd0;
                    rstate     <= R_CS_INITW;
                end
                R_CS_INITW: rstate <= R_CS_ADD;
                R_CS_RD: begin
                    mem_addr <= lin_addr(idx);
                    mem_ren  <= 1'b1;
                    rstate   <= R_CS_W1;
                end
                R_CS_W1: rstate <= R_CS_ADD;
                R_CS_ADD: begin
                    // Accumulate and write cumsum to separate address range
                    cum_linear <= cum_linear + mem_rdata;
                    mem_addr   <= cumsum_addr(idx);
                    mem_wdata  <= cum_linear + mem_rdata;
                    mem_wen    <= 1'b1;
                    if (idx < {1'b0, n_particles}) begin
                        idx    <= idx + 8'd1;
                        rstate <= R_CS_RD;
                    end else begin
                        // Cumsum complete — cum_linear holds total
                        rstate <= R_CP_SETUP;
                    end
                end

                // =========== Step 4: Systematic copy (no ALU) ===========
                R_CP_SETUP: begin
                    // step = total / N (power-of-2 shift)
                    case (n_particles)
                        7'd15:  step_linear <= cum_linear >> 4;
                        7'd31:  step_linear <= cum_linear >> 5;
                        7'd63:  step_linear <= cum_linear >> 6;
                        7'd127: step_linear <= cum_linear >> 7;
                        default: step_linear <= cum_linear >> 4;
                    endcase
                    // Initial threshold = step * U where U = lfsr[7:0]/256
                    // thresh = (step * lfsr[7:0]) >> 8
                    // Computed next cycle from step_linear (needs to settle first)
                    src_idx <= 7'd0;
                    dst_idx <= 7'd0;
                    rstate  <= R_CP_RDCUM;  // will set threshold before first compare
                end

                R_CP_RDCUM: begin
                    // On first entry (dst_idx==0), compute initial threshold
                    if (dst_idx == 7'd0) begin
                        // thresh = (step * lfsr[7:0]) >> 8
                        thresh_linear <= (step_linear * {8'd0, lfsr_raw[7:0]}) >> 8;
                    end
                    mem_addr <= cumsum_addr(src_idx);
                    mem_ren  <= 1'b1;
                    rstate   <= R_CP_RCUMW;
                end
                R_CP_RCUMW: rstate <= R_CP_CMP;

                R_CP_CMP: begin
                    // Integer comparison: cumsum >= threshold?
                    if (mem_rdata >= thresh_linear) begin
                        copy_word_idx <= 3'd0;
                        rstate        <= R_CP_RDSRC;
                    end else if (src_idx < n_particles) begin
                        src_idx <= src_idx + 7'd1;
                        rstate  <= R_CP_RDCUM;
                    end else begin
                        copy_word_idx <= 3'd0;
                        rstate        <= R_CP_RDSRC;
                    end
                end

                R_CP_RDSRC: begin
                    mem_addr <= active_state_addr(src_idx, copy_word_idx);
                    mem_ren  <= 1'b1;
                    rstate   <= R_CP_RSRCW;
                end
                R_CP_RSRCW: rstate <= R_CP_WRST;

                R_CP_WRST: begin
                    mem_addr  <= shadow_state_addr(dst_idx, copy_word_idx);
                    mem_wdata <= mem_rdata;
                    mem_wen   <= 1'b1;
                    if (copy_word_idx < 3'd5) begin
                        copy_word_idx <= copy_word_idx + 3'd1;
                        rstate        <= R_CP_RDSRC;
                    end else begin
                        rstate <= R_CP_WRWT;
                    end
                end

                R_CP_WRWT: begin
                    mem_addr  <= shadow_weight_addr(dst_idx);
                    mem_wdata <= {7'b0, 1'b0, `ZERO_LOG_MAG};
                    mem_wen   <= 1'b1;

                    if (dst_idx < n_particles) begin
                        dst_idx       <= dst_idx + 7'd1;
                        // Advance threshold (integer add, no ALU)
                        thresh_linear <= thresh_linear + step_linear;
                        rstate        <= R_CP_RDCUM;
                    end else begin
                        rstate <= R_DONE;
                    end
                end

                R_DONE: begin
                    bank_swap <= 1'b1;
                    done      <= 1'b1;
                    busy      <= 1'b0;
                    rstate    <= R_IDLE;
                end

                default: rstate <= R_IDLE;
            endcase
        end
    end

endmodule
