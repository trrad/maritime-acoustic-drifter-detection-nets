// 14-bit right-shift with round-to-nearest
// shift range: 1..16 (0 maps to no-shift passthrough)
module barrel_shifter (
    input  wire [13:0] in,
    input  wire [4:0]  shift,  // 0..16
    output wire [13:0] out
);
    // Extended by 1 bit for rounding
    wire [14:0] extended = {in, 1'b0};

    // Shift right by (shift) with the extra LSB as round bit
    // Total shift of extended = shift, then take [14:1] + round
    wire [14:0] shifted = extended >> shift;
    wire        round_bit = shifted[0];

    assign out = shifted[14:1] + {13'b0, round_bit};
endmodule
