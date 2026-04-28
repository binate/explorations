# Plan: ARM32 Bare-Metal Target

> **Status: DRAFT** — initial sketch for review. Not yet ratified;
> details (board choice, runtime split, milestone gating) likely to
> shift before implementation begins. See the corresponding entry in
> `claude-todo.md` ("ARM32 bare-metal target — MAJOR PROJECT") for
> the original framing.

## Why

Binate aspires to be usable as an OS-development language. The first
concrete target is **ARM32 bare-metal** (Cortex-A class), running
without a host OS underneath: no libc, no kernel, no filesystem, no
argv. Code runs directly on a board (real or QEMU-emulated), boots
from reset, and uses MMIO / semihosting for I/O.

This is distinct from "ARM32 Linux" (which would just need a new
LLVM target triple plus an `armv7-linux-gnueabihf` libc). Bare-metal
forces the runtime story we eventually want anyway:

- Allocator implemented in Binate (no `malloc`).
- Memory ops (`memset`/`memcpy`) implemented in Binate (no `<string.h>`).
- Exit/abort/panic implemented via target primitives (semihosting on
  QEMU, `wfi` loop or reset on real hardware).
- I/O implemented via UART driver or semihosting — no `write(2)`.

Once bare-metal works, the same runtime split makes adding kernel /
freestanding-Linux / WASM targets cheaper.

## What already works

The compiler already has substantial ARM32 substrate, all of which
predates this plan:

- **`pkg/asm/arm32`** encodes ARMv7-A instructions (data-processing,
  load/store, multiply/divide, branches, system). 73 unit tests pin
  bit patterns. Assembler-side is essentially done.
- **`pkg/asm/elf`** emits ELF32 with the ARM32 reloc set
  (`R_ARM_JUMP24`, `R_ARM_ABS32`). Existing end-to-end tests in
  `pkg/asm/elf/elf_test.bn` already link with `arm-none-eabi-ld`
  (bare-metal linker) and run under `qemu-system-arm -semihosting`
  on the `virt` machine. Three tests: exit, loop sum, function call.
- **`cmd/bnas`** accepts `.arch arm32` and routes through the ARM32
  instruction parser.

In other words: the back end can emit ARM32 ELF binaries today, and
QEMU-semihosting is already part of the test loop. What's missing is
**(a)** an IR-to-ARM32 lowering, and **(b)** a bare-metal runtime
port.

## What's missing

### Runtime substrate

Things `pkg/rt` and `pkg/bootstrap` currently get from the host that
don't exist on bare metal:

| Surface | Today (libc target) | Bare-metal target |
|---|---|---|
| `malloc` / `free` / `calloc` | `bn_rt__c_malloc` etc. wrap libc | Binate-implemented allocator (bump first, heap second) |
| `memset` / `memcpy` | libc | Pure-Binate (or tiny inline asm later) |
| `exit` / `abort` | libc `exit(2)` | Semihosting `SYS_EXIT_EXTENDED` (QEMU) / `wfi` loop (real HW) |
| `bootstrap.Write` | host `write(2)` | UART driver or semihosting `SYS_WRITE0` |
| `bootstrap.Open` / `Read` / `Stat` / `ReadDir` | filesystem syscalls | Excluded from bare-metal (no FS) |
| `bootstrap.Args` | argv | Empty / synthesized (see `bootstrap.bni` shape below) |
| `bootstrap.Exec` | `posix_spawn` | Excluded |

The "Un-export `rt.c_*`" TODO is a direct prerequisite — it pulls the
libc-shaped C bridges off the public surface so they can be swapped
per target. That work should land first.

### IR-to-machine-code lowering for ARM32

Two paths, both worth pursuing in order:

1. **LLVM-via-clang first.** The compiler's LLVM backend already
   emits LLVM IR. Pass `--target=armv7a-none-eabi -mfloat-abi=soft`
   to clang, link with `arm-none-eabi-ld` (already available — used
   by the `pkg/asm/elf` tests). Fastest to first-light. Validates
   the runtime/boot/linker story without committing to a native
   ARM32 backend yet.
2. **Native `pkg/native/arm32`** as a second milestone. Full sibling
   of `pkg/native/arm64`. AAPCS32 calling convention (NGRN over
   R0..R3, args 5+ on stack, return values in R0..R3, large-
   aggregate return via the hidden pointer in R0). ELF32 only — no
   Mach-O. No external dependency once written. Closer to the OS-
   language goal of "no LLVM at runtime."

### Boot story

A tiny crt0 (asm or, eventually, Binate inline-asm) that:

1. Sets up the stack pointer.
2. Zeroes BSS.
3. Optionally copies `.data` from flash to RAM (if running from ROM).
4. Calls into Binate's `main`.

A linker script per board, defining the memory map (text/rodata in
flash or low RAM, data/BSS in RAM, stack at top of RAM, optional MMU
page tables for A-class).

## Target boards

For v1, plan to support exactly **two** boards:

1. **QEMU `virt` machine** (primary dev target). Already used by the
   `pkg/asm/elf` semihosting tests. Memory at 0x40000000, PL011
   UART, GIC-v2 (irrelevant until we add interrupts). Conventions
   well-documented; `arm-none-eabi-{as,ld}` Just Works.
2. **One real Cortex-A board** — TBD. Candidates: Raspberry Pi (large
   community but boot is awful — proprietary GPU firmware), BeagleBone
   (cleaner boot), STM32MP1 dev board (Cortex-A7 + Cortex-M4 — useful
   for OS work). Pick after v1 is running on QEMU. Real-hardware
   support is not on the v1 critical path; QEMU is sufficient for
   the conformance suite.

Cortex-M (Thumb-only, no MMU, no caches, much smaller RAM) is **not**
in scope for v1. The instruction-encoding overlap is large but the
runtime story diverges (e.g., no real allocator on most M-class
boards). Revisit after v1.

## Allocator design

### Phase 1: bump allocator (v1 milestone)

Simplest possible. A single backing region (`heap_start..heap_end`
defined by the linker script), a bump pointer, no free, no fragmentation
handling. Allocation = `bump_ptr += size; return old_bump_ptr;`. Out-
of-memory = panic via semihosting exit.

This is enough for every conformance test that doesn't actually run
out of memory. It exercises the full managed-pointer / managed-slice
allocation surface — the only thing it doesn't test is reuse of
freed allocations, which is fine for a v1 milestone aimed at
validating "code runs at all on bare metal."

### Phase 2: real heap (v2)

A free-list or buddy allocator, replacing the bump allocator behind
the same `rt.Malloc` / `rt.CFree` surface (post-unexport). At this
point reference-counted free actually returns memory.

Open question: does a generational / region allocator make sense for
managed pointers, given Binate's refcount-only model? Probably not
worth the complexity for v2; revisit after v1 is shipping.

## Bare-metal `bootstrap.bni` shape

Today's `bootstrap.bni` is libc-shaped: `Open`, `Close`, `Read`,
`Write`, `Stat`, `ReadDir`, `Args`, `Exec`, `Exit`, plus pure-Binate
helpers (`Concat`, `Itoa`). On bare metal:

- **Drop** entirely: `Open`, `Close`, `Read`, `Stat`, `ReadDir`,
  `Exec`. There is no filesystem, no process model. Code that uses
  these (the compiler's source loader, the conformance runner's
  fixture loader) cannot run on bare metal, period.
- **Keep** with target-specific bodies: `Write` (semihosting
  `SYS_WRITE0` for v1; UART driver later), `Exit` (semihosting
  `SYS_EXIT_EXTENDED`), `Args` (returns empty `@[]@[]char` — there
  is no argv on bare metal).
- **Keep** unchanged (pure-Binate, no I/O): `Concat`, `Itoa`,
  `formatInt`, `formatBool`, `formatFloat`.

Practically this means a separate `pkg/bootstrap_baremetal.bn` (or
similar — exact name TBD) with the bare-metal-only implementations,
selected at build time. The `.bni` declares the union of names; the
bare-metal build supplies stubs for the dropped operations that
panic if called.

## Inventory: `bootstrap.*` calls in self-hosted code

Grep the self-hosted tree for every `bootstrap.*` call site and
classify:

- **OK on bare metal** (bare-metal stub returns sensible value): used
  by self-hosted code that can run bare-metal.
- **Excluded from bare metal** (bare-metal stub panics): used by the
  compiler / loader / conformance runner / file-based tooling; this
  code does not run on bare metal.

Each excluded site has a corresponding conformance test that's
excluded from the bare-metal mode (similar to how the existing
`.xfail.<mode>` files work for known-failing tests).

The detailed inventory (which packages, which call sites, expected
behavior under bare-metal) is **TODO for a follow-up doc** —
probably a per-package walk through `cmd/bnc`, `cmd/bnas`,
`pkg/loader`, `pkg/conformance`, etc.

## Testing

The existing `pkg/asm/elf` semihosting harness scales up directly:

- Conformance programs that don't touch file I/O / argv / dirs link
  against the bare-metal runtime, run under `qemu-system-arm -M virt
  -semihosting`, and report results via semihosting `SYS_EXIT_EXTENDED`
  (status code) and `SYS_WRITE0` (println output).
- `conformance/run.sh boot-comp_arm32_baremetal` (name TBD) runs the
  filtered subset.
- Probably 200+ of the existing 278 conformance tests qualify
  (arithmetic, control flow, structs, slices, managed pointers,
  methods, etc.). Tests that need `bootstrap.Open` / `Read` / `Args`
  / `Stat` / `ReadDir` / `Exec` are excluded for v1.

## Milestones

### v1: LLVM-via-clang on QEMU virt

Goal: a meaningful subset of the conformance suite passing on QEMU
ARM32 virt machine via the LLVM backend, with a Binate allocator
and semihosting I/O.

1. **Un-export `rt.c_*`** (separate TODO; prerequisite). All callers
   go through Binate wrappers; c_* declarations move to package-
   private. Allows swapping the libc bridges per target.
2. **Bump allocator** in `pkg/rt` (or `pkg/rt/alloc_baremetal.bn`).
   Same surface as the libc-bridge path; build-mode-selected.
3. **Semihosting `SYS_EXIT_EXTENDED` + `SYS_WRITE0`** wired into the
   bare-metal `bootstrap.Exit` and `bootstrap.Write`. Inline-asm
   semihosting calls — same convention `pkg/asm/elf` tests already
   use.
4. **Pure-Binate `memset` / `memcpy`** (or tiny inline-asm wrappers
   if/when inline asm lands).
5. **crt0 + linker script** for the QEMU virt machine. Provided as a
   per-target file under `runtime/baremetal_arm32/` (or similar).
6. **Build-mode wiring**: `bnc` flag (or build-mode arg in
   `conformance/run.sh`) that selects the bare-metal runtime + linker
   script + clang triple.
7. **Conformance filter**: tests requiring `bootstrap.Open` / `Read` /
   `Args` / `Stat` / `ReadDir` / `Exec` get `.xfail` markers (or a
   shared exclude list) for the bare-metal mode.
8. **CI**: the GitHub Actions matrix gains a bare-metal job (Linux
   runner, install `qemu-system-arm` + `gcc-arm-none-eabi`, run the
   filtered conformance subset).

### v2: real heap allocator

Replace the bump allocator with a free-list or buddy allocator. Same
external surface. Refcount-driven free actually returns memory.

### v3: real hardware

Pick one Cortex-A board (TBD — see "Target boards" above), write the
crt0 + linker script for it, write a UART driver. Switch from
semihosting I/O to UART for that board's runtime. Real-hardware
testing involves either physical hardware or a high-fidelity emulator
(QEMU's Raspberry Pi machine, etc.).

### v4: native `pkg/native/arm32` backend

Drop the LLVM dependency. Full sibling of `pkg/native/arm64`. Same
runtime, same boot story, same tests — just a different code-emission
path.

## Open questions

- **Float ABI**: `-mfloat-abi=soft` for v1 (no VFP required, simpler
  ABI). Switch to `softfp` or `hard` later if VFP buys us anything
  on real hardware. The existing `pkg/asm/arm32` doesn't yet encode
  VFP/NEON instructions — that's separate work.
- **MMU**: v1 runs flat (no virtual memory). Cortex-A boards usually
  have an MMU; setting up identity-mapped page tables is a small
  amount of asm in crt0. Defer until needed.
- **Interrupts**: out of scope until we're writing actual OS code.
  v1 conformance tests don't need interrupts.
- **Stack overflow detection**: bare metal has no `MAP_GROWSDOWN`.
  Either reserve a fixed stack and crash on overflow, or set up a
  guard page via MMU (only possible after MMU work). v1: fixed stack,
  best-effort.
- **C++-style global constructors**: not currently a concern (Binate
  has none), but if we ever add init-time hooks, crt0 needs to call
  them.

## Cross-references

- `claude-todo.md` — "ARM32 bare-metal target — MAJOR PROJECT"
  (TODO entry that motivated this plan).
- `claude-todo.md` — "Un-export `rt.c_*`" (direct prerequisite).
- `pkg/asm/elf/elf_test.bn` — existing QEMU-semihosting test
  harness; the v1 conformance runner extends this pattern.
- `pkg/asm/arm32/` — existing ARM32 instruction encoder.
- `runtime-abstraction-plan.md` — broader runtime portability story.
