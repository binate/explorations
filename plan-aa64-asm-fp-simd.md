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

**A. FP scalar** (H / S / D precisions throughout; FP16 forms need `+fullfp16`) — ✅ done
1. ✅ FP data-processing: 1-source (FMOV reg, FABS, FNEG, FSQRT, FCVT H/S/D, FRINT{N,P,M,Z,A,X,I},
   FRINT32/64{Z,X}, BFCVT), 2-source (FMUL, FDIV, FADD, FSUB, FMAX, FMIN, FMAXNM, FMINNM, FNMUL),
   3-source (FMADD, FMSUB, FNMADD, FNMSUB), FCMP / FCMPE (incl. `#0.0`), FCCMP / FCCMPE, FCSEL —
   landed `fd8d2eafb` (2026-09-27), with the lexer's TOK_FLOAT floating-point literals.
2. ✅ FMOV (immediate) + FMOV GP ↔ FP (W–S, X–D, W/X–H; zero is FMOV from WZR/XZR) — landed
   `75fbf40d0` (2026-09-27).  The 8-bit immediate is decided exactly (value × 128), never rounded.
   FMOV `Xd, Vn.D[1]` and back (isa.FMovTopHalf) is parsed since B1's lane syntax (`7e91dc04f`).
3. ✅ FP ↔ integer: FCVT{N,P,M,Z,A}{S,U} (integer and fixed-point `#fbits`), SCVTF / UCVTF (both),
   FJCVTZS, and FEAT_FPRCVT (integer in an S / D register of a different size) — landed `abc0540ed`
   (2026-09-27).  The same-size FP-register forms (`fcvtzs s0, s1`) are Advanced SIMD scalar → B4.
4. ✅ Move `asm/aarch64/aarch64_fp.bn` onto the isa FP encoders — landed `867f6c45c` (2026-09-27).

**B. Advanced SIMD vector** (each class also has its scalar forms where the architecture defines them)
1. Syntax infrastructure.
   - ✅ `vN.<T>` arrangements (incl. the partial 2b / 4b / 2h), `vN.<t>[i]` lanes (also on a partial
     arrangement: `v2.4b[3]`), `vN.<suffix>` always a register (unknown suffix = "invalid vector kind
     qualifier", as clang; ADR / ADRP's symbol reading of such names is a deliberate reject), FMOV
     `Xd, Vn.D[1]` / back, and — brought forward from B5 — the copy class (DUP / INS / UMOV / SMOV and
     their MOV aliases) — landed `7e91dc04f` (2026-09-28).
   - ✅ `{…}` register lists — comma and range forms, wrapping mod 32, 1–4 registers, a lane after a
     list of bare elements, suffixes compared case-sensitively as clang does — and, brought forward from
     B8, TBL / TBX — landed `19a84e582` (2026-09-28).
   - Apple's legacy NEON syntax (`dup.4s v0, w1`, `umov.s w0, v1[1]`, `tbl.16b v0, {v1}, v3`), which
     clang accepts on every target, is NOT supported (user, 2026-09-28: "we don't need alternate syntax,
     unless there's a compelling reason (we've always tended to favor Intel/ARM syntax, I suppose)") —
     listed with the deliberate rejects (`bf5f7966b`).  Kind-less `vN[i]` is still needed for FEAT_LUT
     (`luti2 v0.16b, {v1.16b}, v2[0]`).
   - Scalar SIMD registers in vector instructions (with the classes that use them).
2. Three same, in three commits:
   - ✅ (a) integer (vector + scalar), the bitwise AND … BIF, CMLE / CMLT / CMLO / CMLS, and the
     whole-vector `mov` (ORR alias, any 64/128-bit arrangement as clang) — landed `83c563da2`
     (2026-09-28).  Note for B4 / B7: the three-same parser rejects every form it does not handle for
     its mnemonics, so by-element MUL / MLA / MLS / SQ(R)DMULH, compare-against-zero, and scalar
     pairwise ADDP must be dispatched ahead of it (ORR / BIC vector-immediate too, B5).
   - ✅ (b) FP and FP16 (vector + scalar), FAMAX / FAMIN (FEAT_FAMINMAX) and FSCALE (FEAT_FP8), and
     the FCMLE / FCMLT / FACLE / FACLT register aliases (scalar H forms rejected, as clang; the
     review reports the architecture defines none of the four) — landed `9b90c8190` (2026-09-28).
     Still to come in other classes: FP by-element (B7), scalar pairwise FADDP / FMAXP / … (B4).
   - (c) three same extra, in three commits:
     - ✅ (c1) SQRDMLAH / SQRDMLSH (vector + scalar), SDOT / UDOT / USDOT, SMMLA / UMMLA / USMMLA —
       landed `59a621006` (2026-09-28).
     - ✅ (c2) FCMLA / FCADD (#rot), BFDOT / BFMMLA / BFMLALB / BFMLALT, FMLAL / FMLSL / FMLAL2 /
       FMLSL2 — landed `5fb7778d6` (2026-09-28).  BFCVTN / BFCVTN2 are two-register misc (B4).
     - ✅ (c3) FP8: FDOT (2-way / 4-way), FMLALB / FMLALT, FMLALLBB / BT / TB / TT, the three-register
       FCVTN / FCVTN2, FMMLA (F8F16MM / F8F32MM) — landed `74a3c37f1` (2026-09-28).  Dispatch note for
       B4 / B7: this parser claims fcvtn / fcvtn2 / fdot / fmlalb / fmlalt / fmlall* / fmmla by mnemonic
       and rejects other shapes (two-register fcvtn: "not supported yet"), so the two-register FCVTN /
       FCVTN2 (B4) and the FP8 by-element forms (B7) must be dispatched ahead of it or from inside it
       (e.g. branch on the operand count, as emitA64FP hands vector destinations to the SIMD parser).
     - Not in Apple clang 21, so no reference words yet: the half-precision FDOT (FEAT_F16F32DOT,
       `fdot v0.2s, v1.4h, v2.4h`) and FMMLA (FEAT_F16F32MM / F16MM) Advanced SIMD forms — add them
       when a clang that knows them is available.
3. ✅ Three different (long / wide / narrow and their "2" forms, PMULL incl. 1Q, the scalar SQDMLAL /
   SQDMLSL / SQDMULL) — landed `52de9ee20` (2026-09-28).  Note: `aarch64_instr_dp2.bn` (the integer
   data-processing parser, which routes SIMD destinations of shared mnemonics) is at ~450 lines —
   put the next shared-mnemonic route elsewhere, not there.
4. Two-register miscellaneous (incl. FP16; incl. the scalar FCVT* / SCVTF / UCVTF `s0, s1` forms)
   and across-lanes, in three commits:
   - ✅ (a) integer: REV16/32/64, SADDLP/UADDLP/SADALP/UADALP, SUQADD/USQADD, CLS/CLZ/CNT, NOT/MVN/
     RBIT, SQABS/SQNEG/ABS/NEG, CMxx #0, XTN/SQXTN/UQXTN/SQXTUN(2), SHLL(2) — landed `69fdc5e0e`
     (2026-09-28), after the Advanced SIMD encoders moved to package `asm/aarch64/isa/simd`
     (`4f75fcd93`; isa.bni had reached its 1000-line cap).  Shared-mnemonic routing from the
     integer parser is in `parse/aarch64_instr_simd_route.bn`.
   - ✅ (b1) floating-point, non-converting: FABS/FNEG/FSQRT, FRINT*, FRINT32/64*, FRECPE/FRSQRTE/
     FRECPX, FCMxx #0.0 (zero as `#0.0` / `0.0` / `#0` / `0`), URECPE / URSQRTE; FP16 forms —
     landed `46bd1a0b3` (2026-09-28).  The operand scanner's floating-point literal item
     (IT_FLOAT) is opt-in (`parseA64ItemsFloat`) so no integer operand ever sees one; the
     vector FMOV immediate (B5) will want it.
   - ✅ (b2) conversions: FCVT{N,A,P,M,Z}{S,U} / SCVTF / UCVTF (vector and same-size scalar),
     FCVTL/FCVTN/FCVTXN(2) (+ scalar FCVTXN), BFCVTN(2), and the FP8 F1CVTL / F2CVTL / BF1CVTL /
     BF2CVTL(2); FP16 forms — landed `18788e730` (2026-09-28).
   - ✅ (c) across lanes (ADDV, SADDLV/UADDLV, S/UMAXV, S/UMINV, FMAXNMV/FMINNMV/FMAXV/FMINV incl.
     FP16) and scalar pairwise (ADDP d, FADDP / FMAXP / FMINP / FMAXNMP / FMINNMP incl. 2h) —
     landed `578120799` (2026-09-28).  B4 complete.
   - Note for FEAT_CSSC (later): the general-register ABS / CNT (and friends) will need the same
     integer-first routing that NEG / CLZ have — today `abs` / `cnt` go straight to the SIMD parser.
5. Modified immediate (MOVI / MVNI / ORR / BIC / FMOV vector).  (The copy class landed with B1.)
6. Shift by immediate (incl. narrowing / long / fixed-point conversions — the vector
   `fcvtzs v0.4s, v1.4s, #3` AND the same-size scalar `fcvtzs s0, s1, #3` / `scvtf h0, h1, #16`,
   which the conversion parser now rejects as "not supported yet").
7. Vector × indexed element.
8. Permute (ZIP / UZP / TRN), EXT.  (TBL / TBX landed with B1.)
9. Move `asm/aarch64/aarch64_neon*.bn` onto the isa encoders.

**C. SIMD loads / stores**: LD1–LD4 / ST1–ST4 (multiple structures, post-index), LD1–LD4 / ST1–ST4
single lane, LD1R–LD4R, LDAP1 / STL1 (FEAT_LRCPC3).

**D. Crypto**: AES, SHA1 / SHA256 / SHA512, SHA3 (EOR3 / RAX1 / XAR / BCAX), SM3 / SM4.

SVE / SVE2 and SME / SME2 get their own plan when they are reached.
