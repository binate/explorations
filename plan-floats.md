# Plan: Floating-Point Types (`float32`, `float64`)

## Context

Binate's primitive-type spec already lists `float32` and `float64`
(see `claude-notes.md` — "Primitive types — DECIDED"). They have not
been implemented yet: the lexer doesn't scan float literals, `pkg/types`
has no `TYP_FLOAT`, and neither the LLVM backend nor the VM has float
arithmetic paths. This plan fills that gap end-to-end.

Float support is **not** part of the bootstrap subset — the Go bootstrap
interpreter will reject float-using code. That's fine: everything self-
hosted (the compiler, the VM-based `cmd/bni`, and compiled user code)
will support floats.

## Scope

### In scope

- **Literals**: `3.14`, `1e5`, `2.5e-3`, `.5`, `1.` — decimal only.
- **Types**: `float32`, `float64` (both primary names; no `f32`/`f64`
  alias — keeps symmetry with `int32`/`int64`).
- **Untyped-float class**: `TYP_UNTYPED_FLOAT`, defaulting to `float64`,
  coercible to `float32` when assigned to a float32-typed target.
- **Arithmetic**: binary `+ - * /` on same-typed floats.
- **Comparisons**: `== != < <= > >=` on same-typed floats, result `bool`.
- **Unary**: prefix `-`.
- **Casts** via `cast(T, expr)`:
  - `float32 ↔ float64` (both directions)
  - `int family ↔ float32 / float64` (both directions)
- **Backends**: LLVM codegen and the bytecode VM.
- **Tests**: unit tests in `pkg/lexer`, `pkg/parser`, `pkg/types`,
  `pkg/ir`, `pkg/codegen`, `pkg/vm` added alongside each commit, plus
  conformance tests spanning boot-comp, boot-comp-comp, boot-comp-int.

### Out of scope (for this pass)

- **Modulo `%`**: not defined on floats in the spec we're following.
- **Hex float literals** (`0x1.8p3`): deferred.
- **Special values**: no NaN/Inf literals; no `math.NaN()` library. The
  underlying FPU still produces NaN/Inf at runtime; we just don't add
  syntax.
- **`print` / `println` of floats**: punted — text formatting is gnarly,
  and every test can compare-to-float via an expected boolean or cast to
  int for printing. We'll add a conformance test helper
  `printFloatLike(x float64)` that prints a truncated decimal-ish
  representation only if we end up needing it — first pass will avoid.
- **Untyped float ↔ untyped int mixing**: an untyped int literal is NOT
  usable where a typed float is expected unless cast. That matches the
  strict "no implicit int↔float" rule. (We may relax this later for
  ergonomics — out of scope now.)
- **Constant folding**: the IR will not fold `1.0 + 2.0` at compile
  time; both backends will just emit the operations.
- **Math library**: no `sqrt`, `sin`, etc.
- **Non-bootstrap mode only**: no attempt to make the Go bootstrap
  interpreter handle floats. It will continue to reject float-using
  code (existing "unknown type" / "illegal token" errors are fine).

## Layout

Float values are plain value types, same kind of plumbing as ints.

- `float32` — 4 bytes, alignment 4.
- `float64` — 8 bytes, alignment 8.
- `TYP_FLOAT` kind with `Width = 32` or `Width = 64` and `Signed = true`
  (unused for floats; kept for struct uniformity).
- `TYP_UNTYPED_FLOAT` kind for literals before context resolves them.

On 32-bit targets `float64` is still 8-byte aligned; this matches LLVM's
default layout for `double` and is what the VM will assume.

## Commits (in order)

Each commit lands in a worktree branch, is cherry-picked to main after
review, and includes its tests. Failing tests block the commit.

### Commit 1 — Lexer + token + parser + AST

**Files**:

- `pkg/token/token.bn`: add `FLOAT` token type next to `INT`.
- `pkg/lexer/scan.bn:77` (`scanNumber`): after scanning the integer
  digits, if the next char is `.` (followed by a digit, not `..`) or
  `e`/`E`, keep scanning fractional/exponent parts and set
  `*typ = token.FLOAT`. Handles:
  - `3.14`
  - `3.` (digit + dot + non-digit-after — allowed only when not
    immediately followed by another `.` to avoid conflict with future
    range syntax; initially we only require `<digits>.<digit>*` and
    `<digits>[.][eE][+-]?<digits>`)
  - `.5` — *not* supported from scanNumber (that path doesn't start at
    `.`). A leading-dot float would require an entry point in the main
    lexer dispatch where we see `.` followed by a digit. Add it there.
  - `1e5`, `2.5e-3`, `1.e10`
- `pkg/ast.bni`: add `EXPR_FLOAT_LIT` next to `EXPR_INT_LIT`. `Name`
  field holds the raw literal text (same convention).
- `pkg/parser/parse_primary.bn:38`: add a `token.FLOAT` case that
  produces `EXPR_FLOAT_LIT`.

**Tests**:

- `pkg/lexer/scan_test.bn`: new `TestScanFloatLit` covering
  `3.14`, `1e5`, `2.5e-3`, `1.`, `.5`, `1.e10`; verify the raw literal
  text and the token type (`token.FLOAT`).
- `pkg/lexer/scan_test.bn`: negative cases — `1..2` should be
  `INT DOT DOT INT` (will eventually be range syntax) NOT a float.
- `pkg/parser/parse_primary_test.bn`: `TestParseFloatLit` — parse a
  single float literal and verify `EXPR_FLOAT_LIT` + `Name` matches.

### Commit 2 — Types package

**Files**:

- `pkg/types.bni`: add `TYP_FLOAT`, `TYP_UNTYPED_FLOAT` to the kind
  enum. Add `TypFloat32()`, `TypFloat64()`, `TypUntypedFloat()` exported
  constructors.
- `pkg/types/types.bn`: add `predeclaredFloat32`, `predeclaredFloat64`,
  `predeclaredUntypedFloat`. Reuse `makeIntType(..., width, signed)`
  shape with a new `makeFloatType(name, width)` helper.
- `pkg/types/scope.bn`: add `defineType(s, "float32", TypFloat32())`
  and `"float64"` in `universeScope()`. Extend `SizeOf`/`AlignOf` to
  return `Width/8` for `TYP_FLOAT` (same as int, matches both 32-bit
  and 64-bit target layouts).
- `pkg/types/types.bn` (`AssignableTo`, `Identical`, `defaultType`):
  - `TYP_UNTYPED_FLOAT → float32` / `float64`: assignable.
  - `defaultType(TYP_UNTYPED_FLOAT) = float64`.
  - `TYP_FLOAT` with same width: identical.
  - `TYP_UNTYPED_INT` is NOT assignable to a float type (requires
    explicit cast).
- `pkg/types/check_expr.bn` (arithmetic + comparison + unary):
  - Binary `+ - * /` where both operands are (typed or untyped) floats:
    result type = either the typed float or untyped float if both
    untyped.
  - Mixed typed/untyped float: result = the typed one (coerces the
    untyped).
  - Mixed int and float without cast: type error.
  - `%` (OP_REM) on floats: type error — "operator % not defined on
    float".
  - Bitwise ops on floats: type error.
  - Comparisons: same rules, result `bool`.
  - Unary `-` on float: ok, result same type.
- `pkg/types/check_expr.bn` (cast): allow int↔float, float↔float.
  Reject float↔pointer, float↔bool, etc.

**Tests**:

- `pkg/types/check_expr_test.bn`: new `TestCheckFloatLiteral`,
  `TestCheckFloatArith`, `TestCheckFloatCompare`, `TestCheckFloatCast`,
  `TestRejectMixedIntFloat`, `TestRejectFloatRem`,
  `TestRejectFloatBitwise`.
- `pkg/types/types_test.bn`: `TestTypFloat32`, `TestTypFloat64` —
  predeclared singletons, `AssignableTo(untypedFloat, float32)` etc.
- `pkg/types/scope_test.bn`: verify `float32`, `float64` resolvable in
  universe scope; `SizeOf` returns 4 and 8.

### Commit 3 — IR generation

**Files**:

- `pkg/ir.bni`: add `OP_CONST_FLOAT` next to `OP_CONST_INT`. Keep
  arithmetic/cmp/cast opcodes as-is; they dispatch on operand `Typ` in
  each backend.
- `pkg/ir/gen_expr.bn`: handle `EXPR_FLOAT_LIT` → emit `OP_CONST_FLOAT`
  with the literal's raw text stored (as for `EXPR_INT_LIT`).
- `pkg/ir/gen_expr.bn` (binary/unary/cast): no changes expected since
  they already delegate type-based codegen to the backend. Verify by
  tracing.

We need a place to store the float literal's parsed value. The cleanest
path: store the raw text in `Instr.StrVal` and parse it in the backend
(LLVM accepts decimal, and the VM can parse once at lowering time).
Alternative: add a `FloatVal float64` field — but that requires adding
`float64` to the IR struct, which is circular (we're implementing
floats). Use the raw-text approach for this first pass.

**Tests**:

- `pkg/ir/gen_expr_test.bn`: `TestGenFloatLit`, `TestGenFloatAdd`,
  `TestGenFloatCast`. Verify the `OP_CONST_FLOAT` op, its `.StrVal`,
  and that a `1.0 + 2.0` expression produces `OP_ADD` with float-typed
  operands.

### Commit 4 — LLVM codegen

**Files**:

- `pkg/codegen/emit.bn` (`llvmType`): map `TYP_FLOAT` width=32 → `float`,
  width=64 → `double`.
- `pkg/codegen/emit_instr.bn`:
  - `OP_CONST_FLOAT`: emit the literal text (normalize `1.` → `1.0`,
    etc., so LLVM accepts it; prefer the hex-float form LLVM emits for
    precise round-tripping — `0x3FF0000000000000` for `1.0` — actually
    decimal is fine, LLVM accepts `1.000000e+00`). Safest: generate
    `%r = fadd <type> 0.0, <literal>` OR use a constant initializer. For
    directness, use `fadd` against zero when inlining is needed; for
    top-level constants, emit the C99 decimal form directly.
  - `OP_ADD` / `OP_SUB` / `OP_MUL` / `OP_DIV`: dispatch on result type.
    If float, emit `fadd` / `fsub` / `fmul` / `fdiv` (signed div for
    int stays as-is).
  - `OP_NEG`: float → `fneg`, int stays `sub 0, x`.
  - `OP_EQ` / `OP_NE` / `OP_LT` / `OP_LE` / `OP_GT` / `OP_GE`: float
    operands → `fcmp oeq/one/olt/ole/ogt/oge` (ordered comparisons).
  - `OP_CAST`: extend the existing numeric cast path:
    - int → float: `sitofp` (signed) or `uitofp` (unsigned).
    - float → int: `fptosi` / `fptoui` (document truncation semantics
      in claude-notes.md later).
    - float → float (widen): `fpext`.
    - float → float (narrow): `fptrunc`.

**Tests**:

- `pkg/codegen/emit_test.bn`: `TestEmitFloatConst`, `TestEmitFloatAdd`,
  `TestEmitFloatCast` — compile a small IR snippet and grep the emitted
  LLVM text for the expected instructions (`fadd`, `fptosi`, etc.).

### Commit 5 — VM

**Files**:

- `pkg/vm.bni`: add bytecode opcodes
  `BC_FADD32`, `BC_FSUB32`, `BC_FMUL32`, `BC_FDIV32`, `BC_FCMP_EQ32`,
  `BC_FCMP_LT32`, `BC_FCMP_LE32` (and 64-bit analogues). We split by
  width because the VM's register file is word-oriented and we need to
  know whether to read/write 4 or 8 bytes.
- Alternative: pass width as an immediate. I'll go with the
  by-width-opcode approach since the existing VM already has
  `BC_LOAD32` / `BC_LOAD64` style splits — check what's there and
  follow suit.
- `pkg/vm/vm.bn` (the dispatcher): add the new opcodes, using
  `bit_cast(float32, i32)` / `bit_cast(float64, i64)` to read the
  operands out of register slots, then typed arithmetic, then
  `bit_cast` back to int for storage. (Binate supports `bit_cast`
  between pointer and int; extend it if needed for int↔float.)
- `pkg/vm/lower_instr.bn`: lower `OP_ADD` with float operand types into
  `BC_FADD32` / `BC_FADD64`. Same for other arith / cmp / cast ops.
- `pkg/vm/lower_instr.bn`: `OP_CONST_FLOAT` lowers to a `BC_MOV_IMM64`
  (or whatever the existing immediate-load opcode is) with the
  `bit_cast`ed integer representation of the parsed float.

**Tests**:

- `pkg/vm/vm_test.bn`: `TestVMFloatAdd`, `TestVMFloatCmp`,
  `TestVMFloatCast`, `TestVMFloatConst`. Each constructs a small
  bytecode sequence manually and asserts the result register.

### Commit 6 — Conformance tests

End-to-end tests run under all modes (`boot-comp`, `boot-comp-comp`,
`boot-comp-int`, etc.). Skip `boot` because floats aren't in the
bootstrap subset — add `.xfail.boot` for each.

Test cases (one file each):

- `NNN_float_lit.bn` — declare `var x float64 = 3.14`; cast to int and
  print. Verifies lexer + parser + type + codegen/VM const path.
- `NNN_float_arith.bn` — add/sub/mul/div of float64s; cast result to
  int and print. Covers `fadd`/`fsub`/`fmul`/`fdiv`.
- `NNN_float_cmp.bn` — ordered comparisons; print `"yes"`/`"no"`.
- `NNN_float32.bn` — same but for `float32` to exercise the
  narrow-float path and ensure the right LLVM `float` / VM 32-bit
  opcodes are selected.
- `NNN_float_cast.bn` — `cast(int, 3.7)` → 3 (truncation),
  `cast(float64, -5)` → -5.0 (and then cast back), `cast(float32,
  1.5)` → widen test via `cast(float64, f32_val)`.
- `NNN_float_untyped.bn` — verify `var x float32 = 3.14` works (untyped
  literal coerces), `var y = 3.14; /* y is float64 */`.

Negative tests:

- `NNN_err_float_rem.bn` — `1.5 % 2.0` should be a type error.
- `NNN_err_int_plus_float.bn` — `1 + 2.0` should be a type error
  without cast.
- `NNN_err_float_bitwise.bn` — `1.0 & 2.0` should error.

All positive tests get `.xfail.boot` markers. The negative tests
should be accepted by the bootstrap (it rejects float syntax at the
lexer/parser level earlier than the type checker, so the error messages
may not match; mark `.xfail.boot` as needed after verifying).

## Implementation Order (within each commit)

Each commit should:

1. Write failing unit tests first where the target package has an
   existing test suite.
2. Implement until the unit tests pass.
3. Run the full unit-test suite for any mode that can reach the
   changed package, plus the basic conformance run. No regressions.
4. Write the plan/progress note into this file's "Status" section at
   the bottom before committing.

## Risks & Open Questions

- **Raw-text storage of float literals in IR**: parsing text twice
  (once to validate, once at lowering time) is mildly redundant but
  avoids circularity and keeps the IR struct simple. If it becomes an
  issue we can add a `FloatVal float64` field in a follow-up.
- **VM register storage**: the VM's register file is currently
  word-sized; `float32` needs zero-extended storage. Confirm during
  commit 5 that there's no conflict with e.g. the BC_LOAD32
  implementation's sign-extension.
- **`bit_cast` between int and float**: does the language currently
  allow this? Needs verification during commit 5. If not, we either
  add it or the VM does the bit-level trick in its own code (Binate
  already supports `bit_cast` between pointer types; generalizing to
  int↔float is probably a one-line change).
- **Hex float syntax later**: the raw-text approach means adding hex
  floats later is just a lexer change — no IR or backend work.
- **Constant folding**: deliberately punted. Without it, `1.0 + 2.0`
  generates a load/fadd sequence at runtime. Fine for a warm-up;
  revisit when performance matters.

## Status

Planning only. No code written yet.
