// PF Sequencer — microcode-driven FSM for 6D particle filter
//
// Reads microcode ROM entries, routes operands from memory/registers to ALU,
// waits for result, writes to destination. Drives predict + weight phases
// for up to 128 particles with 6 state dimensions.
//
// Predict phase: inner loop over 6 dimensions per particle.
//   - Position dims (0,1,2): preload velocity from SPRAM into TEMP2,
//     then run 4-entry position microcode (rom[0..3]).
//   - Velocity dims (3,4,5): run 2-entry velocity microcode (rom[4..5]).
//
// Weight phase: sequencer sets dim_idx = sensor_dim, runs 4-entry
//   Gaussian kernel (rom[6..9]) once per particle. External controller
//   iterates over sensors.
//
// Estimate phase is handled by pf_estimator (not the sequencer).

`include "lns8_pkg.v"

module pf_sequencer (
    input  wire        clk,
    input  wire        rst_n,

    // Control interface
    input  wire        start,
    input  wire [1:0]  phase,        // 0=predict, 1=weight
    input  wire [6:0]  n_particles,  // number of particles - 1
    output reg         done,
    output reg         busy,

    // ALU interface
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

    // Register file interface
    output reg  [3:0]  reg_raddr,
    input  wire        reg_rsign,
    input  wire [7:0]  reg_rmag,
    output reg  [3:0]  reg_waddr,
    output reg         reg_wsign,
    output reg  [7:0]  reg_wmag,
    output reg         reg_wen,

    // RNG interface
    output reg         rng_advance,
    input  wire        rng_sign,
    input  wire [7:0]  rng_mag,

    // Sensor dimension (which state dim this sensor observes)
    input  wire [2:0]  sensor_dim
);

    localparam [3:0] SRC_PARTICLE_DIM = 4'd0,
                     SRC_WEIGHT       = 4'd1;

    // States (4-bit, 16 states)
    localparam [3:0]
        S_IDLE          = 4'd0,
        S_PRELOAD_VEL   = 4'd1,   // read velocity for position dim
        S_PRELOAD_VEL_W1= 4'd2,   // wait cycle 1 (SPRAM latency)
        S_PRELOAD_VEL_W2= 4'd3,   // capture velocity into TEMP2
        S_FETCH         = 4'd4,
        S_DECODE        = 4'd5,
        S_LOAD_A        = 4'd6,   // set address for operand A
        S_LOAD_A_W1     = 4'd7,   // wait cycle 1 (NBA propagation / SPRAM latency)
        S_LOAD_A_W2     = 4'd8,   // capture operand A
        S_LOAD_B        = 4'd9,   // set address for operand B
        S_LOAD_B_W1     = 4'd10,  // wait cycle 1
        S_LOAD_B_W2     = 4'd11,  // capture operand B
        S_ISSUE         = 4'd12,
        S_WAIT          = 4'd13,
        S_STORE         = 4'd14,
        S_NEXT          = 4'd15;

    reg [3:0] state;

    // Track source type for capture logic
    reg a_from_spram, b_from_spram;

    // Microcode ROM
    reg  [4:0] ucode_addr;
    wire [23:0] ucode_word;

    pf_ucode_rom u_rom (
        .addr(ucode_addr),
        .ucode(ucode_word)
    );

    // Decoded fields
    reg [2:0]  uc_op;
    reg [3:0]  uc_a_src, uc_b_src, uc_dest;
    reg        uc_negate_a, uc_phase_end;

    reg [4:0] phase_start_addr;
    reg [6:0] particle_idx;
    reg [4:0] cur_ucode_addr;
    reg [2:0] dim_idx;

    reg        op_a_sign, op_b_sign;
    reg [7:0]  op_a_mag, op_b_mag;
    reg        res_sign;
    reg [7:0]  res_mag;

    // Position dim detection: dims 0,1,2 stored as 16-bit signed FP in SPRAM
    wire is_pos_dim = (dim_idx < 3'd3);

    // --- FP→LNS8 conversion for reading position dims from SPRAM ---
    // Combinational: converts mem_rdata (16-bit signed FP) to LNS8 {sign, mag}
    wire        fp_to_lns_sign;
    wire [7:0]  fp_to_lns_mag;

    lin_to_lns8 u_seq_l2l (
        .fp_in(mem_rdata),
        .lns_sign(fp_to_lns_sign),
        .lns_mag(fp_to_lns_mag)
    );

    // --- LNS8→FP conversion for writing position dims to SPRAM ---
    // Combinational: converts ALU result {res_sign, res_mag} to 16-bit signed FP
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

    wire signed [3:0]  wr_int  = res_mag[7:4];
    wire [3:0]         wr_frac = res_mag[3:0];
    wire [15:0]        wr_rom  = lns_to_lin_rom[wr_frac];
    wire [3:0]         wr_shr  = (~wr_int + 4'd1);
    wire [15:0]        wr_unsigned = (res_mag == `ZERO_LOG_MAG) ? 16'd0
        : (wr_int >= 0) ? (wr_rom << wr_int)
                         : (wr_rom >> wr_shr);
    wire signed [16:0] wr_wide = res_sign
        ? -$signed({1'b0, wr_unsigned})
        :  $signed({1'b0, wr_unsigned});
    wire [15:0] res_as_fp =
        (res_mag == `ZERO_LOG_MAG) ? 16'd0 :
        (wr_wide > 17'sd32767)     ? 16'h7FFF :
        (wr_wide < -17'sd32768)    ? 16'h8000 :
        wr_wide[15:0];

    // Stride-8 particle addressing
    wire [13:0] particle_base = {4'b0, particle_idx, 3'b0};

    // Next dimension after current (for predict loop control)
    wire [2:0] next_dim = dim_idx + 3'd1;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state          <= S_IDLE;
            done           <= 1'b0;
            busy           <= 1'b0;
            alu_op_valid   <= 1'b0;
            mem_wen        <= 1'b0;
            mem_ren        <= 1'b0;
            reg_wen        <= 1'b0;
            rng_advance    <= 1'b0;
            a_from_spram   <= 1'b0;
            b_from_spram   <= 1'b0;
            particle_idx   <= 7'd0;
            dim_idx        <= 3'd0;
            cur_ucode_addr <= 5'd0;
        end else begin
            done         <= 1'b0;
            alu_op_valid <= 1'b0;
            mem_wen      <= 1'b0;
            mem_ren      <= 1'b0;
            reg_wen      <= 1'b0;
            rng_advance  <= 1'b0;

            case (state)
                S_IDLE: begin
                    if (start) begin
                        busy         <= 1'b1;
                        particle_idx <= 7'd0;
                        case (phase)
                            2'd0: begin // predict — velocity dims only (pos handled by pf_pos_predict)
                                dim_idx        <= 3'd3;
                                cur_ucode_addr <= 5'd4;
                                phase_start_addr <= 5'd4;
                                state          <= S_FETCH;
                            end
                            2'd1: begin // weight — use sensor_dim, no dim loop
                                dim_idx        <= sensor_dim;
                                cur_ucode_addr <= 5'd6;
                                phase_start_addr <= 5'd6;
                                state          <= S_FETCH;
                            end
                            default: begin
                                cur_ucode_addr <= 5'd0;
                                phase_start_addr <= 5'd0;
                                dim_idx        <= 3'd0;
                                state          <= S_FETCH;
                            end
                        endcase
                    end
                end

                // --- Preload velocity for position dimensions ---
                S_PRELOAD_VEL: begin
                    // Read velocity at particle_base + dim_idx + 3
                    mem_addr <= particle_base + {11'b0, dim_idx + 3'd3};
                    mem_ren  <= 1'b1;
                    state    <= S_PRELOAD_VEL_W1;
                end

                S_PRELOAD_VEL_W1: begin
                    state <= S_PRELOAD_VEL_W2;
                end

                S_PRELOAD_VEL_W2: begin
                    // Write velocity into TEMP2 register (reg 8)
                    reg_waddr <= 4'd8;
                    reg_wsign <= mem_rdata[8];
                    reg_wmag  <= mem_rdata[7:0];
                    reg_wen   <= 1'b1;
                    state     <= S_FETCH;
                end

                // --- Microcode fetch/decode ---
                S_FETCH: begin
                    ucode_addr <= cur_ucode_addr;
                    state      <= S_DECODE;
                end

                S_DECODE: begin
                    uc_op        <= ucode_word[23:21];
                    uc_a_src     <= ucode_word[20:17];
                    uc_b_src     <= ucode_word[16:13];
                    uc_dest      <= ucode_word[12:9];
                    uc_negate_a  <= ucode_word[8];
                    uc_phase_end <= ucode_word[7];
                    state        <= S_LOAD_A;
                end

                // --- Operand A ---
                S_LOAD_A: begin
                    if (uc_a_src == SRC_PARTICLE_DIM) begin
                        mem_addr     <= particle_base + {11'b0, dim_idx};
                        mem_ren      <= 1'b1;
                        a_from_spram <= 1'b1;
                    end else if (uc_a_src == SRC_WEIGHT) begin
                        mem_addr     <= particle_base + 14'd6;
                        mem_ren      <= 1'b1;
                        a_from_spram <= 1'b1;
                    end else if (uc_a_src == 4'd10) begin // NOISE
                        rng_advance  <= 1'b1;
                        op_a_sign    <= rng_sign;
                        op_a_mag     <= rng_mag;
                        a_from_spram <= 1'b0;
                        // Noise is available immediately, skip to B
                        state <= S_LOAD_B;
                    end else begin
                        // Register file: set address, wait 1 cycle for NBA
                        reg_raddr    <= uc_a_src;
                        a_from_spram <= 1'b0;
                    end
                    if (uc_a_src != 4'd10) // noise skips wait
                        state <= S_LOAD_A_W1;
                end

                S_LOAD_A_W1: begin
                    if (a_from_spram) begin
                        // SPRAM: ren was set last cycle, read in progress
                        state <= S_LOAD_A_W2;
                    end else begin
                        // Register: NBA has propagated, combinational read valid
                        op_a_sign <= reg_rsign;
                        op_a_mag  <= reg_rmag;
                        state     <= S_LOAD_B;
                    end
                end

                S_LOAD_A_W2: begin
                    // SPRAM rdata now valid — convert if position dim
                    if (is_pos_dim && uc_a_src == SRC_PARTICLE_DIM) begin
                        op_a_sign <= fp_to_lns_sign;
                        op_a_mag  <= fp_to_lns_mag;
                    end else begin
                        op_a_sign <= mem_rdata[8];
                        op_a_mag  <= mem_rdata[7:0];
                    end
                    state <= S_LOAD_B;
                end

                // --- Operand B ---
                S_LOAD_B: begin
                    // Apply negate_a
                    if (uc_negate_a && op_a_mag != `ZERO_LOG_MAG) begin
                        op_a_sign <= ~op_a_sign;
                    end

                    if (uc_b_src == SRC_PARTICLE_DIM) begin
                        mem_addr     <= particle_base + {11'b0, dim_idx};
                        mem_ren      <= 1'b1;
                        b_from_spram <= 1'b1;
                    end else if (uc_b_src == SRC_WEIGHT) begin
                        mem_addr     <= particle_base + 14'd6;
                        mem_ren      <= 1'b1;
                        b_from_spram <= 1'b1;
                    end else if (uc_b_src == 4'd10) begin // NOISE
                        rng_advance  <= 1'b1;
                        op_b_sign    <= rng_sign;
                        op_b_mag     <= rng_mag;
                        b_from_spram <= 1'b0;
                        state <= S_ISSUE;
                    end else begin
                        reg_raddr    <= uc_b_src;
                        b_from_spram <= 1'b0;
                    end
                    if (uc_b_src != 4'd10)
                        state <= S_LOAD_B_W1;
                end

                S_LOAD_B_W1: begin
                    if (b_from_spram) begin
                        state <= S_LOAD_B_W2;
                    end else begin
                        op_b_sign <= reg_rsign;
                        op_b_mag  <= reg_rmag;
                        state     <= S_ISSUE;
                    end
                end

                S_LOAD_B_W2: begin
                    if (is_pos_dim && uc_b_src == SRC_PARTICLE_DIM) begin
                        op_b_sign <= fp_to_lns_sign;
                        op_b_mag  <= fp_to_lns_mag;
                    end else begin
                        op_b_sign <= mem_rdata[8];
                        op_b_mag  <= mem_rdata[7:0];
                    end
                    state <= S_ISSUE;
                end

                // --- ALU ---
                S_ISSUE: begin
                    if (!alu_busy) begin
                        alu_a_sign   <= op_a_sign;
                        alu_a_mag    <= op_a_mag;
                        alu_b_sign   <= op_b_sign;
                        alu_b_mag    <= op_b_mag;
                        alu_op       <= uc_op;
                        alu_op_valid <= 1'b1;
                        state        <= S_WAIT;
                    end
                end

                S_WAIT: begin
                    if (alu_r_valid) begin
                        res_sign <= alu_r_sign;
                        res_mag  <= alu_r_mag;
                        state    <= S_STORE;
                    end
                end

                // --- Store ---
                S_STORE: begin
`ifdef DEBUG_TRACE
                    // Trace ALL weight kernel final results (ucode 9 = weight update)
                    if (phase == 2'd1 && cur_ucode_addr == 5'd9)
                        $display("DBG_WFINAL p=%0d s=%0d op_a=%0d_%0d op_b=%0d_%0d -> res=%0d_%0d",
                                 particle_idx, dim_idx,
                                 op_a_sign, op_a_mag, op_b_sign, op_b_mag,
                                 res_sign, res_mag);
                    // Trace full kernel for first 4 particles (all ucode steps)
                    if (phase == 2'd1 && particle_idx < 7'd4)
                        $display("DBG_WKERNEL p=%0d ucode=%0d op_a=%0d_%0d op_b=%0d_%0d -> res=%0d_%0d dest=%0d",
                                 particle_idx, cur_ucode_addr,
                                 op_a_sign, op_a_mag, op_b_sign, op_b_mag,
                                 res_sign, res_mag, uc_dest);
`endif
                    if (uc_dest == SRC_PARTICLE_DIM) begin
                        mem_addr  <= particle_base + {11'b0, dim_idx};
                        // Position dims: convert LNS8 result to 16-bit signed FP
                        mem_wdata <= is_pos_dim ? res_as_fp : {7'b0, res_sign, res_mag};
                        mem_wen   <= 1'b1;
                    end else if (uc_dest == SRC_WEIGHT) begin
                        mem_addr  <= particle_base + 14'd6;
                        mem_wdata <= {7'b0, res_sign, res_mag};
                        mem_wen   <= 1'b1;
                    end else begin
                        reg_waddr <= uc_dest;
                        reg_wsign <= res_sign;
                        reg_wmag  <= res_mag;
                        reg_wen   <= 1'b1;
                    end
                    state <= S_NEXT;
                end

                // --- Next entry / dimension / particle ---
                S_NEXT: begin
                    if (uc_phase_end) begin
                        if (phase == 2'd0) begin
                            // ---- Predict: dimension loop ----
                            if (next_dim < 3'd6) begin
                                dim_idx <= next_dim;
                                if (next_dim < 3'd3) begin
                                    // Next position dim — preload velocity
                                    cur_ucode_addr <= 5'd0;
                                    state          <= S_PRELOAD_VEL;
                                end else begin
                                    // Velocity dim — no preload needed
                                    cur_ucode_addr <= 5'd4;
                                    state          <= S_FETCH;
                                end
                            end else begin
                                // All 6 dims done — next particle or finish
                                if (particle_idx < n_particles) begin
                                    particle_idx   <= particle_idx + 7'd1;
                                    dim_idx        <= 3'd3;
                                    cur_ucode_addr <= 5'd4;
                                    state          <= S_FETCH;
                                end else begin
                                    done  <= 1'b1;
                                    busy  <= 1'b0;
                                    state <= S_IDLE;
                                end
                            end
                        end else begin
                            // ---- Weight / Estimate: just iterate particles ----
                            if (particle_idx < n_particles) begin
                                particle_idx   <= particle_idx + 7'd1;
                                cur_ucode_addr <= phase_start_addr;
                                state          <= S_FETCH;
                            end else begin
                                done  <= 1'b1;
                                busy  <= 1'b0;
                                state <= S_IDLE;
                            end
                        end
                    end else begin
                        cur_ucode_addr <= cur_ucode_addr + 5'd1;
                        state          <= S_FETCH;
                    end
                end

                default: state <= S_IDLE;
            endcase
        end
    end

endmodule
