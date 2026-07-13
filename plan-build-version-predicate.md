# Plan: `#[build]` compiler-version predicate (`at_least` / `at_most` / `is(version, …)`)

Status: **PLANNED** (design ratified 2026-07-13; not yet implemented).

## Motivation

The immediate driver is retiring `runtime/binate_runtime.c`'s `main` in favour of
a Binate hosted entry in `pkg/builtins/startup` (design-ffi-export.md §3.3, Phase
6). Moving `main` is a bootstrap problem: **the BUILDER stage links the BUILDER
*bundle*'s frozen `binate_runtime.c` — which still defines `main` — not the
tree's.** Verified:

- `scripts/build-bnc.sh:94,124` — Stage 1 (BUILDER→gen1) uses `--runtime
  "$BUILDER_RUNTIME"`, resolved from `$BUILDER_LIB` (the bundle).
- `scripts/lib/build-compilers.sh:95` — the gen1 build passes `--runtime … --base
  "$blib"` (bundle). Only later stages (gen1→gen2, line 112) use the tree's
  runtime (`--base "$BINATE_DIR"`).

So deleting the tree's `.c` main does **not** remove the frozen bundle's `main`;
a startup `main` would collide with it in Stage 1. A frozen bundle can't be
edited, so the fix must make startup's `main` *conditional on the compiling
compiler*:

- Gate: `startup.main` included **iff compiling-compiler version > BUILDER's
  version**.
- BUILDER builds gen1 (version == BUILDER's): gate false → `main` comes from the
  bundle's `.c`; no startup `main`; no duplicate.
- gen1 (version > BUILDER's) compiles programs / gen2: gate true → `main` from
  startup; the *tree*'s `.c` has no `main`; no duplicate.

This needs a **compiler-version predicate in `#[build]`**, which today does not
exist. This plan adds it. (The `#[c_export]` alias is strong/external —
`emit.bn:398`, no linkage keyword — which is why the *weak-`main`* alternative
would also work, but the version predicate is a reusable feature with no
linker-portability caveats, and the buildcfg author already reserved space for it
— `buildcfg.bn:173`: "ordered matchers (at_least / at_most / …) for versions are
added when ordered predicates land." So: version predicate, not weak `main`.)

## Design (ratified)

The `#[build]` grammar already has `&&`, `||`, and unary `!`
(`buildcfg.bn` `EvalBuildExpr`); bare comparison operators (`>`/`<`) are
deliberately rejected. Predicates are `name(key, "tag")` calls. Extensions:

- **`at_least(version, "X.Y.Z")`** → compiling version ≥ X.Y.Z (ordered).
- **`at_most(version, "X.Y.Z")`** → compiling version ≤ X.Y.Z (ordered).
- **`is(version, "X.Y.Z")`** → exact equality (extends the existing `is`, which
  is exact for `arch`/`os`, to a third key).
- Inverses come from the existing `!`: `!at_least` = `<`, `!at_most` = `>`,
  `!is(version,…)` = `≠`. So all six relations from two new predicate names +
  `is` + `!`. **No** separate `gt`/`lt`/`eq`/`ne` (redundant with `!`, more
  surface, terser names read worse than `at_least`/`at_most`).

Key-first, always (`at_least(version, …)`, matching `is(arch, …)`). **No**
argument-swap form (`at_least("X.Y.Z", version)`) — it breaks the key-first
convention and forces the validator to accept `(key,lit)` and `(lit,key)`.

`at_least`/`at_most` apply only to the `version` key (arch/os aren't ordered);
`at_least(arch, …)` is a hard error.

### Version string format + comparator

**Strict format: `X.Y.Z[pre[N]]`** — exactly three dot-separated digit-runs,
optionally followed by `pre` optionally followed by digits. Anything else is a
hard error (no best-effort parse):

- Valid: `0.0.11`, `0.0.11pre`, `0.0.11pre3`.
- Rejected: `0.0`, `0.0.11.4` (4th component — never silently dropped),
  `0.0.11-rc1`, `0.0.11beta`, `1.2.x`, `v1.2.3`, empty.
- Defensive: tolerate/strip a leading `bnc-` (the `BUILDER_VERSION` file carries
  it; `version.Version` does not — the gate compares `version.Version` against a
  bare literal, so no prefix is in play, but a stray prefixed literal must not
  silently mis-parse to `0.0.0`).

**Comparison:** strip the `pre[N]` suffix (so `X.Y.Zpre[N]` == `X.Y.Z`), then
compare `(X, Y, Z)` **numerically** (major, then minor, then patch — `0.0.11` >
`0.0.9`, not lexical). Rationale for pre-stripping: a prerelease of X.Y.Z is
developing *toward* X.Y.Z's behaviour, so it should gate like X.Y.Z; and it
avoids the gotcha that the tree's `0.0.11pre3` would otherwise fall *below* an
`at_least("0.0.11")` gate. The cost — the comparator can't distinguish a release
from its prereleases — is a non-need for bootstrap staging (new gate boundaries
are made by bumping X.Y.Z, the normal workflow). Diverges from semver (which
orders prerelease identifiers); documented as a deliberate bespoke rule.

### `BuildConfig.Version`

Add a `Version` field to `BuildConfig` carrying the **compiling compiler's own**
version string (`version.Version`, currently `0.0.11pre3`), set in `HostConfig()`
/ `ConfigForTarget()` / `mkConfig()`. Consistent with how `Arch`/`Os` are stored
as raw strings and matched at eval time (`archMatches`/`osMatches`). The version
comparator parses both the config version and the literal at eval time; a
malformed *literal* is a build-constraint error, a malformed *config* version is
an internal invariant (should never happen — `version.Version` is project-owned —
but error loudly rather than defaulting to `0.0.0`).

Open sub-decision: does `buildcfg` import `pkg/binate/version` directly (clean —
version is a leaf const package, BUILDER-compilable), or does the driver inject
the string into the config? Lean: `buildcfg` imports `version`.

## Implementation (first bump — predicate machinery ONLY, no main-move)

Files (all in `cmd/bnc`'s BUILDER-compiled tree — stays BUILDER-safe: this adds
*code that evaluates* the predicate, not new syntax BUILDER must parse):

- `pkg/binate/buildcfg/buildcfg.bn`:
  - `BuildConfig.Version` field; set it in `HostConfig`/`ConfigForTarget`/
    `mkConfig` from `version.Version`.
  - `evalCall`: recognize `at_least` / `at_most` (route to the version
    comparator); keep `is` (extended for the `version` key).
  - `evalIs`: handle the `version` key (exact equality via the comparator).
  - New: a strict `X.Y.Z[pre[N]]` parser + numeric comparator + the
    error diagnostics (malformed version literal; ordered predicate on a
    non-`version` key).
- `pkg/binate/buildcfg/buildcfg_test.bn`: parser (valid/invalid forms),
  comparator (ordering incl. multi-digit + pre-stripping), each predicate
  (`at_least`/`at_most`/`is(version,…)`) with `!`, and the hard-error cases.

**BUILDER constraint (mirrors `#[c_export]`):** the *current* BUILDER
(`bnc-0.0.10`) does not understand `at_least`, so **no `#[build(at_least(…))]`
may appear in `cmd/bnc`'s own BUILDER-compiled tree** until BUILDER is re-pinned
to a version that supports it — otherwise BUILDER's loader hits
`unknownPredicate` gating cmd/bnc's files and the gen1 build breaks. The first
bump only *adds the machinery*; it does not *use* the predicate in the bnc tree.
(Conformance/e2e programs, compiled by gen1, may use it.)

## How it gets used later (the main move — NOT this bump)

Separate future effort, gated behind a BUILDER re-pin:

1. **This plan's bump:** land the predicate + `BuildConfig.Version`. Re-pin
   BUILDER so the pinned BUILDER understands `at_least`.
2. **Main-move bump:** bump the tree version to the threshold (e.g. re-pinned
   BUILDER is `0.0.11` → bump tree to `0.0.12`), add
   `#[build(at_least(version, "0.0.12"))]` on `startup`'s `#[c_export("main")]`
   entry, and delete the tree's `binate_runtime.c` `main`. BUILDER (`0.0.11`) →
   gate false → bundle `.c` main; gen1 (`0.0.12`, or `0.0.12pre*` — same under
   pre-stripping) → gate true → startup main. Re-pin again; thereafter the gate
   is vestigially-true and the C `main` is gone from bundle and tree.

Off-by-one to watch: the threshold must be strictly above the re-pinned BUILDER's
(stripped) version and at-or-below the tree's — pick threshold = the version the
tree is bumped to at the main-move bump.

## Decisions settled / alternatives rejected

- **Weak `main`** (make the C `main` weak so a strong startup `main` overrides
  it): works (the c_export alias is strong), but relies on weak-symbol linker
  semantics (finicky on macOS/Mach-O) and is a one-off hack. Rejected in favour
  of the reusable version predicate.
- **Argument-swap `at_least("X.Y.Z", version)`** for the other direction:
  rejected (breaks key-first; ambiguous both-literal cases). Use `at_most` +
  `!`.
- **`gt`/`lt`/`eq`/`ne` predicate set:** rejected (redundant with `!`; terser
  names read worse). `at_least`/`at_most`/`is(version,…)` + `!` cover all six.
- **Semver prerelease ordering** (`pre < pre1 < … < release`): rejected in favour
  of pre-stripping (`X.Y.Zpre[N]` == `X.Y.Z`) — simpler comparator, and gates a
  prerelease like the release it targets.
- **Strict version format `X.Y.Z[pre[N]]`, error otherwise:** ratified (no
  best-effort parse; a 4th numeric component or unknown tag is an error).
