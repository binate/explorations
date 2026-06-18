# Plan: Binate Language Specification — structure and authoring plan

Status: **proposal under review** (2026-06-08). This is the high-level
*structure* and phased authoring plan for a formal Binate language
specification — not the spec itself. It realizes the existing
`claude-todo.md` entries **"Language spec(s) — write the primary spec"**
and **"pkg/rt review"**.

It is grounded in a survey of the `explorations/` design docs
(`claude-notes.md`, `claude-discussion-detailed-notes.md`,
`grammar.ebnf`, the `plan-*.md` / `design-*.md` cluster,
`ir-backend-guidelines.md`) **and** the live open-defect ledger
(`claude-todo.md`). Load-bearing claims were verified firsthand against
the source files (and, for the string-literal default type, against the
compiler's own type checker).

We do **not** propose authoring the whole spec at once. The point is to
fix the shape, conventions, and order, so chapters can be written and
landed incrementally while the language still changes.

## Decisions to date (2026-06-08)

- **D1 — string-literal default type: RESOLVED → `@[]readonly char`.**
  Verified against the implementation: `pkg/binate/types`
  `defaultStringLitType()` returns `@[]readonly char` (managed-slice);
  natural type is `[N]readonly char`; allowed targets are
  `@[]readonly char` / `*[]readonly char` / `@[]char`; `*[]char` is
  rejected. `claude-notes.md` line 435 (which said `*[]readonly char`)
  was stale and has been reconciled.
- **D2 — in-process dual-mode interop: a stated GOAL, not yet realized.**
  Seamless same-process embedding (one shared heap; thunked
  compiled→interpreted; mixed-mode vtables; hot-swapping) is a high-level
  goal. The `bni` binary is a *partial* realization but is not ready, and
  the embedding APIs are not ready. §19 describes the execution model and
  the dual-mode *contract* (the function-pointer mechanism, identical
  layout) as design-of-record, and frames full in-process embedding as a
  goal/future — not as realized. The **interpreter embedding API is a
  separate spec**, out of scope here.
- **D3 — scope: the CORE LANGUAGE spec, including tier-0 packages.** See
  §1. The stdlib (tier 1) is a separate, younger *sibling* spec dependent
  on this one — we only **reserve space** for it now. Package manager,
  toolchain, and interpreter embedding API are each separate. `pkg/bootstrap`
  is temporary and is **not** part of the language.

- **D4 — grammar source: a canonical `.ebnf` in the spec repo, generated
  from.** The spec's grammar becomes canonical: a `.ebnf` lives in
  `docs/spec/`, and the Markdown grammar annex + the per-section inline
  productions are **generated** from it. `explorations/grammar.ebnf` is
  reconciled (Phase 0), used to seed the canonical copy, then **retired**
  (it is already stale; the CLAUDE.md pointer is updated when it goes).
- **D5 — named-distinct transparency: ADOPT Go's model (RATIFIED
  2026-06-11; supersedes the original v1-RESTRICTIVE choice).** A
  named-distinct type (`type X <underlying>`) is transparent to its
  underlying type for operators, the built-ins `len` / `present` / `same`,
  indexing, slicing, and field access (read+write, incl. auto-deref when
  the underlying is a pointer — `type P *A` → `p.X`) — but never
  auto-inherits the underlying's *methods* (declare those on the distinct
  type itself; reach the underlying's methods via an explicit conversion).
  Assignability follows Go's rule: a value crosses the boundary without a
  cast iff identical underlying types AND ≥1 side is unnamed (so unnamed
  composite underlyings like `@[]int` assign freely; scalar/named
  underlyings and two-named-types need a `cast`). Comparison follows the
  underlying's comparability, with the Binate deviation that **slices are
  never comparable, not even to `nil`** — a named-distinct slice type is
  not comparable at all. The relaxation is forward-compatible (it only
  *accepts* more code), so adopting the target now breaks nothing. Full
  model: `claude-notes.md` "Type declarations — DECIDED"; verified
  empirically (go1.26.3).
  *Historical: v1 originally REJECTED field access / method dispatch
  through any named-distinct type as the safe forward-compatible default;
  §7.3 records that v1 rule alongside this — now adopted — target. (An
  even earlier "struct value yes / pointer no" framing in `claude-todo.md`
  was a mistaken reading of Go, since corrected.)*

- **D6 — opaque (forward-decl) syntax: bare `type Foo`.** Confirmed: the
  shipped bare `type Foo` form is correct (fully opaque); no `type Foo
  struct` variant (`type Foo struct` in discussion was a misspeak).

- **D7 — spec-test placement: `binate/conformance/spec/`.** Spec tests live
  in the `binate` repo under `conformance/spec/`, organized by chapter,
  reusing the existing conformance harness; the spec (`docs`) references
  rule-IDs only; a generated coverage report bridges into Annex C. See §10.

All decisions (D1–D7) resolved. Phase 0 scaffolded; authoring underway.

## Authoring progress (updated 2026-06-12)

The spec lives in the **`docs` repo**, `docs/spec/`. `docs/spec/00-index.md` is
the live ToC + per-chapter status; this section is a durable summary.

**Authored (each: ground via a Workflow → draft → adversarial-verify → correct →
commit):**
- Apparatus: `conventions.md`, `00-index.md`, `binate.ebnf` (canonical grammar;
  Annex A generated from it).
- §3 Terms, §4 Notation (Phase 0 / apparatus).
- §5 Lexical, §6 Constants, §7 Types (`07-types.md` catalogue + `07b-type-layout.md`
  keystone, verified clean), §8 Conversions, §9 Declarations & Scope (Phase 1).
- **Phase-1 adversarial review done** (cross-chapter + current-ground-truth);
  corrections applied.
- §10 Functions/Methods (`10-...md` + `10b-function-values.md`), §11 Interfaces/
  impl/Self (CRITICAL dispatch defects found RESOLVED), §12 Generics/Enumerations,
  §13 Expressions (Phase 2, §10–§13 of §10–§15).
- **Full adversarial review of §3–§13 done** (2026-06-12, docs `f7f1152`):
  13 reviewers re-read live `pkg/binate` source + cross-chapter consistency;
  ~30 findings (1 blocker, 7 major, the rest minor/nit) triaged, the load-bearing
  ones firsthand-verified, corrections applied across all 14 spec files +
  `conventions.md`. One reviewer finding (a "stale `present` todo") was a verified
  FALSE POSITIVE (the todo's DONE header already covers it). New ledger item:
  `expr.unary.addr-literal` (`&5` not diagnosed).
- §14 Statements (`14-statements.md` simple statements + `14b-control-flow.md`
  control flow), authored 2026-06-12 (docs `e7c6252`): grounded (5 readers) →
  drafted → adversarially verified → corrected. Two MAJOR silent-miscompiles
  surfaced + flagged open (see below).
- §15 Built-in Operations (`15-builtin-operations.md`), authored 2026-06-12 (docs
  `f01f8ce`): grounded (4 readers) → drafted → verified → corrected. **Phase 2
  complete.** One MAJOR dual-mode gap surfaced + flagged (panic VM no-op).
- §16 Packages and Program Structure (`16-packages-and-program-structure.md` core
  + `16b-build-constraints.md` annotations/build/FFI), authored 2026-06-12 (docs
  `21d4901`): grounded (4 readers) → drafted → verified → corrected. No NEW
  untracked defects (aliased-imports-broken, _Package VM gap, int-int multi-pkg
  crash all already tracked).
- §17 Program Initialization and Execution (`17-program-initialization-and-execution.md`),
  authored 2026-06-12 (docs `03a0bb6`): grounded (5 readers) → drafted → verified
  → corrected. **Phase 3 complete.** Consolidated the closed-panic catalogue;
  extended the panic entry (compiled also discards the message). (A
  `prog.main.unchecked` "defect" flagged here was later RETRACTED as BY DESIGN —
  entry resolution is link-time under per-package compilation + interop; docs
  `4af9c72`.)
- **Phase-3 adversarial review done** (2026-06-12, docs `ac7982a`): 4 reviewers
  re-read live source + cross-chapter. Key catch — aliased imports were FIXED
  (binate `52d1c832`) AFTER §16 was authored, so the "broken" flag was stale;
  removed it (badge + §16.3 + index). Also corrected: the file-level build gate
  runs AFTER parse (can't hide syntax); unknown-unqualified-annotation error only
  fires when a build config is resolved; `__c_call` void/struct returns
  unsupported; added an `iface.dispatch.nil` rule to §11.11 so §17.5's cross-ref
  resolves. All firsthand-verified.

- §18 Memory Model (`18-memory-model-reference-counting.md`), authored 2026-06-12
  (docs `7d95de2`): grounded (4 readers: rt contract / IR refcount discipline /
  design axioms / refcount matrix) → drafted → verified → corrected. The five
  axioms, lifecycle, acquire-before-release, ownership transfer, move-as-
  optimization, no-leak contract + cycle/raw-UAF user-error escape hatches; with
  operational rules. No new defects (built on already-tracked items).
- §19 Execution Model: Abstract Machine + Dual-Mode Interop
  (`19-execution-model-dual-mode.md`), authored 2026-06-12 (docs `ed8f954`):
  grounded (4 readers: VM / dual-mode interop / design+D2 / conformance modes) →
  drafted → verified → corrected. **Phase 4 complete.** The dual-mode contract
  (Stable) vs in-process embedding (GOAL per D2); abstract machine; function-value
  interop; enumerated divergences. Verify-the-verifier fix: nil-iface dispatch is
  a DEFINED panic (form mode-dependent), not UB — §19.5 corrected to match
  §11.11/§17.5.
- **Phase-4 adversarial review done** (2026-06-13, docs `44a1a4b`): 3 reviewers
  re-read live source + cross-chapter; the nil-iface defined-not-UB consistency
  across §19.5/§11.11/§17.5 confirmed clean. Two majors fixed: §18.5 mem.return's
  "fresh = move, no acquire" was wrong for @T/@[]T (gen_return.bn:145-154 RefIncs
  unconditionally — only @func/@Iface move); §19.5 understated the panic defect
  (compiled ALSO discards the message, not just the VM no-op) and carried a STALE
  "cross-mode call dispatch" limit (trampolines + float-closure shim landed,
  binate `085065d9`). Plus minor/nit polish. All firsthand-verified.

- §20 Intrinsic (Tier-0) Packages (`20-intrinsic-tier0-packages.md`) + §21
  Behavior catalogue (`21-implementation-defined-and-undefined-behavior.md`),
  authored 2026-06-13 (docs `889d359`): grounded (5 readers: lang / reflect /
  testing / behavior-catalogue / design-intent) → drafted → adversarially verified
  (3 reviewers) → corrected. §20 covers lang/reflect/testing; **§20.2 rt left a
  GATED placeholder** per scope. §21 is the consolidated Annex-J-style index with
  back-references. Verify-the-verifier corrections: §21's array/struct over-count
  defect was STALE (both now rejected, binate `910e08cb` / `e185c9c4`) — narrowed
  §21.9 + the §13.10 home; the "optional int64/float types" impl-defined row had
  **no authored home** (§7.2 lists them unconditionally) → reframed as a Draft
  reconciliation gap; tagless-switch cited §14.8 (corrected to §14.10
  `stmt.switch.tagless-bool`); §7.13 subsection anchors tightened; float-`Compare`
  total-order date misattribution dropped. Consistency fix: §8.5 float→int
  saturation has LANDED (binate `b3a52025`, test 732 green all modes), so its "not
  yet realized" Open note became a settled rule. **§20/§21 chapter authoring done.**

- Canonical grammar + Annex A (docs `335cc9f`/`e8a04da`/`052f414`/`a1f20b9`/`d242c95`),
  2026-06-13…15: reconciled `explorations/grammar.ebnf` against the parser + the
  inlined chapter productions into the canonical `docs/spec/binate.ebnf` (Phase-0,
  Decision D4) → adversarially reviewed (4 reviewers) → corrected (added slice
  composite literals, removed the left-recursive BuildExpr / `\u` escape, fixed
  string-concat + annotation granularity + ForInClause cap, restored the
  generic-literal head per user decision, …) → reconciled the recent shift /
  parallel-assignment landings (guard-free `unsafe_shl`/`unsafe_shr`; negative
  shift count now panics; parallel `a,b=b,a`) into §13/§14/§15/§17/§21 + the
  grammar. **Annex A is now GENERATED** from `binate.ebnf` by
  `docs/scripts/gen-annex-a.py` (with a `--check` staleness mode). Retracted
  `prog.main.unchecked` as BY DESIGN (per-package compilation + interop). Three
  parser bugs filed (const-`X T`, generic-literal-unparsed, for-clause chaining).

- Ch.1–2 + reconciliation pass (docs `9a0e2b9`/`bffea71`/`51af44c`), 2026-06-17:
  reconciled recent landings into the spec — `&` addressability tightened (§13.8;
  landed `7f8d0b9c`), opaque-builtin gate landed (§7.12/§15.2; resolved in §21.9;
  `fe9e131e`/`ffc56b36`), no-type-redeclaration across `.bni`/`.bn` (§7.12;
  `8f5cc319`), and **endianness ratified implementation-defined** (§7.13.12/§21.4/
  §3 terms; current little-endian, a `TargetInfo` endianness field is a tracked
  impl follow-up). Ch.5 cleanup (decimal_lit / int_literal / float_literal names
  matched to `binate.ebnf`; leading-zero numeral is a settled lexical error).
  **Ch.1 (Scope) + Ch.2 (Conformance) authored** — all chapters 1–21 now done.
  Held: the cast-does-not-launder-constants §8 edit (DECIDED 2026-06-17 but its
  enforcement is in a dev worktree, NOT on main) — pending the user's
  apply-now-vs-wait call.

**Remaining:** **All chapters 1–21 authored; Annex A generated.** Remaining:
**§20.2** (gated on the `pkg/rt` review) and **Annexes B–D** (B impl-model/IDB
index; C status table — derive last; D rationale). Open user decisions: the
**optional int64/float availability** reconciliation gap
(`behavior.impl-defined.optional-scalars`, §7.2/§21.4), and the **cast §8**
apply-now-vs-wait (held, see above). Prerequisite still pending: the `pkg/rt`
review (→ §20.2). The grammar reconciliation and the endianness decision are
**done**.

**Spec-as-audit:** authoring has surfaced ~21 real implementation discrepancies/
defects, all tracked in `claude-todo.md` (search "spec Ch."). Notable MAJOR:
parallel assignment `a,b=1,2` / swap `a,b=b,a` was a silent dropped-write but is
now **RESOLVED** (decision (A) Support, binate `d2a3b8f1`); inc/dec on a
non-identifier lvalue (`a[i]++`, `p.f++`) still type-checks clean but emits NO
code (Ch.14, MAJOR — open);
`panic(msg)` is a no-op in the bytecode VM (Ch.15, MAJOR dual-mode gap);
indexed array literals silently miscompiled (Ch.13; the array/struct over-count
OOB writes are now RESOLVED — `910e08cb` / `e185c9c4`); generic methods/struct-
constraints unenforced (Ch.12); the const→readonly and grammar-staleness
reconciliations.

**NEXT (updated 2026-06-17):** **all chapters 1–21 are authored**, Annex A is
generated from the canonical `binate.ebnf`, and the endianness decision is
ratified. Remaining authoring: **§20.2 rt** (gated on the `pkg/rt` review) and
**Annexes B–D** (B impl-model/IDB index; C status table — derive last; D
rationale). Open follow-ups for the user, not blocking: (1) the **cast §8** edit
is held (DECIDED but enforcement not yet on main) — apply-now-vs-wait; (2) the
**optional int64/float availability** reconciliation gap needs ratification; (3)
the remaining Ch.14/Ch.15 MAJOR gaps (inc/dec-lvalue drop, panic VM no-op) need a
fix decision + a coordinated `binate` worktree; (4) no xfail conformance coverage
yet for the two MAJOR generics gaps (`gen.no-generic-methods.unenforced`,
`gen.satisfy.struct-iface-unchecked`).

---

## 1. Which spec is this? (the multi-spec map)

Binate is deliberately **less monolithic** than most languages: the
standard library is *not* a core part of the language, and the language
can be used without it. Unlike C, there is no `printf`-equivalent in the
core language. So Binate is specified as **several documents**, and this
plan covers the **core language spec**:

- **Core language spec** (this plan): syntax, type system, semantics, and
  the **tier-0 intrinsic packages** — the packages bound to the language
  itself. These are part of the language, at varying maturity:
  - `pkg/builtins/lang` — canonical interfaces + primitive impls.
    *Fairly mature.*
  - `pkg/builtins/rt` — the runtime contract. *Immature; needs the
    pkg/rt review (a stated prerequisite).*
  - `pkg/builtins/reflect` — reflection/introspection surface.
    *Incomplete.*
  - `pkg/builtins/testing` — testing support + the `*_test.bn`
    convention. *Somewhat immature; needs refinement.*
  Because tier 0 is not fully mature, several of its sections will be
  Draft/Provisional (§4) — specified-in-intent, marked honestly.
- **Standard library spec** (tier 1) — a separate, *younger sibling*
  spec that **depends on** the core language spec. Still in early design;
  **not written now** — the core spec only reserves a pointer to it.
- **Out of scope, each its own thing:** the **package manager**, the
  **toolchain**, and the **interpreter embedding API**.
- **Not part of the language:** `pkg/bootstrap` (temporary scaffolding).

**Why this split.** Binate targets environments with no console,
filesystem, process model, or threads. A core spec free of stdlib/I/O
assumptions stays implementable on bare-metal targets; the stdlib spec
layers selectively per target. The spec therefore defines **hosted vs
freestanding** conformance.

---

## 2. Where it lives, and in what shape

- **Repo:** the new `docs` repo (`github.com/binate/docs`) — the
  published artifact. The design notes (`claude-notes.md`, `plan-*.md`,
  `grammar.ebnf`) stay in `explorations/` as the rationale/working tree
  that Annex D draws on.
- **Layout:** `docs/spec/`, **one Markdown file per chapter + per
  annex**, numeric filename prefixes fixing reading order, an index file,
  and a shared `conventions.md`. Not a single monolithic `spec.md` (a
  huge file invites cross-worker clobbering, and the per-chapter split
  maps one-to-one onto the phased authoring plan).
- **Grammar:** the **canonical grammar is a `.ebnf` in `docs/spec/`** (D4);
  the Markdown grammar annex (Annex A) and the per-section inline
  productions are **generated** from it. `explorations/grammar.ebnf` is
  reconciled in Phase 0, used to seed the canonical copy, then retired.
- **Cross-references use stable rule/anchor IDs** (e.g.
  `mem.ownership.transfer`, `type.slice.layout`, `exec.dualmode.thunk`),
  not section/page numbers, so references and conformance-test citations
  survive renumbering and the file split.

Proposed files:

```
docs/spec/00-index.md            ToC, status legend, reading-order map (links by stable ID)
docs/spec/conventions.md         status legend, requirement vocabulary, per-construct rubric, rule-ID scheme
docs/spec/binate.ebnf            canonical grammar (source of truth; annex-a + inline productions generated from it)
docs/spec/01-scope-introduction.md
docs/spec/02-conformance.md
docs/spec/03-terms-and-definitions.md
docs/spec/04-notation.md
docs/spec/05-lexical-elements.md
docs/spec/06-constants.md
docs/spec/07-types.md            (split 07a-catalogue / 07b-layout if over the hygiene cap)
docs/spec/08-conversions.md
docs/spec/09-declarations-and-scope.md
docs/spec/10-functions-methods-function-values.md
docs/spec/11-interfaces-impl-self.md
docs/spec/12-generics-and-enumerations.md
docs/spec/13-expressions.md
docs/spec/14-statements.md
docs/spec/15-builtin-operations.md
docs/spec/16-packages-and-program-structure.md
docs/spec/17-program-initialization-and-execution.md
docs/spec/18-memory-model-reference-counting.md
docs/spec/19-execution-model-dual-mode.md
docs/spec/20-intrinsic-tier0-packages.md
docs/spec/21-implementation-defined-and-undefined-behavior.md
docs/spec/annex-a-grammar-summary.md       (lockstep with grammar.ebnf)
docs/spec/annex-b-implementation-model-and-idb-index.md
docs/spec/annex-c-stability-status-table.md
docs/spec/annex-d-rationale-and-design-notes.md
```

---

## 3. Organizing principles

- **Spine: Go-style, bottom-up along the dependency DAG.** Lexical →
  constants → types → conversions → declarations → functions →
  interfaces → generics → expressions → statements → builtins → packages
  → program execution → memory model → execution/dual-mode → intrinsic
  packages → behavior catalogue. Reading order minimizes forward
  references; the load-bearing cross-cutting chapters (memory model,
  dual-mode) come **late**, after every term they need is defined.
- **Rigor grafts from ISO/ECMA style.** A first-class **Conformance**
  clause (elevating the dual-mode agreement contract); explicit Scope /
  Terms / Notation up front; a collected **implementation-defined /
  unspecified / undefined** catalogue with a reverse index (C Annex-J
  pattern); strict normative/informative separation.
- **Per-construct rubric** (every feature section): **Grammar** (inlined
  EBNF) → **Constraints** (diagnosable static rules — maps onto Binate's
  "compiler checks upfront / interpreter defers" split) → **Static
  semantics** → **Dynamic semantics** (incl. refcount/ownership effects
  and any compiled-vs-interpreted divergence) → **Exceptions** (error
  conditions / UB) → **Notes/Examples** (informative).
- **Normative by default; rationale is quarantined** to Annex D and Note
  blocks.
- **Two hardest concerns get dual presentation** (prose + a formal
  operational rule, with a statement of which is authoritative): the
  refcount memory model (§18) and dual-mode dispatch (§19).

---

## 4. Status model — TWO orthogonal axes (important)

The language is under active development, so status is load-bearing and
honest. Two independent axes, plus the normative/informative axis:

### 4a. Language-design stability (per section/rule)

Four values, orthogonal to normative/informative (a Draft rule is still
normative-*in-intent*):

- **Stable** — semantics fixed; changes are breaking and rare.
- **Provisional** — specified and implemented but may still change.
- **Draft** — specified but partially/not implemented; normative-in-intent.
- **Reserved** — syntax/feature reserved, semantics not yet defined.

### 4b. Implementation-conformance status

Separately, the spec tracks **whether the current toolchain actually
conforms to a Stable rule**, sourced from `claude-todo.md` (the
CRITICAL/MAJOR open-defect ledger, mandated by CLAUDE.md's Bug Discovery
Protocol) and surfaced in **Annex C** (status table) and **Annex B**
(implementation model).

The distinction matters (and the first survey pass got it wrong by
omitting `claude-todo.md`): **a known miscompile does not make the
language rule unstable — it makes the implementation non-conformant.**
The rule "interface methods may return multiple values; errors are
`(T, @Error)`" is *Stable language design*; that current backends cannot
yet dispatch it (a CONFIRMED CRITICAL defect) is an *implementation*
gap. The spec states the rule normatively and Annex C records the
non-conformance with a claude-todo cross-ref.

### 4c. Do NOT reuse the grammar's `[BOOTSTRAP]`/`[DEFERRED]` tags

Those tracked the *retired Go-interpreter subset* (a tool that no longer
exists), not language stability. They must be **stripped** from the
normative grammar (Phase 0).

Mechanics: stable rule/anchor IDs from day one; a visible `[Status]`
badge per chapter/section; the standalone Annex C ledger re-derived from
per-section tags **and** the defect ledger.

---

## 5. Proposed table of contents

21 chapters + 4 annexes. Each line: scope · primary sources · status
caveat.

**1. Scope and Introduction** *(informative)* — What Binate is; design
goals and enumerated non-goals (no GC, no ownership/borrowing, no
exceptions, no maps, no `string` type, no `append`, no goto/init/defer;
**no `printf`/stdlib in the core**); the less-monolithic philosophy
(language usable without the stdlib); the core-spec-includes-tier-0
boundary, with the stdlib reserved as a dependent sibling spec and the
package-manager/toolchain/embedding-API out of scope; `pkg/bootstrap` is
temporary, not part of the language. · *Finalize last.*

**2. Conformance** *(normative)* — Conforming program; **compiler and
interpreter as co-equal implementations**; the **cross-mode agreement
requirement** (where both modes coexist they shall agree exactly on
observable layout/behavior on a target; implementation-defined choices —
notably word size — shall agree); hosted vs freestanding. · *§2.4 states
the dual-mode contract; full in-process embedding is a goal, not asserted
as realized (D2).*

**3. Terms and Definitions** *(normative)* — Binding glossary: the
behavior-latitude taxonomy (target-invariant / target-parameterized /
implementation-defined / unspecified / undefined / backend-private) and
the core vocabulary (managed/raw pointer, managed-slice, refcount,
ownership transfer, move, destructor, vtable, impl, interface value,
function value, monomorphization, Self, thunk, TargetInfo, readonly). ·
*`readonly` = the type modifier; `const` = compile-time constant;
`managed-slice` hyphenated.*

**4. Notation** *(normative)* — The ISO-14977-flavored EBNF metalanguage
(already in `grammar.ebnf`); the per-construct rubric; the
normative/informative discipline; the four-value status legend; the
stable rule-ID scheme; a light operational-rule notation for the
trickiest dynamic semantics. · *Records that the canonical grammar moves
into the spec as a `.ebnf` (D4) and must drop `[BOOTSTRAP]`/`[DEFERRED]`
in the Phase-0 reconciliation.*

**5. Lexical Elements** *(normative)* — Source representation (ASCII);
identifiers; reserved keywords (incl. `readonly`) and builtin-operation
keywords (`make`, `make_slice`, `box`, `cast`, `bit_cast`, `len`,
`unsafe_index`, `sizeof`, `alignof`, `same`, `present`, `unsafe_div`,
`unsafe_rem`); predeclared shadowable names (`int`, sized ints, `bool`,
`byte`/`char`=`uint8`, `any`, `float32/64`, `iota`); literals + escapes
(no implicit null terminator); adjacent string-literal concatenation;
comments; automatic semicolon insertion. · *Stable core; `readonly`
keyword RECENT, grammar not yet updated.*

**6. Constants** *(normative)* — Untyped literals and default types
(literal-only coercion, unlike Go's named constants); integer-constant
value range and union-range constant arithmetic (intermediate overflow
rejected; no wrap, no bignum); untyped-float class + strict
no-implicit-int↔float; literal overflow is a compile error.
**String-literal natural type `[N]readonly char`, default type
`@[]readonly char`** (D1, verified). · *Resolved.*

**7. Types** *(mixed)* — The type catalogue (value vs reference;
target-parameterized scalars; named distinct types vs aliases;
anonymous-struct structural equivalence; structs; arrays; raw slices
`*[]T` (2-word) vs managed-slices `@[]T` (**4-word**); the
length-0⟹no-backing invariant; managed `@T` / raw `*T` pointers and
nullability; `*func`/`@func`; interface value types; the `readonly`
modifier + its assignability lattice; forward-declared opaque types),
then **§7.13 Type Layout & Representation** — the single normative home
for the cross-mode ABI contract, parameterized by `TargetInfo`. ·
*Catalogue largely Stable. Opaque (forward-declared) types are
**shipped/Stable** (verified: `conformance/512_opaque_handle_cross_pkg`
passes, no xfail): a bare `type Foo` in a `.bni` with the body in a `.bn`
exports `Foo` opaquely — callers hold only `*Foo`/`@Foo`, and field access
/ `make` / `sizeof` outside the package are rejected (encapsulation). The
field-access invariant **unifies §7.3 and §7.12**: access is gated on the
underlying being **visible/concrete**. An opaque type has a nil underlying
→ field access permanently rejected; a named-distinct type over a
*visible* underlying is the D5 case (NOW ADOPTED 2026-06-11 — allow,
peeling only a concrete underlying, so the relaxation never opens opaque
field access). Rationale: a named type gets a fresh method/impl set (→ structure/
fields shared, methods/impls not) — Annex D. Provisional: interface-value
byte layout. Length-0-no-backing: rule + enforcement **landed** (binate
`71ff7489`, conformance 666); an open `builder-comp-int` regression (test
110) remains. **Impl-conformance:** nested arrays mis-compiled
(claude-todo MAJOR). Opaque-decl syntax is bare `type Foo`
(D6; fully opaque — hides even struct-ness; no `type Foo struct` variant).
D5: §7.3 states the now-adopted (2026-06-11) Go-faithful transparency
rule — peeling a concrete underlying for ops / len / index / slice /
field access; opaque (nil-underlying) types stay rejected.*

**8. Conversions** *(normative)* — The closed set of implicit conversions
(untyped-literal coercion, readonly-adding widenings, the managed→raw
borrow) + explicit `cast`/`bit_cast`. No implicit numeric/named
conversions. · *The implicit `@T`→`*T` borrow is the one Provisional
conversion (currently permissive everywhere; a proposal would restrict it
to borrowing positions).*

**9. Declarations and Scope** *(normative)* — `const` (compile-time,
scalar-only, no storage) vs `var` (storage; `.bni` extern form) vs the
`readonly` modifier; `iota` in grouped const blocks; `:=` and the D1
disambiguation; block scope and shadowing; permitted package-scope
declarations (function-local `type` is a parse error); init order (no
`init()`). · *Stable; grammar still spells the modifier `const`.*

**10. Functions, Methods, and Function Values** *(mixed)* — Function
declarations; single/multiple returns (position lists, not tuples) and
destructuring; variadics + spread; methods, the five receiver kinds,
receiver smoothing (safe direction only), object- vs handle-readonly
dispatch, one-method-per-name, one-level auto-deref; function VALUES
(`*func`/`@func`, 2-word repr; closures capture by value; escape via lint
not type error; method expressions/values); indirect calls as the
mode-transparent unification mechanism (xref §19). · *Declaration surface
Stable; function VALUES RECENT — Provisional. **Impl-conformance:**
destructuring a multi-return `@func()` call rejected at type-check
(claude-todo MAJOR). Recursive anonymous closures Reserved.*

**11. Interfaces, impl, and Self** *(mixed)* — Interface declarations
(named, top-level; bare name is not a type); `*Iface`/`@Iface` values and
pointers to them; `impl` (relational, separate, nominal explicit
satisfaction — no duck typing); construction-site explicit conversions +
boxing; `any`/`*any`/`@any`; extension/embedding + transitive impl with
static upcast; the `Self` type and object-safety; interface aliases;
cross-package interfaces (no orphan rule); the primitive-impl carve-out
(the canonical interfaces `Compare`/`String`/`Hash` are *defined* in
§20.1, `pkg/builtins/lang`); vtable dispatch (observable semantics
normative, layout informative → Annex B). · *Language design
overwhelmingly Stable/shipped. **Impl-conformance (per-subsection
overrides required):** interface-method MULTI-RETURN dispatch is
CRITICAL-broken (can't dispatch `(T, @Error)` — blocks pkg/std/io);
transitively re-exported interface → SIGSEGV (CRITICAL, memory-unsafe);
sub-word multi-return mis-unpacked on VM/native (claude-todo 2026-06-08).*

**12. Generics and Enumerations** *(normative)* — Type parameters on
functions/structs/interfaces (each constraint a single named interface;
no `+`; `[T any]`); no generic methods; no conditional impls (v1);
monomorphization (no type inference; explicit args; constraint calls
lower to direct calls); cross-package generic bodies in `.bni`
(source-text); no first-class enums (named-int + `const(...)`+`iota`;
tagged unions are a separate future feature). · *Complete 2026-05-21,
Stable. v1 restrictions are deliberate scope. Generic-path `==` is gated by
the §13 aggregate-`==` rejection (recheck the generic-instantiation path).*

**13. Expressions** *(normative)* — Operands, primaries, operators; **the
grammar's 11-level Go-style precedence is authoritative** over the
conflicting claude-notes prose; defined arithmetic (truncate-toward-zero
division, `%` sign-of-dividend, two's-complement wrap, defined shifts,
div/mod-by-zero and signed-MIN/-1 as defined panics — no UB);
bitwise/shift; comparison (no chaining; pointer address-equality;
IEEE-754 incl. unordered-`!=`); short-circuit logical (bool-only);
selectors (`.` only, auto-deref, no `->`); index/slice + bounds checks;
composite literals; D1–D11; no operator overloading. · *Core Stable.
`==`/`!=` on aggregates (struct/array) AND interface values is **disallowed
by design** — a defined Constraint; rejection **landed** (binate
`60719e01`/`78af9c23`). Equality for such types goes through explicit
methods (`Compare`/`Equatable`), `present()`/`same`, or `errors.Is`;
sentinel identity (e.g. `err == io.EOF`) is `io.IsEOF`/`errors.Is`/`same`,
not `==`. **Flag** the MAJOR compiler-DoS: cyclic non-struct named types
hang the comparability checker.*

**14. Statements** *(normative)* — Block-local decls; simple statements;
assignment (multi-target destructuring; compound); inc/dec
(postfix-only); `if`; `for` (four forms incl. range, for-in
value-vs-index differing from Go); `switch` (no fallthrough);
`return`/`break`/`continue` (no labels/goto). · *Stable. Note deliberate
omissions: no if/for/switch init clause, no labeled break/continue, no
goto, no fallthrough.*

**15. Built-in Operations** *(normative)* — The keyword builtins
(allocation `make`/`make_slice`/`box`; conversions `cast`/`bit_cast`
→ §8; size/layout `sizeof`/`alignof`/`len`; unchecked
`unsafe_index`/`unsafe_div`/`unsafe_rem`; identity/presence
`same`/`present`; volatile-access builtins; managed-representation
introspection). · *Most Stable. `present()` is **extended** to func values
(vtable field 0), pointers (non-null), and slices (`len > 0`); value types
rejected — DONE (binate `29c9dc47`, conformance 667). `move`/`ispod` are
PROPOSED → Annex D.*

**16. Packages and Program Structure** *(mixed)* — `package "path"`;
`.bn` + at-most-one `.bni`; sibling-directory layout; imports;
**structural visibility** (exported iff in `.bni`); `.bni` contents; the
`main` package; the two `-I`/`-L` search paths + `--root`; symbol
mangling (informative ABI → Annex B); the `*_test.bn` reservation (the
testing *package surface* is §20.4). · *Core Stable. Tier layout largely
aspirational → informative. GAP: import cycles — spec must add a rule.*

**17. Program Initialization and Execution** *(normative)* — Retained vs
immediate evaluation (a non-REPL run is fully validated before execution
via an external entry call); no forward declarations required; package
init order (no `init()`); `main` entry + termination (host-dependent
argv); the annotation system (`#[...]` syntax/attachment, namespacing,
type-identity effects); errors-as-values (no exceptions/panic-recover/
defer; any type may be an error); the closed set of defined
non-recoverable runtime panics (bounds, divide-by-zero, MIN/-1). ·
*Stable. The normative list of standard annotation names/arg schemas must
be supplied. Proposed annotation features → Annex D.*

**18. Memory Model: Reference Counting and Object Lifetime** *(mixed)* —
The load-bearing dynamic-semantics chapter. Reference counting (no GC, no
ownership/borrowing; cycles leak = programmer error); the refcount axioms
(live⟹rc>0; rc=0 runs the destructor before free; copy invokes the copy
constructor/RefInc; assignment is copy-then-destroy); managed-allocation
header + destructor-vs-free-function separation; recursive deterministic
drop (statically resolved, no per-object RTTI); ownership transfer
(callee-side param RefInc; return carries one transferred ref; raw params
borrow); statement-level temporary lifetime (temp-borrow UAF is *user
error* — the compiler shall not suppress RefDec); managed-slice lifetime +
subslicing + length-0 tie-in; static-managed immortal (sentinel)
allocations; threading/atomicity stance (single-threaded default,
non-atomic v1). · *Axioms + ownership transfer + temporaries Stable.
Provisional: length-0 enforcement. Draft: static-managed sentinel
(in-flight, value unfinalized). Proposed (out of normative core):
`move`-as-guarantee (recommendation: optimization only), the `move`
builtin, debug lifecycle hooks. Mechanism detail → Annex B.*

**19. Execution Model: the Abstract Machine and Dual-Mode Interop**
*(mixed)* — The abstract machine (shared heap + refcount metadata + a
function-pointer call primitive both modes satisfy identically); function
pointers as the unification mechanism (compiled = native address; direct
call; interpreted = thunk; caller mode-oblivious; one-indirection cost
only at the boundary); cross-mode prerequisites (one heap/refcounting/
type-system; identical layout per §7.13; no marshalling; `.bni` signature
discovery + symbol resolution); the runtime function manifest (specified
concretely in §20.2, `pkg/builtins/rt`); multi-return value
representation; enumerated intentional cross-mode divergence points. ·
***D2 framing:*** the thunk-unification mechanism and identical-layout
prerequisite are the Stable design-of-record, and the two engines pass
conformance individually; **seamless same-process in-process embedding
(shared heap in one running binary, thunked compiled→interpreted,
mixed-mode vtables, hot-swapping) is a stated GOAL, not yet realized**
(`bni` is a partial step). The **embedding API is a separate spec**, out
of scope. Write the dual-mode *contract* without asserting a shipping
in-process embedded interpreter.

**20. Intrinsic (Tier-0) Packages** *(mixed)* — The normative `.bni`
surfaces of the packages bound to the language (D3). Each carries its own
maturity status; these are part of the language but several are immature.
· *src:* claude-notes §"primary spec is minimal", plan-stdlib-bundle /
pkg-layout, plan-primitives-impl-interfaces, plan-std-errors,
notes-package-introspection, claude-notes testing-convention.
  - **§20.1 `pkg/builtins/lang`** — the canonical interfaces
    (`Compare`/`String`/`Hash`) and their signatures (the normative home,
    referenced by §11.10), plus the primitive-impl surface. *Fairly
    mature → mostly Stable.*
  - **§20.2 `pkg/builtins/rt`** — the runtime contract: the minimal set
    of runtime primitives the language requires (alloc/free, RefInc/
    RefDec, box, bounds-check, etc.), split hosted vs freestanding. *Draft
    — gated on the `pkg/rt` review (classify each member stay / move-to-
    stdlib / make-internal); the manifest is actively shrinking.*
  - **§20.3 `pkg/builtins/reflect`** — the reflection/introspection
    surface (auto-generated package reflection info, function-value lists
    for exported functions). *Draft/incomplete — "design notes, not a
    plan."*
  - **§20.4 `pkg/builtins/testing`** — the testing-support surface
    (`TestResult` etc.) and how `*_test.bn` files package (the name
    reservation itself is §16.9). *Provisional — somewhat immature, needs
    refinement.*

**21. Implementation-defined, Unspecified, and Undefined Behavior**
*(normative)* — The single collected catalogue with back-references.
**Implementation-defined** (must be documented; compiled and interpreted
modes must AGREE on a target): pointer/int/word size (32-bit primary,
64-bit supported), alignment, struct padding/offsets, the concrete byte
layout of the 2-word raw slice / **4-word** managed-slice / 2-word
interface-value & function-value / managed-pointer header, byte
order/endianness (**a GAP — the spec MUST decide; almost certainly
implementation-defined**), availability of int64/float types, the
sentinel refcount value, panic message/exit-code, symbol decoration —
stating target-INVARIANT structure (a managed-slice is *exactly* 4 words)
while parameterizing absolute sizes by `TargetInfo`. **Unspecified:**
evaluation order where unpinned, padding contents, inline-vs-runtime-call,
shared-static-literal storage. **Undefined** (the raw-pointer/refcount
escape hatch, fenced as *user error*, not a promised trap): UAF via a
borrowed raw slice/pointer outliving its backing, dangling `*T` deref,
breaking refcount invariants through raw aliasing, `bit_cast`/`unsafe_*`
out of contract, mode-dependence beyond the one-indirection cost.
**Explicitly WELL-DEFINED (not implementation-defined), closing what would
otherwise be a hardware-divergence gap:** `cast(<int>, <float>)` for an
out-of-range / `±Inf` / `NaN` value **saturates** to the target type's
`[MIN, MAX]` (`NaN` → 0; in-range truncates toward zero) — identical across
every backend and the VM (ratified 2026-06-12; refines Go's
"implementation-specific" by pinning a defined value).

**Annex A. Grammar Summary** *(normative)* — The complete EBNF,
**generated from the canonical `docs/spec/binate.ebnf`** (D4), metalanguage
per §4, productions also inlined per feature chapter; D1–D11 as a
consolidated table. · *BLOCKED on the Phase-0 reconciliation that produces
the canonical .ebnf; until then prose clauses govern.*

**Annex B. Implementation Model and Impl-defined Index** *(mixed)* —
*(Informative)* the runtime/ABI contracts observable in consequence but
not mechanism: vtable layout, value-receiver thunks, weak_odr dedup,
destructor/handle-dispatch mechanics, name mangling + object-format
symbol decoration, the IR/backend split, and the
observable-ABI-vs-backend-private boundary. *(Normative index)* the
Annex-J-style reverse index from each impl-defined/unspecified/undefined
point to its defining section + a target-word-size-dependent-points
table. · *Flag the OPEN mangler class (struct types not carrying
fully-qualified names; genMethodValue cross-package value receiver) — the
specific `reflect.Package` collision was FIXED 2026-06-08; the class +
the proposed dedup-mismatch hard-error guard remain.*

**Annex C. Stability Status Table** *(informative)* — The standalone,
auditable maturity ledger: every chapter/section with its
language-stability marker **and** its implementation-conformance status,
the latter sourced from `claude-todo.md` (CRITICAL + MAJOR) mapped to
sections. The single most-valued fidelity artifact. · *Finalize last.*

**Annex D. Rationale and Design Notes** *(informative)* — Why refcounting
over GC and over ownership/borrowing; why two pointer kinds and two slice
kinds; why a named-distinct type shares the underlying's *fields* but not
its *methods/impls* (each named type gets a fresh method/impl set — the
basis for the D5 rule, and why opaque forward-decls hide fields entirely);
the no-implicit-cost philosophy; the minimal-core-spec /
less-monolithic rationale (no stdlib/printf in core; stdlib usable but
not required); the v1-without-foreclosing-v2 deferrals (non-nullable
pointers, tagged unions, atomic refcounts, move-as-guarantee); the
extended Go comparison and prior art; the C-free-target posture and
FFI-as-future-escape-hatch. The home for every PROPOSED/deferred item.

---

## 6. Conventions (summary)

- **Terminology pins** (defined once in §3): `readonly` is the type
  modifier; `const` is *only* the compile-time-constant declaration;
  legacy `const T` / `[N]const char` are superseded. `managed-slice`
  (hyphenated) = `@[]T`, **4 words** `{data,len,backing,backingLen}` (the
  "3-word" descriptions are stale). Canonical interface methods are
  `Compare`/`String`/`Hash` (`toString`/`less`/`hash` superseded).
  **Stale-doc warning:** `claude-discussion-detailed-notes.md` §6/§7/§19
  predate the interface-syntax revision and write `const` for `readonly`;
  `claude-notes.md` + `plan-*.md` are authoritative on conflict.
- **Grammar notation:** keep the ISO-14977-flavored EBNF; `…` = inclusive
  character range, double-quotes = literal terminals, juxtaposition =
  concatenation.
- **Cross-referencing:** stable IDs; exactly one normative home per rule
  (§7.13 owns layout; §8.4 owns the managed→raw borrow; §18.7 owns
  temporary lifetime; §20.1 owns the canonical-interface signatures).
- **Impl-defined taxonomy:** the C three-way model, with the extra axis
  the runtime cluster needs — *target-parameterized-but-fixed-per-target,
  all modes must agree* vs *truly private*. The cross-mode agreement rule
  (§2.4 + §21) is the master ABI invariant.

---

## 7. Phased authoring plan

Write stable, foundational, low-churn material first; the
highest-design-risk cross-cutting and least-mature material last.

- **Phase 0 — Apparatus + prerequisites (before normative writing).**
  §4 Notation (metalanguage, rubric, status legend, rule-ID scheme); seed
  §3 Terms; skeletons of §1/§2. **In parallel, two verified-necessary
  prerequisites:** (a) the grammar **reconciliation + move into the spec
  (D4)** — reconcile `explorations/grammar.ebnf` (strip
  `[BOOTSTRAP]`/`[DEFERRED]`; `const`→`readonly`; complete the
  keyword/builtin list; de-defer floats; `FuncType` → `*func`/`@func`;
  drop stale `enum`/"3-word" text), seed the canonical
  `docs/spec/binate.ebnf` from it, then retire the explorations copy; (b)
  the **`pkg/rt` review** (classify each member stay/move/make-internal)
  which gates §20.2.
- **Phase 1 — Stable lexical/type/conversion/declaration core.** §5, §6
  (D1 recorded), §7 (incl. §7.13 layout, target-parameterized), §8, §9.
- **Phase 2 — Behavioral/type-system superstructure.** §10, §11, §12,
  §13, §14, §15. Quarantine the function-VALUE feature in §10; resolve the
  §13 precedence conflict; add the per-subsection impl-conformance
  overrides in §11.
- **Phase 3 — Modularity.** §16, §17. Two-path `-I`/`-L` resolution; fill
  the import-cycle gap; separate aspirational tier layout as informative.
- **Phase 4 — The two load-bearing cross-cutting chapters.** §18 memory
  model, §19 execution/dual-mode. Dual prose+formal presentation. Frame
  in-process embedding as a goal (D2); keep sentinel (Draft) and
  move/hooks (Proposed) out of the normative core.
- **Phase 5 — Intrinsic packages, catalogue, indices, ledger, framing.**
  §20 (lang first — mature; rt after its review; reflect/testing as
  Draft/Provisional), §21 (resolve the byte-order gap), Annex A (after
  reconciliation), Annex B, Annex C (the honest ledger — last, sourced
  from claude-todo), Annex D, then finalize §1/§2. Reserve the
  stdlib-sibling-spec pointer.

---

## 8. Prerequisites and gating (explicit)

1. **Grammar reconciliation + move into the spec (D4)** — reconcile
   `explorations/grammar.ebnf`, seed the canonical `docs/spec/binate.ebnf`,
   generate Annex A from it, then retire the explorations copy (updating
   the CLAUDE.md pointer). Required before Annex A is authoritative. Pure
   apparatus, no language-design risk.
2. **`pkg/rt` review** — the project's own stated prerequisite; gates
   §20.2 (rt runtime contract). The classification (not the cleanup)
   unblocks.
3. **`claude-todo.md` as a stability input** — Annex C and the
   §7/§10/§11/§13/§18/§21 status grading take the CRITICAL/MAJOR ledger
   as input, kept current as defects close.
4. **Tier-0 maturity** — lang is fairly mature; rt/reflect/testing are
   immature and will be authored as Draft/Provisional, not blockers.

---

## 9. Open decisions for the user

D1–D7 are all resolved — see "Decisions to date". No structural decisions
remain open.

Gaps the spec must *fill* during authoring (not user choices, but flagged
so they aren't missed): **byte order / endianness** (almost certainly
implementation-defined; §21); **import cycles** (permitted or diagnosed;
§16); the standard **annotation name/arg schemas** (§17, not enumerated in
the sources). And the standing prerequisites in §8 (grammar reconciliation
+ move; the `pkg/rt` review) before their dependent chapters/annex can be
finalized.

---

## 10. Spec conformance tests (keeping spec ↔ implementation in sync)

> **Detailed plan: [`plan-spec-tests.md`](plan-spec-tests.md)** — the triage of
> the ~480 rule-IDs, the `.rules` tag + coverage tooling, Annex C derivation, the
> authoring discipline, and the phasing (Phase A: seed the ledger with
> defect-xfails + the load-bearing invariants). This section is the summary.

To guarantee the spec matches the implementation **and vice versa**, every
normative, testable rule is tied to executable **spec conformance tests**.
This is what makes the implementation-conformance axis (§4b, Annex C) real
and mechanical rather than hand-curated — and it catches drift in both
directions. (Concrete motivation: while scaffolding, three status claims
went stale within days — `present()` extended, `==`-on-aggregates rejection
landed, length-0 enforced — exactly the drift a test-derived ledger would
have flagged automatically.)

**Almost no new machinery is needed.** The existing conformance harness
(`conformance/run.sh`) already supports everything:

- **Positive** tests (`NNN_name.bn` + `.expected`) — observable behavior.
- **Negative / rejection** tests (`NNN_name.bn` + `.error`, each `.error`
  line a regex that must appear in the failure output) — exactly what the
  spec's **Constraints** clauses need (must-be-rejected-with-diagnostic).
- **Multi-package** (`NNN_name/` dir), **per-mode** variants
  (`.expected.<mode>` / `.error.<mode>`), and **xfail** (`.xfail.<mode>`
  with a reason).
- Runs across all execution modes (builder / comp / int), so a rule is
  "conformant" only if it passes in **every** relevant mode.

**What spec tests add (the thin layer):**

1. **Rule-ID tagging.** Each spec test cites the rule-ID(s) it exercises
   (the stable IDs from §4 / `conventions.md`, e.g. `iface.dispatch.multireturn`,
   `type.slice.layout`). Simplest form: a sidecar file parallel to the
   existing ones (e.g. `NNN_name.rules`, one rule-ID per line). Existing
   conformance tests get retro-tagged incrementally where they map to a rule.
2. **Coverage tooling.** A report mapping rule-IDs ↔ tests, flagging
   **rules with no test** (spec says X, nothing verifies it) and **tests
   with no rule-ID** (behavior the impl has that no rule covers — a candidate
   for unspecified-behavior the spec should address). This is the
   bidirectional guarantee.
3. **Annex C derivation.** The implementation-conformance column is derived
   from results: rule-ID → its tests → pass/xfail per mode. An `xfail` (with
   its reason text, cross-referencing `claude-todo.md`) IS the
   "non-conformant" marker — so a known defect like interface multi-return
   dispatch shows up automatically as `iface.dispatch.multireturn:
   xfail(builder-comp-int, native)` rather than being hand-noted.

**Drift detection (both directions).** If the implementation changes
behavior, its spec test fails → forces a decision: *regression* (fix the
impl) or *intended change* (update the rule). Neither can silently diverge.

### D7 — where spec tests live (cross-repo placement) — RESOLVED

Spec tests must run against the toolchain, which lives in the **`binate`**
repo; the spec lives in **`docs`**. **Decided:**

- **Tests live in `binate`**, in a **`conformance/spec/`** subtree *organized
  by chapter* so the layout mirrors the spec (and reuses the existing
  `conformance/run.sh` harness + modes).
- **The spec (`docs`) references rule-IDs only** (stable, abstract — never
  file paths), so the two repos don't couple on paths.
- **A generated coverage report bridges them** (rule-ID → tests + pass/xfail
  per mode), surfaced in Annex C. `docs` never depends on the toolchain build.

Sub-questions for when we build it: retrofit existing conformance tests with
rule-IDs incrementally (recommended) vs only new tests; whether the coverage
report is committed to `docs` or published by CI; whether Annex C is fully
generated or a hand-maintained table that *references* the generated report.

**Scope note:** this section is a design; *building* the tagging/coverage
tooling — and especially wiring it into CI — is separate work, each piece its
own go-ahead. Spec tests would be authored in `binate`, which (per your note)
means a coordinated worktree when we get there.

## 11. Proposed next step

Await sign-off on the structure. If approved, the natural first action is
**Phase 0**: scaffold `docs/spec/` (index +
`conventions.md` + chapter stubs carrying their status badges and source
maps), and — as separate, individually-approved pieces — the
`grammar.ebnf` reconciliation pass and the `pkg/rt` classification. I will
not scaffold the docs repo or start the reconciliation without your
go-ahead (each is its own decision).
