# Plan: codegen `byval` for >16-byte struct params

**Status**: COMPLETE (shipped) — binate `f5340fac` + `8ba29d11`. Kept for
design rationale and the divergence-from-plan note below.

Implementation diverges from the plan below on one key point: the
emitted LLVM IR uses a plain `ptr` type (no `byval` attribute), not
`ptr byval(<T>) align 8`.  Empirically tested: LLVM AArch64's
`byval` lowering lays the struct on the caller stack (like SysV-
byval), NOT the pointer-in-reg-indirect form clang picks for the
plain `ptr` shape at the frontend.  Emitting plain `ptr` gets the
indirect-pointer-pass on BOTH targets — matching the
IndirectLargeAggregates path the native backends now take.  Caller-
side alloca + memcpy is in the call's preamble
(`writeByvalArgPreamble`) and OP_STORE's byval-param branch
(`IsByvalParamRef` flag → emit `llvm.memcpy` from byval pointer to
the slot alloca).

The original problem statement below is left for historical context;
the "Fix shape" section is what actually shipped modulo the
byval-vs-plain-ptr substitution.

---

## Problem

When a function takes a >16-byte struct param by value across a package
boundary, the LLVM-compiled callee and the native-compiled caller (or
vice-versa) disagree on the calling convention.  Concretely on
x86_64-darwin:

- 56-byte `Operand` struct passed by value to `pkg/op.Encode(cond, rt, op)`.
- Native `main` (per current SysV.SplitAggregates=false): puts the whole
  56-byte op on the caller's outgoing-stack as 7 qwords.
- LLVM-compiled `Encode`: reads `op.Kind` from `%rdx`, `op.A` from `%rcx`,
  `op.B` from `%r8`, `op.Label.data` from `%r9`, the remaining 3 qwords
  from `0x70..0x80(%rsp)`.
- Result: `Encode` reads garbage from `%rdx..%r9`, returns wrong answer.

Same shape blocks 331 (cross-pkg method receiver) and 411 (cross-pkg
stringer through `pkg/std`).  On aarch64 the symptom is split across X
regs + stack; the native side currently *matches* the LLVM emission
(SplitAggregates=true) so aa64 conformance is green — but that match is
to a non-textbook emission, not the spec.

## Root cause (empirical)

clang emits LLVM IR with the `byval` attribute on >16-byte struct
params:

```llvm
define i64 @Encode(i64 %0, i64 %1, ptr byval(%struct.Operand) align 8 %2)
```

LLVM lowers `byval` to the target's textbook ABI:

- x86_64 SysV: `ptr byval` → struct laid out entirely on caller's outgoing
  stack, no register portion.  Matches §3.2.3 MEMORY classification.
- AAPCS64: `ptr byval` → struct laid out in caller frame, pointer to it
  passed in the next free X register (indirect).  Matches AAPCS spec for
  large aggregates.

Binate's codegen never emits `byval` (verified: zero matches across
`pkg/codegen/`).  LLVM falls back to its IR-level rules for struct-value
params, which:

- on x86_64 decomposes the struct into separate i64 args (the
  `%rdx/%rcx/%r8/%r9/stack…` spread we observe);
- on aarch64 spreads it across X regs and stack (matches AAPCS split,
  but not via byval-indirect).

GCC, clang, and every other C compiler emit `byval` (or equivalent
attributes for other languages).  Binate is the outlier; native
backends are forced to match the outlier convention.

## Fix shape (codegen-side)

(See top-of-file note: the shipped emission is plain `ptr`, not
`ptr byval(<T>)`, for the LLVM-AArch64 reason described there.)

Emit `ptr byval(<T>) align 8` for any aggregate param > 16 bytes (the
threshold where SysV class becomes MEMORY).  Two co-located changes:

1. **Function declaration / definition emission**: the param type for an
   aggregate > 16 B becomes `ptr byval(<T>) align 8`.
2. **Call-site arg emission**: pass an alloca pointer with `byval`
   instead of a struct value.

For the receiving side, the LLVM IR `byval` param IS a pointer.  Binate's
IR generator materialises a per-param alloca, stores the param value into
it, then references the alloca through the body:

```llvm
%v5 = alloca %T
store %T %v2, %T* %v5
%v6 = getelementptr %T, %T* %v5, i32 0, i32 0  ; field access via %v5
```

With `byval`, `%v2` already IS the pointer.  The function-entry IR
must skip the alloca + store and use the byval pointer directly as
the field-access base.

## Native backend implications

### x64 (SysV)

Native side **already textbook-compatible** for >16-byte aggregates:

- `SplitAggregates=false` stays.
- `emitAggregateArg`'s MEMORY-class branch already lays out the struct
  on `rsp+stackOff`, matching what LLVM-byval emits.
- `spillIncomingParams`'s MEMORY-class branch already copies from the
  caller's outgoing-args area.

No native-x64 changes needed beyond the codegen flip.

### aa64 (AAPCS64)

Native side **must change** to match the new emission:

- LLVM-with-byval passes a pointer in the next free X reg (e.g. X2),
  not split fields across X2..X7 + stack.
- Native aggregate-arg path currently splits.  Must switch to "for
  aggregate > 16 B, pass alloca pointer in argReg".
- `spillIncomingParams` aggregate path currently receives
  split-fields-from-regs.  Must switch to "pointer arrives in the arg
  reg; spill it; subsequent field access GEPs from the pointer".
- `CallConv.SplitAggregates` for AAPCS64 → semantic flip.  Two options
  considered; shipped via a separate "indirect pointer pass" mode for
  >16 B aggregates (the IndirectLargeAggregates path), keeping the
  ≤16 B register-split behavior intact.

## Risk + rollback

The codegen change ripples through every aggregate-arg call site.  Any
miss (a forgotten emit path, an aggregate type predicate edge case)
causes runtime miscompiles, not compile-time errors.  Mitigations:

- Cross-pkg conformance tests (331, 337, 411) are the green-light.
- Aa64 must stay green; any regression there is a sign that the
  indirect-pointer-pass path missed a site.
- VM unit tests don't exercise the native ABI but verify IR-level
  correctness — must stay green.

If the change has to be reverted, the revert is mechanical (one big
diff).  No on-disk format / linker / build-system implications.

## Alternative considered: stay with band-aid

The non-byval LLVM emission is internally consistent (LLVM AArch64
splits, native AArch64 splits; LLVM x86_64 would split too if we made
native x64 match).  The textbook fix is correct but expensive.

Band-aid alternative: flip x64 `SplitAggregates=true`.  Native x64 then
matches LLVM's non-byval emission like aa64 does today.  ~20 LOC
including test updates.  Diverges from textbook SysV but would have
unblocked the 3 failing conformance tests immediately.

The mangler-collision entry in claude-todo is the precedent for "small
workaround + tracked root-fix".  This plan IS that root fix.
