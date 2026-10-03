# Plan: enlarge the x64 GP home pool

Status: step 1 (per-register clobbers in the shared allocator, `RegClassDesc.RegClobbers`) landed
in binate `70e6b95de` (2026-10-03); no backend sets the hook yet. The hook receives the RegMap, so
registers used by a folded instruction are named at its consumer. Steps 2–3 not started. Tracked in `claude-todo.md` ("x64: enlarge the GP home pool").

## Why

The native x64 backend homes 9 GP registers; LLVM allocates 15. record-churn's hot mix loop
needs ~11 at once (8 carry lanes, `i`, the element address, the output element pointer), so the
linear scan spills carry lanes every iteration. Keeping the elided aggregate load's address in a
register made record-churn ~4% *slower* for exactly this reason (see the todo entry "an elided
aggregate load's address lives on the stack"). Nothing in that loop is a clobber point (no
returning call), so caller-saved homes are usable there.

## Current register roles (`pkg/binate/native/x64`)

| Register | Role today |
|---|---|
| RBX, R12–R15 | callee-saved homes (`x64AllocatablePool`) |
| RSI, RDI, R8, R9 | caller-saved homes (`x64CallerSavedHomePool`); call sites place args homed in them by parallel move (`x64_call_homes.bn`) |
| R10, R11, RCX, RDX | per-instruction scratch pool (`regPool`, allocated in that order; `allocReg` prefers a free one, so any of the four can be handed out) |
| RAX | reserved: return value, DIV/IDIV and one-operand IMUL/MUL, `make`/`box` result, call-site shuttle and parallel-move swap temp, float-convert scratch |
| RBP | frame pointer |
| RSP | stack pointer |

## Constraints per register

- **RBP — not a candidate without a separate decision.** `rt.CaptureNativeFrames` walks the RBP
  chain for native stack traces (`x64_stackframes.bn`). Freeing it means dropping native frame
  capture or moving to unwind tables. Out of scope unless decided otherwise.
- **R10, R11 — keep as the guaranteed scratch pair.** Every lowering may take them; R11 is also
  the indirect-call target register (`x64_call_indirect.bn`, `x64_iface.bn`).
- **RCX** — fixed uses in function bodies: a shift by a register count (CL; `emitBinop`), the
  signed-division guard (`emitDivCheck`), the third return register (`retGpReg`), argument
  register 4 at calls. Otherwise taken as the 3rd scratch.
- **RDX** — fixed uses: DIV/IDIV and the magic-number multiply-high (`x64_muldiv.bn`, which
  claims it), `make_slice` length argument, the second return register (two-word returns, SSE
  aggregate returns), `emitDivCheck`, `emitStringToCharsCopy`, argument register 3, float-convert
  scratch (`pickScratchGP`). Otherwise the 4th scratch.
- **RAX** — fixed uses: everything listed in the table, plus the iface/vtable value lowering
  (`x64_dispatch_value.bn`) and the return paths.

Shims, trampolines and the C-export entry are separate functions with no homes, so their use of
R10/R11/RAX does not constrain the pool.

## Measured scratch demand

Instrumenting `allocReg` while the native x64 compiler compiled all of `cmd/bnc` (-O2) gave the
maximum number of pool registers held at once, per op:

- **4:** `shr` / `shl` (register count — the RCX reservation walks through R10/R11 to reach RCX),
  `return`, `madd`, `div`
- **3:** `sub`, `add`, `store`, `load`, `lt`, `rem`, `get_elem_ptr`, `iface_value`, `call`,
  `call_iface_method`
- **≤2:** everything else

This is empirical (what `cmd/bnc` exercises), not a guarantee; the design below enforces it.

## Design

Make RCX, RDX and RAX caller-saved homes (12 homes), keeping R10/R11 as the guaranteed scratch
pair. A home in one of them must not be live across an instruction that uses that register.

1. **Per-register clobber positions in the shared allocator** (`native/common`). Today a
   caller-saved home is avoided only across `ClobberPositions` (returning calls). Add an
   op→registers clobber query to `RegClassDesc` (e.g. `RegClobbers func(@ir.Instr) @[]int`) and
   make linear-scan eligibility per register: register r may hold an interval only if no
   position in the interval clobbers r. Native arm32 has the same shape of constraint (fixed
   registers in some lowerings), so this is shared infrastructure, not x64-only.
2. **x64 clobber declarations.** For each in-body lowering, declare which of RCX/RDX/RAX it may
   touch: register shifts (RCX), DIV/REM and magic divide/multiply (RAX, RDX), `make`/`box`/
   `make_slice` and every call (all — already clobber points), two-word and SSE returns
   (RAX/RDX/RCX — returns end the function, so only values live into the return matter),
   `emitDivCheck` (RCX, RDX), float conversion (RAX, RDX), iface value (RAX), string copy (RDX),
   and every op whose scratch demand can exceed 2 (RCX, then RDX).
3. **Scratch allocation that respects homes.** `allocReg` must skip a pool register holding a
   home that is live at the current instruction, and fail loud if the op then runs out — a
   declared-clobber gap must be a compile error, never a silent overwrite of a home. Reduce the
   shift lowering's RCX reservation loop so a shift needs RCX plus at most two scratches.
4. **Call and return marshalling.** RCX and RDX are argument registers, so homes in them join the
   existing argument parallel move (`x64_call_homes.bn`), which today swaps cycles through RAX;
   with RAX a home, the swap temp becomes R11 (free at a call). A home in RAX can receive a call's
   result directly.
5. **Prologue.** Parameter landing (`x64_emit_params.bn`) uses RAX/R11; check ordering against
   homes in RCX/RDX (arriving arguments 3 and 4).

## Validation

- Native x64 conformance at -O0 and -O2, the native self-compile fixpoint, and unit tests for
  `native/common` (allocator) and `native/x64`.
- Since step 1 changes shared allocator code: native arm32-linux conformance too (its pools are
  unchanged, so it must be bit-identical in behavior), and aa64 via CI.
- Measure record-churn (callgrind instruction counts, N=50 and N=300) and the native self-compile
  time, then re-measure the parked elided-load-address change on top.

## Size

Step 1 is a contained allocator change with unit tests. Steps 2–3 are the bulk: an audit of
every in-body x64 lowering (~30 files touched by fixed registers today) plus the fail-loud
check that catches anything missed. Steps 4–5 are local. Suggested landing order: (1) alone
(no behavior change: x64 declares nothing yet); (2)+(3) with RCX/RDX first; then RAX with (4).

## Open decision

- RBP: keep as frame pointer (assumed above), or consider freeing it later together with a
  different frame-walking scheme.
