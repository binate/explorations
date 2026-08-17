# Binate TODO

Tracks open work items, grouped by the subsystem / root cause they touch.
Completed items live in [claude-todo-done.md](claude-todo-done.md).

---

## MAJOR

### `[N]readonly char` (readonly-element array) is wrongly accepted as a slice value — 🔴 OPEN MAJOR (found 2026-08-16)

**Severity: MAJOR** (silent miscompile / accepts ill-typed code). `bnc-0.0.13` accepts
returning/assigning a **readonly-element fixed array** `[N]readonly char` where a slice
type (`@[]char` or `*[]readonly char`) is expected — array→slice is NOT a legal implicit
conversion — and codegen mis-lowers it (dumps the array bytes into the slice return
buffer rather than materialising a slice). The **non-readonly** `[N]char` is correctly
rejected, so the hole is specific to the readonly / const-qualified element path.

**Repro** (BUILDER `bnc-0.0.13`, `--emit-llvm`):
- `func f() @[]char { var a [3]readonly char; return a }` → **accepted** (emits LLVM); should be a type error.
- `func f() *[]readonly char { var a [3]readonly char; return a }` → **accepted**; should be a type error.
- Control `func f() @[]char { var a [3]char; return a }` → correctly `cannot assign [3]uint8 to @[]uint8`.

**Root cause (CONFIRMED).** `AssignableTo` has a type-only arm
(`types_assignable.bn:193`: `isStringLitNaturalType(src) && isStringWritableSliceTarget(dst)`)
that decays a `[N]readonly char` array to a slice. That arm exists for string
LITERALS, whose natural type is `[N]readonly char` — but a runtime `[N]readonly char`
value is type-indistinguishable from a literal's natural type, so the arm fires for
runtime arrays too. (Contrast: the array→ARRAY copy arm at `:43` is legal for runtime
arrays — value copy — and stays.) Reproduced at the checker level: a runtime
`[3]readonly char` → `@[]char` / `*[]readonly char` is wrongly accepted; the `[3]char`
control is correctly rejected; string literals are correctly accepted.

**Fix direction (decided 2026-08-17): make string literals UNTYPED** (`TYP_UNTYPED_STRING`),
so there is no concrete `[N]readonly char` type to collide with a runtime array and no
array→slice conversion to police — the bug class is eliminated by construction. See
`plan-untyped-string-literals.md`. (A narrower expr-gating patch — gate the decay on
`srcExpr.Kind == EXPR_STRING_LIT` — was implemented and adversarially reviewed as a
sound interim, but discarded in favor of the root-cause fix, which the review confirmed
is moderate scope and de-risked because IR-gen already materialises strings off the
expression + target type, not the checker's `[N]readonly char`.)

**Discovery:** surfaced by the adversarial review of the `borrowable-char-param` bnlint
rule (a reviewer probed returning a `[N]readonly char` alongside a parameter view);
orthogonal to that rule. Reproduced independently with the BUILDER bnc.

### Explicit `cast(@[]char, a)` of a runtime `[N]readonly char` array mis-lowers (array→slice, same UAF class) — 🔴 OPEN MAJOR (found 2026-08-17)

**Severity: MAJOR** (silent miscompile). Sibling of the `[N]readonly char`→slice bug
above, on the EXPLICIT-conversion path. `cast(@[]char, a)` where `a : [3]readonly char`
type-checks: the `cast` branch (`pkg/binate/types/check_builtin.bn:63`) validates only
constant integer-fit and interface-value operand/target rejection — it does NO
source→target shape-compatibility check. So a runtime array → managed-slice `cast` is
accepted, and IR-gen (`gen_builtin.bn`) falls through to `EmitCast(val, targetTyp)` — an
`OP_CAST` from an N-byte array value into a 4-word `BnManagedSlice`, a layout mismatch
(the same garbage/UAF the implicit path produced). `bit_cast` is correctly rejected
(`isBitCastRejectedAggregateKind`); only value-preserving `cast` slips.

Orthogonal to the untyped-string-literal rework (a runtime array is a `TYP_ARRAY`
regardless of how literals are typed) — `cast` needs its own shape-compatibility gate.
**Fix:** reject `cast` between incompatible aggregate KINDs (array↔slice, and audit
struct/array/slice cross-casts) in the cast checker, with a negative unit test.
Discovered by the adversarial review of the `[N]readonly char`→slice fix (found real by
a second verify pass via direct code trace).

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
(`explorations/done/plan-rt-fault-cleanup-pads.md`).

## Standard library — environment access

### cmd/bnc: switch env reads from the `os.Env()` scan to `os.Getenv` after the next BUILDER bump — 🟡 OPEN

Stage 7 of `plan-package-search-paths.md` added `os.Getenv` (a public wrapper over the
`os.Env()` snapshot) and routed bni/bnlint through it. cmd/bnc is compiled by the frozen
BUILDER, whose bundled `os.bni` predates `os.Getenv`, so it cannot call the new symbol —
it reads `os.Env()` directly via a local `envLookup` helper (`cmd/bnc/util.bn`), a
temporary duplicate of `os.Getenv`. Once `BUILDER_VERSION` is bumped to a builder that
ships `os.Getenv`, delete `envLookup` and have cmd/bnc's `envPaths` call `os.Getenv`
(matching bni/bnlint). Covered end-to-end by `e2e/env-paths.sh`.

### Remove the unsafe `pkg/std/os/sys.Getenv`; route all env access through `os.Env()` — 🟡 OPEN

`sys.Getenv` reads the live libc environment, which is mutable (setenv/putenv, other
threads) — unsafe. The safe source of truth is the `os.Env()` snapshot (seeded from envp
at startup, immutable/shared); `os.Getenv` is already built on it. The one remaining
`sys.Getenv` consumer is `os/process/lookpath` (`ambientPath`, reading `PATH`); migrate
it to `os.Getenv` (or `os.Env()`), then delete `sys.Getenv` from `pkg/std/os/sys` (its
`.bni` decl, impl, and `sys/process_test.bn` coverage go with it).

## Documentation hygiene

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

### Parallel-assign leaks a managed value by 1 on the VM recoverable-fault path — 🟢 LOW / MINOR (2026-08-15)

Parallel/multi-target assign (`a, b = ...`) acquires each managed RHS up front (phase 1:
`resolveParallelEntry` → `emitManagedValueCopyRefInc`, a RefInc giving the destination slot its own
reference) before storing any target (phase 2). If a **recoverable VM fault** is raised by a LATER
phase-1 entry, an already-acquired earlier value is over-retained by 1 → a leak-by-1 on that rare
path. Inherent to the up-front-acquire design (not specific to any RHS kind): the fault-pad
(`emitPadCleanup` → `emitTempRefDecs`) RefDecs *registered* temps but not the phase-1 slot-acquire
RefInc of a value not yet stored. Surfaced by the adversarial re-review of `f68fbc0bc`, which strictly
improved the string-literal case (leak-by-2 → leak-by-1). Compiled-backend faults are fatal (no pad),
so N/A there.

Not yet tested — needs a fault-injection harness that raises a recoverable fault mid-parallel-assign
and checks `rt.LiveBlocks()` under the VM. Fix direction: have the fault-pad also release the
per-target values already acquired into the pending `PAEntry` list, or defer the phase-1 RefInc until
the phase-2 store.

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

### `native/arm32` bare-metal unit lane leaks raw test fixtures — 🟡 OPEN (MAJOR)

The VM static-data leak this entry once bundled — the execution stack plus the 3 shared raw blocks
(package descriptors / TypeInfo / IfaceId, native interface vtables, and global-variable
storage/managed content) — is **FIXED**. See [claude-todo-done.md](claude-todo-done.md) "VM
static-data refcount" and [done/plan-vm-static-data-refcount.md](done/plan-vm-static-data-refcount.md)
for the landed commits and the owning-slice-list (`ownedBlocks`/`vmOwn`) + `VM.Shutdown` design.

Still open: `pkg/binate/native/arm32` is a SEPARATE package xfail'd on `builder-comp_arm32_baremetal`
(`scripts/unittest/pkg-binate-native-arm32.xfail.builder-comp_arm32_baremetal`) — presumed analogous
raw TEST FIXTURES that `RawAlloc` and never free, exhausting the bare-metal arena under a
refcount-heavy run. Confirm with the same per-class RawAlloc/RawFree leak dump used for the VM work,
and fix in kind (own the fixtures via managed slices, or free them). MAJOR per the
raise-don't-workaround rule; the xfail is a tracked hold, not a silent workaround.

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

### fmt: auxiliary `*any` classifiers still match char-slices by exact spelling (named / `readonly` blind) — 🟢 LOW (2026-08-08)

The main value-rendering path is fixed: `writeArg` now recovers a wrapped/qualified
char-slice via reflection (dynamic type peels to KIND_STRING), so Print/Println/Sprint
+ Printf `%s`/`%v`/`%+v` render an `os.Args()`/`os.Env()` element (`readonly
@[]readonly char`) and a named `type X @[]char` as text, not `%!?(unknown)` — landed
`ce758276` (conformance `1196_fmt_wrapped_string`; see done log). The SAME
qualifier/wrapper blindness remains in the auxiliary classifiers, which still switch
on only the four exact spellings — all lower-impact (they render VISIBLY, never wrong
text):

- `argIsString` (`fmt.bn`) — Fprint's Go-style inter-operand spacing rule; a named /
  `readonly` char-slice reads as non-string, so `fmt.Print(a, b)` may add a space Go
  omits (only Fprint spacing; the text itself renders fine).
- `isStringArg` (`fmt_printf_fields.bn`) — `zeroPadFor`'s `%08x`-of-a-string zero-pad
  decision.
- `emitBase` (`%x`/`%X`) and `emitQuote` (`%q`) char-slice switches
  (`fmt_printf_fields.bn` / `fmt_printf_quote.bn`) — a named / `readonly` string hits
  `default → emitBadVerb` (an error verb) instead of being hex-encoded / quoted.

Fix: reuse the KIND_STRING reflection recovery — ideally a shared
`stringDynamic(arg) -> (bytes, ok)` helper peeling named/alias/readonly — at these
sites too.

**Minor sign-aware edge (from the named-scalar review, `75d6e57c`):**
`signAwareFor('v')` treats any integer-kind operand as sign-aware, but `%v` of a
named int WITH a user `String()` renders that OPAQUE text — so `%08v` of such a
value whose `String()` starts with `-` splits the sign (`-000x`) instead of
front-padding (`000-x`), diverging from Go (which treats Stringer output as an
opaque string). Rare. A clean fix must distinguish "renders as a number" from
"renders via Stringer" for the sign-aware decision, e.g. `intOperandBuiltin(arg).ok
|| (scalarReflect numeric && !tryStringer)`.

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

## Test runner (`bnc --test`)

### `--test` discovery matches TestResult by spelling, not by resolved type — 🟢 LOW (2026-08-03)

`isTestResultReturn` — in BOTH runners now, `cmd/bnc/test.bn` (compiled) and
`cmd/bni/main.bn` (bytecode VM), brought to parity in `236cf255` — recognizes a test by
the *spelling* of its return type: qualified `testing.TestResult` OR `sys.TestResult`
(the canonical named type lives in `pkg/builtins/testing/sys`; `testing.TestResult =
sys.TestResult` re-aliases it, `eba239a2`), or a bare `TestResult` when the package
declares `type TestResult` locally (the `pkg/builtins/testing` own-`_test` case, via
`hasLocalTestResultType`). Neither resolves the type through the checker, so both would
miss `testing`/`sys` imported under a non-default alias, and matching each new alias
spelling by hand (the `sys.TestResult` and bare-local arms were both such patches, and
cmd/bni had to be patched separately) doesn't scale. Fix: resolve the single return type
through the loader/checker to the canonical named `sys.TestResult` (a distinct named
type, `19f9d86c`) and match on identity, in one shared helper both runners call.
Referenced by the TODO comment in `cmd/bnc/test.bn`'s `isTestResultReturn`.

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
appears.  Also still open: **aarch64 → ELF** (`elf.WriteAArch64`) for
aarch64-linux (bnas currently emits aarch64 as Mach-O only).

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
  [`done/plan-repl.md`](done/plan-repl.md) for what `e2e/repl.sh` covers.

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

### `pkg/std/os` follow-ons split out of the (completed) os.Stat work — 🟢 LOW

Two small `pkg/std/os` items surfaced by the finished `os.Stat`/`FileInfo`/`FileMode`
work (`done/plan-os-stat.md`), neither actionable within that plan:
- **`FileMode.String()`** — a `Stringer` for `FileMode` (the `drwxr-xr-x`-style
  rendering). Not implemented (no `String` method in `impls/stdlib/pkg/std/os/mode.bn`);
  the plan deferred it "with the formatting layer." Small — pure bit-to-char formatting.
- **`os.Symlink`** — no `func Symlink` exists in the os iface/impls, so an `Lstat`
  on a *real* symlink can't be exercised end-to-end (the `S_IFLNK → ModeSymlink`
  mapping is unit-tested only). Adding `os.Symlink` (small/med, `symlink(2)` `__c_call`
  + baremetal stub) unblocks that e2e test.

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
