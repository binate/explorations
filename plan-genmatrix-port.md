# Plan: port the conformance matrix generators to Binate (dogfood)

Rewrite the `conformance/gen-*.py` test-matrix generators as a self-hosted
Binate tool, retiring the Python. Every generator's docstring already flags
this as the intended end state ("intended to port to Binate (dogfood) later").

**Strategy: incremental (prove-then-decide), converging on full dogfood.**
Land the shared library plus one generator end-to-end, gate every port behind a
byte-identical diff against the committed corpus, then work through the tiers.
No big-bang cutover — the generated output is committed, so Python and Binate
generators can coexist per-matrix until each is proven and its `.py` deleted.

## Background

15 standalone Python scripts (`conformance/gen-*.py`, ~4,270 LOC) emit the
committed `conformance/matrix/` corpus — ~1,527 generated
`.bn`/`.expected`/`.expected.<mode>`/`.error`/fixture-`.bni` files (the 16
`README.md` files under `matrix/` are hand-written and NOT generated). Output is
**coordinate-addressed**: a cell's path IS its identity
(`matrix/<class>/<axes…>.bn`), so regeneration is idempotent — the same
coordinates map to the same path, and `.xfail.<mode>` markers (hand-maintained,
determined by running) are never written or deleted by the generators.

The generators are wired into **nothing**: `conformance/run.sh` discovers the
cells as ordinary tests (`find matrix -name '*.bn'`), and no hygiene check or CI
workflow invokes any generator. They run by hand when someone deliberately
regenerates (adds an axis value, edits a template, fixes an oracle). This is
what makes the incremental strategy safe: the committed corpus validates the
toolchain regardless of generator health, and a half-ported generator set breaks
nothing.

The dominant activity is **source-text templating, not computation**: 11 of 15
have no numeric oracle (expected is a column of `1`s from self-checking
`cast(int, access == lit)`, echoed literals, or a fixed list). Only 4 compute
values.

## Supply already in place

- **File I/O**: `pkg/std/os` — `Create`/`OpenFile`/`Write`/`ReadDir`/`Stat`/
  `Remove`/`Rename`. `bnfmt` already reads a source tree and writes files back
  (atomic write-to-temp-then-`Rename`), i.e. structurally a generator.
- **Formatting**: `strconv` — `Itoa`/`FormatInt`/`FormatUint`/`FormatFloat`
  (Dragon4 shortest-decimal), `Append*` variants.
- **Accumulation**: `strings.Builder` (io.Writer, exponential growth). Binate
  has no string type; text is `@[]char`/`*[]char`, and all emission is Builder +
  literals + `strconv`.
- **argv**: `os.Args()` (for `--check`).
- **Host-tool precedent**: `bnlint`, `bnas`, `bnfmt` are Binate tools built
  BUILDER → gen1 → final via `scripts/build-*.sh`. They are built by *current-
  source* gen1 (not the BUILDER directly), so — unlike `cmd/bnc`'s tree — they
  may use the **full language** (closures, generics, interfaces). The generator
  tool inherits this: the Tier-3 closure/dispatch-table generators need it.

## External dependencies (assumed landing separately)

- **`os.Mkdir` / `os.MkdirAll`** — currently absent everywhere (zero hits). The
  generators materialize nested coordinate dirs
  (`matrix/scalar/<op>/<width>/<sign>/`); `os.Create` uses `O_CREAT` (the file,
  not parents). This is being implemented independently. This plan assumes a
  `MkdirAll(path, perm) @errors.Error`-shaped primitive lands in `pkg/std/os`;
  Phase 0 adjusts to its actual signature.
- **A CHECK_TOOLS bundle whose `bni` ships `os.MkdirAll`.** The generator runs
  under the bundled `bni`, which injects the *bundle's* `os` (see Run mode), so
  `CHECK_TOOLS_VERSION` must be bumped to include `MkdirAll` after it lands.
  Until then, a from-tree `bni` (`scripts/build-bni.sh`) is the interim runner.

## Architecture

- **`pkg/conformance/gen/`** (new, repo-root tier alongside `pkg/binate/`) — the
  shared `genlib`, factoring what all 15 Python scripts duplicate today (there is
  no shared Python module; `main()`, `--check`, write-if-changed, `cast_int`,
  `_trunc_div`, `_sync` are copy-pasted). Contents:
  - **fs/driver**: `mkdirAll`, read-file, write-if-changed, `--check`
    compare-only, per-mode `.expected.<mode>` write + stale-delete
    (`os.Remove`). One code path, exercised by every generator.
  - **emit**: a small templating layer over `strings.Builder` (a `sprintf`-ish
    helper + literal/`strconv` glue). There is no `fmt` package; this is the
    largest *volume* item but has no language obstacle.
  - **path/string helpers**: `strings` today is just `Builder` — add the handful
    actually used (`join`, `hasPrefix`, `hasSuffix`, `repeat`, path-join). Keep
    these local to genlib unless they clearly belong in stdlib `strings`/`path`
    (a separate decision; do not grow stdlib as a side effect).
  - **int oracle**: `castInt` (sign/zero-extend + truncate at a width), two's-
    complement wrap, truncating div/rem, logical/arith shift — all exact in
    `int64`/`uint64`. One guard: the full-width `w == 64` case where `2^64`
    appears as a modulus/range overflows `uint64` and must be special-cased as
    identity.
  - **float rendering** (Phase 2): a `repr`-emulation formatter. `FormatFloat`
    alone does NOT reproduce the corpus byte-for-byte — it drops the trailing
    `.0` on integer-valued floats (`100.0` → `"100"`), but Binate float literals
    require the decimal point. Need shortest-digits + conditional `.0`.
- **`cmd/genmatrix/`** (new) — thin tool: a registry of generators; no args =
  regenerate all, `--check` = fail if any would change, `<name>` = one
  generator. Mirrors `cmd/bnfmt` structure.
- **`scripts/run-genmatrix.sh`** (new, convenience) — resolves the bundled `bni`
  (`fetch-builder.sh --tool bni --check-tools`) and runs `bni cmd/genmatrix` with
  the standard `-I`/`-L` search paths (as the e2e harness does), so a regenerate
  is one command with no build. A *compiled* `scripts/build-genmatrix.sh`
  (BUILDER → gen1 → final, like `build-bnfmt.sh`, `-o <path>` + `mktemp -d`
  scratch) stays an optional later speed optimization — not required (see Run
  mode).

*(Naming `pkg/conformance/gen` + `cmd/genmatrix` is a proposal; adjust if a
different home is preferred. It is a new top-level under the root impl tier, not
a `pkg/binate` compiler package nor a `pkg/std` stdlib package.)*

### Run mode: the bundled (CHECK_TOOLS) `bni`

Run the generator under the **prebuilt bundled `bni`** — not a self-built one,
not a compiled generator binary. `scripts/fetch-builder.sh --tool bni
--check-tools` resolves the pinned CHECK_TOOLS bundle's `bni` (the release
tarball ships `bin/{bnc,bni,bnas,bnlint,bnfmt}`; `--check-tools` selects
CHECK_TOOLS, the newer pinned set that "may be a pre-release ahead of the
BUILDER" — the same fetch path hygiene uses for its pinned `bnlint`/`bnfmt`). So
there is **no build step at all**: fetch the pinned interpreter and run
`bni cmd/genmatrix -- <args>` (bni splits program args at `--` and installs them
via `os.SetArgs`).

File I/O works despite the VM doing no FFI (`__c_call` is never lowered —
`pkg/binate/vm/lower.bn`): `bni` **injects the native `os`** into the interpreted
program (`pkg/binate/interp/externs.bn` — `RegisterPackageFunctions` over
`os.__Package`, so every `os` method runs as bni's linked native impl). Real
files get written — the same mechanism already lets `bni`-run programs use
`os.Args`/`os.ReadDir` (the `os-args` / `os-env` / `readdir-values` e2e tests).

This makes the "must run even when the toolchain is broken" property concrete
and strong — better than compiled, not a compromise: the generator runs on a
**pinned, known-good interpreter fully decoupled from the current source tree**.
A codegen/native-backend regression in the tree cannot touch it — the
generator's logic is interpreted bytecode, and even the `bni` binary and its
native `os` come from the pinned bundle, not from a build of the (possibly
broken) current source. The "a cold rebuild of `bni` needs the backend" caveat
evaporates entirely: you never build `bni`, you fetch it. A **compiled**
generator (emitted through gen1's codegen) would instead risk baking a codegen
bug straight into the tests meant to catch it.

**The one real constraint — the injected `os` is the *bundle's* `os`.** Because
the VM does no FFI, `bni` services the generator's `os` calls with the native
`os` compiled into *that* bni binary, i.e. the CHECK_TOOLS bundle's snapshot. So
the generator may only use `os`/stdlib functions present in that bundle — in
particular **`os.MkdirAll` must be in the CHECK_TOOLS bundle**. Once `MkdirAll`
lands in the tree, `CHECK_TOOLS_VERSION` must be bumped to a bundle that ships
it before the generator can run bundled. This is the injected-`os` analogue of
the standing "verify the BUILDER supports a new feature before relying on it"
rule (see plan-check-tools-version.md). Interim, before that bump, a from-tree
`bni` (`scripts/build-bni.sh`, whose injected `os` is the tree's) runs it — the
same fallback if a generator ever needs an `os` function newer than the latest
CHECK_TOOLS.

## Verification discipline (the core of the incremental strategy)

For every generator ported, before deleting its `.py`:

1. Run the **Python** generator to a scratch tree (or trust the committed
   corpus, which the Python produced).
2. Run **`genmatrix <name>`** to a separate scratch tree.
3. Assert **byte-identical** against the committed corpus (equivalently:
   `genmatrix --check` is clean on a tree the Python last wrote). Any diverging
   cell is a scope item — fix the emitter, never silently re-baseline.
4. Only when byte-identical: delete the `conformance/gen-<name>.py`.

**Caveat — the float generators break naive byte-identity until Phase 2+.**
`gen-diff-scalar`, `gen-aggregate`, and `gen-abi` emit float literals; byte-
identical output needs the Phase-2 `repr`-emulation formatter. Separately,
`gen-diff-scalar`'s committed corpus already contains `18446744073709551616.0`
(2^64) and 2^65 as *decimal* literals — above `uint64`, so `FormatUint` cannot
render them. Sequence the float-emitting generators after the float formatter
lands, and treat 2^64/2^65 as an explicit Phase-5 decision (wide-int→decimal
rendering vs. a conscious re-baseline of those cells — user's call, with eyes
open).

## Phases

Ordered so each phase leaves the tree green and lands a self-contained,
cherry-pickable increment. Nothing here wires `genmatrix` into
`run.sh`/hygiene/CI — that is a separate new-automation decision (see Open
decisions).

### Phase 0 — foundation + one generator end-to-end
- `pkg/conformance/gen` skeleton: fs/driver + emit + int-oracle helpers (no
  float rendering yet).
- `cmd/genmatrix` skeleton + registry + `--check`.
- `scripts/run-genmatrix.sh` (the bundled-`bni` wrapper).
- The byte-diff verification harness (a script that runs `genmatrix` under `bni`
  to a scratch dir and diffs against committed `matrix/`).
- Port **one** easy generator end-to-end to validate the whole pipeline. Use
  `gen-nested-index-matrix.py` (119 LOC, smallest, static table, no oracle).
- Depends on `os.MkdirAll` in the tree, and — to run bundled — a CHECK_TOOLS
  bundle that ships it (else use a from-tree `bni` interim; see Run mode).

### Phase 1 — Tier 1: the remaining easy generators (5)
Static tables, hardcoded/self-checking expected, no maps, no oracle, no
closures. Each: port → byte-diff green → delete `.py`.
- `gen-dispatch-refcount` (198), `gen-globals` (167), `gen-readonly` (164),
  `gen-shift-typepair` (162), `gen-loop-leak` (154).

### Phase 2 — float-rendering primitive
Add the `repr`-emulation float formatter (shortest digits + conditional `.0`)
to genlib, with tests. Prerequisite for every float-emitting generator (Tiers
2 and 4). Isolated so its correctness is proven before any float cell depends
on it.

### Phase 3 — Tier 2: moderate generators (5)
Int oracle + float literals + multi-kind output.
- `gen-scalar` (274) and `gen-operator` (277) — share `castInt`; emit
  `.expected` + ILP32 `.expected.<mode>` overrides (with stale-delete) +
  (operator) `.error` compile-error cells. Resolve the `w == 64` / `2^64`
  identity guard here.
- `gen-abi` (516), `gen-aggregate` (266), `gen-addr-aggregate` (172) —
  tautological/self-checking expected; function-valued form tables (full-
  language function values / closures are fine — these are outside the BUILDER
  cone); `isinstance(int/float)` rendering branch → an explicit kind tag on the
  value tables.

### Phase 4 — Tier 3: structural generators (2)
No hard oracle; hard because of Python first-class-function tables and multi-
file fixtures.
- `gen-matrix` (488) — per-type `construct`/`fresh`/`use` builders (Python
  lambdas capturing locals) → `*func`/closures or named functions + explicit
  state; `(form, shape)` 2-tuple-keyed dispatch dict → slice-of-struct + linear
  scan.
- `gen-generic-managed` (299) — heterogeneous cell records with optional fields
  (`dict.get`) → structs with present-flags; cross-package fixture **trees**
  (`main.bn` + `pkg/gh.bni` + sentinel `pkg/gh/gh.bn`) → multi-file emission.

### Phase 5 — Tier 4: the oracle walls (2)
- `gen-diff-scalar` (766) — large but the oracle is fixed-width: two's-
  complement wrapping ints (exact in `uint64`), floats that are exact binary
  fractions / powers of two / inf-NaN-`-0` built from bit patterns, an FNV-1a-
  seeded LCG PRNG (uint64 wrapping — reproduces bit-for-bit). Port the `Cell`
  accumulator and PRNG as structs+methods. **Open item**: the 2^64/2^65 decimal
  literals (see Verification caveat) — decide wide-int rendering vs. re-baseline.
- `gen-const` (248) — the only true bignum wall. `bit_pattern()` converts an
  adversarial decimal literal to its exact IEEE-754 bits with round-to-nearest-
  even (e.g. float64 `1.0000000000000001110223024625156540424` = exactly
  `1 + 2^-53`, the tie resolved to `1.0` only under round-to-even). Correctly
  rounding a general decimal string is the strtod/Clinger/Gay problem — needs
  arbitrary-precision comparison; Binate has no bignum and nothing wider than
  f64. **Isolation**: only ~4 of the `VALUES` rows are floats needing correct-
  rounding (`0.5` is exact; the 7 integer rows fit `uint64` and port normally).
  Bake those ~4 correctly-rounded bit patterns as a precomputed static table —
  computed when a literal is *added*, not on every run. For a rarely-changing
  adversarial list this is honest, not a cheat.

### Phase 6 — reach full dogfood (A)
- With all 15 ported and byte-diff green, delete all remaining
  `conformance/gen-*.py`.
- Decide whether to add a hygiene/CI `--check` staleness gate (the Python
  supported `--check` but was never gated in this repo; `docs/scripts/
  gen-annex-a.py` IS gated in the docs repo via `spec-sync.yml`). This is a
  separate new-automation decision — do not add it as a side effect of the port.

## Invariants (all phases)

- Coordinate-addressed paths preserved exactly (path = identity → idempotent,
  never touches `.xfail.<mode>`).
- Generator writes only `.bn` / `.expected` / `.expected.<mode>` / `.error` /
  fixture `.bni`. Never `.xfail`, never the hand-written `README.md`s.
- Every port is byte-identical to the committed corpus before its `.py` is
  deleted; a divergence is a bug to fix, not a re-baseline (except the one
  explicit 2^64/2^65 decision in Phase 5).

## Non-goals / demoted

- **Sorting**: unused by all 15 (`usesSorting: false`; coordinate-addressed
  output needs no sorted listing). Only orphan detection would need it.
- **String-keyed maps**: the `hashmap`/`set` in `pkg/stdx/containers` need
  `lang.Hashable` keys, which `@[]char` isn't — but the "maps" the generators
  use are small static config tables → slice-of-struct + linear scan. No real
  hashmap needed.
- **Orphan detection**: `gen-matrix`'s docstring advertises it, but `main()`
  never implemented it (no readdir/scan). Out of scope unless separately
  requested (it would pull in readdir-walk + sorting).
- **Wiring into run.sh / hygiene / CI**: separate decision (Phase 6).

## Open decisions

1. Source home / naming: `pkg/conformance/gen` + `cmd/genmatrix` (proposal).
2. Phase 5 `gen-diff-scalar` 2^64/2^65 cells: add wide-int→decimal rendering, or
   re-baseline those cells.
3. Phase 5 `gen-const`: confirm the bake-precomputed-constants approach for the
   ~4 correct-rounding float rows is acceptable (implied by choosing full
   dogfood).
4. Phase 6: whether to add a `--check` staleness gate.
5. Whether any genlib string/path helpers should graduate into stdlib
   `strings`/`path` rather than stay local to the generator.
