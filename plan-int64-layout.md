# Plan: int64 type layout

Status: planned 2026-10-13 (work-1); stage 1 landed 2026-10-15 (binate `56c30e545`, docs `dc6444c`).  The remaining piece of claude-todo.md's CRITICAL "The 32-bit-host unit
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
alongside the existing `int` one, so each call site moves once, in its layer's stage.  Then revised after the
design review (2026-10-14), with two user decisions: the largest type is the target's `int` maximum ("Target int
max"), and a too-large aggregate the compiler builds is a checker error ("Checker error").

Spec (docs, stage 1):
- §7.13, a new rule (`type.layout.max-size`): a type's size shall not exceed the target `int`'s maximum
  (2^31-1 bytes on a 32-bit target, 2^63-1 on a 64-bit one) — consistent with `len` returning `int` and with C
  compilers' PTRDIFF_MAX limit; forming a larger type is a compile-time error.
- §7.5 `type.array.form`: the length fits the target's `int` (it is what `len` returns).
- §21.4 / `conf.implementation.limits`: the combined size of a function's results, and of a closure's captures,
  must fit the same limit (the implementation builds an aggregate of them); a function's frame too.

types (stage 1):
- Layout is computed in int64 (`ArrayLen`, `layoutSize`, the internal sizeOf / fieldOffset / structLayoutOf /
  structLayout).  `AlignOf()` stays `int`.  The internal recursion calls the internal int64 functions, never the
  public accessors (which would narrow or fail partway through a large type).
- Every add, multiply and align-up goes through helpers that saturate to an internal "too large" sentinel and
  pass it through, with the checks written without int64 division (`a > LIMIT - b`, `n > LIMIT / size` with
  `size` the element size — an int64 division is a libgcc call on an arm32 host).  The limit is the target's
  object-size maximum (the target `int`'s), not int64's, so a built aggregate past it is caught too.
- Public int64 accessors `Size()`, `Offset(i)`, and `StructLayout()`'s `FieldLayout` fields (int64) panic
  ("internal error: type too large …") on a too-large type — never return the sentinel (callers compare sizes
  with `<= 0`, `> 16` and would read -1 as small or empty).  A non-panicking `SizeIfFits(t) (int64, bool)`
  for the checker, constval's sizeof folding (not-fits: an error, not a value), and the checker's ABI / cast
  / C-interop queries.
- The transitional `SizeOf() int`, `FieldOffset(i) int`, `TrailingPadding() int` fail loud unless the WHOLE
  type fits the host's `int` (for a field query, the containing struct), so a caller's sum of parts of one
  object cannot wrap silently; `ArrayLenHost()` likewise checks the whole array.  `HostInt(v int64)` only for
  values that are host memory (the VM).
- `ast.TypeExpr.LenVal` and `ast.Element.KeyVal` become int64 in stage 1 (the checker stamps them).
- Shared records (FieldLayout, irdata.DataTerm / TypeInfoDesc / FieldInfoDesc, LenVal) force their consumers
  in later layers to change in the stage that changes the record; "each call site moves once" holds for the
  accessor methods.

check (stage 1):
- An array length must fit the TARGET's `int` (evalArrayLen; composite-literal keys and `[...]` inferred lengths
  too — int64, not `constHostInt`).
- checkArrayLit's duplicate-index set is bounded by the element count, not the length (today it allocates a
  byte per element: claude-todo.md "The checker allocates a byte per element").
- "type is too large" (SizeIfFits) where a value's type is required after collection — requireSizedType /
  checkValueEmbedding (vars, fields, params, results, composite literals, make, generic populate), sizeof /
  alignof operands, and a pointee whose size is used (unsafe_index strides by it); not at a type's first
  resolution, where a forward-declared named type is still a placeholder.  A generic instance's types are
  resolved on its checked copy, so substitution is covered there.
- The combined size of a function's results and of a closure's captures (the aggregates IR-gen builds).

Later layers: `types/abi` (the ABI classification, used by check, codegen and native) and mangling (an array
length is part of a mangled name: WriteInt64) move in stage 2; irdata's type-info records too.  Frames and data
sections accumulate with overflow checks bounded by the TARGET, reported as a compile error (not `HostInt`,
which would make acceptance depend on the host).  Native data emission writes zero-fill into host memory
(`asm.Fill`, `Section.Len int`), so a > 2 GB global on a 32-bit host needs asm / elf / macho / link to carry
bss sizes without materializing them — part of stage 4, needed by stage 6's end-to-end test.

## Stages (each a commit that keeps everything green)

1. Spec + types + check as above.  TestLayoutRepeatedStructDag uses `Size()` and passes on the 32-bit lane.
   Tests: an oversized type rejected on every host (sizes past int64), and at the 2^31 boundary for a 32-bit
   target; a 2^43-byte struct laid out on any host; results / captures too large; `[1 << 40]int8{}` checks in
   bounded memory.  (A panic is not unit-testable — `TestResult` only — so "fails loud" is tested through the
   non-panicking predicates.)
2. ir / irgen / irbuild / irdata / iropt / irutil / mangle / types/abi move to the int64 API: sizeof / alignof
   constants, field and element offsets (GEP), copy and zero-fill sizes, `DataZero` and `DataTerm.Addend`,
   type-info records.  Status: DONE `addbb15a2` (2026-10-18).  Also int64: DataTerm.Width, BuildGlobalVar's size,
   abi.ResultImageSize / AggregateReturnSize, ir.Instr.RetbufSize and the descriptors' RetbufSize (a large
   result is legal for a 64-bit target compiled on a 32-bit host); consumers that write host memory narrow
   with HostInt (native's materialized zero-fill until stage 4, the VM, the interpreter).  types/abi's
   register classifiers narrow through boundedInt (fails loud past 64) on aggregates already bounded to 16
   bytes; the HFA fold caps member counts at 5 (an exact count could wrap on a 32-bit host).
3. LLVM codegen: sizes and offsets in the emitted IR (allocas, GEPs, memcpy / memset / memmove, data).  Status:
   DONE `651ea270e` (2026-10-18).  Also: TrailingPadding() returns int64 (codegen is its only user); the per-leaf
   copy / zero / SSA load and store recursions check that the aggregate fits their i32 GEP offsets
   (checkLeafwiseSize, an internal error past it) and skip arrays of zero-size elements; debug info leaves out a
   size past 2^60 bytes (a struct that large is a declaration); an array of zero-size elements is `[0 x …]` in LLVM
   types.
4. Native backends (common, callconv, fold, aarch64, x64, arm32) and asm / elf / macho / link: data sizes,
   copy and zero-fill lengths and loop counts, offsets past 32 bits, bss without materialized zero bytes; a
   frame past the target's limit is a compile error.
5. VM / interp: VM memory is host memory, so its layout narrows at allocation (`HostInt`), failing loud.
6. Remove the int forms of `SizeOf()` / `FieldOffset()` / `TrailingPadding()` / `ArrayLenHost()`; end to end
   on the 32-bit lane: a program with a > 2 GB type for a 64-bit target compiles through both backends.

Call sites today (non-test): native ~235 (aarch64 65, arm32 63, x64 55, common 30, callconv 14, fold 8),
codegen 60, types 55, vm 32, irgen 30, check 22, interp 12, ir 8, others ~15.
