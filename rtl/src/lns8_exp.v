// LNS8 Exp — 2-cycle FSM
// Computes exp(v) where v = a_sign * 2^(a_mag/16)
// Output log_mag = round(v * log2(e) * 16)
//
// Cycle 1: Split mag, issue ROM read for EXP_COEFF[F]
// Cycle 2: Barrel shift ROM result, apply sign, saturate, output
`include "lns8_pkg.v"

module lns8_exp (
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
    localparam S_IDLE  = 2'd0,
               S_ROM   = 2'd1,
               S_SHIFT = 2'd2;

    reg [1:0] state;
    assign busy = (state != S_IDLE);

    // Latched
    reg        la_sign;
    reg        input_neg;     // input value is negative
    reg        input_zero;    // input is zero → exp(0) = 1
    reg [4:0]  shift_amt;     // 8 - I, range 1..16

    // ROM interface
    reg  [3:0]  exp_addr;
    wire [15:0] exp_data;

    exp_coeff_rom u_exp_rom (
        .clk(clk),
        .addr(exp_addr),
        .data(exp_data)
    );

    // Barrel shift: coeff >> shift_amt with rounding
    wire [13:0] coeff = exp_data[13:0];
    wire [14:0] extended = {coeff, 1'b0};
    wire [14:0] shifted_ext = extended >> shift_amt;
    wire [13:0] shifted = shifted_ext[14:1] + {13'b0, shifted_ext[0]};

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state  <= S_IDLE;
            done   <= 1'b0;
            r_sign <= 1'b1;
            r_mag  <= 8'd0;
        end else begin
            done <= 1'b0;

            case (state)
                S_IDLE: begin
                    if (start) begin
                        la_sign <= a_sign;
                        input_zero <= (a_mag == `ZERO_LOG_MAG);
                        input_neg  <= a_sign;

                        // F = a_mag[3:0] selects ROM entry
                        exp_addr <= a_mag[3:0];

                        // I = a_mag[7:4] as signed 4-bit, sign-extended to 5 bits
                        // shift = 8 - I
                        shift_amt <= 5'd8 - {a_mag[7], a_mag[7:4]};

                        state <= S_ROM;
                    end
                end

                S_ROM: begin
                    // ROM data available next cycle
                    state <= S_SHIFT;
                end

                S_SHIFT: begin
                    if (input_zero) begin
                        // exp(0) = 1 → log_mag = 0
                        r_sign <= 1'b0;
                        r_mag  <= 8'd0;
                    end else begin
                        // Apply sign: if input was negative, negate the output magnitude
                        // exp() is always positive → r_sign = 0
                        r_sign <= 1'b0;
                        if (input_neg) begin
                            // out_mag = -shifted (negative log-domain = small positive value)
                            if (shifted > 14'd128) begin
                                r_mag <= `ZERO_LOG_MAG;  // underflow → 0
                            end else begin
                                r_mag <= (~shifted[7:0]) + 8'd1;  // negate
                            end
                        end else begin
                            // out_mag = +shifted
                            if (shifted > 14'd127) begin
                                r_mag <= `LOG_MAG_MAX;  // overflow → max
                            end else begin
                                r_mag <= shifted[7:0];
                            end
                        end
                    end

                    done  <= 1'b1;
                    state <= S_IDLE;
                end

                default: state <= S_IDLE;
            endcase
        end
    end
endmodule
