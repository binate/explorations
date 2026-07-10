# Plan (brief): matrix tests for expanded generics + type assertions/RTTI

**Status:** brief plan (2026-07-10). Proposes two new `conformance/matrix/` families,
extending the established matrix pattern (`plan-code-red.md` §7; existing families:
`refcount`, `dispatch-refcount`, `abi`, `scalar`, `readonly`, …). Grounded in the current
tree. Not yet built.

## Why now
The recent MAJOR/CRITICAL bug cluster was almost entirely in these two spaces, and several
were **false-green because they were only exercised in-package or in one combination**:
`8d9e7577` (cross-package generic container on a managed element), `c14dd95e`/`aba92526`
(named-distinct wrapper element, dtor + copy), `42b3bc83` (two instantiations conflated on a
func-value/array type arg), `2d48f348`/`fedbd0c5` (method value on a generic instantiation —
landed; was false-green because only the direct-call path was tested, not method-value under
the name-mangling backends).
Point tests miss the cross-axis combinations; a matrix over the axes that hid these makes the
whole family regression-proof and surfaces the tail.

## A. Generics matrix — buildable NOW (feature is landed)
`conformance/matrix/generic-managed/` (name TBD). The axes are exactly the ones that hid the
recent bugs:
- **type-arg / element kind:** scalar · managed-ptr `@Foo` · managed-slice `@[]char` ·
  **named-distinct wrapper over a managed** (`type Buf @[]@Box`) · struct-with-managed-field ·
  array-of-managed · func-value · another generic instantiation.
- **instantiation site:** **in-package vs cross-package** (the single most bug-dense axis —
  in-package coincidentally aligns the mangled prefixes, cross-package diverges).
- **operation:** construct · copy · destroy (empty AND populated — the named-wrapper dtor bug
  only showed empty) · direct method call · **method value** · **method expression** ·
  dispatch through a **parameterized-receiver impl** (`impl *Cursor[T] : Iterator[T]`) · through
  a generic constraint.
- **backend/mode:** the default mode set (compiled LLVM · VM · native x64/aa64/arm32).

Invariants per cell: **links + runs** (no undefined-symbol / extern-not-found — catches the
mangler family), **refcount balance** for managed elements (mortal source, RefInc/RefDec net
zero — catches leak/UAF), and **type distinctness** (two cells differing only in a type arg are
distinct types — catches `42b3bc83`). Reuse the `refcount`/`dispatch-refcount` generator +
balance-assertion harness.

## B. Type-assertion / RTTI matrix — split by what's landed (corrected 2026-07-10)
`conformance/matrix/type-assert/`. **NOT wholesale-gated** — the assertion FORM is landed in
compiled mode. Split the family by feature status:
- **Assertion cells — BUILDABLE NOW (compiled mode).** `x.(K T)` (abort) and `v, ok := x.(K T)`
  (comma-ok) are implemented: parser (`parse_assert.bn`), checker (`check_assert.bn`), IR-gen
  lowering (`gen_assert.bn` / `gen_assert_commaok.bn` / `gen_assert_iface.bn`), with conformance
  `998`–`1015` already covering concrete `x.(*T)`/`x.(@T)`, interface target, transitive-ancestor,
  comma-ok, and err/unset cases. So the assertion sub-grid (both forms, concrete + interface
  targets, AND the recovery-kind-legality compile-error cells — `@T`-from-`*I` rejected, etc.)
  builds now in the compiled (`builder-comp`) mode.
- **Type-switch cells — GATED on Phase 6 (IR-gen lowering).** The type-switch form has a parser +
  partial checker (`check_stmt.bn:97`) but NO IR-gen lowering (no `STMT_TYPE_SWITCH` in
  `pkg/binate/ir`) and 0 conformance tests. Design the type-switch sub-grid now; land it when
  Phase 6 lowering is in. (This same Phase-6 lowering also unblocks a `pkg/std/fmt.Print(...*any)`
  scalar fast-path, which is a type switch — cross-noted; variadics is NOT a blocker there, it's
  landed + conformant, `conformance/spec/10-functions/165`–`200`.)
- **VM / cross-mode-agreement axis — GATED on Slice 5.** All RTTI is compiled-mode-only today
  ("Slice 4"); the VM path is Slice 5. Add the `mode: compiled · native · VM` axis + the
  cross-mode-result-agreement assertion when Slice 5 lands.
- **source:** `*I` · `@I` · `*any` · `@any`.
- **recovery kind × target:** `@T` / `*T` / value, against a **concrete** target (scalar ·
  struct · ptr-to-struct · slice · managed-slice · generic instantiation) and against an
  **interface** target (direct satisfaction AND **transitive-ancestor** satisfaction).
- **form:** `x.(K T)` (abort) · `v, ok := x.(K T)` (comma-ok) · type switch (multi-case,
  default, typed-nil→its-type, unset→default).
- **outcome:** match · no-match · absent.
- **mode:** compiled · native · VM — asserting **cross-mode agreement on the result** (the spec's
  cross-mode-on-the-result requirement).

Invariants: **recovery-kind legality** (`@I`→`@T`/`*T`/value; `*I`→`*T`/value; `@T`-from-`*I`
rejected — the spec rule, tested as compile-error cells), **match correctness** per form
(abort vs `ok=false` vs default), **refcount balance** on a successful `@T` recovery from `@I`
(ownership transfer — a natural extension of the `refcount` family), and **cross-mode result
agreement**. The failed-assertion abort is a real §17.5 panic — cross-check it lands there.

## Sequencing & scope
1. **Generics matrix first** (now) — it guards a landed, bug-dense feature and needs no new
   language support. Start with the `element-kind × in/cross-package × operation` core (the
   bug-dense sub-grid), add method-value/expression/parameterized-impl axes next.
2. **Type-assertion matrix** — build the ASSERTION sub-grid NOW in compiled mode (both forms,
   concrete + interface targets, transitive-ancestor, and the recovery-kind-legality compile-error
   cells — all landed, see §B). Design (don't yet emit) the type-switch sub-grid; land it with
   Phase-6 IR-gen lowering. Add the VM / cross-mode-agreement axis with Slice 5.
3. **Adopt, don't wire:** add the matrices + their generators under `conformance/matrix/`; wiring
   any new hygiene/CI gating is a separate decision (per CLAUDE.md "stay within scope").

Each is a Python generator (mirroring `gen-diff-scalar.py` / `gen-addr-aggregate-matrix.py`)
emitting cells + `.expected`, with the balance/agreement assertions in the emitted `.bn`.

## Review corrections (2026-07-10, adversarial review; grounded in the tree)
Verdict: sound + buildable now. Template = `conformance/gen-dispatch-refcount-matrix.py`
(near-verbatim); `run.sh` auto-discovers `conformance/matrix/**.bn` (no runner change);
`pkg/builtins/rt` is auto-allowed by conformance-imports. Load-bearing refinements:
- **Balance harness uses the RELATIVE form** (`rt.Refcount(po) == before+1` / `== before`,
  `.expected` = `[1,<v>,1]`), baseline-independent across element kinds (iface baseline is 2).
- **Cells define their OWN generic container inline** — cross-package via a per-cell
  `pkg/<name>.bni` body-included generic (mirror `conformance/995`'s `gholder.bni`),
  in-package via the single `.bn` (mirror `conformance/1011`).  Do NOT `import
  pkg/stdx/containers/*` — not on the conformance-imports whitelist.
- **Type-distinctness = compile-error PAIR cells** (`.error` = `cannot assign`), not runtime,
  with within-kind sub-grids: array LENGTH `[3]` vs `[5]` (mirror `1017`), func SIGNATURE
  `(int)uint` vs `(bool)uint` (mirror `1016`).
- **Add the unnamed bug shapes**: `named-array` element `type NArr [3]@Box` (aba92526 arm-ii,
  mirror `1011`) alongside `named-wrapper` `type Buf @[]@Box`; and method-value-on-generic-
  CALL-RESULT (`mkbox[int](v).Get`, mirror `168`).  Keep the EMPTY/never-populated destroy
  variant for the named-wrapper (that hid `c14dd95e`).
- **Mode scope**: green under the six default modes + native x64/aa64 (via modeset `all`); do
  NOT expect green under `builder-comp_native_arm32_baremetal` (incomplete P4 backend) — xfail
  those with a "native-arm32 backend incomplete" note, not a generics finding.
- **Second wave (defer, after a minimal cell of each compiles)**: method-expression,
  parameterized-receiver-impl dispatch (`impl *Cursor[T] : Iterator[T]`), generic-constraint
  dispatch.  Adopt, don't wire (no hygiene/CI `--check` gating without a separate decision).
