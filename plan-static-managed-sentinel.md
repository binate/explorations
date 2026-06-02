# Plan: Static-managed sentinel refcount

**Status: IN PROGRESS** (worktree `temp-binate-6` / branch `work-6`, started
2026-06-01).

Prerequisite for the package-descriptor work
([`notes-package-introspection.md`](notes-package-introspection.md)):
the generated `@reflect.Package` / `@TypeInfo` / `@FunctionInfo` descriptor
nodes are immortal static **managed objects**, and the language has no working
mechanism for that yet.

## Motivation

The package descriptor (Phase B onward) wants its nodes to be ordinary `@`
managed values so consumers (the VM, reflection, printing) handle them with
normal lifetime rules — even though the nodes themselves are static, immortal,
never-freed data emitted alongside vtables.

That requires a **managed pointer to static struct data** that RefInc/RefDec
treat as immortal. The design has always specified this
(`claude-notes.md:909`, `claude-discussion-detailed-notes.md:1427`: "Static
managed data uses a sentinel refcount … never decremented, never freed"), but
it is **not implemented** in any refcount path, and is still listed as an open
detail (`claude-discussion-detailed-notes.md:2160`).

## Current state (investigated 2026-06-01)

Immortality today rides entirely on the **nil-pointer** fast-path skip — every
refcount path nil-checks and bails. There is **no sentinel check anywhere**.

**Refcount is not a single chokepoint** — there are ~5 implementations:

| Path | Location | Shape |
|---|---|---|
| Library (out-of-line) | `impls/core/{libc,baremetal}/pkg/builtins/rt/rt.bn` `RefInc`/`RefDec` | load/±1/store; `RefDec` aborts on `h[0] <= 0` |
| LLVM inline | `pkg/binate/codegen/emit_refcount.bn` `emitRefIncInline`/`emitRefDecInline` | nil-check → load/±1/store; RefDec→0 calls `ZeroRefDestroy` |
| native aarch64 inline | `pkg/binate/native/aarch64/aarch64_ops.bn:211/258` | same, in asm |
| native x64 | `pkg/binate/native/x64/x64_managed.bn:50/67` | emits a CALL to the library |
| VM | `pkg/binate/vm/vm_exec_helpers.bn` | interpreted refcount |

Header layout (unchanged): refcount at `-2` words (`h[0]`), free-fn at `-1`
word (`h[1]`); `managedHeaderBytes()` = 16 (LP64) / 8 (arm32).

**The only static managed data today is string-literal managed-slices.** A
`@[]const char` literal lowers to a static `%BnManagedSlice { data→rodata, len,
backing_refptr = null, cap }` (`emit.bn:382`). Because every refcount path
nil-checks the *backing* pointer, RefInc/RefDec no-op → the rodata is never
written or freed. This works only because a managed-*slice* separates its data
pointer from its refcount-backing pointer.

**There is no managed-pointer-to-static-struct today.** Top-level globals take
integer initializers only (`ModuleGlobal.Init`); `&x` yields a raw `*T`, never
`@T`; `@T` structs come only from heap (`make`/`box`). So the descriptor nodes
are the first such case — nothing to migrate, but nothing to lean on either.

## Design

### Sentinel encoding

The refcount field is target-`int`-width (`i64` LP64 / `i32` arm32). Two viable
encodings; **decision: negative-as-immortal** (`h[0] < 0`):

- A single magic value (`h[0] == -1`, the "UINT_MAX" pattern from the notes)
  needs an `icmp eq` against a width-correct literal.
- **Negative-as-immortal** (`h[0] < 0`) is a cheap sign test, gives a whole
  reserved range, and composes with the existing invariant that a live refcount
  is always `>= 1` (real counts never go negative — `RefDec` already treats
  `<= 0` as a bug). Static nodes are emitted with `h[0]` = a fixed negative
  sentinel (proposed `STATIC_REFCOUNT = INT_MIN`, declared in `pkg/builtins/rt`).

Under negative-as-immortal:
- `RefInc`: `if h[0] < 0 { return }` before the increment.
- `RefDec`: `if h[0] < 0 { return }` before the decrement (so it neither
  decrements nor frees, and never reaches the destroy/`ZeroRefDestroy` path).
- `rt.RefDec`'s existing `if h[0] <= 0 { abort }` becomes `if h[0] == 0
  { abort }` (negative is now legal = immortal, not a corruption signal).

The free-fn slot (`h[1]`) is irrelevant for static nodes — the destroy path is
never reached — but is emitted as `0` for tidiness.

### Where the check lands

All five paths get the immortal short-circuit. Inline paths add one
`icmp slt … 0` + branch after the load they already do (predicts not-taken in
the overwhelmingly-common heap case). This is a touch to the refcount hot path
— flagged explicitly because `plan-refcount-inlining.md` and the
refcount-transparency guidance care about exactly this region.

### Emitting static managed nodes

A new codegen path emits a static struct global prefixed by its managed header:

```
@<node>.hdr = <linkage> constant { <int>, i8* } { <int> STATIC_REFCOUNT, i8* null }
@<node>     = <linkage> constant %<StructTy> { ...fields... }   ; immediately follows hdr
```

The `@T` value handed out is `&@<node>` (the payload, header at `-16`). Layout
must guarantee header-immediately-precedes-payload (one combined global, or
explicit section/alignment control). This is the open mechanical risk — see
below.

## Investigation: can we retire the null-backing trick?

The user's question: now that we have a real sentinel, do string-literal
managed-slices still need the special `backing_refptr = null` representation, or
can static-managed be unified under one mechanism (and maybe drop a nil-check)?

- **Unify representation, yes (candidate):** a string literal could instead get
  a static backing header `{STATIC_REFCOUNT, 0}` and a non-null `backing_refptr`,
  so all static-managed data — slices and objects — shares the sentinel path
  rather than two mechanisms (null-backing for slices, sentinel for objects).
- **Drop the nil-check, no:** the refcount nil-check also guards genuinely-nil
  managed values (uninitialized `@T`, nil `@[]T`), so it cannot be removed
  outright. At best the *string-specific* null-backing special case folds into
  the general sentinel path; the nil-check stays.
- **Verdict: defer.** Implement the sentinel first (it is the hard
  prerequisite). Treat unification as a follow-up simplification, measured
  against whether it actually removes branches/code rather than just moving the
  representation. Do **not** change string-literal lowering as part of the
  initial sentinel landing.

## Implementation steps (small, independently-green)

1. **`rt` constant + library check.** Add `STATIC_REFCOUNT` (INT_MIN) to
   `pkg/builtins/rt`; add the `h[0] < 0` short-circuit to `rt.RefInc`/`rt.RefDec`
   (both libc + baremetal copies); flip `RefDec`'s abort to `== 0`. Unit-test
   in `pkg/rt` (RefInc/RefDec no-op on a sentinel header).
2. **VM path.** Mirror the check in `vm_exec_helpers.bn`. Unit-test in `pkg/vm`.
3. **LLVM inline.** Add the immortal branch to
   `emit_refcount.bn:emitRefIncInline`/`emitRefDecInline`. Codegen unit-test
   asserts the emitted IR shape; a conformance test drives an immortal node
   through inc/dec/scope-exit and checks it is never freed.
4. **Native inline.** Mirror in `aarch64_ops.bn`; confirm x64 (library CALL) is
   already covered by step 1.
5. **Static-managed-node emitter.** Codegen helper that emits a header+payload
   static global and yields the `@T`. Unit-test the IR shape (header at `-16`,
   sentinel value, layout adjacency). This is the piece the descriptor work
   consumes.

Steps 1–4 are the sentinel itself (shippable on their own, exercised by a
hand-written conformance test). Step 5 is the bridge to the descriptor.

## Tests

- Conformance: an immortal `@T` repeatedly inc/dec'd and dropped at scope exit;
  assert no free (e.g. via an allocation counter or a poisoned free-fn that
  aborts if called). Pin across all modes including arm32.
- Unit: each refcount path's no-op-on-sentinel behavior; the static-node
  emitter's IR shape.

## Open questions

1. **Header/payload adjacency in LLVM.** Cleanest way to guarantee the header
   sits exactly `-16`/`-8` before the payload — one packed `{header, payload}`
   global with the handed-out pointer GEP'd to the payload, vs. two globals with
   forced ordering. The packed-struct form is the likely answer; confirm GEP
   math and that the linker won't reorder.
2. **Sentinel value.** `INT_MIN` vs `-1` vs a named magic. `INT_MIN` keeps the
   whole negative range reserved and reads unambiguously; confirm nothing else
   relies on negative refcounts.
3. **Overflow interaction.** With negative-as-immortal, a real count
   approaching `INT_MAX` would alias into the immortal range. Real counts never
   get near that, but note it (`detailed-notes:2160` flagged
   "interaction with overflow checking").
