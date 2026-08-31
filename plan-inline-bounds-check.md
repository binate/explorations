# Plan: (2a) Inline the bounds-check fast-path in the compiled backends

Status: DRAFT — awaiting adversarial review, then user go-ahead to implement.
Todo: `claude-todo.md` → "(2a) Inline the bounds-check fast-path in gen — 🔵 IN PROGRESS".

## Goal

Replace the per-access `call rt.BoundsCheck(idx, len)` that the **compiled** backends
emit for every array/slice index with an **inline fast-path**: a single unsigned
compare + a not-taken branch, calling a fail helper only on the (never-taken-in-a-
correct-program) out-of-bounds path. This removes an opaque cross-TU call from the
hot path of every checked access.

Why it matters (strategic frame from the profiling in `claude-todo.md`): `rt.BoundsCheck`
is a CALL for **both** backends (bnc+clang is genuine separate compilation — no LTO —
so clang can't inline it across the `rt` TU boundary either), ~25% of native-compile
self-time. Inlining is **TU-independent**: it helps every program on every backend,
and it specifically closes the single-file case where clang currently wins by bundling
+ inlining `rt`. It is also the exact model the codebase already uses for `RefInc` /
`RefDec` (`emitRefIncInline` / `emitRefDecInline`).

## Scope — what changes and what does NOT

Changes: the `OP_BOUNDS_CHECK` **lowering** in the four compiled backends:
- LLVM: `pkg/binate/codegen/emit_instr.bn:421` (the `OP_BOUNDS_CHECK` case).
- native x64: `pkg/binate/native/x64/x64_dispatch.bn:316`.
- native aarch64: `pkg/binate/native/aarch64/aarch64_dispatch.bn:357`.
- native arm32: `pkg/binate/native/arm32/arm32_dispatch.bn` `emitBoundsCheck` (~348).

Does NOT change:
- **IR-gen** (`gen_access.bn`): `OP_BOUNDS_CHECK` stays a single IR op. The todo's
  "and/or `gen_access.bn`" is resolved to **backend-only** (see Design decision).
- **The VM** (`pkg/binate/vm/vm_exec_helpers.bn:285`, `BC_BOUNDS_CHECK`): it is
  **already inline** — `if bcIdx < 0 || bcIdx >= bcLen { setFault(...) }`, no call.
  Its recoverable-fault-pad dispatch stays exactly as is. (2a) is a no-op for the VM.
- **The IR-level BCE passes** (`bceConstIndex` / `bceLoop` in `opt.bn`): they still
  pattern-match and delete `OP_BOUNDS_CHECK` before the backend runs; the inline
  lowering only fires for checks that survive them. Fewer ops to inline — no interaction.
- **Fault pads / fault semantics**: compiled backends already ignore `Func.FaultPads`
  and are *fatal* on a bounds fault (they abort). They stay fatal, with a **byte-
  identical** diagnostic, because the fail path calls the very sink `rt.BoundsCheck`
  already used. `genBoundsCheck`'s `attachFaultPad` (IR level, VM-only consumer) is
  untouched.

## Facts established by recon (the load-bearing ones)

- `rt.BoundsCheck(index, length)` = `if index < 0 || index >= length { BoundsFail(index, length) }`.
  `rt.BoundsFail(index, length)` = **noreturn** abort with
  `"runtime error: index out of bounds: <i> (len <n>)\n"`. Both are **exported in
  `ifaces/core/pkg/builtins/rt.bni`** (BoundsFail at line 174), and `pkg/builtins/rt`
  is **always imported into every compiled module** (`cmd/bnc/compile_imports.bn`
  `appendRtImport`).
- **Precedent — `emitRefDecInline`**: the inline RefDec fast path calls a *different*
  rt helper, `rt.ZeroRefDestroy`, on its slow (destroy) path. Nothing in Binate calls
  `ZeroRefDestroy` directly, yet its `declare` is emitted and it links on every
  backend. `rt.BoundsFail` is the exact analogue — an rt export reached only from
  inline backend emit code. If `ZeroRefDestroy` links, `BoundsFail` links.
- x64 already defines the unsigned condition codes we need: `CC_AE int = 3`
  (unsigned `>=`) and `CC_B int = 2` (unsigned `<`) in `pkg/binate/asm/x64.bni`. No new
  x64 CC constant required. (arm32 `COND_HS`/`COND_LO` and aarch64 `HS`/`LO` for
  `Bcond` to be confirmed present; add if missing — trivial constants.)
- Each backend already has the primitives (used for `OP_BRANCH`): x64 `Cmp` / `Jcc` /
  `Jmp` + `a.DefineLabel`; aarch64 `Cmp` / `Bcond` / `Bl`; arm32 `Cmp` / `B` / `Bl`.
  Local labels are `L`-prefixed (not relocated symbols), e.g. `common.StringLabel(id)`
  → `Lstr_<id>`; we add an analogous `Lbc_<id>`.

## Design decision: backend-level, single IR op (Option A)

`OP_BOUNDS_CHECK` remains **one IR op**; each compiled backend changes how it lowers
it, from a call to an inline compare+branch+fail-call. This exactly mirrors
`OP_REFINC` / `OP_REFDEC` (single ops, inlined per-backend).

Rejected — Option B (expand to compare+branch+call **IR** in `gen_access.bn`): it
would break the IR-level BCE passes (they match the single op), complicate the VM's
fault-pad model (the pad attaches to the faulting op), and lose the compact single-op
form. No upside.

## Correctness spine

1. **The single unsigned compare.** `(unsigned)idx >= (unsigned)len` is exactly
   equivalent to `idx < 0 || idx >= len` **iff `len >= 0`**: a negative `idx`
   reinterpreted as unsigned is `>= 2^(W-1) > len` for any non-negative `len`, so it
   is caught; a non-negative `idx` compares identically signed and unsigned.
   `len` is **non-negative by construction** — it is either `EmitConstInt(arrayLen)`
   (a fixed array extent, always `>= 0`) or `EmitSliceLen` (a slice/managed-slice
   length word, `>= 0` in any well-formed program). This invariant is the entire
   soundness argument and MUST be stated in a code comment at each site. (The native
   backends' *existing* comments already describe `rt.BoundsCheck` as an "unsigned
   comparison", so this matches the documented intent.)
   - **Fallback if the invariant is doubted** (see Risk 1): use the two-compare signed
     form `idx < 0 || idx >= len`, which matches `rt.BoundsCheck` byte-for-byte at the
     cost of one extra compare+branch. This is a decision for the review.
2. **Fail path = identical abort.** The cold path calls `rt.BoundsFail(idx, len)`,
   the same sink `rt.BoundsCheck` uses → identical diagnostic + `abort`. On LLVM the
   call is followed by `unreachable` (BoundsFail never returns).
3. **Hot path is cheaper.** No call, no argument marshaling into arg registers, no
   caller-saved-register clobber on the taken (in-bounds) path. Operands are compared
   in whatever registers `getOperand` already put them in; only the cold fail path
   moves them into arg registers.
4. **Operand materialization bail preserved.** Every backend currently bails
   `if idx < 0 || ln < 0 { return }` when an operand isn't materializable; keep that.

## The register-map subtlety (native backends — the sharp edge)

Today each native lowering ends with `rm.ResetRegs()` **because it emits a call** (a
call clobbers caller-saved registers, so the reg allocator must forget them). After
inlining, **the hot (fall-through) path has no call.** The fail path is **noreturn**,
so it never rejoins normal control flow. Therefore the reg-map state that must hold
after the op is the **OK path's** state, in which **nothing was clobbered**.

⇒ The inline lowering must **NOT** `rm.ResetRegs()` for the fall-through path. Dropping
the reset is both a **correctness** statement (the post-op reg map correctly reflects
that idx/len and other caller-saved values are still live) and the source of the perf
win (no spill/reload around a now-callless hot path). Getting this wrong in either
direction is a miscompile risk, so it is called out per-backend and is a primary
review target.

Related: `pkg/binate/native/common/common_call.bn:287` `isRuntimeGuardCall` currently
returns true for `OP_BOUNDS_CHECK` (marking it as a call for reg-alloc/clobber
modeling). After inlining, `OP_BOUNDS_CHECK` is no longer a call on the hot path;
audit every consumer of `isRuntimeGuardCall` and decide whether `OP_BOUNDS_CHECK`
should drop out of it (so the allocator stops modeling a hot-path clobber). This is
the same point as the `ResetRegs` decision, viewed from the allocator side — resolve
them together and consistently.

## Per-backend implementation

### LLVM (lowest risk — mirror `emitRefIncInline`)

New file `pkg/binate/codegen/emit_bounds.bn` with `emitBoundsCheckInline(out, instr)`
(keeps `emit_instr.bn` from growing; matches the `emit_refcount.bn` split). Replace the
`OP_BOUNDS_CHECK` case body at `emit_instr.bn:421` with a call to it. Emit (seq from
`tmpSeq`, exactly like `emitRefIncInline`; `<int>` is `intLL()`):

```
%bc.<seq>.oob = icmp uge <int> <idx>, <len>
br i1 %bc.<seq>.oob, label %bc.<seq>.fail, label %bc.<seq>.ok
bc.<seq>.fail:
  call void @<rtSym BoundsFail>(<int> <idx>, <int> <len>)
  unreachable
bc.<seq>.ok:
```

Active LLVM block on return is `bc.<seq>.ok` (subsequent emissions fall into it) —
same discipline as `emitRefIncInline`'s `.skip`. Verify `declare void @<BoundsFail>`
appears in a compiled `.ll` (grep); precedent says it will. Leave the `rt.BoundsCheck`
declare alone (harmless if unreferenced) unless a check shows it must be pruned.
Benefit: at the standard `--cflag -O2` build clang folds the diamond and can hoist the
loop-invariant compare — impossible with the opaque call; the `bnc -O0` IR benefits
directly.

### native x64 (`x64_dispatch.bn:316`)

```
Cmp(a, Reg(idx, SZ64), Reg(len, SZ64))     ; flags = idx - len
Jcc(a, CC_B, okLabel)                       ; unsigned idx < len  → in bounds, skip
; --- cold fail path (falls through only when idx >= len) ---
Mov(RSI <- len); Mov(RDI <- idx)            ; same arg order as today (RSI first)
Call(rt.BoundsFail)                          ; noreturn
DefineLabel(okLabel)
; (NO rm.ResetRegs — see the reg-map subtlety)
```

`okLabel = boundsLabel(ins.ID)` → `Lbc_<id>` (unique per instr; `L`-prefixed local).
Because `BoundsFail` is noreturn, defining `okLabel` immediately after the call is
correct (control reaches `okLabel` only via the `Jcc` skip). New shared helper
`boundsLabel(id)` in `pkg/binate/native/common` (mirrors `StringLabel`).

### native aarch64 (`aarch64_dispatch.bn:357`)

```
Cmp(a, true, idx, Reg(len))
Bcond(a, COND_LO, okLabel)                   ; unsigned < → skip   (LO == unsigned <)
Mov(X0 <- idx); Mov(X1 <- len); Bl(rt.BoundsFail)
DefineLabel(okLabel)
```

Confirm/add `COND_LO`/`COND_HS` constants for `Bcond`.

### native arm32 (`arm32_dispatch.bn` `emitBoundsCheck`)

```
Cmp(COND_AL, idx, Reg(len))
B(COND_LO, okLabel)                          ; unsigned < → skip
Mov(R0 <- idx); Mov(R1 <- len); Bl(COND_AL, rt.BoundsFail)
DefineLabel(okLabel)
```

Confirm/add arm32 `COND_LO`/`COND_HS`. (arm32 predication could fold the fail call
under a `HS` predicate, but a branch keeps all three native backends structurally
identical — prefer the branch.)

## Tests (Bug-Discovery-Protocol: land tests WITH the change)

- **LLVM** `emit_bounds_test.bn` (new): lower an `OP_BOUNDS_CHECK`, assert the emitted
  text contains `icmp uge`, a `.fail` block with `call ... BoundsFail`, `unreachable`,
  a `.ok` label, and **no** `call ... BoundsCheck`. Mirror `emit_refcount_test.bn`.
- **native x64/aarch64/arm32** dispatch tests (extend `x64_dispatch_test.bn`,
  `aarch64_emit_test.bn`, arm32's test): assert the lowering emits Cmp + a conditional
  branch + `Call/Bl BoundsFail`, not a plain call to `BoundsCheck`. There are existing
  `OP_BOUNDS_CHECK` dispatch tests to mirror.
- **Conformance — semantics preserved** (the critical guard):
  - Existing out-of-bounds tests (const index: 310/311/314; plus any runtime-index
    fault tests) must still abort on every compiled mode with the **byte-identical**
    message. Explicitly diff the diagnostic text.
  - Ensure coverage of **both** fail directions: `idx >= len` AND `idx < 0` (negative
    index → unsigned-huge → caught by the single compare). If a negative-index
    conformance case doesn't already exist, add one.
  - VM mode (`builder-comp-int`) is a **regression guard** — it must be unchanged
    (the VM path wasn't touched).
- **In-bounds still works**: existing slice/array programs across the suite exercise
  the taken (skip) path; a targeted micro-program indexing in-bounds in a loop
  confirms no fault + correct values.

## Verification / build

- **Unit tests for every changed package** (smoke-every-changed-package rule; native
  shared files feed all backends): `codegen`, `native/x64`, `native/aarch64`,
  `native/arm32`, and `native/common` (the `boundsLabel` helper).
- **Conformance**: `builder-comp` (LLVM), `builder-comp-int` (VM regression guard),
  and the native modes that are runnable locally — `builder-comp_native_x64_darwin`
  and/or `builder-comp_native_aa64`. **CAUTION** (from CLAUDE.md): the LLVM arm32 modes
  (`builder-comp_arm32_*`, no `native`) do **NOT** exercise `native/arm32`; the arm32
  inline lowering is validated by its unit test and (if runnable) the incomplete
  `builder-comp_native_arm32_baremetal` mode — not the LLVM arm32 mode.
- **Benchmark**: bnc compiling bnc, `--backend native` at `-O2` and `-O0`, before/after,
  to quantify the call-removal. Report honestly (per the mem2reg framing lesson —
  don't over-claim; state what fraction this actually moves on the real workload).

## Staging / landing

Land per backend as separate, self-contained, green commits — recommended order:
1. **LLVM** (independently valuable, closes the single-file gap, easiest to review).
2. **native x64**, 3. **native aarch64**, 4. **native arm32** — each its own commit
   (they share the `boundsLabel` helper + the correctness argument; the shared helper
   lands with the first native commit). The `ResetRegs`/reg-map point is resolved
   per-backend and reviewed in each. Per-instance cherry-pick approval per CLAUDE.md.

## Risks (adversarial-review targets)

1. **`len < 0` soundness.** The single unsigned compare diverges from `rt.BoundsCheck`
   only if `len < 0`. Is a length EVER legitimately negative? (array extent: no; slice
   length word: no in a well-formed program; a corrupted negative length is already
   UB.) If the review finds any real path to a negative length, switch to the
   two-compare signed form. **Primary decision point.**
2. **Reg-map state after the inline op** (drop `ResetRegs`; audit `isRuntimeGuardCall`).
   Must reflect the OK/fall-through path (nothing clobbered), not the noreturn fail
   path. Per-backend; a wrong call here is a miscompile. **Primary review target.**
3. **`rt.BoundsFail` declare on LLVM.** Precedent (`ZeroRefDestroy`) says it links;
   verify empirically by grepping a compiled `.ll`.
4. **Fresh-label uniqueness.** `Lbc_<ins.ID>` must be unique within the function —
   confirm `ins.ID` is unique per instruction and stable through lowering.
5. **Diagnostic byte-identity.** Same sink → identical by construction; verify anyway
   in a conformance diff.
6. **Cold-path layout.** A `DefineLabel` immediately after a noreturn `Call`/`Bl` is
   correct (reached only via the skip branch); confirm no assembler quirk with a label
   following a call with no intervening instruction.
7. **DivCheck / ShiftCheck** are the same call pattern. Explicitly **out of scope** for
   (2a) (bounds only) — noted as a natural follow-up, not scope-crept here.
8. **arm32/aarch64 unsigned CC constants** may need adding (`COND_LO`/`COND_HS`);
   trivial but must be verified present or added with the branch.
