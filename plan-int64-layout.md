# Plan: int64 type layout

Status: planned 2026-10-13 (work-1).  The remaining piece of claude-todo.md's CRITICAL "The 32-bit-host unit
lane ... the compiler may use the HOST's `int` width for target values".  User decision (2026-10-13): "int64
layout seems right" — over the alternative, a checker error for any type the host's `int` cannot size.

## Problem

pkg/binate/types computes layout in the host's `int`: `ArrayLen`, `SizeOf()`, `FieldOffset()`,
`StructLayout()`'s `FieldLayout.Offset` / `Size`, the memoized `layoutSize`.  So:
- A compiler hosted on a 32-bit machine (ILP32) compiling for a 64-bit target silently wraps the size of
  any type of 2 GB or more — `TestLayoutRepeatedStructDag` (8 * 4^20 bytes) on the builder-comp_arm32_linux
  unit lane — and every offset, copy length and data size derived from it.  It also cannot declare such a
  type at all where an array length is involved: the checker caps a length at the host's `int` ("array
  length is too large", eval_const_int.bn).
- On ANY host, nothing rejects a type whose size overflows `int`: `[1 << 40][1 << 40]byte` wraps on a
  64-bit host too.
- Related: `irdata.DataTerm.Addend` is `int`, so a data pointer's offset past 2^31 cannot be carried by a
  32-bit-hosted compiler.

## Goal

A compiler on any host lays out every type of its target exactly; a type too large for the target (or for
int64) is a checker error, never a silent wrap.

## Design

Revised 2026-10-14 (user: "You can do the new int64 API, but get that idea reviewed"): a new int64 layout API
alongside the existing `int` one, so each call site moves once, in its layer's stage — instead of stage 1 giving
every caller an explicit narrowing that stages 2-5 then rewrite.

types (stage 1):
- Layout is computed in int64: `ArrayLen` int64, `layoutSize` int64, the internal sizeOf / fieldOffset /
  structLayoutOf / structLayout int64.  `AlignOf()` stays `int` (a small power of two).
- The layout arithmetic (an array's length times its element size; a struct's running offset, padding and
  field sizes) is overflow-checked: a size past int64 is `SIZE_TOO_LARGE` (-1), and an aggregate with such a
  part is too large itself.
- New accessors: `t.Size() int64` (`SIZE_TOO_LARGE` for a type too large for int64) and `t.Offset(index)
  int64`; `FieldLayout.Offset` / `Size` / `PaddingBefore` become int64 (9 non-test callers, converted in stage 1);
  `MakeArrayType(elem, n int64)`.
- The existing `SizeOf() int`, `FieldOffset(i) int` and `TrailingPadding() int` stay during the transition and
  return the int64 value narrowed to the host `int`, panicking ("internal error: … does not fit the host int")
  when it does not fit (the sentinel included).  On a 64-bit host every type the checker accepts fits, so
  behaviour there is unchanged; a 32-bit host fails loud, never silently, until its caller's layer moves.
- `types.HostInt(v int64) int`: the explicit narrowing (same loud failure), for `ArrayLen` readers outside
  types / check in stage 1, and later for the bounded uses (frame offsets, encodable immediates, VM memory).

check (stage 1):
- An array length must fit the TARGET's `int` (`len` of an array is an `int`), not the host's.
- "type is too large": a type whose size exceeds the target's `int` maximum (the largest object the target can
  index; int64's for a 64-bit target), or overflows int64, is an error where a type is formed from source — an
  array type, a struct type once its fields are resolved, a generic instance's types (resolved on its checked
  copy).  So every type that reaches layout from a checked program fits; the sentinel reaching a backend is a
  compiler bug.
- `sizeof` / `alignof` folding uses `Size()` (bignum.FromInt64), not the host-int `SizeOf()`.

## Stages (each a commit that keeps everything green)

1. types + check as above; `ArrayLen` readers elsewhere narrow through `HostInt`.  TestLayoutRepeatedStructDag
   uses `Size()` and passes on the 32-bit lane.  Tests: an oversized type rejected on every host (array,
   struct, nested, generic instance); a 2^43-byte struct laid out on any host; the narrowing accessors fail loud.
2. ir / irgen / irbuild / irdata / iropt move to `Size()` / `Offset()`: sizeof / alignof constants, field and
   element offsets (GEP), copy and zero-fill sizes, `DataZero` and `DataTerm.Addend` int64.
3. LLVM codegen: sizes and offsets in the emitted IR (allocas, GEPs, memcpy / memset / memmove, data).
4. Native backends (common, callconv, fold, aarch64, x64, arm32): data sizes, copy and zero-fill lengths and
   loop counts, offsets past 32 bits (materialized with the 64-bit constant paths); a frame stays bounded
   (`HostInt` with a loud error).
5. VM / interp: VM memory is host memory, so its layout narrows at allocation (`HostInt`), failing loud.
6. Remove `SizeOf()` / `FieldOffset()` / `TrailingPadding()`'s int forms (any caller left moves to the int64 API
   or an explicit `HostInt`); end to end on the 32-bit lane: a program with a > 2 GB type for a 64-bit target
   compiles through both backends (compile-only, LLVM IR / object inspection).

Call sites today (non-test): native ~235 (aarch64 65, arm32 63, x64 55, common 30, callconv 14, fold 8),
codegen 60, types 55, vm 32, irgen 30, check 22, interp 12, ir 8, others ~15.
