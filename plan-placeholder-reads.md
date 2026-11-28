# Plan: reads of a placeholder while a declaration's type or value is found

Status: design v3.1, 2026-11-26 (temp-5): the head rule (user 2026-11-26: "Head rule (Recommended)"), replacing v2's
design -- declarations pending on a placeholder, undone and resolved again, with errors owned by declarations -- after
a review of v2 against binate main `6ec2bc17e` found that undo without a clear edge.  v3 was reviewed the same day,
and v3.1 takes in its findings (section 9); its two questions are decided (section 8).  Step 1's first stage -- 3.1
and 3.2 at a package's top level, outside builds and function literals -- landed 2026-11-27 (binate `e0973fa62`, docs
`89d8f4c`), with entries 4 and 5; its stage for a function literal's body (fl1, fl2) landed the same day (binate
`cc76b7a07`).  Next: stage (b), instance builds.  Covers six `claude-todo.md` entries:

1. "An array length checked while its variable's type is found takes that variable's placeholder for an error: its
   errors are lost, and a variable read before its turn is a false cycle".
2. "An array length evaluated while a variable's type is found and left without a value by its placeholder is blamed
   as its type cycle, even where the variable's own length does not need it".
3. "A constant read while it is being resolved, where its value is not needed, is a false constant definition cycle".
4. "A type read in its own array length where its value is not needed is checked with no underlying: an ill-typed
   literal there is accepted" (into this plan: user 2026-11-26, "(a)").
5. "A function read in the array length of a variable its signature needs is \"undefined\"" (likewise).
6. "`len` of a variable whose type is an instance not built yet is not a constant" (found by v3's review; lay1).

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
"constant definition cycle".  The plan adds one (S2, decided; section 8): a constant's value may not depend on itself;
it depends on the value of each constant its initializer names, but for those named only in the operand of a `len`
whose operand has a head, where that head's needs count.

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
| s1a | `var u [len(h(func() int { return len(u) }).arr)]int` (`func h(f @func() int) S`) | compiles | cycle `u` (S1) |
| s1b | s1a reading `u[0]` into a bool in the literal | compiles, the error lost | cycle `u` (S1) |
| cv1 | `var V [K]int`; `const K = len(pick(func() int { var t bool = V[0]; _ = t; return 0 }))` | "cannot assign int to bool" | as today |
| cv2 | `var V = [K]int{}` with cv1's K | cycle `K` | "cannot assign int to bool" |
| k5c | `const K = len(f([3]int{}))`; `func f(y [K]int) [3]int` | "undefined: f" | K 3 |
| k5d | `var V = [len(f([3]int{}))]int{}`; `func f(y [len(V)]int) [3]int` | "undefined: f" | V `[3]int` |
| k5r | `var V [len(f([3]int{}))]int`; `func f(y [len(V)]int) [3]int` | cycle `V` | V `[3]int` |
| k5g | `func f(y [len(B[int]{}.a)]int) [3]int`; `type B[T any] struct { a [len(f([3]int{}))]int; t T }` | cycle `B` | B[int]'s a `[3]int` |
| fl1 | `var x = hs(func() int { var a [len(pick(func() int { var t bool = x.k; _ = t; return 0 }))]int; return len(a) }).f` | compiles, the error lost | "cannot assign int to bool" |
| fl2 | `var z = x` with fl1's x | compiles, the error lost | as fl1 |
| fl3 | fl1's shape, the inner literal returning `y`, `y := true` after `a`; `var y int = 1` | compiles | as today |
| fl4 | fl1's shape, the inner literal returning `w`, `w := 1` after `a`; no package w | "undefined: w" | as today |
| gnd | `type G[T any] struct { a [len(pick(func() int { return len(V.a) }))]int; t T }`; `var V G[uint8]` | cycle `G` | a `[3]int` |
| gnd2 | gnd, its literal also declaring `var t T` | cycle `G` | a `[3]int` |
| gdep | gnd's length plus `cast(int, sizeof(T))` | cycle `G` | a `[4]int` |
| ngdep | `type G struct { a [len(pick(func() int { return len(V.a) })) + 1]int }`; `var V G` | cycle `G` | a `[4]int` |
| ginst1 | `type Arr[T any] [len(V)]T`; `var V [len(Arr[int]{})]int` | "array length must be a constant integer" | cycle `Arr[int]` |
| ginst2 | `type Arr[T any] [len(pick(func() int { return len(V) }))]T`; `var V [len(Arr[int]{})]int` | "array length must be a constant integer" | V `[3]int` |
| lay1 | `type N struct { v int64; p *Box[N] }`; `type Box[T any] struct { b [len(V)]uint8; a [sizeof(T)]uint8 }`; `type Arr[T any] [sizeof(T)]uint8`; `var V Arr[N]` | "array length must be a constant integer" | valid: lay2 / lay3, other orders, print 16 16 |
| th1 | `type T [len(T{})]int` | "array length must be a constant integer" | cycle `T` |
| th2 | `func mkT() T`; `type T [len(mkT())]int` | "array length must be a constant integer" | cycle `T` |
| self1 | `func f() [len(f())]int` | "undefined: f" | as today |
| wait1 | `func mk() [sizeof(N)]uint8`; `type N struct { v int64; p *Box[N] }`; `type Box[T any] struct { a [len(V)]uint8; t *T }`; `var V [len(mk())]uint8` | compiles, 16 16 | as today |

Entry 3's own programs are x1-x3.  fl1-fl4 use `func hs(f @func() int) S` with S's fields f and k ints.  Each
"today" was run on binate main `6ec2bc17e`; the probes are in the session scratchpad (s20/pr, s20/pr2, review27,
review28).

## 3. Design

### 3.1 A `len` operand is typed by its head (entries 1-5)

Where `len`'s value is evaluated -- `checkerEnv.Len(e)` (an array length, check_constval.bn), `checkDimLenOperands`
(a generic's own check of a dependent length, eval_const_int.bn), and the `len` arm of `checkExpr` with its
`lenIsConstant` (check_builtin.bn, check_const_expr.bn) while a constant's initializer is checked for its value
(`checkConstDecl`) -- an operand e that spells its type at its head (`initHeadType`, which takes any expression) gives
the length of that type, its own array lengths evaluated as any type's, and e is checked in full later (3.2).  A
length then reads, while it is evaluated, only what its value needs: `len(pick(...))` reads pick's result type and
nothing of the literal; `len(f2(T{true}))` reads f2's result type, not T (t1); `len(f([3]int{}))` reads f's result
type alone while f's signature is being defined (`definingFuncResult`; k5).  Not in a variable's initializer outside
its array lengths: `var a = len(f(a))` has no head (`len` is not one), so a's type depends on a, as today.

A result type resolved alone (`definingFuncResult`, `definingInstanceResult`, `instanceMethodResult`) is marked while
it is resolved, and a head that reaches it again has none: `func f() [len(f())]int`, and the same through a second
function, a method or a generic, stay "undefined: f" (TestBatchSelfReadStopsReads, TestBatchVarAndFuncCycles pin it;
self1), not a recursion without end.

With no head -- an identifier, a selector, an index, a `[...]T` literal, a call of a method of anything but a package
variable, a callee that is undefined or whose result is being resolved alone -- e is checked in full at once, as today,
and depends on each variable it names outside its array lengths, a function literal's body included (S1), and on the
value of each constant it names (S2): a placeholder read anywhere in its check is that variable's type cycle (counted
as `notePlaceholderRead` counts reads; reported for the first), and a read of a constant being resolved is that
constant's cycle (`startConstByName`, as today).  So a check of a head-less operand against a placeholder never loses
an error: the variable is the error type, its cycle reported.

A head that is not an array (a slice, `len(mkSlice())`) is not a constant length, reported where it is evaluated;
never a constant left without a value and with no error (which crashes IR-gen).  What reads a type being collected or
built (3.4) applies to a head's type as to `len(V)`'s.

The bit_cast guard: `checkBitCastShapes` gives no verdict for an operand of the error type (h1).

### 3.2 The full check, later

An operand is queued while `DeclStarts` is not empty or the batch collects -- which covers collection, a constant's
resolution, an on-demand definition, pass 2's checks of package variables (`beginVarCheck`; p3's u, fl1's x) and an
instance's build -- and checked at once otherwise (a function body checked in pass 2).  An entry holds:

- the operand and its head type;
- the declaration's whole context (`enterPackageDecl`'s `declContext`: its scope, `BorrowPosKind`,
  `ExpectedFVType`, `CaptureStack`, ...), and the const-group member's iota;
- a frozen view of block scopes: each scope of the chain with its symbol count where the operand was queued, so a
  later local of the same block is not seen (fl4 stays "undefined: w"; fl3 does not find the later `y := true`);
- for an instance's build, its binding scope, its instance-naming context and `curPkgPath`.

What becomes of an entry follows the deferral records that exist for checks made in builds:

- made in a layout frame that waits, or in a resolution that waits: dropped with it -- the frame is done again and
  queues its own (wait1 would otherwise queue `mk()` with the head `[0]uint8` meanwhile, then `[16]uint8`);
- made in a provisional build: held with the build's checks (`holdAs`), given out or dropped as they are;
- made under `Speculative` or `DerivingInstanceSig` (the clones `definingInstanceResult` and `instanceMethodResult`
  resolve): dropped -- the instance's own copy is checked by `drainInstances`;
- an operand already checked (stamped) or already queued is not queued again: pass 2's `checkVarDecl` resolves a
  TypeRef again, `collectFuncDecl` a result type `definingFuncResult` resolved, `recheckDeferredVars` a variable
  checked before, and an undone on-demand definition is done again.

The queue is drained until empty (an entry's check may queue more, `DeclStarts` being pushed for it) after pass 2's
`checkDecls` and before `orderBatchVars` (`collectVarDeps` reads no array-length operand), at each site that runs
collection then pass 2: `checkPackageImpl`, `checkMain` (bnc's main program, through CheckProgram),
`checkMainPersistent`, and `checkDeclsTentative` (a REPL batch and its retries, before its decision to park on
`TentativeMissing` and before `c.Batch = nil`).  `finalizeUntypedNonConst` runs after each entry, as `checkDecls` runs
it after each declaration.  A missed site would leave operands unchecked -- their errors lost, and IR-gen's
`irgenEnv.Len` reading an unstamped `ResolvedTypeID`.  For `CheckPackageDecls` (bnlint's view of a dependency) the
queue is dropped: the package's own check checks it.

The full check's type must be identical to the head type (`types.Identical`): the head is what the spec says the type
is, and the two derive it the same way (no head is untyped; `readonly` and aliases go through `resolveTypeExpr` on both
sides; `definingFuncResult` and `collectFuncDecl` resolve in the function's file; an instance comes from the same
derivation).  A full check of the error type with an error reported is accepted (`collectLiteralTypes`' error type, a
method set's errors); anything else is an internal error.

### 3.3 The walk resolves ahead only what the head reads (entries 2, 3, 5)

`collectConstDeps` and `collectExprReads`, on `len(e)` with a head, collect only what the head reads: a composite
literal's or a cast's written type, a function literal's signature, and for a call the callee's result type -- through
a new by-name call that resolves a function's or method's result type alone, as `definingFuncResult` does, and keeps
it for `definingFuncResult` and the function's later definition.  So a chain `func f0() [len(f1())]int8`, `func f1()
[len(f2())]int8`, ... stays resolved without recursion (TestDeepBatchFuncChain), and V's length in k5r and e1 reads f's
result type, not its parameters (today a callee read defines the whole signature ahead, whose parameters read V).  A
call's arguments, a literal's elements and a cast's operand are read when the operand is checked (3.2).  So `const K =
len(f(K))` resolves nothing ahead (c2), and e2, e4, e5, x1 and x3 resolve no constant ahead.  `startConstByName`'s
cycle report stays: a read of a constant being resolved that does not wait is a read of its value (S2).

### 3.4 What reads a type being collected or built (a1, a3, F2', f2u, lay1, th1, th2, ginst1)

A variable defined on demand gets its type, not its layout: `defineVarOnDemand` and `finishBatchVar` stop preparing it,
as collection in its turn already does not (check_decl.bn); whatever reads the layout prepares it -- `sizeof`, a type
holding the variable's type by value, `bit_cast` -- and pass 2's `requireSizedType` covers IR-gen.  The detection of a
layout not built yet inside a build (`leaveDemandFrame`) moves with it.

`len` of an operand, or a head, whose type is a named type or instance being collected or built (on the stack) is that
type's cycle -- "cyclic type definition involving `S`" (a1, as F2 decided; th1, th2), Box[int]'s (a3), Arr[int]'s
(ginst1) -- never "array length must be a constant integer".  One whose type is an instance not built yet and not on
the stack waits for it (`waitForLayoutHere`), as a layout read does (lay1, which reads `Arr[N]` while N is collected).

### 3.5 `.bni` declarations (F10)

The `.bni` walk (bni_by_name_walk.bn, bni_by_name_func.bn) takes 3.1 and 3.3: a `.bni` constant `len(F(K))` is F's
result type's (q1).  Nothing of a `.bni` being built is checked later -- its operands are checked by the declaring
package's own check, the `compositeLitArrayLen` precedent -- so 3.2 does not apply there.  In the declaring package, a
`.bni` constant the `.bn` files do not define is among the merged declarations and checked as a `.bn` constant is
(3.1's constant case).

### 3.6 What may go

check_const_pending.bn's pending constants, `VarsStale` / `recheckStaleVars` and `ConstPoisoners`: with 3.1's constant
case and 3.3, a constant resolved while a placeholder stands has read it only where its value needs it, a cycle.
Today they are what make cv1 right.  The last step removes what its tests show has no use left, and keeps the rest.

## 4. Why it is sound

- Every check made against a placeholder is one the rule says the type or value depends on: a head-less `len` operand
  (S1, S2), a head-less initializer of a variable being typed (the spec), a constant's initializer read for its value
  (S2), a head read for what the head needs (a callee variable `v.m`'s type).  Each reports a cycle and leaves the
  variable or constant the error type, so nothing it found is wrong or lost.  Reads inside a build wait, and the build
  is done again.
- What a length does not need is checked once, after every type is known: no placeholder stands, so nothing is undone,
  no error is dropped, and no once-only check (a constraint latch, a held build's release) runs against a placeholder.
  That includes a length inside a function literal's body in a package variable's initializer (fl1, fl2): read at
  `LenDepth + 1`, `judgeFreeReads` skips such a read today, and the variable's type does not depend on it.
- The full check agrees with the head type, or is an internal error (3.2).
- A cycle is reported for the declaration whose type or value needs itself: the culprit is the variable or constant
  whose placeholder its own needed read meets.
- Termination: a head never re-enters a result type being resolved alone (3.1); the queue is drained by checks made
  with no placeholder standing.

## 5. Steps

Each lands on its own, with the tree green and its tests seen to fail before it and pass after.

1. 3.1 for `checkerEnv.Len` and `checkDimLenOperands`, with the result-type guard; 3.2, with every drain site, the
   drops and the once-only queueing; the bit_cast guard; S1's cycle for a head-less operand, and S1's wording of
   decl.var.type-acyclic: p1-p5, p3', g1, h1, f1, b1, d1, t1, k5, k5d, fl1, fl2 (fl3, fl4 kept), gnd, gnd2, ngdep,
   k5g, wait1 (kept), self1 (kept), s1a, s1b.  A row that also needs step 2 waits for it.
2. 3.1's constant case (`checkExpr`'s `len` arm and `lenIsConstant`), 3.3 with the by-name call for a result type
   alone, and S2's spec rule: c1, c2, cv1 (kept), cv2, k5c, k5r, e1, e2, e4, e5, x1, x2 (a cycle), x3; conformance
   regressions/const-read-as-arg-while-resolved and regressions/type-reads-const-while-var-typed lose their xfails.
3. 3.4: a1, a3, F2', f2u, lay1 (lay2, lay3 kept), th1, th2, ginst1, ginst2.
4. 3.5: q1.
5. 3.6.

## 6. Tests

A unit test (pkg/binate/check) for each program in section 2, and a conformance test for each of p1, p3, p4, b1, c1,
e1, t1, k5, F2', x1, fl1 and lay1 (run tests print the lengths; error tests pin the message).  Kept as they are:
TestVarTypeCycleFuncLitBodyNotCycle, the regressions/var-type-cycle-* errors, the spec's examples in
decl.var.type-acyclic, TestBatchSelfReadStopsReads, TestBatchVarAndFuncCycles, TestDeepBatchFuncChain.  A later check's
errors come after pass 2's: tests pinning an order among errors are found by the unit and conformance runs.

## 7. Risks

- The later check's context (3.2): the archived fix failed on a const group's iota.  The entry carries the whole
  `declContext`, the frozen block scopes and an instance's binding.
- A head the later check does not confirm: an internal error, loud; the head rule is the spec's.
- Moving layout preparation out of on-demand definitions (3.4) changes when a layout wait is found inside a build.
- Error order changes for errors in `len` operands found while declarations are resolved.

## 8. Questions (decided 2026-11-26)

- S1: a `len` operand with no head depends on each variable it names outside its array lengths, a function literal's
  body included, as an initializer with no head does; decl.var.type-acyclic's last sentence is reworded to say so.  So
  `var u [len(h(func() int { return len(u) }).arr)]int` (for `func h(f @func() int) S`) is a type cycle (s1a).  The
  alternative, offered and declined, was to count a selector or index of a head (`h(...).arr`) as a head for `len`
  operands, which would make that rule differ from the initializer rule (F1).
- S2: a rule for a constant's definition cycle: a constant's value may not depend on itself; it depends on the value
  of each constant its initializer names, but for those named only in the operand of a `len` whose operand has a head
  (where only that head's needs count, as for a variable: `const K = len(f(K))` is 3).  And a head-less initializer's
  type depends on the value of each constant it names, as on each variable's type -- an edit of decl.var.type-acyclic
  itself -- so x2 is K's cycle.  The alternative, declined: x2 valid, D's check using K's type without its value.

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

v3 (2026-11-26, against binate main `6ec2bc17e`): no flaw in the head rule, but gaps v3.1 takes in: a constant's
initializer is checked by `checkExpr`, never through `checkerEnv.Len` (B1: 3.1's constant case); S2's first wording
contradicted its own examples (B2); a head re-entering a result type being resolved alone would recurse without end
(B3: the guard); builds queue too, rather than check at once (B4); an entry made in a frame that waits, or an operand
queued twice, would fail the identity check (B5: wait1); lengths in a function literal's body in a package variable's
initializer lose errors today, and the queue needs frozen block scopes (B6: fl1-fl4); a callee read ahead defines its
whole signature (B7: k5r, e1); `len` of an instance not built yet must wait, and heads read types being collected too
(B8: lay1, th1, th2, ginst1); every drain site and the whole `declContext` (B9); the speculative clones' entries
(B10); and Binate has no type-argument inference (B11).

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

Decided 2026-11-26, on section 8's questions: S1, user: "Yes, a cycle (Recommended)" -- a head-less `len` operand
depends on each variable it names, a function literal's body included, and decl.var.type-acyclic's last sentence is
reworded to say so; S2, user: "x2 is a cycle (Recommended)" -- the spec gets a rule for a constant's definition
cycle, and a head-less initializer's type depends on the value of each constant it names (x2 is K's cycle, as e3 is
V's under F1).  Probed then on binate main `6ec2bc17e`: `var u [len(h(func() int { return len(u) }).arr)]int` (for
`func h(f @func() int) S`) compiles today and is a cycle under S1, and its variant reading `u[0]` into a bool
compiles, the error lost.
