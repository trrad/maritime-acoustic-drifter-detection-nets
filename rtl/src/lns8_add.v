// LNS8 Add — 4-cycle FSM
// Cycle 1: Latch inputs, compare magnitudes
// Cycle 2: Compute abs_diff, set ROM address
// Cycle 3: ROM read latency
// Cycle 4: Apply correction, saturate, output
`include "lns8_pkg.v"

module lns8_add (
    input  wire        clk,
    input  wire        rst_n,
    input  wire        start,
    input  wire        a_sign,
    input  wire [7:0]  a_mag,
    input  wire        b_sign,
    input  wire [7:0]  b_mag,
    output reg         r_sign,
    output reg  [7:0]  r_mag,
    output reg         done,
    output wire        busy
);
    localparam S_IDLE = 3'd0,
               S_COMP = 3'd1,
               S_ADDR = 3'd2,
               S_ROM  = 3'd3,
               S_CORR = 3'd4;

    reg [2:0] state;
    assign busy = (state != S_IDLE);

    // Latched inputs
    reg        la_sign, lb_sign;
    reg signed [7:0] la_mag, lb_mag;

    // Intermediate results
    reg        same_sign;
    reg        use_phi_minus;
    reg        result_sign_r;
    reg signed [7:0] max_mag_r;
    reg [7:0]  abs_diff_r;
    reg        a_is_zero, b_is_zero;
    reg        exact_cancel;
    reg        diff_ge_128;   // abs_diff >= 128 → correction is 0

    // Signed 9-bit diff for comparison
    wire signed [8:0] diff9 = {la_mag[7], la_mag} - {lb_mag[7], lb_mag};
    wire a_ge_b = ~diff9[8];  // a_mag >= b_mag (signed)

    // Absolute value of diff (9-bit unsigned)
    wire [8:0] abs_diff9 = diff9[8] ? (-diff9) : diff9;

    // PHI ROM interface
    reg  [7:0] phi_addr;
    wire [7:0] phi_data;

    phi_rom u_phi (
        .clk(clk),
        .addr(phi_addr),
        .data(phi_data)
    );

    // Correction: max_mag + phi_data (both signed int8)
    wire signed [8:0] corrected = {max_mag_r[7], max_mag_r} +
                                  {{1{phi_data[7]}}, $signed(phi_data)};
    wire signed [7:0] sat_result;
    sat_clip #(.WIDTH(9)) u_sat (.in(corrected), .out(sat_result));

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state   <= S_IDLE;
            done    <= 1'b0;
            r_sign  <= 1'b0;
            r_mag   <= `ZERO_LOG_MAG;
        end else begin
            done <= 1'b0;

            case (state)
                S_IDLE: begin
                    if (start) begin
                        la_sign <= a_sign;
                        lb_sign <= b_sign;
                        la_mag  <= a_mag;
                        lb_mag  <= b_mag;
                        a_is_zero <= (a_sign == 1'b0) && (a_mag == `ZERO_LOG_MAG);
                        b_is_zero <= (b_sign == 1'b0) && (b_mag == `ZERO_LOG_MAG);
                        state <= S_COMP;
                    end
                end

                S_COMP: begin
                    // Cycle 1: Compare
                    same_sign <= (la_sign == lb_sign);
                    use_phi_minus <= (la_sign != lb_sign);
                    exact_cancel <= (la_sign != lb_sign) && (diff9 == 0);
                    diff_ge_128 <= (abs_diff9 >= 9'd128);

                    if (a_is_zero) begin
                        result_sign_r <= lb_sign;
                        max_mag_r     <= lb_mag;
                    end else if (b_is_zero) begin
                        result_sign_r <= la_sign;
                        max_mag_r     <= la_mag;
                    end else if (a_ge_b) begin
                        result_sign_r <= la_sign;
                        max_mag_r     <= la_mag;
                    end else begin
                        result_sign_r <= lb_sign;
                        max_mag_r     <= lb_mag;
                    end

                    state <= S_ADDR;
                end

                S_ADDR: begin
                    // Cycle 2: Compute ROM address
                    // Clamp abs_diff to 7 bits (0..127)
                    if (a_is_zero || b_is_zero) begin
                        // Zero input: use phi_plus[127] = 0 for no correction
                        phi_addr <= 8'd127;
                    end else if (diff_ge_128) begin
                        // Large diff: use phi_plus[127] = 0 or phi_minus[127] = 0
                        phi_addr <= use_phi_minus ? 8'd255 : 8'd127;
                    end else begin
                        // Normal case: phi_plus at 0..127, phi_minus at 128..255
                        if (use_phi_minus)
                            phi_addr <= {1'b1, abs_diff9[6:0]};
                        else
                            phi_addr <= {1'b0, abs_diff9[6:0]};
                    end

                    state <= S_ROM;
                end

                S_ROM: begin
                    // Cycle 3: Wait for ROM read
                    state <= S_CORR;
                end

                S_CORR: begin
                    // Cycle 4: Apply correction
                    // Compute result into temporaries, then normalize
                    if (a_is_zero && b_is_zero) begin
                        r_sign <= 1'b0;
                        r_mag  <= `ZERO_LOG_MAG;
                    end else if (a_is_zero) begin
                        r_sign <= (lb_mag == `ZERO_LOG_MAG) ? 1'b0 : lb_sign;
                        r_mag  <= lb_mag;
                    end else if (b_is_zero) begin
                        r_sign <= (la_mag == `ZERO_LOG_MAG) ? 1'b0 : la_sign;
                        r_mag  <= la_mag;
                    end else if (exact_cancel) begin
                        r_sign <= 1'b0;
                        r_mag  <= `ZERO_LOG_MAG;
                    end else if (diff_ge_128) begin
                        r_sign <= (max_mag_r == `ZERO_LOG_MAG) ? 1'b0 : result_sign_r;
                        r_mag  <= max_mag_r;
                    end else if (use_phi_minus && (phi_data == 8'h80)) begin
                        r_sign <= 1'b0;
                        r_mag  <= `ZERO_LOG_MAG;
                    end else begin
                        r_sign <= (sat_result == `ZERO_LOG_MAG) ? 1'b0 : result_sign_r;
                        r_mag  <= sat_result;
                    end

                    done  <= 1'b1;
                    state <= S_IDLE;
                end

                default: state <= S_IDLE;
            endcase
        end
    end
endmodule
