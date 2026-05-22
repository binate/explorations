# Native x86-64 Backend Plan

Status: ratified 2026-05-22 (initial scope; phases may be refined as
work lands).

## Why this exists

`pkg/native/arm64` is currently the only consumer of `pkg/native/common`,
so the "shared" common layer hasn't yet been challenged by a backend
with a meaningfully different ABI. Adding a SysV-AMD64 backend forces
that test: a 6-GP-arg / 8-SSE-arg / 16-byte-sret-threshold convention
will surface any latent AAPCS64-isms in the common layer.

Beyond shake-down value, x86-64 is independently worth having:
- CI on x86-64 Linux hardware (no QEMU emulation tax for the bulk of
  conformance runs).
- A first-light story for x86-64 Linux (and macOS-x86-64 in the
  Rosetta/legacy-hardware window) that doesn't rely on LLVM/clang.
- A second native sibling that pkg/native/arm32 can later borrow
  ABI-parameterization shape from, instead of inheriting arm64's
  assumptions wholesale.

## What's already in place

**Layout / contract layer (done in prior work):**
- `types.TargetInfo` + `SetTarget` / `GetTarget` (target-parameterized
  `SizeOf` / `AlignOf` / `FieldOffset` / `StructLayout` /
  `TrailingPadding` / slice/managed-slice/header offsets).
- `pkg/mangle` — shared name mangling.
- `pkg/ir`'s `Module.CollectStrings` / `FinalizeStrings`.
- `cmd/bnc --target` flag wiring through to `types.SetTarget`.

**Assembler / object-file layer:**
- `pkg/asm/x64` — Operand model, MOV/arith/branch/cmp/shift/SIB-mem
  encoders, syscall/int, fixup resolver. Bootstrap-runnable, unit-
  tested.
- `pkg/asm/macho.WriteX86_64(a, path)` — already exists.
- `pkg/asm/elf.WriteX86_64(a, path)` — already exists.

**Native backend infra:**
- `pkg/native/native.EmitObject(mod, arch, path)` dispatch (currently
  only routes "aarch64"/"arm64").
- `pkg/native/common` — RegMap / PlanFrame / generic predicates +
  AAPCS64 call-convention helpers (`ArgWords`, `CallArgRegStart`,
  `CallArgStackOff`, `CallStackBytes`, sret threshold logic).

**Runner shape:**
- `conformance/runners/builder-comp_native_aa64-comp_native_aa64.sh`
  pre-builds bnc with `--backend native`, then drives each
  conformance program through that native bnc. Direct template for
  the x86-64 runner.

## What needs work

### A. Factor pkg/native/common's AAPCS-isms

`pkg/native/common` today bakes in:
- 8 GP argument registers (hardcoded `8` in `CallArgRegStart` /
  `CallArgStackOff` / `CallStackBytes`).
- Sret threshold = 64 bytes for internal multi-word returns
  (`FuncReturnsBigAggregate`), 16 bytes for C-extern callees
  (`CalleeUsesCSret`).
- "Spread aggregate across N consecutive arg regs" semantics
  (AAPCS) — SysV instead passes aggregates ≤ 16 bytes in two regs
  by classification, and > 16 bytes via memory (hidden ptr in rdi).
- 8-byte word size and 16-byte stack alignment (both apply on
  SysV-AMD64 too, so no change there).

**Decision**: introduce a small `CallConv` descriptor *stored on
`RegMap`*. Helpers that need it migrate to receiver-method form
(`m.CallStackBytes(argTypes)`) and read CC from `m.CC`. Free
functions that don't have a RegMap in scope (e.g.
`FuncReturnsBigAggregate` on a raw `@ir.Func`) take `cc` as an
explicit arg or become RegMap methods if a RegMap is always
available in their caller.

```binate
type CallConv struct {
    NumGpArgRegs       int   // 8 for AAPCS64, 6 for SysV-AMD64
    NumFpArgRegs       int   // 8 for both
    InternalSretBytes  int   // > N bytes → sret  (64 for AAPCS, ? for SysV)
    CExternSretBytes   int   // > N bytes → C sret (16 for both, by coincidence)
    AggregateInRegMax  int   // aggregates ≤ N pack into regs; AAPCS = NumGpArgRegs * 8;
                             // SysV = 16
    StackAlign         int   // 16 for both
}

type RegMap struct {
    CC CallConv     // new
    // ...existing fields
}
```

Each backend constructs its CC once and stamps it onto every RegMap
it builds. The aa64 backend's CC matches today's hardcoded numbers,
so behaviour is unchanged.

Rejected alternative: module-level `var CC CallConv` with a
`SetCallConv` setter. Hides which ABI is active (the exact thing
the x64-shake-down goal wants surfaced), needs reset discipline,
and would block fat-binary / cross-target compilation in a single
bnc invocation.

### B. New package `pkg/native/x64`

Mirrors `pkg/native/arm64`'s shape:
- `x64.bn` (entry: `EmitObject(mod @ir.Module, path *[]const char)
  bool`) — calls `mod.FinalizeStrings()`, builds an assembler, walks
  funcs, resolves fixups via `x64.ResolveFixups`, writes object file
  via `macho.WriteX86_64` or `elf.WriteX86_64` based on output target.
- `x64_emit.bn` — per-op IR → x86-64 lowering.
- `x64_call.bn` — call / return lowering (SysV-AMD64-specific glue;
  hidden-sret-in-rdi handling).
- `x64_ops.bn` — arithmetic, comparison, branch op tables.
- `x64_dispatch.bn` — iface dispatch / vtable load shape.
- `x64_regmap.bn` — register-map helpers if any are x64-specific.
- `x64_iface.bn`, `x64_float.bn`, `x64_names.bn`,
  `x64_call_indirect.bn` — direct analogues of the arm64 files.

**Output format split** — Mach-O on macOS, ELF on Linux. Decided per
target triple (e.g., `x86_64-darwin` vs `x86_64-linux`), not by host.
The existing `WriteX86_64` calls handle both.

### C. Dispatch + driver wiring

- `pkg/native/native.EmitObject` gains an `"x86_64" / "x64"` branch
  routing to `x64.EmitObject`.
- `cmd/bnc` needs to know which native backend to pick. Two options:
  1. Auto-derive from `--target` (recommended). `--target host`
     reads `uname -m`; explicit `--target x86_64-linux` or
     `--target aarch64-darwin` picks the obvious one. `--backend
     native` errors out only if the target has no native backend.
  2. Separate `--native-arch` flag (rejected — duplicates info already
     in `--target`).

### D. Runner + CI

- `conformance/runners/builder-comp_native_x64-comp_native_x64.sh` —
  copy of the aa64 runner, parameterized by arch.
- Primary target: **Linux-x86-64** (available stock on GH runners; no
  Rosetta tax on Apple Silicon dev machines). macOS-x86-64 is
  best-effort / later.
- Reuse the same xfail mechanism for ABI-divergence cases that the
  arm64 backend has accumulated. Expect a similar shape: int/float
  classification, sret threshold, float-in-SSE corner cases.

## Phases

### Phase 1: Common-layer factoring (no behavior change)

1. Add `CallConv` struct in `pkg/native/common`.
2. Plumb `CallConv` through `CallArgRegStart` / `CallArgStackOff` /
   `CallStackBytes` / `FuncReturnsBigAggregate` etc. as an explicit
   parameter (or a context object the caller passes in).
3. arm64 backend constructs the AAPCS64 `CallConv` once and threads
   it through every call site that needs it.
4. **Outcome**: aa64 conformance + unit tests stay green; nothing
   else changes. This is purely a refactor to unblock Phase 2.

### Phase 2: x64 skeleton — straight to conformance + xfails

1. `pkg/native/x64` package with `EmitObject`. Start with whatever
   subset compiles cleanly enough to attempt the conformance suite;
   stub out anything that would crash with a hard error so
   bring-up failures show as "COMPILE_ERROR" in the runner, not
   wedge it.
2. Plumb through `pkg/native/native.EmitObject`'s dispatch (new
   `"x86_64" / "x64"` branch).
3. Add `--target x86_64-linux` detection in `cmd/bnc` to pick the
   x64 native backend.
4. **Outcome**: conformance runs end-to-end against the x64 backend;
   most tests xfailed; a handful green. Skip the bespoke smoke-test
   step — the conformance suite is the smoke test.

### Phase 3: Op coverage — straight-line code

Bring up enough opcode lowering to pass the bulk of arithmetic /
control-flow / pointer / struct / slice tests:
- arith: ADD/SUB/MUL/DIV/SHL/SHR/AND/OR/XOR/NEG/CMP/SETcc
- branches: Jcc / JMP / fixup resolution
- loads/stores: MOV reg/mem/imm in all sizes (8/16/32/64)
- function frame: push rbp / mov rbp,rsp / sub rsp / pop rbp / ret
- alloca: SP arithmetic
- calls: SysV-AMD64 arg-reg pack (rdi/rsi/rdx/rcx/r8/r9 + stack
  overflow), return in rax, multi-return packing in rax/rdx, sret
  via hidden rdi pointer.
- string lowering: RIP-relative LEA for `.rodata`-style references.

Track failures via xfail markers — same mechanism as aa64.

### Phase 4: Aggregates, floats, ifaces, indirect calls

- Aggregate arg/return classification (SysV's `INTEGER`/`SSE`/`MEMORY`
  word-by-word classifier — more involved than AAPCS).
- Float scalars: SSE2 (xmm0..xmm7 for args, xmm0/xmm1 for returns).
- Iface dispatch (vtable load + indirect call via `call *rax`).
- Function values + iface upcasts.

### Phase 5: Runner + CI

1. `conformance/runners/builder-comp_native_x64-comp_native_x64.sh`
   (Linux-x86-64). Add the runner alongside Phase 2 so the
   bring-up failure surface is visible from the first commit.
2. CI workflow: stock GH `ubuntu-latest` runner exercises the new
   mode from day one. Tolerate a high xfail count initially; the
   point is to keep the failure surface measurable as Phases 3 / 4
   land.
3. Audit + xfail-fix sweep against the conformance suite.

### Phase 6 (followup, not in this plan)

- Pull the ABI-parameterization shape out into a doc so future
  backends (`pkg/native/arm32`, possible `pkg/native/riscv64`)
  inherit it cleanly.
- Consider whether `pkg/native/common`'s spill-everything frame
  policy needs target-specific tuning; SysV's red zone (128 bytes
  below rsp) is something AAPCS doesn't have.

## Bootstrap-subset constraint

`pkg/native/x64` will sit in `cmd/bnc`'s transitive imports
(`cmd/bnc` already imports `pkg/native`, which will dispatch to
`pkg/native/x64`). Therefore everything in `pkg/native/x64` must
conform to the bootstrap subset — same rule that already applies to
`pkg/native/arm64`. No interfaces / generics / closures / floats /
function values / managed-slice-of-managed-slice composite literals,
etc.

`pkg/asm/x64` is already bootstrap-runnable (per CLAUDE.md's "Bootstrap
Subset Constraint" section listing it explicitly).

## Resolved decisions

1. **CallConv factoring** — `CallConv` is a field on `RegMap`;
   helpers move to receiver-method form. (Decision discussed
   2026-05-22.)
2. **First runtime target** — Linux-x86-64. macOS-x86-64 is
   best-effort / later.
3. **CI** — wire up the x86-64 Linux runner from the start, even
   while the xfail count is high.
4. **Float story** — Phases 1–3 ship with float ops xfailed; SSE2
   support lands as part of Phase 4.
5. **Phase 2 milestone** — straight to conformance + xfails, no
   bespoke smoke test.

## Non-goals

- Windows / Win64 calling convention.
- Position-independent code (PIC) — match the static-relocation shape
  the aa64 backend uses today.
- LLVM IR-as-fallback for x64 — that path already exists via the
  existing LLVM backend with `--target x86_64-linux`; this plan is
  the *native* (LLVM-free) sibling.
- AVX / AVX2 / AVX-512 register usage — SSE2 only (universal on
  x86-64).
- Frame-pointer omission, leaf-function red-zone exploitation, or
  any other size/perf optimization. Match aa64's spill-everything
  policy.
