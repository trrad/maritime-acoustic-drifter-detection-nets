`timescale 1ns/1ps
`include "lns8_pkg.v"

// End-to-end 6D particle filter testbench
// Bypasses SPI: loads sensors directly into registers, drives controller.
// Reads scenario from hex files, dumps results for comparison with Python.
//
// Per particle: 8 words (stride 8)
//   Words 0..5: state dimensions [x, y, z, vx, vy, vz]
//   Word 6: log-weight
//   Word 7: padding
//
// Pipeline: PREDICT → WEIGHT (per sensor) → RESAMPLE → ESTIMATE+RECENTER

module tb_pf_e2e;
    parameter N_PARTICLES = 128;
    parameter N_SENSORS   = 3;
    parameter N_STEPS     = 100;
    parameter N_DIMS      = 6;
    parameter [31:0] LFSR_SEED_DEFAULT = 32'h12345678;
    reg [31:0] lfsr_seed_val;

    reg         clk, rst_n;

    // Sequencer control (direct, bypassing pf_top controller)
    reg         seq_start;
    reg  [1:0]  seq_phase;
    reg  [2:0]  tb_sensor_dim;
    wire        seq_done, seq_busy;

    // Position predict control
    reg         pp_start;
    wire        pp_done, pp_busy;

    // Resampler control
    reg         rs_start;
    wire        rs_done, rs_busy, rs_bank_swap;
    wire [15:0] rs_weight_sum;

    // Estimator control
    reg         est_start;
    reg         est_resume;
    wire        est_done, est_busy;
    wire        est_phase_a_done;
    wire        est_s0, est_s1, est_s2, est_s3, est_s4, est_s5;
    wire [7:0]  est_m0, est_m1, est_m2, est_m3, est_m4, est_m5;
    wire signed [31:0] est_ref0, est_ref1, est_ref2;

    // ALU
    wire        alu_a_sign, alu_b_sign;
    wire [7:0]  alu_a_mag, alu_b_mag;
    wire [2:0]  alu_op;
    wire        alu_op_valid;
    wire        alu_r_sign;
    wire [7:0]  alu_r_mag;
    wire        alu_r_valid, alu_busy;

    // Memory
    wire [13:0] active_mem_addr;
    wire [15:0] active_mem_wdata;
    wire        active_mem_wen, active_mem_ren;
    wire [15:0] mem_rdata;
    reg         bank_sel;

    // Register file
    wire [3:0]  reg_raddr;
    wire        reg_rsign;
    wire [7:0]  reg_rmag;
    wire [3:0]  active_reg_waddr;
    wire        active_reg_wsign;
    wire [7:0]  active_reg_wmag;
    wire        active_reg_wen;

    // RNG
    wire        rng_advance, rng_sign;
    wire [7:0]  rng_mag;
    wire [31:0] lfsr_raw;
    reg         lfsr_load;

    // TB mux signals
    reg  [13:0] tb_mem_addr;
    reg  [15:0] tb_mem_wdata;
    reg         tb_mem_wen, tb_mem_ren;
    reg  [3:0]  tb_reg_waddr;
    reg         tb_reg_wsign;
    reg  [7:0]  tb_reg_wmag;
    reg         tb_reg_wen;

    // Sequencer wires
    wire [13:0] seq_mem_addr;
    wire [15:0] seq_mem_wdata;
    wire        seq_mem_wen, seq_mem_ren;
    wire [3:0]  seq_reg_raddr, seq_reg_waddr;
    wire        seq_reg_wsign;
    wire [7:0]  seq_reg_wmag;
    wire        seq_reg_wen;
    wire        seq_rng_advance;

    wire        seq_alu_a_sign, seq_alu_b_sign;
    wire [7:0]  seq_alu_a_mag, seq_alu_b_mag;
    wire [2:0]  seq_alu_op;
    wire        seq_alu_op_valid;

    // Position predict wires
    wire        pp_alu_a_sign, pp_alu_b_sign;
    wire [7:0]  pp_alu_a_mag, pp_alu_b_mag;
    wire [2:0]  pp_alu_op;
    wire        pp_alu_op_valid;
    wire [13:0] pp_mem_addr;
    wire [15:0] pp_mem_wdata;
    wire        pp_mem_wen, pp_mem_ren;
    wire [3:0]  pp_reg_raddr;
    wire        pp_rng_advance;

    // Resampler wires
    wire [13:0] rs_mem_addr;
    wire [15:0] rs_mem_wdata;
    wire        rs_mem_wen, rs_mem_ren;
    wire        rs_alu_a_sign, rs_alu_b_sign;
    wire [7:0]  rs_alu_a_mag, rs_alu_b_mag;
    wire [2:0]  rs_alu_op;
    wire        rs_alu_op_valid;

    // Estimator wires
    wire [13:0] est_mem_addr;
    wire [15:0] est_mem_wdata;
    wire        est_mem_wen, est_mem_ren;

    // ALU mux: pos_predict > resampler > sequencer
    wire use_pp  = pp_busy;
    wire use_rs  = rs_busy;
    wire use_est = est_busy;
    assign alu_a_sign   = use_pp ? pp_alu_a_sign   : use_rs ? rs_alu_a_sign   : seq_alu_a_sign;
    assign alu_a_mag    = use_pp ? pp_alu_a_mag     : use_rs ? rs_alu_a_mag     : seq_alu_a_mag;
    assign alu_b_sign   = use_pp ? pp_alu_b_sign   : use_rs ? rs_alu_b_sign   : seq_alu_b_sign;
    assign alu_b_mag    = use_pp ? pp_alu_b_mag     : use_rs ? rs_alu_b_mag     : seq_alu_b_mag;
    assign alu_op       = use_pp ? pp_alu_op        : use_rs ? rs_alu_op        : seq_alu_op;
    assign alu_op_valid = use_pp ? pp_alu_op_valid  : use_rs ? rs_alu_op_valid  : seq_alu_op_valid;

    // Memory mux: pos_predict > estimator > resampler > sequencer > TB
    wire any_busy = pp_busy | seq_busy | rs_busy | est_busy;
    assign active_mem_addr  = use_pp  ? pp_mem_addr
                            : use_est ? est_mem_addr
                            : use_rs  ? rs_mem_addr
                            : seq_busy ? seq_mem_addr  : tb_mem_addr;
    assign active_mem_wdata = use_pp  ? pp_mem_wdata
                            : use_est ? est_mem_wdata
                            : use_rs  ? rs_mem_wdata
                            : seq_busy ? seq_mem_wdata : tb_mem_wdata;
    assign active_mem_wen   = use_pp  ? pp_mem_wen
                            : use_est ? est_mem_wen
                            : use_rs  ? rs_mem_wen
                            : seq_busy ? seq_mem_wen   : tb_mem_wen;
    assign active_mem_ren   = use_pp  ? pp_mem_ren
                            : use_est ? est_mem_ren
                            : use_rs  ? rs_mem_ren
                            : seq_busy ? seq_mem_ren   : tb_mem_ren;

    // Register file mux
    assign reg_raddr = use_pp ? pp_reg_raddr : seq_reg_raddr;
    assign active_reg_waddr = any_busy ? seq_reg_waddr : tb_reg_waddr;
    assign active_reg_wsign = any_busy ? seq_reg_wsign : tb_reg_wsign;
    assign active_reg_wmag  = any_busy ? seq_reg_wmag  : tb_reg_wmag;
    assign active_reg_wen   = any_busy ? seq_reg_wen   : tb_reg_wen;

    assign rng_advance = use_pp ? pp_rng_advance : seq_rng_advance;

    // --- Instantiate modules ---
    lns8_alu u_alu (
        .clk(clk), .rst_n(rst_n),
        .a_sign(alu_a_sign), .a_mag(alu_a_mag),
        .b_sign(alu_b_sign), .b_mag(alu_b_mag),
        .op(alu_op), .op_valid(alu_op_valid),
        .r_sign(alu_r_sign), .r_mag(alu_r_mag),
        .r_valid(alu_r_valid), .busy(alu_busy)
    );

    pf_memory u_mem (
        .clk(clk), .rst_n(rst_n),
        .spram_addr(active_mem_addr), .spram_wdata(active_mem_wdata),
        .spram_wen(active_mem_wen), .spram_ren(active_mem_ren),
        .spram_rdata(mem_rdata),
        .bank_sel(bank_sel),
        .reg_raddr(reg_raddr), .reg_rsign(reg_rsign), .reg_rmag(reg_rmag),
        .reg_waddr(active_reg_waddr), .reg_wsign(active_reg_wsign),
        .reg_wmag(active_reg_wmag), .reg_wen(active_reg_wen)
    );

    pf_sequencer u_seq (
        .clk(clk), .rst_n(rst_n),
        .start(seq_start), .phase(seq_phase),
        .n_particles(N_PARTICLES - 1),
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
        .sensor_dim(tb_sensor_dim)
    );

    pf_pos_predict u_pp (
        .clk(clk), .rst_n(rst_n),
        .start(pp_start), .n_particles(N_PARTICLES - 1),
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

    pf_resampler u_rs (
        .clk(clk), .rst_n(rst_n),
        .start(rs_start), .n_particles(N_PARTICLES - 1),
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

    pf_estimator u_est (
        .clk(clk), .rst_n(rst_n),
        .start(est_start), .resume(est_resume),
        .n_particles(N_PARTICLES - 1),
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

    lfsr_rng u_rng (
        .clk(clk), .rst_n(rst_n),
        .advance(rng_advance),
        .seed(lfsr_seed_val), .seed_load(lfsr_load),
        .noise_sign(rng_sign), .noise_mag(rng_mag),
        .lfsr_raw(lfsr_raw)
    );

    always #5 clk = ~clk;

    // --- Sensor offset conversion ---
    // LNS8 → FP ROM (same as estimator/resampler)
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

    // lin_to_lns8 instance for sensor offset conversion
    reg  [15:0] tb_l2l_in;
    wire        tb_l2l_sign;
    wire [7:0]  tb_l2l_mag;
    lin_to_lns8 u_tb_l2l (
        .fp_in(tb_l2l_in), .lns_sign(tb_l2l_sign), .lns_mag(tb_l2l_mag)
    );

    // LNS8 → signed FP conversion function (matches estimator)
    function signed [15:0] lns8_to_fp;
        input       sign;
        input [7:0] mag;
        reg signed [3:0] int_part;
        reg [3:0] frac_part, shift_r;
        reg [15:0] uval;
        reg signed [16:0] wide;
        begin
            if (mag == `ZERO_LOG_MAG) begin
                lns8_to_fp = 16'sd0;
            end else begin
                int_part = mag[7:4];
                frac_part = mag[3:0];
                if (int_part >= 0)
                    uval = lns_to_lin_rom[frac_part] << int_part;
                else begin
                    shift_r = ~int_part + 4'd1;
                    uval = lns_to_lin_rom[frac_part] >> shift_r;
                end
                if (sign && uval != 16'd0)
                    wide = -$signed({1'b0, uval});
                else
                    wide = $signed({1'b0, uval});
                if (wide > 17'sd32767)
                    lns8_to_fp = 16'sd32767;
                else if (wide < -17'sd32768)
                    lns8_to_fp = -16'sd32768;
                else
                    lns8_to_fp = wide[15:0];
            end
        end
    endfunction

    // --- File I/O ---
    integer fd_init, fd_sensors, fd_sensors_fp, fd_truth, fd_out;
    integer rc, i, d, t, s;
    integer cycle_count, predict_cyc, weight_cyc, resample_cyc, estimate_cyc;
    integer dbg_sum;
    reg signed [15:0] dbg_fp;
    reg do_bank_swap;

    reg [7:0] rd_sign, rd_mag;
    reg [7:0] sensor_signs [0:N_SENSORS-1];
    reg [7:0] sensor_mags  [0:N_SENSORS-1];
    reg [31:0] sensor_fp   [0:N_SENSORS-1];  // 32-bit signed FP (8.8 extended)
    reg [7:0] tss_signs    [0:N_SENSORS-1];
    reg [7:0] tss_mags     [0:N_SENSORS-1];
    reg [2:0] sensor_dims  [0:N_SENSORS-1];
    reg [7:0] truth_signs  [0:N_DIMS-1];
    reg [7:0] truth_mags   [0:N_DIMS-1];

    // TB helpers
    task tb_write_mem(input [13:0] addr, input [15:0] data);
        begin
            tb_mem_addr  = addr;
            tb_mem_wdata = data;
            tb_mem_wen   = 1;
            tb_mem_ren   = 0;
            @(posedge clk); #1;
            tb_mem_wen = 0;
        end
    endtask

    task tb_read_mem(input [13:0] addr);
        begin
            tb_mem_addr = addr;
            tb_mem_wen  = 0;
            tb_mem_ren  = 1;
            @(posedge clk); #1;
            tb_mem_ren = 0;
            @(posedge clk); #1;
        end
    endtask

    task tb_write_reg(input [3:0] addr, input sign, input [7:0] mag);
        begin
            tb_reg_waddr = addr;
            tb_reg_wsign = sign;
            tb_reg_wmag  = mag;
            tb_reg_wen   = 1;
            @(posedge clk); #1;
            tb_reg_wen = 0;
        end
    endtask

    task wait_pp_done;
        begin
            cycle_count = 0;
            while (!pp_done) begin
                @(posedge clk); #1;
                cycle_count = cycle_count + 1;
                if (cycle_count > 5000000) begin
                    $display("TIMEOUT waiting for pos_predict at step %0d", t);
                    $finish;
                end
            end
        end
    endtask

    task wait_seq_done;
        begin
            cycle_count = 0;
            while (!seq_done) begin
                @(posedge clk); #1;
                cycle_count = cycle_count + 1;
                if (cycle_count > 5000000) begin
                    $display("TIMEOUT waiting for sequencer at step %0d", t);
                    $finish;
                end
            end
        end
    endtask

    task wait_rs_done;
        begin
            cycle_count = 0;
            while (!rs_done) begin
                @(posedge clk); #1;
                cycle_count = cycle_count + 1;
                if (cycle_count > 5000000) begin
                    $display("TIMEOUT waiting for resampler at step %0d", t);
                    $finish;
                end
            end
        end
    endtask

    task wait_est_phase_a;
        begin
            cycle_count = 0;
            while (!est_phase_a_done) begin
                @(posedge clk); #1;
                cycle_count = cycle_count + 1;
                if (cycle_count > 5000000) begin
                    $display("TIMEOUT waiting for estimator phase A at step %0d", t);
                    $finish;
                end
            end
        end
    endtask

    task wait_est_done;
        begin
            while (!est_done) begin
                @(posedge clk); #1;
                cycle_count = cycle_count + 1;
                if (cycle_count > 5000000) begin
                    $display("TIMEOUT waiting for estimator at step %0d", t);
                    $finish;
                end
            end
        end
    endtask

    integer fd_seed;

    initial begin
        clk = 0; rst_n = 0;
        pp_start = 0;
        seq_start = 0; seq_phase = 0;
        rs_start = 0; est_start = 0; est_resume = 0;
        bank_sel = 0;
        tb_sensor_dim = 0;
        tb_mem_wen = 0; tb_mem_ren = 0; tb_mem_addr = 0; tb_mem_wdata = 0;
        tb_reg_wen = 0; tb_reg_waddr = 0; tb_reg_wsign = 0; tb_reg_wmag = 0;
        lfsr_load = 0;

        // Load LFSR seed from scenario file (scenario-dependent noise)
        lfsr_seed_val = LFSR_SEED_DEFAULT;
        fd_seed = $fopen("vectors/scenario_lfsr_seed.hex", "r");
        if (fd_seed != 0) begin
            rc = $fscanf(fd_seed, "%h", lfsr_seed_val);
            $fclose(fd_seed);
        end

        #20 rst_n = 1;
        // Pulse seed_load to initialize LFSR with scenario seed
        @(posedge clk); #1;
        lfsr_load = 1;
        @(posedge clk); #1;
        lfsr_load = 0;
        #10;

        // Open files
        fd_init = $fopen("vectors/scenario_init.hex", "r");
        fd_sensors = $fopen("vectors/scenario_sensors.hex", "r");
        fd_sensors_fp = $fopen("vectors/scenario_sensors_fp.hex", "r");
        fd_truth = $fopen("vectors/scenario_truth.hex", "r");
        fd_out = $fopen("build/rtl_trace.txt", "w");

        if (fd_init == 0 || fd_sensors == 0 || fd_truth == 0) begin
            $display("ERROR: cannot open scenario files. Run gen_pf_scenario.py first.");
            $finish;
        end
        if (fd_sensors_fp == 0) begin
            $display("WARNING: scenario_sensors_fp.hex not found — using LNS8 sensors (lossy).");
        end

        $display("========================================");
        $display("  PF 6D E2E Test: %0d particles, %0d sensors, %0d steps, LFSR=0x%08h",
                 N_PARTICLES, N_SENSORS, N_STEPS, lfsr_seed_val);
        $display("  With weighted estimator + delta recentering");
        $display("========================================");

        // --- Load initial particle states (6 dims per particle) ---
        // Position dims (0,1,2): read as hi_byte lo_byte → 16-bit signed FP
        // Velocity dims (3,4,5): read as sign mag → LNS8 {7'b0, sign, mag}
        for (i = 0; i < N_PARTICLES; i = i + 1) begin
            for (d = 0; d < N_DIMS; d = d + 1) begin
                rc = $fscanf(fd_init, "%h %h", rd_sign, rd_mag);
                if (d < 3)
                    // Position: {hi, lo} → 16-bit signed FP
                    tb_write_mem({4'b0, i[6:0], 3'b0} + d[2:0], {rd_sign, rd_mag});
                else
                    // Velocity: LNS8
                    tb_write_mem({4'b0, i[6:0], 3'b0} + d[2:0], {7'b0, rd_sign[0], rd_mag});
            end
            // Initialize weight to zero
            tb_write_mem({4'b0, i[6:0], 3'b110}, {7'b0, 1'b0, `ZERO_LOG_MAG});
        end

        // --- Load constants ---
        // DT (reg 2)
        rc = $fscanf(fd_init, "%h %h", rd_sign, rd_mag);
        tb_write_reg(4'd2, rd_sign[0], rd_mag);

        // NOISE_SCALE_POS (reg 3)
        rc = $fscanf(fd_init, "%h %h", rd_sign, rd_mag);
        tb_write_reg(4'd3, rd_sign[0], rd_mag);

        // NOISE_SCALE_VEL (reg 15)
        rc = $fscanf(fd_init, "%h %h", rd_sign, rd_mag);
        tb_write_reg(4'd15, rd_sign[0], rd_mag);

        // Per-sensor: TWO_SIGMA_SQ + sensor dimension
        for (s = 0; s < N_SENSORS; s = s + 1) begin
            rc = $fscanf(fd_init, "%h %h", tss_signs[s], tss_mags[s]);
            rc = $fscanf(fd_init, "%h %h", rd_sign, rd_mag);
            sensor_dims[s] = rd_mag[2:0];
        end

        $fclose(fd_init);
        $display("  Loaded initial state + constants.");

        // --- Main loop: run N_STEPS timesteps ---
        for (t = 0; t < N_STEPS; t = t + 1) begin
            // Read sensor measurements for this timestep
            for (s = 0; s < N_SENSORS; s = s + 1) begin
                rc = $fscanf(fd_sensors, "%h %h", sensor_signs[s], sensor_mags[s]);
            end
            // Read high-precision FP sensor values (for delta-encoding)
            if (fd_sensors_fp != 0) begin
                for (s = 0; s < N_SENSORS; s = s + 1) begin
                    rc = $fscanf(fd_sensors_fp, "%h", sensor_fp[s]);
                end
            end

            // Read ground truth (6 dimensions)
            for (d = 0; d < N_DIMS; d = d + 1) begin
                rc = $fscanf(fd_truth, "%h %h", truth_signs[d], truth_mags[d]);
            end

            @(posedge clk); #1;

            // --- Position Predict (dims 0,1,2 in FP) ---
`ifdef DEBUG_TRACE
            // Dump particle states BEFORE predict
            $fwrite(fd_out, "DBG_PRE_PREDICT t=%0d\n", t);
            for (i = 0; i < 4; i = i + 1) begin
                $fwrite(fd_out, "  p=%0d", i);
                for (d = 0; d < N_DIMS; d = d + 1) begin
                    tb_read_mem({4'b0, i[6:0], 3'b0} + d[2:0]);
                    if (d < 3)
                        $fwrite(fd_out, " F%0d=%0d", d, $signed(mem_rdata));
                    else
                        $fwrite(fd_out, " L%0d=%0d_%0d", d, mem_rdata[8], mem_rdata[7:0]);
                end
                $fwrite(fd_out, "\n");
            end
`endif
            pp_start = 1;
            @(posedge clk); #1;
            pp_start = 0;
            wait_pp_done;
            predict_cyc = cycle_count;

            // --- Velocity Predict (dims 3,4,5 via sequencer) ---
            seq_phase = 2'd0;
            seq_start = 1;
            @(posedge clk); #1;
            seq_start = 0;
            wait_seq_done;
            predict_cyc = predict_cyc + cycle_count;

`ifdef DEBUG_TRACE
            // Dump particle states AFTER predict (pos + vel)
            $fwrite(fd_out, "DBG_POST_PREDICT t=%0d pos_cyc=%0d vel_cyc=%0d\n",
                    t, predict_cyc - cycle_count, cycle_count);
            for (i = 0; i < 4; i = i + 1) begin
                $fwrite(fd_out, "  p=%0d", i);
                for (d = 0; d < N_DIMS; d = d + 1) begin
                    tb_read_mem({4'b0, i[6:0], 3'b0} + d[2:0]);
                    if (d < 3)
                        $fwrite(fd_out, " F%0d=%0d", d, $signed(mem_rdata));
                    else
                        $fwrite(fd_out, " L%0d=%0d_%0d", d, mem_rdata[8], mem_rdata[7:0]);
                end
                $fwrite(fd_out, "\n");
            end
`endif
            // --- Weight: iterate sensors externally ---
            // For position-dim sensors (after step 0), convert measurement
            // to offset space: z_offset = z_abs - ref_pos[dim]
            weight_cyc = 0;
            for (s = 0; s < N_SENSORS; s = s + 1) begin : weight_sensor_loop
                reg        w_sign;
                reg [7:0]  w_mag;
                reg signed [31:0] z_fp_wide, ref_wide, z_offset_wide;
                reg signed [15:0] z_offset_sat;
                w_sign = sensor_signs[s][0];
                w_mag  = sensor_mags[s];

                // Sensor offset conversion for position dims (t > 0)
                // Delta-encode: z_offset = z_absolute - ref_pos
                // Use 32-bit FP sensor values when available (avoids LNS8 quantization)
                if (t > 0 && sensor_dims[s] < 3'd3) begin : sensor_conv
                    case (sensor_dims[s])
                        3'd0: ref_wide = est_ref0;
                        3'd1: ref_wide = est_ref1;
                        3'd2: ref_wide = est_ref2;
                        default: ref_wide = 32'sd0;
                    endcase
                    if (fd_sensors_fp != 0) begin
                        // High-precision path: 32-bit FP sensor → offset → LNS8
                        z_fp_wide = $signed(sensor_fp[s]);
                    end else begin
                        // Fallback: LNS8 sensor → FP16 (lossy) → offset
                        reg signed [15:0] z_fp_16;
                        z_fp_16 = lns8_to_fp(w_sign, w_mag);
                        z_fp_wide = {{16{z_fp_16[15]}}, z_fp_16};
                    end
                    z_offset_wide = z_fp_wide - ref_wide;
                    z_offset_sat = (z_offset_wide > 32'sd32767) ? 16'sd32767
                                 : (z_offset_wide < -32'sd32768) ? -16'sd32768
                                 : z_offset_wide[15:0];
                    tb_l2l_in = z_offset_sat;
                    #1;
                    w_sign = tb_l2l_sign;
                    w_mag  = tb_l2l_mag;
                end

                // Load sensor measurement (absolute or offset) into SENSOR_Z
                tb_write_reg(4'd4, w_sign, w_mag);
                // Load this sensor's TWO_SIGMA_SQ into reg 5
                tb_write_reg(4'd5, tss_signs[s][0], tss_mags[s]);
                // Set sensor dimension
                tb_sensor_dim = sensor_dims[s];
                @(posedge clk); #1;

                seq_phase = 2'd1;
                seq_start = 1;
                @(posedge clk); #1;
                seq_start = 0;
                wait_seq_done;
                weight_cyc = weight_cyc + cycle_count;
            end

`ifdef DEBUG_TRACE
            // Dump log-weights after all sensors
            $fwrite(fd_out, "DBG_POST_WEIGHT t=%0d\n", t);
            for (i = 0; i < 4; i = i + 1) begin
                tb_read_mem({4'b0, i[6:0], 3'b110}); // weight slot
                $fwrite(fd_out, "  p=%0d lw=%0d_%0d\n", i, mem_rdata[8], mem_rdata[7:0]);
            end
`endif
            // --- Resample ---
            rs_start = 1;
            @(posedge clk); #1;
            rs_start = 0;
            wait_rs_done;
            resample_cyc = cycle_count;
            // Latch bank_swap NOW — it's a 1-cycle pulse concurrent with rs_done
            do_bank_swap = rs_bank_swap;

`ifdef DEBUG_TRACE
            // Dump first 4 particles on SHADOW bank (post-resample)
            // Temporarily read from shadow bank by flipping bank_sel
            bank_sel = ~bank_sel;
            $fwrite(fd_out, "DBG_POST_RESAMPLE t=%0d weight_sum=%0d lfsr_raw=0x%08h\n",
                    t, rs_weight_sum, u_rng.lfsr);
            for (i = 0; i < 4; i = i + 1) begin
                $fwrite(fd_out, "  p=%0d", i);
                for (d = 0; d < N_DIMS; d = d + 1) begin
                    tb_read_mem({4'b0, i[6:0], 3'b0} + d[2:0]);
                    if (d < 3)
                        $fwrite(fd_out, " F%0d=%0d", d, $signed(mem_rdata));
                    else
                        $fwrite(fd_out, " L%0d=%0d_%0d", d, mem_rdata[8], mem_rdata[7:0]);
                end
                $fwrite(fd_out, "\n");
            end
            bank_sel = ~bank_sel; // restore
`endif
            // DON'T bank swap yet — Phase A needs pre-resample particles
            @(posedge clk); #1;

            // --- Estimate Phase A: weighted mean on pre-resample bank ---
            est_start = 1;
            @(posedge clk); #1;
            est_start = 0;
            wait_est_phase_a;

            // Debug: print RB estimate (est_fp from estimator, in 8.8 FP)
            if (t < 5)
                $display("  [DBG] t=%0d Phase A est_fp_x=%0d (ref_x=%0d)",
                         t, $signed(u_est.est_fp_0), $signed(u_est.ref_pos_0));

            // NOW do the bank swap (latched from rs_done, deferred until after Phase A)
            if (do_bank_swap)
                bank_sel = ~bank_sel;
            @(posedge clk); #1;
            // Debug: print all 6 est_fp values and weight_sum
            if (t < 5)
                $display("  [DBG] t=%0d est_fp=[%0d,%0d,%0d,%0d,%0d,%0d] ws=%0d",
                         t, $signed(u_est.est_fp_0), $signed(u_est.est_fp_1),
                         $signed(u_est.est_fp_2), $signed(u_est.est_fp_3),
                         $signed(u_est.est_fp_4), $signed(u_est.est_fp_5),
                         rs_weight_sum);

            // Resume estimator for Phase B (recenter post-resample particles)
            est_resume = 1;
            @(posedge clk); #1;
            est_resume = 0;
            wait_est_done;
            estimate_cyc = cycle_count;

            @(posedge clk); #1;
            @(posedge clk); #1;

`ifdef DEBUG_TRACE
            // Dump post-recenter state
            $fwrite(fd_out, "DBG_POST_RECENTER t=%0d refs=%0d_%0d_%0d recenter=%0d_%0d_%0d\n",
                    t, $signed(est_ref0), $signed(est_ref1), $signed(est_ref2),
                    $signed(u_est.recenter_fp_0), $signed(u_est.recenter_fp_1),
                    $signed(u_est.recenter_fp_2));
            for (i = 0; i < 4; i = i + 1) begin
                $fwrite(fd_out, "  p=%0d", i);
                for (d = 0; d < N_DIMS; d = d + 1) begin
                    tb_read_mem({4'b0, i[6:0], 3'b0} + d[2:0]);
                    if (d < 3)
                        $fwrite(fd_out, " F%0d=%0d", d, $signed(mem_rdata));
                    else
                        $fwrite(fd_out, " L%0d=%0d_%0d", d, mem_rdata[8], mem_rdata[7:0]);
                end
                $fwrite(fd_out, "\n");
            end
`endif
            // Debug: measure residual mean after recentering
            // Position dims are now 16-bit signed FP — read directly
            if (t < 10) begin
                dbg_sum = 0;
                for (i = 0; i < N_PARTICLES; i = i + 1) begin
                    tb_read_mem({4'b0, i[6:0], 3'b0});  // dim 0
                    dbg_fp = $signed(mem_rdata);  // position dim: direct FP
                    dbg_sum = dbg_sum + dbg_fp;
                end
                $display("  [DBG] t=%0d residual_x: sum=%0d mean_fp=%0d (=%0d.%03dm)",
                         t, dbg_sum, dbg_sum/N_PARTICLES,
                         dbg_sum/N_PARTICLES/256,
                         ((dbg_sum < 0 ? -dbg_sum : dbg_sum) / N_PARTICLES * 1000 / 256) % 1000);
            end

            // --- Dump results ---
            // Write header line with cycle counts
            $fwrite(fd_out, "STEP %0d predict=%0d weight=%0d resample=%0d estimate=%0d\n",
                    t, predict_cyc, weight_cyc, resample_cyc, estimate_cyc);

            // Write truth line (6 dims: sign mag pairs)
            $fwrite(fd_out, "TRUTH");
            for (d = 0; d < N_DIMS; d = d + 1) begin
                $fwrite(fd_out, " %0d %0d", truth_signs[d][0], truth_mags[d]);
            end
            $fwrite(fd_out, "\n");

            // Write estimate line (6 dims from estimator, LNS8)
            $fwrite(fd_out, "EST %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d\n",
                    est_s0, est_m0, est_s1, est_m1, est_s2, est_m2,
                    est_s3, est_m3, est_s4, est_m4, est_s5, est_m5);

            // Write reference positions (3 dims, signed 16-bit FP)
            $fwrite(fd_out, "REFS %0d %0d %0d\n",
                    $signed(est_ref0), $signed(est_ref1), $signed(est_ref2));

            // Dump particle states (6 dims per particle)
            for (i = 0; i < N_PARTICLES; i = i + 1) begin
                $fwrite(fd_out, "P %0d", i);
                for (d = 0; d < N_DIMS; d = d + 1) begin
                    tb_read_mem({4'b0, i[6:0], 3'b0} + d[2:0]);
                    if (d < 3)
                        // Position: 16-bit signed FP — emit as signed decimal
                        $fwrite(fd_out, " F %0d", $signed(mem_rdata));
                    else
                        // Velocity: LNS8 — emit as sign mag
                        $fwrite(fd_out, " L %0d %0d", mem_rdata[8], mem_rdata[7:0]);
                end
                $fwrite(fd_out, "\n");
            end

            $display("  Step %0d: predict=%0d weight=%0d resample=%0d estimate=%0d cycles",
                     t, predict_cyc, weight_cyc, resample_cyc, estimate_cyc);
        end

        $fclose(fd_sensors);
        if (fd_sensors_fp != 0) $fclose(fd_sensors_fp);
        $fclose(fd_truth);
        $fclose(fd_out);

        $display("\n========================================");
        $display("  6D E2E test complete. Output: build/rtl_trace.txt");
        $display("========================================");
        #100 $finish;
    end
endmodule
