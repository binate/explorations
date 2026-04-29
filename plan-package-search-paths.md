# Plan: Two-path package resolution (interface + impl)

## Status (2026-04-28)

**Phase 1 complete (Stages 1-6).** The two-path search is shipped:
loader, all four CLI tools (bnc, bni, bnlint, bootstrap), and the
deprecated `Roots` field cleanup are landed in both the binate and
bootstrap repos.

| Stage | What | Commit |
|-------|------|--------|
| 1 | pkg/loader: split Roots into BniPath + ImplPath | binate `8736c48` |
| 2 | cmd/bnc: -I / -L (and long forms) | binate `3a1fef4` |
| 3 | cmd/bni: -I / -L (and long forms) | binate `f69a89c` |
| 4 | cmd/bnlint: -I / -L (and long forms) | binate `e6a5d78` |
| 5 | bootstrap: loader split + CLI flags | bootstrap `ad74a4c` |
| 6 | drop deprecated Roots field | binate `f7f53fc`, bootstrap `9e6d107` |

**Outstanding (deferred):**
- **Stage 7**: env-var support
  (`BINATE_PACKAGE_INTERFACE_PATH` / `BINATE_PACKAGE_IMPL_PATH`).
  Gated on adding `bootstrap.Getenv`. Direct CLI is sufficient for
  now since cross-compile drivers can construct command lines.
- **Stage 8** (Phase 2): binary `.o`/`.a`/`.so` artifacts on
  IMPL_PATH. Tied to having a stable per-package ABI/linker
  contract.

The body of this doc remains the design reference; everything below
this status section is unchanged from the original plan.

## Motivation

Today the loader has a single `Roots @[]@[]char` list that's searched
in order for both `.bni` interfaces and implementation directories
(`<root>/<path>.bni` for the first, `<root>/<path>/` containing `.bn`
files for the second). The interface and impl happen to be allowed
to come from different roots — but only as an emergent property of
the iteration order, not as a deliberate design.

We want to make this split first-class for three reasons:

1. **Cross-compilation.** A target's interfaces (the `.bni` files
   defining the target's runtime, syscalls, ABI) need to be
   independently selectable from where the implementations live.
   A single search path conflates those concerns.

2. **Multiple impls per package.** Developer / debugging workflows
   want to point a compile at, say, an instrumented `pkg/rt` impl
   without changing the `.bni`, or at a stripped-down impl on a
   resource-constrained target. The search-in-order semantics make
   this trivial to express ("debug impl path takes precedence over
   release impl path") if the impl path is independent of the
   interface path.

3. **Future binary artifacts.** The impl path will eventually accept
   `.o`/`.a`/`.so` artifacts in addition to `.bn` source directories,
   so a built binary can satisfy an import without re-source-compiling.
   The interface path stays source-only (`.bni` files).

## Current state

- `pkg/loader.Loader` has `Root @[]char` (the primary root) and
  `Roots @[]@[]char` (Root plus any `AddRoot()`-appended dirs).
- `loader.LoadImports` iterates `Roots` and, per root, checks both
  `<root>/<path>.bni` and `<root>/<path>/` (the impl dir, considered
  present if it has any `.bn` file).
- `pkg/loader.bni` declares `Root` and `Roots`; `pkg/loader/loader.bn`
  uses them.
- CLI surfaces:
  - `bnc`: `--root <dir>` (single root). No `--add-root` yet.
  - `bni`: `-root <dir>` (single root) and `-add-root <dir>`
    (repeatable).
  - `bnlint`: `--root <dir>` (single root).
  - bootstrap (Go): `-root <dir>` and `-add-root <dir>` (repeatable).
- Flag-style inconsistency: `bnc` uses `--word`, `bni` and bootstrap
  use `-word` (single dash). Worth resolving en-passant — see
  "Naming and CLI shape" below.
- The directory-is-impl criterion already matches the requirement
  ("at least one `.bn` file present"), since `loader.bn` walks the
  dir entries and only counts `.bn` files (`continue` on others).
  This stays the same in the new model.

## Target state

Two independent, ordered search paths:

- **Interface path** (call it `BNI_PATH` for short, formal name
  `BINATE_PACKAGE_INTERFACE_PATH`): a colon-separated list of
  directories searched in order for `<dir>/<path>.bni`. The first
  hit wins. If none hit, the package has no interface — that's fine
  for impl-only packages (e.g. binaries' `main` packages, or impls
  that don't expose a contract).

- **Impl path** (call it `IMPL_PATH` for short, formal name
  `BINATE_PACKAGE_IMPL_PATH`): a colon-separated list of directories
  searched in order for `<dir>/<path>/`. The first directory that
  contains at least one `.bn` file wins (later, also `.o` / `.a` /
  `.so`). If none hit, the package has no impl — that's fine for
  pure-interface packages (e.g. `pkg/bootstrap` is `.bni`-only and
  its impl is satisfied by the C runtime).

A package is **resolved** if at least one of {interface, impl} was
found. If neither, that's the existing `package "X" not found` error.

Cross-root pairing is now an explicit feature: an interface from
`BNI_PATH[2]` paired with an impl from `IMPL_PATH[0]` is the normal
flow, not an accident.

### Implementation criterion

A directory `<dir>/<path>/` is considered to provide an impl iff it
contains at least one `.bn` file (mere directory existence is not
enough). Already true today; just make it explicit in docs and a
helper.

### Resolution algorithm

```
for each dir in BNI_PATH:
    if <dir>/<path>.bni exists:
        bniFile = parse(<dir>/<path>.bni); break

for each dir in IMPL_PATH:
    if <dir>/<path>/ has a .bn file (or .o/.a/.so eventually):
        implFiles = parse all .bn under <dir>/<path>/; break

if bniFile == nil and implFiles == nil: error "not found"
```

Same merge logic afterward (`MergeFiles`, .bni-decl injection,
etc.); only the search step changes.

## Naming and CLI shape

### Env vars

Lead with the descriptive names; offer a short alias so people don't
have to type the long form:

- `BINATE_PACKAGE_INTERFACE_PATH` (alias `BINATE_BNI_PATH`)
- `BINATE_PACKAGE_IMPL_PATH` (alias `BINATE_IMPL_PATH`)

If both forms are set, the long form wins (or warn). Open question
— could just pick one form and not have an alias at all; the long
names match `LD_LIBRARY_PATH` / `PYTHONPATH` style.

### CLI flags

Several plausible surfaces. Each takes a colon-separated list (so a
single flag can express the whole path), and is repeatable (so
`--bni-path A --bni-path B` is equivalent to `--bni-path A:B`).

Option A — verbose, parallels env var names:
- `--interface-path A:B:C`
- `--impl-path A:B:C`

Option B — short, like a C compiler:
- `-I A` (interface; like `-I` for headers in cc)
- `-L A` (impl; like `-L` for libraries in cc)

Option C — both: lead with B for ergonomics, support A for clarity.

Recommendation: **Option C**. `-I` / `-L` are familiar to anyone
who's used a C toolchain and tracks the same conceptual split
(interface vs library). `--interface-path` / `--impl-path` are the
self-documenting forms.

CLI flags **take precedence over** env vars. If a flag is given for
a path, that path is used as-is (env var ignored). If only one of
the pair is set on the CLI, the other still falls back to env var.

### `--root` interaction

**Decision**: `--root <dir>` is sugar for adding `<dir>` to BOTH
paths (`-I <dir> -L <dir>`). It always wins over env vars (since
CLI > env). This preserves all existing scripts and matches the
common monorepo case. Same treatment for `-add-root` (`bni`,
bootstrap): sugar for appending to both paths.

We're not planning to deprecate `--root` — it's the natural
interface for the common case.

### Flag style: standardize on `--`

bnc uses `--word`, bni and bootstrap use `-word`. **Decision**:
standardize on `--word` for long flags. `-word` forms in bni and
bootstrap stay accepted as aliases for backward compat.

The reason for `--` (not `-`): single-dash is reserved for short
flags, including the conventional combinable form like `-abc`
(equivalent to `-a -b -c`). Reserving `-` for shorts keeps that
door open without ambiguity.

Existing one-off short flags (`-c`, `-o`, `-g`, `-v`, plus the new
`-I`, `-L`) stay single-dash and are eligible for `-abc`-style
combination later.

## Loader changes

`pkg/loader.bni`:

```binate
type Loader struct {
    BniPath  @[]@[]char  // search dirs for .bni files
    ImplPath @[]@[]char  // search dirs for impl directories (.bn,
                         //   later .o/.a/.so)
    // ... existing fields (Packages, Order, Errors, TestPackages)
}
```

Drop `Root` / `Roots` once callers migrate. Or keep `Root` as the
"primary" dir for derived helpers (e.g. discovering the conformance
runner location); only the search-path part splits.

New constructors / mutators:

```binate
func NewLoader(bniPath @[]@[]char, implPath @[]@[]char) @Loader
func AddBniPath(l @Loader, dir @[]char)
func AddImplPath(l @Loader, dir @[]char)
```

Convenience for the common single-root case (used by `--root`):

```binate
// AddRoot: appends dir to BOTH paths. Equivalent to AddBniPath(l, dir)
// + AddImplPath(l, dir). Provided for the common monorepo case and
// for backward compatibility with the --root flag.
func AddRoot(l @Loader, dir @[]char)
```

The search loop in `LoadImports`:

```binate
for ri := 0; ri < len(l.BniPath); ri++ {
    var dir @[]char = l.BniPath[ri]
    // try <dir>/<path>.bni; bail on first hit
    ...
}
for ri := 0; ri < len(l.ImplPath); ri++ {
    var dir @[]char = l.ImplPath[ri]
    // try <dir>/<path>/ with the .bn-presence criterion; bail on first hit
    ...
}
```

Two independent loops. The "directory has a .bn file" check moves
into a helper (`hasImplFiles(dir) bool`) so the criterion is in one
place and easy to extend (later: also count `.o`/`.a`/`.so`).

## Tools to update

- **bnc** (`cmd/bnc`): add `-I` / `--interface-path` and `-L` /
  `--impl-path` flag handling in `args.bn`; teach `--root` (and
  per-flag callers) to populate both paths via the new
  `loader.NewLoader` / `AddRoot`. Update `args_test.bn`.
- **bni** (`cmd/bni`): same flag set; existing `-root` / `-add-root`
  become AddRoot-equivalent shims.
- **bnlint** (`cmd/bnlint`): same flag set; same `--root` shim.
- **bootstrap** (`bootstrap/main.go`): same flag set in Go;
  `bootstrap/loader/loader.go`'s `Roots` splits into `BniPath` /
  `ImplPath`. Same algorithm.

## Path syntax

- Colon-separated, like POSIX `PATH`. (Windows would use `;` —
  defer that decision; Binate has no Windows story today.)
- Empty entries (`A::B`) are skipped silently. Trailing colon is
  benign.
- Relative paths resolved against the current working directory at
  flag/env parse time, not at search time. Document this.
- No tilde expansion at the language level — leave it to the shell.

## Env vars (and their absence in pkg/bootstrap)

`pkg/bootstrap` doesn't expose `getenv` today. Two reasonable paths:

- **Stage 0**: ship CLI flags first; env-var support is a second
  step gated on adding `bootstrap.Getenv`. The CLI is sufficient
  for cross-compilation drivers that already construct command
  lines.
- **Add `bootstrap.Getenv` now** as a tiny addition to the bootstrap
  surface (a handful of lines in C and the Go interpreter). Then env
  + CLI ship together.

Recommendation: **Stage 0 first**, evaluate adding `bootstrap.Getenv`
based on whether direct shell invocations of bnc/bni need env-var
support before the CLI is enough.

## Future: binary impl artifacts

Once `.o`/`.a`/`.so` are accepted on `IMPL_PATH`:

- `hasImplFiles(dir)` becomes "has at least one of {.bn, .o, .a, .so}".
- A directory mixing `.bn` and `.o` requires a precedence rule.
  Probably: `.o`/`.a`/`.so` win over `.bn` (you asked for the
  precompiled artifact; ignore the source). With a `--prefer-source`
  flag for explicit override.
- Linker integration: bnc gathers the binary artifacts from
  IMPL_PATH and feeds them to clang/`ld` automatically (today the
  user supplies them via `--cflag`).

This is Phase 2; the current plan is Phase 1 (source-only impl path,
matching today's capabilities but with the two-path split).

## Stages

1. **Loader split.** Update `pkg/loader.bni` + `loader.bn`: introduce
   `BniPath` / `ImplPath`, keep `Roots` as a deprecated alias that
   AddRoot writes to both new fields. Update internal callers.
   Conformance / unit tests should pass unchanged.
2. **bnc CLI.** Add `-I` / `--interface-path` / `-L` / `--impl-path`
   handling. `--root` continues to populate both. Add `args_test.bn`
   coverage. No behavior change for existing invocations.
3. **bni CLI.** Same.
4. **bnlint CLI.** Same.
5. **bootstrap CLI.** Same in Go.
6. **Drop `Roots` field.** Once all callers use `BniPath` /
   `ImplPath` (or `AddRoot`), remove the deprecated `Roots` field
   and any helper that returned it.
7. **(optional) Env-var support.** Add `bootstrap.Getenv`; wire
   `BINATE_PACKAGE_INTERFACE_PATH` / `BINATE_PACKAGE_IMPL_PATH` into
   each tool's CLI parser as the fallback.
8. **(future Phase 2) Binary artifacts on IMPL_PATH.**

Each stage independently lands and ships green.

## Tests

- **pkg/loader unit tests**: split-path resolution. Set up a temp
  fixture with `<bni-root>/foo.bni`, `<impl-root>/foo/foo.bn` (no
  bni in impl-root, no impl in bni-root); confirm the package
  resolves with the correct files paired together. Cover misses,
  bni-only packages, impl-only packages, ordering precedence.
- **CLI tests** (per tool's `args_test.bn` / `main_test.go`):
  verify flag parsing for both forms (long + short), repeatable
  flags, colon-separated lists, and `--root` shim behavior.
- **Conformance**: an end-to-end test that uses `--bni-path` and
  `--impl-path` separately to point at two different roots and
  produces the expected output. The setup is fiddly enough (needs
  a small two-tree fixture) that this might warrant a multi-package
  conformance test.

## Resolved decisions

- **`--root` stays** as sugar for adding the dir to both paths. No
  deprecation planned.
- **Flag style**: `--word` for long flags everywhere, single `-`
  reserved for short flags (including future `-abc` combination
  syntax). Existing `-word` aliases in bni/bootstrap stay accepted
  for backward compat.
- **Per-package overrides** are achievable in this scheme without
  a special feature: stage a private "root" containing only the
  override package(s) (symlinks fine for the rest), put it first
  on the appropriate path. The IMPL_PATH ordering takes care of
  precedence. So no per-package mechanism needed.

## Open questions

- **Primary docs name**: lead with `-I` / `-L` (short, familiar to
  C-toolchain users) or `--interface-path` / `--impl-path` (self-
  documenting)? Both supported either way; this is just about which
  form the docs/help text show first.
- **Env var names**: long form (`BINATE_PACKAGE_INTERFACE_PATH`)
  vs short (`BINATE_BNI_PATH`). Long matches PEP/PYTHONPATH style;
  short matches `PATH`/`MANPATH`. Recommendation in the body is
  "long primary, short alias".
- **Phase 2 timing**: when does binary-artifact support land? Tied
  to having a stable per-package ABI / linker contract.
