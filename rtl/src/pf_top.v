// PF Top — particle filter top-level integration
//
// Wires together: ALU, sequencer, resampler, estimator, memory, RNG, SPI
// A simple controller FSM sequences the phases:
//   1. SPI receives sensor data → trigger
//   2. Predict phase (sequencer)
//   3. Weight phase (sequencer, per sensor)
//   4. Resample (resampler)
//   5. Estimate + recenter (pf_estimator)
//   6. SPI sends estimate back
//
// ALU is muxed between sequencer and resampler.
// Memory is muxed: estimator > resampler > sequencer.

`include "lns8_pkg.v"

module pf_top (
    input  wire        clk,
    input  wire        rst_n,

    // SPI pins
    input  wire        spi_sclk,
    input  wire        spi_mosi,
    output wire        spi_miso,
    input  wire        spi_cs_n,

    // Debug outputs
    output wire        busy,
    output wire        step_done
);

    // Configuration (fixed for now, could be made SPI-programmable)
    localparam [6:0] N_PARTICLES = 7'd127;  // 128 particles (0-indexed)
    localparam [2:0] N_SENSORS   = 3'd2;    // 3 sensors (0-indexed)

    // =====================================================================
    // Controller FSM
    // =====================================================================
    localparam [2:0]
        C_IDLE        = 3'd0,
        C_POS_PREDICT = 3'd1,  // FP position predict (dims 0,1,2)
        C_VEL_PREDICT = 3'd2,  // LNS8 velocity predict (dims 3,4,5)
        C_WEIGHT      = 3'd3,
        C_RESAMPLE    = 3'd4,
        C_ESTIMATE    = 3'd5,  // Phase A: weighted estimate on pre-resample bank
        C_BANK_SWAP   = 3'd6,  // swap bank, resume estimator for Phase B
        C_RECENTER    = 3'd7;  // Phase B: recenter post-resample particles

    reg [2:0] ctrl_state;
    reg       step_done_r;
    assign busy      = (ctrl_state != C_IDLE);
    assign step_done = step_done_r;

    // =====================================================================
    // SPI slave
    // =====================================================================
    wire [2:0]  spi_sensor_idx;
    wire        spi_sensor_sign;
    wire [7:0]  spi_sensor_mag;
    wire        spi_sensor_valid;
    wire        spi_pf_trigger;

    reg         est_sign_out;
    reg  [7:0]  est_mag_out;
    reg         est_valid_out;

    spi_slave u_spi (
        .clk(clk), .rst_n(rst_n),
        .spi_sclk(spi_sclk), .spi_mosi(spi_mosi),
        .spi_miso(spi_miso), .spi_cs_n(spi_cs_n),
        .sensor_idx(spi_sensor_idx),
        .sensor_sign(spi_sensor_sign),
        .sensor_mag(spi_sensor_mag),
        .sensor_valid(spi_sensor_valid),
        .pf_trigger(spi_pf_trigger),
        .est_sign(est_sign_out),
        .est_mag(est_mag_out),
        .est_valid(est_valid_out)
    );

    // =====================================================================
    // RNG
    // =====================================================================
    wire        rng_advance;
    wire        rng_sign;
    wire [7:0]  rng_mag;
    wire [31:0] lfsr_raw;

    lfsr_rng u_rng (
        .clk(clk), .rst_n(rst_n),
        .advance(rng_advance),
        .seed(32'hDEAD_BEEF), .seed_load(1'b0),
        .noise_sign(rng_sign), .noise_mag(rng_mag),
        .lfsr_raw(lfsr_raw)
    );

    // =====================================================================
    // Memory
    // =====================================================================
    wire [13:0] mem_addr;
    wire [15:0] mem_wdata;
    wire        mem_wen, mem_ren;
    wire [15:0] mem_rdata;
    reg         bank_sel;

    wire [3:0]  reg_raddr;
    wire        reg_rsign;
    wire [7:0]  reg_rmag;
    wire [3:0]  reg_waddr;
    wire        reg_wsign;
    wire [7:0]  reg_wmag;
    wire        reg_wen;

    pf_memory u_mem (
        .clk(clk), .rst_n(rst_n),
        .spram_addr(mem_addr), .spram_wdata(mem_wdata),
        .spram_wen(mem_wen), .spram_ren(mem_ren),
        .spram_rdata(mem_rdata),
        .bank_sel(bank_sel),
        .reg_raddr(reg_raddr), .reg_rsign(reg_rsign), .reg_rmag(reg_rmag),
        .reg_waddr(reg_waddr), .reg_wsign(reg_wsign),
        .reg_wmag(reg_wmag), .reg_wen(reg_wen)
    );

    // =====================================================================
    // ALU
    // =====================================================================
    wire        alu_a_sign, alu_b_sign;
    wire [7:0]  alu_a_mag, alu_b_mag;
    wire [2:0]  alu_op;
    wire        alu_op_valid;
    wire        alu_r_sign;
    wire [7:0]  alu_r_mag;
    wire        alu_r_valid;
    wire        alu_busy;

    lns8_alu u_alu (
        .clk(clk), .rst_n(rst_n),
        .a_sign(alu_a_sign), .a_mag(alu_a_mag),
        .b_sign(alu_b_sign), .b_mag(alu_b_mag),
        .op(alu_op), .op_valid(alu_op_valid),
        .r_sign(alu_r_sign), .r_mag(alu_r_mag),
        .r_valid(alu_r_valid), .busy(alu_busy)
    );

    // =====================================================================
    // Sequencer
    // =====================================================================
    reg         seq_start;
    reg  [1:0]  seq_phase;
    reg  [2:0]  sensor_dim;
    wire        seq_done, seq_busy;

    wire        seq_alu_a_sign, seq_alu_b_sign;
    wire [7:0]  seq_alu_a_mag, seq_alu_b_mag;
    wire [2:0]  seq_alu_op;
    wire        seq_alu_op_valid;
    wire [13:0] seq_mem_addr;
    wire [15:0] seq_mem_wdata;
    wire        seq_mem_wen, seq_mem_ren;
    wire [3:0]  seq_reg_raddr, seq_reg_waddr;
    wire        seq_reg_wsign;
    wire [7:0]  seq_reg_wmag;
    wire        seq_reg_wen;
    wire        seq_rng_advance;

    pf_sequencer u_seq (
        .clk(clk), .rst_n(rst_n),
        .start(seq_start), .phase(seq_phase),
        .n_particles(N_PARTICLES),
        .done(seq_done), .busy(seq_busy),
        .alu_a_sign(seq_alu_a_sign), .alu_a_mag(seq_alu_a_mag),
        .alu_b_sign(seq_alu_b_sign), .alu_b_mag(seq_alu_b_mag),
        .alu_op(seq_alu_op), .alu_op_valid(seq_alu_op_valid),
        .alu_r_sign(alu_r_sign), .alu_r_mag(alu_r_mag),
        .alu_r_valid(alu_r_valid), .alu_busy(alu_busy),
        .mem_addr(seq_mem_addr), .mem_wdata(seq_mem_wdata),
        .mem_wen(seq_mem_wen), .mem_ren(seq_mem_ren),
        .mem_rdata(mem_rdata),
        .reg_raddr(seq_reg_raddr), .reg_rsign(reg_rsign), .reg_rmag(reg_rmag),
        .reg_waddr(seq_reg_waddr), .reg_wsign(seq_reg_wsign),
        .reg_wmag(seq_reg_wmag), .reg_wen(seq_reg_wen),
        .rng_advance(seq_rng_advance),
        .rng_sign(rng_sign), .rng_mag(rng_mag),
        .sensor_dim(sensor_dim)
    );

    // =====================================================================
    // Position Predict (FP-domain, dims 0,1,2)
    // =====================================================================
    reg         pp_start;
    wire        pp_done, pp_busy;

    wire        pp_alu_a_sign, pp_alu_b_sign;
    wire [7:0]  pp_alu_a_mag, pp_alu_b_mag;
    wire [2:0]  pp_alu_op;
    wire        pp_alu_op_valid;
    wire [13:0] pp_mem_addr;
    wire [15:0] pp_mem_wdata;
    wire        pp_mem_wen, pp_mem_ren;
    wire [3:0]  pp_reg_raddr;
    wire        pp_rng_advance;

    pf_pos_predict u_pp (
        .clk(clk), .rst_n(rst_n),
        .start(pp_start), .n_particles(N_PARTICLES),
        .done(pp_done), .busy(pp_busy),
        .alu_a_sign(pp_alu_a_sign), .alu_a_mag(pp_alu_a_mag),
        .alu_b_sign(pp_alu_b_sign), .alu_b_mag(pp_alu_b_mag),
        .alu_op(pp_alu_op), .alu_op_valid(pp_alu_op_valid),
        .alu_r_sign(alu_r_sign), .alu_r_mag(alu_r_mag),
        .alu_r_valid(alu_r_valid), .alu_busy(alu_busy),
        .mem_addr(pp_mem_addr), .mem_wdata(pp_mem_wdata),
        .mem_wen(pp_mem_wen), .mem_ren(pp_mem_ren),
        .mem_rdata(mem_rdata),
        .reg_raddr(pp_reg_raddr), .reg_rsign(reg_rsign), .reg_rmag(reg_rmag),
        .rng_advance(pp_rng_advance),
        .rng_sign(rng_sign), .rng_mag(rng_mag)
    );

    // =====================================================================
    // Resampler
    // =====================================================================
    reg         rs_start;
    wire        rs_done, rs_busy;
    wire        rs_bank_swap;
    wire [15:0] rs_weight_sum;

    wire        rs_alu_a_sign, rs_alu_b_sign;
    wire [7:0]  rs_alu_a_mag, rs_alu_b_mag;
    wire [2:0]  rs_alu_op;
    wire        rs_alu_op_valid;
    wire [13:0] rs_mem_addr;
    wire [15:0] rs_mem_wdata;
    wire        rs_mem_wen, rs_mem_ren;

    pf_resampler u_rs (
        .clk(clk), .rst_n(rst_n),
        .start(rs_start), .n_particles(N_PARTICLES),
        .done(rs_done), .busy(rs_busy),
        .weight_sum(rs_weight_sum),
        .alu_a_sign(rs_alu_a_sign), .alu_a_mag(rs_alu_a_mag),
        .alu_b_sign(rs_alu_b_sign), .alu_b_mag(rs_alu_b_mag),
        .alu_op(rs_alu_op), .alu_op_valid(rs_alu_op_valid),
        .alu_r_sign(alu_r_sign), .alu_r_mag(alu_r_mag),
        .alu_r_valid(alu_r_valid), .alu_busy(alu_busy),
        .mem_addr(rs_mem_addr), .mem_wdata(rs_mem_wdata),
        .mem_wen(rs_mem_wen), .mem_ren(rs_mem_ren),
        .mem_rdata(mem_rdata),
        .bank_sel(bank_sel), .bank_swap(rs_bank_swap),
        .lfsr_raw(lfsr_raw)
    );

    // =====================================================================
    // Estimator
    // =====================================================================
    reg         est_start;
    reg         est_resume;
    wire        est_done, est_busy;
    wire        est_phase_a_done;
    wire [13:0] est_mem_addr;
    wire [15:0] est_mem_wdata;
    wire        est_mem_wen, est_mem_ren;

    wire        est_s0, est_s1, est_s2, est_s3, est_s4, est_s5;
    wire [7:0]  est_m0, est_m1, est_m2, est_m3, est_m4, est_m5;
    wire signed [31:0] est_ref0, est_ref1, est_ref2;

    pf_estimator u_est (
        .clk(clk), .rst_n(rst_n),
        .start(est_start), .resume(est_resume),
        .n_particles(N_PARTICLES),
        .weight_sum(rs_weight_sum),
        .done(est_done), .busy(est_busy),
        .phase_a_done(est_phase_a_done),
        .mem_addr(est_mem_addr), .mem_wdata(est_mem_wdata),
        .mem_wen(est_mem_wen), .mem_ren(est_mem_ren),
        .mem_rdata(mem_rdata),
        .est_sign_0(est_s0), .est_sign_1(est_s1), .est_sign_2(est_s2),
        .est_sign_3(est_s3), .est_sign_4(est_s4), .est_sign_5(est_s5),
        .est_mag_0(est_m0), .est_mag_1(est_m1), .est_mag_2(est_m2),
        .est_mag_3(est_m3), .est_mag_4(est_m4), .est_mag_5(est_m5),
        .ref_pos_0(est_ref0), .ref_pos_1(est_ref1), .ref_pos_2(est_ref2)
    );

    // =====================================================================
    // ALU / Memory / RegFile / RNG mux
    //   ALU:    pos_predict > resampler > sequencer
    //   Memory: pos_predict > estimator > resampler > sequencer
    //   RegFile read: pos_predict > sequencer
    //   RNG: pos_predict > sequencer
    // =====================================================================
    wire use_pp         = pp_busy;
    wire use_estimator  = est_busy;
    wire use_resampler  = rs_busy;

    assign alu_a_sign   = use_pp ? pp_alu_a_sign   : use_resampler ? rs_alu_a_sign   : seq_alu_a_sign;
    assign alu_a_mag    = use_pp ? pp_alu_a_mag     : use_resampler ? rs_alu_a_mag     : seq_alu_a_mag;
    assign alu_b_sign   = use_pp ? pp_alu_b_sign   : use_resampler ? rs_alu_b_sign   : seq_alu_b_sign;
    assign alu_b_mag    = use_pp ? pp_alu_b_mag     : use_resampler ? rs_alu_b_mag     : seq_alu_b_mag;
    assign alu_op       = use_pp ? pp_alu_op        : use_resampler ? rs_alu_op        : seq_alu_op;
    assign alu_op_valid = use_pp ? pp_alu_op_valid  : use_resampler ? rs_alu_op_valid  : seq_alu_op_valid;

    assign mem_addr  = use_pp ? pp_mem_addr  : use_estimator ? est_mem_addr  : use_resampler ? rs_mem_addr  : seq_mem_addr;
    assign mem_wdata = use_pp ? pp_mem_wdata : use_estimator ? est_mem_wdata : use_resampler ? rs_mem_wdata : seq_mem_wdata;
    assign mem_wen   = use_pp ? pp_mem_wen   : use_estimator ? est_mem_wen   : use_resampler ? rs_mem_wen   : seq_mem_wen;
    assign mem_ren   = use_pp ? pp_mem_ren   : use_estimator ? est_mem_ren   : use_resampler ? rs_mem_ren   : seq_mem_ren;

    // Register file: pos_predict + sequencer read; sequencer + SPI write
    wire        ctrl_reg_wen;
    wire [3:0]  ctrl_reg_waddr;
    wire        ctrl_reg_wsign;
    wire [7:0]  ctrl_reg_wmag;

    assign reg_raddr = use_pp ? pp_reg_raddr : seq_reg_raddr;
    assign reg_waddr = (seq_busy) ? seq_reg_waddr : ctrl_reg_waddr;
    assign reg_wsign = (seq_busy) ? seq_reg_wsign : ctrl_reg_wsign;
    assign reg_wmag  = (seq_busy) ? seq_reg_wmag  : ctrl_reg_wmag;
    assign reg_wen   = (seq_busy) ? seq_reg_wen   : ctrl_reg_wen;

    assign rng_advance = use_pp ? pp_rng_advance : seq_rng_advance;

    // =====================================================================
    // Controller: sensor loading + register file writes
    // =====================================================================
    reg        ctrl_reg_wen_r;
    reg [3:0]  ctrl_reg_waddr_r;
    reg        ctrl_reg_wsign_r;
    reg [7:0]  ctrl_reg_wmag_r;
    reg        latched_bank_swap;

    assign ctrl_reg_wen   = ctrl_reg_wen_r;
    assign ctrl_reg_waddr = ctrl_reg_waddr_r;
    assign ctrl_reg_wsign = ctrl_reg_wsign_r;
    assign ctrl_reg_wmag  = ctrl_reg_wmag_r;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            ctrl_state       <= C_IDLE;
            pp_start         <= 1'b0;
            seq_start        <= 1'b0;
            rs_start         <= 1'b0;
            est_start        <= 1'b0;
            est_resume       <= 1'b0;
            step_done_r      <= 1'b0;
            bank_sel         <= 1'b0;
            est_valid_out    <= 1'b0;
            ctrl_reg_wen_r   <= 1'b0;
        end else begin
            pp_start       <= 1'b0;
            seq_start      <= 1'b0;
            rs_start       <= 1'b0;
            est_start      <= 1'b0;
            est_resume     <= 1'b0;
            step_done_r    <= 1'b0;
            est_valid_out  <= 1'b0;
            ctrl_reg_wen_r <= 1'b0;

            // Load sensor data from SPI into register file
            if (spi_sensor_valid && ctrl_state == C_IDLE) begin
                ctrl_reg_waddr_r <= 4'd4;
                ctrl_reg_wsign_r <= spi_sensor_sign;
                ctrl_reg_wmag_r  <= spi_sensor_mag;
                ctrl_reg_wen_r   <= 1'b1;
            end

            case (ctrl_state)
                C_IDLE: begin
                    if (spi_pf_trigger) begin
                        ctrl_state <= C_POS_PREDICT;
                        pp_start   <= 1'b1;
                    end
                end

                C_POS_PREDICT: begin
                    if (pp_done) begin
                        ctrl_state <= C_VEL_PREDICT;
                        seq_phase  <= 2'd0;  // predict (velocity dims only)
                        seq_start  <= 1'b1;
                    end
                end

                C_VEL_PREDICT: begin
                    if (seq_done) begin
                        ctrl_state <= C_WEIGHT;
                        seq_phase  <= 2'd1;  // weight
                        seq_start  <= 1'b1;
                    end
                end

                C_WEIGHT: begin
                    if (seq_done) begin
                        ctrl_state <= C_RESAMPLE;
                        rs_start   <= 1'b1;
                    end
                end

                C_RESAMPLE: begin
                    if (rs_done) begin
                        // Latch bank_swap NOW — it's a 1-cycle pulse with rs_done
                        latched_bank_swap <= rs_bank_swap;
                        // DON'T swap yet — Phase A needs pre-resample bank
                        ctrl_state <= C_ESTIMATE;
                        est_start  <= 1'b1;
                    end
                end

                C_ESTIMATE: begin
                    // Phase A runs weighted estimate on pre-resample bank
                    if (est_phase_a_done) begin
                        // Now swap bank so Phase B recenters post-resample
                        if (latched_bank_swap)
                            bank_sel <= ~bank_sel;
                        ctrl_state <= C_BANK_SWAP;
                    end
                end

                C_BANK_SWAP: begin
                    // One-cycle delay for bank_sel to propagate, then resume
                    est_resume <= 1'b1;
                    ctrl_state <= C_RECENTER;
                end

                C_RECENTER: begin
                    if (est_done) begin
                        est_sign_out  <= est_s0;
                        est_mag_out   <= est_m0;
                        est_valid_out <= 1'b1;
                        step_done_r   <= 1'b1;
                        ctrl_state    <= C_IDLE;
                    end
                end

                default: ctrl_state <= C_IDLE;
            endcase
        end
    end

endmodule
