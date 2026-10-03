# Plan: pass >16-byte by-value aggregates per the platform C ABI (both backends)

Status: IN PROGRESS (work-1, 2026-10-02).  Todo entry: "Binate's ABI for a >16-byte by-value aggregate is not
the C ABI on x64 or arm32".  Step D1 of plan-aggregate-copy-opts.md (whose copy measurements motivated it).

User constraints (2026-10-02): "There should be one ABI per platform (arch/OS); in particular, LLVM and native
MUST share the same ABI.  Also, compatibility with C is an important feature; passing large structs by value
should be compatible between Binate and C (anything else would be extremely unfortunate and inconvenient)."
Decision: "yes" to doing D1 as C-ABI conformance.

## Current state

A ">16-byte by-value aggregate" below means an aggregate argument the C ABI does not pass in registers: larger
than `AggregateInRegMax` (16) and not an HFA the target passes in FP registers.

- Both backends pass it as a plain pointer on every target (native `IndirectLargeAggregates = true`;
  LLVM `ptr`).  The C ABI passes it:
  - AAPCS64 (aa64, Linux and Darwin): by pointer to a copy the CALLER makes in memory it allocates; the
    callee may treat that memory as its own.
  - SysV x86-64: MEMORY class — by value on the stack (the caller copies it into the argument area).
  - AAPCS32: by value, split across r0-r3 and the stack.
- C interop goes through adapters: `__c_call` marshals with `CallConv.ForCBoundary()` (which only flips
  `IndirectLargeAggregates` to `CAbiIndirectLargeAggregates`); a Binate function handed to C goes through a
  `__centry.` / #[c_export] thunk (`funcNeedsCEntryByvalParamThunk`).
- Ownership: a native caller may pass memory it still uses (an elided load — `AggLoadElidable` accepts an
  OP_CALL argument use, refusing only OP_C_CALL), so every callee copies: native aa64
  (`aarch64_emit_func.bn`) copies the pointee into the param's value region, then IR's `store slot, param`
  copies it again into the param slot (zero-filled first).  The LLVM caller copies into a `.bv` slot (or
  passes a single-use `.m` private copy) and the LLVM callee memcpys into its param alloca
  (`IsByvalParamRef`).
- The VM reaches compiled code through `__shimP` → `__shim` (backend-emitted), which forwards a pointer into
  the VM's own storage for an aggregate argument.
- LLVM and native agree, and mixed-producer programs depend on it (`pkg.centry.identity`).

## Target state

The platform C ABI on every target, in both backends; callees never copy a by-value aggregate parameter:
- aa64: the caller passes a pointer to memory it owns for the call — a fresh copy, or a temporary nothing
  reads afterwards (a call result, a load's private copy used only by this call); the callee uses it in
  place.
- x64: the caller copies the aggregate into the outgoing stack area; the callee uses it there.
- arm32: the caller passes it in r0-r3 + stack; the callee stores the register part next to the stack part
  (the usual AAPCS split-argument save) and uses the result in place.
- The adapters' >16-byte-param case goes away (`ForCBoundary` keeps any other difference it has).

## Increments

Each keeps LLVM and native on one ABI per target at every commit (a callee that still copies is compatible
with a caller that owns; a callee that works in place needs every caller — both backends — to own).

1. aa64 native: callers pass owned memory (copy into a per-call argument temp unless the value is an owned
   single-use temporary — the rule `bulkArgDirect` applies on LLVM); callees use the incoming pointer as the
   param's value region (no copy).  `__shimP` (the VM path) copies a VM-storage aggregate into an owned temp.
   LLVM callers already own; LLVM callees still copy — compatible.
2. x64: SysV MEMORY passing in both backends (native internal `IndirectLargeAggregates = false`; LLVM
   `byval` params/args), callees in place; shims, func-value / closure / iface paths, `__shimP`, the C-entry
   thunk's byval case, `__c_call`.
3. arm32: AAPCS32 by-value split in both backends, same coverage as 2.
4. LLVM aa64 callee in place (drop the param memcpy) — after 1, every aa64 caller owns.
5. Param slot in place: the param's slot is the incoming memory (no `store slot, param` copy, no zero-fill)
   — an IR mark set by a shared predicate, honoured by all four backends (the NoZeroInit pattern).
6. Then plan-aggregate-copy-opts.md D2 / D3 / D4.

Validation per increment: the native conformance mode of each arch touched (`builder-comp_native_aa64-…`,
`builder-comp_native_x64_darwin-…`, `builder-comp_native_arm32_baremetal`) and the LLVM ones
(`builder-comp`, `builder-comp_arm32_baremetal`), the VM-boundary mode (`builder-comp-int`), the C-interop
e2e scripts, and the changed packages' unit tests; a copy-count measurement against plan-aggregate-copy-opts.md
step D's table.

## Progress

- DONE `e051ce4b4` (2026-10-02): a parameter's slot skips its zero-fill (iropt dead-store marks it
  NoZeroInit — its first use is the whole store of the incoming value), all backends.
- DONE `f347e953e` (2026-10-03): native callees use the incoming copy as the parameter's value when it is
  read only by the entry stores into stack slots (`common.ParamRegionElidable`; shared
  `callconv.CallConv.PassesIndirect`) — safe before callers own their copies, since nothing can write the
  caller's memory before those stores.  Native aa64 bnc __text 9,649,020 → 9,531,812 bytes (−1.21%)
  against a compiler built at `e051ce4b4`; `sum(b Big)` 2c+1z → 1c.  Conformance 1524.
- Order (user, 2026-10-03: "option 1 is fine"): the x64 switch, then arm32 (the actual C divergence), then
  aa64 ownership + param slot in place (copies).  In progress: x64.
  Local validation for x64: native `builder-comp_native_x64_darwin-…` and LLVM via
  `BINATE_FLAGS="--target x86_64-darwin" ./conformance/run.sh builder-comp` (both run under Rosetta); full
  runs on both, since a 32-byte managed-slice argument is >16 bytes on x64 (nearly every program).

## x64 design (recon 2026-10-03)

A >16-byte aggregate is SysV MEMORY class: bytes on the outgoing stack, no register consumed.
- Shared classifier `types.sysvArgConsumes`: its internal mode counts a >16 aggregate as one GP pointer;
  C mode (`SysVArgInMemoryC`) counts no register.  Internal becomes the C behaviour (then the C variants,
  `aggMemClassMaybeC`, `ForCBoundary`'s flip and `CAbiIndirectLargeAggregates` are redundant on x64).
- LLVM: a >16 param / arg is `IsByvalParam` (types) everywhere; on x64 its spelling becomes
  `ptr byval(<T>) align 8` (`writeByvalMemType`) instead of plain `ptr` — define lines and declarations
  (`writeParamTypeLLVM`, emit.bn), call args (`writeByvalArgLLVM`; the OP_C_CALL-only x64 branch becomes
  the rule), shim → underlying calls (`writeShimUnderlyingArg` / `writeShimArgRef`; no `tail` with a byval
  arg), closure → body calls (`emitClosureCaptureLoads` / `writeClosureUnderlyingArgs`).  The callee still
  receives a pointer (to its own stack copy), so IR-gen's `IsByvalParamRef` slot copy is unchanged.
  C-export / `__centry.` thunks: the byval-param case disappears (`funcNeedsCEntryByvalParamThunk` is
  false once `IndirectLargeAggregates == CAbiIndirectLargeAggregates`).
- Native x64: `SysV_AMD64().IndirectLargeAggregates = false` (the MEMORY paths exist for ≤16 straddlers
  and `__c_call`: `emitAggregateArg` word copy via RAX, the param stack-copy branch, CallStackBytes).
- Func-value DISPATCH stays by-address for a >16 aggregate (one pointer word; Binate-internal, never seen
  by C; the VM's packed slots and LLVM's `shimParamType` already agree): `isByAddressAggX64` must include
  >16 aggregates once they are not `PassesIndirect`, so the native shims re-expand them onto the stack
  (as they do for ≤16 MEMORY-class args); `EffectiveArgWords` must keep 1 for them.
- Closure captures: the closure body takes a >16 capture as a MEMORY-class stack param; the native x64
  closure shims' fast path is register-only (`isIndirectLargeCap_x64` LEA) — a >16 capture must route to
  the spill variants that place stack args.
- Validation: full native x64 darwin + full LLVM x86_64-darwin (Rosetta), against baselines taken on
  `f347e953e`; aa64 subsets to confirm no change there; CI for Linux x64 (incl. the VM).

## Open checks

- aa64 HFAs over 16 bytes (3-4 doubles) ride SIMD registers and are not in scope; the predicate must exclude
  them exactly as the C ABI does (types.HfaClassify).  Same for arm32 hard-float VFP CPRCs.
- Which native values own their memory (an aggregate OP_EXTRACT's region, phis / OP_COPY) — the
  ownership predicate must be conservative.
- Closure shims and func-value `__shim`s that forward an incoming pointer unchanged stay correct (the
  pointer is already owned by the call); `__shimP` must copy.
