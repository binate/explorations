# Plan: implement `iface.construct.value-borrow`

Status: **design ratified — Draft, pending implementation** (spec `docs f8cdd0a`;
`proposal-implicit-any-borrow`). This plans the *implementation* of the boxing half of
the `...*any` `fmt` direction. Scope is the value-borrow language feature **only** (the
`fmt` library itself is a separate, already-underway effort).

## 1. Goal & current state

Permit an **implicit borrow** when constructing a **raw** `*Iface` (incl. `*any`) from a
**value** source, so `fmt.Print("hi", 42)` and `Opts{Any: v}` need no explicit `&`. This
is the last un-built piece of the fmt chain — everything else is landed:

| Piece | State |
|---|---|
| Recovery: `iface.assert.slice` (slice `case`) | ✅ landed `ff36c82a` |
| Recovery: scalar value `x.(T)` / `case int:` | ✅ landed `89b41531` |
| Variadic `...*any` (callee gets stack-packed `*[]*any`, no heap) | ✅ landed |
| Boxing a **pointer** (`&t`, `@T`) into `*any` | ✅ works today |
| **Boxing a VALUE into `*any`** | ❌ **this plan** |

Today a value source is rejected in the checker at
`types_assignable.bn` `canAssignToRawInterfaceValue` (`:264-266`,
`if srcResolved.Kind != TYP_POINTER && != TYP_MANAGED_PTR { return false }` →
`cannot assign T to *I`), and IR-gen `wrapAsIfaceValue` (`gen_iface.bn:208-210`) bails on
a non-pointer source. So `f(42, "hi")` for `func f(...*any)` does not compile.

## 2. Design recap (from the ratified proposal — see `proposal-implicit-any-borrow.md`)

- **Value source only.** A `*T`/`@T` source is unchanged (`iface.construct.managed`).
- **2a — addressable (lvalue):** auto-`&` — `Opts{Any: x}` ≡ `Opts{Any: &x}`. **All
  positions.** Identical borrow/lifetime/UAF profile to the explicit `&x`.
- **2b — non-addressable (rvalue):** materialise a temporary, take its address. Permitted
  **only** in **argument** and **`var`/`:=`-init** positions; a **compile error** in a
  store into a pre-existing location (assignment, field/element store, `return`) — a
  statement-scoped temp (§18.4) would dangle.
- **Raw only.** A managed `@Iface` from a value still requires explicit `box()` — leave
  `canAssignToManagedInterfaceValue` (`:318`) untouched.
- Construction **borrows, does not copy**.

## 3. Staged commits

Each is independently landable and green.

### Commit 1 — lvalue auto-`&` (2a), all positions
- **Checker** (`types_assignable.bn` `canAssignToRawInterfaceValue`, `:264-266`): admit an
  **addressable** value source — after conceptually taking its address (`*T`), run the same
  impl-reachability check the `*T` path already uses. Gate on addressability (an lvalue:
  variable, field, element, deref — reuse the checker's existing addressability predicate,
  the one `&`/receiver-smoothing uses). Non-addressable values still `return false` here
  (handled in Commit 2).
- **IR-gen** (`gen_iface.bn wrapAsIfaceValue`, `:208-210`): for an addressable value source,
  emit the address (`&`) to produce the `*T` the existing `OP_IFACE_VALUE` path consumes —
  the same address-of an lvalue that `&x` lowers to. No temp needed.
- **No positional logic** (lvalue is legal wherever `&x` is).
- **Tests:** `var iv *I = xVar` (was `cannot assign`, now ok); lvalue in field/element/arg/
  `return` positions; `@I` from a value still rejected. **Migration:** conformance
  `035_err_value_to_raw` currently expects `var iv *I = t` (t a *variable* = lvalue) to
  error — it becomes **legal**; re-target 035 to a non-addressable rvalue in a rejected
  position (or fold into the new rvalue tests). `036_err_value_to_managed` (`@I`) stays.

### Commit 2 — rvalue auto-temp (2b) + the positional check
- **Checker:** admit a **non-addressable** value source constructing a raw `*Iface`, but
  **only** in an **argument** or **`var`/`:=`-init** position; a **compile error**
  elsewhere. This is the crux — see §4.
- **IR-gen** (`wrapAsIfaceValue`): for a non-addressable value source, **materialise** a
  stack temp (`EmitAlloc` — the same no-heap alloca the variadic packer uses), store the
  value, take its address → `*T` → existing box path. For an **untyped constant** source
  (`fmt.Print(42)`), default it first (int for `42`, float64 for `2.5`) — a plain
  alloca+store; verify it does **not** re-trip the `box(<untyped constant>)` codegen bug
  (§5), which is a different (managed-alloc) path.
- **Tests:** `fmt.Print(42, "hi")` compiles + runs; an expression arg `f(a+b)`; rvalue
  var-init `o := Opts{Any: 42}; use(o)`; **rejections** — `someStruct.field = 42`,
  `arr[i] = 42`, `return SomeIface{Any: 42}` each `.error`.

### Commit 3 — `bnlint` escaping-borrow rule
The implicit lvalue borrow (2a) has **no visible `&`**, so an escaping raw interface value
built from a local reads like value construction and existing raw-escape lints miss it. Add
a `bnlint` rule (`pkg/binate/lint`) flagging a raw interface value constructed from a local
that escapes (implicit **or** explicit `&`). Per the compiler-emits-no-warnings rule this is
a lint, not a checker error; wiring it into hygiene/CI is a separate decision (don't).

### Commit 4 — flip Draft→Provisional
Once Commits 1–2 are conformance-green in every mode, flip `iface.construct.value-borrow`
Draft→Provisional on the stability axis (`docs/spec/11-interfaces-impl-self.md` rule note +
chapter badge; `conv.assignable` note). Docs only.

## 4. The positional check (the sharpest piece)

The rvalue rule (2b) needs the checker to distinguish a **borrowing/transient** position
(argument, `var`/`:=`-init) from a **storing-into-pre-existing** position (assignment,
field/element store, `return`). The construction contexts that call
`AssignableTo`/`errCannotAssign` are the sites to thread this through:

| Context | Site | rvalue value→`*Iface`? |
|---|---|---|
| `var`/`:=` init | `check_decl.bn:409` | **allow** (temp co-scopes with the new binding) |
| call argument | `check_expr.bn` (`checkVariadicCallBinding:386`, fixed-arg :329/:378) | **allow** (statement-transient) |
| composite-literal element | `check_expr_composite.bn` | allow **iff** the literal is *itself* in an allowed position (recursive) |
| assignment | `check_assign.bn:101/138/194` | **reject** |
| field/element store | (assignment paths) | **reject** |
| `return` | `check_stmt.bn:198/210` | **reject** |

The recursion for composite-literal elements is the subtle bit: `foo(Opts{Any: 42})` and
`o := Opts{Any: 42}` allow the `42`; `someStruct.field = Opts{Any: 42}` rejects it (the
literal is in a storing position). Implement as a "position kind" (borrowing vs storing)
propagated into the assignability check for the raw-interface-from-rvalue case, defaulting a
composite literal's elements to the literal's own position kind. Lvalue (2a) ignores this
entirely (legal everywhere).

## 5. Related items (NOT this plan, but adjacent — flag for the implementer)

- **`box(<untyped constant>)` miscompile** (`claude-todo.md:113`) — `box(42)` crashes
  codegen/VM. Commit 2's rvalue-materialise for an untyped constant shares the *defaulting*
  step but not the managed-alloc path, so it should sidestep the bug — **verify** with a
  `fmt.Print(42)` (untyped) test, and note the box bug is the natural companion to fix.
- **Name-less MANAGED `@any` crash** (plan-slice-type-identity.md §9, open MAJOR) — the
  `@any` path; value-borrow is **raw-only**, so orthogonal and non-blocking. (It only bites
  `box(slice)`-into-`@any`, which value-borrow doesn't touch.)
- **Struct value-*recovery*** (`x.(SomeStruct)`) is still deferred — so `fmt` can format a
  struct arg only once that lands; value-borrow gets the struct value *in* fine, but the
  formatter can't read it back yet. Out of scope here.

## 6. Verify & bookkeeping

- Smoke: `pkg/binate/types` + `pkg/binate/ir` + `pkg/binate/codegen` unit tests; the new
  conformance tests in `builder-comp` **and** `builder-comp-int` (VM boxing path) + one
  native mode; `scripts/hygiene/run.sh`.
- Update the existing `claude-todo.md` entry ("Implicit variadic value→`*any` boxing", ~
  `:101-106`) to point at this plan, and move it to done as Commits land.
