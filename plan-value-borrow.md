# Plan: implement `iface.construct.value-borrow`

Status: **design ratified — Draft, pending implementation** (spec `docs f8cdd0a`;
`proposal-implicit-any-borrow`). This plans the *implementation* of the boxing half of
the `...*any` `fmt` direction. Scope is the value-borrow language feature **only** (the
`fmt` library itself is a separate, already-underway effort).

> This revision expands the original plan with execution-grade detail grounded in the
> current tree (checker `pkg/binate/types`, IR-gen `pkg/binate/ir`, spec
> `docs/spec/11-interfaces-impl-self.md` and §18.4). The single most important
> correction to the original: the assignability chain is **type-only** — it never sees
> the source expression — so the addressability gate (2a) and the position/rvalue gate
> (2b) **cannot** live inside `canAssignToRawInterfaceValue`; they are **expr-aware
> side-checks at the construction sites**, following the existing method-receiver
> auto-address precedent. See §3.0.

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

## 2. Design recap (from the ratified proposal — spec `11-interfaces-impl-self.md:113-146`)

- **Value source only.** A `*T`/`@T` source is unchanged (`iface.construct.managed`).
- **2a — addressable (lvalue):** implicit **address-of** — `Opts{Any: x}` ≡ `Opts{Any: &x}`.
  **All positions** an explicit `&x` is permitted. Identical borrow/lifetime/UAF profile.
- **2b — non-addressable (rvalue):** materialise a temporary, take its address. Permitted
  **only** in **argument** and **`var`/`:=`-init** positions; a **compile error** in a
  store into a pre-existing location (assignment, field/element store, `return`). This is
  a lifetime rule: §18.4 `mem.temporary` releases an unnamed temporary at the **end of its
  statement**, so the borrow must not outlive the statement — except the spec **explicitly
  co-scopes** a var/`:=`-init temporary with its new binding (see §4).
- **Raw only.** A managed `@Iface` from a value still requires explicit `box()` — leave
  `canAssignToManagedInterfaceValue` (`:300-335`, gate `:318`) untouched.
- Construction **borrows, does not copy**.

## 3. Staged commits

Each is independently landable and green.

### 3.0 Architecture: type-only assignability ⇒ expr-aware site checks (read first)

The entire chain `(@Type) AssignableTo(c, dst)` → `canAssignToRawInterfaceValue(c, src, dst)`
receives **types only** (`types_assignable.bn:16`, `:240`); there is **no** source expression
and **no** position parameter, and the `Checker` struct carries no current-expr/-pos field
(only `ExpectedFVType`, a transient type hint). So "gate on addressability *here*" is not
implementable inside `canAssignToRawInterfaceValue`. Addressability and rvalue-ness are
**expr-level** properties.

There is an exact, load-bearing precedent for the shape to use: **method-receiver
auto-address**. `receiverAssignable(src, recv)` already admits a value `T` against a `*T`
receiver at the *type* level (its rule table, `check_method.bn:364`, "src T → dst *T (OK;
auto-take-address)"), and `checkResolvedMethodCall` (`check_method.bn:224-242`) enforces the
**addressability** half *separately at the call site* where the receiver expr is in scope:

```
if recvKind == 0 && mKind == 1 && e.X != nil && e.X.X != nil && !isAddressable(c, e.X.X) {
    <error: cannot call pointer-receiver method M on a non-addressable value>
}
```

`isAddressable(c @ast.Expr) bool` (`check_addr.bn:72-108`) is the ready-made predicate
(ident→SYM_VAR, selector, slice/ptr/array element, `*p` deref, composite literal → true;
literal/const/call-result/arithmetic → false). It already backs `&`-expr diagnostics
(`check_addr.bn:162`), assignment-target checks (`check_assign.bn:84`), **and** the method
auto-address gate above.

**Design consequence.** Keep `AssignableTo` type-pure and **default-deny** for value→`*Iface`.
Do **not** make `AssignableTo` start returning true for a value source (that would silently
admit the borrow at *every* one of its ~20 callers, including the storing ones). Instead add a
single expr-aware construction helper, invoked as an **additional acceptance path** only at the
sites that construct a raw `*Iface`, mirroring how `stringLitInitFitsArray(valueExpr @ast.Expr,
valType, target)` is bolted next to `AssignableTo` at `check_decl.bn:409` /
`check_expr_composite.bn:130,139`:

```
// sketch — checker side
func canBorrowValueIntoRawIface(c @Checker, srcExpr @ast.Expr, srcType @Type, dstType @Type,
                                pos posKind) bool
//   dstType is raw *Iface (TYP_INTERFACE_VALUE) AND srcType is a value (not *T/@T/iface-value)
//   AND the value's *T reaches an impl for the iface   → reuse the impl-reachability half of
//     canAssignToRawInterfaceValue (factor :274-284 into a shared `valuePtrSatisfies(c, src, iface)`)
//   THEN:
//     isAddressable(c, srcExpr)      → true          (2a — any position)
//     else pos is BORROWING (arg / var-init)         → true (2b — materialise; see IR-gen)
//     else (storing: assign/field/return)            → false
```

The source expr the helper needs is **already in hand** at every site (each already reads
`<expr>.Pos` for `errCannotAssign`). The `posKind`, however, **cannot** simply be passed as a
site argument, because of composite literals (§4): `f(Opts{Any: 42})` emits the element's
assignability error **synchronously inside `checkExpr(argExpr)` → `checkCompositeLit` →
`checkStructLit`** (`check_expr_composite.bn:130`), deep below the argument site — the outer
site never gets to run a fallback for that element. So `posKind` must be **threaded into
`checkExpr`**, exactly like the existing `ExpectedFVType` transient hint.

**Mechanism: a transient `Checker.borrowPosKind` field, managed like `ExpectedFVType`**
(`check_expr.bn:33-40` — set, check the sub-expr, restore). A borrowing site (var-init,
call-arg) sets it to BORROWING around its `checkExpr`/`AssignableTo`; a storing site leaves the
default STORING; a composite literal in a borrowing position propagates its own kind to each
element check and **restores** between elements. `canBorrowValueIntoRawIface` reads
`borrowPosKind` (arg/var-init vs store) plus `isAddressable(c, srcExpr)` (2a vs 2b). The
save/restore discipline is what makes it **leaf-granular** and safe: a nested call element
`S{f: g(x)}` re-enters `checkExpr(g(x))`, whose own arg-check sets/restores `borrowPosKind` for
`g`'s argument, so the outer literal's kind cannot leak into `g(x)` — the same reason
`ExpectedFVType` doesn't leak. *(An earlier draft dismissed the field approach over exactly this
leak; `ExpectedFVType` is the working precedent that the worry is unfounded under save/restore.)*
The comparison-only callers (`check_expr_binop.bn:115/129`, `check_stmt.bn:295`) never set the
field and never reach the borrow helper.

Net churn: the type-only `AssignableTo` chain and its 64 test call sites stay **untouched**; the
addition is one `Checker` field + a set/restore at the ~11 borrowing sites + the borrow helper.
*(Rejected alternative: a `posKind` **param** on `AssignableTo` — touches the `.bni` decl, the
2 internal recursions at `types_assignable.bn:96/100`, all ~20 callers, the 3 comparison-only
callers, and all 64 test sites. Strictly more churn than the transient field, which matches
precedent.)*

### Commit 1 — lvalue auto-`&` (2a), all positions
- **Checker.** Factor the impl-reachability loop (`canAssignToRawInterfaceValue:274-284`) into
  a shared `valuePtrSatisfies(c, src, iface) bool` (it already calls
  `receiverAssignable(src, rec.RecvType)`, which already accepts value→`*T`, so no logic
  changes — just make it callable from the new helper). Add `canBorrowValueIntoRawIface`
  (§3.0) but **Commit 1 admits only the addressable branch** (`isAddressable` → true; the
  rvalue branch stays `return false`, delivered in Commit 2). Wire it in as the
  `!AssignableTo → try borrow` fallback at the **all-position** sites (both borrowing and
  storing — an *addressable* borrow is legal everywhere, exactly like `&x`).
- **IR-gen.** For an addressable value source, emit the **address-of** and feed the existing
  pointer path. There is no unified `emitAddrOfLvalue`; `genUnary` (`gen_expr.bn:173`) is
  the address-of dispatcher (`&ident`→`lookupVar`; `&a[i]`→`genIndexPtr`; `&s.f`→
  `genSelectorPtr`; `&*p`→the pointer). Factor that AMP-arm dispatch into a reusable
  `genLValueAddr(ctx, b, e @ast.Expr) @Instr` and call it from the value-borrow path so the
  resulting `*T` is fed to `wrapAsIfaceValue` unchanged (it already lifts a `*T`/`@T` via
  `OP_IFACE_VALUE`). **Where:** `wrapAsIfaceValue(ctx, b, val @Instr, dstTyp, srcExprTyp)`
  takes a lowered `@Instr`, **not** the expr — so the address-of must be emitted at its two
  callers, which *do* hold the AST expr (`gen_stmt.bn:380` has `d.Value`; `gen_util.bn:223`
  has `e`), or `wrapAsIfaceValue` gains an `@ast.Expr` param. Prefer emitting at the callers
  (least churn; keeps `wrapAsIfaceValue` pointer-only). Stay on the **raw** path — no `RefInc`
  (the managed arm at `gen_iface.bn:324` RefIncs; a plain-local `*any` borrow must not).
- **No positional logic** (lvalue is legal wherever `&x` is).
- **Tests:** `var iv *I = xVar` (was `cannot assign`, now ok); lvalue in field/element/arg/
  `return` positions; `@I` from a value still rejected. **Migration (mandatory, same commit):**
  see §6.1 — `035_err_value_to_raw` is a *negative* test whose case becomes **legal**, so it
  must be re-targeted in this commit or it goes red.

### Commit 2 — rvalue auto-temp (2b) + the positional check
- **Checker.** Enable the rvalue branch of `canBorrowValueIntoRawIface`: a non-addressable
  value source constructing a raw `*Iface` is admitted **iff** `posKind == BORROWING`
  (argument or `var`/`:=`-init); a storing position returns false → `errCannotAssign` (or a
  bespoke "temporary would dangle" diagnostic — decide, then pin `035.error` to it). This is
  the crux — see §4 for the exact site classification and the composite-literal recursion.
- **IR-gen** (`wrapAsIfaceValue` caller path). For a non-addressable value source, **materialise**
  a stack temp: `slot := b.EmitAlloc(T); <store v into slot>` → `slot` is a frame-lived `*T`
  (the same `OP_ALLOC` the variadic packer uses, `gen_variadic.bn:42`), fed to the existing box
  path. **`T` must be the DEFAULTED concrete type** — for an untyped constant source
  (`fmt.Print(42)` → int, `2.5` → float64) call `defaultType` (`checker_util.bn:40`) to pick the
  alloca element type; allocating the *untyped* type is what would re-trip the
  `box(<untyped constant>)` class of crash (§5). No `OP_BOX`, no heap; the outer `*any` borrow
  itself takes no `RefInc`.
- **The temp's OWN contents need refcount handling — split by source shape (CRITICAL, easy to
  miss).** The store is **not** always a plain `EmitStore`:
  - **Pointer-/managed-free value** (a scalar, or a **string literal** — a null-backing
    `@[]readonly char`, refcount-inert): a plain `b.EmitStore(slot, v)` is complete; the alloca
    is frame-lived and needs no cleanup. This covers the immediate scalar/string `fmt` path.
  - **Value carrying managed members** (a struct with `@T`/`@[]T` fields, or a heap-backed
    managed-slice): copying `v` into the temp takes **ownership** of those references, so the
    store must go through the managed-aware copy (`emitStoreManagedSlot` / `needsStructCopy`,
    as the variadic packer does at `gen_variadic.bn:43-44` — `if needsStructCopy(arrTyp) {
    registerTemp(ctx, arrPtr) }`) and the temp's managed fields **must** be RefDec'd, or the
    compiler leaks (violating the hard never-leak invariant). Crucially the **cleanup scope is
    position-dependent**: an **argument** temp can use statement-end cleanup (`registerTemp`,
    like the packer — the borrow doesn't outlive the call), but a **var/`:=`-init** temp must be
    RefDec'd at the **binding's scope end**, not statement end — statement-end RefDec would free
    the fields while the frame-lived `*any` still points at them (UAF). So this case **does**
    need per-position temp scoping. → **This is a real design item, not free.** Options: (i) build
    scope-scoped cleanup for the var-init managed case; or (ii) stage it — Commit 2 handles only
    pointer-/managed-free sources (the scalar + string-literal `fmt` path, which is the motivating
    use), and managed-carrying value sources land in a follow-up commit with the scope-scoped
    cleanup. **Decision for the owner** (don't unilaterally defer): which of (i)/(ii). Either way,
    Commit 2 must never emit a plain `EmitStore` of a managed-carrying value with no cleanup.
- **Lifetime: checker-enforced *at construction*, ordinary raw-UAF thereafter.** The `EmitAlloc`
  temp is **frame**-lived (verified: `OP_ALLOC` is entry-hoisted in LLVM and a fixed frame slot
  in native/VM; `registerTemp`/`ctx.Temps` is a managed-RefDec list, not a stack-slot reuser), so
  a pointer-free temp is valid across the statement (argument) and across the binding's scope
  (var-init). The checker's rejection of the escape *construction* positions (assign/field/return,
  §4) stops the temp being **built directly into** a longer-lived location. It does **not**, and
  cannot, stop a later statement from escaping an already-built named borrow (`var iv *any = 42;
  … ; return iv` — `return iv` re-checks `iv` as an iface-value→iface-value assignment
  (`types_assignable.bn:250-262`), which never reaches the borrow helper, so it is accepted and
  UAFs). That is the **ordinary raw-pointer escape hazard** (same as `var p *T = &local; return
  p`), which the spec explicitly leaves to the `bnlint` rule (Commit 3), not the checker. Do not
  overclaim the position rule as full lifetime enforcement.
- **Tests:** `fmt.Print(42, "hi")` compiles + runs; an expression arg `f(a+b)`; rvalue var-init
  `o := Opts{Any: 42}; use(o)` and `var iv *any = 42; use(iv)`; a **cross-statement**
  frame-liveness case (`var iv *any = 42; <other stmts>; use(iv)` prints correctly, proving the
  temp is frame- not statement-scoped); a **managed-field source** case (a value type carrying an
  `@[]char`/`@T`) in both an argument and a var-init position, checked for **no leak** (RefDec
  balance) and correct value — this is the case the scalar tests miss; **rejections** —
  `someStruct.field = 42`, `arr[i] = 42`, `return SomeIface{Any: 42}` each `.error`. Run in
  `builder-comp` **and** `builder-comp-int`.

### Commit 3 — `bnlint` escaping-borrow rule
The implicit lvalue borrow (2a) has **no visible `&`**, so an escaping raw interface value
built from a local reads like value construction and existing raw-escape lints miss it. And per
the Commit-2 lifetime note, the checker's position rule only guards *construction* — a named
`*any` borrow can still escape via a later statement (`var iv *any = 42; … return iv`), which no
checker path catches. Add a `bnlint` rule (`pkg/binate/lint`) flagging a raw interface value —
built from a **local or a materialized rvalue temporary** (2b), implicit **or** explicit `&` —
that escapes (returned, or stored into a longer-lived location). The rvalue-temp escape must be
in scope, not just named-local escapes. Per the compiler-emits-no-warnings rule this is a lint,
not a checker error; wiring it into hygiene/CI is a separate decision (don't).

### Commit 4 — flip Draft→Provisional
Once Commits 1–2 are conformance-green in every mode, flip `iface.construct.value-borrow`
Draft→Provisional on the stability axis. Docs only. **Exact sites** in
`docs/spec/11-interfaces-impl-self.md` (verified): (a) line 3 chapter Maturity banner — remove
value-borrow from the "two Draft rules" list, leaving only §11.12 `iface.assert.slice`;
(b) inline "(`iface.construct.value-borrow`, Draft)" near line 98; (c) inline near line 110;
(d) the rule definition at line 113 — add a per-rule Maturity override to Provisional (per
`04-notation.md:104`); (e) the block-quote note at lines 139-146 — flip "not yet implemented"
→ implemented/Provisional. Axis-1 stability only; Axis-2 conformance (Annex C) is separate
(`04-notation.md:116-119`).

## 4. The positional check (the sharpest piece)

The rvalue rule (2b) needs each construction site to declare whether it is a **borrowing**
position (argument, `var`/`:=`-init — temp co-scopes) or a **storing** position (assignment,
field/element store, `return` — temp would dangle). This is **statically known per site** (no
dataflow), so each site sets `Checker.borrowPosKind` (§3.0) around its check, which the borrow
helper then reads. Full site inventory (from recon; line numbers current):

| Context | Site(s) | posKind | Notes |
|---|---|---|---|
| `var x T = e` init | `check_decl.bn:392, 409` | **borrowing** | `:=` (`checkShortVarDecl`, `check_assign.bn:259-265`) does **not** call `AssignableTo` — infers via `defaultTypeForExpr`; harmless (nothing to reject) but a bare `x := 42` into `*any` can't hook here, so the composite/arg forms are the realistic `:=` cases |
| const init | `check_const.bn:70` | n/a | scalar-only (`errNonScalarConst`); rule inert |
| call argument (fixed) | `check_expr.bn:328`; `check_method.bn:132, 198, 264` | **borrowing** | |
| call argument (variadic/spread) | `check_expr.bn:377, 388, 412, 437` | **borrowing** | the `...*any` fmt path |
| composite element | `check_expr_composite.bn:106, 130, 139, 227` | **inherit** | allow **iff** the enclosing literal is itself in a borrowing position — see below |
| assignment | `check_assign.bn:100, 137, 193` | **storing** | `:137` (multi-return destructure) & `check_stmt.bn:197` have **no per-source expr** (source is a call *result*); a call result is non-addressable anyway → pass a nil/non-addressable marker so they reject |
| `return` | `check_stmt.bn:197, 209` | **storing** | |
| ==/!=/<… operand | `check_expr_binop.bn:115, 129` | **exclude** | comparison, symmetric — must **not** invoke the borrow helper |
| switch case vs tag | `check_stmt.bn:295` | **exclude** | comparison |

**Composite-literal recursion.** A composite element's assignability error is emitted
**synchronously inside `checkExpr(literal)` → `checkCompositeLit` → `checkStructLit`**
(`check_expr_composite.bn:130`), below the enclosing site — so the enclosing site cannot run a
fallback for it, and the element leaf itself must consult the `posKind`. What the plan needs is
the *inheritance* direction: a literal in a **borrowing** position (`o := Opts{Any: 42}`,
`f(Opts{Any: 42})`) must let its elements borrow, while `someStruct.field = Opts{Any: 42}`
must not. Deliver via the `Checker.borrowPosKind` field (§3.0): the borrowing site sets it
BORROWING before `checkExpr(literal)`; `checkCompositeLit` for a borrowing literal propagates
it to each element check and **restores** it between elements. The save/restore discipline (the
`ExpectedFVType` pattern) is what keeps this leaf-granular — a nested **call** element
`S{f: g(x)}` re-enters `checkExpr(g(x))`, whose arg-check sets/restores the field for `g`'s
argument, so the literal's kind cannot leak into `g(x)`. Leaf granularity is mandatory, and the
save/restore field is the vehicle that achieves it (an earlier draft wrongly rejected the field
over a leak that save/restore prevents).

## 5. Related items (NOT this plan, but adjacent — flag for the implementer)

- **`box(<untyped constant>)` miscompile** (`claude-todo.md:113-123`, 🔴 confirmed still
  crashing) — `box(42)`/`box(2.5)`/`box(7+1)` emit invalid LLVM ("extractvalue operand must be
  aggregate type") and segfault the VM. **Root cause:** IR-gen boxes the *undefaulted*
  `TYP_UNTYPED_INT` (`gen_builtin.bn:227` uses `val.Typ` directly), while the checker defaults
  it (`check_builtin.bn:54` `defaultType(rawType)`) — checker/IR-gen divergence, on the
  **managed** `box`→`OP_BOX`→`rt.Box`→`@any` path. Commit 2 **sidesteps** it on two counts
  (raw stack alloca, not `OP_BOX`/managed; and it defaults the type before allocating) — but
  **only because it defaults**: if Commit 2's alloca element type were left untyped, the same
  untyped-type-into-`ensureAnyImplInfo` crash could recur (both funnel through
  `wrapAsIfaceValue`→`EmitIfaceValue`). So Commit 2 **must** `defaultType` first (already
  required above). The `box(42)` fix itself is the one-liner mirror at `gen_builtin.bn:227`
  (`defaultType(val.Typ)`); it is a natural companion but **does not block** this plan, and
  this plan does not fix it. Verify with a `fmt.Print(42)` (untyped) test.
- **Name-less MANAGED `@any` crash** (`plan-slice-type-identity.md` §9, open MAJOR) — the
  `@any` path; value-borrow is **raw-only**, so orthogonal and non-blocking. (It bites
  `box(slice)`-into-`@any`, which value-borrow doesn't touch.)
- **Struct value-*recovery*** (`x.(SomeStruct)`) is still deferred — so `fmt` can format a
  struct arg only once that lands; value-borrow gets the struct value *in* fine, but the
  formatter can't read it back yet. Out of scope here.

## 6. Tests, migration & bookkeeping

### 6.1 Migrating `035_err_value_to_raw` (mandatory in Commit 1)
`035` is a **negative** test (`.error`), not an xfail: it passes today because
`var iv *I = t` (with `t` a *variable* = lvalue, `035.bn:23 var t T`) **fails** to compile with
`cannot assign T to \*I`. Commit 1 makes exactly that case **legal**, so the compile will
**succeed** and `035` will **fail** (expected error not produced) — there is no xfail escape
hatch. Re-target it, in the same commit, to a case that stays rejected **after both** Commits
1 and 2: a **non-addressable** value source (call result) in a **storing** position, e.g.

```
interface I { foo() }
type T struct { x int }
func (t *T) foo() { }
impl *T : I
func mkT() T { var t T; return t }
func main() { var iv *I; iv = mkT(); _ = iv }   // assignment-store of an rvalue → rejected
```

This is rejected at Commit 1 (non-addressable → borrow helper's rvalue branch not yet enabled)
**and** at Commit 2 (rvalue in a storing position). Update `035.rules` to cite
`iface.construct.value-borrow` (declared at `11-interfaces-impl-self.md:113`, so no
spec-coverage DANGLING). Pin `035.error` to whatever diagnostic the assignment path actually
emits — at Commit 1 that is still the generic `cannot assign T to \*I`; if Commit 2 introduces a
bespoke "temporary would dangle" message for storing positions, update `035.error` in Commit 2.
`036_err_value_to_managed` (`@I` from a value) **stays rejected unchanged** — value-borrow is
raw-only; no migration.

### 6.2 Verify
- **Unit:** `pkg/binate/types` + `pkg/binate/ir` (+ `pkg/binate/codegen` if the LLVM
  `OP_IFACE_VALUE` path is touched). Both backends consume `OP_IFACE_VALUE` `Args[0]`
  verbatim, so no per-backend *construction* edit — but **verify the one native/VM asymmetry**:
  a 2a auto-`&` of a **global** lvalue yields an `IsGlobalRef` pseudo-instr (ID=-1) that the
  native x64 path special-cases (`pkg/binate/native/x64/` — `x64_regmap.bn`, `x64_emit.bn`,
  `x64_iface.bn`) and the VM path does not — confirm the VM (`vm_exec_iface.bn`,
  `BC_IFACE_VALUE`) boxes a global-address `Args[0]` correctly. (Risk is low: a 2a auto-`&` of a
  global emits IR **identical** to the already-working explicit `&global`, so this is a
  regression-check, not new codegen.)
- **Conformance:** the new positive + negative tests in **`builder-comp`** (LLVM) **and**
  **`builder-comp-int`** (VM boxing path) + one native mode; plus the frame-liveness
  cross-statement test. `scripts/hygiene/run.sh`.

### 6.3 Bookkeeping
Update the existing `claude-todo.md` entry "🔴 Implicit variadic value→`*any` boxing" (`:101-106`,
inside the "Type assertions, type switches & RTTI" section) to point at this plan, and move it to
`claude-todo-done.md` as Commits land. This is **distinct** from the `box(<untyped constant>)`
entry (`:113-123`) — do not conflate them.
