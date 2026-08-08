# Plan: REPL (Interpreter-Only)

> **Status: COMPLETE (shipped).** All five tiers are functional
> end-to-end; `bni --repl <file.bn|dir>` ships.  Kept for design
> rationale, decided semantics, and the few remaining open
> follow-ups (see "Remaining open items" below).
>
> Compiled-mode REPL features (hot-swap of interpreted functions
> while a compiled binary runs, package descriptors, cross-mode
> trampolines) are explicitly out of scope here — they belong to
> the broader compiler/interpreter interop work and depend on
> `plan-function-values.md`.

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
  Bytecode stores a per-VMFunc strings index for the qualified
  callee name, and `LookupFunc` walks `vm.Funcs` by name on every
  call. Replace-redefinition becomes an in-place body swap;
  shadow-redefinition is append-then-pick-latest.
- **`vm.Funcs` is already incremental.** `LowerModule` is called
  per-module and appends; multiple modules already coexist in one
  VM with their own preserved string pools (earlier behavior
  overwrote `vm.Strings` and broke cross-module string indices,
  since fixed). Globals are also append-only via
  `materializeGlobals`.
- **`@VMFunc` is managed**, so old function bodies stay alive
  via refcount once anything else holds them. The substrate for
  shadow-redefinition is already in place.
- **Parser has per-decl / per-expr entry points internally.**
  `parseFuncDecl`, `parseTypeDecl`, `parseVarDecl`,
  `parseConstDecl`, `parseExpr` are internal today (called from
  the file-level driver) but they exist.
- **Type checker is module-shaped at entry but per-decl
  internally.** `Check(c, file)` iterates declarations.
- **IR-gen is module-shaped at entry but per-decl internally.**
  `GeneratePackage(file, m)` iterates declarations.

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

## Key implementation decisions and gotchas

These are the design choices and non-obvious notes that are the
sole written record; the per-tier landing transcripts they came
from have been removed.

### `CheckMainPersistent`

Added to `pkg/types` (not in the original plan).  Reason:
`Check()` pushes a scope and pops it on return, so the loaded
file's symbols vanish from `c.Scope` — prompt entries can't see
them.  `CheckMainPersistent` does the same work without the
trailing `popScope`, so the file's scope stays installed for the
REPL session.

### `CallCache` and eager-fill (replaces the planned name→idx hash)

The planned "name → idx hash on `vm.Funcs`" was instead realized
as a per-VMFunc `CallCache` memoization for `BC_CALL` /
`BC_FUNC_ADDR`.  Both solve the same root problem (the perf
argument for ever baking idx into bytecode, which would close off
the redefinition story).  The cache is per-VMFunc, parallel to
`Names`, and explicitly designed to be invalidated on REPL
mutation of `vm.Funcs`.

The cache was originally **lazy-filled**, which was correct for
the non-REPL world where `vm.Funcs` never changes after load, but
made shadow semantics unfixable — an old caller whose `CallCache`
slot was still unresolved when its callee got shadowed would pick
up the new (incompatible) callee on its next execution.  The fix
was to **eager-fill** `CallCache` at lowering time, which freezes
the binding.  The cost is covered by making `LookupFunc` O(1) via
an open-addressing string→int hash (`pkg/vm/func_index.bn`, djb2,
linear probing, resize at 75% load).

`LowerModule` is two-pass (build the index for all funcs, then
eager-fill CallCache); `LowerOneFunc` appends/replaces then
eager-fills.

### Replace vs. shadow

- **Replace (compatible sig):** in-place rebind at the existing
  `vm.Funcs` idx.  Same N, just points at a new VMFunc, so cached
  indices stay valid and the per-call-site `CallCache` needs no
  flush.  Old callers see the new body; the old `@VMFunc` is
  freed once nothing holds it.
- **Shadow (incompatible sig):** `LowerOneFuncShadow` always
  APPENDS to `vm.Funcs` and re-points the funcIndex to the new
  (later) idx.  Freshly-lowered code resolves to the new VMFunc;
  old callers' eager-filled `CallCache` slots still hold the old
  idx and route to the old VMFunc.  This is the same end-state as
  the original "LookupFunc returns latest" sketch but cleaner —
  it leaves `LookupFunc` semantics unchanged.  `Checker.AllowRedef`
  suppresses `checkBniSignatureMatch` on the prompt path (the
  compile-time path keeps the strict check).

Methods follow the same replace/shadow rules, keyed on the
qualified `<pkg>.<TypeName>.<Method>` name.
`SetOrAppendMethod(t, m)` replaces a same-named method on
`t.Methods` in place (sibling to `AddMethod`, which still refuses
duplicates for the file-load path).  The shadow warning prints
the qualified `Counter.Add` form for methods, bare name for free
funcs.

### Var-initializer evaluation: entry wrapper instead of a C runtime change

`var x T = expr` actually evaluates `expr` before any subsequent
code runs (previously both the file-load path and the REPL
silently dropped the initializer).  The chosen mechanism: a
per-package synthetic `<pkg>.__init`, a per-binary
`<main>.__init_all` dispatcher run in dep order, and a
`<main>.__entry` wrapper that runs the dispatcher before
`main.main`.  The REPL runs a one-shot synthetic per
prompt-typed `var x T = expr`.

Why an entry wrapper and not a C runtime change: the C runtime is
a temporary scaffold for the pure-Binate end state.  Adding init
dispatch into `int main()` would tie a Binate-level concept to
the C contract.  Instead, a one-time mangler change moves the
entry into Binate:

- `main.main` no longer special-cases to `bn_main`; it mangles
  like any other free function (`bn_main__main`).
- A new special case `main.__entry → bn_entry` reserves a stable
  symbol for the entry wrapper.
- C runtime calls `bn_entry()` (one-line update); Binate's
  `<main>.__entry` does init dispatch + main.

Future entry-time concerns (panic / signal handler setup,
finalizers, etc.) all live in Binate behind the `bn_entry`
symbol — the C side never has to change again for this kind of
reshuffle.

### Untyped var inference

`var x = expr` (no explicit type) infers from the initializer
**literal**: int / bool / `@[]const char` / char / float64.
Non-literal initializers (`var x = i + 100`, `var y = foo()`)
still need an explicit type — the type checker resolves those for
local vars, but threading it through IR-gen for top-level vars
wasn't worth the surface for this common case.  Users spell the
type explicitly when needed.

### Managed-field structs and body-introduced dtor regen

Prompt-typed `type T struct { ... }` with managed fields reuses
the existing `genStructDtorWithName` / `genStructCopyWithName` /
`ensureMsDtor` / `ensureArrayDtor` / `ensureArrayCopy` helpers
(same ones the file-load passes call).  The only difference is
the dedup scope: `ensureReplStructHelpers` targets one struct at
a time and pre-populates the per-call `generated` slice from
`m.Funcs` via `collectExistingHelperNames` (which strips the
`NewFunc`-applied package qualifier so dedup compares
unqualified-to-unqualified).

The *body*-introduced shape case is the subtle one: a
prompt-typed func body, var-init, or bare stmt list that uses an
aggregate shape with a *destructible element* not previously seen
by the loaded module (e.g. `@[]@Bag`).  Without handling it, the
body's end-of-statement RefDec hits
`vm: extern not found: <pkg>.__dtor_ms_mp_Bag`.
`EnsureReplBodyHelpers(m)` drains `pendingMsDtors` /
`pendingStructDtors` (populated during the body's IR-gen) into
freshly-emitted helpers, mirroring the
`generateNonStructDtors` / `generateCopies` drain that file-load
runs at end-of-module.

**Gotcha:** `@[]int` does NOT trigger this — an `int` element
isn't destructible, so `emitManagedSliceRefDec` short-circuits to
a plain `RefDec(refptr)` on the backing block and never calls
`registerPendingMsDtor`.  The bug only fires when the element
type itself needs destruction (managed pointer, managed slice, or
array of destructible).

**Lowering order matters:** helpers must be lowered BEFORE the
body so the body's eager `CallCache` fill resolves every helper
name on first pass.  (`backfillExternCachesForName` would upgrade
the -1 slots once the helper landed, but the explicit order
avoids the round-trip.)

### Forward refs (Tier 3)

A func decl whose body references not-yet-bound names parks
rather than erroring: the sig stays in scope, the body is
deferred, and the prompt prints `function f parked (pending: g)`.
Defining the missing name retries every parked decl and prints
`function f resolved` when the body type-checks.  Use-site calls
to a still-parked func surface a clean type-check error
(`function f is unresolved`) instead of an opaque runtime "extern
not found".

Key invariants: pending decls' SIGS DO participate in scope
lookups (so other code can reference the parked func by name);
only body-level resolution is deferred.  `errUndefined` /
`addCheckError` honor `TentativeMode` — undefined-name capture
goes to `TentativeMissing`, all other body-check errors route to
`TentativeErrors` (so cascading "cannot call non-function" /
"arithmetic op requires numeric" follow-ups don't surface as
separate user-visible errors).

`backfillExternCachesForName` — when a freshly-lowered VMFunc's
name enters the funcIndex, walk all earlier-lowered VMFuncs and
upgrade any -1 ("extern unknown") CallCache entries that match.
Solves the "caller lowered before callee available" problem
(which Tier 3 hits when a pending callee finally resolves).
Tier 4 shadow correctness is preserved because only -1 entries
are touched.

Pending types / vars / consts and cycle detection landed
2026-05-28 → 2026-05-29; see
[`plan-repl-tier3-pending-types.md`](plan-repl-tier3-pending-types.md).
Every top-level decl kind parks on forward-referenced deps,
use-site propagation works through sized contexts, the per-caller
sized-vs-reference distinction preserves recursive types via
pointers, and unbreakable cycles get a `pending cycle: A -> B -> A`
diagnostic at park-close time (the diagnostic only fires for
genuine cycles through sized fields; recursive type pairs via
pointers still resolve via the per-caller distinction).

### Multi-line input / paren-aware accumulator

The brace-balance accumulator is paren-aware: it tracks unclosed
`(` / `)` as well as `{` / `}`, so multi-line `const ( ... )`
blocks (and any paren-bracketed construct typed across lines) are
recognized as continuations.  Brackets inside string / char
literals and `//` / `/* ... */` comments are skipped.  The
combined depth counter is a heuristic, not a real parser:
syntactically wrong interleavings like `{(}` balance to 0 and the
parser catches them.  It does not track `[` / `]`.

### Mid-session imports (Tier 5)

`import "pkg/foo"` at the prompt loads pkg/foo (and any
transitive deps not already in the session), type-checks the
newly-loaded packages, generates IR, lowers them into the VM, and
defines the package symbol in the session scope.  Subsequent
prompt entries can call `foo.X`.

The load-bearing subtlety: `SaveAliasMapState()` /
`RestoreAliasMapState(snap)` snapshot and restore
importAliasNames/Paths.  Bracketing the per-package InitModule
loop with these is what lets the session's main alias map survive
the per-package wipes each InitModule does.  Per-import-prompt
flow: parse specs → `replLoader.LoadImports(specs)` (transitive)
→ SaveAliasMapState → iterate `replLoader.Order` skipping
already-processed packages, per new pkg run
LoadPackageInterface + CheckPackage + InitModule +
registerPkgImports + GeneratePackage + LowerModule →
RestoreAliasMapState + RecordImportPath per user-typed spec →
`c.RegisterReplImport` per top-level spec.

## Pretty-printing — DEFERRED (gated on interfaces)

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

Auto-`println` wrap of bare expressions (wrapping `1+2` at the
prompt as `println(1+2)`) is also **explicitly DEFERRED** until
interfaces / per-type `Format` dispatch lands.  `bootstrap.println`
is a temporary hack scheduled for removal; building features on
top of it (extending the printable set, AST-rewrite to inject
println) would entrench the hack and complicate the cutover.
Users type `println(...)` explicitly.

## Remaining open items

- **Refcount-aware shadow warning.** The plan calls for a warning
  conditioned on outstanding references to the OLD VMFunc.  Today
  the warning fires unconditionally on every shadow; the
  conditional variant needs a way to introspect VMFunc refcounts
  cheaply.
- **Forced-shadow escape hatch** (syntax TBD per
  `claude-notes.md`).  Not blocked on anything in this plan;
  lands when the syntax pins down.
- **Untyped non-literal `var` init at the prompt** (`var x =
  i + 100`, `var y = foo()`) still requires an explicit type
  (see "Untyped var inference" above).  Intentionally deferred.
- **Pretty-printer** — see "Pretty-printing — DEFERRED" above.
- **Richer I/O** (input editing, completion, history) — gated on
  standard-library design, which is gated on interfaces +
  probably generics.

## PoC non-goals (explicit)

State up-front so they don't get re-litigated during
implementation:

- **Line editing / history / completion / syntax highlighting**.
- **Pretty-printing of arbitrary values.** Output is whatever
  `println` can render today.
- **Fancy error messages.** Print the parser/checker diagnostic
  as-is and return to prompt. Pretty-error work is unrelated.
- **Performance tuning.** The PoC is for correctness, not
  throughput.
- **Session save/restore.** The save case is serializing the
  heap, which is enormous and unrelated. Sessions are
  process-bound.

## Adjacencies and pressure-tests

- **`plan-function-values.md`**: when it moves out of DRAFT,
  add an explicit clause: "a function value is a stable
  identity for *what it refers to*, not for the bytes of the
  underlying body. Re-binding the body of an interpreted
  function does not invalidate function values pointing at
  it." This is required for hot-swap (interop scope) but should
  be locked in regardless.
- **Compiler/interpreter interop** (claude-todo.md):
  interpreted-package descriptors are mutable; compiled ones
  are read-only. Sorted-by-mangled-name layout interacts with
  "add a new exported function mid-session" — positions move
  when a new export sorts in. Confirm that's the intended
  behavior when the interop design doc is written.
- **Layout extraction**: the layout layer must
  expose a runtime-extensible type universe, not a
  closed-at-startup one.
- **IR/backend cleanup**: no closed-world assumptions in the
  shared layer.

## Cross-references

- `claude-notes.md` § "Forward references & REPL model —
  DECIDED" — language semantics, authoritative.
- `claude-discussion-detailed-notes.md` § 9 (forward refs and
  REPL model) and § 11 (dual-mode interop), § 23 (REPL
  redefinition revised).
- `claude-todo.md` § "REPL — start now, interpreter-only" —
  the rolling status / forcing-function entry that points
  here.
- `plan-repl-tier3-pending-types.md` — Tier 3 pending
  types / vars / consts and cycle detection.
- `plan-function-values.md` — orthogonal at the frontend,
  paired at the backend (function-value identity stability is
  a hot-swap prerequisite).
- `claude-todo.md` § "Compiler/interpreter interop — MAJOR
  PROJECT" — the broader work that compiled-mode REPL
  features depend on.
