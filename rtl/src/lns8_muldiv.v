// LNS8 Multiply/Divide — combinational, 1 cycle
// MUL: sign = a_sign XOR b_sign, mag = a_mag + b_mag (saturated)
// DIV: sign = a_sign XOR b_sign, mag = a_mag - b_mag (saturated)
`include "lns8_pkg.v"

module lns8_muldiv (
    input  wire        a_sign,
    input  wire [7:0]  a_mag,
    input  wire        b_sign,
    input  wire [7:0]  b_mag,
    input  wire        is_div,   // 0=MUL, 1=DIV
    output wire        r_sign,
    output wire [7:0]  r_mag
);
    wire a_zero = (a_sign == 1'b0) && (a_mag == `ZERO_LOG_MAG);
    wire b_zero = (b_sign == 1'b0) && (b_mag == `ZERO_LOG_MAG);

    // Sign: XOR (both use same sign rule)
    wire result_sign = a_sign ^ b_sign;

    // Magnitude: add or subtract, with saturation
    wire signed [8:0] a_ext = {a_mag[7], a_mag};  // sign-extend to 9 bits
    wire signed [8:0] b_ext = {b_mag[7], b_mag};
    wire signed [8:0] sum   = is_div ? (a_ext - b_ext) : (a_ext + b_ext);

    // Saturate to int8
    wire signed [7:0] sat_mag;
    sat_clip #(.WIDTH(9)) u_sat (.in(sum), .out(sat_mag));

    // Zero handling: if either input is zero for MUL, or numerator is zero for DIV
    wire result_zero = a_zero | (b_zero & ~is_div);

    // Normalize: mag=ZERO_LOG_MAG → sign=0 (zero encoding)
    wire out_is_zero = result_zero | (sat_mag == `ZERO_LOG_MAG);
    assign r_sign = out_is_zero ? 1'b0 : result_sign;
    assign r_mag  = result_zero ? `ZERO_LOG_MAG : sat_mag;
endmodule
