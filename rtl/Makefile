IVERILOG ?= iverilog
VVP      ?= vvp
YOSYS    ?= yosys
NEXTPNR  ?= nextpnr-ice40

SRC_DIR  = src
TB_DIR   = tb
VEC_DIR  = vectors
SYN_DIR  = synth
BUILD    = build

# ALU sources
ALU_SRCS = $(SRC_DIR)/lns8_pkg.v \
           $(SRC_DIR)/sat_clip.v \
           $(SRC_DIR)/priority_enc.v \
           $(SRC_DIR)/barrel_shifter.v \
           $(SRC_DIR)/phi_rom.v \
           $(SRC_DIR)/exp_coeff_rom.v \
           $(SRC_DIR)/log_frac_rom.v \
           $(SRC_DIR)/lns8_muldiv.v \
           $(SRC_DIR)/lns8_add.v \
           $(SRC_DIR)/lns8_exp.v \
           $(SRC_DIR)/lns8_ln.v \
           $(SRC_DIR)/lns8_alu.v

# PF sources (includes ALU)
PF_SRCS = $(ALU_SRCS) \
          $(SRC_DIR)/pf_ucode_rom.v \
          $(SRC_DIR)/pf_memory.v \
          $(SRC_DIR)/pf_sequencer.v \
          $(SRC_DIR)/pf_pos_predict.v \
          $(SRC_DIR)/pf_resampler.v \
          $(SRC_DIR)/lin_to_lns8.v \
          $(SRC_DIR)/pf_estimator.v \
          $(SRC_DIR)/lfsr_rng.v \
          $(SRC_DIR)/spi_slave.v \
          $(SRC_DIR)/pf_top.v

IVFLAGS = -g2005-sv -I$(SRC_DIR) -Wall

.PHONY: all sim sim_alu sim_muldiv sim_add sim_exp sim_ln \
        sim_rng sim_mem sim_seq sim_resamp sim_l2l sim_pf \
        vectors clean synth synth_pf

all: sim

$(BUILD):
	mkdir -p $(BUILD)

# --- Test vector generation ---
vectors:
	cd $(VEC_DIR) && python gen_test_vectors.py

# --- ALU per-module simulation ---
sim_muldiv: $(BUILD)
	$(IVERILOG) $(IVFLAGS) -o $(BUILD)/tb_muldiv.vvp $(TB_DIR)/tb_lns8_muldiv.v $(ALU_SRCS)
	$(VVP) $(BUILD)/tb_muldiv.vvp

sim_add: $(BUILD)
	$(IVERILOG) $(IVFLAGS) -o $(BUILD)/tb_add.vvp $(TB_DIR)/tb_lns8_add.v $(ALU_SRCS)
	$(VVP) $(BUILD)/tb_add.vvp

sim_exp: $(BUILD)
	$(IVERILOG) $(IVFLAGS) -o $(BUILD)/tb_exp.vvp $(TB_DIR)/tb_lns8_exp.v $(ALU_SRCS)
	$(VVP) $(BUILD)/tb_exp.vvp

sim_ln: $(BUILD)
	$(IVERILOG) $(IVFLAGS) -o $(BUILD)/tb_ln.vvp $(TB_DIR)/tb_lns8_ln.v $(ALU_SRCS)
	$(VVP) $(BUILD)/tb_ln.vvp

sim_alu: $(BUILD)
	$(IVERILOG) $(IVFLAGS) -o $(BUILD)/tb_alu.vvp $(TB_DIR)/tb_lns8_alu.v $(ALU_SRCS)
	$(VVP) $(BUILD)/tb_alu.vvp

# --- PF module simulation ---
sim_rng: $(BUILD)
	$(IVERILOG) $(IVFLAGS) -o $(BUILD)/tb_lfsr_rng.vvp $(TB_DIR)/tb_lfsr_rng.v $(SRC_DIR)/lfsr_rng.v $(SRC_DIR)/lns8_pkg.v
	$(VVP) $(BUILD)/tb_lfsr_rng.vvp

sim_mem: $(BUILD)
	$(IVERILOG) $(IVFLAGS) -o $(BUILD)/tb_pf_memory.vvp $(TB_DIR)/tb_pf_memory.v $(SRC_DIR)/pf_memory.v $(SRC_DIR)/lns8_pkg.v
	$(VVP) $(BUILD)/tb_pf_memory.vvp

sim_seq: $(BUILD)
	$(IVERILOG) $(IVFLAGS) -o $(BUILD)/tb_pf_sequencer.vvp $(TB_DIR)/tb_pf_sequencer.v $(PF_SRCS)
	$(VVP) $(BUILD)/tb_pf_sequencer.vvp

sim_resamp: $(BUILD)
	$(IVERILOG) $(IVFLAGS) -o $(BUILD)/tb_pf_resampler.vvp $(TB_DIR)/tb_pf_resampler.v $(PF_SRCS)
	$(VVP) $(BUILD)/tb_pf_resampler.vvp

sim_l2l: $(BUILD)
	$(IVERILOG) $(IVFLAGS) -o $(BUILD)/tb_lin_to_lns8.vvp $(TB_DIR)/tb_lin_to_lns8.v $(SRC_DIR)/lin_to_lns8.v $(SRC_DIR)/priority_enc.v $(SRC_DIR)/log_frac_rom.v $(SRC_DIR)/lns8_pkg.v
	$(VVP) $(BUILD)/tb_lin_to_lns8.vvp

sim_e2e: $(BUILD)
	$(IVERILOG) $(IVFLAGS) -o $(BUILD)/tb_pf_e2e.vvp $(TB_DIR)/tb_pf_e2e.v $(PF_SRCS)
	cd $(BUILD)/.. && $(VVP) $(BUILD)/tb_pf_e2e.vvp

# --- All simulation ---
sim: sim_muldiv sim_add sim_exp sim_ln sim_alu sim_rng sim_mem sim_seq sim_resamp

# --- Synthesis ---
synth: $(BUILD)
	$(YOSYS) -s $(SYN_DIR)/synth_ice40.ys
	$(NEXTPNR) --up5k --package sg48 --pcf $(SYN_DIR)/ice40.pcf \
		--json $(BUILD)/lns8_alu.json --asc $(BUILD)/lns8_alu.asc
	icetime -d up5k $(BUILD)/lns8_alu.asc

synth_pf: $(BUILD)
	$(YOSYS) -s $(SYN_DIR)/synth_pf_ice40.ys
	$(NEXTPNR) --up5k --package sg48 --pcf $(SYN_DIR)/pf_ice40.pcf \
		--json $(BUILD)/pf_top.json --asc $(BUILD)/pf_top.asc
	icetime -d up5k $(BUILD)/pf_top.asc

clean:
	rm -rf $(BUILD)
