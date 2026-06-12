// LNS8 ALU — Top-level multi-cycle state machine
// Dispatches to sub-modules based on op code, manages handshaking
`include "lns8_pkg.v"

module lns8_alu (
    input  wire        clk,
    input  wire        rst_n,
    // Operands
    input  wire        a_sign,
    input  wire [7:0]  a_mag,
    input  wire        b_sign,
    input  wire [7:0]  b_mag,
    // Control
    input  wire [2:0]  op,
    input  wire        op_valid,
    // Results
    output reg         r_sign,
    output reg  [7:0]  r_mag,
    output reg         r_valid,
    output wire        busy
);
    // Master FSM states
    localparam M_IDLE    = 3'd0,
               M_MULDIV  = 3'd1,  // 1-cycle combinational
               M_ADD     = 3'd2,  // wait for add FSM
               M_SUB_NEG = 3'd3,  // negate b_sign, then start add
               M_SUB_ADD = 3'd4,  // wait for add FSM (subtract path)
               M_EXP     = 3'd5,  // wait for exp FSM
               M_LN      = 3'd6;  // wait for ln FSM

    reg [2:0] mstate;
    assign busy = (mstate != M_IDLE);

    // Latched operands
    reg        la_sign, lb_sign;
    reg [7:0]  la_mag, lb_mag;

    // --- Mul/Div (combinational) ---
    wire       md_r_sign;
    wire [7:0] md_r_mag;
    reg        md_is_div;

    lns8_muldiv u_muldiv (
        .a_sign(la_sign),
        .a_mag(la_mag),
        .b_sign(lb_sign),
        .b_mag(lb_mag),
        .is_div(md_is_div),
        .r_sign(md_r_sign),
        .r_mag(md_r_mag)
    );

    // --- Add (4-cycle FSM) ---
    reg        add_start;
    reg        add_a_sign;
    reg [7:0]  add_a_mag;
    reg        add_b_sign;
    reg [7:0]  add_b_mag;
    wire       add_r_sign;
    wire [7:0] add_r_mag;
    wire       add_done;
    wire       add_busy;

    lns8_add u_add (
        .clk(clk),
        .rst_n(rst_n),
        .start(add_start),
        .a_sign(add_a_sign),
        .a_mag(add_a_mag),
        .b_sign(add_b_sign),
        .b_mag(add_b_mag),
        .r_sign(add_r_sign),
        .r_mag(add_r_mag),
        .done(add_done),
        .busy(add_busy)
    );

    // --- Exp (2-cycle FSM) ---
    reg        exp_start;
    wire       exp_r_sign;
    wire [7:0] exp_r_mag;
    wire       exp_done;
    wire       exp_busy;

    lns8_exp u_exp (
        .clk(clk),
        .rst_n(rst_n),
        .start(exp_start),
        .a_sign(la_sign),
        .a_mag(la_mag),
        .r_sign(exp_r_sign),
        .r_mag(exp_r_mag),
        .done(exp_done),
        .busy(exp_busy)
    );

    // --- Ln (2-cycle FSM) ---
    reg        ln_start;
    wire       ln_r_sign;
    wire [7:0] ln_r_mag;
    wire       ln_done;
    wire       ln_busy;

    lns8_ln u_ln (
        .clk(clk),
        .rst_n(rst_n),
        .start(ln_start),
        .a_sign(la_sign),
        .a_mag(la_mag),
        .r_sign(ln_r_sign),
        .r_mag(ln_r_mag),
        .done(ln_done),
        .busy(ln_busy)
    );

    // Intermediate result (pre-normalization)
    reg        raw_sign;
    reg  [7:0] raw_mag;
    reg        raw_valid;

    // Output normalization: if mag == ZERO_LOG_MAG, force sign = 0
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            r_valid <= 1'b0;
            r_sign  <= 1'b0;
            r_mag   <= `ZERO_LOG_MAG;
        end else begin
            r_valid <= raw_valid;
            r_mag   <= raw_mag;
            r_sign  <= (raw_mag == `ZERO_LOG_MAG) ? 1'b0 : raw_sign;
        end
    end

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            mstate    <= M_IDLE;
            raw_valid <= 1'b0;
            raw_sign  <= 1'b0;
            raw_mag   <= `ZERO_LOG_MAG;
            add_start <= 1'b0;
            exp_start <= 1'b0;
            ln_start  <= 1'b0;
        end else begin
            raw_valid <= 1'b0;
            add_start <= 1'b0;
            exp_start <= 1'b0;
            ln_start  <= 1'b0;

            case (mstate)
                M_IDLE: begin
                    if (op_valid) begin
                        la_sign <= a_sign;
                        lb_sign <= b_sign;
                        la_mag  <= a_mag;
                        lb_mag  <= b_mag;

                        case (op)
                            `LNS8_OP_MUL: begin
                                md_is_div <= 1'b0;
                                mstate <= M_MULDIV;
                            end
                            `LNS8_OP_DIV: begin
                                md_is_div <= 1'b1;
                                mstate <= M_MULDIV;
                            end
                            `LNS8_OP_ADD: begin
                                add_a_sign <= a_sign;
                                add_a_mag  <= a_mag;
                                add_b_sign <= b_sign;
                                add_b_mag  <= b_mag;
                                add_start  <= 1'b1;
                                mstate <= M_ADD;
                            end
                            `LNS8_OP_SUB: begin
                                // Subtract = negate b then add
                                // Negate: flip b_sign (but not if b is zero)
                                // Zero = sign==0 AND mag==ZERO_LOG_MAG
                                add_a_sign <= a_sign;
                                add_a_mag  <= a_mag;
                                add_b_sign <= ((b_sign == 1'b0) && (b_mag == `ZERO_LOG_MAG)) ? 1'b0 : ~b_sign;
                                add_b_mag  <= b_mag;
                                mstate <= M_SUB_NEG;
                            end
                            `LNS8_OP_EXP: begin
                                exp_start <= 1'b1;
                                mstate <= M_EXP;
                            end
                            `LNS8_OP_LN: begin
                                ln_start <= 1'b1;
                                mstate <= M_LN;
                            end
                            default: begin
                                mstate <= M_IDLE;
                            end
                        endcase
                    end
                end

                M_MULDIV: begin
                    raw_sign  <= md_r_sign;
                    raw_mag   <= md_r_mag;
                    raw_valid <= 1'b1;
                    mstate    <= M_IDLE;
                end

                M_ADD: begin
                    if (add_done) begin
                        raw_sign  <= add_r_sign;
                        raw_mag   <= add_r_mag;
                        raw_valid <= 1'b1;
                        mstate    <= M_IDLE;
                    end
                end

                M_SUB_NEG: begin
                    add_start <= 1'b1;
                    mstate <= M_SUB_ADD;
                end

                M_SUB_ADD: begin
                    if (add_done) begin
                        raw_sign  <= add_r_sign;
                        raw_mag   <= add_r_mag;
                        raw_valid <= 1'b1;
                        mstate    <= M_IDLE;
                    end
                end

                M_EXP: begin
                    if (exp_done) begin
                        raw_sign  <= exp_r_sign;
                        raw_mag   <= exp_r_mag;
                        raw_valid <= 1'b1;
                        mstate    <= M_IDLE;
                    end
                end

                M_LN: begin
                    if (ln_done) begin
                        raw_sign  <= ln_r_sign;
                        raw_mag   <= ln_r_mag;
                        raw_valid <= 1'b1;
                        mstate    <= M_IDLE;
                    end
                end

                default: mstate <= M_IDLE;
            endcase
        end
    end
endmodule
