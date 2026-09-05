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
2. **LLVM arm32.** The C-ABI form of a >16 struct param is NOT a simple byval-ptr
   (clang coerces to words / uses byval per its ARM rules) — the thunk must
   reconstruct a `ptr` from the split C-ABI form. INVESTIGATE what clang / the
   existing `emit_ccall.bn` arm32 path emits before implementing.
3. **Native x86-64.** The current model (alias label → in-place narrow-reg fixup
   → jmp `sym`) does NOT work: the C-ABI and internal register/stack assignments
   differ (the agg is stack-value in C, ptr-in-reg internally, shifting every
   later param). Needs a real trampoline: point the internal reg at the incoming
   C-stack aggregate (already a caller-owned copy — no memcpy needed) and remap
   the other args, then reach `sym`. The incoming stack aggregate stays live for
   `sym`'s duration. Design the reg/stack remap (interacts with narrow-reg
   normalization when both are present).
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
