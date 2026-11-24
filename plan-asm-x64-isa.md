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
     pext, rorx, sarx, shlx, shrx), AMD's XOP-encoded TBM (bextr imm32, blcfill, blci, blcic, blcmsk, blcs,
     blsfill, blsic, t1mskc, tzmsk) and ADX, MOVBE, CRC32 — with the VEX and XOP prefix encoders stage 4 builds
     on.
   - 1j. The system instructions: control / debug register moves, rdmsr / wrmsr, cpuid, rdtsc / rdtscp, the
     fences, prefetch*, cli / sti, in / out, the descriptor-table loads and stores, sysret / sysenter / sysexit,
     invlpg, wbinvd, xsave / xrstor and the other state-management instructions.
   - 1k. The high-byte registers AH, BH, CH, DH (no REX allowed beside them): clang takes them
     wherever an 8-bit register goes (`mov ah, al`, `setne ah`, `movzx eax, ah`); the text parser
     rejects them everywhere ("'ah' is a register, which cannot stand in an expression"), and the
     operand model cannot express one — Reg 4-7 at SZ8 means SPL..DIL, with a forced REX.  Needs an
     operand form for them (and an error where a REX is needed beside one: `mov ah, r8b`, `mov ah,
     sil`, as clang's "can't encode 'ah' in an instruction requiring EVEX/REX2/REX prefix").  Found 2026-11-12
     by the 1d review.  Also the general-purpose forms the 1j2 mnemonic sweep found missing (user, 2026-11-18:
     "your recs for the placements seem fine"): `clr reg` (clang's alias of xor reg, reg), `inb` / `inw` / `inl`
     / `outb` / `outw` / `outl` with a port number (AT&T names clang takes in Intel syntax), the `mov.s` suffix
     (the swapped encoding) and `movnti`; and the general-purpose pseudo-prefixes `{disp8}`, `{disp32}` (the
     displacement's size) and `{rex}` (a REX prefix), which no mnemonic sweep can see (user, 2026-11-18: "yes (and
     yes)").  In two commits: 1k1 the high-byte registers, 1k2 the rest.
2. **x87**: every form clang takes (memory sizes, st(i) forms, the no-wait aliases).
3. **SSE through SSE4.2**, AES, CLMUL and SHA (extend `asm/x64/sse`); MMX (`emms` and the MMX register forms)
   and 3DNow!'s `femms`, which no stage had listed; and Key Locker's XMM forms (`loadiwkey`, `aesenc128kl` ..
   `aesdec256kl`), which wait for the parser's XMM operands (user, 2026-11-18: "your recs for the placements
   seem fine").
4. **VEX: AVX, AVX2, FMA, F16C** (`asm/x64/avx`): the 2- and 3-byte VEX prefixes, the 256-bit forms, gathers;
   and AMD's XOP vector instructions and FMA4, XOP-encoded (user 2026-11-16, below); the pseudo-prefixes
   `{vex}` / `{vex2}` / `{vex3}` (user 2026-11-18).
5. **EVEX: AVX-512** (`asm/x64/avx512`): masks (k0-k7, merging / zeroing), broadcast, embedded rounding and SAE,
   the compressed disp8*N, the 32 vector registers; the `{evex}` pseudo-prefix (user 2026-11-18).
6. **APX and AMX**: r16-r31 (REX2), the EVEX-promoted general-purpose forms (new data destination, no-flags),
   push2 / pop2, ccmp / ctest, cfcmov, the `{rex2}` / `{nf}` pseudo-prefixes (user 2026-11-18); the AMX tile
   instructions.
7. **32-bit address size** (`[eax]`, `[ebx + ecx*4]`, the 0x67 prefix; user 2026-10-06: "Support it, later").

## Decisions

- Scope (user, 2026-11-11: "All, staged (Recommended)"): every instruction clang takes in 64-bit mode, in the
  order of the stages above.
- Jump relaxation (user, 2026-11-11: "Leave it to code placement (Recommended)"): not in this series; bnas gets
  clang's short jumps when plan-native-code-placement.md's step 12 lands.
- `lock` (user, 2026-11-13: "Reject non-lockable (Recommended)"): with an instruction on the same line, `lock`
  needs a lockable one (add / adc / and / or / sub / sbb / xor / inc / dec / neg / not / bts / btr / btc /
  xadd / cmpxchg / cmpxchg8b / cmpxchg16b / xchg) with a memory destination, else an error, as GNU as; clang
  emits F0 before anything (`lock add rax, rbx`, `lock mov`, `lock nop`: #UD at run time).  A `lock` alone on
  its line emits F0, as clang and GNU (the next line cannot be checked).
- `rep` (user, 2026-11-14: "As GNU as (Recommended)"): rep / repe / repz / repne / repnz with an instruction on
  the same line takes only a string instruction (movs / cmps / scas / lods / stos / ins / outs), ret, nop without
  operands (pause), bsf or bsr (tzcnt / lzcnt), else an error, as are two repeat prefixes or one with xacquire /
  xrelease (GNU: "same type of prefix used twice"); clang emits F3 / F2 before anything (`rep add eax, ebx`).
  A rep alone on its line is its byte, as in both.
- String operands (user, 2026-11-14: "Reject (Recommended)"): an explicit operand of a string instruction must be
  the [rsi] / [rdi] it reads — es: alone on [rdi], any segment on [rsi] (ds: there emitting nothing, as clang);
  another register, a displacement or the two swapped is an error, where clang emits the plain instruction
  silently and GNU with a warning.
- `@GOTPCREL` (user, 2026-11-14: "In 1g (Recommended)"): the general `[rip + x@GOTPCREL]` memory operand, in any
  instruction clang takes it in, goes into 1g beside CALL / JMP, with clang's relocation per instruction
  (R_X86_64_GOTPCRELX / REX_GOTPCRELX, plain GOTPCREL when an immediate follows; Mach-O X86_64_RELOC_GOT /
  GOT_LOAD).  A GOT reference with an addend stays an error (user, 2026-11-14: "Keep rejecting (Recommended)",
  as the AArch64 GOT-addend decision of 2026-10-14; clang takes it, the slot's address plus the addend), which
  corrects this entry's first wording ("or an addend is written").
- A symbol in an INT / RET / RETF / ENTER immediate (user, 2026-11-14: "Accept as clang (Recommended)"): `int
  offset x`, `ret offset x`, `retf offset x`, `enter offset x, offset y` take it, relocated R_X86_64_8 / 16
  (Mach-O UNSIGNED of 1 / 2 bytes), as `mov al / ax, offset x` already are — a symbol defined elsewhere as an
  absolute constant is meaningful there.  Lifts plan-tls.md 3d2's deliberate reject of a symbol as an INT vector
  or RET's stack release.
- XOP (user, 2026-11-16: "TBM in 1i, rest in 4 (Recommended)"): AMD's XOP-encoded instructions (prefix 8F), which
  clang takes and no stage had covered — TBM's general-purpose ones in 1i beside BMI, XOP's vector instructions
  and FMA4 in stage 4 beside AVX / FMA.
- Symbols in shift counts and bit offsets (user, 2026-11-16: "Accept everywhere (Recommended)"): the counts of ror /
  rol / rcl / rcr / shl / sal / shr / sar / shld / shrd and the offsets of bt / bts / btr / btc take `offset x`, as
  clang (R_X86_64_8; Mach-O UNSIGNED of 1 byte), as rorx's count and TBM bextr's control do; in the 1i commit.
  Lifts plan-tls.md 3d2's last deliberate reject of a symbol in an immediate (the INT / RET one went in 1g2).
- `xacquire` / `xrelease` (user, 2026-11-16: "As GNU as (Recommended)"): with an instruction on the line, they need
  a lock-prefixed lockable instruction (not cmpxchg16b, as GNU as) or an xchg, and `xrelease` alone also a mov to
  memory (not the moffs form, A2 / A3, which the SDM leaves out), else an error, as GNU as ("missing `lock' with
  `xacquire'", "invalid instruction `movbe' after `xacquire'"); clang emits F2 / F3 before anything, which can turn
  the instruction into another (`xacquire movbe` is crc32, `xrelease bsf` tzcnt).  In the 1i commit.  The
  question said GNU as takes only an xchg with memory; it goes by encoding (86 / 87 taken, registers too; the
  accumulator form 90+r rejected).  Asked again (user, 2026-11-16: "Memory only (Recommended)"): an xchg with a
  memory operand, the SDM's pairing.
- A lock, repeat prefix or lock-elision hint with prefixes alone after it on its line (`lock fs`, `rep fs`,
  `xacquire lock`, `xacquire fs`) is an error (user, 2026-11-16: "Reject, as GNU as (Recommended)"); clang emits the
  bytes, and 1e took `lock fs` as F0 64.  Prefixes between a hint and its instruction stay (`xacquire fs lock add
  ...`, as GNU as).  In the 1i commit.
- `push fs` / `pop fs` / gs (user, 2026-11-17: "As GNU as (Recommended)"): `push fs` and `pushq fs` are the 64-bit
  0F A0, `pushw fs` 66 0F A0, as GNU as; clang emits 66 0F A0 for `push fs` and has no spelling for the 64-bit push.
- GNU-only spellings in 1j (user, 2026-11-17: "ud0 only (Recommended)"): `ud0 r, r/m` (0F FF /r) at 16 / 32 / 64
  bits, as `int1` (clang has none); the explicit-operand `monitor rax, ecx, edx` / `mwait eax, ecx` / monitorx /
  mwaitx stay errors, as clang (the bare forms say the same), like `crc32b`.
- 1j in two commits, as 1g: 1j1 the classic system instructions (control / debug / segment register moves,
  push / pop fs / gs, msr / pmc / tsc / cpuid, the fences, prefetches, cli / sti / clac / stac, in / out, the
  descriptor-table, LDT, task and MSW instructions, lar / lsl / verr / verw, clts, sysret / sysenter / sysexit
  (into asm/x64/ctrl), swapgs, invlpg / invd / wbinvd / wbnoinvd / invpcid / invlpga, monitor / mwait (x),
  xgetbv / xsetbv, rdrand / rdseed, the fs / gs bases, rdpid, rdpkru / wrpkru, the xsave and fxsave families,
  ldmxcsr / stmxcsr, the cache-line instructions, clzero, cldemote, rsm, ud0 / ud1); 1j2 the extensions (VMX,
  SVM, TSX, CET, SGX, WAITPKG, movdiri / movdir64b, enqcmd, serialize, uintr and the rest clang takes).
- The MSR-immediate forms (`rdmsr r64, imm32`, `wrmsrns imm32, r64`, `urdmsr`, `uwrmsr`; user, 2026-11-17: "Both
  spellings (Recommended)"): the imm32 takes -2^31..2^32-1, so `rdmsr rax, 0xC0000080` (EFER) works as GNU as's
  urdmsr does; clang checks it as signed and wants `-0x3fffff80`.
- `rstorssp` / `clrssbsy` memory (user, 2026-11-17: "qword, as GNU as (Recommended)"): `qword ptr` or unsized, the
  SDM's m64, as GNU as; clang takes `dword ptr` and rejects `qword ptr`.
- x87 (stage 2; user, 2026-11-20): swapped operands on the non-commutative popping forms (`fsubp st, st(1)`,
  fsubrp / fdivp / fdivrp) are an error, as GNU as ("Reject, as GNU as (Recommended)"; clang encodes them as
  `fsubp st(1), st`, the other operation than written); faddp / fmulp take either order, as both.  The 8087 / 287
  relics fdisi / feni / fsetpm and their fn forms are rejected, as clang ("Reject, as clang (Recommended)").  A bare
  fadd / fsub / fmul / fdiv is faddp / fsubp / fmulp / fdivp st(1), st, as clang ("Take them, as clang
  (Recommended)"; GNU as rejects them).
- Stage 3 (user, 2026-11-21): asm/x64/sse grows as an instruction table — one row per encoding form (mnemonic,
  prefix, map, opcode, operand classes, imm) and one encoder matching the operands against a mnemonic's rows, the
  parser looking a mnemonic up; XMM0..15 and new MMX registers MM0..7 as operands; native's int API stays as it is
  ("Instruction table (Recommended)").  Landed in four commits ("Four commits (Recommended)"): 3a XMM operands and
  the SSE / SSE2 floating point (arithmetic, compares, conversions, moves, shuffles, the .s forms); 3b SSE2 packed
  integer and MMX; 3c SSE3, SSSE3, SSE4.1, SSE4.2; 3d AES, CLMUL, SHA, GFNI's SSE forms, Key Locker's XMM forms,
  SSE4a and 3DNow! with emms / femms.  (GFNI, SSE4a and 3DNow! were found by the mnemonic sweep: 310 names clang
  takes with XMM or MMX operands.)
- Prefixes before SSE / MMX and rex64 (user, 2026-11-22): `data16` before an instruction of the SSE table (XMM or
  MMX operands) is an error, as GNU as ("data size prefix invalid"; clang emits the 66, which makes addps addpd
  and an MMX instruction its XMM one) — in 3b ("Reject, as GNU as (Recommended)").  `rex64` follows GNU as: its
  REX.W goes after the legacy and mandatory prefixes, merged into the instruction's own REX (`rex64 mov r9d, eax`
  49 89 c1, `rex64 movd xmm0, eax` 66 48 0f 6e c0), an error where the instruction has REX.W already ("same type
  of prefix used twice"); clang emits 48 where it is written, ignored before another REX or a mandatory prefix — a
  commit of its own after 3b ("As GNU as, own commit (Recommended)").  `emms` moves from 3d into 3b, with the MMX
  set ("Into 3b (Recommended)"); femms stays with 3DNow! in 3d.
- data16 before an operand-size immediate (user, 2026-11-22: "Reject, add pushw imm (Recommended)"): an error
  where data16 narrows an instruction's immediate (MOV's B8+r and C7, the ALU group's 05 / 81, test's A9 / F7,
  imul's 69, push's 68 with an imm32), as for rex64 — bnas, clang and GNU as all emit the imm32, which the CPU
  reads as an imm16 and then the rest as another instruction (GNU as alone narrows push's).  The 16-bit forms are
  written with a 16-bit operand (`mov ax, 1`); for push, GNU as's `pushw imm` / `push word ptr imm` (66 68 iw) is
  added, which bnas lacked (clang drops the size there: `push word ptr 0x1234` 68 imm32).
- data16 before a relative branch (user, 2026-11-23: "Reject both (Recommended)"): an error before xbegin and
  before a relative jmp / jcc / call / loop-family branch without REX.W.  bnas and clang emit the 66 and a rel32:
  Intel's SDM makes 66 C7 F8 cw XBEGIN rel16, so every CPU reads a rel16 and 2 stray bytes; on a near branch Intel
  ignores the 66 in 64-bit mode and AMD honors it (a rel16 and a 16-bit target), so the length depends on the CPU
  (GNU as narrows call to rel16, broken on Intel the other way).  With REX.W the 66 has no effect: `data16 data16
  rex64 call __tls_get_addr@PLT` stands.
- Far indirect jmp / call (user, 2026-11-14: "By operand size (Recommended)"): FF /5 / FF /3 encoded as the
  operand's size defines — `dword ptr` 66 FF (m16:16, as GNU), `fword ptr` FF (m16:32, as both), `tbyte ptr`
  REX.W FF (m16:64); `ljmp` / `lcall` need a size.  Departs from clang, which drops the 66 / REX.W those
  sizes call for (and gives `ljmp [rax]` REX.W but `lcall [rax]` none).

## Progress

- 1a1 — landed binate `acdc70212` (2026-11-11): adc / sbb, the accumulator's short forms of the ALU group and
  TEST (in the shared encoder, so the native backend emits them too), xchg, the flag instructions (clc / stc /
  cmc / cld / std / lahf / sahf, pushf / popf), cbw / cwde / cdqe / cwd; the no-operand instructions in one
  table.  Push of an immediate was already there.

- 1a2 — landed binate `03e21a13c` (2026-11-11): the moffs forms of mov and movabs (A0-A3: `mov` with AL / AX /
  EAX / RAX and an address past a sign-extended 32 bits, `movabs` with any absolute memory operand); a 64-bit
  address elsewhere, or a MOV of one clang reads as a symbol (a label-difference constant's, a symbol's), is an
  error, where clang keeps its low 32 bits.  Also: a rejected x86-64 instruction leaves nothing in its section.

- 1b — landed binate `9cc1c0794` (2026-11-11): rol / ror / rcl / rcr, shld / shrd, mul and the one-operand imul,
  `imul r, imm` (as `imul r, r, imm`); a shift's count optional (1, or cl for shld / shrd), sal as shl.  Against
  clang, beside the deliberate rejects: `rol al, x` and `rol ax, cx` are APX new-data-destination forms to clang
  (stage 6).

- 1c — landed binate `19982a2a4` (2026-11-12): bt / bts / btr / btc (register or imm8 offset), bsf / bsr,
  lzcnt / tzcnt / popcnt (segment, 66, F3, REX), bswap of a 32- or 64-bit register.

- 1d — landed binate `78be67139` (2026-11-12): cmov<cc>; set<cc> (byte register or memory, clang's byte
  suffix `setneb`), movzx / movsx / movsxd in the parser (an unsized source a byte beside a 16-bit
  register, otherwise ambiguous; movsxd's a dword); the pe / po condition spellings.  Found: 1k.

- 1e — landed binate `a30d6e453` (2026-11-13): xadd, cmpxchg, cmpxchg8b / cmpxchg16b (`xmmword ptr`, SZ128);
  lock / xacquire / xrelease as prefixes written as instructions; the F0 after any 0x66, clang's order
  (x64.LockNext: `lock xadd word ptr [rbx], ax` is 66 F0 0F C1 03).  The lock check as decided above, xchg's
  memory either operand (GNU as takes `lock xchg rax, [rbx]`); another prefix between a lock and its
  instruction an error (clang's: `lock fs add ...`), one prefix alone after it that prefix's byte (`lock fs` is
  F0 64, clang's).  `lock lock add ...` / `lock lock` are rejected, as GNU as (clang: one F0) — user
  2026-11-13: "I think rejecting lock lock (etc.) is fine."  An instruction byte while LockNext's flag is set
  is an assembler error,
  so a misplaced LockNext fails rather than lock a later instruction.

- 1f — landed binate `e317cbc64` (2026-11-14): the string instructions movs / cmps / scas / lods / stos / ins /
  outs (by suffix, or with their operands written out) and rep / repe / repz / repne / repnz, in the new
  sub-package asm/x64/strop (x64.Rep_stosb / Rep_stosq, unused, folded into it); the prefixes written as
  instructions moved to parse/x64_prefix.bn.  The rep and string-operand decisions above; a rep before bsf /
  bsr comes after the operand's segment override, as clang's.  Two calls named in the landing request the
  user answered "yes": two lock-elision hints are an error (as two repeat prefixes), and a lock beside a
  repeat prefix is an error (no instruction takes both; clang keeps one byte of `rep lock` alone).

- 1g1 — landed binate `7cf998926` (2026-11-14): the control instructions in the new sub-package asm/x64/ctrl —
  enter, leave, loop / loope / loopne / jrcxz / jecxz (a rel8: the new fixup x64.FIX_REL8, ELF R_X86_64_PC8 with
  addend - 1, Mach-O a 1-byte BRANCH), retf / retfq, iret / iretd / iretq (`iret` 66 CF, as clang), hlt, pause,
  int1 (GNU's; clang takes none); far indirect jmp / call / ljmp / lcall by operand size (the decision above;
  fword / tbyte are x64.SZ48 / SZ80).  enter / retf immediates past their fields are errors (clang truncates).
  A review found ld64 drops a 1-byte BRANCH's addend (clang's own objects mislink, MAJOR); as the user decided, a
  Mach-O rel8 that would carry one is patched in place without .subsections_via_symbols, else an error
  (claude-todo-done.md).

- 1g2 — landed binate `8b1b4f48c` (2026-11-14): `[rip + x@GOTPCREL]` a memory operand to every encoder, its fixup
  as clang marks it (the 64-bit MOV load FIX_GOTPCREL; a 32- / 64-bit register loaded or operated on from a slot,
  CALL / JMP relaxable, FIX_REX_GOTPCRELX / FIX_GOTPCRELX; any other FIX_GOTPCREL_PLAIN; Mach-O X86_64_RELOC_GOT),
  but LEA plain (a review found clang's relaxable 64-bit LEA rewritten into `or reg, imm32` by GNU ld / lld, MAJOR;
  user: "Plain, as GNU as"); GOT addends still errors (decision above), in every encoder too; on Mach-O a GOT
  reference to a temporary past its atom's symbol an error (clang's form misleads ld64).  Symbols in int / ret /
  retf / enter immediates (decision above).  Left in claude-todo.md: bnld takes none of the new GOT relocations;
  an absolute symbol before `@GOTPCREL`.

- 1i — landed binate `131a40a4e` (2026-11-16): asm/x64/vex (the VEX prefix, C5 where it holds the fields, else
  C4, as clang; AMD's XOP prefix 8F) and asm/x64/gpext (BMI1 / BMI2, TBM with bextr's imm32, ADX, MOVBE, CRC32),
  their parser (parse/x64_gpext.bn) and x64.EmitRexRM.  With it, the decisions above: symbols in shift counts and
  bit offsets; xacquire / xrelease as GNU as but an xchg memory-only and no moffs mov; prefixes alone after lock /
  rep / a hint an error (1e's `lock fs` had been F0 64).  APX's EVEX forms clang picks (movbe between registers,
  adcx / adox with three operands) are stage 6's.  Left in claude-todo.md (the x86-64 coverage entry): `[rip]` /
  `[rip ± n]` with no symbol; `@PLT` / `@GOT` in a memory operand.

- x64.bni split — landed binate `0d7e38b30` (2026-11-17; user: "can you split x64.bni as a preliminary step?",
  then "Four moves (Recommended)"): x87 into asm/x64/x87 (stage 2 extends it), the prefixes written as instructions
  into asm/x64/prefix (LockNext stays in x64), int / int3 / ud2 / syscall into asm/x64/ctrl, the flag instructions
  into asm/x64/flags; x64.bni 964 -> 861 lines.

- 1j1 — landed binate `be81b6436` (2026-11-17): asm/x64/sys (the system instructions listed in the 1j decision
  above) and its parser (parse/x64_sys.bn, x64_sysreg.bn), sysret / sysenter / sysexit in asm/x64/ctrl; plus, as
  clang takes them and no stage had listed them, lfs / lgs / lss (sys) and xlat (asm/x64/strop).  The parser also
  reads what clang reads as registers: db0..db15 (lower case only, DR0..DR15; they had been symbols, so `mov db7,
  rax` stored to memory), flags / eflags / rflags, mxcsr, ssp, fpsr, fpcr, dirflag, tmmcfg, fp0..7 (errors, as
  clang); and the size names mmword (qword) and xword (tbyte).  Proposed when asking to land, and landed on the
  user's "wait 4 minutes, then go ahead" (neither answered directly): xlat's operand under the 1f string-operand
  rule (exactly [rbx], any segment emitted: `xlat byte ptr fs:[rbx]` is 64 D7, as GNU as; clang drops the
  override and ignores the address); `ymmword` / `zmmword ptr` on the any-size instructions (lgdt, xsave, ..)
  come with stage 4 / 5's size keywords (adding them changes what lea / setcc take too).

- 1j2 — landed binate `387ee9af8` (2026-11-18; user: "go ahead", then "wait 3 minutes, then land"): asm/x64/virt
  (VMX, SMX's getsec, SVM, SGX, TDX, SEV-SNP); in asm/x64/sys TSX (xbegin's abort target a new fixup kind,
  x64.FIX_REL32_PC: R_X86_64_PC32 / X86_64_RELOC_BRANCH, as clang writes it), CET, WAITPKG, movdiri / movdir64b /
  enqcmd(s), serialize, hreset, UINTR, ptwrite, lkgs, pconfig, rdpru, invlpgb, tlbsync, pbndkb, erets / eretu,
  wrmsrns, the MSR lists and the immediate-MSR forms (VEX map 7), prefetchrst2, Key Locker's forms without XMM
  operands, VIA PadLock and AMD's LWP; in asm/x64/gpext movrs, the remote atomics and cmp<cc>xadd.  A sweep of
  LLVM's mnemonic table (each name bnas did not know, tried in llvm-mc with general-register, memory and immediate
  operands) found getsec, Key Locker, PadLock, MOVRS and LWP after the first review, and the general-purpose gaps
  now under 1k.  Kept differences: the decisions above; GNU as's rejections of prefixes clang emits (lock / rep /
  the hints, repne before PadLock's own F3) and of xabort past an imm8; aes*wide*kl memory of any size (clang; GNU
  as only size 0).  Not yet taken: 32-bit addresses (umonitor r32, movdir64b / enqcmd r32: stage 7), movdir64b's
  `zmmword ptr` (stage 5's size), Key Locker's XMM forms (stage 3).  `xbegin sym@PLT` on Mach-O is a BRANCH, as
  `call sym@PLT` is (the open `@PLT` entry in claude-todo.md).

- 1k1 — landed binate `f862e3ded` (2026-11-18; user: "yes (and yes)"): ah, ch, dh and bh, as x64.AH..BH, register
  numbers of their own (20..23, encoded as 4..7 with no REX); an instruction with a REX beside one is an error, as
  clang's.  Register operands are now checked in every core encoder (regOk: RAX..R15, or AH..BH at 8 bits, before
  any byte) and every REX's numbers in emitRexF — an out-of-range number used to encode silently as another
  register; JmpReg / CallReg and sse's GP <-> XMM encoders (bare numbers) check theirs.  `rex64` before an
  instruction naming ah..dh is an error, as GNU as (clang emits it, making them spl..dil), following the prefix
  decisions above (named in the question to land, which the user answered "yes").

- 1k2 — landed binate `294093c33` (2026-11-18; user: "yes"): clr, inb / inw / inl / outb / outw / outl (the port
  alone), mov.s (x64.MovS), movnti (gpext); the pseudo-prefixes {disp8} / {disp32} (asm.Assembler.X64Disp, for
  each ModR/M memory operand with a base; x64.Operand.Disp8 the per-operand form) and {rex} (asm.Assembler.X64Rex,
  in the assembler's instruction-byte emitters: 40 before the first byte that is no legacy prefix, an instruction's
  own REX standing for it, first before prefixes alone; an error beside ah..dh and before VEX / XOP; a GOT operand
  after it takes R_X86_64_REX_GOTPCRELX), after a statement's labels or after a prefix but lock / rep.  Kept
  differences: {rex}'s 40 goes after legacy prefixes and merges into a REX of the instruction's own, as GNU as
  (clang writes REX bytes the processor ignores: `{rex} fs mov` 40 64, `rex64 {rex} mov` 48 40); {disp16} and
  GNU as's {load} / {store} are rejected, as clang (user, 2026-11-18: "Reject, as clang (Recommended)"); on Mach-O `mov eax,
  [rbx + x]` (a sign-extended disp32 against a symbol) is rejected — clang takes that one form as
  X86_64_RELOC_UNSIGNED, zero-extended, and rejects `add eax, [rbx + x]` and the indexed forms with "32-bit
  absolute addressing is not supported in 64-bit mode", as bnas does them all.

- Stage 2 (x87) — landed binate `7ac5508c0` (2026-11-20; user: "yes, go ahead; then continue"): every x87
  instruction clang takes in 64-bit mode in asm/x64/x87 (the stack registers St(i), ST0..ST7 numbered 64..71; the
  loads and stores; the arithmetic with Intel's crossed DC / DE sub / subr and div / divr; the comparisons; the
  stack, fcmov, the transcendental and constant instructions; the control word, environment, state and status
  word, the waiting forms FWAIT first) and its parser (parse/x64_x87.bn: `st` / `st(i)` with clang's index errors,
  the operands clang implies where none are written, a memory size choosing the form).  Kept differences: the x87
  decisions above; lock / rep before them (GNU as).  Found by its review and fixed before landing: a waiting
  form's segment override went on the FWAIT (`fstcw word ptr fs:[rax]` 64 9B ..; now 9B 64 .., as clang and GNU
  as).  General parser gaps the review found are in claude-todo.md (the x86-64 parser coverage entry: float /
  double / long ptr, ptr required, `offset st`).

- Stage 3a (SSE / SSE2 floating point) — landed binate `e5d6acde4` (2026-11-21; user: "wait 4 minutes, then go
  ahead"): asm/x64/sse's instruction table (sse_table.bn: lines "<mnemonic> <prefix> <map> <opcode> <operand
  classes>" in a string, in parts by family; sse_emit.bn's Has / Emit match a mnemonic's lines against x64
  operands and encode the first that fits, allocating nothing) with SSE / SSE2's floating point, 102 forms, and
  the cmp<pred>ps .. sd names with clang's second spellings (eq_oq .. ord_q); the parser reads xmm0..xmm15
  (parse/x64_sse.bn), and movsd / cmpsd are SSE's with an XMM register on the line, else the string instructions.
  Kept differences: an XMM register in a memory operand is an error, as GNU as (clang encodes the general register
  of its number: [rbx + xmm9*4] as [rbx + r9*4]); lock / rep / xacquire / xrelease before an SSE instruction are
  errors, as GNU as (clang reads rep addps as addss).  Found by its review and fixed before landing: rep before
  SSE movsd / cmpsd was taken; clang's eq_oq .. ord_q spellings were missing; an error named a predicate name's
  base (cmpsd for cmpltsd).  The MMX-operand conversions (cvtpi2ps, cvtps2pi, cvttps2pi, cvtpi2pd, cvtpd2pi,
  cvttpd2pi) go with 3b, which adds the MMX registers.

- Stage 3b (SSE2 packed integer and MMX) — landed binate `065bb57a8` (2026-11-22; user: "wait 4 minutes, then go
  ahead"): the table's second part (sse_table_int.bn, 173 forms): SSE2's packed-integer instructions with XMM and
  MMX operands, the shifts by an immediate (an opcode extension in ModR/M's reg field, written 71/6), pshufd / hw
  / lw / pshufw, pextrw (and its SSE4.1 store), pinsrw, pmovmskb, movd / movq / movdqa / movdqu with stores and
  `.s` forms, movntdq / movntq, maskmovdqu / maskmovq, movq2dq / movdq2q, the MMX conversions; emms (0F 77, moved
  from 3d); the operand classes in sse_class.bn with MMX ones (P, N, Q) and general-register ones (D, Y, J);
  mm0..mm7 (80..87) in the parser, no base or index.  data16 before an instruction of the table is an error (the
  decision above).  Found by its review and fixed before landing: emms missing; data16 taken; the table-shape
  test's gaps (r/m once, the immediate last, its checks never seen to fire).  Clang agrees on a 52,190-line sweep
  of the 85 names and 17 operand kinds, every [rip + x] / symbol form (ELF, Mach-O), and an MMX-register sweep of
  every LLVM mnemonic (but the SSSE3 MMX forms and 3DNow!, 3c / 3d).

- rex64 — landed binate `668d26212` (2026-11-22; user: "go ahead and land"): rex64 before an instruction is its
  REX.W, after the legacy and mandatory prefixes and merged into the instruction's own REX, as GNU as places it
  (asm.Assembler.X64RexW in the instruction-byte emitters; `rex64 mov r9d, eax` 49 89 c1, `rex64 movd xmm0, eax`
  66 48 0f 6e c0, `rex64 lock add` f0 48, `rex64 fstcw` 9b 48 d9 38); alone, or before prefixes alone, its 48.
  Errors, as GNU as: a REX.W of the instruction's own, two rex64 before an instruction, VEX / XOP after it; and
  (the decision above) an immediate REX.W widens (X64RexWide: MOV's B8+r one, a 16-bit one).  rex64 before ah..dh
  stays an error everywhere: GNU as rejects it beside a register or memory operand but encodes `rex64 mov dh, 5`
  as 48 b6 05, which is mov sil, 5 (so "as GNU as" in 1k1 above holds for those forms only).  Its review found two
  older MAJOR bugs, in claude-todo.md: a prefix written before an x87 waiting form goes before its FWAIT (stage
  2), and data16 before an operand-size immediate (all three tools: an imm32 the CPU reads as an imm16).

- x87 waiting forms after a prefix written as an instruction — landed binate `26a2fec4a` (2026-11-23; user:
  "actually, go ahead and land now"): the form's FWAIT goes before the prefixes (`fs fstcw word ptr [rax]` 9b 64
  d9 38, `data16 fstenv [rax]` 9b 66 d9 30), as GNU as; clang emits the prefix first, which makes it the FWAIT's.
  After the 9B the prefixes keep the order written (GNU as puts a segment's before 66).

- data16 — landed binate `dda6e4f85` (2026-11-23; user: "wait 4 minutes, then land"): data16 before an imm32 it
  narrows (MOV B8+r / C7, ALU 05 / 81, TEST A9 / F7, IMUL 69, push's 68) or before a relative branch without REX.W
  (xbegin, jmp, jcc, call, the loop family) is an error, as decided above (asm.Assembler.X64Data16;
  x64.Data16BranchOk); REX.W, a 64-bit register's or rex64's, overrides the 66.  GNU as's `pushw imm` / `push word
  ptr imm` give the 16-bit push of an immediate (66 6A ib / 66 68 iw; `push word ptr 2*3` the shorter 66 6a 06
  where GNU as has 66 68 06 00, and `pushw 0x10000` an error where GNU as truncates).  data16 alone on its line
  before an instruction on the next stays its 66, as in GNU as.

- Stage 3c (SSE3, SSSE3, SSE4.1, SSE4.2) — landed binate `c990ba4c1` (2026-11-23; user: "wait 4 minutes, then
  land"): the table's third part (sse_table_sse4.bn, 98 forms): SSE3's addsub / hadd / hsub, lddqu and the
  duplicating moves; SSSE3's with XMM and MMX operands; SSE4.1's blends (the variable ones with or without their
  xmm0 written), round, dot products, extracts and inserts, pmovsx / pmovzx, ptest, movntdqa and the integer
  operations it adds; SSE4.2's string compares and pcmpgtq.  Operand classes K (pextrd / pinsrd), L (pextrq /
  pinsrq, REX.W) and Z (the blends' xmm0, encoded nowhere).  Clang agrees on a 55,172-line sweep of the 79 names,
  on every [rip + x] / symbol form (ELF, Mach-O), and the MMX-register sweep of every LLVM mnemonic now differs
  only by 3DNow!.  Its review found no wrong encoding (test gaps fixed: the shape check of Z's place and of K32 /
  L64); two older parser gaps it noticed (notrack, a three-operand unknown mnemonic's message) are in
  claude-todo.md.

- Stage 3d (AES, CLMUL, SHA, GFNI, Key Locker, SSE4a, 3DNow!) — landed binate `66e9bd6ed` (2026-11-23; user: "wait
  4 minutes, then land"): the table's fourth part (sse_table_ext.bn, 53 forms) and femms; CLMUL's selector names
  as aliases (sse_alias.bn, beside the compare predicates'); the encoder reworked around an operand list and a
  row: 3DNow!'s opcode after the operand (written 0f.9e), two immediates and a fourth operand (Emit4, for SSE4a's
  insertq), a class A of memory of any size (Key Locker's handle, as clang).  Kept difference: a 3DNow! [rip + x]
  counts the opcode byte after its rel32, as GNU as (R_X86_64_PC32 x-5; Mach-O SIGNED_1); clang leaves it out, so
  its rel32 addresses the byte after the target.  The MMX-register sweep of every LLVM mnemonic now agrees with
  clang in full.  Stage 3 is complete.

Next: stage 4, VEX (asm/x64/avx): AVX, AVX2, FMA, F16C, the 2- and 3-byte VEX prefixes, 256-bit forms and gathers;
AMD's XOP vector instructions and FMA4; the pseudo-prefixes {vex} / {vex2} / {vex3}.
