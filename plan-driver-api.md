# Public driver-API package (Step 7 follow-up)

Status: DESIGN (2026-09-02).  A follow-up to the interpreted linker drivers (Step 7,
`bnld -driver`; see plan-step7-driver.md).  Goal: a stable, PUBLIC package that driver
authors import, so standard drivers can live in `examples/` and survive a released
toolchain — instead of importing the internal `pkg/binate/link`.

## Problem

A driver today does `import "pkg/binate/link"` and calls `link.LinkDynElf(...)`; bnld
injects link's compiled instance as an extern so those calls run compiled.  Two issues
with that being the driver-facing contract:

1. **Not shipped.**  `pkg/binate/link.bni` is an internal compiler-tree interface; a
   released toolchain bundle ships the stdlib/runtime ifaces, not `pkg/binate/*`.  So a
   driver in `examples/` (which builds against a *released* bnc) can't resolve
   `pkg/binate/link` at type-check time.  This is exactly why the Step 7 spike put its
   reference driver in the binate repo, not examples/.
2. **Not stability-committed.**  `pkg/binate/link`'s API is the internal linker's own
   surface; it changes as the linker evolves.  A user's custom driver pinned to it would
   break on internal refactors.

## Options

- **(A) Facade package (recommended).**  A small PUBLIC package — e.g. `pkg/std/linker`
  — that re-exports a CURATED, stability-committed subset of the linker API, forwarding to
  the internal `pkg/binate/link`.  Drivers import the facade; bnld injects the facade's
  compiled instance (which forwards to link).  The facade's `.bni` ships with the toolchain
  (it lives under the stdlib ifaces).  Decouples the driver contract from the internal API.
- **(B) Move the API into a public package.**  Relocate the driver-facing entry points out
  of `pkg/binate/link` into the public package; link keeps only internals.  Cleaner
  long-term but a bigger refactor (cmd/bnc + cmd/bnld call link directly), and churns a
  package that just stabilized.
- **(C) Ship `pkg/binate/link.bni` as-is.**  Add the internal link ifaces to the shipped
  bundle and let drivers import `pkg/binate/link` directly.  Simplest, but exposes the
  internal, un-curated API as the public contract (no stability boundary) — rejected.

## Recommendation: (A) facade

### v1 surface (matches the ratified Step 7 v1 driver API — high-level only)

Expose ONLY the whole-link entry points, whose signatures use just simple values (char
slices / int / uint64) — so the facade's `.bni` is SELF-CONTAINED (it names no
`pkg/binate/link` type, so link.bni need not ship):

    func Link(inputPaths @[]@[]char, entry *[]readonly char, machine int, base uint64,
            out *[]readonly char) @[]readonly char
    func LinkDynElf(inputPaths @[]@[]char, entry *[]readonly char, machine int, base uint64,
            out *[]readonly char, sharedLibPaths @[]@[]char) @[]readonly char
    func LinkDynMacho(inputPaths @[]@[]char, entry *[]readonly char, machine int, base uint64,
            out *[]readonly char) @[]readonly char
    // + the EM_X86_64 / EM_AARCH64 machine consts (re-exported).

Each facade function is a one-line forward to the `pkg/binate/link` function of the same
name.  `ReadObject`/`ReadArchive` and the `Input*` types (which WOULD drag link's types
into the public `.bni`) are deferred to a v2 that also decides how the public types are
declared — same boundary as the Step 7 v1-vs-v2 split.

### Injection + shipping

- bnld's `-driver` path injects the facade's `__Package()` descriptor (plus `pkg/binate/link`
  as its compiled dependency — already injected today).  A driver's `linker.LinkDynElf(...)`
  runs the facade forward → compiled link.
- The facade's `.bni` (and `.bn`) ship in the toolchain bundle's stdlib ifaces/impls, so a
  released bnc type-checks a driver that imports it.  (Confirm the bundle's iface/impl set
  picks up a new `pkg/std/*` package — scripts/make-bundle.sh + binate-paths.sh.)
- `drivers/elf.bn` migrates to import the facade and moves to `examples/` (a driver becomes
  a normal example, buildable with a released toolchain).

## Open questions (for sign-off)

1. **Package name / namespace:** `pkg/std/linker`?  `pkg/std/bnld`?  a non-`std` public
   namespace?  (Affects where it ships and how it's imported.)
2. **v1 surface:** the three `Link*` entry points + machine consts only (recommended),
   deferring `ReadObject`/primitives + public `Input*` types to v2?
3. **Does the released bundle already ship arbitrary `pkg/std/*` ifaces/impls,** or does
   make-bundle.sh need a tweak to include the new package?
4. **Migrate `drivers/elf.bn` to `examples/` in this change,** or leave it in the binate
   repo importing the facade and migrate separately?
