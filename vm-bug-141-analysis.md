# Bug: Multi-Return Struct with Managed Fields (test 141)

## Symptom

`BC_REFDEC` crashes with `ptr = 23 (0x17)` — a struct field value being
treated as a managed pointer. Happens during scope cleanup of a struct
variable that was assigned via multi-return extraction.

## Minimal Reproducer

```binate
type Tab struct { Data @[]int; Len int }
func addTab(t Tab, v int) (Tab, int) {
    var off int = t.Len
    t.Len = t.Len + 1
    return t, off
}
func main() {
    var t Tab
    t.Data = make_slice(int, 10)
    t.Len = 0
    var off int
    t, off = addTab(t, 42)
    println(off)
}
```

Passes in boot-comp (compiled natively). Fails in boot-comp-int2 (bytecode VM).

Single-return struct with managed fields works. Multi-return struct
without managed fields works. Multi-return ints from a function taking
struct-with-managed-fields works. The issue is specifically **returning
a struct with managed fields as part of a multi-return tuple**.

## Investigation Findings

### Debug output from pushFrame and BC_RETURN

Instrumented `pushFrame` and `BC_RETURN` to trace frame offsets:

```
PUSH: at=552 savedSP=552 regsOff=592   ← addTab frame
PUSH: at=552 savedSP=552 regsOff=592   ← ??? same offset!
PUSH: at=864 savedSP=864 regsOff=904   ← __copy_Tab
PUSH: at=864 savedSP=864 regsOff=904   ← __dtor_Tab
MR-PACK: sp=928 hdr3=552               ← multi-return packs at SP=928
MR-POP: regsOff=592 hdrOff=552 hdr3=552 sp=944
```

### Key observation

`addTab`'s `savedSP = 552`. But `main()` calls `make_slice(int, 10)`
before `addTab`, which should bump `vm.SP` by 32 (managed-slice header).
So `vm.SP` should be ≥ 584 when `addTab` is called — but it's 552.

This suggests `vm.SP` lost the `BC_MAKE_SLICE` bump. Possible causes:

1. **An intermediate frame pop reset vm.SP** — if any function call
   between `make_slice` and `addTab` pushed and popped a frame, the pop
   would restore `vm.SP` to before the make_slice bump.

2. **The make_slice bump didn't happen** — but single-return works (the
   Data field is correctly populated), so make_slice did run.

3. **vm.SP was read at the wrong time** — a compiler optimization or
   argument evaluation order issue in the compiled `pushFrame` call.

### Why the crash happens

After BC_RETURN for multi-return, the struct data is copied above
`vm.SP`. The "don't pop" logic checks `retVal >= sbase + callerSP`.
With `callerSP = 552` and struct copy at `vm.SP = 928`, the retVal
(packed result at ~928) is above `sbase + 552`, so "don't pop" fires:
`callerSP = vm.SP`. But `vm.SP` at the pop check is 944 (not 1008
as expected after struct copy + packing). This means the struct copy
region was partially reclaimed.

The caller then extracts the struct pointer from the packed result
and MEMCPY's it to the result alloca. If the struct copy area was
overwritten, the MEMCPY copies garbage — including a bogus refptr
value (23), which crashes on RefDec during scope cleanup.

## Confirmation Plan

### Test 1: Does make_slice's vm.SP bump persist across function calls?

```binate
func noop() { }
func main() {
    var s @[]int = make_slice(int, 3)
    s[0] = 42
    noop()
    println(s[0])  // should print 42
}
```

If this prints 42, the make_slice data survives a function call.
If it prints garbage or crashes, the frame push/pop overwrites it.

### Test 2: Does the second PUSH at offset 552 indicate a frame overlap?

Add a print of the function name to pushFrame (use the VMFunc.Name
from the caller) to identify which function is being pushed at each
offset. This would reveal if two different functions push at the same
offset, indicating a frame management bug.

### Test 3: Trace vm.SP between make_slice and BC_CALL

Add a print of vm.SP after every BC_MAKE_SLICE, BC_LOAD_STR, and
BC_CALL instruction in execLoop. This would show the exact sequence
of SP changes and identify where the make_slice bump is lost.

## Confirmed Frame Sequence (with function names)

```
PUSH __copy_Tab at=552 savedSP=552    ← struct arg copy ctor before addTab call
PUSH addTab at=552 savedSP=552        ← addTab at SAME offset (copy ctor popped)
PUSH __copy_Tab at=864 savedSP=864   ← return value copy ctor (addTab's return seq)
PUSH __dtor_Tab at=864 savedSP=864   ← scope cleanup dtor (addTab's return seq)
MR-PACK at sp=928, callerSP=552      ← multi-return writes struct copy + packed result
PUSH __copy_Tab at=944 savedSP=944   ← Axiom 5 copy (in main, after call returns)
PUSH __dtor_Tab at=944 savedSP=944   ← Axiom 5 dtor (in main)
PUSH __dtor_anon at=944 savedSP=944  ← anonymous multi-return struct dtor
PUSH __dtor_Tab at=1048 savedSP=1048 ← scope cleanup for main's t
```

**No frame overlap** — each push is at or above the previous pop level.
The struct copy at 864 and packed result at ~928 are below the Axiom 5
calls at 944, so they're safe from overwriting.

## Updated Hypothesis

The frame layout is correct. The crash must be in the EXTRACTION or
STORE logic. The packed result at ~928 has `{struct_ptr, off_int}`.
`struct_ptr` points to the struct copy at ~864. The caller EXTRACTs
`struct_ptr`, then MEMCPY's 40 bytes from `struct_ptr` to the result
alloca. If the struct copy at 864 has valid data (it should — it was
MEMCPY'd from addTab's alloca at 592+), the MEMCPY should produce
correct results.

## Root Cause Confirmed

Instrumented BC_EXTRACT and BC_MEMCPY traced the data flow. The
critical instruction is the caller's MEMCPY of the multi-return result:

```
MEMCPY src=...0368 dst=...9912 sz=48 w0=...0304 w1=0 w2=38
```

**Size 48 bytes**: this is `40 (Tab) + 8 (int) = 48` — the size of the
anonymous multi-return struct `(Tab, int)`. The IR gen treats the
multi-return result as an anonymous struct VALUE and emits a MEMCPY
to copy the entire struct to the caller's anonymous-result alloca.

**The packed result on the VM stack is only 16 bytes**: `{struct_ptr,
int}` — 2 words. The MEMCPY reads 48 bytes, which is 32 bytes past
the end of the actual packed result, picking up garbage.

The subsequent `EXTRACT idx=2 val=38` reads word 2 (offset 16) of
the now-corrupted anonymous struct. Word 2 happens to be in the
"Tab" field's `Data.refptr` position (offsets 0-31 are the Tab,
specifically: 0-7=data_ptr, 8-15=len, 16-23=refptr, 24-31=backing_len).
The garbage value 38 (or 23 in the original crash) becomes the
"refptr" passed to RefDec, causing the segfault.

## Fix Direction

The fundamental mismatch: in the compiled path, multi-return packs
values into an LLVM aggregate where struct fields are stored INLINE
(40 bytes for Tab embedded in the anonymous struct). In the VM,
multi-return packs only POINTERS (1 word per value) because that's
how the VM represents struct values in registers.

Two possible fixes:

1. **Pack struct VALUES, not pointers**: in BC_RETURN for multi-return,
   when a value is a struct, copy its full bytes into the packed result
   instead of just the pointer. The packed result size matches what the
   caller expects (the IR gen's anonymous struct layout).

2. **Change EXTRACT for struct fields**: in the lowering, detect when
   EXTRACT pulls a struct field from an anonymous multi-return struct
   and use a different opcode that reads the pointer (one word) instead
   of MEMCPY'ing the full struct size.

Option 1 is cleaner but requires knowing struct sizes in BC_RETURN
(needs type info). Option 2 fits the existing VM design (registers
hold pointers for multi-word types) but requires distinguishing
struct EXTRACTs from scalar EXTRACTs in the lowering.
