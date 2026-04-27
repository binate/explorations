# Native AArch64 backend — known unit-test failures (2026-04-27)

Snapshot of where the native (Mach-O / aarch64) backend stands on its own
unit-test suite, plus a plan of action.

The native backend passes its own targeted unit tests
(`pkg/native/arm64/arm64_test.bn`, modulo the `bn_print_int` test which
was fixed in `4ccea12a`) and the conformance suite under
`boot-comp_native_aa64` (i.e. all conformance programs run end-to-end
against the native backend). Despite that, **running the existing
unit-test suite under `--backend native` reveals 10 failing packages**.
The failures are real backend bugs, not test-infrastructure issues.

CI is **not** wired up for `boot-comp_native_aa64` yet — the workflow
changes were prepared and then reverted because landing them now would
either bake in green-via-xfail (against the project's "don't optimize
for quick wins" rule) or leave CI permanently red on the new mode.

## Sweep snapshot

```
=== Summary (boot-comp_native_aa64): 19 passed, 10 failed, 0 xfail, 0 skipped ===
Failures: pkg/types pkg/asm/macho pkg/asm/parse pkg/asm/arm32 pkg/asm/aarch64
          pkg/asm/elf pkg/native/arm64 pkg/ir pkg/codegen pkg/vm
```

Re-runs of every failing package were done sequentially with
`/tmp/binate_test_*.o` cleaned between runs (parallel re-runs collide on
those filenames and produce bogus link-failure noise).

## Failure inventory

| # | Package | First-failing test / form | Cluster |
|---|---|---|---|
| 1 | `pkg/types` | crash on `TestTarget32StructLayout` | A (test-binary crash) |
| 2 | `pkg/asm/macho` | crash on `TestLoopSum` | A |
| 3 | `pkg/asm/parse` | crash on `TestParseMov` | A |
| 4 | `pkg/asm/aarch64` | crash on `TestAddReg64` | A |
| 5 | `pkg/native/arm64` | crash on `TestEmitConstNilAggregateZeroesAndPoints` | A |
| 6 | `pkg/codegen` | crash on `TestEmitMakeSlice` | A |
| 7 | `pkg/vm` | crash on `TestRepro_StructWithManagedSliceFieldAppend` (runtime OOB) | A |
| 8 | `pkg/asm/arm32` | 19 of 73 assertions fail | B (assertion failures) |
| 9 | `pkg/asm/elf` | 3 of 22 assertions fail | B |
| 10 | `pkg/ir` | clang link error: `ld: relocation … r_type=3, r_extern=0, r_pcrel=1, r_length=2 not supported` | C (backend reloc bug) |

A "test-binary crash" means the runner printed `=== RUN <Test>` and the
process died with no `--- PASS` / `--- FAIL` line and no further output —
i.e. the linked Mach-O test binary segfaulted (or similar) mid-test.
That points to bad codegen (instruction encoding, calling-convention,
or ABI/layout mismatch) for the patterns those particular tests
exercise.

A "runtime OOB" — pkg/vm's `TestRepro_StructWithManagedSliceFieldAppend`
— at least gave us a Binate-runtime panic ("index out of bounds: 0
(len 0)") before dying, which is more informative than a raw crash.

## Three clusters and what they probably mean

### Cluster C — pkg/ir: ARM64 reloc emission (1 package, clearest lead)

Linker reject:

    ld: relocation in '_bn_ir__lookupTypeAlias' is not supported:
        r_address=0xA3E8, r_type=3, r_extern=0, r_pcrel=1, r_length=2
        in '/tmp/binate_test_ir.o'

`r_type=3` for ARM64 is `ARM64_RELOC_PAGE21` (the `adrp` PC-relative-page
relocation). On Mach-O / aarch64, `ld64` only accepts `PAGE21` /
`PAGEOFF12` against **external** symbols (`r_extern=1`). What we're
emitting here is a section-relative PC-relative `adrp` (`r_extern=0`),
which is not a supported reloc form — the linker has no way to express
"PC-relative page of address-X-in-section-Y". The fix is to emit
PAGE21/PAGEOFF12 against the symbol entry rather than against the
section, even when the target is package-local.

The reason this only manifests in `pkg/ir` is presumably that pkg/ir is
the first package large enough (or with the right symbol layout) for
the `adrp` PC-pair encoding path to fire against a same-object local
symbol. Same bug likely lurks in any package big enough to need it.

Probable fix scope: small (a few lines in
`pkg/asm/macho` reloc emission), but needs care.

### Cluster A — Seven test-binary crashes (probably 1–3 shared root causes)

Crashing tests across pkg/types, pkg/asm/macho, pkg/asm/parse,
pkg/asm/aarch64, pkg/native/arm64, pkg/codegen, pkg/vm. The fact that
each of these tests passes under `boot-comp` (LLVM backend) means it's
the native backend's lowering that is wrong for the patterns those
tests use.

What we *don't* know yet is which patterns. The test names suggest
varied surfaces:

- `TestTarget32StructLayout` (pkg/types): no codegen, just layout
  computation — should pass everywhere. Crashing here strongly suggests
  the **test binary itself** (compiled via native backend) has a bad
  prologue / stack-layout / parameter-passing bug that hits in this
  particular call shape.
- `TestEmitMakeSlice` (pkg/codegen): exercises the IR layer's
  `EmitMakeSlice` plumbing — likely allocates and indexes a slice in
  the test, which probes the backend's slice/indexing codegen.
- `TestEmitConstNilAggregateZeroesAndPoints` (pkg/native/arm64):
  ironically a test of the native backend itself, run under the native
  backend.

Plan: reduce 1–2 of these to a minimal conformance program (see
"Conformance reproductions" below). Many will likely collapse to the
same root cause once reduced.

### Cluster B — Assertion failures in pkg/asm/arm32 + pkg/asm/elf

Both packages run their tests to completion but report wrong-byte
results:

- `pkg/asm/arm32`: 19 of 73 tests fail
- `pkg/asm/elf`: 3 of 22 tests fail

These are assembler-encoding tests that compare bytes to expected
literals. Likely a single backend bug (e.g. wrong constant lowering for
specific bit patterns, wrong byte order somewhere) is showing up in
multiple tests. Need to capture the verbose failure output (which test
expected what vs got what) to characterize.

## Plan of action

The cost model matters here: every native-backend re-test of a failing
package takes 1–7 min (compile via bootstrap-interpreted bnc + run).
A full sweep is ~30 min. Iteration cost is high, so the plan must
maximize information per run.

1. **Cluster C first** — pkg/ir reloc bug.
   - Read the offending `_bn_ir__lookupTypeAlias` symbol from the
     emitted `/tmp/binate_test_ir.o`; understand the byte at offset
     `0xA3E8` and what it's trying to address.
   - Check the Mach-O reloc emission in `pkg/asm/macho` against the
     ARM64 ABI doc — almost certainly we're using the section index
     instead of the symbol index for local targets.
   - Add a unit test that constructs a same-package local symbol
     reference forcing `adrp`/`add` and checks the emitted relocs are
     `PAGE21`/`PAGEOFF12` with `r_extern=1`.
   - Fix; re-run pkg/ir to confirm.

2. **Cluster A — reduce a representative crash to a conformance test.**
   Pick one with the cleanest reduction surface; `pkg/types`'s
   `TestTarget32StructLayout` is attractive because the test does no
   codegen of its own (so the bug must be in *our* codegen of the test,
   not in the test's logic). Write the reduction as a conformance
   program under `conformance/programs/` so the failure is reproducible
   without going through the unit-test runner. Reduce to a minimal
   form, fix, re-test.

3. **Cluster A — re-sweep after each fix.** Several tests likely share a
   root cause. After each Cluster-A fix, re-run the failing
   pkg-list with `-q` and re-tabulate.

4. **Cluster B — capture full failure detail.** Re-run pkg/asm/arm32
   and pkg/asm/elf verbosely, save the per-test mismatches, look for a
   pattern (one bit consistently wrong? one opcode family wrong?). May
   collapse to a single fix.

5. **Re-evaluate CI hookup once the dust settles.** When the failure
   count is in the low single digits (or zero), revisit
   `scripts/modesets/all` + the matrix-dispatch workflow changes that
   were reverted on 2026-04-27. The workflow patch handles the
   macos-vs-ubuntu matrix split correctly (gates `apt-get install
   clang` on Linux only); the only thing blocking it is real failures.

## Conformance reproductions — required for each fix

For every backend bug fixed in pursuit of this list, **add a
conformance test** that reproduces the failure pattern. Reasons:

- The unit-test sweep is slow (30 min) and packages bundle many tests.
  A conformance program is fast (sub-second) and isolates one issue.
- Conformance tests run on every backend; if we fix a bug only for
  native, the LLVM backend silently keeps working — but if we
  *introduce* a regression elsewhere, the conformance test catches it.
- The current 10-failure list is dominated by crashes whose actual
  root-cause patterns are unknown. Reducing each into a conformance
  program is exactly the work that turns "test crashed" into
  "instruction X with operand pattern Y is wrong."

Each fix landing in this area should commit the conformance test
*alongside* the fix (or before it, marked `.xfail.boot-comp_native_aa64`).

## Cross-references

- Bug-discovery protocol: `CLAUDE.md` § "Bug Discovery Protocol".
- IR/backend boundary: `explorations/ir-backend-guidelines.md`.
- Multi-backend cleanup: `explorations/ir-backend-cleanup-plan.md`.
