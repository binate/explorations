# Plan: Binate Language Specification — structure and authoring plan

Status: **proposal for review** (2026-06-08). This is the high-level
*structure* and phased authoring plan for a formal Binate language
specification — not the spec itself. It realizes the two existing
`claude-todo.md` entries **"Language spec(s) — write the primary spec"**
and **"pkg/rt review"**, and supersedes the "discussion-only" status of
the former with a concrete plan.

It is grounded in a survey of the `explorations/` design docs
(`claude-notes.md`, `claude-discussion-detailed-notes.md`,
`grammar.ebnf`, the `plan-*.md` / `design-*.md` cluster,
`ir-backend-guidelines.md`) **and** the live open-defect ledger
(`claude-todo.md`). Load-bearing stability claims were verified
firsthand against the source files.

We do **not** propose authoring the whole spec at once. The point of
this doc is to fix the shape, conventions, and order, so chapters can be
written and landed incrementally while the language still changes.

---

## 1. Which spec is this? (the multi-spec map)

Per `claude-notes.md` § "Language specification — primary spec is
minimal — DECIDED", Binate is deliberately specified as **multiple
documents**, not one monolith. This plan covers the **primary language
spec** only:

- **Primary language spec** (this plan): syntax, type system, semantics,
  plus *only* the packages intrinsically tied to the language
  implementation — the `pkg/rt` runtime contract (after the pkg/rt
  review) and a future reflection/introspection surface. Includes the
  one-line reservation that user files cannot be named `*_test.bn`.
- **Minor secondary spec — testing** (later): the `_test.bn` packaging
  convention + `pkg/builtin(s)/testing`. May fold into the primary; TBD.
- **Major secondary spec(s) — stdlib** (later): I/O, containers,
  formatting, string utilities. Probably split by area.

**Why minimal.** Binate targets environments with no console, no
filesystem, no process model, no threads. A primary spec that embedded
I/O assumptions (a `string` type, an output stream, a process model)
would be unimplementable on bare-metal targets. Keeping stdlib out means
freestanding subsets stay conformant to the primary spec, and the
stdlib specs layer selectively per target. The spec therefore defines
**hosted vs freestanding** conformance.

---

## 2. Where it lives, and in what shape

- **Repo:** the new `docs` repo (`github.com/binate/docs`) — the
  published artifact. The design notes (`claude-notes.md`, `plan-*.md`,
  `grammar.ebnf`) stay in `explorations/` as the rationale/working tree
  that Annex D draws on.
- **Layout:** `docs/spec/`, **one Markdown file per chapter + per
  annex**, numeric filename prefixes fixing reading order, an index
  file, and a shared `conventions.md`. *Not* a single monolithic
  `spec.md` — a single huge file invites cross-worker clobbering, and the
  per-chapter split maps one-to-one onto the phased authoring plan
  (chapters drafted/reviewed/landed independently).
- **Grammar:** the normative grammar (Annex A) is kept in **lockstep
  with `explorations/grammar.ebnf`**, ideally *generated* from it (one
  source of truth) after the Phase-0 reconciliation pass — see Open
  Decision D4.
- **Cross-references use stable rule/anchor IDs** (e.g.
  `mem.ownership.transfer`, `type.slice.layout`, `exec.dualmode.thunk`),
  not section/page numbers, so references and conformance-test citations
  survive renumbering and the file split.

Proposed files:

```
docs/spec/00-index.md            ToC, status legend, reading-order map (links by stable ID)
docs/spec/conventions.md         status legend, requirement vocabulary, per-construct rubric, rule-ID scheme
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
docs/spec/20-implementation-defined-and-undefined-behavior.md
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
  → program execution → memory model → execution/dual-mode → behavior
  catalogue. Reading order minimizes forward references; the two
  load-bearing cross-cutting chapters (memory model, dual-mode) come
  **late**, after every term they need is defined (the Go-concurrency /
  Wasm-abstract-machine placement pattern).
- **Rigor grafts from ISO/ECMA style.** A first-class **Conformance**
  clause (elevating the dual-mode agreement contract); explicit Scope /
  Terms / Notation up front; a collected **implementation-defined /
  unspecified / undefined** catalogue with a reverse index (C Annex-J
  pattern); strict normative/informative separation.
- **Per-construct rubric** (every feature section): **Grammar**
  (normative, inlined EBNF) → **Constraints** (diagnosable static rules —
  maps onto Binate's "compiler checks upfront / interpreter defers"
  split) → **Static semantics** (typing, name resolution) → **Dynamic
  semantics** (runtime behavior incl. refcount/ownership effects and any
  compiled-vs-interpreted divergence) → **Exceptions** (error conditions
  / UB) → **Notes/Examples** (informative).
- **Normative by default; rationale is quarantined.** All "why" lives in
  Annex D and clearly-marked Note blocks, never in normative prose.
- **Two hardest concerns get dual presentation** (prose + a formal
  operational rule, with a statement of which is authoritative): the
  refcount memory model (§18 axioms, ownership transfer) and dual-mode
  dispatch (§19 thunk unification). These are the most-argued-over parts
  and the two execution modes must provably agree.

---

## 4. Status model — TWO orthogonal axes (important)

The language is under active development, so the structure makes status
**load-bearing and honest**. There are **two independent axes**, plus
the normative/informative axis:

### 4a. Language-design stability (per section/rule)

A four-value vocabulary, orthogonal to normative/informative (a Draft
rule is still normative-*in-intent*):

- **Stable** — semantics fixed; changes are breaking and rare.
- **Provisional** — specified and implemented but may still change.
- **Draft** — specified but partially/not implemented; normative-in-intent.
- **Reserved** — syntax/feature reserved, semantics not yet defined.

### 4b. Implementation-conformance status (the correction)

Separately, the spec tracks **whether the current toolchain actually
conforms to a Stable rule.** This axis is sourced from `claude-todo.md`
(the authoritative CRITICAL/MAJOR open-defect ledger, mandated by
CLAUDE.md's Bug Discovery Protocol) and lives in **Annex C** (status
table) and **Annex B** (implementation model).

The distinction matters and the first survey pass got it wrong by
omitting `claude-todo.md`: **a known miscompile does not make the
language rule unstable — it makes the implementation non-conformant.**
The rule "interface methods may return multiple values; errors are
`(T, @Error)`" is *Stable language design*; the fact that current
backends cannot yet dispatch it (a CONFIRMED CRITICAL defect) is an
*implementation-conformance* gap. The spec states the rule normatively
and Annex C honestly records "current implementations do not yet
conform — see claude-todo CRITICAL 2026-06-08."

This keeps the language-stability axis clean while still surfacing every
live defect. Annex C must take `claude-todo.md` (CRITICAL + MAJOR) as an
explicit input and map each open defect to the section(s) it touches.

### 4c. Do NOT reuse the grammar's `[BOOTSTRAP]`/`[DEFERRED]` tags

Those tracked the *retired Go-interpreter subset* (an
implementation-maturity axis for a tool that no longer exists), not
language stability. They must be **stripped** from the normative grammar
(see Phase 0).

Mechanics: stable rule/anchor IDs adopted from day one (cross-ref +
conformance-citation targets that survive churn); a visible `[Status]`
badge per chapter/section; the standalone Annex C ledger re-derived from
per-section tags **and** the defect ledger.

---

## 5. Proposed table of contents

20 chapters + 4 annexes. Each line: scope (1–2 sentences) · primary
sources · status caveat.

**1. Scope and Introduction** *(informative)* — What Binate is; design
goals and enumerated non-goals (no GC, no ownership/borrowing, no
exceptions, no maps, no `string` type, no `append`, no goto/init/defer);
"for Go programmers" framing; the minimal-primary-spec boundary (stdlib
is OUT). · *src:* claude-notes §Goals, §"primary spec is minimal",
differences-with-go (incomplete), §31 philosophy. · *Finalize last.*

**2. Conformance** *(normative)* — Conforming program; **compiler and
interpreter as co-equal implementations** that must satisfy the same
semantics; the **cross-mode agreement requirement** (where both modes
coexist, they shall agree exactly on observable layout/behavior on a
target; implementation-defined choices — notably word size — shall
agree); hosted vs freestanding. · *src:* claude-notes dual-mode +
layout-agreement, ir-backend-guidelines. · *§2.4 must split honestly:
the thunk-unification contract is settled; in-process same-process
interop is unverified (see D2).*

**3. Terms and Definitions** *(normative)* — Binding glossary: the
behavior-latitude taxonomy (target-invariant / target-parameterized /
implementation-defined / unspecified / undefined / backend-private) and
the core vocabulary (managed/raw pointer, managed-slice, refcount,
ownership transfer, move, destructor, vtable, impl, interface value,
function value, monomorphization, Self, thunk, TargetInfo, readonly). ·
*Standardize on `readonly` (the type modifier) vs `const` (compile-time
constant); `managed-slice` hyphenated.*

**4. Notation** *(normative)* — The ISO-14977-flavored EBNF metalanguage
(already in `grammar.ebnf`); the per-construct rubric; the
normative/informative discipline; the four-value status legend; the
stable rule-ID scheme; a light operational-rule notation for the
trickiest dynamic semantics. · *Records that Annex A must drop
`[BOOTSTRAP]`/`[DEFERRED]` and that grammar.ebnf needs the Phase-0
reconciliation before it can be cited.*

**5. Lexical Elements** *(normative)* — Source representation (ASCII);
identifiers; reserved keywords (incl. `readonly`) and builtin-operation
keywords (`make`, `make_slice`, `box`, `cast`, `bit_cast`, `len`,
`unsafe_index`, `sizeof`, `alignof`, `same`, `present`, `unsafe_div`,
`unsafe_rem`); predeclared shadowable names (`int`, sized ints, `bool`,
`byte`/`char`=`uint8`, `any`, `float32/64`, `iota`); literals + escapes
(no implicit null terminator); adjacent string-literal concatenation;
comments; automatic semicolon insertion. · *Stable core. `readonly`
keyword RECENT (2026-06-03), grammar not yet updated.*

**6. Constants** *(normative)* — Untyped literals and default types
(literal-only coercion, unlike Go's named constants); integer-constant
value range and union-range constant arithmetic (intermediate overflow
rejected, no wrap, no bignum); untyped-float class + strict
no-implicit-int↔float; literal overflow is a compile error. · *MUST
resolve the string-literal default-type contradiction — see D1.*

**7. Types** *(mixed)* — The type catalogue (value vs reference;
target-parameterized scalars; named distinct types vs aliases;
anonymous-struct structural equivalence; structs; arrays; raw slices
`*[]T` (2-word) vs managed-slices `@[]T` (**4-word**); the
length-0⟹no-backing invariant; managed `@T` / raw `*T` pointers and
nullability; `*func`/`@func`; interface value types; the `readonly`
modifier and its assignability lattice; forward-declared opaque types),
**followed by §7.13 Type Layout & Representation** — the single
normative home for the cross-mode ABI contract, parameterized by
`TargetInfo`. · *Catalogue largely Stable. Provisional/Draft: opaque
.bni types, interface-value byte layout ("future"), length-0 enforcement
(rule Stable, backends still violate). **Impl-conformance:** nested
arrays mis-compiled (claude-todo MAJOR); named-distinct field access
through underlying type rejected by checker (claude-todo SPEC ISSUE —
see D5).*

**8. Conversions** *(normative)* — The closed set of implicit
conversions (untyped-literal coercion, readonly-adding widenings, the
managed→raw borrow) + explicit `cast`/`bit_cast`. No implicit
numeric/named conversions. · *The implicit `@T`→`*T` borrow is the one
Provisional conversion (currently permissive in all positions; a
proposal would restrict it to borrowing positions —
proposal-restrict-implicit-raw-conversion.md).*

**9. Declarations and Scope** *(normative)* — `const` (compile-time,
scalar-only, no storage) vs `var` (storage; `.bni` extern form) vs the
`readonly` modifier; `iota` in grouped const blocks; `:=` and the D1
disambiguation; block scope and shadowing; permitted package-scope
declarations (function-local `type` is a parse error); init order (no
`init()`). · *Stable; grammar still spells the modifier `const`.*

**10. Functions, Methods, and Function Values** *(mixed)* — Function
declarations; single/multiple returns (position lists, not tuples; no
named returns) and destructuring; variadics + spread; methods, the five
receiver kinds, receiver smoothing (safe direction only), object- vs
handle-readonly dispatch, one-method-per-name, one-level auto-deref;
function VALUES (`*func`/`@func`, 2-word repr; closures capture by value;
escape via lint not type error; method expressions/values); indirect
calls as the mode-transparent unification mechanism (xref §19). ·
*Declaration surface Stable; the function-VALUE feature is RECENT —
Provisional. **Impl-conformance:** destructuring a multi-return `@func()`
call rejected at type-check (claude-todo MAJOR). Recursive anonymous
closures Reserved.*

**11. Interfaces, impl, and Self** *(mixed)* — Interface declarations
(named, top-level; bare name is not a type); `*Iface`/`@Iface` values
and pointers to them; `impl` (relational, separate, nominal explicit
satisfaction — no duck typing); construction-site explicit conversions +
boxing; `any`/`*any`/`@any`; extension/embedding + transitive impl with
static upcast; the `Self` type and object-safety; interface aliases;
cross-package interfaces (no orphan rule); the primitive-impl stdlib
carve-out and canonical interfaces (`Compare`/`String`/`Hash`); vtable
dispatch (observable semantics normative, layout informative → Annex B).
· *Language design overwhelmingly Stable/shipped. **Impl-conformance
(per-subsection overrides required):** interface-method MULTI-RETURN
dispatch is CRITICAL-broken (can't dispatch `(T, @Error)` at all — blocks
pkg/std/io); transitively re-exported interface → SIGSEGV (CRITICAL,
memory-unsafe); sub-word multi-return mis-unpacked on VM/native
(claude-todo, 2026-06-08). Interface-value byte layout Provisional.*

**12. Generics and Enumerations** *(normative)* — Type parameters on
functions/structs/interfaces (each constraint a single named interface;
combine via a named interface, no `+`; `[T any]`); no generic methods
(use generic free functions); no conditional impls (v1); monomorphization
(no type inference; explicit args at the site; constraint calls lower to
direct calls); cross-package generic bodies in `.bni` (source-text,
C++-template-in-header model); no first-class enums (named-int +
`const(...)`+`iota` idiom; tagged unions are a separate future feature).
· *Complete 2026-05-21, Stable. v1 restrictions are deliberate scope, not
instability. Generic-path `==` comparability gap is OPEN (xref §13).*

**13. Expressions** *(normative)* — Operands, primaries, operators;
**the grammar's 11-level Go-style precedence is authoritative** over the
conflicting claude-notes prose; defined arithmetic (truncate-toward-zero
division, `%` sign-of-dividend, two's-complement wrap, defined shifts,
div/mod-by-zero and signed-MIN/-1 as defined panics — no UB);
bitwise/shift; comparison (no chaining; pointer address-equality; IEEE-754
incl. unordered-`!=`); short-circuit logical (bool-only); selectors (`.`
only, auto-deref, no `->`); index/slice + bounds checks; composite
literals (fresh construction; control-flow disambiguation); the
disambiguation rules D1–D11; no operator overloading. · *Core Stable.
Provisional/OPEN: struct/array fieldwise equality + generic-instantiation
comparability not implemented (Draft). **Flag** the MAJOR compiler-DoS:
cyclic non-struct named types hang the comparability checker.*

**14. Statements** *(normative)* — Block-local decls; simple statements;
assignment (multi-target destructuring; compound); inc/dec
(postfix-only); `if`; `for` (four forms incl. range, with for-in
value-vs-index semantics differing from Go); `switch` (no fallthrough);
`return`/`break`/`continue` (no labels, no goto). · *Stable. Note
deliberate omissions: no if/for/switch init clause, no labeled
break/continue, no goto, no fallthrough.*

**15. Built-in Operations** *(normative)* — The keyword builtins
(allocation `make`/`make_slice`/`box`; conversions `cast`/`bit_cast`
→ xref §8; size/layout `sizeof`/`alignof`/`len`; unchecked
`unsafe_index`/`unsafe_div`/`unsafe_rem`; identity/presence
`same`/`present`; volatile-access builtins; managed-representation
introspection). · *Most Stable. `same`/`unsafe_div`/`unsafe_rem` RECENT.
`present()` accepts only interface values today; OPEN extension to other
nullable types — Provisional. `move`/`ispod` are PROPOSED → Annex D, not
here.*

**16. Packages and Program Structure** *(mixed)* — `package "path"`;
`.bn` + at-most-one `.bni`; sibling-directory layout; imports (by path,
optional alias); **structural visibility** (exported iff in `.bni`; no
keyword/capitalization visibility); `.bni` contents (authoritative type
defs, signatures, generic bodies, const/var-extern, opaque types); the
`main` package; the two independent `-I`/`-L` search paths + `--root`;
symbol mangling (informative ABI → Annex B); `*_test.bn` reservation. ·
*Core Stable. Tier layout/ifaces-impls trees largely aspirational →
informative. GAP: import cycles unaddressed — spec must add a rule. The
package-introspection/reflection model is unratified design notes. Scope
boundary question → D3.*

**17. Program Initialization and Execution** *(normative)* — Retained vs
immediate evaluation (a non-REPL run is fully validated before execution
via an external entry call; REPL defers); no forward declarations
required; package init order (no `init()`); `main` entry + termination
(host-dependent argv); the annotation system (`#[...]` syntax/attachment,
namespacing, type-identity effects); errors-as-values (no
exceptions/panic-recover/defer; any type may be an error); the closed set
of defined non-recoverable runtime panics (bounds, divide-by-zero,
MIN/-1). · *Stable. The normative list of standard annotation names/arg
schemas is not yet enumerated in sources — must be supplied. Proposed
annotation features (`#[config]`, `#[asm]`, `#[derive]`, debug hooks) →
Annex D.*

**18. Memory Model: Reference Counting and Object Lifetime** *(mixed)* —
The load-bearing dynamic-semantics chapter. Reference counting (no GC, no
ownership/borrowing; cycles leak = programmer error); the refcount axioms
(live⟹rc>0; rc=0 runs the destructor before free; copy invokes the copy
constructor/RefInc; assignment is copy-then-destroy); managed-allocation
header + destructor-vs-free-function separation; recursive deterministic
drop (statically resolved, no per-object RTTI); ownership transfer
(callee-side param RefInc; return carries one transferred ref; raw params
borrow); statement-level temporary lifetime (temp-borrow UAF is *user
error* — the compiler shall not suppress RefDec); managed-slice lifetime
+ subslicing + length-0 tie-in; static-managed immortal (sentinel)
allocations; threading/atomicity stance (single-threaded default,
non-atomic v1). · *Axioms + ownership transfer + temporaries Stable.
Provisional: length-0 enforcement. Draft: static-managed sentinel
(implementation in-flight on branch work-6; value unfinalized; no
compiled-mode runtime coverage yet). Proposed (keep out of normative
core): `move`-as-guarantee (on-record recommendation: optimization only —
refcounts are introspection, not a semantic contract), the `move`
builtin, debug lifecycle hooks. Keep destructor mechanism detail in
Annex B.*

**19. Execution Model: the Abstract Machine and Dual-Mode Interop**
*(mixed)* — The abstract machine (shared heap + refcount metadata + a
function-pointer call primitive both modes satisfy identically); function
pointers as the unification mechanism (compiled = native address/direct
call; interpreted = thunk; caller mode-oblivious; one-indirection cost
only at the boundary); cross-mode prerequisites (one heap/refcounting/
type-system; identical layout per §7.13; no marshalling; `.bni` signature
discovery + symbol resolution); mixed-mode vtables; the embeddable
interpreter + REPL model; the runtime function manifest (the `pkg/rt`
contract — the only library surface in the primary spec); enumerated
intentional cross-mode divergence points. · ***THE single biggest
caveat — SPLIT honestly (see D2):*** the thunk-unification design and the
identical-layout prerequisite are Stable, and the two engines pass
conformance individually; but **in-process same-process embedding (one
shared heap inside a running binary, thunked compiled→interpreted calls,
mixed-mode vtables, hot-swapping) is design-of-record but NOT verified
end-to-end** — do not write it as realized until verified. The runtime
manifest is actively shrinking and **gated on the pkg/rt review**.*

**20. Implementation-defined, Unspecified, and Undefined Behavior**
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
while leaving absolute sizes per-target and parameterizing everything by
`TargetInfo`. **Unspecified:** evaluation order where unpinned, padding
contents, inline-vs-runtime-call, shared-static-literal storage.
**Undefined** (the raw-pointer/refcount escape hatch, fenced as *user
error*, not a promised trap): UAF via a borrowed raw slice/pointer
outliving its backing, dangling `*T` deref, breaking refcount invariants
through raw aliasing, `bit_cast`/`unsafe_*` out of contract,
mode-dependence beyond the one-indirection cost.

**Annex A. Grammar Summary** *(normative)* — The complete EBNF, in
lockstep with `grammar.ebnf`, metalanguage per §4, productions also
inlined per feature chapter; D1–D11 as a consolidated table. · *BLOCKED
on the Phase-0 reconciliation pass; until then prose clauses govern.*

**Annex B. Implementation Model and Impl-defined Index** *(mixed)* —
*(Informative)* the runtime/ABI contracts observable in consequence but
not mechanism: vtable layout, value-receiver thunks, weak_odr dedup,
destructor/handle-dispatch mechanics, name mangling + object-format
symbol decoration, the IR/backend split (backend-private concerns), and
the observable-ABI-vs-backend-private boundary. *(Normative index)* the
Annex-J-style reverse index from each impl-defined/unspecified/undefined
point to its defining section + a target-word-size-dependent-points
table. · *Flag the OPEN mangler class (struct types not carrying
fully-qualified names; genMethodValue cross-package value receiver) — the
specific `reflect.Package` collision was FIXED 2026-06-08, but the class
and the proposed dedup-mismatch hard-error guard remain.*

**Annex C. Stability Status Table** *(informative)* — The standalone,
auditable maturity ledger: every chapter/section with its
language-stability marker **and** its implementation-conformance status,
the latter sourced from `claude-todo.md` (CRITICAL + MAJOR) mapped to
sections. The single most-valued fidelity artifact. · *Finalize last.
Must call out: the unverified in-process dual-mode interop; the
sentinel-in-flight; length-0 enforcement gaps; the interface
multi-return / transitive-re-export / nested-array / sub-word-packing
defects; function values RECENT; present() interface-only; struct/array +
generic comparability OPEN; the const→readonly grammar reconciliation;
the string-literal default-type contradiction; the pkg/rt-review
prerequisite.*

**Annex D. Rationale and Design Notes** *(informative)* — Why refcounting
over GC and over ownership/borrowing; why two pointer kinds and two slice
kinds; the no-implicit-cost philosophy; the minimal-primary-spec
rationale; the v1-without-foreclosing-v2 deferrals (non-nullable
pointers, tagged unions, atomic refcounts, move-as-guarantee); the
extended Go comparison and prior art; the C-free-target posture and
FFI-as-future-escape-hatch. The home for every PROPOSED/deferred item.

---

## 6. Conventions (summary)

- **Terminology pins** (defined once in §3): `readonly` is the type
  modifier everywhere; `const` is *only* the compile-time-constant
  declaration; legacy `const T` / `[N]const char` are superseded.
  `managed-slice` (hyphenated) = `@[]T`, **4 words**
  `{data,len,backing,backingLen}` (the "3-word" descriptions are stale).
  Canonical interface methods are `Compare`/`String`/`Hash`
  (`toString`/`less`/`hash` superseded). **Stale-doc warning carried in
  clause notes:** `claude-discussion-detailed-notes.md` §6/§7/§19 predate
  the interface-syntax revision (bare `Stringer` as a raw value,
  `type X interface`, anonymous interfaces — all dropped) and write
  `const` for `readonly`; `claude-notes.md` + `plan-*.md` are
  authoritative on conflict.
- **Grammar notation:** keep the ISO-14977-flavored EBNF; `…` = inclusive
  character range, double-quotes = literal terminals, juxtaposition =
  concatenation. Do not switch to Go's bespoke variant.
- **Cross-referencing:** stable IDs; exactly one normative home per rule
  (e.g. §7.13 owns layout; §8.4 owns the managed→raw borrow; §18.7 owns
  temporary lifetime; §9.7 only *previews* it). "Related rules" notes
  link cross-cutting appearances (`readonly` in §7/§10/§11).
- **Impl-defined taxonomy:** the C three-way model, with the extra axis
  the runtime cluster needs — *target-parameterized-but-fixed-per-target,
  all modes must agree* vs *truly an implementation's private choice*.
  The cross-mode agreement rule (§2.4 + §20.3) is the master ABI
  invariant: implementation-defined choices, notably word size and every
  layout fact, MUST agree between an implementation's compiled and
  interpreted modes on a given target.

---

## 7. Phased authoring plan

Write stable, foundational, low-churn material first; the
highest-design-risk cross-cutting chapters last.

- **Phase 0 — Apparatus + prerequisites (before any normative writing).**
  §4 Notation (metalanguage, rubric, status legend, rule-ID scheme); seed
  §3 Terms (taxonomy + core vocabulary); skeletons of §1/§2 to anchor
  scope decisions. **In parallel, two verified-necessary prerequisites:**
  (a) the `grammar.ebnf` **reconciliation pass** — strip
  `[BOOTSTRAP]`/`[DEFERRED]`; `const`→`readonly`; complete the
  keyword/builtin list (it omits `make_slice`/`sizeof`/`alignof`/
  `readonly`/`same`/`present`/`unsafe_div`/`unsafe_rem`); de-defer floats;
  `FuncType` → `*func`/`@func`; drop stale `enum` and "3-word" text;
  reconcile D5; (b) the **`pkg/rt` review** (the project's own stated
  prerequisite — classify each member stay/move/make-internal) which
  gates §19.6.
- **Phase 1 — Stable lexical/type/conversion/declaration core.** §5, §6,
  §7 (incl. §7.13 layout — target-parameterized, the ABI anchor), §8, §9.
  The largest body of settled material; builds the cross-reference graph.
  Resolve D1 (string-literal default) here.
- **Phase 2 — Behavioral/type-system superstructure.** §10, §11, §12,
  §13, §14, §15. Quarantine the still-settling function-VALUE feature in
  §10; resolve the §13 precedence conflict (grammar wins); add the
  per-subsection impl-conformance overrides in §11 (interface
  multi-return / re-export defects).
- **Phase 3 — Modularity.** §16, §17. Describe the two-path `-I`/`-L`
  resolution as superseding the single-Roots model; fill the import-cycle
  gap; separate aspirational tier layout / package-manager / reflection
  as informative/Draft.
- **Phase 4 — The two load-bearing cross-cutting chapters.** §18 memory
  model, §19 execution/dual-mode. Dual prose+formal presentation. **Verify
  the in-process dual-mode realization before writing §19.3/19.4/19.5 as
  realized** (D2). Keep sentinel (Draft) and move/hooks (Proposed)
  flagged and out of the normative core.
- **Phase 5 — Catalogue, indices, ledger, framing.** §20 (resolve the
  byte-order gap), Annex A (finalize after reconciliation), Annex B,
  Annex C (the honest ledger — finalize last, sourced from claude-todo),
  Annex D, then finalize §1/§2.

---

## 8. Prerequisites and gating (explicit)

1. **`grammar.ebnf` reconciliation** — required before Annex A can be
   cited as authoritative. Pure apparatus, no language-design risk.
2. **`pkg/rt` review** — the project's own stated prerequisite to the
   primary spec; gates §19.6 (runtime contract). The classification (not
   the cleanup) is what unblocks.
3. **`claude-todo.md` as a stability input** — Annex C and the §7/§10/§11/
   §13/§18/§20 status grading must take the CRITICAL/MAJOR ledger as an
   input, kept current as defects close.

---

## 9. Open decisions for the user

These are genuine decisions — several touch language semantics, which I
will not pin unilaterally (per the project rule).

- **D1 — string-literal default type.** `claude-notes.md` contradicts
  *itself*: line 435 says default `*[]readonly char` (raw borrow); lines
  468/470 say default `@[]readonly char` (managed). Default type affects
  assignability. **Recommendation:** pin `@[]readonly char` (the spelling
  in the two most-recent paragraphs and the composite-literal
  generalization; composes with the static-managed-sentinel immortality
  path so the default is self-sufficient), mark `*[]readonly char` an
  allowed coercion target, and reconcile line 435 in the same pass. Needs
  your sign-off (semantics).
- **D2 — in-process dual-mode realization.** Is the same-process
  embedded-interpreter path (one shared heap; thunked
  compiled→interpreted; mixed-mode vtables; hot-swapping) realized
  end-to-end today, or are `cmd/bnc` and the VM currently separate
  processes? `ir-backend-guidelines.md` asserts it in the present tense
  (and references the now-retired bootstrap Go interp), but no surveyed
  doc confirms the same-binary path. **Recommendation:** do not write
  §19.3–19.5 as realized until verified; keep the settled parts
  (thunk-unification, identical-layout) Stable and mark the embedding
  Provisional. A verification/author task, not an assumption.
- **D3 — language-spec vs toolchain/library-spec boundary.** How much of
  package tiers / ifaces-impls trees / package-manager / REPL-redefinition
  / reflection belongs in the *language* spec vs a separate spec?
  **Recommendation:** per-topic split — keep search-path resolution (a
  language-observable contract), structural visibility, and `*_test.bn`
  reservation normative; move tiers/package-manager/reflection to
  secondary specs; keep REPL-redefinition as a clearly-marked informative
  §19.5 subsection. Affects scope — confirm.
- **D4 — grammar annex: generated or hand-maintained?**
  **Recommendation:** generate Annex A from `grammar.ebnf` (single
  source) after Phase-0 reconciliation; fall back to a hand-maintained
  copy + a hygiene check that the two agree. Tooling/process decision.
- **D5 — named-distinct field access (SPEC ISSUE, claude-todo line 75).**
  Does a named-distinct type permit field access / method dispatch
  through its underlying type (`type H @Box; h.v` is currently rejected by
  the checker)? This is a genuine language-design question, not just a
  bug — it needs a decision that §7.3 will record. Surfacing, not
  pinning.

(Also gaps the spec must *fill* but that aren't really choices: byte
order/endianness — almost certainly implementation-defined; import
cycles — permitted or diagnosed.)

---

## 10. Proposed next step

Await a decision on the structure (and ideally D1–D3). If approved, the
natural first action is **Phase 0**: scaffold `docs/spec/` (index +
`conventions.md` + chapter stubs carrying their status badges and source
maps), and — as separate, individually-approved pieces — the
`grammar.ebnf` reconciliation pass and the `pkg/rt` classification. I will
not scaffold the docs repo or start the reconciliation without your
go-ahead (both are their own decisions).
