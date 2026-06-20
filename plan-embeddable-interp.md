# Plan: Embeddable whole-program interpreter API (`pkg/binate/interp`)

Status: **SCOPED** (2026-06-19) — increments below not started. Builds directly
on `plan-embeddable-vm.md` (increment 5 + 5d-4 landed: the loader / types /
ir-gen / vm-lowering layers are reentrant, no per-run process-globals).

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
- **Output goes through an injectable sink** (Category-A), not hardcoded fd-1
  writes — required for the wasm consumer (escaping to a message port is a bug).
  Inc 1 keeps stdout as the default; Inc 2 makes it injectable.
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
// later increments: SetIO(sink), SetBuildConfig(cfg), typed-result helpers
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

### Inc 2 — host-IO sink (serves wasm; no stdout hardwire)
Add `SetIO(sink)` (ReplIO-style `@func` channels). Route Category-A output
through it; default = stdout. Audit the program-run path for stray
`print`/`println`/`Write(1,…)` that bypass the sink (the wasm fd-1-escape risk).

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
