# Native AArch64 ABI: stack args + sret returns

The native arm64 backend currently spreads aggregate arguments into
X0..X7 and packs aggregate returns into X0..X(N-1) for N up to 8.
When a function's args or return don't fit in that budget, overflow
words are **silently dropped**, producing wrong results (135) or
SIGBUS (252, 253).

This plan extends the calling convention to handle overflow per
AAPCS64.

## Scope

Three conformance tests fail because of this:

- **135**: `appendEntry(@[]Entry, Entry)` — 4 + 5 = 9 word args. The
  Val word of Entry is dropped → wrong reads later.
- **252**: `appendNode(@State, Node)` where Node is 17+ words. Arg
  overflow → SIGBUS as the callee reads garbage past its received args.
- **253**: `addNode(@Parser, Node)` (15-word arg) and
  `makeNodeFromToken(Token) Node` (6-word arg, 15-word return). Arg
  overflow + return overflow.

## Design

### Argument passing

Mirror AAPCS64 §B.4:

- NGRN (next general register number) starts at 0.
- For each arg in order:
  - **Scalar**: if NGRN < 8, allocate at X(NGRN), NGRN += 1.
    Else, place on stack (8-byte slot).
  - **Aggregate** of N words:
    - If NGRN + N ≤ 8: spread X(NGRN)..X(NGRN+N-1), NGRN += N.
    - Otherwise: NGRN := 8 (consume the rest), place ENTIRE
      aggregate on stack (N×8 bytes, 8-byte aligned).

This matches what clang does for our LLVM-emitted IR at the AAPCS64
boundary, so callee/caller agree without us having to special-case
the runtime.

### Stack-arg layout

Outgoing args sit at the **bottom** of the caller's frame:

```
high addr
  [caller's saved fp/lr at original_sp - 16]
  [caller's spills/allocas]
  [caller's outgoing-args area, M bytes]    <- caller_sp at bl callee
low addr
```

Inside the callee:

```
  [caller's outgoing-args area]              <- callee entry sp
  [callee's saved fp/lr, 16 bytes]
  [callee's spills/allocas]
  [callee's outgoing-args area, M' bytes]    <- callee sp during body
```

So a callee reads the i-th incoming stack-arg word at
`[sp + frame_size + 16 + 8*i]`. (The +16 is for fp/lr.)

A caller writes the i-th outgoing stack-arg word at `[sp + 8*i]`
during the call sequence (before `bl`).

### Frame planning

`PlanFrame` already lays out spills/allocas starting at offset 0.
We shift everything by `OutgoingArgsSize` (the max stack-arg space
across all calls in this function) and reserve the [0..M) region
for outgoing args. Computing `OutgoingArgsSize` requires walking
each OP_CALL/OP_CALL_BUILTIN and simulating the AAPCS dispatch.

Add `OutgoingArgsSize int` to `RegMap`.

### Aggregate returns

When an aggregate return doesn't fit in X0..X7 (more than 64 bytes),
use the AAPCS64 "indirect result" register **X8**: the caller passes
a pointer to a stack buffer in X8; the callee writes the return
value through X8 and returns void.

Affected code:

- `emitCall` return-collect: if return type > 64 bytes, allocate a
  data region (already done by PlanFrame for aggregate returns), pass
  `add x8, sp, #dataOff` before `bl`, after `bl` produce the pointer
  in `rd`.
- `emitFunc` prologue: if function returns >64 bytes, X8 is the sret
  pointer — store it to a hidden spill slot.
- `emitReturn`: if function returns >64 bytes, copy the return value
  through the X8 pointer.

For now, we don't have a test for >64-byte returns; arg overflow is
the immediate need. Token (48B / 6 words) returns fine in X0..X5.
Node (120B / 15 words) returns are needed for `makeNodeFromToken`,
which IS >64 bytes — we DO need sret to fix 253.

## Steps

1. Extract AAPCS dispatch helper to `pkg/native/common`. Returns
   per-arg assignments (reg index OR stack offset) and the total
   `outgoing_args_words`.
2. Extend `PlanFrame` to compute `OutgoingArgsSize` and shift all
   alloc/spill offsets by it.
3. Update `emitCall` (caller): use the dispatch helper to write reg
   args into argRegs and stack args into [sp + N×8].
4. Update `emitFunc` prologue (callee): use the dispatch helper to
   read incoming reg args from argRegs and incoming stack args from
   [sp + frame_size + 16].
5. Add sret support: call return-collect uses X8; emitFunc prologue
   stashes X8; emitReturn writes through X8.
6. Tests: extend `TestEmitCallSpreadsRawSliceArgAcrossTwoRegs` to
   cover overflow. Add tests for AAPCS dispatch helper directly.
7. Conformance: 135, 252, 253 should pass.

## Non-goals

- Float register passing (D0..D7) — pkg/native doesn't support floats
  yet (xfailed).
- HFA / HVA homogeneous aggregate special cases — the AAPCS rules for
  pure-float aggregates. We don't have floats yet.
- Variadic args — Binate has none.
