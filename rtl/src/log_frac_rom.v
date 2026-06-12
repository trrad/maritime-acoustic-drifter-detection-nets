// LOG_FRAC LUT ROM: 16 entries x 4-bit, combinational
// LOG_FRAC[i] = round(log2(1 + i/16) * 16)
module log_frac_rom (
    input  wire [3:0] addr,
    output reg  [3:0] data
);
    always @(*) begin
        case (addr)
            4'd0:  data = 4'd0;
            4'd1:  data = 4'd1;
            4'd2:  data = 4'd3;
            4'd3:  data = 4'd4;
            4'd4:  data = 4'd5;
            4'd5:  data = 4'd6;
            4'd6:  data = 4'd7;
            4'd7:  data = 4'd8;
            4'd8:  data = 4'd9;
            4'd9:  data = 4'd10;
            4'd10: data = 4'd11;
            4'd11: data = 4'd12;
            4'd12: data = 4'd13;
            4'd13: data = 4'd14;
            4'd14: data = 4'd15;
            4'd15: data = 4'd15;
        endcase
    end
endmodule
