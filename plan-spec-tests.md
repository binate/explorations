# Plan: Spec Conformance Tests (detailed)

Expands `plan-language-spec.md` §10 + decision D7 into an actionable plan. The
spec now has ~480 declared rule-IDs across Ch.3–21 + Annex A; this is the plan to
tie the **testable** ones to executable tests and derive the
implementation-conformance ledger (Annex C) mechanically.

**Status: design.** *Building* the tagging/coverage tooling and *wiring CI* are
separate go-aheads, each its own decision. Spec tests are authored in the
**`binate`** repo → a coordinated worktree when we start.

---

## 1. Goal

Make the **implementation-conformance** axis (§2.6, Annex C) mechanical and
**bidirectional**, not hand-curated:

- Every normative *testable* rule-ID is tied to executable spec tests.
- A generated **coverage report** maps rule-ID ↔ tests ↔ pass/xfail-per-mode.
- **Annex C is derived** from that report — a known defect shows up
  automatically as `xfail(modes, reason→claude-todo)`, never hand-noted.

Drift is caught both ways: an impl behavior change fails its spec test (forces
*regression-vs-intended-change*); a rule with **no** test is a visible coverage
gap; a test with **no** rule-ID is impl behavior the spec doesn't cover (a
candidate unspecified-behavior item). (Motivation: while scaffolding, several
status claims went stale within days — exactly the drift a test-derived ledger
flags automatically.)

## 2. Placement & harness (D7 — settled)

- Tests live in **`binate`** under **`conformance/spec/<chapter>/`**, mirroring
  the spec's chapter layout, reusing `conformance/run.sh` + its mode sets.
- The spec (`docs`) references **rule-IDs only** — stable, abstract, never file
  paths — so the two repos don't couple on paths.
- A generated **coverage report** bridges them; `docs` never depends on the
  toolchain build.
- Harness conventions already supported (no new harness work):
  - **Positive:** `NNN_name.bn` + `.expected` (run, compare stdout).
  - **Negative (Constraints):** `NNN_name.bn` + `.error` (must fail; each
    `.error` line is a regex that must appear in the failure output).
  - **Multi-package:** `NNN_name/` directory.
  - **Per-mode:** `.expected.<mode>` / `.error.<mode>`.
  - **Xfail:** `.xfail.<mode>` (with reason) or `.xfail.all`.
  - Modes: `builder-comp`, `builder-comp-int`, `builder-comp-int-int`,
    `builder-comp-comp`, `builder-comp-comp-int`, `builder-comp-comp-comp`, plus
    the cross-compile/baremetal modes. A rule is **conformant** only if it passes
    in **every** relevant mode — which is also the **cross-mode-agreement**
    check (§2.4): the same compiled and interpreted run must agree.

## 3. The rule-ID tag (the one new artifact)

- Each spec test carries a sidecar **`NNN_name.rules`** — one rule-ID per line —
  citing the rule-ID(s) it exercises. (Multi-pkg dir: `NNN_name/NNN_name.rules`.)
- A test may cite **multiple** rule-IDs; a rule-ID may be covered by **multiple**
  tests (positive + negative + boundary). Convention: the **first** line is the
  test's PRIMARY target; the rest are incidental coverage.
- The coverage tool validates every cited rule-ID against the spec's declared
  IDs (catches typos and post-rename dangling citations).

## 4. What's testable — triage of the ~480 rule-IDs

Each rule-ID falls into exactly one bucket; the coverage tool is told which:

| Bucket | Test artifact | Examples |
|--------|---------------|----------|
| **Positive-testable** (defined behavior) | `.bn` + `.expected` asserting the observable result | most `expr.*` / `stmt.*` / `func.*` / `conv.*` / `builtin.*` / `exec.*` |
| **Negative-testable** (a **Constraint** — must be rejected) | `.bn` + `.error` | every `_(Constraint)_` rule + every "shall be rejected" / "is an error" clause (e.g. `pkg.files.test`, `iface.impl.nominal`, `conv.cast.const-not-laundered`) |
| **xfail-now** (a flagged impl non-conformance) | the test exists, marked `.xfail.<mode>` with a reason → claude-todo | the package-scoped-imports CRITICAL, `builtin.panic.vm-noop`, inc/dec-lvalue drop, indexed array literals, the generics gaps, the cast bitwise ≥2^63 residual |
| **Intentionally untestable** (NOT a gap) | none — allowlisted | non-observable rules: `mem.move.optimization`, `mem.scope-exit` release order, intermediate refcounts, padding contents, backend-private realization |
| **Framework / informative** (excluded from the denominator) | none | `term.*` (Ch.3 definitions), `notation.*` (Ch.4 metalanguage), `behavior.*` (Ch.21 is a back-reference **index** — its items are tested via their HOME rule-IDs), Ch.1 |

The **denominator** for coverage % is *positive- + negative- + xfail-* rules; the
allowlist and framework buckets are excluded so the gap list is honest.

## 5. Coverage tooling — `binate/scripts/spec-coverage` (to build)

1. **Rule-ID source.** Consume an exported `rule-ids.txt` the spec emits (a tiny
   `docs/scripts/extract-rule-ids.py`, like the Annex-A generator), rather than
   reading `docs/spec/*.md` directly — this decouples the repos. The export also
   records each rule's bucket tag (Constraint / informative / untestable) where
   derivable from the `_(Constraint)_` marker + an allowlist.
2. **Parse** all `conformance/spec/**/*.rules`.
3. **Collect results** from a `conformance/run.sh conformance/spec` run across the
   mode set: pass / fail / xfail-per-mode per test.
4. **Emit a report** with the four bidirectional findings:
   - **rules-with-no-test** (minus the untestable/framework buckets) — the gap list;
   - **`.rules`-citing-a-nonexistent-rule** — dangling/typo citations;
   - **tests-with-no-`.rules`** in the spec subtree — must be tagged;
   - per-rule-ID: covering tests + worst-mode status (conformant / xfail(modes) / untested).
5. **Two outputs:** a human Markdown table and a machine **JSON** (for Annex C).

## 6. Annex C derivation

- Annex C's implementation-conformance column is **generated** from the coverage
  JSON: rule-ID → status (conformant / `xfail(mode-list, reason→claude-todo)` /
  untested).
- Recommended shape: a **generated status table** + a short hand-written preface
  (not a fully hand-maintained table). The coverage report is a committed
  generated file in `docs` (regenerated like Annex A) or CI-published — decide
  when building.

## 7. Authoring discipline (per rule-ID)

1. Cite the primary rule-ID in `.rules`.
2. If the rule is a **Constraint**, write the NEGATIVE `.error` test (the
   must-reject case) — this is the half most likely to be missing.
3. If it's **defined behavior**, write the POSITIVE `.expected` test asserting
   the result.
4. Cover the **boundary** the rule names (shift: overshift AND negative-count;
   cast: in-range AND out-of-range; bounds: in AND out).
5. Run **all** relevant modes; on a known-defect failure, mark `.xfail.<mode>`
   with a reason + claude-todo ref — **never silently drop a mode** (a smoke pass
   that skips a mode hides exactly the cross-mode divergences we test for).
6. Dual-mode rules (`exec.*`, the cross-mode invariant) need **no special**
   test — the same positive test running in compiled and interpreted modes *is*
   the §2.4 agreement check.

## 8. Priorities & phasing

**Phase A — seed the ledger (highest value first).**
- **A1 — the flagged CRITICAL/MAJOR defects → xfail tests**, so Annex C captures
  them immediately and reproducibly. One test per `claude-todo.md` "spec Ch." /
  CRITICAL/MAJOR entry: **package-scoped imports** (incl. the new BUG-B
  type-layout-corruption and BUG-C implicit-same-last-segment facets — these are
  the most important to pin with a reproducible test), `builtin.panic.vm-noop`,
  inc/dec on a non-ident lvalue, indexed array literals, the two generics
  enforcement gaps, the cast bitwise-`≥2^63` residual, the alias-vs-decl
  redeclaration gap. This makes Annex C *real* on day one and pins the landmines.
- **A2 — the load-bearing cross-cutting invariants:** type layout §7.13 (a
  size/offset/cutoff assertion per word-count rule), the closed defined-panic set
  §17.5 (one abort test each), the observable refcount contract §18 (no-leak /
  deterministic-free, via reflection or an observable free), the §2.4/§19.3
  cross-mode agreement (one program, both modes), conversions §8 (assignability,
  cast fit, float→int saturation).

**Phase B — bulk per-chapter coverage**, chapter by chapter, positive + negative
per testable rule-ID, driving the gap list down (track coverage % per chapter).

**Phase C — retrofit** existing `conformance/` tests with `.rules` tags where
they already map to a rule (the ~500 existing tests are a coverage windfall); the
coverage tool surfaces the untagged ones.

## 9. Open sub-questions (decide when building)

- Rule-ID source: exported `rule-ids.txt` (recommended — decouples) vs reading
  `docs` directly. **→ DECIDED: export** (see §11).
- Coverage report: committed generated file in `docs` vs CI-published. *(open)*
- Annex C: generated table + preface (recommended) vs fully hand-maintained. *(open)*
- Retrofit: incremental tag-as-you-go on existing tests (recommended) vs new-only. *(open)*

## 11. Build kickoff — decisions & recon (Phase B start)

Phase B chosen as the first work. Two forks settled:

- **Thin tooling first** — build the rule-ID export + a minimal coverage reader
  before authoring, so Phase B is measurable (coverage %, validated `.rules`
  citations) from test #1.
- **First chapter end-to-end: Ch.13 Expressions** (the template) — densest mix
  of positive + negative + boundary cases (divzero, MIN/−1, shift
  overshift/negative, comparability).

Insight: **Phase B subsumes A1.** A thorough chapter yields that chapter's
known-defect xfails for free; only **A2** (cross-cutting invariants — §7.13
layout, §17.5 panic set, §18 refcount, §2.4 cross-mode) stays a separate pass.

### Recon findings that shape the extract script

- **Rule-ID declaration detector:** a declaration is a **column-0** line
  `` `<prefix>.<area>.<name>` `` followed by ` — ` (em-dash lede). Verified
  repo-wide: **490** such declarations; **zero** hide in lists/tables/blockquotes,
  so the col-0 detector is complete. References (mid-prose backtick mentions) are
  *not* declarations and are ignored.
- **Bucketing is not a single clean marker.** Only **6** rules carry an explicit
  `` _(Constraint)_ `` marker; the per-construct "Constraints" rubric from
  `conventions.md` is *not* mechanically marked in the spec body. So the extract
  script tags: `constraint` (explicit `_(Constraint)_`), `constraint-candidate`
  (body matches a reject-signal regex — "is rejected"/"is an error"/"compile(-)time
  error"/"shall be rejected"/"must reject" — needs human confirmation),
  `framework` (prefixes `term`/`notation`/`behavior`, + Ch.1), and an
  `untestable` allowlist (mem.move.optimization, scope-exit release order,
  padding contents, …). Default bucket = `positive`.
- No per-rule status legend markers inline (Stable/Provisional/etc. are
  per-section, not per-rule) — not used for bucketing.

### Build steps (this session)

1. ✅ `docs/scripts/extract-rule-ids.py` → `docs/spec/rule-ids.txt` (docs
   `a7a88d9`): 483 declared rule-IDs, bucket-tagged; col-0 declaration detector
   verified complete repo-wide. **Not** wired into docs CI.
2. ✅ `binate/scripts/spec-coverage/run.sh` + vendored `rule-ids.txt` (binate
   `05b2bf56`): static coverage (per-chapter %, GAPS / DANGLING / UNTAGGED) + JSON.
   `--run` (per-mode pass/xfail) is a later increment. **Not** wired into binate CI.
3. ✅ `conformance/spec/13-expressions/` — 33 tests, all 29 denominator rules
   cited (100%), 3 `.xfail.all` for known defects (aggregate `==`, indexed array
   literal, generic-literal head). `conformance/run.sh` registers `spec/` as an
   **opt-in** subtree (runs only when filtered, e.g. `run.sh <mode> spec`) — NOT
   in the default suite/CI.
4. ✅ Ch.13 green on builder-comp, builder-comp-int (VM), builder-comp-comp
   (gen1), builder-comp-comp-comp (gen2) — 30 pass / 0 fail / 3 xfail each
   (cross-mode agreement, §2.4). Findings filed to claude-todo (Ch.13, 2026-06-18).

**Landed on main 2026-06-19:** binate `21aba0b6` (spec-coverage), `74b8362f`
(Ch.13 + opt-in runner), `6cdbfedf` (review refinements), `595b0eee` (sub-rule
citations + inferred-len/d4-paren tests); docs `a7a88d9` (extract-rule-ids),
`2389676` (drop stale generic-literal note), `2f95afc` (declare 3 sub-rule-IDs).
Ch.13 is now **42 tests, 32/32 denominator (100%)**, 3 xfails (aggregate `==`,
indexed array, inferred-len).

**Ch.8 Conversions landed 2026-06-19:** binate `98b8012e` (spec-coverage hygiene
check), `0807f4f0` + `eaeb9d26` (Ch.8: **15 tests, 11/11 (100%)**, green on
compiler/VM/gen1/gen2/native_aa64/arm32_baremetal). spec/ now runs in the default
suite (`d6a0bfc3`). Findings filed (claude-todo, Ch.8 2026-06-19): bit_cast
sub-word narrowing (VM + native; → scalar-diff harness); `int↔int64` implicit on
64-bit (NEEDS A DECISION); stale §8.5 residual note. Phase B chapter order from
the triage: **Ch.16 Packages** next (highest defect-pinning), then **Ch.11
Interfaces** — both via the authoring fan-out.

**Ch.16 Packages — PARTIAL, committed 2026-06-19** (binate `5c4c226c` nested
multi-package harness + `f7ed4eb4` tests). Authored via a 5-way fan-out; **28
tests, 21/22 rules (95%)**, green on compiler/VM/gen1/gen2/native_aa64 (VM: 3
xfails — user-package `_Package` + C-call FFI, both VM non-goals). The
build-constraint group (`#[build(...)]`) needs real-mechanism rework — the lone
GAP is `pkg.build.errors` (claude-todo, Ch.16 2026-06-19). Harness gain: run.sh
now discovers nested multi-package tests under spec/. NOT yet landed on main
(committed on the worktree). **Ch.11 Interfaces** is next.

User decisions on the follow-ons (2026-06-19): (4) ✅ correct stale spec note —
DONE. (5) ✅ declare the sub-rule-IDs — DONE (also found d4-paren already works;
the genuine remaining gap is inferred-len). (2) ✅ promote `conformance/spec/`
to the default suite + CI — DONE (binate `d6a0bfc3` removed the opt-in gate).
Validated green on 8 of 10 `all` modes (incl. `native_aa64` + `arm32_baremetal`);
`native_x64` (chronically red pre-existing, claude-todo #203) and `arm32_linux`
were not validatable on the darwin/arm64 host — the next CI run on `main`
confirms them. (3) wire `extract-rule-ids.py`/`spec-coverage` into CI — DISCUSS.

**Ch.11 Interfaces — landed on main 2026-06-19** (binate `34ae6eb3`).
Authored via the design fan-out (6 clusters) + central validation + a 6-cluster
adversarial review. **45 tests, 25/25 rules (100%)**, green across 7 modes
(builder-comp, VM int, int-int, gen1, gen2, native_aa64, arm32_baremetal); 4
xfails: `048_transitive_direct_ancestor` (.xfail.all — known ancestor-walk
checker gap), `062_noorphan_imported_third_pkg` (.xfail.all — NEW MAJOR bug, see
below), and the `081`/`082` nil-abort pair (per-mode, re-homing root 385/386 with
rule-IDs). DANGLING=0, UNTAGGED=0, hygiene green (15/15).

**MAJOR bug surfaced by the review** (claude-todo, 2026-06-19): a cross-package
`impl R : I` declared ONLY in an imported THIRD package (not R's package, not the
root) is accepted by the checker but its (R,I) vtable is not wired → null-vtable
crash. Pinned by `062` (xfail). Same `collectImportedImplsFromDecl` machinery as
the ancestor-walk MAJOR. **Needs a user prioritization decision.**

**Ch.11 coverage-gap expansion — landed on main 2026-06-20** (binate `1fa326ef`;
chapter now **63 tests**, still 25/25 rules, green on 7 modes). Authored via a 2nd
design fan-out + empirical probing of every edge.
The review's deeper sub-clause gaps are now covered:
- ✅ `iface.decl`: 016 dup method, 017 no `type X interface{}`, 018 no method body.
- ✅ `iface.impl.coverage`: 028 receiver-kind unreachable; 027 `@readonly` receiver.
- ✅ `iface.extend`: 049 harmless diamond; 038 transitive (A:B,B:A) cycle (surfaces
  as `undefined: B` / `extension target must be an interface` — forward-ref, not a
  dedicated cycle message, but correctly rejected).
- ✅ `iface.any`: 053 `*any` ≠ `*uint8`; 054 `any` generic-constraint position.
- ✅ `iface.crosspkg.no-orphan`: 084 duplicate-impl weak_odr dedup (multi-package).
- ✅ `iface.self`: 065 Self in struct field, 066 Self in extension-parent list;
  068 object-safety positive companion.
- ✅ `iface.alias`: 056 `type X = Interface` rejected.
- ✅ `iface.canonical.carveout`: 057 method-on-primitive; 083 sized-int + float;
  085 (xfail) the NEW MINOR impl-pass carve-out gap (see below).
- ✅ `iface.extend.transitive` managed half: 039 (xfail, companion to 048's raw).
- `iface.value.repr` clean same-kind positive: done in the review fix (012 rewrite).
  `iface.value.no-readonly-slot` inner-vs-outer: adequately covered by 014 (outer
  `readonly @Iface` dispatches) + 015 (inner `*readonly Iface` rejected); the inner
  rejection is not separately observable from the bare-name error, so no dedicated
  test (the only intentionally-skipped item).

**NEW MINOR bug surfaced by the expansion** (claude-todo, 2026-06-20): the §11.10
primitive-impl carve-out is enforced only in the method-declaration pass, so a
non-lang `impl <primitive> : <empty interface>` is wrongly accepted. Pinned by
`085` (xfail). Needs a fix decision (low priority).

**Ch.10 Functions/methods/function-values — landed on main 2026-06-20** (binate
`9e2aa365`; `conformance/spec/10-functions/`). Authored via an 8-cluster
design fan-out + central empirical probing + an 8-cluster adversarial review +
fixes. **77 tests**; coverage **10 → 21/21 (100%)**, **10b → 19/20 (95%)** — the
lone gap is `func.closure.escape-lint`, a bnlint WARNING the bnc-based conformance
harness fundamentally cannot test (could be reclassified `untestable` to make it
19/19 — a denominator-changing extract-rule-ids tweak, flagged not done). Green on
7 modes (builder-comp, VM int, int-int, gen1, gen2, native_aa64, arm32_baremetal);
DANGLING=0, UNTAGGED=0, hygiene 15/15. 3 xfails.

**Bugs / stale notes surfaced (all tracked):**
- **MAJOR (NEW, claude-todo 2026-06-20):** a method EXPRESSION over a named SCALAR
  type (`Celsius.M`) miscompiles — direct call emits undefined symbol `@bn_T__M`;
  the `*func` form compiles but SIGSEGVs. Fails on compiled AND VM. Pinned by
  `132` (xfail.all).
- **Stale spec note §10.3** (empty-param arity "known defect"): FIXED (conformance
  /741; restricted to the variadic builtins). `032` is green; the doc note should
  be corrected (FLAGGED — not yet edited).
- **Stale spec note §10.12** (value-receiver method value "fails on 32-bit ARM"):
  `138` XPASSes on arm32_baremetal — the arm32 codegen handles it now; born-stale
  xfail markers removed (the review re-flagged this purely from the stale note).
- **Diagnostic-quality (minor):** function-value types render as `<unknown>` in
  assignability errors (086/089/094 patterns can only anchor `cannot assign ... to
  Fn`, not the raw/managed KIND). Worth a fix; not blocking.
- Known gaps pinned by xfail: `068` (value-receiver bodies not checked read-only,
  §10.6 known gap), `102` (named func-value type from a literal rejected, §10.9).

**Ch.10 review-driven coverage-gap follow-ups (deeper sub-clauses, NOT authored):**
func.return.missing positive (the 6 terminator forms accepted); func.return.stmt
result-assignability negative; func.destructure into `s[i]`/`s.field` targets +
target/result-count negative; func.return.tail negative (mismatched tuple);
func.method.smoothing accepted `*T->*readonly T` + the mutable->readonly object-
const "adding" direction; func.method.receiver-base anonymous-type + lang-carveout
cases; func.method.auto-deref through-alias/readonly; func.dispatch.vtable managed
`@Iface`; func.dispatch.routing pkg-alias-head; func.value.identity cross-signature
rejection; func.ref.decay into alias/readonly dests; func.value.named-nominal from
a method-expression (negative); func.closure inner-write isolation + no-auto-promote;
method-EXPRESSION over a `@T` receiver + multi-param signatures; func.value.dual-mode
genuine single-run mixed compiled<->interpreted (harness can't pin per-fn modes —
currently covered only by cross-mode agreement); func.value.equality on `@func` /
non-nil non-func operand.

**Ch.7 Types — landed on main 2026-06-20** (binate `f5fa834b`; the LARGEST
chapter, `conformance/spec/07-types/`). 10-cluster design fan-out + extensive
empirical probing + 10-cluster adversarial review + fixes. **136 tests**; coverage
**07-types 61/61 (100%)** + **07b-type-layout 14/14 (100%)**; DANGLING=0, UNTAGGED=0,
hygiene 15/15. Green on all modes: 3 xfails on builder-comp/VM/int-int/gen1/gen2/
arm32_baremetal, 5 on native_aa64. Layout tests are TARGET-INVARIANT (relationships
+ fixed sizes, single .expected; no per-arch files).

**4 bugs filed (claude-todo 2026-06-20), each xfail-pinned:** (1) MAJOR cross-pkg
distinct named SCALAR types wrongly inter-assign (`049`); (2) MAJOR `type Buf @[]int`
miscompiles on native-aarch64 ONLY (`033`/`036`); (3) MINOR opaque field-access not
rejected cross-pkg (`222`); (4) MINOR `@([N]T)` managed-ptr-to-array indexing broken
(noted, not pinned). Plus a stale §7.8 note — the `@[N]T` parser leniency is FIXED
(binate `7ccd13e1`); `150` pins the green rejection — FLAGGED for doc correction.

**Adversarial review applied** (6 critical, 10 major fixed). The critical class was
VACUOUS NEGATIVES whose `.error` pattern leaked via the test's own FILENAME in the
diagnostic path (e.g. `bool` matched `…/021_err_arith_on_bool.bn`) or matched only a
cascade — a systematic scan caught all of them (021/022/031 filename-leak; 024/043/
044/085/096/165/196 loose/cascade), now pinned to the real rule-relevant diagnostics;
124 managed-iface nil line added; comments fixed where they contradicted the files
(096/224); mis-cites re-homed (273/251/175); 275 differentiated to int64 byte-order.

**Ch.7 review-driven follow-ups (NOT done — 17 minor + 21 nit + 54 coverage-gap
notes in the review output `w06h0q1xu`):** notably — no-implicit-mix bitwise/relational
forms; bool required-operand-of-&&/||/! negatives; type.scalar.universe "true/false/nil
are constants not types"; copy-semantics recursion through array/named/readonly; the
`<unknown>` func-value-type rendering in assignability diagnostics (diagnostic-quality,
also seen in Ch.10); 088 slice-ownership best-effort UAF detection; 251 header is
informative/unobservable; plus per-rule sub-clause breadth across every cluster.

**Ch.12 Generics + Enumerations — landed on main 2026-06-20** (binate `b8a5100a`;
`conformance/spec/12-generics/`). 3-cluster design fan-out + empirical probing +
adversarial review. **20 tests, 12/12 rules (100%)**, green on all 7 modes;
DANGLING=0, UNTAGGED=0, hygiene 15/15. The cleanest run yet (18/19 first-try). 1
xfail (`034`) pins the open §12.4 gap: generic struct/interface constraint
satisfaction unchecked (`gen.satisfy.struct-iface-unchecked`; generic-function
case `033` is green). Review added `037` (the no-conditional-impls prohibition —
`impl[T] Box[T] : Show` parse-errors). Stale §12.1 note corrected — the
generic-method gap is FIXED (`a7e0beb2`); `014` pins the rejection. (Docs
`123bffa`.) Coverage-gap follow-ups (user-named-interface constraint positive;
type-arg arity mismatch; full method-set satisfaction; generic-interface .bni
body; enum→other-int conversion) noted in the review output `wd4ivz2ob`.

**Ch.5 Lexical Elements — landed on main 2026-06-21** (binate `dcdc6b82`;
`conformance/spec/05-lexical/`). 7-cluster design fan-out (every test probed
against the live compiler) + a 7-cluster adversarial review (1 major + 18
minor/nit, all resolved: 4 redundant tests dropped, 4 `.rules` re-homed, 151
strengthened to byte-level, comments softened). **115 tests**, all **29 `lex.*`
rules → 29/29 (100%)**; DANGLING=0, UNTAGGED=0, hygiene 15/15. Green on all 7
modes (builder-comp, VM int/int-int, gen1, gen2, native_aa64, arm32_baremetal);
lexical behavior is backend-independent and floats work everywhere, so no
mode-specific xfails. Byte-level tests (NUL=EOF `180`, form-feed-not-whitespace
`181`, non-ASCII-byte-illegal `182`) authored via exact source bytes.

5 xfails. **Two NEW spec/impl DIVERGENCES → new file `explorations/spec-todo.md`**
(decide spec-vs-impl): `055` — `\uHHHH` Unicode escape IS implemented (UTF-8
encodes the codepoint) though §5.11 says it doesn't exist; `035` — `1.foo` lexes
as a trailing-dot float, not the §5.8 selector tokens `1 . foo` (greedy lex, Go
agrees with the impl). The other 3 are settled-intent impl gaps: `056`/`057` pin
the `lex.literal.char.one` open items (`''`→0x00, `'ab'`→truncated, undiagnosed);
`122` reuses the inferred-length-array gap (same as 13-expr/041). **Stale §5.11
note corrected** — unsupported escapes ARE rejected (`unknown escape sequence`),
not silently accepted (docs `ac62326`); negatives `047`–`054` pin it green. Minor
unary-`+`-rejected question also in spec-todo. **Review-driven follow-up landed**
(binate `5f228cda`; chapter now **120 tests**): `183`–`185` `true`/`false`/`nil`-
as-keyword negatives (vs the shadowable predeclared names), `186` carriage-return
(0x0D) is whitespace, `187` `return` triggers ASI.

**Ch.14 + 14b Statements & Control Flow — landed on main 2026-06-21** (binate
`84b0b4a4`; `conformance/spec/14-statements/`). 5-cluster design fan-out (every test
probed) + 5-cluster adversarial review (1 major-class new-defect found + 17 minor/nit;
fixes: 2 redundant tests dropped, `092` strengthened with an iteration counter,
`121` re-cited, `122`/`130` differentiated, `153` comment reworded; the
const-target/expr.unused sub-ID "miscites" correctly REJECTED as undeclared — citing
them would DANGLE). **78 tests**, all **34 `stmt.*` rules → 34/34 (100%)** (17/17 +
17/17); DANGLING=0, UNTAGGED=0, hygiene 15/15. Green on all 7 modes (each verified
individually with `--check-xpass`).

**Bugs surfaced (filed in claude-todo, pinned):**
- **CRITICAL** — a **tagless** `switch { … }` SIGSEGVs the compiler (null-deref in
  `ir.genExprInner` on the absent tag); the documented if/else-if replacement, with
  ZERO prior coverage. Pinned `121_switch_tagless_xfail` (xfail.all — the crash is in
  the IR-gen shared by the compiler AND the VM). User: pin + fix as a follow-up.
- **MAJOR** — a `switch` on a **sub-64-bit integer tag** (char/int8/16/32, uint8) with
  an **untyped-int-literal case** emits invalid IR (literal stays `i64` vs `icmp eq iN`)
  → LLVM/clang and arm32 fail; the bytecode VM, native aarch64, and int/int64 tags
  handle it. Pinned `134_..._xfail` (per-mode xfail: builder-comp, -comp-comp,
  -comp-comp-comp, arm32_baremetal — verified each mode).
- **Stale §14.5 note corrected** — `a[i]++`/`p.f++`/`(*p)++` (old MAJOR no-op defect)
  were FIXED+LANDED (`6a2f551f`); dropped the §14.5 open-note + status flag (docs
  `5e2d8ce`).

Open-item behaviors documented as positives: tagless-switch `break` targets the
enclosing loop (`133`), a bare effect-free `x + 1` is accepted (`036`). **Review-driven
follow-up landed** (binate `430a46b1`; chapter now **84 tests**): `046` string-literal
`:=` default type, `047` `s[i]++` managed-slice incdec, `097` non-bool for-condition
rejected, `098` raw-slice range, `165` block-last-terminates, `166` no `select` absence.

Next chapter (bulk Phase B) is the workflow-fan-out target, using Ch.13 as the
worked template.

## 10. Appendix — example spec tests

**Positive** (`conformance/spec/13-expressions/NNN_arith_divzero.bn` + `.expected`):
```
// .rules:  expr.arith.divzero
package "main"
func main() { var a int = 1; var b int = 0; println(a / b) }   // aborts
// .expected: (empty stdout) + nonzero exit + "runtime error: integer divide by zero" on the panic stream
```
(Realized as a `.error`-style abort test per the harness's panic convention.)

**Negative / Constraint** (`.../16-packages/NNN_alias_vs_decl/` + `.error`,
**xfail** — BUG D):
```
// .rules:  pkg.import   (alias-vs-decl redeclaration — currently undiagnosed)
// a.bn:  package "pkg/x"; import p "pkg/p1"; func p() int { return 0 }
// .error: p redeclared
// .xfail.all: alias-vs-decl collision not diagnosed (claude-todo: import CRITICAL, facet D)
```

**xfail miscompile** (`.../16-packages/NNN_import_implicit_collide/` — BUG C):
```
// .rules:  pkg.import
// a.bn: import "pkg/alpha/widget"; func A() int { return widget.Code() }   // →100
// b.bn: import "pkg/beta/widget";  func B() int { return widget.Code() }   // →200
// main.bn: println(x.A()); println(x.B())
// .expected: 100\n200
// .xfail.all: package-scoped imports collapse same-last-segment across files (claude-todo: import CRITICAL, facet C)
```

The xfail reason text is the claude-todo cross-reference, so when the import fix
lands the test goes green and Annex C flips to conformant **automatically**.
