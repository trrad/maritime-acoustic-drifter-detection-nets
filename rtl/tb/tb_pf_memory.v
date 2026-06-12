`timescale 1ns/1ps
`include "lns8_pkg.v"

module tb_pf_memory;
    reg         clk, rst_n;
    reg  [13:0] spram_addr;
    reg  [15:0] spram_wdata;
    reg         spram_wen, spram_ren;
    wire [15:0] spram_rdata;
    reg         bank_sel;
    reg  [3:0]  reg_waddr, reg_raddr;
    reg         reg_wsign;
    reg  [7:0]  reg_wmag;
    reg         reg_wen;
    wire        reg_rsign;
    wire [7:0]  reg_rmag;

    pf_memory uut (
        .clk(clk), .rst_n(rst_n),
        .spram_addr(spram_addr), .spram_wdata(spram_wdata),
        .spram_wen(spram_wen), .spram_ren(spram_ren),
        .spram_rdata(spram_rdata),
        .bank_sel(bank_sel),
        .reg_waddr(reg_waddr), .reg_wsign(reg_wsign),
        .reg_wmag(reg_wmag), .reg_wen(reg_wen),
        .reg_raddr(reg_raddr), .reg_rsign(reg_rsign),
        .reg_rmag(reg_rmag)
    );

    always #5 clk = ~clk;

    integer errors;

    // Drive signals before the clock edge (setup time) to avoid races
    task write_spram(input [13:0] addr, input [15:0] data);
        begin
            spram_addr  = addr;
            spram_wdata = data;
            spram_wen   = 1;
            spram_ren   = 0;
            @(posedge clk); // write captured here
            #1;
            spram_wen   = 0;
        end
    endtask

    task read_spram(input [13:0] addr);
        begin
            spram_addr = addr;
            spram_wen  = 0;
            spram_ren  = 1;
            @(posedge clk); // read issued
            #1;
            spram_ren  = 0;
            @(posedge clk); // rdata valid (NBA from prev cycle)
            #1;
        end
    endtask

    task write_reg(input [3:0] addr, input sign, input [7:0] mag);
        begin
            @(posedge clk);
            reg_waddr = addr;
            reg_wsign = sign;
            reg_wmag  = mag;
            reg_wen   = 1;
            @(posedge clk);
            reg_wen   = 0;
        end
    endtask

    initial begin
        clk = 0; rst_n = 0;
        spram_addr = 0; spram_wdata = 0; spram_wen = 0; spram_ren = 0;
        bank_sel = 0;
        reg_waddr = 0; reg_wsign = 0; reg_wmag = 0; reg_wen = 0;
        reg_raddr = 0;
        errors = 0;
        #20 rst_n = 1;
        #10;

        $display("========================================");
        $display("  PF Memory — Test");
        $display("========================================");

        // --- Test 1: Basic SPRAM write/read ---
        $display("\n--- SPRAM basic write/read ---");
        write_spram(14'd0, 16'h0123);
        write_spram(14'd1, 16'h4567);
        write_spram(14'd2, 16'h89AB);

        read_spram(14'd0);
        if (spram_rdata !== 16'h0123) begin
            $display("FAIL: addr 0 got %h expected 0123", spram_rdata);
            errors = errors + 1;
        end else
            $display("PASS: addr 0 = %h", spram_rdata);

        read_spram(14'd1);
        if (spram_rdata !== 16'h4567) begin
            $display("FAIL: addr 1 got %h expected 4567", spram_rdata);
            errors = errors + 1;
        end else
            $display("PASS: addr 1 = %h", spram_rdata);

        // --- Test 2: Bank swapping ---
        $display("\n--- Bank swap test ---");
        // Write to bank A (bank_sel=0), addr 0
        bank_sel = 0;
        write_spram(14'd0, 16'hAAAA);

        // Write to bank B (bank_sel=1), addr 0 — maps to physical addr 512
        bank_sel = 1;
        write_spram(14'd0, 16'hBBBB);

        // Read back bank A
        bank_sel = 0;
        read_spram(14'd0);
        if (spram_rdata !== 16'hAAAA) begin
            $display("FAIL: bank A addr 0 got %h expected AAAA", spram_rdata);
            errors = errors + 1;
        end else
            $display("PASS: bank A addr 0 = %h", spram_rdata);

        // Read back bank B
        bank_sel = 1;
        read_spram(14'd0);
        if (spram_rdata !== 16'hBBBB) begin
            $display("FAIL: bank B addr 0 got %h expected BBBB", spram_rdata);
            errors = errors + 1;
        end else
            $display("PASS: bank B addr 0 = %h", spram_rdata);

        // --- Test 3: Particle layout (2 words per particle) ---
        $display("\n--- Particle layout test ---");
        bank_sel = 0;
        // Particle 0: state sign=1, mag=0x10; weight sign=0, mag=0xF0
        write_spram(14'd0, {7'b0, 1'b1, 8'h10}); // word 0: state
        write_spram(14'd1, {7'b0, 1'b0, 8'hF0}); // word 1: weight

        // Particle 1: addr 2,3
        write_spram(14'd2, {7'b0, 1'b0, 8'h20}); // state
        write_spram(14'd3, {7'b0, 1'b1, 8'hE0}); // weight

        // Read particle 0
        read_spram(14'd0);
        if (spram_rdata !== {7'b0, 1'b1, 8'h10}) begin
            $display("FAIL: particle 0 state got %h", spram_rdata);
            errors = errors + 1;
        end else
            $display("PASS: particle 0 state = %h", spram_rdata);

        read_spram(14'd1);
        if (spram_rdata !== {7'b0, 1'b0, 8'hF0}) begin
            $display("FAIL: particle 0 weight got %h", spram_rdata);
            errors = errors + 1;
        end else
            $display("PASS: particle 0 weight = %h", spram_rdata);

        // --- Test 4: Weight array (no bank swapping) ---
        $display("\n--- Weight array (no bank swap) ---");
        bank_sel = 0;
        write_spram(14'd1024, 16'h1111);
        bank_sel = 1;
        read_spram(14'd1024);
        if (spram_rdata !== 16'h1111) begin
            $display("FAIL: weight addr 1024 got %h expected 1111", spram_rdata);
            errors = errors + 1;
        end else
            $display("PASS: weight array addr 1024 unaffected by bank_sel");

        // --- Test 5: Register file ---
        $display("\n--- Register file ---");
        // Write VELOCITY (reg 2)
        write_reg(4'd2, 1'b1, 8'h08);
        // Write NOISE_SCALE (reg 3)
        write_reg(4'd3, 1'b1, 8'h04);
        // Write TEMP0 (reg 6)
        write_reg(4'd6, 1'b0, 8'hF0);

        // Read VELOCITY
        reg_raddr = 4'd2;
        #1;
        if (reg_rsign !== 1'b1 || reg_rmag !== 8'h08) begin
            $display("FAIL: VELOCITY got sign=%b mag=%h", reg_rsign, reg_rmag);
            errors = errors + 1;
        end else
            $display("PASS: VELOCITY = sign=%b, mag=%h", reg_rsign, reg_rmag);

        // Read NOISE_SCALE
        reg_raddr = 4'd3;
        #1;
        if (reg_rsign !== 1'b1 || reg_rmag !== 8'h04) begin
            $display("FAIL: NOISE_SCALE got sign=%b mag=%h", reg_rsign, reg_rmag);
            errors = errors + 1;
        end else
            $display("PASS: NOISE_SCALE = sign=%b, mag=%h", reg_rsign, reg_rmag);

        // Read TEMP0
        reg_raddr = 4'd6;
        #1;
        if (reg_rsign !== 1'b0 || reg_rmag !== 8'hF0) begin
            $display("FAIL: TEMP0 got sign=%b mag=%h", reg_rsign, reg_rmag);
            errors = errors + 1;
        end else
            $display("PASS: TEMP0 = sign=%b, mag=%h", reg_rsign, reg_rmag);

        // --- Test 6: Register file reset ---
        $display("\n--- Register file reset ---");
        rst_n = 0;
        #20;
        rst_n = 1;
        #10;
        reg_raddr = 4'd2;
        #1;
        if (reg_rmag !== `ZERO_LOG_MAG) begin
            $display("FAIL: After reset, reg 2 mag=%h expected %h", reg_rmag, `ZERO_LOG_MAG);
            errors = errors + 1;
        end else
            $display("PASS: After reset, registers cleared");

        // --- Summary ---
        $display("\n========================================");
        if (errors == 0)
            $display("  ALL TESTS PASSED");
        else
            $display("  %0d ERRORS", errors);
        $display("========================================");
        #100 $finish;
    end
endmodule
