# Plan: Embeddable whole-program interpreter API (`pkg/binate/interp`)

Status: **Inc 1 + Inc 2 (Layers 1 & 2) LANDED** (2026-06-20).  Inc 1: the `@Interp`
whole-program embedder API on main (1a `5bdd76b6`: machinery →
`pkg/binate/interp`, errors as values; 1b `596fb872`: the `@Interp` facade,
cmd/bni's `runProgram` collapsed onto it).  Inc 2 was **reframed** — the
original "host-IO sink" was misguided (see Inc 2 below): an interpreter, like a
compiler, has no I/O of its own; all program I/O flows through the standard
library / `pkg/bootstrap` externs, so redirecting I/O means supplying a
different stdlib, not adding an I/O knob to the engine.  Inc 2 is now an
**extern-registration cleanup**: Layer 1 (`71748fa4`) moved the standard-set
POLICY out of `pkg/binate/vm` into the host (`pkg/binate/interp`), the VM
keeping only the mechanism; Layer 2 (`c843eab7`) made `New(stackSize, pkgs)`
take the swappable library inject-set (`StandardPackages()` default).  Layer 2b
(a `@reflect.Package` wrapping helper, the ergonomic override path) is the open
follow-up.  Builds on `plan-embeddable-vm.md` (increment 5 + 5d-4
landed: the loader / types / ir-gen / vm-lowering layers are reentrant, no
per-run process-globals).

Notes from implementing Inc 1 (refinements vs the scope below):
- **`runTests` was re-qualified too**, not "untouched" — it shares the machinery
  1a moved, so it now calls `interp.X` (mechanical; user-ratified 2026-06-20).
- **`@Interp` is opaque** (forward-decl in `interp.bni`, full struct in
  `interp.bn`): cmd/bni drives it through methods; same-package tests use fields.
- **Loader root:** `New` seeds `NewLoader(".")`; the CLI shell adds the real
  root via `AddRoot` + the `-I`/`-L` flags.  The leading `"."` is benign
  (conformance VM modes unchanged: `builder-comp-int` 1646/0).
- **Two-instance reentrancy test** is the path-free isolation form (distinct VM
  + loader, independent search paths); the full run-a-program-twice path needs
  real search paths and stays covered in-process by `vm/vm_reentrancy_test`.

## Goal

A clean embedder API for running a whole Binate program in-process, the shape
the user asked for:

```
var it @interp.Interp = interp.New(stackSize)   // create interpreter
it.AddBniPath(dir); it.AddImplPath(dir)         // tell it where to load stuff
errs := it.LoadProgram(files)                    // parse-check-irgen-lower; errors as values
rc, errs := it.RunMain()                          // run main.main (after package init)
rc, errs := it.RunFunc("pkg.Func", args)          // …or a particular function
```

`cmd/bni`'s `runProgram` collapses into a thin CLI shell over this.

## Finding (from the 7-reader recon, 2026-06-19)

- **The building blocks already exist.** Post inc-5, every stage is a
  carrier-based public API: `loader.NewLoader/AddBniPath/AddImplPath/LoadImports/
  GetPackage/MergeFiles`, `types.NewChecker/Check`, `ir.InitModule/NewGenCtx/
  GeneratePackage/Register{Imports,StructTypes,AllInterfaces,FuncExterns,…}`,
  `vm.NewVM/LowerModule/CallFunc/RegisterExtern/RegisterStandardExterns`. The
  embedder is a **thin facade**, not new machinery.
- **The REPL is the precedent.** `pkg/binate/repl` (`@ReplSession` via
  `NewReplSession/Init/Step`) is already an embeddable, push-driven,
  errors-as-values, I/O-agnostic interpreter with a host-injected `ReplIO` sink
  and an extern-registration callback — but **incremental** (per line). The
  whole-program shape is a *sibling* session type, not a change to the REPL.
- **The gap is decoupling, not capability.** The whole-program pipeline is
  inlined in `cmd/bni/main.bn:runProgram` (`:62`) and CLI-coupled:
  `typecheckAll` (`util.bn`) calls `bootstrap.Exit(1)` on error instead of
  returning; output is hard-wired to stdout; entry point is hardcoded to
  `main.main` via the synthesized `main.__entry`; `CallFunc` returns a bare
  `int` over `@[]int` args.
- **Multi-session is now unblocked.** `plan-repl-embeddable.md` lists
  "multi-session blocked by ir process-globals (currentChecker,
  importAlias*)" — those were eliminated in inc-5 + 5d-4. Multiple independent
  `@Interp` instances in one process are now structurally sound at the loader /
  types / ir / vm layers (a direct dividend of this session's work).
- **`interp` is NOT in cmd/bnc's BUILDER tree** (only cmd/bni + the future wasm
  host import it), so it may use the full language — no BUILDER-subset
  constraint, unlike the ir/codegen/vm packages it composes.

## Alignment constraints (ratified in `plan-repl-embeddable.md` — do not violate)

- **Errors as values, never `os.Exit` inside the library.** The CLI shell
  decides exit codes; `interp` returns `@[]@[]char` error bundles.
- **Output is a stdlib concern, not an engine concern.** The interpreter has no
  I/O of its own (modulo panic/abort diagnostics — its runtime-fault handler);
  `print`/`println` dispatch to the registered `pkg/bootstrap.Write` extern,
  exactly as a compiler emits a call to the linked runtime.  So redirecting
  output (e.g. a wasm message port) is done by supplying a different
  stdlib/extern set, NOT by an `@Interp` I/O knob.  (This supersedes the
  "injectable Category-A sink" framing inherited from `plan-repl-embeddable.md`.)
- **No new process-globals.** All `@Interp` state lives on the struct.
- **Host drives I/O.** The embedder supplies sources (and later, host functions
  / file-IO); the library never blocks on stdin or opens files itself.

## API shape (target, end-state)

`pkg/binate/interp.bni`:
```
type Interp   // owns @vm.VM, @loader.Loader, @types.Checker, paths, IO sink
func New(stackSize int) @Interp
func (it @Interp) AddBniPath(dir @[]char)
func (it @Interp) AddImplPath(dir @[]char)
func (it @Interp) LoadProgram(files @[]@ast.File) @[]@[]char     // errs ([] = ok)
func (it @Interp) RunMain() (int, @[]@[]char)                     // init_all + main.main
func (it @Interp) RunFunc(qualName @[]char, args @[]int) (int, @[]@[]char)
// later increments: New(pkgs…) inject-set, SetBuildConfig(cfg), typed-result helpers
```

## Increments (each independently green, cherry-pickable)

### Inc 1 — extract the whole-program pipeline into `pkg/binate/interp` (CLI parity)
The chosen first cut: lift `runProgram` into the library, errors-as-values,
`cmd/bni` becomes a thin shell. Output stays on stdout; standard externs
registered internally; entry-point parameterized to `main.main` + named funcs.

- **New `pkg/binate/interp.bni` + `pkg/binate/interp/interp.bn`** with the
  `@Interp` type and `New / AddBniPath / AddImplPath / LoadProgram / RunMain /
  RunFunc`.
- **What moves out of `cmd/bni` into `interp`** (de-CLI'd — `bootstrap.Exit`
  replaced by error returns):
  - the `runProgram` pipeline body (`main.bn:62`): `MergeFiles` → loader paths
    → `LoadImports` → implicit-stdlib loads → typecheck → per-package
    `InitModule`/`NewGenCtx`/`Register*`/`GeneratePackage`/`LowerModule` →
    main `EmitInitDispatcher`/`EmitMainEntry`/`LowerModule`.
  - `typecheckAll` / `typecheckPackages` (`util.bn`) → an errors-returning
    variant (no `Exit`).
  - `ensureBootstrapLoaded` / `ensureLangLoaded` (`util.bn`) — implicit stdlib
    imports; pure, reusable.
  - standard extern setup: `RegisterStandardExterns` (already public) +
    `registerPureCExterns` / `injectStdlibExterns` (`externs.bn`) — move or
    re-home so `interp` wires them in `New`/`LoadProgram`. (Injection point is
    left as an internal call in Inc 1; Inc 2 makes it a host callback.)
  - `RunMain` = `CallFunc("main.__entry", …)`; `RunFunc(name,args)` = run
    `<pkg>.__init_all` (package initializers) then `CallFunc(name,args)`.
- **What stays in `cmd/bni`** (the CLI shell): `parseArgs`/`CLIArgs`,
  `parseSourceFiles` (file read → `@[]@ast.File`, handed to `LoadProgram`),
  `expandDirArgs`, and exit handling (shell maps `interp`'s returned errs/rc →
  `bootstrap.Exit`). `runTests` / `runRepl` are untouched in Inc 1 (a later
  increment can route `runTests` through `interp` too).
- **Open design points to settle during impl** (flagged, not pre-decided):
  loader-root seeding (`New` default `"."` + `AddBniPath`, vs an explicit
  `AddRoot`); whether `RunFunc` auto-runs `__init_all` once and guards
  re-entry; whether `New` or `LoadProgram` constructs the VM.
- **Tests:** `interp/interp_test.bn` — in-process `New → AddBniPath →
  LoadProgram(parsed source) → RunMain()` returns the expected exit code; a
  `RunFunc` path; an error case (bad program → non-empty errs, no process
  exit); a **two-`@Interp`-instances-in-one-process** test (proves the
  inc-5/5d-4 reentrancy end-to-end at the embedder level).
- **Verify:** `interp` + `cmd/bni` unit tests; full conformance on the VM modes
  (`builder-comp-int`, `builder-comp-int-int`) — these run programs through
  `cmd/bni`, so they exercise the extracted pipeline end-to-end; hygiene.

### Inc 2 — extern-registration cleanup (host owns stdlib policy)
**Reframed** from the original "host-IO sink", which was misguided: an
interpreter has no interesting I/O of its own (only panic/abort diagnostics);
program I/O flows through the stdlib / `pkg/bootstrap` externs.  Redirecting I/O
= swapping the stdlib, so the embeddability work is "let the host choose the
extern/stdlib set", not "add an I/O knob".

**Layer 1 — LANDED `71748fa4`.** Moved the standard-set POLICY (which packages:
the rt/reflect/bootstrap `_Package` descriptors, rt/reflect auto-enumeration,
the `pkg/bootstrap` C surface) out of `pkg/binate/vm` into the host
(`pkg/binate/interp`).  The VM keeps only the MECHANISM
(`RegisterPackageFunctions/Globals/Vtables` + `RegisterVmTrampolines`).  This
also killed the double `pkg/bootstrap` registration (vm + interp each bound it,
via two drift-prone copies of `registerBootstrapExterns`); interp's is now the
sole one, preserving the `progArgsAfterDash` Args override.  vm's own tests
(can't import interp — cycle) got a test-local `registerTestExterns`; repl's
test support routes through interp.

**Layer 2 — LANDED `c843eab7`.** `New(stackSize, pkgs @[]@reflect.Package)`
takes the inject-set; empty/nil → `StandardPackages()` (newly exported = the
pkg/std set).  The engine substrate (rt + reflect + the `_Package` accessors +
the bootstrap C surface + the VM trampolines) is always installed, not
swappable.  `injectPackageSet` does the per-package binding; `cmd/bni` passes
`StandardPackages()`.  An embedder targeting wasm passes a set whose I/O package
routes to a message port (the "swap the stdlib" path).

**Layer 2b — PENDING (the ergonomic override path).** A `@reflect.Package`
wrapping helper: build a modified descriptor from an existing one with selected
`FunctionInfo` values replaced, so an embedder overrides e.g. `os.Args()`
without hand-constructing a descriptor.  This is what makes the per-function
override usable; it also resolves the deferred Args-shim home (the embedder
supplies its own `os.Args` binding; `progArgsAfterDash` becomes a cmd/bni-built
wrapped-os concern rather than baked into interp's bootstrap registration).
Optional sub-step: auto-enumerate bootstrap's exported format helpers via
`RegisterPackageFunctions` (they qualify — exported, non-extern), leaving only
the 9 extern C-I/O entries hand-bound.

**The Args / per-function-override question (raised 2026-06-20, deferred).** The
`progArgsAfterDash` shim is really a cmd/bni concern, not general interp policy,
so it needs a real home.  A naïve `SetArgs` does NOT work: `pkg/std/os` is
*shared* with the embedder (one native instance, injected wholesale), so setting
its args would change them for the embedder itself and for every other embedded
interpreter.  The coherent capability is letting the embedder
**inject/override/shadow arbitrary already-imported functions** — so the
embedder just provides its own `os.Args()` binding without touching the shared
package.  The constructive (heavyweight) alternative is forcing the embedder to
build/wrap all of `os._Package`, selectively replacing the functions it wants to
modify.  This is the natural Layer-2+ generalization of "host chooses the
externs": per-symbol override on top of the package inject-set.

**Related direction (raised 2026-06-20).** `repl` should probably become an
*extension of* interp — a `@ReplSession` that *starts* with an embedded
`@Interp` and then adds incremental eval — rather than a sibling that
re-implements the load/lower wiring.  (Today repl is vm-only in production; its
tests already route through interp after Layer 1.)

### Inc 3 — typed / aggregate results + string args
`RunFunc` variants that pass `@[]@[]char` argv and unpack multi-return /
aggregate results off `vm.Stack` into typed values, instead of bare `int`.

### Inc 4 — build-config + in-memory source FS (wasm enablement)
`SetBuildConfig(cfg)` (cross-target gating, today baked to host); an injectable
source provider so `LoadProgram` can take in-memory/virtual sources (no
`bootstrap.Open`), the last filesystem coupling — unblocks the
`plan-wasm-browser.md` consumer.

## Explicitly deferred (surfaced, not silently dropped)
- Routing `cmd/bni`'s **test runner** (`runTests`) and **REPL** (`runRepl`)
  through `interp` — Inc 1 only does `runProgram`.
- **Suspend/break** (REPL Stages 6–7) — separate plan, unrelated to whole-program.
- A unified **error object** across loader/checker/ir (recon's suggestion) —
  Inc 1 returns the existing `@[]@[]char` bundles; a richer error type is a
  later nicety.

## Verification (end-to-end, per increment)
- Unit: `./scripts/unittest/run.sh builder-comp interp bni`
- Conformance (VM path exercises the extracted pipeline):
  `./conformance/run.sh builder-comp-int` and `builder-comp-int-int`
- Hygiene: `scripts/hygiene/run.sh`
