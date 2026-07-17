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
implementation: `plan-var-init-dependency-order.md` (`444c9c90`); spec corrected in
`85a70ff`. The impl's own note lives at `pkg/binate/types/check_var_resolve.bn`
(the "transitive-through-functions gap").

**v2 work:** extend `collectVarDeps` to follow calls to same-package named functions
(transitively) when building the init-order graph, so an initializer observes the
initialized value of any package var its callee reads — and so a cycle through a
called function is diagnosed rather than silently mis-ordered. When done, tighten
§17.2 to drop the "reached only through a named-function call … is not ordered"
carve-out.

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
