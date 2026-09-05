# Plan: implement `defer` (spec §14.13)

Status: **LANDED** (2026-09-04, commits `9d5895f40` + `4596e1dd2`; spec §14.13 flipped Draft→implemented in docs `c04ab22`).  Both plan reviews and two implementation re-reviews folded in.  Originally drafted for adversarial review (author, 2026-09-04). Spec §14.13 is
ratified (docs `03dd078`); design in [proposal-defer.md](proposal-defer.md).
This plan is the *implementation* design — how the six rules (`stmt.defer`,
`.call`, `.no-loop`, `.exit`, `.return`, `.no-abort`) become code across
token → parser/AST → checker → IR-gen, plus tests.

Tracked as claude-todo.md "`defer` — spec RATIFIED & landed" and task #228.

## 0. Recon summary (where each piece lives)

- **Keyword enum:** `pkg/binate/token.bni` `const ( … keyword_start … keyword_end … )`
  (iota). `pkg/binate/token/token.bn` `TypeName` switch (one case per keyword);
  the lexer's `keywordMap` is auto-built by `buildKeywordMap` walking
  `keyword_start+1 .. keyword_end` and calling `TypeName`, so adding a case +
  enum entry is all the lexer needs. `TriggersASI` is an explicit allow-list
  (defer must NOT be added — no ASI change).
- **AST:** `pkg/binate/ast.bni` `STMT_*` iota enum (+ `NUM_STMT_KINDS`),
  `StmtKindName` switch in `ast.bn`. `Stmt` struct is a wide union; `defer`
  reuses the existing `X @Expr` field for the deferred call (like `STMT_EXPR`).
- **Parser:** `pkg/binate/parser/parse_stmt.bn` `parseStmtInner` dispatches
  keyword-led statements before `parseSimpleStmt`. `parseSimpleStmt` is what
  for/if/switch headers call — it does NOT see `parseStmtInner`, so `defer` is
  automatically excluded from those clauses (it is not a simple statement).
- **Checker:** `pkg/binate/types/check_stmt.bn` `checkStmt`. Loop context is
  `c.InLoop`/`c.InSwitch` (`checker_state.bn`), set around the for-body in
  `checkForStmt`. Function-body entry is `checkFuncDecl` (`check_decl_func.bn`)
  and `checkFuncLit` (`check_func_lit.bn`); neither currently resets
  `InLoop`/`InSwitch`. REPL immediate-mode statement lists are checked via
  `CheckStmtListInScope` (`checker_persistent.bn`) — no enclosing function.
  Builtins parse as `EXPR_BUILTIN`, real calls as `EXPR_CALL` (`ast.bni`), so
  "operand must be a call" is a one-line kind test.
- **IR-gen:** `pkg/binate/ir/gen_stmt.bn` `genStmt` dispatch; `genBlock`
  save/restores `ctx.Vars` per block (`ctx.Vars = ctx.Vars[:savedVarLen]` on
  exit). `gen_func.bn` `genFuncWithPrependedParams` builds the entry block,
  param slots, runs `genBlock`, then emits fall-off void-return /
  unreachable for every non-terminated block. `gen_return.bn` `genReturnStmt`
  is the explicit-return path. Whole-function managed cleanup is
  `emitDecForManagedLocals` (`gen_local_cleanup.bn`), which walks **`ctx.Vars`**
  and RefDecs each managed slot (nil-safe). The **VM fault pads**
  (`emitPadCleanup`) call the *same* `emitDecForManagedLocals` + temp RefDecs
  and emit **no** user calls. Per-arg lowering + ownership is `coerceArg`
  (`gen_call.bn`); call shapes are `genCall` → direct `OP_CALL`, static-method
  `OP_CALL` (mangled `T.M` + receiver arg0), interface `OP_CALL_IFACE_METHOD`,
  func-value `OP_CALL_FUNC_VALUE`, `panic` → `genPanicCall` (`OP_CALL rt.Panic`
  + `OP_UNREACHABLE`).
- **LLVM alloca hoisting:** `codegen/emit_alloca_hoist.bn` re-emits every
  alloca declaration at the top of entry (scanning all blocks), so an `OP_ALLOC`
  emitted in a nested IR block still *dominates* all uses in the LLVM output.
  Hoisting fixes domination but NOT initialization — an un-stored slot holds
  stack garbage. (Native backends give each `OP_ALLOC` a frame slot; same
  garbage-if-uninitialized property.)

## 1. Surface: token / AST / parser (mechanical)

1. `token.bni`: add `DEFER` in the keyword block, alphabetically between
   `DEFAULT` and `ELSE`. (Inserting shifts later iota values — all symbolic,
   safe.)
2. `token/token.bn`: add `case DEFER: return "defer"` to `TypeName`. (Lexer
   map + `IsKeyword` now recognize it; do NOT touch `TriggersASI`.)
3. `ast.bni`: add `STMT_DEFER` to the `STMT_*` enum (before `NUM_STMT_KINDS`);
   `ast.bn`: add its `StmtKindName` case. Document in the `Stmt` field-map
   comment: `DEFER  X (the deferred call expression)`.
4. `parse_stmt.bn`: in `parseStmtInner`, add (near the `RETURN` arm) a
   `token.DEFER` branch → `parseDeferStmt`, which consumes `defer`, parses one
   `parseExpr`, and builds `STMT_DEFER` with `X = <expr>`, `Pos = defer-kw`.
   The operand is parsed as a full expression; the checker enforces call-ness.

   Note: `for defer f();;{}` / `if defer f(); c {}` — `defer` in a for/if/switch
   header — is rejected by the parser naturally: those headers call
   `parseSimpleStmt`, which never reaches the `DEFER` dispatch and fails to
   parse `defer` as an expression (it is a keyword). This realizes the
   "`defer` is not a simple statement" half of the grammar with no extra code.

Tests: `parse_stmt_test.bn` — parse `defer f()`, `defer p.M(a, b)`,
`defer panic("x")` to `STMT_DEFER` with the right `X.Kind == EXPR_CALL`;
`token_test.bn` — `Lookup("defer") == DEFER`, `IsKeyword(DEFER)`,
`!TriggersASI(DEFER)`, `TypeName(DEFER) == "defer"`.

## 2. Checker (`check_stmt.bn` + state)

Add a `checkDeferStmt(c, s)` arm to `checkStmt`. It must implement three rules:

- **`stmt.defer.call`** — reject non-calls and builtin keyword forms:
  `if s.X == nil || s.X.Kind != ast.EXPR_CALL { addCheckError(… "defer requires a
  function, method, or function-value call") }`. `EXPR_BUILTIN`
  (`make`/`cast`/`__c_call`/…) fails this — they are special call shapes, not
  calls (spec §15.1). `panic(x)` parses as `EXPR_CALL` → accepted. Then
  `checkExpr(c, s.X)` to type-check the call normally (records `ResolvedTypeID`
  on the call + subexpressions for IR-gen).
- **`stmt.defer.no-loop`** — reject a defer lexically inside a `for`, with no
  intervening function literal. Implement with a **dedicated checker flag**
  `c.InLoopBody bool` (new, in `checker_state.bn`): set true around the for-body
  check in `checkForStmt` (mirroring the existing `InLoop`), and **saved/reset
  to false in `checkFuncLit`** around the literal body (a defer inside a
  literal belongs to the literal). `checkDeferStmt`: `if c.InLoopBody {
  addCheckError(…, "defer may not appear in a loop; wrap the loop body in a
  function or call the cleanup explicitly") }`. Message text is pinned by the
  spec rule (`stmt.defer.no-loop`).

  Rationale for a *dedicated* flag rather than reusing `c.InLoop`: `InLoop`
  drives break/continue and is NOT reset across function-literal boundaries
  today; reusing it would either (a) mis-reject a defer inside a literal inside
  a loop, or (b) force a change to break/continue's literal-boundary behavior —
  a separate semantics question out of scope here. A dedicated flag reset only
  in `checkFuncLit` is precise and touches nothing else. (The latent
  break/continue-in-literal leak is noted as a follow-up, not fixed here.)
- **enclosing-function requirement / REPL immediate mode** — a top-level
  immediate-mode `defer` (no enclosing function) is rejected. Add
  `c.InFunc bool` (new): set true around the body check in **both**
  `checkFuncDecl` and `checkFuncLit` (save/restore). `checkDeferStmt`:
  `if !c.InFunc { addCheckError(…, "defer requires an enclosing function") }`.
  In `CheckStmtListInScope` (REPL immediate mode) `InFunc` stays false, so a
  bare `defer` there is rejected; a `defer` inside a func literal typed at the
  REPL is fine (the literal sets `InFunc`).

Tests (`check_stmt_test.bn` / a new `check_defer_test.bn`):
`expectNoErrors` — `defer f()`, `defer p.Close()`, `if c { defer f() }`,
`defer func(){ … }()` and a defer inside a literal inside a for; `expectError`
— `defer 1+1` / `defer x` (not a call), `defer make(T)` (builtin),
`for { defer f() }` (no-loop), `for { go' … }` nested-block defer,
immediate-mode `defer f()` (via a `CheckStmtListInScope` harness).

## 3. IR-gen — the core (fixed frame slots)

This is the load-bearing part. Chosen design = the proposal §6 sketch: **fixed
frame slots + static LIFO emission**, no closures, no heap allocation.

### 3.1 Why not closures (recorded, so the review can challenge it)

A tempting alternative lowers `defer C` to a synthesized `func(){ C }` capturing
the eager operands, reusing the whole call+capture pipeline. Rejected:
(1) IR-gen cannot synthesize a *checked* func literal — captures are recorded by
`checkFuncLit` during checking; manufacturing them in IR-gen means
re-implementing capture analysis. (2) An AST-level desugar before checking would
have to hoist each argument into a temp local + build a literal, distorting
positions/messages, and still needs eager-eval temps. (3) Even the stack-alloc
`*func` closure form registers its cleanup at *block* depth (truncated at block
exit), so a conditional/nested defer would need function-scope re-registration
anyway — i.e. the same slot machinery. Closures buy uniform call-shape
reconstruction at the cost of allocation-model and checker/IR-gen-boundary
violations. Fixed slots are the ratified design and fit the architecture.

### 3.2 State

New per-function IR-gen state on `GenContext` (`gen_func.bn`):

```
Defers @[]@DeferSite   // one per lexical defer in this function, lexical order
```

`DeferSite` (new type, `gen_defer.bn`):

```
type DeferSite struct {
    Stmt      @ast.Stmt      // identity key (matched by `same` at gen time)
    ArmedSlot @Instr         // bool alloca in entry, false-init
    Shape     int            // DEFER_DIRECT | _METHOD | _IFACE | _FUNCVAL | _PANIC
    Sym       @[]char        // resolved callee symbol (DIRECT / METHOD)
    OperandSlots @[]@Instr   // typed allocas in entry: recv (METHOD/IFACE) /
                             //   funcval (FUNCVAL) then each argument
    OperandTypes @[]@types.Type // natural (pre-coercion) type of each slot
    ParamTypes   @[]@types.Type // callee param types (coercion targets), arg-aligned
    ResultType   @types.Type    // for discarded-result cleanup
}
```

`ctx.Defers` is a **function-level list, never truncated** (unlike `ctx.Vars`),
so a DeferSite registered while gen'ing a nested block survives to every exit.

### 3.3 Pre-pass (in `genFuncWithPrependedParams`, after param setup, before `genBlock`)

Walk the body AST in **lexical order, not descending into function literals**
(their defers belong to their own lifted function's pre-pass). For each
`STMT_DEFER` found, in order, build its `DeferSite` and **emit its slots into
the entry block**:

- `ArmedSlot = entry.EmitAlloc(bool)`; `entry.EmitStore(ArmedSlot, false)`.
- Classify the shape + resolve callee/receiver/arg types from the checker
  (`ctx.Checker.ExprType(subexpr.ResolvedTypeID)`; callee param types from the
  resolved callee function type). One shared classifier
  `classifyDeferCall(ctx, callExpr) -> (shape, sym, operandTypes, paramTypes,
  resultType)` is used by BOTH the pre-pass and the gen-time store (single
  source of truth for shape/typing).
- For each operand slot: `slot = entry.EmitAlloc(opType)`. If `opType` is
  managed (`isManagedPtrType`/`isManagedSliceType`/`isManagedFuncValueType`/
  `isManagedIfaceValueType`) OR `needsStructCopy(opType)`: **nil/zero-init at
  entry** (`entry.EmitStore(slot, EmitConstNil(opType))` or the struct-zero
  form) AND **register it in `ctx.Vars`** (a `VarSlot` with this type) so
  `emitDecForManagedLocals` (return + fall-off + fault pads) RefDecs/dtors it,
  nil-safe. Non-managed scalar/raw slots need no init (only read when armed)
  and are NOT registered.

Both the entry-block placement (domination + one-time init before any early
exit) and the `ctx.Vars` registration at entry depth are load-bearing — see
proposal §6's two conditions. Order matters: register the defer operand slots
in `ctx.Vars` *before* `genBlock` so a `savedVarLen` taken by any inner block
is above them, i.e. they are never truncated.

### 3.4 At the defer statement (`genStmt` `STMT_DEFER` arm → `genDeferStmt`)

1. Find the pre-registered `DeferSite` for this `stmt` (linear scan of
   `ctx.Defers` matching `same(site.Stmt, stmt)` — robust to any
   traversal-order skew between the pre-pass and gen).
2. Evaluate operands **eagerly**, in source order, via `genExpr` (callee/receiver
   first for METHOD/IFACE/FUNCVAL, then each argument), using the SAME
   classifier so the operand list lines up with the pre-alloc'd slots.
3. Store each operand into its slot. For a managed operand: **RefInc into the
   slot** (the slot now owns one reference), matched by the whole-function
   `emitDecForManagedLocals` RefDec after defers run — this realizes "retained
   with the function's lifetime, released after all pending deferred calls have
   run". For a `needsStructCopy` by-value struct: copy-with-field-RefInc into
   the slot. For a plain scalar/raw: plain store. (No coercion here — operands
   are stored in natural form; coercion happens at the call, so a
   managed→raw arg retains its pre-conversion managed value, per `stmt.defer`.)
4. `entry`-independent: `b.EmitStore(site.ArmedSlot, true)`.
5. `emitTempCleanup` for the defer statement's own scratch temporaries (the
   statement's *other* temporaries release at end of statement, per the §18.4
   `mem.temporary` carve-out — only the retained operands persist).

A defer statement executes at most once per activation (no-loop + no goto), so
each slot is stored at most once — no double-RefInc.

### 3.5 At each function exit — `emitPendingDefers(ctx, b) -> b` (new)

Emit, for `ctx.Defers` in **reverse** order (LIFO = reverse lexical):

```
for i := len(ctx.Defers)-1; i >= 0; i-- {
    d := ctx.Defers[i]
    armed := b.EmitLoad(d.ArmedSlot, bool)
    // if armed { <call> } ; continue
    thenB, contB := new blocks; b.EmitCondBranch(armed, thenB, contB)
    // thenB: load operands, coerceArg to ParamTypes, emit the shape's call,
    //        release a discarded managed result, temp-cleanup, jump contB
    b = contB
}
return b
```

The per-shape call emit in `thenB` (from stored, loaded operands — never
re-`genExpr`'ing the AST):

- **DEFER_DIRECT:** load arg slots → `coerceArg` each to `ParamTypes[k]` →
  `b.EmitCall(d.Sym, args, d.ResultType)`.
- **DEFER_METHOD:** load recv slot + arg slots → coerce → `b.EmitCall(d.Sym,
  recv::args, ResultType)` (static method = direct call with mangled `T.M` +
  receiver first; `d.Sym` resolved in the pre-pass exactly as `genMethodCall`
  resolves it).
- **DEFER_IFACE:** load the stored **interface-value** receiver (a managed/raw
  iface value snapshot) + arg slots → re-dispatch `OP_CALL_IFACE_METHOD` from
  the loaded receiver (the snapshot carries the vtable; re-dispatch is
  deterministic). Factor the tail of `genInterfaceMethodCall` into a core
  `emitIfaceMethodCallCore(ctx, b, recvInstr, methodInfo, args, resultTyp)`
  taking an already-evaluated receiver, shared by the normal path and here.
- **DEFER_FUNCVAL:** load the stored func value + arg slots → coerce →
  `b.EmitCallFuncValue(fv, args, ResultType)`.
- **DEFER_PANIC:** load the stored message operand → `coerceArg` to the
  panic-message slice type → `EmitCall rt.Panic` + `EmitUnreachable` (mirrors
  `genPanicCall`). The panic terminates `thenB`; `contB` (the not-armed path)
  continues to earlier defers — so an armed deferred panic abandons the rest
  (`stmt.defer.no-abort`), while earlier defers still run if it wasn't armed.

Discarded managed result: if `ResultType` is managed, RefDec the call result
immediately in `thenB` (spec: "a discarded managed result is released
immediately after the call returns, before the next pending deferred call
runs"). Reuse the return-path helpers.

Insertion points (call `emitPendingDefers` **before** the existing
`emitDecForManagedLocals`):
- `gen_return.bn` `genReturnStmt`: both the void-return early arm and the
  main tail — after the Axiom-3 return-value RefInc/copy, before
  `emitDecForManagedLocals(ctx, b, stmt.Exprs)`. (Order: return operands
  acquire their owning ref first; defers run; locals release — `stmt.defer.return`.)
- `gen_func.bn`: the fall-off void-return arms (the `entry`-tail arm and the
  "check all blocks" arm) — before their `emitDecForManagedLocals`. Non-void
  functions fall off to `EmitUnreachable` (no normal exit → no defers there,
  correct — a non-void fall-off is a missing-return the checker already
  rejects). Guard: only emit defers on a *void return*, not on unreachable.

The **VM fault pads** are deliberately untouched: `emitPadCleanup` calls
`emitDecForManagedLocals` (RefDecs the operand slots — nil-safe, no user calls)
but never `emitPendingDefers`, so a VM-isolated fault runs no deferred calls yet
balances the retained operands — `stmt.defer.no-abort`, and memory stays
balanced. This is why the operand slots live in `ctx.Vars` rather than a
private list.

### 3.6 Interaction checks (call these out in tests)

- **Discarded-result / temp cleanup on the fall-off path:** the fall-off arms
  in `gen_func.bn` have no `emitTempCleanupForReturn`. A deferred call whose
  `coerceArg` registers a temp (e.g. a string-literal arg coerced to `@[]char`)
  would leak there. Fix: `emitPendingDefers`' `thenB` runs its own local temp
  cleanup after each call (self-contained), not relying on a trailing
  statement-temp sweep.
- **`needsStructCopy` operand at the call:** stored value is an owned struct
  (fields RefInc'd); `coerceArg` at the call copies again for the callee. Verify
  no double-free: slot's copy released by whole-func cleanup; callee's copy by
  the callee. Covered by a managed-field-struct-arg conformance test.
- **`ctx.CurBlock` discipline:** `genExpr` can split blocks; after each operand
  eval do `b = ctx.CurBlock`, as the FFI arg loop does.

## 4. BUILDER constraint

`defer` postdates the pinned BUILDER, so **no `defer` may be used inside
cmd/bnc's BUILDER-compiled tree** (the compiler compiling itself) until a future
BUILDER carries it. All new code (checker/IR-gen/parser) is written *without*
using `defer`. Adding the keyword + `STMT_DEFER` compiles fine under the current
BUILDER (an enum entry + a switch case is not a new language feature) — verify
the gen1 build stays green.

## 5. Tests

- **Unit:** token, parser, checker (§1–§2 above), plus IR-gen unit tests
  (`gen_defer_test.bn`): a `defer f()` emits an armed-flag store + a
  conditional call before the return's local-dec; LIFO order for two defers;
  a managed operand slot is registered for cleanup.
- **Conformance** (`conformance/NNN_defer_*`), cross-mode + cross-target:
  1. ordering (LIFO): `defer print(1); defer print(2); …` → 2 then 1.
  2. conditional defer: `if cond { defer … }` runs iff taken.
  3. loop rejection: `for { defer f() }` → compile error (`.error` file).
  4. eager arg eval: `x:=1; defer print(x); x=2` prints 1.
  5. managed operand retention: `defer useSlice(mSlice)` — no leak, correct
     value at exit (managed ptr, managed slice, @func, @Iface variants).
  6. managed→raw operand: `defer takesRaw(mSlice)` where param is `*[]T` —
     backing retained, borrow delivered at call time, no leak/UAF.
  7. method + interface defers: `defer p.Close()`, `defer iface.M()`.
  8. `defer panic("x")` aborts with the message; a defer scheduled *after* it
     does not run; one scheduled before does.
  9. return-value interaction: `return mkManaged()` then a defer that inspects
     state — deferred call observes post-evaluation state, returned managed
     value intact (`stmt.defer.return`).
  10. no-abort: a real trap (e.g. bounds fault) runs NO defers.
  Include `.error`/`.xfail.<mode>` per the Bug Discovery Protocol for anything
  not yet green.

## 6. Landing increments (each independently green)

- **Inc A — surface + checker + fail-loud IR-gen.** token/AST/parser (§1),
  checker rules (§2), and a `genDeferStmt` that emits a clean `COMPILE_ERROR`
  ("defer codegen not yet implemented") instead of silently dropping the
  statement (no miscompile). Tests: unit (token/parser/checker) + the loop /
  not-a-call / immediate-mode `.error` conformance tests. Green: defer is
  *recognized and rejected loudly at codegen*, never miscompiled.
- **Inc B — IR-gen lowering (§3).** The core: pre-pass, slots, store,
  `emitPendingDefers`, all shapes. Drops the fail-loud. Full runtime
  conformance suite (§5). This is the big, review-critical commit.

Splitting keeps Inc A trivially correct and isolates the codegen risk in Inc B,
which gets the adversarial-review + full cross-mode/cross-target verification
(x86_64 Rosetta, aarch64 host, arm32 Docker+qemu) before landing — same bar as
the FFI widening.

Whether to further split Inc B (e.g. DIRECT/METHOD/FUNCVAL/panic first,
IFACE + managed→raw second) is a scope question for the owner; the spec + todo
require all of them, so any split is landing-cadence only, not a scope cut.

## 7. Open questions for review

1. **Shape reconstruction vs. shared cores.** §3.5 reconstructs each call shape
   from stored operands. IFACE re-dispatch wants a factored
   `emitIfaceMethodCallCore` shared with `genInterfaceMethodCall` — touches an
   existing hot path. Acceptable, or keep IFACE emission fully self-contained in
   `gen_defer.bn` (more duplication, zero risk to the normal path)?
2. **Storing the whole iface value vs. eager fnptr+data.** Plan stores the
   iface value and re-dispatches. Spec says the *callee* is evaluated eagerly;
   re-loading an immutable vtable from a snapshot is observationally identical.
   Any objection to snapshot-then-redispatch over eager-fnptr-extract?
3. **`needsStructCopy` operand double-copy** (§3.6) — confirm the ownership
   ledger (slot copy + callee copy) has no double-free / leak.
4. **Pre-pass typing fidelity.** `classifyDeferCall` must resolve operand types
   identically to what `genExpr` produces at the defer site, else slot/value
   type mismatch. Is driving both from one classifier + asserting
   `same-type(stored, slot)` at gen time enough of a guard?
5. **Fall-off exits.** Are the two `gen_func.bn` void-return arms (entry-tail +
   "check all blocks") the complete set of normal void exits, or can other
   non-terminated blocks reach a void return without passing through them?

## 8. Adversarial review outcomes & design revisions (2026-09-04)

Two independent adversarial reviews ran against §1–§7: one on the
memory/refcounting-ownership lens, one on control-flow/exit-path/spec/front-end.
**Both verdicts: SOUND-WITH-MUST-FIXES.** The fixed-frame-slot approach is
correct and no reviewer preferred the closure alternative. The front-end
(token/parser/checker) design is sound as written and fully cleared (call-only,
no-loop `InLoopBody`, `InFunc`/REPL-immediate, parser headers/ASI, builtin
rejection, pre-pass/literal non-double-count, terminating analysis). The
must-fixes below apply to §3 (IR-gen) and supersede the conflicting prose there.

### MUST-FIX 1 (critical, mem-C1) — coerce operands to call-ready form EAGERLY at the defer site, not at exit

The §3.4/§3.5 "store natural (pre-coercion) form, run `coerceArg` at exit" is
**wrong** for coercions keyed on the argument's IR opcode / untyped-ness rather
than its type: string-literal→`@[]char`/`@[]readonly char` (`OP_CONST_STRING`,
`gen_call.bn:56`), `nil`→typed-slice (`OP_CONST_NIL`, `:70`), untyped-scalar→
param width (`coerceScalarWidth`, `:120` / `gen_binary_width.bn:103-109`). A
value LOADED from a slot is `OP_LOAD` with a concrete type, so those arms can
never fire at exit → a mis-shaped value is stored (2-word const into a 4-word
managed-slice slot), and the callee's entry `emitManagedSliceRefInc` then
RefInc/RefDecs a **garbage refptr** → heap corruption / UAF for any `@[]char`/
`@[]T` param. (`defer panic("x")` may survive by luck since panic's param is a
raw `*[]readonly char`; `@[]char` params do not.)

Revised operand model:
- **At the defer site (eager):** evaluate each operand (`genExpr`), then run the
  **constant/literal + width** coercions immediately (`EmitStringToChars`,
  nil→typed, `coerceScalarWidth`), producing a **call-ready, param-typed** value.
  Store it into a **param-typed** slot, acquiring slot ownership via the tested
  `emitStoreManagedSlot(ctx, b, slot, val, slotTyp, isInit=true)`
  (`gen_store_slot.bn:49` — the move-if-fresh / RefInc-if-borrow dispatcher;
  reuse it rather than a hand-rolled RefInc — mem-N4). `OperandTypes[k]` = the
  **param type** for these.
- **The one exception — managed→raw args** (`@[]T`/`@T` arg to a raw `*[]T`
  param): store the **pre-conversion managed value** in a **managed-typed** slot
  (spec `stmt.defer`: retain the pre-conversion managed value, deliver the borrow
  at call time). `OperandTypes[k]` = the managed type here.
- **At exit (type-driven, replayable from a load):** ONLY the borrow/delivery
  coercions — `EmitManagedToRaw` for a managed→raw slot, the `@Iface`
  deliver-ref RefInc (a load is never "fresh" so it takes the RefInc arm,
  `gen_call.bn:110-118`), and the callee-copy `emitStructCopy` for a
  `needsStructCopy` operand (`:82-95`). No constant/width coercion at exit.

This also dissolves §3.6 bullet 1 (the string-temp-at-exit worry): the
string→chars copy now happens at the defer site and its temp is swept by the
defer statement's own end-of-statement `emitTempCleanup`.

### MUST-FIX 2 (major, mem-M1) — DEFER_METHOD receiver conversion is eager too

A pointer-receiver method on an addressable value receiver (`defer v.Close()`,
`Close` has `*T` recv, `v` a struct local) needs the receiver **address**,
computed by `applyReceiverConversion` (`gen_method_recv.bn:23`) via
`genSelectorPtr` **from the AST `srcExpr`** — unreconstructable at exit from a
stored value (taking `&slot` addresses a copy → pointer method mutates the copy,
silent divergence). Fix: run `applyReceiverConversion` **at the defer site**
(srcExpr in hand) and store the already-converted receiver (the `*T` address for
a pointer method; the loaded value for a value/`@T` method). At exit just load +
call. (`defer p.Close()` with `p @T` value-or-managed receiver is already fine.)

### MUST-FIX 3 (critical, cf-F1) — fall-off must run defers while body-scope locals are still LIVE

On the fall-off exit, the outermost body `genBlock` (`gen_stmt.bn:22`) ALREADY
runs `emitDecForScopeVars` + truncates `ctx.Vars` (`:57-58`) for the body's own
managed locals BEFORE control returns to `gen_func.bn`'s fall-off arm — so
inserting `emitPendingDefers` there runs defers AFTER the body locals were
released. That violates `stmt.defer.exit` ("deferred calls run while the
function's still-open scopes' locals are live; then locals release") and makes
`return` and fall-off **diverge** (return centralizes cleanup in
`genReturnStmt`, which runs while `ctx.Vars` is full because `genBlock` skips
cleanup on a terminated block). UAF for a raw operand borrowing a managed
body-local (`p := makeManaged(); defer inspect(&p)` on fall-off frees `p` before
`inspect` runs).

Fix: route the function-body fall-off through the SAME epilogue as return, with
body-scope locals still live. Add `genBlockEx(ctx, b, stmt, skipFalloffCleanup)`
(`genBlock` = `genBlockEx(…, false)`); `gen_func.bn` calls it with
`skipFalloffCleanup = (len(ctx.Defers) > 0)` for the body — on a non-terminated
(fall-off) body exit it then skips the body-scope `emitDecForScopeVars` +
truncate, leaving the full `ctx.Vars` (body + entry) intact so `gen_func.bn`'s
fall-off arm runs `emitPendingDefers` THEN `emitDecForManagedLocals` over the
FULL set. Gated on has-defers → zero behavior change for defer-free functions.
(Return path unchanged and already correct.) Test: a void function that falls
off with a `defer` observing a managed local via a raw borrow, and its
`return`-terminated twin, asserting identical behavior.

### MUST-FIX 4 (major, cf-F2) — `emitPendingDefers` uses SCOPED temp cleanup, never the clearing form / never SP_RESTORE at a return site

`emitTempCleanup`/`…Body` CLEAR the shared `ctx.Temps` and emit `OP_SP_RESTORE`
when `StmtGrewSP` (`gen_temp_cleanup.bn:71,106,53-58`). At the return value-arm
insertion point `ctx.Temps` already holds the return expression's in-flight
temps (e.g. `return "lit"`'s `OP_RODATA_MSLICE_COPY`, `gen_return.bn:100`);
clearing them early or emitting `SP_RESTORE` would corrupt the returned value on
the VM (`SP_RESTORE` truncates the aggregate before `BC_RETURN` copies it back).
Fix: `emitPendingDefers` snapshots `savedTempLen := len(ctx.Temps)` on entry and
uses the scoped `emitTempCleanupSince(ctx, b, savedTempLen)`
(`gen_temp_cleanup.bn:150`) inside each `thenB` — never the clearing form, never
`SP_RESTORE` at the return site. This also covers the fall-off leak (no trailing
`emitTempCleanupForReturn` there). A temp created in the armed-only `thenB` must
be RefDec'd IN `thenB` (it does not dominate `contB` — a `contB` RefDec = UAF).

### MUST-FIX 5 (major, cf-F3 / mem-N1) — pre-pass walk mirrors `genStmt` recursion; `genDeferStmt` HARD-FAILS on a missed `DeferSite`

The §3.3 pre-pass walk must enumerate exactly the `STMT_DEFER` set `genStmt`
reaches: `STMT_BLOCK`, `STMT_IF` Body+Else, `STMT_SWITCH` case bodies,
`STMT_TYPE_SWITCH` case bodies, nested blocks — **not** descending into function
literals (each literal re-enters `genFuncWithPrependedParams` with its own
pre-pass). A `defer` in an `if`/`switch`/nested block is legal (only loops are
banned), so under-walking silently drops a legal defer. `genDeferStmt` MUST
abort (verify-style) when `same(site.Stmt, stmt)` finds no site — never no-op
(the current `genStmt` default `gen_stmt.bn:145` IS a silent drop). Test a defer
nested in each construct.

### Minor fixes to fold in

- **mem-N2** — a discarded managed **struct** result (`needsStructCopy(ResultType)`)
  needs store-to-temp + `emitStructDtor`, not a scalar RefDec.
- **mem-N3** — `emitPendingDefers` uses raw `EmitCall` (no `OP_SP_RESTORE`
  between deferred calls); not a leak (bounded by static defer count, reclaimed
  by `BC_RETURN`). Add a comment so nobody "fixes" it by inserting SP restores.
- **cf-F4** — the `gen_func.bn:222` check-all-blocks loop re-reads
  `len(f.Blocks)` while `emitPendingDefers` appends blocks; safe only because
  every runner block is terminated in-iteration. Snapshot `n := len(f.Blocks)`
  before the loop and/or assert runner blocks are terminated.
- **cf-F5** — the IIFE operand `defer func(){…}()` classifies as the FUNCVAL
  shape (the literal is lifted to its own `Func` with its own pre-pass; not
  double-counted). Add it to the §3.5 shape list + a conformance test.
- **cf-F6** — Inc A's `genDeferStmt` fail-loud is an abort/panic (there is no
  first-class IR-gen user-error channel); it is genuinely non-miscompiling
  (checker already rejects invalid defers; only valid ones reach codegen and
  abort). Describe it as an abort, not a "clean COMPILE_ERROR". Since a valid
  `defer` would abort the compiler in Inc A, prefer landing Inc A and Inc B
  close together (or as one commit) so there is no lasting "valid defer crashes
  bnc" window — the keyword is brand-new (zero collisions) so this is not a
  regression, but keep the window short.

### Cleared by review (no action)

Ownership ledger balances for all four managed kinds, the `@Iface` move model,
managed→raw, struct-copy, conditional/return/fault paths (mem scenarios 1–10);
the armed flag is needed only for CALL correctness, not cleanup (cleanup is
nil-safe). The four exit-insertion points are the complete normal-exit set;
LIFO=reverse-lexical-with-armed-gate; no-abort holds on both compiled traps and
VM fault pads; `defer panic` LIFO abandonment is correct; the no-loop flag
covers both `for` shapes; parser/ASI/REPL/builtin-rejection all correct.
