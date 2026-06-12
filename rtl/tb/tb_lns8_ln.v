`timescale 1ns/1ps
`include "lns8_pkg.v"

module tb_lns8_ln;
    reg clk, rst_n;
    reg start;
    reg a_sign;
    reg [7:0] a_mag;
    wire r_sign;
    wire [7:0] r_mag;
    wire done, busy_w;

    lns8_ln uut (
        .clk(clk), .rst_n(rst_n), .start(start),
        .a_sign(a_sign), .a_mag(a_mag),
        .r_sign(r_sign), .r_mag(r_mag),
        .done(done), .busy(busy_w)
    );

    always #5 clk = ~clk;

    integer fd, rc, line_num, errors, total;
    reg [7:0] exp_sign_v, exp_mag_v;
    reg [7:0] va_sign, va_mag;

    initial begin : main_block
        clk = 0; rst_n = 0; start = 0;
        a_sign = 0; a_mag = 0;
        #20 rst_n = 1;
        #10;

        $display("=== LN test (exhaustive) ===");
        fd = $fopen("vectors/ln_vectors.hex", "r");
        if (fd == 0) begin $display("ERROR: cannot open ln_vectors.hex"); $finish; end

        line_num = 0; errors = 0; total = 0;

        while (!$feof(fd)) begin
            rc = $fscanf(fd, "%h %h %h %h\n", va_sign, va_mag, exp_sign_v, exp_mag_v);
            if (rc != 4) begin
                if (!$feof(fd)) $display("WARN: parse error line %0d", line_num);
                disable main_block;
            end
            line_num = line_num + 1;
            total = total + 1;

            @(posedge clk);
            a_sign = va_sign[0];
            a_mag  = va_mag;
            start  = 1'b1;
            @(posedge clk);
            start = 1'b0;

            while (!done) @(posedge clk);

            if (r_sign !== exp_sign_v[0] || r_mag !== exp_mag_v) begin
                errors = errors + 1;
                if (errors <= 20)
                    $display("FAIL line %0d: a=%h_%h exp=%h_%h got=%h_%h",
                             line_num, va_sign, va_mag,
                             exp_sign_v, exp_mag_v, {7'b0, r_sign}, r_mag);
            end
        end
        $fclose(fd);
        $display("LN: %0d/%0d passed (%0d errors)", total - errors, total, errors);

        #100 $finish;
    end
endmodule
