# PyUVM-APB_slave-environment

A 16 x 32-bit APB register block implemented in Verilog and verified with a
pyuvm/Cocotb testbench.

## Project contents

- `rtl/apb_slave.v` — APB slave RTL, including byte-strobe writes, a read-only
  ID register, error responses, and configurable wait states.
- `testbench/pyuvm_test.py` — pyuvm sequences, tests, and functional coverage.
- `testbench/merge_coverage.py` — merges per-run Cocotb coverage XML reports.
- `documents/` — register-block design specification.
- `Makefile` — simulation and coverage regression targets.
- `build/coverage/merged.xml` — aggregate functional coverage report. Other
  simulator outputs and per-run artifacts under `build/` are ignored.

## Requirements

- Python with Cocotb, pyuvm, and cocotb-coverage installed.
- GNU Make and QuestaSim (the Makefile defaults to `questa-compat`).
- `cocotb-config` available on `PATH`.

## Run the tests

Run the full test suite:

```sh
make run GUI=0
```

Run one test, for example:

```sh
make run GUI=0 APB_TESTCASE=test_ro
```

The available test cases are `test_random`, `test_pstrb`, `test_illegal`,
`test_ro`, `test_reset_on_going`, and `test_all_ones`.

Run the repeated regression and merge its coverage results:

```sh
make coverage RUNS=5
```

The regression writes the merged report to `build/coverage/merged.xml`.
