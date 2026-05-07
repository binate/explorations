# Binate TODO

Tracks open work items. Completed items live in [claude-todo-done.md](claude-todo-done.md).

---

## TODO

### Migrate self-hosted code to method form (opportunistic)
- Pattern: add methods alongside free functions (same body), migrate
  callers per function (perl pass for simple shapes, manual fixup for
  nested args), drop the free function + `.bni` decl.
  `conformance/run.sh boot` after each migration; full `basic` at the
  end of a batch.
- ~~`pkg/buf.CharBuf`~~ — DONE (commits `174666c` Len, `1d5a4f9`
  Bytes, `b3cd116` Freeze, `e4a90fb` WriteHexByte, `b8799cb`
  WriteInt, `b7958f3` WriteByte, `80e3ac8` WriteStr, `8f96357` test
  cleanup). `New` and `CopyStr` stay free — no CharBuf receiver.
- **Open candidates** (do as ergonomic, in any order):
  - `pkg/asm/elf/elf_util.bn:BinBuf` — same shape as CharBuf
    (`bbU8`/`bbU16`/`bbU32`/`bbU64`/`bbBytes`/`bbZeros`/`bbAlign`/
    `bbAddr`/`bbGrow`). Mechanical; ~50–100 callers.
  - `pkg/asm.Assembler` — `asm.Emit*` / `asm.AddSection` /
    `asm.AddRelocation`. Larger surface, same pattern.
  - `pkg/types.Type` — `IsInteger`, `IsFloat`, `Identical`,
    `AssignableTo`, `ResolveAlias`, `SliceElem`, `PointerElem`,
    `FieldByName`, `NeedsDestruction`, `IsConst`, `StripConst`,
    `TypeName`, etc. Cleanly mechanical; reads naturally as
    `t.IsInteger()`. Many call sites.
  - `pkg/parser.Parser` — `next(p)`, `expect(p, tok)`,
    `got(p, tok)`, `peekTok(p)`. Many small sites.
  - `pkg/lexer.Lexer` — same shape as Parser.
  - `pkg/ir.Func` / `Block` / `Instr` — `EmitConstInt(f, b, …)`,
    `EmitCall(f, b, …)`, etc. **Needs a design pass first** — most
    signatures take both Func and Block, so it's not obvious whether
    the receiver should be Block or Func. Pick one before starting
    mechanics.

### ~~pkg/types boot-comp regression: hang during unit-test run~~ — FIXED
- **Root cause**: `pkg/ir/gen_method.bn` was missing the
  needsStructCopy-on-arg handling that `gen_call.bn` does for free-
  function calls. When a method takes a value-struct arg with
  managed fields (e.g. `p.addError(pos, msg)` where `pos` is
  `token.Pos` with `@[]char File`), the method-call path passed
  the struct by value WITHOUT RefIncing the managed field. The
  callee's scope cleanup then RefDec'd the field at end of scope,
  freeing the backing under the caller. After many such calls the
  freed-but-still-referenced backings led to use-after-free, then
  malloc heap corruption — eventually trapped at the next Malloc
  (which happened to be deep inside checkSrc → ParseFile →
  appendDecl during TestCheckSizeofBasic).
- **Why it appeared at 7251ffc**: parser helpers like next /
  expect / addError were free functions before that commit, so
  argument copies went through `gen_call.bn`'s correct handling.
  Method form routed them through `gen_method.bn` instead, which
  was missing the args-side struct-copy emit. The receiver-side
  branch already had it; only user args were missed.
- **Fix**: add the args-side `needsStructCopy` block to
  `gen_method.bn` (mirrors `gen_call.bn`), and also the
  `ctx.StmtGrewSP = true` markers on managed-slice / struct-copy
  results (also missed). Boot-comp `pkg/types` 270/270 after fix.

### Clarify rules for integer literals and constant expressions
- The bootstrap interpreter rejects hex literals with the high bit set
  (`strconv.ParseInt(..., 16, 64)` overflows int64), e.g.
  `0xFFFFFFFFFFFFFFFF`. The self-hosted type checker silently wraps
  via int64 overflow in `pkg/types/checker_util.bn:parseHexInt`. Two
  different bugs, both surprising.
- Go-style bignum support for constant expressions is too onerous, but
  we should at least support `uint64` literals — i.e. accept any
  64-bit value as either signed or unsigned depending on context, and
  reject (not wrap) values outside the chosen 64-bit range.
- Open questions to nail down in the spec:
  - What's the type of an integer literal? Currently "untyped int"
    that fits in int64; should an unsigned literal too big for int64
    but fitting in uint64 be allowed?
  - What about constant-expression overflow at type-check time
    (`1 << 63`, `0xFF * 0xFF * ... `)? Today it silently wraps.
  - Hex / binary / octal literals all need consistent rules.
- Update both impls together; document the result in claude-notes.md
  and update binate-coding-guide.md.

### Clarify spec for `return f(...)` with multi-return functions — SELF-HOSTED LANDED; bootstrap pending decision
- **Self-hosted (LANDED, 2026-05-01)**: type-checker
  (`pkg/types/check_stmt.bn:checkReturnStmt`) and IR-gen
  (`pkg/ir/gen_stmt.bn` STMT_RETURN branch) accept
  `return f(...)` when `f` returns the matching tuple. Each
  per-result type must be `AssignableTo` the outer's declared
  result. IR-gen lowers to one OP_CALL + one OP_EXTRACT per
  result; the existing return-RefInc/copy + temp-cleanup
  machinery handles ownership transfer. The literal-shape
  coercions in the per-expr return path (OP_CONST_NIL retyping,
  OP_CONST_STRING → string_to_chars, untyped-int width) all
  fire only on literals, which can't be call results — so the
  multi-return path skips them. The one non-literal coercion,
  `@[]T → *[]T` when the outer expects raw, is preserved on
  extracted values, mirroring the per-expr path.
  - Tests: `pkg/types/check_stmt_test.bn` (positive, arity-
    mismatch, type-mismatch); `pkg/ir/gen_stmt_test.bn`
    (`TestGenReturnMultiCallEmitsExtracts` pins
    1×OP_CALL + 2×OP_EXTRACT); conformance
    `347_return_multi_call` (all-scalar + mixed scalar/managed
    end-to-end; was 345 originally, renumbered after collision
    with `345_interface_decl`). xfail.boot. boot-comp /
    boot-comp-int / boot-comp_native_aa64 all green.
- **Bootstrap (pending decision)**:
  `bootstrap/types/checker.go:checkReturnStmt` (~963-978) still
  rejects this shape. Bootstrap acceptance is a separate
  question — the bootstrap subset is intentionally restrictive,
  and the self-hosted toolchain doesn't need this to compile.
  Defer until there's a concrete reason to widen the subset.
- Spec recorded in `claude-notes.md` ("Tail-call return for
  multi-return functions"). `bootstrap-subset.md` notes the
  bootstrap-only rejection.

### Mirror `return f(...)` acceptance in the Go bootstrap — LOW PRIORITY
- Self-hosted accepts the shape (commits `b88918e` /
  `d11e4f2` / `d3fc0db` / `96572fb` on main; conformance
  `347_return_multi_call`). Bootstrap still rejects it.
- **What's needed**:
  1. **Type-checker** (`bootstrap/types/checker.go:checkReturnStmt`,
     ~lines 963-978): when `len(s.Results) == 1` and
     `len(c.funcRet) > 1`, allow it iff the single expression is
     a `CallExpr` whose function type returns a matching tuple
     and each per-result type is `AssignableTo` the
     corresponding `c.funcRet[i]`. Mirrors the existing
     multi-return shape in `checkShortVarDecl` (~lines
     937-955) — same `(len(s.RHS) == 1 && rhsType is FuncType
     with matching Results)` predicate.
  2. **Bootstrap interpreter STMT_RETURN execution path**:
     extend it to handle the single-expression-multi-return
     shape, mirroring how `q, r := f()` is already executed
     (single call eval + per-result destructure).
  3. **Conformance**: drop `347_return_multi_call.xfail.boot`
     once both impls handle it. Drop the bootstrap-only
     rejection note from `bootstrap-subset.md`.
- **Why low priority**: the bootstrap subset is intentionally
  restrictive; the self-hosted toolchain doesn't need this to
  compile, and no in-flight work depends on it. Pick up when
  there's a concrete user (e.g., a self-hosted source file that
  wants the form, or a broader bootstrap-subset widening pass).

### ~~Array of managed-slice elements: string→@[]char in array context~~ — FIXED
- **Was**: two distinct bnc miscompiles for arrays whose element type
  is a char-slice (`@[]char`):
  - `[N]@[]char{"a","b","c"}` array-literal — silent wrong output,
    each slot's data ptr written but len/refptr/backing_len left at
    zero, so println saw len=0 and printed nothing.
  - `var arr [N]@[]char; arr[i] = "x"` indexed assignment — bnc
    aborted with `extractvalue operand must be aggregate type` on
    the refcount-Inc step (extractvalue called on a bare i8* from
    OP_CONST_STRING instead of a %BnManagedSlice).
  Both: var-decl / non-array-assign paths were converting
  OP_CONST_STRING → managed-slice value via EmitStringToChars; the
  array-literal and array-index-assign paths weren't.
- **Repros** (now passing in all modes):
  conformance/365_array_managed_elem_lit.bn,
  conformance/366_array_managed_elem_assign.bn.
- **Unit tests** in pkg/ir/gen_access_test.bn:
  TestArrayLitManagedElemEmitsRodataMSliceCopy,
  TestArrayIndexAssignManagedElemEmitsRodataMSliceCopy.
- **Related verification sweep (2026-05-06)**: tested arrays of
  OTHER managed element shapes after the initial fix.  `[N]@T`
  and `[N]@[]int` (with @[]int{...} elements) work cleanly under
  bnc.  `[N]struct-with-managed-field` revealed two additional
  bugs in genCompositeLit and genArrayLit, now fixed and pinned
  by conformance/367 + 368 and
  TestGenCompositeLitStructManagedCharField:
  - genCompositeLit's per-field string→char-slice conversion was
    gated `&& ft.Kind == types.TYP_SLICE`, so it only fired for
    raw-slice fields; @[]char fields fell through and the
    managed-slice RefInc / store wrote 8 bytes into the 32-byte
    slot.  Fix: drop the kind gate (isCharSliceType already
    matches both raw and managed).
  - genArrayLit didn't load struct values from their alloca
    pointer before storing into the array slot (mirroring what
    gen_control.bn's array-index-assign branch already did), so
    `[N]S{S{...}, ...}` wrote each element's i8* alloca pointer
    into the struct-sized slot instead of the struct value.
    Fix: add the same load-from-alloca guard.
- **Third site, found 2026-05-07** while resuming the unit-test
  cleanup sweep into asm / bnc / bni / bnlint args fixtures
  (which want to use `@[]@[]char{"a","b",...}` in place of
  `make_slice(@[]char,N)` + indexed assigns): genManagedSliceLit
  had the same gap.  String-literal elements stored only their
  bare data pointer (8 bytes) into the 32-byte managed-slice
  element slot, so reads came back len=0 (silent empty output).
  Fixed and pinned by conformance/372 +
  TestManagedSliceLitCharElemEmitsRodataMSliceCopy.  All three
  sites — genArrayLit, gen_control's array-branch, gen_composite
  per-field, genManagedSliceLit — now apply the same isCharSliceType
  + OP_CONST_STRING → EmitStringToChars conversion.  If a fourth
  store site surfaces, look for a missing instance of that same
  pattern.

### boot-comp-int-int: blocked on registerPureCExterns from interpreted cmd/bni
- **Repro**: `conformance/run.sh boot-comp-int-int 001_hello` (or
  any boot-comp-int-int run). Mode not in the `all` modeset, so
  CI is unaffected. Smaller repro: e2e/print-args.sh's `bni-under-bni`
  case (currently SKIPed pointing here).
- **State (2026-05-04)**: TWO root causes were stacked.
  1. **vm.Stack overflow** — FIXED via OP_SP_RESTORE plumbing
     across IR + all backends + IR-gen end-of-statement emission.
     Five-step series: `322a90a`, `2e1a4c3`, `7079fa6`, `f47f474`,
     `3393e62`.
  2. **Infinite recursion** — FIXED. Inner cmd/bni called
     `bootstrap.Args()` and got the OUTER process's full argv
     (including `cmd/bni` itself), so its parseArgs reinterpreted
     cmd/bni at every level. Fix: cmd/bni now registers a Binate
     shim (`progArgsAfterDash`) under the `"bootstrap.Args"`
     extern name in the per-VM registry, so programs running in
     bni's VM see post-`--` args (matching the spec and the Go
     bootstrap interpreter). This is what made the original "leak"
     symptom (8 MB vmInst per recursion level) catastrophic.
  3. **CURRENT BLOCKER**: registerPureCExterns crashes when called
     from interpreted cmd/bni. `var libcMalloc *func(int) *uint8 =
     libc.Malloc` requires LookupFunc("libc.Malloc") to find a
     VMFunc; libc.Malloc has no `.bn` body, so lookup fails and
     execLoop calls rt.Exit(1) with "vm: function not found:
     libc.Malloc". Outer cmd/bni's main runs natively (so the
     direct function-pointer dereference works); inner cmd/bni
     runs as bytecode (so the same code path is hit through
     BC_FUNC_VALUE, which can only resolve VMFunc names).
  - Introduced by the registry refactor (`a841f30`, `9486de9`,
    `faa98dc`). Pre-refactor, hand-coded arms in vm_extern.bn
    served libc/bootstrap calls without any registration step;
    refactor moved bindings into a per-VM registry that requires
    a function value at registration time.
- **Chosen fix (2026-05-06)**: extend `BC_FUNC_VALUE`'s
  `LookupFunc` miss path in `pkg/vm/vm_exec_helpers.bn:execFuncRefOp`
  to fall back to the executing VM's `vm.Externs` registry. On
  hit, build the function value as
  `{vtable=ExternBinding.VtableAddr, data=ExternBinding.DataAddr}`
  — same shape `OP_FUNC_VALUE` produces today, just sourced from
  the registry instead of from `vm.Funcs`. ~15 lines, one file.
  - **Why this and not a manifest / .bn-body wrappers**: the wall
    is at the lookup. The registry is already populated by each
    layer's host (cmd/bni's `registerPureCExterns`) before the
    next layer's main runs, so each layer's `BC_FUNC_VALUE` is
    dispatched by a VM whose `vm.Externs` already has the
    bindings. Works at arbitrary recursion depth without any
    bytecode-side compile-time emission and without forcing
    pkg/libc.bn (or analogous wrapper bodies) to be loaded into
    every nested VM.
  - **Soft limitation**: a user program that does
    `var f = libc.Malloc` at top-level with no surrounding
    `RegisterExtern("libc.Malloc", ...)` in the calling VM gets
    "function not found". Not an issue for cmd/bni-on-cmd/bni;
    soft problem for ad-hoc scripts under unusual embeddings.
- **Considered and rejected**:
  1. Detect interpreted context in cmd/bni and skip
     registerPureCExterns. Fragile; "interpreted" detection isn't
     first-class.
  2. Revert pure-C externs out of the registry — mixes two
     dispatch shapes per extern name.
  3. Compile-time-emitted shim manifest in both native backends +
     `rt.LookupShim`. Drafted in (now-deleted)
     `plan-shim-manifest.md`. Comparable cost to option 2 below;
     redundant with the chosen fix; only wins for the
     "no-pre-registration" case which doesn't apply here.
  4. `.bn`-body wrappers (intrinsic-call form `_c_<name>` or
     `@cextern` annotation) for pure-C externs. Cleanest in
     theory but doesn't help nested VMs that don't load
     `pkg/libc.bn` — same wall recurs at depth.
- **Not blocking**: still not in the `all` modeset.
- **Earlier original diagnosis** (pre-leak-fix, kept for context):
  caller was bytecode `rt.Free`, fnIdx was a NATIVE function
  pointer (e.g. 0x1043F5BAC ≈ 4.37e9) being treated as a 1-
  based VM index. The allocation was made by NATIVE rt.Alloc
  via the BC_MAKE_SLICE handler in vm_exec.bn calling native
  rt.MakeManagedSlice → native rt.Alloc, which stored
  `_raw_func_addr(RawFree)` in h[1] as a native pointer; later
  RefDec'd by bytecode rt.RefDec → bytecode rt.Free →
  BC_CALL_INDIRECT mismatch. Phase 3 trampolines retire this.

### Native AArch64 backend — regPool saturation (cluster A follow-up)
- **Silent-corruption hazard removed** (`e8dfb85`, 2026-05-01).
  `pkg/native/arm64/arm64_regmap.bn:regPool(i)` previously returned
  X15 for any `i >= 6`, silently aliasing distinct SSA values when
  more than 7 live scratch regs were needed (the original cluster-A
  miscompile shape). It now panics on `i == 7` with a clear message
  pointing at this TODO. Any future over-allocating op pattern
  surfaces as a loud compile-time abort with a stack trace, not a
  silent miscompile.
- **Live site fixed** in the same commit: `046_many_params`'s
  8-int-arg call was hitting saturation via `emitCall` (8
  sequential `nextReg` calls — one per arg load). Fix in
  `arm64_call.bn`: the per-arg scratch reg is dead after the
  immediately-following Mov-to-argReg / Str-to-stack, so reset
  between arg slots. Conformance 289/0/0 after.
- **Still open — structural fix.** The pool is still X9..X15 (7
  slots) and the codegen still has no spill mechanism for
  in-instruction temporaries. Any new op pattern that needs >7
  simultaneously-live scratches will panic. Real fix: spill on
  pool exhaustion, or extend the pool to X16/X17 with BL discipline
  (BL clobbers X16/X17, so they can't span calls). Not blocking —
  the panic is the oracle for whether anything actually needs this.

### ~~Inline RefInc / fast-path inline RefDec (perf)~~ — DONE
- **Plan doc**: `explorations/plan-refcount-inlining.md` (Status: DONE).
- New IR ops `OP_REFINC` / `OP_REFDEC` added alongside the old `OP_REFCOUNT_INC` / `OP_REFCOUNT_DEC`; IR-gen switched to emit the new ops; old emitters (`EmitRefcountInc` / `EmitRefcountDec` / `EmitRefcountDecDtor`) deleted in favor of `EmitRefInc` / `EmitRefDec` / `EmitRefDecDtor`.
- All three backends (LLVM, VM, native arm64) lower the new ops inline:
  - LLVM: nil-check diamond + header GEP at -16 + load/{add,sub}/store, with a slow-path call to `@bn_rt__ZeroRefDestroy` for RefDec when the count hits zero.
  - VM: fused single-dispatch bytecode ops `BC_REFINC_INLINE` / `BC_REFDEC_INLINE_FAST` — one switch arm per refcount site, vs ~5 if the IR had pre-expanded to primitives.
  - arm64: CBZ + LDR(pre-index for RefInc, separate SUB+LDR for RefDec to keep ptrReg alive across the BL) + add/sub + STR + CBNZ for RefDec; BL `bn_rt__ZeroRefDestroy` only on the slow path.
- **Slow-path helper**: `rt.ZeroRefDestroy(ptr, dtor)` lives in `pkg/rt`; called only when the inline RefDec decrement leaves the refcount at zero. Runs the optional dtor (via `_call_dtor`) and `Free`.
- **User-visible impact**: none. All call sites are compiler-emitted.
- **Commits** (chronological): `eb7332e` (OP_REFINC), `9cb934d` (LLVM RefInc), `e972953` (VM RefInc), `8b896de` (arm64 RefInc), `34511bd` (RefInc switchover); `6aa78d1` (ZeroRefDestroy), `46e8e52` (OP_REFDEC), `a8104d2` (LLVM RefDec), `445e40d` (VM RefDec), `a4847b2` (arm64 RefDec), `19502d4` (RefDec switchover + with-dtor tests).
- **Cleanup status (2026-05-02)**: IR/backend dead code is GONE — old `OP_REFCOUNT_INC` / `OP_REFCOUNT_DEC` constants, all three backends' old dispatch arms, the non-INLINE `BC_REFINC` / `BC_REFDEC` bytecode ops + their VM exec handlers, and `emitRefcountCall` are all removed. The `bn_rt__RefInc` / `bn_rt__RefDec` runtime symbols (declared `pkg/rt.bni:122-127`, defined `pkg/rt/rt.bn:157,166`) are NOT dead — but their remaining callers are dubious and they should probably be retired:
  - **Remaining callers**: (a) VM extern handlers in `pkg/vm/vm_extern.bn` — the `rt.RefInc` / `rt.RefDec` extern arms at lines 21-29 plus the managed-slice copy/dtor paths at 169/175/191/195 that hand-RefInc element backings during structural copies; (b) conformance tests `092_rt_alloc`, `093_rt_managed_slice`, `104_rt_refcount`, which exercise these as a public manual-refcount API.
  - **Why retire**: with every compiled refcount op inlined, the runtime symbols exist only for these dubious users. Keeping them in `pkg/rt`'s public surface entrenches a manual-refcount escape hatch that nothing in the language model encourages. The `vm_extern.bn` callers are part of a broader "all of `vm_extern.bn` is dubious" question — the managed-slice copy paths there should probably move out of host code entirely.
  - **Scope when picked up**: drop or rewrite the three conformance tests; audit/migrate the `vm_extern.bn` paths (likely part of a larger vm_extern.bn rework); then delete the symbols from `pkg/rt.bni` + `pkg/rt/rt.bn`. Not a "just deletion" change — has public-API implications. The "VM extern dispatch: name → function-value registry" entry below describes the natural vehicle: the `rt.RefInc` / `rt.RefDec` extern arms cease to exist (no caller left to register), and the surgical refcount paths in `bootstrap.Args` / `ReadDir` get audited as part of that rework.

### Function values — MAJOR PROJECT (interop prerequisite)
- **Plan docs**: `explorations/plan-function-values.md` (parent;
  Phase 1 COMPLETE) + `explorations/plan-function-values-phase-3.md`
  (cross-mode trampolines; Slices 3.1, 3.1.5, 3.2, 3.3, 3.4 all
  LANDED).
- **Phase 1 COMPLETE (2026-05-01)**: A.1–A.7 all landed. Type
  syntax, nil + zero-init, function-reference-as-value, calling
  through a function value, flow through args/returns/fields,
  method expressions `T.M`, and non-capturing function literals
  (lifted to synthetic `__funclit_<n>` top-level Funcs).
  Conformance tests 338–342 + 344 cover each slice; pkg/ir + pkg/types
  unit tests cover each coercion site, AssignableTo predicate,
  and capture-rejection. `pkg/ir/gen_call.bn` and
  `pkg/ir/gen_func_lit.bn` extracted to keep file-length hygiene
  clean.
- **Phase 3 LANDED (per plan-function-values-phase-3.md)**:
  cross-mode trampolines bridge compiled ↔ VM through a uniform
  always-shim convention `<ret>(*uint8 data, <args>)`. Compiled
  side: per-function `__shim.<mangled>` set in each `__vt.<mangled>`'s
  `call` slot (Slice 3.1). Common kind-tag at the start of `data`
  (Slice 3.1.5) discriminates `DATA_KIND_VM_CLOSURE_REC` vs
  `DATA_KIND_COMPILED_CLOSURE` (Phase 2). Compiled→VM goes through
  `vm.TrampolineScalar`, a fixed 7-int-arg trampoline that reads
  VM handle + vm_func_idx from the closure rec and dispatches via
  `execFunc` (Slice 3.2). Bytecode→compiled goes through
  `dispatchCompiledFuncValue` (`pkg/vm/vm_exec_helpers.bn:247`),
  which routes via `rt._call_shim_scalar` — a new IR-magic helper
  alongside `_call_dtor` / `_call_free_fn`, lowered to
  OP_CALL_INDIRECT (Slice 3.3). The earlier `5f4333f` cross-mode
  hack for `func(*uint8)` is now reframed as `dispatchNativeIndirect`
  — the BC_CALL_INDIRECT counterpart of BC_CALL_FUNC_VALUE's
  data==null branch (Slice 3.4). VM handle lives in the
  VMClosureRec (not a global), so multi-VM works without ordering
  concerns. Bootstrap-subset constraint: scalars + pointers ≤7,
  no floats, no aggregates — broader signatures need additional
  trampoline shapes when they actually reach this path.
- **Phase 2 DEFERRABLE**: closures + capturing function literals;
  capture design (by-value vs by-ref, mutability, lifetime) is
  its own pass. The bytecode dispatcher (`BC_CALL_FUNC_VALUE`)
  already has a `DATA_KIND_COMPILED_CLOSURE` arm (clear-error
  guard) ready to fill in.
- **Downstream**: Phase 3's machinery is what the
  compiler/interpreter interop project needs. With per-signature
  shims + the `(data, args)` convention, a "package descriptor"
  of function-value pointers is enough to dispatch arbitrary
  cross-mode calls — no per-function hand-coding required. This
  also opens the door to retiring `pkg/vm/vm_extern.bn`'s
  hand-written extern arms (~30 of them, including the
  `rt.RefInc` / `rt.RefDec` arms flagged for retirement above);
  see the Compiler/interpreter interop entry below.
- **Reframed scope**: function values were originally framed as
  "blocked on / a piece of interop." Inverted: data interops fine
  via shared `.bni` layout; what crosses the compiled/interpreted
  boundary at runtime are *exported functions and methods passed
  as values*. The package descriptor the interop work needs is just
  a struct of function values per export. So function values are
  the **upstream prerequisite** for the broader interop project,
  not a sub-item of it.
- **Representation**: 2-word `{vtable, data}`, identical to
  interface values. The vtable type is per-signature; the vtable
  *instance* is per-(function, capture-shape). Vtable layout has
  `dtor` first (matching all other vtables — common destruction
  sequence) and `call` second. Function types are structural —
  `*func(...)` / `@func(...)` — with no user-visible "function
  interface" declaration; the compiler synthesizes the impls at
  function-literal and method-value sites.
- **Frontend syntax**: `*func(int) int` raw / `@func(int) int`
  managed, mirroring the slice migration (`*[]T` / `@[]T`) and the
  proposed interface revision. Bare `func(...)` is not a usable
  type.
- **Upstream prerequisite**: `plan-call-indirect.md` — LANDED.
  The `OP_CALL_INDIRECT` IR op (LLVM + VM + native arm64
  lowerings) is what Phase 1's vtable-indirect call sequence is
  built on. Already exercised end-to-end by RefDec's dtor
  dispatch; this plan's Phase 1 doesn't need to re-invent
  indirect dispatch.
- **Phasing** (per the plan doc):
  - **Phase 1 — backend vtable machinery + non-capturing function
    values.** This is primarily about *building the shared
    interface/vtable backend* (vtable type/instance generation,
    `call`-shim mechanism, vtable indirect-call sequence in
    compiler + VM). Non-capturing function values are the
    smallest user-visible thing the backend can deliver. The same
    machinery is what user-declared interfaces will need at the
    runtime layer. Non-capturing call sites use a check-data-nil
    short-circuit (consistent with other nil-checks in the
    codebase) rather than always going through the shim.
  - **Phase 2 — closures + method values (DEFERRABLE).** Capture
    analysis, closure-struct generation, receiver-capture for
    method values. **Capture design is open** (by-value vs. by-
    reference, mutability semantics, lifetime extension) and is
    its own design pass before implementation. Most current goals
    do *not* need Phase 2; the compiler and self-hosted runtime
    don't write closures, CallDtor retirement doesn't need it
    (see Path B above), and the interop descriptor exposes only
    non-capturing function values. Defer until there's a concrete
    user-facing need.
  - **Phase 3 — cross-mode trampolines.** LANDED. Per-signature
    (currently per-return-shape: TrampolineScalar) trampolines
    bridge compiled ↔ VM through the always-shim convention.
    See plan-function-values-phase-3.md for slice-by-slice detail
    and the "Phase 3 LANDED" bullet above for the LANDED summary.
    Unlocks the broader interop work; doesn't require Phase 2.
- **Recursive lambdas — explicit non-goal for Phase 1.** Go-style
  recursive closures (`var f = func(x) { ... f(...) ... }`) are
  NOT supported. Top-level named recursive functions work as
  always. Y-combinator pattern is the workaround if needed.
  Revisit when Phase 2 capture design is settled.
- **Backend dependency**: function values share the vtable layout
  and dispatch path with interfaces, but **not** the frontend
  interface syntax. They depend on the runtime/codegen vtable
  machinery, not on `plan-interface-syntax-revision.md`. Either
  plan can land first; both share the backend.
- **Method values** (`x.M`, `T.M`) and **closures** are folded
  under this plan rather than tracked separately.

### VM extern dispatch: name → function-value registry
- `pkg/vm/vm_extern.bn`'s `execExtern` is a hand-coded ~30-arm
  `if streq(name, ...)` switch bridging each host function the
  VM might call (rt + libc + bootstrap). It pre-dates Phase 3's
  function-value machinery and is the "by-name dispatch" half of
  the legacy VM-extern bridge.
- Phase 3 made this redundant in principle: every cross-mode
  call already goes through a 2-word `{vtable, data}` value
  dispatched via the always-shim convention `(*uint8 data,
  <args>)`. What's missing is the simple bridge: bind a name to
  a function value (or raw native pointer) at VM-init time, and
  have BC_CALL-by-name route through that binding to the
  existing `dispatchCompiledFuncValue` path.
- **Sketch**: a name → function-value map on the VM, populated
  by explicit registration calls at startup. BC_CALL's "name
  didn't resolve in vm.Funcs" branch consults the registry
  instead of falling into execExtern's hand-coded switch.
  Registration stays manual — no package-descriptor design
  required (descriptors are the more general form, owned by the
  Compiler/interpreter interop project below). Each currently-
  supported extern becomes one line:
  `vm.RegisterExtern("rt.Alloc", rt.Alloc)`. Same coverage,
  uniform dispatch, no bit_cast unpacking.
- **Why now**: addresses the "vm_extern.bn is dubious" question
  without waiting for descriptor design. Drops out the hand-
  coded arms (including the `rt.RefInc` / `rt.RefDec` arms
  paired with the runtime-symbol retirement above). When
  descriptors do land, the registry stays as the manual-
  registration escape hatch for host-only externs that have no
  Binate-side `.bni` package.
- **Open questions**:
  - **API shape**: `vm.RegisterExtern(name, fn)` per call, or
    bulk `vm.RegisterExterns([]ExternBinding{...})`?
  - **Registered fn shape**: hold function values (uniform
    dispatch via `_call_shim_scalar` — but the registrant has
    to package each function as a function value first), or raw
    native pointers + a per-binding signature descriptor (the
    dispatcher decodes argv per shape)? The function-value
    route reuses Phase 3's machinery directly; the raw-pointer
    route is more general but adds a parallel signature decoder.
  - **`bootstrap.Args` / `ReadDir` refcount surgery**: the
    existing arms hand-RefInc managed-slice element backings
    before pushing onto vm.Stack. A naive function-value
    dispatch can't replicate that. Either (a) clean up those
    bootstrap APIs to not need surgery, (b) supply per-binding
    "registers + adapts" shims, or (c) leave those few cases in
    vm_extern.bn as residual until they're addressed
    separately.
  - **Const args**: some bootstrap calls take `*[]const char`
    that the current arms unpack via `bit_cast(*(*[]const char),
    args[0])`. The registry has to decide whether the registrant
    sees the unpacked value or the raw `args[i]` int.
  - **Timing**: register before the first BC_CALL — likely in
    cmd/bni's main, before running user code. Per-VM init
    instead is also reasonable if multiple VMs ever coexist.
- **Cross-references**: the entry should be referenced from the
  rt.RefInc/Dec retirement (the registry replaces those arms
  cleanly) and from the Compiler/interpreter interop entry (the
  registry is the lighter-weight first step; descriptors
  generalize it).

### Interface syntax revision — *Stringer / @Stringer + top-level decl
- **Plan doc**: `explorations/plan-interface-syntax-revision.md`
  (DRAFT — pending review).
- **Scope**: revise the IN-PROGRESS interface design in
  `claude-notes.md` § "Interfaces" before any of it ships. Three
  shifts:
  1. Raw / managed forms become `*Stringer` / `@Stringer`
     (mirroring the slice migration). Bare `Stringer` is no
     longer a usable type — only a referenceable interface name.
  2. Top-level `interface Foo { ... }` declaration form replaces
     `type Foo interface { ... }`. Anonymous interface type
     expressions are dropped entirely.
  3. Interface aliasing: `interface MyStringer = Stringer` (or
     possibly `type MyStringer = Stringer` — open in the plan).
- **Why**: same UAF-prevention argument as the slice migration —
  forcing the explicit raw-vs-managed choice prevents the "I
  thought it was managed" failure mode. Interfaces aren't types
  in this model; they're named contracts referenced via `*Iface`
  / `@Iface` / `impl T : Iface`.
- **No frontend dependency on function values**, and vice versa.
  Either can land first.
- **Backend**: vtable machinery (per-(impl, interface) static
  tables, vtable-indirect dispatch, cross-mode trampoline path)
  is shared with function values — building it once serves both.

### Cross-package method visibility in `.bni`
- Methods defined on a public type in package `foo` need to be declared
  in `foo.bni` for callers in other packages to see them — analogous to
  the existing `.bni` rules for free functions and types (covered by
  conformance tests 235/236, "Verify .bni vs .bn visibility semantics"
  is DONE).
- Currently, methods *do* work cross-package (conformance 330/331 cover
  it via `pkg/buf.CharBuf` methods called from `main`) because IR-gen's
  `RegisterImport` registers methods from the imported package's `.bn`
  source via the loader. That's a happy accident of the loader path, not
  a deliberate visibility design.
- Open: should `.bni` method declarations be required for cross-package
  visibility (matching free functions / types), and should the type
  checker enforce that? Today methods skip the `.bni` requirement.
- When picking this up, look at: how `pkg/buf.bni` declares its type but
  not its methods, yet cross-package callers still resolve them; whether
  to extend `checkBniSignatureMatch` to methods; whether `.bni` method
  decls are mandatory or just allowed.

### Verify anonymous struct equivalence — edge cases
- Both type checkers now implement structural equivalence for anonymous structs (field names + types in order)
- Needs edge case testing: nested anonymous structs, anonymous struct with managed fields, cross-package anonymous struct equivalence
- See claude-discussion-detailed-notes.md section 22

### Continue backfilling negative conformance tests
- 31 negative tests exist (112, 200-210, 214-221, 235-236, 238-246), covering type mismatches, undeclared vars, wrong args, nil semantics, operators, comparisons, field access, indexing, non-function calls, managed pointer misuse, multi-return, undefined types, .bni/.bn mismatch, visibility, imports, type conversion, const/break/continue/param, package mismatch, missing return, var redeclaration
- `.error` files use `grep -E` regex matching
- **Fixed diagnostics**: assign to const (238), break/continue outside loop (239, 242), duplicate param names (243), var redeclaration in same scope (246)
- **Remaining xfail'd**: missing return (245) — needs control flow analysis
- Bootstrap-only: package name mismatch not detected in single-file mode (244 xfail on boot)
- Still needed: const expression errors, more shadowing edge cases

### ~~`const` type modifier~~ — Stages 0–2c LANDED; Stage 3 deferred
- Stage 0 (syntax + TYP_CONST wrapper kind), Stage 1 (enforcement
  + cast drops), Stage 2a (reject `string → *[]char`), Stage 2b
  (implicit alloc+copy for `@[]char = "..."`), and Stage 2c (string
  literal natural type `[N]const char`, default `@[]const char`,
  array-init copy `var s [N]char = "..."`, managed-slice + raw-slice
  composite literals `@[]T{...}` / `*[]const T{...}`) all landed.
- Stage 3 (const method receivers) deferred — depends on the
  methods/interfaces feature.
- Ratification: Phase 3 of the composite-literal generalization plan
  (next entry) supersedes the spec for *how* string literals lower at
  the IR level. The semantic surface is fixed.

### Observable optimizations and UB policy — broader question
- Surfaced while planning const: allowing the compiler to allocate
  a shared static global for all-const composite literals is an
  optimization observable via raw-pointer comparison (`&a[0] ==
  &b[0]` where `a`, `b` are both `"hello"`). The const plan accepts
  this as UB rather than either blocking the optimization or
  carving out precise "same-literal-text gives same address"
  semantics.
- Same class as the refcounting move optimizations that are already
  observable via `rt.Refcount(...)` without a nailed-down spec.
- **Broader question**: do we want a general policy of "these kinds
  of observations are UB, the compiler may optimize across them",
  written up somewhere authoritative? Candidates for the same UB
  bucket: literal address identity, refcount timing, struct padding
  bytes, uninitialized-memory reads of stack-allocated vars. The
  alternative (fully specified observable behavior) is probably
  incompatible with small-target codegen goals.
- Not urgent — we're already making these trade-offs silently. A
  short design note ratifying the policy would be useful when a
  future optimization / feature forces the question.

### Switch `fallthrough` — proposal
- Not in the current grammar (`grammar.ebnf`). Binate switch cases are implicit-break (Go-style), but there's no opt-in for Go's `fallthrough` keyword.
- Would add one reserved keyword, one AST statement kind (`STMT_FALLTHROUGH`), and one IR lowering (branch to the next case's entry block, skipping its case-value check).
- Before implementing: decide whether we want it at all. Arguments for: matches reader expectations from Go, lets users avoid duplicated bodies across related cases. Arguments against: rarely needed in practice, adds a new keyword for a small ergonomic win, forces the type checker to recognize terminators beyond `return`/`panic` (termination analysis already inspects case bodies for bare `break`).
- Likely a decline unless a concrete use case comes up, but worth capturing as a live option.

### Termination analysis — labeled break
- Missing-return check (test 245) uses Go-style termination analysis simplified: RETURN terminates; `panic(...)` terminates; BLOCK terminates if last stmt does; IF terminates if both branches do; FOR with no condition and no `break` in body terminates; SWITCH with default and all cases terminating (no break) terminates.
- **Labeled break**: Binate currently has no labels. If/when we add them, termination analysis needs to track labels — a `break L` inside a nested for doesn't break the inner for (contrary to the current "any break disqualifies enclosing for/switch" rule). Revisit when labels are on the table.

### Pointers to interface values
- Interface values are regular value types — allow `*Iface`, `@(Iface)`, `*@Iface`, `@(@Iface)`, etc.
- `@Iface` sugar parallels `@[]T` sugar; parens break it
- Needed for: generics (`*T` where `T=Stringer`), out parameters, arrays of interfaces, containers
- Implementation: grammar, parser, type checker, codegen, bootstrap interpreter

### Test harness `isTestResultReturn` should resolve type aliases
- The test harnesses (bootstrap Go `main.go` and self-hosted `cmd/bnc/test.bn`) only accept `testing.TestResult` (qualified) or `@[]char` (literal managed-slice of char) as test return types.
- They don't resolve type aliases, so an unqualified `TestResult` from within the `pkg/builtin/testing` package itself is rejected ("wrong signature").
- **Fix**: resolve the return type through aliases before checking. If the return type is a named type in the current package, look up its definition and check the underlying type.
- **Workaround**: use `@[]char` as the return type in `pkg/builtin/testing/testing_test.bn`.
- Affects: `cmd/bnc/test.bn:isTestResultReturn`, `bootstrap/main.go:isTestResultReturn`.

### Clean up conformance tests to use array literal + `arr[:]` pattern
- `arr[:]` works in compiled mode; conformance tests using `make_slice` + indexed assignment for static data could use `[N]T{...}` + `arr[:]` instead
- Consider adding slice literal syntax (`*[]T{...}`) as sugar

### DWARF debug info — foundation in place, type coverage missing
**Done** (via `56ea542`, `a15ef50`, `2cd2c25`):
- `-g` flag in `cmd/bnc`, `SetDebugInfo` in `pkg/codegen`; off by default.
- Module-level: `source_filename`, `DICompileUnit` (FullDebug), `DIFile`, `DISubroutineType`, per-function `DISubprogram`.
- Line-level: `Line int` field on `ir.Instr` (`pkg/ir.bni:170`). `genExpr` sets `.Line` from `e.Pos.Line` (`pkg/ir/gen_expr.bn:16`). `annotateBlockInstrs` backfills zero-line instrs to statement line (`pkg/ir/gen_stmt.bn:11-14`). Per-instruction inline `!DILocation(line: N, scope: !M)` in emitted LLVM (`pkg/codegen/emit_debug.bn:99-114`).
- Variables: `llvm.dbg.declare` + `DILocalVariable` for named allocas (`emit_debug.bn:139-162`). Names propagated via `StrVal` on `OP_ALLOC`.
- lldb/gdb now show Binate function names, file, line numbers, and local variable names.

**Gaps**:
- Type coverage is basically just `i64`. Only one `DIBasicType` emitted (`emit_debug.bn:220`), reused for every variable. No `DIBasicType` for bool/uint8/uint16/uint32/char; no `DICompositeType` for struct/array/slice; no `DIDerivedType` for pointers/typedefs. All locals show as `i64` in the debugger.
- Parameters don't get `DILocalVariable` (stack slots exist but no dbg.declare for params).
- `DISubprogram` has `line: 0` and `scopeLine: 0` (function definition line never captured).
- `DISubroutineType` is a single shared generic; no per-function signature or parameter types.
- No `llvm.dbg.value` (only `dbg.declare` for allocas).
- Line positions: only `genExpr` explicitly threads `.Line`; most IR-emission sites rely on statement-line backfill (coarse). No columns.

**Reasonable next steps** (roughly ordered by effort/payoff):
1. Emit `DIBasicType` for each scalar kind (bool, char, u8/16/32, i32, etc.) and reference from variable declares — unlocks correct type display in debuggers.
2. Capture function definition lines into `DISubprogram` (thread from AST `Func`/`FuncDecl` node).
3. Emit `DILocalVariable` for parameters.
4. Emit `DICompositeType` for structs (field names + types), `DIDerivedType` for pointers. Wire into `emit_types.bn`'s struct collection.
5. Thread positions through more IR-gen sites (statements, assignments, calls) for finer-grained `DILocation`.
6. Per-function `DISubroutineType` with real parameter + return types.

### Package directory organization and conventions
- Think more carefully about `pkg/` directory structure and naming conventions
- Current layout mixes toolchain internals with runtime and bootstrap support
- Questions: should toolchain packages be under a sub-prefix? Where do future stdlib packages live?

### Conformance tests: consider a separate repo
- Running conformance tests in CI creates a circular dependency: the bootstrap repo needs the binate repo (which contains the test cases), and the binate repo needs the bootstrap binary (to run the tests)
- Consider moving conformance tests to their own repo (e.g., `binate/conformance`) that both repos reference
- This also gives a natural place for test infrastructure (run.sh, runners, xfail metadata) that doesn't belong to either the bootstrap or self-hosted repo
- The unit test runner (`binate/scripts/unittest/`) has a similar issue — it's in the binate repo but the `boot` mode runs via Go in the bootstrap repo

### Standard library design
- Candidates: growable collections (Vec[T], Map[K,V] post-generics), I/O abstractions, string utilities, formatting
- CharBuf is implemented (pkg/buf); broader stdlib design should inform future collection APIs

### Slice ownership model — design notes
Binate is NOT Go. The two types of slice are intentionally different:

**Raw slices (`*[]T`)** — two words: (data ptr, length)
- Value types, no refcounting, no GC. Caller manages lifetime (like C).
- Cannot be compared to `nil` — check `len(s) == 0` for empty.

**Managed-slices (`@[]T`)** — four words: (data ptr, length, backing_refptr, backing_len)
- Prefix-compatible with `*[]T`. Refcounted via backing_refptr.
- backing_len stores total element count for destructor cleanup.
- `make_slice(T, n)` returns `@[]T`. `@[]T → *[]T` conversion: extractvalue fields 0,1.

### Test runner improvements
- ~~**Better docs/help**~~: DONE. Both runners show description, examples, flag docs, test format/convention docs, xfail mechanism. READMEs added for conformance/ and scripts/unittest/.
- ~~**Better output**~~: DONE. `-v` (verbose: all test names), `-q` (quiet: failures+summary only), default (dots for passes, detail for failures).
- ~~**Mode sets in files**~~: DONE. `scripts/modesets/` directory with one file per set (basic, all, full). Adding a new mode set is just adding a file. Both runners read from the shared directory. Help output dynamically lists available sets.
- ~~**Better mode specification**~~: DONE. Comma-separated modes (`boot,boot-comp`) expand into sequential runs. Works alongside mode set files.
- ~~**Better filtering (unit tests)**~~: DONE. Fixed unit test runner to use substring match (was exact match). `token` now matches `pkg/token`, consistent with conformance runner.
- **Better filtering (individual test functions)**: ability to specify individual test functions, not just packages (e.g., `run.sh boot-comp pkg/ir TestFoo`).
- **Timeout/hang handling**: better and/or automatic detection and handling of tests that hang.
- **Parallelization**: consider running test packages in parallel within a mode.

### ARM32 bare-metal target — MAJOR PROJECT
- **Why**: enable Binate as an OS-development language on ARM32
  bare-metal (Cortex-A and possibly Cortex-M). Bare-metal, not Linux —
  we want to write the OS in Binate, not run on top of one.
- **Existing substrate that already handles bare-metal cleanly**:
  - `pkg/asm/arm32` encodes ARMv7-A instructions (data-processing,
    load/store, multiply/divide, branches, system); 73 unit tests pin
    bit patterns. Assembler-side is essentially done.
  - `pkg/asm/elf` emits ELF32 with the right ARM32 reloc set
    (R_ARM_JUMP24, R_ARM_ABS32). End-to-end tests in
    `pkg/asm/elf/elf_test.bn` already link with `arm-none-eabi-ld`
    (bare-metal linker) and run under `qemu-system-arm -semihosting`
    on virt machine. Three tests: exit, loop sum, function call.
  - `cmd/bnas` already accepts `.arch arm32` and routes through the
    ARM32 instruction parser.
- **What's missing**: an IR-to-machine-code lowering for ARM32 (a
  `pkg/native/arm32` sibling of `pkg/native/arm64`), and a bare-metal
  runtime port.
- **The interesting bit: bare-metal makes the runtime story
  non-trivial.** Things the language/runtime currently assumes from
  the host that don't exist on bare metal:
  - **Allocator**: `pkg/rt`'s managed-pointer/managed-slice
    allocations go through `bn_rt__c_malloc` / `bn_rt__c_free` /
    `bn_rt__c_calloc` (libc-shaped C stubs). On bare metal we need
    a Binate-implemented allocator — probably a simple bump
    allocator first (no free, suitable for early boot), then a real
    heap (free-list or buddy). Allocator implementation lives in
    pkg/rt (or a peer package) and replaces the `c_*` bridges for
    the bare-metal target. The existing "Un-export `rt.c_*`" TODO
    is a prerequisite — once those are private, we can swap them.
  - **`memset` / `memcpy`**: tiny Binate or asm implementations.
  - **Exit / abort / panic**: semihosting `SYS_EXIT_EXTENDED` for
    QEMU testing; on real hardware, `wfi` loop or reset.
  - **I/O**: no stdout/stderr — need a UART driver or semihosting.
    Two flavors:
    - Semihosting (used by the existing QEMU tests): debug-only,
      requires a debugger / QEMU. Useful for development, not for
      shipping.
    - UART: target-specific MMIO. Need a small driver per board —
      PL011 for ARM virt machine, vendor-specific for real hardware.
      The `bootstrap.Write` extern would dispatch to a board-defined
      `uart_putbyte` instead of `write(2)`.
  - **`bootstrap.*` shape**: today's bootstrap.bni is libc-shaped
    (Open / Read / Write / Stat / Args). Bare metal has no
    filesystem and no argv. We'd want a smaller bare-metal-friendly
    bootstrap interface — probably just an output sink and a panic.
    The `formatInt` / `formatBool` / `formatFloat` helpers stay
    (they're pure Binate); only the I/O surface changes.
- **Boot**: a tiny crt0 in asm (or Binate inline-asm if we ever add
  it) to set up the stack, zero BSS, copy .data from flash to RAM,
  then jump to `bn_main`. Provided as a per-board file alongside the
  linker script.
- **Linker script**: per-board memory map (text/rodata in flash, data
  in RAM, BSS, stack at top of RAM, optional MMU page tables for A-
  class). The QEMU virt machine convention (text at 0x40000000) is a
  good first target.
- **Two paths to actual codegen**, similar to the ARM32-Linux
  consideration but with bare-metal twists:
  - **LLVM-via-clang**: pass `--target=armv7a-none-eabi`,
    `-mfloat-abi=soft` (or `hard` if we want NEON/VFP), no sysroot.
    Fastest to first-light, but the LLVM dependency is heavier on a
    bare-metal toolchain story (we'd need to ship clang + lld or
    require the user to have a cross toolchain installed).
  - **Native pkg/native/arm32**: full sibling of `pkg/native/arm64`.
    AAPCS32 calling convention (NGRN over R0..R3, args 5+ on stack,
    return values in R0..R3, large-aggregate return via the hidden
    pointer in R0). Mach-O isn't relevant here — only ELF32 output.
    No external dependency once written. Larger upfront cost; closer
    to the OS-language goal of "no LLVM at runtime."
- **Testing**: the existing `pkg/asm/elf` semihosting harness scales
  up — write conformance programs that use only the bare-metal
  runtime surface, link with `arm-none-eabi-ld`, run under QEMU
  with `-semihosting`. Once the UART driver lands, switch to
  reading stdout from QEMU's serial0.
- **Adjacent in-flight items that affect this**:
  - "Un-export `rt.c_*`" — direct prerequisite for swapping the
    allocator/memops bridges per-target.
  - "Native AArch64 backend cluster A" — in flight; the
    common AAPCS dispatch helper in `pkg/native/common` is shared
    between ARM64 and a future ARM32, so ARM32 work shouldn't start
    until the ARM64 native backend is stable enough that we know the
    common shape is right.
  - The compiler/interpreter interop work is independent of this —
    interop is mostly a layout/representation question, not a
    target question.
- **Suggested first milestone**: get a meaningful subset of
  conformance running on QEMU via the LLVM backend with semihosting
  I/O. Concretely:
    - Pick the codegen path: LLVM-via-clang first
      (`--target=armv7a-none-eabi -mfloat-abi=soft`). Defer the
      native `pkg/native/arm32` backend until LLVM-via-clang
      validates the runtime/boot/linker story.
    - Implement a bump allocator in `pkg/rt` (no free) — enough for
      every conformance test that doesn't actually run out of memory.
      Allocations touch managed-pointer / managed-slice paths only,
      so this is the same surface the existing `c_malloc`/`c_calloc`
      bridges expose. Wire it behind a build-mode switch alongside
      the existing libc-bridges path.
    - Implement semihosting `SYS_EXIT_EXTENDED` (already used by the
      pkg/asm/elf QEMU tests) and `SYS_WRITE0` for putchar/print.
      Replace `bootstrap.Write` (the I/O primitive everything
      eventually funnels into after the print rewire) with the
      semihosting variant for this target.
    - Add `memset` / `memcpy` in pure Binate (or a tiny inline-asm
      wrapper if one is later added).
    - Conformance tests that DON'T touch file I/O / argv / dirs
      should pass: arithmetic, control flow, structs, slices,
      managed pointers, methods, etc. Probably 200+ of the existing
      278. Tests that rely on `bootstrap.Open` / `Read` / `Args` /
      `Stat` / `ReadDir` / `Exec` would be excluded for v1.
- **Plan doc**: `explorations/plan-arm32-bare-metal.md` exists as a
  **DRAFT** (initial sketch — not yet ratified). Covers the items
  above plus: target board choice (QEMU virt + one real Cortex-A
  board TBD), allocator design (bump first, heap second), bare-
  metal `bootstrap.bni` shape, boot/linker-script convention, and a
  placeholder for the per-package inventory of `bootstrap.*` calls
  (the inventory itself is deferred to a follow-up). Needs review
  pass before any implementation begins.

### Compiler/interpreter interop — MAJOR PROJECT
- **Why this is high priority**: dual-mode execution is a core promise of the
  Binate language. Compiled-and-interpreted code calling each other (in both
  directions) is what makes "compile some packages, interpret others" actually
  useful. We should make this real BEFORE pushing on more language features —
  large language additions risk locking in design choices that close off
  interop options.
- **Likely-already-compatible substrate** (verify rather than redesign):
  - **In-memory layout of types** is supposed to match across modes. Compiler
    uses `pkg/types`'s SizeOf/AlignOf/FieldOffset; interpreter uses (or should
    use) the same. Verify with a small cross-mode struct-pass test.
  - **Refcounting**: managed allocations carry a header with refcount and a
    pointer to the destructor, populated at allocation site. Compiled and
    interpreted code use the same `rt.RefInc` / `rt.RefDec` / `rt.Free`. Free
    paths invoke the per-type dtor through the header, so a managed value
    allocated on one side and dropped on the other should clean up correctly.
    Verify with a cross-mode managed-pointer round-trip.
- **Direction to start with**: interpreted code calling compiled code. Simpler
  than the reverse (no need for the compiler to plant trampolines into a
  running interpreter). Once that works, compiled code calling interpreted
  code falls out roughly symmetrically.
- **Granularity: package-level.** For interpreted code in package P to call
  into a compiled package Q, the interpreter needs:
  - Q's `.bni` (so the interpreter can type-check P against Q's signatures —
    this already works today via the existing `.bni` loading path).
  - **Pointers to Q's compiled functions** (the actual interop primitive).
- **Proposed mechanism: auto-generated package descriptor.** The compiler emits,
  for each package Q, a synthetic `const` of a synthetic struct type — call it
  e.g. `foo.Package` (working name; could be `foo.PackageImpl` or another
  canonical name) — whose fields are pointers to Q's exported functions in some
  canonical order (e.g., sorted by mangled name). The interpreter, when it
  loads compiled package Q, reads that descriptor and binds each field as the
  function value for the corresponding name in Q's scope. Naming and layout
  must be canonical so an interpreter built against Q's `.bni` can read Q's
  descriptor without further metadata.
- **Symmetry**: the interpreter should produce the same shape on its own end —
  for each interpreted package, expose a `foo.Package` whose function-pointer
  fields are trampolines into the interpreter (call into the bytecode VM
  using the trampoline's bound bytecode/closure-env/types/aliases). That way
  compiled code calling interpreted code is the same mechanism, mirrored.
- **Prerequisite — DONE**: function values (see
  `plan-function-values.md` + `plan-function-values-phase-3.md`).
  The descriptor's fields are pointers to functions — that's
  exactly what function values are. The 2-word `{vtable, data}`
  representation, the `(*uint8 data, <args>)` always-shim
  convention, the per-function `__shim.<mangled>` shims, the
  bytecode-side `dispatchCompiledFuncValue` (via
  `rt._call_shim_scalar`), and the compiled-side `TrampolineScalar`
  are all in place. The remaining work is the descriptor itself
  (naming, layout, emission, loading) plus the symmetric VM-side
  emission for interpreted packages — pure plumbing; no new
  trampoline machinery needed.
- **Adjacent cleanup, lighter-weight first step**: see the
  "VM extern dispatch: name → function-value registry" entry
  above. A per-VM name → function-value registry with manual
  registration (no descriptor design needed) replaces
  `pkg/vm/vm_extern.bn`'s hand-coded switch via the same
  `dispatchCompiledFuncValue` path Phase 3 already provides.
  Auto-generated descriptors are the more general form of the
  same idea — the registry stays as the manual-registration
  escape hatch for host-only externs that have no Binate-side
  `.bni` package.
- **Design open questions** (need a writeup before implementation):
  - Canonical name for the descriptor — `foo.Package` reads naturally but
    risks conflicting with user names. `foo.PackageImpl` or a reserved-prefix
    name (`__pkg_foo`)? Reserve a keyword?
  - Canonical layout — sort by mangled name? By declaration order in `.bni`?
    Layout must be agreed-upon by the descriptor's emitter and reader.
  - Interaction with import aliases (`import alt "pkg/foo"`) and blank imports
    (`import _ "pkg/foo"`) — see the "Import aliases and blank imports" entry.
  - What does the descriptor look like for the package being compiled itself
    (the "self" descriptor)?
  - How are package-level globals exposed? Functions are the obvious starting
    point; globals are a separate (but related) interop question.
  - Versioning: if Q's `.bni` and Q's compiled descriptor disagree (different
    function set, different layout), how do we detect and report it?
- **Adjacent in-flight work that affects this**:
  - "Function values — MAJOR PROJECT" (above) and
    `plan-function-values.md` — direct prerequisite. Phase 3 of
    that plan delivers the cross-mode trampoline machinery this
    work consumes.
  - "Free-function pointer in managed-allocation header — bug"
    (above, DONE within a single mode) — Free now dispatches through
    `header[1]`. Cross-mode allocate-on-one-side / free-on-the-
    other still requires Phase 3's trampolines to translate
    `header[1]` between the C-pointer and VM-index conventions.
  - "Lift function-name qualification into IR" (above) — would simplify name
    resolution at the interop boundary.
  - "Import aliases and blank imports" (below) — affects how the descriptor
    is named at the import site.
- **Suggested next step**: write a design doc (e.g.
  `explorations/plan-compiler-interp-interop.md`) that nails down the
  descriptor name/layout, walks through one concrete cross-mode call end-to-
  end on each side, and identifies the first concrete code change to make.
  Don't start implementation until the design is reviewed.

### REPL — Tier 1 + Tier 2 + Tier 4 (full) LANDED (2026-05-01)
- **Status (2026-05-01)**: Tier 1 PoC ships as `bni --repl
  <file.bn|dir>`; Tier 2 adds top-level `func`, `const`, and
  typed `var` declarations at the prompt; Tier 4 full
  redefinition shipped — compatible-sig replaces in place
  (old callers see new body), incompatible-sig shadows (old
  callers retain the old VMFunc via eager-filled CallCache,
  new callers route to the new entry).  Substrate is an O(1)
  name→idx hash on `vm.Funcs` plus eager `CallCache` fill at
  lowering time (commit `9af2d56`); shadow itself in
  `63cc49b`.  Multi-line input also landed (paren-aware
  accumulator — tracks `{`/`}` and `(`/`)` in
  `computeOpenDepth`).  See `plan-repl.md` for the per-step
  commit table, verified behaviors, deviations from the
  original plan, and the remaining follow-ups (Tier 2: type
  at prompt, methods, prompt-introduced new managed-type dtor
  regen, var-initializer evaluation; Tier 4: refcount-aware
  shadow warning, forced-shadow escape hatch, method
  redefinition).  Tier 3 (forward refs) and Tier 5
  (mid-session imports) remain DRAFT.
- **Why this matters now**: the REPL is an explicit core goal in
  `claude-notes.md` (see "Forward references & REPL model — DECIDED"
  and the dual-mode rationale in
  `claude-discussion-detailed-notes.md` § 11 / § 23). Its semantics
  are largely *already decided*; what's not decided is the
  toolchain shape. Writing it down now so that adjacent decisions
  (function values, interop descriptors, layout extraction, IR
  cleanup) get checked against REPL feasibility before they land
  — and so that interpreter-only REPL work can start in parallel,
  since most of it overlaps with the audit work the interop story
  already needs.
- **Already-decided semantics** (do NOT relitigate here — see
  `claude-notes.md`):
  - **Retained mode** (definitions) — parsed and stored, validation
    deferred until dependencies are met. Source files are entirely
    retained mode.
  - **Immediate mode** (bare expressions / statements at the prompt)
    — fully checked at entry, can reference validated retained defs.
    Top-level scope in source files is declarative-only; bare exprs
    are REPL-only.
  - **No forward declarations.** Deferred validation handles forward
    references. Errors surface at use, not at definition.
  - **Redefinition**: *compatible* (same sig) → replace; *incompatible*
    (different sig) → shadow with refcounted old-def retention; warn
    on outstanding refs at shadow time. Forced-shadow escape hatch.
  - **Hot-swap of interpreted functions while a compiled binary runs**
    — fall-out of the thunk model.
- **What the VM is/isn't rigid about** (corrects an earlier overstatement
  in this entry):
  - **`BC_CALL` is name-resolved per call, not idx-baked.** Bytecode
    stores a per-VMFunc strings index for the callee's qualified name;
    `LookupFunc` walks `vm.Funcs` by name on every call
    (`pkg/vm/vm_exec.bn:418-421`). That makes replace-redefinition an
    in-place body swap and shadow-redefinition an append-then-shadow,
    both nearly free given `@VMFunc` already being managed.
  - **`vm.Funcs` is already incremental.** `LowerModule` is called
    per-module and appends; multiple modules already coexist in one
    VM with their own preserved string pools (`pkg/vm/lower.bn:42`).
    Globals are also append-only via `materializeGlobals`.
  - **The frontend pipeline is module-shaped, not declaration-shaped.**
    Loader, parser, type checker, and IR-gen are entered per-package;
    there's no "type-check this single decl against an existing scope"
    entry point. Forward refs work today only because the whole module
    is parsed before checking.
  - **Type checker has no concept of pending.** Errors fire immediately
    on undefined names. Deferred validation (the "retained" half of
    the model) is real new infrastructure.
  - **No pretty-printer for arbitrary values.** `println` covers char
    slices and primitives only.
  - **`LookupFunc` is a linear scan.** Fine today; will matter if REPL
    workloads run real volumes of calls. Easy to fix (name → idx hash)
    and worth doing before Tier 1 ships, since the alternative
    (bake-idx-into-bytecode) would close off the redefinition story.
- **Tiered plan** (each tier shippable on its own; see
  `plan-repl.md` for entry-point names and concrete steps):
  1. ~~**Load-then-poke.**~~ **LANDED (2026-04-30).** Load a `.bn`
     module the normal way; prompt accepts only immediate-mode
     entries.  Each entry → synthetic `__repl_N()` → IR-gen →
     lower-one-function → call.  Auto-`println` wrap of bare exprs
     was deferred (gated on interfaces / proper Format dispatch
     once `bootstrap.println` is retired) — type `println(...)`
     explicitly.  Multi-line input also landed.
  2. ~~**Add new top-level decls at the prompt.**~~ **`func`,
     `const`, and typed `var` LANDED (2026-04-30 / 2026-05-01).**
     Per-decl entry points in parser/types/ir; append to current
     scope, plus `vm.Funcs` for funcs, `moduleConsts` for consts,
     and `globalNames`/`globalAddrs` for vars (via the new
     `vm.MaterializeOneGlobal`).  `type` / methods +
     prompt-introduced new-managed-type dtor regen +
     var-initializer evaluation are remaining follow-ups (see
     plan-repl.md).  Still no forward refs.
  3. **Forward references.** Pending-validation queue in the type
     checker.
  4. ~~**Redefinition.**~~ **LANDED (2026-05-01).**
     Compatible-sig: `LowerOneFunc` rebinds the existing
     `vm.Funcs` entry in place at the same idx, so the
     CallCache stays valid; old callers see the new body.
     Incompatible-sig: `LowerOneFuncShadow` appends a fresh
     entry and re-points the funcIndex hash; old callers'
     eager-filled CallCache slots keep them on the OLD VMFunc,
     while freshly-lowered code routes through the new one.
     Shipped via two commits: substrate (O(1) name→idx hash +
     eager `CallCache` fill, `9af2d56`) and the shadow path
     proper (`63cc49b`).  Refcount-aware shadow warning,
     forced-shadow escape hatch, and method redefinition are
     remaining Tier 4 follow-ups.
  5. **Mid-session imports.** Loader entry point for "load this one
     package now."
- **What's free / "should-do-now-anyway"**:
  - ~~The audit itself~~ — done; `plan-repl.md` is the live doc.
  - ~~Per-decl entry points exposed opportunistically when the
    relevant code is touched for unrelated reasons.~~  Done as part
    of Tier 1 + Tier 2 (parser ParseExpr / ParseStmtList /
    ParseTopLevelDecl / IsAtTopLevelDecl; types CheckExprInScope /
    CheckStmtListInScope / CheckDeclInScope / CheckMainPersistent;
    ir GenSyntheticFunc / GenDecl; vm LowerOneFunc / CallByVMFunc).
  - ~~Name → idx hash in `LookupFunc`.~~  Solved differently:
    per-VMFunc CallCache (commit `6c8e0c0`) memoizes the lookup
    result per call site, removing the per-dispatch scan; lazy fill
    on first call; explicitly designed for REPL invalidation.
  - A minimal pretty-printer (probably `pkg/replprint`, leaning on
    `pkg/buf.CharBuf`). Useful well beyond REPL.  **Deferred until
    interfaces land** — `bootstrap.println` is a temporary hack
    scheduled for removal; building features on top of it would
    entrench the hack.  See "Pretty-printer" in plan-repl.md and
    the auto-`println` deferral note.
- **Decisions / non-decisions in adjacent work to pressure-test**:
  - **Function values** (`plan-function-values.md`): a function value
    must be a *stable identity for what it refers to*, not for the
    bytes of the underlying body. Re-binding the body of an
    interpreted function does not invalidate function values pointing
    at it. Add this clause to that plan when it moves out of DRAFT.
  - **Compiler/interpreter interop** (above): the package descriptor
    is shaped right for REPL — interpreted-package descriptors are
    mutable, compiled ones are read-only. Sorted-by-mangled-name
    layout interacts with "add a new exported function mid-session"
    (positions move when a new export sorts in); confirm that's the
    intended behavior.
  - **Layout extraction** (`layout-extraction-plan.md`): expose a
    runtime-extensible type universe, not a closed-at-startup one.
  - **IR/backend cleanup**: no closed-world assumptions in the shared
    layer.
- **What this entry is NOT**:
  - A REPL implementation plan — that lives in `plan-repl.md`.
  - A relitigation of REPL semantics — those are decided; if they
    change, update `claude-notes.md` first.
- **Open design questions worth pinning before Tier 1 starts** —
  resolved as part of the Tier 1 work:
  - ~~Top-level prompt grammar.~~  Settled as bare statement list;
    auto-`println` wrap deferred until interfaces (above).  `func`
    decls are dispatched to the decl path via
    `parser.IsAtTopLevelDecl`.
  - ~~Error recovery.~~  Implemented exactly as proposed: parse /
    type / IR-gen / lower / runtime errors in immediate mode print
    and return to prompt; loaded state unaffected.  Verified by
    `e2e/repl.sh` cases.
  - ~~Where pretty-printing lives.~~  Deferred (see above).
  - ~~Sentinel for "no result".~~  Nothing — empty stmt lists are
    skipped by `evalReplStmtList` before reaching IR-gen.
  - ~~Whether REPL is a separate `cmd/bnrepl` or a `--repl` flag on
    `cmd/bni`.~~  Settled as `--repl` flag on `cmd/bni`.
    `scripts/build-bni.sh` (commit `22ea525`) is a convenience
    wrapper for casual use.

### Import aliases and blank imports
- Do we support Go-like `import somethingelse "pkg/foo"` currently? We'll likely need this.
- Do we support `import _ "pkg/foo"`? Should we? (Side-effect-only imports.)
- Both interact with the package object naming question above.

### Package path: env-var support (Stage 7)
- Add `BINATE_PACKAGE_INTERFACE_PATH` / `BINATE_PACKAGE_IMPL_PATH`
  (long names match `LD_LIBRARY_PATH`/`PYTHONPATH` style; aliases TBD)
  as the fallback when CLI flags are absent.
- Gated on adding `bootstrap.Getenv` (a few lines of C + Go-interp
  glue). Deferred because direct shell invocations of bnc/bni today
  can construct CLI arguments — the env-var fallback is convenience
  for users invoking the tools by hand.
- See [`plan-package-search-paths.md`](plan-package-search-paths.md)
  § "Env vars".

### Package path: binary artifacts on IMPL_PATH (Stage 8 / Phase 2)
- Once we have a stable per-package ABI/linker contract: accept
  `.o`/`.a`/`.so` files on `IMPL_PATH` as alternatives to `.bn`
  source. `hasImplFiles(dir)` becomes "has at least one of {.bn, .o,
  .a, .so}". Precedence rule (likely .o/.a/.so wins over .bn, with
  `--prefer-source` to override) is open.
- bnc would also gather binary artifacts from `IMPL_PATH` and feed
  them to the linker automatically (today users supply via
  `--cflag`).
- See [`plan-package-search-paths.md`](plan-package-search-paths.md)
  § "Future: binary impl artifacts".

### Build out e2e testing
- We have unit tests (per package) and conformance tests (language
  semantics). What we don't have is a place for **end-to-end tool
  integration tests** — checks that the CLI/loader/runtime wiring
  works the same way across all four tools that load Binate
  packages: `bootstrap`, `bnc`, `bni`, `bnlint`.
- **What's landed (2026-04-30):**
  - Two scripts: `e2e/split-paths.sh` (the original — `-I`/`-L`
    cross-tool contract; covers Stage 1–6 of the package-search-paths
    plan) and `e2e/repl.sh` (9 cases for `bni --repl`: basic call,
    multi-stmt, error recovery, multi-line for-block, braces in
    string literal, plus four Tier 2 cases — func persists, cross-
    decl call, type rejected with diagnostic, bad body recovery).
  - CI hookup at `.github/workflows/e2e-tests.yml` — matrix-
    discovery via `ls e2e/*.sh`, one runner per script, `fail-fast:
    false`.  Standard checkout layout (binate + bootstrap as
    siblings) matches what the scripts assume.  New e2e scripts are
    picked up automatically.
- **Unique challenges this dir still has to solve over time:**
  - **4 tools, not 1.** A single feature (like `-I`/`-L`) needs to
    be exercised on each tool independently, since each parses CLI
    flags separately and threads them into the loader differently.
  - **Multiple build/run modes for the binate-written tools.** bnc,
    bni, and bnlint can each be exercised through several pipelines:
    bnc via boot-comp / boot-comp-comp / boot-comp-comp-comp /
    boot-comp_native_aa64; bni via boot-comp-int / boot-comp-comp-int;
    bnlint via the same chains as bnc. Note that bni cannot be
    interpreted directly by the bootstrap (cmd/bni imports pkg/vm,
    whose float literals the bootstrap lexer doesn't recognize) —
    bni really has to be built via boot-comp first.
    Full e2e coverage of "feature X works" multiplies tools × build
    modes — easily 10+ runs per feature. We don't necessarily want
    that today; figuring out which slice is worth the cost is part
    of building this out.  Today both shipping scripts pick a
    single mode each (split-paths covers all four tools at their
    "default" build path; repl uses boot-comp bni).
  - **Fixture management.** Conformance tests share a single root;
    e2e tests like split-paths need disjoint fixtures, ad-hoc temp
    dirs, optional checked-in subtrees. No standard pattern yet —
    both current scripts use `mktemp -d` + `trap rm -rf` and inline
    `cat <<EOF` heredocs for fixture files.
- **Why these scripts are useful motivating examples:**
  - **split-paths**: the `-I`/`-L` feature is something `bootstrap`,
    `bnc`, `bni`, and `bnlint` should all support **identically** —
    a deliberate cross-tool contract.  e2e is the only layer where
    that contract can be observed directly.
  - **repl**: the `bni --repl` PoC is a multi-stage user-facing
    flow (load module → drive prompt via stdin → check banner +
    prompts + results byte-for-byte).  No unit test could easily
    exercise the full input-to-output transcript; e2e is the right
    layer for "the REPL works end-to-end".
- See [`plan-package-search-paths.md`](plan-package-search-paths.md)
  for the spec `e2e/split-paths.sh` validates and
  [`plan-repl.md`](plan-repl.md) for what `e2e/repl.sh` covers.

### Annotations and C function interop
- Consider implementing annotations (decorators/attributes).
- Specific use case: annotating functions as C functions.
  - **Option A**: annotation in `.bni` — callers know the name and calling convention, but mixes interface with implementation.
  - **Option B**: annotation on the definition (with empty body) — `bnc` generates a trampoline. But empty body is weird (missing return values?).
  - **Option C**: annotation on a call site, indicating it's a C function call. Maybe a "magic" C package so no annotation is needed at all.
  - **Option D**: manual trampolines, with a magic C package for declarations.
