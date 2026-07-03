# Plan: Implementing Type Assertions, Type Switches, and RTTI

**Status:** high-level plan (2026-07-02). A follow-up worker is expected to expand
each phase into ordered, edit-site-level steps. The **design is settled** and
**specified** — see spec §11.12 (`iface.assert`, `iface.assert.kind`,
`iface.assert.absent`, `iface.typeswitch`, `iface.rtti`), §7.13.14
(`type.layout.typeinfo`), §7.13.8, §13.8 (`expr.type-assert`), §14.10
(`stmt.type-switch`), §17.5 (the failed-assertion panic), and the DECIDED notes in
`claude-notes.md` ("Type assertions, type switches, and RTTI"). This is the
implementation roadmap; it does **not** re-litigate design.

## 1. What we are building

Go-style **type assertions** and **type switches** — the *downcast* direction from
an interface value back to a concrete type or a narrower interface — plus the
**RTTI** substrate they need. Binate stays **open** (no sum types, no
exhaustiveness). Pinned design:

- **Source:** an interface value `*I`/`@I` (incl `*any`/`@any`).
- **Target:** a **nameable** type with a mandatory `*`/`@`/value **recovery kind**
  (`AssertTarget = [ "*" | "@" ] [ "readonly" ] TypeName`) — slice/func/array/
  struct/`Self` targets are compile errors. Concrete target matches by **exact**
  dynamic-type identity (named-distinct preserved: `Celsius` ≠ `float64`; each
  generic instantiation distinct); interface target matches by **satisfaction**
  (explicit `impl`, **including transitive ancestors**).
- **Forms:** expression `x.(K T)` **aborts** on a miss (a new defined runtime
  panic, §17.5 — no `recover`); `v, ok := x.(K T)` is comma-ok (single expression
  → two values); type switch `switch [v :=] x.(type) { case K T: … }`.
- **Recovery-kind table** (managed→raw decay direction): `@I` → `@T` (retain) /
  `*T` (borrow, no churn) / value; `*I` → `*T` / value; `@T`-from-`*I` **rejected**.
- **No `case nil`** (interface values not nil-comparable). Unset (`present`=false)
  → no dynamic type → assertion miss / type-switch `default`; typed-nil → matches
  its type. Absence via `present()`.
- **RTTI:** a per-type static **`TypeInfo`** (identity, dtor, size, align, name,
  **satisfaction-table** of `interface → sub-vtable` over the *transitive-closure*
  of satisfied interfaces), reached via a `*TypeInfo` in every interface vtable's
  offset-0 **any-block** (beside the dtor). One `TypeInfo` per type **program-wide**;
  cross-mode agreement is on the *equality result*, not a shared address.

## 2. Current state

- **Vtable any-block** today holds only the destructor at offset 0; methods follow
  (spec §7.13.8, §11.11 `iface.dispatch`). Adding a `*TypeInfo` **grows the
  any-block to two words**, shifting method slots — a vtable ABI change that every
  backend and the VM must apply **consistently**.
- **Interface machinery** (impl collection, nominal satisfaction, interface
  extension + the fixed-offset upcast / nested sub-vtables) already exists and is
  stable — the satisfaction-table is its transitive closure, so it reuses that
  machinery rather than inventing new lookup.
- `ELLIPSIS`/keyword lexing is fine; `type` is already a reserved keyword (so
  `.(type)` is unambiguous). No RTTI, no `TypeInfo`, no `.(` postfix, and no
  type-switch parsing exist yet.
- The failed-assertion panic is a **new** member of the closed §17.5 set.

## 3. Implementation phases (high level — to be expanded)

Ordered so each phase leaves the tree green.

1. **RTTI substrate (do this first — everything else needs it).**
   - Define the `TypeInfo` record in the **shared layout layer** (`pkg/types`),
     cross-mode (identity, dtor, size, align, name, satisfaction-table). It is part
     of the `type.layout.keystone` contract.
   - Emit one static `TypeInfo` per concrete type that can be a dynamic type (any
     type constructed into an interface value).
   - Add the `*TypeInfo` slot to the vtable **any-block**; **re-base method slots**
     accordingly in vtable emission AND every dispatch site (IR-gen + all native
     backends + VM). This is the highest-risk change — a slot-index mismatch
     silently misdispatches.
   - Populate the satisfaction-table as the **transitive closure** of satisfied
     interfaces (explicit impls + all ancestors), each mapped to the correct
     nested sub-vtable (the same offset the static upcast uses).
   - Ensure every **nested** sub-vtable's any-block carries the **leaf** type's
     `*TypeInfo` (not the parent's) — required for downcast-through-an-upcast.

2. **Lexer/parser.**
   - Parse the `.(AssertTarget)` postfix; parse `.(type)` (keyword) as the
     type-switch head; front-end decision (`switch id :=` → type switch; else
     scrutinee + trailing `.(type)`), under the D4 composite-literal suppression.
   - Reject non-nameable targets at parse or check time.

3. **Checker.**
   - Assertion: operand must be an interface value; target nameable + recovery
     kind legal for the source (the `@I`/`*I` × `@T`/`*T`/value table); result type
     is the recovered kind. Expression form (one value) vs comma-ok (value + bool),
     reusing the two-result-RHS path.
   - Type switch: per-case `AssertTarget`s, kind legality, bind `v` per case
     (single-target → case type; multi-target / `default` → scrutinee type). First
     match wins; overlap allowed; no exhaustiveness.
   - Exact identity for concrete (box-site type, named-distinct preserved);
     satisfaction (incl transitive) for interface; type-param targets resolved per
     monomorphization.

4. **IR-gen.**
   - Assertion lowering: load vtable; **null-check first** (unset → miss); load
     `*TypeInfo` from the any-block; concrete → `TypeInfo` identity compare;
     interface → satisfaction-table lookup → form `{data, vtable(T, J)}` (ancestor
     case = the static upcast after the identity check). Recovery kind: `@T` →
     RefInc; `*T` → borrow (no refcount); value → field-wise acquiring copy
     (`mem.copy`).
   - Expression form: on miss, raise the **failed-assertion panic** (§17.5).
     Comma-ok: yield `(value, ok)`.
   - Type switch: a chain of identity compares / satisfaction lookups + branches,
     binding `v` per case.

5. **Native backends + VM.**
   - Emit `TypeInfo` static data + the two-word any-block in every backend; the
     assertion/switch lowering + the new panic in native and VM.
   - **Cross-mode:** the assertion's boolean result must agree between compiled and
     interpreted execution; comparison is pointer-equality *within* a mode
     (self-describing-handle model, §19.4), not a shared native address.

6. **Tests.**
   - Positive: concrete assert (each recovery kind); interface assert incl a
     **transitive-ancestor** target (`impl R : Child`, assert `*Parent`); comma-ok;
     type switch (single/multi/default, binding); typed-nil matches its type; unset
     → default; generic `...` targets; `any`.
   - Negative: assert a non-interface value; non-nameable target; `@T` from a `*I`;
     wrong-type expression-form abort (the §17.5 panic diagnostic).

## 4. Key risks / correctness invariants

- **Vtable any-block growth re-bases method slots** — the single highest-risk item.
  Every vtable emitter and every dispatch slot computation (IR-gen, x64, aarch64,
  arm32, VM) must agree on the new layout or dispatch silently corrupts. Smoke-test
  all backends.
- **Satisfaction-table must be the transitive closure**, not just declared impls,
  or `x.(*Parent)` wrongly fails (the review's critical). Reuse the
  interface-extension ancestor set.
- **Leaf `*TypeInfo` in every nested sub-vtable** — downcast after an upcast must
  still recover the concrete type.
- **Cross-mode identity is result-agreement, not address-sharing** — do not emit a
  native address the VM is expected to match; compare within a mode.
- **Recovery refcount discipline:** `@T` retains (RefInc), `*T` borrows (nothing),
  value copies field-wise (acquire managed fields). A bug leaks or double-frees.
- **BUILDER compatibility:** almost certainly runtime/codegen-only (not in
  `cmd/bnc`'s BUILDER-compiled tree), but audit if any assertion syntax reaches it.

## 5. Cross-references

- Spec: §11.12 (`iface.assert*`, `iface.typeswitch`, `iface.rtti`); §7.13.14
  (`type.layout.typeinfo`); §7.13.8 (any-block `*TypeInfo`); §13.8
  (`expr.type-assert`); §14.10 (`stmt.type-switch`); §17.5 (failed-assertion
  panic); §11.6 (`iface.extend.transitive`/`.upcast`); §20.3 (reflect, later
  phase); §2.4 / §19.4 (cross-mode).
- Design: `claude-notes.md` "Type assertions, type switches, and RTTI — DECIDED
  2026-07-02".
