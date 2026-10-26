# PyUVM APB Slave Register Block

A 16 x 32-bit APB register block implemented in Verilog and verified with a
pyuvm/Cocotb testbench.

## Design overview

The slave has 15 reset-to-zero, read/write registers at byte addresses
`0x00`-`0x38`, and a read-only ID register at `0x3C` with value `0xA9B00001`.
Addresses `0x40`-`0xFF` are unmapped. `PSTRB` selects the byte lanes updated by
writes. An illegal access asserts `PSLVERR` with `PREADY`; writes to the ID
register and accesses to unmapped addresses do not modify the register file.
`WAIT_CYCLES` configures access wait states (default `1`).

See the [register-block design specification](documents/APB%20Slave%20Register%20Block%20Design%20Specification%20%281%29.pdf)
for the design requirements.

## Repository contents

| Path | Description |
| --- | --- |
| `rtl/apb_slave.v` | APB slave RTL. |
| `testbench/pyuvm_test.py` | Cocotb entry points, pyuvm components and tests, reference model, scoreboard, and functional coverage model. |
| `testbench/merge_coverage.py` | Merges coverage XML inputs and prints the aggregate and any uncovered bins. |
| `Makefile` | Simulation, directed-test selection, repeated regression, and coverage merge targets. |
| `documents/` | Design specification PDF. |
| `build/all-tests/results.xml` | Saved Cocotb result summary for the full six-test run. |
| `build/coverage/merged.xml` | Checked-in aggregate functional-coverage report. |
| `.gitignore` | Excludes simulator databases, logs, Python caches, local editor settings, and generated build output while allowing the saved test and coverage reports to be tracked. |

Individual simulator outputs and per-run coverage XML files are generated under
`build/` but ignored by Git. The full-suite Cocotb summary and merged coverage
report are retained so both test and coverage results are available in the
repository.

## Testbench architecture

The testbench is implemented in `testbench/pyuvm_test.py` and uses Cocotb to
run the simulator and pyuvm to organize the verification components.

1. **Transaction items and sequences.** `apb_item_base` represents an APB
   operation (address, data, direction, and byte strobe). Driver items add
   randomized pre/post delays. Sequences generate random traffic, each byte
   strobe value, illegal accesses, a write to the read-only ID register, or
   all-ones data.
2. **Agent and driver.** The agent connects a sequencer, driver, and monitor.
   The driver drives setup and access phases, holds the transfer until
   `PREADY`, and inserts the requested idle delays between transfers. Reset
   cancels an in-flight driver operation and returns the bus signals to idle.
3. **Monitor.** The monitor observes the bus, records transaction fields and
   idle gap length, waits for `PREADY`, then captures `PSLVERR` and read data.
   It publishes the request to the reference model before completion and the
   completed transaction to the scoreboard and coverage collector.
4. **Register model and reference model.** The UVM register block models
   `REG_0`-`REG_14` as read/write and the ID register as read-only. The APB
   adapter translates between register operations and bus items. The
   reference model predicts whether an access should error, returns mirrored
   read data, and applies `PSTRB` byte masks when predicting writes.
5. **Scoreboard.** Expected responses and read data from the model are
   compared with the completed monitor transaction. The expected-value FIFOs
   are flushed when reset occurs.
6. **Coverage collector.** Completed transactions are sampled for functional
   coverage. Reset coverage is sampled when reset handling occurs.
7. **Test and simulator setup.** Each Cocotb test initializes the bus and
   reset, starts a 10 ns clock, configures the pyuvm environment, runs the
   selected pyuvm test, and optionally exports coverage XML when
   `APB_COVERAGE_FILE` is set.

### Test cases

The Makefile's `TESTS` list is the authoritative list of Cocotb tests run by
the coverage target.

| Cocotb test | pyuvm test / sequence | Stimulus |
| --- | --- | --- |
| `test_random` | `apb_test_base` / `apb_sequence_random` | 5,000 randomized APB operations per test invocation. Each operation randomizes aligned address, direction, write data, byte strobe, and pre/post delay. |
| `test_pstrb` | `apb_test_pstrb` / `apb_sequence_pstrb` | Seeds `REG_0`, then writes and reads it with every 4-bit `PSTRB` value from `0x0` through `0xF`. Checks masked updates and preservation of unselected bytes through the scoreboard. |
| `test_illegal` | `apb_test_illegal` / `apb_sequence_illegal` | 30 randomized reads or writes to addresses `0x40`-`0xFF`, exercising unmapped-access error responses. |
| `test_ro` | `apb_test_ro` / `apb_sequence_ro` | Attempts a write to the read-only ID register at `0x3C`. |
| `test_reset_on_going` | `apb_test_reset_ongoing` / random sequence | Starts a random transfer, asserts reset while the bus is active, releases reset, then performs 10 more random operations. |
| `test_all_ones` | `apb_test_all_ones` / `apb_sequence_all_ones` | Writes `0xFFFF_FFFF` to all 15 read/write registers, then reads those registers and the ID register. |

The directed tests replace the base test's random sequence through the pyuvm
factory. The reset test uses its own run phase to interrupt a transfer.

### Reset handling

Reset is active low (`presetn`). The testbench coordinates reset handling
through the `reset_handler` mixin, which gives each participating component a
`handle_reset()` callback. The agent watches for the falling edge of
`presetn`; when reset asserts, it calls the callback on each child that
implements the mixin, then waits for reset to be released before continuing.
The driver and monitor also wait for reset release before starting their
background transaction loops.

On reset assertion, the components perform the following actions:

- **Driver:** cancels the active transaction-driving task if one is running,
  marks the interrupted sequencer item done, and drives `psel`, `penable`,
  `paddr`, `pwrite`, `pwdata`, and `pstrb` to idle values. This prevents a
  partially driven APB transfer from continuing after reset.
- **Monitor:** cancels its active collection task so that a transfer
  interrupted before `PREADY` is not published as a completed transaction.
  Collection restarts after reset is released.
- **Reference model:** performs a hard reset of the UVM register model, which
  restores the mirrored register state to its reset values.
- **Scoreboard:** flushes the expected-response and expected-read-data FIFOs
  so predictions queued for an interrupted transfer cannot be compared with
  later traffic.
- **Coverage collector:** samples the value of `psel` when reset handling
  occurs, distinguishing reset with an active bus select from reset while
  idle.

The `test_reset_on_going` test starts a random sequence, waits for `psel` to
assert, then asserts `presetn` during the transfer. After a clock edge it
releases reset and submits ten more random sequence items. This exercises
interruption cleanup and post-reset traffic through the same monitor,
reference-model, and scoreboard paths as normal transactions.

## Functional coverage and results

The coverage model samples operation direction, response, address/register
class, `PSTRB`, data corner values, idle gap, selected crosses, and reset
context. The checked-in [merged coverage report](build/coverage/merged.xml)
reports **341 of 341 bins covered (100.0%)**:

| Coverage group | Covered / total | What it measures |
| --- | ---: | --- |
| `dir` | 2 / 2 | Read and write operations. |
| `slverr` | 2 / 2 | Successful and error responses. |
| `register` | 16 / 16 | Aligned register addresses `REG_0`-`REG_15` (including the ID slot). |
| `reg_right` | 3 / 3 | Read/write, read-only, and unmapped address classes. |
| `pstrb` | 15 / 15 | Strobe values `0x0`-`0xE` in the coverpoint. |
| `data_corners` | 3 / 3 | All-zero, all-one, and other data values. |
| `gap` | 2 / 2 | Back-to-back and gapped transactions. |
| `dir_x_reg_right` | 6 / 6 | Direction crossed with register access class. |
| `valid_reg_x_dir` | 31 / 31 | Register address crossed with direction; writing the ID slot is excluded. |
| `pstrb_on_write` | 240 / 240 | Strobe and register combinations for writes. |
| `pstrb_on_read` | 15 / 15 | Strobe values observed on reads. |
| `dir_x_slverr` | 4 / 4 | Direction crossed with response status. |
| `reset_coverage` | 2 / 2 | Reset asserted with the bus active or idle. |

The strobe coverpoint intentionally records only bins `0x0`-`0xE` as currently
defined in the testbench; the directed test still exercises `0xF`, but it has
no separate strobe coverage bin.

This is **functional bin coverage**, not RTL code coverage or a complete set of
APB protocol assertions. In particular, the monitor captures read data when
`PREADY` is high; the current testbench does not separately assert that
`PRDATA` is zero during setup or wait states. The 100% figure should not be
interpreted as verification of that timing property.

The committed merged report represents five regression runs for each of the
six test cases (30 per-test run reports). It is a saved report snapshot; rerun
the regression to generate fresh results for a new RTL or testbench revision.

The saved [full-suite Cocotb results](build/all-tests/results.xml) report six
tests, zero failures, and zero errors. This is a separate saved simulation
result from the merged functional-coverage report.

## Requirements

- Python with Cocotb, pyuvm, and cocotb-coverage installed.
- GNU Make and QuestaSim. `SIM` defaults to `questa-compat`.
- `cocotb-config` available on `PATH`.

## Running tests and coverage

Run all six tests once in one simulator session:

```sh
make run GUI=0
```

Run a single test (valid names: `test_random`, `test_pstrb`, `test_illegal`,
`test_ro`, `test_reset_on_going`, and `test_all_ones`):

```sh
make run GUI=0 APB_TESTCASE=test_ro
```

The make target's default for `GUI` is `1`; set `GUI=0` for a non-GUI run.
`WAVES` defaults to `1`. Simulation results are written under `build/`.

Run the repeated regression and merge coverage:

```sh
make coverage RUNS=5
```

The `coverage` target runs each test separately with GUI disabled, assigns a
different seed to each invocation, exports one XML report per run, and merges
them into `build/coverage/merged.xml`. `RUNS` defaults to `5`; set `SEED` to a
non-negative integer to choose the base seed, for example:

```sh
make coverage RUNS=3 SEED=12345
```

The coverage target deletes the prior `build/coverage/runs/` directory and
`build/coverage/merged.xml` before generating replacement results. `make clean`
removes the entire `build/` directory, including the tracked merged report.
The top-level Makefile's default target is `coverage`.
