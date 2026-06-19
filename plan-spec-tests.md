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

Two user-owned follow-on decisions (NOT done — scope guard, CLAUDE.md "Stay
Within the Asked Scope"): (a) promote `conformance/spec/` to the default
conformance suite + CI; (b) wire `extract-rule-ids.py` / `spec-coverage` into CI.
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
