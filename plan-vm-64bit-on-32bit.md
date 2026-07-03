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

### A. Known miscompile: int64↔float CONVERSION casts (`vm_exec_cast.bn`)

The 8 conversion handlers are pair-aware on the **float** operand (`REG_SLOT < 8`
branches) but **not the int** operand. On ILP32:
- int→float **source** side (`SITOF`/`UITOF`/`SITOF32`/`UITOF32`): the int64/uint64
  source is read as a single slot → high half dropped.
- float→int **dest** side (`FTOSI`/`FTOUI`/`F32TOSI`/`F32TOUI`): the int64/uint64
  dest is written as a single slot (`cast(int, f)`) → truncated + stale high slot.

`lowerCast`'s int→float and float→int arms have no `is64BitScalar` gate, so they
emit the single-slot op regardless of int-operand width.

**DESIGN FORK (needs a decision — see below):** how to represent the int64 side.
1. **Distinct always-pair opcodes** (8 new: the int64 variants), handler always
   pairs the int side — matches the `execOp64` convention, **host-testable on a
   64-bit host** via direct handler calls. Doubles the conversion opcode count.
2. **Reuse the 8 opcodes + `REG_SLOT < 8` branch** on the int side (needs the
   handler to know the int operand is 64-bit — via a spare-field flag), matching
   the existing float-side style. Leaner (no new opcodes) but the pair path is
   `REG_SLOT<8`-dead on a 64-bit host, so **not unit-testable off arm32** — the
   arm32 VM-host mode (Phase 2) would be its only test.

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

1. **Phase 1 — land the int64↔float conversion fix (A).** The one known
   miscompile; must land before/with the mode so the mode doesn't report it as
   noise. (Design fork above pending.)
2. **Phase 2 — stand up `builder-comp_arm32_linux_int`** (or similar): a
   conformance runner that cross-builds `cmd/bni` to arm32 via LLVM and runs it
   under qemu, feeding each `.bn`. Add the runner; **CI-matrix hookup is a
   separate decision to raise with the user** (adding the mode ≠ wiring it into
   the CI matrix). Expect the first full run to be RED beyond the conversion cast.
3. **Phase 3 — triage the red run.** Fix iteratively, data-driven: the easy LP64
   `ptrSize` wins, the `32`/`16` push strides, `BC_LOAD64/STORE64` pairing, then
   the role-3 field audit and the argSlots re-marshal as they actually surface.

Rationale: buckets B(hard) and C(hard) are information-gated — speculative until
the red run says what breaks. Getting the failing signal beats a blind audit
(the todo's own "settle the model before editing" concern is best answered by
data). Phase 1 lands the one thing we already know is wrong.

## Open decisions

- **Conversion-fill design fork (A):** distinct host-testable opcodes vs leaner
  `REG_SLOT<8` branch. (Blocking Phase 1.)
- **Phase 2 CI hookup:** add the mode to the CI matrix, or keep it a
  locally-runnable mode for now.
- **Alt host:** arm32-linux is the cheapest first 32-bit VM host (proven
  toolchain). 32-bit x86 (i386) is a possible alternative if arm32/qemu proves
  fiddly — not yet scoped.
