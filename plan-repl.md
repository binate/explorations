# Plan: REPL (Interpreter-Only)

> **Status: DRAFT** — initial sketch for review. Tier 1 is concrete
> enough to start; later tiers are intentionally less specific and
> will be filled in as we go. Compiled-mode REPL features
> (hot-swap of interpreted functions while a compiled binary
> runs, package descriptors, cross-mode trampolines) are
> explicitly out of scope here — they belong to the broader
> compiler/interpreter interop work and depend on
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
- An interpreter-only REPL is the smallest artifact that
  demonstrates Binate's dual-mode promise as a usable thing
  rather than a design claim.

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

## Tier 1: Load-then-poke

Smallest useful thing. **No new declarations at the prompt, no
redefinition, no forward refs.** Loads a `.bn` file/program the
normal way, then drops into a prompt that accepts only
**immediate-mode** entries against the loaded scope.

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

### Pretty-printing (Tier 1.5)

Promote the implicit `println` of bare expressions into a real
pretty-printer:

- New package, e.g. `pkg/replprint`. Built on `pkg/buf.CharBuf`.
- Knows about: int family, bool, char-slice (`*[]char`,
  `@[]char`), structs (recursive, with field names), arrays,
  slices (raw and managed), pointers (raw and managed; print
  the address and the pointee, with cycle detection deferred).
- Public entry point `Format(t @types.Type, value int)
  @[]char` — the prompt calls this on the result of an expr
  evaluation.
- Out of scope for v1: function values (don't exist yet),
  interfaces (don't exist yet), generics (don't exist yet),
  cycles in pointer graphs.

This is independently useful (debugger output, test failures,
diagnostic dumps), and decoupling it from the REPL means we can
land it on its own schedule.

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

### Replace path (compatible — same sig/type)

Mostly mechanical given `BC_CALL` resolves by name per call:

- Find existing `vm.Funcs` entry by qualified name.
- Generate the new VMFunc.
- Swap `vm.Funcs[idx]` to point at the new VMFunc.
- The old `@VMFunc` stays alive via refcount if anything still
  holds it; otherwise it's freed.

### Shadow path (incompatible — different sig/type)

- Append the new VMFunc.
- `LookupFunc` semantics change: return *latest* match by name,
  not first. (Or: layer a REPL-side name-table that maps name
  → idx and shadows on assignment, leaving `vm.Funcs` purely
  positional.)
- Refcount probe at shadow time: if the old VMFunc has > 1 ref,
  print a warning that outstanding references exist.

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

## What's "free" — should-do-now-anyway

Independent of when REPL implementation actually starts, the
following are short, low-risk, and unambiguously useful for
both REPL and interop:

1. **Name → idx hash on `vm.Funcs`.** Removes the perf argument
   for ever baking idx into bytecode. Estimate: ~50-line
   change, plus tests.
2. **Per-decl entry points exposed opportunistically.** When
   `pkg/parser`, `pkg/types`, or `pkg/ir` are touched for
   unrelated reasons, add the per-decl public surface as part
   of the change. Each shrinks Tier 2 work later.
3. **Pretty-printer (`pkg/replprint`).** Useful for debugger
   output, test diagnostics, and arbitrary value inspection.
   Doesn't depend on any REPL infrastructure; can land first.
4. **Audit doc** (this file). Keep it current as the picture
   firms up.

These can land in any order, as separate PRs / commits.

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

## Open design questions

- **REPL driver: separate `cmd/bnrepl` or `--repl` flag on
  `cmd/bni`?** Suggested: `--repl` flag for now (one binary,
  shared loading paths). Spin out a separate command if/when
  the REPL grows enough surface to justify it.
- **Top-level prompt grammar**: bare expression vs. bare
  statement list vs. either? Suggested convention above —
  single-expr → auto-`println`-wrap, otherwise stmt list with
  no auto-print.
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
