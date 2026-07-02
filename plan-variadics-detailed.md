# Detailed Plan: Variadic Functions and Spread

**Status:** detailed implementation plan (2026-07-02), expanded from
[plan-variadics.md](plan-variadics.md) (the high-level roadmap) and grounded in a
repo-wide codebase survey. The **design is settled and specified** — spec §10.3
(`func.call.apply`, `func.variadic.decl/identity/pack/spread/borrow`), grammar
`binate.ebnf` (`VariadicParam`, `FuncTypeParams`, `ArgumentList`, D12), and the
DECIDED notes in `claude-notes.md`. This document does **not** re-litigate design;
it enumerates the concrete edit sites, the load-bearing decisions, the ordered
phases (each green), the risk/invariant checklist, and the test matrix.

All `pkg/…` / `conformance/…` / `scripts/…` paths are relative to the `binate`
repo. Spec/grammar paths (`docs/spec/…`) live in the sibling `docs` repo and are
committed separately.

---

## 0. Load-bearing decisions (made here; challenge in review)

These are implementation choices the survey surfaced. Each is defensible; the
adversarial review should attack them and the user owns any that stay contentious.

### D-A. Variadic-ness lives as `IsVariadic bool` on the **last** `types.Param`

**Decision:** add `IsVariadic bool` to `types.Param` (`pkg/binate/types.bni:245`),
set **only on the final param** of a variadic signature. **Not** a flag on the
func `Type`.

**Rationale:** a per-`Param` flag rides through the two rebuild paths that a
FuncType-level flag would silently drop:
- **Receiver drop** for method values (`check_expr_access.bn:337` builds
  `paramsNoRecv` by dropping `Params[0]`) — the flag stays on the last param
  after reindex.
- **Per-param substitution** for generics (`check_generic.bn:74` rebuilds each
  `Param` via `substituteTypeParams`) — copy the flag per-param and it survives.

A FuncType-level flag would need explicit propagation at ~8 construction sites
(`MakeFuncType` / `MakeFuncValueType` / `MakeManagedFuncValueType` and every
caller — `instantiateGenericFunc`, `defaultType`, the method-value builder, …),
each an easy-to-miss drop. **Cost of D-A:** identity/equality comparators must
read `params[len-1].IsVariadic` (a helper `funcIsVariadic(t @Type) bool` keeps
this uniform and single-sourced).

### D-B. AST stores the **element type** + a variadic flag; the checker derives `*[]T`

**Decision:**
- `ast.ParamDecl` (`ast.bni:419`) gains `Variadic bool`; for `name ...T` the
  parser stores `Type = T` (the element type, exactly as written) and
  `Variadic = true`. It does **not** synthesize `*[]T`.
- The checker's `resolveFuncDeclType` derives the **body type** `*[]T =
  MakeSliceType(T)` (or `MakeSliceType(MakeReadonlyType(T))` for `...readonly T`)
  and sets `Param.IsVariadic`.

**Rationale:** keeps the AST faithful to source (no derived types baked into the
parse tree) and centralizes the one `*[]T` derivation in the checker, where the
element-`readonly` lattice and slice construction already live. Every `.Params`
reader then sees a normal `*[]T` body type with an orthogonal variadic marker.

### D-C. Call-site spread recorded as `Expr.Spread bool`; pack shape re-derived in IR

**Decision:** `ast.Expr` (`ast.bni:148`, the `EXPR_CALL` node) gains `Spread
bool` (true iff the final arg was written `expr...`). No fixed/variadic split
count is stored on the call node.

**Rationale:** IR-gen already has the callee signature at the call site, so it
re-derives the fixed-arg count as `len(params) - 1` and the pack-vs-spread choice
from `Spread`. This mirrors the existing single-purpose `CFixedArgs int` field
(used only by `__c_call`) and avoids duplicating checker state the IR can
recompute. (Binate has no overloading, so the callee resolution the checker did
is reproducible in IR.)

### D-D. Func-value-type variadic marker: `TypeExpr.VariadicParams bool`

**Decision:** `ast.TypeExpr` (`ast.bni:353`, `TEXPR_FUNC_VALUE` /
`TEXPR_MANAGED_FUNC_VALUE`) gains `VariadicParams bool`, meaning "the last
`ParamTypes` entry is the variadic element type." `resolveFuncValueType`
(`resolve_type.bn:136`) reads it and builds a variadic func-value `Type` (last
`Param.IsVariadic = true`, body type `*[]T`).

**Rationale:** `ParamTypes` is a flat `@[]@TypeExpr` with no per-entry struct, so
a single bool on the `TypeExpr` is the only place to hang it. Convention
(last-entry-is-variadic) matches D-A's last-`Param` convention.

### D-E. IR carries `FuncSig.IsVariadic` + `ModuleInterface` per-method flag

**Decision:** `ir.FuncSig` (`ir.bni:30`) gains `IsVariadic bool`, populated at
every FuncSig-build site from `params[len-1].IsVariadic`. `ModuleInterface` gains
a parallel per-method variadic marker (see §4) so vtable-dispatched calls know
the final param is variadic.

**Rationale:** `ir.FuncSig` stores `Params @[]@types.Type` (drops the `Param`
wrapper), so the `types.Param.IsVariadic` marker is **lost** when a FuncSig is
built — IR needs its own copy. Func-value calls instead read the live
`fnTyp.Params[last].IsVariadic` off the `types.Type` (still a `@[]@Param`), so
only the named-func and interface paths need duplicated markers.

### D-F. Proposed diagnostic strings (pinned once `.error` tests match them)

`.error` conformance files match each line as a `grep -E` regex, so these become
a de-facto contract. Proposed (review/user may refine):

| Condition | Where | Message |
|---|---|---|
| `...` on a non-final parameter | parser | `variadic parameter must be last` |
| more than one variadic parameter | parser | `at most one variadic parameter is allowed` |
| variadic receiver (`func (r ...T)`) | parser | `receiver may not be variadic` |
| spread `...` not on the final argument | parser | `spread argument must be last` |
| spread mixed w/ individual variadic args | checker | `cannot combine spread with individual arguments` |
| spread into a non-variadic callee | checker | `cannot spread into non-variadic function` |
| spread onto `print`/`println`/`panic` | checker | `cannot spread into <name>` |
| wrong element type in pack | checker | reuse `errCannotAssign`: `cannot assign X to Y` |
| `f(s...)` with unfilled fixed params | checker | reuse `wrong number of arguments` |

Where the spec's `func.call.apply` wording already fits (`wrong number of
arguments`, `cannot assign`), reuse it; coin new strings only for the spread/param
constraints the spec describes but does not pin verbatim.

---

## 1. Representation summary (what each layer stores)

| Layer | Field | Meaning |
|---|---|---|
| AST param | `ast.ParamDecl.Variadic bool` (`ast.bni:419`) | `name ...T`: `.Type = T`, `.Variadic = true` |
| AST func-value type | `ast.TypeExpr.VariadicParams bool` (`ast.bni:353`) | last `ParamTypes` entry is variadic element type |
| AST call | `ast.Expr.Spread bool` (`ast.bni:148`, `EXPR_CALL`) | final arg written `expr...` |
| types | `types.Param.IsVariadic bool` (`types.bni:245`), set on last param; body `Type = *[]T` | signature identity + binding |
| IR sig | `ir.FuncSig.IsVariadic bool` (`ir.bni:30`) | named-call pack/spread dispatch |
| IR iface | `ModuleInterface` per-method variadic marker (§4) | vtable-dispatch pack/spread |
| IR param | `ir.Param.Typ = *[]T` (raw) | callee borrow — no acquire/release |

**Invariant:** a variadic param's resolved/body/IR type is **always raw `*[]T`**,
never managed `@[]T`. This is what makes the callee-borrow discipline automatic
(raw slices hit no managed arm in the entry-RefInc / exit-RefDec loops).

---

## 2. Phase 0 — Spec-coverage prep (unblocks new rule-ID citations)

The docs spec §10.3 already dropped `func.variadic.absent` and added the six new
rules, and `docs/spec/rule-ids.txt` reflects that — but the binate repo's
**vendored** `scripts/spec-coverage/rule-ids.txt` is **stale** (still lists
`func.variadic.absent`, lacks the six). spec-coverage passes today only because
the vendored inventory **and** three citing tests are consistently stale.

**Edits (one commit, all coupled):**
1. Re-vendor: `python3 docs/scripts/extract-rule-ids.py` (in the docs repo) then
   copy `docs/spec/rule-ids.txt` → `scripts/spec-coverage/rule-ids.txt`. Result:
   drops `func.variadic.absent`, adds `func.call.apply`, `func.variadic.decl`,
   `func.variadic.identity`, `func.variadic.pack`, `func.variadic.spread`,
   `func.variadic.borrow`.
2. Re-cite the three now-dangling `.rules` sidecars (in
   `conformance/spec/10-functions/`):
   - `023_variadic_absent_exact_args.rules` → `func.call.apply` (and update the
     `.bn` comment, which currently asserts the now-false "Binate has NO variadic
     parameters"; keep it as a fixed-arity positive test, or repurpose — see
     open item O-1).
   - `030_err_too_many_args.rules` → `func.call.apply`.
   - `032_err_zero_param_arity.rules` → `func.call.apply`.

**Green invariant:** re-vendor **and** citation fixes land together, or hygiene
goes red (3 DANGLING if only re-vendored; DANGLING against stale inventory if
only re-cited). After this, Phases 2–6 may cite the new rule-IDs from
`conformance/spec/10-functions/`. **Do not** flip the §10.3 "Draft; not yet
implemented" note yet — that waits until implementation-conformance is actually
met (Phase 7).

---

## 3. Phase 1 — Type-system identity foundation (inert)

Front-loads the highest-risk sweep — the FuncType identity edits — while it is
still a **no-op** (nothing sets `IsVariadic = true` until Phase 2), so a mistake
here can't miscompile anything yet and unit tests can pin the invariant directly.

**Edits:**
1. `types.bni:245` — add `IsVariadic bool` to `Param`.
2. `pkg/binate/types/types_query.bn` — add helper `funcIsVariadic(t @Type) bool`
   = `len(t.Params) > 0 && t.Params[len-1].IsVariadic`. Single-source for all
   comparators.
3. **Every FuncType identity/equality/assignability comparator** must compare
   variadic-ness (enumerate repo-wide — see the sweep note below — do not trust
   this list as complete):
   - `Identical` func/func-value branch (`types_query.bn:409-436`) — the core
     gate; after the per-param/result loops, require
     `funcIsVariadic(a) == funcIsVariadic(b)`.
   - `funcSignaturesMatch` (`types_query.bn:281-296`) — func-ref→func-value and
     `@func`→`*func` assignability.
   - `methodSigSatisfies` (`check_impl.bn:165-186`) — `iface.impl.coverage`;
     bespoke, does **not** route through `Identical`.
   - `checkBniSignatureMatch` (`check_decl_func.bn:35-102`) — `.bni`↔`.bn`;
     bespoke, does **not** route through `Identical`.
   - Verify transitive coverage: `appendIfaceMethodWithConflictCheck`
     (`check_iface_extends.bn:158-176`) and `checkBniVarMatch`
     (`check_decl.bn:422-441`) both call `.Identical`, so they are covered once
     `Identical` is — **confirm** by reading, do not assume.
4. `sig_string.bn:26-44` (`SigString`) — encode `...` before the last param's
   type when `funcIsVariadic`, so reflect Sig-equality distinguishes variadic
   from fixed-`*[]T`.
5. `ir.bni:30` — add `IsVariadic bool` to `ir.FuncSig`; populate `false` at every
   build site now (`gen_func.bn`, `gen_func_lit.bn`, `gen_import.bn`,
   `gen_generic.bn`, `gen_init.bn`) so Phase 2 only flips the value.
6. `ModuleInterface` — add the per-method variadic marker (§4), populated `false`.

**Sweep discipline (critical):** enumerate the comparator sites with a repo-wide
grep for func-signature comparison patterns (e.g. `numArgs != `, `\.Params\[`,
`Identical`, `funcSignaturesMatch`, `SizeOf`-independent per-param loops), **not**
from this list. Missing one comparator makes a variadic and a fixed-`*[]T` func
interchangeable — the single most dangerous soundness gap.

**Tests:** `types` unit tests that hand-construct a variadic vs a fixed-`*[]T`
func `Type` and assert `Identical` returns **false**, `funcSignaturesMatch`
returns false, and `SigString` differs.

**Green invariant:** no behavior change (all flags false). Existing suites pass;
new unit tests pass.

---

## 4. Phase 2 — Parser + AST + declaration resolve

Adds the three syntactic forms and makes a variadic declaration resolve to a
`*[]T` body. After this phase a variadic function is **declarable** (body sees
`*[]T`) but **not yet callable** (the old arity check still rejects every call) —
which is a legitimately green intermediate: no existing test calls a variadic
function, and we add only a *declare-only* test here.

**AST (`ast.bni`):**
- `ParamDecl.Variadic bool` (D-B), `TypeExpr.VariadicParams bool` (D-D),
  `Expr.Spread bool` (D-C). Update the AST doc-comment tables (CALL row ~128-147,
  FUNC row ~216-243, TypeExpr rows ~340-352) to document the new fields.

**Parser:**
- `parseParamDecl` (`parse_decl.bn:199-207`) — after the name IDENT, before
  `parseType`: `if p.tok.Typ == token.ELLIPSIS { p.next(); pd.Variadic = true }`;
  then `parseType` parses element `T`. **No lookahead needed** (name already
  consumed).
- `parseParamList` (`parse_decl.bn:186-196`) — enforce **at-most-one** and
  **last-only** (error otherwise). This covers `parseFuncDecl`, `parseFuncLit`,
  and `parseInterfaceMethod` (all route params through here) in one edit.
- **Receiver:** `parseFuncDecl` (`parse_func.bn:23`) calls `parseParamDecl`
  **directly** for the receiver, bypassing `parseParamList` — so it must
  **explicitly reject** `pd.Variadic` on the receiver (D-F: `receiver may not be
  variadic`). Easy-to-miss site.
- `parseFuncTypeBody` (`parse_type.bn:248-276`) — in the `ParamTypes` comma loop,
  detect a leading `ELLIPSIS` before the type on the **last** param; consume it,
  set `te.VariadicParams = true`, enforce last-only. Handle the sole-param
  `...T` form (`FuncTypeParams = "..." Type`).
- `parseCallExpr` (`parse_expr.bn:494-513`) — recognize a trailing `expr...` on
  the **final** arg and set `e.Spread`. Do this in `parseCallExpr` (it owns the
  `)` boundary), **not** in the shared `parseExprList` (`parse_stmt.bn:333`),
  which is reused by `return`/short-var/assign — adding spread there would wrongly
  accept `return a...`. Enforce spread-is-final (next token must be `)`; no
  trailing comma / further args → D-F: `spread argument must be last`).
- **Do not disturb** the three existing `ELLIPSIS` roles: `__c_call` boundary
  (`parseCCall`, `parse_builtin.bn:127-145`, `CFixedArgs`) and `[...]T` inferred
  array length (`parse_type.bn:157-171`, `parse_primary.bn:372-380`). Adjacency
  (D12) keeps them separate; leave them byte-for-byte intact. Do **not** model
  spread as a unary `ELLIPSIS` operator (it would leak into every `Expr`
  consumer) — it is a bool on the call node.

**Checker resolve:**
- `resolveFuncDeclType` (`resolve_type.bn:233-274`) — when a param is variadic:
  resolve element `T`, set the param body `Type = MakeSliceType(T)`
  (`MakeSliceType(MakeReadonlyType(T))` for `...readonly T`), set
  `Param.IsVariadic = true`. Re-assert at-most-one/last-only (defense in depth vs
  the parser).
- `resolveFuncValueType` (`resolve_type.bn:136-154`) — read `te.VariadicParams`,
  build the last param as `*[]T` with `IsVariadic = true`.
- `checkFuncDecl` body binding (`check_decl_func.bn:364`) — no change: the param
  `Type` is already `*[]T`, so `defineVar(name, *[]T)` needs no special-casing.
- `collectMethodDecl` / `prependRecvParam` (`check_decl_func.bn:120,286`) — verify
  the variadic flag on the last (non-receiver) param survives the receiver
  prepend (it does: receiver → index 0, variadic stays last).

**Tests:**
- Parser unit tests: positive parses of `name ...T`, `*func(...T)`,
  `@func(...T)`, `f(a, s...)`; negative parse errors for non-final variadic param,
  two variadic params, variadic receiver, spread-not-last.
- A declare-only conformance test: `func f(xs ...int) int { return len(xs) }`
  (never called) — proves resolve produces a `*[]int` body and `len(xs)` works.

**Green invariant:** variadic functions declarable; calls still rejected by the
unchanged arity check (no call tests yet). Existing suites pass.

---

## 5. Phase 3 — Direct call, individual-arg pack (non-managed element)

**Checker-accepts and IR-lowers MUST land in the same commit** — a checker that
accepts `f(1,2)` with an IR that can't lower it crashes.

**Checker (`check_expr.bn:488-549`, `checkCallExpr`):**
- Remove/repurpose the dead `isVariadic` local (`:490`, assigned never read).
- When the callee's last param is variadic (`funcIsVariadic`): bind the leading
  `k = len(params)-1` args to the fixed params (existing per-arg
  `AssignableTo`); then require `numArgs >= k` and each trailing arg (index
  `k..numArgs`) `AssignableTo` the **element** type `T`. Zero trailing args is
  valid (empty variadic). Adjust the `numArgs != numParams` count error to the
  variadic-aware rule.
- (Spread arm and the other three call sites come in Phases 4/6.)

**IR (`gen_call.bn:259-266`, `genCall` arg loop) — factor into a shared helper:**
- Add `emitVariadicTail(ctx, b, elemTyp, trailingArgExprs) @Instr` (new
  `gen_variadic.bn` — `gen_call.bn` is already ~414 lines, near the soft cap, so
  split along this natural boundary rather than inlining). For the **pack** case:
  1. `EmitAlloc(MakeArrayType(elemTyp, n))` → stack `[N]T` backing (zero heap).
  2. Per element: `EmitGetElemPtr(arrPtr, i, elemTyp)` + `genExprOrFuncRef` +
     **coerce against `elemTyp`** (call `coerceArg` with `paramTyp = elemTyp`,
     **not** the nil-param no-op the trailing-arg path currently hits) +
     `EmitStore`.
  3. Build the `{data, len}` header via the `emitMakeRawSlice` idiom
     (`gen_print.bn:246`): `EmitAlloc(sliceTyp)` + field-0 = `bitcast(arrPtr)` +
     field-1 = `n` + `EmitLoad`. **`n == 0` → `EmitConstNil(sliceTyp) = {null,
     0}`** (emptiness is `len == 0`, never a nil compare).
  This is exactly the `genRawSliceLit` template (`gen_composite.bn:254-303`) minus
  managed-element RefInc (Phase 5).
- `genCall` passes the fixed leading args by the existing per-index loop, then
  appends the single `*[]T` from `emitVariadicTail` as the last arg to
  `EmitCall`.
- Populate `ir.FuncSig.IsVariadic` at the `gen_func.bn` build site so `genCall`
  can detect the variadic callee alongside `lookupFuncParams`.

**Callee side — no new code:** `gen_func.bn:155-176` entry-RefInc and
`gen_util_refcount.bn:437-508` exit-RefDec both skip raw slices, so a `*[]T`
variadic param is neither acquired nor released. Confirm the param stack-slot
store (`gen_func.bn:117-140`) handles a 2-word raw slice (it already does for any
`*[]T` param).

**Tests (conformance/spec/10-functions, `.rules` → `func.variadic.pack`):**
- positive: `f(1,2,3)` sums/collects; `f()` empty (`len == 0`); mixed fixed +
  variadic `f(a, 1, 2)`.
- negative: wrong element type (`func f(xs ...int); f("x")`) → `cannot assign`.
- Run under `builder-comp`, `builder-comp-int`, `builder-comp-comp`,
  `builder-comp-comp-int` (the VM inherits the IR-driven pack; if an int mode
  fails, an `.xfail.<mode>` + tracked TODO, never a silent skip).

**Green invariant:** direct individual-arg variadic calls work end-to-end;
existing suites pass.

---

## 6. Phase 4 — Spread (`expr...`)

**Checker (`checkCallExpr` spread arm):**
- When `e.Spread`: the callee must be variadic (else D-F `cannot spread into
  non-variadic function`), and the callee must **not** be `print`/`println`/`panic`
  (detected by name via `isVariadicBuiltinCall`/`isPanicCall`; D-F `cannot spread
  into <name>` — the empty-param bypass branch at `:502` would otherwise silently
  accept `expr...`). Then: bind the leading `k` args to fixed params; require
  exactly `k+1` args (the last being the spread operand; `f(s...)` with unfilled
  fixed params → `wrong number of arguments`); the spread operand must be a
  **slice assignable to `*[]T`** — check via `spreadType.AssignableTo(c,
  MakeSliceType(elemT))`, which gives the `@[]T`→`*[]T` decay
  (`types_assignable.bn:146`) **and** the element-`readonly` lattice
  (`dropsConst`, `types_const.bn:70`) **for free**. Do **not** hand-roll element
  comparison (misses the readonly lattice).
- Exclusivity (`f(a, s...)` OK, `f(a, b, s...)` where b is variadic-individual is
  an error) and spread-not-last are already parser-enforced; the checker
  additionally rejects a non-slice operand (array must be sub-sliced first:
  `arr[:]...`; a string literal is `[N]readonly char`, spread as `lit[:]...`).

**IR (`emitVariadicTail` spread case):**
- Evaluate the spread operand; if `@[]T`, decay via `EmitManagedToRaw`
  (`ir_ops_flow.bn:266`, extracts `{data,len}`); if already `*[]T`, forward
  directly. **No copy, no alloc.** `len == 0` operand → same empty variadic
  argument as zero individual args. Pass the resulting `*[]T` as the single
  trailing arg.

**Tests (`.rules` → `func.variadic.spread`):**
- positive: spread `@[]T`; spread `*[]T`; spread `arr[:]...`; spread
  `stringLit[:]...`; spread `len == 0` slice (empty); spread after fixed args
  `f(a, s...)`.
- negative: spread into non-variadic callee; spread onto `print`/`println`/`panic`;
  `f(a, s...)` mixed with an individual variadic arg (parser or checker); spread of
  a bare array (not sub-sliced); `f(s...)` with unfilled fixed params.

**Green invariant:** spread works end-to-end for direct calls; existing suites
pass.

---

## 7. Phase 5 — Managed-element variadics (`...@T`)

The **highest-risk** phase: the refcount discipline is **inverted** vs a fixed
managed param (caller acquires as statement temps; callee borrows).

**IR (`emitVariadicTail` pack case, managed `elemTyp`):**
- As each trailing `@T` element is stored into the `[N]@T` backing array,
  **acquire it** (RefInc, per `mem.copy`/Axiom 3) — reuse `coerceArg`'s managed
  copy/iface-move handling against `elemTyp`.
- **Register the backing array as a statement temp** via `registerTemp` so
  `emitTempCleanup` RefDecs the elements at **statement end** (not call return).
  Model on `emitTempCleanupBody`'s `isStructOrArrayAlloc(tmp) &&
  needsStructCopy(tmp.TypeArg)` arm (array-of-managed dtor'd in place) — verify
  that arm fires for a `[N]@T` alloca (its `TypeArg` is `[N]@T`, so
  `needsStructCopy` must be true). Prefer registering the **whole array** (one
  registration) over N per-element registrations, if the arm fires correctly.
- The temp array is **not** a managed-slice backing (no `{refcount, free_fn}`
  header), so it must **not** be released via `emitManagedSliceRefDec`.
- **Do NOT `consumeTemp`** the array or elements (that would transfer ownership /
  suppress the RefDec) — the borrow model requires the temps stay in statement
  cleanup (`mem.temporary`). Suppressing RefDec → double-free of caller temps.

**Callee side — still no code:** the `*[]@T` param is raw → no entry-acquire, no
exit-release. Confirm by test (refcount observation).

**Interface-element variadics (`...@Iface` / `...Stringer`):** the raw-interface
form stays zero-heap ((ptr, vtable) pairs stack-packed); the managed
`...@Stringer` form gives `*[]@Stringer` with the same caller-acquire/callee-borrow
discipline. Verify iface-element move semantics (`consumeTemp` for a fresh
`@Iface` temp) still work when the element is **stored into the backing array**
rather than passed as a direct scalar arg.

**Tests (`.rules` → `func.variadic.pack` + `func.variadic.borrow`):**
- A refcount-observing test (`conformance/matrix` refcount cell, or a
  `rt.Refcount(*uint8)`-based conformance test): pack N `@T` elements, assert the
  post-call refcount returns to baseline (no leak, no double-free); assert a
  copied-out element (callee `make_slice` + acquiring copy) retains correctly.
- `...@Iface` / `...Stringer` pack; callee copies one element out to retain.

**Green invariant:** managed-element variadics leak-free and double-free-free;
existing refcount tests pass.

---

## 8. Phase 6 — Indirect boundaries, methods, generics, method values

Extends the remaining three checker call sites and IR call paths, plus generics
and method values/expressions. **ABI erasure:** at every indirect boundary the
caller packs/spreads to a plain `*[]T` **before** the single indirection, so the
shim/slot sees an ordinary raw-slice param. This phase can land as several green,
tested commits (func-values → interfaces → generics → method values).

**Checker — the other three call sites** (each hand-rolls `numArgs != numParams`
+ per-arg `AssignableTo`; all need the Phase-3/4 variadic binding):
- `checkResolvedMethodCall` (`check_method.bn:190-227`) — static method dispatch;
  params after the receiver.
- `tryInterfaceMethodCall` (`check_method.bn:138-185`) — vtable dispatch.
- `tryTypeParamMethodCall` (`check_method.bn:103-129`) — generic-constraint method
  (with `substituteSelf` on the element type).

**Checker — generics + method values:**
- `instantiateGenericFunc` (`check_generic.bn:74-99`) — copy the last param's
  `IsVariadic` into the instantiated `MakeFuncType`. `substituteTypeParams`
  (`:224-270`, `TYP_SLICE` arm) already turns the `*[]T` element into `*[]int`;
  only the flag needs carrying.
- Method expression `T.M` (`check_expr_access.bn:260`) and method value `x.M`
  (`:337-343`) — the constructed func-value type must be variadic when the method
  is. The per-last-`Param` flag survives the receiver-drop reindex automatically;
  **add a test** confirming the flag lands on the right param after dropping
  `Params[0]`.
- `defaultType` (`checker_util.bn:59`) — preserve variadic-ness when decaying a
  func reference to `@func` (`var f = variadicFn`).

**IR — the other call paths** (pack/spread via the shared `emitVariadicTail`
**before** building the args slice):
- `genFuncValueCallWithFn` (`gen_call.bn:381-413`) — reads
  `fnTyp.Params[last].IsVariadic` off the `types.Type`; packs/spreads before
  `EmitCallFuncValue` (also covers the IIFE path `genImmediateFuncLitCall`,
  `:368`).
- `genInterfaceMethodCall` (`gen_iface_dispatch.bn:72-139`) — reads the
  per-method variadic marker from `ModuleInterface`; packs/spreads before
  `EmitCallIfaceMethod`.
- `genMethodCall` (`gen_method.bn`) and the **generic-instantiation call path**
  (`gen_generic.bn` / `genCallInstantiate`) — **not covered by the survey; must
  be enumerated and edited** (a static method or a monomorphized `func f[T
  C](xs ...T)` can be variadic). Sweep for the arg-loop shape
  (`genExprOrFuncRef` + `coerceArg`) repo-wide to be sure no call path is missed.
- Tail-call return `return f(...)` — confirm it routes through the same call
  lowering (it should) and thus gets pack/spread.

**IR — interface registry:** populate the `ModuleInterface` per-method variadic
marker at the registry-build path (`collectInterfaceFromDecl` /
`MethodParamsFlat` construction). Decide the exact shape (parallel
`MethodParamVariadic @[]bool` per method, alongside `MethodParamCounts`).

**ABI-erasure confirmation (backends/VM — expected zero changes):** the three
indirect ops (`OP_CALL_FUNC_VALUE`, `OP_CALL_IFACE_METHOD`, `OP_CALL_HANDLE`)
already pass a `*[]T` as an ordinary 2-word aggregate; the shim loops iterate
`fvTyp.Params` and, post-erasure, see one raw-slice param. The cross-mode shim
bank is capped at 7 int slots (`rt._call_shim_*`, `a0..a6`); a variadic param
counts as **one** by-address slot, so variadic-ness alone doesn't blow the cap
(pre-existing constraint, shared with all calls). Add cross-mode tests but expect
no backend/VM edits.

**Tests (`.rules` → `func.variadic.identity` + `iface.impl.coverage`):**
- variadic func-value type `*func(...T)`: identity (assign a variadic func to it;
  reject assigning a fixed-`*[]T` func); indirect call through it; spread through
  it.
- variadic interface/impl method + vtable dispatch; a fixed impl must **not**
  satisfy a variadic iface method (and vice versa).
- method expression and method value of a variadic method.
- generic `func f[T C](xs ...T)` — per-instantiation pack (`f[int]`, `f[@Foo]`).
- cross-mode: compiled variadic caller ↔ VM callee and vice versa (locks the
  2-word `*[]T` contract; runs in `-int` modes).

**Green invariant:** all indirect/method/generic variadic forms work; existing
suites pass.

---

## 9. Phase 7 — Spec-coverage close-out + status flip

- Add the remaining `.rules`-cited positive/negative spec tests under
  `conformance/spec/10-functions/` (next free `NNN` in that directory's
  independent namespace — currently up to 164, so 165+) not already added in
  Phases 3–6, ensuring each new rule-ID (`func.call.apply`, `func.variadic.*`) has
  at least one covering test (spec-coverage GAPS → 0 for these).
- **Docs repo (separate commit):** remove the "Draft; not yet implemented" inline
  note in `docs/spec/10-functions-methods-function-values.md` §10.3 (~lines
  199-206) now that implementation-conformance is met. (`annex-c` is a stub — no
  per-rule row to flip; the inline note is the status marker.)
- Update `explorations/claude-todo.md`: move the variadics entry (~line 64) to
  `claude-todo-done.md` with the landing commits.

**Green invariant:** hygiene (incl. spec-coverage) passes; the feature is fully
covered and its spec status reflects reality.

---

## 10. Risk / invariant checklist (verify at every phase)

1. **No hidden heap allocation on the pack path.** `emitVariadicTail` uses
   `EmitAlloc(MakeArrayType(T,N))` (stack) + manual header — **never**
   `OP_MAKE_SLICE` (heap `rt.MakeManagedSlice`). A MAKE_SLICE here silently
   violates the zero-alloc guarantee and creates a managed-slice (wrong ownership).
2. **Signature identity includes variadic-ness at every comparator.** Enumerate
   repo-wide; two bespoke comparators (`methodSigSatisfies`,
   `checkBniSignatureMatch`) don't route through `Identical`. Miss one → variadic
   and fixed-`*[]T` become interchangeable (unsound func-value / interface /
   method-value assignment).
3. **Variadic body type is raw `*[]T`, never `@[]T`.** Guarantees the automatic
   callee-borrow (no entry-acquire/exit-release). A managed body type would
   RefInc/RefDec a borrow the caller owns.
4. **Managed-element refcount is inverted.** Caller RefIncs each element on store
   and registers the backing array as a **statement temp** (RefDec'd at statement
   end); callee borrows. Missing `registerTemp` → leak; RefDec-in-callee or
   `consumeTemp` → double-free/UAF.
5. **Zero variadic args → `{null, 0}`**, tested with `len == 0`, never a nil
   compare.
6. **ABI erasure caller-side at all three indirect boundaries** (pack/spread
   before the single indirection); shim/slot signature is a plain `*[]T`.
7. **`print`/`println`/`panic` stay special**, never routed through the `...T`
   path; spread onto them is rejected.
8. **`__c_call` / `CFixedArgs` / `VariadicStackOnly` untouched** — the C-varargs
   mechanism (§16.9) is separate; never set `CFixedArgs` on an `OP_CALL` or route
   `...T` through the V-variant CallConv helpers.
9. **BUILDER-compat.** `pkg/binate/{parser,types,ir,ast,loader}` are in cmd/bnc's
   BUILDER-compiled tree. The code that **implements** variadics must **not itself
   use** variadic syntax (or any post-`BUILDER_VERSION` feature) — only plain bool
   fields + logic. Confirmed: no bnc-tree signature needs converting to variadic;
   do **not** dogfood. Verify any tempting new syntax against the pinned BUILDER
   first.
10. **Each commit green.** Checker-accepts and IR-lowers for a given form land
    **together** (accept-without-lower crashes). Phase 1 is inert; Phase 2 is
    declare-only; Phases 3–6 add call support incrementally.

---

## 11. Test matrix (consolidated)

| Form | positive | negative | modes |
|---|---|---|---|
| individual pack | sum/collect, mixed fixed+var | wrong elem type | comp, int, comp-comp |
| empty (`len==0`) | `f()` | — | all |
| spread `@[]T`/`*[]T` | forward `{data,len}` | into non-variadic; onto print/panic | comp, int |
| spread `arr[:]...` / `lit[:]...` | sub-sliced array/string | bare array (not sub-sliced) | comp |
| managed `...@T` | acquire/borrow, copy-out retain | — (refcount assertion) | comp, int |
| `...@Iface`/`...Stringer` | pack, copy-out | — | comp |
| func-value `*func(...T)` | identity, indirect call, spread-through | assign fixed-`*[]T` func | comp, int |
| variadic iface/impl method | dispatch | fixed impl ∤ variadic iface | comp, int |
| method expr/value | variadic preserved | — | comp |
| generic `f[T C](xs ...T)` | per-instantiation pack | — | comp, comp-comp |
| cross-mode | compiled↔VM 2-word `*[]T` | — | int modes |
| parser | 3 forms parse | non-final var param, 2 var params, variadic receiver, spread-not-last | unit |
| identity (unit) | variadic≠fixed in `Identical`/`funcSignaturesMatch`/`SigString` | — | unit |

Conformance homes: rule-ID-cited tests in `conformance/spec/10-functions/` (with
`.rules`); invariant repros (no-heap, borrow-escape-is-UB-not-tested) in
`conformance/regressions/` (no number); refcount assertions in
`conformance/matrix` refcount cells.

---

## 12. Open items for the user / review

- **O-1.** `023_variadic_absent_exact_args` — repurpose in place (keep NNN 023,
  rewrite `.bn` + `.rules` to a real variadic positive test) or delete + replace
  with a fresh NNN? (Renaming a spec test changes its runner name.)
- **O-2.** Exact diagnostic wording (D-F) — pinned as `.error` regexes on first
  use, so effectively a stable contract. Confirm the proposed strings.
- **O-3.** Flag placement D-A (per-`Param` vs FuncType) — the survey strongly
  favors per-`Param`; confirm no consumer needs a FuncType-level flag.
- **O-4.** Whether the no-heap-alloc and managed-borrow invariants are asserted
  via `conformance/matrix` refcount/alloc cells vs plain conformance — decide the
  mechanism (§7, §11).
- **O-5.** Mode coverage from day one — the pack/spread is IR-driven so the VM
  inherits it; confirm no `-int`-mode xfails are expected (avoid born-stale
  markers).

---

## 13. Cross-references

- Roadmap: [plan-variadics.md](plan-variadics.md).
- Spec: §10.3 (`func.call.apply`, `func.variadic.decl/identity/pack/spread/borrow`);
  §7.9 `type.func.kinds`; §7.13 slice layout; §10.8 func-value spelling/identity;
  §10.11 method expr/value; §10.12 indirect call; §11.1 `iface.impl.coverage`;
  §15.7 `builtin.predeclared`; §2.4 cross-mode; §16.9 `pkg.ccall` (separate
  C-varargs `...`, unaffected).
- Grammar: `binate.ebnf` `VariadicParam` / `FuncTypeParams` / `ArgumentList` / D12.
- Design: `claude-notes.md` "Variadic functions — DECIDED" / "Spread operator —
  DECIDED".
- Guidelines: `ir-backend-guidelines.md` (layout in `pkg/types`, target-parameterized).
