# Native AArch64 floats

Six conformance tests (279–283, 287) fail because pkg/native has
zero floating-point support. Tests cover float literals, arithmetic,
comparison, casts, untyped float, and println. The IR already emits
the same OP_ADD / OP_SUB / OP_CMP / OP_CAST / OP_NEG ops for floats
as for ints; backends dispatch on operand type.

## Approach: keep floats in GP-spilled bytes

Spill slots remain 8-byte GP-addressed memory cells. Float values
travel through GP scratch regs for load/store and only briefly
occupy FP regs during arithmetic:

```
FADD a, b → c
   LDR Xa, [sp, #spill_a]
   LDR Xb, [sp, #spill_b]
   FMOV Da, Xa
   FMOV Db, Xb
   FADD Dc, Da, Db
   FMOV Xc, Dc
   STR Xc, [sp, #spill_c]
```

Wasteful but consistent with the walking-skeleton's "spill
everything" model. No FP-LDR/STR needed; no separate FP regalloc;
existing spill addressing keeps working.

For now, treat both float32 and float64 as 64-bit internally
(operate via D regs). Tests 279–283 don't probe float32 precision
edges — they cast to int. If a future test demands strict f32, we
can switch to S regs at that point.

## Asm-level work (pkg/asm/aarch64)

New file `aarch64_fp.bn` with:

- **D registers**: D0..D31. Encoding-wise the same 0..31 numbers as
  X regs but in a different namespace. Define them offset (e.g.
  100..131) so callers can't accidentally pass an X where a D is
  expected; mask with 0x1f when emitting.
- **FMOV (gp↔fp)**:
  - FMOV Dd, Xn (gp→fp): 0x9E670000 base
  - FMOV Xd, Dn (fp→gp): 0x9E660000 base
- **FADD/FSUB/FMUL/FDIV (D)**:
  - FADD: 0x1E602800
  - FSUB: 0x1E603800
  - FMUL: 0x1E600800
  - FDIV: 0x1E601800
- **FNEG (D)**: 0x1E614000
- **FCMP (D)**: 0x1E602000 (D, D form: opcode2=00000)
- **FCVTZS (Xd, Dn)**: 0x9E780000 — float→int signed truncate
- **SCVTF (Dd, Xn)**: 0x9E620000 — int→float signed
- **FCVT (S↔D)**: 0x1E624000 (D→S), 0x1E22C000 (S→D)

Add a test file with golden encodings cross-checked against clang
output.

## Native-backend work (pkg/native/arm64)

- **FP scratch regs**: D16, D17 — caller-saved, used only briefly.
  Don't need a pool/regmap entry; just hardcode for each FP op.
- **emitConstFloat**: parse the literal text via a `bootstrap.ParseFloat`
  helper (or roll a small parser); convert to 64-bit IEEE 754 bits
  via bit_cast; MOVZ+MOVK chain into a GP scratch; FMOV to D reg;
  FMOV back to GP and spill. (Or: bypass the FP reg entirely and
  spill the bit pattern directly — even simpler.)
- **emitBinop**: dispatch on `ins.Typ.Kind == TYP_FLOAT` to emit
  FADD/FSUB/FMUL/FDIV via the GP-roundtrip pattern above.
- **emitCompare**: dispatch on operand type. FCMP sets PSTATE flags;
  use the same CSINC pattern as integers.
- **emitCast**: handle (int, float), (float, int), and (float, float)
  width changes. SCVTF for int→float, FCVTZS for float→int, FCVT
  for f32↔f64.
- **emitNeg**: FNEG when operand is float.
- **emitCall NSRN**: AAPCS uses NSRN (next SIMD/FP register number,
  separate from NGRN). Float scalar args go to D(NSRN); D regs
  available 0..7. Add NSRN tracking to the dispatch helper.
- **emitFunc prologue**: receive float params from D regs, store to
  spill slots.

## Tests

- pkg/asm/aarch64: golden-encoding tests for each new instruction.
- pkg/native/arm64: end-to-end EmitObject + clang link tests for
  float arithmetic, comparison, cast, and println.
- Conformance: 279–283, 287 should pass.

## Stages

Each commit is independently testable.

1. Asm-level FP encodings + tests.
2. Native: emitConstFloat (literal → spill); CAST int↔float (via
   SCVTF/FCVTZS). Unblocks 279, 282.
3. Native: float arithmetic (FADD/FSUB/FMUL/FDIV), FNEG. Unblocks
   280, 283.
4. Native: float comparison (FCMP + CSINC). Unblocks 281.
5. Native: NSRN-based FP arg passing for `bn_print_float`. Unblocks
   287.
