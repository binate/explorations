# Compiler Bug: `var` declarations in loop body leak native stack per iteration

## Summary

The Binate compiler (`bnc`) generates code where `var` declarations
inside a `for` loop body allocate a fresh native stack slot every
iteration, without reusing the slot or freeing it at the end of the
iteration. After enough iterations (~1M for an 8-byte int), the
native stack overflows.

## Symptom

Programs with high-iteration loops that declare any `var` inside
the loop body crash with `EXC_BAD_ACCESS` (stack guard page hit)
when run as a compiled binary. The crash happens at a stack address
just below the thread's stack guard page.

This was first noticed when the bytecode VM (`pkg/vm`) — which has
a dispatch `for` loop with many `var` declarations per iteration —
crashed when interpreting programs that perform many bytecode
operations (e.g., the managed stress conformance tests 252-255).
The VM itself doesn't recurse on the native stack; the entire
crash is in a single `execLoop` invocation that overflows.

## Minimal Reproducer

`conformance/268_compiler_loop_var_leak.bn`:

```binate
package "main"

func main() {
	var sum int = 0
	for i := 0; i < 1000000; i++ {
		var local int = i * 2
		sum = sum + local
	}
	println(sum)
}
```

Expected output: `999999000000`
Actual: crashes (segfault)

The same loop **without** `var local int` (inlining `i * 2` into
the addition) runs to 2,000,000+ iterations without issue.

## Scaling Confirmation

```
n=100,000   → succeeds (output: 9999900000)
n=500,000   → succeeds (output: 249999500000)
n=1,000,000 → crashes
```

Linear growth is consistent with a per-iteration leak. With an
8MB native stack and ~8 bytes leaked per iteration, the crash
threshold is ~1M.

## Diagnosis

### Static frame size is small

`otool -tv` on `bn_vm__execLoop` shows the prologue as:
```
sub sp, sp, #0x290
```
(656 bytes static frame)

But the runtime frame size — from `fp_caller - sp` — is ~8MB.
There is no `alloca` call, so the stack growth must come from
repeated SP adjustments inside the loop that aren't paired with
restoration.

### Per-iteration leak

A single `var local int` in a loop body causes the leak to be
exactly 8 bytes per iteration. Adding more `var` declarations
(e.g., a struct copy `var local Pair = p`) causes the leak to
match the cumulative size of those declarations.

### Independent of types

The bug occurs with simple `var local int`, not just managed
types or struct copies. This suggests the issue is in the
general lowering of `var` declarations inside `for` blocks.

## Hypothesis

The IR gen treats each `var` declaration in a block scope as
needing its own SSA value / alloca. For `var` declarations in
the body of a `for` loop, a fresh `OP_ALLOC` is emitted per
iteration (not hoisted to the function entry block).

Each `OP_ALLOC` becomes a fresh stack slot in the LLVM-emitted
function — and the LLVM codegen probably emits `alloca` for each,
which is allocated on entry to the basic block but never freed
at the end of the iteration.

A correct lowering would either:
1. Hoist all allocas to the function entry block (LLVM convention),
   so they're allocated once and reused across iterations.
2. Pair each in-loop alloca with a stack restoration at the end
   of the iteration (using LLVM's `llvm.stacksave` / `llvm.stackrestore`
   intrinsics).

LLVM's standard pattern is (1): all allocas in the entry block.
This is also how Clang handles C `int x = ...;` in loop bodies.

## Where to Investigate

- `pkg/ir/gen_stmt.bn`: how `var` declarations are lowered. Look
  for `EmitAlloc` calls in non-entry blocks, particularly inside
  loop bodies.
- `pkg/codegen/emit.bn`: how `OP_ALLOC` is translated to LLVM IR.
  Check whether allocas are emitted at their original location
  (in any block) or hoisted to the entry block.

## Impact

- **Bytecode VM stress tests crash** (252-255). The VM's dispatch
  loop has many `var` declarations per iteration; high-iteration
  programs blow the stack.
- **Any high-iteration compiled program with loop-body vars**
  is at risk. Most programs don't hit the threshold (~1M), but
  the bug is real.
- **Workaround** for affected code: hoist all `var` declarations
  in loop bodies to before the loop, e.g.:
  ```binate
  var local int  // hoisted
  for i := 0; i < N; i++ {
      local = i * 2
      // ...
  }
  ```

## Notes

- Discovered while investigating why VM stress tests 252-255
  crash. The VM itself uses an iterative dispatch (no native
  recursion), but `execLoop`'s body has many `var` declarations
  per iteration, leading to the stack overflow.
- The fix is in the compiler, not the VM. Once fixed, the VM
  stress tests should pass without further VM changes (subject
  to other bugs).
