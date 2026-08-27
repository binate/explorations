# Plan: IR optimization passes — gating infrastructure + bounds-check elimination (BCE)

> **Revised 2026-08-26 after a 3-lens adversarial review** (gating architecture,
> mem2reg-lite soundness, BCE soundness). The review materially reshaped the plan:
> it surfaced two large prerequisites the first draft omitted (no backend lowers
> `OP_PHI`; no dominator/dataflow infra), showed that "mem2reg-lite" conflated a
> safe/high-value part with a dangerous/low-value part, and showed the loop-BCE
> condition as first written was **unsound**. All claims below are grounded in the
> real tree (paths under the binate repo); the ground-truth section cites files.

## Motivation & strategic frame
Profiling bni running real programs (bnc compiling bnlint; bnlint linting packages)
shows the memory-access path dominates: `execMemoryOp` + `rt.BoundsCheck` ≈ 28% of
VM samples (vs ~12% in fib). Two distinct bounds-check costs, don't conflate them:
- **The VM's OWN native checks** (`code[pc]` per instruction; other VM-internal
  managed-slice reads). Biggest single chunk but NOT IR-BCE-eliminable (the
  pc-in-range invariant isn't compiler-provable). Tactical `unsafe_index` is the
  only lever there — DEFERRED / separate (user deprioritized it).
- **Compiled programs' checks** — every `s[i]` in every Binate program. Today
  `OP_BOUNDS_CHECK` lowers to an **opaque `call @rt.BoundsCheck`** (codegen
  emit_instr.bn), which LLVM does not fold **as currently declared** (external,
  unknown effects). So every native Binate program pays a call per access; the
  native backends (pkg/binate/native/*) have **no** optimizer at all; and the VM
  pays a `BC_BOUNDS_CHECK` per access. IR-level BCE removes the check once, before
  lowering, so **every consumer — LLVM backend, native backends, AND the bytecode
  VM — benefits from one IR change.** This is the "make the native backends a
  viable LLVM alternative" enabler.

> **Accuracy note (review lens 3):** do NOT claim "LLVM *cannot* eliminate the
> call." It can't fold it *as declared*; marking the decl `memory(none)`/
> `willreturn` (or inlining a compare + `@llvm.trap`) would let LLVM's own range
> analysis fold many checks. The correct, durable justification for IR-level BCE is
> that it *also* helps the VM and the native backends, which LLVM attributes can't.

> **Accuracy note (review lens 3):** `for _, v in s` emits **no** bounds check
> (`genForIn` owns the `0..len-1` index and lowers directly). So loop-BCE's payoff
> is confined to **explicit C-style index loops** (`for i := 0; i < len(s); i++ {
> s[i] }`). Confirm the perf micro `perf/005_slice_sum.bn` uses an explicit index,
> or it will show a zero BCE delta and misreport the win.

## What the review changed (read this before the phases)
1. **`OP_PHI` is dead IR — no compiled backend lowers it.** The opcode + `EmitPhi`
   exist, but LLVM codegen has no `OP_PHI` case (hits the `unimplemented IR op`
   panic), and x64/aarch64/arm32 dispatch have no case (fail-loud). Only the VM has
   phi-copy insertion, and it has **zero producers today** (grep: the only
   `EmitPhi` callers are the decl + a unit test) — so it is untested in anger and
   has latent critical-edge / parallel-copy bugs. **Consequence:** any pass that
   introduces a phi breaks LLVM + all three native backends immediately. Phi
   lowering is a *prerequisite project* (new **Phase P**), not a free consequence.
2. **No dominator/dataflow infrastructure exists.** No `Block.Preds`, no dominator
   tree, no dominance frontier anywhere in `pkg/binate/ir`. SSA promotion needs all
   three, built from scratch — and `pkg/binate/ir` is **BUILDER-compiled**, so the
   algorithm must stay in the BUILDER subset. New **Phase D**.
3. **"mem2reg-lite" conflated two very different things.** Promoting *managed*
   locals (`@T`) to SSA is dangerous (fault-pad hazard below) and low-value for
   BCE. Promoting *non-managed scalar* locals (`int` induction vars) is what BCE
   actually needs and sidesteps every refcount/fault-pad hazard. **Split:** Phase
   2a = scalar-only (the enabler); managed promotion (2b) deferred behind its own
   refcount-aware spec + review.
4. **The fault-pad hazard.** Recoverable-fault cleanup pads live on
   `Func.FaultPads` (NOT `Func.Blocks`, so compiled backends never see them) and
   emit `load slot.Ptr` + RefDec for every *managed* local live at a faulting op. A
   `Func.Blocks`-only promotion strands those loads → double-free on any recovered
   fault. Scoping promotion to non-managed scalars avoids this entirely (pads never
   reference `int` locals).
5. **The loop-BCE condition as first written was unsound.** `OP_BOUNDS_CHECK`
   guards BOTH bounds on a SIGNED `int` (`idx < 0 || idx >= len`; conformance 314
   deliberately faults on `-1`). `i < len(s)` proves only the upper bound. The
   tightened soundness spec is in Phase 3.
6. **Unbundle the cheap win.** Constant-index-into-constant-array BCE needs no phi,
   no dominators, no promotion — landable right after Phase 1 (new **Phase 1.5**).
   But "dominated-redundant" BCE is NOT cheap: today each `s[i]` re-loads `i` and
   re-extracts `len` as fresh SSA ids, so it needs value-numbering (which doesn't
   exist) — deferred.

## Design decisions (settled with the user, refined by review)
- **Gate = a bnc opt-level `-O<n>`**, DISTINCT from `--cflag -On`, but `-On`
  auto-implies `--cflag -On` when the backend is LLVM (one unified knob). Default
  `-O0` = no IR passes = today's behavior.
  - **Flag grammar (lens 1 M3):** intercept `-O0`/`-O1`/`-O2`/`-O3` in the args
    parser (today `-O2` falls to the else-branch and is treated as a *filename*).
    Scope to numeric levels first; reject/deferred: `-Os`/`-Oz`/`-Ofast`/bare `-O`.
  - **Precedence with `--cflag` (lens 1 M3):** an explicit `--cflag -O*` **wins**
    over the implied one (emit the implied `-On` *before* user `--cflag`s so clang's
    last-wins picks the user's). The IR `OptLevel` is set by `-On` **independently**
    of any `--cflag` override — document that `-O2 --cflag -O0` runs IR passes at 2
    but clang at 0 (a deliberate, documented combination, not a contradiction).
  - **Native backend semantics (lens 1 M3):** the LLVM `--cflag` implication is
    inert for native (clang is link-only there). On native, `-On` means "run IR
    passes at level n" — which is the *primary* value there (native has no other
    optimizer). Write this down; don't hand-wave it.
- **IR-level, shared, at a single chokepoint per lowering boundary (lens 1 B1/B2/B3):**
  - bnc: invoke `ir.RunOptPasses(mod, level)` at the **top of `compileModuleVia`**
    (compile.bn:266) — all 7 `GeneratePackage` paths (compile/library/main/test)
    route through it, for BOTH the LLVM and native backends. **Also** run it on the
    `--emit-llvm` bypass path (compile.bn:141, main.bn:342, test.bn:209 call
    `codegen.EmitModule` directly, skipping `compileModuleVia`) so `--emit-llvm -On`
    prints *optimized* IR. Passes must run on the **finalized** module — i.e. AFTER
    `EmitInitDispatcher`/`EmitMainEntry`/`EmitSatRegistryWiring` synthesis
    (main.bn:332-338), which happens post-`GeneratePackage`.
  - interpreter: invoke it in the interp lowering path (`interp.LoadProgram`
    lowers each finalized `@Module` to bytecode; interp.bn:195 dep loop + :230 main).
    `interp.LoadProgram(files)` takes no opt level today — thread one through (Interp
    field or signature change; multiple callers: cmd/bni run, cmd/bni --test, REPL).
- **Opt-on test mechanism (lens 1 M2 — MUST decide in Phase 1):** with default
  `-O0` and no CI mode opting in, the passes are dead code exercised by nothing and
  will bit-rot. Phase 1 establishes a way to turn opt ON for testing — at minimum a
  bnc/bni `-O` flag the unit tests drive, and a decision on whether to add an
  opt-on conformance mode. (Wiring a new CI *mode* is scope the user owns — propose,
  don't auto-wire. Same for flipping `scripts/build-bnc.sh`'s `--cflag -O2` release
  build to `-O2` — that's the highest-stakes first consumer; do NOT flip it silently.)
- **Pass-list structure (lens 1 M1):** BUILDER is now **bnc-0.0.14** (not 0.0.1 —
  CLAUDE.md is stale here), and the BUILDER-compiled tree already uses interfaces
  (`interface Backend` in compile.bn) and generics (`vec.Vec`, `slices.Append`). So
  an interface-based pass list IS expressible — but use a **plain hardcoded ordered
  sequence of function calls** inside `RunOptPasses` anyway (simplest, no
  interface/closure needed). Verify any new-to-BUILDER construct against the pinned
  0.0.14 BUILDER before relying on it.

## Ground-truth facts established by the review (with cites)
- Check semantics: `rt.BoundsCheck(index, length)` aborts if `index < 0 || index
  >= length` (impls/core/common/pkg/builtins/rt/rt_managed.bn:168); VM
  `BC_BOUNDS_CHECK` same test (vm/vm_exec_helpers.bn:285). `int` is signed
  (types/types.bn:94). Emitted by `EmitBoundsCheck(index, length)` (ir/ir_ops.bn:223)
  via ir/gen_access.bn. LLVM lowering = opaque `call void @rt.BoundsCheck` (
  codegen/emit_instr.bn:397). Faults pinned by conformance 309/310/311/314 (314 =
  negative index).
- Array len is compile-time (`EmitConstInt(collSt.ArrayLen,…)`, gen_access.bn:27);
  slice len is runtime (`EmitSliceLen` = extract field 1). ⇒ constant-index BCE
  works for **arrays**, not slices.
- `for _, v in s` emits no bounds check (genForIn, gen_flow.bn:108).
- `OP_PHI` unhandled by LLVM (emit_instr.bn default panic) + native dispatch; VM's
  phi-copy path (vm/lower_func_helpers.bn:77) is the only consumer, untested.
- Fault pads on `Func.FaultPads` not `Func.Blocks` (ir/ir.bn:106-138).
- Refcount is already lowered to explicit IR: managed store = acquire-new → load-old
  → RefDec-old → store-new (gen_store_slot.bn); scope-exit = load-slot → RefDec
  (gen_local_cleanup.bn). ⇒ a *correct reaching-def* promotion of a managed local
  is refcount-balanced and a phi is refcount-transparent (verified) — but a
  *textbook* pass violates this (see 2b invariants).
- Managed decls emit an explicit `EmitConstNil` + `EmitStore` init
  (gen_stmt.bn:420-453); that store is the entry reaching-def and must NOT be DCE'd.

## Phased plan (each phase independently landable + green)

### Phase 1 — pass infrastructure + `-On` gating (small, unblocks everything)
- Add bnc `-O0..-O3` parsing (args.bn / CLIArgs): sets `OptLevel`; LLVM backend
  implies `--cflag -On` (emitted before user `--cflag`s). Keep `--cflag` standalone.
- Add `ir.RunOptPasses(mod @Module, level int)` — a plain ordered sequence of pass
  function calls gated by level; initially just the observable verify/count pass.
- Slot it at the chokepoints above (compileModuleVia + emit-llvm bypass + interp
  lowering), on the FINALIZED module. Thread `level` into the interp lowering path.
- **Observable proof pass (lens 1 Mo1):** a no-op pass is untestable (a mis-wire
  passes identically). Ship a pass with an observable effect — either fold in the
  existing `ir.VerifyModule` (verify.bn; decide whether `--verify-ir` stays a
  separate knob) gated at `-O1+`, or a `debug.Log` op-count line the test asserts on.
- Add a bni/`-O` flag + default so the interpreter can opt in (decide bni's default
  — likely `-O0` to keep `int`-mode load cheap; revisit when BCE lands).
- Tests: `-O0` output byte-identical to today; `-O1` runs the pass (assert the
  observable effect); the LLVM `--cflag` implication + precedence; `-O2 --cflag -O0`
  documented combination.
- BUILDER: keep `RunOptPasses` + all `pkg/binate/ir` additions in the BUILDER subset.

### Phase 1.5 — constant-array-index BCE (cheap real win; no phi/dominators)
- Remove `OP_BOUNDS_CHECK(idx, len)` **iff** `idx` is an `OP_CONST_INT` c, `len` is
  a compile-time array length L, and `0 <= c < L`. **KEEP** in every other case —
  crucially KEEP when `c < 0` or `c >= L` (that's a real fault today: conformance
  310/311). This is a local peephole; no CFG/dominator/promotion machinery.
- Both backends + the VM skip the check automatically (one IR edit).
- Tests: removed where safe; **KEPT where unsafe**, incl. negative-const and
  over-len-const (assert the fault still fires); `-O0` leaves all checks in place.
- Value is narrow (constant indices into fixed arrays), but it lands the pass
  pipeline end-to-end with real, testable behavior and the KEEP-test discipline.

### Phase P — `OP_PHI` lowering in every backend (prerequisite for loops)
> Nothing below Phase 1.5 can emit a phi until this lands. This is the big
> unadmitted prerequisite.
- Implement `OP_PHI` lowering in LLVM codegen, x64, aarch64, arm32.
- Fix/verify the VM's existing phi-copy path: split critical edges, add
  parallel-copy (temp) sequencing for interdependent phis (or prove one-reg-per-SSA
  makes both moot), and add tests feeding hand-built diamond + loop-back-edge phi IR
  through **each** backend (LLVM, x64, aarch64, arm32, VM) — five lowering lanes.
- No promotion pass yet; this phase only proves the lowering.

### Phase D — dominator / dataflow infrastructure (prerequisite for promotion)
- Add `Block.Preds` (or compute predecessors), a dominator tree, and iterated
  dominance frontiers — hand-rolled in the BUILDER subset (no generic set/map/closure
  reliance). Verify BUILDER-compilability against the pinned 0.0.14 BUILDER on a
  skeleton *before* building it out.
- Tests: dominance on hand-built CFGs (diamond, loop, nested).

### Phase 2a — non-managed scalar SSA promotion (the real BCE enabler)
- Promote function-local **non-managed scalar** memory slots (int/bool/…, incl. the
  loop-header induction phi across the back-edge — the reducible-loop case, which is
  the hard-but-necessary SSA-construction case, NOT "single-block") to SSA + phis.
- **Escape analysis must be over-broad (lens 2 MODERATE):** pin the slot on ANY use
  of the alloca result that is not the address operand of a plain `OP_LOAD`/
  `OP_STORE` (GET_FIELD_PTR/GET_ELEM_PTR on the slot, call arg, bit_cast, address
  stored as a value, closure capture, …). Back it with an IR verifier assertion that
  no promoted alloca has surviving non-load/store uses.
- Scoping to non-managed scalars **sidesteps the entire refcount hazard family AND
  the fault-pad hazard** (pads only load managed locals; `int` locals carry no
  inc/dec). This is exactly and only what Phase 3 needs.
- Tests: semantics-preserving (unit + conformance across LLVM/native/VM); a
  size/quality check that induction vars are actually promoted.

### Phase 3 — loop-BCE (tightened soundness)
Remove `OP_BOUNDS_CHECK(idx, len)` for an induction-variable access **only** when
ALL hold (else KEEP):
1. **Upper bound:** the access's `idx` operand *is* (SSA-identical to) a loop-header
   phi `p`, and the loop guard is `p < L` on the path to the access.
2. **Lower bound:** `p >= 0` is provable — the induction init is a constant `>= 0`
   AND every step is monotonic non-decreasing. (The check guards `idx >= 0` too;
   `p < L` says nothing about sign, and `int` is signed. cf. conformance 314.)
3. **No signed overflow:** restrict to **unit stride** (step is literally `+1`, so
   `p` reaches exactly `L` and stops), OR prove the step cannot wrap. Arbitrary
   stride can overflow `p` to negative while `p < L` still holds.
4. **Same-SSA-value collection:** `L` in the guard and the collection at the access
   derive from the **same SSA value** (post-mem2reg: no other reaching def / no
   store to the slice slot between guard and use). "Equal length" is NOT enough
   (slice reassigned/aliased/shrunk in the body is unsound).
- Also the two non-loop cheap-ish cases live here or earlier: constant-index (Phase
  1.5, already landed) and — only if value-numbering is built — dominated-redundant.
- Tests: removed where all four hold; **KEPT** for each violated precondition —
  negative/underflowing index, non-unit stride, body-modified index (`s[i+1]`,
  `j=i*2; s[j]`), reassigned/aliased slice in the loop (all still fault when OOB).
  Verify across **all three lowerings** (LLVM, native, VM) — three test lanes.
  Perf: `perf/005_slice_sum.bn` (confirm explicit index) improves under the opt level.

### Deferred (behind their own specs + reviews)
- **2b — managed-local (`@T`/`@[]T`/`@Iface`/`@func`) promotion.** Feasible but
  NOT "lite": requires a refcount-AWARE pass honoring, as hard invariants — (R1)
  the init nil-store is a live def, never DCE'd; (R2) phis emit NO refcount op
  (adding RefInc/RefDec per edge → leak / double-free of a never-acquired value);
  (R3) no load-forwarding past the overwrite-release load; (R4) aggregate phis
  (multi-word `@[]T`/`@Iface`/`@func`) supported by every backend, placed in a CFG
  already split by RefDec null-guards. Plus the fault-pad rewrite (rewrite
  `Func.FaultPads` loads to the reaching SSA def at each faulting op — bespoke, not
  standard SSA). Low BCE value (BCE needs int locals). Defer behind its own review.
- **Dominated-redundant BCE.** Needs GVN/value-numbering (doesn't exist); each
  `s[i]` currently re-loads/re-extracts as fresh SSA ids so "same (idx,len)" never
  matches. Build value-numbering first, or a memory-aware same-slot/no-intervening-
  store recognizer — non-trivial; not the "cheap" case the first draft implied.

## Risks / open questions
- **Sequencing (the big open question for the user).** The marquee loop-BCE win now
  requires Phase P (phi lowering ×5 lanes) + Phase D (dominator infra) + Phase 2a
  (scalar promotion) before Phase 3 — a substantially larger build-out than the
  first draft implied. Phase 1 + Phase 1.5 (infra + constant-index BCE) are a
  self-contained, low-risk increment that lands the pipeline + gating + a real (if
  narrow) BCE with the full KEEP-test discipline, WITHOUT any of that. Recommend
  landing 1 + 1.5 first, then deciding whether to invest in P/D/2a/3 for the loop
  win. **User owns this call.**
- mem2reg refcount/fault-pad hazards — avoided by scoping 2a to non-managed scalars;
  re-open only if 2b is ever attempted (with R1-R4 + fault-pad rewrite).
- Whether bni runs passes by default (load-time cost vs execution win) — tune bni's
  default opt level separately once BCE exists.
- Each phase gets a focused adversarial review before landing (esp. P, 2a, 3).
- CLAUDE.md's "Builder Compatibility Constraint" still says BUILDER = bnc-0.0.1 with
  "no interfaces, no generics" — stale (actual = 0.0.14, both features in-tree).
  Flagged to the user; not edited unilaterally.
