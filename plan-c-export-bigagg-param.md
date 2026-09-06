# Plan: inbound `#[c_export]` >16-byte by-value param — full-fidelity adapting thunk (fix (b))

Tracks the fix for the MAJOR latent ABI bug: an inbound `#[c_export]` function
with a >16-byte by-value aggregate parameter presents Binate's **internal**
pointer convention to a C caller on x86-64 / arm32 → silent garbage. Owner chose
**fix (b)** (adapting entry thunk, full fidelity) over (a) (reject at export).
Diagnosis lives in `claude-todo.md` ("Inbound `#[c_export]` with a >16-byte
by-value param …"); ABI spec status note in `docs/abi/04-c-boundary.md` §4.4.

## The mismatch (exact)

Two CallConv flags (`pkg/binate/native/common/common_callconv_ctors.bn`):

| target | `IndirectLargeAggregates` (internal >16 → ptr) | `CAbiIndirectLargeAggregates` (C ABI >16 → ptr) | inbound c_export |
|---|---|---|---|
| aarch64 (AAPCS64) | true | **true** | coincide — no bug |
| x86-64 (SysV) | true | **false** | C passes stack MEMORY — **bug** |
| arm32 (AAPCS32) | true | **false** | C passes split r0–r3+stack — **bug** |

Internal (both LLVM + native): a >16 aggregate param is a single `ptr` in the
next GP arg reg (`writeDefineParam` → plain `ptr` since `SysVArgInMemory` is
FALSE for >16; native `IndirectLargeAggregates`). C ABI: byval MEMORY on x64,
by-value split on arm32 (`SysVArgInMemoryC` / `CAbiIndirectLargeAggregates`).
≤16 aggregates and all scalars/pointers/slices/iface-func values are passed
IDENTICALLY by both conventions, so ONLY the >16 by-value aggregate param needs
adaptation. Returns coincide (internal sret thresholds match the C ABI:
InternalSretBytes 16 on x64/aa64, 4 on arm32), so the thunk forwards the return
unchanged.

## Shape of the fix

Emit a C-ABI **entry thunk** for a `#[c_export]` function that has ≥1 param
needing adaptation (a >16 aggregate under a `!CAbiIndirectLargeAggregates`
target). The thunk has the true C-ABI signature; it converts each adapted param
to internal form and calls the internal (mangled) definition, forwarding the
return. When no param needs adaptation, keep the current cheap emission (LLVM
alias / native narrow-reg prefix). aarch64 never needs a thunk.

## Increments

1. **LLVM x86-64 — LANDED (`e9f9a6166`, 2026-09-05).** `emitCExportEntries` emits a
   C-ABI entry thunk (`pkg/binate/codegen/emit_cexport_thunk.bn`) instead of the plain
   alias when a param needs adaptation: each param declared in the C form
   (`SysVArgInMemoryC` — byval for the >16 agg), forwarded in the internal form
   (plain `ptr`). Three adversarial-review rounds found + fixed three distinct facets
   of the register-cursor shift: (a) a ≤16 agg after a >16 agg is register-class in C
   but memory-class internally → spill to alloca; (b) the spill store must use the
   incoming param's spelling (`%BnSlice`/`%BnIfaceValue`/`%BnFuncValue` for a
   first-class 2-word aggregate, not `[N x iW]`) or the IR is invalid; (c) the spill
   alloca must be sized to the stored type, not the natural type, or a
   non-word-multiple struct silently miscompiles at -O2 (SROA drops an eightbyte).
   Validated end-to-end under Rosetta (x86_64-darwin) at -O0 and -O2; five codegen
   tests + an `e2e/ffi-export.sh` bigagg check. Review CONFIRMED-CLEAN for the thunk's
   scope. (The original design sketch below is superseded by the landed code.)

1. **LLVM x86-64 (original sketch).** Tractable: the C-ABI byval param
   IS a `ptr` at the LLVM value level, so the thunk is
   `define <ret> @"name"(ptr byval(%T) align N %a, <others>) { %r = call <ret>
   @<mangled>(ptr %a, <others>); ret <ret> %r }` — the internal define's
   signature with the >16-agg params changed to `byval` and passed through as
   plain `ptr`. LLVM does the register-shift lowering. Replaces `emitCExportAliases`'s
   plain alias for such functions (`pkg/binate/codegen/emit_module_util.bn`); reuse
   the C-ABI byval param classification `SysVArgInMemoryC` / `aggMemClassMaybeC(cabi=true)`
   and `writeMemByvalParamDefine` from `emit_ccall.bn` / `emit_mem_byval.bn`. Carry
   the return signext/zeroext (cabiIntExtAttr) and sret forwarding.
2. **LLVM arm32 — LANDED (`aa7cf377c`, 2026-09-05).** Confirmed: on arm32 (AAPCS32)
   the C ABI passes a >16-byte aggregate BY VALUE coerced to `[N x iW]` (via
   `aggCoerceLLTy`, alignment-aware — `[3 x i64]` for a 24-byte 8-aligned struct,
   `[5 x i32]` for a 20-byte 4-aligned one), NOT the x86-64 `ptr byval`.  So the thunk
   spills every >16 param (store the coerced value to an alloca, forward the internal
   plain `ptr`), unlike x86-64's byval-ptr pass-through.  Factored the per-target
   C-ABI param type into a shared `writeCAbiParamType` (used by both the `__c_call`
   declare and the thunk); generalized `cExportThunkParamSpills` to "arrives as a
   value, forwarded as a pointer"; `cExportNeedsThunk` fires on `PointerSize==4` too.
   Validated end-to-end under real arm32 emulation (Docker armhf + qemu) at -O0 AND
   -O2 (lone big struct, non-word-multiple 20-byte struct, big+scalars+small, sret,
   r0-r3-boundary struct, slice/iface/func); pre-fix alias SIGSEGVs.  Adversarial
   review CONFIRMED-CLEAN (12+-case emulated matrix).
3. **Native x86-64 — LANDED (`cebfc6695`, 2026-09-05).** Adapter trampoline
   (`x64_cexport_trampoline.bn`): frame + `call sym`, reading each arg at its C-ABI
   position (`cc.ForCBoundary()`) and placing it at sym's internal position (`cc`) —
   a >16 agg becomes a pointer to the incoming C-stack bytes (sym's prologue memcpys
   from it), scalars/≤16 aggs copied word-for-word (narrow ones extended), floats
   left in XMM (unshifted). Phase-1 spills the GP arg regs to a scratch frame so
   phase-2 writes can't clobber a live source. Validated end-to-end under Rosetta at
   -O0 AND -O2 across 18 signatures (lone/non-word-multiple/two big aggs, stack
   overflow, narrow-after-big, floats interleaved, sret+big-param, multi-name);
   pre-fix crashes. Adversarial review CONFIRMED-CLEAN on FIRST pass (disassembled +
   ran 18 sigs × -O0/-O2). Details of the superseded design sketch below.

3. **Native x86-64 (design sketch — superseded by the landed code).** The current
   model (alias label → in-place
   narrow-reg fixup → jmp `sym`) does NOT work for a >16 agg: the C ABI passes it on
   the stack (MEMORY, consuming 0 GP regs) but `sym` expects a pointer in a GP reg,
   which shifts every later param's register/stack slot. **Design: an adapter
   trampoline**, mirroring the closure/func-value shims
   (`x64_closure_shim_aggregate.bn` — a register-only fast path + a stack-spill
   path). Per param compute the C-ABI position (`cc.ForCBoundary()` →
   `CallArgRegStart`/`CallArgStackOff`) and the internal position (`cc`), then move
   each arg from its C slot to its internal slot before reaching `sym`:
   - a >16 agg: `lea` the address of the incoming C-stack aggregate into `sym`'s
     internal GP reg (the C-passed stack copy is live for `sym`'s duration — no
     memcpy);
   - scalars / ≤16 aggs: reg→reg (a permutation — internal cursor ≥ C cursor, so
     process in reverse to avoid clobber) or, when args overflow the registers,
     stack→stack at the shifted offset (the C stack layout includes the big agg's
     bytes, the internal one a pointer, so offsets differ → the stack-spill path
     with its own outgoing-args frame + `call sym`).
   Composes with the existing narrow-reg normalization (still applied per param),
   the sret RDI shift, and the SEPARATE float/XMM cursor (a >16 GP agg does not
   shift NSRN). Validate end-to-end under Rosetta at -O0 AND -O2, like the LLVM
   legs. I emit the raw register/stack moves here (no LLVM to lower the ABI), so
   this is the most error-prone leg — expect adversarial-review iteration.
4. **Native arm32.** As x64 but the agg arrives split across r0–r3 + stack; gather
   into a copy, pass its pointer, remap. Verify on `builder-comp_native_arm32_baremetal`.

## Test plan

Extend `e2e/ffi-export.sh`: a `#[c_export]` function taking a >16-byte struct by
value (e.g. `struct { long a, b, c; }` = 24 B on LP64), a C driver that passes it
by value and checks the callee read the fields correctly. Runs on the LLVM path
(always) and native (self-skips if unavailable), at `-O0` and `-O2`. Verify it
FAILS without the fix (garbage field values) and passes with it. Plus codegen
unit tests asserting the thunk's emitted IR (LLVM) and the trampoline (native).

## Open questions

- arm32 C-ABI IR form (Inc 2) — resolve by inspecting clang + `emit_ccall.bn`.
- Native trampoline stack-arg remap when the C-ABI and internal stack layouts
  differ (Inc 3/4) — the hardest piece.
- A function with a >16-agg param AND a >16-agg (sret) return: both forwardable
  (sret ptr passes through), but pin it with a test.
