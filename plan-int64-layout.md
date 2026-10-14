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

- types: `ArrayLen` int64; `SizeOf()` and `FieldOffset()` return int64; `FieldLayout.Offset` / `Size` int64;
  `layoutSize` int64.  `AlignOf()` stays `int` (an alignment is a small power of two).  The layout
  arithmetic (an array's length times its element size; a struct's running offset plus padding) is
  overflow-checked.
- check: an array length must fit the TARGET's `int` (`len` of it is an `int`), not the host's; a type's
  size must fit the target's `int` too (the largest object the target can index) — else "type is too large"
  at the type's declaration / use.  Layout reached with an oversized type after that is a compiler bug
  (internal error, fail loud).
- Consumers either carry int64 through, or narrow explicitly where a value is bounded by construction (a
  frame offset, an encodable immediate, VM memory, which is host memory) — with a loud error, never a
  silent truncation, when the bound does not hold.

## Stages (each a commit that keeps everything green)

1. types + check: the int64 API and the overflow-checked arithmetic; the checker's target-width limits and
   "type is too large"; every existing caller compiles through an explicit narrowing that fails loud when a
   value does not fit the host's `int` (so 64-bit hosts behave exactly as before, and a 32-bit host fails
   loud, not silently, until its layer is converted).  TestLayoutRepeatedStructDag passes on the 32-bit
   lane.  Tests: oversized types rejected on every host; a 2^43-byte struct laid out on any host.
2. ir / irgen / irbuild / irdata / iropt: sizeof / alignof constants, field and element offsets (GEP),
   copy and zero-fill sizes, `DataZero` and `DataTerm.Addend` int64.
3. LLVM codegen: sizes and offsets in the emitted IR (allocas, GEPs, memcpy / memset / memmove, data).
4. Native backends (common, callconv, fold, aarch64, x64, arm32): data sizes, copy and zero-fill lengths
   and loop counts, offsets past 32 bits (materialized with the 64-bit constant paths); a frame stays
   bounded (a narrowing with a loud error).
5. VM / interp: VM memory is host memory, so its layout narrows at allocation, failing loud.
6. End to end: on the 32-bit lane, a program with a > 2 GB type for a 64-bit target compiles through both
   backends (compile-only, LLVM IR / object inspection).

Call sites today (non-test): native ~235 (aarch64 65, arm32 63, x64 55, common 30, callconv 14, fold 8),
codegen 60, types 55, vm 32, irgen 30, check 22, interp 12, ir 8, others ~15.
