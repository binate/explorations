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

> **STATUS 2026-08-27 — Phase 1 + Phase 1.5 LANDED (bnc path).** Commits
> `036d06d5f` (the `-O0..-O3` flag) + `2b8de2bd8` (RunOptPasses runner +
> constant-index BCE pass + bnc wiring at compileModuleVia and the 3 --emit-llvm
> bypasses).  Validated: gen1 builds; ir 696 + cmd/bnc 135 unit tests green (6 BCE
> + 4 flag new); `--emit-llvm -O1` drops const-in-range array checks and KEEPS the
> runtime-index check; hygiene 20/20; a focused adversarial code review returned
> SAFE TO LAND.
>
> **STATUS 2026-08-27 — Phase-1 chokepoint now complete on BOTH paths.** Commit
> `664b51522` threads RunOptPasses into the interpreter's lowering path:
> `vm.LowerModule` runs `ir.RunOptPasses(m, vm.OptLevel)` on each finalized module
> before bytecode lowering (covers interp run path, REPL, --test), plumbed via a
> `VM.OptLevel` field + `Interp.SetOptLevel` + a cmd/bni `-O <n>` flag (run path;
> --test/REPL stay at 0).  So constant-index BCE now reaches interpreted programs
> under -O1+ too.  Adversarial review returned SAFE TO LAND; the fault-pad
> orphaning risk was verified safe (a removed check's pad is dead bytecode — the VM
> FaultTable is built from the post-BCE blk.Instrs, so nothing routes to it).
> Default bni opt level stays 0 (int-mode load cheap).  The executable -O1
> fault-routing test (a removed const check coexisting with a live runtime check in
> one function, faulting via the live check) landed as `86ca24db1`
> (TestVMOptLevelBCEKeepsLiveFault).  **Phase 1 + 1.5 are now complete on both
> paths.**  Next up is the loop-BCE build-out (Phase P/D/2a/3), pending a
> sequencing decision.

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

### Phase P — `OP_PHI` lowering (prerequisite for loops) — ✅ COMPLETE
> **DONE 2026-08-27.** OP_PHI lowers on every backend (LLVM native phi; VM + all
> 3 native backends via the shared EliminatePhis).  The whole path is dormant
> until a phi PRODUCER (mem2reg / scalar SSA promotion, Phase 2a) lands.

**DESIGN (settled with the user 2026-08-27): LLVM keeps native phis; a SINGLE
shared SSA-destruction (phi-elimination) pass serves the VM + all 3 native
backends** — rather than four separate phi-handling implementations.  The shared
pass inserts copies in predecessors, splits critical edges, and sequences parallel
copies (swap/cycle), once and well-tested; the native backends then need zero
phi-specific code, and the VM's current `insertPhiCopies` (which has exactly the
critical-edge fragility + lost-copy-on-swap bug the review flagged) is replaced.
The pass is a lowering transform (not an `-On` optimization), backend-conditional
(LLVM keeps phis), so it runs at the start of native/VM per-function lowering.

- **LLVM lane — DONE (`1f22a3302`).** codegen emits `%vID = phi <type> [ val,
  %pred ], ...` from Instr.Phis.  Test: TestEmitPhiDiamond (hand-built diamond).
  Adversarial review SAFE TO LAND.  **Two latent notes for the phi-PRODUCER work
  (can't fire until something emits OP_PHI):** (1) phi operands emit via
  `emitRef(Val.ID)` — switch to `emitValRef` so a global-address pseudo
  (`IsGlobalRef`, ID==-1) renders as `@<mangled>`, not `%v-1`; (2) a zero-entry
  phi would emit malformed LLVM (a well-formed producer never makes one; consider
  an IR-verifier guard).  Address both when mem2reg starts emitting phis.
- **Shared `EliminatePhis` pass + OP_COPY — DONE (`2de26198d`).** The
  SSA-destruction pass in `pkg/binate/ir` (critical-edge split + parallel-copy
  sequencing with cycle-break temps; scalar-only, panics on aggregate/managed).
  OP_COPY opcode.  Adversarial-reviewed (sequencing executed against an oracle for
  swap / 3-cycle / chain / rho / dup-src / self).
- **VM lane — DONE (`80b6a83b6`).** `lowerFunc` runs `EliminatePhis` at entry;
  OP_COPY → BC_MOV/BC_MOV64; deleted the old buggy `insertPhiCopies`.  Executed
  tests: diamond, swap loop (returns 100 vs a lost-copy's 200), critical-edge
  split.  Reviewed SAFE TO LAND.
- **Native lanes (x64, aarch64, arm32) — DONE (`e85a3f8e1`).** One shared
  `ir.EliminatePhis` hook in `common.EmitObject` (before `EmitFunc`, covers all 3)
  + an OP_COPY arm in each arch dispatch (getOperand → reg-move → id's spill slot;
  x64 factored into `emitCopy`/`x64_copy.bn` for file length).  Per-backend tests
  confirm OP_COPY → a move, not the fail-loud default.  Reviewed SAFE TO LAND; the
  review's finding (int64-on-ILP32 silent truncation) is fixed — EliminatePhis now
  loudly rejects an integer phi wider than the target word (`t.Width >
  TypInt().Width`), so a >word scalar phi fails to compile rather than truncating.
- No promotion pass yet; this phase only proved the lowering.

**Latent codegen items for the phi-PRODUCER work (Phase 2a), collected from the
lane reviews — none can fire until something emits OP_PHI:** (1) LLVM + native
OP_COPY/phi operands use `emitRef`/`getOperand`, not `emitValRef`/`emitValOperand`
— a global-address pseudo (`IsGlobalRef`, ID -1) as a phi operand would render as
`%v-1` / be dropped; switch when the producer can emit such operands.  (2) A
zero-entry phi would be malformed (consider an IR-verifier guard).  (3) The
64-bit-scalar-on-ILP32 phi is now a loud reject, not supported — lift when 64-bit
scalar promotion is wanted (needs register-pair copies in the VM + arm32).

### Phase D — dominator / dataflow infrastructure (prerequisite for promotion) — ✅ COMPLETE (`cd944ec81`)
- **DONE.** `pkg/binate/ir/dom.bn`: `ComputeDom(f @Func) @DomInfo` computes
  predecessors (derived by scanning each block's terminator successors — no
  `Block.Preds` field added), DFS-postorder numbering, immediate dominators (the
  Cooper-Harvey-Kennedy iterative fixed point over reverse postorder, with the
  postorder-number finger walk for intersect), and dominance frontiers (Cooper's DF
  walk). `DomInfo` also answers `Dominates(a, b)` and `IteratedDF(defBlocks)` — the
  DF⁺ phi-placement query Phase 2a needs.
- Block-index-based, hand-rolled (no generic set/map container, no closures); stays
  within the BUILDER-compilable surface (`pkg/binate/ir` is BUILDER-compiled). A
  skeleton was verified against the pinned 0.0.14 BUILDER *before* build-out, and the
  whole package re-verified BUILDER-clean after.
- Kept **package-private** (no `.bni` export) — the only consumers (mem2reg / Phase
  2a and the tests) are in-package; export only when a cross-package consumer appears.
- Tests (`dom_test.bn`): hand-built diamond, reducible loop (self-DF header), nested
  diamonds, and straight line — asserting preds, idoms, DFs, the `Dominates` relation,
  and iterated DF. Green under builder-comp and builder-comp-int. Adversarial review
  (diamond/loop/nested + two irreducible CFGs hand-traced; test expectations
  independently recomputed) found no bugs.

### Phase 2a — non-managed scalar SSA promotion (the real BCE enabler) — ✅ COMPLETE (`ea7687188`)
**Detailed design: `plan-mem2reg-phase2a.md`.** `promoteScalars` in `RunOptPasses`
(`-O1+`, before the BCE passes): the first `OP_PHI` producer — makes the Phase P +
Phase D stack live and unblocks Phase 3. `pkg/binate/ir/mem2reg.bn` +
`mem2reg_rename.bn`. Everything below was implemented; two review cycles (design +
code) and a `-O2` conformance gate found + fixed one MAJOR (grounding, below); the
no-surviving-use fail-loud assertion + phi/predecessor-parity check are in.
**Validation:** full native `-O2` conformance **2987 pass / 0 fail**; `scalar-diff`
131/131; VM `-O2` correct; IR unit tests green (compiled + VM). (The ~200 LLVM `-O2`
failures are `clang -O2`, orthogonal — see the claude-todo entry; native is the clean
signal for the IR passes.)
- **Type grounding (the one bug the reviews/conformance caught):** a load reinterprets
  memory AS its type, so a reaching value of a looser type (an untyped-int
  `Signed=false` const/expr into a signed int slot) is grounded — the load is
  rewritten in place into an `OP_CAST` to its own type, so a downstream
  compare/div/shift/int→float still reads the slot's signedness. Without it a promoted
  `x < -1` flipped signed→unsigned (`matrix/scalar-diff/cmp/64/signed`). The phi path
  already carries the slot type, so only direct single-def forwarding needed it.
- **Orphan-block prune (prerequisite):** gen leaves benign orphan blocks (an if.merge
  whose arms all terminate) that branch into a phi-block; mem2reg prunes unreachable
  blocks first so a phi never has fewer entries than the LLVM backend's CFG
  predecessor count.
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
