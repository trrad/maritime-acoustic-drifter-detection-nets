// LFSR RNG — 32-bit maximal-length LFSR + 256-entry Gaussian inverse-CDF ROM
// Taps: 32, 22, 2, 1 (maximal length = 2^32 - 1)
// Output: LNS8 (sign, mag) approximating N(0,1)
//
// Two improvements over naive LFSR→ROM approach:
//   1. Advance by 8 shifts per sample (unrolled feedback) — decorrelates
//      consecutive samples so 6D per-particle noise is independent.
//   2. 256-entry ROM maps 8-bit uniform index to half-normal |N(0,1)| via
//      inverse CDF. Sign from a separate high bit. 98 unique LNS8 magnitudes
//      (vs 16 in the old 4-bit ROM).
//
// When INJECT_NOISE is defined, reads noise from scenario_noise.hex instead
// of generating it. For simulation-only comparison with Python reference.
//
// Zero ALU cycles per noise sample.

`include "lns8_pkg.v"

module lfsr_rng (
    input  wire        clk,
    input  wire        rst_n,
    input  wire        advance,   // pulse to get next sample
    input  wire [31:0] seed,      // initial seed (must be non-zero)
    input  wire        seed_load, // load seed value
    output wire        noise_sign, // 0=positive, 1=negative
    output wire [7:0]  noise_mag,
    output wire [31:0] lfsr_raw   // raw LFSR state (for debug/other uses)
);

`ifdef INJECT_NOISE
    // -----------------------------------------------------------------------
    // File-based noise injection (simulation only)
    // -----------------------------------------------------------------------
    reg [7:0] noise_file_sign [0:131071];
    reg [7:0] noise_file_mag  [0:131071];
    reg [16:0] noise_idx;
    reg [31:0] lfsr;  // still maintained for lfsr_raw

    initial begin
        $readmemh("vectors/scenario_noise_sign.hex", noise_file_sign);
        $readmemh("vectors/scenario_noise_mag.hex", noise_file_mag);
    end

    // Maintain real LFSR for lfsr_raw (used by resampler)
    wire [31:0] inj_s0 = lfsr;
    wire [31:0] inj_s1 = {inj_s0[30:0], inj_s0[31] ^ inj_s0[21] ^ inj_s0[1] ^ inj_s0[0]};
    wire [31:0] inj_s2 = {inj_s1[30:0], inj_s1[31] ^ inj_s1[21] ^ inj_s1[1] ^ inj_s1[0]};
    wire [31:0] inj_s3 = {inj_s2[30:0], inj_s2[31] ^ inj_s2[21] ^ inj_s2[1] ^ inj_s2[0]};
    wire [31:0] inj_s4 = {inj_s3[30:0], inj_s3[31] ^ inj_s3[21] ^ inj_s3[1] ^ inj_s3[0]};
    wire [31:0] inj_s5 = {inj_s4[30:0], inj_s4[31] ^ inj_s4[21] ^ inj_s4[1] ^ inj_s4[0]};
    wire [31:0] inj_s6 = {inj_s5[30:0], inj_s5[31] ^ inj_s5[21] ^ inj_s5[1] ^ inj_s5[0]};
    wire [31:0] inj_s7 = {inj_s6[30:0], inj_s6[31] ^ inj_s6[21] ^ inj_s6[1] ^ inj_s6[0]};

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            noise_idx <= 16'd0;
            lfsr <= 32'hDEAD_BEEF;
        end else if (seed_load) begin
            lfsr <= (seed == 32'h0) ? 32'h1 : seed;
        end else if (advance) begin
            noise_idx <= noise_idx + 16'd1;
            lfsr <= inj_s7;  // real LFSR advance for lfsr_raw
        end
    end

    assign noise_sign = noise_file_sign[noise_idx][0];
    assign noise_mag  = noise_file_mag[noise_idx];
    assign lfsr_raw   = lfsr;

`else
    // -----------------------------------------------------------------------
    // Hardware noise generation (synthesis + default simulation)
    // -----------------------------------------------------------------------
    reg [31:0] lfsr;

    // 8-shift unrolling: advance LFSR by 8 positions per sample
    // Polynomial: x^32 + x^22 + x^2 + x + 1
    wire [31:0] s0 = lfsr;
    wire [31:0] s1 = {s0[30:0], s0[31] ^ s0[21] ^ s0[1] ^ s0[0]};
    wire [31:0] s2 = {s1[30:0], s1[31] ^ s1[21] ^ s1[1] ^ s1[0]};
    wire [31:0] s3 = {s2[30:0], s2[31] ^ s2[21] ^ s2[1] ^ s2[0]};
    wire [31:0] s4 = {s3[30:0], s3[31] ^ s3[21] ^ s3[1] ^ s3[0]};
    wire [31:0] s5 = {s4[30:0], s4[31] ^ s4[21] ^ s4[1] ^ s4[0]};
    wire [31:0] s6 = {s5[30:0], s5[31] ^ s5[21] ^ s5[1] ^ s5[0]};
    wire [31:0] s7 = {s6[30:0], s6[31] ^ s6[21] ^ s6[1] ^ s6[0]};

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            lfsr <= 32'hDEAD_BEEF;
        end else if (seed_load) begin
            lfsr <= (seed == 32'h0) ? 32'h1 : seed;
        end else if (advance) begin
            lfsr <= s7;
        end
    end

    assign lfsr_raw = lfsr;

    // 256-entry Gaussian inverse-CDF ROM
    reg [7:0] gauss_rom [0:255];
    initial begin
        gauss_rom[  0] = 8'h80; gauss_rom[  1] = 8'h8f; gauss_rom[  2] = 8'h9a; gauss_rom[  3] = 8'ha2;
        gauss_rom[  4] = 8'ha8; gauss_rom[  5] = 8'had; gauss_rom[  6] = 8'hb0; gauss_rom[  7] = 8'hb4;
        gauss_rom[  8] = 8'hb7; gauss_rom[  9] = 8'hb9; gauss_rom[ 10] = 8'hbb; gauss_rom[ 11] = 8'hbe;
        gauss_rom[ 12] = 8'hbf; gauss_rom[ 13] = 8'hc1; gauss_rom[ 14] = 8'hc3; gauss_rom[ 15] = 8'hc4;
        gauss_rom[ 16] = 8'hc6; gauss_rom[ 17] = 8'hc7; gauss_rom[ 18] = 8'hc9; gauss_rom[ 19] = 8'hca;
        gauss_rom[ 20] = 8'hcb; gauss_rom[ 21] = 8'hcc; gauss_rom[ 22] = 8'hcd; gauss_rom[ 23] = 8'hce;
        gauss_rom[ 24] = 8'hcf; gauss_rom[ 25] = 8'hd0; gauss_rom[ 26] = 8'hd1; gauss_rom[ 27] = 8'hd2;
        gauss_rom[ 28] = 8'hd3; gauss_rom[ 29] = 8'hd3; gauss_rom[ 30] = 8'hd4; gauss_rom[ 31] = 8'hd5;
        gauss_rom[ 32] = 8'hd6; gauss_rom[ 33] = 8'hd6; gauss_rom[ 34] = 8'hd7; gauss_rom[ 35] = 8'hd8;
        gauss_rom[ 36] = 8'hd8; gauss_rom[ 37] = 8'hd9; gauss_rom[ 38] = 8'hda; gauss_rom[ 39] = 8'hda;
        gauss_rom[ 40] = 8'hdb; gauss_rom[ 41] = 8'hdb; gauss_rom[ 42] = 8'hdc; gauss_rom[ 43] = 8'hdc;
        gauss_rom[ 44] = 8'hdd; gauss_rom[ 45] = 8'hdd; gauss_rom[ 46] = 8'hde; gauss_rom[ 47] = 8'hdf;
        gauss_rom[ 48] = 8'hdf; gauss_rom[ 49] = 8'hdf; gauss_rom[ 50] = 8'he0; gauss_rom[ 51] = 8'he0;
        gauss_rom[ 52] = 8'he1; gauss_rom[ 53] = 8'he1; gauss_rom[ 54] = 8'he2; gauss_rom[ 55] = 8'he2;
        gauss_rom[ 56] = 8'he3; gauss_rom[ 57] = 8'he3; gauss_rom[ 58] = 8'he3; gauss_rom[ 59] = 8'he4;
        gauss_rom[ 60] = 8'he4; gauss_rom[ 61] = 8'he5; gauss_rom[ 62] = 8'he5; gauss_rom[ 63] = 8'he5;
        gauss_rom[ 64] = 8'he6; gauss_rom[ 65] = 8'he6; gauss_rom[ 66] = 8'he6; gauss_rom[ 67] = 8'he7;
        gauss_rom[ 68] = 8'he7; gauss_rom[ 69] = 8'he8; gauss_rom[ 70] = 8'he8; gauss_rom[ 71] = 8'he8;
        gauss_rom[ 72] = 8'he9; gauss_rom[ 73] = 8'he9; gauss_rom[ 74] = 8'he9; gauss_rom[ 75] = 8'hea;
        gauss_rom[ 76] = 8'hea; gauss_rom[ 77] = 8'hea; gauss_rom[ 78] = 8'hea; gauss_rom[ 79] = 8'heb;
        gauss_rom[ 80] = 8'heb; gauss_rom[ 81] = 8'heb; gauss_rom[ 82] = 8'hec; gauss_rom[ 83] = 8'hec;
        gauss_rom[ 84] = 8'hec; gauss_rom[ 85] = 8'hed; gauss_rom[ 86] = 8'hed; gauss_rom[ 87] = 8'hed;
        gauss_rom[ 88] = 8'hed; gauss_rom[ 89] = 8'hee; gauss_rom[ 90] = 8'hee; gauss_rom[ 91] = 8'hee;
        gauss_rom[ 92] = 8'hef; gauss_rom[ 93] = 8'hef; gauss_rom[ 94] = 8'hef; gauss_rom[ 95] = 8'hef;
        gauss_rom[ 96] = 8'hf0; gauss_rom[ 97] = 8'hf0; gauss_rom[ 98] = 8'hf0; gauss_rom[ 99] = 8'hf0;
        gauss_rom[100] = 8'hf1; gauss_rom[101] = 8'hf1; gauss_rom[102] = 8'hf1; gauss_rom[103] = 8'hf1;
        gauss_rom[104] = 8'hf2; gauss_rom[105] = 8'hf2; gauss_rom[106] = 8'hf2; gauss_rom[107] = 8'hf2;
        gauss_rom[108] = 8'hf3; gauss_rom[109] = 8'hf3; gauss_rom[110] = 8'hf3; gauss_rom[111] = 8'hf3;
        gauss_rom[112] = 8'hf3; gauss_rom[113] = 8'hf4; gauss_rom[114] = 8'hf4; gauss_rom[115] = 8'hf4;
        gauss_rom[116] = 8'hf4; gauss_rom[117] = 8'hf5; gauss_rom[118] = 8'hf5; gauss_rom[119] = 8'hf5;
        gauss_rom[120] = 8'hf5; gauss_rom[121] = 8'hf6; gauss_rom[122] = 8'hf6; gauss_rom[123] = 8'hf6;
        gauss_rom[124] = 8'hf6; gauss_rom[125] = 8'hf6; gauss_rom[126] = 8'hf7; gauss_rom[127] = 8'hf7;
        gauss_rom[128] = 8'hf7; gauss_rom[129] = 8'hf7; gauss_rom[130] = 8'hf7; gauss_rom[131] = 8'hf8;
        gauss_rom[132] = 8'hf8; gauss_rom[133] = 8'hf8; gauss_rom[134] = 8'hf8; gauss_rom[135] = 8'hf8;
        gauss_rom[136] = 8'hf9; gauss_rom[137] = 8'hf9; gauss_rom[138] = 8'hf9; gauss_rom[139] = 8'hf9;
        gauss_rom[140] = 8'hf9; gauss_rom[141] = 8'hfa; gauss_rom[142] = 8'hfa; gauss_rom[143] = 8'hfa;
        gauss_rom[144] = 8'hfa; gauss_rom[145] = 8'hfa; gauss_rom[146] = 8'hfb; gauss_rom[147] = 8'hfb;
        gauss_rom[148] = 8'hfb; gauss_rom[149] = 8'hfb; gauss_rom[150] = 8'hfb; gauss_rom[151] = 8'hfc;
        gauss_rom[152] = 8'hfc; gauss_rom[153] = 8'hfc; gauss_rom[154] = 8'hfc; gauss_rom[155] = 8'hfc;
        gauss_rom[156] = 8'hfd; gauss_rom[157] = 8'hfd; gauss_rom[158] = 8'hfd; gauss_rom[159] = 8'hfd;
        gauss_rom[160] = 8'hfd; gauss_rom[161] = 8'hfe; gauss_rom[162] = 8'hfe; gauss_rom[163] = 8'hfe;
        gauss_rom[164] = 8'hfe; gauss_rom[165] = 8'hfe; gauss_rom[166] = 8'hfe; gauss_rom[167] = 8'hff;
        gauss_rom[168] = 8'hff; gauss_rom[169] = 8'hff; gauss_rom[170] = 8'hff; gauss_rom[171] = 8'hff;
        gauss_rom[172] = 8'h00; gauss_rom[173] = 8'h00; gauss_rom[174] = 8'h00; gauss_rom[175] = 8'h00;
        gauss_rom[176] = 8'h00; gauss_rom[177] = 8'h01; gauss_rom[178] = 8'h01; gauss_rom[179] = 8'h01;
        gauss_rom[180] = 8'h01; gauss_rom[181] = 8'h01; gauss_rom[182] = 8'h01; gauss_rom[183] = 8'h02;
        gauss_rom[184] = 8'h02; gauss_rom[185] = 8'h02; gauss_rom[186] = 8'h02; gauss_rom[187] = 8'h02;
        gauss_rom[188] = 8'h03; gauss_rom[189] = 8'h03; gauss_rom[190] = 8'h03; gauss_rom[191] = 8'h03;
        gauss_rom[192] = 8'h03; gauss_rom[193] = 8'h04; gauss_rom[194] = 8'h04; gauss_rom[195] = 8'h04;
        gauss_rom[196] = 8'h04; gauss_rom[197] = 8'h04; gauss_rom[198] = 8'h04; gauss_rom[199] = 8'h05;
        gauss_rom[200] = 8'h05; gauss_rom[201] = 8'h05; gauss_rom[202] = 8'h05; gauss_rom[203] = 8'h05;
        gauss_rom[204] = 8'h06; gauss_rom[205] = 8'h06; gauss_rom[206] = 8'h06; gauss_rom[207] = 8'h06;
        gauss_rom[208] = 8'h06; gauss_rom[209] = 8'h07; gauss_rom[210] = 8'h07; gauss_rom[211] = 8'h07;
        gauss_rom[212] = 8'h07; gauss_rom[213] = 8'h08; gauss_rom[214] = 8'h08; gauss_rom[215] = 8'h08;
        gauss_rom[216] = 8'h08; gauss_rom[217] = 8'h08; gauss_rom[218] = 8'h09; gauss_rom[219] = 8'h09;
        gauss_rom[220] = 8'h09; gauss_rom[221] = 8'h09; gauss_rom[222] = 8'h0a; gauss_rom[223] = 8'h0a;
        gauss_rom[224] = 8'h0a; gauss_rom[225] = 8'h0a; gauss_rom[226] = 8'h0a; gauss_rom[227] = 8'h0b;
        gauss_rom[228] = 8'h0b; gauss_rom[229] = 8'h0b; gauss_rom[230] = 8'h0c; gauss_rom[231] = 8'h0c;
        gauss_rom[232] = 8'h0c; gauss_rom[233] = 8'h0c; gauss_rom[234] = 8'h0d; gauss_rom[235] = 8'h0d;
        gauss_rom[236] = 8'h0d; gauss_rom[237] = 8'h0e; gauss_rom[238] = 8'h0e; gauss_rom[239] = 8'h0e;
        gauss_rom[240] = 8'h0f; gauss_rom[241] = 8'h0f; gauss_rom[242] = 8'h0f; gauss_rom[243] = 8'h10;
        gauss_rom[244] = 8'h10; gauss_rom[245] = 8'h11; gauss_rom[246] = 8'h11; gauss_rom[247] = 8'h11;
        gauss_rom[248] = 8'h12; gauss_rom[249] = 8'h13; gauss_rom[250] = 8'h13; gauss_rom[251] = 8'h14;
        gauss_rom[252] = 8'h15; gauss_rom[253] = 8'h16; gauss_rom[254] = 8'h17; gauss_rom[255] = 8'h1a;
    end

    // Sign from bit 31 (24 positions from magnitude bits [7:0]).
    // Magnitude from 256-entry ROM indexed by bits [7:0].
    assign noise_sign = lfsr[31];
    assign noise_mag  = gauss_rom[lfsr[7:0]];

`endif

endmodule
