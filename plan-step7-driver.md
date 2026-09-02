# Step 7 — Interpreted linker drivers (`bnld -driver custom.bn`)

Status: DESIGN (2026-09-01). The original goal of the bnld work: let a custom linker
*driver*, written in Binate and **interpreted at link time**, orchestrate a link by
calling the **compiled** `pkg/binate/link` library. The dual-mode showcase — interpreted
*policy* driving a compiled *mechanism* (the hot relocation/emit path stays native).

This doc proposes the design; it does not implement it. Open questions for
sign-off are collected at the end.

## Why

A custom driver lets a user change link *policy* — load base, section ordering, entry
symbol, output shape, target-specific quirks (embedded images, custom segments) — WITHOUT
recompiling the linker. The driver is a `.bn` file the compiled `bnld` loads and runs
under the bytecode VM; the O(sections) policy work is interpreted (fast enough — it runs
once per link), while the O(bytes) relocation patching and object emission stay compiled.

## Substrate that already exists

- **The VM is embeddable.** `cmd/bni` embeds `pkg/binate/vm` (`vm.NewVM(...)`), loads a
  `.bn` through the loader → `interp.TypecheckPackages` → `ir.InitModule` lowering →
  runs it. bnld can do the same.
- **VM → compiled interop via package descriptors.** `interp.RegisterStandardExterns` /
  `interp.InjectStdlibExterns` expose a COMPILED package to the VM by its
  `@reflect.Package` descriptor (`injectPure(vm, targets, path, descriptor())`): the
  package's functions/globals/vtables/sat-entries run as their one native instance, and
  interpreted bytecode that `import`s the package calls straight into compiled code. This
  is the exact mechanism a driver needs to reach `pkg/binate/link`.
- **The link library is already a clean library** with a small exported surface
  (`pkg/binate/link.bni`): `ReadObject`, `ReadArchive`, `Link`, `LinkDynElf`,
  `LinkDynMacho`, plus the `Input{Object,Section,Symbol,Reloc}` types.

What's missing: bnld has no `-driver` flag, and nothing injects `pkg/binate/link` as an
extern package for a driver to call.

## Proposed design

### `bnld -driver <driver.bn> [driver args...]`

`bnld` gains a `-driver` flag. When set, instead of running its built-in link path, bnld:

1. **Loads + typechecks + lowers** `driver.bn` (and its imports) via the same
   loader/checker/VM-lowering path `cmd/bni` uses.
2. **Injects the compiled instances** the driver may call as externs, by descriptor:
   `pkg/binate/link` (the linker library), plus the stdlib packages
   (`InjectStdlibExterns`) and the standard runtime externs
   (`RegisterStandardExterns`). So a driver's `import "pkg/binate/link"` +
   `link.LinkDynElf(...)` runs compiled.
3. **Runs the driver's entry**, passing the driver args, and maps its result to bnld's
   exit status.

### Driver entry contract (decision point — see open questions)

Two candidate shapes:

- **(A) `main`-shaped** — the driver is `package "main"` with `func main()`; it reads
  `os.Args()` (bnld forwards the post-`-driver` args) and calls `os.Exit`. Simplest,
  most flexible, and mirrors `cmd/bni` exactly (a driver is "a program with `link`
  injected"). Downside: no typed contract; the driver owns all argument parsing.
- **(B) typed `Drive` entry** — the driver exports
  `func Drive(objs @[]@[]readonly char, out *[]readonly char, target *[]readonly char)
  @[]readonly char` (returns "" or an error); bnld parses the CLI and calls it. A
  clearer contract, but rigid (bnld fixes the argument shape).

Recommendation: **(A)** for v1 — a driver is just a program that links, with the linker
library handed to it. It keeps bnld's job tiny (embed VM, inject link, run) and lets
driver authors evolve their own arg handling. A typed entry can be layered later.

### Driver API surface (decision point)

- **v1 — high-level, low interop risk.** Expose only what `link.bni` exports today:
  `Link` / `LinkDynElf` / `LinkDynMacho` (driver picks target/base/entry/mode) and
  `ReadObject` / `ReadArchive`. These cross only SIMPLE values at the VM↔compiled
  boundary — `@[]@[]char` (object paths), `int`/`uint64` (machine, base), and an
  `@[]readonly char` error return. No managed aggregate has to survive the boundary, so
  the interop surface is minimal. A v1 driver customizes *which* link to run and with
  what parameters.
- **v2 — primitive-level, richer + higher interop risk.** To let a driver COMPOSE the
  pipeline (custom layout, custom section order, custom emit), export the primitives
  (`Resolve`, `Layout`/`LayoutPaged`, `Relocate`, `EmitElfExec`/`EmitDynElfExec`/`Emit*Macho*`)
  and the `LayoutResult`/`SymbolTable` types. Now `@InputObject`, `@LayoutResult` etc.
  must cross the boundary in both directions — the harder interop case (managed structs,
  slices of managed pointers), which overlaps the open "compiler/interpreter interop"
  work (whole-program enumeration, descriptor Phase C). Defer until v1 proves the loop.

## Interop constraints / risks

1. **Boundary types.** v1 stays on simple scalars + char-slices, which the extern path
   already handles (the stdlib crosses these). v2's managed aggregates (`@InputObject`
   with nested `@[]InputSection` etc.) are the real risk — validate against the
   interop project's current limits before committing to v2.
2. **`link`'s own deps.** `pkg/binate/link` imports `asm/*`, `buf`, `sha256`, `os`, and
   stdlib containers. Injecting `link` by descriptor must also make its transitive
   compiled deps reachable (they're already in bnld, so injecting their descriptors —
   as the stdlib injection already does for the std set — should suffice; confirm the
   `asm/*` packages have descriptors and inject cleanly).
3. **`link` is BUILDER-compiled surface.** It already is (cmd/bnc embeds it). No new
   constraint, but the driver-facing API is now a semi-public interface — keep it stable.
4. **Error/fault surfacing.** A driver fault (VM_STATUS_FAULTED) must become a clean
   bnld error + non-zero exit, not a swallowed failure — reuse cmd/bni's fault handling.

## Incremental plan

1. **Spike:** `bnld -driver` embeds the VM, injects `pkg/binate/link` + stdlib, runs a
   trivial `driver.bn` whose `main` calls `link.LinkDynElf` on hardcoded inputs →
   produces a runnable ELF. Proves the interpreted-driver → compiled-link loop and
   surfaces the real injection wrinkles (descriptor availability for `link` + `asm/*`).
2. **Args + result:** forward post-`-driver` args to the driver's `os.Args()`; map its
   exit to bnld's.
3. **A standard ELF driver** shipped as an example (`examples/` or `drivers/`): reads
   objects, links, writes — the reference a user copies.
4. **e2e:** assemble → `bnld -driver std-elf.bn objs -o out` → run → exit 42, mirroring
   the existing bnld e2e (native + Docker run gating).
5. **(v2, separate)** export the primitives + validate managed-aggregate interop for
   fully-custom drivers.

## Open questions (for sign-off)

1. **Entry contract:** (A) `main`-shaped driver (recommended) or (B) a typed `Drive`
   entry?
2. **v1 API surface:** high-level `Link*` + `ReadObject`/`ReadArchive` only (recommended),
   deferring primitive exposure to v2?
3. **Where do standard drivers live** — `examples/`, a new `drivers/`, or inside the bnld
   repo tree? (Affects whether they're BUILDER-compiled or free-language.)
4. **Scope for this milestone:** just the spike + a std ELF driver + e2e (v1), or push
   into the primitive-level v2 API now?
