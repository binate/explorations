# Plan: eliminate memcpy/memset/memmove/memclr dependencies from generated code

**Status**: draft / not started
**Tracks**: claude-todo MAJOR entry "runtime/baremetal_arm32: missing `__aeabi_memcpy` / `__aeabi_memmove` / `__aeabi_memset` / `__aeabi_memclr` aliases"
**Blocks**: pkg/binate/buf-using tests on `builder-comp_arm32_baremetal` (pkg/builtins/lang unit-tests + conformance 064)

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

## Current emission sites

Survey by `grep -rn "llvm\.mem\|libc__Mem" pkg/binate/codegen/`:

### Site A — `emit_instr.bn:129-146` — `OP_STORE` byval-param spill

When a >16-byte struct param arrives via the `byval` calling convention
(landed in commit `f5340fac` as part of plan-codegen-byval), the callee
emits a slot alloca and copies the caller-provided buffer into it.
Current shape:

```llvm
%v.slot = alloca %T
call void @llvm.memcpy.p0.p0.i64(ptr %v.slot, ptr %v.byvalParam, i64 sizeof(T), i1 false)
```

**Replacement**: per-field GEP + scalar store.  We have `types.SizeOf` /
`FieldOffset` already.  Emit one `load <scalar>` + `store <scalar>` pair
per field.  For nested aggregates, recurse.

### Site B — `emit_strings.bn:55-65` — `OP_RODATA_CHARS_COPY`

The "copy from rodata into a stack array" path (for `var s [N]char = "hi"`-
style decls).  Emits:

```llvm
call void @bn_pkg__libc__Memcpy(i8* %v.dst, i8* %v.src, i32 strLen)
```

**Replacement**: emit an inline loop in LLVM IR over `i8` (or `i32` for
4-byte-aligned chunks).  Or, since rodata-array sizes are known at
compile time, fully unroll into `strLen` individual `store i8` ops.
Unrolling is simpler and likely faster for the typical short-string case.

### Site C — `emit_strings.bn:160-170` — `OP_RODATA_MSLICE_COPY`

The "make a fresh `@[]char` from a string literal" path.  Same shape as
Site B; same replacement strategy.  This is the much more common path
(every `"foo"` literal goes through here).

### Site D — `emit_const_nil.bn:13-31` — zero-init of `%BnSlice` / `%BnManagedSlice`

```llvm
store %BnSlice zeroinitializer, %BnSlice* %v
store %BnManagedSlice zeroinitializer, %BnManagedSlice* %v
```

These are aggregate stores.  `%BnSlice` is 8 bytes (`{i8*, i32}`), small
enough that LLVM inlines.  `%BnManagedSlice` is 16 bytes — sometimes
inlined, sometimes lowered to memset.  Even when inlined to 4 scalar
stores, the ARM backend may pick `__aeabi_memclr8` for the all-zero case
under some `-O` settings.

**Replacement**: emit per-field `store i32 0` / `store ptr null` sequences
explicitly.  No aggregate store, no zeroinitializer.

### Site E — implicit LLVM lowering of `store <Aggregate>` / `load <Aggregate>`

The actual root cause of the current baremetal link failure.  In
`emit_instr.bn:148-157`, the "plain" `OP_STORE` path emits:

```llvm
store <T> <val>, <T>* <ptr>
```

For `<T>` larger than a few words (CharBuf = 20 bytes), the LLVM backend
lowers this to `@llvm.memcpy` → `__aeabi_memcpy`.

**Replacement**: at the codegen level, when `<T>` is an aggregate
(struct / array / fixed-size composite), emit per-field stores via GEP
instead of the aggregate store.  Mirror change for aggregate loads.

This is the central piece of the plan.

### Site F — native backends (pkg/binate/native/x64, /aarch64)

The native backends do their own ABI lowering, but the
IndirectLargeAggregates path (landed in `f5340fac`) and
`_call_shim_aggregate` (used for closure captures) currently copy via
runtime helpers.  Need to audit each call site and replace the byte-loop
runtime helpers with inline scalar-store sequences.

This is mostly orthogonal to A-E (different IR-gen layer) and can be a
separate step.

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

### Types module support needed

A new codegen helper: `emitFieldwiseCopy(out, dstPtr, srcPtr, T) → buf`
that walks `T`'s layout (via `types.SizeOf` / `types.FieldOffset` /
`types.FieldType`) and emits the per-field load/store sequence.  Lives
in `pkg/binate/codegen/emit_util.bn` or a new `emit_copy.bn`.

For zero-init (Site D), a sibling `emitFieldwiseZero(out, dstPtr, T) → buf`
that walks `T`'s layout and emits per-field zero-stores.  Same shape,
no source pointer.

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

## Sequencing

The order is the natural difficulty gradient — each step lands
independently, doesn't regress the prior, and the test suite stays
green throughout.

1. **Add `emitFieldwiseCopy` + `emitFieldwiseZero` helpers** in a new
   `pkg/binate/codegen/emit_copy.bn`.  Unit-test directly (build a
   synthetic IR-gen invocation, dump the LLVM text, assert on the
   expected scalar-store sequence).  No callers yet — pure plumbing.

2. **Site A**: replace `@llvm.memcpy` in `OP_STORE` byval-param branch
   with `emitFieldwiseCopy`.  Only path that's been in the tree < 1 wk;
   smallest blast radius.  Run unit + conformance suites in all modes.

3. **Site D**: replace `store %BnSlice zeroinitializer` / `store
   %BnManagedSlice zeroinitializer` with `emitFieldwiseZero`.  All
   four scalar stores per `%BnSlice` (2 fields) / `%BnManagedSlice` (4
   fields).  Refresh codegen goldens.

4. **Sites B + C** (rodata copies): replace `@bn_pkg__libc__Memcpy` with
   unrolled per-byte stores (length is known at compile time, so
   unrolling is trivial — no loop needed).  Drop the
   `@bn_pkg__libc__Memcpy` declaration from `emit.bn` once both call
   sites are gone.

5. **Site E** (the actual blocker): change the generic `OP_STORE` /
   `OP_LOAD` paths so that aggregate-typed operations route through
   `emitFieldwiseCopy` / a matching `emitFieldwiseLoadInto`.  Largest
   blast radius — every struct copy in the codebase changes shape.

6. **Site F** (native backends): audit `pkg/binate/native/{x64,aarch64}`
   for direct memcpy/memset emission in the indirect-large aggregate
   and closure-capture paths.  Replace with inline scalar stores or
   call into the same Binate-level helpers used by Site B/C.

7. **Drop unused declarations**: `@llvm.memcpy.p0.p0.i64` and
   `@bn_pkg__libc__Memcpy` from `emit.bn`.  Drop the
   `__aeabi_memcpy`/`memcpy` placeholders from
   `runtime/baremetal_arm32/semihost.s` (keep `memcmp` — that's
   user-callable via `pkg/libc`).

8. **Verification**: with steps 1-7 landed, the
   `runtime/baremetal_arm32` lane should run `pkg/binate/buf`-using
   tests cleanly.  Bring `pkg/builtins/lang` and conformance `064` to
   green; remove their de-facto-xfail status.

Each step is one commit.  Steps 1-4 should be 1-2 days of work; step 5
is 2-3 days; step 6 is independent and can interleave with the
remaining language-feature work; step 7-8 are cleanup.

## Out of scope (followups, not blockers)

- Variadic / heap-array bulk copies that don't have static sizes.  When
  these appear (currently none in the codebase), they'll need a
  Binate-level `rt.CopyBytes` helper — but write it then, not now.
- `pkg/libc` itself.  This package wraps libc-host's memcpy/memset for
  user code that explicitly wants to call them on platforms with libc.
  This plan does not change `pkg/libc`; it only changes what bnc
  *emits* unprompted.
