# Refcount Axioms and Principled Refcounting

## Motivation

The current refcounting implementation has accumulated ad-hoc special cases
("skip copy for OP_CALL", "consumeTemp", "isFreshManagedPtr",
"skip dtor for returned locals") that interact in subtle ways. Each
optimization tries to avoid a redundant RefInc/RefDec pair, but without
enforcing invariants, the combinations produce bugs (over-increment,
over-decrement, leaks) that are hard to diagnose and fix.

This document proposes a principled approach based on four axioms.

## Axioms

1. **Alive → rc > 0.** A managed allocation that has any live reference
   must have a positive refcount. After `make(T)`, rc = 1.

2. **rc = 0 → destructor.** When a managed allocation's refcount reaches
   zero, the destructor MUST run (RefDec managed fields, then free). This
   is not optional and cannot be skipped.

3. **Copy → copy constructor.** Whenever a managed value (or a struct
   containing managed fields) is copied, the copy constructor MUST run.
   This includes: variable declaration, variable assignment, function
   argument passing, function return, struct field assignment, slice
   element assignment. No exceptions.

4. **Move → zero source.** The only way to avoid the copy constructor is a
   move. A move transfers ownership from source to destination. After the
   move, the source MUST be zeroed (nil'd) so that its eventual destructor
   is a no-op. A zeroed/nil managed pointer or struct has no references to
   decrement.

## What This Means

### The Slow Path (Always Correct)

Every transfer of a managed value is a copy:

```
var x @T = make(T)     // make → rc=1. copy into x → rc=2. dtor temp → rc=1.
var y @T = x            // copy → rc=2.
y = nil                 // dtor y → rc=1.
// x goes out of scope  // dtor x → rc=0 → freed.
```

For structs with managed fields:

```
var w Wrapper = makeWrapper(n, 42)
// makeWrapper returns Wrapper. Copy into w → RefInc managed fields.
// Temp cleanup → dtor temp → RefDec managed fields.
// These balance. w owns its refs via copy.
// Scope exit → dtor w → RefDec managed fields.
```

For function calls:

```
func foo(n @Node) { ... }
foo(myNode)
// Arg passing: copy myNode into param n → RefInc → rc=2.
// Inside foo: n is alive, rc=2.
// foo returns: dtor n → RefDec → rc=1.
// myNode still alive, rc=1. ✓
```

For returns:

```
func bar() @Node {
    var n @Node = make(Node)  // rc=1. copy into n → rc=2. dtor temp → rc=1.
    return n                   // copy into return slot → rc=2. dtor n → rc=1.
}
var x @Node = bar()            // copy into x → rc=2. dtor temp → rc=1.
```

**Every operation is a copy + dtor.** The refcount goes up and down, but
always stays at exactly the right value. No special cases needed.

### Move Optimization

The slow path has redundant RefInc/RefDec pairs. A move eliminates them
by transferring ownership:

```
var x @T = make(T)     // make → rc=1. MOVE into x. Zero temp. rc=1.
```

Instead of: make (rc=1) → copy (rc=2) → dtor temp (rc=1), we do:
make (rc=1) → move into x (x takes ownership) → zero temp (dtor is no-op).
Same result (rc=1), but no RefInc/RefDec.

**Zeroing is essential.** Without it, the temp's eventual dtor would
decrement rc, causing a double-free. Zeroing makes the dtor safe (a
zeroed @T is nil, and RefDec(nil) is a no-op).

### Where Moves Apply

A move is valid when the source is **expiring** — it will not be used
again after the move. Cases:

1. **Temporaries**: `var x = make(T)` — the make result is a temp that
   expires at end of statement. Move into x, zero the temp.

2. **Last use of a local at return**: `return localVar` — after the
   return, the local's scope exits. Move into the return slot, zero the
   local.

3. **Temporary passed as argument**: `foo(make(T))` — the make result is
   passed to foo and then expires. Move into the param, zero the temp.

Moves are NOT valid for:
- Globals (they outlive the function)
- Variables that are used again after the copy
- Shared references (multiple variables pointing to the same object)

## Implications for the Current Codebase

### What Changes

The current code uses `isFreshManagedPtr`, `consumeTemp`, and
"skip copy for OP_CALL" as implicit moves WITHOUT zeroing the source.
This violates axiom 4. To fix:

**Option A: Remove all move optimizations.** Use the slow path everywhere.
Always copy, always dtor. This is correct by construction. Performance
overhead is one extra RefInc/RefDec per transfer, which is cheap (atomic
increment + decrement on the same cache line, or non-atomic in
single-threaded mode).

**Option B: Implement proper moves with zeroing.** Keep the optimizations
but add explicit zeroing of the source after each move. This requires:
- For `@T` and `@[]T` temps: after moving into a variable, emit a store
  of null/zero to the temp's location (or remove it from the temp list
  AND zero).
- For struct returns: after moving into the return slot, zero the local's
  alloca so its dtor is a no-op.
- For struct args: after moving a temp into the param slot, zero the temp.

**Recommendation: Option A first, then Option B as optimization.** Option A
is simple, correct, and easy to verify. Option B can be added later for
specific hot paths where the extra RefInc/RefDec matters.

### For @T and @[]T

Currently:
- `isFreshManagedPtr` returns true for OP_CALL → skip RefInc at var decl.
  This is an implicit move (temp's ref becomes variable's ref) without
  zeroing. The temp is removed from the temp list via `consumeTemp`, so
  it's not double-freed. But this means the temp list removal IS the
  "zeroing" — just done at a different level.

With axioms:
- **Slow path**: always RefInc at var decl. Register temp. Temp cleanup
  RefDec's the temp. Net: +1 -1 = 0 from the temp. Variable's RefInc
  provides its ref. Scope dtor provides its RefDec. Correct.
- **Move**: skip RefInc. Remove from temp list (implicit zero — temp is
  dead and won't be RefDec'd). Correct, but only because the temp list
  removal is a form of zeroing.

The current approach for @T/@[]T is actually safe because `consumeTemp`
prevents the temp from being double-freed. The problem is that this same
pattern was extended to structs without proper handling.

### For Structs with Managed Fields

Currently:
- Var decl: skip copy for OP_CALL. No temp registration. Scope dtor
  handles cleanup. This is an implicit move without zeroing. It works
  as long as the return's refcounts are exactly right — which they ARE
  for the move case (local return skips dtor, so the local's refs
  transfer to the caller).
- But: the local-return skip-dtor for structs means the struct's managed
  fields carry an extra RefInc from field assignment that was never
  balanced. This works when the caller skips copy (the extra +1 is the
  caller's ownership ref), but breaks when we try to add temp cleanup
  (the extra +1 becomes a leak or requires a matching -1).

With axioms (slow path):
- **Return**: always copy the return value (RefInc managed fields).
  Always dtor the local (RefDec managed fields). Net: +1 -1 = 0.
  The return value's managed fields have rc = original + 1 (from the
  copy), which is the caller's ownership ref.
- **Var decl**: always copy from the call result. Register as temp.
  Temp cleanup dtors (RefDec). Scope dtor dtors (RefDec). Copy provides
  +1 for scope dtor. Temp cleanup provides -1 for the return copy's +1.
  Balanced.
- **Inline use** (`g(f())`): arg copy +1 for callee param. Callee dtor
  -1 at scope exit. Temp cleanup -1 for the return copy's +1. Balanced.

**This should work.** The boot-comp-comp failure from earlier attempts was
likely caused by the inconsistent mix of old (move-style) and new
(slow-path) code, not by the slow path itself being wrong.

### Concrete Implementation Plan

Phase 1: Slow path for structs with managed fields.
1. Return: always emit struct copy (remove `!isLocalReturn` check).
2. Scope cleanup: always dtor struct locals (remove `isReturned` skip).
3. Var decl: always copy (remove `val.Op != OP_CALL` skip).
4. Register struct call results as temps.
5. Temp cleanup: dtor struct temps at end of statement.
6. Verify boot-comp, boot-comp-comp, boot-comp-int.

Phase 2: Slow path for @T and @[]T (clean up isFresh/consumeTemp).
1. Remove `isFreshManagedPtr`/`isFreshManagedSlice` checks.
2. Always RefInc at var decl/assign/arg.
3. Keep callee-entry RefInc (already there).
4. Keep scope-exit RefDec (already there).
5. Keep temp cleanup (already there).
6. Verify all modes.

Phase 3: Move optimization (optional, performance).
1. Identify expiring temps and last-use locals.
2. Replace copy+dtor with move+zero (memcpy + memset source to zero).
3. The zero+dtor-of-zero can be eliminated further down the pipeline
   (LLVM will optimize away a dtor called on a zeroed struct, since the
   null checks on each managed field all short-circuit). So the IR
   doesn't need to be clever — emit the move+zero literally, and let
   the optimizer clean it up.
4. Verify correctness with zeroing.

## Relationship to Existing Code

### What `isFreshManagedPtr` Really Means

In axiom terms: "this value is a temporary that we're about to move into
a variable. Skip the copy (no RefInc), skip the temp cleanup (no RefDec),
let the variable take direct ownership." This is a correct move — the temp
is dead after the variable takes it. The current implementation is safe
for @T because `consumeTemp` removes it from the temp list.

### What the Struct Return Skip-Dtor Really Means

"This local struct is expiring (being returned). Move its refs to the
return value without copy+dtor." This is a correct move IF the source
(local) is zeroed. Currently, the local is NOT zeroed — it just has its
dtor skipped. This is equivalent to zeroing because the local's alloca
is dead after the return (it's a stack allocation that goes away). So
this is actually safe for the struct itself, but the managed fields
inside it still need their refs properly handled.

### Why boot-comp-comp Breaks

The gen1 compiler is built with the new codegen. The new codegen changes
how struct returns and temps are handled. If the slow path (always copy,
always dtor) is correct, boot-comp-comp should not break. The fact that
it does suggests either:

1. The slow path implementation has a bug (most likely — careful review
   of the exact implementation is needed).
2. There's a latent bug in the compiler that the changed codegen exposes
   (e.g., a missing copy or dtor on a code path not covered by tests).
3. The gen1 compiler's self-compilation hits an edge case that the
   conformance tests don't cover.

The right approach is to implement the slow path carefully, verify each
step with the axioms, and debug any boot-comp-comp failure by examining
the gen1 compiler's actual behavior (with ASan or refcount instrumentation).
