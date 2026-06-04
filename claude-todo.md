# Binate TODO

Tracks open work items. Completed items live in [claude-todo-done.md](claude-todo-done.md).

---

## CRITICAL

### Returning a by-value struct through interface-method dispatch was miscompiled — FIXED + LANDED 2026-06-04 (binate `9baa579d`)
- **Was**: an interface method returning a by-value struct (small
  aggregate, NOT a managed handle like `@T`/`@[]T`) came back through
  vtable dispatch with only its FIRST field correct, later fields garbage,
  in BOTH the LLVM backend and the bytecode VM.  Direct (concrete-receiver)
  calls were fine.
- **Root cause**: the interface method's result type was resolved during
  interface collection (GeneratePackage / GenModule first pass), which ran
  interleaved with struct-name registration in declaration order.  An
  interface method whose result is a struct declared LATER in the file
  (`interface B { get() Pair }` before `type Pair struct {...}`) resolved
  the struct via resolveTypeExpr's unresolved-name path, which silently
  falls back to `int`.  OP_CALL_IFACE_METHOD's result type (`instr.Typ`)
  thus degraded to a single word; both backends read `instr.Typ`, so both
  miscompiled identically (llvmType -> `i64`; the VM mis-sized the result).
  Latent because conformance/553 only returned a scalar / a managed-slice
  through an interface, never a plain struct.
- **Fix** (`9baa579d`): a struct-name pre-pass registers every struct name
  before the first pass, so interface method result types resolve to the
  real struct type.  Interface collection stays interleaved in the first
  pass (order vs globals / type-aliases -- which may be interface-typed;
  isInterfaceTypeExpr consults moduleInterfaces -- is unchanged).
  conformance/581 covers 2- and 3-field structs through managed- and
  raw-receiver dispatch, interfaces declared before the structs.  Full
  conformance green (505 comp / 499 int); no other
  by-value-struct-returning interface exists in-tree (Backend returns
  bool / @[]char).
- **Unblocked + LANDED 2026-06-04** (binate `b9ca1acc`): the repl ReplSession->interface conversion.

### Managed-aggregate-by-value element/field stores skip save-copy-destroy — ASSIGNMENT PATHS DONE; literal/short-var/raw-ptr siblings remain — MEMORY-CORRECTNESS (latent)
- **UPDATE 2026-06-04 (binate `32bad348`)**: the two gaps below are now
  FIXED.  The single-assign ARRAY-element aggregate arm landed; the
  multi-assign SLICE aggregate case was switched from the incomplete
  `emitStructElemRefcount` to the two-slot `emitStructCopy`/`emitStructDtor`
  form (complete for `@Iface` fields + nested aggregates), and
  `emitStructElemRefcount` was deleted.  Pinned by `conformance/583`
  (multi-assign slice element with an `@Iface` field — verified to fail
  pre-fix) and `582` (single-assign array aggregate).  All ASSIGNMENT-store
  paths (single + multi assign, IDENT/SELECTOR/array/pointer/slice) now
  save-copy-destroy correctly.  REMAINING siblings: short-var multi-bind
  (CRITICAL), raw-pointer single-assign index (MAJOR), array/managed-slice
  literals (MAJOR) — separate entries below.
- **What**: when the store TARGET is a managed struct/array **by value**
  (`needsStructCopy(T)` true — a struct/array holding managed fields, NOT
  `@T`/`@[]T` which are handles), a plain store under-retains the new
  aggregate's managed fields and leaks the old's — violates "the compiler
  must NEVER generate code that leaks."  Several store paths had this gap.
- **FIXED (multi-assign `=` SELECTOR/array-INDEX/pointer-INDEX)**: binate
  `6c4d45b0` (concurrent worker) added `emitElemPtrStore`
  (`gen_assign_multi.bn`) — the save-copy-destroy via `emitStructCopy`/
  `emitStructDtor`.  Pinned by `conformance/574_multiassign_struct_aggregate`.
- **MAJOR BUG INTRODUCED by that fix — multi-assign SLICE aggregate is
  INCOMPLETE**: `6c4d45b0` routed the multi-assign managed-slice-element
  aggregate case (`gen_assign_multi.bn`, `needsStructCopy` arm) through
  `emitStructElemRefcount` (`gen_util_refcount.bn`), which RefDec/RefIncs
  `@T`/`@[]T`/`@func` fields field-by-field but **omits `@Iface` fields and
  does NOT recurse into nested aggregates**.  So `s[i], n = f()` where the
  slice element is a struct holding an `@Iface` (or a nested managed
  aggregate) field leaks the old field / under-retains the new.  `574`
  doesn't catch it — it uses a `@Counter` (managed-ptr) field only.  **Fix**:
  replace the `emitStructElemRefcount` call with the complete two-slot
  `EmitSliceGet`→`oldSlot`/`newSlot`→`emitStructCopy(newSlot)`/
  `emitStructDtor(oldSlot)` form (mirrors single-assign slice
  `gen_control.bn:391-401`, which uses the generated `__copy_`/`__dtor_`
  helpers — complete for all field kinds + nesting); then delete the now-dead
  `emitStructElemRefcount`.  Add a conformance test with an `@Iface` field in
  a slice-element struct.
- **STILL MISSING — single-assign ARRAY-element aggregate** (`gen_control.bn`
  TYP_ARRAY arm): handles the four managed scalar kinds but no
  `needsStructCopy` arm → `arr[i] = w` (managed-struct array element) leaks
  old / under-retains new.  Fix: `emitElemPtrStore(ctx, b, elemPtr, rhs,
  elemTyp)`.  (Single-assign SELECTOR + slice already complete.)
- **Severity / priority**: real memory-correctness, but **purely latent** —
  no caller in pkg/+cmd/ today (SELECTOR/INDEX multi-assign sites target
  scalar `int`; fixed-size arrays are all `[N]uint8`/`[N]char`).  Invariant-
  hardening.  See sibling entries: short-var multi-bind (CRITICAL, below),
  raw-pointer single-assign index, array/managed-slice literals.
- **Discovery**: 2026-06-03 investigation + 2026-06-04 adversarial review
  workflow; the `@Iface` slice incompleteness found reviewing `6c4d45b0`.

### ~~Short-var multi-bind `q, n := f()` does NO refcounting on bound components — CRITICAL (double-free)~~ — FIXED + LANDED 2026-06-04 (binate `efa4f569`)
- **Fixed**: `genShortVar`'s multi-bind branch now acquires each managed
  component after the store — `emitManagedValueCopyRefInc` (scalar) +
  `emitStructCopy` for `needsStructCopy` aggregates (fresh slot → no dtor) —
  mirroring `genMultiAssign`.  Pinned by `conformance/584`
  (`q := fresh @Box`, aliased into `keep`, rc must read 2; verified to fail
  pre-fix where `q` was freed at the end of the `:=` statement) + a unit
  test asserting the scalar (OP_REFINC) / aggregate (`__copy_`) acquire.
- **Original analysis retained below.**
- **What**: `genShortVar`'s multi-assign branch (`gen_short_var.bn`, the
  `len(Exprs)>1 && len(Exprs2)==1` arm) does `EmitExtract` → `EmitAlloc` →
  plain `EmitStore` → `defineVar` with **zero acquire** — neither the Axiom-3
  copy-RefInc for managed scalars (`@T`/`@[]T`/`@func`/`@Iface`) nor
  `emitStructCopy` for managed aggregates.  The extracted component is a
  borrow from the OP_CALL result temp (whose dtor RefDec's it at end of
  statement); the new var is registered via `defineVar` so its scope-exit
  dtor RefDec's it AGAIN → **0 acquires, 2 releases = double-free / UAF** for
  any managed component.  This is the exact bug `0b3f4abe` fixed for the `=`
  form (`genMultiAssign` calls `emitManagedValueCopyRefInc`), never applied to
  the `:=` short-var sibling.
- **Fix**: in the multi-bind loop, after `EmitExtract`, mirror
  `genMultiAssign`: `emitManagedValueCopyRefInc(ctx.Func, b, extracted,
  elemTyp)` for scalar components, and for `needsStructCopy(elemTyp)`
  `emitStructCopy` on the freshly-alloc'd slot (no old value → no dtor).
- **Latent**: every conformance multi-`:=` (023, 066, 288) returns scalar
  int/bool components.  Add a conformance test returning a managed scalar and
  a managed aggregate via `:=` (rt.Refcount balance) + a unit test asserting
  the acquire is emitted.
- **Discovery**: 2026-06-04 adversarial review workflow (probe-confirmed:
  short-var multi with `@Node` emits refinc=0 in `foo` vs the `=` form's 2).

### Raw-pointer single-assign index `p[i] = v` does no element refcounting — MAJOR, LATENT
- **What**: `gen_control.bn` single-assign INSTANTIATE_OR_INDEX `TYP_POINTER`
  arm is a bare `EmitGetElemPtr`+`EmitStore` — no managed-scalar RefDec-old/
  acquire-new arms (the adjacent array arm has them) and no `needsStructCopy`
  arm.  `p[i] = v` for a managed-scalar OR managed-aggregate element leaks the
  old slot contents / under-retains the new.  The multi-assign `emitIndexStore`
  pointer arm (via `emitElemPtrStore`) IS correct, so the two forms diverge.
  The earlier "(raw = unmanaged, likely fine)" note was WRONG: the raw pointer
  only excuses keeping the *block* alive, not balancing the managed values
  *inside* the slot.
- **Fix**: give the TYP_POINTER arm the same discipline as the array arm —
  the four managed-scalar arms + `emitElemPtrStore` for the aggregate case.
  Conformance + unit test (`*Wrap` receiver).
- **Discovery**: 2026-06-04 review (probe: `p[0]=w` → copy=0, dtor=1).

### Array-literal / managed-slice-literal elements don't acquire managed-aggregate fields — MAJOR, LATENT
- **What**: `genArrayLit` (`gen_access.bn`) element store is a bare
  `EmitStore` with no `needsStructCopy` follow-up; `genManagedSliceLit`
  handles managed-scalar elements (and even there omits the `@func` arm) but
  has no `needsStructCopy` arm before `EmitSliceSet`.  So `[2]Wrap{w,w}` /
  `@[]Wrap{w,w}` copy the elements' managed fields by value without RefInc
  (initialization sites — no old value to release, but the new still needs
  the acquire half, as `genCompositeLit` does for struct fields).  Under-
  retain → double-free when source and element are both destroyed.
- **Fix**: `genArrayLit` — after `EmitStore`, `if needsStructCopy(elemTyp) {
  emitStructCopy(ctx.Func, b, elemPtr, elemTyp) }`.  `genManagedSliceLit` —
  add a `needsStructCopy` arm (two-slot copy of `val` before/at
  `EmitSliceSet`) AND the missing `@func` scalar arm.  Unit tests asserting
  `__copy_` count == element count.
- **Discovery**: 2026-06-04 review (probe: array/managed-slice literal
  copy=0 vs struct literal copy=1).

### Multi-value assignment `a, n = f()` mishandled managed targets — FIXED + LANDED 2026-06-03 (binate `0b3f4abe`)
- **Was**: `genMultiAssign` (then inline in `genAssign`) Axiom-3 copy-RefInc'd each managed component then stored it, with two defects:
  - **Defect A (CRITICAL, wrong-code/UAF)**: the copy-RefInc had arms for `@T` / `@[]T` / `@Iface` but **none for `@func`**, so `g, n = f()` returning `(@func(...), int)` stored the `@func` without a copy-RefInc; the call-result temp's dtor freed the closure record while `g` still pointed at it → UAF on invoke (+ double-free at scope exit).  Probe: a capturing `@func` multi-assigned then invoked → SIGSEGV.
  - **Defect B (MAJOR, leak)**: the IDENT / INDEX / SELECTOR stores overwrote the target with no RefDec of its OLD managed value, so reassigning a live managed variable leaked the previous value (+1/exec).
- **Fix**: reworked the multi-assign managed-store to mirror single-assign's RefInc-new / RefDec-old discipline (Axiom 5) across all four managed VALUE types (`@T`/`@[]T`/`@func`/`@Iface`) and all three target shapes (IDENT / INDEX / SELECTOR), via new shared dispatchers `emitManagedValueCopyRefInc` / `emitManagedValueRefDec` (gen_util_refcount.bn) + predicate `isManagedScalarType` (gen_refcount_pred.bn).  The multi-assign body was extracted to `genMultiAssign` + `emitIndexStore` in a new `gen_assign_multi.bn` (gen_control.bn was over the 500-line soft cap).  Blank `_` targets still skip copy-retain (the `_`-discard fix, `567`).
- **Tests**: conformance `571_multiassign_old_value_released` (B: aliased object's refcount returns to baseline), `572_multiassign_func_value_retained` (A: capturing `@func` multi-assigned + invoked, no UAF — crashed pre-fix), plus `gen_assign_multi_test.bn` unit tests (bound component copy-RefInc'd vs blank `_` skipped, for `@T` and `@func`; index target refcounts the old element).  Green in all 6 default modes; compiled 491/0, int 485/1 (the 1 = pre-existing 520).
- **Struct-aggregate SELECTOR/INDEX — FIXED 2026-06-03 (binate, pending cherry-pick)**: a managed *struct/array AGGREGATE* field/element targeted by a multi-assign SELECTOR/INDEX (`s.structField, n = f()` / `arr[i], n = f()` where the element is a managed struct) was a plain store — no save-copy-destroy — so the new aggregate's managed fields were under-retained (double-free at scope end) and the old element's leaked.  Now save-copy-destroyed: SELECTOR mirrors the IDENT struct case; INDEX array/pointer via a new `emitElemPtrStore` helper, INDEX slice via `emitStructElemRefcount`.  Test `conformance/574_multiassign_struct_aggregate` (captured `@Counter` refcount returns to baseline 2, was 1 pre-fix); green in all 6 modes, verified to fail pre-fix.
- **Discovery**: 2026-06-03, reviewing the multi-assign path while fixing the `_`-discard leak (`570`).  Pre-existing.

### `@func` copy-RefInc symmetry — FIXED 2026-06-03 (binate `d118a3c4` + `76099018`); `@Iface` analogue + VM-leak still open
- **Was**: `@func` / `@Iface` values (`TYP_MANAGED_FUNC_VALUE` /
  `TYP_INTERFACE_VALUE_MANAGED`) had `NeedsDestruction() == false`, so the
  struct copy/dtor generators, `emitStructElemRefcount`, and the
  assignment paths skipped them on COPY, while `@func`/`@Iface` LOCALS
  *were* RefDec'd at scope end — an acquire/release asymmetry.  A
  capturing `@func` stored into a struct field, passed as a parameter, or
  returned dropped its only owning ref; the param/scope-end RefDec then
  freed the capture record while a field/caller still pointed at it, and a
  later invocation was a use-after-free.  Concrete all-modes repro:
  `conformance/534_func_value_param_to_field_capture`
  (`func install(h @Holder, f @func(int) int) { h.F = f }` then invoke
  `h.F`) — SIGSEGV compiled.
- **`@func` half FIXED** (binate `d118a3c4`, `76099018`):
  1. `d118a3c4` — null-safe `emitManagedFuncValueRefDec`: guard the
     closure-dtor fetch (vtable[0] load, `OP_FUNC_VALUE_DTOR`) + RefDec
     behind `data != null`.  The flip below makes struct dtors run on the
     zero-inited `@func` fields a managed struct's `make()` leaves behind
     (`{vtable=null, data=null}`); the unguarded vtable[0] load faulted on
     the null vtable.  Shared IR layer → fixes every backend + the VM.
  2. `76099018` — flip `NeedsDestruction(@func) = true` + acquire (RefInc)
     at every copy site: parameter entry, var-init / short-var
     (isFresh-guarded), the three assignment paths, return,
     `emitStructElemRefcount`, and slice/array element stores.
  `534` now passes in **all 6 default modes** and is un-xfailed; `542`
  adds a return-a-capturing-closure regression.  Unit test
  `TestEmitFuncValueRefDecGuardsNullData` pins the guard shape.
- **VM capture-record leak — FIXED 2026-06-03 (binate `0a0d00af`).**  Under
  the bytecode VM a capturing `@func`'s data slot is a 32-byte
  `DATA_KIND_COMPILED_CLOSURE` rec whose `rec[3]` points at the heap
  closure struct; RefDec'ing the @func value decremented the *rec* and
  (`vt.Dtor == 0`) just freed it, never the struct → the struct and its
  captured managed values leaked.  Fix:
  `ensureHandle` marks an IsClosure callee's vtable dtor slot with a `-1`
  sentinel; `BC_REFDEC_INLINE_FAST` recognizes it, frees the rec and
  RefDec's the closure struct, running its dtor via an iterative frame push
  (flat-stack, no host recursion at `-int-int` depth).  Dtor name plumbed
  ir.Func → VMFunc, resolved by `LookupFunc`.  Conformance `550` pins it
  (captured `@Counter` refcount returns to baseline).  @func is now
  leak-clean on every backend + the VM.
- **REMAINING — `@Iface` analogue still BROKEN** (the symmetric half).
  `emitManagedIfaceValueRefDec` has the same unguarded vtable[0] load (the
  shared `emitVtableDtorLoad`) and there is no `@Iface` acquire arm on
  copy.  `520_iface_dtor_callee_sole_ref` fails in all int modes ("call
  through nil interface value"); `383_cross_pkg_iface_dtor` is in the same
  family (and additionally hits the int-int multi-package loader bug
  below).  Apply the same recipe to `@Iface`
  (`TYP_INTERFACE_VALUE_MANAGED`): null-safe iface RefDec + flip + acquire
  arms.  This is the separate "@Iface first-class" follow-up.
- **Unblocks the REPL interrupt seam (Stage 5 of `plan-repl-embeddable.md`)
  — DONE.**  `vm.SetPoll(poll @func(@VM) int) { vm.Poll = poll }` is the
  param→field `@func` store; with the acquire arms a CAPTURING poll no
  longer UAFs.  Capturing-poll seam tests added and green in every int
  mode: `pkg/binate/vm/vm_poll_test.bn` (`TestCapturingPollFiresViaSetPoll`,
  `TestCapturingPollSuspendsAfterThreshold` — direct `vm.SetPoll`) and
  `pkg/binate/repl/step_test.bn` (`TestStepCapturingPollSuspendsTurn` — the
  end-to-end `s.SetPoll → vm.SetPoll` forward, a capture-driven SUSPEND
  mapping onto `STEP_SUSPENDED`).  The previously-omitted non-capturing
  NOTEs in those files are updated to describe the capturing coverage.

### A closure that captures a `@func` under-retained the captured value — FIXED + LANDED 2026-06-04 (binate `388c48d3`)
- **Was**: a closure that captures a `@func` value did not acquire a ref
  to the captured @func's record, but the closure struct's dtor RefDec'd
  it (NeedsDestruction(@func) = true).  The captured @func was
  under-retained: its record freed when the source @func's scope ended,
  then the closure called / dtor'd freed memory (use-after-free).  Native
  only; a flaky crash in __dtor_closure_* (deterministic under
  guard-malloc).  First seen as a wrapper poll (capturing a host @func)
  installed via vm.SetPoll — the shape an embedder needs for a VM-free
  poll — but the root cause is general (any closure capturing a @func).
- **Root cause**: gen_func_lit.bn emitCaptureRefInc handled
  TYP_MANAGED_PTR / TYP_MANAGED_SLICE but had no TYP_MANAGED_FUNC_VALUE
  branch — the capture-side acquire counterpart of the @func copy-RefInc
  symmetry work (d118a3c4 / 76099018), missing for closure captures.
- **Fix** (`388c48d3`): add the TYP_MANAGED_FUNC_VALUE branch calling
  emitManagedFuncValueRefInc (the acquire helper every other @func copy
  site uses).  conformance/586 pins it deterministically via refcounts;
  pkg/binate/vm TestWrappedCapturingPollSuspends covers the wrapper-poll
  shape.  Full conformance green (513 comp / 507 int).
- **Unblocks**: the VM-free repl poll (request #3 of the repl-improvements
  work).  Work preserved on binate branch `repl-poll-wip`.

### `136_grouped_imports` / `383_cross_pkg_iface_dtor` — `package "pkg/builtins/rt" not found` under int-int (pre-existing loader bug)
- **Symptom**: both fail ONLY in `builder-comp-int-int` with
  `package "pkg/builtins/rt" not found` (a loader error, before execution);
  green in all other modes.  Confirmed pre-existing on a clean tree
  (2026-06-03) — independent of the `@func`/`@Iface` work.  Both are
  multi-package tests (grouped imports / cross-package), so the deeply
  nested interpreter's package resolver appears to mis-resolve a transitive
  core import at int-int depth.  No xfail markers yet.  Root cause: unknown
  — needs investigation of the int-int package search-path setup.

### Audit the home of generic low-level helpers shared by cmd/bni + the REPL engine (low priority / code-org)
- **Context**: extracting the REPL engine to `pkg/binate/repl` (Stage 4c
  of `plan-repl-embeddable.md`) needs generic helpers that ALSO stay in
  cmd/bni: `streq`, `appendCharSlice`, `appendFilePtr`, `appendImportSpec`,
  `readFile`, `quotePath` (+ the IR-gen import-registration subtree
  `registerPkgImports`/`registerMainImports`/`loadBuiltinBNIs`/
  `ensureBootstrapLoaded`/`addLoaderPaths`).  For 4c these are
  **DUPLICATED** (each package keeps its own copy) to avoid a weird
  dependency (runProgram/runTests pulling in `pkg/binate/repl` just for
  `streq`).  `pkg/binate/buf` is the WRONG home (it owns CharBuf/CopyStr;
  `readFile`/`quotePath` don't belong there).
- **What to audit**: where these generic string / slice / file / IR-gen
  helpers SHOULD live long-term.  Survey the codebase for the real
  commonalities (who needs `streq`, `readFile`, the import-registration
  helpers?) and decide: a genuinely-shared tier-2 package (a possibly-
  uselessly-named `pkg/binate/utils`? a split between string-utils /
  file-utils / ir-import-helpers?), vs leaving the small ones duplicated.
  Consolidate the 4c duplicates once decided.

---

## MINOR

### Wire `--version` into bnc / bni / bnas / bnlint — next-release follow-up
- **Goal**: each tool accepts `--version` and prints its display version
  (`<tool>-` + `version.Version`, e.g. `bnc-0.0.7-pre`) to stdout, then
  exits 0.  Single source of truth is `version.Version` (the repo-root
  `VERSION` file, minus its `bnc-` builder prefix).
- **Why deferred (user, 2026-06-03)**: `cmd/bnc` is the only
  BUILDER-compiled tool, and reading `version.Version` cross-package is
  the extern-var-read feature (`be49c0a9`) — plus pulling the `version`
  package into bnc's tree needs BUILDER to parse the `var Version`
  declaration in `version.bni` (the `bni_scope` `DECL_VAR` support).
  Neither is in `bnc-0.0.6` (confirmed: `be49c0a9` is not in the 0.0.6
  tree).  So bnc can't consume `version.Version` until `BUILDER_VERSION`
  is bumped to a snapshot that includes the extern-var landing.
  `bni`/`bnas`/`bnlint` are built BY bnc (full language) and COULD be
  wired today, but the user chose to defer all four together so they
  land consistently after the next BUILDER bump.
- **When**: the next release / BUILDER bump (same gate as the bnlint
  dep-body deployment and the `vm` lint-skip removal).  After the bump,
  BUILDER understands extern vars, so all four can
  `import "pkg/binate/version"` and read `version.Version`.
- **Implementation sketch**: in each tool's `main()` arg handling,
  detect `--version` before the rest of parsing, build `<tool>-` +
  `version.Version` via `buf.Concat`, print + newline to stdout, exit 0.
  Each tool already imports `buf`; add `import "pkg/binate/version"`.
- **Also update**: `release-process.md` step-4 smoke test (currently
  notes "`bin/bnc` doesn't accept a `--version` flag") — once wired, the
  release can confirm-by-banner instead of confirm-by-behavior.
- **Discovery**: 2026-06-03, after landing the version redesign
  (`b745c877`); user requested `--version` on all four tools.

---

## MAJOR

### Field access into an anonymous (multi-return tuple) struct miscomputes the LLVM GEP index when a field has alignment padding before it — FIXED 2026-06-03 (binate `5f4a8eaf`)
- **What**: `emitGetFieldPtr` (`pkg/binate/codegen/emit_helpers.bn:118`) maps the
  Binate field index to the LLVM field index via `structLLVMIndex` (which counts
  inserted `[N x i8]` padding fields) **unconditionally**.  But anonymous
  multi-return tuple structs are emitted by `llvmType()` in the non-packed
  `{...}` form **without** explicit padding fields — so for them the Binate index
  already IS the LLVM index.  When such a tuple has a field with
  `PaddingBefore > 0` (a pointer/aligned field following a sub-word field like
  `bool`/`i1`), the mapping overshoots by the number of preceding padding gaps.
- **Symptom**: a `(bool, @errors.Error)` multi-return (e.g. `strconv.ParseBool`)
  generates its anon-tuple destructor `__dtor_anon_bool_unknown` with
  `getelementptr inbounds {i1, %BnIfaceValue}, ... i32 0, i32 2` — index 2 into a
  2-field struct → `error: invalid getelementptr indices`, clang fails.  If the
  overshoot had landed in-bounds it would be a SILENT wrong-field access instead.
- **Root cause**: `emitGetFieldPtr` is the lone `structLLVMIndex` caller missing
  the named-vs-anonymous guard.  The SSA copy paths already do it right:
  `emit_copy_ssa.bn:103` and `emit_copy_ssa_load.bn:85` apply `structLLVMIndex`
  only `if named` (`named = len(t.Name) > 0`) and otherwise use the raw index.
- **Fix**: `emitGetFieldPtr` now gates the `structLLVMIndex` remap on
  `len(baseTyp.ResolveAlias().Name) > 0` — named structs remap past padding
  fields; anonymous tuples use `instr.Index` directly.  Mirrors the
  named-vs-anonymous split already in `emitStoreSSARec`.  `pkg/codegen`
  function-body change (BUILDER-safe).
- **Affects**: LLVM backend (the GEP-index path).  VM uses byte offsets and was
  unaffected (conformance 144 passes on `builder-comp-int` as well as
  `builder-comp`).
- **Discovery**: 2026-06-03, implementing `strconv.ParseBool` (first
  `(bool, @errors.Error)` multi-return).  Had blocked `ParseBool`; the rest of
  the Parse series (`int64`/`uint64`/`float64` first elements — pointer-aligned,
  no padding) was unaffected.
- **Tests**: codegen unit test `TestAnonTupleDtorFieldGepIndex`
  (emit_refcount_test.bn) pins the GEP index; `conformance/144_multi_return_bool_iface`
  covers it end-to-end (green on LLVM + VM).

### Float-literal converter 1 ULP low for ~38+ sig-digit literals just above a tie (round-bit loss) — DEFERRED, blocked on stdlib-via-BUILDER
- **Symptom**: a float64 literal with ~38+ significant digits sitting JUST
  ABOVE a binary rounding tie (e.g. `1.0000000000000001110223024625156540424`)
  converts 1 ULP LOW.  `common.ParseFloatLitToBits` holds the significand in a
  128-bit window and collapses everything below the kept 53 bits into a single
  sticky flag, losing the exact round bit.  LLVM (its own strtod) is correct;
  the VM and native backends share the converter, so they are wrong.
- **Discovery**: 2026-06-03 completeness review of the 128-bit-accumulation
  rewrite; reproduced vs strconv + a big.Float reference (~50% of constructed
  just-above-tie inputs diverge, all +1 ULP in strconv's favor).  Realistic
  literals (≤~37 sig digits) are correct — this is the table-maker's-dilemma
  tail.
- **Test**: `conformance/538_float_lit_tie_roundbit` (passes on LLVM, xfailed
  on the VM modes).
- **Proper fix**: exact rounding via `pkg/std/math/big` (mantInt*10^exp as a
  Nat, extract 53 bits + round-to-even from the exact remainder — Go's
  slow-path).  BLOCKED: the converter is in cmd/bnc's BUILDER tree, which can't
  yet import stdlib `big`; unblocks once stdlib is bundled with BUILDER (below).
  Interim alternative: widen the fixed window (256-bit → correct to ~76 digits)
  — covers all realistic literals but not adversarially complete.
- **Severity**: MAJOR (silent 1-ULP-wrong float constant), narrow (38+ digits
  AND just-above-tie).

### Bundle tier-1 stdlib (pkg/std, pkg/stdx) with the BUILDER; cut a new BUILDER release
- **What**: the BUILDER bnc tarball should ship the tier-1 stdlib so cmd/bnc's
  tree (and any BUILDER-compiled code) can import `pkg/std/...` / `pkg/stdx/...`
  — including `pkg/std/math/big` and a future `strconv.ParseFloat`.  The "BUILDER
  tree can't use stdlib" constraint is purely an artifact of stdlib not being
  bundled (plus a few BUILDER float gaps — we're well past bnc-0.0.1; a release
  is overdue).
- **Unblocks**: the exact-rounding fix above; lets the float-literal converter
  use `big` / `strconv.ParseFloat` directly.
- **Also**: clear the remaining BUILDER float gaps so floats are fully
  BUILDER-compilable, then cut the release and bump BUILDER_VERSION.

### Implement the strconv `Parse...` series (ParseInt / ParseUint / ParseBool / ParseFloat) — LANDED (hex-float follow-up remains)
- **What**: strconv has only the `Format.../Append...`/`Itoa` (number→string)
  direction; add the parse direction.  `ParseFloat` is the correct,
  fully-rounded decimal→double, built over `pkg/std/math/big` (exact
  mantInt*10^exp, round-to-even from the remainder) — the canonical home for
  what `common.ParseFloatLitToBits` approximates.  Once stdlib is
  BUILDER-bundled, the compiler's float-literal converter can route through it
  (or share its core), fixing the round-bit bug above.
- **Plan**: `explorations/plan-strconv-parse.md` (errors via the now-landed
  `@errors.Error`; input `*[]readonly uint8`).
- **Landed (binate)**: full series —
  `ParseBool` + unexported `numError` (`@errors.Error` impl) (`b4bfe843`;
  surfaced + fixed a MAJOR anon-tuple field-GEP codegen bug, `5f4a8eaf`);
  integer core `ParseInt`/`ParseUint`/`Atoi` (`6a91cf5b`); `ParseFloat`
  over `big` — exact, correctly-rounded decimal→binary for f64 and f32
  (`eb4a7aee`); `_` digit separators across all of them (`ea706e43`).
  Verified by Go differentials of the algorithms (integers 9.6M; floats
  2.59M incl. underscores + the over/underflow error kind; 0 divergences),
  exact-bit unit goldens, a Format↔Parse round-trip, and the
  `526_strconv_parse_cross_pkg` cross-package consumer (LLVM/VM/gen2;
  arm32/native via CI — the code is ILP32-safe, all math in uint64).
- **Remaining (follow-up)**: hex-float syntax (`0x1.8p3`) for `ParseFloat`
  (needs a separate binary-exponent path).  Once stdlib is BUILDER-bundled,
  route the compiler's float-literal converter through `ParseFloat`'s core
  to retire the round-bit dtoa bug + the duplicate converter.

### float32 const literal: VM/native load the float64 pattern (wrong value) — DEFERRED, blocked on a new BUILDER release
- **LLVM compile error — FIXED 2026-06-03 (binate `4fd196d0`)**: a float32-typed
  OP_CONST_FLOAT emitted a decimal `float` constant (`fadd float 0.0, 0.1`),
  which LLVM rejects unless exactly representable (`floating point constant
  invalid for type`).  Fixed in `pkg/binate/codegen/emit_instr.bn`: materialize
  the value as a `double` (decimal is valid there) and `fptrunc` to `float`.
- **Still open — VM/native load the wrong VALUE**: a float32-typed const on the
  VM and native backends loads the LOW 32 BITS of the float64 pattern instead of
  narrowing to float32 (e.g. `const C float32 = 0.1` → `bit_cast(int32, C)`
  reads `0x9999999A` not `0x3DCCCCCD`).  A silent wrong value.  `var x float32 =
  0.1` works on the VM (the assignment inserts a narrowing cast); a direct const
  read does not.
- **Fix is written but BLOCKED**: `common.F64BitsToF32Bits` (round-to-nearest-
  even f64→f32 narrowing, verified vs Go across 3.5M cases + a unit test) is in
  `pkg/binate/native/common`.  Wiring it into the VM (`vm/lower_instr.bn`) and
  the native `emitConstFloat`s is a one-liner each, BUT they can't call this NEW
  `native/common` symbol until a new BUILDER release bundles it — BUILDER
  resolves bnc's internal cross-package symbols against its frozen snapshot, so
  a new exported function is "undefined" at every importer (same blocker as the
  round-bit dtoa fix and stdlib-via-BUILDER, above).  Wire it up after the
  BUILDER release.
- **Test**: `conformance/539_float32_const` (passes on the C/LLVM modes, xfailed
  on the VM modes).
- **Discovery**: 2026-06-03, fixing the LLVM compile error surfaced the VM/native
  value bug.  **Severity**: MAJOR (silent wrong float32 const on the VM/native),
  narrow (a directly-read float32 const).

### Self-referential interface method (`Unwrap() @Error` — a method whose return type is its own interface) mis-resolves to a managed pointer → in-package ABI mismatch — FIXED 2026-06-03 (binate `77499153`)
- **Symptom**: an interface with a method that returns its own interface type — e.g. `interface Error { Error() @[]char; Unwrap() @Error }` — miscompiles *in-package* at every dispatch of that method.  The vtable dispatch shim is typed `i8* (i8*)` (return = single pointer), but the method *body* returns a 16-byte `%BnIfaceValue`; the copy-site at the call (`var cause @Error = e.Unwrap()`) RefIncs the result via `extractvalue %BnIfaceValue …, 0`, so LLVM gets `%v6 = extractvalue i8* %v5, 0` → verifier error `extractvalue operand must be aggregate type`.  (Caught here only by that `extractvalue`; a dispatch whose iface-value result is merely stored/forwarded would **silently miscompile** — caller reads 1 word, callee wrote 2.)
- **Root cause (CONFIRMED)**: `collectInterfaceFromDecl` (`pkg/binate/ir/gen_iface_registry.bn`) resolves each method's return type via `resolveTypeExpr(m.Results[0])` (≈line 143) and stores it in `mi.MethodResults` **before** appending the interface to `moduleInterfaces` (≈line 201).  So while resolving `Unwrap`'s `@Error`, `Error` is not yet in the registry → `isInterfaceTypeExpr(Error)` misses → `resolveTypeExpr` falls to `MakeManagedPtrType` (`gen_util.bn:349`) → `i8*`.  `genInterfaceMethodCall` then reads `mi.MethodResults[j]` (`gen_iface.bn:153`) as the dispatch result type, so the shim returns `i8*`.  The method *definition*'s return type is resolved later (in `gen_func`, after all interfaces are collected) and correctly yields `%BnIfaceValue` — hence the in-module mismatch.
- **Why never caught**: `Unwrap() @Error` is the FIRST self-referential interface method in the codebase (an interface method whose return type is its own — or any not-yet-registered — interface).  All prior interface methods return scalars / `@[]char` / managed pointers, where the managed-ptr fallback and the correct type coincide at the LLVM level.
- **Severity**: MAJOR — in-package ABI mismatch for a whole class of interface (anything self-referential: builders, linked nodes, iterator-returns-iterator, and `Unwrap`).  Verifier-loud here, silent on store-only dispatch paths.
- **Fix (landed `77499153`)**: two layers.  `types/check_interface.bn` defines the interface symbol BEFORE resolving its method/parent signatures (matching the `.bni` bni_scope pre-registration, for in-`.bn` decls).  `ir/gen_iface_registry.bn` appends an identity stub to `moduleInterfaces` and points `currentImportAlias` at the interface's package before resolving method results (so a self-ref resolves even in the cross-package `RegisterAllInterfaces` pre-pass), then overwrites the stub.  Defining the interface early would let `interface A : A` resolve A as its own parent, so `resolveInterfaceExtension` now rejects self-extension explicitly.  Tests: `575_self_ref_iface_method` + `TestInterfaceSelfReferentialMethod`.
- **Discovery**: 2026-06-03, implementing `plan-std-errors.md` Part 1 — `pkg/std/errors`'s in-package unit tests (`TestNewUnwrapEmpty`/`TestWrapUnwrapCause`/`TestChainWalk` all call `.Unwrap()`).  Pre-existing latent bug.  Distinct from (but same managed-ptr-fallback symptom as) the cross-package entry below.

### Cross-package function returning `@Iface` resolves the return type to a managed pointer (`i8*`) in the consumer → ABI mismatch — FIXED 2026-06-03 (binate `cb8c0f1a`)
- **Symptom**: a consumer that imports a package and calls a function declared (in the `.bni`) to return a managed interface value — e.g. `errors.New(msg) @Error` / `errors.Wrap(...) @Error` — fails to compile with LLVM verifier error `extractvalue operand must be aggregate type` on `%v6 = extractvalue i8* %v5, 0`, because the consumer lowers the call as `call i8* @bn_pkg__std__errors__New(...)` (single pointer) while the callee's real ABI returns a 16-byte `%BnIfaceValue` (register pair).  The consumer's own refcount/copy machinery *correctly* treats the OP_CALL result as an interface value (hence the `extractvalue …, 0` to RefInc the data field), so the call-return-type and the copy machinery disagree inside one module.
- **Root cause (CONFIRMED)**: `isInterfaceTypeExpr` / `ifaceTypeForName` (`pkg/binate/ir/gen_iface.bn`) resolve a **bare** interface name (`te.Pkg` empty) by looking it up in `moduleInterfaces` only under `currentModulePkgPath` (the *consumer's* package) — never under `currentImportAlias` (the package whose `.bni` decls are currently being registered, `gen_import.bn:registerImportFieldsAndFuncs`, which sets `currentImportAlias = alias`).  The imported interface is registered (by `collectInterfaceFromDecl`) under its full path (`resolveImportPkg(alias)` = `pkg/std/errors`).  So while registering `errors.bni`'s `func New(...) @Error`, `resolveTypeExpr(@Error)` calls `isInterfaceTypeExpr(Error)` → lookup `("main","Error")` MISS → falls through to `MakeManagedPtrType` (`gen_util.bn:349`) → `llvmType` = `i8*`.  The struct / `TEXPR_NAMED` path already consults `currentImportAlias` (`gen_util.bn:271–283`, mirrored in `gen_const.bn:85`); the interface path does **not** — that asymmetry is the entire bug.
- **Why never caught**: errors is the FIRST cross-package function whose return type is an interface value.  The mis-resolution is INVISIBLE for managed-pointer (`@T`) and managed-slice (`@[]T`) returns — those lower to `i8*` / `%BnManagedSlice` whether resolved correctly or as the managed-ptr fallback — and strconv/big return exactly those.  An interface value is the first return type where correct (`%BnIfaceValue`, 2-word) and fallback (`i8*`, 1-word) diverge.  In-package compilation is fine (there the interface is under `currentModulePkgPath`), so `pkg/std/errors` itself builds; only the consumer mis-resolves.
- **Severity**: MAJOR — a cross-package ABI mismatch.  Here the LLVM verifier happens to reject it (the copy machinery's `extractvalue` on an `i8*`); on any codegen path that does NOT extractvalue the result (e.g. a `@Iface`-returning function whose result is only stored/passed, not retained at the call site) it would be a **silent miscompile** — caller reads a 1-word return, callee wrote a 2-word value.  Also affects `*Iface` returns by the same path.  (Almost certainly also `@func` / `*func` returns from a cross-package function whose signature spells the func-value type via a NAMED alias — not the structural `@func(...)` form, which resolves context-free — though unconfirmed.)
- **Fix (landed `cb8c0f1a`)**: in `isInterfaceTypeExpr` and `ifaceTypeForName` (`gen_iface.bn`), a bare name that misses under `currentModulePkgPath` now also tries `currentImportAlias` (keying the produced `TYP_INTERFACE` on the resolved full path), mirroring `gen_util.bn`'s `TEXPR_NAMED` arm.  Test: `576_cross_pkg_iface_return` (and the `577_std_errors` cross-package suite).
- **Discovery**: 2026-06-03, implementing `plan-std-errors.md` Part 1 (`pkg/std/errors`).  Pre-existing latent bug, exposed by the first cross-package interface-value return.

### Multi-return of a `@func` component was miscompiled — capture lost (LLVM) + invalid closure-data kind (VM) — FIXED 2026-06-03
- **Was**: a function returning a tuple with a function-value component — `func two(...) (int, @func(int) int)` — was wrong-coded for the `@func` slot.  `two(false)` returns `(0, adder(10))` (a capturing `func(x){ return x + n }`, n=10); `f(5)` then gave `5` not `15` in LLVM (capture `n` read as 0) and crashed `vm: unsupported function-value data kind: 0` in the VM.
- **Fix — two independent halves**:
  - **LLVM/IR (capture loss)**: fixed by the multi-assign managed-target refcount work (binate `0b3f4abe` + `6c4d45b0`) — the `@func` component was under-retained through the multi-value path, so the closure record was freed before invocation.  (Landed independently for the multi-assign CRITICAL bug; it also closed the LLVM half here.)
  - **VM (invalid closure data)**: binate `98f65edb`.  Once the closure record was valid again, the only remaining issue was the VM packing a 16-byte address-based `@func` component as one scalar word — the same shape as the iface case `578`.  Generalized `isVMInterfaceValue` → `isVMAddressAggregate` (iface + func) for both the multi-return result-layout classification and the EXTRACT pointer-mode.  (578 deliberately scoped to iface because the LLVM half was still broken then; with that fixed, extending to `@func` completes it cleanly.)
- **Tests**: `579_multi_return_func_value` (empty + capturing `@func` component, reassignment, invocation) — green in all six default modes.  Single-return `@func` stays pinned by 534/542/555.
- **Discovery**: 2026-06-03, while fixing the `@Iface` multi-return VM bug for `plan-std-errors.md` (the `(T, @Error)` error-return pattern).  Was pre-existing.

### A managed-slice-of-interface-value (`@[]@I`) constructed via a slice LITERAL leaks its elements
- **Symptom**: `var s @[]@Foo = @[]@Foo{makeFoo(i)}` (a slice literal of interface values), dropped at scope exit, never RefDec's its `@Foo` elements — the receiver (and its managed fields) leak (rc 1→2, never back to 1).  The element-ASSIGN form (`var s @[]@Foo = make_slice(@Foo, n); s[0] = makeFoo(i)`) is balanced; only the literal leaks.
- **Root cause (from `--emit-llvm`)**: both forms call the slice's `__dtor_ms_unknown`, which RefDec's the slice backing with a NULL dtor and does not walk the interface-valued elements (no per-element iface dtor).  So the element-type isn't propagated into the managed-slice dtor selection for the literal shape.  This is the `@[]@I` feature area already flagged as incomplete by `440_iv_in_slice_mgd` ("compiles, but writes into the iv slot segfault").
- **Discovery**: 2026-06-03 adversarial coverage audit of the `@Iface` refcount lifecycle.  Likely **pre-existing** / part of the known-incomplete `@[]@I` support — NOT a regression in the core refcount wiring (the common copy-sites — return / var-init / assign / field / array-element / managed-slice-element-assign / composite / struct-copy / param / deref — are all rc-balanced, pinned by 553/554/556/560/567).
- **Status**: tracked, not fixed.  Lower priority (exotic shape in a known-incomplete feature); fix alongside the broader `@[]@I` completion (440).

### Bytecode VM `@Iface` (interface) value handling — two VM bugs — FIXED 2026-06-03
- **Part A — single interface-value return not copied back → "call through nil interface value"** (binate `511e1395`).  Interface values are 16-byte address-based VM stack slots.  `lowerReturn` set BC_RETURN's copy-back size only for `isMultiWordField` types (struct / slice / array) — it omitted interface values, so a single `@Iface` return dangled in the reclaimed callee frame and the next call clobbered it; `consume(makeFoo(i))` (an iv call result passed directly as an arg) then panicked `vm: call through nil interface value` in `-int` only (LLVM + native don't use this lowering).  Fix: set the copy-back size for `TYP_INTERFACE_VALUE` / `_MANAGED` single returns too.  Pinned by `560_iface_return_call_arg` (green all modes).
- **Part B — interface-value receiver dtor crashed on RefDec-to-zero** (binate `5de3d09d`, the direct analogue of the `@func` capture-record dtor `0a0d00af`).  `BC_IFACE_DTOR` produced the receiver dtor's 1-based func index, but `BC_REFDEC_INLINE_FAST` consumes its dtor input as a func-value HANDLE — so an interface value that was the *last* holder of a managed-field receiver bit_cast the small index to a pointer and crashed (520; the dtor arms of 554 / 556).  473 hid it because its iv lives in a nested block the receiver outlives, so its RefDec never reached zero.  Fix: `BC_IFACE_DTOR` hands `BC_REFDEC` the dtor func's handle via `ensureHandle` (the same `{Vtable, ClosureRec{VM_CLOSURE_REC, FnIdx}}` the `@func` path uses); the existing iterative-push arm runs the receiver dtor and frees it via `freeOnPop`.
- **Result**: `520_iface_dtor_callee_sole_ref` (a standing `-int` red) is green; `554_iface_refcount_balance` and `556_iface_struct_field_balance` un-xfailed in all VM modes; `-int` suite 478/0.  Both were `pkg/vm`-only (codegen always emitted correct IR; LLVM + native were already correct).

### Conformance int-int mode: `136_grouped_imports` + `383_cross_pkg_iface_dtor` fail with "pkg/builtins/rt not found"
- **Symptom**: on `builder-comp-int-int` (the double-VM default mode),
  `136_grouped_imports` and `383_cross_pkg_iface_dtor` fail at compile time
  with `package "pkg/builtins/rt" not found`.  Both PASS on `builder-comp-int`
  and `builder-comp-comp-int`; the other ~468 int-int tests pass.
- **Pre-existing**: confirmed on clean `17c722d1` (reproduced with the
  pre-float-fix VM tree), so NOT caused by the float-constant work; it is a
  recent main regression in the int-int package-resolution path.
- **Root cause (unknown)**: only certain multi-package tests can't resolve
  `rt` in the int-int pipeline; needs investigation of how that mode locates
  the `rt` package (vs the single-int / comp-int modes that succeed).
- **Discovery**: 2026-06-03, full-suite regression sweep while landing the
  float-constant fix (536).
- **Severity**: MAJOR — a default conformance mode is red, masking real
  coverage on those tests.

### Managed-interface-value refcount lifecycle is unwired — FAMILY of leaks + 1 UAF — IN PROGRESS / NEEDS DECISION
- **Root cause (CONFIRMED)**: managed interface values (`@Iface`) were added to the language, but the refcount *lifecycle* machinery in `pkg/binate/ir` was only ever wired for managed-ptr / managed-slice / struct — **never iface**.  Three distinct sites are missing the `isManagedIfaceValueType` case, producing three bugs:
  1. **UAF — return a named-local `@Iface`** (`func f() @I { var s @I = q; return s }` → `f().m()` reads freed data).  `gen_return.bn`'s Axiom-3 retain loop has no iface case, so a *borrowed* (loaded) iface return is never retained for the caller; the source local's scope-exit RefDec frees it.  (The original target bug; found 2026-06-03 building `plan-std-errors.md` Part 1, where `errors.New`/`Wrap` return `@Error`.)
  2. **LEAK — discarded / non-moved iface temp** (`makeFoo(inner)` as a bare statement → inner rc 1→2, dtor never runs).  `emitTempCleanupBody` (gen_util_refcount.bn:292) RefDec's managed-ptr/slice/struct temps but **skips iface temps**, even though they are registered in `ctx.Temps` (gen_call.bn:252).  **Pre-existing**, independent of the return path (reproduces on Part-0 `bnc`).
  3. **LEAK — reassigning an `@Iface` local** (`var f @I = a; f = b` → `a`'s old iface value is overwritten without a RefDec → leaked).  `gen_assign` doesn't RefDec the previous managed-interface value.  **Pre-existing.**
- **Why these were never caught**: NO conformance test returns / discards / reassigns a managed interface value — every `@…` test uses managed *pointers* (`@Counter`/`@Item`/…).  520 is the only test that returns an `@Foo`, and only via the *boxed-on-return* shape (which happens to be balanced).
- **Verified shape matrix** (rt.Refcount before/after, 8 return shapes, adversarially adjudicated): balanced *before any fix* = boxed-on-return (A/520), call-result (C), field-extract (E), multi-return (H), empty (G).  Broken *before any fix* = named-local (B) and param (D) → the UAF.  A naive unconditional `gen_return` RefInc fixes B/D but **over-retains the already-owned producers** (C call-result, E field-extract) → new leaks.  A narrow `rv.Op != OP_IFACE_VALUE` gate still leaks C/E (call/extract are owned too).  → the discriminator is "borrowed load vs owned producer", which the temp/local machinery already tracks for `@T`.
- **Fix (chosen: principled / uniform, 2026-06-03)**: wired `@Iface` through the refcount machinery everywhere `@func` / `@[]T` already go.  Added `isFreshManagedIfaceValue` (gen_refcount_pred); iface RefDec in `emitTempCleanupBody`/`Since`; the consume-fresh / RefInc-borrowed hybrid at every copy-site (return / var-init / `:=` / assign / index-range / composite / slice-literal element); iface struct/array copy+dtor field cases (gen_copy_emit, gen_dtor_emit_bodies); registration of iface call/method results (gen_call, gen_method); and `NeedsDestruction → true` for `TYP_INTERFACE_VALUE_MANAGED` (types_query — was making the struct-field handling dead code).
  - **Params/args use the MOVE model, NOT the copy model** (this is the subtle part): an iface param gets NO entry RefInc; the caller MOVES a fresh arg in via `consumeTemp` or RefInc's a borrowed one (gen_call/gen_method arg sites), and the param's scope-exit RefDec releases that single ref.  Reason: the bytecode VM passes a 2-word iface value on transient `vm.SP` that the call reclaims, so the copy model (caller retains + cleans its arg COPY post-call) reads freed stack and crashes (370/383 in `-int`).  `@T` can use the copy model only because it's 1 word in a stable local.
- **Verification**: all 16 lifecycle shapes (return×6 / var-init / assign / composite / struct-by-value-copy / multi-consumer / discard / reassign / 1000-iter loop / self-assign) rt.Refcount-balanced, adversarially adjudicated.  Conformance 370/383/473/521/545/546 green in builder-comp / -int / -comp-comp / native aa64+x64.  (520 still fails in `-int` = the separate pre-existing "call through nil interface value" VM bug; 383 fails only in `-int-int` = the pre-existing cross-package double-interp loader limit, which also fails 136_grouped_imports.)
- **Why MAJOR/critical**: #1 is a silent UAF; #2/#3 are silent leaks (violate the "compiler must NEVER leak" invariant).  Blocks `plan-std-errors.md` Part 1.
- **Tests**: 546 (method-value, catches UAF) exists; add a new rt.Refcount-*balance* conformance test (catches leaks) for the return / discard / reassign / param shapes before landing.
- **Status**: FIX IMPLEMENTED + verified on worktree (branch `work-1`); adding the balance conformance test, then full regression + cherry-pick.  Part 0 (`present`) already landed.  See `plan-std-errors.md`.

### Multi-value return assignment to `_` leaks the discarded managed component(s) — FIXED 2026-06-03 (binate, pending cherry-pick)
- **Was**: `_, n = f()` where `f` returns `(@T, int)` (or `@Iface`, `@[]T` — any managed type) never RefDec'd the `_`-discarded managed result → +1 leak per execution.  Root cause: the multi-assign loop (`genAssign`, `gen_control.bn`) ran the Axiom-3 copy-RefInc for the `_` component unconditionally, but a blank target stores nothing (`lookupVar("_") == nil`), so that RefInc had no matching RefDec.  (The single-value `_ = g()` path doesn't leak because its RefInc is *inside* the `ptr != nil` guard.)
- **Fix**: skip a blank-identifier target entirely in the multi-assign loop (`if lhs.Kind == EXPR_IDENT && isBlank(lhs.Name) { continue }`) — no copy-RefInc, no store; the call-result temp's dtor RefDec's the owned ref at end of statement.
- **Test**: `conformance/570_blank_discard_managed_balance` (loop of 100 discards; b's refcount returns to baseline 1, was 101 pre-fix).  Verified to fail on the unfixed compiler.
- **NOTE — the BOTH-bound form `a, n = f()` is NOT balanced** (the old entry wrongly claimed it was — it had only been checked for `@T` bound to a fresh-nil var).  See the two multi-assign defects in the CRITICAL section.

### bnlint typechecks dependency BODIES, not just signatures — FIX LANDED 2026-06-03 (binate `3fcfdf8c`); deployment pending next BUILDER bump
- **Status**: source fix LANDED (binate `3fcfdf8c`, + composition test
  `a079621d`).  Takes effect in hygiene only after BUILDER_VERSION is bumped
  to a snapshot containing it — the bundled bnlint is what hygiene runs.
- **Symptom**: linting package A that imports package B re-typechecks B's
  function *bodies*, not just its exported signatures.  A body-level type
  error in B then surfaces when linting A — false coupling.  Concrete
  trigger: `pkg/binate/vm`'s `_func_handle(rt._Package)` (valid, but newer
  than the BUILDER-bundled bnlint can typecheck) made `pkg/binate/repl` and
  `cmd/bni` *also* fail lint purely because they import vm, forcing the
  `scripts/hygiene/lint.sh` skip to cascade across all three.
- **Root cause**: `cmd/bnlint/main.bn` (`lintPackages`) loops over ALL loaded
  packages (`ldr.Order` — targets AND transitive deps) and calls
  `c.CheckPackage(...)` on each, which runs Pass 1 (`collectDecls`) + Pass 1.5
  (`checkAllImplsSatisfaction`) + Pass 2 (`checkDecls`, body checking).  The
  *lint* loop below only iterates the target `pkgs`, so it already
  distinguishes targets from deps — the body-checking of deps is incidental
  over-reach.  Dependents only ever consume a dep's exported surface, which
  `collectDecls` + `registerPackage` provide; body-checking a dep adds
  nothing for the dependent.
- **Fix (landed)**: `pkg/binate/types/checker.bn` gained `CheckPackageDecls`
  — Pass 1 (`collectDecls`) + `registerPackage`, skipping Pass 1.5/2 —
  sharing `checkPackageImpl(checkBodies)` with `CheckPackage`.
  `cmd/bnlint/main.bn` body-checks (`CheckPackage`) only the lint targets and
  registers transitive deps decls-only (`CheckPackageDecls`), routed by
  `isLintTarget`.  Removes redundant re-checking and stops a dep's body
  errors from leaking into importers.  Once deployed, shrinks the present
  skip from {vm, repl, bni} to {vm}.
- **Severity**: major for the *linter's* robustness (false failures + wasted
  work); linter-only, no effect on generated code.
- **Deployment**: takes effect after a BUILDER_VERSION bump — same release
  that ships the `_Package` typecheck support (Phase B entry above).
- **Tests (landed)**: `pkg/binate/types/checker_test.bn` —
  `TestCheckPackageDeclsSkipsBodies` (decls-only reports no body error; full
  check does), `TestCheckPackageDeclsRegistersScope` (exported surface still
  registered), `TestCheckPackageDeclsDependentResolves` (a dependent resolves
  a decls-only dep AND its body error doesn't leak).  `cmd/bnlint/main_test.bn`
  — `TestIsLintTarget`.

### Remove the `pkg/binate/vm` lint skip after the next release
- **What**: `scripts/hygiene/lint.sh` temporarily skips `pkg/binate/vm`,
  `pkg/binate/repl`, and `cmd/bni` (`LINT_SKIP`).  The BUILDER-bundled bnlint
  (bnc-0.0.6) predates the `_Package` selector + `_func_handle` typecheck
  support, so it aborts at the typecheck pass on `_func_handle(rt._Package)`
  / `@reflect.Package` in `vm/extern_register_std.bn`; repl + bni cascade in
  because bnlint typechecks dependency bodies (entry above).
- **Removal condition**: drop the whole `LINT_SKIP` block once
  `BUILDER_VERSION` is bumped to a snapshot that includes BOTH (a) the
  `_Package` selector + `_func_handle(pkg._Package)` typecheck support
  (binate `feadde2c` and predecessors), and (b) the bnlint dep-body fix
  (entry above — landed in source as binate `3fcfdf8c`, awaiting only the
  BUILDER bump).  With (a), `vm` lints; with (b), the repl/bni cascade is
  gone.  A from-source bnlint already lints all three cleanly today.
- **Marker**: the skip block carries a `TODO(remove after next release)`
  pointing here.

### Native aa64 self-host lane failed to BUILD — `duplicate symbol` (62 dups) — FIXED 2026-06-03 (binate, pending cherry-pick)
- **Was**: `builder-comp_native_aa64-comp_native_aa64` failed at
  compiler-build (link) time, `ld: 62 duplicate symbols` (e.g.
  `_bn_pkg__binate__types__predeclaredNil`,
  `_bn_pkg__binate__ir__moduleGlobals`, …) — each a top-level package var
  defined in BOTH `main.o` and its owning package's `.o`.  The lane never
  reached running a test.
- **Root cause (the static-managed-sentinel hypothesis was WRONG)**:
  `ir.Global` carries `IsExtern` (an imported `.bni` extern var, defined by
  its owner's TU).  The LLVM backend honors it — emits `external global`
  (declaration only).  The NATIVE backends' `emitGlobals`
  (`pkg/binate/native/{aarch64,x64}`) did NOT check `IsExtern`: they emitted
  a strong definition for EVERY global, so every importing TU carrying an
  IsExtern entry re-defined the owner's symbol → duplicate-symbol link
  failure.  The recent cross-package extern-var feature (binate `be49c0a9`
  etc.) populated modules with IsExtern globals, tipping the latent native
  gap into a build break.
- **Fix**: native `emitGlobals` (both backends) now `continue`s on
  `g.IsExtern` (no definition — the reference resolves to the owner
  cross-object, exactly like LLVM's `external global`).  Also open the data
  section LAZILY (only once a real non-extern global is emitted): a module
  whose globals are ALL extern was otherwise leaving an empty data section
  that the Mach-O writer turned into a malformed load command (the
  `548/552/558` cross-pkg link failures).  Unit tests:
  `TestEmitGlobalsSkipsExtern` in both backends.
- **Result**: the aa64 self-host lane BUILDS and runs — `491 passed, 0
  failed` (xfails skipped).  `534` (the `@func` fix) passes on native aa64;
  `541` stays xfailed (native float gap).
- **Newly-exposed native-aa64 gaps (xfailed + tracked; NOT regressions —
  these tests never ran before the lane built)**: `550` (@func
  capture-record refcount wrong on native), `569` (float captured in a
  closure reads 0 — native float gap, 541-family), `559`/`561` (cross-package
  MANAGED extern var — already xfailed on every mode; needs the imported
  type's dtor).  `550`/`569` are the genuinely native-specific ones worth a
  follow-up.  (`551` `&G`-as-rvalue is now FIXED — see entry below.)

### `551`/`573` native-aa64 `&G`-as-rvalue — FIXED 2026-06-04 (binate `9a0f4f9a`)
- **Was**: taking a top-level global's address as a VALUE (`&G` as an
  rvalue: store value, call arg, return value, comparison operand,
  bit_cast source) was silently wrong on the native aarch64 backend.  `&G`
  is the IsGlobalRef pseudo-instr (ID -1, no SSA register); `getOperand`
  missed every lookup and returned -1, so the value-operand site dropped
  the operand (call args / return) or stored garbage.  Native handled
  IsGlobalRef only in ADDRESS-operand positions (load/store target, GEP
  base) via `emitGlobalAddr`; value positions were unwired.  The native
  analogue of the LLVM bug fixed in `99655f4e` (which rendered `%v-1`).
- **Fix**: new `emitValOperand` (aarch64_regmap.bn) — the value-operand
  analogue of `getOperand`: materializes an IsGlobalRef into a fresh
  scratch via ADRP+ADD, else defers to `getOperand`.  Routed every
  value-operand site through it (OP_STORE value; direct / indirect /
  func-value / handle call args; OP_RETURN single / sret-multi / packed;
  comparison operands; OP_BIT_CAST source); threaded `pkgName` into
  emitCallIndirect / emitCallFuncValue / emitCompare.  Two globals in one
  instruction (`&G == &H`) each get their own scratch — no clobber
  (contrast the VM's shared globalReg, 573's still-open `-int` bug).
- **Result**: `551` un-xfailed on native aa64; `573` (`return &G,&H` /
  `&G == &H`) — which was failing native aa64 UNMARKED — now passes there
  too.  Full native aa64 lane: 498 passed, 0 failed.  Unit tests:
  `aarch64_global_ref_test.bn`.  573's VM (`-int`) xfails are unaffected
  (the separate shared-globalReg bug, another worker's).
- **x64 parity still OPEN**: the structurally-identical gap exists in
  `pkg/binate/native/x64` value-operand sites (emitStore value, the call /
  return / compare emitters) — no x64 native lane in CI catches it, so it
  is a latent silent-wrong-value-operand bug there.  Fix with the same
  `emitValOperand`-style helper (a `getValOperand` mirroring the LLVM
  `emitValRef` fix); the x64 root-cause + site map is already scoped.

### `550` native @func capture-record refcount — FIXED 2026-06-04 (binate `7dab4be7`; split `879fe3a1`) — pending cherry-pick
- **Symptom**: a capturing `@func`'s captured managed value was not
  released when the closure died on native aa64; `conformance/550` read
  rt.Refcount 2 instead of 1.  Green on every other mode (VM via
  `0a0d00af`; LLVM via the func-value vtable dtor slot).
- **Root cause**: native `emitFuncValueVtables` always wrote the
  vtable's slot-0 (dtor) as 8 zero bytes, even for a capturing managed
  closure whose struct needs destruction.  `fv.vtable[0]` null ->
  OP_FUNC_VALUE_DTOR yields null -> rt.ZeroRefDestroy skips the dtor ->
  the captured value's ref leaks.  The OP_FUNC_VALUE_DTOR load and
  emitRefDecInline forwarding were already correct; only slot-0 wiring
  was missing.
- **Fix**: new `emitFuncValueVtableDtorSlot` (aarch64) /
  `emitFuncValueVtableDtorSlot_x64` emit slot 0 as a pointer to the
  closure-struct dtor's HANDLE (`___handle.<dtor>`) when
  `lookupClosureFuncAA64(mod, seen[i])` returns a func that is
  `IsManagedFuncValue && ClosureStruct != nil &&
  ClosureStruct.NeedsDestruction() && len(ClosureStructDtorName) > 0`;
  else 8 zero bytes (unchanged).  Mirrors `emitFuncValueVtableDtor` in
  pkg/binate/codegen.
- **Symbol-convergence note (the part the pre-fix plan got slightly
  wrong)**: `f.ClosureStructDtorName` is the UNqualified dtor name
  (`__dtor_<closure>`), NOT the dtor func's qualified `Name`
  (`<pkg>.__dtor_<closure>`).  They still resolve to ONE symbol because
  `handleSymFor` routes through `mangle.FuncName(pkgName, ...)`, which
  folds a same-package qualifier prefix and a pkgName-prefixed
  unqualified name to the identical `bn_<pkg>__<dtor>` — so slot 0
  references exactly the `___handle.<dtor>` triple that
  collectFuncValueRefs' IsLinkOnce pre-pass already emits.  No new
  global, no dangling reference.  (Used the EXISTING `lookupClosureFuncAA64`,
  which returns the closure func directly — the planned
  `lookupModuleFuncAA64` was unnecessary.)
- **x64 parity**: same fix in `pkg/binate/native/x64/x64_funcvalue.bn`
  (no CI lane, but had the identical latent capture-leak).
- **Hygiene**: the +45-line fix pushed `aarch64.bn` over the 500-line
  cap, so the func-value emission was first extracted to
  `aarch64_funcvalue.bn` (mirrors `x64_funcvalue.bn`) in `879fe3a1`.
- **Tests**: 550 un-xfailed on native aa64 (verified fail pre-fix /
  pass post-fix); `aarch64_funcvalue_test.bn` pins slot-0 shape (dtor
  handle for a capturing managed closure, null otherwise, null for the
  *func and no-managed-capture forms).

### `526_strconv_parse_cross_pkg` crashes on native aa64 — NEW, UNMARKED failure (needs investigation)
- **Symptom**: `conformance/526_strconv_parse_cross_pkg` (added with the
  strconv `Parse*` series, `6a91cf5b`) FAILS on
  `builder-comp_native_aa64-comp_native_aa64`: expected the 14-line
  ParseInt/ParseUint/ParseBool/Atoi transcript, **actual is EMPTY** —
  the program prints nothing, i.e. it crashes/aborts before (or during)
  the first `println`.  Green on the default C/LLVM and VM modes.
- **Discovery**: surfaced 2026-06-04 by the first full native-aa64
  `--check-xpass` lane run (the flag was previously mis-positioned after
  the mode, so the lane had never actually executed end-to-end).  NOT
  caused by the `550` work — 526 uses no closures/func-values.
- **Status**: no `.xfail` marker yet → it makes the native-aa64 lane
  RED.  Pre-existing on main.  Root cause UNKNOWN — candidates: native
  int64/uint64 lowering, the `@errors.Error` multi-return + `present()`
  path, or cross-package const/string handling in the strconv Parse
  surface.  Empty output (vs wrong output) points at an early
  crash/abort.  **Needs: triage to root-cause vs. xfail-and-defer
  (user's call — it's a real native-backend miscompile, not a workaround
  candidate to silently mark).**

### Native backends mis-lower float consts/returns — `541` silently reads 0 (Phase A float-const gap on the native code generators)
- **Symptom**: `conformance/541_cross_pkg_const_float` passes on the
  default C/LLVM-backed modes but **fails on the native aarch64 backend**
  (`builder-comp_native_aa64-comp_native_aa64`): expected `7 -3 7 -3 9`,
  actual `7 0 0 …`.  Two distinct silently-wrong cases (both → `0.0`):
  1. **Negative float const** — `cfg.NegHalf` (`= -1.5`) read cross-package
     reads as `0.0` (line 2).  The positive sibling `cfg.Ratio` (`= 3.5`)
     read the same way (cross-pkg `EXPR_SELECTOR`) is **correct** (line 1 → 7),
     so positive `EmitConstFloat` + float-mul + `cast(int, float)` all work
     on the native backend; only the **negative/unary-minus-folded** float
     literal mis-lowers.
     **FIXED 2026-06-03 (binate `5281b138`)**: the root cause was
     `common.ParseFloatLitToBits` (the shared text→bits converter used by
     every native backend) silently dropping a leading `-` in the folded
     literal text and returning 0; it now honors the sign.  Verified at unit
     level (`TestParseFloatSigned`) and via `541` on the VM modes (the VM was
     made to route through the same converter).  The native aa64 *lane* can't
     confirm end-to-end because it no longer links (the duplicate-symbol entry
     above), but the converter is the shared piece and native's emit path was
     already correct for positive consts.  Case 2 below is still open.
  2. **Float function return** — `cfg.Scale()` (returns `Ratio` via an
     in-package `EXPR_IDENT` read) reads as `0.0` (line 3), ditto
     `cfg.NegScaled()` (line 4).  Either the native float-return ABI (value
     should arrive in `d0`, caller reads 0) or the in-package `EXPR_IDENT`
     float-const read is broken — 541 alone can't disambiguate (need a
     direct-return-vs-direct-read probe).
- **Discovery**: 2026-06-03, running `./conformance/run.sh
  builder-comp_native_aa64-comp_native_aa64` (the aa64 lane the user
  watches).  `541` has **no xfail markers** and its own header explicitly
  intends cross-backend stability ("cast-to-int keeps the expected output
  stable across backends"), so this is a genuine native-backend correctness
  hole, not an intended skip.
- **Why MAJOR**: silent wrong float values (reads 0 instead of the real
  value) on a shipping backend — the exact silent-miscompile class.  The
  IR-gen Phase A fix (above, line ~462) is correct at the IR level; the gap
  is in the **native code generators** (`pkg/binate/native/{aarch64,x64}`),
  which Phase A never validated (it was checked on the C/LLVM modes only).
- **Unverified / TODO**: (a) confirm whether `native_x64*` modes fail the
  same way (likely — same native-float codegen path; not run here, no x64
  host) and add their xfails too; (b) disambiguate case 2 (float-return ABI
  vs in-package float-const read) with a minimal probe; (c) `534` (the
  `@func` bug) also fails unmarked on the aa64 lane — its xfails cover only
  the 6 default modes, so the cross-compile lanes need 534 xfails for an
  honest suite.
- **Tracking**: proposed xfail `541_cross_pkg_const_float.xfail.builder-comp_native_aa64-comp_native_aa64`
  (one-line: native aa64 mis-lowers negative float const + float return → 0).

### `rt.Exit` paradigm: `exit` vs `abort`/`panic` — DISCUSS
- `rt.Exit` (→ libc `exit`) is the wrong model in general: process exit
  is meaningless in an embedded/freestanding environment, and the
  runtime mostly invokes it for *abort* conditions (OOM, bounds-fail,
  refcount corruption). `abort`/`panic` is likely the right paradigm.
- Surfaced 2026-06-03 alongside the `__c_call`/drop-libc work; that
  change preserves `Exit`→`exit` behavior, so this is a clean,
  independent follow-up. Needs a design discussion before any change.

### `__c_call` should support void returns
- Today `__c_call` "requires a return type" and `checkCCall` rejects
  void ("void and struct returns not yet supported"). So calling a void
  C function (`free`, `exit`) means declaring a dummy scalar return
  (e.g. `int`) and discarding it as a bare statement — see the
  placeholders in `impls/core/libc/pkg/builtins/rt/rt.bn`
  (`__c_call("free", int, ptr)` / `__c_call("exit", int, code)`).
- **Fix**: accept a void return spelling for `__c_call` (and a bare-
  statement form), so void C calls don't carry a misleading return type.
- Surfaced 2026-06-03 by the drop-libc work.

### Float function-values are silently miscompiled in the VM (`-int` modes) — FIXED on main (`7abc3809`)
- **Plan**: [`plan-float-arg-shim.md`](plan-float-arg-shim.md). Design A
  (uniform all-`int` shim ABI) approved + landed on main `7abc3809`
  (2026-06-03), verified across all default LLVM modes + codegen/vm unit
  tests, hygiene clean. Unblocks the bootstrap native-only work below.
- **Canonical repro**: `pkg/binate/vm` `TestExternFloat*ViaRegistry` (a
  bytecode caller invoking a native float extern via the registry) — the
  only path that hits the bug; user float func-values in `-int` are
  bytecode/trampoline (all-int VM slots) and round-trip fine without the
  fix, so the conformance 562-566 tests are compiled-mode reshape guards,
  not the repro.
- **Symptom**: a function-value call with a `float64`/`float32` arg or
  return produces the wrong value in any `-int` (bytecode VM) mode.
  Compiled modes are correct. Currently masked: there is *zero* test
  coverage for float func-values.
- **Root cause**: VM dispatch routes through `rt._call_shim_scalar(fn,
  data, a0..a6 int)` — an all-`int` `OP_CALL_INDIRECT`. The native
  backend only places an arg in an FP register when the IR operand type
  is float, so a float arg's bits land in a GP register while the natural-
  typed shim reads `d0`/`xmm0`. Float returns break symmetrically
  (aarch64 indirect has no float-return path).
- **Fix (Design A)**: int-ify float **scalars** in shim signatures and
  `bitcast` `i64↔double` / `i32↔float` at the shim boundary; the compiled
  call site (`emitCallFuncValue`) bitcasts to match. VM/`rt`/native
  unchanged; no-op for non-float signatures. Pure `pkg/binate/codegen`
  change. Conventions: exact-width slots (f64→i64, f32→i32), aggregate
  retbufs stay natural-typed, one shared `shimIntSlotType` predicate so
  shim and call site can't disagree (the only silent-miscompile path).
- **Why now**: prerequisite for the bootstrap injection below
  (`bootstrap.formatFloat` is a native extern once bootstrap is native-
  only) — without it, `conformance/287_float_println` regresses in `-int`.
  Per Bug Discovery Protocol, the new func-value-float tests are the
  tracked reproduction. Surfaced 2026-06-03 by the bootstrap work.

### Inject `pkg/bootstrap` into the VM + convert I/O to `__c_call` — Phase 1 DONE; Phase 2 DEFERRED (BUILDER-runtime coupling)
- **Phase 1 LANDED** on main (`a7fabc7a`, 2026-06-03): bootstrap is now
  native-only in the VM — cmd/bni skips lowering it, the format helpers
  (formatInt/Int64/Uint/Bool/Float, Itoa) are registered as externs in
  both `registerBootstrapExterns` copies, bootstrap's bytecode unit tests
  are xfailed in the 3 `-int` modes, and `extern_register_std_test` guards
  format-helper registration.  `formatFloat` (the first native float
  extern) dispatches via the all-int shim ABI (`7abc3809`).  Verified:
  `287_float_println` green in `-int`; full `builder-comp-int` /
  `-comp-int` / `-int-int` clean but for pre-existing failures.
- **Plan**: [`plan-bootstrap-ccall.md`](plan-bootstrap-ccall.md). The
  rt-drop-libc pattern applied to bootstrap: eliminate the hand-written
  `bn_pkg__bootstrap__*` I/O glue in `binate_runtime.c` by converting it
  to `.bn` + `__c_call`, and make bootstrap native-only in the VM.
- **Phase 2 DEFERRED (2026-06-03), possibly indefinitely**: converting
  the I/O to `.bn` *adds* `bn_pkg__bootstrap__{Open,Read,Write,Close,Exit}`
  defs that collide with BUILDER's pinned runtime (gen1 links it,
  `build-compilers.sh:55-62`) → duplicate-symbol link failure building
  gen1. It's a runtime-ABI change, so it can only be done *during a
  BUILDER bump/release* (the new BUILDER's runtime omits the I/O), not in
  the pinned-BUILDER tree. The trivial+moderate `.bn` code was written +
  reviewed (correct modulo the link blocker) and is preserved in
  plan-bootstrap-ccall.md's appendix. `Stat` is a further defer (struct
  stat platform divergence → needs a per-libc-platform impl split). It may
  be better to *eliminate* these bootstrap I/O functions (subsumed by a
  real stdlib `io`) than convert them — so this may never be worth doing.
- **Harder than rt**: `__c_call` is scalar/pointer-only, but bootstrap's
  I/O takes slices + returns managed-slice aggregates → marshalling
  (null-term cstr, data-ptr extraction, aggregate construction). `Args`
  can't be pure `__c_call` (no libc fn returns argv) — a minimal argv
  hook stays in C. Not C-freedom (still links libc syscall wrappers).
- **Needs a BUILDER bump** (the deferral reason above; the original
  "no BUILDER bump" claim was wrong — BUILDER *compiles* `__c_call` fine,
  but its *runtime* still defines the I/O symbols gen1 links). Baremetal
  keeps its semihost impl (per-target, like rt). Filed 2026-06-03.

### Better test-mode/target annotation than `.xfail` (unit + conformance)
- We lean on `.xfail.<mode>` files to mark tests that can't run in a
  given configuration (e.g. `pkg-builtins-rt.xfail.builder-comp-int*`
  because rt is native-only in the VM; the `__c_call` conformance tests
  498/500/527/530 xfailed in every VM-leg mode). But "expected to FAIL"
  is the wrong semantics for "not APPLICABLE here" — these tests are
  *bnc-only* / *vm-only* / *target-specific* by nature, not regressions.
- **Want**: a first-class annotation (in the test source or a manifest)
  declaring a test's applicable modes/targets — `bnc-only`, `vm-only`,
  per-backend, per-target — so the runner *skips* inapplicable configs
  cleanly and reserves `xfail` for genuine known-failures. Would also
  let `__c_call` tests declare "compiled-only" honestly instead of a
  fan of per-mode xfail files.
- Surfaced 2026-06-03 by the drop-libc / native-only-rt work.

### Cross-package managed-PTR extern var: value-copy crash (559 OPEN — but native-aa64 XPASSES); field-write (561) RESOLVED 2026-06-03 — native-aa64 xfail now STALE
- **Native-aa64 lane update (2026-06-04)**: now that the native aa64 lane
  builds (after the `551`/`573` `&G`-rvalue fix `9a0f4f9a`), a full-lane
  `--check-xpass` run shows BOTH `559` and `561` XPASS on native aa64
  (pass despite their xfail markers).  `561` is squarely stale — it was
  already RESOLVED on all default modes (below); its native-aa64 xfail
  only lingered because the lane didn't build.  **Action: remove
  `conformance/561_cross_pkg_ptr_field_write.xfail.builder-comp_native_aa64-comp_native_aa64`.**
  `559` is more surprising: the entry below still lists it as an OPEN
  cross-package value-copy CRASH, yet it now passes on native aa64.
  Either a recent fix resolved the crash (making the all-mode xfails
  stale too) or the crash is mode-specific and native aa64 happens to
  dodge it — NEEDS a cross-mode re-check before removing 559's xfails.
  (Surfaced while landing `550`; not caused by it — 559/561 use no
  closures.)
- **OPEN — Symptom A (crash, 559)**: copying an extern managed-ptr var's
  whole value — `var n @pkg.T = pkg.G` — crashes at runtime.  Isolated to
  extern + managed-ptr + value-copy (same-package managed-ptr copy works,
  the qualified type works from a func return, managed-SLICE copy works,
  managed-ptr FIELD read works).  The cross-package RefDec at the local's
  scope end needs the imported type's dtor, which the importer lacks.
  Test: `conformance/559_cross_pkg_managed_ptr_copy` (xfail all modes).
  **Fix direction**: emit/import the managed type's dtor so a
  cross-package managed-ptr local can RefDec at scope end.
- **~~Symptom B (field-write no-op, 561)~~ — RESOLVED 2026-06-03 (binate
  `733d4485`)**: `pkg.G.V = v` through an imported managed-ptr var
  silently dropped the store.  Root cause was NOT `genSelectorPtr`'s
  EXPR_IDENT-only branch (its nested-selector branch already recurses and
  obtains the lvalue) but `getSelectorType` returning nil for `pkg.G` — it
  resolved the import alias `pkg` as a (nonexistent) variable, so the
  nested branch couldn't type the inner selector and skipped the
  managed-ptr field-store case.  Fixed with a package-qualified-var case
  in `getSelectorType` (returns the imported var's declared type via
  `lookupImportedGlobalPtr`); `getSelectorType` moved to
  `gen_selector_type.bn` (length cap).  `conformance/561` un-xfailed
  (green all 6 default modes; native-aa64 lane stays xfailed — it doesn't
  build).  Unit: `TestGetSelectorTypeQualifiedImportedVar`.
- **Discovery**: 2026-06-03, deferral-2 Slice 4 + coverage review.

### Dispatch conflicts (extern registered + Binate body provided) should be a HARD ERROR
- **What**: today the VM dispatches a `BC_CALL` by name: `LookupFunc`
  → if `>=0`, run the bytecode body; if `-1`, fall through to
  `execExtern` (which consults `vm.Externs`).  Functions registered
  via `RegisterExtern` shadow whatever the .bni declares, but ONLY
  when there's no Binate body — if a user (or a future migration)
  adds a `.bn` body for a name that's also extern-registered, the
  bytecode body silently wins and the extern is dead code.
- **Why a hard error**: the previously-explored "dispatch flip"
  (silently skip lowering when an extern is registered, so the
  extern wins) is the wrong design — the conflict represents
  contradictory definitions of the function, and the right answer
  is to make the user resolve it explicitly, not pick a winner
  silently.
- **Where**: `pkg/binate/vm/lower.bn::LowerModule` (the loader
  pass) is the natural place to detect it — when about to lower
  a function whose qualified name `vm.LookupExtern(...) >= 0`,
  abort with a clear diagnostic naming the offending function
  and both sources.  Same shape as the existing extern-registry
  pre-checks but loud instead of silent.
- **Tests**: unit test pinning the abort path (register an
  extern + lower an IR module with a function under that name
  → assert it errors with a recognizable message).

### Slim `pkg/bootstrap` and `pkg/libc` by migrating callers OUT
- **What**: rather than converting bootstrap's I/O surface
  in place, migrate callers AWAY from `pkg/bootstrap.X` and
  `pkg/libc.X` toward whatever the long-term replacement is
  (a new I/O package, a slimmer `pkg/std/os`, etc., TBD).
  Goal: shrink the surface of both bootstrap and libc until
  they can either be retired entirely or held as truly minimal
  bootstrap primitives.
- **Approach** (sketch — needs design): identify call sites,
  classify them by what they want (formatted print, file I/O,
  process control, raw libc memops), and route each class to
  the canonical replacement.  bootstrap and libc only get
  what's TRULY platform-essential and inappropriate for any
  higher-level package.
- **Progress**:
  - **libc Memcpy / Memset — DONE 2026-06-02 (binate `87965b70`)**:
    the libc-host rt's MemCopy / MemZero now do pure-Binate byte loops
    (matching the baremetal rt, which already did) and Box copies via
    MemCopy, so both primitives were removed from the whole surface —
    `pkg/libc.bni`, `runtime/libc_stubs.c`, the cmd/bni + vm extern
    registries, and the vestigial baremetal `bn_pkg__libc__*` aliases
    in semihost.s.  No BUILDER bump (gen1 links BUILDER's runtime;
    gen1's outputs emit no `bn_pkg__libc__*` and link checkout's
    runtime).  Verified across compiled / VM / self-hosted / baremetal
    lanes.  Perf footnote: the byte loops are slower than libc
    memcpy/memset at -O0 (no idiom recognition) — accepted for now,
    revisit with a word-at-a-time loop if it shows in profiles.  This
    does NOT touch the C-ABI memcpy/memset LLVM emits for aggregate
    copies (llvm.memcpy intrinsics), which are independent of pkg/libc.
- **Remaining libc surface**: Malloc / Calloc / Free (now the only
  callers; need a real Binate allocator to retire) and Exit (needs a
  process-exit syscall, gated on the C-free syscall story).
  `pkg/bootstrap` — the larger I/O surface — is the next target.
- **TODO — migrate `bootstrap.Itoa` callers to `strconv.Itoa` /
  `strconv.FormatInt`**: now that `pkg/std/strconv` has `Itoa(v int)`
  (base 10) and `FormatInt(v int64, base)`, they are the canonical
  replacement for `bootstrap.Itoa`.  Goal: every Tier-1/Tier-2 caller
  uses strconv instead of bootstrap (a sub-step of retiring the
  bootstrap int-format surface).  **Two hard constraints gate which
  sites can move:**
  - `cmd/bnc` and its **BUILDER-compilable dependency tree** (incl.
    `pkg/binate/token`, the `native/*` backends, codegen, ir, …) CANNOT
    import `pkg/std/strconv`: the package pulls in `pkg/std/math/big` (and
    floats) via `ftoa.bn`, which is not BUILDER-compilable.  These stay
    on `bootstrap.Itoa` until either strconv's integer-only path is split
    into a BUILDER-compilable subpackage or the BUILDER constraint lifts.
  - `pkg/builtins/lang` (Tier-0 core) CANNOT depend on strconv either —
    strconv imports the builtins, so it would cycle.  Stays.
  - **Migratable now** (built by bnc, full language, not in bnc's tree):
    `cmd/bni/main.bn`, `cmd/bnlint/main.bn`, `pkg/binate/vm/*`,
    `pkg/binate/repl/*`.  Audit each call site (a `grep -rn '\.Itoa('`
    sweep currently finds ~10 non-test sites) and route base-10 ones to
    `strconv.Itoa`, other bases to `strconv.FormatInt`; check each file's
    BUILDER status before switching.
- **Why migrate OUT rather than convert in place (do NOT re-attempt the
  in-place shape)**: in-place renames of packages whose surface is
  declared-only and resolved by C symbols (`pkg/libc`, and the I/O side
  of `pkg/bootstrap`) hit a wall that pure-Binate-package renames
  (pkg/rt → pkg/builtins/rt) do not.  The wall: at Stage 1, gen1 is
  linked against BUILDER's bundled `libc_stubs.c` (auto-found next to
  `--runtime`), which only defines symbols under the OLD mangled name
  (e.g. `bn_pkg__libc__Memset`).  Checkout source — now compiling under
  the NEW package name — emits calls to `bn_pkg__builtins__libc__Memset`,
  which is UNRESOLVED at Stage 1's link.  Pure-Binate packages don't hit
  this because the bnc-compiled package provides the NEW-name symbols as
  definitions in its own `.o`; declare-only-via-C packages have no such
  Binate-side definition.  Compat aliases in checkout's `libc_stubs.c`
  don't help — BUILDER's runtime is what Stage 1 links against, not
  checkout's.  Resolving would require either (a) pointing Stage 1's
  `--runtime` at checkout's (build-script surgery), (b) a supplemental
  compat .o via `--link-after-objs` (build-script surgery + new
  artifact), or (c) two release cycles with a transitional bridge —
  none worth the bootstrap migration's payoff.  Migrating callers OUT
  side-steps the whole tangle.
- **Status**: in progress.

### Package descriptors (Phase B) — `_Package()` works in compiled + VM modes (builtins); general Functions-table still future
- **Status**: compiled-mode AND VM-mode `_Package()` landed (binate
  `feadde2c`, VM-mode for the builtin packages).  The general interop
  Functions-table (user packages, auto-enumeration) remains future work.
- **What works (compiled mode)**: every package emits an immortal
  static-managed `reflect.Package` descriptor node + a generated
  `_Package() @reflect.Package` accessor (codegen `emit_pkg_descriptor.bn`,
  via the static-managed emitter).  The type checker synthesizes the
  `_Package` signature at selector resolution (`check_expr_access.bn`
  `packageAccessorType`), IR-gen registers it as an imported extern so calls
  resolve + a `declare` emits (`gen_import.bn`), and `reflect` is force-loaded
  (`ensureReflectLoaded`).  Drives a real immortal node through the compiled
  RefInc/RefDec sentinel end-to-end (see [`plan-static-managed-sentinel.md`]).
- **What works (VM mode, binate `feadde2c`)**: the earlier "Functions-table
  is genuinely required" finding was too pessimistic.  `_Package` is already
  a real exported per-module symbol, and the IR/func-value path already
  mangles a qualified `pkg._Package` reference to call it — so the only
  blocker was the type checker rejecting `_func_handle(pkg._Package)` (it's
  compiler-synthesized, not a `SYM_FUNC` in scope).  Two small changes wired
  it: (1) `types/check_builtin.bn` accepts `pkg._Package` as a `_func_handle`
  argument by name; (2) `vm/extern_register_std.bn`
  `registerPackageDescriptorExterns` binds the builtin packages' `_Package`
  (rt, libc, bootstrap, reflect) as VM externs.  Interpreted `pkg._Package()`
  now dispatches through the func-value shim to the real accessor, and the
  returned `@reflect.Package` is RefDec-safe via the static-managed sentinel —
  exercising the sentinel end-to-end in interpreted mode too.
- **Coverage**: `conformance/532_reflect_package_accessor`
  (`rt._Package().Name` → "pkg/builtins/rt") now green in ALL 6 default modes
  (the 3 VM-mode xfails removed).
- **Still future — the general Functions-table**
  ([`notes-package-introspection.md`](notes-package-introspection.md) Phase B):
  `registerPackageDescriptorExterns` is a hand-maintained precursor covering
  only the builtins compiled INTO the host binary (their `_Package` is a real
  symbol the shim can call).  USER packages run as interpreted bytecode and
  have no `_Package` body — those need the real table: codegen emits a
  per-package `Functions` table (name + signature + function-value per
  exported func), and the VM auto-enumerates all packages' tables (the
  cross-package registry, open Q4 in the notes — likely a linker section with
  start/stop symbols) to bind names → function values, replacing the hand-
  maintained `RegisterStandardExterns` entirely.  Then richer type metadata
  (Phase C) for reflection/printing + RTTI for type assertions.
- **Linter caveat (see "bnlint typechecks dependency bodies" + lint-skip
  entries)**: `registerPackageDescriptorExterns` is the first `_Package`
  reference in *linted* source, which the BUILDER-bundled bnlint can't yet
  typecheck — `scripts/hygiene/lint.sh` temporarily skips pkg/binate/vm +
  pkg/binate/repl + cmd/bni until the next BUILDER bump.

### Untyped single const (`const X = 5`) is not forward-referenceable — same collectDecls gap, distinct from the (fixed) group case
- **Symptom**: a top-level untyped single const with no explicit type
  (`const X = 5`) reports `undefined` when referenced from a decl
  checked BEFORE it — a forward reference within a file, or a sibling
  file ordered ahead of it (package files are merged).  `const X int = 5`
  (typed) does NOT have this problem.
- **Relationship**: the sibling of the const-GROUP bare-iota-member bug
  fixed in binate `88c9c0b7` — same root cause, `collectDecls`
  (`pkg/binate/types/check_decl.bn`) only forward-registers consts whose
  `TypeRef != nil`.  The group fix handled bare iota members (always
  untyped int → trivial untyped-int placeholder); this single-const case
  was left because it is **harder**: an untyped single const's type
  depends on its VALUE, and naively `checkExpr`-ing the value during the
  collection pass would emit spurious `undefined` errors for
  reference-valued consts (`const X = Y; const Y = 5`, where Y is checked
  after X).
- **Discovery**: 2026-06-02, characterizing the completeness of the
  group fix (a probe test, `TestForwardRefUntypedSingleConstKnownGap` in
  `pkg/binate/types/check_decl_test.bn`, asserts the current buggy
  behavior so the suite stays green).
- **Why MAJOR (loud, not silent)**: compile-time `undefined`, not a
  silent miscompile.  Lower-priority than the group case in practice —
  untyped single consts forward-referenced are uncommon (most code
  writes `const X int = …` or uses a group).
- **Proposed fix direction**: in `collectDecls`, for an untyped single
  const, forward-register the name when the value is a simple LITERAL
  (int / string / float / bool / char) whose type is unambiguous and
  dependency-free; leave reference / expression values for a later pass
  (or a two-phase const resolution).  Avoids the spurious-error trap.
- **Tests covering it**: `TestForwardRefUntypedSingleConstKnownGap`
  (flip to `expectNoErrors` when fixed); add a conformance test mirroring
  `526_forward_ref_iota_const` for the single-const case as part of the
  fix.

### Static-managed sentinel refcount — IN PROGRESS (prerequisite for package descriptors)
- **Status**: IN PROGRESS — worktree `temp-binate-6` / branch `work-6`,
  started 2026-06-01.  Plan:
  [`plan-static-managed-sentinel.md`](plan-static-managed-sentinel.md).
- **What**: implement the long-designed sentinel refcount for immortal
  static **managed objects** (`claude-notes.md:909`,
  `detailed-notes:1427`), so the package descriptor's
  `@reflect.Package` / `@TypeInfo` / `@FunctionInfo` nodes can be static,
  never-freed `@` values.  Designed but unimplemented in **all ~5 refcount
  paths** (library rt.bn ×2, LLVM-inline `emit_refcount.bn`, native aarch64
  inline, native x64 (library CALL), VM `vm_exec_helpers.bn`).
- **Root context**: immortality today rides entirely on the nil-pointer
  skip; there is no sentinel check anywhere.  The only static-managed data
  is string-literal managed-*slices* (immortal via `backing_refptr = null`,
  `emit.bn:382`).  There is no managed-pointer-to-static-struct in the
  language yet — the descriptor nodes are the first such case.
- **Design**: negative-as-immortal (`h[0] < 0`, cheap sign test); static
  nodes emitted with `h[0] = STATIC_REFCOUNT` (INT_MIN); `rt.RefDec`'s
  `<= 0` abort becomes `== 0`.  Add the short-circuit to all five paths +
  a static-node emitter (header `-16`/`-8` before payload).
- **Investigation rider** (per user): can the string-literal null-backing
  trick be retired / unified under the sentinel?  Representation can plausibly
  unify; the nil-check itself can't be dropped (guards genuinely-nil `@`
  values).  Deferred — sentinel lands first; string-literal lowering is
  untouched in the initial landing.
- **Tests**: conformance — immortal `@T` inc/dec'd + dropped, asserted never
  freed (poisoned free-fn / alloc counter), pinned across modes incl. arm32;
  unit — per-path no-op-on-sentinel + static-node IR shape.
- **Candidate user of the sentinel** (added 2026-06-02): the VM's per-callee
  shared non-capturing-`@func` `ClosureRec` (`ensureHandle` in
  `pkg/binate/vm/vm_exec_funcref.bn` — `callee.ClosureRec`, a
  `@VMClosureRec` shared by all instances of that func value) is exactly a
  static, never-freed managed object.  It was being prematurely freed by
  instance RefDecs (the `@func`-RefInc/RefDec-asymmetry CRITICAL bug,
  fixed symmetrically in binate `<commit>` — see `conformance/528`).  The
  symmetric-RefInc fix works, but making the shared `ClosureRec` an
  immortal sentinel object would be the cleaner long-term representation
  (no per-instance refcount churn on a shared singleton).  Consider
  folding it in when the sentinel lands.

### bnc: top-level consts of non-int types silently emit `EmitConstInt(0)` at read sites (Phase A — string/bool/float — DONE; composite/pointer remain)
- **Symptom — general**: declare a top-level `const X T = <expr>` where T is anything other than an integer-family type (or the iota-fed untyped int), and reads of X from any function — in-package OR cross-package qualified `pkg.X` — fall through to `EmitConstInt(0, TypInt())` in IR-gen.  Downstream effects depend on T's expected LLVM shape:
  - **Loud** (clang rejects the .ll with shape mismatch): types whose read sites perform an aggregate operation on what should be a slice / struct / array — get `extractvalue i64 %v, N` (extractvalue on a scalar).  Boolean reads hit `'%v' defined with type 'i64' but expected 'i1'` at branch sites.
  - **Silent wrong** (compiles cleanly, runs with zero values): scalar non-int types (float, char[fixed via lit-fold], pointer) read back as 0 / 0.0 / nil; struct reads return all-zeros.
- **Per-type characterization** (probed 2026-06-01):
  - `int` / all sized int+uint types / `char` / `iota` const groups — work (evalConstExpr handles INT_LIT, CHAR_LIT, arithmetic, references to prior int consts).
  - `*[]const char` (string) — **FIXED** in binate `a5acfc45`.  Producer (`genConst` in pkg/binate/ir/gen_const.bn + the importer's `registerImportFile` in gen_import.bn) recognizes EXPR_STRING_LIT initializers and populates a new `StrVal @[]char` + `IsStr bool` on ModuleConst.  Read sites (EXPR_IDENT in gen_expr.bn, qualified EXPR_SELECTOR in gen_selector.bn) walk moduleConsts and emit `EmitConstString` + `EmitStringToChars` for IsStr entries — producing the same OP_CONST_STRING + OP_RODATA_SLICE shape literal `*[]const char` values already use.
  - `bool` — broken loud (i64 vs i1 mismatch at branch).  Same-shape fix as string: add `BoolVal`/`IsBool` to ModuleConst, recognize EXPR_BOOL_LIT, emit EmitConstBool.
  - `float32` / `float64` — broken silent (read as 0).  Add `FltText @[]char` + `IsFlt bool`, recognize EXPR_FLOAT_LIT, emit EmitConstFloat (which takes raw text + a type — needs the const's declared type carried through).
  - `[N]T` (array literal) — broken loud (extractvalue on i64).
  - `struct T{...}` (struct literal) — broken silent (all-zero struct).
  - `*[]const T` / `@[]const T` (composite-literal slice / managed-slice) — broken loud.
  - `*T` / `@T` (pointer to value) — not yet probed.  Three sub-cases worth keeping straight when designing the fix:
    1. const-pointer to a static global (`const P *T = &G`) — needs the pointee's address to be known at compile time;
    2. const-pointer to a string literal address (`const P *const T = &SomeStringLitContent`?) — niche;
    3. const-pointer where `T` is itself const (`const P *const T = ...`) — orthogonal const-of-const.
- **Discovery**: 2026-06-01, while trying to land Phase 1 of plan-version-info.md.  The string case tripped first; subsequent probing across other types showed the common root cause.
- **Root cause**: `moduleConsts` only carried `Val int`; producers (`genConst`, `registerImportFile`) call `evalConstExpr` which is integer-only and discards non-int initializers entirely; read sites (EXPR_IDENT in gen_expr.bn, qualified EXPR_SELECTOR in gen_selector.bn) called `lookupConst` (also int-only), missed the discarded consts, and emitted a zero-int placeholder via `EmitConstInt(0, TypInt())`.  The type-checker correctly accepts these declarations — `const X T = expr` in Binate marks `X` as an immutable variable (`claude-notes.md` "Compile-time constants" / "Const on variable declarations"), not a compile-time-foldable literal — so the bug is squarely in IR-gen's const-handling.
- **Why MAJOR**: any production package that exposes a non-int top-level const silently mis-emits.  Currently latent only because the project has no such consts yet; the version-package draft (now landed for string only) was the first encounter.  Composite-typed consts are particularly dangerous — both loud-on-aggregate-access and silent-on-zero-default-read modes occur.
- **Tests covering it**: pkg/binate/version's tests pin the string case end-to-end through both in-package and cross-package reads; `conformance/522_cross_pkg_const_string` and the new `TestGenConstStringLit*` unit tests in `pkg/binate/ir/gen_const_test.bn` (binate `a000855a`) add coverage at the IR-gen producer + read sites.  No coverage for bool / float / composite / pointer cases yet — Phase A adds focused unit + conformance suites for each.
- **Status**: **Phase A DONE** (2026-06-02).  Every *scalar* non-int top-level const now lowers correctly — string (binate `7b0f77a3`), bool (`c3ff33f7`, conformance 540), float incl. untyped + float32 (`82c985f5`, conformance 541), negative float literals (`054629fd`), and non-int members of `const ( … )` **groups** (`a6fef840`).  Single + group producers, in-package + imported, all route through the shared `classifyConstLit` (string/bool/(unary-negated-)float) helper in `pkg/binate/ir/gen_const.bn`; read sites dispatch on `ModuleConst.Kind` (CONST_INT/STR/BOOL/FLT).  Unit tests in `gen_const_test.bn` + conformance 540/541 (cross-package EXPR_SELECTOR + in-package EXPR_IDENT, incl. a branch-condition bool and a group member).
  - **Coverage note** (probed): `GenConstMember` (REPL forward-ref retry) needs no non-int handling — it only ever sees *parkable* (undefined-name-referencing) consts, i.e. int/iota expressions, never literals.  `RegisterImport` (singular, `gen_register_import.bn`) is still int-only but is **test-only** (no production caller; production imports use the fixed `registerImportFieldsAndFuncs`) — a minor consistency follow-up, not a production gap.
- **Decision (2026-06-02): Phase B (composite-typed consts) is CANCELED.**  `const` stays **scalar-only** (per `claude-notes.md:267-283`); immutable composite data is expressed with `var readonly` (`plan-const-readonly.md`), not `const`.
  - **RESOLVED (2026-06-03, plan-const-readonly step 6)**: `checkConstDecl` now rejects a non-scalar const type via the new `Type.IsScalar` predicate (`errNonScalarConst`).  Unit tests: `check_decl_test.bn` (string + struct rejected; int/bool/char/float accepted) + `TestIsScalar` in `types_test.bn`.  The string-const IR-gen workaround (the `EmitConstInt(0)`-path CONST_STR family) was then removed in step 7, so the latent mis-emit bug this entry tracked is gone.
  - **Scouting handoff (if a `const`→composite extension is ever revisited)** — it is a real language extension, NOT the plan's lighter estimate: (a) composite consts would route through `moduleGlobals` + the synthetic `__init` allocate/store path (`gen_init.bn`), reusing the var-as-initialized-global lowering — **not** static rodata, which is byte/i8-only; (b) **cross-package global reads do not exist yet** — no imported-`var` registration in `gen_import.bn`, no qualified global read-site in `gen_selector.bn` (it searches only `moduleConsts`), no extern-global decl in codegen — so the plan's "reuse existing global machinery" is **false**; that plumbing must be built; (c) immutability needs **real checker work** (make a composite const read as a `TYP_READONLY` value + fix `checkIndexExpr` to re-wrap readonly on the element type so `X[i]=v` is caught), not "just tests" — `X[i]=`/`X.F=` on a composite const are silently accepted today because `SYM_CONST` (binding) and `TYP_READONLY` (type) are disjoint.
- **Phase C (pointer consts) is also CANCELED** — a pointer isn't scalar, and more fundamentally it *refers to storage*, so it can't be a pure compile-time value.  const-pointer / const-slice / const-managed forms stay rejected (storage-referring types), alongside the composite forms above.
- **Future direction (TODO, not started): allow `const` of transitively *purely value* types.**  A type is *purely value* iff it carries no storage reference: scalars (int-family / bool / char / float) are purely value; `[N]T` is purely value iff `T` is; a struct is purely value iff every field type is.  Pointers, slices, and managed pointers/slices are NOT (they hold a pointer to storage) and stay rejected.  (Strings are a slice of rodata, already handled as a separate immutable-rodata case in Phase A.)  A purely-value const's whole value is known at compile time, so it should be **const-folded at read sites as an immediate** — the scalar-const model (per-use `EmitConst…`), NOT Phase B's canceled initialized-global lowering.  This subsumes `const P Point = Point{1,2}` and `const M [3]int = …` as real constants.  When picked up: define an `isPurelyValueType` predicate, widen `checkConstDecl`'s accept boundary from "scalar" to "purely value", and extend the const producer + read-site dispatch to fold value-struct / value-array literals.

### Demote raw-slice escape check from type error to linter rule
- **Final diagnosis**: an unqualified EXPR_IDENT inside a
  `.bni`-declared const initializer (e.g. `WORDS` in
  `const SIZE int = WORDS * cast(int, sizeof(int))`) wasn't
  resolving during import processing — pkg/ir's evalConstExpr
  looked the name up only in unqualified form, but the sibling
  const had been registered under the import-qualified name
  (`pkg/x.WORDS`).  The EXPR_IDENT arm returned (0, false), the
  binary expression silently became 0, and the resulting const
  was registered with value 0.
- **Fix (binate `8fd4f378`)**: retry the lookup with
  `buildQualName(currentImportAlias, e.Name)` when the
  unqualified one misses.  Pinned by conformance
  `504_bni_const_sibling_ref`.
- **Boundary-enforcement aside**: my first writeup of this also
  speculated that bnc was accepting unexported cross-package
  references.  Re-tested with a focused repro: bnc DOES correctly
  reject `pkg.NAME` references when NAME isn't in the package's
  `.bni`.  Pinned positively by conformance
  `502_err_unexported_const_rejected`.  That part was always fine
  — the only bug was the sibling-ident lookup above.
- **Discovery**: managed-allocation-header refactor (binate
  `c7323fb2`).  Replacing pkg/vm's hardcoded `-16` managed-header
  offset with `ptr - rt.HEADER_SIZE` (declared as
  `HEADER_WORDS * cast(int, sizeof(int))`) built cleanly but
  produced `ptr - 0`, silently corrupting the payload's first
  word.  TestExecRefIncRefDecInline (pkg/vm) caught it on amd64.

### Demote raw-slice escape check from type error to linter rule
- **Today**: returning a raw slice (`*[]T`) into a local array
  (`return arr[:]`) is a hard type-check error.  The check catches
  the obvious pattern but **misses the real escape paths** the
  type system can't see (escape via out-param, via mutating
  callee, via interface, etc.), so it's a false-confidence trap:
  the user assumes "if it type-checks, my raw slice doesn't
  escape", which isn't what the check actually proves.
- **Why now**: while designing Phase 2 of function values
  (`plan-function-values-phase-2.md`), the same escape question
  came up for capturing `*func(...)`.  Decision: no type-check
  rejection; raw is the opt-in escape hatch, linter warns on
  obvious patterns.  That makes the raw-slice rule the
  inconsistent one — slices are the only raw type with a hard
  escape check in the type system.
- **Fix direction**: demote the raw-slice escape rejection to a
  linter rule in `cmd/bnlint` (best-effort detection of return,
  store-to-outliving-field, assign-to-global, etc.).  Type
  checker stops rejecting; existing tests that exercise the
  reject become linter-positive cases.
- **Scope cost**: small.  One rule to remove from the type
  checker, one to add to bnlint, conformance test updates for
  the affected patterns, doc updates.
- **Coordination**: ideally lands alongside or just after Phase
  2 of function values (where the analogous capturing-`*func`
  linter rule is added — B.5 of `plan-function-values-phase-2`).

### IR integer constants are host-width `int` (blocks 32-bit-hosted toolchain) — LAYER 1 + 2 (INT64 + FLOAT64) DONE
- **Symptom**: under `builder-comp_arm32_linux` unit tests, `pkg/ir`
  and everything downstream of it (`pkg/native{,/amd64,/arm64,/common}`,
  `pkg/codegen`, `pkg/vm`, `cmd/{bnc,bni,bnas}`) fail to compile for
  arm32 with int-width type errors.  `pkg/ir` is the cascade root.
- **Discovery**: triaging the 14 arm32_linux unit-test failures after
  type-check errors gained source locations (binate `c011827`,
  conformance/494).  With locations on, `pkg/ir`'s only *source* error
  is `gen_util_literals.bn:234` (`intFitsInType` compares against
  `4294967295` > INT32_MAX), and tracing the value upstream shows the
  whole literal path is `int`.
- **Root cause**: the IR stores program integer constants in
  `Instr.IntVal`, typed `int` (`pkg/ir.bni:356`) — host-width.  The
  feeding path (`exprIntLitValue`, `bignumToInt`, `parseIntLit`,
  `EmitConstInt`) is all `int` too.  On a 64-bit host this happens to
  work (it's really storing a 64-bit *bit pattern* — a `uint64`-max
  literal lands as the int64 pattern `-1` and codegen emits it fine).
  On a 32-bit host `int` is 32 bits, so the path neither compiles nor
  can represent a `uint32`/`int64` constant.  Symbol/codegen output
  must not depend on host int width.
- **Severity**: major.  Loud (compile failure) on 32-bit, not a silent
  64-bit-host miscompile — but it blocks the C-free / 32-bit-hosted
  self-hosting goal.  `int64` vs `uint64` for the field is immaterial
  (it's a stored bit pattern reinterpreted by the constant's type);
  `int64` is the minimal-churn choice since the existing range-check /
  negation code is written in signed terms whose bounds fit `int64`.

- **Layer 1 — IR + codegen + native (DONE)**: made the program
  -constant path host-independent.  Landed: binate `879ba38`
  (asm 64-bit immediates: x64 Imm→int64 + Imm64, finished aarch64
  Imm consumers in pkg/asm/parse), `035022c` (IR int64 contract),
  `294b5f0` (wide-constant tests), `075e1f5` (made the int-width
  -assuming bootstrap/vm tests 32-bit compatible).
  - `Instr.IntVal` `int` → `int64`.
  - `exprIntLitValue` / `bignumToInt` return `int64`; `intFitsInType`
    takes `int64`.  (`parseIntLit` stayed host-`int` — a
    non-type-checked fallback; the real path takes the bignum branch.)
  - `EmitConstInt(int)` kept (widens internally) + new
    `EmitConstInt64(int64)` for the literal path.
  - `buf.WriteInt64` added; codegen's OP_CONST_INT emit uses it.
  - `pkg/native/{amd64,arm64}` `emitConstInt64` → `int64`; arm64
    extracts MOVZ/MOVK chunks via int64 shifts.  Fixed a latent bug:
    arm64 `emitConstFloat` did `cast(int, bits)` on a 64-bit IEEE
    pattern (dropped the high word on a 32-bit host) → `cast(int64,…)`.
  - VM boundary: `lower_instr.bn` `bc.Imm = cast(int, instr.IntVal)`
    — lossless on a 64-bit host; the truncation-on-32-bit is what
    Layer 2 addresses.
  - **Result**: all 14 packages in the arm32_linux unit-test set
    compile for arm32 (verified locally; runtime validated by the
    `builder-comp_arm32_linux` CI job).

- **Layer 2 — VM machine word (INT64 PATH DONE)**: `pkg/vm` uses host
  `int` as its universal machine word — registers, immediates,
  pointer arithmetic (`bit_cast(int, frameBase) + instr.Imm`),
  offsets.  So a 32-bit-hosted VM is a 32-bit machine and can't carry
  64-bit immediates.  Open design question (raised by user): can the
  VM keep host-sized words for most values and use 64-bit only when
  necessary?
  - On a 32-bit host the VM interprets 32-bit-*target* bytecode, where
    pointers / `int` / sizes / offsets are all 32-bit by definition —
    so host-word is already correct for the vast majority of values.
    The 64-bit cases are exactly the explicitly-64-bit ones: `int64` /
    `uint64` values and large literals.
  - Two implementations of "64-bit only when necessary":
    (a) uniform 64-bit value slots + width-aware ops — simplest and
    correct; on a 32-bit host it costs 64-bit slot storage and 64-bit
    arithmetic only where the op is 64-bit (the compiler already
    supports `int64` on 32-bit; bytecode is largely typed already).
    (b) host-word slots + 64-bit via register pairs / a parallel wide
    slot, switched by typed opcodes — saves the 32-bit storage but
    complicates the register model and bytecode (must track which
    slots are wide).
  - Recommendation: do (a) first (correctness, minimal model change);
    treat (b)'s host-word-mostly layout as a later 32-bit perf
    refinement, not a correctness prerequisite.
  - **Investigation findings (2026-05-26)**: the change is larger and
    more entangled than the (a)/(b) framing implies — `int` is a
    *single conflated word* across three distinct roles, so it can't
    be swapped to int64 blindly:
    1. **Register slots.** `regs *int`, accessed `regs[i]`.  But
       `pushFrame` already budgets `f.NumRegs * 8` bytes/reg
       (`vm.bn:181`) — 8-byte slots.  On a 64-bit host int==8 so it's
       consistent; **on a 32-bit host this is a latent stride bug**
       (8-byte budget, 4-byte `*int` access → registers alias).  So
       `regs *int → *int64` actually *fixes* this and matches the
       existing layout.
    2. **Host pointers.** Registers also hold host addresses via
       `bit_cast(int, vm.Stack)` / `bit_cast(*uint8, regs[i])`.  With
       int64 regs on a 32-bit host these become a width mismatch
       (host ptr 32-bit, reg 64-bit) — `bit_cast` is illegal
       (size differs); they need explicit widen-on-store /
       truncate-on-read helpers (`ptrToReg` / `regToPtr`).
    3. **Target-memory-structure access.** `bit_cast(*int, hdrPtr)`
       reads managed-slice/refcount headers as `*int`.  These are
       target-word-sized fields; tying their stride to the register
       word is wrong if the two ever differ.  Needs separating
       "VM register word" from "target word".
  - Surface: ~106 `bit_cast(int,…)/(*uint8,…)/(*int,…)` sites across
    vm_exec*.bn + vm.bn, plus `BCInstr.Imm int→int64`, register
    arithmetic, and the memory ops.  This is a multi-step refactor;
    settle the register-word-vs-target-word model before editing.
  - **What landed (int64 path)** — model:
    register == host word; 64-bit values use register pairs; pair ops
    only engage when `REG_SLOT < 8` (no-op on a 64-bit host).
    Pointer-vs-target-word ambiguity stays narrow because `bit_cast`
    sites are at register-vs-pointer boundary — register word stays
    host `int`, so the ~106 `bit_cast` sites are untouched.
    - Step 1 (binate `f7cae70`): `REG_SLOT = sizeof(int)`; register
      area / frame header sized by it.
    - Step 2a (`ca7def6`, `394a16a`, `ca41a75`): `buildSlotMap` /
      `regWidths` / `remapRegisters` — id→slot mapping with the
      audited `BC_RETURN.Dst` exception.
    - Step 3 (`fd3ca06`, `f764a66`, `be877fd`, `60657fd`, `947205f`,
      `ebaa077`): full `BC_*64` handler set — `LOAD_IMM64`, `MOV64`,
      arith / bitwise / shifts / signed+unsigned compares / unary
      (NEG, BITNOT) / casts (WIDEN_S, WIDEN_U, NARROW, MOV64-bitcast)
      / pair memory `LOAD64_PAIR` / `STORE64_PAIR`.  Pure compute
      factored into evalArith64 / evalCmp64 / evalShift64 /
      evalUnary64 / widen64* — host-tested across the tricky cases.
    - Step 4 (`925e9bc`, `949ea29`, `ebaa077`): lowering emits the
      `BC_*64` ops host-word-aware — `OP_CONST_INT`, all binary
      arith / cmp / shift, load/store, casts, NEG/BITNOT.
    - Step 2b (`24a5d67` RETURN64, `7353523` direct CALL,
      `2eaa8f9` indirect/func-value/iface call ABI,
      `11da9d7` multi-return pair-aware): int64 return + call ABI
      complete.  `NumParamSlots` + slot-count `Imm` semantics.
    - Step 6 (`1fd3b9f`): conformance/499 int64 arithmetic E2E.
  - **Float64-on-32-bit (DONE)**: mirrors the int64 pair pattern.
    - `ba1a798`: route the existing `BC_FNEG` / `BC_F*` /
      `BC_SITOF` / `BC_FTOSI` / `BC_F64_TO_F32` / `BC_F32_TO_F64` /
      `OP_CONST_FLOAT` `bit_cast(int, float64)` hops through
      int64 — compile-clean on a 32-bit host without yet changing
      lowering semantics.
    - `3126655`: `BC_F*64` opcode decls (`BC_FNEG64`,
      `BC_FADD64..BC_FDIV64`, `BC_FEQ64..BC_FGE64`) + pure
      `evalFloatArith64` / `evalFloatCmp64` / `evalFloatNeg64`
      helpers in `vm_exec64.bn` + host-testable unit tests for
      each helper.
    - `ae08c1ed`: `execOp64` dispatch glue — joins source pair(s),
      bit_casts through `int64` to `float64` for the compute,
      bit_casts back, splits to dst pair (or single-slot bool for
      compares).  Direct `execOp64(&stackArr[0], instr)` tests
      cover all three shapes (binary arith, unary FNEG, compare-
      writes-single-slot).
    - `00b10e38`: lowering — `lowerBinOp` / `lowerCmpOp` add an
      `isFloatPair` branch alongside the existing `isIntPair`;
      `OP_NEG` dispatches `BC_FNEG64`; `OP_CONST_FLOAT` emits
      `BC_LOAD_IMM64` with `splitInt64` halves when
      `is64BitScalar(instr.Typ) && REG_SLOT < 8`.
    - `769d2e54`: gate test for OP_CONST_FLOAT — confirms 64-bit
      host falls back to `BC_LOAD_IMM` (no spurious pair branch).
  - **End-to-end arm32 coverage status (2026-05-28)**:
    - `pkg/vm` source compiles cleanly on arm32 (since `ba1a798`).
    - Conformance `builder-comp_arm32_linux`: green.
    - **pkg/vm unit tests on `builder-comp_arm32_linux`: green**
      (was 16 failures pre-session → 9 → 1 → 0).  The bytecode-VM
      BC_*64 / BC_F*64 dispatch and slot allocation are now fully
      end-to-end-validated on a real 32-bit target — including
      the `TestRepro_StructWithManagedSliceFieldAppend` managed-
      memory path, which surfaced the hardcoded-LP64 managed-
      allocation-header offset that `81d31b7c`'s MANAGED_HDR
      const fixed.
    - The cascade-revealed packages — pkg/{types, codegen,
      native/{common,aarch64,x64}} — are also green on arm32 now
      after the LP64-baked-test cleanup (`11ff9864`, `2d13838d`).
    - Remaining arm32_linux failures (5) are all the int64-min-
      boundary cluster in pkg/{bootstrap,buf,ir} — see the
      "arm32 unit-test cleanup" entry for the bucket.  Unrelated
      to this work.

### arm32 unit-test cleanup: 5 remaining int64-boundary tests
- **Context (2026-05-28)**: `builder-comp_arm32_linux` unit tests
  are now down to **5 failures across 3 packages** — every other
  cascade of arm32 issues that surfaced through May 27–28 has
  been root-caused and fixed.  The remaining 5 share one shape:
  int64-min literal handling on a host whose `int` is 32-bit.
- **Resolved (commit trail)**:
  - `aee0260` — `cmd/bni` test runner lookup keyed on full
    pkgPath (fixed the entire `-int` unit-test lane that was
    silently broken since `7f989ad`'s mangler full-path flip).
  - `73651c28` — int↔int width-cast lowering: BC_TRUNC32 + emit
    BC_SEXT / BC_ZEXT for narrowings / widenings between
    int8/int16/int32/int64 (was unconditionally BC_MOV — wrong
    for any non-8-bit width change).
  - `a2588c54` — `pkg/types` `initTarget()` defaults host-detect
    via `sizeof` (was hardcoded LP64).  Fixes the root cause that
    made `is64BitScalar(TypInt())` true on arm32 and triggered
    pair-branch emission for plain-int ops.
  - `11ff9864` + `2d13838d` — LP64-baked test assertions across
    pkg/{vm,types,codegen,native/{common,aarch64,x64}} replaced
    with host-aware checks or explicit `setTarget64()` + a
    `TypInt → TypInt64` substitution where the test's intent was
    "an 8-byte int field on LP64 ABI".  Also fixed two real bugs
    the cascade exposed: BC_FTOSI / BC_SITOF / BC_F64_TO_F32 /
    BC_F32_TO_F64 pair-aware, and `is64BitScalar` accepting
    TYP_UNTYPED_FLOAT.
  - `81d31b7c` — managed-allocation header offset host-aware
    (`MANAGED_HDR` const = `2 * sizeof(int)`, was hardcoded 16),
    cleared the `TestRepro_StructWithManagedSliceFieldAppend`
    qemu segfault.
- **Status of previously-listed buckets**:
  - **Bucket 1 (LP64-baked tests)**: pkg/vm, pkg/codegen, pkg/native/*
    are GREEN.  pkg/asm/{x64,aarch64,macho} weren't in the
    cascade-revealed set and remain native-host-arch dependent
    (likely still need xfails, but separate workstream — host
    arch != target arch).
  - **Bucket 1b (pkg/vm TypInt width)**: ROOT-CAUSED.  Fixed by
    `a2588c54` (initTarget host-detect — the LP64-default was
    the deeper-than-suspected cause; not a test-scaffolding
    SetTarget ordering issue).
  - **Bucket 2 (genuine test-level)**: Still open as listed —
    `TestBinBufWriteU64LittleEndian` (pkg/asm/elf),
    `TestOrrImm` (pkg/asm/arm32).
- **Still open — Bucket 3 (int64-min boundary)**:
  - `pkg/bootstrap.TestFormatInt64Boundaries`
  - `pkg/buf.TestWriteInt` — "expected int64-min round-trip"
  - `pkg/ir.TestBignumToIntInt64Min`
  - `pkg/ir.TestGenUnaryMinusOnInt64Preserves`
  - `pkg/ir.TestNeedsHintNarrowing`
  All five share the int64-min literal pattern.  Likely one
  underlying fix: bignum / parseIntLit handling for values that
  overflow int32 on the host but fit int64 at the target.  Not
  blanket-xfail — investigate and fix.

### `print(42)` and friends: how do primitives implement interfaces? — DESIGN OPEN
- **Problem**: with the current rules, `int` (and other predeclared
  primitives) can't implement interfaces. Methods can only be
  declared on TYP_NAMED types (the receiver lookup in
  `check_decl_func.bn:resolveMethodReceiver` rejects `func (x int)
  ...` because `int` is TYP_INT, not TYP_NAMED). So a user-written
  `printIt(s *Stringer) { ... println(s.String()) }` can't accept
  a literal `42` — the user has to wrap with `type MyInt int` +
  impl, then write `printIt(&MyInt(42))`. That's a lot of
  ceremony for a basic use case.
- **Generics don't help.** A `printIt[T Stringer](t T)` call site
  still requires `T` to satisfy `Stringer`, so `int` would need a
  Stringer impl somewhere — same blocker as the non-generic case.
  Generics solve "extensible dispatch", not "primitives need to
  carry methods."
- **Today's escape**: `println(42)` works only because it's a
  compiler builtin — `bootstrap.println` synthesizes per-type
  formatting at the call site. Not user-extensible. The hack is
  documented as temporary in `feedback_println_hack.md`.
- **Two real options** (discussed 2026-05-07):
  1. **Language-blessed implicit interfaces.** The interface plan
     already lists `any` as a built-in implicit interface and
     reserves the mechanism for "small, closed, language-defined
     set" of others. Add `Stringer` (and possibly `Eq`, `Hash`,
     etc.) to that set — every type, including primitives, gets
     a synthesized impl from the compiler. Then a user-written
     `printIt(s *Stringer)` accepts any value uniformly.
     Cost: every iv gets a real vtable, even for primitives, and
     the language has to define the canonical formatting story
     for each primitive.
  2. **Standard-library carve-out for methods on universe types.**
     Allow a designated package (`pkg/std` or similar) to declare
     `func (x int) String() ...` even though `int` is a universe
     type. The carve-out exists only for the language's own std
     library; user packages still can't extend `int`. Closer to
     Go's `fmt.Println` model. Heavier carve-out but lets the
     std lib look like normal Binate code.
- **Lean (preliminary):** option 1 — the implicit-interface
  mechanism is already the named escape hatch, the formatting
  story for primitives is small + closed, and the result is
  user-extensible (their own types implement Stringer normally).
  But this is a real design call; needs a plan doc before
  shipping.
- **Not blocking**: today's `println(42)` carries the load.
  Revisit when generics land or when a user-written `printIt`-
  style function becomes pressing.

### Use interfaces more (opportunistic)
- **Constraint**: now bounded by `BUILDER_VERSION`-pinned bnc
  rather than the historical bootstrap subset — cmd/bnc no longer
  has to be bootstrap-runnable now that boot mode is gone (binate
  `c1be3cc`, 2026-05-21).  bnc-0.0.1 (the current BUILDER) supports
  interfaces, so anything in cmd/bnc's dep tree is fair game too.
  Generics are NOT in bnc-0.0.1, but interfaces are.
- **Candidates that look natural**: anywhere we currently
  switch on a kind tag with a dispatch table (e.g. opcode
  handlers, AST visitors, asm encoders) is the textbook shape
  where an interface compresses the dispatch.  Print/format
  helpers that take a kind + value pair are another easy lift.
  pkg/ast's tagged-union nodes (DECL_*, EXPR_*, STMT_*, TEXPR_*
  Kind enums + switch-on-Kind in pkg/{parser,types,ir,codegen,
  loader}) is the biggest single target but also the longest
  refactor — touches every layer.
- **How to land**: pick one site per PR, define the interface
  alongside, methodify the concrete types, drop the dispatch
  switch.  Keeps each step small enough that conformance +
  unit-tests stay green.  Mirrors the
  `migrate-to-method-form-opportunistic` pattern from
  `claude-todo-done.md` (DONE 2026-05-13).
- **Recon finding (2026-05-26)**: there is NO clean *small*
  retrofit target.  The candidates above split into two
  unappealing buckets: (a) enum→value lookups (reloc maps,
  opName, the emitInstr op dispatch) where `switch` is genuinely
  the right tool and an interface would mean manufacturing one
  empty marker type per enum value — pure ceremony; and (b)
  monolithic tagged unions (`ast.Stmt`/`Decl`, `ir.Instr`) where
  a real interface means splitting a struct that touches every
  layer.  So "use interfaces more" here is a deliberate design
  choice, not opportunistic cleanup.
- **Landed (2026-05-26): driver `Backend` interface** (binate
  `0ee0faa`, `bda81ca`, `6dacb23`).  The genuinely-valuable use
  found: `cmd/bnc/compile.bn`'s `Backend` interface
  (`compileModule`) with `llvmBackend` / `nativeBackend` impls,
  dispatched via `compileModuleVia`.  This collapsed the
  duplicated driver flow — `compileMainNative` is gone, `main()`
  picks the backend and the LLVM/native paths are unified.
  pkg/native also got an internal arch `Backend`
  (arm64/amd64).  These are the first non-synthetic interface
  users beyond pkg/std's `Stringer`.  NOTE: interface values
  must be constructed from locals, not package globals — `&global`
  iface construction was a codegen bug (now fixed, see
  conformance/495).

### Use `@[]@[]char{...}` composite literals (opportunistic)
- **Constraint**: previously forbidden because bootstrap didn't
  support managed-slice-of-managed-slice composite literals; now
  unlocked everywhere (bnc-0.0.1 supports them).  Mirrors the
  unconstraint situation for `cmd/bnlint`'s tests, which already
  use this shape.
- **Pattern to replace**: a known-fixed-length run of
  `args = appendCharSlice(args, "foo"); args = appendCharSlice(args, "bar"); ...`
  → `var args @[]@[]char = @[]@[]char{"foo", "bar", ...}`.  Same
  shape for `appendRawCharSlice` (since string literals are
  already `*[]const char`).  When the run mixes constants with
  computed values, leave it alone — the literal form only helps
  for known-static sets.
- **Candidates**: argv construction in build scripts (e.g.
  `cmd/bnc/{main,test,compile}.bn` clang-args setup), test
  scaffolding (anywhere a test builds a known `@[]@[]char`
  fixture), and short fixed sets of import paths.
- **Why bother**: cuts line count, removes a runtime O(n²)
  rebuild pattern (each `appendCharSlice` allocates a new
  slice + copies), and matches the language's expressive
  default instead of the bootstrap workaround.

### Use function values to collapse explicit dispatch shims (opportunistic)
- **Constraint**: function values are unlocked now that
  cmd/bnc is no longer bootstrap-bound; bnc-0.0.1 has the
  function-value machinery (see plan-function-values-phase-3
  in `claude-todo-done.md`).
- **Pattern to look for**: places where we route through a
  `kind` int + a per-kind dispatch table, when the data flow
  would be clearer as "the caller hands us the function it
  wants invoked".  Candidates need a closer look before they're
  fully scoped — function-value adoption isn't always a win
  (each call adds an indirect-call overhead), so this is
  selectively-opportunistic, not blanket.
- **How to land**: TBD; needs concrete site survey.

### Generics in cmd/bnc's tree — UNBLOCKED 2026-05-26 (BUILDER → bnc-0.0.2)
- **Status**: BUILDER is now bnc-0.0.2 (binate `5414bab`), which
  was cut from a tree that has generics (slices 4–7).  Verified the
  builder compiles generic decls + explicit instantiation
  `f[T](...)`; cross-package monomorphization works too.  So
  cmd/bnc-tree code may now use generics.
- **No type inference** (claude-notes.md:537, 1000): always spell
  the type arg, e.g. `slices.Append[@ast.Decl](xs, d)`.  The
  builder's "generic function requires type arguments" diagnostic
  on a bare `f(...)` call is intended behavior, not a gap.
- **First consumer — `pkg/slices`** (IN PROGRESS): `Append[T]`
  collapses the dozens of per-type `appendXxx` / `appendXxxPtr`
  helpers scattered across cmd/bnc + pkg/*.  Migration is staged
  one package at a time (see below).
  - **Generic packaging pattern**: a generic's body must live in
    the `.bni` (body-included) so cross-package consumers can
    monomorphize at the call site.  For an all-generic package the
    `.bn` needs **no** copy of the body — just the `package` decl
    (the package's own compile + tests resolve the generic from the
    merged `.bni`).  Keeping a second body in the `.bn` is a
    needless sync hazard; don't.
- **Mechanical migration DONE 2026-05-28**: ~62 per-type append
  helpers across pkg/{ast,types,ir,parser,loader,codegen,vm,
  native/aarch64} + cmd/bnc collapsed into ~378 call sites of
  `slices.Append[T]`, one commit per package boundary
  (binate `2714e67` loader → `ed727f8` parser → `bbb7fab5` ir →
  `60f385ff` cmd/bnc → `12f20a06` types → `79c11465` ir literals →
  `efbac9db` codegen → `d43185bb` vm → `1a45bb9b` aarch64 →
  `d226b237` ir scattered → `13477619` types capture → `a66b287c`
  cmd/bnc test).  Four `pkg/{loader,parser,ir,cmd-bnc}/slices.bn`
  files deleted.  Net ~-750 lines.

### Review remaining non-standard `appendXxx` helpers — opportunistic
- 13 helpers were kept past the `slices.Append[T]` migration because
  their bodies aren't a pure slice-of-T append (per the commit
  messages around 2026-05-28).  Worth reviewing whether any could be
  refactored to use `slices.Append` plus a small adapter:
  - ~~**Char-concat into a `@[]char` buffer** (not slice-of-T):
    `pkg/native/x64/x64_iface.bn`'s `appendPkgIdent_x64`,
    `appendStrIface`; `pkg/native/aarch64/aarch64_iface.bn`'s
    `appendPkgIdentNative`, `appendStrLocal`.  These four could
    probably share a single `buf.WriteStr`-style helper.~~ — DONE
    2026-05-28 (binate `fd1e931c` + `1b762f16`): pulled the two
    distinct shapes into `pkg/native/common.AppendStr` /
    `AppendPkgIdent`, x64/aarch64 callers rewritten, 4 duplicate
    helpers deleted, direct unit coverage in common_test.bn.
  - **Dedup / diagnostic-emitting**:
    `pkg/types/check_iface_extends.bn`'s
    `appendIfaceMethodWithConflictCheck` (emits a `CheckError` on
    signature mismatch) and `appendUniqueMethods` (dedup by method
    name).  These stay non-standard.
  - **Parallel two-slice append**:
    `pkg/ir/gen_iface_extends.bn`'s `appendAncestors(pkgs, names,
    pkg, name)` — could split into two `slices.Append` calls but
    the paired-update pattern is the helper's value; debatable.
  - **Conditional multi-arg append**: `cmd/bnc/target.bn`'s
    `appendTargetFlags`, `appendTargetRuntime` — fine as-is.
  - **Loader-level Imports**: `cmd/bnc/compile_imports.bn`'s
    `appendRtImport`, `appendLibcImport`, `appendBootstrapImport` —
    not slice append; fine as-is.
  - **Raw-slice wrap-and-append**: `cmd/bnc/util.bn`'s
    `appendRawCharSlice(s, *[]const char) → @[]@[]char` (CopyStr +
    append).  Could inline the 47 call sites as
    `slices.Append[@[]char](s, buf.CopyStr(v))` but the named
    helper documents the wrap-and-append idiom; debatable.

### Expand `pkg/slices` beyond `Append` — opportunistic
- `pkg/slices.Append[T]` is the only generic helper today.  Natural
  additions when call sites demand them (don't add speculatively):
  - `Concat[T](a, b) @[]T` — for the managed-slice + managed-slice
    shape.  `bootstrap.Concat` covers the char-slice case but is
    raw-slice-typed.
  - `Filter[T, P]` / `Map[T, U]` — block on closures or func-value
    params; only worth it once those constraints land properly.
  - `RemoveLast[T](s) @[]T` — `popLoading`-style pattern (rebuild
    minus last occurrence) repeats per element type.
  - Don't pre-add a kitchen-sink set — let the first 2-3 call
    sites pull each helper in.
- **Survey 2026-05-28** of the BUILDER-compilable tree: none of the
  above clears the "2-3+ same-shape sites" bar at the moment.
  Concrete numbers found:
    * `Concat[T]` over two managed slices: 0 sites; the only
      `Concat` callers all funnel through char-specialised
      `bootstrap.Concat`.
    * `Contains[T]`: 4 candidate sites (`containsTypePtr` /
      `containsName` / `containsPkgName` / `containsStr`) but each
      uses a different equality (Identical / charEq / streq), so
      collapsing them needs func-value comparators or method-based
      equality — gap.
    * `Reverse[T]`: 1 site (loader `popLoading`).
    * `RemoveLast` / `RemoveByValue[T]`: 1 site (also loader
      `popLoading`, but it's "rebuild minus *streq match*", which
      is `RemoveWhere` shape — not a pure index/value remove).
    * `Copy[T]` one-liner: 2 sites; most slice-copies in the tree
      are inlined in larger functions.
  So no new helper to add right now without going speculative.
- **The real next pkg/slices step** the survey surfaced: 168
  `slices.Append[T]` calls live inside `for` loops, i.e. O(n²)
  builds.  Folding those into a growable container with amortised
  O(1) append (a `Vector[T]` / `Builder[T]` shape with capacity
  tracking) is a substantive design, not a quick add — file it for
  later when the surface is being intentionally pulled into a
  proper stdlib effort.

### Replace repeated `WriteStr(literal)` runs with adjacent-string concat (opportunistic)
- **Pattern**: code that builds output via a CharBuf often calls
  `WriteStr` many times with adjacent string literals — e.g.
  `cb.WriteStr("foo"); cb.WriteStr("bar"); cb.WriteStr("baz")`.
  Binate allows adjacent string literals to be concatenated by
  juxtaposition (`"foo" "bar" "baz"`), so a single
  `cb.WriteStr("foo" "bar" "baz")` (split across lines for
  readability) does the same work in one call.
- **Why it matters**: each `WriteStr` call is a method dispatch
  plus a CharBuf grow check.  Collapsing the literals into one
  call cuts both, and is also less code to read.
- **Most of these are in tests**, which compounds with the
  slow-tests theme — every saved WriteStr in a test that runs
  under boot-comp-int-int (or any interpreted mode) saves
  bytecode-dispatch overhead × test count.
- **How to land**: opportunistic, file at a time.  Best
  candidates: `cmd/bnc/test.bn`'s `genTestRunner`, anywhere
  building LLVM-IR text, and test fixtures that paste source
  fragments together a chunk at a time.
- **First pass landed** (binate `07b21ed`, 2026-05-15): 18 files,
  ~200 runs coalesced (`cmd/bnc/test.bn`, `cmd/bnc/util.bn`,
  `cmd/bni/main.bn`, plus check_*_test.bn and emit_*_test.bn /
  gen_*_test.bn in pkg/types, pkg/codegen, pkg/ir).  The
  cmd/bnc/test.bn growth (524 → 533) prompted a follow-up split
  to a new `gen_test_runner.bn` — test.bn now 381 lines.

### Replace if-return chains with `switch` where applicable (opportunistic)
- **Pattern**: code that does
  `if x == A { ... return ... }; if x == B { ... return ... }; ...`
  over many cases.  Common in op-dispatchers, kind-handlers, and
  predicates.
- **Why it matters**: a `switch` makes the structure obvious (all
  cases over the same scrutinee, mutually exclusive), gives the
  type-checker a hook for exhaustiveness checking if/when it
  lands, and reads more naturally.
- **Watch out for**: chains where the conditions aren't really
  equality on a single scrutinee — those genuinely are
  if/else-if and should stay.  Also: the bootstrap subset
  supports `switch`, so this isn't restricted to non-bootstrap
  code (unlike the interface TODO above).
- **How to land**: opportunistic.  Top candidates: the per-op
  dispatchers in `pkg/native/arm64/arm64_dispatch.bn`,
  `pkg/codegen/emit_instr.bn`, `pkg/vm/vm_exec*.bn`, and
  `pkg/ir/ir_ops.bn`'s opName / similar string-form helpers.
- **Landed (2026-05-25/26)**: the big per-op dispatchers are
  converted — `pkg/vm/vm_exec_pure.bn` + `vm_exec_helpers.bn`
  (binate `b4456ab`, `e4e7d29`), `pkg/codegen/emit_instr.bn`
  (`2d6d0f7`), `pkg/native/arm64/arm64_dispatch.bn` (`3756acc`).
  Where a chain mixes equality cases with op-RANGE checks
  (emit_instr's OP_ADD..OP_SHR / OP_EQ..OP_GE; arm64_dispatch's
  emitCompare/emitBinop/emitUnop delegates), the range arms stay
  as guards alongside the switch.  `ir_ops.bn`'s opName was
  already a switch — nothing to do there.  This work flushed out
  a CRITICAL case-scope miscompile (managed local in a `case`
  body), since fixed (`4306197`) — see the FIXED entry above.
  Remaining candidates are smaller / lower-value (assorted
  if-chains in cmd/* and pkg/* tools).


- **Self-hosted (LANDED, 2026-05-01)**: type-checker
  (`pkg/types/check_stmt.bn:checkReturnStmt`) and IR-gen
  (`pkg/ir/gen_stmt.bn` STMT_RETURN branch) accept
  `return f(...)` when `f` returns the matching tuple. Each
  per-result type must be `AssignableTo` the outer's declared
  result. IR-gen lowers to one OP_CALL + one OP_EXTRACT per
  result; the existing return-RefInc/copy + temp-cleanup
  machinery handles ownership transfer. The literal-shape
  coercions in the per-expr return path (OP_CONST_NIL retyping,
  OP_CONST_STRING → string_to_chars, untyped-int width) all
  fire only on literals, which can't be call results — so the
  multi-return path skips them. The one non-literal coercion,
  `@[]T → *[]T` when the outer expects raw, is preserved on
  extracted values, mirroring the per-expr path.
  - Tests: `pkg/types/check_stmt_test.bn` (positive, arity-
    mismatch, type-mismatch); `pkg/ir/gen_stmt_test.bn`
    (`TestGenReturnMultiCallEmitsExtracts` pins
    1×OP_CALL + 2×OP_EXTRACT); conformance
    `347_return_multi_call` (all-scalar + mixed scalar/managed
    end-to-end; was 345 originally, renumbered after collision
    with `345_interface_decl`). xfail.boot. boot-comp /
    boot-comp-int / boot-comp_native_aa64 all green.
- **Bootstrap (pending decision)**:
  `bootstrap/types/checker.go:checkReturnStmt` (~963-978) still
  rejects this shape. Bootstrap acceptance is a separate
  question — the bootstrap subset is intentionally restrictive,
  and the self-hosted toolchain doesn't need this to compile.
  Defer until there's a concrete reason to widen the subset.
- Spec recorded in `claude-notes.md` ("Tail-call return for
  multi-return functions"). `bootstrap-subset.md` notes the
  bootstrap-only rejection.

### Mirror `return f(...)` acceptance in the Go bootstrap — LOW PRIORITY
- Self-hosted accepts the shape (commits `b88918e` /
  `d11e4f2` / `d3fc0db` / `96572fb` on main; conformance
  `347_return_multi_call`). Bootstrap still rejects it.
- **What's needed**:
  1. **Type-checker** (`bootstrap/types/checker.go:checkReturnStmt`,
     ~lines 963-978): when `len(s.Results) == 1` and
     `len(c.funcRet) > 1`, allow it iff the single expression is
     a `CallExpr` whose function type returns a matching tuple
     and each per-result type is `AssignableTo` the
     corresponding `c.funcRet[i]`. Mirrors the existing
     multi-return shape in `checkShortVarDecl` (~lines
     937-955) — same `(len(s.RHS) == 1 && rhsType is FuncType
     with matching Results)` predicate.
  2. **Bootstrap interpreter STMT_RETURN execution path**:
     extend it to handle the single-expression-multi-return
     shape, mirroring how `q, r := f()` is already executed
     (single call eval + per-result destructure).
  3. **Conformance**: drop `347_return_multi_call.xfail.boot`
     once both impls handle it. Drop the bootstrap-only
     rejection note from `bootstrap-subset.md`.
- **Why low priority**: the bootstrap subset is intentionally
  restrictive; the self-hosted toolchain doesn't need this to
  compile, and no in-flight work depends on it. Pick up when
  there's a concrete user (e.g., a self-hosted source file that
  wants the form, or a broader bootstrap-subset widening pass).

### pkg/codegen `TestEmitDebug*` dominates `boot-comp-int-int` runtime (perf)
- **Symptom**: pkg/codegen unit tests take ~1084s in CI under
  `boot-comp-int-int` (vs ~4s under `boot-comp-int`). The 26
  `TestEmitDebug*` tests account for ~78% of that runtime (~500s
  on local Apple Silicon, scaling up on CI x86). Top offenders:
  `TestEmitDebugStructWithArrayAndSliceFields` (~79s),
  `TestEmitDebugSliceFieldInStruct` (~41s),
  `TestEmitDebugSliceOfPointerChain` (~32s).
- **Isolated repro**: `TestEmitDebugStructWithArrayAndSliceFields`
  alone — 0.7s under `boot-comp-int`, ~120s under
  `boot-comp-int-int` (>100× slowdown for one test).
- **Mitigation in tree**: `scripts/unittest/pkg-codegen.skip.boot-comp-int-int`
  skips the `TestEmitDebug` substring under double interp. Coverage
  is preserved by every other mode that exercises codegen
  (`boot`, `boot-comp`, `boot-comp-int`, `boot-comp-comp*`).
- **Root cause to investigate**: each `TestEmitDebug*` runs
  `compileToLLVM(src)` with `SetDebugInfo(true)`. The DWARF emission
  path (DICompositeType chains, DIDerivedType members, member
  scope/baseType references) is heavy on string-building and
  small allocations. Under double interp every byte append /
  small allocation pays 2× bytecode-dispatch overhead, and there
  are many of them per test.
- **Possible angles** (investigated; first attempt was a net loss):
  1. Buffered string construction in `pkg/codegen/emit_debug*.bn`
     — coalesce per-node fragments to reduce CharBuf grows.  On
     inspection the literal-string `WriteStr` calls are already
     coalesced; the only repeating fusable pattern is `WriteByte('!')
     + WriteInt(id)` (~18 sites).  Mechanically fusable but ~18
     dispatches saved per node-emit × ~10 nodes/test ≈ milliseconds.
     Won't move 100s+ runtimes meaningfully.
  2. Cache stable strings (e.g. DI tag names, common type keys).
     **Tried 2026-05-13**: pointer-keyed cache in `dbgTypeID` that
     short-circuits `dbgTypeKey` for repeat lookups.  Single-test
     baseline 160s → 106s (-34%), but aggregate of all 26
     `TestEmitDebug*` went 441s → 513s (+16%) under boot-comp-int-int
     locally — the added pointer-scan per call pays off only when
     the registry is large (few slow tests) but slows the small-
     registry common case.  Reverted; needs a cache that's O(1)
     per call (e.g. a side-table on `@types.Type` itself, with the
     attendant `pkg/types` layout-contract implications).
  3. Reduce redundant work in the type registry — same composite
     type is rebuilt every call to `compileToLLVM`.  Cross-test
     state would also need per-module id offsets to keep nodes
     self-consistent; non-trivial.
- **Real next step**: actually profile before guessing again.  The
  intuition that "many small allocations × double-interp overhead"
  is the cost was correct in direction but wrong in distribution —
  most of the cost isn't where it looks like it should be.
- **Not blocking anything**; mitigation in tree (`1bffc43`).

### pkg/asm/aarch64 slow under `builder-comp-int-int` (perf)
- **Symptom**: under `builder-comp-int-int`, the
  `pkg/asm/aarch64` test package alone is slow enough to time
  out its CI shard at the 30-min cap. Other packages in the
  same mode finish comfortably.
- **Mitigation in tree** (2026-05-22): xfailed via
  `scripts/unittest/pkg-asm-aarch64.xfail.builder-comp-int-int`.
  Coverage is preserved by `builder-comp`, `builder-comp-int`,
  `builder-comp-comp*` and the native_aa64 / arm32 modes —
  this is purely a double-interp pacing issue.
- **Hypothesis**: same shape as the codegen `TestEmitDebug*`
  entry above — many small CharBuf / refcount / bounds-check
  operations per emitted instruction, each paying 2× bytecode-
  dispatch overhead under VM-on-VM. The aarch64 assembler is
  string-heavy (encoding tables, mnemonic dispatch). Hasn't
  been profiled.
- **Next step**: profile one `pkg/asm/aarch64` test under
  `builder-comp-int-int` to confirm the hypothesis and identify
  the actual hot path before guessing at fixes. See the codegen
  entry above for the lesson on guessing-without-profiling.
- **Not blocking anything**; mitigation in tree.

### Function values — MAJOR PROJECT (interop prerequisite)
- **Plan docs**: `explorations/plan-function-values.md` (parent;
  Phase 1 COMPLETE) + `explorations/plan-function-values-phase-3.md`
  (cross-mode trampolines; Slices 3.1, 3.1.5, 3.2, 3.3, 3.4 all
  LANDED).
- **Phase 1 COMPLETE (2026-05-01)**: A.1–A.7 all landed. Type
  syntax, nil + zero-init, function-reference-as-value, calling
  through a function value, flow through args/returns/fields,
  method expressions `T.M`, and non-capturing function literals
  (lifted to synthetic `__funclit_<n>` top-level Funcs).
  Conformance tests 338–342 + 344 cover each slice; pkg/ir + pkg/types
  unit tests cover each coercion site, AssignableTo predicate,
  and capture-rejection. `pkg/ir/gen_call.bn` and
  `pkg/ir/gen_func_lit.bn` extracted to keep file-length hygiene
  clean.
- **Phase 3 LANDED (per plan-function-values-phase-3.md)**:
  cross-mode trampolines bridge compiled ↔ VM through a uniform
  always-shim convention `<ret>(*uint8 data, <args>)`. Compiled
  side: per-function `__shim.<mangled>` set in each `__vt.<mangled>`'s
  `call` slot (Slice 3.1). Common kind-tag at the start of `data`
  (Slice 3.1.5) discriminates `DATA_KIND_VM_CLOSURE_REC` vs
  `DATA_KIND_COMPILED_CLOSURE` (Phase 2). Compiled→VM goes through
  `vm.TrampolineScalar`, a fixed 7-int-arg trampoline that reads
  VM handle + vm_func_idx from the closure rec and dispatches via
  `execFunc` (Slice 3.2). Bytecode→compiled goes through
  `dispatchCompiledFuncValue` (`pkg/vm/vm_exec_helpers.bn:247`),
  which routes via `rt._call_shim_scalar` — a new IR-magic helper
  alongside `_call_dtor` / `_call_free_fn`, lowered to
  OP_CALL_INDIRECT (Slice 3.3). The earlier `5f4333f` cross-mode
  hack for `func(*uint8)` is now reframed as `dispatchNativeIndirect`
  — the BC_CALL_INDIRECT counterpart of BC_CALL_FUNC_VALUE's
  data==null branch (Slice 3.4). VM handle lives in the
  VMClosureRec (not a global), so multi-VM works without ordering
  concerns. Bootstrap-subset constraint: scalars + pointers ≤7,
  no floats, no aggregates — broader signatures need additional
  trampoline shapes when they actually reach this path.
- **Phase 2 DEFERRABLE**: closures + capturing function literals;
  capture design (by-value vs by-ref, mutability, lifetime) is
  its own pass. The bytecode dispatcher (`BC_CALL_FUNC_VALUE`)
  already has a `DATA_KIND_COMPILED_CLOSURE` arm (clear-error
  guard) ready to fill in.
- **Downstream**: Phase 3's machinery is what the
  compiler/interpreter interop project needs. With per-signature
  shims + the `(data, args)` convention, a "package descriptor"
  of function-value pointers is enough to dispatch arbitrary
  cross-mode calls — no per-function hand-coding required. This
  also opens the door to retiring `pkg/vm/vm_extern.bn`'s
  hand-written extern arms (~30 of them, including the
  `rt.RefInc` / `rt.RefDec` arms flagged for retirement above);
  see the Compiler/interpreter interop entry below.
- **Reframed scope**: function values were originally framed as
  "blocked on / a piece of interop." Inverted: data interops fine
  via shared `.bni` layout; what crosses the compiled/interpreted
  boundary at runtime are *exported functions and methods passed
  as values*. The package descriptor the interop work needs is just
  a struct of function values per export. So function values are
  the **upstream prerequisite** for the broader interop project,
  not a sub-item of it.
- **Representation**: 2-word `{vtable, data}`, identical to
  interface values. The vtable type is per-signature; the vtable
  *instance* is per-(function, capture-shape). Vtable layout has
  `dtor` first (matching all other vtables — common destruction
  sequence) and `call` second. Function types are structural —
  `*func(...)` / `@func(...)` — with no user-visible "function
  interface" declaration; the compiler synthesizes the impls at
  function-literal and method-value sites.
- **Frontend syntax**: `*func(int) int` raw / `@func(int) int`
  managed, mirroring the slice migration (`*[]T` / `@[]T`) and the
  proposed interface revision. Bare `func(...)` is not a usable
  type.
- **Upstream prerequisite**: `plan-call-indirect.md` — LANDED.
  The `OP_CALL_INDIRECT` IR op (LLVM + VM + native arm64
  lowerings) is what Phase 1's vtable-indirect call sequence is
  built on. Already exercised end-to-end by RefDec's dtor
  dispatch; this plan's Phase 1 doesn't need to re-invent
  indirect dispatch.
- **Phasing** (per the plan doc):
  - **Phase 1 — backend vtable machinery + non-capturing function
    values.** This is primarily about *building the shared
    interface/vtable backend* (vtable type/instance generation,
    `call`-shim mechanism, vtable indirect-call sequence in
    compiler + VM). Non-capturing function values are the
    smallest user-visible thing the backend can deliver. The same
    machinery is what user-declared interfaces will need at the
    runtime layer. Non-capturing call sites use a check-data-nil
    short-circuit (consistent with other nil-checks in the
    codebase) rather than always going through the shim.
  - **Phase 2 — closures + method values (DEFERRABLE).** Capture
    analysis, closure-struct generation, receiver-capture for
    method values. **Capture design is open** (by-value vs. by-
    reference, mutability semantics, lifetime extension) and is
    its own design pass before implementation. Most current goals
    do *not* need Phase 2; the compiler and self-hosted runtime
    don't write closures, CallDtor retirement doesn't need it
    (see Path B above), and the interop descriptor exposes only
    non-capturing function values. Defer until there's a concrete
    user-facing need.
  - **Phase 3 — cross-mode trampolines.** LANDED. Per-signature
    (currently per-return-shape: TrampolineScalar) trampolines
    bridge compiled ↔ VM through the always-shim convention.
    See plan-function-values-phase-3.md for slice-by-slice detail
    and the "Phase 3 LANDED" bullet above for the LANDED summary.
    Unlocks the broader interop work; doesn't require Phase 2.
- **Recursive lambdas — explicit non-goal for Phase 1.** Go-style
  recursive closures (`var f = func(x) { ... f(...) ... }`) are
  NOT supported. Top-level named recursive functions work as
  always. Y-combinator pattern is the workaround if needed.
  Revisit when Phase 2 capture design is settled.
- **Backend dependency**: function values share the vtable layout
  and dispatch path with interfaces, but **not** the frontend
  interface syntax. They depend on the runtime/codegen vtable
  machinery, not on `plan-interface-syntax-revision.md`. Either
  plan can land first; both share the backend.
- **Method values** (`x.M`, `T.M`) and **closures** are folded
  under this plan rather than tracked separately.

### Interface syntax revision — *Stringer / @Stringer + top-level decl — MOSTLY DONE
- **Plan doc**: `explorations/plan-interface-syntax-revision.md`
  (RATIFIED 2026-05-01).
- **Implementation status (audited 2026-05-22 / 2026-05-23)**:
  Plan §1–§5 all landed.  §6 (`any` universal interface) landed
  end-to-end across type-checker (`e5f2f8a`) and IR-gen + codegen
  (`61eb6cd`): universe `any` is a real empty-method-set
  TYP_INTERFACE registered in both `pkg/types` (via
  `defineInterface`) and `pkg/ir` (via `registerUniverseAny` at
  `InitModule` time). `wrapAsIfaceValue` synthesizes a per-(T, any)
  ImplInfo on demand so codegen emits
  `__ivt.bn_<T_pkg>__<T>__any` as `[1 x i8*]` with T's dtor in
  slot 0 (or null if T has no dtor).  `@any` of a managed-field-
  bearing pointee now RefDec's the pointee's managed fields at
  scope exit via the synthesized vtable's dtor slot — the
  previously-silent leak is closed.
  Verified working: top-level `interface X { ... }` decl
  (`pkg/parser/parse_decl.bn:35`), `*Iface` / `@Iface` syntax
  (`pkg/types/resolve_type.bn:38-50`), bare-name rejection
  (`resolve_type.bn:30-35`, test 348), interface alias
  `interface X = Y` (test 369), construction-site explicit-only
  conversions (`types_assignable.bn:149-189`, tests 379/380/381),
  five receiver kinds + `impl T : Iface` (tests 357–410), per-
  (impl, interface) vtable codegen (`pkg/codegen/emit_impls.bn:24-40`),
  cross-package `.bni` interface visibility (tests 373–388, 464),
  universe `any` (tests 470–474, plus
  `pkg/ir/gen_iface_vtable_test.bn` for vtable-name mangling
  including the empty-pkg form).
- **Remaining (small) gaps**:
  1. **`type X = BareIface` explicit negative test** — the code
     flow should reject via `resolveTypeExpr`'s bare-interface
     error path, but it isn't separately covered. One-line
     negative test.
  2. **Interface-value nil comparison** — `iv == nil` (for any
     iv type, not just `*any`) is currently rejected:
     `IsNillable` in `pkg/types/types_query.bn:196` returns true
     only for pointer types and function-value types.  A nil iv
     IS a meaningful runtime state (both data and vtable slots
     zero, mirroring `*func(...)`'s convention), so the natural
     extension is to add `TYP_INTERFACE_VALUE` /
     `TYP_INTERFACE_VALUE_MANAGED` to `IsNillable`'s positive
     set and check both slots zero at the comparison site
     (codegen + VM lowering for `iv == nil`).  Not a regression;
     pre-existed plan §6 — surfaced while writing a nil-
     propagation test for the iv→any upcast.  This is a real
     language-semantics extension that should be confirmed
     before implementing.

### Cross-package method visibility in `.bni`
- Methods defined on a public type in package `foo` need to be declared
  in `foo.bni` for callers in other packages to see them — analogous to
  the existing `.bni` rules for free functions and types (covered by
  conformance tests 235/236, "Verify .bni vs .bn visibility semantics"
  is DONE).
- Currently, methods *do* work cross-package (conformance 330/331 cover
  it via `pkg/buf.CharBuf` methods called from `main`) because IR-gen's
  `RegisterImport` registers methods from the imported package's `.bn`
  source via the loader. That's a happy accident of the loader path, not
  a deliberate visibility design.
- Open: should `.bni` method declarations be required for cross-package
  visibility (matching free functions / types), and should the type
  checker enforce that? Today methods skip the `.bni` requirement.
- When picking this up, look at: how `pkg/buf.bni` declares its type but
  not its methods, yet cross-package callers still resolve them; whether
  to extend `checkBniSignatureMatch` to methods; whether `.bni` method
  decls are mandatory or just allowed.

### Verify anonymous struct equivalence — edge cases
- Both type checkers now implement structural equivalence for anonymous structs (field names + types in order)
- Needs edge case testing: nested anonymous structs, anonymous struct with managed fields, cross-package anonymous struct equivalence
- See claude-discussion-detailed-notes.md section 22

### Continue backfilling negative conformance tests
- 31 negative tests exist (112, 200-210, 214-221, 235-236, 238-246), covering type mismatches, undeclared vars, wrong args, nil semantics, operators, comparisons, field access, indexing, non-function calls, managed pointer misuse, multi-return, undefined types, .bni/.bn mismatch, visibility, imports, type conversion, const/break/continue/param, package mismatch, missing return, var redeclaration
- `.error` files use `grep -E` regex matching
- **Fixed diagnostics**: assign to const (238), break/continue outside loop (239, 242), duplicate param names (243), var redeclaration in same scope (246)
- **Remaining xfail'd**: missing return (245) — needs control flow analysis
- Bootstrap-only: package name mismatch not detected in single-file mode (244 xfail on boot)
- Still needed: const expression errors, more shadowing edge cases

### Readonly method receivers — deferred (gated on methods/interfaces)
- A method's receiver kind (`*readonly T` / `@readonly T`, plus value
  receivers — which are always readonly) determines which pointer kinds
  satisfy an `impl` and bounds what the method may mutate.  See
  `claude-notes.md` (value receivers always readonly; readonly-restricted
  dispatch expressed at the impl level; `*readonly T` receiver smoothing
  auto-takes `&t` at the call site).
- This was "Stage 3" of the old `const` type modifier.  The rest of that
  work landed and the type-level modifier is now spelled `readonly`
  (`plan-const-readonly.md`, COMPLETE 2026-06-03 — `const` split into
  compile-time `const` / `var` storage / `readonly T` modifier; that
  plan's three listed deferrals — readonly-slice slicing, `.bni`
  extern-var, `&pkg.Const` — are all since resolved).
- Deferred, not abandoned — depends on the methods/interfaces feature.
  Fold into that project's tracking when it firms up.

### Observable optimizations and UB policy — broader question
- Surfaced while planning const: allowing the compiler to allocate
  a shared static global for all-const composite literals is an
  optimization observable via raw-pointer comparison (`&a[0] ==
  &b[0]` where `a`, `b` are both `"hello"`). The const plan accepts
  this as UB rather than either blocking the optimization or
  carving out precise "same-literal-text gives same address"
  semantics.
- Same class as the refcounting move optimizations that are already
  observable via `rt.Refcount(...)` without a nailed-down spec.
- **Broader question**: do we want a general policy of "these kinds
  of observations are UB, the compiler may optimize across them",
  written up somewhere authoritative? Candidates for the same UB
  bucket: literal address identity, refcount timing, struct padding
  bytes, uninitialized-memory reads of stack-allocated vars. The
  alternative (fully specified observable behavior) is probably
  incompatible with small-target codegen goals.
- Not urgent — we're already making these trade-offs silently. A
  short design note ratifying the policy would be useful when a
  future optimization / feature forces the question.

### Switch `fallthrough` — proposal
- Not in the current grammar (`grammar.ebnf`). Binate switch cases are implicit-break (Go-style), but there's no opt-in for Go's `fallthrough` keyword.
- Would add one reserved keyword, one AST statement kind (`STMT_FALLTHROUGH`), and one IR lowering (branch to the next case's entry block, skipping its case-value check).
- Before implementing: decide whether we want it at all. Arguments for: matches reader expectations from Go, lets users avoid duplicated bodies across related cases. Arguments against: rarely needed in practice, adds a new keyword for a small ergonomic win, forces the type checker to recognize terminators beyond `return`/`panic` (termination analysis already inspects case bodies for bare `break`).
- Likely a decline unless a concrete use case comes up, but worth capturing as a live option.

### Termination analysis — labeled break
- Missing-return check (test 245) uses Go-style termination analysis simplified: RETURN terminates; `panic(...)` terminates; BLOCK terminates if last stmt does; IF terminates if both branches do; FOR with no condition and no `break` in body terminates; SWITCH with default and all cases terminating (no break) terminates.
- **Labeled break**: Binate currently has no labels. If/when we add them, termination analysis needs to track labels — a `break L` inside a nested for doesn't break the inner for (contrary to the current "any break disqualifies enclosing for/switch" rule). Revisit when labels are on the table.

### Clean up conformance tests to use array literal + `arr[:]` pattern
- `arr[:]` works in compiled mode; conformance tests using `make_slice` + indexed assignment for static data could use `[N]T{...}` + `arr[:]` instead
- Consider adding slice literal syntax (`*[]T{...}`) as sugar

### DWARF debug info — foundation in place, type coverage missing
**Done** (via `56ea542`, `a15ef50`, `2cd2c25`):
- `-g` flag in `cmd/bnc`, `SetDebugInfo` in `pkg/codegen`; off by default.
- Module-level: `source_filename`, `DICompileUnit` (FullDebug), `DIFile`, `DISubroutineType`, per-function `DISubprogram`.
- Line-level: `Line int` field on `ir.Instr` (`pkg/ir.bni:170`). `genExpr` sets `.Line` from `e.Pos.Line` (`pkg/ir/gen_expr.bn:16`). `annotateBlockInstrs` backfills zero-line instrs to statement line (`pkg/ir/gen_stmt.bn:11-14`). Per-instruction inline `!DILocation(line: N, scope: !M)` in emitted LLVM (`pkg/codegen/emit_debug.bn:99-114`).
- Variables: `llvm.dbg.declare` + `DILocalVariable` for named allocas (`emit_debug.bn:139-162`). Names propagated via `StrVal` on `OP_ALLOC`.
- lldb/gdb now show Binate function names, file, line numbers, and local variable names.

**Gaps**:
- ~~Type coverage is basically just `i64`.~~ FIXED for scalars,
  pointers, structs, slices, interface-values, function-values,
  arrays, and named typedefs (2026-05-07/08).
- ~~Parameters don't get `DILocalVariable`~~ — FIXED (2026-05-07).
  Param allocas were already named so the existing dbg.declare
  fired; step 3 added `arg: <N>` so lldb shows them as function
  arguments rather than mixed in with locals.
- ~~`DISubprogram` has `line: 0` and `scopeLine: 0`~~ — FIXED
  (2026-05-07). `ir.Func` carries a `Line` field; gen_func.bn
  populates it from the AST decl's `Pos.Line`; emit_debug.bn
  threads it into both the `line:` and `scopeLine:` fields.
  Synthetic helpers (init dispatcher / entry wrapper / dtor /
  copy stubs) keep `line: 0`.
- ~~`DISubroutineType` is a single shared generic~~ — FIXED
  (2026-05-09). Per-function DISubroutineType + types tuple
  emitted; void/nullary funcs get `!{null}`, parameterised funcs
  get `!{<ret-or-null>, <param1>, ...}` referencing the type
  registry. See step 7 below.
- No `llvm.dbg.value` (only `dbg.declare` for allocas).
- Line positions: only `genExpr` explicitly threads `.Line`; most IR-emission sites rely on statement-line backfill (coarse). No columns.

**Reasonable next steps** (roughly ordered by effort/payoff):
1. ~~Emit `DIBasicType` for each scalar kind~~ — DONE (2026-05-07).
   Unit tests in `pkg/codegen/emit_debug_test.bn` pin the slot
   layout (`TestDbgTypeIDScalars`), the emitted DIBasicType nodes
   (`TestEmitDebugBasicTypesEmitted`), and the `dbg.declare` →
   slot wiring (`TestEmitDebugDeclareReferencesScalarType`). Full
   conformance (boot-comp, 317/0) compiled with `BINATE_FLAGS=-g`.
2. ~~Capture function definition lines into `DISubprogram`~~ —
   DONE (2026-05-07). `TestEmitDebugSubprogramLine` pins
   `line:` / `scopeLine:` for two functions on different source
   lines; `TestSyntheticFuncDefaultLineZero` pins the synthetic
   `Line == 0` invariant.
3. ~~Emit `DILocalVariable` for parameters~~ — DONE (2026-05-07).
   Step actually emitted `arg: <N>` on the existing DILocalVariable
   for params (vs. the gap entry's premise of "no dbg.declare for
   params" — the dbg.declare was already firing once defineVarParam
   tagged the alloca). Tests:
   `TestEmitDebugDeclareParamsCarryArgIndex`,
   `TestEmitDebugMethodReceiverIsArgOne`,
   `TestParamAllocaParamIndex`.
4. ~~Emit `DICompositeType` for structs / `DIDerivedType` for
   pointers~~ — DONE (2026-05-08). `pkg/codegen/emit_debug_types.bn`
   carries a per-module type registry keyed by structural string
   (raw vs managed pointers distinguished); ids allocate past the
   per-function metadata block. Recursive interning means a
   `*Counter` local pulls in Counter's struct nodes; field types
   route back through `dbgTypeID` so scalar fields wire to !5..!15.
   Tests in `emit_debug_types_test.bn` cover pointer + struct
   emission, the pointer-to-struct chain, the dedup invariant, and
   the structural-key helper. Full conformance under -g: 327/0.
5. ~~Wire slices, managed-slices, interface-values, function-values,
   arrays, and named typedefs into the registry~~ — DONE
   (2026-05-08). New `pkg/codegen/emit_debug_aggr.bn` carries
   intern + emit functions for each kind. Slices map to
   DICompositeType DW_TAG_structure_type with the runtime layout
   (2-word for raw, 4-word for managed); iface and func values
   map to 2-word DICompositeType; arrays map to DICompositeType
   DW_TAG_array_type with DISubrange(count:); named typedefs map
   to DIDerivedType DW_TAG_typedef. Tests in
   `emit_debug_aggr_test.bn`. Full conformance under -g: 327/0
   (1 unrelated xfail). NOTE: TYP_NAMED rarely surfaces in
   today's IR-gen because `type Pos int` is currently treated
   as an alias and unwrapped before reaching the alloca's
   TypeArg; the typedef path is in place for when distinct-
   named-type semantics land.
6. Thread positions through more IR-gen sites (statements, assignments, calls) for finer-grained `DILocation`.
7. ~~Per-function `DISubroutineType` with real parameter + return
   types~~ — DONE (2026-05-09). `setupDbgFuncSubroutineTypes`
   allocates a (typesList, subrType) id pair per non-extern Func
   and eagerly interns each function's param + return types so the
   tuple resolves; `emitDbgFuncSubroutineTypes` writes both nodes
   after the per-function metadata block. DISubprogram now
   references the per-func DISubroutineType instead of `!4` (the
   legacy shared empty placeholder remains for backwards compat).
   Tests in `emit_debug_test.bn`:
   `TestEmitDebugSubroutineTypePerFunc` (non-!4 + `!{!5, !5...}`
   shape), `TestEmitDebugSubroutineTypeVoidNullary` (`!{null}`),
   `TestEmitDebugSubroutineTypeVoidWithParam` (`!{null, !5}`).
   Full conformance under -g: 327/0 (1 unrelated xfail).

### Package manager — sketch a design
- We don't have one yet. The current model is "everything lives under a
  root directory; `-I` and `-L` point the loader at extra search paths."
  Fine for the toolchain and a handful of conformance fixtures; doesn't
  scale to "I want to depend on `someone/foo` at version vX."
- Questions a sketch should answer:
  - Naming: are packages identified by URL (`github.com/...` Go-style),
    by a registry name, by a flat namespace? Interacts heavily with the
    package-name/path conventions item below.
  - Manifest file format and location (`binate.toml` / `bn.mod` / TBD).
    What does a minimal valid manifest look like?
  - Dependency resolution: version constraints, lockfile, MVS vs SAT,
    handling of mutually-incompatible transitive deps.
  - Vendor / cache layout: per-project, per-user, or system-wide.
    Reproducibility story.
  - Binary artifacts vs. source: tied to the existing IMPL_PATH split
    (compiled `.o` / `.a` distribution vs. source) — see
    "Package path: binary artifacts on IMPL_PATH (Stage 8 / Phase 2)"
    below.
  - Interop with `.bni` distribution: the loader already treats `.bni`
    and impl as independent search paths; the package manager must
    respect that.
  - Bootstrap path: how does the bootstrap interpreter find packages?
    Probably "vendored copy in tree, no resolver." Confirm that's the
    right answer.
  - Out-of-tree builds: where do build artifacts go? How does the
    package manager interact with `--build-dir`?
- Output: a plan doc in `explorations/` (e.g. `plan-package-manager.md`),
  not implementation. Decisions are interleaved with the name/path
  conventions item below — sketch and conventions probably ratify
  together.

### Package name/path conventions — decide and possibly reorganize
- Current `pkg/` layout mixes toolchain internals (`pkg/parser`,
  `pkg/types`, `pkg/codegen`, …) with runtime (`pkg/rt`), bootstrap
  support (`pkg/bootstrap`), libc bridges (`pkg/libc`), and small
  utilities (`pkg/buf`, `pkg/mangle`, …). Future stdlib packages would
  pile in alongside them with no organizing principle.
- Questions to answer:
  - Should toolchain internals live under a distinct prefix
    (`compiler/parser`, `compiler/types`, …) so that "what's stdlib"
    vs. "what's compiler implementation" is visible at the import
    path? Same question for runtime / bootstrap support.
  - What does a Binate package path *look* like? Is `pkg/` a real
    prefix or just a directory convention? Are external (third-party)
    packages spelled differently?
  - How do package paths interact with the package manager's naming
    scheme (URL? registry name? short alias)?
  - Mangling: short package names (`mangle.PkgShortNameFromModule`)
    currently derive from the path's last segment. If conventions
    change, mangled symbol names change, which affects ABI. Plan a
    migration story.
  - Are there packages that should move? `pkg/bootstrap` is arguably a
    stdlib piece; `pkg/rt` is closer to runtime-internal; toolchain
    internals could become `compiler/...`. Each move is a real refactor.
- Heavily entangled with the package-manager sketch — they should
  probably ratify together, since the manager design depends on what
  paths look like.
- Output: a plan / decision doc in `explorations/`. Reorganization is
  a follow-up project.

### Tier + dependency-direction hygiene checks (enforce `pkg-layout-spec.md`)
- **What**: a hygiene check (new script under `scripts/hygiene/`, alongside
  `conformance-imports.sh`) that enforces the tier dependency-direction rule
  from [`pkg-layout-spec.md`](pkg-layout-spec.md): a package may import only
  packages at its own tier or **lower**; importing a strictly-higher tier is
  a violation.  Tiers, low→high: 0 / 0b (`pkg/builtins/*`) < 1 (`pkg/std/*`)
  < 1x (`pkg/stdx/*`) < 2 (`pkg/<org>/*`, e.g. `pkg/binate/*`) < 3
  (app-specific).  E.g. `pkg/builtins/rt` importing `pkg/std/io` is illegal;
  `pkg/binate/parser` importing `pkg/std/os` is fine.  (This is the runtime
  enforcement of the spec's "Transitive constraint" + tier table.)
- **Special case — `pkg/std` → `pkg/stdx`**: tier 1 (`std`) may depend on
  tier 1x (`stdx`) **internally** (in `.bn` impl files) but **not externally**
  (in `.bni` interface files).  A `.bni` importing `stdx` would leak a
  no-inter-version-compat (1x) type into `std`'s strict-compat (tier 1)
  surface.  So the check must scan `.bni` imports separately from `.bn`
  imports: the std→stdx edge is allowed only from `.bn`.  (Generalize if
  other interface-vs-impl tier asymmetries surface.)
- **How**: derive each package's tier from its path — the realized layout
  makes tier path-derivable (`ifaces/core` + `impls/core/*` → tier 0/0b;
  `ifaces/stdlib/pkg/std` → tier 1, `…/pkg/stdx` → tier 1x; `pkg/binate/*`
  → tier 2).  Walk every package's imports (split by `.bni` vs `.bn`), map
  importer + imported to tiers, flag any higher-than-self edge, applying the
  std/stdx interface refinement.  A whitelist file (cf.
  `conformance-imports.whitelist` / `naming.whitelist`) covers sanctioned
  exceptions.
- **Scope** (per CLAUDE.md "Stay Within the Asked Scope"): add the script
  only; wiring it into `scripts/hygiene/run.sh` and CI is a separate decision
  for the user.

### Per-file build constraints — conditional file inclusion/exclusion by target — DESIGN
- **What**: a way for a single file to opt *itself* in or out of
  compilation based on the build configuration — arch, target triple,
  OS, libc-vs-freestanding, backend (LLVM / native-aa64 / native-x64),
  engine (`bnc` compiled vs `bni` interpreted), etc.
- **Why the current mechanisms are inadequate**:
  - **Separate trees + symlinks** (what we have now —
    `impls/{common,libc,baremetal}/…`, per
    [`pkg-layout-spec.md`](pkg-layout-spec.md) invariant 5 "Whole-package
    selection only"): too **coarse** (selection is whole-package /
    whole-variant-dir; "shared core + one per-variant file in the same
    package" is unrepresentable) and too **annoying** (symlinks to share
    the common files across variant dirs; a new axis means a new tree).
  - **Go-style filename suffixes** (`foo_posix.bn`, `foo_arm32.bn`): too
    **magical** (the constraint is invisible *inside* the file, smuggled
    in via the name) and too **coarse** (only a fixed suffix vocabulary;
    can't express conjunctions/disjunctions like "arm32 AND libc", or
    "any of {x64,aa64} but not baremetal").
- **Proposed shape**: an **annotation (writ large) near the top of the
  file** declaring the file's applicability condition as an *expression*
  over target predicates (`arch == "arm32"`, `libc`, `engine == "bni"`,
  with `&&` / `||` / `!`).  Two candidate syntactic forms to weigh:
  - a real **annotation on the `package` clause** (e.g.
    `#[build(arch == "arm32" && libc)] package foo`) — first-class,
    grammar-integrated, parseable; but the file must parse far enough to
    read it before we know whether to compile it, so the condition has to
    be evaluable from a cheap leading-prefix scan (read annotation →
    decide → continue or drop the file);
  - a **comment-form pragma** (a recognized leading comment, e.g.
    `//bn:build arch == "arm32" && libc` — Go-`//go:build`-shaped but
    expression-based, not suffix-based) — even cheaper to scan, but
    out-of-grammar / more "magical".
- **Design questions**:
  - **Predicate vocabulary + authority**: arch, triple, OS,
    libc-vs-freestanding, backend, engine, possibly user-defined build
    tags.  Where is the canonical list defined?  How extensible?
  - **Relationship to the `impls/` trees**: does this *replace* the
    `{common,libc,baremetal}` split (collapse back toward one tree, files
    self-select) or *complement* it (trees for the coarse axis,
    annotations for the fine)?  At minimum it should retire the symlink
    workaround; possibly the per-variant impl dirs too.  Decide
    explicitly — interacts with `pkg-layout-spec.md`.
  - **Loader/merge interaction**: excluded files simply don't join the
    merged package; ensure a package can still be legitimately empty (or
    require ≥1 surviving file) for a given target without spurious errors.
- **Tooling interaction (the bnlint question)**:
  - bnlint + the hygiene scripts must **understand** the annotation, so a
    file inapplicable to the current config isn't false-flagged (and so
    they can choose to lint each file under its applicable config(s)).
  - **Corollary worth designing in**: the same annotation surface could
    carry a directive telling bnlint / hygiene checks to **skip or ignore**
    a file (or regions of it) — a first-class "lint-exempt this file"
    mechanism, unifying build-constraints and lint-control under one
    annotation vocabulary.
- **Related entries to unify with**: the MAJOR "Better test-mode/target
  annotation than `.xfail`" entry above wants exactly this shape for
  *tests* (declare applicable modes/targets); and "Annotations and C
  function interop" below is the general annotation-syntax design.  This
  is the *source-file* instance of the same idea — design them together.
- **Prior art to consult**: Go build constraints (the `//go:build`
  expression form that replaced the `_GOOS` suffix era), Rust
  `#[cfg(...)]` / `cfg_if!`, Zig comptime target switches.  The
  expression form is the model.

### Conformance tests: consider a separate repo
- Running conformance tests in CI creates a circular dependency: the bootstrap repo needs the binate repo (which contains the test cases), and the binate repo needs the bootstrap binary (to run the tests)
- Consider moving conformance tests to their own repo (e.g., `binate/conformance`) that both repos reference
- This also gives a natural place for test infrastructure (run.sh, runners, xfail metadata) that doesn't belong to either the bootstrap or self-hosted repo
- The unit test runner (`binate/scripts/unittest/`) has a similar issue — it's in the binate repo but the `boot` mode runs via Go in the bootstrap repo

### Language spec(s) — write the primary spec; later, secondaries
- See `claude-notes.md` § "Language specification — primary spec is
  minimal — DECIDED" for the philosophy.
- **Primary language spec**: syntax, type system, semantics, plus
  *only* the packages intrinsically tied to the language
  implementation — `pkg/rt` (after the review below) and a future
  reflection/introspection package. Includes the one-line note that
  user files cannot be named `*_test.bn` (reserved).
- **Minor secondary spec — testing**: `_test.bn` packaging
  convention + `pkg/builtin/testing`. May fold into primary; TBD.
- **Major secondary spec(s) — stdlib**: I/O, containers, formatting,
  string utilities, etc. Probably split across multiple specs by
  area.
- **Not started.** Discussion-only at this point. When writing
  begins, the natural artifact is `explorations/spec-*.md` (or a
  separate `spec/` directory). The primary spec is gated on the
  pkg/rt review entry below, since the primary spec describes
  pkg/rt's normative surface.

### pkg/rt review — decide runtime vs. stdlib vs. internal
- Today `pkg/rt` is a grab-bag of runtime helpers, refcount
  primitives, allocator wrappers, bounds-check stubs, etc.
- For the primary spec to nail down "what the runtime contract
  is," `pkg/rt`'s surface needs a review: classify each member as
  **stay** (truly language-runtime, normative in the primary
  spec), **move** (standard-library-shaped — belongs in a stdlib
  package, out of `pkg/rt`), or **make-internal** (only used by
  the language implementation itself, no `.bni` export).
- Output: a classification of `pkg/rt` members + a follow-up
  cleanup plan (a `plan-*.md` doc under `explorations/`). The
  cleanup itself is separate work and can be sequenced
  independently — what's important first is the *classification*,
  which unblocks the primary spec writeup.

### Standard library design
- Candidates: growable collections (Vec[T], Map[K,V] post-generics), I/O abstractions, string utilities, formatting
- CharBuf is implemented (pkg/buf); broader stdlib design should inform future collection APIs

### Slice ownership model — design notes
Binate is NOT Go. The two types of slice are intentionally different:

**Raw slices (`*[]T`)** — two words: (data ptr, length)
- Value types, no refcounting, no GC. Caller manages lifetime (like C).
- Cannot be compared to `nil` — check `len(s) == 0` for empty.

**Managed-slices (`@[]T`)** — four words: (data ptr, length, backing_refptr, backing_len)
- Prefix-compatible with `*[]T`. Refcounted via backing_refptr.
- backing_len stores total element count for destructor cleanup.
- `make_slice(T, n)` returns `@[]T`. `@[]T → *[]T` conversion: extractvalue fields 0,1.

### Test runner improvements
- ~~**Better docs/help**~~: DONE. Both runners show description, examples, flag docs, test format/convention docs, xfail mechanism. READMEs added for conformance/ and scripts/unittest/.
- ~~**Better output**~~: DONE. `-v` (verbose: all test names), `-q` (quiet: failures+summary only), default (dots for passes, detail for failures).
- ~~**Mode sets in files**~~: DONE. `scripts/modesets/` directory with one file per set (basic, all, full). Adding a new mode set is just adding a file. Both runners read from the shared directory. Help output dynamically lists available sets.
- ~~**Better mode specification**~~: DONE. Comma-separated modes (`boot,boot-comp`) expand into sequential runs. Works alongside mode set files.
- ~~**Better filtering (unit tests)**~~: DONE. Fixed unit test runner to use substring match (was exact match). `token` now matches `pkg/token`, consistent with conformance runner.
- **Better filtering (individual test functions)**: ability to specify individual test functions, not just packages (e.g., `run.sh boot-comp pkg/ir TestFoo`).
- **Timeout/hang handling**: better and/or automatic detection and handling of tests that hang.
- **Parallelization**: consider running test packages in parallel within a mode.

### ARM32 bare-metal target — MAJOR PROJECT
- **Why**: enable Binate as an OS-development language on ARM32
  bare-metal (Cortex-A and possibly Cortex-M). Bare-metal is the
  endgame — we want to write the OS in Binate, not run on top of
  one. **ARM32 Linux via LLVM** has been added to the plan as an
  explicit v0 derisking step (it shares all the prerequisites and
  validates the 32-bit type-system path without committing to
  bare-metal runtime work); see plan doc.
- **Existing substrate that already handles bare-metal cleanly**:
  - `pkg/asm/arm32` encodes ARMv7-A instructions (data-processing,
    load/store, multiply/divide, branches, system); 73 unit tests pin
    bit patterns. Assembler-side is essentially done.
  - `pkg/asm/elf` emits ELF32 with the right ARM32 reloc set
    (R_ARM_JUMP24, R_ARM_ABS32). End-to-end tests in
    `pkg/asm/elf/elf_test.bn` already link with `arm-none-eabi-ld`
    (bare-metal linker) and run under `qemu-system-arm -semihosting`
    on virt machine. Three tests: exit, loop sum, function call.
  - `cmd/bnas` already accepts `.arch arm32` and routes through the
    ARM32 instruction parser.
- **What's missing**: an IR-to-machine-code lowering for ARM32 (a
  `pkg/native/arm32` sibling of `pkg/native/arm64`), and a bare-metal
  runtime port.
- **The interesting bit: bare-metal makes the runtime story
  non-trivial.** Things the language/runtime currently assumes from
  the host that don't exist on bare metal:
  - **Allocator**: `pkg/rt`'s managed-pointer/managed-slice
    allocations go through `bn_rt__c_malloc` / `bn_rt__c_free` /
    `bn_rt__c_calloc` (libc-shaped C stubs). On bare metal we need
    a Binate-implemented allocator — probably a simple bump
    allocator first (no free, suitable for early boot), then a real
    heap (free-list or buddy). Allocator implementation lives in
    pkg/rt (or a peer package) and replaces the `c_*` bridges for
    the bare-metal target. The existing "Un-export `rt.c_*`" TODO
    is a prerequisite — once those are private, we can swap them.
  - **`memset` / `memcpy`**: tiny Binate or asm implementations.
  - **Exit / abort / panic**: semihosting `SYS_EXIT_EXTENDED` for
    QEMU testing; on real hardware, `wfi` loop or reset.
  - **I/O**: no stdout/stderr — need a UART driver or semihosting.
    Two flavors:
    - Semihosting (used by the existing QEMU tests): debug-only,
      requires a debugger / QEMU. Useful for development, not for
      shipping.
    - UART: target-specific MMIO. Need a small driver per board —
      PL011 for ARM virt machine, vendor-specific for real hardware.
      The `bootstrap.Write` extern would dispatch to a board-defined
      `uart_putbyte` instead of `write(2)`.
  - **`bootstrap.*` shape**: today's bootstrap.bni is libc-shaped
    (Open / Read / Write / Stat / Args). Bare metal has no
    filesystem and no argv. We'd want a smaller bare-metal-friendly
    bootstrap interface — probably just an output sink and a panic.
    The `formatInt` / `formatBool` / `formatFloat` helpers stay
    (they're pure Binate); only the I/O surface changes.
- **Boot**: a tiny crt0 in asm (or Binate inline-asm if we ever add
  it) to set up the stack, zero BSS, copy .data from flash to RAM,
  then jump to `bn_main`. Provided as a per-board file alongside the
  linker script.
- **Linker script**: per-board memory map (text/rodata in flash, data
  in RAM, BSS, stack at top of RAM, optional MMU page tables for A-
  class). The QEMU virt machine convention (text at 0x40000000) is a
  good first target.
- **Two paths to actual codegen**, similar to the ARM32-Linux
  consideration but with bare-metal twists:
  - **LLVM-via-clang**: pass `--target=armv7a-none-eabi`,
    `-mfloat-abi=soft` (or `hard` if we want NEON/VFP), no sysroot.
    Fastest to first-light, but the LLVM dependency is heavier on a
    bare-metal toolchain story (we'd need to ship clang + lld or
    require the user to have a cross toolchain installed).
  - **Native pkg/native/arm32**: full sibling of `pkg/native/arm64`.
    AAPCS32 calling convention (NGRN over R0..R3, args 5+ on stack,
    return values in R0..R3, large-aggregate return via the hidden
    pointer in R0). Mach-O isn't relevant here — only ELF32 output.
    No external dependency once written. Larger upfront cost; closer
    to the OS-language goal of "no LLVM at runtime."
- **Testing**: the existing `pkg/asm/elf` semihosting harness scales
  up — write conformance programs that use only the bare-metal
  runtime surface, link with `arm-none-eabi-ld`, run under QEMU
  with `-semihosting`. Once the UART driver lands, switch to
  reading stdout from QEMU's serial0.
- **Adjacent in-flight items that affect this**:
  - "Un-export `rt.c_*`" — direct prerequisite for swapping the
    allocator/memops bridges per-target.
  - "Native AArch64 backend cluster A" — in flight; the
    common AAPCS dispatch helper in `pkg/native/common` is shared
    between ARM64 and a future ARM32, so ARM32 work shouldn't start
    until the ARM64 native backend is stable enough that we know the
    common shape is right.
  - The compiler/interpreter interop work is independent of this —
    interop is mostly a layout/representation question, not a
    target question.
- **Suggested first milestone**: get a meaningful subset of
  conformance running on QEMU via the LLVM backend with semihosting
  I/O. Concretely:
    - Pick the codegen path: LLVM-via-clang first
      (`--target=armv7a-none-eabi -mfloat-abi=soft`). Defer the
      native `pkg/native/arm32` backend until LLVM-via-clang
      validates the runtime/boot/linker story.
    - Implement a bump allocator in `pkg/rt` (no free) — enough for
      every conformance test that doesn't actually run out of memory.
      Allocations touch managed-pointer / managed-slice paths only,
      so this is the same surface the existing `c_malloc`/`c_calloc`
      bridges expose. Wire it behind a build-mode switch alongside
      the existing libc-bridges path.
    - Implement semihosting `SYS_EXIT_EXTENDED` (already used by the
      pkg/asm/elf QEMU tests) and `SYS_WRITE0` for putchar/print.
      Replace `bootstrap.Write` (the I/O primitive everything
      eventually funnels into after the print rewire) with the
      semihosting variant for this target.
    - Add `memset` / `memcpy` in pure Binate (or a tiny inline-asm
      wrapper if one is later added).
    - Conformance tests that DON'T touch file I/O / argv / dirs
      should pass: arithmetic, control flow, structs, slices,
      managed pointers, methods, etc. Probably 200+ of the existing
      278. Tests that rely on `bootstrap.Open` / `Read` / `Args` /
      `Stat` / `ReadDir` / `Exec` would be excluded for v1.
- **Plan doc**: `explorations/plan-arm32-bare-metal.md` exists as a
  **DRAFT** (initial sketch — not yet ratified). Covers the items
  above plus: target board choice (QEMU virt + one real Cortex-A
  board TBD), allocator design (bump first, heap second), bare-
  metal `bootstrap.bni` shape, boot/linker-script convention, and a
  placeholder for the per-package inventory of `bootstrap.*` calls
  (the inventory itself is deferred to a follow-up). Needs review
  pass before any implementation begins.

### Compiler/interpreter interop — MAJOR PROJECT
- **Why this is high priority**: dual-mode execution is a core promise of the
  Binate language. Compiled-and-interpreted code calling each other (in both
  directions) is what makes "compile some packages, interpret others" actually
  useful. We should make this real BEFORE pushing on more language features —
  large language additions risk locking in design choices that close off
  interop options.
- **Likely-already-compatible substrate** (verify rather than redesign):
  - **In-memory layout of types** is supposed to match across modes. Compiler
    uses `pkg/types`'s SizeOf/AlignOf/FieldOffset; interpreter uses (or should
    use) the same. Verify with a small cross-mode struct-pass test.
  - **Refcounting**: managed allocations carry a header with refcount and a
    pointer to the destructor, populated at allocation site. Compiled and
    interpreted code use the same `rt.RefInc` / `rt.RefDec` / `rt.Free`. Free
    paths invoke the per-type dtor through the header, so a managed value
    allocated on one side and dropped on the other should clean up correctly.
    Verify with a cross-mode managed-pointer round-trip.
- **Direction to start with**: interpreted code calling compiled code. Simpler
  than the reverse (no need for the compiler to plant trampolines into a
  running interpreter). Once that works, compiled code calling interpreted
  code falls out roughly symmetrically.
- **Granularity: package-level.** For interpreted code in package P to call
  into a compiled package Q, the interpreter needs:
  - Q's `.bni` (so the interpreter can type-check P against Q's signatures —
    this already works today via the existing `.bni` loading path).
  - **Pointers to Q's compiled functions** (the actual interop primitive).
- **Proposed mechanism: auto-generated package descriptor.** The compiler emits,
  for each package Q, a synthetic `const` of a synthetic struct type — call it
  e.g. `foo.Package` (working name; could be `foo.PackageImpl` or another
  canonical name) — whose fields are pointers to Q's exported functions in some
  canonical order (e.g., sorted by mangled name). The interpreter, when it
  loads compiled package Q, reads that descriptor and binds each field as the
  function value for the corresponding name in Q's scope. Naming and layout
  must be canonical so an interpreter built against Q's `.bni` can read Q's
  descriptor without further metadata.
- **Symmetry**: the interpreter should produce the same shape on its own end —
  for each interpreted package, expose a `foo.Package` whose function-pointer
  fields are trampolines into the interpreter (call into the bytecode VM
  using the trampoline's bound bytecode/closure-env/types/aliases). That way
  compiled code calling interpreted code is the same mechanism, mirrored.
- **Prerequisite — DONE**: function values (see
  `plan-function-values.md` + `plan-function-values-phase-3.md`).
  The descriptor's fields are pointers to functions — that's
  exactly what function values are. The 2-word `{vtable, data}`
  representation, the `(*uint8 data, <args>)` always-shim
  convention, the per-function `__shim.<mangled>` shims, the
  bytecode-side `dispatchCompiledFuncValue` (via
  `rt._call_shim_scalar`), and the compiled-side `TrampolineScalar`
  are all in place. The remaining work is the descriptor itself
  (naming, layout, emission, loading) plus the symmetric VM-side
  emission for interpreted packages — pure plumbing; no new
  trampoline machinery needed.
- **Adjacent cleanup, lighter-weight first step**: see the
  "VM extern dispatch: name → function-value registry" entry
  above. A per-VM name → function-value registry with manual
  registration (no descriptor design needed) replaces
  `pkg/vm/vm_extern.bn`'s hand-coded switch via the same
  `dispatchCompiledFuncValue` path Phase 3 already provides.
  Auto-generated descriptors are the more general form of the
  same idea — the registry stays as the manual-registration
  escape hatch for host-only externs that have no Binate-side
  `.bni` package.
- **Design open questions** (need a writeup before implementation):
  - Canonical name for the descriptor — `foo.Package` reads naturally but
    risks conflicting with user names. `foo.PackageImpl` or a reserved-prefix
    name (`__pkg_foo`)? Reserve a keyword?
  - Canonical layout — sort by mangled name? By declaration order in `.bni`?
    Layout must be agreed-upon by the descriptor's emitter and reader.
  - Interaction with import aliases (`import alt "pkg/foo"`) and blank imports
    (`import _ "pkg/foo"`) — see the "Import aliases and blank imports" entry.
  - What does the descriptor look like for the package being compiled itself
    (the "self" descriptor)?
  - How are package-level globals exposed? Functions are the obvious starting
    point; globals are a separate (but related) interop question.
  - Versioning: if Q's `.bni` and Q's compiled descriptor disagree (different
    function set, different layout), how do we detect and report it?
- **Adjacent in-flight work that affects this**:
  - "Function values — MAJOR PROJECT" (above) and
    `plan-function-values.md` — direct prerequisite. Phase 3 of
    that plan delivers the cross-mode trampoline machinery this
    work consumes.
  - "Free-function pointer in managed-allocation header — bug"
    (above, DONE within a single mode) — Free now dispatches through
    `header[1]`. Cross-mode allocate-on-one-side / free-on-the-
    other still requires Phase 3's trampolines to translate
    `header[1]` between the C-pointer and VM-index conventions.
  - "Lift function-name qualification into IR" (above) — would simplify name
    resolution at the interop boundary.
  - "Import aliases and blank imports" (below) — affects how the descriptor
    is named at the import site.
- **Suggested next step**: write a design doc (e.g.
  `explorations/plan-compiler-interp-interop.md`) that nails down the
  descriptor name/layout, walks through one concrete cross-mode call end-to-
  end on each side, and identifies the first concrete code change to make.
  Don't start implementation until the design is reviewed.

### REPL refactor: embeddable component for non-CLI hosts — DESIGN RATIFIED, not started
- **Status (2026-06-02)**: design decided; see
  [`plan-repl-embeddable.md`](plan-repl-embeddable.md) for the full
  staged plan, API, and ratified decisions. The old open "which shape
  (a/b/c)" question is resolved: **push session** (host owns the read,
  engine exposes `Init`/`Step(line,eof) → StepResult`), with the
  interrupt **seam designed-in but unimplemented** in v1 and
  suspend/break staged behind it.
- **Why**: today the REPL is welded to stdin/stdout via
  `bootstrap.{Read,Write}` and a blocking `for{}` loop — can't embed
  into a wasm worker (I/O over message ports; must yield to the event
  loop while awaiting input), nor into test harnesses / IDE hosts.
- **Decided shape** (full rationale in the plan doc): push, not pull
  (wasm can't block on inbound `postMessage`); `ReplIO` is a struct of
  `@func` fields, not an interface; user-program output (category B) is
  redirected by **rebinding the `bootstrap.Write/Read/Exit` externs**
  (no user-code recompile); REPL-framing output (category A) routes
  through the host `ReplIO`; engine extracted to **`pkg/binate/repl`**
  (tier-2); **single live session per process** in v1 (multi-session is
  a tracked blocker — next entry); interrupt layer is **seam-only** in
  v1.
- **Staged v1** (each independently landable, green): (1) session struct
  + re-entrancy; (2) `NewReplSession` constructor (errors as values, no
  `Exit`); (3) `ReplIO` sink + extern rebind; (4) push `Init`/`Step` +
  extract `pkg/binate/repl`; (5) inert interrupt seam.
- **Future, gated**: continuable-suspend (Stage 6; partially gated on
  `plan-bni-heap-frames.md`) and break/unwind (Stage 7; needs new IR-gen
  cleanup landing pads — a frame-discard break LEAKS, so it is
  forbidden without them).
- **Out of scope** (raised, not deferred silently): running the
  type-checker + IR-gen + VM under wasm32 in-worker — necessary for B1
  but separate from this I/O-shape refactor; its own open scope question
  for `plan-wasm-browser.md`.

### REPL: remove process-global session state (multi-session blocker)
- **What**: the REPL engine keeps per-session state in PROCESS-GLOBAL
  package vars instead of threading it through the session. v1 of the
  embeddable refactor (above) lifts the cmd/bni-local ones into
  `@ReplSession` but deliberately keeps **single live session per
  process**, leaving two `pkg/binate/ir` globals in place.
- **The globals**:
  - cmd/bni-local (lifted into `@ReplSession` by Stage 1 of the
    refactor): `replLoader`/`replRoot`/`replBniPaths`/`replProcessedPkgs`
    (`cmd/bni/repl_import.bn:24-41`) and `replInitCounter`
    (`cmd/bni/repl_decl.bn:411`).
  - `pkg/binate/ir` process-globals (NOT lifted in v1, the real
    multi-session blocker): `currentChecker` (`pkg/binate/ir/gen.bn:148`,
    set via `ir.SetChecker`) and the import alias map
    `importAliasNames`/`importAliasPaths` (`gen.bn:107/110`), with
    `Save`/`RestoreAliasMapState` bracketing in `evalReplImport`
    (`repl_import.bn:101/146`).
- **Why it matters**: single re-entrant session is unaffected (the ir
  globals are set once and save/restored inside import turns as today).
  But >1 concurrent embedded session in one process needs those globals
  session-scoped (or save/restored at every `Step` boundary) — a
  separate, larger change that must land BEFORE `pkg/binate/repl` can
  honestly claim multi-session support.
- **Guidance (applies now)**: **do not add any new REPL globals.** New
  per-session state goes through `@ReplSession`. Adding a global "to keep
  a signature stable" (the exact shortcut that created the current ones,
  per `repl_import.bn:18-20`) is what this entry exists to stop.
- **When**: only if multi-session embedding becomes a goal. Not needed
  for wasm B1 (one worker = one session).

### REPL — All five tiers LANDED (2026-05-29)
- **Status**: `bni --repl <file.bn|dir>` ships.  `plan-repl.md` is
  the live source of truth for per-step state — commit tables,
  verified behaviors, deviations from the original plan, and the
  per-tier remaining-follow-ups list.  Briefly:
  - **Tier 1 (load-then-poke)** LANDED.
  - **Tier 2 (top-level decls at the prompt)** LANDED in full,
    including the body-introduced dtor-regen follow-up landed
    2026-05-28 (`EnsureReplBodyHelpers`).  Every top-level decl
    kind supported by the language works at the prompt: `func`
    (incl. methods, redefinition replace + shadow), `const`
    (single, untyped, grouped), `var` (typed,
    untyped-with-literal-init, with init), `type` (aliases,
    named non-struct, structs incl. managed-field).  Bodies that
    introduce a fresh managed-aggregate shape with a destructible
    element (e.g. `@[]@Bag`) have their helper emitted before the
    body lowers.
  - **Tier 3 (forward refs)** LANDED for `func` decls.  Pending
    types / vars / consts (need a structural treatment of
    "unsized" type symbols) are deferred.
  - **Tier 4 (redefinition)** LANDED for both replace and shadow
    paths, free funcs and methods.
  - **Tier 5 (mid-session imports)** LANDED 2026-05-29 via
    `78685ac3`.  `import "pkg/foo"` at the prompt loads pkg/foo
    transitively, type-checks, IR-gens, lowers, and defines the
    package symbol in the session scope.
- **Remaining REPL work**, per plan-repl.md:
  - ~~**Tier 3**: pending types / vars / consts; cycle
    detection.~~  **ALL STAGES LANDED** 2026-05-28 → 2026-05-29
    via 9 commits on main; see
    [`plan-repl-tier3-pending-types.md`](plan-repl-tier3-pending-types.md)
    for the per-stage commit table.  Every top-level decl
    kind parks on forward-referenced dependencies; use-site
    propagation works through sized contexts (struct field,
    var decl, func sig, composite literal, impl recv, method
    receiver); per-caller sized-vs-reference distinction
    preserves recursive types via pointers; cycle detection
    catches genuine cycles through sized fields with a clean
    `pending cycle: A -> B -> A` diagnostic.
  - **Tier 4**: refcount-aware shadow warning (today fires
    unconditionally); forced-shadow escape hatch (syntax TBD per
    `claude-notes.md`).
  - ~~**Tier 5**: loader entry point for "load this one package
    now."~~  LANDED 2026-05-29 — `evalReplImport` in
    `cmd/bni/repl_import.bn` drives it via the session loader's
    existing LoadImports (plus a SaveAliasMapState /
    RestoreAliasMapState bracket around the per-package InitModule
    loop so the main alias map survives the wipes).
  - **Pretty-printer** (`pkg/replprint`) — **deferred** until
    interfaces land.  `bootstrap.println` is a temporary hack;
    building features on top of it would entrench it.
- **Why this matters now**: the REPL is an explicit core goal in
  `claude-notes.md` (see "Forward references & REPL model — DECIDED"
  and the dual-mode rationale in
  `claude-discussion-detailed-notes.md` § 11 / § 23). Its semantics
  are largely *already decided*; what's not decided is the
  toolchain shape. Writing it down now so that adjacent decisions
  (function values, interop descriptors, layout extraction, IR
  cleanup) get checked against REPL feasibility before they land
  — and so that interpreter-only REPL work can start in parallel,
  since most of it overlaps with the audit work the interop story
  already needs.
- **Already-decided semantics** (do NOT relitigate here — see
  `claude-notes.md`):
  - **Retained mode** (definitions) — parsed and stored, validation
    deferred until dependencies are met. Source files are entirely
    retained mode.
  - **Immediate mode** (bare expressions / statements at the prompt)
    — fully checked at entry, can reference validated retained defs.
    Top-level scope in source files is declarative-only; bare exprs
    are REPL-only.
  - **No forward declarations.** Deferred validation handles forward
    references. Errors surface at use, not at definition.
  - **Redefinition**: *compatible* (same sig) → replace; *incompatible*
    (different sig) → shadow with refcounted old-def retention; warn
    on outstanding refs at shadow time. Forced-shadow escape hatch.
  - **Hot-swap of interpreted functions while a compiled binary runs**
    — fall-out of the thunk model.
- **What the VM is/isn't rigid about** (corrects an earlier overstatement
  in this entry):
  - **`BC_CALL` is name-resolved per call, not idx-baked.** Bytecode
    stores a per-VMFunc strings index for the callee's qualified name;
    `LookupFunc` walks `vm.Funcs` by name on every call
    (`pkg/vm/vm_exec.bn:418-421`). That makes replace-redefinition an
    in-place body swap and shadow-redefinition an append-then-shadow,
    both nearly free given `@VMFunc` already being managed.
  - **`vm.Funcs` is already incremental.** `LowerModule` is called
    per-module and appends; multiple modules already coexist in one
    VM with their own preserved string pools (`pkg/vm/lower.bn:42`).
    Globals are also append-only via `materializeGlobals`.
  - **The frontend pipeline is module-shaped, not declaration-shaped.**
    Loader, parser, type checker, and IR-gen are entered per-package;
    there's no "type-check this single decl against an existing scope"
    entry point. Forward refs work today only because the whole module
    is parsed before checking.
  - **Type checker has no concept of pending.** Errors fire immediately
    on undefined names. Deferred validation (the "retained" half of
    the model) is real new infrastructure.  *(Now: Tier 3 added a
    pending queue (`check_pending.bn`) for `func` decls; types / vars
    / consts still fire immediately.)*
  - **No pretty-printer for arbitrary values.** `println` covers char
    slices and primitives only.  *(Still true; deferred — see above.)*
  - **`LookupFunc` is a linear scan.** Fine today; will matter if REPL
    workloads run real volumes of calls. Easy to fix (name → idx hash)
    and worth doing before Tier 1 ships, since the alternative
    (bake-idx-into-bytecode) would close off the redefinition story.
    *(Now: Tier 4 substrate (`9af2d56`) added the funcIndex hash;
    `LookupFunc` is O(1).  Eager CallCache fill keeps shadow
    semantics correct.)*
- **Tiered plan** (each tier shippable on its own; see
  `plan-repl.md` for entry-point names, per-step commit tables,
  and the live follow-up state):
  1. ~~**Load-then-poke.**~~ **LANDED (2026-04-30).** Load a `.bn`
     module the normal way; prompt accepts immediate-mode entries.
     Multi-line input via paren-aware accumulator.  Auto-`println`
     wrap of bare exprs deferred (gated on interfaces).
  2. ~~**Add new top-level decls at the prompt.**~~ **FULLY LANDED
     (2026-04-30 → 2026-05-28).**  All decl kinds: `func` (incl.
     methods), `const`, `var` (typed + untyped-with-literal-init +
     var-initializer evaluation), `type` (aliases, named
     non-struct, structs incl. managed-field).  Body-introduced
     new-managed-aggregate dtor regen also landed (2026-05-28,
     `EnsureReplBodyHelpers`).
  3. ~~**Forward references.**~~ **LANDED for `func` decls
     (2026-05-05).**  Pending-validation queue in the type checker;
     parked decls retry on every newly-resolved name.  Pending
     types / vars / consts remain (see follow-ups above).
  4. ~~**Redefinition.**~~ **LANDED in full (2026-05-01 →
     2026-05-05).**  Compatible-sig: in-place rebind keeps
     CallCache valid.  Incompatible-sig: `LowerOneFuncShadow`
     appends + re-points funcIndex; old callers retain old VMFunc
     via eager-filled CallCache.  Methods follow the same rules,
     keyed on qualified `<pkg>.<TypeName>.<Method>`.  Substrate
     `9af2d56`; shadow `63cc49b`; method redef `026ad22`.
     Refcount-aware shadow warning + forced-shadow escape hatch
     are remaining follow-ups.
  5. ~~**Mid-session imports.**~~  **LANDED** 2026-05-29 via
     `78685ac3`.  evalReplImport in cmd/bni/repl_import.bn
     drives the existing loader's LoadImports for incremental
     transitive loads, brackets the per-package InitModule
     loop with SaveAliasMapState/RestoreAliasMapState so the
     session's main alias map survives, and routes through
     c.RegisterReplImport to make `foo.X` resolvable from
     subsequent prompt entries.
- **What's free / "should-do-now-anyway"**:
  - ~~The audit itself~~ — done; `plan-repl.md` is the live doc.
  - ~~Per-decl entry points exposed opportunistically when the
    relevant code is touched for unrelated reasons.~~  Done as part
    of Tier 1 + Tier 2 (parser ParseExpr / ParseStmtList /
    ParseTopLevelDecl / IsAtTopLevelDecl; types CheckExprInScope /
    CheckStmtListInScope / CheckDeclInScope / CheckMainPersistent;
    ir GenSyntheticFunc / GenDecl; vm LowerOneFunc / CallByVMFunc).
  - ~~Name → idx hash in `LookupFunc`.~~  Solved differently:
    per-VMFunc CallCache (commit `6c8e0c0`) memoizes the lookup
    result per call site, removing the per-dispatch scan; lazy fill
    on first call; explicitly designed for REPL invalidation.
  - A minimal pretty-printer (probably `pkg/replprint`, leaning on
    `pkg/buf.CharBuf`). Useful well beyond REPL.  **Deferred until
    interfaces land** — `bootstrap.println` is a temporary hack
    scheduled for removal; building features on top of it would
    entrench the hack.  See "Pretty-printer" in plan-repl.md and
    the auto-`println` deferral note.
- **Decisions / non-decisions in adjacent work to pressure-test**:
  - **Function values** (`plan-function-values.md`): a function value
    must be a *stable identity for what it refers to*, not for the
    bytes of the underlying body. Re-binding the body of an
    interpreted function does not invalidate function values pointing
    at it. Add this clause to that plan when it moves out of DRAFT.
  - **Compiler/interpreter interop** (above): the package descriptor
    is shaped right for REPL — interpreted-package descriptors are
    mutable, compiled ones are read-only. Sorted-by-mangled-name
    layout interacts with "add a new exported function mid-session"
    (positions move when a new export sorts in); confirm that's the
    intended behavior.
  - **Layout extraction** (archived — see `historical-notes.md`): expose a
    runtime-extensible type universe, not a closed-at-startup one.
  - **IR/backend cleanup**: no closed-world assumptions in the shared
    layer.
- **What this entry is NOT**:
  - A REPL implementation plan — that lives in `plan-repl.md`.
  - A relitigation of REPL semantics — those are decided; if they
    change, update `claude-notes.md` first.
- **Open design questions worth pinning before Tier 1 starts** —
  resolved as part of the Tier 1 work:
  - ~~Top-level prompt grammar.~~  Settled as bare statement list;
    auto-`println` wrap deferred until interfaces (above).  `func`
    decls are dispatched to the decl path via
    `parser.IsAtTopLevelDecl`.
  - ~~Error recovery.~~  Implemented exactly as proposed: parse /
    type / IR-gen / lower / runtime errors in immediate mode print
    and return to prompt; loaded state unaffected.  Verified by
    `e2e/repl.sh` cases.
  - ~~Where pretty-printing lives.~~  Deferred (see above).
  - ~~Sentinel for "no result".~~  Nothing — empty stmt lists are
    skipped by `evalReplStmtList` before reaching IR-gen.
  - ~~Whether REPL is a separate `cmd/bnrepl` or a `--repl` flag on
    `cmd/bni`.~~  Settled as `--repl` flag on `cmd/bni`.
    `scripts/build-bni.sh` (commit `22ea525`) is a convenience
    wrapper for casual use.

### Import aliases and blank imports
- Do we support Go-like `import somethingelse "pkg/foo"` currently? We'll likely need this.
- Do we support `import _ "pkg/foo"`? Should we? (Side-effect-only imports.)
- Both interact with the package object naming question above.

### Package path: env-var support (Stage 7)
- Add `BINATE_PACKAGE_INTERFACE_PATH` / `BINATE_PACKAGE_IMPL_PATH`
  (long names match `LD_LIBRARY_PATH`/`PYTHONPATH` style; aliases TBD)
  as the fallback when CLI flags are absent.
- Gated on adding `bootstrap.Getenv` (a few lines of C + Go-interp
  glue). Deferred because direct shell invocations of bnc/bni today
  can construct CLI arguments — the env-var fallback is convenience
  for users invoking the tools by hand.
- See [`plan-package-search-paths.md`](plan-package-search-paths.md)
  § "Env vars".

### Package path: binary artifacts on IMPL_PATH (Stage 8 / Phase 2)
- Once we have a stable per-package ABI/linker contract: accept
  `.o`/`.a`/`.so` files on `IMPL_PATH` as alternatives to `.bn`
  source. `hasImplFiles(dir)` becomes "has at least one of {.bn, .o,
  .a, .so}". Precedence rule (likely .o/.a/.so wins over .bn, with
  `--prefer-source` to override) is open.
- bnc would also gather binary artifacts from `IMPL_PATH` and feed
  them to the linker automatically (today users supply via
  `--cflag`).
- See [`plan-package-search-paths.md`](plan-package-search-paths.md)
  § "Future: binary impl artifacts".

### Build out e2e testing
- We have unit tests (per package) and conformance tests (language
  semantics). What we don't have is a place for **end-to-end tool
  integration tests** — checks that the CLI/loader/runtime wiring
  works the same way across all four tools that load Binate
  packages: `bootstrap`, `bnc`, `bni`, `bnlint`.
- **What's landed (2026-04-30):**
  - Two scripts: `e2e/split-paths.sh` (the original — `-I`/`-L`
    cross-tool contract; covers Stage 1–6 of the package-search-paths
    plan) and `e2e/repl.sh` (9 cases for `bni --repl`: basic call,
    multi-stmt, error recovery, multi-line for-block, braces in
    string literal, plus four Tier 2 cases — func persists, cross-
    decl call, type rejected with diagnostic, bad body recovery).
  - CI hookup at `.github/workflows/e2e-tests.yml` — matrix-
    discovery via `ls e2e/*.sh`, one runner per script, `fail-fast:
    false`.  Standard checkout layout (binate + bootstrap as
    siblings) matches what the scripts assume.  New e2e scripts are
    picked up automatically.
- **Unique challenges this dir still has to solve over time:**
  - **4 tools, not 1.** A single feature (like `-I`/`-L`) needs to
    be exercised on each tool independently, since each parses CLI
    flags separately and threads them into the loader differently.
  - **Multiple build/run modes for the binate-written tools.** bnc,
    bni, and bnlint can each be exercised through several pipelines:
    bnc via boot-comp / boot-comp-comp / boot-comp-comp-comp /
    boot-comp_native_aa64; bni via boot-comp-int / boot-comp-comp-int;
    bnlint via the same chains as bnc. Note that bni cannot be
    interpreted directly by the bootstrap (cmd/bni imports pkg/vm,
    whose float literals the bootstrap lexer doesn't recognize) —
    bni really has to be built via boot-comp first.
    Full e2e coverage of "feature X works" multiplies tools × build
    modes — easily 10+ runs per feature. We don't necessarily want
    that today; figuring out which slice is worth the cost is part
    of building this out.  Today both shipping scripts pick a
    single mode each (split-paths covers all four tools at their
    "default" build path; repl uses boot-comp bni).
  - **Fixture management.** Conformance tests share a single root;
    e2e tests like split-paths need disjoint fixtures, ad-hoc temp
    dirs, optional checked-in subtrees. No standard pattern yet —
    both current scripts use `mktemp -d` + `trap rm -rf` and inline
    `cat <<EOF` heredocs for fixture files.
- **Why these scripts are useful motivating examples:**
  - **split-paths**: the `-I`/`-L` feature is something `bootstrap`,
    `bnc`, `bni`, and `bnlint` should all support **identically** —
    a deliberate cross-tool contract.  e2e is the only layer where
    that contract can be observed directly.
  - **repl**: the `bni --repl` PoC is a multi-stage user-facing
    flow (load module → drive prompt via stdin → check banner +
    prompts + results byte-for-byte).  No unit test could easily
    exercise the full input-to-output transcript; e2e is the right
    layer for "the REPL works end-to-end".
- See [`plan-package-search-paths.md`](plan-package-search-paths.md)
  for the spec `e2e/split-paths.sh` validates and
  [`plan-repl.md`](plan-repl.md) for what `e2e/repl.sh` covers.

### Annotations and C function interop
- **Option E (`__c_call` intrinsic) has a detailed implementation plan:
  [plan-c-call.md](plan-c-call.md).**
- Consider implementing annotations (decorators/attributes).
- Specific use case: annotating functions as C functions.
  - **Option A**: annotation in `.bni` — callers know the name and calling convention, but mixes interface with implementation.
  - **Option B**: annotation on the definition (with empty body) — `bnc` generates a trampoline. But empty body is weird (missing return values?).
  - **Option C**: annotation on a call site, indicating it's a C function call. Maybe a "magic" C package so no annotation is needed at all.
  - **Option D**: manual trampolines, with a magic C package for declarations.
  - **Option E**: a `__c_call` compiler intrinsic at the call site, no
    declaration needed.  Two forms were considered:
    - **E1 (rejected)**: pass a C prototype string —
      `__c_call("ssize_t write(int, const void*, size_t)", fd, buf, len)`.
      Reads nicely, but forces the compiler to parse C and resolve C
      types, which drags in typedefs, macros, and platform builtins
      (`__size_t` &c.).  Not practical.
    - **E2 (preferred)**: pass the C symbol name, an explicit return
      type, then the argument values already in (or cast to) the
      Binate types that match the C ABI —
      `result = __c_call("write", int, cast(int, fd), cast(*uint8, buf), cast(uint, len))`
      (casts are unnecessary when the variables already have the right
      type).  Supported argument/return types: scalars, struct types,
      and pointers to these (to any depth: `*T`, `**T`, …).  This
      reuses the backends' existing platform-C-ABI lowering (struct
      sret thresholds, register assignment) — no C parsing, no type
      resolution, no new ABI logic.  The symbol name is emitted
      verbatim (no `bn_` mangling); the backend emits the matching
      `extern`/`declare`.
  - **C-types alias package (decided)**: a package (e.g. `pkg/c`)
    pins the Binate↔C scalar correspondence in one place so call sites
    don't open-code it.  `C_int`/`C_uint` = `i32`/`u32` (C `int` is
    32-bit on both ILP32 and LP64, *not* target-word-width like Binate
    `int`); `C_long`/`C_ulong` = target-word (LP64 Unix; matches Binate
    `int`/`uint`); `C_size_t` = `uint` (pointer-width); `C_char` = `i8`
    (signedness is platform-dependent in C — note the caveat, but it's
    promoted on pass so rarely matters).  Plus a sentinel `C_void` for
    the return-type slot of functions that return nothing.  So the
    example's `fd` is really `C_int` (= `i32`), not `int`.
  - **Scope decisions (v1)**:
    - **Compiled-mode-only to start.** The compiler emits a direct
      call; the VM would need FFI-style dispatch (resolve the symbol
      via the extern registry + marshal by the supplied types) — punt
      that.  `__c_call` outside compiled mode is an error for now.
    - **Include variadics from the start.** The whole point of
      `__c_call` is to retire `pkg/bootstrap`'s hand-written C
      wrappers and the special shim machinery — and several of those
      OS interfaces are variadic in C (`open(const char*, int, ...)`
      where `mode` is a vararg; `fcntl`, eventually the `printf`
      family).  Punting variadics would leave bootstrap unable to go
      away, defeating the purpose.  So v1 supports them.
      - **Boundary marker (required).** The call site must declare
        where fixed args end and variadic args begin — it can't be
        inferred from the values (`open(path, flags, mode)` is
        indistinguishable from a 3-fixed-arg call).  Proposed: a
        `C_varargs` sentinel (or a recognized `...` token) in the
        argument list:
        `__c_call("open", C_int, path, flags, C_varargs, mode)`.
        Everything after the marker is an anonymous/variadic arg.
      - **Backend work is lopsided.** LLVM path: nearly free — emit
        `declare i32 @open(i8*, i32, ...)` + a varargs call with the
        right fixed-arg count, and LLVM does the platform-correct
        lowering (x86-64 `AL` = vararg float count, darwin-arm64
        stack-passing, 64-bit-vararg alignment) for us.  Native
        backends (`pkg/native/{arm64,amd64}`): real work — they emit
        machine code directly and must implement the vararg
        convention per target (darwin-arm64 stacks all varargs;
        x86-64 SysV sets `AL`; AArch64-Linux/arm32 mostly match the
        fixed convention but 64-bit varargs need 8-byte alignment).
        This extends the existing `CallConv`/register-assignment
        logic; needs per-target tests.
  - **Open considerations for E2 (still to resolve)**:
    - Confirm the full `pkg/c` scalar table against each target
      (`C_long` on a 32-bit target, `C_char` signedness, the float
      types if/when floats land).
    - Final spelling of the variadic boundary marker (`C_varargs`
      sentinel vs a `...` token vs an explicit fixed-arg count).
    - VM/dual-mode FFI dispatch (deferred above) when interpreted-mode
      `__c_call` is eventually wanted.
  - **Companion idea — link-requirement annotation (sketch)**: Option E
    makes a C symbol *callable*; a complementary annotation would make
    it *resolve at link time* by declaring, at the source level, that
    using a package requires linking some C library — so the driver
    adds the flag automatically instead of every consumer passing
    `--cflag -lm` / `--link-after-objs` by hand.  Prior art:
    Rust `#[link(name = "m", kind = "static")]`, Go cgo
    `// #cgo LDFLAGS: -lm`, MSVC `#pragma comment(lib, "foo")`.
    Natural shape: `#[link("m")]` (optionally a `static`/`dynamic`/
    `framework` kind), most naturally on the `.bni` since the link
    requirement is part of the package's contract.  This is also the
    first real payoff of the general annotations feature this item is
    about — both Option E and this want it.
    - **Open wrinkles**:
      - **Transitivity** — the requirement must propagate through the
        import graph (aggregate + dedup all declared libs for any
        binary that transitively imports the package).  Hooks into the
        loader's `ldr.Order` walk + the driver's `clangArgs` assembly.
      - **Link ordering** — static archives only supply symbols
        referenced by *earlier* inputs, so aggregated `-l` entries
        need correct placement vs. the `.o` files and runtime (the
        driver already does this for `linkAfterObjs`).
      - **Search paths** — keep the annotation name-only (`-l`); leave
        `-L<dir>` to driver flags.
      - **Platform-conditionality** — a `libm` dep is meaningless on
        bare-metal arm32 and `framework` kind is macOS-only, so the
        annotation likely needs to be target-qualifiable.  Ties into
        the C-free principle: this exists only to interface with
        existing C systems and should evaporate on freestanding
        targets.
      - **Static-spec portability** — even with `kind = static`,
        expressing it portably is messy (GNU ld `-l:libfoo.a` /
        `-Wl,-Bstatic`; macOS `ld` has neither), so it may need
        per-platform lowering in the driver or a full-path escape
        hatch.
