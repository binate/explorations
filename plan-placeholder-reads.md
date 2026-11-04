# Plan: reads of a placeholder while a variable's type is found

Status: design, 2026-11-03 (temp-5), not reviewed yet.  Covers three `claude-todo.md` entries that share one judgment:

1. "An array length checked while its variable's type is found takes that variable's placeholder for an error: its
   errors are lost, and a variable read before its turn is a false cycle" (claimed, temp-5; the user: "we can design
   it now").
2. "An array length evaluated while a variable's type is found and left without a value by its placeholder is blamed
   as its type cycle, even where the variable's own length does not need it" (OPEN; its entry says its design "needs
   the same judgment").
3. "A constant read while it is being resolved, where its value is not needed, is a false constant definition cycle"
   (OPEN; its proposed fix takes the constant as a variable's placeholder is taken).

Whether 2 and 3 are done with 1 is for the user (section 8).

## 1. The rule

Spec `decl.var.type-acyclic` (docs/spec/09-declarations-and-scope.md): a package variable's type is its declared
type, or else its initializer's; an initializer that spells its type at its head (a function literal, a composite
literal of a written type, a `cast` / `bit_cast`, a call of a package's function, a generic instance, a method of a
package variable, a function literal called where it is written) has that type before its operands are checked.
"Any other initializer's type depends on the type of each variable it names outside the array lengths in it, a
function literal's body included.  A type depends on each variable whose type the value of an array length within it
needs: `len(v)` needs v's type, while `len(pick(func() int { return len(v) }))` needs only pick's result's."

So while V's type is found, V's type is needed only where the value of a length of V's type needs it.  Everything
else checked on the way -- a call's arguments, a function literal's body, a composite literal's elements, a cast's
operand, another declaration resolved on demand whose result V's type does not take -- still has to be checked, but
against V's real type, once V has one.

## 2. Today

- `batchVarOnDemand` (check_decl_batch_var.bn) binds V, named while its type is found, to a placeholder
  (`definePlaceholder`: the error type), which a read takes for an error already reported.  Its three branches: V's
  declared type being resolved (`VarsResolving`); V's head type being found (`VarsHeadTyping`), which reports
  `errVarTypeCycle` at once; V's initializer being checked (`VarsChecking`).
- `evalArrayLen` -> `notePoisonedLen` -> `placeholderPoisoned` (check_var_placeholder.bn): a length left without a
  value by a placeholder, wherever it is evaluated -- in V's type, in a constant or a type resolved on demand, in a
  literal's body -- is reported as that variable's type cycle.  `noteConstPoisoner` / `notePoisonedConst` carry the
  same through a constant.
- check_const_pending.bn: a constant resolved while a placeholder it read stands is pending -- kept while the
  placeholder stands, undone once it does not (`releasePendingAt`), resolved again when next read or by
  `finishTopLevelConsts` -- and a variable whose initializer read one is checked again (`VarsStale`,
  `recheckStaleVars`).  No other kind of declaration is pending.
- `judgeFreeReads` / `judgeSelfReads` (check_var_free_reads.bn): a placeholder read in an initializer outside its
  array lengths.
- `checkerEnv.Len` / `Const` (check_constval.bn) reuse an operand's checked type (`ResolvedTypeID`), so an operand
  checked against a placeholder is never checked again.
- Errors (`addCheckError`, checker_errors.bn) are one list, one entry per (position, message); drops are by count
  (`dropChecksSince`, with `StandingErrors` / `ConstErrs` to re-report what a drop must keep).
- A generic instance's body is checked after pass 2 and `orderBatchVars` (`drainInstances`, checker.bn), so it never
  reads a placeholder.

Probed on binate main `311d4b282`, with `func pick(f @func() int) [3]int`, `func pickN(n int) [3]int`, `func g4(x
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
| e1 | `func f2(x T) [3]int`; `var V [len(f2(T{}))]int`; `type T [K]int`; `const K = len(V)` | cycle `V` | V `[3]int`, T `[3]int` |
| e2 | `var V [len(f(K))]int`; `const K = cast(int, sizeof([len(V)]int))` | cycle `V` | V `[3]int` |
| e3 | `var V [len(W)]int`; `var W = S{k: len([K]int{})}.a`; `const K = len(V)` | cycle `V` | V `[3]int` |
| e4 | `var V [len(f(K))]int`; `var W [L]int`; `const K = len(W)`; `const L = len(V)` | cycle `V` | V `[3]int` |
| e5 | `type T [len(u)]uint8`; `var u = [len(pickN(N))]int{}`; `const N = len(u)` | cycle `u` | u `[3]int`, T `[3]uint8` |

(`var V [len(bit_cast([4]int, V)) + 1]int` is rejected today, rightly, but for a size compared against the
placeholder.)  The cast and call verdicts in the last column were checked on their own (`var a [5]int` passed to each).

The archived fix (binate `59b804c91`, not landed, kept on the local branch `archive/20261030-dim-read-recheck`) dropped the head-typing report,
re-checked each package-level array-length operand that read a placeholder after pass 2, and found a checked
variable's head type again "quietly".  Its review found: errors of variables checked on demand inside the quiet search
dropped (an ill-typed program compiled and ran); a variable left the error type with nothing reported (IR-gen panic);
a constant resolved during head typing left without a value and never judged (IR-gen panic); the re-check outside its
const group's iota.  Hence the requirements:

- R1: every declaration that read a placeholder is judged again, constants included.
- R2: no error that belongs to another declaration is dropped.
- R3: a check done again runs in its full context.
- R4: a cycle is reported for the variable whose own type needs itself, and only then.

## 3. Design

### 3.1 Two kinds of read

A read of V's placeholder, or of a result computed from one, either leaves a value without one -- an array length's
or a constant's, where the value needs V's type -- or leaves only checks incomplete: a call's argument, a literal's
body, a cast's operand, where the value it feeds is found without V.

The design relies on one property: a placeholder never yields a wrong value, only none (constant evaluation leaves
without a value whatever an operand of the error type reaches).  Section 3.5's identity check is its safety net.

### 3.2 V's cycle is judged at V's own type (entries 1 and 2)

V's search is a frame: V's declared type resolved, its head type found, or its initializer checked.  `LenDepth` is d0
when it starts.

- Declared or head type: V's type depends on itself iff a length evaluated in V's own frame at depth d0 + 1 -- a
  length of V's type -- is left without a value with V as its culprit.  A length nested in such a length's operand
  whose value the outer one takes leaves the outer one without a value, so it is caught at d0 + 1.  A length in a
  function literal's body (d0 + 2 or deeper, in an operand) or in another declaration resolved on demand (another
  frame) is not V's cycle.
- Initializer with no head: as today -- a length in V's own frame at d0 + 1 with V as its culprit, and the free reads
  (`judgeFreeReads`), which the spec's rule for such an initializer covers.  (A length in a function literal's body
  in such an initializer stays a cycle, as today; not changed here.)

A culprit is the variable whose placeholder left a value without one (`PoisonedBy` today).  It reaches V's frame
directly, or through a pending declaration's result read in a value position (3.3): a constant (`notePoisonedConst`
today), a type's layout (`sizeof(T)`), a variable's type (`len(W)`), a signature's result.  So `placeholderPoisoned`
stops reporting; the judgment moves to the end of V's search, which reports `errVarTypeCycle(V)` when V's result
holds such a length.  A cycle through two variables (`var a [len(b)]int`; `var b [len(a)]int`) is reported for the
variable whose search started first: b's search leaves b's own length without a value with culprit a, so b is
pending on a, not a cycle; a's length then reads b's result and is left so with culprit a.

### 3.3 Pending declarations of every kind

Today only constants are pending.  Every declaration resolved while a placeholder stands, whose resolution read a
placeholder or a pending declaration's result (other than V's own frame reading V's own placeholder, which 3.2 and 3.5
judge), is pending: a constant; a variable's declared type, head type or initializer check; a named type
(`collectBatchTypeDecl`, `collectTypeOnDemand`, `prepareLayout`); an interface (`collectIfaceOnDemand`); an alias; a
function's or method's signature (`batchFuncOnDemand`, `batchMethodOnDemand`); an instance's build; a generic
declaration resolved abstractly; a generic type's methods copied onto an instance.  The record holds the declaration,
the placeholders it depends on (read directly or through what it read), and its culprits if its result is left without
a value.

- While those placeholders stand, its result is kept: a value found without V serves the lengths that need it, and a
  result left without a value carries its culprits to what reads it.
- What reads a pending declaration's result is pending on the same placeholders.  A declaration that only names a
  pending type, without reading its layout or members, is counted too: a redo that was not needed costs time, not a
  wrong answer, and this happens only while a placeholder stands.
- When V is defined -- with its type, or the error type once its cycle is reported -- each pending declaration none
  of whose placeholders stands any more is undone: in place for a named type (`setUnderlying(c, t, nil)`, as
  `collectTypeOnDemand` undoes one that waited) and an instance (`undoInstanceBuild`); its binding restored for the
  others (`restoreBinding`); its record as resolved, collected or defined removed; its annotations cleared
  (`ast.ClearCheckerAnnotations`); its own errors dropped (3.4).  It is resolved again when next read, when its turn
  comes, or, its turn passed, by a sweep at the end of the batch (as `finishTopLevelConsts` resolves a pending
  constant).
- Termination: a redo runs with one more placeholder gone; it can be pending again only on placeholders that still
  stand.

check_const_pending.bn becomes this mechanism's constant case, and `VarsStale` its variable case.

### 3.4 An error belongs to the declaration that reported it (R2)

Each error records its owner: the innermost declaration being resolved when it was reported (`DeclStarts`, extended to
types, interfaces, aliases, signatures and builds).  Undoing a pending declaration drops its own errors, and those of
the pending declarations undone with it; an error of a declaration resolved on demand inside it that is not pending
stands.  Dedup (`appendUniqueCheckError`) keeps one entry per (position, message) with every owner that reported it,
and an entry goes only when it has no owner left -- otherwise a stable declaration's error, deduped against a pending
one's identical error, would go with it.  The REPL's `TentativeErrors` the same.  This is the error half of the drop
the archived fix did by count.

### 3.5 V's own check, again (entry 1)

V's own frame may read V's placeholder, or a result pending on V, where V's type does not need it: p3, p4, p4' and the
size check of `var V [len(bit_cast([4]int, V)) + 1]int`.  Once V has its type, V's declaration is checked again: its
declared type's lengths, annotations cleared, or its initializer (`recheckStaleVars`'s path).  The type found again must
be identical to V's: an internal error if not (3.1).  The head-typing branch's immediate report goes; V named in its own
head is judged by 3.2 (p1, p2, p3').

### 3.6 Constants being resolved (entry 3)

A constant being resolved (`ConstResolving`) and read again where its value is not needed (`var V [len(f(K))]int`;
`var U [len(f(K))]int`; `const K = len(V) + len(U)`) is a placeholder the same way: a definition cycle iff the
constant's own value is left without a value with it as the culprit; otherwise what read it is pending on it, and is
resolved again once it has its value.

## 4. The requirements

- R1: 3.3 (every kind, constants included, resolved again).
- R2: 3.4.
- R3: a redo goes through the declaration's own path -- `resolveConstByName` with its group's iota, collection, the
  signature's definition, the instance's build -- never a detached operand.
- R4: 3.2.

## 5. Steps

Each step lands on its own, with the tree green.

1. Error ownership (3.4): the owner stack over every kind, owners on errors, drop by owner, dedup by owner set.  No
   change in behavior; unit tests of drop by owner and of dedup across owners, in the batch and in the REPL.
2. Pending declarations of every kind (3.3), with culprits; constants and `VarsStale` move onto it; the end-of-batch
   sweep.  p5 is fixed here (K's false size error is K's own, dropped with K's redo).
3. V's cycle judged at V's own type (3.2): e1-e5; regressions/type-reads-const-while-var-typed loses its xfail.
4. V's own check again, without the head-typing report (3.5): p1-p4'.
5. Constants being resolved (3.6): entry 3; regressions/const-read-as-arg-while-resolved loses its xfail.

## 6. Tests

A unit test (pkg/binate/check) for each program in section 2 and each of entry 3's, and a conformance regression for
each of p1-p5.  Kept as they are: TestVarTypeCycleFuncLitBodyNotCycle, the regressions/var-type-cycle-* errors, the
spec's examples in decl.var.type-acyclic.  May name the other variable of a two-variable cycle (as the archived fix
did): spec/09 178.  Each step's tests run before it, and pass after.

## 7. Risks

- The property in 3.1.  A case where a placeholder yields a wrong value would break the identity check in 3.5.
- Pending declarations and layout waits both undo and redo; a declaration can be both (it read a layout not built
  and a placeholder).  The order of their redos, and their flushes, needs care.
- A named type undone in place: what read its layout while it was pending is pending too, so no stale layout
  survives, but instances built from it must be on the list (3.3).
- REPL batches (`TentativeMode`, `EditFloor`): an error owned by an earlier declaration must never be dropped.
- Cost: none on programs with no placeholder reads; redos only while a placeholder stands.

## 8. Scope

Step 3 is entry 2's fix and step 5 entry 3's; steps 1, 2 and 4 are entry 1's (p1-p5).  e5, which the archived fix's
review found, needs step 3 as well.  Recommended: one project for all three entries.
