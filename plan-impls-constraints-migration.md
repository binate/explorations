# Migrating the `impls/` / `ifaces/` duplicate trees onto `#[build(...)]`

## Goal

Retire the directory-based platform-variant selection (`impls/{core,stdlib}/{common,libc,baremetal}/`
and the per-triple `ifaces/targets/<triple>/` + `impls/targets/<triple>/`
subtrees) in favour of in-file `#[build(...)]` constraints, now that the
build-constraint mechanism (arch/os, file/decl/import/`.bni` gating) is live.
This removes the per-target duplication and retires
[`pkg-layout-spec.md`](pkg-layout-spec.md) Invariant 5's "whole-package
selection only" restriction (the shared-core + per-variant-file case it said
needed symlinks is now expressible directly).

## Decisions (ratified with the user)

- **libc spelling**: start with the existing `os` predicate —
  `#[build(!is(os, "baremetal"))]` for the libc variants,
  `#[build(is(os, "baremetal"))]` for the baremetal variants. Correct for
  today's target set (baremetal is the only non-libc OS). Switch to an explicit
  `libc` predicate later, *if/when* one is added (a vocabulary-expansion task,
  including its boolean-predicate spelling + a build-config `Libc` property).
- **Sequencing**: do the full `bnlint --target` work (item #2) **first**, then
  the tree collapse (#3), then the `.bni` coverage follow-ups (#5).

## Current shape (survey)

- `ifaces/{core,stdlib}` — platform-independent `.bni`s (unchanged by this).
- `ifaces/targets/<triple>/pkg/builtins/build.bni` — **6 copies** differing
  *only* in the `OS` / `Arch` / `PtrSize` / `IntSize` constants (identical
  type/enum defs). Exactly the `.bni`-gating shape.
- `impls/{core,stdlib}/{common,libc,baremetal}` — only `pkg/bootstrap`,
  `pkg/builtins/rt`, and `pkg/std/os` actually have libc+baremetal duplicates,
  and they are **whole-file variants** (baremetal pulls in `pkg/semihost`;
  baremetal `os` is a 90-line stub vs the 277-line libc one). So the collapse
  is **file-level gating** (two files in one package dir, each with a
  package-clause constraint), not decl-merging.
- `impls/targets/<triple>/pkg/std/os/internal/internal.bn` — 5 tiny per-triple
  files (syscall constants), libc-only. Collapsible via arch+os gating.
- No symlinks are in use today.

## Hard prerequisite: universal build-config activation

The gate only filters when the loader's `BuildConfig` is **active**. Today the
platform variant is chosen by the `-L` search path, so an *inactive* config
still loads exactly one variant. After the collapse, both variants live in one
directory, so any loader that loads core/stdlib with gating **off** would load
*both* → duplicate-symbol collision.

A repo-wide sweep of `NewLoader` sites found **four** entry points that load
packages without `ResolveBuildConfig` (only `bnc` program-mode `main.bn` and
`bni` `main.bn` already activate it):

| Site | Mode | Why it loads core/stdlib |
|---|---|---|
| `cmd/bnc/compile.bn` | `--pkg` single-package | how the unit-test runner & builds compile each package standalone — most load-bearing |
| `cmd/bnc/test.bn` | `--test` | loads test packages + stdlib |
| `cmd/bnlint/main.bn` | lint | typechecks dependency bodies (incl. core/stdlib); the hygiene `lint` check runs it over all of `pkg/`+`cmd/` |
| `pkg/binate/repl/session.bn` | REPL | evaluates expressions that import std |

All four must activate a config before any collapse lands. Adding
`ResolveBuildConfig()` is a **no-op today** (core/stdlib carry no build
annotations yet, so gating keeps everything; a missing `build.bni` leaves the
config nil = inactive, which is safe), so the prerequisite can land ahead of
the collapse with no behaviour change.

## Stages (each independently green & cherry-pickable)

### Stage A — `bnlint --target` + universal config activation (item #2)
- Add `buildcfg.ConfigForTargetKey(key) (@BuildConfig, bool)` mapping the
  `bnc --target` keys (`x86_64-linux`, `x86_64-darwin`, `aarch64-linux`,
  `arm32-linux`, `arm32-baremetal`, and `host`/`""`/`aarch64-darwin`) → arch/os
  tags. Lives in `buildcfg` (BUILDER-compilable) because Stage C reuses it for
  bnc/bni.
- `cmd/bnlint`: add a `--target KEY` flag; **host / no `--target`** →
  `ResolveBuildConfig()` (reads the host `build.bni` from `-I`, correct per
  `uname`, mirroring bnc); **explicit `--target KEY`** → `ConfigForTargetKey`.
- Add `ResolveBuildConfig()` to `cmd/bnc/compile.bn` (`--pkg`),
  `cmd/bnc/test.bn` (`--test`), and `pkg/binate/repl/session.bn` so config
  activation is universal.
- Tests: `ConfigForTargetKey` unit tests; bnlint `--target` arg test; confirm
  hygiene `lint` still clean.

### Stage B — collapse the `impls/` variant + targets trees (item #3, part 1)
- One commit per duplicated package (`pkg/builtins/rt`, `pkg/bootstrap`,
  `pkg/std/os`): move both variants into one package dir under the `common`
  tree, each file gated by its package-clause constraint; delete the now-empty
  `libc`/`baremetal` package dirs.
- Collapse `impls/targets/<triple>/.../internal.bn` into one gated location
  (arch+os constraints).
- Update `scripts/binate-paths.sh` to stop emitting the `-L` entries that no
  longer exist; update `pkg-layout-spec.md` (Invariant 5).

### Stage C — collapse `ifaces/targets/build.bni` (item #3, part 2; the chicken-and-egg)
- The 6 `build.bni` copies become one gated `.bni`. But `ResolveBuildConfig`
  *reads* `build.bni` to *determine* the config (sub-loader, gating inactive),
  so a gated `build.bni` can't be read that way. Resolution: source the config
  from the resolved **target/host directly** — explicit `--target` via
  `ConfigForTargetKey` (bnc already maps the triple in `applyTarget` for
  codegen, so this removes a redundant round-trip); host via a host-detection
  primitive or a minimal retained host seed. **Open decision to confirm when
  reached.**

### Stage D — `.bni` coverage follow-ups (item #5)
- Gated `.bni` decl overlaying a real `.bn` impl (combined package).
- Malformed-constraint-in-`.bni` negative test.

## Status

- Build-constraint mechanism (arch/os, file/decl/import/`.bni`): **landed**
  (see [`plan-build-constraints.md`](plan-build-constraints.md)).
- This migration: **Stage A landed** (binate `c0710a78` ConfigForTargetKey,
  `aaa7dc3e` bnlint `--target`, `ace53953` config activation in
  `--pkg`/`--test`/repl, `2d45916d` resolveLintConfig coverage). Config
  activation is now universal across all package-loading entry points, and
  `bnlint --target` gates correctly (verified end-to-end). **Stage B next.**
