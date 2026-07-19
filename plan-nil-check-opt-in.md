# Design: opt-in nil-pointer-deref checking (Plan 2 nil-deref follow-up)

Status: **APPROVED (design), 2026-07-18.** The last of the six Plan 2 recoverable
faults ([`plan-rt-fault-cleanup-pads.md`](plan-rt-fault-cleanup-pads.md)). Unlike the
other five, nil-deref's guard is **never emitted** today — Binate is deliberately
C-like (a `@T`/`*T` deref compiles to a bare load/store; a nil deref is UB/SEGV). So
making it recoverable means *adding* the detection, which is a language-visible
choice. User decisions (2026-07-18):

1. **Optional** — nil-checking is opt-in per embedder (important for a REPL; a
   plain `bni` run may not want it). Truly zero-cost when off.
2. **All pointers** — check both `@T` (managed) and `*T` (raw) derefs when on. (An
   `@T`-only variant is defensible but not chosen; a REPL wants full coverage.)
3. **No signal-handler route** — the Go-style SIGSEGV-handler approach is too
   platform-specific; out of scope.

## Architecture (why VM-only is clean)

- The VM already lowers `OP_NIL_CHECK → BC_NIL_CHECK` (`vm/lower_instr.bn:292`); the
  handler currently does `println + rt.Exit` (a dead op — nothing emits `OP_NIL_CHECK`).
- **Compiled codegen does NOT handle `OP_NIL_CHECK`**, and there is no `rt.NilCheck`.
  So the guard is inherently VM-only — which is exactly what we want: nil-checking is a
  **VM/REPL recovery aid with zero compiled-backend cost**. Compiled stays C-like/fast.

## Design

**Opt-in at gen time.** A per-compilation `GenCtx.EmitNilChecks` flag, default
**off**. When on, IR-gen emits `OP_NIL_CHECK ptr` + `attachFaultPad` immediately before
each pointer deref (load/store/field/elem through a pointer), for ALL pointer kinds.
When off: nothing emitted — byte-for-byte current behavior (no ops, no pads, no bloat).

- **VM:** `BC_NIL_CHECK` → `setFault("runtime error: nil pointer dereference")` (was
  `println + rt.Exit`); recovers through the pad exactly like bounds/divide/shift (the
  existing `execManagedMemoryOp` consume point dispatches it).
- **Compiled:** `OP_NIL_CHECK` → **no-op** (skip). Compiled ignores the checks and stays
  C-like; if compiled nil-checks are ever wanted, that's a later add (`rt.NilCheck` +
  codegen), out of scope now. The no-op (rather than an unhandled-op error) keeps the IR
  robust even if a nil-check-gen'd module is compiled.
- **Message:** `runtime error: nil pointer dereference` (Go-aligned, specific-per-fault
  style like the other recoverable messages).

## Redundant-check elision (in N2 — user asked for it)

Don't emit `OP_NIL_CHECK` for a deref whose pointer is provably non-nil at that point:

- **(a) Non-nil by construction.** If the pointer operand's defining IR op yields a
  known-non-nil value — `OP_ALLOC` / `OP_BOX` / `make`-family, an address-of
  (`&local`/`&global`/`&field`/`&elem`), a composite-literal address, a string constant
  — skip the check. (These are the dominant case: `make(T).field` needs no check.)
- **(b) Dominating-check dedup.** A pointer is an SSA value (never reassigned), so once
  `OP_NIL_CHECK V` has run, every later deref of `V` is safe. Track the set of
  already-checked SSA value-ids within the current block (a per-block set — cheap and
  catches the common `p.x + p.y` / `p.a.b` intra-block redundancy) and skip repeats.
  Full cross-block dominator analysis is a possible later refinement; note the
  intra-block approximation in the code.

Elision is purely an optimization: correctness never depends on it (an un-elided check
of a non-nil pointer just passes).

## Increments

- **N1 ✅ LANDED (`49ad00ef`, 2026-07-18).** `BC_NIL_CHECK` handler → `setFault`
  (recoverable — was `println + rt.Exit`); the exec loop's managed-memory consume point
  dispatches it to the op's cleanup pad exactly like bounds/divide/shift.  Proven by a
  hand-built-pad VM unit test (`TestNilCheckFaultRecovers`: nil const 0 → OP_NIL_CHECK
  with a PadBlock → unwind → host FAULTED + message).  `EmitNilCheck` now returns the
  check `@Instr` (zero prior callers; `.bni` synced).  Inert for real programs — nothing
  emits `OP_NIL_CHECK` yet (that is N2), conformance unchanged.  Review CLEAN after one
  MINOR fix (the `.bni` signature/doc had not been updated to match the `.bn` — it slips
  past gen1 because the `.bni`⟷`.bn` agreement check does not cover methods).  The
  **compiled `OP_NIL_CHECK` → no-op arm moves to N2** (bundled with emission, so the
  compiled path is testable in the same increment).
- **N2a ✅ LANDED (`3cabb1c2`, 2026-07-18).** The gated-emission foundation +
  proving cut.  `GenCtx.EmitNilChecks` (default off); `emitNilCheckIfEnabled` /
  `isProvablyNonNil` (gen_local_cleanup.bn) — the flag gate + construction-non-nil
  elision (a) (skip when the pointer's op is `OP_ALLOC` / `OP_BOX` / `OP_MAKE` /
  `OP_MAKE_SLICE`; base-relative GEPs deliberately NOT elided — that's (b), N2b).
  Wired at the ONE deref site `genUnary`'s `*p` STAR as the proving cut.  Compiled
  no-op arms: LLVM (emit_instr.bn) + explicit aarch64/arm32 arms (arm32 needs it to
  dodge its fail-loud tail); x64 no-ops via its silent tail.  Unit tests: flag on ⇒
  padded `OP_NIL_CHECK` at `*p`; flag off ⇒ none; `*(&x)` still loads but elides;
  per-backend `OP_NIL_CHECK → 0 bytes`.  Review CLEAN (2 NITs folded: field-name
  doc-sync + a non-vacuous elision assertion).  Default-off ⇒ byte-identical IR, so
  conformance unchanged.
- **N2b — READY (validated, awaiting landing; field derefs).** Reconnaissance found
  the plan under-scoped this: field-deref lowering is DUPLICATED across
  `genSelector` (reads, ~13 pointer-follow GEP sites) and `genSelectorPtr`
  (writes/receivers/address-of, ~11 sites).  The unify path (route reads through
  `genSelectorPtr`) was **investigated and rejected**: the two functions are
  SEMANTICALLY divergent — a read materializes a value-struct base (`genExpr(*p)` →
  alloca → GEP) while a write must address it in place (`genExpr(p)` → GEP through
  the pointer; a materialized copy would drop the store).  `forDeref` doesn't
  distinguish read from write, so unifying would need a *new* read/write flag that
  ADDS complexity rather than removing duplication.  **User decision: grind — keep
  the two functions**, instrument each directly (the read/write duplication is a
  pre-existing structural choice, tracked as a separate refactor question, not this
  feature's job).  What landed:
  - `genSelectorPtr` gains a `forDeref` param, nil-checking each FOLLOWED pointer
    before its GEP (`emitDerefNilCheck`, gated on `GenCtx.EmitNilChecks`).  Deref
    callers pass `true`; address-of (`&p.field` via `genLValueAddr`, the
    method-receiver address) pass `false` so a nil base is NOT faulted (deref-only
    semantics — the user's other N2 decision).  The recursion passes `true` (a
    chained `p.a.b` loads `p.a` through to reach `.b`).  All 12 callers updated.
  - `genSelector`'s ~13 inline pointer-follow READ sites nil-check the followed
    pointer before the GEP (`emitNilCheckIfEnabled` — reads are always derefs).
    Value-struct / element / composite / borrow-copy bases follow no user pointer
    and stay unchecked.
  - Verified: ir unit tests (read/write emit a check; `&p.x` + value-struct emit
    none; flag-off none); **builder-comp conformance 2838/0** and
    **builder-comp-int 2819/0** (flag-off byte-identical — the genSelector rewrite
    is behavior-preserving); gen1 built.  Review CLEAN.
- **N2c (index derefs).** Instrument `genIndex`/`genIndexPtr` directly (same
  grind, keeping the read/write split), nil-checking the collection pointer before
  each element GEP.
- **N2d (intra-block dedup elision (b)).** A per-block already-checked SSA-id set
  so `p.x + p.y` / `p.a.b` emit ONE check for a repeated pointer; skip repeats.
- **N3 (embedder opt-in + end-to-end).** Plumb the flag through the pipeline
  (`interp` / `cmd/bni`): REPL → on; `bni <prog>` run → **off** by default with a
  `--check-nil` opt-in; `bni --test` → **on** (tests want safety). Conformance: a nil
  deref recovers under the VM with checks on (message + exit 1), xfail on compiled
  (SEGV, no message) — the 386/1105 pattern.

## Sub-decisions (proposed, confirm at N3)

- `bni <prog>` run: nil-checks **off** by default (`--check-nil` to enable) — keeps a
  plain run C-like/fast.
- `bni --test`: nil-checks **on** — a test suite wants host survival + a clear message.
- Elision: (a) construction-non-nil landed in N2a + (b) intra-block dedup in N2b;
  cross-block dominator elision deferred.

## Verification

Per increment: `./scripts/unittest/run.sh builder-comp ir codegen vm` for the touched
packages; `scripts/build-bnc.sh` (BUILDER-compilable — the flag/emit code lives in
`ir`/`codegen`, both in bnc's tree, so it must stay within the BUILDER subset);
conformance smoke on `builder-comp-int` (recover) + `builder-comp` (compiled no-op ⇒
C-like, unchanged). Default-off must be byte-identical to pre-feature on every mode.
