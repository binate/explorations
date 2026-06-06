# Binate TODO

Tracks open work items. Completed items live in [claude-todo-done.md](claude-todo-done.md).

---

## CRITICAL

### `~` (bitwise complement) IR-gen hardcodes the result type to `int` — invalid IR for sub-word, wrong-signed shift on uint64 — CONFIRMED wrong-code, default LLVM mode (`bitnot-result-type`)
- **Symptom (two facets, one root)**:
  - **A (invalid IR)**: `~x` for any sub-word int (`uint/int 8/16/32`) emits
    `xor i64 %x, -1` with a hardcoded i64 — clang rejects it
    (`'%x' defined with type 'i8' but expected 'i64'`). `~` simply does not
    compile for sub-word ints on the LLVM backend.
  - **B (wrong value)**: `(~v) >> k` consumed DIRECTLY (no intervening store)
    on `uint64` does an ARITHMETIC shift, not logical: `(~0) >> 32` is
    `2^64-1`, not the spec `2^32-1`. Storing `~v` into a `uint64` var first
    masks it (the store re-types to unsigned), and `(a+b) >> k` for unsigned is
    fine — so it is specific to `~`-results.
- **Root cause (CONFIRMED)**: `pkg/binate/ir/gen_expr.bn:247` lowers `~` as
  `b.EmitUnary(OP_BITNOT, arg, types.TypInt())` — the result type is hardcoded
  to `int` (signed, target-width i64) instead of the OPERAND's type. So the
  BITNOT instr is mis-typed: i64 width (→ facet A, mismatched `xor` width for a
  sub-word arg) and signed (→ facet B, a directly-consumed `>>` lowers to
  `ashr` not `lshr` per `emit_ops.bn:48-52`, which keys on `instr.Typ.Signed`).
  This is the SHARED IR layer, so it likely affects the VM/native backends too
  (facet B at least; the full `all` sweep is pending this decision).
- **Test**: `conformance/matrix/scalar-diff/bitwise/not/*` — 7 cells fail on
  `builder-comp` (the sub-word ones COMPILE_ERROR; `64/unsigned` value-diverges;
  `64/signed` passes — i64 + signed happen to match the hardcoded type).
- **Discovery**: 2026-06-06, differential-harness v2 (bitwise cells).
- **Fix**: type the `OP_BITNOT` result as the operand's type, mirroring the
  adjacent `OP_NEG` path's `negTyp` derivation (`gen_expr.bn:223-241`) — for
  `~`, the result type is always exactly the operand type (no widening). A
  one-site fix resolving both facets.

### Whole-array (aggregate) `=` assignment is silently dropped — CONFIRMED wrong-code, default modes
- **Symptom**: `a = [4]int{10,20,30,40}` (a whole-array assignment via `=`, RHS a
  composite literal) does NOT update `a` — it stays at its prior value. The store
  is silently a no-op; no error, no diagnostic.
- **Discovery**: 2026-06-06, porting `math.Pow10` (which wants package-level
  `var pow10tab [32]float64 = {...}` lookup tables). Minimal repro in a unit test:
  `var a [4]int = [4]int{0,0,0,0}; a = [4]int{10,20,30,40}; a[0]` reads `0`.
- **Scope confirmed by probing (builder-comp / LLVM gen1)**:
  - LOCAL array *decl-init* (`var a [N]T = [N]T{...}`): WORKS (int + float).
  - Whole-array `=` *assignment* (`a = [N]T{...}`): BROKEN (no-op) — the LHS keeps
    its old value. This is the underlying defect.
  - GLOBAL array initializer (`var arr [N]T = {...}` at package scope): BROKEN
    (reads as all-zero) — because the synthetic per-package `__init` (gen_init.bn)
    lowers each `var x = expr` into the assignment `x = expr`, and whole-array
    assignment is the dropped op. (GLOBAL *scalar* int init via `__init` WORKS,
    confirming `__init` itself runs in the unit-test harness.)
- **Likely root cause (needs confirming)**: IR-gen for `STMT_ASSIGN` with an
  aggregate (array, and probably struct) LHS/RHS doesn't emit an element-wise copy
  / memcpy — only scalar assignments store. The decl-init path (genLocalVarDecl)
  emits the element stores, which is why decl-init works but `=` doesn't.
- **Severity**: CRITICAL — silent data loss on a routine operation (`arr = other`,
  `arr = {...}`, and therefore *all* global array/struct initializers). Any program
  relying on a package-level table reads zeros with no warning.
- **Impact / blocks**: `math.Pow10` (table-based) is blocked; any global aggregate
  table or `arr = arr2` copy is unsafe until fixed.
- **Test (TODO when fixing)**: conformance cell for whole-array `=` assignment and
  global array-initializer readback (LLVM/VM/native/gen2), xfailed until the fix.

### Global float `var` emits invalid LLVM (`global double 0`) — CONFIRMED compile failure, LLVM backend
- **Symptom**: any package-level `var x float64` (with or without an initializer)
  makes the LLVM backend emit `@<mangled> = global double 0`, which clang rejects:
  `error: integer constant must have integer type` — the whole package fails to
  compile. (`var x float64 = 7.5` fails identically; the initializer is irrelevant
  because the static zero is what's malformed.)
- **Root cause**: `pkg/binate/codegen/emit.bn` global-var emission (~line 156-170)
  picks the static zero by type kind: `null` for pointers, `zeroinitializer` for
  slice/struct/array, and a bare ` 0` for *everything else* — but ` 0` is only
  valid for integer LLVM types. For `double`/`float` it must be ` 0.0` (or
  `0.000000e+00`). The runtime value (for `= expr`) comes from `__init`, which
  works for scalars — so emitting the correct float zero fully fixes scalar float
  globals.
- **Severity**: MAJOR — hard compile error (not silent), blocks any global float
  var. Discovered 2026-06-06 alongside the array-assignment bug, porting `Pow10`.
- **Proposed fix**: in the global-var zero-emission, branch on float type kinds
  (TYP_FLOAT64/TYP_FLOAT32) to emit ` 0.0`; keep ` 0` for integers. One-line-ish.
- **Test (TODO when fixing)**: codegen unit test asserting a `double`/`float`
  global emits a float zero, plus a conformance cell reading back a global float.

### Integer shift by a count >= bit width is hardware-masked (mod width), NOT the spec's defined 0 / sign-extend — FIXED 2026-06-06 (binate `32fde83d`)
- **Fix**: a branchless overshift guard in IR-gen (`gen_binary.bn`,
  `emitGuardedShift`), so a non-constant (or out-of-range constant) shift count
  yields 0 (logical) / sign-fill (arithmetic `>>`) per the spec, on every
  backend with no per-backend logic. An in-range constant count stays a plain
  shift (the common case is unchanged). `math.RoundToEven`'s temporary IsInf/
  IsNaN workaround was removed. Pinned by `conformance/631_shift_overshift`
  (LLVM/VM/native-aa64/gen2) + IR-gen unit tests; full builder-comp 854/0.
- **Symptom (was)**: a shift whose count is >= the operand's bit width returns a
  hardware-masked result instead of the documented value. Confirmed (LLVM, both
  const-folded and runtime counts): `full >> 64 == full` and `1 << 64 == 1`
  (both should be `0`); `full >> 70 == full >> 6` (count masked to `70 mod 64`).
  The native backends (aarch64 `LSL`/`LSR`, x64 `SHL`/`SHR` mask the count to 5/6
  bits) and the VM (host shift) almost certainly do the same — needs confirming
  per backend.
- **Spec violated**: `claude-notes.md` Operators — "Shift by >= bit width:
  defined behavior (zero for `<<` and logical `>>`, sign-extended for arithmetic
  `>>`)". Matches Go (which guarantees shift-away-to-0). The implementation does
  C/hardware masking instead.
- **Impact**: any shift by a *runtime* count that can reach/exceed the width is
  silently wrong. Breaks ported code that assumes Go's shift semantics — e.g.
  `math.RoundToEven` (its `e >= bias` branch shifts by huge counts for ±Inf/NaN
  and relies on `>> n == 0`; worked around with an IsInf/IsNaN guard, removable
  once this is fixed), and likely upcoming fdlibm ports. Discovered 2026-06-06
  porting `math.RoundToEven` (the ±Inf/NaN case produced a non-NaN).
- **Root cause**: codegen emits the raw hardware shift. LLVM `shl`/`lshr`/`ashr`
  by >= width is poison, lowered to a masking hardware shift; the native shifts
  mask the count register directly.
- **Test**: `conformance/matrix/scalar/{shl,shr}-overshift/<width>/<sign>` (16
  cells, binate `6fdb56eb`) — count == width, runtime `var` count (exercises the
  backend shift, not const-fold). CONFIRMED wrong on **every** backend (LLVM, VM,
  both natives); xfailed all modes — **un-xfail when the fix lands**. (Closes the
  scalar matrix's value-axis gap: shifts were only tested as in-range consumers.)
- **Fix (in progress, honor the spec)**: make codegen guard each variable-count
  shift so a count >= width yields 0 (logical `<<` / `>>`) or sign-fill
  (arithmetic `>>`), on every backend + the VM. The alternative — changing the
  spec to hardware-masked / UB-on-overshift (cheaper, matches C/hardware) — was
  considered and rejected in favour of keeping the documented Go-style guarantee.

### Managed struct destructor: `func_value_dtor on nil` for `@func` fields in a complex session struct — CONFIRMED crash, both backends
- **Symptom**: a managed struct holding managed function-value (`@func`) fields
  alongside other managed members crashes on destruction — compiled SIGTRAPs
  (rc 133, no output), interpreted aborts with `vm: func_value_dtor on nil fv
  address`. The per-type destructor calls `func_value_dtor` on a nil/garbage func
  value (it walks to a wrong field/offset).
- **Confirmed against binate main**: minbasic's `basicSession`
  (`examples/minbasic/pkg/basic/session.bn`):
  `{prog @progStore; env @env; col int; io ReplIO{WriteOut, WriteErr
  @func(*[]readonly char) int}; out @io.ConsoleOut; turns int; poll @func()
  PollResult}`. Spelling the sink/poll fields `@func` (the `pkg/binate/repl`
  contract) crashes `cmd/basic` at session teardown in BOTH modes against a
  `main`-built bundle. Spelling them raw `*func` (no managed dtor) avoids it —
  minbasic ships `*func` as a marked temporary (examples `2c553d8`,
  `examples/TODO.md`). Re-confirmed 2026-06-06 against `975db032`: the `@func`
  flip → rc 133 compiled / `vm: func_value_dtor on nil` interpreted; reverting
  to `*func` is clean.
- **Could NOT minimize to a small standalone repro** — these all WORK (no crash,
  both modes, main bundle): a struct of two `@func` fields; `@func` + a managed
  pointer (either order); a nested `IO{2 @func}` + a sibling `@func` (± a managed
  pointer); a faithful mirror of `basicSession`'s field types+order with shallow
  `@Store{int}`/`@Iface`; and the same with `@Store{@[]char}` (managed pointers
  to structs that themselves hold managed fields). So the trigger is more
  specific to the real session (the actual `progStore`/`env`/`ConsoleOut` types,
  the by-value `ReplIO` copies through `NewSession`, or the funcs being invoked
  through the struct). Needs a bisect against the real type to pin a minimal case
  — toggling minbasic's sink/poll between `@func` and `*func` is the repro switch.
- **Discovery**: 2026-06-06, building minbasic's M3 embeddable REPL; the
  duplicated `@func` `ReplIO` in the session struct crashed `cmd/basic` at
  startup.
- **Note**: `pkg/binate/repl.ReplIO` is itself a 2-`@func` struct and the binate
  REPL works (a standalone 2-`@func` struct destructs fine), so this is
  layout/composition-specific, not "any `@func` struct".
- **Fix**: correct the per-type destructor's managed-field walk so a `@func`
  (2-word {vtable,data}) member is dtor'd at the right offset within a struct that
  mixes it with other (possibly deep) managed members; today it dtors a
  wrong/nil func value.

### Native backend leaks native stack per loop iteration for a default-initialized managed local — CONFIRMED stack-overflow crash, native-only (compiled); VM unaffected
- **Symptom**: a compiled program that declares a *default-initialized* managed
  local (e.g. `var m @[]char` with no initializer) inside a loop body SIGSEGVs
  (exit 139) once the loop runs enough iterations — the local need not even be
  used. The bytecode VM (`bni`) runs the identical program fine.
- **Minimal repro** (crashes compiled after ~130k iterations on an 8 MiB stack;
  completes under the VM):

      package "main"
      func main() {
          for i := 0; i < 3000000; i++ {
              var m @[]char
          }
      }

- **Proven native-stack growth, not a heap leak**: peak RSS is flat
  (~10 MiB ≈ stack limit + heap) regardless of iteration count, and the crash
  threshold scales linearly with `ulimit -s` — 8 MiB → ~130k iters, 64 MiB →
  ~1 M iters; the *same* N=800 000 program crashes at 8 MiB but completes at
  64 MiB. So each iteration leaks a fixed ~64 bytes of native stack that is never
  reclaimed.
- **Trigger axis = default-init vs expression-init** (matrix, 3 M iters,
  native-aa64): `var m @[]char` (no initializer) → CRASH, whether the local is
  unused / passed by value / has its address taken; `var m @[]char =
  make_slice(char, 0)` → OK in all the same shapes; a default-init *raw* local
  (`var x int; &x`) → OK. So the leak is specific to a managed local that is
  *implicitly zero-initialized*; an explicit managed initializer takes a
  different (correct) code path.
- **Root-cause hypothesis** (for the backend owner): the implicit zero-init /
  cleanup-slot setup for a default-valued managed aggregate local is emitted at
  the declaration site *inside the loop body* via a stack allocation that is not
  hoisted to the function entry block (or a cleanup registration that pushes a
  per-iteration frame), so it accumulates across iterations. Expression-init
  managed locals reuse a hoisted slot and don't leak. The VM is unaffected (it
  doesn't use the native C stack for these).
- **Impact**: any compiled loop with a default-init managed local that iterates
  more than ~130k times crashes — a common idiom. Concretely it crashes the
  compiled minbasic interpreter (`examples`): `runProgramInto`'s step loop has
  `var errMsg @[]char` (default-init), so any BASIC program exceeding ~130k
  statement-steps SIGSEGVs the compiled `minbasic` binary while the interpreted
  one completes. (minbasic could side-step it by hoisting `errMsg` out of the
  loop / giving it an initializer, but that masks the backend defect — left as-is
  pending this fix.)
- **Discovery**: 2026-06-06, characterizing a "FOR/NEXT crash" flagged during
  minbasic M5 conformance; reduced from BASIC FOR/GOTO loops to the 6-line repro
  above.
- **Test**: needs a conformance test — a default-init managed local in a
  ~200k-iteration loop, expected to complete; xfail the compiled/native modes
  (VM passes). NOT yet added (would land in the binate repo).
- **Fix**: hoist the implicit-zero-init/cleanup stack slot for a default-valued
  managed local to the function entry block (allocate once), so it is reused
  across loop iterations exactly as expression-initialized managed locals already
  are.

### Plan-1 adversarial review (2026-06-06) — regressions + completeness gaps from the const/slice fixes

The Plan-1 fixes (binate 1.1-1.6, landed 2026-06-05) were adversarially
reviewed. Real defects found, several wrong-code on main. Listed worst-first.
Repros marked (verified) were reproduced directly; (reviewer) were proven by a
review subagent via --emit-llvm / gen1. Each needs an xfail test added (Bug
Discovery Protocol) — most don't have one yet.

#### C1 — inc/dec on a local const mutates it — ✅ FIXED+LANDED (binate `2e8fbb33`, 2026-06-06)
- **Symptom**: `func main(){ const C int = 5; C++; println(C) }` prints **6** (verified). Pre-fix C++ was a silent no-op (const not in ctx.Vars → lookupVar nil); local-const materialization (binate 273d7e4a) put the slot in ctx.Vars, and the checker's STMT_INC_DEC arm (check_stmt.bn ~39-45) only checks IsInteger(), never const-ness, so genIncDec now load/add/store-s into the const slot.
- **Root cause**: checker STMT_INC_DEC doesn't reject a SYM_CONST target (assign / compound-assign / &C ARE rejected; only ++/-- slip through).
- **Fix**: reject ++/-- on a const in the checker. **Test**: conformance .error or a checker unit test (expectError), currently xfail/known-gap.

#### C2 — untyped non-int local const mistyped as int — ✅ FIXED+LANDED (binate `912718e6`, 2026-06-06)
- **Symptom**: `const C = 0.5; var y float32 = C` → high lane **24191** (garbage; verified); `const C = 0.5; var x float64 = C + 0.5` → invalid `add i64 …, double`, clang rejects. genDecl's no-TypeRef inference defaults typ=TypInt() (only special-cases EXPR_STRING_LIT), so an untyped float/bool/char local const gets an i64 slot and a `sitofp`/int op. The checker accepts it (untyped const stays assignable to float32), so it miscompiles silently. The var-init sibling `var C = 0.5` is checker-rejected for the float32 assign, so this divergence is specific to routing DECL_CONST through the int-defaulting path.
- **Root cause**: gen_stmt.bn genDecl untyped-inference covers only string literals; float/bool/char untyped local consts fall to TypInt default.
- **Fix**: infer the type from the initializer literal kind (float→float64, bool, char) for an untyped local const (mirror checker default-type), or reject untyped non-int local const. **Test**: conformance xfail (float32/float64 untyped local const).

#### C3 — local const as array dimension → IR-gen wrong size — ✅ FIXED+LANDED (binate `c97d7acc`, 2026-06-06)
- **Symptom**: `const N int = 3; var a [N]int; println(len(a))` → **30** (verified); package-scope const gives 3. Checker sees the local const via c.Scope.Lookup (correct length 3), but IR-gen resolveTypeExpr→evalConstExpr→lookupConst (gen.bn ~386) walks only moduleConsts (module scope) and falls back to parseIntLit("N")=garbage. Checker/IR-gen layout disagreement.
- **Root cause**: IR-gen has no function-local const table; lookupConst is module-only. (1.3a fixed array-dim for PACKAGE consts; locals were not covered.)
- **Fix**: give IR-gen access to local const values for resolveTypeExpr (a function-scoped const table), or restrict array dims to package consts at the checker. **Test**: conformance xfail (local const array dim).

#### C4 — &s[i] on a readonly-wrapped slice mis-strides — ✅ FIXED+LANDED (binate `f4769aac`, 2026-06-06)
- **Symptom**: `var s readonly @[]uint8 = "AB"; var p *uint8 = &s[1]; println(cast(int,*p))` → **0** (verified; expect 66). Dropping the TYP_STRUCT guard (binate 937ae78e) exposed it: for `readonly @[]uint8`, arrTyp.Kind==TYP_READONLY; isSliceType peels readonly (true) but arrTyp.Elem is then the INNER managed-slice, not uint8, so EmitSliceElemPtr GEPs with a ~32-byte stride. Pre-fix this crashed (guard failed → wild-pointer fall-through); now silently wrong.
- **Root cause**: genIndexPtr (gen_access.bn) uses arrTyp.Elem / collTyp.Elem without peeling TYP_READONLY.
- **Fix**: peel readonly (resolve to the underlying slice type) before reading .Elem in both slice arms. **Test**: conformance xfail (&readonly-slice[i]).

#### C5 — cross-package float const-EXPRESSION reads int 0 — ✅ FIXED+LANDED (binate `3dfc4b4a`, 2026-06-06)
- **Symptom**: a `.bni`-exported `const C float64 = 1.5 + 2.5`, read package-qualified, lowers to `add i64 0, 0` (reviewer). The CONST_EXPR family (binate 9ef5db58) was wired into gen_expr.bn's EXPR_IDENT read but NOT into gen_selector.bn's qualified read (no CONST_EXPR arm → falls to EmitConstInt(Val=0)), and the importer (gen_import.bn single + registerImportConstGroup) never registers a float const-expr at all.
- **Root cause**: const-folding fixes scoped to in-package producers/readers; the cross-package read (gen_selector) + import producers were not updated.
- **Fix**: add a CONST_EXPR arm to gen_selector read + route import producers through the shared classifiers (see M1/M4 — a unifying shared const-classifier is the real fix). **Test**: cross-pkg conformance xfail.

#### M1 — cross-package bool/float-comparison + bool-logic consts → silent int 0 — ✅ FIXED+LANDED (binate `3dfc4b4a`, 2026-06-06)
- **Symptom**: `.bni`-exported `const CMP bool = 1 < 2` / `(1<2)&&(3>2)` / `1.5 < 2.5` read cross-package lower to `add i64 0,0` → 0 (reviewer). gen_import single-const handles only EXPR_BOOL_LIT + float-literal; registerImportConstGroup calls only classifyConstLit; neither calls classifyConstBoolExpr/classifyConstFloatExpr.
- **Fix**: route both import producers (and gen_repl GenConstMember) through the same classifier chain genConst/genConstGroup use. **Test**: cross-pkg conformance xfail (bool-cmp, bool-logic, float-cmp).

#### M2 — composite-LITERAL element float32 store → memory corruption — ✅ FIXED+LANDED (binate `975db032`, 2026-06-06)
- **Symptom**: `var a [2]float32 = [2]float32{0.5, 0.5}` emits `store double %v, float* %slot` — an 8-byte store through a 4-byte slot (reviewer). The 1.1 coerceScalarWidth was wired into call-arg/field/return but NOT the three composite-literal element-store loops (genArrayLit, genManagedSliceLit, genRawSliceLit). Worse than the contained-field case (clobbers adjacent memory).
- **Fix**: call coerceScalarWidth before the element store in all three composite-literal loops. **Test**: conformance xfail (array/mslice/rawslice float32 literal).

#### M3 — const array dim in a struct field → spurious type-check rejection — ✅ FIXED+LANDED (binate `a56943c8`, 2026-06-06)
- **Symptom**: `const N int = 3; type S struct { arr [N]int }; … s.arr passed to a [3]int param` is REJECTED `cannot assign [..] to [..]` (reviewer). Struct types resolve once in pass 1 (collectTypeDecl), where no const has HasConstVal yet, so evalConstInt's leniency returns 0 and [0]int sticks on Field.Type; the var path re-resolves in pass 2, struct fields don't. Codegen is fine (resolves independently) → false-positive rejection, not a miscompile.
- **Fix**: collectDecls now folds the const's integer value (defineConstVal) at pass-1 forward-registration when evalConstIntValue can fold it — so a struct field's array dim resolving in the same pass sees the value. evalConstIntValue doesn't checkExpr, so non-literal / forward initializers fold to nothing and the name still resolves value-less (unchanged forward-ref behavior). **Test**: `TestConstArrayDimInStructField` (checker unit, expectNoErrors).

#### M4 — float const referencing only float consts → int 0 — ✅ FIXED+LANDED (binate `c716ea0c`, 2026-06-06)
- **Symptom**: `const C float64 = A + B` (A,B float consts, no float literal) → isFloatExpr false (literal-only) → integer evalConstExpr → lookupConst returns Val=0 for CONST_FLT entries → C registers CONST_INT 0 (reviewer). Checker accepts.
- **Fix**: isFloatExpr should also recognize a const-ident operand whose const is float; or the shared classifier should consult the operand const kinds. **Test**: conformance xfail.

#### M5 — iota inside a float CONST_EXPR re-lowers to 0 — ✅ FIXED+LANDED (binate `c716ea0c`, 2026-06-06)
- **Symptom**: `const ( C float64 = 1.5*cast(float64,iota); D; E )` → 0.0,0.0,0.0 (reviewer). CONST_EXPR stashes only the AST, not the iotaVal; the read-site genExpr has no iota in scope → `iota` ident → EmitConstInt(0). Affects bare iota-repeat float members too.
- **Fix**: capture iotaVal with the CONST_EXPR and bind it at the read site, or fold float-with-iota at gen time. **Test**: conformance xfail.

#### M6 — forward-ref non-literal untyped const → silent false-accept (MAJOR, from untyped-single-const fix)
- **Symptom**: `var x int = A; const A = B; const B = 1.5` is accepted with NO error (reviewer-verified probe); reversed order correctly errors. The pass-1 placeholder for a NON-literal initializer is a value-less untyped-int (untypedConstPlaceholder fall-through), which AssignableTo treats as assignable to any int with the fit-check skipped — so a forward use sees int, not the const's real (float/out-of-range) type. Trades a loud `undefined` for a silent missed type error.
- **Root cause**: untypedConstPlaceholder returns value-less untyped-int for non-literal initializers; AssignableTo skips the fit-check for value-less untyped-int.
- **Coarse fix REJECTED (2026-06-06)**: "don't forward-register non-literal untyped consts" (gate registration on `isSimpleLiteral`) was tried and reverted — it regresses a strictly broader, *legal* case: a forward ref to a non-literal **int** const (`var x int = A; const A = 1 + 2`) then errors `undefined A`, because pass-2 use-sites are checked in strict source order and see only the pass-1 placeholder. The gate can't tell a legal forward int const from an illegal float one — both are non-literals whose real type is unknown in pass 1. So the coarse fix trades a narrow false-accept for a broader false-reject. Guarded by `TestForwardRefNonLiteralIntConstAccepted` (must stay expectNoErrors); the gap itself is documented by `TestForwardRefNonLiteralUntypedConstFalseAcceptKnownGap` (expectNoErrors, flip to expectError when fixed).
- **Proper fix (needs decision)**: resolve each const's *real* type before any use is type-checked — i.e. lazy / dependency-ordered const resolution (resolve-on-reference with a resolving-stack for cycle detection, like Go's const DAG), or a pass-1 fixpoint that resolves const types in dependency order. This is a real checker change (touches checkExpr's ident path or adds a pre-pass), not a one-liner; surface to user before implementing. Also (separate, minor): int-literal placeholder uses parseIntLiteral (host-int, no overflow guard) vs pass-2 bignum — a >host-int literal gets a wrapped placeholder magnitude. **Test**: `TestForwardRefNonLiteralUntypedConstFalseAcceptKnownGap` (checker unit, known-gap).

#### M7 — &f()[i] / &a[i][j] still wild-pointer SIGSEGV (MAJOR, pre-existing, &slice[i] incompleteness)
- **Symptom**: `&get()[1]` (call base) and `&a[i][j]` (nested-index base) compile then SIGSEGV (reviewer). genIndexPtr only handles e.X.Kind IDENT/SELECTOR; other bases return nil → genUnary falls through to the r-value wild-pointer path (gen_expr.bn:177). Pre-existing (not a regression), untracked.
- **Fix**: handle call/nested-index bases in genIndexPtr (materialize the base slice to a temp, GEP), or reject &<non-addressable-base>[i]. **Test**: conformance xfail.

#### Minor follow-ups (adversarial review 2026-06-06)
- bool-logic (`&&`/`||`/`!`) const-folding has no test (only `<`/`>` covered).
- REPL parked-member + iota-repeat: a bare member after a PARKED explicit member gets plain iota (prevExpr not updated across the parked `continue`); GenConstMember has no iota-repeat. REPL-only.
- coerceScalarWidth / ensureWidth emit an OP_CAST to a TYP_NAMED float target that codegen's float-float cast doesn't match (named-float type mis-lowering). Pre-existing, shared with var-init.
- negative (`const N int = -2`) and div-by-zero (`[A/B]int`, B=0) array dims are silently length-0 / `alloca [-2 x …]` with no clean diagnostic.
- bare iota-repeat member type uses the GROUP type, not the preceding explicit member's type (mixed-type groups).
- stale comments: aarch64_fp.bn says D-regs at "offset 100" (actually 32); iota-repeat.bn says "Currently … PLAIN iota" (now fixed).

### ~~Native backends drop `binate_runtime.c` — every native program fails to link~~ — FIXED + LANDED 2026-06-05 (binate `1285683e`)
- **Was**: every `builder-comp_native_aa64-comp_native_aa64` cell failed at link
  with `Undefined symbols for architecture arm64: "_bn_pkg__bootstrap__Write"`.
  Self-hosted `BNC_NATIVE` computed an empty `runtimePath` (findRuntime ends in
  `return suffixes[i]`) so the `if len(runtimePath) > 0` gate dropped
  `binate_runtime.c` from the link.
- **Actual root cause** — a **shared native-backend** wrong-code bug, NOT what
  this entry first guessed: both native backends (aa64 AND x64 — not aa64-only)
  lowered an aggregate `OP_LOAD` as a bare *pointer into the source object*
  instead of materializing a copy. `return container[i]` then copied the
  element header into the sret buffer only AFTER the function's cleanup RefDec'd
  (and freed) the local container's backing → read freed/zeroed memory, so the
  return came back empty/garbage. LLVM and the VM were always correct (LLVM loads
  the aggregate into an SSA value at the load site).
- **`ee671b6c` (sub-word narrowing) was REFUTED by bisect** — rebuilding gen1
  with `emitSubWordNarrow` neutralized left the repro broken. It was never the
  cause; the bug is not char/sub-word arithmetic and predates `ee671b6c`. The
  earlier "aa64-only / findRuntime char handling / prime-suspect ee671b6c"
  framing in this entry was all wrong (recorded here so the mistake isn't
  repeated).
- **Fix**: `PlanFrame` now reserves an own data region for an aggregate
  `OP_LOAD` (as `OP_MAKE_SLICE` / aggregate calls already do); `emitLoad` copies
  the loaded bytes into it and points the result there, so the load owns its
  bytes and can't alias a freed source. Fixed in both the aa64 and x64
  `emitLoad`. aa64-native lane: 0 passed (all COMPILE_ERROR) → 811 passed, 0
  failed.
- **Tests**: `conformance/regressions/return-aggregate-element-of-local`
  (managed-slice element + struct array element returned directly — caught in
  the existing gen1-native lane, which is why a bespoke BNC_NATIVE smoke wasn't
  needed) + `TestPlanFrameReservesAggregateLoadDataRegion` (native/common).

### bnc front-end / IR-gen memory blows up (>8.5 GB, OOM) compiling a ~1370-line program — super-linear, NOT raw size — PRIMARY FIX LANDED on main
- **Status (2026-06-05)**: fix **(1)** below LANDED on main (binate
  `7804c287`) — `registerPendingStructDtor`/
  `registerPendingMsDtor` now dedup via a precomputed-name list (`hasName`) with
  the incoming name built once, instead of re-spelling every existing entry per
  call. **Validated**: minbasic `bnc cmd/run` now compiles to a working 270 KB
  binary in **~1 s at 27 MB peak RSS** (was >8.5 GB / OOM-killed after ~15 min);
  `--emit-llvm` 27 MB / 2 s (was 7.5 GB / 54 s / 0 IR lines). `refcount` matrix
  105/0 and the `pkg/binate/ir` unit tests stay green. Fixes (2)-(4) below remain
  as follow-ups — they remove the *other* super-linear factors (unmemoized Type
  queries, O(n) `slices.Append`, `ctx.Vars` rescan) for even larger programs, but
  (1) alone brought minbasic back to tractable.
- **Symptom**: compiling the minbasic example (examples repo, `minbasic/cmd/run`
  — ~1370 lines of `pkg/basic` plus transitive `strconv`/`buf`/`slices`/`errors`)
  drives `bnc` to **>8.5 GB RSS** and it is OOM-killed (SIGKILL) after ~15 min on
  a 24 GB machine. `bni` similarly peaks ~8 GB. M0 (the banner skeleton) compiled
  in seconds; the jump is the M1 interpreter code.
- **Localization — front-end / IR-gen, NOT the LLVM backend**: `bnc --emit-llvm`
  (stops after IR-gen, before the native/LLVM backend) reaches **7.5 GB in 54 s
  and emits 0 IR lines** before being killed. So the blowup is in `bnc`'s
  front-end / IR-gen, not LLVM codegen.
- **NOT raw program size**: `bnc`/`bni` themselves (far larger) build fine.
  Ruled out by probes (all `bnc --emit-llvm`, peak RSS, on a `main` bundle):
  trivial `strconv.FormatFloat` user → light (2 s); recursive/nested managed AST
  types (`Expr{@Expr, @[]@Expr}` + `Stmt`/`Line`) → light; a struct
  `Value{int,float64,@[]char}` returned BY VALUE, standalone → light;
  `Value` + nested AST types + `slices.Append[@Line]` + `buf` together,
  standalone → light; synthetic 10/20/30 functions each building managed
  `Expr`/`Value` → all light.
- **Bisected trigger (a super-linear interaction)**: within minbasic's
  `pkg/basic`, the **parser side alone** (token/ast/lex/parse/parse_expr + the
  basic.bn loader — ~700 lines; nested-managed AST types, `slices.Append`, `buf`)
  compiles LIGHT (2 s). **Adding `value.bn`** — 34 lines: a
  `Value{int,float64,@[]char}` struct + two by-value constructors, *not even
  referenced by the parser side* — flips it to an **8.56 GB blowup**. Each piece
  is light in isolation; the combination is not. Cost appears super-linear in
  (functions × managed-types) within one package, but is NOT reproduced by
  synthetic isolations — the real parser-side code's structure matters.
- **Repro**: (full) build `examples/minbasic/cmd/run` against a `main` `bnc`
  bundle → OOM. (reduced) the same package with the eval-side files
  (eval/exec/print/format/env) removed and `runProgram` stubbed, leaving the
  parser side + `value.bn`, still OOMs at ~8.5 GB; removing `value.bn` makes it
  light (~2 s).
- **Discovery**: 2026-06-05, building minbasic M1 slice 1 (examples `5b55644`).
- **Root cause (triaged 2026-06-05, 5-agent static analysis — strong
  cross-corroboration; all five independently fingered the same site)**: the
  dominant term is **`registerPendingStructDtor` / `registerPendingMsDtor`**
  (`pkg/binate/ir/gen_util_refcount.bn:96-102` / `:143-149`). Each call does a
  linear dedup scan of the **module-global** `pendingStructDtors` list AND, for
  **every** existing entry, *recomputes* `dtorNameForType(entry)` — a `buf.New()`
  managed-slice allocation + a recursive type-spelling walk + `Bytes()`. It is
  invoked from `emitStructCopy`/`emitStructDtor`, which fire at every
  managed-AGGREGATE copy/dtor/scope-cleanup site (var-init, assignment,
  composite-literal field/element, return, and every scope-exit cleanup for every
  managed-aggregate local) across **all** functions; the list grows monotonically
  for the whole package. Net **O(functions × managed-aggregate-types)** with a
  throwaway name-buffer allocation per existing entry per call → both the 54 s
  time and the multi-GB transient/persistent RSS, all before a single IR line.
- **Why `value.bn` is the trigger**: before it, the parser side holds its AST via
  `@Expr` / `@[]@Expr` — managed **pointers/slices**, which take the *scalar*
  refcount arms (`EmitRefInc`/`emitManagedSliceRefDec`), NOT
  `emitStructCopy`/`emitStructDtor`, so `pendingStructDtors` stays ~empty.
  `Value{int,float64,@[]char}` is a managed-**aggregate** (`needsStructCopy` via
  the `@[]char` field), so the moment any `Value` is copied/dtor'd/cleaned-up the
  *aggregate* arms fire across the package's many functions — flipping the
  dominant term from ~0 to `functions × aggregate-sites`.
- **Amplifiers (corroborated, secondary)**: (a) `slices.Append` (stdx) is **O(n)
  per append** — `make_slice(n+1)` + copy-all, no capacity doubling — so every
  hot IR-gen accumulator (`pendingStructDtors`, `ctx.Temps`, `ctx.Vars`, return
  `vals`) is O(n²); (b) `NeedsDestruction` (`types_query.bn:377`) and
  `SizeOf`/`AlignOf`/`FieldOffset` (`scope.bn:112/160/207`) are **unmemoized**
  (no cache slot on `@types.Type`, `types.bni:71`), recomputed at every emit-site;
  (c) `emitDecForManagedLocals` re-scans **all** `ctx.Vars` at each scope-exit;
  (d) `resolveTypeExpr` allocates a fresh `@Type` per type-expr occurrence (no
  interning); (e) `lookupFuncParams`/`collectFuncStrings` do O(n) linear scans.
  The unifying disease: **no memoization on the `@types.Type` node + module-global
  accumulators scanned/re-mangled linearly.**
- **Fix (ranked, layered)**: **(1) PRIMARY** — make the
  `registerPendingStructDtor`/`registerPendingMsDtor` dedup O(1): compute the
  dtor name once for the incoming type, look it up in a set (or hang a
  `DtorRegistered` flag / cached name on `@types.Type`); never recompute
  `dtorNameForType(existing)` in the loop. This alone removes the dominant
  O(functions × types) + per-entry-allocation term. **(2)** add cache slots to
  `@types.Type` and memoize `NeedsDestruction` + `SizeOf`/`AlignOf`/`FieldOffset`
  + the dtor/copy name (layout is fixed within a compile). **(3)** give `slices`
  a capacity-doubling amortized-O(1) append (or use growable buffers for the hot
  accumulators). **(4)** track managed-cleanup slots in a compact per-function
  list instead of re-scanning `ctx.Vars`. (1) is the high-leverage fix; (2)-(4)
  remove the remaining super-linear factors.
- **Validation suggested**: instrument `registerPendingStructDtor`'s call-count ×
  list-length (or a knob-scaled repro: N managed-aggregate types × M functions)
  to confirm the O(N×M) curve, then re-run the reduced minbasic repro after fix
  (1). No `bnc` profiling flag exists; a temporary counter is the cheapest probe.

### >16-byte struct passed by value through an indirect call SIGSEGVs on LLVM — CONFIRMED wrong-code, default modes
- **Symptom**: passing a struct larger than 16 bytes (`three-int` = 24B, all
  `int` fields) **by value** as an **interface-method argument** or through a
  **function-value call** segfaults (exit 139) on the LLVM backend, before any
  output. The *direct* call `takeS(s)` with the identical 24B struct is correct,
  so it is the indirect-call path specifically. Crashes on every LLVM execution
  mode: `builder-comp`, `builder-comp-comp`, `builder-comp-comp-comp` (verified),
  and — by reasoning, since arm32 is LLVM-codegen — `builder-comp_arm32_linux` /
  `builder-comp_arm32_baremetal` (CI to confirm). The bytecode VM passes, and the
  native aa64/x64 backends pass `three-int` (they have their own non-8-multiple /
  sub-word packing defects on other shapes, but not this).
- **Root cause (suspected — needs codegen confirmation)**: the >16-byte
  "memory class" aggregate is lowered with a `byval`-pointer parameter on the
  *direct* call signature but the **indirect** call site (the iface vtable slot
  type, or the function-value's function-pointer type) is emitted without the
  matching `byval` attribute / pointer indirection — caller passes the aggregate
  by-value-in-registers while the callee reads it through a pointer (or vice
  versa), so the callee dereferences a non-pointer field → wild load → SIGSEGV.
  This is the §3.9 "byval/sret threshold disagreement," now confirmed as a hard
  crash rather than a value error. The boundary is bracketed to (16, 24]; a
  17-byte shape would pin it.
- **Test**: `conformance/matrix/abi/iface-param/three-int` and
  `.../funcval-param/three-int` (value-correctness; both SIGSEGV). Xfailed on the
  5 LLVM modes above; `struct-param/three-int` (direct) is the passing control.
- **Discovery**: 2026-06-05, extending the ABI matrix with the call-shape axis
  (iface-method / func-value param passing) — the direct-call cells were all
  green, so the indirect path was the first thing the new axis exercised.
- **Fix**: make the indirect-call signature (iface vtable slot type and
  function-value pointer type) carry the *same* aggregate-passing convention
  (`byval` ptr for memory-class structs) as the direct call. Likely in codegen's
  function-type construction for vtable slots / function-value pointers — it must
  reuse the same param-lowering decision the direct path uses, not a default
  by-value lowering.

### A float literal narrowed to `float32` is NOT coerced at call-arg / composite-field / return positions — FIXED+LANDED (binate `d37cc7ba`, 2026-06-05)
- **Symptom**: an untyped float literal flowing into a `float32` slot via a
  function **argument** (`f(0.1)` where `f(x float32)`), a **composite-literal
  field** (`S{f: 0.1}`, field `f float32`), or a **return** (`func g() float32 {
  return 0.1 }`) is NOT narrowed double→float32. Arg and field SILENTLY produce
  the wrong value: `bit_cast(int32, x)` reads `0x9999999A` (low 32 bits of
  `double(0.1)`) instead of `0x3DCCCCCD` (`float32(0.1)`). Return emits invalid
  LLVM (`value doesn't match function result type 'float'`) → clang rejects.
  Fails on **every** backend (LLVM, VM, native) — it is a front-end gap, not a
  backend issue. The control cases `var x float32 = 0.1`, `const C float32 = 0.1`,
  and a const-group member all narrow correctly (so the coercion exists; it is
  just not applied at these three positions).
- **Root cause (suspected)**: the front-end inserts the float-narrowing
  `OP_CAST` (→ `fptrunc` / `BC_F64_TO_F32`) only on var-init / typed-const decls
  via `ensureWidth`; the call-arg path (`genExprOrFuncRef` / `coerceArg`),
  composite-field store (`gen_composite.bn` `EmitStore`, no `ensureWidth`), and
  the `return` path do INT narrowing only — an untyped-float literal at a
  `float32` slot keeps its `double` type. Cite: gen_composite.bn:50-59,140;
  gen_expr.bn:37-39 (untyped-float born `double`).
- **Severity**: CRITICAL — passing a float literal to a `float32` parameter or
  initializing a `float32` struct field with one are idiomatic, and the value is
  silently wrong (no diagnostic). Distinct from the DEFERRED §844 (which is the
  *backend* float32-const bug on VM/native); this is a front-end coercion gap
  that hits LLVM too.
- **Test**: `conformance/matrix/const/{call-arg,field,return}/float32/*` (9 cells;
  arg/field = wrong value, return = compile error). To land: see the
  matrix-vs-regressions decision below — likely a few representative
  `regressions/` cells (the bug is position-dependent, not type-dependent).
- **Discovery**: 2026-06-05, P1 const matrix (read-form axis).
- **Fix**: apply the float-width coercion (`ensureWidth`/equivalent) for
  untyped-float literals at call-arg, composite-literal-field, and return
  positions — the same narrowing the var-init path already performs.

### Local `const` declarations silently materialize 0 — FIXED+LANDED (binate `273d7e4a`, 2026-06-05)
- **Symptom**: a `const` declared inside a function body (`func main() { const C
  T = V; var x T = C }`) reads as **0** (the zero value), for EVERY type
  (int/uint of all widths, float32, float64). The value `V` is dropped entirely.
  Fails on every backend (LLVM/VM/native). Package-level `const`, const-group
  members, and inline literals all work — only the **local** const form is
  broken. Local `const` is currently used nowhere in the compiler tree or
  conformance suite, so real-world impact is nil today, but it is a silent-wrong-
  value landmine.
- **Root cause (unknown — needs investigation)**: a local const declaration
  appears to register the name but never bind its value at the IR-gen read site
  (the read resolves to a zero-initialized slot rather than the const's
  materialized value). Either local consts must materialize like package consts,
  or the type-checker should reject local `const` until supported — silently
  emitting 0 is the wrong outcome.
- **Test**: `conformance/matrix/const/local-const/*` (12 cells, all types). To
  land: see the matrix-vs-regressions decision (one representative cell likely
  suffices — the bug is type-independent).
- **Discovery**: 2026-06-05, P1 const matrix (read-form axis).
- **Fix**: bind a local const's materialized value at its read site (mirror the
  package-const path), or reject local `const` at type-check if intentionally
  unsupported.

### Non-integer const-EXPRESSIONS (binary float, bool comparison) and const-as-array-dimension are dropped → read as int 0 — FIXED+LANDED (binate `52a9eabf` and predecessors, 2026-06-05)
- **Scope**: this is the const-*expression* tail of the non-int-const family
  (the literal cases — `const C float64 = 0.1`, `const B bool = true` — were
  fixed in Phase A; see the "top-level consts of non-int types" MAJOR entry).
  `classifyConstLit` recognizes only a *bare / unary-minus* float or bool
  **literal**; any non-int const whose initializer is an **expression** still
  falls through to the integer-only `evalConstExpr`, which can't evaluate it, so
  `genConst` drops the const and reads fall to `EmitConstInt(0, TypInt())`.
- **Confirmed manifestations** (2026-06-05, on LLVM — default mode):
  - **binary float** — `const X float64 = 1.5 + 2.5` (and `*`, `/`) reads as
    **0** (silent wrong; in some shapes emits `mul i64` over `double` operands →
    invalid IR / clang reject).
  - **bool comparison** — `const B bool = 1 < 2` reads as **0** (false) instead
    of true; `< == > …` const-comparisons are dropped.
  - **const-as-array-dimension** — `const N int = 3; var a [N]int` →
    `len(a)` is wrong (observed 30, not 3): `resolveTypeExpr` (gen_util.bn:354-359)
    uses `parseIntLit(te.Len.Name)` on the *ident text*, never resolving the
    const; and `[N+1]int` is rejected outright by the checker's `evalConstInt`
    ("array length must be a constant integer") even though it is one.
- **Root cause**: IR-gen's const-expression evaluation is integer-only
  (`evalConstExpr`, gen_const.bn) and `classifyConstLit` is literal-only; the
  checker accepts these decls (it does fold ints via `foldIntArith`/
  `foldIntBitwise` but attaches no value to float/bool exprs). Same root as the
  non-int-literal family — extended from *literals* to *expressions* and to the
  array-dimension read path.
- **Severity**: MAJOR — silent wrong values (bool/float) and a silently wrong
  array length, on idiomatic const-expressions; the binary-float shape can also
  emit invalid IR.
- **Tests**: `conformance/regressions/const-expr/*` — green baselines
  (`int-arith`, `int-bitwise`, `int-paren`, `int-of-const`, `float-neg-literal`,
  `bool-literal`) confirm the integer/literal paths fold; xfailed
  (`float-binary-{add,div,mul}`, `bool-comparison`, `array-dim`) pin the gaps.
- **RESOLVED — now a Plan-1 defect (2026-06-05, user decision)**: a **bare**
  const-group member must **repeat the previous initializer expression**
  (Go-style), not take plain iota. Today it takes plain iota
  (`gen_const.bn:293-299`), so `const ( B0 int = 1 << iota; B1; B2; B3 )` gives
  `1,1,2,3` instead of the correct `1,2,4,8` bit-flag idiom, and
  `const ( K0 int = iota + 100; K1; K2 )` gives `1,2` instead of `101,102`. This
  is now a CONFIRMED bug to fix in Plan 1: a bare member re-evaluates the most
  recent explicit initializer expression with its own `iota`. Test:
  `conformance/regressions/const-expr/iota-repeat` (the `1<<iota` bit-flag form,
  xfailed until implemented).
- **Discovery**: 2026-06-05, P1 const-expr loose-axis (design fan-out + probes).
- **Fix**: evaluate non-int const *expressions* at the right type — fold float
  const-exprs at float precision and bool const-comparisons to a bool, and
  resolve const idents/exprs in the array-dimension path — or reject
  unsupported const-exprs with a clear diagnostic rather than dropping to int 0.

### Native backends mis-pass a variadic float `__c_call` argument — CONFIRMED, both native backends
- **Symptom**: a variadic `double` passed via `__c_call` reaches the callee
  wrong on the native backends — `__c_call("printf", int32, fmtPtr, ...,
  cast(float64, 2.0))` with format `"%.0f\n"` prints **0**, not **2**. Correct
  on LLVM (comp) and the VM is N/A (`__c_call` is compiled-mode-only). Fails on
  both `native_aa64` and `native_x64`.
- **Root cause (suspected, §3.9)**: the variadic calling-convention edge — on
  x86-64 SysV the caller must set `AL` = number of vector (XMM) args so a
  variadic `double` is read from `XMM0`; on darwin-arm64 every variadic arg is
  passed on the stack as an 8-byte slot (not in registers). The native backends
  do neither for the `__c_call` variadic tail, so the float lands in the wrong
  place and printf reads garbage/0.
- **Test**: `conformance/regressions/c-call/printf-variadic-float` (xfailed the
  3 native modes; also xfailed VM + arm32 like all `__c_call` cells).
- **Discovery**: 2026-06-05, P1 `__c_call` loose-axis.
- **Fix**: in the native `__c_call` lowering, implement the variadic ABI —
  set `AL`=vector-count on x64-SysV; stack-pass varargs on darwin-arm64
  (per-target, since the convention differs).

### `handle` is not a user-expressible call shape — NOT a bug, design note
- While extending the ABI matrix with call shapes, confirmed there is **no user
  syntax that emits `OP_CALL_HANDLE` with a value argument**: `OP_CALL_HANDLE`
  is the compiler-internal dtor/free dispatch (`_call_dtor` / `_call_free_fn`,
  gen_call.bn:241), always invoked with a single pointer. A user "call through a
  function value" lowers to `OP_CALL_FUNC_VALUE`, already covered by the ABI
  matrix's `funcval-param` cells. So the §3.9 "CALL_HANDLE aggregate by-value"
  concern has no user-level test surface; nothing to add.

### `&slice[i]` (address-of a slice element) lowers to a wild pointer — FIXED+LANDED (binate `937ae78e`, 2026-06-05)
- **Symptom**: taking the address of a *slice*-indexed element yields a garbage
  pointer instead of the element address. `var p *uint8 = &s[0]; *p = 66`
  SIGSEGVs (the store writes through `(i8*)0x41`). Affects both `@[]T`
  managed-slices and `*[]T` raw slices; **fixed arrays `[N]T` are correct**
  (`&a[0]` works). Crashes identically compiled (bnc) and interpreted (bni), so
  the defect is in the shared IR address-of lowering, not a backend.
- **Root cause (CONFIRMED)**: the address-of path for a slice-indexed l-value
  computes the correct element address via GEP, then wrongly falls through to the
  *r-value* path — it loads the element and `inttoptr`s the byte:
  `%a = getelementptr i8, i8* %data, i64 %idx` (element address — correct) →
  `%v = load i8, i8* %a` (BUG: loads the VALUE) →
  `%p = inttoptr i8 %v to i8*` (BUG: byte → pointer). Fixed arrays take the
  proper address path (yield the GEP), which is why `&a[0]` works; slice-indexed
  operands share the load path instead. Likely in IR-gen's address-of handling
  for a SliceIndex operand (gen_expr l-value path).
- **Test**: `conformance/599_addr_of_slice_elem.bn` — `&slice[i]` write-through +
  read-back on `@[]T` and `*[]T` (mutation must be visible; currently SIGSEGVs).
  Xfailed in all 6 default modes.
- **Discovery**: 2026-06-05, while probing bundle I/O for the minbasic example —
  `__c_call("write", …, &buf[0], …)` silently wrote nothing; chasing it exposed
  the address-of miscompile. Confirmed firsthand against `bnc-0.0.7` with
  `--emit-llvm`, and **confirmed still present in local main HEAD** (2026-06-05)
  via `conformance/run.sh builder-comp` + `builder-comp-int`.
- **Fix**: the slice-indexed l-value address-of must yield the GEP'd element
  address, not load+inttoptr — mirror the fixed-array address path. (If
  `&slice[i]` were intentionally unsupported, reject at type-check instead — but
  arrays support it and raw pointers are the documented hot-path escape, so
  emitting the address is the intended fix.)

### VM: a function value RETURNED from a call and PASSED DIRECTLY as an argument has a nil vtable — CONFIRMED, VM-only
- **Symptom**: `use(mk())`, where `mk() @func(...)` returns a (non-capturing)
  function value and `use(w @func(...))` invokes it, aborts in the bytecode VM
  with `vm: function value has nil vtable`. Compiled (native) is correct.
- **Scope**: bytecode VM ONLY (LLVM/native correct). Triggered specifically by
  passing a freshly-RETURNED function value DIRECTLY as a call argument. The two
  halves work in isolation: returning a function value then calling it directly
  (`var w = mk(); w(x)`) is fine, and passing a LOCAL/param function value as an
  arg (`use(w)` with `w` a local) is fine — only the un-materialized
  return-value-as-arg combination loses the vtable word. Workaround: bind to a
  local first (`var w @func(...) = mk(); use(w)`).
- **Test**: ✅ `conformance/regressions/funcval/return-as-arg` (binate
  `d493b25b`, on the worktree, pending cherry-pick). `use(mk())` returning/
  passing a non-capturing `@func(int) int`, asserts `42`. Verified: compiled-
  final + native pass; the 3 VM-final modes (`builder-comp-int`,
  `builder-comp-int-int`, `builder-comp-comp-int`) abort `nil vtable` and are
  xfailed — un-xfail when the fix lands.
- **Discovery**: 2026-06-05, wiring minbasic's injected `@func` writer
  (`basic.Run(host.NewWriter())`): the VM aborted with nil vtable. Isolated to
  the return-value-as-arg pattern; `bnc-0.0.7`.
- **Why it matters**: blocks injecting a `@func` writer/sink built by a factory
  (`Run(host.NewWriter())`) — a natural DI shape. Together with the iface-vtable
  2-word-slice-arg bug, it leaves only static/direct calls reliable for I/O
  injection on `bnc-0.0.7`, so minbasic uses a clearly-marked static temp
  meanwhile.
- **Fix**: in the VM, marshal a function-value (2-word {vtable,data}) call
  argument that is an un-spilled call result the same way a local/param function
  value is marshalled — the vtable word is being dropped for the return-value-as-
  arg case.

### Sub-word arithmetic results not narrowed in the VM (and natives) — dirty upper bits → wrong values — CONFIRMED — UPDATE 2026-06-06: the scalar-diff differential confirms the native-aa64 variant extends beyond arithmetic to sub-word **signed shifts, all int-casts, and signed sub-word conversions**; see `aa64-subword` below
- **Symptom**: a sub-word integer op (`uint8/16/32` add/mul/…) whose true result
  overflows the width leaves the un-narrowed value in the host register; a
  width-sensitive consumer reached DIRECTLY (no intervening sized store/cast) —
  shift, unsigned compare, divide, widen — reads the dirty upper bits → wrong
  value. E.g. `(a*b) >> 8` for `uint16 a=b=60000`: **164 on LLVM, 37796 on the VM**.
- **Root cause (CONFIRMED)**: the bytecode VM's `execArithOp`
  (`vm_exec_pure.bn`) computes at the host word width with no post-op narrowing
  to the result type's width; the native backends (x64/aa64) carry the same gap
  (§3.8). LLVM is correct (true-width SSA). Storing the result into a sized var
  re-narrows it, so the bug is latent until the op result is consumed directly.
- **Test**: `conformance/matrix/scalar/{add,mul}/{8,16,32}/unsigned` (xfailed the
  3 VM default modes; pass on LLVM). The scalar matrix's first members.
- **Discovery**: 2026-06-05, P1 scalar matrix. Flagged in plan-code-red.md §3.8 /
  §8; now confirmed + systematically covered.
- **Fix**: narrow sub-word op results to their width — a post-op narrow in the
  VM/native arith handlers, or an IR-gen narrow after each sub-word value-
  producing op (a P3 design call). Also covers the native variants.

### Unsigned int→float uses a SIGNED conversion in the VM — wrong value — CONFIRMED — UPDATE 2026-06-06: the scalar-diff differential shows the unsigned→**float64** path now PASSES on the VM (so this specific signedness bug appears resolved); a *distinct* int→float32 defect remains — see `vm-int-to-float32` below
- **Symptom**: `cast(float64, y)` for an unsigned int whose top register bit is
  set (on the 64-bit host, only `uint64` with bit 63) yields a NEGATIVE float —
  the VM converts as signed. E.g. `cast(float64, <uint64 bit-63>) > 0.0` is
  true on LLVM, false on the VM.
- **Root cause (CONFIRMED)**: the VM's int→float lowering uses `BC_SITOF`
  (signed) regardless of the operand's signedness; LLVM uses `uitofp` for
  unsigned. The native backends carry the same gap (§3.8). A `uint32` is
  zero-extended (positive in the 64-bit register), so only `uint64` triggers
  it on the host.
- **Test**: `conformance/matrix/scalar/int-to-float/64/unsigned` (xfailed the 3
  VM modes; `/32` passes as a baseline).
- **Discovery**: 2026-06-05, P1 scalar matrix int-to-float cells. Flagged §3.8.
- **Fix**: dispatch int→float on operand signedness (a `BC_UITOF` / unsigned
  path), mirroring the cmp/div/shift signedness selection. Same for float→int
  and the native backends.

### Differential scalar harness (`matrix/scalar-diff`) landed — two backend defects found: `vm-int-to-float32` and `aa64-subword` — CONFIRMED
- **What landed**: `conformance/gen-diff-scalar.py` + 41 cells / 1707 tuples
  under `conformance/matrix/scalar-diff/` — a property-based **differential**
  value-correctness harness for scalar shifts & conversions. Oracle is the
  **spec** (computed at full precision, independently validated by a 5-reader
  adversarial pass), not a backend, so spec-divergences (the shift-bug class)
  are caught too. Self-checking cells (`println(cast(int, computed == spec))`)
  for target-stability across 32/64-bit. Green on all LLVM modes + arm32
  baremetal; the two clusters below are xfailed (verified non-stale via
  `--check-xpass`). Idempotent generator; `int↔int` casts and all shifts pass
  on every real backend (broadened regression net for `32fde83d`).
- **`vm-int-to-float32` — VM `int → float32` is broken (every width/sign)**:
  every `cast(float32, <int>)` diverges — even `cast(float32, 1) > 0.0` is
  false on the VM. `float64` conversions, `float32 → int` truncation, and
  `float32` literals all work; the 17 xfailed VM cells (all `int-to-float` /
  `float-to-int` / `float-cast`) fail *only* on their `float32` tuples.
  Distinct from the now-resolved unsigned→float64 signedness bug above. Likely
  the VM never implemented (or mis-lowered) the 32-bit-float conversion target.
  Tests: the 17 cells, xfailed on `builder-comp-int` / `-int-int` /
  `-comp-comp-int`. Fix: implement/repair `int → float32` in the VM's
  `lower_cast` (both `BC_SITOF`/`BC_UITOF` to a 32-bit float result).
- **`aa64-subword` — native-aa64 doesn't narrow/sign-extend sub-word results**:
  a sub-word op leaves dirty high bits / wrong sign. `int8(-128) << 1` keeps
  bit 8 set (so `== 0` fails); `cast(int8, 128:uint8)` and the other
  `uint8 → int{8,16}` casts are wrong. 17 xfailed cells: `shl`/`shr` 8/16/32
  **signed**, all 8 `int-cast`, signed sub-word `float-to-int`/`int-to-float`.
  64-bit and most unsigned paths are fine. The native sibling of the VM/native
  sub-word-narrowing gap above, here confirmed across shifts/casts/conversions
  (not just arithmetic). Fix: post-op narrow + sign-extend sub-word results in
  the aa64 backend (or an IR-gen narrow — the shared P3 design call).
- **native-x64 / arm32-linux not evaluated**: the host lacks x86_64 C runtime
  headers (`stdio.h` → every native-x64 cell `COMPILE_ERROR`s uniformly, an env
  limitation, *not* a backend result — no x64 xfails placed), and `arm32-linux`
  needs `qemu-arm` (skipped). Re-check on an x64 host: the aa64 sub-word defect
  very likely has an x64 analog needing its own xfails.
- **Discovery**: 2026-06-06, differential-harness v1 (plan-differential-testing.md).

### ~~Short-var single-bind `x := s` of a managed struct-by-value skips the copy~~ — FIXED + LANDED 2026-06-05 (binate `b0eb7299`, plan-cr-p2-2 step 3; routed through `emitStoreManagedSlot`; matrix short-var/ident/managed-struct un-xfailed)
- **Symptom**: `x := src` where `src` is a struct with a managed field copies the
  struct WITHOUT `__copy_` — the copy's managed field is not RefInc'd, so when
  both `src` and `x` leave scope the field is RefDec'd twice (double-free).
  `var x T = src` and `x = src` (var-init / assign) copy correctly; only short-var
  `:=` under-copies.
- **Root cause (CONFIRMED)**: `genShortVar`'s single-bind arm
  (`gen_short_var.bn:83-117`) has `isManagedPtrType` / `isManagedSliceType` /
  `isManagedFuncValueType` / `isManagedIfaceValueType` cases but NO
  `needsStructCopy` arm — a managed struct/array aggregate RHS is stored raw.
  var-init and the short-var MULTI-bind arm (`:41`) both `emitStructCopy`; the
  single-bind arm is the gap.
- **Test**: `conformance/matrix/short-var/ident/managed-struct.bn` (xfailed all 6
  default modes) — observable refcount stays 1 after `tgt := src` vs the balanced 2.
- **Discovery**: 2026-06-05, P1 matrix generator (the managed-struct cell across
  forms — var-init/assign pass, short-var fails).
- **Fix**: add a `needsStructCopy(typ) { emitStructCopy(...) }` arm to
  genShortVar's single-bind path, mirroring var-init.

### ~~`for v in coll` over a managed-element collection over-releases the bound value~~ — FIXED + LANDED 2026-06-05 (binate `b0eb7299`, plan-cr-p2-2 step 3; the bind acquires via `emitStoreManagedSlot`, blank `_` skips the bind; matrix for-range-value cells + `602`)
- **Symptom**: `for v in s` where `s @[]@T` (or `[N]@T`) loads each element as a
  borrow (no RefInc) but `defineVar` registers `v` as a managed scope var, so
  scope cleanup RefDec's `v` — an unbalanced release. Per iteration the bound
  element is over-released by one; at the collection's destruction it
  double-frees. Latent because the over-release lands at v's SCOPE END (after a
  mid-function refcount read), so it surfaces only once that scope closes.
- **Root cause (CONFIRMED)**: `genForIn` (`gen_flow.bn:137-149`) emits the
  element load (a borrow) then a raw `OP_STORE` into v's slot + `defineVar` —
  no RefInc of the new value, yet v joins `ctx.Vars` and is RefDec'd at cleanup.
  The bind must acquire (RefInc / `__copy_`, the isFresh/RefInc-borrowed hybrid
  the assignment arms use) before defining v, OR v must be a non-owning borrow
  not registered for RefDec. Also covers `for i, v`, array collections, and the
  blank `_` value (a phantom scope var today).
- **Test**: `conformance/matrix/for-range-value/value/managed-ptr.bn` (xfailed in
  all 6 default modes) — `loopOnce(s)` ranges + returns, then `rt.Refcount`
  reads 1 instead of the balanced 2. Confirmed comp / int / int-int /
  comp-comp-comp.
- **Discovery**: 2026-06-05, P1 conformance-matrix authoring. Pre-existing;
  flagged suspected in plan-code-red.md §3.2/§3.4, now confirmed with a repro.

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

### ~~Managed-aggregate-by-value element/field stores skip save-copy-destroy~~ — ALL SIBLINGS FIXED + LANDED 2026-06-04 — MEMORY-CORRECTNESS (was latent)
- **UPDATE 2026-06-04 (binate `32bad348`)**: the two gaps below are now
  FIXED.  The single-assign ARRAY-element aggregate arm landed; the
  multi-assign SLICE aggregate case was switched from the incomplete
  `emitStructElemRefcount` to the two-slot `emitStructCopy`/`emitStructDtor`
  form (complete for `@Iface` fields + nested aggregates), and
  `emitStructElemRefcount` was deleted.  Pinned by `conformance/583`
  (multi-assign slice element with an `@Iface` field — verified to fail
  pre-fix) and `582` (single-assign array aggregate).  All ASSIGNMENT-store
  paths (single + multi assign, IDENT/SELECTOR/array/pointer/slice) now
  save-copy-destroy correctly.  ALL SIBLINGS now also done: short-var
  multi-bind (CRITICAL, `efa4f569`), raw-pointer single-assign index (MAJOR,
  `5429a37d`), array/managed-slice/struct literals (MAJOR, `f2aff0d4`,
  including a third-sibling `@func` struct-field UAF found during that work) —
  see the (struck-through) entries below.
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

### ~~Raw-pointer single-assign index `p[i] = v` does no element refcounting~~ — FIXED + LANDED 2026-06-04 (binate `5429a37d`)
- **Fixed**: the TYP_POINTER arm now mirrors the array arm — RefDec-old +
  consumeTemp-if-fresh-else-RefInc-new for the four managed-scalar kinds, and
  save-copy-destroy (`emitStructCopy`/`emitStructDtor`) for managed aggregates.
  Pinned by `conformance/589` (raw `*@Box`: old released 3->2, new acquired
  1->2; output `3` not `2` pre-fix, green all 6 modes) + unit tests
  `TestRawPtrIndexAssignManagedRefcounts` (baseline-delta) /
  `TestRawPtrIndexAssignAggregateCopies` (`__copy_`); both fail pre-fix.
- **Original analysis retained below.**
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

### ~~Array-literal / managed-slice-literal elements don't acquire managed-aggregate fields~~ — FIXED + LANDED 2026-06-04 (binate `f2aff0d4`)
- **Fixed**: all three composite-literal constructors now acquire managed
  elements/fields.  `genArrayLit` gained the FULL acquire — it was missing
  EVERY managed-scalar arm, not just the aggregate one this entry named, so
  `[2]@Node{a,a}` under-retained too; now mirrors `genCompositeLit`
  (always-RefInc @T/@[]T, consumeTemp-if-fresh @func/@Iface, OP_CONST_NIL-
  guarded) + `emitStructCopy` for aggregates.  `genManagedSliceLit` gained the
  omitted `@func` arm + the aggregate arm (throwaway-slot `__copy_`).
  `genCompositeLit` (struct literals) gained the omitted `@func` field arm —
  the SAME closure-record UAF in a third sibling, fixed as part of the class
  (was untracked; discovered during this work).  Pinned by `conformance/590`
  (array aggregate) + `591` (managed-slice aggregate) — green all 6 modes,
  off-by-2 + double-free pre-fix — and 5 unit tests in `gen_composite_test.bn`
  (aggregate `__copy_` for array+slice; per-element acquire deltas for
  array-scalar, slice-`@func`, struct-`@func`); all fail pre-fix.  The literal
  constructors relocated `gen_access.bn` → `gen_composite.bn` (500-line cap).
- **Original analysis retained below.**
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
- **Unblocked + the VM-free poll is now LANDED 2026-06-04** (binate
  `e3dc0d07`): repl's SetPoll takes a VM-free `@func() PollResult`, so
  the ReplSession interface no longer mentions pkg/binate/vm.

### `136_grouped_imports` / `383_cross_pkg_iface_dtor` — `package "pkg/builtins/rt" not found` under int-int — FIXED+LANDED (binate `db18f26b`, 2026-06-05; harness wiring, not the loader)
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

### A NAMED distinct *signed sub-word* integer's MIN/-1 divide escapes the divide-fault guard
- **Symptom**: `type I8 int8; var a I8 = <I8 MIN>; var b I8 = -1; a / b` does NOT
  panic with "integer overflow" (the ratified signed-MIN/-1 behavior); it
  silently wraps (the int64 divide `-128 / -1 = 128` truncates back to `-128`
  in the I8 result). Divide-by-zero on the same type IS still caught, and
  unsigned named types / named full-width signed types (`type Count int`) are
  fine — only a named *signed sub-word* type at exactly MIN/-1 is affected.
- **Root cause**: IR-gen's `widenType` (gen_binary.bn) collapses a distinct
  NAMED integer type to plain `int` (signed, host width) — the named/sized-ness
  is lost before the `OP_DIV_CHECK` guard sees the result type, so the guard
  uses INT64_MIN instead of the type's true (e.g. int8) MIN. This is a
  pre-existing `widenType` behavior, not a defect in the divide-fault guard
  itself (plain, non-named `int8`/`int16`/`int32` MIN/-1 ARE detected — they
  keep their TYP_INT width through widenType).
- **Discovered**: 2026-06-05 by the adversarial coverage review of the
  divide-fault guard (plan-divide-by-zero.md). The guard itself is correct;
  this is the one width-dependent corner it can't reach because the type info
  is already gone.
- **Proper fix**: make `widenType` preserve a named integer type (or at least
  its underlying width/signedness) for same-named operands, so `I8 / I8` keeps
  width 8. Out of scope for the divide-by-zero work (touches general arithmetic
  typing). A reproducer xfail cell can be added when this is picked up.

### Bare func literal in assignment position doesn't infer its managed/raw flavour from the LHS
- `existing = func(){...}` where `existing @func(...)...` fails type checking
  with `cannot assign <unknown> to <unknown>`: a bare func literal in
  **assignment** (non-var-init) position does not pick up its managed
  (`@func`) vs raw (`*func`) flavour from the assignment target's type.
  Var-init works (`var x @func(...)... = func(){...}` — the declared type
  hints the flavour).
- **Workaround in use**: assign through a typed var
  (`var drop @func(...)... = func(){...}; existing = drop`) — see
  `conformance/587_closure_captures_func_value.bn` and
  `conformance/matrix/assign/ident/func-value.bn`.
- **Fix**: in the assignment type-checker, flow the LHS func type's flavour
  to a bare func-literal RHS — the same hinting var-init already applies.
- Surfaced 2026-06-05 while authoring the conformance matrix func-value cell
  (plan-code-red.md §7 / P1).

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

### Float `!=` is ORDERED (`NaN != NaN` is false) — diverges from IEEE/Go/C; `==` and `!=` not complementary for NaN — FIXED 2026-06-06 (binate `8f78575f`)
- **Symptom**: `var n float64 = NaN; n != n` evaluates to **false** (and `n == n`
  is also false), so the two are not complements. Every other language (Go, C,
  Rust, IEEE 754) makes `!=` *unordered*: `NaN != NaN` is **true**, and
  `(a == b) == !(a != b)` always holds. Any Binate code using the idiomatic
  `x != x` NaN test, or doing NaN-aware compare/sort/dedup, silently
  mis-behaves.
- **Root cause (deliberate, now reversed by user, 2026-06-06)**: the float
  compare emitters force ordered semantics for `!=`. LLVM `emit_ops.bn` uses
  `one` (ordered) instead of `une`; x64 `x64_float.bn` AND's `SETNE` with
  `SETNP` (NaN-gate); aarch64 `aarch64_float.bn` adds a `Csel … COND_VC` to
  zero the unordered result. `==` (`oeq`) and the four relationals (`olt`/`ole`/
  `ogt`/`oge`) are already correct; only `!=` is wrong.
- **Fix** (Phase 0 of `plan-std-math.md`): `one`→`une` (LLVM); `SETNE OR SETP`
  (x64); delete the aarch64 `OP_NE` Csel block; VM is fixed transitively
  (recompile) + a test. `oeq`/`une` are exact complements, restoring
  complementarity. Pin with a conformance cell (NaN compares + complementarity)
  across all default + native alt-modes; update the misleading code comments and
  add a float-comparison spec entry to `claude-notes.md`.
- **Discovered**: 2026-06-06 while scoping `pkg/std/math` (IsNaN needs correct
  NaN semantics). Prerequisite for the math package; lands standalone first.

### Native widening int casts don't sign/zero-extend from the SOURCE width — silent wrong value for a non-canonical source — FIXED 2026-06-05 (binate 445d846a)
- **Symptom**: a widening integer cast (`cast(int, <int32 x>)`, sub-word →
  host-word) on both native backends does NOT re-extend the value from the
  source width; it just MOVs, assuming the source register is already
  sign/zero-canonical. The VM (`BC_SEXT`/`BC_ZEXT`) and LLVM (`sext`/`zext`)
  extend per the source type, so this is a native-only divergence — a silent
  wrong value whenever the source register is non-canonical.
- **Root cause**: `emitCast` (aa64 `aarch64_ops.bn:476`, x64 mirror) keys ONLY
  on the TARGET width: for `target.Width == 0 || >= 64` it emits a plain MOV
  (no extension); the sub-word LSL+ASR/LSR path only runs for a *narrowing*
  target. It never receives the source type, so it cannot extend-from-source on
  a widening cast.
- **Why it surfaced now**: post-4.1 (sub-word arith narrowing), arith results
  ARE canonical, so `cast(int, arithResult)` is correct via the MOV. But a
  `bit_cast(int32, <float32 const>)` result is left ZERO-extended (bit_cast is a
  plain reinterpret MOV), so `cast(int, bit_cast(int32, Neg))` keeps the
  zero-extended bits → `println` prints `3184315597` instead of `-1110651699`.
  This is the residual on **conformance/539_float32_const** (xfailed on all 3
  native lanes; the 4 non-negative lines pass; passes on VM + LLVM).
- **Fix (LANDED 445d846a)**: thread the source type into `emitCast` on both
  natives; on a widening cast (target host-word), sign/zero-extend from the
  SOURCE width per the source's signedness — mirroring the VM's `BC_SEXT`/
  `BC_ZEXT`. Narrowing casts keep the target-width behavior. No-op for canonical
  sources (scalar-matrix cells unaffected). The fix at the CAST is the right
  layer — do NOT narrow at OP_BIT_CAST instead (that would also touch the
  compiler's internal pointer bit_casts; the cast site is where the widening
  semantics belong).
- **CORRECTION — the earlier "blocked by a self-compilation break" conclusion
  was WRONG**: I had attributed a ~267/796 aa64 conformance wipeout (`bnc` link
  error `_bn_pkg__bootstrap__Write` undefined) to this fix. That breakage is the
  **separate, already-tracked CRITICAL aa64-native lane regression** (from the
  divide-fault guard series) — my experiments were rebased onto a base that
  already had it. There is NO hidden cmd/bnc cast/bit_cast dependency. Proof: the
  fix on the **clean x64_darwin lane** gives 807 passed / 4 failed (only the 4
  unrelated pre-existing failures, NOT 267), and 539 passes. The aa64 lane can't
  confirm until its CRITICAL issue is resolved, but 539 passed there too and the
  aa64 emitCast uses identical logic.
- **Test**: `conformance/539_float32_const` — now green on all modes (native
  xfails dropped). A direct `cast(int, bit_cast(int32, <high-bit u32>))`
  regression cell would harden it further.
- **Severity**: was MAJOR (silent wrong value, native-only). Resolved.

### aa64 native backend mis-packs non-8-multiple / sub-word-packed structs (param + return) — CONFIRMED
- **Symptom**: a struct whose size is not a multiple of 8 (`3×uint32` = 12B) or
  whose fields pack sub-word (`5×uint8` = 5B), passed OR returned by value,
  loses/corrupts its trailing field on the aa64 native backend — e.g.
  struct-return of `{uint32,uint32,uint32}` reads the third field wrong.
- **Test**: `conformance/matrix/abi/struct-{param,return}/{three-u32,five-u8}`
  (4 cells, xfailed aa64-native). Pass on LLVM + VM (and x64-return).
- **Discovery**: 2026-06-05, P1 ABI matrix. §3.9 (the aa64 non-8-multiple
  tail-drop / sub-word packing).
- **Root cause**: the aa64 aggregate param/return regWords-vs-stack split
  drops or mis-sizes the trailing sub-8-byte chunk. Needs investigation.

### x64 native backend mis-packs sub-word multi-return + non-8-multiple struct params — CONFIRMED
- **Symptom**: (a) a sub-word (`uint16`) multi-return at arity ≥ 3 mis-packs the
  3rd+ component; (b) a `3×uint32` (12B) or `5×uint8` (5B) struct passed by value
  as a param loses its trailing field. (x64 struct-RETURN works.) On x64 native.
- **Test**: `conformance/matrix/abi/multi-return/u16/{3,4,5}` +
  `abi/struct-param/{three-u32,five-u8}` (5 cells, xfailed both x64 modes). Pass
  on LLVM + VM (and aa64 multi-return).
- **Discovery**: 2026-06-05, P1 ABI matrix. §3.9. NOTE: the all-int multi-return
  n=2-cap from §3.1 is **FIXED** (arity ≤ 5 all-int passes everywhere).
- **Root cause**: x64 aggregate-arg + sub-word multi-return packing. Needs
  investigation.

### Managed-struct under multi-assign / multi-short-var miscompiled on the x64 native backend — CONFIRMED, x64-specific
- **Symptom**: a by-value managed-struct destructured into a target via
  multi-assign (`s.f, _ = pair()`, `arr[i], _ = pair()`, …) or multi-short-var
  (`a, _ := pair()`) is miscompiled by the x64 native backend — the
  refcount-balance cell reads a wrong value. PASSES on aa64-native and the
  LLVM / VM modes, so it is x64-codegen-specific.
- **Test**: `conformance/matrix/refcount/multi-assign/{ident,index-array,
  index-rawptr,index-slice,selector}/managed-struct` +
  `multi-short-var/ident/managed-struct` (6 cells, xfailed both x64 modes).
- **Discovery**: 2026-06-05, triaging the matrix on the x64 native lane (via
  x64_darwin/Rosetta), while enabling x64 in the `all` modeset.
- **Root cause**: unknown — the x64 native backend's aggregate handling in the
  multi-assign / multi-bind element-store path. Needs investigation.

### ~~Discarded `@func`-returning call result leaks~~ — FIXED + LANDED 2026-06-05 (binate `f5410fcf`, plan-cr-p2-2 step 2; `registerManagedCallResult` at all 4 call sites + the missing `@func` arm in `emitTempCleanupBody`/`Since` + `OP_CALL_FUNC_VALUE`/`OP_CALL_IFACE_METHOD` in the isFresh predicates; matrix assign/blank/func-value + discard/stmt + `601`)
- **Symptom**: a managed `@func` returned by a call and discarded (`_ = f()`,
  or an unused call result) is never released — its closure record (and any
  captured managed values) leaks. `@T` / `@[]T` / `@Iface` / struct call results
  are registered as cleanup temps and freed; only `@func` is missing.
- **Root cause (CONFIRMED)**: `genFuncDirectCall` (`gen_call.bn:268-288`) /
  `genFuncValueCall` (`gen_call.bn:366-382`) / `gen_method.bn` register
  `@T`/`@[]T`/`@Iface`/struct results as end-of-statement cleanup temps but have
  no `isManagedFuncValueType` arm; `emitTempCleanupBody` likewise lacks the
  func-value RefDec arm, and `isFreshManagedFuncValue` omits the call ops.
- **Test**: `conformance/matrix/assign/blank/func-value.bn` (xfailed all 6
  default modes) — `_ = wrap(src)` leaves the @func record at 2 instead of 1.
- **Discovery**: 2026-06-05, P1 matrix blank-discard form. Pre-existing; flagged
  suspected in plan-code-red.md §3.4 / §8 #16, now confirmed with a repro.
- **Fix**: add the `isManagedFuncValueType` arm to the call-result temp
  registration (gen_call / gen_method) + the func-value RefDec arm in
  `emitTempCleanupBody`; add the call ops to `isFreshManagedFuncValue`.

### Interface dispatch drops the trailing scalar after a multi-word by-value arg (LLVM/native codegen) — xfail'd
- **Symptom**: an interface method whose params include a multi-word BY-VALUE
  arg (a struct with managed fields, or a `@[]T` managed-slice param — both 4
  words) followed by a scalar drops the scalar through vtable dispatch — the
  multi-word arg shifts it.  E.g. `Take(b Box, tag int)` (Box has a `@[]int`
  field) reads `tag` as 0; `Hold(xs @[]int, n int)` reads `n` as 0.  Same
  failure mode as the (fixed) repl arg-coercion bug, but for params that are
  INHERENTLY multi-word: `coerceArg` cannot help — it only NARROWS a managed
  slice to a 2-word raw slice (which the ABI passes correctly), so a natively
  ≥3-word by-value arg still shifts.
- **Scope**: LLVM/native codegen ONLY — the bytecode **VM is correct** (the
  `-int` conformance modes pass `598`; the `-comp`-final modes fail).  So the
  fix is the LLVM iface-call emission / vtable-thunk arg ABI (`pkg/binate/
  codegen`, `emit_iface_call.bn` — it reconstructs the call signature from the
  arg instructions' LLVM types, which mis-passes a multi-word by-value param
  vs the impl method's actual ABI).
- **Test**: `conformance/598_iface_dispatch_multiword_arg` (struct-by-value +
  `@[]int` param each drop the trailing scalar — 4 instead of 9 / 5).  Xfail'd
  on `builder-comp`, `builder-comp-comp`, `builder-comp-comp-comp`; passes in
  the `-int` modes.  The cross/native comp conformance jobs (already red) may
  also show it.
- **Discovery**: 2026-06-04 exhaustive iface-dispatch coverage review (workflow)
  after the arg-coercion fix (`d6bb3b2f`) — the new coverage tests surfaced this
  deeper ABI bug.
- **Why MAJOR**: a silent dropped scalar arg in any *compiled* iface method
  with a multi-word by-value arg before a scalar.  Latent (no current caller
  has that exact shape), but a real miscompile.
- **Fix direction**: pass a multi-word by-value arg through the vtable thunk
  with the ABI the impl method expects (by-ref/sret-style or correct
  multi-register layout — match what the VM already does).  Once fixed, add the
  further dispatch coverage the review listed: iface-value arg (move vs RefInc),
  managed-slice RETURN through dispatch, iface-wrap/upcast args, and a multi-
  return-iface-dispatch deferral-lock (`.error`).

### ~~A `@[]@[]@T` (managed-slice-of-managed-slice) STRUCT FIELD emits a reference to an undefined nested cross-package element dtor~~ — FIXED + LANDED 2026-06-05 (binate `1cb4490c`, plan-cr-p2-2 step 6; `elemDtorName`/`elemCopyName` call ms/array element dtor/copy by their LOCAL weak_odr name; `607`). NOTE: the `MethodParamsFlat` `@[]@types.Type` workaround is NOT yet reverted — gated on a BUILDER bump (a bnc rebuilt from this fix accepts the natural nested encoding).
- **Symptom**: adding a struct field of type `@[]@[]@types.Type` to a struct in
  `pkg/binate/ir` made clang fail building `pkg__binate__ir.ll` with `use of
  undefined value '@bn_pkg__binate__types____dtor_ms_mp_pkg__binate__types__Type'`.
  The generated nested dtor `__dtor_ms_ms_mp_Type` (for the field) references the
  inner element dtor `__dtor_ms_mp_Type` qualified to the *element's* package
  (`pkg/binate/types`), but that inner dtor is never emitted/defined there.
- **Discovery**: 2026-06-04, building the interface-arg-coercion fix (`d6bb3b2f`)
  — `ModuleInterface` initially carried `MethodParams @[]@[]@types.Type`.  Worked
  around by switching to a flat encoding (`MethodParamsFlat @[]@types.Type` +
  `MethodParamCounts @[]int`), so the shape stays at `@[]@Type` (known-good, ==
  `MethodResults`).  `gen_dtor.bn` documents `ms_ms_mp` dtors as supported in the
  abstract, but the cross-package element-dtor emission for a *struct field* of
  that shape isn't wired up.
- **Why MAJOR / latent**: it's a silent undefined-symbol at link for a legal
  type shape; latent because nothing in the BUILDER tree currently needs a
  `@[]@[]@T` struct field (the flat workaround avoids it).  A non-flat use would
  hit it again.
- **Root cause**: unknown — needs investigation in the dtor-emission path
  (does the nested ms-of-ms dtor ensure its inner element dtor is emitted, and
  with the right package qualification, when the element type is cross-package?).
- **Fix direction**: ensure `__dtor_ms_mp_<Elem>` is emitted (in the element's
  package, or homed where referenced) whenever a `__dtor_ms_ms_mp_<Elem>` is
  generated.  Add a unit/conformance test with a `@[]@[]@T` struct field where T
  is a cross-package managed type.

### ~~Interface method dispatch drops args after a width-mismatched managed-slice arg (codegen)~~ — FIXED + LANDED 2026-06-04 (binate `d6bb3b2f`)
- **Fixed**: factored the per-arg coercion loop out of `genCall` into a shared
  `coerceArg` helper (used by `genCall` + `genMethodCall`); `genInterfaceMethodCall`
  now evaluates args via `genExprOrFuncRef(...paramTyp)` + `coerceArg` like the
  regular path.  Interface method param types are carried via
  `ModuleInterface.MethodParamsFlat` + `MethodParamCounts` (flat encoding —
  `@[]@[]@types.Type` as a struct field trips a missing nested cross-package
  element dtor in the BUILDER, tracked separately below), populated at the decl
  AND generic-instantiation sites; `findInterfaceMethod` returns the param list
  from the inheritance level that owns the method (so embedded methods coerce
  too).  Pinned by `conformance/593` (own + inherited + func-value arg;
  negative-verified 3/3/3 without the fix vs 700/3/700 with) and `e2e/repl.sh`
  (now 53/53; `basic-call` was the hang).  Full conformance 522/0 + unit 39/39.
  Adversarial-reviewed before implementing (C1 inherited / C2 whole coercion
  machinery / M2 generic site / M3 self-ref timing / V2 flat encoding).
  Follow-up: a dedicated generic-interface-method slice-arg regression test
  (the generic-site population is code-identical to the verified decl path).
- **Root cause (CONFIRMED)**: `genInterfaceMethodCall` (`pkg/binate/ir/gen_iface.bn:89-94`)
  builds its call args with a bare `genExpr` per arg — it **omits the argument
  coercions** the regular call path applies (`gen_call.bn:140-202`), notably the
  `@[]T → *[]T` managed→raw slice conversion (`EmitManagedToRaw`).  When an iface
  method param is a raw slice (`*[]readonly uint8`, 2 words) and the arg is a
  managed slice (`@[]uint8`, 4 words), the unconverted 4-word value is passed
  where 2 words are expected, **shifting every following argument** — the next
  scalar arg is read from the wrong slot.  General MAJOR codegen bug; latent in
  conformance (no iface method has a managed-slice→raw-slice param).  The other
  omitted coercions (string-lit→chars, nil→slice, by-value struct-copy RefInc,
  iface-value move/RefInc) are each their own latent iface-arg bug.
- **How it surfaces (repl)**: the host loop calls `s.Step(line, eof)` where
  `line` is `@[]uint8` and `Step(line *[]readonly uint8, eof bool)`; with the
  conversion missing, `eof` is read as garbage/false, so an EOF turn never
  returns `STEP_EOF_CLEAN`.  The loop spins forever printing `> ` (NOT a clean
  segfault — it exhausts and dies; CI's captured output shows `> 14` then the
  crash).  `b9ca1acc` (ReplSession→interface) exposed it by routing `Step`
  through iface dispatch; green through `16:47`, first red `16:52`.  Not from
  the stdlib / bnc-0.0.7 work.
- **Minimal repro**: an iface method `M(line *[]readonly uint8, b bool) Res`
  (struct return) called via the interface with a `@[]uint8` arg returns the
  `b=false` branch even when `b=true` is passed.  Controls: `(int,bool)→int`,
  `(int,bool)→struct`, and `(@[]uint8,bool)→struct` (matched width) all pass —
  isolating it to the width mismatch, not sret / multi-word args in general.
- **Fix (planned)**: add `MethodParams` to `ModuleInterface` (populate alongside
  `MethodResults` during registration); factor the per-arg coercion loop out of
  `gen_call.bn` into a shared helper and call it from `genInterfaceMethodCall`
  too, so both paths stay in sync.
- **Why MAJOR**: silent wrong-arg in iface dispatch (not just repl).  Also E2E is
  red on *every* main commit, masking new E2E regressions; and `bnc-0.0.7` ships
  a `bni` whose interactive REPL hangs (accepted — REPL is a Tier-1 PoC, not
  build-critical; fix to land in 0.0.8-pre).
- **Test**: `e2e/repl.sh` `basic-call` (covers it end-to-end) + a new unit/
  conformance test from the minimal repro above.

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

### Float-literal converter 1 ULP low for ~38+ sig-digit literals just above a tie (round-bit loss) — UNBLOCKED 2026-06-05 (BUILDER compiles `math/big`); proper fix actionable
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
  slow-path).  **No longer blocked**: the earlier "cmd/bnc's BUILDER tree can't
  import stdlib `big`" caveat is STALE — verified 2026-06-05 that the current
  BUILDER (`bnc-0.0.7`) compiles and runs a `pkg/std/math/big`-importing program
  correctly (`Nat.Mul` → 3000000). `math/big` is float-free integer big-num (no
  floats / generics / closures / interfaces), so it is BUILDER-compilable; only
  `strconv`-as-a-whole stays blocked (its `ftoa.bn` is float-using), and the fix
  needs `math/big` directly, not `strconv`. So the converter (in
  `pkg/binate/native/common`) can `import "pkg/std/math/big"` and do the exact
  mantInt*10^exp rounding. Remaining check before landing: confirm no tier/layer
  hygiene rule forbids the compiler tree depending on tier-1 stdlib (a layering
  question, not a BUILDER-compilability one). Interim alternative (no longer
  needed if the proper fix lands): widen the fixed window (256-bit → ~76 digits).
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

### Implement the strconv `Parse...` series (ParseInt / ParseUint / ParseBool / ParseFloat) — LANDED (complete)
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
- **Hex floats — DONE both directions**: `ParseFloat` reads `0x1.8p3`
  (`15b6ce90`, pure-binary path sharing the rational rounding core; Go
  differential ~2M) and `FormatFloat`/`AppendFloat` emit `'x'`/`'X'`
  (`e85eb129`, exact nibble rendering, no big.Nat; Go differential ~4M).
  `_` separators accepted in hex too.
- **No remaining strconv follow-up** for parse/format parity.  (The only Go
  float format not implemented is `'b'` — decimal mantissa, binary exponent —
  which nothing needs yet.)  Once stdlib is BUILDER-bundled, route the
  compiler's float-literal converter through `ParseFloat`'s core to retire the
  round-bit dtoa bug + the duplicate converter (tracked above).

### float32 const literal: VM/native loaded the float64 pattern (wrong value) — FIXED 2026-06-05 (binate, plan-cr-p2 Plan 4 step 1)
- **LLVM compile error — FIXED 2026-06-03 (binate `4fd196d0`)**: a float32-typed
  OP_CONST_FLOAT emitted a decimal `float` constant (`fadd float 0.0, 0.1`),
  which LLVM rejects unless exactly representable (`floating point constant
  invalid for type`).  Fixed in `pkg/binate/codegen/emit_instr.bn`: materialize
  the value as a `double` (decimal is valid there) and `fptrunc` to `float`.
- **VM/native value bug — FIXED**: a float32-typed OP_CONST_FLOAT now narrows
  through `common.F64BitsToF32Bits` (round-to-nearest-even f64→f32) in the VM
  (`vm/lower_instr.bn` OP_CONST_FLOAT arm) and both natives' `emitConstFloat`, so
  `bit_cast(int32, C)` observes the true float32 pattern (`0x3DCCCCCD` for `0.1`,
  not `0x9999999A`).
- **The "blocked on a new BUILDER release" diagnosis was WRONG**: the real blocker
  was that `F64BitsToF32Bits` was defined in `common_float.bn` but never declared
  in `common.bni`, so no importer could resolve it.  BUILDER recompiles
  `native/common` from current source when it builds `cmd/bnc`, so a new `.bni`
  export is honored with no BUILDER bump.  Exporting it unblocked the one-liner
  wire-ins.
- **Test**: `conformance/539_float32_const` — now passes on the C/LLVM **and** VM
  lanes (those xfails dropped).  Native lanes still xfail, but ONLY on the
  negative const: native leaves the high-bit-set `bit_cast(int32)` result
  zero-extended (`3184315597`) not sign-extended (`-1110651699`).  That residual
  is sub-word value correctness — folded into **plan-cr-p2-4 #4.1** (the float32
  narrowing itself is correct on native too: the four non-negative lines pass).
- **Discovery**: 2026-06-03 (fixing the LLVM compile error surfaced the value
  bug).  **Severity**: MAJOR (was a silent wrong float32 const on VM/native).

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

### ~~A managed-slice-of-interface-value (`@[]@I`) constructed via a slice LITERAL leaks its elements~~ — FIXED + LANDED 2026-06-05 (binate `fddf8676`, plan-cr-p2-2 step 6; root cause was the `__dtor_ms_unknown` name collision when a module has both `@[]@I` and `@[]@func` — dtorTypeSuffix now emits injective `iv`/`fv` suffixes; `606`)
- **Symptom**: `var s @[]@Foo = @[]@Foo{makeFoo(i)}` (a slice literal of interface values), dropped at scope exit, never RefDec's its `@Foo` elements — the receiver (and its managed fields) leak (rc 1→2, never back to 1).  The element-ASSIGN form (`var s @[]@Foo = make_slice(@Foo, n); s[0] = makeFoo(i)`) is balanced; only the literal leaks.
- **Root cause (from `--emit-llvm`)**: both forms call the slice's `__dtor_ms_unknown`, which RefDec's the slice backing with a NULL dtor and does not walk the interface-valued elements (no per-element iface dtor).  So the element-type isn't propagated into the managed-slice dtor selection for the literal shape.  This is the `@[]@I` feature area already flagged as incomplete by `440_iv_in_slice_mgd` ("compiles, but writes into the iv slot segfault").
- **Discovery**: 2026-06-03 adversarial coverage audit of the `@Iface` refcount lifecycle.  Likely **pre-existing** / part of the known-incomplete `@[]@I` support — NOT a regression in the core refcount wiring (the common copy-sites — return / var-init / assign / field / array-element / managed-slice-element-assign / composite / struct-copy / param / deref — are all rc-balanced, pinned by 553/554/556/560/567).
- **Status**: tracked, not fixed.  Lower priority (exotic shape in a known-incomplete feature); fix alongside the broader `@[]@I` completion (440).

### Bytecode VM `@Iface` (interface) value handling — two VM bugs — FIXED 2026-06-03
- **Part A — single interface-value return not copied back → "call through nil interface value"** (binate `511e1395`).  Interface values are 16-byte address-based VM stack slots.  `lowerReturn` set BC_RETURN's copy-back size only for `isMultiWordField` types (struct / slice / array) — it omitted interface values, so a single `@Iface` return dangled in the reclaimed callee frame and the next call clobbered it; `consume(makeFoo(i))` (an iv call result passed directly as an arg) then panicked `vm: call through nil interface value` in `-int` only (LLVM + native don't use this lowering).  Fix: set the copy-back size for `TYP_INTERFACE_VALUE` / `_MANAGED` single returns too.  Pinned by `560_iface_return_call_arg` (green all modes).
- **Part B — interface-value receiver dtor crashed on RefDec-to-zero** (binate `5de3d09d`, the direct analogue of the `@func` capture-record dtor `0a0d00af`).  `BC_IFACE_DTOR` produced the receiver dtor's 1-based func index, but `BC_REFDEC_INLINE_FAST` consumes its dtor input as a func-value HANDLE — so an interface value that was the *last* holder of a managed-field receiver bit_cast the small index to a pointer and crashed (520; the dtor arms of 554 / 556).  473 hid it because its iv lives in a nested block the receiver outlives, so its RefDec never reached zero.  Fix: `BC_IFACE_DTOR` hands `BC_REFDEC` the dtor func's handle via `ensureHandle` (the same `{Vtable, ClosureRec{VM_CLOSURE_REC, FnIdx}}` the `@func` path uses); the existing iterative-push arm runs the receiver dtor and frees it via `freeOnPop`.
- **Result**: `520_iface_dtor_callee_sole_ref` (a standing `-int` red) is green; `554_iface_refcount_balance` and `556_iface_struct_field_balance` un-xfailed in all VM modes; `-int` suite 478/0.  Both were `pkg/vm`-only (codegen always emitted correct IR; LLVM + native were already correct).

### Conformance int-int mode: `136_grouped_imports` + `383_cross_pkg_iface_dtor` fail with "pkg/builtins/rt not found" — FIXED+LANDED (binate `db18f26b`, 2026-06-05)
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

### ~~Managed-interface-value refcount lifecycle is unwired — FAMILY of leaks + 1 UAF~~ — FIXED + LANDED (core wired 2026-06-03; residual closed 2026-06-05 plan-cr-p2-2 steps 2+5: the iface-method-DISPATCH result leak — `genInterfaceMethodCall` registered nothing — via `registerManagedCallResult` (binate `f5410fcf`), and the per-arm `@Iface`/`@func` copy switches consolidated onto `emitStoreManagedSlot` (binate `ce2c8175`); b2 depth coverage `605`)
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

### Native aa64 miscompiles a cross-package multi-return whose component is a managed interface value (`@Iface`) — MAJOR, silent wrong-code (`526` xfailed)
- **Symptom**: `conformance/526_strconv_parse_cross_pkg` (added with the
  strconv `Parse*` series, `6a91cf5b`) crashes on
  `builder-comp_native_aa64-comp_native_aa64` — empty output.  The
  `Parse*` functions return `(T, @errors.Error)`; the cross-package
  multi-return of a managed-interface-value component is miscompiled:
  the returned `@Iface` comes back as **non-nil garbage** and the scalar
  component is **corrupted**, then the program crashes when the garbage
  `@Iface` is used.  Green on the default C/LLVM and VM modes.
- **Root cause (BISECTED 2026-06-04 with minimal native-aa64 repros)** —
  the break is exactly *cross-package* + *multi-return* + *managed-
  interface-value component*:
  - same-package `(int64, @errors.Error)` multi-return → **passes**
  - cross-package *single* `@errors.Error` return (`errors.New`) → **passes**
  - cross-package `(int, int)` multi-return → **passes**
  - cross-package `(int, @errors.Error)` multi-return → **FAILS**
    (returned `@Iface` non-nil, scalar corrupted)
  Minimal repro: a helper pkg `func Maybe(x int) (int, @errors.Error)`
  returning `x, <nil>`, with `main` doing `n, err = helper.Maybe(7)` — on
  native aa64 `present(err)` reads true (should be false) and `n` is
  wrong.  The importer mis-sizes the `@Iface` tuple component (resolves
  it to a managed pointer / wrong word-count within the return tuple), so
  the caller's sret layout disagrees with the callee's — the native-aa64
  analogue of the LLVM ABI mismatch fixed in `cb8c0f1a` (line ~434), but
  in the MULTI-RETURN-tuple case (the single-`@Iface` case is already
  correct on native aa64, hence `errors.New` passes).
- **Status**: `526` xfailed on native aa64 (binate `49d03616`) + this
  TODO.  **MAJOR (silent wrong-code) — NOT a
  workaround; needs a real fix to the native-aa64 importer's tuple-
  component type resolution for `@Iface` returns.**  Discovery: 2026-06-04
  full native-aa64 `--check-xpass` lane (first correct end-to-end run; the
  flag had been mis-positioned after the mode).  Not caused by the `550`
  work.

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

### Cross-package managed-PTR extern var: value-copy (559) + field-write (561) — BOTH RESOLVED 2026-06-04 (native-aa64 stale xfails removed `c4036777`)
- **Resolution (2026-06-04)**: with the native aa64 lane now building
  (after the `551`/`573` `&G`-rvalue fix `9a0f4f9a`), a per-mode
  `--check-xpass` sweep showed **`559` XPASSes on every execution path**
  (LLVM, VM, self-host gen2/gen3, native aa64) and **`561` XPASSes on
  native aa64**.  Both were stale:
  - `559`'s cross-package value-copy crash (the importer lacking the
    imported type's dtor for the scope-end RefDec) was closed by recent
    main work.  `559` is now the ORIGINAL aliasing test — green on ALL 6
    default modes + native aa64, no xfail.  The refcount-BALANCE check
    (which needs an `rt` import, tripping the int-int loader bug) was
    split out into a new directory test `586_cross_pkg_managed_ptr_copy_balance`,
    xfailed only in `builder-comp-int-int` (`66aef4c1`).  (Interim
    history: `32bee84c` strengthened `559` in place + carried an int-int
    xfail; `c4036777` dropped the stale native-aa64 xfails; `66aef4c1`
    then split aliasing vs balance so `559` is xfail-free again.)
  - `561` was already RESOLVED on the default modes 2026-06-03
    (`733d4485`, below); only its native-aa64 xfail lingered, because
    that lane didn't build until `9a0f4f9a`.
  The native-aa64 xfails for BOTH `559` and `561` removed in `c4036777`
  (the strengthened `559` test XPASSes on native aa64).  `559`'s
  `builder-comp-int-int` xfail intentionally remains (rt loader bug).
  (My earlier combined removal attempt `20d7a59d` was abandoned — it
  collided with `32bee84c`'s better, concurrent 559 handling.)  Surfaced
  while landing `550`; not caused by it (559/561 use no closures).
- **~~Symptom A (value-copy crash, 559)~~ — RESOLVED 2026-06-04**: the
  crash (importer lacking the imported type's dtor for the scope-end
  RefDec) was closed by recent main work; see the Resolution note above.
  Tests: `conformance/559_cross_pkg_managed_ptr_copy` (aliasing — green on
  all 6 default modes + native aa64) and
  `conformance/586_cross_pkg_managed_ptr_copy_balance` (refcount balance —
  rc 1->2 on copy, ->1 at the scope-end RefDec; xfailed in
  `builder-comp-int-int` for the orthogonal rt-loader bug).
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
  (green all 6 default modes + native aa64 — the stale native-aa64 xfail
  was removed in `c4036777`).  Unit: `TestGetSelectorTypeQualifiedImportedVar`.
- **Discovery**: 2026-06-03, deferral-2 Slice 4 + coverage review.

### Cross-package managed refcount-safety + extern-var coverage gaps (2026-06-04 audit)
- A coverage audit (multi-agent workflow) of cross-package extern-var
  and managed-ptr/value test coverage — run after the 551/559/561
  deferrals + 586 — found that most cross-package MANAGED scenarios are
  tested only FUNCTIONALLY (output is right), not for REFCOUNT BALANCE,
  so a leak (rc stays elevated) or an extra RefInc/RefDec would slip
  through.  17 gaps confirmed (adversarially verified vs existing tests).
- **Addressed**: managed-slice extern-var value-copy rc-balance is now
  `conformance/592_cross_pkg_managed_slice_copy_balance` (the 586
  companion; balanced in 5 default modes + native aa64, int-int xfailed
  for the rt-loader bug; binate `efe989e6`).  (Landed as 592 — 587/588
  then 589/590/591 were taken by concurrent landings as the number kept
  moving.)
- **Remaining rc-balance gaps** (functional coverage exists; no
  `rt.Refcount` before/after — add it, pattern: 586/592/130) — a managed
  value crossing a package boundary as:
  - a managed-slice ELEMENT assignment of a managed value
    (`pkg.S[i] = @v`; also exercises RefDec of the overwritten element);
  - a function ARGUMENT (`pkg.f(@T)`) / RETURN (`pkg.New() @T`);
  - a STRUCT FIELD store (`root.X = child`, X a cross-pkg `@Node`);
  - an INTERFACE construction (`var iv @pkg.I = h`) / interface RETURN
    (`pkg.Make() @Shape`);
  - a GENERIC type argument (`genlib.Append[@pkg.T](...)`).
  These are pre-existing and NOT extern-var-specific — a broader
  cross-package-managed refcount-safety test initiative.
- **Extern-var FUNCTIONAL gaps** (the paths work; just untested):
  `&pkg.X` (address-of an imported SCALAR var — the 551 analogue for
  imports); field write through an imported RAW-ptr / value-STRUCT var
  (the 561 analogue); raw-slice element write through a `*[]T` extern var.
- **Blocked**: 586/592's `builder-comp-int-int` xfails clear once the
  136/383 int-int rt-loader bug (above) is fixed.
- **Discovery**: 2026-06-04 coverage-audit workflow.

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

### Checker does not fold `iota` in expressions — bit-flag const COMPILE-TIME values stay plain-iota (MINOR follow-up)
- **Symptom**: iota-repeat (binate `52a9eabf`) gives correct RUNTIME values for bit-flag consts (`const ( B0 int = 1 << iota; B1; B2 )` -> 1,2,4 at runtime). But `checkIdent` returns a plain `TYP_UNTYPED_INT` for `iota` (no `HasLitVal`), so the checker never folds an iota expression to a value: a bare member is given the plain-iota value via `makeUntypedIntWithLit(c.Iota)`; an explicit `1 << iota` member gets no value. So a bit-flag const's COMPILE-TIME value (array dimensions, assignability/overflow checks) is wrong/absent -- e.g. `var x uint8 = B10` with `B10 = 1 << 10 = 1024` is wrongly accepted because the checker thinks `B10 = 10`.
- **Scope**: compile-time only; runtime values are correct (IR-gen). The dominant `= iota` enum idiom is unaffected (plain-iota == iota-repeat there). Affects only bit-flag-style consts used as array dims or in narrow-type checks -- rare.
- **Fix sketch**: fold `iota` in `checkExpr` (return `makeUntypedIntWithLit(c.Iota)` from `checkIdent`), and have `checkGroupDecl` re-check a bare member's repeated previous expression with the current iota so its symbol value matches IR-gen. Watch for new overflow errors on large iota enums assigned to narrow types.
- **Discovery**: 2026-06-05, while implementing iota-repeat (Plan 1 / 1.3d).

### Untyped single const (`const X = 5`) is not forward-referenceable — FIXED+LANDED (binate `99057185`, 2026-06-05)
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

---

## TEST COVERAGE — conformance matrix follow-ups

The code-red conformance-matrix family (`conformance/matrix/`, see
`plan-code-red.md` §7) has four members realized: `refcount` (Class 1),
`scalar` (Class 5), `abi` (Class 4), `const` (named-constant invariant). These
are the remaining matrix-shaped classes not yet built as their own matrix —
candidates for after the loose-axis finish (const-expr folding + ABI
`handle`/`__c_call` shapes).

### (b1) Class 2 matrix — VM 16-byte address-aggregate (iface / func value) handling — ✅ REALIZED 2026-06-05 (binate `12d6782f`)
- **Realized**: `conformance/matrix/addr-aggregate` (generator
  `gen-addr-aggregate-matrix.py`). Axes `kind (@func / @Iface) × operation
  (direct / copy / return / arg / return-arg / field / array-elem)`; assertion:
  both words of the 16-byte value survive the boundary, observed by invoking it
  (→ 42); a dropped/swapped word faults or returns wrong. 14 cells.
- **Result**: all 14 green on `comp` (LLVM), `int` (VM), and x64-native — the
  Class-2 fixes that landed in P2 (the VM func-value nil-vtable `e337e413`, the
  2-word-slice-len-drop) hold across the grid; this is regression coverage, no
  new defects. aa64-native is collateral-red on the self-hosting `BNC_NATIVE`
  miscompile (separate CRITICAL), not these cells.
- **Note**: the `field`/`array-elem` cells store an already-typed value (a bare
  func literal in those positions trips the separate filed bare-func-literal
  flavour-inference MINOR, not 2-word survival).

### (b2) Lifecycle matrix — Class 6 (`@Iface` / `@[]@I`) + Class 7 (captured-`@func` over-release) — PARTLY ADDRESSED 2026-06-05 (plan-cr-p2-2 step 5)
- **Status**: the existing `conformance/matrix/refcount` form × type grid already
  covers Class 6's construction/consumption shapes (the copy-sites are now uniform
  after the `emitStoreManagedSlot` consolidation), and `604`/`605` add lifecycle-
  DEPTH balance (a value chained through param/store/pass/return/bind/invoke) for
  captured-`@func` and cast-from-impl `@Iface`, green in builder-comp/-int/-comp/
  native-aa64. REMAINING: a true single-program **Class 7 native↔VM trampoline**
  balance test is not expressible in the single-mode conformance harness (each
  test runs in one mode) — needs a cross-mode harness; left as a follow-up.
- **Why a matrix**: Class 6 (`@Iface`/`@[]@I` first-class lifecycle) and Class 7
  (native call-a-captured-`@func` over-release via the VM trampoline) are
  lifecycle-completeness classes. Axes would be `managed-kind (@Iface / @[]@I /
  captured-@func) × construction (make / literal / cast-from-impl / capture) ×
  consumption (call-method / index / range / pass / return / discard) ×
  backend`, with a refcount-balance assertion (mortal source).
- **Status**: the refcount matrix already covers `@Iface`/`@func` as value-types
  across assignment-forms, so this would EXTEND rather than start fresh — the
  new axis is construction × consumption depth (esp. the native↔VM trampoline
  path for Class 7, which the refcount matrix does not exercise).
- **Note**: several `@Iface` lifecycle bugs are already filed (leaks/UAF family,
  `@[]@I` literal element leak); a matrix would close the long tail.

### (b3) Class 3 / Class 8 — point-bugs, NOT matrices
- Class 3 (cross-package / interface-name type-resolution ordering → `i8*`
  fallback) and Class 8 (multi-package loader resolution at int-int depth) are
  one-off ordering/loader bugs, not systematic products. Track them as
  individual regression tests under `conformance/regressions/` + filed bugs, not
  as a matrix.
