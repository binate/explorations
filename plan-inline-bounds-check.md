# Plan: (2a) Inline the bounds-check fast-path in the compiled backends

Status: REVISED after adversarial review (see "Adversarial review — incorporated"
below). Awaiting user go-ahead to implement.
Todo: `claude-todo.md` → "(2a) Inline the bounds-check fast-path in gen — 🔵 IN PROGRESS".

## Goal

Replace the per-access `call rt.BoundsCheck(idx, len)` that the **compiled** backends
emit for every array/slice index with an **inline fast-path**: an in-line compare
sequence + a not-taken branch, calling a fail helper only on the (never-taken-in-a-
correct-program) out-of-bounds path. This removes an opaque cross-TU call round-trip
from the hot path of every checked access.

Why it matters (from the profiling in `claude-todo.md`): `rt.BoundsCheck` is a CALL for
**both** backends (bnc+clang is genuine separate compilation — no LTO — so clang can't
inline it across the `rt` TU boundary either), ~25% of native-compile self-time.
Inlining is **TU-independent**: it helps every program on every backend, and it
specifically closes the single-file case where clang currently wins by bundling +
inlining `rt`. It is the same model the codebase already uses for `RefInc`/`RefDec`
(`emitRefIncInline`/`emitRefDecInline`).

## Adversarial review — incorporated

An adversarial review of the first draft (commit `016bdf40`) found one native
build-break and refuted two of the draft's correctness arguments. Changes made:

- **Native fresh labels (was Risk 4, now a build-break fix).** The draft used
  `boundsLabel(ins.ID)`. `OP_BOUNDS_CHECK` is a **void** instr with `ID == -1`
  (`ir.bn:220` `newVoidInstr`; asserted at `ir_ops_test.bn:192`,
  `x64_dispatch_test.bn:806`), so every check would emit the same label `Lbc_-1` →
  `Assembler.DefineLabel` duplicate-label error (`asm.bn:282`) → assembly fails
  (`assemble.bn:53`) at the **second** checked access in any module. Native has **no**
  fresh-label counter. Fix: add a per-function monotonic counter (below). LLVM is
  unaffected — it uses the per-function `tmpSeq`, not `ins.ID`.
- **Compare form → two-compare signed (was the single unsigned compare).** The draft's
  `len >= 0` invariant is **false**: slice range checks reuse `OP_BOUNDS_CHECK` and pass
  **`hi+1`** (a user value, `gen_slice.bn:217`) as the length operand. The single
  unsigned compare stays sound only via an undocumented "`hi <= len` check runs before
  the `lo` check" ordering (`gen_slice.bn:213` before `:217`) — fragile. The
  **two-compare signed form `idx < 0 || idx >= len` is byte-identical to `rt.BoundsCheck`**
  (`rt_managed.bn:168`), immune to the `hi+1` / negative-len / overflow subtleties, and
  **still delivers the entire win** (the call is what's expensive, not the extra
  compare). This is now the default. Single-compare is offered as an explicit user
  opt-in only (see Open decision). clang `-O2` folds two-compare → one unsigned compare
  anyway, so the LLVM standard-build codegen is unchanged either way.
- **Deleted the "reg-map / drop `ResetRegs`" section.** Its premise was wrong: the
  native driver calls `spillAndReset` after **every** instruction
  (`x64_emit_func.bn:90`) under a spill-everything policy (`common.bn:114`), so the
  in-dispatch `ResetRegs()` is redundant and keeping/dropping it is correctness- and
  perf-neutral. There is no "keep idx/len live across the op" win to capture. The real
  native win is narrower and real: **no `Call` + no arg-marshaling `Mov`s on the hot
  path** (the call round-trip — call/ret + BoundsCheck's own prologue/epilogue + its
  internal branch — is what the profile charged ~25% to).
- **`isCallOp`, not `isRuntimeGuardCall`.** The draft named a nonexistent function. The
  real one is `isCallOp` (`common_call.bn:280`), which governs **PlanFrame
  outgoing-arg sizing**, not clobbering. `OP_BOUNDS_CHECK` **must stay** in `isCallOp`
  and in the arg-type dispatch (`common_call.bn:~250`), because the cold path still
  calls `rt.BoundsFail` (same `(int,int)` signature → same 0-byte outgoing-arg size).
  **Leave `common_call.bn` unchanged.**
- **Native tests are rewrites, not extensions.** `TestEmitBoundsCheckCallsRuntime`
  (x64 `x64_dispatch_test.bn:791`; aarch64 `aarch64_emit_test.bn:278`) assert the CALL
  form (tail byte `0xE8`, `BoundsCheck` global) and must be **rewritten**. arm32 has no
  such test — a new one is required.
- **Native fail path needs `a.SetGlobal(BoundsFail)`** before the call, or the
  relocation is missing → link failure (the current code does this for `BoundsCheck`).

Review confirmations kept as-is: `rt.BoundsFail` gets its LLVM `declare` with **no
extra work** (import registration is ungated — `mod.AddFunc` for every rt decl,
`gen_import.bn`; BoundsFail is already declared-but-unused today); all needed condition
codes exist; the diagnostic is byte-identical (same sink); operand widths match (both
`TypInt()`, no extension); LLVM control-flow / active-block continuation is correct.

## Scope — what changes and what does NOT

Changes: the `OP_BOUNDS_CHECK` **lowering** in the four compiled backends, plus a
per-function label counter for the three native backends.
- LLVM: `pkg/binate/codegen/emit_instr.bn:421` (the `OP_BOUNDS_CHECK` case).
- native x64: `pkg/binate/native/x64/x64_dispatch.bn:316`.
- native aarch64: `pkg/binate/native/aarch64/aarch64_dispatch.bn:357`.
- native arm32: `pkg/binate/native/arm32/arm32_dispatch.bn` `emitBoundsCheck` (~348).

Does NOT change:
- **IR-gen** (`gen_access.bn`): `OP_BOUNDS_CHECK` stays a single IR op (backend-only —
  see Design decision).
- **`common_call.bn`**: `isCallOp` + arg-type dispatch keep `OP_BOUNDS_CHECK` (cold path
  still calls `rt.BoundsFail`; frame sizing must account for it).
- **The VM** (`vm_exec_helpers.bn:285`, `BC_BOUNDS_CHECK`): already inline
  (`if bcIdx < 0 || bcIdx >= bcLen { setFault }`); its recoverable-fault-pad dispatch is
  untouched. (2a) is a no-op for the VM.
- **The IR-level BCE passes** (`bceConstIndex`/`bceLoop`): still delete `OP_BOUNDS_CHECK`
  before the backend; the inline lowering fires only for survivors. No interaction.
- **Fault pads / fault semantics**: compiled backends already ignore `Func.FaultPads`
  and are fatal on a bounds fault; they stay fatal with a **byte-identical** diagnostic
  (the fail path calls the same sink `rt.BoundsCheck` used). `attachFaultPad` (IR level,
  VM-only) untouched.

## Facts established by recon

- `rt.BoundsCheck(index, length)` = `if index < 0 || index >= length { BoundsFail(index, length) }`
  (`rt_managed.bn:168`). `rt.BoundsFail(index, length)` = **noreturn** abort with
  `"runtime error: index out of bounds: <i> (len <n>)\n"` (`rt_diag.bn`). Both exported
  in `ifaces/core/pkg/builtins/rt.bni` (BoundsFail:174), and `pkg/builtins/rt` is always
  imported into every compiled module (`compile_imports.bn` `appendRtImport`).
- `rt.BoundsFail`'s LLVM `declare` needs **no** work — import registration is
  reference-independent (`gen_import.bn` `mod.AddFunc` for every rt decl; declare loop
  `emit.bn:189`); it is already emitted (unused) today. Precedent: `rt.ZeroRefDestroy`,
  reached only from `emitRefDecInline`, links the same way.
- Condition codes all present: x64 `CC_L=12` (signed `<`), `CC_GE=13`
  (`asm/x64.bni`); aarch64 `COND_LT`/`COND_GE`; arm32 `COND_LT`/`COND_GE`. (The
  two-compare form needs only signed less-than / greater-equal, all present.)
- Primitives (used today for `OP_BRANCH`): x64 `Cmp`/`Jcc`/`Jmp` + `a.DefineLabel`;
  aarch64 `Cmp`/`Bcond`/`Bl`; arm32 `Cmp`/`B`/`Bl`. x64 `Cmp` takes an immediate
  (`TestCmpRegImm`), so `Cmp(idx, 0)` is available.
- Operands are both `TypInt()` (`EmitSliceLen` = extract(slice,1,TypInt);
  `EmitConstInt(arrayLen, TypInt())`), no sign/zero extension — equal-width compares.

## Design decision: backend-level, single IR op

`OP_BOUNDS_CHECK` remains one IR op; each compiled backend changes how it lowers it.
Mirrors `OP_REFINC`/`OP_REFDEC`. Rejected — expanding to compare+branch+call IR in
`gen_access.bn`: breaks the IR BCE passes (they match the single op), complicates the
VM fault-pad model, loses the compact form.

## Correctness spine (two-compare form)

1. **Two signed compares, exactly `rt.BoundsCheck`.** Emit the fault condition as
   `idx < 0 || idx >= len` (signed), byte-identical to `rt.BoundsCheck`. No `len >= 0`
   assumption, no dependence on slice-check ordering, correct even for the `hi+1` slice
   length operand and for any (UB) corrupted negative length.
2. **Fail path = identical abort.** Cold path calls `rt.BoundsFail(idx, len)` — the same
   sink → identical diagnostic + `abort`. On LLVM, followed by `unreachable`.
3. **Hot path is cheaper.** Two compares + two branches, **no call round-trip**, no
   argument marshaling into arg registers on the taken (in-bounds) path.
4. **Operand materialization bail preserved.** Keep each backend's
   `if idx < 0 || ln < 0 { return }` guard for non-materializable operands.

Open decision (surfaced for the user): the **single unsigned compare**
`(unsigned)idx >= (unsigned)len` shaves one compare+branch off the hot path but is
sound only under `len >= 0`, which for slice checks holds only via the current
`hi`-before-`lo` check ordering. Default here is the safe two-compare form (honors the
todo's "preserve exact fault semantics" and CLAUDE.md semantics-conservatism). Say the
word to switch to single-compare and I'll document the ordering dependency at each site.

## Per-function fresh-label counter (native — new infra)

The three native backends need object-unique local labels for the fail/ok targets.
`ins.ID` is unavailable (−1 for void ops) and per-function even for value ops; the
assembler `a` spans the whole object, so labels need object-scope uniqueness.

Add a per-function monotonic counter to the native codegen state threaded to
`emitInstr` (the `RegMap` is already threaded to every native `emitInstr` and is
per-function — confirm its lifetime and add a `LabelSeq int` field there, else add a
tiny per-function context). Emit labels namespaced by `funcSym` + the counter, e.g.
`L<funcSym>_bcf_<n>` (fail) and `L<funcSym>_bco_<n>` (ok) from one `n` per check —
`funcSym` namespacing matches how block labels already avoid cross-function collisions
(`blockLabel(funcSym, …)`). Lands with the first native backend commit; shared by all
three. A small helper in `pkg/binate/native/common` builds the names.

## Per-backend implementation

### LLVM (lowest risk — mirror `emitRefIncInline`; unaffected by the label fix)

New file `pkg/binate/codegen/emit_bounds.bn` with `emitBoundsCheckInline(out, instr)`
(keeps `emit_instr.bn` from growing; matches the `emit_refcount.bn` split). Replace the
`OP_BOUNDS_CHECK` case body at `emit_instr.bn:421` with a call to it. Emit (`seq` from
`tmpSeq`, like `emitRefIncInline`; `<int>` = `intLL()`):

```
%bc.<seq>.neg = icmp slt <int> <idx>, 0
%bc.<seq>.oob = icmp sge <int> <idx>, <len>
%bc.<seq>.bad = or i1 %bc.<seq>.neg, %bc.<seq>.oob
br i1 %bc.<seq>.bad, label %bc.<seq>.fail, label %bc.<seq>.ok
bc.<seq>.fail:
  call void @<rtSym BoundsFail>(<int> <idx>, <int> <len>)
  unreachable
bc.<seq>.ok:
```

Active LLVM block on return is `bc.<seq>.ok` (subsequent emissions fall into it) — same
discipline as `emitRefIncInline`'s `.skip`. clang `-O2` folds the two `icmp`s into a
single unsigned compare and can hoist the loop-invariant compare (impossible with the
opaque call), so the standard build wins; the `bnc -O0` IR benefits directly.

### native x64 (`x64_dispatch.bn:316`)

```
Cmp(a, Reg(idx, SZ64), Imm(0));  Jcc(a, CC_L, failLabel)   ; idx < 0  → fail
Cmp(a, Reg(idx, SZ64), Reg(len, SZ64)); Jcc(a, CC_L, okLabel) ; idx < len → in bounds
; failLabel reached iff idx>=len (fallthrough) or idx<0 (branch):
DefineLabel(failLabel)
Mov(RSI <- len); Mov(RDI <- idx)       ; same arg order as today (RSI first)
a.SetGlobal(BoundsFail); Call(BoundsFail)   ; noreturn
DefineLabel(okLabel)
```

`failLabel`/`okLabel` from the per-function counter. Because `BoundsFail` is noreturn,
defining `okLabel` right after the call is correct. `ResetRegs()` neutral (spillAndReset
follows) — keep or drop; no behavior change.

### native aarch64 (`aarch64_dispatch.bn:357`)

```
Cmp(a, true, idx, Imm 0);   Bcond(a, COND_LT, failLabel)
Cmp(a, true, idx, Reg(len)); Bcond(a, COND_LT, okLabel)
DefineLabel(failLabel)
Mov(X0 <- idx); Mov(X1 <- len); a.SetGlobal(BoundsFail); Bl(BoundsFail)
DefineLabel(okLabel)
```

(Confirm aarch64 `Cmp` immediate-form for the `,#0` compare; if none, `Cmp(idx, Reg(zr))`
or materialize 0.)

### native arm32 (`arm32_dispatch.bn` `emitBoundsCheck`)

```
Cmp(COND_AL, idx, Imm 0);   B(COND_LT, failLabel)
Cmp(COND_AL, idx, Reg(len)); B(COND_LT, okLabel)
DefineLabel(failLabel)
Mov(R0 <- idx); Mov(R1 <- len); a.SetGlobal(BoundsFail); Bl(COND_AL, BoundsFail)
DefineLabel(okLabel)
```

## Tests (land WITH the change — Bug-Discovery-Protocol)

- **LLVM** `emit_bounds_test.bn` (new): assert the emitted text has `icmp slt … 0`,
  `icmp sge`, an `or`, a `.fail` block with `call … BoundsFail` + `unreachable`, a `.ok`
  label, and **no** `call … BoundsCheck`. Mirror `emit_refcount_test.bn`.
- **native x64 / aarch64**: **rewrite** `TestEmitBoundsCheckCallsRuntime`
  (`x64_dispatch_test.bn:791`, `aarch64_emit_test.bn:278`) — they currently assert the
  CALL form. New assertions: two compares + two conditional branches + a
  `Call/Bl BoundsFail` registered global, and **no** `BoundsCheck` call. Also assert two
  checks in one function get **distinct** labels (regression guard for the `Lbc_-1`
  collision).
- **native arm32**: **new** dispatch test (none exists) with the same assertions.
- **Conformance — semantics preserved (critical)**:
  - Out-of-bounds tests must still abort on every compiled mode with a **byte-identical**
    message. `314_err_slice_neg_lo` already covers the negative-index (`-1 (len 3)`)
    direction; confirm `310`/`311` (index `>= len`) too. Diff the diagnostic text.
  - VM mode (`builder-comp-int`) is a **regression guard** — must be unchanged.
  - A tight in-bounds indexing loop confirms the hot (skip) path: no fault, correct
    values.

## Verification / build

- **Unit tests for every changed package** (smoke-every-changed-package; native shared
  files feed all backends): `codegen`, `native/x64`, `native/aarch64`, `native/arm32`,
  `native/common` (the label helper).
- **Conformance**: `builder-comp` (LLVM), `builder-comp-int` (VM regression guard), and
  the runnable native modes — `builder-comp_native_x64_darwin` and/or
  `builder-comp_native_aa64`. **CAUTION**: the LLVM arm32 modes (`builder-comp_arm32_*`,
  no `native`) do NOT exercise `native/arm32`; validate arm32 via its unit test and (if
  runnable) `builder-comp_native_arm32_baremetal`.
- **Benchmark**: bnc compiling bnc, `--backend native` at `-O2` and `-O0`, before/after,
  to quantify the call-removal. Report honestly (mem2reg framing lesson).

## Staging / landing

1. **LLVM** — independently valuable (closes the single-file gap), no new infra, lowest
   risk, most testable.
2. **native label counter + x64** — the fresh-label infra lands here.
3. **native aarch64**. 4. **native arm32**.
Each commit self-contained + green. Per-instance cherry-pick approval per CLAUDE.md.

## Remaining risks (post-review)

1. **Label counter lifetime/uniqueness** — verify the chosen per-function counter is
   reset per function and its labels are object-unique with `funcSym` namespacing; the
   "two checks → distinct labels" unit test guards this.
2. **aarch64/arm32 `Cmp #0` immediate form** — confirm the assembler supports a compare
   against an immediate 0, else compare against a zero register / materialized 0.
3. **Diagnostic byte-identity** — same sink → identical by construction; verify in a
   conformance diff anyway.
4. **DivCheck / ShiftCheck** — same call pattern, explicitly **out of scope** for (2a);
   noted as a natural follow-up, not scope-crept.
