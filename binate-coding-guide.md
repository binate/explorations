# Binate Coding Guide

> **Scope**: This guide currently covers only the **bootstrap subset** of Binate — the
> subset supported by the Go bootstrap interpreter. Features like interfaces, generics,
> closures, const-qualified types, and floats are part of the full language but not
> available yet. See `bootstrap-subset.md` for the complete list of what is and isn't
> supported, and `claude-notes.md` for the full language design.

---

## Binate Is Not Go

Binate borrows syntax from Go, but the semantics differ in important ways. The biggest
practical difference is **slices**.

In Go, slices are growable, reference-counted-ish containers backed by arrays with
capacity management. In Binate, **slices are just views** — a pointer and a length,
nothing more. They are not resizable. They do not manage the lifetime of the data they
point into. They are not there to help you build up arrays of data.

- `[]T` (raw slice): two words — `(data_ptr, length)`. Does **not** keep the
  underlying data alive.
- `@[]T` (managed-slice): three words — `(data_ptr, length, refptr)`. Keeps the
  backing allocation alive via refcounting. But still not resizable.

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

Raw pointers (`*T`) and raw slices (`[]T`) do **not** keep the data they reference
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

- Non-exported functions should have godoc-style comments unless the function is
  extremely short and self-explanatory.
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
- **`testing.TestResult`** is a type alias for `[]char`. Return `""` (empty string) for
  pass, a non-empty error message for fail.
- Test files must `import "pkg/builtin/testing"`.

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
- Conformance tests in all applicable configurations (bootstrap, selfhost, compiled,
  etc.).

If there are **pre-existing test failures** (failures that exist before your changes),
you do not need to fix them before committing — and you should not, to keep the commit
focused. However, addressing those pre-existing failures should be done as an immediate
follow-up. The only exception is if a pre-existing failure obviously impedes validation
of the code you changed in the commit.
