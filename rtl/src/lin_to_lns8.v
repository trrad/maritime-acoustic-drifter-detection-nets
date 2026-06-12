// lin_to_lns8 — combinational converter: signed 16-bit fixed-point → LNS8
//
// Input:  16-bit signed fixed-point (8.8 format, 2's complement)
// Output: LNS8 sign (0=pos, 1=neg) + 8-bit magnitude
//
// Algorithm:
//   1. Extract sign, take absolute value
//   2. Find MSB position P via priority encoder (0..15)
//   3. Integer part I = P - 8 (maps 8.8 FP exponent to LNS8 scale)
//   4. Extract 4 fractional bits below MSB → log_frac_rom lookup
//   5. Assemble mag = {I[3:0], F[3:0]}
//   6. Zero → ZERO_LOG_MAG; clamp -128 → -127 (avoid zero sentinel)

`include "lns8_pkg.v"

module lin_to_lns8 (
    input  wire [15:0] fp_in,       // signed 8.8 fixed-point (2's complement)
    output wire        lns_sign,    // 0 = positive, 1 = negative
    output wire [7:0]  lns_mag      // LNS8 magnitude
);

    // Step 1: sign and absolute value
    wire input_sign = fp_in[15];
    wire [15:0] abs_val = input_sign ? (~fp_in + 16'd1) : fp_in;
    wire is_zero = (abs_val == 16'd0);

    // Step 2: find MSB position (0..15)
    wire [4:0] msb_pos;
    wire       msb_valid;

    priority_enc u_pe (
        .in({3'b0, abs_val}),   // zero-extend 16 → 19 bits
        .pos(msb_pos),
        .valid(msb_valid)
    );

    // Step 3: integer part I = P - 8 (signed 4-bit, range -8..+7)
    wire [3:0] int_part = msb_pos[3:0] - 4'd8;

    // Step 4: extract 4 fractional bits below MSB
    // Shift left to normalize MSB to position 15, then take bits [14:11]
    wire [3:0] left_shift = 4'd15 - msb_pos[3:0];
    wire [15:0] normalized = abs_val << left_shift;
    wire [3:0] frac_bits = normalized[14:11];

    // Step 5: log_frac_rom lookup for fractional correction
    wire [3:0] log_frac;

    log_frac_rom u_lfr (
        .addr(frac_bits),
        .data(log_frac)
    );

    // Step 6: assemble magnitude and handle edge cases
    wire [7:0] raw_mag = {int_part, log_frac};

    // If raw_mag == 0x80 (ZERO_LOG_MAG) for a non-zero input, clamp to -127
    wire [7:0] clamped_mag = (raw_mag == `ZERO_LOG_MAG) ? 8'h81 : raw_mag;

    assign lns_sign = is_zero ? 1'b0 : input_sign;
    assign lns_mag  = (is_zero || !msb_valid) ? `ZERO_LOG_MAG : clamped_mag;

endmodule
