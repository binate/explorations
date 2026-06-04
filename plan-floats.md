# Plan: Floating-Point Types (`float32`, `float64`)

Status: COMPLETE (shipped); kept for design rationale (scope, type rules,
and deferred items). This is the detailed record of the float design
cuts; `claude-notes.md` only has the high-level "Primitive types —
DECIDED".

## Context

Binate's primitive-type spec already lists `float32` and `float64`
(see `claude-notes.md` — "Primitive types — DECIDED"). They had not
been implemented yet: the lexer didn't scan float literals, `pkg/types`
had no `TYP_FLOAT`, and neither the LLVM backend nor the VM had float
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

## Type rules

These are the ratified type-checking rules (in `pkg/types`):

- `TYP_UNTYPED_FLOAT → float32` / `float64`: assignable.
- `defaultType(TYP_UNTYPED_FLOAT) = float64`.
- `TYP_FLOAT` with same width: identical.
- `TYP_UNTYPED_INT` is NOT assignable to a float type (requires
  explicit cast).
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
- Cast: allow int↔float, float↔float. Reject float↔pointer,
  float↔bool, etc.

## Codegen / VM notes

- **IR storage of float literals**: store the raw literal text in
  `Instr.StrVal` and parse it in the backend (LLVM accepts decimal; the
  VM parses once at lowering time). The alternative — a `FloatVal
  float64` field — requires adding `float64` to the IR struct, which is
  circular (we're implementing floats). Hence the raw-text approach.
- **LLVM casts**: int → float = `sitofp` (signed) / `uitofp`
  (unsigned); float → int = `fptosi` / `fptoui` (truncation
  semantics); float → float widen = `fpext`; narrow = `fptrunc`.
  Comparisons use ordered `fcmp oeq/one/olt/ole/ogt/oge`.
- **VM opcodes split by width** (`BC_FADD32` / `BC_FADD64`, etc.)
  because the VM's register file is word-oriented and we need to know
  whether to read/write 4 or 8 bytes — following the existing
  `BC_LOAD32` / `BC_LOAD64` split. The dispatcher uses `bit_cast`
  between the int register slots and float operands.

## Risks & Open Questions

- **Raw-text storage of float literals in IR**: parsing text twice
  (once to validate, once at lowering time) is mildly redundant but
  avoids circularity and keeps the IR struct simple. If it becomes an
  issue we can add a `FloatVal float64` field in a follow-up.
- **VM register storage**: the VM's register file is currently
  word-sized; `float32` needs zero-extended storage. Confirm there's no
  conflict with e.g. the BC_LOAD32 implementation's sign-extension.
- **`bit_cast` between int and float**: does the language currently
  allow this? If not, we either add it or the VM does the bit-level
  trick in its own code (Binate already supports `bit_cast` between
  pointer types; generalizing to int↔float is probably a one-line
  change).
- **Hex float syntax later**: the raw-text approach means adding hex
  floats later is just a lexer change — no IR or backend work.
- **Constant folding**: deliberately punted. Without it, `1.0 + 2.0`
  generates a load/fadd sequence at runtime. Fine for a warm-up;
  revisit when performance matters.
