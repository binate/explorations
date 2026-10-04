### A type-parameter assertion target (`x.(*T)`) is accepted although §11.12 says it is rejected — `f[@Node]` recovers a Node cell as `*(@Node)` — DONE (binate `ab98a0439`, 2026-10-02, another session; confirmed 2026-10-03, work-3)

`func f[T any](x @any) bool { _, ok := x.(*T); return ok }` with `var a @any = n` (n `@Node`) and
`f[@Node](a)` compiles on builder-comp and prints `true`: the checker sees TYP_TYPE_PARAM (not in the
alias-target rejection list), checks the generic body once abstractly, and IR-gen builds the
instantiated target `*(@Node)`, which typeInfoSymFor keys on `main.Node` — matching a plain `@Node` box and
recovering its Node cell as a `*(@Node)` (the same type confusion the alias-target fix closed).  The spec
(§11.12 `iface.assert.typeparam`, Draft) specifies per-instantiation semantics but says "Today an assertion
whose target names a type parameter is rejected at the generic declaration" — the checker does not reject
it.  Decide: (a) reject a TYP_TYPE_PARAM target in assertTargetType now, with its own message (first
check nothing in the tree or the conformance suite asserts on a type parameter), or (b) implement the
Draft rule (per-instantiation checking — design B).
Decision (user, 2026-10-03: "3, 5, 8, 9: go with your recs (though for 9 probably bnlint should complain about it)"): implement the Draft per-instantiation rule (§11.12 `iface.assert.typeparam`) — instance bodies are now checked per instance (gen.mono.check), so the instantiated target is checked as a concrete one; if that turns out larger than expected, reject a type-parameter target at the definition instead (with its own message).  Tests: `f[@Node]` recovering a Node cell as `*(@Node)` rejected or missed per the concrete rules; a valid instantiation recovering correctly.

Resolved by binate `ab98a0439` ("check: an assertion or conversion through a type parameter is checked per instance"), which implements iface.assert.typeparam as decided.  Confirmed 2026-10-03 on current main: `f[@Node](a)` with `x.(*T)` is rejected at the instance — "type-assertion target names a type parameter whose type argument makes it `*@Node`, which is not an assertion target".

