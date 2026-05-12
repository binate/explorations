# Plan: Interface Embedding / Extension

**Status**: DRAFT — pending ratification.

## Context

Interface extension is in the design (`claude-notes.md` § "Interfaces"; full discussion in `claude-discussion-detailed-notes.md` § "Interface Extension"; original layout in `claude-plan-1.md` § 2.3) but not implemented. Motivation: needed for any Go-style I/O stdlib (Reader / Writer / ReadCloser / etc.).

Ratified design summary:

- **Syntax**: `interface X : I1, I2, ... { methods }`. Parents listed once before the body. No interspersing of parents and methods (unlike Go), no anonymous embedding (Binate has no anonymous interfaces). Empty body allowed.
- **Aliases vs zero-method extension**: `interface X = A` is an alias (same identity); `interface X : A {}` is a distinct interface that requires A's methods.
- **Static upcast**: `*Child → *Parent` is a nominal, compile-time-known relation — no runtime query. No structural satisfaction check exists in the language.
- **Vtable layout**: `[any-block][full vtable of (R, P1)][full vtable of (R, P2)][own methods]`, recursively. Every interface implicitly extends `any`, so every vtable starts with the any-block at offset 0. Conversion = fixed compile-time pointer offset; no swap, no lookup. Some any-block content is duplicated at each nested origin.
- **Transitivity**: `impl T : Child` implicitly registers `impl T : Parent` for every Parent in Child's ancestor closure. The `(R, Parent)` vtable is emitted; the `(R, Child)` vtable inlines it.

## Current state (pre-implementation)

- Parser (`pkg/parser/parse_decl.bn:parseInterfaceDecl`): accepts only `interface X { methods }` and `interface X = Y`. No `:` after the interface name.
- AST (`pkg/ast.bni:Decl`): `Interfaces @[]@TypeExpr` field exists (used for IMPL's interface list). Can be reused for DECL_INTERFACE's parents.
- Type checker (`pkg/types/check_interface*.bn`, `pkg/types/check_impl*.bn`): no concept of inherited methods.
- IR (`pkg/ir/gen_iface.bn`): records interface method sets and impl-table entries; no inheritance.
- Codegen (`pkg/codegen/emit_impls.bn`): one vtable per `(R, I)` pair, layout `[dtor, method1, method2, ...]`. Currently the dtor is the only any-block slot.
- Cross-package machinery (Slices 2.6–2.9): canonical (R, I) mangling, weak_odr dedup, imported-impl declarations. Works orthogonally to extension; extension just adds more `(R, I)` pairs to emit.

## Slicing

Each slice keeps tests green. Intermediate slices may "accept the syntax but reject extension at the type checker" until the codegen side is ready — that way the parser change is decoupled from the codegen change.

### Slice E.1 — Parser + AST + reject-extension

- **Scope**: parser accepts `interface X : I1, I2, ... { ... }`. AST stores parents in `Decl.Interfaces` (reused; same field that IMPL uses). Type checker rejects with "interface extension not yet implemented" so no test paths actually use the feature.
- **Files**:
  - `pkg/parser/parse_decl.bn`: `parseInterfaceDecl` — after the IDENT, optionally consume `COLON` and a comma-separated `parseInterfaceRef` list before the `LBRACE`. Reject `:` immediately followed by `=` (extension AND alias is contradictory).
  - `pkg/types/check_interface*.bn`: if a DECL_INTERFACE has non-empty `Interfaces`, emit a placeholder type error.
- **Tests**: parser unit tests (parse various extension forms, including empty body, single parent, multi-parent, qualified parent name). Type-checker unit test confirming the reject error fires. No conformance change.
- **Estimated size**: ~80 lines + tests.

### Slice E.2 — Type checker: method set propagation + impl validation

- **Scope**: type checker resolves parents, validates them (must be interfaces, no cycles, no duplicate parents, no incompatible same-name signatures), and computes the inherited method set. `impl T : Child` validation checks own + inherited methods. The reject from E.1 is removed.
- **Files**:
  - `pkg/types/check_interface*.bn` or new `pkg/types/check_iface_extends.bn`: parent resolution, cycle detection, method set computation. Probably stash the resolved parent list and the full method set on the type info (TYP_INTERFACE) so consumers don't recompute.
  - `pkg/types/check_impl*.bn`: when validating `impl T : I`, walk I's full method set (own + inherited transitively) rather than just I.Methods.
  - `pkg/types/types_query.bn` or equivalent: helper to enumerate I's ancestor closure (used by both impl-validation and IR-gen later).
- **Tests**: unit tests for: parent resolution (named + qualified), cycle (A : B, B : A), self-extension (A : A), duplicate parent (A : B, B), unknown parent (A : Foo where Foo doesn't exist), parent isn't an interface (A : SomeStruct), method conflict (A : P, Q where P.foo and Q.foo have different signatures), zero-method extension (A : B {}).
- **Estimated size**: ~250 lines + tests.

### Slice E.3 — IR-gen + codegen: transitive impl emission + concat vtable layout

- **Scope**: when `impl T : Child` is processed, IR-gen registers ImplInfo entries for `(T, Child)` AND `(T, P)` for every P in Child's ancestor closure. Vtable codegen emits `(T, Child)` with the concat layout (any-block, then each parent's full vtable inlined, then own methods).
- **Files**:
  - `pkg/ir/gen_iface.bn`: when collecting impl info, expand `(T, Child)` into the transitive set. Each ancestor entry shares the same MethodFuncs slice for its own methods (lookup in T's method table by interface-method-name). Avoid duplicate entries when multiple paths reach the same ancestor.
  - `pkg/codegen/emit_impls.bn`: rewrite `emitImplVtable` to use the concat layout. For an interface I with parents P1, P2, recursively emit P1's full vtable as slots, then P2's, then I's own methods. The dtor slot at offset 0 stays; nested any-blocks (in each parent sub-vtable) also get the same dtor pointer.
  - `pkg/codegen/emit_impls.bn:vtableSlotCount` / `vtableSlotCountForInfo`: update to compute the recursive size (sum over parents + own methods + any-block).
- **Tests**: unit test that `impl T : Child` emits both `__ivt.bn_T__Child` and `__ivt.bn_T__Parent`. Unit test that Child's vtable structure has the expected number of slots (recursive). End-to-end: an extension-using program compiles to LLVM with the expected vtable globals.
- **Estimated size**: ~300 lines + tests.

### Slice E.4 — Method dispatch through inherited methods + `*Child → *Parent` conversion codegen

- **Scope**: method-dispatch site for an inherited method computes the correct vtable slot offset (taking into account the nesting). Conversion `*Child → *Parent` produces `{data, vtable + static_offset}`.
- **Files**:
  - `pkg/types/check_method.bn` (or wherever method resolution lives): when resolving `iv.f()` on a `*Child` where `f` is inherited from `Parent`, record the dispatch path (which ancestor the method comes from) so codegen can compute the offset.
  - `pkg/codegen/emit_instr.bn` (around OP_IFACE_CALL or equivalent): for inherited methods, emit `getelementptr` to compute the slot index = sizeof(any-block) + sum(sizeof(preceding-parent-vtables)) + (offset of method within target ancestor's own slot block). Statically computable per (Child, target-ancestor, method).
  - Conversion lowering: emit code that adjusts the vtable pointer by the static offset and keeps the data pointer.
  - Construction-site rules: `*Child → *Parent` should be implicit at any context where a `*Parent` is expected — analogous to how `*T → *Iface` works once `impl *T : Iface` is declared. Wherever the construction-site logic lives, extend it.
- **Tests**: end-to-end conformance — interface extension, single parent dispatch through child, multi-parent dispatch, deep extension (3+ levels). Also a test that verifies `*Child → *Parent` upcast preserves data identity and dispatches through the parent's slot.
- **Estimated size**: ~250 lines + tests.

### Slice E.5 — Cross-package, edge cases, conformance hardening

- **Scope**: cross-package extension (parent interface in another package), `.bni` propagation, negative conformance tests, ratification of design at the docs level (claude-notes already says "not implemented" — flip to implemented).
- **Files**:
  - `pkg/loader/*.bn`: verify that imported `.bni`s carry their parent lists correctly.
  - Conformance suite: `XXX_iface_extend_basic.bn`, `XXX_iface_extend_multi.bn`, `XXX_iface_extend_deep.bn`, `XXX_iface_extend_cross_pkg.bn`, plus negatives (`XXX_iface_extend_cycle.bn`, `XXX_iface_extend_dup_parent.bn`, `XXX_iface_extend_method_conflict.bn`, `XXX_iface_extend_not_an_iface.bn`).
  - `explorations/claude-notes.md`: update extension entry from "not yet implemented" to "implemented".
  - `explorations/claude-todo.md`: move the TODO entry to claude-todo-done.md.
- **Estimated size**: ~200 lines mostly tests + doc updates.

## Slicing order rationale

- E.1 first because it's small, isolated, and lets later slices build on a stable AST shape.
- E.2 next because the type checker is the source of truth for "what methods does this interface have?" — codegen will consume that.
- E.3 next because it produces the actual vtables; without it, no real dispatch can happen.
- E.4 last among the implementation slices because method dispatch and conversion both rely on the vtables existing.
- E.5 sweeps up cross-package and edge cases — typically these "just work" once the core machinery is right, but tests pin the behavior.

Each slice should be one commit, each one keeping conformance and unit tests green.

## Implementation notes / open questions

1. **Where do parents live on TYP_INTERFACE?** Currently TYP_INTERFACE carries a method list. Add a `Parents @[]@Type` field, or a `FullMethods @[]@Method` computed once and cached? Probably both — parents for cycle detection and dispatch-path computation, FullMethods (or equivalent helper that computes it on demand) for quick "does this interface have method X" queries.

2. **Method conflict resolution.** If `interface X : A, B {}` and both A and B declare `foo()` with identical signature, is that allowed? Reasonable answer: yes (no actual conflict). With different signatures: error. If `X` itself also declares `foo()` overriding both parents: error (or maybe allow if X's signature is identical — but Binate has no method overriding, so probably just an error).

3. **Method dispatch path threading.** When resolving `iv.f()` on `*Child` where f comes from Parent (or some ancestor), how is the dispatch path recorded? Options: (a) the method-resolution result carries `via: @Interface` indicating which ancestor; (b) codegen recomputes the path. (a) is cleaner — type checker is the natural place to resolve "which method does this name refer to".

4. **Conversion vs dispatch.** `*Child → *Parent` upcast is one operation; dispatching `*Child.parent_method()` via direct slot lookup is another. They're related but distinct. I'd implement the dispatch path first (covers most cases naturally) and add explicit upcast as a separate codegen.

5. **Multi-extension layout determinism.** Order of parents matters at the vtable level. The user's declaration order is the canonical order — first parent occupies the prefix immediately after the any-block. Document this; tests pin it.

6. **`any` as a parent.** All interfaces implicitly extend `any`. The any-block at offset 0 *is* the `any` vtable in some sense — once the language has `any` for real (currently TBD), we can revisit whether to make the implicit extension explicit anywhere. For now, the dtor-only any-block is the practical realization.

7. **Cycle detection performance.** Naive walk is O(N^2) in interfaces × extends-edges. Fine for any realistic codebase. Document the algorithm: DFS with visited set; if a back-edge to the start is found, report a cycle with the path.

8. **`.bni` extension visibility.** Parent interface refs in `.bni` files need to be loaded by the loader. Should already work via `parseInterfaceRef` — verify in E.5.

## Cross-references

- `claude-notes.md` § "Interfaces" (canonical brief design)
- `claude-discussion-detailed-notes.md` § "Interface Extension" (long form)
- `claude-plan-1.md` § 2.3 (original layout decision)
- `plan-interface-syntax-revision.md` (Phase 2 foundation; extension builds atop)
- `plan-cross-package-interfaces.md` (Slices 2.6–2.9; cross-package extension reuses this machinery)
- `claude-todo.md` § "Interface embedding/extension — NOT IMPLEMENTED"
- `notes-package-introspection.md` (related: `*TypeInfo` slot in any-block; not a dependency for this work)
