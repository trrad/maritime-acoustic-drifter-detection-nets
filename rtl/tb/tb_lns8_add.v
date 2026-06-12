`timescale 1ns/1ps
`include "lns8_pkg.v"

module tb_lns8_add;
    reg clk, rst_n;
    reg start;
    reg a_sign, b_sign;
    reg [7:0] a_mag, b_mag;
    wire r_sign;
    wire [7:0] r_mag;
    wire done, busy_w;

    lns8_add uut (
        .clk(clk), .rst_n(rst_n), .start(start),
        .a_sign(a_sign), .a_mag(a_mag),
        .b_sign(b_sign), .b_mag(b_mag),
        .r_sign(r_sign), .r_mag(r_mag),
        .done(done), .busy(busy_w)
    );

    always #5 clk = ~clk;

    integer fd, rc, line_num, errors, total;
    reg [7:0] exp_sign, exp_mag;
    reg [7:0] va_sign, va_mag, vb_sign, vb_mag;

    task test_file(input [8*32-1:0] filename);
        begin
            fd = $fopen(filename, "r");
            if (fd == 0) begin
                $display("ERROR: cannot open %s", filename);
                $finish;
            end
            line_num = 0;
            errors = 0;
            total = 0;

            while (!$feof(fd)) begin
                rc = $fscanf(fd, "%h %h %h %h %h %h\n",
                             va_sign, va_mag, vb_sign, vb_mag, exp_sign, exp_mag);
                if (rc != 6) begin
                    if (!$feof(fd))
                        $display("WARN: parse error line %0d", line_num);
                    disable test_file;
                end
                line_num = line_num + 1;
                total = total + 1;

                // Apply inputs
                @(posedge clk);
                a_sign = va_sign[0];
                a_mag  = va_mag;
                b_sign = vb_sign[0];
                b_mag  = vb_mag;
                start  = 1'b1;
                @(posedge clk);
                start = 1'b0;

                // Wait for done
                while (!done) @(posedge clk);

                if (r_sign !== exp_sign[0] || r_mag !== exp_mag) begin
                    errors = errors + 1;
                    if (errors <= 20)
                        $display("FAIL line %0d: a=%h_%h b=%h_%h exp=%h_%h got=%h_%h",
                                 line_num, va_sign, va_mag, vb_sign, vb_mag,
                                 exp_sign, exp_mag, {7'b0, r_sign}, r_mag);
                end
            end
            $fclose(fd);
        end
    endtask

    initial begin
        clk = 0; rst_n = 0; start = 0;
        a_sign = 0; a_mag = 0; b_sign = 0; b_mag = 0;
        #20 rst_n = 1;
        #10;

        $display("=== ADD test ===");
        test_file("vectors/add_vectors.hex");
        $display("ADD: %0d/%0d passed (%0d errors)", total - errors, total, errors);

        #100 $finish;
    end
endmodule
