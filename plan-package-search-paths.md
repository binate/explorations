# Plan: Two-path package resolution (interface + impl)

## Status

**Phase 1 COMPLETE (shipped); kept for design rationale.** The
two-path search is shipped: loader, all four CLI tools (bnc, bni,
bnlint, bootstrap), and the deprecated `Roots` field cleanup are
landed in both the binate and bootstrap repos.

**Outstanding (deferred):**
- **Stage 7**: env-var support
  (`BINATE_PACKAGE_INTERFACE_PATH` / `BINATE_PACKAGE_IMPL_PATH`, with
  `BINATE_BNI_PATH` / `BINATE_IMPL_PATH` short aliases). The original
  gate — "needs `bootstrap.Getenv`" — is gone: the Go bootstrap
  interpreter was retired. Env access goes through the public `os`
  package, NOT `pkg/std/os/sys` (that low-level libc-syscall layer is
  boundary-private — the `os-sys-consumers` hygiene check forbids any
  importer outside the `os` family). Concretely a new
  `os.Getenv` (built on the safe `os.Env()` snapshot, not a mutable
  libc `getenv`) is added; bni and bnlint call it. cmd/bnc, compiled
  by the frozen BUILDER whose bundled `os.bni` predates `os.Getenv`,
  reads `os.Env()` directly for now (TODO: switch to `os.Getenv`
  after the next `BUILDER_VERSION` bump). The finalized semantics are
  in "Stage 7" below.
- **Stage 8** (Phase 2): binary `.o`/`.a`/`.so` artifacts on
  IMPL_PATH. Tied to having a stable per-package ABI/linker
  contract. Still genuinely deferred.

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
enough).

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

Each flag takes a colon-separated list (so a single flag can express
the whole path), and is repeatable (so `--bni-path A --bni-path B`
is equivalent to `--bni-path A:B`).

**Decision**: support both forms. Lead with the short C-compiler
forms for ergonomics, support the verbose forms for clarity:
- `-I A` / `--interface-path A:B:C` (interface; like `-I` for headers
  in cc)
- `-L A` / `--impl-path A:B:C` (impl; like `-L` for libraries in cc)

`-I` / `-L` are familiar to anyone who's used a C toolchain and track
the same conceptual split (interface vs library).
`--interface-path` / `--impl-path` are the self-documenting forms.

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

**Decision**: standardize on `--word` for long flags. `-word` forms
in bni and bootstrap stay accepted as aliases for backward compat.

The reason for `--` (not `-`): single-dash is reserved for short
flags, including the conventional combinable form like `-abc`
(equivalent to `-a -b -c`). Reserving `-` for shorts keeps that
door open without ambiguity.

Existing one-off short flags (`-c`, `-o`, `-g`, `-v`, plus the new
`-I`, `-L`) stay single-dash and are eligible for `-abc`-style
combination later.

## Path syntax

- Colon-separated, like POSIX `PATH`. (Windows would use `;` —
  defer that decision; Binate has no Windows story today.)
- Empty entries (`A::B`) are skipped silently. Trailing colon is
  benign.
- Relative paths resolved against the current working directory at
  flag/env parse time, not at search time. Document this.
- No tilde expansion at the language level — leave it to the shell.

## Stage 7: Env vars

The original framing here — "`pkg/bootstrap` doesn't expose `getenv`,
so gate env support on adding `bootstrap.Getenv`" — is obsolete. The
Go bootstrap interpreter (and `pkg/bootstrap`) is retired; the tools
now read the environment through `pkg/std/os/sys.Getenv`
(`func Getenv(name) (@[]char, bool)`), which bnc, bni, and bnlint all
already have in their import graph (each imports `pkg/std/os`, which
imports `os/sys`). So env support is just CLI plumbing.

### Variables

- Interface path: `BINATE_PACKAGE_INTERFACE_PATH` (long, primary),
  `BINATE_BNI_PATH` (short alias).
- Impl path: `BINATE_PACKAGE_IMPL_PATH` (long, primary),
  `BINATE_IMPL_PATH` (short alias).

Each is a colon-separated list, same syntax as the `-I` / `-L` flag
values (empty entries dropped, trailing colon benign) — the same
`splitColon` used for the flags parses them.

### Precedence (per path, resolved in each tool's `main`)

1. **CLI wins per path.** If a tool got any `-I` / `--interface-path`
   entry, the interface env vars are ignored entirely; likewise `-L`
   for the impl path. The two paths are independent — `-I` on the CLI
   with `BINATE_PACKAGE_IMPL_PATH` in the env is a normal
   cross-config case (CLI interface path + env impl path).
2. **Long form wins over the short alias.** If
   `BINATE_PACKAGE_INTERFACE_PATH` is *set* (present in the
   environment, even to the empty string) its value is used and
   `BINATE_BNI_PATH` is not consulted. A present-but-empty long form
   therefore means "no env paths," not "fall through to the alias."
   No warning is emitted when both are set; the long form just wins.

The `--root` / `-add-root` "sugar for both paths" discussed elsewhere
in this doc no longer exists — it was removed in favor of plain
`-I` / `-L` (in bni and bnlint the first `-I` entry additionally acts
as the implicit source root). So the old "`--root` always wins over
env" clause is moot; env fallback keys purely off whether the CLI
supplied that specific path.

### Where it lives

The precedence decision is a small pure function (`envPathList`,
taking the two lookup results so it is unit-testable — the environment
is a process-wide snapshot with no per-variable setter, so it can't be
staged inside a unit test), wrapped by a thin `envPaths` that reads
the environment. Each tool carries its own copy (the same per-tool
duplication already used for `splitColon` / `streq`), and applies the
fallback right after `parseArgs`, before it assembles the loader
search paths. `parseArgs` itself stays a pure function of its
arguments (bnlint's "an interface path is required" check moves out of
`parseArgs` into `main`, after the env fallback, so an env-supplied
interface path satisfies it).

Env reads go through the public `os` package. `os.Getenv(name)` is
added, implemented over the `os.Env()` snapshot (safe — an immutable
shared list seeded from envp at startup — rather than a mutable libc
`getenv`). bni and bnlint call `os.Getenv`. cmd/bnc is compiled by
the frozen BUILDER, whose bundled `os.bni` predates `os.Getenv`, so it
cannot call the new symbol yet; it scans `os.Env()` directly via a
local `envLookup` helper (a temporary duplicate of `os.Getenv`, with a
TODO to collapse it after the next `BUILDER_VERSION` bump). Two
follow-up TODOs are tracked in `claude-todo.md`: switch cmd/bnc to
`os.Getenv`, and remove the unsafe `pkg/std/os/sys.Getenv`, routing
its one remaining consumer (`os/process/lookpath`) through `os.Env()`
too.

End-to-end coverage is `e2e/env-paths.sh`: it builds all three tools
from source (the frozen BUILDER predates the feature) and asserts a
split-root fixture resolves purely from the env vars, that `-I`/`-L`
override a bogus env value, and that the short aliases work.

## Stage 8 (Phase 2): Binary impl artifacts

Once `.o`/`.a`/`.so` are accepted on `IMPL_PATH`:

- `hasImplFiles(dir)` becomes "has at least one of {.bn, .o, .a, .so}".
- A directory mixing `.bn` and `.o` requires a precedence rule.
  Probably: `.o`/`.a`/`.so` win over `.bn` (you asked for the
  precompiled artifact; ignore the source). With a `--prefer-source`
  flag for explicit override.
- Linker integration: bnc gathers the binary artifacts from
  IMPL_PATH and feeds them to clang/`ld` automatically (today the
  user supplies them via `--cflag`).

## Resolved decisions

- **`--root` was removed** (superseding the earlier "stays"
  decision): the tools use plain `-I` / `-L`, and in bni / bnlint the
  first `-I` entry doubles as the implicit source root. Env fallback
  therefore keys off `-I` / `-L` presence, not a root flag.
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
- **Env var names** — RESOLVED (see Stage 7): support both, long form
  primary (`BINATE_PACKAGE_INTERFACE_PATH` / `_IMPL_PATH`) and short
  alias (`BINATE_BNI_PATH` / `BINATE_IMPL_PATH`), with the long form
  winning when both are set.
- **Phase 2 timing**: when does binary-artifact support land? Tied
  to having a stable per-package ABI / linker contract.
