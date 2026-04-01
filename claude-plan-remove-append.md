# Plan: Remove `append` from the Binate Language

> **STATUS (2026-03-31): COMPLETED.** `append` has been fully removed from the language -- parser, type checker, IR gen, codegen, both interpreters, all source code, tests, and conformance tests. Replacements: `buf.CharBuf` for strings, `make_slice(T, n)` + indexed assignment for known-size allocations, per-type append helpers for other types.

## Decision

`append` is being removed from the language entirely. It's a performance footgun
(O(n) per call via realloc, O(n^2) for incremental building) and doesn't fit the
language's design. Growable collections belong in a library, not as a builtin.

## Current Usage

### Bootstrap interpreter (Go)
- `evalAppend` in `interpreter.go:169-193` — the builtin implementation
- 73 uses of Go's `append()` in the interpreter's own Go code (these are Go, not
  Binate, so they stay — but the *Binate-level* append builtin gets removed)

### Self-hosted compiler (.bn files)
- 423 `append()` calls across 20 files
- Biggest users: emit.bn (82), gen.bn (67), ir.bn (48), parser.bn (33)
- Two patterns: string building (`[]char`) and object list accumulation

### Conformance tests
- 67 `append()` calls across 18 test files
- Tests that specifically test append behavior: 020_nil_append_len, 043_nil_slice

### Runtime (C)
- `bn_append_i64`, `bn_append_i8`, `bn_append_struct` in `binate_runtime.c`
- These do per-element `realloc` — the source of the O(n) per append

## Replacement

### For string building: `CharBuf`

See `claude-plan-slice-compiler.md` for details. A struct with backing store,
length, and capacity. Amortized O(1) append via geometric growth.

### For object list accumulation

**Pre-generics:** Concrete buffer types for the most common element types, or
pre-sized raw slices filled via indexing.

**Post-generics:** A generic `Vec[T]` or `Buffer[T]` type in the standard library.

### For conformance tests

Tests that use append get rewritten to use either:
- Pre-sized arrays: `var arr [5]int; arr[0] = 1; ...`
- A buffer type if testing growable behavior
- Removed entirely if they only tested append itself (020, 043 partial)

## Removal Steps

### Step 1: Implement CharBuf and any needed concrete buffer types

Before removing append, the replacement must exist and work. See the compiler
plan for CharBuf details.

### Step 2: Convert all self-hosted compiler code from append to buffers

All 423 append calls in the compiler must be replaced. This is the bulk of the
work. Validate with full conformance + self-compilation after each file.

### Step 3: Update conformance tests

- Rewrite tests that use append to use the new buffer types or pre-sized arrays
- Remove or rewrite 020_nil_append_len (append-specific test)
- Update 043_nil_slice (nil-append behavior)
- All other tests that incidentally use append: rewrite

### Step 4: Remove append from the bootstrap interpreter

- Delete `evalAppend` from `interpreter.go`
- Remove the `"append"` entry from the builtins map
- Remove `NilVal` special handling for append

### Step 5: Remove append from the type checker

- Remove `append` from recognized builtins in `checker.bn` and `checker.go`
- Ensure `append(...)` is a type error

### Step 6: Remove append from the parser/lexer

- Remove `APPEND` token if it exists as a keyword
- Or just let it be an unresolved identifier (which the type checker catches)

### Step 7: Remove append from IR generation

- Remove `genAppend` or the append path in `genBuiltin` from `gen.bn`
- Remove `OP_APPEND` IR instruction from `ir.bni` and `ir.bn`
- Remove append emission from `emit.bn`

### Step 8: Remove runtime append functions

- Delete `bn_append_i64`, `bn_append_i8`, `bn_append_struct` from `binate_runtime.c`
- Remove their declarations from emit.bn's runtime preamble

### Step 9: Update documentation and notes

- Remove append from grammar.ebnf
- Update exploration notes
- Update any remaining references

## Files Modified

| File | Change |
|------|--------|
| `bootstrap/interpreter/interpreter.go` | Delete evalAppend, remove builtin entry |
| `bootstrap/types/checker.go` | Remove append type checking |
| `pkg/ir/gen.bn` | Remove genAppend/genBuiltin append path |
| `pkg/ir/ir.bn` | Remove EmitAppend |
| `pkg/ir.bni` | Remove OP_APPEND |
| `pkg/codegen/emit.bn` | Remove OP_APPEND emission, remove runtime declarations |
| `pkg/types/checker.bn` | Remove append builtin |
| `runtime/binate_runtime.c` | Delete bn_append_* functions |
| `conformance/*.bn` | Rewrite 18 test files |
| All 20 compiler .bn files | Replace 423 append calls |
| `compile.bn` | Replace 32 append calls |

## Order Relative to Other Plans

1. **First:** Implement CharBuf + buffer types (compiler plan step 1)
2. **Second:** Convert all compiler code (compiler plan steps 2-6)
3. **Third:** Remove append from the language (this plan steps 4-9)
4. **In parallel:** Fix nil-slice semantics (compiler plan step 7)

The key constraint is that we can't remove append until all code that uses it
has been converted. The conversion must happen while append still works.

## Validation

- Full conformance suite (all modes) after each step
- Self-compilation after each compiler code change
- The bootstrap interpreter must still be able to run the compiler at every point

## Risk

High overall due to the sheer volume of changes (400+ call sites). But each
individual change is mechanical and low-risk. The strategy is incremental
conversion with continuous testing.

The riskiest moment is step 4 (removing from bootstrap) — at that point,
ALL Binate code must be converted, or the bootstrap will fail to interpret it.
