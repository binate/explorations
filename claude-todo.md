# Binate TODO

Tracks open work items, grouped by the subsystem / root cause they touch.
Completed items live in [claude-todo-done.md](claude-todo-done.md).

## CRITICAL

### Constraint calls through `impl *P` / `impl @M` give wrong results — needs a spec decision — 🔴 NEEDS DECISION (found 2026-09-30, work-4, review of design B's per-instance checking; reproduced on BUILDER bnc-0.0.16; pre-existing)

```
type P struct { v int }
impl *P : lang.Comparable
func (s *P) Compare(o P) int { return s.v - o.v }   // Self is P (iface.self); `o *P` is rejected
type PP = *P
func eq[T lang.Comparable](a T, b T) bool { return a.Compare(b) == 0 }
// eq[PP](&p1, &p2) with p1.v == p2.v == 3
```
- **bnc-0.0.16 (LLVM and native):** `eq[PP]` and the `@M` analogue print `false false` for equal values, while the direct calls return 0.  This is silent wrong code.
- **Since design B's per-instance body check (binate `fe95d7de8`):** rejected, but with a misleading error inside the generic body: `cannot assign PP to P (in eq[PP], instantiated at …)`.

`*P` satisfies the constraint, and the abstract check passes `b : T` as Self; with `T = *P`, the method's `o P` doesn't take a `*P`.  Two directions, which need a spec call:
- `*P` does not satisfy `Comparable`, so the error is at the instantiation.
- Constraint-call lowering reconciles Self with the receiver kind (passes `*b`).

From a review on 2026-10-16 (work-6): reconciling at lowering cannot work where Self is inside a type argument
(`OBox[*P]` and `OBox[P]` differ in layout, and no conversion exists), as already for `*[]Self`; making Self the
receiver type (`*P` for `impl *P`) would make the rule that Self satisfies the constraints its interface extends
exact for pointer impls too.

The checker already answers which receiver shape satisfies a constraint two ways (review, 2026-10-16, work-6;
reproduced): a plain impl's receiver must equal the type argument (`impl *P : lang.Orderable` does not make `P`
satisfy it), while a parameterized impl's receiver is matched with the call-site receiver smoothing
(genericImplSatisfies uses receiverAssignable).  `impl *W[K] : lang.Orderable` (`func (w *W[K]) Compare(o W[K])
int`) makes `W[int]` satisfy it, and `g[W[int]](w)` with `g[U lang.Orderable](u U) int { return u.Compare(u) }`
runs.  (It also lets `impl W[K]` make `@W[int]` satisfy it, which neither reading allows: the MAJOR entry on
`g[@W[int]]`.)  The spec takes both sides too: gen.impl.generic-recv says `impl
*Cursor[T] : Iterator[T]` asserts that `Cursor[T]` satisfies `Iterator[T]`.  Text to revise with the decision:
§11.9 iface.self ("substituted by the implementing named type"), §11.3 iface.impl.coverage ("with `Self`
substituted by `R`'s named type"), gen.impl.generic-recv, gen.satisfy's receiver clause, gen.mono.constraint-call,
iface.self.constraint's "as a type parameter whose constraint is `I` would", and the Provisional notes under §11.3
iface.impl.form and §12.1 gen.impl.generic-recv (docs `69e9d03`).

With `Self` in a type argument, an `impl *P : I` cannot be implemented at all (2026-10-17, work-6): for `interface
I : lang.Orderable { Put(b *OBox[Self]) int }` and `type OBox[T lang.Orderable]`, the method spelled `Put(b
*OBox[P])` (Self is P) is rejected, since P does not satisfy Orderable (the impl is for *P), and spelled `Put(b
*OBox[*P])` it does not match I.

## MAJOR

### An aggregate with no word alignment copied whole through pointers is staged in a copy of its size on the stack — a 16 MiB struct of bytes overflows it; 1718's 2 GiB one is why the LLVM x86-64 lanes are red — 🟡 MAJOR, IN PROGRESS (claimed 2026-10-29, work-1; user: "Fix it now"; found 2026-10-29, work-1, root-causing conformance 1718's CI failure; pre-existing)

`*d = *s` with `type Page struct { b [0x1000000]uint8 }` and both on the heap crashes (SIGSEGV) on the LLVM and
native backends and is "runtime error: stack overflow" in the VM; 4 MiB works, and so does a 16 MiB struct of
int32.  The load of `*s` is copied into a frame region (an LLVM `alloca`; in the VM, its stack) the size of the
aggregate, then that copy into `*d`.  Root cause (compiled modes): native/common's AggLoadElision
(common_aggload_elision.bn) returns AGG_LOAD_COPIED for any aggregate with AlignOf() < 4 before looking at its
uses, to keep word-wise consumers (call-argument / return marshals) off a possibly unaligned source on
strict-align arm32 — which also catches a load whose one use is a whole store (storedWhole), the case it
otherwise classifies AGG_LOAD_IN_PLACE_OVERLAPPING and the backends copy as memmove does.  The LLVM backend and
the native backends both ask it.  The VM copies EVERY aggregate load into a frame region of its own
(BC_LOAD_AGGREGATE, vm/lower_memory.bn), whatever its alignment: a 16 MiB struct of int32 copied the same way
overflows its 8 MiB stack too.  Conformance
1718_offsets_past_2gib's copyBytes (`*d = *s`, a 2 GiB struct of bytes) gets a 2 GiB frame, which clang rejects on
x86-64 ("stack frame size (2147483704) exceeds limit (2147483647)"): the LLVM lanes (builder-comp, -comp-comp,
-comp-comp-comp) have been red on main since binate `650f83fc5` (work-3's note under the int64-layout entry).
Proposed fix: classify a whole store of a sub-word-aligned aggregate AGG_LOAD_IN_PLACE_OVERLAPPING too, once
each backend's overlapping store copies a sub-word-aligned source correctly (LLVM: memmove; x64 / aarch64:
unaligned access is fine; arm32: the memmove path must go byte by byte for such a source), so no region is
planned; and the same for the VM's load + store.  Test: conformance 1722_copy_large_byte_struct (xfail.all; bare
metal xfailed for its 4 MiB heap arena), not yet landed.

### A method copied onto an instance while a layout its signature reads is still built keeps the zero length it was resolved with — 🟡 IN PROGRESS (claimed 2026-10-29, work-7/session; user: "let's fix that in the commit right after; you can choose which goes first (maybe this)") (MAJOR: a valid program compiled with a wrong array type, silently; found 2026-10-29, work-7, probing the method-copy wait; reproduced on main; pre-existing)

`type Bx[T any] struct { a [len(hv.m())]uint8; t T }`, `type H[T any] struct { z T }`, `var hv H[int64]`, `func (h
*H[T]) m() [sizeof(W[T])]uint8 { … }`, `type W[T any] struct { v T; p *Bx[T] }`, `var Q [len(W[int64]{}.p.a)]uint8`
compiles, and the program prints 0 for `len(hv.m())` and `len(Q)` while `sizeof(W[int64])` prints 16; the same with
Q's length as a struct field's or a function's result type.  The pinned BUILDER (bnc-0.0.17-pre3) rejects the program
("array length must be a constant integer").  Traced: collecting Q builds W[int64] and Bx[int64], whose build selects
hv.m(), and m is copied onto H[int64] on demand (attachInstanceMethods → copyPlaceholderMethods,
check/check_generic_backfill.bn).  Likely its signature takes sizeof(W[int64]) while W[int64] is still being built, a
read that waits (check/check_layout_wait.bn) and leaves the length 0, and the copy, in no frame that is done again,
keeps m with `[0]uint8`, which Bx[int64], built again, reads.  Related: the entry on a waiting build that still builds
the instances it names from the length it could not read.  Fix: a method whose copied signature makes a frame wait is
not kept — taken back as a definition on demand is (enterDemandFrame, undoBatchMethods), to be copied again when next
selected or back-filled — with a conformance test printing the lengths.

### The checker accepts a constant cycle through a generic instance's field, and IR-gen panics — 🔴 OPEN (MAJOR: an internal-error crash on an invalid program; found 2026-10-28, work-7, review of the constant form of the abstract-declaration wait; reproduced on main, also before binate `f7e99b80b`; pre-existing)

`type Bx[T any] struct { a [S]uint8; t T }`, `var gv Bx[int64]`, `const S = len(gv.a)` (in either order) passes the
type checker, and bnc then stops with "panic: internal error: IR generation cannot evaluate a constant the type
checker accepted".  It is a cycle — S is the length of gv.a, whose length is S — and the same without generics
(`type B struct { a [S]uint8; t int }`, `var gv B`) is "cyclic type definition involving `B`".  Root cause not traced:
gv's type builds Bx[int64], whose declaration (resolved with its type parameters abstract) and build read S on
demand, whose `len(gv.a)` reads a field of Bx[int64] while it is built (a member read of a type not built yet
waits, check/check_member_read.bn; layout frames, check/check_layout_wait.bn); somewhere on the last, no-wait round
that read or S's resolution is dropped without an error, leaving S with no value.  Fix: find where the cycle's error
is lost and report it (one error), with a conformance error test (it panics today) and a unit test.

### CI red: the slow lanes hit their job time limits — unit int-int shards, conformance int-int and comp-comp-comp — 🟡 IN PROGRESS on option (a) (claimed 2026-10-28, work-3; user: "both", then "(a)") (filed 2026-10-28, work-3)

Unit run 40643666280 (at f7e99b80b): builder-comp-int-int shards 3, 5 and 6 of 12 cancelled at the 45-minute limit.
Conformance run 40645707398 (at 5e54b7554): all six builder-comp-int-int shards and builder-comp-comp-comp cancelled at
their limits.

**Findings (2026-10-28, work-3): the int-int lanes' fixed per-test cost has risen ~2.5-3x since late September; the
suites grew ~20% in the same span.**
- Conformance int-int (6 shards, 30-min cap; sharded by `562b4cbba` to fit): shards took 15-23 min on 09-15 and
  17-29 min through 09-25; some hit the cap from 09-26, and in every run sampled since 09-29 every shard has (one
  29-min success on 09-29), so the lane has not completed for a month.  Per test (shard 1's first 40, from the job
  logs): ~2.0-2.5 s through 09-28, 4.0 s on 09-30, ~5.0 s from 10-11, 6.4 s in run 40776545497 (10-28), where
  001_hello alone takes 6 s.  Conformance entries grew 2553 -> 3068 (+20%) over 09-28 -> 10-28.  A shard now gets
  through 230-400 tests before the cap; in September one ran ~570 in 20 min.
- Unit int-int (12 shards, 45-min cap): shards took 3-13 min in September (one outlier at 27-30) and 8-45 min by
  10-14, the same factor.
- The per-test cost is the OUTER bni loading cmd/bni: the int-int runner has it parse, check, IR-gen, optimize and
  lower cmd/bni's whole import cone for every test.  Locally an int-int 001_hello takes 2.25 s of CPU, 91% of it in
  Interp.LoadProgram and 8% running the program; a plain int run takes 0.01 s.  At 19772cd6b (09-27) it took
  1.24 s.  cmd/bni's cone grew 92k -> 121k lines (62 -> 66 packages), 1.31x, while the load's phases grew 2-5x
  (macOS `sample`, 1-ms samples, 09-27 -> HEAD): IR-gen 258 -> 669 (overlayFileImports 86 -> 281), VM lowering
  381 -> 1050 (IR passes 282 -> 578; lowerFunc 78 -> 371, of which assignSlots, new in e38f2cbf0 on 10-24, is
  155), import / struct registration 108 -> 415 (RegisterPkgImports, registerAllStructTypes).  Parse and check
  are not comparable: the 09-27 sample missed the start of the run.  Turning every IR pass off saves ~15%.
- Not looked at: comp-comp-comp (one 30-min shard; 20-30 min since late September, cancelled about half the
  time).  CI's gen1 build also went from 76-89 s to 180-212 s over the same span.

**Progress on (a)** (int-int conformance/001_hello, instructions retired, perf/ab-binaries.sh, median of 5):
- binate `331ebd2ea` (landed 2026-10-29): irgen caches overlayFileImports' alias maps per base and import list
  instead of rebuilding one per declaration per pass — 36.76G -> 31.40G (-14.6%).
- binate `7502003fd` (landed 2026-10-29): mem2reg reads each alloca's uses from one use index, and its stacks and
  lists, and the dominator tree's postorder walk and IteratedDF worklist, are vecs — 32.43G -> 31.35G (-3.3%).
  (The tree itself grew between the two: the base went 31.40G -> 32.43G with a day of upstream commits.)
- binate `140629e75` (landed 2026-10-29): load forwarding coalesces all slices' extracts together, and applyRLE
  places each materializing load through a per-block position map — 31.58G -> 31.00G (-1.9%).
- binate `9bbe8a2f7` (landed 2026-10-29): Module.Structs grows through a vec builder and is looked up through a
  name index (ir.RegisterModuleStruct / StructIndexLookup) — 31.12G -> 30.30G (-2.6%).
- Next: re-profile; known candidates are irbuild.BlockIndexIn's linear scan (O(blocks^2) in buildSuccsPreds,
  pruneUnreachableBlocks, assertPhiEntriesArePreds), the append-built Preds / dominator children / DF sets in
  iropt/dom.bn, the VM's slot assignment (assignSlots), and the fault-pad cleanup emission in irgen.

Options (decision needed): (a) profile and fix the load pipeline's superlinear phases (cmd/bni's load is also
bni's start-up cost for every user); (b) more shards or a longer cap for the int-int lanes
(.github/workflows/{conformance,unit}-tests.yml); (c) stop re-loading cmd/bni for every test in int-int (one outer
bni running many inner programs) -- a harness change that alters what the lane exercises.

### CI red: bare-metal unit-test binaries have outgrown the 16 MiB RAM region — 🔴 NEEDS DECISION, RELEASE BLOCKER (filed 2026-10-20, work-3, CI triage)

`builder-comp_arm32_baremetal` runs each package's test binary in QEMU `virt` with `-m 16M`, linked by
runtime/baremetal_arm32/baremetal.ld (`RAM LENGTH = 16M`: code, data, `.bss` — including the 4 MiB heap arena,
rt_baremetal.bn ARENA_BYTES — then the stack growing down from the top).  Several packages no longer fit:
`native/arm32`'s test binary fails to link (".bss will not fit in region RAM: overflowed by 844365 bytes",
735013 a few hours earlier); `irgen`'s links with `.text` 11.9 MB + `.bss` 4.2 MB, leaving ~600 KB of stack, and
dies with no output (reproduced locally); `asm/parse` and `check` abort with "rt.RawAlloc: arena exhausted" (an
abort since binate `aa166c839`, which stopped the allocator wrapping).  Options: give test runs more RAM
(QEMU `virt` has 1 GiB; the runner's `-m` and the linker script's LENGTH move together) and a larger arena, or
shard / shrink the test packages, or xfail them on bare metal.  The test binaries grow with every test added,
so a one-off trim will not last.
**Correction (2026-10-20, work-3): `irgen`'s silent failure is NOT the RAM limit.**  `irgen`'s and
`asm/assemble`'s bare-metal test binaries read their command line (semihosting 0x15) and exit 1 (0x20) with
no CPU fault — and with no output, because ALL bare-metal test-runner output is dropped: the generated runner
(cmd/bnc/gen_test_runner.bn) prints RUN / PASS / FAIL through pkg/std/fmt -> os.Stdout, and
impls/stdlib/pkg/std/os/os_baremetal.bn's Stdout is an empty File whose Write always fails (errNoFS).  So
those two packages have ordinary FAILING TESTS nobody can see (the CI log shows `()` for every bare-metal
package's count for the same reason).  NEEDS DECISION: route bare-metal os.Stdout / Stderr to the semihosting
console (as pkg/builtins/testing/sys already does), or have the runner print through pkg/builtins/testing
(its source says it must use the real pkg/std/fmt).  Only after that can the failing tests be found; the
`native/arm32` link overflow and the arena exhaustion remain size problems.

### A parameterized impl lets a managed pointer to its instance satisfy a constraint — `g[@W[int]]` is accepted through `impl W[K]` — 🔴 OPEN MAJOR (found 2026-10-16, work-6, review of the spec status cleanup; split out of the pointer-impl decision entry 2026-10-17; reproduced; pre-existing)

With `impl W[K] : lang.Orderable` (`func (w W[K]) Compare(o W[K]) int`) and `func g[U lang.Orderable](u U) int {
return 7 }`, `g[@W[int]](w)` is accepted and prints 7, although no reading of the receiver shape lets `impl W[K]`
make `@W[int]` satisfy Orderable; the plain counterpart, `impl P : lang.Orderable` with `g[@P](p)`, is rejected
("type argument @P does not satisfy constraint Orderable").  With a constraint call in g (`u.Compare(u)`) the
program is rejected inside the instance instead, with a misleading error ("cannot assign @W[int] to W[int] (in
g[@W[int]], …)").  Cause: typeSatisfiesConstraint (check/check_generic.bn) checks a parameterized impl with
genericImplSatisfies (check/types_assignable_iface.bn), the interface-value construction helper, which matches the
receiver with the construction site's smoothing (receiverAssignable), so `@W[int]` matches `W[K]`.  Fix: match a
type argument against a parameterized impl's substituted receiver as the plain arm matches a plain impl's
(Identical), adjusted to whatever the pointer-impl decision above settles for `impl *W[K]`.  Covered by
conformance spec/12-generics/119_err_satisfy_generic_impl_managed_ptr (binate `0051d4792`, expected to fail in every
mode).

### IR-gen spells every type argument out in an instance's name — instances whose arguments share a type get exponentially long names — 🔴 NEEDS DECISION (found 2026-10-26, work-7, testing the abstract-instance change's shared-argument walk; reproduced on main; pre-existing)

- **Symptom:** `type Pair[A any, B any] struct { a *A; b *B }`, `type P0 = Pair[int, int]`, `type Pk = Pair[P(k-1), P(k-1)]` up to n, and `var x Pn`, with no type error, compiles in 2.6 s at n = 18, 31 s at 22, and more than 120 s at 24 (main at `3b3b6ef2d`); the time is in IR-gen, after the checker.
- **Root cause (from a sample; the name length by reasoning):** IR-gen's `registerModuleTypeDecl` → `ensureInstantiatedType` → `ensureInstantiatedStruct` (recursing), with the time in `instantiationMangledName` → `mangle.LpWriteArgList` / `lpTypeArgNamed`: the mangled name of `Pair[P(k-1), P(k-1)]` writes its argument's name twice, so the name of `Pn` is 2^n long.
- **Needs a decision:** the mangling scheme — a backreference to an argument already written in the name (as the Itanium ABI's substitutions do), or a hash for a long argument list.  Whether `ensureInstantiatedStruct`'s recursion is exponential apart from the name is still to check.

### Postponed abstract instances: three small gaps — a size check that does not enter them, a REPL rollback that keeps a build, and a possible missed opaque check — 🔴 OPEN (minor; found 2026-10-26, work-7, reviews of the abstract-instance change, binate `3fc098581`)

- `partTooLarge` does not enter a postponed instance, so it can miss a too-large part reached only through one —
  something like `[1 << 62]U`, U := `*T`, in a generic that is never instantiated.
- REPL: a postponed instance that existed before a tentative check and is built inside it keeps that build after
  the check is rolled back.  Whether that can change a later answer is not established.
- Unverified (a review's low-confidence report; not reproduced): an instance still waiting for its layout when
  checkValueEmbedding runs (before finishDeclBatch) is not counted as opaque at the declaration, and since
  pointeeEmbedsOpaque stops at an instance whose type arguments cannot bring an opaque type in, a later `*G[...]`
  would not judge it again — an opaque type held by value could be accepted.  Needs a repro attempt first.

### `len` of an array variable whose type is inferred is taken as a read — a false initialization cycle — 🟡 IN PROGRESS (claimed 2026-10-29, temp-5/session, self-drive; minor; found 2026-10-28, work-5, probing the placeholder MAJOR's part 2; reproduced; pre-existing)

`var a = len(b)` with `var b = [3]int{a, 0, 0}` is rejected "variable initialization cycle involving `a`", though
`len` of an array does not read it (builtin.len); with `var b [3]int = [3]int{a, 0, 0}` it compiles
(TestVarInitLenOfArrayNotARead covers only declared types).  `var a = len(b.arr)` with `var b = mk(a)` is rejected
the same way.  The initialization order (resolveTopLevelVarOrder, check/check_var_resolve.bn) is computed at the end
of collection, before pass 2 checks the initializers, so declaredType finds no type for b (its symbol is not defined
yet, and its initializer has no ResolvedTypeID) and `len`'s operand is walked as a read.  Fix (to decide): give
declaredType a variable's head type (varHeadType), or compute the order after pass 2; a unit test and a conformance
test.

### A variable taking its own length in a call its type comes through is called an initialization cycle, not a type cycle — 🔴 OPEN (minor: diagnostic; found 2026-10-29, temp-5, review of the init-order-after-pass-2 change; reproduced)

`var d = hi(len(d)).arr` (`func hi(n int) S`, S holding an `arr [3]int`) is "variable initialization cycle involving
`d`".  d's initializer spells no type at its head and names d outside an array length, so its type depends on
itself (§9.2 decl.var.type-acyclic); and `len(d)` reads d only if d is not an array.  d's check reads its own
placeholder there, so `len(d)`'s operand is the error type and d's own type comes out the error type too: the
initialization order, found after pass 2 (check/check_var_resolve.bn), cannot tell that d would be an array, counts
`len(d)` as a read, and reports the cycle of reads.  The same with another variable between: `var b = hi(a).arr`
and `var a = len(b) + 0` (a's `len(b)` reads b's placeholder) is "variable initialization cycle involving `b`" (the
unit test TestVarPlaceholderReadThroughLenIsTypeCycle pins only that one cycle is reported).  Taking the variable's
final type for such a read instead does not work: when a type cycle is reported the variable's type becomes the
error type, and the views during and after pass 2 then disagree (a double report).  Fix: when the only reads that
make a variable's cycle are `len` of placeholders read while its type is found, report the type cycle
(judgeSelfReads, check/check_var_free_reads.bn); unit tests of both programs.

### An array length checked while its variable's type is found takes that variable's placeholder for an error: its errors are lost, and a variable read before its turn is a false cycle — 🟡 IN PROGRESS (claimed 2026-10-29, temp-5/session, self-drive; minor: a valid program rejected, and ill-typed code that is never compiled accepted; found 2026-10-28, work-5, review of the placeholder MAJOR's part 2; reproduced; the false cycle since binate `4fa186628`, the rest pre-existing)

`var u = [len(pick(func() int { return len(u) }))]int{}` (`func pick(f @func() int) [3]int`) compiles, as
TestVarTypeCycleFuncLitBodyNotCycle pins (the length is pick's result's, and the literal is never compiled), but
with `var z = u` before it is "variable type cycle involving `u`".  Read before its turn, u's head type is found on
demand (varHeadType), and u named inside it is reported at once (batchVarOnDemand's head-typing branch, since binate
`4fa186628`); pass 2's check of u instead finds its head type again inside the check (the VarsChecking branch), where
the literal's read is a placeholder whose error does not reach the length.  Likewise `var u = [len(w)]int{}` with
`var w = [len(pick(func() int { return len(u) }))]int{}` is "variable type cycle" in either order, though w's length
does not need u.

And errors in an array length's operand checked against a placeholder are lost: `var u = wrap([len(pick(func() int
{ var t bool = u[0]; _ = t; return 0 }))]int{}).arr` (`func wrap(x [3]int) S`) compiles, an int assigned to a bool
in a literal that is never compiled; and `var z = u` with `var u = [len(pick(func() int { var t bool = u[0]; _ = t;
return 0 }))]int{}` is "variable type cycle involving `u`" (by the report at once above), where u alone gives
"cannot assign int to bool".  The operand's type is kept (checkerEnv.Len reuses its ResolvedTypeID), so pass 2 does
not check it again.

Tried (not landed): finding the head's type again inside the on-demand head typing, as pass 2 does.  It fixed both,
but `func f() int { return len(u) }` with `var u = [len(w)]int{5, 6, 7}` and `var w = mk(u).arr` then blamed w and
left u a `[0]int` (follow-on "too many elements in array literal"), and `var w = [len(pick(func() int { return
len(u) }))]int{}` with `var u = [len(w)]int{}` stayed a cycle.  Fix (to design): once the variable has its type,
check again the array-length operands that read its placeholder (their kept types dropped), reporting their errors,
rather than reporting a cycle at once; unit tests of the programs above.

### A variable type cycle through a function's signature, met while types are collected, says "declare its type" — 🟡 IN PROGRESS (claimed 2026-10-29, temp-5/session, self-drive; minor: diagnostic; found 2026-10-26, work-5, review of the variable type-cycle fix; reproduced; pre-existing)

`type B [len(t)]uint8`, `var t = mk()`, `func mk() [len(t)]int` is rejected, but with "the type of `t` is needed
while the package's types are collected ..., but its initializer uses declarations not collected yet: declare its
type"; t's type is mk's result, whose length reads t, so the error is "variable type cycle involving `t`".  B's
length checks t on demand while types are collected, and t's call defines mk on demand
(batchFuncOnDemand), whose signature reads t while t is checked; the check of t is deferred, and when it is checked
again (recheckDeferredVars, check/check_decl_batch_var.bn) nothing is reported, so the deferred variable gets
errVarNeededDuringCollection.  Why the check again does not meet the cycle is not traced.  A cycle through a
variable checked while types are collected gives the same follow-on: `type A [len(u)]int`, `var u = [len(w)]int{5,
6, 7}`, `var w = mk(u).arr` with `func mk(p [3]int) S` (S holding an `arr [3]int`) reports the variable type cycle
and then "declare its type" for one of u and w (found 2026-10-28, work-5, review of the head-typing change).  Fix:
trace it and report the cycle there alone; unit tests of both programs.

### A variable whose head calls a generic function's instance, or a method of a generic type's instance, whose signature reads the variable is rejected — 🔴 OPEN (minor: valid programs rejected; found 2026-10-29, temp-5, review of the head-call result-type change; reproduced; pre-existing)

`var t = mk[int](nil)` with `func mk[T any](p *([len(t)]int)) [3]int` is "variable type cycle involving `t`", and
`var v B[int]; var t = v.m(nil)` with `func (r B[T]) m(p *([len(t)]int)) [3]int` is "declare its type".  By §9.2
decl.var.type-acyclic t's type is the instance's one result, `[3]int`, which does not depend on t; only the
parameter does.  calleeResult (check/check_var_placeholder.bn) takes a non-generic function's or method's result
type alone while its signature is not defined yet (definingFuncResult), but an instance's result is found by
instantiating the whole signature (checkInstantiateOrIndex / the instance's method set), whose parameter reads t.
Fix: resolve an instance's result type alone, with its type arguments bound, before its signature; unit tests of
both programs.

### The checker's unit tests ignore parse errors, so a test whose program does not parse passes on what is left — 🟡 IN PROGRESS (claimed 2026-10-29, temp-5/session, self-drive; minor: test accuracy; found 2026-10-28, work-5, writing the head-typing tests)

checkSrc (check/types_test.bn) and the other helpers that parse a test's program (`p.ParseFile()` in
bni_scope_test.bn, check_const_bare_test.bn, check_decl_func_test.bn and more) never read `p.Errors()`, so a
program with a syntax error is checked as the parser recovered it.  Two rewritten tests in
check_decl_batch_var_test.bn wrote a pointer to an array as `*[3]int32` (raw-slice sugar, a parse error; the
spelling is `*([3]int32)`) and still passed, expecting one checker error.  Fix: make the helpers fail a test whose
program does not parse (a test that means to pass unparsable source says so, through a helper of its own), then fix
the tests that turn out not to parse.

### Other packages' unit tests may also check what the parser recovered from a program that does not parse — 🔴 OPEN (minor: test accuracy; found 2026-10-29, temp-5, fixing the checker's tests)

The checker's tests now stop on a program that does not parse (mustParse, check/types_test.bn).  Other packages'
tests parse programs too, and some never read `p.Errors()`: `ParseFile()` sites in irgen (49), vm (39), lint (10),
format (10), codegen (6), loader (5), interp (4), repl (3), native/x64, native/arm32, native/aarch64 and buildcfg
(1 each), plus e2e host programs (e2e/xmiface.sh, e2e/xmhfa.sh).  A test whose program does not parse then tests
what the parser recovered (eight checker tests did, five of them a parser rejection checked as the checker's).
Fix: per package, route the parses through a helper that fails on a parse error, as the checker's tests do, and
fix the tests that turn out not to parse.

### An importer's `sizeof` / `alignof` of a type its `.bni` declares later crashes the compiler — 🔴 OPEN MAJOR (found 2026-10-17, work-5, review of the `.bni` constant `len` change; reproduced; pre-existing)

`const Z uint = sizeof(S)` followed by `type S struct { x int64; y int64 }` in `q.bni`, with an importer's
`var a [q.Z]int8`, panics: "internal error: an array length the type checker did not resolve reached IR
generation" (also `alignof(S)` and `sizeof([2]S)`; the single-package form compiles and prints 16).  The
importer evaluates the `.bni` constant while building the `.bni` scope (BuildingIfaceScope), when S is still a
pass-1 placeholder: checkerEnv.Type (check/check_constval.bn) sees it as embedding an opaque type and returns
POISONED without reporting anything — a status that means "its error is already reported" — so the importer's
`[q.Z]` reports nothing and the unresolved length reaches IR-gen.  Fix: in that context return NOT_CONST, never a
silent POISONED (the importer then rejects `[q.Z]` "array length must be a constant integer"), and properly make a
`.bni` constant's evaluation see the `.bni`'s later types (resolve them first, as collectConstDeps does for later
constants), so the program compiles.  A conformance test for the crash (the program above) and for a later
named array type under `len` (`const R int = len(Row{})` before `type Row [8]int16`, also rejected today).

### Instance-check diagnostics: a second instance's violation is hidden, and an instance formed through a generic's field is placed at that field — 🔴 OPEN (minor: diagnostics; found 2026-10-16, work-5, review of the generic-type method-signature sizing; pre-existing)

contextualizeInstanceErrors drops an error another instance already reported at the same position, so with
`Bx[Opaque]` and `Bx[Op2]` (both bad) only the first is named.  And an instance formed through a generic struct's
field (`Outer[Opaque]` with a field `b Bx[T]`) is reported as instantiated at that field inside the generic, with
no `via Outer[Opaque]`; through a generic function the `via` chain is right.  The program is rejected either way.
Fix: keep one error per instance (or name every instance at a shared position), and record the root
instantiation for an instance formed through a field.

### Does populateInstantiatedInterface's speculative (`userFacing` false) path still have a valid-program case? — 🔴 OPEN (cleanup question; found 2026-10-16, work-5, review of the generic-type method-signature sizing)

The split exists so an impl-match probe that instantiates an interface (`Consumer[Wrap[Op]]` probing `Wrap[Op]`'s
impl `Sink[K]`) does not report Sink[Op]'s by-value method on valid code.  Now that a generic type's instance
methods are sized, a `Wrap[Op]` whose impl names `Sink[Op]` with a by-value `Op` method has the same method itself
and is rejected, so the scenario TestSpeculativeIfaceInstNoOpaqueByValueLeak was written for no longer has a valid
form.  Find whether any valid program still reaches the speculative path with a method the user-facing check
would reject; if none, the split (and requireInstantiatedIfaceMethodsSized's re-check) can go.

### int64 layout, stages 2-6: IR, LLVM codegen, native backends, VM — a 32-bit-hosted compiler fails loud on a > 2 GB type for a 64-bit target until they carry int64 — 🟡 IN PROGRESS (claimed 2026-10-15, work-1; resumed 2026-10-18 after the MAJORs and the aggregate-copy items, per
the user's 2026-10-15 order: "MAJORs first, then back"; the CRITICAL's continuation, plan-int64-layout.md)

Stage 1 landed (binate `56c30e545`, docs `dc6444c`): types lays out in int64 and the checker rejects a type too
large for the target; the rest of the compiler still narrows to the host's int through the transitional
accessors (SizeOf / FieldOffset / TrailingPadding / ArrayLenHost / HostInt), which fail loud on a 32-bit host
for a type of 2^31 bytes or more.  Stage 2 landed (binate `addbb15a2`, 2026-10-18): ir / irgen / irbuild / irdata /
iropt / types/abi read layout in int64, and the shared records (irdata's data terms, type-info records, retbuf sizes)
are int64.  Stage 3 landed (binate `651ea270e`, 2026-10-18): the LLVM backend writes sizes and offsets from the
int64 API.  Stage 4a / 4b landed (binate `73d8d968f`, `845a85f52`, 2026-10-18): the shared native layer compares
sizes with Size(); native aa64 materializes an add / sub immediate past 16 MiB.  Stage 4f-1 landed (binate
`25ffa73f0`): native zero globals go to bss, which the assembler counts without holding.  Stage 4d-1 landed
(binate `8d5ed266a`): native frame tables in int64, a frame past the target's address space a compile error
(spec §21.4 row, docs `1501abe`; user decision 2026-10-18 "Address space").  The LLVM frame check landed (binate
`1246f0e68`, `3279fc9ac`, `519dd78fa`).  Stage 4d-2 landed (binate `74f8e652e`): native x64 frame slots and incoming
arguments past disp32.  Stage 4d-3 landed (binate `b108193b0`): native aarch64 frame offsets in int64 through the
assembler.  Stage 4d-4 landed (binate `f2264bbe4`): native arm32.  Stage 4d-5 landed (binate `842db9d45`): the
RegMap's frame lookups return int64 (the int forms and FrameInt gone); stage 4d is done.  Remaining
(plan-int64-layout.md): 4e field / element offsets and copy sizes DONE (binate `87830f726` common, `650f83fc5` x64, `b771afffa` aarch64, `3beb3cc38` arm32); 4c callconv's stack layout (in progress 2026-10-27, work-1; with outgoing stack
arguments past 2 GiB, which native x64 rejects at compile time); 4f-2 bss lengths and symbol offsets in int64 for a
32-bit host; 5 VM / interp (HostInt at allocation); 6 remove the int accessors, and an end-to-end test on the
32-bit lane.
CI red from stage 4e (noted 2026-10-28, work-3): conformance 1718_offsets_past_2gib (binate `650f83fc5`) fails on the
LLVM lanes (builder-comp, -comp-comp, -comp-comp-comp; CI run 40645707398 at 5e54b7554) — clang rejects main's frame:
"stack frame size (2147483704) exceeds limit (2147483647) in function 'bn_F1_4_main1_9_copyBytes'
[-Werror,-Wframe-larger-than]" ("clang failed compiling main").  The test has .error markers only for the 32-bit lanes.

### An array length reading a composite literal of a type declared later, or a parent interface's method, is rejected while the package's types are collected — 🔴 OPEN (a valid program rejected; found 2026-10-27, work-7, probing the member-read fix, binate `f7e99b80b`; reproduced on main; pre-existing)

`type X struct { b [len(Z{}.c)]uint8 }` with `type Z struct { c [16]uint8 }` declared after it is rejected "cannot
use an opaque type by value" and "cannot access field on this type" (Z declared first, it compiles): nothing
collects the literal's type before its fields are read.  `interface P { m() [16]uint8 }`, `interface I : P { n()
int }`, `var i *I`, `type X struct { b [len(i.m())]uint8 }` — all declared in that order — is rejected "cannot
access field on this type": an interface's parents are resolved only once the whole batch is collected
(resolvePendingIfaceParents, check/check_interface.bn), so a method it inherits is not there during collection.
Fix: collect a composite literal's type on demand (prepareLayout) where collection has not reached it; resolve an
interface's parents on demand when a method is looked up on it during collection; unit tests for both.

### A member read is a cycle when another member of its type depends on the reader — the checker judges it per type, not per member — 🔴 NEEDS DECISION (found 2026-10-27, work-7, review of the member-read fix; pre-existing, before binate `f7e99b80b` as "cannot access field on this type")

`type X struct { b [len(p.c)]uint8 }`, `var p *Z`, `type Z struct { c [16]uint8; d [sizeof(X)]uint8 }` is "cyclic
type definition involving `X`": reading `p.c` collects Z, whose `d` reads X's size while X is collected.  `c` does
not depend on X, so resolving members one at a time would accept it; with `var p Z` (Z by value) main reports the
same cycle.  Interfaces likewise: `type X struct { b [len(i1.m())]uint8 }`, `var i1 *I1`, `var i2 *I2`,
`interface I1 { m() [len(i2.n())]uint8; k() [3]uint8 }`, `interface I2 { n() [len(i1.k())]uint8 }` is "cyclic type
definition involving `I1`": I1's methods are added together, and `k` is read while `m` is resolved.  The spec's
`type.named.value-acyclic` covers a type containing itself by value, not a layout reached through an array length,
so whether these programs are valid is not decided.  Decide: such cycles judged per type (say so in the spec, with
a conformance error test), or per member (a struct's fields and an interface's methods resolved one at a time on
demand, with tests).

### A field read through a pointer to a type declared later that has no such field is "cannot access field on this type" — 🔴 OPEN (minor: a misleading diagnostic; found 2026-10-27, work-7, review of the member-read fix; pre-existing)

`type X struct { b [len(p.c)]uint8 }`, `var p *Z`, `type Z struct { E }`, `type E struct { c [16]uint8 }` is
"cannot access field on this type" at `p.c`; in a function body the same read is "undefined: c" (Binate promotes
nothing from an embedded field).  Z's declaration has no field `c`, so the member read does not collect Z
(check/check_member_read.bn, fieldPath), and the selector then finds Z with no underlying type.  Fix: where neither
the declaration's fields nor its methods have the name, report "undefined: c", with a unit test.

### A build that waits for a layout still builds the instances it names from the length it could not read, and reports their errors — 🔴 OPEN (a valid program rejected; found 2026-10-28, work-7, review of the abstract-declaration wait; reproduced on main; pre-existing)

`type Other[T any] struct { x T }`, `func (o *Other[T]) q() int { return 32 / cast(int, sizeof(T)) }`, `type Box[T
any] struct { a *Other[[sizeof(T)]uint8]; t int }`, `type N struct { v int64; p *Box[N] }` is rejected "division by
zero in constant expression (in Other[[0]uint8].q, instantiated at … via Box[N])"; with `y [sizeof(T) - 1]uint8` in
Other in place of q, "array length is too large".  Box[N]'s build reads sizeof(N) while N is collected and waits
(check/check_layout_wait.bn), but goes on resolving with the length as 0, and names Other[[0]uint8]: that instance
is built in a frame of its own that does not wait, so its errors are reported, and it stays cached.  The same
happens inside a generic's declaration resolved abstractly (`type Box[T any] struct { a *Other[[sizeof(N)]uint8];
t T }`, `type N struct { v int64; p *Box[int] }`).  Without the member that breaks on a zero length the programs
compile and print the right lengths.  Fix: build no new instance inside a build that waits, as skipDemand resolves
no declaration on demand there (or build it uncached, its errors dropped), with unit tests for both forms.

### A generic alias of a struct type that takes the size of a type being collected is a false cycle — 🔴 OPEN (a valid program rejected; found 2026-10-28, work-7, review of the abstract-declaration wait; reproduced on main; pre-existing)

`type A[T any] = struct { a [sizeof(N)]uint8; t T }` with `type N struct { v int64; w int64; p *A[int] }` is
"cyclic type definition involving `N`", though N holds A[int] only through a pointer; N's field then keeps the
struct with `[0]uint8` (`n.p = &x`, x an A[int], is "cannot assign *struct { a [24]uint8; t int } to *struct { a
[0]uint8; t int }").  A generic alias's target is resolved for its arguments (instantiateGenericAlias) in no layout
frame that can wait, unlike a generic type's instance (check/check_layout_wait.bn).  Fix: let the alias target's
resolution wait as an instance's build does, and be resolved again once the layout is built, with a unit test.

### An invalid array length makes the array `[0]T`, so its uses report follow-on errors — 🟡 IN PROGRESS (claimed 2026-10-29, temp-5/session, self-drive; minor: a follow-on diagnostic; found 2026-10-26, work-5, probing the `len(nosuch)` change; reproduced; pre-existing)

`func f() [-1]int { var r [3]int; return r }` reports "array length must be non-negative" and then "cannot assign
[3]int to [0]int"; a variable length (`[n]int`), `[true]int` and an undefined name do the same.  resolveTypeExpr
(check/resolve_type.bn) builds `[0]T` whenever evalArrayLen's length is not known, which covers a length already
reported invalid as well as one not known yet (re-resolved later).  Fix: have evalArrayLen tell the two apart and
resolve an invalid length to the error type, as resolveTypeExpr does for `[...]T` outside a composite literal, with
a unit test pinning a single error.

### A deferred early read of a variable loses the errors of the constants it resolved — 🔴 OPEN (minor: a lost diagnostic; found 2026-10-25, work-5, review of the on-demand methods change; reproduced)

`batchVarOnDemand` (check/check_decl_batch.bn), deferring a variable's check while types are collected, drops every
error since its mark (`dropChecksSince`) and leaves the functions it defined on demand to collection, which reports
their errors again.  A constant first resolved during that check is not taken back: it stays resolved
(`c.ConstResolved`), and its own error is gone.  `type B [len(tbl)]uint8`, `var tbl = mk()`, `func mk() [N]int`,
`const N = undefinedThing` reports "array length must be a constant integer" and "declare its type", but not
"undefined: undefinedThing".  A method in mk's place behaves the same (methods are defined on demand since binate
`7206c4652`).  The program is rejected either way.  Fix: take back the constants (and the types
collected on demand) resolved inside the deferred check, so they are resolved again and report their own errors.

### A deferred early read of a variable leaves the function instances its check built cached — 🔴 OPEN (minor: a misleading diagnostic; found 2026-10-19, work-5, review of the on-demand functions change; by reasoning)

`batchVarOnDemand` (check/check_decl_batch.bn), deferring a variable's check read while types are collected, drops
its errors but not the generic-function instances (`FuncInstance`s) or queued instance work the check built.
`type B [len(tbl)]uint8`, `var tbl = gf[int]()`, then `func gf[T any]() [<a length negative for T=int>]T`: the
instance's signature error defers the check and is dropped; `recheckDeferredVars` then hits the cached instance and
reports a misleading "declare its type" at tbl, while the real error surfaces only from the instance's body check.
The program is rejected either way.  The REPL undoes such caches (`markInstCaches` / `dropInstCaches`,
`discardInstWork`), but dropping them mid-collection could leave a function defined on demand meanwhile with a
signature naming a dropped instance type, so the fix needs care.  Also noted by that review, with no concrete case:
a lookup made from an imported generic's instance-signature scope (which sets `c.curPkgPath` to the generic's home)
could define a same-named local function on demand, and `enterPackageDecl` does not reset `curPkgPath`.

### The LLVM backend declares a function's own sret parameter `align 8` whatever the result's alignment; a C caller's buffer may be less aligned — 🔴 OPEN (found 2026-10-09, work-1, review of the returned-local change; pre-existing; plausible, not yet reproduced)

A function returning through sret is defined `define void @f(ptr sret(%T) align 8 %v.retbuf, ...)`
(emit_debug.bn), promising LLVM an 8-aligned buffer for every T.  Binate callers keep that promise (an LLVM
`.sret` buffer is declared `align 8` by writeResultBufAlign; native frame regions are 8-aligned), but a C caller
reaching the function through #[c_export] — the alias, or an __c_entry thunk, hands a single sret aggregate's
C pointer straight through — aligns its buffer only to alignof(T): 4 for a struct of int32s, 1 for a char array.
LLVM may then access the buffer with instructions that need 8-byte alignment (LDRD / STRD, NEON on arm32, which
fault on a strict-alignment target).  The native backends assume nothing.  The return-time copy into the buffer
relied on it already; the returned-local change (a local living in the buffer for the whole function) widens
the exposure.  Fix: spell the define line's alignment from T (its AlignOf, as byvalArgAlign does for by-value
arguments) — at least for functions a C caller can reach; reproduce first with an e2e C caller passing a
4-aligned, not 8-aligned, buffer to a #[c_export] function returning a struct of int32s, on arm32.
Same assumption elsewhere (found 2026-10-13, review of the shim retbuf exact-size fix): the LLVM func-value /
closure shims forward `%retbuf` as `ptr sret(...) align 8` (emit_funcvals_shim.bn, emit_funcvals_closure.bn) —
abi/03 promises the retbuf only AlignOf; every current dispatch caller 8-aligns it, so latent.  Native arm32
storeMultiRetGpFieldArm32 picks word (STR / STRH) vs byte stores by the field offset's parity, assuming the base
is word-aligned; through the #[c_export] trampoline's CRET_ADAPT_SRET path the base is the C caller's buffer, so a
1-aligned tuple such as `(int8, [3]int8, [3]int8)` (C sret, 7 bytes) at an odd address gets unaligned stores —
a fault on strict-alignment bare metal.  Fix with the rest: decide by the buffer's guaranteed alignment
(AlignOf of the tuple / result), not offset parity alone.

### asm Mach-O writer: init / fini / preinit arrays are rejected — map them to `__mod_init_func` / `__mod_term_func`? — 🔴 NEEDS DECISION (found 2026-10-15, work-2, review of the section-flags change)

The Mach-O writer (`machoSectionsOk`, pkg/binate/asm/macho/macho_sect.bn) rejects a section named
init_array / fini_array / preinit_array (alone or with a "." suffix): written as an ordinary `__DATA` section,
as it was before, its functions would silently never run.  The Mach-O equivalent of an init array is
`__DATA,__mod_init_func` (S_MOD_INIT_FUNC_POINTERS), of a fini array `__mod_term_func`
(S_MOD_TERM_FUNC_POINTERS); preinit arrays and priority suffixes (`init_array.N`) have none.  Decide: map
init_array / fini_array to them (and keep rejecting preinit and suffixed arrays), or keep rejecting.  Also
open: note sections (note*) — SHT_NOTE on ELF — are ordinary sections on Mach-O, which has no notes.

### asm Mach-O writer: section alignment minimums by name (8 for data / bss / relro, 4 otherwise), where clang's is the contents' — 🔴 OPEN (found 2026-10-15, work-2, review of the ELF alignment change; latent)

`machoSectAlign` (pkg/binate/asm/macho/macho_sect.bn) gives `__data` / `__bss` / relro 2^3 and every other section
2^2, and `sectionAlignment` (macho_layout.bn) places each section on at least 8, where clang's hand-written-asm
objects give `__text`, `__data` and `__const` 2^0 unless `.p2align` asks (probed in the review).  The ELF writer
now uses the contents' alignment (Section.Align, which instructions raise to 4) with clang's minimums; Mach-O
should match clang the same way.  Before lowering it, check what relies on it without asking: the comments name
64-bit globals in `__data` and ARM64_RELOC_UNSIGNED slots ld requires 8-aligned — native data globals request
their alignment (EmitDataGlobal), so find anything else.  No null-entry hazard here (Mach-O rejects arrays).

### bnld firmware-arm driver: `_sdata` / `_edata` / `__bss_start` / `__bss_end` are not word-aligned — 🔴 OPEN (found 2026-10-15, work-2, review of the ELF alignment change; latent: the driver is structure-checked, not booted)

`drivers/firmware-arm.bn` defines the data / bss boundary symbols at dot with no `AlignDot(4)`, but a startup
routine copies `.data` and zeroes `.bss` a word at a time between them.  The start symbols were 16-aligned only
because the asm ELF writer over-aligned every section (no longer: a byte-only `.data` is 1-aligned, as clang's);
the end symbols never were.  `runtime/baremetal_arm32/baremetal.ld` aligns them (`ALIGN(4)`).  Fix: align dot to
4 before each boundary symbol, and check the driver's symbol values in its builder unit test.

### bnld: an input symbol's ELF type is ignored — an IFUNC or TLS symbol, or a GNU_UNIQUE binding, is linked as an ordinary one — 🔴 OPEN (found 2026-10-15, work-2, scoping asm `.type`; latent)

bnld's ELF reader (`readSymbols`, pkg/binate/link/parse_elf.bn) keeps st_info's binding and drops its type, so a
symbol typed STT_GNU_IFUNC (`__attribute__((ifunc))`, glibc's static string functions) is linked as a plain
function — a call reaches the resolver, not the function it returns — and an STT_TLS symbol as a plain address,
both silently.  A binding of STB_GNU_UNIQUE (10) is none of local / global / weak.  Do: reject these inputs with an
error naming the symbol until bnld implements them (IFUNC: an IRELATIVE relocation in a static link, or the
symbol's type kept in a dynamic one; unique: as global, as ld.lld does).  An STT_TLS symbol is rejected already
(binate `4d91dc223`), until the TLS series links it.  The asm can produce an IFUNC symbol once `.type` lands.

### bnld Mach-O reader: a section type it does not handle is read as data — S_GB_ZEROFILL gets the header bytes — 🔴 OPEN (found 2026-10-17, work-2, review of the asm TLS change; latent)

`machoSectionKind` (pkg/binate/link/parse_macho.bn) treats only S_ZEROFILL as zero-fill and every other section type
as data or rodata, copying `size` bytes from the section's file offset.  A zero-fill section of another type has
offset 0, so S_GB_ZEROFILL (0xc) and S_THREAD_LOCAL_ZEROFILL (0x12) get the object's Mach-O header as their
contents, silently.  Types with linker semantics bnld does not implement are also taken as plain data: S_COALESCED
(0xb, one copy kept across objects), the literal- and symbol-pointer and stub types (5–8, 0x10), S_INTERPOSING (0xd),
S_DTRACE_DOF (0xf), the thread-local types (0x11–0x15) and S_INIT_FUNC_OFFSETS (0x16).  Fix: handle each type clang
or ld64 can put in an object, or reject it with an error naming the section (the thread-local types are rejected
already, binate `4d91dc223`).

### asm Mach-O `@GOTPAGEOFF`: accepts STR / LDR D / PRFM, which ld64 refuses to link — 🔴 OPEN (found 2026-10-18, work-2, review of the asm `@TLVPPAGEOFF` change; loud at link time)

The aarch64 parser takes `sym@GOTPAGEOFF` on every 8-byte transfer (`isa.GotSlotTransferOk`: LDR / STR of X or D,
PRFM), as clang assembles them, but ld64 links ARM64_RELOC_GOT_LOAD_PAGEOFF12 only on an LDR (or ADD): `str x1,
[x0, _d@GOTPAGEOFF]` fails with "relocation on non-LDR/ADD instruction".  ELF's `:got_lo12:` is fine with the
whole set.  Fix: on Mach-O take only what ld64 links (check LDR D and PRFM against ld64 too), as `@TLVPPAGEOFF`
takes only `ldr Xt` (binate, the `@TLVPPAGE` change).

### Thread-local storage in the assembler and bnld (the TLS series) — 🟡 IN PROGRESS (claimed 2026-10-17, work-2; user: "let's continue with the items on the list"; scope user 2026-10-09: "All, staged"; 2026-10-17: "All three arches (Recommended)")

Nothing handles TLS today: the assembler rejects `.tdata` / `.tbss`, `%tls_object` and every TLS operator;
bnld reads `.tdata` / `.tbss` as plain data / bss and rejects every TLS relocation, so no C object using
`__thread` links with bnld on any target.  Stages (all three arches: aa64, x86-64, arm32, ELF and
Mach-O) and the clang survey are in `plan-tls.md`.  Progress: stage 1, the assembler's thread-local sections
and symbols — landed binate `08e9b84b1` (2026-10-17); bnld rejects thread-local storage in an input (an SHF_TLS
section or STT_TLS symbol, a Mach-O S_THREAD_LOCAL_* section) until it links it — landed binate `4d91dc223`
(2026-10-17); stage 2a, the aa64 local-exec / local-dynamic operators (`:tprel_*:`, `:dtprel_*:`) — landed
binate `54d587043` (2026-10-17); 2b, initial-exec / TLSDESC (`:gottprel*:`, `:tlsdesc*:`, `.tlsdesccall`,
`:tlsdesc_auth*:`) — landed binate `7df7a13b2` (2026-10-18); 2c, Mach-O `@TLVPPAGE` / `@TLVPPAGEOFF` — landed binate
`0b78c67a8` (2026-10-18; a TLV program assembled here links with ld64 and runs).  Stage 2 is complete.  Next: stage 3,
the x86-64 operators.

### bnld: the firmware-arm and script-arm drivers do not place arm32's unwind index — 🔴 NEEDS DECISION (found 2026-10-16, work-2, the `.ARM.exidx` change)

The baremetal-arm driver places `.ARM.exidx` (PlaceArmExidx) and `.ARM.extab`; drivers/firmware-arm.bn and
drivers/script-arm.bn place neither, so a clang-compiled C object's index is left unplaced and dropped
without a word (the builder fails only for an unplaced init / fini array, or an `.eh_frame` its search table
counts).  An unwinder that looks the table up by `__exidx_start` / `__exidx_end` fails the link as undefined
(only PlaceArmExidx defines them); one that never runs is unaffected.  Options: (a) add PlaceArmExidx and
`*(.ARM.extab*)` to both drivers, after their code, as baremetal-arm does; (b) also make the builder reject
an unplaced index, as it rejects an unplaced array, with an explicit way for a driver to discard one; (c)
leave the two drivers minimal.  Recommendation: (a) and (b), so no driver loses the index without saying so.

### bnld Mach-O: unwind info is dropped — `__compact_unwind` / `__eh_frame` read and discarded, no `__unwind_info` written — 🔴 OPEN (found 2026-10-16, work-2, the ELF unwind-tables work)

bnld's Mach-O reader drops clang's `__LD,__compact_unwind` and `__TEXT,__eh_frame` (parse_macho.bn), and the
writers emit no `__TEXT,__unwind_info` — the two-level lookup table ld64 builds from the compact-unwind
entries, with `__eh_frame` for frames compact unwind cannot describe, and which libunwind finds through that
section — so a bnld-linked Mach-O program cannot unwind through its C code (C++ exceptions, `backtrace()`):
the Mach-O counterpart of the ELF gap closed by binate `10adaecd2`.  Do: read `__compact_unwind` (with its
section-relative relocations; see "bnld's Mach-O reader has no general section-relative (non-extern) reloc
support") and `__eh_frame`, write `__unwind_info` as ld64 does, and add an e2e that unwinds through
bnld-linked C code on macOS.

### asm: unwind directives are rejected — `.cfi_*`, and arm32's EHABI `.fnstart` / `.fnend` / `.cantunwind` / … — 🔴 OPEN (found 2026-10-16, work-2, the bnld `.ARM.exidx` change)

The text assembler knows no unwind directive ("unknown directive"), so hand-written assembly (bnas input,
the runtime `.s` files) cannot describe its frames.  clang turns `.cfi_startproc` … `.cfi_endproc` and the
CFA rules between them (`.cfi_def_cfa*`, `.cfi_offset`, `.cfi_restore`, `.cfi_remember_state`, …) into
`.eh_frame` FDEs on ELF and compact-unwind entries on Mach-O, and arm32's EHABI directives (`.fnstart` /
`.fnend` / `.cantunwind` / `.save` / `.vsave` / `.setfp` / `.pad` / `.movsp` / `.personality` /
`.personalityindex` / `.handlerdata` / `.unwind_raw`) into `.ARM.exidx` / `.ARM.extab` entries.  In scope
as assembler completeness (every directive clang accepts), each target's output verified against clang's
objects; bnld already links the ELF forms.

### Spec gap: when are a compound statement's header temporaries released? — 🔴 NEEDS DECISION (found 2026-10-12, work-4, spec review of the `defer { … }` change; pre-existing)

§9.7 says a statement's temporaries last "until the end of that statement", but the review's probe shows a
`switch` tag's temporaries released before the case body runs: a deferred block in the case body sees
`switch asNamed(d).name()`'s temporary already released (observable with `rt.Refcount`).  For a compound
statement, "the end of the statement" would keep them alive through the whole body.  Decide — the review
suggests pinning that `if` conditions' and `switch` tags' temporaries are released before the selected body
runs, which matches the compiler for `switch` (unverified for `if`) — and give the `for` condition and post
statement the same answer (each evaluation's temporaries); then state it in §9.7 / §18.4 `mem.temporary`, with
a conformance test.

### Neither backend probes the stack for a large frame — a frame past the guard page can step over it — 🔴 OPEN MAJOR (found 2026-10-18, work-1, review of the native aa64 16 MiB fix; pre-existing; needs a decision)

A function's prologue moves SP down by its whole frame in one step (native: `sub sp, sp, #frame`, now also past 16
MiB on aarch64; LLVM: the IR carries no "probe-stack" attribute, so clang emits none either), so with the stack
nearly exhausted a frame larger than the guard region sets SP past the guard page, into whatever is mapped below
(another thread's stack, the heap), and the function's stores corrupt it instead of faulting (the "stack clash"
class).  clang probes such frames on its own targets (macOS: `___chkstk_darwin`; Linux: `-fstack-clash-protection`).
Fix: probe each guard-sized step of a large frame in the native prologues (touch every page, or a probe loop),
give the LLVM backend's functions the matching "probe-stack" attribute, and decide the bare-metal behaviour (no
guard page there).  Test: a recursion with a frame past the guard size that overflows must fault, not corrupt.

### A variadic call's backing array is not size-checked — on a 32-bit target, two 1 GiB arguments end in an internal error, not "type is too large" — 🔴 OPEN (found 2026-10-27, work-1, review of int64 layout 4e-4; pre-existing; minor: fails loud)
IR-gen builds a variadic call's backing array as `[n]T` (irgen/gen_variadic.bn) after the checker, which checks
only the types the program writes (check_too_large.bn), so an `[n]T` past the target's maximum object size (spec
type.layout.max-size; 2^31 - 1 on a 32-bit target) reaches layout, where types.Size() panics: "internal error: a
type too large for the target reached layout (the checker rejects it)".  Every backend (the panic is in types);
the host target compiles the same program.  Repro (`--target arm32-baremetal`):
```
type Big [1 << 30]uint8
func use(xs ...Big) int { return len(xs) }
func main() { var a Big; var b Big; _ = use(a, b) }
```
Fix: the checker rejects a variadic call whose packed arguments (n of the element type) pass the maximum object
size, with the same "type is too large" error.  Test: conformance 1719 (binate `8b25f89ee`) expects that error on
the five arm32 modes, xfailed there until the fix.

### Native backends copy a by-value aggregate argument read through a pointer twice — on arm32 three 1 GiB arguments pass the 4 GiB frame — 🔴 OPEN (found 2026-10-28, work-1, int64 layout 4c-2's conformance test 1720; pre-existing)
`f(*p, *p, *p)` with f taking three by-value 1 GiB arrays: the native backends give each `*p` load its own frame
region (emitAggLoad) and then copy it into the outgoing-args area, so the frame holds every argument twice.  With
`Big` 64 KiB, native arm32 gives `direct` a frame of 0x60030 bytes (3 load regions + 3 argument copies); at 1 GiB
native arm32 rejects it ("the frame of main.direct is larger than the target allows (at most 4294967295 bytes)")
where LLVM arm32 compiles it, copying straight into the argument area.  The 64-bit native backends compile it with
a doubled frame.  Fix: elide the load's region when the loaded value only feeds a by-value call argument (read in
place, as AggLoadElision does for other uses), copying from the source to the argument slot.  Covered by conformance 1720 (binate `e320b4e07`),
xfailed on the two native arm32 modes until the fix.

### Array `==` / `!=` unrolls one comparison per element — a large array blows up compile time and IR size — 🔴 OPEN (found 2026-10-18, work-1, review of int64 layout stage 2; pre-existing)

irgen/gen_eq_aggregate.bn emitAggregateEq compares an array element by element, fully unrolled in straight-line
IR (its own TODO says so): `var a, b [1<<24]int; a == b` emits ~100M IR instructions, and `[1<<40]struct{}` (legal
on a 64-bit target) never finishes compiling.  Fix: the loop form genArrayCopy uses (a counted loop over the
elements with a bool accumulator slot), at least past a small element count; a conformance test comparing a large
array (equal, and unequal at the last element).  An element of size 0 has no data, so such an array compares equal
with no compare at all (review of int64 layout stage 3: `var a, b [1 << 16]E; a == b` with `E` a `struct {}` emits
590k lines of LLVM IR, and conformance 1700's `Z` compared with `==` never finishes compiling).

### `var s [N]char = "…"` emits per-byte IR for the whole array — a large array blows up compile time and IR size — 🔴 OPEN (found 2026-10-18, work-1, int64 layout stage 3; pre-existing)

codegen/emit_strings.bn lowers OP_RODATA_ARRAY by storing every byte of the `[N x i8]` slot one at a time
(emitInlineByteStores, the zero-padding tail too), then loads the slot as an SSA `[N x i8]` value, which the store
to the variable decomposes again leaf by leaf (an OP_RODATA_ARRAY is not a memory-backed bulk value,
emit_bulk_value.bn): `var s [20000]char = "hi"` emits ~100k IR lines (40k GEPs + byte stores, 20k extractvalues +
stores).  The native backends' lowering is unchecked.  Fix: store the literal's bytes, zero the tail with one bulk
zero-fill (emitMemZero), and make the value memory-backed (or have IR-gen store into the variable's slot
directly); a conformance test with a large zero-padded array.  Past 2^31 bytes (a 64-bit
target) int64 layout stage 3 (`651ea270e`) made it the internal error "an aggregate too large for i32
offsets is emitted leaf by leaf".

### No CI lane runs the LLVM arm32 bare-metal mode at -O2 — 🔴 OPEN (raised 2026-09-30, work-1; awaiting a user decision)

.github/workflows/conformance-o2.yml runs builder-comp (host LLVM) and the native modes at -O2, not
builder-comp_arm32_baremetal, so bare-metal -O2 link failures (`__aeabi_memclr`, fixed in `457365120`) went
unnoticed.  Locally that mode now passes the 805-test call/aggregate subset at -O2 (1414/0); the one known -O2
failure is stdlib/debug/001_callers (see "stdlib/debug/001_callers prints "short" at -O2 …"), which needs its own
decision before a lane can go green (xfail markers are per mode, not per -O level).  Adding the lane is the user's
call (CI wiring).

### Until `BUILDER_VERSION` includes binate `50d176225`, BUILDER-compiled code must not instantiate one generic with both `int` and `int64` — 🔴 OPEN (constraint until the next BUILDER release; found 2026-10-18, work-1, int64 layout stage 4)

bnc-0.0.16 (the pinned BUILDER) predates `50d176225` ("`types.Identical` treated same-width predeclared integers
as one type"), so on a 64-bit host it makes `vec.Vec[int]` and `vec.Vec[int64]` one instance: gen1's build of a
package using both fails, e.g. `b.Push(x)` with `x int64` → "cannot assign int64 to int" (seen in native/common's
int64 frame tables).  The current compiler is fine, so the unit tests (built by it) pass and only a gen1 build
shows it.  In cmd/bnc's cone, keep a generic's int and int64 instances apart (native/common keeps its int64 frame
lists in a non-generic list).  Clears when a BUILDER containing `50d176225` is pinned (cut only when
independently justified).

### Until `BUILDER_VERSION` includes binate `1f29d31e9`, gen1 silently miscompiles some statements that open a block or follow a compound statement in BUILDER-compiled code — 🔴 OPEN MAJOR (constraint until the next BUILDER release; found 2026-09-30, work-4)

bnc-0.0.16 (the pinned BUILDER) has the IR-gen defect fixed on main by `1f29d31e9`.  So in cmd/bnc's cone (the packages the BUILDER compiles into gen1), these must not be the first statement of a loop / `if` / `else` / `case` body, nor the statement right after an `if` / `for` / `switch`:
- a struct-field `++` / `--`;
- `f := someFunc`;
- assigning a variable to a `*any`.

gen1 would get silently wrong code there, and gen1 compiles every test and gen2.  Examples: a loop body that never runs (the function returns early, with no error); `st.Top++` right after an `if` or a `for` does nothing.  An identifier `++` just before it doesn't help.  A statement that goes through expression evaluation first (`x.f = x.f + 1`, a declaration, a call) is fine, and so is one in a function's opening straight-line code.  Design B's instance stack (`pushInstWork` / `popInstWork`, binate `fe95d7de8`) keeps its `++` / `--` out of those positions; TODOs there mark it.  A 2026-09-30 scan of non-test code found no other field `++` / `--` (the only two are design B's), no `x := name` short-var, and no `*any` variable, parameter or field.  Clears when a BUILDER containing `1f29d31e9` is pinned (cut only when independently justified).

### REPL redefinition of types and interfaces, and across kinds: shadowing (the design) is not implemented; such a redefinition is rejected meanwhile — 🟡 IN PROGRESS (claimed 2026-10-09, work-6/session — user: "let's do item 7 then") (found 2026-09-29, work-6, review of the REPL forward-reference plan; widened 2026-09-30)

claude-notes.md ("Redefinition in the REPL") says an incompatible type redefinition shadows the old
type: "existing instances retain the old layout/type definition".  Until binate `8ba473042` a
redefinition was silently ignored by the checker (collectTypeDecl returned early on a filled named type)
while IR-gen registered a second `main.T`; since then `type T …` over a bound type is rejected ("cannot
redefine type T", check/check_pending_tentative.bn rejectTypeRedefinitions; user chose this until
shadowing lands).  Shadowing needs a type identity that tells the two T's apart through the checker
(named-type identity is by qualified name today), IR-gen's type registries (lookupStructIdx returns the
first `main.T`) and the destructor / copy helper names.

The same holds for interfaces, and for a declaration of another kind over a type's or an interface's
name, or a type or interface over another kind of name (`interface Sizer {…}`, `const Sizer = 1`,
`interface Sizer {…}` again): IR-gen's registries keep the first registration, so without the rejection
a conversion dispatched through the stale interface (wrong code).  These are rejected at the prompt too
("cannot redefine interface Sizer as a constant"; user: "(a) is fine for now, though maybe (b) should be
a todo" — (b) being cross-kind shadowing, like type shadowing).  Shadowing them needs the same
generation-distinct identity through the checker's and IR-gen's registries.

### The conformance runner has no compile-size / compile-memory guard — one pathological test can exhaust the machine — 🔴 OPEN (split out of the 1301 whole-array-load entry, 2026-09-28, work-1; user: "yes, keep the 1301 suggestion as its own todo")

Conformance 1301 used to make clang -cc1 pass 4.5 GB RSS, and an unwatched `builder-comp-comp` run on
2026-09-28 drove the machine into memory-pressure jetsam, while the test itself "passed" — nothing flagged
the blow-up.  1301 is fixed (binate `e0287aa7b`), but any future test (or compiler regression) with the
same shape would do it again: the per-leaf aggregate lowering entry below is one live source.  Proposal:
make the runner fail a test whose compile (bnc and its clang/linker children) exceeds a memory or time
budget, instead of letting it take the machine down.  macOS has no working `ulimit -v`, so this needs a
watcher on the process tree's RSS (the ad hoc local one sampled `ps` every 2 s and killed any descendant
over 4 GB) or a per-test timeout plus a post-hoc peak-RSS check (`/usr/bin/time -l` reports the max over
waited-for children).  Wiring it into CI is a separate decision.

### A managed operand borrowed during evaluation can be freed by a later operand's side effect — spec it as undefined behavior; consider a bnlint check — 🔴 OPEN (found 2026-09-29, work-1, review of the evaluation-order change; pre-existing)

IR-gen reads a managed value from a variable as a BORROW (no RefInc) while it evaluates later operands; if a
later operand runs code that reassigns the variable holding the only reference, the pending use reads or
writes freed memory.  Repros (exit 139 under `DYLD_INSERT_LIBRARIES=/usr/lib/libgmalloc.dylib`), all on the
compiler before the evaluation-order change: `f(s, g())` with `g` reassigning global `s @[]int` (f reads a
freed slice); `s[g()] = 5` and a read of `s[g()]`; parallel `p, p.val = q, 5` (p a sole-owner @Node).  The
base-before-index change extends it to `s[g()]++`, `&s[g()]`, `ps[h()].x = 3`, and a field/element address
through a managed pointer (`p.arr[g()]`).  Not `mem.raw-uaf` (no raw value in user code: the compiler chose
the borrow).  Decided (user, 2026-09-29): NOT a compiler fix — a hidden RefInc/RefDec is rejected ("That's a
hidden refinc/refdec, which we don't like"), and holding a reference whenever a later operand has a call is
needlessly expensive when the call doesn't touch the earlier value.  The spec makes it undefined behavior
(docs `e2c178c`: §18.7 `mem.operand-release`, listed in §21.6).  Remaining here:
a possible bnlint rule — e.g. flag an expression/statement that reads a managed GLOBAL (or a field/element of
one) as an operand before a later operand that contains a call (any call can reassign a global).

### The LLVM backend lowers aggregate loads, copies and zero-fills one scalar leaf at a time — IR (and clang memory) grows with array length — 🟡 IN PROGRESS (found 2026-09-28, work-1, while fixing conformance 1301's whole-array load; pre-existing; claimed 2026-09-29, work-1; zero-fill + memory-to-memory copy DONE `a39d67d9f`; memory-backed values step 1 (bulk load stored as a whole) DONE `575fb43ee`; by-value arguments DONE `c97493379`; returns + sret call results DONE `352691b60` — the 100 KB pass/return program is 475 lines of IR, 0.2 s; extracts DONE `18ffbb48c`)

Every aggregate memory operation in the LLVM backend decomposes per scalar leaf: a zero-fill is one GEP +
`store 0` per leaf (codegen emit_copy.bn `emitFieldwiseZero` / `emitZeroRec`), a copy one GEP + load +
store per leaf (`emitCopyRec`), an aggregate load one load + `insertvalue` per leaf
(emit_copy_ssa_load.bn).  So the emitted IR is O(number of array elements):
- `var buf [1000000]uint8` in a function: 1,000,000 `store i8 0` (2M lines of IR);
- `var d [100000]uint8 = src` (a global): 800k lines (per-byte zero-fill, then per-byte load +
  insertvalue);
- conformance 1301's `var local [2]Big` (`Big` holds `[70001]uint8`): 140,002 byte stores, most of its
  remaining 13 MB of IR.
A local array of a few MB drives clang into multi-GB RSS, which is the same failure 1301 had.  Native aa64
compiles the 1 MB case in 0.07 s, so this is LLVM-specific.
Why it is per-leaf: emitFieldwiseZero's doc says an aggregate `store zeroinitializer` may lower to
`memset` / `__aeabi_memclr`, which bare metal does not carry.  But `runtime/baremetal_arm32/semihost.s`
already provides byte-loop `memset` / `memcpy` / `memmove` / `memcmp` for exactly the calls clang emits
implicitly (not `__aeabi_memclr`), so that rationale needs re-checking per target.
Fix direction (needs a decision): emit a loop over the elements for an array past a small size (it would
need `"no-builtins"` so LLVM does not turn it back into memset, if memset is to be avoided), or allow the
memset/memcpy intrinsics and provide every symbol they can lower to on each target.
No test pins it yet: unit-test xfails are per package and mode (scripts/unittest/), so one codegen test
can't be marked expected-fail, and a conformance test big enough to show it would exhaust memory until
the fix lands.
Recon (2026-09-29, work-1): the per-leaf lowering is deliberate — done/plan-codegen-c-free-copies.md (shipped
2026-06-01) made bnc-emitted code free of memcpy / memset / memmove / `__aeabi_*` so bare metal (no libc, and
semihost.s has no `__aeabi_*`) links; that plan already named the follow-up for big aggregates: "a
Binate-level byte-loop helper (`rt.CopyBytes(dst, src, n)`) ... add later if measured" — now measured.  Also
found: on HOSTED targets at `-O2` LLVM's loop idioms already turn a plain Binate copy loop into a libc
`memcpy` call (`nm -u` shows `_memcpy`) — only bare metal passes `-ffreestanding` (cmd/bnc/target.bn), which
stops that; so the C-free property holds for bnc-emitted aggregate ops but not for optimized user loops on
hosted targets (a separate question).  Options: (A) above a size / leaf-count threshold, emit a call to a
C-free runtime helper (rt.ZeroBytes / rt.CopyBytes — Binate, later per-arch asm) for zero-fill and
memory-to-memory copy — O(1) IR on every target, keeps the C-free rule; (B) emit an IR loop over the
elements — O(1) IR, stays a loop on bare metal, but becomes libc memset/memcpy at hosted -O2; (C) allow the
llvm.memset/memcpy intrinsics — contradicts the C-free plan, needs `__aeabi_*` on bare metal.  Hard part in
any option: an aggregate LOAD is an SSA value (per-leaf load + insertvalue); a big one has to stay in memory
(temp + copy helper, the value represented by its address, as native does) — every SSA-aggregate consumer
(store, byval call arg, return, extract, phi) must accept that form.
Decided (user, 2026-09-29): "(A) is already our standard, and we already have rt.MemZero/rt.MemCopy (or
however they're spelled) ... and they mostly have per-arch assembly.  When running on Linux or Mac OS, we
can live with a call to memcpy; that's ok, given that stdlib isn't C free at all." — so big aggregates call
the existing runtime helpers; the hosted -O2 memcpy is accepted.
Stage 1 landed (binate `a39d67d9f`): above 16 scalar leaves emitFieldwiseZero / emitFieldwiseCopy call
rt.MemZero / rt.MemCopy — 1301 is 37 KB of IR (was 13 MB), a 1 MB local zero-fill 412 lines.  REMAINING:
large aggregate SSA values.  Measured with stage 1: a 100 KB array passed by value and returned by value
is 900k lines of IR, 382 s and 3.8 GB to compile.  Decided (user, 2026-09-29: "Doesn't (a) still leave the
'uncommon' case still rather disastrous?"): the full fix — every large-aggregate VALUE is memory-backed in
the LLVM backend (as native does): a load MemCopies into a function-scoped temp and the value is its
address; stores MemCopy from it, extracts GEP into it, by-value args / returns / call results / phis use
the address — every codegen producer and consumer of aggregate values handles that form.

Also (found 2026-10-18, work-1, review of int64 layout stage 3): a multi-return tuple with a bulk member is still
built as an SSA value, leaf by leaf — `func two(p *Big) (Big, int) { return *p, 3 }` emits 8202 per-leaf lines
for a 4 KB `Big`; for a `Big` past 2^31 bytes (a 64-bit target) int64 layout stage 3 (`651ea270e`) made it the
internal error "an aggregate too large for i32 offsets is emitted leaf by leaf" (before: a hang / out of memory).  The
tuple build needs the memory-backed form too.  So does `box(*p)` (OP_BOX stores an SSA value): a 4 KB struct boxes
in 12.6k lines of IR (4097 insertvalues), and a 3 GB one gets the same internal error.

### Per-instantiation checking (design B) follow-ups — 🔴 NEEDS DECISION (raised 2026-09-30 – 2026-10-02, work-4; design B itself is done, commits 1–7 landed)
- bnlint (CheckPackageDecls) skips the instance checks of a dependency's generics, whose bodies it does not
  check (bnc and bni check them: an interface-only package's `.bni` is its merged file).
- An instance whose constraint check failed is still checked (possible cascades of errors).
- Every instance error is reported as a user error (no ICE classification of a divergence between the
  abstract check and an instance's).
Plan (done): `done/plan-generic-instance-check.md`.

### Language feature: array-literal keys that depend on a type parameter — 🔴 OPEN (raised 2026-09-28, work-4)
`[sizeof(T)]uint8{sizeof(T) - 1: 7}` (a key whose value depends on a type parameter) is rejected today
(check_expr_composite.bn, "must be a constant").  User (2026-09-28): "they seem like they could be useful
(precisely for your example), but maybe not worth the effort of doing now. (OTOH, if it's easy to do now,
I guess we can allow.)"  Not easy before per-instantiation checking (design B) lands: IR-gen would need
each instantiation's key values.  After it, likely small: the abstract check defers a dependent key (as it
defers the element count), each instance's check validates it on its clone (KeyVal / KeyKnown), and
IR-gen reads the clone's stamps.  Needs a spec line (§7 composite literals) with it.

### Spec gap: which scope do for-header, range and type-switch binder names live in? — 🔴 OPEN (found 2026-09-28, work-4, review of the local-const redeclaration error)
The checker puts a `for` header's variables and `for … in` range variables in the same scope as the loop
body (`checkForStmt` pushes one scope), and a type-switch binder in each clause's scope, so `for i := 0; …
{ var i int }` / `{ const i = 1 }`, `for i, v in xs { const v = 1 }` and `switch v := x.(type) { case …:
const v = 1 }` are redeclaration errors (decl.var.redeclare / decl.const.redeclare).  The spec does not say
so: §9.5 `decl.scope.block` ("the bodies of … `for` … are blocks") reads as if the body were nested inside
the header's scope, where these would shadow.  Decide and state where these names live (and that a `:=` is
an "earlier declaration" for the redeclaration rules).

### Language feature: exempt a branch whose condition is constant-false for an instantiation from that instantiation's check — 🔴 OPEN (raised 2026-09-28, work-4, review of the gen.mono.check rule)
Under per-instantiation checking (spec §12.3 `gen.mono.check`, docs `9c9b08e`) both branches
of `if sizeof(T) == 4 { … bit_cast(uint32, t) … } else { … bit_cast(uint64, t) … }` are checked for every
T, so one always fails; Binate has no compile-time `if`.  The user accepted the limitation for now (my
recommendation: limitation + this todo).  Open questions if pursued: which conditions count as constant
for an instantiation, `&&` / `||`, `switch`, and whether the skipped branch is still checked abstractly.

### Resolve every package-level declaration on demand (a full demand-driven resolver) — 🔴 OPEN (raised 2026-09-29, work-4, review of the package-constant fix; user chose to track it: "go with your rec")

The package-constant fix resolves constants after collection and, on first read, the constants, types (for a
layout) and variables a read needs.  One path stays partial: an inferred-type variable named while types are
collected — by an array length, directly or through a constant it reads (`type B [len(tbl)]uint8; var tbl =
mk()`) — is checked against a half-collected package, so a function, type, method or impl declared later is
not there yet; the check reports that name as "declared later than a type whose array length needs it"
rather than resolving it.  Functions (binate `f1645f4c1`) and methods (binate `7206c4652`) are now defined on
demand; an impl declared later still is not (`var tbl = pick(&s)` before `impl S : I` gets "declare its type";
check_decl_batch_test.bn `TestArrayLenOfInferredVarNeedsLaterDecl` pins it).  Fix: resolve every package-level declaration on demand — function signatures,
types (for any use that needs their layout or methods), methods and impls — with one cycle stack, so the
order of collection never matters.  Needs conformance tests for each of those forms and for cycles through
them (`var v = F(); func F() [len(v)]int`, `type T [len(v)]int; var v = T{}`).
The `.bni` scope build (buildScopeFromFile) has the same gap for NAMED TYPES: constants and variables are
defined on demand (defineBniVarByName, work-3's `.bni` len fix), but a named type is resolved in declaration
order, so (found 2026-10-03 by the review of that fix; valid code rejected, each rejected before it too)
`const L = len(V); var V Buf; type Buf [4]int` → "not a constant expression"; `var V [S]int; type Size int;
const S Size = 4` → "array length must be a constant integer" (S records NOT_CONST); `var V [S]uint8; type H
struct{a int; b int}; const S = sizeof(H)` → S silently POISONED and V `[0]uint8` (only the package's own
checkBniVarMatch then complains).
Nor does a named type's array length define the `.bni` names it reads first (found 2026-10-26, work-5, probing
the `len(nosuch)` change; reproduced): `type T [N]int` before `const N = 3` in `q.bni`, or `type T [len(V)]int`
before `var V [3]int`, is "array length must be a constant integer" at q.bni, though the same in a `.bn`
compiles (a `.bn` constant or variable is resolved on demand): buildScopeFromFile's pass 2 resolves T, and so
evaluates its length, before it reaches N.

### aa64 text assembler: clang-valid instruction families still rejected (completeness) — 🟡 IN PROGRESS (listed 2026-09-26; claimed 2026-09-27, work-2/session; user: "Next, after this lands", then "yes"; landing family by family)

Loud rejections, not mis-assembly, but the assembler is meant to be comprehensive: exclusive / acquire-
release loads and stores (LDXR/STXR/LDAXR/STLXR/LDXP/STXP/LDAR/STLR/LDAPR/LDLAR/LDAPUR/STLUR), LSE
atomics (LDADD*/STADD/SWP/CAS/CASP), LDTR/STTR*, LDRAA/LDRAB, NEON LD1/ST1/LD1R and NEON / FP arithmetic
text forms, CRC32*, PACGA/XPACI, CFINV/RMIF/SETF8, TLBI/AT, the `:abs_g*:` and TLS relocation
operators (with them, thread-local sections: `asm.SetSection` rejects the names tdata / tbss, which clang makes
SHF_TLS, until a section flag can mark one thread-local — ELF SHF_TLS, Mach-O `__thread_data` /
`__thread_bss` with the thread-local section types), `.L` / numeric local labels, literal pools (`ldr =imm`, deliberately rejected today); and `name = expr` constants, which are defined but can't be
referenced (lookupConst has no callers) — these four 🟡 IN PROGRESS (claimed 2026-09-30, work-2/session;
user: "yes"; plan and open decisions in `plan-aa64-asm-symbols.md`).  Each family: isa encoder (if missing) +
parser + golden lines from clang.

**Progress (work-2):** family by family, one reviewed commit each.  (1) exclusive / ordered loads and
stores — LDXR…STLXP, LDAR/STLR, LDLAR/STLLR, LDAPR, LDAPUR/STLUR incl. the RCPC3 SIMD&FP forms — landed
`920be83aa` (2026-09-27; LDXP/LDAXP Rt == Rt2 deliberately rejected, unlike clang).  (2) the rest of FEAT_LRCPC3's loads/stores — LDIAPP / STILP,
LDAPR post-index, STLR pre-index — landed `898eb3d59` (2026-09-27; the CONSTRAINED UNPREDICTABLE register
overlaps deliberately rejected, unlike clang).  (3) LSE atomics
(v8.1: LD<op>/ST<op>, SWP, CAS, CASP) — landed `69116ea9c` (2026-09-27).  (4) LDTR/STTR family and LDRAA/LDRAB
(incl. the bare `[Xn]!` pre-index) — landed `ac4c5fc69` (2026-09-27).  (5) CRC32 / CRC32C, the pointer-auth
register forms (PAC* / AUT* / XPAC* / PACGA) and flag manipulation (CFINV / XAFLAG / AXFLAG / RMIF /
SETF8 / SETF16) — landed `8114782c0` (2026-09-27).  (6) FP and Advanced SIMD, incl. the SIMD loads /
stores (LD1–LD4 / ST1–ST4, LD1R–LD4R, LDAP1 / STL1) and crypto — complete, last piece landed `ef7ab6191`
(2026-09-29); step-by-step record in `done/plan-aa64-asm-fp-simd.md`.  (7) the system operation tables —
TLBI / AT, all of DC / IC, DSB nXS, PRFM's SLC target, CLRBHB / PACM, every PSTATE field — landed `19e49d3c0`
(2026-09-30; a DSB immediate past 15 written as an expression deliberately rejected, since clang reads it by
its leading literal alone — user: "The reject sounds good").  (8) `.`-leading names, numeric local labels and
per-format temporary labels (plan item 3a) — landed `477048003` (2026-09-30).  (9) `name = expr` constants (plan
item 3b) — complete: numeric constants `1a31e768f`, symbol-valued expressions (multi-term addends, aliases,
`C = .`) `f9acb7bb6`, x64 label addends `331b13ee4`, constants in the symbol table `63c3ba948`, global / weak
numbers `bf00c139e`, global / weak aliases `da8befa0a` (2026-10-02).  (10) literal pools (plan item 3c) —
`ldr Wt|Xt, =expr`, `.ltorg` / `.pool` — landed `14b65cb8b` (2026-10-03; the 4-byte absolute data word a W entry
needs, `2a26ec4d9`).  (11) the MOVW-group relocation operators (plan item 3d, first part) —
`:abs_g*:` / `:prel_g*:` on MOVZ / MOVN / MOVK and `mov`, with bnld — landed `7010879cb` (2026-10-04).  (12)
`:pg_hi21_nc:` on ADRP, with bnld — landed `42f0697f2` (2026-10-04).  (13) the GOT forms (assembler):
`:gotpage_lo15:`, `:got:` literal loads, 8-byte GOT stores — landed `f38f50223` (2026-10-04).  (14)
bnld's linker-made GOT (all four drivers) — landed `c645a568e` (2026-10-05).  (15) a number after an absolute
MOVW operator folded as clang does (plan remaining (1)) — landed `6da46c766` (2026-10-14).  (16) bnld's MOVW
PREL `_NC` / PREL_G3 opcodes as lld (plan remaining (2)) — landed `fc29977b9` (2026-10-14).  (17) ELF
relocations against named locals made against the section symbol, as clang's (plan remaining (4)) — landed
`fbb62a67a` (2026-10-15); plan remaining (3), GOT addends on ELF, dropped (user 2026-10-14: keep rejecting).
Apple's legacy NEON syntax
(`dup.4s v0, w1`, `tbl.16b v0, {v1}, v3`), which clang
accepts on every target, is not supported (user, 2026-09-28: "we don't need alternate syntax, unless there's
a compelling reason (we've always tended to favor Intel/ARM syntax, I suppose)") — listed with the deliberate
rejects, `bf5f7966b`.  **Also in scope (clang supports them; found while
scoping (3)):** the later atomic extensions — FEAT_LSE128 (LDCLRP / LDSETP / SWPP), FEAT_THE (RCWCAS /
RCWSWP / RCWCLR / RCWSET and their S / pair forms; the pairs need FEAT_D128), FEAT_LSFE (LDFADD /
LDFMAX(NM) / … and ST* aliases on H / S / D), FEAT_LSUI (LDT<op> / SWPT / CAST / CASPT) — each its own family.
**Scope (user, 2026-09-27: "we don't need to support everything *now* -- we just don't want ad hoc/arbitrary
omissions just because we don't need it now"):** every A64 extension clang supports is in scope, SVE/SVE2 and
SME/SME2 included — sequencing is free, but no family is dropped for lack of a consumer.  Beyond the families
above: FEAT_MTE (IRG / GMI / SUBP(S) / ADDG / SUBG / STG / LDG / STGP / …), FEAT_MOPS (CPY* / SET*), FEAT_LS64
(LD64B / ST64B*), FEAT_GCS, FEAT_CSSC (ABS / CNT / CTZ / SMAX / SMIN / UMAX / UMIN reg & imm), FEAT_SYSREG128 /
FEAT_SYSINSTR128 (MRRS / MSRR / SYSP), FEAT_RPRFM (RPRFM), FEAT_PAuth_LR (PACIASPPC / AUTIASPPC / RETAASPPC /
PACIA171615 / …), CHKFEAT and the other newer hint / system forms with their own syntax (GCSB DSYNC, STSHH,
CFP / DVP / CPP / COSP RCTX, BRB, TRCIT, TLBIP, SMSTART / SMSTOP), SVE/SVE2(.1), SME/SME2 — and the named
system registers: `sysRegByName` knows ~70 of the hundreds clang names (the generic
`s<op0>_<op1>_c<n>_c<m>_<op2>` form reaches any).  Newer LLVM only (Apple clang 21 rejects them; take them
when the reference clang does): FEAT_TLBID's optional Xt on the broadcast TLBI operations, DC GBVA / ZGBVA.
**Also open (found by reviews, 2026-09-29):** outside the instruction parsers a rejected
line can still emit: a data directive with trailing text or a later bad value (`.ascii "ab" x`, `.uint32 1
2`, `.int8 1, 300`, `.zero 4 x`, `.fill 2, 1, 7 x`, `.balign 8 x` padding in text) writes its bytes before
the error, and a label prefix on a rejected line (`L1: ldr x0, [x1] x2`) is still defined.  Harmless (an
error aborts the file) but the same "a rejected line emits nothing" rule; `lineRejected` in `parse_test.bn`
checks bytes but not fixups.  And x64's Intel-syntax parser reads a '$'-leading
operand (`call $foo`, `mov rax, $5`) as a symbol reference, where clang rejects it (the shared lexer takes
'$' in names for AArch64 / arm32, where clang does; an undefined, undeclared symbol still fails at the end).  And
aarch64 `bl sym@PLT` / `b sym@PLT` — and x86-64 `call sym@PLT` / `jmp sym@PLT` / `jcc sym@PLT`, since
`331b13ee4` — is accepted and the `@PLT` dropped (a plain branch fixup) on both formats:
clang rejects `@PLT` on Mach-O ("invalid specifier"), and on ELF keeps a temporary target's own symbol for it
(`R_AARCH64_CALL26 .Lg`, where we relocate against the section — the same linked result).  Fix: carry the
specifier into the fixup (a PLT branch kind), which the Mach-O writer rejects and the ELF writer keeps against
the label (found 2026-09-30 by the review of the local-labels commit).

### Loud miscompiles / wrong rejections found by the forwarder audit (not forwarder-specific) — 🟡 IN PROGRESS (claimed 2026-09-26, work-1 — user: "take on the bugs that you filed"; found 2026-09-26, work-1; agents' repros, not yet independently re-verified)

Each needs a test (xfail'd) + triage; grouped here so none is lost.
- **Generic body referencing a const/var of a package the consumer doesn't import directly** builds,
  then panics at run time "internal error: unresolved selector in IR-gen" in every mode:
  `RegisterFuncExterns` (`irgen/gen_register_import.bn:221-292`) registers only funcs; nothing
  registers a non-direct package's consts/vars.
- **Method value bound to a pointer-receiver method of a generic struct local** (`var mg = b.Get`,
  `func (b *Box[T]) Get()`) → SIGSEGV in LLVM, native and VM (non-generic works).
- ✅ FIXED (binate `b576ece21`, 2026-10-12, work-4 — the call form is now generated as `defer { <call> }`, so its
  callee resolves as an ordinary call's; conformance `spec/14-statements/191_defer_method_generic_imported`)
  **ICE `defer of an unresolved method call`** (`gen_defer_build.bn:168`) for `defer x.M()` with x a
  generic instance (local or cross-package), and for `var p = &home.S; defer p.Inc()`.
- **Cross-package non-generic method expression drops the receiver in the checker:**
  `home.S.Sum(s)` → "wrong number of arguments"; `*func(home.S) int = home.S.Sum` → "cannot assign
  *func()int to *func(S)int" (local `S.Add(s, x)` works).
  Note (2026-10-01, work-3): since the type-name-as-value check (binate, `check: a type or interface name
  read as a value is an error`, not yet landed when written), `pkg.T.M` is rejected earlier with "T is a
  type, not a value" (checkSelectorExpr checks `pkg.T` as an expression before the method lookup) — the
  error text above changes; the fix still needs the qualified-type branch before checkExpr(c, e.X).
- **Generic method expression as a value** (`var f = Box[int].Peek`) → invalid LLVM (`extractvalue
  operand must be aggregate type`), native link failure, VM SIGSEGV (local) / ICE `unresolved selector
  in IR-gen` (cross-package).
- **Bare universe `any` in an impl list or extension clause** (`impl T : any`, `interface X : any`) is
  keyed to the declaring package (`main.any`) → undefined `@__ifaceid…main…any`, VM out-of-bounds
  (`gen_impl.bn:133,347`, `gen_iface_registry.bn:166`; check the universe first like
  `isInterfaceTypeExpr`, `gen_iface.bn:73-80`) — or should the checker reject it?  Spec is silent:
  user's call.
- **The `.bn` checker rejects a forward-referenced interface parent** (`interface LSub : LBase` before
  `LBase` → "undefined: LBase"), violating `decl.order.forward` (the `.bni` scope builder accepts it).
- **`impl *home.Box[T] : Peeker[T]` in a third package is rejected** ("cannot define methods or impls
  on types from other packages"), though `iface.crosspkg.no-orphan` allows impls in any package and
  the non-generic `impl *home.S : Summer` is accepted.
- **Explicit type argument `*Iface` rejected:** `glib.Id[*Loc](s)` → "cannot assign *Loc to *Loc"
  (`@Loc` works); likely `check/check_generic.bn:212-214` building `MakePointerType(TYP_INTERFACE)`
  instead of an interface-value type.
- **A `.bni` `var V int` with no `.bn` definition** builds; a write or `&` panics at run time
  ("unresolved selector") even with a direct import.  Spec position unchecked.
- **After the native backend fails to emit a package's object, bnc still links** and reports an
  unrelated undefined symbol instead of the emission failure.
- **Spec mismatch:** `pkg.expose.dep` says a pure forwarder emits no code/storage, but its object
  defines `__Package`, `__pkg_info`, `__pkg_funcs`, `__pkg_satfrag` and `__ifaceid` copies.
- **Unverified:** the REPL (`repl/ir_imports.bn`) has no equivalent of `registerGenericBodyExternDeps`.

### Compile time is superlinear in function size — iropt mem2reg, the native allocator/liveness passes, and the compiler-wide copy-per-append `slices.Append` — 🔴 OPEN (found 2026-09-27); its native-analysis part and the residual terms work-3 took on are DONE (plan-native-analysis-scaling.md stages 1–4; binate `e3babf99e`, `ba076e293`, `8b7cac1e2` for ir.AddBlock / AddFaultPad's block lists, the assembler's symbol hash, spansClobber and buildSuccs, 2026-10-24); iropt mem2reg and the general slices.Append sweep stay OPEN (unclaimed)

**Severity: major (a single large function compiles in tens of seconds; the cost roughly quadruples per
doubling).** Found while fixing the arm32 `UnhomeID` blowup, with a generated `main` holding N copies of
`h = seed; for i := 0; i < len(xs); i++ { h = mix(h, f(xs[i], K)) }; testing.Println("bN", h)`.  With
the `UnhomeID` fix in, compile time still grows ~×3.5–5 per doubling of N on all three native targets:
-O0, N=800: arm32 5.8 s, aa64 ~7 s; -O2, N=800: aa64 21.8 s, x64 24.4 s, arm32 25.8 s (N=400: ~3.5 s).
Profiles (`sample`, top of stack):
- **-O2:** iropt mem2reg's alloca analysis (`analyzeAlloca`, `allocaDefBlocks`, `blockReferencesAlloca`,
  `blockUsesAllocaNonLoadStore`) plus `slices.Append[@ir.Instr]` — together most of the samples, and the
  same on every target (so the LLVM path pays it too).
- **-O0 native:** `DomInfo.Dominates` called from `AllocateRegisters` (~28%), liveness
  (`blockLiveBefore` + `livenessFixpoint`, ~23%), `blockReferencesValue` (~14%), `AggLoadElidable` →
  `blockAllocaOnlyLoadStore` (~7%), `sortIntervalsByStart` (~5%).
- **Pervasive:** `pkg/stdx/slices.Append` allocates len+1 and copies on every call (documented O(n)) —
  and it cannot be made amortized: a Binate slice is a view with no capacity (not a Go slice), so the
  fix is always to build such lists with `std/containers/vec` instead of `Append`;
  it has 779 non-test call sites in the compiler tree (irgen 41 files, check 25, iropt 20, codegen 13,
  parser 12, native …) plus 63 per-type `appendXxx` helpers, so every loop that builds a list sized by
  the function (instructions, values, uses, blocks) is quadratic.
**Fix:** per hot spot, replace the per-query rescans with a precomputed index (e.g. per-alloca use/def
block sets computed once; dominance by DFS pre/post numbering so `Dominates` is O(1); per-value
block-reference sets), sort intervals with an O(n log n) sort, and move function-sized list builds to
`vec.Vec` (amortized) — starting with the sites these profiles name, then a sweep of loop-built
`slices.Append` sites.  Needs a decision on scope/order.

### asm arm32: an ARM feature model gating instructions as clang does, and the forms it lacks — 🟡 IN PROGRESS (claimed 2026-10-26, work-2; stage 1, the feature model, DONE binate `09f41e4f7`; stage 2's steps 2a, data processing, DONE binate `4925d2d0c` and 2a2, `:lower16:` / `:upper16:`, DONE binate `37cfd247a`, 2a3, the `.` label's symbol order, DONE binate `31c5e7c60`, 2b, the multiplies and divides, DONE binate `5fa9deadf`; see the done log; found 2026-10-25, work-2, the `.ARM.attributes` work; user: "Attributes now, gating next (Recommended)")

clang gates arm32 instructions by the target's features, which `.cpu` and `.fpu` change: a CPU without VFP (generic,
arm7tdmi, xscale) rejects VFP instructions, a Thumb-only CPU (cortex-m*) switches to Thumb mode (bnas: an error), `.fpu
softvfp` / a d16 FPU narrows VFP, and the architecture version gates the rest.  bnas has no feature model, and its
arm32 text parser has only ~55 mnemonics (data processing, loads / stores, block transfers, branches, mul / mla /
umull / smull, sdiv / udiv, clz, nop, svc, bkpt) — no VFP, NEON, barriers, exclusives, media / saturating / bitfield,
system or coprocessor instructions — and asm/arm32's encoders cover only what the native backend uses (the basic A32
set and ~40 VFP forms).  Named gaps so far: `vmov.f64 d0, #1.0`, d16–d31 (`vadd.f64 d20, d1, d2`), `dmb ish`,
`blx <label>` ("expected register for BLX").  ARM's `.arch <name>` (`.arch armv7-a`), which sets the architecture
version, collides with bnas's own `.arch arm32` and is to be settled here.
User (2026-10-26): "Staged series, gating first (Recommended)" — **(1)** a feature model (architecture version, FPU,
extensions, set by the target, `.cpu`, `.fpu`) gating the current set as clang does; **(2)** the complete A32 core,
an exact, range-checked encoder layer like aarch64/isa plus its parser; **(3)** VFPv3 / v4; **(4)** NEON — each landed
separately, with clang accept / reject goldens.  Then back to TLS stage 3 and the rest of the queue.  Plan and
clang's facts: plan-asm-arm32-isa.md.

### asm ELF: a listed generated label is named `.Ltmp<n>` counting only the labels bnas lists, where clang counts every temporary — 🔴 OPEN, unclaimed (found 2026-10-29, work-2, the review of the arm32 `.` symbol order)

`1: nop` / `.short (1f - 1b), 2f` / `1: nop` / `2: nop` on arm32 ELF (R_ARM_ABS16 keeps the label): clang's symbol
and relocation are `.Ltmp2`, bnas's `.Ltmp0`.  bnas numbers only the generated labels it lists
(`elf_symtab.bn` ltmpName); clang numbers each temporary it creates (here the third numeric-label instance is
`.Ltmp2`).  **Fix:** number generated labels as clang numbers its temporaries, probed against clang where a
listed one follows unlisted ones (numeric labels, pool entries, `.` labels).

### asm aarch64 Mach-O: `adrp x0, .@PAGE` / `add x0, x0, .@PAGEOFF` are rejected — 🔴 OPEN, unclaimed (found 2026-10-28, work-2, the review of the arm32 `:lower16:` work)

bnas: "unexpected token after an operand (expected ',' or end of line)" at `.@PAGE`.  clang (`--target=arm64-apple-macos`)
takes both: ARM64_RELOC_PAGE21 against the function's symbol and ARM64_RELOC_ADDEND 0x4 + ARM64_RELOC_PAGEOFF12 for
the `add` (`.` made a symbol plus addend).  **Fix:** let a `@PAGE` / `@PAGEOFF` suffix follow `.` (and `. ± N`) as it
follows a name, checked against clang's relocations.

### asm: two general differences from clang — a symbol defined nowhere needs `.global`, and a constant cannot be used before its definition — 🔴 OPEN, unclaimed (found 2026-10-28, work-2, the arm32 2a review's probe lists)

(a) A reference to a symbol the file never defines (`b R0`, `bl SL`, `ldr r0, K`) is an error in bnas ("undefined symbol
'R0' (declare it .global if it is defined elsewhere)", from `asm.bn`); clang makes it an undefined symbol for the
linker.  (b) A constant used before its definition (`add r0, r1, #K` and then `K = 4`) is an error in bnas ("'K' is
not a constant defined before this line"); clang takes it (E2810004).  Both are rejected on all three architectures
((b) with other messages on AArch64 and x86-64) and predate the arm32 work.  **Fix:** decide each with the user
first — (a) may be deliberate (it catches a misspelt name) — then match clang or record the decision.

### asm: `.set` / `.equ` (and `.equiv` / `.eqv`) are unknown directives — 🔴 OPEN, unclaimed (found 2026-10-27, work-2, the arm32 data-processing harness)

bnas defines a constant only as `name = expr`.  clang also takes `.set name, expr` and `.equ name, expr` (probed on arm32:
`.equ K, 4` / `add r0, r1, #K` is E2810004 in clang, "unknown directive" in bnas), and has the related `.equiv`
and `.eqv`; bnas knows none of them on any architecture.  **Fix:** the directives in `parse_directive.bn` on top of
the `=` path (`parseConstDef`), each probed against clang first (`.equiv` redefining a name, `.eqv`'s evaluation).

### asm ELF: two layout differences from clang — an instruction in a data section, and arm32 `bl` to a local label — 🔴 OPEN, unclaimed (found 2026-10-26, work-2, comparing arm32 objects with clang's for the `.ARM.attributes` work)

With `.ARM.attributes` matching, bnas's arm32 objects are byte-identical to clang's (`arm-linux-gnueabihf` /
`arm-none-eabi`, `-march=armv7ve`) but for these (and the held-back first `$d`, kept by decision):
(a) An instruction in a data section (`.section data` / `.uint8 1` / `bx lr`; on AArch64 `ret`) gives the section
alignment 4 in bnas, 1 in clang (arm32 and AArch64; x86-64 matches).  (b) arm32 `bl g` / `bl<cond> g` to a label in
the same section is resolved by bnas; clang keeps an R_ARM_CALL / R_ARM_JUMP24 relocation — for a `.L` label too,
which then stays in the symbol table — so the linker can interwork or add a veneer (`b` / `b<cond>` match: resolved
by both).  The assemble tests compare those objects by their symbols only.  **Fix:** decide each with the user
(clang's choice, or keep bnas's), then follow it and make the cases full-layout goldens.

### asm text: `;` as a statement separator — 🔴 OPEN, unclaimed (found 2026-10-27, work-2, review of the arm32 data-processing step)

clang takes `;` between statements on one line in ARM code (`add r0, r1; nop`); bnas's lexer rejects the line.  Probe
which targets clang takes it on (ARM; AArch64 and x86-64 may differ — on some, `;` starts a comment or separates as
well), then split statements in prepareLine / parseLine as clang does.

### asm text: string escapes and directive-name case differ from clang — 🔴 OPEN, unclaimed (found 2026-10-26, work-2, review of the `.ARM.attributes` change)

(a) bnas's lexer decodes and checks the escapes of every string token, so a line with an escape it refuses fails
before any directive sees it; clang's lexer only skips the character after a `\`, and a directive that decodes
escapes (`.ascii`, `.asciz`, Tag_also_compatible_with) checks them then, while one that takes a string as written
(`.eabi_attribute` for every other string tag) never does: `.eabi_attribute 5, "a\qb"` writes `a\qb` in clang,
"unknown escape sequence" in bnas.  (b) `\'` is accepted in a bnas string; clang rejects it ("invalid escape
sequence (unrecognized character)", `.ascii "a\'b"`).  (c) A `\x` escape above 0xFF is an error in bnas (by its
lexer's no-truncation rule); clang keeps the low byte (`.ascii "a\x141"`).  (d) clang matches the names of its
generic directives in any case (`.GLOBAL`, `.ASCII`, `.FPU`, `.EABI_ATTRIBUTE`) but not ELF's section directives
(`.TEXT`: "unknown directive"); bnas matches every directive name as written.  **Fix:** decide each with the user;
for (a), string tokens carrying their escape errors to the directives that decode them.

### asm Mach-O: a literal-pool load of a forward label difference is rejected (`_f: ldr x1, =(_e - _f)` … `_e:`) — 🔴 OPEN, unclaimed (found 2026-10-24, work-2, review of the LC_DATA_IN_CODE change)

`bnas -target darwin-aarch64` rejects `_f: ldr x1, =(_e - _f)` / ` ret` / `_e: ret` with "a difference of locations
in different sections or atoms is not a number", where clang assembles it into the pool with no relocation.  bnas
accepts the backward form (`_e` defined before the load) and the same input for ELF.  Root cause not yet
investigated (a guess: the pool operand's forward difference does not wait for the second pass, as other forward
differences across atoms do without `.subsections_via_symbols`).  **Fix:** root-cause it; test against clang's
object, with and without `.subsections_via_symbols`.

### arm32 assembler: condition codes and some register fields are silently masked — 🔴 OPEN, unclaimed (found 2026-09-25 by the T6 b1 review; work-4's 2026-09-25 claim released 2026-10-12 — nobody is on it)

(The Operand2 path, register-offset memory shifts and operand registers, and the clang alias substitutions are
fail-loud / clang-faithful since `54592d56d`, `889e56d0f` and `b26005483`, see the done log.)  Still silent,
re-checked 2026-10-12: (b) `condBits` (`arm32.bn`) masks `cond & 0xf` for every encoder: cond 16 silently
becomes EQ, cond 15 lands in the unconditional space (a different instruction).  (c) `Bx`, `Blx`, `Mul` and
`Mla` (`arm32_branch.bn`) mask register numbers `& 0xf` with no validation (MOVW/MOVT and the VFP core
transfers were not re-checked).  **Fix:** validate `cond` in `condBits`; reuse `isCoreReg` in those encoders;
golden bnas-vs-clang tests.

### native: folded-away values still get PlanFrame slots — 🔴 OPEN, unclaimed (work-4's claim, riding the T6 LinearScan step, released 2026-10-12) (found 2026-09-25; the dispatcher silent-return part DONE binate `d9e2d82b3`, see the done log)

(`getOperand` fails loud on any fold-flagged id since `f0a7f78fe`, see done log.)  Still open: `PlanFrame`
reserves a slot for every value, folded-away ones included (`native/common/common.bn` ~162, ~222) — wasted
frame space (visible in the arm32 managed-slice destructor loops: the frame stays `sub sp,#128` after the
folded constant's slot went unused); fixed together with dropping folded values from LinearScan.

### A raw interface box of a name-less pointee carries no owning slot-0 dtor — blocks `unsafe_cast` `*I -> @I` — 🔴 OPEN (filed 2026-10-17, work-3, split out of the entry below)

A box's `(T, any)` / `(T, I)` vtable row has slot 0 = the owning dtor, which a MANAGED interface value calls
when it drops its data.  For a RAW box of a name-less pointee (isBoxableNamelessType: a pointer to a slice,
array, function value, pointer or interface value) irgen/gen_iface.bn leaves slot 0 null — "a RAW iface
BORROWS, so its any-block slot-0 dtor is never called".  That is correct today, but it is what makes the
decided `unsafe_cast` raw -> managed interface conversion (entry below) unsound: asserting such a raw value
managed yields a managed value whose drop frees the cell without destroying the pointee (e.g. the elements
of a `@[2]@Node`) — a leak, reproduced on LLVM and native when the conversion was implemented.  The dynamic
type is not known statically, so the checker cannot exclude it.  Fix: give every box row its owning slot-0
dtor, raw-only boxes included (queue the dtor body whenever a row is created), then implement the entry
below.  Also check whether gen_iface.bn's "boxing a managed pointer to an anonymous managed struct into `@any`
is not yet supported" panic is still reachable (a direct `var a @any = p` of a `@struct { q @Inner }` compiles
and runs).

### Should `unsafe_cast` convert a RAW interface value to a MANAGED one (`*I -> @I`, `*I -> @J`)? — 🔴 OPEN, DECIDED 2026-10-03, BACK-BURNERED (raised 2026-10-03, work-3, review of the unsafe_cast interface-widening change)

§8.7 lists `*T -> @T` (raw pointer -> managed pointer, asserting a management header at the pointee's
`-2W`) among unsafe_cast's additions, but says nothing about interface values.  The checker today:
`unsafe_cast(@T, r)` with `r *I` (raw interface value -> managed CONCRETE pointer) is ACCEPTED — the
narrowing branch puts no kind constraint on it — while `unsafe_cast(@I, r)` / `unsafe_cast(@J, r)` (raw
interface value -> managed interface value) is REJECTED, with the interface-to-interface message (which
advises a type assertion, and `x.(@J)` on a `*I` is itself forbidden by §11.12 `iface.assert.kind`).
Options: (a) allow raw -> managed between interface values as the interface analogue of `*T -> @T` (the
data word asserted to carry a header; a same-interface or widening vtable), lowering through the upcast;
(b) reject it, and also reject `*I -> @T` for consistency, with a message that names the problem;
(c) leave as is.  Recommendation: (a) — §8.7's `*T -> @T` already sanctions the assertion, and (b) would
take away an accepted conversion.  Whichever: the diagnostic for a rejected raw -> managed interface
conversion should say that, not "recover a narrower interface".
Decision (user, 2026-10-03: "3, 5, 8, 9: go with your recs (though for 9 probably bnlint should complain about it)"): option (a) — allow it, as the interface analogue of `*T -> @T` (the data word asserted to carry a management header; a same-interface or widening vtable), lowered through the upcast; `genIfaceUpcast`'s raw -> managed panic and the checker's "does not convert a raw interface value to a managed one" message (with its TODO) go.  Spec §8.7 bullet plus tests (identity and widening, refcount-neutral).
Back-burnered 2026-10-03 (work-3, self-drive): an implementation (checker branch, genIfaceUpcast taking
"may assert managed", conformance spec/08-conversions 028) was written and reviewed, then withdrawn — not
landed — because the assertion is unsound until a prerequisite exists.  (1) PREREQUISITE (its own entry
above, "A raw interface box of a name-less pointee carries no owning slot-0 dtor") — a raw box's
`(T, any)` / `(T, I)` vtable row leaves slot 0 (the owning dtor) null for a NAME-LESS pointee ("a raw iface
borrows, slot 0 is never called", gen_iface.bn), and for an anonymous managed struct / managed func-value
pointee no owning dtor is wired at all (a managed `@any` of one is an IR-gen panic, "FU4 Part B").  So
`unsafe_cast(@any, r)` of a raw box of `@struct{ p @Inner }` produced a managed value whose drop freed the
cell without releasing `p` — a leak, reproduced (refcount +1 on LLVM and native).  The dynamic type is not
known statically, so the checker cannot exclude it: every box row must carry its owning slot-0 dtor (queue
the body even for raw-only boxes; wire anonymous structs — `RegisterModulePendingDtor` /
`boxSlot0DtorName` skip TYP_STRUCT with no name — and managed func values).  (2) The review also found a
NAMED raw interface source (`type NB *B`; `unsafe_cast(@A, nb)`) accepted and miscompiled: the checker built
the managed form from the peeled source (cast rejects a named source's widening), and
`irbuild.EmitIfaceUpcast` reads `.Elem` without peeling, so the slot offset stayed 0 (LLVM / native crash;
the VM peels and is right) — the checker must reject a named source unless the interfaces are identical, and
EmitIfaceUpcast must peel (its own entry, "`irbuild.EmitIfaceUpcast` reads the interface off `.Elem`…").

### An expose "diamond" (`expose "P"` plus `expose "Q"` where Q itself exposes P) is an "exposed by both" error — 🔴 NEEDS DECISION (raised 2026-10-03, work-3, review of binate `bcf90223e`; code reading)

`checkExposeCollisions` (check/check_expose_collision.bn) compares the NAMES each expose surfaces, not the
declarations they denote, so a forwarder with `expose "P"` and `expose "Q"`, where Q's `.bni` itself has
`expose "P"`, reports every P name as "exposed by both `P` and `Q`" — although both paths reach the same
declaration and the second re-exports nothing new.  `bcf90223e` made a repeated `expose` of the SAME package
idempotent; this is the transitive case.  Spec `pkg.expose.conflict` (§16): "a name reachable through two
expose declarations … is a compile error", amended for the same-package case only.  Decide: (a) keep it an
error (the forwarder should drop the redundant `expose "P"`; a bnlint rule could say so), or (b) accept it
when both paths denote the same declaration (compare the defining package and declaration, not the name),
spec to match.  Needs a conformance test either way.

### Language feature (design): an annotation that keeps a function's frame from being optimized away — 🔴 OPEN, DESIGN (raised 2026-10-09 by the user, closing the debug.Callers -O2 entry: "As a todo, we can perhaps ponder an annotation that prevents the eliminiation of certain frames.")

`debug.Callers` promises no particular frames: at -O1 and above LLVM makes a tail call a sibling call on
every target (the caller's frame is gone before the callee runs), and the native inliner folds small
callees into their callers.  A program that wants a frame to show up in a captured stack (a stack-trace
anchor, a debugging helper) has no way to ask for it.  Ponder an annotation on a function declaration
that keeps its frame: the function is never inlined into a caller, and its calls are never made sibling
calls (in LLVM, roughly `noinline` plus `"disable-tail-calls"="true"`; natively, an inliner exclusion —
the native backends make no sibling calls today).  Open questions: the spelling (the existing `#[…]`
annotations: `#[build]`, `#[c_export]`); whether it constrains the function's own calls, its callers'
inlining of it, or both; what the VM does (its frames are never removed); and whether `debug.Callers`
should then promise those frames.

### A cycle through a generic's layout is reported once at each place that reads it — 🔴 OPEN (minor: one cycle reported twice; found 2026-10-28, work-7, review of the constant form of the abstract-declaration wait; reproduced on main for a type being collected; pre-existing)

`type Cyc[T any] struct { v T; a [sizeof(Cyc[T])]uint8 }` with `type N struct { v int64; a [sizeof(Cyc[int8])]uint8
}` reports "cyclic type definition involving `Cyc`" twice: in Cyc's declaration and at N's `sizeof`.  With the
constant form of the entry below (a constant read by a build its resolution started waits, binate `d9e37cfce`), a cycle
through a constant does the same: `type Bx[T any] struct { a [S]uint8; t T }`, `const S = sizeof(W[int64])`, `type
W[T any] struct { v T; p *Bx[T]; q [sizeof(Bx[T])]uint8 }` reports "… involving `Bx`" at W's `sizeof(Bx[T])` and
"… involving `W`" at S's `sizeof` (on main: one "constant definition cycle involving `S`").  An instance that still
waits is built on the flush's last round, which reports the cycle where its build reads a layout; a `sizeof` that
cannot wait — a type being collected, or a constant resolved on demand in that round (layoutNotBuilt,
check/check_layout_wait.bn) — reports it again where it reads the instance.  Fix: report a cycle once, e.g. a read
that cannot wait builds the waiting instance for the last time first, so the cycle is reported where it closes and
the read finds a built layout; unit tests for both forms.

### An array length left without a value by a reported error is taken as 0, and a second, false error follows — 🔴 OPEN (minor: an extra error after a real one; found 2026-10-28, work-7, probing the variable and function forms of the abstract-declaration wait; reproduced on main; pre-existing)

`const A = B; const B = A; func g() [A]uint8 { var r [3]uint8; return r }` reports the constant cycle and then
"cannot assign [3]uint8 to [0]uint8" at `return r`; so does `type B struct { a [sizeof(B)]uint8 }` with `func g()
[sizeof(B)]uint8 { … return r }` (the cyclic type, then the same).  The array length is POISONED — its error
already reported — but the array type is made with length 0, which the body's `return` then mismatches.  Through a
generic the same shows with the wait for a function's signature: `type Box[T any] struct { a [len(g())]uint8; t T }`
with `func g() [sizeof(Box[int])]uint8 { var r [sizeof(Box[int])]uint8; return r }` is "cyclic type definition
involving `Box`" and then "cannot assign [8]uint8 to [0]uint8".  Fix: a type whose array length has no value
because of an error already reported is the error type (or is otherwise kept from reporting again); unit tests
expecting exactly the one error for each form.

### A generic interface's method that selects a generic type's method, whose signature builds an instance naming the interface, is a false "method redeclared" — 🔴 OPEN (a valid program rejected; found 2026-10-29, work-7, review of the copied-method zero-length fix; reproduced on main; pre-existing)

`interface J[T any] { k() [len(hv.m())]uint8 }`, `type H[T any] struct { z T }`, `var hv H[int64]`, `func (h *H[T])
m() [sizeof(W[T])]uint8 { … }`, `type W[T any] struct { v T; j *J[T] }` is "method m redeclared on type H" at m's
declaration; with J declared after m, and an `impl *Impl : J[int64]` whose k returns `[16]uint8`, it is that and "type
Impl does not implement J[int64] (method `k` has wrong signature)".  With m's signature not building W (`[8]uint8`)
both compile, J generic or not.  Root cause not traced; a guess: m is defined on demand while J's instance is built
and W[int64] is not, the definition is taken back (undoBatchMethods) without its method on H's placeholder
(collectGenericMethodDecl's AddMethod), and collection's own definition then finds it there.  Fix: trace it, and take
back what the on-demand definition added, with a unit test for both forms.

### At the REPL, a statement whose build reads a declaration or a method copy still being resolved does not wait for it — 🔴 OPEN (minor: a valid statement at the prompt rejected; found 2026-10-29, work-7, review of the method-copy wait; from reading the code, not reproduced; pre-existing)

The waits for a declaration, or a copy of a generic's methods onto an instance, still being resolved
(check/check_layout_wait_decl.bn: DeclBatch.DeclStarts, declReadWaits, methodCopyReadWaits) are kept on the package's
declaration batch, and the REPL drops the batch once a prompt's declarations are checked
(check/check_pending_tentative.bn, check/checker_persistent.bn: `c.Batch = nil`), so a statement or an expression at
the prompt is checked with none.  A prompt statement `var hb H[int16]`, with `type Bx[T any] struct { a
[len(hh[T]().m())]uint8; t T }`, `func hh[T any]() *H[T] { return nil }`, `type H[T any] struct { z T }`, `func (h
*H[T]) m() [sizeof(W[T])]uint8 { … }`, `type W[T any] struct { v T; p *Bx[T] }` declared before, should then be
"undefined: m", while the same statement in a function body compiles.  The flush's suppression of its last round
(batchDeclCollecting) reads the batch too, so letting the copy wait without one would report a false cycle from a
flush inside the copy.  Fix: reproduce it in the REPL's tests first; then give a prompt's statements the state the
batch carries for these waits (the starts and DeclReadWaited), with a REPL unit test.

### An instance's `sizeof` of a type not built yet: still rejected in a `.bni`'s own declarations — 🟡 IN PROGRESS (claimed 2026-10-26, work-7/session; user: "let's take on all the open entries, starting with 4; then we can take on [T Eq[T]]") (minor; found 2026-10-19, work-7, review of the layout-wait change; reproduced; pre-existing)

Since binate `a25aeb940` an instance whose build reads the layout of a type not built yet waits for it
(check_layout_wait.bn), since binate `2de248620` so does a generic's own declaration resolved with its type
parameters abstract (check_layout_wait_abstract.bn), and since binate `d9e37cfce` so does a read of a constant by a
build its resolution started (check_layout_wait_const.bn).  Declared in a `.bni`, both forms are still rejected:
`type Box[T any] struct { a [sizeof(T)]uint8 }` and `type N struct { v int64; next *Box[N] }` in `q.bni` is "an
opaque type" at q.bni, though the same in `q.bn` compiles: a `.bni`'s types are not collected through the DeclBatch,
so N being built is not seen as in progress (next to the `.bni` `sizeof` MAJOR above).  And `type Bx[T any] struct {
a [S]uint8; t T }`, `const S = sizeof(W[int64])`, `type W[T any] struct { v T; p *Bx[T] }` in `lib.bni` is "array
length must be a constant integer" at lib.bni, with the constant form's wait in place: a `.bni`'s constants are
resolved by resolveBniConstByName (check/bni_scope_const.bn), which does not wait.  Fix: collect the `.bni`'s types,
and resolve its constants, as the batch does the `.bn`'s.

## Performance

One umbrella for all perf work. **How to measure — run the benchmarks; never
quote numbers from this file (they go stale):**

- **Native↔LLVM code-quality gap:** `perf/native-vs-llvm.sh` — the canonical
  ratio (same tree, same work: native-built vs llvm-built bnc self-compiling
  cmd/bnc). Figures quoted in pre-2026-09-04 notes were a different,
  throughput-contaminated metric — not comparable.
- **Native↔LLVM gap on the benchmark suite:** `scripts/native-vs-llvm.sh` in
  github.com/binate/benchmarks (`e47d9dd`) — builds each benchmark with both
  backends, cross-checks output, times USER CPU in interleaved, order-alternating
  rounds; `--self native|llvm` gives the A/A noise floor. Use
  `BINATE_BUNDLE=<dir>` to measure a toolchain built from `main`.
- **VM execution:** `perf/001_fib.bn` (builder-comp-int) and `perf/self.sh`
  `bni_runs_hello` / `bni_runs_bni_hello`.
- **Compile speed:** `perf/self.sh bnc_compiles_bnc`; always compare **USER
  CPU time**, not wall-clock (concurrent-worker noise has hidden real wins).
- **GOTCHA (has wasted time):** `perf/native-vs-llvm.sh` builds
  `--backend native` = the **host** backend — on an arm64 box it cannot see
  x64/arm32 codegen changes (a revert looks "neutral"). Measure non-host
  backends by static instruction/reload counting on a `--target` build, or on
  real hardware/CI.

### IR-level optimizations for large-aggregate copies — shared by every backend — 🟡 IN PROGRESS (raised 2026-09-30, work-1; user decision: "This and other optimizations are what we need"; claimed 2026-09-30, work-1)

The LLVM backend now carries a large aggregate (more than 16 scalar leaves) as memory and copies it with
rt.MemCopy / rt.MemZero (plan-llvm-bulk-aggregate-values.md), and the native backends copy aggregates too, so
the copies the IR asks for are all executed — nothing downstream removes them (clang can't see through
rt.MemCopy on bare metal, and the native backends run the IR's loads / stores as written).  Reviews of that
work found these patterns in -O2 IR; each is an IR-level (iropt) transform so native benefits equally.
Field reads of a whole load, dead aggregate stores, zero-fill-then-overwrite and dead values are done (see
claude-todo-done.md and plan-aggregate-copy-opts.md); still open:
- Copy chains: a value copied into a temporary only to be copied again.  Re-measured 2026-10-13 with bnc's
  experimental `--x-copy-stats` (`ddd1e1f4c`; table in plan-aggregate-copy-opts.md D): cmd/bnc native aa64
  makes 51,482 aggregate copies (3.4 MB static), 5,399 of them chains (604 KB: a load's own copy stored 3,187,
  passed to a call 1,258, returned 954); call results stored whole 9,700 (600 KB); loads copied and then
  passed to a call 5,832 (665 KB).  Two rows suggest a missed in-place read, to check: 2,650 copied loads of
  exactly 32 bytes with "one other use", and 1,553 copied loads read only by extracts (S-extract's shape).
  User order (2026-10-13): this entry's `var x Big = *p` (the OP_ALLOC between the load and the store keeps
  the load copied) and then copy propagation for `var y = x` (copyThen) come after the CRITICAL 32-bit-host
  entry and the MAJORs.  `var x Big = *p` is done (binate `c97a72eb7`, plan D5); copy propagation for `var y =
  x` is done (binate `25c8a5f6d`, plan D6: copyThen 1c -> 0).
- plan-aggregate-copy-opts.md D1-D4 are done (D1 `dfde1e73a` / `e62af60cf` / `dfce07941` / `35a7a4f3b` /
  `2e5cec867`; D2 `9fe09deb6` / `3c18f42d4`; D3 `69d6dcf57`, its native follow-up `d304bed5f`; D4
  `11375988e`; managed results' moves `d7f5734a2`; fault pads left out of NoZeroInit and every emit-time
  analysis `04930fe7e` / `27728fce5`).  The D table, re-measured 2026-10-12 (plan-aggregate-copy-opts.md D),
  leaves two causes: (1) a large load from an unknown source (`return *p`, `h.b = *p`) copies into a
  private region first — AggLoadElidable's S-adjacent rule stops at 16 bytes for overlap's sake; a return
  into the caller's (noalias) buffer cannot overlap, a store needs an overlap-safe large copy.  The return
  case is done (`45e8b6537`), and so is the store case (`0bf631ebd`, `0d0a2d966`, `987685d08`): a load stored
  whole reads its source in place at any size, the store copying as memmove does (claude-todo-done.md); `h.b =
  *p` is 2c on both backends, its minimum.  (2) SROA's copy-out split of a struct
  only copied whole — done (`dc0df663a`); a struct whose field is also read on its own (`t := x.n; var y = x`)
  copies once too (`f37af4e28`, `f85ed73b1`).  Measured with perf/agg-copy-table.sh (`2e852d3be`).  Their
  prerequisite D1 (by-value aggregates the platform C way, callees using their incoming bytes in place) is
  done (claude-todo-done.md; done/plan-c-abi-large-aggregates.md).
User direction (2026-09-30): the native backends are co-equal, so fixes must let native make the same
optimizations — do them in the IR, not only by handing LLVM an intrinsic.  (On hosted targets the LLVM
backend separately switches its bulk copies to llvm.memcpy / llvm.memset so clang can optimize them — a
regression fix, not a substitute for this entry.)
Measure per explorations/perf-optimization-guide.md.

### LLVM backend: a store of a large aggregate value is split into one store per element — code size grows with N — 🔴 OPEN (found 2026-09-28, work-6, review of the range-loop operand change; re-scoped 2026-10-06, work-3)

Originally: copying or releasing an array of managed elements was emitted unrolled, one sequence per
element (`var tmp [8000]@Box` took a native binary from 169 KB to 466 KB).  The retains / releases are loops
(the `__copy_arrN_…` / `__dtor_arrN_…` helpers), and the native backends now copy and zero-fill a large
aggregate with a loop (binate `01ee02873` arm32, `2fd5685fc` aarch64, `f6b4cc81a` x64): checked 2026-10-06,
`[8000]@Box` copied and ranged over three times gives 170 KB native, the same as `[8]@Box`.

What remains is LLVM-only: the same program is 522 KB on LLVM (142 KB for `[8]@Box`), because
`emitAggregateStoreFromSSAInstr` (`codegen/emit_copy_ssa.bn`) writes an aggregate SSA value (an OP_LOAD of
the whole array) to memory with one `extractvalue` + GEP + `store` per leaf: 8000 of each in `main` for one
copy (and for the zero-value construction `memset` + `load` + split store).  It is split that way to avoid
`store <T>` (LLVM's ARM EABI lowering may call `__aeabi_memcpy`), and `extractvalue` takes only constant
indices, so the split cannot be a loop while the value is an SSA aggregate.  Fix options: lower a large
aggregate OP_LOAD to an alloca'd copy made with a loop (the value then lives by address, as on native),
which every consumer of an aggregate value must then accept; or copy OP_LOAD→OP_STORE pairs pointer to
pointer when nothing writes memory between them.  Related: "LLVM backend: whole-aggregate load / store left
in sret returns …".  Unclaimed 2026-10-06 (work-3): a refactor of how the LLVM backend holds aggregate
values, not a self-contained fix.

### Standing: decide each new IR pass's VM membership in [vm-pass-set.md](vm-pass-set.md)

The bytecode VM runs a fixed pass set (`iropt.VMOptConfig`, pass_config.bn); a new pass is off for
the VM until measured. When adding or materially changing a pass, run `perf/vm-pass-costs.py`, add
its row to the living doc [vm-pass-set.md](vm-pass-set.md), and record the decision there.

### Cross-language benchmark suite (github.com/binate/benchmarks) — 🟢 in-flight

Repo scaffolded; harness + first benchmark (spectral-norm) landed. Measures
Binate — both the native and LLVM backends of the same source — against
C/C++/Rust/Go/Java/Python on shared problems, so it feeds the native↔LLVM gap
work below. Added as the workspace's `benchmarks/` submodule. Plan and benchmark
list: `plan-benchmarks.md`. Direction: add benchmarks a couple at a time.

### Native codegen quality — closing the native↔LLVM gap — 🔵 OPEN

**x64 suite baseline (2026-09-24, host x86-64 Linux — a 4-vCPU Firecracker VM, Xeon @ 2.1GHz,
no PMU so no instruction counts).** bnc built from binate `main` `abb168186`; benchmarks `e47d9dd`
`scripts/native-vs-llvm.sh`, 21 interleaved order-alternating rounds, USER CPU, native `-O2` vs
llvm `-O2 --cflag -O2` (clang 18.1.3). fasta/mandelbrot run at raised N (canonical is ~1ms/60ms;
mandelbrot at canonical N=1000 gives 4.05×, fasta has no usable ratio). A/A noise = worst
|A/A best ratio − 1| over `--self native` and `--self llvm` (11 rounds each): host contention swings
same-binary user time up to ~27% run to run, so best-of-N matters.

| benchmark | N | llvm best | native best | native/llvm best | median | A/A noise |
|---|---|---|---|---|---|---|
| binary-trees | 16 | 0.786 | 1.240 | 1.58 | 1.48 | ±2% |
| fannkuch-redux | 11 | 2.215 | 5.696 | 2.57 | 2.47 | ±8% |
| fasta | 2500000 | 0.468 | 1.233 | 2.63 | 2.49 | ±3% |
| mandelbrot | 4000 | 0.857 | 3.641 | 4.25 | 4.04 | ±4% |
| n-body | 5000000 | 5.098 | 17.435 | 3.42 | 3.26 | ±1% |
| record-churn | 8000 | 0.106 | 1.287 | 12.11 | 11.27 | ±7% |
| richards | 10000 | 0.921 | 1.611 | 1.75 | 1.68 | ±6% |
| spectral-norm | 5500 | 1.348 | 9.455 | 7.01 | 6.84 | ±5% |
| geomean | | | | 3.51 | 3.34 | |

Paired per-round ratios agree (e.g. n-body median 3.26, IQR 3.24–3.34); user+sys best-ratio geomean
3.44. Note the x64 numbers are NOT comparable with the aa64 figures recorded elsewhere in this
section (different arch and box): record-churn is 12× on x64 vs 4.75× last recorded on aa64, and
fannkuch 2.57× vs ~1.37× — a hint that some aa64-first scalar work has not reached x64 (to be
confirmed by disassembly, not assumed).


Worked example with prioritized backend steps: `plan-native-fannkuch-gap.md`
(fannkuch-redux ~3.2× native/llvm; root cause is machine-level codegen — spill-
everything lowering, no CSE/peephole/scaled-addressing/BCE — not the IR passes).

The lens is **"does it close the gap?"**, not "is it hot?" — most hot buckets
run in BOTH builds and leave the ratio unchanged. **Verified attribution
(2026-09-08, native `-O2` self-compile of cmd/bnc, host aarch64, main
`9fa1a37ff`): native/llvm wall-clock ≈ 4.19×; N/L instructions 2.51×, memory
ops 3.23×, calls 1.12×.** An adversarial per-hot-function disassembly pass split
the *active* codegen gap (excluding a ~33%-of-runtime shared floor — `rt.MemZero`
is byte-identical N vs L, plus malloc/dyld/kernel/irreducible dataflow) as
**~53% aggregate-copy, ~45% scalar spill/reload, ~1–2% vectorization**. Ranked
levers (this REPLACES the earlier ranking; see the plan-native-regalloc META
CORRECTION 2026-09-08):

1. **Aggregate scalar-replacement (SROA) + copy-propagation — THE biggest
   lever (~53% of the gap) — ✅ DONE (2026-09-18).** The full SROA line landed and
   is validated (Phase 0 eligibility, Phase 1 non-managed, Phase 2 managed
   slices/structs, increments 1 & 2, SROA-to-a-fixpoint, field-broadening,
   call-result stores, nested-aggregate fields).  The compiler's DOMINANT slice type
   `@[]@T` now scalar-replaces as both locals and struct fields, and nested managed
   structs compose.  Full arc + commits + validation: see the **"SROA line COMPLETE"**
   capstone in claude-todo-done.md.  **Post-SROA re-measurement (2026-09-20, work-3
   @ `903349e51`, `perf/native-vs-llvm.sh` cmd/bnc self-compile, host aarch64):
   native/LLVM ≈ 2.65× median / 2.73× best (N median 15.51s, L 5.85s; 7 rounds,
   within-arm spread ~±10% on a loaded box, but best- and median-ratios agree to
   3%).**  NOT a clean delta vs the pre-SROA 2.89× — that figure was WALL CLOCK,
   whereas the driver now measures USER CPU (`903349e51`), so the metric changed
   underneath it; treat 2.65× as the first user-CPU baseline, not a "2.89→2.65"
   move.  Same ballpark, consistent with the narrowing from the landed
   regalloc/SROA/refcount work.  The native backend still does ~2.6× the CPU work
   of clang `-O2` on this workload — the gap left to close.
2. **Register-allocation quality (scalar spill/reload) — ~45% of the gap.**
   🟢 spill-cost eviction (increment 1) LANDED `fb215bf79` (2026-09-17,
   work-4/temp-4); further regalloc levers open.  The landed allocator is
   whole-interval linear-scan, callee-saved homes only (~10 regs); it used to
   spill the NEWCOMER when the pool was exhausted, so a hot value round-tripped
   the stack under pressure where clang keeps it in registers (e.g.
   `livenessFixpoint` reloaded its receiver from `[sp]` on every field access).
   The two SHELVED Stage-5 refinements (caller-saved homes, copy coalescing) were
   the wrong knobs.  **Spill-cost eviction (LANDED):** when the pool is exhausted
   the scan now evicts the cheapest active (by static def+use count) if it is
   cheaper than the newcomer, keeping hot values in registers.  Measured: native
   aa64 self-compile median 18.45s → 16.03s, native/LLVM ratio 3.86× → 3.34×
   (~13% faster; LLVM unchanged).  Validated on all three native backends
   (aa64/arm32-linux/x64_darwin) + unit tests + clean adversarial review.
   **Increment 2 — loop-depth weighting — 🟢 LANDED `c82f31b6d` (2026-09-18):** each
   def/use in the spill cost is weighted by ~10^(block loop depth) via the new
   `ir.ComputeLoopDepths`, so a value used once inside a hot loop beats a
   straight-line value with a higher raw count.  DISASSEMBLY-confirmed ~3.7×
   (0.63s→0.17s) on a high-register-pressure loop (the loop's accumulator+counter
   stay in registers instead of reloading ~10×/iteration); neutral on the compiler
   self-compile (not a loop-heavy workload).  Validated 3 native modes 3037/3037/3256,
   0 fail.  (Note: an initial benchmark wrongly read "neutral" because it timed
   -O0 binaries where the regalloc never runs — always benchmark at -O2 and
   disassemble to confirm the allocation changed.)  **Open next levers:** share the
   `ComputeDom`/CFG build between liveness and loop-depth (perf, not correctness —
   currently two CFG traversals per AllocateRegisters); interval splitting; more
   homes; and closing the COMPILER's own remaining spill gap.  **Compiler-gap
   diagnosis (2026-09-18, disassembly of livenessFixpoint native vs LLVM):** native
   stores scalars to the stack ~30× more (153 vs 5) — it homes only 10 values
   (callee-saved; `CallerSaved` EMPTY) vs LLVM's ~27-register file; plus a large
   aggregate/slice-header-copy component (SROA, work-1).
   ✅ LANDED — **caller-saved homes in the ARG BANK X0–X7 via parallel-move
   (Stage 5d)** (`4eed9523a`, with the `spansClobber` fix `348cb15aa` and cleanups `9099f9c0a`).
   **UPDATE 2026-09-19:** arg-bank homes committed (`temp-4` `6bf481432`, rebased onto
   current main incl. the guards refactor + the DivCheck/BoundsCheck parallel-move port).
   The native self-compile hang that blocked it was **root-caused + FIXED** — it was a
   register-allocator bug in `spansClobber`, NOT a runtime UAF / emitRefDec issue (the
   earlier hypothesis was wrong): a live-in parameter whose block OPENS with a call had its
   range Start == the clobber position, so the birth guard `p <= Start` misclassified it as
   non-spanning and homed it in caller-saved X7, which the call destroyed (confirmed in
   `irdata.DataZero`: `n` in X7 across `rt.Alloc`, garbage `t.Width` → multi-GB
   `Assembler.Fill`).  Fix keys the birth test on `DefPos` not `Start` — **LANDED on main `348cb15aa`**
   (independent of the arg-bank commit, inert on main; adversarial review confirmed correct).  Verified: native self-compile
   completes ~10s + gen3→gen4 fixpoint; native aa64 conformance 3040/0; allocator unit tests
   + new regression pass; `DataZero` `n` now callee-saved (X28), disasm-confirmed.
   **BOTH Stage 5d commits LANDED on main:** the spansClobber fix (`348cb15aa`) and the arg-bank
   homes (`4eed9523a`).  The arg-bank marshalling got its own independent adversarial review
   (parallel moves at all call/return/refdec/guard sites, spill-then-reload param landing,
   X16/X17 cycle-temp freedom, PlanParallelMove) — **confirmed correct, no miscompiles**; native
   aa64 conformance 3040/0 on the final tree; native/aarch64 + native/common unit-test smoke
   green.  **Ratio MEASURED** (controlled before/after, `perf/native-vs-llvm.sh` cmd/bnc
   self-compile): arg-bank homes narrow native↔LLVM **3.10×→2.89× median** (native 9.52s→8.84s;
   LLVM unchanged 3.07→3.05s) — a real but modest narrowing (spill is only part of the gap).
   **Cleanups LANDED** (`9099f9c0a`): removed the dead arg-bank spill/reload
   in `emitCallFuncValue`/`emitCallIfaceMethod` (func-values/iface-values are aggregates → never
   homed), added the two-disjoint-cycles `parallel_move_test.bn` case.  REMAINING: **interval
   splitting** — see the dedicated claimed item below.  (Measured floors killed the
   X9–X15-static-partition idea: it caps at ~2–3 homes / ~15% because X9–X15 is also the
   scratch pool.  The lever is the idle arg bank X0–X7 as caller-saved homes → ~18 homes,
   ~40% of the spill cost — "Stage 5a done right": keep the X0–X7 homes 5a had, but marshal
   correctly via spill-then-reload param landing + a parallel-move at call sites, instead of
   un-homing every call operand the way 5a did.)  **The earlier "caller-saved homes is the WRONG lever / spilled values
   are call-spanning" RESULT (2026-09-18) was WRONG — it overgeneralized from ~30
   loop-invariants in ONE function.**  Instrumented the allocator to dump, per function,
   every spilled value split by spans-a-call vs not, loop-weighted, over the whole
   self-compile (6114 funcs, 371K values): **83% of spilled values and 75% of the
   loop-weighted spill cost are NON-call-spanning** (spilled only because the 10
   callee-saved homes are exhausted).  livenessFixpoint itself is 89% non-spanning spill
   cost (cns=127087 vs cs=15134; 137 of 156 spills non-spanning); every top-15 hot
   function is cns-dominated.  Reconciled vs disassembly (homes 99/255 yet 123 real
   stores) — the within-block cache is NOT hiding it.  **Why Stage 5a (`69d650f41`) still
   measured neutral: it homed in the X0–X7 ARG BANK and had to un-home every call operand
   (they marshal into X0–X7) → in call-heavy code most non-spanning values reverted to
   spilling.  Wrong pool.**  X9–X15 (7 regs, never used for arg passing) survive arg setup
   and need NO operand un-homing and NO param-permutation fix — strictly better than 5a.
   The one cost is partitioning X9–X15 between homes and the transient scratch/reload pool;
   the home count N is being picked from the measured scratch high-water (safe: too-few
   scratch loudly panics at compile time, never miscompiles, since home/scratch stay
   disjoint).  Targets ~75% of the spill cost and builds the caller-saved-pool + scratch
   partition that interval splitting needs anyway.  **INTERVAL SPLITTING is deferred to the
   FOLLOW-UP** (🔵 the remaining ~25%, genuinely call-spanning values; the landed
   range-list interval is its foundation).  SROA is DONE (652→555 instrs, 58→24 aggregate
   copies on livenessFixpoint) and did NOT move the self-compile ratio (3.38× vs inc1's
   3.34×).  See plan-native-regalloc.md "Stage 5d — caller-saved homes (X9–X15)".
2b. **Interval splitting (option B) — TRIED THOROUGHLY, ~3.5% REGRESSION, DO NOT LAND (2026-09-19, work-4).**
   Implemented caller-saved home + per-call save/restore for spanning values, then fixed every
   issue digging surfaced: (a) RefDec save/restore moved to its SLOW path (fast path pays nothing);
   (b) RefDec weight-0 in the cost model; the `SplitSpanningHomes` flag (which also caught + gated a
   real arm32 cross-arch MISCOMPILE the shared allocator change would have caused — arm32 has
   caller-saved R0..R3 and no save/restore machinery); and a reload-aware cost gate (compare
   2*SpanWeight against `ReloadBenefit` = cache-modeled reloads avoided, NOT the def+use spillCost).
   Correct throughout (native aa64 self-compiles + gen3 fixpoint; allocator unit tests + gate test).
   VERDICT via the noise-immune metric — INSTRUCTIONS RETIRED (`/usr/bin/time -l`; wall-clock and
   user-CPU-seconds were unusable on the loaded shared box): option B executes **+3.3–3.6% MORE
   instructions** to compile cmd/bnc (194.1B vs 187.3B, reproducible).  Root cause: the reload-aware
   gate barely moved the allocation (homed values are ~single-use-per-segment, ReloadBenefit ≈
   spillCost — the retention cache already had the easy reloads), and the save/restore overhead
   (esp. the RefDec slow path, executed OFTEN in a refcounting language) exceeds the reloads a home
   avoids.  A stricter gate can only approach neutral, never a win.  **Interval splitting of this
   form does not pay off for Binate — THIRD confirmation that register spill is not the remaining
   gap term.**  Kept on branch `optB-regression` (NOT landed).  The gap now lives in **instruction
   selection** and the **aggregate/slice-header copy path**; those are the levers, if the gap is
   pursued further.  (Perf-methodology note: on this shared box use instructions-retired, not time.)
3. **Inliner threshold tuning — POSTPONED; revisit AFTER SROA/regalloc.** 🔵 NOT ASSIGNED
   The `--inline-threshold` flag is landed (`3022706ce`) so the value is
   runtime-settable without recompiling the compiler. A drift-controlled
   interleaved benchmark (build bnc with its OWN code inlined at threshold X;
   then time it compiling cmd/bnc at a FIXED threshold 15) showed raising the
   threshold makes native-compiled code MONOTONICALLY SLOWER and bigger — thr 30
   0.95×, 60 0.89×, 120 0.82×, 200 0.81×; size 1.0×→1.9×. On native, inlining is
   currently a NET NEGATIVE: native's per-function codegen deficit (aggregate-copy
   + spill traffic — the SROA/regalloc problem) scales with function size, so
   bigger inlined bodies cost more. This CORRECTS the earlier "inlining gates
   SROA/regalloc" framing — inlining is DOWNSTREAM of them, not a prerequisite:
   raising the threshold only becomes a win once SROA + register allocation make
   merged bodies cheap on native (as they already are on clang — which is why
   inlining helps LLVM and WIDENED the native↔LLVM ratio). Default stays 15: the low region is FLAT — an interleaved sweep of
   12/15/20/25 found them within noise (medians 21.0/21.0/21.2/20.7s; sizes
   ~10.3-10.7MB), so nothing nearby beats 15, and degradation only starts ~30+
   (30 → 0.95×). So 15 is a reasonable default; thorough re-tuning should WAIT
   until the SROA/regalloc work lands (it changes the whole curve). The hot tiny leaves clang inlines away
   (charsEqual/streq/FnEq/LiveInterval.Start/symHash — ~600 profile samples) are
   real, but the fix is native's codegen quality, not a blanket threshold.
   Threshold-gated test debt to pay IF/when raising (the -O1-only inline paths
   have no conformance lane): (i) a non-dtor managed-aggregate result live at a
   CALLER fault via a multi-block merge-slot callee; (ii) a managed multi-value
   live across a fault; (iii) `return f()` passthrough.
4. **NOT vectorization (corrects the prior "it's clang's vectorization"
   conclusion).** clang emits ZERO compute-vector ops (no `add.4s`/`cmeq`/
   `uminv`); its ~35K q-register instructions are wide aggregate copies /
   zero-init in COLD functions, none in the hot path — ~1–2% of the gap. SIMD
   byte/word compares (`plan-native-vectorization.md`) are therefore a
   low-value lever, not the residual the regalloc plan gave up on.
5. **Codegen defects found during the attribution (file/fix independently):**
   `LiveInterval.Start` (a borrow getter) emits receiver RefInc/RefDec +
   `rt.ZeroRefDestroy`; `mul rd,i,#1` (index×1) not strength-reduced; a double
   `OP_BOUNDS_CHECK` on one access.
6. **Smaller / speculative:** float register allocation (float scalars are
   non-allocatable today — distinct mechanism); home function params (landing
   code correct but effectively dead — a param still reloads per use); arm32
   int64-in-registers; reclaim x64 RCX/RDX (clobber-modeled); rt.ShiftCheck
   cost (minor).

(Within-block retention + dead-store elimination are COMPLETE on all three
backends — aarch64's barrier unified to the safe-by-default allowlist in
`501b2d9eb`; done log. The native -O1/-O2 startup hang that blocked -O1+
measurement is fixed, `181ff6807`.)

### native FP-register homes — follow-ups — 🔵 OPEN (all three arch ports DONE; see done/plan-native-fp-register-homes.md)

Float SSA values home in FP registers on all three native backends (aarch64 D8..D15 + D18..D31,
x64 XMM8..13, arm32 hard-float D8..D15 including f32 S-views).  The ports and the finished
follow-ups are in claude-todo-done.md ("native FP-register homes (aarch64)" and "native
FP-register homes — x64 + arm32 ports and follow-ups").  The mandelbrot loop-carried-float slot
traffic once listed here as an "FP register pressure" item was unpromoted float locals, fixed by binate
`f6c82064d` (done log: "Float locals promoted to SSA").  Remaining follow-ups (each re-checked against
main `01b5a0091`, 2026-10-12):

- **arm32: fold int64/uint64 struct-field addressing** — 🔵 OPEN (a 2026-09-26 claim, work-5, released
  2026-10-12: no code was written; the design below stands).  Still true on main: FusableFieldGeps
  rejects a field wider than the word unless `allowFloat` admits it as a float64
  (TestFusableFieldInt64Arm32NotFused pins that).  The 64-bit fused field path (emitFusedFieldLoad64/Store64) is type-agnostic;
  the analysis still excludes int64 on a 32-bit target.  Minimal adversarial review (2 lenses):
  fold, no miscompile found (prototype: full native arm32 suites green at -O0/-O2); ~500 fewer
  instructions at -O2 across 16 packages; one function +1 (an int64 load is a retention barrier, so a
  dirty unhomed base can get a store+reload — a retention tweak is a separate tuning item).  User
  chose API (b): a separate default-off `allowPair` parameter to FusableFieldGeps plus ONE shared
  two-word-scalar classifier in common that arm32's isPair64Typ routing also uses (also bounds wide
  scalars on 64-bit targets).  Also: compute an alloca 64-bit field's IP address once past 4095
  (today each word re-materializes it — also affects soft-float float64 and plain int64 locals); flip
  TestFusableFieldInt64Arm32NotFused; add int64 emitter unit tests + a conformance test (int64 struct
  fields are thin in conformance).
- **x64 + arm32: fold element-GEP addressing** — 🔵 OPEN.  Only aarch64 folds an element GEP into a
  scaled register-offset load/store (FusableElemGeps, 4- and 8-byte scalar elements; no x64 or arm32
  caller); x64 (SIB `[base+idx*4/8]`) and arm32 (`ldr rd,[rn,rm,lsl #2]`) materialize every such
  address.  x64's `df75b805a` changed only how a non-SIB element size scales; it does not fold.  The
  n-body example once given here (`bodies[i].x` recomputing `mov r5,#56; mul; add` per access) is
  NOT a case this fold covers — a 56-byte element; that repetition is a separate matter (iropt has no
  CSE pass to share the element address).
- **arm32: callee-saved GP homes** — 🔵 OPEN.  arm32 homes are caller-saved R0..R3 only, so a value
  live across a call (e.g. n-body's loop indices across `Sqrt`) can't be homed and is spilled.
  Unchanged on main: `arm32AllocatablePool` is R0..R3 and CalleeSaved is empty, because R4..R10 —
  the callee-saved bank — is also the ONLY scratch pool.  Homing there means partitioning it; the
  aarch64 X9–X15 study found such a partition capped at ~2–3 homes, so measure arm32's scratch
  high-water first.
- **CSE float constants** — 🔵 OPEN.  Each float literal is its own OP_CONST_FLOAT, so equal
  constants take separate homes or slots: mandelbrot's `main` has 2.0 three times, 4.0 twice and 0.0
  four times.  Re-checked after float promotion (binate `f6c82064d`): the inner loop is now all registers,
  but the after-loop `Tr + Ti <= 4.0` still re-materializes its 4.0 from a GP slot (`ldr x9,[sp,#0x310];
  fmov d17,x9; fcmp`) while the loop condition's 4.0 sits in d14.  iropt has no CSE pass.

### native↔LLVM gap round 2 — richards/fannkuch next levers (see plan-native-codegen-gaps-round2.md) — 🔵 OPEN

Six tracks from re-profiling richards (1.57×) + fannkuch (1.68×) on current main after round 1
landed. Several are SHARED (help both + array/refcount code broadly). All non-FP (FP-scalar
homes are a separate item). Do NOT propose the refuted levers (raise inline threshold; home more
values / interval splitting). Claim by flipping to 🟡 IN PROGRESS (`work-N/session`). Measure the
native/llvm ratio on the named benchmark before/after. Full evidence: `plan-native-codegen-gaps-round2.md`.

- **T1 — refcount header via `LDUR`/`STUR [ptr,#-16]` — ✅ DONE (611a34f1d), see done log.**
- **T3 — condition/compare-branch lowering: `cmp/tst #imm`, flag-branch fusion (no `cset`), `ccmp` for
  `&&`/`||` — ✅ DONE (work-4), ccmp DECLINED. SHARED (richards+fannkuch).** Flag-branch fusion +
  immediate-cmp/const-fold landed on ALL THREE backends; aa64 `tst`-fold landed; fold analyses extracted
  to `pkg/binate/native/fold`. `ccmp` (Inc 3b) declined as high-effort/low-gain (cross-block CFG merge,
  ~≤0.3% pattern — see Inc 3b recon below). aa64 measured: fusion richards −2.8% / fannkuch −4.7% /
  binary-trees −1.0%; const-fold +richards −0.8% / fannkuch −1.6%; tst-fold +richards −0.61%.
  Executing in increments:
    - **Inc 1 — aa64 flag-branch fusion (drop `cset`+`cbnz` → `b.cond`). LANDED `b621dfc8d`.**
      `BranchFusedCmps` (backend-neutral, native/common) flags a single-use integer compare whose sole
      use is the immediately-following branch; aa64 emits CMP-only + `b.cond`, leaves it unhomed.
      Controlled same-base A/B (instructions retired, byte-identical outputs): richards −2.8%, fannkuch
      −4.7%, binary-trees −1.0%, fasta flat. Adversarial review clean; sampled -O2 native aa64
      conformance 568/0.
    - **Inc 2 — aa64 immediate `cmp #imm`/`cmn` + eliminate the folded constant — LANDED `c899204fb`.**
      `ImmFoldableConsts` (backend-neutral, native/common, parameterized by immediate range) flags a
      const used only as compare-RHS in range; aa64 skip-emits + unhomes it and rides it in the CMP/CMN
      immediate. Removes the STATE_* const materialization/spill (`classify`'s guard is now `ldr; cmp
      x7,#0xa; b.lt` — no dead mov). Controlled A/B (const-fold on top of fusion): richards −0.8%,
      fannkuch −1.6%, byte-identical. Adversarial review clean (7 vectors); sampled -O2 native aa64
      conformance 570/0. (Split `aarch64RetentionSafe` → `aarch64_retention.bn` for the 500-line cap.)
    - **Inc 2 x64/arm32 const-fold port — LANDED `5a47de6ed`.** Reused the parameterized
      `ImmFoldableConsts`: x64 folds 0..2^31-1 / -2^31..-1 into `cmp r64, imm32` (sign-extended); arm32
      folds 0..255 / -255..-1 into `cmp/cmn #imm` (rotation-0 modified immediate, conservative subset).
      Adversarial review clean (incl. the arm32 int64 no-fold-path confirmed harmless — emitInstr64
      intercepts int64 consts); x64 (Rosetta) 321/0, arm32 baremetal (QEMU) 316/0.
    - **fold-package extraction — LANDED `fbeee9123`.** native/common.bni hit the 1000-line .bni cap (a
      .bni is single-file-per-package, can't be split like .bn), so the branch/compare fold analyses
      moved to a new `pkg/binate/native/fold` package (pure refactor; common.bni 992→977, headroom for
      the fold flags). The GEP-fuse analyses stayed in common (concurrent T2 owner).
    - **Inc 3a — `tst` fold (`(a & b) == 0`/`!= 0` → single `TST`, aa64) — LANDED `5239f0b9a`.**
      `TstFoldableAnds` (native/fold); emitBinop emits TST in place of the AND, emitCompare reads Z,
      AND left unhomed. Adversarial review clean (6 vectors, incl. sub-word 64-bit-TST correctness);
      sampled -O2 native aa64 conformance 570/0. Controlled A/B: richards −0.61%, fannkuch flat.
      (Necessitated splitting aa64 compare lowering to `aarch64_compare.bn`; a concurrent worker did the
      identical split, so the landing rebase collided and was re-applied onto their structure.)
    - **Inc 3b — `ccmp` for short-circuit `&&`/`||` — DECLINED (2026-09-21, user call).** Recon: NOT a peephole —
      `&&`/`||` lower to SEPARATE blocks (block0 fuses `cmp; b.cond then` then falls through to block1
      which computes the 2nd cond as a boolean + branches in a 3rd block). ccmp requires a CROSS-BLOCK
      CFG merge (collapse 2-3 blocks → `cmp; ccmp; b.cond`) + a new Ccmp encoder + nzcv-immediate
      computation, and interacts with tst/const-fold (the 2nd cond may be a tst ccmp can't express).
      High effort, rare pattern (richards had ~1), likely ≤0.3% — the plan's smallest-gain lever.
    - **x64 port — LANDED `3b83fafc5`.** Reused `BranchFusedCmps`; fused `Setcc`+`Movzx`+`Test`/`Jcc` →
      `Jcc` on the CMP flags (x64 `condForOp` already existed). Adversarial review clean; sampled -O2 x64
      native conformance 321/0 (under Rosetta); unit test pins "no SETcc when fused". (64-bit: int64 =
      single CMP, safe.)
    - **arm32 port — LANDED `f4e919464`.** Same fusion, plus a `wordBytes` parameter on `BranchFusedCmps`
      excluding operands wider than the target word: on ILP32 an int64 compare is a multi-word
      `emitCompare64` sequence (no single flag state) that would miscompile if fused, so wordBytes=4
      excludes it (aa64/x64 pass 8 → int64 still fuses, no behavior change). Split `arm32RetentionSafe`
      out to `arm32_retention.bn` (emit_func was at the 500-line cap). Adversarial review clean (6
      vectors); native arm32 baremetal (QEMU) conformance 316/0; unit tests pin the fused CMP-only emit
      and the int64 exclusion.
- **T4 — hoist loop-invariant slice descriptors / fields out of loops — 🔵 OPEN (reduced to a native
  regalloc lever). SHARED (fannkuch DOMINANT + richards), backend-neutral part refuted.** The
  plan's original framing (alias-precise load-forwarding / LICM in iropt) was investigated and is a
  **root-caused negative** — see done log ("T4: iropt LICM-of-extract is a register-pressure trade-off").
  Summary: at `-O1` the descriptor is already a clean loop-invariant SSA aggregate (nothing for
  alias/RLE to do); the per-iteration reload is the NATIVE backend re-lowering `OP_EXTRACT` of a
  memory-homed aggregate under register pressure. A pure-iropt LICM-of-`OP_EXTRACT` helps richards
  (~2%) but regresses fannkuch (~1.5%) by extending live ranges → more spilling, and the two cases
  are NOT separable pre-regalloc (equal redundancy; only pressure differs). The commit is preserved
  (not landed). Remaining real levers: (a) native regalloc keeping a loop-invariant extracted scalar
  in a callee-saved reg **only when it pays** — this IS the refuted "home-more-values / interval-
  splitting" neighborhood, so needs a pressure model + all-benchmark A/B; (b) the richards-only
  distinct-pointee-type field-alias piece (`field_forward_analysis.bn` `storeKillsPath`) — a genuine
iropt win, ✅ LANDED `2fa428d8b` (2026-09-21) — but a NO-OP on richards/fannkuch
  (byte-identical output; the plan's `s.current` example is same-param, already handled). It is a
  correct general alias-precision improvement, all-backend conformance green + adversarial-review
  clean, that fires only on the "distinct-typed managed-ptr params, store through one between loads
  of another's field" pattern the benchmarks lack. SOUNDNESS is TBAA-dependent — see the SPEC
  QUESTION entry above; revert if the spec author rules not-TBAA.
- **T5 — loop-aware BCE via monotonic-induction range facts — 🔴 fannkuch target RETIRED as UNSOUND
  (investigated 2026-09-21, work-3); see below.** The plan's premise — the flip-loop guard `i < j`
  with `j` starting at `k = perm[0]` "provably `< len`" — is FALSE: `k = perm[0]` is an arbitrary int
  loaded from the slice, with NO compiler-provable upper bound (fannkuch is safe only by the
  permutation invariant `perm ∈ [0,n)`, which dataflow can't see). Monotonic-induction facts prove
  `0 ≤ i < j ≤ k`, `j ≥ 1` — but CANNOT bound `i`/`j` above by `len`, so eliminating either
  `perm[i]`/`perm[j]` check would drop a load-bearing bounds check (silent OOB if `perm[0] ≥ n`).
  Both backends correctly KEEP these — there is no *sound* native↔LLVM gap here (the phase-3 doc
  deferred "descending loops" as "needs more proof"; for this loop it's an *impossible* proof).
  **Option for later (if the perf is wanted):** a sound *hoist* — check `k < len` ONCE before the
  flip loop (faulting there), then drop the per-iteration checks; removes 4 instrs/iter but moves
  the fault point earlier (a fault-location semantics call the user owns). The general
  descending-induction `i < j` BCE (for loops where `j`'s start genuinely IS `< len`) is sound and
  buildable but doesn't help fannkuch, and on managed slices is blocked behind the in-flight
  managed-slice length-coalescing. `iropt/bce_loop.bn`.
- **T6 — native peephole + regalloc polish — 🔴 OPEN, unclaimed (work-4's 2026-09-21 claim released 2026-10-12; (a) and (b) done).** "Drop branch-to-fallthrough" is 🟡 CLAIMED 2026-10-15 by the native-code-placement session as part of block layout (all three native backends; `plan-native-code-placement.md`); the rest stays OPEN.** dead-load elim, drop branch-to-fallthrough,
  phi-copy coalescing, small-const immediates, don't-home register-resident params, right-size leaf
  frames. `aarch64_emit.bn`, `native/common/regalloc_*.bn`, `common.bn`. NOTE: coalescing / home-fewer
  is the OPPOSITE of the refuted home-more — validate against the interval-splitting regression.
  - **Measurement infra landed `ddc1c091a`**: `perf/007_bucket_count` (loop + inner if-chain,
    phi/spill-heavy) and `perf/008_reg_pressure` (8 reductions + min/max, pressure canary). Both
    deterministic (native == LLVM), suite-friendly iteration counts; scale up locally (×~100 iters)
    for A/B timing.
  - **Finding (disasm + A/B at -O2, the gap-defining level).** The literal "adjacent
    store-then-reload local" dead-load is an -O0 artifact — `mem2reg` promotes those locals at -O2 and
    it vanishes; a store-then-reload peephole would not move the -O2 gap. At -O2: **008's gap is
    CLOSED** (native 0.08s == LLVM 0.08s; was 2.25× at -O0). **007's gap PERSISTS** (native ~1.23s vs
    LLVM ~0.70s, ~1.75×), dominated by (1) **phi-copy explosion** — 37 reg-to-reg movs/iter vs ~13
    work-instrs — and (2) **spilled-constant reloads**: the increment `1` is materialized + spilled to
    7 slots and reloaded per-iter (`ldr; add`) instead of `add …, #1`; loop-invariant thresholds are
    likewise spilled + reloaded.
  - **Direction chosen: small-const ALU immediate-folding** (extends the landed T3 `native/fold` pkg;
    fold small ints into add/sub/and/or immediates + don't spill/rematerialize constants). Safe,
    reduces pressure (no interval-splitting regression), kills the spilled-const dead-loads. Phi-copy
    coalescing is the bigger 007 lever but touches regalloc core (regression risk) — deferred behind
    the safe immediate-folding.
  - **aa64 ADD/SUB immediate fold LANDED `f1989126b`.** `fold.AddImmFoldableConsts` marks an
    OP_CONST_INT used only as an ADD/SUB operand (ADD either operand w/ a register sibling; SUB
    subtrahend only; result ≤ word; value in [0,4095] or, sign-swapped, [-4095,-1]); it rides the
    `add/sub #imm` immediate instead of being materialized + (under pressure) spilled/reloaded.
    Controlled same-tree A/B (-O2): 007_bucket_count ~1.28s → ~1.07s (~15% faster; the increment `1`
    was spilled to 7 slots + reloaded per iter — folding it also freed the reg file, cutting phi-copy
    movs: main 451→423); 008_reg_pressure unchanged (no regression). Adversarial review clean; native
    aa64 conformance 3047/0; hygiene 20/20.  Also split RegMap flag accessors → `regalloc_flags.bn`.
    **Remaining: (a) ✅ DONE `abb168186` — add/sub-imm fold ported to x64 (signed imm8/imm32,
    no operation swap) and arm32 (rotation-0 modified-immediate, sign-swap negatives, wordBytes=4
    excludes int64 pair-add).  Shared analysis reused; per-backend helpers in x64_fold.bn /
    arm32_fold.bn.  Native conformance x64 3048/0, arm32 3002/0; unit x64 316 / arm32 405; x64 disasm
    confirms `addq $0x1` fires.  (b) AND/OR/EOR logical-immediate folding — ✅ DONE `b2aa6d919` (see done
    log); (c) phi-copy coalescing (the bigger 007 lever — regalloc-core, regression risk) — 🔵 OPEN,
    unclaimed.**
  - **Plan (decided 2026-09-25):** (b1 ✅ `96b39fd89`/`54592d56d`/`d35da4a89`) fail-loud fixes for the encoders on the fold's path; (b2 ✅ `f0a7f78fe`) replace the
    per-kind compare/add folds with ONE `fold.ImmOperandConsts(f, fits)` analysis + one `FoldedImm` flag
    (per-backend predicate + shared encoding helpers; uniform width guards) and make `getOperand` fail loud on
    fold-flagged ids; (b3 ✅ `b2aa6d919`, with the arm32 file split `fa9647492`) the AND/OR/XOR immediate fold on all 3 backends (+ aa64 `tst a,#k`, XOR-all-ones →
    MVN/NOT, arm32 BIC); then folded values out of LinearScan/PlanFrame; then the assembler hardening sweep;
    then (c).  Each step lands separately.
  - **Survey findings (2026-09-25, T6(b) understand pass; aa64 -O2; counts from disassembly cross-checked
    against the -O2 IR):**
    - **Logical-immediate opportunity:** 295 AND/ORR/EOR/TST sites with a constant source across perf
      001-008 + the 8 macro benchmarks + 32 bit-twiddling conformance programs (222 encodable, 95 in loops);
      1178 in `bnc` itself (1067 encodable, 666 in loops).  Constants are never interned (IR-gen emits one
      per literal; 94% have a single use): of 1485 constants with a logical use, 1439 are used only by
      logical ops, so an exclusive-use rule suffices for (b).  The aa64 tst-fold's AND operand is a constant
      in 55 of 71 cases (53 bitmask-encodable) — the tst path must emit `tst a, #k` once those constants
      fold.  Biggest non-encodable group: XOR with all-ones (→ MVN / NOT).
    - **Folded values still take part in LinearScan** — 🔴 OPEN, unclaimed (work-4's claim released 2026-10-12;
      re-checked then: intervals are still built for every id, a folded one only skipped when homes are recorded)
      (`native/common/regalloc_scan.bn` ~321-355; intervals
      built for every id, fold flags only consulted after assignment): a folded constant can hold a pool
      register it never uses for its whole interval — shown on aa64: one shared add-folded constant (10 uses
      in a loop) left x6 idle while 4 accumulators spilled every iteration; the 10-separate-constants
      version used x6 and spilled 3.  Fix: drop fold-flagged ids from the intervals / spill costs before
      LinearScan and from PlanFrame.  Affects every existing fold; directly relevant to (c).
    - **Nil compares are not folded** (the compare fold only accepts OP_CONST_INT): 5762 unfolded
      compare-only zero constants in `bnc`'s disassembly (4632 in loops) — a larger opportunity in `bnc`
      than the logical fold.  Fix: treat OP_CONST_NIL as immediate 0; better, fuse compare-with-zero +
      branch into `cbz`/`cbnz`.
    - **Strength-reduced MUL/DIV/REM constants are still materialized and homed** (aa64
      `aarch64_muldiv.bn` consumes them via `constDivisor` but never marks them folded): 648 in `bnc` (566
      mul, 82 div/urem); some are stored to slots never read.
    - **Cross-kind constants** (✅ folded since `f0a7f78fe`): folding a constant whose uses span kinds (compare + add) would catch 289
      more in `bnc` (all the value 1 in managed-slice destructor loops).
    - **(-O0) a constant LHS operand is materialized and then overwritten** (x64, e.g. `0x0F & x`: a
      dead `mov r13, 0xf` immediately overwritten by the reload of x; gone at -O2) — an IR/regalloc
      artifact, not the fold; found by the b3 x64 review.
    - **iropt does no integer constant folding** of all-constant binary ops / compares, nor identities
      (x&0, x&-1, x|-1, 0-x), nor NEG/BITNOT of a constant — so `x & -16` / `x & ~15` reach the AND as a
      unop-of-constant and miss any immediate fold; 79-102 both-constant logical ops survive to codegen.

Order: T1 → T2 → T3 → T4 → T5 → T6.

### record-churn residual is SROA-pinned aggregate copies, NOT the SIMD ceiling — findings 2026-09-24 — 🟡 IN PROGRESS (claimed 2026-09-29, claude/exciting-davinci-wahyt2 session; SROA copy-out split + dead-phi elimination ✅ LANDED `9da1662f`/`9c934585`, see done log)

Profiled on x64 (callgrind instruction counts; no PMU in the VM) + static aa64 disassembly of a
cross-built object, bnc from main `abb168186`, `--emit-llvm` for the shared IR. x64 record-churn is
12.1× native/llvm user CPU (see the x64 suite baseline above). Per inner-loop element: **x64 native
217 instrs (137 mem ops, 105 of them stack), aa64 native ~150 (static), LLVM 25 (0 stack ops)**;
whole program N=4000: native 3.86G vs LLVM 0.92G instructions. LLVM's SLP (fields 4–7 in `xmm`)
accounts for only ~4 of its 25 instrs.

**CORRECTION** to the round-3 conclusion (claude-todo-done "residual is now essentially the
integer-SIMD ceiling") and `plan-native-vectorization.md`'s "record-churn residual is `add.4s` SLP":
the dominant residual is scalar — after `mix` is inlined, TWO `Record` allocas stay in memory in the
loop (the inlined callee's `m` and the caller's `var m`), on BOTH native backends: each iteration
zero-inits both (8 field stores each), stores fields, whole-reloads, does three 32-byte copies
(callee m → caller m → `out[i]`), and reloads 8 fields back into the carry. On x64 the 4-byte field
stores feeding 16-byte `movups` reloads are also a likely store-forwarding stall (fits time ratio
12× > instruction ratio 8.5×). The latch's ~60-instr phi-copy shuffle on x64 is register pressure
that mostly follows from the same live aggregate state.

**Re-profiled 2026-09-29 (bnc main `f4570cfdf`): the aggregate-copy premise above is RESOLVED** — the
optimized IR's inner loop has no `Record` alloca and no aggregate copy (8 field loads, 8 ops, 8 field
stores). x64 native/llvm user CPU is ~4.5× (0.19s vs 0.04s at N=4000). What remains, by backend:
- **x64 (126 instrs/element): register allocation.** x64 homes only RBX/R12–R15 (5 regs,
  `x64AllocatablePool`); R10/R11/RCX/RDX/R8/R9/RDI are a fixed scratch pool and `CallerSaved` is
  deliberately empty. The loop has ~18 live scalars (8 carried fields, 8 loaded fields, index, bound),
  so most round-trip `[rsp+..]` every iteration. aa64 closed the same gap with caller-saved arg-bank
  homes (Stage 5d); x64 has no equivalent.
- **aa64 (67 instrs/element, static): nearly register-resident.**
- **Both:** (a) the `arr`/`out` managed-slice header allocas' ADDRESS is materialized (`sp+off`),
  spilled and reloaded, then data/len are reloaded — loop-invariant (the "managed-slice header
  reloaded" item below); (b) the elided aggregate load's element address is stored to its slot
  and reloaded within the iteration.  (Latch phi-copy moves are gone since 8ef99bd39; field loads
  sit at their uses since ac6d08b92; dead eviction stores are gone since 6de81730a.)

- **n-body is ~90% software `math.Sqrt` on BOTH backends — 🔵 OPEN (found 2026-09-24).** callgrind:
  native 88%, LLVM 90% of instructions in `math.Sqrt`'s bit-by-bit loop (neither emits `sqrtsd` /
  `fsqrt`); the source notes "a hardware sqrt intrinsic may replace this as a fast path later". So
  n-body's native/LLVM ratio is essentially the codegen gap on that one integer loop, and both
  backends are far from C (which uses `sqrtsd`). A hardware sqrt (per-arch asm or an intrinsic the
  backends lower) is the large lever for n-body; the loop's native codegen (spilled loop-carried
  values, shift counts reloaded into `cl` from stack slots) is the gap lever.
- **aarch64 `emitCallIndirect` diverges from `emitCall` (latent)** — 🔵 OPEN (found 2026-10-07, work-3, by the
  review of the x64 counterpart's fix).  `pkg/binate/native/aarch64/aarch64_call_indirect.bn` `emitCallIndirect`
  keeps its own argument loop: a float past D7 is dropped (`if nsrn < 8`, no stack store — the slot is reserved
  but never written), an HFA goes down the GP path (`emitCall` handles HFAs), stack arguments are always stored
  8 bytes wide, and it keeps sret / aggregate / multi-return collect paths that cannot work (PlanFrame gives
  OP_CALL_INDIRECT no data region; they now fail loud through `NeedAlloc`).  Unreachable from real programs:
  OP_CALL_INDIRECT lowers only the `_call_shim_*` intrinsics, whose arguments are words and whose result is a
  scalar or nothing.  Fix as on x64: share `emitCall`'s argument placement and fail loud on an aggregate result
  (arm32 already does — it shares `emitCallArg` with `emitCall`).

- **Native: an elided aggregate load's address lives on the stack, not in a register** — 🟡 IN
  PROGRESS (claimed 2026-10-01). When `AggLoadElidable` lets a load alias its source, the load's
  value is just the source address, but aggregates are never register-allocatable, so the address
  is written to the load's spill slot and reloaded at every extract (record-churn: `arr[i]`'s
  address stored and reloaded each iteration). Make an elided load's address an allocatable
  pointer value on all three native backends.
  - **Re-measured 2026-10-04 on the x64 RCX/RDX homes + the non-SIB GEP fix — still a net loss; parked.**
    record-churn N=300 x64: 7,263,549 → 8,253,016 instructions (+13.6%) with the address allocatable
    (mix loop 56 → 67 instructions). The address takes R14 and three carry lanes spill (stack slot
    store+reload each, plus latch copies). Root cause unchanged: the spill cost (9 uses × loop weight)
    overstates what spilling the address costs (1 store + ~2 reloads, since the retention cache reuses
    a reload across consecutive extracts). Copy-partner eviction pricing alone: neutral (+0.1%).
    Revisit with a spill-cost model that accounts for retention reuse, or after RAX / RDX-in-hot-code
    homes add registers.
  - **Investigated 2026-10-01 — not a net improvement as-is.** Implemented
    (all three backends; set `AggAddrRegLoads` = elided loads whose every use is an extract's
    aggregate or a store's value, threaded into liveness and handleResult). record-churn N=50 x64
    instructions (callgrind): 797,726 → 830,672 (+4%). Root cause: register pressure. The x64 home
    pool is 9 registers; the mix loop has 8 carry lanes + `i` + the address + the out GEP. The
    address takes a register, and the scan then cascades: an untied latch value (cost 300) evicts
    a lane phi (cost 210), whose own latch value then evicts the next — 4 lanes spilled instead of 1.
    Pricing eviction by value + direct copy-tie partners stops the cascade (805,878, +1%), but the
    address still displaces a lane, and its spill-cost (9 uses × weight) overstates the real cost
    of leaving it spilled (the scratch reload cache made it ~1 store + 3 reloads). The pricing
    change alone (address off): 798,634 (≈ neutral). Code parked locally (not landed).
  - The hot loop is the mix loop (N² iterations; the checksum and seeding loops run N times), and
    its pressure is the 8 carry lanes + `i` + the address + the out GEP against a 9-register home
    pool. The structural fix is a larger home pool (RAX/RCX/RDX/R10/R11 are scratch-only; LLVM
    allocates all 15). (The x64 `imul`-immediate fold frees the checksum loop's 8 multiplier
    registers, but that loop is cold.)
- **native code placement (x64, aarch64, arm32): deliberate loop/function placement policies** — plan: `plan-native-code-placement.md` (tooling T2-T5, then block layout, then policies; open decisions listed there) —
  🟡 IN PROGRESS (claimed 2026-10-10). Identical machine code runs at very different speeds depending
  on where it lands: `blockLiveBefore` (byte-identical, same instruction count) is 12.5% slower in one
  native bnc build than another; fannkuch-redux is 12.7% faster with identical instruction counts,
  and the one visible difference is that its inner loop (0x5f bytes, 16-byte-aligned header) spans
  3 instead of 4 32-byte fetch windows (hypothesis, not yet tested). Today functions and loop headers are 16-byte aligned (`X64_FUNC_ALIGN`/`X64_LOOP_ALIGN`,
  LLVM's generic x86-64 values). Per-pair self-compile timing varies ±1–3 s (2–6%) with code
  placement (per-function padding) and stack placement (environment size), which makes A/B results
  of ~1% unreadable. First test: does aligning small hot loop headers to 32/64 bytes (when the pad is
  small) make the base fannkuch build match the faster one? This VM exposes no hardware counters
  (no cycles / front-end stall events), only cpu-clock sampling (`perf` from
  linux-tools-6.8.0-31). Blocks the decision on turning `-fx64-rax-home` on by default (see the done
  entry for `edeabc7d1`): on this Xeon it ran −0.21% instructions but ~+1.3% user time on the native
  self-compile, and mixed benchmark results.
  Progress: tooling commit 1 (hidden `-fnative-place-seed` / `-fnative-place-mode`, all three native
  backends) landed as binate `417f660bd` (2026-10-16); tooling commit 2 (static report
  `perf/placement/layout.py`) landed as binate `f05342e5d` (2026-10-18); tooling commit 3 (timing driver
  `ab.py`, analysis `stats.py`) landed as binate `4f9e3c75b` (2026-10-18); done entries for all three.
  Phase-0 calibration ran 2026-10-19/20 on the x86 VM (results in the plan); its follow-ups landed as
  binate `15f9d7ac4` (usage text test), `ffe4a8e3d` (Ctrl-C tests) and `5ff452880` (ab.py page-cache drop,
  seed-by-seed builds); the Holm correction of the selection guard (user decision 2026-10-20) landed as
  binate `3f95bef54` (2026-10-28). Next: block layout (design in the plan; its questions decided 2026-10-30),
  then the placement policies; a JCC-erratum mitigation experiment is motivated by the blockLiveBefore
  result. User
  decisions on commit 3 (2026-10-18): keep the selection order (the Decisions rule, geomean first), keep
  the 1% text budget summed over workloads, and make ab.py refuse a reference run under 0.5 s of CPU
  unless --allow-short-runs (binate `0a6a3aeaf`). Tooling commit 4 (the placement probe,
  `-fnative-place-probe=SYM -fnative-place-probe-phase=P`, plus layout.py / ab.py support) landed as
  binate `053d743c1` (2026-10-19). Pending user confirmation: the placement switches are now also an error
  with `--emit-llvm`. Pending user decision: on Mach-O, `--expect-seed` in shift mode fails only when inexact rows
  outnumber exact ones (dead-strip moves functions); the exact alternative is an option that reads the
  kept object files.
  Open (found 2026-10-18 by `layout.py --expect-seed`): phase mode does NOT survive a dead-stripped Mach-O link
  by ld64.lld. ld64.lld aligns each atom only to the largest power of two dividing its offset, so once a
  stripped atom (or the unnamed leading-filler atom) is dropped, whole objects move off their phase (97 of 244
  functions in fannkuch-redux; the same objects linked without -dead_strip all pass). `place.bni` and
  `perf/placement/placehash.py` claim the opposite and must be corrected. Unverified: Apple ld64 / ld-prime,
  which bnc uses on macOS; run `layout.py BIN --expect-seed S` on a Mac build to check. If it also loses the
  phase, the compiler needs a fix (e.g. a symbol on each leading filler, or no subsections in seeded builds).
- **native backends keep every copy of a weak function: no per-function section / COMDAT group** — 🔵 OPEN.
  The LLVM path emits each generic instantiation and other shared definitions as `weak_odr` in its own COMDAT,
  so the linker keeps one body. The native backends (x64, aarch64, arm32) put weak functions in the object's
  single `.text`; the link binds the symbol to one copy but every other copy's bytes stay. In the x64 native
  `cmd/bnc` (built -O2 by bnc at binate `417f660bd`) 494,535 of 13,591,036 text bytes (3.6%) are code outside
  any function, and 51 weak functions (e.g. `stdx/slices.Append` and `containers/table.Table.Remove`
  instantiations) have block labels in bodies other than the kept one (`perf/placement/layout.py` reports the
  outside-function bytes). A native-vs-LLVM gap in code size and i-cache footprint, and it shifts placement.
  Fix: emit each weak (and linkonce-style) function in its own section in a COMDAT group (ELF `SHF_GROUP` /
  `.text.<sym>`; Mach-O relies on atoms plus coalescing, check what the linkers do), on all three backends and
  in bnld; add a link test that two objects defining the same instantiation produce one body.
- **LOW: `bnc --keep-objs` keeps no package objects when building a program** — 🔵 OPEN. `cmd/bnc/main.bn`
  removes the compiled `.o` files after the clang link unconditionally (and the bnld path keeps only the start
  stub), so `--keep-objs` ("keep intermediate .o files") is honored only by library and `--test` builds. Seen
  2026-10-18 while trying to inspect native objects. Fix: guard every removal on `cli.KeepObjs`; add a cmd/bnc
  test.
- **bnld writes no symbols for defined functions (Mach-O or ELF)** — 🔵 OPEN. A program linked with
  `--linker bnld` has a symbol table holding only its undefined imports (Mach-O `buildMachoSymtab` in
  `pkg/binate/link/dynmacho.bn` writes one UND nlist per import; the ELF output has no `.text` symbols at all),
  so profilers (`perf`, `sample`), lldb backtraces and `perf/placement/layout.py` cannot name any function in
  it. Seen 2026-10-18: fannkuch-redux linked by bnld for x86_64-linux and aarch64-darwin. Fix: emit a local
  (or global, per the input binding) symbol for every defined function and data object, as clang/ld do, with
  sizes on ELF; add link tests that read the symbols back.
- **LOW: `bnc --build-dir <dir>` with a nonexistent `<dir>` reports only "native backend failed to emit
  object (arch=...)"** — 🔵 OPEN. Each object write fails because the directory is missing, but the
  message names neither the path nor the cause (seen 2026-10-15 while validating the placement tooling;
  the conformance/perf scripts create the directory first, so they never hit it). Either create the
  directory or report the failed path and errno; add a cmd/bnc test.
- **LOW: assembler has no arm32 UDF encoder or parser; aarch64 UDF is reachable only through
  `asm/aarch64/isa.ExceptionGen` and the text parser** — 🔵 OPEN. Comprehensive-assembler rule: add an
  arm32 `Udf` encoder + parser and a public aarch64 `Udf`. Found while designing placement filler
  (which uses BRK/BKPT instead).
- **native allocator: a tie hint is lost when the first-allocated partner takes a register the
  other cannot use** — 🔵 OPEN. `LinearScan` (`native/common/regalloc_linear_scan.bn`) honours a tie
  (`pickPartner`) only if the partner's register is eligible for the later interval; nothing steers
  the FIRST-allocated interval toward a register its partners can also take. Seen in record-churn's
  mix loop while freeing RDX (fixed there by `981151c9b`, which stopped native no-ops declaring
  RCX/RDX): lane phi `v842` (live only in the loop header) took RCX, but its update `v391 = add v689
  v842` was live across an instruction declaring RCX/RDX (an `sp_restore`), so it could not share
  RCX — the hint failed and the loop gained a two-address copy and a back-edge copy (+2 instructions
  per iteration, record-churn +2.5%). Any per-register clobber (`RegClobbers`, also callee/caller
  spans) can split a tie this way. Possible fixes: when picking a free register for an interval with
  tie partners, prefer one eligible for the partners' intervals too (they are known up front); or
  allocate tie groups together. Reproduce: `rc/main.bn`-style loop with a value carried across a
  statement end and a declared clobber inside the loop body.
- **x64 `rt.MemZero`** (zeroing each `make_slice`) is a 4×-unrolled 8-byte store loop reloading its
  zero constants from 4 stack slots — 11.6% of native instructions; LLVM uses glibc `rep stosb`.
  Covered by the x64 MemZero/MemCopy item under native vectorization (A) (being worked on
  separately) — not duplicated here.

### native vectorization (SIMD) — V1 asm encoders FIRST (see plan-native-vectorization.md) — 🔵 OPEN

Resurrected + re-scoped 2026-09-21 (FP-register homes just landed → a float register class exists;
the visible vector frontier is record-churn ~4.75× integer `add.4s` SLP + the FP kernels + the
always-wanted memory primitives). Endpoint is full native↔LLVM parity, so these are planned, not
profile-gated. Much is INTEGER SIMD (record-churn, memory primitives) → not gated on the deferred
FP-arithmetic work. Full plan + sequencing: `plan-native-vectorization.md`.

- **V1 — SIMD asm encoders + vector-register model — 🟢 aa64 + x64 COMPLETE; arm32 NEON deferred.** The
  foundation for the two priority arches is landed (unblocks the (A)/(B) tracks below); fully
  unit-tested (assemble → assert bytes vs clang). Per arch, independently landable:
  - **aa64 NEON** (`asm/aarch64/aarch64_neon*.bn`): ✅ COMPLETE (core `03141db1e..be6f4ecd4` +
    follow-ups `a722230e4..e325a88b1`; see done log).  Comprehensive per the user directive: V-register
    model + arrangements, packed int/bitwise, vector load/store (+post, +LDUR/STUR for signed offsets),
    lane ops, packed FP, the FULL MOVI/MVNI/FMOV modified-immediate matrix, and multi-register LD1/ST1
    (1–4 regs).  The latent Add/Sub negative-immediate footgun was fixed as part of this.  Golden tests
    vs clang throughout.
  - **x64 SSE2** (`asm/x64/x64_sse*.bn`): ✅ COMPLETE (landed `7a01b88ae..0fe9ac7ac`; see done log) —
    packed int/bitwise (Padd*/Psub*/Pmullw/Pmulld/Pand/Por/Pxor/Pandn), packed FP (Add/Sub/Mul/Div/Min/
    Max/Sqrt ps/pd, Cmpps/pd + CMPP_*, FP-bitwise), packed moves (Movdqu/Movdqa/Movaps + load/store),
    Rep_stosb/q, and shuffles/interleave (Pshufd, Shufps/pd, Movddup, Punpck*, Unpck*, Pshufb).  Golden
    tests vs clang; adversarial-reviewed (no bugs).
  - **arm32 NEON — 🔵 DEFERRED (2026-09-22, user: "defer it, for now").** `asm/arm32` has scalar VFP
    but no Advanced-SIMD (NEON) layer.  Building it is a substantial A32 NEON encoder layer (its own
    D/Q-register encoding scheme, distinct from aa64); NEON is optional/absent on many arm32 targets
    (baremetal), where the existing scalar path is the universal fallback.  Pick up when arm32 SIMD
    is actually wanted — not a blocker for the (A)/(B) tracks, which start on aa64/x64.
- **(A) SIMD memory primitives — IN PROGRESS (claimed work-2).** Off V1, FIXED vector regs (no vector
  regalloc); hand-`.s`, `#[build]`-gated. Metric: native ABSOLUTE time hits the `bzero`/inline-NEON bar.
  - **aa64 `rt.MemZero` → DC ZVA — ✅ LANDED `d962b76e4`** (~2.5× on 1 MiB fills; perf/009_memzero).
  - aa64 `rt.MemCopy` → wide `ldp/stp q` — 🟡 IN PROGRESS. Parser bridge ✅ LANDED `2329b187c` (text
    assembler now parses vector-`q` ldr/str/ldp/stp → V1 Vldr_q/Vstr_q/Vldp_q/Vstp_q). REMAINING:
    (i) split `MemCopy` out of rt_managed.bn into a `#[build(!is(arch,"aarch64"))]`-gated rt_memcopy.bn
    (mirror rt_memzero.bn); (ii) write memcopy_aarch64.s (wide ldp/stp q + tail); (iii) measure
    (perf/010_memcopy) + adversarial review + land.
  - Follow-ups from the bridge review (not blockers): (a) the text assembler + V1 ldr/str/ldp/stp
    encoders SILENTLY mis-encode out-of-range/misaligned offsets (imm9 `&0x1ff`, imm7 `/16 &0x7f`,
    scaled `/16`) — pre-existing, affects the GP path too; harden with encoder-level `a.SetError`
    (like the GP unscaled path). (b) add parser tests: GP ldp/stp fall-through, and rejection of
    pre-index/reg-offset/label/mismatched-reg vector-q forms (behavior verified correct, untested).
  - aa64 `rt.MemZero` hardening — 🟡 IN PROGRESS (claimed 2026-09-25, work-2): memzero_aarch64.s
    returns silently on size < 0 instead of aborting (rt.bni contract) → tail-branch to rt.Panic like
    memcopy_aarch64.s; the MemZero test caps at size 95, never reaching the DC ZVA path (>= 256) →
    add a wide-size test.
  - x64 `rt.MemZero`/`rt.MemCopy` → `rep stosb`/wide-SSE / `MOVDQU` — 🔵 OPEN (needs the x64 text
    assembler taught `rep stos` + `movdqu`).
  - (`MemCompare` profile-gated.)
- **(B) arithmetic SIMD — the parity work (large), roadmap:** B1 vector register allocation (width-
  generalize the landed FP-register class) → B2 SLP vectorization (pack struct-field/adjacent scalar ops
  — record-churn's `add.4s`) → B3 loop auto-vectorization (FP kernels; sequence last).
- **Idiom recognition** (between A and B): recognise memset/memcpy loops in compiled code → lower to (A).

Order: V1 (aa64 first) → (A) → idiom recognition → B1 → B2 → B3. Each independently landable/measurable.

### IR optimization passes (help LLVM + native backends + the VM) — 🟡 OPEN

- **Pass infra + mem2reg + BCE** — design settled
  (`plan-ir-opt-passes-bce.md`, `plan-mem2reg-phase2a.md`): bnc `-On` gate
  (distinct from --cflag, implying it for LLVM); phases (1) infra+gating,
  (2) mem2reg-lite, (3) bounds-check elimination. Constant-index BCE landed;
  mem2reg is Phase 2a. BCE is also the top lever for bni LOAD time
  (rt.BoundsCheck ≈ a third of load self-time) and helps VM-executed code.
- **Multi-way-branch IR construct:** `genSwitch` lowers to a linear if-else
  chain; LLVM -O2 jump-tables it, but the native backends and the bytecode
  path (no BC_SWITCH) stay linear. Add a dense-integer multi-way branch
  lowered per backend (LLVM `switch` / native jump table / `BC_SWITCH`).
  Baselines landed: perf/003_dispatch_switch vs 004_dispatch_ifchain. (A
  jump-table rewrite of the VM's own execLoop dispatch is LOW value since the
  dispatch reorder — cheap inline comparisons remain.)
- **Opt-level conformance-matrix dimension (CI; agreed 2026-08-27):** the
  passes only run at -O1+, conformance runs -O0 → no end-to-end coverage.
  Make optimization level a matrix dimension. **BLOCKER for the LLVM lane:**
  `clang -O2` reddens ~200 conformance tests (managed/refcount/dtor/iface/
  fmt) — NOT the IR passes (the same tests pass on native -O2); likely latent
  UB/strict-aliasing that clang exploits — its own investigation, a real
  correctness concern. Reproduce: `BINATE_FLAGS=-O2 ./conformance/run.sh
  builder-comp`. The native -O2 lane is clean modulo the mem2reg grounding
  fix.

### VM execution speed (bni) — 🔵 OPEN (unblocks the double-VM lane)

A faster VM lets the full double-VM lane return and speeds every VM lane.
Landed so far (done log): dispatch reorder `835ec63bc` (~1.7× on fib),
execArithOp float-bail fix `63676b720`. Open:

- **Re-land the same-function frame-skip (un-revert `9c7ef5518`, ~12% fib).**
  It was reverted (`0d5f786a8`) for an int-int regression whose real cause —
  the VM leaking `vm.SP` on raw aggregate call results — is FIXED
  (`20c51d0ca`); the optimization itself is correct. Gate the un-revert on
  builder-comp-int AND builder-comp-int-int + pkg/binate/vm unit tests.
  Still open after re-landing: the 5 colder frameLocals sites (needs a
  vm_exec.bn split — it is at the file-length cap), pushFrame's frame-header
  write + register zeroing, and the @Vec receiver RefInc on vm.Funcs.Get.
- **VM-internal bounds checks** (`regs[]`, `code[pc]` — several % of fib and
  growing): tactical `unsafe_index` on the proven-safe hot paths (register
  indices validated at load; pc bounded) as a stopgap, vs waiting for the
  compiler BCE pass above (the real fix — but note the `code[pc]` check is
  NOT IR-BCE-eliminable; it stays a tactical case either way).

### bni load time — 🟡 OPEN (levers need a design discussion)

Loading toolchain-sized graphs (parse → typecheck → IR-gen → lower) is
memory-management-bound (profile in the done log: ~two-thirds
alloc/zero/free — `rt.MemZero` via the generic `rt.Alloc` scales with total
allocation VOLUME — plus rt.BoundsCheck ≈ a third of self-time). Measure at
-O2. After the O(n²) sweeps (done log), the levers are:

- **Bounds-check elision** — the IR BCE pass above.
- **Cut allocation COUNT** — design-level; do not pick unilaterally (partly
  owned by others).
- **lookupFunc*/lookupFuncSig per-lookup allocation (small):** call sites are
  consolidated (`3d078c4c2`, done log) but each remaining lookup still does a
  CopyStr + qualify-concat per call — intern the qualified key or hash the
  (pkgPath, name) components without materializing it.

### Double-VM (`*-int-int`) lane — 🟡 stopgap in place

GREEN via the representative-subset stopgap (`083e1f334`; the full saga —
skip rounds, test-level sharding, the two O(N²) registration fixes — is in
the done log): types+ir run test-sharded plus all cheap packages; every
compile/run-heavy `.split.vm` package (codegen, vm, native/*, asm/*, lint,
bnlint, bnfmt, parser, irdata) is skipped in THIS lane only (their logic is
covered by the single-VM and native lanes). Open:

- **Re-add the heavy packages once VM execution is faster** (section above) —
  or make the explicit per-package call that double-VM adds no coverage over
  single-VM for it (strong for the compiler-side packages, which `-int`
  already runs through one VM; weakest for `pkg/binate/vm` itself — prefer
  per-test skips there over losing its lane entirely).
- Residual mitigations still in tree for when packages rejoin: codegen's
  `TestEmitDebug` per-test skip (the DWARF path is unprofiled — profile
  before guessing; a 2026-05-13 cache attempt was a net LOSS, done log) and
  `pkg/asm/aarch64` (unprofiled, same hypothesis).
- Tune the int-int shard count / 45-min cap down once stable;
  `build_interp_arm32` is still -O0 (possible -O2 follow-up).

## Standard library — pkg/std namespace migration

### Finish the containers move: BUILDER-tree imports, the examples repo, then delete the flat forwarders — 🔴 OPEN (gated on the next BUILDER release + `BUILDER_VERSION` bump)

The seven containers live at `pkg/std/containers/{vec,iter,table,mapfn,set,setfn,hashmap}`
(binate `083146cf1`); `expose` forwarders at the old flat `pkg/std/<name>` paths keep the rest
resolving.  Every non-BUILDER-tree consumer already imports the new paths.  Remaining, in order:

1. **After the next BUILDER release is pinned** (cut only when independently justified —
   never for this): bnc-0.0.15/0.0.16 bundle only the flat paths, and gen1 resolves the
   stdlib from the BUILDER's bundle, so the BUILDER-compiled tree must keep the flat paths
   until a BUILDER carrying `pkg/std/containers` is pinned.  Then switch its 41 import lines
   (`vec`/`iter`/`table`/`mapfn` in pkg/binate/{asm,check,ir,irbuild,irdata,iropt,link,
   token,types} — `git grep -nP 'pkg/std/(vec|iter|table|mapfn|set|setfn|hashmap)\b'`
   lists them; `stdlib-forwarder-imports.sh` exempts exactly these via the computed
   BUILDER tree).
2. **The examples repo** (pinned to a released bnc) imports the flat paths in
   `containers/{cmd/wordcount,pkg/tally*}`, `files/cmd/tour`, `fmt/pkg/table*` and two
   READMEs; move them once examples bumps to a release that has `pkg/std/containers`.
3. **Delete the seven forwarders** (`ifaces/stdlib/pkg/std/<name>.bni`) once nothing
   imports them.  The hygiene checks need no edit (they auto-discover forwarders).
- The irgen forwarder-resolution bug that made the forwarders unsafe for out-of-tree importers
  is fixed (binate `a7331ce01`).

## Documentation hygiene

### examples/files/README.md says "There is no `defer`" — 🟢 LOW (found 2026-10-12, work-4, `defer { … }` step 1)

`examples/files/README.md:56`: "every open in the tour has a matching close. There is no `defer`;
deterministic release is the language's answer".  Binate has `defer` (the examples' pinned `bnc-0.0.16` has
the call form `defer f()`; `bnc-0.0.17-pre2` and later have `defer { … }`), so the sentence is wrong whichever
version it describes.  Fix with the examples' next bnc bump: say the tour closes each file explicitly (or use
`defer { f.Close() }` once the pin carries the block form).

### Language spec doesn't state the package-path syntax the toolchain enforces — 🟢 minor, needs a decision (found 2026-09-27, work-3)

§16.2/§16.3 define a package path only as a `string_literal` (the package clause / import path), but
the loader rejects any path that is not a `/`-separated sequence of non-empty `[A-Za-z0-9_]` segments
("invalid package path …", `pkg/binate/loader/loader_load.bn` via `mangle.IsValidPackagePath`) — so
`package "ev.il"` is a user-visible compile error the language spec never mentions (the ABI spec,
§5.2, now documents it).  Decide whether the language spec should state the rule (a new constraint
rule-ID in §16.2, e.g. `pkg.clause.path`, with an `.error` conformance test) — it is a language-level
restriction, so it's the user's call, not a doc tidy-up.

### ABI spec — first version AUTHORED (docs 2fc2b2e); follow-up decisions open — 🟡 (2026-09-04)

The ABI spec now exists: `docs/abi/` (sibling to `docs/spec/`), 7 chapters +
index — scope/model (three convention layers; cross-producer
interchangeability), calling convention (per-target registers, sret,
multi-return, sub-word canonical form), dispatch convention (shim seam,
handle contract, trampolines, 7-slot cap), C boundary (type mapping,
c_export/__c_entry mechanics, arm32-linux HFA deviation), symbol naming (the
bn_ grammar), linkage/object format (bindings, sections, relocs, startup),
runtime-at-ABI-level (Draft; manifest stays gated on spec §20.2). Grounded in
a 7-reader implementation recon (2026-09-04). Marked Provisional — "ABI not
declared stable". Remaining owner decisions:

1. **Rule-ID wiring**: `abi.*` IDs use the standard lede grammar but are not
   in `rule-ids.txt` (extractor scans docs/spec/*.md only) nor the §4.5
   prefix table. Extend the apparatus, or leave the ABI spec un-extracted?
2. **Move vs cite** for the C-type mapping squatting in
   `pkg.cexport.signature` (16b): the ABI spec §4.2 states it with 16b as
   coordinate authority; slimming 16b to a citation is a language-spec edit
   needing ratification.
3. **Ratify spec-over-LLVM authority** for the empirically-pinned legs
   (multi-return register budgets incl. x64 x87 ST0/ST1; arm32
   sret-pointer-returned-in-R0): abi/01 §1.2 Note claims the spec is now the
   authority; confirm.
4. Whether/when any part gets declared **Stable** (abi §1.4).
5. `ir-backend-guidelines.md` rehoming (separate entry below): the ABI spec
   now covers its calling-convention/mangling/linkage material; the IR-vs-
   backend responsibility-split guidance still needs a home.

**Adversarial review DONE (2026-09-04):** 7 reviewers, 349 claims verified,
49 findings; all spec-text fixes applied (docs 6c27343 — incl. correcting
the stale main-native/deps-LLVM build claim, the retbuf sizing contract,
the multi-return-not-C-replicable mapping, buffer alignment rules, and the
coalescing-vs-TU-local symbol split), 16b status staleness fixed (ad91a26).
Implementation gaps found by the review are raised under MAJOR as the
"ABI review #1–#7" entries; the review's owner-decision items are the
"ABI review #8–#12" entries below.

### Code comments reference only normative docs + TODOs; rehome the implementation "specs" — 🟡 OPEN

Policy (in effect): code comments must not reference plan/design/notes docs.
The only doc references allowed in comments are **normative docs** (the
specification under `docs/spec/`) and clearly-labeled TODOs. Plan/design-doc
pointers (`plan-*.md`, `design-*.md`, `notes-*.md`) are being stripped repo-wide
so each comment stands on its own (Comments Stand Alone). Deferred follow-ups:

1. **`ir-backend-guidelines.md` needs a real home.** It is an implementation
   "spec" (the authoritative IR / backend / layout boundary), currently just a
   loose `explorations/` doc. Code-comment references to it are **kept for now**
   (treated as normative). Give it a proper home — a spec annex or a docs/
   implementation-spec section — so those references point at a real spec.
2. **`pkg-layout-spec.md` needs splitting + cleanup.** It mixes external
   (normative) and internal (implementation) specification; split the two and
   clean up. Code-comment references to it are **kept for now**.
3. **`claude-notes.md` code references (~41) — replace with spec references
   where they belong in the spec.** During the comment-sweep, pure-pointer
   `claude-notes.md` references whose comments stand alone are stripped; where a
   comment genuinely needs the normative content, the pointer should be replaced
   with the corresponding spec reference rather than deleted. Any such
   references left un-stripped by the sweep are tracked here.

## Test-flake watch

Intermittent, load-/environment-dependent test failures tracked for recurrence —
NOT known defects and NOT critical.  Before treating a red one as a real
regression, **re-run the named test in isolation.**  Each entry notes the date(s)
observed.

### `spec/11-interfaces/052_alias_same_identity` — suspected environmental one-off (observed 2026-07-10)

One failure during a saturated multi-mode `builder-comp` sweep; passed 3/3 in
isolation and clean in the concurrent `builder-comp-comp` run. The test is
deterministic (exact `"ok"`), `builder-comp` has no per-test timeout, and tests
run sequentially within a mode — so the lone red was almost certainly a transient
OS-level hiccup under load, not a real defect. A recurrence will reveal it.

### arm32 iface shape-test intermittent LP64-doubling flake (observed 2026-07-06) — suspected REAL bug, needs investigation

`TestEmitImplVtables{NonExtending,ExtendedConcat}Shape` (`arm32_iface_test.bn`)
~1/50 in the full ordered native unit run (never in `--run` isolation) fail relro
byte-counts with EXACTLY LP64-doubled values (24→48, 72→144) — ILP32 `IntSize=4`
not in effect at emit. Root cause UNKNOWN (target-global leak or a real gen1
emission-nondeterminism bug); guard `3ca73110` pins it, and do NOT widen the tolerance.

## Method values & function values (codegen)

### cross-mode coerced-agg func-value ABI — residual native-shim follow-ups
The cross-mode coerced-aggregate-ARG residuals — the iface/func-value by-address
fix, the >7-arg extern guard, and the sub-word/bool RETURN — LANDED via the by-address
ABI rework (`233cc82d`) + the >7-arg guard (`17cfc16b`); see claude-todo-done.md. An
observable native-struct-return-into-by-value-extern fixture (`dd3d8b59`) landed too.
Smaller follow-ups remain:

1. **shim-extends RETURN (cleanup, optional).** The sub-word RETURN was fixed VM-side
   (the 25117a2e VM-narrow mechanism extended to iface/func-value), since the sub-word/bool
   RETURN concern is VM-only. The review's cleaner shim-extends design (every backend's shim
   sext/zext's sub-word returns; drop the VM narrow) is deferred — a multi-backend,
   target-word-dependent change with a tail-branch→call-shape wrinkle.  Plan +
   per-backend shim sites + verification: [plan-funcvalue-shim-extend.md](plan-funcvalue-shim-extend.md).

(The x64 closure-shim soft-length split and the conditional func-value spill staging are
✅ DONE & LANDED — see claude-todo-done.md.)

See explorations/done/plan-funcvalue-byaddr-abi.md.

## Cross-mode interface dispatch & compiler/interpreter interop

### `__init` dispatcher (+ other main-enumerated structures) assume whole-program enumeration — remaining blockers for opaque binary distribution — 🟡 OPEN

The **satentry-registry** whole-program-enumeration defect is **fixed** — decentralized into
the per-package `_pkg_satfrag` graph (all three phases landed; see the done log +
[`plan-rtti-decentralize.md`](plan-rtti-decentralize.md)), which also validated the
decentralized dependency-graph-of-fragments approach that
[`plan-stacktraces.md`](plan-stacktraces.md) reuses. But that fixed only ONE of Binate's
whole-program-enumeration points. Still open for full **opaque binary distribution** (a
closed-source `{facade.bni, bundle.a}` whose internal packages the consumer never names):

- **The `__init` dispatcher** (`__init_all`, built from `initPkgNames` over the driver's
  `ldr.Order`, `cmd/bnc/main.bn`) has the identical main-enumeration shape — a facade-hidden
  package's top-level initializers would be absent for an opaque-blob consumer, exactly as
  its satentries were before the PoC. Decentralize it the same way (the `_pkg_satfrag` graph
  is the template: a per-package init-edge graph walked at startup).
- **No wired/tested Binate-consumes-a-prebuilt-`.a` path** exists (`--library` targets C via
  `bn_init`, not Binate import). **Archive-inclusion trade-off (2026-08-22, `a7cfb5169`):** the
  dep-frag edges are no longer STRONG undefined refs — they are now WEAK-DEFINITION fallbacks
  (an overridable empty node) + a STRONG real node, because the strong undefined refs made a
  package's object un-linkable standalone (they broke `--pkg`/facade/partial links — the
  ffi-export e2e). The weak model is required for standalone linkability, but a weak ref does
  NOT drag its dependency's object out of a static archive the way a strong ref did. So the
  side-effect that satfrag strong edges *would* have force-included a satentries-only internal
  package's object from `bundle.a` is GONE. When the blob-consuming build mode is actually
  built, it must ensure ALL internal package objects are included by an EXPLICIT mechanism
  (e.g. link the whole archive / an inclusion manifest), not rely on satfrag edges as a
  side-channel. (This property was already "asserted-but-unverified" — no test ever exercised
  it — so nothing tested regressed; but the future mode must not assume it.)
- **A consumer compiling against a library's `.bni` alone** (seen 2026-10-07, work-3, while
  making a `.bni`-only package's vars extern): the consumer's module for the `.bni`-only package
  still defines that package's descriptor symbols (`__pkgname`, `__pkg_info`, `__pkg_funcs`,
  `__pkg_globals`, `__Package`, `__pkg_satfrag` — emit_pkg_descriptor.bn), duplicating the
  library object's; and its `bn_init` never calls the library's `__init` (`HasPackageInit` is
  false for the consumer's module), so the library's var initializers do not run.  Linking
  `main.o` + the consumer's other objects + the library's object, leaving out the consumer's
  module for the package, works today (a shared var reads the library's value).

### Package descriptors — Phase C (richer metadata) + VM extern auto-enumeration remain

The general per-package `reflect.Package` descriptor incl. the `Functions` table
(one `reflect.FunctionInfo` per exported func — Name / Sig / RetbufSize / ParamSlots
+ a function-value handle) is **delivered** for user packages across LLVM, all three
native backends, and the VM (record + coverage in `claude-todo-done.md`). What
remains:
- **Phase C — richer type metadata** ([`notes-package-introspection.md`](notes-package-introspection.md)):
  grow the descriptor beyond `Functions` to expose Types / Impls / Consts / Vars for
  user-facing reflection, plus fuller RTTI.
- **VM extern registration**: `RegisterStandardExterns` (`pkg/binate/interp/externs.bn`)
  still hand-registers the BUILTIN packages' native-runtime externs (rt runtime, lang
  RTTI) rather than auto-enumerating a cross-package registry. These externs are
  native-runtime injection (legitimately special), not the user-package interop table
  (done) — so re-evaluate whether full auto-enumeration is still the goal before
  pursuing it.

### Compiler/interpreter interop — MAJOR PROJECT — 🟢 substrate + descriptor + general Functions-table LANDED; Phase C + VM auto-enumeration remain

Dual-mode execution substrate is LANDED: shared-layout/refcount cross-mode interop, function values (`{vtable,data}` rep + shims + `dispatchCompiledFuncValue`), the `reflect.Package`/`__Package()` descriptor with a populated per-package `Functions` table (LLVM + all three native backends + VM, user packages included; `conformance/532`/`725`/`727` green), cross-mode dispatch coverage, and VM extern registration (`RegisterStandardExterns`, `pkg/binate/interp/externs.bn`).

Remaining (LIVE tracker is the "Package descriptors" entry above): Phase C richer type metadata / RTTI (Types / Impls / Consts / Vars); and — optionally — replacing the VM's hand-maintained `RegisterStandardExterns` builtin native-runtime injection with a cross-package auto-enumeration registry (re-evaluate: those externs are legitimately special, so this may no longer be wanted).

Dormant cross-mode func-value residual (folded in from the retired "Function values — residual follow-ups" entry): the one trampoline ARG shape not yet covered is **float args in V/FP registers** — nothing reaches it today (float scalars ride the integer banks; aggregate returns use `TrampolineAggregate`, ILP32 i64 returns use `TrampolineScalar64`, and >7 args fail loud by design, `17cfc16b`). Add a float-V-reg trampoline if/when a path actually needs it.

(Background/history archived in claude-todo-done.md.)

### `repl.Kernel` reshape (embeddable REPL → request/reply kernel) — Inc 1 ✅ LANDED; Inc 2/3/4 parked — 🟡 OPEN (2026-07-16)

`pkg/binate/repl` was reshaped from a line-push read-loop
(`Init`/`Step`/`ReplIO`) into a request/reply **`Kernel`** (`Execute` +
`IsComplete` + `KernelInfo` + `Complete`/`Inspect` + `RunReadLoop`; notices /
errors returned as `Result` DATA, not a sink). **Inc 1 is ✅ DONE & LANDED** on
`main` (`6910166f`..`6fa25ae5`, plus the e2e ordering-pin `f17ea5dc`) — verified
green (repl + cmd/bni unit tests, hygiene 17/17, `e2e/repl.sh` 56/0) and hardened
by a 3-lens adversarial review (which caught two land blockers, fixed pre-land).
Plan + full design: [`done/plan-repl-kernel.md`](done/plan-repl-kernel.md).

Remaining increments (all parked, none started):

- **Inc 2 — `Complete`** (tab-completion) and **Inc 3 — `Inspect`**
  (introspection): ⏸ DEFERRED (2026-07-16, user: not needed currently) — the
  interface stubs stay. Both need NEW `pkg/binate/types` API (a **shared,
  BUILDER-tree** package): a `Scope`-enumeration API for `Complete`, and `Symbol`
  doc/signature retention for `Inspect`. That shared-package API is a design
  decision to settle before starting either.
- **Inc 4 — result display** (`Result.Display`, the `Out[n]` value echo): future
  — needs a new `pkg/replprint` pretty-printer (was gated on interfaces+generics,
  which have landed).
- **Evaluated-code output / stdin capture** — deferred to package-impl injection
  (`done/plan-repl-kernel.md` Decision #4); untouched. Full side-effect capture is
  impossible in general.

## VM runtime faults & the rt.Exit/abort/panic paradigm

### VM user-code faults — residual follow-ups (Plan 2 core is DONE) — 🟡 OPEN

Plan 2 (`rt.Abort`/`rt.Panic`) made all six VM user-code faults (bounds / divide /
shift / nil-deref / stack-overflow / call-through-nil) RECOVERABLE — the host
(REPL / test-runner / embedder) survives a bad interpreted program while compiled
code stays fatal.  Core landed (Plan 1 primitives; Inc 1/2a/2b/3 cleanup-pad
unwind; nil-deref N1–N3 last, `de9a7c05`); see claude-todo-done.md and
[`plan-rt-abort-panic.md`](done/plan-rt-abort-panic.md).  Still open:

- **Native-extern SIGSEGV is unguarded (filed 2026-06-30).** A bad-pointer deref
  inside a NATIVE EXTERN called from the VM (e.g. handing a wild pointer to
  `rt.Refcount`) SIGSEGVs the VM host with no guard — it is not one of the six
  guarded VM user-fault sites, and there is no signal handler in `pkg/binate/vm` /
  `cmd/bni` / `rt`.  Recoverable faults stop at the outermost `execLoop`; a fault
  under a live native callback stays fatal (mid-callback gate, needs heap frames),
  so this native-extern boundary needs a host signal handler to be recoverable.
- **Route panic / `runtime error:` / VM diagnostics to stderr (fd 2)** — deferred
  out of Plan 1 (infra exists: `bootstrap.Write(fd)`, `bootstrap.STDERR = 2`); a
  real behavior change for anything scraping them off stdout.
- (Separately filed under MAJOR: the re-entrant-`execFunc` fault-swallow.)

## 32-bit-host toolchain: IR constant width & VM machine word

### Baremetal console output is unwired — `os.Stdout` is an empty `@File`, so `fmt` is silent; make it PLUGGABLE — 🔴 NEEDS DECISION (found 2026-09-18)

On bare metal, `impls/stdlib/pkg/std/os/os_baremetal.bn` defines `os.Stdout` /
`os.Stderr` as EMPTY `@File` handles ("a bare-metal target has no standard
output"), and baremetal `File.Write` unconditionally fails (no filesystem).  So
anything writing through `os.Stdout` — notably `fmt.Print*`, which the generated
`bnc --test` runner uses for its RUN / PASS / FAIL / summary lines — is silently
dropped.  A baremetal unit-test run therefore emits NO diagnostic output; only the
process exit code is observable.  This actively bit: a plain filesystem-test
failure in `pkg/binate/native/arm32` looked like a mysterious "memory leak"
because the failing test's message was invisible (see
[claude-todo-done.md](claude-todo-done.md), landed `7cdc667a4`).

By contrast `testing.Println` works on baremetal: it goes through
`testing/sys.WriteStdout`, whose baremetal impl (`sys_baremetal.bn`) calls
`semihost.SemihostWriteChar` (SYS_WRITEC) directly, bypassing `os.Stdout`.

Goal (owner-directed): wire baremetal `os.Stdout`/`os.Stderr` to a console in a
PLUGGABLE way — there are many baremetal configurations and the console sink
differs: semihosting SYS_WRITEC (what `qemu -semihosting` exposes), a
memory-mapped UART / serial console (e.g. PL011 on `qemu -M virt`), or genuinely
nothing.  The board/target should select the sink; `os.Stdout` routes to it
rather than being hardcoded empty.  `testing/sys` (target-gated WriteStdout) is a
partial precedent but is testing-specific and bypasses `os.Stdout`; the general
fix is an os-level console abstraction that `fmt` (via `os.Stdout`) also flows
through.

Decisions to settle: is `os.Stdout` a console-writer type rather than a `@File`?
How is the sink selected per board — a `#[build]`-gated `os_baremetal_<board>` (as
existing target gating does), or a runtime-registered writer the crt0 / board
init installs?  Support both semihosting and a real UART.  Once wired, the
`--test` runner's fmt output becomes visible on baremetal (exit-code-only runs
become name+message diagnostics).
Recommendation (work-3, 2026-10-06, self-drive triage): a runtime-registered console writer that the
board / crt0 installs (semihosting by default under `qemu -semihosting`), with `os.Stdout` / `os.Stderr`
writing through it — one image can then target several boards.  Needs the user's call on the questions
above.

### `data_pkg_descriptor.bn` header/slice-width conflation — 🟢 LOW (non-urgent cleanup)
The `GetTarget().IntSize` "footgun" was a MISDIAGNOSIS and the native-accessor header reads
were switched to `ManagedHeaderSize()` (main `581216d9`) — see [claude-todo-done.md](claude-todo-done.md).
Residual: `data_pkg_descriptor.bn` (IR-gen phase) still uses one int-sized `w` for BOTH the
managed-header words (pointer-sized) AND slice lengths (int-sized) — a documented "assumes
PointerSize==IntSize" conflation, harmless on every shipping ABI. Untangle header (→
`ManagedHeaderSize`/ptrSize) from slice-length (→ IntSize) only if a wide-int ILP32 ABI is targeted.

**Do NOT mistake this for a quick width-swap.** Two reasons it stays deferred, not just small:
(1) **Untestable until a `ptr≠int` target exists** — every current ABI has PointerSize==IntSize
(LP64 8/8, ILP32 4/4), so the emitted bytes are byte-identical before/after on every backend and
mode; no test can distinguish a correct fix from a buggy one, and this is a memory-layout contract
(both backends emit it, `reflect.Package` readers consume it) — the worst place for a silent,
unverifiable error. (2) **A correct version needs explicit padding, not just widths** — the payload
is four raw slices `{data: ptr, len: int}`; when `ptr≠int` each `len` no longer fills to the next
pointer's alignment, so `DataZero` padding terms are required between `len` and the next `data` (the
current flat-`DataTerm` sequence emits none, relying on `2*w` spacing). Do it WHEN a wide-int ABI is
built, together with a test that exercises `ptr≠int` (the only thing that validates it).

## Slimming `pkg/bootstrap`; C interop (`__c_call`)

### Eliminate the last C runtime shim + native syscall allocator (libc-free) — 🟡 OPEN (future)

`runtime/binate_runtime.c` + the native-test stub `native_test_stubs.c` are
deleted (`6f58f32fd` / `53fe13137` — see the done log); bnc links no C runtime
(pure-Binate `pkg/builtins/rt` + `startup`). Residual goals of the now-archived
[`done/runtime-abstraction-plan.md`](done/runtime-abstraction-plan.md) (Phase 3;
steps 3.1–3.3 shipped, the rest delivered via a different architecture — `rt_stubs.c`
gone, `pkg/rt` calls libc via `__c_call`, entry-point at `startup._entry` `c4607a71`,
libc/baremetal impls split). Follow-ups:
- **Retire the `--runtime` no-op.** bnc still *accepts* `--runtime` (its file is
  never linked; its dir only anchors the bare-metal crt0.s/semihost.s/linker
  script via `dirOf(--runtime)`), and every runner/e2e/build script +
  `binate-paths --runtime` still passes/emits one — because the pinned BUILDER
  (`bnc-0.0.12`) REQUIRES a real `--runtime` file to link and links its own
  bundled one. Once `BUILDER_VERSION` is bumped to a bnc built from `6f58f32fd`
  (no `--runtime` requirement), drop the flag (bnc `RuntimePath` + parse), the
  `binate-paths --runtime` selector + `BINATE_RT`, and all runner/e2e/build
  `--runtime` args — and migrate the bare-metal crt0.s/semihost.s/baremetal.ld
  delivery off `dirOf(--runtime)` to a flag-free anchor (primaryRoot +
  `runtime/baremetal_arm32`, or `--link-after-objs`).
One goal remains genuinely unbuilt:
- **Native syscall allocator for bare metal** — step 3.7's optional pure-Binate /
  syscall-backed allocator so a truly libc-free bare-metal image (no `__c_call`
  into libc) can allocate. Matters most for the native-arm32 bare-metal path.

### `pkg/std/os/process` — v1.1 residual: exec-failure precision — 🟡 OPEN (low, post-1.0)

The `bootstrap.Exec` → `pkg/std/os/process` migration is **fully landed** (Phase A
`0d0b3a62`, Commit 3 `786f8feb`, Commit 2 `62b4a828`, Commit 4 `91f56d47`; see the
done log — `bootstrap.Exec` is gone from the tree). Remaining is a v1.1 quality
gap (design §6): a `+x` non-executable/bad-format file passes the parent-side
`sys.Accessible` (`access` X_OK) check, then execve fails child-side and surfaces
as the child's `_exit(127)` rather than a typed start error; a self-pipe (write
end `O_CLOEXEC`) would report the exact errno. Also `access(X_OK)` accepts a
searchable directory.

### aarch64-linux **native** conformance mode (e2e for the aarch64 ELF relocs) — 🟢 MODE LANDED (`e8c99290`, 2026-07-09); residuals below

The native aarch64 **ELF** data + GOT relocations (`ADD_ABS_LO12_NC`,
`LDST64_ABS_LO12_NC`, `ADR_GOT_PAGE`, `LD64_GOT_LO12_NC`) landed in `9e866a43`
— fixing a MAJOR silent-`R_AARCH64_NONE` miscompile (see `claude-todo-done.md`)
— were clang-byte-verified (`objdump`) + unit-tested but **not link+run-verified**.
The `builder-comp_native_aa64_linux-comp_native_aa64_linux` mode (`e8c99290`)
now closes that: gen1 compiles each test `--backend native --target aarch64-linux`
and runs it under qemu-aarch64 on the x86_64 CI runner (`gcc-aarch64-linux-gnu`
cross-libc + `qemu-user-static`), analogous to the x64-linux `builder-comp_native_x64`
runner. It exercises the aarch64 ELF path — and the `__c_global` §5b GOT lowering
— end-to-end. Wired **experimental** (continue-on-error) in
`.github/workflows/conformance-tests.yml`.

**Residuals (🟡 OPEN):**
1. **First-CI-run triage — 1st pass done, awaiting a clean run.** The debut run
   (push `e8c99290`) reported 492 pass / 2203 fail, but ~all failures were one
   runner bug — `qemu-aarch64-static: Could not open '/lib/ld-linux-aarch64.so.1'`
   (dynamically-linked binaries; qemu-user looked for the loader on the host, not
   the cross sysroot). Fixed by `QEMU_LD_PREFIX=/usr/aarch64-linux-gnu` in the
   runner (`2f97732b`), mirroring arm32_linux. The NEXT CI run is what shows the
   aarch64 native backend's real pass/fail once the loader resolves → then compute
   the xfail set / fix real bugs → drop `experimental` once green. Not runnable on
   the macOS dev host (no aarch64-linux cross-libc / qemu).
2. **Native arm64 runner via a cross-compiled `linux-arm64` bundle (option 1) —
   🟢 plumbing + release-wiring LANDED, `linux-arm64` bundle now PUBLISHED
   (first shipped by `bnc-0.0.14`); awaiting a native runner.**
   Done: `build-{bnc,bni,bnas,bnlint,bnfmt}.sh` + `make-bundle.sh` gained a
   `--target`/non-host-`--platform` cross-compile path (`ec421c0b`) — Stage 1
   (BUILDER→gen1) stays host, Stage 2 cross-emits — and `release.yml` gained a
   `linux-arm64` matrix row that cross-builds on the x86_64 runner via the
   existing `bnc-0.0.10-linux-x64` BUILDER + `gcc-aarch64-linux-gnu` (`b32c53c9`),
   breaking the chicken-and-egg. Validated end-to-end on macos-arm64→macos-x64
   (Rosetta), guarded by `e2e/cross-compile.sh`. The `bnc-0.0.14` release is the
   first to publish the `linux-arm64` bundle — its `Build (linux-arm64)` matrix
   row cross-built and attached it. **Remaining (🟡 OPEN):** a native
   `ubuntu-*-arm` conformance runner (fetch-builder pulling the arm64 bundle)
   could replace the current qemu-aarch64 mode from residual (1)'s
   `builder-comp_native_aa64_linux`.

### Annotations & C function interop — `__c_call` DONE; residual is the `#[link]` companion — 🟡 OPEN (low)

**Option E (`__c_call` intrinsic) was chosen (form E2) and is ✅ DONE & SHIPPED**
(incl. native variadics; `done/plan-c-call.md` = "COMPLETE, 2026-06-02"). Call sites use
`result = __c_call("write", int32, cast(int32, fd), buf, len)` — C symbol name +
explicit return type + args already in the Binate types matching the C ABI, reusing
the backends' platform-C-ABI lowering (no C parsing, no `bn_` mangling). It is in
production across `pkg/builtins/rt` + `pkg/std/os` (open/read/stat/readdir/errno…),
retiring `pkg/bootstrap`'s hand-written C wrappers as intended. The general `#[…]`
annotation syntax also landed (as `#[build(…)]`). Options A–D and the E1
(C-prototype-string) form were rejected — see `done/plan-c-call.md` / git for that history.

**Chose NOT to build: the `pkg/c` C-types alias package** (`C_int`/`C_long`/
`C_size_t`/…). Call sites open-code the Binate↔C scalar correspondence directly
(`int32`, `*uint8`, `uint`, …). Revisit only if that open-coding becomes a real
maintenance pain. (`__c_call` stays compiled-mode-only; interpreted-mode use is a
frontend error — VM/dual-mode FFI dispatch is a separate deferred item.)

**Residual — the companion `#[link]` link-requirement annotation (sketch, NOT
built).** `__c_call` makes a C symbol *callable*; a complementary annotation would
make it *resolve at link time* — declare at the source level (most naturally in the
`.bni`, since the link requirement is part of the package's contract) that a package
needs some C library linked, so the driver adds the flag automatically instead of
every consumer passing `--cflag -lm` / `--link-after-objs` by hand. Prior art: Rust
`#[link(name="m")]`, Go cgo `#cgo LDFLAGS`, MSVC `#pragma comment(lib,…)`. Natural
shape `#[link("m")]` (optional `static`/`dynamic`/`framework` kind). This is the
first real payoff of the general annotations feature. Open wrinkles:
- **Transitivity** — propagate + dedup declared libs through the import graph (hook
  the loader's `ldr.Order` walk + the driver's `clangArgs` assembly).
- **Link ordering** — static archives supply only symbols referenced by *earlier*
  inputs, so aggregated `-l` entries need correct placement vs the `.o`s + runtime
  (the driver already does this for `linkAfterObjs`).
- **Platform-conditionality** — a `libm` dep is meaningless on bare-metal and
  `framework` kind is macOS-only, so the annotation likely needs target-qualification
  (ties into the C-free principle: it should evaporate on freestanding targets).
- **Static-spec portability** — `kind=static` is messy to express portably (GNU ld
  `-l:libfoo.a` / `-Wl,-Bstatic`; macOS `ld` has neither) → per-platform driver
  lowering or a full-path escape hatch.
- **Search paths** — keep the annotation name-only (`-l`); leave `-L<dir>` to flags.

### FFI export (`#[c_export]`) — post-MVP follow-ons (core + entry-move landed) — 🟡 OPEN

The outbound C-interop core landed (see claude-todo-done.md): `#[c_export("name")]` +
alias emission (Phases 2/3), `bnc --library` + `bn_init`/`bn_entry` (Phase 5a), and the
entry-move (`startup._entry` replacing `binate_runtime.c`'s `main` — the design's
`platform_init` package, renamed `startup`; Phase 6).  Design:
[design-ffi-export.md](design-ffi-export.md); roadmap:
[done/plan-ffi-export-detailed.md](done/plan-ffi-export-detailed.md).  Remaining follow-ons (all
post-MVP, none started):
- **Header generator** (Phase 7): emit a C `.h` for a facade's `#[c_export]` surface (a
  new `pkg/binate/codegen/emit_c_header.bn`).  Deferred at MVP — the C consumer
  hand-writes the small header for now.
- **Trivial-forward → symbol-alias optimization** (§3.4): a signature-preserving
  `#[c_export] func bar_(x) R { return foo.Bar(x) }` should lower to a symbol alias
  (`bar` = `foo.Bar`'s mangled symbol) / tail thunk, not a real call frame.
- **Merge build mode** (§3.6): co-link separately-built libraries without a `bn_init`
  collision.
- **Signature lint** (Phase 9, optional): a bnlint rule flagging C-unusable
  `#[c_export]` signatures (e.g. func-value params needing the trampoline).

The design's Phase 8 (baremetal linker-placement annotation) is NOT an FFI-export
concern — it is a linker-placement problem, tracked in [plan-linker.md](plan-linker.md).
The `--library` end-to-end (`check_library`) un-skip is in the entry-point-move
follow-ups above (blocked on the shim relocation, not `main`).

## Build constraints (`#[build(EXPR)]`)

### Build constraints (`#[build(EXPR)]`) — deferred follow-ups (arch/os MVP landed) — 🟡 OPEN
The `#[build(EXPR)]` arch/os MVP is landed at all four granularities (file / decl / import / `.bni`),
host-default config overridable per `--target`, through `c7249552` (conformance 731/733/735/736/737/746/747);
full design in [`plan-build-constraints.md`](plan-build-constraints.md), archived in
[claude-todo-done.md](claude-todo-done.md). Still deferred (none started):
- Vocabulary beyond arch/os: `triple` / `backend` / `libc` / `ptrsize` / `version` with `is` / `at_least` / `at_most`.
  (The **`version`** slice is now designed + planned — see the dedicated entry below.)
- `bnlint --target`; main-module gating; migrating the `impls/` duplicate trees onto constraints.
- The separate inline-asm (`#[asm]`) doc that composes with this substrate.

## Standard library — pkg/stdx/fmt

### fmt Printf — residual verb/flag gaps + two inert latent edges — 🟡 OPEN

Printf/Sprintf/Fprintf are complete for the common path — the verb-directed core,
width/precision/flags, `#`/string-hex/`*`/`%q`, and custom `lang.Stringer`
formatting all landed (see the done log). What's left: small verb/flag gaps (below),
plus two inert latent edges carried over from the struct-reflection layer.

Struct/default reflection (`%v`/`%+v` of an aggregate without a `String()`) is also
complete (per-phase summary in `claude-todo-done.md`). Two genuinely-inert deferred
edges remain from it — both confirmed unreachable in an adversarial review, so
nothing renders wrong today; tracked only so they aren't forgotten:
- A `readonly`-bearing anon-struct FIELD's assert-identity would use the stripped
  form (`mergeQualifiedReadonly` doesn't recurse struct fields).  Inert because
  anon-struct assert TARGETS are parser-rejected, so anon-struct record identity is
  used only for fmt rendering, where `readonly @[]char` and `@[]char` render alike.
- `mangleTypeArg`'s struct arm gates on the `__anon_` prefix while `typeNameImpl`'s
  anon arm also accepts an EMPTY name; an empty-name struct reaching `mangleTypeArg`
  would fall to the (linker-unsafe) named leaf.  Unreached — IR-gen always stamps
  `__anon_<N>` before mangle time.  A one-line defensive gate alignment would close it.

Still deferred (small verb/flag gaps — all render as visible error verbs /
documented divergences, never silently):

- **`+`/space sign flags on `%v` of a number** — `% v` of 7 is `7`, Go ` 7`; apply
  the sign in `emitDefault` (needs to detect a numeric arg + its sign).
- **`#` on a FLOAT** — `%#g` keeps trailing zeros (`3.00000`), `%#.0f`/`%#.0e`
  keep the decimal point (`3.`); currently `#` is ignored for floats.
- **`%#q`** → Go uses raw-string backquotes (`` `hi` ``); Binate stays `"hi"`.
- Some **malformed formats** differ from Go — a bare `%.` (precision, no verb)
  renders `%!(NOVERB)` where Go treats the `.` as a bad verb (`%!.(...)`).
- **Error-verb internal padding** — `%8d` of a string is `%!d(string=hi)`; Go pads
  the value inside (`%!d(string=      hi)`).  Niche; the error is still visible.
- Consider `%p` (pointer), `%U` (unicode), `%+v`/`%#v` — only if a use appears.

(NB: not a bug — Binate's `-0.0` LITERAL is a genuine negative zero, so `fmt`
signs it exactly as Go signs a real `math.Copysign(0,-1)`; Go constant-folds the
`-0.0` literal to `+0.0`.  A language constant-folding difference, not a fmt one.)

Tests: unit tests `fmt_printf_test.bn` + `fmt_printf_fields_test.bn`, conformance
`1135_fmt_printf`.

### `lang.Stringer` returns `@[]char`, but every string producer returns `@[]readonly char` — 🟡 OPEN (2026-08-02)

`Stringer.String()` is declared to return `@[]char` (mutable), while the natural
ways to produce the result all hand back `@[]readonly char`: `fmt.Sprintf`,
`fmt.Sprint`, and `strings.Builder.String()`.  So the idiomatic implementation

    func (p *readonly point) String() @[]char {
        return fmt.Sprintf("(%d,%d)", p.x, p.y)
    }

does not compile (`cannot assign @[]readonly uint8 to @[]uint8`), and the
implementer has to write `cast(@[]char, fmt.Sprintf(…))` — casting `readonly`
away from a slice that was freshly allocated for them.  That is sound here, but
it is exactly the cast that is *unsound* elsewhere (dropping `readonly` from a
view of static or shared data), so teaching it as the standard way to implement
Stringer is bad.

**Sharper as of bnc-0.0.14**, which tightened `cast`: dropping element-level
`readonly` is no longer a `cast` at all (§8.3 `conv.readonly` — "another live
handle may rely on the `readonly` view's immutability"), so the workaround above
now fails to compile with *"cast cannot drop element-level readonly … use
unsafe_cast (§8.7)"*. That leaves an implementer two choices, and both are bad:
reach for **`unsafe_cast`** — an unverifiable conversion, in the one interface
every printable type implements — or **copy the bytes** into a fresh `@[]char`
purely to satisfy the signature. The friction is no longer cosmetic; the language
now actively forbids the cheap way out.

Options: **(a)** change `Stringer.String()` to `@[]readonly char` — a rendering
is a value the caller only reads, and `@[]char → @[]readonly char` is implicit,
so an impl that still returns a mutable slice keeps satisfying it (what breaks is
a *caller* holding the result as `@[]char`); **(b)** have the producers return
`@[]char`; **(c)** keep it and document the cast.  (a) looks right, but it is a
signature change in `pkg/builtins/lang` that every implementer sees — user's
call.  Found while writing the standard-library example series in
binate/examples.

---

## Conformance matrix generators — port to Binate (dogfood)

### Port the `conformance/gen-*.py` matrix generators to Binate — 🟡 SCOPED, not started (2026-07-17)
Rewrite the 15 `conformance/gen-*.py` generators (~4,270 LOC) as a self-hosted
Binate tool, retiring the Python — every generator's docstring already flags
this as the intended end state. Full plan (strategy, tiers, phases, verification
discipline, the two float-rendering traps): [plan-genmatrix-port.md](plan-genmatrix-port.md).
Chosen approach: **C→A** — incremental, byte-diff-gated per generator,
converging on full dogfood. New `pkg/conformance/gen` genlib + `cmd/genmatrix`;
run under the **bundled (CHECK_TOOLS) `bni`** (no build step). Gated on two
external deps: `os.MkdirAll` landing in the tree (being implemented separately),
and a CHECK_TOOLS bundle whose injected `os` ships it (bump `CHECK_TOOLS_VERSION`
after it lands; interim runner is a from-tree `bni`).

## bnas (self-hosted assembler)

### bnas x64 → ELF: typical integer programs LANDED; SSE/exotic-addressing remain — 🟡 PARTIAL
`bnas -arch x64` → ELF64 landed (`d7e924a2c`): the CLI wiring
(`x64.ResolveFixups` + `elf.WriteX86_64`) plus the x64 text-parser essentials real
programs need — **RIP-relative addressing** (`[rip + label]` → a new `OP_RIPLABEL`
operand routed to `LeaRipLabel` / `MovRipLabel` / `MovRipLabelStore`, all
`R_X86_64_PC32`) on top of the pre-existing call/ret, push/pop, arithmetic, cmp,
conditional jumps, syscall, immediates, and `[base+index*scale+disp]`.  Validated
end-to-end (bnas → lld → linux/amd64 container): hello (RIP-rel lea), a
call/loop/jne calc, and a RIP-relative global read-modify-write.

**Remaining:** the x64 text parser recognizes only ~30 mnemonics (mov / lea, the ALU
group, test, the shifts, imul, push / pop, the one-operand group, div / idiv, branches):
no SSE / x87, movzx / movsx / movsxd, setcc, cmov, etc., though the encoder library has
many of them.  The assembler is meant to be comprehensive, so the parser must cover the
encoder library (and the encoder fill the gaps clang has).  **User decision 2026-10-18:
do it after the x86-64 TLS operators (plan-tls.md stage 3).**  Also found 2026-10-18 (review of the x64
immediate-classification fix): (a) encodings that differ from clang's for the same instruction — a jump to a
known nearby label is always rel32 (`e9` / `0f 8x`) where clang relaxes to rel8 (`eb` / `7x`), and the ALU /
TEST immediate forms on AL / AX / EAX / RAX use the ModR/M encodings (`cmp al, 1` is `80 f8 01`, `test eax,
imm32` is `f7 c0 …`) where clang uses the accumulator short forms (`3c 01`, `a9 …`; likewise `05`, `2d`,
`25`, `0d`, `35`, `3d` for add / sub / and / or / xor / cmp); (b) forms clang accepts that bnas rejects (no wrong
code): `dword ptr N` without brackets, `N[rbx]`, `call [N]` / `jmp N` (to an absolute address), `[rip + N]`
(R_X86_64_PC32 against the absolute address), `shl eax, N` (an APX form), a displacement past 32 bits (clang
keeps the low 32), constants whose labels come later in an operand, and a label difference across Mach-O
atoms.  **aarch64 → ELF is DONE** (`846802a77`): `bnas -target linux-aarch64`
routes aarch64+linux to `elf.WriteAArch64` (the object format now follows the OS
via `AssembleFile`'s `osName`; `""` keeps the per-arch host default).  The aa64
text parser also gained the `#:lo12:label` ADD operand (`4dbd8bb4e`), so a
hand-written ADRP+ADD pair reaches a datum; `e2e/bnld-linux-aarch64.sh`'s `hellopg`
runtime-proves R_AARCH64_ADR_PREL_PG_HI21 + R_AARCH64_ADD_ABS_LO12_NC through bnld
on linux/arm64.  Also landed `ldr xt, [xn, #:lo12:label]` (LDST64 lo12, `7419309b8`), so hand-written
ADRP+LDR loads a datum too.  Still narrower than the encoder on the aa64 side: only
the GOT `:got:`/`:got_lo12:` operands remain absent from the text parser (the native
backend emits those via the library, not text asm; and bnld rejects GOT relocs — a
hermetic linker — so there is no consumer for them yet).  Also wanted on the x64 side (user, 2026-10-06):
32-bit addressing in memory operands (`[eax]`, `[ebx + ecx*4]`, clang's 0x67 address-size prefix; rejected
since `c361a10e8`) — "Support it, later", after the label-difference work; and absolute symbol addressing
(`mov rax, [lbl]`, clang R_X86_64_32S; only `[rip + lbl]` is supported) — "Add to completeness list".  An
immediate starts only at a digit or `-` (`parseX64Operand`, `pkg/binate/asm/parse/x64.bn`), so `mov eax, ~8`,
`mov eax, ~(~1)` and `mov eax, (1 + 2)` are rejected where clang takes them (found 2026-10-06 by review).

## bnld (self-hosted linker)

### bnld dynamic Mach-O: `__mh_execute_header` is not defined — 🔴 OPEN (minor; found 2026-10-11, work-2, review of the Mach-O constructors change; pre-existing)

ld64 defines `__mh_execute_header` (C `_mh_execute_header`) as the executable's Mach-O header — the address
bnld gives `___dso_handle` (`LinkDynMacho`, `machoDynHeaderAddr`).  bnld does not define it, so a reference
becomes a dynamic import dyld cannot bind (a load failure) or, for a direct ADRP reference, a link error.  Fix:
define it in `LinkDynMacho` as `___dso_handle` is (`referencedNotDefined` → `withImageSym` → `setAbsValue` to the
header address), with a unit test alongside `TestLinkDynMachoInitFuncAndDsoHandle`.

### bnld dynamic Mach-O: llvm-objdump rejects the rebase table ("bad segIndex (too large)") — 🔴 OPEN (minor, tools only; found 2026-10-11, work-2, review of the Mach-O constructors change; pre-existing)

`llvm-objdump --macho --rebase` on a bnld-linked dynamic Mach-O stops with "truncated or malformed object (for
REBASE_OPCODE_SET_SEGMENT_AND_OFFSET_ULEB bad segIndex (too large) for opcode at: 0x1)", though dyld loads and
rebases the same binary correctly (e2e `bnld-macho-dynamic.sh`).  Cause unknown.  One difference from ld64's
output: bnld's dynamic writer (`EmitDynMachoExec`) writes `section_64` records only for `__mod_init_func` /
`__mod_term_func`, none for `__TEXT`'s `__text` / `__stubs` / `__const` or `__DATA`'s `__data` / `__got` /
`__bss`, where ld64 writes one per section.  Fix: find the check in llvm's MachOObjectFile that fails, then make
the output satisfy it (most likely a record per output section, as ld64 writes, which also lets otool / lldb
name the sections of bnld output).

### bnld's Mach-O reader has no general section-relative (non-extern) reloc support — 🟢 ENHANCEMENT

`parse_macho` resolves only EXTERN (symbol-indexed) relocations; a non-extern
(section-number + addend) relocation in a KEPT section is rejected loud.  Today the only
section clang emits section-relative relocs into is `__compact_unwind` (unwind metadata),
which bnld DROPS (ld64 consumes it into `__unwind_info`; bnld does no unwind processing) —
so LLVM-backend + `--linker bnld` links on macOS with no section-relative resolution
needed (all data/function pointers use extern relocs).  If a future clang/LLVM object puts
a section-relative reloc in a section bnld must KEEP, add general support: map r_symbolnum
(1-based section number) → the InputSection, and resolve to (final section address +
in-section offset) — e.g. via a synthesized section-base symbol + the offset as addend, so
the existing symbol-based Relocate path works unchanged.  Until then the drop is the
correct, minimal-linker behavior.

DEFERRED 2026-09-02 (reviewed alongside the other bnld follow-ups): confirmed preemptive —
nothing currently reaches the reject (the only section-relative relocs are in the dropped
unwind sections), so this stays parked until a real clang/LLVM object needs a
section-relative reloc in a KEPT section.

## bnfmt (self-hosted formatter)

### bnfmt moves a comment inside a declaration's header — 🟢 LOW (found 2026-10-18, temp-4, review of the import-section fix; pre-existing)

A comment between a declaration's keyword and its name is placed by no node: `func /* c */ f() {}`
prints `func f() {` / `/* c */` / `}` (the comment moves into the body), `var /* c */ x int` prints
`var x int /* c */` (and a struct field's, `b /* c */ int`, prints `b int /* c */`), and `package /* c */ "t"` prints the comment after the path; an interface's go above
its first method (`interface /* k */ I /* n */ {`).  A comment after the `]` of an annotation block that
spans lines goes below the block.  Each output is valid and idempotent,
but the comment moves.  The AST records no position for a declaration's name or an annotation block's
`]`, which placing these comments needs.  Related: an empty annotation block `#[]`, which bnfmt drops on
purpose, leaves an empty line in its place, so a doc comment above it no longer touches its declaration
(conformance/spec/16-packages/071_annotation_degenerate.bn).

### bnfmt moves a comment in some lists below its statement — 🟢 LOW (found 2026-10-14, work-4, review of the statement-start fix; pre-existing)

Delimited lists print the comments after their opener and between their elements (binate `1deb2f0a9`,
`pkg/binate/format/print_listcomments.bn`).  Still placed by no node, so printed after the statement with
the list joined: a comment after a list's last element, before its close (`h(x,` / `y, // c` / `)`); one
between the elements of a statement's expression list (`return a, // c` / `b`, `x, // c` / `y := …`); and
one among a typed builtin's operands (`make_slice(T, // c` / `n)`, `cast`), in a declaration's
type-parameter list (`func h[T any, // c` / `U any]()`, which goes into the body), an impl's interface list,
an interface's parent list (`interface X : A, /* a */ B {`, whose comment goes above its first method) or an
annotation's arguments.  Also after the opener of a parameter, result or function-type list, whose
opener position the AST does not record, and in an empty list (`f(/* c */ )`, `T{/* c */ }`).  A list printer
that takes these would claim them as `claimGap` does.  binate `scripts/bnfmt-check`'s comment check (`33c49c1b8`)
finds a comment moved past another in 686 of its corpus files (580 of the comment-injected tree copies, mostly
`func f(/* c */ a T, …)`, whose comment goes into the body; `r, /* c */ e = …`, whose goes after the statement).

### bnfmt indents a function literal's body from its parameters' continuation line — 🟢 LOW (found 2026-10-18, temp-4, testing the list-comment fix; pre-existing)

When a function literal's parameter list wraps, the body and its `}` indent from the continuation line the
`{` lands on, not from the line the literal starts on: `var g = func(aaaa… int, bbbb… int,` /
`\t\t\tcccc int) {` / `\t\t\t\ts()` / `\t\t\t}` (both bnfmt `bnc-0.0.17-pre3` and the tree's).
A declaration's body is not affected (it indents from column 0).  The body should indent one tab past the
line the literal starts on.

### bnfmt prints a builtin with a type operand without wrapping, and moves a comment after its `(` — 🟢 LOW (found 2026-10-19, temp-4, review of the builtin-opener fix; pre-existing)

`printBuiltin` (`pkg/binate/format/print_builtin.bn`) writes a builtin with a type operand (`make`,
`make_slice`, `cast`, `bit_cast`, `unsafe_cast`, `sizeof`, `alignof`, `__c_global`) as `name(` + type +
`, ` + value with no list logic: a source `cast` / `(int,` / `x)` is joined, and a comment after its `(`
(`cast( // c` / `int, x)`) goes after the statement.  The parser records these calls' `(` already
(`ast.File.BuiltinOpeners`); printing the operands as an item list (a type item, then the value) would
give them the value-argument builtins' wrapping and comments.

### The checker finds a generic function's instance by scanning every instance — quadratic in a package's instance count — 🟢 LOW (found 2026-10-25, work-7, profiling a total cap on instance checks for 5a; pre-existing)

funcInstance (check/check_instances.bn) looks an instance up on every naming of a generic function by scanning
st.Funcs and comparing each instance's type arguments (typeListsIdentical), so a package that names n instances does
O(n^2) comparisons.  The tree's largest package drain checks 93 instances, so it does not show today.  Checking 16384
instances of conformance 090's fan-out family (24.6 s natively), funcInstance with IdenticalStrict and ResolveAlias under it were over half of
the samples.  Fix: index the instances by declaration and type arguments.

### The lexer builds its comment list with slices.Append — quadratic in a file's comments — 🟢 LOW (found 2026-10-19, temp-4, review of the builtin-opener fix; pre-existing)

`pkg/binate/lexer/scan.bn` appends each comment to `l.comments` with `slices.Append`, which copies the
list every time: a comment-collecting lex (bnfmt) of a file with 160k comments takes ~90 s.  Real files
have a few hundred.  Build it in a vec, as the parser's builtin records are (binate `fd8e5836b`); the
lexer's Save / Restore would keep the vec's length and pop back to it.

### bnfmt's time grows exponentially with the nesting depth of function literals — 🟢 LOW (found 2026-10-19, temp-4, review of the rendering cache; calls and composites fixed in binate `922992893`)

Function literals nested in each other's bodies still format in time exponential in the depth: with
`return X` or `_ = X` in each body about 2x per level (17 deep ~2 s), with a comment in each body about 3x
(14 deep ~19 s).  The statement trials in a body (`exprListSingleLine`, `rhsInlineLongest` /
`rhsContLongest`, `printBlockSingleTrial`) render afresh and are not cached, and each print of a literal is
under a fresh comment cursor (`literalBodyCursor`), so nothing in its body hits the rendering cache
(`pkg/binate/format/print_rendermemo.bn`, keyed by cursor).  Real code does not nest this deep.
A statement with a comment no list prints on its last line is printed twice to reserve the comments after it
(printTrailing, print_trail.bn; see the inner-comment entry), which doubles this per level: with `x := func() int {`
… `}() /* s */ + 1 // t` in each body, depth 8 takes 61 s, against 1.5 s before binate `cb6028078`.

### bnfmt is not idempotent on some layouts: a lone argument, a string bnfmt splits, function literals — 🟡 IN PROGRESS (claimed 2026-10-23, temp-4; user: "yes" to taking it next) 🟢 LOW (found 2026-10-19, temp-4, fuzzing in the review of the rendering cache; pre-existing)

Formatting a formatted file changes it again in 26 of the ~25,000 files of binate's `scripts/bnfmt-check` corpora
(`33c49c1b8`, at binate `cb6028078`; gen 16, genb 9, rv 1); the tree's own files are at a fixpoint.  binate
`ae0e0a63c` fixed eight causes, `f5a890569` a binary chain's column-0 operand measure, `9b58a7bd0` a commented
one-line block on a `// LONG-LINE ALLOWED` line (see the done file), and `cb6028078`, reserving a statement's
trailing comments, 11 more.  That makes a trailing comment a tail, as a selector after a call is, so it also starts
the moves below: 4 of the 3,000 files generated to review `cb6028078` (`gg.hh(x.yyy, x.yyy(cccc,` / `cccc(func(p
int) int { … }))) // <80 bytes>` moves the argument and expands the literal; the second format keeps it on the head
line, as with a long selector there).  (Before `33c49c1b8` the corpora were
scratch copies, which the system's /tmp cleanup deleted; the scripts regenerate them from the same seeds, with the
files that did not parse fixed, and genb with index expressions, so it went from 24 to 18.)
binate `844d788c5` fixed one more cause: a one-line block whose list comment a measurement claimed before it printed
stayed on its line, and a second format expanded it (none in the corpora; 34 of 1,500 files generated with switch
statements; see the done file).

The 46, minimized, are nearly all one mechanism.  bnfmt keeps some layouts it finds in the source -- an opener alone, a
string run split across lines, a multi-line block, a wrapped list or chain -- and a first format produces those
layouts itself, so a second format takes them for the author's and measures differently.  Mostly the first format
moves an element to a continuation line because its rendering after the previous element breaks earlier inside (in
fitsPlacedOrMoved, a shorter first line placed than moved: an inner opener alone, a lone function literal moved to
stay on one line, a string split at the deeper column); the second format keeps the breaks printed at the moved
position, the two renderings then start alike, and the element moves back (`g(a{f},` / `zzz(hhhhh(func() {` becomes
`g(a{f}, zzz(hhhhh(func() {`).  Fewer go the other way: a split the first format made is kept at a deeper column,
runs past the cap, and the element moves on.  Several of the splits leave a one-character last part.  Formatted
repeatedly, 42 of the 46 stop changing on the second format and 4 on the third; none oscillate.

- Measuring a lone argument with everything after it (printArgList's tail 1) fixes 6 of the 46 and adds 2, and
  changes 6 tree files, some better (a composite literal or a call kept whole after its opener), some worse (`len(`
  alone; `patchX64(…)) !=` / `0 || …`).

Decided (user, 2026-10-24, of: format to a fixpoint inside bnfmt; fix the move measure; change the lone-argument rule;
leave the rest): "Fix the move measure" -- when deciding whether an element moves, measure its placed form with the
breaks a second format would keep; prototype it and report how many of the 40 it fixes and which tree files change.

Open questions from `ae0e0a63c` (landed as is): pkg/binate/check/check_instances.bn's right-hand side
`table.NewTable[…](…)` will wrap its type arguments inline (`…, instanceDeclHasher,` / `instanceDeclEq](`) at the
next CHECK_TOOLS bump, where it moved to a continuation line only because its inline form ran a byte past the cap;
giving right-hand sides the fill measure's first-line rule instead changes 330 tree files.  And making room for a
tail can leave a one-character last part (`"hello worl"` / `"d"}, // <85-byte comment>`).  Minor: trailWidth reserves
nothing for a line comment after a block comment in a gap (`x, /* b */ // c`); a statement's leading `/* a */ // b`
on a line of its own still prints as two lines (stable).

### bnfmt gives a declaration, group member or struct field the comments of the next one on its line: what remains — 🟡 IN PROGRESS (claimed 2026-10-25, temp-4; user: "let's fix next") 🟢 LOW (found 2026-10-25, temp-4, review of the marked-line fix; pre-existing)

Declarations, group members, struct fields and imports stop at the next node's comments (binate `1b66c0a0a`).  Left:
- A one-member `var ( … )` is parsed as a plain `var` whose Pos is the member's name, so the `var` keyword's position is
  lost and `var a = 1; var ( // c` / `b = 2` / `)` gives `// c` to `a`.  The parser should record the keyword.
  The same collapse (parseVarDecl, parseTypeDecl) moves the member's trailing comment to a line of its own after
  the declaration: `type (` / `F int // c` / `)` prints `type F int`, a blank line, then `// c`; `var (` / `x = 1 // d` /
  `)` likewise (bnfmt `bnc-0.0.17-pre3` and the tree's).  A one-member `const (` stays a group (for `iota`) and
  keeps its comment.
  The collapsed declaration's End is stamped past the `)` (parseTopLevelDecl in parser.bn; parse_stmt.bn for a
  local one), so the comment is not on End's line; and the declaration then spans lines, so a `// LONG-LINE
  ALLOWED` on its member's line is not looked for: the member wraps and the marker goes on a line of its own.
- A comment between a grouped type member's name and its type goes to the previous member: the member's Pos is the
  `type` keyword's (parseTypeSpec), not its name's.
  When the member starts its line, the comment goes on a line of its own above it, which is not idempotent: the
  next format finds a comment line there and splits the alignment run (`type (` / `AAAAAAAA int // x` /
  `B /* c */ bool // y` / `)` aligns `// y` with `// x`, and the second format does not; bnfmt `bnc-0.0.17-pre3`
  and the tree's).
- A group's comment alignment checks the width of a member's first trailing comment only, so `j = 1 /* j */ // <80
  chars>` can be aligned out to 111 columns.
- In `#[build(...)] /* f */ import (`, the comment moves above the annotation block.
- A case kept on one line with its body prints the body with no comment cursor, so the comments after its label and
  between its statements go after the last: `case 1: a() /* c */; b()` prints `case 1: a(); b() /* c */` (bnfmt
  `bnc-0.0.17-pre3` and the tree's).  Its width checks measure every comment on its line there too, as a second
  format finds them (binate `26c2ba240`), so a case with a comment before its `:` expands for it, though the label
  line the comment then goes on cannot hold it: `case <85 a's> /* cccccccccc */: g() // d` prints a 109-wide label
  line ending `: /* cccccccccc */` (on one line it is 118 wide).  Printed where they are, the comments should be
  measured there, and that case would keep its line.  When its last statement ends on a later source line (`case 6:
  a() /* x */; b(c,` / `d) // e`), emitTrailingCommentBefore stops at the label line's pending comment, and both go
  on lines of their own before the next case; a second format sees an own-line comment there and ends the case's
  alignment run at it, so the run's bodies move (not idempotent; found 2026-10-26 in a review of the switch cases'
  trailing-comment room).
- A comment between a signature's `)` and its results goes into the body: `func f(a int) /* c */ int {` prints
  `/* c */` on a line of its own as the body's first (bnfmt `bnc-0.0.17-pre3` and the tree's).

### bnfmt moves a statement's trailing comment to a line of its own after a comment inside it that no list prints — 🟢 LOW (found 2026-10-26, temp-4, review of the marked group member fix; pre-existing)

A comment inside a statement that no list prints (an operand's, in `x := 1 + /* c */` / `2 // d`) is still pending
in the cursor when the statement ends, and emitTrailingCommentBefore stops at it, as it is not on the statement's
last line.  So both comments go on lines of their own after the statement (`x := 1 + 2`, `/* c */`, `// d`), and a
`// LONG-LINE ALLOWED` there no longer marks the line (bnfmt `bnc-0.0.17-pre3` and the tree's).  On one line, the
inner comment goes after the statement (`p = 1 + /* z */ 2` prints `p = 1 + 2 /* z */`).  A line comment inside
cannot simply join the trailing one (`// c // d` lexes as one comment).

### bnfmt's operator chain moves a function literal to a continuation line for what follows its last line — 🟢 LOW (found 2026-10-26, temp-4, working on the statement trailing-comment entry; pre-existing)

printBinaryWrapped keeps an operand on the line when binOperandWidth plus the operand's tail fits there
(print_chain.bn).  An operand that spans lines is measured by all its bytes, unindented, so the tail -- which goes
on its last line -- counts against the line the break is decided for: a function literal that ends a chain moves to
a continuation line when what follows it is long, as a line comment after it in a list is (`g(a, // c` /
`e((zzzzzzzzzzzzzzzzzzzzzzz + func() int { return hhhhhhhhhhhhhhhhhhhhhhh }).gggg), // <40 bytes>` breaks before
`func`; bnfmt `bnc-0.0.17-pre3` and the tree's).  Dropping the tail for an operand that spans lines when rendered
from column 0 is not stable: a literal written on one line renders on one line there and moves, and once it has
expanded it spans lines there and stays (tried 2026-10-26: two files of the scripts/bnfmt-check rv corpus became
non-idempotent).  The fix needs a measure that does not depend on how the source laid the operand out.
A statement's or group member's trailing comment is such a tail too (binate `cb6028078`): `m int = a * func(p int)
int {` … `} // <60 bytes>` breaks before `func`, for nothing (3 of the 3,000 files generated to review that commit).

### bnfmt aligns a run across a comment before an element on its line, which it moves to a line of its own — 🔴 OPEN 🟢 LOW (found 2026-10-28, temp-4, review of the marked method-and-field fix; pre-existing)

A struct type pads its fields' names, and a group and a switch align their members' and one-line cases' comments, in
runs that a blank line or an own-line comment breaks, found by source lines (structRunEnd, print_type.bn; groupRunEnd,
print_group.bn; caseRunEnd, print_switch.bn).  A comment before an element on the element's own line (`/* p */ yy
int`) is in no gap, so the element stays in the run; but it prints on a line of its own above the element
(emitLeadingComments), which breaks the run when the output is formatted again.  `type T struct {` / `x int` /
`/* p */ yy int` / `}` prints `x  int` / `/* p */` / `yy int`, and a second format `x int`; the same with `a = 1 //
x` / `/* p */ bbbb = 2 // y` in a `var` or `const` group, and with `case 1: g() // a` / `/* p */ case 2222: g() //
b` (bnfmt `bnc-0.0.17-pre3` and the tree's).  The second format is stable.  An interface method's comment
(`/* p */ F() int`) moves to a line of its own the same way, with no run to break.  A run should break at any comment that
prints on a line of its own between two elements, or such a comment should stay on its element's line.

### bnfmt's lone element in brackets leaves out what follows the close — 🟡 IN PROGRESS (claimed 2026-10-26, temp-4; user: "take on the todos") 🟢 LOW (found 2026-10-26, temp-4, review of the statement trailing-comment fix; pre-existing)

printArgList (print_wrap.bn) measures whether a lone element fits after its opener with only the close after it, as
an element that wraps -- a call, a composite -- reserves the rest itself; a lone string literal gets the whole tail.
An identifier or a selector cannot reserve it, so `ffff…[cccc…]++` with the `]` at column 100 prints 102 wide, and
`ffff…[cccc…]++ // a trailing cc` and `gggg(ffff…[cccc…]) // a trailing cc` stay 103 wide, while an index that
itself runs past the cap wraps (`ffff…[` / `cccc…]++`) (bnfmt `bnc-0.0.17-pre3` and the tree's).  Measuring every
lone element with everything after it is the lone-argument rule the non-idempotence entry tried; giving the whole
tail only to an element that cannot break, as a string literal has it, is narrower.

### bnfmt's `__c_call` leaves out what follows its `)` — 🔴 OPEN 🟢 LOW (found 2026-10-29, temp-4, testing the non-idempotence prototype; pre-existing)

printCCall (print_builtin.bn) takes no tail: it fills its arguments with only its own `)` after the last, so what
follows the call -- a close, a trailing comment -- gets no room.  A string run as the last argument is split to the
cap, and the line then runs past it: `__c_call("sym", int, "<92 a's>" "<84 a's>") // K2` at four tabs prints its
last part 101 wide, and inside `g(qqq[…])` 103 (bnfmt at binate `4f58c52e5`).  printBuiltin's other builtins take
the tail (`1 + tail` for the last operand); pass it to printCCall as well.  It is what leaves one file of the
`scripts/bnfmt-check` corpora (gen/g1_122) a line past the cap under the non-idempotence prototype, which lays the
call out at a shallower column.

## bnlint rules, unused-entity checks & lint skips

### bnlint: flag a pointer-receiver method value on a composite literal (`S{…}.PGet`) — 🔴 OPEN (proposed 2026-09-30 by the review of the method-value fixes; decided: add later)

A pointer-receiver method value on an addressable composite literal captures the
literal's ADDRESS (`func.method-value.capture`: `*T` captures `&x`), and the
literal is a statement temporary (§18.4 `mem.temporary`), so the method value
reads freed fields once the statement ends — user error by the spec, but
inconsistent with `mk().PGet`, which captures a call result by value.  Likewise
`var p *S = &S{m: mkLeaf(1000)}`.  Flag a method value or `&` of a composite
literal whose result outlives its statement.

### bnlint: flag a capturing `*func` closure made in a loop and stored where it outlives the iteration — 🔴 OPEN (proposed 2026-09-30 by the spec review of the *func closure frame lifetime; decided: add later)

A capturing `*func` closure site has one record per frame (`func.closure.allocation`):
each evaluation re-captures into it, so every value the site produced sees the latest
captures.  `for i := 0; i < 3; i++ { fs[i] = func() int { return i } }` gives three
closures that all return 2.  Flag a capturing `*func` literal or method value inside a
loop whose value is stored into a variable, element or field declared outside the loop
body (or otherwise outlives the iteration), pointing at `@func` for independent
closures.

### bnlint: flag `bit_cast` of a function literal to a raw or named-raw function-value type — 🔴 OPEN (proposed 2026-09-30 by the review of the cast-operand hint fix; decided: add later)

`bit_cast` reinterprets its operand as it is typed (`conv.bit-cast`), so it gives a
function literal no destination hint: `bit_cast(*func(int) int, func(x int) int {…})`
makes the literal a heap `@func` statement temporary and reinterprets it as a raw
closure that dangles after the statement.  Flag it, pointing at `cast` (which makes
the literal a frame closure).

### bnlint: flag an `@func` temporary borrowed by a `*func` destination past its statement — 🔴 OPEN (proposed 2026-09-30 by the review of the cast-operand hint fix; decided: add later)

`var h *func(int) int = cast(@func(int) int, func…)` or `= mk(7)` (with
`mk` returning `@func`) borrows a statement temporary as a raw `*func`; the temporary
is released at the end of the statement and `h` dangles (user error per the
memory-management rules — the compiler does not extend the temporary).  Flag a
`*func` variable / field / element initialized or assigned from an `@func`-typed call
result or cast that is not otherwise owned.

### Raw-slice escape: decide whether a BROADER best-effort escape lint is wanted — 🟡 NEEDS DECISION
The original framing ("demote the raw-slice escape TYPE ERROR to a linter rule")
is obsolete: there is NO type-check rejection for raw-slice escape (the checker
never rejected it), and a `raw-slice-return` LINT rule already exists (`lint.bn`,
landed `10d19369`) — but it only covers the `@[]T → *[]T` "drops the managed
wrapper" return case. **Open decision (user):** is a broader best-effort escape
lint wanted (return / store-to-outliving-field / assign-to-global of a raw slice
borrowing a local), or is the current narrow rule + "raw is an opt-in escape
hatch" sufficient (close this out)?

### `dangling-raw-borrow-of-temporary` lint rule (§9.7) — 🟢 LOWER PRIORITY (2026-09-03)
A concrete instance of the broader escape-lint decision above.  `var s *[]T =
make_slice(..)[:]` binds a raw slice to a borrow of a `make_slice(..)` TEMPORARY,
which spec §9.7 (`mem.temporary`) releases at end of statement — so any later use of
`s` is a use-after-free (programmer error, correctly NOT suppressed by the compiler).
This bit `conformance/439_iv_in_slice_raw`: benign on LLVM / native aa64 / native x64
(freed block not immediately reused), but corrupted on the native-arm32 bare-metal
no-free-until-teardown arena (freed backing reused; gdb-watchpoint traced the write to
rt.writeTags).  Fixed by owning the backing (`0d86fb9be`; write-up in
claude-todo-done.md).  A rule flagging a raw slice/pointer bound to a borrow of a
statement-scoped temporary and used past that statement would catch this class
statically — same family as `func-value-escape` / `iface-borrow-escape`.  Caveat:
`conformance/` is NOT in the lint scope today (hygiene lints compiler/stdlib source;
conformance is excluded as intentional fixtures), so catching it in 439-like tests would
ALSO need a decision to lint conformance.  Lower priority: §9.7 makes it programmer error
and owning the backing is the trivial fix.

## Hygiene checks: tier dependencies & file length

### `Self`-parameter method is uncallable through a generic constraint (Self binds to the type param, not its base) — 🟠 OPEN (2026-07-03)

**Severity: minor (obscure `Self` corner; the fix is a semantics decision, not a
clear defect).** A `Self`-parameter interface method — `eq(other Self)`,
`grab(rest *[]Self)`, or a variadic `merge(others ...Self)` — is satisfiable and
directly callable, but **cannot be called THROUGH a generic constraint** when the
type param is a pointer, because the two `Self` resolutions disagree:

- **Impl-satisfaction** (`methodSigSatisfies`, `check_impl.bn`): `Self` → the impl's
  **base named type** (`named = recv.ReceiverBaseNamed()`, e.g. `Bag`). Correct, and
  matches §11 — `010`'s `eq(other Self)` is satisfied by `eq(other Square)` (a value).
- **Constraint-call binding** (`tryTypeParamMethodCall`, `check_method.bn`):
  `substituteSelf(param, recvType)` uses `recvType` = the **type param** (`T` = `*Bag`).

So inside `func f[T Eq](a T, b Bag) { a.eq(b) }`, `eq` expects `*Bag` (Self→T) while
the impl takes `Bag` (Self→base) → "cannot assign Bag to T". **General** — not
composite- or variadic-specific (the plain `eq(other Self)` reproduces it).

- **Consequence:** a `Self`-parameter method can't be invoked via a constraint with
  a pointer type param — and a constraint is the ONLY path that reaches such methods
  (they're object-unsafe through an interface value). So the variadics Phase 6c
  `substituteSelf`-recursion in `tryTypeParamMethodCall` (correct code) has no
  end-to-end test.
- **Repro:** `interface Eq { eq(other Self) bool }` + `impl *Bag` /
  `func (b *Bag) eq(other Bag) bool` + `func areEq[T Eq](a T, b Bag) bool { return
  a.eq(b) }`.
- **NOT a bug in impl-satisfaction** — that works; `*[]Self` is satisfiable and
  `conformance/regressions/iface-self-in-composite` is a POSITIVE test. (The earlier
  "satisfaction fails" framing was a test error: the repro impl used `*[]*Bag` where
  `Self=Bag` wants `*[]Bag`.)
- **Fix is a semantics decision** — should the constraint call bind `Self` to
  `base(T)` (matching impl-satisfaction), or should impl-satisfaction use the
  receiver form? Deferred pending that decision; **do not fix without one**.
- **Discovered:** 2026-07-03, adding variadics Phase 6 coverage.

---

### `print(42)` and friends: how do primitives implement interfaces? — DESIGN OPEN
- **Problem**: with the current rules, `int` (and other predeclared
  primitives) can't implement interfaces. Methods can only be
  declared on TYP_NAMED types (the receiver lookup in
  `check_decl_func.bn:resolveMethodReceiver` rejects `func (x int)
  ...` because `int` is TYP_INT, not TYP_NAMED). So a user-written
  `printIt(s *Stringer) { ... println(s.String()) }` can't accept
  a literal `42` — the user has to wrap with `type MyInt int` +
  impl, then write `printIt(&MyInt(42))`. That's a lot of
  ceremony for a basic use case.
- **Generics don't help.** A `printIt[T Stringer](t T)` call site
  still requires `T` to satisfy `Stringer`, so `int` would need a
  Stringer impl somewhere — same blocker as the non-generic case.
  Generics solve "extensible dispatch", not "primitives need to
  carry methods."
- **Today's escape**: `println(42)` works only because it's a
  compiler builtin — `bootstrap.println` synthesizes per-type
  formatting at the call site. Not user-extensible. The hack is
  documented as temporary in `feedback_println_hack.md`.
- **Two real options** (discussed 2026-05-07):
  1. **Language-blessed implicit interfaces.** The interface plan
     already lists `any` as a built-in implicit interface and
     reserves the mechanism for "small, closed, language-defined
     set" of others. Add `Stringer` (and possibly `Eq`, `Hash`,
     etc.) to that set — every type, including primitives, gets
     a synthesized impl from the compiler. Then a user-written
     `printIt(s *Stringer)` accepts any value uniformly.
     Cost: every iv gets a real vtable, even for primitives, and
     the language has to define the canonical formatting story
     for each primitive.
  2. **Standard-library carve-out for methods on universe types.**
     Allow a designated package (`pkg/std` or similar) to declare
     `func (x int) String() ...` even though `int` is a universe
     type. The carve-out exists only for the language's own std
     library; user packages still can't extend `int`. Closer to
     Go's `fmt.Println` model. Heavier carve-out but lets the
     std lib look like normal Binate code.
- **Lean (preliminary):** option 1 — the implicit-interface
  mechanism is already the named escape hatch, the formatting
  story for primitives is small + closed, and the result is
  user-extensible (their own types implement Stringer normally).
  But this is a real design call; needs a plan doc before
  shipping.
- **Not blocking**: today's `println(42)` carries the load.
  Revisit when generics land or when a user-written `printIt`-
  style function becomes pressing.

### Purely-value const extension (future language direction) — DESIGN, not started
Future direction split out of the (now-resolved) non-int-const mis-emit bug:
allow `const` of certain non-scalar but purely-value types (no storage, no
managed fields). Currently `const` is scalar-only (non-scalar → `errNonScalarConst`,
"use `var readonly`"); no `isPurelyValueType` predicate exists yet. A genuine
language extension, not a bug fix.

## Language-feature proposals

### Switch `fallthrough` — proposal
- Not in the current grammar (`grammar.ebnf`). Binate switch cases are implicit-break (Go-style), but there's no opt-in for Go's `fallthrough` keyword.
- Would add one reserved keyword, one AST statement kind (`STMT_FALLTHROUGH`), and one IR lowering (branch to the next case's entry block, skipping its case-value check).
- Before implementing: decide whether we want it at all. Arguments for: matches reader expectations from Go, lets users avoid duplicated bodies across related cases. Arguments against: rarely needed in practice, adds a new keyword for a small ergonomic win, forces the type checker to recognize terminators beyond `return`/`panic` (termination analysis already inspects case bodies for bare `break`).
- Likely a decline unless a concrete use case comes up, but worth capturing as a live option.

### Termination analysis — labeled break
- Missing-return check (test 245) uses Go-style termination analysis simplified: RETURN terminates; `panic(...)` terminates; BLOCK terminates if last stmt does; IF terminates if both branches do; FOR with no condition and no `break` in body terminates; SWITCH with default and all cases terminating (no break) terminates.
- **Labeled break**: Binate currently has no labels. If/when we add them, termination analysis needs to track labels — a `break L` inside a nested for doesn't break the inner for (contrary to the current "any break disqualifies enclosing for/switch" rule). Revisit when labels are on the table.

### Conformance harness: a test gets ONE search root — `pkg.resolve`'s multi-root facets are untestable — 🟢 OPEN (from the Ch.16 review, 2026-06-19; rest of that entry landed 2026-09-27)

The runner prepends a single test root (`binate-paths.sh --prepend <test dir>`), so two `pkg.resolve`
facets cannot be exercised: the `.bni` and the impl directory resolved on INDEPENDENT search paths
(a `.bni` under one root, its impl dir under another — `spec/16-packages/012`), and
`pkg.resolve.public`'s public-vs-local packages living under DIFFERENT roots (`013`).  Both tests now
say so in their comments.  Closing it needs a harness extension — e.g. an optional second root
directory inside a multi-file test (`NNN_name/root2/`) that every runner prepends as well — then a
test per facet.  (Annex C, where the spec plan says untested rules are to be listed, is still an
unauthored stub.)

### Observable optimizations and UB policy — broader question
- Surfaced while planning const: allowing the compiler to allocate
  a shared static global for all-const composite literals is an
  optimization observable via raw-pointer comparison (`&a[0] ==
  &b[0]` where `a`, `b` are both `"hello"`). The const plan accepts
  this as UB rather than either blocking the optimization or
  carving out precise "same-literal-text gives same address"
  semantics.
- Same class as the refcounting move optimizations that are already
  observable via `rt.Refcount(...)` without a nailed-down spec.
- **Broader question**: do we want a general policy of "these kinds
  of observations are UB, the compiler may optimize across them",
  written up somewhere authoritative? Candidates for the same UB
  bucket: literal address identity, refcount timing, struct padding
  bytes, uninitialized-memory reads of stack-allocated vars. The
  alternative (fully specified observable behavior) is probably
  incompatible with small-target codegen goals.
- Not urgent — we're already making these trade-offs silently. A
  short design note ratifying the policy would be useful when a
  future optimization / feature forces the question.

### Secondary specs — testing + stdlib (primary spec is written) — 🟡 OPEN
The **primary** language spec is **written & maintained in `docs/spec/`** (21 chapters +
Annexes A-D, canonical `binate.ebnf`, rule-ID apparatus; reconciled as features land) — moved to
the done log ("Primary language spec — WRITTEN"). Philosophy: `claude-notes.md` § "Language
specification — primary spec is minimal — DECIDED". Remaining, both **NOT started**:
- **Minor secondary spec — testing**: the `_test.bn` packaging convention + `pkg/builtins/testing`.
  May fold into the primary; TBD.
- **Major secondary spec(s) — stdlib**: I/O, containers, formatting, string utilities, etc. —
  probably split by area.

Artifact when writing begins: alongside `docs/spec/` or `explorations/spec-*.md`. (The `pkg/rt`
review below still gates finalizing §20.2's normative surface, currently Draft.)

### pkg/rt review — decide runtime vs. stdlib vs. internal
- Today `pkg/rt` is a grab-bag of runtime helpers, refcount
  primitives, allocator wrappers, bounds-check stubs, etc.
- For the primary spec to nail down "what the runtime contract
  is," `pkg/rt`'s surface needs a review: classify each member as
  **stay** (truly language-runtime, normative in the primary
  spec), **move** (standard-library-shaped — belongs in a stdlib
  package, out of `pkg/rt`), or **make-internal** (only used by
  the language implementation itself, no `.bni` export).
- Output: a classification of `pkg/rt` members + a follow-up
  cleanup plan (a `plan-*.md` doc under `explorations/`). The
  cleanup itself is separate work and can be sequenced
  independently — what's important first is the *classification*,
  which unblocks the primary spec writeup.

## Codegen & backend (non-func-value)

- **Native backends: per-op guards on a malformed instruction silently emit nothing** — 🔵 OPEN (found
  2026-10-07, work-3, while making unresolved operands and slots fail loud).  About 70 `if len(ins.Args) < N {
  return }` and 11 `TypeArg == nil` / `Typ == nil { return }` guards across `pkg/binate/native/{x64,aarch64,arm32}`
  drop the instruction without a word when an op arrives with too few operands or no type.  The IR verifier
  (`pkg/binate/irbuild/verify.bn`) checks block structure, phi ownership and constant ranges but not operand
  counts, so nothing proves those inputs impossible.  Fix: check each op's operand count and required types in
  the verifier (then the guards are dead and can go), or make each guard record `a.SetError`.

- **Checker rejects valid alias uses in const / untyped-literal contexts** — 🔵 OPEN (found
  2026-09-26).  Spec type.alias.transparency: an alias is its target in every context.  But with
  `type I8 = int8`: `type N I8; fn(-5)` / `var n N = 3` → "cannot assign untyped int to N" (while
  `type M int8; fm(-5)` compiles), and `const C I8 = -5` → "const `C` requires a scalar type".  Same
  for float32/float64 aliases.  Not root-caused (checker's untyped-constant assignability and
  const-type checks don't resolve the alias under a named type).
- **LLVM arm32-baremetal at -O1+ can't link any program: undefined `__aeabi_memclr`** — 🔵 OPEN
  (found 2026-09-26).  At -O2 clang lowers a zeroing in `rt.rtFormatInt` to `__aeabi_memclr`, which
  runtime/baremetal_arm32/aeabi_*.s doesn't provide; -O0 links and runs.  CI's -O2 lane has no LLVM
  arm32-baremetal shard, so it's unseen.  Fix: provide `__aeabi_memclr` (+ `memclr4/8`, `memset*`,
  `memcpy*` family as needed) in our arm32 runtime asm.

### Big-endian CODEGEN — deferred (no BE target exists yet) — 🟡 DEFERRED
The Ch.7.13 layout follow-ups (`type.layout.funcval-order-hardening` + the
`type.layout.byte-order` decision / `TargetInfo.BigEndian` field + little-endian-only
assert) are ✅ DONE & LANDED — see [claude-todo-done.md](claude-todo-done.md). What
remains: actual big-endian byte-EMISSION (object writers, `ir.DataGlobal` int terms,
`bit_cast` / the representation builtins) for a future big-endian / cross-endian
target. `SetTarget` currently `panic`s on a big-endian target, so there is no
silent-wrong-code risk meanwhile; do this when such a target is actually needed.

### DWARF debug info — finer-grained source positions (open-ended, low priority) — 🟡 OPEN

The DWARF foundation + full type coverage are done (archived in [claude-todo-done.md](claude-todo-done.md):
`-g`, DICompileUnit/DIFile/DISubprogram, per-function DISubroutineType, DILocalVariable for
locals + params, and DIBasicType/DICompositeType/DIDerivedType covering scalars, pointers,
structs, slices, managed-slices, interface-values, function-values, arrays, named typedefs).
The one remaining, open-ended piece:
- Thread source positions through more IR-gen sites (statements, assignments, calls) for
  finer-grained `DILocation` — today only `genExpr` threads `.Line`; most emission sites rely
  on coarse statement-line backfill. No columns.
- No `llvm.dbg.value` (only `dbg.declare` for allocas).

### Static-managed sentinel — deferred follow-ups (optimizations, not correctness) — 🟢 LOW
Follow-ups split out of the (now-done) static-managed sentinel landing:
- **String-literal null-backing unification**: can the string-literal
  `backing_refptr = null` immortality trick (`emit.bn`) be unified under the
  negative-refcount sentinel? Representation can plausibly unify; the nil-check
  itself can't be dropped (it guards genuinely-nil `@` values). Repr cleanup.
- **ClosureRec-as-sentinel**: the VM's shared per-callee non-capturing-`@func`
  `ClosureRec` (`vm_exec_funcref.bn`) is a static, never-freed managed object.
  The premature-free CRITICAL was already fixed symmetrically (conformance 528);
  making the shared `ClosureRec` an immortal sentinel would remove per-instance
  refcount churn on a shared singleton. Optimization, not a correctness gap.

### relro section infra (`__DATA_CONST` / `.data.rel.ro`) for relocatable read-only data — 🟡 OPEN (follow-up from DataGlobal Inc 4b)

Today every **relocatable** read-only blob — the `_Package` descriptor node, the
info-node tables, the backing arrays, all vtables, the string `.ms` managed-slice
header — stays in writable `data` rather than rodata, because Mach-O rejects
relocations out of `__TEXT,__const` (text-relocs) and the object writer has no
relro section.  These blobs are logically immutable after load; leaving them
writable is a hardening gap (a stray write corrupts a descriptor/vtable instead of
faulting), not a correctness bug — `DataGlobal.ReadOnly` already routes
non-relocatable read-only data (e.g. string bytes) to rodata correctly.

**Fix:** add a relro section — Mach-O `__DATA_CONST,__const` + ELF `.data.rel.ro`
(`SHF_ALLOC|SHF_WRITE`) — and route relocatable `ReadOnly` `DataGlobal`s there so
they become read-only-after-load (the dynamic loader applies relocations, then the
page is remapped read-only).  This is a new object-writer feature
(segment/section/load-command emission); verify on both formats + arm32.  Low
urgency (no current miscompile; the writable placement is safe, just unhardened).

## Testing: harness, runners & conformance coverage

### Conformance harness: `pkg0.testing` `--test`-only rules are not conformance-testable

1. **GAP (harness limitation, not a defect) — `pkg0.testing.testfunc` + `pkg0.testing.run` are not
   conformance-testable.** Both require the `--test` discovery/execution runner (`cmd/bnc --test` /
   `cmd/bni --test`); `conformance/run.sh` only runs ordinary programs (no `--test` plumbing). They
   are exercised by the unit-test suite, not conformance. Closing them would need a test-runner mode
   added to the harness. Left as documented coverage gaps (Ch.20 is 18/20). Candidate for an
   `untestable`/`framework` reclassification in `extract-rule-ids.py` (a denominator decision).

### Better test-mode/target annotation than `.xfail` (unit + conformance)
- We lean on `.xfail.<mode>` files to mark tests that can't run in a
  given configuration (e.g. `pkg-builtins-rt.xfail.builder-comp-int*`
  because rt is native-only in the VM; the `__c_call` conformance tests
  498/500/527/530 xfailed in every VM-leg mode). But "expected to FAIL"
  is the wrong semantics for "not APPLICABLE here" — these tests are
  *bnc-only* / *vm-only* / *target-specific* by nature, not regressions.
- **Want**: a first-class annotation (in the test source or a manifest)
  declaring a test's applicable modes/targets — `bnc-only`, `vm-only`,
  per-backend, per-target — so the runner *skips* inapplicable configs
  cleanly and reserves `xfail` for genuine known-failures. Would also
  let `__c_call` tests declare "compiled-only" honestly instead of a
  fan of per-mode xfail files.
- Surfaced 2026-06-03 by the drop-libc / native-only-rt work.

### Test runner improvements
- **Better filtering (individual test functions)**: ability to specify individual test functions, not just packages (e.g., `run.sh boot-comp pkg/ir TestFoo`).
- **Timeout/hang handling**: better and/or automatic detection and handling of tests that hang.
- **Parallelization**: consider running test packages in parallel within a mode.

### Build out e2e testing
- We have unit tests (per package) and conformance tests (language
  semantics). What we don't have is a place for **end-to-end tool
  integration tests** — checks that the CLI/loader/runtime wiring
  works the same way across all four tools that load Binate
  packages: `bootstrap`, `bnc`, `bni`, `bnlint`.
- **What's landed (2026-04-30):**
  - Two scripts: `e2e/split-paths.sh` (the original — `-I`/`-L`
    cross-tool contract; covers Stage 1–6 of the package-search-paths
    plan) and `e2e/repl.sh` (9 cases for `bni --repl`: basic call,
    multi-stmt, error recovery, multi-line for-block, braces in
    string literal, plus four Tier 2 cases — func persists, cross-
    decl call, type rejected with diagnostic, bad body recovery).
  - CI hookup at `.github/workflows/e2e-tests.yml` — matrix-
    discovery via `ls e2e/*.sh`, one runner per script, `fail-fast:
    false`.  Standard checkout layout (binate + bootstrap as
    siblings) matches what the scripts assume.  New e2e scripts are
    picked up automatically.
- **Unique challenges this dir still has to solve over time:**
  - **4 tools, not 1.** A single feature (like `-I`/`-L`) needs to
    be exercised on each tool independently, since each parses CLI
    flags separately and threads them into the loader differently.
  - **Multiple build/run modes for the binate-written tools.** bnc,
    bni, and bnlint can each be exercised through several pipelines:
    bnc via boot-comp / boot-comp-comp / boot-comp-comp-comp /
    boot-comp_native_aa64; bni via boot-comp-int / boot-comp-comp-int;
    bnlint via the same chains as bnc. Note that bni cannot be
    interpreted directly by the bootstrap (cmd/bni imports pkg/vm,
    whose float literals the bootstrap lexer doesn't recognize) —
    bni really has to be built via boot-comp first.
    Full e2e coverage of "feature X works" multiplies tools × build
    modes — easily 10+ runs per feature. We don't necessarily want
    that today; figuring out which slice is worth the cost is part
    of building this out.  Today both shipping scripts pick a
    single mode each (split-paths covers all four tools at their
    "default" build path; repl uses boot-comp bni).
  - **Fixture management.** Conformance tests share a single root;
    e2e tests like split-paths need disjoint fixtures, ad-hoc temp
    dirs, optional checked-in subtrees. No standard pattern yet —
    both current scripts use `mktemp -d` + `trap rm -rf` and inline
    `cat <<EOF` heredocs for fixture files.
- **Why these scripts are useful motivating examples:**
  - **split-paths**: the `-I`/`-L` feature is something `bootstrap`,
    `bnc`, `bni`, and `bnlint` should all support **identically** —
    a deliberate cross-tool contract.  e2e is the only layer where
    that contract can be observed directly.
  - **repl**: the `bni --repl` PoC is a multi-stage user-facing
    flow (load module → drive prompt via stdin → check banner +
    prompts + results byte-for-byte).  No unit test could easily
    exercise the full input-to-output transcript; e2e is the right
    layer for "the REPL works end-to-end".
- See [`plan-package-search-paths.md`](plan-package-search-paths.md)
  for the spec `e2e/split-paths.sh` validates and
  [`done/plan-repl.md`](done/plan-repl.md) for what `e2e/repl.sh` covers.

### (b4) Differential harness v3 — port `gen-diff-scalar.py` to Binate (dogfood) + flavor B — NOT STARTED
- **Context**: the property-based differential value-correctness harness
  (`conformance/matrix/scalar-diff`, oracle = spec) is realized through v2 —
  shifts, conversions, arithmetic, comparisons, bitwise; 123 cells / 5415
  tuples; generator `conformance/gen-diff-scalar.py` (Python). See
  `done/plan-differential-testing.md` (phasing item 3) for the full design.
- **v3 scope** (the remaining phase):
  1. **Port the generator to Binate** — rewrite `gen-diff-scalar.py` as a `.bn`
     program so the harness dogfoods the language on a real codegen-shaped task
     (LCG, two's-complement oracle, bit-pattern formatting). Keep the emitted
     cells byte-identical so the existing `.expected`/`.xfail` set and
     `--check` idempotence carry over unchanged.
  2. **Flavor B (optional, for the highest-volume ops)** — one self-checking
     `.bn` per op that loops an embedded `(inputs, expected)` table and prints
     `mismatch i: got… want…`, denser than the current static-cell flavor A and
     debuggable on failure (flavor A shows *which* tuple, not the wrong value).
     Decide per op once flavor A shows which need the volume.
  3. **Sample-size knob** — a fixed, seeded count parameter so coverage can be
     dialed up without touching the generator logic.
- **Why**: dogfooding is the highest-leverage *process* check (the OOM, the
  `@func`-dtor crash, the shift bug all first surfaced by compiling real Binate
  programs); porting the generator turns the harness itself into one more such
  program. Not urgent — v1/v2 already give the value coverage; v3 is the
  dogfood + debuggability upgrade.

## Standard library & libraries

### Standard library design
- Candidates: growable collections (Vec[T], Map[K,V] post-generics), I/O abstractions, string utilities, formatting
- CharBuf is implemented (pkg/buf); broader stdlib design should inform future collection APIs

### Expand `pkg/slices` beyond `Append` — opportunistic
- `pkg/slices.Append[T]` is the only generic helper today.  Natural
  additions when call sites demand them (don't add speculatively):
  - `Concat[T](a, b) @[]T` — for the managed-slice + managed-slice
    shape.  `bootstrap.Concat` covers the char-slice case but is
    raw-slice-typed.
  - `Filter[T, P]` / `Map[T, U]` — block on closures or func-value
    params; only worth it once those constraints land properly.
  - `RemoveLast[T](s) @[]T` — `popLoading`-style pattern (rebuild
    minus last occurrence) repeats per element type.
  - Don't pre-add a kitchen-sink set — let the first 2-3 call
    sites pull each helper in.
- **Survey 2026-05-28** of the BUILDER-compilable tree: none of the
  above clears the "2-3+ same-shape sites" bar at the moment.
  Concrete numbers found:
    * `Concat[T]` over two managed slices: 0 sites; the only
      `Concat` callers all funnel through char-specialised
      `bootstrap.Concat`.
    * `Contains[T]`: 4 candidate sites (`containsTypePtr` /
      `containsName` / `containsPkgName` / `containsStr`) but each
      uses a different equality (Identical / charEq / streq), so
      collapsing them needs func-value comparators or method-based
      equality — gap.
    * `Reverse[T]`: 1 site (loader `popLoading`).
    * `RemoveLast` / `RemoveByValue[T]`: 1 site (also loader
      `popLoading`, but it's "rebuild minus *streq match*", which
      is `RemoveWhere` shape — not a pure index/value remove).
    * `Copy[T]` one-liner: 2 sites; most slice-copies in the tree
      are inlined in larger functions.
  So no new helper to add right now without going speculative.
- **The real next pkg/slices step** the survey surfaced: 168
  `slices.Append[T]` calls live inside `for` loops, i.e. O(n²)
  builds.  Folding those into a growable container with amortised
  O(1) append (a `Vector[T]` / `Builder[T]` shape with capacity
  tracking) is a substantive design, not a quick add — file it for
  later when the surface is being intentionally pulled into a
  proper stdlib effort.

### `pkg/std/time` has no clock — no `Now()`, no `Sleep` — 🟡 OPEN (2026-08-02)

`time` can build a `Point` only from `FromUnix`, and the sole Point that comes
from outside the program is a file's `ModTime` (`os.Stat`).  Nothing in the
stdlib reads the current time: there is no `time.Now()`, and `pkg/std/os/sys`
(the libc-syscall layer, which is where such a primitive would enter) exposes no
`clock_gettime`/`gettimeofday`.  There is no `Sleep` either.  So a program cannot
time itself, stamp an event, seed from the clock, or wait.

What it needs: a `sys` entry point over `clock_gettime` via `__c_call`, a
`time.Now() Point` on top of it, and the bare-metal variant failing with
`errors.Unsupported` like the rest of the os family.  Wall-versus-monotonic is a
real design call, and `Point`'s own doc comment already frames it ("carries no
clock identity"): a monotonic reading is not on the same timeline as a wall-clock
one, so decide whether monotonic gets its own type or `Now()` is wall-only.
`Sleep` (`nanosleep`) is a separate, smaller addition.  Found while writing the
standard-library example series in binate/examples — the planned `time` example
can only do arithmetic over constructed Points and file mtimes.

### `os` errors carry only the op, not the failing path (P3)
`pkg/std/os` `failErrno(op)` renders e.g. `"open: not found"`, but
plan-std-error-hierarchy.md §7 specifies context `(path, op)` —
`"open /etc/foo: not found"`. The path is available in `OpenFile`'s `name`
param (Create/Open delegate to it); `read`/`write`/`seek` operate on an fd and
have no path, so op-only is correct there. Add the failing path to the open
family's error context (e.g. a path-aware wrapper, or `failErrno(op, path)`).
Deferred 2026-06-11 (user: op-only acceptable for now) — low impact (message
richness, not classification). Tests: extend the `TestOpen*Classified` cases
to assert the path appears in the rendered message.

## Package management & search paths

### A deployed toolchain finds no packages of its own — which blocks `#!` scripts — 🟡 OPEN (2026-08-02)

`bni` and `bnc` have no default search path at all.  A released bundle's
`bin/bni` does not consult its sibling `lib/`, so even a script that imports
nothing fails on the core packages every program needs:

    $ bni -x noimports.bn
    package "pkg/bootstrap" not found
    package "pkg/builtins/lang" not found
    package "pkg/builtins/reflect" not found

Every invocation therefore has to pass the whole `-I`/`-L` formula, which is why
every caller shells out to `binate-paths` first.  For a shell script that is
merely verbose; for a **shebang** it is fatal.  A `#!` line must be literal — it
cannot compute anything — and the kernel truncates it at ~256 bytes (Linux).  A
bundle in the standard cache location already yields `-I` of 264 chars and `-L`
of 353: each one alone exceeds the cap.  So `bni -x` (spec §17.3.1) works only
for a caller who can shorten the paths first: `e2e/shebang-exec.sh` symlinks
every search-path component to a one-character name, which no real script can do.
The shebang feature is effectively unusable as shipped.

Fix — either, ideally both:

- **A default root relative to the executable.**  A tool at `<prefix>/bin/bni`
  defaults its search paths to `<prefix>/lib` (exactly the bundle layout), so an
  installed toolchain works with no flags at all and `#!/usr/bin/env -S bni -x`
  becomes a complete, portable shebang.  Explicit `-I`/`-L` still override.
- **The env-var fallback** (`BINATE_PACKAGE_INTERFACE_PATH` /
  `BINATE_PACKAGE_IMPL_PATH`) — the Stage 7 entry below.  It helps a caller who
  controls the environment, but does not rescue a script someone else runs, so it
  does not substitute for the default root.

Found while writing the standard-library example series in binate/examples (a
`scripting` example must stamp a runnable script with shortened paths rather than
ship one that runs).

### Package manager — sketch a design
- We don't have one yet. The current model is "everything lives under a
  root directory; `-I` and `-L` point the loader at extra search paths."
  Fine for the toolchain and a handful of conformance fixtures; doesn't
  scale to "I want to depend on `someone/foo` at version vX."
- Questions a sketch should answer:
  - Naming: are packages identified by URL (`github.com/...` Go-style),
    by a registry name, by a flat namespace? Interacts heavily with the
    package path conventions, decided in [`pkg-layout-spec.md`](pkg-layout-spec.md).
  - Manifest file format and location (`binate.toml` / `bn.mod` / TBD).
    What does a minimal valid manifest look like?
  - Dependency resolution: version constraints, lockfile, MVS vs SAT,
    handling of mutually-incompatible transitive deps.
  - Vendor / cache layout: per-project, per-user, or system-wide.
    Reproducibility story.
  - Binary artifacts vs. source: tied to the existing IMPL_PATH split
    (compiled `.o` / `.a` distribution vs. source) — see
    "Package path: binary artifacts on IMPL_PATH (Stage 8 / Phase 2)"
    below.
  - Interop with `.bni` distribution: the loader already treats `.bni`
    and impl as independent search paths; the package manager must
    respect that.
  - Bootstrap path: how does the bootstrap interpreter find packages?
    Probably "vendored copy in tree, no resolver." Confirm that's the
    right answer.
  - Out-of-tree builds: where do build artifacts go? How does the
    package manager interact with `--build-dir`?
- Output: a plan doc in `explorations/` (e.g. `plan-package-manager.md`),
  not implementation. The path conventions are already ratified in
  [`pkg-layout-spec.md`](pkg-layout-spec.md); this sketch builds on them
  (esp. its "Package manager interaction" section).

### Package path: env-var support (Stage 7)
- Add `BINATE_PACKAGE_INTERFACE_PATH` / `BINATE_PACKAGE_IMPL_PATH`
  (long names match `LD_LIBRARY_PATH`/`PYTHONPATH` style; aliases TBD)
  as the fallback when CLI flags are absent.
- The old gate (adding `bootstrap.Getenv`) is **gone**: `pkg/std/os/sys.Getenv`
  ships as of bnc-0.0.12.
- The old rationale for deferring — "direct shell invocations can construct CLI
  arguments" — does not hold everywhere: a `#!` line is literal and length-capped
  and can construct nothing, so it cannot build the `-I`/`-L` formula.  See the
  "A deployed toolchain finds no packages of its own" entry above; a default root
  relative to the executable is the stronger fix, with this as the override.
- See [`plan-package-search-paths.md`](plan-package-search-paths.md)
  § "Env vars".

### Package path: binary artifacts on IMPL_PATH (Stage 8 / Phase 2)
- Once we have a stable per-package ABI/linker contract: accept
  `.o`/`.a`/`.so` files on `IMPL_PATH` as alternatives to `.bn`
  source. `hasImplFiles(dir)` becomes "has at least one of {.bn, .o,
  .a, .so}". Precedence rule (likely .o/.a/.so wins over .bn, with
  `--prefer-source` to override) is open.
- bnc would also gather binary artifacts from `IMPL_PATH` and feed
  them to the linker automatically (today users supply via
  `--cflag`).
- See [`plan-package-search-paths.md`](plan-package-search-paths.md)
  § "Future: binary impl artifacts".

## REPL

### REPL: whether a parked declaration sees a parked redefinition of a name it uses depends on an earlier parking — decide the rule — 🟡 IN PROGRESS (claimed 2026-10-29, work-6; found 2026-10-25, probing the method-value shadowing fix)

Parked declarations resolve in groups: the strongly connected groups of the graph with an edge from each parked
declaration to the parked declarations providing the names it MISSES (check/check_pending_retry.bn).  A name a
declaration uses but does not miss — bound when it was checked, though a parked redefinition will rebind it — makes
no edge.  So here `{P, f}` resolves first, against the old h, and h's redefinition (another signature) resolves
after it and shadows it, f keeping the old h:
```
func P(n int) int { if n == 0 { return 1 }; return f() + q(n) }
func h() int { return 1000 }
func f() int { return 0 }
func f() int { return cast(int, h()) + P(0) }
func h() int8 { var _ int = P(0); return 5 }
func q(n int) int { return 100 + n }
testing.Println(f())                    1001
```
With `var x int8 = h()` in f, `{P, f}` does not check (cannot assign int to int8) and nothing resolves.  But when P
parked on h first (P names h before h is declared), P's pending names still list h when the redefinition comes,
so the redefinition, which uses P, joins P's group and f sees the NEW h — e2e/repl.sh
group-earlier-redefinition-calls-later-shadowing-one (3), its generic- and value-method variants,
group-shadowing-redefinitions-call-each-other and group-method-value-of-shadowing-method all rely on that.  A method behaves the same (`cast(int, mv.Size())` gives
1001 one way, 6 the other).  Whether a member sees the new definition thus depends on how an unrelated member
happened to park.
- Options: (a) a parked redefinition joins the group of every parked declaration that uses its name, so f sees
  the new h however the others parked; (b) only the names a declaration misses now make edges (refreshed on each
  retry), so f always sees the old h and the e2e cases above change to calling the old definitions.
- Decided (user, 2026-10-29): (a) — "(a) seems right".
- Needs: the fix, and e2e cases for the form that now sees the old definition.

### REPL: an incompatible redefinition of a generic method that a sibling still calls in the old shape — decide the rule — 🔴 OPEN (found 2026-10-03, work-6, review of the generic-methods-at-the-prompt fix; put to the user)

`type Cur[T any] struct { v T }`, `func (c *Cur[T]) Get() T { return c.v }`, `func (c *Cur[T]) Twice() T
{ return c.Get() }`, then `func (c *Cur[T]) Get(k int) int { return k }`: with an instance of Cur already
emitted the redefinition is rejected (Twice's instance no longer checks); with none it is accepted with the
shadow warning, and every later instance of Cur is rejected ("wrong number of arguments (in Cur[int].Twice
…)").  A non-generic type's sibling keeps calling the shadowed method, but a generic type's future instances
have no old method to call.  Proposed: reject the redefinition whenever a sibling no longer checks against
it, instance or not.  Also to confirm: signatures are compared at the generic level, so `Get() T` →
`Get() int` shadows even on `Cur[int]`, whose instantiated signatures match.
The same question for generic FUNCTIONS (found 2026-10-04, work-6, review of generic functions at the prompt): a
generic caller instantiated after its callee's name was rebound is checked against the current binding — `g[T]`
calls `f[T]`; `f` is rebound to a non-generic function; `g[bool]` then fails ("T is a type, not a value … (in
g[bool] …)"), and once a new generic `f` exists `g[bool]` calls it while `g[int]`, emitted before, keeps the old
one.  Decide whether old generic bodies should see the binding they were declared against.
User (2026-10-14), to (B) for generics with parked declarations unchanged: "That sounds good".  They do: a generic's names (type and value)
resolve where it was declared, and an instance reuses the declaration-time check's records
(plan-irgen-checker-types.md decision 7).  For generic METHODS that suggests replacing the proposal above: a
sibling keeps calling the old (shadowed) method, which stays instantiable — to confirm with the user.

### A constraint instance naming a type parameter that shadows a predeclared type compares equal to the predeclared one's — 🔴 OPEN MINOR (found 2026-10-16, work-6, review of the Self-constraint proposal; reproduced; pre-existing)

`func inner[U Eq[int]](u U) int`, `func outer[int any, T Eq[int]](t T) { report(inner[T](t)) }`: the declaration
check accepts `inner[T]`.  typeSatisfiesConstraint's type-parameter arm (check/check_generic.bn) compares T's bound
`Eq[<outer's int>]` with `Eq[<predeclared int>]` by Pkg and Name, and an instance's Name is its mangled name
(`Eq[int]`), which prints a type parameter bare.  `outer[bool, S]` (with `impl S : Eq[bool]`) is then rejected
only by the per-instance check ("type argument S does not satisfy constraint Eq[int] (in outer[bool, S], …)"),
and an `outer` that is never instantiated is accepted.  No wrong code.  Fix: compare instance interfaces by their
generic declaration and type arguments, not by mangled name; isDescendantInterface and implCoversInterface compare
the same way.

### `readonly T` does not satisfy T's constraint, though `readonly int` satisfies `int`'s — 🔴 NEEDS DECISION (found 2026-10-16, work-6, review of the Self-constraint proposal; reproduced; pre-existing)

Inside `func f[T lang.Orderable](t T)`, `g[readonly T](r)` (for `func g[U lang.Orderable]`) is rejected ("type
argument readonly T does not satisfy constraint Orderable"), while `g[readonly int](x)` is accepted: the
concrete-type arm of typeSatisfiesConstraint matches impls with Identical, which strips `readonly`, and the
type-parameter arm tests `t.Kind == TYP_TYPE_PARAM` without peeling it.  Decide whether `readonly T` satisfies T's
constraint (then that arm peels `readonly`, and a `Self` arm does the same), or whether `readonly int` stops
satisfying `int`'s.  The `Self` arm (binate `2aaeb6013`) peels no `readonly` either, and gen.satisfy's text (docs
`5ba93bd`) says only a parameterized impl can match a type built from type parameters or `Self`, which reads as
`readonly T` never satisfying: revise both with the decision.

### REPL: remove process-global session state (multi-session blocker)
- **Now owned by [`done/plan-embeddable-vm.md`](done/plan-embeddable-vm.md)** (scoped
  2026-06-16): the `ir` half below is increments 4–5 of that plan, which
  covers the full compiler/VM global inventory, not just the REPL's two.
  This entry's `ir/gen.bn` line numbers are stale as of 2026-06-02; see the
  plan for verified ones.
- **What**: the REPL engine keeps per-session state in PROCESS-GLOBAL
  package vars instead of threading it through the session. v1 of the
  embeddable refactor (above) lifts the cmd/bni-local ones into
  `@ReplSession` but deliberately keeps **single live session per
  process**, leaving two `pkg/binate/ir` globals in place.
- **The globals**:
  - cmd/bni-local (lifted into `@ReplSession` by Stage 1 of the
    refactor): `replLoader`/`replRoot`/`replBniPaths`/`replProcessedPkgs`
    (`cmd/bni/repl_import.bn:24-41`) and `replInitCounter`
    (`cmd/bni/repl_decl.bn:411`).
  - `pkg/binate/ir` process-globals (NOT lifted in v1, the real
    multi-session blocker): `currentChecker` (`pkg/binate/ir/gen.bn:148`,
    set via `ir.SetChecker`) and the import alias map
    `importAliasNames`/`importAliasPaths` (`gen.bn:107/110`), with
    `Save`/`RestoreAliasMapState` bracketing in `evalReplImport`
    (`repl_import.bn:101/146`).
- **Why it matters**: single re-entrant session is unaffected (the ir
  globals are set once and save/restored inside import turns as today).
  But >1 concurrent embedded session in one process needs those globals
  session-scoped (or save/restored at every `Step` boundary) — a
  separate, larger change that must land BEFORE `pkg/binate/repl` can
  honestly claim multi-session support.
- **Guidance (applies now)**: **do not add any new REPL globals.** New
  per-session state goes through `@ReplSession`. Adding a global "to keep
  a signature stable" (the exact shortcut that created the current ones,
  per `repl_import.bn:18-20`) is what this entry exists to stop.
- **When**: only if multi-session embedding becomes a goal. Not needed
  for wasm B1 (one worker = one session).

### REPL — Tier-4 follow-ups + pretty-printer (all five tiers landed) — 🟡 OPEN (low priority)
Residual (all five REPL tiers landed):
- **Tier 4**: refcount-aware shadow warning (today fires unconditionally); forced-shadow escape hatch (syntax TBD per `claude-notes.md`).
- **Pretty-printer** (`pkg/replprint`) — deferred until interfaces land (`bootstrap.println` is a temporary hack; don't entrench it).
(Background/history archived in claude-todo-done.md.)

### REPL: continuable suspend/resume (Stage 6) — 🟡 OPEN (future)

Was Stage 6 of the now-archived [`done/plan-repl-embeddable.md`](done/plan-repl-embeddable.md)
(the rest of that plan landed; its API was superseded by the `Kernel` reshape,
design in [`done/plan-repl-kernel.md`](done/plan-repl-kernel.md)). **What**: pause a
running evaluation and resume it later — the VM frame stack is heap-resident so
pure-interpreted execution is suspendable in principle, but the active frame's
control state (`pc`, `funcIdx`, `regs`, `frameBase`) is host-stack-local in
`execLoop`, so the active frame needs a side-field to hold its resume pc.
**When**: only if a host needs pause/resume (e.g. a wasm worker yielding to the
event loop mid-eval); not needed for the current Kernel request/reply model.
Tracked here so archiving the plan doesn't strand it; belongs under the Kernel
design if picked up.

## ARM32 bare-metal / native arm32 backend

### native arm32 backend — P6 (VFP + hard-float) in progress; P0–P5 done

`pkg/binate/native/arm32` (IR→ELF32) is complete through P5: baremetal soft-float is
FULLY GREEN (`builder-comp_native_arm32_baremetal` 2851/0, only the legit
`982_c_global_environ` xfail — no libc `environ` on baremetal).  **Open:** P6 (VFP +
hard-float for `arm32-linux` native) — in progress — and P7 (promote baremetal to a
blocking modeset + full unit sweep).  Authoritative live tracker (phase status, landed
commits, deferred shapes): [plan-native-arm32.md](plan-native-arm32.md).  Backend
deferrals are all **fail-loud** (an unimplemented shape emits a clean COMPILE_ERROR,
never silent wrong-code).  Delivered P0–P5 history is in claude-todo-done.md.

### ARM32 bare-metal OS endgame — FUTURE (beyond QEMU)

The QEMU-baremetal conformance path is delivered (the native backend runs green under
`qemu-system-arm` semihosting).  The remaining ambition is real-hardware OS-dev — per-board
UART drivers, MMU, crt0/linker-script conventions, a bare-metal `bootstrap.bni` — sketched
in the DRAFT [plan-arm32-bare-metal.md](plan-arm32-bare-metal.md) (needs a review pass
before implementation).  Not scoped to the current milestone.

## stdx containers: Map/Set key-type ergonomics

**STATUS 2026-07-18 — the Fn-variant unblock path is DONE for the lint sites.**
The function-taking `containers/mapfn.MapFn[K any,V]` / `setfn.SetFn[T any]` (key on
ANY type via explicit hash+eq fns — NO `lang.Hashable`) is the first of the two
unblock ways below, and it is now adopted where it cleanly fits.  LANDED (lint
cluster, keyed on the owned `@[]char` via a shared `nameHash` djb2 + `nameEq` in
`pkg/binate/lint/namekey.bn`): `unused_local.Refs`→SetFn (`578f60a0`); the shared
`refIndex` `ValNames`→SetFn + `TypeNames`→counting `MapFn[@[]char,int]` (renamed
`TypeCounts` — unused-type needs the COUNT, not membership) (`954ad648`);
`unused_func` funcReach `Reach`→SetFn + `CNames`→`MapFn[@[]char,int]` (`0486c6c4`);
and `refIndex`'s per-file qualifier set as a composite-key `SetFn[qualKey{File,Name}]`
(`f6f89eab`).  DECLINED (verified + adversarially confirmed): the VM name-keyed
lookups — `func_index.bn` (already an O(1) hand-rolled djb2 map; converting is pure
code-churn), `lookupGlobalAddr`/`lookupDataSymAddr`/`findIfaceVtable`/`LookupExtern`/
`lookupVtableAddr`.  They take BORROWED `*[]readonly char` / string-literal keys
(incl. the public `LookupFunc(*[]readonly char)` API and interp's `"main.__entry"`),
which MapFn's uniform-OWNED-`K` interface can't serve without a per-lookup `@[]char`
copy; their insert-owned / lookup-borrowed asymmetry is the correct design.  The
INTRINSIC `hashmap.Map[K lang.Hashable]`/`set.Set` path (the two `###` sub-entries
below) stays design-open, but is a nicety — not needed for the adoption above.

Motivation for both entries below: the container-adoption audit (2026-07-09,
see the `Adopt stdx/containers Vec …` opportunistic entry) found that `Vec[T]`
is usable across the non-BUILDER tools *now*, but `hashmap.Map[K lang.Hashable,
V]` and `set.Set[T lang.Hashable]` are blocked at nearly every real site —
because those all key on an *identifier or path name* spelled `@[]char`, and
only scalar primitives implement `lang.Hashable`
(`impls/core/common/pkg/builtins/lang/order.bn`; no impl for `@[]char`/`[]char`,
any slice/pointer, or any struct). Blocked sites include vm's `func_index.bn`
(an ENTIRE hand-rolled djb2 open-addressing hashmap on the hot func-resolution
path — the smoking gun), vm `LookupExtern`/`lookupGlobalAddr`/`findIfaceVtable`,
lint `unused_func` reachability + `refs`/`unused_local` membership, interp/repl
path-dedup sets, and asm/parse's const symbol table. Two complementary ways to
unblock them:

### Derived/structural Hashable for aggregates (slices, arrays, structs of Hashables) — 🟡 DESIGN OPEN (2026-07-09)
- **Idea**: make an aggregate whose components are all `lang.Hashable` itself
  `lang.Hashable`, derived structurally: a slice `@[]T`/`[]T` and array `[N]T`
  with `T: Hashable` (Hash = fold over element hashes; Compare = element-wise /
  lexicographic), and a struct whose fields are all Hashable (Hash = combine
  field hashes; Compare = field-by-field). Since `char` is Hashable (via its
  `uint8` alias), this makes `@[]char` — *the* Binate string — Hashable, so
  identifier/path-name keys "just work" with no new type.
- **Why this over a dedicated string type** (the user's steer, 2026-07-09):
  adding a distinct `String` type to be the Hashable key conflicts with the
  widespread `@[]char`-as-string convention, including `std/strings` (which
  operates on `@[]char`/`*Builder`, not a string type). We'd end up with two
  string representations and conversion friction. Structural Hashable keeps
  `@[]char` as the string and just makes aggregates-of-Hashables usable as keys.
- **Open design questions**:
  - Automatic/blanket vs. opt-in: is this a built-in structural rule in the type
    system, or a conditional generic impl (`impl []T : Hashable where
    T:Hashable`)? Binate today has NO derived/blanket impls, and the
    `AllowUniverseRecv` gate restricts who may `impl` on universe
    primitives/slices — where would these impls live, and can the constraint
    system express the conditional form?
  - Hash fold + Compare semantics (which mixing function; is lexicographic the
    intended slice `Compare`?).
  - Scope: `@[]T` and `[]T`; arrays `[N]T`; structs. Pointers (`@T`/`*T`) should
    almost certainly NOT auto-derive (identity-vs-pointee hashing is a footgun) —
    leave them out.
  - Cost: `Hash`/`Compare` on `@[]char` is O(len) — fine for map keys.
- **Relatedly — should the comparison OPERATORS drive `.Compare`? (folded in 2026-07-11)** The
  question "should any `==`-capable type automatically have a `.Compare` (with `== iff Compare==0`),
  and any `<`-capable type a `.Compare` (with `< iff Compare<0`)?" is **the same call as this entry**,
  one layer down (`Compare`, not `Hash`). The **`<`-side is moot**: the only `<`-capable types are
  the numeric scalars, which `lang` already ships as `Orderable` with a `<`-consistent `Compare` — no
  non-scalar type has `<` (operator overloading is off the table). The **`==`-side is the live one**:
  `==`-capable *aggregates* (structs/arrays, §13.6 `expr.compare.aggregate`) have `==` but **no**
  `.Compare` today; making them auto-`Comparable` with `== iff Compare==0` **is exactly this
  structural derivation** (its derived-`Comparable`/`Compare` half). Key: the **consistency guarantee**
  (`== iff Compare==0`) is only achievable by the compiler *deriving* `Compare` from `==` — a
  hand-written `Comparable` impl on an `==`-capable struct can silently disagree with `==` (like
  `Orderable`'s unenforced total-order promise). **So decide `==`→auto-`Compare` HERE:** adopt
  structural derivation → `==`-capable aggregates are auto-`Comparable` (consistent by construction),
  `Hashable` following with a component-`Hashable` constraint; keep no-derived-impls → aggregates need
  explicit impls and operator↔`Compare` consistency is at most a documented, unenforced obligation.
  (`Equatable`/`Equals` was considered and **rejected** 2026-07-11 — keep just `Comparable`+`Orderable`;
  equality stays `Compare==0`. And operators are never available on generic type params — spec
  `expr.compare.typeparam`, §13.6.)
- **Payoff**: unblocks the entire compiler-domain Map/Set class in one move,
  including deleting vm's hand-rolled `func_index.bn` hashmap in favour of
  `hashmap.Map`. Supersedes the key half of the "168 `slices.Append` in loops"
  note elsewhere in this file — the same key-ergonomics gap.

## Opportunistic code cleanups

### Migrate `pkg/semihost`'s assembly to the package-`.s` mechanism — 🟢 candidate (2026-09-21)

pkg/semihost is a `.bni`-only package whose mangled definitions
(SemihostWriteChar/SemihostExit/SemihostGetCmdline) live in
runtime/baremetal_arm32/semihost.s, injected per-target via cmd/bnc's
targetRuntimeFiles — the pre-§16.10 arrangement the package-`.s` mechanism
was built to retire. Note the current mechanism requires an impl dir with
build-included `.bn` files (§16.10: a `.s`-only dir is not an impl dir), so
the migration needs either a stub `.bn` or relaxing that rule — surface the
design choice before doing it. crt0.s (startup glue, pre-package) and the
`__aeabi_*` set (unmangled helper surface) stay link-time runtime files.
abi/07 §7.4 documents the two-arrangement split (docs e5483a0); update it
if this lands.


### Use interfaces more (where an interface is the best/natural design)
- **Framing (2026-07-16)**: the bar is NOT "opportunistic / cheap
  cleanup".  The question is *what is the best/natural implementation*
  for a given piece of code — and where an interface is that, but we
  used a lesser pattern (often because interfaces landed late, not
  because they were unwanted), it should be converted *eventually*, with
  the honest caveat that the cost may be high.  Evaluate each candidate
  by payoff (quality / consistency / bug-resistance / clarity) balanced
  against conversion cost — not by whether it's a quick win.
- **Constraint**: interfaces are supported by the current BUILDER
  (`bnc-0.0.11`), so all of cmd/bnc's dep tree is fair game.  (Generics
  too now, but they're not needed for interface adoption.)  NOTE:
  interface values must be constructed from locals, not package globals
  — `&global` iface construction was a codegen bug (fixed; see
  conformance/495).
- **Candidate 1 — native arch emit (NEAR-TERM; natural interface).**
  `pkg/binate/native/{aarch64,x64,arm32}` each have a ~30-line
  `EmitObject` that is the *same algorithm* (FinalizeStrings → `asm.New`
  → text section → per-func `emitFunc` loop → shims/strings/globals/
  vtables/descriptor/SatEntry → `ResolveFixups` → `Finalize` → write)
  over per-arch primitives, plus byte-identical name helpers
  (`stringLabel`/`stringMSSym`/`globalSymFor`) and near-identical
  `emitStringTable`/`emitGlobals`.  The natural design is the skeleton
  written ONCE against a `common.ArchEmitter` interface (`wordBytes`,
  `emitFunc`, `resolveFixups`, `writeObject`, prefix set/clear, …) with
  three impls — a real "use interfaces more" instance, not ceremony.
  Tracked/executed under its own todo (see "De-duplicate the triplicated
  native EmitObject").
- **Candidate 2 — AST/IR tagged unions (LONG-TERM; genuinely
  debatable, HIGH cost).** `ast.Expr/Stmt/Decl/TypeExpr` + `ir.Instr`
  (~138 kinds) are one wide struct + `Kind`/`Op` tag, dispatched at
  ~2200 sites across ~228 files.  This is the *expression problem*:
  tagged-union+switch makes adding a PASS cheap and a KIND expensive;
  interfaces/visitors invert it.  A compiler adds passes far more often
  than kinds, so tagged-union+switch is a standard, defensible design
  here — but "defensible" isn't "obviously best", and the missing-case
  fragility is real (no exhaustiveness checking; an unhandled op silently
  emits `; unhandled op N`).  Do NOT dismiss it as settled; but its main
  safety payoff is far cheaper via exhaustiveness checking (see that
  todo) than a 228-file rewrite.  If ever converted, it's a deliberate,
  staged, multi-month project.
- **Candidate 3 — minor**: the `asm/{elf,macho}` object writers share a
  `Write(@asm.Assembler, path, …)` shape selected by a static branch;
  a small `Writer` interface is plausible but low-payoff.  The asm
  instruction encoders and the enum→value string maps (`OpName`,
  `*KindName`) are NOT interface targets (different operand types /
  pure enum→value where `switch` is correct — an interface there is one
  empty marker type per value).
- **Landed (2026-05-26): driver `Backend` interface** (binate
  `0ee0faa`, `bda81ca`, `6dacb23`): `cmd/bnc/compile.bn`'s `Backend`
  (`compileModule`, `llvmBackend`/`nativeBackend`) collapsed the
  duplicated driver flow; pkg/native got an internal arch `Backend`.
  These + `ReplSession` are the only compiler-internal interfaces so far
  — the point above is that this is under-use to correct where natural,
  not a sign interfaces don't fit.

### Consider raw-slice-literal sugar `*[]T{...}` (language feature)
- Today a raw slice over static data is spelled `[N]T{...}` + `arr[:]`
  (a named array local, then a slice view).  Sugar `*[]T{...}` would let
  a raw slice literal be written directly.
- **Open design question**: where does the backing array live and how
  long?  The literal must materialize a backing (a stack temp) whose
  lifetime covers every use of the resulting `*[]T` borrow — same
  lifetime concern as `arr[:]` today, but implicit.  Needs a concrete
  rule (e.g. backing has the enclosing statement's / block's lifetime)
  before it can be specced; get sign-off on semantics before any impl.
- Parser + typecheck + codegen work; not a mechanical change.  Was the
  second bullet of the (now retired) "clean up conformance tests to use
  array literal + `arr[:]`" cleanup — split out because it is a language
  feature, not a test cleanup.
