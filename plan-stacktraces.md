# Plan: from-code stacktraces (`debug` library)

**Status:** proposed (not started). Grounds in a 6-lens reconnaissance of the
stack/frame/debug/symbol infrastructure and a 3-lens adversarial review of an earlier
draft (both 2026-08-20); the review corrected two architectural blockers now folded in
(the native backend compiles only the main module; a `builtinPkgs` member is injected
native in the VM).

## Goal & scope

Add a from-code API for **explicit stack capture + symbolization** — a program can ask,
at a point of its choosing, "who called me / what is my call stack," and turn the result
into readable frames (package + function name).

- **Tier A (this plan): function-name + package backtraces.** Verified on x64 and
  aarch64 (native + VM); **arm32 is best-effort, gated on the incomplete self-hosted
  arm32 backend** (see that section — it needs real frame-layout work, not a one-liner).
  No `file:line`.
- **Tier B (follow-up, separately planned): `file:line`, cross-mode-stitched stacks,
  and on-fault capture.** The user has chosen Tier A first with `file:line` as a separate
  follow-up project. Tier B is called out only where Tier A must not paint itself into a
  corner; it is NOT built here.

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
   established its own frame and could be transformed). It needs compiler support.
   Everything *above* capture is ordinary library code.
4. **Faithfulness is a documented best-effort contract, not a hard guarantee**, and it
   must be honest in *both* directions — some real frames may be absent, and some present
   frames may be synthetic/unsymbolizable (see that section).

## Where code actually gets compiled (the fact the whole native story hinges on)

`cmd/bnc` has two code-emission backends behind one `Backend` interface
(`cmd/bnc/compile.bn`): `llvmBackend` (emit LLVM IR → `clang -c`) and `nativeBackend`
(the self-hosted `pkg/binate/native` asm emitter). **Dependency packages ALWAYS go
through `llvmBackend`; only the *main module* honors `--backend native`** (compile.bn
comment + `main.bn` main-module dispatch) — the self-hosted native backend "only lowers
the main module." Consequences that shape everything below:

- The `debug` library is always a *dependency*, never the main module ⇒ it is **always
  LLVM-compiled**. So the capture intrinsic MUST be lowered in **`pkg/binate/codegen`
  (LLVM)** — that is its real home — and in the **VM**. It is **never** lowered by the
  self-hosted `native/{x64,aarch64,arm32}` backends (they never see it). (The precedent
  intrinsic `OP_FUNC_HANDLE` is likewise lowered in codegen, `emit_funcvals.bn`.)
- A backtrace's frames span *both* main-module functions (self-hosted native, in
  `--backend native` builds) *and* dependency functions (LLVM). So the symbolization
  table cannot be a single self-hosted-native global — it is emitted by **every backend**
  as **per-package fragments linked into a dependency graph** and merged at runtime (the
  decentralized model in [`plan-rtti-decentralize.md`](plan-rtti-decentralize.md), done
  first as a PoC — see the table section).
- The FP walk (which runs inside LLVM-compiled `debug` code) traverses whatever frames
  are on the stack. Its correctness depends on **every** frame keeping a standard FP
  chain: clang at `-O0` (bnc passes no `-O`, so this holds today — we will additionally
  pass `-fno-omit-frame-pointer` to guarantee it), and the self-hosted backend on
  x64/aarch64 (already unconditional). **arm32 self-hosted keeps no FP chain at all** —
  the gap.

## Architecture overview

| Part | Where | Nature |
|---|---|---|
| `debug` library | `ifaces/.../pkg/std/debug` + impl — an **ordinary importable package** | ordinary Binate; lowered in VM, LLVM-compiled in AOT |
| capture intrinsic | `token`/`lexer`/`parser`/`types`/`ir` + lowering in **`codegen` (LLVM) and the VM** | compiler surface |
| loaded symbolization table | emitted by **every** backend as **per-package graph-linked fragments**; **merged + sorted at runtime** (decentralized, per `plan-rtti-decentralize.md`) | compiler-emitted data + runtime reader |
| VM current-frame publish hook | `pkg/binate/vm` (`execLoop`) | VM-internals |
| generic-name renderer | `pkg/binate/mangle` | pure library |
| arm32 clang-compatible FP frame | `pkg/binate/native/arm32` prologue/epilogue | backend (non-trivial) |
| `-fno-omit-frame-pointer` | `cmd/bnc/compile.bn` clang flags | driver flag |

**`debug` is an ordinary library, NOT a `builtinPkgs` member.** A `builtinPkgs` /
`IsNativeOnlyInVM` package is injected into the VM as *native* code and never lowered to
bytecode (`interp/externs.bn`: "substrate builtins have no bytecode-runnable surface the
VM needs to lower"). If `debug` were a builtin, `debug.Callers` would run as native code
walking `execLoop`'s **C stack** instead of the interpreted VM frames — defeating VM
capture. As an ordinary package it is lowered to bytecode in the VM (so the intrinsic
becomes the VM frame-walk op) and LLVM-compiled in AOT (so the intrinsic becomes the FP
walk). It likely lives under `pkg/std/debug` (an ordinary stdlib package), not
`builtins/`.

## The library surface (`debug`)

```
// capture — intrinsic-backed, no allocation (fills a caller-owned buffer)
func Callers(skip int, into *[]Frame) int      // returns TOTAL depth available (see below)
func Caller(skip int) (Frame, bool)            // the single skip-th frame

// symbolize — separate, on-demand, mode-dispatched
func Symbolize(f Frame) FrameInfo

// ergonomic all-in-one (allocates a right-sized owned stack)
func Stacktrace(skip int) @[]FrameInfo
```

- `Frame` — an opaque, **value-type, no-managed-refs** token: `{ Mode; <locator> }`
  where the native locator is a return address (`*uint8`) and the VM locator is
  `{ FuncIdx, Pc }`. No managed references ⇒ `Callers` into a `*[]Frame` allocates
  nothing (also the property the Tier-B fault path needs). Ordinary struct via
  `pkg/types`, so both backends and the VM agree on its layout.
- `FrameInfo` — `{ Mode; Ok; Pkg @[]char; Func @[]char; Mangled *[]readonly char }`.
  **`Ok`** distinguishes a symbolized frame from an address that falls outside every
  table range (a compiler-generated shim, a `__c_call` C frame, a clang-runtime frame) —
  when `!Ok`, `Pkg`/`Func` are empty and the caller still has the raw `Frame`. `Func` is
  the member chain joined (a method reads `Type.Method`). `Mangled` can be a zero-copy
  borrow into the always-live table's string blob. (Tier B adds `File @[]char; Line int`.)
- **`skip` convention (ONE scheme, stated unambiguously):** each library entry drops its
  *own* frame, so **`skip = 0` is the frame that called the debug function** (your own
  frame); `skip = 1` is your caller (the proximal caller of you); etc. Indices are stable
  regardless of which entry you use — `Caller`, `Callers`, and `Stacktrace` each embed the
  intrinsic in their own body (or delegate with an explicit `+1`) so no extra library
  frame leaks in. (This is NOT `runtime.Callers`' numbering — we auto-drop the library
  frame; do not describe it as matching Go.)
- **Truncation & sizing:** `Callers` returns the **total** number of frames available
  at/above `skip`, while writing at most `len(into)`. So truncation is detectable
  (`total > len(into)`), and `Stacktrace` sizes its owned slice in two passes (query depth
  with a small/empty buffer, allocate, capture). `Caller` returns `(Frame, bool)` where
  `false` means "no frame at that depth."
- **Segment bottom:** a single capture covers one mode segment (see cross-mode section);
  the capture reports whether it bottomed out at a genuine **program entry** vs a **mode
  boundary** (exact encoding — a companion flag or a `Frame` kind — is an impl detail),
  so "stack truly ends here" is distinguishable from "more frames exist in the other mode."

## The capture intrinsic

Model on the existing `_func_handle` keyword-token intrinsic (`token.RAW_FUNC_ADDR` →
`EXPR_BUILTIN` → `checkBuiltinCall` shape check → IR op → per-backend lowering). Working
name `_stack_frames(skip int, into *[]Frame) int`, returning total depth. The `debug`
entries embed it directly so it expands *inline* in their bodies (the "current frame" at
expansion is the library entry's frame, which it drops per the skip convention).

Lowering — **two** targets (NOT the self-hosted native backends; see the compilation
fact above):

- **`codegen` (LLVM)** — emit IR that reads the frame pointer and walks the standard FP
  chain (`return address = [fp + 1 word]`, `next fp = [fp]`), or uses
  `llvm.frameaddress`/`llvm.returnaddress`; skip `skip+1` frames, fill `into` up to
  `len(into)`, return total depth. Correct because all frames keep an FP chain at `-O0`
  (+ `-fno-omit-frame-pointer`). This lowering compiles `debug` for *every* AOT target,
  including when the main module uses the self-hosted native backend (whose x64/aarch64
  frames share the same FP convention).
- **VM** — a new bytecode op whose `execLoop` handler walks the 6-word frame headers from
  the **live** `regsOff` (a loop local) back through `savedRegsOff` to the entry frame
  (`savedPC == -1`), emitting `{FuncIdx = savedFuncIdx, Pc = savedPC}` per level, seeding
  the walk with the current top `funcIdx` (the header stores the *caller's* funcIdx). Same
  header chase `BC_RETURN` already performs.

**VM current-frame publish hook.** The live `(funcIdx, pc, regsOff)` are `execLoop`
C-stack locals, not on the VM struct. The new op is dispatched from within `execLoop`, so
it has them directly (thread them into the handler exactly as `vmPollPoint` is). No new
persistent VM field is needed for the explicit path (a persistent current-frame field is
a Tier-B fault-path concern).

## The loaded symbolization table (native / AOT)

The native side of "names in memory, format we own." Because functions are split across
many separately-compiled objects (all deps via LLVM; the main module via either backend),
the table is emitted as **per-package fragments linked into a dependency graph and merged
at runtime** — the decentralized model developed and proven first in
[`plan-rtti-decentralize.md`](plan-rtti-decentralize.md) (the RTTI/satentry PoC). Each
package emits its own `_pkg_symtab` fragment (a fresh symbol — no dual-purpose hazard like
RTTI's `_pkg_satentries`) carrying (a) its own functions and (b) symrefs to its **direct**
dependencies' `_pkg_symtab`; the runtime graph-walks from the main module's fragment (dedup
by fragment address), then **sorts the merged set by address after relocation** (addresses
are relocation-deferred, so the sort cannot happen at emit time). This — rather than a
main-module-enumerated flat root — is what lets a backtrace symbolize functions in an
opaque binary-blob dependency the final program never named (the fragment's undefined
dep-symrefs pull and retain the blob's internal objects; see the RTTI plan). Every package
emits a fragment even with no relevant entries, since it is a graph **waypoint**.

Retention is per-backend (as the RTTI PoC establishes): main's `_pkg_symtab` is pinned via
**`@llvm.used`** on the LLVM build and via the **`__entry` LEA reloc** on the native build
(the `__entry` reference is a folded no-op under LLVM, so `@llvm.used` is mandatory there);
the strong dep-symref chain then transitively retains descendants.

Each fragment entry is `{ startAddr, nameOffset }` (start address via a `DT_SYMREF`
absolute relocation against the function's symbol — the mechanism the data layer already
has; `irdata/data_global.bn`), plus a string blob of mangled names; the fragment is
**length-prefixed** (self-describing) so edges carry no counts and editing a package body
never touches another package's object. **No per-function size field** — the asm layer has
no label-difference/size reloc (`SetSize` etc. do not exist), so size is net-new machinery
we avoid: `Symbolize` attributes an address to the **nearest start-address at or below it**
in the merged sorted set (a return address always lands inside its function), with an
end-sentinel per fragment to bound the last function. An address below every start / above
the last sentinel ⇒ `FrameInfo.Ok = false`.

`Symbolize` for a native frame: binary-search the sorted table by address → `nameOffset`
→ borrow the mangled string → `Demangle` → render. Gate emission behind a build flag
(default **on**; a `--no-symbol-table` to drop it for size-sensitive builds — default is
an open sub-decision, see below). Leaves the ELF/Mach-O `.symtab` untouched (orthogonal).

Layering: which functions + their mangled names is language-level/shared (mangling is
shared); the address-range fragment emission is per-backend (codegen + native each emit
their own objects' fragments). Matches `ir-backend-guidelines.md`.

Why this over the alternatives: reading our own binary needs a filesystem (fails on bare
metal) + a net-new ELF *and* Mach-O reader + still hits `STT_NOTYPE`/size-0 coarseness;
DWARF (`-g`) only exists on LLVM, is off by default, lives in unmapped `.debug_*`, and
needs a DWARF parser. Both drag in format parsers we don't have and a filesystem we can't
assume.

## VM symbolization

VM frames symbolize via `VMFunc.Name`, which is the **canonical fully-qualified dotted
form** (`"pkg/binate/asm.New"` — `vm/lower_func.bn` passes `f.Name` through unchanged),
already in memory. So VM `Symbolize` needs no table: split `VMFunc.Name` on the last `/`
and `.` into `{Pkg, Func}`. The only real cross-mode difference is that native names are
mangled (need `Demangle` + render) while VM names are already readable full-path — the
package *granularity is identical* in both modes (there is no short-vs-full asymmetry).

## Generic-name renderer

`Demangle` exists and is production runtime code, but returns generic type-args as
verbatim lp-encoded strings and has no human-readable renderer. Add one in
`pkg/binate/mangle`: decode the TypeArg sub-language (`p`→`*`, `m`→`@`, `s`→`*[]`,
`M`→`@[]`, `a<len>_`→`[len]`, `r`→`readonly`, `i`/`j`→iface, `f`/`F`/`g`→func sigs,
`S`→anon struct, `N`→named/primitive leaf) plus joining `Pkg`/`Members`. Expose as
`mangle.Readable(sym) @[]char`. `Symbolize` uses it. This is the one net-new library piece
for readable *generic* frames; non-generic names are a trivial join.

## arm32 frame-pointer chain (non-trivial)

arm32's prologue `push {r4-r12, lr}` (mask `0x5FF0` = `{r4-r12, lr}`; `r4..r12` alone is
`0x1FF0`) saves both r11 and lr but never anchors r11 at the frame, so the stack is not
walkable beyond the current frame. **This is more than a one-instruction fix:** the single
FP walk (running in clang-compiled `debug` code) must traverse *both* clang arm32 frames
*and* self-hosted arm32 main-module frames with one offset rule, so the self-hosted
prologue must adopt **clang's AAPCS arm32 FP-frame convention** — an adjacent
`{fp, lr}`-anchored pair with `[fp]` = caller's fp and `[fp + 4]` = saved lr — rather than
leaving lr 8 bytes above saved-r11 (r12 currently sits between them). That means
restructuring the prologue/epilogue (and its `PlanFrame`/`SAVED_BYTES` offset math), not
just adding `mov r11, sp`.

**Risk / verification (load-bearing):** verify with the **native** arm32 conformance mode
— `builder-comp_native_arm32_baremetal` (the `native` is load-bearing; the non-`native`
`builder-comp_arm32_baremetal` is the LLVM path and would NOT exercise this code). The
native arm32 backend is currently incomplete (~2026 pass / 611 fail per
`plan-native-arm32.md`); land this without regressing that baseline. Because of this, the
headline scopes arm32 as **best-effort / gated**, not delivered alongside x64/aarch64.

## Faithfulness contract (documented, best-effort, both directions)

> A captured stack is a best-effort view of the physical call stack, not a guaranteed
> 1:1 record of logical calls.
> - **Frames may be absent.** Compiler-generated transparent thunks (func-value/closure
>   shims that tail-jump) do not appear as frames. On the LLVM backend, optimization
>   (inlining, tail-call elimination, frame-pointer omission) could merge or drop frames;
>   we pin `-fno-omit-frame-pointer` and build deps at `-O0`, but do not otherwise
>   guarantee against future opt levels.
> - **Frames may be present but unsymbolizable.** Some real frames are *not* source
>   functions — non-tail func-value/aggregate-return shims that CALL (establishing a
>   frame), `__c_call` C frames, and clang-runtime frames. These fall outside the
>   symbolization table and surface as `FrameInfo.Ok == false` (raw address only), NOT as
>   a legitimate function with an empty name. Callers must handle `!Ok`.

Forward-looking reservation (not built): recovering inlined *logical* frames (once
inlining lands) needs inline-frame metadata — a Tier-B+ decision, not owed now. Whether
shim/thunk ranges should instead be emitted into the table with synthetic names (vs left
`!Ok`) is an open sub-decision below.

## Cross-mode single capture (a real Tier-A limitation, not just deferred stitching)

Tier A defers cross-mode *stitching* to Tier B, but a single `Callers()` at a mode
boundary has behavior that must be **documented, not silent**:

- A **VM-segment** capture stops at the `execFunc` entry frame (`savedPC == -1`). If that
  entry was a native→VM re-entry (a trampoline/`CallFunc`), there are more frames *below*
  in native — so the capture reports "bottomed at a mode boundary," distinct from "bottomed
  at program entry."
- A **native-segment** capture taken inside a VM-invoked extern (`__c_call`-style, no VM
  frame pushed) walks *up* through the interpreter's own frames (`execExternCall` →
  `execLoop` → `execFunc`) as if they were user frames — so native captures may include
  interpreter-internal frames until Tier-B stitching learns to elide/label them.

Tier A therefore returns a **single-mode segment**, tags each `Frame` with its `Mode`, and
exposes the bottom-reason (program-entry vs mode-boundary). Full stitching across the
boundary is Tier B.

## Phasing (each commit independently green & cherry-pickable)

0. **RTTI/satentry decentralization PoC** ([`plan-rtti-decentralize.md`](plan-rtti-decentralize.md)).
   Convert the native `_satentry_root` from a main-enumerated flat root to per-package
   graph-linked fragments, proving the decentralized approach on an existing tested
   subsystem *before* the symbolization table reuses it. Precedes phase 2.
1. **`debug` library skeleton + capture intrinsic (LLVM + VM lowering).** Ordinary
   `pkg/std/debug` package; `Frame`/`FrameInfo` types; the intrinsic through
   token/lexer/parser/checker/IR; **codegen (LLVM) FP-walk lowering** + **VM bytecode op**
   with `execLoop` walk + publish threading; `Callers`/`Caller` returning raw `Frame`s
   (total-depth semantics). Green in the **default (LLVM) modes AND the `-int` (VM) modes**
   — the intrinsic is handled in both backends `debug` is ever compiled by, so no
   fail-loud-on-unimplemented-op. (`-fno-omit-frame-pointer` added here.) First milestone:
   raw capture depth/identity verified in both modes.
2. **Loaded symbolization table + native `Symbolize`.** Per-package graph-linked
   `_pkg_symtab` fragments (codegen + native, reusing the phase-0 pattern), runtime
   graph-merge + sort, nearest-start-below lookup, `Ok` handling, `--no-symbol-table`
   flag. VM `Symbolize` via `VMFunc.Name`.
3. **Generic renderer + `Symbolize`/`Stacktrace` completion.** End-to-end readable
   `{Pkg, Func}` backtraces in both modes; `!Ok` frames surfaced correctly.
4. **arm32 clang-compatible FP frame + fragment emission.** Restructure the self-hosted
   arm32 prologue/epilogue to AAPCS FP convention; verify against
   `builder-comp_native_arm32_baremetal` without regressing its baseline.

Tests per phase: conformance tests exercising known call depths/identities in both modes;
unit tests for the renderer, the table lookup, and `!Ok` handling. Bug-discovery protocol
applies.

## BUILDER / layering constraints

- **The intrinsic is define-only-safe in the BUILDER tree, use-only-outside.** Its
  *definition* (a `token` enum entry, `checkBuiltinCall`/IR/`codegen` switch cases) lives
  in BUILDER-compiled packages and is fine (adding an enum arm + switch case is
  BUILDER-accepted, like `unsafe_cast`). The new keyword must NOT be *used* anywhere in
  cmd/bnc's BUILDER-compiled tree, or gen1 breaks — it is used only in `pkg/std/debug`,
  compiled by the fresh bnc, never in cmd/bnc's tree. (The self-hosted native backends are
  BUILDER-compiled but never *lower* the intrinsic, so nothing there references the new op
  beyond an unreachable/absent switch arm.)
- **`debug` library code** is outside the BUILDER tree ⇒ full language available.
- **`Frame`/`FrameInfo` layout** is a cross-mode contract ⇒ ordinary structs via
  `pkg/types`. No backend-local layout.
- **Symbolization manifest** (function → mangled name) is shared/language-level; only the
  per-object fragment emission is per-backend.

## Risks

1. **The native story rests on "deps are LLVM-compiled."** The intrinsic's real home is
   `codegen`, and the symbol table is a per-package graph of fragments — both folded in
   above. If the native backend ever grows to compile deps, the table already handles it
   (per-package fragments), but the intrinsic would then also need self-hosted lowering.
2. **arm32 is a real backend change** (AAPCS FP convention) against an incomplete native
   backend — verify with the `native` arm32 mode; scoped as best-effort in the headline.
3. **clang frame-pointer reliance** — mitigated by `-fno-omit-frame-pointer` + `-O0`
   deps; re-verify if bnc ever raises the opt level.
4. **Binary size** from the always-present table — mitigated by `--no-symbol-table`.
5. **`!Ok` / shim frames** — the contract and `FrameInfo.Ok` make table-misses explicit
   rather than silently mis-symbolized.
6. **Tier-B entanglement not pulled in early** — file:line, cross-mode stitching, on-fault
   capture out of scope; fault-path correctness gaps noted only so the on-fault follow-up
   is not layered on them prematurely.

## Open sub-decisions (resolve during impl or surface)

- Exact intrinsic spelling: keyword-token (`_stack_frames`) vs IR-magic-by-name.
- `--no-symbol-table` default (on vs off) & name — weighs backtrace-usability vs binary
  size on the bare-metal target.
- Shim/thunk address ranges: emit into the table with synthetic names, vs leave `!Ok`.
- Encoding of the capture's bottom-reason (companion flag vs a `Frame` kind).
