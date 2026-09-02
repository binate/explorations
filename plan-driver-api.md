# Public driver-API package `pkg/bnld` (Step 7 follow-up)

Status: DESIGN — decisions taken 2026-09-02, pending final sign-off on two sub-choices
(marked ★ below).  Follow-up to the interpreted linker drivers (Step 7, `bnld -driver`;
see plan-step7-driver.md).  Goal: a stable, PUBLIC, bnld-specific package that driver
authors import, so standard drivers can live in `examples/` and build against a *released*
toolchain — instead of importing the internal `pkg/binate/link`.

## Decisions (user, 2026-09-02)

- **Name / namespace:** NOT a standard-library package (not under `pkg/std/*`), even though
  it ships.  Must reference **bnld** specifically (it's the bnld linker's API, not "any
  linker").  → import path **`pkg/bnld`**.
- **Surface:** the FULL current link public surface — `Link` / `LinkDynElf` / `LinkDynMacho`
  **and** `ReadObject` / `ReadArchive` **and** the `Input*` types (`InputObject`,
  `InputSection`, `InputSymbol`, `InputReloc`) + the `EM_*` machine consts.  (Not the
  high-level-only v1 originally sketched.)
- **Migrate `drivers/elf.bn` → `examples/` in this change** (importing `pkg/bnld`), proving
  a driver builds against a released-style toolchain.

## Findings that constrain the design

1. **Bundle layout (scripts/make-bundle.sh, binate-paths.sh).**  A release bundle ships
   `lib/ifaces/{core,stdlib}/` and `lib/impls/{core,stdlib}/` (plus `runtime/`), by
   `cp -R`ing the source `ifaces/` and `impls/` trees.  **`pkg/` is never shipped** — it's
   the compiler's own source.  An import path maps directly to a file under a search-root:
   `import "pkg/std/os"` → `<root>/pkg/std/os.bni`, e.g. `ifaces/stdlib/pkg/std/os.bni`.
   So a shipped package's source lives under `ifaces/` + `impls/`, NOT under `pkg/`.
2. **Injection model (cmd/bnld/driver.bn).**  `bnld -driver` runs the driver in the
   interpreter with `linkInjectSet()` = `interp.StandardPackages() + link.__Package()` — it
   injects link's *compiled* instance.  The driver never *compiles* link; its `link.*` calls
   resolve to the injected compiled package.  Typechecking the driver needs only the `.bni`
   on the `-I` path (bnld's `-I/--interface-path` flags).
3. **`pkg/bnld` is inherently driver-only.**  Its impl forwards to `pkg/binate/link`, which
   is the compiler-tree linker and is NOT shippable as stdlib.  So a normal user *program*
   can't compile `import "pkg/bnld"` (no shippable impl).  It's usable only where bnld
   injects the compiled instance — i.e. inside a driver.  ⇒ ship `pkg/bnld.bni` (for driver
   typecheck); do NOT ship `pkg/bnld.bn` (would drag in unshippable link); bnld carries the
   compiled `pkg/bnld` + `link` and injects both.
4. **Type aliases exist** (`type X = U`, spec 07-types.md).  link's `Input*` structs are
   self-contained value types (char/uint8 slices, ints, nested `Input*` slices — no
   external-package field types), so they can be re-exported by alias with zero conversion.

## Shape

`pkg/bnld` is a thin re-export of `pkg/binate/link`:

    // ifaces: ships as ifaces/<tier>/pkg/bnld.bni
    package "bnld"
    import "pkg/binate/link"
    const EM_X86_64  int = link.EM_X86_64
    const EM_AARCH64 int = link.EM_AARCH64
    type InputSection = link.InputSection      // type aliases: SAME types, no conversion
    type InputSymbol  = link.InputSymbol
    type InputReloc   = link.InputReloc
    type InputObject  = link.InputObject
    func ReadObject(path *[]readonly char) (@InputObject, @[]readonly char)
    func ReadArchive(path *[]readonly char) (@[]@InputObject, @[]readonly char)
    func Link(...) @[]readonly char
    func LinkDynElf(...) @[]readonly char
    func LinkDynMacho(...) @[]readonly char

    // impl (compiled INTO cmd/bnld, injected; NOT shipped)
    func ReadObject(path *[]readonly char) (@InputObject, @[]readonly char) {
        return link.ReadObject(path)           // one-line forwards
    }
    // ... etc

Because the types are ALIASES (`type InputObject = link.InputObject`), `pkg/bnld.InputObject`
IS `link.InputObject` — the forwards need no field copies, and `pkg/bnld.bni` referencing
`link` means **`pkg/binate/link.bni` must ship alongside `pkg/bnld.bni`** (as pkg/bnld's iface
dependency).  This is NAME stability (drivers import the blessed `pkg/bnld`, never
`pkg/binate/link` directly), not TYPE decoupling (see ★A).

### Wiring

- `cmd/bnld/driver.bn`: `linkInjectSet()` adds `bnld.__Package()` → `StandardPackages() + 2`
  (link + bnld); update `driver_test.bn`'s count assertion.
- Ship `pkg/bnld.bni` (+ its `link.bni` dep) in a shipped ifaces tier (see ★B).  Do NOT ship
  the impls.
- `drivers/elf.bn` → `examples/` importing `pkg/bnld`; e2e (`bnld-driver-linux.sh`) points
  `bnld -I` at the shipped tier.

## ★ Two sub-choices for sign-off

**★A — type exposure: ALIAS (recommended) vs REDECLARE.**
- *Alias* (above): `type InputObject = link.InputObject`.  Thin, zero conversion, but ships
  `link.bni` as pkg/bnld's dep and gives name-stability only (if link changes a struct,
  pkg/bnld's contract changes with it).
- *Redeclare*: pkg/bnld declares its OWN `Input*` structs; forwards deep-copy/convert at the
  boundary.  Self-contained `.bni` (link.bni need not ship), true type-decoupling — but
  ReadObject/ReadArchive must deep-copy nested managed slices on every call, ~150 more lines,
  and conversion is error-prone.  Recommendation: **alias for v1**; revisit decoupling if/when
  link's internal types need to diverge from the public ones.

**★B — shipping tier: dedicated `tools` tier (recommended) vs reuse `stdlib`.**
- pkg/bnld should NOT be reachable by a normal compile (it's driver-only, and its impl isn't
  shippable).  A dedicated tier — `ifaces/tools/pkg/bnld.bni` (+ the `link.bni` dep) — ships
  automatically (make-bundle `cp -R`s all of `ifaces/`) and is added to `-I` ONLY when
  building a driver (bnld's `-I` flags / the e2e), NOT to the default stdlib/core search
  list.  Keeps it out of the "standard library" bucket (honoring the naming decision) and
  prevents normal programs from importing an unbuildable package.
- Reusing `stdlib` would place it in `ifaces/stdlib/pkg/bnld.bni`, which is always on `-I`;
  a normal program could then typecheck `import "pkg/bnld"` and only fail at impl-resolution.
  Rejected for that reason.

## Open (verify during implementation)

- Confirm a new `ifaces/tools/` (and `impls/tools/`, if any impls ship — none here) is picked
  up by make-bundle's `cp -R` (it is) and that bnld's `-I` can point at it.
- Confirm `type X = otherpkg.Struct` cross-package alias resolves in a `.bni` (spec says yes;
  verify against the current bnc during the spike).
