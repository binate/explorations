# Plan: VM closure-record dtor (finish @func under the bytecode VM)

Status: **DONE 2026-06-03 (binate `77bae9ad`).** Implemented exactly as
designed below, except the sentinel is a `-1` const rather than a global's
address (taking `&module-global` lowers to an undefined `%v-1` — a separate
codegen limitation; `-1` is safe since no real dtor handle is ever `-1` or
`0`). Conformance `548_func_value_capture_released` pins it (a captured
`@Counter`'s refcount returns to baseline after the closure dies: `2`
pre-fix = leaked, `1` now); green in all six default modes, no regressions.
The IR-level @func copy-RefInc fix landed earlier (binate `ec01460c` +
`ee57e0e2`). With this, @func is leak-clean on every backend + the VM.

This plan removed the one remaining defect: the bytecode VM **leaked** a
capturing `@func`'s capture record and every managed value captured in it.

## The leak (root-caused)

For a managed capturing closure (`@func`), `gen_func_lit.bn`:
1. `EmitMake`s a heap closure struct (refcount 1),
2. RefIncs each captured managed value into a field (`emitCaptureRefInc`),
3. registers the closure-struct dtor, and
4. builds the func value with **data = the closure struct** pointer.

**Compiled** (`emitFuncValueVtable`) puts the closure-struct dtor in the
func value's vtable slot 0, so `RefDec(@func value)` = `RefDec(closure
struct)` → at 0 the dtor RefDec's the captures and frees the struct.
Leak-free.

**The bytecode VM** adds an indirection. `BC_FUNC_VALUE` (capturing path,
`vm_exec_funcref.bn:123-141`) heap-allocates a 32-byte **rec**
`{DATA_KIND_COMPILED_CLOSURE, vm, idx+1, closureStructAddr}` and sets the
func value's **data = the rec** (rec[3] points at the closure struct).
`ensureHandle` sets `vt.Dtor = 0`. So when the @func value's data is
RefDec'd to zero, `BC_REFDEC_INLINE_FAST` sees `dtorHandle == 0` and just
`rt.Free(rec)`. The closure struct (`rec[3]`, refcount 1) is **never
touched** → the closure struct *and every captured managed value leak*.

Output is correct in every mode (captures are read through live pointers
during the run; the leak is end-of-life only), which is why no conformance
test catches it. But it violates the never-leak rule.

Refcount note: func-value COPIES RefInc/RefDec the **rec** (my acquire
arms), never the closure struct — so the closure struct stays at refcount
1 and must be released exactly when the rec hits 0 (the last func-value
copy dies).

## Why we can't just read `rec[0]`

`BC_REFDEC_INLINE_FAST` handles **all** managed-ptr RefDecs, so `ptr` is
any managed allocation. `DATA_KIND_COMPILED_CLOSURE = 2` (tiny), so
testing `ptr[0] == 2` would false-positive on any struct whose first int
field is 2 — unacceptable for a compiler. (`BC_CALL_FUNC_VALUE` *can* test
`data[0]` because there the data is known-a-rec.) The reliable signal must
come from the **dtor handle** (vtable slot 0), which the IR's @func RefDec
passes as `Src2` and which we control per-callee.

## The fix (6 sites)

### 1. Marker sentinel (vm package, module global)
Add `var compiledClosureDtorMark uint8` at module scope. Its **address**
(`bit_cast(int, &compiledClosureDtorMark)`) is the sentinel: a real,
unique pointer that is never a struct-dtor handle and never 0. (Don't use
a small magic int — on 32-bit a real handle could in principle collide; a
dedicated global's address cannot.)

### 2. `ensureHandle` (`vm_exec_funcref.bn`) — set the sentinel for capturing callees
`IsClosure` ⟺ capturing (set under `if len(captures) > 0` in
`gen_func_lit.bn`). In `ensureHandle`, when `callee.IsClosure`, set
`vt.Dtor = bit_cast(int, &compiledClosureDtorMark)` instead of 0. All
func values of an IsClosure callee are capturing (have a COMPILED_CLOSURE
rec), so this is exact. Non-closure callees keep `vt.Dtor = 0`.

### 3-4. Plumb the closure-struct dtor NAME (ir.Func → VMFunc)
The VM needs the closure-struct dtor function to RefDec the captures.
- `ir.bni` Func: add `ClosureStructDtorName @[]char`.
- `gen_func_lit.bn` (the `isManagedFV` + `closureStruct.NeedsDestruction()`
  branch): `f.ClosureStructDtorName = qualifiedDtorNameForType(closureStruct)`.
  (qualifiedDtorNameForType is already in pkg/binate/ir; this is the same
  symbol `emitFuncValueVtable` puts in the compiled vtable slot.)
- `vm.bni` VMFunc: add `ClosureStructDtorName @[]char`.
- `lower_func.bn` (the `f.IsClosure` block): copy
  `vmf.ClosureStructDtorName = f.ClosureStructDtorName`.
Empty name = the closure struct has no managed fields (no dtor needed,
just free it).

### 5. `BC_REFDEC_INLINE_FAST` (`vm_exec.bn`) — the sentinel arm
After the rec hits refcount 0, BEFORE the existing `dtorHandle == 0`
branch, add:
```
if dtorHandle == bit_cast(int, &compiledClosureDtorMark) {
    // ptr is guaranteed a COMPILED_CLOSURE rec (only IsClosure callees
    // use this sentinel). Release the heap closure struct it owns, then
    // free the rec.
    var rec *int = bit_cast(*int, ptr)
    var csAddr int = rec[3]
    var ccFnIdx int = rec[2]            // closure-body func idx + 1
    rt.Free(ptr)                        // free the 32-byte rec
    if csAddr != 0 {
        var csHd *int = bit_cast(*int, csAddr - MANAGED_HDR)
        if csHd[0] >= 0 {               // not immortal
            csHd[0] = csHd[0] - 1
            if csHd[0] == 0 {
                var ccFunc @VMFunc = vm.Funcs[ccFnIdx - 1]
                if len(ccFunc.ClosureStructDtorName) > 0 {
                    var dIdx int = vm.LookupFunc(ccFunc.ClosureStructDtorName)
                    if dIdx >= 0 {
                        // push the dtor frame iteratively (mirror the
                        // DATA_KIND_VM_CLOSURE_REC arm ~line 401-424):
                        // newRegs[0] = csAddr, freeOnPop frees csAddr after
                        // the dtor returns. Mutate funcIdx/f/code/regsOff/
                        // regs/frameBase/pc and `continue`.
                        ...
                        continue
                    }
                }
                rt.Free(bit_cast(*uint8, csAddr))  // no dtor: just free
            }
        }
    }
    continue
}
```
The iterative push (not a recursive host call) matches the existing
VM_CLOSURE_REC handling so deep dtor chains don't blow the host C stack at
`-int-int` depth. Set the new frame's `freeOnPop` (FRAME_HDR slot 5) to
`csAddr` so the closure struct is freed after its dtor runs, exactly like
the VM_CLOSURE_REC arm frees `ptr`.

## Ordering / coupling
Sites 1+2+5 MUST land together: once `ensureHandle` sets `vt.Dtor` =
sentinel, a capturing-closure RefDec no longer hits `dtorHandle == 0`; the
old code would then dereference the sentinel as a handle and crash. Sites
3-4 (name plumbing) are inert until site 5 reads the name, so they can
land first (still green).

## Tests
- **Leak accumulation** (new conformance test): a loop that creates +
  drops many capturing closures over a heap `@Counter`, run under
  `builder-comp-int-int`. That lane overflows on accumulated leaks (cf.
  the byval-spill-leak history), so it passes iff the rec + struct +
  captures are actually freed. Keep it small/bounded for the other modes.
- 531/534/546/547 must stay green in all six modes (no regression).
- Unit test (pkg/vm): a capturing-closure VMFunc carries a non-empty
  `ClosureStructDtorName`; an `ensureHandle`d IsClosure callee has
  `vt.Dtor != 0`.

## Out of scope
- The non-capturing shared `VM_CLOSURE_REC` is already balanced
  (construction RefInc + VMFunc-owned base ref). Unchanged.
- Compiled / native backends already leak-clean (vtable dtor slot). Unchanged.
