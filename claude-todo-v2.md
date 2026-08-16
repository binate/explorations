# Binate TODO — v2 (deferred / post-1.0)

Work deliberately deferred past the current milestone. These are NOT active items
for [claude-todo.md](claude-todo.md) — they are documented limitations or
enhancements to revisit in a later version. Finished/declined items still go to
[claude-todo-done.md](claude-todo-done.md); the active, current-milestone todo is
[claude-todo.md](claude-todo.md).

---

## Package-`var` init ordering: follow named-function calls (Go-complete dependency order)

Package-level `var` initializers run in **dependency order**, but the ordering
graph is built from **direct syntactic reads** only (identifiers in the initializer
expression, plus an immediately-invoked function-literal body) — it does **not**
follow calls to **named functions**. So `var a = f()` where `f` reads package var
`b` does not order `a` after `b`, and `a` can observe `b` at its zero value. Go's
initialization dependency analysis DOES follow function bodies reachable from an
initializer.

This is a **documented limitation** (accepted for v1), specified as such in §17.2
`prog.init.order` / `prog.init.var-cycle` (the edge definition is scoped to direct
reads, and the "not a zero" guarantee is likewise scoped). Landed decision +
implementation: `done/plan-var-init-dependency-order.md` (`444c9c90`); spec corrected in
`85a70ff`. The impl's own note lives at `pkg/binate/types/check_var_resolve.bn`
(the "transitive-through-functions gap").

**v2 work:** extend `collectVarDeps` to follow calls to same-package named functions
(transitively) when building the init-order graph, so an initializer observes the
initialized value of any package var its callee reads — and so a cycle through a
called function is diagnosed rather than silently mis-ordered. When done, tighten
§17.2 to drop the "reached only through a named-function call … is not ordered"
carve-out.

---

## `iface.construct.value-borrow`: indirect escape of a borrowed `*any` is not caught

A **documented limitation** (accepted for v1; found 2026-07-29). The implicit
value→`*any` borrow (`iface.construct.value-borrow`) is admitted only at BORROWING
positions (argument / var-init) and the checker rejects the DIRECT escape
(`return "hi"`, `v = "hi"`, `b.field = "hi"` — all rejected). But the INDIRECT escape
slips through: once the borrow lands in a `*any` variable, copying that variable out of
scope dangles it — `func g() *any { var v *any = "hi"; return v }` compiles, and the
caller then reads a `*any` box that borrows `g`'s dead frame temp (UAF: an empty/garbage
recover).

This is **consistent with Binate's raw-pointer semantics** (no escape analysis;
`func g() *int { var n int = 5; return &n }` compiles the same way — user error via the
escape hatch) and affects **scalars too** (`var v *any = n; return v`), not just string
literals — so it is NOT specific to the `9d04870b` string-literal box, which merely
changed the dangling profile from "points at static rodata" to "points at a frame
alloca". The direct-form rejection is a cheap syntactic gate, not a real lifetime check.

**v2 work:** make the escape gate CONSISTENT — either extend the direct-escape rejection
to catch the indirect case (a real escape/lifetime check scoped to the implicit
value-borrow — warranted because the borrow is INVISIBLE, unlike an explicit `&n`), or
drop the direct gate to match raw-pointer semantics. Genuinely low priority: a full
lifetime check runs against Binate's no-escape-analysis design, which is why it's
deferred rather than active. String literals should ride the SAME mechanism as scalars
whichever way it goes. Until then this is the documented raw-borrow behavior.

---

## Exhaustiveness checking for `Kind`/`Op` tagged-union dispatch

Deferred from the active todo (2026-07-17) after scoping showed the payoff is
modest relative to the machinery + ongoing annotation discipline it needs.

**Motivation.** Binate has NO switch/exhaustiveness checking. Adding a new
`EXPR_`/`STMT_`/`DECL_`/`TEXPR_`/`OP_` kind means hand-finding every `switch`/if-chain
that must handle it; a missed site silently falls through (`codegen/emit_instr.bn`
emits a literal `; unhandled op N` comment and returns). Surfaced by the 2026-07-16
"use interfaces more" survey as the cheap alternative to interface-ifying the AST/IR
(candidate 2 there) for the one real safety payoff.

**Findings from scoping (why it's deferred, not built):**
- The simple "a `switch` with no `default` must be exhaustive" heuristic is
  **unusable**: the data shows the vast majority of default-less kind/op switches are
  *deliberately partial* (`emit_alloca_hoist` handles 10 of ~72 opcodes; the asm
  operand switches handle 3 of N kinds; each `vm_exec_*` file dispatches a category).
  Flagging them would be pure noise. So exhaustiveness checking **must be opt-in**.
- There are only ~41 `switch .Kind/.Op` sites; the other ~2200 dispatch sites are
  `.Kind ==` **if-chains** (much harder to analyze; a first cut would cover switches
  only).
- **No enum/sum type exists.** Kinds are plain `int` consts (`EXPR_IDENT = 0`, …);
  `ast.Expr.Kind` is typed `int`, not `ExprKind`. So "the EXPR family = these N
  consts" is a *naming convention*, not something the language/checker knows — every
  route must define the family by convention (prefix) unless real enum types are
  introduced first (see the enum-types item below).

**Two routes (when revisited):**
- **A — `bnlint` rule + `// bnlint:exhaustive` marker** (mirrors the existing
  `// bnlint:allow` directive; lint ctx already carries a `@types.Checker`). For a
  *marked* switch on a convention-defined kind family, flag any missing family
  member **even if a `default` exists** (that's the point). Non-invasive, no BUILDER
  bump, incremental adoption; runs at lint/CI time only; the marker is a magic
  comment. Delivers value only after switches are annotated.
- **B — compiler feature (exhaustive `switch` the checker enforces).** Only genuinely
  first-class if bundled with real enum/named-kind types (below); without them it
  degrades to "a keyword + the same prefix convention" — strictly more machinery than
  A (grammar/parser/checker/spec + BUILDER bump + language-semantics sign-off) for the
  same convention-based check. Its one edge (compile-time, every-build enforcement)
  only materializes with the enum investment.

## Enum / named-kind types (replace the `int`-const kind families)

A standalone language project (filed 2026-07-17 out of the exhaustiveness scoping).
Introduce real `enum` (or named-int sum) types so the ~138 kind constants
(`EXPR_*`/`STMT_*`/`DECL_*`/`TEXPR_*` + the ~72 `OP_*`) become closed types instead of
bare `int` consts distinguished only by a naming convention.

**Why it's worth doing on its own merits:**
- Types the ~2200 `.Kind ==`/`switch .Kind` dispatch sites against a real family
  instead of `int`, so the checker knows the closed set.
- Makes **exhaustiveness checking fall out for free** (route B above becomes natural:
  an exhaustive `switch` over an enum errors on a missing variant at compile time).
- Removes a class of bugs (assigning an unrelated `int`, or an `OP_*` where an
  `EXPR_*` is meant — currently both just `int`).

**Cost (why v2, not now):** a large type-system + migration effort — design the enum
type (representation, assignability, switch semantics), retype the kind fields
(`ast.Expr.Kind` etc.) and the const declarations, and thread the named type through
every dispatch/producer site. Also a BUILDER-compat staging concern (cmd/bnc's own
tree can't use the new type until a BUILDER ships it). Expression-problem note (see
the "use interfaces more" done entry): tagged-union+switch stays the right dispatch
shape for a pass-heavy compiler; this is about *typing* the tag, not replacing the
dispatch with vtables.

## RTTI: make the design-D registry the single seam for the `__typeinfo.<T>` symbol

A robustness nicety left over from the (now-complete) type-assertions / type-switch /
RTTI work — see the "Type assertions, type switches & RTTI — COMPLETE" done entry.
Deferred here because nothing wants it any time soon.

The `__typeinfo.<T>` record symbol is produced at ~5 independent
`mangle.TypeInfoName(RecvPkg, RecvTypeName)` call sites: the vtable slot-1 writers
`collectImplVtableSlots` (LLVM, `emit_impls.bn`) and `collectImplVtableSlotsNative`
(x64 / arm32 / aarch64 `*_iface.bn`), plus the record builder `buildTypeInfoDesc`
(`ir/data_typeinfo.bn`, → `desc.Sym`). The tightening: store the symbol once on the
`TypeInfoEntry` and have both ends read it through a single registry accessor (e.g.
`ir.TypeInfoSymFor`), so the "record symbol == slot-1 reference" invariant holds by
construction rather than by call-site agreement.

**Why v2, not now:** the invariant already holds today — every site calls the SAME
pure `mangle.TypeInfoName` with the same receiver-identity pair, so the risk is low.
And the fix is a cross-backend change (one new registry accessor + 5 call sites across
all four backends + VM, each needing verification) for a robustness nicety, not a
correctness fix.

## x64 inline RefInc/RefDec (retire the `rt.RefInc`/`rt.RefDec` runtime calls)

The refcount-inlining project (see `done/plan-refcount-inlining.md`) inlined the
hot `RefInc`/`RefDec` paths on LLVM codegen, aarch64, arm32, and the bytecode VM
(header nil-check + load/add/store, no runtime call). The **x64 native backend is
the one holdout** — `emitRefInc`/`emitRefDec` in `pkg/binate/native/x64/x64_managed.bn`
still emit a slow-path `call bn_rt__RefInc` / `bn_rt__RefDec`, so those two runtime
functions (`impls/core/.../pkg/builtins/rt/rt.bn`) can't yet be retired.

**v2 work:** give x64 an inline RefInc/RefDec fast path mirroring aarch64
(`aarch64_refcount.bn`'s CBZ/nil-check + inline header mutation). Once x64 inlines,
`rt.RefInc`/`rt.RefDec` have no remaining caller and can be deleted along with their
`.bni` decls. A perf + cleanup optimization, deliberately deferred as a follow-on.

## Embeddable VM Inc 6 — cross-target compilation in one process

From `done/plan-embeddable-vm.md` (v1 landed increments 1–5; the interpreter is
reentrant for a SINGLE target). `types.target` (`pkg/binate/types/layout.bn:9`, set
via `SetTarget`) and the predeclared-type singletons stay process-shared and
immutable-after-init, so two sessions targeting DIFFERENT targets in one process
would cross-talk through `target`. Fix: thread the target (and any target-derived
layout state) through the session (`@Checker`/`@Module`/`@VM`), mirroring the
increment-1–5 threading, instead of the global. Deferred with user sign-off
(2026-06-16) — only if in-process cross-compilation becomes a goal.

## Embeddable VM Inc 7 — AOT-compiler reentrancy (codegen/native globals)

From `done/plan-embeddable-vm.md`. v1 made the INTERPRETER path reentrant; the ~17
`pkg/binate/codegen/*` + `pkg/binate/native/*` process-globals (off the `cmd/bni`
path — `cmd/bni` imports ir/loader/types/vm, not codegen/native; "Group E" in the
plan's inventory) were left in place. To embed the COMPILER reentrantly (e.g. an
in-process bnc), thread those through the emit context the same way increments 1–5
did for the interpreter. Deferred with user sign-off — only if reentrant AOT
compilation in one process becomes a goal.
