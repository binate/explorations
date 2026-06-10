# Plan: `binate-paths` search-path helper → `pkg/bootstrap` regularization → bnc-0.0.8 release

Status: **Phase 1 LANDED** 2026-06-10 (binate `5b7f3a6a` the helper + bundle
ship + BUNDLE-HOWTO; `8cb1e0ec` the in-tree adoption sweep — 29 files, validated
cross-cutting across all runnable conformance modes + unittest + make-bundle;
the landing rebase merged a concurrent `--runtime` addition `a256c893` into the
6 compiled-mode runners, routed through `binate-paths --runtime`; `8de458b4`
the completeness follow-up — the `8cb1e0ec` sweep had inventoried only
`scripts/` + `conformance/` and missed `e2e/` + `perf/runners/`, now also
adopted, so a repo-wide grep shows only the intentional remainders:
`e2e/split-paths.sh` (a split-path test fixture), `fetch-builder.sh` doc
comments, and `build_gen1`'s overlay). **D2 LANDED** 2026-06-10 (binate
`be692ce0`) — the BUILDER overlay across `build_gen1`, the four `build-*.sh`,
and `e2e/{repl,print-args}.sh` is now `binate-paths --base "$blib" --prepend
"$BINATE_DIR"` (BUILDER-only builtin+stdlib deps, source only for
pkg/binate+pkg/bootstrap, **no source fallback** — the bnc source cone may only
use features the BUILDER has, per the user); kills the duplication behind the
`e29aaec0` drift bug. Validated locally (gen1/gen2/interp, make-bundle, e2e);
monitoring CI for arm32 / x64-elf. The outer `-I/-L` is a vestigial
bootstrap-shape prefix the bnc-* wrapper strips. Remaining:
Phase 2 (`pkg/bootstrap` under core), Phase 3
(bnc-0.0.8 release); `examples/_common.sh` adoption deferred to post-release
(it consumes binate via the release bundle). Owner: TBD. Spans three repos
(`binate`, `examples`, and the release) plus a BUILDER bump.

This plan turns the hand-copied package-search-path formula into one shipped
helper (`binate-paths`), then removes the last reason that formula needs its
awkward "bare root" entry (regularizing `pkg/bootstrap`), then cuts the
long-overdue bnc-0.0.8 release that ships both. The phases are dependency-
ordered; each is independently landable and keeps every mode green.

---

## 0. Background and the facts this plan rests on

The canonical search paths a tool needs, rooted at a layout base `B` (the
directory holding `ifaces/`, `impls/`, `runtime/`, and — today — `pkg/bootstrap`):

```
-I  B : B/ifaces/core : B/ifaces/stdlib
-L  B : B/impls/core/common : B/impls/core/libc : B/impls/stdlib/common
--runtime  B/runtime/binate_runtime.c        # bnc only, host C runtime
```

`B` is the only thing that varies between contexts: `B = $bundle/lib` for a
release bundle, `B = $BINATE_DIR` for the source tree — `make-bundle.sh`
`cp -R`s `ifaces/ impls/ runtime/` straight into `lib/`, so the two have an
identical relative layout. The formula is **target-invariant** (even
`arm32-baremetal` uses the same `-I/-L`; target specifics — `--target`,
baremetal crt0/libgcc, baremetal runtime — are compiler-driven and separate).

That formula is currently hand-copied across **~48 standard-formula call
sites** (41 plain `$BINATE_DIR` + 7 conformance `$compile_root`), plus
`examples/scripts/_common.sh`, plus the BUNDLE-HOWTO prose — none tied to
`make-bundle.sh`'s layout, so all drift silently if the layout changes.

Key findings from investigation (2026-06-09), which the design depends on:

- **The bare-root `B` entry's only job is `pkg/bootstrap`** (and, in source
  only, `pkg/binate/*` for compiler self-builds). At the bare source root,
  `pkg/` holds only `pkg/binate/` and `pkg/bootstrap/`; builtins live under
  `ifaces/core`+`impls/core`, stdlib under `ifaces/stdlib`+`impls/stdlib`. In
  a bundle the bare `lib` root resolves *only* `pkg/bootstrap`.
- **`bnc` resolves `pkg/bootstrap` even when it is not on the `-I/-L` path**
  (a default derived from the runtime/source location); the **VM (`bni`) does
  not** and hard-fails `package "pkg/bootstrap" not found`. So a compiled
  `println`-only program tolerates a missing bare root; the VM and any
  explicit `import "pkg/bootstrap"` do not.
- **The gen1 "BUILDER overlay" reduces to `--base $blib --prepend $BINATE_DIR`.**
  Experiment: a gen1 built with `-I $BINATE_DIR:$blib/ifaces/core:$blib/ifaces/stdlib:$blib`
  (source root first, then BUILDER-only deps) builds and runs (`001_hello`,
  `061_cross_pkg_call` pass). The current interleave's `src-core`-ahead-of-
  `BUILDER-core` ordering is redundant *today* and backwards from intent
  (it makes source win, not the BUILDER); source core/stdlib's only real role
  is forward-compat *fallback* (an append).
- **The one hard ordering constraint is `$BINATE_DIR` before the bundle bare
  root `$blib`.** The released **bnc-0.0.7 bundle ships `lib/pkg/binate`** —
  the entire compiler internals, including a frozen `ir.bni` *without*
  `SetVerifyIR`. Putting `$blib` (bare) ahead of `$BINATE_DIR` resolves
  `pkg/binate/ir` to that stale copy → `undefined: SetVerifyIR`. The current
  `make-bundle.sh` already excludes `pkg/binate` (its comment: shipping it
  makes compiler internals importable via the bare root); 0.0.7 predates that
  fix. See the leak note in §4.

---

## Phase 1 — the `binate-paths` helper

### 1.1 The script: `scripts/binate-paths.sh`

A self-contained POSIX `sh` script — the single source of truth for the
formula. Self-contained (no `scripts/lib/` dependency) so it can be copied
verbatim into a bundle.

```
binate-paths [--base DIR] [--prepend PATH]... [--append PATH]...
             [--iface | --impl | --runtime] [--export]
```

Semantics:

- `--base DIR` — the layout root (contains `ifaces/ impls/ runtime/`). Default:
  **self-locate** — the shipped `bin/binate-paths` probes `$(dirname $0)/../lib`
  (a bundle); the in-repo `scripts/binate-paths.sh` probes `$(dirname $0)/..`
  (the source root). Probe rule: if `<dir>/ifaces` exists use it, else error.
- `--prepend PATH` (repeatable) — prepended to `-I` *and* `-L`, in order. This
  is the general primitive: a project root is `--prepend $root`; the gen1
  overlay's source root is `--prepend $BINATE_DIR`.
- `--append PATH` (repeatable) — appended to `-I` *and* `-L`, in order. The
  "fallback if the base doesn't ship this package" case.
- Selector (exactly one, optional): `--iface` prints the `-I` value, `--impl`
  the `-L` value, `--runtime` the runtime file path. With **no** selector it
  prints an eval-able block (one assignment per line):
  ```
  BINATE_I='...'
  BINATE_L='...'
  BINATE_RT='...'
  ```
  `--export` prefixes each with `export `. `--runtime` ignores prepend/append
  (the runtime is a single file from the base, not a search path).
- **Dedup**: identical path entries collapse to their first occurrence, so
  `--prepend $X` with `$X == base` (the common in-repo `compile_root=$BINATE_DIR`
  case) does not double the entry.

Emitted standard form for base `B`:
- `--iface` → `B:B/ifaces/core:B/ifaces/stdlib`
- `--impl`  → `B:B/impls/core/common:B/impls/core/libc:B/impls/stdlib/common`
- `--runtime` → `B/runtime/binate_runtime.c`
(note the `-L` asymmetry: two core impl dirs `core/common` + `core/libc` vs the
single `ifaces/core`.)

Worked examples:
```
binate-paths --iface --base $BINATE_DIR
  -> $BINATE_DIR:$BINATE_DIR/ifaces/core:$BINATE_DIR/ifaces/stdlib

binate-paths --iface --base $BINATE_DIR --prepend $compile_root      # conformance
  -> $compile_root:$BINATE_DIR:$BINATE_DIR/ifaces/core:$BINATE_DIR/ifaces/stdlib
     (dedup → identical to the plain form when compile_root==$BINATE_DIR)

eval "$(binate-paths --base $LIB --prepend $PWD)"                    # a consumer
  -> sets BINATE_I / BINATE_L / BINATE_RT
```

Unit-ish test: a small `scripts/hygiene`-adjacent shell test (or a `*_test`
under `scripts/`) asserting the emitted strings for the standard, prepend,
append, dedup, and self-locate cases. (Do **not** wire it into CI without
explicit sign-off — see §5.)

### 1.2 Ship it in the bundle — `scripts/make-bundle.sh`

- Add one line after the four binaries: `cp "$SCRIPT_DIR/binate-paths.sh"
  "$dest/bin/binate-paths"` (+ `chmod +x`).
- Extend make-bundle's layout doc-comment and the `release.yml` /
  BUNDLE-HOWTO layout blocks to list `bin/binate-paths`.
- Extend make-bundle's existing smoke (or add one): build a trivial `hello.bn`
  using `binate-paths` output against the staged bundle, so a layout change
  that breaks the formula fails the bundle build:
  ```
  eval "$("$dest/bin/binate-paths" --base "$dest/lib")"
  "$dest/bin/bnc" -I "$BINATE_I" -L "$BINATE_L" --runtime "$BINATE_RT" -o ... hello.bn
  ```

### 1.3 Adopt at the standard-formula sites

All call the in-repo `"$BINATE_DIR/scripts/binate-paths.sh"` (which self-locates
`--base $BINATE_DIR`, or pass it explicitly):

- **`scripts/lib/build-compilers.sh`** — compute `_BN_I`/`_BN_L`/`_BN_RT` once
  at top via command substitution, reuse in `build_gen2`, `build_bnc_native_aa64`,
  `build_interp`. (The `build_gen1` overlay is §1.4.)
- **`conformance/runners/*.sh`** (~12) and **`scripts/unittest/runners/*.sh`**
  (~10) — replace the inline `-I/-L` strings. The conformance `compile_root`
  sites become `--prepend "$compile_root"`; **normalize** the multi-package
  case to include the base root (it currently drops `$BINATE_DIR` for
  proj≠base, which only "works" via `bnc`'s bootstrap leniency — adding it back
  is harmless and removes the quirk; single-file is byte-identical).
- **`scripts/build-{bnc,bni,bnas,bnlint}.sh`** — the plain (`$BINATE_DIR`)
  `-I/-L` blocks (e.g. the bnc unit-test invocations). Their **BUILDER-overlay**
  block is §1.4.
- **`scripts/check-alloca-hoist.sh`**, **`scripts/hygiene/lint.sh`** — plain
  formula.
- **`examples/scripts/_common.sh`** (separate repo, separate commit):
  `set_paths` becomes a `binate-paths` call against the fetched bundle `$LIB`
  with `--prepend "$_root"`.
- **`BUNDLE-HOWTO.md`** — replace the prose "set these once" block with the
  `eval "$(binate-paths --base "$LIB")"` form; keep a one-line note on
  `--prepend` for project roots.

### 1.4 The BUILDER-staging sites (the 5 overlay invocations)

`build_gen1` (in `build-compilers.sh`) and `build-{bnc,bni,bnas,bnlint}.sh`
share one BUILDER-interleave string. It is **not** expressible as a clean
prepend/append of the *current* form (the `BUILDER-stdlib` entry is hoisted
mid-list), but the investigation showed it **reduces** to:

```
binate-paths --base "$blib" --prepend "$BINATE_DIR"
```

i.e. source root first (load-bearing: `pkg/binate` + bootstrap, and it shadows
the 0.0.7 bundle's stale `pkg/binate`), then the BUILDER bundle's standard set.
This is a **real behavior change** to the bootstrap build (drops the redundant
source core/stdlib and the stdlib hoist), so:

- Treat it as its **own commit**, validated across **all** `builder-comp*`
  modes (`builder-comp`, `builder-comp-int`, `-int-int`, `builder-comp-comp`,
  `-comp-int`, `-comp-comp`, the native `aa64`/`x64-darwin`, and an
  `arm32_linux`/`arm32_baremetal` compile-check) + gen2 + compiled-interp,
  not just `001`/`061`.
- **Decision (D1):** keep a source core/stdlib *fallback* (append, for when
  source adds a package newer than the frozen bundle) or drop it? Recommend
  **drop for now** (Exp. shows it's unused today) and reintroduce as an
  `--append-base $BINATE_DIR` if/when a too-new dependency appears — i.e. add
  `--append-base DIR` to the helper only if needed.
- **Decision (D2):** do this overlay change *in Phase 1*, or land Phase 1
  reproducing today's behavior (leaving the 5 staging strings hand-written —
  they can't go through `binate-paths` unchanged) and do the overlay change as
  a focused follow-up? Recommend **follow-up** — it keeps Phase 1 a pure
  refactor (no behavior change) and isolates the bootstrap-build change with
  its own full-matrix validation. (After Phase 3 + BUILDER bump the overlay
  collapses even further — see §3.)

### 1.5 Validation, hygiene, landing

- After §1.3 (pure refactor — emitted strings identical modulo the documented
  conformance normalization): run `scripts/hygiene/run.sh`, then
  `conformance/run.sh basic` and a sampling of `scripts/unittest/run.sh`
  across the default modes. The whole point is byte-identical paths, so green
  is expected and any red is a bug in the helper.
- Land in small green commits, cherry-picked to `main` per the approval rule:
  (a) `binate-paths.sh` + make-bundle ship + smoke + BUNDLE-HOWTO; (b) the
  in-repo standard-site sweep; (c) examples repo (`_common.sh`); [(d) the §1.4
  overlay change, if folded in rather than deferred].
- **Optional hygiene guard (ask first):** a check that forbids the inline
  `ifaces/core:…/ifaces/stdlib` formula anywhere except `binate-paths.sh`, to
  stop re-duplication. Adding/wiring a check is a separate decision (§5).

---

## Phase 2 — regularize `pkg/bootstrap` under core (transitional, off the bare root)

**`pkg/bootstrap` must not join the permanent `pkg/builtins/*` set** (per the
user, 2026-06-09): it is a transitional host-I/O shim **slated for deprecation**
(its surface migrates to `pkg/std/os` / `pkg/std/io` — the bootstrap-dissolution
work). But that is about *namespace membership*, not physical directory: it may
sit under the existing `ifaces/core`+`impls/core` search dirs **as long as the
import path stays `"pkg/bootstrap"`** (NOT renamed to `pkg/builtins/bootstrap`).
Keeping the path means it is not a member of the builtins package set even
though its files live in the core dirs; when bootstrap is fully deprecated the
files are simply deleted.

Goal: remove the *reason* the formula needs a bare-root entry by moving
`pkg/bootstrap` under the **existing** `ifaces/core`+`impls/core` search dirs,
so it resolves via formula entries already present — **no new tier, no
`binate-paths` formula change** — with no source-wide `import`/`bootstrap.X`
churn (the path string is unchanged) and no change to the mangled
`bn_pkg__bootstrap__*` symbols print/println emits.

### 2.1 The move (source)

- `git mv pkg/bootstrap.bni        ifaces/core/pkg/bootstrap.bni`
- `git mv pkg/bootstrap/           impls/core/common/pkg/bootstrap/`
  (`bootstrap.bn`, `bootstrap_test.bn`)
- The loader resolves `<I-entry>/pkg/bootstrap.bni`, so the **existing**
  `ifaces/core` entry now resolves it and `impls/core/common` the impl —
  **no `binate-paths` formula change** (those entries are already present). No
  code references change: the path string `"pkg/bootstrap"` is unchanged (it
  does NOT become `pkg/builtins/bootstrap`), so it stays out of the builtins
  namespace.
- **Baremetal:** the target-specific bootstrap impl
  (`runtime/baremetal_arm32/pkg/bootstrap/bootstrap.bn`, prepended via the
  `--target arm32-baremetal` impl-path-front in `cmd/bnc/target.bn`) keeps its
  override location; verify the front-prepend still wins over the new
  `impls/core/common/pkg/bootstrap` host impl (first-match-wins). Adjust the
  target suffix list if the host-impl relocation changes precedence.

### 2.2 Downstream updates

- **`scripts/make-bundle.sh`** — delete the special `lib/pkg/bootstrap` copy
  (the `mkdir lib/pkg` + two `cp` lines); bootstrap now travels inside the
  existing `cp -R ifaces` / `cp -R impls`. The bundle's bare `lib` root becomes
  empty of importable packages.
- **`binate-paths`** — the bare-root `B` entry's bootstrap job is gone. It is
  now needed *only* for `pkg/binate/*` during compiler self-builds (in source).
  Options: (a) keep `B` in the standard formula (harmless — an empty dir entry
  in a bundle, and load-bearing for `pkg/binate` in source); or (b) drop `B`
  from the standard formula and have compiler-self-build callers
  `--prepend $BINATE_DIR`. Recommend **(a) for the transition**, revisit after
  Phase 3 (§3) when the bundle is guaranteed `pkg/binate`-free.
- **BUNDLE-HOWTO** — drop the "the bare `$LIB` root is for `pkg/bootstrap`"
  note; bootstrap is now an ordinary core package.

### 2.3 Compatibility during the BUILDER=0.0.7 window

Phase 2 works *before* a BUILDER bump: the gen1 build's source paths include
`$BINATE_DIR/ifaces/core` (resolves the relocated source bootstrap); the 0.0.7
bundle still carries bootstrap at its bare `lib/pkg/bootstrap`, which remains a
harmless fallback. No BUILDER change required to land Phase 2.

### 2.4 Validation

Full conformance + unit matrix across all modes (this touches package
resolution for a universally-loaded package + the baremetal override): all
`builder-comp*` + `-int*` + native + arm32 compile-checks, plus
`pkg/bootstrap`'s own unit tests at their new location, plus a bundle build
(`make-bundle.sh` + the §1.2 smoke) to confirm the relocated layout resolves.

---

## Phase 3 — cut the bnc-0.0.8 release (overdue)

`VERSION` is already `bnc-0.0.8-pre`; `BUILDER_VERSION` is `bnc-0.0.7`. The
release is tag-triggered (`.github/workflows/release.yml` on a `bnc-*` tag),
which runs `make-bundle.sh` per platform (linux-x64, macos-arm64; macos-x64
omitted) and publishes the tarballs + `SHA256SUMS`.

### 3.1 Pre-release checklist

- Phase 1 landed (bundle ships `bin/binate-paths`).
- Phase 2 landed (bootstrap under `ifaces/core`+`impls/core`; bundle no longer
  ships `lib/pkg/bootstrap`; already no `lib/pkg/binate`).
- `VERSION` → `bnc-0.0.8` (drop `-pre`); confirm version-sync hygiene.
- Full conformance + unit green on `main`; a local `make-bundle.sh` dry-run on
  the host platform + the §1.2 smoke.
- BUNDLE-HOWTO reflects the new layout (binate-paths present; bootstrap under
  core).

### 3.2 Release

- Tag `bnc-0.0.8` on `main` and push → the Release workflow builds + publishes.
- Verify the published `SHA256SUMS` and that each tarball has
  `bin/{bnc,bni,bnas,bnlint,binate-paths}` and a `lib/` with bootstrap under
  `ifaces/core`+`impls/core` and **no** `lib/pkg/binate`.

### 3.3 Post-release: BUILDER bump and the final overlay collapse

- Bump `BUILDER_VERSION` → `bnc-0.0.8` (binate repo + `examples` repo). Now the
  BUILDER bundle has the regularized layout and carries no `pkg/binate`.
- With a `pkg/binate`-free BUILDER bundle, the gen1 overlay's last ordering
  constraint (`$BINATE_DIR` before `$blib`, to shadow the stale `pkg/binate`)
  disappears: the overlay can become a plain "BUILDER set, source appended only
  for `pkg/binate`" — and `binate-paths`'s bare-root entry can be dropped from
  the consumer formula entirely (§2.2 option (b)). Do this as a final cleanup
  commit after the bump, validated across the full matrix.
- Update `BUILDER` compatibility notes in `CLAUDE.md` (the bnc version + what
  the bundle now contains).

---

## 4. Tracked finding — the 0.0.7 bundle ships compiler internals (leak)

The released **bnc-0.0.7** bundle ships `lib/pkg/binate/*` (full compiler
internals). A BUNDLE-HOWTO consumer pointing `-I` at `lib` can therefore
`import "pkg/binate/ir"` (etc.) and pull frozen compiler internals through the
bare-root entry. Severity **low** (who imports `pkg/binate`?) and **already
fixed going forward** — the current `make-bundle.sh` excludes `pkg/binate`, so
bnc-0.0.8 won't ship it. Action: file a one-line `claude-todo.md` note so it's
tracked until 0.0.8 ships and supersedes 0.0.7 as BUILDER. No code change
needed beyond cutting the release.

---

## 5. Decisions (resolved 2026-06-09 unless noted)

- **D1 (source fallback): DROP it.** The gen1 overlay drops the source
  core/stdlib fallback (unused today); add an `--append-base $BINATE_DIR` only
  if a too-new dependency later appears.
- **D2 (overlay timing): FOLLOW-UP.** Phase 1 is a pure refactor; the gen1
  overlay simplification (§1.4) is a separate, full-matrix-validated commit.
- **D3 (bootstrap destination): RESOLVED — under core, path unchanged.** Move
  the files under `ifaces/core`+`impls/core/common` (so they resolve via the
  existing formula entries, no new tier), but **keep the import path
  `"pkg/bootstrap"`** so it does NOT join the permanent `pkg/builtins/*` set.
  The constraint the user drew is namespace membership, not the physical dir;
  the files are deleted outright when bootstrap is fully deprecated.
- **D4 (bare-root through transition): KEEP `B`** in the standard formula for
  now; revisit dropping it after Phase 3 + BUILDER bump.
- **D5 (hygiene guard): NO** (for now). Script count is small and `binate-paths`
  adoption is self-reinforcing; reconsider only if duplication recurs.

---

## 6. Sequencing summary

```
Phase 1  binate-paths + ship + standard-site sweep + examples + HOWTO   (pure refactor)
   └─(1.4) gen1 overlay → binate-paths form   [follow-up, full-matrix validated]
Phase 2  regularize pkg/bootstrap under ifaces/core + impls/core        (works w/ BUILDER 0.0.7)
Phase 3  cut bnc-0.0.8 (ships binate-paths + regularized bootstrap)
   └─ bump BUILDER_VERSION → 0.0.8 → final overlay/bare-root collapse
```

Each phase is independently landable and green; Phases 2 and 3 are the
user-requested follow-ups, and 3 unblocks the overdue release.
