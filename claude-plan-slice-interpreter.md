# Plan: Fix Slice Usage in the Bootstrap Interpreter

## Problem

The bootstrap interpreter (Go) treats slices as Go slices — nullable, GC-managed,
freely aliased. This doesn't match the Binate spec where raw slices (`[]T`) are
unmanaged value types. Since the bootstrap interpreter is interpreting Binate code,
its runtime representation of slices should model the Binate semantics, not Go's.

## Current State

The interpreter has ~54 slice usages across `interpreter.go`, `value.go`, and `main.go`:

- **SliceVal** uses `Elems []Value` — a Go slice that can be nil, shared, grown
- `evalNilCompare` (line 1309) checks `v.Elems == nil` — slices shouldn't be nil-comparable
- `evalAppend` (line 178) handles `NilVal` as first arg — append shouldn't exist
- `coerce()` converts `NilVal` to `ZeroValue(SliceType)` — nil shouldn't coerce to slice
- Multiple places pass nil where a slice is expected

## What Changes

The interpreter needs to model Binate's actual slice semantics. But we also need to
be pragmatic: the bootstrap interpreter exists to get the self-hosted compiler running.
It doesn't need to be a perfect reference implementation — it needs to correctly execute
the compiler's `.bn` source files.

### Phase 1: Remove append from the language

Once append is removed from the Binate language (see `claude-plan-remove-append.md`),
the interpreter's `evalAppend` can be deleted. The replacement (a buffer library type
written in Binate, interpreted by the bootstrap) needs the interpreter to correctly
handle struct method calls and the underlying operations (which it already does).

### Phase 2: Remove nil-slice semantics

1. **`evalNilCompare` for slices** — remove the `case *SliceVal` branch that checks
   `v.Elems == nil`. Type checker should reject `slice == nil` comparisons.

2. **`coerce()` NilVal-to-SliceVal** — remove this path. If Binate code assigns nil
   to a slice variable, the type checker should reject it (nil is for pointer types only).

3. **SliceVal zero value** — `ZeroValue(SliceType)` should return a SliceVal with
   `Elems: []Value{}` (empty, non-nil) rather than `Elems: nil`. This is the zero
   value of a slice: length 0, valid but empty. In Binate syntax: `[]T{}`.

4. **`evalAppend` nil handling** — once append is removed from the language, delete
   the nil-as-first-arg path entirely. Until then, the `NilVal` case should create
   an empty SliceVal and proceed (not special-case nil).

### Phase 3: SliceVal representation

The Go `SliceVal.Elems []Value` models Binate slices using Go's own slice, which
brings Go's aliasing semantics. This mostly works because the interpreter copies
on sub-slicing and on append. But it's worth auditing that no code path relies on
shared backing arrays between SliceVal instances.

No immediate change needed here — the Go representation is adequate for bootstrapping.
The key fix is removing nil-comparability and append, not changing the representation.

## Files to Modify

| File | Changes |
|------|---------|
| `interpreter/interpreter.go` | Remove `evalAppend` (after append removal), remove SliceVal nil comparison in `evalNilCompare`, remove NilVal-to-SliceVal coercion |
| `interpreter/value.go` | Change `ZeroValue(SliceType)` to return non-nil empty Elems |
| `interpreter/interpreter_test.go` | Update tests that rely on nil-slice semantics |

## Order of Operations

This plan depends on:
1. **Remove append** (see `claude-plan-remove-append.md`) — must happen first, since
   the compiler source uses append extensively and we need a replacement before we can
   remove the builtin
2. **Type checker changes** — reject `slice == nil`, `slice = nil` at compile time
3. Then the interpreter changes above follow naturally

## Risk

Low. The bootstrap interpreter is tested via the conformance suite (bootstrap mode)
and by self-hosting the compiler. Changes can be validated incrementally.
