# Plan: Phase 3 — Unify String Literals as Composite-Literal Sugar

Sub-plan for Phase 3 of `plan-composite-literal-generalization.md`.
Phases 1 and 2 (Stage 2c default-flip + composite literals for
managed-slice / raw-slice) have landed. This plan covers Phase 3:
deleting the special string-lowering machinery and treating string
literals as parser-level sugar over composite literals.

## Goals

1. Delete `OP_STRING_TO_CHARS`, `OP_STRING_TO_ARRAY`, and their
   per-backend lowerings (LLVM, VM, native ARM64).
2. Delete `EmitStringToChars`, `EmitStringToArray`, and the
   `instr.BoolVal` flag that distinguishes Stage-2b copy.
3. Delete VM ops `BC_LOAD_STR`, `BC_STRING_COPY_MS`,
   `BC_STRING_COPY_ARR`. (`BC_LOAD_STR` keeps existing as part of
   strings infrastructure if needed by other paths — TBD.)
4. Eventually: delete `TYP_STRING` entirely.
5. Preserve today's rodata-alias optimization for
   `@[]const char` / `*[]const char` targets — string literals must
   not regress to per-encounter `MakeSlice + N stores` at runtime.

## Non-goals

- Rewriting how string literals are parsed (the AST node stays
  `EXPR_STRING_LIT`). Only the *lowering* changes.
- Changing the natural type or default type. Stage 2c default-flip
  fixed those (`[N]const char` natural, `@[]const char` default).
- Performance changes beyond preserving today's behavior.

## Current state (post Stage 2c default-flip)

For an EXPR_STRING_LIT in IR-gen, the path depends on the target type:

| target              | IR-gen path                                       | LLVM lowering                            |
|---------------------|---------------------------------------------------|------------------------------------------|
| `@[]const char`     | `EmitStringToChars` (BoolVal=false)               | `load %BnManagedSlice @.str.N.ms`        |
| `*[]const char`     | `EmitStringToChars` (BoolVal=false)               | extract 2 words from `@.str.N.ms`        |
| `@[]char`           | `EmitStringToChars` (BoolVal=true, Stage 2b)      | `MakeManagedSlice + memcpy from @.str.N` |
| `[N]char` / `[N]const char` | `EmitStringToArray` (Stage 2c Phase 1)    | `alloca [N x i8] + zerofill + memcpy`    |

Call sites of `EmitStringToChars` (10) and `EmitStringToArray` (2)
live in `pkg/ir/gen_stmt.bn`, `gen_control.bn`, `gen_selector.bn`,
`gen_expr.bn` (variadic-arg coercion).

Module data: codegen emits `@.str.N` (the raw `[L x i8]` rodata) and
`@.str.N.ms` (the paired `%BnManagedSlice` global) per string in
`f.Strings`. Both are consumed by the lowerings above.

VM backend: `BC_LOAD_STR` aliases (Aux=1 returns header pointer);
`BC_STRING_COPY_MS` Stage 2b; `BC_STRING_COPY_ARR` Stage 2c.

ARM64 native: `arm64.bn:242` lowers `OP_STRING_TO_CHARS` to a
materialization of the static %BnManagedSlice header.

## Target shape

After Phase 3, the IR for `var s @[]const char = "abc"` is the same
shape as `var s @[]const char = @[]const char{'a','b','c'}`:

```
%v_n = OP_CONST_INT 3
%v_s = OP_MAKE_SLICE(elemTyp=const uint8, n=%v_n)
%v_0 = OP_CONST_INT 'a'   ; etc., for indices 0..2
       OP_SLICE_SET(%v_s, 0, %v_0)
       OP_SLICE_SET(%v_s, 1, %v_1)
       OP_SLICE_SET(%v_s, 2, %v_2)
```

The codegen layer (LLVM, VM, ARM64) **detects the rodata-able
pattern** and emits a rodata-alias load instead of running the stores
at runtime. Concretely:

- All stored values are `OP_CONST_INT` of width 8 (compile-time bytes).
- The MakeSlice element type is `const uint8` (i.e., destination is
  `@[]const T` — read-only view, so aliasing is sound).
- The store indices are 0..N-1 in some order (any order is fine — we
  reconstruct the byte array from the (index, value) pairs).

For `[N]char` / `[N]const char` targets (array, not slice), the IR
becomes a normal `[N]T{'a','b','c'}` literal — alloca + element
stores, with optional zero-padding for `N > L`. No special op.

For `@[]char` (mutable, Stage 2b), the IR is `MakeSlice + N stores`
with non-const element type — codegen does NOT alias rodata (the
target needs to own its bytes). Today's Stage-2b copy is preserved
because the same IR shape forces a fresh allocation.

For `*[]const char` (raw slice borrowing rodata), the lowering is
trickier — see "Open questions" below.

## Why a peephole / pre-pass

Without optimization, every `var s @[]const char = "..."` would do
`MakeManagedSlice(1, N) + N stores` at runtime. Today's rodata-alias
is one load. The optimization matters for:

- Hot string-literal sites (error messages, format strings, identifier
  literals in the parser/lexer).
- Long string literals (parser error message cascades).
- Tight loops that touch string-literal sites.

The optimization is keyed on "all stored values are compile-time bytes
+ target is read-only." Not specific to string literals — applies
equally to user-written `@[]const char{'a','b','c'}` (per Phase 2a).

## Implementation strategy

Two viable approaches; pick one and stick to it.

### Approach A — IR pre-pass (recommended)

Add a function pass `optimizeRodataLiterals(f, &strings)` that runs
between IR-gen and codegen. It walks each block, detects the pattern,
and rewrites:

- The `OP_MAKE_SLICE` instruction becomes `OP_RODATA_MSLICE` (a new
  op) with `StrVal` set to the recovered byte sequence and a
  string-table ID stored in `IntVal`. Its `Args` are cleared.
- The N `OP_SLICE_SET` instructions are deleted from the block (or
  marked dead — easier with a `dead bool` flag on `Instr`).
- The pass appends the byte sequence to the module's `Strings`
  collection if not already present (deduplication via FindStringID).

Codegen then handles `OP_RODATA_MSLICE` as a load-from-`@.str.N.ms`,
similar to today's `OP_STRING_TO_CHARS` (but element-type-agnostic).

**Pros:**
- No look-ahead in codegen — each instruction processed independently.
- One source of truth for the optimization (the pass).
- IR remains side-effect-free; pass is purely transformational.
- Easy to test in isolation: feed in an IR Func, assert the rewrite.

**Cons:**
- New IR op (`OP_RODATA_MSLICE`). Runs counter to "no special ops"
  spirit. But it's *one* op for any const-byte composite, not strings
  specifically — closer to a generic "rodata managed-slice."
- The pass needs to be wired into the IR-gen → codegen pipeline.

Naming: maybe `OP_RODATA_SLICE` (covers raw-slice case too) or stick
with `OP_RODATA_MSLICE` and add a separate one for raw slice. TBD.

### Approach B — true codegen peephole

Inside the codegen instruction loop, when seeing `OP_MAKE_SLICE`,
look ahead at the next N instructions. If they match the const-store
pattern, emit rodata-alias and add the consumed instruction IDs to a
"skip set." Subsequent iterations check the skip set and no-op.

**Pros:**
- No new IR op. Strictly cleaner unification.

**Cons:**
- Each backend (LLVM, VM, ARM64) implements the peephole separately.
- Skip-set bookkeeping in every backend's instruction loop.
- Harder to test — the optimization is interleaved with emission.
- Look-ahead complicates `emitInstr`'s contract.

### Recommendation

**Approach A.** A new IR op for "rodata-able managed-slice" is a
small concession; it's still simpler than scattering peephole logic
across three backends. The op is *generic* (any const element type,
not strings), which keeps the unification spirit.

If the new op feels wrong, Approach B is the fallback.

## Phase 3 sub-steps

### 3.1 — Pre-pass + new IR op (one session)

1. Add `OP_RODATA_MSLICE` to `pkg/ir.bni` (and `OP_RODATA_SLICE` for
   the raw-slice variant — see Open Questions).
2. Add `EmitRodataMSlice(f, b, byteSeq, sliceTyp) @Instr` constructor.
3. Implement `optimizeRodataLiterals(f, &moduleStrings)` in a new
   file `pkg/ir/opt_rodata.bn`. Pass walks blocks, detects pattern,
   rewrites in place. Marks consumed instructions as dead (add
   `Dead bool` field to `Instr` if needed) or deletes from `Block.Instrs`.
4. Wire the pass into IR-gen (or codegen entry). Probably called
   from `GenerateModule` after IR-gen completes.
5. Update LLVM codegen (`emit_instr.bn`) to lower `OP_RODATA_MSLICE`
   to `load %BnManagedSlice @.str.N.ms`. (Same code as the
   `BoolVal=false` branch of `OP_STRING_TO_CHARS` today.)
6. Update VM lowering (`lower_instr.bn`) to lower to `BC_LOAD_STR`
   (Aux=1).
7. Update ARM64 native lowering similarly.
8. Add a conformance test for user-written `@[]const char{'a','b','c'}`
   that asserts both correctness and (optionally) the peephole fired
   via IR snapshot. Existing tests should be unaffected (the pass
   rewrites code that didn't exist before).

### 3.2 — Migrate string-literal IR-gen (one session)

For each `EmitStringToChars` / `EmitStringToArray` call site:

1. Replace with the equivalent composite-literal lowering. Specifically,
   write a helper `emitStringLitAsComposite(ctx, b, str, targetTyp)`
   that:
   - For `@[]T` / `*[]T` targets: emits `MakeSlice + N stores` of
     compile-time const-i8 values from the string text. Pass picks
     it up if target is const.
   - For `[N]T` array targets: emits an array literal — alloca, N
     element stores, zero-pad if N > strLen.
2. Run the full conformance suite. The pass should turn most cases
   into rodata-alias; `@[]char` (mutable) stays a per-encounter
   alloc+copy (correct).
3. Delete `EmitStringToChars`, `EmitStringToArray`, the
   `instr.BoolVal` Stage-2b flag.

### 3.3 — Delete special ops (one session)

1. Delete `OP_STRING_TO_CHARS`, `OP_STRING_TO_ARRAY` from `ir.bni`
   and `ir_ops.bn`.
2. Delete corresponding LLVM codegen, VM lowering, VM bytecode ops
   (`BC_STRING_COPY_MS`, `BC_STRING_COPY_ARR`), ARM64 native
   lowering.
3. Audit `TYP_STRING` for remaining users. If only `EmitConstString`
   produces it (as the *intermediate* type before the pass), rename
   or restructure so even that goes through the new path. Goal:
   remove `TYP_STRING` entirely.
4. Run hygiene. Update `claude-todo.md`. Update `claude-notes.md` if
   the intermediate semantics docs reference the old ops.

## Open questions

### Raw-slice variant

`*[]const char` rodata-alias today extracts the 2-word `%BnSlice`
header from the 4-word `%BnManagedSlice` global (fields 0 and 1).
Phase 3 needs to handle this too.

Options:
- Same `OP_RODATA_MSLICE` op, codegen branches on target type
  (extract 2 words for `*[]T`, full 4 words for `@[]T`).
- Separate `OP_RODATA_SLICE` op for raw-slice variant.
- Pre-pass also detects raw-slice composite literals (`*[]const T{...}`
  with all-const elements) and rewrites uniformly.

The raw-slice composite-literal path (Phase 2b, landed in `f0bbb43`)
already emits a stack-backed slice for non-rodata cases. The pass
would only intervene for all-const cases targeted at `*[]const T`.

Probably go with one op (`OP_RODATA_SLICE`?) and let codegen handle
the slice-vs-managed-slice extraction based on the target type. To
revisit after 3.1 lands.

### Identification of the pattern

The pattern detector needs to:
- Confirm `OP_MAKE_SLICE` length is a compile-time `OP_CONST_INT`.
- Walk up to N instructions and confirm each is an `OP_SLICE_SET`
  on the same MakeSlice result, with index = constant 0..N-1, value
  = `OP_CONST_INT` of width 8.

Edge cases:
- Empty literal `@[]const char{}` (N=0): becomes a length-0 rodata
  managed-slice. Today's `MakeManagedSlice(1, 0)` returns nil
  data/refptr — should preserve that (no rodata global needed).
- Side effects between MakeSlice and the stores (other instructions
  interleaved, reordering issues): pass must abort if the next N
  instructions aren't *exactly* the N stores (no interleaving).
  IR-gen does emit them contiguously today.
- Refcounting interactions: today's IR-gen emits a RefInc on the
  MakeSlice result if it's stored to a managed-slice variable. The
  pass must preserve that — the rodata managed-slice still goes
  through normal refcount paths, but `@.str.N.ms` is special: its
  refcount is "permanent" (reads as a large sentinel value so
  RefDec is a no-op). Today this works; Phase 3 must not break it.

### IR-gen vs pass detection

Could the optimization happen at IR-gen time instead of as a separate
pass? IR-gen knows when a composite literal has all-const-byte
elements. It could emit `OP_RODATA_MSLICE` directly.

**Pros:** simpler, no pass; fewer instructions to delete.
**Cons:** the unification is weaker — string literals and
`@[]const char{...}` user literals still take different code paths
inside `gen_access.bn` / `gen_stmt.bn`.

If we accept "decision at IR-gen, lowering uniform," we can skip the
pass entirely. The op `OP_RODATA_MSLICE` becomes an IR-gen helper, not
a peephole result.

This is **simpler than Approach A** but loses the "peephole"
framing of the plan. It's also the most pragmatic — implement once
in IR-gen, every backend just consumes it.

**Tentative recommendation: do this** unless the user prefers the
pass approach. It's effectively a "renamed and generalized"
`OP_STRING_TO_CHARS` — but the rename matters because:
- It's no longer string-specific (works for `@[]const T{...}` of
  any const-byte type).
- The IR-gen entry point is unified (one helper for both
  `EmitStringToChars` and `genManagedSliceLit`).

## Risks & rollback

- **Perf regression** if the pass / detection misses cases. Mitigation:
  add IR-snapshot tests asserting the rewrite happens for key shapes.
- **Refcounting bugs** if the rewritten op has different ownership
  semantics than `OP_STRING_TO_CHARS` did. Mitigation: keep the
  semantics identical (rodata managed-slice with sentinel refcount),
  add destructor tests.
- **Cross-backend drift**: each backend (LLVM, VM, ARM64) needs its
  own lowering for the new op. Mitigation: do them all in 3.1 before
  3.2 lands.

Rollback strategy: each sub-step (3.1, 3.2, 3.3) is a separate
commit. If 3.2 turns up problems, 3.1 stands alone (the new op +
pass are dormant for strings, fired only for user-written
const-char composite literals — bounded blast radius).

## Decision points for the user

1. **Approach**: Approach A (pre-pass + new IR op), Approach B (true
   codegen peephole), or the simpler "IR-gen-time decision" variant
   in Open Questions?
2. **Scope of session 1**: just 3.1 (the optimization machinery,
   no string migration), or 3.1 + a partial 3.2 (migrate one path
   end-to-end as a proof)?
3. **Raw-slice variant**: handle in 3.1 or punt to 3.3?
