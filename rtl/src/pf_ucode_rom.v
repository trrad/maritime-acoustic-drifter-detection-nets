// Microcode ROM — hardcoded sequences for 6D predict + weight (Gaussian)
//
// Microcode word (24 bits):
//   [23:21] op       — ALU opcode (3 bits)
//   [20:17] a_src    — operand A source (4 bits)
//   [16:13] b_src    — operand B source (4 bits)
//   [12:9]  dest     — result destination (4 bits)
//   [8]     negate_a — flip sign of operand A before issuing
//   [7]     phase_end— last entry in current phase
//   [6:0]   reserved
//
// Source/dest encoding:
//   0000 PARTICLE_DIM  SPRAM particle state[dim_idx]
//   0001 WEIGHT        SPRAM log-weight
//   0010 DT            timestep constant
//   0011 NOISE_SCALE   position process noise scaling
//   0100 SENSOR_Z      current sensor measurement
//   0101 TWO_SIGMA_SQ  sensor parameter
//   0110 TEMP0
//   0111 TEMP1
//   1000 TEMP2         (also preloaded velocity)
//   1001 TEMP3
//   1010 NOISE         fresh RNG sample
//   1011 MAX_WEIGHT    max log-weight (resampling)
//   1100 CUM_SUM       cumulative sum (resampling)
//   1101 ESTIMATE_ACC  estimate accumulator
//   1110 CONST_N       constant ln(128) in LNS8
//   1111 NOISE_SCALE_VEL  velocity process noise scaling

`include "lns8_pkg.v"

module pf_ucode_rom (
    input  wire [4:0]  addr,   // up to 32 entries
    output wire [23:0] ucode
);
    // Field positions
    localparam OP_HI = 23, OP_LO = 21;
    localparam A_HI  = 20, A_LO  = 17;
    localparam B_HI  = 16, B_LO  = 13;
    localparam D_HI  = 12, D_LO  =  9;
    localparam NEG_A =  8;
    localparam PH_END = 7;

    // Source/dest names
    localparam [3:0] PARTICLE_DIM  = 4'd0,
                     WEIGHT        = 4'd1,
                     DT            = 4'd2,
                     NOISE_SCALE   = 4'd3,
                     SENSOR_Z      = 4'd4,
                     TWO_SIGMA_SQ  = 4'd5,
                     TEMP0         = 4'd6,
                     TEMP1         = 4'd7,
                     TEMP2         = 4'd8,
                     TEMP3         = 4'd9,
                     NOISE         = 4'd10,
                     MAX_WEIGHT    = 4'd11,
                     CUM_SUM       = 4'd12,
                     ESTIMATE_ACC  = 4'd13,
                     CONST_N       = 4'd14,
                     NOISE_SCALE_VEL = 4'd15;

    // Helper function to pack microcode word
    function [23:0] uc;
        input [2:0] op;
        input [3:0] a_src, b_src, dest;
        input       neg_a, ph_end;
        uc = {op, a_src, b_src, dest, neg_a, ph_end, 7'b0};
    endfunction

    reg [23:0] rom [0:31];

    initial begin
        // =====================================================================
        // PREDICT — position dimensions (4 entries, dim_idx 0,1,2)
        // Sequencer preloads velocity into TEMP2 before these run
        // =====================================================================
        // [0] MUL TEMP2, DT → TEMP0           (vel * dt)
        rom[0]  = uc(`LNS8_OP_MUL, TEMP2,        DT,         TEMP0, 0, 0);
        // [1] ADD PARTICLE_DIM, TEMP0 → TEMP1  (pos + vel*dt)
        rom[1]  = uc(`LNS8_OP_ADD, PARTICLE_DIM, TEMP0,      TEMP1, 0, 0);
        // [2] MUL NOISE_SCALE, NOISE → TEMP0   (scale position noise)
        rom[2]  = uc(`LNS8_OP_MUL, NOISE_SCALE,  NOISE,      TEMP0, 0, 0);
        // [3] ADD TEMP1, TEMP0 → PARTICLE_DIM  (pos + noise)  [phase_end=1]
        rom[3]  = uc(`LNS8_OP_ADD, TEMP1,        TEMP0,      PARTICLE_DIM, 0, 1);

        // =====================================================================
        // PREDICT — velocity dimensions (2 entries, dim_idx 3,4,5)
        // =====================================================================
        // [4] MUL NOISE_SCALE_VEL, NOISE → TEMP0  (scale velocity noise)
        rom[4]  = uc(`LNS8_OP_MUL, NOISE_SCALE_VEL, NOISE,   TEMP0, 0, 0);
        // [5] ADD PARTICLE_DIM, TEMP0 → PARTICLE_DIM  (vel + noise)  [phase_end=1]
        rom[5]  = uc(`LNS8_OP_ADD, PARTICLE_DIM, TEMP0,      PARTICLE_DIM, 0, 1);

        // =====================================================================
        // WEIGHT — Gaussian: log p ∝ -(z-x)²/(2σ²)  (4 entries per sensor)
        // Sequencer sets dim_idx = sensor_dim before these run
        // =====================================================================
        // [6] SUB SENSOR_Z, PARTICLE_DIM → TEMP0   (z - x)
        rom[6]  = uc(`LNS8_OP_SUB, SENSOR_Z,    PARTICLE_DIM, TEMP0, 0, 0);
        // [7] MUL TEMP0, TEMP0 → TEMP1             (diff²)
        rom[7]  = uc(`LNS8_OP_MUL, TEMP0,       TEMP0,       TEMP1, 0, 0);
        // [8] DIV TEMP1, TWO_SIGMA_SQ → TEMP2      (diff²/(2σ²))
        rom[8]  = uc(`LNS8_OP_DIV, TEMP1,       TWO_SIGMA_SQ, TEMP2, 0, 0);
        // [9] SUB WEIGHT, TEMP2 → WEIGHT            (log_w -= ...)  [phase_end=1]
        rom[9]  = uc(`LNS8_OP_SUB, WEIGHT,      TEMP2,       WEIGHT, 0, 1);

        // Fill rest with NOPs (estimate handled by pf_estimator, not sequencer)
        rom[10] = 24'h0;
        rom[11] = 24'h0;
        rom[12] = 24'h0;
        rom[13] = 24'h0;
        rom[14] = 24'h0;
        rom[15] = 24'h0;
        rom[16] = 24'h0;
        rom[17] = 24'h0;
        rom[18] = 24'h0;
        rom[19] = 24'h0;
        rom[20] = 24'h0;
        rom[21] = 24'h0;
        rom[22] = 24'h0;
        rom[23] = 24'h0;
        rom[24] = 24'h0;
        rom[25] = 24'h0;
        rom[26] = 24'h0;
        rom[27] = 24'h0;
        rom[28] = 24'h0;
        rom[29] = 24'h0;
        rom[30] = 24'h0;
        rom[31] = 24'h0;
    end

    assign ucode = rom[addr];

endmodule
