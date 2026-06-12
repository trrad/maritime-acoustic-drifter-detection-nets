// LNS8 package: parameters, constants, op encodings
// 8-bit log-magnitude (s3.4 fixed-point), 1-bit sign

// Op encoding (3-bit)
`define LNS8_OP_MUL  3'd0
`define LNS8_OP_DIV  3'd1
`define LNS8_OP_ADD  3'd2
`define LNS8_OP_SUB  3'd3
`define LNS8_OP_EXP  3'd4
`define LNS8_OP_LN   3'd5

// Constants
`define FRAC_BITS     4
`define ZERO_LOG_MAG  8'h80   // -128 = zero sentinel
`define LOG_MAG_MAX   8'h7F   // +127
`define LOG_MAG_MIN   8'h80   // -128
`define LN_COEFF      16'h0B17 // 2839
