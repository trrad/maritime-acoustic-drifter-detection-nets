// Signed saturation: clip wider signed value to int8 range [-128, +127]
module sat_clip #(
    parameter WIDTH = 10
) (
    input  wire signed [WIDTH-1:0] in,
    output wire signed [7:0]       out
);
    wire overflow  = (in > $signed({{(WIDTH-8){1'b0}}, 8'h7F}));
    wire underflow = (in < $signed({{(WIDTH-8){1'b1}}, 8'h80}));

    assign out = overflow  ? 8'h7F :
                 underflow ? 8'h80 :
                 in[7:0];
endmodule
