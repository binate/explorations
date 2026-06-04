# Plan: eliminate memcpy/memset/memmove/memclr dependencies from generated code

**Status**: COMPLETE (shipped 2026-06-01) — kept for design rationale. Resolved
the claude-todo MAJOR entry "runtime/baremetal_arm32: missing `__aeabi_memcpy` /
`__aeabi_memmove` / `__aeabi_memset` / `__aeabi_memclr` aliases" via codegen
changes rather than runtime-side aliases. The aggregate `OP_LOAD` followup (which
the original plan marked deferrable) was also addressed for completeness — LLVM
aggregate-LOAD → memcpy lowering is size + LLVM-version dependent, so "currently
green" wasn't a robust guarantee.

## Goal

Generated Binate code must **not** depend on `memcpy`, `memset`, `memmove`,
or `memclr` (or their `__aeabi_*` flavors).  This is the C-free runtime
target spelled out in CLAUDE.md ("the *only* reason to ever use C is to
interface with existing C-based systems") — and the baremetal lane has
no libc, so any latent dependency surfaces there as `undefined symbol:
__aeabi_memcpy` at link time.

Concretely:

1. **No `@llvm.memcpy.*` / `@llvm.memset.*` / `@llvm.memmove.*` intrinsic
   emissions** from `pkg/binate/codegen`.
2. **No `@bn_pkg__libc__Memcpy` / `Memset` / `Memmove` / `Memclr` calls**
   in generated IR.
3. **No aggregate `store`/`load` operations** large enough that the LLVM
   backend lowers them to memcpy implicitly.  This is the subtle one — even
   if we never emit a memcpy call, an `store <%CharBuf> %val, ptr %slot`
   for a 20-byte struct under ARM EABI becomes `__aeabi_memcpy` at the
   backend.
4. **No memcpy/memset usage in the native backends' generated code**
   (separate audit; secondary to the LLVM-IR-side work).

The `runtime/baremetal_arm32/semihost.s` byte-loop helpers stay (they're
the bare-metal placeholders for the libc-host's `memcpy`/`memset` — they
exist to back symbols that **we** never emit), and `bn_pkg__libc__Memcpy`
stays in the libc-host runtime as a possible target for user-level
`pkg/libc` callers.  This plan is about what bnc *emits*, not what
hand-written C provides.

## Proposed replacement strategy

The unifying pattern across all sites is: **replace bulk-memory operations
with sequences of scalar memory operations whose sizes match individual
fields of the type being copied**.

For a struct `S { a int32; b @[]char; c int32 }` on a 32-bit target:

```llvm
; Aggregate copy (current):
%val = load %S, %S* %src
store %S %val, %S* %dst

; Per-field copy (proposed):
%dst.a = getelementptr %S, %S* %dst, i32 0, i32 0
%src.a = getelementptr %S, %S* %src, i32 0, i32 0
%a = load i32, i32* %src.a
store i32 %a, i32* %dst.a
; b: 4 i32 fields of %BnManagedSlice — unroll into 4 i32 loads + stores
%dst.b0 = getelementptr %S, %S* %dst, i32 0, i32 1, i32 0
%src.b0 = getelementptr %S, %S* %src, i32 0, i32 1, i32 0
%b0 = load i32, i32* %src.b0
store i32 %b0, i32* %dst.b0
; ... etc ...
; c:
%dst.c = getelementptr %S, %S* %dst, i32 0, i32 2
%src.c = getelementptr %S, %S* %src, i32 0, i32 2
%c = load i32, i32* %src.c
store i32 %c, i32* %dst.c
```

For typical small structs (≤ ~6 fields), the per-field unroll is also
the same shape LLVM would emit if it chose to inline a small memcpy.
So in the small-struct case we're just making the unroll explicit at
IR-gen time; perf is unchanged.

For larger structs (e.g., the 56-byte Operand struct that drove the
byval work), per-field unroll generates more IR but should still
compile to roughly the same machine code — LLVM is good at recognizing
the pattern and turning it back into a streaming copy when that's a win.

**Where bulk copy is genuinely better** (e.g., copying a 1 KB array),
we'd write a Binate-level byte-loop helper (`rt.CopyBytes(dst, src, n)`)
and emit a call to it.  But the threshold for that is well above any
struct size in current usage — start without one, add later if measured.

The codegen helpers are `emitFieldwiseCopy(out, dstPtr, srcPtr, T) → buf`
(walks `T`'s layout via `types.SizeOf` / `types.FieldOffset` /
`types.FieldType`, emits the per-field load/store sequence) and a sibling
`emitFieldwiseZero(out, dstPtr, T) → buf` (per-field zero-stores, no
source pointer) for zero-init.

The aggregate-store walker handles named-struct (packed `<{ }>` with
`[N x i8]` padding) vs anonymous tuple (non-packed `{ }`) correctly. The
`fc`/`fz` SSA prefix split avoided idTag collisions between alloca-zero
and byval-spill on the same slot.

## Risks and considerations

1. **IR size**: every aggregate copy expands into N scalar ops.  For
   tests + selfhost compilation, expect ~5-10% IR-size increase.  LLVM
   constant-folds and DCE's much of it, but `.ll` files (which we keep
   in tests as goldens) will grow.

2. **Native-backend ABI matching**: the LLVM and native backends agree
   on aggregate ABI today.  If the LLVM side switches to per-field
   stores while the native side still does bulk aggregate moves, do
   they still produce the same final memory image?  Yes — both end up
   writing the same bytes in the same order — but we need explicit
   test coverage at the LLVM↔native boundary to confirm.

3. **Padding bytes**: aggregate stores write padding bytes as
   undef-or-zero (per LLVM semantics).  Per-field stores leave padding
   undefined.  For most consumers this is fine, but the refcount-aware
   `__copy_<T>` helpers walk a struct field by field via the type
   table; padding never matters because the helpers never touch it.
   Still — flag as a thing to verify when implementing.

4. **Volatile / atomic store ordering**: aggregate stores are
   guaranteed atomic-w.r.t.-other-aggregate-stores (no observer can
   see a half-updated struct).  Per-field stores break that guarantee.
   We don't have user-visible threading yet, so this is fine for now;
   document the assumption.

5. **Codegen test goldens**: every `*_test.bn` that pins an expected IR
   snippet for a struct copy will need a refresh.  Plan a single pass
   to regenerate goldens after the codegen change, with manual review
   of one representative golden per shape (struct of scalars / struct
   of slices / nested struct / array of struct).

## Out of scope (followups, not blockers)

- Variadic / heap-array bulk copies that don't have static sizes.  When
  these appear (currently none in the codebase), they'll need a
  Binate-level `rt.CopyBytes` helper — but write it then, not now.
- `pkg/libc` itself.  This package wraps libc-host's memcpy/memset for
  user code that explicitly wants to call them on platforms with libc.
  This plan does not change `pkg/libc`; it only changes what bnc
  *emits* unprompted.
