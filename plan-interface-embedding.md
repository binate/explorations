# Plan: Interface Embedding / Extension

**Status**: COMPLETE (shipped 2026-05-13). The plan content below remains as
a record of the design. `claude-notes.md` § "Interfaces" carries the canonical
brief; this doc holds the rationale and implementation notes.

## Context

Interface extension is in the design (`claude-notes.md` § "Interfaces"; full discussion in `claude-discussion-detailed-notes.md` § "Interface Extension"; original layout in `claude-plan-1.md` § 2.3). Motivation: needed for any Go-style I/O stdlib (Reader / Writer / ReadCloser / etc.).

Ratified design summary:

- **Syntax**: `interface X : I1, I2, ... { methods }`. Parents listed once before the body. No interspersing of parents and methods (unlike Go), no anonymous embedding (Binate has no anonymous interfaces). Empty body allowed.
- **Aliases vs zero-method extension**: `interface X = A` is an alias (same identity); `interface X : A {}` is a distinct interface that requires A's methods.
- **Static upcast**: `*Child → *Parent` is a nominal, compile-time-known relation — no runtime query. No structural satisfaction check exists in the language.
- **Vtable layout**: `[any-block][full vtable of (R, P1)][full vtable of (R, P2)][own methods]`, recursively. Every interface implicitly extends `any`, so every vtable starts with the any-block at offset 0. Conversion = fixed compile-time pointer offset; no swap, no lookup. Some any-block content is duplicated at each nested origin.
- **Transitivity**: `impl T : Child` implicitly registers `impl T : Parent` for every Parent in Child's ancestor closure. The `(R, Parent)` vtable is emitted; the `(R, Child)` vtable inlines it.

## Implementation notes / open questions

1. **Where do parents live on TYP_INTERFACE?** Currently TYP_INTERFACE carries a method list. Add a `Parents @[]@Type` field, or a `FullMethods @[]@Method` computed once and cached? Probably both — parents for cycle detection and dispatch-path computation, FullMethods (or equivalent helper that computes it on demand) for quick "does this interface have method X" queries.

2. **Method conflict resolution.** If `interface X : A, B {}` and both A and B declare `foo()` with identical signature, is that allowed? Reasonable answer: yes (no actual conflict). With different signatures: error. If `X` itself also declares `foo()` overriding both parents: error (or maybe allow if X's signature is identical — but Binate has no method overriding, so probably just an error).

3. **Method dispatch path threading.** When resolving `iv.f()` on `*Child` where f comes from Parent (or some ancestor), how is the dispatch path recorded? Options: (a) the method-resolution result carries `via: @Interface` indicating which ancestor; (b) codegen recomputes the path. (a) is cleaner — type checker is the natural place to resolve "which method does this name refer to".

4. **Conversion vs dispatch.** `*Child → *Parent` upcast is one operation; dispatching `*Child.parent_method()` via direct slot lookup is another. They're related but distinct. Implement the dispatch path first (covers most cases naturally) and add explicit upcast as a separate codegen.

5. **Multi-extension layout determinism.** Order of parents matters at the vtable level. The user's declaration order is the canonical order — first parent occupies the prefix immediately after the any-block. Document this; tests pin it.

6. **`any` as a parent.** All interfaces implicitly extend `any`. The any-block at offset 0 *is* the `any` vtable in some sense — once the language has `any` for real (currently TBD), we can revisit whether to make the implicit extension explicit anywhere. For now, the dtor-only any-block is the practical realization.

7. **Cycle detection performance.** Naive walk is O(N^2) in interfaces × extends-edges. Fine for any realistic codebase. Document the algorithm: DFS with visited set; if a back-edge to the start is found, report a cycle with the path.

8. **`.bni` extension visibility.** Parent interface refs in `.bni` files need to be loaded by the loader. Works via `parseInterfaceRef`.

## Cross-references

- `claude-notes.md` § "Interfaces" (canonical brief design)
- `claude-discussion-detailed-notes.md` § "Interface Extension" (long form)
- `claude-plan-1.md` § 2.3 (original layout decision)
- `plan-interface-syntax-revision.md` (Phase 2 foundation; extension builds atop)
- `plan-cross-package-interfaces.md` (Slices 2.6–2.9; cross-package extension reuses this machinery)
- `notes-package-introspection.md` (related: `*TypeInfo` slot in any-block; not a dependency for this work)
