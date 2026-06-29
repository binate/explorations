# Binate Coding Guide

> **Scope**: General Binate coding conventions. The full language —
> interfaces, generics, closures, `readonly` types, floats — is available; see
> `claude-notes.md` for the language design. Code in `cmd/bnc`'s dependency
> tree carries an additional constraint: it must be compilable by the current
> BUILDER `bnc` (see `bootstrap-subset.md`).

---

## Binate Is Not Go

Binate borrows syntax from Go, but the semantics differ in important ways. The biggest
practical difference is **slices**.

In Go, slices are growable, reference-counted-ish containers backed by arrays with
capacity management. In Binate, **slices are just views** — a pointer and a length,
nothing more. They are not resizable. They do not manage the lifetime of the data they
point into. They are not there to help you build up arrays of data.

- `*[]T` (raw slice): two words — `(data_ptr, length)`. Does **not** keep the
  underlying data alive.
- `@[]T` (managed-slice): four words — `(data_ptr, length, backing_refptr,
  backing_len)`. Keeps the backing allocation alive via refcounting. The first
  two words are layout-compatible with a raw slice. But still not resizable.

If you find yourself wanting to append to a slice, you need a library type (see
[String Building and Growable Collections](#string-building-and-growable-collections)
below).

---

## Naming Conventions

- **Exported symbols** (those declared in `.bni` interface files): `CamelCase` —
  `TypeName`, `IsKeyword`, `Lookup`, `MaxSize`.
- **Non-exported symbols**: `snake_case` or `camelCase` — `helper_func`,
  `internal_state`, `parseExpr`.
- **Constants**: follow the same rule — exported constants are `CamelCase`, internal
  ones are `snake_case`/`camelCase`.

This is a convention, not compiler-enforced. Visibility is determined solely by
presence in the `.bni` file.

---

## Managed vs Raw: Ownership Convention

Raw pointers (`*T`) and raw slices (`*[]T`) do **not** keep the data they reference
alive. Managed pointers (`@T`) and managed-slices (`@[]T`) do. This distinction drives
the core convention for function signatures and data structures:

### Function Arguments

- **Raw pointer/slice** = "I'm borrowing this; I won't retain it past my return."
- **Managed pointer/slice** = "I will retain this (e.g., store it in a data structure)."

Most functions only need to read or temporarily use their arguments, so raw
pointers/slices are the common case in parameter lists.

### Struct Fields

Structs that form data structures should generally hold **managed** pointers and
managed-slices, since the struct needs to keep its referenced data alive.

Raw pointers in structs are the exception, appropriate when:
- The lifetime of the referenced data is guaranteed by other means.
- A "weak" / "back" pointer is needed to avoid reference cycles (e.g., a child node
  pointing back to its parent).

---

## Zero Initialization

All variables and struct fields are zero-initialized by default. Partial struct
literals zero-init omitted fields: `Point{x: 1}` gives `y = 0`. There is no
uninitialized memory in safe Binate code.

---

## Slices Are Not Nillable

Coming from Go, this trips people up: **slices in Binate are not nillable**. This
applies to both raw slices (`*[]T`) and managed-slices (`@[]T`).

- `var s @[]T` zero-initializes to an empty managed-slice (length 0, no backing
  allocation), not to nil. Same for `*[]T`. Use it directly; do not reach for a
  separate "nil" state.
- `s == nil` is a type error. Test emptiness with `len(s) == 0`.
- `nil` is only assignable to pointer types: `*T`, `@T` (and any future pointer
  flavors). The type checker rejects `s = nil` for slices of either kind.

Pointer types **are** nillable, so `*T` and `@T` follow the familiar Go pattern:
default-init to nil, compare with `== nil` / `!= nil`, etc.

---

## Error Handling

There are no exceptions in Binate. Errors are values — return them as part of a
multiple-return tuple and check them:

```
result, err := doSomething(x)
if err != 0 {
    // handle error
}
```

---

## String Building and Growable Collections

**`append()` and `make_raw_deprecated()` have been removed from the language.**
`append` was a performance footgun (O(n) per call, O(n^2) for incremental building)
and did not fit the language's design. `make_raw_deprecated` was a transitional builtin
that has been replaced by `make_slice`. Using either is now a compile error.

Until generics are available, the current approach for growable collections is:
- **`CharBuf`** for building strings incrementally (backed by `@[]char` with geometric
  growth).
- Purpose-built buffer types for other element types as needed.

Once generics land, a general `Vec[T]` type will replace these ad-hoc solutions.

For fixed-size allocations where the size is known, use `make_slice(T, n)`.

---

## C Interop (`__c_call`): match the C type's exact width

A `__c_call("sym", RetType, args...)` reads its result straight out of the C
return register. Pick `RetType` (and integer arg types) to match the **C
function's actual ABI width**, because a mismatch silently reads undefined bits
on the target where the widths differ — there is **no checker enforcement** for
this (the checker can't know the C function's signature from the symbol name).

- **C `int` is FIXED 32-bit → use `int32`** (not binate `int`). Binate `int` is
  target-*word*-sized (64-bit on a 64-bit target), so reading a 32-bit C `int`
  return into a binate `int` leaves the upper 32 bits undefined: on x86-64,
  `mov eax, -1` zeroes the upper half of `RAX`, so a C `-1` reads back as
  `4294967295`. This is exactly the bug that made `os.Stat` miss `ENOENT` and
  reddened all Linux compiled CI (fixed by switching the stat-family + `closedir`
  to `int32`).
- **C `long` / `ssize_t` / `size_t` / `intptr_t` / `ptrdiff_t` are TARGET-width →
  use binate `int` / `uint`** (which is target-word-sized and correctly tracks
  them on both 32- and 64-bit targets). This is why `read`/`write`/`pread`/
  `pwrite` (returning `ssize_t`) correctly use `int` — do NOT "fix" these to a
  fixed width: `int32` is wrong on 64-bit and `int64` is wrong on 32-bit.
- **C `long long` / `int64_t` → `int64`; C `short` → `int16`; C `char` → `int8` /
  `uint8` (`char`/`byte`).** Pointers (`*T`) pass through as pointers.

Rule of thumb: if the C type is fixed-width, name a fixed-width binate type
(`int32`/`int64`/…); only use the target-width `int`/`uint` for genuinely
target-width C types. (A lint for this is a possible future addition, but it would
need the C signature, which `__c_call` does not carry.)

---

## File Organization

- **Source files** (`.bn`): keep to roughly **500 lines maximum** (600 as a hard
  ceiling), including comments and blank lines. If a file is getting long, split it.
- **Test files** (`_test.bn`): every `.bn` file that contains code (functions, methods
  — not just type definitions or constants) **must** have a corresponding `_test.bn`
  file that tests it. Test files may be longer than 500 lines, as long as they only
  test the corresponding source file.
- **Interface files** (`.bni`): one per package, declares the public API.

---

## Documentation and Comments

### Interface Files (`.bni`)

- Every `.bni` file should have a **package-level doc comment** at the top (before the
  `package` declaration), describing the package's purpose.
- Every exported function, type, and constant should have a **godoc-style comment**
  immediately above its declaration.

### Implementation Files (`.bn`)

- **Every** top-level `func`, `type`, and `const` (or `const ( ... )` group) needs
  a godoc-style comment immediately above its declaration. No "trivial" carve-out:
  in practice nearly every function has at least one pre-/post-condition, lifetime,
  ownership, or aliasing consideration that the signature alone doesn't convey, and
  the carve-out invites omitting comments precisely on the functions that need them
  most. In particular, call out:
  - Whether a returned managed-slice or managed-pointer aliases an argument (shares
    backing, so mutations and lifetime apply to both), is a copy, or is a fresh
    allocation.
  - Whether the caller is responsible for closing/freeing returned resources.
  - What happens on failure: returns `nil`, returns a sentinel, sets a flag,
    aborts, etc.
- Use **inline comments** to explain non-obvious logic, especially:
  - Subtle invariants or assumptions
  - References to outside requirements (e.g., "required by the language spec",
    "matches Go's behavior for X")
  - Why something is done a particular way (not just what)

---

## Testing

Testing is built into the toolchain, not just a convention — the `-test` flag drives
test discovery and execution.

### How It Works

- Test files are named `*_test.bn` and live alongside the code they test.
- Test files use the same `package` declaration as the code — they have access to all
  symbols, including non-exported ones.
- Test files are **excluded from normal builds**. They are only compiled/interpreted
  when the package is a `-test` target.

### Test Functions

Test functions follow a strict naming and signature convention that the test runner
uses for automatic discovery:

```
func TestParseIdent() testing.TestResult {
    // ... test logic ...
    if something_wrong {
        return "expected X, got Y"
    }
    return ""
}
```

- **Name**: must start with `Test` followed by an uppercase letter.
- **Signature**: `() testing.TestResult` — no parameters, returns `testing.TestResult`.
- **`testing.TestResult`** is a type alias for `*[]char`. Return `""` (empty string) for
  pass, a non-empty error message for fail.
- Test files must `import "pkg/builtins/testing"`.

Functions named `TestXxx` with the wrong signature produce a warning.

### Running Tests

```
binate -test [-root dir] pkg/foo [pkg/bar ...]
```

Output follows Go's format: `=== RUN`, `--- PASS`/`--- FAIL`, per-package summary.

---

## Commit Discipline

Each commit should change **one thing** and be as minimal as possible while still being
standalone (compiling, passing tests). Large refactors, feature additions, or migrations
should be broken into many small, incremental commits. This makes review easier, bisection
possible, and rollbacks safe.

### Testing Before Committing

Before committing, run **all applicable tests**:
- Unit tests for any packages you changed.
- Conformance tests in all applicable configurations (compiled, interpreted/VM,
  and self-hosted — see `conformance/run.sh`).

If there are **pre-existing test failures** (failures that exist before your changes),
you do not need to fix them before committing — and you should not, to keep the commit
focused. However, addressing those pre-existing failures should be done as an immediate
follow-up. The only exception is if a pre-existing failure obviously impedes validation
of the code you changed in the commit.
