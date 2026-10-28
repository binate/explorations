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
   Recon (2026-10-18, a 64-bit host, main at stage 3): 228 sites (aarch64 63, arm32 60, x64 53, common 29,
   callconv 15, fold 8), most of them size comparisons.  A 20 MB / 3 GB local and a global with a field past
   that offset: native aa64 fails to compile both sizes ("ADD/SUB immediate out of range (0..0xFFFFFF)" — the
   open claude-todo entry "Native aa64 rejects a struct field at an offset of 16 MiB or more", which a frame
   past 16 MiB hits too); native x64 compiles and runs 20 MB and fails loud at 3 GB (disp32 / imm32); native
   arm32 compiles 20 MB, and its zero global goes to `.data` as 20 MB of zero bytes (the 16 MiB bare-metal RAM
   region then stops the link).  LLVM compiles all of them.
   Scope found while starting (2026-10-18): x64 SysV and arm32 pass a large aggregate by value on the stack, so
   the call-argument stack layout (callconv ArgWords 152 uses, CallArgStackOff 124, EffectiveArgWords 34,
   CallStackBytes 36) carries argument sizes too, besides RegMap's frame tables (~110 backend sites).  User
   decision (2026-10-18): "Full int64 (as planned)" — frames and the call-argument stack layout carry int64
   everywhere (chosen over a uniform 2 GB native limit, and over int64 frames with stack arguments capped at 2
   GB).  Commits:
   - 4a. DONE `73d8d968f` (2026-10-18): native/common, callconv, fold compare sizes with Size().
   - 4b. DONE `845a85f52` (2026-10-18): aarch64: an add / sub of an offset past the 0..0xFFFFFF immediate range
     is materialized in the X17 scratch (the 16 MiB entry, fields and frames).  A 3 GB global still fails on
     native aa64 with no specific error (likely the object writer; 4f).
   - 4c (in progress, 2026-10-27, work-1). callconv's stack layout in int64 (argument words, stack offsets, stack bytes) and each backend's
     stack-argument paths; a large by-value argument is copied with a loop, not word by word.  Includes (found in
     the 4e-2 review): native x64's closure shims narrow a capture's closure-struct offset with HostInt; a capture
     past 2 GiB follows more than 2 GiB of stack-passed captures, and a register-passed capture after them loads
     at Mem(R11 / RDI, offset), which the assembler rejects past 2^31 -- the shims need the far paths too.
     Also (4e-3 review): callconv's argWords uses SizeOf(), and argRegWordsStackWords computes ArgWords before its
     indirect-large check, so on a 32-bit host CallArgRegStart / CallStackBytes panic on any argument, parameter or
     capture past 2 GiB, even an indirect-large one passed as a pointer.  Once they take int64, add the aarch64
     closure-capture test held back from 4e-3: two Big captures, the second's ADD (and an int64 capture after it,
     at 2^31 + 8) through X17 -- it panics on CI's 32-bit-host unit lane (builder-comp_arm32_linux) until then.
     Split (2026-10-27, from a survey: in the native backends ArgWords ~87 uses, CallArgStackOff ~60,
     CallArgRegStart ~50, CallStackBytes ~18, EffectiveArgWords ~16; also codegen's checkStackArgs and native/common's
     param slots), as 4d was:
     - 4c-1. DONE `e88c25dba` (2026-10-28): callconv computes from Size() in int64: argWords (rounded without an
       overflowing add), the stack words, CallArgStackOff / CallStackBytes and their V / VFP forms, an indirect-
       large argument's one word decided before any size-derived word count; the walkers' adds are checked
       (stackAdd, fail-loud past the int64 range).  int64 forms (ArgWords64, CallArgStackOff64, CallStackBytes64,
       CallArgStackOffV64, CallStackBytesV64) beside the int ones, which narrow fail-loud (HostInt); codegen's
       checkStackArgs and native/common's param slots / PlanFrame use the int64 forms.  cmd/bnc objects
       byte-identical on all three native targets.
     - 4c-2 / 4c-3 / 4c-4. x64 / aarch64 / arm32 stack-argument paths on the int64 forms: outgoing stores and
       incoming reads past a displacement (x64 far paths, aarch64 X17, arm32 its word), a large by-value argument
       copied with a loop, closure-shim capture offsets (x64's HostInt gone), the aarch64 capture test above.
     - 4c-5. The int forms go; the int64 ones take their names.
   - 4d. RegMap's frame tables in int64 (PlanFrame bounded by the target's int, a compile error past it) and each
     backend's slot addressing, materializing offsets past an instruction's reach (aarch64 past what MemImm
     takes on the host, x64 past disp32; arm32 is a 32-bit target, so narrowing is exact).
   - 4e DONE (2026-10-27). Field / element offsets and copy lengths per backend (ElemSizeOf,
     FieldByteOffset, aggregate copies).
     Split (2026-10-26, from a survey: ~225 int-layout uses in native/, most in the backends): on a 32-bit host
     SizeOf() / FieldOffset() / ArrayLenHost() panic (HostInt) on a type past 2^31 even where only a check
     (`SizeOf() <= 0`, register classification) reaches them, so each backend moves to Size() / Offset() /
     int64 array lengths throughout, narrowing explicitly only where the ABI bounds the value (register-returned
     aggregates, tuples); copy and zero lengths, element sizes and field offsets thread int64 into the copy
     helpers (aggcopy pieces stay small: tails relative to an int64 base).
     - 4e-1. DONE `87830f726` (2026-10-26): native/common's ElemSizeOf and FieldByteOffset return int64 (backends
       narrow at the call with types.HostInt, fail-loud, until their step).
     - 4e-2. DONE `650f83fc5` (2026-10-26): native x64 reads Size() / Offset() / ArrayLen throughout.  The copy
       helpers take int64 sizes; x64_frame_far.bn's far paths serve every base (a field / element / copy past 2^31
       through a pointer or a global goes through a register loaded with base + offset); immNear decides an
       immediate, and an element size or a runtime call's size past imm32 is a 64-bit constant (emitMulConstX64,
       Imm64); the overlap-safe copy compares a size past imm32 through the red zone's bottom slot; a zero-fill's
       tail past a displacement goes through a saved scratch.  Values the register ABI bounds narrow with a cast;
       closure-shim capture offsets narrow with HostInt (left for 4c).  native x64 objects of cmd/bnc are
       byte-identical; conformance 1718 (compile-only: a field past 2^31, an element > 2 GiB, a 2 GiB + 19 copy,
       make / make_slice) passes on every 64-bit mode and is a "type is too large" error on the arm32 modes; the
       native x64 subset passes with -fx64-far-disp=16.
     - 4e-3. DONE `b771afffa` (2026-10-27): native aarch64 reads Size() / Offset() / ArrayLen throughout; the asm
       layer already took int64 (X17 past an immediate), so only types widened (cmd/bnc objects byte-identical).
       Closure capture offsets are int64 (an indirect-large capture is passed by pointer, so a later capture sits
       past 2^31 with nothing on the stack).  Fixed with it (review): the packed func-value shim's int sum of its
       frame copies wrapped on a 32-bit host (two 1.5 GiB arguments made SUB SP an ADD SP, the copies overwriting
       the caller's frame; three 1 GiB ones skipped one copy) -- now int64 offsets / frame size; a single
       aggregate return read ArgWords before its sret copy.  native/common's copy-stats tally is int64.
     - 4e-4. DONE `3beb3cc38` (2026-10-27): native arm32 reads Size() / Offset() / ArrayLen through checked int
       helpers (sizeArm32 / offsetArm32 / arrayLenArm32 / layoutIntArm32): no arm32 type passes 2^31 - 1 (spec
       type.layout.max-size; types' Size() fails loud on one), so the narrowing is exact on any host; frame offsets
       stay int64.  cmd/bnc objects byte-identical.  After it only callconv's argWords reads the int SizeOf() in the
       native backends (4c).  Found by its review: a variadic call's backing array is not size-checked (claude-todo).
   - 4f. asm / elf / macho / link: native backends put zero-initialized globals in bss, which records its size
     without materializing bytes (an int64 size on a 32-bit host); check crt0 / the linker scripts clear bss.
     4f-1 DONE `25ffa73f0` (2026-10-18, done before 4c-4e: it fixed a 64-bit-host failure): every native package
     global goes to bss (its storage is all zero; the initializer runs at start-up), and a zero-fill section
     holds no bytes in the assembler (Section.ZeroFill, countZeroFill: fails loud past the host's int or a
     32-bit target's 4 GiB).  A 3 GB global compiles on native aa64.  crt0 clears no bss — QEMU's ELF loader
     zero-fills it, as for the LLVM backend's bss.  4f-2 (open): bss lengths, symbol offsets and local-common
     sizes in int64 through asm / elf / macho / link, for a 32-bit host.
   Order (revised 2026-10-18, after reading x64's helpers): 4d before 4e, so each backend helper's offset
   parameter moves to int64 once (4e's field offsets flow into the same helpers as the frame offsets):
   - 4d-1. DONE `8d5ed266a` (2026-10-19): native/common: RegMap's frame tables and frame fields int64; PlanFrame
     accumulates in int64 (saturating) and a frame past the target's address space — 2^32 - 1 on a 32-bit target,
     the int64 range on a 64-bit one — is a compile error naming the function (CheckFrameFits).  The bound was
     first the target's int, which the review found rejects a legal program (one local of nearly 2^31 bytes on
     arm32); user decision (2026-10-18): "Address space", catalogued in spec §21.4 (docs `1501abe`, "modes must
     agree").  The int lookups (LookupAlloc, LookupSpill, NeedAlloc, NeedSpill, LookupArgCopy, LookupAllocSize)
     stay, narrowing fail-loud (FrameInt), beside int64 variants; native/common's int64 lists are a non-generic
     int64List (the pinned BUILDER conflates vec.Vec[int64] with vec.Vec[int]).
   - DONE `1246f0e68`, `3279fc9ac` (2026-10-19): the LLVM backend rejects the same frames.  Every define carries
     "warn-stack-size" at the LLVM frame limit and bnc compiles the IR with -Werror=frame-larger-than, so LLVM's
     own measure of the frame (after its optimizations) is checked: on a 32-bit target the limit is
     types.FrameLimit (an arm32 frame past 4 GiB wrapped: two 2 GiB locals → `sub sp, sp, #16`); on x86-64 it
     is 2^31 - 1, the largest frame LLVM addresses there (user decision 2026-10-19, "Reject past 2 GiB"; spec
     §21.4 notes it, docs `11beb8f`); aarch64 is unchecked.  conformance 1710 covers every backend (run.sh's new
     `.error.<mode>.<arch>` tier for LLVM x86-64).  The VM must agree too (stage 5).
   - Note for 4d-2..4: FrameInt guards the frame values, not sums the backends compute from them in host int
     (`frameSize + 16`, `SavedRegsOff + 8 * i`, `LookupAlloc(...) + fieldOff`); those sums move to int64 too.
   - 4d-2. DONE `74f8e652e` (2026-10-24): native x64 takes int64 frame offsets (LookupAlloc64 / LookupSpill64 /
     NeedAlloc64 / NeedSpill64 / LookupAllocSize64, SretSlotOff / SavedRegsOff) and addresses every frame slot
     through x64_frame_far.bn: an access whose displacements pass disp32 goes through a register loaded with
     base + offset (MOV imm64; LEA, flags intact); a load or LEA computes into its own destination; a store, an
     XMM load or a copy loop saves a scratch in the red zone's bottom slots ([RSP-128], [RSP-120]) and restores
     it (RSP never moves; emitMultiReturnPack fails loud if its image would reach them); the prologue's
     `sub rsp` past imm32 goes through R11; incoming stack arguments are read off RBP + 16.  Test option
     `bnc -fx64-far-disp=N` (native.Options.X64FarDisp): the native x64 arg subset passes with N = 16, and a bnc
     built that way by native x64 emits the same IR and code as gen1.  1710 / 1711 compile on native x64.  The
     far mode runs in no CI mode (wiring one is the user's call).  Left for 4c: OUTGOING stack arguments past
     2 GiB (a caller of 1711's f) still fail to compile on native x64 -- the assembler rejects the displacement
     of the argument-area stores (x64_call*.bn, the shims).
   - 4d-3. DONE `b108193b0` (2026-10-25): native aarch64 takes the int64 lookups and fields and passes int64
     offsets to the assembler: MemImm64; LDR/STR (word, sub-word, sign-extending, FP) range-check the int64 and
     form one out of range in X17; LDP/STP and the pre-/post-indexed / unscaled forms reject one past the host's
     int; Fstr_d / Fldr_d / Fstr_s / Fldr_s take int64.  A load into X17 past the immediate field is allowed (an
     indirect call's target reload past a 32 KiB frame failed to compile).  The frame zero-fill takes an int64
     size on both native backends (a >= 2 GiB zero-initialized region failed in FrameInt on a 32-bit host).
     Coverage on a 32-bit host comes from the builder-comp_arm32_linux unit-test lane (it runs asm/aarch64,
     native/aarch64 and native/x64).  On a 64-bit host the code is unchanged (byte-identical cmd/bnc objects).
   - 4d-4. DONE `f2264bbe4` (2026-10-25): native arm32 takes the int64 lookups; its frame / base memory helpers,
     VFP base load / store, tuple stores, frame zero-fill, epilogue and stackArgsBase take int64, decide their
     encoding on the int64, and materialize a constant from its 32-bit target word (wordArm32), so a 64-bit and
     a 32-bit host emit the same code.  Fixed with it: the copy / zero loops ended on BPL (a >= 2 GiB copy did one
     unit; now BCS), and string-literal byte stores wrote through a hard-coded R4 that could be their own base.
   - 4d-5. DONE `842db9d45` (2026-10-26): the RegMap's int frame lookups (LookupAlloc / LookupAllocSize /
     LookupSpill / NeedSpill / NeedAlloc / LookupArgCopy) and FrameInt are gone; the int64 forms took their
     names (user decision 2026-10-26: "Rename to plain names").  Stage 4d is done.
   - Note for 4e: copy and zero sizes still come from the int SizeOf(), which fails loud (HostInt) on a 32-bit
     host for an aggregate of 2 GiB or more, so the arm32 BCS loop fix is reachable only from a 64-bit host
     until 4e.
   - 4e (field / element offsets, copy sizes; common.ElemSizeOf / FieldByteOffset int64), 4c (callconv's stack
     layout), 4f-2.
   Tests compile functions holding the large frames / offsets without calling them, so they need no memory.
5. VM / interp: VM memory is host memory, so its layout narrows at allocation (`HostInt`), failing loud.
6. Remove the int forms of `SizeOf()` / `FieldOffset()` / `TrailingPadding()` / `ArrayLenHost()`; end to end
   on the 32-bit lane: a program with a > 2 GB type for a 64-bit target compiles through both backends.

Call sites today (non-test): native ~235 (aarch64 65, arm32 63, x64 55, common 30, callconv 14, fold 8),
codegen 60, types 55, vm 32, irgen 30, check 22, interp 12, ir 8, others ~15.
