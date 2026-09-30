# Plan: memory-backed large aggregate values in the LLVM backend

Status: IN PROGRESS (work-1, 2026-09-29).  Todo entry: "The LLVM backend lowers aggregate loads, copies and
zero-fills one scalar leaf at a time".  Stage 1 (zero-fill / memory-to-memory copy → rt.MemZero / rt.MemCopy
above 16 leaves) landed as binate `a39d67d9f`.  This plan is stage 2 — user decision 2026-09-29: the full fix
("Doesn't (a) still leave the 'uncommon' case still rather disastrous?").

## Problem

The LLVM backend carries an aggregate VALUE as an LLVM first-class SSA aggregate: an aggregate `OP_LOAD` is a
per-leaf load + `insertvalue` chain, an aggregate `OP_STORE` of a value a per-leaf `extractvalue` + store, a
`>16`-byte by-value argument is stored into a `byval` slot from the SSA value, and an `sret` call result is a
whole first-class `load T, ptr %sret`.  All of it is O(leaves) IR.  Measured after stage 1: a 100 KB array
passed by value and returned by value is 900k lines of IR, 382 s and 3.8 GB of clang to compile.

## Representation

A value is "bulk" when its type has more than `bulkLeafThreshold` (16) scalar leaves (`isBulkAggregate`).  A
bulk value may be MEMORY-BACKED: its LLVM form is then a pointer to its bytes, `%v<ID>.m`, instead of an SSA
aggregate.  The bytes are immutable for the value's lifetime (IR values are SSA): they live in a hoisted
function-scoped alloca private to the value, or in memory nothing writes while the value is live (an sret
slot, a `byval` parameter, read-only rodata).

A bulk value is memory-backed only when EVERY consumer of it supports the memory form (a per-function scan
before emission).  Any value with an unsupported consumer keeps today's SSA form — so each step below is
correct on its own, and extends the set of memory-backed values by adding consumer / producer support.

## Producers (how a memory-backed value comes to exist)

- P1 `OP_LOAD` of a bulk type: hoisted alloca `%v<ID>.m`, `rt.MemCopy(%v<ID>.m, src, size)` at the load (the
  load's value is read where it is evaluated; the private copy makes every consumer overlap-free).
- P2 a call returning a bulk type via sret: the `.sret` slot IS the value (no load).
- P3 `OP_EXTRACT` of a bulk member of a memory-backed value: a GEP into the parent's bytes.
- P4 `OP_CONST_NIL` of a bulk type: hoisted alloca + `rt.MemZero`.
- P5 `OP_PHI` of a bulk type: its own hoisted slot, `rt.MemCopy` on each incoming edge.
- (Parameters are already addresses — `byval` — spilled by `emitFieldwiseCopy`, now `rt.MemCopy`; their uses
  are P1 loads of the spill slot.)

## Consumers

- C1 `OP_STORE` of the value: `rt.MemCopy(dst, %v<ID>.m, size)`.
- C2 `OP_EXTRACT`: scalar leaf — GEP + load; small aggregate member — per-leaf load from the GEP; bulk member
  — P3.
- C3 by-value call argument (direct / func-value / interface-method call): pass `%v<ID>.m` as the `byval`
  pointer (LLVM's byval makes the callee's copy).
- C4 `OP_RETURN` via sret: `rt.MemCopy(sret, %v<ID>.m, size)`.
- C5 `OP_PHI` incoming value (P5's edge copy).
- C6 `OP_BOX` / interface-value construction from the value, multi-return tuples, C calls, casts: SSA
  fallback until supported.

## Steps (each lands separately, green)

1. Framework (bulk-value analysis, `.m` slots in the alloca hoist) + P1 + C1 — whole-aggregate copies.  DONE `575fb43ee`; runtime test conformance 1442 (`8a8b29687`).
2. C2 / P3 — extracts.
3. C3 — by-value arguments.  DONE `c97493379` (a single-use argument in the load's block passes the private copy itself; conformance 1447).
4. C4 + P2 — returns and sret call results.
5. P4, P5 / C5 — nil constants, phis.
6. C6 — whatever remains (box, interface values, tuples, C calls).

Tests per step: codegen unit tests on the emitted IR (the memory form is used; the IR stays O(1) in the
array length), and conformance for correctness (a large array copied, passed, returned, extracted from, on
LLVM + LLVM arm32 bare metal); the 100 KB pass/return program above compiles in seconds.
