# Plan: `pkg/stdx/flags` — a command-line flag parser for the stdlib

Status: design ratified; implementation in progress.

## Goal

A single, small, standards-track command-line parser under `pkg/stdx/flags`
(tier-1x — bundled, no inter-version compat guarantee) that every `cmd/*` tool
can adopt, replacing the five hand-rolled, copy-pasted parsers in the tree.

Home / layout (ifaces/impls split, per `pkg-layout-spec.md`):

- interface: `ifaces/stdlib/pkg/stdx/flags.bni`
- impl: `impls/stdlib/pkg/stdx/flags/` (flat — no platform variants)
- import path: `pkg/stdx/flags`

## Motivation

All five tools (`bnc`, `bni`, `bnas`, `bnlint`, `bnfmt`) hand-roll a `for i` +
`streq` loop and copy-paste the same primitives:

- `progArgs()`/`bniArgs()` (argv-from-`os.Args` copy) — duplicated ×5.
- `streq`/`charsEqual` (identical body) — ×5.
- `splitColon` (the `:`-list splitter for `-I`/`-L`) — ×3.
- a per-tool `append*` string-slice helper — one each.

There is no shared arg-parsing abstraction today; this package is greenfield and
absorbs all of the above.

## Ratified design decisions

1. **Go-style, dash-insensitive.** `-x` and `--x` are the same flag; a flag is a
   primary *name* plus an optional one-char *alias*, each matchable with either
   dash. (This collapses bni's `-verbose`/`--verbose` spellings to a single
   name, and models bnc's `-test`/`--test`, `-g`/`--debug`, `-I`/
   `--interface-path` uniformly.) No GNU getopt: **no** `-abc` bundling, **no**
   attached `-oX` short values.
2. **No interspersed positionals.** Parsing stops at the first positional token
   (or at `--`); that token and everything after it are the returned `rest`.
   Verified safe across the tree: all 144 compile/tool invocations in
   `scripts/`/`conformance/`/`e2e/`/`perf/` are flags-first, positional(s)-last;
   nothing puts a flag after a source file. The stop point doubles as the
   subcommand hand-off (a consumer dispatches on `rest[0]` and re-parses
   `rest[1:]` with a second `FlagSet`). Interspersing is intentionally dropped —
   it invites `foo *`-style flag injection from globs.
3. **Destination-pointer binding.** `fs.StringVar(&r.Output, name, alias, def,
   usage)` writes through a raw pointer to the consumer's result struct — a
   near-mechanical replacement for the existing struct-filling loops. (Rejected
   alternatives: query-by-name `fs.GetString("output")`; add-flag-returns-a-
   getter.)
4. **Strict unknown-flag policy only.** An unrecognized `-`/`--` token is an
   error. Safe because real positionals (filenames, package paths) never start
   with `-`. No lenient (unknown→positional) mode; bnc/bnlint's current lenient
   swallow is not reproduced.
5. **Missing-value is an error only.** A value flag that is the last token (no
   value follows) errors. This subsumes bnc's current silent-ignore, which is a
   latent bug (`bnc -o` → silently no output path). Minor, intended bnc
   behavior change on adoption.
6. **Value forms:** `--name value`, `--name=value`, `-a value`, `-a=value`. A
   value flag consumes the literal next token as its value (even if it looks
   like a flag), matching today's `i++` behavior. Bool flags never consume the
   next token; bare `--flag` sets true, `--flag=true|false` is accepted.
7. **`--` terminator.** Consumed; forces the remainder into `rest` even if a
   token looks like a flag (lets a `-`-leading positional through). bni's
   program-args passthrough is exactly this.

### Flag kinds (v1)

| Kind | Registrar | Dest | Repeat |
|---|---|---|---|
| bool | `BoolVar(p *bool, …)` | `*bool` | idempotent (last `=value` wins) |
| string | `StringVar(p *@[]char, …, def, …)` | `*@[]char` | last-wins |
| int | `IntVar(p *int, …, def, …)` | `*int` | last-wins |
| uint | `UintVar(p *uint, …, def, …)` | `*uint` | last-wins |
| string-list | `StringListVar(p *@[]@[]char, …)` | `*@[]@[]char` | accumulates |
| string-list (split) | `StringListSepVar(p *@[]@[]char, …, sep, …)` | `*@[]@[]char` | accumulates, each occurrence split on `sep` |

Int/Uint are included as table stakes even though no current tool needs them;
they parse decimal and error on a bad value. `StringListSepVar` folds the
tree's triplicated `splitColon` (the `:`-split of `-I`/`-L`) into the flag def.

### API surface (`.bni` sketch)

```binate
package "pkg/stdx/flags"

// Value is a settable flag target; a concrete impl holds a raw pointer to the
// consumer's destination and writes through it. Set returns false on a parse
// failure (e.g. a bad integer). IsBool marks flags that take no separate value.
interface Value {
    Set(s @[]char) bool
    IsBool() bool
}

func New(name @[]char) @FlagSet

func (fs @FlagSet) BoolVar(p *bool, name @[]char, alias @[]char, usage @[]char)
func (fs @FlagSet) StringVar(p *@[]char, name @[]char, alias @[]char, def @[]char, usage @[]char)
func (fs @FlagSet) IntVar(p *int, name @[]char, alias @[]char, def int, usage @[]char)
func (fs @FlagSet) UintVar(p *uint, name @[]char, alias @[]char, def uint, usage @[]char)
func (fs @FlagSet) StringListVar(p *@[]@[]char, name @[]char, alias @[]char, usage @[]char)
func (fs @FlagSet) StringListSepVar(p *@[]@[]char, name @[]char, alias @[]char, sep @[]char, usage @[]char)

// Parse consumes flags from args (already argv-minus-slot-0), stops at the
// first positional or `--`, and returns (positionals, err). err == "" on
// success; a non-empty err is a ready-to-print message (unknown flag, missing
// value, bad int). The consumer decides whether to print Usage() and exit.
func (fs @FlagSet) Parse(args @[]@[]char) (@[]@[]char, @[]char)

// Usage renders one line per registered flag (name, alias, value placeholder,
// usage text) under a header naming the program.
func (fs @FlagSet) Usage() @[]char
```

`--version`/`-h` need no special pre-scan: register them as ordinary bool flags
and check them before any positional-count validation (Parse never requires
positionals).

## BUILDER-compilability

`cmd/bnc` is compiled by the pinned BUILDER (`bnc-0.0.13`), so the package —
being a `cmd/bnc` dependency once bnc adopts it — must stay BUILDER-compilable.
The current bnc tree already proves the BUILDER accepts **generics**
(`slices.Append[T]`), **interfaces** (`impl *llvmBackend : Backend`), and
**variadics-over-interface** (`fmt.Print(args ...*any)`). This package uses only
an interface (`Value`) with concrete impls holding raw `*T`, plus `for`/`streq`
loops and `make_slice`+copy appenders — no closures, method values, or floats.
Confirm any newer construct directly against the BUILDER before use.

## Conversion plan (after the package is green)

Adopt one tool at a time, each its own commit (tests stay green between):

1. `bnfmt` — smallest (`-w`, `--check`, files). Proof of the API.
2. `bnas` — `-o`, `-arch`, one positional.
3. `bnlint` — `-I`/`-L` (`:`-split), `--target`, `--tests`, pkgs; error-return style already matches.
4. `bni` — `-x` script mode + `--` program-args passthrough (uses the stop-at-first-positional split; keeps a thin tail handler for ProgArgs).
5. `bnc` — BUILDER-compiled; the headline. Replaces `parseArgs`/`CLIArgs`
   filling with registrations; drops `splitColon`/`appendRawCharSlice`/local
   `streq`. Requires a smoke over its whole BUILDER-compiled tree.

Each conversion may need small consumer changes (e.g. bnc's missing-value now
errors; `--version` becomes a normal flag).

## Out of scope for v1

GNU bundling (`-abc`), attached-short (`-oX`), built-in subcommand *dispatch*
(only the stop-at-first-positional primitive is provided), response files
(`@file`), float flags, generic `List[T]`. Each is additive later if a consumer
needs it.
