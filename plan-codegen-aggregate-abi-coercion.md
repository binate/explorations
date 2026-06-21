# Plan: codegen `[N x i64]` ABI coercion for in-register aggregates

Status: **READY TO IMPLEMENT (2026-06-20)**. Root cause confirmed by disassembly
+ emitted LLVM IR (see claude-todo.md MAJOR entry). Fixes the native↔LLVM
struct-by-value miscompile on BOTH native backends. Codegen-only; the native
backends and `pkg/types` layout are correct and stay untouched.

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

## Follow-up

- Re-check the sibling MAJOR `extractvalue`-on-scalar-i64 entry — likely the same
  family (codegen mis-lowering a by-value cross-pkg struct result); this coercion
  may fix or interact with it. Verify `conformance/stdlib/os/004_modtime_chain`.
