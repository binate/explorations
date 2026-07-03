# Plan: run the bytecode VM correctly on a 32-bit host

**Status: active (2026-07-02).** Referenced by `pkg/binate/vm/vm.bn:9`,
`vm.bni`, `vm_exec64.bn` — this doc is the home for the "64-bit values on a
32-bit VM host" work. Detailed history + the Layer 1/2 changelog live in
`claude-todo.md` ("32-bit-host toolchain" section); this doc is the forward plan.

## Goal

Make the bytecode VM (`pkg/binate/vm`, `cmd/bni`) run correctly on a **32-bit
host** (host `int` == 4 bytes), so it interprets 32-bit-*target* bytecode — a
prerequisite for the C-free / 32-bit-hosted self-hosting goal.

## The unlock (how to REACH a 32-bit VM host, no compiler changes)

Cross-build `cmd/bni` itself to arm32-linux via the proven LLVM path and run it
under `qemu-arm`. Because `cmd/bni/main.bn` bakes in `ConfigForTarget("")` (its
own host config), a 32-bit `bni` automatically interprets 32-bit-target
bytecode. Both halves are already proven: `bnc --target arm32-linux`
cross-compiles full binaries that run under qemu (the arm32 unittest runner
already builds + runs `pkg/binate/vm`'s own tests green on real arm32), and the
`-int` runners already invoke `bni` on `.bn` files. CI already installs the
arm32 cross-toolchain + qemu-user.

## Current state (much is DONE + validated on real arm32)

- **Layer 1** (IR int64 constants host-independent): DONE.
- **Layer 2 — VM machine word**: the register==host-word + **register-pair
  model** for 64-bit values is LANDED and green on `builder-comp_arm32_linux`
  unit tests. `REG_SLOT = sizeof(int)`; a 64-bit value spans two adjacent slots
  on ILP32 (little-endian). `MANAGED_HDR` = 16/8 by host. Full `BC_*64` handler
  set (`vm_exec64.bn`: LOAD_IMM64, MOV64, WIDEN64_S/U, NARROW64, arith/cmp/shift,
  the float64 pair family), pair memory ops (`LOAD64_PAIR`/`STORE64_PAIR` via
  portable `*uint32` stride), int64 return + call/multi-return ABI. Float64
  arith/cmp/neg pairs done.

So the hard core — 64-bit values as register pairs — already works on a 32-bit
target. What remains is (A) one known conversion miscompile, (B) LP64 hardcodes,
(C) target-word-vs-register-word residue in memory-structure access, and the
infrastructure to observe them.

## Remaining work

### A. int64↔float CONVERSION casts — ✅ DONE & LANDED (`0a8507a1`, 2026-07-02)

The 8 conversion handlers paired the **float** operand (`REG_SLOT < 8`) but not
the **int** operand, so on ILP32 an int64/uint64 source (int→float) or dest
(float→int) lost its high 32 bits.  Fixed with the **distinct-opcode** design
(user-chosen over the leaner `REG_SLOT<8`-branch alternative, for host
testability): 8 pair-variant opcodes (`BC_SI64TOF`/`UI64TOF`/`SI64TOF32`/
`UI64TOF32`, `BC_FTOSI64`/`FTOUI64`/`F32TOSI64`/`F32TOUI64`) whose handlers
(`vm_exec_cast64.bn`) ALWAYS pair the int64 side; `lowerCast` selects them when
`is64BitScalar(int-operand) && REG_SLOT < 8`.  Coverage: 8 direct handler tests
(host-testable) + 8 REG_SLOT-aware lowering tests (4 signed + 4 unsigned) that
assert the pair variant on arm32.  Adversarially reviewed (0 bugs).  Verified on
the 64-bit host (vm 228, conformance 303); the arm32 selection path is gated by
CI's `builder-comp_arm32_linux` job (un-runnable on macOS — no qemu-arm).

### B. LP64 (8-byte) hardcodes in the VM

- **Easy (self-flagged):** `lookupShimVtable` extent `*8` (`vtable_inject.bn`) +
  `OP_IFACE_UPCAST` `offset*8` (`vm_exec_iface.bn`) → `* GetTarget().PointerSize`.
- **Medium/mechanical:** managed-slice header stride `32` → `4*ptrSize`;
  iface/func-value stack pushes `16` → `2*ptrSize`.
- **Leave (verify):** the `(sz+7)/8*8` retbuf rounding — that 8 is the i64-array
  shim ABI, not the target word.
- **Hard, separate workstream:** 64-bit-scalar-args-packed-as-2-slots in
  cross-mode dispatch. The shim ABI is a fixed 7-int-slot bank read one slot per
  positional arg; a packed i64 arg occupies two slots but is consumed as one →
  truncates + shifts every following arg. Needs a coordinated slot-stream →
  ABI-arg re-marshal across the three dispatchers.

### C. VM word-model — role-3 target-memory access

- **Medium:** non-pair `BC_LOAD64`/`BC_STORE64` (`vm_exec_helpers.bn`) use `*int`
  (4 bytes on ILP32) → route 64-bit-target loads/stores through the `*uint32`
  pair ops. The one unambiguous memory-path truncation.
- **Hard:** audit the ~42 `bit_cast(*int, hdrPtr)` target-structure reads
  (managed/refcount/slice headers, iface/fv/vtable slots, closure recs,
  multi-return packing). Most are actually fine on ILP32 (the target word == host
  word == 4), so `*int` matches the 32-bit-target layout; only genuinely-fixed-64
  fields truncate. The **red run tells you which** — this is why we don't
  speculatively audit first.

## Chosen sequencing (red-mode-first, per user 2026-07-02)

1. **Phase 1 — int64↔float conversion fix (A). ✅ DONE & LANDED (`0a8507a1`).**
2. **Phase 2 — `builder-comp_arm32_linux_int` conformance mode. ✅ DONE & LANDED
   (`7577e446` runner + `build_interp_arm32`; `19ad5047` CI wiring).** Cross-builds
   `cmd/bni` to arm32 (`GEN1_COMPILER --target arm32-linux`) and runs it under
   qemu, feeding each `.bn`; `ConfigForTarget("")` makes it interpret 32-bit-target
   bytecode.  Wired into the conformance CI matrix as **non-blocking**
   (`continue-on-error`, kept OUT of the shared `scripts/modesets/all`).
   UN-runnable on macOS (no arm cross-toolchain / qemu-user) — the CI
   `ubuntu-latest` job is the red-signal source.  **NEXT: read the first main-CI
   run's `builder-comp_arm32_linux_int` job log** for (a) whether `cmd/bni`
   cross-compiles to arm32 at all, and (b) the red failure list.
3. **Phase 3 — triage the red run. `println` PATH ROOT-CAUSED + FIXED
   (2026-07-03); THREE distinct ILP32 bugs, not the single one first hypothesized.
   Fixes staged on `temp-binate-2`/`work-2`, not yet landed.**
   - **Red run** (CI run 28634304798 / job 84924780859, and reproduced locally):
     `509 pass / 2106 fail`. `cmd/bni` cross-compiles to arm32 and runs — Phase 2
     infra fully validated. ~1471 failures are the same `index out of bounds: 0
     (len 0)`.
   - **Local debug env**: a Docker `linux/amd64` container `binate-arm32` (arm
     cross-toolchain + qemu-user). NB it is x86-emulated-on-Apple-Silicon, so the
     arm32 VM runs *triple*-emulated (qemu-arm inside x86-on-ARM) — fine for a
     single-program repro loop (`docker exec binate-arm32 bash /root/build.sh`
     rebuilds arm32 bni; then `qemu-arm-static /root/arm32_bni -I $(cat
     /root/ifaces) -L $(cat /root/impls) test.bn`), but a FULL conformance sweep is
     impractically slow locally — the definitive numbers come from CI (single
     qemu layer on native x86). (`docker rm -f binate-arm32` to clean up.)
   - **ROOT CAUSE — three separate LP64-word hardcodes, all on the `println`
     path** (disassembly-verified). The initial "coerced-aggregate slot marshaling
     in the VM dispatchers" hypothesis was WRONG — the VM dispatchers pass args one
     host-word per slot, which is already correct on ILP32; the by-address slice
     the VM hands the shim was fine. The real bugs:
     - **(A) MAJOR — cross-mode / func-value shim ABI.** `codegen`'s
       `isAggregateArg` was `SizeOf() > 8` (LP64 word). On ILP32 an 8-byte
       aggregate (2-word slice / func-value / iface-value) is `!(> 8)`, so the
       per-function `__shim` treated it as a scalar coerced-in-registers, while the
       VM cross-mode dispatcher AND the native caller (`emit_call_funcvalue`) pass
       it **by-address** — the shim then read the slice's `len` from a stale
       register. Smoking gun: the arm32 `__shim.bootstrap.Write` was a pure
       register-shuffle, whereas the x86-64 one *dereferences* the by-address ptr
       (`mov (%rdx),%rsi; mov 0x8(%rdx),%rdx`). Fix: new shared
       `types.IsAggregateArg` — KIND-gated (slice/mslice/struct/array/fv/iface)
       **and** `SizeOf > PointerSize`; `codegen` delegates. The kind gate is
       essential: an 8-byte *scalar* (int64/float64) must stay scalar, else the
       shim's `bitcast i64 %a to double` fails to compile.
     - **(B) slice field offset.** `vm/lower_memory.bn` `lowerGetFieldPtr` used
       `instr.Index * 8` for (managed-)slice fields → the `len` field landed at
       offset 8, past the 8-byte raw slice, so the slice materialized with `len 0`.
       Fix: `* GetTarget().PointerSize`.
     - **(C) VM runtime header/slot strides.** `vm_exec_helpers.bn` / `vm.bn`
       `* 32`/`+ 32` (4-word slice/@[]char headers), `vm_exec_funcref.bn` /
       `vm_exec_iface.bn` `+ 16` (2-word fv/iface slots), iface-upcast native
       vtable `offset * 8`, and `rt.Alloc(32)` (4-word closure rec) — all LP64
       literals. The 2nd+ string literal in a function got a null data ptr (the
       dropped `println` newline). Fix: `4*REG_SLOT` / `2*REG_SLOT` / `* PointerSize`.
   - **VALIDATION.** `println` (single/multi-string, int, mixed) + slices +
     capturing closures all work on arm32-VM. Subset `001` = 17/20 (was ~19%
     overall). New unit tests `types.TestIsAggregateArgKindGated` and
     `vm.TestGetFieldPtrSliceOffsetTargetAware` (both LP64+ILP32 via `SetTarget`);
     types/codegen/vm unit packages green on 64-bit. Bug-A native regression: the
     `builder-comp_arm32_linux` func-value/print/iface/closure surface = **99
     passed, 0 failed** (the shim-ABI change is a no-op on LP64 and self-consistent
     on ILP32). All three fixes are LP64 no-ops (thresholds/strides unchanged at
     word 8).
   - **REMAINING TAIL (out of scope for the marshaling fix).** With `println`
     working, deeper tests now reach new failures — e.g. `133_slice_elem_copy_rc`
     SEGFAULTs after printing `1 2 3` (a refcount / slice-elem-copy ILP32 bug,
     previously masked). So A/B/C are large progress but the mode is NOT fully
     green — it stays experimental/non-blocking until the tail is cleared; only
     then flip it to blocking in `conformance-tests.yml`.

Rationale: buckets B(hard) and C(hard) are information-gated — speculative until
the red run says what breaks. Getting the failing signal beats a blind audit
(the todo's own "settle the model before editing" concern is best answered by
data). Phase 1 lands the one thing we already know is wrong.

## Open decisions

- **Conversion-fill design fork (A):** ✅ RESOLVED — distinct host-testable
  opcodes (landed `0a8507a1`).
- **Phase 2 CI hookup:** ✅ RESOLVED — wired into conformance CI as a
  non-blocking (`continue-on-error`) mode (`19ad5047`); flip to blocking once
  Phase 3 gets it green.
- **Alt host:** arm32-linux is the cheapest first 32-bit VM host (proven
  toolchain). 32-bit x86 (i386) is a possible alternative if arm32/qemu proves
  fiddly — not yet scoped.
