# Code-Red P2 — Fix plan (four disjoint work plans)

P1 (discovery) built a family of coordinate-addressed conformance matrices
(`conformance/matrix/{refcount,scalar,abi,const}`) plus the
`conformance/regressions/{const-expr,c-call}` point-test suites, and cataloged
the code-red defect set in `claude-todo.md`. **P2 is the fix phase**: the
confirmed defects are partitioned into **four disjoint work plans** so they can
be executed in parallel by separate workers, leaving a fifth worker free for
other tasks.

Each plan is source-confirmed (root cause verified against the code, not just
restated from the todo) and states, per defect, the fix shape, the files to
touch, and the exact test coverage vs. gap. The defect-of-record stays in
`claude-todo.md`; these docs are the *execution* view.

## The partition

| Plan | Theme | Subsystems | Defects | Test coverage |
|---|---|---|---|---|
| **[1](plan-cr-p2-1-frontend.md)** | Front-end: const materialization & expr-folding, float32 coercion, `&slice[i]` address-of, declaration resolution, int-int loader | `pkg/binate/ir` (gen_const, gen_expr/coerce, gen_util, gen_composite, gen_return, address-of), `pkg/binate/types`, `pkg/binate/loader` | 6 | `matrix/const`, `regressions/const-expr`, `599` — gaps: fwd-ref, iota decision, loader |
| **[2](plan-cr-p2-2-refcount.md)** | Refcount Axiom-5 discipline & `@Iface`/`@func` lifecycle | `pkg/binate/ir` managed-copy/dtor dispatchers + copy-sites | 6 | `matrix/refcount` — gap: lifecycle matrix (b2) |
| **[3](plan-cr-p2-3-abi.md)** | Aggregate ABI, calling convention & 2-word value passing | `pkg/binate/codegen` (byval/sret, emit_call/iface/ccall), `native/{aarch64,x64}`, `pkg/binate/vm` (2-word handling) | 8 | `matrix/abi`, `regressions/c-call` — gaps: 2-word-slice + iface-arg-drop tests, Class 2 matrix (b1) |
| **[4](plan-cr-p2-4-scalar.md)** | Scalar & float-literal value correctness (sub-word, 64-on-32, float) | `pkg/binate/vm` (exec-arith, int→float, const-load), `native/{aarch64,x64}` (sub-word narrow, float const), shared float-literal converter | 6 | `matrix/scalar`, `538/539/541` — gap: divide-by-zero panic cells |

26 confirmed defects across the four plans.

## Disjointness & parallel-safety

The plans are scoped so two workers don't edit the same functions:

- **Plans 1 & 2** both live in `pkg/binate/ir`, but on **different functions**:
  Plan 1 owns const materialization / scalar-width coercion / address-of l-value
  (gen_const, gen_expr read-path, gen_call coerceArg, gen_composite store,
  gen_return, gen_util resolveTypeExpr); Plan 2 owns the managed-value
  copy/dtor dispatchers and copy-sites (emitManagedValueCopyRefInc /
  emitManagedValueRefDec / emitStoreManagedSlot). The risk seam is a copy-site
  that also needs scalar-width coercion (composite-literal field stores appear
  in both) — coordinate on `gen_composite.bn`.
- **Plans 3 & 4** both touch `native/{aarch64,x64}` and `pkg/binate/vm`, but on
  **different functions**: Plan 3 owns calling-convention / aggregate packing /
  outgoing-args / 2-word value handling (call + return emit, iface dispatch,
  ccall); Plan 4 owns scalar arithmetic narrowing and float-const
  materialization (arith ops, const-load, the float-literal converter). The risk
  seam is the native return path (struct return is Plan 3; scalar/float return
  width is Plan 4) — coordinate on the per-target `*_return.bn`.

When two plans must touch one file, the owning plan is the one whose *function*
is changed; the other plan rebases onto it. Land small, cherry-pick early (the
project's stay-close-to-main discipline) to keep the seams shallow.

## Folded-in items (the dropped 5th plan)

The originally-proposed 5th plan (VM value-handling & loader) was folded in to
keep N=4: the **VM func-value nil-vtable** and **Class 2 VM 16-byte
address-aggregate** items went to **Plan 3** (they are 2-word value-passing
defects), and the **int-int loader `rt`-not-found** went to **Plan 1** (it is a
declaration/import-resolution concern).

## Open decisions (owned by the user, not the plans)

1. **`iota` group-member semantics** (Plan 1) — a bare `const`-group member
   currently takes **plain `iota`**, not the Go-style repeat-previous-expression,
   so `const ( B0 int = 1 << iota; B1; B2; B3 )` yields plain-iota values, not
   the `1,2,4,8` bit-flag idiom. Whether to implement repeat-previous is a spec
   decision; until it's made, the group-member const-expr cell stays out and
   Plan 1 must not assert either behavior. (Filed in `claude-todo.md`.)
2. **Divide-by-zero panic** (Plan 4) — the defined-panic behavior + the
   `unsafe_div` / `unsafe_rem` opt-out intrinsics are **ratified**; the
   implementation and its conformance cells are pending and scoped into Plan 4.

## Coverage posture

P1's whole point was to make P2 testable: most defects already have a pinning
test (a matrix cell or a regression), so the fixer's loop is **un-xfail →
implement → green**, not "write the test first." The gaps the plans must fill
are listed per-plan and small: the lifecycle matrix (Plan 2 / b2), the Class 2
matrix (Plan 3 / b1), the 2-word-slice and iface-arg-drop point-tests (Plan 3),
the untyped-const forward-ref test (Plan 1), and the divide-by-zero cells
(Plan 4). The `claude-todo.md` "TEST COVERAGE" section tracks the candidate new
matrices (b1/b2) and the point-bugs that stay as regressions (b3).
