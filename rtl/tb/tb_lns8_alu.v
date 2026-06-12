`timescale 1ns/1ps
`include "lns8_pkg.v"

module tb_lns8_alu;
    reg clk, rst_n;
    reg a_sign, b_sign;
    reg [7:0] a_mag, b_mag;
    reg [2:0] op;
    reg op_valid;
    wire r_sign;
    wire [7:0] r_mag;
    wire r_valid, busy_w;

    lns8_alu uut (
        .clk(clk), .rst_n(rst_n),
        .a_sign(a_sign), .a_mag(a_mag),
        .b_sign(b_sign), .b_mag(b_mag),
        .op(op), .op_valid(op_valid),
        .r_sign(r_sign), .r_mag(r_mag),
        .r_valid(r_valid), .busy(busy_w)
    );

    always #5 clk = ~clk;

    integer cycle_count;
    integer fd, rc, line_num, errors, total;
    reg [7:0] va_sign, va_mag, vb_sign, vb_mag, exp_sign, exp_mag;

    task issue_binary_op(
        input [2:0] op_code,
        input       in_a_sign,
        input [7:0] in_a_mag,
        input       in_b_sign,
        input [7:0] in_b_mag
    );
        begin
            @(posedge clk);
            a_sign   = in_a_sign;
            a_mag    = in_a_mag;
            b_sign   = in_b_sign;
            b_mag    = in_b_mag;
            op       = op_code;
            op_valid = 1'b1;
            @(posedge clk);
            op_valid = 1'b0;
            cycle_count = 1;
            while (!r_valid) begin
                @(posedge clk);
                cycle_count = cycle_count + 1;
            end
        end
    endtask

    task issue_unary_op(
        input [2:0] op_code,
        input       in_a_sign,
        input [7:0] in_a_mag
    );
        begin
            @(posedge clk);
            a_sign   = in_a_sign;
            a_mag    = in_a_mag;
            b_sign   = 1'b0;
            b_mag    = 8'h80;
            op       = op_code;
            op_valid = 1'b1;
            @(posedge clk);
            op_valid = 1'b0;
            cycle_count = 1;
            while (!r_valid) begin
                @(posedge clk);
                cycle_count = cycle_count + 1;
            end
        end
    endtask

    task test_binary_file(
        input [8*32-1:0] filename,
        input [2:0] op_code,
        input [8*8-1:0] name
    );
        begin
            fd = $fopen(filename, "r");
            if (fd == 0) begin
                $display("ERROR: cannot open %s", filename);
                $finish;
            end
            line_num = 0; errors = 0; total = 0;

            while (!$feof(fd)) begin
                rc = $fscanf(fd, "%h %h %h %h %h %h\n",
                             va_sign, va_mag, vb_sign, vb_mag, exp_sign, exp_mag);
                if (rc != 6) begin
                    if (!$feof(fd))
                        $display("WARN: parse error line %0d", line_num);
                    disable test_binary_file;
                end
                line_num = line_num + 1;
                total = total + 1;

                issue_binary_op(op_code, va_sign[0], va_mag, vb_sign[0], vb_mag);

                if (r_sign !== exp_sign[0] || r_mag !== exp_mag) begin
                    errors = errors + 1;
                    if (errors <= 10)
                        $display("FAIL %s line %0d: a=%h_%h b=%h_%h exp=%h_%h got=%h_%h cyc=%0d",
                                 name, line_num, va_sign, va_mag, vb_sign, vb_mag,
                                 exp_sign, exp_mag, {7'b0, r_sign}, r_mag, cycle_count);
                end
            end
            $fclose(fd);
            $display("%s: %0d/%0d passed (%0d errors)", name, total - errors, total, errors);
        end
    endtask

    task test_unary_file(
        input [8*32-1:0] filename,
        input [2:0] op_code,
        input [8*8-1:0] name
    );
        begin
            fd = $fopen(filename, "r");
            if (fd == 0) begin
                $display("ERROR: cannot open %s", filename);
                $finish;
            end
            line_num = 0; errors = 0; total = 0;

            while (!$feof(fd)) begin
                rc = $fscanf(fd, "%h %h %h %h\n",
                             va_sign, va_mag, exp_sign, exp_mag);
                if (rc != 4) begin
                    if (!$feof(fd))
                        $display("WARN: parse error line %0d", line_num);
                    disable test_unary_file;
                end
                line_num = line_num + 1;
                total = total + 1;

                issue_unary_op(op_code, va_sign[0], va_mag);

                if (r_sign !== exp_sign[0] || r_mag !== exp_mag) begin
                    errors = errors + 1;
                    if (errors <= 10)
                        $display("FAIL %s line %0d: a=%h_%h exp=%h_%h got=%h_%h cyc=%0d",
                                 name, line_num, va_sign, va_mag,
                                 exp_sign, exp_mag, {7'b0, r_sign}, r_mag, cycle_count);
                end
            end
            $fclose(fd);
            $display("%s: %0d/%0d passed (%0d errors)", name, total - errors, total, errors);
        end
    endtask

    initial begin
        clk = 0; rst_n = 0; op_valid = 0;
        a_sign = 0; a_mag = 0; b_sign = 0; b_mag = 0; op = 0;
        #20 rst_n = 1;
        #10;

        $display("========================================");
        $display("  LNS8 ALU — Full Integration Test");
        $display("========================================");

        test_binary_file("vectors/mul_vectors.hex", `LNS8_OP_MUL, "MUL");
        test_binary_file("vectors/div_vectors.hex", `LNS8_OP_DIV, "DIV");
        test_binary_file("vectors/add_vectors.hex", `LNS8_OP_ADD, "ADD");
        test_binary_file("vectors/sub_vectors.hex", `LNS8_OP_SUB, "SUB");
        test_unary_file("vectors/exp_vectors.hex",  `LNS8_OP_EXP, "EXP");
        test_unary_file("vectors/ln_vectors.hex",   `LNS8_OP_LN,  "LN ");

        $display("========================================");
        #100 $finish;
    end
endmodule
