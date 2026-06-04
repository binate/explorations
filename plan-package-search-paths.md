# Plan: Two-path package resolution (interface + impl)

## Status

**Phase 1 COMPLETE (shipped); kept for design rationale.** The
two-path search is shipped: loader, all four CLI tools (bnc, bni,
bnlint, bootstrap), and the deprecated `Roots` field cleanup are
landed in both the binate and bootstrap repos.

**Outstanding (deferred):**
- **Stage 7**: env-var support
  (`BINATE_PACKAGE_INTERFACE_PATH` / `BINATE_PACKAGE_IMPL_PATH`).
  Gated on adding `bootstrap.Getenv`. Direct CLI is sufficient for
  now since cross-compile drivers can construct command lines.
- **Stage 8** (Phase 2): binary `.o`/`.a`/`.so` artifacts on
  IMPL_PATH. Tied to having a stable per-package ABI/linker
  contract.

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

## Stage 7: Env vars (and their absence in pkg/bootstrap)

`pkg/bootstrap` doesn't expose `getenv` today. Two reasonable paths:

- **Stage 0**: ship CLI flags first; env-var support is a second
  step gated on adding `bootstrap.Getenv`. The CLI is sufficient
  for cross-compilation drivers that already construct command
  lines.
- **Add `bootstrap.Getenv` now** as a tiny addition to the bootstrap
  surface (a handful of lines in C and the Go interpreter). Then env
  + CLI ship together.

Recommendation: **Stage 0 first** (this shipped — CLI only),
evaluate adding `bootstrap.Getenv` based on whether direct shell
invocations of bnc/bni need env-var support before the CLI is enough.

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
