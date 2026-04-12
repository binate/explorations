# Proposal: Restrict Implicit `@T` → `*T` Conversion to Borrowing Positions

**Status**: Proposal (needs investigation)

## Current Behavior

`@T` converts implicitly to `*T` in all contexts, and `@[]T` converts implicitly to
`*[]T` (f.k.a. `[]T`) in all contexts. This includes:

- Function arguments: `f(x)` where `x: @T`, `f` takes `*T` — **safe**
- Method receivers: `x.g()` where `x: @T`, `g` takes `*T` receiver — **safe**
- Variable assignment: `var p *T = x` where `x: @T` — **dangerous**
- Struct field store: `s.ptr = x` where `x: @T` — **dangerous**
- Return: `return x` where return type is `*T` — **dangerous**

The "dangerous" cases are dangerous because the raw pointer can outlive the managed
value. If the managed value is RefDec'd (e.g., goes out of scope), the raw pointer
dangles.

## Proposal

Restrict implicit `@T` → `*T` (and `@[]T` → `*[]T`) conversion to **borrowing
positions** — contexts where the managed value is guaranteed to outlive the raw
pointer:

1. **Function/method arguments**: the caller holds the managed value for the duration
   of the call, so the raw pointer is valid for the callee's entire execution.
2. **Method receivers**: same reasoning — the managed value is live for the call.
3. **Subexpressions within a statement**: e.g., `*x + 1` where `x: @T`.

In all other positions ("storing" positions), an explicit conversion would be required.

## What Changes

| Context | Current | Proposed |
|---|---|---|
| `f(x)` — arg | implicit | implicit (unchanged) |
| `x.g()` — receiver | implicit | implicit (unchanged) |
| `var p *T = x` | implicit | **explicit required** |
| `s.ptr = x` | implicit | **explicit required** |
| `return x` (ret type `*T`) | implicit | **explicit required** |
| `p = x` (reassignment) | implicit | **explicit required** |

## Explicit Conversion Syntax

For "storing" positions, the programmer would need to write an explicit conversion.
Options to consider:

- `cast(*T, x)` — uses existing cast syntax, but `cast` is normally for value
  conversions (e.g., int → uint), not pointer coercions
- A new builtin like `raw(x)` or `borrow(x)` — clearer intent but adds a keyword
- `bit_cast(*T, x)` — technically correct (same bits) but `bit_cast` connotes
  "reinterpret bits" which is stronger than needed

The best option needs discussion. `cast(*T, x)` is probably the most natural fit
since it's a type-narrowing conversion.

## Edge Cases to Investigate

### Temporaries

`f(g(x))` where `g` returns `@T` and `f` takes `*T` — the temporary from `g(x)` is
live for the full statement, so the implicit conversion should be safe. But does the
rule need to explicitly handle temporaries, or does "argument position" cover it?

Likely covered: `g(x)` produces a temporary `@T`, which is then passed as an argument
to `f`. The argument-position rule applies to the temporary.

### Short variable declarations

`p := x` where `x: @T` — should `p` infer as `@T` or `*T`? It should infer as `@T`
(the actual type of the RHS). The implicit conversion only triggers when a `*T` is
explicitly expected by the context.

### Chained field access

`x.field` where `x: @T` and `field: SomeType` — this involves an implicit deref
(`(*x).field`). This is a subexpression, so it should remain safe/implicit.

### Slice conversion

`@[]T` → `*[]T` follows the same rule. `f(ms)` where `ms: @[]T` and `f` takes
`*[]T` is fine. `var s *[]T = ms` requires explicit conversion.

### Multiple levels

`@(@T)` (managed pointer to managed T) → what converts implicitly? Only the outermost
`@` → `*` in borrowing position? This needs careful thought.

### Existing code impact

The self-hosted codebase likely has many `var s []T = someManaged` patterns that would
need explicit conversions. The migration impact should be assessed before committing
to this change.

## Rationale

The current rule is convenient but masks a common class of bugs: storing a raw
pointer/slice derived from a managed value, then using it after the managed value has
been freed. The linter's `raw-slice-return` and `managed-to-raw-assign` rules exist
precisely because this pattern is dangerous.

Restricting the implicit conversion to borrowing positions would make many of these
bugs compile-time errors rather than runtime use-after-free. The explicit conversion
at storing positions serves as a "I know what I'm doing" marker — similar to how
`bit_cast` signals dangerous reinterpretation.

## Relationship to Other Proposals

- **`[]T` → `*[]T` syntax change**: complementary. The `*` prefix makes raw slices
  visually distinct; restricting implicit conversion makes the distinction enforced.
- **Coding guide's "returning `*[]T` from an allocating function is wrong" rule**:
  this proposal would make that a compile-time error (returning `@[]T` as `*[]T`
  would require explicit conversion, forcing the programmer to think about it).

## Open Questions

- What explicit conversion syntax is best?
- How much existing code breaks? Is the migration tractable?
- Should there be a "trusted" annotation or unsafe block that re-enables implicit
  conversion for low-level code?
- Does the rule need to distinguish between "the managed value is a local variable"
  (caller controls lifetime) vs. "the managed value is from a field/slice" (lifetime
  less clear)?
- Is the borrowing/storing distinction purely syntactic (argument position vs.
  everything else), or does it need any flow analysis?
