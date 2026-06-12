`timescale 1ns/1ps
`include "lns8_pkg.v"

// Test resampler: load known weights, run resampler, verify linear weights
module tb_pf_resampler;
    reg         clk, rst_n;

    // Resampler control
    reg         rs_start;
    reg  [6:0]  rs_n_particles;
    wire        rs_done, rs_busy;

    // ALU wires
    wire        alu_a_sign, alu_b_sign;
    wire [7:0]  alu_a_mag, alu_b_mag;
    wire [2:0]  alu_op;
    wire        alu_op_valid;
    wire        alu_r_sign;
    wire [7:0]  alu_r_mag;
    wire        alu_r_valid, alu_busy;

    // Memory — shared SPRAM (no register file needed for resampler)
    wire [13:0] rs_mem_addr;
    wire [15:0] rs_mem_wdata;
    wire        rs_mem_wen, rs_mem_ren;

    reg  [13:0] tb_mem_addr;
    reg  [15:0] tb_mem_wdata;
    reg         tb_mem_wen, tb_mem_ren;

    wire [13:0] mem_addr  = rs_busy ? rs_mem_addr  : tb_mem_addr;
    wire [15:0] mem_wdata = rs_busy ? rs_mem_wdata  : tb_mem_wdata;
    wire        mem_wen   = rs_busy ? rs_mem_wen    : tb_mem_wen;
    wire        mem_ren   = rs_busy ? rs_mem_ren    : tb_mem_ren;
    wire [15:0] mem_rdata;

    reg         bank_sel;
    wire        bank_swap;

    // SPRAM (use pf_memory without register file)
    pf_memory u_mem (
        .clk(clk), .rst_n(rst_n),
        .spram_addr(mem_addr), .spram_wdata(mem_wdata),
        .spram_wen(mem_wen), .spram_ren(mem_ren),
        .spram_rdata(mem_rdata),
        .bank_sel(bank_sel),
        .reg_waddr(4'd0), .reg_wsign(1'b0), .reg_wmag(8'h80), .reg_wen(1'b0),
        .reg_raddr(4'd0), .reg_rsign(), .reg_rmag()
    );

    // ALU
    lns8_alu u_alu (
        .clk(clk), .rst_n(rst_n),
        .a_sign(alu_a_sign), .a_mag(alu_a_mag),
        .b_sign(alu_b_sign), .b_mag(alu_b_mag),
        .op(alu_op), .op_valid(alu_op_valid),
        .r_sign(alu_r_sign), .r_mag(alu_r_mag),
        .r_valid(alu_r_valid), .busy(alu_busy)
    );

    // RNG (for resampler uniform draw)
    wire [31:0] lfsr_raw;
    lfsr_rng u_rng (
        .clk(clk), .rst_n(rst_n),
        .advance(1'b1), // always running
        .seed(32'hABCD1234), .seed_load(1'b0),
        .noise_sign(), .noise_mag(),
        .lfsr_raw(lfsr_raw)
    );

    // Resampler
    pf_resampler u_rs (
        .clk(clk), .rst_n(rst_n),
        .start(rs_start), .n_particles(rs_n_particles),
        .done(rs_done), .busy(rs_busy),
        .alu_a_sign(alu_a_sign), .alu_a_mag(alu_a_mag),
        .alu_b_sign(alu_b_sign), .alu_b_mag(alu_b_mag),
        .alu_op(alu_op), .alu_op_valid(alu_op_valid),
        .alu_r_sign(alu_r_sign), .alu_r_mag(alu_r_mag),
        .alu_r_valid(alu_r_valid), .alu_busy(alu_busy),
        .mem_addr(rs_mem_addr), .mem_wdata(rs_mem_wdata),
        .mem_wen(rs_mem_wen), .mem_ren(rs_mem_ren),
        .mem_rdata(mem_rdata),
        .bank_sel(bank_sel), .bank_swap(bank_swap),
        .lfsr_raw(lfsr_raw)
    );

    always #5 clk = ~clk;

    integer i, cycle_count;

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

    initial begin
        clk = 0; rst_n = 0;
        rs_start = 0; rs_n_particles = 0;
        bank_sel = 0;
        tb_mem_wen = 0; tb_mem_ren = 0; tb_mem_addr = 0; tb_mem_wdata = 0;
        #20 rst_n = 1;
        #10;

        $display("========================================");
        $display("  PF Resampler — Test");
        $display("========================================");

        // RTL sign convention: 0=positive, 1=negative
        // Initialize 4 particles with known states and weights
        // Particle 0: state=+1.0 (sign=0, mag=0x00), weight=-0.25 (sign=1, mag=0xE0)
        tb_write_mem(14'd0, {7'b0, 1'b0, 8'h00}); // state: +1.0
        tb_write_mem(14'd1, {7'b0, 1'b1, 8'hE0}); // weight: -0.25 (best)

        // Particle 1: state=+2.0, weight=-0.5
        tb_write_mem(14'd2, {7'b0, 1'b0, 8'h10}); // state: +2.0
        tb_write_mem(14'd3, {7'b0, 1'b1, 8'hF0}); // weight: -0.5

        // Particle 2: state=+4.0, weight=-2.0 (worst)
        tb_write_mem(14'd4, {7'b0, 1'b0, 8'h20}); // state: +4.0
        tb_write_mem(14'd5, {7'b0, 1'b1, 8'h10}); // weight: -2.0

        // Particle 3: state=+0.5, weight=-0.707
        tb_write_mem(14'd6, {7'b0, 1'b0, 8'hF0}); // state: +0.5
        tb_write_mem(14'd7, {7'b0, 1'b1, 8'hF8}); // weight: -0.707

        @(posedge clk); #1;

        $display("\n--- Initial weights ---");
        for (i = 0; i < 4; i = i + 1) begin
            tb_read_mem({10'b0, i[2:0], 1'b1});
            $display("  Particle %0d weight: sign=%b mag=%h (%0d)",
                     i, mem_rdata[8], mem_rdata[7:0], $signed(mem_rdata[7:0]));
        end

        // Run resampler
        $display("\n--- Running resampler ---");
        rs_n_particles = 7'd3;
        rs_start = 1;
        @(posedge clk); #1;
        rs_start = 0;

        cycle_count = 0;
        while (!rs_done) begin
            @(posedge clk); #1;
            cycle_count = cycle_count + 1;
            if (cycle_count > 10000) begin
                $display("TIMEOUT");
                $finish;
            end
        end
        $display("  Resampler completed in %0d cycles", cycle_count);

        // Toggle bank after swap
        if (bank_swap)
            bank_sel = ~bank_sel;

        @(posedge clk); #1;
        @(posedge clk); #1;

        // Read linear weights (addresses 1024..1027)
        $display("\n--- Linear weights (post-exp) ---");
        for (i = 0; i < 4; i = i + 1) begin
            tb_read_mem(14'd1024 + i[13:0]);
            $display("  Particle %0d linear: sign=%b mag=%h (%0d)",
                     i, mem_rdata[8], mem_rdata[7:0], $signed(mem_rdata[7:0]));
        end

        // Read resampled particles from new active bank
        $display("\n--- Resampled particles (new bank) ---");
        for (i = 0; i < 4; i = i + 1) begin
            tb_read_mem({10'b0, i[2:0], 1'b0});
            $display("  Particle %0d state: sign=%b mag=%h (%0d)",
                     i, mem_rdata[8], mem_rdata[7:0], $signed(mem_rdata[7:0]));
        end

        $display("\n========================================");
        $display("  Resampler test complete");
        $display("========================================");
        #100 $finish;
    end
endmodule
