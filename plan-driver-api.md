# Public driver-API (Step 7 follow-up)

Status: **LANDED 2026-09-02, commit `6efec9cf1`.**  The landed approach is NOT the
`pkg/bnld` facade this doc first explored — it is simpler: **ship `pkg/binate/link.bni`
itself** (keep the name; it's namespaced under `pkg/binate/`).  Follow-up to the
interpreted linker drivers (Step 7, `bnld -driver`; see plan-step7-driver.md).  Goal: a
PUBLIC interface driver authors import, so a driver can build against a *released*
toolchain.

## What actually landed (`6efec9cf1`) — read this first

- `pkg/binate/link.bni` moved into a new **`ifaces/toolchain/`** tier so a release bundle
  ships it (make-bundle `cp -R`'s `ifaces/`).  The impl stays at `pkg/binate/link/*.bn`
  (tier 2, unshipped); bnld injects the compiled instance into an interpreted driver, so a
  driver only needs the shipped interface to type-check.
- `ifaces/toolchain` wired into `scripts/binate-paths.sh` (covers `--base $BINATE_DIR` +
  the bundle) and `--prepend`ed into EVERY BUILDER-based bnc/bnld compile site (the 7 build
  scripts, the shared `build-compilers.sh:build_gen1`, and 6 inline-stage-1 e2e scripts).
- No facade, no rename, no types split — those were RULED OUT (see below): Binate rejects
  import cycles, so a public package that forwards to a kept-internal `link` can't work;
  and the user chose to keep the name `link` and just ship its `.bni`.
- `drivers/elf.bn` stays in the binate repo for now (exercised by
  `e2e/bnld-driver-linux.sh`); its move to the external `examples` repo waits on a release
  that ships `link.bni`.

The sections below are the SUPERSEDED design exploration (the `pkg/bnld` facade, the alias
direction, the import-cycle finding, MOVE-vs-SPLIT) — kept for rationale on why the simpler
"ship `link.bni`" approach won.

## Original goal (superseded framing)

A stable, PUBLIC, bnld-specific package that driver
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

## FINAL DECISION (2026-09-02) — keep `pkg/binate/link`, ship its `.bni`

Neither MOVE nor SPLIT is needed.  The cycle problem only arose from having a SECOND package
forward to link; with a single package there is no forwarding and no cycle.  The plan:

- **Keep the package as `pkg/binate/link`** (the name `link` is fine — it's namespaced under
  `pkg/binate/`, so not a generic "linker").  No rename, no facade, no `bnldabi`/`bnldapi`.
- **Move `pkg/binate/link.bni` into the shipped `ifaces/` subtree** (proposed
  `ifaces/tools/pkg/binate/link.bni` — a dedicated tier, consistent with the earlier
  tools-tier choice) so `make-bundle`'s `cp -R ifaces/` ships it.  The impl stays at
  `pkg/binate/link/*.bn` (compiled into bnc/bnld, injected, NOT shipped); the loader pairs
  `.bni` (BniPath) and impl (ImplPath) via independent search loops, so split locations are
  fine.  Wire `ifaces/tools` into `scripts/binate-paths.sh`'s iface list so BOTH the bnc
  self-build and driver builds resolve `import "pkg/binate/link"` from it (it leaves
  `pkg/binate/link.bni`'s old repo-root location, so the ifaces/tools copy is the single
  source of truth).
- **No split.**  Verified `link.bni`'s entire public surface (the 4 `Input*` types, `EM_*`
  consts, `ReadObject`/`ReadArchive`, `Link`/`LinkDynElf`/`LinkDynMacho`) is driver-facing,
  and bnc/bnld use only a subset of it — nothing internal-only is exported, so there's no
  bnc-only surface to hold back.  (If that changes later — an internal-only decl appears in
  `link.bni` — revisit: split a public `link.bni` from an internal one.)
- **Migrate `drivers/elf.bn` → `examples/`** (it already imports `pkg/binate/link`; no import
  change).  Point the driver e2e's `-I` at the shipped location (via binate-paths).
- Injection is unchanged: bnld already injects `link.__Package()`; a driver's `link.*` calls
  resolve to the injected compiled instance.  (`linkInjectSet()` stays `StandardPackages()+1`.)

The rest of this doc (below) records the superseded MOVE/SPLIT exploration and the cycle
finding that ruled out a forwarding facade.

## Alias DIRECTION + the import-cycle constraint (user correction 2026-09-02)

The user corrected the alias direction: the INTERNAL package must alias the PUBLIC types
(`link.InputObject = bnld.InputObject`), NOT the reverse — because `pkg/binate/link` (and
its `.bni`) will NOT ship, so the public types must be OWNED by the shipped package.

But **Binate's loader rejects package import cycles** (`loader_load.bn` emits
`cycleErrorMsg`).  So the literal "bnld FORWARDS to link" (bnld→link) + "link ALIASES bnld's
types" (link→bnld) is a rejected `pkg/bnld` ↔ `pkg/binate/link` cycle.  An acyclic design
that ships only `pkg/bnld.bni` and exposes both the types and the functions must break the
cycle.  Two shapes do:

**★ Shape MOVE — one public package; the linker BECOMES `pkg/bnld`.**
Rename `pkg/binate/link` → `pkg/bnld` (package name `bnld`): the 8244-line linker's `package`
decl changes in ~20 files (files within a package don't self-qualify, so no internal churn),
its `.bni` ships in the tools tier, and the 4 external callers switch `link.*`→`bnld.*`.  No
internal package remains (or a thin leaf-ward `pkg/binate/link` alias-shim that imports bnld,
only if some internal caller is left unmigrated).  Cleanest END STATE (single public package
owns types+funcs+impl), but re-homes a large BUILDER-surface package (update CLAUDE.md's
BUILDER list; settle where an iface-shipped/compiled-in package's source physically lives).

**★ Shape SPLIT — keep the linker in place; add two tiny public packages.**
- `pkg/bnldabi` (public LEAF, ships `.bni`; no imports): OWNS the `Input*` types + `EM_*`
  consts.
- `pkg/binate/link` STAYS put (8244 lines unmoved): stops defining the types, instead imports
  `pkg/bnldabi` and uses/aliases them (`type InputObject = bnldabi.InputObject` — the
  "internal aliases public" the user asked for).  `link.bni` still does NOT ship.
- `pkg/bnld` (public facade, ships `.bni`): re-exports the types by alias
  (`type InputObject = bnldabi.InputObject`) and FORWARDS the 5 functions to link.
- Dependency graph `bnld → {link, bnldabi}`, `link → bnldabi`, `bnldabi → ∅` — ACYCLIC.
  Minimal risk (linker untouched), but TWO public packages + a forwarding facade.

Recommendation: **SPLIT** — it honors "internal aliases public / only bnld ships" with a
tiny, low-risk diff to the 8244-line linker (swap 4 struct defs + 2 consts for an import),
vs MOVE re-homing the whole package.  The cost is one extra public leaf (`pkg/bnldabi`).
(If a single public package is preferred over two, MOVE is the way, accepting the big rename.)

Type-DECOUPLING (public types as DISTINCT structs from link's, with boundary conversion) is
NOT proposed for either shape — both use aliases so there's zero conversion; the public types
ARE link's types.  Revisit only if link's internals must later diverge from the public ABI.

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
