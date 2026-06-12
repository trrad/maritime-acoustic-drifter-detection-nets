// PHI_PLUS[0..127] + PHI_MINUS[128..255] in single 256x8 ROM
// Inferred as EBR by Yosys; fallback to SB_RAM256x16 if needed
module phi_rom (
    input  wire        clk,
    input  wire [7:0]  addr,
    output reg  [7:0]  data
);
    reg [7:0] mem [0:255];

    initial begin
        // PHI_PLUS[0..127]
        mem[  0] = 8'h10; mem[  1] = 8'h10; mem[  2] = 8'h0f; mem[  3] = 8'h0f;
        mem[  4] = 8'h0e; mem[  5] = 8'h0e; mem[  6] = 8'h0d; mem[  7] = 8'h0d;
        mem[  8] = 8'h0c; mem[  9] = 8'h0c; mem[ 10] = 8'h0c; mem[ 11] = 8'h0b;
        mem[ 12] = 8'h0b; mem[ 13] = 8'h0a; mem[ 14] = 8'h0a; mem[ 15] = 8'h0a;
        mem[ 16] = 8'h09; mem[ 17] = 8'h09; mem[ 18] = 8'h09; mem[ 19] = 8'h08;
        mem[ 20] = 8'h08; mem[ 21] = 8'h08; mem[ 22] = 8'h08; mem[ 23] = 8'h07;
        mem[ 24] = 8'h07; mem[ 25] = 8'h07; mem[ 26] = 8'h06; mem[ 27] = 8'h06;
        mem[ 28] = 8'h06; mem[ 29] = 8'h06; mem[ 30] = 8'h06; mem[ 31] = 8'h05;
        mem[ 32] = 8'h05; mem[ 33] = 8'h05; mem[ 34] = 8'h05; mem[ 35] = 8'h05;
        mem[ 36] = 8'h04; mem[ 37] = 8'h04; mem[ 38] = 8'h04; mem[ 39] = 8'h04;
        mem[ 40] = 8'h04; mem[ 41] = 8'h04; mem[ 42] = 8'h03; mem[ 43] = 8'h03;
        mem[ 44] = 8'h03; mem[ 45] = 8'h03; mem[ 46] = 8'h03; mem[ 47] = 8'h03;
        mem[ 48] = 8'h03; mem[ 49] = 8'h03; mem[ 50] = 8'h03; mem[ 51] = 8'h02;
        mem[ 52] = 8'h02; mem[ 53] = 8'h02; mem[ 54] = 8'h02; mem[ 55] = 8'h02;
        mem[ 56] = 8'h02; mem[ 57] = 8'h02; mem[ 58] = 8'h02; mem[ 59] = 8'h02;
        mem[ 60] = 8'h02; mem[ 61] = 8'h02; mem[ 62] = 8'h02; mem[ 63] = 8'h01;
        mem[ 64] = 8'h01; mem[ 65] = 8'h01; mem[ 66] = 8'h01; mem[ 67] = 8'h01;
        mem[ 68] = 8'h01; mem[ 69] = 8'h01; mem[ 70] = 8'h01; mem[ 71] = 8'h01;
        mem[ 72] = 8'h01; mem[ 73] = 8'h01; mem[ 74] = 8'h01; mem[ 75] = 8'h01;
        mem[ 76] = 8'h01; mem[ 77] = 8'h01; mem[ 78] = 8'h01; mem[ 79] = 8'h01;
        mem[ 80] = 8'h01; mem[ 81] = 8'h01; mem[ 82] = 8'h01; mem[ 83] = 8'h01;
        mem[ 84] = 8'h01; mem[ 85] = 8'h01; mem[ 86] = 8'h01; mem[ 87] = 8'h01;
        mem[ 88] = 8'h01; mem[ 89] = 8'h00; mem[ 90] = 8'h00; mem[ 91] = 8'h00;
        mem[ 92] = 8'h00; mem[ 93] = 8'h00; mem[ 94] = 8'h00; mem[ 95] = 8'h00;
        mem[ 96] = 8'h00; mem[ 97] = 8'h00; mem[ 98] = 8'h00; mem[ 99] = 8'h00;
        mem[100] = 8'h00; mem[101] = 8'h00; mem[102] = 8'h00; mem[103] = 8'h00;
        mem[104] = 8'h00; mem[105] = 8'h00; mem[106] = 8'h00; mem[107] = 8'h00;
        mem[108] = 8'h00; mem[109] = 8'h00; mem[110] = 8'h00; mem[111] = 8'h00;
        mem[112] = 8'h00; mem[113] = 8'h00; mem[114] = 8'h00; mem[115] = 8'h00;
        mem[116] = 8'h00; mem[117] = 8'h00; mem[118] = 8'h00; mem[119] = 8'h00;
        mem[120] = 8'h00; mem[121] = 8'h00; mem[122] = 8'h00; mem[123] = 8'h00;
        mem[124] = 8'h00; mem[125] = 8'h00; mem[126] = 8'h00; mem[127] = 8'h00;

        // PHI_MINUS[0..127] at addresses 128..255
        mem[128] = 8'h80; mem[129] = 8'hb7; mem[130] = 8'hc7; mem[131] = 8'hcf;
        mem[132] = 8'hd6; mem[133] = 8'hda; mem[134] = 8'hde; mem[135] = 8'he1;
        mem[136] = 8'he4; mem[137] = 8'he6; mem[138] = 8'he8; mem[139] = 8'hea;
        mem[140] = 8'heb; mem[141] = 8'hed; mem[142] = 8'hee; mem[143] = 8'hef;
        mem[144] = 8'hf0; mem[145] = 8'hf1; mem[146] = 8'hf2; mem[147] = 8'hf3;
        mem[148] = 8'hf3; mem[149] = 8'hf4; mem[150] = 8'hf5; mem[151] = 8'hf5;
        mem[152] = 8'hf6; mem[153] = 8'hf6; mem[154] = 8'hf7; mem[155] = 8'hf7;
        mem[156] = 8'hf8; mem[157] = 8'hf8; mem[158] = 8'hf9; mem[159] = 8'hf9;
        mem[160] = 8'hf9; mem[161] = 8'hfa; mem[162] = 8'hfa; mem[163] = 8'hfa;
        mem[164] = 8'hfb; mem[165] = 8'hfb; mem[166] = 8'hfb; mem[167] = 8'hfb;
        mem[168] = 8'hfc; mem[169] = 8'hfc; mem[170] = 8'hfc; mem[171] = 8'hfc;
        mem[172] = 8'hfc; mem[173] = 8'hfc; mem[174] = 8'hfd; mem[175] = 8'hfd;
        mem[176] = 8'hfd; mem[177] = 8'hfd; mem[178] = 8'hfd; mem[179] = 8'hfd;
        mem[180] = 8'hfd; mem[181] = 8'hfe; mem[182] = 8'hfe; mem[183] = 8'hfe;
        mem[184] = 8'hfe; mem[185] = 8'hfe; mem[186] = 8'hfe; mem[187] = 8'hfe;
        mem[188] = 8'hfe; mem[189] = 8'hfe; mem[190] = 8'hfe; mem[191] = 8'hfe;
        mem[192] = 8'hff; mem[193] = 8'hff; mem[194] = 8'hff; mem[195] = 8'hff;
        mem[196] = 8'hff; mem[197] = 8'hff; mem[198] = 8'hff; mem[199] = 8'hff;
        mem[200] = 8'hff; mem[201] = 8'hff; mem[202] = 8'hff; mem[203] = 8'hff;
        mem[204] = 8'hff; mem[205] = 8'hff; mem[206] = 8'hff; mem[207] = 8'hff;
        mem[208] = 8'hff; mem[209] = 8'hff; mem[210] = 8'hff; mem[211] = 8'hff;
        mem[212] = 8'hff; mem[213] = 8'hff; mem[214] = 8'hff; mem[215] = 8'hff;
        mem[216] = 8'hff; mem[217] = 8'h00; mem[218] = 8'h00; mem[219] = 8'h00;
        mem[220] = 8'h00; mem[221] = 8'h00; mem[222] = 8'h00; mem[223] = 8'h00;
        mem[224] = 8'h00; mem[225] = 8'h00; mem[226] = 8'h00; mem[227] = 8'h00;
        mem[228] = 8'h00; mem[229] = 8'h00; mem[230] = 8'h00; mem[231] = 8'h00;
        mem[232] = 8'h00; mem[233] = 8'h00; mem[234] = 8'h00; mem[235] = 8'h00;
        mem[236] = 8'h00; mem[237] = 8'h00; mem[238] = 8'h00; mem[239] = 8'h00;
        mem[240] = 8'h00; mem[241] = 8'h00; mem[242] = 8'h00; mem[243] = 8'h00;
        mem[244] = 8'h00; mem[245] = 8'h00; mem[246] = 8'h00; mem[247] = 8'h00;
        mem[248] = 8'h00; mem[249] = 8'h00; mem[250] = 8'h00; mem[251] = 8'h00;
        mem[252] = 8'h00; mem[253] = 8'h00; mem[254] = 8'h00; mem[255] = 8'h00;
    end

    always @(posedge clk)
        data <= mem[addr];
endmodule
