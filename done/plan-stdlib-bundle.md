# Plan: bundle tier-1 stdlib with the BUILDER, use it from stage-1 builds

## Goal

Make the tier-1 stdlib (`pkg/std/...`, `pkg/stdx/...`) a **BUILDER-provided
component**, so that the stage-1 build of `cmd/bnc` (and the other tools)
resolves stdlib imports from the BUILDER's bundled `lib/` rather than
recompiling current-source stdlib.

The payoff: `cmd/bnc`'s BUILDER-compilable tree can then `import
"pkg/std/math/big"` (and other stdlib) **without** dragging full-language
current-source stdlib into the set the BUILDER must compile. That unblocks
routing the float-literal converter (`common.ParseFloatLitToBits`, 1-ULP-low
for 38+-digit just-above-tie literals — see `claude-todo.md`) through
`pkg/std/math/big` / `strconv.ParseFloat`, retiring the round-bit bug and the
duplicate dtoa converter.

Secondary payoff: it frees current-source stdlib from the BUILDER-subset
constraint. Today `cmd/bnc` imports `pkg/stdx/slices`, which therefore must
stay BUILDER-compilable; once stage-1 reads stdlib from the frozen bundle,
current-source stdlib can use the full language.

## Current state (as of `bnc-0.0.7-pre`, BUILDER `bnc-0.0.6`)

- **Stdlib source** lives in the split trees, mirroring tier-0 (`ifaces/core`
  + `impls/core`): `ifaces/stdlib/pkg/{std,stdx}/*.bni` and
  `impls/stdlib/common/pkg/{std,stdx}/*.bn`. Present today: `std/errors`,
  `std/strconv`, `std/math/big`, `stdx/slices`. (`impls/stdlib/libc` is an
  empty placeholder.)
- **The release bundle already ships it.** `release.yml` does `cp -R
  binate/ifaces lib/ifaces` and `cp -R binate/impls lib/impls`, so a tarball
  cut from current source bundles the populated stdlib trees with no
  release.yml change.
- **But the BUILDER (`bnc-0.0.6`) bundles EMPTY stdlib** — only `README.md`s
  under `lib/ifaces/stdlib` and `lib/impls/stdlib`. The content landed after
  0.0.6 was cut. So 0.0.6 cannot provide stdlib to anything.
- **The build scripts search current-source stdlib FIRST.** Every stage-1
  (BUILDER→gen1) invocation orders its gen1 `-I`/`-L` as
  `$BINATE_DIR/...stdlib : ... : $BUILDER_LIB/...stdlib` — current source wins,
  BUILDER is an unused fallback. Stage-2 (gen1→final) and gen2/native/interp
  builds are current-source-only (no `$BUILDER_LIB`).

Net: stdlib is bundled but never *used* from the BUILDER, and the BUILDER that
would provide it is empty anyway.

## Why this is two phases (a bootstrap step)

A new BUILDER capability can't be *used* until it *is* the BUILDER. We cannot
make 0.0.7's own build consume BUILDER stdlib, because 0.0.7 is built with
0.0.6 (empty bundle). So:

- **Cutting 0.0.7 is the enabler** — its tarball is the first BUILDER whose
  bundle contains real stdlib.
- **Consuming BUILDER stdlib only takes effect once `BUILDER_VERSION =
  bnc-0.0.7`.**

The script reorder, however, can land *dormant* ahead of the release: against
0.0.6's empty bundle it is a no-op (BUILDER stdlib not found → falls through to
current source), and it activates automatically the moment BUILDER becomes
0.0.7. That is the chosen ordering.

"Is this release worth cutting?" — **yes.** Empty→populated bundled-stdlib is
explicitly a substantive bundle change, and it unblocks the float fix.

## Execution order

### Step 1 — dormant: prefer BUILDER stdlib in stage-1 (`-pre` tree)

In every stage-1 (BUILDER→gen1) invocation, reorder the **stdlib** search
entries so the BUILDER's come *before* current source, keeping current source
as a fallback:

- `-I`: move `$BUILDER_LIB/ifaces/stdlib` ahead of `$BINATE_DIR/ifaces/stdlib`.
- `-L`: move `$BUILDER_LIB/impls/stdlib/common` ahead of
  `$BINATE_DIR/impls/stdlib/common`.

Leave the **core** entries current-first (core is the compiler-coupled tier-0;
not part of this change) and leave `$BINATE_DIR` (the `pkg/` root) first.
Resulting gen1 order, e.g.:

    -I "$BINATE_DIR:$BINATE_DIR/ifaces/core:$BUILDER_LIB/ifaces/stdlib:$BINATE_DIR/ifaces/stdlib:$BUILDER_LIB:$BUILDER_LIB/ifaces/core"
    -L "$BINATE_DIR:$BINATE_DIR/impls/core/common:$BINATE_DIR/impls/core/libc:$BUILDER_LIB/impls/stdlib/common:$BINATE_DIR/impls/stdlib/common:$BUILDER_LIB:$BUILDER_LIB/impls/core/common:$BUILDER_LIB/impls/core/libc"

Apply uniformly to the gen1 invocations in: `scripts/build-bnc.sh`,
`scripts/build-bni.sh`, `scripts/build-bnas.sh`, `scripts/build-bnlint.sh`,
and `scripts/lib/build-compilers.sh` (the gen1 build at line ~62; the
gen2/native/interp builds are current-only and unchanged). Update the example
invocation in `scripts/fetch-builder.sh`'s header comment to match (doc-only).

**Why harmless against 0.0.6:** the BUILDER stdlib dirs hold only `README.md`,
so `pkg/stdx/slices.bni` isn't found there and resolution falls through to
`$BINATE_DIR/ifaces/stdlib` — identical to today.

**Verify (dormant):** `build-bnc.sh -o /tmp/bnc-dormant` against BUILDER 0.0.6
still succeeds and the binary works; one conformance mode (`builder-comp`)
stays green. This proves the reorder is a no-op against the empty bundle.

This lands on `main` via the normal flow (commit on worktree → cherry-pick,
with approval).

### Step 2 — cut `bnc-0.0.7`

Per `release-process.md`:

1. Pre-cut checks: `-comp*` CI green; stdlib content present under
   `ifaces/stdlib` + `impls/stdlib/common`; confirm no remaining BUILDER float
   gap gates the cut (strconv `Parse...`/hex-float are landed — verify nothing
   else).
2. `VERSION`: `bnc-0.0.7-pre` → `bnc-0.0.7`; sync
   `pkg/binate/version/version.bn` `var Version = "0.0.7"`. Commit
   ("Release bnc-0.0.7"), push.
3. Tag `bnc-0.0.7`, push tag → `release.yml` builds the tarballs.
4. Verify the release: smoke-test the tarball per release-process.md **plus a
   stdlib-specific check** — confirm `lib/ifaces/stdlib/pkg/std/math/big.bni`
   and `lib/impls/stdlib/common/pkg/std/math/big/*.bn` are present and that a
   tiny program importing `pkg/stdx/slices` compiles against the bundle's
   stdlib `-I`/`-L` roots.

### Step 3 — bump BUILDER → `bnc-0.0.7` (activates Step 1)

`BUILDER_VERSION` → `bnc-0.0.7`; `VERSION` → `bnc-0.0.8-pre` (+ `version.bn`).
One commit, push. Now the dormant reorder activates: stage-1 builds resolve
stdlib from the 0.0.7 bundle. **Watch post-release CI (hygiene + unit +
conformance + e2e)** — this is the real test that the bundle works on every
platform. Treat breakage as fix-now.

Also update `version-history.md` — its ladder table is **stale** (stops at
`bnc-0.0.5 (pending)`); record 0.0.5, 0.0.6, and 0.0.7 (headline: first
release to bundle a populated tier-1 stdlib + consume it from stage-1).

### Step 4 — (separate, post-bump) let `cmd/bnc` use stdlib

With BUILDER = 0.0.7, `cmd/bnc` can import stdlib. Fix the 1-ULP round-bit bug
by routing the float-literal converter (`common.ParseFloatLitToBits`) through
**`pkg/std/strconv.ParseFloat`** — it already does exactly the literal→bits
job (decimal + hex, `bitSize` 32/64, underscores, correctly rounded via
`pkg/std/math/big`), so the compiler imports the high-level `strconv` entry
rather than reimplementing over `big` directly, and the duplicate in-tree dtoa
(`ParseFloatLitToBits`) is retired. `strconv` pulls in `math/big` + `errors`
transitively — all bundled. Convert the parsed value to the bit pattern with
`bit_cast` (f64) / `cast(float32, …)` then `bit_cast` (f32 — which also
resolves the separate float32-const-narrowing bug). NOT dormant, NOT part of
the release — requires BUILDER 0.0.7; tracked in `claude-todo.md`.

## Design notes / invariants

- **Stage-2 keeps using current-source stdlib** (it's gen1, current codegen,
  full language). So the *final* binary embeds current-source stdlib while
  gen1 was built against the frozen bundle. This is the intended decoupling;
  gen1 only needs to run to compile stage-2.
- **Current-source stdlib stays as a stage-1 fallback** (we reorder, not
  remove). A genuinely *new* stdlib package that `cmd/bnc` imports before the
  next release bundles it still must be BUILDER-compilable — same constraint as
  today, now scoped to brand-new packages instead of all stdlib.
- **All-or-nothing bundle** keeps the fallback clean: 0.0.6's stdlib is fully
  empty (all fall to current), 0.0.7's is fully populated (all use BUILDER); no
  per-package mixing at the transition.

## Risks

- Post-bump skew: if 0.0.7's bundled stdlib diverges from current source in a
  way stage-1 depends on. Right after the bump current source ≈ 0.0.7 source,
  so low; grows as the tree evolves (intended).
- Platform skew: the linux-x64 bundle's stdlib must build as well as
  macos-arm64's. Covered by post-release CI (Step 3).
- A pre-existing BUILDER float gap blocking the cut. Mitigated by the Step 2
  pre-cut check.
