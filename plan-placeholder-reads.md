# Plan: reads of a placeholder while a variable's type is found

Status: design, 2026-11-03 (temp-5).  Revised after a focused adversarial review the same day (section 9); not
reviewed again yet.  Covers three `claude-todo.md` entries that share one judgment:

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

So while V's type is found, V's type is needed only where the value of a length of V's type -- or of a type V holds by
value, whose layout V's needs -- needs it.  Everything else checked on the way (a call's arguments, a function
literal's body, a composite literal's elements, a cast's operand, another declaration resolved on demand whose result
V's type does not take) still has to be checked, against V's real type, once V has one.

## 2. Today

- `batchVarOnDemand` (check_decl_batch_var.bn) binds V, named while its type is found, to a placeholder
  (`definePlaceholder`: the error type), which a read takes for an error already reported.  Its branches: V's
  declared type being resolved (`VarsResolving`); V's head type being found (`VarsHeadTyping`), which reports
  `errVarTypeCycle` at once; V's initializer being checked (`VarsChecking`), which binds V to the head type a second
  `varHeadType` search finds, or else to a placeholder, reporting `errVarCycle` while types are collected.
- `varHeadType` (check_var_head.bn) returns nil when `PoisonedBy` grew during the search, and the caller then checks
  the initializer as one with no head.
- `evalArrayLen` -> `notePoisonedLen` -> `placeholderPoisoned` (check_var_placeholder.bn): a length left without a
  value by a placeholder, wherever it is evaluated -- in V's type, in a constant or a type resolved on demand, in a
  literal's body -- is reported as that variable's type cycle.  `noteConstPoisoner` / `notePoisonedConst` carry the
  same through a constant.  `evalArrayLen` has a second outcome without a value, lenNotKnown (from NOT_KNOWN, or from
  POISONED inside a layout frame that waits), which `resolveTypeExpr` turns into a `[0]T` meanwhile.
- check_const_pending.bn: a constant resolved while a placeholder it read stands is pending -- kept while the
  placeholder stands, released lazily (`releasePendingAt`, at the next `lookupDeclOnDemand` or `resolveConstByName`
  of it) once it does not -- and a variable whose initializer read one is checked again (`VarsStale`,
  `recheckStaleVars`).  No other kind of declaration is pending.
- `judgeFreeReads` / `judgeSelfReads` (check_var_free_reads.bn): a placeholder read in an initializer outside its
  array lengths.  A read of another variable p's placeholder, p not being checked, reports p's type cycle
  (`freeReadVerdict` -> `errVarTypeCycle(p)`).
- `checkerEnv.Len` / `Const` (check_constval.bn) reuse an operand's checked type (`ResolvedTypeID`), so an operand
  checked against a placeholder is never checked again; `definingFuncResult` (check_var_head.bn) resolves a function's
  result type in place, stamping its syntax.
- Errors (`addCheckError`, checker_errors.bn): `c.Errors` keeps one entry per (position, message) from `DedupFrom` on;
  `TentativeErrors` are appended without dedup; drops are by count (`dropChecksSince`, with `StandingErrors` /
  `ConstErrs` to re-report what a drop must keep).
- `checkBitCastShapes` (check_c_interop.bn) compares sizes with no guard for an operand of the error type, so
  `bit_cast([4]int, nosuch)` reports "undefined: nosuch" and a size mismatch.
- `resolveConstByName`'s dependency pre-pass (`collectConstDeps`) reports a definition cycle for any read of the
  constant in its initializer, call arguments included, before evaluating it.
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
| g1 | `var V [len(bit_cast([5]int, V))]int` (and `var V = [len(bit_cast([5]int, V))]int{}`) | the same size error | V a `[5]int` |
| h1 | `var x [4]int = bit_cast([4]int, nosuch)` in a function | "undefined: nosuch" and the size error | "undefined: nosuch" alone |
| f1 | `var X [len(pick(func() int { return V }))]int`; `var V = X[0] + 1` | cycle `X` | X `[3]int`, V int |
| b1 | `var Z [len(V)]int`; `var V = mk()`; `func mk() [len(pick(func() int { var t bool = V[0]; _ = t; return 0 }))]int` | cycle `V` | "cannot assign int to bool" |
| d1 | `var b [len(pick(func() int { return len(a) }))]int`; `var a = [len(b)]int{len(a)}` | cycle `b`, initialization cycle `a` | both `[3]int` |
| e1 | `func f2(x T) [3]int`; `var V [len(f2(T{}))]int`; `type T [K]int`; `const K = len(V)` | cycle `V` | V `[3]int`, T `[3]int` |
| e2 | `var V [len(f(K))]int`; `const K = cast(int, sizeof([len(V)]int))` | cycle `V` | V `[3]int` |
| e3 | `var V [len(W)]int`; `var W = S{k: len([K]int{})}.a`; `const K = len(V)` | cycle `V` | V `[3]int` |
| e4 | `var V [len(f(K))]int`; `var W [L]int`; `const K = len(W)`; `const L = len(V)` | cycle `V` | V `[3]int` |
| e5 | `type T [len(u)]uint8`; `var u = [len(pickN(N))]int{}`; `const N = len(u)` | cycle `u` | u `[3]int`, T `[3]uint8` |
| c1 | `const K = len(pick(func() int { var t bool = K; _ = t; return 0 }))` | cycle `K`, and "cannot assign untyped int to bool" | the bool error alone (K is 3) |
| c2 | `const K = len(f(K))` | cycle `K` | K 3 |
| a1 | `var V S`; `type S [len(V)]int` | "array length must be a constant integer" | cycle `V` |
| a2 | `type S struct { a [len(V)]int }`; `var V = S{}` | "cyclic type definition involving `S`" | a cycle (naming V or S) |
| a3 | `type Box[T any] struct { a [len(V)]int; t T }`; `var V Box[int]` | "array length must be a constant integer" | cycle `V` |

The cast and call verdicts in the last column were checked on their own (`var a [5]int` passed to each).  Entry 3's
own programs are in its entry.

The archived fix (binate `59b804c91`, not landed, kept on the local branch `archive/20261030-dim-read-recheck`) dropped
the head-typing report, re-checked each package-level array-length operand that read a placeholder after pass 2, and
found a checked variable's head type again "quietly".  Its review found: errors of variables checked on demand inside
the quiet search dropped (an ill-typed program compiled and ran); a variable left the error type with nothing reported
(IR-gen panic); a constant resolved during head typing left without a value and never judged (IR-gen panic); the
re-check outside its const group's iota.  Hence the requirements:

- R1: every declaration that read a placeholder is judged again, constants included.
- R2: no error that belongs to another declaration is dropped, and no record of an error (a variable in `VarsCycled`)
  outlives the error.
- R3: a check done again runs in its full context.
- R4: a cycle is reported for a variable whose type needs itself, and only then.

## 3. Design

### 3.1 Two kinds of read

A read of V's placeholder, or of a result computed from one, either leaves a value without one -- an array length's
or a constant's, where the value needs V's type -- or leaves only checks incomplete: a call's argument, a literal's
body, a cast's operand, where the value it feeds is found without V.

The design needs three properties:

- A placeholder gives no value -- POISONED, never NOT_KNOWN, which would make a `[0]T` (`lenNotKnown`).  Constant
  evaluation holds this today for `Len`, `Const` and `Type` of the error type, or of a type holding it by value; casts,
  bit_casts and calls keep their spelled types.  A constant being resolved (3.6) must give POISONED too.
- A check that reads an operand's layout gives no verdict for the error type: `checkBitCastShapes` gets the guard
  (fixing h1, and the false errors of p5 and g1).
- A pending record made inside a layout frame that waits is discarded with the frame.

Section 3.5's identity check is the safety net for the first.

### 3.2 V's cycle is judged at V's type (entries 1 and 2; R4)

V's search -- its declared type resolved and its layout prepared, its head type found, or its initializer checked --
keeps V "being typed" until it ends; today `defineVarOnDemand` prepares the layout after V leaves `VarsResolving`, so a
read from V's own named type re-enters `defineVarOnDemand` (a1, a3).

- A length left without a value records its culprits: the variables whose placeholders left it so, directly or
  through a pending declaration's result read in a value position (3.3) -- a constant (`notePoisonedConst` today), a
  type's layout (`sizeof(T)`), a variable's type (`len(W)`), a signature's result.  The record is kept on the array
  type left without a value (or the pending declaration holding it), not reported where the length is evaluated
  (`placeholderPoisoned` stops reporting).
- When V's search ends, V's type depends on itself iff its resulting type -- its own array lengths, and the underlying
  of each named type or instance it holds by value (what its layout needs) -- holds a length with culprit V.  That is
  `errVarTypeCycle(V)`.  A length in a function literal's body, or in a declaration V's layout does not reach, is not
  V's cycle (e1-e5, f1).
- A length with another culprit p leaves V's type without a value with culprit p: V is pending on p (3.3), and p's own
  search judges p.  So a cycle through two variables (`var a [len(b)]int`; `var b [len(a)]int`) is reported for the
  variable whose search started first.
- An initializer with no head: V's type is the initializer's, and depends on each variable the initializer names
  outside its array lengths.  A free read of V's own placeholder is judged as today (`judgeFreeReads`,
  `judgeSelfReads`).  A free read of another variable p's placeholder leaves V's type without a value with culprit p
  -- V pending on p -- and never reports p's cycle (f1).

Evaluation order matters: constant evaluation stops at the first operand without a value, so in `var W [len(V)]int`;
`var V [len(W) + len(V)]int` only one culprit is seen, and one variable is reported.  Either names a real cycle.

### 3.3 Pending declarations of every kind (R1)

Today only constants are pending.  Every declaration resolved while a placeholder stands, whose resolution read a
placeholder or a pending declaration's result (other than V's own search reading V's own placeholder, which 3.2 and 3.5
judge), is pending: a constant; a variable's declared type, head type or initializer check; a named type
(`collectBatchTypeDecl`, `collectTypeOnDemand`, `prepareLayout`); an interface (`collectIfaceOnDemand`); an alias; a
function's or method's signature (`batchFuncOnDemand`, `batchMethodOnDemand`); an instance's build; a generic
declaration resolved abstractly; a generic type's methods copied onto an instance.  The record holds the declaration,
the placeholders it depends on (read directly or through what it read), and its culprits if its result is left without
a value.

- While those placeholders stand its result is kept: a value found without V serves the lengths that need it, and a
  result left without a value carries its culprits to what reads it.
- A head search left without a value is a third outcome of `varHeadType`, beside a type and none: the variable is
  bound to the error type as a pending declaration with those culprits, and its initializer is not checked as one with
  no head (d1; the same for `calleeResult`'s method of a package variable whose receiver is a placeholder).
- What reads a pending declaration's result is pending on the same placeholders.  A declaration that only names a
  pending type, without reading its layout or members, is counted too: a redo that was not needed costs time, not a
  wrong answer, and this happens only while a placeholder stands.
- When V is defined -- with its type, or the error type once its cycle is reported -- each pending declaration none of
  whose placeholders stands any more is undone at once (not lazily, as `releasePendingAt` is today): in place for a
  named type (`setUnderlying(c, t, nil)`, as `collectTypeOnDemand` undoes one that waited) and an instance
  (`undoInstanceBuild`); its binding restored (`restoreBinding`); its records removed wherever they are --
  `TypesCollected`, `VarsDefined`, `VarsChecked` (anywhere in it, not only last, or pass 2 skips the check again),
  `VarsDeferred`, `VarsSelfRead`, `VarsCycled`, `ConstPoisoners`, `Placeholders`, `ConstResolved` and the
  `StandingErrors` / `ConstErrs` / `SilentPoisons` counts; its annotations cleared (`ast.ClearCheckerAnnotations`); its
  own errors dropped (3.4).  It is resolved again when next read, when its turn comes, or by a sweep.
- The sweep resolves every undone declaration whose turn has passed, and repeats until none is left.  It runs before
  `finishTopLevelConsts` is done, and again before `orderBatchVars`, `judgeSelfReads` and `drainInstances` -- pass 2
  can undo variables and instances too.
- `definingFuncResult` resolves a clone of the result type (as `resolveInstanceResult` does), and drops the errors its
  resolution reports: they are the function's own, reported when collection defines it, which it always does (b1).
- Termination: a redo runs with one more placeholder gone; it can be pending again only on placeholders that still
  stand.

check_const_pending.bn becomes this mechanism's constant case, and `VarsStale` its variable case.

### 3.4 An error belongs to a declaration (R2)

Each error records its owner: the declaration whose state the error records, if any -- `errVarTypeCycle(p)` belongs to
p, a constant definition cycle to each of its members -- or else the innermost declaration being resolved when it was
reported (`DeclStarts`, extended to types, interfaces, aliases, signatures and builds).  Undoing a pending declaration
drops its own errors, and those of the pending declarations undone with it, together with the records they stand for
(`VarsCycled`, `ConstCycleMembers`); an error of a declaration resolved on demand inside it that is not pending stands.
Dedup keeps one entry per (position, message) with every owner that reported it, and an entry goes only when it has no
owner left; `TentativeErrors` are deduped the same way.  This is the error half of the drop the archived fix did by
count.

### 3.5 V's own check, again (entry 1)

V's own search may read V's placeholder, or a result pending on V, where V's type does not need it (p3, p4, p4', g1).
Once V has its type, the errors V's first search reported are dropped, and V's declaration is checked again: its
declared type's lengths, annotations cleared, or its initializer (`recheckStaleVars`'s path).  The type found again
must be identical to V's: an internal error if not (3.1).  The head-typing branch's immediate report goes; V named in
its own head is judged by 3.2 (p1, p2, p3').

### 3.6 Constants being resolved (entry 3)

A constant K being resolved (`ConstResolving`) and read again is a placeholder the same way: the read gives POISONED
with culprit K, never NOT_KNOWN (3.1).  K's definition is a cycle iff K's own value is left without a value with
culprit K; a read elsewhere leaves the reading declaration pending on K, resolved again once K has its value.  As for a
variable (3.5), K's first-pass errors are dropped and its initializer checked again once it has its value (c1).  K's
reads of itself are judged at K, never pending on itself, so a redo cannot recreate the placeholder.
`collectConstDeps`'s pre-pass no longer reports a definition cycle; the evaluation judges it (c2).

## 4. The requirements

- R1: 3.3 (every kind, constants included, resolved again, swept).
- R2: 3.4.
- R3: a redo goes through the declaration's own path -- `resolveConstByName` with its group's iota, collection, the
  signature's definition, the instance's build -- never a detached operand.
- R4: 3.2 and 3.6.

## 5. Steps

Each step lands on its own, with the tree green.

1. Error ownership (3.4): owners on errors (a record's declaration, or the innermost being resolved), drop by owner
   with the records, dedup by owner set in both lists.  No change in behavior; unit tests of drop by owner and of dedup
   across owners, in the batch and in the REPL.
2. Pending declarations of every kind (3.3), with culprits: `varHeadType`'s third outcome, the undo list, eager
   release, the sweep, `definingFuncResult`'s clone; constants and `VarsStale` move onto it.
3. Cycles judged at V's type (3.2): V typed until its layout is prepared, culprits on types, the walk of V's type,
   free reads of another variable made pending.  e1-e5, f1, a1-a3; regressions/type-reads-const-while-var-typed
   loses its xfail.
4. V's own check again (3.5), without the head-typing report, with the bit_cast guard (3.1): p1-p5, g1, h1, b1, d1.
   The guard waits for this step: without V's own check again, `var V [len(bit_cast([4]int, V)) + 1]int` would compile.
5. Constants being resolved (3.6): entry 3's programs, c1, c2; regressions/const-read-as-arg-while-resolved loses its
   xfail.

## 6. Tests

A unit test (pkg/binate/check) for each program in section 2 and each of entry 3's, and a conformance regression for
each of p1-p5, f1, b1, d1, c1, c2 and a1.  Kept as they are: TestVarTypeCycleFuncLitBodyNotCycle, the
regressions/var-type-cycle-* errors, the spec's examples in decl.var.type-acyclic.  May name another variable of a
cycle (as the archived fix did): spec/09 178.  Each step's tests are seen to fail before it and pass after.

## 7. Risks

- The properties in 3.1.  A case where a placeholder yields a wrong value would break the identity check in 3.5.
- Pending declarations and layout waits both undo and redo; a declaration can be both (it read a layout not built and
  a placeholder).  The order of their redos, and their flushes, needs care.
- A named type undone in place: what read its layout while it was pending is pending too, so no stale layout survives,
  but instances built from it must be on the list (3.3).
- REPL batches (`TentativeMode`, `EditFloor`): an error owned by an earlier declaration must never be dropped.
- Cost: none on programs with no placeholder reads; redos only while a placeholder stands.

## 8. Scope

Step 3 is entry 2's fix and step 5 entry 3's; steps 1, 2 and 4 are entry 1's (p1-p5).  e5, f1 and a1-a3 need step 3
as well.  Recommended: one project for all three entries.

## 9. Review (2026-11-03, temp-5)

A focused adversarial review of the first version found, each probed on binate main `311d4b282` and reproduced:
the no-head free-read rule reporting another variable's cycle, which with error ownership by the innermost declaration
would have left a variable the error type with nothing reported (f1; 3.2, 3.4); `definingFuncResult` stamping a
function's result type with a check made against a placeholder (b1; 3.3); no re-check, and no termination argument, for
constants being resolved, and the dependency pre-pass's syntactic cycle (c1, c2; 3.6); a head search left without a
value falling back to a check with no head (d1; 3.3); V's own first-pass errors kept, and the bit_cast guard missing
(g1, h1; 3.1, 3.5); cycles through V's named type or instance outside V's frame (a1-a3; 3.2); the sweep's timing
(3.3); and in section 2, the lazy release, the `TentativeErrors` dedup, the VarsChecking branch's second search and
`judgeFreeReads`'s report for another variable.  This version takes each in.
