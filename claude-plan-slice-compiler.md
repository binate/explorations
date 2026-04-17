# Plan: Fix Slice Usage in the Self-Hosted Compiler

> **STATUS (2026-03-31): COMPLETED.** All `append` calls in the self-hosted compiler have been replaced with `buf.CharBuf`, `make_slice` + indexed assignment, and per-type helpers. `append` has been fully removed from the language.

## Problem

The self-hosted compiler (`.bn` files under `pkg/`) uses raw slices (`*[]T`) with
append for almost all dynamic collections — struct fields in AST/IR/types, module-level
accumulators, output buffers, temporary lists. Per the Binate spec, raw slices are
unmanaged value types that should be used for fixed-size views or short-lived local
data, not for growable retained collections.

## Current Append Usage (423 calls across 20 files)

| Package | File | Appends | Primary Use |
|---------|------|---------|-------------|
| codegen | emit.bn | 82 | String building (LLVM IR output) |
| ir | gen.bn | 67 | IR construction, module accumulators |
| ir | ir.bn | 48 | Instruction operand lists |
| parser | parser.bn | 33 | AST node lists |
| interp | interp.bn | 26 | Runtime value lists |
| interp | value.bn | 19 | Value conversion |
| loader | loader.bn | 23 | Package/path lists |
| types | checker.bn | 6 | Error/package accumulation |
| types | types.bn | 4 | Type construction |
| compile.bn | — | 32 | Clang args, file lists |

## Categories of Misuse

### 1. String building via `*[]char` append (~150 calls)

**Where:** `emit.bn` (82), `gen.bn` (name construction), `loader.bn` (paths),
`parser.bn` (error messages), `debug.bn`, `compile.bn` (paths/args)

**Pattern:**
```
var out *[]char
out = append(out, 'x')
out = appendStr(out, "hello")
out = appendInt(out, 42)
return out
```

**Fix:** Replace with a `CharBuf` (or `StringBuilder`) type — a struct with a
backing `*[]char` (or `*char` + length + capacity). This is the single biggest
category. The `appendStr`, `appendInt`, `appendChar` helper functions in emit.bn
become methods or functions operating on CharBuf.

This is the critical path: emit.bn alone has 82 append calls, and it's the
hottest code in the compiler (generates all LLVM IR output).

### 2. Object list accumulation (~200 calls)

**Where:** `ir.bn` (48 — instruction args), `gen.bn` (67 — module accumulators,
parameter/result lists), `parser.bn` (33 — AST lists), `interp.bn` (26),
`checker.bn` (6)

**Subpatterns:**

**a) Fixed-size lists built in a loop then stored:**
```
var params *[]@Param
for i := 0; i < len(decl.Params); i++ {
    params = append(params, makeParam(...))
}
f.Params = params
```
These are built locally, then assigned to a struct field. The final size is
often known or bounded. A typed buffer that can be "frozen" into a slice would
work, or these could use a pre-allocated slice if the size is known.

**b) Module-level accumulators (4 globals in gen.bn):**
```
var moduleStructs *[]ModuleStruct    // grown across entire module
moduleStructs = append(moduleStructs, ms)
```
These grow during IR generation and are read later. They need a growable buffer
with capacity. After generation completes, they could be frozen to slices.

**c) Small fixed-arity lists (instruction args):**
```
instr.Args = append(instr.Args, slice)
instr.Args = append(instr.Args, index)
```
Most instructions have 1-3 args. A small fixed-size array (3-4 elements)
would avoid allocation entirely for the common case.

**d) AST node children (parser.bn):**
```
fields = append(fields, parseFieldDecl(p))
```
Parser builds lists of unknown length. Needs a growable buffer.

### 3. Slice nil assignments (~15 occurrences in gen.bn)

```
moduleConsts = nil
moduleStructs = nil
```

**Fix:** Replace with `buf.Clear()` or `buf = CharBuf{}` / `buf = ListBuf{}`.
Or, if these are true "reset to empty," use `*[]T{}` syntax (zero-value slice
literal).

### 4. Slice nil comparisons and coercions (gen.bn, emit.bn)

```
// nil assigned to slice: re-emit as nil slice
if rhs.Typ.Kind == types.TYP_NIL { ... }
```

The codegen handles `nil` being assigned to slice-typed variables. Once the
type checker rejects this, the codegen path can be removed.

## Replacement Types

We need two internal types, both implemented in Binate:

### `CharBuf` — growable character buffer

See `claude-plan-charbuf.md` for the full design. Summary:
```
type CharBuf struct {
    Data @[]char   // managed backing store (refcounted)
    Len  int       // logical length
    Cap  int       // allocated capacity
}
```

Uses `@[]char` (managed-slice) for proper memory management. Geometric growth
(doubling) gives amortized O(1) append. Return-by-value pattern:
`b = buf.WriteByte(b, 'x')`.

**Dependency:** Requires `make_slice(T, n)` to exist and return `@[]T`. See
`claude-plan-fix-make.md` for the migration plan.

### `List[T]` — growable typed list (post-generics)

For now, since we don't have generics, we need concrete list types for the
most common element types:
- `InstrList` (for `*[]@Instr`) — used in ir.bn
- `FuncList` (for `*[]@Func`) — used in ir.bn module
- etc.

Or, pragmatically: keep raw slices for small, bounded, locally-built lists
(like instruction args with 1-3 elements) and only replace the growable
accumulator patterns.

**Pre-generics pragmatic approach:** For object lists that are built and then
stored (not further grown), we can continue using raw slices with a "make
slice of size N, fill it" pattern instead of append. For the few truly
growable lists (module accumulators), write concrete buffer types.

## Implementation Order

### Prerequisite: Fix `make` semantics

See `claude-plan-fix-make.md`. Steps 1-3 of that plan must complete before
CharBuf can be implemented (CharBuf needs `make_slice(T, n)` → `@[]T`).

### Step 1: Implement CharBuf (`pkg/buf`)

See `claude-plan-charbuf.md`. Depends on correct `make`.

### Step 2: Convert `emit.bn` to use CharBuf
- This is the biggest win (82 appends → CharBuf method calls)
- Replace `var out *[]char` with `var out CharBuf`
- Replace `out = appendStr(out, ...)` with `CharBuf_writeStr(&out, ...)`
- Replace `return out` with `return CharBuf_toSlice(&out)`
- The `appendStr`, `appendInt`, `appendChar` helper functions become
  wrappers or are replaced directly

### Step 3: Convert `gen.bn` string building to CharBuf
- qualName construction, error messages, etc.

### Step 4: Convert `loader.bn`, `parser.bn`, `compile.bn` string building

### Step 5: Replace module-level accumulators in gen.bn
- Either concrete buffer types, or use pre-sized slices where possible
- `moduleConsts`, `moduleStructs`, `moduleGlobals`, `moduleFuncs`

### Step 6: Fix instruction arg building in ir.bn
- Most instructions have 1-3 args; consider small fixed-size array
- Or keep append for now since these are small and local

### Step 7: Remove nil-slice semantics
- Type checker: reject `slice == nil`, `slice = nil`
- Remove nil-to-slice coercion in gen.bn
- Replace `x = nil` with `x = *[]T{}` or `x = CharBuf{}`
- Update conformance tests (043, 076, 087)
- Fix `emitDecForScopeVars` comment

### Step 8: Remove append from the language
- See `claude-plan-remove-append.md`

## Validation

Each step must pass:
- All unit tests (`-test pkg/ir`, `-test pkg/codegen`, etc.)
- All conformance tests (bootstrap + compiled modes)
- Self-compilation (compiled-compiler mode)

## Risk

Medium. The compiler is self-hosting — every change must compile correctly
under both the bootstrap interpreter and the self-compiled compiler. Changes
to slice semantics affect the most fundamental data structures. Incremental
steps with full test runs between each are essential.

The CharBuf conversion (steps 1-4) is low risk since it's a mechanical
replacement. The module accumulator changes (step 5) and nil-semantics
changes (step 7) are higher risk.
