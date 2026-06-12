// PF Position Predict — FP-domain position update
//
// For each particle, for each position dim d in {0,1,2}:
//   1. Read velocity (LNS8) from SPRAM[particle][d+3]
//   2. ALU MUL(vel, dt) -> vdt_lns8
//   3. ALU MUL(noise_scale, rng_noise) -> sn_lns8
//   4. Read position (16-bit signed FP) from SPRAM[particle][d]
//   5. Convert vdt_lns8 -> vdt_fp  (combinational ROM decode)
//   6. Convert sn_lns8  -> sn_fp   (combinational ROM decode)
//   7. pos_new = pos_fp + vdt_fp + sn_fp  (FP adds with saturation)
//   8. Write pos_new to SPRAM[particle][d]
//
// All multiplies go through the LNS8 ALU (MUL only).
// All additions are done in fixed-point, avoiding lossy LNS8 ADD.
//
// ~10 cycles per dim per particle.  128 x 3 x 10 = ~3840 cycles total.

`include "lns8_pkg.v"

module pf_pos_predict (
    input  wire        clk,
    input  wire        rst_n,

    // Control
    input  wire        start,
    input  wire [6:0]  n_particles,  // number of particles - 1
    output reg         done,
    output reg         busy,

    // ALU interface (MUL only)
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

    // Register file read
    output reg  [3:0]  reg_raddr,
    input  wire        reg_rsign,
    input  wire [7:0]  reg_rmag,

    // RNG
    output reg         rng_advance,
    input  wire        rng_sign,
    input  wire [7:0]  rng_mag
);

    // =====================================================================
    // FSM states
    // =====================================================================
    localparam [3:0]
        S_IDLE         = 4'd0,
        S_READ_VEL     = 4'd1,   // SPRAM addr for velocity; reg_raddr for DT
        S_READ_VEL_W1  = 4'd2,   // SPRAM latency; capture DT from reg file
        S_ISSUE_VDT    = 4'd3,   // capture vel from SPRAM; issue MUL(vel, dt)
        S_WAIT_VDT     = 4'd4,   // wait alu_r_valid; capture vdt result
        S_READ_POS     = 4'd5,   // SPRAM addr for pos; advance RNG; reg for NOISE_SCALE
        S_READ_POS_W1  = 4'd6,   // SPRAM latency; capture NOISE_SCALE from reg
        S_ISSUE_NOISE  = 4'd7,   // capture pos FP; issue MUL(noise_scale, noise)
        S_WAIT_NOISE   = 4'd8,   // wait alu_r_valid; capture sn result
        S_FP_ADD_WRITE = 4'd9,   // convert LNS8->FP, add, write
        S_NEXT         = 4'd10;  // dim/particle loop control

    reg [3:0] state;
    reg [6:0] particle_idx;
    reg [1:0] dim_idx;            // 0, 1, 2 only

    // Captured operands
    reg        dt_sign;
    reg [7:0]  dt_mag;
    reg        vdt_sign;
    reg [7:0]  vdt_mag;
    reg        noise_s;
    reg [7:0]  noise_m;
    reg        ns_sign;
    reg [7:0]  ns_mag;
    reg        sn_sign;
    reg [7:0]  sn_mag;
    reg signed [15:0] pos_fp;

    // Stride-8 particle addressing
    wire [13:0] particle_base = {4'b0, particle_idx, 3'b0};

    // =====================================================================
    // LNS8 -> signed 16-bit FP conversion (shared ROM, two decode paths)
    // =====================================================================
    reg [15:0] lns_to_lin_rom [0:15];
    initial begin
        lns_to_lin_rom[0]  = 16'd256; lns_to_lin_rom[1]  = 16'd267;
        lns_to_lin_rom[2]  = 16'd279; lns_to_lin_rom[3]  = 16'd292;
        lns_to_lin_rom[4]  = 16'd305; lns_to_lin_rom[5]  = 16'd318;
        lns_to_lin_rom[6]  = 16'd332; lns_to_lin_rom[7]  = 16'd347;
        lns_to_lin_rom[8]  = 16'd362; lns_to_lin_rom[9]  = 16'd378;
        lns_to_lin_rom[10] = 16'd395; lns_to_lin_rom[11] = 16'd412;
        lns_to_lin_rom[12] = 16'd431; lns_to_lin_rom[13] = 16'd450;
        lns_to_lin_rom[14] = 16'd470; lns_to_lin_rom[15] = 16'd490;
    end

    // --- vdt (vel * dt) decode ---
    wire signed [3:0]  vdt_int  = vdt_mag[7:4];
    wire [3:0]         vdt_frac = vdt_mag[3:0];
    wire [15:0]        vdt_rom  = lns_to_lin_rom[vdt_frac];
    wire [3:0]         vdt_shr  = (~vdt_int + 4'd1);
    wire [15:0] vdt_unsigned = (vdt_mag == `ZERO_LOG_MAG) ? 16'd0
        : (vdt_int >= 0) ? (vdt_rom << vdt_int) : (vdt_rom >> vdt_shr);
    wire signed [16:0] vdt_wide = vdt_sign
        ? -$signed({1'b0, vdt_unsigned})
        :  $signed({1'b0, vdt_unsigned});
    wire signed [15:0] vdt_fp =
        (vdt_mag == `ZERO_LOG_MAG) ? 16'sd0 :
        (vdt_wide > 17'sd32767)    ? 16'sd32767 :
        (vdt_wide < -17'sd32768)   ? -16'sd32768 :
        vdt_wide[15:0];

    // --- sn (noise_scale * noise) decode ---
    wire signed [3:0]  sn_int  = sn_mag[7:4];
    wire [3:0]         sn_frac = sn_mag[3:0];
    wire [15:0]        sn_rom  = lns_to_lin_rom[sn_frac];
    wire [3:0]         sn_shr  = (~sn_int + 4'd1);
    wire [15:0] sn_unsigned = (sn_mag == `ZERO_LOG_MAG) ? 16'd0
        : (sn_int >= 0) ? (sn_rom << sn_int) : (sn_rom >> sn_shr);
    wire signed [16:0] sn_wide = sn_sign
        ? -$signed({1'b0, sn_unsigned})
        :  $signed({1'b0, sn_unsigned});
    wire signed [15:0] sn_fp =
        (sn_mag == `ZERO_LOG_MAG) ? 16'sd0 :
        (sn_wide > 17'sd32767)    ? 16'sd32767 :
        (sn_wide < -17'sd32768)   ? -16'sd32768 :
        sn_wide[15:0];

    // --- FP addition: pos_new = pos_fp + vdt_fp + sn_fp (saturating) ---
    wire signed [16:0] sum1    = {pos_fp[15], pos_fp} + {vdt_fp[15], vdt_fp};
    wire signed [16:0] sum2    = sum1 + {sn_fp[15], sn_fp};
    wire signed [15:0] pos_new =
        (sum2 > 17'sd32767)  ? 16'sd32767 :
        (sum2 < -17'sd32768) ? -16'sd32768 :
        sum2[15:0];

    // =====================================================================
    // FSM
    // =====================================================================
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state        <= S_IDLE;
            done         <= 1'b0;
            busy         <= 1'b0;
            alu_op_valid <= 1'b0;
            mem_wen      <= 1'b0;
            mem_ren      <= 1'b0;
            rng_advance  <= 1'b0;
            particle_idx <= 7'd0;
            dim_idx      <= 2'd0;
        end else begin
            // Default de-assert pulses
            done         <= 1'b0;
            alu_op_valid <= 1'b0;
            mem_wen      <= 1'b0;
            mem_ren      <= 1'b0;
            rng_advance  <= 1'b0;

            case (state)
                S_IDLE: begin
                    if (start) begin
                        busy         <= 1'b1;
                        particle_idx <= 7'd0;
                        dim_idx      <= 2'd0;
                        state        <= S_READ_VEL;
                    end
                end

                // --- Read velocity LNS8 from SPRAM[particle][dim+3] ---
                S_READ_VEL: begin
                    mem_addr  <= particle_base + {12'b0, dim_idx} + 14'd3;
                    mem_ren   <= 1'b1;
                    reg_raddr <= 4'd2;  // DT register
                    state     <= S_READ_VEL_W1;
                end

                // --- SPRAM latency; capture DT from register file ---
                S_READ_VEL_W1: begin
                    dt_sign <= reg_rsign;
                    dt_mag  <= reg_rmag;
                    state   <= S_ISSUE_VDT;
                end

                // --- Capture velocity from SPRAM; issue MUL(vel, dt) ---
                S_ISSUE_VDT: begin
                    if (!alu_busy) begin
                        alu_a_sign   <= mem_rdata[8];    // vel sign
                        alu_a_mag    <= mem_rdata[7:0];  // vel mag
                        alu_b_sign   <= dt_sign;
                        alu_b_mag    <= dt_mag;
                        alu_op       <= `LNS8_OP_MUL;
                        alu_op_valid <= 1'b1;
                        state        <= S_WAIT_VDT;
                    end
                end

                // --- Wait for vel*dt result ---
                S_WAIT_VDT: begin
                    if (alu_r_valid) begin
                        vdt_sign <= alu_r_sign;
                        vdt_mag  <= alu_r_mag;
                        state    <= S_READ_POS;
                    end
                end

                // --- Read position FP; advance RNG; read NOISE_SCALE ---
                S_READ_POS: begin
                    mem_addr    <= particle_base + {12'b0, dim_idx};
                    mem_ren     <= 1'b1;
                    reg_raddr   <= 4'd3;  // NOISE_SCALE register
                    rng_advance <= 1'b1;
                    noise_s     <= rng_sign;
                    noise_m     <= rng_mag;
                    state       <= S_READ_POS_W1;
                end

                // --- SPRAM latency; capture NOISE_SCALE from reg ---
                S_READ_POS_W1: begin
                    ns_sign <= reg_rsign;
                    ns_mag  <= reg_rmag;
                    state   <= S_ISSUE_NOISE;
                end

                // --- Capture pos FP; issue MUL(noise_scale, noise) ---
                S_ISSUE_NOISE: begin
                    pos_fp <= $signed(mem_rdata);
                    if (!alu_busy) begin
                        alu_a_sign   <= ns_sign;
                        alu_a_mag    <= ns_mag;
                        alu_b_sign   <= noise_s;
                        alu_b_mag    <= noise_m;
                        alu_op       <= `LNS8_OP_MUL;
                        alu_op_valid <= 1'b1;
                        state        <= S_WAIT_NOISE;
                    end
                end

                // --- Wait for noise_scale*noise result ---
                S_WAIT_NOISE: begin
                    if (alu_r_valid) begin
                        sn_sign <= alu_r_sign;
                        sn_mag  <= alu_r_mag;
                        state   <= S_FP_ADD_WRITE;
                    end
                end

                // --- Combinational FP add + write ---
                S_FP_ADD_WRITE: begin
                    mem_addr  <= particle_base + {12'b0, dim_idx};
                    mem_wdata <= pos_new;
                    mem_wen   <= 1'b1;
                    state     <= S_NEXT;
                end

                // --- Loop control ---
                S_NEXT: begin
                    if (dim_idx < 2'd2) begin
                        dim_idx <= dim_idx + 2'd1;
                        state   <= S_READ_VEL;
                    end else if (particle_idx < n_particles) begin
                        particle_idx <= particle_idx + 7'd1;
                        dim_idx      <= 2'd0;
                        state        <= S_READ_VEL;
                    end else begin
                        done  <= 1'b1;
                        busy  <= 1'b0;
                        state <= S_IDLE;
                    end
                end

                default: state <= S_IDLE;
            endcase
        end
    end

endmodule
