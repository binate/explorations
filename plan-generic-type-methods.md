# Plan: Methods on Generic Types + Parameterized-Receiver Impls

**Status:** high-level plan (2026-07-05). A follow-up worker expands each phase
into ordered, edit-site-level steps. The **design is settled** and **specified** —
see spec §12.1 (`gen.no-generic-methods` narrowed, `gen.method.generic-recv`,
`gen.impl.generic-recv`), §11.3 `iface.impl.form`, §10.1/§10.4, and the DECIDED
note in `claude-notes.md` ("Methods on generic types + parameterized-receiver
impls"). This document is the implementation roadmap; it does **not** re-litigate
design.

## 1. What we are building

Let a generic type carry methods and satisfy interfaces — the missing piece that
makes `interface Iterator[T]` / `Container[T]` *implementable* (today they are
declarable-only). Pinned design:

- **Method on a generic type:** `func (it *Cursor[T]) Next() (T, bool)` — the
  receiver's `[T]` **binds** the type's parameters as fresh names; constraints are
  **inherited** from the type declaration (not restated); the count must equal the
  type's arity. The method introduces **no** type parameter of its own.
- **Parameterized-receiver impl:** `impl *Cursor[T] : Iterator[T]` — binds `T`,
  the interface list references it; coverage checked **abstractly** at the impl
  decl, vtable + satisfaction resolved **per monomorphized instantiation**.
- **Still forbidden:** method-level type parameters (`func (v Vec[T]) map[U](…)`)
  — a vtable slot would vary per `U` (`gen.no-generic-methods`).
- **Dispatch:** constraint-path calls stay **direct, no vtable** (`gen.mono.constraint-call`);
  the interface-value path builds a `(Cursor[int], Iterator[int])` vtable + a
  distributed satisfaction entry (the no-orphan-rule RTTI registry). **No run-time
  generic dispatch** — everything resolves at monomorphization.

## 2. Current state

- **Generic structs/interfaces exist and monomorphize** (`gen.mono`, shipped);
  generic **free functions** with interface constraints work (constraint-path
  direct-call lowering, `gen.mono.constraint-call`).
- **The wall:** `MethodDecl` has no `[TypeParams]` slot AND no way to bind the
  receiver's `T` (`ReceiverType = Type`, brackets = concrete type args); a method
  on `*Cursor[T]` fails ("T undefined" / "methods cannot have type parameters").
  `ImplDecl` likewise can't bind a receiver param. So a generic type has **no
  methods** → satisfies **no** interface.
- **Adjacent machinery already present** and reused here: impl collection +
  transitive-ancestor closure (`collectImplsFromDecl`, `gen_impl.bn`), vtable
  emission (`emitImplVtables` / `__ivt.<T>__<J>`), the distributed satisfaction
  registry (`type.layout.satisfaction`, from the type-assertion RTTI work),
  monomorphization driver, and constraint-body checking.
- **Related open gap** this makes load-bearing: `gen.satisfy` constraint checking
  is currently skipped at generic **struct/interface** instantiation (§12.4 gap) —
  the parameterized impl's per-instantiation satisfaction relies on that check.

## 3. Implementation phases (high level — to be expanded)

Ordered so each phase leaves the tree green.

1. **Parser / AST.**
   - Method receiver: parse the **binding identifier list** on a generic base
     (`*Cursor[T]`, `(m HashMap[K, V])`) — brackets in a receiver are always
     binding names (never concrete args; a concrete-arg receiver is a
     **specific-instantiation** impl, forbidden — §12.4 `gen.no-conditional-impls`,
     distinct from a *conditional* impl). Store the bound names on the `MethodDecl`
     AST.
   - Impl receiver: parse `impl *Cursor[T] : Iterator[T]` — bind `T`, interface
     list references it. Same binding treatment.
   - Keep rejecting a method-level `[…]` after the method name (no `[TypeParams]`
     slot on `MethodDecl`).

2. **Checker / type resolution.**
   - Resolve a method on a generic type: bind the receiver names fresh, fetch
     their **constraints from the type's declaration** (inherited, not restated),
     verify name-count == type arity, put them in scope for the signature + body,
     and check the body against the constraints (reuse the generic-free-function
     constraint-body machinery).
   - Represent the method's signature abstractly (parameterized by the type's
     params) in the method/impl tables, keyed for per-instantiation specialization.
   - Parameterized impl: bind receiver params; run `iface.impl.coverage`
     **abstractly** (`Cursor[T]` provides `Iterator[T]`'s methods, `T` abstract).
   - Diagnostics: wrong binding-name count vs arity; a specific-instantiation
     (concrete-arg) receiver; a method-level type param.

3. **Monomorphization.**
   - On instantiation (`Cursor[int]`), specialize its methods (`Next() (int,
     bool)`) and the impl (`Cursor[int] : Iterator[int]`), reusing the existing
     mono driver.
   - Run `gen.satisfy` at instantiation (fixing/covering the §12.4 gap for the
     param-impl case).
   - Constraint-path use (`func f[I Iterator[int]](it I)`, `I = Cursor[int]`):
     direct calls to the monomorphized methods — no vtable.
   - Interface-value use (`*Iterator[int]` built from a `Cursor[int]`): build the
     `(Cursor[int], Iterator[int])` vtable (reuse `emitImplVtables`) + a
     distributed `SatEntry` (RTTI registry).

4. **IR-gen / backends / VM.**
   - Emit the monomorphized method bodies (like generic free functions, plus a
     receiver arg) across LLVM / native / VM.
   - Emit the per-instantiation vtable + satisfaction entry; confirm both dispatch
     paths (direct constraint-call, indirect vtable-call) and cross-mode.

5. **Tests.**
   - Positive: `Cursor[T].Next` method; `impl *Cursor[T] : Iterator[T]`; use via a
     constraint (direct dispatch); use via a `*Iterator[int]` value (vtable
     dispatch); multi-param (`HashMap[K, V]`); constraint **inheritance**
     (`Box[T Orderable]` method calls `T.Compare`); the trivial `Iterator[T]`
     end-to-end; cross-package parameterized impl.
   - Negative: method-level type param (`map[U]`) rejected; binding-count ≠ arity;
     specific-instantiation impl (`impl Cursor[int] : …`) rejected (§12.4).
   - All backends + all modes.

## 4. Key risks / correctness invariants

- **Binding vs. concrete in the receiver** — the parser/checker must treat
  receiver/impl brackets as **binding** names, not type arguments (a concrete-arg
  receiver is a specific-instantiation impl and is forbidden — §12.4). The
  distinction is **semantic** (predeclared names like `int` are ordinary
  identifiers): a bracket name that resolves to a type is rejected. Get it right,
  or `impl Cursor[int]` silently means the wrong thing.
- **Constraint inheritance** — the method's `T` constraint comes from the type
  declaration, never restated on the method; the checker must fetch it so the body
  can call the constrained methods.
- **Method-level type params stay forbidden** — don't accidentally admit `[…]`
  after the method name while adding receiver binding.
- **Methods + impl monomorphize *with* the type** — a `(Cursor[int],
  Iterator[int])` vtable must be emitted for the interface-value path (reuse
  `emitImplVtables`); the satisfaction entry rides the distributed registry
  (per-instantiation), consistent with the no-orphan-rule model.
- **§12.4 gap becomes load-bearing** — the parameterized impl's per-instantiation
  satisfaction needs constraint checking at struct/interface instantiation, which
  is currently skipped. Fix or explicitly scope it.
- **BUILDER compatibility** — if any of `cmd/bnc`'s tree (or `pkg/rt`) starts
  using methods on generic types, verify the pinned BUILDER accepts the receiver
  binding syntax before relying on it; otherwise gate.

## 5. Cross-references

- Spec: §12.1 (`gen.no-generic-methods`, `gen.method.generic-recv`,
  `gen.impl.generic-recv`), §11.3 `iface.impl.form`, §10.1/§10.4
  `func.method.receiver-base`, §12.3 `gen.mono`/`gen.mono.constraint-call`, §12.4
  `gen.satisfy` (+ the §12.4 gap), §7.13.14 `type.layout.satisfaction` /
  §11.12 `iface.rtti` (the satisfaction registry).
- Design: `claude-notes.md` "Methods on generic types + parameterized-receiver
  impls — DECIDED 2026-07-05".
