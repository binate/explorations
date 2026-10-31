# Plan: a comprehensive, feature-gated arm32 assembler

Tracked by the claude-todo.md entry "asm arm32: an ARM feature model gating instructions as clang does, and the
forms it lacks".  User (2026-10-26): "Staged series, gating first (Recommended)".

## Where it starts

- `pkg/binate/asm/parse/arm32*.bn` (the text parser) knows ~55 mnemonics: data processing, loads / stores, block
  transfers, branches, mul / mla / umull / smull, sdiv / udiv, clz, nop, svc, bkpt.  No VFP, NEON, barriers,
  exclusives, media / saturating / bitfield, system or coprocessor instructions.
- `pkg/binate/asm/arm32` (the encoders, 112 functions) has what the native backend uses: the A32 basics and ~40 VFP
  forms.  There is no exact, range-checked encoder layer like `asm/aarch64/isa`.
- No feature model: every accepted instruction is accepted on every target.

## Stages (each landed on its own, with clang accept / reject goldens)

1. **Feature model + gating of the current set.**  (Below.)
2. **The complete A32 core**: an exact encoder layer (range-checked, fail-loud, like aarch64/isa) and its parser —
   multiplies and multiply-accumulates (incl. the DSP halfword / dual forms), saturating, parallel add / sub,
   extend / extend-add, bitfield, reverse, select / pack / usad8, ldrd / strd, the T-forms, exclusives, pld / pli,
   barriers, hints, system (mrs / msr, cps, setend, srs / rfe, smc / hvc / eret, udf), coprocessor (mcr / mrc / mcrr
   / mrrc / cdp / ldc / stc), `blx <label>`; each gated.
3. **VFPv3 / v4** (the FPU part of the model: `.fpu`, d16 / d32, fp16, vfp4, fp-armv8), in the sub-package
   asm/arm32/vfp (binate `e93b3d6fc`).
4. **NEON** (incl. the v8 crypto forms).

## Stage 2: the complete A32 core

`pkg/binate/asm/arm32` extended to the whole A32 set — its encoders are already exact (each emits exactly the one
instruction its operands name, or sets an error — nothing split, synthesized or masked into range; the parser's
mov → movw choice is the parser's), so no separate layer like `asm/aarch64/isa` is needed (user, 2026-10-27:
"Extend asm/arm32 (Recommended)"; files and `.bni` split by instruction class as they grow, VFP and NEON as
sub-packages) — and the parser taking every A32 form clang takes, each gated by the stage 1 features.  One instruction class per commit, each checked by a differential harness against clang
(operand combinations valid and invalid: registers incl. SP / PC, immediates at and past their ranges, every shift,
condition and flag form, clang's aliases and shorthands), the cases pinned as in-tree goldens.

- 2a. DONE (binate `4925d2d0c`).  Data processing: the 16 operations × immediate (modified immediate), register (shift by immediate, RRX) and
  register-shifted-register forms, S, the two-operand and Rd-less / Rn-less forms, clang's substitutions.
  User (2026-10-27): a constant's name written without `#` is an immediate wherever a number without `#` is
  ("Accept them consistently (Recommended)"), where clang takes it only where an alias or MOVW catches it.
  Deliberate differences: an immediate past 32 bits is an error (clang truncates it), and so is a float literal
  (`#4.5`, `$ 4.5`), which clang encodes as 0 in an integer instruction.
- 2a2. DONE (binate `37cfd247a`).  `:lower16:` / `:upper16:` operands for MOVW / MOVT (and MOV), which the text parser does not read yet (the
  encoders' MovwLabel / MovtLabel exist); found by the 2a review.  User (2026-10-28) on an addend outside
  [-0x8000, 0x7FFF], where clang is inconsistent (it rejects an explicit `ext+0x8000` and a fixed difference past
  16 bits, and truncates a distance it folds — a wrong high half for MOVT): "Correct halves (Recommended)" — a
  low-half relocation keeps the low 16 bits of any 32-bit addend, a high-half one must fit, a fixed difference is
  its true half.
- 2a3. DONE (binate `31c5e7c60`; data directives value by value).  A `.` label's place in the ELF symbol table: clang registers the label it makes for `.` where it reads the
  `.`, so a `.` followed by a forward reference in the same expression (`movw r0, #:lower16:(. + (2f - 1f))`,
  `.short . + (b - a)`) is listed before the item's mapping symbol; bnas lists it after (2a2's
  DefineLabelBeforeMark only covers a `.` with nothing mentioned since the reservation).  Fix: give each `.` its
  own label name and mention when the expression parser reads it, carried in the ExprResult (Dot / NegDot copies,
  negation), in the shared expression code — validated on all three architectures and Mach-O.  User (2026-10-28):
  "Separate commit next (Recommended)".
- 2b. DONE (binate `5fa9deadf`).  Multiplies and divide: mul / mla / mls, the long forms (umull, umlal, smull, smlal, umaal), the DSP halfword
  ones (smla<x><y>, smlaw<y>, smulw<y>, smlal<x><y>, smul<x><y>), the dual ones (smuad / smusd / smlad / smlsd /
  smlald / smlsld, with X), the most-significant-word ones (smmul / smmla / smmls, with R); sdiv / udiv.
  From the 2b review, rejections whose message differs from clang's (both reject): no operands ("expected
  register"; clang "too few operands for instruction"), five registers to a three-register form or a trailing comma
  ("too many operands"; clang "invalid instruction" / "unexpected token in operand"), the gates where clang lists
  alternatives (`mls` before ARMv6T2, `sdiv` on armv7-r, `smlabb pc, …` before ARMv5TE, `mulgt r3, r12, pc`), S on
  a name ending in a condition (`smmuls`, `umaals`, `sdivs`: clang reads `ls` / `vs`); and from shared code,
  `muleqs` ("unknown instruction"; clang "instruction 'muleq' can not set flags") and an immediate or a list where a
  register goes ("expected register"; clang "operand must be a register in range").
- 2c. DONE (binate `1c6638c25`).  Saturating (qadd / qsub / qdadd / qdsub, ssat / usat / ssat16 / usat16), parallel add / sub (s / q / sh / u /
  uq / uh × add16 / asx / sax / sub16 / add8 / sub8), sel, usad8 / usada8, pkhbt / pkhtb.
  User (2026-10-29) on clang's other PKHBT / PKHTB spellings (the amount without `#`, an amount with no shift
  name), where clang takes a bare PKHTB 0 as 32 and wraps 33 to 1: "Take them, same ranges (Recommended)" — the
  named form's ranges (PKHBT 0..31, PKHTB 1..32).  Message-only differences from the 2c review (both reject):
  trailing commas, a non-register where a register goes, a shift on an instruction that takes none, missing shift
  amounts or names, two PC operands, the gates before ARMv6 where clang says "invalid instruction", and clang's
  'lsr' typo for LSL's range (bnas says 'lsl').
- 2d. DONE (binate `c8501ae76`; the review's follow-up `c8d407930`).  Extend and extend-add (sxtb / sxth / sxtb16 / uxt*,
  sxtab / ... with rotation), bitfield (bfc / bfi / sbfx / ubfx), reverse (rev / rev16 / revsh / rbit), clz; their
  encoders exact (arm32_bits.bn).  clang rejects an extend's written rotation before ARMv6 even as `ror #0`, so bnas
  does.  Message-only differences (both reject): the gates where clang says "invalid instruction" (bnas names the
  architecture; for a rotation before ARMv6 clang says "operand must be a register"), `bfc r0, 8, ...`, `revs`,
  `ubfx ..., $8`.  The follow-up takes clang's two rules for its general operand (saturate widths, SBFX / UBFX,
  a pack's amount, SVC / BKPT, MOVW / MOVT, shift-instruction amounts, branch / ADR targets): no `-`, `+` or `~`
  at the start without `#` / `$`, and a negative zero (`#-0`, `#-1+1`) out of range, ADR's SUB form; and makes
  SVC / BKPT refuse a label difference.
- 2e. Loads and stores, in two parts.  Part 1 DONE (binate `fc4a352de`): ldr / str / ldrb / strb, ldrh / strh /
  ldrsb / ldrsh and the T forms, exact (arm32_mem.bn, arm32_memh.bn) and parsed by parseArm32LoadStore, ~4,100
  cases against clang; the negative zero (U clear), unprefixed offsets, `#lbl` targets, the writeback-clash
  messages, and the 2a review's post-indexed label difference (now refused) are in.  Kept differences (user
  2026-10-30, "Land both now" over "Change `[r1, N]` first"): `ldr r0, [r1, N]`, N a constant, is the offset N
  (clang: "register expected"); an offset is the number written (clang cuts it to 32 bits: a halfword's post-index
  takes `#0xFFFFFFFF` as -1, and `#-4294967296` is a negative zero); a `#` target on a conditional halfword load
  (`ldrheq r0, #lbl`, which clang refuses, bnas takes); a label difference as a target (`ldr r0, lbl-.`, an
  absolute address in clang, refused as for a branch); clang crashes on `ldrh r0, [pc]`, which bnas encodes.  The
  review also found bnas silently dropping `.` from a data-processing operand (`mov r0, (.+8)` was `mov r0, #8`):
  fixed, binate `b5000e152`.
  Part 2 DONE (binate `ef8444c43`): ldrd / strd (Rt even, below r14, Rt2 = Rt + 1, `r12, sp`; the one-register
  form before a memory operand only, r12 there from ARMv8, no register post-index; clang's writeback-clash
  messages) and pld / pldw / pli (unconditional, offset forms, PC-relative), ~900 cases and whole objects against
  clang.  clang's gates kept: ldrd's offset and PC-relative forms from ARMv5TE but its writeback forms on any
  architecture, pli from ARMv7, pldw with the MP extension.  It took arm32.bni past its 1000-line cap, so the VFP
  encoders moved to the sub-package asm/arm32/vfp first (binate `e93b3d6fc`, user 2026-10-30: "Peel VFP now
  (Recommended)"); arm32.bni is ~840 lines, so 2f..2j will likely need another class peeled (system /
  coprocessor).
- 2f. Exclusives and ordering: ldrex / strex (b, h, d), clrex, swp / swpb, the v8 acquire / release forms (lda / stl
  / ldaex / stlex families).
- 2g. Block transfer: ldm / stm ia / ib / da / db, writeback, `^`, push / pop forms.
- 2h. Branches: b, bl, blx <label>, blx <reg>, bx, bxj.  From the 2a review: `adr r0, <label>` with a label named
  like a register (`r1`, `sp`, `v1`) is taken by bnas; clang: "invalid operand for instruction" (`b r1` to such a
  label is fine in both).  From the 2d review: a branch to a number past +/-32 MiB (`b #0x2000000`) clang wraps
  into the offset field (EA800000); bnas rejects it, deliberately.
- 2i. System and hints: svc, bkpt, udf, hvc, smc, eret, mrs / msr (APSR / CPSR / SPSR fields, banked registers),
  cps, setend, srs / rfe, nop / yield / wfe / wfi / sev / sevl / dbg / csdb / esb, dmb / dsb / isb with options,
  sb, ssbb / pssbb, setpan, hlt.
- 2j. Coprocessor: mcr / mrc (and 2), mcrr / mrrc (and 2), cdp (and 2), ldc / stc (and 2, L).
- Exact encoders: DONE for every encoder.  The steps through 2e made their classes' encoders check their condition
  and registers; binate `d90f4ce8c` (temp-5, a separate todo) did the rest -- arm32_branch.bn, arm32_ldm.bn,
  arm32_sys.bn, arm32_vfp.bn -- and `condBits` no longer masks.  Later steps keep each new encoder so.
- Outside the steps, found by the 2d reviews (bnas and clang differ; none is yet assigned):
  - A quoted register (`add "r0", r1, r2`, `clz "r0", r1`) is taken by bnas -- the Rd parse does not check that the
    token is a name; clang: "operand must be a register".
  - `.arm`, `.code 16` / `.code 32`, `.thumb` and `.syntax` are unknown directives in bnas (clang takes `.arm`,
    `.code 32` and `.syntax unified` in ARM code; `.thumb` / `.code 16` switch to Thumb, which bnas has none of).
  - A `$` right before a character literal (`svc $'a'`): clang reads the symbol `$'a'`, bnas `$` and then 0x61.
  - The expression parser: no `!` (logical not; clang `svc #!0` is 1), no quoted symbol names (`bl "lbl"`), and
    clang reads `'\0'` as 0x30, where bnas rejects the escape.

## Stage 1: the feature model — DONE (binate `09f41e4f7`)

### clang's facts (Apple clang 21, `--target=arm-linux-gnueabihf -march=armv7ve`; probes in the session scratchpad)

- `.arch <name>` (A32 names): armv4, armv4t, armv5 (= 5T), armv5t, armv5te, armv6 / armv6j, armv6k, armv6kz /
  armv6zk, armv6t2, armv7 / armv7-a / armv7a, armv7-r / armv7r, armv8-a / armv8a, armv8.1-a .. armv8.9-a, armv9-a ..
  armv9.6-a, iwmmxt, xscale; also without `arm` (`v7`, `7-a`); lower case only ("Unknown arch name").  It sets the
  features to the architecture's (dropping extensions such as idiv: `.arch armv7-a` then `sdiv` is "instruction
  requires: divide in ARM") and Tag_CPU_name to its short name ("4T", "5T", "5TE", "6", "6K", "6KZ", "6T2", "7-A",
  "7-R", "8-A", "8.1-A" .., "9-A" .., "iwmmxt", "xscale"); the other attributes stay the target's.  M-profile names
  (armv6-m, armv6s-m, armv7-m, armv7e-m, armv8-m.base / .main, armv8.1-m.main): clang switches to Thumb — bnas: an
  error, as for a Thumb-only `.cpu`.  armv5tej, armv7ve, armv7s, armv7k, armv8-r: clang fails ("error in backend:
  Unknown Arch"); GNU as accepts armv7ve, armv5tej, armv8-r (Tag_CPU_name "7VE", "5TEJ", "8-R") and rejects armv7s /
  armv7k.  User (2026-10-26): "Accept 3 as GNU as (Recommended)" — bnas follows GNU as for those five.
- Messages: where clang says "invalid instruction, any one of the following would fix this:" its notes name the
  ARM requirement and a Thumb one (`bx` on armv4: "armv4t" / "thumb"); bnas, with no Thumb, reports the ARM one as
  "instruction requires: <feature>", clang's feature names (armv4t, armv5t, armv6t2, "divide in ARM", ..).
- `.cpu <name>`: the CPU's features (arch level + extensions + FPU), for the instructions after it; Tag_CPU_name.
  `generic` behaves as armv4 (no `bx`).
- `.arch_extension [no]<ext>`: on armv7ve `idiv`, `mp`, `sec`, `virt` (each with `no`); `crc`, `crypto`, `sha2`,
  `aes`, `fp`, `simd`, `ras`, `fp16`, `mve.fp`, `pacbti` are "architectural extension 'X' is not allowed for the
  current base architecture" (v8 ones; to probe on armv8-a); `os`, `iwmmxt`, `iwmmxt2`, `maverick`, `xscale`
  "unsupported architectural extension"; others "unknown architectural extension".  No attribute effect.
- Gating of the current set by architecture: `bx` v4T; `blx <reg>`, `clz` v5T; `movw` / `movt` v6T2; `sdiv` /
  `udiv` the hwdiv-arm extension (armv7ve's, not armv7-a's / armv7-r's); `nop` is `mov r0, r0` (e1a00000) before
  v6K and the hint (e320f000) from v6K; the rest (`ldrh` .., `mul` / `mla` / `umull`, `push` / `pop`, `svc`, `adr`,
  `bkpt`) on every A32 architecture (armv4 up).  Messages: "instruction requires: armv5t", "instruction requires:
  armv6t2", "instruction requires: divide in ARM", and "invalid instruction, any one of the following would fix
  this:" (`bx` on v4, `clz` on v4T, `sdiv` on v7-R).
- CPU features (one representative instruction per feature, all 80 A32 CPUs): the arch level plus per-CPU
  extensions that differ even within a level — hwdiv-arm (cortex-a7 / a12 / a15 / a17 have it, a5 / a8 / a9 do
  not), mp (pldw), sec (smc), virt (hvc), dsp, crc, ras (esb), sb — and a default FPU (none, vfpv2, vfpv3-d16,
  vfpv3, vfpv4, fp-armv8, with or without NEON; crypto on some v8 CPUs).  The table is generated from clang probes,
  not written by hand.  `ldrex` is accepted on every CPU (even v4).

### Design

- A feature set (a bit set) in the parser: the architecture levels as cumulative bits (V4T, V5T, V5TE, V6, V6K,
  V6T2, V7, V8, V8.1 ..), the profile, the extensions (idiv-arm, mp, sec, virt, crc, ras, sb, acquire-release, ..)
  and the FPU features (filled by stage 3).  Each instruction names the features it needs; the parser checks them
  and reports clang's message.
- Tables: architecture name → features + Tag_CPU_name; CPU name → features (+ default FPU); extension name → bits
  (+ which base architectures allow it).
- The target sets the starting features: armv7ve (idiv, mp, sec, virt) for both bnas arm32 targets.
- `.arch`, `.cpu` replace the feature set (and set Tag_CPU_name); `.arch_extension` adds / removes bits.
- `nop` encodes by feature (mov r0, r0 before v6K).
- Tests: per-architecture and per-CPU accept / reject / encoding goldens from clang for the current set.
- User (2026-10-26): bnas's instruction-set names in `.arch` (aarch64 / arm64, arm32 / arm, x64 / x86_64) keep their
  meaning in arm32 code — "Keep bnas's ISA meaning (Recommended)" — where clang reads aarch64 / arm64 as ARMv8-A; an
  `.arch` name starting with a decimal number (`8.1-a`), which clang's lexer turns into armv7-a, is read as written
  — "Keep bnas's reading (Recommended)".

### For stages 3–4 (from the stage 1 review, each probed against clang)

- `.arch` and `.cpu` also reset the FP / NEON features: `.arch armv6` then `vadd.f32 s0, s1, s2` is "requires VFP2";
  `.arch armv7-a` turns NEON on; `.cpu cortex-a8` has NEON but not VFPv4, `.cpu arm1176jzf-s` VFPv2 but no NEON;
  `.fpu vfpv2` then `.arch armv7-a` ends with NEON allowed.  So the architecture and CPU tables need their FP
  features (the attributes, by contrast, keep the FPU's: `.arch` changes only Tag_CPU_name, and Tag_WMMX_arch for
  iwmmxt).
- The starting FP features differ by target (Linux: VFPv3 + NEON; bare metal: none), but NewParser sets the
  features before the OS is known: set the FP part where the target is (assemble.armTargetAttrs, bnc).
- clang applies extensions through LLVM's implication graph, setting and clearing: `.arch armv8-a` then
  `.arch_extension noaes` makes `vadd.f32 d0, ..` "requires NEON" and `sha1h` fail; `nofp` / `nosimd` drop both VFP2
  and NEON.  Stage 1 adds / drops flat bits (idiv / virt the one implication); stages 3–4 need the closure, `.fpu`
  through it too.
- `bxj` is accepted from armv5t to armv8-a, so there is no V5TEJ level (removed); gate it as clang does.
