// PF Estimator — weighted mean estimate + delta recentering
//
// Phase A: Weighted estimate on PRE-resample bank (Rao-Blackwellized)
//   For each particle i:
//     Read weight[i] from SPRAM 2048+i (individual linear weights)
//     For each dim d (0..5):
//       Read particle[i][d], convert LNS8 → signed 16-bit FP
//       MAC: acc[d] += weight × signed_fp (40-bit accumulator)
//   For each dim d:
//     est_fp[d] = acc[d] / weight_sum (sequential restoring divider)
//   → phase_a_done: controller swaps bank_sel
//
// Phase A2: Uniform mean on POST-resample bank (for recentering)
//   For each particle i, for position dims d (0,1,2):
//     Read particle[i][d], convert LNS8 → signed 16-bit FP
//     acc[d] += signed_fp (no weight multiply)
//   recenter_fp[d] = acc[d] >>> 7 (divide by 128)
//
// Phase B: Recentering using recenter_fp (self-consistent with particles)
//   For each position dim d:
//     ref_pos[d] += recenter_fp[d]
//     For each particle i:
//       particle[i][d] = lin_to_lns8(lns_to_lin(particle[i][d]) - recenter_fp[d])
//
// Phase C: Convert estimates to LNS8
//   Position dims: lin_to_lns8(ref_pos[d])
//   Velocity dims: lin_to_lns8(est_fp[d])

`include "lns8_pkg.v"

module pf_estimator (
    input  wire        clk,
    input  wire        rst_n,

    input  wire        start,
    input  wire        resume,       // pulse to start Phase A2+B after bank swap
    input  wire [6:0]  n_particles,  // number of particles - 1
    input  wire [15:0] weight_sum,   // total linear weight sum from resampler
    output reg         done,
    output reg         busy,
    output reg         phase_a_done, // pulses when Phase A complete (time to bank swap)

    // SPRAM interface
    output reg  [13:0] mem_addr,
    output reg  [15:0] mem_wdata,
    output reg         mem_wen,
    output reg         mem_ren,
    input  wire [15:0] mem_rdata,

    // Estimate outputs: 6 dims × LNS8 {sign, mag}
    output reg         est_sign_0, est_sign_1, est_sign_2,
    output reg         est_sign_3, est_sign_4, est_sign_5,
    output reg  [7:0]  est_mag_0, est_mag_1, est_mag_2,
    output reg  [7:0]  est_mag_3, est_mag_4, est_mag_5,

    // Reference positions: 3 position dims, signed 32-bit FP (24.8)
    // Wide enough for ±8M meters — any practical mission range
    output reg signed [31:0] ref_pos_0,
    output reg signed [31:0] ref_pos_1,
    output reg signed [31:0] ref_pos_2
);

    // =====================================================================
    // State encoding (5-bit for 19 states)
    // =====================================================================
    localparam [4:0]
        E_IDLE      = 5'd0,
        // Phase A: Weighted accumulation (pre-resample bank)
        E_RD_WT     = 5'd1,
        E_WT_W1     = 5'd2,
        E_WT_CAP    = 5'd3,
        E_RD_DIM    = 5'd4,
        E_DIM_W1    = 5'd5,
        E_DIM_MAC   = 5'd6,
        // Phase A: Division
        E_DIV_INIT  = 5'd7,
        E_DIV_STEP  = 5'd8,
        E_DIV_STORE = 5'd9,
        // Wait for bank swap
        E_WAIT_SWAP = 5'd10,
        // Phase A2: Uniform accumulation (post-resample bank, pos dims only)
        E_UA_RD     = 5'd11,
        E_UA_W1     = 5'd12,
        E_UA_ACC    = 5'd13,
        // Phase B: Recentering
        E_RC_RD     = 5'd14,
        E_RC_W1     = 5'd15,
        E_RC_PROC   = 5'd16,
        // Phase C: Convert to LNS8
        E_CVT       = 5'd17,
        // Done
        E_DONE      = 5'd18;

    reg [4:0] estate;

    // =====================================================================
    // Address helpers
    // =====================================================================
    function [13:0] lin_addr;
        input [6:0] i;
        lin_addr = 14'd2048 + {7'b0, i};
    endfunction

    function [13:0] particle_addr;
        input [6:0] i;
        input [2:0] d;
        particle_addr = {4'b0, i, 3'b0} + {11'b0, d};
    endfunction

    // =====================================================================
    // LNS8 → 16-bit fixed-point ROM (same as resampler)
    // =====================================================================
    reg [15:0] lns_to_lin_rom [0:15];
    initial begin
        lns_to_lin_rom[0]  = 16'd256;
        lns_to_lin_rom[1]  = 16'd267;
        lns_to_lin_rom[2]  = 16'd279;
        lns_to_lin_rom[3]  = 16'd292;
        lns_to_lin_rom[4]  = 16'd305;
        lns_to_lin_rom[5]  = 16'd318;
        lns_to_lin_rom[6]  = 16'd332;
        lns_to_lin_rom[7]  = 16'd347;
        lns_to_lin_rom[8]  = 16'd362;
        lns_to_lin_rom[9]  = 16'd378;
        lns_to_lin_rom[10] = 16'd395;
        lns_to_lin_rom[11] = 16'd412;
        lns_to_lin_rom[12] = 16'd431;
        lns_to_lin_rom[13] = 16'd450;
        lns_to_lin_rom[14] = 16'd470;
        lns_to_lin_rom[15] = 16'd490;
    end

    // =====================================================================
    // Particle value → signed 16-bit FP conversion
    // Position dims (0,1,2): mem_rdata IS already signed 16-bit FP
    // Velocity dims (3,4,5): mem_rdata is LNS8 {sign, mag} → convert via ROM
    // =====================================================================
    wire is_pos_dim = (dim_idx < 3'd3);

    // LNS8 decode (velocity dims only)
    wire        p_sign_raw = mem_rdata[8];
    wire [7:0]  p_mag_raw  = mem_rdata[7:0];
    wire signed [3:0] p_int = p_mag_raw[7:4];
    wire [3:0]        p_frac = p_mag_raw[3:0];
    wire [15:0]       p_rom_val = lns_to_lin_rom[p_frac];

    wire [3:0] p_shift_r = (~p_int + 4'd1);
    wire [15:0] p_unsigned = (p_mag_raw == `ZERO_LOG_MAG) ? 16'd0
        : (p_int >= 0) ? (p_rom_val << p_int)
                        : (p_rom_val >> p_shift_r);

    wire signed [16:0] p_wide = p_sign_raw
        ? -$signed({1'b0, p_unsigned})
        :  $signed({1'b0, p_unsigned});
    wire signed [15:0] p_lns8_decoded =
        (p_mag_raw == `ZERO_LOG_MAG) ? 16'sd0 :
        (p_wide > 17'sd32767)        ? 16'sd32767 :
        (p_wide < -17'sd32768)       ? -16'sd32768 :
        p_wide[15:0];

    // Final mux: position dims read directly as FP, velocity via LNS8 decode
    wire signed [15:0] p_signed = is_pos_dim ? $signed(mem_rdata) : p_lns8_decoded;

    // =====================================================================
    // lin_to_lns8 instance (combinational)
    // =====================================================================
    wire [15:0] l2l_in;
    wire        l2l_sign;
    wire [7:0]  l2l_mag;

    // Recentering mean offset — uses recenter_fp (from Phase A2)
    wire signed [15:0] mean_offset =
        (dim_idx == 3'd0) ? recenter_fp_0 :
        (dim_idx == 3'd1) ? recenter_fp_1 : recenter_fp_2;

    wire signed [15:0] recentered_fp = p_signed - mean_offset;

    // CVT input: position → ref_pos (truncated to 16-bit for lin_to_lns8),
    //            velocity → est_fp
    // ref_pos is 32-bit but lin_to_lns8 handles 16-bit. For positions up to
    // ~127m the lower 16 bits suffice. For larger, we saturate.
    wire signed [31:0] ref_cur =
        (dim_idx == 3'd0) ? ref_pos_0 :
        (dim_idx == 3'd1) ? ref_pos_1 : ref_pos_2;
    wire signed [15:0] ref_sat =
        (ref_cur > 32'sd32767)  ? 16'sd32767 :
        (ref_cur < -32'sd32768) ? -16'sd32768 :
        ref_cur[15:0];
    wire signed [15:0] cvt_input =
        (dim_idx < 3'd3) ? ref_sat :
        (dim_idx == 3'd3) ? est_fp_3 :
        (dim_idx == 3'd4) ? est_fp_4 : est_fp_5;

    // lin_to_lns8 only used in Phase C (CVT) now — position recentering writes FP directly
    assign l2l_in = cvt_input;

    lin_to_lns8 u_l2l (
        .fp_in(l2l_in),
        .lns_sign(l2l_sign),
        .lns_mag(l2l_mag)
    );

    // =====================================================================
    // Working registers
    // =====================================================================
    reg [6:0]  particle_idx;
    reg [2:0]  dim_idx;
    reg [15:0] cur_weight;

    // 6 accumulators, 40-bit signed (reused: Phase A weighted, Phase A2 uniform)
    reg signed [39:0] acc_0, acc_1, acc_2, acc_3, acc_4, acc_5;

    // 6 weighted estimate values from Phase A (RB quality, for output)
    reg signed [15:0] est_fp_0, est_fp_1, est_fp_2;
    reg signed [15:0] est_fp_3, est_fp_4, est_fp_5;

    // 3 uniform mean values from Phase A2 (for recentering)
    reg signed [15:0] recenter_fp_0, recenter_fp_1, recenter_fp_2;

    // Division registers
    reg [56:0] div_work;    // {17-bit partial remainder, 40-bit dividend}
    reg [5:0]  div_step;    // 0..39
    reg        div_sign;
    reg [15:0] div_divisor;

    // Division combinational logic
    wire [56:0] div_shifted = {div_work[55:0], 1'b0};
    wire [17:0] div_trial = {1'b0, div_shifted[56:40]} - {2'b0, div_divisor};
    wire        div_q_bit = ~div_trial[17];

    wire [56:0] div_next_q1 = {div_trial[16:0], div_shifted[39:1], 1'b1};
    wire [56:0] div_next_q0 = div_shifted;

    // MAC combinational logic (Phase A only)
    wire signed [16:0] weight_signed = $signed({1'b0, cur_weight});
    wire signed [32:0] mac_product = weight_signed * p_signed;

    // Accumulator read mux (for division init)
    wire signed [39:0] acc_cur =
        (dim_idx == 3'd0) ? acc_0 :
        (dim_idx == 3'd1) ? acc_1 :
        (dim_idx == 3'd2) ? acc_2 :
        (dim_idx == 3'd3) ? acc_3 :
        (dim_idx == 3'd4) ? acc_4 : acc_5;

    // Division quotient extraction
    wire [15:0] div_quot_raw = div_work[15:0];
    wire signed [15:0] div_quotient = div_sign
        ? (~div_quot_raw + 16'd1) : div_quot_raw;

    // =====================================================================
    // State machine
    // =====================================================================
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            estate       <= E_IDLE;
            done         <= 1'b0;
            busy         <= 1'b0;
            phase_a_done <= 1'b0;
            mem_wen      <= 1'b0;
            mem_ren      <= 1'b0;
            particle_idx <= 7'd0;
            dim_idx      <= 3'd0;
            cur_weight   <= 16'd0;
            acc_0 <= 40'sd0; acc_1 <= 40'sd0; acc_2 <= 40'sd0;
            acc_3 <= 40'sd0; acc_4 <= 40'sd0; acc_5 <= 40'sd0;
            est_fp_0 <= 16'sd0; est_fp_1 <= 16'sd0; est_fp_2 <= 16'sd0;
            est_fp_3 <= 16'sd0; est_fp_4 <= 16'sd0; est_fp_5 <= 16'sd0;
            recenter_fp_0 <= 16'sd0; recenter_fp_1 <= 16'sd0; recenter_fp_2 <= 16'sd0;
            ref_pos_0 <= 32'sd0; ref_pos_1 <= 32'sd0; ref_pos_2 <= 32'sd0;
            est_sign_0 <= 1'b0; est_sign_1 <= 1'b0; est_sign_2 <= 1'b0;
            est_sign_3 <= 1'b0; est_sign_4 <= 1'b0; est_sign_5 <= 1'b0;
            est_mag_0 <= `ZERO_LOG_MAG; est_mag_1 <= `ZERO_LOG_MAG;
            est_mag_2 <= `ZERO_LOG_MAG; est_mag_3 <= `ZERO_LOG_MAG;
            est_mag_4 <= `ZERO_LOG_MAG; est_mag_5 <= `ZERO_LOG_MAG;
            div_work     <= 57'd0;
            div_step     <= 6'd0;
            div_sign     <= 1'b0;
            div_divisor  <= 16'd0;
        end else begin
            done         <= 1'b0;
            phase_a_done <= 1'b0;
            mem_wen      <= 1'b0;
            mem_ren      <= 1'b0;

            case (estate)

            // =============================================================
            // IDLE
            // =============================================================
            E_IDLE: begin
                if (start) begin
                    busy         <= 1'b1;
                    particle_idx <= 7'd0;
                    dim_idx      <= 3'd0;
                    acc_0 <= 40'sd0; acc_1 <= 40'sd0; acc_2 <= 40'sd0;
                    acc_3 <= 40'sd0; acc_4 <= 40'sd0; acc_5 <= 40'sd0;
                    div_divisor  <= weight_sum;
                    estate       <= E_RD_WT;
                end
            end

            // =============================================================
            // Phase A: Weighted accumulation on pre-resample bank
            // =============================================================

            E_RD_WT: begin
                mem_addr <= lin_addr(particle_idx);
                mem_ren  <= 1'b1;
                estate   <= E_WT_W1;
            end
            E_WT_W1: estate <= E_WT_CAP;
            E_WT_CAP: begin
                cur_weight <= mem_rdata;
                dim_idx    <= 3'd0;
                estate     <= E_RD_DIM;
            end

            E_RD_DIM: begin
                mem_addr <= particle_addr(particle_idx, dim_idx);
                mem_ren  <= 1'b1;
                estate   <= E_DIM_W1;
            end
            E_DIM_W1: estate <= E_DIM_MAC;

            E_DIM_MAC: begin
                case (dim_idx)
                    3'd0: acc_0 <= acc_0 + {{7{mac_product[32]}}, mac_product};
                    3'd1: acc_1 <= acc_1 + {{7{mac_product[32]}}, mac_product};
                    3'd2: acc_2 <= acc_2 + {{7{mac_product[32]}}, mac_product};
                    3'd3: acc_3 <= acc_3 + {{7{mac_product[32]}}, mac_product};
                    3'd4: acc_4 <= acc_4 + {{7{mac_product[32]}}, mac_product};
                    3'd5: acc_5 <= acc_5 + {{7{mac_product[32]}}, mac_product};
                endcase

                if (dim_idx < 3'd5) begin
                    dim_idx <= dim_idx + 3'd1;
                    estate  <= E_RD_DIM;
                end else if (particle_idx < n_particles) begin
                    particle_idx <= particle_idx + 7'd1;
                    estate       <= E_RD_WT;
                end else begin
                    dim_idx <= 3'd0;
                    estate  <= E_DIV_INIT;
                end
            end

            // =============================================================
            // Phase A: Division — restoring divider, 40 steps per dim
            // =============================================================

            E_DIV_INIT: begin
                if (div_divisor == 16'd0) begin
                    case (dim_idx)
                        3'd0: est_fp_0 <= 16'sd0;
                        3'd1: est_fp_1 <= 16'sd0;
                        3'd2: est_fp_2 <= 16'sd0;
                        3'd3: est_fp_3 <= 16'sd0;
                        3'd4: est_fp_4 <= 16'sd0;
                        3'd5: est_fp_5 <= 16'sd0;
                    endcase
                    if (dim_idx < 3'd5) begin
                        dim_idx <= dim_idx + 3'd1;
                        estate  <= E_DIV_INIT;
                    end else begin
                        phase_a_done <= 1'b1;
                        estate       <= E_WAIT_SWAP;
                    end
                end else begin
                    div_sign <= acc_cur[39];
                    div_work <= {17'd0, acc_cur[39] ? (~acc_cur + 40'd1) : acc_cur};
                    div_step <= 6'd0;
                    estate   <= E_DIV_STEP;
                end
            end

            E_DIV_STEP: begin
                if (div_q_bit)
                    div_work <= div_next_q1;
                else
                    div_work <= div_next_q0;
                div_step <= div_step + 6'd1;
                if (div_step == 6'd39)
                    estate <= E_DIV_STORE;
            end

            E_DIV_STORE: begin
                case (dim_idx)
                    3'd0: est_fp_0 <= div_quotient;
                    3'd1: est_fp_1 <= div_quotient;
                    3'd2: est_fp_2 <= div_quotient;
                    3'd3: est_fp_3 <= div_quotient;
                    3'd4: est_fp_4 <= div_quotient;
                    3'd5: est_fp_5 <= div_quotient;
                endcase

                if (dim_idx < 3'd5) begin
                    dim_idx <= dim_idx + 3'd1;
                    estate  <= E_DIV_INIT;
                end else begin
                    phase_a_done <= 1'b1;
                    estate       <= E_WAIT_SWAP;
                end
            end

            // =============================================================
            // Wait for bank swap, then Phase A2: uniform accumulate
            // =============================================================
            E_WAIT_SWAP: begin
                if (resume) begin
                    // Clear accumulators for Phase A2 (position dims only)
                    acc_0        <= 40'sd0;
                    acc_1        <= 40'sd0;
                    acc_2        <= 40'sd0;
                    particle_idx <= 7'd0;
                    dim_idx      <= 3'd0;
                    estate       <= E_UA_RD;
                end
            end

            // =============================================================
            // Phase A2: Uniform accumulation on post-resample bank
            // Position dims only (0,1,2). No weight reads, no multiply.
            // =============================================================

            E_UA_RD: begin
                mem_addr <= particle_addr(particle_idx, dim_idx);
                mem_ren  <= 1'b1;
                estate   <= E_UA_W1;
            end
            E_UA_W1: estate <= E_UA_ACC;

            E_UA_ACC: begin
                // Accumulate p_signed directly (uniform weight = 1)
                case (dim_idx)
                    3'd0: acc_0 <= acc_0 + {{24{p_signed[15]}}, p_signed};
                    3'd1: acc_1 <= acc_1 + {{24{p_signed[15]}}, p_signed};
                    3'd2: acc_2 <= acc_2 + {{24{p_signed[15]}}, p_signed};
                endcase

                if (dim_idx < 3'd2) begin
                    dim_idx <= dim_idx + 3'd1;
                    estate  <= E_UA_RD;
                end else if (particle_idx < n_particles) begin
                    particle_idx <= particle_idx + 7'd1;
                    dim_idx      <= 3'd0;
                    estate       <= E_UA_RD;
                end else begin
                    // Phase A2 done — compute mean by shift (÷128)
                    // and start Phase B recentering
                    recenter_fp_0 <= acc_0 >>> 7;
                    recenter_fp_1 <= acc_1 >>> 7;
                    recenter_fp_2 <= acc_2 >>> 7;
                    // Update ref_pos for dim 0 (others updated during Phase B)
                    ref_pos_0    <= ref_pos_0 + (acc_0 >>> 7);
                    dim_idx      <= 3'd0;
                    particle_idx <= 7'd0;
                    estate       <= E_RC_RD;
                end
            end

            // =============================================================
            // Phase B: Recentering (position dims 0, 1, 2)
            // Uses recenter_fp from Phase A2 (self-consistent with particles)
            // =============================================================

            E_RC_RD: begin
                mem_addr <= particle_addr(particle_idx, dim_idx);
                mem_ren  <= 1'b1;
                estate   <= E_RC_W1;
            end
            E_RC_W1: estate <= E_RC_PROC;

            E_RC_PROC: begin
                // Position dims stored as 16-bit signed FP — write directly
                // No LNS8 round-trip: this eliminates the dominant error source
                mem_addr  <= particle_addr(particle_idx, dim_idx);
                mem_wdata <= recentered_fp;
                mem_wen   <= 1'b1;

                if (particle_idx < n_particles) begin
                    particle_idx <= particle_idx + 7'd1;
                    estate       <= E_RC_RD;
                end else if (dim_idx < 3'd2) begin
                    dim_idx      <= dim_idx + 3'd1;
                    particle_idx <= 7'd0;
                    case (dim_idx)
                        3'd0: ref_pos_1 <= ref_pos_1 + recenter_fp_1;
                        3'd1: ref_pos_2 <= ref_pos_2 + recenter_fp_2;
                    endcase
                    estate <= E_RC_RD;
                end else begin
                    dim_idx <= 3'd0;
                    estate  <= E_CVT;
                end
            end

            // =============================================================
            // Phase C: Convert estimates to LNS8 output registers
            // =============================================================

            E_CVT: begin
                case (dim_idx)
                    3'd0: begin est_sign_0 <= l2l_sign; est_mag_0 <= l2l_mag; end
                    3'd1: begin est_sign_1 <= l2l_sign; est_mag_1 <= l2l_mag; end
                    3'd2: begin est_sign_2 <= l2l_sign; est_mag_2 <= l2l_mag; end
                    3'd3: begin est_sign_3 <= l2l_sign; est_mag_3 <= l2l_mag; end
                    3'd4: begin est_sign_4 <= l2l_sign; est_mag_4 <= l2l_mag; end
                    3'd5: begin est_sign_5 <= l2l_sign; est_mag_5 <= l2l_mag; end
                endcase

                if (dim_idx < 3'd5) begin
                    dim_idx <= dim_idx + 3'd1;
                end else begin
                    estate <= E_DONE;
                end
            end

            // =============================================================
            E_DONE: begin
                done <= 1'b1;
                busy <= 1'b0;
                estate <= E_IDLE;
            end

            default: estate <= E_IDLE;

            endcase
        end
    end

endmodule
