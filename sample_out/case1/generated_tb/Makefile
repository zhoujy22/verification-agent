# Auto-generated Makefile for cocotb
SIM ?= verilator
TOPLEVEL_LANG ?= verilog
VERILOG_SOURCES += D:\111\tb_enviroment\public_dataset\case1\rtl\stream_dut.v
# prebuilt wrapper
VERILOG_SOURCES += D:\111\tb_enviroment\sample_out\case1\generated_tb\dut_inst.v
TOPLEVEL ?= tb_top
MODULE ?= tb_top

EXTRA_ARGS += --coverage --build -Wno-fatal

SEED ?= 12345
NUM_SEQ ?= 5000

export COCOTB_REDUCED_LOG_PRUNING ?= 1

include $(shell cocotb-config --makefiles)/Makefile.sim

run:
	echo "Use 'make SIM_ARGS+=--seed=$(SEED) NUM_SEQ=$(NUM_SEQ)' via run.sh"
