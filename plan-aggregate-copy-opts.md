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

## Steps

A. LLVM backend parity with native: a bulk load that `AggLoadElidable` accepts is not copied into `.m`; its
   memory is its source.  Extracts GEP into the source; a store / return copies straight from the source.  A
   by-value call argument from such a load is always copied into the call's slot (never the source itself —
   the callee may write its argument).  Fixes the first pattern on every LLVM target, bare metal included.
B. IR-level dead-store elimination for aggregate slots: a store (or zero-fill) into an alloca whose bytes are
   never read afterwards — SROA leftovers — is dropped.  Every backend and the VM benefit.
C. Zero-fill then full overwrite: a zero-fill of a slot followed by a store of the whole slot, with no read of
   it between, drops the zero-fill.  IR-level, like B.
D. Copy chains (load → private copy → temp slot → argument): revisit after A–C with measured -O2 IR; parts may
   already be gone.

Each step: unit tests on the emitted IR / pass output, conformance on LLVM + LLVM arm32 bare metal + native aa64
(and the VM for B / C), and a measurement per perf-optimization-guide.md (copy bytes in the native aa64
self-compile, as done/plan-native-aggcopy-fusion.md §4 measured; LLVM -O2 IR / binary for the review's cases).
