# Plan: from-code stacktraces (`builtins/debug`)

**Status:** proposed (not started). Grounds in a 6-lens reconnaissance of the
stack/frame/debug/symbol infrastructure (2026-08-20).

## Goal & scope

Add a from-code API for **explicit stack capture + symbolization** — a program can
ask, at a point of its choosing, "who called me / what is my call stack," and turn the
result into readable frames (package + function name).

- **Tier A (this plan): function-name + package backtraces**, working in the VM and on
  all three native targets (x64, aarch64, arm32). No `file:line`.
- **Tier B (follow-up, separately planned): `file:line`, cross-mode-stitched stacks,
  and on-fault capture.** Called out where the Tier-A design must not paint itself into
  a corner, but NOT built here.

Non-goals / clarifications:

- This is **diagnostics/observability, not a recover mechanism.** Binate deliberately
  has no panic/recover and no unwinding-to-catch (spec §17). A stacktrace is orthogonal
  to the panic model.
- **Explicit capture first.** On-fault capture is a Tier-B, VM-first follow-up: native
  faults abort immediately with no signal handler, and two tracked correctness gaps sit
  under any fault-time feature (the re-entrant-`execFunc` fault swallow [MAJOR,
  claude-todo.md] and diagnostics going to stdout not stderr). Explicit capture
  sidesteps all of that.

## Design principles (from the recon)

1. **Separate capture from symbolization.** Capture (walk the stack, collect raw frame
   tokens) is cheap, allocation-free, and mode-uniform-ish; symbolization (token →
   package/function name) is expensive, mode-specific, and its data lives in different
   places per mode. The split is forced, not merely tidy — and it is what makes the
   Tier-B fault-time path (eager capture, deferred symbolization) possible later.
2. **Symbolization data lives in memory, in a format we own.** The VM already embodies
   this (`VMFunc.Name` is in memory); Tier A extends the same property to native via a
   compiler-emitted *loaded* side-table (see below) — NOT by reading the on-disk symbol
   table or DWARF at runtime. This is what keeps the feature working on the **C-free /
   bare-metal** target (no filesystem, no `/proc/self/exe`).
3. **The capture primitive is a compiler intrinsic, not a library function.** "Give me
   my caller's return address" cannot be an ordinary call (an ordinary call has already
   established its own frame and could be transformed). It needs compiler support in
   both native backends and the VM. Everything *above* capture is ordinary library code.
4. **Faithfulness is a documented best-effort contract, not a hard guarantee** (see the
   dedicated section).

## Architecture overview

Five moving parts:

| Part | Where | Nature |
|---|---|---|
| `builtins/debug` library | `ifaces/core/pkg/builtins/debug` + `impls/core/common/...` | ordinary Binate; injected like `testing` |
| capture intrinsic | `token`/`lexer`/`parser`/`types`/`ir` + per-backend lowering (`native/{x64,aarch64,arm32}`, `vm`) | compiler surface |
| loaded symbolization table | shared manifest pass + native backends emit; runtime lookup in the library | compiler-emitted data + runtime reader |
| VM current-frame publish hook | `pkg/binate/vm` (`execLoop`) | VM-internals |
| generic-name renderer | `pkg/binate/mangle` | pure library |
| arm32 frame-pointer chain | `pkg/binate/native/arm32` prologue | backend |

## The library surface (`builtins/debug`)

Ordinary Binate code, registered by adding one entry to `builtinPkgs()` +
`IsNativeOnlyInVM`/`NativeOnlyInterfacePaths` (`pkg/binate/interp/externs.bn`), exactly
as `testing` is. Exported (capitalized) surface:

```
// capture — intrinsic-backed, no allocation (fills a caller-owned buffer)
func Callers(skip int, into *[]Frame) int      // returns count written
func Caller(skip int) (Frame, bool)            // the single skip-th frame

// symbolize — separate, on-demand, mode-dispatched
func Symbolize(f Frame) FrameInfo

// ergonomic all-in-one (allocates an owned stack)
func Stacktrace(skip int) @[]FrameInfo
```

- `Frame` — an opaque, **value-type, no-managed-refs** token: `{ Mode; <locator> }`
  where the native locator is a return address (`*uint8`) and the VM locator is
  `{ FuncIdx, Pc }`. No managed references ⇒ `Callers` into a `*[]Frame` allocates
  nothing (also the property the Tier-B fault path needs). Its layout is an ordinary
  struct via `pkg/types`, so both backends and the VM agree on it.
- `FrameInfo` — `{ Mode; Pkg @[]char; Func @[]char; Mangled *[]readonly char }`.
  `Func` is the member chain joined (a method reads `Type.Method`). `Mangled` can be a
  zero-copy borrow into the always-live symbolization table's string blob; `Pkg`/`Func`
  are rendered on demand. (Tier B adds `File @[]char; Line int`.)
- **`skip` convention** (documented, matches `runtime.Callers` shape): the capture
  point's own enclosing library frame is `skip = 0`; the user's own frame (the caller of
  `Caller`/`Callers`) is `skip = 1`, so the proximal caller is `Caller(1)`. The intrinsic
  drops the library frame automatically; `skip` drops that many more.

## The capture intrinsic

Model on the existing `_func_handle` keyword-token intrinsic (`token.RAW_FUNC_ADDR`):
a keyword token → `EXPR_BUILTIN`, shape-checked in `checkBuiltinCall`, a new IR op,
lowered per-backend. Working name `_stack_frames(skip int, into *[]Frame) int`; the
library's `Callers` body is just this intrinsic, so the intrinsic expands *inline in
`Callers`* and the "current frame" at expansion is `Callers`' frame (which it drops).

Per-backend lowering:

- **x64 / aarch64** — read FP (`rbp`/`x29`), then loop: return address `= [fp+8]`,
  next `fp = [fp]`; stop at a null/entry sentinel. Skip `skip+1` frames, fill `into` up
  to `len(into)`. Reliable and 1:1 because prologues are unconditional and FP is set once
  (recon: no inlining/TCO/leaf-omission).
- **arm32** — same walk once the r11 chain exists (see below).
- **VM** — a new bytecode op whose `execLoop` handler walks the 6-word frame headers from
  the **live** `regsOff` (a loop local) back through `savedRegsOff` to the entry frame
  (`savedPC == -1`), emitting `{FuncIdx = savedFuncIdx, Pc = savedPC}` per level. Seed the
  walk with the current top `funcIdx` (the header stores the *caller's* funcIdx). This is
  the same header chase `BC_RETURN` already performs.

**VM current-frame publish hook.** The live `(funcIdx, pc, regsOff)` are `execLoop`
C-stack locals, not on the VM struct. The new bytecode op is dispatched *from within*
`execLoop`, so it has them directly (thread them into the handler exactly as `vmPollPoint`
is threaded — `vm_exec.bn` / `vm_exec_helpers.bn`). No new persistent VM field is needed
for the *explicit* path (a persistent current-frame field is a Tier-B concern for the
fault path).

## The loaded symbolization table (native)

The native side of "names in memory, format we own." The compiler emits, into an ordinary
**mapped (`PT_LOAD`) rodata** global:

- a table sorted by address: `{ startAddr, size, nameOffset }` per function (start+size
  give exact extents — we record them, so no `STT_NOTYPE`/size-0 coarseness and no
  block-label pollution to filter), and
- a string blob of the mangled names.

Addresses are filled by **relocations against each function's symbol** (the linker
resolves them), so the emitter does not need final addresses. `Symbolize` for a native
frame is a binary search by address → `nameOffset` → borrow the mangled string →
`Demangle` → render.

Layering: the *manifest* (which functions, their mangled names) is language-level and
shared (mangling is already shared); the *address-range emission* is per-backend (each
native backend emits the table with relocations to its own function labels). This keeps
the split in `ir-backend-guidelines.md`.

Gate emission behind a build flag (default **on**; `--no-symbol-table` drops the table
for size-sensitive builds). This deliberately does NOT touch the ELF/Mach-O `.symtab`
(leaving that as-is is orthogonal; we do not rely on it).

Why this over the alternatives (the #3 discussion, recorded for posterity):

- **Reading our own binary in-process** (parse `.symtab` via `/proc/self/exe`) needs a
  filesystem (fails on bare metal), a net-new ELF *and* Mach-O reader, and still hits
  `STT_NOTYPE`/size-0/block-label coarseness — so we'd pay the emitter work anyway.
- **DWARF (`-g`) / `addr2line`** only exists on the LLVM backend, is off by default,
  lives in unmapped `.debug_*`, needs a DWARF parser (bigger still) or a C subprocess,
  and is debugger-facing.

## VM symbolization & the name-form asymmetry

VM frames symbolize via `VMFunc.Name` (already in memory) — no table needed for VM in
Tier A. **But note a real asymmetry the review should scrutinize:** `VMFunc.Name` is
stored readable and short-package (`"asm.New"`), whereas the native table stores the
`bn_...` mangled full-path form. So `Symbolize` normalizes both to `{Pkg, Func}` but the
`Pkg` *granularity* differs between modes (full path on native, short segment on VM)
unless we align VM naming.

**Open sub-decision:** either (a) accept the asymmetry and document it for Tier A, or
(b) have VM lowering also carry the mangled (or full-qualified) name so both modes yield
full-path `Pkg`. (b) is cleaner but a VM-lowering + `VMFunc` change; (a) is free. Leaning
(a) for Tier A, (b) when Tier B touches VM lowering for line info anyway.

## arm32 frame-pointer chain

arm32's prologue currently saves r11 in the push mask (`0x5FF0` = r4..r12) but never
points it at the frame — r11 holds stale garbage, so the stack is not walkable beyond the
current frame. Establish a conventional chain: after the register push, set r11 to the
frame anchor so `[r11]`/`[r11+…]` yield saved-r11/saved-lr, mirroring x64/aarch64. Cost is
1–2 instructions; the register is already reserved and saved.

**Risk / verification (load-bearing):** this changes the arm32 frame model, which
interacts with `PlanFrame`/`SAVED_BYTES` offset math. It MUST be verified with the
**native** arm32 conformance mode — `builder-comp_native_arm32_baremetal` (the `native`
is load-bearing; the non-`native` `builder-comp_arm32_baremetal` is the LLVM path and
would NOT exercise this code). The native arm32 backend is currently incomplete
(~2026 pass / 611 fail per `plan-native-arm32.md`), so land this change without
regressing that baseline.

## Generic-name renderer

`Demangle` exists and is production runtime code, but returns generic type-args as
verbatim lp-encoded strings and has no human-readable renderer. Add one in
`pkg/binate/mangle`: decode the TypeArg sub-language (`p`→`*`, `m`→`@`, `s`→`*[]`,
`M`→`@[]`, `a<len>_`→`[len]`, `r`→`readonly`, `i`/`j`→iface, `f`/`F`/`g`→func sigs,
`S`→anon struct, `N`→named/primitive leaf) plus joining `Pkg` with `/` and `Members`
with `.`. Expose as `mangle.Readable(sym) @[]char` (and/or a render of `DemangledName`).
`Symbolize` uses it. This is the one net-new library piece for readable *generic* frames;
non-generic names are a trivial join.

## Faithfulness contract (documented, best-effort)

> A captured stack is a faithful **subset** of the physical call stack. On the
> self-hosted native backend, frames correspond 1:1 to calls (no inlining, tail-call
> elimination, or leaf-frame omission today). On the LLVM backend, faithfulness tracks
> the optimization level — inlining/TCO/frame-pointer omission may merge or drop frames.
> Compiler-generated transparent thunks (func-value/closure shims) may not appear as
> frames, by design. The API does not guarantee that every *logical* (e.g. inlined) call
> is represented.

Forward-looking reservation (not built): when inlining eventually lands, recovering
inlined logical frames needs inline-frame metadata — a Tier-B+ decision, not owed now.

## Phasing (each commit independently green & cherry-pickable)

1. **`builtins/debug` skeleton + capture intrinsic (VM path).** Package registration;
   `Frame`/`FrameInfo` types; the intrinsic through token/lexer/parser/checker/IR; VM
   bytecode op + `execLoop` walk + publish threading; `Callers`/`Caller`. First green
   milestone: VM-mode raw capture (depth + per-frame `FuncIdx`) verified.
2. **Native x64 / aarch64 intrinsic lowering.** FP-chain walk; raw native capture
   verified (return addresses at expected call sites).
3. **Loaded symbolization table + native `Symbolize`.** Manifest pass, per-backend
   emission with relocations, runtime binary-search lookup, `--no-symbol-table` flag.
4. **arm32 FP chain + arm32 intrinsic lowering.** Verified against
   `builder-comp_native_arm32_baremetal` without regressing its baseline.
5. **Generic renderer + `Symbolize`/`Stacktrace` completion + VM name normalization.**
   End-to-end readable `{Pkg, Func}` backtraces in both modes; the name-form asymmetry
   resolved per the sub-decision above.

Tests per phase: conformance tests exercising known call depths/identities in both modes;
unit tests for the renderer and the table lookup. Bug-discovery protocol applies.

## BUILDER / layering constraints

- **The intrinsic is define-only-safe in the BUILDER tree, use-only-outside.** The
  intrinsic's *definition* (a `token` enum entry, `checkBuiltinCall`/IR/backend switch
  cases) lives in BUILDER-compiled packages and is fine — adding an enum arm + switch case
  is BUILDER-accepted. But the new keyword must NOT be *used* anywhere in cmd/bnc's
  BUILDER-compiled tree, or gen1 breaks (same rule as `unsafe_cast`). It is used only in
  `builtins/debug`, which is compiled by the fresh bnc (like `testing`), NOT in cmd/bnc's
  tree — so this holds. Verify by keeping all `_stack_frames` uses in `builtins/debug`.
- **`builtins/debug` library code** is outside the BUILDER tree ⇒ full language available.
- **Layout of `Frame`/`FrameInfo`** is a cross-mode contract (used by native and VM) ⇒
  ordinary structs via `pkg/types`, which both already agree on. No backend-local layout.
- **The symbolization manifest** (function → mangled name) is shared/language-level; only
  the address-range emission is per-backend. Matches `ir-backend-guidelines.md`.

## Risks

1. **Intrinsic surface is wide** — token/lexer/parser/checker/IR + three native backends +
   VM. Mitigated by phasing (VM path, then x64/aa64, then arm32) so each lands green.
2. **arm32 prologue change against an incomplete native backend** — must use the `native`
   arm32 mode and not regress its baseline (see that section).
3. **Binary size** from the always-present loaded table — mitigated by `--no-symbol-table`.
4. **VM name-form asymmetry** (short vs full package) — an explicit sub-decision, not a
   silent gap.
5. **Tier-B entanglement not to be pulled in early** — file:line, cross-mode stitching,
   and on-fault capture are out of scope; the fault-path correctness gaps
   (re-entrant-`execFunc` swallow; stdout/stderr) are noted only so the on-fault
   follow-up is not layered on them prematurely.

## Open sub-decisions (resolve during impl or surface)

- Exact intrinsic spelling: keyword-token (`_stack_frames`) vs IR-magic-by-name.
- VM name normalization: accept short-package asymmetry (Tier A) vs carry full/mangled
  name in `VMFunc` (aligns with Tier B).
- `--no-symbol-table` default & name.
- arm32 chain anchor offset (impl detail; verify against native mode).
