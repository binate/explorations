# Binate TODO

Tracks open work items. Completed items live in [claude-todo-done.md](claude-todo-done.md).

---

## native_x64 (ELF) was NOT "WIP" — one reloc bug masked a 99%-working backend — ✅ core FIXED+LANDED, C-call gap OPEN (2026-06-14)

**Fixed + landed:** `dd74c91e` (PC32 addend) + `c097a381` (`.note.GNU-stack`).

Native x64-linux/ELF produced binaries that printed correct output then SIGSEGV'd
on exit — on EVERY program with a cross-package call (nearly all of them). It was
repeatedly mislabeled "WIP / non-blocking" in CI triage and the bnc-0.0.8 release
plan. **That label was wrong** — the codegen was fine; the CI log literally showed
`actual: hello world` before the crash.

**Root cause:** the ELF writer emitted `R_X86_64_PC32` relocations (cross-object
CALL/JMP/Jcc rel32 and RIP-relative LEA disp32) with `r_addend = 0`, but x86-64
RELA requires `-4` (the rel32/disp32 is measured from the END of the
instruction). So every cross-object call resolved to `symbol + 4`, skipping the
callee's 4-byte `push %rbp; mov %rsp,%rbp` prologue → frame corruption → SIGSEGV
on return. Intra-object calls were fine (the in-section `patchRel32` bakes the -4
in); Mach-O was fine (its branch reloc carries -4 implicitly) — so
`native_x64_darwin` under Rosetta ran clean and hid the bug from ALL local
testing (the only real-x86 x64 signal is the ELF CI job). Fix: `elfRelocAddend()`
applies -4 for EM_X86_64 PC32 (+ `TestElfRelocAddendPC32`). Separately, the ELF
writer omitted `.note.GNU-stack` (→ ld marked the stack RWX); now emits an empty
`.note.GNU-stack` so the linked stack is non-executable.

**NOT a regression vs bnc-0.0.8:** the released bnc-0.0.8 binary builds an
identically-crashing `001_hello` (verified by building+running it under docker
linux/amd64). The bug predates 0.0.8; it was just never root-caused.

**Result on real x86 CI (with the PC32 fix):** native_x64 conformance went from
~0 passing to **1423 passed / 12 failed / 4 skipped** (CI run 27520866068).

**Remaining native_x64 gap — 🔴 OPEN (the 12 failures):**
- **C-call / variadic ABI** (the bulk): `498_c_call_basic`, `500_c_call_variadic`,
  `527_c_call_variadic_multi`, `530_c_call_variadic_stack`,
  `regressions/c-call/{abs-negative,abs-positive-zero,labs,printf-variadic-float,
  printf-variadic-int,strlen}`. Likely the x86-64 SysV variadic rule (AL = number
  of vector registers used) and/or stack alignment for variadic C calls — a real
  native_x64 codegen gap; investigate `pkg/binate/native/x64` C-call lowering.
- **xfail candidates** (already xfailed on other native modes): `386_iface_nil_
  dispatch_vm_panic` (compiled-mode SEGV vs VM message), `719_named_slice_
  transparency` (native named-slice codegen gap, also fails native_aa64).

Follow-ups: (1) fix the C-call/variadic gap; (2) xfail 386 + 719 on native_x64;
(3) treat native_x64 conformance as expected-mostly-green (stop dismissing it as
WIP) so a regression of THIS bug is caught; (4) stale comments at
`pkg/binate/native/x64/x64.bn:26` + `x64_call.bn:171` say "PLT32" where the code
emits PC32 — correct them.

**WATCH — possible flaky `matrix/readonly/pass-arg/value-struct-large` on native_x64 (2026-06-14):** this cell is NOT in the 12 failures above and PASSES in normal native_x64 conformance CI (it has no native_x64 xfail), but it produced a **one-off empty-output crash** (expected `7\n9`, got nothing) when pulled into a `conformance-xpass` sweep run via the (now-fixed) substring-filter collision with `value-struct` (`run.sh --exact` stops that now). Same test, same mode, two different outcomes → likely a flaky / marginal native_x64 codegen issue for a >16-byte `readonly` aggregate passed by value, or a one-off artifact of that poisoned sweep run. Not reproduced since; no marker added. If it recurs in normal CI, investigate the native_x64 byval path for `readonly`-wrapped >16-byte aggregates (`pkg/binate/native/x64`).

## MINOR — cross-mode interface dispatch: test-coverage gaps + LP64 assumption (2026-06-14) — 🟡 OPEN

The shim-route that dispatches a native-only package's interface methods from
bytecode (landed `93f75f27` + the math/big extension `7c3b17a2`) is exercised by
726 (`strings.Builder` via `io.Writer`: a raw-slice arg, a scalar arg, a no-arg
method; scalar + multi-return) and 577 (`errors.Error`: no-arg, multi-return).
An adversarial review found these shapes UNTESTED — each needs a SYNTHETIC
native-only test package, since no current stdlib impl hits them:

- A VALUE-receiver iface method (`@__ivtshim` slot holds the thunk's handle, and
  `a0` = the iv-data ptr the thunk derefs). 410 covers native-to-native only.
- A method with MULTIPLE aggregate args (the `a1/a2/...` slot accounting).
- A FLOAT arg / float-containing aggregate (the shim's int-slot bitcast path).
- The `n>6` user-arg overflow guard (a negative test).

Latent, LP64-host-only (NOT active — default VM modes run a 64-bit host):
- `dispatchCompiledIfaceMethod`'s `resultSize > 8` aggregate-vs-scalar threshold
  (and `dispatchExternBinding`'s identical one) must track `isAggregateReturn`'s
  `> target.PointerSize`; on an ILP32 VM host a 5–8-byte aggregate return would
  pick the wrong shim shape. (Now commented in `vm_exec_iface.bn`.)
- 64-bit-scalar args pack as 2 slots on a 32-bit host (`argSlots`); the dispatch
  reads them as positional shim args.

Separately (PRE-EXISTING, independent of the VM): the COMPILED native iface-call
path (`emitCallIfaceMethod`) has no HFA classification — a struct-of-floats arg
is mis-seen as a GP aggregate (no `IsFloatScalarTyp`-style struct handling in the
native backend; the LLVM side relies on LLVM to classify HFAs).

---

## MINOR — pkg/std native-only inject-all: add a coverage hygiene check (2026-06-14) — 🟡 OPEN

**Status: the inject-all AND the list-factoring are DONE.** Every `pkg/std`
package is native-only in the bytecode VM — `errors`, `io`, `strconv`, `math`,
`math/big`, `strings` — except `os` (lowered + injected for its `__c_call`
funcs) and `os/internal` (reached only from native `os`). Cross-mode injection
covers functions, globals, managed-struct dtors, AND interface-impl vtables (the
shim-route, `93f75f27`). The native-only set was factored into one source of
truth — `nativeOnlyStdPkgs()`, a table pairing each import path with a thunk to
its `_Package()` descriptor, iterated by both `isNativeOnlyInVM` and
`injectStdlibExterns` (`8e45cc7e`). (`rt`/`bootstrap` stay named explicitly —
native-only but not `pkg/std`; `os` stays explicit — lowered + always injected.)
All in `cmd/bni/externs.bn`.

Remaining (the only open part):
- **Coverage hygiene check.** A check (a `*.sh` in `scripts/hygiene/`, which
  `run.sh` auto-discovers) that every `pkg/std/**` package is either in
  `nativeOnlyStdPkgs()` or explicitly exempted (`os`/`os/internal`), so a
  newly-added stdlib package can't be silently left lowered (which would re-open
  the cross-mode-identity hazards this whole effort closed). The check must read
  the native-only set from `externs.bn` (e.g. grep the `nativeOnlyStdPkg{ path:
  "..." }` lines) so it stays in sync with the one source of truth.

Worktree note: do this on a fresh worktree synced to current `main`.

---

## MAJOR — native Mach-O writer emits no `LC_DYSYMTAB` / unpartitioned symtab → linker won't coalesce cross-object weak defs → `duplicate symbol` link failure (2026-06-14) — 🔴 OPEN

`builder-comp_native_aa64` **unit** mode fails to link 11 package test binaries
(native, native/common, native/aarch64, native/x64, ir, codegen, vm, repl,
mangle, cmd/bnc, cmd/bni) with:

    ld: duplicate symbol '_bn_pkg__binate__buf____dtor_Builder__vt' in:
        ...pkg__binate__buf.o      (owner of buf.Builder)
        ...pkg__binate__mangle.o   (a consumer of buf.Builder)

**Root cause (corrected after investigation — NOT a strong-symbol or codegen
bug).** The dtor vtable `__dtor_<T>__vt` IS emitted **weak** (`N_WEAK_DEF`) in
both owner and consumer objects, by design, mirroring the LLVM backend — confirmed
`a.SetWeak(vtLabel)` at `pkg/binate/native/aarch64/aarch64_funcvalue.bn:202` and
both objects parsed as `n_desc=0x0080 [N_WEAK_DEF]`. A correct linker coalesces
these. The defect is in the **native Mach-O object writer**
(`pkg/binate/asm/macho/macho.bn`): it emits only `LC_SEGMENT_64 + LC_SYMTAB +
LC_BUILD_VERSION` (`macho.bn:190`, `ncmds=3`) and an **unpartitioned** symbol
table (insertion order, locals/extdefs/undefs interleaved). It never emits
`LC_DYSYMTAB` (the constant is defined-but-unused at `macho_const.bn:19`).
Without `LC_DYSYMTAB` + a partitioned symtab (local → external-defined →
undefined ranges), the current toolchain linker (`ld-1266.8`, Mar 2026) cannot
identify external-defined weak symbols as coalescing candidates and treats two
weak `__vt` defs as a hard collision. clang objects carry `LC_DYSYMTAB` and
coalesce fine; `ld -r` on a native object (which rewrites it WITH `LC_DYSYMTAB`)
makes the duplicate vanish — isolating the missing load command as the cause.

**Not** caused by `7acda3a4` ("inject managed-struct dtors", 2026-06-13): the
failure reproduces identically at its parent `2654d858`. The trigger is
`buf.Builder` (a user-defined managed struct owned by `pkg/binate/buf`, added
`26c224c0` 2026-06-12, RefDec'd in consumers like `mangle`) — the first
cross-package managed-struct dtor-vtable weak-symbol pair linked into a single
native unit-test binary. The latent Mach-O-writer defect was dormant until that
pair existed. (Related to the prior dtor-mangling fix `94b75294` /
claude-todo-done.md ~L1832, which made owner+consumer both emit *weak* and relied
on the linker coalescing — that mechanism is intact; the linker requirement
underneath is what's unmet.)

**Scope / impact:**
- Affects the **native Mach-O backends** (native-aa64 AND native-x64-darwin —
  shared writer). The LLVM backend dedupes (clang objects have `LC_DYSYMTAB`), so
  builder-comp / -comp-comp / -comp-comp-comp unit + conformance are GREEN
  (0 dup-symbol on those modes in unit run `27488837411`).
- **Does NOT affect the shipped release bundle** — bnc/bni/bnas/bnlint are
  LLVM-built (`scripts/build-*.sh` → clang), not native-aa64.
- native-aa64 **conformance** passes (single-program cells rarely link an
  owner+consumer of the same managed struct); only the **unit** test binaries
  (package + all deps) hit a colliding pair.
- Will NOT self-heal at a BUILDER bump (current-source native object writer, not
  BUILDER skew) — persists in post-release CI until fixed.

**Fix — empirically narrowed (2026-06-14); the simple object-writer flag flip is a
DEAD END. The real fix is to make native layout atom-independent so weak
coalescing works (NOT owner-only — see below).** Findings from a worktree
experiment (preserved on binate branch `wip-macho-coalesce-experiment`,
`ea164d48`):
- `LC_DYSYMTAB` + symbol-table partitioning (local/extdef/undef ranges) is
  **necessary but INSUFFICIENT.** Implemented it; the emitted `buf.o` has a
  correct `LC_DYSYMTAB` (`nlocalsym 48 / iextdefsym 48 / nextdefsym 118 /
  iundefsym 166 / nundefsym 5`) and the `__dtor_Builder__vt` symbol is correctly
  `weak external` in the extdef range — yet ld **still** reports duplicate
  symbol. So the missing-`LC_DYSYMTAB` theory (and the earlier `ld -r` evidence)
  was wrong about the discriminator.
- The actual discriminator vs clang is the **`MH_SUBSECTIONS_VIA_SYMBOLS`** mach-
  header flag (0x2000): clang sets it, the native writer hard-codes flags=0. A
  controlled test (clang weak global in `__DATA,__data`/`S_REGULAR`, same
  `N_WEAK_DEF`) coalesces *only* because clang sets this flag — it tells ld the
  sections split into per-symbol atoms, which is what lets ld keep one weak def
  and drop duplicates.
- **BUT setting `MH_SUBSECTIONS_VIA_SYMBOLS` breaks the native backend:** with the
  flag, the dup-symbol link error disappears, but the resulting native binaries
  **SIGILL** (e.g. a freshly native-built `bnc` crashes, exit 132, on any
  compile). The native codegen's layout relies on atom **adjacency** (data/code
  referenced by offset across what would become separate atoms), which the flag
  lets ld reorder/coalesce/dead-strip — so the flag is unsafe without first
  making native layout fully atom-independent (every inter-atom reference a
  reloc-to-symbol). That is a large codegen project, not an object-writer tweak.

**Owner-only emission is NOT the fix — generics require coalescing (author
decision, 2026-06-14).** Having a consumer reference the owner's `__dtor_<T>__vt`
external would work for a package-owned struct like `buf.Builder`, but with
**monomorphized generics** the same instantiated type's dtor/vtable (`Foo[int]`)
is emitted by EVERY package that instantiates it — there is no single owner
package to reference — so cross-object **weak coalescing is genuinely required**
(this is exactly why the LLVM path emits weak-in-consumers and relies on ld
coalescing). The native backend must therefore support the SAME coalescing, which
means making `MH_SUBSECTIONS_VIA_SYMBOLS` safe.

**Real fix: make the native object layout atom-independent, then set
`MH_SUBSECTIONS_VIA_SYMBOLS`.** Every inter-atom reference (code→code, code→data,
data→data) must be a relocation to a *symbol*, with no reliance on adjacency /
section-relative offsets that cross a defined-symbol boundary — so ld can split,
reorder, and coalesce atoms without breaking the program. Once that holds, the
flag (plus the `LC_DYSYMTAB` partitioning from the experiment) lets ld coalesce
the weak dtor/vtable defs exactly like clang. This is a native-codegen project in
`pkg/binate/native/{aarch64,x64}` (instruction/data emission + the macho writer),
not an object-writer tweak. Needs a native-aa64 unit/link regression once
fixed. Discovered 2026-06-14 during the bnc-0.0.9 release-readiness gate (unit run
`27488837411`).

## MAJOR — parallel assignment `a, b = 1, 2` (and the swap `a, b = b, a`) type-checks clean but generates NO code (silent dropped writes) — spec Ch.14 (2026-06-12) — 🔴 OPEN

Found + verified firsthand while grounding spec Ch.14 (Statements) against the
live impl (read parser/checker/IR; conclusive from the code path, plus the
conformance gap below). A matched-arity assignment with **more than one expression
on each side** — `a, b = 1, 2`, or the swap idiom `a, b = b, a` — is accepted by
the type-checker but **silently lowered to nothing**: both stores are dropped, no
diagnostic, no IR.

- **Checker accepts it.** `checkAssignStmt` (`check_stmt.bn:225-267`) takes the
  matched-arity path when `len(Exprs) == len(Exprs2)` and loops over each index,
  checking each LHS/RHS pair assignable — so `a, b = 1, 2` (2 == 2) passes clean.
- **IR-gen drops it.** `genAssign` (`gen_control.bn:89-411`) has exactly two
  branches: `len(Exprs) > 1 && len(Exprs2) == 1` → `genMultiAssign` (call
  destructure), and `len(Exprs) == 1 && len(Exprs2) == 1` → single assign. The
  `len(Exprs) > 1 && len(Exprs2) > 1` case matches **neither** and falls straight
  to `return b` (line 411) — no store IR emitted. So `a, b = 1, 2` and
  `a, b = b, a` compile to a **no-op**.
- **No conformance coverage.** Every multi-`=` conformance site has a single
  multi-return *call* on the RHS (`q, r = divmod(...)`); there is NO `a, b = b, a`
  / multi-expr-RHS test, so this has never been exercised (conformance grounding
  confirmed: "parallel-assignment swap semantics are UNVERIFIED").
- **Severity**: MAJOR silent wrong-code — arguably CRITICAL, because the swap
  idiom `a, b = b, a` is exactly what a Go-familiar user reaches for and it
  silently does nothing. Not memory-unsafety (no OOB); blast radius is limited
  because idiomatic Binate uses multi-RETURN (one call), which works.
- **Decision needed (user owns)** — the design notes frame multiple assignment
  around multi-RETURN (`x, err = foo()`), not first-class tuples, but the grammar
  production is `ExpressionList "=" ExpressionList` (permissive):
  - **(A) Support parallel assignment** (Go-like): IR-gen must handle the
    matched-arity multi-expr case — evaluate ALL RHS exprs, then store ALL LHS (so
    `a, b = b, a` swaps correctly). Add conformance for swap + multi-expr RHS.
  - **(B) Reject it** at the checker: a multi-`=` requires a single multi-return
    call on the RHS; `a, b = 1, 2` becomes a diagnostic ("multiple assignment
    requires a single multi-valued call" or similar). Cheaper; matches the
    multi-return-only design intent. The swap idiom would then be unavailable.
  Either way the current accept-then-drop is a bug. This decision also determines
  what spec §14 (Statements) says about parallel assignment, so §14 authoring is
  paused on it.

### `main` existence/signature not checked at compile time — ❌ NOT A BUG — BY DESIGN (closed 2026-06-14)
Previously filed (2026-06-12) as a missing-diagnostic defect
(`prog.main.unchecked`). That was WRONG — it is **by design**, per the user.
Under **separate (per-package) compilation** the compiler never sees the whole
program, so a valid `func main()` entry point cannot be resolved when a package
is compiled (not without a weird hack). Moreover, **requiring `main` to exist
runs counter to the dual-mode interop story**: any package may be compiled or
loaded independently and have its functions called across the
compiled/interpreted boundary (Ch.19), so a package is never obligated to furnish
an entry point. The entry is resolved at **link / program-assembly** time; a
missing or wrong-shaped `main` surfaces as a link error, which is intrinsic to
the model — NOT a missing diagnostic. **Do NOT re-file this and do NOT add a
checker rule for `main`.** Spec corrected (docs `4af9c72`): §17.3 now carries a
"_Note (by design)_" (the `prog.main.unchecked` ID is retired) and §21.9 no longer
lists it as a non-conformance.

---

## MAJOR — native funcval shim marshalling used `ArgWords`, not the CallConv classifier — x64 false-rejected, aa64 SILENTLY MISCOMPILED (✅ NON-CLOSURE shim RESOLVED — Stage A + Stage B + B0 force-emit landed on main `cd417081`, 2026-06-11)

> The NON-closure funcval shim bug is fixed and landed. What remains: the
> CLOSURE-shim cousins (the FOLLOW-UP bullet below — still latent) and B0
> step 3 (the Functions table — separate from this bug). Kept here rather
> than moved to done because both reference this context.

- **Confirmed silent miscompile (native aa64)**: a function VALUE with > 8 user-arg words (e.g. a `*func(int×9) int`) is **silently miscompiled** — `aarch64_funcvalue.bn:283` does `if nUserWords > 8 { nUserWords = 8 }` (a clamp the comment calls "accept the truncation"), so the 9th+ arg is dropped/garbled. **Runtime-verified: a 9-int-arg funcval returns `43` instead of `45`** (correct on LLVM + VM; wrong only on native aa64). x64 has the loud analogue (`x64_funcvalue.bn:324` `a.SetError(... "unimplemented stack-spill path ...")` → build fails).
- **Root cause**: the per-function shim counts/shifts arg WORDS via raw `common.ArgWords` (managed-slice = 4 words, iface = 2), but the dispatch caller (`emitCallFuncValue`) and the underlying ABI use the `CallConv` classifier where a >16B aggregate is **IndirectLargeAggregates = 1 pointer word**. The two only "agree" by a contiguous-block-shift coincidence (real words land right; over-counted extras spill into unread high regs). Consequences: (a) **x64 false over-budget** — `pkg/std/errors.Wrap(cause @Error, msg @[]char) @Error` is 3 real classifier-words but `ArgWords` counts 6 > the 4-word pack budget → spurious `SetError`; (b) **aa64 silent truncation** for genuinely-wide funcvals; (c) **latent**: an indirect-large arg followed by another arg misplaces the trailing arg (the `ArgWords` shift over-advances ngrn) — affects the closure shims' user-arg path too (`emitClosureShimFast_*` / the spill paths use `ArgWords` for users, `effectiveCapWords` only for captures).
- **Discovery trigger**: B0 of `plan-package-introspection-phase-b.md` force-emits a func-value triple for every exported func; `errors.Wrap` is the first wide funcval emitted, hitting the x64 `SetError`. (Before force-emit, only a handful of narrow-signature funcvals existed, so the gap was masked.)
- **Fix (the proper one — `B`, no workaround)**: switch the shim counting AND marshalling to **effective words** (indirect-large = 1, via the classifier / an `effectiveArgWords` helper) on both incoming and outgoing sides, for the non-closure funcval shims AND the closure shims, on x64 + aa64; add a genuine-overflow **stack-spill** path (mirroring the already-correct `emitClosureShimStackSpill_x64`/`AA64` scalar reference) for funcs whose effective words truly exceed the GP reg file; replace aa64's silent clamp with the spill (loud-or-correct, never silent). The dispatch caller + `common_callconv.bn` classifier need **no** change. Then re-apply the B0 native force-emit.
- **Tests to land with the fix**: conformance cases for (1) `errors.Wrap`-class wide funcval (managed-slice + iface args), (2) indirect-large arg NOT in last position, (3) the 9-scalar-arg funcval (the confirmed aa64 repro). All must pass on LLVM / VM / native aa64 / native x64.
- **Map**: full subsystem map in the workflow output (dispatch caller already spills; VM dispatch caps at `a0..a6` = 7 words via `rt._call_shim_*`, so a >7-word funcval would ALSO need the VM helpers widened — `errors.Wrap` at 3 effective words is well under, so not blocking).

#### Status (2026-06-11)
- **Stage A — DONE, landed `9ceab3be`**: non-closure funcval shims count/marshal by EFFECTIVE words (`cc.EffectiveArgWords`) on both backends; aa64's silent clamp replaced with a loud over-budget `SetError`. Verified: 696 green on all 4 modes; x64/aa64 funcval regression 237/0; the 9-arg aa64 miscompile is gone (now fails loud, pending Stage B). `conformance/696_funcval_indirect_large_args` pins the effective-words cases.
- **Stage B — DONE, LANDED `cd417081` (rebased SHAs `f4fe9f76` split / `e599d2fc` ir.bni / `43573a33` spill / `4d7f7fe0` SPLIT-arg coverage / `cd417081` test renumber)**: replaced the loud `SetError` over-budget guards with a real genuine-overflow **stack-spill** for the NON-closure funcval shims on both backends. (Pre-rebase worktree SHAs were `d56c5fa2`/`bde64614`/`5e4d0899`.) Conformance tests renumbered to **716/717/718** at land time (696/697/698 were taken by concurrently-landed tests). Design (mirrors `emitClosureShimStackSpill_*`): when `nUserWords > userBudget`, `SUB rsp/sp` an outgoing-args frame (x64 ≡8 mod 16 for the return-addr push; aa64 STP FP,LR to preserve LR), marshal user args with spill (incoming overflow read from the dispatch caller's stack; outgoing overflow placed via the CallConv classifier with AAPCS SPLIT honored; floats peel to FP regs), `CALL/BL` (not tail-jump), post-process (pack: store result through stashed retbuf; sret: retbuf in RDI / X8; float-ret: fmov FP→GP), `ADD`, `RET`. Verified: 697 green on all 8 modes (x64 scalar/sret/pack + aa64 sret/pack spill, within the VM 7-word cap); 698 green on the 5 native lanes (aa64 scalar spill = the 9-int repro now returns 45; float-scalar return + arg spill), xfailed on the 3 VM modes (VM dispatch cap — line 14); unit `*_funcvalue_spill_test.bn` pin no-SetError.
- **B0 step 2b (native force-emit) — DONE, LANDED `cd417081` (rebased SHA `df496851`)**: `collectFuncValueRefs` (both backends) now also adds every `.bni`-exported non-extern func, mirroring codegen's `addExportedFuncsToSeen` (LLVM half, rebased SHA `e80c49b8`). Unblocked by Stage B. (B0 step 1 = rebased `d6d60b00`, Stage A = `a7c462a5`.) Verified: `ir`/`types`/`mangle` native unit tests pass on both backends (the exact packages that previously failed); gen1→gen2 self-compile builds; full `builder-comp` conformance green (1360 passed, 0 failed). Pre-existing-and-unrelated: `pkg/binate/native/aarch64`'s link-and-run tests (`TestEmitEmptyMainLinksAndRuns` / `TestEmitCallExitsWithCode`) fail under the `builder-comp_native_x64_darwin` CROSS mode (cross-linking aa64 code from an x64 harness) — confirmed failing WITHOUT 2b too; not a funcval issue.
- **B0 step 3 (the Functions table) — IN PROGRESS**:
  - **3a `Sig` serializer — DONE, LANDED `d277c7d3`**: `types.Type.SigString` renders a func-value type as `(params)(results)` via `QualifiedTypeName` (deterministic, BUILDER-compatible); 3 unit tests. (Limitation: array/func PARAM types use QualifiedTypeName's placeholder — fine while Sig is opaque in Phase B.)
  - **3b-i ABI layout bump (empty table) — DONE, committed `211be04f` (worktree, NOT landed; 3b-i is a valid/landable ABI on its own)**: `reflect.Package` grew `{Name}` → `{Name, Functions *[]@FunctionInfo}` + the full `FunctionInfo` struct (all 5 payload fields per D1, one ABI bump) in lockstep across `reflect.bni` + the LLVM emitter (`emit_pkg_descriptor.bn`) + the shared-native emitter (`common_pkg_descriptor.bn`). `Functions` emitted EMPTY `{null,0}`. Node grows native 32→48 B, LLVM payload `{ptr,int}`→`{ptr,int,ptr,int}`. Verified: descriptor unit tests (codegen + native/common) green; existing `_Package` conformance 532/708/709 green on native + VM; gen1 builds; VM + hygiene green.
  - **3b-ii populate the table — DONE, LANDED on main (`aa698e5d`)**: per `.bni`-exported non-extern func, emit a static-managed `FunctionInfo` node (header + 8-word payload) + a `*[]@FunctionInfo` pointer array, wire `Functions.{data,len}`. `Pkg @Package` (immortal back-ref — managed, per the immortal-refcount design; NOT `*Package`); `Name` = `f.Name` verbatim (already fully-qualified via NewFunc/QualifyName); `Value` = `&@__handle.<mangled>`; `ResultSize` = `SizeOf(result)`; `ParamSlots` = `len(params)`; `Sig` = `SigString`. Landed SHAs: `bfa1ed89` (LLVM half, `emit_pkg_functions.bn` + `emitPackageDescriptor(m)`), `aa698e5d` (native half, `common/common_pkg_functions.bn` + per-arch). Test is **conformance/725** (renumbered from 720 at land — collision). Verified: 725 green on LLVM host + both native cross modes + gen1→gen2; xfailed on 3 VM modes (Gap 2); full conformance green on `builder-comp` (1385) + both all-native modes (no duplicate symbols).
  - **The `@Package` dtor-handle gap — ROOT-CAUSED + FIXED, LANDED `d0b6fc78`**: `FunctionInfo.Pkg @Package` makes `FunctionInfo` need a dtor. A managed type declared in an INTERFACE-ONLY package (reflect) generates its dtor LOCAL in the defining package, but its dtor HANDLE was never emitted: the defining pkg doesn't reference its own dtor as `OP_FUNC_HANDLE`, and the native CONSUMER *skipped* cross-package `OP_FUNC_HANDLE` refs (the `lookupFuncValueType` extern gate). LLVM never hit it (consumer-side `EmitFuncHandle` emits the weak triple unconditionally). Fix: native `lookupFuncValueType_{x64,AA64}` now synthesize a sig for extern handle targets, so the consumer emits the WEAK `(shim,vt,handle)` triple (deduped via `N_WEAK_DEF`), matching LLVM. (Earlier I wrongly proposed `Pkg *Package` to dodge the dtor — REJECTED by owner: `*Package` breaks assignability to anything taking `@Package`; immortal refcounts exist exactly so `@Package` works on an immortal node. Reverted.)
  - **Pre-existing, UNRELATED**: `conformance/719_named_slice_transparency` FAILS on all-native aa64 (expected `10`, prints nothing) — 0 funcvals (so independent of the dtor fix), no native xfail; a named-slice-transparency native-codegen gap the landing worker missed. Owner of 719 to fix or xfail.
  - **Post-land adversarial review (8 agents) — DONE; 0 correctness/ABI bugs, 5 coverage gaps, addressed + LANDED**: review confirmed LLVM↔native byte-identical node layout, correct field targets/offsets, the weak-triple dtor fix, and the multi-return tuple ResultSize (LLVM `functionResultSize` == native `FuncResultSize`). Coverage filled: `8bfceb41` (aa64 `lookupFuncValueType` extern-synthesis unit test — was x64-only; multi-return ResultSize unit tests), `0458f71a` (**conformance/727** — table across multi-return tuple [ResultSize 16], wide 7-param [ParamSlots 7], and float). One gap NOT added (noted, owner's call): a USER interface-only package with *real* (non-static) destruction — 725 already exercises the fix's purpose (handle resolution; links+runs+correct output) and real destruction isn't observable without `rt.Refcount` plumbing, so marginal value.
  - **Remaining for B0**: nothing in B0 step 3 — done. (Whole-package auto-injection / dropping the VM's hardcoded extern table is the Gap-2 VM-backend project, separately deferred.)
- **Closure-shim cousins — FOLLOW-UP (not Stage B; user owns)**: the closure shims (`emitClosureShimFast_*` / `emitClosureShimStackSpill_*` / the closure-aggregate shims) (1) still count USER words via raw `ArgWords` (the indirect-large divergence, per line 10), and (2) don't marshal float-scalar USER args GP→FP at all (the non-closure shim does). Both are latent miscompiles for closures with managed-slice/iface or float params. B0's force-emit only emits NON-closure triples (top-level exported funcs aren't closures), so these don't block B0 — Stage B has now landed, so this is a ready-to-pick follow-up (the non-closure spill in `*_funcvalue_spill.bn` is the reference to mirror).

### Array composite-literal defects (indexed silent-miscompile; over-count OOB write) — spec Ch.13 (2026-06-12) — 🔴 OPEN
Found + verified firsthand authoring spec Ch.13 (read the type-check +
IR-gen; not run, but the code path is conclusive). Two MAJOR array-literal
defects; the type checker `checkArrayLit` (`check_expr_composite.bn:84-91`)
iterates elements positionally, never reading `el.Key`, and never checks
element count against `ArrayLen`; IR-gen `gen_composite.bn:149-152` stores
element `i` at index `i`.
- **Indexed array literals silently MISCOMPILE** (`expr.composite.array.indexed`,
  MAJOR wrong-code). `[5]int{1: 10, 3: 30}` is DECIDED (claude-notes.md:801) to
  mean `{0,10,0,30,0}`, but the keys are ignored and values stored positionally
  → `{10,30,0,0,0}`. Silent wrong values, no diagnostic, no test. Fix: in
  checkArrayLit/genArrayLit, when an element has a Key, fold it to a const index
  and place the value there (validate `index < N`, detect duplicates), zero-fill
  gaps — OR reject indexed-array syntax outright (user's call).
- **Array over-count not rejected → OUT-OF-BOUNDS stack writes** — ✅ RESOLVED 2026-06-12 (binate `910e08cb`; over-count reject only — indexed-literal + `[...]T` sub-items below remain OPEN). `checkArrayLit` now rejects `len(elems) > ArrayLen` with "too many elements in array literal" before IR-gen. conformance/740_array_overcount_rejected; full unit 45/0 + conformance 1407/0 native + 1389/0 VM (no previously-valid code rejected).
  - **Sibling found in self-review + fixed (binate `e81bfbbe`)**: NAMED array/slice composite literals (`type Row [3]int; Row{...}`) bypassed element validation ENTIRELY — `checkCompositeLit` routed a `TYP_NAMED` underlying to its element checker only for STRUCT underlyings, so named-array over-count (→ OOB) AND wrong-element-type (→ miscompile) were both silently accepted (exposed when named composite literals were enabled, `2eeb71c1`, which fixed IR-gen but not the checker). Fix: peel alias/const/named (`peelNamedBounded`) to the composite shape once up front so all element-check branches handle named + unnamed uniformly. conformance/742_named_array_lit_checked; 723/728 still green; full unit 45/0 + conformance 1408/0 native + 1390/0 VM.
  (`expr.composite.array.overcount`, MAJOR, latent memory-unsafety). `[3]int{1,2,3,4,5}`
  is accepted; `gen_composite.bn:149-152` emits stores at indices 0..4 into a
  3-element alloca → 2 out-of-bounds stack writes. Should be "too many elements
  in array literal". No test. (Struct over-count — the benign analogue, extra
  positional values silently discarded — ✅ RESOLVED 2026-06-12 binate
  `e185c9c4`: `checkStructLit` rejects `len(Elems) > len(Fields)` for a
  positional literal, "too many values in struct literal"; negative test
  `743_struct_overcount_rejected`. Applies to named structs too via the
  `peelNamedBounded` routing.)
- **Inferred-length `[...]T{...}` NOT IMPLEMENTED** (`expr.composite.array.inferred-len`).
  DECIDED (claude-notes.md:798) but the checker rejects it ("array length must be
  a constant integer"). Either wire it (substitute `len(Elems)` for the `...`
  marker) or mark deferred.
- **(minor) Positional struct-literal elements are not assignability-checked**
  (`check_expr_composite.bn:73-79` checks keyed but not positional values).
All referenced from `13-expressions.md`.

### D4 composite-literal-in-condition paren-escape does not work — spec Ch.13 (2026-06-12) — 🔴 OPEN
MINOR (parser strictness). Grammar D4 (and Go) say a composite literal may be
used in an `if`/`for`/`switch` condition by parenthesizing it (`if (Point{1,2})
== p`). But `noCompositeLit` is a single sticky boolean never cleared on
entering parentheses/call-args/index brackets/composite-lit bodies
(`parse_primary.bn:178-183` calls parseExpr without saving/clearing it), so the
documented escape is suppressed too — the workaround doesn't actually work.
Fix: clear `noCompositeLit` for nested sub-expressions of `(`/`[`/call-args
(matching Go's exprLev), or amend grammar D4 to state no escape exists.
`expr.disambiguation.d4-paren` in `13-expressions.md`.

### `_Package()`: bytecode VM works only for the 4 builtins (Gap 2; unqualified form ✅ FIXED; builtin auto-injection ✅ LANDED) — 🔴 OPEN (user-package bytecode `_Package` remains)

> **Update 2026-06-12** — two related pieces landed on main:
> - **VM injection Part A** (binate `a8ba52f2`): `RegisterStandardExterns` now
>   auto-enumerates `rt._Package().Functions` (+ empty reflect) via
>   `registerPackageFunctions`, replacing the hand-maintained rt block. bootstrap
>   stays hand-bound (deprecation path + extern-heavy; table skips `IsExtern`);
>   the 3 `_Package` accessors + 2 trampolines stay hand-bound. See
>   `plan-vm-package-injection.md` Part A.
> - **`_Package` self-listing** (binate `53ea3875`): every package self-lists its
>   own `_Package` accessor as the last `Functions` entry (closing the reflection
>   gap), and `--pkg` compilation force-loads reflect (`ensureReflectLoaded`) so
>   it holds even for packages that don't import reflect — i.e. `cmd/bnc` now
>   force-loads reflect on ALL paths (main/test already did; `compileSinglePkg`
>   now too). fv stashed on `ir.Module.PackageAccessorSig` → byte-identical
>   LLVM/native entry (Name `<pkg>._Package`, ResultSize 8, ParamSlots 0, Sig
>   `()(@pkg/builtins/reflect.Package)`). Validated: builder-comp 1395/0,
>   builder-comp-int 1360/0, reflect byte-identical across LLVM/native-aa64/native-x64.
>   Follow-ups (binate `2988cda4`, `6d052181`): arm32 (ILP32) per-mode `expected`
>   overrides for 725/727 — the self-entry's ResultSize is `ptrSize()` (4 on
>   ILP32, 8 on LP64), breaking target-independence (⚠️ NOT verified locally —
>   no qemu; needs arm32 CI confirmation); plus native unit tests
>   (`TestEmitPackageDescriptorSelfListsPackage{AA64,X64}`) for the self-listing.
> - **Still open (the core Gap 2 below)**: user/stdlib packages compiled to
>   BYTECODE still have no `_Package` body → Part B (§2a of the VM-injection plan).
>   The `cmd/bni`-doesn't-force-load-reflect asymmetry below is still accurate
>   (the fix above is `cmd/bnc`-side only).

The compiler synthesizes a `_Package() @reflect.Package` accessor per package
returning the package's immortal static-managed descriptor (Phase B,
notes-package-introspection.md).  `codegen/emit_pkg_descriptor.bn` (+
`native/{x64,aarch64}/_pkg_descriptor.bn`) emit it as a NATIVE function; the
checker synthesizes its signature in BOTH the qualified-access arm
(`check_expr_access.bn`) and the unqualified `checkIdent` arm
(`check_expr.bn`).  Two gaps, surfaced 2026-06-11 by writing
`conformance/708_reflect_package_all_kinds` (user-requested "every package has a
`_Package`" coverage):

- **Gap 1 — no unqualified form (checker) — ✅ FIXED (binate `1164ef04`).** An
  UNQUALIFIED `_Package()` (the current package's own accessor) was `undefined:
  _Package`; now it type-checks and lowers like a normal exported function,
  callable unqualified within AND qualified from importers.  `checkIdent`
  (`check_expr.bn`) synthesizes the `() @reflect.Package` type; IR-gen's
  `registerCurrentModulePackageAccessor` (`gen_import.bn`) registers the current
  module's `_Package` FuncSig so the bare-ident call path lowers it to the local
  symbol `emit_pkg_descriptor.bn` emits.  Compiled modes only — VM still hits
  Gap 2.  Pinned by `conformance/709_reflect_package_unqualified` (compiled PASS,
  3 VM modes xfailed for Gap 2).
- **Gap 2 — VM works only for builtins (MAJOR VM-backend project; DEFERRED).**
  `_Package()` is emitted only as a native function; the bytecode VM reaches
  `_Package` ONLY for the four builtin packages, via the HARDCODED externs in
  `vm/extern_register_std.bn`.  A user/stdlib package compiled to bytecode has no
  native `_Package` symbol → `vm: extern not found: <pkg>._Package`.  The extern
  approach CANNOT work for bytecode-compiled packages.  Fix: emit `_Package()` +
  its static-managed descriptor as BYTECODE per package (the VM equivalent of
  `emit_pkg_descriptor`) so the VM runs it directly, dropping the
  hardcoded-builtin extern table.  Major VM-backend work — the user explicitly
  deferred this.  (Subsumes a sibling asymmetry: `cmd/bni` does not force-load
  reflect the way `cmd/bnc` does — `ensureReflectLoaded` is cmd/bnc-only — so
  reflect-dependent type-checking under the VM needs an explicit reflect import;
  709 imports reflect for exactly this reason.  When the VM emits `_Package`, it
  will force-load reflect too.)
- **Test**: `708_reflect_package_all_kinds` pins `<pkg>._Package().Name` == import
  path for a user package + all four builtins + a stdlib package.  PASSES on the
  3 compiled modes; **xfailed on the 3 VM modes** (`-int`/`-int-int`/`-comp-int`)
  for Gap 2 (int-int also hits the pre-existing multi-package double-VM failure).

## CR-2 Plan-1 Round-2 + Plan-A — closing adversarial review (2026-06-09): SIBLING gaps in the just-landed fixes

A 28-agent adversarial review of the 9 landed CR-2 Round-2 + Plan-A fixes (the same review style that found the Round-1 siblings) — verdicts triaged below against the code + (where noted) runtime probes. **Headline: the recurring pattern recurred — several of THIS round's fixes peeled/guarded SOME sites sharing a root cause and left siblings broken.** All are PRE-EXISTING/latent (variants the landed fixes didn't cover; none is a regression from the fixes — they're the *un*covered cousins). Filed per the bug-discovery protocol; **fix decisions are the user's.**

> ⚠️ **The two reviews MASSIVELY over-confirmed via static reasoning — runtime-verify before acting on ANY finding here.** (1) The 28-agent closing review's 6 "confirmed" gaps reduced under runtime probing to: 1 real (S1, fixed `5c9b00e1`) + 2 niche real-rejections (S3/S4, filed) + 3 false positives (S2/S5/S6). (2) A follow-up 32-agent sweep (verifying S1 + hunting more un-peel siblings) flagged **21 further candidate sites** in `gen_selector` fallback arms / `gen_access` (readonly/named/alias slice+array+ptr indexing) / `gen_iface` ptr-to-readonly-iface — **ALL runtime-refuted**: one probe per distinct category (`mk().v`, `(*p).v`, slice-of-`@readonly Box` field, `readonly @[]int` index, `[2]readonly int` struct field, `*readonly @Getter` dispatch) returns the CORRECT value; named-array variants don't even parse. The static agents flag `.Elem` reads without tracing that the type arrives ALREADY-unwrapped (return-coercion strips readonly; predicate guards peel before the arm). The sweep DID verify the S1 fix + the A2 revert are correct/clean. **Net real bugs from BOTH reviews: S1 (fixed) + S3/S4 (filed niche). Do not chase the 21 phantoms.**

### [closing-review] Triaged verdicts — RUNTIME-verified (the review's static verify phase over-confirmed: of 6 "confirmed", 1 was a clean real fix, 3 are false positives, 2 are real rejections whose type-only fix is a compile→SIGSEGV regression)

**✅ RESOLVED**
- **CRITICAL — `getSelectorType` un-peeled pointee** (`gen_selector_type.bn:56,63`) — ✅ landed `5c9b00e1`. Read the un-peeled `.Elem.Name` of a managed/raw ptr-to-struct base; `@readonly Box`/alias base → `""` → nil; `rp.inr.x` folded to const-0. R2-D1 sibling. Fixed with `peelTransparent(peelTransparent(baseTyp).Elem).Name` (peel the base's own alias wrapper too — an alias base has nil `.Elem`). Cell `regressions/nested-selector-readonly-pointee`, 7 modes.

**⚠️ REAL reject, but the type-only fix is a compile→SIGSEGV safety regression (needs an IR-gen companion) — per the user (2026-06-09): FILE as a known limitation, do NOT pursue the IR-gen work now. Type fixes were prototyped + REVERTED.**
- **MAJOR — alias receiver unsupported for METHOD VALUES** (`pkg/binate/types/check_expr_access.bn:249` + IR-gen): `type AB = @Box; var mv = ab.getV` is rejected ("undefined: getV") because the method-value path calls `ReceiverBaseNamed()` on the un-alias-peeled `origXt`. Peeling it (`resolveAliasAndConst(origXt).ReceiverBaseNamed()`) makes it type-check, but the method-value CLOSURE layout (`gen_method_value.bn`) doesn't peel the alias → runtime **SIGSEGV**. A DIRECT method value (`p.getV`) works; only the alias receiver is broken. Niche (method values × alias receiver). To fix properly: type peel + peel the alias in the closure-capture IR-gen.
- **MAJOR — alias receiver unsupported for IMPL declarations** (`pkg/binate/types/check_impl.bn:90` + dispatch): `type AB = *Box; impl AB : Getter` is rejected ("impl receiver must be (a wrapper around) a named type") because `checkImplSatisfaction` calls `ReceiverBaseNamed()` on the possibly-`TYP_ALIAS` `recv`. Peeling it accepts the impl, but dispatch through the alias-impl iface value → runtime **SIGSEGV**. Niche (impl on alias receiver). To fix properly: type peel + alias handling in impl/vtable dispatch.

**❌ REFUTED / non-exploitable — RUNTIME-verified; do NOT act**
- **R2-D6 ALIAS cycles** (flagged CRITICAL) — **REFUTED**: `type A = B; type B = A` does NOT hang (3 variants tested; compiles + runs). `type A = B` with `B` forward sets `A.Target` to a `TYP_NAMED` forward (not a `TYP_ALIAS`), so `resolveAliasAndConst`'s loop terminates at the named type — the cycle the review imagined isn't formed. The static "unguarded loop" claim missed the forward-decl resolution.
- **R2-D2 named-array `peelReadonly`** (flagged MAJOR) — **REFUTED**: named-distinct array types (`type Arr [N]S`) don't PARSE (syntax error), and alias arrays (`type Arr = [N]S`) resolve via `indexExprType` and work (`a[i][j].x` → 9). The `peelReadonly`-vs-`peelTransparent` gap doesn't manifest for arrays.
- **R2-D6 unbounded `Underlying`-walkers** (`NeedsDestruction`/`SizeOf`/`AlignOf`/`discoverStructFromType`) (flagged MAJOR) — **non-exploitable**: only reachable via a cycle; named cycles are decl-time-rejected + broken (`Underlying=nil`), and alias cycles don't form (above). No reachable hang; `peelNamedBounded` on the 4 comparison predicates is sufficient. (Bounding them anyway is harmless defense-in-depth if ever wanted, but defends an unreachable state.)
- **gen_stmt.bn:259 genDecl iface boxing** (flagged CRITICAL R2-D4 sibling) — **REFUTED**: runtime-verified `var iv readonly @Getter = im; iv.get()` → 7. `genExprOrFuncRef` boxes before the unpeeled `typ.Kind` check, so the skipped re-box at :259 is harmless.
- **LowerOneFunc / LowerOneFuncShadow missing externNameConflict** (flagged CRITICAL A2 sibling) — **MOOT**: A2 was reverted as a misdiagnosis; the guard no longer exists.

### [closing-review] Coverage gaps (lower priority — add tests)
R2-D7: no readonly/alias-wrapped named-int or named-float-minus test. R2-D5: matrix covers only `type AB = @Box` (not alias-over-readonly / value-receiver alias). R2-D4: only managed `readonly @Iface` construct un-xfailed (no `readonly *Iface`, no return/arg-pass position). A1: no float-scalar / named-sub-word / box-in-loop box test.

---

## CR-2 follow-up batch adversarial review (2026-06-09) — post-landing

Adversarial review (find → perspective-diverse cross-examine → synthesize, 56 agents)
of the 8 landed CR-2 follow-up commits (R2-1 `79ebfa98`, R2-2 `d086ccac`, B2
`e15680d7`, B1 `05901f97`, B4 `b4648200`, B3 `5fc5a52f`, R2-3 `ca155319`, split
`2beab6e5`). **Heeding the over-confirmation caution at the top of this file, the
three critical/major entries below were RUNTIME-verified by hand (gen1/gen2 bnc
built from the worktree + an A/B against BUILDER bnc-0.0.7), not just statically.**
Two of the serious findings are regressions in THIS batch's own commits.

- **CRITICAL — X2** (R2-3 `ca155319`): the new negative-offset `panic` false-fires
  on valid code (iface-value upcast to an unrelated zero-method interface).
  **✅ RESOLVED 2026-06-10 (binate `4ac123da`)** — root-caused as a checker
  duck-typing hole; fixed via `isUniverseAny` + supported `@Iface -> *Iface`
  decay (fork B). Full entry under ## CRITICAL.
- **MAJOR — B1/X3** (`05901f97`/`5fc5a52f`): bare const-group member drops its
  inherited narrow type → checker accepts an overflow the explicit form rejects,
  IR truncates (silent wrong value). Full entry under ## MAJOR. Straight bug fix.
- **MAJOR — B2** (pre-existing, NOT from `e15680d7`): named func-value types
  (`type Fn @func(...)`) are unconstructible. Full entry under ## MAJOR.

**Lower-severity / follow-up (not yet runtime-triaged unless noted):**
- **X3-highbit (major, DIRECTION CONTESTED — semantics-owned).** `1<<iota` now
  folds in the checker (B1), so a flag member hitting the SIGN bit of a signed
  target (`1<<63` → `int` on 64-bit; `1<<31` on 32-bit) computes positive
  2^(W-1), which `FitsSigned(W)` rejects — while IR's `evalConstExpr` wraps to the
  valid two's-complement `INT_MIN`. A real checker-vs-IR divergence, but the
  RESOLUTION is a spec call: `claude-notes.md` §const decides const values are
  abstract and must fit the target range (→ the reject may be CORRECT; the
  canonical idiom uses an UNSIGNED target, unaffected). Do NOT change semantics
  unilaterally. (The literal `1<<63` form was already rejected pre-B1; B1 only
  widens that to the iota form without aligning IR.)
- **X2b (major, derivative/pre-existing).** The VM upcast path (`vm_exec_iface.bn`)
  reacts to the SAME checker-accepted upcast with a runtime abort (`iface_upcast:
  target vtable not found`) — a third distinct behavior. Not touched by R2-3.
  Whatever fixes X2 must reconcile all four consumers (LLVM/aa64/x64/VM).
- **B3 type-divergence (minor) — ✅ RESOLVED 2026-06-10 (binate `b9d6d807`).** A bare
  const member that PARKS (REPL) used to resolve via `GenConstMember` (reads only
  `d.TypeRef`=nil → untyped int), whereas the non-parked sibling got the inherited
  type via `genConstGroup`. Fixed by the B1/X3 fix: `checkGroupDeclTentative` now
  threads the inherited type onto the synthesized repeat, so the parked member
  carries `d.TypeRef`=the inherited type and resolves at that width.
- **✅ RESOLVED 2026-06-10 (binate `e16d53bc`) — the four cheap CR-2-review minors:**
  - arm32 xfail rationale (value-struct-large linux+baremetal): corrected to the
    real cause (shared IR-gen readonly field-read defect / Defect 1), matching the
    sibling value-struct markers verbatim so both clean up together (was an XPASS
    landmine).
  - `IsByvalParam` unbounded peel: routed through `peelNamedBounded` (1024 cap),
    behaviour-identical for valid types.
  - stale `gen_func.bn` comment: rewritten to the actual mechanism (`IsByvalParamRef`
    flag drives `OP_STORE`'s memcpy; `ParamIndex` is debug-info only).
  - B3 test: added the `IotaIdx == 1` assertion (mirrors the sibling iota test).
- **R2-3 commit message (nit) — NO ACTION.** Message says conformance 683; landed
  test is 685 (rebase renumber). Commit messages are immutable and the authoritative
  tracking docs already say 685, so nothing to change.

REFUTED by cross-examination (recorded so they aren't re-chased): no other
`emitRef`/`emitValRef` global-ref drop sites beyond OP_CAST + iface-arg (R2-2 clean);
B2's `=` change correct for multi-assign/non-func-LHS; the split (`2beab6e5`) moved
all functions/tests intact; B4 regression tests are non-vacuous.

---

## CRITICAL

---

## MAJOR

### `int64 << int` rejected in 32-bit-int modes → breaks ALL 32-bit-int compilation — REGRESSION from `efeb0f94` — ✅ RESOLVED 2026-06-10 (binate `fd3cb7ac`)
- **✅ RESOLVED 2026-06-10 (binate `fd3cb7ac`).** Root-caused as a TYPE-CHECKER + IR-gen defect, NOT the missing source cast of the initial diagnosis. Per the user's semantics decision: a shift `x << y` / `x >> y` takes its result type from the LEFT (value) operand, and the count `y` may be ANY integer type, independent of the value (Go semantics). Fix: (1) checker `check_expr.bn` — shifts get their own arm instead of being lumped with the symmetric bitwise ops `& | ^` (which unified the operands via commonType → "mismatched types"); untyped-operand cases still defer to foldIntBitwise (byte-identical to before — this matters, see below), a typed-vs-typed pair returns the left operand's type; (2) IR-gen `gen_binary.bn` — a shift's result type is the value (left) type, not the symmetric widenType (which would narrow the result to `int` for `int64 << int` in 32-bit-int, silently truncating). `cast(int64, 1) << (width - 1)` (`types_query.bn:168`) now compiles as-written. Verified: native conformance 1337/0, VM 1307/0, gen2; unit ir/types/codegen/vm/native 8/8; cell `regressions/shift-count-any-int-type`. **arm32 unit/conformance confirmation pending CI on `fd3cb7ac`.**
- **A dead-end worth recording**: a first, larger rewrite (a dedicated `emitShift` that also fixed the count-wider OVERSHIFT corner by widening the value) was correct on native but **regressed signed sub-word shifts on the bytecode VM** — identical-looking IR, different VM result (`int8(1) << 2` → -64). Reverted for the minimal change above. Separately, the checker's `return lt` for an UNTYPED count (vs deferring to foldIntBitwise's commonType) also broke signed sub-word `>>` on the VM (`(-i8v) >> 4` → 0 not -1); hence the minimal checker only short-circuits the typed-vs-typed case. The VM-fragility of these paths is real but was avoided, not fixed.
- **Symptom**: `pkg/binate/types/types_query.bn:168` is `var shifted int64 = cast(int64, 1) << (width - 1)`. In 32-bit-int target modes the shift count `(width - 1)` is `int` (32-bit) while the shifted operand is `int64`, so the checker rejected it: `mismatched types int64 and int`. Because `types_query.bn` sits in nearly every package's transitive dependency, the single error cascaded — arm32 unit + conformance failed to compile. Compiles fine in 64-bit-int host modes (where `int`'s width == `int64`), which is why every `-comp*` mode stayed green and the break was invisible to the green legs.
- **Baseline / regression proof**: `builder-comp_arm32_baremetal` Unit was **green at bnc-0.0.7** (commit `ee06ec87`, job `success`); it was **red at `ac738936`**. The offending line landed in `efeb0f94` (2026-06-05, the integer divide/remainder fault-guard work), after the 0.0.7 tag (2026-06-04) → in-window regression.
- **Follow-ups**: (a) ✅ split `pkg/binate/types/check_expr.bn` (binate `a57496e6`) — back under the soft limit; binary-op checking + tests in `check_expr_binop{,_test}.bn`. (b) ✅ comprehensive shift type-pair MATRIX (binate `93d6ecd4`) — `conformance/matrix/shift-typepair/` covers the full (value-type, count-type) product for `<<`/`>>`, asserting permitted + result-type-is-the-value's + value correctness; green on native/VM/gen2. (c) 🟡 OPEN (narrowed) — RUNTIME count-wider OVERSHIFT corner: when the count is a RUNTIME value whose TYPE is wider than the value AND whose VALUE ≡ a small residue mod 2^valueBitWidth (e.g. a runtime `uint16` count of 256 shifting a `uint8`), the count truncates to the value width and overshift is mis-detected (silent wrong value). The CONSTANT case of this (a literal/const count `>= width`, any count type) is now handled — `emitConstOvershiftOrNil` keys on the untruncated `IntVal` (see N1 RESOLVED `11f99ed9`); only the runtime sub-case remains. Reachable only with an absurd RUNTIME count (≥ 2^width); proper fix = do the overshift comparison at the wider (count's) width (VM-safe, not sub-word) before truncating. The matrix deliberately uses count = valueWidth (≤ 64, fits every count type) so it does NOT exercise this corner.
- **Coverage gap (origin)**: `SignedMinForWidth`'s tests ran only in 64-bit-int host mode, so the 32-bit-int break was invisible to the green legs — the recurring "tests only exercise host-int" trap.
- **Discovery**: 2026-06-10 bnc-0.0.8 release-gate verification.

### Named func-value type (`type Fn @func(...)`) is unconstructible — all backends — PRE-EXISTING — REF-HALF ✅ RESOLVED (binate `e1dcd14e` 2026-06-11); literal-half 🔴 OPEN (tracked follow-up)
- **DESIGN (decided 2026-06-11)**: named func-value types are **constructible from func REFERENCES / literals but NOMINAL for func VALUES** (parallel to named scalars — untyped-literal construction, nominal typed values). So `var f Fn = dbl` (ref) and `var f Fn = func(...){}` (literal) should work; `var f Fn = g` (a `@func` value) stays rejected.
- **REF-HALF ✅ RESOLVED (`e1dcd14e`)**: `var f Fn = dbl` (+ raw `*func` named types + reassignment) now construct and call correctly on all modes. The originally-proposed `checkExprWithFVHint`-peel was the LITERAL half; the REF half was actually two checker peels (`AssignableTo`'s func-ref arm + `checkCallExpr`) **plus the real root fix in IR-gen**: `typeDeclEntryType` (moved to `gen_typedecl.bn`) now represents a named func-value type transparently as its underlying `@func`, because func values carry no IR-level nominal identity and every consumer (construction / call dispatch / copy / dtor / refcount) keys off the func-value kind — a TYP_NAMED wrapper made each mis-handle it (call → direct global ref to a nonexistent symbol; dtor skipped). Stripping once at the source routes the value through all existing `@func` machinery (no missed-site UAF/leak risk). Cells: `named-func-value-construct` (un-xfailed), `named-func-value-reject-value` (locks the value-rejection); unit `gen_typedecl_test.bn`.
- **LITERAL-HALF 🔴 OPEN**: `var f Fn = func(...){}` still rejected (`conformance/regressions/named-func-value-construct-literal`, xfailed all modes). Needs `checkFuncLit` to RETURN the named type when hinted by one (so the literal is `Identical` to `Fn` — a `@func` value isn't assignable to the nominal `Fn`) AND `isManagedFuncValueLit` (`gen_func_lit.bn:192`) to peel TYP_NAMED. This is the **memory-sensitive** piece: a func literal can CAPTURE, so the stack-vs-heap-alloc + refcount classification must be right (validate under guard-malloc). `checkExprWithFVHint` (`check_expr.bn:30`) must also peel the hint so the literal gets the `@func` flavour.
- **Symptom**: `type Fn @func(int) int; var f Fn = dbl` → rejected "cannot assign func(...) to Fn"; `var f Fn; f = func(x int) int {…}` → "cannot assign <unknown> to Fn". The anonymous spelling `var f @func(int) int = dbl` WORKS (prints 42). So a named func-value type can be declared but never constructed.
- **Root cause**: `checkExprWithFVHint` (`pkg/binate/types/check_expr.bn:30-39`) installs the func-value flavour hint only when `hint.Kind` is TYP_FUNC_VALUE / TYP_MANAGED_FUNC_VALUE; it never peels TYP_NAMED/ALIAS/READONLY. A named func-value resolves to TYP_NAMED, so the hint is dropped and the literal defaults to raw `*func`. Broader: AssignableTo's named-func-reference arm (`types_assignable.bn:69-73`) also doesn't peel the named dst, so even `var f Fn = someTopLevelFunc` fails. Shared by ALL func-value hint sites (plain `=`, var-init, return-slot, call-arg); `e15680d7` routed plain `=` through the SAME pre-existing single-peel-short guard, so this is not a regression from it.
- **Severity**: MAJOR — a whole supported, tested feature (`conformance/matrix/globals/noinit/named-func.bn` declares one) is unusable; spurious compile-time rejection (fail-safe, no miscompile). Workaround: use the anonymous `@func(...)` spelling.
- **Fix**: peel transparent wrappers in `checkExprWithFVHint` before reading `hint.Kind`, AND peel the dst in AssignableTo's func arms. Touches the shared hint mechanism.
- **Test**: `conformance/regressions/named-func-value-construct` (xfailed all modes, binate `a77591e0`). Cells at each assignment position + a unit test still wanted.
- **Discovery**: 2026-06-09 CR-2-batch review (B2 finder); runtime-confirmed (named rejected, anon works).

---

## CR-2 Plan-1 Adversarial Review — pre-existing sibling miscompiles (2026-06-08)

An adversarial multi-agent review (53 agents) + hand-verification of the CR-2
Plan-1 defect fixes (Defects 1–9). **Headline: the landed fixes are correct
for exactly what they claimed, but INCOMPLETE — each peeled/migrated at SOME of
the sites sharing its root cause and left the siblings broken.** These siblings
are PRE-EXISTING miscompiles (no Plan-1 fix touched them; C1's pre-existence
was confirmed by building a pre-fix compiler) — **none is a regression
introduced by the fixes**, and no green test went red. The recurring root
causes: (R1) wrapper-transparency peeled in predicates but not at the consuming
extraction / call-convention / construction sites; (R2) `isAggregateAllocToLoad`
migrated to only 2 of ≥6 aggregate-store/arg arms; (R3) the multi-return
slot-typing fallback landed in `:=` but not `=`; plus the Defect-9 `-` fix
gating on `TYP_INT` (not peeling `TYP_NAMED`). Each fix is a peel-at-the-
consuming-site / swap-the-guard one-liner + xfail-then-fix coverage; all ship
green because no test exercises the wrapped / nameless / composite-literal /
named-type variant. Per the user (2026-06-08): FILE all, FIX nothing yet.
The CRITICAL entries below are also surfaced in `## CRITICAL`-class triage.

### [CR-2 Plan-1 review] AMEND existing CRITICAL "iface-upcast −1-offset footgun" (filed `866b935`) with two new reproducer details
- **(a) Concrete trigger via the Kind axis**: `ifaceValueTypesAgree` (`gen_util.bn:225`) keeps `if a.Kind != b.Kind { return false }`, so a managed↔raw decay of the SAME canonical interface (`@Empty → *Empty`) still takes the upcast path → `IfaceParentSlotOffset(X,X) = −1` → `getelementptr inbounds i8*, i8** %vt, i64 −1` (UB). **Probe-confirmed** the −1 GEP is emitted. **Reachability is bounded and currently HARMLESS**: the type checker rejects `@Iface → *Iface` for any NON-empty interface (`isDescendantInterface(X,X)=false`), and `@Iface → *any` uses the offset-0 `any` special case — so −1 fires only for zero-method interfaces, which never dereference the vtable. Latent wrong-code, not a reachable crash. Targeted fix when the footgun is addressed: in `ifaceValueTypesAgree`, when canonical `(Pkg,Name)` AGREE return true regardless of Kind (same-interface managed↔raw relabel needs offset 0); a blanket Kind-ignore would be WRONG (a genuine `@Child → *Parent` upcast must keep its real positive offset — probe shows +1).
- **(b) Cross-backend divergence on the −1**: the LLVM lowering (`emit_iface_upcast.bn:57-60`) writes the offset verbatim (`i64 −1`), while BOTH native backends guard with `if byteOff > 0` (`aarch64_dispatch.bn:166`, `x64_dispatch.bn:242`) and so leave the vtable pointer unchanged (accidentally offset 0). So the same IR yields base−1 on LLVM vs base on native. The fix should make −1 a hard assert in all three lowerings (not a silent GEP) and have `IfaceParentSlotOffset(X,X)` return 0 like the `any` case.

### [CR-2 Plan-1 review] MINOR / doc-comment & xfail-hygiene corrections (2026-06-08)
- **N2 (misleading comment, no bug)**: `gen_iface.bn:86-90`'s `peelTransparent(iv.Typ)` is DEAD for the readonly case (`stripConstForIR` in newInstr/EmitLoad already removed it); the load-bearing fixes are in `check_method.tryMethodCall` + `isInterfaceMethodCall`. Correct the comment (reframe as defensive or drop).
- **N3 (misleading comment + 1 xfail cell)**: `checker_errors.bn:193-194` and `types_query.bn:248-251` claim comparability is "deferred to the concrete instantiation" — NO instantiation-time check exists; `eq[*[]int]`/`eq[@[]int]`/`eq[struct]` emit invalid `icmp` at instantiation (pre-existing, tracked as a user-owned follow-up). Fix the doc-comments; add an xfail `eq[@[]int]` cell (deterministic clang failure).
- **N1 (narrow, pre-existing) — ✅ RESOLVED 2026-06-12 (`11f99ed9`)**: an out-of-range CONSTANT shift count was wrapped into [0,width) by `ensureWidth` BEFORE the overshift guard (`v << 256` on uint8 → 1 not 0; signed `int8 >> 256` stays -64 not sign-filled; same in `<<=`/`>>=`). New `emitConstOvershiftOrNil` (`gen_binary.bn`) detects a constant count `>= width` from its ORIGINAL (pre-`ensureWidth`) `IntVal` and emits the spec result directly — 0 (logical `<<`/unsigned `>>`) or sign-fill `lhs >> (W-1)` (signed `>>`), the SAME result `emitGuardedShift` already produces for a runtime overshift (VM-consistent — the path the reverted "widen the value" attempt regressed). Wired into BOTH `genBinaryExpr` and `emitCompoundBinop`, before each truncates the count. Keying on `IntVal` also covers a wider-TYPED constant count (uint16 const 256 shifting a uint8). `conformance/729_const_shift_overshift` green on LLVM / both VM lanes / native aa64 / native x64-darwin; the 48 existing runtime-count shift/overshift cases + ir unit tests unaffected. (Only the **runtime** count-wider corner (c) remains.)
- **N10/N11 (xfail hygiene)**: funcval-multi-return arm32/x64 un-xfail is SOUND (record why in a note). iface-multi-return x64 xfails are stale and arm32 xfails are mislabeled "native" though arm32 compiles via LLVM — rewrite the markers (x64-darwin verified / x64-linux unverified-no-qemu; arm32 → LLVM, separate unconfirmed 32-bit ABI).
- **Coverage-only (verified-correct paths)**: 659 omits raw-pointer-index compound-shift (`p[i] <<=`) and signed `>>=` overshift on non-IDENT lvalues; the genShortVar nameless `multiReturnFieldTypes` fallback has no IR-gen unit test / no managed-component func-value `:=` cell; Defect-2b raw-pointer & value receiver rows have no conformance/unit coverage (the reject paths are soundness-critical and the TYP_POINTER/TYP_MANAGED_PTR arms are duplicated).

## CRITICAL

### `=` (assignment) multi-bind from an interface dispatch / func-value call mistyped every component as int — FIXED 2026-06-08 (`f8916b88`)
- **Found by the Plan-2 adversarial review.** `genMultiAssign` (`pkg/binate/ir/gen_assign_multi.bn`, the `a, b = …` form) derived per-component result types only from `lookupFuncResults(val.StrVal)` for a DIRECT call (`OP_CALL`). An interface dispatch (`OP_CALL_IFACE_METHOD`) and a func-value call (`OP_CALL_FUNC_VALUE`) have no callee name, so retTypes stayed empty and every component defaulted to `int`: a sub-word component was stored as i64 (invalid IR → clang reject) and a managed component skipped its Axiom-3 copy-RefInc (latent UAF if it had compiled). `a, b = iv.m()` / `a, b = fv()` with any non-int component thus failed to compile; the `:=` form (`genShortVar`) already had the `multiReturnFieldTypes` fallback, so the asymmetry hid it. Became reachable once iface/func-value multi-return dispatch started working (the CR-2 SEAM `6c39d460` + iface-dispatch-by-value `43cb195d` + func-value destructure `2a77188c`); no test caught it because the whole abi multi-return matrix binds with `:=` and uses only int/u16.
- **Fix**: mirror genShortVar's fallback in genMultiAssign (derive component types from the multi-return tuple struct when retTypes is empty). Additive. Pinned by `gen_assign_multi_test.bn` TestMultiAssignFuncValueCallCopyRefInc (verified red without the fix); end-to-end (uint16,int) and (int,@[]int) `=`-form iface + func-value repros compile/run, 200k-iter managed loop balances.
- **OPEN follow-ups (from the same review)**: (a) **coverage** — extend `conformance/gen-abi-matrix.py` with an `=`-form (assignment) binding axis + a managed-component type for the multi-return-through-dispatch cells (the surface that hid this bug; today all cells use `:=` and int/u16 only). (b) **stale xfail comment** — the surviving native `iface-multi-return/int/{3,4,5}` xfails (`builder-comp_native_x64`, arm32) blame the already-fixed SEAM ("drops multi-return result type"), not Plan-3's open native tuple-packing gap; rewrite the markers.

### Global `var` of an interface-value / func-value (or readonly-wrapped aggregate) type emits invalid LLVM (`global %BnIfaceValue 0`) — ✅ RESOLVED — LANDED `91ef4fc4` (verified on main 2026-06-10)
- **Symptom**: any package-level `var x @Iface` / `@errors.Error` / `*func()` / `@func` (with or without an initializer), AND any `readonly`-qualified aggregate/iface/func/struct/array/slice global, made the LLVM backend emit `@<mangled> = global %BnIfaceValue 0` (or `%BnFuncValue 0`, `%bn_main__Pt 0`, …), which clang rejects: `error: integer constant must have integer type` — the whole package fails to compile. Blocked a `pkg/std/io` `var EOF @errors.Error = errors.New("EOF")` sentinel (and any iface/func-value package global).
- **Root cause**: `pkg/binate/codegen/emit.bn` global-var static-zero dispatch — the SAME dispatch as the float-global sibling above — picks the zero by type kind (`null` ptr, `zeroinitializer` slice/struct/array, ` 0.0` float, ` 0` otherwise). Two gaps: (1) the 16-byte address-aggregate kinds (`TYP_INTERFACE_VALUE[_MANAGED]` → `%BnIfaceValue`; `TYP_[MANAGED_]FUNC_VALUE` → `%BnFuncValue`) fell through to ` 0` but are LLVM struct types needing `zeroinitializer`; (2) the dispatch tested `g.Typ.Kind` DIRECTLY while `llvmType`/`IsFloat` unwrap `TYP_READONLY` first, so a `readonly`-wrapped aggregate global got the right printed type but the wrong ` 0` init token. Same code-red "missing iface/func-value arm" + "aggregate-as-scalar" shape, in the global emitter.
- **Fix — LANDED `91ef4fc4` (the orphaned worktree commit `5dddef7d` on `temp-binate-4` was superseded by this functionally-identical landed commit; `f2ebaca1` later extended the dispatch to also peel `TYP_NAMED`)**: add the four address-aggregate kinds to the `zeroinitializer` branch AND unwrap `TYP_READONLY` at the top of the dispatch (mirroring `llvmType`). Verified on main 2026-06-10: `--emit-llvm` emits `@bn_main__g = global %BnIfaceValue zeroinitializer`, `@bn_main__f = global %BnFuncValue zeroinitializer`, `@bn_main__ro = global %bn_main__Big zeroinitializer` (valid, not the invalid ` 0`); full clang compile exit 0. Adversarially reviewed (4-agent workflow): correctness + refcount confirmed (the `__init` store MOVES the fresh value in via consumeTemp; the zeroinitializer prior-occupant RefDec is a verified null-data no-op; immortal sentinel by design, like Go's io.EOF); the readonly variant was the review's blocker finding; no regression to int/bool/char/ptr/float/struct globals. Unit test `pkg/binate/codegen/emit_global_test.bn` (func/iface/readonly → zeroinitializer, not ` 0`; + the float sibling). End-to-end: cross-package `var EOF @errors.Error = errors.New("EOF")` compiles, `__init` runs it, consumer reads it + `.Error()` correct; 1000-iter stress clean.
- **Severity**: MAJOR — hard compile error (not silent), blocked any package-level interface-value / func-value (or readonly-aggregate) global. Discovered 2026-06-07 implementing `pkg/std/io`'s `io.EOF`.
- **Test-gap analysis (the "why wasn't this caught / how to prevent" ask) + FOLLOW-UP**: the defect lived in a structurally-EMPTY matrix intersection — `conformance/matrix/aggregate/global` sweeps the `global` op over {scalar,array,struct}×{int,float} but NOT iface/func kinds; `conformance/matrix/addr-aggregate/{func-value,iface-value}` sweeps those kinds over {direct,copy,return,arg,return-arg,field,array-elem} but has NO `global` op. Neither product's coordinates included "a package-level global of a 2-word address-aggregate", and there was ZERO codegen unit coverage of the module-global path. PREVENTIVE FOLLOW-UP (deferred per the user): add a `global` operation to `conformance/gen-addr-aggregate-matrix.py` (OPERATIONS) → `addr-aggregate/{func-value,iface-value}/global.bn` + a no-initializer companion (sweeping the with/without-runtime-initializer axis), update its README, run hygiene. ALSO unverified: VM (`-int`) + native modes — the VM materializes globals separately (`vm/lower_data.bn`); confirm it handles iface/func-value globals before relying on `io.EOF` in `-int`/native (xfail per mode if not). The unit test is mode-independent and already guards the codegen fix.

### bnc front-end / IR-gen memory blows up (>8.5 GB, OOM) compiling a ~1370-line program — super-linear, NOT raw size — PRIMARY FIX LANDED on main
- **Status (2026-06-05)**: fix **(1)** below LANDED on main (binate
  `7804c287`) — `registerPendingStructDtor`/
  `registerPendingMsDtor` now dedup via a precomputed-name list (`hasName`) with
  the incoming name built once, instead of re-spelling every existing entry per
  call. **Validated**: minbasic `bnc cmd/run` now compiles to a working 270 KB
  binary in **~1 s at 27 MB peak RSS** (was >8.5 GB / OOM-killed after ~15 min);
  `--emit-llvm` 27 MB / 2 s (was 7.5 GB / 54 s / 0 IR lines). `refcount` matrix
  105/0 and the `pkg/binate/ir` unit tests stay green. Fixes (2)-(4) below remain
  as follow-ups — they remove the *other* super-linear factors (unmemoized Type
  queries, O(n) `slices.Append`, `ctx.Vars` rescan) for even larger programs, but
  (1) alone brought minbasic back to tractable.
- **Symptom**: compiling the minbasic example (examples repo, `minbasic/cmd/run`
  — ~1370 lines of `pkg/basic` plus transitive `strconv`/`buf`/`slices`/`errors`)
  drives `bnc` to **>8.5 GB RSS** and it is OOM-killed (SIGKILL) after ~15 min on
  a 24 GB machine. `bni` similarly peaks ~8 GB. M0 (the banner skeleton) compiled
  in seconds; the jump is the M1 interpreter code.
- **Localization — front-end / IR-gen, NOT the LLVM backend**: `bnc --emit-llvm`
  (stops after IR-gen, before the native/LLVM backend) reaches **7.5 GB in 54 s
  and emits 0 IR lines** before being killed. So the blowup is in `bnc`'s
  front-end / IR-gen, not LLVM codegen.
- **NOT raw program size**: `bnc`/`bni` themselves (far larger) build fine.
  Ruled out by probes (all `bnc --emit-llvm`, peak RSS, on a `main` bundle):
  trivial `strconv.FormatFloat` user → light (2 s); recursive/nested managed AST
  types (`Expr{@Expr, @[]@Expr}` + `Stmt`/`Line`) → light; a struct
  `Value{int,float64,@[]char}` returned BY VALUE, standalone → light;
  `Value` + nested AST types + `slices.Append[@Line]` + `buf` together,
  standalone → light; synthetic 10/20/30 functions each building managed
  `Expr`/`Value` → all light.
- **Bisected trigger (a super-linear interaction)**: within minbasic's
  `pkg/basic`, the **parser side alone** (token/ast/lex/parse/parse_expr + the
  basic.bn loader — ~700 lines; nested-managed AST types, `slices.Append`, `buf`)
  compiles LIGHT (2 s). **Adding `value.bn`** — 34 lines: a
  `Value{int,float64,@[]char}` struct + two by-value constructors, *not even
  referenced by the parser side* — flips it to an **8.56 GB blowup**. Each piece
  is light in isolation; the combination is not. Cost appears super-linear in
  (functions × managed-types) within one package, but is NOT reproduced by
  synthetic isolations — the real parser-side code's structure matters.
- **Repro**: (full) build `examples/minbasic/cmd/run` against a `main` `bnc`
  bundle → OOM. (reduced) the same package with the eval-side files
  (eval/exec/print/format/env) removed and `runProgram` stubbed, leaving the
  parser side + `value.bn`, still OOMs at ~8.5 GB; removing `value.bn` makes it
  light (~2 s).
- **Discovery**: 2026-06-05, building minbasic M1 slice 1 (examples `5b55644`).
- **Root cause (triaged 2026-06-05, 5-agent static analysis — strong
  cross-corroboration; all five independently fingered the same site)**: the
  dominant term is **`registerPendingStructDtor` / `registerPendingMsDtor`**
  (`pkg/binate/ir/gen_util_refcount.bn:96-102` / `:143-149`). Each call does a
  linear dedup scan of the **module-global** `pendingStructDtors` list AND, for
  **every** existing entry, *recomputes* `dtorNameForType(entry)` — a `buf.New()`
  managed-slice allocation + a recursive type-spelling walk + `Bytes()`. It is
  invoked from `emitStructCopy`/`emitStructDtor`, which fire at every
  managed-AGGREGATE copy/dtor/scope-cleanup site (var-init, assignment,
  composite-literal field/element, return, and every scope-exit cleanup for every
  managed-aggregate local) across **all** functions; the list grows monotonically
  for the whole package. Net **O(functions × managed-aggregate-types)** with a
  throwaway name-buffer allocation per existing entry per call → both the 54 s
  time and the multi-GB transient/persistent RSS, all before a single IR line.
- **Why `value.bn` is the trigger**: before it, the parser side holds its AST via
  `@Expr` / `@[]@Expr` — managed **pointers/slices**, which take the *scalar*
  refcount arms (`EmitRefInc`/`emitManagedSliceRefDec`), NOT
  `emitStructCopy`/`emitStructDtor`, so `pendingStructDtors` stays ~empty.
  `Value{int,float64,@[]char}` is a managed-**aggregate** (`needsStructCopy` via
  the `@[]char` field), so the moment any `Value` is copied/dtor'd/cleaned-up the
  *aggregate* arms fire across the package's many functions — flipping the
  dominant term from ~0 to `functions × aggregate-sites`.
- **Amplifiers (corroborated, secondary)**: (a) `slices.Append` (stdx) is **O(n)
  per append** — `make_slice(n+1)` + copy-all, no capacity doubling — so every
  hot IR-gen accumulator (`pendingStructDtors`, `ctx.Temps`, `ctx.Vars`, return
  `vals`) is O(n²); (b) `NeedsDestruction` (`types_query.bn:377`) and
  `SizeOf`/`AlignOf`/`FieldOffset` (`scope.bn:112/160/207`) are **unmemoized**
  (no cache slot on `@types.Type`, `types.bni:71`), recomputed at every emit-site;
  (c) `emitDecForManagedLocals` re-scans **all** `ctx.Vars` at each scope-exit;
  (d) `resolveTypeExpr` allocates a fresh `@Type` per type-expr occurrence (no
  interning); (e) `lookupFuncParams`/`collectFuncStrings` do O(n) linear scans.
  The unifying disease: **no memoization on the `@types.Type` node + module-global
  accumulators scanned/re-mangled linearly.**
- **Fix (ranked, layered)**: **(1) PRIMARY** — make the
  `registerPendingStructDtor`/`registerPendingMsDtor` dedup O(1): compute the
  dtor name once for the incoming type, look it up in a set (or hang a
  `DtorRegistered` flag / cached name on `@types.Type`); never recompute
  `dtorNameForType(existing)` in the loop. This alone removes the dominant
  O(functions × types) + per-entry-allocation term. **(2)** add cache slots to
  `@types.Type` and memoize `NeedsDestruction` + `SizeOf`/`AlignOf`/`FieldOffset`
  + the dtor/copy name (layout is fixed within a compile). **(3)** give `slices`
  a capacity-doubling amortized-O(1) append (or use growable buffers for the hot
  accumulators). **(4)** track managed-cleanup slots in a compact per-function
  list instead of re-scanning `ctx.Vars`. (1) is the high-leverage fix; (2)-(4)
  remove the remaining super-linear factors.
- **Validation suggested**: instrument `registerPendingStructDtor`'s call-count ×
  list-length (or a knob-scaled repro: N managed-aggregate types × M functions)
  to confirm the O(N×M) curve, then re-run the reduced minbasic repro after fix
  (1). No `bnc` profiling flag exists; a temporary counter is the cheapest probe.

### VM: a function value RETURNED from a call and PASSED DIRECTLY as an argument has a nil vtable — CONFIRMED, VM-only — ✅ RESOLVED (binate `e337e413`, `isVMAddressAggregate` single-return copy-back in `lowerReturn`)
- **Symptom**: `use(mk())`, where `mk() @func(...)` returns a (non-capturing)
  function value and `use(w @func(...))` invokes it, aborts in the bytecode VM
  with `vm: function value has nil vtable`. Compiled (native) is correct.
- **Scope**: bytecode VM ONLY (LLVM/native correct). Triggered specifically by
  passing a freshly-RETURNED function value DIRECTLY as a call argument. The two
  halves work in isolation: returning a function value then calling it directly
  via an EXPLICITLY-typed local (`var w @func(...) = mk(); w(x)`) is fine, and
  passing a LOCAL/param function value as an
  arg (`use(w)` with `w` a local) is fine — only the un-materialized
  return-value-as-arg combination loses the vtable word here.  (The
  INFERRED-type spelling `var w = mk(); w(x)` — no `@func` annotation — is
  separately broken on ALL backends; see the `## MAJOR` entry "Inferred-type
  func-value local call mis-lowers to a direct symbol".)  Specifically, only the un-materialized
  return-value-as-arg combination loses the vtable word. Workaround: bind to a
  local first (`var w @func(...) = mk(); use(w)`).
- **Test**: ✅ `conformance/regressions/funcval/return-as-arg` (binate
  `d493b25b`, on the worktree, pending cherry-pick). `use(mk())` returning/
  passing a non-capturing `@func(int) int`, asserts `42`. Verified: compiled-
  final + native pass; the 3 VM-final modes (`builder-comp-int`,
  `builder-comp-int-int`, `builder-comp-comp-int`) abort `nil vtable` and are
  xfailed — un-xfail when the fix lands.
- **Discovery**: 2026-06-05, wiring minbasic's injected `@func` writer
  (`basic.Run(host.NewWriter())`): the VM aborted with nil vtable. Isolated to
  the return-value-as-arg pattern; `bnc-0.0.7`.
- **Why it matters**: blocks injecting a `@func` writer/sink built by a factory
  (`Run(host.NewWriter())`) — a natural DI shape. Together with the iface-vtable
  2-word-slice-arg bug, it leaves only static/direct calls reliable for I/O
  injection on `bnc-0.0.7`, so minbasic uses a clearly-marked static temp
  meanwhile.
- **Fix**: in the VM, marshal a function-value (2-word {vtable,data}) call
  argument that is an un-spilled call result the same way a local/param function
  value is marshalled — the vtable word is being dropped for the return-value-as-
  arg case.

### Sub-word arithmetic results not narrowed in the VM (and natives) — dirty upper bits → wrong values — PRIMARY (add/mul narrowing, VM + natives) RESOLVED; aa64-subword EXTENSION still OPEN
- **STATUS 2026-06-10 (triage)**: the PRIMARY facet — sub-word add/mul narrowing in the VM and natives — is FIXED+LANDED (`435b6cdd` VM, `ee671b6c` aa64, `57e72d9e` x64; `matrix/scalar/{add,mul}/{8,16,32}/unsigned` have no xfails; the VM's `vm_exec_pure.bn` has `applyNarrow`/`narrowToWidth`). What REMAINS OPEN is the **aa64-subword extension** below — native-aa64 sub-word **signed shifts, all int-casts, signed sub-word conversions, signed cmp, float↔int** still leave dirty upper bits (≈29 `matrix/scalar*` native_aa64 xfails, `--check-xpass`-confirmed genuinely-failing). Keep this entry for that extension; the original add/mul-narrowing symptom below is historical.
- **Symptom**: a sub-word integer op (`uint8/16/32` add/mul/…) whose true result
  overflows the width leaves the un-narrowed value in the host register; a
  width-sensitive consumer reached DIRECTLY (no intervening sized store/cast) —
  shift, unsigned compare, divide, widen — reads the dirty upper bits → wrong
  value. E.g. `(a*b) >> 8` for `uint16 a=b=60000`: **164 on LLVM, 37796 on the VM**.
- **Root cause (CONFIRMED)**: the bytecode VM's `execArithOp`
  (`vm_exec_pure.bn`) computes at the host word width with no post-op narrowing
  to the result type's width; the native backends (x64/aa64) carry the same gap
  (§3.8). LLVM is correct (true-width SSA). Storing the result into a sized var
  re-narrows it, so the bug is latent until the op result is consumed directly.
- **Test**: `conformance/matrix/scalar/{add,mul}/{8,16,32}/unsigned` (xfailed the
  3 VM default modes; pass on LLVM). The scalar matrix's first members.
- **Discovery**: 2026-06-05, P1 scalar matrix. Flagged in plan-code-red.md §3.8 /
  §8; now confirmed + systematically covered.
- **Fix**: narrow sub-word op results to their width — a post-op narrow in the
  VM/native arith handlers, or an IR-gen narrow after each sub-word value-
  producing op (a P3 design call). Also covers the native variants.

### Unsigned int→float uses a SIGNED conversion in the VM — wrong value — CONFIRMED — UPDATE 2026-06-06: the scalar-diff differential shows the unsigned→**float64** path now PASSES on the VM (so this specific signedness bug appears resolved); a *distinct* int→float32 defect remains — see `vm-int-to-float32` below
- **Symptom**: `cast(float64, y)` for an unsigned int whose top register bit is
  set (on the 64-bit host, only `uint64` with bit 63) yields a NEGATIVE float —
  the VM converts as signed. E.g. `cast(float64, <uint64 bit-63>) > 0.0` is
  true on LLVM, false on the VM.
- **Root cause (CONFIRMED)**: the VM's int→float lowering uses `BC_SITOF`
  (signed) regardless of the operand's signedness; LLVM uses `uitofp` for
  unsigned. The native backends carry the same gap (§3.8). A `uint32` is
  zero-extended (positive in the 64-bit register), so only `uint64` triggers
  it on the host.
- **Test**: `conformance/matrix/scalar/int-to-float/64/unsigned` (xfailed the 3
  VM modes; `/32` passes as a baseline).
- **Discovery**: 2026-06-05, P1 scalar matrix int-to-float cells. Flagged §3.8.
- **Fix**: dispatch int→float on operand signedness (a `BC_UITOF` / unsigned
  path), mirroring the cmp/div/shift signedness selection. Same for float→int
  and the native backends.

### Differential scalar harness (`matrix/scalar-diff`) landed — two backend defects found: `vm-int-to-float32` and `aa64-subword` — CONFIRMED
- **What landed**: `conformance/gen-diff-scalar.py` + 41 cells / 1707 tuples
  under `conformance/matrix/scalar-diff/` — a property-based **differential**
  value-correctness harness for scalar shifts & conversions. Oracle is the
  **spec** (computed at full precision, independently validated by a 5-reader
  adversarial pass), not a backend, so spec-divergences (the shift-bug class)
  are caught too. Self-checking cells (`println(cast(int, computed == spec))`)
  for target-stability across 32/64-bit. Green on all LLVM modes + arm32
  baremetal; the two clusters below are xfailed (verified non-stale via
  `--check-xpass`). Idempotent generator; `int↔int` casts and all shifts pass
  on every real backend (broadened regression net for `32fde83d`).
- **`vm-int-to-float32` — VM `int → float32` is broken (every width/sign) — ✅ RESOLVED 2026-06-12 (binate `289420b6`)**:
  every `cast(float32, <int>)` diverged — even `cast(float32, 1) > 0.0` was
  false on the VM. Root cause: `int → float` lowered to `BC_SITOF`/`BC_UITOF`,
  which land at **float64**; the VM's float32 register form is the float32 IEEE
  bits in the low 4 bytes, so the float64 pattern's low word (usually zero) read
  back as ~0. Fix: fused `BC_SITOF32`/`BC_UITOF32` opcodes that write the
  float32 bit pattern directly, selected in `lower_cast` when the cast dest is
  float32 (signedness still picks signed/unsigned). Un-xfailed **16 of 17** VM
  cells across all 3 VM modes; 3 VM unit tests added (lowering decision ×2 +
  end-to-end round-trip). The 17th cell (`float-to-int/64/unsigned`) uncovered a
  **distinct sibling bug** (`vm-float32-to-unsigned`, now also resolved — see
  below).
- **`vm-float32-to-unsigned` — VM `float32 → unsigned int` used the SIGNED conversion — ✅ RESOLVED 2026-06-12 (binate `3fd7e712`)**:
  surfaced while fixing `vm-int-to-float32`. `lower_cast`'s `float → int` arm
  picked `BC_F32TOSI` (signed) for a float32 source regardless of dest sign
  (its comment admitted "float32 → unsigned is not yet exercised; it stays on
  the signed `BC_F32TOSI`"). So `cast(uint64, <float32 ≥ 2^63>)` saturated to
  `INT64_MAX` instead of the in-range unsigned value — a *defined* (in-range)
  conversion miscompiled, MINOR (only float32→uint64 of values ≥ 2^63; the
  8/16/32-bit unsigned high-bit values fit signed int64 so those cells already
  passed). Fix: the exact mirror of the float64→unsigned `BC_FTOUI` — added a
  `BC_F32TOUI` opcode (`cast(int, cast(uint64, <float32>))`), picked in
  `lower_cast` for a float32 source with an unsigned dest. Un-xfailed the last
  scalar-diff VM cell (`float-to-int/64/unsigned`, the 2^63 round-trip) across
  all 3 VM modes; 2 unit tests added (lowering decision + high-bit round-trip).
  **All scalar-diff conversion cells are now green on every VM mode** — the VM
  int↔float32 story is complete in both directions.
- **`aa64-subword` — native-aa64 doesn't narrow/sign-extend sub-word results**:
  a sub-word op leaves dirty high bits / wrong sign. `int8(-128) << 1` keeps
  bit 8 set (so `== 0` fails); `cast(int8, 128:uint8)` and the other
  `uint8 → int{8,16}` casts are wrong. 17 xfailed cells: `shl`/`shr` 8/16/32
  **signed**, all 8 `int-cast`, signed sub-word `float-to-int`/`int-to-float`.
  64-bit and most unsigned paths are fine. The native sibling of the VM/native
  sub-word-narrowing gap above, here confirmed across shifts/casts/conversions
  (not just arithmetic). Fix: post-op narrow + sign-extend sub-word results in
  the aa64 backend (or an IR-gen narrow — the shared P3 design call).
- **native-x64 / arm32-linux not evaluated**: the host lacks x86_64 C runtime
  headers (`stdio.h` → every native-x64 cell `COMPILE_ERROR`s uniformly, an env
  limitation, *not* a backend result — no x64 xfails placed), and `arm32-linux`
  needs `qemu-arm` (skipped). Re-check on an x64 host: the aa64 sub-word defect
  very likely has an x64 analog needing its own xfails.
- **Discovery**: 2026-06-06, differential-harness v1 (plan-differential-testing.md).
- **v2 (arith/cmp/bitwise) — LANDED 2026-06-06** (binate `42ad4fa0` fix +
  `e71de1e0` harness): 123 cells / 5415 tuples total. v2 found+fixed the LLVM
  `~` bug (`bitnot-result-type`, above). Remaining divergences, all xfailed
  (`--check-xpass`-clean) and in the known classes: VM
  `bitwise/not/{8,16,32}/unsigned` (sub-word `~` dirty bits); native-aa64
  sub-word *signed* `arith/{add,sub,mul}/8`, `bitwise/{and,or,xor}/{8,16}`,
  `cmp/{8,16,32}`, `bitwise/not/*/unsigned`. Float compares incl. NaN/Inf/-0 pin
  the ordered/unordered `==`/`!=` semantics (corrected 2026-06-06). `fcmp/32`
  was xfailed at first but the float32-compare fix (binate `fc11d862`) landed
  concurrently, so it un-xfailed at land time (`--check-xpass` flagged the
  XPASS). The remaining VM `float32` *conversion* xfails (`int-to-float` /
  `float-to-int` / `float-cast`) stand — that gap is separate from compare.

### Audit the home of generic low-level helpers shared by cmd/bni + the REPL engine (low priority / code-org)
- **Context**: extracting the REPL engine to `pkg/binate/repl` (Stage 4c
  of `plan-repl-embeddable.md`) needs generic helpers that ALSO stay in
  cmd/bni: `streq`, `appendCharSlice`, `appendFilePtr`, `appendImportSpec`,
  `readFile`, `quotePath` (+ the IR-gen import-registration subtree
  `registerPkgImports`/`registerMainImports`/`loadBuiltinBNIs`/
  `ensureBootstrapLoaded`/`addLoaderPaths`).  For 4c these are
  **DUPLICATED** (each package keeps its own copy) to avoid a weird
  dependency (runProgram/runTests pulling in `pkg/binate/repl` just for
  `streq`).  `pkg/binate/buf` is the WRONG home (it owns CharBuf/CopyStr;
  `readFile`/`quotePath` don't belong there).
- **What to audit**: where these generic string / slice / file / IR-gen
  helpers SHOULD live long-term.  Survey the codebase for the real
  commonalities (who needs `streq`, `readFile`, the import-registration
  helpers?) and decide: a genuinely-shared tier-2 package (a possibly-
  uselessly-named `pkg/binate/utils`? a split between string-utils /
  file-utils / ir-import-helpers?), vs leaving the small ones duplicated.
  Consolidate the 4c duplicates once decided.

---

## MINOR

### Generic-instantiated composite-literal head `Foo[T]{…}` is specified but not built by the parser — spec §13.10 `expr.composite.generic` (2026-06-13) — 🔴 OPEN
Generic composite literals (`List[int]{1,2,3}`, `Pair[int, S]{first: …}`) are a
language feature (user decision 2026-06-13; matches Go) — specified in §13.10
`expr.composite.generic` and in `binate.ebnf` (the composite-literal head is a
`TypeName`, optionally generic-instantiated). The parser does NOT build them:
`parseIdentOrCompositeLit` (`pkg/binate/parser/parse_primary.bn` ~196–242) after
the head identifier checks only for `DOT` (qualified name) or `LBRACE`
(composite literal) — it never consumes a `[ TypeArgList ]` before `{`. So
`Foo[int]{…}` parses `Foo` as an ident, the postfix loop eats `[int]` as
`EXPR_INSTANTIATE_OR_INDEX`, sees `{` (not a postfix op) and stops, leaving the
`{…}` to the statement layer (a parse error / mis-parse). Fix: in
`parseIdentOrCompositeLit` (and/or the postfix path), after `Ident "[" TypeArgList
"]"` look ahead for `{` and, when present (and not `noCompositeLit`), parse a
composite literal with a generic-instantiated head — the D5/D11-style
disambiguation. Tracked as `expr.composite.generic-unparsed`. Needs a conformance
test (`var x @List[int] = List[int]{1,2,3}` or similar) + a coordinated binate
worktree.

### pkg/std/os O_* flags now compile-time-correct via build.OS — ✅ RESOLVED 2026-06-10 (binate 590906c8); arm32 off_t + VM residuals remain
`nativeOpenFlags` (`impls/stdlib/libc/pkg/std/os/os.bn`) branches on
`build.OS` — a per-target compile-time constant from `pkg/builtins/build`
(`ifaces/targets/<key>/pkg/builtins/build.bni`) that the compiler folds —
to emit the correct native open(2) modifier bits for Linux (asm-generic:
`O_CREAT`=0x40 / `O_TRUNC`=0x200 / `O_APPEND`=0x400 / `O_EXCL`=0x80 /
`O_SYNC`=0x101000) vs macOS (0x200/0x400/0x8/0x800/0x80); access modes
(0/1/2) are POSIX-identical and pass through. No runtime `uname` (the
user ruled that out as counter to Binate's compile-time-determinism
goals). The four Linux/host xfails were removed in the same commit, so os
is now green on every unit-test mode except the residuals below.
- **Residual — arm32-linux off_t (still xfailed,
  `pkg-std-os.xfail.builder-comp_arm32_linux`)**: Seek/ReadAt/WriteAt
  pass `int64` offsets, but on ILP32 arm32-linux `off_t` is 32-bit — a
  64-bit arg shifts the `lseek`/`pread`/`pwrite` register-pair arg layout
  and corrupts the call. Fix: use the `*64` variants or a target-width
  off_t (key off `build.Arch`/`build.PtrSize`), then drop that xfail.
- **Residual — os under the bytecode VM (still xfailed: the three
  `-int`/VM modes)**: the VM never interprets `__c_call` (by design); os
  runs under the VM only as the injected compiled package (registered
  native externs, like `pkg/builtins/rt`) — not wired up. Tracked
  separately. `arm32_baremetal` (no filesystem) stays xfailed too.

### Stdlib conformance tests: relax conformance-imports + add a conformance/stdlib/* suite — 2026-06-10
`pkg/std/os` (and stdlib packages generally) have unit tests but no
conformance coverage, because the `conformance-imports` hygiene check
(`scripts/hygiene/`) restricts what a conformance test may import — it
keeps the conformance set focused on the *language core*. In Binate the
stdlib is deliberately SEPARATE from the core language, so stdlib
conformance belongs in its own suite rather than mixed into the language
conformance tree.
- **Relax the check** so a conformance test may import core / builtins
  (per `pkg-layout-spec.md` — importing the always-bundled core is part
  of the language contract, not a stdlib dependency). Scope the
  relaxation precisely to what the spec sanctions; don't open it to
  arbitrary stdlib imports in the language conformance set.
- **Add a separate stdlib conformance suite** (e.g. `conformance/stdlib/*`)
  with its own runner wiring, so stdlib packages (`os` first) get
  end-to-end coverage across modes without polluting the language
  conformance set.
- Follow-up to landing `pkg/std/os` (binate `3ca36c82`), which shipped
  with libc unit tests only — conformance was deferred here per the user.

### Two generics v1-restrictions not enforced — spec Ch.12 (2026-06-12)
Found authoring spec Ch.12 (verified via toolchain probes through
builder-comp). Both MAJOR (the spec implies these are enforced; they
aren't) but neither miscompiles.
- **Generic methods accepted at declaration** (`gen.no-generic-methods.unenforced`).
  `func (b Box) Get[T any](x T) T {...}` compiles clean: `parse_func.bn:34-37`
  reads the type-param list whether or not a receiver is present, and
  `check_decl_func.bn:122-127` / `resolve_type.bn:201-242` type-check the body
  with `T` in scope. It only fails (confusingly: "cannot index this type") at a
  call site `b.Get[int](42)`, because `b.Get` is a selector so `[int]` is parsed
  as indexing. Should be rejected at collection time (`DECL_FUNC` with
  `Recv != nil && len(TypeParams) > 0`). IR-gen even documents the unenforced
  assumption (`gen_register_import.bn:99-102`).
- **Constraint satisfaction unchecked for generic struct/interface instantiation**
  (`gen.satisfy.struct-iface-unchecked`). `typeSatisfiesConstraint`/
  `reportConstraintMiss` are called ONLY from `instantiateGenericFunc`
  (`check_generic.bn:259-264`); `buildInstantiatedStruct` (:196-218) and
  `buildInstantiatedInterface` (:115-138) install the type-param scope but make
  NO satisfaction call. So `type Box[T lang.Orderable] struct{val T}`
  instantiated as `Box[NoOrder]` (no `impl NoOrder : Orderable`) compiles clean.
  Generic-FUNCTION constraint checking works correctly.

### Value-receiver "always readonly" not enforced — spec Ch.10 (2026-06-12)
MINOR (design-intent vs impl; no correctness bug — by-value copy makes any
mutation harmless). `claude-notes.md:359` says a value receiver `(r T)` is
"always readonly". The checker does NOT enforce it: `receiverShape`
(`check_method.bn:251-285`) classifies a plain `(r T)` as kind 0 with
`isObjectConst=false`, and no pass rejects `r.field = ...` in the body — the
mutation just modifies the discarded copy. Decide: enforce read-only on value
receivers (a checker addition + a diagnostic), or downgrade the design note to
"the receiver is a copy; mutations are local" (the implemented semantics, which
the spec `func.method.value-recv` currently describes). Referenced from
`10-functions-methods-function-values.md`.

### Layout follow-ups surfaced authoring spec Ch.7.13 (Type Layout) — 2026-06-12
Both referenced from the spec (`07b-type-layout.md`).
- **`type.layout.funcval-order-hardening`** (hardening). The function-value
  field order `{vtable, data}` and the interface-value order `{data, vtable}`
  (the deliberate, verified ABI asymmetry) are encoded as fixed/magic indices
  in codegen + IR (`emit_instr.bn`, `emit_funcvals.bn`, `emit_iface_call.bn`,
  `ir_ops_flow.bn`) rather than as shared named-offset helpers in
  `pkg/binate/types` (unlike `SliceDataOffset`/`MSliceBackingOffset`/
  `ManagedRefcountOffset`, which ARE shared helpers). The VM and codegen agree
  by convention, not a single shared definition — a divergence risk for the
  keystone cross-mode contract. Harden the func/iface field orders into shared
  named-offset constants in `pkg/binate/types`.
- **`type.layout.byte-order`** (open decision). `TargetInfo` (`types.bni:374-378`)
  carries no endianness field, so byte order is target-defined but unconstrained
  by the layout layer (observable via `bit_cast` and the representation builtins).
  Decide whether to pin endianness as implementation-defined and add a
  `TargetInfo` endianness field so layout-dependent constant emission is
  well-defined. (Also noted in `plan-language-spec.md` §21/§9.)

### `cast` is unchecked at the type layer; literal fit-check not enforced — spec Ch.8 (2026-06-12)
MINOR (a permissiveness / missing-diagnostic question). `claude-notes.md:483`
says `cast(uint, -1)` is a compile error (literal doesn't fit). The
type-checker does NOT enforce this: `check_builtin.bn:48-54` resolves the
target type, checks the argument for well-formedness, and returns the target
type **unconditionally** — no convertibility check, no constant fit-check
(`bit_cast` likewise, :56-62). IR-gen (`gen_expr.bn`) only re-widens the
literal. So `cast(uint, -1)` is accepted. (The fit-check on a plain assignment
— `var x uint8 = 256` → error — IS enforced, via `untypedIntLitFitsTarget`;
that path is separate.) Decide: add a fit-check (and/or convertibility check)
to `cast`, or update `claude-notes.md:483`. Tracked as `conv.cast.literal-fit`
in the spec (`08-conversions.md`).

### Type-system issues surfaced while authoring spec Ch.7 (Types) — 2026-06-12
Found writing the docs spec's Types chapter (grounding + adversarial
verification against pkg/binate/types). The spec (`07-types.md`)
documents these as open items.
- **`@[N]T` parser leniency** (`type.ptr.array-parens.at-leniency`, MINOR).
  The documented rule is that bare `@[`/`*[` followed by a non-`]` is a
  syntax error (parens required: `@([N]T)` / `*([N]T)`), so the `@[`/`*[`
  sugar is unambiguously slices. The parser enforces this for `*[N]T`
  (`parse_type.bn:49-52` emits an error) but **silently accepts `@[N]T`**
  as `@([N]T)` (`parse_type.bn:98-112`, no error). Asymmetric; likely
  unintended. Decide: reject bare `@[N]T` too, or relax both. No miscompile.
- **Opaque `make`/`sizeof`/`alignof` not gated**
  (`type.opaque.make-sizeof-gap`, MAJOR doc-vs-impl). The ratified design
  (plan-type-decls.md:42-51, ast.bni:232-233) says `make(Opaque)` /
  `sizeof(Opaque)` / `alignof(Opaque)` must be rejected outside the
  defining package (layout unknown). The checker enforces ONLY field
  access (`check_expr_access.bn:306`); `check_builtin.bn:17-22,144-155`
  accept make/sizeof/alignof on a nil-Underlying named type with no opaque
  gate, so the failure (if any) is a downstream layout/codegen error, not a
  clean diagnostic. Decide: add the opaque gate (per the ratified design),
  or update the design docs.
- **Named func-value LITERAL construction unimplemented** (gap). A func
  *reference* constructs a named `@func` type fine, but a func *literal*
  into a named func-value type is rejected in ALL modes
  (`conformance/regressions/named-func-value-construct-literal` xfailed
  everywhere; checkFuncLit must return the named type when hinted and peel
  TYP_NAMED at isManagedFuncValueLit). Value-rejection and reference
  construction both work.

### Lexer issues surfaced while authoring spec Ch.5 (Lexical Elements) — 2026-06-08
Found writing the docs spec's Lexical Elements chapter (adversarial
verification of the draft against `pkg/binate/lexer`). All MINOR
(confusing errors / silent leniency, not silent miscompile). Tests +
xfails pending a coordinated `binate` worktree. The spec documents these
as open items (`lex.literal.int.leading-zero`, `lex.escape.unsupported`).
- **`0123` / `00` split into two integer tokens.** — ✅ RESOLVED 2026-06-12 (binate `82e86216`; reject as a clean single ILLEGAL token per grammar `decimal_lit = "1".."9" {digit} | "0"` — no C-style leading-zero octal/decimal). `scanNumber` now consumes the digit run after a leading `0` so the numeral is ONE token, upgrading to FLOAT if `.`/`eE` follows (`0123.5` stays valid) else reporting ILLEGAL; `0` and `0.5` unaffected. Unit tests in `scan_test.bn`. `lexer/scan.bn:84`
  `scanNumber`'s leading-`0` branch consumes only the `0` then falls to
  the float-tail **without a digit-consuming loop** (unlike the non-zero
  `else` branch). So `0123` lexes as `INT("0")` then `INT("123")`, and
  `00` as two `INT("0")`. A multi-digit numeral with a leading `0` and no
  base prefix should be a single literal or a diagnostic, not a split.
  Yields a confusing downstream parse error. UNCOVERED by conformance.
- **Unknown escapes silently dropped.** `ir/gen_util_literals.bn`
  `unescapeStr`/`parseCharLit` decode only `\n \r \t \\ \' \" \0 \xHH`;
  any other `\X` falls through to a verbatim `X` (backslash dropped) with
  no diagnostic — so `"\a"` decodes to `"a"`. Decide whether unknown
  escapes should be rejected.
- **`\uHHHH` documented but unimplemented.** `claude-notes.md` and
  `grammar.ebnf` list a `\uHHHH` escape, but the decoder has **no `\u`
  case** (it would emit `u` followed by the hex digits). Either implement
  `\u` (and decide the >0xFF-into-single-byte-`char` question) or drop it
  from the notes/grammar. The spec currently omits `\u` to match the
  implementation.

### Untyped `const` coercion: implementation diverges from a DECIDED note — surfaced authoring spec Ch.6 (2026-06-11)
Needs a decision (MINOR — no miscompile; a type-system permissiveness
question).
- **The note (`claude-notes.md` "Type conversions & literals — DECIDED",
  ~line 444)**: untyped-literal coercion "does NOT extend to named
  constants — only literals." (A deliberate divergence from Go.)
- **The implementation does the opposite.** An untyped `const X = <expr>`
  (no explicit type) carries `TYP_UNTYPED_INT` (with `HasLitVal`) and
  **coerces / narrows at each use, exactly like a literal**:
  `check_const.bn:91-102` (no-`TypeRef` branch defines the name with the
  untyped `valType`), `check_expr.bn:185` (`checkIdent` returns it),
  fit-checked at the use site like a literal. Tests confirm:
  `check_const_test.bn:160-167` (`const A = 1+2` → assignable to `int`),
  `:210-217` (`const A = 200+100` → rejected against `uint8` because 300
  doesn't fit — pure literal-coercion behavior), and
  `check_expr_constfold_test.bn:181-204` whose comment says "the bare
  members stay untyped and **narrow freely at the use site**." Only a
  `const X <type> = …` (explicit type) gets a definite, non-coercing type.
- **Decision**: either (a) enforce the note — give an untyped `const` name
  a definite default type that does not coerce (the Go-divergent design),
  or (b) accept the implemented Go-like behavior and update
  `claude-notes.md:444`. The spec (docs `06-constants.md`,
  `const.untyped.coercion`) currently describes the **implemented**
  behavior and flags this as an open item.

### Extend hygiene checks to scan `ifaces/` and `impls/` (not just `pkg/`+`cmd/`) — ✅ DONE (sub-todo: .bni cap)
- **Goal (user-requested, 2026-06-10)**: `line-length`, `file-length`,
  `bni-doc`, `bn-doc`, `naming` find-roots were `$BINATE_DIR/pkg` (+`cmd`)
  only, so source under `ifaces/`+`impls/` wasn't linted (surfaced by
  `ifaces/targets/**/build.bni`, `a3755cb4`; `file-format` already covers the
  whole tree).  Extend each to also scan `ifaces/`+`impls/`.
- **Approach (user, 2026-06-10)**: extending surfaces ~150 PRE-EXISTING
  violations, almost all in ported stdlib (math/strconv/os, never linted under
  `impls/`).  Do it **one check at a time**: land the backlog fixes for a check
  and enable that check alongside (fix + enable as separate commits, landed
  together).  Triage, never mass-suppress.
- **Status**:
  - ✅ **file-length** — enabled (binate `a8c37bdf`); `.bn` keeps 500/600, `.bni`
    gets a higher 1500/1800 cap (interfaces can't be split like impls).  No
    backlog (largest `.bni` is ir.bni ~1159 < 1500).
  - ✅ **naming** — enabled (binate `4c79b2d1`+`79ca70f2`).  The 9 lowercase-in-.bni
    (`bootstrap.format*` 5 + `rt._call_*` 4) were already whitelisted, but under
    pre-move `pkg/...` paths; repointed to `ifaces/core/...` (latent bug: the
    whitelist would've silently stopped matching once naming scanned ifaces/).
  - ✅ **bni-doc** — enabled (binate `a0a82aa4`+`812c9dd1`).  Added the missing
    package doc to `ifaces/core/pkg/builtins/reflect.bni` (its block documented
    `type Package`, not the package).
  - ✅ **line-length** — enabled (binate `beff4c89`+`2281cabd`).  Wrapped 128
    long lines across 20 stdlib math/strconv files (all wrappable — no
    LONG-LINE-ALLOWED needed); semantics-preserving (numeric-token multiset
    identical per file; math+strconv unit tests green).  Follow-up that the
    wrapping forced: bessel01.bn grew 407→502 (file-length soft-WARN), so its
    asymptotic machinery (pzero/qzero/pone/qone + tables) was split into
    `bessel01_asymp.bn` (binate `4c31ba50`); both files now <300 lines.
  - ✅ **bn-doc** — enabled (binate `56784a86`+`705f4928`).  Fixed all 118: erf
    (4) + gamma (1) coefficient blocks const-grouped (existing section comment →
    group doc); 37 lookup-table vars (bessel01_asymp R/S tables, cosTab,
    Stdout/Stderr, …) + 23 funcs (@Nat methods, os Read/Write/…, Shl/Shr, …)
    documented individually.  Semantics-preserving (numeric-token sequence
    byte-identical per file; math+strconv tests green; os/rt edits comment-only).
- **DONE** 2026-06-10: all five file checks (file-length, bni-doc, naming,
  line-length, bn-doc) now scan `ifaces/`+`impls/`.  ~150 pre-existing stdlib
  violations were triaged + fixed (not suppressed), one check at a time.
- **Sub-TODO (file-length .bni cap)**: consider lowering the `.bni` cap from
  1500/1800 toward 1000/1200; `ir.bni` (~1159) would need refactoring (split
  into sub-interfaces) first.
- **Discovery**: adversarial verification workflow over `a3755cb4`; user asked
  for the extension as a follow-up.

### Wire `--version` into bnc / bni / bnas / bnlint — next-release follow-up
- **Goal**: each tool accepts `--version` and prints its display version
  (`<tool>-` + `version.Version`, e.g. `bnc-0.0.7-pre`) to stdout, then
  exits 0.  Single source of truth is `version.Version` (the repo-root
  `VERSION` file, minus its `bnc-` builder prefix).
- **Why deferred (user, 2026-06-03)**: `cmd/bnc` is the only
  BUILDER-compiled tool, and reading `version.Version` cross-package is
  the extern-var-read feature (`be49c0a9`) — plus pulling the `version`
  package into bnc's tree needs BUILDER to parse the `var Version`
  declaration in `version.bni` (the `bni_scope` `DECL_VAR` support).
  Neither is in `bnc-0.0.6` (confirmed: `be49c0a9` is not in the 0.0.6
  tree).  So bnc can't consume `version.Version` until `BUILDER_VERSION`
  is bumped to a snapshot that includes the extern-var landing.
  `bni`/`bnas`/`bnlint` are built BY bnc (full language) and COULD be
  wired today, but the user chose to defer all four together so they
  land consistently after the next BUILDER bump.
- **When**: the next release / BUILDER bump (same gate as the bnlint
  dep-body deployment and the `vm` lint-skip removal).  After the bump,
  BUILDER understands extern vars, so all four can
  `import "pkg/binate/version"` and read `version.Version`.
- **Implementation sketch**: in each tool's `main()` arg handling,
  detect `--version` before the rest of parsing, build `<tool>-` +
  `version.Version` via `buf.Concat`, print + newline to stdout, exit 0.
  Each tool already imports `buf`; add `import "pkg/binate/version"`.
- **Also update**: `release-process.md` step-4 smoke test (currently
  notes "`bin/bnc` doesn't accept a `--version` flag") — once wired, the
  release can confirm-by-banner instead of confirm-by-behavior.
- **Discovery**: 2026-06-03, after landing the version redesign
  (`b745c877`); user requested `--version` on all four tools.

---

## MAJOR

### MAJOR PROJECT — unify module-level static data into one IR representation (`ir.DataGlobal`) + one per-backend emitter — FILED 2026-06-10 (needs design + planning + phased migration)
- **The smell**: module-level constant data is currently modeled and emitted **per kind**, each with its own IR rep + its own LLVM emitter + its own native emitter: `mod.Strings` (string consts), `mod.Globals` (`var` storage), `mod.Impls` (impl vtables), func-value vtables/handles (derived from `mod.Funcs`), and the package descriptor `_Package` (worst case: LLVM-text-only, no IR rep, no native emitter). That's ~5 kinds × 2 backends ≈ 10 emitters for ONE concept — *a named, module-level constant blob the backend lays into a data section.* The proliferation is what let `_Package` ship with only its LLVM half written (see the native-`_Package` link bug below) — the LLVM-only-divergence bug class is structural to this design.
- **The unification**: one IR concept `ir.DataGlobal { Name; Linkage (private|weak_odr|linkonce_odr|external); Align; Init }` where `Init` is a sequence of terms: `bytes` | `int(width)` | **`symref(symbol, +offset)`** (pointer to another symbol). The `symref` term is the one expressive thing today's `ir.Global.Init` (a single int-only `@Instr`) lacks, and it's what every interesting blob needs. Then ONE `emitDataGlobal` per backend (lay bytes + apply relocations + linkage/align) replaces all the per-kind emitters. Mappings: string → `bytes`; var → `int/zero`; `_Package` → `int(RC),int(0),symref(_pkgname),int(len)` (the static-managed node, no special primitive); impl/func-value vtable → `[symref(dtor),symref(m0),…]`. Both backends walk one path → LLVM-only divergence becomes impossible. Consonant with `ir-backend-guidelines.md` ("string constant collection belongs in a shared layer") — this is the shared *static-data manifest* backends lower.
- **What stays / what resists (design must handle)**: (1) func-value `__shim`s are CODE → stay in `mod.Funcs`; only the symref *table* is data. (2) impl vtables carry **per-arch layout** + `weak_odr`/`linkonce` linkage + alignment — the model must carry linkage/align and backends keep arch layout knowledge. (3) **string interning/dedup** (`FinalizeStrings`) is a real optimization to preserve, not regress to one-global-per-occurrence. (4) `mod.Globals` carries **front-end semantics** (extern vars, qualified-name resolution, `IsExtern` external-decl emission) — the front-end layer maps onto `DataGlobal`, isn't replaced by it.
- **Payoff**: kills the LLVM-only-divergence bug class structurally; ~10 emitters → ~2; new static-data needs get both backends for free. **Cost/risk**: real IR + dual-backend refactor of *currently-working* code; non-trivial regression surface; per-kind quirks above. This is a project, not a bug fix — needs a `plan-*.md` (design the `Init`/relocation model + linkage/align + interning; phased migration).
- **Suggested migration order**: introduce `ir.DataGlobal` + one `emitDataGlobal` per backend → migrate `_Package` onto it FIRST (the proving case; also retires the interim native emitter below) → then impl + func-value vtables → then strings → then globals (front-end-coupled, last). Each step keeps all backends green.
- **Interim DONE**: the short-term native `emitPackageDescriptor` is LANDED (binate `f7d116f3`) — `common.EmitPackageDescriptorData` (shared static-managed-node layout) + a per-arch accessor. Explicitly throwaway: the `_Package` migration step of this project deletes it (and `codegen/emit_pkg_descriptor.bn`) once the descriptor is an `ir.DataGlobal`.
- **Low-priority hardening surfaced by the interim's adversarial review (not reachable today)**: the native interim `SetGlobal`s `_pkg_info` + `_pkgname` as STRONG symbols, vs LLVM's `weak_odr` (`_pkg_info`) / `private` (`_pkgname`). NOT a current bug — in `--backend native` only `main` is native and all deps go via LLVM (disjoint package names), so the same package's strong native `_pkg_info` never lands in two objects; conformance/532 + the native vm/repl/bni unit links are clean. It WOULD bite a future native-library-packaging path (a precompiled native `.o` for a package linked beside a from-source native recompile of it → duplicate strong symbol where `weak_odr` dedupes). Cheap fix when that lands (or sooner): `a.SetWeak` on `_pkg_info` (matches `weak_odr`); `_pkgname` only needs same-object visibility (sole consumer is the same-object `Name.data` fixup) so it can be local/weak. The `ir.DataGlobal` unification should carry a linkage field so this is expressed once. (`_pkg_info` must stay a defined symbol the accessor's cross-section reloc can target — the native Adrp/Lea fixup resolves to it like `emitGlobalAddr` — so not an unnamed local.)

### Add a hygiene check enforcing package-tier dependency rules (`pkg-layout-spec.md`) — bundled tiers must not import non-bundled tiers — FILED 2026-06-10
- **What**: a `scripts/hygiene/` check that statically validates every package's import closure against the tier ordering in `pkg-layout-spec.md` ("Tiers"). A package must not import a *less-bundled* (higher-numbered) tier. Concretely — tier 0/0b/1/1x packages (always- or by-default-bundled: `pkg/builtins/*`, `pkg/std/*`, `pkg/stdx/*`) must NOT import a tier-2/3 package (project-pulled / not bundled: `pkg/binate/*` and any other `pkg/<org>/*`). Also enforce the tier-2 transitive-closure rule (`pkg-layout-spec.md` "Tiers": tier 2's dependency closure must itself be tier 2). Tier is derivable from the import-path prefix (`pkg/builtins/`→0/0b, `pkg/std/`→1, `pkg/stdx/`→1x, `pkg/binate/` & other `pkg/<org>/`→2); `pkg/bootstrap` is a bundled runtime primitive (treat as tier-0-equivalent). EXEMPT `*_test.bn` — tests aren't bundled (e.g. `lang_test.bn` legitimately imports `pkg/binate/buf`).
- **Why**: a bundled package whose dependency closure escapes the bundled tiers silently breaks the bundle — the dependency's source isn't shipped, so a consumer compiling against the bundle gets `package "<dep>" not found`. NOTHING currently catches this: it only manifests when a consumer compiles the offending package from a real bundle (`make-bundle.sh` output), which no CI / hygiene / conformance step does today.
- **Motivating bug (discovery 2026-06-10, release-prep for `bnc-0.0.8`)**: `pkg/builtins/lang` (tier 0, always bundled) imported `pkg/binate/buf` (tier 2) for two `buf.CopyStr("true"/"false")` calls in `bool.String()`. The bundle ships only `lib/pkg/bootstrap`, not `pkg/binate/buf`, so the tier-0 `Stringer` carve-out (`var s *lang.Stringer = &x; s.String()`) failed to compile from ANY bundle with `package "pkg/binate/buf" not found` — present since `bnc-0.0.7`, undetected because the carve-out smoke step (`release-process.md` step 5) had never actually been run against a real bundle. Fixed in binate `84818a77` (lang returns bare string literals; `[N]readonly char → @[]char` is a literal-init allocate+copy). This check would have caught it at the `import` line.
- **Scope note**: adding the check ≠ wiring it into `scripts/hygiene/run.sh` / CI — but a hygiene check belongs in the run.sh master, so do both when implementing. A first audit may surface other pre-existing violations to triage.
- **First manual sweep (Lane C, 2026-06-10) — CLEAN baseline**: swept every import (incl. aliased) in the bundled trees (`ifaces/{core,stdlib}`, `impls/{core,stdlib}`, `pkg/bootstrap`, `runtime/`). No non-test bundled package imports outside the bundled set. Two non-obvious cases the eventual check must handle: (1) `impls/core/baremetal/pkg/builtins/rt` imports `pkg/semihost`, which is NOT a violation — `pkg/semihost.bni` ships under `runtime/baremetal_arm32/` (a bundled runtime component) and resolves under the arm32-baremetal build's own `-I`/`-L`; the check should treat shipped `runtime/<target>/pkg/*` as bundled, or scope tier rules per build target. (2) all `pkg/builtins/testing` imports are in `*_test.bn` (already EXEMPT) and it has a bundled `.bni` with a harness-provided impl. So `lang → pkg/binate/buf` (binate `84818a77`) was the only true tier-0→tier-2 violation; the baseline is otherwise clean.

### Stale `native_x64` (ELF) iface-multi-return xfails — REMOVED (binate `10798d42`) — 2026-06-10 (Lane B)
- **What**: the 16 markers `conformance/matrix/abi/iface-multi-return{,-assign}/{int,u16}/{2,3,4,5}.xfail.builder-comp_native_x64-comp_native_x64` blamed "iface dispatch multi-return: native tuple-packing not yet implemented". That packing **IS implemented** (`pkg/binate/native/x64/x64_iface.bn` routes `OP_CALL_IFACE_METHOD` multi-returns through `collectMultiReturnTuple`), and the **identical-codegen** `builder-comp_native_x64_darwin` (Mach-O; same `pkg/binate/native/x64` backend, only object format differs) **PASSES all of these cells** (Lane B run 2026-06-10, and already noted in `03b80566`). ELF also passes the un-xfailed `multi-return` / `funcval-multi-return` / iface `f64` / `iface-param` / `iface-return` cells, so iface dispatch and multi-return both work there — these int/u16 markers were the lone stale holdouts.
- **Removed** on the x64-darwin evidence (user-authorized 2026-06-10). The ELF mode isn't locally runnable on macOS/arm64 (no `qemu-x86_64`), so **CI is the confirmation point**: it runs ELF natively on the x86-64 ubuntu runner and will exercise these 16 cells once Lane A's `-comp*` link break clears. Expected green; **treat any ELF failure as a real x64-ELF-specific bug to fix (not a re-xfail).** (arm32 iface-multi-return xfails left in place — different, less-complete backend.)

- **REMAINING — x64 float32 cross-package native↔LLVM ABI mismatch (tracked, NOT a regression):** an adversarial review of the float64 commit found that a sub-8-byte float (float32) multi-return component COALESCES into a shared eightbyte on SysV-AMD64 — `(float32,float32)` → one SSE eightbyte (XMM0), `(float32,int32)` → one INTEGER eightbyte (RAX). The native x64 pack/collect (`multiReturnEightbyteIsSSE`-driven, self-consistent) still disagree with LLVM's actual x64 float32 ABI, so cross-package float32 reads garbage / faults. aa64 is correct (each float gets its own D register). `conformance/684_cross_pkg_mr_f32` pins this **xfailed on native x64** (passes aa64/LLVM/VM). float32 multi-return was always broken (the integer-only path); this surfaced it. **Fix direction:** dump LLVM's actual register usage for an x64 float32 multi-return (the `F32F32`/`F32I32` `.ll`/asm), then align the native x64 pack/collect — the per-eightbyte `emitMultiReturnPack` is the groundwork. The aa64 per-field scheme is already correct, so this is x64-only.
- **CORRECTED ROOT CAUSE — empirically dumped 2026-06-10 (the bullet above had it BACKWARDS), and the bug is BROADER than float32:** our LLVM backend emits LITERAL struct return types (`{float,float}`, `{float,i32}`, `{i16,i16,i16}`, `{i32,i32}`, …) and LLVM lowers a first-class IR aggregate return **purely FIELD-PER-REGISTER, with NO SysV eightbyte coalescing** — confirmed by lowering hand-written `.ll` with `clang -S --target=x86_64-*` (Darwin == Linux): `{float,float}`→XMM0,**XMM1**; `{float,i32}`→XMM0,**EAX**; `{i16,i16,i16}`→AX,DX,**CX**; `{i32,i32}`→EAX,**EDX**; `{i64,double}`→RAX,XMM0. So the native x64 **eightbyte-coalescing** model (`multiReturnEightbyteIsSSE`, packs `(i32,i32)`/`(f32,f32)` into ONE register) is the WRONG model for native↔LLVM agreement: it only COINCIDES with LLVM when every field is a full 8 bytes (`(int,f64)`/`(f64,f64)` — why 683 is green). It DIVERGES for **every sub-8-byte field** (`(f32,f32)`, `(f32,i32)`, `(u16,u16)`, `(i32,i32)`, …) crossing the native↔LLVM (hybrid: native main + LLVM dep) boundary → silent garbage. **The abi matrix never caught this because its multi-return cells are SAME-MODULE** (`package "main"`, callee inline → native↔native self-consistent), so only the cross-package 683/684 exercise the boundary. **aa64 is already correct because it does FIELD-PER-REGISTER** (each leaf → next reg of its class), matching LLVM (684 green on aa64). **FIX = replace the x64 eightbyte-coalescing pack/collect with FIELD-PER-REGISTER-BY-CLASS** (int leaves → RAX,RDX,RCX,… ; float leaves → XMM0,XMM1,… ; store/load at the field's offset), mirroring aa64 + LLVM's literal-struct lowering. NOT a float32 patch and NOT codegen coercion (emitting `<2 x float>`/`i64` would fix x64 but BREAK aa64, since one target-independent IR type can't express both targets' ABIs — clang lowers `<2 x float>` to V0-packed on aa64, which aa64's per-field collect would then mis-read). Need to confirm LLVM's exact GP/FP return-reg sequence + the >N-register sret threshold before implementing. **Surfaced to user as a major finding + design reversal (the b5911fbe eightbyte choice) — user APPROVED the field-per-register rework (2026-06-10).**
- **EXACT LLVM x64 first-class-struct return CC (empirically probed via `clang -S` on hand-written `.ll` with CALLERS that read each field — the definitive register map):** GP-class leaves → **RAX, RDX, RCX** (3 regs; `{i64,i64,i64}` is IN-REGISTER with field 2 in RCX); 4+ GP-words → **sret**. FP-class leaves → **XMM0, XMM1** (2 regs); a 3rd/4th float64 spills to **x87 ST0/ST1** (NOT sret, NOT XMM — `{double,double,double}`/`{...,double}` read the field via `fstpl`); 5+ floats → sret. INTEGER and FP counters are INDEPENDENT and there is **no eightbyte coalescing**. So x64's sret threshold is **register-count-based** (gpWords>3 OR fpCount>2-ish), NOT the 16-byte rule — `{i64,i64,i64}` is 24 bytes yet in-register.
- **BOUNDED FIX PLAN (delivers the greenlit scope + fixes the whole sub-8-byte class):** x64 `emitMultiReturnPack` + `collectMultiReturnTuple` → field-per-register-by-class: a non-float field's words → RAX,RDX,RCX (retGp); a float-scalar field → XMM0,XMM1 (retFp); each stored/loaded at its field offset (mirror of aa64 `collectMultiReturnFields`). Delete `multiReturnEightbyteIsSSE`. x64 sret decision (currently the shared 16-byte `CallReturnsBigMultiReturn`) → an **x64-specific** register-count rule (gpWords>3 OR fpCount>2 → sret), so `{i64,i64,i64}` stays in-register matching LLVM while the same-module abi-matrix (int/3 etc.) stays green (native↔native self-consistent). Keep aa64 on its 16-byte rule (unchanged). Un-xfail 684; add cross-package coverage for `(u16,u16)`/`(i32,i32)`. Verify 683/684 + abi matrix green on aa64 + x64-darwin.
- **LANDED — binate `47ebdbac` (2026-06-10).** x64 multi-return pack/collect are now field-per-register-by-class (RAX,RDX,RCX / XMM0,XMM1 at each field offset); the multi-return sret threshold is target-aware (`CallConv.MultiReturnTupleNeedsSret`, exported): SysV register-count (>3 GP-words / >2 FP-fields), AAPCS64 unchanged (SizeOf>16). `multiReturnEightbyteIsSSE` deleted; the x64 funcval sret classifier `isBigMultiReturn_x64` (from `f0747762`) was reconciled onto the same shared threshold (same-area concurrent commit — its size>16 rule disagreed for `(i64,i64,i64)` funcvals). Conformance 684 un-xfailed both x64 modes; new 693 (`(i32,i32)`,`(u16,u16,u16)`,`(i32,i32,i32)`) added. Verified: 683/684/693 + full abi MR matrix + `funcval-big-multi-return-args` green on aa64 + x64-darwin; unit + hygiene green.
- **SIDE-EFFECT — 526 (`strconv_parse_cross_pkg`, managed-iface multi-return) now PASSES on x64, still FAILS on aa64.** My fix resolved 526 on x64-darwin (its `(int,@errors.Error)` = 3 GP-word multi-return was mis-collected by the eightbyte scheme); `0d29a4b5`'s `builder-comp_native_x64{,_darwin}` xfails for 526 are now STALE → **REMOVED (binate `f895848b`, 526 un-xfailed + verified green on x64-darwin)**. 526 still fails on aa64 (a separate aa64-specific managed-iface-multi-return bug, NOT fixed by this x64-only change) → keep the aa64 xfail; likely related to residual gap (2) below or an iface-value-in-multi-return refcount issue. Track as an aa64 follow-up.
- **RESIDUAL GAPS (loud follow-ups, NOT silently deferred):** (1) **x87 cross-package — ✅ RESOLVED 2026-06-11 (`50850315`).** A multi-return with >2 FLOAT fields crossing native↔LLVM diverged on x64: LLVM x86_64 returns the 3rd/4th float in x87 ST0/ST1 (empirically dumped via clang + a Rosetta run: `{f64,f64,f64}`→XMM0,XMM1,ST0; `{f64×4}`→XMM0,XMM1,ST0,ST1; `{f32,f32,f32}`→XMM0,XMM1,ST0 via FLDS; `{i64,f64,f64}`→RAX,XMM0,XMM1, no x87 — GP/FP counters independent, no eightbyte coalescing; field N→ST0, N+1→ST1), while native x64 sret'd at `fpCount>2`. Pinned by `conformance/698_cross_pkg_mr_float3`. **Option B (force-sret in pkg/binate/codegen) was ATTEMPTED 2026-06-11 and REVERTED** — a codegen sret attribute affects ALL call paths, breaking the LLVM func-value + iface multi-return shims (`abi/{funcval,iface}-multi-return*/f64/{3,4,5}`). **Option A (chosen, localized to native x64, no codegen change):** (a) added FLDS/FLDL (D9/DD /0) + FSTPS/FSTPL (D9/DD /3) to `pkg/binate/asm/x64` (byte-exact vs clang); (b) CallConv gained `NumX87RetRegs` (SysV 2, AAPCS64 0) and the shared sret threshold is now `fpCount > NumFpRetRegs + NumX87RetRegs` — x64 register-returns up to 4 floats while aa64 reduces to the identical `fpCount>8` (untouched); (c) `emitMultiReturnPack` (`x64_return.bn`) pushes overflow floats in REVERSE field order (so field N lands on ST0) and `collectMultiReturnTuple` (`x64_call.bn`) pops ST0-first to the result slot — the spill-everything frame policy collects every MR call so the x87 stack stays balanced even for a discarded result. Un-xfailed 698 on native x64 (darwin + linux; the linux lane is CI's native-x86_64 runner, where 698's 2-float sibling 683 already passes). New `715_x87_mr` covers float32 x87 (FLDS/FSTPS), mixed int+float x87, and a stack-balance stress loop — green on native x64 / aa64 / LLVM / VM. Full x64-darwin suite 1376 passed / 0 failed; abi funcval/iface MR matrix green (no Option B blast radius); aa64 unaffected. (2) **aa64 SAME threshold bug — ✅ RESOLVED (`d206635d` 2026-06-11).** aa64 native used the 16-byte rule and sret'd any 17..64-byte tuple while LLVM register-returns up to 8 GP (X0..X7) + 8 FP (D0..D7); `MultiReturnTupleNeedsSret` now uses the per-target register-count rule (aa64 8/8). This was hit in practice by 526's `(int64, @errors.Error)` (3 GP words) — see the dedicated 526 entry. Float-HFA ≥3-component cross-pkg on aa64 is now register-returned too (D0,D1,D2..), though a dedicated cross-pkg FP-≥3 cell isn't added (the x64 sibling of that shape is the open x87 gap (1), so such a cell would need an x64 xfail). (3) **aggregate FIELDS inside a multi-return** — LLVM flattens; keep current behavior / sret, don't regress.
- **Symptom (direction)**: a multi-return tuple with a FLOAT component (`(int, f64)`, `(f64, f64)`) — the native callee pack (aa64 `aarch64_dispatch.bn:354-385` OP_RETURN multi-return loop; x64 `emitMultiReturnPack` `x64_return.bn:159-201`) has only two arms (aggregate / else-scalar→X-or-RAX/RDX), with NO `IsFloatScalarTyp` branch and no HFA/SSE eightbyte classification (only the LONE-single-scalar-float early return is float-aware). So a float field is packed into an INTEGER register, and the native caller collect reads it from an integer register — native↔native self-consistent, but DIVERGENT from AAPCS64 / SysV-AMD64 + LLVM, which return a float eightbyte in D0/XMM0 (or an SSE-classified aggregate eightbyte in an FP reg). cmd/bnc compiles only the main module natively and routes cross-package callees through LLVM/clang, so a float-component multi-return crossing the native↔LLVM boundary (e.g. an impl method or multi-return func defined in a non-main, LLVM-compiled package) reads the float field from the WRONG register class → silent garbage. Now reachable for iface dispatch too (post-SEAM); still ZERO coverage (abi matrix is int/u16 only).
- **Severity**: MAJOR — silent wrong value at the native↔LLVM ABI boundary on a type-valid shape; narrow trigger (float-component multi-return crossing the boundary) but real and untested.
- **Fix direction**: add `IsFloatScalarTyp` handling (and HFA/SSE eightbyte classification) to the native multi-return callee pack + caller collect on both arches, matching AAPCS64 / SysV-AMD64 + the LLVM legalization. Extend `gen-abi-matrix.py`'s type axis with `f64` for multi-return / iface-multi-return / funcval-multi-return — decisive shapes `(f64,f64)` (HFA on aa64) and `(int,f64)` (mixed INTEGER+SSE eightbytes on x64).
- **Discovery**: 2026-06-08, adversarial review of plan-cr2-3 — the iface-classifier (`cc2ddcc4`) made a float-component iface multi-return reachable; the underlying native multi-return pack was never float-aware. Filed (not fixed) per user decision.

### `==` / `!=` (and relational) on aggregates: checker now rejects — no more invalid LLVM. DECIDED + LANDED at the checker (binate `60719e01`, coverage `78af9c23`); struct/array impl + generic path remain OPEN
- **What it was**: the comparison type-check rule only checked mutual assignability and returned bool, so `==`/`!=`/`<`/`>`/`<=`/`>=` were accepted on *any* same-typed operands. For aggregates (raw/managed slice, raw/managed func value, interface value, struct, array) codegen then emitted `icmp` on a multi-word value → invalid LLVM (`error: icmp requires integer operands`), hard package compile failure.
- **DECIDED (user, 2026-06-07)** and **LANDED** in `pkg/binate/types` (binate `60719e01`; coverage `78af9c23`):
  - **Equality (`==`/`!=`)**: scalars + pointers compare directly. **Slices, interface values, func values → permanently rejected** with a type-specific diagnostic (consistent with `slice == nil` / `iface == nil` already being disallowed footguns; the sanctioned tests are `len()` / `present()` / identity). **Structs and arrays → "not yet implemented"** (comparable in principle; the fieldwise/elementwise lowering is deferred — arrays in the same bucket as structs, per user). `nil` is judged by the other operand (`ptr == nil` OK; `iface == nil` / `func == nil` rejected).
  - **Relational (`<`/`>`/`<=`/`>=`)**: numeric operands only — ordering is undefined for pointers (claude-notes.md:898) and every aggregate (folds in the same invalid-IR bug for `<` etc.).
  - **Type parameters / Self**: deferred (no error at generic-definition time) in both paths — preserves prior generic behavior; NOT a unilateral generic-semantics change.
  - Validated: 21 targeted checker unit tests; full unit suite (40 pkgs) green; conformance (1094) green; adversarial-reviewed (no real defects introduced).
- **STILL OPEN — do not lose these**:
  1. **Struct/array equality implementation** — currently a clean "not yet implemented" checker error. When implemented: a recursive "comparable iff all fields/elements comparable" check (a struct with a slice/iface/func field → permanent reject; all-comparable struct → fieldwise compare); add a runtime equality conformance cell then.
  2. **Generic path NOT covered** — `==`/relational on a type parameter later INSTANTIATED with an aggregate is not caught: the body is checked once with `T` opaque (deferred), and instantiation does not re-check it (`check_generic.bn`), so it can reach IR-gen → the same invalid-IR class, via generics. PRE-EXISTING (before this change all aggregate `==` was permissive); this change does not worsen it. Needs instantiation-time re-checking OR a `comparable`-style constraint decision. Separate follow-up.
  3. **Sentinel detection (`err == io.EOF`)** — disallowing interface-value `==` means this is NOT the mechanism; needs `identical`/`same` + `errors.Is` (under discussion / see io.EOF TODO). Resolve before the first real `Reader` lands.

### x64 native backend mis-packs sub-word multi-return + non-8-multiple struct params — CONFIRMED
- **Symptom**: (a) a sub-word (`uint16`) multi-return at arity ≥ 3 mis-packs the
  3rd+ component; (b) a `3×uint32` (12B) or `5×uint8` (5B) struct passed by value
  as a param loses its trailing field. (x64 struct-RETURN works.) On x64 native.
- **Test**: `conformance/matrix/abi/multi-return/u16/{3,4,5}` +
  `abi/struct-param/{three-u32,five-u8}` (5 cells, xfailed both x64 modes). Pass
  on LLVM + VM (and aa64 multi-return).
- **Discovery**: 2026-06-05, P1 ABI matrix. §3.9. NOTE: the all-int multi-return
  n=2-cap from §3.1 is **FIXED** (arity ≤ 5 all-int passes everywhere).
- **Root cause**: x64 aggregate-arg + sub-word multi-return packing. Needs
  investigation.

### Float-literal converter 1 ULP low for ~38+ sig-digit literals just above a tie (round-bit loss) — ✅ RESOLVED (binate `58570970`, `ParseFloatLitToBits` via `strconv.ParseFloat` — exact round bit)
- **Symptom**: a float64 literal with ~38+ significant digits sitting JUST
  ABOVE a binary rounding tie (e.g. `1.0000000000000001110223024625156540424`)
  converts 1 ULP LOW.  `common.ParseFloatLitToBits` holds the significand in a
  128-bit window and collapses everything below the kept 53 bits into a single
  sticky flag, losing the exact round bit.  LLVM (its own strtod) is correct;
  the VM and native backends share the converter, so they are wrong.
- **Discovery**: 2026-06-03 completeness review of the 128-bit-accumulation
  rewrite; reproduced vs strconv + a big.Float reference (~50% of constructed
  just-above-tie inputs diverge, all +1 ULP in strconv's favor).  Realistic
  literals (≤~37 sig digits) are correct — this is the table-maker's-dilemma
  tail.
- **Test**: `conformance/538_float_lit_tie_roundbit` (passes on LLVM, xfailed
  on the VM modes).
- **Proper fix**: exact rounding via `pkg/std/math/big` (mantInt*10^exp as a
  Nat, extract 53 bits + round-to-even from the exact remainder — Go's
  slow-path).  **No longer blocked**: the earlier "cmd/bnc's BUILDER tree can't
  import stdlib `big`" caveat is STALE — verified 2026-06-05 that the current
  BUILDER (`bnc-0.0.7`) compiles and runs a `pkg/std/math/big`-importing program
  correctly (`Nat.Mul` → 3000000). `math/big` is float-free integer big-num (no
  floats / generics / closures / interfaces), so it is BUILDER-compilable; only
  `strconv`-as-a-whole stays blocked (its `ftoa.bn` is float-using), and the fix
  needs `math/big` directly, not `strconv`. So the converter (in
  `pkg/binate/native/common`) can `import "pkg/std/math/big"` and do the exact
  mantInt*10^exp rounding. Remaining check before landing: confirm no tier/layer
  hygiene rule forbids the compiler tree depending on tier-1 stdlib (a layering
  question, not a BUILDER-compilability one). Interim alternative (no longer
  needed if the proper fix lands): widen the fixed window (256-bit → ~76 digits).
- **Severity**: MAJOR (silent 1-ULP-wrong float constant), narrow (38+ digits
  AND just-above-tie).

### Bundle tier-1 stdlib (pkg/std, pkg/stdx) with the BUILDER; cut a new BUILDER release
- **What**: the BUILDER bnc tarball should ship the tier-1 stdlib so cmd/bnc's
  tree (and any BUILDER-compiled code) can import `pkg/std/...` / `pkg/stdx/...`
  — including `pkg/std/math/big` and a future `strconv.ParseFloat`.  The "BUILDER
  tree can't use stdlib" constraint is purely an artifact of stdlib not being
  bundled (plus a few BUILDER float gaps — we're well past bnc-0.0.1; a release
  is overdue).
- **Unblocks**: the exact-rounding fix above; lets the float-literal converter
  use `big` / `strconv.ParseFloat` directly.
- **Also**: clear the remaining BUILDER float gaps so floats are fully
  BUILDER-compilable, then cut the release and bump BUILDER_VERSION.

### Multi-value return assignment to `_` leaks the discarded managed component(s) — FIXED 2026-06-03 (binate, pending cherry-pick)
- **Was**: `_, n = f()` where `f` returns `(@T, int)` (or `@Iface`, `@[]T` — any managed type) never RefDec'd the `_`-discarded managed result → +1 leak per execution.  Root cause: the multi-assign loop (`genAssign`, `gen_control.bn`) ran the Axiom-3 copy-RefInc for the `_` component unconditionally, but a blank target stores nothing (`lookupVar("_") == nil`), so that RefInc had no matching RefDec.  (The single-value `_ = g()` path doesn't leak because its RefInc is *inside* the `ptr != nil` guard.)
- **Fix**: skip a blank-identifier target entirely in the multi-assign loop (`if lhs.Kind == EXPR_IDENT && isBlank(lhs.Name) { continue }`) — no copy-RefInc, no store; the call-result temp's dtor RefDec's the owned ref at end of statement.
- **Test**: `conformance/570_blank_discard_managed_balance` (loop of 100 discards; b's refcount returns to baseline 1, was 101 pre-fix).  Verified to fail on the unfixed compiler.
- **NOTE — the BOTH-bound form `a, n = f()` is NOT balanced** (the old entry wrongly claimed it was — it had only been checked for `@T` bound to a fresh-nil var).  See the two multi-assign defects in the CRITICAL section.

### Remove the `pkg/binate/vm` lint skip after the next release
- **What**: `scripts/hygiene/lint.sh` temporarily skips `pkg/binate/vm`,
  `pkg/binate/repl`, and `cmd/bni` (`LINT_SKIP`).  The BUILDER-bundled bnlint
  (bnc-0.0.6) predates the `_Package` selector + `_func_handle` typecheck
  support, so it aborts at the typecheck pass on `_func_handle(rt._Package)`
  / `@reflect.Package` in `vm/extern_register_std.bn`; repl + bni cascade in
  because bnlint typechecks dependency bodies (entry above).
- **Removal condition**: drop the whole `LINT_SKIP` block once
  `BUILDER_VERSION` is bumped to a snapshot that includes BOTH (a) the
  `_Package` selector + `_func_handle(pkg._Package)` typecheck support
  (binate `feadde2c` and predecessors), and (b) the bnlint dep-body fix
  (entry above — landed in source as binate `3fcfdf8c`, awaiting only the
  BUILDER bump).  With (a), `vm` lints; with (b), the repl/bni cascade is
  gone.  A from-source bnlint already lints all three cleanly today.
- **Marker**: the skip block carries a `TODO(remove after next release)`
  pointing here.

### Remove the build.bni-dedup workarounds after a BUILDER bump
- **What**: the build-constraint migration collapsed `pkg/builtins/build` to one
  `#[build(...)]`-gated `ifaces/core/pkg/builtins/build.bni` and re-sourced the
  build config from the active target (binate `5a8714d8` / `b64b21fd` /
  `b0bd1096`).  Because the pinned BUILDER (`bnc-0.0.8`) predates BOTH the
  `ARCH_ARM64 → ARCH_AARCH64` rename AND `#[build]` parsing, three TEMPORARY
  workarounds were needed:
  1. an `ARCH_ARM64` alias (`= ARCH_AARCH64`) in `build.bni`, referenced by
     `buildcfg.HostConfig`, so `cmd/bnc` (which now imports `build`) compiles
     under the bundle's pre-rename `build.bni`;
  2. a throwaway ungated-`build.bni` shim in `scripts/hygiene/lint.sh` (prepended
     to `-I`) so the bundled bnlint — which can't parse `#[build]` and now loads
     `build` transitively via `buildcfg` — typechecks against the shim, not the
     gated file (keeps the fast bundled-bnlint path);
  3. a `[ -d ]`-guarded `ifaces/targets/<key>` lookup in `scripts/binate-paths.sh`
     so a bundle's old per-target `build.bni` (the bundle still ships
     `ifaces/targets/`) is still found when compiling cmd/bnc, while being a
     no-op against the current tree (`build` lives in `ifaces/core`).
- **Removal condition**: bump `BUILDER_VERSION` to a snapshot built AFTER this
  migration (its `build.bni` has `ARCH_AARCH64` and lives in `ifaces/core`, and
  its bnc/bnlint parse `#[build]`).  Then: drop the alias + switch
  `buildcfg.HostConfig` to `ARCH_AARCH64`; remove the lint shim (restore the
  plain bundled-bnlint invocation); drop the guarded `ifaces/targets` lookup +
  `TARGET_DIR` from binate-paths.  Each is comment-flagged in-tree
  (`TEMPORARY`/`Remove once BUILDER`).  Full plan +
  workaround list in
  [`plan-impls-constraints-migration.md`](plan-impls-constraints-migration.md).
- **Bonus**: the same bump would also let `pkg/bootstrap` be collapsed onto
  `#[build]` (it's in cmd/bnc's BUILDER-compiled tree, currently left
  path-selected — see that plan doc).

### Native aa64 self-host lane failed to BUILD — `duplicate symbol` (62 dups) — FIXED 2026-06-03 (binate, pending cherry-pick)
- **Was**: `builder-comp_native_aa64-comp_native_aa64` failed at
  compiler-build (link) time, `ld: 62 duplicate symbols` (e.g.
  `_bn_pkg__binate__types__predeclaredNil`,
  `_bn_pkg__binate__ir__moduleGlobals`, …) — each a top-level package var
  defined in BOTH `main.o` and its owning package's `.o`.  The lane never
  reached running a test.
- **Root cause (the static-managed-sentinel hypothesis was WRONG)**:
  `ir.Global` carries `IsExtern` (an imported `.bni` extern var, defined by
  its owner's TU).  The LLVM backend honors it — emits `external global`
  (declaration only).  The NATIVE backends' `emitGlobals`
  (`pkg/binate/native/{aarch64,x64}`) did NOT check `IsExtern`: they emitted
  a strong definition for EVERY global, so every importing TU carrying an
  IsExtern entry re-defined the owner's symbol → duplicate-symbol link
  failure.  The recent cross-package extern-var feature (binate `be49c0a9`
  etc.) populated modules with IsExtern globals, tipping the latent native
  gap into a build break.
- **Fix**: native `emitGlobals` (both backends) now `continue`s on
  `g.IsExtern` (no definition — the reference resolves to the owner
  cross-object, exactly like LLVM's `external global`).  Also open the data
  section LAZILY (only once a real non-extern global is emitted): a module
  whose globals are ALL extern was otherwise leaving an empty data section
  that the Mach-O writer turned into a malformed load command (the
  `548/552/558` cross-pkg link failures).  Unit tests:
  `TestEmitGlobalsSkipsExtern` in both backends.
- **Result**: the aa64 self-host lane BUILDS and runs — `491 passed, 0
  failed` (xfails skipped).  `534` (the `@func` fix) passes on native aa64;
  `541` stays xfailed (native float gap).
- **Newly-exposed native-aa64 gaps (xfailed + tracked; NOT regressions —
  these tests never ran before the lane built)**: `550` (@func
  capture-record refcount wrong on native), `569` (float captured in a
  closure reads 0 — native float gap, 541-family), `559`/`561` (cross-package
  MANAGED extern var — already xfailed on every mode; needs the imported
  type's dtor).  `550`/`569` are the genuinely native-specific ones worth a
  follow-up.  (`551` `&G`-as-rvalue is now FIXED — see entry below.)

### `550` native @func capture-record refcount — FIXED 2026-06-04 (binate `7dab4be7`; split `879fe3a1`) — pending cherry-pick
- **Symptom**: a capturing `@func`'s captured managed value was not
  released when the closure died on native aa64; `conformance/550` read
  rt.Refcount 2 instead of 1.  Green on every other mode (VM via
  `0a0d00af`; LLVM via the func-value vtable dtor slot).
- **Root cause**: native `emitFuncValueVtables` always wrote the
  vtable's slot-0 (dtor) as 8 zero bytes, even for a capturing managed
  closure whose struct needs destruction.  `fv.vtable[0]` null ->
  OP_FUNC_VALUE_DTOR yields null -> rt.ZeroRefDestroy skips the dtor ->
  the captured value's ref leaks.  The OP_FUNC_VALUE_DTOR load and
  emitRefDecInline forwarding were already correct; only slot-0 wiring
  was missing.
- **Fix**: new `emitFuncValueVtableDtorSlot` (aarch64) /
  `emitFuncValueVtableDtorSlot_x64` emit slot 0 as a pointer to the
  closure-struct dtor's HANDLE (`___handle.<dtor>`) when
  `lookupClosureFuncAA64(mod, seen[i])` returns a func that is
  `IsManagedFuncValue && ClosureStruct != nil &&
  ClosureStruct.NeedsDestruction() && len(ClosureStructDtorName) > 0`;
  else 8 zero bytes (unchanged).  Mirrors `emitFuncValueVtableDtor` in
  pkg/binate/codegen.
- **Symbol-convergence note (the part the pre-fix plan got slightly
  wrong)**: `f.ClosureStructDtorName` is the UNqualified dtor name
  (`__dtor_<closure>`), NOT the dtor func's qualified `Name`
  (`<pkg>.__dtor_<closure>`).  They still resolve to ONE symbol because
  `handleSymFor` routes through `mangle.FuncName(pkgName, ...)`, which
  folds a same-package qualifier prefix and a pkgName-prefixed
  unqualified name to the identical `bn_<pkg>__<dtor>` — so slot 0
  references exactly the `___handle.<dtor>` triple that
  collectFuncValueRefs' IsLinkOnce pre-pass already emits.  No new
  global, no dangling reference.  (Used the EXISTING `lookupClosureFuncAA64`,
  which returns the closure func directly — the planned
  `lookupModuleFuncAA64` was unnecessary.)
- **x64 parity**: same fix in `pkg/binate/native/x64/x64_funcvalue.bn`
  (no CI lane, but had the identical latent capture-leak).
- **Hygiene**: the +45-line fix pushed `aarch64.bn` over the 500-line
  cap, so the func-value emission was first extracted to
  `aarch64_funcvalue.bn` (mirrors `x64_funcvalue.bn`) in `879fe3a1`.
- **Tests**: 550 un-xfailed on native aa64 (verified fail pre-fix /
  pass post-fix); `aarch64_funcvalue_test.bn` pins slot-0 shape (dtor
  handle for a capturing managed closure, null otherwise, null for the
  *func and no-managed-capture forms).

### Native backends mis-lower float consts/returns — `541` silently reads 0 (Phase A float-const gap on the native code generators) — ✅ RESOLVED (binate `5281b138` + `cc6d0e9b` AAPCS64 D0 float-return + `1285683e` runtime link; `541` green on native aa64)
- **Symptom**: `conformance/541_cross_pkg_const_float` passes on the
  default C/LLVM-backed modes but **fails on the native aarch64 backend**
  (`builder-comp_native_aa64-comp_native_aa64`): expected `7 -3 7 -3 9`,
  actual `7 0 0 …`.  Two distinct silently-wrong cases (both → `0.0`):
  1. **Negative float const** — `cfg.NegHalf` (`= -1.5`) read cross-package
     reads as `0.0` (line 2).  The positive sibling `cfg.Ratio` (`= 3.5`)
     read the same way (cross-pkg `EXPR_SELECTOR`) is **correct** (line 1 → 7),
     so positive `EmitConstFloat` + float-mul + `cast(int, float)` all work
     on the native backend; only the **negative/unary-minus-folded** float
     literal mis-lowers.
     **FIXED 2026-06-03 (binate `5281b138`)**: the root cause was
     `common.ParseFloatLitToBits` (the shared text→bits converter used by
     every native backend) silently dropping a leading `-` in the folded
     literal text and returning 0; it now honors the sign.  Verified at unit
     level (`TestParseFloatSigned`) and via `541` on the VM modes (the VM was
     made to route through the same converter).  The native aa64 *lane* can't
     confirm end-to-end because it no longer links (the duplicate-symbol entry
     above), but the converter is the shared piece and native's emit path was
     already correct for positive consts.  Case 2 below is still open.
  2. **Float function return** — `cfg.Scale()` (returns `Ratio` via an
     in-package `EXPR_IDENT` read) reads as `0.0` (line 3), ditto
     `cfg.NegScaled()` (line 4).  Either the native float-return ABI (value
     should arrive in `d0`, caller reads 0) or the in-package `EXPR_IDENT`
     float-const read is broken — 541 alone can't disambiguate (need a
     direct-return-vs-direct-read probe).
- **Discovery**: 2026-06-03, running `./conformance/run.sh
  builder-comp_native_aa64-comp_native_aa64` (the aa64 lane the user
  watches).  `541` has **no xfail markers** and its own header explicitly
  intends cross-backend stability ("cast-to-int keeps the expected output
  stable across backends"), so this is a genuine native-backend correctness
  hole, not an intended skip.
- **Why MAJOR**: silent wrong float values (reads 0 instead of the real
  value) on a shipping backend — the exact silent-miscompile class.  The
  IR-gen Phase A fix (above, line ~462) is correct at the IR level; the gap
  is in the **native code generators** (`pkg/binate/native/{aarch64,x64}`),
  which Phase A never validated (it was checked on the C/LLVM modes only).
- **Unverified / TODO**: (a) confirm whether `native_x64*` modes fail the
  same way (likely — same native-float codegen path; not run here, no x64
  host) and add their xfails too; (b) disambiguate case 2 (float-return ABI
  vs in-package float-const read) with a minimal probe; (c) `534` (the
  `@func` bug) also fails unmarked on the aa64 lane — its xfails cover only
  the 6 default modes, so the cross-compile lanes need 534 xfails for an
  honest suite.
- **Tracking**: proposed xfail `541_cross_pkg_const_float.xfail.builder-comp_native_aa64-comp_native_aa64`
  (one-line: native aa64 mis-lowers negative float const + float return → 0).

### `rt.Exit` paradigm: `exit` vs `abort`/`panic` — DISCUSS
- `rt.Exit` (→ libc `exit`) is the wrong model in general: process exit
  is meaningless in an embedded/freestanding environment, and the
  runtime mostly invokes it for *abort* conditions (OOM, bounds-fail,
  refcount corruption). `abort`/`panic` is likely the right paradigm.
- Surfaced 2026-06-03 alongside the `__c_call`/drop-libc work; that
  change preserves `Exit`→`exit` behavior, so this is a clean,
  independent follow-up. Needs a design discussion before any change.

### `__c_call` should support void returns
- Today `__c_call` "requires a return type" and `checkCCall` rejects
  void ("void and struct returns not yet supported"). So calling a void
  C function (`free`, `exit`) means declaring a dummy scalar return
  (e.g. `int`) and discarding it as a bare statement — see the
  placeholders in `impls/core/libc/pkg/builtins/rt/rt.bn`
  (`__c_call("free", int, ptr)` / `__c_call("exit", int, code)`).
- **Fix**: accept a void return spelling for `__c_call` (and a bare-
  statement form), so void C calls don't carry a misleading return type.
- Surfaced 2026-06-03 by the drop-libc work.

### Inject `pkg/bootstrap` into the VM + convert I/O to `__c_call` — Phase 1 DONE; Phase 2 DEFERRED (BUILDER-runtime coupling)
- **Phase 1 LANDED** on main (`a7fabc7a`, 2026-06-03): bootstrap is now
  native-only in the VM — cmd/bni skips lowering it, the format helpers
  (formatInt/Int64/Uint/Bool/Float, Itoa) are registered as externs in
  both `registerBootstrapExterns` copies, bootstrap's bytecode unit tests
  are xfailed in the 3 `-int` modes, and `extern_register_std_test` guards
  format-helper registration.  `formatFloat` (the first native float
  extern) dispatches via the all-int shim ABI (`7abc3809`).  Verified:
  `287_float_println` green in `-int`; full `builder-comp-int` /
  `-comp-int` / `-int-int` clean but for pre-existing failures.
- **Plan**: [`plan-bootstrap-ccall.md`](plan-bootstrap-ccall.md). The
  rt-drop-libc pattern applied to bootstrap: eliminate the hand-written
  `bn_pkg__bootstrap__*` I/O glue in `binate_runtime.c` by converting it
  to `.bn` + `__c_call`, and make bootstrap native-only in the VM.
- **Phase 2 DEFERRED (2026-06-03), possibly indefinitely**: converting
  the I/O to `.bn` *adds* `bn_pkg__bootstrap__{Open,Read,Write,Close,Exit}`
  defs that collide with BUILDER's pinned runtime (gen1 links it,
  `build-compilers.sh:55-62`) → duplicate-symbol link failure building
  gen1. It's a runtime-ABI change, so it can only be done *during a
  BUILDER bump/release* (the new BUILDER's runtime omits the I/O), not in
  the pinned-BUILDER tree. The trivial+moderate `.bn` code was written +
  reviewed (correct modulo the link blocker) and is preserved in
  plan-bootstrap-ccall.md's appendix. `Stat` is a further defer (struct
  stat platform divergence → needs a per-libc-platform impl split). It may
  be better to *eliminate* these bootstrap I/O functions (subsumed by a
  real stdlib `io`) than convert them — so this may never be worth doing.
- **Harder than rt**: `__c_call` is scalar/pointer-only, but bootstrap's
  I/O takes slices + returns managed-slice aggregates → marshalling
  (null-term cstr, data-ptr extraction, aggregate construction). `Args`
  can't be pure `__c_call` (no libc fn returns argv) — a minimal argv
  hook stays in C. Not C-freedom (still links libc syscall wrappers).
- **Needs a BUILDER bump** (the deferral reason above; the original
  "no BUILDER bump" claim was wrong — BUILDER *compiles* `__c_call` fine,
  but its *runtime* still defines the I/O symbols gen1 links). Baremetal
  keeps its semihost impl (per-target, like rt). Filed 2026-06-03.

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

### Cross-package managed refcount-safety + extern-var coverage gaps (2026-06-04 audit)
- A coverage audit (multi-agent workflow) of cross-package extern-var
  and managed-ptr/value test coverage — run after the 551/559/561
  deferrals + 586 — found that most cross-package MANAGED scenarios are
  tested only FUNCTIONALLY (output is right), not for REFCOUNT BALANCE,
  so a leak (rc stays elevated) or an extra RefInc/RefDec would slip
  through.  17 gaps confirmed (adversarially verified vs existing tests).
- **Addressed**: managed-slice extern-var value-copy rc-balance is now
  `conformance/592_cross_pkg_managed_slice_copy_balance` (the 586
  companion; balanced in 5 default modes + native aa64, int-int xfailed
  for the rt-loader bug; binate `efe989e6`).  (Landed as 592 — 587/588
  then 589/590/591 were taken by concurrent landings as the number kept
  moving.)
- **Remaining rc-balance gaps** (functional coverage exists; no
  `rt.Refcount` before/after — add it, pattern: 586/592/130) — a managed
  value crossing a package boundary as:
  - a managed-slice ELEMENT assignment of a managed value
    (`pkg.S[i] = @v`; also exercises RefDec of the overwritten element);
  - a function ARGUMENT (`pkg.f(@T)`) / RETURN (`pkg.New() @T`);
  - a STRUCT FIELD store (`root.X = child`, X a cross-pkg `@Node`);
  - an INTERFACE construction (`var iv @pkg.I = h`) / interface RETURN
    (`pkg.Make() @Shape`);
  - a GENERIC type argument (`genlib.Append[@pkg.T](...)`).
  These are pre-existing and NOT extern-var-specific — a broader
  cross-package-managed refcount-safety test initiative.
- **Extern-var FUNCTIONAL gaps** (the paths work; just untested):
  `&pkg.X` (address-of an imported SCALAR var — the 551 analogue for
  imports); field write through an imported RAW-ptr / value-STRUCT var
  (the 561 analogue); raw-slice element write through a `*[]T` extern var.
- **Blocked**: 586/592's `builder-comp-int-int` xfails clear once the
  136/383 int-int rt-loader bug (above) is fixed.
- **Discovery**: 2026-06-04 coverage-audit workflow.

### Slim `pkg/bootstrap` and `pkg/libc` by migrating callers OUT
- **What**: rather than converting bootstrap's I/O surface
  in place, migrate callers AWAY from `pkg/bootstrap.X` and
  `pkg/libc.X` toward whatever the long-term replacement is
  (a new I/O package, a slimmer `pkg/std/os`, etc., TBD).
  Goal: shrink the surface of both bootstrap and libc until
  they can either be retired entirely or held as truly minimal
  bootstrap primitives.
- **Approach** (sketch — needs design): identify call sites,
  classify them by what they want (formatted print, file I/O,
  process control, raw libc memops), and route each class to
  the canonical replacement.  bootstrap and libc only get
  what's TRULY platform-essential and inappropriate for any
  higher-level package.
- **Progress**:
  - **libc Memcpy / Memset — DONE 2026-06-02 (binate `87965b70`)**:
    the libc-host rt's MemCopy / MemZero now do pure-Binate byte loops
    (matching the baremetal rt, which already did) and Box copies via
    MemCopy, so both primitives were removed from the whole surface —
    `pkg/libc.bni`, `runtime/libc_stubs.c`, the cmd/bni + vm extern
    registries, and the vestigial baremetal `bn_pkg__libc__*` aliases
    in semihost.s.  No BUILDER bump (gen1 links BUILDER's runtime;
    gen1's outputs emit no `bn_pkg__libc__*` and link checkout's
    runtime).  Verified across compiled / VM / self-hosted / baremetal
    lanes.  Perf footnote: the byte loops are slower than libc
    memcpy/memset at -O0 (no idiom recognition) — accepted for now,
    revisit with a word-at-a-time loop if it shows in profiles.  This
    does NOT touch the C-ABI memcpy/memset LLVM emits for aggregate
    copies (llvm.memcpy intrinsics), which are independent of pkg/libc.
- **Remaining libc surface**: Malloc / Calloc / Free (now the only
  callers; need a real Binate allocator to retire) and Exit (needs a
  process-exit syscall, gated on the C-free syscall story).
  `pkg/bootstrap` — the larger I/O surface — is the next target.
- **`bootstrap.Itoa` — FULLY RETIRED (2026-06-08, `f7966135`).**  Every
  caller migrated, then the function, declaration, tests, baremetal
  duplicate, and VM extern registration all removed.  Now that
  `pkg/std/strconv` has `Itoa(v int)`
  (base 10), `FormatInt(v int64, base)`, and `FormatUint(v uint64, base)`,
  they are the canonical replacement for `bootstrap.Itoa`.  Goal: every
  Tier-1/Tier-2/Tier-3 caller uses strconv instead of bootstrap (a
  sub-step of retiring the bootstrap int-format surface).
  - **The old "BUILDER tree CANNOT import strconv" constraint was wrong /
    is now moot.**  `strconv` (whole package, incl. its `pkg/std/math/big`
    dependency via `ftoa.bn`) is ALREADY in cmd/bnc's BUILDER-compiled
    tree: `pkg/binate/ir/gen_const_fold.bn` and
    `pkg/binate/native/common/common_float.bn` import it, and BUILDER
    compiles them when building gen1.  So BUILDER-surface packages
    (`token`, `native/*`, codegen, ir, …) CAN migrate — verified by
    migrating `token` (gen1 rebuilds clean across builder-comp / -int /
    -comp).  No integer-only strconv subpackage is needed.
  - **`pkg/builtins/lang` (Tier-0 core) — DONE (2026-06-07):** lang can't
    import `strconv` (below Tier 1; layering inversion, and a cycle since
    strconv's closure reaches the builtins), so it got package-internal
    full-width formatters (`formatUint64` / `formatInt64`, mirroring
    `bootstrap.Itoa`'s uint64-magnitude approach incl. the two's-complement
    trick for int64-min).  This also fixed a correctness bug: the impls had
    funnelled through `bootstrap.Itoa(cast(int, x))`, which on 32-bit
    targets TRUNCATED the wide types — `(int64/uint32/uint64).String()`
    were WRONG on ILP32 for values outside int32 range — and mis-signed
    unsigned values ≥ 2^63 on every target.  Each impl now widens
    losslessly (signed → `cast(int64, x)`, unsigned → `cast(uint64, x)`);
    lang keeps `bootstrap` only for `formatFloat`.  Covered by lang_test.bn
    boundary cases (the unsigned ≥ 2^63 ones fail under the old code on a
    64-bit host) and `conformance/653_int_string_width` (width-independent
    output, one .expected for LP64+ILP32; guards the 32-bit truncation
    under the arm32 modes — green on all 64-bit modes locally, arm32 needs
    qemu so it runs in CI).
  - **Conversion discipline for the migration:** route each site by the
    *argument's* type, never by a lossy down-cast — bare `int` →
    `strconv.Itoa`; wider signed → `strconv.FormatInt(cast(int64, x), 10)`;
    unsigned → `strconv.FormatUint(cast(uint64, x), 10)`.
  - **Leave (not formatting calls / separate decisions):** the extern
    registrations that expose `bootstrap.Itoa` to interpreted code
    (`pkg/binate/vm/extern_register_std.bn`, `cmd/bni/externs.bn`) — those
    go when `bootstrap.Itoa` is deleted, not now; the test-runner codegen
    in `cmd/bnc/gen_test_runner.bn` (emits source that calls
    `bootstrap.Itoa`); and `conformance/064_bootstrap_funcs.bn` (tests
    `bootstrap.Itoa` itself).
  - **Progress — all migratable package callers DONE** (2026-06-07; each
    green across builder-comp / -int / -comp, landed on main, one package
    per commit): `token`, `repl`, `native/{x64,aarch64}`, `vm`, `ir`
    (test-only), `lexer` (test-only), `types` (test-only), `lint`
    (test-only), `cmd/bnlint`, `cmd/bni`.  Every arg was a bare `int`, so
    all sites used `strconv.Itoa` directly (no `FormatInt`/`FormatUint`
    needed yet).
  - **Retirement — DONE** (landed in order, each its own commit):
    `gen_test_runner.bn` formats counts via `passed.String()` (`c2aaaabf`,
    relying on [A]); `321` migrated to `total.String()` (`9ba85eec`);
    `conformance/064` retired (`0d7c0501`); the VM extern registration
    dropped from both drivers (`6d2384de`); and finally the definition,
    `.bni` declaration, unit tests, and baremetal duplicate removed
    (`f7966135`).  The bootstrap int-formatting surface used by
    print/println (`formatInt`/`Int64`/`Uint`/`Bool`/`Float`) deliberately
    STAYS — only the standalone allocating `Itoa` is gone.
  - **Done since:** the ad-hoc `intToChars` helpers — the package-scoped
    one in `pkg/binate/ir/gen_func_lit.bn` (3 call sites: `__closure_local_`,
    `__funclit_`, `__mv_local_`) and a duplicate in
    `pkg/binate/vm/func_index_test.bn` — now use `strconv.Itoa` and are
    deleted (2026-06-07).
- **[A] Primitive `.String()` without importing `pkg/builtins/lang` —
  DONE across all execution modes (compiled `37b2ffcc`, VM `487c2d08`).**
  `myInt.String()` resolves AND links/executes with no import in both the
  compiled backends and the bytecode VM; naming the `lang.Stringer`
  interface *type* still requires the import (gated by the type checker).
  Mechanism (reverses the "No auto-import" decision in
  `plan-primitives-impl-interfaces.md`, for methods only): `ensureLangLoaded`
  force-loads lang so its carve-out impls attach `String()`/`Compare()` to
  the global primitive singletons (resolution); `appendLangImport` (a clone
  of `appendBootstrapImport`, added at every `RegisterImports` site with the
  same self-import guard, in BOTH `cmd/bnc/compile_imports.bn` and
  `cmd/bni/irgen.bn`) registers lang's signatures so the cross-package call
  resolves/links.  DCE/baremetal worry is moot (unused impls stripped by
  `--gc-sections`/`-dead_strip`).  Full conformance green in both
  builder-comp (1085) and builder-comp-int (1072).  Covered by conformance
  `654`–`656` (per-type positives) + `658` (negative).
  - **Remaining follow-up — the repl.** The repl has its own import setup
    (`pkg/binate/repl/{ir_imports,session,util}.bn`) not covered by the
    `cmd/bni` change; add `ensureLangLoaded` + `appendLangImport` there so
    `.String()` works at the repl too.  Small, same pattern.
- **[B] Test runners can depend on the stdlib — DONE (2026-06-08,
  `36e979df`).**  The `cmd/bnc --test` runner (`gen_test_runner.bn`,
  compiled by `test.bn`) is parsed *after* typecheck, so a stdlib package
  it imports that no test package pulls in was never loaded → not compiled
  → wouldn't link.  Fix: `genTestRunner` declares its stdlib deps in
  `testRunnerStdlibImports()`, and `test.bn` force-loads that list before
  typecheck (the compile loop already builds every loaded package, so they
  then link).  Adding the future `pkg/std/os` (for `Args`/`Open` when
  bootstrap I/O migrates) is a one-line addition to that list plus its use
  in the runner.  Exercised end-to-end now by a placeholder: the runner
  imports `pkg/std/errors` and makes one harmless `errors.New` call
  (TODO-marked for removal once a real dep lands) — proven by
  `pkg/binate/buf` (closure `{buf, testing}` excludes errors) whose test
  binary links the errors-importing runner only via the force-load.  The
  whole unit-test suite now exercises [B].  (The VM `-int` path is
  unaffected — `cmd/bni` executes tests directly, no generated runner; a
  future VM stdlib dep would be force-loaded there the same way as
  bootstrap/lang.)  Distinct from [A], which force-loaded lang to make
  `bootstrap.Itoa` removable.
- **Why migrate OUT rather than convert in place (do NOT re-attempt the
  in-place shape)**: in-place renames of packages whose surface is
  declared-only and resolved by C symbols (`pkg/libc`, and the I/O side
  of `pkg/bootstrap`) hit a wall that pure-Binate-package renames
  (pkg/rt → pkg/builtins/rt) do not.  The wall: at Stage 1, gen1 is
  linked against BUILDER's bundled `libc_stubs.c` (auto-found next to
  `--runtime`), which only defines symbols under the OLD mangled name
  (e.g. `bn_pkg__libc__Memset`).  Checkout source — now compiling under
  the NEW package name — emits calls to `bn_pkg__builtins__libc__Memset`,
  which is UNRESOLVED at Stage 1's link.  Pure-Binate packages don't hit
  this because the bnc-compiled package provides the NEW-name symbols as
  definitions in its own `.o`; declare-only-via-C packages have no such
  Binate-side definition.  Compat aliases in checkout's `libc_stubs.c`
  don't help — BUILDER's runtime is what Stage 1 links against, not
  checkout's.  Resolving would require either (a) pointing Stage 1's
  `--runtime` at checkout's (build-script surgery), (b) a supplemental
  compat .o via `--link-after-objs` (build-script surgery + new
  artifact), or (c) two release cycles with a transitional bridge —
  none worth the bootstrap migration's payoff.  Migrating callers OUT
  side-steps the whole tangle.
- **Status**: in progress.

### Package descriptors (Phase B) — `_Package()` works in compiled + VM modes (builtins); general Functions-table still future
- **Status**: compiled-mode AND VM-mode `_Package()` landed (binate
  `feadde2c`, VM-mode for the builtin packages).  The general interop
  Functions-table (user packages, auto-enumeration) remains future work.
- **What works (compiled mode)**: every package emits an immortal
  static-managed `reflect.Package` descriptor node + a generated
  `_Package() @reflect.Package` accessor (codegen `emit_pkg_descriptor.bn`,
  via the static-managed emitter).  The type checker synthesizes the
  `_Package` signature at selector resolution (`check_expr_access.bn`
  `packageAccessorType`), IR-gen registers it as an imported extern so calls
  resolve + a `declare` emits (`gen_import.bn`), and `reflect` is force-loaded
  (`ensureReflectLoaded`).  Drives a real immortal node through the compiled
  RefInc/RefDec sentinel end-to-end (see [`plan-static-managed-sentinel.md`]).
- **What works (VM mode, binate `feadde2c`)**: the earlier "Functions-table
  is genuinely required" finding was too pessimistic.  `_Package` is already
  a real exported per-module symbol, and the IR/func-value path already
  mangles a qualified `pkg._Package` reference to call it — so the only
  blocker was the type checker rejecting `_func_handle(pkg._Package)` (it's
  compiler-synthesized, not a `SYM_FUNC` in scope).  Two small changes wired
  it: (1) `types/check_builtin.bn` accepts `pkg._Package` as a `_func_handle`
  argument by name; (2) `vm/extern_register_std.bn`
  `registerPackageDescriptorExterns` binds the builtin packages' `_Package`
  (rt, libc, bootstrap, reflect) as VM externs.  Interpreted `pkg._Package()`
  now dispatches through the func-value shim to the real accessor, and the
  returned `@reflect.Package` is RefDec-safe via the static-managed sentinel —
  exercising the sentinel end-to-end in interpreted mode too.
- **Coverage**: `conformance/532_reflect_package_accessor`
  (`rt._Package().Name` → "pkg/builtins/rt") now green in ALL 6 default modes
  (the 3 VM-mode xfails removed).
- **Still future — the general Functions-table**
  ([`notes-package-introspection.md`](notes-package-introspection.md) Phase B):
  `registerPackageDescriptorExterns` is a hand-maintained precursor covering
  only the builtins compiled INTO the host binary (their `_Package` is a real
  symbol the shim can call).  USER packages run as interpreted bytecode and
  have no `_Package` body — those need the real table: codegen emits a
  per-package `Functions` table (name + signature + function-value per
  exported func), and the VM auto-enumerates all packages' tables (the
  cross-package registry, open Q4 in the notes — likely a linker section with
  start/stop symbols) to bind names → function values, replacing the hand-
  maintained `RegisterStandardExterns` entirely.  Then richer type metadata
  (Phase C) for reflection/printing + RTTI for type assertions.
- **Linter caveat (see "bnlint typechecks dependency bodies" + lint-skip
  entries)**: `registerPackageDescriptorExterns` is the first `_Package`
  reference in *linted* source, which the BUILDER-bundled bnlint can't yet
  typecheck — `scripts/hygiene/lint.sh` temporarily skips pkg/binate/vm +
  pkg/binate/repl + cmd/bni until the next BUILDER bump.

### Static-managed sentinel refcount — IN PROGRESS (prerequisite for package descriptors)
- **Status**: IN PROGRESS — worktree `temp-binate-6` / branch `work-6`,
  started 2026-06-01.  Plan:
  [`plan-static-managed-sentinel.md`](plan-static-managed-sentinel.md).
- **What**: implement the long-designed sentinel refcount for immortal
  static **managed objects** (`claude-notes.md:909`,
  `detailed-notes:1427`), so the package descriptor's
  `@reflect.Package` / `@TypeInfo` / `@FunctionInfo` nodes can be static,
  never-freed `@` values.  Designed but unimplemented in **all ~5 refcount
  paths** (library rt.bn ×2, LLVM-inline `emit_refcount.bn`, native aarch64
  inline, native x64 (library CALL), VM `vm_exec_helpers.bn`).
- **Root context**: immortality today rides entirely on the nil-pointer
  skip; there is no sentinel check anywhere.  The only static-managed data
  is string-literal managed-*slices* (immortal via `backing_refptr = null`,
  `emit.bn:382`).  There is no managed-pointer-to-static-struct in the
  language yet — the descriptor nodes are the first such case.
- **Design**: negative-as-immortal (`h[0] < 0`, cheap sign test); static
  nodes emitted with `h[0] = STATIC_REFCOUNT` (INT_MIN); `rt.RefDec`'s
  `<= 0` abort becomes `== 0`.  Add the short-circuit to all five paths +
  a static-node emitter (header `-16`/`-8` before payload).
- **Investigation rider** (per user): can the string-literal null-backing
  trick be retired / unified under the sentinel?  Representation can plausibly
  unify; the nil-check itself can't be dropped (guards genuinely-nil `@`
  values).  Deferred — sentinel lands first; string-literal lowering is
  untouched in the initial landing.
- **Tests**: conformance — immortal `@T` inc/dec'd + dropped, asserted never
  freed (poisoned free-fn / alloc counter), pinned across modes incl. arm32;
  unit — per-path no-op-on-sentinel + static-node IR shape.
- **Candidate user of the sentinel** (added 2026-06-02): the VM's per-callee
  shared non-capturing-`@func` `ClosureRec` (`ensureHandle` in
  `pkg/binate/vm/vm_exec_funcref.bn` — `callee.ClosureRec`, a
  `@VMClosureRec` shared by all instances of that func value) is exactly a
  static, never-freed managed object.  It was being prematurely freed by
  instance RefDecs (the `@func`-RefInc/RefDec-asymmetry CRITICAL bug,
  fixed symmetrically in binate `<commit>` — see `conformance/528`).  The
  symmetric-RefInc fix works, but making the shared `ClosureRec` an
  immortal sentinel object would be the cleaner long-term representation
  (no per-instance refcount churn on a shared singleton).  Consider
  folding it in when the sentinel lands.

### bnc: top-level consts of non-int types silently emit `EmitConstInt(0)` at read sites (Phase A — string/bool/float — DONE; composite/pointer remain)
- **Symptom — general**: declare a top-level `const X T = <expr>` where T is anything other than an integer-family type (or the iota-fed untyped int), and reads of X from any function — in-package OR cross-package qualified `pkg.X` — fall through to `EmitConstInt(0, TypInt())` in IR-gen.  Downstream effects depend on T's expected LLVM shape:
  - **Loud** (clang rejects the .ll with shape mismatch): types whose read sites perform an aggregate operation on what should be a slice / struct / array — get `extractvalue i64 %v, N` (extractvalue on a scalar).  Boolean reads hit `'%v' defined with type 'i64' but expected 'i1'` at branch sites.
  - **Silent wrong** (compiles cleanly, runs with zero values): scalar non-int types (float, char[fixed via lit-fold], pointer) read back as 0 / 0.0 / nil; struct reads return all-zeros.
- **Per-type characterization** (probed 2026-06-01):
  - `int` / all sized int+uint types / `char` / `iota` const groups — work (evalConstExpr handles INT_LIT, CHAR_LIT, arithmetic, references to prior int consts).
  - `*[]const char` (string) — **FIXED** in binate `a5acfc45`.  Producer (`genConst` in pkg/binate/ir/gen_const.bn + the importer's `registerImportFile` in gen_import.bn) recognizes EXPR_STRING_LIT initializers and populates a new `StrVal @[]char` + `IsStr bool` on ModuleConst.  Read sites (EXPR_IDENT in gen_expr.bn, qualified EXPR_SELECTOR in gen_selector.bn) walk moduleConsts and emit `EmitConstString` + `EmitStringToChars` for IsStr entries — producing the same OP_CONST_STRING + OP_RODATA_SLICE shape literal `*[]const char` values already use.
  - `bool` — broken loud (i64 vs i1 mismatch at branch).  Same-shape fix as string: add `BoolVal`/`IsBool` to ModuleConst, recognize EXPR_BOOL_LIT, emit EmitConstBool.
  - `float32` / `float64` — broken silent (read as 0).  Add `FltText @[]char` + `IsFlt bool`, recognize EXPR_FLOAT_LIT, emit EmitConstFloat (which takes raw text + a type — needs the const's declared type carried through).
  - `[N]T` (array literal) — broken loud (extractvalue on i64).
  - `struct T{...}` (struct literal) — broken silent (all-zero struct).
  - `*[]const T` / `@[]const T` (composite-literal slice / managed-slice) — broken loud.
  - `*T` / `@T` (pointer to value) — not yet probed.  Three sub-cases worth keeping straight when designing the fix:
    1. const-pointer to a static global (`const P *T = &G`) — needs the pointee's address to be known at compile time;
    2. const-pointer to a string literal address (`const P *const T = &SomeStringLitContent`?) — niche;
    3. const-pointer where `T` is itself const (`const P *const T = ...`) — orthogonal const-of-const.
- **Discovery**: 2026-06-01, while trying to land Phase 1 of plan-version-info.md.  The string case tripped first; subsequent probing across other types showed the common root cause.
- **Root cause**: `moduleConsts` only carried `Val int`; producers (`genConst`, `registerImportFile`) call `evalConstExpr` which is integer-only and discards non-int initializers entirely; read sites (EXPR_IDENT in gen_expr.bn, qualified EXPR_SELECTOR in gen_selector.bn) called `lookupConst` (also int-only), missed the discarded consts, and emitted a zero-int placeholder via `EmitConstInt(0, TypInt())`.  The type-checker correctly accepts these declarations — `const X T = expr` in Binate marks `X` as an immutable variable (`claude-notes.md` "Compile-time constants" / "Const on variable declarations"), not a compile-time-foldable literal — so the bug is squarely in IR-gen's const-handling.
- **Why MAJOR**: any production package that exposes a non-int top-level const silently mis-emits.  Currently latent only because the project has no such consts yet; the version-package draft (now landed for string only) was the first encounter.  Composite-typed consts are particularly dangerous — both loud-on-aggregate-access and silent-on-zero-default-read modes occur.
- **Tests covering it**: pkg/binate/version's tests pin the string case end-to-end through both in-package and cross-package reads; `conformance/522_cross_pkg_const_string` and the new `TestGenConstStringLit*` unit tests in `pkg/binate/ir/gen_const_test.bn` (binate `a000855a`) add coverage at the IR-gen producer + read sites.  No coverage for bool / float / composite / pointer cases yet — Phase A adds focused unit + conformance suites for each.
- **Status**: **Phase A DONE** (2026-06-02).  Every *scalar* non-int top-level const now lowers correctly — string (binate `7b0f77a3`), bool (`c3ff33f7`, conformance 540), float incl. untyped + float32 (`82c985f5`, conformance 541), negative float literals (`054629fd`), and non-int members of `const ( … )` **groups** (`a6fef840`).  Single + group producers, in-package + imported, all route through the shared `classifyConstLit` (string/bool/(unary-negated-)float) helper in `pkg/binate/ir/gen_const.bn`; read sites dispatch on `ModuleConst.Kind` (CONST_INT/STR/BOOL/FLT).  Unit tests in `gen_const_test.bn` + conformance 540/541 (cross-package EXPR_SELECTOR + in-package EXPR_IDENT, incl. a branch-condition bool and a group member).
  - **Coverage note** (probed): `GenConstMember` (REPL forward-ref retry) needs no non-int handling — it only ever sees *parkable* (undefined-name-referencing) consts, i.e. int/iota expressions, never literals.  `RegisterImport` (singular, `gen_register_import.bn`) is still int-only but is **test-only** (no production caller; production imports use the fixed `registerImportFieldsAndFuncs`) — a minor consistency follow-up, not a production gap.
- **Decision (2026-06-02): Phase B (composite-typed consts) is CANCELED.**  `const` stays **scalar-only** (per `claude-notes.md:267-283`); immutable composite data is expressed with `var readonly` (`plan-const-readonly.md`), not `const`.
  - **RESOLVED (2026-06-03, plan-const-readonly step 6)**: `checkConstDecl` now rejects a non-scalar const type via the new `Type.IsScalar` predicate (`errNonScalarConst`).  Unit tests: `check_decl_test.bn` (string + struct rejected; int/bool/char/float accepted) + `TestIsScalar` in `types_test.bn`.  The string-const IR-gen workaround (the `EmitConstInt(0)`-path CONST_STR family) was then removed in step 7, so the latent mis-emit bug this entry tracked is gone.
  - **Scouting handoff (if a `const`→composite extension is ever revisited)** — it is a real language extension, NOT the plan's lighter estimate: (a) composite consts would route through `moduleGlobals` + the synthetic `__init` allocate/store path (`gen_init.bn`), reusing the var-as-initialized-global lowering — **not** static rodata, which is byte/i8-only; (b) **cross-package global reads do not exist yet** — no imported-`var` registration in `gen_import.bn`, no qualified global read-site in `gen_selector.bn` (it searches only `moduleConsts`), no extern-global decl in codegen — so the plan's "reuse existing global machinery" is **false**; that plumbing must be built; (c) immutability needs **real checker work** (make a composite const read as a `TYP_READONLY` value + fix `checkIndexExpr` to re-wrap readonly on the element type so `X[i]=v` is caught), not "just tests" — `X[i]=`/`X.F=` on a composite const are silently accepted today because `SYM_CONST` (binding) and `TYP_READONLY` (type) are disjoint.
- **Phase C (pointer consts) is also CANCELED** — a pointer isn't scalar, and more fundamentally it *refers to storage*, so it can't be a pure compile-time value.  const-pointer / const-slice / const-managed forms stay rejected (storage-referring types), alongside the composite forms above.
- **Future direction (TODO, not started): allow `const` of transitively *purely value* types.**  A type is *purely value* iff it carries no storage reference: scalars (int-family / bool / char / float) are purely value; `[N]T` is purely value iff `T` is; a struct is purely value iff every field type is.  Pointers, slices, and managed pointers/slices are NOT (they hold a pointer to storage) and stay rejected.  (Strings are a slice of rodata, already handled as a separate immutable-rodata case in Phase A.)  A purely-value const's whole value is known at compile time, so it should be **const-folded at read sites as an immediate** — the scalar-const model (per-use `EmitConst…`), NOT Phase B's canceled initialized-global lowering.  This subsumes `const P Point = Point{1,2}` and `const M [3]int = …` as real constants.  When picked up: define an `isPurelyValueType` predicate, widen `checkConstDecl`'s accept boundary from "scalar" to "purely value", and extend the const producer + read-site dispatch to fold value-struct / value-array literals.

### Demote raw-slice escape check from type error to linter rule
- **Today**: returning a raw slice (`*[]T`) into a local array
  (`return arr[:]`) is a hard type-check error.  The check catches
  the obvious pattern but **misses the real escape paths** the
  type system can't see (escape via out-param, via mutating
  callee, via interface, etc.), so it's a false-confidence trap:
  the user assumes "if it type-checks, my raw slice doesn't
  escape", which isn't what the check actually proves.
- **Why now**: while designing Phase 2 of function values
  (`plan-function-values-phase-2.md`), the same escape question
  came up for capturing `*func(...)`.  Decision: no type-check
  rejection; raw is the opt-in escape hatch, linter warns on
  obvious patterns.  That makes the raw-slice rule the
  inconsistent one — slices are the only raw type with a hard
  escape check in the type system.
- **Fix direction**: demote the raw-slice escape rejection to a
  linter rule in `cmd/bnlint` (best-effort detection of return,
  store-to-outliving-field, assign-to-global, etc.).  Type
  checker stops rejecting; existing tests that exercise the
  reject become linter-positive cases.
- **Scope cost**: small.  One rule to remove from the type
  checker, one to add to bnlint, conformance test updates for
  the affected patterns, doc updates.
- **Coordination**: ideally lands alongside or just after Phase
  2 of function values (where the analogous capturing-`*func`
  linter rule is added — B.5 of `plan-function-values-phase-2`).

### IR integer constants are host-width `int` (blocks 32-bit-hosted toolchain) — LAYER 1 + 2 (INT64 + FLOAT64) DONE
- **Symptom**: under `builder-comp_arm32_linux` unit tests, `pkg/ir`
  and everything downstream of it (`pkg/native{,/amd64,/arm64,/common}`,
  `pkg/codegen`, `pkg/vm`, `cmd/{bnc,bni,bnas}`) fail to compile for
  arm32 with int-width type errors.  `pkg/ir` is the cascade root.
- **Discovery**: triaging the 14 arm32_linux unit-test failures after
  type-check errors gained source locations (binate `c011827`,
  conformance/494).  With locations on, `pkg/ir`'s only *source* error
  is `gen_util_literals.bn:234` (`intFitsInType` compares against
  `4294967295` > INT32_MAX), and tracing the value upstream shows the
  whole literal path is `int`.
- **Root cause**: the IR stores program integer constants in
  `Instr.IntVal`, typed `int` (`pkg/ir.bni:356`) — host-width.  The
  feeding path (`exprIntLitValue`, `bignumToInt`, `parseIntLit`,
  `EmitConstInt`) is all `int` too.  On a 64-bit host this happens to
  work (it's really storing a 64-bit *bit pattern* — a `uint64`-max
  literal lands as the int64 pattern `-1` and codegen emits it fine).
  On a 32-bit host `int` is 32 bits, so the path neither compiles nor
  can represent a `uint32`/`int64` constant.  Symbol/codegen output
  must not depend on host int width.
- **Severity**: major.  Loud (compile failure) on 32-bit, not a silent
  64-bit-host miscompile — but it blocks the C-free / 32-bit-hosted
  self-hosting goal.  `int64` vs `uint64` for the field is immaterial
  (it's a stored bit pattern reinterpreted by the constant's type);
  `int64` is the minimal-churn choice since the existing range-check /
  negation code is written in signed terms whose bounds fit `int64`.

- **Layer 1 — IR + codegen + native (DONE)**: made the program
  -constant path host-independent.  Landed: binate `879ba38`
  (asm 64-bit immediates: x64 Imm→int64 + Imm64, finished aarch64
  Imm consumers in pkg/asm/parse), `035022c` (IR int64 contract),
  `294b5f0` (wide-constant tests), `075e1f5` (made the int-width
  -assuming bootstrap/vm tests 32-bit compatible).
  - `Instr.IntVal` `int` → `int64`.
  - `exprIntLitValue` / `bignumToInt` return `int64`; `intFitsInType`
    takes `int64`.  (`parseIntLit` stayed host-`int` — a
    non-type-checked fallback; the real path takes the bignum branch.)
  - `EmitConstInt(int)` kept (widens internally) + new
    `EmitConstInt64(int64)` for the literal path.
  - `buf.WriteInt64` added; codegen's OP_CONST_INT emit uses it.
  - `pkg/native/{amd64,arm64}` `emitConstInt64` → `int64`; arm64
    extracts MOVZ/MOVK chunks via int64 shifts.  Fixed a latent bug:
    arm64 `emitConstFloat` did `cast(int, bits)` on a 64-bit IEEE
    pattern (dropped the high word on a 32-bit host) → `cast(int64,…)`.
  - VM boundary: `lower_instr.bn` `bc.Imm = cast(int, instr.IntVal)`
    — lossless on a 64-bit host; the truncation-on-32-bit is what
    Layer 2 addresses.
  - **Result**: all 14 packages in the arm32_linux unit-test set
    compile for arm32 (verified locally; runtime validated by the
    `builder-comp_arm32_linux` CI job).

- **Layer 2 — VM machine word (INT64 PATH DONE)**: `pkg/vm` uses host
  `int` as its universal machine word — registers, immediates,
  pointer arithmetic (`bit_cast(int, frameBase) + instr.Imm`),
  offsets.  So a 32-bit-hosted VM is a 32-bit machine and can't carry
  64-bit immediates.  Open design question (raised by user): can the
  VM keep host-sized words for most values and use 64-bit only when
  necessary?
  - On a 32-bit host the VM interprets 32-bit-*target* bytecode, where
    pointers / `int` / sizes / offsets are all 32-bit by definition —
    so host-word is already correct for the vast majority of values.
    The 64-bit cases are exactly the explicitly-64-bit ones: `int64` /
    `uint64` values and large literals.
  - Two implementations of "64-bit only when necessary":
    (a) uniform 64-bit value slots + width-aware ops — simplest and
    correct; on a 32-bit host it costs 64-bit slot storage and 64-bit
    arithmetic only where the op is 64-bit (the compiler already
    supports `int64` on 32-bit; bytecode is largely typed already).
    (b) host-word slots + 64-bit via register pairs / a parallel wide
    slot, switched by typed opcodes — saves the 32-bit storage but
    complicates the register model and bytecode (must track which
    slots are wide).
  - Recommendation: do (a) first (correctness, minimal model change);
    treat (b)'s host-word-mostly layout as a later 32-bit perf
    refinement, not a correctness prerequisite.
  - **Investigation findings (2026-05-26)**: the change is larger and
    more entangled than the (a)/(b) framing implies — `int` is a
    *single conflated word* across three distinct roles, so it can't
    be swapped to int64 blindly:
    1. **Register slots.** `regs *int`, accessed `regs[i]`.  But
       `pushFrame` already budgets `f.NumRegs * 8` bytes/reg
       (`vm.bn:181`) — 8-byte slots.  On a 64-bit host int==8 so it's
       consistent; **on a 32-bit host this is a latent stride bug**
       (8-byte budget, 4-byte `*int` access → registers alias).  So
       `regs *int → *int64` actually *fixes* this and matches the
       existing layout.
    2. **Host pointers.** Registers also hold host addresses via
       `bit_cast(int, vm.Stack)` / `bit_cast(*uint8, regs[i])`.  With
       int64 regs on a 32-bit host these become a width mismatch
       (host ptr 32-bit, reg 64-bit) — `bit_cast` is illegal
       (size differs); they need explicit widen-on-store /
       truncate-on-read helpers (`ptrToReg` / `regToPtr`).
    3. **Target-memory-structure access.** `bit_cast(*int, hdrPtr)`
       reads managed-slice/refcount headers as `*int`.  These are
       target-word-sized fields; tying their stride to the register
       word is wrong if the two ever differ.  Needs separating
       "VM register word" from "target word".
  - Surface: ~106 `bit_cast(int,…)/(*uint8,…)/(*int,…)` sites across
    vm_exec*.bn + vm.bn, plus `BCInstr.Imm int→int64`, register
    arithmetic, and the memory ops.  This is a multi-step refactor;
    settle the register-word-vs-target-word model before editing.
  - **What landed (int64 path)** — model:
    register == host word; 64-bit values use register pairs; pair ops
    only engage when `REG_SLOT < 8` (no-op on a 64-bit host).
    Pointer-vs-target-word ambiguity stays narrow because `bit_cast`
    sites are at register-vs-pointer boundary — register word stays
    host `int`, so the ~106 `bit_cast` sites are untouched.
    - Step 1 (binate `f7cae70`): `REG_SLOT = sizeof(int)`; register
      area / frame header sized by it.
    - Step 2a (`ca7def6`, `394a16a`, `ca41a75`): `buildSlotMap` /
      `regWidths` / `remapRegisters` — id→slot mapping with the
      audited `BC_RETURN.Dst` exception.
    - Step 3 (`fd3ca06`, `f764a66`, `be877fd`, `60657fd`, `947205f`,
      `ebaa077`): full `BC_*64` handler set — `LOAD_IMM64`, `MOV64`,
      arith / bitwise / shifts / signed+unsigned compares / unary
      (NEG, BITNOT) / casts (WIDEN_S, WIDEN_U, NARROW, MOV64-bitcast)
      / pair memory `LOAD64_PAIR` / `STORE64_PAIR`.  Pure compute
      factored into evalArith64 / evalCmp64 / evalShift64 /
      evalUnary64 / widen64* — host-tested across the tricky cases.
    - Step 4 (`925e9bc`, `949ea29`, `ebaa077`): lowering emits the
      `BC_*64` ops host-word-aware — `OP_CONST_INT`, all binary
      arith / cmp / shift, load/store, casts, NEG/BITNOT.
    - Step 2b (`24a5d67` RETURN64, `7353523` direct CALL,
      `2eaa8f9` indirect/func-value/iface call ABI,
      `11da9d7` multi-return pair-aware): int64 return + call ABI
      complete.  `NumParamSlots` + slot-count `Imm` semantics.
    - Step 6 (`1fd3b9f`): conformance/499 int64 arithmetic E2E.
  - **Float64-on-32-bit (DONE)**: mirrors the int64 pair pattern.
    - `ba1a798`: route the existing `BC_FNEG` / `BC_F*` /
      `BC_SITOF` / `BC_FTOSI` / `BC_F64_TO_F32` / `BC_F32_TO_F64` /
      `OP_CONST_FLOAT` `bit_cast(int, float64)` hops through
      int64 — compile-clean on a 32-bit host without yet changing
      lowering semantics.
    - `3126655`: `BC_F*64` opcode decls (`BC_FNEG64`,
      `BC_FADD64..BC_FDIV64`, `BC_FEQ64..BC_FGE64`) + pure
      `evalFloatArith64` / `evalFloatCmp64` / `evalFloatNeg64`
      helpers in `vm_exec64.bn` + host-testable unit tests for
      each helper.
    - `ae08c1ed`: `execOp64` dispatch glue — joins source pair(s),
      bit_casts through `int64` to `float64` for the compute,
      bit_casts back, splits to dst pair (or single-slot bool for
      compares).  Direct `execOp64(&stackArr[0], instr)` tests
      cover all three shapes (binary arith, unary FNEG, compare-
      writes-single-slot).
    - `00b10e38`: lowering — `lowerBinOp` / `lowerCmpOp` add an
      `isFloatPair` branch alongside the existing `isIntPair`;
      `OP_NEG` dispatches `BC_FNEG64`; `OP_CONST_FLOAT` emits
      `BC_LOAD_IMM64` with `splitInt64` halves when
      `is64BitScalar(instr.Typ) && REG_SLOT < 8`.
    - `769d2e54`: gate test for OP_CONST_FLOAT — confirms 64-bit
      host falls back to `BC_LOAD_IMM` (no spurious pair branch).
  - **REMAINING GAP — int64 side of int↔float CONVERSION casts is NOT
    pair-aware (latent; surfaced 2026-06-12 by the int↔float32 VM-fix
    review).** The "DONE" above covers float *arith/compare* pairs and
    the *float* side of conversions; it does NOT cover an int64/uint64
    operand of a `cast` to/from a float:
    - int→float SOURCE side (`BC_SITOF`/`BC_UITOF`/`BC_SITOF32`/
      `BC_UITOF32`): the handlers read the int source as a single slot
      (`regs[instr.Src1]`) and `lowerCast`'s int→float arm has no
      `is64BitScalar(srcTyp) && REG_SLOT < 8` check, so `cast(float*,
      <int64>)` on a 32-bit host drops the source's high half. (These
      handlers ARE dest-pair-aware for the float64 result — the
      asymmetry is source-only.)
    - float→int DEST side (`BC_FTOSI`/`BC_FTOUI`/`BC_F32TOSI`/
      `BC_F32TOUI`): the handlers write a single dest slot via
      `cast(int, f)` (host int) and `lowerCast`'s float→int arm has no
      `is64BitScalar(dstTyp)` check, so `cast(<int64/uint64>, <float>)`
      on a 32-bit host leaves the dest's high slot stale (and truncates
      through a 32-bit host int). (These handlers ARE source-pair-aware
      for a float64 source — the asymmetry is dest-only.)
    Latent, not a live miscompile: no conformance mode runs the bytecode
    VM on a 32-bit host (the `-int` legs run `bni` natively on the
    64-bit build host; arm32 modes are comp/native, not VM), and the
    arm32 `pkg/vm` unit tests don't exercise int64↔float conversion
    casts. NOT introduced by the int↔float32 fixes (`289420b6`/
    `3fd7e712`) — the new float32 ops faithfully mirror the existing
    single-slot float64 ones. Fix (to land before/with any arm32
    VM-host enablement): add `is64BitScalar` gates in both conversion
    arms of `lowerCast` and pair-aware source/dest handling
    (`joinInt64`/`splitInt64`) in the eight handlers, plus direct
    `execNumericCast` unit tests in `vm_exec64_test.bn` driving a
    pair-wide int64 source and dest.
  - **End-to-end arm32 coverage status (2026-05-28)**:
    - `pkg/vm` source compiles cleanly on arm32 (since `ba1a798`).
    - Conformance `builder-comp_arm32_linux`: green.
    - **pkg/vm unit tests on `builder-comp_arm32_linux`: green**
      (was 16 failures pre-session → 9 → 1 → 0).  The bytecode-VM
      BC_*64 / BC_F*64 dispatch and slot allocation are now fully
      end-to-end-validated on a real 32-bit target — including
      the `TestRepro_StructWithManagedSliceFieldAppend` managed-
      memory path, which surfaced the hardcoded-LP64 managed-
      allocation-header offset that `81d31b7c`'s MANAGED_HDR
      const fixed.
    - The cascade-revealed packages — pkg/{types, codegen,
      native/{common,aarch64,x64}} — are also green on arm32 now
      after the LP64-baked-test cleanup (`11ff9864`, `2d13838d`).
    - Remaining arm32_linux failures (5) are all the int64-min-
      boundary cluster in pkg/{bootstrap,buf,ir} — see the
      "arm32 unit-test cleanup" entry for the bucket.  Unrelated
      to this work.

### arm32 unit-test cleanup: 5 remaining int64-boundary tests
- **Context (2026-05-28)**: `builder-comp_arm32_linux` unit tests
  are now down to **5 failures across 3 packages** — every other
  cascade of arm32 issues that surfaced through May 27–28 has
  been root-caused and fixed.  The remaining 5 share one shape:
  int64-min literal handling on a host whose `int` is 32-bit.
- **Resolved (commit trail)**:
  - `aee0260` — `cmd/bni` test runner lookup keyed on full
    pkgPath (fixed the entire `-int` unit-test lane that was
    silently broken since `7f989ad`'s mangler full-path flip).
  - `73651c28` — int↔int width-cast lowering: BC_TRUNC32 + emit
    BC_SEXT / BC_ZEXT for narrowings / widenings between
    int8/int16/int32/int64 (was unconditionally BC_MOV — wrong
    for any non-8-bit width change).
  - `a2588c54` — `pkg/types` `initTarget()` defaults host-detect
    via `sizeof` (was hardcoded LP64).  Fixes the root cause that
    made `is64BitScalar(TypInt())` true on arm32 and triggered
    pair-branch emission for plain-int ops.
  - `11ff9864` + `2d13838d` — LP64-baked test assertions across
    pkg/{vm,types,codegen,native/{common,aarch64,x64}} replaced
    with host-aware checks or explicit `setTarget64()` + a
    `TypInt → TypInt64` substitution where the test's intent was
    "an 8-byte int field on LP64 ABI".  Also fixed two real bugs
    the cascade exposed: BC_FTOSI / BC_SITOF / BC_F64_TO_F32 /
    BC_F32_TO_F64 pair-aware, and `is64BitScalar` accepting
    TYP_UNTYPED_FLOAT.
  - `81d31b7c` — managed-allocation header offset host-aware
    (`MANAGED_HDR` const = `2 * sizeof(int)`, was hardcoded 16),
    cleared the `TestRepro_StructWithManagedSliceFieldAppend`
    qemu segfault.
- **Status of previously-listed buckets**:
  - **Bucket 1 (LP64-baked tests)**: pkg/vm, pkg/codegen, pkg/native/*
    are GREEN.  pkg/asm/{x64,aarch64,macho} weren't in the
    cascade-revealed set and remain native-host-arch dependent
    (likely still need xfails, but separate workstream — host
    arch != target arch).
  - **Bucket 1b (pkg/vm TypInt width)**: ROOT-CAUSED.  Fixed by
    `a2588c54` (initTarget host-detect — the LP64-default was
    the deeper-than-suspected cause; not a test-scaffolding
    SetTarget ordering issue).
  - **Bucket 2 (genuine test-level)**: Still open as listed —
    `TestBinBufWriteU64LittleEndian` (pkg/asm/elf),
    `TestOrrImm` (pkg/asm/arm32).
- **Still open — Bucket 3 (int64-min boundary)**:
  - `pkg/bootstrap.TestFormatInt64Boundaries`
  - `pkg/buf.TestWriteInt` — "expected int64-min round-trip"
  - `pkg/ir.TestBignumToIntInt64Min`
  - `pkg/ir.TestGenUnaryMinusOnInt64Preserves`
  - `pkg/ir.TestNeedsHintNarrowing`
  All five share the int64-min literal pattern.  Likely one
  underlying fix: bignum / parseIntLit handling for values that
  overflow int32 on the host but fit int64 at the target.  Not
  blanket-xfail — investigate and fix.

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

### Use interfaces more (opportunistic)
- **Constraint**: now bounded by `BUILDER_VERSION`-pinned bnc
  rather than the historical bootstrap subset — cmd/bnc no longer
  has to be bootstrap-runnable now that boot mode is gone (binate
  `c1be3cc`, 2026-05-21).  bnc-0.0.1 (the current BUILDER) supports
  interfaces, so anything in cmd/bnc's dep tree is fair game too.
  Generics are NOT in bnc-0.0.1, but interfaces are.
- **Candidates that look natural**: anywhere we currently
  switch on a kind tag with a dispatch table (e.g. opcode
  handlers, AST visitors, asm encoders) is the textbook shape
  where an interface compresses the dispatch.  Print/format
  helpers that take a kind + value pair are another easy lift.
  pkg/ast's tagged-union nodes (DECL_*, EXPR_*, STMT_*, TEXPR_*
  Kind enums + switch-on-Kind in pkg/{parser,types,ir,codegen,
  loader}) is the biggest single target but also the longest
  refactor — touches every layer.
- **How to land**: pick one site per PR, define the interface
  alongside, methodify the concrete types, drop the dispatch
  switch.  Keeps each step small enough that conformance +
  unit-tests stay green.  Mirrors the
  `migrate-to-method-form-opportunistic` pattern from
  `claude-todo-done.md` (DONE 2026-05-13).
- **Recon finding (2026-05-26)**: there is NO clean *small*
  retrofit target.  The candidates above split into two
  unappealing buckets: (a) enum→value lookups (reloc maps,
  opName, the emitInstr op dispatch) where `switch` is genuinely
  the right tool and an interface would mean manufacturing one
  empty marker type per enum value — pure ceremony; and (b)
  monolithic tagged unions (`ast.Stmt`/`Decl`, `ir.Instr`) where
  a real interface means splitting a struct that touches every
  layer.  So "use interfaces more" here is a deliberate design
  choice, not opportunistic cleanup.
- **Landed (2026-05-26): driver `Backend` interface** (binate
  `0ee0faa`, `bda81ca`, `6dacb23`).  The genuinely-valuable use
  found: `cmd/bnc/compile.bn`'s `Backend` interface
  (`compileModule`) with `llvmBackend` / `nativeBackend` impls,
  dispatched via `compileModuleVia`.  This collapsed the
  duplicated driver flow — `compileMainNative` is gone, `main()`
  picks the backend and the LLVM/native paths are unified.
  pkg/native also got an internal arch `Backend`
  (arm64/amd64).  These are the first non-synthetic interface
  users beyond pkg/std's `Stringer`.  NOTE: interface values
  must be constructed from locals, not package globals — `&global`
  iface construction was a codegen bug (now fixed, see
  conformance/495).

### Use `@[]@[]char{...}` composite literals (opportunistic)
- **Constraint**: previously forbidden because bootstrap didn't
  support managed-slice-of-managed-slice composite literals; now
  unlocked everywhere (bnc-0.0.1 supports them).  Mirrors the
  unconstraint situation for `cmd/bnlint`'s tests, which already
  use this shape.
- **Pattern to replace**: a known-fixed-length run of
  `args = appendCharSlice(args, "foo"); args = appendCharSlice(args, "bar"); ...`
  → `var args @[]@[]char = @[]@[]char{"foo", "bar", ...}`.  Same
  shape for `appendRawCharSlice` (since string literals are
  already `*[]const char`).  When the run mixes constants with
  computed values, leave it alone — the literal form only helps
  for known-static sets.
- **Candidates**: argv construction in build scripts (e.g.
  `cmd/bnc/{main,test,compile}.bn` clang-args setup), test
  scaffolding (anywhere a test builds a known `@[]@[]char`
  fixture), and short fixed sets of import paths.
- **Why bother**: cuts line count, removes a runtime O(n²)
  rebuild pattern (each `appendCharSlice` allocates a new
  slice + copies), and matches the language's expressive
  default instead of the bootstrap workaround.

### Use function values to collapse explicit dispatch shims (opportunistic)
- **Constraint**: function values are unlocked now that
  cmd/bnc is no longer bootstrap-bound; bnc-0.0.1 has the
  function-value machinery (see plan-function-values-phase-3
  in `claude-todo-done.md`).
- **Pattern to look for**: places where we route through a
  `kind` int + a per-kind dispatch table, when the data flow
  would be clearer as "the caller hands us the function it
  wants invoked".  Candidates need a closer look before they're
  fully scoped — function-value adoption isn't always a win
  (each call adds an indirect-call overhead), so this is
  selectively-opportunistic, not blanket.
- **How to land**: TBD; needs concrete site survey.

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

### Replace repeated `WriteStr(literal)` runs with adjacent-string concat (opportunistic)
- **Pattern**: code that builds output via a CharBuf often calls
  `WriteStr` many times with adjacent string literals — e.g.
  `cb.WriteStr("foo"); cb.WriteStr("bar"); cb.WriteStr("baz")`.
  Binate allows adjacent string literals to be concatenated by
  juxtaposition (`"foo" "bar" "baz"`), so a single
  `cb.WriteStr("foo" "bar" "baz")` (split across lines for
  readability) does the same work in one call.
- **Why it matters**: each `WriteStr` call is a method dispatch
  plus a CharBuf grow check.  Collapsing the literals into one
  call cuts both, and is also less code to read.
- **Most of these are in tests**, which compounds with the
  slow-tests theme — every saved WriteStr in a test that runs
  under boot-comp-int-int (or any interpreted mode) saves
  bytecode-dispatch overhead × test count.
- **How to land**: opportunistic, file at a time.  Best
  candidates: `cmd/bnc/test.bn`'s `genTestRunner`, anywhere
  building LLVM-IR text, and test fixtures that paste source
  fragments together a chunk at a time.
- **First pass landed** (binate `07b21ed`, 2026-05-15): 18 files,
  ~200 runs coalesced (`cmd/bnc/test.bn`, `cmd/bnc/util.bn`,
  `cmd/bni/main.bn`, plus check_*_test.bn and emit_*_test.bn /
  gen_*_test.bn in pkg/types, pkg/codegen, pkg/ir).  The
  cmd/bnc/test.bn growth (524 → 533) prompted a follow-up split
  to a new `gen_test_runner.bn` — test.bn now 381 lines.

### Replace if-return chains with `switch` where applicable (opportunistic)
- **Pattern**: code that does
  `if x == A { ... return ... }; if x == B { ... return ... }; ...`
  over many cases.  Common in op-dispatchers, kind-handlers, and
  predicates.
- **Why it matters**: a `switch` makes the structure obvious (all
  cases over the same scrutinee, mutually exclusive), gives the
  type-checker a hook for exhaustiveness checking if/when it
  lands, and reads more naturally.
- **Watch out for**: chains where the conditions aren't really
  equality on a single scrutinee — those genuinely are
  if/else-if and should stay.  Also: the bootstrap subset
  supports `switch`, so this isn't restricted to non-bootstrap
  code (unlike the interface TODO above).
- **How to land**: opportunistic.  Top candidates: the per-op
  dispatchers in `pkg/native/arm64/arm64_dispatch.bn`,
  `pkg/codegen/emit_instr.bn`, `pkg/vm/vm_exec*.bn`, and
  `pkg/ir/ir_ops.bn`'s opName / similar string-form helpers.
- **Landed (2026-05-25/26)**: the big per-op dispatchers are
  converted — `pkg/vm/vm_exec_pure.bn` + `vm_exec_helpers.bn`
  (binate `b4456ab`, `e4e7d29`), `pkg/codegen/emit_instr.bn`
  (`2d6d0f7`), `pkg/native/arm64/arm64_dispatch.bn` (`3756acc`).
  Where a chain mixes equality cases with op-RANGE checks
  (emit_instr's OP_ADD..OP_SHR / OP_EQ..OP_GE; arm64_dispatch's
  emitCompare/emitBinop/emitUnop delegates), the range arms stay
  as guards alongside the switch.  `ir_ops.bn`'s opName was
  already a switch — nothing to do there.  This work flushed out
  a CRITICAL case-scope miscompile (managed local in a `case`
  body), since fixed (`4306197`) — see the FIXED entry above.
  Remaining candidates are smaller / lower-value (assorted
  if-chains in cmd/* and pkg/* tools).


- **Self-hosted (LANDED, 2026-05-01)**: type-checker
  (`pkg/types/check_stmt.bn:checkReturnStmt`) and IR-gen
  (`pkg/ir/gen_stmt.bn` STMT_RETURN branch) accept
  `return f(...)` when `f` returns the matching tuple. Each
  per-result type must be `AssignableTo` the outer's declared
  result. IR-gen lowers to one OP_CALL + one OP_EXTRACT per
  result; the existing return-RefInc/copy + temp-cleanup
  machinery handles ownership transfer. The literal-shape
  coercions in the per-expr return path (OP_CONST_NIL retyping,
  OP_CONST_STRING → string_to_chars, untyped-int width) all
  fire only on literals, which can't be call results — so the
  multi-return path skips them. The one non-literal coercion,
  `@[]T → *[]T` when the outer expects raw, is preserved on
  extracted values, mirroring the per-expr path.
  - Tests: `pkg/types/check_stmt_test.bn` (positive, arity-
    mismatch, type-mismatch); `pkg/ir/gen_stmt_test.bn`
    (`TestGenReturnMultiCallEmitsExtracts` pins
    1×OP_CALL + 2×OP_EXTRACT); conformance
    `347_return_multi_call` (all-scalar + mixed scalar/managed
    end-to-end; was 345 originally, renumbered after collision
    with `345_interface_decl`). xfail.boot. boot-comp /
    boot-comp-int / boot-comp_native_aa64 all green.
- **Bootstrap (pending decision)**:
  `bootstrap/types/checker.go:checkReturnStmt` (~963-978) still
  rejects this shape. Bootstrap acceptance is a separate
  question — the bootstrap subset is intentionally restrictive,
  and the self-hosted toolchain doesn't need this to compile.
  Defer until there's a concrete reason to widen the subset.
- Spec recorded in `claude-notes.md` ("Tail-call return for
  multi-return functions"). `bootstrap-subset.md` notes the
  bootstrap-only rejection.

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
  "int-int slow-package skips" entry below.
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
- **STATUS 2026-06-10 — GREEN** (unit run on `3342460e`): all 8 `builder-comp-int-int` shards pass (2.5–26.7 min) and `builder-comp-int` / `-comp-int` pass. **Margin note**: shard 4/8 ran 26.7 min — ~89% of the 30-min cap; the 8-shard + skip set is sufficient but thin, so if the int-int suite grows it may need a 9th–10th shard or one more skip before it times out again. (The remaining unit reds — `arm32_{linux,baremetal}`, `native_x64` — are separate modes, not this. NOTE: `native_x64` was NOT "WIP" — it was broken by an ELF PC32 reloc bug, fixed 2026-06-14 `dd74c91e`; see the top-of-file native_x64 entry.)

### Function values — MAJOR PROJECT (interop prerequisite)
- **Plan docs**: `explorations/plan-function-values.md` (parent;
  Phase 1 COMPLETE) + `explorations/plan-function-values-phase-3.md`
  (cross-mode trampolines; Slices 3.1, 3.1.5, 3.2, 3.3, 3.4 all
  LANDED).
- **Phase 1 COMPLETE (2026-05-01)**: A.1–A.7 all landed. Type
  syntax, nil + zero-init, function-reference-as-value, calling
  through a function value, flow through args/returns/fields,
  method expressions `T.M`, and non-capturing function literals
  (lifted to synthetic `__funclit_<n>` top-level Funcs).
  Conformance tests 338–342 + 344 cover each slice; pkg/ir + pkg/types
  unit tests cover each coercion site, AssignableTo predicate,
  and capture-rejection. `pkg/ir/gen_call.bn` and
  `pkg/ir/gen_func_lit.bn` extracted to keep file-length hygiene
  clean.
- **Phase 3 LANDED (per plan-function-values-phase-3.md)**:
  cross-mode trampolines bridge compiled ↔ VM through a uniform
  always-shim convention `<ret>(*uint8 data, <args>)`. Compiled
  side: per-function `__shim.<mangled>` set in each `__vt.<mangled>`'s
  `call` slot (Slice 3.1). Common kind-tag at the start of `data`
  (Slice 3.1.5) discriminates `DATA_KIND_VM_CLOSURE_REC` vs
  `DATA_KIND_COMPILED_CLOSURE` (Phase 2). Compiled→VM goes through
  `vm.TrampolineScalar`, a fixed 7-int-arg trampoline that reads
  VM handle + vm_func_idx from the closure rec and dispatches via
  `execFunc` (Slice 3.2). Bytecode→compiled goes through
  `dispatchCompiledFuncValue` (`pkg/vm/vm_exec_helpers.bn:247`),
  which routes via `rt._call_shim_scalar` — a new IR-magic helper
  alongside `_call_dtor` / `_call_free_fn`, lowered to
  OP_CALL_INDIRECT (Slice 3.3). The earlier `5f4333f` cross-mode
  hack for `func(*uint8)` is now reframed as `dispatchNativeIndirect`
  — the BC_CALL_INDIRECT counterpart of BC_CALL_FUNC_VALUE's
  data==null branch (Slice 3.4). VM handle lives in the
  VMClosureRec (not a global), so multi-VM works without ordering
  concerns. Bootstrap-subset constraint: scalars + pointers ≤7,
  no floats, no aggregates — broader signatures need additional
  trampoline shapes when they actually reach this path.
- **Phase 2 DEFERRABLE**: closures + capturing function literals;
  capture design (by-value vs by-ref, mutability, lifetime) is
  its own pass. The bytecode dispatcher (`BC_CALL_FUNC_VALUE`)
  already has a `DATA_KIND_COMPILED_CLOSURE` arm (clear-error
  guard) ready to fill in.
- **Downstream**: Phase 3's machinery is what the
  compiler/interpreter interop project needs. With per-signature
  shims + the `(data, args)` convention, a "package descriptor"
  of function-value pointers is enough to dispatch arbitrary
  cross-mode calls — no per-function hand-coding required. This
  also opens the door to retiring `pkg/vm/vm_extern.bn`'s
  hand-written extern arms (~30 of them, including the
  `rt.RefInc` / `rt.RefDec` arms flagged for retirement above);
  see the Compiler/interpreter interop entry below.
- **Reframed scope**: function values were originally framed as
  "blocked on / a piece of interop." Inverted: data interops fine
  via shared `.bni` layout; what crosses the compiled/interpreted
  boundary at runtime are *exported functions and methods passed
  as values*. The package descriptor the interop work needs is just
  a struct of function values per export. So function values are
  the **upstream prerequisite** for the broader interop project,
  not a sub-item of it.
- **Representation**: 2-word `{vtable, data}`, identical to
  interface values. The vtable type is per-signature; the vtable
  *instance* is per-(function, capture-shape). Vtable layout has
  `dtor` first (matching all other vtables — common destruction
  sequence) and `call` second. Function types are structural —
  `*func(...)` / `@func(...)` — with no user-visible "function
  interface" declaration; the compiler synthesizes the impls at
  function-literal and method-value sites.
- **Frontend syntax**: `*func(int) int` raw / `@func(int) int`
  managed, mirroring the slice migration (`*[]T` / `@[]T`) and the
  proposed interface revision. Bare `func(...)` is not a usable
  type.
- **Upstream prerequisite**: `plan-call-indirect.md` — LANDED.
  The `OP_CALL_INDIRECT` IR op (LLVM + VM + native arm64
  lowerings) is what Phase 1's vtable-indirect call sequence is
  built on. Already exercised end-to-end by RefDec's dtor
  dispatch; this plan's Phase 1 doesn't need to re-invent
  indirect dispatch.
- **Phasing** (per the plan doc):
  - **Phase 1 — backend vtable machinery + non-capturing function
    values.** This is primarily about *building the shared
    interface/vtable backend* (vtable type/instance generation,
    `call`-shim mechanism, vtable indirect-call sequence in
    compiler + VM). Non-capturing function values are the
    smallest user-visible thing the backend can deliver. The same
    machinery is what user-declared interfaces will need at the
    runtime layer. Non-capturing call sites use a check-data-nil
    short-circuit (consistent with other nil-checks in the
    codebase) rather than always going through the shim.
  - **Phase 2 — closures + method values (DEFERRABLE).** Capture
    analysis, closure-struct generation, receiver-capture for
    method values. **Capture design is open** (by-value vs. by-
    reference, mutability semantics, lifetime extension) and is
    its own design pass before implementation. Most current goals
    do *not* need Phase 2; the compiler and self-hosted runtime
    don't write closures, CallDtor retirement doesn't need it
    (see Path B above), and the interop descriptor exposes only
    non-capturing function values. Defer until there's a concrete
    user-facing need.
  - **Phase 3 — cross-mode trampolines.** LANDED. Per-signature
    (currently per-return-shape: TrampolineScalar) trampolines
    bridge compiled ↔ VM through the always-shim convention.
    See plan-function-values-phase-3.md for slice-by-slice detail
    and the "Phase 3 LANDED" bullet above for the LANDED summary.
    Unlocks the broader interop work; doesn't require Phase 2.
- **Recursive lambdas — explicit non-goal for Phase 1.** Go-style
  recursive closures (`var f = func(x) { ... f(...) ... }`) are
  NOT supported. Top-level named recursive functions work as
  always. Y-combinator pattern is the workaround if needed.
  Revisit when Phase 2 capture design is settled.
- **Backend dependency**: function values share the vtable layout
  and dispatch path with interfaces, but **not** the frontend
  interface syntax. They depend on the runtime/codegen vtable
  machinery, not on `plan-interface-syntax-revision.md`. Either
  plan can land first; both share the backend.
- **Method values** (`x.M`, `T.M`) and **closures** are folded
  under this plan rather than tracked separately.

### Interface syntax revision — *Stringer / @Stringer + top-level decl — MOSTLY DONE
- **Plan doc**: `explorations/plan-interface-syntax-revision.md`
  (RATIFIED 2026-05-01).
- **Implementation status (audited 2026-05-22 / 2026-05-23)**:
  Plan §1–§5 all landed.  §6 (`any` universal interface) landed
  end-to-end across type-checker (`e5f2f8a`) and IR-gen + codegen
  (`61eb6cd`): universe `any` is a real empty-method-set
  TYP_INTERFACE registered in both `pkg/types` (via
  `defineInterface`) and `pkg/ir` (via `registerUniverseAny` at
  `InitModule` time). `wrapAsIfaceValue` synthesizes a per-(T, any)
  ImplInfo on demand so codegen emits
  `__ivt.bn_<T_pkg>__<T>__any` as `[1 x i8*]` with T's dtor in
  slot 0 (or null if T has no dtor).  `@any` of a managed-field-
  bearing pointee now RefDec's the pointee's managed fields at
  scope exit via the synthesized vtable's dtor slot — the
  previously-silent leak is closed.
  Verified working: top-level `interface X { ... }` decl
  (`pkg/parser/parse_decl.bn:35`), `*Iface` / `@Iface` syntax
  (`pkg/types/resolve_type.bn:38-50`), bare-name rejection
  (`resolve_type.bn:30-35`, test 348), interface alias
  `interface X = Y` (test 369), construction-site explicit-only
  conversions (`types_assignable.bn:149-189`, tests 379/380/381),
  five receiver kinds + `impl T : Iface` (tests 357–410), per-
  (impl, interface) vtable codegen (`pkg/codegen/emit_impls.bn:24-40`),
  cross-package `.bni` interface visibility (tests 373–388, 464),
  universe `any` (tests 470–474, plus
  `pkg/ir/gen_iface_vtable_test.bn` for vtable-name mangling
  including the empty-pkg form).
- **Remaining (small) gaps**:
  1. **`type X = BareIface` explicit negative test** — the code
     flow should reject via `resolveTypeExpr`'s bare-interface
     error path, but it isn't separately covered. One-line
     negative test.
  2. **Interface-value nil comparison** — `iv == nil` (for any
     iv type, not just `*any`) is currently rejected:
     `IsNillable` in `pkg/types/types_query.bn:196` returns true
     only for pointer types and function-value types.  A nil iv
     IS a meaningful runtime state (both data and vtable slots
     zero, mirroring `*func(...)`'s convention), so the natural
     extension is to add `TYP_INTERFACE_VALUE` /
     `TYP_INTERFACE_VALUE_MANAGED` to `IsNillable`'s positive
     set and check both slots zero at the comparison site
     (codegen + VM lowering for `iv == nil`).  Not a regression;
     pre-existed plan §6 — surfaced while writing a nil-
     propagation test for the iv→any upcast.  This is a real
     language-semantics extension that should be confirmed
     before implementing.

### Cross-package method visibility in `.bni`
- Methods defined on a public type in package `foo` need to be declared
  in `foo.bni` for callers in other packages to see them — analogous to
  the existing `.bni` rules for free functions and types (covered by
  conformance tests 235/236, "Verify .bni vs .bn visibility semantics"
  is DONE).
- Currently, methods *do* work cross-package (conformance 330/331 cover
  it via `pkg/buf.CharBuf` methods called from `main`) because IR-gen's
  `RegisterImport` registers methods from the imported package's `.bn`
  source via the loader. That's a happy accident of the loader path, not
  a deliberate visibility design.
- Open: should `.bni` method declarations be required for cross-package
  visibility (matching free functions / types), and should the type
  checker enforce that? Today methods skip the `.bni` requirement.
- When picking this up, look at: how `pkg/buf.bni` declares its type but
  not its methods, yet cross-package callers still resolve them; whether
  to extend `checkBniSignatureMatch` to methods; whether `.bni` method
  decls are mandatory or just allowed.

### Verify anonymous struct equivalence — edge cases
- Both type checkers now implement structural equivalence for anonymous structs (field names + types in order)
- Needs edge case testing: nested anonymous structs, anonymous struct with managed fields, cross-package anonymous struct equivalence
- See claude-discussion-detailed-notes.md section 22

### Continue backfilling negative conformance tests
- 31 negative tests exist (112, 200-210, 214-221, 235-236, 238-246), covering type mismatches, undeclared vars, wrong args, nil semantics, operators, comparisons, field access, indexing, non-function calls, managed pointer misuse, multi-return, undefined types, .bni/.bn mismatch, visibility, imports, type conversion, const/break/continue/param, package mismatch, missing return, var redeclaration
- `.error` files use `grep -E` regex matching
- **Fixed diagnostics**: assign to const (238), break/continue outside loop (239, 242), duplicate param names (243), var redeclaration in same scope (246)
- **Remaining xfail'd**: missing return (245) — needs control flow analysis
- Bootstrap-only: package name mismatch not detected in single-file mode (244 xfail on boot)
- Still needed: const expression errors, more shadowing edge cases

### Readonly method receivers — deferred (gated on methods/interfaces)
- A method's receiver kind (`*readonly T` / `@readonly T`, plus value
  receivers — which are always readonly) determines which pointer kinds
  satisfy an `impl` and bounds what the method may mutate.  See
  `claude-notes.md` (value receivers always readonly; readonly-restricted
  dispatch expressed at the impl level; `*readonly T` receiver smoothing
  auto-takes `&t` at the call site).
- This was "Stage 3" of the old `const` type modifier.  The rest of that
  work landed and the type-level modifier is now spelled `readonly`
  (`plan-const-readonly.md`, COMPLETE 2026-06-03 — `const` split into
  compile-time `const` / `var` storage / `readonly T` modifier; that
  plan's three listed deferrals — readonly-slice slicing, `.bni`
  extern-var, `&pkg.Const` — are all since resolved).
- Deferred, not abandoned — depends on the methods/interfaces feature.
  Fold into that project's tracking when it firms up.

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

### Switch `fallthrough` — proposal
- Not in the current grammar (`grammar.ebnf`). Binate switch cases are implicit-break (Go-style), but there's no opt-in for Go's `fallthrough` keyword.
- Would add one reserved keyword, one AST statement kind (`STMT_FALLTHROUGH`), and one IR lowering (branch to the next case's entry block, skipping its case-value check).
- Before implementing: decide whether we want it at all. Arguments for: matches reader expectations from Go, lets users avoid duplicated bodies across related cases. Arguments against: rarely needed in practice, adds a new keyword for a small ergonomic win, forces the type checker to recognize terminators beyond `return`/`panic` (termination analysis already inspects case bodies for bare `break`).
- Likely a decline unless a concrete use case comes up, but worth capturing as a live option.

### Termination analysis — labeled break
- Missing-return check (test 245) uses Go-style termination analysis simplified: RETURN terminates; `panic(...)` terminates; BLOCK terminates if last stmt does; IF terminates if both branches do; FOR with no condition and no `break` in body terminates; SWITCH with default and all cases terminating (no break) terminates.
- **Labeled break**: Binate currently has no labels. If/when we add them, termination analysis needs to track labels — a `break L` inside a nested for doesn't break the inner for (contrary to the current "any break disqualifies enclosing for/switch" rule). Revisit when labels are on the table.

### Clean up conformance tests to use array literal + `arr[:]` pattern
- `arr[:]` works in compiled mode; conformance tests using `make_slice` + indexed assignment for static data could use `[N]T{...}` + `arr[:]` instead
- Consider adding slice literal syntax (`*[]T{...}`) as sugar

### DWARF debug info — foundation in place, type coverage missing
**Done** (via `56ea542`, `a15ef50`, `2cd2c25`):
- `-g` flag in `cmd/bnc`, `SetDebugInfo` in `pkg/codegen`; off by default.
- Module-level: `source_filename`, `DICompileUnit` (FullDebug), `DIFile`, `DISubroutineType`, per-function `DISubprogram`.
- Line-level: `Line int` field on `ir.Instr` (`pkg/ir.bni:170`). `genExpr` sets `.Line` from `e.Pos.Line` (`pkg/ir/gen_expr.bn:16`). `annotateBlockInstrs` backfills zero-line instrs to statement line (`pkg/ir/gen_stmt.bn:11-14`). Per-instruction inline `!DILocation(line: N, scope: !M)` in emitted LLVM (`pkg/codegen/emit_debug.bn:99-114`).
- Variables: `llvm.dbg.declare` + `DILocalVariable` for named allocas (`emit_debug.bn:139-162`). Names propagated via `StrVal` on `OP_ALLOC`.
- lldb/gdb now show Binate function names, file, line numbers, and local variable names.

**Gaps**:
- ~~Type coverage is basically just `i64`.~~ FIXED for scalars,
  pointers, structs, slices, interface-values, function-values,
  arrays, and named typedefs (2026-05-07/08).
- ~~Parameters don't get `DILocalVariable`~~ — FIXED (2026-05-07).
  Param allocas were already named so the existing dbg.declare
  fired; step 3 added `arg: <N>` so lldb shows them as function
  arguments rather than mixed in with locals.
- ~~`DISubprogram` has `line: 0` and `scopeLine: 0`~~ — FIXED
  (2026-05-07). `ir.Func` carries a `Line` field; gen_func.bn
  populates it from the AST decl's `Pos.Line`; emit_debug.bn
  threads it into both the `line:` and `scopeLine:` fields.
  Synthetic helpers (init dispatcher / entry wrapper / dtor /
  copy stubs) keep `line: 0`.
- ~~`DISubroutineType` is a single shared generic~~ — FIXED
  (2026-05-09). Per-function DISubroutineType + types tuple
  emitted; void/nullary funcs get `!{null}`, parameterised funcs
  get `!{<ret-or-null>, <param1>, ...}` referencing the type
  registry. See step 7 below.
- No `llvm.dbg.value` (only `dbg.declare` for allocas).
- Line positions: only `genExpr` explicitly threads `.Line`; most IR-emission sites rely on statement-line backfill (coarse). No columns.

**Reasonable next steps** (roughly ordered by effort/payoff):
1. ~~Emit `DIBasicType` for each scalar kind~~ — DONE (2026-05-07).
   Unit tests in `pkg/codegen/emit_debug_test.bn` pin the slot
   layout (`TestDbgTypeIDScalars`), the emitted DIBasicType nodes
   (`TestEmitDebugBasicTypesEmitted`), and the `dbg.declare` →
   slot wiring (`TestEmitDebugDeclareReferencesScalarType`). Full
   conformance (boot-comp, 317/0) compiled with `BINATE_FLAGS=-g`.
2. ~~Capture function definition lines into `DISubprogram`~~ —
   DONE (2026-05-07). `TestEmitDebugSubprogramLine` pins
   `line:` / `scopeLine:` for two functions on different source
   lines; `TestSyntheticFuncDefaultLineZero` pins the synthetic
   `Line == 0` invariant.
3. ~~Emit `DILocalVariable` for parameters~~ — DONE (2026-05-07).
   Step actually emitted `arg: <N>` on the existing DILocalVariable
   for params (vs. the gap entry's premise of "no dbg.declare for
   params" — the dbg.declare was already firing once defineVarParam
   tagged the alloca). Tests:
   `TestEmitDebugDeclareParamsCarryArgIndex`,
   `TestEmitDebugMethodReceiverIsArgOne`,
   `TestParamAllocaParamIndex`.
4. ~~Emit `DICompositeType` for structs / `DIDerivedType` for
   pointers~~ — DONE (2026-05-08). `pkg/codegen/emit_debug_types.bn`
   carries a per-module type registry keyed by structural string
   (raw vs managed pointers distinguished); ids allocate past the
   per-function metadata block. Recursive interning means a
   `*Counter` local pulls in Counter's struct nodes; field types
   route back through `dbgTypeID` so scalar fields wire to !5..!15.
   Tests in `emit_debug_types_test.bn` cover pointer + struct
   emission, the pointer-to-struct chain, the dedup invariant, and
   the structural-key helper. Full conformance under -g: 327/0.
5. ~~Wire slices, managed-slices, interface-values, function-values,
   arrays, and named typedefs into the registry~~ — DONE
   (2026-05-08). New `pkg/codegen/emit_debug_aggr.bn` carries
   intern + emit functions for each kind. Slices map to
   DICompositeType DW_TAG_structure_type with the runtime layout
   (2-word for raw, 4-word for managed); iface and func values
   map to 2-word DICompositeType; arrays map to DICompositeType
   DW_TAG_array_type with DISubrange(count:); named typedefs map
   to DIDerivedType DW_TAG_typedef. Tests in
   `emit_debug_aggr_test.bn`. Full conformance under -g: 327/0
   (1 unrelated xfail). NOTE: TYP_NAMED rarely surfaces in
   today's IR-gen because `type Pos int` is currently treated
   as an alias and unwrapped before reaching the alloca's
   TypeArg; the typedef path is in place for when distinct-
   named-type semantics land.
6. Thread positions through more IR-gen sites (statements, assignments, calls) for finer-grained `DILocation`.
7. ~~Per-function `DISubroutineType` with real parameter + return
   types~~ — DONE (2026-05-09). `setupDbgFuncSubroutineTypes`
   allocates a (typesList, subrType) id pair per non-extern Func
   and eagerly interns each function's param + return types so the
   tuple resolves; `emitDbgFuncSubroutineTypes` writes both nodes
   after the per-function metadata block. DISubprogram now
   references the per-func DISubroutineType instead of `!4` (the
   legacy shared empty placeholder remains for backwards compat).
   Tests in `emit_debug_test.bn`:
   `TestEmitDebugSubroutineTypePerFunc` (non-!4 + `!{!5, !5...}`
   shape), `TestEmitDebugSubroutineTypeVoidNullary` (`!{null}`),
   `TestEmitDebugSubroutineTypeVoidWithParam` (`!{null, !5}`).
   Full conformance under -g: 327/0 (1 unrelated xfail).

### Package manager — sketch a design
- We don't have one yet. The current model is "everything lives under a
  root directory; `-I` and `-L` point the loader at extra search paths."
  Fine for the toolchain and a handful of conformance fixtures; doesn't
  scale to "I want to depend on `someone/foo` at version vX."
- Questions a sketch should answer:
  - Naming: are packages identified by URL (`github.com/...` Go-style),
    by a registry name, by a flat namespace? Interacts heavily with the
    package-name/path conventions item below.
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
  not implementation. Decisions are interleaved with the name/path
  conventions item below — sketch and conventions probably ratify
  together.

### Package name/path conventions — decide and possibly reorganize
- Current `pkg/` layout mixes toolchain internals (`pkg/parser`,
  `pkg/types`, `pkg/codegen`, …) with runtime (`pkg/rt`), bootstrap
  support (`pkg/bootstrap`), libc bridges (`pkg/libc`), and small
  utilities (`pkg/buf`, `pkg/mangle`, …). Future stdlib packages would
  pile in alongside them with no organizing principle.
- Questions to answer:
  - Should toolchain internals live under a distinct prefix
    (`compiler/parser`, `compiler/types`, …) so that "what's stdlib"
    vs. "what's compiler implementation" is visible at the import
    path? Same question for runtime / bootstrap support.
  - What does a Binate package path *look* like? Is `pkg/` a real
    prefix or just a directory convention? Are external (third-party)
    packages spelled differently?
  - How do package paths interact with the package manager's naming
    scheme (URL? registry name? short alias)?
  - Mangling: short package names (`mangle.PkgShortNameFromModule`)
    currently derive from the path's last segment. If conventions
    change, mangled symbol names change, which affects ABI. Plan a
    migration story.
  - Are there packages that should move? `pkg/bootstrap` is arguably a
    stdlib piece; `pkg/rt` is closer to runtime-internal; toolchain
    internals could become `compiler/...`. Each move is a real refactor.
- Heavily entangled with the package-manager sketch — they should
  probably ratify together, since the manager design depends on what
  paths look like.
- Output: a plan / decision doc in `explorations/`. Reorganization is
  a follow-up project.

### Tier + dependency-direction hygiene checks (enforce `pkg-layout-spec.md`)
- **What**: a hygiene check (new script under `scripts/hygiene/`, alongside
  `conformance-imports.sh`) that enforces the tier dependency-direction rule
  from [`pkg-layout-spec.md`](pkg-layout-spec.md): a package may import only
  packages at its own tier or **lower**; importing a strictly-higher tier is
  a violation.  Tiers, low→high: 0 / 0b (`pkg/builtins/*`) < 1 (`pkg/std/*`)
  < 1x (`pkg/stdx/*`) < 2 (`pkg/<org>/*`, e.g. `pkg/binate/*`) < 3
  (app-specific).  E.g. `pkg/builtins/rt` importing `pkg/std/io` is illegal;
  `pkg/binate/parser` importing `pkg/std/os` is fine.  (This is the runtime
  enforcement of the spec's "Transitive constraint" + tier table.)
- **Special case — `pkg/std` → `pkg/stdx`**: tier 1 (`std`) may depend on
  tier 1x (`stdx`) **internally** (in `.bn` impl files) but **not externally**
  (in `.bni` interface files).  A `.bni` importing `stdx` would leak a
  no-inter-version-compat (1x) type into `std`'s strict-compat (tier 1)
  surface.  So the check must scan `.bni` imports separately from `.bn`
  imports: the std→stdx edge is allowed only from `.bn`.  (Generalize if
  other interface-vs-impl tier asymmetries surface.)
- **How**: derive each package's tier from its path — the realized layout
  makes tier path-derivable (`ifaces/core` + `impls/core/*` → tier 0/0b;
  `ifaces/stdlib/pkg/std` → tier 1, `…/pkg/stdx` → tier 1x; `pkg/binate/*`
  → tier 2).  Walk every package's imports (split by `.bni` vs `.bn`), map
  importer + imported to tiers, flag any higher-than-self edge, applying the
  std/stdx interface refinement.  A whitelist file (cf.
  `conformance-imports.whitelist` / `naming.whitelist`) covers sanctioned
  exceptions.
- **Scope** (per CLAUDE.md "Stay Within the Asked Scope"): add the script
  only; wiring it into `scripts/hygiene/run.sh` and CI is a separate decision
  for the user.

### Per-file build constraints — conditional file inclusion/exclusion by target — DESIGN
- **STATUS — arch/os MVP IMPLEMENTED + LANDED.** The `#[build(EXPR)]`
  mechanism is live with the minimal `is(arch, …)` / `is(os, …)` vocabulary
  (membership form, bnas-aliased), gating at all four granularities: file
  (package clause), declaration, import, and `.bni` interface decls. The
  active config defaults to the host (read from `pkg/builtins/build` via
  `loader.ResolveBuildConfig`), overridable per `--target`. Landed across
  binate increments through `c7249552` (`.bni` gating + the `loader.bn` /
  `MergeFiles` split + conformance 746/747; the aliased-import fix `52d1c832`
  + coverage 738/745 was a detour surfaced en route). Conformance:
  731 (file), 733/735/736 (decl: const/var/type/func), 737 (import), 746
  (`.bni` decl), 747 (whole-`.bni` drop, negative). See
  [`plan-build-constraints.md`](plan-build-constraints.md) for the full
  status. **Still deferred** (each its own follow-up, none started):
  vocabulary beyond arch/os (`triple`/`backend`/`libc`/`ptrsize`/`version`
  with `is`/`at_least`/`at_most`), `bnlint --target`, main-module gating,
  migrating the `impls/` duplicate trees onto constraints, and the separate
  inline-asm (`#[asm]`) doc.
- **Concrete proposals**: see [`plan-build-constraints.md`](plan-build-constraints.md) — generalized per the user from *per-file* to **per-declaration** conditional compilation via a first-class `#[build(EXPR)]` annotation on any top-level decl (`const`/`type`/`var`/`func`/`package`/`import`); the `#[...]` grammar already reserves an `[ Annotation ]` slot on every top-level form (only `PackageClause` lacks it) and the attachment + `compiler.*`/`tool.*` namespacing are decided. Covers the predicate model + expression semantics (closed typo-checked vocab; ordered comparisons for `ptrsize`/`intsize`/`version`/`os.version`; hard-error on unknown/malformed/not-yet-wired), two gate seams (pre-parse file-level + post-merge/pre-resolve decl-level), disjoint variant definitions / conditional imports / conditional `.bni` decls (relaxing Invariant 1), the impls/-tree relationship + migration, tooling (bnlint `--target` now necessary; `tool.lint` lint-exempt), and a phased roadmap. Inline asm (`#[asm]`) is deferred to its own sibling doc that composes with this substrate.
- **What**: a way for a single file to opt *itself* in or out of
  compilation based on the build configuration — arch, target triple,
  OS, libc-vs-freestanding, backend (LLVM / native-aa64 / native-x64),
  engine (`bnc` compiled vs `bni` interpreted), etc.
- **Why the current mechanisms are inadequate**:
  - **Separate trees + symlinks** (what we have now —
    `impls/{common,libc,baremetal}/…`, per
    [`pkg-layout-spec.md`](pkg-layout-spec.md) invariant 5 "Whole-package
    selection only"): too **coarse** (selection is whole-package /
    whole-variant-dir; "shared core + one per-variant file in the same
    package" is unrepresentable) and too **annoying** (symlinks to share
    the common files across variant dirs; a new axis means a new tree).
  - **Go-style filename suffixes** (`foo_posix.bn`, `foo_arm32.bn`): too
    **magical** (the constraint is invisible *inside* the file, smuggled
    in via the name) and too **coarse** (only a fixed suffix vocabulary;
    can't express conjunctions/disjunctions like "arm32 AND libc", or
    "any of {x64,aa64} but not baremetal").
- **Proposed shape**: an **annotation (writ large) near the top of the
  file** declaring the file's applicability condition as an *expression*
  over target predicates (`arch == "arm32"`, `libc`, `engine == "bni"`,
  with `&&` / `||` / `!`).  Two candidate syntactic forms to weigh:
  - a real **annotation on the `package` clause** (e.g.
    `#[build(arch == "arm32" && libc)] package foo`) — first-class,
    grammar-integrated, parseable; but the file must parse far enough to
    read it before we know whether to compile it, so the condition has to
    be evaluable from a cheap leading-prefix scan (read annotation →
    decide → continue or drop the file);
  - a **comment-form pragma** (a recognized leading comment, e.g.
    `//bn:build arch == "arm32" && libc` — Go-`//go:build`-shaped but
    expression-based, not suffix-based) — even cheaper to scan, but
    out-of-grammar / more "magical".
- **Design questions**:
  - **Predicate vocabulary + authority**: arch, triple, OS,
    libc-vs-freestanding, backend, engine, possibly user-defined build
    tags.  Where is the canonical list defined?  How extensible?
  - **Relationship to the `impls/` trees**: does this *replace* the
    `{common,libc,baremetal}` split (collapse back toward one tree, files
    self-select) or *complement* it (trees for the coarse axis,
    annotations for the fine)?  At minimum it should retire the symlink
    workaround; possibly the per-variant impl dirs too.  Decide
    explicitly — interacts with `pkg-layout-spec.md`.
  - **Loader/merge interaction**: excluded files simply don't join the
    merged package; ensure a package can still be legitimately empty (or
    require ≥1 surviving file) for a given target without spurious errors.
- **Tooling interaction (the bnlint question)**:
  - bnlint + the hygiene scripts must **understand** the annotation, so a
    file inapplicable to the current config isn't false-flagged (and so
    they can choose to lint each file under its applicable config(s)).
  - **Corollary worth designing in**: the same annotation surface could
    carry a directive telling bnlint / hygiene checks to **skip or ignore**
    a file (or regions of it) — a first-class "lint-exempt this file"
    mechanism, unifying build-constraints and lint-control under one
    annotation vocabulary.
- **Related entries to unify with**: the MAJOR "Better test-mode/target
  annotation than `.xfail`" entry above wants exactly this shape for
  *tests* (declare applicable modes/targets); and "Annotations and C
  function interop" below is the general annotation-syntax design.  This
  is the *source-file* instance of the same idea — design them together.
- **Prior art to consult**: Go build constraints (the `//go:build`
  expression form that replaced the `_GOOS` suffix era), Rust
  `#[cfg(...)]` / `cfg_if!`, Zig comptime target switches.  The
  expression form is the model.

### Conformance tests: consider a separate repo
- Running conformance tests in CI creates a circular dependency: the bootstrap repo needs the binate repo (which contains the test cases), and the binate repo needs the bootstrap binary (to run the tests)
- Consider moving conformance tests to their own repo (e.g., `binate/conformance`) that both repos reference
- This also gives a natural place for test infrastructure (run.sh, runners, xfail metadata) that doesn't belong to either the bootstrap or self-hosted repo
- The unit test runner (`binate/scripts/unittest/`) has a similar issue — it's in the binate repo but the `boot` mode runs via Go in the bootstrap repo

### Language spec(s) — write the primary spec; later, secondaries
- See `claude-notes.md` § "Language specification — primary spec is
  minimal — DECIDED" for the philosophy.
- **Primary language spec**: syntax, type system, semantics, plus
  *only* the packages intrinsically tied to the language
  implementation — `pkg/rt` (after the review below) and a future
  reflection/introspection package. Includes the one-line note that
  user files cannot be named `*_test.bn` (reserved).
- **Minor secondary spec — testing**: `_test.bn` packaging
  convention + `pkg/builtin/testing`. May fold into primary; TBD.
- **Major secondary spec(s) — stdlib**: I/O, containers, formatting,
  string utilities, etc. Probably split across multiple specs by
  area.
- **Not started.** Discussion-only at this point. When writing
  begins, the natural artifact is `explorations/spec-*.md` (or a
  separate `spec/` directory). The primary spec is gated on the
  pkg/rt review entry below, since the primary spec describes
  pkg/rt's normative surface.

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

### Standard library design
- Candidates: growable collections (Vec[T], Map[K,V] post-generics), I/O abstractions, string utilities, formatting
- CharBuf is implemented (pkg/buf); broader stdlib design should inform future collection APIs

### Test runner improvements
- ~~**Better docs/help**~~: DONE. Both runners show description, examples, flag docs, test format/convention docs, xfail mechanism. READMEs added for conformance/ and scripts/unittest/.
- ~~**Better output**~~: DONE. `-v` (verbose: all test names), `-q` (quiet: failures+summary only), default (dots for passes, detail for failures).
- ~~**Mode sets in files**~~: DONE. `scripts/modesets/` directory with one file per set (basic, all, full). Adding a new mode set is just adding a file. Both runners read from the shared directory. Help output dynamically lists available sets.
- ~~**Better mode specification**~~: DONE. Comma-separated modes (`boot,boot-comp`) expand into sequential runs. Works alongside mode set files.
- ~~**Better filtering (unit tests)**~~: DONE. Fixed unit test runner to use substring match (was exact match). `token` now matches `pkg/token`, consistent with conformance runner.
- **Better filtering (individual test functions)**: ability to specify individual test functions, not just packages (e.g., `run.sh boot-comp pkg/ir TestFoo`).
- **Timeout/hang handling**: better and/or automatic detection and handling of tests that hang.
- **Parallelization**: consider running test packages in parallel within a mode.

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

### Compiler/interpreter interop — MAJOR PROJECT
- **Why this is high priority**: dual-mode execution is a core promise of the
  Binate language. Compiled-and-interpreted code calling each other (in both
  directions) is what makes "compile some packages, interpret others" actually
  useful. We should make this real BEFORE pushing on more language features —
  large language additions risk locking in design choices that close off
  interop options.
- **Likely-already-compatible substrate** (verify rather than redesign):
  - **In-memory layout of types** is supposed to match across modes. Compiler
    uses `pkg/types`'s SizeOf/AlignOf/FieldOffset; interpreter uses (or should
    use) the same. Verify with a small cross-mode struct-pass test.
  - **Refcounting**: managed allocations carry a header with refcount and a
    pointer to the destructor, populated at allocation site. Compiled and
    interpreted code use the same `rt.RefInc` / `rt.RefDec` / `rt.Free`. Free
    paths invoke the per-type dtor through the header, so a managed value
    allocated on one side and dropped on the other should clean up correctly.
    Verify with a cross-mode managed-pointer round-trip.
- **Direction to start with**: interpreted code calling compiled code. Simpler
  than the reverse (no need for the compiler to plant trampolines into a
  running interpreter). Once that works, compiled code calling interpreted
  code falls out roughly symmetrically.
- **Granularity: package-level.** For interpreted code in package P to call
  into a compiled package Q, the interpreter needs:
  - Q's `.bni` (so the interpreter can type-check P against Q's signatures —
    this already works today via the existing `.bni` loading path).
  - **Pointers to Q's compiled functions** (the actual interop primitive).
- **Proposed mechanism: auto-generated package descriptor.** The compiler emits,
  for each package Q, a synthetic `const` of a synthetic struct type — call it
  e.g. `foo.Package` (working name; could be `foo.PackageImpl` or another
  canonical name) — whose fields are pointers to Q's exported functions in some
  canonical order (e.g., sorted by mangled name). The interpreter, when it
  loads compiled package Q, reads that descriptor and binds each field as the
  function value for the corresponding name in Q's scope. Naming and layout
  must be canonical so an interpreter built against Q's `.bni` can read Q's
  descriptor without further metadata.
- **Symmetry**: the interpreter should produce the same shape on its own end —
  for each interpreted package, expose a `foo.Package` whose function-pointer
  fields are trampolines into the interpreter (call into the bytecode VM
  using the trampoline's bound bytecode/closure-env/types/aliases). That way
  compiled code calling interpreted code is the same mechanism, mirrored.
- **Prerequisite — DONE**: function values (see
  `plan-function-values.md` + `plan-function-values-phase-3.md`).
  The descriptor's fields are pointers to functions — that's
  exactly what function values are. The 2-word `{vtable, data}`
  representation, the `(*uint8 data, <args>)` always-shim
  convention, the per-function `__shim.<mangled>` shims, the
  bytecode-side `dispatchCompiledFuncValue` (via
  `rt._call_shim_scalar`), and the compiled-side `TrampolineScalar`
  are all in place. The remaining work is the descriptor itself
  (naming, layout, emission, loading) plus the symmetric VM-side
  emission for interpreted packages — pure plumbing; no new
  trampoline machinery needed.
- **Adjacent cleanup, lighter-weight first step**: see the
  "VM extern dispatch: name → function-value registry" entry
  above. A per-VM name → function-value registry with manual
  registration (no descriptor design needed) replaces
  `pkg/vm/vm_extern.bn`'s hand-coded switch via the same
  `dispatchCompiledFuncValue` path Phase 3 already provides.
  Auto-generated descriptors are the more general form of the
  same idea — the registry stays as the manual-registration
  escape hatch for host-only externs that have no Binate-side
  `.bni` package.
- **Design open questions** (need a writeup before implementation):
  - Canonical name for the descriptor — `foo.Package` reads naturally but
    risks conflicting with user names. `foo.PackageImpl` or a reserved-prefix
    name (`__pkg_foo`)? Reserve a keyword?
  - Canonical layout — sort by mangled name? By declaration order in `.bni`?
    Layout must be agreed-upon by the descriptor's emitter and reader.
  - Interaction with import aliases (`import alt "pkg/foo"`) and blank imports
    (`import _ "pkg/foo"`) — see the "Import aliases and blank imports" entry.
  - What does the descriptor look like for the package being compiled itself
    (the "self" descriptor)?
  - How are package-level globals exposed? Functions are the obvious starting
    point; globals are a separate (but related) interop question.
  - Versioning: if Q's `.bni` and Q's compiled descriptor disagree (different
    function set, different layout), how do we detect and report it?
- **Adjacent in-flight work that affects this**:
  - "Function values — MAJOR PROJECT" (above) and
    `plan-function-values.md` — direct prerequisite. Phase 3 of
    that plan delivers the cross-mode trampoline machinery this
    work consumes.
  - "Free-function pointer in managed-allocation header — bug"
    (above, DONE within a single mode) — Free now dispatches through
    `header[1]`. Cross-mode allocate-on-one-side / free-on-the-
    other still requires Phase 3's trampolines to translate
    `header[1]` between the C-pointer and VM-index conventions.
  - "Lift function-name qualification into IR" (above) — would simplify name
    resolution at the interop boundary.
  - "Import aliases and blank imports" (below) — affects how the descriptor
    is named at the import site.
- **Suggested next step**: write a design doc (e.g.
  `explorations/plan-compiler-interp-interop.md`) that nails down the
  descriptor name/layout, walks through one concrete cross-mode call end-to-
  end on each side, and identifies the first concrete code change to make.
  Don't start implementation until the design is reviewed.

### REPL refactor: embeddable component for non-CLI hosts — DESIGN RATIFIED, not started
- **Status (2026-06-02)**: design decided; see
  [`plan-repl-embeddable.md`](plan-repl-embeddable.md) for the full
  staged plan, API, and ratified decisions. The old open "which shape
  (a/b/c)" question is resolved: **push session** (host owns the read,
  engine exposes `Init`/`Step(line,eof) → StepResult`), with the
  interrupt **seam designed-in but unimplemented** in v1 and
  suspend/break staged behind it.
- **Why**: today the REPL is welded to stdin/stdout via
  `bootstrap.{Read,Write}` and a blocking `for{}` loop — can't embed
  into a wasm worker (I/O over message ports; must yield to the event
  loop while awaiting input), nor into test harnesses / IDE hosts.
- **Decided shape** (full rationale in the plan doc): push, not pull
  (wasm can't block on inbound `postMessage`); `ReplIO` is a struct of
  `@func` fields, not an interface; user-program output (category B) is
  redirected by **rebinding the `bootstrap.Write/Read/Exit` externs**
  (no user-code recompile); REPL-framing output (category A) routes
  through the host `ReplIO`; engine extracted to **`pkg/binate/repl`**
  (tier-2); **single live session per process** in v1 (multi-session is
  a tracked blocker — next entry); interrupt layer is **seam-only** in
  v1.
- **Staged v1** (each independently landable, green): (1) session struct
  + re-entrancy; (2) `NewReplSession` constructor (errors as values, no
  `Exit`); (3) `ReplIO` sink + extern rebind; (4) push `Init`/`Step` +
  extract `pkg/binate/repl`; (5) inert interrupt seam.
- **Future, gated**: continuable-suspend (Stage 6; partially gated on
  `plan-bni-heap-frames.md`) and break/unwind (Stage 7; needs new IR-gen
  cleanup landing pads — a frame-discard break LEAKS, so it is
  forbidden without them).
- **Out of scope** (raised, not deferred silently): running the
  type-checker + IR-gen + VM under wasm32 in-worker — necessary for B1
  but separate from this I/O-shape refactor; its own open scope question
  for `plan-wasm-browser.md`.

### REPL: remove process-global session state (multi-session blocker)
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

### REPL — All five tiers LANDED (2026-05-29)
- **Status**: `bni --repl <file.bn|dir>` ships.  `plan-repl.md` is
  the live source of truth for per-step state — commit tables,
  verified behaviors, deviations from the original plan, and the
  per-tier remaining-follow-ups list.  Briefly:
  - **Tier 1 (load-then-poke)** LANDED.
  - **Tier 2 (top-level decls at the prompt)** LANDED in full,
    including the body-introduced dtor-regen follow-up landed
    2026-05-28 (`EnsureReplBodyHelpers`).  Every top-level decl
    kind supported by the language works at the prompt: `func`
    (incl. methods, redefinition replace + shadow), `const`
    (single, untyped, grouped), `var` (typed,
    untyped-with-literal-init, with init), `type` (aliases,
    named non-struct, structs incl. managed-field).  Bodies that
    introduce a fresh managed-aggregate shape with a destructible
    element (e.g. `@[]@Bag`) have their helper emitted before the
    body lowers.
  - **Tier 3 (forward refs)** LANDED for `func` decls.  Pending
    types / vars / consts (need a structural treatment of
    "unsized" type symbols) are deferred.
  - **Tier 4 (redefinition)** LANDED for both replace and shadow
    paths, free funcs and methods.
  - **Tier 5 (mid-session imports)** LANDED 2026-05-29 via
    `78685ac3`.  `import "pkg/foo"` at the prompt loads pkg/foo
    transitively, type-checks, IR-gens, lowers, and defines the
    package symbol in the session scope.
- **Remaining REPL work**, per plan-repl.md:
  - ~~**Tier 3**: pending types / vars / consts; cycle
    detection.~~  **ALL STAGES LANDED** 2026-05-28 → 2026-05-29
    via 9 commits on main; see
    [`plan-repl-tier3-pending-types.md`](plan-repl-tier3-pending-types.md)
    for the per-stage commit table.  Every top-level decl
    kind parks on forward-referenced dependencies; use-site
    propagation works through sized contexts (struct field,
    var decl, func sig, composite literal, impl recv, method
    receiver); per-caller sized-vs-reference distinction
    preserves recursive types via pointers; cycle detection
    catches genuine cycles through sized fields with a clean
    `pending cycle: A -> B -> A` diagnostic.
  - **Tier 4**: refcount-aware shadow warning (today fires
    unconditionally); forced-shadow escape hatch (syntax TBD per
    `claude-notes.md`).
  - ~~**Tier 5**: loader entry point for "load this one package
    now."~~  LANDED 2026-05-29 — `evalReplImport` in
    `cmd/bni/repl_import.bn` drives it via the session loader's
    existing LoadImports (plus a SaveAliasMapState /
    RestoreAliasMapState bracket around the per-package InitModule
    loop so the main alias map survives the wipes).
  - **Pretty-printer** (`pkg/replprint`) — **deferred** until
    interfaces land.  `bootstrap.println` is a temporary hack;
    building features on top of it would entrench it.
- **Why this matters now**: the REPL is an explicit core goal in
  `claude-notes.md` (see "Forward references & REPL model — DECIDED"
  and the dual-mode rationale in
  `claude-discussion-detailed-notes.md` § 11 / § 23). Its semantics
  are largely *already decided*; what's not decided is the
  toolchain shape. Writing it down now so that adjacent decisions
  (function values, interop descriptors, layout extraction, IR
  cleanup) get checked against REPL feasibility before they land
  — and so that interpreter-only REPL work can start in parallel,
  since most of it overlaps with the audit work the interop story
  already needs.
- **Already-decided semantics** (do NOT relitigate here — see
  `claude-notes.md`):
  - **Retained mode** (definitions) — parsed and stored, validation
    deferred until dependencies are met. Source files are entirely
    retained mode.
  - **Immediate mode** (bare expressions / statements at the prompt)
    — fully checked at entry, can reference validated retained defs.
    Top-level scope in source files is declarative-only; bare exprs
    are REPL-only.
  - **No forward declarations.** Deferred validation handles forward
    references. Errors surface at use, not at definition.
  - **Redefinition**: *compatible* (same sig) → replace; *incompatible*
    (different sig) → shadow with refcounted old-def retention; warn
    on outstanding refs at shadow time. Forced-shadow escape hatch.
  - **Hot-swap of interpreted functions while a compiled binary runs**
    — fall-out of the thunk model.
- **What the VM is/isn't rigid about** (corrects an earlier overstatement
  in this entry):
  - **`BC_CALL` is name-resolved per call, not idx-baked.** Bytecode
    stores a per-VMFunc strings index for the callee's qualified name;
    `LookupFunc` walks `vm.Funcs` by name on every call
    (`pkg/vm/vm_exec.bn:418-421`). That makes replace-redefinition an
    in-place body swap and shadow-redefinition an append-then-shadow,
    both nearly free given `@VMFunc` already being managed.
  - **`vm.Funcs` is already incremental.** `LowerModule` is called
    per-module and appends; multiple modules already coexist in one
    VM with their own preserved string pools (`pkg/vm/lower.bn:42`).
    Globals are also append-only via `materializeGlobals`.
  - **The frontend pipeline is module-shaped, not declaration-shaped.**
    Loader, parser, type checker, and IR-gen are entered per-package;
    there's no "type-check this single decl against an existing scope"
    entry point. Forward refs work today only because the whole module
    is parsed before checking.
  - **Type checker has no concept of pending.** Errors fire immediately
    on undefined names. Deferred validation (the "retained" half of
    the model) is real new infrastructure.  *(Now: Tier 3 added a
    pending queue (`check_pending.bn`) for `func` decls; types / vars
    / consts still fire immediately.)*
  - **No pretty-printer for arbitrary values.** `println` covers char
    slices and primitives only.  *(Still true; deferred — see above.)*
  - **`LookupFunc` is a linear scan.** Fine today; will matter if REPL
    workloads run real volumes of calls. Easy to fix (name → idx hash)
    and worth doing before Tier 1 ships, since the alternative
    (bake-idx-into-bytecode) would close off the redefinition story.
    *(Now: Tier 4 substrate (`9af2d56`) added the funcIndex hash;
    `LookupFunc` is O(1).  Eager CallCache fill keeps shadow
    semantics correct.)*
- **Tiered plan** (each tier shippable on its own; see
  `plan-repl.md` for entry-point names, per-step commit tables,
  and the live follow-up state):
  1. ~~**Load-then-poke.**~~ **LANDED (2026-04-30).** Load a `.bn`
     module the normal way; prompt accepts immediate-mode entries.
     Multi-line input via paren-aware accumulator.  Auto-`println`
     wrap of bare exprs deferred (gated on interfaces).
  2. ~~**Add new top-level decls at the prompt.**~~ **FULLY LANDED
     (2026-04-30 → 2026-05-28).**  All decl kinds: `func` (incl.
     methods), `const`, `var` (typed + untyped-with-literal-init +
     var-initializer evaluation), `type` (aliases, named
     non-struct, structs incl. managed-field).  Body-introduced
     new-managed-aggregate dtor regen also landed (2026-05-28,
     `EnsureReplBodyHelpers`).
  3. ~~**Forward references.**~~ **LANDED for `func` decls
     (2026-05-05).**  Pending-validation queue in the type checker;
     parked decls retry on every newly-resolved name.  Pending
     types / vars / consts remain (see follow-ups above).
  4. ~~**Redefinition.**~~ **LANDED in full (2026-05-01 →
     2026-05-05).**  Compatible-sig: in-place rebind keeps
     CallCache valid.  Incompatible-sig: `LowerOneFuncShadow`
     appends + re-points funcIndex; old callers retain old VMFunc
     via eager-filled CallCache.  Methods follow the same rules,
     keyed on qualified `<pkg>.<TypeName>.<Method>`.  Substrate
     `9af2d56`; shadow `63cc49b`; method redef `026ad22`.
     Refcount-aware shadow warning + forced-shadow escape hatch
     are remaining follow-ups.
  5. ~~**Mid-session imports.**~~  **LANDED** 2026-05-29 via
     `78685ac3`.  evalReplImport in cmd/bni/repl_import.bn
     drives the existing loader's LoadImports for incremental
     transitive loads, brackets the per-package InitModule
     loop with SaveAliasMapState/RestoreAliasMapState so the
     session's main alias map survives, and routes through
     c.RegisterReplImport to make `foo.X` resolvable from
     subsequent prompt entries.
- **What's free / "should-do-now-anyway"**:
  - ~~The audit itself~~ — done; `plan-repl.md` is the live doc.
  - ~~Per-decl entry points exposed opportunistically when the
    relevant code is touched for unrelated reasons.~~  Done as part
    of Tier 1 + Tier 2 (parser ParseExpr / ParseStmtList /
    ParseTopLevelDecl / IsAtTopLevelDecl; types CheckExprInScope /
    CheckStmtListInScope / CheckDeclInScope / CheckMainPersistent;
    ir GenSyntheticFunc / GenDecl; vm LowerOneFunc / CallByVMFunc).
  - ~~Name → idx hash in `LookupFunc`.~~  Solved differently:
    per-VMFunc CallCache (commit `6c8e0c0`) memoizes the lookup
    result per call site, removing the per-dispatch scan; lazy fill
    on first call; explicitly designed for REPL invalidation.
  - A minimal pretty-printer (probably `pkg/replprint`, leaning on
    `pkg/buf.CharBuf`). Useful well beyond REPL.  **Deferred until
    interfaces land** — `bootstrap.println` is a temporary hack
    scheduled for removal; building features on top of it would
    entrench the hack.  See "Pretty-printer" in plan-repl.md and
    the auto-`println` deferral note.
- **Decisions / non-decisions in adjacent work to pressure-test**:
  - **Function values** (`plan-function-values.md`): a function value
    must be a *stable identity for what it refers to*, not for the
    bytes of the underlying body. Re-binding the body of an
    interpreted function does not invalidate function values pointing
    at it. Add this clause to that plan when it moves out of DRAFT.
  - **Compiler/interpreter interop** (above): the package descriptor
    is shaped right for REPL — interpreted-package descriptors are
    mutable, compiled ones are read-only. Sorted-by-mangled-name
    layout interacts with "add a new exported function mid-session"
    (positions move when a new export sorts in); confirm that's the
    intended behavior.
  - **Layout extraction** (archived — see `historical-notes.md`): expose a
    runtime-extensible type universe, not a closed-at-startup one.
  - **IR/backend cleanup**: no closed-world assumptions in the shared
    layer.
- **What this entry is NOT**:
  - A REPL implementation plan — that lives in `plan-repl.md`.
  - A relitigation of REPL semantics — those are decided; if they
    change, update `claude-notes.md` first.
- **Open design questions worth pinning before Tier 1 starts** —
  resolved as part of the Tier 1 work:
  - ~~Top-level prompt grammar.~~  Settled as bare statement list;
    auto-`println` wrap deferred until interfaces (above).  `func`
    decls are dispatched to the decl path via
    `parser.IsAtTopLevelDecl`.
  - ~~Error recovery.~~  Implemented exactly as proposed: parse /
    type / IR-gen / lower / runtime errors in immediate mode print
    and return to prompt; loaded state unaffected.  Verified by
    `e2e/repl.sh` cases.
  - ~~Where pretty-printing lives.~~  Deferred (see above).
  - ~~Sentinel for "no result".~~  Nothing — empty stmt lists are
    skipped by `evalReplStmtList` before reaching IR-gen.
  - ~~Whether REPL is a separate `cmd/bnrepl` or a `--repl` flag on
    `cmd/bni`.~~  Settled as `--repl` flag on `cmd/bni`.
    `scripts/build-bni.sh` (commit `22ea525`) is a convenience
    wrapper for casual use.

### Import aliases and blank imports
- Do we support Go-like `import somethingelse "pkg/foo"` currently? We'll likely need this.
- Do we support `import _ "pkg/foo"`? Should we? (Side-effect-only imports.)
- Both interact with the package object naming question above.

### Package path: env-var support (Stage 7)
- Add `BINATE_PACKAGE_INTERFACE_PATH` / `BINATE_PACKAGE_IMPL_PATH`
  (long names match `LD_LIBRARY_PATH`/`PYTHONPATH` style; aliases TBD)
  as the fallback when CLI flags are absent.
- Gated on adding `bootstrap.Getenv` (a few lines of C + Go-interp
  glue). Deferred because direct shell invocations of bnc/bni today
  can construct CLI arguments — the env-var fallback is convenience
  for users invoking the tools by hand.
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

### Annotations and C function interop
- **Option E (`__c_call` intrinsic) has a detailed implementation plan:
  [plan-c-call.md](plan-c-call.md).**
- Consider implementing annotations (decorators/attributes).
- Specific use case: annotating functions as C functions.
  - **Option A**: annotation in `.bni` — callers know the name and calling convention, but mixes interface with implementation.
  - **Option B**: annotation on the definition (with empty body) — `bnc` generates a trampoline. But empty body is weird (missing return values?).
  - **Option C**: annotation on a call site, indicating it's a C function call. Maybe a "magic" C package so no annotation is needed at all.
  - **Option D**: manual trampolines, with a magic C package for declarations.
  - **Option E**: a `__c_call` compiler intrinsic at the call site, no
    declaration needed.  Two forms were considered:
    - **E1 (rejected)**: pass a C prototype string —
      `__c_call("ssize_t write(int, const void*, size_t)", fd, buf, len)`.
      Reads nicely, but forces the compiler to parse C and resolve C
      types, which drags in typedefs, macros, and platform builtins
      (`__size_t` &c.).  Not practical.
    - **E2 (preferred)**: pass the C symbol name, an explicit return
      type, then the argument values already in (or cast to) the
      Binate types that match the C ABI —
      `result = __c_call("write", int, cast(int, fd), cast(*uint8, buf), cast(uint, len))`
      (casts are unnecessary when the variables already have the right
      type).  Supported argument/return types: scalars, struct types,
      and pointers to these (to any depth: `*T`, `**T`, …).  This
      reuses the backends' existing platform-C-ABI lowering (struct
      sret thresholds, register assignment) — no C parsing, no type
      resolution, no new ABI logic.  The symbol name is emitted
      verbatim (no `bn_` mangling); the backend emits the matching
      `extern`/`declare`.
  - **C-types alias package (decided)**: a package (e.g. `pkg/c`)
    pins the Binate↔C scalar correspondence in one place so call sites
    don't open-code it.  `C_int`/`C_uint` = `i32`/`u32` (C `int` is
    32-bit on both ILP32 and LP64, *not* target-word-width like Binate
    `int`); `C_long`/`C_ulong` = target-word (LP64 Unix; matches Binate
    `int`/`uint`); `C_size_t` = `uint` (pointer-width); `C_char` = `i8`
    (signedness is platform-dependent in C — note the caveat, but it's
    promoted on pass so rarely matters).  Plus a sentinel `C_void` for
    the return-type slot of functions that return nothing.  So the
    example's `fd` is really `C_int` (= `i32`), not `int`.
  - **Scope decisions (v1)**:
    - **Compiled-mode-only to start.** The compiler emits a direct
      call; the VM would need FFI-style dispatch (resolve the symbol
      via the extern registry + marshal by the supplied types) — punt
      that.  `__c_call` outside compiled mode is an error for now.
    - **Include variadics from the start.** The whole point of
      `__c_call` is to retire `pkg/bootstrap`'s hand-written C
      wrappers and the special shim machinery — and several of those
      OS interfaces are variadic in C (`open(const char*, int, ...)`
      where `mode` is a vararg; `fcntl`, eventually the `printf`
      family).  Punting variadics would leave bootstrap unable to go
      away, defeating the purpose.  So v1 supports them.
      - **Boundary marker (required).** The call site must declare
        where fixed args end and variadic args begin — it can't be
        inferred from the values (`open(path, flags, mode)` is
        indistinguishable from a 3-fixed-arg call).  Proposed: a
        `C_varargs` sentinel (or a recognized `...` token) in the
        argument list:
        `__c_call("open", C_int, path, flags, C_varargs, mode)`.
        Everything after the marker is an anonymous/variadic arg.
      - **Backend work is lopsided.** LLVM path: nearly free — emit
        `declare i32 @open(i8*, i32, ...)` + a varargs call with the
        right fixed-arg count, and LLVM does the platform-correct
        lowering (x86-64 `AL` = vararg float count, darwin-arm64
        stack-passing, 64-bit-vararg alignment) for us.  Native
        backends (`pkg/native/{arm64,amd64}`): real work — they emit
        machine code directly and must implement the vararg
        convention per target (darwin-arm64 stacks all varargs;
        x86-64 SysV sets `AL`; AArch64-Linux/arm32 mostly match the
        fixed convention but 64-bit varargs need 8-byte alignment).
        This extends the existing `CallConv`/register-assignment
        logic; needs per-target tests.
  - **Open considerations for E2 (still to resolve)**:
    - Confirm the full `pkg/c` scalar table against each target
      (`C_long` on a 32-bit target, `C_char` signedness, the float
      types if/when floats land).
    - Final spelling of the variadic boundary marker (`C_varargs`
      sentinel vs a `...` token vs an explicit fixed-arg count).
    - VM/dual-mode FFI dispatch (deferred above) when interpreted-mode
      `__c_call` is eventually wanted.
  - **Companion idea — link-requirement annotation (sketch)**: Option E
    makes a C symbol *callable*; a complementary annotation would make
    it *resolve at link time* by declaring, at the source level, that
    using a package requires linking some C library — so the driver
    adds the flag automatically instead of every consumer passing
    `--cflag -lm` / `--link-after-objs` by hand.  Prior art:
    Rust `#[link(name = "m", kind = "static")]`, Go cgo
    `// #cgo LDFLAGS: -lm`, MSVC `#pragma comment(lib, "foo")`.
    Natural shape: `#[link("m")]` (optionally a `static`/`dynamic`/
    `framework` kind), most naturally on the `.bni` since the link
    requirement is part of the package's contract.  This is also the
    first real payoff of the general annotations feature this item is
    about — both Option E and this want it.
    - **Open wrinkles**:
      - **Transitivity** — the requirement must propagate through the
        import graph (aggregate + dedup all declared libs for any
        binary that transitively imports the package).  Hooks into the
        loader's `ldr.Order` walk + the driver's `clangArgs` assembly.
      - **Link ordering** — static archives only supply symbols
        referenced by *earlier* inputs, so aggregated `-l` entries
        need correct placement vs. the `.o` files and runtime (the
        driver already does this for `linkAfterObjs`).
      - **Search paths** — keep the annotation name-only (`-l`); leave
        `-L<dir>` to driver flags.
      - **Platform-conditionality** — a `libm` dep is meaningless on
        bare-metal arm32 and `framework` kind is macOS-only, so the
        annotation likely needs to be target-qualifiable.  Ties into
        the C-free principle: this exists only to interface with
        existing C systems and should evaporate on freestanding
        targets.
      - **Static-spec portability** — even with `kind = static`,
        expressing it portably is messy (GNU ld `-l:libfoo.a` /
        `-Wl,-Bstatic`; macOS `ld` has neither), so it may need
        per-platform lowering in the driver or a full-path escape
        hatch.

---

## TEST COVERAGE — conformance matrix follow-ups

### Sweep for STALE xfails — the runner skips xfailed tests, so now-passing ones sit marked-failing forever (2026-06-13) — 🟡 OPEN (all host-runnable modes SWEPT; only the qemu-gated cross modes remain)
Discovered while triaging done-but-residual todo entries: `const-group-bare-inherited-overflow` was fixed by `b9d6d807` but its 11 `.xfail.*` files were never removed, and `conformance/run.sh` does NOT re-run xfailed tests (it skips them — they show as `x`, never `XPASS`), so the stale xfail was invisible. There are ~247 conformance `.xfail.*` files (+29 unittest); an unknown number are similarly stale.
- **builder-comp + builder-comp-comp (gen2) swept (2026-06-13)**: only ONE stale xfail — `const-group-bare-inherited-overflow` — REMOVED (binate `680a4eca`, all 11 markers; `.error` type-check test, stale in every mode). Both default LLVM modes otherwise clean.
- **VM modes swept (2026-06-13)** — `builder-comp-int` / `-comp-int` / `-int-int`, via `run.sh --check-xpass <mode> <test-names>` (run only the xfailed tests, not the whole hang-prone suite). **25 stale removed in 2 commits:**
  - `8741c552` (14 top-level): `718_funcval_spill_over_vm_cap` ×3 VM modes (bytecode→bytecode func-value dispatch never hits the 7-arg `_call_shim_*` cap — that cap only bites compiled-target/nested-VM); + 11 `-int-int`-only that all blamed now-fixed double-VM infra (`272_raw_slice_star_sugar`; the `586/592/673/674/675/676/677/678/682` cross-pkg `*_balance` family on the int-int "package pkg/builtins/rt not found" loader bug; `665_transitive_iface_reexport` on the int-int multi-package `rt.MemCopy` NULL-deref). Confirmed fixed: the canaries `136`/`383`/`061`/`373`/`384` are unmarked + green under int-int.
  - `bcb3c362` (11 subdirectory readonly/matrix): `pass-arg/value-struct{,-large}` (int/-comp-int/-int-int) + the `-int-int` Round-2 cells (`nested-index/field/nested-value-struct`, `readonly/alias/method-receiver`, `readonly/construct/readonly-iface`, `readonly/wrapper-order/inner-{managed,raw}-ptr`). These were left xfailed only on VM after the plan-cr2-1 Defect-1/Round-2 fixes landed on LLVM (cf. line ~879 "stay xfailed on VM / native-globals").
  - **VM xfails KEPT (genuine)**: `regressions/c-call/*` + top-level `498/500/527/530` (VM has no FFI); `matrix/globals/readonly/struct` (Defect-1 `gen_selector` global-readonly path, still open); `regressions/named-func-value-construct-literal` (open B2 follow-up, xfailed in every mode incl. LLVM); `385/386_iface_nil_dispatch*`; `708/709/725/727_reflect_*`.
- **Unittest comp-comp-int swept (2026-06-13)** — `76fe86cc`: 4 stale (`cmd-bnlint`, `pkg-binate-{codegen,ir,vm}`) that blamed the now-fixed "boot-comp-int VM field-layout bug"; all 4 packages' full suites pass under comp-comp-int. NOTE: `scripts/unittest/run.sh` has NO XPASS detection (it just skips xfailed packages) — sweep by hand (move marker aside → run → restore). The 8 ccall unittest xfails (`pkg-bootstrap`/`pkg-builtins-rt`/`pkg-std-os` in VM modes) are genuine (VM can't interpret `__c_call`).
- **Native aa64 + x64_darwin swept (2026-06-13)**: 0 stale. `386` (compiled SEGVs with no VM panic msg; mode-correct, pinned by `385`), `705/706/707` (native closure-float shim gaps, claude-todo #121 open) all genuinely fail. gen3 (`builder-comp-comp-comp`) lone xfail is `386` — same mode-correct reason, structurally can't XPASS.
- **CROSS MODES SWEPT via the CI workflow (2026-06-14) — 99 stale conformance xfails removed.** The on-demand `.github/workflows/conformance-xpass.yml` (Actions → "Conformance XPASS (stale-xfail sweep)" → Run workflow; blank `mode` = all 10 modes, or pass one) re-runs each mode's xfailed tests under `--check-xpass`; a red job lists XPASS = stale markers. Full-matrix run results:
  - `native_aa64`: **29** `matrix/scalar-diff/*` signed sub-word cells (arith/bitwise/cmp/int-cast/shift/float-conv) — aa64-subword narrowing fixed; binate `5f94558b`. Host-runnable but MISSED by the earlier top-level-only host sweep (the same subdirectory-enumeration lesson — these live under `matrix/scalar-diff/`).
  - `arm32_linux`: **40**, `arm32_baremetal`: **30** — native arm32 backend + multi-return tuple-packing caught up (markers blamed "native arm32 not yet implemented" / Plan-3 tuple-packing; some carried already-stale "drops result type / SILENT wrong-code" text). binate `1ce5a6d9` / `56c275b6`. (Includes the line-~5077 `abi/iface-multi-return{,-assign}` cells — confirmed stale as predicted.)
  - `native_x64`: **22** stale, but only visible AFTER a **workflow bug** was fixed. run.sh filters were substring-match, so the `value-struct` xfail filter also pulled in the *unmarked* `value-struct-large` (which crashes on native_x64) → false-positive that masked everything else. Fixed by `run.sh --exact` (exact filter match) + the workflow passes it (binate `982727d1`). With `--exact`, two consecutive native_x64 CI runs agree on 22 stale: `538_float_lit_tie_roundbit` + `635_float32_arith` + the `matrix/const/*` float32/float64 tie/half/neg/tenth cells (native float round-bit / float32-narrowing, "blocked on a new BUILDER release" = bnc-0.0.9, now shipped); plus `matrix/readonly/*` + `matrix/nested-index/field/*` (plan-cr2-1 Defect-1/Round-2 shared-IR-gen, same cells dropped on the VM modes). Removed: binate `27ba1f7e`. Post-removal native_x64 sweep: green. **All 10 modes now green under the sweep** (121 stale conformance markers removed total: aa64 29 / arm32_linux 40 / arm32_baremetal 30 / native_x64 22).
  - **Unittest sweep now possible** — `scripts/unittest/run.sh` gained `--check-xpass` (binate `ddc624d2`; same XPASS-on-stale semantics, per-package): run `scripts/unittest/run.sh --check-xpass <mode>`. Swept the 3 VM modes: `pkg/builtins/rt`, `pkg/bootstrap`, `pkg/std/os` all XPASS (they're injected as native in the VM, so their tests run against native code and pass — e.g. rt runs 21 passing tests). **8 stale markers removed** (bootstrap+rt on `builder-comp-int`; bootstrap+rt+os on `-comp-int` and `-int-int`); binate `55229591`. The `native_aa64` unittest xfails (11, the weak-`buf.Builder`-dtor dup-symbol MAJOR bug) correctly stay XFAIL (`mangle` re-confirmed genuinely failing). The arm32 unit xfails (16 baremetal + 1 linux) need qemu + the unittest `--check-xpass` isn't wired into CI, so they're UNSWEPT.
  - **STILL OPEN — cross-mode unittest xfails (17)**: the unittest runner (`scripts/unittest/run.sh`) still lacks `--check-xpass` (it just skips xfailed packages), so the workflow is CONFORMANCE-only; sweep those by hand or teach the runner XPASS detection.
  - **FOLLOW-UP — `value-struct-large` on `native_x64`**: it's *not* xfailed there yet crashes (empty output) when run — a real missing-xfail or native_x64 bug, surfaced (then masked) by the substring collision. Worth a look now that `--exact` no longer pulls it in.
- **METHODOLOGY (learned the hard way)**: enumerate sweep sites with `find conformance -name '*.xfail.*'` (RECURSIVE) — a top-level `ls conformance/*.xfail.*` misses ~160 subdirectory (`matrix/`, `regressions/`, `abi/`) markers. Per-mode list: `find conformance -name '*.xfail.<mode>'`. Run only the xfailed tests as filters (amortizes one toolchain build); `--check-xpass` reports `XPASS` for the stale ones.
- **Why it matters**: stale xfails hide regressions (a real future failure on that test would still show `x`) and inflate the xfail count; each one may correspond to a "done-but-not-archived" todo entry.

### Plan-3 adversarial-review follow-ups (test-hygiene + coverage gaps from `cc2ddcc4` / `997c4c04` / `0c707e1f`) — 2026-06-08
Non-wrong-code items from the adversarial review of the plan-cr2-3 work; each is small. (The live wrong-code findings are the OP_CAST/iface-arg CRITICAL and the float-multi-return MAJOR above.)
- **Stale x64-ELF (CI x64 mode) iface-multi-return xfails**: `cc2ddcc4` removed the aa64 xfails but left the `builder-comp_native_x64-comp_native_x64` (ELF/linux) ones (16 files incl. the `iface-multi-return-assign` siblings). The iface path now routes through the SAME object-format-independent `collectMultiReturnTuple` as direct/funcval multi-return (both 0-xfail on ELF), so these are almost certainly STALE / now-XPASS. `native_x64` (ELF) is the ONLY x64 mode in `scripts/modesets/all`; CI runs it WITHOUT `--check-xpass` (`conformance-tests.yml`) so the now-green cells are silently SKIPPED. Action: on a qemu-x86_64 / x86_64-linux host run `run.sh --check-xpass builder-comp_native_x64-comp_native_x64 abi/iface-multi-return abi/iface-multi-return-assign`; if XPASS, delete the 16 files. (Also: the x64-linux runner HEADER DOC still says "Phase 2 stub / most tests COMPILE_ERROR" — stale; native x64-linux lowering is implemented.)
- **Stale xfail-reason text**: `iface-multi-return/u16/{2,3,4,5}.xfail.{arm32_baremetal,arm32_linux,native_x64}` (+ the `-assign` siblings) still say "drops result type / SILENT wrong-code" though the SEAM fixed the front-end; the `int/*` siblings were corrected (`03b80566`), `u16/*` were not.
- **Stale comments**: `pkg/binate/native/x64/x64_call_indirect.bn:146-148` still claims `IsMultiReturnCall` gates on `OP_CALL`/`OP_CALL_FUNC_VALUE` only (`cc2ddcc4` added `OP_CALL_IFACE_METHOD`); `conformance/573_addr_of_two_globals_one_instr.bn:8-11` claims a VM xfail that no longer exists (the `lower_func` global-clobber bug was fixed).
- **Weak / over-claimed Defect-6 pin**: the addr-aggregate `global` cells (`997c4c04`) + their generator docstring/README claim to pin "2-word sizing / mis-sized-to-one-word drops a word" — but store+load are width-consistent so the cell is INVARIANT to allocation size (it pins materialization + `__init`-store + read-back wiring, NOT sizing). Fix the docstring (`gen-addr-aggregate-matrix.py:96-104`) / README / commit framing to match. Also Defect 6 closed using only the two shapes that typecheck; readonly-wrapped + named-over-aggregate + raw `*func()` + uninitialized-nil global companions (the Class-A materialization risk in `plan-code-red-2.md`) were left out — record as an explicit deferral (invoking them is blocked upstream at the call typechecker).
- **`&G == &H` unit test too weak**: `x64_global_ref_test.bn` `TestEmitCompareGlobalRefOperandsMaterialize` asserts only ONE RIP-LEA, not the load-bearing "two distinct globals get DISTINCT scratch regs" property — add a count==2 (and ideally distinct-dest-reg) assertion.
- **Coverage gaps**: aa64 per-field iface-multi-return collect (`aarch64_iface.bn:204-228`, the exact loop that dropped sub-word fields) has NO unit test (only conformance on aa64); x64 `collectMultiReturnTuple`-for-iface has no unit test for the IFACE op; an aggregate-component iface multi-return tuple (`(Pair,int)`) is uncovered; the iface-method-arg-with-global position is covered by neither a unit test nor 551/573 (see the CRITICAL entry).
- **Latent fragility (nit)**: `pkg/binate/ir/gen_call.bn` computes `resultTyp` generically and hands it to `EmitCallHandle`/`EmitCallIndirect` (magic-name dispatch) with no structural guard that it isn't a multi-return struct — add a cheap assert so the "these ops never carry a multi-return" invariant is enforced in code, not convention.
- **Discovery**: 2026-06-08, adversarial multi-agent review of plan-cr2-3 work (6 reviewers → adversarial verify → completeness critic; 21/23 findings confirmed).

The code-red conformance-matrix family (`conformance/matrix/`, see
`plan-code-red.md` §7) has four members realized: `refcount` (Class 1),
`scalar` (Class 5), `abi` (Class 4), `const` (named-constant invariant). These
are the remaining matrix-shaped classes not yet built as their own matrix —
candidates for after the loose-axis finish (const-expr folding + ABI
`handle`/`__c_call` shapes).

### (b2) Lifecycle matrix — Class 6 (`@Iface` / `@[]@I`) + Class 7 (captured-`@func` over-release) — PARTLY ADDRESSED 2026-06-05 (plan-cr-p2-2 step 5)
- **Status**: the existing `conformance/matrix/refcount` form × type grid already
  covers Class 6's construction/consumption shapes (the copy-sites are now uniform
  after the `emitStoreManagedSlot` consolidation), and `604`/`605` add lifecycle-
  DEPTH balance (a value chained through param/store/pass/return/bind/invoke) for
  captured-`@func` and cast-from-impl `@Iface`, green in builder-comp/-int/-comp/
  native-aa64. REMAINING: a true single-program **Class 7 native↔VM trampoline**
  balance test is not expressible in the single-mode conformance harness (each
  test runs in one mode) — needs a cross-mode harness; left as a follow-up.
- **Why a matrix**: Class 6 (`@Iface`/`@[]@I` first-class lifecycle) and Class 7
  (native call-a-captured-`@func` over-release via the VM trampoline) are
  lifecycle-completeness classes. Axes would be `managed-kind (@Iface / @[]@I /
  captured-@func) × construction (make / literal / cast-from-impl / capture) ×
  consumption (call-method / index / range / pass / return / discard) ×
  backend`, with a refcount-balance assertion (mortal source).
- **Status**: the refcount matrix already covers `@Iface`/`@func` as value-types
  across assignment-forms, so this would EXTEND rather than start fresh — the
  new axis is construction × consumption depth (esp. the native↔VM trampoline
  path for Class 7, which the refcount matrix does not exercise).
- **Note**: several `@Iface` lifecycle bugs are already filed (leaks/UAF family,
  `@[]@I` literal element leak); a matrix would close the long tail.

### (b3) Class 3 / Class 8 — point-bugs, NOT matrices
- Class 3 (cross-package / interface-name type-resolution ordering → `i8*`
  fallback) and Class 8 (multi-package loader resolution at int-int depth) are
  one-off ordering/loader bugs, not systematic products. Track them as
  individual regression tests under `conformance/regressions/` + filed bugs, not
  as a matrix.

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

## P3 — low-priority follow-ups

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
