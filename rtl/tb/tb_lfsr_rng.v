`timescale 1ns/1ps
`include "lns8_pkg.v"

module tb_lfsr_rng;
    reg         clk, rst_n;
    reg         advance;
    reg  [31:0] seed;
    reg         seed_load;
    wire        noise_sign;
    wire [7:0]  noise_mag;
    wire [31:0] lfsr_raw;

    lfsr_rng uut (
        .clk(clk), .rst_n(rst_n),
        .advance(advance),
        .seed(seed), .seed_load(seed_load),
        .noise_sign(noise_sign),
        .noise_mag(noise_mag),
        .lfsr_raw(lfsr_raw)
    );

    always #5 clk = ~clk;

    integer i;
    integer pos_count, neg_count;
    integer mag_hist [0:15];
    reg [31:0] first_state;

    initial begin
        clk = 0; rst_n = 0; advance = 0; seed = 0; seed_load = 0;
        #20 rst_n = 1;
        #10;

        $display("========================================");
        $display("  LFSR RNG — Test");
        $display("========================================");

        // Test 1: Default seed produces non-zero output
        @(posedge clk);
        advance = 1;
        @(posedge clk);
        advance = 0;
        @(posedge clk);
        $display("Default seed: lfsr=%h, sign=%b, mag=%0d (%h)",
                 lfsr_raw, noise_sign, $signed({1'b0, noise_mag}), noise_mag);
        if (lfsr_raw == 32'h0)
            $display("FAIL: LFSR stuck at zero");
        else
            $display("PASS: LFSR non-zero after advance");

        // Test 2: Seed loading
        @(posedge clk);
        seed = 32'hCAFE_BABE;
        seed_load = 1;
        @(posedge clk);
        seed_load = 0;
        @(posedge clk);
        if (lfsr_raw == 32'hCAFE_BABE)
            $display("PASS: Seed loaded correctly");
        else
            $display("FAIL: Seed load — got %h expected CAFEBABE", lfsr_raw);

        // Test 3: Zero seed prevention
        seed = 32'h0;
        seed_load = 1;
        @(posedge clk);
        seed_load = 0;
        @(posedge clk);
        if (lfsr_raw != 32'h0)
            $display("PASS: Zero seed prevented (got %h)", lfsr_raw);
        else
            $display("FAIL: Zero seed not caught");

        // Test 4: Generate 10000 samples, check sign balance and mag distribution
        seed = 32'h12345678;
        seed_load = 1;
        @(posedge clk);
        seed_load = 0;
        @(posedge clk);

        pos_count = 0;
        neg_count = 0;
        for (i = 0; i < 16; i = i + 1)
            mag_hist[i] = 0;

        for (i = 0; i < 10000; i = i + 1) begin
            advance = 1;
            @(posedge clk);
            advance = 0;
            @(posedge clk);

            if (noise_sign)
                pos_count = pos_count + 1;
            else
                neg_count = neg_count + 1;

            // Track which ROM entry was selected (lfsr[3:0])
            mag_hist[lfsr_raw[3:0]] = mag_hist[lfsr_raw[3:0]] + 1;
        end

        $display("\nSign distribution (10000 samples):");
        $display("  Positive: %0d (%0d%%)", pos_count, pos_count * 100 / 10000);
        $display("  Negative: %0d (%0d%%)", neg_count, neg_count * 100 / 10000);

        if (pos_count > 4000 && pos_count < 6000)
            $display("PASS: Sign roughly balanced");
        else
            $display("FAIL: Sign imbalanced");

        $display("\nMagnitude ROM bin histogram:");
        for (i = 0; i < 16; i = i + 1) begin
            $display("  bin[%2d]: %4d", i, mag_hist[i]);
        end

        // Test 5: LFSR doesn't repeat in 100 cycles
        seed = 32'hAAAA_5555;
        seed_load = 1;
        @(posedge clk);
        seed_load = 0;
        @(posedge clk);
        first_state = lfsr_raw;

        for (i = 0; i < 100; i = i + 1) begin
            advance = 1;
            @(posedge clk);
            advance = 0;
            @(posedge clk);
            if (lfsr_raw == first_state && i > 0) begin
                $display("FAIL: LFSR repeated at cycle %0d", i);
                i = 100; // break
            end
        end
        if (lfsr_raw != first_state)
            $display("PASS: No LFSR repeat in 100 cycles");

        $display("========================================");
        #100 $finish;
    end
endmodule
