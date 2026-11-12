# Plan: a comprehensive x86-64 assembler

Tracked by the claude-todo.md entry "bnas x64 → ELF: typical integer programs LANDED; SSE/exotic-addressing remain"
(the x86-64 parser coverage, claimed 2026-11-11, work-2).  User (2026-10-18): do it after the TLS operators
(plan-tls.md stage 3, complete 2026-11-11).

## Where it starts (2026-11-11)

- `pkg/binate/asm/parse/x64*.bn` (the text parser) knows ~35 mnemonics: mov, movabs, lea, add / sub / and / or /
  xor / cmp, test, shl / shr / sar, imul (2- and 3-operand), push / pop, inc / dec / neg / not, div / idiv, jcc /
  jmp / call (direct, register, memory), ret / retn (with imm16), nop, syscall, cqo, cdq, ud2, int3, int, and the
  prefixes written as instructions (data16, rex64, the segment names).  Operands: registers, immediates
  (`offset sym`, the thread-local offsets), memory with base / index / scale / displacement, a symbol, `rip`,
  segment overrides, the displacement before the brackets, several bracket groups (TLS stage 3d).
- `pkg/binate/asm/x64` (the encoders, ~70 functions) has what the native backend uses: the above plus setcc,
  movzx / movsx / movsxd, the one-operand mul / imul, multi-byte nops, rep stosb / stosq, four x87 loads / stores;
  `asm/x64/sse` ~90 SSE / SSE2 forms (moves, scalar and packed arithmetic, compares, conversions, shuffles,
  unpacks, pshufb).  The parser reaches none of setcc / movzx / x87 / SSE.
- Missing in both: adc / sbb, the rotates (rol / ror / rcl / rcr), shld / shrd, bt / bts / btr / btc, bsf / bsr,
  lzcnt / tzcnt / popcnt, bswap, xchg / xadd / cmpxchg / cmpxchg8b / cmpxchg16b, cmovcc, the string instructions
  (movs / cmps / scas / lods / stos at every size, rep / repe / repne), the lock prefix, enter / leave, loop /
  jrcxz, cbw / cwde / cdqe / cwd, lahf / sahf, the flag instructions, hlt / pause, cpuid / rdtsc / rdtscp, the
  fences, prefetch, BMI1 / BMI2 / ADX / MOVBE / CRC32, the system instructions (control / debug register moves,
  msr, iret, sysret, the descriptor tables, cli / sti, in / out), the rest of x87, most of SSE through SSE4.2 and
  AES / CLMUL / SHA, all of VEX (AVX, AVX2, FMA, F16C, the VEX-encoded BMI), all of EVEX (AVX-512), APX (r16-r31,
  REX2, the new-data-destination forms), AMX.
- Encoding fidelity gaps (claude-todo.md, the same entry): no accumulator short forms (`cmp al, 1` is 80 F8 01
  where clang has 3C 01; `test eax, imm32` F7 C0 where clang has A9), every jump to a known label is rel32 where
  clang relaxes to rel8 (so a label difference across a jump differs from clang's), no moffs forms (`movabs rax,
  [x]`), no CALL / JMP through `[rip + x@GOTPCREL]`, no 32-bit address size (`[eax]`, the 0x67 prefix; user
  2026-10-06: "Support it, later").

## What clang takes

clang's x86-64 assembler gates nothing by CPU feature (probed 2026-11-11, clang 22, `--target=x86_64-linux-gnu`
with no `-m` flags): `vaddps zmm0, zmm1, zmm2` (AVX-512), `add r16, r17` (APX), `tileloadd tmm0, [rax + rbx]`
(AMX), `pdep`, `sha256rnds2`, `mov cr0, rax`, `wrmsr` all assemble.  So unlike arm32 there is no feature model to
build: the target is "every instruction clang takes, with clang's encoding", gated only by the 64-bit mode
(instructions invalid in 64-bit mode — `aaa`, `pusha`, `into`, ... — rejected as clang rejects them).

## Proposed stages (each landed on its own, checked by a differential harness against clang)

As for arm32: each instruction class one commit, its operand combinations (valid and invalid: every register
class and width, REX-needing registers, immediates at and past their ranges, every addressing form, clang's
aliases and alternative spellings) compared with clang's bytes and relocations, the cases pinned as in-tree
goldens.  The encoders stay in `asm/x64` (exact: each emits the one instruction its operands name or sets an
error), split by class into sub-packages as they grow, as `asm/x64/sse` already is.

1. **The general-purpose instructions**, every one clang takes in 64-bit mode, sub-commits by class:
   - 1a. ALU completeness: adc / sbb; the accumulator short forms (clang's encodings); xchg; push of an
     immediate; cbw / cwde / cdqe / cwd; lahf / sahf; the flag instructions; the moffs forms of mov / movabs.
   - 1b. Shifts and rotates (rol / ror / rcl / rcr, by 1 / imm8 / cl), shld / shrd; mul and the one-operand imul,
     the two-operand imul with an immediate.
   - 1c. Bit instructions: bt / bts / btr / btc (register and immediate), bsf / bsr, lzcnt / tzcnt / popcnt,
     bswap.
   - 1d. cmovcc, setcc, movzx / movsx / movsxd (the encoders exist).
   - 1e. Atomics: the lock prefix (and which instructions take it), xadd, cmpxchg, cmpxchg8b / cmpxchg16b.
   - 1f. The string instructions at every size, with rep / repe / repne.
   - 1g. Control: enter / leave, loop / loope / loopne / jrcxz, hlt, pause, iret / iretq, int1, CALL / JMP
     through `[rip + x@GOTPCREL]`.
   - (Jump relaxation — a jump to a label within reach rel8, as clang's — is step 12 of
     plan-native-code-placement.md, an `asm/x64` feature there; not in this series.  User 2026-11-11.)
   - 1i. The VEX-encoded integer instructions (BMI1 / BMI2: andn, bextr, blsi, blsmsk, blsr, bzhi, mulx, pdep,
     pext, rorx, sarx, shlx, shrx) and ADX, MOVBE, CRC32 — with the VEX prefix encoder stage 4 builds on.
   - 1j. The system instructions: control / debug register moves, rdmsr / wrmsr, cpuid, rdtsc / rdtscp, the
     fences, prefetch*, cli / sti, in / out, the descriptor-table loads and stores, sysret / sysenter / sysexit,
     invlpg, wbinvd, xsave / xrstor and the other state-management instructions.
2. **x87**: every form clang takes (memory sizes, st(i) forms, the no-wait aliases).
3. **SSE through SSE4.2**, AES, CLMUL and SHA (extend `asm/x64/sse`).
4. **VEX: AVX, AVX2, FMA, F16C** (`asm/x64/avx`): the 2- and 3-byte VEX prefixes, the 256-bit forms, gathers.
5. **EVEX: AVX-512** (`asm/x64/avx512`): masks (k0-k7, merging / zeroing), broadcast, embedded rounding and SAE,
   the compressed disp8*N, the 32 vector registers.
6. **APX and AMX**: r16-r31 (REX2), the EVEX-promoted general-purpose forms (new data destination, no-flags),
   push2 / pop2, ccmp / ctest, cfcmov; the AMX tile instructions.
7. **32-bit address size** (`[eax]`, `[ebx + ecx*4]`, the 0x67 prefix; user 2026-10-06: "Support it, later").

## Decisions

- Scope (user, 2026-11-11: "All, staged (Recommended)"): every instruction clang takes in 64-bit mode, in the
  order of the stages above.
- Jump relaxation (user, 2026-11-11: "Leave it to code placement (Recommended)"): not in this series; bnas gets
  clang's short jumps when plan-native-code-placement.md's step 12 lands.

## Progress

- 1a1 — landed binate `acdc70212` (2026-11-11): adc / sbb, the accumulator's short forms of the ALU group and
  TEST (in the shared encoder, so the native backend emits them too), xchg, the flag instructions (clc / stc /
  cmc / cld / std / lahf / sahf, pushf / popf), cbw / cwde / cdqe / cwd; the no-operand instructions in one
  table.  Push of an immediate was already there.

- 1a2 — landed binate `03e21a13c` (2026-11-11): the moffs forms of mov and movabs (A0-A3: `mov` with AL / AX /
  EAX / RAX and an address past a sign-extended 32 bits, `movabs` with any absolute memory operand); a 64-bit
  address elsewhere, or a MOV of one clang reads as a symbol (a label-difference constant's, a symbol's), is an
  error, where clang keeps its low 32 bits.  Also: a rejected x86-64 instruction leaves nothing in its section.

Next: 1b, shifts and rotates (rol / ror / rcl / rcr, by 1 / imm8 / cl), shld / shrd, mul and the one-operand
imul, the two-operand imul with an immediate.
