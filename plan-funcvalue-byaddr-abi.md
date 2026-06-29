# Plan: function-value cross-mode ABI → coerced-aggregate args by-address + shim-extended sub-word returns

**Status (2026-06-29, BUG-BASH LANE 3):** ARG side implemented across LLVM + VM +
native (x64/aarch64); a confirmed clobber bug fixed on the aarch64 register paths
and validated; x64 + spill rigor + the RETURN change + fixtures remain. Worktree
`temp-binate-2` branch `work-2`: `f841caaf` (LLVM+VM ARG), `613e4a1f` (native ARG +
aarch64 clobber-safe staging). **Not yet landable** (atomic cross-backend change;
see "Remaining" + "Clobber-safety" below). Also `3298428b` (the >7-arg extern guard,
independent, landable on its own).

## Clobber-safety (CONFIRMED MAJOR bug + the fix) — read this before touching native

By-address makes the INCOMING dispatch cursor (1 pointer word per coerced struct)
and the OUTGOING real-ABI cursor (ArgWords words) DIVERGE. The native shims do
hand-written register shuffles; the old "N words" convention was clobber-safe only
because incoming==outgoing (a constant down-shift, dst<src). With divergence an
outgoing register write can land in a LATER param's not-yet-read incoming register —
a silent miscompile. Confirmed by `conformance/937_funcval_multi_coerced_struct`
(`[S3,S3,int]` etc.): PASSES on LLVM (builder-comp) + VM (builder-comp-int) — they
don't hand-allocate — but FAILED on native_aa64 before the fix. NEITHER processing
direction is universally safe (left-to-right clobbers a trailing scalar; right-to-
left a leading one — it is a register-permutation/parallel-move hazard).

Fix that IS applied + validated (aarch64 funcvalue shim, `emitShimArgMarshalAA64`):
**stage every incoming user-arg dispatch word to scratch X9..X15** (disjoint from the
X0..X7 outgoing bank and the X16/X17 assembler temps; nIn<=7 by the shim budget)
before any outgoing write, then marshal reading from the staged regs — clobber-safe
in any direction, frame-free. The aarch64 CLOSURE shims do NOT need staging: a
capturing closure prepends >=1 capture word, forcing a strictly-UPWARD shift, so the
right-to-left walk the sweep agents wrote is provably clobber-safe. 937 (funcvalue
trailing/leading/3-struct + capturing closure) is green on builder-comp, -int, AND
builder-comp_native_aa64; 889 green on native_aa64.

ALSO fixed: the shim register/spill ROUTING budget must count OUTGOING gp words
(`AggCoercedInReg ? ArgWords : EffectiveArgWords`), not EffectiveArgWords — else a
wide coerced-struct call (incoming 1/coerced) undercounts the n outgoing regs and
wrongly stays on the register-only path. Done in aarch64_funcvalue_shim.bn.

## ARG side: DONE + validated across ALL backends + VM (2026-06-29)

The coerced-aggregate-arg by-address change is complete and clobber-safe everywhere.
Worktree commits: `f841caaf` (LLVM+VM), `613e4a1f` (native sweep + aarch64 reg-only
staging), `d2696f35` (x64 + both spill paths stack-staging). Tests `937` (register-
only: trailing/leading/3-struct + capturing closure) and `938` (wide spill: 4
structs + scalar) green on builder-comp, builder-comp-int, builder-comp_native_aa64,
AND builder-comp_native_x64_darwin (Rosetta — note x64 IS testable locally via the
`_darwin` mode, no Docker needed). `889` green on both native arches. Native
regression (aggregate/closure/funcval) green on both arches. Key fix: incoming
by-address cursor (1/coerced) vs outgoing real-ABI cursor (ArgWords/coerced) diverge
→ stage incoming GP dispatch regs before any outgoing write (aarch64 reg-only:
X9..X15; x64 + all spill paths: a frame staging area; capturing closures are safe
via their upward capture-shift, no staging).

## Remaining

1. **Sub-word/bool RETURN, shim-extends** (the second half — not started). NOTE the
   structural wrinkle: a sub-word-return shim currently TAIL-branches, so it can't
   post-process the return; extending shim-side means giving it the call-then-extend
   shape (like the float-return shape). Likely the LLVM shim is the load-bearing one
   (the VM reads the shim's return as a full i64 via _call_shim_scalar, so garbage
   upper bits corrupt it — items 4); native callers know the func-value return type
   and read the right width, so native↔native may already be fine — verify before
   changing native shapes. Then revert the VM return-narrow (25117a2e:
   subWordReturnInfo + post-call BC_ZEXT/BC_SEXT) once the shim extends. LLVM:
   shimIntSlotType -> i64 for sub-word; shim sext/zext before ret; caller truncates.
   Native: sext/zext (bool: and #1) before returning in x0/rax (all shapes + spill).
   VM: revert the return-narrow (subWordReturnInfo + post-call BC_ZEXT/BC_SEXT in
   lower_func.bn; remove subWordReturnInfo; re-check callTargetIsExtern unused).
4. **Fixtures** for the original items 1/4 (cross-mode iface/func-value coerced-agg +
   sub-word return) per the recon, and the cross-backend interchange test.
5. **File-length splits** (hygiene hard/soft caps): aarch64_closure_shim.bn 601 > 600
   (hard, BLOCKER), x64_closure_shim.bn 584 > 500 (soft). Split along natural
   boundaries (do NOT trim docs).
6. **Full matrix**: builder-comp / -int / -int-int / -comp / -comp-int / -comp-comp;
   builder-comp_native_aa64; builder-comp_arm32_*; x86-64 Docker; hygiene.

## Decision

Per an architectural review (the "right long-term design", ignoring transition cost
since the ABI is not declared stable): **all aggregates ride the func-value dispatch
convention BY-ADDRESS** (one i8*/pointer slot), and the per-function shim — the one
place that statically knows the signature — loads + re-marshals to the underlying's
real ABI. Sub-word/bool RETURNS are **extended to a full word by the shim**, so every
caller (LLVM, native, VM) reads a clean word.

Rationale: marshaling knowledge belongs in the shim, not duplicated across ~5 callers
× 2 backends + the VM. By-address is ALREADY the convention for >16B byval, slices,
iface-values, float scalars, and the cross-mode iface-method path; the ≤16B coerced
`[N x i64]` carve-out was the sole exception and the sole recurring source of
cross-mode divergence bugs (ToUnix, claude-todo #112, B1/C1). Hard requirement (user):
LLVM + native backends + VM must be FULLY INTERCHANGEABLE (compile pkg A with LLVM,
pkg B native — a func value crosses that boundary), so the convention must be uniform.

## Key value-model insight (makes native tractable)

In the native backends a struct value is already in MEMORY and the backend holds a
POINTER to it (`getOperand` returns the pointer). The coerced path currently *loads*
N words from that pointer to pass them; by-address just passes the pointer instead
(exactly the existing indirect-large `>16B` path). The shim then loads N words from
the incoming pointer into the real-ABI coerced registers (use a scratch reg, e.g.
aarch64 X16 / x64 r10/r11, to hold the pointer so the loads can't clobber it).

## Done + validated (commit f841caaf)

LLVM shim (`pkg/binate/codegen/emit_funcvals_sig.bn`, `emit_funcvals_shim.bn`):
- `shimParamType`: coerced-agg → `i8*` (was `[N x i64]`); merged with the
  isAggregateArg i8* branch.
- `emitShimArgLoads`: coerced-agg now loads `[N x i64]` from the i8* (mirrors the
  isAggregateArg load, but loads the coerced `aggCoerceLLTy` type).
- `writeShimArgRef`: coerced-agg → `%a<i>_v` (the loaded value).

LLVM caller (`emit_call_funcvalue.bn`): `emitFuncValueArgPreamble` stores the struct
into the `[N x i64]`-sized `.cas` slot (so a partial last word is covered by the
shim's full load) and passes the slot's i8* address (`.cas8`); `emitFuncValueArgList`
passes `i8* %v.cas8<i>`. (`emit_iface_call.bn` is a DIRECT impl-method call — natural
coerced ABI — UNCHANGED. `emit_call_handle.bn` uses plain `llvmType` and never hit the
coerced-agg shim path — pre-existing, left alone.)

VM (`pkg/binate/vm/lower_*.bn`): OP_CALL stops expanding coerced-agg to words — all
paths pack the struct ADDRESS (1 slot, plain MOV) like the iface/func-value path; dead
helpers `coercedAggArgWords` / `packSlotWords` / `packInstrCount` removed;
`findMaxCallArgs` + `lower_call.bn` Imm use `argSlots`. `subWordReturnInfo` + the VM
return-narrow are KEPT for now (the shim doesn't extend yet — see RETURN below).

Validated: builder-comp (97 aggregate / 49 closure / 55 func_value / 81 funcval / 190
iface), builder-comp-int (same + stdlib time/math/os), vm (207) + codegen (246) unit
tests.

## Remaining — ARG by-address, native backends

1. `pkg/binate/native/common/common_callconv.bn` `EffectiveArgWords`: return **1 for
   every `IsAggregateTyp(t)`** (was: only indirect-large `>AggregateInRegMax`). This is
   the dispatch-convention word count, used ONLY by the shim marshalers (confirmed —
   not the real ABI). Changing it forces matching updates in EVERY shim marshaler.
2. Native callers — pack a coerced-agg as the pointer (1 word), merging into the
   existing indirect-large branch: `aarch64_call_indirect.bn` (~L56-92, the
   `IsAggregateTyp` block — drop the `ArgWords` load-and-pass, always pass the
   `getOperand` ptr), the general native func-value caller in `aarch64_call.bn`, and
   the x64 equivalents (`x64_call_indirect.bn`, `x64_call.bn`). Also the iface/handle
   native callers if they pack coerced-aggs for a shim.
3. Native shim marshalers — for a coerced-agg param the incoming is now 1 pointer word;
   LOAD `ArgWords(pt)` words from it into the real-ABI coerced registers (scratch-reg
   to avoid clobber). Files (one marshaler each; also their spill paths):
   - aarch64: `aarch64_funcvalue_shim.bn` (`emitShimArgMarshalAA64`),
     `aarch64_funcvalue_spill.bn`, `aarch64_closure_shim.bn`,
     `aarch64_closure_shim_aggregate.bn`, `aarch64_closure_shim_float.bn`.
   - x64: `x64_funcvalue_shim.bn`, `x64_funcvalue_spill.bn`, `x64_closure_shim.bn`,
     `x64_closure_shim_aggregate.bn`, `x64_closure_shim_float.bn`.
   NB: incoming srcWord advances by 1 (the pointer) but outgoing ngrn/register count
   advances by `ArgWords(pt)` (the coerced words) — these now DIVERGE, so the single
   `w = EffectiveArgWords` used for both must split.

## Remaining — sub-word/bool RETURN extended by the shim

4. LLVM: `shimIntSlotType` → `i64` for a sub-word int/bool return (like the float-slot
   convention); shim body sext/zext the underlying's sub-word result to i64 before
   `ret`; `emit_call_funcvalue.bn` caller truncates the i64 shim result back to the
   natural type (mirror the float bitcast-back path).
5. Native shims: sext/zext (bool: `and #1`) the underlying's sub-word return to a full
   word before returning in x0/rax (all four shapes incl. spill).
6. VM: revert the return-narrow (`subWordReturnInfo` + the post-call BC_ZEXT/BC_SEXT in
   `lower_func.bn`; remove `subWordReturnInfo`). Re-verify `callTargetIsExtern` becomes
   unused and remove it.

## Remaining — tests

7. Synthetic pkg/binate/vm unit fixture: an injected native-only iface method / func
   value taking a coerced-agg by value (N=1 ≤8B, N=2 16B, partial-last-word 12B,
   interleaved with scalars) AND returning a sub-word/bool — validates items 1 & 4 (no
   injected stdlib exercises this; it's why they were latent). Closest existing:
   `TestExternSmallStructAggregateDispatch` + `vm_exec_iface_test.bn` hand-built
   vtables. Cross-backend interchange test (LLVM↔native func value with a coerced-agg
   arg + sub-word return).
8. Full matrix: builder-comp / -int / -int-int / -comp / -comp-int / -comp-comp,
   builder-comp_native_aa64-comp_native_aa64, builder-comp_arm32_*, and x86-64 via
   Docker (`--platform linux/amd64 ubuntu` + clang; the sub-word-return bug is
   x86-64-specific). Hygiene.

## Landing

Atomic — the convention must flip on every backend + the VM together (cross-backend
modes mix native-main + LLVM-deps, so a half-flip is a silent miscompile). Likely two
landable commits if staged so each keeps all modes green is impossible here; more
realistically one large commit (ARG) then one (RETURN), each green across the full
matrix. Update claude-todo.md cross-mode-residuals entry (items 1, 3, 4) on landing.
Item 2 (the >7-arg extern guard) already committed separately (`3298428b`).
