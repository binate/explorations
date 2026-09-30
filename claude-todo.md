# Binate TODO

Tracks open work items, grouped by the subsystem / root cause they touch.
Completed items live in [claude-todo-done.md](claude-todo-done.md).

## CRITICAL

### native aa64: a conditional branch beyond ±1 MB is not relaxed — a very large function fails to assemble — 🔴 OPEN (found 2026-09-30, work-7, review of the exact aggregate-copy fix; pre-existing)

"PC-relative reference to 'L_…phicrit.71' is out of range or misaligned": B.cond / CBZ reach ±1 MB and
the backend does not relax an out-of-range one (invert the condition around an unconditional B).  Reached
when every aggregate copy is fully unrolled — a function copying a 16 KB aggregate a few times (about 4K
instructions per copy) at -O1.  Fails loudly at build time.  Fix: branch relaxation in the aa64 emitter
(or a loop for large aggregate copies, which the LLVM backend's per-leaf entry also wants).

### An interface value as a generic type argument (`id[GI](g)`, `type GI = *Getter`) — native prints garbage, LLVM emits invalid IR — 🔴 OPEN (found 2026-09-30, work-4, review of design B's per-instance checking; reproduced on BUILDER bnc-0.0.16; pre-existing)

```
interface Getter { Get() int }
type W struct { n int }
impl *W : Getter
func (w *W) Get() int { return w.n }
type GI = *Getter
func id[T any](x T) T { var y T = x; return y }
// main: var w W; w.n = 4; var g *Getter = &w
//       var h *Getter = id[GI](g); testing.Println(h.Get())   // expect 4
```
- **Native aa64:** compiles and prints garbage (e.g. `6135964040`), a silent wrong result.
- **LLVM:** clang rejects the IR: `ret i8* %v2` against a `%BnIfaceValue` result.

Root cause unknown; it looks as if the instance's `T` is lowered as a plain pointer rather than a two-word interface value.  The bytecode VM fails too ("call of nil interface value"), so the defect is in shared IR, not a backend.  Test: conformance 1452 (binate `3d026ea69`, expected-fail in every mode).  Needs a root-cause investigation.

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
- **With design B's per-instance body check (not yet landed):** rejected, but with a misleading error inside the generic body: `cannot assign PP to P (in eq[PP], instantiated at …)`.

`*P` satisfies the constraint, and the abstract check passes `b : T` as Self; with `T = *P`, the method's `o P` doesn't take a `*P`.  Two directions, which need a spec call:
- `*P` does not satisfy `Comparable`, so the error is at the instantiation.
- Constraint-call lowering reconciles Self with the receiver kind (passes `*b`).

## MAJOR

### Until `BUILDER_VERSION` includes binate `1f29d31e9`, gen1 silently miscompiles some statements that open a block or follow a compound statement in BUILDER-compiled code — 🔴 OPEN MAJOR (constraint until the next BUILDER release; found 2026-09-30, work-4)

bnc-0.0.16 (the pinned BUILDER) has the IR-gen defect fixed on main by `1f29d31e9`.  So in cmd/bnc's cone (the packages the BUILDER compiles into gen1), these must not be the first statement of a loop / `if` / `else` / `case` body, nor the statement right after an `if` / `for` / `switch`:
- a struct-field `++` / `--`;
- `f := someFunc`;
- assigning a variable to a `*any`.

gen1 would get silently wrong code there, and gen1 compiles every test and gen2.  Examples: a loop body that never runs (the function returns early, with no error); `st.Top++` right after an `if` or a `for` does nothing.  An identifier `++` just before it doesn't help.  A statement that goes through expression evaluation first (`x.f = x.f + 1`, a declaration, a call) is fine, and so is one in a function's opening straight-line code.  Hit by design B's `drainInstances` (`for st.Top > 0 … { st.Top-- … }`): the BUILDER-built loop returns immediately.  A 2026-09-30 scan of non-test code found no other field `++` / `--` (the only two are design B's), no `x := name` short-var, and no `*any` variable, parameter or field.  Clears when a BUILDER containing `1f29d31e9` is pinned (cut only when independently justified).

### VM: does building a non-capturing raw `*func` value add a reference to the callee's shared ClosureRec that nothing releases? — 🟡 IN PROGRESS (claimed 2026-09-30, work-5; needs investigation; found 2026-09-30 by the review of the method-value / cast fixes, unverified)

`BC_FUNC_VALUE` for a non-capturing function value (`vm_exec_funcref.bn`, `Src1 == -1`)
RefIncs the callee's shared per-function ClosureRec so an `@func` value owns a
reference, balanced by the value's scope-end RefDec.  A raw `*func` has no RefDec
lifecycle, so if the same RefInc runs for a raw `*func` construction
(`var f *func(int) int = add1` in a loop), the ClosureRec's refcount grows by one per
evaluation and it is never freed — a leak (the record is per function, so bounded
per function, but it never reaches zero).  Check with a VM unit test in the style
of `TestCastFuncRefToManagedReleasesClosureRec` (vm_funcvalue_rec_leak_test.bn);
if it leaks, skip the RefInc for a raw `*func` result type.

### Checker / IR-gen: a function literal cast to a type parameter gets no destination type — the instantiated cast borrows a freed heap closure (silent use-after-free) — 🟡 IN PROGRESS (claimed 2026-09-30, work-5; decided: implement per-instantiation checking) (found 2026-09-30 by the review of the cast-operand hint fix)

In a generic body, `var g = cast(T, func(x int) int { return x + k })` (or
`unsafe_cast`) types the literal once, while `T` is still abstract, so
`checkExprWithFVHint` installs no hint and the literal defaults to a heap
`@func` statement temporary.  Instantiated with a raw `*func` type (`viaCast[RF]`,
`type RF = *func(int) int`), the cast borrows that temporary past its statement
and the result reads freed memory (LLVM / native print garbage, the VM panics).
Instantiated with `@func` it is correct.  The literal's destination type is only
known per instantiation, which is what §12.3 `gen.mono.check` (per-instantiation
checking — specified, not yet implemented) covers.  Options: implement
per-instantiation checking for this case, or an interim IR-gen rule that picks
the literal's heap vs frame allocation from the substituted cast target.
Test: `conformance/spec/10-functions/217_funclit_cast_type_param` (`.xfail.all`,
landed `b3dbd9d35`).

### A parameterized impl's coverage is not checked per instance — dependent array lengths pass, and a call reads past the caller's array — 🟡 IN PROGRESS MAJOR (claimed 2026-09-30, work-4/session; fixed as part of design B's per-instance checking, not yet landed) (found 2026-09-30 by the review of that work; pre-existing)

```
interface Sized[T any] { Put(x [sizeof(T)]uint8) int }
type Wrap[K any] struct { k K }
impl *Wrap[K] : Sized[K]
func (w *Wrap[K]) Put(x [sizeof(K) + 6]uint8) int { … }
```
The impl is accepted because both lengths are dependent placeholders.  A call through `*Sized[int16]` with a `[2]uint8` gives a callee that sees `[8]uint8`: `len` is 8, and `x[7]` reads past the caller's array (LLVM and native aa64).  Each concrete instance of `Wrap` must re-check the impl's coverage with the bindings in place, and reject `Wrap[int16]` (8 against 2).

### LLVM backend: a >16-byte `__c_call` aggregate argument's slot is smaller / less aligned than the ABI access made through it (undefined behaviour; can fault) — 🔴 OPEN (found 2026-09-30, work-1, by the review of the bulk by-value-argument change; pre-existing)

The by-value slot a `__c_call` argument is passed through is `alloca <T>` with no explicit alignment, but:
- arm32 (AAPCS32): writeByvalArgPreamble loads the coerced words from it — for `[17]uint8`,
  `load [5 x i32], ptr <slot>` from `alloca [17 x i8]`: 3 bytes past the slot, and the load's implied 4-byte
  alignment exceeds the slot's 1 (an `ldm` from a misaligned address faults).
- x64: writeByvalArgLLVM passes it `ptr byval(<T>) align 8`, claiming 8-byte alignment the alloca doesn't have.
The slot is `%v<call>.bv<i>`, or (since the bulk by-value change) a memory-backed argument's `%v<ID>.m`, which
has the same shape.  Found reading emitted IR (`--target arm32-linux` for the arm32 case); no test shows a
failure yet.
Fix: give each slot the size and alignment the ABI access needs — allocate the coerced `[N x iW]` (as the
`.agA<i>` coercion slot already does) or round the slot up, and emit `align` ≥ the alignment the load / byval
claims; the same for a `.m` slot passed to a `__c_call`.  Add an IR-level unit test for each target.
Same class, internal calls too (found by the review of the bulk-returns change): on arm32 every sret call site
passes `ptr sret(<T>) align 8` on its `.sret` alloca, whose natural alignment is 4 for e.g. `[64 x i32]` — a
false alignment claim.  Fix it with the rest (emit the claimed alignment on the slot's alloca).

### Native aa64: a by-value aggregate parameter passed indirectly may be copied as whole 8-byte words, reading past the caller's copy — 🔴 OPEN, UNCONFIRMED (reported 2026-09-30 by the review of the bulk by-value-argument change)

Reported: the aa64 callee copies an IndirectLargeAggregates parameter as `common.ArgWords(T) * 8` bytes, so a
100-byte `[100]uint8` parameter reads 104 bytes from the caller's copy — past its end (harmless on the stack
in practice; a fault if the copy ends at an unmapped page boundary).  Not yet confirmed against the code
(start at the aa64 incoming-parameter spill and `common.ArgWords`); check x64 and arm32 for the same pattern.
Fix if confirmed: copy exactly `SizeOf(T)` bytes (a byte / halfword tail after the whole words).

### LLVM backend: `cast(*T, nil)` emits invalid IR — clang rejects the program — 🔴 OPEN (found 2026-09-30, work-1, by the review of the bulk by-value-argument change)

`var p *uint8 = cast(*uint8, nil)` type-checks but the LLVM backend emits `%v0 = inttoptr i64 0 to i8*` then
`%v1 = inttoptr i64 %v0 to i8*` — `%v0` is already a pointer, so clang fails: "'%v0' defined with type 'ptr'
but expected 'i64'".  A loud compile failure, not a miscompile.  Other backends / the VM not yet checked.
Needs a conformance test (xfail on the failing modes) and a fix in the cast lowering (a pointer-typed nil
source needs no `inttoptr`).

### LLVM backend: whole-aggregate load / store left in sret returns, call-site sret loads and zero-value construction — possible `__aeabi_memcpy` on ARM EABI — 🔴 OPEN (investigate; found 2026-09-29 by the review of the named-aggregate copy fix `d500a2af7`)

codegen lowers an aggregate OP_LOAD / OP_STORE leaf by leaf (emit_copy_ssa{,_load}.bn) because LLVM's ARM
EABI backend may lower a whole-aggregate `load <T>` / `store <T>` to `__aeabi_memcpy`, a C-library call
bare metal does not have.  Other paths still emit whole-aggregate forms: a by-value struct RETURN writes
`store %W %v, ptr %v.retbuf`, the call site reads `load %W, ptr %v.sret`, and a zero value built field by
field is then loaded as `%vN = load %T, ptr %vN.a`.  Nothing has been observed (conformance on LLVM arm32
baremetal passes), but large enough aggregates on these paths may hit the memcpy lowering.  **To do:**
reproduce on `builder-comp_arm32_baremetal` with a large by-value struct return / zero value (check the
object for `__aeabi_memcpy` references); if it reproduces, route those paths through the leaf-by-leaf
helpers.

### Interface and `impl` declarations at the REPL prompt are refused by IR-gen but stay bound in the checker — a later use crashes the REPL — 🟡 IN PROGRESS MAJOR (found 2026-09-29, work-6, recon for parking interface / impl declarations; reproduced; pre-existing; both parts claimed 2026-09-29, work-6/session — user: "Yes, claim both and start with 1.")

`interface Sizer { Size() int }` and `impl *Box : Sizer` at the prompt each print "only func / const /
var / type declarations are supported at the prompt (Tier 2)" (irgen GenDecl), but the checker has
already bound Sizer and recorded the impl (collectInterfaceDecl / collectImplDecl), and the REPL does not
undo a declaration GenDecl refuses (repl/decl.bn evalReplOneDecl prints the message and returns).  So
`var s *Sizer = &b` then `testing.Println(s.Size())` type-checks and panics "vm: extern not found:
pkg/builtins/lang.int.Size", killing the REPL.  Two parts: (1) a declaration IR-gen refuses must not
stay bound — undo it (SnapshotDecl / RollbackDecl), or reject the kind in the checker; (2) supporting
interface and impl declarations at the prompt (IR-gen registration, vtables, lowering).  The user wants
interface and impl declarations to park on names not yet declared like other REPL declarations ("I
guess they should park"), which needs (2).  Related: the rollback-gaps entry below (`impl` at the
prompt registers into c.Impls).

Part 1 (undo a declaration IR-gen refuses) landed as binate `418119a87`.  Part 2 decisions (user, 2026-09-30,
"1-3 recs seem fine; 4: do what you think is best (if it expands scope too much, then no); 5: yes"):
(1) redefining an interface, or a type over an interface or the reverse, is rejected like a type
redefinition; (2) an incompatible redefinition of a method an `impl` uses shadows it — the `impl` keeps
the old method, as existing callers do; (3) a parked `impl` is labelled `impl *Box : Sizer`, and a
conversion to its interface or any of that interface's parents waits on it; (4) generic interfaces and
generic-receiver impls at the prompt only if they do not expand the scope much; (5) first, as its own
change: build the VM vtables for impl rows minted while a prompt function or var initializer is lowered
(LowerNewImpls runs only after a statement prompt) — the two "vtable not found" entries.  Risks from the
recon: value-receiver dispatch thunks must be built from the latest definition of a method (a stale one
is the old signature calling the new body); a boxed named managed-slice / managed-pointer / array
receiver's slot-0 destructor is frozen at 0 when its vtable is built (a leak); a prompt impl's coverage
is never checked (checkAllImplsSatisfaction runs only for whole packages).

### Boxing a named type defined over a struct (`type S2 S`) leaks the struct's managed fields — 🔴 OPEN MAJOR (found 2026-09-30, work-6, review of interface / impl at the REPL prompt; reproduced, compiled and REPL; pre-existing)

`type S struct { p @Inner }`, `type S2 S`, `impl *S2 : Sizer`: dropping a `@S2` boxed into `@Sizer` leaves
the Inner's refcount one high (a file program too).  irgen boxSlot0DtorName (gen_iface_anybox.bn) names
slot 0 `__dtor_S2`, but only `__dtor_S` is emitted, so slot 0 is null.  Fix: when the receiver's
underlying type is a struct, key the slot-0 destructor on that struct's name (as the named managed
pointer receiver case keys on its pointee's).  Needs a conformance test with a refcount check.

### A REPL type defined after a forward declaration keeps a stale IR-gen type in types that used it — leak — 🔴 OPEN MAJOR (found 2026-09-30, work-6, review of interface / impl at the REPL prompt; reproduced; pre-existing)

`type S5`, `type MPP @@S5`, `type S5 struct { p @Inner }`, then dropping an MPP value leaves Inner's
refcount rising (1 → 2 → 3) — no impl involved; a box of one into a prompt interface leaks too.  MPP was
lowered while S5 was an empty forward type, and IR-gen does not refresh it when S5 is filled.  Root
cause: needs investigation (IR-gen's forward-type registration at the prompt).

### A type or interface name used as a value is accepted — `testing.Println(I)` crashes — 🔴 OPEN MAJOR (found 2026-09-30, work-6, review of interface / impl at the REPL prompt; reproduced, compiled and REPL; pre-existing)

`interface I { M() }` then `testing.Println(I)` segfaults (a file program too); a struct type name
prints `%!?(unknown)`.  The checker must reject a type or interface name where a value is required.
Needs a conformance `.error` test.

### Boxing a named managed function value into an interface panics — "no shim vtable for native interface method dispatch" — 🔴 OPEN (found 2026-09-30, work-6, review of interface / impl at the REPL prompt; reproduced, compiled and REPL; pre-existing)

`type FV @func() int`, `func (f *FV) Size() int`, `impl *FV : Sizer`, boxing a `*FV` into `*Sizer` and
calling `Size` panics in the VM "no shim vtable for native interface method dispatch" (a file program
too).  Root cause: needs investigation.

### REPL redefinition of types and interfaces, and across kinds: shadowing (the design) is not implemented; such a redefinition is rejected meanwhile — 🔴 OPEN (found 2026-09-29, work-6, review of the REPL forward-reference plan; widened 2026-09-30)

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

### A generic type that names a type declared after it is broken at the REPL prompt — wrong size, IR-gen panic — 🔴 OPEN MAJOR (found 2026-09-29, work-6, review of the REPL forward-reference rework; reproduced; pre-existing)

`type G[T any] struct { v T; w Missing }`, then `type Missing struct { a int; b int }`: `sizeof(G[int])`
prints 8 (the same program as a file prints 24), and `var g G[int]` then `g.w.b = 7` panics "internal
error: unresolved selector in IR-gen", killing the REPL.  Instantiating before Missing is declared
(`var g G[bool]` parks on Missing, then resolves) gives the same size 8.  A generic type declaration at
the prompt never parks — its body is resolved only when instantiated — so it is accepted with a missing
name, and something (the checker's instantiation or IR-gen's REPL type registration) then lays out the
field of the later-declared type wrongly.  Root cause: unknown — needs investigation.

### A type declared over a REPL variable or function name is silently ignored — 🔴 OPEN MAJOR (found 2026-09-29, work-6, review of the REPL forward-reference rework; reproduced; pre-existing)

`type Q struct { a int }`, `var P Q`, `type P struct { x int; y int }`: no error, but P stays the
variable — `sizeof(P)` then reports "P is not a type".  `func T() int { return 1 }`, `type T struct { x
int }`: no error, then `func (t *T) M() int` reports "method receiver must be a named type".  A type
declaration's name already bound to a non-type in the session scope gets no placeholder
(preRegisterTypeNames), and collectTypeDecl then leaves the binding alone or binds an unnamed struct.  Fix:
decide what a declaration of another kind over a REPL name does (replace the binding, or reject it as a
type redefinition is), and make the checker and IR-gen do it.

### A REPL variable redefined with a different type keeps the old variable's value — 🔴 OPEN MAJOR (found 2026-09-29, work-6, review of the REPL forward-reference rework; reproduced; pre-existing)

`var x int = 1`, `var x bool = true`, `testing.Println(x)` prints 1 (silent wrong value); redefining with
the same type (`var q int = 1`, `var q int = 2`) prints 2.  Root cause: unknown — needs investigation
(how a redefined variable's new global is materialized and found, and what its initializer writes).

### A value-receiver method of a named POINTER type called through an interface reads garbage — the receiver is the box cell, not the pointer in it — 🔴 OPEN (found 2026-09-30, work-3, fixing the `@any` named-owning-pointee identity; reproduced on LLVM and the VM; pre-existing)

`type H @Node` with `func (h H) Get() int { return h.v }` and `impl H : Getter`: `h.Get()` returns the
right value, but `var r *Getter = &h; r.Get()` and `var g @Getter = box(h); g.Get()` return garbage
(an address-sized number) — silent wrong value.  Looks like the interface dispatch passes the boxed
cell's address as the receiver where the value receiver is the POINTER stored in the cell (a missing
load for a receiver whose named type is itself a pointer).  Probably the same for `type P *Node` with a
value receiver (the probe's line for it was cut off by the runner's output limit — check).  Native
backends not yet checked.  Needs a conformance test over raw and managed interfaces, `@`- and
`*`-named receivers, and every backend.

### An alias to a pointer (or array / function / struct) type is accepted as a type-assertion target — `x.(*NP)` recovers a Node cell as `*(@Node)` — 🟡 IN PROGRESS (found 2026-09-30, work-3, review of the `@any` pointee-keying change; reproduced; pre-existing; claimed 2026-09-30, work-3/session — user: "yes")

With `type NP = @Node`, `x.(*NP)` compiles: the parser takes a TypeName and `assertTargetType` never
rejects a base that resolves (through the alias) to a pointer.  §11.12 allows only a nameable type or a
slice as a target, so it should be a compile error.  On a plain `@Node` box (`var r *any = n`, data word =
the Node cell), `r.(*NP)` HITS (typeInfoSymFor keys it on `main.Node`, peeling every pointer level) and
recovers the Node cell as a `*(@Node)` — dereferencing it reads `Node.v` as a pointer (type confusion).
The same gap admits an alias to an array, function or struct type.  Fix: in the checker, reject an
assertion target whose base resolves through an alias to a pointer / array / function / struct (the
kinds §11.12 already rejects when spelled directly); add a conformance `.error` test for each.

### Parse errors are printed with no file:line:col — a syntax error anywhere in a build gives no location — 🔴 OPEN (found 2026-09-29, work-3, review of the type-argument parser fix; reproduced; pre-existing)

`var x int = = 1` makes bnc print just `expected expression` / `expected ; or }` — no file, line or
column — while checker errors print `file:line:col: msg`.  In a multi-package build the user cannot even
tell which file.  Every `ParseError` carries a `Pos` (token.Pos: File, Line, Col), but each reporting
site keeps only the message: `cmd/bnc/compile.bn` ~:338 (`fmt.Println(errors[ei].Msg)`),
`pkg/binate/loader/loader_load.bn` ~:56 (.bni) and ~:128 (.bn) (`l.Errors = … errs[j].Msg`),
`pkg/binate/loader/loader_asm.bn` ~:35 (a `.s` file's build gate), and `cmd/bnc/test.bn` ~:111 (the test
runner source); check bni / bnlint / bnfmt's own reporting too.  Diagnostics quality rather than wrong
code — filed as MAJOR because it hits every syntax error; re-rank if that is too high.  Fix: format each
as `file:line:col: msg`, the same way checker errors are, ideally through one shared helper so the sites
cannot drift again.  Conformance `.error` files for parse errors are `grep -E` regexes over the message,
so they should keep matching; add a test that pins the position (a parse-error `.error` line matching
`<file>:<line>:<col>: expected expression`).

### `@any` of a named managed pointer or function value (`type H @Node`, `type F @func() int`) never matches its own `case` — 🔴 NEEDS DECISION (split out 2026-09-30, work-3, from the named-owning-pointee entry; slices / arrays fixed in binate `02857f863`)

`var a @any = box(h)` for `type H @Node` keys the box structurally (`rt.__nameless_<H>`) while `case @H:` /
`a.(@H)` key on `main.H`, so the assertion misses.  It cannot simply key by name like a named slice: for a
named POINTER type the nominal identity `main.H` already means "the data word IS the H" (an own `impl H :
I`: collectImplsFromDecl registers TypeInfo(main.H) with RecvTyp = Node, dispatch without a thunk), while
`&h` / `box(h)` put a pointer to an H CELL in the data word — two layouts.  Keying `box(h)` by name made
reflection / fmt misread it as a Node, let `a.(@Getter)` succeed and dispatch garbage, and `var gd @Getter
= h; up.(@H)` hits and segfaults on deref (that last one on main already).  Decide the convention for
boxing a named pointer type — which layout the data word carries, and which identity each spelling
(`h`, `&h`, `box(h)`) gets — then fix `case @H:` together with the dispatch MAJOR above ("A value-receiver
method of a named POINTER type called through an interface reads garbage"), which is the same root cause.
A named function value is separate: IR-gen erases F's name (`typeDeclEntryType`), so the box keys
`rt.__nameless_<@func()>` while `case @F:` keys `(main, "")` — `case @F:` / `fa.(@F)` miss (spec §11.12
allows the target).  A managed box of a named RAW pointer with its own impl (`type PS *S`, `impl PS : I`)
has the same layout conflict as H.  wrapAsIfaceValue / typeInfoSymFor carry TODOs pointing here.

### Spec decision: may a type assertion recover a MUTABLE pointer to a boxed `readonly` named value? — 🔴 NEEDS DECISION (raised 2026-09-29, work-3, review of the outer-readonly boxing fix)

`var c readonly Celsius = 21; var x *any = &c; x.(*Celsius)` succeeds today (named boxes drop the outer
readonly), handing out a mutable `*Celsius` to readonly storage without `unsafe_cast`.  §11.12 iface.assert's
literal wording ("outer-`readonly` stripped") allows it, but iface.assert.kind ("element-level readonly may
be added but not dropped") and type.readonly.drop say otherwise.  Decide which the spec means (and whether
the dynamic type, or the recovery, should keep the readonly); then pin it with a test (conformance 1429 was
deliberately limited to the handle-readonly `readonly @Box` case so as not to lock this in).

### Method values on non-addressable / read-only receivers, and the lifetime of an addressed composite literal — 🔴 OPEN, DECIDED 2026-09-30 (raised 2026-09-30, work-7, fixing the method-value-captures-a-copy bug)

Decisions (user, 2026-09-30):
1. A method value binds its receiver exactly as the call would: `x.M` is legal iff `x.M()` is, as far as
   the receiver goes.  The checker's method-value arm (check_expr_access.bn) applies none of the call's
   receiver checks today; it must apply all of them: func.method.smoothing (an implicit `&` needs an
   addressable receiver), `receiverAssignable` (a value / `*T` receiver cannot bind a `@T` method — that
   would fabricate a reference), and func.method.object-const (a read-only object binds only a
   read-only-receiver method).  Rejected by this, accepted today: `mk().Inc` (captured by value, and the
   wrapper re-copies it per call, so `h := mk().Inc; h(); h()` gives 41, 41); `s.p.Inc` with
   `s *readonly S` and `xs[0].Inc` with `xs *[]readonly P` (capture a copy); `var rp readonly P; rp.Inc`
   (captures `&rp`, and `Inc` writes the read-only object).
2. An addressed composite literal follows the `iface.construct.value-borrow` precedent (§11: a
   materialized temporary in a `var` / `:=` initializer "co-scopes with the new binding"; conformance
   11-interfaces/091).  In a `var` / `:=` initializer — `var q *P = &P{name: mk()}`, or `h := P{…}.Name`
   with `func (p *P) Name()` — the literal lives as long as the new binding: its managed fields are
   released at the end of the binding's scope, not the statement (no extra refcount operations; only the
   release point moves).  Anywhere else the literal is a statement temporary, and a raw pointer to it used
   after the statement is `mem.raw-uaf`.  Today the literal is always released at the statement's end, so
   both examples read freed memory on a later `q.name` / `h()`.  The same rule settles
   `var iv *any = P{name: mk()}`: value-borrow's text lists "a variable, field, or element" as addressable
   and "a literal, an expression, or a call result" as not, while §13 `expr.addressable` makes a composite
   literal addressable — the literal is co-scoped either way.
   (A call `P{…}.Name()` is legal and safe: it runs within the statement.)

Work: spec (§10b `func.method-value.capture` / §10 smoothing wording for method values; §13 / §18.4 the
addressed-literal lifetime; §11 value-borrow's addressable list); checker (the method-value arm's
receiver checks); IR-gen (enrol an addressed composite literal in a `var` / `:=` initializer for
scope-end cleanup, as value-borrow's materialized temporaries are); `.error` and run conformance tests on
every mode.

3. (decided 2026-09-30, follow-up to 2) An addressed composite literal in a storing position that
   outlives the statement — an assignment, a field / element store, a `return`: `q = &P{name: mk()}`,
   `return &P{…}`, `s.h = P{…}.Name` — is a compile error, like value-borrow's store rule
   (11-interfaces/090): it always dangles.  No tree code is affected (every `&T{…}` in pkg / cmd /
   conformance is a `var` initializer).

### An `unsafe_index(c, i)` result is addressable exactly when `c[i]` is — 🔴 OPEN, DECIDED 2026-09-30 (raised 2026-09-30, work-7, review of the `(&x).f` selector fix)

§15.6 describes `unsafe_index(c, i)` as "exactly `c[i]`" (without the bounds check), yet the checker
treats its result as non-addressable: `unsafe_index(arr, 2).x = 5` and `unsafe_index(arr, 2).x++`
(binate `aad5222aa`) are rejected, while `arr[2].x = 5` is accepted.  Decide which is meant — make the
result addressable wherever `c[i]` is, or state in §15.6 that it is a value.
Decision (user, 2026-09-30): addressable exactly when `c[i]` is — always for a slice or raw pointer, for
an array iff the array operand is addressable — as "exactly `c[i]`" says.  Today there is no unchecked
store at all (`unsafe_index(s, i) = v` is "cannot assign to a non-addressable value"; isAddressable,
check_addr.bn, has no arm for it), so the bounds-check opt-out covers only reads.  Work: the checker's
isAddressable arm; IR-gen's lvalue-address path for unsafe_index (the element address computation exists
for reads); §13 `expr.addressable` lists it; run tests for stores, `++`, a field store, `&unsafe_index(…)`
and the array-of-a-call-result rejection, on every mode.

### A method value on a generic receiver written as `(*p).M`, `(&b).M`, `Box[int]{…}.M` or `a.(*Box[int]).M` fails to build — 🔴 OPEN (found 2026-09-30, work-7, review of the method-value fix; pre-existing)

With `type Box[T any] struct { n T }` and `func (b *Box[T]) Inc() int`: `(*pb).Inc`, `(*pb).Get`, `(&b).Inc`,
`(&w.b).Inc`, `Box[int]{n: 3}.Inc` and `a.(*Box[int]).Inc` — native: undefined `…Box[int]3_Inc`; LLVM:
invalid IR (`%bn_S…_Box[int]`); VM: "extern not found: main.Box[int].Inc".  methodValueRecvIRType
(irgen gen_method_value_recv.bn) returns nil for a unary, composite-literal or type-assertion receiver, so
the method is named from the checker's raw instantiation spelling.  Fix: arms for those shapes (`*P` → P's
IR pointee, `&x` → a pointer to x's IR type, a composite literal → its resolved TypeRef, an assertion → its
resolved target).  Needs conformance cases (each shape, a generic `*T` and value method).

### The implicit managed→raw borrow silently drops element-level readonly — `var q *[]int = p` with `p @[]readonly int` compiles — 🔴 OPEN MAJOR (found 2026-09-30, work-7, review of the unsafe_cast gate; pre-existing)

`func h(p @[]readonly int) { var q *[]int = p; q[0] = 7 }` and `func k(p @readonly int) { var q *int = p; *q =
9 }` both compile: assignability (check/types_assignable.bn ~159 / ~164) tests the element with
`dropsConst(src.Elem, d.Elem)`, which strips the OUTER readonly of both arguments, so `readonly int` vs `int`
compares equal.  §8.4 says the borrow has "no element-level `readonly` drop".  The same leak lets `cast(*S,
p @readonly S)` through cast's safe set.  Fix: `DropsConstStrict(src.Elem, d.Elem)` (or `dropsConst(src,
d)`), then fix whatever in the tree relied on it.  Needs `.error` conformance tests (slice and pointer).

### REPL: `b.v++` / `b.v += 1` on a top-level var of a generic struct type panics in IR-gen — 🔴 OPEN MAJOR (found 2026-09-30, work-7, review of the ++/-- addressability fix; pre-existing)

At the prompt: `type B[T any] struct { v T }`, `var b B[int]`, then `b.v++` → "internal error: ++/-- target with
no address in IR-gen"; `b.v += 1` → "selector assignment target with no address" (these were silently dropped
stores before the IR-gen selector fix made them loud).  The non-generic equivalent works, and so does the
same code in a file.  Likely: the REPL global's IR-gen type for a generic instantiation is not the
instantiated struct genSelectorPtr looks the field up in.  Needs an e2e/repl.sh case.

### REPL: package-variable initializers are not run — `qa.G` reads 0 — 🔴 OPEN (found 2026-09-30, work-7, review of the duplicate-import fix; pre-existing)

With `pkg/qa` declaring `var G int = 5`, `qa.G` reads 0 in the REPL — the module's own package-level vars and
imported packages' alike, at the initial load and on a mid-session import; `bni main.bn` gives 5.  The
REPL does not call the packages' `__init` functions (or not the imported ones).  Needs an e2e/repl.sh case.

### A cast through a generic struct whose type parameter appears in no field is not deferred to instantiation — valid code rejected — 🔴 OPEN (found 2026-09-30, work-7, review of the recursive-cast fix; pre-existing)

`type P[T any] struct { n int }; func g[T any](x @P[int]) @P[T] { return cast(@P[T], x) }` is rejected at the
definition, though valid for T = int.  isTypeParamType / containsTypeParam (check/check_cast_safe.bn) calls
StripWrappers first, which drops the TYP_NAMED wrapper carrying the instantiation's InstArgs, so a type
parameter that only appears there is never seen.  Fix: check InstArgs on each named step before peeling.

### A `bool` holding a byte other than 0 / 1 is undefined behaviour; the aggregate retype nests — 🔴 OPEN, DECIDED 2026-09-30 (found 2026-09-30, work-7, review of the unsafe_cast gate; pre-existing)

§8.7 makes `int8 -> bool` an unsafe_cast direction ("a value outside {0, 1} is not a valid bool") but the spec
says neither what the scalar conversion produces nor what using such a bool does, and Ch.21 has no entry.
`var i int8 = 2; b := unsafe_cast(bool, i)`, printed as `b, cast(int, b)`: LLVM `false 0`, native `true 2`,
VM `false 2`; a retyped `@[]int8{2} -> @[]bool` element: LLVM `false 0 true` (b, int, !b), native `true 2
true` (b and !b both true), VM `false 2 false`; constant `unsafe_cast(bool, 2)`: LLVM / VM false, native true.
Decide: normalize at the conversion (any nonzero -> true), or make an invalid bool undefined behaviour
(listed in Ch.21) — and reject a constant operand outside {0, 1} at compile time either way.
Related (same review): a NESTED container retype (`[2][4]int8 -> [2][4]bool`, `@[]@[]int8 -> @[]@[]bool`) is
accepted by unsafe_cast (its element retype applied in place, recursively) while `cast`'s leaf rule looks one
level deep (`cast([2][4]uint8, a)` from `[2][4]int8` is rejected though total and bit-preserving) — state in
§8.5 whether the aggregate retype nests.
Decisions (user, 2026-09-30): (1) undefined behaviour — `unsafe_cast(bool, i)` asserts `i` is 0 or 1 (as
`*T -> @T` asserts a header); using a bool object whose byte is not 0 / 1, however it got there (a scalar
unsafe_cast, a container retype, bit_cast, raw memory), is UB, listed in §21.6; a CONSTANT operand outside
{0, 1} (`unsafe_cast(bool, 2)`) is a compile error.  `i != 0` is the defined integer -> bool.  (User: "each
backend has surprising behavior in its own way!" — sanctioned under UB.)  (2) The aggregate retype nests,
for `cast` and `unsafe_cast` alike: `cast([2][4]uint8, a)` from `[2][4]int8` is accepted.  Work: spec
§8.5 (nesting), §8.7 / §21.6 (the bool assertion); checker (the constant-operand check; `cast`'s leaf
rule recursing through nested containers, as checkUnsafeCastSet already does); tests.

### A failed interface-target assertion names the target by its bare name — qualify it — 🔴 OPEN (follow-up to `53c0e5fd5`, 2026-09-28; user: "Improving the message with the qualified name would be better, but can be a follow-up.")

`x.(*Flyer)` failing prints `type assertion failed: main.Dog is not Flyer` (gen_assert_iface.bn uses the
interface's bare `.Name`), while every other type in these messages — the dynamic type, a concrete target,
an interface inside a composite (`*[]*pkg/b.P`) — prints qualified.  Print the target qualified
(`<Pkg>.<Name>`) and update the tests that pin the bare form: conformance 1014 and the
matrix/type-assert/iface/*/abort cells (generator `conformance/gen-type-assert-matrix.py`, whose comment
documents the bare form).

### A package-level NON-type declaration named like a predeclared type (`func uint16()`) shadows it only after its own position — invalid code accepted in one order — 🔴 OPEN (found 2026-09-28, work-5, review of the named-scalar-constants fix; pre-existing)

`type N2 uint16; const c2 N2 = 5; func uint16() {}` is accepted, while the same declarations with
`func uint16() {}` first give "uint16 is not a type".  A package-level name is visible throughout the
package, so both orders must be rejected.  **Root cause:** only type declarations are pre-registered
(preRegisterTypeNames); a func / var / const of a predeclared type's name enters the package scope
only when collectDeclsBody reaches it, so earlier type references (and the scalar pre-fill) resolve
the name to the universe type.  **Fix:** pre-register (or at least reserve) every package-level name
before any type expression is resolved, so a non-type declaration shadows the predeclared type from
the start.  **Test:** checker unit test for both orders (with the fix).

### A deferred method call on a generic instantiation or an imported type panics — "defer of an unresolved method call" — 🟡 IN PROGRESS (found 2026-09-28, work-5, review of the defer named-receiver fix; pre-existing; tried and unclaimed 2026-09-30, work-7 — see the note at the end; claimed 2026-09-30, work-7/session, with the package-level-var entry: one checker→IR-gen type mapper for both)

`var b @Box[int]; defer b.Get()` and `var sb @strings.Builder; defer sb.WriteByte(…)` panic "defer of an
unresolved method call" on every backend; the direct calls work.  buildDeferMethod (gen_defer_build.bn)
names the method from the receiver's CHECKER type (baseNamedTypeName → buildMethodQualName), which carries
the checker's raw instantiation spelling / unqualified imported name, while a direct method call names it
from the receiver's IR-gen value type.  Fix: resolve the receiver's IR-gen type the way the direct call /
method-value paths do (cf. methodValueRecvIRType) instead of the checker type.
Tried 2026-09-30 (work-7): that alone does not work — defer sites are built by an ENTRY pre-pass
(registerFuncDefers, gen_defer.bn), before any local is declared, so ctx.Vars has no IR-gen type for a local
receiver (only parameters and globals resolve); and a local generic instantiation may not be instantiated
yet at that point, so ensureMethodsForInstName has nothing to emit.  The pre-pass cannot be made lazy (an
early `return` before the defer statement emits the site's exit call).  So the fix needs a checker-type →
IR-gen-type mapper (instantiate a checker instantiation via InstDecl + mapped InstArgs; qualify an imported
named type) — the same mapper the "Package-level var inferred from a generic-instantiated non-literal
initializer" entry needs; build it once for both.

### A deferred method call on a receiver whose instantiation has an array argument sized by a type parameter panics — "defer of an unresolved method call" — 🔴 OPEN (found 2026-09-30, work-7, review of the checker→IR-gen type mapper; user chose to track it separately)

`func F[T any](x T) { var b Box[[sizeof(T)]uint8]; defer b.Mark(3) }`, `F[int32](5)`: bnc panics; the
same call without `defer` compiles and runs.  The defer path names the method from the receiver's
checker type mapped to IR-gen's (irTypeFromChecker — defer sites are built at function entry, before any
local exists), and the checker's `[sizeof(T)]uint8` has no length (ArrayLenDependent, placeholder 0), so
the receiver's type has no IR-gen form.  Fix options: the checker records the length expression on a
dependent array type (an opaque AST pointer, like InstDecl) and IR-gen evaluates it under the current
instantiation — careful: evaluating `sizeof(T)` resolves T by NAME, the binder-name hazard
bindTypeParams notes; or, for a local receiver, resolve the type from its declaration's written type
(the entry pre-pass would have to find the declaration in the body).  Needs a conformance test.

### A `cast` / `unsafe_cast` / `bit_cast` that is invalid only once a generic type parameter is instantiated crashes IR-gen instead of getting a diagnostic — 🔴 OPEN (found 2026-09-28, work-5, review of the composite-literal cast fix; pre-existing design gap)

check_cast_safe.bn `checkCastSafeSet` defers validation when a side is an abstract type parameter, and
nothing re-checks the conversion per instantiation; IR-gen's backstops then `panic` ("internal error: …
reached codegen via a generic type parameter", in gen_builtin.bn / gen_cast_value.bn: narrowing an
interface, mismatched aggregate shapes, different-size slices / bit_cast, and widening a VALUE
operand (a composite literal) to an interface, e.g. `func conv[T any]() T { return cast(T, Thing{x: 42}) }` called as
`conv[*Getter]()`).  A user program should get a positioned compile error naming the instantiation, not a
compiler panic.  Fix: run the cast-safety rules on the substituted types when a generic body is
instantiated (checker-side, before IR-gen), and turn the IR-gen panics into unreachable asserts.

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

### A multi-value assignment into an interface-typed target never builds the interface value — 🔴 OPEN (found 2026-09-29, work-1, review of the evaluation-order change; pre-existing)

`iv, n = mkHello()` (mkHello returns `(@Hello, int)`, iv `@Greeter`): the extracted @Hello component is stored
into the interface slot as-is.  Before the evaluation-order change it crashed at run time; with the shared
assignment lowering (gen_assign_entry.bn) clang rejects the IR ("extractvalue operand must be aggregate").  A
parallel or single assignment gets the interface construction from genExprOrFuncRef's target-type hint (it is
driven by the right-hand AST expression — box / implicit borrow); a multi-value component is an extracted IR
value with no expression, so there is no conversion to apply.  Fix: an IR-value-level concrete→interface
construction (the value-producing half of genExprOrFuncRef's interface arms), applied in coerceAssignValue.
Needs a conformance test (LLVM, VM, native).

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

### A `.bni` forward `type X` completed by a NON-struct `type X int` in the `.bn` — checker accepts, IR-gen internal error — 🔴 OPEN (found 2026-09-28, work-1, review of the named-type identity fix; pre-existing)

`pkg/h.bni`: `type Handle` plus `func Make(v int) @Handle`; `pkg/h/h.bn`: `type Handle int`.  The
checker accepts it; every backend then panics "internal error: cast between mismatched aggregate/scalar
shapes reached codegen".  RegisterSelfTypes pre-registers every forward declaration as an empty opaque
struct and resolveTypeExpr consults structs first.  **Needs a language decision:** spec §7.12
(type.opaque.forward / single-source) describes the completion as `type Foo struct { … }` and doesn't
say whether a non-struct definition may complete a forward declaration.  If it may, IR-gen must register
the completion as the named type; if not, the checker must reject it.  Probe: a library as above, main
does `var x @h.Handle = h.Make(21)` and calls a method on it.

### A `.bni` constant `len` of a `.bni` array variable has no constant value for an importer — valid code rejected — 🔴 OPEN (found 2026-09-29, work-4, review of the constant-expression check; reproduced; pre-existing)

With `var Arr [4]int` and `const LArr = len(Arr)` in `a.bni`, an importer's `var x [a.LArr]int` fails with
"array length must be a constant integer" (printing `a.LArr` gives 4, re-lowered at run time).  Cause: while
the `.bni` scope is built, its variables are defined only in the package scope `s`, not in the build scope
`c.Scope` that `checkerEnv.Len` reads, so the constant is recorded without a value; and a variable declared
after the constant is not defined at all yet (the constant's dependency walk does not follow `len` operands
to the constants their array lengths name).  Fix: resolve a `.bni` variable a constant's `len` reads on
demand, with the constants its array length names first.  Needs a multi-package conformance test (before
and after the constant; the length from a later constant).

### A `.bni` extern `var` with no definition in the `.bn` is not diagnosed — IR-gen internal error / link failure — 🔴 OPEN (found 2026-09-28, work-1, review of the instantiated interface-alias fix; pre-existing)

`pkg/home.bni`: `var G int` (any type — scalar, interface, instantiated interface); `pkg/home/home.bn`
assigns `G` but never declares it.  Spec `decl.var.extern` / §16 (`.bni` `var`): the `.bn` **must**
define `X` with an identical type.  The checker accepts the missing definition; an importer reading
`home.G` then panics "internal error: unresolved selector in IR-gen" (VM) or fails to link
(`undefined _bn_F3_3_pkg8_builtins4_lang2_3_int3_Get` for an interface-typed one — the type fell to the
int fallback).  With the `.bn` definition in place all of these work.  Fix: the checker rejects a `.bni`
extern var the package's `.bn` files do not define (in the package's own compile, where the `.bn` files
are available).  Needs an `.error` conformance test.
Also (a reviewer of the in-place array-index change, reproduced by a second): an undefined `.bni` var
produced invalid LLVM (`extractvalue i64`), and since that change reading `A[2]` of such an array panics
in IR-gen.
Also (found 2026-09-29, work-4, reviewing the constant-expression check): with `var Arr [4]int` only in
`c.bni`, `func Get() int { return len(Arr) }` in `c.bn` reaches clang as invalid IR ("extractvalue operand
must be aggregate type" in `pkg__c.ll`) instead of a diagnostic.

### REPL: a top-level `var` initialized with a function literal panics — "vm: function not found: main.__funclit_0" — 🔴 OPEN (found 2026-09-28, work-5, review of the REPL raw-slice-literal fix; pre-existing)

At the REPL prompt, `var f *func() int = func() int { return 7 }` panics `vm: function not found:
main.__funclit_0` (before and after `1000f6105`).  runReplVarInit (repl/decl.bn) lowers the var-init
synthetic and the dtor/copy helpers EnsureReplBodyHelpers adds, but not the lifted `__funclit_<N>` the
initializer's func literal produced.  Likely fix: lower every function the generation appended to the
module (as the file-load path and the statement path do), not just the helpers; add an e2e/repl.sh case.

### arm32 hard-float: a homogeneous-float-aggregate `__c_call` ARGUMENT is passed in GP registers — C reads garbage from `s0…` — 🔴 OPEN (found 2026-09-27, work-3, stale-ABI-comment sweep)

On `--target arm32-linux` (FLOAT_ABI_HARD) `HfaAggregates` stays false, so a float-only named struct /
array rides the GP-coerced path — self-consistent between Binate's own native and LLVM sides, but NOT
what a hard-float C callee expects: AAPCS-VFP passes a 1-4 member HFA in VFP registers.  Evidence
(compile-only, clang `--target=arm-linux-gnueabihf -mfloat-abi=hard -O2`): for
`type V2 struct { x float32; y float32 }` and `__c_call("sum_v2", float32, v)`, the LLVM backend
declares `float @sum_v2([2 x i32])` and the caller loads 1.5f/2.5f into `r0`/`r1`, while the C
`float sum_v2(struct V2 v) { return v.x + v.y; }` compiles to `vadd.f32 s0, s0, s1` — it reads `s0`/`s1`.
Silent wrong values.  The checker already REJECTS the return side for exactly this reason
(`check_c_interop.bn`: "__c_call return of a homogeneous-float aggregate is not yet supported on arm32
hard-float"); the argument side has no check.  The native arm32 hard-float backend takes the same
GP-coerced path (not separately verified — no qemu-arm here).  Likely the same mismatch in reverse for
C calling Binate (`#[c_export]` / `__c_entry`) with an HFA parameter — unverified.  Fix options: (a)
interim, matching the return-side policy — reject an HFA `__c_call` argument (and `#[c_export]` HFA
params, if confirmed) on arm32 hard-float with a clear error + tests; (b) real — AAPCS-VFP HFA passing
at the C boundary (back-filling the S-slot mask, `common_callconv_vfp.bn`) on both backends.  User's
call which (and whether (a) first).  Needs a conformance test on `builder-comp_arm32_linux` /
`builder-comp_native_arm32_linux` (qemu-arm user-mode is not installed on this host).

### Methods and impls on a named function-value type are broken — link failure / runtime segfault — 🟡 IN PROGRESS (found 2026-09-27, work-5, review of the named-readonly func-value fix; pre-existing, no wrapper needed; claimed 2026-09-30, work-7/session — to start once the checker→IR-gen type mapper lands; user: "Your recs for A and B are fine.")

For `type Fn @func() int` (plain, no readonly): a method `func (f Fn) M() int` compiles to a call of an
undefined symbol (link failure), and `impl *Fn : Caller` with vtable dispatch compiles and links but
SEGFAULTS at runtime — on the pre- and post-`31c1bc297` compilers alike (the readonly-wrapped
`type RF readonly @func() int` now fails the same way).  The checker accepts both.  Likely root cause:
irgen `typeDeclEntryType` strips every named func-value type to its underlying func value (so IR-gen has
no nominal type to mangle the method / impl vtable against, while the checker still resolves the method
on the named type).  The spec allows it (§10 `func.method.receiver-base`: any named type declared in
the same package), so this is a compiler bug: IR-gen needs the named identity for method / impl dispatch
while keeping the func-value representation for calls / copies / dtors.

### Polymorphic recursion in a generic function crashes the compiler — 🔴 OPEN (found 2026-09-28, work-4, per-instantiation design mapping; reproduced on main)

`func depth[T any](n int) int { ...; return 1 + depth[@T](n - 1) }` makes bnc segfault (exit 139): IR-gen's
monomorphization instantiates `depth[int]`, `depth[@int]`, `depth[@@int]`, … without bound.  Fix: an
instantiation depth limit with a diagnostic, in the checker's per-instantiation worklist (plan above).
Needs a conformance test.

### Per-instantiation checking of generic bodies (design B) — 🟡 CLAIMED (2026-09-28, work-4; user chose "B"; to be done after the two const-redeclaration MAJORs — user: "I guess you can take on the two MAJORs next")
The constant evaluator (binate `1db847da0`) marks a value depending on a type parameter DEPENDENT; the
checker checks a generic body once, abstractly, and IR-gen evaluates DEPENDENT values per instantiation.
Until each instantiation is checked:
- an array whose length depends on a type parameter is rejected in a generic signature and with
  array-literal elements, and `len` of one is not a constant (conformance
  `spec/15-builtins/153_len_dependent_array_len`, xfail); the same for a local generic type's METHOD
  signature (`func (b *Box[T]) Get(x [len(gArr) + sizeof(T)]uint8)` called with a `[7]uint8`: "cannot
  assign [7]uint8 to [0]uint8" — substituteTypeParams keeps the placeholder 0; the imported equivalent is
  right, being re-resolved per instantiation) — commit 4 (signatures re-resolved per instantiation);
- an error that only one instantiation has (`cast(uint8, sizeof(T) * 100)` with a large T) panics in
  IR-gen with no position (`constEvalFailure`), as do the cast / `bit_cast` size checks that reach codegen
  through a type parameter (conformance 1123, 1217, 1220);
- IR-gen's own evaluation of a type-parameter-dependent constant resolves names by last registration in
  `Module.Consts`, not by scope: `{ const K = 1 }; const S = K * sizeof(T)` reads the ended block's K
  (silent wrong value; conformance `spec/12-generics/078_dependent_const_names_in_scope`, xfail; found
  2026-09-28 reviewing the const-redeclaration MAJORs);
- arrays whose lengths depend on a type parameter are identical whatever their length expressions
  (`types.Identical` compares the placeholder 0): `var a [sizeof(T)*2]uint8; var b [sizeof(T)]uint8;
  a = b` compiles, as does `[sizeof(Outer[T])]` from `[sizeof(T)]` — at int32 an 8-byte array assigned
  from a 4-byte one (found 2026-09-29 reviewing design B commit 3) — commits 4–5.
- the same placeholder makes a generic FUNCTION's dependent-length parameter wrong both ways
  (substituteTypeParams' TYP_ARRAY case keeps ArrayLen 0 and drops ArrayLenDependent): with
  `func alen[T any](x [sizeof(T)]uint8) int { return len(x) }`, `var z [0]uint8; alen[int32](z)` compiles
  and prints 4 — the callee reads 4 bytes from a 0-byte object (silent miscompile) — while `alen[int32](x4)`
  with `x4 [4]uint8` is rejected ("cannot assign [4]uint8 to [0]uint8"); a direct method call
  `b4.Fill(x4)` with `func (b *Box[T]) Fill(x [sizeof(T)]uint8)` is rejected the same way
  (`spec/12-generics/079` only calls it through an interface).  An anonymous struct parameter
  (`struct { a [sizeof(T)]uint8 }`) reaches it too.  Imported generics are right (re-resolved from the AST
  under a binder scope, `copyImportedGenericMethods`).  (Reproduced 2026-09-29 by the review of the
  readonly-substitution fix, work-3.)
Design and commit plan: `plan-constant-evaluator.md` ("Per-instantiation checking"),
`plan-generic-instance-check.md`.  Also fixes the polymorphic-recursion MAJOR entry above.

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

### Constant-evaluator leftovers — 🔴 OPEN (found 2026-09-28 by the review of constant-evaluator step 2; pre-existing)
- `const F float64 = cast(float64, 5)` fails in clang: both the old and new compiler emit invalid LLVM IR.
- `const S2 = sizeof([G2]uint8)` naming a const-group member declared later is rejected ("array length
  must be a constant integer"): the dependency walk (`collectConstDeps`) does not look into type
  arguments.  `const S = sizeof(T)` with `type T` declared later is rejected as opaque by both compilers.
- After `undefined: Undef` in a constant initializer, a follow-on "arithmetic op requires numeric
  operands" is reported for the same expression.
- REPL: `checkGroupDeclTentative` still re-checks a const group's shared initializer for its bare members,
  which restamps it; IR-gen reads the checker's per-declaration values now, so this may be harmless.  Not
  verified.

### Resolve every package-level declaration on demand (a full demand-driven resolver) — 🔴 OPEN (raised 2026-09-29, work-4, review of the package-constant fix; user chose to track it: "go with your rec")

The package-constant fix resolves constants after collection and, on first read, the constants, types (for a
layout) and variables a read needs.  One path stays partial: an inferred-type variable named while types are
collected — by an array length, directly or through a constant it reads (`type B [len(tbl)]uint8; var tbl =
mk()`) — is checked against a half-collected package, so a function, type, method or impl declared later is
not there yet; the check reports that name as "declared later than a type whose array length needs it"
rather than resolving it.  Fix: resolve every package-level declaration on demand — function signatures,
types (for any use that needs their layout or methods), methods and impls — with one cycle stack, so the
order of collection never matters.  Needs conformance tests for each of those forms and for cycles through
them (`var v = F(); func F() [len(v)]int`, `type T [len(v)]int; var v = T{}`).

### Constant `sizeof` of a type built from repeated struct fields takes time exponential in the nesting depth — 🔴 OPEN (found 2026-09-29, work-4, review of design B commit 3; pre-existing)

Eleven levels of `type Ln struct { f0 Ln-1; f1 Ln-1; f2 Ln-1; f3 Ln-1 }` and `const S = sizeof(L11)` in a
function take 8.6s of user time to compile (36s with a gen1 bnc of 2026-09-26).  Cause not confirmed:
the checker's by-value walks (`embedsOpaqueByValueSeen`, `containsByValueTypeParam`,
`layoutDependsOnTypeParam`) recurse into every field of every named type with no memo, so a DAG of named
types is walked as a tree (4^11 visits here).  Fix: memoize per named type, or stop at a named type whose
answer is already known.  Needs a compile-time test that bounds it.

### An interface alias named as a parent breaks the upcast — runtime panic / compiler ICE — 🔴 OPEN (found 2026-09-27, work-1, review of the checker forward-parent fix; pre-existing)

`interface Y {…}; interface X = Y; interface A : X {…}` then `var x *X = a` (a `*A`): the checker
accepts it, but IR-gen / the backends do not follow the alias when walking A's ancestors for the upcast —
VM "iface_upcast: target vtable not found: …_X", compiled "negative vtable slot offset (target not an
ancestor of source)".  Fix: canonicalize an alias parent to its target when recording parents (IR-gen
`collectInterfaceParents` / ParentNames) and at the upcast's target lookup.  Covered by conformance
1321_iface_alias_parent_upcast (xfail.all, binate `b78c88f61`).

### IR-gen silently lowers an unresolved identifier to the constant 0 — 🔴 OPEN (found 2026-09-27, work-1, review of the bare-name precedence fix)

`genExpr`'s `EXPR_IDENT` arm (`gen_expr.bn` ~:125, "Unknown ident — return a zero placeholder") emits
`0` for a name that is neither a local, a global nor a const.  The checker has already accepted the
program, so reaching it means an IR-gen resolution gap — and the result is silent wrong code instead of a
diagnosable failure (a generic body reading a transitively-reached package's global read 0 until
RegisterGenericBodyDeps registered those).  Fix: make the miss loud — an ICE at IR-gen time, or the same
runtime internal-error panic `genSelector`'s fallback emits — after auditing which legitimate idents (if
any) still reach it.  A sibling fallback: `lookupBareConst` / `bareGlobalIdx` (`gen_bare_name.bn`) fall
back to the consuming module's same-named const/global when the defining package's is not registered —
e.g. an imported const that neither folds nor has a checker stamp is dropped by
`registerImportConstsAndVars`, so a generic body's bare read of it binds the consumer's.  The principled
guard is "the defining package declares this name" (the checker's package scope), not "it is registered".

### An imported package's alias of a generic instantiation (its OWN generic or a THIRD package's) resolves to `int` in IR-gen — 🔴 OPEN (found 2026-09-27, work-1, review of the alias-receiver fix; reproduced by the reviewer, pre-existing; widened 2026-09-27, work-6)

pkg/home has `type VI = bx.Box[int]` (bx another package's generic): `RegisterStructTypes`
(`gen_module_register.bn` ~:117-123) resolves the alias before the generic decl is stashed (later, in
`registerImportsImpl` pass 1, `gen_import.bn` ~:152), so the entry is the `TypInt()` fallback; the correct
entry appended later (`registerImportFieldsAndFuncs`) is shadowed because `lookupTypeAlias` returns the
first match.  Effects: `var v home.VI; v.Get()` → undefined `…lang.int.Get` (LLVM/native) / VM
"unresolved selector in IR-gen"; `impl *home.VI : I` keys on (pkg/home, VI) (native link failure).  A
local `type LV = bx.Box[int]` works.  Fix: stash generic type decls before aliases are resolved, and/or
replace a stale entry instead of appending a second one.

Not limited to a THIRD package's generic (widened 2026-09-27, work-6, review of the generic-method
param-shadowing fix; reproduced): an alias of the package's OWN generic fails the same way —
`type IntBox = Box[int]` in `home.bni` (with `Box[T]` and `func (b *Box[U]) Val() U` in the same
`.bni`), then `var b home.IntBox; b.Val()` in main → link failure (undefined `…lang…int…Val`), with or
without a `home.bn`.  The checker accepts it; the failure is IR-gen's.

### A generic struct / interface that is never instantiated is never checked — invalid declarations accepted — 🔴 OPEN (found 2026-09-28, work-6, review of the declared-type-param change; pre-existing)

A generic type's fields (and a generic interface's method signatures) are resolved only when it is
instantiated (populateInstantiatedStruct / populateInstantiatedInterface), so an uninstantiated one is
never checked: `type Box[T any] struct { x Undefined }` (or `x _` with a blank `_` parameter) compiles
without a diagnostic as long as nothing uses `Box[…]`.  Done in part (binate `285d7faab`, docs `f0d69c6`): each generic struct's
instantiation with its own type parameters is now built at its declaration, which rejects one that holds
itself by value (spec `type.named.value-acyclic`) or whose fields grow without bound (`gen.mono.instances`);
the rest of a never-instantiated generic's fields and method signatures are still unchecked.  Fix: check each generic type / interface
declaration once abstractly at the declaration (its parameters held abstract, as generic functions'
bodies are checked), independent of instantiation.

### Support generic type declarations whose underlying type is not a struct (`type P[T any] [2]T`, `@func(T) bool`, …) — 🔴 OPEN (found 2026-09-29, work-4, probing the generic self-containment fix; direction decided 2026-09-30: support them, don't reject them)

The checker accepts a generic `type` declaration of any underlying type (`type P[T any] [2]T`,
`type Ptr[T any] *T`, `type Less[T any] @func(a T, b T) bool`), but instantiation fills in only a struct
body (`populateInstantiatedStruct` in check, `ensureInstantiatedStruct` in IR-gen), so every
instantiation stays an unfilled named type: `var p P[int32]` reports "cannot use an opaque type by
value", `p[1]` "cannot index this type", and a declaration that is never used compiles silently.  Spec
§12.1 `gen.typeparams` allows type parameters only on a function, struct or interface, but that wording
recorded what the implementation handled when the chapter was written (2026-06-12): no design rationale
excludes other forms (the design notes say "generic types AND functions"), and the grammar
(`TypeSpec = identifier [ TypeParams ] TypeDef`) already admits them.  Decision (2026-09-30): support
them rather than reject the declaration.

Work:
- Spec: widen `gen.typeparams` to a `type` declaration of any underlying type; define instantiation as
  substituting the type arguments into the underlying type (a distinct named type per instantiation:
  `P[int32]`, underlying `[2]int32`).
- Checker: instantiate a non-struct generic declaration by resolving its underlying type with the
  parameters bound and substituting (`substituteTypeParams`, check_generic.bn) — a generalization of
  `populateInstantiatedStruct`.
- IR-gen: naming/mangling of non-struct instantiations (the layout is the underlying type's); methods on
  such types through the parameterized receiver (`gen.method.generic-recv`).
- Tests: positive conformance tests (array, raw/managed pointer, slice, function-value underlyings;
  methods; use from another package through a `.bni`) on every mode.

Open questions for the user before the spec change: (1) generic aliases (`type L[T any] = Box[T]`) —
transparent substitution, so `L[int]` is identical to `Box[int]`?  (Related: the imported-alias-of-a-
generic-instantiation entry above.)  (2) empty (opaque / forward) generic declarations (`type L[T any]`
with no body) — consumers need the body to instantiate, so allow only as a same-package forward
declaration, or reject?

### An opaque type held by value inside an array is accepted at the declaration — 🔴 OPEN (found 2026-09-28, work-6, review of the blank-type-decl change; reproduced; pre-existing)

The declaration-site value-embedding check (checkValueEmbedding → requireSizedType, check_decl.bn) walks
only a struct declaration's TOP-LEVEL fields, so an opaque type held by value inside an array is accepted
where the type is declared and rejected only at a use: `type Op` (forward / opaque) then `type A [2]Op`,
`type _ [2]Op`, `type _ = [2]Op`, `type _ [3]struct { o Op }` all compile; `var c [2]Op` is rejected.  A
type that can never be used is thus declarable, and a blank type (a compile-time validity assertion)
passes although its type is invalid.  Fix: at the declaration, run requireSizedType over each non-forward,
non-generic type declaration's whole resolved type (recursing into array elements and nested structs),
and over the type checkBlankTypeDecl resolves.

### Bugs found reviewing the identity refactor (pre-existing) — 🔴 OPEN (found 2026-09-27, work-1; reproduced by the reviewer)

- **`defer` of a method on an interface keys on the checker's SHORT package name:** (title kept — the
  TODO in `gen_defer_build.bn` cites it.)  Interface types now carry their full package path (`53c0e5fd5`),
  so an explicitly aliased import and a non-main package's own interface work; what remains: a deferred
  call through a GENERIC interface instance (`@gen.Holder[int]`) still misses ("defer of an unresolved
  interface method") — the checker names the instance differently than IR-gen.  Fix: compute the identity
  from the receiver's IR type, as `genInterfaceMethodCall` does.

### aa64 text assembler: clang-valid instruction families still rejected (completeness) — 🟡 IN PROGRESS (listed 2026-09-26; claimed 2026-09-27, work-2/session; user: "Next, after this lands", then "yes"; landing family by family)

Loud rejections, not mis-assembly, but the assembler is meant to be comprehensive: exclusive / acquire-
release loads and stores (LDXR/STXR/LDAXR/STLXR/LDXP/STXP/LDAR/STLR/LDAPR/LDLAR/LDAPUR/STLUR), LSE
atomics (LDADD*/STADD/SWP/CAS/CASP), LDTR/STTR*, LDRAA/LDRAB, NEON LD1/ST1/LD1R and NEON / FP arithmetic
text forms, CRC32*, PACGA/XPACI, CFINV/RMIF/SETF8, TLBI/AT, the `:abs_g*:` and TLS relocation
operators, `.L` / numeric local labels, literal pools (`ldr =imm`, deliberately rejected today); and `name = expr` constants, which are defined but can't be
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
its leading literal alone — user: "The reject sounds good").  Apple's legacy NEON syntax
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
`s<op0>_<op1>_c<n>_c<m>_<op2>` form reaches any); and label differences — `l2 - l1` in a constant
definition, an immediate (`#(l2 - l1)`) or a data directive (`.uint32 l2 - l1`), which clang evaluates at
layout — none supported today (user, 2026-09-30: its own family on this list).  Newer LLVM only (Apple clang 21 rejects them; take them
when the reference clang does): FEAT_TLBID's optional Xt on the broadcast TLBI operations, DC GBVA / ZGBVA.
**Also open (found by reviews, 2026-09-29):** outside the instruction parsers a rejected
line can still emit: a data directive with trailing text or a later bad value (`.ascii "ab" x`, `.uint32 1
2`, `.int8 1, 300`, `.zero 4 x`, `.fill 2, 1, 7 x`, `.balign 8 x` padding in text) writes its bytes before
the error, and a label prefix on a rejected line (`L1: ldr x0, [x1] x2`) is still defined.  Harmless (an
error aborts the file) but the same "a rejected line emits nothing" rule; `lineRejected` in `parse_test.bn`
checks bytes but not fixups.  And x64's Intel-syntax parser reads a '$'-leading
operand (`call $foo`, `mov rax, $5`) as a symbol reference, where clang rejects it (the shared lexer takes
'$' in names for AArch64 / arm32, where clang does; an undefined, undeclared symbol still fails at the end).

### Package-level var inferred from a generic-instantiated non-literal initializer (also: interface-typed, pointer-to-foreign-type) — builds broken — 🟡 IN PROGRESS (found 2026-09-26, work-1, fixing the inferred-var miscompile; pre-existing; claimed 2026-09-30, work-7/session, with the deferred-method-call entry: one checker→IR-gen type mapper for both)

`var gv = vec.New[int]()` / `var gb = mkBox[int](6)` / `var gp = &gb` at package level (the type is
inferred and involves a generic instantiation, and the initializer is not a composite literal) fail to
link (undefined method symbols): `resolveGlobalVarType` (`irgen/gen_global_type.bn`) cannot use the
checker's inferred type because the checker names an instantiation by its source spelling
(`Box[int]`, a `TYP_NAMED` with `InstDecl`), not IR-gen's instantiated name, so such a global stays
untyped (a pointer-sized scalar slot).  A composite-literal initializer (`var g = Box[int]{...}`)
works (resolved through `resolveTypeExpr`), as do locals.  Fix: map a checker instantiation type to
IR-gen's (instantiate via the generic decl in `InstDecl` + `InstArgs`, recursively through
pointer/slice/array/iface wrappers), then drop the `checkerTypeUnmappable` skip (its TODO names this
entry).  The same skip also covers two other shapes whose checker type differs from IR-gen's (both
broken before too, found reviewing the inferred-var fix): an interface-typed inferred global
(`var gi2 = gi` with `gi *Shape` — the checker's interface carries the quoted `"main"` package
spelling; VM "extern not found: main..Area"), and one reaching another package's named type through
a pointer (`var g1 = geom.NewPoint(1, 2)` returning `@geom.Point` — methods resolved in this package:
undefined `main.Point.Sum`).  Explicitly typed forms work.

At the REPL (found 2026-09-30, work-6, review of the refused-declaration undo; MAJOR there): the same
limitation makes irgen GenDecl refuse such a var ("var decl at the prompt requires an explicit type or a
literal initializer").  One typed at the prompt is undone since the refused-declaration undo, but one
that parked and resolves on a retry is refused after the checker bound it (and after other members of
its group may have been emitted): it stays bound, its initializer runs against a global that does not
exist, and a later use crashes the REPL — `type Box[T any] struct { v T }`, `var b = mk()` (parks),
`func mk() Box[int] { var x Box[int]; return x }` ("variable b resolved", then the refusal), then
`testing.Println(b.v)` panics "internal error: unresolved selector in IR-gen".  Fixing this entry removes
the refusal.

### The REPL never runs the generic-body dependency registration — 🔴 OPEN (found 2026-09-27, work-1; the indirect-package type registration half landed in binate `1ec1766ce`)

bnc and the interp driver register, for every package a monomorphized generic body may reach without the
consumer importing it, its func externs, consts and vars (`registerGenericBodyExternDeps` →
`irgen.RegisterGenericBodyDeps`); the REPL (`pkg/binate/repl/ir_imports.bn`, initial load and mid-session
import) never does: a prompt-level instantiation of `g.Outer[int]` whose body calls `h.Inner` (h never
imported at the prompt) lacks h's signatures (a struct-returning call lowers as a scalar), consts and vars.
`RegisterGenericBodyDeps` always adds extern Funcs, which the live session module must not get
mid-session (its func index space mirrors the VM's) — it needs a signatures-only mode, as
`RegisterImportFuncSigs` has.  Add an `e2e/repl.sh` case.

### Loud miscompiles / wrong rejections found by the forwarder audit (not forwarder-specific) — 🟡 IN PROGRESS (claimed 2026-09-26, work-1 — user: "take on the bugs that you filed"; found 2026-09-26, work-1; agents' repros, not yet independently re-verified)

Each needs a test (xfail'd) + triage; grouped here so none is lost.
- **Generic body referencing a const/var of a package the consumer doesn't import directly** builds,
  then panics at run time "internal error: unresolved selector in IR-gen" in every mode:
  `RegisterFuncExterns` (`irgen/gen_register_import.bn:221-292`) registers only funcs; nothing
  registers a non-direct package's consts/vars.
- **Method value bound to a pointer-receiver method of a generic struct local** (`var mg = b.Get`,
  `func (b *Box[T]) Get()`) → SIGSEGV in LLVM, native and VM (non-generic works).
- **ICE `defer of an unresolved method call`** (`gen_defer_build.bn:168`) for `defer x.M()` with x a
  generic instance (local or cross-package), and for `var p = &home.S; defer p.Inc()`.
- **Cross-package non-generic method expression drops the receiver in the checker:**
  `home.S.Sum(s)` → "wrong number of arguments"; `*func(home.S) int = home.S.Sum` → "cannot assign
  *func()int to *func(S)int" (local `S.Add(s, x)` works).
- **Generic method expression as a value** (`var f = Box[int].Peek`) → invalid LLVM (`extractvalue
  operand must be aggregate type`), native link failure, VM SIGSEGV (local) / ICE `unresolved selector
  in IR-gen` (cross-package).
- **Bare universe `any` in an impl list or extension clause** (`impl T : any`, `interface X : any`) is
  keyed to the declaring package (`main.any`) → undefined `@__ifaceid…main…any`, VM out-of-bounds
  (`gen_impl.bn:133,347`, `gen_iface_registry.bn:166`; check the universe first like
  `isInterfaceTypeExpr`, `gen_iface.bn:73-80`) — or should the checker reject it?  Spec is silent:
  user's call.
- **An aggregator's own `.bn` sees its exposed names bare** (`check/checker.bn:199-212` copies the
  injected symbols into the implementation scope), violating `pkg.expose.surface`: `MakePoint(4, 5)`
  inside the aggregator's `.bn` checks, then IR-gen mangles it as the aggregator's → undefined.
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
- **Hazard:** `gen_type_resolve.bn:113,153,193` silently fall back to `TypInt()` on a registry miss.

### The checker rejects spec-valid non-integer container retypes (`[N]bool → [N]uint8`, named ↔ underlying, same-layout named structs) — valid code rejected — 🔴 OPEN (found 2026-09-27, work-3, fixing the LLVM array-cast bug; pre-existing)

`conv.cast.aggregate-retype`'s leaf rule admits any element conversion that is total and
bit-preserving (`cast` equals `bit_cast` on every element); its note names `bool → int8` explicitly,
and named ↔ underlying / two named types sharing one underlying are same-layout retypes "for any type"
(§8.5).  The checker's `bitPreservingElem` (`pkg/binate/check/check_cast_safe.bn`) admits only
identical elements or same-size integers, so every mode rejects, with "cast does not support this
conversion": `[3]bool → [3]uint8` / `[3]int8`, `@[]bool → @[]uint8`, `[2]Celsius → [2]float64`
(`type Celsius float64`), `[2]P → [2]struct{…}` (P's anonymous underlying), and `[2]P → [2]Q` (two
named structs, one layout).  Controls accepted: `[2]MyInt → [2]int`, scalar `cast(Q, p)`.  Test:
`conformance/spec/08-conversions/017_cast_aggregate_retype_leaf` (`.xfail.all`).  Fix: widen
`bitPreservingElem` to the leaf rule — elements identical after peeling named/alias wrappers
(`sameStructFields` for two named structs), plus `bool →` a 1-byte integer (NOT the reverse:
`int8 → bool` is partial) — keeping the readonly and managed-element exclusions.  This WIDENS what the
checker accepts (to match the spec), so confirm with the user before landing.  Codegen is ready: the
LLVM backend reinterprets an array whose element LLVM types differ (`[N x i1]` → `[N x i8]`,
`[N x %P]` → `[N x %Q]`) through a scratch slot (`89be70e05`, unit-tested); native and the VM get
their first end-to-end check when 017 un-xfails.

### Compile time is superlinear in function size — iropt mem2reg, the native allocator/liveness passes, and the compiler-wide copy-per-append `slices.Append` — 🔴 OPEN (found 2026-09-27)

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

### x64 assembler / text parser: `emitModRM` addresses the wrong location for some memory shapes, operand sizes unchecked, `[base+idx*scale+disp]` misparsed — 🟡 CLAIMED, queued (found 2026-09-25; claimed 2026-09-25, work-4/session — assembler sweep after T6(b), before (c))
**Also (2026-09-26, found by the aa64 batch review, work-2/session):** the displacement parse after a `-`
negates the whole rest of the expression — `mov eax, [rbp - 8 + 4]` encodes disp -12 (`8b 45 f4`), clang
-4; same greedy-`ParseExpr` root as the scale case (`[rcx*2 + 8]` → scale 10, encoded `(%rdi,%rcx,8)`).
The shared `expr.bn` is being made clang-faithful in the aa64 batch (ambiguous precedence mixes
rejected, logical `>>`), which changes what these greedy calls see.

(REX.X/REX.B, RIP-label operand size, operand-kind validation, per-width immediate ranges incl. the imm8-only
paths, and SZ16 imm16 landed in `d35da4a89`, see done log.)  Still open, all silent: (i) `emitModRM` wrong
addresses — a memory operand with no base but an index (`MemIdx(-1, RCX, 8, 16)`, which the parser builds
from `[rcx*8 + 16]`) encodes `[rdi + rcx*8 + 0x10]` (clang `48 8b 04 cd 10000000`); index=RSP is dropped
(`[rax+rsp]` → `[rax+riz]`); scale 3 encodes as 8; a displacement beyond int32 truncates (same unbounded
frame/field-offset callers).  (ii) Operand SIZES are not validated: mixed register/memory or register/register
sizes encode at one operand's width (`Mov(Mem SZ64, Reg SZ32)` → `48 89 08`, a 64-bit store; clang rejects
`mov qword ptr [rax], ecx`), non-SZ sizes encode as 32-bit ops, and the parser defaults an unsized `[rax]` to
SZ64 so `mov [rax], ecx` is an 8-byte store — fix by requiring matching SZ sizes in emitALU/Test/Mov/
emitUnaryRM/emitShift (every native call site already passes matching sizes) and inferring an unsized memory
operand's size from the register in the parser.  (iii) The text parser's `ParseExpr` after `*` consumes a
trailing `+ disp` into the scale (`[rax+r9*2+2]` → `[rax+4*r9]`).  (iv) `[rip + label]` is supported only by
Mov/Lea (everything else rejects it); RIP-label forms followed by an immediate need a relocation that
accounts for the trailing immediate.  (v) `x64_data_test.bn` ~61 has a "Pre-fix this" comment (not
stand-alone).  **Fix:** as listed; golden bnas-vs-clang tests per form.
### arm32 assembler: register-offset shifts, condition codes and register fields are silently masked — 🟡 CLAIMED, queued (found 2026-09-25 by the T6 b1 review; claimed 2026-09-25, work-4/session — assembler sweep after T6(b), before (c))

(The data-processing Operand2 path — immediates, shifted registers, shift kinds/amounts — fails loud and
clang-faithful since `54592d56d`, see done log.)  Still silent, all confirmed by probe vs clang: (a) `MemSReg`
(`arm32_mem.bn` ~84-93, LDR/STR register offset) masks `ShAmt & 0x1f` and `Shift & 3`: RRX becomes LSL
(`ldr r0,[r1,r2,rrx]` → `e7910002`, clang `e7910062`), `lsl #32` becomes `lsl #0`, `lsr #0` becomes
`lsr #32` (clang: plain register); the text parser also only accepts `[Rn, Rm, shift #n]`, not `, rrx`.
(b) `condBits` masks `cond & 0xf` for every encoder: cond 16 silently becomes EQ, cond 15 lands in the
unconditional space (a different instruction).  (c) Register numbers are masked `& 0xf` in the memory,
multiply/branch, MOVW/MOVT and VFP core-transfer encoders.  (d) Text parser alias selection differs from
clang: `add r0, r1, #-4` (clang: `sub r0, r1, #4`), `cmp r1, #-1` (clang: `cmn r1, #1`) are rejected
rather than substituted.  **Fix:** reuse the Operand2 path's `isCoreReg` / shift-amount validation in every
encoder; validate `cond` in `condBits`; add the clang alias substitutions in the parser; golden
bnas-vs-clang tests.

### Text assemblers truncate 64-bit immediates on a 32-bit host; the assemble path hides encoder errors — 🟡 CLAIMED, queued (found 2026-09-25; claimed 2026-09-25, work-4/session — assembler sweep after T6(b), before (c))
**Overlap note (2026-09-25, work-2/session):** the aa64 text-assembler fix (landed `0df814a41`)
deletes `asm/parse/aarch64.bn`; the new aa64 parser carries operand immediates as int64 (a value narrowed to the host `int`, e.g. an address offset,
is range-checked, never truncated) and checks
end-of-line after every aa64 instruction, and the shared `parse.bn` `ParseLine` now reports an
encoder's `ErrorMsg` with file:line (the `parse_file.bn` stop-at-first-error and the x64/arm32 parts are
untouched).

`asm/parse/aarch64.bn` ~125 and `asm/parse/x64.bn` ~258 / `x64_instr.bn` ~39, ~137 build immediates with
`Imm(cast(int, result.Val))`, truncating the int64 value on a 32-bit host (arm32 hosts are first-class).
`asm/assemble/assemble.bn` ~53 reports only a generic "assembly failed" (no message, no line) for an encoder
`SetError`, and `parse_file.bn` ~40 keeps parsing after an encoder error (checks only the parser's own flag),
so the fail-loud fixes above would surface without context.  **Fix:** keep immediates 64-bit to the encoder
(`ImmU64`-style), propagate the assembler's error message with the source line, stop at the first error.
Also (T6 b1 parse-stage finding): on all three arches the text parser silently ignores anything after a
complete instruction — `add x0, x1, x2 junk`, `add rax, rcx junk`, `add r0, r1, r2 junk`, `add r0, r1,
r2, rrx #3` all assemble (a mistyped extra operand is dropped); the arm32 `, rrx` operand path returns the
`rrx` token itself as the next token instead of advancing past it (harmless only because trailing tokens
are ignored).  Fix together: an end-of-line check after every instruction.

### native: folded-away values still get PlanFrame slots; several dispatcher cases silently drop an instruction on an unresolved operand — 🟡 PARTLY CLAIMED (found 2026-09-25; the PlanFrame-slot part rides the T6 LinearScan step, work-4; the dispatcher silent-return part stays 🔴 OPEN)

(`getOperand` fails loud on any fold-flagged id since `f0a7f78fe`, see done log.)  Still open: (a) `PlanFrame`
reserves a slot for every value, folded-away ones included (`native/common/common.bn` ~162, ~222) — wasted
frame space (visible in the arm32 managed-slice destructor loops: the frame stays `sub sp,#128` after the
folded constant's slot went unused); fixed together with dropping folded values from LinearScan.  (b) Per-op
dispatcher cases (`OP_COPY`, `OP_MANAGED_TO_RAW`, `OP_BIT_CAST`, `OP_CAST`, ~15 more `if … < 0 { return }`
sites across the three backends) silently emit nothing on an unresolved operand instead of failing loud like
the dispatch tail — a dropped `OP_COPY` leaves a phi stale.  **Fix:** replace the silent returns with
`a.SetError("<op>: unresolved operand")`.

### Checker: the function-literal destination hint leaks into operands that are not destinations (`bit_cast`, `box`, a callee) — 🔴 OPEN (MINOR; found 2026-09-30 by the spec review of the function-value destination rules)

`checkExprWithFVHint` installs `c.ExpectedFVType` for its whole destination
expression, and only `checkExprWithFVHint`, `checkFuncLit` (for its body) and
`check_decl_batch` ever clear it; an operand checked with plain `checkExpr`
inside that expression inherits the outer destination.  So in
`var p *func() = bit_cast(*func(), func() {…x…})` the literal becomes a `*func`
frame closure, though `func.lit.inferred-default` says a `bit_cast` operand is
not a destination (it gets the `@func` default); likewise a `box` operand and a
callee expression.  The effect is benign (code the spec says dangles works), but
the checker diverges from the spec.  Proposed fix: clear the hint (check with
`checkExprWithFVHint(c, x, nil)`) for every operand that is not itself a
destination — the `bit_cast` / `box` operands, callees, index / selector bases,
binary / unary operands.

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
work found these patterns in -O2 IR; each is an IR-level (iropt) transform so native benefits equally:
- Field reads of a whole load: `extract(load p, i)` with no write between the load and the extract → load the
  field from `p` directly (e.g. `var x Big = *p; return x.n` copies all of Big to read one field).
- Dead aggregate stores: a store / copy into an alloca that is never read (SROA leftovers — `mkBig().n` at -O2
  copies the 100-byte member into a slot nothing reads).
- Copy chains: a value copied into a temporary only to be copied again (load → private copy → temp slot →
  argument).
- Zero-fill then full overwrite: `var x T` followed by `x = v` zero-fills x and then overwrites every byte.
User direction (2026-09-30): the native backends are co-equal, so fixes must let native make the same
optimizations — do them in the IR, not only by handing LLVM an intrinsic.  (On hosted targets the LLVM
backend separately switches its bulk copies to llvm.memcpy / llvm.memset so clang can optimize them — a
regression fix, not a substitute for this entry.)
Measure per explorations/perf-optimization-guide.md.

### Copying or releasing an array of managed elements is emitted unrolled, one sequence per element — code size grows with N — 🔴 OPEN (found 2026-09-28, work-6, review of the range-loop operand change; pre-existing)

The copy of an array whose elements are managed (a retain per element) and its release (a release per
element) are emitted inline for every element: a local `var tmp [8000]@Box` alone takes a native
binary from 169 KB to 466 KB.  A range loop now copies its array operand into a hidden local
(`stmt.for.in.operand`), so each loop over a `[N]@T` array pays it: one loop over `[8000]@Box` gives
532 KB, three loops 1.23 MB, against 169 KB for an index loop.  The N retains and N releases are
required; emitting them unrolled is a codegen choice.  Fix: emit a loop over the elements (in IR-gen's
struct/array copy and destructor helpers) once N passes a small threshold.

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
   🟡 IN PROGRESS — **caller-saved homes in the ARG BANK X0–X7 via parallel-move
   (Stage 5d)**, claimed 2026-09-18, work-4/temp-4.
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

### native FP-register homes — follow-ups (all 3 arch ports LANDED) — 🟡 IN PROGRESS (aarch64+x64+arm32 DONE; follow-ups open; see plan-native-fp-register-homes.md)

**ALL THREE ARCHES LANDED** — float SSA values home in FP registers instead of round-tripping
through GP slots:
- **aarch64** (2026-09-21, work-5; `d7eb2cbd5` `2c865f627` `d5bffa3ca` `86468170a` `a0afe37ec`):
  D8..D15 / D18..D31.  Measured native user-CPU: fasta 2.04×→1.88× (~8% faster), mandelbrot
  ~11.7×→~5.1× (2.3× faster).  Full write-up in `claude-todo-done.md`.
- **x64** (2026-09-21, work-5; `945129d67`): XMM8..13 (SysV has NO callee-saved XMM, so all XMM homes
  are caller-saved; call-spanning floats spill).  f32 IS homed.  Native x64 conformance 3047/0.
- **arm32** (2026-09-21, work-5; `6daae4f1a`): callee-saved VFP D8..D15, hard-float (AAPCS-VFP) only;
  soft-float unchanged (empty FP descriptor).  f64-only (f32 deliberately un-homed — see follow-up).
  builder-comp_native_arm32_linux conformance 3047/0, arm32 unit 395/0.  arm32 speed not directly
  measurable on the dev box (qemu not cycle-accurate); compute-path win verified structurally
  (per-access VMOV round-trip gone from the disassembly).  Both x64+arm32 got clean adversarial
  reviews (no correctness bug).

Remaining (follow-ups):
- **arm32 f32-homing** — ✅ **DONE (`f72d23dbd`)**.  arm32 now homes f32 in the low S-view of its
  callee-saved D-register home (D8..D15), reaching full f32+f64 FP-home parity.  `nextReg` hands out
  a GP scratch for an FP home (NOT the D-home number, which a GP encoder mis-encodes to r15/PC) and
  `handleResult` VMOVs it into the S-view; `getFloatOperandF32` + the arith/cast/compare emitters
  compute straight into the S-home (the f32 analogue of the f64 Stage-3 — `635_float32_arith`'s main
  dropped 49→15 VMOVs); `unhomeF32Values` deleted.  Full native_arm32_linux conformance 3048/0;
  adversarial review clean (all 8 vectors, incl. the f64→f32 Sd⊂Dm narrow aliasing).
  RESOLVED the two "latent gaps": (1) arm32 `nextReg` fixed as above; `getOperand`'s FP-home branch
  (low-single read) is now correctly the live f32-consumer path.  (2) aarch64's `nextReg` needs NO
  change — it returns the D-home for an FP home, which is correct THERE because aarch64 producers
  dispatch on `isFpReg(rd)` and use FP instructions (Fldr_s / Fmov / the arith's `if isFpReg` path);
  arm32's bug was specific to its GP-compute model (the review conflated the two).  Confirmed by the
  green aarch64 f32-homing conformance and the adversarial reviewer.
- **hard-float unit coverage** — ✅ **DONE (`bcb4e21eb`)**.  Added two arm32 hard-float unit tests
  exercising the D-home marshalling directly (homed float64 call-return VMOVs D0→home;
  homed float64 param VMOVs its CPRC reg→home), plus conformance 1280 (>8 float64 params read in a
  loop).  CORRECTION: the note written here claimed the overflow-HOMED param branch was "nearly
  unreachable … correct defensive code".  Both parts were wrong: it is reachable at -O1/-O2 (1280
  fails there) and it loads a float64 with VLDR.32 — see the MAJOR entry "native arm32 hard-float:
  an FP-homed OVERFLOWED float64 param is loaded with VLDR.32".
- **x64 f32 upper-bits comment** — ✅ **DONE (`bcb4e21eb`)**.  Reworded x64_float.bn's two Movapd
  comments to state the f32 upper bits may be dirty but no consumer reads them (rather than claim a
  clean-upper invariant that isn't maintained).  Left x64_regmap.bn:189 (its `Movd` genuinely
  upper-zeroes — accurate).  The follow-on note "x64 `emitFusedFieldStore` stores a homed float via
  the GP bridge" turned out MOOT as stated — `fieldAccessFusable` excludes floats on every backend, so
  no float ever reaches the fused field store.  The real item is the float field fold below.
- **arm32: fold int64/uint64 struct-field addressing** — 🟡 IN PROGRESS (claimed 2026-09-26,
  work-5/session).  The 64-bit fused field path (emitFusedFieldLoad64/Store64) is type-agnostic;
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
  scaled register-offset load/store (FusableElemGeps); x64 and arm32 materialize every element
  address — e.g. arm32 n-body's `bodies[i].x` recomputes it each access (`mov r5,#56; mul; add`).
- **arm32: callee-saved GP homes** — 🔵 OPEN.  arm32 homes are caller-saved R0..R3 only, so a value
  live across a call (e.g. n-body's loop indices across `Sqrt`) can't be homed and is spilled.
- **aarch64 follow-ups to close more of fasta's residual gap**:
  - fold float array-element addressing — ✅ **DONE (`1ca914edc`)**.  A float `a[i]` load/store now
    folds its address into a scaled register-offset FP load/store straight into the D-home
    (`ldr d31,[x23,x5,lsl #3]`); new asm FldrRegScaled_d/_s + FstrRegScaled_d/_s, the shared
    element-fold analysis gained an `allowFloat` gate (aarch64-only; x64/arm32 unaffected — they
    don't fold element GEPs), the emitter dispatches on the FP-home class.  Measured fasta N=25M
    (instructions retired, byte-identical output): 1.06% fewer.  Native aa64 conformance 3047/0;
    adversarial review clean.  (Only the ELEMENT fold; the field fold for floats — via
    FusableFieldGeps — is a separate smaller item, left untouched.)
  - home loop-invariant float constants — ✅ SUBSUMED by the FP-homes work (the pre-FP-homes premise
    is obsolete).  Investigated on mandelbrot post-FP-homes: loop-invariant consts (2.0, 4.0) now
    materialize ONCE at function entry (`mov x9,#0x4000000000000000; fmov d11,x9`) and are HOMED in
    callee-saved D8..D15 — the inner loop reads `fmul d29,d15,d28` from the const home, no per-use
    re-materialization.  The residual FP gap is a DIFFERENT, harder problem → new item below.
  - **FP register pressure / spill-cost priority** — 🔵 OPEN (the real residual, found investigating
    the above).  mandelbrot's inner loop spills the loop-CARRIED variables (Zr/Zi/Tr/Ti) to slots
    (17 `ldr/str d,[sp,#0x7..]` round-trips/iteration) while the 8-register D8..D15 pool is full of
    consts + other homes.  Two levers: (1) **CSE float constants** — the same const `2.0` is homed in
    THREE separate D-regs (d9,d11,d15) because each literal is a distinct OP_CONST_FLOAT; CSE'ing them
    frees FP homes.  (2) **spill-cost priority** — a loop-carried variable (reload AND store each
    iteration) should out-prioritize a reload-only const for a home; check whether computeSpillCosts
    accounts for store cost.  Also possible: use caller-saved D0..D7 for short-lived homes to enlarge
    the effective pool.  Needs measurement (mandelbrot/spectral-norm/n-body) before committing to a
    lever.

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
- **T6 — native peephole + regalloc polish — 🟡 IN PROGRESS (claimed 2026-09-21, work-4/session).** dead-load elim, drop branch-to-fallthrough,
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
    log; the follow-ups in the plan below stay with this session); (c) phi-copy coalescing (the bigger
    007 lever — regalloc-core, regression risk) — 🔵 OPEN, queued after (b) by the same session.**
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
    - **Folded values still take part in LinearScan** — 🟡 CLAIMED, queued after (b), before (c) (work-4)
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
  reloaded" item below); (b) three stores per iteration write field values that are already dead
  (the lazy spill keeps saving them) — not yet investigated.  (Latch phi-copy moves are gone since
  8ef99bd39; field loads sit at their uses since ac6d08b92.)

- **n-body is ~90% software `math.Sqrt` on BOTH backends — 🔵 OPEN (found 2026-09-24).** callgrind:
  native 88%, LLVM 90% of instructions in `math.Sqrt`'s bit-by-bit loop (neither emits `sqrtsd` /
  `fsqrt`); the source notes "a hardware sqrt intrinsic may replace this as a fast path later". So
  n-body's native/LLVM ratio is essentially the codegen gap on that one integer loop, and both
  backends are far from C (which uses `sqrtsd`). A hardware sqrt (per-arch asm or an intrinsic the
  backends lower) is the large lever for n-body; the loop's native codegen (spilled loop-carried
  values, shift counts reloaded into `cl` from stack slots) is the gap lever.
- **x64 `emitCallIndirect` diverges from `emitCall` (latent)** — 🔵 OPEN (found 2026-09-29 in review of
  the x64 caller-saved-homes work; pre-existing). `pkg/binate/native/x64/x64_call_indirect.bn`
  `emitCallIndirect`: (a) no sret shift in `argTypes`, so for a big aggregate / big multi-return
  result the post-loop `LEA RDI` overwrites arg 0; (b) floats beyond XMM7 are silently dropped (no
  stack overflow path); (c) SSE aggregates go down the GP `emitAggregateArg` path, so a pure-SSE
  aggregate is placed nowhere. Probably unreachable today (OP_CALL_INDIRECT is used for dtor /
  free_fn / trampoline calls with scalar-or-void results), but each is a silent miscompile if a
  new caller reaches it. Fix: share emitCall's argument placement, or assert the supported shapes
  loudly. Needs a test that pins whichever is chosen.
- **Native aggregate-load elision: aggregate-typed extract read after its checked interval (latent)**
  — 🟡 IN PROGRESS (claimed 2026-09-30; found in review of the extract-sinking pass; pre-existing).
  `native/common/common_aggload_elision.bn`: the S-alloca and S-adjacent shapes accept any extract
  as a read-only use (`lastReadOnlyUseIndex`), and only S-extract rejects aggregate-typed extracts.
  An aggregate-typed extract lowers to an ADDRESS inside the load's source (aarch64 `emitExtract`
  `Add` when `SpillHoldsAggregatePointer`; check x64/arm32), so its consumer reads the source later
  than the extract — outside the (load, last use] interval the store/purity checks cover. Shape:
  `ld = load A; e = extract(ld, k) /*aggregate*/; store A (or any writer); consume e` → the consume
  reads the overwritten bytes (silent wrong value). Not reproduced: tuple assignment
  (`a, b = y, a.inner` / `c, a = a.inner, y`) is correct on native and LLVM at -O0/-O2 (IR-gen copies
  RHS first). Next: find whether IR-gen can produce the shape at all; either way close the analysis
  gap (treat an aggregate-typed extract's uses as uses of the load, or reject aggregate-typed
  extracts in every shape), with a unit test on AggLoadElidable.
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

### Baremetal console output is unwired — `os.Stdout` is an empty `@File`, so `fmt` is silent; make it PLUGGABLE — 🟡 OPEN (found 2026-09-18)

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

Tests: unit tests `fmt_printf_test.bn` + `fmt_printf_fields_test.bn` (`&`-boxed
operands until CHECK_TOOLS carries value-borrow — see below), conformance
`1135_fmt_printf`.

**Note (CHECK_TOOLS lag):** the hygiene `lint` bnlint (`CHECK_TOOLS_VERSION`,
bnc-0.0.12-pre3) predates the implicit value→`*any` borrow, so LINTED stdlib code
(incl. fmt's own tests) must `&`-box operands (`Sprintf("%d", &n)`), not pass them
bare.  A CHECK_TOOLS bump to a bundle carrying value-borrow (the `9d04870b`
string-literal box + the earlier scalar/var value-borrow) would let those tests
drop the `&`.  The non-linted conformance tests (1090/1135) already use the bare
form.

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

**Remaining (surface as programs need them):** the x64 text parser is narrower
than the x64 *encoder* for the non-typical surface — SSE / float / xmm forms, and
exotic addressing modes — so a program using those may hit a parser gap.  Audit
`pkg/binate/asm/parse/x64*` against `pkg/binate/asm/x64` when such a consumer
appears.  **aarch64 → ELF is DONE** (`846802a77`): `bnas -target linux-aarch64`
routes aarch64+linux to `elf.WriteAArch64` (the object format now follows the OS
via `AssembleFile`'s `osName`; `""` keeps the per-arch host default).  The aa64
text parser also gained the `#:lo12:label` ADD operand (`4dbd8bb4e`), so a
hand-written ADRP+ADD pair reaches a datum; `e2e/bnld-linux-aarch64.sh`'s `hellopg`
runtime-proves R_AARCH64_ADR_PREL_PG_HI21 + R_AARCH64_ADD_ABS_LO12_NC through bnld
on linux/arm64.  Also landed `ldr xt, [xn, #:lo12:label]` (LDST64 lo12, `7419309b8`), so hand-written
ADRP+LDR loads a datum too.  Still narrower than the encoder on the aa64 side: only
the GOT `:got:`/`:got_lo12:` operands remain absent from the text parser (the native
backend emits those via the library, not text asm; and bnld rejects GOT relocs — a
hermetic linker — so there is no consumer for them yet).

## bnld (self-hosted linker)

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

### opaque-export of an external-C managed type would call a never-defined dtor — 🟡 LATENT (noted 2026-09-03)

**Latent — not reachable today.** The opaque-export dtor guarantee (landed b02ca1fff /
f7c7495e5: a package force-emits a public `__dtor_X` for a `.bni` forward-declared type,
and an importer's opaque `@X` drop RefDecs through it) assumes the defining package actually
BUILDS + LINKS that dtor.  A `.bni` that forward-declares `type X` with NO `.bn` body and NO
in-tree provider — held as `@X` and dropped — would emit a call to an undefined
`pkg.__dtor_X`.  Cannot arise now: every shipped opaque forward-decl has a `.bn` body, and a
managed `@X` (refcount-headered) cannot name a purely-external C/asm allocation (those are
`*X`, not `@X`).  Only relevant if external-C opaque MANAGED types are ever introduced —
then the checker should require an in-tree/linked dtor for an opaque-exported managed type.
Found: adversarial review of the opaque-export dtor fix.

## bnfmt (self-hosted formatter)

### bnfmt moves comments inside a function literal — 🟢 LOW (found 2026-09-28, review of the bnfmt defer fix)

`printFuncLit` (`pkg/binate/format/print_stmt.bn`) prints the literal's body with no comment cursor, so a
comment inside a function literal's body — or trailing the line that opens a multi-line literal — is
re-emitted on its own line before the next statement instead of in place.  Nothing is lost, but the comment
moves, e.g. in the common `defer func() { // why\n ... }()` pattern.  Fix: thread the comment cursor into the
literal's body like printBlock does for statement blocks.

### bnfmt prints `for ;; {` with a double space — 🟢 LOW (found 2026-09-28, review of the bnfmt defer fix)

`printFor` (`pkg/binate/format/print_stmt.bn`) emits the separator space before an absent post statement,
so `for ;; {` becomes `for ; ;  {` and `for ; i < n; {` becomes `for ; i < n;  {`.  The output reparses the
same and is stable, but not canonical; the for-clause tests compare tokens only, so they miss it.  Fix the
spacing and add a byte-exact test.

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

### bnlint: `func-value-escape` and `managed-func-raw-capture` do not look inside composite literals — 🔴 OPEN (MINOR; found 2026-09-29 by code reading in the review of the composite-literal function-literal hint fix, not run)

`func-value-escape` flags only a bare function literal in `return` position, so
`return H{g: func(x int) int { return x + k }}` — a frame-owned `*func` closure
escaping through the returned struct — is not flagged.  `walkExprFuncLits`
(`pkg/binate/lint/func_value_escape.bn`) never descends into a composite
literal's `Elems`, so `managed-func-raw-capture` misses an `MFn` / `@func`
composite element that captures a raw pointer.  Proposed fix: walk
`Elems[i].Value`, and apply the return check to `*func`-typed composite elements
recursively.

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

### Split `pkg/binate/ir.bni` — at 975 of its 1000-line cap — 🔴 OPEN (raised 2026-09-30, work-3; user: "File the splitting of ir.bni as a todo.")

`ir.bni` reached 975 lines with the module-level `ir.RegisterModulePendingDtor` (binate, the `@any`
named-owning-pointee identity fix).  A `.bni` cannot be split within its package (the loader reads one
`<pkg>.bni`), so this means peeling a cohesive part of `ir`'s API into an acyclic sub-package, as with
ir -> {irbuild, iropt, irdata}.  Its sections: IR data structures (~20-565), Module / Function / Param /
Block, string constants, static-data / RTTI gather (~649-664), helpers, module init/entry emission
(~705-732), and "Shared IR-construction utilities (used by irgen)" (~733-972, the largest non-data-model
part — a natural candidate).  The file also ends with an empty "Structural IR verifier (verify.bn)"
section header (the verifier lives in irbuild now) — delete it.  Do this before anything else grows
`ir.bni`.

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

### `os.RemoveAll` is path-based — a concurrent directory→symlink swap mid-walk can make it delete outside the tree — 🟢 LOW (found 2026-09-28, review of `6b1044949`)

`RemoveAll` walks by name (`Lstat`, then `ReadDir` / `Remove` on `path/...`), so another process that can
write into the tree can replace a subdirectory with a symbolic link between the `Lstat` and the later
calls, and the walk then deletes through the link (the class of Rust's CVE-2022-21658 `remove_dir_all`).
Documented as a limitation in `os.bni` (fine for its current users: private 0700 `MkdirTemp` dirs).  A
race-free walk needs directory-fd-relative calls that `pkg/std/os/sys` lacks: `openat(O_NOFOLLOW |
O_DIRECTORY)`, `fdopendir`, `unlinkat(AT_REMOVEDIR)` (and an `fstatat(AT_SYMLINK_NOFOLLOW)`), on every
hosted target.

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

### Generic functions and methods of generic types declared at the REPL prompt panic when called — 🔴 OPEN MAJOR (found 2026-09-29, work-6, review of the REPL failed-prompt fix; reproduced 2026-09-30; pre-existing)

`func id[T any](x T) T { return x }` then `testing.Println(id[int](3))` panics "vm: extern not found:
main." (the call names an empty function).  `type Box[T any] struct { v T }`, `func (b *Box[T]) Get() T
{ return b.v }`, `var bx Box[int]`, `testing.Println(bx.Get())` panics "vm: extern not found:
pkg/builtins/lang.int.Get" (the receiver resolved as int).  irgen GenDecl lowers a generic function or
method declaration as an ordinary one (genFunc / genMethod) instead of registering it for instantiation
at its call sites, as GeneratePackage does (gc.GenericDecls, stashGeneric…).  Root cause: needs
investigation.  Generic types, generic interfaces and generic-receiver impls declared in an imported
package work (e2e tier5-box-generic-receiver-impl-instantiation).
Found 2026-09-30 (work-7, building the checker→IR-gen type mapper): generic TYPE declarations typed at
the prompt are not registered for instantiation either — the REPL lowers a `type` through GenTypeDecls,
which never stashes a generic struct decl (stashGenericStructDecl), so every IR-gen instantiation of a
prompt-declared generic type falls back to `int`: `type Box[T any] struct { v T }` then
`func mk3() Box[int] { var x Box[int]; x.v = 7; return x }` panics "internal error: selector assignment
target with no address in IR-gen"; a local, global or field of such a type is mis-typed, and a var
inferred from one (`var a = mk()`) is refused ("var decl at the prompt requires an explicit type …") because
the mapper finds no generic decl to instantiate.  Likely also the root cause of the `b.v++` entry
("REPL: `b.v++` / `b.v += 1` on a top-level var of a generic struct type panics in IR-gen").
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

## native arm32-linux: variadic-floats-in-GP DONE — mode now green, promotable to P7

- **Landed `5d8181d86` (2026-08-25):** `native/arm32` now passes variadic FLOAT args
  in GP registers (AAPCS-VFP base-standard rule), fixing `regressions/c-call/
  printf-variadic-float` — the ONE real failure that kept `builder-comp_native_arm32_linux`
  non-blocking.  New `CallConv.VariadicFloatInGp` (arm32-hard-float only) makes the shared
  V-walkers classify a variadic float as its same-width uint (GP pair, never VFP);
  arm32 `emitCallArg` skips the VFP peel for variadic floats.  **Validated end-to-end under
  qemu-arm** (a fresh `bnarm1` container on this worktree): printf-variadic-float PASSES,
  707/888/926 stay green → the mode is now **2982/0**.  Adversarial review clean on 6 hazards.
  **FOLLOW-UPS:** (1) a variadic FLOAT32 currently fails loud (C promotes variadic float→double;
  frontend doesn't yet) — proper fix is VCVT-promote float32→float64 in the emit; (2) with the
  mode green, promote `builder-comp_native_arm32_linux` false→blocking in conformance-tests.yml
  (plan-native-arm32.md P7), pending a green CI run of the landed fix.
