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
3. **Phase 3 (NEXT) — triage the CI red run.** Fix iteratively, data-driven: the
   easy LP64 `ptrSize` wins, the `32`/`16` push strides, `BC_LOAD64/STORE64`
   pairing, then the role-3 field audit and the argSlots re-marshal as they
   actually surface. As the mode goes green, flip it from experimental to
   blocking in `conformance-tests.yml`.

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
