SIM ?= questa-compat
TOPLEVEL_LANG = verilog
COCOTB_TOPLEVEL = apb_slave
COCOTB_TEST_MODULES = testbench.pyuvm_test
VERILOG_SOURCES = rtl/apb_slave.v

GUI ?= 1
WAVES ?= 1
APB_TESTCASE ?=
BUILD_DIR = build
COVERAGE_DIR = $(BUILD_DIR)/coverage
TESTS = test_random test_pstrb test_illegal test_ro test_reset_on_going test_all_ones
MERGED_COVERAGE = $(COVERAGE_DIR)/merged.xml
COVERAGE_RUN_DIR = $(COVERAGE_DIR)/runs
RUNS ?= 5
SEED ?= $(shell $(PYTHON_BIN) -c "import secrets; print(secrets.randbelow(1000000000) + 1)")

# Add recursive RTL waves without starting the simulation before Cocotb is ready.
SIM_ARGS += -no_autoacc -do {add wave -r /*; log -r /*}
CUSTOM_SIM_DEPS += Makefile

export APB_COVERAGE_FILE

.DEFAULT_GOAL := coverage
.PHONY: run test coverage

# Run all tests in one Questa session, or select one with APB_TESTCASE=test_ro.
run:
	$(MAKE) sim SIM=$(SIM) GUI=$(GUI) WAVES=$(WAVES) \
		TESTCASE= COCOTB_TESTCASE= \
		COCOTB_TEST_FILTER=$(APB_TESTCASE) \
		SIM_BUILD=$(BUILD_DIR)/$(if $(APB_TESTCASE),$(APB_TESTCASE),all-tests) \
		COCOTB_RESULTS_FILE=$(BUILD_DIR)/$(if $(APB_TESTCASE),$(APB_TESTCASE),all-tests)/results.xml

test: run

# Run every test RUNS times with distinct seeds and merge all coverage results.
coverage:
	@set -e; \
	case "$(RUNS)" in ''|*[!0-9]*|0) echo "RUNS must be a positive integer" >&2; exit 2 ;; esac; \
	case "$(SEED)" in ''|*[!0-9]*) echo "SEED must be a non-negative integer" >&2; exit 2 ;; esac; \
	$(RM) -r $(COVERAGE_RUN_DIR); \
	$(RM) $(MERGED_COVERAGE); \
	echo "Regression base seed: $(SEED)"; \
	coverage_files=""; run_index=0; \
	for testcase in $(TESTS); do \
		iteration=1; \
		while [ $$iteration -le $(RUNS) ]; do \
			seed=$$(( $(SEED) + run_index )); \
			coverage_file=$(COVERAGE_RUN_DIR)/$${testcase}_run$${iteration}.xml; \
			echo "Running $$testcase ($$iteration/$(RUNS), seed=$$seed)"; \
			$(MAKE) --no-print-directory run GUI=0 APB_TESTCASE=$$testcase \
				COCOTB_RANDOM_SEED=$$seed APB_COVERAGE_FILE=$$coverage_file; \
			coverage_files="$$coverage_files $$coverage_file"; \
			iteration=$$((iteration + 1)); \
			run_index=$$((run_index + 1)); \
		done; \
	done; \
	$(PYTHON_BIN) testbench/merge_coverage.py $(MERGED_COVERAGE) $$coverage_files

clean::
	$(RM) -r $(BUILD_DIR)

include $(shell cocotb-config --makefiles)/Makefile.sim
