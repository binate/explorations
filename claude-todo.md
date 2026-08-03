# Binate TODO

Tracks open work items, grouped by the subsystem / root cause they touch.
Completed items live in [claude-todo-done.md](claude-todo-done.md).

---

## CRITICAL

## MAJOR

### VM float64 negation is `0.0 - x`, so `-x` of a zero loses the sign — compiled and interpreted disagree — 🔴 OPEN MAJOR (found 2026-08-02)

**Severity: MAJOR (silent wrong value, and a cross-mode divergence).** Not a
crash — the two execution modes simply compute different values for `-x` when x
is a zero, which breaks the dual-mode contract that a value is a value in either
mode. Narrow in reach (only ±0 is affected: for every nonzero x, `0 - x` and
`-x` are bit-identical) but it is a core arithmetic operator, and a negative zero
is observable — `math.Signbit`, `1/x` → ∓Inf, and the bit pattern.

**Repro** (released bnc-0.0.12 and a main-built bnc-0.0.13-pre1; `zero(1)`
returns a runtime +0.0 that no constant folding can reach):

    var lit float64 = -0.0
    var neg float64 = -zero(1)

    compiled:      literal -0.0  bits 0x8000000000000000 signbit true
                   runtime -x    bits 0x8000000000000000 signbit true
    interpreted:   literal -0.0  bits 0x0000000000000000 signbit false
                   runtime -x    bits 0x0000000000000000 signbit false

`math.Copysign(0.0, -1.0)` and `-1.0 * x` produce a correct −0.0 in BOTH modes,
so only the negation operator is wrong.

**Root cause** — `pkg/binate/vm/vm_exec_pure.bn`, `execUnaryOp`:

    case BC_FNEG:
        var x float64 = bit_cast(float64, cast(int64, regs[instr.Src1]))
        regs[instr.Dst] = cast(int, bit_cast(int64, 0.0 - x))

IEEE negation is a **sign-bit flip**; `0.0 - x` is a different function, because
under round-to-nearest `0.0 - 0.0` is `+0.0`. The register-pair path has the same
bug (`vm_exec64.bn`: `if op == BC_FNEG64 { return 0.0 - a, true }`). The fix is
already written correctly one case below, for float32:

    case BC_F32NEG:
        // IEEE negate is a sign-bit flip; for float32 that's bit 31 …
        regs[instr.Dst] = cast(int, cast(uint32, regs[instr.Src1]) ^ (1 << 31))

float64 wants the same thing on bit 63.

**Noticed and dismissed once already**, which is why this needs a note and not
just a patch: `vm_exec64_test.bn`'s `TestEvalFloatNeg64` records "(The
`0 - 0 == +0` IEEE-default-rounding rule means FNEG64 of 0.0 stays bit-identical
to 0.0; that's the same behavior as BC_FNEG, not a distinction worth testing.)"
— true of the implementation, but the implementation is the thing that is wrong,
and it was compared against `BC_FNEG` (equally wrong) rather than against
compiled mode.

**Tests:** a conformance test asserting `Float64bits(-x) == 0x8000000000000000`
for a runtime `x = +0.0` (it must run under an `-int` mode, where the VM leg is
what fails), and a VM unit test that negating ±0.0 flips the sign — replacing the
comment above.

**Discovered** writing the `math` example in binate/examples: its float tour
prints the IEEE class and bit pattern of `-0.0`, and the two modes disagreed.

### VM SIGSEGVs in `errors.Is` when the error is a USER-defined `errors.Error` — 🔴 OPEN MAJOR — ROOT-CAUSED (found 2026-08-02)

**Severity: MAJOR (hard crash on correct code).** `bni` segfaults; the identical
program compiled with `bnc` is fine. It hits any program that defines its own
error type — the documented, encouraged way to carry structured failure data —
and then classifies it, which is what `errors.Is` is *for*. It also breaks that
program's unit tests under `bni --test`, so a package can be green compiled and
crash interpreted.

**Minimal repro** (released bnc-0.0.12; SIGSEGV under `bni`, correct under `bnc`):

    package "main"

    import "pkg/std/errors"

    type leaf struct { tag int }

    func (e @leaf) Error() @[]readonly char { return "leaf" }
    func (e @leaf) Unwrap() @errors.Error { var none @errors.Error; return none }

    impl @leaf : errors.Error

    func main() {
        var l @leaf = make(leaf)
        var as @errors.Error = l
        println(errors.Is(as, as))   // <- SIGSEGV under bni
    }

Note `errors.Is(as, as)` matches on the FIRST `same(cur, target)` compare inside
`Is` — before `Unwrap` is ever called — so the crash does not need the chain walk.

**What does NOT crash** (each verified under `bni`, and this is the useful part —
it rules out the obvious suspects):

- `errors.Is` on a STDLIB-made error (`errors.New` / `Wrap` / `Rooted`, and the
  base singletons). Only a user-defined implementation triggers it.
- Calling the user error's own `Error()` and `Unwrap()` through the interface
  value, from user code.
- `same(as, base)`, `same(as, as)`, and `present(as)` in user code.
- A **local reimplementation of `errors.Is`** (same `present` / `same` /
  `Unwrap()` loop) in the user's own package, walking the same user error —
  returns the right answer. So the algorithm and the dispatch both work; what
  differs is only that the loop lives in `pkg/std/errors`.
- `fmt.Fprintf` into a user-defined `@io.Writer` — stdlib code calling a
  user impl through a managed interface value is fine in general.
- A synthetic clone of the whole shape in probe packages: a two-method
  interface, a self-referential interface (`Next() @Rec`), an interface with
  impls in two different packages, and a cross-package `Walk(r, target)` doing
  `present`/`same`/`Next` — all correct. The synthetic version does NOT
  reproduce, so something specific to `pkg/std/errors` is involved.

**Still present on `main`** (verified 2026-08-02 against a freshly built
`bnc-0.0.13-pre1` bundle from `scripts/make-bundle.sh`, not just the released
`bnc-0.0.12`): identical SIGSEGV, same repro.

**ROOT CAUSE (found 2026-08-02).** An interface value is a two-word
`{data, vtable_word}` fat pointer, and `vtable_word` has TWO representations the
VM discriminates BY RANGE (`ifaceVtIsNative`, `pkg/binate/vm/vm_exec_iface.bn`):
a **1-based index into `vm.IfaceVtables`** for a bytecode-lowered impl (a small
int — the crash's `0x35` = 53), or a **native `@__ivt` address** for an
injected-compiled impl. The VM's OWN dispatch range-checks the word and handles
both. But an INJECTED-NATIVE function — `errors` is in `stdPkgs()`, so `errors.Is`
runs as compiled native code — was codegen'd to treat `vtable_word` as a native
pointer UNCONDITIONALLY (no range check). So a bytecode (user) impl's iface value
reaching native `errors.Is` gets its index (53) dereferenced as a pointer →
SIGSEGV. `same`/`present` only COMPARE / null-check the word (never deref), which
is why they pass; the fault is the RefDec of the `@Error` args on `return true`
(`Is+308: ldr x1,[x23]` with `x23 = vtable = 0x35`, loading the dtor at
`vtable[0]`), and `Unwrap` dispatch (`ldr x8,[x24,#0x18]`) would fault the same.
Evidence (lldb over the bundle `bni`): on entry to native `errors.Is`, args are
`{data=0x…f410 (valid — refcount 4 at data-0x10, native type-desc at data-0x08),
vtable=0x35}`; the bytecode→native extern-call marshalling (`vm_extern.bn`) packs
args raw and does NOT translate the vtable word. Every clue fits: injected
`errors` (native) over a bytecode impl crashes; a user-package reimplementation of
`Is` (bytecode) over the same impl is fine (VM dispatch resolves the index); `fmt`
is fine because `pkg/stdx/fmt` is LOWERED (bytecode), not injected.

**Fix direction (MAJOR — a new interop mechanism, not a patch).** No native
trampoline vtable is built for a bytecode impl today — `vm.IfaceVtables` holds
only the VM-index vtable (`lower.bn`). The fix: build a NATIVE shim vtable per
bytecode impl (slot 0 = a native dtor trampoline; method slots = native
trampolines that re-enter the VM — analogous to the func-value cross-mode
`@__handle` machinery in `vm_funcvalue_handle.bn`) and substitute its native
pointer for the vtable word at the bytecode→native marshalling boundary
(`vm_extern.bn` arg packing) for every interface-typed argument (recursively, for
the value the injected fn may RefDec or dispatch). Surfaced for a scope decision
before implementing.

**Discovered** writing the `errors` example in binate/examples, whose `ParseError`
(a concrete error type carrying a line number) is exactly the repro shape: the
example runs compiled and dies interpreted, and its unit tests pass every
assertion over stdlib errors then crash on the first `errors.Is` over a
`ParseError`. That example is BLOCKED on this — it cannot be a both-modes example
until this is fixed.

**Tests:** a conformance test for the repro (it must run under `builder-comp-int`,
where the VM leg is what fails), plus a `pkg/std/errors` unit test that
classifies a locally-defined error type.

### A nested generic instantiation reached only through a `.bni`-surface signature skips its constraint check — 🔴 OPEN MAJOR (found 2026-08-02)

**Severity: MAJOR (soundness — missing diagnostic on ill-typed code).** A generic
instantiation nested inside a type that is reached ONLY through a package's
`.bni`-surface signature — never named directly in a body — is silently accepted
even when its type-arg satisfies no impl anywhere. It compiles through to codegen;
the pre-Bug-A compiler rejected it. (Fix is the NEXT task per the user, 2026-08-02.)

**Minimal repro** (`pkg/glib.bni` + a consumer that imports it):

    // pkg/glib.bni
    interface Sizer { Size() int }
    type Box[T Sizer] struct { v T }
    type Bad struct { n int }          // NO impl Bad : Sizer anywhere
    type Outer[U any] struct { inner @Box[Bad] }
    func Make() @Outer[int]

Consumer: `import "pkg/glib"; … glib.Make()`. Current `main` ACCEPTS (the nested
`@Box[Bad]`'s constraint is dropped); the released bnc-0.0.12 BUILDER rejects
`Bad does not satisfy constraint Sizer`.

**Root cause / attribution:** introduced by the Bug A `.bni`-deferral commit
`ff505c92` (verified by git-archive A/B: grandparent REJECT → `ff505c92` ACCEPT).
`buildScopeFromFile` (under `BuildingIfaceScope`) resolves the `.bni` func sig
`@Outer[int]`, populating its nested field `@Box[Bad]` and DEFERRING that check —
but `CheckPackage` cache-hits `Outer[int]` and never re-populates the nested field,
so the check is never re-fired. The Bug A extern-var backstop
(`recheckBniExternVarConstraints`) does not cover this nested-via-signature shape.

**Validated fix:** the order-independence commit (`8476b8be`) added a pending-list
(`PendingConstraintCheck` / `recheckDeferredConstraints`) that records every
deferred check and replays it once impls are complete. Extend the recording gate in
`instantiateGenericDeclWithArgs` from `c.CollectingDecls` to
`c.CollectingDecls || c.BuildingIfaceScope`, so `.bni`-surface deferred checks
(including nested ones) are recorded and replayed by the same package's later
`CheckPackage`. Tested: fixes this repro; all Bug A cases (own-`.bni` valid,
extern-var valid, extern-var genuine-miss rejected) and the order-independence
cases still behave correctly. Likely makes the extern-var backstop redundant
(verify + remove if so).

**Tests:** a conformance/checker test that a nested generic instantiation reached
only through a `.bni` signature is rejected when its type-arg satisfies no impl.
Discovered by adversarial review of the order-independence fix (`8476b8be`).

### Recoverable VM fault inside a RE-ENTRANT execFunc (native→VM callback) is swallowed — 🔴 OPEN MAJOR (found 2026-07-18)

**Severity: MAJOR** — a recoverable user-code fault (bounds / divide / shift /
call-through-nil / stack-overflow — Plan 2) raised inside a **re-entrant** `execFunc`
(a VM function dispatched from COMPILED code through a trampoline / `_call_shim_*`
during an outer `execLoop`) is silently swallowed instead of propagating. The nested
`execFunc` unwinds to *its* entry frame, clears `FaultRaised`, and returns
`Status = FAULTED` + a garbage `0` to the trampoline; neither `execFunc` (after
`execLoop`) nor `execExternCall` re-checks `vm.Status` / `FaultRaised`, so the OUTER
loop continues on the bogus result rather than continuing the unwind (or aborting). A
faulting VM callback thus returns `0` to its compiled caller instead of the program
aborting.

**Pre-existing + affects ALL recoverable faults** (not introduced by the stack-overflow
work — surfaced by its adversarial review, `022a76ac`). Trigger is narrow: a
cross-mode callback (compiled higher-order fn → VM-side callback) whose callback
faults. The unwind only reaches the host cleanly when the *outermost* `execLoop` is the
one that faults.

**Fix direction:** after a nested `execFunc` returns with `Status == VM_STATUS_FAULTED`,
propagate rather than swallow — the trampoline / `execExternCall` should re-raise
(re-`setFault` + re-dispatch in the outer frame, or bail the outer `execLoop`). Needs a
test: a compiled/native higher-order fn calling a VM callback that indexes OOB, asserting
the program aborts (not returns 0). Tracked against Plan 2
(`explorations/plan-rt-fault-cleanup-pads.md`).

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

## Language features — specified, not yet implemented

### Type assertions, type switches & RTTI — pointer/slice forms COMPLETE; value-recovery NOT implemented

The pointer/slice forms (RTTI substrate + front-end: `x.(*T)` / `x.(@T)` /
`x.(*[]T)` / `x.(@[]T)`, comma-ok, type switches, the §17.5 panic, the
cross-mode/VM story) **and** the design-D TypeInfo-registry migration are landed
and conformance-green in every mode; the spec Draft banners are flipped.  See the
done log for the full record.  Two residuals — one a genuine missing form, one an
optional nicety:

**Value-recovery type assertion `x.(T)` — SCALARS + STRUCTS done (`89b41531`,
`21d4c38e`); ergonomics remain.**  The third §11.12 recovery form now recovers a scalar VALUE
(int/bool/float, incl. char, the sized ints/uints, and named scalars like
`type Money int`) from an interface box — `x.(int)`, `case int:`,
`v, ok := a.(int)` — a plain no-refcount load through the box's data pointer, with
exact-identity preserved (`Money` ≠ `int`).  Bundled: a pre-existing VM
`BC_EXTRACT` sub-word over-read fix (a `{char,bool}` comma-ok result was the first
byte-packed sub-word extract the VM ever saw → a wrong `ok` → type confusion).
Tests: conformance 1086/1087/1088 + checker unit tests.  See the done log.

Remaining:

**🔧 Optional tightening (deferred, low value).** Make the design-D registry the
*single seam* that BOTH `collectImplVtableSlots` (vtable slot-1) and
`BuildTypeInfo` read, so the "record symbol == slot reference" invariant holds by
construction instead of via two independent `mangle.TypeInfoName` call sites.  A
separate, later step — a robustness nicety, not a correctness fix.

### `iface.construct.value-borrow`: indirect escape of a borrowed `*any` is not caught — 🟢 LOW / known-limitation (found 2026-07-29)

The implicit value→`*any` borrow (`iface.construct.value-borrow`) is admitted only
at BORROWING positions (argument / var-init) and the checker rejects the DIRECT
escape (`return "hi"`, `v = "hi"`, `b.field = "hi"` — all rejected).  But the
INDIRECT escape slips through: once the borrow lands in a `*any` variable, copying
that variable out of scope dangles it —
`func g() *any { var v *any = "hi"; return v }` compiles, and the caller then reads
a `*any` box that borrows `g`'s dead frame temp (UAF: an empty/garbage recover).
This is **consistent with Binate's raw-pointer semantics** (no escape analysis;
`func g() *int { var n int = 5; return &n }` compiles the same way — user error via
the escape hatch) and affects **scalars too** (`var v *any = n; return v`), not just
string literals — so it is NOT specific to the `9d04870b` string-literal box, which
merely changed the dangling profile from "points at static rodata" to "points at a
frame alloca".  The direct-form rejection is a cheap syntactic gate, not a real
lifetime check.  Fix (if ever): a genuine escape/lifetime check for raw
value-borrows; string literals should ride the SAME mechanism as scalars.  Until
then this is the documented raw-borrow behavior, tracked for awareness.

---

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

See explorations/plan-funcvalue-byaddr-abi.md.

## Cross-mode interface dispatch & compiler/interpreter interop

### Package descriptors (Phase B) — `__Package()` works in compiled + VM modes (builtins); general Functions-table still future
- **Status**: compiled-mode AND VM-mode `__Package()` landed (binate
  `feadde2c`, VM-mode for the builtin packages).  The general interop
  Functions-table (user packages, auto-enumeration) remains future work.
- **What works (compiled mode)**: every package emits an immortal
  static-managed `reflect.Package` descriptor node + a generated
  `__Package() @reflect.Package` accessor (codegen `emit_pkg_descriptor.bn`,
  via the static-managed emitter).  The type checker synthesizes the
  `__Package` signature at selector resolution (`check_expr_access.bn`
  `packageAccessorType`), IR-gen registers it as an imported extern so calls
  resolve + a `declare` emits (`gen_import.bn`), and `reflect` is force-loaded
  (`ensureReflectLoaded`).  Drives a real immortal node through the compiled
  RefInc/RefDec sentinel end-to-end (see [`plan-static-managed-sentinel.md`]).
- **What works (VM mode, binate `feadde2c`)**: the earlier "Functions-table
  is genuinely required" finding was too pessimistic.  `__Package` is already
  a real exported per-module symbol, and the IR/func-value path already
  mangles a qualified `pkg.__Package` reference to call it — so the only
  blocker was the type checker rejecting `_func_handle(pkg.__Package)` (it's
  compiler-synthesized, not a `SYM_FUNC` in scope).  Two small changes wired
  it: (1) `types/check_builtin.bn` accepts `pkg.__Package` as a `_func_handle`
  argument by name; (2) `vm/extern_register_std.bn`
  `registerPackageDescriptorExterns` binds the builtin packages' `__Package`
  (rt, libc, bootstrap, reflect) as VM externs.  Interpreted `pkg.__Package()`
  now dispatches through the func-value shim to the real accessor, and the
  returned `@reflect.Package` is RefDec-safe via the static-managed sentinel —
  exercising the sentinel end-to-end in interpreted mode too.
- **Coverage**: `conformance/532_reflect_package_accessor`
  (`rt.__Package().Name` → "pkg/builtins/rt") now green in ALL 6 default modes
  (the 3 VM-mode xfails removed).
- **Still future — the general Functions-table**
  ([`notes-package-introspection.md`](notes-package-introspection.md) Phase B):
  `registerPackageDescriptorExterns` is a hand-maintained precursor covering
  only the builtins compiled INTO the host binary (their `__Package` is a real
  symbol the shim can call).  USER packages run as interpreted bytecode and
  have no `__Package` body — those need the real table: codegen emits a
  per-package `Functions` table (name + signature + function-value per
  exported func), and the VM auto-enumerates all packages' tables (the
  cross-package registry, open Q4 in the notes — likely a linker section with
  start/stop symbols) to bind names → function values, replacing the hand-
  maintained `RegisterStandardExterns` entirely.  Then richer type metadata
  (Phase C) for reflection/printing + RTTI for type assertions.
- **Linter caveat (see "bnlint typechecks dependency bodies" + lint-skip
  entries)**: `registerPackageDescriptorExterns` is the first `__Package`
  reference in *linted* source, which the BUILDER-bundled bnlint can't yet
  typecheck — `scripts/hygiene/lint.sh` temporarily skips pkg/binate/vm +
  pkg/binate/repl + cmd/bni until the next BUILDER bump.

### Compiler/interpreter interop — MAJOR PROJECT — 🟢 substrate + descriptor LANDED; general user-package table remains (Phase B)

Dual-mode execution substrate is LANDED: shared-layout/refcount cross-mode interop, function values (`{vtable,data}` rep + shims + `dispatchCompiledFuncValue`), the `reflect.Package`/`__Package()` descriptor (compiled + VM builtins, `conformance/532` green in all 6 modes), cross-mode dispatch coverage, and the VM name→function-value registry (`registerPackageDescriptorExterns`).

Remaining (LIVE tracker is the "Package descriptors (Phase B)" entry above): the GENERAL Functions-table for USER packages — codegen emits a per-package `Functions` table + the VM auto-enumerates all packages via a cross-package registry, replacing hand-maintained `RegisterStandardExterns` (now down to ~11 `RegisterExtern` arms; `vm_extern` dispatch is already table-driven); then Phase C richer type metadata / RTTI.

Dormant cross-mode func-value residual (folded in from the retired "Function values — residual follow-ups" entry): the one trampoline ARG shape not yet covered is **float args in V/FP registers** — nothing reaches it today (float scalars ride the integer banks; aggregate returns use `TrampolineAggregate`, ILP32 i64 returns use `TrampolineScalar64`, and >7 args fail loud by design, `17cfc16b`). Add a float-V-reg trampoline if/when a path actually needs it.

(Background/history archived in claude-todo-done.md.)

### Embeddable-interp — open follow-ups (Inc 2 extern cleanup core landed) — 🟡 OPEN (2026-06-20)

The embeddable-interp core (Inc 1, Inc 2 Layers 1/2 + the review (b)-fix, and the
loader de-rooting) is **✅ DONE & LANDED** — full detail in
[claude-todo-done.md](claude-todo-done.md). Plan:
[`plan-embeddable-interp.md`](plan-embeddable-interp.md). Remaining open
follow-ups (deferred with user sign-off):

(The interpreted-`__c_call` frontend guards — run/REPL `da3bd46a` and `--test`-path
`1de21404` — landed and moved to [claude-todo-done.md](claude-todo-done.md).)

- **Globals/vtables-sensitive inject-set test.** `TestNewCustomPkgsRespected`
  proxies on `len(Externs)` (function registration only); add a test that a
  custom set's globals + impl vtables are honored (the `errors.Is`
  sentinel-identity path).
- **Layer 2b — `@reflect.Package` wrapping helper.** Build a modified descriptor
  from an existing one with selected `FunctionInfo` values replaced, so an
  embedder overrides e.g. `os.Args()` without hand-constructing a descriptor.
  This is the ergonomic per-function override path; it also rehomes the
  `progArgsAfterDash` Args shim (becomes a cmd/bni-built wrapped-`os` concern
  rather than baked into interp's bootstrap registration). Land with an
  end-to-end test proving a wrapped package changes observed runtime behavior.
- Optional: auto-enumerate bootstrap's exported format helpers via
  `RegisterPackageFunctions` (they qualify — exported, non-extern), leaving only
  the 9 extern C-I/O entries hand-bound.

### `repl.Kernel` reshape (embeddable REPL → request/reply kernel) — Inc 1 ✅ LANDED; Inc 2/3/4 parked — 🟡 OPEN (2026-07-16)

`pkg/binate/repl` was reshaped from a line-push read-loop
(`Init`/`Step`/`ReplIO`) into a request/reply **`Kernel`** (`Execute` +
`IsComplete` + `KernelInfo` + `Complete`/`Inspect` + `RunReadLoop`; notices /
errors returned as `Result` DATA, not a sink). **Inc 1 is ✅ DONE & LANDED** on
`main` (`6910166f`..`6fa25ae5`, plus the e2e ordering-pin `f17ea5dc`) — verified
green (repl + cmd/bni unit tests, hygiene 17/17, `e2e/repl.sh` 56/0) and hardened
by a 3-lens adversarial review (which caught two land blockers, fixed pre-land).
Plan + full design: [`plan-repl-kernel.md`](plan-repl-kernel.md).

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
  (`plan-repl-kernel.md` Decision #4); untouched. Full side-effect capture is
  impossible in general.

## VM runtime faults & the rt.Exit/abort/panic paradigm

### VM user-code faults — residual follow-ups (Plan 2 core is DONE) — 🟡 OPEN

Plan 2 (`rt.Abort`/`rt.Panic`) made all six VM user-code faults (bounds / divide /
shift / nil-deref / stack-overflow / call-through-nil) RECOVERABLE — the host
(REPL / test-runner / embedder) survives a bad interpreted program while compiled
code stays fatal.  Core landed (Plan 1 primitives; Inc 1/2a/2b/3 cleanup-pad
unwind; nil-deref N1–N3 last, `de9a7c05`); see claude-todo-done.md and
[`plan-rt-abort-panic.md`](plan-rt-abort-panic.md).  Still open:

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

### `builder-comp_arm32_baremetal` unit lane: ir/vm/buf xfail-removal CI confirmation — 🟡 OPEN

The `builder-comp_arm32_linux` unit-lane reds — the ILP32 int-width buckets, the
large-by-value-FCA multi-return sret fix, the anonymous-multi-return-tuple dtor segv (the
`asm/parse TestParseMov` crash), and the ir sizeof test-portability reds — are all FIXED &
LANDED; see [claude-todo-done.md](claude-todo-done.md). The one still-open arm32 unit-lane
item is the baremetal ir/vm/buf xfail removal below (pre-existing, not introduced by the
above; awaiting a CI signal):
- 🟡 **arm32_baremetal xfails whose "literals exceed int32" cause is fixed — REMOVED
  (`02dbb8e0`), CI confirmation STILL PENDING.** The baremetal unit lane had 17 package
  xfails: 13 PERMANENT ("require host filesystem / subprocess / native-host arch —
  can't run under baremetal QEMU semihosting") + **4 citing "tests use literals that
  exceed int32 range; AssignableTo fit-check rejects on arm32 ILP32"** — the cause
  Bucket C (`5b5987d7`) + IR-gen ModuleConst.Val (`2bf360fc`) fixed. Removed all 4:
  `pkg-binate-{ir,vm,buf}` (real packages — each verified to type-check + emit cleanly
  for `--target arm32-baremetal`, 0 fit-check errors) and `pkg-std` (a dead/orphaned
  marker: exact-match lookup, no `package "pkg/std"` exists). Not locally runnable
  (no qemu-system-arm/gcc-arm-none-eabi), so CI is the confirmation — but run
  `29604623344`'s **`arm32_baremetal` job was CANCELLED** (the run was superseded by
  later pushes; its `arm32_linux` sibling had already FAILED on the segv above). So the
  ir/vm/buf baremetal xfail removal is UNCONFIRMED, and every newer Unit-tests run is
  queued. Re-confirm on the next completed Unit-tests run that reaches the
  `arm32_baremetal` job; if ir/vm/buf pass there → done; if any red at runtime, re-add
  that marker with the real reason (fix-forward).

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
   🟢 plumbing + release-wiring LANDED; awaiting a release cut, then a runner.**
   Done: `build-{bnc,bni,bnas,bnlint,bnfmt}.sh` + `make-bundle.sh` gained a
   `--target`/non-host-`--platform` cross-compile path (`ec421c0b`) — Stage 1
   (BUILDER→gen1) stays host, Stage 2 cross-emits — and `release.yml` gained a
   `linux-arm64` matrix row that cross-builds on the x86_64 runner via the
   existing `bnc-0.0.10-linux-x64` BUILDER + `gcc-aarch64-linux-gnu` (`b32c53c9`),
   breaking the chicken-and-egg. Validated end-to-end on macos-arm64→macos-x64
   (Rosetta), guarded by `e2e/cross-compile.sh`. **Remaining (🟡 OPEN):** (a) no
   `linux-arm64` bundle is PUBLISHED yet — it needs a `bnc-*` release cut (the
   next release will build it; deliberately not cut yet); (b) once published, a
   native `ubuntu-*-arm` conformance runner (fetch-builder pulling the arm64
   bundle) could replace the current qemu-aarch64 mode from residual (1)'s
   `builder-comp_native_aa64_linux`.

### Slim `pkg/bootstrap` toward retirement — 🟡 OPEN

**`pkg/libc` is GONE** (retired: Memcpy/Memset became pure-Binate byte loops;
Malloc/Calloc/Free, Exit, and the rest all migrated out — see the done log / git
history). **`pkg/bootstrap` is now seriously slimmed** — only four things remain,
and they all hang off `print`/`println`:

- **`Write()`** — the raw stdout/stderr sink, called internally by `print`/`println`.
- **the "private" format helpers** (`formatInt`/`formatInt64`/`formatUint`/
  `formatBool`/`formatFloat`) — also `print`/`println` internals.
- **`Args()`** — process argv; not yet replaced (no libc fn returns argv, so a
  minimal platform hook is unavoidable).
- **`Exec()`** — subprocess spawn; not yet replaced.

**Actionable plan (what's left to retire bootstrap):**
1. **Replace `Exec()`** with an equivalent in `pkg/std/os`.
2. **Support `Args()`** in `pkg/std/os` + `pkg/builtins/rt` (or similar) — decide
   where the argv hook lives (it can't be pure `__c_call`; a minimal platform hook
   is required).
3. **Deprecate `print`/`println`.** They are the *only* remaining users of
   `Write()` and the private format helpers, so retiring them frees the entire
   rest of bootstrap's surface.

**In flight — `os.Remove()` added, one `Exec("rm")` use to convert (BUILDER-gated):**
`pkg/std/os` now has `Remove()` (libc `__c_call("remove", ...)` — `remove(3)`, i.e.
unlink/rmdir — plus a baremetal stub). `cmd/bnc/util.bn`'s `remove()` helper still
shells out via `bootstrap.Exec("rm", ["-f", path])`; converting it to `_ =
os.Remove(path)` (discard the error, matching `rm -f`'s missing-file tolerance) is
**BUILDER-bump-gated**. Empirically confirmed (2026-07-17): the pinned BUILDER
(`bnc-0.0.11`) resolves `pkg/std/os` against its embedded stdlib snapshot, which
predates `Remove`, so the Stage-1 gen1 build fails `cmd/bnc/util.bn: undefined:
Remove`. Do the swap once `BUILDER_VERSION` is bumped to a release that includes
`os.Remove`. (This is a partial down-payment on step 1 — it retires the `rm` use;
`Exec` itself stays until its other callers, the `clang`/`ar` link invocations, also
have an `os` equivalent.)

**Progress on step 3 — most of the tree migrated to `pkg/stdx/fmt`:**
- **Wave 1 — non-BUILDER CLI tools (2026-07-29):** `cmd/{bni,bnas,bnfmt,bnlint}` emit
  all user-facing stdout/stderr via `pkg/stdx/fmt` (`Print`/`Println`/`Fprint`) instead
  of the `print`/`println` builtins — dropping the manual `strconv.Itoa`/`strings.Builder`
  scaffolding and the raw-vs-managed `printErr` overload split (fmt's `...*any` absorbs
  both). Landed `0b57ba04`/`71fdf553`/`896a7db1`, gated behind `CHECK_TOOLS_VERSION` →
  `bnc-0.0.12-pre4` (`89cf3432`): pre3's pinned bnlint predates the implicit
  value-borrow-into-`*any` boxing the call sites need.
- **Wave 2 — the BUILDER-compiled tree (2026-08-02), now that `BUILDER_VERSION` is
  `bnc-0.0.12`:** the old "can't use fmt in the BUILDER tree" blocker is GONE — BUILDER
  0.0.12 compiles fmt (variadics + `...*any` implicit boxing + reflection are all
  in-subset; verified by a direct BUILDER compile of fmt before the sweep). `cmd/bnc`'s
  driver diagnostics (`0d266ac0`), the compiler-internal diagnostics in
  `pkg/binate/{ir,native}` (`acaa06a7`, also folding away three `strconv.Itoa` regmap
  uses), and one `pkg/binate/vm` test diagnostic (`8c15394c`) now all go through fmt.

**Remaining `print`/`println` holdouts (what step 3 still needs):**
- ✅ **The runtime — `pkg/builtins/rt`** — DONE (`8ddaec6a`, 2026-08-02): the reason rt was
  the "last blocker" (fmt's circular dependency on rt + the no-alloc OOM-handler constraint)
  is resolved by rt owning its diagnostics — an alloc-free `rtFormatInt` + a per-target raw
  sink (`rtWriteRaw`: `write(2)` / semihost) + `abortWithMessage(...*any)`, deduped into a
  target-neutral `rt_diag.bn`. The fault handlers now Abort (not Exit — `rt.Exit` removed)
  and use no print/println / no bootstrap. See the done log.
- ✅ **The perf fixtures** (`perf/*.bn`) — DONE (`7b395154`, 2026-08-02): `001_fib` /
  `002_many_funcs` emit via `fmt.Println`. The added fmt compile cost is a constant offset
  that cancels in the suite's mode/version comparisons; alongside it the perf harness now
  reports Total (compile+run) + the separate Compile/Run breakdown (`8300afdc`). See the done log.
- **`conformance/` and `examples/`:** separate (conformance handled as its own decision;
  examples build from prebuilt bundles).
- ✅ **The generated test runner** — DONE (`10b2d772`, 2026-08-02): `cmd/bnc`'s `--test`
  runner now emits via fmt; this required root-causing + fixing a latent `--test`
  miscompilation (the runner was IR-gen'd un-type-checked, so fmt's `*any` boxing typed a
  string literal as its `char` element). See the done log.

All library, compiler, and runtime code is now off `print`/`println` — the CLIs, the BUILDER
tree, the `--test` runner, rt, AND the perf fixtures. The only remaining users of the builtins
are `conformance/` and `examples/` (separate decisions); once those are handled, the builtins
(and the `Write()`/format-helper bootstrap surface they alone keep alive) can be removed.

**Residual (small, separable):** wire `ensureLangLoaded` + `appendLangImport` into
the repl's import setup (`pkg/binate/repl/{ir_imports,session,util}.bn`) so
`myInt.String()` works at the repl too — the rest of the "primitive `.String()`
without importing `lang`" work is done (compiled + VM).

**Constraints (still apply):** migrate callers OUT — never rename bootstrap's
C-symbol-resolved I/O in place. An in-place rename hits a Stage-1 link wall (gen1
links BUILDER's *pinned* runtime, which only defines the OLD mangled I/O symbols),
and any change that adds/removes `bn_pkg__bootstrap__*` runtime defs is a
runtime-ABI change → **BUILDER-bump-gated**. `__c_call` is scalar/pointer-only, so
slice-taking / aggregate-returning I/O needs marshalling (cstr, data-ptr,
aggregate build).

(VM Phase 1 is DONE — bootstrap is native-only in the VM, format helpers
registered as externs; main `a7fabc7a` + `7abc3809`. The older "convert bootstrap
I/O to `.bn` + `__c_call`" Phase 2 is superseded by the plan above: `pkg/std/os`
subsumes the I/O, so there's no reason to convert it in place. Design notes:
`plan-bootstrap-ccall.md`.)

### Annotations & C function interop — `__c_call` DONE; residual is the `#[link]` companion — 🟡 OPEN (low)

**Option E (`__c_call` intrinsic) was chosen (form E2) and is ✅ DONE & SHIPPED**
(incl. native variadics; `plan-c-call.md` = "COMPLETE, 2026-06-02"). Call sites use
`result = __c_call("write", int32, cast(int32, fd), buf, len)` — C symbol name +
explicit return type + args already in the Binate types matching the C ABI, reusing
the backends' platform-C-ABI lowering (no C parsing, no `bn_` mangling). It is in
production across `pkg/builtins/rt` + `pkg/std/os` (open/read/stat/readdir/errno…),
retiring `pkg/bootstrap`'s hand-written C wrappers as intended. The general `#[…]`
annotation syntax also landed (as `#[build(…)]`). Options A–D and the E1
(C-prototype-string) form were rejected — see `plan-c-call.md` / git for that history.

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
[plan-ffi-export-detailed.md](plan-ffi-export-detailed.md).  Remaining follow-ons (all
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

### Inject `pkg/builtins/lang` into the VM — 🟡 OPEN (deferred; scoped, spike-verified 2026-08-03)

The last un-injected runtime builtin.  (The rest of the entry-point-move follow-ups
— `testing` injection + the BUILDER-0.0.12 gate cleanup + `bootstrap.Args`
retirement — landed; see claude-todo-done.md.)  A spike proved lang is a REAL
increment, not a quick add: adding lang to interp/externs.bn `builtinPkgs()` +
keeping `lang.bn` loaded (a carve-out from the interface-only lists) is
INSUFFICIENT — lang's primitive-impl methods (`int.String`, `uint8.String`,
`int64.String`, `float64.String`, `int.Compare`, …) are reached by DIRECT calls
(`primitiveQualifiedName` → `pkg/builtins/lang.int.String`), but VM injection binds
externs only from the reflect descriptor's Functions table, which
`collectPackageFuncs` (codegen/emit_pkg_descriptor.bn) builds from `f.Exported` — and
impl methods are NOT marked Exported (only top-level `.bni` funcs/vars are, via
markBniExported{Funcs,Vars}; there is no method/impl Exported-marker).  So an injected
lang's direct method calls crash `vm: extern not found` (spike: conformance
411/415/434/653/664 all failed).  Injected packages' methods work today only via
VTABLES (iface dispatch); lang uniquely uses direct calls.  Two routes, both real
work: **(a)** extend the shared package descriptor to include impl/primitive methods
so `RegisterPackageFunctions` auto-binds them — the general fix (any injected pkg's
direct method calls would then work), but touches the shared descriptor emitters
(both backends + irdata) with byte-identical/hygiene risk on EVERY package's
descriptor; **(b)** hand-bind lang's ~25 primitive methods as externs in interp (like
`registerBootstrapExterns`) — localized, no codegen change, but tedious + lang-
specific.  Leave lang lowered (it works fine) until picked up.

## Standard library — pkg/stdx/fmt

### fmt Printf follow-ups (small remaining verb/flag gaps) — 🟡 OPEN (lean core `2dc07f46` + width/precision/flags `d01a2774` + #/string-hex/*/%q-char `caedd9da`, 2026-07-29)

The Printf/Sprintf/Fprintf **lean core** landed (`2dc07f46`): verbs `%v %d %s %t %f
%g %e %x %X %o %b %c %q %%` with Go-style error verbs, over the `...*any` operands.
**Width / precision / flags landed** (`d01a2774`): `%[flags][width][.prec]verb` —
`-` left-justify, `0` zero-pad (every verb, sign-aware for numbers), `+`/space sign,
width, and precision (float digits / min integer digits / string truncation);
verified byte-for-byte against Go 1.26.3 across ~2800 generated cases, incl. Inf/NaN
(signed, space-padded).  **`#` / string-hex / `*` / `%q`-char landed** (`caedd9da`):
`#` alternate form (0x/0X/0b + octal ensure-leading-0; hex/binary zero-pad prefix
counts toward width, an intentional divergence from Go); `%x`/`%X` hex-encode a
string (space-separated, `#`-prefixed, precision-limited); `*`/`.*` operand-driven
width/precision (bad operand → `%!(BADWIDTH)`/`%!(BADPREC)`); `%q` of a char/int
with full control escapes.  Go-verified.  **Custom formatting landed** (`04e0b3d7`):
a type implementing `lang.Stringer` formats via `String()` on `%v`/`%s`/Print (a
runtime `arg.(*lang.Stringer)` assertion after the built-in fast-path).  `%s` of a
non-string renders it like `%v` (fmt's own scalar formatting, then Stringer for a
user type) — fmt does its OWN formatting for built-ins, not lang's simple
`String()`.  Adversarially reviewed (no leak/UAF, cross-mode identical).

The struct/default-reflection fmt LAYER (`%v`/`%+v` of an aggregate without a
`String()`, per `plan-struct-reflection.md`) is **COMPLETE** — P1 (RTTI substrate +
reflect API, `133db88d`) → P2 (`2ef97634`) → P3 (`6a8b7f8f`) → P4a `%+v` (`f7a3ec06`)
→ P4b-1 array/slice fields + cycle guard (`d52211ae`) → P4b-2 pointer fields
(`30663c5f`) all landed.  See `claude-todo-done.md` for the per-phase summary.  ONE
follow-up remains:

- **Anon-struct records (decision 7, still unmet):** add a STRUCTURAL `TYP_STRUCT` arm
  to `mangleTypeArg` so anonymous structs get a TU-invariant weak-record key (they
  mangle to an occurrence-ordered `__anon_N` today — unsafe as a shared key, so
  `fieldStructName` skips them).  Until then an anonymous struct — top-level AND as a
  by-value / element / pointee field — renders opaque `{...}` instead of its fields.
  This is the sole gap between current fmt struct rendering and full coverage; when it
  lands, drop the `isAnonStructName` skip and add anon-struct conformance coverage.

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

### fmt recognizes only six scalar types — `uint`, sized ints and `char` render as error verbs — 🟡 OPEN (2026-08-02)

`writeArg`'s dispatch covers `int`, `int64`, `uint64`, `bool`, `float32`,
`float64`, the four char-slice spellings, and `lang.Stringer`.  Every other
scalar falls through to the unrecognized-type arm, so an ordinary operand
renders as a visible error verb instead of a number (released bnc-0.0.12,
byte-identical compiled and interpreted):

    i8=%!d(?=-8)  i16=%!d(?=-16)  i32=%!d(?=-32)  i64=-64
    u=%!d(?=5)    u8=%!d(?=8)     u32=%!d(?=32)   u64=64
    c=%!c(?=90)   f32=1.5         f64=2.5         b=true

Two of the gaps hurt: **`uint`** is a core type (it is what `lang.Hashable.Hash()`
returns), and **`char`** is the element of every Binate string — `%c` of a `char`
is about the most natural formatting call there is, and it is exactly the one
that fails.  Callers must write `cast(int, x)` at every site.

Fix: extend the dispatch to the fixed-width integers (`int8/16/32`,
`uint8/16/32`) and word-sized `uint`, widening to `int64`/`uint64` ahead of the
existing digit emitters.  `char` follows from `uint8` (its alias), but decide how
`%c`/`%q` should treat a `char` versus a small integer.  This is a completeness /
ergonomics gap, not a correctness one — the error verb is visible, never a silent
wrong rendering — and it is NOT covered by the struct-reflection work above (a
sized int is a scalar, not an aggregate).  Tests: one case per scalar type in
`fmt_printf_test.bn`.  Found while writing the standard-library example series in
binate/examples.

### fmt cannot print an `os.Args()` / `os.Env()` element — a `readonly`-qualified slice misses every case — 🟡 OPEN (2026-08-03)

Distinct from the missing-scalars entry above: this is about the *qualifier*, not
the element type, and it hits the single most common thing a program prints.

    var a @[]readonly @[]readonly char = os.Args()
    fmt.Printf("[%s]", a[1])            // [%!?(unknown)]
    var s @[]readonly char = a[1]
    fmt.Printf("[%s]", s)               // [world]        <- identical bytes

`os.Args()` and `os.Env()` return `@[]readonly @[]readonly char` — fully readonly,
element slots included — so an element has type **`readonly @[]readonly char`**,
and fmt's dispatch has no case for a `readonly`-qualified managed-slice. Only the
four unqualified spellings match. Copying into an unqualified local fixes it,
which is the tell that the VALUE is fine and only the type match fails; keeping
the qualifier on the local (`var b readonly @[]readonly char = a[1]`) still
misses.

So today a program cannot print its own command-line arguments or environment
entries directly — the first thing most programs do. Verified on released
bnc-0.0.12, compiled and interpreted alike.

Fix: match the char-slice cases irrespective of an outer `readonly` qualifier
(strip it where the dynamic type is compared, as the assignment above evidently
does). Worth auditing the other `*any`-consuming paths for the same
qualifier-blindness — `lang.Stringer` recovery included.

Tests: a `fmt_printf_test.bn` case boxing a `readonly @[]readonly char`, and an
e2e that prints `os.Args()[1]` (`e2e/os-args.sh` already has the harness for it).
Found while writing the `scripting` example in binate/examples, whose whole point
is a script echoing its arguments.

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

Options: **(a)** change `Stringer.String()` to `@[]readonly char` — a rendering
is a value the caller only reads, and `@[]char → @[]readonly char` is implicit,
so an impl that still returns a mutable slice keeps satisfying it (what breaks is
a *caller* holding the result as `@[]char`); **(b)** have the producers return
`@[]char`; **(c)** keep it and document the cast.  (a) looks right, but it is a
signature change in `pkg/builtins/lang` that every implementer sees — user's
call.  Found while writing the standard-library example series in
binate/examples.

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

## bnfmt (self-hosted formatter)

### Batch the `bnfmt-format` hygiene check via multi-file bnfmt — 🟡 OPEN (gated on CHECK_TOOLS)
bnfmt now accepts multiple files per run and names offending files on `--check`
(landed `7821afd0`), but `scripts/hygiene/bnfmt-format.sh` still forks the bundled
bnfmt once PER FILE (~1,219 forks, ~7.5s). Rewire it to pass all files in ONE
`bnfmt --check` invocation (parsing the `<path>: not formatted` lines bnfmt now
emits) to drop the check to ~sub-second, like the other batched hygiene checks.
GATED: the check runs the CHECK_TOOLS-bundled bnfmt, so this needs a
CHECK_TOOLS_VERSION bump to a bundle that ships multi-file bnfmt (a release ≥ the one
containing `7821afd0`).

## bnlint rules, unused-entity checks & lint skips

### Raw-slice escape: decide whether a BROADER best-effort escape lint is wanted — 🟡 NEEDS DECISION
The original framing ("demote the raw-slice escape TYPE ERROR to a linter rule")
is obsolete: there is NO type-check rejection for raw-slice escape (the checker
never rejected it), and a `raw-slice-return` LINT rule already exists (`lint.bn`,
landed `10d19369`) — but it only covers the `@[]T → *[]T` "drops the managed
wrapper" return case. **Open decision (user):** is a broader best-effort escape
lint wanted (return / store-to-outliving-field / assign-to-global of a raw slice
borrowing a local), or is the current narrow rule + "raw is an opt-in escape
hatch" sufficient (close this out)?

## Hygiene checks: tier dependencies & file length

### Lower the file-length `.bni` cap toward 1000/1200 — 🟡 OPEN
- **Residual** of the (now-archived) "Extend hygiene checks to scan `ifaces/`+`impls/`" work. The `.bni` file-length cap is currently 1500/1800 (warn/error); consider lowering toward 1000/1200.
- **Blocker**: `pkg/binate/ir.bni` (~1183 lines) exceeds the proposed lower cap and would need refactoring (split into sub-interfaces) first. A live `TODO` in `scripts/hygiene/file-length.sh` tracks this.
- (Full resolved diagnosis of the ifaces/impls hygiene-scan extension archived in claude-todo-done.md.)

## Type-system & checker semantics

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

## Spec authoring & language-decision residuals

### Relational-comparison chain (`a < b < c`) diagnostic reach — nicety
The `expr.compare.relational` rule: `a < b < c` is correctly rejected in every context, but the
dedicated "comparison operators do not chain" message fires only for the identifier-leading
for-clause Pratt path (`parse_for.bn:199`); `if`/`var`/literal-leading contexts reject via generic
parse errors. Conformant (rejection holds) — a diagnostic-consistency nicety only. Surfaced
authoring `conformance/spec/13-expressions`.

### Spec Ch.16 (Packages) — adversarial-review follow-ups (test-quality, non-blocking) — 2026-06-19
The Ch.16 review found 0 blockers, 7 should-fix (landed tests work; these
improve rigor). 015 mis-cite already FIXED (re-cited pkg.resolve→pkg.identity).
Remaining, for a focused follow-up (with the build-constraint rework below):
- **Harness limit (root cause of 2 findings):** the runner gives a test ONE
  search root, so `pkg.resolve.public` (013, public-vs-local under DIFFERENT
  roots) and `pkg.resolve`'s independent-.bni/impl-roots facet (012) can't be
  exercised — both tests only show "resolves under one root". Soften their
  comments to not overclaim; the multi-root facets need a harness extension (a
  second `--prepend` root) — note in Annex C as untested.
- **Vacuity to tighten:** 050 (`pkg.identity`) asserts values, not type-
  distinctness — the distinctness is actually pinned by 051's cross-pkg-assign
  reject; re-scope 050's comment. 091 (`pkg.extern` var) only reads once — make
  var-ness load-bearing (mutate via a setter, observe). 090 extern-func is the
  same shape as a normal exported func (inherent).
- **Missing coverage:** `pkg.bni.consistency` only tests return/var-type
  mismatch (033/034) — add param-type + param-count + result-count mismatch.
  `pkg.bni` (032) omits the opaque-type and interface/impl .bni decl kinds.
  `pkg.ccall` (092) has no C-ABI-passability reject test (§16.9). `pkg.clause`
  (010) and `pkg.import` (001) lack negative tests (package-must-be-a-string-
  literal; no block-scoped import).

### Spec Ch.16 build-constraint group — only the `pkg.build.errors` conformance test remains — 🟡 (done parts in done log, 2026-07-10)
The build-constraint rework is done (re-authored `075_build_gate_file` / `076_build_gate_import` on the
real file/import gating mechanism; the "unknown predicate/annotation" possible-gap was NOT a real
validation gap — the compiler rejects them under a resolved config, unit-tested — see the done log).
**Remaining:** the one uncovered rule `pkg.build.errors` needs a conformance `.error` test (or a
small suite) — a `#[build(...)]` whose predicate FAILS TO EVALUATE on a *required* element under a
resolved target, so validation fires and the build aborts. Ch.16 stays 21/22 until then (behavior is
unit-tested in `buildcfg_test.bn`).

**Scope grew (the version predicate landed `dedbb620`, 2026-07-13; spec `038d98e`):** `pkg.build.errors`
now covers more than the original "unknown predicate/annotation" framing, so the test(s) should exercise
the expanded set — each a distinct `#[build(...)]` on a required element under a resolved target:
- unknown unqualified annotation; unknown predicate or tag (the original cases);
- **unknown predicate function** — a call that isn't `is`/`at_least`/`at_most` (e.g. `gt(version,"1.0.0")`);
- **ordered matcher on a non-`version` key** — `at_least(arch, "x64")` / `at_most(os, "linux")`;
- **malformed or adjacent-concatenated `version` literal** — `at_least(version, "0.0")` / `at_least(version, "0.0" ".11")`;
- a disallowed operator (a bare `<`/`==`) or otherwise malformed expression.
(Behavior for all of these is already unit-tested in `buildcfg_test.bn`; this is the conformance-side gap.)

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

## Performance (double-VM `*-int-int` runtime)

### pkg/codegen `TestEmitDebug*` dominates `boot-comp-int-int` runtime (perf)
- **Symptom**: pkg/codegen unit tests take ~1084s in CI under
  `boot-comp-int-int` (vs ~4s under `boot-comp-int`). The 26
  `TestEmitDebug*` tests account for ~78% of that runtime (~500s
  on local Apple Silicon, scaling up on CI x86). Top offenders:
  `TestEmitDebugStructWithArrayAndSliceFields` (~79s),
  `TestEmitDebugSliceFieldInStruct` (~41s),
  `TestEmitDebugSliceOfPointerChain` (~32s).
- **Isolated repro**: `TestEmitDebugStructWithArrayAndSliceFields`
  alone — 0.7s under `boot-comp-int`, ~120s under
  `boot-comp-int-int` (>100× slowdown for one test).
- **Mitigation in tree**: `scripts/unittest/pkg-codegen.skip.boot-comp-int-int`
  skips the `TestEmitDebug` substring under double interp. Coverage
  is preserved by every other mode that exercises codegen
  (`boot`, `boot-comp`, `boot-comp-int`, `boot-comp-comp*`).
- **Root cause to investigate**: each `TestEmitDebug*` runs
  `compileToLLVM(src)` with `SetDebugInfo(true)`. The DWARF emission
  path (DICompositeType chains, DIDerivedType members, member
  scope/baseType references) is heavy on string-building and
  small allocations. Under double interp every byte append /
  small allocation pays 2× bytecode-dispatch overhead, and there
  are many of them per test.
- **Possible angles** (investigated; first attempt was a net loss):
  1. Buffered string construction in `pkg/codegen/emit_debug*.bn`
     — coalesce per-node fragments to reduce CharBuf grows.  On
     inspection the literal-string `WriteStr` calls are already
     coalesced; the only repeating fusable pattern is `WriteByte('!')
     + WriteInt(id)` (~18 sites).  Mechanically fusable but ~18
     dispatches saved per node-emit × ~10 nodes/test ≈ milliseconds.
     Won't move 100s+ runtimes meaningfully.
  2. Cache stable strings (e.g. DI tag names, common type keys).
     **Tried 2026-05-13**: pointer-keyed cache in `dbgTypeID` that
     short-circuits `dbgTypeKey` for repeat lookups.  Single-test
     baseline 160s → 106s (-34%), but aggregate of all 26
     `TestEmitDebug*` went 441s → 513s (+16%) under boot-comp-int-int
     locally — the added pointer-scan per call pays off only when
     the registry is large (few slow tests) but slows the small-
     registry common case.  Reverted; needs a cache that's O(1)
     per call (e.g. a side-table on `@types.Type` itself, with the
     attendant `pkg/types` layout-contract implications).
  3. Reduce redundant work in the type registry — same composite
     type is rebuilt every call to `compileToLLVM`.  Cross-test
     state would also need per-module id offsets to keep nodes
     self-consistent; non-trivial.
- **Real next step**: actually profile before guessing again.  The
  intuition that "many small allocations × double-interp overhead"
  is the cost was correct in direction but wrong in distribution —
  most of the cost isn't where it looks like it should be.
- **Not blocking anything**; mitigation in tree (`1bffc43`).

### pkg/asm/aarch64 slow under `builder-comp-int-int` (perf)
- **Symptom**: under `builder-comp-int-int`, the
  `pkg/asm/aarch64` test package alone is slow enough to time
  out its CI shard at the 30-min cap. Other packages in the
  same mode finish comfortably.
- **Mitigation in tree**: skipped via the whole-package skip
  mechanism `scripts/unittest/pkg-binate-asm-aarch64.skip-pkg.builder-comp-int-int`
  (2026-06-10 — migrated from the old `.xfail`; slowness is a skip,
  not an expected failure). Coverage is preserved by `builder-comp`,
  `builder-comp-int`, `builder-comp-comp*` and the native_aa64 / arm32
  modes — this is purely a double-interp pacing issue. See the
  "int-int slow-package skips" entry below in this group.
- **Hypothesis**: same shape as the codegen `TestEmitDebug*`
  entry above — many small CharBuf / refcount / bounds-check
  operations per emitted instruction, each paying 2× bytecode-
  dispatch overhead under VM-on-VM. The aarch64 assembler is
  string-heavy (encoding tables, mnemonic dispatch). Hasn't
  been profiled.
- **Next step**: profile one `pkg/asm/aarch64` test under
  `builder-comp-int-int` to confirm the hypothesis and identify
  the actual hot path before guessing at fixes. See the codegen
  entry above for the lesson on guessing-without-profiling.
- **Not blocking anything**; mitigation in tree.

### int-int slow-package skips — re-add after optimizing (or decide double-VM coverage isn't worth it) — FILED 2026-06-10
- **Context**: `builder-comp-int-int` (double-VM, VM-interpreting-VM) was "globally broken — every cell SIGSEGV'd" until `c997cf2e` (2026-06-09) made cells actually run. Now-healthy, the lane runs ~120+ min of work and was timing out its CI shards. Bumping unit sharding 4→8 (binate `e40fe3a0`) helped the light half but **4 of 8 shards still timed out at the 30-min cap, each completing ≤1 package** — i.e. a handful of packages each take **>~24 min (or hang) under double-VM**, which sharding can't fix (a single package can't be split across shards).
- **New mechanism (not xfail)**: added a whole-package skip — `scripts/unittest/<pkg-key>.skip-pkg.<mode>` (run.sh). Distinct from `.xfail` (asserts the package FAILS; XPASS-errors if it ever passes) and from `.skip` (drops individual tests but still runs the package). `.skip-pkg` omits the whole package from a mode because it's too slow there; it is NOT a failure (the tests pass — they're just not run in this lane). Counted as `pkg-skipped` in the summary.
- **Skipped under `builder-comp-int-int`**: round 1 (2026-06-10) — `pkg/binate/codegen` (its `TestEmitDebug` per-test `.skip` was insufficient), `pkg/binate/ir`, `pkg/binate/types`, `pkg/std/math/big`, `pkg/binate/asm/aarch64` (migrated from `.xfail`); these took 6 of 8 shards green. Round 2 (2026-06-10) — added `pkg/binate/vm` itself (CI showed it was the last timed-out shard's >24-min offender). The set was found empirically (heuristic + iterating on which shard still timed out), since the timed-out shards never log the offender's time.
- **Re-add work (the "separately" part)**: for each skipped package, either (a) profile + optimize its double-VM runtime so it fits a shard, or (b) make the explicit call that the double-VM lane adds no coverage over single-VM (`-int`) for that package (strong for the compiler-side ones — codegen/ir/types/asm test the COMPILER; `-int` already runs their tests through the VM; double-VM is the same logic + an extra dispatch layer). `pkg/binate/vm` is the one whose lost double-VM coverage is most arguable — its logic is still covered by `builder-comp-int` / `-comp-int` (single VM), and the lane's unique value is exercised by every OTHER package; re-adding it likely wants per-test `.skip` of its slowest tests rather than the whole package. When re-adding `codegen`, its `TestEmitDebug` per-test `.skip` still applies.
- **Separately unmasked**: `pkg/std/os` (landed `3ca36c82`) fails `vm/lower: unhandled IR opcode c_call` on ALL three VM-leg unit modes — libc-backed (native-only), same category as the `rt`/`bootstrap` xfails. NOT a slow-skip case (it genuinely FAILS in the VM), so it's `.xfail`'d (not `.skip-pkg`'d) for `builder-comp-int` / `-comp-int` / `-int-int`, matching that convention. My skips merely unmasked it (the shard used to time out before reaching it); it was already reding `builder-comp-int` independently.
- **Not a release blocker** (int-int non-blocking per `release-process.md`; was red at `bnc-0.0.7` too). Tracked here so the skips don't become permanent silent coverage loss.
- **STATUS 2026-06-10 — GREEN** (unit run on `3342460e`): all 8 `builder-comp-int-int` shards pass (2.5–26.7 min) and `builder-comp-int` / `-comp-int` pass. **Margin note**: shard 4/8 ran 26.7 min — ~89% of the 30-min cap; the 8-shard + skip set is sufficient but thin, so if the int-int suite grows it may need a 9th–10th shard or one more skip before it times out again. (The remaining unit reds — `arm32_{linux,baremetal}`, `native_x64` — are separate modes, not this. NOTE: `native_x64` was NOT "WIP" — it was broken by an ELF PC32 reloc bug, fixed 2026-06-14 `dd74c91e`; that native_x64 ELF PC32 reloc bug is fixed and archived in claude-todo-done.md.)

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
- ~~**Better docs/help**~~: DONE. Both runners show description, examples, flag docs, test format/convention docs, xfail mechanism. READMEs added for conformance/ and scripts/unittest/.
- ~~**Better output**~~: DONE. `-v` (verbose: all test names), `-q` (quiet: failures+summary only), default (dots for passes, detail for failures).
- ~~**Mode sets in files**~~: DONE. `scripts/modesets/` directory with one file per set (basic, all, full). Adding a new mode set is just adding a file. Both runners read from the shared directory. Help output dynamically lists available sets.
- ~~**Better mode specification**~~: DONE. Comma-separated modes (`boot,boot-comp`) expand into sequential runs. Works alongside mode set files.
- ~~**Better filtering (unit tests)**~~: DONE. Fixed unit test runner to use substring match (was exact match). `token` now matches `pkg/token`, consistent with conformance runner.
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
  [`plan-repl.md`](plan-repl.md) for what `e2e/repl.sh` covers.

### Matrix tests for expanded generics + type assertions/RTTI — 🟡 PART A LANDED, PART B OPEN (brief plan 2026-07-10)

Two new `conformance/matrix/` families, motivated by the recent bug cluster (all in these
spaces, several false-green because tested in-package or in one combination): `8d9e7577`
xpkg-generic-managed-dtor, `c14dd95e`/`aba92526` named-wrapper dtor/copy, `42b3bc83`
func-value/array type-arg conflation, `fedbd0c5` method-value-on-generic. Brief plan:
[plan-matrix-tests-generics-rtti.md](plan-matrix-tests-generics-rtti.md).

**(A) Generics matrix — ✅ core BUILT & LANDED** as `conformance/matrix/generic-managed/`
(sub-axes `inpkg` / `xpkg` / `method-value` / `distinct`, 18 cells + generator; commits
`591f6945` bug-dense core, `ca3dd5b5` method-value + type-distinctness, `bea54fc2`
managed-struct + func-value, `a5869af1` iface balance element). Invariants in place:
links+runs, refcount balance (relative form), type-distinctness (compile-error pairs —
array-len `[3]`/`[5]`, func-sig `(int)uint`/`(bool)uint`), empty/never-populated destroy.
The `iface` balance element's `xpkg` cell uses `gh.At[@Numbered](h,0).num()` (iface-method
CALL on a generic-call result), so it regression-guards conformance/1027 (`dfbdf1dd`).
**Remaining (the plan's deferred "second wave"):** method-**expression** cells,
parameterized-receiver-impl dispatch (`impl *Cursor[T] : Iterator[T]`), and
generic-constraint dispatch; plus array-of-managed / nested-generic element kinds and
`copy` / `destroy-populated` ops.

**(B) Type-assertion/RTTI matrix — ❌ NOT built.** Axes = source `*I`/`@I`/`*any` × recovery
kind × target (concrete/interface incl. transitive) × form × outcome × mode; invariants =
recovery-kind legality, match correctness, `@T`-recovery refcount balance, cross-mode result
agreement. **Status correction (the plan doc's Section B is stale):** type *assertions* ARE
implemented + conformance-tested — `parse_assert.bn`/`check_assert.bn`/`gen_assert*.bn` +
conformance `998`–`1015` (concrete, iface, transitive-ancestor, comma-ok) — so the **assertion
cells are buildable NOW in compiled mode** (incl. the recovery-legality compile-error cells).
Only the **type-switch** cells are gated (parser exists but **no IR-gen lowering** — execution-
plan Phase 6), and the **VM / cross-mode-agreement** axis is gated on the VM RTTI path
(Slice 5). So build B as: assertion cells now → type-switch cells at Phase 6 → VM axis at
Slice 5.

Adopt the matrices only (wiring CI/hygiene is a separate decision).

### (b2 residual) code-red Class 7 — captured-`@func` over-release, native↔VM balance test — 🟡 (Class 6 done, in done log)
The one remaining lifecycle-matrix item: a single-program refcount-balance test of a native call to a
captured `@func` through the VM trampoline. UNBLOCKED — the "needs a cross-mode harness" blocker is
cleared (`e2e/xmiface.sh` / `e2e/xmhfa.sh` exist); add a captured-`@func` refcount-balance case there.
(`conformance/matrix/dispatch-refcount/funcval` is single-mode multi-return balance, not this.)

### (b4) Differential harness v3 — port `gen-diff-scalar.py` to Binate (dogfood) + flavor B — NOT STARTED
- **Context**: the property-based differential value-correctness harness
  (`conformance/matrix/scalar-diff`, oracle = spec) is realized through v2 —
  shifts, conversions, arithmetic, comparisons, bitwise; 123 cells / 5415
  tuples; generator `conformance/gen-diff-scalar.py` (Python). See
  `plan-differential-testing.md` (phasing item 3) for the full design.
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

### `os.Args()` — one open follow-up (argv[0] on the compiled path)
`os.Args()` is landed and correct in every mode, including under the interpreter
(SetArgs + bni wiring — see the done log).  One follow-up remains:

- **argv[0] is an empty placeholder on the COMPILED path.** Element 0 (the
  program name) is left empty because nothing exposes argv[0] yet —
  `bootstrap.Args()` deliberately returns the arguments only, and its one
  remaining consumer, cmd/bnc (BUILDER-compiled, so it stays on
  `bootstrap.Args()`), relies on that.  The other out-of-tree tools
  (bnlint/bnas/bnfmt) now read `os.Args()` instead — see the done log.  Populate
  element 0 once a bootstrap primitive surfaces the program name (e.g. a new
  `bootstrap.ProgName()`/`Arg0()`,
  or the C runtime storing `bn_argv[0]`).  A pure-additive change; the slot is
  already reserved.  This also converges the one remaining compiled/interpreted
  divergence — the interpreter already fills index 0 with the real program path
  via `os.SetArgs`.

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

### `strconv` errors root in no base — the one specified case that never landed — 🟡 OPEN (2026-08-02)

`errors.bni` promises "every error the stdlib returns roots in one of these
[bases]", and `plan-std-error-hierarchy.md` §7 names the exact mapping for the
canonical parse case: a **syntax** error → `BadData`, a value that **overflows**
→ `OutOfRange` (§9 lists it as a migration step: "`strconv`: split syntax
(`BadData`) vs overflow (`OutOfRange`)").  It never happened.  On released
bnc-0.0.12:

    _, err := strconv.Atoi("12x")
    errors.Is(err, errors.BadData)          // false
    errors.Is(err, errors.ConditionsUnmet)  // false — as is every other base

`impls/stdlib/pkg/std/strconv/num_error.bn` is why: `numError.Unwrap()` returns
an EMPTY `@errors.Error`, so the chain ends at the leaf and `Is` can never match
anything.  Its own comment records the reason it was deferred — "read the kind
back out … needs errors.Is / RTTI, deferred … When errors.Is lands these become
the [bases]" — and `errors.Is` has since landed, so the deferral is stale.

Fix: `syntaxErr` roots in `errors.BadData` and `rangeErr` in `errors.OutOfRange`
(have `numError` carry the base and return it from `Unwrap`, which is how a
concrete error type roots itself — the same thing `errors.Rooted` does).  The
rendered message need not change.

Consequence today: a caller that parses user input cannot classify the failure at
all — it must either match on message text or treat every parse failure as
opaque.  It also silently weakens anything downstream that wraps a strconv error
and expects the classification to come through, since a wrapper inherits its
cause's classification and here there is none.

Tests: extend `atoi_test.bn` / `atof_test.bn` with `errors.Is(err, BadData)` and
`errors.Is(err, OutOfRange)` assertions, plus `errors.Is(err, ConditionsUnmet)`
for the shared parent.  Note the hygiene check §9 proposes (whitelist
`errors.New(` to base/root sites) would have caught this class at the source.
Found while writing the `errors` example in binate/examples.

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

### REPL: remove process-global session state (multi-session blocker)
- **Now owned by [`plan-embeddable-vm.md`](plan-embeddable-vm.md)** (scoped
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

## ARM32 bare-metal target

### native arm32 backend — IN PROGRESS (live tracker: [plan-native-arm32.md](plan-native-arm32.md))

The `pkg/binate/native/arm32` backend is tracked in detail in
`plan-native-arm32.md`; that doc is authoritative for phase status, landed
commits, and deferred shapes.  **Status: P0–P5 done** — baremetal soft-float is
FULLY GREEN (`builder-comp_native_arm32_baremetal` 2851/0, only the legitimate
`982_c_global_environ` xfail: no libc `environ` on baremetal to link).  **Remaining:
P6 (VFP + hard-float for `arm32-linux` native — `asm/arm32` has zero VFP encoders
yet) and P7 (promote baremetal to a blocking modeset + full unit sweep).**  Deferrals
below are all **fail-loud** (a shape the backend doesn't implement emits a clean
COMPILE_ERROR, never silent wrong-code).  The FIXED/RESOLVED notes below are retained
for context; the historical fix records also live in claude-todo-done.md.

**MAJOR — FIXED (arm32 + x64) in P4-b2 (`bce99096`), found 2026-07-04 by the
P4-b2 review: big-multi-return FUNC-VALUE call under-reserves outgoing-args →
cross-module silent miscompile.** For an `OP_CALL_FUNC_VALUE`/`OP_CALL_HANDLE`
whose result is a big multi-return tuple (gpWords > NumGpRetRegs, so sret), the
native EMITTER uses `prefixSlots = 2` (retbuf in R0 + data in R1, via the
SretInGpArgReg convention) — see arm32_call_indirect.bn `emitCallFuncValue` and
x64_call_indirect.bn:226-230 (`useRetbuf = aggregateRet || bigMultiRet`). But the
shared SIZER `callDispatchArgTypesAnyOp` (common_call.bn:132-137, feeding
PlanFrame's outgoing-args reservation) gates its prefix bump on `aggregateRet`
which is `!IsMultiReturnCall` — so a big multi-return keeps `prefixSlots = 1` and
has NO bigMultiRet handling (unlike the direct-`OP_CALL` branch,
callDispatchArgTypes:91-93, which DOES prepend a slot for CallReturnsBigMultiReturn).
So emitter(2) vs sizer(1): with 3+ single-word user args the emitter spills the
3rd user word to SP+0, which PlanFrame never reserved → it overlaps the first
spill/alloc slot (a 523-class frame-corruption miscompile). SAME-module is
fail-loud (the arm32 sret shim rejects >2 args), but CROSS-module — an LLVM-dep
func value called from native main with 3+ args — emits the overlap with NO local
fail-loud → **silent miscompile at the native↔LLVM boundary**. **x64 has the
IDENTICAL pre-existing bug** (also SretInGpArgReg=true); aarch64 is safe (X8, no
SretInGpArgReg, prefixSlots stays 1). LATENT: no conformance test exercises a
big-multi-return func-value call with ≥3 user args. **Fix** (recommended, fixes
both, inert on aa64): in callDispatchArgTypesAnyOp's OP_CALL_FUNC_VALUE branch add
`if cc.SretInGpArgReg && ins.ID >= 0 && cc.CallReturnsBigMultiReturn(ins) {
prefixSlots = 2 }` — a shared change (touches x64 codegen for this shape, so
verify x64 units/conformance) + a conformance test.  DONE: the shared
`prefixSlots=2` bump landed in P4-b2 (`bce99096`), gated on `cc.SretInGpArgReg`
(fixes arm32 + x64, inert/byte-identical on aarch64); x64 native units +
func-value/multi-return conformance verified green; a `common_call` unit test
pins prefixSlots=2 (SysV/AAPCS32) vs 1 (AAPCS64).  The end-to-end cross-module
repro is the F5/Get5 test noted in the aa64 follow-up above (not committed because
it also trips the separate aa64 crash).

**MAJOR — FIXED (landed `bc42705e`, 2026-07-04, by-address): the func-value
consumer miscompiled aggregate ARGS through CROSS-PACKAGE func values.**
`emitCallFuncValue` (arm32_call_indirect.bn)
marshals user args via `emitCallArg` — the DIRECT-call ABI, which spreads an
aggregate as its inline words. But the func-value shim ABI passes an aggregate
arg BY-ADDRESS (one pointer word the shim re-expands). For a SAME-package func
value the arm32 shim is emitted and `shimUserArgWords` fail-louds aggregate/float/
pair args; but a CROSS-package func value's shim is LLVM-emitted, so the arm32
shim's fail-loud never runs and the consumer silently emits the mismatched
spread-words marshaling → the shim dereferences the first struct word as a pointer
→ a wild deref / runtime HANG (Data Abort loop) under QEMU. **Present since P4-a
(`a888e9cd`)**; the func-value CONSUMER was introduced there. Discovered via
`889_funcval_small_aggregate` (a cross-pkg func value taking an 8-byte struct by
value), which HANGS ([11s] QEMU timeout). **It was MISSED at P4-a land because the
hang-detection grep (`\[10s\]`) did not match the actual per-test timeout marker
on non-verbose output — a process miss: hang audits MUST grep the QEMU
"terminating on signal" message, not a `[Ns]` bracket.** Fix (confirmed: 889 →
COMPILE_ERROR): user chose to fully implement the by-address arg convention
(mirror x64/aa64 `AggCoercedInReg` → substitute to `*uint8` + pass a pointer), so
CROSS-package aggregate-arg func values now WORK (889 passes). SAME-package
aggregate-arg func values still fail-loud at SHIM emission (the arm32 shim can't
re-marshal an aggregate arg yet — `shimUserArgWords` rejects it; that shim
aggregate re-marshaling is the remaining piece, see below). 64-bit-pair ARGS ride
emitCallArg's pair placement (matches the shim ABI), so they are NOT fail-loud'd
in the consumer. Fixed as part of P4-b1 (`bc42705e`).

**725/727 cross-package reflect — ✅ RESOLVED (`4fe304dd`, 2026-07-12; see done log).**
NOT a miscompile: the 2026-07-04 symptom (per-function info not printed) was fixed by
intervening reflect/descriptor work, and the residual was STALE arm32 expected files
(pre-`0479813a` int64 `RetbufSize` 8; a single int64/float64 return is a register-pair,
RetbufSize 0, on ILP32 too). 725/727 now pass on all native + LLVM arm32 modes + LP64.

**Follow-up (deferred): SAME-package aggregate-arg func value — the arm32 SHIM's
aggregate re-marshaling.** The by-address fix above handles the CONSUMER + the
cross-pkg (LLVM shim) direction. For a SAME-package aggregate-arg func value, the
arm32 shim must load the by-address pointer and re-expand the aggregate into the
underlying's real ABI (mirror x64/aa64 `emitShimArgMarshal`'s coerced-agg
expansion). Currently `shimUserArgWords` fail-louds aggregate args, so
`matrix/abi/funcval-param/*` (same-pkg) COMPILE_ERROR. Not a hang — a clean
deferred shape; implement alongside the P4-d spill shim or as its own increment.

**P4-a DONE (landed `a888e9cd`):** func-value / indirect-call consumer path
(`arm32_call_indirect.bn`) + the shim's big-aggregate R0-sret return shape + all
six dispatch cases (OP_CALL_INDIRECT/OP_CALL_FUNC_VALUE/OP_CALL_HANDLE/
OP_FUNC_HANDLE/OP_FUNC_VALUE/OP_FUNC_VALUE_DTOR). Conformance 1898/727/32 (+118
pass); adversarial review found 0 defects. (The P4-a land claimed "0 `[10s]`
hangs" — that was WRONG; the hang-detection grep was faulty and missed the
cross-pkg aggregate-arg hang tracked in the MAJOR entry above.) Non-capturing
func-value construct/call/handle-dispatch run end-to-end under QEMU. See
plan-native-arm32.md § P4.

- **small (SizeOf ≤ InternalSretBytes = 4) in-register aggregate return —
  deferred (P4-b).** A struct ≤ 4 bytes (e.g. `struct{x int32}`) is returned BY
  VALUE in R0 on AAPCS32, not via sret (P3.3's single-aggregate-sret covers only
  the > 4-byte case). The in-register pack (callee) + collection (caller) are not
  implemented; the direct-call path AND the P4-a func-value/indirect path both
  fail LOUDLY. The x64 backend packs this size class via `emitAggregateReturnPack`
  / the `!bigRet` RAX(+RDX) store — the arm32 analogue (LDR/STR the ≤ 1-word value
  into/out of R0) is the P4-b port. Covered by `conformance/966_return_small_struct`
  (xfail'd for `builder-comp_native_arm32_baremetal`) and unit tests
  `TestReturnSmallAggregateSetsError` / `TestCallSmallAggregateReturnSetsError`
  (direct) plus `TestFuncValueShimSmallAggregateReturnSetsError` /
  `TestEmitCallFuncValueSmallAggregateReturnSetsError` (func-value). Root cause of
  the fail-loud: the sret predicates use a strict `SizeOf > InternalSretBytes`,
  leaving the `≤ 4` class as a non-sret in-register shape not yet lowered.
- **multi-return (in-register tuple collection AND > register-budget sret) —
  deferred (P4-b).** Fail-loud today (direct, func-value, and iface paths); not
  yet xfail'd per-test (they sit among the native-arm32 conformance failures,
  e.g. `401_return_many_scalars`).
- **soft-float (P5) / VFP hard-float + arm32-linux (P6) / CI wiring (P7)** — see
  the plan doc.

**✅ RESOLVED (`7b4303a6`, 2026-07-12) — superseded by the holistic 0-byte fix (see
done log "0-byte func-value results mishandled across all 3 native backends"): a
0-byte aggregate result is now VOID-LIKE everywhere (routed off the pack path via
`IsAggregateReturn`), so the pack-store guards described below were REMOVED as dead
code. The historical write-up is kept below for context.**

**MINOR / latent (found 2026-07-11, P4-d Phase C.2 follow-up review): a 0-byte
aggregate result (`struct{}` / `[0]T`) routes to the arm32 PACK path and
4-byte-overwrites its 0-byte retbuf.** `shimReturnIsSmallPackAggregateArm32` (and
the non-closure `emitFuncValueShimBody` dispatch, arm32_funcvalue.bn:290-301) gates
the pack path on `SizeOf() <= InternalSretBytes` (4) — a `struct{}` has `SizeOf 0`
and IsAggregateTyp, so 0 ≤ 4 routes it to the pack shim, whose unconditional
`STR R0, [retbuf]` writes a 4-byte garbage word PAST the end of the 0-byte result
buffer (silent memory corruption). It IS reachable: a closure / func value returning
`struct{}` compiles on the LLVM backend (verified) and conformance 1029 has
zero-size struct values. **FIXED in the CLOSURE pack path** (`emitClosureShimPackCoreArm32`,
via `emptyAggregatePackResultArm32` — skips the post-BL store + retbuf reload for a
0-byte single-aggregate result; multi-return is never 0-byte since it has ≥2 fields),
covered by `TestClosureShimPackEmptyStructResultSkipsStore` (mutation-verified: the
test fails if the guard is removed). **The NON-closure pack shim (`emitPackShim`,
arm32_funcvalue.bn:423) has the IDENTICAL unguarded `STR R0, [R4, #0]`** — a plain
`*func() struct{}` (or non-capturing method value returning `struct{}`) hits the same
4-byte-past-end store. Left UNFIXED pending a user decision (the closure fix is the
in-scope P4-d Phase C follow-up; the non-closure shim is the same latent issue in the
sibling P4-b pack path). The same size class should also be audited on x64/aa64's pack
emitters (they pack `≤ InternalSretBytes` too) if a 0-byte result can reach them.

### ARM32 bare-metal target — MAJOR PROJECT
- **Why**: enable Binate as an OS-development language on ARM32
  bare-metal (Cortex-A and possibly Cortex-M). Bare-metal is the
  endgame — we want to write the OS in Binate, not run on top of
  one. **ARM32 Linux via LLVM** has been added to the plan as an
  explicit v0 derisking step (it shares all the prerequisites and
  validates the 32-bit type-system path without committing to
  bare-metal runtime work); see plan doc.
- **Existing substrate that already handles bare-metal cleanly**:
  - `pkg/asm/arm32` encodes ARMv7-A instructions (data-processing,
    load/store, multiply/divide, branches, system); 73 unit tests pin
    bit patterns. Assembler-side is essentially done.
  - `pkg/asm/elf` emits ELF32 with the right ARM32 reloc set
    (R_ARM_JUMP24, R_ARM_ABS32). End-to-end tests in
    `pkg/asm/elf/elf_test.bn` already link with `arm-none-eabi-ld`
    (bare-metal linker) and run under `qemu-system-arm -semihosting`
    on virt machine. Three tests: exit, loop sum, function call.
  - `cmd/bnas` already accepts `.arch arm32` and routes through the
    ARM32 instruction parser.
- **What's missing**: an IR-to-machine-code lowering for ARM32 (a
  `pkg/native/arm32` sibling of `pkg/native/arm64`), and a bare-metal
  runtime port.
- **The interesting bit: bare-metal makes the runtime story
  non-trivial.** Things the language/runtime currently assumes from
  the host that don't exist on bare metal:
  - **Allocator**: `pkg/rt`'s managed-pointer/managed-slice
    allocations go through `bn_rt__c_malloc` / `bn_rt__c_free` /
    `bn_rt__c_calloc` (libc-shaped C stubs). On bare metal we need
    a Binate-implemented allocator — probably a simple bump
    allocator first (no free, suitable for early boot), then a real
    heap (free-list or buddy). Allocator implementation lives in
    pkg/rt (or a peer package) and replaces the `c_*` bridges for
    the bare-metal target. The existing "Un-export `rt.c_*`" TODO
    is a prerequisite — once those are private, we can swap them.
  - **`memset` / `memcpy`**: tiny Binate or asm implementations.
  - **Exit / abort / panic**: semihosting `SYS_EXIT_EXTENDED` for
    QEMU testing; on real hardware, `wfi` loop or reset.
  - **I/O**: no stdout/stderr — need a UART driver or semihosting.
    Two flavors:
    - Semihosting (used by the existing QEMU tests): debug-only,
      requires a debugger / QEMU. Useful for development, not for
      shipping.
    - UART: target-specific MMIO. Need a small driver per board —
      PL011 for ARM virt machine, vendor-specific for real hardware.
      The `bootstrap.Write` extern would dispatch to a board-defined
      `uart_putbyte` instead of `write(2)`.
  - **`bootstrap.*` shape**: today's bootstrap.bni is libc-shaped
    (Open / Read / Write / Stat / Args). Bare metal has no
    filesystem and no argv. We'd want a smaller bare-metal-friendly
    bootstrap interface — probably just an output sink and a panic.
    The `formatInt` / `formatBool` / `formatFloat` helpers stay
    (they're pure Binate); only the I/O surface changes.
- **Boot**: a tiny crt0 in asm (or Binate inline-asm if we ever add
  it) to set up the stack, zero BSS, copy .data from flash to RAM,
  then jump to `bn_main`. Provided as a per-board file alongside the
  linker script.
- **Linker script**: per-board memory map (text/rodata in flash, data
  in RAM, BSS, stack at top of RAM, optional MMU page tables for A-
  class). The QEMU virt machine convention (text at 0x40000000) is a
  good first target.
- **Two paths to actual codegen**, similar to the ARM32-Linux
  consideration but with bare-metal twists:
  - **LLVM-via-clang**: pass `--target=armv7a-none-eabi`,
    `-mfloat-abi=soft` (or `hard` if we want NEON/VFP), no sysroot.
    Fastest to first-light, but the LLVM dependency is heavier on a
    bare-metal toolchain story (we'd need to ship clang + lld or
    require the user to have a cross toolchain installed).
  - **Native pkg/native/arm32**: full sibling of `pkg/native/arm64`.
    AAPCS32 calling convention (NGRN over R0..R3, args 5+ on stack,
    return values in R0..R3, large-aggregate return via the hidden
    pointer in R0). Mach-O isn't relevant here — only ELF32 output.
    No external dependency once written. Larger upfront cost; closer
    to the OS-language goal of "no LLVM at runtime."
- **Testing**: the existing `pkg/asm/elf` semihosting harness scales
  up — write conformance programs that use only the bare-metal
  runtime surface, link with `arm-none-eabi-ld`, run under QEMU
  with `-semihosting`. Once the UART driver lands, switch to
  reading stdout from QEMU's serial0.
- **Adjacent in-flight items that affect this**:
  - "Un-export `rt.c_*`" — direct prerequisite for swapping the
    allocator/memops bridges per-target.
  - "Native AArch64 backend cluster A" — in flight; the
    common AAPCS dispatch helper in `pkg/native/common` is shared
    between ARM64 and a future ARM32, so ARM32 work shouldn't start
    until the ARM64 native backend is stable enough that we know the
    common shape is right.
  - The compiler/interpreter interop work is independent of this —
    interop is mostly a layout/representation question, not a
    target question.
- **Suggested first milestone**: get a meaningful subset of
  conformance running on QEMU via the LLVM backend with semihosting
  I/O. Concretely:
    - Pick the codegen path: LLVM-via-clang first
      (`--target=armv7a-none-eabi -mfloat-abi=soft`). Defer the
      native `pkg/native/arm32` backend until LLVM-via-clang
      validates the runtime/boot/linker story.
    - Implement a bump allocator in `pkg/rt` (no free) — enough for
      every conformance test that doesn't actually run out of memory.
      Allocations touch managed-pointer / managed-slice paths only,
      so this is the same surface the existing `c_malloc`/`c_calloc`
      bridges expose. Wire it behind a build-mode switch alongside
      the existing libc-bridges path.
    - Implement semihosting `SYS_EXIT_EXTENDED` (already used by the
      pkg/asm/elf QEMU tests) and `SYS_WRITE0` for putchar/print.
      Replace `bootstrap.Write` (the I/O primitive everything
      eventually funnels into after the print rewire) with the
      semihosting variant for this target.
    - Add `memset` / `memcpy` in pure Binate (or a tiny inline-asm
      wrapper if one is later added).
    - Conformance tests that DON'T touch file I/O / argv / dirs
      should pass: arithmetic, control flow, structs, slices,
      managed pointers, methods, etc. Probably 200+ of the existing
      278. Tests that rely on `bootstrap.Open` / `Read` / `Args` /
      `Stat` / `ReadDir` / `Exec` would be excluded for v1.
- **Plan doc**: `explorations/plan-arm32-bare-metal.md` exists as a
  **DRAFT** (initial sketch — not yet ratified). Covers the items
  above plus: target board choice (QEMU virt + one real Cortex-A
  board TBD), allocator design (bump first, heap second), bare-
  metal `bootstrap.bni` shape, boot/linker-script convention, and a
  placeholder for the per-package inventory of `bootstrap.*` calls
  (the inventory itself is deferred to a follow-up). Needs review
  pass before any implementation begins.

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
