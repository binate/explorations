# Plan: emit `bn_init` in every artifact; `bn_entry` = `bn_init(); main.main()`

Status: COMPLETE — landed `e6abbd234` (2026-09-08); docs Status notes cleared
`1fc94b6`. Was the `claude-todo.md` entry "Emit `bn_init` in every artifact"
(ABI review #9, owner decision option (b), 2026-09-08).

## Verification (all green)

- Unit tests (builder-comp): ir + mangle + codegen packages pass. New/renamed:
  `TestEmitBnEntry`, `TestProgramEmitsBnInitAndBnEntry`, `TestEmitBnInit*`,
  codegen `emit_bninit_test.bn`, `data_satregistry_test.bn`.
- Conformance FULL: LLVM `builder-comp` 3021/0; native-aa64 3021/0;
  native-arm32-baremetal (soft-float, `bl bn_entry`) 2976/0; native-arm32-linux
  (hard-float, Docker) 3021/0. Interp path (unchanged) exercised via smoke.
- e2e `e2e/program-bn-init.sh`: a C host calls `bn_init` (twice) + two exports on
  a program-shaped archive WITHOUT running main → `42 1` (init ran once;
  run-once guard held). LLVM + native both pass.
- Hygiene 20/20.
- Adversarial review: sound to land, no critical/major issues; interp
  correctness, `_pkg_satfrag` dead-strip liveness (native reloc + LLVM
  `@llvm.used`), run-once guard, registry-before-inits order, mangling, and
  old-name residue all verified clean. Flagged minor stale comments folded in.

## Still pending (on landing)

Clear the two spec Status divergence notes in the `docs` repo (they say
`bn_init` is emitted only in `--library`): `spec/17` §17.3.2 and `abi/06` §6.7.
Do this in the docs repo AFTER the code is on `main` (edit → commit → push).

## Problem

`bn_init` is emitted only in `--library` builds today. A program's `bn_entry`
calls the internal `<main>.__init_all` dispatcher directly, and the program
path wires `BuildSatRegistry` into `__entry` separately (`EmitSatRegistryWiring`).
So a C host that follows the spec (abi/06 §6.7) and calls `bn_init` against a
*program*-shaped link gets an undefined symbol. Make the realization match
`prog.entry.glue`: `bn_init` exists in every artifact; `bn_entry` is literally
`bn_init(); main.main()`.

## Design

Compiled (native + LLVM) program driver path (`cmd/bnc/main.bn`, `test.bn`):

- Replace `EmitInitDispatcher + EmitMainEntry + EmitSatRegistryWiring` with
  `EmitBnInit(initPkgNames) + EmitBnEntry()`.
- `EmitBnInit` is the renamed `EmitLibInit` — now emitted for programs AND
  libraries (build root = `main` for a program, the facade for a library). Its
  shape is unchanged: run-once guard (`__bninit_done`) → `BuildSatRegistry` from
  the build root's `_pkg_satfrag` → dependency-order package inits.
- `EmitBnEntry` (new) emits `<main>.__entry` (→ `bn_entry`) = `call bn_init;
  call main.main`. No more `__init_all`, no separate registry wiring.
- The run-once guard makes a host calling `bn_init()` and then the process
  running `bn_entry` (which re-calls `bn_init`) compose safely — inits run once.

Library driver path (`cmd/bnc/library.bn`): `EmitLibInit` → `EmitBnInit`
(rename only; unchanged behavior — bn_init, no bn_entry).

Deletions in `pkg/binate/ir/data_satregistry.bn`:

- `EmitSatRegistryWiring` — folded into `EmitBnInit` (which already builds the
  registry, ahead of the inits, by construction — no rotation needed).
- `rotateLastInstrsToFront` — only `EmitSatRegistryWiring` used it in
  production; delete it and its unit test.
- `emitBuildSatRegistryCall` stays; it now has one caller (`EmitBnInit`).

## Why the interp path is NOT changed

The interp (`pkg/binate/interp/`) keeps `EmitInitDispatcher` (`__init_all`) and
`EmitMainEntry` (`__entry` = `__init_all(); main.main()`), and `RunFunc` /
`RunFuncTyped` keep calling `main.__init_all` directly. It must NOT be switched
onto `bn_init`, because `bn_init` emits a `rt.BuildSatRegistry` call and the
interp builds its satisfaction registry a different way (host-side, not by
lowering `BuildSatRegistry`). So after this change `EmitInitDispatcher` and
`EmitMainEntry` are interp-only — a legitimate split, since the interp and the
compiled backends have genuinely different init/entry machinery.

## Why the risk to the native backends is low

The compiled program path ALREADY emitted `BuildSatRegistry` + the `_pkg_satfrag`
LEA (via `EmitSatRegistryWiring` into `__entry`) for every program, baremetal
included. This change only MOVES those ops into `bn_init` and adds the guard
(`OP_LOAD`/`OP_BRANCH`/`OP_STORE` on a bool global) — all ops every backend
already lowers. `codegen.emitSatFragPin` / `collectDefinedDataSyms` gate on the
`m.SatRegistryRequested` flag (set by `EmitBnInit`), not on the `__entry` name,
so the dead-strip pins are unaffected.

## Tests

- `gen_init` unit: a program emits both `main.__bninit` (→ bn_init) and
  `main.__entry` (→ bn_entry); `__entry` calls `__bninit` then `main.main`.
  Run-once guard shape already covered by the (renamed) `TestEmitBnInit*`.
- e2e: a program-shaped link whose C host calls `bn_init` only, then a
  `#[c_export]` function, without running main. `library-iface-assert.sh` and
  `ffi-export.sh` keep the library leg.
- Conformance: full `builder-comp_native_arm32_linux` (hard-float, Docker) +
  `builder-comp_native_arm32_baremetal` (soft-float) to cover the baremetal
  `bl bn_entry` path and confirm the guard'd bn_init lowers on native arm32.

## Comment/doc sweep (old behavior references)

`impls/core/common/pkg/builtins/startup/args_main.bn`, `args_baremetal.bn`;
`impls/core/common/pkg/builtins/rt/rt_satregistry.bn`;
`ifaces/core/pkg/builtins/rt.bni`; `pkg/binate/native/arm32.bni`,
`arm32_emit_func.bn`; `pkg/binate/codegen/emit_satfrag_pin.bn`,
`emit_cglobal.bn`; `pkg/binate/ir.bni`. Reword "`__entry`'s first statement /
before `__init_all`" to "startup / before the package inits (in `bn_init`)".

Spec is already aligned (abi/06 §6.7; spec/17 §17.3.2 Status) — clear both
Status notes on landing.
