# Plan: fewer large-aggregate copies — the LLVM backend's parity with native, then IR-level passes

Status: IN PROGRESS (work-1, 2026-09-30).  Todo entry: "IR-level optimizations for large-aggregate copies —
shared by every backend".  User direction (2026-09-30): "This and other optimizations are what we need";
native backends are co-equal, so the fixes must let native make the same optimizations.

## Background

The LLVM backend carries a bulk aggregate (> 16 scalar leaves) as memory (plan-llvm-bulk-aggregate-values.md):
a load copies the value into a private `.m` slot, and each consumer copies out of it.  Reviews found -O2 code
where those copies are pure overhead:
- `var x Big = *p; return x.n` copied all of Big (808 bytes) to read one field;
- `mkBig().n` at -O2 copied a 100-byte member into a slot nothing reads;
- `var x T` then `x = v` zero-fills x and then overwrites every byte.
On hosted targets the copies are now llvm.memcpy / llvm.memset, so clang removes most of them — but not on
bare metal (rt.MemCopy is opaque), and not in the native backends, which execute the IR's copies as written.

Prior art: the native backends already elide an aggregate load's materialization when it is provably safe —
`AggLoadElidable` (pkg/binate/native/common/common_aggload_elision.bn; done/plan-native-aggcopy-fusion.md):
S-alloca (the source is a confined, non-escaping stack slot not re-stored before the last use), S-adjacent
(nothing that writes or frees memory between the load and its last use; ≤ 16 bytes for copy shapes) and
S-extract (every use is a field extract; any size).  An IR-level load→store peephole was considered there and
rejected as the primary lever: it changes the IR the VM also runs.

Landed first (the quick regression fix): on hosted targets the LLVM backend's bulk copies / zero-fills are
llvm.memcpy / llvm.memset (`733aa87dc`), which clang optimizes; bare metal keeps rt.MemCopy / rt.MemZero.

Finding (2026-09-30): the native aa64 backend does NOT elide the review's example either — `var x Big = *p;
return x.n` at -O2 copies all 808 bytes, zero-fills the 800-byte SROA slot of `arr` and copies `arr` into it
(a slot nothing reads).  Those writes sit between the load and the `n` extract, so AggLoadElidable's
"nothing writes between" rules refuse.  Step B (dropping the dead slot's zero-fill and store) is what makes
the load's only use the `n` extract; then native's S-extract elides it, and step A lets LLVM do the same.

## Steps

A. LLVM backend parity with native: a bulk load that `AggLoadElidable` accepts is not copied into `.m`; its
   memory is its source.  Extracts GEP into the source; a store / return copies straight from the source.  A
   by-value call argument from such a load is always copied into the call's slot (never the source itself —
   the callee may write its argument).  Fixes the first pattern on every LLVM target, bare metal included.
   Status: DONE `8589029a6` (2026-09-30) — emit_bulk_elide.bn, AggLoadElidable exported from
   native/common, conformance 1463.  A load whose only use is a by-value argument of a call in its block keeps
   passing its private copy (one copy either way).  Checked while designing: AggLoadElidable's S-alloca shape
   does not check an aggregate member's later reads (S-extract rejects aggregate members for exactly that
   reason); probe programs (member read after the source is reassigned) ran correctly on native and LLVM at
   -O0 / -O2; another session then made the rule follow aliases (`a3766314e`).  The LLVM side excludes
   memory-backed members regardless.
B. IR-level dead-store elimination for aggregate slots: a store (or zero-fill) into an alloca whose bytes are
   never read afterwards — SROA leftovers — is dropped.  Every backend and the VM benefit.
   Status: DONE `79d72d01e` (2026-09-30) — iropt dead-slot pass (-fdead-slot, on at -O1+, not
   in the VM set since the VM does not run sroa), conformance 1471.  The review's `var x Big = *p; return x.n` is
   now one field load on both backends (native aa64: frame 0x690 → 0x40 bytes, ~490 → ~25 instructions).
   Measured: native aa64 bnc built -O2 with vs without the pass — __text 10,074,440 → 10,072,928 bytes (−1,512,
   −0.015%; bnc's own code rarely has the pattern); both compilers produce identical output.
   Possible refinement: a slot written only through address computations (`x.data[0] = 1` on a field slot) is
   still kept — the pass counts any non-store use, GEPs included, as a read.
C. Zero-fill then full overwrite: a zero-fill of a slot followed by a store of the whole slot, with no read of
   it between, drops the zero-fill.  IR-level, like B.
   Status: DONE `bcec3508a` (2026-10-01) — iropt dead-store pass (-fdead-store, on at -O1+, not in the VM set):
   within a block, a store to a confined slot that a later whole store overwrites unread is dropped, and an
   OP_ALLOC whose first use is a whole store is marked ir.Instr.NoZeroInit (LLVM / aa64 / x64 / arm32 skip the
   zero-fill; the VM ignores it).  Padding left unwritten is allowed (spec §21: contents unspecified).
   Measured: native aa64 bnc built -O2 with vs without the pass — __text 10,193,708 → 9,559,112 bytes (−6.2%);
   identical compiler output.  Conformance 1479.
User decision 2026-09-30: after C, also (order mine): native should not copy a struct load nothing uses
(AggLoadElidable refuses a load with no uses, so native still copies a struct whose fields are all dead — the
LLVM backend already skips it, bulkMemUnused), and a general dead-code sweep for pure values (dead phis and
arithmetic left behind when their only consumer was a removed field).
   Status: DONE `2946dfa98` (2026-10-01) — iropt dce pass (-fdce, on at -O1+, not in the VM set), after the last
   check-removing pass (bce-redundant) and before licm.  Removes unused loads too (user decision 2026-10-01:
   "unused loads should be deletable" — a bad pointer's read is not a defined panic), which also closes the
   native gap: a struct copy whose fields are all dead is no longer made (the review's example: main's frame
   0xb80 → 0x500 bytes, 507 → 221 instructions).  Measured: native aa64 bnc −76,408 bytes __text (−0.8%),
   identical output.  Conformance 1494, 1495.
D. Copy chains (load → private copy → temp slot → argument): revisit after A–C with measured -O2 IR; parts may
   already be gone.
   Measured 2026-10-01 (after A–C and dce, on `2946dfa98`), -O2, `Big` = {n int; arr [100]int}; full-size
   copies (c) and zero-fills (z) per function, native aa64 (-fno-inline) / LLVM arm32 bare metal; the last
   column is the fewest the semantics need:
   | shape | native | LLVM | min |
   |---|---|---|---|
   | `sum(b Big)` reads 3 fields (param slot) | 2c+1z | 1c+1z | 0 |
   | `mk`: `var b Big; …; return b` | 2c+1z | 2c+1z | 1z (in the sret buffer) |
   | `sum(*p)` | 1c | 1c | 1c |
   | `var x Big = mk(k); return sum(x)` | 1c | 2c | 0 |
   | `sum(mk(k))` | 0 | 0 | 0 |
   | `return *p` | 2c | 2c | 1c |
   | `var x Big = mk(k); return x` | 2c | 4c+1z | 0 |
   | `forward(b Big) { return sum(b) }` | 2c+1z | 2c+1z | 0 |
   | `var x = mk(k); var y = x; sum(y)` | 3c+1z | 3c+1z | 0 |
   | `h.b = *p; sum(h.b)` | 2c | 3c | 1c |
   | `sum(g)` (global) | 1c | 1c | 1c |
   Native copies are fully unrolled (808 B = 202 instructions each).
   Real code — static tally of cmd/bnc's -O2 IR (native aa64; throwaway instrumentation, 69,698 aggregate
   loads / call results / param slots; bytes are static, not dynamic): loads passed as by-value arguments
   ~750 KB static (load of a local → arg alone 2,752 sites > 64 B, 15.7%); by-value aggregate param slots
   4,202 (203 KB; e.g. every `(cc CallConv)` method — 88 B); call result stored whole into a local ~5,600
   (~170 KB); loads stored whole elsewhere ~4,000 (~560 KB, some necessary); `load → extracts` 22,867 (the
   S-extract shape, not a chain).
   Root of most of the table: nobody owns a by-value argument's copy.  The LLVM backend copies on BOTH
   sides (the caller's `.bv` slot, then the callee's param slot); native callers may pass an aliasing
   (elided) load by reference (AggLoadElidable accepts an OP_CALL argument use), so native callees must
   copy (and do: incoming → value region → param slot, plus the slot's zero-fill).
   User constraints (2026-10-02): "There should be one ABI per platform (arch/OS); in particular, LLVM and
   native MUST share the same ABI.  Also, compatibility with C is an important feature; passing large
   structs by value should be compatible between Binate and C (anything else would be extremely
   unfortunate and inconvenient)."  So no Binate-specific ownership convention: the C ABI already says who
   owns a large by-value struct's memory — the callee, on all three (AAPCS64: the caller copies it to
   memory it allocates and passes a pointer; SysV x86-64: MEMORY class, copied onto the stack; AAPCS32:
   copied into r0-r3 + stack).
   Finding (2026-10-02): today Binate's own convention for a >16-byte by-value aggregate is NOT the C ABI
   on x64 or arm32 — both backends pass a plain pointer (LLVM `ptr`, native IndirectLargeAggregates; the
   callconv comments say it was chosen to match what pkg/codegen emitted, "NOT textbook AAPCS"), while C
   passes it by value; C interop goes through adapters (__c_call's ForCBoundary marshalling, the
   `__centry.` / #[c_export] thunks).  On aa64 the shape matches C but the ownership does not: a native
   caller may pass memory it still uses (an elided load), relying on every Binate callee copying.  LLVM
   and native do agree with each other (mixed-producer programs depend on it).
   Revised proposal (awaiting the user's decision):
   - D1. Make Binate's ABI for a >16-byte by-value aggregate the platform C ABI, in both backends: on x64
     and arm32 pass it the C way (SysV stack / AAPCS32 r0-r3 + stack) instead of a pointer, retiring the
     adapters for this case; on aa64 keep the pointer but follow AAPCS64's rule (the caller passes a copy
     it owns — or a temporary nothing reads afterwards — and the callee uses it in place).  Callees then
     never copy their aggregate params.  An ABI change on x64 / arm32 (toward C) for Binate-to-Binate
     calls: param + call lowering in all four backends, closure / func-value shims, the VM ↔ compiled
     boundary, C-entry thunks, __c_call.
     DONE (2026-10-07): x64 `dfde1e73a`, arm32 `e62af60cf`, aa64 `dfce07941` / `35a7a4f3b`, callees in place
     on every target `2e5cec867` — done/plan-c-abi-large-aggregates.md.
   - D2. Return-value placement at the call site: a call whose aggregate result is stored whole into a
     confined local (or returned) gets that local (or the incoming sret buffer) as its result buffer — a
     shared analysis marks the store, as NoZeroInit marks allocs; each backend honours the mark.
     Design (2026-10-07, work-1; recon on `2e5cec867`).  -O2 IR of `var x Big = mk(k); use x.n`: the call's
     result goes to a temporary (`.sret` / a native region) and a whole OP_STORE copies it into x's slot;
     `return mk(k)` copies the temporary into the incoming sret buffer.  An emit-time shared analysis in
     native/common (like InPlaceParamSlots — no IR field, every -O level) maps a call to the slot its result
     is stored into when: the result's only use is that whole store, in the call's block; the slot is a stack
     OP_ALLOC (not a parameter's, not a global) of the result's size whose address never leaves the
     function (used only as a load / store address or the base of field / element pointers that are, in
     turn); nothing between the call and the store references the slot or a value derived from it (an
     elided load of the slot aliases it — AggLoadElidable assumes the store is the slot's writer); and the
     slot's OP_ALLOC precedes the call or is NoZeroInit (its zero-fill must not land after the result).
     Native: PlanFrame gives the call the slot's region, so every call path writes there, and the store
     emits nothing.  LLVM: the call's result memory is the slot's alloca instead of `.sret`, and the store
     emits nothing.  The return case (the incoming sret buffer as the call's) follows as a second commit.
     Status: the slot case DONE `9fe09deb6` (2026-10-07) — conformance 1606 / 1607; a local declared before a
     loop is placed too (the slot's OP_ALLOC need only not sit between the call and the store, unless
     NoZeroInit); LLVM honours it for direct sret calls only (other LLVM calls keep their copy).  -O2 native
     aa64: `var x Big = mk(k); use x` frame 0x6a0 → 0x370, no copy.
     The return case DONE `3c18f42d4` (2026-10-09) — common.ReturnedCallResults: a direct call whose result is
     the function's one aggregate result, returned as is in the call's block, gets the incoming sret buffer
     (natively X8 / RDI / R0 loaded from SretSlotOff, no region, no copy at the return; LLVM passes
     `%v.retbuf`).  -O2 native aa64 `passOn`: frame 0x340 → 0x20, no copy.  Conformance 1608 / 1609.
     Not covered (review observation, user's call): results with managed fields never qualify — their
     ownership handling gives the value a second use — so managed-slice / interface-value returns still copy
     (on arm32 nearly every such return is sret).
   - D3. A local returned at every return lives in the sret buffer.
     Design (2026-10-09, work-1; recon on `3c18f42d4`).  -O2 `mk`: `var b Big; b.n = k; ...; return b` —
     native zero-fills b's region, copies b into the load's region, then that into the caller's buffer (two
     808-byte copies; the load is not elided: b is reached through field pointers); LLVM zero-fills, copies
     into `.m`, then into `%v.retbuf`.  Shared emit-time analysis (native/common RetbufLocal): f has one
     aggregate result; every OP_RETURN returns, as its one value, a whole OP_LOAD of the same plain stack slot
     S (the result's size), in the return's block, the load's only use; nothing between such a load and its
     return references S or a value computed from it; S's address never leaves f (slotReach); S is not a slot
     that may be a parameter's incoming copy.  Honoured when f returns through a buffer.  Native: S gets no
     region and its spill slot is SretSlotOff (holding the incoming pointer), so every address use goes
     through getOperand as for an in-place parameter slot; its zero-fill goes through that pointer and covers
     exactly SizeOf bytes (the caller's buffer may be exactly the struct's size — a frame region's 8-rounding
     must not leak into it); the returned loads get no region (emitAggLoad's no-region path aliases S) and the
     return copies nothing.  A call stored into S keeps copying natively (S has no region, so
     placeCallResults falls back).  LLVM: S's alloca is `getelementptr i8, ptr %v.retbuf, i64 0`, the returned
     load is not emitted, and the return is `ret void`.  Safe because the caller's buffer is private to the
     call (a temporary, a non-escaping local per CallResultSlots, the caller's own incoming buffer per
     ReturnedCallResults; a C caller's sret buffer may be written by the callee at any time — clang's NRVO).
   - D4. A confined local whose last use is a by-value argument is passed without a copy (needs D1; aa64
     only — on x64 / arm32 the C ABI copies the value into the argument area regardless).

Each step: unit tests on the emitted IR / pass output, conformance on LLVM + LLVM arm32 bare metal + native aa64
(and the VM for B / C), and a measurement per perf-optimization-guide.md (copy bytes in the native aa64
self-compile, as done/plan-native-aggcopy-fusion.md §4 measured; LLVM -O2 IR / binary for the review's cases).
