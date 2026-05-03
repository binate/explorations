# Plan: REPL (Interpreter-Only)

> **Status: Tier 1 + Tier 2 (func/const/var with init,
> untyped var inference) + Tier 4 (replace + shadow) LANDED**
> (2026-05-02).  `bni --repl <file.bn|dir>` ships; top-level
> `func`, `const`, and `var` decls persist across turns
> (`var x = expr` infers type from a literal initializer);
> `var x T = expr` and `var x = lit` both evaluate the
> initializer before subsequent reads (at the prompt and on
> file load); redefining a func works for both compatible-sig
> (replace in place — old callers see new body) and
> incompatible-sig (shadow — old callers retain old body via
> eager-filled CallCache, new callers route to the new VMFunc).
> The brace-balance accumulator is paren-aware, so multi-line
> `const ( ... )` etc. are recognized as continuations.  Tier 3
> (forward refs) and Tier 5 (mid-session imports) remain DRAFT;
> Tier 2 also has follow-ups for type / methods; Tier 4 still
> has small follow-ups (refcount-aware shadow warning,
> forced-shadow escape hatch, method redefinition).
> Compiled-mode REPL features (hot-swap of interpreted functions
> while a compiled binary runs, package descriptors, cross-mode
> trampolines) are explicitly out of scope here — they belong to
> the broader compiler/interpreter interop work and depend on
> `plan-function-values.md`.

## Tier 1 landed — what shipped (2026-04-30)

Five commits on main, each independently shippable, plus one
companion entry point added during the driver work:

| Layer | Entry points added | Commit |
|---|---|---|
| `pkg/vm` | `CallCache` per-VMFunc memoization for `BC_CALL` / `BC_FUNC_ADDR` (the perf foundation that made REPL redefinition safe to design — see "What to do now" item #2 below) | `6c8e0c0` |
| `pkg/parser` | `ParseExpr`, `ParseStmtList` | `eac3149` |
| `pkg/types` | `CheckExprInScope`, `CheckStmtListInScope` | `de0d168` |
| `pkg/ir` | `GenSyntheticFunc` | `3424248` |
| `pkg/vm` | `LowerOneFunc`, `CallByVMFunc` | `e945cdb` |
| `pkg/types` (companion) + `cmd/bni` | `CheckMainPersistent`; `--repl` driver | `0fcf9d2` |

### Deviations from the original plan

- **`CheckMainPersistent` was added** to `pkg/types` (not in the
  original plan).  Reason: `Check()` pushes a scope and pops it on
  return, so the loaded file's symbols vanish from `c.Scope` —
  prompt entries can't see them.  `CheckMainPersistent` does the
  same work without the trailing `popScope`, so the file's scope
  stays installed for the REPL session.

- **`CallCache` (commit `6c8e0c0`) replaced the planned
  "name → idx hash on `vm.Funcs`"** (item #2 under "What to do
  now").  Both solve the same root problem (the perf argument for
  ever baking idx into bytecode, which would close off the
  redefinition story).  The cache is per-VMFunc, parallel to
  `Names`, lazy-filled, and explicitly designed to be invalidated
  on future REPL mutation of `vm.Funcs` (full flush on
  rebind / append-with-shadow; -1-only flush is sufficient on
  pure append).

- **Auto-`println` wrap of bare expressions is deferred.** The
  plan called for wrapping `1+2` at the prompt as `println(1+2)`
  for primitives / char slices.  Implementation requires
  constructing an `EXPR_CALL` AST node from `cmd/bni`, which
  isn't blocked on anything but adds surface for one minor
  affordance.  Tier 1 PoC ships without it — users type
  `println(...)` explicitly.  Easy follow-up.

### Verified behaviors (manual smoke)

Loaded module declares `func helper(int) int { return x * 2 }`:

```
> println(helper(7))                        → 14
> println(helper(100))                      → 200
> var x int = helper(3); println(x + 1)     → 7
> undefined_name                            → undefined: undefined_name
> println(helper(7))                        → 14   (session intact after error)
> var y int = "string"                      → cannot assign ... to int
> println(helper(2))                        → 4    (session intact after type error)
```

Errors at parse / type / IR-gen / lower / runtime print and return
to prompt; loaded state is unaffected.

### Conformance / unit-test coverage

- 282/282 `boot-comp-int` conformance after each step.
- 29/29 unit-test packages green under `boot-comp-int`.
- New targeted unit tests:
  - `parser.TestParseExprBasic`,
    `parser.TestParseStmtListSingle`,
    `parser.TestParseStmtListMultiple`
  - `types.TestCheckExprInScopeBasic`,
    `types.TestCheckExprInScopeUndefined`,
    `types.TestCheckStmtListInScopeBasic`,
    `types.TestCheckStmtListInScopeNoLeak`,
    `types.TestCheckMainPersistentLeavesScope`
  - `ir.TestGenSyntheticFunc`
  - `vm.TestLowerOneFuncAndCall`,
    `vm.TestBCFuncAddrCacheHit`

### Tier 1 follow-ups (small, optional, not blocking later tiers)

- **Auto-`println` wrap** for printable bare expressions —
  **explicitly DEFERRED** until interfaces / per-type `Format`
  dispatch lands.  `bootstrap.println` is a temporary hack
  scheduled for removal; building features on top of it
  (extending the printable set, AST-rewrite to inject println)
  would entrench the hack and complicate the cutover.  The plan's
  pretty-printing section already says this; reaffirmed here so
  it's not relitigated.
- ~~**Multi-line input** at the prompt.~~ **LANDED.**  Brace-balance
  scan over accumulated input; continuation prompt is `... `;
  braces inside string / char literals and `//` / `/* ... */`
  comments are skipped.  Doesn't track parens / brackets, so
  multi-line `(...)` expressions still aren't recognized as
  continuations — niche enough to leave for later.
- **Pretty-printer** is still gated on interfaces (Tier 1.5+).

## Tier 2 first cut landed — what shipped (2026-04-30)

`bni --repl` accepts top-level `func` declarations at the prompt;
defined functions persist in `c.Scope` and `vm.Funcs` and are
callable from subsequent prompt entries.  Single commit on main:

| Layer | Entry points added | Commit |
|---|---|---|
| `pkg/parser` | `ParseTopLevelDecl`, `IsAtTopLevelDecl` | `b1af7d1` |
| `pkg/types` | `CheckDeclInScope` | `b1af7d1` |
| `pkg/ir` | `GenDecl` (DECL_FUNC only; diagnostic for other kinds) | `b1af7d1` |
| `cmd/bni` | `evalReplLine` dispatches via `IsAtTopLevelDecl` to a new `evalReplDecl` (Tier 2) or `evalReplStmtList` (Tier 1) | `b1af7d1` |

### Verified behaviors

```
> func double(x int) int { return x * 2 }
> println(double(7))                                → 14
> func a(x int) int { return x + 1 }
> func b(x int) int { return a(x) * 10 }
> println(b(4))                                     → 50
> type T struct { X int }
  only func declarations are supported at the prompt (Tier 2 first cut)
> println(double(100))                              → 200    (session intact)
```

Multi-line func decls (body across several lines) work via the
existing brace-balance accumulator from the multi-line input
patch — no extra work needed.

### Out of scope in the first cut (Tier 2 follow-ups)

- **`type` at the prompt.**  `ir.GenDecl` surfaces a "not yet
  supported" diagnostic.  Needs: register in `moduleStructs`;
  regenerate dtor / copy helpers (and the dedup-aware machinery
  to avoid duplicate AddFunc on a re-run); make the new type's
  layout known to `pkg/types` queries.
- ~~**Untyped `var x = 5` at the prompt.**~~  LANDED
  (2026-05-02).  Type inference from a literal initializer (int
  / bool / char-slice / char / float) works at file scope and
  at the prompt.  Non-literal initializers (function calls,
  arithmetic) still need an explicit type — the type checker
  could resolve those for top-level vars too, but that would
  require threading the checker through IR-gen.  Out of scope
  for the literal-init common case.
  ~~Initializer evaluation itself~~ LANDED (2026-05-02) — see
  "Tier 2 var-initializer evaluation landed" section below.
- **Method declarations** (`func (r T) m(...) ...`).  Diagnostic
  surfaces for these too.  Method-receiver registration and
  vtable interactions add scope.
- **New managed-type dtor needs introduced by a body.**  If a
  func defined at the prompt uses a `@[]T` shape that wasn't in
  the loaded module, the dtor is missing.  Fix is dedup-aware
  re-run of `generateDtors` / `generateCopies`.  Until then,
  bodies typed at the prompt should stick to managed-type shapes
  the loaded module already uses.
- **Forward references** (Tier 3) remain explicitly excluded.
  **Redefinition** (Tier 4) replace path has since landed; see
  the dedicated section below.

### Conformance / unit-test coverage

- 289/289 `boot-comp-int` and `boot-comp` conformance after
  Tier 2 + Tier 4 replace path.
- 29/29 unit-test packages green under `boot-comp-int`.
- New targeted unit tests:
  - `parser.TestParseTopLevelDeclFunc`
  - `types.TestCheckDeclInScope`,
    `types.TestCheckDeclInScopeBadBody`,
    `types.TestCheckDeclInScopeConst`,
    `types.TestCheckDeclInScopeConstGroup`,
    `types.TestCheckDeclInScopeVar`
  - `ir.TestGenDeclFunc`,
    `ir.TestGenDeclTypeRejected`,
    `ir.TestGenDeclConst`,
    `ir.TestGenDeclConstGroup`,
    `ir.TestGenDeclVar`,
    `ir.TestGenDeclVarWithoutType`,
    `ir.TestGenDeclFuncRedefinesInPlace`
  - `vm.TestLowerOneFuncReplacesExisting`
- `e2e/repl.sh` covers the const path with four cases (typed,
  untyped, single-line group, const-then-func-using-it), the
  multi-line const-group continuation case, the var path with
  three cases (read+write, func-mutates, no-type-rejected), and
  the Tier 4 redef path with three cases (basic replace,
  caller-sees-new, shadow on diff-sig).

## Tier 4 shadow path landed — what shipped (2026-05-01)

Re-typing a func with a DIFFERENT signature now SHADOWS the old
definition rather than rejecting it.  Old VMFunc stays in
`vm.Funcs` at its existing index, callable via any caller whose
`CallCache` already resolved that callee — those callers
continue invoking the OLD shape.  The funcIndex re-points the
name to the NEW (later) entry, so freshly-lowered code that
mentions the name resolves to the new VMFunc.  The replace path
(compatible-sig, prior commit) keeps its in-place rebind.

Two commits on main, in order:

### Substrate: O(1) LookupFunc + eager CallCache fill (`9af2d56`)

- `pkg/vm.bni`: new `FuncIndexEntry` + 3 fields on `VM`
  (`IndexBuckets`, `IndexCount`, `IndexMask`).
- `pkg/vm/func_index.bn`: open-addressing string→int hash, djb2,
  linear probing, lazy-init, resize at 75% load.
- `pkg/vm/vm.bn`: `LookupFunc` is now a thin wrapper over the
  hash — O(1) instead of O(N).
- `pkg/vm/lower.bn`: `LowerModule` is two-pass (build the index
  for all funcs, then eager-fill CallCache); `LowerOneFunc`
  appends/replaces then eager-fills.

Why eager: the lazy `CallCache` fill was correct for the
non-REPL world where `vm.Funcs` never changes after load, but it
made shadow semantics unfixable — an old caller whose
`CallCache` slot was still -2 when its callee got shadowed would
pick up the new (incompatible) callee on its next execution.
Eager-fill freezes the binding at lowering time.  The cost is
covered by O(1) LookupFunc.

### Shadow itself (`63cc49b`)

- `pkg/types.bni` + `check_decl.bn`: `Checker.AllowRedef` flag
  suppresses `checkBniSignatureMatch` when set.
  `CheckDeclInScope` toggles it on around its inner passes
  (compile-time path keeps the strict check).  Removes the
  misleading "X: .bni declares N" wording for prompt
  redefinitions.
- `pkg/vm/lower.bn` + `.bni`: new `LowerOneFuncShadow` —
  always APPENDS to `vm.Funcs`; re-points the funcIndex to the
  new (later) idx; eager-fills the new VMFunc's CallCache.
- `cmd/bni/repl.bn`: `evalReplDecl` captures the old func type
  before `CheckDeclInScope`, then dispatches:
    * no existing → `LowerOneFunc` (first definition).
    * `types.Identical(oldType, newType)` → `LowerOneFunc`
      (replace, in-place rebind).
    * different sig → `LowerOneFuncShadow` + warning.

### Verified behaviors

```
> func caller() int { return helper(5) }
> println(caller())                       → 10  (helper: x*2)
> func helper(a int, b int) int { return a + b }
warning: helper shadowed (incompatible signature);
         existing callers retain old definition
> println(caller())                       → 10  (old helper)
> println(helper(3, 4))                   → 7   (new helper)
```

### Out of scope (Tier 4 follow-ups)

- **Refcount probe at shadow time.**  Plan calls for a warning
  conditioned on outstanding references to the OLD VMFunc.
  Today the warning fires unconditionally on every shadow; the
  conditional variant needs a way to introspect VMFunc
  refcounts cheaply.
- **Forced-shadow escape hatch** (syntax TBD per
  `claude-notes.md`).
- **Method redefinition** is still rejected at the IR-gen
  layer (`GenDecl` rejects `DECL_FUNC` with a `Recv`).

## Tier 4 replace path landed — what shipped (2026-05-01)

The replace path (compatible-sig redefinition) landed first in
commit `5b0de9a`.  Shipped state:

| Layer | Change |
|---|---|
| `pkg/ir/gen.bn` | New `setOrAppendFuncSig(sig)` — replace by name in `moduleFuncs` or append. |
| `pkg/ir/gen_module.bn` | `GenDecl` for DECL_FUNC uses `setOrAppendFuncSig` and qualifies `sig.Name` (drive-by fix — Tier 2 first cut stored bare names, which silently fell through to default sig lookups). |
| `pkg/vm/lower.bn` | `LowerOneFunc` replaces an existing `vm.Funcs` entry with the same qualified name in place; appends only if no match. |

In-place rebind at the existing `vm.Funcs` idx keeps cached
indices valid (same N, just points at a new VMFunc), so the
per-call-site `CallCache` needs no flush.  Subsequent lookups
and freshly-lowered callers all resolve to the new VMFunc.

```
> println(helper(7))                  → 14   (loaded helper: x*2)
> func helper(x int) int { return x * 3 }
> println(helper(7))                  → 21   (rebound)
> func caller() int { return helper(10) }
> println(caller())                   → 30
> func helper(x int) int { return x + 100 }
> println(caller())                   → 110  (caller sees new helper)
```

The shadow path (incompatible-sig redefinition) shipped in a
follow-up — see "Tier 4 shadow path landed" above.

## Tier 2 var landed — what shipped (2026-05-01)

`bni --repl` accepts top-level typed `var` decls at the prompt.
A `var counter int` registers in `c.Scope`, in `moduleGlobals`
(so subsequent IR-gen of expressions resolves the name through
the global path), and on the VM (storage allocated via
`MaterializeOneGlobal`).  Reads, writes, and func-mediated
mutations from later prompt entries all see the same storage.

Single commit on main:

| Layer | Change |
|---|---|
| `pkg/vm/lower.bn` + `.bni` | New `MaterializeOneGlobal(g)` — per-global analog of `materializeGlobals`; allocates and zero-initializes one slot, appending to `globalNames` / `globalAddrs`. |
| `pkg/ir/gen_module.bn` | `GenDecl` now also accepts `DECL_VAR` (typed); registers in `moduleGlobals` + `AddGlobal` on the module.  Untyped `var x = 5` returns a clear diagnostic. |
| `cmd/bni/repl.bn` | After `GenDecl` succeeds for a var, finds the new entry in `m.Globals` and dispatches it through `vm.MaterializeOneGlobal`. |

### Verified behaviors

```
> var x int
> println(x)                   → 0
> x = 42
> println(x)                   → 42
> var counter int
> func bump() { counter = counter + 1 }
> bump(); bump(); bump()
> println(counter)             → 3
> var x = 5                    → var decl at the prompt requires
                                 an explicit type
```

### Initializer evaluation — LANDED (2026-05-02)

See "Tier 2 var-initializer evaluation landed" section below
for the shipped detail.  The original Tier 2 first cut left
`var x int = 42` silently zero-initialized (matching the
file-load path's pre-existing limitation); the follow-up wired
both paths through a new IR-emitted synthetic init function.

## Tier 2 var-initializer evaluation landed — what shipped (2026-05-02)

`var x T = expr` now actually evaluates `expr` and stores the
result in `x` before any subsequent code runs.  Previously,
both the file-load path and the REPL silently dropped the
initializer (the IR layer captured `mg.Init` for int literals
only and the VM never read it).  After this commit:

- **File load**: each package gets a synthetic `<pkg>.__init`
  function (when it has any non-trivial var inits).  A
  per-binary `<main>.__init_all` dispatcher runs each package's
  init in dep order, then `<main>.__entry` runs the dispatcher
  before `main.main`.
- **REPL prompt**: a freshly-typed `var x T = expr` runs a
  one-shot synthetic that evaluates the assignment immediately,
  so the next prompt entry sees `x` at its declared value.

### Why an entry wrapper instead of a C runtime change

The C runtime is a temporary scaffold for the pure-Binate end
state.  Adding init dispatch into `int main()` would tie a
Binate-level concept to the C contract.  Instead, a one-time
mangler change moves the entry into Binate:

- `main.main` no longer special-cases to `bn_main`; it
  mangles like any other free function (`bn_main__main`).
- A new special case `main.__entry → bn_entry` reserves a
  stable symbol for the entry wrapper.
- C runtime calls `bn_entry()` (one-line update); Binate's
  `<main>.__entry` does init dispatch + main.

Future entry-time concerns (panic / signal handler setup,
finalizers, etc.) all live in Binate behind the `bn_entry`
symbol — the C side never has to change again for this kind
of reshuffle.

### Implementation

| Layer | Change |
|---|---|
| `pkg/ir/gen_init.bn` (new) | `generatePackageInit` emits `<pkg>.__init` per package when it has var inits.  `EmitInitDispatcher` emits `<m_pkg>.__init_all`.  `EmitMainEntry` emits `<m_pkg>.__entry`.  `MakeInitAssignStmt` builds an assignment AST for the REPL one-shot. |
| `pkg/ir/gen_module.bn` | `GeneratePackage` and `GenModule` call `generatePackageInit` after their main passes. |
| `pkg/mangle/mangle.bn` | Drop `main.main → bn_main` special case; add `main.__entry → bn_entry`. |
| `cmd/bnc/main.bn` | Track which compiled packages have inits; call `EmitInitDispatcher` and `EmitMainEntry` on the main module. |
| `cmd/bni/main.bn` | Mirror cmd/bnc's emission, then dispatch via `vm.CallFunc(vmInst, "main.__entry", ...)` (instead of calling `main.main` directly). |
| `cmd/bni/repl.bn` | After REPL `var x T = expr`, build a one-shot synthetic via `MakeInitAssignStmt + GenSyntheticFunc + LowerOneFunc + CallByVMFunc`. |
| `runtime/binate_runtime.c` | One-line: call `bn_entry()` instead of `bn_main()`. |

Conformance test `353_global_var_init.bn` exercises the
end-to-end path in all four chains (boot / boot-comp /
boot-comp-int / boot-comp-comp).  pkg/ir gains 9 unit tests
in `gen_init_test.bn` covering each emitter individually.

(Originally numbered 345; renumbered to 353 to free the slot
for `345_interface_decl` which landed in parallel.)

## Tier 2 untyped var landed — what shipped (2026-05-02)

`var x = expr` (no explicit type) at file scope and at the
REPL prompt now infers the var's type from the initializer
literal: `var x = 42` → int, `var x = true` → bool,
`var x = "hi"` → @[]const char, `var x = 'A'` → char,
`var x = 1.5` → float64.

Single commit on main (`fdf6b52`).  New helper
`pkg/ir.resolveGlobalVarType(d)` honors `d.TypeRef` when set,
infers from initializer kind otherwise.  Both file-load DECL_VAR
sites and REPL `GenDecl` use it.

Non-literal initializers (`var x = i + 100`, `var y = foo()`)
still need an explicit type — the type checker resolves those
for local vars but threading it through IR-gen for top-level
vars wasn't worth the surface for this common case.  Users
spell the type explicitly when needed.  Conformance test
`352_global_var_untyped.bn` exercises the literal path.

## Tier 2 paren-aware accumulator landed — what shipped (2026-05-01)

The multi-line input accumulator (`computeBraceDepth` →
`computeOpenDepth`) now tracks unclosed `(` / `)` as well as
`{` / `}`.  Multi-line `const ( ... )` blocks are recognized as
continuations (rather than firing evaluation prematurely on
the first line); same for any other paren-bracketed
construct typed across lines.  Brackets inside string / char
literals and `//` / `/* ... */` comments are skipped using the
existing escape-handling logic.

The combined depth counter is a heuristic, not a real parser:
syntactically wrong interleavings like `{(}` will balance to 0
and the parser catches them — same contract as before.

## Tier 2 const landed — what shipped (2026-05-01)

`bni --repl` accepts `const` declarations at the prompt: typed
(`const K int = 42`), untyped (`const K = 42`), and grouped
on a single line (`const ( A = 10; B = 20 )`).  Registered
constants persist in `c.Scope` and `moduleConsts`, so any
subsequent prompt entry — bare expression, stmt list, or
new func decl — resolves the name through the existing
`lookupConst` path.

Single commit on main:

| Layer | Change |
|---|---|
| `pkg/ir/gen_module.bn` | `GenDecl` now also accepts `DECL_CONST` (calls existing `genConst`) and `DECL_GROUP` of consts (calls existing `genConstGroup`). |
| `cmd/bni/repl.bn` | `evalReplDecl` only routes `DECL_FUNC` results through `LowerOneFunc`; const decls register in `moduleConsts` and need no VM-side work. |

No new entry points needed — `genConst` / `genConstGroup` and
`lookupConst` already existed for the file-load path.

### Verified behaviors

```
> const K int = 42
> println(K)                     → 42
> const L = 7
> println(K + L)                 → 49
> const ( A = 10; B = 20 )
> println(A); println(B)         → 10 / 20
> const SCALE int = 3
> func tripled(x int) int { return x * SCALE }
> println(tripled(11))           → 33
```

The remainder of this document describes the original plan as
written before Tier 1 landed.  Remaining Tier 2 follow-ups
(above) and tiers 3–5 are still DRAFT.

---

## Scope and rationale

The REPL is an explicit core goal of the language (see
`claude-notes.md` § "Forward references & REPL model — DECIDED" and
the dual-mode rationale in `claude-discussion-detailed-notes.md`
§ 11 / § 23). The semantics are largely already decided. What
this plan covers is the **toolchain shape** — what the existing
pipeline can and can't do today, and what concretely needs to
change to expose an interpreter-only REPL on top of `pkg/vm`.

Why interpreter-only first:

- No LLVM / native involvement, so we can ship without touching
  the compiler backend.
- No cross-mode trampolines (Phase 3 of the function-values
  plan) and no package descriptor with canonical layout (interop
  work). Both can land later without revisiting REPL.
- Most of the work overlaps with the audit the interop story
  already needs ("verify rather than redesign" — interop wants
  per-package incremental loading, REPL wants per-decl
  incremental loading, both want the same shape from the
  pipeline). Two birds.
- An interpreter-only REPL is shippable now as a **proof of
  concept**: minimum-viable line reading, no fancy editing /
  history / completion, output bounded by what `println`
  already supports. The point is proving that
  parse → typecheck → IR-gen → lower → exec works
  *incrementally* against an existing VM state, end-to-end
  through the real pipeline. UX quality is explicitly
  out of scope — UI improvements don't involve deep
  architectural constraints and can land any time.
- A polished REPL — pretty-printed values for arbitrary types,
  rich I/O, real line editing — is gated on interfaces (and
  probably generics), because pretty-printing arbitrary values
  cleanly requires interface dispatch, and richer I/O requires
  standard-library design that's also gated on interfaces. None
  of that blocks shipping the PoC.

## Already decided — do not relitigate here

Refer to `claude-notes.md`. Summary for context:

- **Retained mode** (definitions) vs **immediate mode** (bare
  expressions / statement lists at the prompt). Source files are
  declarative-only; bare exprs are REPL-only.
- **No forward declarations.** Deferred validation handles them.
  Errors surface at use, not at definition.
- **Redefinition**: compatible (same sig) → replace; incompatible
  → shadow with refcounted old-def retention; warn on outstanding
  refs at shadow time. Forced-shadow escape hatch (syntax TBD).
- The compiled-and-running case (hot-swap into a live binary) is
  in the long-term design but **not** in scope here.

If any of these need to change, update `claude-notes.md` first;
this plan adapts to whatever lands there.

## What exists today (verified)

The picture is friendlier than it first looks. Key findings from
poking at the current pipeline:

- **`BC_CALL` is name-resolved per call**, not idx-baked.
  `pkg/vm/vm_exec.bn:418-421`: bytecode stores a per-VMFunc
  strings index for the qualified callee name, and `LookupFunc`
  walks `vm.Funcs` by name on every call. Replace-redefinition
  becomes an in-place body swap; shadow-redefinition is
  append-then-pick-latest.
- **`vm.Funcs` is already incremental.** `LowerModule`
  (`pkg/vm/lower.bn:10-44`) is called per-module and appends;
  multiple modules already coexist in one VM with their own
  preserved string pools (the comment around line 14-21 calls
  this out — earlier behavior overwrote `vm.Strings` and broke
  cross-module string indices, since fixed). Globals are also
  append-only via `materializeGlobals`.
- **`@VMFunc` is managed**, so old function bodies stay alive
  via refcount once anything else holds them. The substrate for
  shadow-redefinition is already in place.
- **Parser has per-decl / per-expr entry points internally.**
  `pkg/parser/parse_decl.bn` exposes `parseFuncDecl`,
  `parseTypeDecl`, `parseVarDecl`, `parseConstDecl`;
  `pkg/parser/parse_expr.bn` exposes `parseExpr`. They're
  internal today (called from the file-level driver) but they
  exist.
- **Type checker is module-shaped at entry but per-decl
  internally.** `Check(c, file)` (`pkg/types/checker.bn:271`)
  iterates declarations.
- **IR-gen is module-shaped at entry but per-decl internally.**
  `GeneratePackage(file, m)` (`pkg/ir/gen_module.bn:107`)
  iterates declarations.

What's actually rigid:

- **Loader is package-shaped.** `pkg/loader` resolves and loads
  whole packages. There's no "extend this package with one more
  decl" entry.
- **Frontend pipeline drivers are module-shaped.** Per-decl
  internals exist, but no current call sequence wires them
  together for the "one decl against an existing scope" case.
- **Type checker has no concept of pending.** Errors fire
  immediately on undefined names. Forward references work in
  current code only because the whole module is parsed before
  checking. Deferred validation is real new infrastructure.
- **No pretty-printer for arbitrary values.** `println` covers
  char slices and primitives only.
- **`LookupFunc` is a linear scan.** Fine today; will matter at
  REPL volumes. Easy to fix (name → idx hash on `vm.Funcs`) and
  worth doing before Tier 1 ships, since the alternative
  (idx-baked bytecode) would close off the redefinition story.

## Tier 1: Load-then-poke (PoC)

Smallest end-to-end artifact. **No new declarations at the
prompt, no redefinition, no forward refs.** Loads a `.bn`
file/program the normal way, then drops into a prompt that
accepts only **immediate-mode** entries against the loaded
scope.

**Framing.** This is a PoC, not a product. Its job is to
validate that the incremental parse → typecheck → IR-gen →
lower → exec pipeline actually works against a long-lived VM
state. UX quality is explicitly out of scope: line input via
`bootstrap.Read` (or equivalent), no editing, no history, no
completion, no syntax highlighting. Output is whatever
`println` can render. If the architectural pieces work, the
PoC succeeded. UI polish is independent work that doesn't gate
anything else and can land separately whenever we feel like
it.

### Behavior

- `cmd/bni <module> --repl` (or `cmd/bnrepl <module>` — see
  Open Question below) loads `module` via the normal loader
  path, then enters the prompt.
- Prompt accepts:
  - A bare expression (parses as a single `ast.Expr`): wrap in
    `println(...)` for primitives / char slices; for other
    types, print a placeholder (`<value of type X>`) until the
    pretty-printer (Tier 1.5) lands.
  - A bare statement list (anything else): run for side
    effects, no auto-print.
- Each entry: parse → type-check against the loaded module's
  scope → IR-gen as a synthetic `__repl_<n>()` function → lower
  → call. Discard the result (or print, per above).
- Errors at parse / type / runtime are caught, printed, and
  control returns to the prompt. Nothing in the loaded module
  is affected by an error in immediate mode.
- Ctrl-C cancels the current input; Ctrl-D exits.

### Concrete entry points to add

(These are working names; finalize during implementation.)

- `pkg/parser`: `ParseExpr(p) @ast.Expr` and `ParseStmtList(p)
  @[]@ast.Stmt` exposed in the `.bni`. Implementations exist
  internally.
- `pkg/types`: `CheckExprInScope(c, e, scope) @types.Type` and
  `CheckStmtListInScope(c, ss, scope)`. The first returns the
  inferred type so the prompt can decide auto-`println`.
- `pkg/ir`: `GenSyntheticFunc(name @[]char, body @[]@ast.Stmt)
  @ir.Func` — wraps a stmt list as a function with no params,
  optional single int / @[]char return.
- `pkg/vm`: `LowerOneFunc(vm @VM, m @ir.Module, f @ir.Func)
  @VMFunc` — the per-function half of the existing
  `LowerModule`. Appends to `vm.Funcs` and returns the new
  `@VMFunc` (so the prompt can call it directly without going
  through `LookupFunc`).
- `pkg/vm`: `CallByVMFunc(vm @VM, vmf @VMFunc, args @[]int)
  int` — convenience wrapper that constructs the initial frame
  from a `@VMFunc` directly. (Currently `CallFunc` looks up by
  name; we want to skip the lookup for the just-lowered repl
  function.)
- `cmd/bni` (or new `cmd/bnrepl`): the REPL driver itself.
  Reads lines, dispatches expr vs. stmt-list, formats results.

### Pretty-printing — DEFERRED (gated on interfaces)

A real pretty-printer for arbitrary values (structs, managed
pointers, etc.) needs **either** per-type `Format(self) @[]char`
methods dispatched through an interface, **or** a megalithic
type-switch over `pkg/types.Type` — and the latter is the kind
of thing interfaces exist to avoid. The PoC therefore relies on
`println` only, which currently covers primitives and char
slices. Bare-expression input that doesn't have a directly
`println`-able type either prints a placeholder (e.g. `<value
of type X>`) or refuses to auto-print and tells the user to
extract / call something explicit.

When interfaces (and possibly generics) land, a `pkg/replprint`
or similar package becomes worth designing — and it should be
designed alongside the broader standard-library effort, not as
a one-off bolted onto `pkg/bootstrap`. Until then, this stays
out of the critical path.

### Out of scope for Tier 1

- New top-level declarations at the prompt (Tier 2).
- Forward references / pending validation (Tier 3).
- Redefinition (Tier 4).
- Mid-session imports (Tier 5).
- Hot-swap into compiled binaries (interop, separate plan).

## Tier 2: Add new top-level decls at the prompt

The prompt accepts `func` / `type` / `var` / `const` decls in
addition to immediate-mode entries.

### What's new

- Per-decl pipeline entry points: `pkg/parser` exposes
  `ParseDecl(p) @ast.Decl`; `pkg/types` exposes
  `CheckDeclInScope(c, d, scope)`; `pkg/ir` exposes
  `GenDecl(d, m)`. Each appends to a long-lived "REPL session"
  scope and ir module.
- `vm.Funcs` grows by one entry per func decl (already supported
  mechanically).
- `materializeGlobals` factored so it can take a single new
  global.
- Type registration: when a new `type T struct { ... }` is
  declared, the type info needs to be addable to whatever
  registry the layout / dtor machinery uses. (Today this is
  mostly compile-time; need to confirm.)

### Still out of scope

- Forward refs (a func body referencing an as-yet-undefined
  func is still an error at definition time).
- Redefinition (repeating a name is still an error).

## Tier 3: Forward references / pending validation

Real new semantic infrastructure. The type checker grows a
"pending" queue: when a declaration references a name that
isn't bound yet, the decl is parked rather than errored. When
the dependency binds, parked decls re-attempt validation.

### What's new

- Pending queue in `pkg/types` (per-checker, per-session).
- Re-attempt-on-bind hook.
- Use of a still-pending decl reports at the call site, not at
  decl time.
- Type checker invariants need pinning: pending decls don't
  participate in scope lookups; pending types don't have layout.

### Open questions

- What does "pending" mean for a `type` (vs a `func`)? A
  pending struct definition can't have its size computed; uses
  of it are themselves pending until layout exists.
- Does the user see anything? E.g. should the prompt indicate
  `f` is currently pending on `g`? Probably yes — print a
  one-liner at decl time.

## Tier 4: Redefinition

Both halves have shipped (2026-05-01).  See "Tier 4 replace path
landed" and "Tier 4 shadow path landed" above for the detailed
state; the pre-implementation sketches below are preserved for
context.

### Replace path (compatible — same sig/type) — LANDED

- Find existing `vm.Funcs` entry by qualified name.
- Generate the new VMFunc.
- Swap `vm.Funcs[idx]` to point at the new VMFunc.
- The old `@VMFunc` stays alive via refcount if anything still
  holds it; otherwise it's freed.

### Shadow path (incompatible — different sig/type) — LANDED

- Append the new VMFunc.
- The shipped implementation took a different route from the
  original "LookupFunc returns latest" sketch: instead of
  changing LookupFunc semantics, it **freezes per-call-site
  resolution at lowering time** by eager-filling `CallCache`
  (substrate commit `9af2d56`).  The funcIndex hash points
  the name at the new (later) idx, so freshly-lowered code
  resolves to the new VMFunc; old code's eager-filled
  `CallCache` slots still hold the old idx and route to the
  old VMFunc.  Same end-state as latest-match; cleaner because
  it leaves `LookupFunc` semantics unchanged.
- Refcount probe at shadow time: not yet implemented (warning
  fires unconditionally; conditional variant is a follow-up).

### Forced-shadow escape hatch

Syntax TBD per `claude-notes.md`. Not blocked on anything in
this plan; lands when the syntax pins down.

## Tier 5: Mid-session imports

`import "pkg/foo"` at the prompt loads `pkg/foo` (and its
dependencies) incrementally.

- Loader entry point: `LoadOnePackage(l @Loader, path @[]char)`
  alongside the current top-level driver.
- Type checker: register the new package's exports in the REPL
  session scope.
- IR-gen / VM lowering: `LowerModule` already handles
  add-another-module; just needs to be re-runnable mid-session.

## What to do now

In rough priority order. Each item is independently shippable.

1. **Audit doc** (this file). Keep it current as the picture
   firms up.
2. **Name → idx hash on `vm.Funcs`.** Removes the perf argument
   for ever baking idx into bytecode, which is the property
   that makes redefinition free. Estimate: ~50-line change,
   plus tests. Useful for non-REPL workloads too.
3. **Per-decl entry points exposed opportunistically.** When
   `pkg/parser`, `pkg/types`, or `pkg/ir` are touched for
   unrelated reasons, add the per-decl public surface as part
   of the change. Each shrinks Tier 1/2 work later. Doesn't
   need to be a single project.
4. **Tier 1 PoC.** Once (2) and a critical mass of (3) are
   in, build the load-then-poke loop. Targets architectural
   validation, not UX. See "Tier 1" above for entry-point
   names and concrete steps.

Items deferred (gated on language progress, mostly interfaces):

- **Pretty-printer** — see "Pretty-printing — DEFERRED" above.
- **Richer I/O** (input editing, completion, history) — gated
  on standard-library design, which is gated on interfaces +
  probably generics.
- **Tier 2+** (new decls at prompt, forward refs, redefinition,
  mid-session imports) — language-semantic work; sequence with
  the function-values plan and the broader interop story.

## Adjacencies and pressure-tests

- **`plan-function-values.md`**: when it moves out of DRAFT,
  add an explicit clause: "a function value is a stable
  identity for *what it refers to*, not for the bytes of the
  underlying body. Re-binding the body of an interpreted
  function does not invalidate function values pointing at
  it." This is required for hot-swap (Tier 6+ /
  interop scope) but should be locked in regardless.
- **Compiler/interpreter interop** (claude-todo.md):
  interpreted-package descriptors are mutable; compiled ones
  are read-only. Sorted-by-mangled-name layout interacts with
  "add a new exported function mid-session" — positions move
  when a new export sorts in. Confirm that's the intended
  behavior when the interop design doc is written.
- **`layout-extraction-plan.md`**: the layout layer must
  expose a runtime-extensible type universe, not a
  closed-at-startup one.
- **IR/backend cleanup**: no closed-world assumptions in the
  shared layer.

## PoC non-goals (explicit)

State up-front so they don't get re-litigated during
implementation:

- **Line editing / history / completion / syntax highlighting**.
  Use `bootstrap.Read` (or whatever's available) for raw line
  input. If terminal handling on the host is annoying, accept
  the suboptimal experience.
- **Pretty-printing of arbitrary values.** Output is whatever
  `println` can render today.
- **Fancy error messages.** Print the parser/checker diagnostic
  as-is and return to prompt. Pretty-error work is unrelated.
- **Multi-line input.** Single-line entries only, at least to
  start. If a single line doesn't tokenize / parse, error and
  re-prompt. (Multi-line could come later as a small UI win.)
- **Performance tuning.** The PoC is for correctness, not
  throughput.

## Open design questions

- **REPL driver: separate `cmd/bnrepl` or `--repl` flag on
  `cmd/bni`?** Suggested: `--repl` flag for now (one binary,
  shared loading paths). Spin out a separate command if/when
  the REPL grows enough surface to justify it.
- **Top-level prompt grammar**: bare expression vs. bare
  statement list vs. either? Suggested convention above —
  single-expr → auto-`println`-wrap (when `println` can
  handle the type) or placeholder, otherwise stmt list with
  no auto-print.
- **What does the PoC do for non-`println`-able expression
  results?** Options: print a placeholder, refuse to auto-print
  and tell the user to extract a primitive subfield, or simply
  not auto-wrap (require explicit `println` call). All three
  are fine for a PoC; pick one and move on.
- **Sentinel for "no result"**: probably nothing (just return
  to the prompt).
- **Error recovery**: parse / type / runtime errors in
  immediate mode print and return to prompt; nothing in
  retained mode is affected. Probably uncontroversial.
- **Type registration mechanics for new types declared at the
  prompt** (Tier 2): what data structure today owns the
  authoritative list of struct types and their dtors? Needs to
  be appendable.
- **Pending visibility** (Tier 3): does the user see "`f`
  pending on `g`" at decl time? Probably yes.
- **Session save/restore**: out of scope. The save case is
  serializing the heap, which is enormous and unrelated.
  Sessions are process-bound.

## Cross-references

- `claude-notes.md` § "Forward references & REPL model —
  DECIDED" — language semantics, authoritative.
- `claude-discussion-detailed-notes.md` § 9 (forward refs and
  REPL model) and § 11 (dual-mode interop), § 23 (REPL
  redefinition revised).
- `claude-todo.md` § "REPL — start now, interpreter-only" —
  the rolling status / forcing-function entry that points
  here.
- `plan-function-values.md` — orthogonal at the frontend,
  paired at the backend (function-value identity stability is
  a hot-swap prerequisite).
- `claude-todo.md` § "Compiler/interpreter interop — MAJOR
  PROJECT" — the broader work that compiled-mode REPL
  features depend on.
