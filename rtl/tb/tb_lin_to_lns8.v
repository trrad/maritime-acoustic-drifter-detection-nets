`timescale 1ns/1ps
`include "lns8_pkg.v"

// Round-trip test: LNS8 → signed 16-bit FP → LNS8
// Tests all 510 valid non-zero LNS8 values (255 magnitudes × 2 signs).
// Verifies identity or ±1 LSB for values within the representable range.

module tb_lin_to_lns8;

    // LNS8 → fixed-point ROM (same values as pf_resampler / pf_estimator)
    reg [15:0] lns_to_lin_rom [0:15];
    initial begin
        lns_to_lin_rom[0]  = 16'd256;
        lns_to_lin_rom[1]  = 16'd267;
        lns_to_lin_rom[2]  = 16'd279;
        lns_to_lin_rom[3]  = 16'd292;
        lns_to_lin_rom[4]  = 16'd305;
        lns_to_lin_rom[5]  = 16'd318;
        lns_to_lin_rom[6]  = 16'd332;
        lns_to_lin_rom[7]  = 16'd347;
        lns_to_lin_rom[8]  = 16'd362;
        lns_to_lin_rom[9]  = 16'd378;
        lns_to_lin_rom[10] = 16'd395;
        lns_to_lin_rom[11] = 16'd412;
        lns_to_lin_rom[12] = 16'd431;
        lns_to_lin_rom[13] = 16'd450;
        lns_to_lin_rom[14] = 16'd470;
        lns_to_lin_rom[15] = 16'd490;
    end

    // DUT
    reg  [15:0] fp_in;
    wire        lns_sign;
    wire [7:0]  lns_mag;

    lin_to_lns8 u_dut (
        .fp_in(fp_in),
        .lns_sign(lns_sign),
        .lns_mag(lns_mag)
    );

    // Forward conversion: LNS8 → unsigned 16-bit fixed-point
    function [15:0] lns_to_unsigned_fp;
        input [7:0] mag;
        reg signed [3:0] int_part;
        reg [3:0] frac_part;
        reg [3:0] shift_r;
        begin
            if (mag == `ZERO_LOG_MAG) begin
                lns_to_unsigned_fp = 16'd0;
            end else begin
                int_part  = mag[7:4];
                frac_part = mag[3:0];
                if (int_part >= 0)
                    lns_to_unsigned_fp = lns_to_lin_rom[frac_part] << int_part;
                else begin
                    shift_r = ~int_part + 4'd1;
                    lns_to_unsigned_fp = lns_to_lin_rom[frac_part] >> shift_r;
                end
            end
        end
    endfunction

    integer mag_i, sign_i;
    integer total, exact, off1, skip_quant, skip_overflow, fail;
    reg [7:0]  orig_mag;
    reg        orig_sign;
    reg [15:0] uval;
    reg signed [16:0] wide;
    reg signed [15:0] fp_val;
    integer mag_diff, signed_orig, signed_result;

    initial begin
        total = 0; exact = 0; off1 = 0;
        skip_quant = 0; skip_overflow = 0; fail = 0;

        // Test zero input
        fp_in = 16'd0;
        #1;
        if (lns_mag != `ZERO_LOG_MAG) begin
            $display("FAIL: zero input -> mag %0d (expected %0d)", lns_mag, `ZERO_LOG_MAG);
            fail = fail + 1;
        end

        // Test all non-zero LNS8 values: mag from -127 to +127
        for (sign_i = 0; sign_i < 2; sign_i = sign_i + 1) begin
            for (mag_i = -127; mag_i <= 127; mag_i = mag_i + 1) begin
                orig_sign = sign_i[0];
                orig_mag  = mag_i[7:0];
                uval = lns_to_unsigned_fp(orig_mag);

                // Skip values that overflow signed 16-bit (unsigned > 32767)
                if (uval > 16'd32767) begin
                    skip_overflow = skip_overflow + 1;
                end
                // Skip values where FP quantization is too coarse (uval < 16)
                // Multiple LNS8 values collapse to same FP — can't round-trip
                else if (uval < 16'd16) begin
                    skip_quant = skip_quant + 1;
                end else begin
                    // Forward: LNS8 → signed FP (with saturation)
                    if (orig_sign && uval != 16'd0)
                        wide = -$signed({1'b0, uval});
                    else
                        wide = $signed({1'b0, uval});
                    fp_val = wide[15:0];

                    // Inverse: signed FP → LNS8
                    fp_in = fp_val;
                    #1;

                    total = total + 1;

                    // Check sign
                    if (lns_sign != orig_sign) begin
                        $display("FAIL sign: s=%0d m=%0d uval=%0d fp=%0d -> s=%0d m=%0d",
                                 orig_sign, mag_i, uval, fp_val, lns_sign, lns_mag);
                        fail = fail + 1;
                    end else begin
                        // Check magnitude (±1 LSB)
                        signed_orig   = (orig_mag[7]) ? ({24'hFFFFFF, orig_mag}) : ({24'h0, orig_mag});
                        signed_result = (lns_mag[7])  ? ({24'hFFFFFF, lns_mag})  : ({24'h0, lns_mag});
                        mag_diff = signed_result - signed_orig;
                        if (mag_diff < 0) mag_diff = -mag_diff;

                        if (mag_diff == 0)
                            exact = exact + 1;
                        else if (mag_diff <= 1)
                            off1 = off1 + 1;
                        else begin
                            $display("FAIL: s=%0d m=%0d uval=%0d fp=%0d -> s=%0d m=%0d (diff=%0d)",
                                     orig_sign, mag_i, uval, $signed(fp_val),
                                     lns_sign, signed_result, mag_diff);
                            fail = fail + 1;
                        end
                    end
                end
            end
        end

        $display("========================================");
        $display("  lin_to_lns8 round-trip test");
        $display("========================================");
        $display("  Tested (in range): %0d", total);
        $display("  Exact match:       %0d", exact);
        $display("  Off by +/-1:       %0d", off1);
        $display("  Skip (FP quant):   %0d  (uval < 16, expected)", skip_quant);
        $display("  Skip (overflow):   %0d  (uval > 32767, expected)", skip_overflow);
        $display("  Failures:          %0d", fail);

        if (fail == 0)
            $display("  PASS");
        else
            $display("  FAIL: %0d values exceeded +/-1 LSB tolerance", fail);

        $display("========================================");
        $finish;
    end

endmodule
