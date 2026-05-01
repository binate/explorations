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

**Current** (post `1612221`):

```
=== Summary (boot-comp_native_aa64): 29 passed, 0 failed, 0 xfail, 0 skipped ===
```

Plus 285/285 conformance.

**Original snapshot** (2026-04-27, kept for context):

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

### Cluster A — RESOLVED (last fix: `1612221` imm12 overflow)

The remaining 8-package residual all collapsed to a single root cause:
`aarch64.Str/Ldr/Strb/Strh/Ldrb/Ldrh` silently masked the offset
immediate to 12 bits when it didn't fit the unsigned-imm12 form. Any
function with a frame > 32KB (or for sub-word ops, > 4KB) and a stored
value at one of those high offsets would store at the wrong address.
The auto-generated test runner in `cmd/bnc --test` builds a frame
proportional to the test count, so packages with many unit tests
(pkg/types, pkg/codegen, pkg/native/arm64 itself, etc.) all hit this.

Fix in `pkg/asm/aarch64/aarch64_arith.bn`: `emitLdrStr` and a new
`ldrStrSubWordEmit` helper detect overflow via
`LdrStrImmFitsUnsigned` and materialize the address into X17 (AAPCS
intra-call scratch) before emitting with offset 0. Tests in
`pkg/asm/aarch64/aarch64_arith_test.bn`.

Result: 29/29 unit-test packages pass under `boot-comp_native_aa64`,
plus 285/285 conformance. The original cluster-A pre-fixes
(macho, multi-return, X16 stack-arg-copy) below were all real bugs
and remain in place, but the dominant residual was this one
encoder-level bug masking what looked like ~8 distinct codegen
problems.

#### Original pre-fixes (still relevant, kept here for history)

**pkg/asm/macho — DONE** (`ca9f287` + `ac7be3f`). Conformance test
`332_struct_arg_forward_inserts` reduces the TestLoopSum failure to a
4-line shape: a function takes a >16-byte struct by value and forwards
it to another call that inserts extra register-args between the
received args and the struct (e.g.
`Add(a, sf, rd, rn, op) -> emitDPOp(a, sf, REG_OPC, IMM_OPC, rd, rn, op)`).

Root cause: in `emitCall`'s "aggregate goes entirely on stack" branch,
both `getOperand` (returning the source-pointer reg) and `scratchReg`
(picking a load temp) hand out X15 once `m.Next` exceeds 6 — because
`regPool(i)` saturates at X15. Fix: hardcode X16 (AAPCS intra-call
scratch, safe across ldr/str) for the load temp in this one call
site. Larger root cause — `regPool` saturation at X15 — tracked
separately.

**Multi-return tuples > 64 bytes** (`ae1b4c3` + `ab4bba7`).
Conformance test `335_multi_return_big_tuple` reduces the trigger to
an 8-line shape: any function returning two structs whose combined
size exceeds 64 bytes (the X0..X7 packing limit). The native backend
silently dropped bytes past word 8 with no fallback to the
AAPCS-style sret indirect path used for single >64-byte aggregate
returns. Fix: extend the sret path to multi-return tuples. New
helpers `FuncReturnsBigMultiReturn` / `CallReturnsBigMultiReturn`;
PlanFrame allocates SretSlotOff for these too; emitCall points X8
at the spill slot before BL and skips the register-collect after;
emitReturn writes through the loaded X8 at FieldOffset for each arg.

This was the proximate cause of pkg/asm/parse's TestParseMov silent
crash (`LexNext(l) (Lexer, Token)` returns 40+64 = 104 bytes), but
fixing it unmasked a downstream `index out of bounds: 0 (len 0)` —
the package still fails for an unrelated reason.

**Remaining cluster A** (was 8 packages, distinct-looking bugs — ALL
turned out to be the imm12 truncation bug above. Kept for the snapshot
of how it manifested across the unit-test suite):

| Package | First-failing test | Notes |
|---|---|---|
| pkg/types | `TestIntTypes` | Crash in `_bn_rt__RefInc` writing refcount to `0x2e2ebdb68` — r-- memory. Symptom of imm12 truncation: a STR at frame-relative offset > 32KB silently aliased, corrupting an unrelated managed-pointer's refptr. |
| pkg/asm/parse | `TestParseMov` | `index out of bounds: 0 (len 0)` — uninitialized-looking slice header. Same root cause: a STR landed at the wrong frame slot, leaving the slice's len field as zero. |
| pkg/asm/aarch64 | `TestResolveFixupsAdrOutOfRangeIsError` | Loop of 262144 NOPs; collateral from the same imm12 issue affecting unrelated frame slots. |
| pkg/asm/arm32 | `TestLdrshImm` | Cumulative-state crash. Same root cause: each test in the runner accumulates state at frame offsets that eventually cross the imm12 boundary. |
| pkg/native/arm64 | `TestEmitCallAggregateReturnSpillsAllWords` then `TestEmitCallStackOverflowAggregateArg` | The on-the-nose case: emitCall's bool slot at sp+0x1158 → STRB silently truncated to sp+0x158 → corrupted argTypes' refptr. |
| pkg/codegen | `TestEmitMakeSlice` | Same root cause. |
| pkg/vm | `TestRepro_StructWithManagedSliceFieldAppend` | Same root cause. |
| pkg/ir | `TestGenIndex*` | Same root cause. |

The "different-looking" symptoms across packages (RefInc into r--,
uninitialized slice header, runtime OOB) all bottomed out in a
silently-truncated STR/STRB target offset corrupting something
unrelated in the frame. Once `aarch64.Str/Strb/etc.` started
materializing the address into X17 for oversize offsets, every
package passed.

### Cluster B — Assertion failures in pkg/asm/arm32 + pkg/asm/elf — RESOLVED (`43ab7a3`)

All 22 cluster B failures came from a single root cause: native ARM64
codegen mishandled multi-return tuples whose fields are smaller than
an 8-byte word, e.g. `(uint32, uint32)`.

- `pkg/asm/elf`: 3/3 cluster B failures fixed → 22/22 passing on
  `boot-comp_native_aa64`.
- `pkg/asm/arm32`: 19/19 cluster B failures fixed (all `TestAdd*`,
  `TestSub*`, `TestAnd*`, `TestOrr*`, `TestEor*`, `TestBic*`,
  `TestMov*`, `TestMvn*`, `TestCmp*`, `TestTst*`, `TestTeq*`,
  `TestAdc*`, `TestSbc*`). The package as a whole still fails because
  of a pre-existing `TestLdrshImm` crash (cluster A material —
  unrelated to cluster B's encoding bugs and already on pristine main
  before this fix).

Two related code paths were broken:

1. Caller-side spill in `emitCall`'s return-collect (`pkg/native/arm64/
   arm64_ops.bn`): walked by 8-byte word (`SizeOf(tuple) / 8`), but
   the callee returns each value in its own X register. For
   `(uint32, uint32)` — total size 8 bytes / 1 word — the loop stored
   only X0; X1 was lost. Fix: walk by FIELD; aggregate fields keep
   their per-word spread, scalar fields take one X-register and use
   a sized store at `FieldOffset(tuple, fi)` so a uint32 doesn't
   clobber its 8-byte-cell neighbor.

2. `emitExtract` for sub-word fields: a 64-bit LDR at slot+4 read 8
   bytes from a slot only 8 bytes long. Fix: size-dispatch through
   `emitScalarLoad` (matches the existing pattern in `emitLoad`).

The dpEnc tests in pkg/asm/arm32 were the canonical caller — `dpEnc`
calls `encodeOperand2(op) → (uint32, uint32)`, which is exactly the
tuple shape that broke. pkg/asm/elf had a similar shape elsewhere.

Regression test: `pkg/native/arm64.TestEmitExtractMultiReturnUint32-
FieldUsesScalarLoad`.

### Separately discovered — cross-package by-value struct ABI mismatch (`337_cross_pkg_struct_arg`) — RESOLVED (`0e3f357`)

Surfaced while reducing the original cluster A pkg/asm/arm32 LDRSH
crash. Not the cause of that unit-test crash (unit tests build all
packages native), but a real native-backend bug.

Repro: a struct that doesn't fit fully in remaining arg registers,
passed by value across a package boundary. The 56-byte (3 ints +
@[]char) Operand-style struct in pkg/op + Encode(int, int, Operand)
demonstrates: 2 leading int args leave 6 reg slots (X2..X7), but op
needs 7 words. The conformance runner builds main with `-backend
native` and pkg/op via LLVM (per cmd/bnc/main.bn:194 — deps still
go through LLVM until the native backend self-hosts the runtime).

ABI disagreement:
- LLVM Encode prologue: split fill — reads op[0..5] from X2..X7,
  op[6] from stack[0]. This is LLVM's `byval` struct emission.
- Native main emitCall: writes op entirely to stack[0..48].
  `common.CallArgRegStart` returns -1 once `ngrn + w > 8`, so the
  caller-side code never fills X2..X7.

Result: Encode reads garbage from X2..X7 as op[0..5]. op.Kind ends
up = leftover in X2 at the call site (whatever the previous BL
returned in X2).

Conformance runner output pre-fix: native fails, boot/boot-comp pass.

Fix (`0e3f357`): support split passing across three call sites.
1. `common.CallArgRegStart` / `CallArgStackOff` / `CallStackBytes` —
   when an aggregate straddles, regStart returns the first reg AND
   stackOff returns the overflow start; both can be ≥ 0
   simultaneously. CallStackBytes only counts post-X7 words.
2. `arm64_ops.bn:emitCall` aggregate branch — when both regStart >= 0
   and overflow exists (stackOff >= 0), fill `8 - regStart` regs
   first via Ldr, then write the remaining `nWords - regWords` words
   to the stack via X16.
3. `arm64.bn` prologue aggregate branch — symmetric: store reg
   portion to data slot, copy overflow words from caller's stack-args
   area.

Tests updated in `pkg/native/common/common_test.bn`:
- Renamed `TestCallStackBytesAggregateOverflowsAtomically` to
  `…Split…`; the (@[]int, Entry5w) shape now reports
  regStart=4, stackOff=0, stackBytes=16.
- Added `TestCallArgRegStartFullyOnStackAfterRegsExhausted` to
  pin down the post-saturation case (Tup8w + int → int is stack-only).

Note: the bug requires the @[]char (managed-slice) field — pure-int
structs of the same total size pass. That's because LLVM's struct ABI
for managed-aware types differs from int-only structs: the managed
field forces a different by-value passing strategy (verified
empirically; haven't confirmed against LLVM IR).

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

4. ~~**Cluster B — capture full failure detail.**~~ — DONE (`43ab7a3`).
   Single root cause: multi-return tuples with sub-word fields. See
   the cluster B section above for the writeup.

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
