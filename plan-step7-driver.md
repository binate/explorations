# Step 7 — Interpreted linker drivers (`bnld -driver custom.bn`)

Status: DESIGN (2026-09-01). The original goal of the bnld work: let a custom linker
*driver*, written in Binate and **interpreted at link time**, orchestrate a link by
calling the **compiled** `pkg/binate/link` library. The dual-mode showcase — interpreted
*policy* driving a compiled *mechanism* (the hot relocation/emit path stays native).

This doc proposes the design; it does not implement it. Open questions for
sign-off are collected at the end.

## Decisions (ratified 2026-09-01)

1. **Entry contract: typed `Drive`.** The driver exports a typed entry (not a
   `main`-shaped program); bnld parses the CLI and calls it. See the entry-contract
   section for the arg-marshaling consideration this introduces (the key spike risk).
2. **v1 API surface: high-level only** — today's `link.bni` exports (`Link` /
   `LinkDynElf` / `LinkDynMacho` + `ReadObject` / `ReadArchive`). Primitive-level
   composition is deferred to v2.
3. **Scope this milestone: v1** — the spike + a standard ELF driver + an e2e. No v2
   primitives yet.
4. **Standard drivers live in `examples/`** — free-language, user-copyable, not
   BUILDER-constrained.

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

**RATIFIED: (B) typed `Drive`.** bnld parses the CLI and calls the driver's typed entry.

**Arg-marshaling consideration (the key spike risk).** The VM invokes an interpreted
function via `vm.CallFunc(name, args @[]int)` — args are WORDS. bnld (compiled) must
therefore marshal `Drive`'s arguments into words that the interpreted `Drive` reads
correctly across the compiled→VM boundary. Scalars (`int`, `uint64`, a `*char`) are one
word each and easy. An AGGREGATE arg — `objs @[]@[]readonly char` (a 4-word managed-slice)
— is the risk: it must be passed by the same convention the VM expects for that parameter
shape (by-address vs the 4 words in-line). Options if direct aggregate passing is awkward:
(i) pass `objs` by a single pointer to a compiled-built slice header; (ii) make `Drive`
take C-shaped `(argc int, argv **char)` and a couple of `*char`s (all scalar words);
(iii) fall back to having bnld install the args in the startup `args` cell and `Drive`
read `os.Args()` (no aggregate crosses the call). The spike resolves which is needed
before the API is fixed. Proposed initial signature, pending the spike:

    func Drive(objs @[]@[]readonly char, out *[]readonly char, target *[]readonly char)
            @[]readonly char   // "" on success, else an error message

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

## Implementation blueprint (spike — API verified 2026-09-01)

The spike is de-risked: every API piece exists. `pkg/binate/interp/runfunc_typed_test.bn`
is the canonical embedder template. The bnld `-driver` path is:

    // inject set = stdlib + the compiled link library (its compiler-synthesized
    // __Package() accessor — emitted for EVERY module, so link.__Package() exists).
    var pkgs = append(interp.StandardPackages(), link.__Package())
    var it @interp.Interp = interp.New(<stack>, pkgs)   // injects + marks link interface-only
    it.AddBniPath(root); it.AddImplPath(root)            // resolve the driver's imports
    var file @ast.File = parser.New(driverBytes, driverPath).ParseFile()
    var loadErrs = it.LoadProgram([file])                // resolves imports, typechecks, lowers
    // typed cross-boundary call — RunFuncTyped marshals Value args ↔ the VM:
    var args = [interp.StringSliceValue(objs), interp.StringValue(out), interp.StringValue(target)]
    var results, runErrs = it.RunFuncTyped(<driver-pkg>, "Drive", args)
    var err @[]char = results[0].AsString()              // "" = ok

Confirmed: `interp.New` injects `pkgs` as compiled instances AND adds them to
`Ldr.InterfaceOnly` (link runs compiled, loads from its `.bni` only, is not lowered);
`RunFuncTyped` type-checks + marshals `Value` args (`StringValue`/`StringSliceValue`
supported) and returns `Value` results; `__Package()` is emitted for every module.
Ratified `Drive` types adjust to the `Value` API: `Drive(objs @[]@[]char, out @[]char,
target @[]char) @[]char`.

**Design refinement the mapping surfaced — driver location.** A driver `import
"pkg/binate/link"` and needs link's `.bni` to type-check; that interface is INTERNAL to
the binate repo and is not shipped with a released toolchain. So the "standard drivers in
`examples/`" decision cannot hold as-is: either (a) the spike + standard drivers live in
the binate repo (where `pkg/binate/link.bni` exists), or (b) bnld first exposes a STABLE
PUBLIC driver-API package (a curated `.bni`, distinct from the internal `link`) that
drivers import and that ships with the toolchain — the cleaner long-term shape, a small
design addition. Spike lives in the binate repo; revisit examples/ placement once a public
driver-API package exists. (Spike-open items to settle empirically: whether `LoadProgram`
needs the driver to be `package main` / have a `func main`, and the driver's package path
passed to `RunFuncTyped`.)

## Spike results (2026-09-01)

The spike is built and runs the loop end-to-end **up to the typed call**, proving the core
feasibility. What landed on the worktree (WIP, unlanded): `drivers/elf.bn` (a reference
static-ELF driver exporting `Drive(objs @[]@[]char, out @[]char, target @[]char) @[]char`),
and `cmd/bnld/{driver.bn,main.bn}` — a `-driver <path>` + `-I <root>` path that embeds the
VM via `interp.New(16MB, StandardPackages() + link.__Package())`, reads+parses the driver,
`LoadProgram`s it, and calls `RunFuncTyped("main", "Drive", [StringSliceValue(objs),
StringValue(out), StringValue(target)])`. **bnld built** (it now embeds the VM + front-end
+ link, ~3 MB), assembled an `exit42.o` with bnas, and `bnld -driver drivers/elf.bn`
parsed, injected, loaded, and lowered the driver with no crash.

**What worked:** VM embed; injecting compiled `link` by `link.__Package()`; interface-only
load of the injected set; parsing + `LoadProgram` of the driver (imports `pkg/binate/link`
+ `pkg/binate/buf` resolved via the standard search paths); reaching `RunFuncTyped`.

**The one wrinkle it surfaced (clear fix):** `RunFuncTyped` resolves the target via
`Checker.PackageType(pkg, fn)` → `lookupPackage` — a **CheckPackage-registered package
scope**. But `TypecheckAll` (what `LoadProgram` uses) checks the driver as a MAIN PROGRAM
via `c.Check(mainFile)`, NOT `c.CheckPackage("main", …)`, so `"main"` is not a
lookupPackage-able scope and `RunFuncTyped("main","Drive")` returns "function not found in
the type checker". So the entry-contract call needs the driver's function to live in a
CheckPackage-registered scope. Three ways to close it (pick in the next increment):
1. Load the driver as a NAMED package (the `TypecheckPackages` path cmd/bni uses for test
   packages) rather than a main program, then `RunFuncTyped("<driver-pkg>","Drive",…)`.
   Cleanest; the driver becomes `package "<name>"` at a loader-findable path.
2. Add an interp entry that checks the loaded main file via `CheckPackage("main", …)` (or
   registers the main scope under "main") so `RunFuncTyped("main", …)` resolves.
3. Fall back to `RunFunc` (mangled-name call) with hand-marshaled `@[]int` args — avoids
   the type-checker lookup but reintroduces the aggregate-arg marshaling `RunFuncTyped`
   was chosen to handle.

Recommendation: (1) — align the driver contract to a named package. Also carry the
already-noted spike shortcut (the standard search paths are hard-coded in `runDriver`;
they should come from binate-paths.sh as flags) and the driver-location finding
(binate repo, not examples/, until a public driver-API package exists).

### Wrinkle CLOSED (2026-09-01) — the loop works end-to-end

Took option (1).  Added `interp.LoadCallable(files, pkgPath)` (+ `TypecheckDriver`): it
registers the driver as a CheckPackage'd, lookup-able package scope (not a main program)
and lowers it as a regular package — no `main`/entry required.  `drivers/elf.bn` is now
`package "driver"` (no stub main); bnld reads the driver's package name from its parsed
file and calls `RunFuncTyped(<driver-pkg>, "Drive", …)`.  Result: `bnld -driver
drivers/elf.bn -I <root> -target linux-x64 -o exit42 exit42.o` produces a valid static ELF
that **runs → exit 42** (Docker linux/amd64) — the interpreted driver (in the VM) drove the
compiled `link.Link` to a working executable.  The interp additions are purely new
functions (existing users unaffected; interp is not BUILDER surface).

### Adversarial review + v1 ready (2026-09-01, commit 26a521aac)

Two focused adversarial passes (interp additions; bnld driver path).  One MAJOR, since
fixed: `LoadCallable` emitted NO package-init dispatcher, so `RunFuncTyped`'s
`main.__init_all` guard always missed and NO lowered package's init ran — not just the
driver's own `var` initializers (the documented limitation) but every lowered dependency's
`__init`, silently zeroing their globals.  Fixed: `LoadCallable` now collects init names
and emits a `main.__init_all` dispatcher (the callable analogue of LoadProgram's), and the
e2e's init-driver case regression-guards it (a top-level `var` initializer must run or the
link fails).  Minor fixes: `driver.bn` reports parser errors + releases the result Value on
the non-conforming-Drive path; bnld rejects `-dynamic`/shared `-l` on the `-driver` path;
`stripQuotes` gains asymmetric-quote tests.  Everything else in the review checked out
(Release discipline, arg marshaling, interface-dispatch registration, TypecheckDriver).

v1 is COMPLETE and validated: `e2e/bnld-driver-linux.sh` ALL PASS (exit42→42, hello→0,
init-driver→42); `builder-comp interp bnld bni repl` 4 passed; hygiene 20/20.  Remaining
follow-ups (tracked, not blocking): de-shortcut the search paths (flags from
binate-paths.sh); driver location vs a public driver-API package; aarch64/other targets in
the e2e; a `LoadCallable`-with-imports unit test (currently covered by the e2e).

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

## Open questions — RESOLVED

All four were signed off 2026-09-01 (see "Decisions (ratified)" above): (1) typed
`Drive` entry; (2) high-level API only for v1; (3) v1 scope = spike + std ELF driver +
e2e; (4) standard drivers in `examples/`.  The one thing the spike must still settle is
mechanical, not a policy choice: how `Drive`'s aggregate `objs` argument is marshaled
across the compiled→VM boundary (see the entry-contract section).
