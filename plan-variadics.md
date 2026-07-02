# Plan: Implementing Variadic Functions and Spread

**Status:** high-level plan (2026-07-02). Expanded into an ordered, edit-site-level
implementation plan in **[plan-variadics-detailed.md](plan-variadics-detailed.md)**
(grounded in a repo-wide codebase survey). The **language design is settled** and
**specified** — see spec §10.3 (`func.variadic.*`, `func.call.apply`) and the
DECIDED notes in `claude-notes.md` ("Variadic functions" / "Spread operator").
This document is the implementation roadmap; it does **not** re-litigate design.

## 1. What we are building

Go-style variadic functions, specified ahead of implementation. The pinned design:

- **Declaration:** a function/method's **final** parameter may be variadic,
  `name ...T` (at most one, last only). In the body it has type **raw slice
  `*[]T`** (2-word borrow) — never a managed `@[]T`.
- **Individual-arg calls** `f(a, b, c)` materialize a **caller-side stack temp
  array** and pass a `*[]T` viewing it: **zero heap allocation** (the
  no-hidden-allocation rule). Zero args → empty `{null, 0}`.
- **Borrow, not retainable:** the `*[]T` is valid only for the call; retaining it
  is undefined behavior (`mem.raw-uaf`), not a diagnosed error. To keep elements,
  the callee copies into an owned `@[]T`.
- **Spread** `expr...` as the final argument forwards a slice's `{data, len}`
  directly (no copy/alloc); `expr` must be a slice assignable to `*[]T` (`@[]T`
  decays; element-`readonly` capability rules apply; an array must be sub-sliced
  `arr[:]...` first). Spread is **exclusive** (Go-style): the entire variadic
  argument, no mixing with individual variadic args; only fixed args may precede.
- **Type identity:** variadic-ness is part of signature type identity, so
  `*func(...T)` / `@func(...T)` are distinct variadic function-value types, and
  **interface/impl methods** and **method expressions/values** may be variadic.
- **ABI erasure at indirect boundaries:** at a call through a function value or
  an interface-method vtable slot, the **caller** packs/spreads to a plain `*[]T`
  *before* the single indirection; the shim/slot receives `*[]T`. So an indirect
  variadic callee's calling convention is identical to a fixed `*[]T` parameter.
- **Not** `print`/`println`/`panic`: those stay special predeclared
  heterogeneous forms; general homogeneous `...T` does not subsume them, and a
  spread on them is rejected.

## 2. Current state (from the codebase survey)

- **Lexer/token:** `ELLIPSIS` (`...`) is already tokenized
  (`pkg/binate/lexer/lexer.bn:385`, `token/token.bn:111`). Today it is consumed
  **only** by `parseCCall` (the `__c_call` C-varargs boundary) — a *separate*
  mechanism that this work does not touch.
- **No variadic scaffolding on parameters:** neither `ast.ParamDecl`
  (`parser/parse_decl.bn:199`) nor `types.Param` (`types/resolve_type.bn:252`)
  carries a variadic flag.
- **print/println/panic:** special-cased by callee name in the checker
  (`types/check_expr.bn:440-513`), lowered per-arg in IR
  (`ir/gen_print.bn`). `panic` is already fixed single-arg. These are the
  *only* current variadic-ish path and are unrelated to the general feature.
- **Call checking:** `checkCallExpr` (`types/check_expr.bn:457-549`) does exact
  arity + per-arg assignability; this is where variadic call-binding attaches.
- **Signature build:** `resolveFuncDeclType` (`types/resolve_type.bn:233`) →
  `MakeFuncType` (`types/types.bn:264`); func-value type via
  `resolveFuncValueType` (`resolve_type.bn:136`).

## 3. Implementation phases (high level — to be expanded)

Ordered so each phase leaves the tree green. Rough dependency order:

1. **AST + parser.**
   - Add a `variadic bool` (or an ellipsis position) to `ast.ParamDecl`; parse
     `name ...T` in `parseParamDecl`, enforcing *last-only* / *at-most-one* at
     parse or check time (grammar `VariadicParam`, D12).
   - Parse the variadic form in a **function-value type** body (`FuncTypeParams`:
     trailing `...T`).
   - Add a spread marker to the last call argument; parse trailing `expr...` in
     the call-args path (grammar `ArgumentList`).
   - Reject a `...` that is not final (param or arg) with a clear diagnostic.

2. **Types / checker.**
   - Thread the variadic flag into `types.Param` / `FuncType`; make **signature
     type identity / equality** compare variadic-ness (so `func(...T)` ≠
     `func(*[]T)`). This is load-bearing for function values, interfaces, and
     method values — audit every `FuncType` equality site.
   - Body type of a variadic param is `*[]T`.
   - `checkCallExpr` variadic binding: fixed params bind positionally; then
     either (a) N individual trailing args each assignable to `T`, or (b) one
     spread `s...` with `s` assignable to `*[]T`; enforce exclusivity; reject a
     spread into a non-variadic callee and onto `print`/`println`/`panic`.
   - **Generics:** `...T` for a type parameter — resolve element type and both
     packing size and spread assignability per-instantiation.
   - **Interfaces:** allow variadic interface methods; extend
     `iface.impl.coverage` matching to compare variadic-ness. Method
     expressions/values of a variadic method yield a variadic function-value
     type.

3. **IR-gen.**
   - **Direct variadic call, individual args:** materialize a caller-side
     **stack** temp array (`alloca [N]T`), store each trailing arg (for a
     managed element type, **acquire/RefInc as stored** — the elements are
     statement temporaries the caller RefDec's at statement end), build the
     `*[]T` `{data = &array, len = N}`, pass it. Zero args → `{null, 0}`.
   - **Spread:** forward the source slice's `{data, len}` directly (decaying
     `@[]T` → `*[]T`); no copy, no alloc.
   - The callee receives a `*[]T` **borrow**: no RefInc/RefDec of the slice, no
     per-element release at exit (contrast a fixed managed param).
   - **Indirect calls** (function value, vtable): do the pack/spread at the call
     site; the shim/slot signature uses `*[]T` (variadic-ness erased at the ABI).

4. **Native backends + VM.**
   - Backends: emit the temp-array alloca + 2-word slice construction; confirm
     the `*[]T` is passed exactly like any raw-slice parameter (no special
     calling convention — variadic-ness is a front-end/type property only).
   - **Bytecode VM:** implement the same pack/spread at the VM call boundary so
     **cross-mode** interop holds (a compiled caller ↔ interpreted callee must
     agree the argument is a standard 2-word `*[]T`; spec §2.4, §10.3 cross-mode
     note). The *materialization mechanism* is backend/VM-private; only the
     `*[]T` passing is the ABI contract.

5. **Conformance + unit tests.**
   - Positive: individual-arg pack; empty (`len==0`); spread of `@[]T`/`*[]T`;
     spread of a sub-sliced array/string literal; managed element type
     (`...@T`) retain-by-copy; generic `...T`; variadic function-value type +
     indirect call + spread through it; variadic interface method + dispatch;
     method expression/value of a variadic method.
   - Negative: `...` not on the last parameter; more than one variadic param;
     spread mixed with individual variadic args; spread into a non-variadic
     callee; spread on `print`/`println`/`panic`; wrong element type; wrong
     arity with fixed params + spread.
   - Note the borrow-escape (retain the `*[]T` past the call) is **UB**, not a
     diagnosed error, so it is not a negative test (matches `mem.raw-uaf`).

## 4. Key risks / correctness invariants

- **No hidden heap allocation** on the individual-arg path — the whole point of
  the raw-`*[]T` design. The temp array is stack storage, torn down after the
  call; nested/re-entrant variadic calls each get their own live array.
- **Managed-element refcount discipline is inverted vs a fixed managed param:**
  the caller acquires each element as a statement temporary and releases it at
  statement end; the callee **borrows** (no entry-acquire, no exit-release). A
  bug here leaks (never releasing copied-out acquires) or double-frees (callee
  wrongly releasing borrowed elements). See §10.3 `func.variadic.pack` note.
- **Signature identity must include variadic-ness everywhere** — miss a
  `FuncType` equality site and a variadic and a `*[]T`-fixed function become
  interchangeable, breaking function-value / interface / method-value soundness.
- **BUILDER compatibility:** cmd/bnc's tree must stay BUILDER-compilable. If any
  variadic-carrying signature enters that tree, verify the pinned BUILDER accepts
  the new syntax before relying on it (the BUILDER lags the tree).

## 5. Cross-references

- Spec: §10.3 (`func.call.apply`, `func.variadic.decl/identity/pack/spread/borrow`);
  §7.9 `type.func.kinds`; §10.8 `func.value.spelling`/`func.value.identity`;
  §10.11 method expr/value; §10.12 `func.value.indirect-call`; §11.1
  `iface.impl.coverage`; §15.7 `builtin.predeclared`; §2.4 cross-mode; §16.9
  `pkg.ccall` (the separate C-varargs `...`, unaffected).
- Design: `claude-notes.md` "Variadic functions — DECIDED" / "Spread operator —
  DECIDED" (refined 2026-07-02).
