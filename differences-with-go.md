# Differences with Go

Binate draws heavily on Go's surface syntax and sensibility (type-after-name
declarations, implicit semicolons, Go-style multiple returns, `:=`, package
system, explicit interface methods, etc.) but diverges in several places where
Binate's design goals — systems programming on small targets, dual-mode
execution, no GC — push the other direction.

This document captures the major places where a reader fluent in Go would be
surprised. **It is currently incomplete** — see the TODO section at the end
for the categories still to be backfilled. Entries below are linked to the
authoritative rationale in `claude-notes.md` (short) and
`claude-discussion-detailed-notes.md` (long).

---

## Memory model and pointers

- **No garbage collector.** Binate is reference-counted. Cycles are the
  programmer's problem. See `claude-notes.md` § Memory model.
- **Two pointer kinds**: `*T` (raw, C-style) and `@T` (managed, refcounted).
  Go has only one pointer, with GC handling lifetime.
- **Two slice kinds**: `*[]T` (raw, 2 words: data + length, non-owning) and
  `@[]T` (managed-slice, 4 words: data, length, refptr, backing length —
  owns its backing via refcount). Go's slice is a 3-word (data, len, cap)
  type with GC.
- **`nil` on raw slices is undefined.** You can't compare `*[]T` to `nil`;
  use `len(s) == 0`. Managed-slices `@[]T` and managed pointers `@T` do
  compare to `nil`.
- **No `append`.** Growable sequences are a library concern — `pkg/buf`
  provides one, others live out-of-core.
- **`make`/`make_slice`/`box` return managed types.**
  `make(T)` → `@T`. `make_slice(T, n)` → `@[]T`. `box(expr)` → `@T`. There's
  no heap raw slice allocator by design; to get a non-owning view, slice a
  managed allocation.
- **No implicit conversions between integer types.** Use `cast(T, x)`.

## Strings

- **No `string` type.** String literals are untyped constants with natural
  type `[N]const char` and default type `*[]const char`. See
  `claude-notes.md` § Type conversions & literals.
- **No `+` operator for strings.** Neither compile-time nor runtime.
- **No hidden null terminators.** `"abc"` is exactly 3 bytes. Use `"abc\0"`
  when you need the terminator (e.g., for C FFI).
- **Adjacent string-literal concatenation (C-style).** Two literals with
  whitespace between them merge at lex/parse time:
  ```binate
  return errMsg(pos, "expected 'func', 'type', 'var', 'const', "
                     "'import', or identifier at top level")
  ```
  Go has no such rule — it uses `+` with compile-time folding. Binate has
  no `string` `+` to fold, and adjacent concat is more ergonomic for
  multi-line literals than `\`-newline continuation.

## Type system

- **No `init()` functions.** Package initialization is via ordinary code
  (the `main` package's `main` function, or setup functions called
  explicitly). Package-level `var` initializers run in dependency order.
- **No function-local `type` declarations.** Go allows
  `func F() { type Foo struct{...} ... }`; Binate makes this a parse error.
  Rarely used in Go, and each one raises scoping/mangling/(future)generic
  questions we'd rather not answer. Declare `Foo` at package scope with a
  doc comment. See `claude-discussion-detailed-notes.md` § 16.
- **No anonymous-struct methods.** Only named types can have methods or
  `impl` declarations, matching Go's rule.
- **Explicit `impl` declarations for interfaces.** Unlike Go's structural
  subtyping, Binate requires an explicit `impl Iface for T { ... }`
  declaration. Looking up "who implements `Iface`" is a grep. See
  `claude-notes.md` § Interfaces.
- **Monomorphized generics with interface constraints** (planned).
  Different approach from Go's generics — Binate aims for no runtime
  overhead on generic calls.
- **Untyped-literal coercion does not extend to named constants.**
  In Go, an untyped named constant coerces in all the places a literal
  does. In Binate, only literals themselves are untyped; a `const C = 3`
  has a concrete type.
- **`cast(T, expr)` syntax for type conversions**, not Go's `T(expr)` —
  avoids parser ambiguity with function calls.

## Control flow

- **No `goto`.** The (usual) arguments apply.

## Modules & packaging

- **Separate `.bni` interface files.** Each package has a `.bni` file
  declaring the public API (types, functions, constants) and zero or more
  `.bn` implementation files. Go has no such split; compilation starts
  from sources. See `claude-notes.md` § Visibility & package interfaces.

## Error handling

- **Errors as values**, like Go — but no `error` interface (no interfaces
  are built-in). Users define their own.
- **No `panic` / `recover` as a general control-flow mechanism.** `panic`
  exists for "should never happen" abort; there's no `recover`.
- **No `defer`.** Destructors (dtors run at scope exit) handle the common
  cleanup case; RAII-style.

## Concurrency

- **No goroutines, no channels, no `select`.** Binate is single-threaded
  by default. Threading-compatible primitives exist (atomic ops planned)
  but concurrency is not a language-level feature. See `claude-notes.md`
  § Threading.

## Minor syntax / idiom differences

- **No short-variable re-declarations in `:=`.** Go's "at least one new
  variable" rule for `a, b := f()` doesn't apply; you declare once and
  assign after.
- **`sizeof(T)` and `alignof(T)` are compile-time builtins.** Go uses
  `unsafe.Sizeof(x)` on an instance; Binate takes a type.
- **`bit_cast(T, x)` for bit-pattern reinterpretation.** Go has no
  equivalent — you'd use `unsafe.Pointer` conversions.
- **Visibility is by capitalization** (Go-style) — `Foo` exported, `foo`
  package-private.

---

## TODO — backfill

This document is nowhere near complete. Categories still to flesh out,
roughly by importance for someone reading Binate code with Go in their head:

- [ ] Slice/array semantics beyond ownership: literal handling, const
  variants (`@[]const char`), indexing traps, bounds checking.
- [ ] Struct semantics: value vs reference, field copy rules,
  destructor invocation points, raw structs vs managed structs.
- [ ] Interface values: two-word layout, boxing value types, vtable
  dispatch, receiver kinds (5 of them).
- [ ] Method resolution: `const *T`, `*T`, `*const T`, value receivers
  as `*const T`, auto-dereferencing via `.`.
- [ ] Const-ness (`const T`, not const variables): how Binate's
  `const T` differs from C's and why we didn't just skip it.
- [ ] REPL and forward-reference model — Go has no REPL; Binate's design
  is retained-mode.
- [ ] Bootstrap interpreter limitations: what subset of the language is
  self-host-safe. Not strictly a Go difference, but readers will hit
  this every day.
- [ ] Dual-mode execution: the fact that a function might be running
  compiled or interpreted has no cognate in Go.
- [ ] Temporary lifetimes: statement-scope temps with destructor calls.
- [ ] `volatile` as builtin functions, not a type qualifier.
- [ ] Visibility nuances: package interfaces determine what's public,
  not just capitalization.
- [ ] Import aliases and blank imports — currently unresolved design
  question.
- [ ] Annotations (`#[...]` planned) vs Go's struct tags.
- [ ] `make` vs `make_slice` vs `box` — three allocators instead of one.
- [ ] No `any` / empty interface — Binate's `any` interface exists but
  means something narrower (uniform interface value layout).

Add entries here as they come up in discussion or documentation work.
