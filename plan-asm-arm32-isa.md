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
3. **VFPv3 / v4** (the FPU part of the model: `.fpu`, d16 / d32, fp16, vfp4, fp-armv8).
4. **NEON** (incl. the v8 crypto forms).

## Stage 2: the complete A32 core

An exact encoder package, `pkg/binate/asm/arm32/isa` (as `asm/aarch64/isa`: each encoder emits exactly the one
instruction its operands name, or sets an error — nothing split, synthesized or masked into range), the parser
taking every A32 form clang takes through it, each gated by the stage 1 features, then `asm/arm32`'s backend
encoders routed through it.  One instruction class per commit, each checked by a differential harness against clang
(operand combinations valid and invalid: registers incl. SP / PC, immediates at and past their ranges, every shift,
condition and flag form, clang's aliases and shorthands), the cases pinned as in-tree goldens.

- 2a. Data processing: the 16 operations × immediate (modified immediate), register (shift by immediate, RRX) and
  register-shifted-register forms, S, the two-operand and Rd-less / Rn-less forms, clang's substitutions.
- 2b. Multiplies and divide: mul / mla / mls, the long forms (umull, umlal, smull, smlal, umaal), the DSP halfword
  ones (smla<x><y>, smlaw<y>, smulw<y>, smlal<x><y>, smul<x><y>), the dual ones (smuad / smusd / smlad / smlsd /
  smlald / smlsld, with X), the most-significant-word ones (smmul / smmla / smmls, with R); sdiv / udiv.
- 2c. Saturating (qadd / qsub / qdadd / qdsub, ssat / usat / ssat16 / usat16), parallel add / sub (s / q / sh / u /
  uq / uh × add16 / asx / sax / sub16 / add8 / sub8), sel, usad8 / usada8, pkhbt / pkhtb.
- 2d. Extend and extend-add (sxtb / sxth / sxtb16 / uxt*, sxtab / ... with rotation), bitfield (bfc / bfi / sbfx /
  ubfx), reverse (rev / rev16 / revsh / rbit), clz.
- 2e. Loads and stores: ldr / str / ldrb / strb, ldrh / strh / ldrsb / ldrsh, ldrd / strd in every addressing mode
  (immediate, register, scaled register, pre / post-index, writeback, literal), the T forms, pld / pldw / pli.
- 2f. Exclusives and ordering: ldrex / strex (b, h, d), clrex, swp / swpb, the v8 acquire / release forms (lda / stl
  / ldaex / stlex families).
- 2g. Block transfer: ldm / stm ia / ib / da / db, writeback, `^`, push / pop forms.
- 2h. Branches: b, bl, blx <label>, blx <reg>, bx, bxj.
- 2i. System and hints: svc, bkpt, udf, hvc, smc, eret, mrs / msr (APSR / CPSR / SPSR fields, banked registers),
  cps, setend, srs / rfe, nop / yield / wfe / wfi / sev / sevl / dbg / csdb / esb, dmb / dsb / isb with options,
  sb, ssbb / pssbb, setpan, hlt.
- 2j. Coprocessor: mcr / mrc (and 2), mcrr / mrrc (and 2), cdp (and 2), ldc / stc (and 2, L).
- 2k. `asm/arm32`'s encoders (the native backend's) through isa.

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
