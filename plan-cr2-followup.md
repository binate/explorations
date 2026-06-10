# Code-Red-2 Follow-up — bugs NOT covered by the original CR-2 plans

A follow-up to `plan-code-red-2.md` / `plan-cr2-{1,2,3}.md`, covering the open
defects the CR-2 five-class taxonomy did not (the backend ABI/value tail, a
`gen_selector` index-path sibling of Plan-1 Defect 1, a few front-end checker/
const/REPL gaps), plus a cross-package refcount-balance **coverage** sweep.

Every candidate was **source-confirmed against the live tree** while authoring
this plan — and because the assigned workers are fixing fast, **most candidates
were already RESOLVED mid-audit.** What remains is small. Defect-of-record stays
in `claude-todo.md`.

## Confirmation pass — already RESOLVED (move these to `claude-todo-done.md`)

| Bug | Status | Landed |
|---|---|---|
| Native variadic float `__c_call` arg mis-passed | RESOLVED, both natives | `56f09bc6` (SysV `AL=nsrn` + AAPCS64-darwin variadic-stack rule) |
| VM funcval-return-as-arg nil vtable | RESOLVED | `e337e413` (`isVMAddressAggregate` single-return copy-back in `lowerReturn`) |
| Native float consts/returns (`541` reads 0) | RESOLVED | `5281b138` + `cc6d0e9b` (AAPCS64 D0 float-return) + `1285683e` (runtime link) |
| Float-literal converter 1 ULP low | RESOLVED | `58570970` (`ParseFloatLitToBits` via `strconv.ParseFloat`, exact round bit) |
| Named signed-sub-word MIN/-1 divide escapes the guard | RESOLVED in behavior | `b43a0057` (named-distinct-scalar landing; `widenType` preserves named width+sign) — **needs a regression test only** (Plan B) |

Also confirmed RESOLVED from the `2083` audit's extern-var list: cross-pkg
managed-PTR extern value-copy/field-write (`559`/`561`, native-aa64 stale xfails
cleared `c4036777`) and managed-slice extern value-copy balance (`592`).

## The open work — three disjoint sub-plans

Disjoint by subsystem, extending the CR-2 concurrency model:

| Plan | Owns | Open defects |
|---|---|---|
| **A** | `pkg/binate/native` + `pkg/binate/vm` | `box(<scalar>)` native no-emit; dispatch-conflict should be a HARD ERROR (vm) |
| **B** | `pkg/binate/ir` + `pkg/binate/types` + `pkg/binate/repl` | `iota`-in-expressions fold; func-literal flavour inference; REPL parked-member iota; + the named-divide regression test (nested-array field-access moved → plan-cr2-1 Round-2) |
| **C** | `conformance/` only (test) | cross-package managed refcount-balance sweep (C1–C9) |

> **Reassignment note:** the dispatch-conflict diagnostic was originally scoped to
> Plan B (front-end), but its detection site is `pkg/binate/vm/lower.bn` — so it
> moves to **Plan A** (the vm owner). Plan B declined it rather than reach across
> the package boundary.

---

## Plan A — Backend tail (`pkg/binate/native` + `pkg/binate/vm` only)

### Defect: `box(<scalar>)` is unimplemented on both native backends — silent no-emit → garbage
✅ **LANDED** (binate `6235e43a`). **Also broken on the VM** — this plan's "runs on the VM" was wrong: `BC_BOX` passed `regs[Src1]` (the scalar VALUE) as the source pointer to `rt.Box` → read from address == value → SIGSEGV. Fix (native + VM): box the scalar from an ADDRESS holding its bytes. Native spills the value into its per-value frame slot (PlanFrame, reused for the function's lifetime) and passes `&slot`; the VM's `lowerBox` flags a value-carrying source (Args[0] not OP_ALLOC, not `isAggregateLoadTyp`) via `Src2` and `BC_BOX` boxes from `&regs[Src1]`. Coverage: `regressions/box-scalar` (int + uint8), green on LLVM ×3, VM ×2, native aa64; a vm `lowerBox` unit test. native_x64 not runtime-testable on the aa64 host (the C runtime can't cross-compile) — the x64 arm mirrors aa64, review-verified.
- **Symptom**: `box(i)` for a bare scalar register (not an `OP_ALLOC`, not an aggregate) compiles on LLVM and runs on the VM, but on aa64 + x64 `emitBox` falls to its `else` arm and emits **nothing** — the `OP_BOX` result is undefined → garbage managed pointer. MINOR-severity silent wrong-code.
- **Root cause (confirmed)**: the scalar `else` arm is a deliberate bare `return` on both backends — `pkg/binate/native/aarch64/aarch64_emit.bn:94-98` ("Scalar / unhandled — no emit") and `pkg/binate/native/x64/x64_managed.bn:134-137` ("Scalar / unhandled — silent return"). Only `OP_ALLOC` and `IsAggregateTyp` sources are handled.
- **Fix shape**: implement the scalar arm mirroring the LLVM path (`pkg/binate/codegen/emit_helpers.bn:425-451`): spill the scalar into a frame slot, pass its address (X0/RDI) + size (X1/RSI), `BL`/`CALL` `rt.Box`. Reuse the `OP_ALLOC`-arm frame-slot machinery; hoist the spill slot to the entry block (cf. LLVM `emitBoxAllocDecl`) so a `box()` in a loop doesn't leak native stack per iteration.
- **Files**: `native/aarch64/aarch64_emit.bn` `emitBox`; `native/x64/x64_managed.bn` `emitBox`.
- **Test**: a `conformance` cell `box(i)` returning the boxed value (currently no coverage); runs all modes.

### Defect: dispatch conflict (extern registered + Binate body) silently shadows — should be a HARD ERROR
✅ **LANDED** (binate `e508c841`). `LowerModule` now checks `externNameConflict` (LookupExtern(name) >= 0) before AddFunc-ing each non-extern function and, on a conflict, prints the name + `rt.Exit(1)` (the VM's fatal-error idiom). Coverage: a `lower_test` unit test pins the condition; the firing path (`rt.Exit`) isn't unit-testable and the collision can't be built in a passing test / conformance source program, so no-false-positive is verified by the full builder-comp-int suite (1263/0).
- **Symptom**: a name with BOTH an extern registration and a Binate body silently uses the Binate body, shadowing the extern with no diagnostic.
- **Root cause (confirmed)**: `pkg/binate/vm/lower.bn` `LowerModule` (~`:187-194`) lowers each non-extern func and `AddFunc`s it **without** checking `vm.LookupExtern(vmf.Name) >= 0` (`LookupExtern` at `vm.bn:335`).
- **Fix shape**: before `AddFunc`, `if vm.LookupExtern(vmf.Name) >= 0 { <hard error: dispatch conflict> }`. (Decide whether the same check belongs at the codegen/native registration sites — but those are other plans' files; the VM site is the confirmed one here.)
- **Files**: `vm/lower.bn` `LowerModule`.
- **Test**: a negative conformance/unit test: a package that both registers an extern and defines the same name → expect the conflict error.

### Disjointness (A)
Owns `pkg/binate/native/{aarch64,x64,common}` and `pkg/binate/vm` only. The three RESOLVED tail defects already live in this same island (`native/{x64,aarch64}`, `vm/lower_instr_helpers.bn`), so no escape. Consumes nothing from Plans B/C.

---

## Plan B — Front-end tail (`pkg/binate/ir` + `pkg/binate/types` + `pkg/binate/repl` only)

### Moved → `plan-cr2-1-frontend.md` Round-2 (nested-array element field access)
The nested-array managed-POINTER field-read (`a[i][j].field`, `a [N][M]@Struct` → literal 0) is the **managed-ptr-read facet** of the broader Round-2 Plan-1 defect "`[N][M]Struct` value-struct field access reads 0 and writes NOWHERE" — the **same root cause** (`getIndexElemType` doesn't recurse a nested-index base, `gen_access.bn`), which also breaks the value-struct variant and the **write** path. Both are front-end (`ir`) ownership, so the **single fix-of-record lives in plan-cr2-1 Round-2**; fix all variants (managed-ptr read, value-struct read, write) together, and have the test cover the `[N][M]@Box` read as one cell of that sweep. No separate item here.

### Defect: checker does not fold `iota` in expressions — bit-flag const compile-time values stay plain-iota — ✅ RESOLVED (binate `05901f97`, 2026-06-09)
- **STATUS 2026-06-09**: FIXED as a TWO-part change (the one-liner below was necessary but not sufficient — see claude-todo's MINOR entry, which already specified both parts). (1) `checkIdent` returns `makeUntypedIntWithLit(c.Iota)` for `iota` so it folds inside expressions (the explicit `1 << iota` member). (2) `checkGroupDecl` now repeats the previous explicit member's initializer (re-folded at the current iota) for bare members, matching IR-gen's `genConstGroup`, so `B1`/`B2` fold to `1<<1`/`1<<2` not the plain indices. Intended consequence (pre-authorized by the MINOR entry): a large bit-flag const assigned to a narrow type that the checker previously accepted by computing the wrong value is now correctly rejected. No existing unit/conformance cell changed (151 const/iota/enum cells stay green; the `= iota` enum idiom is unaffected). Tests: conformance `672_err_iota_bitflag_overflow` (negative, all modes) + two checker unit tests in `check_expr_constfold_test.bn`.
- **Symptom**: `const ( B0 int = 1 << iota; B1; B2 )` — the compile-time *values* of `B1`/`B2` stay plain `iota` (`1,2`) instead of the folded `1<<iota` (`2,4`) when read as compile-time constants (array dims, other const exprs).
- **Root cause**: `pkg/binate/types/check_expr.bn` `checkIdent` iota arm folds bare `iota` but not `iota` inside a binary expression.
- **Fix shape**: fold `iota` within const expressions during the checker's const evaluation (the same evaluator that handles `1 << iota` for runtime values must feed the compile-time value table).
- **Files**: `types/check_expr.bn` (+ the const-fold helpers if shared).
- **Test**: a conformance cell using a bit-flag const as an array dimension / in a const expr.

### Defect: bare func literal in assignment position doesn't infer its managed/raw flavour from the LHS — ✅ RESOLVED (binate `e15680d7`, 2026-06-09)
- **STATUS 2026-06-09**: FIXED exactly as the fix shape below — `check_stmt.bn`'s simple-assign equal-count loop now threads `checkExprWithFVHint(rhs, lhsType)`. Tests: conformance `671_assign_func_literal` (green on every mode) + two checker unit tests (managed upgrade + raw default) in `check_func_lit_test.bn` (its `findFuncLitExprInStmt` helper also gained the missing `Exprs2`/`Init`/`Body` walk so it can reach an assign-RHS literal). At this tree the pre-fix symptom is a hard `cannot assign` error (AssignableTo is strict), not a silent lifetime bug.
- **Symptom**: `var f @func() = func() { ... }` resolves the literal as `*func()` (raw) rather than the LHS `@func()` flavour, so the FV-hint isn't applied at a plain assignment (the call-arg / return positions DO apply it — B.3b of plan-function-values-phase-2).
- **Root cause**: `pkg/binate/types/check_stmt.bn` `checkAssignStmt` simple-assign loop doesn't install the LHS type as the function-value hint when checking the RHS literal.
- **Fix shape**: thread `checkExprWithFVHint(rhs, lhsType)` through the simple-assign loop (mirror the call-arg/return sites).
- **Files**: `types/check_stmt.bn` `checkAssignStmt`.
- **Test**: a conformance cell `var f @func() int; f = func() int {…}` (lifetime/flavour correct).

### Defect: REPL parked-member iota-repeat (the `447` adversarial-review leftover, REPL-only) — ✅ RESOLVED (binate `5fc5a52f`, 2026-06-09)
- **STATUS 2026-06-09**: FIXED with a SINGLE checker-side change — simpler than the fix shape below, which assumed the bare member already parks (it doesn't). The real fix: `checkGroupDeclTentative` (the REPL per-member group check) now SYNTHESIZES the repeat for a bare member (carrying the preceding explicit member's initializer, re-folded at this iota) and checks/parks THAT — mirroring `checkGroupDecl`'s batch-path repeat. So a bare member that repeats a forward-ref'd initializer captures the same dependency and PARKS; on retry the existing `GenConstMember` path re-folds the carried initializer via `evalConstExpr` (now resolving the defined name). No new IR-gen / `PendingDecl` machinery needed: `genConstGroup` already skips parked members and the retry already routes a resolved group-member const through `GenConstMember`. Verified end-to-end by driving a gen1-built bni REPL: `const ( B0 int = M << iota; B1 )` then `const M int = 2` then `println(B1)` prints `4` (= 2<<1), not 1. Tests: `check_pending_test.bn TestPendingConstGroupBareMemberRepeatsParkedExpr` (park + resolve) + `e2e/repl.sh tier3-pending-const-group-bare-iota-repeat` (verified transcript). NOTE: the e2e harness is currently blocked by a pre-existing BUILDER-vs-`same` break (the generated test runner now depends on `std/errors`, which uses the `same` builtin that BUILDER `bnc-0.0.7` predates) — a `bnc-0.0.8` release + BUILDER bump fixes it.
- **Symptom**: in a REPL `const ( … )` group, a bare member after a *parked* (forward-ref-blocked) explicit member gets plain `iota` instead of repeating the parked member's initializer once it resolves.
- **Root cause (confirmed)**: `genConstGroup` doesn't carry `prevExpr`/`prevTyp` across the parked `continue`; `GenConstMember` (the REPL retry) has no iota-repeat contract.
- **Fix shape**: (1) update `prevExpr`/`prevTyp` from the parked member before `continue`; (2) carry the preceding-member effective expr+type on the parked member's `types.PendingDecl` so the REPL retry (`repl/decl.bn` `GenConstMember(rd, m, iotaIdx)`) repeats it. All in owned packages (`ir/gen_const.bn`, `ir/gen_repl.bn`, `repl/decl.bn`, `types.PendingDecl`).
- **Files**: `ir/gen_const.bn`, `ir/gen_repl.bn`, `repl/decl.bn`, `types` (`PendingDecl` fields).
- **Test**: a REPL/repl-unit test (`repl/decl_test.bn`) — define `fwd` on a later prompt; assert the bare member repeats the parked expression, not plain iota.

### Test-only: named signed-sub-word MIN/-1 divide (RESOLVED in behavior by `b43a0057`) — ✅ COVERAGE ADDED (binate `b4648200`, 2026-06-09)
- **STATUS 2026-06-09**: regression coverage landed. conformance `679/680/681_err_div_named_int{8,16,32}_min` (MIN/-1 → "integer overflow" panic, operands from functions; green on every mode like plain-int 608/609) + `gen_binary` unit test `TestWidenTypePreservesNamedSubWordWidthAndSign` (pins `typeWidth(widenType(I8,I8))==8`, `typeIsSigned==true` — read via the peeling helpers, since widenType returns the NAMED type, not plain int). Also corrected the now-stale comment on `TestNamedTypeDivideEmitsDivCheck`. (Cells renumbered 673→679 etc. at landing to dodge a Plan-C collision.)
- Add the missing regression: a `conformance` error-cell `type I8 int8; cast(I8,-128) / cast(I8,-1)` → "integer overflow" panic (cover int8/16/32 named variants), plus a `gen_binary` unit test asserting `widenType(I8,I8)` keeps `typeWidth==8`, `signed==true`. Mark the todo entry RESOLVED with the `b43a0057` cite.

### Disjointness (B)
Owns `pkg/binate/{ir,types,repl}` front-end gen only. Touches no `codegen`/`native`/`vm` file. The only cross-package surface is adding fields to `types.PendingDecl` (also owned). The two out-of-package candidates were declined: the float-literal round-bit is RESOLVED and lives in `native/common`; the dispatch-conflict diagnostic lives in `vm/lower.bn` → Plan A.

---

## Plan C — Cross-package managed refcount-balance coverage sweep (`conformance/` only)

From the `2083` audit: these cross-package managed scenarios **work functionally
but have no refcount-balance assertion**, so a leak (rc stays elevated) or an
extra RefInc/RefDec would slip through. This is a coverage sweep — like the CR-2
matrices, it is expected to *surface* latent refcount bugs, not just add green
cells. Pattern: `rt.Refcount(p)` before/after, mirroring `586`/`592`/`130`.

**Status (2026-06-09): C1–C7 BUILT + GREEN** (conformance `673`–`678` + `682`; the
arg cell is `678` and the generic cell is `682` after landing-rebase renumbers —
`672`/`679` were taken by other workers; find them by name `cross_pkg_*_balance`;
directory form, balance-invariant assertion), passing on LLVM/VM/native aa64+x64 —
the cross-pkg refcount discipline is **sound** for arg / return / struct-field /
managed-slice-element / iface-construct / iface-return / **generic type-arg**; no
latent bug surfaced, so these are a regression net closing the `2083`
refcount-balance gap. **Remaining (lower value):** C8/C9 — extern-var *functional*
cells (not balance); C9 blocked by `551`. The balance-bearing sweep is DONE.

| Cell | Asserts (refcount returns to baseline crossing a package boundary) | Functional precedent |
|---|---|---|
| **C1** `cross_pkg_managed_slice_elem_store_balance` | `store.S[i] = @v` (extern `@[]@Node`) — element store balances AND the overwritten element is RefDec'd | `558` |
| **C2** `cross_pkg_managed_arg_balance` | `store.Consume(a @Node)` — caller RefInc + callee-scope RefDec net zero | `337`, `595` |
| **C3** `cross_pkg_managed_return_balance` | `store.New() @Node` — return-move arrives rc==1 (no double-retain leak) | `157`/`576`, `554` |
| **C4** `cross_pkg_managed_struct_field_store_balance` | `root.Child = c` (field type cross-pkg `@Node`) — store balances, struct dtor RefDecs the field | `556`, `062`/`270` |
| **C5** `cross_pkg_iface_construct_balance` | `var iv @shape.Shape = h` (imported impl) — box RefIncs receiver, box-drop RefDecs | `382`/`585`, `554`/`567` |
| **C6** `cross_pkg_iface_return_balance` | `shape.Make() @Shape` — iface return arrives rc==1 | `576` |
| **C7** `cross_pkg_generic_typearg_balance` | `genlib.Append[@pkg.T](…)` — managed type-arg lifecycle balances | `497`/`464` |
| **C8** `cross_pkg_extern_field_write` (functional) | field write through an imported value-struct / raw-ptr var (the `561` analogue) | `561` |
| **C9** `cross_pkg_extern_addr_rvalue` (functional) | `&pkg.X` (imported scalar var) — the `551` analogue for imports (**blocked by `551`**) | — |

**Mechanics**: all use the directory form (`main.bn` + `expected` + `pkg/…`),
mirroring `586`/`592`. Pick the next free contiguous numbers **at landing time**
and re-run `scripts/hygiene/run.sh` (`conformance-test-numbers`) on every rebase
— concurrent workers move the frontier. Every cell that imports
`pkg/builtins/rt` **alongside** another `pkg/…` must carry an
`.xfail.builder-comp-int-int` (the pre-existing `136`/`383` int-int rt-loader
bug; balance verified in the other 5 default modes + native; functional behavior
covered mode-agnostically by the precedent) — single-package balance tests
(`554`/`556`/`567`/`595`/`130`) need no such xfail because the loader bug only
bites when `rt` is one of ≥2 imported packages.

### Disjointness (C)
Edits only `conformance/` test files — collides with no compiler-source plan. If
a cell turns red, the *fix* is filed and routed to whichever source plan owns it
(A or B or the CR-2 plans), keeping C purely additive.

---

## Sequencing

Land small, cherry-pick early (stay-close-to-main). Plan A (2 small fixes) and
Plan B (4 fixes + 1 test) are independent and parallel; Plan C is test-only and
can run alongside both — its red cells, if any, feed back as new defects. First
housekeeping step: move the five RESOLVED entries above to
`claude-todo-done.md`.
