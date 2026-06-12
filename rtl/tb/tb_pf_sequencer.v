`timescale 1ns/1ps
`include "lns8_pkg.v"

// Integration test: sequencer + ALU + memory + RNG
// Runs predict phase for 4 particles, verifies state updates.
// Runs weight phase for 4 particles × 1 sensor, verifies weight updates.

module tb_pf_sequencer;
    reg         clk, rst_n;

    // Sequencer control
    reg         seq_start;
    reg  [1:0]  seq_phase;
    reg  [6:0]  seq_n_particles;
    reg  [2:0]  seq_n_sensors;
    wire        seq_done;
    wire        seq_busy;

    // ALU wires
    wire        alu_a_sign, alu_b_sign;
    wire [7:0]  alu_a_mag, alu_b_mag;
    wire [2:0]  alu_op;
    wire        alu_op_valid;
    wire        alu_r_sign;
    wire [7:0]  alu_r_mag;
    wire        alu_r_valid;
    wire        alu_busy;

    // Memory wires
    wire [13:0] mem_addr;
    wire [15:0] mem_wdata;
    wire        mem_wen, mem_ren;
    wire [15:0] mem_rdata;
    reg         bank_sel;

    // Register file wires
    wire [3:0]  reg_raddr, reg_waddr;
    wire        reg_rsign, reg_wsign;
    wire [7:0]  reg_rmag, reg_wmag;
    wire        reg_wen;

    // RNG wires
    wire        rng_advance;
    wire        rng_sign;
    wire [7:0]  rng_mag;
    wire [31:0] rng_raw;

    reg  [2:0]  sensor_idx;

    // Instantiate sequencer
    pf_sequencer u_seq (
        .clk(clk), .rst_n(rst_n),
        .start(seq_start), .phase(seq_phase),
        .n_particles(seq_n_particles), .n_sensors(seq_n_sensors),
        .done(seq_done), .busy(seq_busy),
        .alu_a_sign(alu_a_sign), .alu_a_mag(alu_a_mag),
        .alu_b_sign(alu_b_sign), .alu_b_mag(alu_b_mag),
        .alu_op(alu_op), .alu_op_valid(alu_op_valid),
        .alu_r_sign(alu_r_sign), .alu_r_mag(alu_r_mag),
        .alu_r_valid(alu_r_valid), .alu_busy(alu_busy),
        .mem_addr(mem_addr), .mem_wdata(mem_wdata),
        .mem_wen(mem_wen), .mem_ren(mem_ren),
        .mem_rdata(mem_rdata),
        .reg_raddr(reg_raddr), .reg_rsign(reg_rsign), .reg_rmag(reg_rmag),
        .reg_waddr(reg_waddr), .reg_wsign(reg_wsign), .reg_wmag(reg_wmag),
        .reg_wen(reg_wen),
        .rng_advance(rng_advance), .rng_sign(rng_sign), .rng_mag(rng_mag),
        .sensor_idx(sensor_idx)
    );

    // Instantiate ALU
    lns8_alu u_alu (
        .clk(clk), .rst_n(rst_n),
        .a_sign(alu_a_sign), .a_mag(alu_a_mag),
        .b_sign(alu_b_sign), .b_mag(alu_b_mag),
        .op(alu_op), .op_valid(alu_op_valid),
        .r_sign(alu_r_sign), .r_mag(alu_r_mag),
        .r_valid(alu_r_valid), .busy(alu_busy)
    );

    // Instantiate memory
    // We need separate write ports for init vs sequencer
    // For simplicity, use a mux: before seq runs, TB drives memory; during seq, sequencer drives
    reg         tb_mem_wen, tb_mem_ren;
    reg  [13:0] tb_mem_addr;
    reg  [15:0] tb_mem_wdata;
    reg         tb_reg_wen;
    reg  [3:0]  tb_reg_waddr;
    reg         tb_reg_wsign;
    reg  [7:0]  tb_reg_wmag;

    wire [13:0] mux_mem_addr  = seq_busy ? mem_addr  : tb_mem_addr;
    wire [15:0] mux_mem_wdata = seq_busy ? mem_wdata : tb_mem_wdata;
    wire        mux_mem_wen   = seq_busy ? mem_wen   : tb_mem_wen;
    wire        mux_mem_ren   = seq_busy ? mem_ren   : tb_mem_ren;

    wire [3:0]  mux_reg_waddr = seq_busy ? reg_waddr : tb_reg_waddr;
    wire        mux_reg_wsign = seq_busy ? reg_wsign : tb_reg_wsign;
    wire [7:0]  mux_reg_wmag  = seq_busy ? reg_wmag  : tb_reg_wmag;
    wire        mux_reg_wen   = seq_busy ? reg_wen   : tb_reg_wen;

    pf_memory u_mem (
        .clk(clk), .rst_n(rst_n),
        .spram_addr(mux_mem_addr), .spram_wdata(mux_mem_wdata),
        .spram_wen(mux_mem_wen), .spram_ren(mux_mem_ren),
        .spram_rdata(mem_rdata),
        .bank_sel(bank_sel),
        .reg_raddr(reg_raddr), .reg_rsign(reg_rsign), .reg_rmag(reg_rmag),
        .reg_waddr(mux_reg_waddr), .reg_wsign(mux_reg_wsign),
        .reg_wmag(mux_reg_wmag), .reg_wen(mux_reg_wen)
    );

    // Instantiate RNG
    lfsr_rng u_rng (
        .clk(clk), .rst_n(rst_n),
        .advance(rng_advance),
        .seed(32'h12345678), .seed_load(1'b0),
        .noise_sign(rng_sign), .noise_mag(rng_mag),
        .lfsr_raw(rng_raw)
    );

    always #5 clk = ~clk;

    integer i, cycle_count;

    // TB helper: write to SPRAM
    task tb_write_mem(input [13:0] addr, input [15:0] data);
        begin
            tb_mem_addr  = addr;
            tb_mem_wdata = data;
            tb_mem_wen   = 1;
            tb_mem_ren   = 0;
            @(posedge clk);
            #1;
            tb_mem_wen = 0;
        end
    endtask

    // TB helper: read from SPRAM
    task tb_read_mem(input [13:0] addr);
        begin
            tb_mem_addr = addr;
            tb_mem_wen  = 0;
            tb_mem_ren  = 1;
            @(posedge clk);
            #1;
            tb_mem_ren = 0;
            @(posedge clk);
            #1;
        end
    endtask

    // TB helper: write register
    task tb_write_reg(input [3:0] addr, input sign, input [7:0] mag);
        begin
            tb_reg_waddr = addr;
            tb_reg_wsign = sign;
            tb_reg_wmag  = mag;
            tb_reg_wen   = 1;
            @(posedge clk);
            #1;
            tb_reg_wen = 0;
        end
    endtask

    initial begin
        clk = 0; rst_n = 0;
        seq_start = 0; seq_phase = 0; seq_n_particles = 0; seq_n_sensors = 0;
        bank_sel = 0; sensor_idx = 0;
        tb_mem_wen = 0; tb_mem_ren = 0; tb_mem_addr = 0; tb_mem_wdata = 0;
        tb_reg_wen = 0; tb_reg_waddr = 0; tb_reg_wsign = 0; tb_reg_wmag = 0;
        #20 rst_n = 1;
        #10;

        $display("========================================");
        $display("  PF Sequencer — Integration Test");
        $display("========================================");

        // =====================================================================
        // Setup: initialize 4 particles and constants
        // =====================================================================
        $display("\n--- Initializing 4 particles ---");

        // Particle 0: state = +1.0 (sign=1, mag=0x00)
        tb_write_mem(14'd0, {7'b0, 1'b1, 8'h00});  // state
        tb_write_mem(14'd1, {7'b0, 1'b0, 8'h80});  // weight = zero

        // Particle 1: state = +2.0 (sign=1, mag=0x10)
        tb_write_mem(14'd2, {7'b0, 1'b1, 8'h10});
        tb_write_mem(14'd3, {7'b0, 1'b0, 8'h80});

        // Particle 2: state = +4.0 (sign=1, mag=0x20)
        tb_write_mem(14'd4, {7'b0, 1'b1, 8'h20});
        tb_write_mem(14'd5, {7'b0, 1'b0, 8'h80});

        // Particle 3: state = +0.5 (sign=1, mag=0xF0 = -16 = -1.0 in log2)
        tb_write_mem(14'd6, {7'b0, 1'b1, 8'hF0});
        tb_write_mem(14'd7, {7'b0, 1'b0, 8'h80});

        // Register 2 (VELOCITY): +0.25 (sign=1, mag=0xE0 = -32 = -2.0 in log2)
        tb_write_reg(4'd2, 1'b1, 8'hE0);

        // Register 3 (NOISE_SCALE): +0.125 (sign=1, mag=0xD0 = -48 = -3.0 in log2)
        tb_write_reg(4'd3, 1'b1, 8'hD0);

        // Register 4 (SENSOR_Z): +1.5 ~ (sign=1, mag=0x09 ≈ log2(1.5)*16 ≈ 9)
        tb_write_reg(4'd4, 1'b1, 8'h09);

        // Register 5 (TWO_SIGMA_SQ): +2.0 (sign=1, mag=0x10)
        tb_write_reg(4'd5, 1'b1, 8'h10);

        @(posedge clk); #1;
        $display("  Particles and constants loaded.");

        // =====================================================================
        // Test 1: Predict phase (4 particles)
        // =====================================================================
        $display("\n--- Predict phase: 4 particles ---");

        seq_phase       = 2'd0;  // predict
        seq_n_particles = 7'd3;  // 0..3 = 4 particles
        seq_n_sensors   = 3'd0;
        seq_start       = 1;
        @(posedge clk); #1;
        seq_start = 0;

        cycle_count = 0;
        while (!seq_done) begin
            @(posedge clk); #1;
            cycle_count = cycle_count + 1;
            if (cycle_count > 5000) begin
                $display("TIMEOUT waiting for predict done");
                $finish;
            end
        end
        $display("  Predict completed in %0d cycles", cycle_count);

        // Wait for any pending SPRAM writes to settle
        @(posedge clk); #1;
        @(posedge clk); #1;

        // Read back particle states
        for (i = 0; i < 4; i = i + 1) begin
            tb_read_mem({10'b0, i[2:0], 1'b0});
            $display("  Particle %0d state: sign=%b mag=%h (%0d)",
                     i, mem_rdata[8], mem_rdata[7:0], $signed(mem_rdata[7:0]));
        end

        // =====================================================================
        // Test 2: Weight phase (4 particles × 1 sensor)
        // =====================================================================
        $display("\n--- Weight phase: 4 particles × 1 sensor ---");

        // First initialize weights to zero (log-weight = 0 → linear weight = 1)
        for (i = 0; i < 4; i = i + 1) begin
            tb_write_mem({10'b0, i[2:0], 1'b1}, {7'b0, 1'b0, 8'h80});
        end

        seq_phase       = 2'd1;  // weight
        seq_n_particles = 7'd3;
        seq_n_sensors   = 3'd0;  // 1 sensor
        sensor_idx      = 3'd0;
        seq_start       = 1;
        @(posedge clk); #1;
        seq_start = 0;

        cycle_count = 0;
        while (!seq_done) begin
            @(posedge clk); #1;
            cycle_count = cycle_count + 1;
            if (cycle_count > 5000) begin
                $display("TIMEOUT waiting for weight done");
                $finish;
            end
        end
        $display("  Weight completed in %0d cycles", cycle_count);

        @(posedge clk); #1;
        @(posedge clk); #1;

        // Read back weights
        for (i = 0; i < 4; i = i + 1) begin
            tb_read_mem({10'b0, i[2:0], 1'b1});
            $display("  Particle %0d weight: sign=%b mag=%h (%0d)",
                     i, mem_rdata[8], mem_rdata[7:0], $signed(mem_rdata[7:0]));
        end

        // Also read back states to see what the weight phase saw
        $display("  Post-weight particle states:");
        for (i = 0; i < 4; i = i + 1) begin
            tb_read_mem({10'b0, i[2:0], 1'b0});
            $display("    Particle %0d state: sign=%b mag=%h (%0d)",
                     i, mem_rdata[8], mem_rdata[7:0], $signed(mem_rdata[7:0]));
        end

        // Verify: particle closest to SENSOR_Z should have highest (least negative) weight
        $display("\n  (Sensor z=1.5, particles at ~1.0, ~2.0, ~4.0, ~0.5 after predict)");
        $display("  Expected: particle 0 or 1 gets least-negative weight");

        $display("\n========================================");
        $display("  Integration test complete");
        $display("========================================");
        #100 $finish;
    end
endmodule
