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
- 2f. DONE (binate `ae3d7ce99`).  Exclusives and ordering: ldrex / strex (b, h, d), ldaex / stlex (b, h, d), lda /
  stl (b, h), swp / swpb, clrex -- exact (arm32_excl.bn: Ldrex / Strex / Lda / Stl / Swp / Clrex with EX_* sizes),
  ~5,400 cases against clang.  clang's gates: acquire / release from ARMv8, swp only before it, clrex from ARMv6K,
  none on the other exclusives.  Kept difference: before ARMv8 clang takes a second memory operand as a
  doubleword's Rt2 (`ldrexd r12, [sp], [r0]`, a clang bug); bnas refuses it.
- 2g. DONE (binate `f34e5cf63`).  Block transfer: ldm / stm ia / ib / da / db, the fd / ea aliases (clang takes no
  fa / ed), writeback, `^`, push / pop.  bnas already matched clang over ~970 cases but for `push {sp}`, refused
  since 2e's STR writeback clash; Push now encodes its one-register STR itself.  clang's warnings (unsorted or
  repeated registers, PC in the list) are not copied; the words match.
- 2h. DONE (binate `e7ea2d33a`, with bnld `600b9da73`).  Branches: b, bl, blx <label> (BlxLabel / BlxOffset, the H bit),
  blx <reg>, bx, bxj (ungated, as clang takes it).  Following clang (user: "Follow clang (Recommended)"), a BL /
  BL<cond> / BLX to any symbol stays a relocation, `.L` labels listed, `.±N` a label made at `.`; `(PLT)` is part
  of the expression primary on every PC-relative target and keeps the relocation; a branch number is read as 32
  bits; a name clang reads as a register (arm32RegisterName) is no ADR / literal-load / preload / BLX-label
  operand.  bnld picks BL or BLX for R_ARM_CALL by the target function's instruction set, gated on an input's
  Tag_CPU_arch >= ARMv5T, as lld.  Kept differences: `blx<cond> label`, a relocated BLX whose addend is not a
  multiple of 4, a branch number past 32 bits and one past +/-32 MiB (`b #0x2000000`, clang wraps it) are errors.
- 2i. DONE (binate `9473afec6`, after the move of NOP / SVC / BKPT into the sub-package asm/arm32/sys, binate
  `743ec8d07`; user: "Peel all to asm/arm32/sys (Recommended)").  System and hints: svc / swi, bkpt, udf, hvc,
  hlt, smc / smi, hint, dbg, setpan, the named hints (also clrbhb, tsb csync, trap), eret, sb, ssbb / pssbb, dfb,
  dmb / dsb / isb with options, mrs / msr (field masks, banked registers, immediates), cps / cpsie / cpsid, srs /
  rfe, setend -- ~2,100 cases and the gates over ~800 contexts against clang; a new feature bit armFDB (ARMv8-R
  and the Cortex-R52s).  splitMnem now keeps clang's never-split list (`sb` broke `sbcs` in review).  Kept
  differences: bare cpsie / cpsid (clang: CPS #2 / #3) are errors; SETEND's ARMv8 deprecation warning is not
  copied.
- 2j. DONE (binate `9170c4d75`; user: "asm/arm32/sys (Recommended)").  Coprocessor: mcr / mrc (and 2), mcrr /
  mrrc (and 2), cdp (and 2), ldc / stc (and 2, L), the encoders in asm/arm32/sys; a label is a new fixup,
  FIX_LDC_PC, resolved in place only (clang has no relocation for it); `pr15` / `cr1` as clang reads them --
  ~2,300 cases and the gates from ARMv4 to ARMv9-A against clang.  Kept differences: a label or `.±N` not a
  multiple of 4 away is an error (clang encodes a wrong offset); the cp10 / cp11 warning is not copied.
  Message-only differences (both reject): `mrcs` / `ldcs` / `stcs` / `mrrcs` ("can not set flags"; clang reads `cs`
  as a condition, "invalid instruction" -- the shared splitMnem order, as 2b's `smmuls`); MCRR / MRRC with PC (the
  range; clang says "invalid instruction" and lists it).
- Exact encoders: DONE for every encoder.  The steps through 2e made their classes' encoders check their condition
  and registers; binate `d90f4ce8c` (temp-5, a separate todo) did the rest -- arm32_branch.bn, arm32_ldm.bn,
  arm32_sys.bn, arm32_vfp.bn -- and `condBits` no longer masks.  Later steps keep each new encoder so.
- Outside the steps, found by the 2d and 2f reviews (bnas and clang differ; none is yet assigned):
  - No `.req` directive (`foo .req r1`, a register alias), which clang takes for every register kind.
  - A quoted register (`add "r0", r1, r2`, `clz "r0", r1`) is taken by bnas -- the Rd parse does not check that the
    token is a name; clang: "operand must be a register".  (BX / BXJ / BLX check it.)
  - `.arm`, `.code 16` / `.code 32`, `.thumb` and `.syntax` are unknown directives in bnas (clang takes `.arm`,
    `.code 32` and `.syntax unified` in ARM code; `.thumb` / `.code 16` switch to Thumb, which bnas has none of).
  - A `$` right before a character literal (`svc $'a'`): clang reads the symbol `$'a'`, bnas `$` and then 0x61.
  - The expression parser: no `!` (logical not; clang `svc #!0` is 1), no quoted symbol names (`bl "lbl"`), and
    clang reads `'\0'` as 0x30, where bnas rejects the escape.
- Outside the steps, found by the 2h review:
  - A numeric local label's `.Ltmp<n>` name: clang numbers every instance in the order it makes them, bnas only
    the instances listed (elf_symtab.bn), so `1: nop` / `2: nop` / `bl 2b` lists `.Ltmp1` in clang, `.Ltmp0` in
    bnas.  Since 2h every `bl` / `blx` to a numeric label lists it (before, only a cross-section reference did).
  - `C = 4` then `b C(PLT)`: clang relocates against the absolute symbol C (R_ARM_JUMP24); bnas takes `(PLT)` only
    after a label or an alias, so it is an error there.
  - A forward label difference in an addend (`bl lbl+(l2-l1)` before `l1`, `l2`, `lbl`): clang lists the locals
    `lbl l2 l1`, bnas `l2 l1 lbl` (the first pass, pending, does not record `lbl`); every reference form has it.
- Outside the steps, found in step 3a:
  - clang reads a floating-point literal in any expression as its IEEE binary64 bit pattern (`mov r0, #0.0` is
    E3A00000; `.long` and every architecture's immediates the same); bnas's expression parser takes one only as
    VCMP's zero (Parser.ExprZeroReals, a zero literal as 0).  Needs an exact decimal-to-binary64 conversion.
    A NEON lane the same (found in step 4b): clang keeps the pattern's low 32 bits, so `vmul.i16 d0, d1, d2[1.0]`
    is lane 0, where bnas takes a floating-point lane as none (the matcher keeps an integer lane's low 32 bits).

- Outside the steps, found in step 3b:
  - AArch64 FMOV's immediate: LLVM rounds the literal to IEEE binary64 first (every size), then wants an exact
    8-bit encoding -- `fmov d0, #1.0000000000000001`, `fmov s0, #1.00000000000000001` and `fmov h0,
    #1.0000000000000001` assemble -- while bnas wants the literal's exact decimal value ("immediate not
    representable"); `fmov s0, #1.00000001` both reject.  arm32's float_round.bn (exact decimal bounds around
    each 8-bit candidate) does the same at single precision; binary64's half-ulp bounds would serve here.

- Outside the steps, found in step 3c:
  - 3b's VMOV immediate with a type clang does not read as floating point (`vmov.d d0, #1.0`, `vmov.f64 d0, #0`):
    clang's message comes from its matcher (the register copy's "operand must be a register in range [d0, d15]",
    or several) where bnas says "invalid operand for instruction" / "invalid floating point immediate"; both
    reject.

- Outside the steps, found in step 3d:
  - An `s` after an unknown name: clang reports its flag-setting error for any mnemonic ending in `s` it cannot
    split otherwise (`foos r0` is "instruction 'foo' can not set flags, but 's' suffix specified", `ldrss`,
    `vldrss`, `vfoos` the same), where bnas says "unknown instruction" or "expected register".  And clang reads
    an unknown name's operands before it reports the name, so their errors come first (`fldmiad r0!!, {d0}` is
    "unexpected token in argument list", `vldmfd r0, {d0` "'}' expected").
  - A PC-relative label fixup out of reach or misaligned (B, ADR, LDR, LDC, VLDR): bnas's one message ("the
    reference to 'x' is out of range or misaligned for its instruction"), where clang's is per fixup ("out of
    range pc-relative fixup value", "invalid value for this fixup" for VLDR.16, ...).
  - A quoted symbol name in a VLDR / LDR / LDC target (`vldr s0, "lbl"`) is "invalid load / store target"; the
    expression parser's quoted names (above) would let it through.
  - A floating-point literal other than zero ends the expression parse where it stands (ExprZeroReals), so a
    syntax error after it (`[r0, #1.5 + ]`, `lsl #(1.5]`) is not clang's message, and arithmetic on it
    (`#1.5 - 1.5`, a number to clang) is refused -- part of the binary64 conversion above.

- Outside the steps, found in step 4a (part 3's review; older than it, and as much for core names):
  - A name nothing takes, with no `s`: clang's "invalid instruction" once its operands are read, where bnas says
    "expected register": `vdivs s0, s1, s2` (VDI with VS), `vnmuls` (VNMU with LS), `vcmpeqe`, `vnegq s0, s1` (no
    q alias), `fnmuls`, `fsubeq`; with the `s` item of step 3d (`fabss`, `fcmpes`, `vqabss` are clang's
    flag-setting error), one fallback for a name nothing takes.
  - The expression parser where an operand is read: an error in an expression clang reads is a near miss of the
    instruction to clang (`vcmp.f32 s0, #0*lbl`, `#und-und`: "invalid instruction, any one of the following would
    fix this:"), the expression's own message here; clang reads `#[0]` as 0 (`vcmp.f32 s0, #[0]`, `mov r0,
    #[0]`), and a floating-point literal that rounds to zero (`#1e-400`, `#2e-324`) as 0.
  - The expression parser has no comparison operators (clang's true is -1: `.long 3 > 2` is 0xffffffff; a lane
    `d2[3 > 2]` "invalid operand for instruction", here "unexpected character '>'") and reads `[e]` as no grouping
    (`vmul.i16 d0, d1, d2[[0]]` and `svc #[1]` assemble in clang).  In a NEON lane clang's messages differ for
    `d2[1 ? 2 : 3]` ("']' expected"), `d2[3 / 0]` and `d2[undef-undef]` ("immediate value expected for vector
    index"), `d2[0x]` ("invalid hexadecimal number").  Found by step 4b part 2's review.
  - An operand the matcher reads (arm32_match_ops.bn) does not report clang's "directional label undefined" for
    a backward local label with no definition before it (`vshr.s8 d0, d1, #1b`, `vcvt.f32.s32 s0, s0, #1b`,
    `vldr d0, [r0, #1b]`): it is no constant there, so the range's or the operand's message instead (a bare
    `vldr d0, 1b` is right).  And an undefined symbol minus itself (`#und-und`) is "a difference of locations in
    different sections or atoms is not a number" here, clang's operand message ("operand must be an immediate in
    the range [1,8]").  Found by step 4c part 1's review.
  - An integer literal past 64 bits is "integer literal does not fit 64 bits" here, clang's "literal value out of
    range for directive" (in an instruction's operand as in a directive: `mov r0, #0x10000000000000000`).  Found
    by step 4c part 2's review.

## Stage 3: VFP (asm/arm32/vfp)

The FPU half of the feature model and every VFP instruction clang takes, a class per step, each landed with clang
accept / reject goldens and gates and its encoders exact (asm/arm32/vfp's existing ones, which the native backend
uses, kept or rebuilt on the general ones).  Each class takes its pre-UAL names too (fadds, fldd, fmrx, ..) where
clang does.

clang's facts so far (Apple clang 21; bnas's targets as bnc drives clang: Linux `--target=arm-linux-gnueabihf
-march=armv7ve`, bare metal `--target=arm-none-eabi -march=armv7ve -mfloat-abi=soft`):
- Linux starts with VFPv3, 32 D registers and NEON (no VFPv4, no half conversions); bare metal with no FP at all
  (`vadd.f32` is "instruction requires: VFP2").
- `.fpu` sets the FP features: vfpv2 (16 D registers -- `d16` is "operand must be a register in range [d0, d15]" --
  and no `vmov` immediate), vfpv3, vfpv3-d16, vfpv4 (vfma, half conversions), neon (VFPv3 + NEON), fp-armv8
  (vrint*, vcvtb to f64), crypto-neon-fp-armv8 (+ AES / SHA2 under ARMv8), softvfp / none (no FP).
- `.arch` and `.cpu` set them too, on either target: armv6 none, armv7-a VFPv3 + NEON, armv8-a
  crypto-neon-fp-armv8; cortex-a8 VFPv3 + NEON, cortex-a9 the same with half conversions, arm1176jzf-s VFPv2,
  cortex-a53 crypto-neon-fp-armv8.
- `.f16` arithmetic needs full FP16 (ARMv8.2's fp16), on by no default; vjcvt needs ARMv8.3.
- Messages: "instruction requires: VFP2" / "double precision VFP" / "VFP4" / "half-float conversions" / "FPARMv8"
  / "NEON" / "full half-float" / "FPARMv8 armv8.3a"; a NEON instruction without NEON is "invalid instruction, any
  one of the following would fix this".

Steps:
- 3a. The FP feature model and the arithmetic class.  Part 1, the model, DONE (binate `e22a55b98`): LLVM's 29 FP
  features with their implications in Parser.Arm32FP, set by the target (Linux VFPv3 + NEON, bare metal none, not
  even the FP registers), `.fpu`, `.arch` (armv7ve as armv7-a, armv8-r ARMv8's FP SP D16 -- clang crashes only
  writing an object whose last `.arch` is one of them), `.cpu` and `.arch_extension`; ~220 clang goldens, ~12,000
  sequences in review.  Part 2, the arithmetic class, DONE (binate `b398ec32a`): encoders Arith3 / Arith2 / CmpZero
  (the backend's VaddF32 .. VcmpeF64 on them), the parser with clang's pre-UAL rules, `.w` / `.n`, VSQRT with no
  type, VCMP's #0, the gates and clang's matcher messages (near misses); the lexer's "invalid sign in float
  literal".  ~7,400 cases and the gates in ten contexts against clang.  Kept differences: a nonzero floating-point
  literal in VCMP's #0 is an error (clang folds its bit pattern: `#(1.0 - 1.0)`, `#1e-400` are zero to it); some
  multi-candidate messages give the note that applies.  NEON forms (`vadd.f32 d0, ..`) wait for stage 4.
  The plan as written: FP bits (VFP2, VFP3, VFP4, FP-ARMv8, double precision, 32 D
  registers, half conversions, full FP16, NEON, AES, SHA2), set by the target, `.fpu`, `.arch`, `.cpu` (each CPU's
  default FPU) and `.arch_extension` through clang's implication closure; then vadd / vsub / vmul / vnmul / vdiv,
  vmla / vmls / vnmla / vnmls, vfma / vfms / vfnma / vfnms, vabs / vneg / vsqrt, vcmp / vcmpe (and `#0`), in .f32 /
  .f64 / .f16.
- 3b. Moves, DONE (binate `e94c5234e`): encoders VmovReg, VmovImm, VmovCoresToSingles / VmovSinglesToCores,
  VmovCoreToLane / VmovLaneToCore, Vmrs / Vmsr with the system registers, VmovHalfToCore / VmovCoreToHalf, Vins,
  Vmovx (the backend's VmovF32 / VmovF64 / VmrsApsrNzcv on them); the parser with clang's pre-UAL names (fcpys /
  fcpyd, fmrs / fmsr / fmdrr / fmrrd / fmdhr / fmdlr, fconsts / fconstd taking the 8-bit encoding, fmrx / fmxr /
  fmstat), a floating-point immediate rounded to single precision as clang rounds it (float_round.bn, exact
  decimal bounds), a lane as a constant expression, the gates (the FP registers; VFPv2 for the typed and pair
  forms, lane writes and the pre-UAL names; VFPv3 for immediates; ARMv8's FP for MVFR2; FP16 for .F16 / VINS /
  VMOVX) and NEON's forms as "instruction requires: NEON".  ~5,800 cases against clang.  Kept differences: some
  multi-candidate messages give the note that applies; `#1.0+0` gets the lexer's "invalid sign in float literal"
  (clang: "invalid floating point immediate"); a difference of undefined labels as a lane gets the expression
  parser's message.  NEON's forms (`vmov.i32 d0, #1`, `vmov d0, d1`, Q registers) wait for stage 4.
- 3c. Conversions and ARMv8's FP, DONE (binate `470159517`; with it `7d6efc934`, clang's `.d` and q aliases on the
  3a / 3b instructions): encoders VcvtFP, VcvtFromInt, VcvtToInt, VcvtFixed, VcvtHalf, VcvtBF16, VcvtRound, Vjcvt,
  Vrint, Vsel, Vmaxnm (the backend's VcvtF64F32 .. VcvtU32F64 on them).  The parser (arm32_cvt*.bn) models clang's
  matcher: the mnemonic split (vcvtal is VCVT with AL, vcvtaal VCVTA), the pre-UAL names whole only, then a table of
  each name's VFP, NEON and MVE encodings matched with clang's near-miss rules and FilterNearMisses' de-duplication
  (a non-AL condition is an operand, out of step with an encoding that has no condition operand), so the messages
  are clang's.  clang's facts: fixed-point VCVT is gated on VFPv2 (VFPI's default), not VFPv3; LLVM's FBits16 /
  FBits32 classes are 0..17 and 1..33, so clang takes #17 / #33 and wraps them; `.f` / `.d` are token aliases of
  `.f32` / `.f64` everywhere; MVE's encodings are offered in ARM code too ("instruction requires: mve.fp").  ~10,700
  cases against clang.  Kept differences: #17 / #33 are errors; no "did you mean" suggestions; a non-zero
  floating-point literal in the fraction bits is an error; a name clang lacks is "invalid instruction" before its
  operands are read; a few expression errors keep the expression parser's wording; a vmovq with a condition, `.w`
  or .f16 before a floating-point immediate (clang picks its FP-immediate parser by the name as written, so it reads
  an integer there).  NEON's forms wait for stage 4.
- 3d. Loads and stores.  Part 1, VLDR / VSTR, DONE (binate `41e48f8b0`; with it `bf748398c`, the conversion matcher
  moved into arm32_match*.bn for every VFP instruction): encoders Vldr / Vstr (.16 / .32 / .64, the backend's Vldr32
  .. Vstr64 on them), FIX_LDC16_PC (VLDR.16's PC-relative halfword offset); the parser (arm32_vldr.bn) on the
  matcher, which now reads a memory operand as clang's parseMemory does (offset, index with a shift, alignment, and
  their messages), system registers and `!`.  clang's facts: plain VLDR needs only the FP registers, .32 / .64 (and
  their token aliases) and the pre-UAL flds / fldd / fsts / fstd VFPv2, .16 the 16-bit FP registers (and is
  unpredicable); .f16 and .8 are no VLDR types; `#-0` clears U; `#lbl` is a label; VLDR.16 takes a label and `.±N`
  (fixup_arm_pcrel_9: an odd offset is "invalid value for this fixup", checked before the range); the M-profile
  forms (fpscr, fpscr_nzcvqc, fpcxtns / fpcxts, vpr, p0) are near misses in ARM code, their address any base but PC,
  or a label; an `s` before or after the condition is the flag-setting error, naming what is left after clang splits
  the condition off the end and then the `s`; a floating-point literal is a number (its bit pattern), not a label.
  ~7,200 cases against clang.  Kept differences: a label or `.±N` no multiple of 4 away (.32 / .64) is an error
  (clang encodes another address); the "unsupported relocation type" error names the coprocessor or VFP load /
  store, as LDC's does; a constant's name as an offset without `#` is the offset, as in LDR (2c's); an offset whose
  low 32 bits clang reads as `#-0` (`#-2147483648`) is out of range, as in LDR.  Part 2, VLDM / VSTM, VPUSH / VPOP
  and FLDMX / FSTMX, DONE (binate `d328c3eca`): encoders Vldm / Vstm, Fldmx / Fstmx, Vpush / Vpop; a register-list
  reader on clang's parseRegisterList (arm32_match_list.bn); the parser (arm32_vldm.bn), M-profile's VLLDM / VLSTM /
  VSCCLRM as near misses.  clang's facts: VLDM, VPUSH and FLDMX need only the FP registers, the FLDMX aliases
  fldmfdx / fldmeax / fstmfdx / fstmeax VFPv2 (checked after the operands); clang has no fldmias-style pre-UAL
  names, only the X forms; VLDM / VSTM ignore every data-type suffix clang has (isDataTypeToken), VPUSH / VPOP take
  .8 .. .64 through their aliases; only VLDMIA / VSTMIA without write-back refuse a D list past 16 ("list of
  registers must be at least 1 and at most 16"), the write-back forms, VPUSH and FLDMX encoding one; in a list
  D16..D31 need 32 D registers but Q8..Q15 do not; a `^` after a list is an operand.  ~8,100 cases against clang.
  Kept differences: a range ending in another class is "invalid register in register list" (clang walks its register
  numbering: it hangs on `{d0-s1}` and `{s0-vpr}`, or ends at D31); clang's FLDMX / FSTMX leave the D bit clear
  (`fldmiax r0, {d31}` is its word for D15), where bnas sets it as GNU as does (user 2026-11-03, "yes" to landing it
  so); clang's warnings are not copied.

## Stage 4: NEON (asm/arm32/neon)

Every Advanced SIMD instruction clang takes in ARM code, a class per step (the ARM ARM's encoding groups), each
landed with clang accept / reject goldens and gates: the encoders exact, in a new sub-package asm/arm32/neon
(importing arm32, never the reverse; the VFP encoders stay in asm/arm32/vfp; CLAUDE.md's list of BUILDER-compiled
packages gains it), the parser on the near-miss matcher (arm32_match*.bn), whose NEON encodings today only shape VFP
messages and report a NEON match as "not supported yet".  The VFP mnemonics' NEON forms (`vadd.f32 d0, d1, d2`,
NEON's VCVT and VRINT*) come with their class.

Decided: a condition on an unconditional NEON instruction is an error, clang's own wording for an unpredicable one,
"instruction 'vadd' is not predicable, but condition code specified" (user 2026-11-03, "Reject (Recommended)");
clang drops the condition silently (`vaddeq.i32 d0, d1, d2` is F2210802, as `vadd.i32`; `vld1eq.8`, `vmoveq.i32 d0,
#1`, `vcvteq.f32.s32 d0, d1` the same), GNU as rejects it ("instruction cannot be conditional").  NEON's conditional
forms (VDUP from a core register, VMOV between a core register and a scalar) keep theirs (`vdupeq.32 d0, r0` is
0E800B10).

Steps:
- 4a. The NEON operand model (D and Q registers, data types and their aliases, the two-operand forms) and three
  registers of the same length: vadd / vsub, vhadd / vrhadd / vhsub, vqadd / vqsub, vand / vbic / vorr / vorn /
  veor, vbsl / vbit / vbif, vceq / vcge / vcgt / vacge / vacgt / vtst (and their reversed-operand aliases), vabd /
  vaba, vmax / vmin, vpadd / vpmax / vpmin, vmul / vmla / vmls (integer, polynomial, float), vqdmulh / vqrdmulh,
  vrecps / vrsqrts, vshl / vrshl / vqshl / vqrshl by register, vfma / vfms, NEON's vmaxnm / vminnm.
  Part 1, DONE (binate `311d4b282`): asm/arm32/neon (Three) and the parser (arm32_neon3.bn) for the NEON-only names,
  with MVE's encodings of them as near misses (its instruction definitions only: its aliases want Thumb code, and
  clang's FilterNearMisses drops a near miss that needs Thumb mode and another feature), every NEON q alias (vcleq
  alone as VCLE), a lane read as an operand of its own.  clang's facts: every NEON form carries a condition operand,
  which is why clang takes a condition and drops it; its token aliases let .s / .u stand for .i, .p8 and .f32 for
  VTST's sizes, .d / .f64 for the bitwise ones' .64; VBIC takes a type only with three operands, VORN none; VCLE /
  VCLT / VACLE / VACLT and the register shifts swap Vn and Vm; MVE's VQDMULH has .S8.  ~6,000 cases against clang,
  ~12,000 in review.  Part 2, DONE (binate `eb4693315`): vadd / vsub / vmul / vmla / vmls / vfma / vfms on the
  matcher in arm32_neon3.bn, their VFP forms (arm32_varith.bn), NEON's and MVE's in one table, MVE's vmlas / vfmas,
  the matcher's VFP4, NEON's vmaxnm / vminnm assembled (matchFormSIMDNoCond), clang's whole-name and keeps-`s`
  lists, and a shift after a register read as clang reads it (arm32_match_shift.bn; it was a stray token).  38,880
  cases against clang (9 names, 9 FP settings) agree but the condition policy and the by-scalar forms (4b), which
  until then are errors of another kind (`vmul.f32 d0, d1, d2[0]`) and make clang's near misses for `vmla.f32 q0, q1,
  d2`; ~100,000 in review.  Part 3, DONE (binate `1e97c1512`; user 2026-11-03, "yes, go ahead"): 3a's parser
  retired, every VFP data-processing name on the matcher (arm32_varith.bn; the driver arm32_vdp.bn, NEON's VABS /
  VNEG offered in arm32_neon2.bn, MVE's as near misses), the pre-UAL names as clang's mnemonic and instruction
  aliases (arm32_vfp_preual.bn), a mnemonic that is no instruction "invalid instruction" after its operands (a q
  alias without NEON too).  15,240 cases against clang agree but NEON's VABS / VNEG (4d); ~200,000 in review.
  Found in part 2, older than it: 3a's hand-written parser, which keeps vnmul /
  vdiv, vnmla / vnmls, vfnma / vfnms, vabs / vneg / vsqrt, vcmp / vcmpe and the pre-UAL names, misses near misses
  clang counts -- the two-operand forms', the other types', NEON's -- so 2,054 of 6,600 of those names' cases
  (types, register kinds, operand counts, conditions; five FP settings) have another message than clang's, the
  encodings agree but NEON's vabs / vneg (4d) (`vdiv.f32 s0, s1, s32`: clang's "any one of the following", here
  the S range; `vsqrt.f32 d0, d1, d2`: "invalid instruction", here "too many operands"; `vcmp.f32 d0, d1`:
  "invalid operand for instruction", here "invalid instruction").  The fix is those names on the matcher too.
- 4b. Three registers of different lengths and two registers and a scalar: vaddl / vaddw / vsubl / vsubw, vaddhn /
  vraddhn / vsubhn / vrsubhn, vabal / vabdl, vmlal / vmlsl / vmull (vmull.p8, vmull.p64), vqdmlal / vqdmlsl /
  vqdmull; vmul / vmla / vmls / vmull / vmlal / vmlsl / vqdmulh / vqrdmulh / vqdmull / vqdmlal / vqdmlsl by a
  scalar.
  Part 1, DONE (binate `f61776f39`): asm/arm32/neon's Diff and the parser (arm32_neondiff.bn) for the long, wide
  and narrowing ones, the matcher's "aes" and .p64 (VMULL.P64: ARMv8 with AES, no NEON, no condition operand;
  clang's rule that a mnemonic starting vmull and ending .p64 is never predicable).  49,536 cases against clang
  agree but the by-scalar forms (part 2); ~18,000 in review, which found .p64 dropped after VLDM (fixed).
  Part 2, DONE (binate `ade353377`): asm/arm32/neon's Scalar and the parser (arm32_neonscalar.bn) for the
  by-scalar forms among the names' other encodings (VMUL's Vd, Dm[x] too, so fmuls / fmacs as well), the
  matcher's scalar register (D0..D7, D0..D15) and lane (0..3, 0..1, a lane's low 32 bits) kinds.  29,040 cases
  against clang agree but a condition; ~50,000 in review, which found the encoder cutting a lane to 32 bits
  before its range check (fixed).  A floating-point lane is the binary64 item's.
- 4c. Shifts by an immediate: vshr / vsra / vrshr / vrsra / vsri, vshl / vsli, vqshl / vqshlu, the narrowing vshrn /
  vrshrn / vqshrn / vqshrun / vqrshrn / vqrshrun, vshll and vmovl, NEON's fixed-point vcvt.
  Part 1, DONE (binate `05e0557a8`): asm/arm32/neon's Shift and the parser (arm32_neonshift.bn) for the
  same-length ones (vshr / vsra / vrshr / vrsra / vsri, vshl / vsli, vqshl / vqshlu), MVE's as near misses, the
  matcher's right-shift (named, clang's "[1,8]" ..) and MVE's left-shift ranges.  Kept difference: clang takes a
  left shift's amount as any expression and wraps it (vshl.i8 d0, d1, #63 is #7; #-1 and undefined symbols
  assemble), an error here past 0..e-1 or not a constant ("operand must be an immediate in the range [0,7]" ..),
  as for its wrapped VCVT fraction bits and branch offsets.  33,696 cases against clang agree but a condition and
  those; ~37,000 in review, no bug.
  Part 2, DONE (binate `d9efabbd2`): Shift's narrowing and long forms and the parser for vshrn / vrshrn / vqshrn /
  vqrshrn / vqshrun / vqrshrun (Dd, Qm, #1..e/2), vshll (#1..e-1, and by the size: its other encoding, .I / .S /
  .U) and vmovl (vshll #0), the matcher's VSHLL ranges (named, "[1,7]" .. "[1,31]", "[8,8]" .. "[32,32]").  ~30,000
  cases against clang agree but a condition; ~330 more in review, which found no bug.  The core sweep's case lists,
  lost to the scratchpad's cleanup, were regenerated from the session transcript (none changed by this part) and
  now live in binate's scripts/bnas-sweep (`66f39a558`): `sweep.py <old bnas> <new bnas>` over 14,087 cases, by
  class, reporting changed objects (sections, relocations, symbols), changed errors and crashes.
  Part 3, DONE (binate `f9c6efb8f`): asm/arm32/neon's Cvt and the conversion parser's NEON VCVT between floating
  point and integers (F32 with S32 / U32, F16 with S16 / U16), by 1..e fraction bits and -- #0, or none -- the
  integer conversion, which 4d listed; the pre-UAL names on D and Q registers too.  Kept difference: fraction bits
  past 1..e or not a constant are an error ("[1,32]" / "[1,16]"), which clang wraps into another instruction (#33
  is VMOV by an immediate) or, for one it does not evaluate while parsing, a different word each run.  12,780
  cases against clang agree but those, a condition and 4d's conversions; ~640 more in review, no bug.
- 4d. Two registers, miscellaneous: vrev16 / 32 / 64, vpaddl / vpadal, vcls / vclz / vcnt, vmvn, vqabs / vqneg, vabs
  / vneg, the comparisons with #0, vswp / vtrn / vuzp / vzip, vmovn / vqmovn / vqmovun, NEON's
  vcvt (half and BFloat16) and vrint* / vcvta .. vcvtm, vrecpe / vrsqrte; the crypto aese / aesd / aesmc /
  aesimc, sha1h / sha1su1 / sha256su0.
  Part 1, DONE (binate `a3f2517d8`): asm/arm32/neon's Two (the whole two-register miscellaneous group, the
  comparisons with 0 included) and the parser (arm32_neon2.bn) for 22 names: vrev*, vpaddl / vpadal, vcls / vclz /
  vcnt, vmvn, vqabs / vqneg, vabs / vneg, vswp, vtrn / vuzp / vzip (vuzp.32 / vzip.32 on D registers as vtrn.32,
  clang's word), vmovn / vqmovn / vqmovun, vrecpe / vrsqrte; MVE's encodings as near misses.  38,016 cases against
  clang agree but a condition; ~4,300 more in review, no bug.
  Part 2, DONE (binate `e74dd75bb`): the comparisons with #0 (vceq / vcge / vcgt / vcle / vclt Vd, Vm, #0 and Vd,
  #0, among the three-register comparisons' encodings; vcle / vclt #0 their own encodings).  17,928 cases against
  clang agree but a condition; ~210 zero spellings and more in review, no bug.
  Part 3a, DONE (binate `c36c6a87d`): Two's rest of the group (VCVT.F16.F32 / BF16.F32 / F32.F16, VRINTN .. VRINTP,
  VCVTA .. VCVTM, the crypto) and the conversion parser's NEON forms (half and BFloat16 vcvt, vrint* on D / Q with
  clang's .f32.f32, vcvta .. vcvtm); nothing is matched without being assembled now, so the matcher's not-assembled
  kinds and their "not supported yet" are gone.  15,576 vrint* / vcvta .. vcvtm cases against clang agree; ~4,000
  more in review, no bug.
  Part 3b, DONE (binate `566cee05b`): the crypto names (aese / aesd / aesmc / aesimc .8, sha1h / sha1su1 / sha256su0
  .32, on Q registers, AES or SHA2 with ARMv8) through a new entry for mnemonics starting aes / sha1 / sha256, which
  takes clang's rule that none of those takes an `s` or a condition, a name or not; the matcher's "sha2" feature.
  11,565 cases against clang agree but "did you mean"; review, no defect.  4d is complete.
- 4e. One register and a modified immediate, and the moves: vmov / vorr / vmvn / vbic by an immediate, vmov between
  registers (vorr), vext, vtbl / vtbx, vdup from a scalar and from a core register, NEON's vmov between a core
  register and a scalar.
  - Part 1a, vorr / vbic / vand / vmvn by an immediate (with MVE's near misses; it also gives `vmvn d0, d1, #0`
    clang's "invalid instruction, any one of ..."): validated and reviewed, not yet landed.  Decided (user
    2026-11-06, "Reject (Recommended)"): clang's negative zero, `#-0` (-2^31 to clang, so `vorr.i32 d0, #-0` is
    VORR.I32 #0x80000000), is no modified immediate here.  Kept: values past the data type's range (clang's
    truncation; `vand.i16 #~0xab00` and `vmvn.i32 #~0xab000000` too), a condition, and a floating-point literal
    (the binary64 gap: clang takes `vmvn.i16 d0, #1.5` by its pattern's low bits).
  - Part 1b, VMOV: decided (user 2026-11-06, "Move VMOV onto the matcher (Recommended)"): one candidate list
    for all of VMOV's encodings (VFP, NEON, MVE near misses, the pre-UAL names), replacing the hand-written
    arm32_vmov*.bn, whose immediate reader takes only a literal under 2^20 and whose messages for the shapes
    VFP and NEON share are its own (`vmov.i32 s0, #1`).  First, 3b's VMOV case lists (~5,800 cases) go into
    scripts/bnas-sweep's corpus so the sweep guards the VFP forms through the move.
- 4f. Element and structure loads and stores: vld1 .. vld4 / vst1 .. vst4, multiple structures, one lane and all
  lanes, the alignment, write-back and a register post-index.
- 4g. ARMv8's and later additions: sha1c / sha1p / sha1m / sha1su0, sha256h / sha256h2 / sha256su1 (through 4d's
  crypto entry these are "invalid instruction" until then, clang's words or feature messages); vqrdmlah /
  vqrdmlsh (v8.1); vsdot / vudot, vfmal / vfmsl (v8.2); vcmla / vcadd (v8.3); the BFloat16 and Int8 matrix forms
  (vdot.bf16, vmmla, vfmab / vfmat, vsmmla / vummla / vusmmla, vusdot / vsudot).

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
