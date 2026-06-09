# Code Red 2 — Follow-up Audit: the Empty-Cell Meta-Pattern

A follow-up to `plan-code-red.md`, run on the bugs surfaced **since or during-but-
outside** the original code red (mostly 2026-06-05..07), fixed or not. The
original code red cataloged a defect set and built the coordinate-addressed
conformance matrix family (`conformance/matrix/{refcount,scalar,scalar-diff,abi,
addr-aggregate,aggregate,const,loop-leak}`). This audit asks the same questions
of the *next* batch of bugs: what recurring patterns produced them, what test
gaps hid them, and — generalizing — where the *next* similar bugs are.

The execution split for the fixes lives in `plan-cr2-1-frontend.md`,
`plan-cr2-2-codegen.md`, `plan-cr2-3-backends.md` (three disjoint concurrent
plans). The defect-of-record stays in `claude-todo.md`.

---

## 0. The diagnosis (one sentence)

Every off-default bug in this batch is an **unfilled cell in an implicit
`FEATURE × MODIFIER × POSITION` product**: a feature works at its *default
coordinate* (plain `int`, direct call, value position, canonical bits) and an
untested *combination* breaks — exactly the shape of the original code red's
"empty matrix intersection," now recurring in new regions (globals, the
`readonly` wrapper, operator sibling-paths, result-side dispatch).

This is not a coincidence of unrelated bugs. It is one structural failure mode
expressed many times. Two invariants, if enforced by the test protocol, make the
whole class impossible to reintroduce silently.

---

## 1. The meta-pattern and its two invariants

The product whose cells keep coming up empty:

```
{operation / feature}
  × {type-shape modifier: sub-word width, signedness, named-wrapper,
       readonly-wrapper, managed/aggregate shape, raw-ptr}
  × {call / dispatch shape: direct, iface-vtable, func-value}
  × {storage position: local, param, return, field, array-elem,
       package-global, static-local, extern-cross-package}
  × {side: value/param vs result}
  × {locality: same-pkg vs cross-pkg}
  × {arity}
```

asserting **VALUE + REFCOUNT + COMPILES on every backend** (LLVM, VM, native
aa64/x64/arm32) for **every** cell. The default-coordinate test passes precisely
because it lands in the one path / one peel that happens to be implemented; only
*generating the off-default cells* forces the rest to exist.

Two invariants are the levers:

### Invariant A — Wrapper transparency

`TYP_READONLY`, `TYP_NAMED`, and `TYP_ALIAS` are **semantic-only wrappers**: they
change the type's *meaning*, not its runtime representation. Any code that
switches on `Type.Kind` to decide a representation (LLVM slot type, zero-init
token, refcount predicate, method-dispatch target, struct-type discovery) **must
peel all transparent wrappers first**. A missed peel in *any one* of the four
parallel lowerings (IR-gen, codegen, VM, native) is a silent miscompile (wrong
zero token, literal-0 field read) or a spurious compile error (readonly iface
can't dispatch). The test protocol must sweep **every wrapper over every shape in
every position** so a missed peel anywhere lights up.

### Invariant B — Path parity

A feature is lowered in **N parallel code paths**:
- binary-expr vs **compound-assign** vs unary (operators);
- direct vs **iface-vtable** vs **func-value** (calls);
- **param-side** vs **result-side** (ABI);
- `m.Funcs`-scan vs **`m.Globals`-scan** (struct-type discovery);
- LLVM vs VM vs native aa64/x64/arm32 (backends).

A guard or fix added to one path is **never** automatically mirrored into its
siblings. The protocol must drive each feature through **all N paths**, because
"works on the path we tested" is the exact illusion that hides the others.

> The confirmed new bug from this pass is a clean Invariant-B failure: the
> overshift guard `emitGuardedShift` exists on the expression-shift path but not
> on the compound-assign path, so `uint32 y <<= 40` → `256` on LLVM instead of
> the spec's `0` (the expression form `y << 40` is correct). See claude-todo MAJOR.

---

## 2. The five new bug-classes

Beyond the original 8 classes. Each is a region of the product whose cells the
existing matrices structurally never reach. Locations are source-confirmed; "live"
= unfixed as of this audit.

### Class A — Globals materialization (off-default-shape global/static storage)

A package-level `var`/`static` of a non-plain-`int` type mis-lowers because the
global path enumerates type kinds by hand and never peels wrappers or scans
`m.Globals`.

- Float global `global double 0` (fixed); iface/func/readonly globals (fixed) —
  all hit the hand-rolled static-zero dispatch in `emit.bn` (~173-196) which peels
  only `TYP_READONLY`.
- **live**: named-over-aggregate/iface/func/managed-ptr global emits a valid LLVM
  *type* but an invalid zero *token* (`%BnIfaceValue 0`, `[3 x i64] 0`, `i8* 0`) —
  the dispatch never peels `TYP_NAMED` (`llvmType` does, so type is right, token
  wrong).
- **live**: cross-module by-value struct/array global referenced *only* through a
  global → `use of undefined type` — `collectStructTypes` (`emit_types.bn`) scans
  only `m.Funcs`, never `m.Globals`; `discoverStructFromType` lacks `TYP_NAMED` /
  `TYP_ARRAY` recursion arms.
- **live**: nested-array `[N][M]T` global initializer stores the inner alloca
  *pointer* into the element slot — `gen_composite.bn` `genArrayLit`'s
  nested-composite guard fires only for `TYP_STRUCT` elements.

**Gap:** there is **no `globals` matrix.** `aggregate/global` sweeps only
`{scalar,array,struct}×{int,float}`; `addr-aggregate` has every op *except*
`global`. The whole `{global} × {iface,func,slice,managed,named,nested-array,
cross-module,readonly}` grid is structurally untested — so each fix patched one
cell, and the next off-default shape re-broke.

### Class B — Readonly-wrapper transparency

The `readonly`/`const` modifier is a transparent wrapper that read and dispatch
paths don't peel.

- **live**: `var rp readonly @Box = p; println(rp.v)` reads `0` silently —
  `gen_selector.bn` falls through every `Kind`-test for a readonly value and emits
  `EmitConstInt(0)`.
- **live**: `readonly @Iface` can't be dispatched (hard compile error) —
  `check_method.bn` `ResolveAlias` follows only `TYP_ALIAS`, so a readonly iface
  never reaches dispatch.

**Gap:** no `readonly` matrix. This is Invariant A applied to one wrapper.
Distinct from the *const-value materialization* the `const` matrix covers.

### Class C — Operator result-type / sub-word / path-parity

An operator is lowered in four parallel places (IR-gen binary, IR-gen unary, VM,
native), and a guard added to one is not mirrored to the others; plus the checker
accepts aggregate `==`/`!=` codegen can't lower.

- **live (new)**: compound shift-assign bypasses `emitGuardedShift` (§1).
- **live**: native `emitUnop` (`~`/unary-`-`) lacks the `emitSubWordNarrow` that
  `emitBinop` has → dirty upper bits when the result is consumed without
  re-narrowing (predicted; hard to expose because most consumers re-narrow).
- **live**: `==`/`!=` on slices/managed-slices/func/iface/struct accepted by the
  checker → invalid `icmp` (claude-todo MAJOR; semantics are an open spec gap, the
  fix is the checker-reject).
- (fixed, same class): `~` hardcoded `int` result; relational untyped-literal-left
  unsigned compare; shift-by-≥-width expression form.

**Gap:** no `operator` matrix. `scalar`/`scalar-diff` cover sub-word arithmetic
*values* but not the `op × lvalue-form × operand-position` grid where the
path-parity bugs live.

### Class D — Result-side indirect-call shape

A value crosses a call boundary fine via a *direct* call with plain-`int`
components but breaks once the boundary is *indirect* (iface vtable / func-value)
on the **result** side — the registry result-type plumbing, the front-end
multi-assign expansion, and the per-backend tuple packers were each generalized
only along the *param* axis and only for plain-`int` single results.

- iface dispatch of a ≥2-result method → `extractvalue void`
  (`ModuleInterface.MethodResults` stores one type, `nil` for ≥2);
- func-value multi-return destructure → typecheck reject (`check_stmt` guards on
  `TYP_FUNC` only);
- native funcval/iface tuple packers mis-pack sub-word / non-8-multiple / managed
  components.

**Coverage:** the `abi` matrix gained a result-side call-shape axis
(`{iface,funcval}-{return,multi-return}`, value/packing, landed `546ad30a`); the
**refcount discipline** for a managed tuple *component* through dispatch is now
covered by `conformance/matrix/dispatch-refcount` (`@T`/`@func`/`@Iface` via
iface-dispatch, balance-invariant assertion) — **all green on every backend**, so
the SEAM-fixed dispatch path refcounts managed components correctly. **Still
open** (smaller): the `funcval`-producer and `managed-slice`-component cells, and
cross-pkg × result-side.

### Class E — Named / cross-package type-resolution recurrence

Code-red Class 3 recurring in new positions: a type reference (named-distinct,
cross-package, self-referential, transitively re-exported) is resolved before its
definition is registered, or a wrapper-over-aggregate is forgotten in a new
storage position, so it falls back to a wrong representation (`i8*` / `i64` /
undeclared / bare-`0`).

- fixed: `resolveTypeExpr` named → `TypInt()`; self-ref iface return → `i8*`;
  cross-pkg `@Iface` return → `i8*`.
- **live**: named-over-aggregate global zero-init (Class A); cross-module
  by-value struct/array global type-decl never emitted (Class A); transitively
  re-exported iface = one import-alias hop too few (predicted).

**Gap:** the named-distinct-wrapper axis is absent from every matrix; the
`package-global` and `extern-cross-package-global` positions are skipped by
`aggregate`/`addr-aggregate`.

---

## 3. The test protocol (the matrices to build)

Five matrices, coordinate-addressed and generator-driven like the existing
family. `globals` and `readonly` are **built this pass**; the rest are specced
here for follow-up.

### 3.1 `conformance/matrix/globals` — [high, BUILD NOW]

- **Axes:** `storage {global-init, global-noinit, static-local}` ×
  `type {int, int8/16/32, uint8/16/32, bool, char, float32, float64, struct,
  nested-array[N][M], array-of-struct, raw-slice, managed-slice, managed-ptr,
  iface-value, func-value, named-scalar, named-float, named-struct, named-array,
  named-iface, named-managed-slice, named-func, readonly-aggregate}` ×
  `access {same-pkg-read, cross-pkg-read}`.
- **Assertion:** the module compiles (no `integer constant must have integer
  type` / `use of undefined type`), and every component reads back the initialized
  value (or zero for no-init); a `0/1` PASS lane per component. Cross-pkg cells
  additionally require the consumer to declare any aggregate/named type it
  references by value. Runs on **all** modes incl. native (the VM materializes
  globals via a separate `vm/lower_data.bn` path that must agree).
- **Red-first:** named-over-iface, named-over-array, named-over-struct,
  named-over-managed-slice/func, nested-array `[2][2]int`, cross-pkg by-value
  struct global, cross-pkg array-of-struct global.

### 3.2 `conformance/matrix/readonly` — [high, BUILD NOW]

- **Axes:** `inner-shape {scalar, named-scalar, struct-value, array, raw-slice,
  managed-slice, raw-ptr-to-struct, managed-ptr-to-struct, iface-value-mgd,
  iface-value-raw, func-value}` × `operation {var-init, global-init, field-read,
  field-write-rejected(.error), index-read, method-call-nonmut, iface-method-call,
  pass-as-arg, return, compare, addr-of-field}`.
- **Assertion:** value-producing ops → a readonly view observes byte-identical
  bits to the non-readonly cell; dispatch ops → a non-mutating method/iface call on
  a readonly receiver succeeds and matches the plain result; write ops → REJECTED
  (`.error` cell). Pin every backend. (Operationalizes Invariant A for the
  `readonly` wrapper.)
- **Red-first:** readonly × managed-ptr-to-struct × field-read (literal-0),
  readonly × raw-ptr-to-struct × field-read, readonly × plain-struct × field-read,
  readonly × managed-slice × index-read, readonly × iface × method-call (compile
  error), readonly × managed-struct-global × field-read (the `io.EOF` shape).
- **Note (language-design vs bug):** whether `readonly` *permits* non-mutating
  method calls is a design question (cf. Rust `&self`, C++ const methods); the
  *silent wrong field-read* is unambiguously a bug. The `.error` cells encode the
  design decision and must be ratified before they're pinned.

### 3.3 `conformance/matrix/operator` — [high, FOLLOW-UP; targeted + sampled]

Per the scope decision: **exhaustive on the confirmed/predicted gap cells, plus a
*meaningful sample* of the broader product** (not the full Cartesian, which is
thousands of cells already partly covered by `scalar`/`scalar-diff`).

- **Axes:** `op {+ - * / % << >> & | ^ ~ unary- == != < <= and the compound forms
  += -= *= /= %= <<= >>= &= |= ^=}` × `width/sign {i8 u8 i16 u16 i32 u32 i64 u64}`
  × `operand-position {var-var, untyped-literal-left, untyped-literal-right,
  dirty-operand (operand = ~x/-x/overflow consumed directly)}` ×
  `lvalue-form (compound-assign) {ident, field, index, deref}` × `wrapper {plain,
  named}`.
- **Sampling strategy (the "in-between"):**
  1. **Exhaustive** over the confirmed/predicted-gap sub-grids:
     - every compound-assign op × every lvalue-form (path-parity: compound vs
       expr) — pinned with the overshift case;
     - unary `~`/`-` × sub-word × consumed-by-compare (Invariant-B native narrow);
     - `==`/`!=` × every aggregate operand (checker-reject `.error` cells);
     - untyped-literal-left/right × signed/unsigned sub-word (signedness).
  2. **Pairwise (all-pairs) sample** over the remaining `op × width × sign ×
     position` product: generate a covering array so **every pair of axis values
     co-occurs in at least one cell**. Pairwise catches the overwhelming majority
     of interaction bugs at a small fraction of the full-product size, and is a
     principled, reproducible "sample" (the generator emits the same covering
     array each run — target-stable like `scalar-diff`'s seeded sets).
- **Assertion:** spec-oracle value correctness (full-precision, target-stable
  self-check) for well-defined cells; checker-reject `.error` for the aggregate
  `==`/`!=` cells. Each `op × position` must produce the spec value **regardless of
  which lowering path emits it** — that is the path-parity assertion.

### 3.4 `conformance/matrix/abi` + `refcount` extensions — [high, FOLLOW-UP]

- **abi:** add a **component-type** axis `{int, u16, u32, int64 (register-pair /
  32-bit split), @T, @Iface (2-word), @[]T (4-word), *T, named-wrapper}` and a
  **cross-package** axis, crossed with the existing `side {param,result} ×
  call-shape {direct,iface,funcval} × arity`. Add a mixed param+result cell family.
- **refcount:** add an **indirect-producer** axis `{direct, iface-dispatch,
  funcval}` to the existing multi-assign/multi-short-var managed-component forms —
  so a managed tuple component produced through dispatch is checked for
  balance/leak, not just value.
- **Assertion:** every component's value survives **and** managed components return
  to refcount baseline (no leak/UAF) on **all** backends — because the confirmed
  defects split across LLVM (compile error), VM (sub-word pack), and native (silent
  wrong-code), so a compile-only or VM-only check passes a native silent miscompile.

### 3.5 `conformance/matrix/type-resolution` — [medium, FOLLOW-UP]

- **Axes:** `type-reference {primitive, named-distinct-scalar, named-over-struct,
  named-over-array, named-over-managed-slice, named-over-iface, named-over-func,
  self-referential-iface, forward-referenced-named, cross-pkg-struct,
  cross-pkg-iface, cross-pkg-named, transitively-re-exported-iface}` × `position
  {local, package-global(±init), field, array-elem, param, return,
  extern-cross-pkg-global}`.
- **Assertion:** compiles to valid LLVM, the emitted slot type matches the true
  layout/width (peeling named/readonly), and the stored value reads back on every
  backend. Heavy overlap with `globals` (named-over-aggregate × global is in both);
  build `globals` first and let `type-resolution` reference its cells rather than
  duplicate.

### 3.6 Every cell runs in all relevant modes, asserts value-correctness

The cross-backend, value-asserting discipline is load-bearing: the recurring
lesson (most recently the abi result-side sweep) is that **VM-only or
compile-only checks pass native silent miscompiles**. Every new matrix runs LLVM
+ VM + native, and asserts the *value* (and, for managed cells, the *refcount*),
not just "it compiled."

---

## 4. The fix split (three disjoint concurrent plans)

The confirmed root-cause fixes partition cleanly by subsystem so three workers run
in parallel. The **only seam** is one IR field —
`ModuleInterface.MethodResultsFlat`/`MethodResultCounts` — **defined** by Plan 1
and **consumed read-only** by Plans 2 and 3.

| Plan | Owns | Theme | Doc |
|---|---|---|---|
| **1** | `pkg/binate/ir` + `pkg/binate/types` only | Front-end type resolution, wrapper-peeling, result-type plumbing, checker guards | `plan-cr2-1-frontend.md` |
| **2** | `pkg/binate/codegen` only | LLVM emission: global zero-token + struct-type discovery, indirect-call signatures | `plan-cr2-2-codegen.md` |
| **3** | `pkg/binate/native` + `pkg/binate/vm` only | Backends: sub-word narrow path-parity, aggregate packing, VM tuple unpack + global parity | `plan-cr2-3-backends.md` |

Disjointness holds because the three layers (IR/types ↔ codegen ↔ backends) are
file-disjoint and the cross-layer dependency is a single IR struct field, edited
once (Plan 1) and read elsewhere. The same *conceptual* bug (e.g. a byval/sret
convention, a wrapper peel) appears in more than one plan, but in **different
files** (Plan 1 peels on the IR read path; Plan 2 peels on the LLVM write path;
Plan 3 peels in the native/VM lowerings) — so they don't edit the same function.
Land small and cherry-pick early (stay-close-to-main) to keep the seam shallow.

---

## 5. What this pass confirmed vs predicted

Probed serially (the analysis agents were read-only):

- **Confirmed new** (filed, claude-todo MAJOR): compound shift-assign overshift
  (`uint32 <<= 40` → 256 on LLVM; native correct). A clean Invariant-B failure.
- **Since-fixed** (predicted red, probed green): >16-byte struct byval-arg +
  struct-return through *one* iface dispatch passes on LLVM — the abi `three-int`
  indirect-byval SIGSEGV at todo:621 is no longer reproducing for this shape (only
  arm32 abi xfails remain). Re-verify on native in Plan 3.
- **Predicted, hard to expose**: native `emitUnop` sub-word narrow — source-strong
  (`emitBinop` narrows, `emitUnop` does not) but masked whenever a consumer
  re-narrows; Plan 3 confirms by reading the code and crafting a no-re-narrow probe.
- **Already filed, reproduce as predicted**: named-over-aggregate global,
  nested-array global, cross-module struct global, readonly-managed field-read,
  readonly-iface dispatch, `==`/`!=` on aggregates, iface multi-return.

---

## 6. Status & sequencing

- **Done:** this audit (Classes A–E, the meta-pattern, the test protocol spec, the
  3-way split); the abi result-side matrix (Class D value/packing, landed
  `546ad30a`); the compound-shift-assign filing.
- **This pass:** build `conformance/matrix/globals` and `conformance/matrix/readonly`
  (§3.1, §3.2) with generators + cells + xfails, mirroring the existing matrix
  family; the red cells pin the live Class-A/B defects for Plans 1–2.
- **Follow-up:** the `operator` (targeted + pairwise-sampled), `abi`/`refcount`
  extensions, and `type-resolution` matrices (§3.3–3.5); then execute Plans 1–3.

The forcing function, restated: make the generators sweep **every wrapper over
every shape in every position through every lowering path**, asserting value +
refcount + compiles on every backend. The bugs in this audit are precisely the
cells that sweep would have been red on from the start.

---

## 7. Round-2 validation (2026-06-08) — the reviews found the siblings

Adversarial reviews of the *landed* CR-2 fixes (Plan-1 and Plan-3) filed ~13 new
defects — and almost every one is a **path-parity / wrapper-transparency
sibling** of a fix that already landed: the same bug one variant over from the
cell the fix covered. The meta-pattern (§1) confirming itself: a fix that
peels/guards at *some* of the sites sharing a root cause leaves the others. Folded
into the Round-2 sections of `plan-cr2-{1,2,3}.md`. Representative siblings:

- Defect 1 (readonly field-read) peeled the OUTER `readonly @Box`; the
  **inner-pointee** `@readonly Box` / `*readonly Box` still reads literal-0 (and
  `&field` SIGSEGVs) — `gen_selector` reads an un-peeled `.Elem`.
- Defect 1's nested-array sibling covered the managed-ptr *read*; the
  **value-struct** variant and the **write** path (`a[i][j].field = …` stores
  nowhere) share the one type-resolver (`getIndexElemType`) never taught the
  nested-index base.
- Defect 2 (readonly receiver) peeled `TYP_READONLY` at dispatch; the **alias**
  receiver (`type AB = @Box`) isn't peeled (`ReceiverBaseNamed` has no `TYP_ALIAS`
  arm).
- Defect 9 (unary-minus sub-word) gated on `TYP_INT`; the **named** sub-word
  (`TYP_NAMED`) variant still emits `sub i64 0, %i8` — strictly weaker than the
  `~` fix it claimed to mirror.
- The `emitValRef` global-address migration fixed the return form; the
  **`OP_CAST`** and **iface-arg** value-operand sites were skipped.

The lesson is Invariant B (path parity): a fix must be driven through ALL N
sibling sites, and the regression net must sweep every wrapper/position so the
untested sibling lights up. The `globals`/`readonly` matrices should grow the
wrapper-ORDER (`@readonly` vs `readonly @`), named-vs-readonly, and read-vs-write
axes these siblings expose.
