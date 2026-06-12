// LNS8 Ln — 2-cycle FSM
// Computes ln(a) where a = 2^(a_mag/16)
// ln(a) = a_mag * ln(2)/16, then convert to LNS8
//
// Cycle 1: Multiply abs(a_mag) * LN_COEFF → abs_fp (19-bit)
// Cycle 2: Priority encode + LOG_FRAC → log_mag → saturate → output
`include "lns8_pkg.v"

module lns8_ln (
    input  wire        clk,
    input  wire        rst_n,
    input  wire        start,
    input  wire        a_sign,
    input  wire [7:0]  a_mag,
    output reg         r_sign,
    output reg  [7:0]  r_mag,
    output reg         done,
    output wire        busy
);
    localparam S_IDLE = 2'd0,
               S_MUL  = 2'd1,
               S_LOG  = 2'd2;

    reg [1:0] state;
    assign busy = (state != S_IDLE);

    // Latched
    reg        is_zero;       // ln(1) = 0 (a_mag == 0)
    reg        is_invalid;    // ln(0) or ln(negative)
    reg        result_neg;    // result is negative (a_mag < 0 signed)

    // Multiply result: abs(a_mag) * LN_COEFF, max = 128 * 2839 = 363392
    reg [18:0] abs_fp;

    // Priority encoder (combinational on abs_fp)
    wire [4:0] msb_pos;
    wire       msb_valid;

    priority_enc u_pri (
        .in(abs_fp),
        .pos(msb_pos),
        .valid(msb_valid)
    );

    // Extract 4 bits below MSB for LOG_FRAC address
    // frac_idx = (abs_fp >> (msb_pos - 4)) & 0xF  when msb_pos >= 4
    // frac_idx = (abs_fp << (4 - msb_pos)) & 0xF  when msb_pos < 4
    wire [3:0] frac_idx;
    wire [18:0] abs_fp_shifted = (msb_pos >= 5'd4) ?
                                 (abs_fp >> (msb_pos - 5'd4)) :
                                 (abs_fp << (5'd4 - msb_pos));
    assign frac_idx = abs_fp_shifted[3:0];

    // LOG_FRAC ROM (combinational)
    wire [3:0] log_frac_data;

    log_frac_rom u_log_frac (
        .addr(frac_idx),
        .data(log_frac_data)
    );

    // log2_x16 = msb_pos * 16 + log_frac_data
    // log_mag = log2_x16 - 256
    wire [9:0] log2_x16 = {msb_pos, 4'b0} + {6'b0, log_frac_data};
    wire signed [9:0] log_mag_raw = $signed(log2_x16) - 10'sd256;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state  <= S_IDLE;
            done   <= 1'b0;
            r_sign <= 1'b0;
            r_mag  <= `ZERO_LOG_MAG;
            abs_fp <= 19'd0;
        end else begin
            done <= 1'b0;

            case (state)
                S_IDLE: begin
                    if (start) begin
                        // ln(negative) or ln(0) → invalid
                        is_invalid <= (a_sign == 1'b1) || (a_mag == `ZERO_LOG_MAG);

                        // ln(1) = 0 when a_mag == 0 (value = 2^0 = 1)
                        is_zero <= (a_sign == 1'b0) && (a_mag == 8'd0);

                        // Result sign: negative when a_mag < 0 (value < 1)
                        result_neg <= a_mag[7];

                        // Multiply: abs(a_mag) * LN_COEFF
                        if (a_mag[7])
                            abs_fp <= ({11'b0, ~a_mag + 8'd1}) * {3'b0, `LN_COEFF};
                        else
                            abs_fp <= ({11'b0, a_mag}) * {3'b0, `LN_COEFF};

                        state <= S_MUL;
                    end
                end

                S_MUL: begin
                    // abs_fp now stable, priority encoder + LOG_FRAC are combinational
                    // Results available immediately; we register in next state
                    state <= S_LOG;
                end

                S_LOG: begin
                    if (is_invalid) begin
                        // ln(0) or ln(negative): large negative penalty
                        // Python returns (-1, LOG_MAG_MAX) = negative, mag 127
                        r_sign <= 1'b1;  // negative
                        r_mag  <= `LOG_MAG_MAX;
                    end else if (is_zero) begin
                        // ln(1) = 0
                        r_sign <= 1'b0;
                        r_mag  <= `ZERO_LOG_MAG;
                    end else begin
                        r_sign <= result_neg;

                        // Saturate log_mag_raw to int8
                        if (log_mag_raw > 10'sd127)
                            r_mag <= `LOG_MAG_MAX;
                        else if (log_mag_raw < -10'sd128)
                            r_mag <= `ZERO_LOG_MAG;
                        else
                            r_mag <= log_mag_raw[7:0];
                    end

                    done  <= 1'b1;
                    state <= S_IDLE;
                end

                default: state <= S_IDLE;
            endcase
        end
    end
endmodule
