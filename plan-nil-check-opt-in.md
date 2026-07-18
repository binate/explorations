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

**Opt-in at gen time.** A `GenContext` (→ `GenModule`) flag `emitNilChecks`, default
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

- **N1 (VM guard becomes recoverable — inert).** `BC_NIL_CHECK` handler →
  `setFault` (recoverable); attach-pad wiring proven by a hand-built-pad VM unit test
  (like the 2a/2b cross-frame tests). Compiled `OP_NIL_CHECK` → no-op arm. Nothing emits
  `OP_NIL_CHECK` yet, so this is inert for real programs (conformance unchanged).
- **N2 (IR-gen emission, gated + elision).** IR-gen emits `OP_NIL_CHECK` + pad before
  each pointer deref, behind `GenContext.emitNilChecks` (default off ⇒ zero change), with
  the (a)+(b) elision above. Unit tests: flag on ⇒ checks at un-elided derefs, elided at
  non-nil ones; flag off ⇒ no `OP_NIL_CHECK`.
- **N3 (embedder opt-in + end-to-end).** Plumb the flag through the pipeline
  (`interp` / `cmd/bni`): REPL → on; `bni <prog>` run → **off** by default with a
  `--check-nil` opt-in; `bni --test` → **on** (tests want safety). Conformance: a nil
  deref recovers under the VM with checks on (message + exit 1), xfail on compiled
  (SEGV, no message) — the 386/1105 pattern.

## Sub-decisions (proposed, confirm at N3)

- `bni <prog>` run: nil-checks **off** by default (`--check-nil` to enable) — keeps a
  plain run C-like/fast.
- `bni --test`: nil-checks **on** — a test suite wants host survival + a clear message.
- Elision: (a) construction-non-nil + (b) intra-block dedup in N2; cross-block dominator
  elision deferred.

## Verification

Per increment: `./scripts/unittest/run.sh builder-comp ir codegen vm` for the touched
packages; `scripts/build-bnc.sh` (BUILDER-compilable — the flag/emit code lives in
`ir`/`codegen`, both in bnc's tree, so it must stay within the BUILDER subset);
conformance smoke on `builder-comp-int` (recover) + `builder-comp` (compiled no-op ⇒
C-like, unchanged). Default-off must be byte-identical to pre-feature on every mode.
