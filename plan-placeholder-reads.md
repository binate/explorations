# Plan: reads of a placeholder while a declaration's type or value is found

Status: design v3, 2026-11-26 (temp-5): the head rule (user 2026-11-26: "Head rule (Recommended)"), replacing v2's
design -- declarations pending on a placeholder, undone and resolved again, with errors owned by declarations -- after
a review of v2 against binate main `6ec2bc17e` found that undo without a clear edge (section 9).  Not reviewed yet;
reviewed before implementing.  Two questions for the user (section 8).  Covers five `claude-todo.md` entries:

1. "An array length checked while its variable's type is found takes that variable's placeholder for an error: its
   errors are lost, and a variable read before its turn is a false cycle".
2. "An array length evaluated while a variable's type is found and left without a value by its placeholder is blamed
   as its type cycle, even where the variable's own length does not need it".
3. "A constant read while it is being resolved, where its value is not needed, is a false constant definition cycle".
4. "A type read in its own array length where its value is not needed is checked with no underlying: an ill-typed
   literal there is accepted" (into this plan: user 2026-11-26, "(a)").
5. "A function read in the array length of a variable its signature needs is \"undefined\"" (likewise).

## 1. The rule

Spec `decl.var.type-acyclic` (docs/spec/09-declarations-and-scope.md): a package variable's type is its declared
type, or else its initializer's; an initializer that spells its type at its head (a function literal, a composite
literal of a written type, a `cast` / `bit_cast`, a call of a package's function, a generic instance, a method of a
package variable, a function literal called where it is written) has that type before its operands are checked.
"Any other initializer's type depends on the type of each variable it names outside the array lengths in it, a
function literal's body included.  A type depends on each variable whose type the value of an array length within it
needs: `len(v)` needs v's type, while `len(pick(func() int { return len(v) }))` needs only pick's result's."

The head rule reads the last sentence by the one before it: the value of `len(e)` needs e's type, found as an
initializer's is -- the type e spells at its head, or else the type of each variable e names outside the array lengths
in it, a function literal's body included (S1, section 8).  `sizeof` and `alignof` take a type, whose layout they
need; `len` is the one constant operand that is an expression (constval/eval.bn).

The spec has no rule for a constant's definition cycle (§9.1 `decl.const` and Ch.6 name none); the checker reports
"constant definition cycle".  The plan proposes one (S2, section 8): a constant's value may not depend on itself, and
it depends on each constant its initializer names outside its array lengths, and on what each array length within it
needs.

## 2. Today

- `checkerEnv.Len` (check_constval.bn) types `len`'s operand by checking it in full (`checkDimOperand`,
  check_var_placeholder.bn), but for a composite literal in a `.bni` being built, whose length comes from its written
  type alone (`compositeLitArrayLen`).  The operand's annotations are reused (`ResolvedTypeID`), so it is checked
  once, wherever the length is first evaluated: during collection, a constant's resolution, a variable's on-demand
  definition.
- A variable named while its type is found takes a placeholder (`definePlaceholder`: the error type), in
  `batchVarOnDemand` (check_decl_batch_var.bn): its declared type being resolved (`VarsResolving`); its head type
  being found (`VarsHeadTyping`), which reports `errVarTypeCycle` at once, unless a layout frame waits; its
  initializer being checked (`VarsChecking`), which binds it to the head type a second search finds, or else a
  placeholder.  A read inside a build the variable's resolution started does not take one: the build waits
  (`declReadWaits`, check_layout_wait_decl.bn) and is built again by the flush; so do a constant's
  (check_by_name_walk.bn `startConstByName`) and a function's (check_decl_batch.bn).  Inside a build that waits
  already (`skipDemand`) nothing is defined on demand.
- A placeholder left a length without a value: `evalArrayLen` -> `placeholderPoisoned` reports that variable's type
  cycle wherever the length is evaluated; `noteConstPoisoner` / `notePoisonedConst` carry it through a constant;
  check_const_pending.bn keeps a constant resolved while a placeholder it read stands pending, released lazily
  (`resolveDeclOnDemand`, `startConstByName`), and a variable whose initializer read one is checked again
  (`VarsStale`).
- The by-name walk (check_by_name_*.bn) resolves a declaration's reads ahead of it, on an explicit stack, so a chain of
  any length is resolved without recursion: a constant's the constants its initializer names (`collectConstDeps`,
  check_const_resolve.bn: every name, call arguments included) and the types whose layouts it reads; a variable's or
  function's what its type reads (`collectExprReads`, check_by_name_reads.bn: a call's callee, then its arguments).
  `startConstByName` reports a definition cycle for any read of a constant being resolved (`ConstResolving`), on
  demand or ahead, unless the read waits; bni_by_name_walk.bn has its own copy of that check.
- `initHeadType` (check_var_head.bn) is the head rule for an initializer, and takes any expression: a function
  literal's signature, a composite literal's written type (not `[...]T`), a cast's or bit_cast's type, a call's one
  result (`calleeResult`; a function or method of the batch whose signature is not defined yet has its result type
  resolved alone, `definingFuncResult`).  `varHeadType` wraps it for a variable, and returns nil when a placeholder left
  a length in the head without a value or the head holds an instance not built yet (`instanceNotBuiltIn`).
- A variable defined on demand with its type (`defineVarOnDemand` :109, and `finishBatchVar`, check_by_name_vars.bn,
  for one read ahead) has its layout prepared, which collects the types it holds by value.
- `checkBitCastShapes` (check_c_interop.bn) defers its size check while an operand's type is not collected
  (`deferIfPending`), and has no guard for an operand of the error type.

Probed on binate main `6ec2bc17e`, with `func pick(f @func() int) [3]int`, `func pickN(n int) [3]int`, `func g4(x
[4]int) [4]int`, `func f(x int) [3]int`, `type S struct { arr [3]int; a [3]int; k int }`, `func wrap(x [3]int) S`:

| | program | today | right |
|---|---|---|---|
| p1 | `var z = u`; `var u = [len(pick(func() int { return len(u) }))]int{}` | cycle `u` | u is a `[3]int` |
| p2 | `var u = [len(w)]int{}`; `var w = [len(pick(func() int { return len(u) }))]int{}` | cycle `w` (`u` in the other order) | both `[3]int` |
| p3 | `var u = wrap([len(pick(func() int { var t bool = u[0]; _ = t; return 0 }))]int{}).arr` | compiles | "cannot assign int to bool" |
| p3' | `var z = u`; `var u = [len(pick(func() int { var t bool = u[0]; _ = t; return 0 }))]int{}` | cycle `u` | as u alone: "cannot assign int to bool" |
| p4 | `var V [len(g4(V)) + 1]int` | compiles, V a `[5]int` | "cannot assign [5]int to [4]int" |
| p4' | `var V [len(cast([4]int, V)) + 1]int` | compiles | "cast does not support this conversion" |
| p5 | `const K = len(bit_cast([3]int, V))`; `var V [K]int` | "bit_cast requires the source and target to be the same size" | V a `[3]int` |
| g1 | `var V [len(bit_cast([5]int, V))]int` (and `var V = [len(bit_cast([5]int, V))]int{}`) | the same size error | V a `[5]int` |
| h1 | `var x [4]int = bit_cast([4]int, nosuch)` in a function | "undefined: nosuch" and the size error | "undefined: nosuch" alone |
| f1 | `var X [len(pick(func() int { return V }))]int`; `var V = X[0] + 1` | cycle `X` | X `[3]int`, V int |
| b1 | `var Z [len(V)]int`; `var V = mk()`; `func mk() [len(pick(func() int { var t bool = V[0]; _ = t; return 0 }))]int` | cycle `V` | "cannot assign int to bool" |
| d1 | `var b [len(pick(func() int { return len(a) }))]int`; `var a = [len(b)]int{len(a)}` | cycle `b`, initialization cycle `a` | both `[3]int` |
| e1 | `func f2(x T) [3]int`; `var V [len(f2(T{}))]int`; `type T [K]int`; `const K = len(V)` | cycle `V` | V `[3]int`, T `[3]int` |
| e2 | `var V [len(f(K))]int`; `const K = cast(int, sizeof([len(V)]int))` | cycle `V` | V `[3]int` |
| e3 | `var V [len(W)]int`; `var W = S{k: len([K]int{})}.a`; `const K = len(V)` | cycle `V` | cycle `V` (F1, section 10) |
| e4 | `var V [len(f(K))]int`; `var W [L]int`; `const K = len(W)`; `const L = len(V)` | cycle `V` | V `[3]int` |
| e5 | `type T [len(u)]uint8`; `var u = [len(pickN(N))]int{}`; `const N = len(u)` | cycle `u` | u `[3]int`, T `[3]uint8` |
| c1 | `const K = len(pick(func() int { var t bool = K; _ = t; return 0 }))` | cycle `K`, and "cannot assign untyped int to bool" | the bool error alone (K is 3) |
| c2 | `const K = len(f(K))` | cycle `K` | K 3 |
| x1 | `var V [len(f(K))]int`; `var U [len(f(K))]int`; `const K = len(V) + len(U)` | cycle `K` | K 6 |
| x2 | `var D = S{k: K}.a`; `const K = len(D)` | cycle `K` | S2 (section 8): cycle `K`, or K 3 |
| x3 | `var V = [len(f(K))]int{}`; `const K = len(V)` | cycle `K` | K 3 |
| a1 | `var V S`; `type S [len(V)]int` | "array length must be a constant integer" | cycle, reported as S's (F2) |
| a2 | `type S struct { a [len(V)]int }`; `var V = S{}` | "cyclic type definition involving `S`" | as today |
| a3 | `type Box[T any] struct { a [len(V)]int; t T }`; `var V Box[int]` | "array length must be a constant integer" | cycle, reported as Box[int]'s |
| F2 | `var V [3]S`; `type S struct { x [len(V)]int }` | compiles, `len(V[0].x)` 3 | as today (F2, section 10) |
| F2' | `type S struct { x [len(V)]int }`; `var V [3]S` | "cyclic type definition involving `S`" | as F2 |
| f2u | `type U [len(V)]int`; `var V [3]S`; `type S struct { x [len(V)]int }` | "cyclic type definition involving `S`" | as F2 |
| t1 | `func f2(x T) [3]int`; `type T [len(f2(T{true}))]int` | compiles | "cannot assign untyped bool to int" (or as worded) |
| k5 | `func f(y [len(V)]int) [3]int`; `var V [len(f([3]int{}))]int` | "undefined: f" | V `[3]int` |
| q1 | a `.bni` with `func F(x int) [3]int`; `const K = len(F(K))` | cycle `K`, "undefined: K" | K 3 (F10) |

Entry 3's own programs are x1-x3.  The probes are in the session scratchpad (s20/pr, review27).

## 3. Design

### 3.1 A `len` operand is typed by its head (entries 1, 2, 4, 5)

`checkerEnv.Len(e)`: when e spells its type at its head (`initHeadType`, which takes any expression), the length is
that type's -- its own array lengths evaluated as any type's -- and e is checked in full later (3.2), not now.  An
array length then reads, while it is evaluated, only what its value needs: `len(pick(...))` reads pick's result type
and nothing of the literal; `len(f2(T{true}))` reads f2's result type, not T (t1); `len(f([3]int{}))` reads f's result
type alone, while f's signature is being defined (`definingFuncResult`; k5).

With no head -- an identifier, a selector, an index, a `[...]T` literal, a call of a method of an expression that is not
a package variable, a generic function's call whose type arguments are inferred, a callee that is undefined or whose
head is not known yet (`varHeadType`'s nil cases) -- e is checked in full now, as today, and depends on each variable it
names outside its array lengths, a function literal's body included (S1): a placeholder read anywhere in its check is
that variable's type cycle (counted as `notePlaceholderRead` counts reads; reported for the first).  That is what makes
a check of a head-less operand against a placeholder never lose an error: the variable has the error type, and its
cycle is reported.

The bit_cast guard: `checkBitCastShapes` gives no verdict for an operand of the error type (h1).

### 3.2 The full check, later

The operand checked later is queued with its context: `c.Scope` (a file's scope, or a generic declaration's with its
type parameters), the iota of the const-group member it is in, the declaration it is in (for `DeclStarts` and a REPL
batch's attribution), and its head type.  It is queued while the batch resolves a declaration -- collection, a
constant's resolution, a variable's or function's definition on demand, a type collected on demand -- and checked at
once anywhere else (a function body checked in pass 2, a generic instance's build: a build's reads of a declaration
being resolved wait, `declReadWaits`).

The queue is checked after pass 2's `checkDecls` -- every declaration is collected and every package variable has its
type, so no placeholder stands -- and before `orderBatchVars` (`len` evaluates nothing, so the init order does not
change).  An entry checked may queue none (nothing is being resolved then).  In a REPL batch, at the same point of the
batch's own pass (checker_persistent.bn).  For a package checked for its declarations only (`CheckPackageDecls`), the
queue is dropped: its own compile checks it, as a dependency's instances are (`discardInstWork`).

The full check's type must be identical to the head type (the head is what the spec says the type is): an internal
error if not.  Its errors are reported once, in the declaration's context; nothing is dropped or undone.

### 3.3 The walk resolves ahead only what the head reads (entries 2, 3)

`collectConstDeps` and `collectExprReads`, on `len(e)` with a head, collect what the head reads -- a call's callee (its
result type's reads), a composite literal's or a cast's written type, a function literal's signature -- and not its
arguments, elements or operand, which 3.2 checks later.  So `const K = len(f(K))` resolves nothing ahead (c2), and V's
type in e2 / e4 / e5 / x1 / x3 resolves no constant ahead.  `startConstByName`'s cycle report stays: a read of a
constant being resolved that does not wait is a read of its value, which its value needs -- a cycle (S2).

### 3.4 What reads a type being collected or built (a1, a3, F2', f2u)

A variable defined on demand gets its type, not its layout: `defineVarOnDemand` and `finishBatchVar` stop preparing
it.  What reads a layout -- `sizeof`, a type holding the variable's type by value, `bit_cast` -- prepares it, as any
reader of a layout does; the detection of a layout not built yet inside a build (`leaveDemandFrame`) moves with it.
`len(V)` of a variable whose type is a named type or instance whose underlying is being collected or built is that
type's cycle: "cyclic type definition involving `S`" (a1, as F2 decided), Box[int]'s (a3), never "array length must be
a constant integer".

### 3.5 `.bni` declarations (F10)

The `.bni` walk (bni_by_name_walk.bn, bni_by_name_func.bn) takes 3.1 and 3.3: a `.bni` constant `len(F(K))` is F's
result type's (q1).  Nothing of a `.bni` is checked later (its operands are checked by the declaring package's own
check: the `compositeLitArrayLen` precedent), so 3.2 does not apply there.

### 3.6 What may go

With 3.1 and 3.3, a constant resolved while a placeholder stands has read it only where its value needs it, a cycle:
check_const_pending.bn's pending constants, `VarsStale` / `recheckStaleVars`, and `ConstPoisoners` may have no use
left.  The last step removes what its tests show has none, and keeps what still has one.

## 4. Why it is sound

- Every check made against a placeholder is one the rule says the type depends on: a head-less `len` operand (S1), a
  head-less initializer of a variable being typed (the spec), a constant's initializer read for its value (S2), a head
  read for what the head needs (a callee variable `v.m`'s type).  Each reports a cycle and leaves the variable or
  constant the error type, so nothing it found is wrong or lost.  Reads inside a build wait and are built again.
- What a length does not need is checked once, after every type is known: no placeholder stands, so nothing is undone,
  no error is dropped, and no once-only check (a constraint latch, a held build's release) runs against a placeholder.
- The full check agrees with the head type or is an internal error.
- A cycle is reported for the declaration whose type or value needs itself: the culprit is the variable (or constant)
  whose placeholder its own needed read meets.

## 5. Steps

Each lands on its own, with the tree green and its tests seen to fail before it and pass after.

1. 3.1 and 3.2, with the bit_cast guard, and S1's spec edit if the user takes it: p1-p5, p3', g1, h1, f1, b1, d1, e1,
   t1, k5 (each checked when the step is done; one that also needs the walk's change waits for step 2).
2. 3.3, and S2's spec rule if the user takes it: c1, c2, e2, e4, e5, x1, x3; conformance
   regressions/const-read-as-arg-while-resolved and regressions/type-reads-const-while-var-typed lose their xfails.
3. 3.4: a1, a3, F2', f2u.
4. 3.5: q1.
5. 3.6.

## 6. Tests

A unit test (pkg/binate/check) for each program in section 2, and a conformance test for each of p1, p3, p4, b1, c1,
e1, t1, k5, F2' and x1 (run tests print the lengths; error tests pin the message).  Kept as they are:
TestVarTypeCycleFuncLitBodyNotCycle, the regressions/var-type-cycle-* errors, the spec's examples in
decl.var.type-acyclic.  A deferred check's errors come after pass 2's: tests pinning an order among errors are
found by the unit and conformance runs.

## 7. Risks

- The context a later check needs (3.2): a const group's iota, a file scope, a generic declaration's type parameters,
  a REPL batch's `TentativeMode` and `EditFloor`.  The archived fix failed on the iota (section 9's first review).
- A head that a later check does not confirm: the internal error makes it loud; the head rule is the spec's.
- Moving layout preparation out of on-demand definitions (3.4) changes when a layout wait is found inside a build.
- Error order changes for errors in `len` operands found while declarations are resolved.

## 8. Questions for the user

- S1: a `len` operand with no head depends on each variable it names outside its array lengths, a function literal's
  body included, as an initializer with no head does.  So `var u [len(h(func() int { return len(u) }).arr)]int` (for
  `func h(f @func() int) S`) is a type cycle.  Recommended: yes, with the spec's last sentence of
  decl.var.type-acyclic reworded to say so.  Without it, a head-less operand's check against a placeholder loses its
  errors (as p3 does today), and needs v2's undo.
- S2: a rule for a constant's definition cycle: a constant's value may not depend on itself; it depends on each
  constant its initializer names outside the array lengths in it, and on what each array length within it needs
  (`const K = len(f(K))` is 3).  And x2: (a) a head-less initializer's type depends on the value of each constant it
  names, as on each variable's type (F1), so x2 is K's cycle -- recommended, as for e3; (b) x2 is valid: D's check
  then has to use K's type without its value, and a read of its value (`S{k: K + 1}`) stays a cycle or needs v2's undo.

## 9. Reviews

v1 (2026-11-03): a focused adversarial review, each finding probed on binate main `311d4b282`: the no-head free-read
rule reporting another variable's cycle (f1); `definingFuncResult` stamping a function's result type with a check made
against a placeholder (b1); no re-check, and no termination argument, for constants being resolved, and the
dependency pre-pass's syntactic cycle (c1, c2); a head search left without a value falling back to a check with no head
(d1); V's own first-pass errors kept, and the bit_cast guard missing (g1, h1); cycles through V's named type or instance
outside V's frame (a1-a3).  v2 took each in.  An earlier fix (binate `59b804c91`, not landed, archived on
`archive/20261030-dim-read-recheck`) had dropped the head-typing report, re-checked each package-level length operand
that read a placeholder after pass 2, and found a checked variable's head type again "quietly"; its review found errors
of other declarations dropped (an ill-typed program compiled and ran), a variable left the error type with nothing
reported, a constant left without a value and never judged (IR-gen panics), and the re-check outside its const
group's iota.

v2 (2026-11-04): a second review raised three questions the user decided (F1, F2, F10; section 10); its other
findings were not recorded.

v2 (2026-11-26, against binate main `6ec2bc17e`): section 2 had gone stale (the by-name walk, reads that wait inside a
build, the layout an on-demand definition prepares, the error machinery's in-place edits); and v2's design had no clear
edge.  Its walk of the types V holds by value contradicted F2 (B1); F2' was fixed nowhere, since the cause is the
layout an on-demand definition prepares (B2); undoing a declaration dropped its errors but not the once-only state that
stops them being found again -- a constraint check latched per instance, a held build's releases -- so ill-typed
programs would compile (B3); the undo list missed FuncsDefined, the methods' records, IfacesCollected,
TypesPrepared / TypesReadied / TypesWalking, ConstMembersResolved, the queued opaque re-checks and the instance
entries' state (B4); errors owned by the innermost declaration mis-assign work done for another (B5); culprits had
nothing to attach to, an array left without a value being the error type singleton (B6); F10 was in no step (B7); and
types and functions read while they are resolved have the same bugs (B8, now entries 4 and 5).  Its step 1 (error
ownership) was not well-defined against errors edited in place and the counts taken "since a mark", step 2 had no test
of its own, and step 3 alone would have accepted code rejected today.  v3 replaces the design (the head rule), so that
nothing is undone.

## 10. Decisions

Decided 2026-11-04 (user: "C seems ok. Go with your recs, but get focused reviews of the decisions."), on three
questions from the second review of v2 (F1, F2, F10):
- F1 (a): keep the spec.  An initializer with no type at its head depends on every variable its array lengths need,
  so the spec's `var a = len([len(b)]int{})` with `var b = h(a).arr` stays a cycle (as
  regressions/var-type-cycle-read-in-call pins), and so does e3 (W's type depends on K), dropped from entry 2's valid
  examples.
- F2 (a): `var V [3]S` with `type S struct { x [len(V)]int }` is valid -- V's type needs only S's declaration, as for
  a signature (decided 2026-11-01), so `len(V)` is 3 in either declaration order; `var V S` with `type S [len(V)]int`
  stays a cycle, reported as S's.
- F10 (a): constants in a `.bni` get entry 3's fix too, so a `.bn` and a `.bni` agree on `const K = len(F(K))`.
The review of those decisions found the `bit_cast` size check skipped while its operand's layout is being built
(claude-todo.md, "A `bit_cast` whose operand's layout is still being built skips its size check").

Decided 2026-11-26: entries 4 and 5 go into this plan (user: "(a)"); the design is the head rule (user: "Head rule
(Recommended)").

Re-probed 2026-11-26 on binate main `6ec2bc17e`: every program in section 2 gives its "today" column.
