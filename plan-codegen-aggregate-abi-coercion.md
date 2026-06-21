# Plan: codegen `[N x i64]` ABI coercion for in-register aggregates

Status: **✅ DONE & LANDED (`b9081931`, 2026-06-20)**. Codegen-only `[N x i64]`
coercion; new module `pkg/binate/codegen/emit_agg_coerce.bn`. The implementation
cascaded beyond the 6 touch points below to the func-value shims / closure shims /
iface dispatch (a function has one signature, so coercing the def cascades to
every caller — ~940 lines, 13 files). Test `conformance/877_aggregate_abi_xpkg`.
Validated: native-aa64 1902/0, builder-comp 1908/0, VM/gen2/units green, hygiene
15/15. Resolution recorded in claude-todo-done.md. (The design below is retained
as the implementation record.)

## The bug (one paragraph)

`cmd/bnc` compiles the MAIN module natively but dep packages via LLVM/clang, so a
cross-package call is a native-caller → LLVM-callee boundary. For a ≤16-byte
aggregate passed by value, the native caller packs it `[N x i64]` (N=⌈size/8⌉) in
consecutive GP regs — correct AAPCS. But `pkg/binate/codegen` emits the LLVM
function with a **first-class struct-value** param/return (e.g.
`%S = type <{ i64, i32, [4 x i8] }>`, `declare i1 @Less(%S, %S)`), and LLVM's
backend lowers a first-class struct param by **expanding it field-per-register**
(i64→x0, i32→x1, `[4 x i8]`→x2..x5 ⇒ 6 regs), so the 2nd struct arg starts at x6,
not x2. They disagree → silent corruption. The comment at
`common_callconv.bn:25` ("clang emits `[2 x i64]`") describes clang's *C
frontend*; codegen never performs that coercion.

## Fix: coerce at the LLVM boundary only

Pass/return an **in-register aggregate** (`IsAggregateTyp(t) && !t.IsByvalParam()`,
i.e. `1 ≤ SizeOf ≤ 16`) using the ABI type `[N x i64]` (N=⌈SizeOf/8⌉) instead of
the first-class struct. The function BODY is untouched — it keeps the `%S` SSA
value (field access is `extractvalue %S …`). Coercion is local to three
boundaries via the alloca/store/load idiom (the struct and `[N x i64]` share the
same N*8-byte memory; allocas are entry-hoisted, so add to the hoist set):

- **Param (callee side).** Declare the incoming param as `[N x i64]` named
  `%vID.ag`; in the entry block reconstruct the struct value the body expects:
  ```
  %vID.agp = alloca %S
  store [N x i64] %vID.ag, ptr %vID.agp
  %vID = load %S, ptr %vID.agp
  ```
  Now `%vID` is the `%S` value every body reference already uses.
- **Return (callee side).** Declare return type `[N x i64]`; at OP_RETURN coerce
  the `%S` result value `%r`:
  ```
  %r.agp = alloca %S
  store %S %r, ptr %r.agp
  %r.ag = load [N x i64], ptr %r.agp
  ret [N x i64] %r.ag
  ```
- **Call site (caller side, LLVM caller only).** For each in-register-aggregate
  arg, coerce `%S`→`[N x i64]` (store struct to a `.agarg<i>` alloca, load
  `[N x i64]`) and pass `[N x i64]`. For an in-register-aggregate result, the
  call returns `[N x i64]`; store it to a `.agret` alloca and `load %S` to
  produce the result value the rest of the IR consumes.

`N*8` may exceed `SizeOf` (e.g. a 12-byte struct → N=2 → 16): the alloca is
`%S`-typed (its true size, ≥... no — must be ≥ N*8). **Make the coercion alloca
`[N x i64]`-typed** (always N*8 bytes) and bitcast/GEP for the struct view, so
neither the `store [N x i64]` nor the `load [N x i64]` over-runs. Concretely: use
one `[N x i64]` alloca; `store`/`load` the `[N x i64]` directly, and
`store %S`/`load %S` to/from the *same* pointer (LLVM allows differently-typed
load/store through one `ptr` since opaque pointers). The alloca is N*8 ≥ SizeOf,
so the `%S` access stays in-bounds.

## Touch points (all in `pkg/binate/codegen`)

1. `emit_util.bn :286 writeParamTypeLLVM` — in-register aggregate ⇒ emit
   `[N x i64]` (today emits `llvmType(t)`; the `IsByvalParam` `ptr` branch
   stays). Add a helper `aggCoerceLLTy(t) -> "[N x i64]"`.
2. `emit_debug.bn :44-86 emitFuncDbg` — (a) `retTyp`: in-register aggregate
   single-result ⇒ `[N x i64]`; (b) after `(...) {` and the entry alloca decls,
   emit the **param-coercion prologue** for each in-register-aggregate param
   (rename the param ref to `%vID.ag`, reconstruct `%vID`). The entry alloca for
   `%vID.agp` must be hoisted (emitEntryAllocaDecls / the alloca-hoist pass).
3. `emit.bn :230-257 declare` (extern decls) — return type for an in-register
   aggregate ⇒ `[N x i64]` (params already route through writeParamTypeLLVM).
   Also `emit.bn :282-301 funcRetTypes` — record `RetType = "[N x i64]"` for an
   in-register aggregate so call sites use the coerced type.
4. `emit_instr.bn :229 OP_RETURN` (and emit.bn's emitReturn router) — when the
   function returns an in-register aggregate (NOT sret), coerce `%S`→`[N x i64]`
   and `ret [N x i64]`.
5. `emit_call.bn emitCall` — (a) arg coercion: in-register-aggregate args
   `%S`→`[N x i64]` before the call line (mirror the existing byval pre-emit
   loop at :48); (b) result: when the callee returns an in-register aggregate,
   the call yields `[N x i64]`; store→`load %S` to bind the result ref. The
   sret path (>16) is unchanged.
6. Audit the parallel signature emitters that also call writeParamTypeLLVM /
   emit a callee signature: `emit_impls.bn :405` (impl thunks), `emit_iface_call.bn`,
   `emit_funcvals_*` (these use the i8*-pointer shim ABI for aggregates — a
   SEPARATE, internally-consistent convention; confirm they still agree with the
   native func-value path and need NO change). Iface method calls (`emit_iface_call.bn`)
   go through a uniform shim too — verify.

## What does NOT change

- The native backends (`pkg/binate/native/**`) — already pack `[N x i64]`.
- `pkg/types` layout (`SizeOf`/`FieldOffset`/`IsByvalParam`) — correct.
- The **C-extern ABI** (`CExternSretBytes`, C struct returns/params) — untouched;
  coercion applies to INTERNAL Binate calls only. (Do NOT fold this into a
  "lower all thresholds to 0 / make everything indirect" change — that would
  break C interop for small-struct-by-value params/returns and regress perf. The
  coercion approach keeps the in-register ABI the design intends.)
- The `>16` byval (params) / sret (returns) paths — unchanged.
- builder-comp / VM: both caller and callee change in lockstep, so pure-LLVM
  still agrees; the VM path doesn't use this LLVM-text lowering.

## Tests / validation

- Add the minimal `pkg/tt` repro (saved `/tmp/aa64_xpkg_saved/`) as a conformance
  directory test (`main.bn` + `pkg/tt.bni` + `pkg/tt/tt.bn` + `expected`) —
  cross-package struct-by-value `{i64,i32}`. Also add a `{i32,i32}` (8-byte,
  sub-word) and a `{i8,i64}` (leading-pad) variant — the shapes the first-class
  expansion breaks differently.
- Must pass on `builder-comp`, `builder-comp-int`, `builder-comp_native_aa64-comp_native_aa64`,
  and (via CI) `…native_x64…`.
- Re-run the two failing stdlib time tests under native aa64 (`855_std_time`,
  `stdlib/time/001_negative_pre_epoch`) — both should pass with NO xfail.
- Unit tests: every `pkg/binate/codegen` package test; gen1/gen2 self-host.
- Disassembly spot-check: `tt.Less` now reads `q` from x2/x3 (not x6).
- BUILDER constraint: codegen is in cmd/bnc's BUILDER tree — keep all new code
  BUILDER-compilable (no closures/generics/etc. beyond what BUILDER accepts).

## Implementation grounding (2026-06-20 — verified against the tree, ready to execute)

Recon confirmed every touch point against current `pkg/binate/codegen`; the
existing **byval-spill machinery is the exact template** to mirror
(`writeByvalArgPreamble` + `emitByvalAllocDecls` + the `emitEntryAllocaDecls`
Op-dispatch hoist + `writeByvalArgLLVM`). Concrete recipe:

**Predicate + helpers (`emit_util.bn`).** No new `types` method needed — reuse
the existing `isAggregateForStore(t)` (emit_copy_ssa.bn:261, the 8 aggregate
kinds) and `IsByvalParam` (>16 ⇒ indirect):
```
func isInRegAggregate(t @types.Type) bool { return isAggregateForStore(t) && !t.IsByvalParam() }
func aggCoerceNWords(t @types.Type) int   { return (t.SizeOf() + 7) / 8 }
func writeAggCoerceLLTy(out *strings.Builder, t @types.Type) { out.Write("["); stringutils.WriteInt(out, aggCoerceNWords(t)); out.Write(" x i64]") }
func aggCoerceLLTy(t @types.Type) @[]char { var b @strings.Builder = strings.NewBuilder(); writeAggCoerceLLTy(b, t); return buf.CopyStr(b.String()) }
```
(emit_util already imports buf/stringutils/strings/types; codegen is
BUILDER-compiled, so this stays within the subset — fine.) **Scope note:** this
covers ALL in-register aggregates, not just structs; a clean `{ptr,i64}` (raw
slice / func-value / iface-value) already expands to exactly N=2 regs so the
coercion is a safe round-trip no-op there, while struct/array with sub-i64
fields or explicit `[K x i8]` padding is where LLVM's first-class expansion
desyncs from native `[N x i64]`.

**Param sites — ONE edit covers both.** `writeParamTypeLLVM` (emit_util:286) is
called by BOTH the `define` (emit_debug:83) and `declare` (emit.bn:255). Add a
branch after the `IsByvalParam→ptr` branch: `if isInRegAggregate(t) {
writeAggCoerceLLTy(out,t); return }`.

**Param reconstruction (emit_debug `emitFuncDbg`).** The body references a param
as `%v<ID>` (emitRef of `f.Params[i].ID`). With the signature param now
`[N x i64]`, rename the SIGNATURE ref to `%v<ID>.ag` and, at the TOP of the entry
block (after `emitEntryAllocaDecls`, before block instrs — new prologue loop over
in-reg-agg params): `store [N x i64] %v<ID>.ag, ptr %v<ID>.agp` then `%v<ID> =
load <%S>, ptr %v<ID>.agp`. The `%v<ID>.agp = alloca [N x i64]` must be hoisted —
add a per-param pass in `emitEntryAllocaDecls` (it currently scans instr Ops; the
param allocas are function-level, so emit them once up front from `f.Params`).

**Return type.** `emit_debug:51` (`retTyp`) and `emit.bn:236` (declare) and
`emit.bn:290` (`funcRetTypes[].RetType`) all do `llvmType(f.Results[0])` for the
single-result case — coerce each to `aggCoerceLLTy` when
`isInRegAggregate(f.Results[0]) && !needsSret(...)`. Coercing `funcRetTypes`
makes the call site's `lookupRetType` return `[N x i64]` for free.

**OP_RETURN (emit_instr:229 + emit.bn `emitReturn` router :33).** When the func
returns an in-reg aggregate (NOT sret): `store <%S> %r, ptr %r.agp`; `%r.ag =
load [N x i64], ptr %r.agp`; `ret [N x i64] %r.ag` (hoist `%r.agp`).

**Call site — THE WRINKLE (do this carefully).** `writeByvalArgPreamble` /
`writeByvalArgLLVM` (emit_util) are shared by emit_call, emit_call_handle,
emit_call_indirect, emit_impls. The DIRECT path (emit_call, emit_impls thunks)
needs the coercion; the FUNC-VALUE / indirect path (emit_call_funcvalue and the
`*_handle`/`*_indirect` shim ABI) uses a SEPARATE i8*-pointer convention that is
internally consistent and must stay untouched — so do NOT blanket-add in-reg-agg
coercion to the shared writeByvalArg* helpers without confirming each caller is a
direct (mangled-symbol) call vs a shim call. For the direct path:
- arg `i` (in-reg-agg): preamble `store <%S> %v<argID>, ptr %v<callID>.agarg<i>`;
  `%v<callID>.agarg<i>.ld = load [N x i64], ptr %v<callID>.agarg<i>`; pass
  `[N x i64] %v<callID>.agarg<i>.ld`.
- result (in-reg-agg): the call already returns `[N x i64]` (via funcRetTypes) —
  bind it to `%v<ID>.agret`, then `store [N x i64] %v<ID>.agret, ptr %v<ID>.agp`;
  `%v<ID> = load <%S>, ptr %v<ID>.agp`. Hoist `%v<callID>.agarg<i>` + `%v<ID>.agp`
  via the OP_CALL branch in `emitEntryAllocaDecls`.

**Allocas are `[N x i64]`-typed** (N*8 ≥ SizeOf) and accessed as both `[N x i64]`
and `<%S>` through the one opaque `ptr` — neither access over-runs.

**Validation order (cheap→expensive):** codegen unit pkgs → `builder-comp` +
`builder-comp-int` conformance (lockstep ⇒ must stay GREEN; proves IR valid + no
pure-LLVM regression) → `builder-comp_native_aa64-comp_native_aa64` (the two time
tests RED→GREEN, no xfail) → gen1/gen2 self-host → x64 native via CI. Add the
`{i64,i32}` / `{i32,i32}` / `{i8,i64}` cross-package conformance repros.

## Follow-up

- Re-check the sibling MAJOR `extractvalue`-on-scalar-i64 entry — likely the same
  family (codegen mis-lowering a by-value cross-pkg struct result); this coercion
  may fix or interact with it. Verify `conformance/stdlib/os/004_modtime_chain`.
