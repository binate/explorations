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
