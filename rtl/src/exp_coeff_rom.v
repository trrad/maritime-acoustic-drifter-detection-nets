// EXP_COEFF ROM: 16 entries x 16-bit
// EXP_COEFF[f] = round(2^(f/16) * log2(e) * 16 * 256)
module exp_coeff_rom (
    input  wire        clk,
    input  wire [3:0]  addr,
    output reg  [15:0] data
);
    reg [15:0] mem [0:15];

    initial begin
        mem[ 0] = 16'h1715;  // 5909
        mem[ 1] = 16'h181b;  // 6171
        mem[ 2] = 16'h192c;  // 6444
        mem[ 3] = 16'h1a49;  // 6729
        mem[ 4] = 16'h1b73;  // 7027
        mem[ 5] = 16'h1caa;  // 7338
        mem[ 6] = 16'h1def;  // 7663
        mem[ 7] = 16'h1f43;  // 8003
        mem[ 8] = 16'h20a5;  // 8357
        mem[ 9] = 16'h2217;  // 8727
        mem[10] = 16'h2399;  // 9113
        mem[11] = 16'h252d;  // 9517
        mem[12] = 16'h26d2;  // 9938
        mem[13] = 16'h288a;  // 10378
        mem[14] = 16'h2a56;  // 10838
        mem[15] = 16'h2c35;  // 11317
    end

    always @(posedge clk)
        data <= mem[addr];
endmodule
