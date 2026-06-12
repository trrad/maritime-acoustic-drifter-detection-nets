`timescale 1ns/1ps
`include "lns8_pkg.v"

module tb_lns8_muldiv;
    reg        a_sign, b_sign;
    reg  [7:0] a_mag, b_mag;
    reg        is_div;
    wire       r_sign;
    wire [7:0] r_mag;

    lns8_muldiv uut (
        .a_sign(a_sign), .a_mag(a_mag),
        .b_sign(b_sign), .b_mag(b_mag),
        .is_div(is_div),
        .r_sign(r_sign), .r_mag(r_mag)
    );

    integer fd, rc, line_num, errors, total;
    reg [7:0] exp_sign, exp_mag;
    reg [7:0] va_sign, va_mag, vb_sign, vb_mag;

    task test_file(input [8*32-1:0] filename, input div_mode);
        begin
            fd = $fopen(filename, "r");
            if (fd == 0) begin
                $display("ERROR: cannot open %s", filename);
                $finish;
            end
            line_num = 0;
            errors = 0;
            total = 0;
            is_div = div_mode;

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

                a_sign = va_sign[0];
                a_mag  = va_mag;
                b_sign = vb_sign[0];
                b_mag  = vb_mag;

                #1;  // combinational settle

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
        $display("=== MUL test ===");
        test_file("vectors/mul_vectors.hex", 1'b0);
        $display("MUL: %0d/%0d passed (%0d errors)", total - errors, total, errors);

        $display("=== DIV test ===");
        test_file("vectors/div_vectors.hex", 1'b1);
        $display("DIV: %0d/%0d passed (%0d errors)", total - errors, total, errors);

        $finish;
    end
endmodule
