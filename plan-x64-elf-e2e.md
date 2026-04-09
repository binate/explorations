# Plan: x86-64 ELF End-to-End Tests on Linux CI

## Goal

Add end-to-end tests that assemble x86-64 → ELF64 → link → run natively on Linux x86-64. This validates the full x86-64 pipeline on CI without needing QEMU or Rosetta.

## Approach

The existing x86-64 Mach-O end-to-end tests (in `pkg/asm/macho/macho_x64_test.bn`) use macOS syscall conventions and Mach-O output. The ELF tests need:

1. **Linux x86-64 syscall convention**: `syscall` instruction with RAX = syscall number (not `0x2000000 | n` like macOS). `SYS_exit = 60`, `SYS_write = 1`. Arguments in RDI, RSI, RDX, R10, R8, R9.

2. **Linking**: `cc -nostdlib -static -o exe obj.o` on Linux (no `-lSystem` needed, no `-arch` flag).

3. **Probe**: detect Linux by checking for `readelf` or `cc -static` availability. The ARM32 semihosting tests already use a similar probe (`canLinkElf`).

4. **Entry point**: `_start` (not `_main` — Linux static binaries use `_start` without the underscore prefix convention).

## Test Cases

Mirror the Mach-O x86-64 tests:

- **Exit**: `mov eax, 60; mov edi, 42; syscall` — validates basic encoding + ELF64 + linking
- **Loop**: sum 1..9, exit with 45 — validates branches and fixup resolution
- **Function call**: CALL/RET with stack frame — validates cross-label CALL fixups
- **Cross-object**: two `.o` files linked together — validates ELF64 relocations (R_X86_64_PC32)

## Location

Tests go in `pkg/asm/elf/elf_test.bn` (or a new `elf_x64_test.bn` if the file gets too large). They sit alongside the existing AArch64 ELF link-and-run test (`TestElfLinkAndRun`) and the ARM32 semihosting tests.

## Prerequisites

- x86-64 ELF relocation mapping: already done (`FIX_REL32 → R_X86_64_PC32`)
- ELF64 symbol ordering: already fixed (locals before globals)
- CI environment: needs `cc` (gcc or clang) on Linux x86-64 — should already be available

## Effort

Small — mostly adapting the existing Mach-O x86-64 test code to use Linux syscall numbers, ELF output, and Linux linking flags. ~1 hour.
