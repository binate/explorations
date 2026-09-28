# Plan: aa64 assembler — FP and Advanced SIMD (NEON) instructions

Part of the aa64 text-assembler completeness entry in `claude-todo.md` (scope: every A64 extension
clang supports — "we don't need to support everything *now* -- we just don't want ad hoc/arbitrary
omissions just because we don't need it now").  Status: in progress, work-2.

## Where things stand (2026-09-27)

- The text parser (`pkg/binate/asm/parse`) has **no** FP or Advanced SIMD data-processing
  instructions — only the SIMD&FP loads/stores (LDR/STR/LDP/STP B/H/S/D/Q, LDAPUR/STLUR).  `v0`–`v31`
  parse as RC_V but no form takes one; there is no `vN.<T>`, lane, or register-list syntax, and the
  lexer has no floating-point literal.
- The exact-encoder layer (`asm/aarch64/isa`) has **no** FP / SIMD data-processing encoders.
- The backend wrapper package (`asm/aarch64`) hand-encodes the subset native codegen uses
  (`aarch64_fp.bn`, `aarch64_neon*.bn`: FMOV / FADD… / FCVT / SCVTF…, a few three-same NEON ops,
  MOVI/MVNI/FMOV-vector, DUP/INS/UMOV, LD1/ST1); its register fields are mapped and class-checked
  (`d1327d3f7`) but the encodings are not shared with the parser.

## Approach (same as the integer families)

Per instruction class: exact encoders in `isa` (range-checked, fail-loud, emit nothing on error) →
parser forms → golden words from clang (`-target aarch64-linux-gnu`, the needed `-march`) plus
clang-parity rejects; CONSTRAINED UNPREDICTABLE combinations rejected even if clang accepts them
(listed with the deliberate rejects).  Once `isa` covers a class, the backend wrappers in
`asm/aarch64` move onto it (one encoding per instruction, no second hand encoder).  One reviewed
commit per step; land each before starting the next.

## Steps

**A. FP scalar** (H / S / D precisions throughout; FP16 forms need `+fullfp16`)
1. ✅ FP data-processing: 1-source (FMOV reg, FABS, FNEG, FSQRT, FCVT H/S/D, FRINT{N,P,M,Z,A,X,I},
   FRINT32/64{Z,X}, BFCVT), 2-source (FMUL, FDIV, FADD, FSUB, FMAX, FMIN, FMAXNM, FMINNM, FNMUL),
   3-source (FMADD, FMSUB, FNMADD, FNMSUB), FCMP / FCMPE (incl. `#0.0`), FCCMP / FCCMPE, FCSEL —
   landed `fd8d2eafb` (2026-09-27), with the lexer's TOK_FLOAT floating-point literals.
2. FMOV (immediate): floating-point literal lexing + the 8-bit FP immediate (exact-representability
   check, no rounding).
3. FP ↔ integer: FCVT{N,P,M,Z,A}{S,U} (integer and fixed-point `#fbits`), SCVTF / UCVTF (both),
   FMOV GP ↔ FP (incl. `v0.d[1]`), FJCVTZS.
4. Move `asm/aarch64/aarch64_fp.bn` onto the isa FP encoders.

**B. Advanced SIMD vector** (each class also has its scalar forms where the architecture defines them)
1. Syntax infrastructure: `vN.<T>` arrangements, `vN.<T>[i]` lanes, `{…}` register lists (comma and
   range forms), scalar SIMD registers in vector instructions.
2. Three same (integer, FP, FP16, extra: SDOT / UDOT / SQRDMLAH / FCMLA / FCADD …).
3. Three different (long / wide / narrow, PMULL).
4. Two-register miscellaneous (incl. FP16) and across-lanes.
5. Copy (DUP / INS / UMOV / SMOV / MOV aliases) and modified immediate (MOVI / MVNI / ORR / BIC /
   FMOV vector).
6. Shift by immediate (incl. narrowing / long / fixed-point conversions).
7. Vector × indexed element.
8. Permute (ZIP / UZP / TRN), EXT, TBL / TBX.
9. Move `asm/aarch64/aarch64_neon*.bn` onto the isa encoders.

**C. SIMD loads / stores**: LD1–LD4 / ST1–ST4 (multiple structures, post-index), LD1–LD4 / ST1–ST4
single lane, LD1R–LD4R, LDAP1 / STL1 (FEAT_LRCPC3).

**D. Crypto**: AES, SHA1 / SHA256 / SHA512, SHA3 (EOR3 / RAX1 / XAR / BCAX), SM3 / SM4.

SVE / SVE2 and SME / SME2 get their own plan when they are reached.
