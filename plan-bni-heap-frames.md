# Plan: heap-allocated frames in bni's eval loop — stop host-stack growth on extern callbacks

**Status**: design / not started
**Severity**: MAJOR
**Tracks**: `claude-todo.md` MAJOR entry
  "bni VM consumes host call stack per interpreted frame — `*-int*`
  unit-test modes overflow on deep recursion"
**Blocks (today)**: every `builder-comp-int*` / `builder-comp-comp-int`
  CI lane.  Workaround: `ulimit -s` bump in the runner scripts
  (landed).

## Problem

bni's interpreter is iterative for pure VM-to-VM calls — `execLoop`
handles `BC_CALL` by pushing a frame on `vm.Stack` (the heap-
backed register/spill stack) and continuing without recursing
into a fresh `execLoop` invocation.  That part is correct and
not the bug.

The bug is in `execExtern`'s callback path.  When an opcode
dispatches a native extern that itself calls back into the VM
(e.g. `vm.CallFunc` from a runtime helper, or a callback
registered with `extern_register_std`'s registry), the host
stack pattern becomes:

```
execLoop (2304 B)
  → execExtern → native callback (varies)
    → VM.CallFunc (336 B)
      → execFunc (240 B)
        → execLoop (2304 B)
          → execExtern → native callback
            → VM.CallFunc
              → execFunc
                → execLoop
                  → …
```

Each cross-VM boundary adds ~3 KB of host stack (the four
boxed-in frames above).  Code paths in the type-checker /
IR-gen / pattern-matcher often pattern-match recursively over
deeply-nested AST trees through extern-registered helpers —
empirically ~2000+ levels deep on the `pkg/binate/types`
test suite — so the host 8 MiB stack runs out before the
algorithm completes.

This is a structural limit of the current dispatcher.  The
fix is to make the callback path NOT consume host stack per
level.

## Solution shapes

Three viable approaches; pick one based on test impact + LOC
budget.  Listed cheapest to most invasive.

### A. Eliminate extern→VM callbacks for the hot paths

For every extern that calls back into `vm.CallFunc`, see if
the callback can be replaced with a precomputed result table
or a bytecode-level dispatch.

Examples that may be eliminable:
- Type-checker callbacks that walk AST nodes through registered
  visitors — could be hoisted to inline bytecode if the visitor
  pattern is uniform.
- IR-gen callbacks for destructor / copy emission — already
  go through codegen-emitted `__dtor_<T>` functions; the
  extern callback layer might be redundant for the test
  scenarios.

Pros: smallest LOC change; preserves the existing extern model
for non-recursive callbacks.

Cons: case-by-case work; may not cover every recursion site;
limits future extensibility if new extern callbacks land.

### B. Trampoline `execExtern` callbacks

Restructure the dispatcher so that:
1. When `execExtern` needs to call back into the VM, it
   doesn't call `vm.CallFunc` directly.  Instead, it returns
   a "pending VM call" record (callee funcIdx + args).
2. `execLoop` checks the return: if it's a pending call,
   push a frame for that call on `vm.Stack` (same as BC_CALL
   would) and continue.  When the inner execLoop returns,
   forward the result back to whoever the extern wants to
   return into.
3. To preserve extern semantics (the extern needs to RECEIVE
   the VM call's result and continue), extend the bytecode
   with a "resume extern" marker that triggers the saved
   continuation.

Pros: covers every callback path uniformly; preserves the
extern model.

Cons: significant API change to `execExtern` and every
extern's registration shape; requires per-extern state
machine refactoring.  Probably the most LOC.

### C. Reserve a dedicated VM-callback stack and switch to it

When `execExtern` invokes a callback that re-enters the VM:
1. Allocate a separate stack region (large heap block) once
   per VM instance.
2. Use a host-stack-switching primitive (e.g. `swapcontext`
   on POSIX, manual asm on bare metal) to run the nested
   `vm.CallFunc` on the dedicated stack.
3. On return, swap back to the original host stack.

Pros: minimal source change to extern dispatchers; covers
all paths.

Cons: introduces a host-OS dependency on stack-switching
(POSIX has `ucontext` deprecated; manual asm is portable
but per-target).  Adds a "C-impure" runtime piece — conflicts
with the C-free target direction in CLAUDE.md.

## Recommended path: A first, then B if A is incomplete

Start with **A** because the LOC cost is bounded and it
moves the actual problem space.  If A leaves some recursion
sites in place that still overflow on representative test
loads, escalate to **B**.

Skip **C** unless A+B prove infeasible — the C-free target
direction makes host-stack-switching a non-starter.

## Sites to investigate (for option A)

Run lldb / a profiler on the failing tests and identify which
externs appear repeatedly in the backtrace.  Likely
candidates (pre-investigation guesses):

- `pkg/binate/vm/extern_register_std.bn` — registry of standard
  externs.  Anything here that calls back into `vm.CallFunc`
  is the prime target.
- `pkg/builtins/rt` callbacks for managed-pointer cleanup
  (CallDtor) — these recurse over the type graph.
- `pkg/binate/types/Checker` callbacks for symbol resolution
  / generic instantiation — these recurse over the AST.

For each: trace whether the callback is genuinely necessary
or whether the work could happen in bytecode.  Code that's
already in bytecode (and just dispatched through an extern
for performance) can be moved inline.

## Sites to touch (for option B, if pursued)

- `pkg/binate/vm/vm_exec.bn` (`execLoop`):
  - Extend the "after BC_CALL" / "after extern" merge point
    to handle a new "extern wants to call VM" sub-state.
  - Add a pending-call record on `vm.Stack` per extern that
    can request VM calls.
- `pkg/binate/vm/vm_exec_helpers.bn` (`execExtern`):
  - Change the return type from `int` (result) to a tagged
    `(int, ext_state)` where state ∈ {done, pending_vm_call}.
  - Each extern's body becomes a state machine that can
    yield mid-execution.  Awkward in straight-line code;
    consider a code generator.
- `pkg/binate/vm/extern_register_std.bn` + every consumer:
  - Update extern registration shape to handle the new
    return type.

## Tests to add

For either option:

1. **Direct stack-overflow probe**: a small bytecode program
   that does `1000`-deep recursion under default `ulimit -s`
   should pass.  Pin this as `pkg/binate/vm/TestDeepRecursionDoesNotOverflow`.

2. **Extern callback recursion**: pin that a callback path
   from extern → VM → extern → VM → … sustains `500+` round-
   trips without overflowing.

3. **Regression for the original symptom**:
   - `pkg/binate/types/TestLoadPackageInterfaceTypeDecl` must
     pass under `builder-comp-int` without `ulimit` bump.
   - `pkg/binate/types/TestCheckGenericInterfaceConstraintSatisfied`
     must pass under same.

4. **Remove the ulimit band-aid**: once the proper fix lands
   and verifies, the `ulimit -s` lines in the runner scripts
   come back out.  Add a TODO comment pointing at the future
   removal commit.

## Phasing

If pursuing option A:
- Pass 1: profile failing tests under lldb; list top-N
  recursion-contributing externs.
- Pass 2: for each, replace the callback with an inline /
  bytecode equivalent.
- Pass 3: re-run unit tests on bni modes; expect the affected
  packages to flip green; remove the `ulimit -s` band-aid.

If pursuing option B:
- Pass 1: extend `execExtern` return type + add the pending-
  call state.  Don't yet update externs.
- Pass 2: refactor each extern's body to yield via the new
  return.
- Pass 3: re-run; remove band-aid.

## Estimated LOC

- Option A: highly variable; 200-1000 depending on extern count
  and how cleanly each can be hoisted.
- Option B: ~500-1500 in `pkg/binate/vm/` plus per-extern updates.
- Option C: ~200 in vm + asm primitives per target; do NOT pursue.

## Risk + rollback

- Option A: per-callback risk is bounded; can land incrementally.
- Option B: bigger blast radius; needs a coordinated landing
  + full bni unit-test sweep before flipping CI.
- Both: if the fix is wrong, the symptom returns (stack
  overflow); the `ulimit -s` band-aid stays as the safety net.

## Out of scope

- Compiling to native always (i.e., bypassing bni entirely
  for unit tests).  Defeats the purpose of having a bytecode
  VM.
- Changing test code to be less recursive.  The recursion
  is inherent to a type-checker / IR-gen — Go's go/types,
  rustc's chalk, etc. all recurse similarly.
