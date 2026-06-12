# Plan: Deprecate `pkg/binate/buf` in favor of `pkg/std/strings.Builder`

**Status:** in progress (started 2026-06-12). **Goal:** retire the bespoke
`pkg/binate/buf.CharBuf` growable byte-buffer so the project has a single
byte-buffer abstraction — the stdlib `pkg/std/strings.Builder` — instead of
two.

## What's already landed

- `pkg/std/strings.Builder` — `Write` / `WriteByte` / `Grow` / `Len` / `Cap`
  / `Reset` / `String`; implements `io.Writer` + `io.ByteWriter`.
- `pkg/binate/stringutils` (binate `04c67dd3`, 2026-06-12) — the Builder-method
  gap as free functions over `*strings.Builder`: `WriteInt`, `WriteInt64`,
  `WriteHexByte`, `Freeze`. Free functions (not Builder methods) keep the
  stdlib type minimal.
- The cross-package `.bni`-impl-registration bug that blocked using a Builder
  *through* `io.Writer` from another package is resolved (binate `3d147369`),
  so the interface route works if a caller needs it. (Most buf callers use the
  buffer directly, not through an interface.)

## API mapping (buf.CharBuf → Builder / stringutils)

| `buf` | replacement | note |
|---|---|---|
| `buf.New()` | `strings.NewBuilder()` | returns `@strings.Builder`, not a value |
| `b.WriteStr(s)` | `b.Write(s)` | `char` is an alias for `uint8`, so a `*[]readonly char` IS a `*[]readonly uint8` |
| `b.WriteByte(c)` | `b.WriteByte(c)` | `char`/`uint8` interchangeable |
| `b.WriteInt(n)` | `stringutils.WriteInt(b, n)` | |
| `b.WriteInt64(n)` | `stringutils.WriteInt64(b, n)` | |
| `b.WriteHexByte(v)` | `stringutils.WriteHexByte(b, v)` | |
| `b.Len()` / `b.Len` | `b.Len()` | the field `.Len` has no Builder analogue — use the method |
| `b.Bytes()` → `@[]char` | `b.String()` → `@[]readonly char` | **readonly** — see Open Decision 2 |
| `b.Freeze()` | `stringutils.Freeze(b)` | owned, exactly-sized copy |
| `buf.CopyStr(s)` | — | **no home yet** — see Open Decision 1 |
| `buf.Concat(a, b)` | — | **no home yet** — see Open Decision 1 |

## The main migration cost: value type → reference type

`buf.CharBuf` is a **value type**: every mutator returns the updated buffer and
callers thread it (`b = b.WriteStr(...)`), and writes chain
(`b.WriteStr(..).WriteInt(..)`). `strings.Builder` is a **managed reference**
(`@Builder`) mutated **in place** (`b.Write(...)`), and its mutators return void
/ error, so they don't chain. Migration is therefore NOT a symbol rename:

- `var b buf.CharBuf = buf.New()` → `var b @strings.Builder = strings.NewBuilder()`
- `b = b.WriteStr("x")` → `b.Write("x")` (drop the reassignment)
- `b.WriteStr(a).WriteInt(n)` → two statements
- functions that take/return a `CharBuf` by value to thread it need their
  signatures reworked to take a `*strings.Builder` and mutate in place

This value→reference rewrite — not the missing helpers — is the bulk of the
work, and it touches the highest-count sites (see inventory).

## Inventory (repo-wide grep, 2026-06-12)

Call-site counts (non-declaration): `WriteStr` 2956, `Bytes` 718, `New` 700,
`WriteInt` 423, `WriteByte` 281, `CopyStr` 168, `Concat` 103, `Freeze` 8,
`WriteInt64` 2, `WriteHexByte` 1.

### Cone classification — the release gate

Per the project owner: **in-cone buf callers cannot migrate until the next
BUILDER release.** Migrating an in-cone caller makes it import
`stringutils` → `strings`, which pulls those into `cmd/bnc`'s build cone; the
BUILDER must then compile them as cone dependencies. The current BUILDER is
`bnc-0.0.8`; the tree is `bnc-0.0.9-pre`. So in-cone migration is gated on a
BUILDER built from a tree that can carry the Builder as a cone dependency
(≥ `bnc-0.0.9`, post-`3d147369`). **Before starting any in-cone work, confirm
the exact gate with a trial cone build against the new BUILDER** — don't assume
the mechanism; verify it compiles.

- **OUT of cone — migratable NOW:** `cmd/bni`, `cmd/bnlint`, `cmd/bnas`,
  `pkg/binate/lint`, `pkg/binate/repl`, `pkg/binate/vm`. (Verified: `lint` is
  imported only by `bnlint`; `asm/parse` only by `bnas`; `cmd/bnc` imports none
  of vm/repl/lint.)
- **IN cone — gated on the release:** `cmd/bnc` and `pkg/binate/{ast, codegen,
  debug, ir, lexer, loader, mangle, native, parser, token, types, asm}` (but
  `asm/parse` is OUT — only `bnas` imports it). This is the bulk of the
  `WriteStr`/`Bytes`/`New` volume.

Classification rule: a package is in-cone iff reachable from `cmd/bnc`'s
imports; cross-check against the cone list in CLAUDE.md "Builder Compatibility
Constraint". **Re-derive the site list from a fresh repo-wide grep before each
migration batch** — concurrent commits add callers (the `binate-paths` sweep
got bitten by exactly this).

## Open decisions (USER-OWNED — not pre-resolved here)

1. **`CopyStr` / `Concat` have no home.** They are pure byte-slice utilities
   (clone a slice; concatenate two) with NO Builder/strings dependency, used in
   BOTH in-cone and out-of-cone code (168 + 103 sites; in-cone users include
   `asm/*`, `codegen`, `ir`, `lexer`, `native/*`, `token`, `types`, `cmd/bnc`).
   They therefore **cannot** move into `stringutils` (out of cone via the
   `strings` dep) — in-cone callers need them. Options:
   - (a) a small BUILDER-compilable utility package (e.g. `pkg/std/bytes` with
     `Clone` / `Concat`, or `pkg/binate/byteutil`) importable in-cone;
   - (b) shrink `buf` to a thin slice-utility shim holding only these two (buf
     survives, much smaller);
   - (c) fold into an existing stdlib package.
   This is a **separate workstream** and is arguably the harder gate on *fully*
   retiring `buf`: the Builder piece is gated on the BUILDER release, but
   `CopyStr`/`Concat` are gated on this decision (and, like the Builder piece,
   their in-cone callers can only switch after the release).

2. **`Bytes()` is mutable (`@[]char`); `String()` is readonly
   (`@[]readonly char`).** 718 `Bytes()` sites. If any caller MUTATES the
   returned slice, `String()` won't substitute — those need
   `stringutils.Freeze` (an owned mutable copy) or a dedicated accessor. Needs
   an audit of whether `Bytes()` returns are read-only in practice, and a
   decision on whether to add a mutable accessor.

## Proposed sequencing (pending the decisions above)

1. ✅ `stringutils` formatting/Freeze helpers — done (`04c67dd3`).
2. Decide the `CopyStr`/`Concat` home (Decision 1); land that package/shim.
3. Migrate **out-of-cone** callers (`cmd/bni`, `cmd/bnlint`, `cmd/bnas`,
   `lint`, `repl`, `vm`): value→reference rewrite + helper swap; audit
   `Bytes()`/mutation per package (Decision 2). One package per commit, tree
   green throughout.
4. **After the next BUILDER release:** trial cone build with the Builder
   dependency; if green, migrate **in-cone** callers in batches (by package),
   re-grepping each batch.
5. Remove `pkg/binate/buf` once its call count reaches zero.

Each step keeps the tree green and is independently landable. Steps 3–5 are
large; do NOT treat any sub-step as "deferred"/"non-goal" without an explicit
decision — surface the real effort and let the owner scope it.
