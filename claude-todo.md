# Binate TODO

Tracks open work items. Completed items live in [claude-todo-done.md](claude-todo-done.md).

---

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

### Iface-value upcast to an unrelated zero-method interface ABORTS the compile (R2-3 negative-offset panic false-fires on valid code) — LLVM + native aa64/x64 — REGRESSION from `ca155319` — ✅ RESOLVED 2026-06-10 (binate `4ac123da`)
- **✅ RESOLVED 2026-06-10 (binate `4ac123da`).** Fixed at the ROOT (the checker duck-typing hole), per the user's choice of the secondary fork **(B)**. The four assignability arms now gate universal satisfiability on a new checker `isUniverseAny` (mirrors IR-gen's predicate) instead of `len(Methods)==0`, and managed→raw same-interface decay rides an explicit `sameInterface` check so `@Iface -> *Iface` works for EVERY interface (not just empty by accident). Now `*Speaker -> *Empty` / `*T -> *Empty` (no impl) are rejected; `*any`/`@any` and real upcasts (incl. to an empty PARENT via extends) unchanged; the R2-3 panic is now unreachable on valid code (kept as defense-in-depth); R2-3's same-canonical→0 stays and is now correctly exercised for non-empty decay. conformance/685 extended to non-empty decay + conformance/689 nominal-rejection guard (both green across builder-comp / -int / -comp / native aa64 / native x64-darwin; full builder-comp suite 1318/0); unit tests in `check_iface_empty_marker_test.bn`. Fork (B) chosen over (A) because decay should mirror `@T -> *T`.
- **Symptom**: `var e *Empty = s` where `s` is `*Speaker` and `Empty` is a user-declared ZERO-method interface (unrelated to Speaker) — accepted by the checker — aborts the gen1/gen2 compile with **exit 1 and no diagnostic** (OP_PANIC discards its message). Managed variant (`@Speaker -> @Empty`) identical. **A/B proof**: BUILDER bnc-0.0.7 (pre-R2-3) compiles the same program through codegen, emitting a harmless `getelementptr inbounds i8*, i8** %vt, i64 -1` (harmless because `Empty` has no dispatchable methods, so the −1-offset vtable pointer is never dereferenced); gen1/gen2 (post-R2-3) emits NO `.ll` and aborts.
- **Root cause (two layers)**: (1) PRE-EXISTING checker hole — `canAssignToInterfaceValue` / `canAssignToManagedInterfaceValue` (`pkg/binate/types/types_assignable.bn:185` / `:234`) short-circuit `if len(iface.Methods) == 0 { return true }`, accepting an iface-value upcast to ANY zero-method target, not just `any`/same/ancestor. For such an upcast `IfaceParentSlotOffset` (`pkg/binate/ir/gen_iface_extends.bn:145`) returns −1 (target is not `any`, not same-canonical, not a parent). (2) REGRESSION — `ca155319` added `if offset < 0 { panic(...) }` to all three offset-based upcast lowerings (`emit_iface_upcast.bn:38`, `aarch64_dispatch.bn`, `x64_dispatch.bn`) on the FALSE premise (stated in the comment) that "the checker should never produce a negative offset." It does. R2-3 turned a latent-but-running path into a hard compile abort.
- **VM divergence (X2b, separate/pre-existing)**: the VM (`vm_exec_iface.bn`) doesn't use IfaceParentSlotOffset; it looks up a `(T, target)` vtable by name (`findIfaceVtable`), never registered → runtime abort `vm: iface_upcast: target vtable not found`. Its only zero-method shortcut matches literal `any`, not a user empty interface. So the SAME accepted upcast now has THREE behaviors: pre-R2-3 LLVM/native = works; post-R2-3 LLVM/native = compile abort; VM = runtime abort.
- **Severity**: CRITICAL — a newly-added assert aborts the compile of previously-accepted code on all offset-based backends; the exact "panic false-fires on valid code" class this review exists to catch. (Loud abort, not silent miscompile; gated on the checker hole + an unusual shape, so the 140-cell iface suite stayed green, and R2-3's own 685 covers only the empty-interface decay.)
- **ROOT CAUSE is a DUCK-TYPING checker hole (confirmed 2026-06-09 with the user — Binate is nominal, no structural typing).** Design docs are unambiguous: `any` is THE single built-in/universe universal interface (`claude-notes.md:575` "a small, closed, language-defined set… `any` is the primary one"; `plan-interface-syntax-revision.md §6`); a USER-declared `interface Empty {}` is a NOMINAL marker interface requiring an explicit `impl`. The four `len(iface.Methods)==0 { return true }` sites (`types_assignable.bn:185/194/234/240`) are a too-broad proxy for "is `any`". IR already has the correct predicate `isUniverseAny()` (`gen_iface.bn:446`: `Kind==TYP_INTERFACE && len(Pkg)==0 && Name=="any"`). The hole is SYSTEMATIC, not upcast-only: a CONCRETE `*T -> *Empty` with NO `impl *T : Empty` ALSO compiles today (runtime-verified). Correct fix core = gate those 4 sites on a checker `isUniverseAny` instead of `len(Methods)==0`; then `*Speaker -> *Empty` and `*T -> *Empty` are rejected, `*any`/`@any` still work, and the −1/panic path is unreachable on valid code (panic stays as defense-in-depth). The earlier "(B) make any zero-method target universal" idea is REFUTED by the docs — do not do it.
- **SECONDARY DESIGN FORK this surfaces (USER-OWNED) — managed→raw iface-value decay.** Tightening also rejects `@E -> *E` (the empty decay conformance 685 exercises). Turns out `@Iface -> *Iface` decay is ALREADY rejected for NON-empty interfaces (`@Speaker -> *Speaker` → "cannot assign @Speaker to *Speaker", runtime-verified); the empty case only ever worked via this same hole, so 685 tests buggy behavior. Decide: **(A)** decay stays unsupported for all interfaces — rewrite/drop 685, and R2-3's same-canonical→0 machinery (`gen_iface_extends.bn:160-165`) becomes dead → remove; minimal + consistent. **(B)** make `@Iface -> *Iface` decay a real supported op for all interfaces (mirroring `@T -> *T` at `types_assignable.bn:77`) via a reflexive same-interface acceptance — keep+extend 685 to non-empty; R2-3's same-canonical→0 stays. (`isDescendantInterface` is NOT reflexive today — `types_assignable.bn:259`.)
- **All four upcast consumers** (LLVM/aa64/x64/VM) auto-resolve once the checker rejects the bad upcast (IR/codegen/VM never see it). Add reject cells for both concrete `*T -> *Empty` (no impl) and iface-value `*Speaker -> *Empty` (raw + managed).
- **Test (to add)**: `conformance/NNN_err_iface_assign_unrelated_empty` (`.error`) covering concrete + iface-value sources; plus the 685 decision (A: drop/rewrite, B: extend to non-empty) per the fork.
- **Discovery**: 2026-06-09 CR-2-batch adversarial review (X2 finder); runtime A/B confirmed; root-cause + fork confirmed with the user.


### `builder-comp-int-int` (double-VM) globally broken — every test SIGSEGVs — ✅ RESOLVED 2026-06-09 (binate `c997cf2e`; root cause `71ff7489`)

- **Symptom**: EVERY `builder-comp-int-int` conformance test produces empty output and exits 139 (SIGSEGV) — including the most trivial: `001_hello` (`println("hello world")`), `002_arithmetic`, `003_variables`, bare `println(42)`. The whole int-int lane is dead, not a per-test issue.
- **Where it crashes**: the compiled `bni` (gen1-compiled `cmd/bni`) **SIGSEGVs while interpreting `cmd/bni`** — the bni-under-bni (double-VM) path. The inner VM dies at startup/load, before any test output. Reproduced manually outside the harness:
  `COMPILED_INTERP -I … cmd/bni -- -I … conformance/001_hello.bn` → exit 139, no output.
- **Not a stack limit**: a 64 MB stack (`ulimit -s 65532`) changes nothing; the crash is immediate, not a gradual overflow.
- **Single-VM is fine**: `builder-comp-int` and `builder-comp-comp-int` (one VM layer) pass normally — only the double-VM (`int-int`) crashes.
- **Scope**: `builder-comp-int-int` is in the `all` CI modeset (comprehensive lane red across ~1150 tests), NOT in `basic` (basic smoke = `builder-comp` + `builder-comp-int`, both green). So basic smoke is green; the comprehensive lane is red.
- **Pre-existing / not from Round-2 work**: crashes on field-access-free `001_hello`, which no front-end fix touches. The earlier Defect-8 note (at `a869e8e7`) characterized int-int crashing only for MULTI-package tests; it is now GLOBAL. The worsening happened somewhere in `a869e8e7..0c707e1f` (unbisected), or int-int single-package was already broken then and only the multi-package case was checked.
- **Root cause (CONFIRMED)**: `71ff7489` (the "length-0 ⟹ no backing" rep change) made the bytecode VM lower an *aggregate* `OP_CONST_NIL` — an empty string literal, an empty raw composite, `make_slice(_,0)` — to a scalar `0`, i.e. a NULL address. The VM carries every aggregate value (slice / struct / iface- / func-value) by the ADDRESS of its in-memory image, so any by-address consumer (a call argument, an `OP_EXTRACT` such as `len()`) read through null. Single-VM only tripped on test programs that actually hit that path (e.g. `110_cross_pkg_type_alias`); under double-VM the inner program *is* `cmd/bni`, which uses empty literals by-address during load → universal null-deref at startup (hence even `001_hello` SIGSEGV'd, before any test output). The suspected `a869e8e7..0c707e1f` range and the `68616b20` candidate were red herrings — the culprit `71ff7489` predates that range, so "int-int single-package was already broken then" (per the bullet above) was the correct read.
- **Discovered**: 2026-06-09 while validating CR-2 Plan-1 Round-2 (R2-D1). Per the user (2026-06-09): FILE this; do NOT add per-cell `.xfail.builder-comp-int-int` to new Round-2/Plan-A cells (the whole lane is down — per-cell xfails would be noise that falsely reads as a known per-cell issue). Validate Round-2/Plan-A fixes on the other 6 runnable modes; the cells are mode-agnostic and pass int-int once this is fixed.
- **RESOLVED 2026-06-09 (binate `c997cf2e`)**: the VM now reserves a dedicated zeroed frame region for each aggregate `OP_CONST_NIL` (mirroring native's dedicated data region and LLVM's alloca + zero-fill), so the value's register is a valid address of a `{0,…}` image. This is the SAME commit recorded elsewhere in this file as fixing the single-VM `110_cross_pkg_type_alias` regression — the int-int entry just wasn't connected to it. Bisect-verified: int-int `001_hello` SIGSEGVs at `c997cf2e^` (`b4d5b37b`) and passes at HEAD; the full int-int sweep is green at HEAD (1245 passed, 0 failed, 48 xfail-skipped).

---

## MAJOR

### native-aa64 corrupts SIGNED sub-word (int8/int16) values under register pressure (spill/reload) → wrong shift results — 🔴 OPEN (NOT a release blocker; release bundle is BUILDER/LLVM)
- **Symptom**: the `conformance/matrix/shift-typepair/sh{l,r}/int{8,16}` cells (value type int8/int16, sweeping all 10 count types) fail ONLY on `builder-comp_native_aa64-comp_native_aa64`. Each cell's ~60 sub-word locals force register spills; the output is correct for the first ~9 self-checks, then wrong once spilling starts — POSITIONAL, not count-type-specific (`shl/int8` raw output: checks 1-9 = 1, then 10/11/13-20 = 0). Isolated `int8<<int8` / `int8<<int64` / overshift all pass (no spilling); `uint8`/`uint16` and `int32`/`int64`/`int`/`uint` value cells all pass. So it is **SIGNED sub-word spill/reload corruption in the aa64 backend**, not the shift logic — host-int (`builder-comp`/`-comp-comp`/`-comp-comp-comp`), VM (`-comp-int`), and gen2 all pass these cells.
- **Root cause (suspected, unconfirmed)**: the native-aa64 register allocator's spill store or reload for an int8/int16 value uses the wrong width or sign-extension, so a spilled signed sub-word value comes back corrupted. Unsigned sub-word (uint8/uint16) survives — likely the reload zero-extends correctly but sign-extension is wrong/missing for the signed case. Needs a look at the aa64 spill/reload codegen (`pkg/binate/native/aarch64`).
- **Severity**: MAJOR — silent wrong values for signed sub-word arithmetic under register pressure on the native-aa64 backend; any user code with enough int8/int16 locals could hit it. **NOT a bnc-0.0.8 release blocker**: the release bundle's bnc is built by the BUILDER (bnc-0.0.7, LLVM backend), not the native-aa64 backend; native-aa64 is a separate codegen path exercised only by the `*_native_aa64` conformance mode.
- **Handling**: the 4 cells are xfailed on `builder-comp_native_aa64-comp_native_aa64` (so the mode is green-modulo-xfails and the matrix still covers host-int/VM/gen2). Un-xfail when the aa64 spill bug is fixed.
- **Discovery**: 2026-06-10, bnc-0.0.8 release-gate recheck — the new shift-typepair matrix (binate `93d6ecd4`) exposed it (its many sub-word locals create the register pressure prior shift tests didn't).
- **Symptom**: on `fd3cb7ac` (after the shift fix unmasked it), arm32-baremetal unit + conformance fail at LINK, not compile: `clang: error: no such file or directory: '.../ifaces/targets/arm32-baremetal/runtime/baremetal_arm32/crt0.s'` (and `semihost.s`). The files exist at the **repo root** `runtime/baremetal_arm32/`, not under the `ifaces/targets/arm32-baremetal/` overlay.
- **Root cause**: `appendTargetRuntime` (`cmd/bnc/target.bn:308`) joins the relative `targetRuntimeFiles` (`runtime/baremetal_arm32/crt0.s`, …) against `root`, where `root = primaryRoot(cli)` (`cmd/bnc/args.bn:58`) = `cli.BniPaths[0]` — the FIRST `-I` path. The per-target `ifaces/targets/` overlay work (build.bni metadata, in-window) makes `binate-paths --iface --target arm32-baremetal` **prepend** `ifaces/targets/arm32-baremetal` as the first `-I` entry (confirmed; the unittest-runner change `ac738936` mirrors `--target` onto `--iface` for cross modes). So `BniPaths[0]` is the overlay dir, not the repo root, and the runtime files resolve to a non-existent path. (Harmless on host: host `targetRuntimeFiles` is empty, so `appendTargetRuntime` is a no-op — which is why host modes stayed green and this hid behind the `int64 << int` compile error until that was fixed.)
- **Baseline**: `builder-comp_arm32_baremetal` Unit was green at bnc-0.0.7 (before both the `ifaces/targets/` overlay and the types compile error). In-window regression; the SECOND arm32 regression masked behind the first (the `int64 << int` one, now resolved `fd3cb7ac`).
- **Severity**: MAJOR — breaks all arm32-baremetal linking on a previously-green mode — but **NOT a bnc-0.0.8 release blocker**: per the user (2026-06-10), arm32-baremetal is excluded from the release gate. Fix tracked for after.
- **Fix (direction, per user)**: the runtime files + linker script belong on the existing **`--runtime` flag** mechanism — the runner should pass the concrete `crt0.s`/`semihost.s`/`.ld` paths explicitly (as it already does for `libgcc.a` via `--link-after-objs`), NOT have bnc infer a `root` from `-I[0]` (`primaryRoot` = `BniPaths[0]`, which is wrong now that the iface overlay is prepended). i.e. retire the `appendTargetRuntime`/`primaryRoot`-based path inference for these in favor of `--runtime`. (My initial `primaryRoot`-skip-overlay idea was wrong — recorded so it isn't retried.)
- **Discovery**: 2026-06-10, watching CI on the shift fix `fd3cb7ac` — the arm32 error changed from `mismatched types int64 and int` (compile) to the missing-`crt0.s` link error.
- **✅ RESOLVED 2026-06-10 (binate `1d95923e`)**: rooted out `primaryRoot`/`root`
  from bnc entirely — per the user's "full root-out" decision, which SUPERSEDED
  the earlier "runner passes the link files explicitly" direction above. The
  loader is now seeded from `discoverBinateRoot(--runtime)`; `appendTargetRuntime`
  resolves `targetRuntimeFiles`+linker relative to `dirOf(--runtime)`; baremetal's
  `targetRuntimeFiles = {"semihost.s"}`, `targetLinkerScript = "baremetal.ld"`, and
  `crt0.s` is the `--runtime` (linked via a split link-gate: link the `--runtime`
  file whenever present, stubs only when `!suppressHostRuntime`). `binate-paths
  --target arm32-baremetal` now supplies `impls/core/baremetal` +
  `runtime/baremetal_arm32` on `-I`/`-L` (replacing the deleted
  `targetImplPathSuffixes`); the two baremetal runners pass `--runtime
  .../crt0.s` + `--target arm32-baremetal` on their `--impl` call.  Verified host
  (gen1 builds, conformance 001+692, bnc-unit 114) + baremetal package-resolution
  via `gen1 --target arm32-baremetal -c`; the LINK itself is CI-verified (no local
  arm-none-eabi toolchain).
- **Bonus finding → follow-up — dead `Builtin` machinery**: `root` was threaded
  through the registration call-graph ONLY to feed `collectPkgFile`'s
  `if depPkg.Builtin { read <root>/<pkg>.bni }` branch, but `RegisterBuiltin` has
  NO production caller (only `loader_test.bn`), so `pkg.Builtin` is never true in a
  real build → that branch was DEAD (and pointed at a path that no longer exists
  post-regularization).  Removed the dead branch + vestigial `root`.  The REST of
  the Builtin machinery (`loader.RegisterBuiltin`, `loader.Package.Builtin`, the
  `pkg.Builtin` guards in cmd/bnc compile/main/test) is now fully dead-but-harmless
  → optional cleanup to remove it entirely.

### `int64 << int` rejected in 32-bit-int modes → breaks ALL 32-bit-int compilation — REGRESSION from `efeb0f94` — ✅ RESOLVED 2026-06-10 (binate `fd3cb7ac`)
- **✅ RESOLVED 2026-06-10 (binate `fd3cb7ac`).** Root-caused as a TYPE-CHECKER + IR-gen defect, NOT the missing source cast of the initial diagnosis. Per the user's semantics decision: a shift `x << y` / `x >> y` takes its result type from the LEFT (value) operand, and the count `y` may be ANY integer type, independent of the value (Go semantics). Fix: (1) checker `check_expr.bn` — shifts get their own arm instead of being lumped with the symmetric bitwise ops `& | ^` (which unified the operands via commonType → "mismatched types"); untyped-operand cases still defer to foldIntBitwise (byte-identical to before — this matters, see below), a typed-vs-typed pair returns the left operand's type; (2) IR-gen `gen_binary.bn` — a shift's result type is the value (left) type, not the symmetric widenType (which would narrow the result to `int` for `int64 << int` in 32-bit-int, silently truncating). `cast(int64, 1) << (width - 1)` (`types_query.bn:168`) now compiles as-written. Verified: native conformance 1337/0, VM 1307/0, gen2; unit ir/types/codegen/vm/native 8/8; cell `regressions/shift-count-any-int-type`. **arm32 unit/conformance confirmation pending CI on `fd3cb7ac`.**
- **A dead-end worth recording**: a first, larger rewrite (a dedicated `emitShift` that also fixed the count-wider OVERSHIFT corner by widening the value) was correct on native but **regressed signed sub-word shifts on the bytecode VM** — identical-looking IR, different VM result (`int8(1) << 2` → -64). Reverted for the minimal change above. Separately, the checker's `return lt` for an UNTYPED count (vs deferring to foldIntBitwise's commonType) also broke signed sub-word `>>` on the VM (`(-i8v) >> 4` → 0 not -1); hence the minimal checker only short-circuits the typed-vs-typed case. The VM-fragility of these paths is real but was avoided, not fixed.
- **Symptom**: `pkg/binate/types/types_query.bn:168` is `var shifted int64 = cast(int64, 1) << (width - 1)`. In 32-bit-int target modes the shift count `(width - 1)` is `int` (32-bit) while the shifted operand is `int64`, so the checker rejected it: `mismatched types int64 and int`. Because `types_query.bn` sits in nearly every package's transitive dependency, the single error cascaded — arm32 unit + conformance failed to compile. Compiles fine in 64-bit-int host modes (where `int`'s width == `int64`), which is why every `-comp*` mode stayed green and the break was invisible to the green legs.
- **Baseline / regression proof**: `builder-comp_arm32_baremetal` Unit was **green at bnc-0.0.7** (commit `ee06ec87`, job `success`); it was **red at `ac738936`**. The offending line landed in `efeb0f94` (2026-06-05, the integer divide/remainder fault-guard work), after the 0.0.7 tag (2026-06-04) → in-window regression.
- **Follow-ups**: (a) ✅ split `pkg/binate/types/check_expr.bn` (binate `a57496e6`) — back under the soft limit; binary-op checking + tests in `check_expr_binop{,_test}.bn`. (b) ✅ comprehensive shift type-pair MATRIX (binate `93d6ecd4`) — `conformance/matrix/shift-typepair/` covers the full (value-type, count-type) product for `<<`/`>>`, asserting permitted + result-type-is-the-value's + value correctness; green on native/VM/gen2. (c) 🔴 OPEN — count-wider OVERSHIFT corner: when the count's TYPE is wider than the value AND its VALUE ≡ a small residue mod 2^valueBitWidth (e.g. `byteVal << 256`), the count truncates to the value width and overshift is mis-detected (silent wrong value). Reachable only with an absurd count (≥ 2^width); proper fix = guard at the wider width (VM-safe, not sub-word) for the wider-count case. The matrix deliberately uses count = valueWidth (≤ 64, fits every count type) so it does NOT exercise this corner.
- **Coverage gap (origin)**: `SignedMinForWidth`'s tests ran only in 64-bit-int host mode, so the 32-bit-int break was invisible to the green legs — the recurring "tests only exercise host-int" trap.
- **Discovery**: 2026-06-10 bnc-0.0.8 release-gate verification.

### Bare const-group member drops its INHERITED narrow type — checker accepts an overflow the explicit form rejects; IR-gen truncates → SILENT wrong value — all backends — REGRESSION from `05901f97`/`5fc5a52f` — ✅ RESOLVED 2026-06-10 (binate `b9d6d807`)
- **✅ RESOLVED 2026-06-10 (binate `b9d6d807`).** Per the user's semantics decision (**A — typed inheritance, Go-style**): a bare const-group member inherits the preceding member's TYPE, so it is range-checked at the inherited width. Fix threads the effective type (own if present, else the closest preceding member's, mirroring `genConstGroup`'s `prevTyp`) into the synthesized repeat in BOTH `checkGroupDecl` (`check_const.bn`) and `checkGroupDeclTentative` (`check_pending.bn`). Now `const ( B0 uint8 = 1<<iota; …; B8 )` rejects B8 at the declaration; an UNTYPED-base group is unaffected (members stay untyped, narrow at the use site). Also resolves the **B3 type-divergence** minor below (the parked bare member now carries the inherited type). Verified: full builder-comp suite 1328/0; cells 690 (typed-base decl overflow) + 691 (in-range typed bit-flag values) + 672 (reframed to untyped-base use-site overflow) green across all 5 modes; REPL path confirmed via manual bni (`println(B1)`→4). Known minor: the overflow error points at the inherited initializer expression (shared node), message correct. Two existing "Fits" unit tests + 672 reframed (they encoded the old untyped-narrowing behavior).
- **Symptom**: `const ( B0 uint8 = 1 << iota; B1; …; B8 )` — B8 = 1<<8 = 256 inherits `uint8`. The checker **ACCEPTS** it (compile exit 0); at runtime B7 correctly prints 128 but **B8 prints 0** (IR-gen types the bare member at the inherited width → `add i8 256, 0` → truncates). The explicit equivalents `var x uint8 = 256` AND `const B8 uint8 = 256` are BOTH rejected ("cannot assign untyped int to uint8"). So the bare-member path silently miscompiles an overflow the rest of the language rejects.
- **Root cause**: when synthesizing the repeat decl for a bare member, `checkGroupDecl` (`pkg/binate/types/check_const.bn:154`) sets `rep.TypeRef = inner.TypeRef` (nil for a bare member) — it never threads the PRECEDING member's TYPE. So `checkConstDecl` stores the member as untyped-int with NO range check. IR-gen's `genConstGroup` (`pkg/binate/ir/gen_const.bn`) DOES track `prevTyp` and types the bare member at the inherited width — hence the checker/IR disagreement + truncation. Same gap in the REPL path (`check_pending.bn:373`, B3).
- **Severity**: MAJOR — silent wrong-value miscompile from compiler-accepted source, contradicting conformance/645's documented rule and undercutting B1's own overflow-catching goal (B1's 672 cell uses a WIDE `int` base, so the bare-member-narrow path was untested). Held at major (not critical): trigger needs a narrow-typed flag word with an overflowing bare member.
- **Fix (NOT a semantics change)**: thread the inherited type into the synthesized rep in both `checkGroupDecl` and `checkGroupDeclTentative` (mirror genConstGroup's `prevTyp`), so the checker range-checks the bare member at the inherited width and rejects 256:uint8 like the explicit form — aligning the checker with itself and with IR. (Companion: the X3-highbit SIGNED sign-bit variant is a related divergence whose DIRECTION is contested/semantics-owned — see the CR-2-review section. Decide separately.)
- **Test**: `conformance/regressions/const-group-bare-inherited-overflow` (`.error`, expects "cannot assign untyped int to uint8"; currently compiles → xfailed all modes, binate `a77591e0`). A unit test pinning the inherited type on the synthesized rep is still wanted.
- **Discovery**: 2026-06-09 CR-2-batch review (B1 + X3-constfold finders, folded); runtime-confirmed (128 then 0; explicit form rejected).

### Named func-value type (`type Fn @func(...)`) is unconstructible — func-value flavour hint doesn't peel TYP_NAMED — all backends — PRE-EXISTING (NOT from `e15680d7`) — RUNTIME-CONFIRMED 2026-06-09
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

### [CR-2 Plan-1 review] `@readonly Box` / `*readonly Box` field read → literal 0 (and `&field` → SIGSEGV) — ✅ RESOLVED (landed binate `b4d5b37b` + `73bd9081`, 2026-06-09)
- **Symptom**: reading a field through a pointer whose POINTEE is wrapped (`@readonly Box`, `*readonly Box`, and nested fields of that type) compiles clean and reads literal `0`; taking the address `&p.v` lowers to a const-0 pointer then dereferences → exit 139 (SIGSEGV). Probe: `var p @readonly Box = mk(); println(p.v)` → `0` (expected 55).
- **Pre-existing (verified)**: built a compiler at fa265629 (parent of Defect 1) — same `0`. Defect 1 (`27c1ee8b`) fixed the OUTER wrapper (`readonly @Box`) and left the inner-pointee family untouched; it is NOT a regression introduced by the fix.
- **Root cause**: `isManagedPtrToStruct`/`isRawPtrToStruct` now peel and answer TRUE, but the ~19 value-extraction sites in `gen_selector.bn` (genSelector/genSelectorPtr: lines ~31,47,77,90,108,120,151,164,193,228,239,323,335,363,375,390,400,426,438) still read the UN-peeled `t.Elem`, whose `.Name` is "" → `lookupStructIdx == -1` → const-0 fallback.
- **Severity**: CRITICAL — silent miscompile (wrong value) + SIGSEGV on the lvalue form, on valid documented code. **Owner: Plan-1 (`pkg/binate/ir/gen_selector.bn`).** Fix: peel the pointee (`peelTransparent(varTyp.Elem)`) at each extraction site, mirroring `gen_access.bn`'s indexing path. Add conformance + IR-gen coverage for `@readonly Box`/`*readonly Box` read AND `&field` (assert GET_FIELD_PTR, not const-0). The green suite (conformance 660, `TestGenReadonlyManagedPtrFieldRead`) only exercises the OUTER wrapper — the inner-pointee family is uncovered → false confidence.
- **Discovery**: 2026-06-08 adversarial review of Plan-1 (probe-confirmed; pre-existence confirmed via pre-fix build).

### [CR-2 Plan-1 review] `readonly`-wrapped >16-byte aggregate parameter: by-value signature vs by-pointer call site → garbage / SIGSEGV — ✅ RESOLVED 2026-06-09 (LLVM+IR `79ebfa98`, native `c6fe0914`)
- **NATIVE half DONE (binate `c6fe0914`, Plan 3):** a `peelTransparent` helper (alias+readonly+named to a fixpoint, mirroring `Type.IsByvalParam`) now backs the native classifiers `IsAggregateTyp`, `IsFloatScalarTyp`, AND `StructTypeOf` (which all peeled only `UnwrapNamed`/TYP_NAMED). Un-xfails `conformance/matrix/readonly/pass-arg/value-struct-large` on native aa64 + x64 + x64-darwin; `common_test.bn` pins the peel. The `StructTypeOf` peel also fixed a pre-existing sibling — `readonly` struct-pointer field reads (`matrix/globals/readonly/struct`), un-xfailed on native aa64 + x64 (it was loud-failing unmarked on x64-darwin). Full native aa64 sweep 1288/0. The VM keeps its xfails (its own aggregate classifier — separate fix, still tracked by the VM xfail markers).
- **STATUS 2026-06-09**: the LLVM + IR-gen halves are FIXED. The two byte-identical `isByvalParam` copies (`codegen/emit_util.bn` for the param signature, `ir/gen_func.bn` for the `IsByvalParamRef` flag that drives the callee param-copy) had to agree; they were unified into one `Type.IsByvalParam()` in `pkg/types` (`scope.bn`) — which peels alias/readonly/named — and IR-gen + codegen (11 call sites) route through it, so the "two predicates must agree" hazard can't recur. Tests: `types_query_test.bn TestIsByvalParamPeelsWrappers` + conformance `matrix/readonly/pass-arg/value-struct-large` (green on every LLVM mode; xfailed on VM = shared-IR readonly field-read defect, this list; and on native — see remainder). **REMAINING (Plan 3, native backend):** `common.IsAggregateTyp` (`pkg/binate/native/common/common.bn:345`) peels only `UnwrapNamed` (TYP_NAMED), not readonly/alias → a `readonly Big` >16B param is passed by value on aa64 + x64 (both natives print garbage, confirmed 2026-06-09). Fix: peel readonly+alias there too (mirror `Type.IsByvalParam`). The new conformance cell xfails the native modes for this until it lands.
- **Symptom**: a param typed `readonly Big` (24-byte struct) / `readonly [4]int` / `readonly @[]int` is lowered by-value in the callee signature but passed by-pointer at the call site → silent garbage (exit 0) or SIGSEGV. Probe: `func first(b readonly Big) int { return b.a }; first(x)` with `x.a=123` → garbage `6102984704` (expected 123). Controls: plain `Big`/`@[]int`, below-16B readonly struct, and alias-typed slices all work — only readonly-wrapped >16B aggregates diverge.
- **Root cause**: `isByvalParam` (`pkg/binate/codegen/emit_util.bn:290`, and the copy at `gen_func.bn:26`) tests `t.Kind` against the aggregate set BEFORE peeling `readonly`, so a `TYP_READONLY` param returns false and never reaches the (peel-aware) `SizeOf() > 16` gate; `SizeOf`/`llvmType` DO peel → signature and gate disagree. Native `common.IsAggregateTyp` (`pkg/binate/native/common/common.bn:345`) peels only `UnwrapNamed` (TYP_NAMED — not readonly/alias).
- **Distinctness**: NEW — not the already-filed byval entry (that is the INDIRECT iface/func-value call; the DIRECT call is confirmed broken here only for readonly-wrapped aggregates). Same wrapper-transparency class as Defect 1/2, in the calling-convention layer the fixes never touched.
- **Severity**: CRITICAL — silent miscompile + SIGSEGV on both LLVM and native. **Owner: Plan-2/3 (codegen `emit_util.bn`/`gen_func.bn` + native `common.bn`).** Fix: peel transparent wrappers (readonly+named+alias to fixpoint) at the top of both `isByvalParam` copies and the native aggregate classifiers before the Kind test. Add conformance (readonly >16B struct/array/`@[]T` DIRECT call + plain/below-threshold/alias controls) on LLVM+native+VM; xfail until fixed. No existing test passes a readonly aggregate >16B as an argument.
- **Discovery**: 2026-06-08 adversarial review of Plan-1 (probe-confirmed).

### [CR-2 Plan-1 review] `[N][M]Struct` (value struct) field write `a[i][j].field = …` stores NOWHERE (silent data loss) + read → 0 — ✅ RESOLVED (landed binate `c2b9bbe8`, 2026-06-09)
- **Symptom**: `var a [1][1]B; a[0][0].v = 9; println(a[0][0].v)` → `0`; a following whole-struct read `var w = a[0][0]; println(w.v)` → `0` — so the WRITE went nowhere, not just the read. IR shows the value computed (`add i64 9, 0`) but NO `store`, and the read folds to const 0. Controls: single-level `s[0].v`, nested-array scalar `m[1][1]`, and whole-struct read `var w = a[1][1]` all work — isolating it to {nested-array base `a[i][j]`} × {struct-field selector}, on read AND write.
- **Relationship to filed**: the only tracked/xfailed test (`conformance/regressions/nested-array-managed-ptr-field`) covers ONLY `[N][M]@Box` (managed pointer), characterized as a read-path bug. The VALUE-struct variant and the write-stores-nowhere aspect are neither tested nor characterized → materially BROADER than the filed item; broaden that entry.
- **Severity**: CRITICAL — silent data loss (write to nowhere). **Owner: Plan-1 (`pkg/binate/ir` — root the field GEP at the in-place element pointer the inner index produces, for both gen_assign field-write lvalue and gen_selector index-selector read).** Add `[N][M]ValueStruct` field read+write conformance coverage (xfail per failing mode).
- **Discovery**: 2026-06-08 adversarial review of Plan-1 (probe-confirmed).

### [CR-2 Plan-1 review] whole-inner-array composite-literal store via slice/pointer element or struct field-init stores the alloca POINTER (managed-inner variant CORRUPTS) — ✅ RESOLVED (landed binate `cac7a2e0`, 2026-06-09)
- **Symptom**: `s[i] = [M]T{...}` (raw `*[][M]T` or managed `@[][M]T`) and `S{ [M]T{...} }` struct field-init store the inner alloca pointer instead of the array value → garbage (exit 0); the managed-inner variant `@[][N]@[]int` CORRUPTS (`index out of bounds: 0 (len 0)`, exit 1 — the misplaced pointer is read as a managed-slice header). Probe: raw-slice `s[0] = [2]int{5,6}; s[0][0]+s[0][1]` → `6102280160` (expected 11).
- **Root cause**: three sibling store arms keep the struct-only guard `... .Kind == TYP_STRUCT` instead of `isAggregateAllocToLoad`: `pkg/binate/ir/gen_composite.bn:97` (struct field init), `gen_control.bn:288` (TYP_POINTER/raw-slice arm), `gen_control.bn:324-330` (managed/generic slice-set arm). Defect 6 (`7583b669`) migrated only `genArrayLit` (gen_composite.bn:155) and `emitArrayElemStore` (gen_control.bn:23).
- **Severity**: MAJOR (silent wrong-code, exit 0; managed-inner corrupts). **Owner: Plan-1 (`pkg/binate/ir/gen_composite.bn`, `gen_control.bn`).** Fix: replace all three struct-only guards with `isAggregateAllocToLoad(rhs, <slotElem/elemTyp/fields[i].Type>)`. Add conformance: raw-slice, managed-slice (plain-int AND managed-inner), struct-field composite-lit array stores; xfail any not fixed immediately. None has a tracking xfail today.
- **Discovery**: 2026-06-08 adversarial review of Plan-1 (probe-confirmed).

### [CR-2 Plan-1 review] concrete value cannot be iface-wrapped into a `readonly @Iface` / `readonly *Iface` target (reject-only) — ✅ RESOLVED (landed binate `5d9cdeb1`, 2026-06-09; NOT reject-only — needed a companion IR-gen boxing peel in coerceExprToType, else compile→SIGSEGV)
- **Symptom**: `var rr readonly @Getter = im` (concrete `@Impl`) → `cannot assign @Impl to readonly @Getter`; same for `return im` from a `readonly @Getter` func and arg-pass; the raw arm `readonly *Getter = &im` is symmetric. Dropping the outer `readonly` compiles.
- **Root cause**: `AssignableTo` (`pkg/binate/types/types_assignable.bn:110,120`) gates the two iface-wrap arms on `dst.Kind == TYP_INTERFACE_VALUE[_MANAGED]` with NO peel → a `TYP_READONLY` dst misses both and falls to `return false`. Same transparent-wrapper principle as Defect 2's DISPATCH-site fix, left unapplied at the CONSTRUCTION site.
- **Severity**: MAJOR (reject-only — soundness intact; blocks factory functions returning `readonly @Iface` and readonly-iface params). **Owner: Plan-1 (`pkg/binate/types/types_assignable.bn`).** Fix: peel an outer `TYP_READONLY` (`resolveAliasAndConst(dst)`) before both iface-value Kind checks (raw + managed). Add conformance for concrete→readonly-iface construction across var-init/return/arg-pass.
- **Discovery**: 2026-06-08 adversarial review of Plan-1 (probe-confirmed by the review; reject-only).

### [CR-2 Plan-1 review] method call through an alias-typed receiver (`type AB = @Box`) rejected "cannot call non-function" (reject-only) — ✅ RESOLVED (landed binate `b24978b6`, 2026-06-09)
- **Symptom**: `type AB = @Box; func (b *Box) m(); var r AB = p; r.m()` → "cannot call non-function"; `type RB = readonly Box; ... r.peek()` likewise. Direct (un-aliased) forms compile; FIELD access through the same alias works.
- **Root cause**: `ReceiverBaseNamed` (`pkg/binate/types/types.bn:458`) peels POINTER/MANAGED_PTR/READONLY but NOT ALIAS; `check_method.bn:77` calls it on the raw `recvType`. Attributed by `git` to 05fb3216 (readonly migration), NOT 408cc533 — pre-existing.
- **Severity**: MAJOR (reject-only). **Owner: Plan-1 (`pkg/binate/types`).** Fix: use the already-resolved `resolved.ReceiverBaseNamed()` for method lookup (keep raw `recvType` for the object-const classification), or make `ReceiverBaseNamed` peel `TYP_ALIAS`. Add method-call-through-alias conformance + a `ReceiverBaseNamed` unit test.
- **Discovery**: 2026-06-08 adversarial review of Plan-1.

### [CR-2 Plan-1 review] cyclic named type + the new `==`/`<` operand checks → infinite hang (`==`) / SIGSEGV (`<`) — ✅ RESOLVED (landed binate `68a62f8c`, 2026-06-09; def-time reject + bounded operand-predicate guards)
- **Symptom**: `type A B; type B A; func f(a A, b A) bool { return a == b }` → exit 124 (hang in `comparabilityKind`'s unguarded Underlying-loop, `pkg/binate/types/types_query.bn:235`); self-cycle `type A A` + `==` → 124; relational `a < b` → exit 139 (stack overflow in `IsNumeric`→`IsInteger`/`IsFloat`). The same cyclic type WITHOUT a comparison compiles through the front-end → the new `checkEqOperands`/`relationalOperandOK` entry points (commit `60719e01`, Defect 5) are the specific trigger.
- **Relationship to filed**: the underlying cyclic-type bug is filed, but a "neither introduces nor worsens" note there is WRONG — 60719e01's operand checks genuinely introduce the hang/SIGSEGV on the comparison path. The reviewer also refuted the filed claim that the old path already SIGSEGV'd via AssignableTo (`Identical`'s name-based TYP_NAMED branch short-circuits, no recursion). Amend that entry's attribution.
- **Severity**: MAJOR (compiler DoS — hang/crash on pathological but valid-to-parse input). **Owner: Plan-1 (`pkg/binate/types`).** Fix direction: cycle detection at type-definition time + a shared visited/depth guard on the Underlying-walking helpers (`comparabilityKind`, `IsNumeric`/`IsInteger`/`IsFloat`).
- **Discovery**: 2026-06-08 adversarial review of Plan-1.

### [CR-2 Plan-1 review] unary minus on a NAMED sub-word/non-host-width int → invalid IR (`sub i64 0, %i8`) — Defect-9 fix incomplete for `TYP_NAMED` — ✅ RESOLVED (landed binate `3c609caf`, 2026-06-09; conformance-pinned — the unit-test harness can't resolve named-type underlyings)
- **Symptom**: `-x` on a named integer type (`type Small uint8`, `type Tiny int8`, `type Mid int16`, `type W int64`) emits `sub <host-i64> 0, %i8/%i16` → clang hard error `'%vN' defined with type 'i8' but expected 'i64'` (no binary). Probe: `type Small uint8; var ns Small = -s` → `error: '%v3' defined with type 'i8' but expected 'i64'`. Plain (non-named) sub-word `-x` works (prints 251). The named-`int64` case only links on this 64-bit host (host int==i64); on the 32-bit primary target it emits `sub i32 0, %i64` → silent truncation (the conformance-423 class).
- **Root cause**: `genUnary`'s MINUS arm (`pkg/binate/ir/gen_expr.bn:225-236`) selects `negTyp = arg.Typ` only when `arg.Typ.Kind == TYP_INT` (or float, or checker-resolved `TYP_INT`); a `TYP_NAMED` operand misses BOTH guards → falls through to host `types.TypInt()`. The commit (`fce07ccd`) claims to be "the exact analog of the `~` fix", but the TILDE arm (`gen_expr.bn:~249`) sets `bnTyp = arg.Typ` UNCONDITIONALLY (passes TYP_NAMED through; `llvmType` unwraps it to the underlying width), so `~` is correct for named sub-word ints while `-` is a build break — the MINUS fix is strictly weaker than the `~` fix it mirrors.
- **Severity**: MAJOR (hard build break on named sub-word negation; silent truncation for named int64 on 32-bit). **Owner: Plan-1 (`pkg/binate/ir/gen_expr.bn`).** Fix: type OP_NEG at `arg.Typ` for any non-float concrete operand (mirroring TILDE), letting `llvmType` unwrap TYP_NAMED — do NOT gate on `Kind == TYP_INT`. Add a regression mirroring `conformance/regressions/unary-minus-subword.bn` with `type` over int8/16/32/64 operands (fails the build today), plus a named-type unit test; correct the commit message's "exact analog" claim. The added tests use only PLAIN sub-word ints, so the TYP_NAMED hole is invisible to CI.
- **Discovery**: 2026-06-08 adversarial review of Plan-1 (probe-confirmed by me).

### [CR-2 Plan-1 review] Memory-safety / refcount audit — CLEAN on the probed Plan-1 paths (2026-06-08)
- The dedicated memory-safety finder failed to emit output twice; I audited the highest-risk Plan-1 refcount changes by hand with `rt.Refcount`-balance probes. **All balanced — no leak / UAF / double-free found:** (a) Defect-1 `peelTransparent` on `readonly @Box` (the managed/raw-classification flip risk): `var p readonly @Box = b` RefIncs 1→2, stays 2 while alive, scope-end RefDec balances — the peel makes the readonly-wrapped managed ptr counted (correct). (b) Defect-6 managed array element (`a[0] = b` on `[1]@Box`): 1→2→2 balanced. (c) Defect-3/4 multi-return managed field (`x, n := wrap(b)` returning `@Box`): 1→2→2 balanced. NOT exhaustive (the `=`-destructure path M1 fails to compile, and the un-migrated store arms M2 can't be reached with managed inner without crashing), but the directly-attributable Plan-1 refcount changes are sound.

### [CR-2 Plan-1 review] AMEND existing CRITICAL "iface-upcast −1-offset footgun" (filed `866b935`) with two new reproducer details
- **(a) Concrete trigger via the Kind axis**: `ifaceValueTypesAgree` (`gen_util.bn:225`) keeps `if a.Kind != b.Kind { return false }`, so a managed↔raw decay of the SAME canonical interface (`@Empty → *Empty`) still takes the upcast path → `IfaceParentSlotOffset(X,X) = −1` → `getelementptr inbounds i8*, i8** %vt, i64 −1` (UB). **Probe-confirmed** the −1 GEP is emitted. **Reachability is bounded and currently HARMLESS**: the type checker rejects `@Iface → *Iface` for any NON-empty interface (`isDescendantInterface(X,X)=false`), and `@Iface → *any` uses the offset-0 `any` special case — so −1 fires only for zero-method interfaces, which never dereference the vtable. Latent wrong-code, not a reachable crash. Targeted fix when the footgun is addressed: in `ifaceValueTypesAgree`, when canonical `(Pkg,Name)` AGREE return true regardless of Kind (same-interface managed↔raw relabel needs offset 0); a blanket Kind-ignore would be WRONG (a genuine `@Child → *Parent` upcast must keep its real positive offset — probe shows +1).
- **(b) Cross-backend divergence on the −1**: the LLVM lowering (`emit_iface_upcast.bn:57-60`) writes the offset verbatim (`i64 −1`), while BOTH native backends guard with `if byteOff > 0` (`aarch64_dispatch.bn:166`, `x64_dispatch.bn:242`) and so leave the vtable pointer unchanged (accidentally offset 0). So the same IR yields base−1 on LLVM vs base on native. The fix should make −1 a hard assert in all three lowerings (not a silent GEP) and have `IfaceParentSlotOffset(X,X)` return 0 like the `any` case.

### [CR-2 Plan-1 review] MINOR / doc-comment & xfail-hygiene corrections (2026-06-08)
- **N2 (misleading comment, no bug)**: `gen_iface.bn:86-90`'s `peelTransparent(iv.Typ)` is DEAD for the readonly case (`stripConstForIR` in newInstr/EmitLoad already removed it); the load-bearing fixes are in `check_method.tryMethodCall` + `isInterfaceMethodCall`. Correct the comment (reframe as defensive or drop).
- **N3 (misleading comment + 1 xfail cell)**: `checker_errors.bn:193-194` and `types_query.bn:248-251` claim comparability is "deferred to the concrete instantiation" — NO instantiation-time check exists; `eq[*[]int]`/`eq[@[]int]`/`eq[struct]` emit invalid `icmp` at instantiation (pre-existing, tracked as a user-owned follow-up). Fix the doc-comments; add an xfail `eq[@[]int]` cell (deterministic clang failure).
- **N1 (narrow, pre-existing)**: out-of-range CONSTANT shift count is wrapped into [0,width) by `ensureWidth` BEFORE the overshift guard (`v <<= 256` on uint8 → 1 not 0; signed `int8 >>= 256` stays -64 not sign-filled). Same in expression form. Fix in the shared shift lowering (guard on the un-truncated count); add wrapping-count conformance.
- **N10/N11 (xfail hygiene)**: funcval-multi-return arm32/x64 un-xfail is SOUND (record why in a note). iface-multi-return x64 xfails are stale and arm32 xfails are mislabeled "native" though arm32 compiles via LLVM — rewrite the markers (x64-darwin verified / x64-linux unverified-no-qemu; arm32 → LLVM, separate unconfirmed 32-bit ABI).
- **Coverage-only (verified-correct paths)**: 659 omits raw-pointer-index compound-shift (`p[i] <<=`) and signed `>>=` overshift on non-IDENT lvalues; the genShortVar nameless `multiReturnFieldTypes` fallback has no IR-gen unit test / no managed-component func-value `:=` cell; Defect-2b raw-pointer & value receiver rows have no conformance/unit coverage (the reject paths are soundness-critical and the TYP_POINTER/TYP_MANAGED_PTR arms are duplicated).

## CRITICAL

### Global address (`&G`) as an rvalue dropped at `OP_CAST` (all 3 non-VM backends) + iface-method ARG (aa64 + LLVM) — `emitValOperand`/`emitValRef` per-op whack-a-mole — ✅ RESOLVED 2026-06-09 (LLVM `d086ccac`, native `4a9775cf`)
- **STATUS 2026-06-09**: RESOLVED in two halves. LLVM (CR-2 Plan-2 Round-2, binate `d086ccac`): `emit_ops.bn` `emitCast` precomputes the source via `emitValRef(buf.New(), Args[0])` (mirroring `emitBitCast`) and writes it at every arm; `emit_iface_call.bn:156` switched `emitRef`→`emitValRef`; conformance `669_cast_global_addr` + `670_iface_method_global_addr` added (green on all LLVM modes + VM, xfailed on the native modes pending Plan 3). NATIVE (Plan 3, binate `4a9775cf`): Facet 1 native OP_CAST (`x64_dispatch.bn`, `aarch64_dispatch.bn`) and Facet 2 native aa64 iface-arg (`aarch64_iface.bn` `emitCallIfaceMethod`, now threading `pkgName`) both route the source/arg through `emitValOperand`; only the scalar branch needs it (a global ref is always a scalar pointer). Un-xfailed `669` on all 3 native modes and `670_iface_method` on aa64; both green on native aa64 + x64-darwin (`x64`-elf not host-runnable, but the OP_CAST fix is in shared `x64_dispatch.bn`, verified via x64-darwin). Native unit pins assert the OP_CAST source materializes (aa64 ADRP+ADD+MOV; x64 RIP-LEA). The architectural root (per-op whack-a-mole vs the VM's op-agnostic materialization pass) below still stands as the durable-fix recommendation (FILED follow-up: make `emitValOperand`/`emitValRef` the SOLE value-operand fetch).
- **Context**: binate `0c707e1f` (x64) + the earlier aa64 `emitValOperand` work fixed `&G`-as-rvalue at the *enumerated* value-operand sites (return value, compare operands, store value, call/dispatch args, `OP_BIT_CAST`). An adversarial multi-agent review of that work found the enumeration was INCOMPLETE — two more value positions still drop the `IsGlobalRef` pseudo (ID -1) via bare `getOperand` / `emitRef`.
- **Facet 1 — `OP_CAST` source: silent wrong-code (native) + compile error (LLVM), REPRODUCED on all 4 host-runnable modes**: `var addr int = cast(int, &G)` → `builder-comp` (LLVM) `error: use of undefined value '%v-1'` (clang fails on `ptrtoint i8* %v-1`); on `builder-comp_native_x64_darwin` AND native aa64 the cast drops the address so the `bit_cast`-back round-trip prints the UNCHANGED global (`10`, not `11`) — silent corruption (a dropped cast leaves a garbage register that gets reused). VM is CORRECT. Sites: `pkg/binate/native/x64/x64_dispatch.bn:388` (OP_CAST arm, bare `getOperand(ins.Args[0].ID)`), `pkg/binate/native/aarch64/aarch64_dispatch.bn:411` (same), `pkg/binate/codegen/emit_ops.bn` `emitCast` (uses `emitRef`, not the `emitValRef` precompute-srcRef treatment `emitBitCast` already has). `bit_cast(int,&G)` was fixed (conformance 551); the value-preserving `cast(int,&G)` sibling was MISSED and is UNCOVERED.
- **Facet 2 — iface-method ARG: silent wrong-code (aa64) + compile error (LLVM)**: `i.m(&G)` (a global address passed to an interface method) — aa64 `emitCallIfaceMethod` (`pkg/binate/native/aarch64/aarch64_iface.bn`) never took `pkgName` and fetches its scalar args via bare `getOperand` (the x64 sibling WAS routed through `emitValOperand` in `0c707e1f`; aa64 was a pre-existing gap); LLVM `pkg/binate/codegen/emit_iface_call.bn:156` uses `emitRef(out, argInstr.ID)` not `emitValRef`. NO conformance test passes `&global` to an iface method on ANY backend.
- **ROOT CAUSE / why this recurs (the architectural finding)**: the VM is correct for ALL these sites FOR FREE because `pkg/binate/vm/lower_func.bn` (~276-291) does an OP-AGNOSTIC pre-pass — for every instruction it materializes any `IsGlobalRef` arg into a fresh register (`BC_LOAD_IMM`) and rewrites `Args[k].ID` BEFORE the op is lowered. The native + LLVM backends handle `IsGlobalRef` PER-OP (whack-a-mole), so each value-operand site must be individually converted and the missed ones are exactly these defects. The DURABLE fix is to centralize: make `emitValOperand` / `emitValRef` the SOLE value-operand fetch (audit every site so none can forget), or mirror the VM's up-front materialization pass. Also-noted latent asymmetry (true negative today): `OP_FUNC_VALUE` data slot (`x64_dispatch.bn:166`, aa64 analog) is NOT global-ref-aware while its `OP_IFACE_VALUE` sibling IS — one IR change from becoming live.
- **Severity**: CRITICAL — silent wrong-code / corruption on idiomatic, type-valid programs (`cast(<int>, &global)`, `iface.method(&global)`) across the native backends, plus hard compile errors on the primary LLVM backend. Confined to the global-address-as-value feature; reproduced on the dev-host-runnable modes.
- **Tests to add WITH the fix**: a conformance cell `cast(int, &G)` round-trip (FAILS pre-fix on LLVM/aa64/x64-darwin, PASSES on VM); an `i.m(&G)` iface-method-arg cell; unit pins for the OP_CAST + iface-arg `emitValOperand`/`emitValRef`.
- **Discovery**: 2026-06-08, adversarial multi-agent review of plan-cr2-3 work (`cc2ddcc4` / `0c707e1f`); OP_CAST empirically reproduced on all four host-runnable modes (independently re-confirmed after the review). Per user decision (2026-06-08) this is FILED, not yet fixed; native parts (x64/aa64 OP_CAST + aa64 iface-arg) are Plan-3, the LLVM parts (`emit_ops.bn` emitCast, `emit_iface_call.bn:156`) are codegen/Plan-2.

### `71ff7489` (length-0 slices → nil-equivalent rep) regressed the bytecode VM — `110_cross_pkg_type_alias` fails on `builder-comp-int` (a default CI mode) — RESOLVED 2026-06-09 (plan-cr2-3 Round-2, binate `c997cf2e`)
- **Symptom**: `conformance/110_cross_pkg_type_alias` fails on `builder-comp-int`: the final `if mylib.IsEmpty(MakeResult("")) { println("empty ok") }` does NOT print, so the output is missing the `empty ok` line. `IsEmpty(r)` is `len(r) == 0` over an empty `@[]char` produced by `make_slice(char, len(""))` — the VM reads `len != 0` for the empty managed-slice. Green on `builder-comp` (LLVM); fails ONLY on the VM. No xfail marker (was passing).
- **Bisect (CONFIRMED)**: `110` PASSES on `builder-comp-int` at `43cb195d` (71ff7489's parent) and FAILS at `71ff7489` / `cc2ddcc4`. So `71ff7489` ("ir: enforce length-0 slices have no backing (nil-equivalent rep)") is the cause.
- **Mechanism (direction)**: `71ff7489` made empty string/byte literals emit `EmitConstNil`, and normalized `lo==hi` subslices / empty raw composite literals to the `{null,0}` nil-equivalent. The VM (`pkg/binate/vm`) was not updated to AGREE with the new length-0 rep, so either `len("")` (empty-literal-as-nil) or `len(make_slice(char,0))` reads non-zero on the VM and the emptiness check inverts. LLVM/codegen handle the new rep; the VM lowering/runtime does not.
- **Severity**: MAJOR — breaks a default CI mode (`builder-comp-int`) with wrong output on the idiomatic `len()==0` empty-slice test through the VM. Narrow blast radius: the full-suite VM sweep showed ONLY `110` failing.
- **Discovery**: 2026-06-08, plan-cr2-3 post-landing full-suite `--check-xpass` sweep on both arches + LLVM + VM (the only VM failure in the full suite).
- **Root cause (confirmed)**: the bytecode VM carries every aggregate value (slice / struct / iface- / func-value) by the ADDRESS of its in-memory image, but lowered EVERY `OP_CONST_NIL` — scalar AND aggregate — to `BC_LOAD_IMM 0`. For an aggregate const-nil that 0 is a null address; a by-address consumer (a call argument, an `OP_EXTRACT` such as `len()`) reads through null. The var-decl form (`666`) was masked because `OP_STORE` of a const-nil memsets the destination directly and never reads the source register; `MakeResult("")`'s direct call-argument form was not. The codegen + native backends already give an aggregate `OP_CONST_NIL` a dedicated zero-filled data region; the VM was the lone backend that didn't.
- **Fix (binate `c997cf2e`)**: the VM planner (`lower_func.bn`) reserves a dedicated frame region for each aggregate `OP_CONST_NIL` — zeroed at frame entry by `pushFrame`, never written — and the lowering (`lower_instr.bn`) points `BC_STACK_ALLOC` at it, so the nil value's register is a valid address of a `{0,...}` image. Scalar nils stay the immediate 0. `110` green on `builder-comp-int`; new `conformance/668_empty_slice_byaddr` isolates the mechanism (direct `len()`, call-argument, empty composite literal, `make_slice(_,0)` by argument) green on LLVM + all three `-int` modes and fails pre-fix; full `builder-comp-int` sweep 1165/0; VM unit tests pass.

### Interface alias re-export → spurious `OP_IFACE_UPCAST` (−1 offset) → SIGSEGV — RESOLVED 2026-06-08 (plan-cr2-1 Defect 8, binate `a869e8e7`)
- **Symptom (was)**: a consumer that imports package A — which re-exports `interface I = B.I` from package B — and uses `@A.I` crashed (SIGSEGV) dispatching a method. `conformance/665_transitive_iface_reexport`.
- **Actual root cause** (lldb-traced; the original "degrades to `i8*`" hypothesis was WRONG — the iface value is correctly 2-word throughout): `A.Get()` returns `B.Make()` (typed `@B.I`) coerced to declared return type `@A.I` (the alias). `ifaceValueTypesAgree` (`gen_util.bn`) compared the two iface types by raw `(Pkg, Name)` — `pkg/B` vs `pkg/A` — without resolving the alias chain, so they looked distinct and the coercion emitted a spurious `OP_IFACE_UPCAST`. Its offset is `IfaceParentSlotOffset(B.I, A.I)` = **−1** (the alias is not a PARENT of its target), used directly as a vtable GEP index → `vtable − 8` → the method slot loads the dtor word (NULL) → call through null → SIGSEGV.
- **Fix**: `ifaceValueTypesAgree` canonicalizes both sides through the alias chain (`canonicalIfacePkg`/`canonicalIfaceName`) before comparing; an alias IS the same interface, so no upcast is emitted. `conformance/665` un-xfailed on all 6 runnable modes (LLVM ×3, VM single-int ×2, native aa64) + 2 unit tests.
- **Residual**: `665.xfail.builder-comp-int-int` kept (retagged) — blocked by the SEPARATE pre-existing `-int` multi-package crash below, not this defect. And the −1-as-GEP-offset footgun this exposed is filed as its own CRITICAL (next entry).

### Iface upcast lowerings use `IfaceParentSlotOffset`'s −1 sentinel directly as a vtable GEP/byte offset — silent vtable corruption — ✅ RESOLVED 2026-06-09 (binate `ca155319`)
- **STATUS 2026-06-09**: FIXED as the coordinated 3-plan set the "two sub-parts" below describe. (b) IR (`gen_iface_extends.bn`): `IfaceParentSlotOffset` returns **0** for the same canonical interface (extends the `any` special-case) — the source vtable IS the target's, so no slot adjustment; this removes the only emittable −1 (the zero-method `@X→*X` decay). (a) the LOWERINGS — `emit_iface_upcast.bn` (LLVM) and `aarch64/x64_dispatch.bn` (native) — now hard-`panic` on a negative offset instead of feeding it to a GEP / silently skipping it (`if byteOff > 0`). The IR fix lands WITH the asserts (without it they'd fire on the zero-method decay's −1). Verified the asserts never false-fire: the full iface conformance suite (140 cells) is green on builder-comp, builder-comp-comp, builder-comp-int, native aa64, and native x64-darwin. Tests: `gen_iface_extends_test.bn TestIfaceParentSlotOffsetSameInterfaceIsZero` + conformance `685_iface_same_interface_decay` (`@E→*E`, green on every mode; with the asserts a regression to −1 is a compile error). The stale "leaves offset at 0" comment on `TestIfaceParentSlotOffsetNotAParent` was corrected.
- **Symptom**: `OP_IFACE_UPCAST` lowering computes the target's vtable slot via `IfaceParentSlotOffset(src, target)`, which returns **−1** when `target` is not a (transitive) PARENT of `src`. The LLVM lowering (`pkg/binate/codegen/emit_iface_upcast.bn:34`) and the native aarch64/x64 dispatch lowerings feed that result DIRECTLY into a `getelementptr` / byte-offset with **no −1 guard** — so a −1 walks the vtable pointer one slot BEFORE its base, and the dispatched method slot reads the wrong word (e.g. the dtor) → call through garbage/null → SIGSEGV or silent wrong dispatch. The unit-test comment at `gen_iface_extends_test.bn:72-75` CLAIMS the caller "leaves offset at 0" for the −1 case, but no caller actually clamps it — the comment is aspirational, the code is a footgun.
- **How exposed**: Defect 8 (above) hit exactly this — a spurious alias upcast produced a −1 offset → `vtable − 8` → crash. The Defect-8 fix stops the ALIAS path from emitting that upcast, but the lowering still trusts the offset blindly, so any OTHER non-parent upcast (or the same-interface case below) corrupts the same way.
- **Severity**: CRITICAL — silent vtable-base corruption → memory-unsafe crash / wrong method dispatch. Currently latent (no remaining known emitter of a −1-offset upcast), but a sharp edge: a future mis-emitted upcast becomes a memory-safety bug instead of a loud error.
- **Two sub-parts**: (a) the LOWERINGS (`pkg/binate/codegen/emit_iface_upcast.bn`, `pkg/binate/native/{aarch64,x64}/*_dispatch.bn` — Plan-2/Plan-3 territory) should clamp/assert: a −1 must be a hard error (or 0 only where provably same-interface), never a silent GEP index. (b) `IfaceParentSlotOffset` itself (`pkg/binate/ir/gen_iface_extends.bn` — Plan-1) returns −1 for the SAME canonical interface `(X, X)` rather than 0 — the `any` case is special-cased to 0 but same-interface is not, so a managed↔raw decay of the same interface (which still routes through the upcast path via the Kind check in `ifaceValueTypesAgree`) would also corrupt. Reachability of that decay-through-upcast not confirmed.
- **Discovery**: 2026-06-08, while root-causing Defect 8 (disassembly of `Get`'s `subs x1, x8, #0x8`). User opted to file (not fix) for now; decide separately.

### Interface-method dispatch of a multi-return method mis-packs the result tuple on two backends — SILENT wrong values — BOTH FACETS RESOLVED 2026-06-08 (residual: arm32 + x64-linux xfails)
- **STATUS 2026-06-08 — RESOLVED on every runnable host/native mode.** The CR-2 SEAM (`6c39d460`) fixed the front-end (typed the iface multi-return as the same anonymous tuple struct a direct multi-return uses), which exposed two BACKEND tuple-lowering gaps; both are now fixed: **Facet A** (LLVM >16-byte sret) by Plan-2 `43cb195d`, **Facet B** (native aa64 sub-word) by Plan-3 `cc2ddcc4`. `iface-multi-return/{int,u16}/{2,3,4,5}` are green on `builder-comp{,-comp,-comp-comp}` (LLVM), all three `-int` modes (VM), `builder-comp_native_aa64` (aa64), and `builder-comp_native_x64_darwin` (x64 via Rosetta). The DIRECT multi-return call was already correct for the same shapes, which is what localized each gap to iface-dispatch lowering.
- **Facet A — LLVM, >16-byte result (codegen, Plan 2) — RESOLVED (`43cb195d`)**: `iface-multi-return/int/{3,4,5}` (3/4/5 `int`s = 24/32/40-byte struct) printed GARBAGE on `builder-comp{,-comp,-comp-comp}`. The LLVM iface-call emission dispatched a register-returned tuple via sret incorrectly; `emit_iface_call.bn` now dispatches it by value (plan-cr2-2 Defect 3). LLVM-host int/3,4,5 xfails removed.
- **Facet B — native aa64, sub-word result (native, Plan 3) — RESOLVED (`cc2ddcc4`)**: `iface-multi-return/u16/{2,3,4,5}` (2..5 `uint16`s) printed wrong values on `builder-comp_native_aa64` (`int/*` was correct). Root cause: `common.IsMultiReturnCall` recognized only `OP_CALL`/`OP_CALL_FUNC_VALUE`, so an iface multi-return fell into the aggregate-single-return collect; on aa64 (one register per tuple field) that collect read `ArgWords` eightbytes and dropped every field past the first (e.g. `(u16,u16)` lost field 1). x64 survived because its callee coalesces sub-word fields into the RAX/RDX byte image. Fix: add the `OP_CALL_IFACE_METHOD` arm to `IsMultiReturnCall` — every downstream native site keys on it (PlanFrame tuple-vs-pointer spill, the per-arch collect, EXTRACT's `SpillHoldsAggregatePointer` split, `CallReturnsBigMultiReturn`), so aa64 runs its per-field collect and x64 runs `collectMultiReturnTuple` (pre-wired by `760402b7`). aa64 u16/* unxfailed (XPASS confirmed); x64 verified via darwin-x64; new common unit tests pin the classifier arm + PlanFrame split.
- **RESIDUAL (not part of either facet)**: `iface-multi-return/{int,u16}/{2,3,4,5}` stay xfailed on (a) **arm32** (baremetal + linux) — arm32 has no native backend and goes through LLVM, yet stays broken after the host-LLVM Facet-A fix, so it is a SEPARATE arm32-specific issue (cause unconfirmed — likely 32-bit sub-word/aggregate ABI; not runnable on the dev host); and (b) **`builder-comp_native_x64-comp_native_x64`** (x64-linux/ELF) — the x64 backend codegen is verified correct via the darwin-x64 runner (same codegen, different objfmt), so these are almost certainly STALE, but unrunnable on this host (no qemu) so left for a follow-up where x64-linux executes. Both residuals are tracked here; neither is silent wrong-code on a runnable mode.

### Cross-package struct-name mangler collision (`reflect.Package` vs a module's own `type Package`) broke the `bni` build — FIXED 2026-06-08 (`7ebafc51` mangler fix + `aa8d6828` Defect-2 re-land)
- **STATUS 2026-06-08 — FIXED & LANDED.** Fixed at its source: the synthetic `_Package()` descriptor's `reflect.Package` result type now carries its path-qualified name `pkg/builtins/reflect.Package` (`7ebafc51`, `pkg/binate/ir/gen_import.bn` `qualifiedReflectPackageType`), so the mangler folds it to the reflect package's own symbol and it can never collide with the compiling module's structs. Defect 2 (the `m.Globals` scan + `TYP_NAMED`/`TYP_ARRAY` discovery arms) was then re-landed (`aa8d6828`) — safe now — with `conformance/657_cross_pkg_struct_global` and the `globals/noinit/named-struct` cell. Verified on `builder-comp` + **`builder-comp-int`** (the VM build that broke). History: the original Defect-2 commit `b0402d04` was REVERTED (`1ae18289`) to un-break main, then re-landed on top of the mangler fix; Defect 1 (`f2ebaca1`, global static-zero NAMED-peel) was never reverted (independent, correct).
- **FOLLOW-UPS — ✅ BOTH DONE 2026-06-10 (Option B).** (a) **Class-level fix
  (Option B) — LANDED `59771b8d`..`f5b3b387` + identity fix `1e37a637`.** Struct
  types now carry their fully-qualified name at definition (checker qualifies via
  `currentPkgPath`/`QualifyName`; IR registers qualified; lookups qualify-if-bare),
  killing the cross-package collision class at the root; `Identical` distinguishes
  cross-pkg same-name structs (was still comparing the bare TYP_NAMED wrapper); the
  latent `genMethodValue` cross-package value-receiver leak is fixed too.
  Byte-identical, green across all modes + self-host. (b) **Dedup-mismatch guard —
  LANDED `15f1fae2`.** `addStructDef` now aborts as a codegen precondition when a
  mangled-name match has a disagreeing field layout (`structShapesMatch`), instead
  of silently keeping the first. See `plan-cr2-optionb.md`.
- **Symptom**: building `cmd/bni` via gen1 (any `-int` mode: `builder-comp-int` / `builder-comp-int-int` / `builder-comp-comp-int`) fails — `clang … pkg__binate__loader.ll: error: invalid getelementptr indices` on `getelementptr %bn_pkg__binate__loader__Package, …Package* %v.sc, i32 0, i32 4`. The emitted `Package` LLVM struct type has fewer fields than the field-4 GEP expects. Deterministic (reproduced 3×, fresh build dirs).
- **Bisected**: builds `bni` cleanly at `27c1ee8b` (b0402d04's parent); FAILS at `b0402d04`. So `b0402d04` ("codegen: discover struct types reachable only through globals", plan-cr2-2 Defect 2) is the culprit. NOT caused by the plan-cr2-3 Defect-1 commit (`68616b20`, native/VM only) — the regression reproduces at `b0402d04` without it.
- **Root cause (direction — needs confirmation)**: `b0402d04` added an `m.Globals` scan to `collectStructTypes` plus `TYP_NAMED→.Underlying` / `TYP_ARRAY→.Elem` recursion arms to `discoverStructFromType`. Claimed "purely additive," but in **cmd/bni's** module (which has globals cmd/bnc lacks — `builder-comp-comp`/gen2 appeared to still build, so the trigger is bni-module-specific) the new discovery emits the `Package` struct type with a wrong/truncated body (likely the `TYP_NAMED` arm registering the underlying struct under a name that collides via `addStructDef` dedup, OR an `m.Globals`-discovered path emitting a partial def), so a later field-read GEP into field 4 is out of range. Inspect the emitted `loader.ll` `%bn_pkg__binate__loader__Package = type {…}` def vs the GEP.
- **Scope (BROAD)**: the failing operation is gen1 (LLVM) compiling `loader` while building `cmd/bnc`/`cmd/bni`, so EVERY mode that rebuilds the toolchain via gen1 is broken — `-int` (bni build), `builder-comp_native_aa64`/`_x64` (the native-backend bnc binary is itself BUILT by gen1's LLVM codegen — CONFIRMED fails with the same `loader.ll` GEP), and gen2 (`builder-comp-comp`/-comp-comp-comp, once the stale gen2 cache is invalidated). Only `builder-comp` (BUILDER compiles cells directly, no gen1 recompile of `loader`) and unit tests for packages that don't import `loader` (e.g. the native/x64 backend test binaries) still build. Nearly all conformance verification is blocked until this is fixed.
- **Severity**: CRITICAL/MAJOR — breaks the self-hosted bytecode-VM build and ≥3 conformance modes on `main`; loud (compile error). Landed minutes before discovery (concurrent Plan-2 work); CI may not have run the `-int` modes against it yet.
- **Discovery**: 2026-06-08, building `bni` to test the `unary-minus-subword` regression cell during plan-cr2-3 Defect 1. `bni` had built fine earlier this session pre-rebase (at `c2aaaabf`).
- **Fix direction**: revisit `b0402d04` — revert + re-land with a self-host guard (a `builder-comp-int` smoke that builds the FULL `cmd/bni` toolchain, not just simple cells, would have caught it), or fix the struct-def emission in the new discovery arms.
- **Refined root cause + VERIFIED mitigation (2026-06-08, plan-cr2-2 author session)**: the trigger is specifically the `discoverStructFromType` recursion **arms**, NOT the `m.Globals` scan — removing only the scan does NOT fix it; removing the scan AND the `TYP_NAMED`/`TYP_ARRAY` arms DOES (bni builds clean). The colliding struct is the per-package **`reflect.Package` descriptor** payload (`<{ %BnSlice }>` = `{ Name *[]readonly char }` emitted by `emit_pkg_descriptor.bn`): a new arm reaches it with the UNQUALIFIED name `"Package"`, and `addStructDef` mangles every discovered struct via `mangle.StructName(modulePkgName, t.Name)` — the **current module's** prefix — so while compiling the `loader` module it registers as `bn_pkg__binate__loader__Package`, colliding (dedup, first-wins) with the loader's own 5-field `type Package`; the 1-field descriptor def wins and the field-4 GEP into the real Package is out of range. So this is a **cross-package struct-name mangler collision** (`addStructDef` keys by current-module prefix, not the struct's defining package) that the new discovery arms merely EXPOSE. gen2 builds because its `loader.o` is reused from the builder-compiled artifact (gen1 never recompiles loader for gen2); the `-int` path compiles `cmd/bni` fresh with gen1, hitting it. **Reverting `b0402d04` restores green (verified: revert of the discovery change on top of `f2ebaca1` → bni builds + `globals/struct` passes `-int`).** Proper fix: make `addStructDef` mangle a discovered struct by its DEFINING package (or ensure cross-package structs reach it qualified), so a same-named struct in the compiled module can't shadow it — then the discovery arms can be restored.

### `=` (assignment) multi-bind from an interface dispatch / func-value call mistyped every component as int — FIXED 2026-06-08 (`f8916b88`)
- **Found by the Plan-2 adversarial review.** `genMultiAssign` (`pkg/binate/ir/gen_assign_multi.bn`, the `a, b = …` form) derived per-component result types only from `lookupFuncResults(val.StrVal)` for a DIRECT call (`OP_CALL`). An interface dispatch (`OP_CALL_IFACE_METHOD`) and a func-value call (`OP_CALL_FUNC_VALUE`) have no callee name, so retTypes stayed empty and every component defaulted to `int`: a sub-word component was stored as i64 (invalid IR → clang reject) and a managed component skipped its Axiom-3 copy-RefInc (latent UAF if it had compiled). `a, b = iv.m()` / `a, b = fv()` with any non-int component thus failed to compile; the `:=` form (`genShortVar`) already had the `multiReturnFieldTypes` fallback, so the asymmetry hid it. Became reachable once iface/func-value multi-return dispatch started working (the CR-2 SEAM `6c39d460` + iface-dispatch-by-value `43cb195d` + func-value destructure `2a77188c`); no test caught it because the whole abi multi-return matrix binds with `:=` and uses only int/u16.
- **Fix**: mirror genShortVar's fallback in genMultiAssign (derive component types from the multi-return tuple struct when retTypes is empty). Additive. Pinned by `gen_assign_multi_test.bn` TestMultiAssignFuncValueCallCopyRefInc (verified red without the fix); end-to-end (uint16,int) and (int,@[]int) `=`-form iface + func-value repros compile/run, 200k-iter managed loop balances.
- **OPEN follow-ups (from the same review)**: (a) **coverage** — extend `conformance/gen-abi-matrix.py` with an `=`-form (assignment) binding axis + a managed-component type for the multi-return-through-dispatch cells (the surface that hid this bug; today all cells use `:=` and int/u16 only). (b) **stale xfail comment** — the surviving native `iface-multi-return/int/{3,4,5}` xfails (`builder-comp_native_x64`, arm32) blame the already-fixed SEAM ("drops multi-return result type"), not Plan-3's open native tuple-packing gap; rewrite the markers.

### VM mis-unpacks a SUB-WORD (uint16) multi-return returned through interface dispatch — SILENT wrong values — ✅ RESOLVED by the CR-2 SEAM (`6c39d460`)
- **STATUS 2026-06-08 — RESOLVED.** This was the symptom pre-SEAM, when the front-end dropped the iface multi-return result type (void-typed dispatch) so the VM's tuple lowering operated on a malformed shape; the symptom presented AS a sub-word `BC_EXTRACT` width bug (`13107300 = (200<<16)|100` for `(u16,u16)→(100,200)`). Once the SEAM typed the dispatch as a proper tuple struct, the VM lowers it correctly — `iface-multi-return/u16/{2,3,4,5}` pass on all three `-int` modes (verified 0-failed under `--check-xpass`; the SEAM removed the VM xfails). So the separately-planned VM `BC_EXTRACT` sub-word fix (plan-cr2-3 "Defect 5") is MOOT — the VM's value-mode `BC_EXTRACT` already does a sized sub-word read; the bug was upstream typing, not VM extract.
- **Original symptom (historical)**: `iface-multi-return/u16/2` printed `13107300, 1` on the VM instead of `100, 200`. The `int` variant was correct, which is why it read as sub-word-specific.
- **Discovery**: 2026-06-07, abi result-side matrix sweep. **Resolution confirmed**: 2026-06-08, post-SEAM `--check-xpass` sweep of the abi subtree on the three `-int` modes (0 failed).

### ~~Native (aa64/x64) mis-packs a SUB-WORD struct-return (`five-u8`) returned through a FUNCTION-VALUE call — SILENT wrong values~~ — FIXED aa64+x64 (binate `3950f59f`, plan-cr2-3 Defect 2); arm32 remains
- **FIXED (aa64/x64)**: the caller passes a retbuf for ANY aggregate funcval return, but the per-function shim only wrote retbuf for retSz 9..16 (usePack) / >16 (useSret) — a ≤8-byte aggregate fell into the SCALAR (tail-branch) shim and never wrote retbuf, so the caller read an unwritten alloc region. Lowered the shim's pack-path floor to cover an aggregate result of 1..16 bytes, gated on a new `shimReturnIsAggregate[_x64]` (a ≤8-byte SCALAR still tail-branches — size alone can't tell a scalar from a ≤8-byte aggregate). `funcval-return/five-u8` unxfailed on native aa64 (CI-verified) + x64 (verified via the darwin-x64 mode); 9..16 / sret / scalar funcval returns unaffected; native funcvalue-shim unit tests green.
- **arm32 REMAINS xfailed**: arm32 has no native backend (LLVM path) and mis-handles this for a SEPARATE, unconfirmed reason — not the native shim. The `funcval-return/five-u8` arm32 xfail reason now says so; needs its own investigation.
- **Follow-up (GAP)**: the only ≤8-byte funcval-return cell is sub-word (`five-u8`, 5B). A non-sub-word ≤8-byte cell (e.g. `two-u32`=8B or `{int32}`=4B) would pin the whole retSz≤8-aggregate class end-to-end (the fix keys on aggregate-ness + retSz≤16, NOT sub-word packing). Deferred: adding a STRUCTS shape to `gen-abi-matrix.py` generates all 6 abi families with arm32 / x64-linux xfails not verifiable on the dev host.
- **Was**: `funcval-return/five-u8` printed `16,146,211,…` on native aa64/x64 instead of `1,2,3,…`; the iface-dispatch variant and the DIRECT struct-return passed. Discovery 2026-06-07 (abi result-side matrix sweep); fixed 2026-06-08.

### `readonly` / `const` type modifier is broken for managed values — FULLY RESOLVED: field read (binate `27c1ee8b`), iface dispatch (binate `d3761004`), and the `readonly @Box` method-receiver rejection via the object-const model (binate `408cc533`)
- **Symptom 1 (SILENT wrong-code) — FIXED (binate `27c1ee8b`, plan-cr2-1 Defect 1)**: reading a field through a `readonly @T` managed pointer returned the wrong value. `var p @Box = make(Box); p.v = 7; var rp readonly @Box = p; println(rp.v)` printed `0`, not `7`. Root cause: genSelector / genSelectorPtr (and the managed/raw-ptr-to-struct predicates) didn't peel the IR-transparent `readonly`/named/alias wrapper, so the read fell through every Kind-dispatch arm to `EmitConstInt(0)`. Fixed by adding `peelTransparent` (readonly/named/alias to fixpoint) and peeling the dispatched type at acquisition + each `val.Typ` read. `field-read/*` matrix cells (+ `pass-arg/value-struct`, `globals/readonly/struct` on the modes their internal field-read unblocked) unxfailed; conformance 660 + a gen_selector unit test added.
- **Symptom 2 (compile error)** — iface dispatch part FIXED (binate `d3761004`, plan-cr2-1 Defect 2): `readonly @Iface` → `cannot access field on this type` is gone; `tryMethodCall` now resolves the receiver with `resolveAliasAndConst` (peels readonly) and `gen_iface.bn` peels the receiver before dispatch/mangling. **Still rejected**: `readonly @Box` calling a `*Box`-receiver method (`method/managed-struct` cell, xfailed).
- **The remaining rejection — FIXED via the object-const model (binate `408cc533`, plan-cr2-1 Defect 2b)**: `receiverShape`'s const flag now tracks OBJECT-constness only. An outer `readonly` on a POINTER (`readonly @Box`) is handle-const and no longer blocks dispatch — `readonly @Box` (const pointer, mutable object) calls any method, including `*Box`/`@Box`-receiver ones. Only an inner `readonly` on the pointee (`@readonly Box` / `*readonly Box`) or an outer `readonly` on a VALUE receiver (`readonly Box`) is object-const, and may call only a const-pointee-receiver method (`*Box`/`@Box`-receiver methods rejected — they could mutate the const object). Confirmed `@(readonly Box)` IS accepted (parses as `@readonly Box`) and `*readonly Box` receivers are supported. No const-method annotations. `method/managed-struct` unxfailed (all backends); 3 `check_method` unit tests; spec clarified in `claude-notes.md` ("Method dispatch keys off OBJECT-constness").
- **Impact**: `readonly`/`const` is effectively unusable on any managed value — interfaces (`@Iface`) and managed structs/ptrs with methods can't be called at all, and `readonly @struct` field reads silently corrupt. Directly blocks a *readonly* `io.EOF` sentinel.
- **Discovery**: 2026-06-07, designing `pkg/std/io`'s `io.EOF` (wanted a readonly managed-value global).
- **Root cause direction (needs investigation)**: (1) field-access lowering mis-bases / doesn't see through the `readonly` modifier on a managed pointer (the silent one — fix first); (2) method resolution needs a non-mutating-receiver path so a `readonly` receiver can call methods that don't mutate (cf. Rust `&self`, C++ const methods) — partly a language-design call (does Binate want const-correct receivers, or does `readonly` implicitly permit non-mutating method calls?).
- **Tests**: PINNED by `conformance/matrix/readonly` (Code-Red-2 Class B). After the Defect-1 fix: `field-read/{value-struct,managed-ptr,raw-ptr}` are GREEN on all backends; `pass-arg/value-struct` and `globals/readonly/struct` are green on LLVM (+aa64 for pass-arg) and stay xfailed only on VM / native-globals (Plan 2/3). `method/{iface,managed-struct}` (compile-error, all modes) remain xfailed red — that is Symptom 2 / plan-cr2-1 Defect 2 (check_method `resolveAliasAndConst`), still OPEN. `scalar/*` + `index/array` are green controls.

### SPEC ISSUE: does a named-DISTINCT type permit field access / method dispatch through its underlying type? (`type H @Box; h.v` rejected at the checker) — needs a decision
- **What surfaced it**: building the Defect-1 named-distinct companion test (plan-cr2-1). `type Handle @Box; var h Handle = cast(Handle, p); h.v` is rejected by the *type checker* with `cannot access field on this type` — and likewise `type NamedBox Box; nb.v` (named-distinct over a struct value). This is NOT the Defect-1 IR-gen literal-0 bug (that was `readonly`/alias and is fixed `27c1ee8b`); the named-distinct case never reaches IR-gen because the checker rejects field access first.
- **The question**: should a named-distinct type (`type X <underlying>`) inherit field access / non-mutating method dispatch from its underlying type? Reference points (verified empirically, go1.26.3): Go ALLOWS field access through a named-distinct type whether the underlying is a struct VALUE (`type B A` → `b.X` reads/writes) OR a POINTER-to-struct (`type P *A` → `p.X` works via auto-deref); only the underlying's METHODS are not inherited (call them via an explicit conversion, e.g. `A(b).M()`). (An earlier note here claimed Go disallows field access through a named pointer type — that is wrong.) Today Binate rejects field access through named-distinct in BOTH cases. **Decision (plan-language-spec.md D5, 2026-06-08):** stay RESTRICTIVE in v1 (reject — the forward-compatible direction, since opening up later breaks no code while tightening later would), with the documented target being Go's rule (allow field access, incl. auto-deref for a pointer underlying; never auto-inherit methods).
- **Where**: the field-access type-checker (`pkg/binate/types`, the selector/`check_selector` path) — it peels `readonly`/alias (those field reads type-check) but not named-distinct. Whatever the decision, it is a deliberate language-semantics change and must be ratified before implementing (do NOT silently make the checker peel named-distinct).
- **Scope**: a separate decision from Defect 1; IR-gen is already wrapper-transparent for named (peelTransparent peels `TYP_NAMED`), so if the checker is later opened up, the lowering is ready. Also relevant to whether a named-distinct *managed pointer* variable is refcounted correctly (isManagedPtrType now peels named, so a `Handle` var IS RefDec'd — that part is handled).
- **Discovery**: 2026-06-08, plan-cr2-1 Defect 1 companion-test reconnaissance.

### A relational op with an untyped int literal on the LEFT and a signed int on the right uses an UNSIGNED comparison — silent wrong result, ALL backends — FIXED 2026-06-06 (binate `b54c9fdf`)
- **Fix**: `gen_binary.bn` (`genBinary`) now stamps the resolved concrete type
  onto an untyped-int operand after `widenType`+`ensureWidth`.  `widenType`
  already resolves an untyped operand to the other's concrete type, but
  `ensureWidth` returns it unchanged at equal width, leaving it
  `TYP_UNTYPED_INT` (Signed=false) — so every backend's relational lowering read
  it as unsigned.  Stamping the concrete type fixes signed/unsigned selection on
  all backends at once (and makes div/rem/shift with an untyped-literal operand
  use the resolved signedness consistently).  Pinned by
  `conformance/regressions/cmp-literal-left-signedness` (operand order ×
  relational × signedness × width) across LLVM/VM/gen2/native; full builder-comp
  conformance 1069/0.  `math.Pow` reverted to Go's faithful `4096 < xe`
  (binate `f7d6446b`).  The systematic home for this class is the scalar
  matrix's named-but-unbuilt "comparisons" axis (plan-differential-testing.md v2).
- **Symptom (was)**: `5 < xe` where `var xe int = -1` evaluated to **true** (`5 < -1` is
  false).  An untyped integer literal on the LEFT of `<` / `<=` / `>` / `>=`,
  compared against a SIGNED `int` variable, emits an unsigned compare — so a
  negative signed value is read as a huge unsigned one.  Silent: no error, wrong
  control flow / result.
- **Scope confirmed by probing** (builder-comp / LLVM, builder-comp-int / VM, and
  native-aa64 — so it is a shared IR/type-checker bug, not a backend):
  - `literal < signedVar` (literal LEFT): UNSIGNED → BUG (`0 < -1`, `5 < -1`,
    `4096 < -1` all wrongly true).
  - `signedVar < literal` (literal RIGHT): signed → CORRECT.
  - `cast(int, literal) < signedVar` (typed literal LEFT): signed → CORRECT.
  - `var < var` (both `int`): signed → CORRECT.
  So the defect is operand-order-dependent: an untyped-literal LEFT operand drives
  the comparison signedness to unsigned.
- **Discovery**: 2026-06-06, porting `math.Pow` — Go's `1<<12 < xe` overshoot
  guard (`Othreshold`/exponent check) reads `4096 < xe` for a negative `xe`,
  making `Pow(0.5, 2)` return a wrong value instead of `0.25`.
- **Severity**: CRITICAL — silent wrong comparison result for a fundamental
  operation; any `literal < signedVar` (or `<=`/`>`/`>=`) in the codebase is
  miscompiled.  Most existing code writes `var OP literal` (literal on the right),
  which is why it went unnoticed.
- **Likely root cause (needs confirming)**: the relational lowering picks
  signed-vs-unsigned from the LEFT operand's type; an untyped int literal defaults
  to (or is treated as) unsigned, so the whole compare goes unsigned even though
  the other operand is a signed `int`.  The fix is in the type-checker / IR: when
  one operand is untyped and the other a typed integer, the untyped operand must
  take the typed operand's type (incl. signedness), and the compare's signedness
  must come from the unified type regardless of operand order.
- **Test (TODO when fixing)**: `conformance/matrix/scalar` (or a regression) — a
  comparison cell with the literal on the LEFT against a negative signed var, all
  four relationals, all signed widths; this is the "comparisons — signed vs
  unsigned at width boundaries" axis already named in `plan-differential-testing.md`
  (v2).  xfail until fixed.

### ~~Compound assignment (`+=`, `-=`, …) to a non-IDENT lvalue silently drops the operator~~ — FIXED+LANDED (binate `45b9e767`, 2026-06-06) (`compound-assign-nonident`)
- **Symptom**: `a[i] += x`, `s[i] += x`, `a[i][j] += x`, `p.field += x`, and `*p += x` all store the BARE RHS (`x`), discarding the operator and the old value — a silent miscompile (no error, wrong result). Only the plain-variable form `v += x` is correct. Repro (each prints `5`, should print `15`):
  ```
  func main() { var a [3]int; a[1] = 10; a[1] += 5; println(a[1]) }          // array elem
  func main() { var a @[]int = make_slice(int,3); a[1]=10; a[1]+=5; println(a[1]) } // slice elem
  type P struct { x int }; func main() { var p P; p.x = 10; p.x += 5; println(p.x) } // field
  func main() { var v int = 10; var p *int = &v; *p += 5; println(v) }        // deref
  ```
- **Root cause**: `genAssign` (gen_control.bn) applies the compound op (`cur = load; rhs = cur OP rhs`, incl. the `/=` `%=` div-check guard) ONLY in the IDENT arm. The EXPR_INSTANTIATE_OR_INDEX (array/slice), EXPR_SELECTOR, and `*p` deref arms ignore `stmt.Op` and store `rhs` directly. Pre-existing; unnoticed because the whole codebase writes these longhand (`x.f = x.f + 1`) — 0 occurrences of compound-assign-to-lvalue in non-test source. Found during M7/M8 coverage review.
- **Fix (landed)**: the compound step (load current lvalue → `cur OP rhs` with the `/=` `%=` div-check guard) is factored into `emitCompoundBinop` + `isCompoundAssign`; every lvalue arm (IDENT, array, slice, pointer, struct-field, deref, nested-array) runs it before its store — a slot load through the elem/field/deref pointer, or EmitSliceGet for a slice element. **Test**: conformance 640 (variable, array elem, slice elem, nested array, field, deref; `+= -= *= /=`), green on LLVM + VM.

### ~~`~` (bitwise complement) IR-gen hardcodes the result type to `int` — invalid IR for sub-word, wrong-signed shift on uint64~~ — FIXED + LANDED (binate `42ad4fa0`, 2026-06-06) (`bitnot-result-type`)
- **FIXED**: `gen_expr.bn:247` now types `OP_BITNOT` as the operand's type
  (nil-fallback to `int`), mirroring `OP_NEG`. All `bitwise/not` cells pass on
  LLVM (123/123); unit tests `TestGenBitnotOn{Uint16PreservesWidth,
  Uint64IsUnsigned}` added. NOTE: the *native* backends keep a separate
  sub-word `~` gap — aa64's `Mvn` / x64's `not` ignore the operand width (part
  of `aa64-subword`); not addressed by this IR-gen fix.
- **Symptom (two facets, one root)**:
  - **A (invalid IR)**: `~x` for any sub-word int (`uint/int 8/16/32`) emits
    `xor i64 %x, -1` with a hardcoded i64 — clang rejects it
    (`'%x' defined with type 'i8' but expected 'i64'`). `~` simply does not
    compile for sub-word ints on the LLVM backend.
  - **B (wrong value)**: `(~v) >> k` consumed DIRECTLY (no intervening store)
    on `uint64` does an ARITHMETIC shift, not logical: `(~0) >> 32` is
    `2^64-1`, not the spec `2^32-1`. Storing `~v` into a `uint64` var first
    masks it (the store re-types to unsigned), and `(a+b) >> k` for unsigned is
    fine — so it is specific to `~`-results.
- **Root cause (CONFIRMED)**: `pkg/binate/ir/gen_expr.bn:247` lowers `~` as
  `b.EmitUnary(OP_BITNOT, arg, types.TypInt())` — the result type is hardcoded
  to `int` (signed, target-width i64) instead of the OPERAND's type. So the
  BITNOT instr is mis-typed: i64 width (→ facet A, mismatched `xor` width for a
  sub-word arg) and signed (→ facet B, a directly-consumed `>>` lowers to
  `ashr` not `lshr` per `emit_ops.bn:48-52`, which keys on `instr.Typ.Signed`).
  This is the SHARED IR layer, so it likely affects the VM/native backends too
  (facet B at least; the full `all` sweep is pending this decision).
- **Test**: `conformance/matrix/scalar-diff/bitwise/not/*` — 7 cells fail on
  `builder-comp` (the sub-word ones COMPILE_ERROR; `64/unsigned` value-diverges;
  `64/signed` passes — i64 + signed happen to match the hardcoded type).
- **Discovery**: 2026-06-06, differential-harness v2 (bitwise cells).
- **Fix**: type the `OP_BITNOT` result as the operand's type, mirroring the
  adjacent `OP_NEG` path's `negTyp` derivation (`gen_expr.bn:223-241`) — for
  `~`, the result type is always exactly the operand type (no widening). A
  one-site fix resolving both facets.

### Whole-array (aggregate) `=` assignment is silently dropped — FIXED 2026-06-06 (binate, gen_control.bn)
- **Fix**: the ident and deref assignment arms in `gen_control.bn` now load the
  aggregate value out of an `OP_ALLOC` RHS (`isStructOrArrayAlloc(rhs)` →
  `EmitLoad`) before the store, matching the selector arm (which already did).
  Whole-array/struct `=` from a composite literal or another variable, and
  `*p = {...}`, now copy the value.  This *also* fixes GLOBAL array/struct
  initializers (they route through `__init`'s `x = expr`).  Pinned by
  `conformance/regressions/whole-aggregate-assign` + `global-aggregate-init`
  (LLVM/VM/gen2/native); full builder-comp conformance 882/0, no regression.
- **Confirmed root cause**: `emitStoreManagedSlot`'s non-managed path does a plain
  `EmitStore(slotPtr, val)`; the ident/deref arms passed `val` = the RHS `OP_ALLOC`
  *pointer* (a composite literal lowers to a stack alloca), so the pointer bits
  were stored into the aggregate slot instead of the contents. The selector and
  (struct-only) index arms already loaded first; ident/deref did not.
- **Symptom (was)**: `a = [4]int{10,20,30,40}` (a whole-array assignment via `=`,
  RHS a composite literal) did NOT update `a` — it stayed at its prior value. The
  store was silently a no-op; no error, no diagnostic.
- **Discovery**: 2026-06-06, porting `math.Pow10` (which wants package-level
  `var pow10tab [32]float64 = {...}` lookup tables). Minimal repro in a unit test:
  `var a [4]int = [4]int{0,0,0,0}; a = [4]int{10,20,30,40}; a[0]` reads `0`.
- **Scope confirmed by probing (builder-comp / LLVM gen1)**:
  - LOCAL array *decl-init* (`var a [N]T = [N]T{...}`): WORKS (int + float).
  - Whole-array `=` *assignment* (`a = [N]T{...}`): BROKEN (no-op) — the LHS keeps
    its old value. This is the underlying defect.
  - GLOBAL array initializer (`var arr [N]T = {...}` at package scope): BROKEN
    (reads as all-zero) — because the synthetic per-package `__init` (gen_init.bn)
    lowers each `var x = expr` into the assignment `x = expr`, and whole-array
    assignment is the dropped op. (GLOBAL *scalar* int init via `__init` WORKS,
    confirming `__init` itself runs in the unit-test harness.)
- **Likely root cause (needs confirming)**: IR-gen for `STMT_ASSIGN` with an
  aggregate (array, and probably struct) LHS/RHS doesn't emit an element-wise copy
  / memcpy — only scalar assignments store. The decl-init path (genLocalVarDecl)
  emits the element stores, which is why decl-init works but `=` doesn't.
- **Severity**: CRITICAL — silent data loss on a routine operation (`arr = other`,
  `arr = {...}`, and therefore *all* global array/struct initializers). Any program
  relying on a package-level table reads zeros with no warning.
- **Impact / blocks**: `math.Pow10` (table-based) is blocked; any global aggregate
  table or `arr = arr2` copy is unsafe until fixed.
- **Test (TODO when fixing)**: conformance cell for whole-array `=` assignment and
  global array-initializer readback (LLVM/VM/native/gen2), xfailed until the fix.

### Global float `var` emits invalid LLVM (`global double 0`) — FIXED 2026-06-06 (binate, emit.bn)
- **Fix**: `emit.bn`'s global-var static-zero emission now emits ` 0.0` when
  `g.Typ.IsFloat()` (else ` 0` for integers).  The runtime initializer value
  still flows through `__init`, so `var x float64 = 7.5` both compiles and reads
  back 7.5.  Pinned by `conformance/regressions/global-aggregate-init`.
- **Symptom (was)**: any package-level `var x float64` (with or without an initializer)
  makes the LLVM backend emit `@<mangled> = global double 0`, which clang rejects:
  `error: integer constant must have integer type` — the whole package fails to
  compile. (`var x float64 = 7.5` fails identically; the initializer is irrelevant
  because the static zero is what's malformed.)
- **Root cause**: `pkg/binate/codegen/emit.bn` global-var emission (~line 156-170)
  picks the static zero by type kind: `null` for pointers, `zeroinitializer` for
  slice/struct/array, and a bare ` 0` for *everything else* — but ` 0` is only
  valid for integer LLVM types. For `double`/`float` it must be ` 0.0` (or
  `0.000000e+00`). The runtime value (for `= expr`) comes from `__init`, which
  works for scalars — so emitting the correct float zero fully fixes scalar float
  globals.
- **Severity**: MAJOR — hard compile error (not silent), blocks any global float
  var. Discovered 2026-06-06 alongside the array-assignment bug, porting `Pow10`.
- **Proposed fix**: in the global-var zero-emission, branch on float type kinds
  (TYP_FLOAT64/TYP_FLOAT32) to emit ` 0.0`; keep ` 0` for integers. One-line-ish.
- **Test (TODO when fixing)**: codegen unit test asserting a `double`/`float`
  global emits a float zero, plus a conformance cell reading back a global float.

### Global `var` of an interface-value / func-value (or readonly-wrapped aggregate) type emits invalid LLVM (`global %BnIfaceValue 0`) — ✅ RESOLVED — LANDED `91ef4fc4` (verified on main 2026-06-10)
- **Symptom**: any package-level `var x @Iface` / `@errors.Error` / `*func()` / `@func` (with or without an initializer), AND any `readonly`-qualified aggregate/iface/func/struct/array/slice global, made the LLVM backend emit `@<mangled> = global %BnIfaceValue 0` (or `%BnFuncValue 0`, `%bn_main__Pt 0`, …), which clang rejects: `error: integer constant must have integer type` — the whole package fails to compile. Blocked a `pkg/std/io` `var EOF @errors.Error = errors.New("EOF")` sentinel (and any iface/func-value package global).
- **Root cause**: `pkg/binate/codegen/emit.bn` global-var static-zero dispatch — the SAME dispatch as the float-global sibling above — picks the zero by type kind (`null` ptr, `zeroinitializer` slice/struct/array, ` 0.0` float, ` 0` otherwise). Two gaps: (1) the 16-byte address-aggregate kinds (`TYP_INTERFACE_VALUE[_MANAGED]` → `%BnIfaceValue`; `TYP_[MANAGED_]FUNC_VALUE` → `%BnFuncValue`) fell through to ` 0` but are LLVM struct types needing `zeroinitializer`; (2) the dispatch tested `g.Typ.Kind` DIRECTLY while `llvmType`/`IsFloat` unwrap `TYP_READONLY` first, so a `readonly`-wrapped aggregate global got the right printed type but the wrong ` 0` init token. Same code-red "missing iface/func-value arm" + "aggregate-as-scalar" shape, in the global emitter.
- **Fix — LANDED `91ef4fc4` (the orphaned worktree commit `5dddef7d` on `temp-binate-4` was superseded by this functionally-identical landed commit; `f2ebaca1` later extended the dispatch to also peel `TYP_NAMED`)**: add the four address-aggregate kinds to the `zeroinitializer` branch AND unwrap `TYP_READONLY` at the top of the dispatch (mirroring `llvmType`). Verified on main 2026-06-10: `--emit-llvm` emits `@bn_main__g = global %BnIfaceValue zeroinitializer`, `@bn_main__f = global %BnFuncValue zeroinitializer`, `@bn_main__ro = global %bn_main__Big zeroinitializer` (valid, not the invalid ` 0`); full clang compile exit 0. Adversarially reviewed (4-agent workflow): correctness + refcount confirmed (the `__init` store MOVES the fresh value in via consumeTemp; the zeroinitializer prior-occupant RefDec is a verified null-data no-op; immortal sentinel by design, like Go's io.EOF); the readonly variant was the review's blocker finding; no regression to int/bool/char/ptr/float/struct globals. Unit test `pkg/binate/codegen/emit_global_test.bn` (func/iface/readonly → zeroinitializer, not ` 0`; + the float sibling). End-to-end: cross-package `var EOF @errors.Error = errors.New("EOF")` compiles, `__init` runs it, consumer reads it + `.Error()` correct; 1000-iter stress clean.
- **Severity**: MAJOR — hard compile error (not silent), blocked any package-level interface-value / func-value (or readonly-aggregate) global. Discovered 2026-06-07 implementing `pkg/std/io`'s `io.EOF`.
- **Test-gap analysis (the "why wasn't this caught / how to prevent" ask) + FOLLOW-UP**: the defect lived in a structurally-EMPTY matrix intersection — `conformance/matrix/aggregate/global` sweeps the `global` op over {scalar,array,struct}×{int,float} but NOT iface/func kinds; `conformance/matrix/addr-aggregate/{func-value,iface-value}` sweeps those kinds over {direct,copy,return,arg,return-arg,field,array-elem} but has NO `global` op. Neither product's coordinates included "a package-level global of a 2-word address-aggregate", and there was ZERO codegen unit coverage of the module-global path. PREVENTIVE FOLLOW-UP (deferred per the user): add a `global` operation to `conformance/gen-addr-aggregate-matrix.py` (OPERATIONS) → `addr-aggregate/{func-value,iface-value}/global.bn` + a no-initializer companion (sweeping the with/without-runtime-initializer axis), update its README, run hygiene. ALSO unverified: VM (`-int`) + native modes — the VM materializes globals separately (`vm/lower_data.bn`); confirm it handles iface/func-value globals before relying on `io.EOF` in `-int`/native (xfail per mode if not). The unit test is mode-independent and already guards the codegen fix.

### Package-level global of a NAMED type miscompiles — named scalar emitted `global i64 0`, named-over-aggregate emitted an invalid zero token — FIXED (binate `b43a0057` IR-gen + `f2ebaca1` codegen, plan-cr2-2 Defect 1)
- **Fix (two layers, both landed)**: (1) IR-gen now registers a named-distinct non-struct type as a `TYP_NAMED` alias (binate `b43a0057`, the named-distinct-scalar work), so `resolveTypeExpr` returns the real `TYP_NAMED` (carrying `.Underlying`) instead of the old `TypInt()` fallback — named-scalar/float globals get `double 0.0` / `float` / `iN`, and named-over-aggregate globals reach codegen as `TYP_NAMED`. (2) The `emit.bn` global static-zero token dispatch now peels `TYP_NAMED` as well as `TYP_READONLY` (via the new `stripWrappers` helper, binate `f2ebaca1`), so a named-over-aggregate global emits `zeroinitializer` / `null` instead of the invalid bare ` 0`. Pinned by `emit_global_test.bn` (TestEmitGlobalNamed{IfaceValue,FuncValue,ManagedSlice,ManagedPtr}ZeroInit) + the four `conformance/matrix/globals/noinit/named-{iface,func,managed-ptr,managed-slice}` cells (now green on the LLVM modes; xfails removed). Verified by reverting `f2ebaca1` (cells red) and re-applying (green on gen1+gen2).
- **Symptom (was)**: `type Celsius float64; var C Celsius = 3.5` emitted `@C = global i64 0` (should be `double 0.0`); a named-over-address-aggregate (`type MyErr @errors.Error; var X MyErr`) emitted `@X = global %BnIfaceValue 0` — an invalid LLVM token clang rejects (`integer constant must have integer type`).
- **Note on the prior root-cause text (now corrected)**: an earlier version blamed `resolveTypeExpr`'s `gen_util.bn:294` `TypInt()` fallback as still-live; that was made stale by `b43a0057`, which registers the `TYP_NAMED` alias so the fallback is no longer reached for these. The remaining live gap was purely the `emit.bn` token peel, fixed by `f2ebaca1`.
- **Severity**: MAJOR (was an invalid-LLVM hard failure for named-over-aggregate; latent wrong-type/width for named-scalar). Discovered 2026-06-07 by the adversarial review of the global-init fix.

### Cross-module global of struct type emits `external global %Struct` without declaring the type — FIXED 2026-06-08 (binate `b0402d04`, plan-cr2-2 Defect 2)
- **Fix**: `collectStructTypes` (`pkg/binate/codegen/emit_types.bn`) now scans
  `m.Globals` after `m.Funcs`, so a struct reachable only through a package-level
  global (the defining `global` or a consuming `external global` — both record
  `g.Typ`) is discovered and its `%bn_<pkg>__Struct = type {...}` def emitted
  before the global that references it. `discoverStructFromType` also gained
  `TYP_NAMED`→`.Underlying` and `TYP_ARRAY`→`.Elem` recursion arms (a named-over-
  struct or `[N]Struct` global's struct was missed even via a function). Purely
  additive (discovery can only find more structs; `addStructDef` dedups). Pinned
  by `emit_types_test.bn` (TestStructTypeDiscoveredViaGlobal / ...ArrayOfStruct...
  / ...NamedStruct...) and `conformance/657_cross_pkg_struct_global`.
- **Symptom (was)**: a module that references another package's package-level `var
  g StructType` emitted `@<mangled> = external global %bn_<pkg>__Struct` but never
  `%bn_<pkg>__Struct = type {...}` in that module → clang "use of undefined type".
  Confirmed to bite a normal cross-package import (not just the test harness); also
  the defining package of a zero-init struct global (no function references the
  struct).
- **Discovery**: 2026-06-06; root cause was `collectStructTypes` scanning only
  `m.Funcs`. **Severity**: MAJOR (was a hard compile failure).

### Integer shift by a count >= bit width is hardware-masked (mod width), NOT the spec's defined 0 / sign-extend — FIXED 2026-06-06 (binate `32fde83d`)
- **Fix**: a branchless overshift guard in IR-gen (`gen_binary.bn`,
  `emitGuardedShift`), so a non-constant (or out-of-range constant) shift count
  yields 0 (logical) / sign-fill (arithmetic `>>`) per the spec, on every
  backend with no per-backend logic. An in-range constant count stays a plain
  shift (the common case is unchanged). `math.RoundToEven`'s temporary IsInf/
  IsNaN workaround was removed. Pinned by `conformance/631_shift_overshift`
  (LLVM/VM/native-aa64/gen2) + IR-gen unit tests; full builder-comp 854/0.
- **Symptom (was)**: a shift whose count is >= the operand's bit width returns a
  hardware-masked result instead of the documented value. Confirmed (LLVM, both
  const-folded and runtime counts): `full >> 64 == full` and `1 << 64 == 1`
  (both should be `0`); `full >> 70 == full >> 6` (count masked to `70 mod 64`).
  The native backends (aarch64 `LSL`/`LSR`, x64 `SHL`/`SHR` mask the count to 5/6
  bits) and the VM (host shift) almost certainly do the same — needs confirming
  per backend.
- **Spec violated**: `claude-notes.md` Operators — "Shift by >= bit width:
  defined behavior (zero for `<<` and logical `>>`, sign-extended for arithmetic
  `>>`)". Matches Go (which guarantees shift-away-to-0). The implementation does
  C/hardware masking instead.
- **Impact**: any shift by a *runtime* count that can reach/exceed the width is
  silently wrong. Breaks ported code that assumes Go's shift semantics — e.g.
  `math.RoundToEven` (its `e >= bias` branch shifts by huge counts for ±Inf/NaN
  and relies on `>> n == 0`; worked around with an IsInf/IsNaN guard, removable
  once this is fixed), and likely upcoming fdlibm ports. Discovered 2026-06-06
  porting `math.RoundToEven` (the ±Inf/NaN case produced a non-NaN).
- **Root cause**: codegen emits the raw hardware shift. LLVM `shl`/`lshr`/`ashr`
  by >= width is poison, lowered to a masking hardware shift; the native shifts
  mask the count register directly.
- **Test**: `conformance/matrix/scalar/{shl,shr}-overshift/<width>/<sign>` (16
  cells, binate `6fdb56eb`) — count == width, runtime `var` count (exercises the
  backend shift, not const-fold). CONFIRMED wrong on **every** backend (LLVM, VM,
  both natives); xfailed all modes — **un-xfail when the fix lands**. (Closes the
  scalar matrix's value-axis gap: shifts were only tested as in-range consumers.)
- **Fix (in progress, honor the spec)**: make codegen guard each variable-count
  shift so a count >= width yields 0 (logical `<<` / `>>`) or sign-fill
  (arithmetic `>>`), on every backend + the VM. The alternative — changing the
  spec to hardware-masked / UB-on-overshift (cheaper, matches C/hardware) — was
  considered and rejected in favour of keeping the documented Go-style guarantee.

### Managed struct `@func` fields: stale `ctx.CurBlock` after a block split → malformed IR — FIXED + LANDED 2026-06-06 (binate `47d05c81`)
- **Symptom**: a managed struct holding `@func` fields crashes — compiled SIGTRAPs
  (rc 133, no output), interpreted aborts `vm: func_value_dtor on nil fv address`
  (the `fvAddr == 0` "IR-gen bug — fatal" branch in `vm_exec_iface.bn`). NOTE: this
  is NOT the destructor walking a wrong field offset (the original guess, now
  disproven) — it is malformed IR produced during *construction*.
- **Root cause (confirmed)**: `genExprOrFuncRef` (`pkg/binate/ir/gen_util.bn`) had a
  function-reference early-return that emitted into block `b` and returned WITHOUT
  `ctx.CurBlock = b` — unlike every other return path in that function (the typed-int
  returns and `genExpr`'s pre-amble all sync it; the function's own comment documents
  why). Assigning a function reference to an `@func` field emits an old-value RefDec
  whose null-guard SPLITS the block; the split leaves `ctx.CurBlock` pointing at the
  now-terminated block, and the next statement's `b = ctx.CurBlock` reverts `b` to it.
  So two consecutive func-ref `@func` assignments emit statement 2 into the already-
  terminated block → two terminators + an orphaned `unreachable` continuation, i.e.
  malformed IR. It is built before backend selection, so BOTH native and the VM crash.
  Raw `*func` has no managed dtor → no split → no desync, which is why `*func` is clean.
- **Minimal repro (cross-package was incidental — the real discriminator is func-ref
  vs param RHS)**: single package, two function-reference assignments to `@func` fields
  in sequence — `io.W = sinkW; io.E = sinkE` → malformed `newIO` (two `br` in `entry.0`;
  `fv_refdec_cont.2` → `unreachable`; rc 133 / vm-fatal). The param form `io.W = w;
  io.E = e` is well-formed (params route through `genExpr`, which syncs). Verified:
  old bnc rc 133 / fixed bnc rc 0; param control rc 0 both. Every prior single-package
  minimization used params or a single assignment, which dodged it.
- **Discovery**: 2026-06-06, building minbasic's M3 embeddable REPL; basicSession's
  duplicated `@func` `ReplIO` crashed `cmd/basic`. minbasic's `newIO` / session setup
  assigns function references, which is what tripped it.
- **Fix**: add `ctx.CurBlock = b` before the func-ref `return fv` in `genExprOrFuncRef`.
  Covered by `conformance/634_funcref_managed_field_seq` (basicSession-shaped: inline
  `@func`-bearing struct field + sibling `@func`, all assigned from function
  references; prints `1 2 1 2 7 42`, crashed rc 133 / vm-fatal before the fix).
  Landed binate `47d05c81` (fix + test).
- **Sibling instance (found by adversarial review, also FIXED + LANDED)**: the same
  `ctx.CurBlock`-desync class was live in `genMultiAssign`'s SELECTOR arm
  (`gen_assign_multi.bn`) — a multi-assign whose earlier target is a managed
  `@func`/`@Iface` IDENT (block-splitting old-value RefDec) and a later target is a
  selector silently DROPPED the selector store and every statement after the
  multi-assign (`f, h.n = twoFI()` printed nothing pre-fix; `11`/`5` after). Root
  cause: `genSelectorPtr` (unlike `genExpr`) does not sync `ctx.CurBlock`, so the
  arm's `b = ctx.CurBlock` reverted to the stale block. Fixed by re-syncing
  `ctx.CurBlock = b` per target. Landed binate `2f507f26` + `conformance/641`.
- **Follow-up (broader gap) — DONE + LANDED 2026-06-06**: this whole class — a
  `ctx.CurBlock` desync in *any* codegen path after a block split — is invisible to
  output/refcount conformance tests (they only see the end result, if the program
  survives at all). A structural IR verifier now catches it at the source:
  `VerifyFunc`/`VerifyModule` (binate `c899e33b`, `pkg/binate/ir/verify.bn`) check
  per-block single-terminator-last + valid successors (the exact malformed shapes the
  desync produces); wired into `genFunc` behind `SetVerifyIR` (off by default; binate
  `4e78e28d`). Designed + adversarially critiqued (the critique excluded reachability
  — IR-gen legitimately leaves benign orphaned `switch.exit`/`if.merge` blocks when
  all arms return — and SSA dominance, as false-positive-prone / redundant for this
  class). Shadow-validated with the assertion forced on over the whole conformance
  corpus + gen2 self-compile in all three modes (1069/0, 1039/0, 1069/0): zero false
  positives. On its first run it caught a real pre-existing bug — `panic(...)` emitted
  a dead `OP_CONST_NIL` into the block `EmitPanic` had terminated, so the finalizer
  added a redundant `unreachable` (a two-terminator block on every panic-terminated
  func); fixed in binate `b03d1f07` (return a detached const-nil). **Enabled in CI**:
  `cmd/bnc --verify-ir` (binate `b4312c0e`) flips `SetVerifyIR(true)`; the
  `e2e/verify-ir.sh` test (binate `ff42d9ec`) builds gen1, then compiles the whole
  toolchain — `cmd/{bnc,bni,bnas,bnlint}` + full dep closure (≈ the entire codebase,
  incl. the compiler's own self-compiled IR) — with `--verify-ir`, so a malformed-IR
  regression fails CI at IR-gen.  (An earlier conformance `verify-ir` job, `64fb2c19`,
  covered only test-program IR via a redundant full-suite re-run and was dropped in
  favor of the e2e test, `e6fdb3f8`.)  Remaining (optional): add reachability (needs
  IR-gen to prune benign orphans first) / SSA-dominance to the verifier itself.

### ~~Compiled program leaks native stack per loop iteration for a default-init managed local~~ — FIXED + LANDED 2026-06-06 (binate `2411295c`)
- **Was**: a *compiled* program declaring a default-init managed local
  (`var m @[]char`) inside a loop body SIGSEGV'd once the loop ran enough
  iterations (~130k at an 8 MiB stack; threshold scaled linearly with
  `ulimit -s`, RSS flat — a native-stack leak, ~32 B/iter). The VM ran it fine.
- **Attribution correction**: this was the **LLVM codegen** (the `comp` /
  compiled modes), NOT the native-aa64 backend the old title named. The native
  aa64/x64 backends use a fixed frame (PlanFrame) and don't leak; the VM doesn't
  touch the native C stack. "native stack" = the C stack of the *LLVM-compiled*
  binary. (Verified: `var m @[]char` in a 3 M-iter loop completes on
  `--backend native`, crashes via `comp`.)
- **Root cause**: codegen hoists every alloca to the function entry block (an
  alloca in a non-entry block isn't freed until return, so a loop body alloca
  leaks per iteration), but the hoist pre-pass was missing three alloca-emitting
  ops, leaving their allocas in the loop body:
  - `OP_CONST_NIL` — the `.a` zero-fill slot of a default-init managed aggregate
    (the reported case).
  - `OP_RODATA_ARRAY` — the `.tmp` `[N x i8]` slot of `var a [N]char = "..."`.
  - `OP_BOX` — the `.tmp` spill slot of `box(<scalar register>)`.
  The latter two were **found by the new static checker** below, not the
  original repro.
- **Fix**: each op now splits its alloca into a hoistable decl emitter (run by
  the entry-block pre-pass) plus the in-place fill/store/load, matching
  OP_ALLOC. The pre-pass dispatch lives in `pkg/binate/codegen/emit_alloca_hoist.bn`.
  This also resolves the compiled-minbasic `runProgramInto` `var errMsg @[]char`
  crash without the doc's suggested side-step.
- **Detection (3 legs)** — the "detect this class in general" ask:
  - `conformance/check-alloca-hoist.py` + `scripts/check-alloca-hoist.sh` — a
    static checker asserting every alloca lives in its function's entry block,
    swept over the corpus (734 cells, 0 violations post-fix; it found the
    rodata-array + box siblings). The construct-agnostic, compile-time detector.
  - `conformance/gen-loop-leak-matrix.py` → `matrix/loop-leak/` — runtime cells
    that loop a construct enough to overflow an 8 MiB stack if it leaks, then
    print 42 (leak-prone cells crash pre-fix, pass post-fix on LLVM/VM/native).
  - `pkg/binate/codegen/emit_alloca_hoist_test.bn` — unit tests asserting each
    construct's alloca precedes the loop body in the emitted IR.

### `box(<scalar>)` is unimplemented on the native backend — silent no-emit → garbage result (MINOR wrong-code) — ✅ RESOLVED (landed binate `6235e43a`, 2026-06-09; native AND VM — the "VM works" claim below was wrong, BC_BOX SIGSEGV'd too)
- **Symptom**: `box(i)` where the operand is a scalar register (not an OP_ALLOC
  or aggregate) compiles fine on the LLVM backend but the native backends'
  `emitBox` hits the `else { ... return }` scalar arm (aarch64_emit.bn /
  x64_managed.bn) and emits **nothing** — no `rt.Box` call — so the OP_BOX
  result is undefined; the managed pointer then carries garbage.
- **Discovery**: 2026-06-06, building the loop-leak matrix (a `box(i)`-in-a-loop
  cell crashed on native while the LLVM build leaked-then-was-fixed). Not a leak.
- **Scope**: native aa64 + x64 only; LLVM/VM compile+run `box(scalar)` correctly.
  `box(struct-literal)` (OP_ALLOC source) and `box(iface-value)` (aggregate
  source) ARE handled — only the bare-scalar source is dropped.
- **Fix**: emit the scalar-source spill + `rt.Box` call in the native `emitBox`
  else arm (store the scalar into a frame slot, pass its address), OR reject
  `box(scalar)` in the checker if it isn't meant to be supported. **Test**: a
  conformance cell `box(i)` returning the boxed value; currently no coverage.

### Plan-1 adversarial review (2026-06-06) — regressions + completeness gaps from the const/slice fixes — ✅ ALL FIXED+LANDED except ONE REPL-only leftover (parked-member iota-repeat — see "Minor follow-ups" below; tracked in plan-cr2-followup.md Plan B)

The Plan-1 fixes (binate 1.1-1.6, landed 2026-06-05) were adversarially
reviewed. Real defects found, several wrong-code on main. Listed worst-first.
Repros marked (verified) were reproduced directly; (reviewer) were proven by a
review subagent via --emit-llvm / gen1. Each needs an xfail test added (Bug
Discovery Protocol) — most don't have one yet.

#### C1 — inc/dec on a local const mutates it — ✅ FIXED+LANDED (binate `2e8fbb33`, 2026-06-06)
- **Symptom**: `func main(){ const C int = 5; C++; println(C) }` prints **6** (verified). Pre-fix C++ was a silent no-op (const not in ctx.Vars → lookupVar nil); local-const materialization (binate 273d7e4a) put the slot in ctx.Vars, and the checker's STMT_INC_DEC arm (check_stmt.bn ~39-45) only checks IsInteger(), never const-ness, so genIncDec now load/add/store-s into the const slot.
- **Root cause**: checker STMT_INC_DEC doesn't reject a SYM_CONST target (assign / compound-assign / &C ARE rejected; only ++/-- slip through).
- **Fix**: reject ++/-- on a const in the checker. **Test**: conformance .error or a checker unit test (expectError), currently xfail/known-gap.

#### C2 — untyped non-int local const mistyped as int — ✅ FIXED+LANDED (binate `912718e6`, 2026-06-06)
- **Symptom**: `const C = 0.5; var y float32 = C` → high lane **24191** (garbage; verified); `const C = 0.5; var x float64 = C + 0.5` → invalid `add i64 …, double`, clang rejects. genDecl's no-TypeRef inference defaults typ=TypInt() (only special-cases EXPR_STRING_LIT), so an untyped float/bool/char local const gets an i64 slot and a `sitofp`/int op. The checker accepts it (untyped const stays assignable to float32), so it miscompiles silently. The var-init sibling `var C = 0.5` is checker-rejected for the float32 assign, so this divergence is specific to routing DECL_CONST through the int-defaulting path.
- **Root cause**: gen_stmt.bn genDecl untyped-inference covers only string literals; float/bool/char untyped local consts fall to TypInt default.
- **Fix**: infer the type from the initializer literal kind (float→float64, bool, char) for an untyped local const (mirror checker default-type), or reject untyped non-int local const. **Test**: conformance xfail (float32/float64 untyped local const).

#### C3 — local const as array dimension → IR-gen wrong size — ✅ FIXED+LANDED (binate `c97d7acc`, 2026-06-06)
- **Symptom**: `const N int = 3; var a [N]int; println(len(a))` → **30** (verified); package-scope const gives 3. Checker sees the local const via c.Scope.Lookup (correct length 3), but IR-gen resolveTypeExpr→evalConstExpr→lookupConst (gen.bn ~386) walks only moduleConsts (module scope) and falls back to parseIntLit("N")=garbage. Checker/IR-gen layout disagreement.
- **Root cause**: IR-gen has no function-local const table; lookupConst is module-only. (1.3a fixed array-dim for PACKAGE consts; locals were not covered.)
- **Fix**: give IR-gen access to local const values for resolveTypeExpr (a function-scoped const table), or restrict array dims to package consts at the checker. **Test**: conformance xfail (local const array dim).

#### C4 — &s[i] on a readonly-wrapped slice mis-strides — ✅ FIXED+LANDED (binate `f4769aac`, 2026-06-06)
- **Symptom**: `var s readonly @[]uint8 = "AB"; var p *uint8 = &s[1]; println(cast(int,*p))` → **0** (verified; expect 66). Dropping the TYP_STRUCT guard (binate 937ae78e) exposed it: for `readonly @[]uint8`, arrTyp.Kind==TYP_READONLY; isSliceType peels readonly (true) but arrTyp.Elem is then the INNER managed-slice, not uint8, so EmitSliceElemPtr GEPs with a ~32-byte stride. Pre-fix this crashed (guard failed → wild-pointer fall-through); now silently wrong.
- **Root cause**: genIndexPtr (gen_access.bn) uses arrTyp.Elem / collTyp.Elem without peeling TYP_READONLY.
- **Fix**: peel readonly (resolve to the underlying slice type) before reading .Elem in both slice arms. **Test**: conformance xfail (&readonly-slice[i]).

#### C5 — cross-package float const-EXPRESSION reads int 0 — ✅ FIXED+LANDED (binate `3dfc4b4a`, 2026-06-06)
- **Symptom**: a `.bni`-exported `const C float64 = 1.5 + 2.5`, read package-qualified, lowers to `add i64 0, 0` (reviewer). The CONST_EXPR family (binate 9ef5db58) was wired into gen_expr.bn's EXPR_IDENT read but NOT into gen_selector.bn's qualified read (no CONST_EXPR arm → falls to EmitConstInt(Val=0)), and the importer (gen_import.bn single + registerImportConstGroup) never registers a float const-expr at all.
- **Root cause**: const-folding fixes scoped to in-package producers/readers; the cross-package read (gen_selector) + import producers were not updated.
- **Fix**: add a CONST_EXPR arm to gen_selector read + route import producers through the shared classifiers (see M1/M4 — a unifying shared const-classifier is the real fix). **Test**: cross-pkg conformance xfail.

#### M1 — cross-package bool/float-comparison + bool-logic consts → silent int 0 — ✅ FIXED+LANDED (binate `3dfc4b4a`, 2026-06-06)
- **Symptom**: `.bni`-exported `const CMP bool = 1 < 2` / `(1<2)&&(3>2)` / `1.5 < 2.5` read cross-package lower to `add i64 0,0` → 0 (reviewer). gen_import single-const handles only EXPR_BOOL_LIT + float-literal; registerImportConstGroup calls only classifyConstLit; neither calls classifyConstBoolExpr/classifyConstFloatExpr.
- **Fix**: route both import producers (and gen_repl GenConstMember) through the same classifier chain genConst/genConstGroup use. **Test**: cross-pkg conformance xfail (bool-cmp, bool-logic, float-cmp).

#### M2 — composite-LITERAL element float32 store → memory corruption — ✅ FIXED+LANDED (binate `975db032`, 2026-06-06)
- **Symptom**: `var a [2]float32 = [2]float32{0.5, 0.5}` emits `store double %v, float* %slot` — an 8-byte store through a 4-byte slot (reviewer). The 1.1 coerceScalarWidth was wired into call-arg/field/return but NOT the three composite-literal element-store loops (genArrayLit, genManagedSliceLit, genRawSliceLit). Worse than the contained-field case (clobbers adjacent memory).
- **Fix**: call coerceScalarWidth before the element store in all three composite-literal loops. **Test**: conformance xfail (array/mslice/rawslice float32 literal).

#### M3 — const array dim in a struct field → spurious type-check rejection — ✅ FIXED+LANDED (binate `a56943c8`, 2026-06-06)
- **Symptom**: `const N int = 3; type S struct { arr [N]int }; … s.arr passed to a [3]int param` is REJECTED `cannot assign [..] to [..]` (reviewer). Struct types resolve once in pass 1 (collectTypeDecl), where no const has HasConstVal yet, so evalConstInt's leniency returns 0 and [0]int sticks on Field.Type; the var path re-resolves in pass 2, struct fields don't. Codegen is fine (resolves independently) → false-positive rejection, not a miscompile.
- **Fix**: collectDecls now folds the const's integer value (defineConstVal) at pass-1 forward-registration when evalConstIntValue can fold it — so a struct field's array dim resolving in the same pass sees the value. evalConstIntValue doesn't checkExpr, so non-literal / forward initializers fold to nothing and the name still resolves value-less (unchanged forward-ref behavior). **Test**: `TestConstArrayDimInStructField` (checker unit, expectNoErrors).
- **Residual gap (M3-residual)** — ✅ FIXED+LANDED by M6 (binate `3a3fa453`, 2026-06-06): the struct-BEFORE-const order (`type S struct { arr [N]int }; const N int = 3`) now resolves correctly — dependency-ordered const resolution (resolveTopLevelConsts) runs before struct types are collected, so the dim sees N's folded value. **Test**: `TestStructBeforeConstDim` (checker unit, expectNoErrors).

#### M4 — float const referencing only float consts → int 0 — ✅ FIXED+LANDED (binate `c716ea0c`, 2026-06-06)
- **Symptom**: `const C float64 = A + B` (A,B float consts, no float literal) → isFloatExpr false (literal-only) → integer evalConstExpr → lookupConst returns Val=0 for CONST_FLT entries → C registers CONST_INT 0 (reviewer). Checker accepts.
- **Fix**: isFloatExpr should also recognize a const-ident operand whose const is float; or the shared classifier should consult the operand const kinds. **Test**: conformance xfail.

#### M5 — iota inside a float CONST_EXPR re-lowers to 0 — ✅ FIXED+LANDED (binate `c716ea0c`, 2026-06-06)
- **Symptom**: `const ( C float64 = 1.5*cast(float64,iota); D; E )` → 0.0,0.0,0.0 (reviewer). CONST_EXPR stashes only the AST, not the iotaVal; the read-site genExpr has no iota in scope → `iota` ident → EmitConstInt(0). Affects bare iota-repeat float members too.
- **Fix**: capture iotaVal with the CONST_EXPR and bind it at the read site, or fold float-with-iota at gen time. **Test**: conformance xfail.

#### M6 — forward-ref non-literal untyped const → silent false-accept — ✅ FIXED+LANDED (binate `3a3fa453`, 2026-06-06)
- **Symptom**: `var x int = A; const A = B; const B = 1.5` is accepted with NO error (reviewer-verified probe); reversed order correctly errors. The pass-1 placeholder for a NON-literal initializer is a value-less untyped-int (untypedConstPlaceholder fall-through), which AssignableTo treats as assignable to any int with the fit-check skipped — so a forward use sees int, not the const's real (float/out-of-range) type. Trades a loud `undefined` for a silent missed type error.
- **Root cause**: untypedConstPlaceholder returns value-less untyped-int for non-literal initializers; AssignableTo skips the fit-check for value-less untyped-int.
- **Coarse fix REJECTED**: "don't forward-register non-literal untyped consts" (gate on `isSimpleLiteral`) was tried and reverted — it regresses the *legal* `var x int = A; const A = 1 + 2` (pass-2 use-sites are source-ordered and see only the placeholder → `undefined A`). The gate can't tell a legal forward int const from an illegal float one in pass 1.
- **Fix**: `resolveTopLevelConsts` (check_const.bn) resolves every top-level const in DEPENDENCY order in pass 1 — depth-first, resolving each initializer's referenced consts first (ConstResolving stack → cycle detection; ConstResolved memo), then `checkConstDecl` records the real type+value. A forward use sees the real type; struct field array dims see the folded value regardless of source order (also fixes M3-residual). Gated on a new `ReplDeclMode` flag (NOT TentativeMode, which is false during the REPL's pass-1) so the REPL keeps parking forward-ref consts. Approved acceptance changes: forward float-const→int errors; forward int-const out-of-range for a narrower target fails the fit-check; const cycles report a clean error. **Tests**: check_const_test.bn (float-rejected, struct-before-const, int-accepted, float-chain, cycle, self-cycle, out-of-range). Full builder-comp conformance 1070/0.

#### M7 — &f()[i] / &a[i][j] wild-pointer — ✅ FIXED+LANDED (binate `fdc92562`, 2026-06-06)
- **Symptom**: `&get()[1]` (call base) and `&a[i][j]` (nested-index base) compile then SIGSEGV / invalid IR (reviewer). genIndexPtr only handled e.X.Kind IDENT/SELECTOR; other bases returned nil → genUnary fell through to the r-value wild-pointer path (gen_expr.bn:177). Pre-existing (not a regression).
- **Fix (gen_access.bn genIndexPtr)**: general arm — (1) nested-index base recurses genIndexPtr for an in-place pointer to the inner element, then indexes it (array inner → GEP the pointer; slice/raw-ptr inner → load then index); (2) r-value base (call result) is genExpr'd and its slice/raw-pointer backing is GEP'd; an r-value array has no stable address → nil. The `&a[i][j]`-array sub-case became reachable once **M8** landed (same commit). **Test**: conformance 623 (unxfailed, call→managed-slice) + 638 (&a[i][j] array + slice-of-slices).

#### M8 — nested ARRAY indexing `a[i][j]` emits invalid LLVM — ✅ FIXED+LANDED (binate `fdc92562`, 2026-06-06)
- **Symptom**: plain `a[i][j]` on a 2-D array (e.g. `var a [2][3]int; a[1][2] = 7; println(a[1][2])`) — NO `&` involved — fails to compile: `error: '%vN' defined with type 'i64' but expected 'ptr'` (the codegen GEP-on-raw-pointer handler bitcasts i8*→elem*, but the base is the LOADED array r-value, an integer-ish value, not a pointer). Affects both READ (genIndex) and WRITE (assignment lowering).
- **Root cause**: same non-IDENT/SELECTOR index-base limitation as M7, but in genIndex (read) and the lvalue/assignment path: for a nested base `a[i]` they loaded the inner array as an r-value and then GEP/SliceGet it. Nested SLICE indexing already worked (the loaded inner slice value still carries its backing pointer); nested ARRAY did not.
- **Fix**: genIndex + the index-assignment lowering detect a nested-ARRAY base by TYPE (indexExprType / isNestedArrayBase — no genExpr, so the inner index isn't evaluated twice) and route through genIndexPtr to load/store via an in-place element pointer. Array-element store logic extracted into emitArrayElemStore (shared with the IDENT/SELECTOR arm). Verified 2-D/3-D arrays, arrays of managed slices, slice-of-slices. **Test**: conformance 637 (nested array read/write, incl. 3-D + managed element).

#### Minor follow-ups (adversarial review 2026-06-06)
- ~~bool-logic (`&&`/`||`/`!`) const-folding has no test~~ — ✅ FIXED (binate `1d41aa62`): adding the test surfaced a real miscompile (a bool const referencing another bool const, `const C bool = !A`, misfolded to int 0 — evalConstBool had no ident arm); fixed via lookupConstBool + ident/selector arms.  Conformance 642 + evalConstBool unit tests; gen_const folding helpers split into gen_const_fold.bn.
- REPL parked-member + iota-repeat: a bare member after a PARKED explicit member gets plain iota (prevExpr — and now prevTyp — not updated across the parked `continue` in genConstGroup); GenConstMember has no iota-repeat. REPL-only. STILL OPEN.
- ~~named-float / named distinct scalar type mis-lowering~~ — ✅ FIXED + LANDED (binate `b43a0057` LLVM + shared type/IR-gen, `5b64b44a` VM, `0ca49975` native aa64/x64).  IR-gen now registers a named distinct non-struct type as a `TYP_NAMED` carrying its name (bare for the current package / REPL / self-types, qualified for imports — mirroring named structs, so method-dispatch keys agree) with `.Underlying` set, via a shared `typeDeclEntryType` helper at the six registration sites; resolveTypeExpr returns the TYP_NAMED.  Every Kind/Width/Signed-based lowering decision peels TYP_NAMED (codegen llvmType/typeBits/typeWidth/isUnsigned/emitBinop/emitCmp/emitCast/emitBitCast/OP_NEG/funcval-ABI + emitCopyRec/emitZeroRec; ir gen_print/gen_dtor/shift+divide signedness; VM via vmUnwrapNamed; native via common.UnwrapNamed).  types IsInteger/IsFloat now recurse and IsBool gained the peel.  Checker `resolveBuiltinScalarTypeDecls` fills a named-over-builtin underlying before top-level consts resolve (so `const C Rate = 0.5` over `type Rate float64` typechecks).  Also fixed a latent miscompile this surfaced: a named struct method-value receiver wider than one word was copied/zeroed as a single i64 (the int fallback masked it).  Conformance 646-652 (float, value+pointer methods, struct/array/managed-slice members, func/multi-return, sized-int width+sign, named-float const, cross-package value+method) green on every runnable mode; unit tests pin the codegen/types peels.  **Plan: `plan-named-distinct-scalar-types.md`.**
- ~~negative / div-by-zero array dims have no clean diagnostic~~ — ✅ FIXED (binate `a341b521`): evalConstInt now reports a negative length and a fully-known div/mod-by-zero dimension.  Conformance 643 / 644 error tests.
- ~~bare iota-repeat member type uses the GROUP (first-member) type~~ — ✅ FIXED (binate `9af67422`): genConstGroup tracks prevTyp alongside prevExpr, so a bare member inherits the PRECEDING member's type.  Conformance 645.
- ~~stale comments~~ — ✅ DONE (binate `73046ef3`): iota-repeat.bn comment updated to the fixed runtime (1,2,4,8).  The aarch64 "D-regs at offset 100" comment is already gone from the tree (recent float work removed it).

### ~~Native backends drop `binate_runtime.c` — every native program fails to link~~ — FIXED + LANDED 2026-06-05 (binate `1285683e`)
- **Was**: every `builder-comp_native_aa64-comp_native_aa64` cell failed at link
  with `Undefined symbols for architecture arm64: "_bn_pkg__bootstrap__Write"`.
  Self-hosted `BNC_NATIVE` computed an empty `runtimePath` (findRuntime ends in
  `return suffixes[i]`) so the `if len(runtimePath) > 0` gate dropped
  `binate_runtime.c` from the link.
- **Actual root cause** — a **shared native-backend** wrong-code bug, NOT what
  this entry first guessed: both native backends (aa64 AND x64 — not aa64-only)
  lowered an aggregate `OP_LOAD` as a bare *pointer into the source object*
  instead of materializing a copy. `return container[i]` then copied the
  element header into the sret buffer only AFTER the function's cleanup RefDec'd
  (and freed) the local container's backing → read freed/zeroed memory, so the
  return came back empty/garbage. LLVM and the VM were always correct (LLVM loads
  the aggregate into an SSA value at the load site).
- **`ee671b6c` (sub-word narrowing) was REFUTED by bisect** — rebuilding gen1
  with `emitSubWordNarrow` neutralized left the repro broken. It was never the
  cause; the bug is not char/sub-word arithmetic and predates `ee671b6c`. The
  earlier "aa64-only / findRuntime char handling / prime-suspect ee671b6c"
  framing in this entry was all wrong (recorded here so the mistake isn't
  repeated).
- **Fix**: `PlanFrame` now reserves an own data region for an aggregate
  `OP_LOAD` (as `OP_MAKE_SLICE` / aggregate calls already do); `emitLoad` copies
  the loaded bytes into it and points the result there, so the load owns its
  bytes and can't alias a freed source. Fixed in both the aa64 and x64
  `emitLoad`. aa64-native lane: 0 passed (all COMPILE_ERROR) → 811 passed, 0
  failed.
- **Tests**: `conformance/regressions/return-aggregate-element-of-local`
  (managed-slice element + struct array element returned directly — caught in
  the existing gen1-native lane, which is why a bespoke BNC_NATIVE smoke wasn't
  needed) + `TestPlanFrameReservesAggregateLoadDataRegion` (native/common).

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

### A float literal narrowed to `float32` is NOT coerced at call-arg / composite-field / return positions — FIXED+LANDED (binate `d37cc7ba`, 2026-06-05)
- **Symptom**: an untyped float literal flowing into a `float32` slot via a
  function **argument** (`f(0.1)` where `f(x float32)`), a **composite-literal
  field** (`S{f: 0.1}`, field `f float32`), or a **return** (`func g() float32 {
  return 0.1 }`) is NOT narrowed double→float32. Arg and field SILENTLY produce
  the wrong value: `bit_cast(int32, x)` reads `0x9999999A` (low 32 bits of
  `double(0.1)`) instead of `0x3DCCCCCD` (`float32(0.1)`). Return emits invalid
  LLVM (`value doesn't match function result type 'float'`) → clang rejects.
  Fails on **every** backend (LLVM, VM, native) — it is a front-end gap, not a
  backend issue. The control cases `var x float32 = 0.1`, `const C float32 = 0.1`,
  and a const-group member all narrow correctly (so the coercion exists; it is
  just not applied at these three positions).
- **Root cause (suspected)**: the front-end inserts the float-narrowing
  `OP_CAST` (→ `fptrunc` / `BC_F64_TO_F32`) only on var-init / typed-const decls
  via `ensureWidth`; the call-arg path (`genExprOrFuncRef` / `coerceArg`),
  composite-field store (`gen_composite.bn` `EmitStore`, no `ensureWidth`), and
  the `return` path do INT narrowing only — an untyped-float literal at a
  `float32` slot keeps its `double` type. Cite: gen_composite.bn:50-59,140;
  gen_expr.bn:37-39 (untyped-float born `double`).
- **Severity**: CRITICAL — passing a float literal to a `float32` parameter or
  initializing a `float32` struct field with one are idiomatic, and the value is
  silently wrong (no diagnostic). Distinct from the DEFERRED §844 (which is the
  *backend* float32-const bug on VM/native); this is a front-end coercion gap
  that hits LLVM too.
- **Test**: `conformance/matrix/const/{call-arg,field,return}/float32/*` (9 cells;
  arg/field = wrong value, return = compile error). To land: see the
  matrix-vs-regressions decision below — likely a few representative
  `regressions/` cells (the bug is position-dependent, not type-dependent).
- **Discovery**: 2026-06-05, P1 const matrix (read-form axis).
- **Fix**: apply the float-width coercion (`ensureWidth`/equivalent) for
  untyped-float literals at call-arg, composite-literal-field, and return
  positions — the same narrowing the var-init path already performs.

### Local `const` declarations silently materialize 0 — FIXED+LANDED (binate `273d7e4a`, 2026-06-05)
- **Symptom**: a `const` declared inside a function body (`func main() { const C
  T = V; var x T = C }`) reads as **0** (the zero value), for EVERY type
  (int/uint of all widths, float32, float64). The value `V` is dropped entirely.
  Fails on every backend (LLVM/VM/native). Package-level `const`, const-group
  members, and inline literals all work — only the **local** const form is
  broken. Local `const` is currently used nowhere in the compiler tree or
  conformance suite, so real-world impact is nil today, but it is a silent-wrong-
  value landmine.
- **Root cause (unknown — needs investigation)**: a local const declaration
  appears to register the name but never bind its value at the IR-gen read site
  (the read resolves to a zero-initialized slot rather than the const's
  materialized value). Either local consts must materialize like package consts,
  or the type-checker should reject local `const` until supported — silently
  emitting 0 is the wrong outcome.
- **Test**: `conformance/matrix/const/local-const/*` (12 cells, all types). To
  land: see the matrix-vs-regressions decision (one representative cell likely
  suffices — the bug is type-independent).
- **Discovery**: 2026-06-05, P1 const matrix (read-form axis).
- **Fix**: bind a local const's materialized value at its read site (mirror the
  package-const path), or reject local `const` at type-check if intentionally
  unsupported.

### Non-integer const-EXPRESSIONS (binary float, bool comparison) and const-as-array-dimension are dropped → read as int 0 — FIXED+LANDED (binate `52a9eabf` and predecessors, 2026-06-05)
- **Scope**: this is the const-*expression* tail of the non-int-const family
  (the literal cases — `const C float64 = 0.1`, `const B bool = true` — were
  fixed in Phase A; see the "top-level consts of non-int types" MAJOR entry).
  `classifyConstLit` recognizes only a *bare / unary-minus* float or bool
  **literal**; any non-int const whose initializer is an **expression** still
  falls through to the integer-only `evalConstExpr`, which can't evaluate it, so
  `genConst` drops the const and reads fall to `EmitConstInt(0, TypInt())`.
- **Confirmed manifestations** (2026-06-05, on LLVM — default mode):
  - **binary float** — `const X float64 = 1.5 + 2.5` (and `*`, `/`) reads as
    **0** (silent wrong; in some shapes emits `mul i64` over `double` operands →
    invalid IR / clang reject).
  - **bool comparison** — `const B bool = 1 < 2` reads as **0** (false) instead
    of true; `< == > …` const-comparisons are dropped.
  - **const-as-array-dimension** — `const N int = 3; var a [N]int` →
    `len(a)` is wrong (observed 30, not 3): `resolveTypeExpr` (gen_util.bn:354-359)
    uses `parseIntLit(te.Len.Name)` on the *ident text*, never resolving the
    const; and `[N+1]int` is rejected outright by the checker's `evalConstInt`
    ("array length must be a constant integer") even though it is one.
- **Root cause**: IR-gen's const-expression evaluation is integer-only
  (`evalConstExpr`, gen_const.bn) and `classifyConstLit` is literal-only; the
  checker accepts these decls (it does fold ints via `foldIntArith`/
  `foldIntBitwise` but attaches no value to float/bool exprs). Same root as the
  non-int-literal family — extended from *literals* to *expressions* and to the
  array-dimension read path.
- **Severity**: MAJOR — silent wrong values (bool/float) and a silently wrong
  array length, on idiomatic const-expressions; the binary-float shape can also
  emit invalid IR.
- **Tests**: `conformance/regressions/const-expr/*` — green baselines
  (`int-arith`, `int-bitwise`, `int-paren`, `int-of-const`, `float-neg-literal`,
  `bool-literal`) confirm the integer/literal paths fold; xfailed
  (`float-binary-{add,div,mul}`, `bool-comparison`, `array-dim`) pin the gaps.
- **RESOLVED — now a Plan-1 defect (2026-06-05, user decision)**: a **bare**
  const-group member must **repeat the previous initializer expression**
  (Go-style), not take plain iota. Today it takes plain iota
  (`gen_const.bn:293-299`), so `const ( B0 int = 1 << iota; B1; B2; B3 )` gives
  `1,1,2,3` instead of the correct `1,2,4,8` bit-flag idiom, and
  `const ( K0 int = iota + 100; K1; K2 )` gives `1,2` instead of `101,102`. This
  is now a CONFIRMED bug to fix in Plan 1: a bare member re-evaluates the most
  recent explicit initializer expression with its own `iota`. Test:
  `conformance/regressions/const-expr/iota-repeat` (the `1<<iota` bit-flag form,
  xfailed until implemented).
- **Discovery**: 2026-06-05, P1 const-expr loose-axis (design fan-out + probes).
- **Fix**: evaluate non-int const *expressions* at the right type — fold float
  const-exprs at float precision and bool const-comparisons to a bool, and
  resolve const idents/exprs in the array-dimension path — or reject
  unsupported const-exprs with a clear diagnostic rather than dropping to int 0.

### Native backends mis-pass a variadic float `__c_call` argument — CONFIRMED, both native backends — ✅ RESOLVED (binate `56f09bc6`, SysV `AL=nsrn` + AAPCS64-darwin variadic-stack rule)
- **Symptom**: a variadic `double` passed via `__c_call` reaches the callee
  wrong on the native backends — `__c_call("printf", int32, fmtPtr, ...,
  cast(float64, 2.0))` with format `"%.0f\n"` prints **0**, not **2**. Correct
  on LLVM (comp) and the VM is N/A (`__c_call` is compiled-mode-only). Fails on
  both `native_aa64` and `native_x64`.
- **Root cause (suspected, §3.9)**: the variadic calling-convention edge — on
  x86-64 SysV the caller must set `AL` = number of vector (XMM) args so a
  variadic `double` is read from `XMM0`; on darwin-arm64 every variadic arg is
  passed on the stack as an 8-byte slot (not in registers). The native backends
  do neither for the `__c_call` variadic tail, so the float lands in the wrong
  place and printf reads garbage/0.
- **Test**: `conformance/regressions/c-call/printf-variadic-float` (xfailed the
  3 native modes; also xfailed VM + arm32 like all `__c_call` cells).
- **Discovery**: 2026-06-05, P1 `__c_call` loose-axis.
- **Fix**: in the native `__c_call` lowering, implement the variadic ABI —
  set `AL`=vector-count on x64-SysV; stack-pass varargs on darwin-arm64
  (per-target, since the convention differs).

### `handle` is not a user-expressible call shape — NOT a bug, design note
- While extending the ABI matrix with call shapes, confirmed there is **no user
  syntax that emits `OP_CALL_HANDLE` with a value argument**: `OP_CALL_HANDLE`
  is the compiler-internal dtor/free dispatch (`_call_dtor` / `_call_free_fn`,
  gen_call.bn:241), always invoked with a single pointer. A user "call through a
  function value" lowers to `OP_CALL_FUNC_VALUE`, already covered by the ABI
  matrix's `funcval-param` cells. So the §3.9 "CALL_HANDLE aggregate by-value"
  concern has no user-level test surface; nothing to add.

### `&slice[i]` (address-of a slice element) lowers to a wild pointer — FIXED+LANDED (binate `937ae78e`, 2026-06-05)
- **Symptom**: taking the address of a *slice*-indexed element yields a garbage
  pointer instead of the element address. `var p *uint8 = &s[0]; *p = 66`
  SIGSEGVs (the store writes through `(i8*)0x41`). Affects both `@[]T`
  managed-slices and `*[]T` raw slices; **fixed arrays `[N]T` are correct**
  (`&a[0]` works). Crashes identically compiled (bnc) and interpreted (bni), so
  the defect is in the shared IR address-of lowering, not a backend.
- **Root cause (CONFIRMED)**: the address-of path for a slice-indexed l-value
  computes the correct element address via GEP, then wrongly falls through to the
  *r-value* path — it loads the element and `inttoptr`s the byte:
  `%a = getelementptr i8, i8* %data, i64 %idx` (element address — correct) →
  `%v = load i8, i8* %a` (BUG: loads the VALUE) →
  `%p = inttoptr i8 %v to i8*` (BUG: byte → pointer). Fixed arrays take the
  proper address path (yield the GEP), which is why `&a[0]` works; slice-indexed
  operands share the load path instead. Likely in IR-gen's address-of handling
  for a SliceIndex operand (gen_expr l-value path).
- **Test**: `conformance/599_addr_of_slice_elem.bn` — `&slice[i]` write-through +
  read-back on `@[]T` and `*[]T` (mutation must be visible; currently SIGSEGVs).
  Xfailed in all 6 default modes.
- **Discovery**: 2026-06-05, while probing bundle I/O for the minbasic example —
  `__c_call("write", …, &buf[0], …)` silently wrote nothing; chasing it exposed
  the address-of miscompile. Confirmed firsthand against `bnc-0.0.7` with
  `--emit-llvm`, and **confirmed still present in local main HEAD** (2026-06-05)
  via `conformance/run.sh builder-comp` + `builder-comp-int`.
- **Fix**: the slice-indexed l-value address-of must yield the GEP'd element
  address, not load+inttoptr — mirror the fixed-array address path. (If
  `&slice[i]` were intentionally unsupported, reject at type-check instead — but
  arrays support it and raw pointers are the documented hot-path escape, so
  emitting the address is the intended fix.)

### VM: a function value RETURNED from a call and PASSED DIRECTLY as an argument has a nil vtable — CONFIRMED, VM-only — ✅ RESOLVED (binate `e337e413`, `isVMAddressAggregate` single-return copy-back in `lowerReturn`)
- **Symptom**: `use(mk())`, where `mk() @func(...)` returns a (non-capturing)
  function value and `use(w @func(...))` invokes it, aborts in the bytecode VM
  with `vm: function value has nil vtable`. Compiled (native) is correct.
- **Scope**: bytecode VM ONLY (LLVM/native correct). Triggered specifically by
  passing a freshly-RETURNED function value DIRECTLY as a call argument. The two
  halves work in isolation: returning a function value then calling it directly
  (`var w = mk(); w(x)`) is fine, and passing a LOCAL/param function value as an
  arg (`use(w)` with `w` a local) is fine — only the un-materialized
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
- **`vm-int-to-float32` — VM `int → float32` is broken (every width/sign)**:
  every `cast(float32, <int>)` diverges — even `cast(float32, 1) > 0.0` is
  false on the VM. `float64` conversions, `float32 → int` truncation, and
  `float32` literals all work; the 17 xfailed VM cells (all `int-to-float` /
  `float-to-int` / `float-cast`) fail *only* on their `float32` tuples.
  Distinct from the now-resolved unsigned→float64 signedness bug above. Likely
  the VM never implemented (or mis-lowered) the 32-bit-float conversion target.
  Tests: the 17 cells, xfailed on `builder-comp-int` / `-int-int` /
  `-comp-comp-int`. Fix: implement/repair `int → float32` in the VM's
  `lower_cast` (both `BC_SITOF`/`BC_UITOF` to a 32-bit float result).
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

### Returning a by-value struct through interface-method dispatch was miscompiled — FIXED + LANDED 2026-06-04 (binate `9baa579d`)
- **Was**: an interface method returning a by-value struct (small
  aggregate, NOT a managed handle like `@T`/`@[]T`) came back through
  vtable dispatch with only its FIRST field correct, later fields garbage,
  in BOTH the LLVM backend and the bytecode VM.  Direct (concrete-receiver)
  calls were fine.
- **Root cause**: the interface method's result type was resolved during
  interface collection (GeneratePackage / GenModule first pass), which ran
  interleaved with struct-name registration in declaration order.  An
  interface method whose result is a struct declared LATER in the file
  (`interface B { get() Pair }` before `type Pair struct {...}`) resolved
  the struct via resolveTypeExpr's unresolved-name path, which silently
  falls back to `int`.  OP_CALL_IFACE_METHOD's result type (`instr.Typ`)
  thus degraded to a single word; both backends read `instr.Typ`, so both
  miscompiled identically (llvmType -> `i64`; the VM mis-sized the result).
  Latent because conformance/553 only returned a scalar / a managed-slice
  through an interface, never a plain struct.
- **Fix** (`9baa579d`): a struct-name pre-pass registers every struct name
  before the first pass, so interface method result types resolve to the
  real struct type.  Interface collection stays interleaved in the first
  pass (order vs globals / type-aliases -- which may be interface-typed;
  isInterfaceTypeExpr consults moduleInterfaces -- is unchanged).
  conformance/581 covers 2- and 3-field structs through managed- and
  raw-receiver dispatch, interfaces declared before the structs.  Full
  conformance green (505 comp / 499 int); no other
  by-value-struct-returning interface exists in-tree (Backend returns
  bool / @[]char).
- **Unblocked + LANDED 2026-06-04** (binate `b9ca1acc`): the repl ReplSession->interface conversion.

### Multi-value assignment `a, n = f()` mishandled managed targets — FIXED + LANDED 2026-06-03 (binate `0b3f4abe`)
- **Was**: `genMultiAssign` (then inline in `genAssign`) Axiom-3 copy-RefInc'd each managed component then stored it, with two defects:
  - **Defect A (CRITICAL, wrong-code/UAF)**: the copy-RefInc had arms for `@T` / `@[]T` / `@Iface` but **none for `@func`**, so `g, n = f()` returning `(@func(...), int)` stored the `@func` without a copy-RefInc; the call-result temp's dtor freed the closure record while `g` still pointed at it → UAF on invoke (+ double-free at scope exit).  Probe: a capturing `@func` multi-assigned then invoked → SIGSEGV.
  - **Defect B (MAJOR, leak)**: the IDENT / INDEX / SELECTOR stores overwrote the target with no RefDec of its OLD managed value, so reassigning a live managed variable leaked the previous value (+1/exec).
- **Fix**: reworked the multi-assign managed-store to mirror single-assign's RefInc-new / RefDec-old discipline (Axiom 5) across all four managed VALUE types (`@T`/`@[]T`/`@func`/`@Iface`) and all three target shapes (IDENT / INDEX / SELECTOR), via new shared dispatchers `emitManagedValueCopyRefInc` / `emitManagedValueRefDec` (gen_util_refcount.bn) + predicate `isManagedScalarType` (gen_refcount_pred.bn).  The multi-assign body was extracted to `genMultiAssign` + `emitIndexStore` in a new `gen_assign_multi.bn` (gen_control.bn was over the 500-line soft cap).  Blank `_` targets still skip copy-retain (the `_`-discard fix, `567`).
- **Tests**: conformance `571_multiassign_old_value_released` (B: aliased object's refcount returns to baseline), `572_multiassign_func_value_retained` (A: capturing `@func` multi-assigned + invoked, no UAF — crashed pre-fix), plus `gen_assign_multi_test.bn` unit tests (bound component copy-RefInc'd vs blank `_` skipped, for `@T` and `@func`; index target refcounts the old element).  Green in all 6 default modes; compiled 491/0, int 485/1 (the 1 = pre-existing 520).
- **Struct-aggregate SELECTOR/INDEX — FIXED 2026-06-03 (binate, pending cherry-pick)**: a managed *struct/array AGGREGATE* field/element targeted by a multi-assign SELECTOR/INDEX (`s.structField, n = f()` / `arr[i], n = f()` where the element is a managed struct) was a plain store — no save-copy-destroy — so the new aggregate's managed fields were under-retained (double-free at scope end) and the old element's leaked.  Now save-copy-destroyed: SELECTOR mirrors the IDENT struct case; INDEX array/pointer via a new `emitElemPtrStore` helper, INDEX slice via `emitStructElemRefcount`.  Test `conformance/574_multiassign_struct_aggregate` (captured `@Counter` refcount returns to baseline 2, was 1 pre-fix); green in all 6 modes, verified to fail pre-fix.
- **Discovery**: 2026-06-03, reviewing the multi-assign path while fixing the `_`-discard leak (`570`).  Pre-existing.

### `@func` copy-RefInc symmetry — FIXED 2026-06-03 (binate `d118a3c4` + `76099018`); `@Iface` analogue + VM-leak still open
- **Was**: `@func` / `@Iface` values (`TYP_MANAGED_FUNC_VALUE` /
  `TYP_INTERFACE_VALUE_MANAGED`) had `NeedsDestruction() == false`, so the
  struct copy/dtor generators, `emitStructElemRefcount`, and the
  assignment paths skipped them on COPY, while `@func`/`@Iface` LOCALS
  *were* RefDec'd at scope end — an acquire/release asymmetry.  A
  capturing `@func` stored into a struct field, passed as a parameter, or
  returned dropped its only owning ref; the param/scope-end RefDec then
  freed the capture record while a field/caller still pointed at it, and a
  later invocation was a use-after-free.  Concrete all-modes repro:
  `conformance/534_func_value_param_to_field_capture`
  (`func install(h @Holder, f @func(int) int) { h.F = f }` then invoke
  `h.F`) — SIGSEGV compiled.
- **`@func` half FIXED** (binate `d118a3c4`, `76099018`):
  1. `d118a3c4` — null-safe `emitManagedFuncValueRefDec`: guard the
     closure-dtor fetch (vtable[0] load, `OP_FUNC_VALUE_DTOR`) + RefDec
     behind `data != null`.  The flip below makes struct dtors run on the
     zero-inited `@func` fields a managed struct's `make()` leaves behind
     (`{vtable=null, data=null}`); the unguarded vtable[0] load faulted on
     the null vtable.  Shared IR layer → fixes every backend + the VM.
  2. `76099018` — flip `NeedsDestruction(@func) = true` + acquire (RefInc)
     at every copy site: parameter entry, var-init / short-var
     (isFresh-guarded), the three assignment paths, return,
     `emitStructElemRefcount`, and slice/array element stores.
  `534` now passes in **all 6 default modes** and is un-xfailed; `542`
  adds a return-a-capturing-closure regression.  Unit test
  `TestEmitFuncValueRefDecGuardsNullData` pins the guard shape.
- **VM capture-record leak — FIXED 2026-06-03 (binate `0a0d00af`).**  Under
  the bytecode VM a capturing `@func`'s data slot is a 32-byte
  `DATA_KIND_COMPILED_CLOSURE` rec whose `rec[3]` points at the heap
  closure struct; RefDec'ing the @func value decremented the *rec* and
  (`vt.Dtor == 0`) just freed it, never the struct → the struct and its
  captured managed values leaked.  Fix:
  `ensureHandle` marks an IsClosure callee's vtable dtor slot with a `-1`
  sentinel; `BC_REFDEC_INLINE_FAST` recognizes it, frees the rec and
  RefDec's the closure struct, running its dtor via an iterative frame push
  (flat-stack, no host recursion at `-int-int` depth).  Dtor name plumbed
  ir.Func → VMFunc, resolved by `LookupFunc`.  Conformance `550` pins it
  (captured `@Counter` refcount returns to baseline).  @func is now
  leak-clean on every backend + the VM.
- **REMAINING — `@Iface` analogue still BROKEN** (the symmetric half).
  `emitManagedIfaceValueRefDec` has the same unguarded vtable[0] load (the
  shared `emitVtableDtorLoad`) and there is no `@Iface` acquire arm on
  copy.  `520_iface_dtor_callee_sole_ref` fails in all int modes ("call
  through nil interface value"); `383_cross_pkg_iface_dtor` is in the same
  family (and additionally hits the int-int multi-package loader bug
  below).  Apply the same recipe to `@Iface`
  (`TYP_INTERFACE_VALUE_MANAGED`): null-safe iface RefDec + flip + acquire
  arms.  This is the separate "@Iface first-class" follow-up.
- **Unblocks the REPL interrupt seam (Stage 5 of `plan-repl-embeddable.md`)
  — DONE.**  `vm.SetPoll(poll @func(@VM) int) { vm.Poll = poll }` is the
  param→field `@func` store; with the acquire arms a CAPTURING poll no
  longer UAFs.  Capturing-poll seam tests added and green in every int
  mode: `pkg/binate/vm/vm_poll_test.bn` (`TestCapturingPollFiresViaSetPoll`,
  `TestCapturingPollSuspendsAfterThreshold` — direct `vm.SetPoll`) and
  `pkg/binate/repl/step_test.bn` (`TestStepCapturingPollSuspendsTurn` — the
  end-to-end `s.SetPoll → vm.SetPoll` forward, a capture-driven SUSPEND
  mapping onto `STEP_SUSPENDED`).  The previously-omitted non-capturing
  NOTEs in those files are updated to describe the capturing coverage.

### A closure that captures a `@func` under-retained the captured value — FIXED + LANDED 2026-06-04 (binate `388c48d3`)
- **Was**: a closure that captures a `@func` value did not acquire a ref
  to the captured @func's record, but the closure struct's dtor RefDec'd
  it (NeedsDestruction(@func) = true).  The captured @func was
  under-retained: its record freed when the source @func's scope ended,
  then the closure called / dtor'd freed memory (use-after-free).  Native
  only; a flaky crash in __dtor_closure_* (deterministic under
  guard-malloc).  First seen as a wrapper poll (capturing a host @func)
  installed via vm.SetPoll — the shape an embedder needs for a VM-free
  poll — but the root cause is general (any closure capturing a @func).
- **Root cause**: gen_func_lit.bn emitCaptureRefInc handled
  TYP_MANAGED_PTR / TYP_MANAGED_SLICE but had no TYP_MANAGED_FUNC_VALUE
  branch — the capture-side acquire counterpart of the @func copy-RefInc
  symmetry work (d118a3c4 / 76099018), missing for closure captures.
- **Fix** (`388c48d3`): add the TYP_MANAGED_FUNC_VALUE branch calling
  emitManagedFuncValueRefInc (the acquire helper every other @func copy
  site uses).  conformance/586 pins it deterministically via refcounts;
  pkg/binate/vm TestWrappedCapturingPollSuspends covers the wrapper-poll
  shape.  Full conformance green (513 comp / 507 int).
- **Unblocked + the VM-free poll is now LANDED 2026-06-04** (binate
  `e3dc0d07`): repl's SetPoll takes a VM-free `@func() PollResult`, so
  the ReplSession interface no longer mentions pkg/binate/vm.

### `136_grouped_imports` / `383_cross_pkg_iface_dtor` — `package "pkg/builtins/rt" not found` under int-int — FIXED+LANDED (binate `db18f26b`, 2026-06-05; harness wiring, not the loader)
- **Symptom**: both fail ONLY in `builder-comp-int-int` with
  `package "pkg/builtins/rt" not found` (a loader error, before execution);
  green in all other modes.  Confirmed pre-existing on a clean tree
  (2026-06-03) — independent of the `@func`/`@Iface` work.  Both are
  multi-package tests (grouped imports / cross-package), so the deeply
  nested interpreter's package resolver appears to mis-resolve a transitive
  core import at int-int depth.  No xfail markers yet.  Root cause: unknown
  — needs investigation of the int-int package search-path setup.

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

### Lexer issues surfaced while authoring spec Ch.5 (Lexical Elements) — 2026-06-08
Found writing the docs spec's Lexical Elements chapter (adversarial
verification of the draft against `pkg/binate/lexer`). All MINOR
(confusing errors / silent leniency, not silent miscompile). Tests +
xfails pending a coordinated `binate` worktree. The spec documents these
as open items (`lex.literal.int.leading-zero`, `lex.escape.unsupported`).
- **`0123` / `00` split into two integer tokens.** `lexer/scan.bn:84`
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

### A NAMED distinct *signed sub-word* integer's MIN/-1 divide escapes the divide-fault guard — ✅ RESOLVED in behavior (binate `b43a0057`, named-distinct landing — `widenType` preserves named width+sign); regression test pending (plan-cr2-followup Plan B)
- **Symptom**: `type I8 int8; var a I8 = <I8 MIN>; var b I8 = -1; a / b` does NOT
  panic with "integer overflow" (the ratified signed-MIN/-1 behavior); it
  silently wraps (the int64 divide `-128 / -1 = 128` truncates back to `-128`
  in the I8 result). Divide-by-zero on the same type IS still caught, and
  unsigned named types / named full-width signed types (`type Count int`) are
  fine — only a named *signed sub-word* type at exactly MIN/-1 is affected.
- **Root cause**: IR-gen's `widenType` (gen_binary.bn) collapses a distinct
  NAMED integer type to plain `int` (signed, host width) — the named/sized-ness
  is lost before the `OP_DIV_CHECK` guard sees the result type, so the guard
  uses INT64_MIN instead of the type's true (e.g. int8) MIN. This is a
  pre-existing `widenType` behavior, not a defect in the divide-fault guard
  itself (plain, non-named `int8`/`int16`/`int32` MIN/-1 ARE detected — they
  keep their TYP_INT width through widenType).
- **Discovered**: 2026-06-05 by the adversarial coverage review of the
  divide-fault guard (plan-divide-by-zero.md). The guard itself is correct;
  this is the one width-dependent corner it can't reach because the type info
  is already gone.
- **Proper fix**: make `widenType` preserve a named integer type (or at least
  its underlying width/signedness) for same-named operands, so `I8 / I8` keeps
  width 8. Out of scope for the divide-by-zero work (touches general arithmetic
  typing). A reproducer xfail cell can be added when this is picked up.

### Bare func literal in assignment position doesn't infer its managed/raw flavour from the LHS — ✅ RESOLVED 2026-06-10 (binate `e15680d7`)
- **✅ RESOLVED `e15680d7`** — the simple-assign RHS is checked via
  `checkExprWithFVHint(c, rhs, lhsType)`, so a bare func-literal `existing =
  func(){…}` (where `existing @func(...)`) now picks up the managed/raw flavour
  from the LHS, like var-init already did. NOTE: the NAMED func-value spelling
  (`type Fn @func(...)`) is still broken — the hint doesn't peel `TYP_NAMED` —
  tracked separately as the B2 MAJOR entry above.
- `existing = func(){...}` where `existing @func(...)...` fails type checking
  with `cannot assign <unknown> to <unknown>`: a bare func literal in
  **assignment** (non-var-init) position does not pick up its managed
  (`@func`) vs raw (`*func`) flavour from the assignment target's type.
  Var-init works (`var x @func(...)... = func(){...}` — the declared type
  hints the flavour).
- **Workaround in use**: assign through a typed var
  (`var drop @func(...)... = func(){...}; existing = drop`) — see
  `conformance/587_closure_captures_func_value.bn` and
  `conformance/matrix/assign/ident/func-value.bn`.
- **Fix**: in the assignment type-checker, flow the LHS func type's flavour
  to a bare func-literal RHS — the same hinting var-init already applies.
- Surfaced 2026-06-05 while authoring the conformance matrix func-value cell
  (plan-code-red.md §7 / P1).

### Wire the cross runners to `binate-paths --target` — ✅ RESOLVED 2026-06-10
- **Conformance (binate `a3755cb4`)**: the four cross *conformance* runners
  mirror their bnc `--target` onto the `binate-paths.sh --iface` call
  (arm32-linux, arm32-baremetal, x86_64-linux, x86_64-darwin); 692 green on
  every mode, no xfails.
- **Unittest (binate `ac738936`)**: the three parallel
  `scripts/unittest/runners/` cross runners (arm32_linux, arm32_baremetal,
  native_x64_darwin) now mirror `--target` too.  Inert today (no unit-test
  package imports `build`), but it closes the latent silent-miscompile gap.
- **Sweep complete**: a repo-wide grep confirms every `.sh` that passes
  `--target` to a compiler AND calls `binate-paths` now carries `--target` on
  its `--iface` call (7 sites: 4 conformance + 3 unittest).
- **Discovery**: adversarial verification workflow over the `a3755cb4` change.

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

### Unit tests RED on compiled-native modes — `pkg/binate/vm` / `repl` / `cmd/bni` test binaries don't link the std `_Package` objects — ✅ FIXED binate `f7d116f3` 2026-06-10 — was MAJOR
- **FIX**: the native backend now emits the per-package `_Package` descriptor + accessor (binate `f7d116f3`, `native: emit the per-package _Package descriptor`), so the `_bn_pkg__*___Package` symbols resolve. After it: `pkg/binate/repl` + `cmd/bni` are GREEN on native_aa64; `pkg/binate/vm` LINKS and runs (168 pass). conformance/532 now green on native aarch64 + x64-darwin (was LLVM/VM only). **Remaining native_aa64 `pkg/binate/vm` red is NOT this bug**: the 2 surviving failures are `TestExternFloat{,32}ArgViaRegistry` — the separately-tracked float-arg-shim bug (see below; it was MASKED by this link failure, now visible). Root-cause direction below was correct (native never emitted `_Package`); the original "make the link include the objects / xfail" framing is superseded by the real fix.
- **Symptom**: in `builder-comp_native_aa64-comp_native_aa64` unit mode, `pkg/binate/vm`, `pkg/binate/repl`, and `cmd/bni` FAIL to link: `Undefined symbols: _bn_pkg__bootstrap___Package, _bn_pkg__builtins__reflect___Package, _bn_pkg__builtins__rt___Package, referenced from _bn_pkg__binate__vm__RegisterStandardExterns`. **Reproduces LOCALLY** (`scripts/unittest/run.sh builder-comp_native_aa64-comp_native_aa64 pkg/binate/vm` → 0 passed, 1 failed). Pre-existing across many commits (Plan-C, getSelectorType, lang-bool, #113, …). NOT introduced by any bnc-0.0.8 release-prep lane; NOT xfailed (so it reads as a hard CI fail, not a tracked xfail).
- **Root cause (direction)**: `RegisterStandardExterns` (`pkg/binate/vm/extern_register_std.bn`, the Phase-B `_Package()` VM-extern feature, binate `feadde2c`) takes `*func() @reflect.Package` handles to `rt._Package` / `bootstrap._Package` / `reflect._Package`. Those `_Package` accessors are codegen-only (no `.bn` body — bnc synthesizes the def per *module*). The native unit-test link of the vm/repl/bni binaries references them but doesn't link any object that DEFINES them (no module in the test link emits the builtin packages' `_Package`), so they're undefined. The `builder-comp` (LLVM) unit mode passes — so this is specific to the native-backend unit-test link step.
- **Distinct from**: (a) Lane A's conformance `-comp*` `bn_pkg__bootstrap__Write` break (CI-only, doesn't reproduce locally; this one DOES reproduce locally and is the `_Package` symbol class); (b) the `loader` struct-mangler collision (different symptom). 
- **Also red (separate)**: `builder-comp-int-int` unit shards time out (~30m) in CI; `builder-comp_native_x64` (ELF) Perf fails (Lane A compiled-link family — native_aa64 Perf passes). 
- **Fix direction (owner's call)**: either make the native unit-test link include the builtin packages' `_Package`-defining objects (the harness/link step), or xfail `pkg/binate/{vm,repl}` + `cmd/bni` on `builder-comp_native_aa64`/`_x64` unit modes with a tracked reason until the `_Package` extern registration is link-complete for test binaries. **Not a shipped-artifact blocker** (the four release binaries build fine — `make-bundle.sh` green), but a red CI category to resolve or consciously accept before the bnc-0.0.8 tag.

### e2e/repl.sh + print-args.sh build broken — gen1 build resolved stdlib current-first, so the BUILDER hit current `std/errors` (`same`) — ✅ FIXED binate `c44ab9b7` 2026-06-10 (Lane C)
- **Symptom**: `e2e/repl.sh` (and `e2e/print-args.sh`) fail at their BUILDER-stage gen1 build: `impls/stdlib/common/pkg/std/errors/errors.bn:104:6: undefined: same` (+ `cannot call non-function` / `non-bool condition`). The e2e never runs because the toolchain build aborts.
- **Root cause (CORRECTED)**: the e2e scripts' two-stage gen1 build listed the checkout's stdlib roots AHEAD of the `$BUILDER_LIB` bundle (current-first), unlike the canonical `scripts/lib/build-compilers.sh` `build_gen1`, which lists the BUILDER's stdlib first. `cmd/bnc`'s import closure reaches `pkg/std/errors` (bnc → `native/common` → `std/strconv` → `std/errors`; `std/strconv` → `std/errors` also gives bni/bnlint the same closure), and `std/errors` uses the `same` builtin (binate `1f87b905`), which BUILDER `bnc-0.0.7` predates — so the BUILDER compiled CURRENT `std/errors` and choked on `same`. NOT a current-bnc bug (`same` works in gen1; conformance `661_same_ref` is green). The conformance/unit runners were unaffected precisely because `build_gen1` resolves stdlib BUILDER-first; the e2e scripts had drifted from that ordering.
- **Fix**: reorder the e2e scripts' gen1-stage `-I`/`-L` to put the `$BUILDER_LIB` stdlib roots ahead of the checkout's (core stays current-first), matching `build_gen1` (binate `c44ab9b7`). e2e/repl.sh now builds + passes 54/54 and print-args.sh 2/2 on the **pre-bump** tree. (The `same` skew also self-heals at the Convergence BUILDER bump, but the script fix makes e2e green without waiting for it.)
- **Correction to the prior note**: the earlier claim that "the four binaries don't import `std/errors`" is WRONG — bnc (via `native/common` → `std/strconv`), bni, and bnlint all transitively import it. The release **bundle** build (`make-bundle.sh`) was never blocked because its build scripts already resolve stdlib BUILDER-first, NOT because the binaries avoid the import.
- **Discovery**: 2026-06-09, building the CR-2 Plan-B B3 e2e value test (REPL parked-member iota-repeat). B3's e2e case (`tier3-pending-const-group-bare-iota-repeat`) now runs and passes.

### Add a hygiene check enforcing package-tier dependency rules (`pkg-layout-spec.md`) — bundled tiers must not import non-bundled tiers — FILED 2026-06-10
- **What**: a `scripts/hygiene/` check that statically validates every package's import closure against the tier ordering in `pkg-layout-spec.md` ("Tiers"). A package must not import a *less-bundled* (higher-numbered) tier. Concretely — tier 0/0b/1/1x packages (always- or by-default-bundled: `pkg/builtins/*`, `pkg/std/*`, `pkg/stdx/*`) must NOT import a tier-2/3 package (project-pulled / not bundled: `pkg/binate/*` and any other `pkg/<org>/*`). Also enforce the tier-2 transitive-closure rule (`pkg-layout-spec.md` "Tiers": tier 2's dependency closure must itself be tier 2). Tier is derivable from the import-path prefix (`pkg/builtins/`→0/0b, `pkg/std/`→1, `pkg/stdx/`→1x, `pkg/binate/` & other `pkg/<org>/`→2); `pkg/bootstrap` is a bundled runtime primitive (treat as tier-0-equivalent). EXEMPT `*_test.bn` — tests aren't bundled (e.g. `lang_test.bn` legitimately imports `pkg/binate/buf`).
- **Why**: a bundled package whose dependency closure escapes the bundled tiers silently breaks the bundle — the dependency's source isn't shipped, so a consumer compiling against the bundle gets `package "<dep>" not found`. NOTHING currently catches this: it only manifests when a consumer compiles the offending package from a real bundle (`make-bundle.sh` output), which no CI / hygiene / conformance step does today.
- **Motivating bug (discovery 2026-06-10, release-prep for `bnc-0.0.8`)**: `pkg/builtins/lang` (tier 0, always bundled) imported `pkg/binate/buf` (tier 2) for two `buf.CopyStr("true"/"false")` calls in `bool.String()`. The bundle ships only `lib/pkg/bootstrap`, not `pkg/binate/buf`, so the tier-0 `Stringer` carve-out (`var s *lang.Stringer = &x; s.String()`) failed to compile from ANY bundle with `package "pkg/binate/buf" not found` — present since `bnc-0.0.7`, undetected because the carve-out smoke step (`release-process.md` step 5) had never actually been run against a real bundle. Fixed in binate `84818a77` (lang returns bare string literals; `[N]readonly char → @[]char` is a literal-init allocate+copy). This check would have caught it at the `import` line.
- **Scope note**: adding the check ≠ wiring it into `scripts/hygiene/run.sh` / CI — but a hygiene check belongs in the run.sh master, so do both when implementing. A first audit may surface other pre-existing violations to triage.
- **First manual sweep (Lane C, 2026-06-10) — CLEAN baseline**: swept every import (incl. aliased) in the bundled trees (`ifaces/{core,stdlib}`, `impls/{core,stdlib}`, `pkg/bootstrap`, `runtime/`). No non-test bundled package imports outside the bundled set. Two non-obvious cases the eventual check must handle: (1) `impls/core/baremetal/pkg/builtins/rt` imports `pkg/semihost`, which is NOT a violation — `pkg/semihost.bni` ships under `runtime/baremetal_arm32/` (a bundled runtime component) and resolves under the arm32-baremetal build's own `-I`/`-L`; the check should treat shipped `runtime/<target>/pkg/*` as bundled, or scope tier rules per build target. (2) all `pkg/builtins/testing` imports are in `*_test.bn` (already EXEMPT) and it has a bundled `.bni` with a harness-provided impl. So `lang → pkg/binate/buf` (binate `84818a77`) was the only true tier-0→tier-2 violation; the baseline is otherwise clean.

### Remove `pkg/builtins/lang` → `pkg/bootstrap` dependency — the float `Stringer` borrows a deprecated, semi-private helper — FILED 2026-06-10
- **What**: `pkg/builtins/lang` (tier 0) imports `pkg/bootstrap` solely for `bootstrap.formatFloat`, called from `floatToCharSlice` (the helper behind `float32.String()` / `float64.String()`, `lang.bn:163-184`). Drop this dependency.
- **Two rules violated**:
  1. **`pkg/bootstrap` is slated for deprecation** — it's the transitional I/O + format primitive layer meant to be removed (cf. the println-hack / bootstrap-retirement direction). A tier-0, always-bundled stdlib package building a *public* API (`Stringer`) on top of it cements a dependency on infrastructure designed to go away.
  2. **`formatFloat` is semi-private** — lowercase (package-private by Binate naming convention) and exported via `pkg/bootstrap.bni` ONLY for a technical reason: "cross-compilation-unit linkage: IR-gen for the print/println builtin emits direct calls into this helper" (`bootstrap.bni:36-38`), and whitelisted in `scripts/hygiene/naming.whitelist` precisely because it's a lowercase-in-a-`.bni` linkage hook, NOT a public API. Same for `formatInt`/`formatUint`/`formatBool`/`formatInt64`. lang reaching for `formatFloat` abuses an internal print-builtin linkage hook as if it were a library function.
- **Fix direction**: give lang its own float→decimal formatter (it already carries its own *integer* formatters — `formatUint64`/`formatInt64` — for exactly this reason; the integer `Stringer`s do NOT borrow `bootstrap.formatInt`), or source float formatting from a proper public package. Honest caveat: a real float formatter (shortest-round-trip / `%g`-grade dtoa) is non-trivial — but that's an algorithm question, not a reason to keep borrowing bootstrap's helper; scope the formatter against what `Stringer` actually needs and decide. NOT caught by the tier-dependency hygiene check above (`pkg/bootstrap` IS bundled, so a tier check won't flag it, and it doesn't break the bundle) — this is a distinct "don't build a public API on deprecated / semi-private internals" concern.
- **Discovery**: 2026-06-10, release-prep for `bnc-0.0.8`, while removing lang's sibling `pkg/binate/buf` violation (binate `84818a77`). With `buf` gone, `bootstrap` is lang's remaining questionable dependency.

### Replace redundant `buf.CopyStr("<string-literal>")` calls with bare string literals — FILED 2026-06-10
- **What**: ~59 call sites across 7 `.bn` files (e.g. `pkg/binate/lexer/lexer.bn` token lits, `pkg/binate/ir/gen_init.bn` / `gen_iv_thunk.bn` / `gen_import.bn`, `cmd/bni/main.bn`) call `buf.CopyStr("<literal>")` to materialize a `@[]char`. The bare literal already does exactly this: a string literal has natural type `[N]readonly char`, and assigning it to a *writable* `@[]char` is a **literal-init allocate+copy** (`OP_RODATA_MSLICE_COPY`) — identical to what `CopyStr` produces. So `tok.Lit = buf.CopyStr("+=")` → `tok.Lit = "+="`. Same realization that fixed `lang.bn` (binate `84818a77`); the wrapper is pure redundancy.
- **Two non-negotiable caveats**:
  1. **Literal args ONLY.** `buf.CopyStr(<variable>)` copies a runtime slice and must stay — this is strictly `CopyStr("...")` with a string-literal argument.
  2. **Target must be (explicit) `@[]char`.** Valid where the literal lands in a writable managed-slice context (assignment / field-init / param / return typed `@[]char`). An INFERENCE site — `x := buf.CopyStr("...")` → `x := "..."` — would change `x`'s type from `@[]char` to the literal's default `@[]readonly char` (a refcount-exempt rodata VIEW, not a writable copy): a semantic change. Such sites need an explicit `@[]char` annotation or must be left.
- **BUILDER-compat precondition (CHECK FIRST)**: most sites live in `cmd/bnc`'s BUILDER-compiled tree (lexer, ir/gen_*). Confirm the current BUILDER (`bnc-0.0.7`) accepts `var x @[]char = "lit"` before touching those files; if it's a post-0.0.7 feature, the cleanup in BUILDER-compiled files must wait for a BUILDER bump (or be limited to non-BUILDER files). `lang.bn` was safe because it's stdlib-impl (compiled by gen1, not the BUILDER).
- **Optional**: add a `bnlint` / hygiene rule flagging `CopyStr("...literal...")` so the pattern doesn't creep back.
- **Discovery**: 2026-06-10, after the `lang.bn` buf fix (binate `84818a77`) showed the wrapper is redundant.

### Remove `findRuntime` auto-resolution; require an explicit `--runtime` — ✅ RESOLVED 2026-06-10 (binate `aa757361`)
- **What**: `cmd/bnc`'s `findRuntime` (`cmd/bnc/util.bn:163-188`) auto-resolves the libc C runtime path when `--runtime` is absent. Its search is fragile: phase 1 probes `{runtime,../runtime,../../runtime}/binate_runtime.c` relative to the input file's dir (only **3 levels**), phase 2 falls back to those suffixes **relative to CWD**, and on a miss it returns **empty** — at which point the link gate (`main.bn:214`, `len(runtimePath) > 0`) **silently drops the C runtime** (and rt/libc stubs) from the clang link, producing a cryptic downstream `undefined _bn_pkg__bootstrap__Write` / `undefined reference to main`. The preferred end-state (per user) is to **delete `findRuntime` entirely and require `--runtime`**.
- **Why**: this implicit, CWD-dependent resolution caused the Lane A CI conformance break — deeply-nested conformance cells, compiled from CI's workspace-root CWD (checkout one dir deeper, under `binate/`), resolved empty → runtime dropped → every deep `-comp*` cell failed to link. The immediate release-blocker fix made the conformance runners pass explicit `--runtime` (binate `a256c893`). With that, **no caller relies on auto-resolution** — `scripts/build-*.sh`, `e2e/*.sh`, `scripts/lib/build-compilers.sh` (gen1), and the `release-process.md` smoke tests all already pass `--runtime`.
- **Direction**: (1) Confirm no remaining caller depends on `findRuntime` (grep repo + scripts + any embedder). (2) Delete `findRuntime` + its call in `main.bn:85-88`. (3) When a host-runtime-linking compile is requested without `--runtime`, **error clearly** ("no host runtime: pass --runtime <binate_runtime.c>") instead of silently dropping it. Only error when a runtime is actually needed — baremetal targets use `appendTargetRuntime` (`target.bn`), and `-c`/VM/interpret paths don't link a host runtime.
- **Caveats**: `cmd/bnc` is BUILDER-compiled — deleting a function + adding an error stays BUILDER-`bnc-0.0.7`-compatible. Update any docs that mention runtime auto-resolution.
- **Discovery**: 2026-06-10, Lane A root-cause (`plan-bnc-0.0.8-release-blockers.md`): the depth-correlated CI failure (615 flat cells PASS, whole `matrix/` tree FAIL) traced to `findRuntime`'s CWD-relative fallback.
- **RESOLVED 2026-06-10 (binate `aa757361`; the `arm32_linux` runner --runtime fix `328582d7` is what surfaced it)**: `findRuntime` deleted; `main.bn` + `test.bn` error if `--runtime` is absent when linking, exempting `--emit-llvm` / `-c` and bare-metal (`suppressHostRuntime`). **The "Why" claim above that "no caller relies on auto-resolution" was WRONG** — ~13 in-tree LINKING sites silently depended on `findRuntime` and had to be given explicit `--runtime` (via `binate-paths --runtime`): `build-compilers.sh` gen2/native/interp, `build-{bnc,bni,bnas,bnlint}.sh` Stage-2 (both branches), the 5 native unittest runners, the 4 compiling perf runners, e2e repl/print-args/verify-ir, and the `arm32_linux` conformance+unit runners. Validated across every locally-runnable compile mode (conformance/unittest/perf comp+native, e2e, make-bundle, check-alloca) + the error/baremetal paths; arm32 confirmed on CI.

### Float-component multi-return mis-packed on the native backends — packed into INTEGER regs, not D0/XMM0 — native↔LLVM ABI divergence — ✅ RESOLVED 2026-06-10 (float64 `b5911fbe`; x64 field-per-register rework `47ebdbac`; verified on main — `(int,f64)`, `(f64,f64)` HFA, `(f32,f32)` HFA, and iface-dispatch `(f64,f64)` all pass on builder-comp + native aa64 + native x64-darwin). Residual aa64/x87 ≥3-float-component gaps tracked in the RESIDUAL GAPS bullet below.
- **STATUS 2026-06-09 — float64 RESOLVED & LANDED (binate `b5911fbe`).** Native pack + collect now assign each leaf to the next register of its CLASS: aa64 `emitReturn` (FP counter D0.. alongside GP X0..) + a shared `collectMultiReturnFields` routed from all four collect sites (direct/iface/funcval/call-indirect, which were four copies of the integer-only loop); x64 `emitMultiReturnPack` builds the full byte image then loads each eightbyte by class (new `multiReturnEightbyteIsSSE`, SysV two-eightbyte rule) with `collectMultiReturnTuple` the symmetric mirror. `conformance/683_cross_pkg_mr_float` ((int,float64)+(float64,float64) collected by native main from an LLVM pkg) fails pre-fix / passes post-fix on both native arches; green LLVM+VM. `gen-abi-matrix.py` gained an `f64` axis; full abi matrix green native aa64 + x64-darwin.
### Stale `native_x64` (ELF) iface-multi-return xfails — REMOVED (binate `10798d42`) — 2026-06-10 (Lane B)
- **What**: the 16 markers `conformance/matrix/abi/iface-multi-return{,-assign}/{int,u16}/{2,3,4,5}.xfail.builder-comp_native_x64-comp_native_x64` blamed "iface dispatch multi-return: native tuple-packing not yet implemented". That packing **IS implemented** (`pkg/binate/native/x64/x64_iface.bn` routes `OP_CALL_IFACE_METHOD` multi-returns through `collectMultiReturnTuple`), and the **identical-codegen** `builder-comp_native_x64_darwin` (Mach-O; same `pkg/binate/native/x64` backend, only object format differs) **PASSES all of these cells** (Lane B run 2026-06-10, and already noted in `03b80566`). ELF also passes the un-xfailed `multi-return` / `funcval-multi-return` / iface `f64` / `iface-param` / `iface-return` cells, so iface dispatch and multi-return both work there — these int/u16 markers were the lone stale holdouts.
- **Removed** on the x64-darwin evidence (user-authorized 2026-06-10). The ELF mode isn't locally runnable on macOS/arm64 (no `qemu-x86_64`), so **CI is the confirmation point**: it runs ELF natively on the x86-64 ubuntu runner and will exercise these 16 cells once Lane A's `-comp*` link break clears. Expected green; **treat any ELF failure as a real x64-ELF-specific bug to fix (not a re-xfail).** (arm32 iface-multi-return xfails left in place — different, less-complete backend.)

- **REMAINING — x64 float32 cross-package native↔LLVM ABI mismatch (tracked, NOT a regression):** an adversarial review of the float64 commit found that a sub-8-byte float (float32) multi-return component COALESCES into a shared eightbyte on SysV-AMD64 — `(float32,float32)` → one SSE eightbyte (XMM0), `(float32,int32)` → one INTEGER eightbyte (RAX). The native x64 pack/collect (`multiReturnEightbyteIsSSE`-driven, self-consistent) still disagree with LLVM's actual x64 float32 ABI, so cross-package float32 reads garbage / faults. aa64 is correct (each float gets its own D register). `conformance/684_cross_pkg_mr_f32` pins this **xfailed on native x64** (passes aa64/LLVM/VM). float32 multi-return was always broken (the integer-only path); this surfaced it. **Fix direction:** dump LLVM's actual register usage for an x64 float32 multi-return (the `F32F32`/`F32I32` `.ll`/asm), then align the native x64 pack/collect — the per-eightbyte `emitMultiReturnPack` is the groundwork. The aa64 per-field scheme is already correct, so this is x64-only.
- **CORRECTED ROOT CAUSE — empirically dumped 2026-06-10 (the bullet above had it BACKWARDS), and the bug is BROADER than float32:** our LLVM backend emits LITERAL struct return types (`{float,float}`, `{float,i32}`, `{i16,i16,i16}`, `{i32,i32}`, …) and LLVM lowers a first-class IR aggregate return **purely FIELD-PER-REGISTER, with NO SysV eightbyte coalescing** — confirmed by lowering hand-written `.ll` with `clang -S --target=x86_64-*` (Darwin == Linux): `{float,float}`→XMM0,**XMM1**; `{float,i32}`→XMM0,**EAX**; `{i16,i16,i16}`→AX,DX,**CX**; `{i32,i32}`→EAX,**EDX**; `{i64,double}`→RAX,XMM0. So the native x64 **eightbyte-coalescing** model (`multiReturnEightbyteIsSSE`, packs `(i32,i32)`/`(f32,f32)` into ONE register) is the WRONG model for native↔LLVM agreement: it only COINCIDES with LLVM when every field is a full 8 bytes (`(int,f64)`/`(f64,f64)` — why 683 is green). It DIVERGES for **every sub-8-byte field** (`(f32,f32)`, `(f32,i32)`, `(u16,u16)`, `(i32,i32)`, …) crossing the native↔LLVM (hybrid: native main + LLVM dep) boundary → silent garbage. **The abi matrix never caught this because its multi-return cells are SAME-MODULE** (`package "main"`, callee inline → native↔native self-consistent), so only the cross-package 683/684 exercise the boundary. **aa64 is already correct because it does FIELD-PER-REGISTER** (each leaf → next reg of its class), matching LLVM (684 green on aa64). **FIX = replace the x64 eightbyte-coalescing pack/collect with FIELD-PER-REGISTER-BY-CLASS** (int leaves → RAX,RDX,RCX,… ; float leaves → XMM0,XMM1,… ; store/load at the field's offset), mirroring aa64 + LLVM's literal-struct lowering. NOT a float32 patch and NOT codegen coercion (emitting `<2 x float>`/`i64` would fix x64 but BREAK aa64, since one target-independent IR type can't express both targets' ABIs — clang lowers `<2 x float>` to V0-packed on aa64, which aa64's per-field collect would then mis-read). Need to confirm LLVM's exact GP/FP return-reg sequence + the >N-register sret threshold before implementing. **Surfaced to user as a major finding + design reversal (the b5911fbe eightbyte choice) — user APPROVED the field-per-register rework (2026-06-10).**
- **EXACT LLVM x64 first-class-struct return CC (empirically probed via `clang -S` on hand-written `.ll` with CALLERS that read each field — the definitive register map):** GP-class leaves → **RAX, RDX, RCX** (3 regs; `{i64,i64,i64}` is IN-REGISTER with field 2 in RCX); 4+ GP-words → **sret**. FP-class leaves → **XMM0, XMM1** (2 regs); a 3rd/4th float64 spills to **x87 ST0/ST1** (NOT sret, NOT XMM — `{double,double,double}`/`{...,double}` read the field via `fstpl`); 5+ floats → sret. INTEGER and FP counters are INDEPENDENT and there is **no eightbyte coalescing**. So x64's sret threshold is **register-count-based** (gpWords>3 OR fpCount>2-ish), NOT the 16-byte rule — `{i64,i64,i64}` is 24 bytes yet in-register.
- **BOUNDED FIX PLAN (delivers the greenlit scope + fixes the whole sub-8-byte class):** x64 `emitMultiReturnPack` + `collectMultiReturnTuple` → field-per-register-by-class: a non-float field's words → RAX,RDX,RCX (retGp); a float-scalar field → XMM0,XMM1 (retFp); each stored/loaded at its field offset (mirror of aa64 `collectMultiReturnFields`). Delete `multiReturnEightbyteIsSSE`. x64 sret decision (currently the shared 16-byte `CallReturnsBigMultiReturn`) → an **x64-specific** register-count rule (gpWords>3 OR fpCount>2 → sret), so `{i64,i64,i64}` stays in-register matching LLVM while the same-module abi-matrix (int/3 etc.) stays green (native↔native self-consistent). Keep aa64 on its 16-byte rule (unchanged). Un-xfail 684; add cross-package coverage for `(u16,u16)`/`(i32,i32)`. Verify 683/684 + abi matrix green on aa64 + x64-darwin.
- **LANDED — binate `47ebdbac` (2026-06-10).** x64 multi-return pack/collect are now field-per-register-by-class (RAX,RDX,RCX / XMM0,XMM1 at each field offset); the multi-return sret threshold is target-aware (`CallConv.MultiReturnTupleNeedsSret`, exported): SysV register-count (>3 GP-words / >2 FP-fields), AAPCS64 unchanged (SizeOf>16). `multiReturnEightbyteIsSSE` deleted; the x64 funcval sret classifier `isBigMultiReturn_x64` (from `f0747762`) was reconciled onto the same shared threshold (same-area concurrent commit — its size>16 rule disagreed for `(i64,i64,i64)` funcvals). Conformance 684 un-xfailed both x64 modes; new 693 (`(i32,i32)`,`(u16,u16,u16)`,`(i32,i32,i32)`) added. Verified: 683/684/693 + full abi MR matrix + `funcval-big-multi-return-args` green on aa64 + x64-darwin; unit + hygiene green.
- **SIDE-EFFECT — 526 (`strconv_parse_cross_pkg`, managed-iface multi-return) now PASSES on x64, still FAILS on aa64.** My fix resolved 526 on x64-darwin (its `(int,@errors.Error)` = 3 GP-word multi-return was mis-collected by the eightbyte scheme); `0d29a4b5`'s `builder-comp_native_x64{,_darwin}` xfails for 526 are now STALE → **REMOVED (binate `f895848b`, 526 un-xfailed + verified green on x64-darwin)**. 526 still fails on aa64 (a separate aa64-specific managed-iface-multi-return bug, NOT fixed by this x64-only change) → keep the aa64 xfail; likely related to residual gap (2) below or an iface-value-in-multi-return refcount issue. Track as an aa64 follow-up.
- **RESIDUAL GAPS (loud follow-ups, NOT silently deferred):** (1) **x87 cross-package** — a 3rd/4th float64 in a multi-return crossing native↔LLVM (LLVM uses ST0/ST1; native srets it) stays divergent; rare, untested, pre-existing; native can't easily emit x87 returns → leave as sret + track. (2) **aa64 has the SAME latent threshold bug** — aa64 LLVM returns an HFA `{double,double,double}` (24B) in D0–D3 (in-register, ≤4-element HFA) but aa64 native srets it (>16B `CallReturnsBigMultiReturn`); untested cross-package (683 is 2-field), so aa64's "already correct" holds only for ≤2-element float / ≤16B shapes. Track as an aa64 follow-up. (3) **aggregate FIELDS inside a multi-return** — LLVM flattens; keep current behavior / sret, don't regress.
- **Symptom (direction)**: a multi-return tuple with a FLOAT component (`(int, f64)`, `(f64, f64)`) — the native callee pack (aa64 `aarch64_dispatch.bn:354-385` OP_RETURN multi-return loop; x64 `emitMultiReturnPack` `x64_return.bn:159-201`) has only two arms (aggregate / else-scalar→X-or-RAX/RDX), with NO `IsFloatScalarTyp` branch and no HFA/SSE eightbyte classification (only the LONE-single-scalar-float early return is float-aware). So a float field is packed into an INTEGER register, and the native caller collect reads it from an integer register — native↔native self-consistent, but DIVERGENT from AAPCS64 / SysV-AMD64 + LLVM, which return a float eightbyte in D0/XMM0 (or an SSE-classified aggregate eightbyte in an FP reg). cmd/bnc compiles only the main module natively and routes cross-package callees through LLVM/clang, so a float-component multi-return crossing the native↔LLVM boundary (e.g. an impl method or multi-return func defined in a non-main, LLVM-compiled package) reads the float field from the WRONG register class → silent garbage. Now reachable for iface dispatch too (post-SEAM); still ZERO coverage (abi matrix is int/u16 only).
- **Severity**: MAJOR — silent wrong value at the native↔LLVM ABI boundary on a type-valid shape; narrow trigger (float-component multi-return crossing the boundary) but real and untested.
- **Fix direction**: add `IsFloatScalarTyp` handling (and HFA/SSE eightbyte classification) to the native multi-return callee pack + caller collect on both arches, matching AAPCS64 / SysV-AMD64 + the LLVM legalization. Extend `gen-abi-matrix.py`'s type axis with `f64` for multi-return / iface-multi-return / funcval-multi-return — decisive shapes `(f64,f64)` (HFA on aa64) and `(int,f64)` (mixed INTEGER+SSE eightbytes on x64).
- **Discovery**: 2026-06-08, adversarial review of plan-cr2-3 — the iface-classifier (`cc2ddcc4`) made a float-component iface multi-return reachable; the underlying native multi-return pack was never float-aware. Filed (not fixed) per user decision.

### x64 native backend drops a global address (`&G`) used as an RVALUE — `return &G` emits an empty body → SIGBUS — ✅ RESOLVED (binate `0c707e1f`, 2026-06-08)
- **STATUS 2026-06-08 — RESOLVED & LANDED.** Mirrored aa64: added `emitValOperand(a, pkgName, m, ins)` to `x64_regmap.bn` (`isGlobalRef` → `emitGlobalAddr` into a scratch reg, else `getOperand`), threaded `pkgName` into `emitReturn`/`emitSretReturn`/`emitMultiReturnPack`/`emitCompare`/`emitCallIndirect`/`emitCallFuncValue`/`emitCallIfaceMethod`, and routed every x64 value-operand fetch through it — scalar + multi-return return values, comparison operands, store value, call/dispatch args, and the `OP_BIT_CAST` source. `conformance/551,573` flip green on x64-darwin (full suite 1166 passed / 4 pre-existing-unrelated failures, no regressions); aa64/LLVM/VM unchanged (x64-only); new `x64_global_ref_test.bn` pins `emitReturn`/`emitValOperand`/`emitCompare` materializing an `IsGlobalRef` via a RIP-relative LEA.
- **Symptom (historical)**: `conformance/551_addr_of_global_scalar` and `573_addr_of_two_globals_one_instr` crash (SIGBUS, exit 138) on `builder-comp_native_x64_darwin`. Disassembly: `func getG() *int { return &G }` compiles to an EMPTY body (prologue/epilogue only, RAX never set) — `return &G` emits nothing — so the caller dereferences garbage. Green on native aa64 (also Mach-O) and on LLVM/VM, so **NOT Mach-O-specific** despite the surface framing: it is an x64-codegen gap exposed only because x64-darwin is the one runnable x64 mode on the dev host (x64-linux/ELF needs qemu; likely wrong there too at runtime, unverified).
- **Root cause (CONFIRMED)**: the IR emits a global reference as an `IsGlobalRef` pseudo-Instr with ID -1 (no SSA register). x64's value-operand sites fetch operands with the bare `getOperand(a, rm, id)` (`pkg/binate/native/x64/x64_regmap.bn`), which receives only an `id` (no `ins`) and so cannot test `isGlobalRef` — for ID -1 it returns -1 and the site DROPS the operand: `emitReturn` scalar arm (`x64_return.bn` — `getOperand(ins.Args[0].ID)` → RAX never set), `emitBinop`/cmp (`x64_ops.bn` — `getOperand(Args[i].ID)` → `lhs<0||rhs<0` → not emitted, e.g. `&G==&H`), and the call-arg / dispatch-arg sites. aa64 handles this via `emitValOperand(a, pkgName, m, ins)` (`aarch64_regmap.bn`): `if isGlobalRef(ins) { emitGlobalAddr(...) } else getOperand(ins.ID)`, used at all 11 of its value-operand sites. **x64 has NO `emitValOperand`**, and its `emitReturn`/`emitBinop` emitters don't even thread `pkgName` (which `emitGlobalAddr` needs). x64 handles `isGlobalRef` only piecemeal at address-position sites (load/store/refcount/dispatch-data in `x64_emit.bn`/`x64_managed.bn`), never the generic value positions.
- **Severity**: MAJOR — silent wrong-code / crash on an idiomatic, common pattern (`return &global`, `f(&global)`, `&a == &b`) in the x64 native backend. Confined to x64-native (aa64/LLVM/VM are correct). x64-native is still being built out (Phase 3), so this is a completeness gap, not a regression of a once-working path.
- **Tests**: `conformance/551_addr_of_global_scalar` (8 rvalue positions), `573_addr_of_two_globals_one_instr` (multi-return + comparison) — currently UNxfailed (fail on x64-darwin, pass elsewhere).
- **Discovery**: 2026-06-08, plan-cr2-3 follow-up — investigating the x64-darwin-only 551/573 failures per user direction; built bnc, compiled 551 `--target x86_64-darwin`, ran under Rosetta (SIGBUS), disassembled (`getG` empty; only 4 of ~8 `&G/&H` LEAs present).
- **Fix**: mirror aa64 — add `emitValOperand(a, pkgName, m, ins)` to x64 (`isGlobalRef` → `emitGlobalAddr` into a scratch reg, else `getOperand`), thread `pkgName` into `emitReturn`/`emitBinop`/cmp (+ their `x64_dispatch.bn` callers), and route every value-operand fetch (return value, binop lhs/rhs, cmp operands, call/dispatch args) through it. Breadth fix across `x64_{return,ops,call,call_indirect,iface,dispatch,regmap}.bn` + signature changes. Pin with 551/573 flipping green on x64-darwin + a unit test that `emitReturn`/`emitBinop` materialize an `IsGlobalRef` operand.

### Unary minus on a SUB-WORD int (`-uint8`/`-int16`/…) is mis-typed in IR-gen — FIXED + LANDED 2026-06-08 (binate `fce07ccd`, plan-cr2-1 Defect 9; the exact analog of the fixed `~` `bitnot-result-type` bug)
- **FIX (landed `fce07ccd`)**: `genUnary` MINUS arm now types OP_NEG at the operand's exact integer width (any concrete `TYP_INT`, or the checker-resolved type for an untyped literal), not just float/Width==64.  The native/VM sub-word re-narrow (`68616b20`) was already landed, so facet B is correct on every backend and facet A compiles.  Pinned by `conformance/regressions/unary-minus-subword` + a gen_expr OP_NEG sub-word width unit test, plus the exhaustive `scalar-diff/neg/{8,16,32,64}/{signed,unsigned}` differential family (binate `d64b76d0`, green on every backend).
- **Symptom (two facets, one root, like `bitnot-result-type`)**:
  - **A (invalid IR / compile error)**: `-x` for any sub-word int (`uint/int 8/16/32`) emits `sub i64 0, %x` with a hardcoded i64 zero while `%x` is i8/i16/i32 → clang rejects it (`'%x' defined with type 'i8' but expected 'i64'`). Unary minus simply does not compile for sub-word ints on the LLVM backend (all `comp`/`comp-comp`/`comp-comp-comp` + arm32 LLVM modes).
  - **B (silent wrong value)**: on the VM and native aa64/x64, `-x` computes at host width and the result keeps dirty upper bits / is the host-width negation, not the sub-word value — e.g. `-1` as `uint8` reads as host `-1`, not `255`. Silent.
- **Root cause (CONFIRMED)**: `pkg/binate/ir/gen_expr.bn:223-241` (`genUnary`, MINUS arm) sets `negTyp` defaulting to host-word `types.TypInt()` and only overrides it for floats or `Width == 64` (the int64-preserving path). A sub-word operand matches NEITHER, so `EmitUnary(OP_NEG, arg, negTyp)` carries i64 while `arg` is i8/16/32. This is the SAME mistake `~` had — and the fixed `~` entry (`bitnot-result-type`, binate `42ad4fa0`) even says its fix "mirrors `OP_NEG`," not realizing OP_NEG had the identical latent gap for sub-word.
- **Fix direction**: type the `OP_NEG` result as the operand's resolved (sub-word) type — accept any concrete `TYP_INT` width from the checker resolution / operand type, not just `Width == 64`, mirroring the `~` fix at `gen_expr.bn:247`. A one-site IR-gen change. Once it lands, the native/VM `OP_NEG` sub-word re-narrow (already landed, binate `68616b20`, plan-cr2-3 Defect 1) makes B correct on every backend, and A compiles.
- **Owner**: IR/frontend (Plan 1 territory — `pkg/binate/ir`). NOT owned by any CR2 plan as written; surfaced during plan-cr2-3 Defect 1. plan-cr2-3 Defect 1 explicitly did NOT touch `gen_expr.bn` (disjointness).
- **Severity**: MAJOR — a basic operation (negate a sized int) is broken: loud (compile error) on LLVM, silent (wrong value) on VM + native.
- **Test**: a `conformance/regressions/unary-minus-subword` cell (xfailed every mode until the IR fix) pins it. The full `scalar-diff/neg/{8,16,32}/{signed,unsigned}` generator family (reverted out of the Defect-1 commit) should be re-added when the fix lands — it goes green across all backends once IR-gen types OP_NEG correctly and Defect 1's narrow applies. (Defect 1's OP_NEG narrow is itself already pinned by the new aarch64/x64 `emitUnop` narrow unit tests, which construct a correctly-typed sub-word OP_NEG directly and so don't depend on this fix.)
- **Discovery**: 2026-06-08, adding `neg` cells to the scalar-diff differential harness as Defect-1 (sub-word unary narrow) coverage; the cells compile-errored on LLVM, exposing the upstream IR mis-typing.

### A named fixed-array type (`type Row [3]int`) is unparseable — `type X [` is greedily read as generic type-params — CONFIRMED 2026-06-07
- **Symptom**: `type Row [3]int` → parse error `expected IDENT, got INT` / `expected type`. After `type Row [`, the parser commits to the TypeParams form (`type Row [T U] …`), which requires an identifier, so a fixed-array size (an integer) is rejected. You cannot name a fixed-array type at all; `type Buf @[]int` (managed-slice) and `type S struct{…}` parse fine — only the `[N]T` array form collides with the generic-params `[ident ident]` syntax.
- **Root cause**: the `[`-after-type-name disambiguation in the parser (grammar `TypeDecl`/`TypeSpec`, the `[` → ArrayType-vs-TypeParams ambiguity noted in grammar.ebnf ~158-164). The parser must look past `[` for an integer/expression (ArrayType) vs two identifiers (TypeParams).
- **Severity**: MAJOR — a whole type-construction form is unavailable; loud (parse error), workaround is to use the structural type inline.
- **Test**: the `conformance/matrix/globals` `named-array` cell is omitted for this reason; a `conformance/regressions/named-array-type` point-test would pin it.
- **Discovery**: 2026-06-07, building the Code-Red-2 globals matrix.

### `len()` on a named-managed-slice (`type Buf @[]int; len(buf)`) is rejected by the checker — named wrapper not peeled — CONFIRMED 2026-06-07
- **Symptom**: `type Buf @[]int; var b Buf; len(b)` → checker error `len argument must be slice or array`. The `len` builtin's argument check tests the raw `Kind` (`TYP_NAMED`), never peeling to the underlying managed-slice. A wrapper-transparency miss (Code-Red-2 Class B / Invariant A) on the `len` builtin specifically.
- **Severity**: MAJOR — `len` unusable on any named-slice/array type; loud (compile error).
- **Root cause direction**: the `len`-arg type check (checker / builtin resolution) must peel `TYP_NAMED` (and `TYP_READONLY`) before testing slice/array-ness. Likely the same fix shape as plan-cr2-1's other peels.
- **Test**: `conformance/regressions/len-named-managed-slice` (xfailed all modes, binate `a77591e0`) pins the `len()` rejection. The `conformance/matrix/globals/noinit/named-managed-slice` cell reads `0` (compile-only) to isolate the codegen zero-token defect.
- **Discovery**: 2026-06-07, building the Code-Red-2 globals matrix.

### Compound shift-assign (`<<=` / `>>=`) bypasses the overshift guard — FIXED + LANDED (binate `fa265629`)
- **Symptom**: `var y uint32 = 1; y <<= 40; println(cast(int, y))` printed `256` (= `1 << (40 & 31)`) on `builder-comp`, not the spec's `0` (count 40 ≥ width 32). The expression form `y = y << 40` correctly gives `0` (fixed at the CRITICAL "shift by ≥ bit width" entry, binate `32fde83d`). Native aa64 gave the correct `0` — so this was an LLVM-path divergence. `uint8 x <<= 9` happened to read `0` (the `1<<9=512` result is narrowed to `uint8` → 0, masking the bug); only a width where the masked count stays in range (`uint32 <<= 40` → `<<8`) exposed it.
- **Root cause (path-parity)**: the overshift guard (`emitGuardedShift`) was applied on the expression-shift path but NOT on the compound-assign path — `emitCompoundBinop` (`pkg/binate/ir/gen_control.bn`) lowered `<<=`/`>>=` without routing through `emitGuardedShift`. Classic Code-Red-2 path-parity gap: a guard added to one of N sibling lowerings (expr-shift) was never mirrored into the others (compound-assign). See `plan-code-red-2.md`.
- **Fix (landed, binate `fa265629`)**: route compound `OP_SHL`/`OP_SHR` through `emitGuardedShift` in `emitCompoundBinop`, mirroring `genBinaryExpr`, keeping the in-range-const fast path. **Companion fix in the same commit**: `emitCompoundBinop` now width-coerces both operands to the lvalue type internally (only the IDENT arm did so before), so a sub-word element/field/deref compound assign no longer keeps an untyped-int count/operand at int64 and emits width-mismatched IR — latent for sub-word non-IDENT compound assigns generally (a `uint32` `a[0] += 5` would have emitted `add i32, i64`), previously unexercised.
- **Severity**: MAJOR — was silent wrong-code, but narrow (a compile-time shift count ≥ width in a compound-assign).  Plan-1 defect (7) in `plan-cr2-1-frontend.md`.
- **Test**: `conformance/659_compound_shift_overshift` — `<<=`/`>>=` overshift across variable / array-elem / slice-elem / nested-array-elem / field / deref lvalues at uint32 & int32, runtime + out-of-range-const counts, self-checking (target-stable 0/1).  Green on builder-comp{,-comp,-comp-comp}, builder-comp-int{,-int}, -comp-comp-int, native aa64.  (Exhaustive `op × lvalue-form` compound-assign coverage — incl. sub-word non-shift arith that the companion width fix also repairs — is the `conformance/matrix/operator` follow-up, §3.3.)
- **Discovery**: 2026-06-07, Code-Red-2 probing of path-parity predictions (the operator pattern).

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

### Cyclic non-struct named-type definitions (`type A B; type B A`, `type A A`) accepted with no diagnostic → every `Underlying`-walking helper hangs/crashes the compiler — ✅ RESOLVED (landed binate `68a62f8c`, 2026-06-09)
- **Resolution**: `collectTypeDecl` now rejects the cyclic definition (`cyclic type definition involving X`) and breaks the cycle (`Underlying = nil`), so NO `Underlying`-walker — `IsInteger`/`IsFloat`/`IsBool`/`NeedsDestruction`/`AssignableTo`/`comparabilityKind` — ever encounters a cycle. The four operand-comparability predicates additionally carry a bounded named-peel (`peelNamedBounded`) as defense-in-depth; `NeedsDestruction`/`AssignableTo` are protected transitively (the cycle can't exist) rather than independently bounded. See the CR-2 Plan-1 review entry above for coverage. (Original report retained below for context.)
- **Symptom**: a cyclic named-distinct-type definition that is NOT struct-field-mediated — `type A B` + `type B A`, or the self-cycle `type A A` — is accepted by the checker with ZERO errors. The cyclic `TYP_NAMED.Underlying` chain then makes every helper that walks `Underlying` unsafe: `IsInteger`/`IsFloat`/`IsBool`/`NeedsDestruction`/`AssignableTo` recurse unboundedly → SIGSEGV; the new `comparabilityKind` (types_query.bn, loop-based) → infinite hang. Any expression touching such a type (e.g. `var a A; var b A; a == b`, or merely `AssignableTo(A, A)`) takes down the compiler.
- **Root cause**: no cycle detection for non-struct named-type `Underlying` chains. `FindFreshCycles` (check_pending.bn) catches only SIZED-use (struct-field) cycles; const-cycle detection exists too; bare `type A B; type B A` is unguarded.
- **Severity**: MAJOR — compiler DoS (hang/crash) on invalid source that should be rejected with a diagnostic; NOT silent wrong-code. PRE-EXISTING (the old `==` path already SIGSEGV'd here via `AssignableTo`); surfaced while adversarially reviewing the `==`-comparability change (binate `e0f40c06`), which converts the crash into a hang on its one path but neither introduces nor worsens the root defect.
- **Fix direction**: detect named-type underlying cycles at definition time (in `collectTypeDecl`, mirroring struct-field-cycle and const-cycle detection) and emit a `type cycle: A -> B -> A` diagnostic so the cyclic type never reaches IR-gen or the predicates. Defense-in-depth: a shared visited/depth guard for the `Underlying`-walking helpers. Do NOT band-aid `comparabilityKind` alone — that leaves IsInteger/AssignableTo crashing.
- **Test**: add WITH the fix — a checker test for `type A B; type B A` and `type A A` expecting a cycle diagnostic. (Cannot add now as an xfail: the defect is a hang/crash, so the test would hang/crash the suite rather than fail cleanly.)
- **Discovery**: 2026-06-07, adversarial review of the `==`-comparability change.

### ~~`present(...)` is interface-value-only~~ — DONE 2026-06-08 (binate `29c9dc47`, conformance `667`): extended to func values (vtable field 0), pointers (non-null), slices (`len > 0`); value types rejected. Prerequisite length-0 ⟹ no-backing invariant landed (`71ff7489`, conformance `666`). Original investigation note kept below for context.
- **Current state**: the checker (`pkg/binate/types/check_builtin.bn:78-92`) accepts `present(x)` ONLY when `x` is a raw or managed interface value (`TYP_INTERFACE_VALUE` / `TYP_INTERFACE_VALUE_MANAGED`); everything else is rejected with "present argument must be an interface value". Lowering (`pkg/binate/ir/ir_ops.bn` `EmitIfacePresent`) extracts the vtable word (field 1) and compares it non-null (honest about typed-nil: boxing a nil `*T` still fills the vtable, so `present` is true).
- **Why this matters**: `present()` is the language's *sanctioned* "does this hold something / is it set" test for types where a direct `== nil` is a footgun or outright disallowed. We deliberately disallow `slice == nil` (a nil slice acts like an empty slice but is not the same) and steer interface values to `present(iv)` rather than `iv == nil` (typed-nil). For that story to be complete, `present()` must cover every type that has a meaningful "set / unset" (nullable) notion — otherwise disallowing `== nil` leaves users with no sanctioned test.
- **Investigate — which types are "sensible", and what does `present` mean for each**:
  - Interface values (`*Iface`/`@Iface`) — DONE (vtable non-null).
  - Managed pointers (`@T`) — if `@T` is nillable, `present(@T)` is the natural replacement for `@T == nil` (test the pointer word non-null). Confirm nillability, then define.
  - Func values (`*func`/`@func`) — `present(fv)` = code-pointer non-null (is the func value set?); replaces `fv == nil`. Ties into the `==`-on-func-values disallow above.
  - Raw pointers (`*T`) — already comparable to nil via `==` (spec: address equality). Decide whether `present(*T)` is ALSO accepted for uniformity, or left out as redundant.
  - Slices (`*[]T`/`@[]T`) — the footgun case. `present(slice)` testing data-ptr-non-null would re-introduce the exact nil-vs-empty footgun that disallowing `slice == nil` exists to avoid. Likely EXCLUDE (or define very deliberately) — specify explicitly either way.
  - Scalars / value structs / arrays — no presence notion; keep rejecting.
- **Then implement**: extend the checker rule (per-type accept/reject), add lowerings (each is the same "extract the relevant word, compare to null" shape as `EmitIfacePresent`, so every backend lowers it for free), and keep a clear diagnostic for the rejected types.
- **Tests (with the work)**: checker accept/reject per type; a runtime conformance cell per accepted type (set vs unset).
- **Relation to the `==` spec gap (above)**: the decision to DISALLOW `==`/`!=` on aggregates (incl. interface values) leans on `present()` covering all the nullability tests — land this so disallowing `== nil` does not leave a gap. NOTE: `present()` answers "is there anything here", NOT sentinel identity — `err == io.EOF` ("is this THE EOF error") is a separate, still-open question (see io.EOF entry).
- **Requested**: 2026-06-07, by user.

### ~~`pkg/std/io`: add `io.EOF` sentinel~~ — LANDED (binate `4fdbd1f9`, plain non-readonly var) — two NON-blocking refinements remain
- **LANDED**: `var EOF @errors.Error` declared in `io.bni` (extern), defined in `impls/.../io/io.bn` as `errors.New("EOF")`; the synthetic `pkg/std/io.__init` constructs it before main; a consumer reads `io.EOF` + `.Error()` correctly. Plain (non-readonly) var, matching Go's `io.EOF`. (Needed the iface-value global-init codegen fix, landed `91ef4fc4`.)
- **Refinement, NOT a blocker — readonly**: making `io.EOF` immutable to consumers (`readonly`) is wanted eventually but does NOT gate the sentinel; it's a plain reassignable var for now (as Go's is). Gated on the readonly-for-managed-values CRITICAL.
- **Refinement — ergonomic detection: RESOLVED 2026-06-08.** `err == io.EOF` is (correctly) NOT the mechanism — `==` on interface values is disallowed. Detection is `io.IsEOF(err)` = `errors.Is(err, io.EOF)` (binate `5282563b`), built on `errors.Is` (`1f87b905`) walking the `Unwrap()` chain via the `same` reference-identity builtin (`e7c1b7fc`). Robust to wrapping; identity (not message) is the test.

### float32 ops (arithmetic, negate, comparison) were computed in double precision on the f32 bit pattern — FIXED/LANDED 2026-06-06 (binate df7a5ec1, 12a24e74, fc11d862)
The VM and both native backends computed float32 `+ - * /`, unary negate, and all six comparisons as float64 on the raw f32 bit pattern (the low-4-byte f32 bits reinterpreted as a double), producing garbage — a silent miscompile (LLVM was always correct). All three now compute at single precision:
- **arithmetic** (df7a5ec1): native single-precision ops (aa64 FADD/FSUB/FMUL/FDIV `_s` ty=00 encoders; x64 ADDSS/SUBSS/MULSS/DIVSS) and VM `BC_F32ADD/SUB/MUL/DIV`.
- **negate** (12a24e74): aa64 FNEG `_s`; VM `BC_F32NEG` (sign-bit XOR); x64 already XOR'd the f32 sign bit.
- **comparison** (fc11d862): aa64 FCMP `_s`; x64 UCOMISS; VM `BC_F32EQ/NE/LT/LE/GT/GE` — NaN-unordered semantics preserved via the shared condition logic.
- **Tests**: `conformance/635_float32_arith` (4 binops + negate + bit-exact `1.0f/3.0f` rounding) and `639_float32_compare` (negative operand exposes the order-flip + runtime NaN unordered/ordered checks); green on every lane.  Golden-encoding tests for all new native encoders.  The `builder-comp_native_x64` x86_64-linux 635 marker is retained (unverifiable without qemu-user on this arm64 host; same x64 codegen as the passing x64_darwin lane).
- **NOT broken / unchanged**: float32 CONST materialization (539), float32 RETURN bits (636), and float32 CASTS (`BC_F32TOSI` / `emitFloatCast` carry width) were already correct.  Discovered by the Plan-4 + float32-arithmetic adversarial reviews.

### VM drops a returned aggregate / managed-slice element of a local (`return container[i]`) — wrong-result, VM-only — FIXED + LANDED 2026-06-06 (binate `61488b48`)
- **Symptom**: under `builder-comp-int` (bytecode VM), a function that returns an
  aggregate element loaded directly from a local container — e.g.
  `func f() @[]char { var s @[]@[]char = @[]@[]char{"hello","world"}; return s[0] }`
  — returns an EMPTY/garbage value (the managed-slice element comes back empty; a
  struct array element reads garbage). The compiled backends (LLVM + native) are
  correct; only the VM is wrong.
- **Confirmed**: `conformance/regressions/return-aggregate-element-of-local` —
  expected `hello\n1\n2\n3`, VM prints an EMPTY first line then `1 2 3`. PASSES in
  `builder-comp` and `builder-comp-comp` (922/0), FAILS only in `builder-comp-int`
  (untracked — NOT xfail'd, so the default VM conformance lane is live-red on it).
- **This is the VM analog of the native aggregate-`OP_LOAD` aliasing bug** fixed in
  binate `1285683e` (PlanFrame/emitLoad now reserve an own data region so the load
  owns its bytes instead of aliasing the source, which gets RefDec'd/freed at
  function cleanup BEFORE the copy into the sret/result). That entry asserts "LLVM
  and the VM were always correct" — STALE: the VM mishandles this exact case.
- **Root cause (confirmed)**: `pkg/binate/vm/lower_memory.bn` `lowerLoad` emitted
  `BC_MOV` for a multi-word (aggregate) load — the loaded register just ALIASED the
  source pointer ("the consumer handles the bytes"). For `return container[i]` that
  alias pointed into the local's backing, which the function's cleanup RefDec'd
  (freed/zeroed) before the sret copy ran, so the return read freed memory.
- **Fix (binate `30f21816`, work-3)**: the VM frame planner (`lower_func.bn`) now
  reserves an own region for every aggregate `OP_LOAD` (`isAggregateLoadTyp`,
  matching native `common.IsAggregateTyp`); a new `BC_LOAD_AGGREGATE` bytecode copies
  the loaded bytes into that region and points the result there, so the load owns its
  bytes — mirroring the LLVM/native aggregate load (and native fix `1285683e`).
- **Severity**: MAJOR — silent wrong-result (data loss) on a routine
  `return container[i]` under the VM; VM-only (the compile path is correct).
- **Discovery**: 2026-06-06, regression-testing the `genExprOrFuncRef` CurBlock fix
  (binate `47d05c81`); unrelated to that fix (the test has no function-value types —
  same IR passes natively, failed only in the VM).
- **Tests**: `conformance/regressions/return-aggregate-element-of-local` now passes
  `builder-comp-int` (full lane 895/0, was 894/1); `TestAggregateElementLoadMaterializesCopy`
  (`lower_memory_test.bn`) pins aggregate `OP_LOAD` → `BC_LOAD_AGGREGATE`.

### Float `!=` is ORDERED (`NaN != NaN` is false) — diverges from IEEE/Go/C; `==` and `!=` not complementary for NaN — FIXED 2026-06-06 (binate `8f78575f`)
- **Symptom**: `var n float64 = NaN; n != n` evaluates to **false** (and `n == n`
  is also false), so the two are not complements. Every other language (Go, C,
  Rust, IEEE 754) makes `!=` *unordered*: `NaN != NaN` is **true**, and
  `(a == b) == !(a != b)` always holds. Any Binate code using the idiomatic
  `x != x` NaN test, or doing NaN-aware compare/sort/dedup, silently
  mis-behaves.
- **Root cause (deliberate, now reversed by user, 2026-06-06)**: the float
  compare emitters force ordered semantics for `!=`. LLVM `emit_ops.bn` uses
  `one` (ordered) instead of `une`; x64 `x64_float.bn` AND's `SETNE` with
  `SETNP` (NaN-gate); aarch64 `aarch64_float.bn` adds a `Csel … COND_VC` to
  zero the unordered result. `==` (`oeq`) and the four relationals (`olt`/`ole`/
  `ogt`/`oge`) are already correct; only `!=` is wrong.
- **Fix** (Phase 0 of `plan-std-math.md`): `one`→`une` (LLVM); `SETNE OR SETP`
  (x64); delete the aarch64 `OP_NE` Csel block; VM is fixed transitively
  (recompile) + a test. `oeq`/`une` are exact complements, restoring
  complementarity. Pin with a conformance cell (NaN compares + complementarity)
  across all default + native alt-modes; update the misleading code comments and
  add a float-comparison spec entry to `claude-notes.md`.
- **Discovered**: 2026-06-06 while scoping `pkg/std/math` (IsNaN needs correct
  NaN semantics). Prerequisite for the math package; lands standalone first.

### Native widening int casts don't sign/zero-extend from the SOURCE width — silent wrong value for a non-canonical source — FIXED 2026-06-05 (binate 445d846a)
- **Symptom**: a widening integer cast (`cast(int, <int32 x>)`, sub-word →
  host-word) on both native backends does NOT re-extend the value from the
  source width; it just MOVs, assuming the source register is already
  sign/zero-canonical. The VM (`BC_SEXT`/`BC_ZEXT`) and LLVM (`sext`/`zext`)
  extend per the source type, so this is a native-only divergence — a silent
  wrong value whenever the source register is non-canonical.
- **Root cause**: `emitCast` (aa64 `aarch64_ops.bn:476`, x64 mirror) keys ONLY
  on the TARGET width: for `target.Width == 0 || >= 64` it emits a plain MOV
  (no extension); the sub-word LSL+ASR/LSR path only runs for a *narrowing*
  target. It never receives the source type, so it cannot extend-from-source on
  a widening cast.
- **Why it surfaced now**: post-4.1 (sub-word arith narrowing), arith results
  ARE canonical, so `cast(int, arithResult)` is correct via the MOV. But a
  `bit_cast(int32, <float32 const>)` result is left ZERO-extended (bit_cast is a
  plain reinterpret MOV), so `cast(int, bit_cast(int32, Neg))` keeps the
  zero-extended bits → `println` prints `3184315597` instead of `-1110651699`.
  This is the residual on **conformance/539_float32_const** (xfailed on all 3
  native lanes; the 4 non-negative lines pass; passes on VM + LLVM).
- **Fix (LANDED 445d846a)**: thread the source type into `emitCast` on both
  natives; on a widening cast (target host-word), sign/zero-extend from the
  SOURCE width per the source's signedness — mirroring the VM's `BC_SEXT`/
  `BC_ZEXT`. Narrowing casts keep the target-width behavior. No-op for canonical
  sources (scalar-matrix cells unaffected). The fix at the CAST is the right
  layer — do NOT narrow at OP_BIT_CAST instead (that would also touch the
  compiler's internal pointer bit_casts; the cast site is where the widening
  semantics belong).
- **CORRECTION — the earlier "blocked by a self-compilation break" conclusion
  was WRONG**: I had attributed a ~267/796 aa64 conformance wipeout (`bnc` link
  error `_bn_pkg__bootstrap__Write` undefined) to this fix. That breakage is the
  **separate, already-tracked CRITICAL aa64-native lane regression** (from the
  divide-fault guard series) — my experiments were rebased onto a base that
  already had it. There is NO hidden cmd/bnc cast/bit_cast dependency. Proof: the
  fix on the **clean x64_darwin lane** gives 807 passed / 4 failed (only the 4
  unrelated pre-existing failures, NOT 267), and 539 passes. The aa64 lane can't
  confirm until its CRITICAL issue is resolved, but 539 passed there too and the
  aa64 emitCast uses identical logic.
- **Test**: `conformance/539_float32_const` — now green on all modes (native
  xfails dropped). A direct `cast(int, bit_cast(int32, <high-bit u32>))`
  regression cell would harden it further.
- **Severity**: was MAJOR (silent wrong value, native-only). Resolved.

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

### ~~Interface method dispatch drops args after a width-mismatched managed-slice arg (codegen)~~ — FIXED + LANDED 2026-06-04 (binate `d6bb3b2f`)
- **Fixed**: factored the per-arg coercion loop out of `genCall` into a shared
  `coerceArg` helper (used by `genCall` + `genMethodCall`); `genInterfaceMethodCall`
  now evaluates args via `genExprOrFuncRef(...paramTyp)` + `coerceArg` like the
  regular path.  Interface method param types are carried via
  `ModuleInterface.MethodParamsFlat` + `MethodParamCounts` (flat encoding —
  `@[]@[]@types.Type` as a struct field trips a missing nested cross-package
  element dtor in the BUILDER, tracked separately below), populated at the decl
  AND generic-instantiation sites; `findInterfaceMethod` returns the param list
  from the inheritance level that owns the method (so embedded methods coerce
  too).  Pinned by `conformance/593` (own + inherited + func-value arg;
  negative-verified 3/3/3 without the fix vs 700/3/700 with) and `e2e/repl.sh`
  (now 53/53; `basic-call` was the hang).  Full conformance 522/0 + unit 39/39.
  Adversarial-reviewed before implementing (C1 inherited / C2 whole coercion
  machinery / M2 generic site / M3 self-ref timing / V2 flat encoding).
  Follow-up: a dedicated generic-interface-method slice-arg regression test
  (the generic-site population is code-identical to the verified decl path).
- **Root cause (CONFIRMED)**: `genInterfaceMethodCall` (`pkg/binate/ir/gen_iface.bn:89-94`)
  builds its call args with a bare `genExpr` per arg — it **omits the argument
  coercions** the regular call path applies (`gen_call.bn:140-202`), notably the
  `@[]T → *[]T` managed→raw slice conversion (`EmitManagedToRaw`).  When an iface
  method param is a raw slice (`*[]readonly uint8`, 2 words) and the arg is a
  managed slice (`@[]uint8`, 4 words), the unconverted 4-word value is passed
  where 2 words are expected, **shifting every following argument** — the next
  scalar arg is read from the wrong slot.  General MAJOR codegen bug; latent in
  conformance (no iface method has a managed-slice→raw-slice param).  The other
  omitted coercions (string-lit→chars, nil→slice, by-value struct-copy RefInc,
  iface-value move/RefInc) are each their own latent iface-arg bug.
- **How it surfaces (repl)**: the host loop calls `s.Step(line, eof)` where
  `line` is `@[]uint8` and `Step(line *[]readonly uint8, eof bool)`; with the
  conversion missing, `eof` is read as garbage/false, so an EOF turn never
  returns `STEP_EOF_CLEAN`.  The loop spins forever printing `> ` (NOT a clean
  segfault — it exhausts and dies; CI's captured output shows `> 14` then the
  crash).  `b9ca1acc` (ReplSession→interface) exposed it by routing `Step`
  through iface dispatch; green through `16:47`, first red `16:52`.  Not from
  the stdlib / bnc-0.0.7 work.
- **Minimal repro**: an iface method `M(line *[]readonly uint8, b bool) Res`
  (struct return) called via the interface with a `@[]uint8` arg returns the
  `b=false` branch even when `b=true` is passed.  Controls: `(int,bool)→int`,
  `(int,bool)→struct`, and `(@[]uint8,bool)→struct` (matched width) all pass —
  isolating it to the width mismatch, not sret / multi-word args in general.
- **Fix (planned)**: add `MethodParams` to `ModuleInterface` (populate alongside
  `MethodResults` during registration); factor the per-arg coercion loop out of
  `gen_call.bn` into a shared helper and call it from `genInterfaceMethodCall`
  too, so both paths stay in sync.
- **Why MAJOR**: silent wrong-arg in iface dispatch (not just repl).  Also E2E is
  red on *every* main commit, masking new E2E regressions; and `bnc-0.0.7` ships
  a `bni` whose interactive REPL hangs (accepted — REPL is a Tier-1 PoC, not
  build-critical; fix to land in 0.0.8-pre).
- **Test**: `e2e/repl.sh` `basic-call` (covers it end-to-end) + a new unit/
  conformance test from the minimal repro above.

### Field access into an anonymous (multi-return tuple) struct miscomputes the LLVM GEP index when a field has alignment padding before it — FIXED 2026-06-03 (binate `5f4a8eaf`)
- **What**: `emitGetFieldPtr` (`pkg/binate/codegen/emit_helpers.bn:118`) maps the
  Binate field index to the LLVM field index via `structLLVMIndex` (which counts
  inserted `[N x i8]` padding fields) **unconditionally**.  But anonymous
  multi-return tuple structs are emitted by `llvmType()` in the non-packed
  `{...}` form **without** explicit padding fields — so for them the Binate index
  already IS the LLVM index.  When such a tuple has a field with
  `PaddingBefore > 0` (a pointer/aligned field following a sub-word field like
  `bool`/`i1`), the mapping overshoots by the number of preceding padding gaps.
- **Symptom**: a `(bool, @errors.Error)` multi-return (e.g. `strconv.ParseBool`)
  generates its anon-tuple destructor `__dtor_anon_bool_unknown` with
  `getelementptr inbounds {i1, %BnIfaceValue}, ... i32 0, i32 2` — index 2 into a
  2-field struct → `error: invalid getelementptr indices`, clang fails.  If the
  overshoot had landed in-bounds it would be a SILENT wrong-field access instead.
- **Root cause**: `emitGetFieldPtr` is the lone `structLLVMIndex` caller missing
  the named-vs-anonymous guard.  The SSA copy paths already do it right:
  `emit_copy_ssa.bn:103` and `emit_copy_ssa_load.bn:85` apply `structLLVMIndex`
  only `if named` (`named = len(t.Name) > 0`) and otherwise use the raw index.
- **Fix**: `emitGetFieldPtr` now gates the `structLLVMIndex` remap on
  `len(baseTyp.ResolveAlias().Name) > 0` — named structs remap past padding
  fields; anonymous tuples use `instr.Index` directly.  Mirrors the
  named-vs-anonymous split already in `emitStoreSSARec`.  `pkg/codegen`
  function-body change (BUILDER-safe).
- **Affects**: LLVM backend (the GEP-index path).  VM uses byte offsets and was
  unaffected (conformance 144 passes on `builder-comp-int` as well as
  `builder-comp`).
- **Discovery**: 2026-06-03, implementing `strconv.ParseBool` (first
  `(bool, @errors.Error)` multi-return).  Had blocked `ParseBool`; the rest of
  the Parse series (`int64`/`uint64`/`float64` first elements — pointer-aligned,
  no padding) was unaffected.
- **Tests**: codegen unit test `TestAnonTupleDtorFieldGepIndex`
  (emit_refcount_test.bn) pins the GEP index; `conformance/144_multi_return_bool_iface`
  covers it end-to-end (green on LLVM + VM).

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

### Implement the strconv `Parse...` series (ParseInt / ParseUint / ParseBool / ParseFloat) — LANDED (complete)
- **What**: strconv has only the `Format.../Append...`/`Itoa` (number→string)
  direction; add the parse direction.  `ParseFloat` is the correct,
  fully-rounded decimal→double, built over `pkg/std/math/big` (exact
  mantInt*10^exp, round-to-even from the remainder) — the canonical home for
  what `common.ParseFloatLitToBits` approximates.  Once stdlib is
  BUILDER-bundled, the compiler's float-literal converter can route through it
  (or share its core), fixing the round-bit bug above.
- **Plan**: `explorations/plan-strconv-parse.md` (errors via the now-landed
  `@errors.Error`; input `*[]readonly uint8`).
- **Landed (binate)**: full series —
  `ParseBool` + unexported `numError` (`@errors.Error` impl) (`b4bfe843`;
  surfaced + fixed a MAJOR anon-tuple field-GEP codegen bug, `5f4a8eaf`);
  integer core `ParseInt`/`ParseUint`/`Atoi` (`6a91cf5b`); `ParseFloat`
  over `big` — exact, correctly-rounded decimal→binary for f64 and f32
  (`eb4a7aee`); `_` digit separators across all of them (`ea706e43`).
  Verified by Go differentials of the algorithms (integers 9.6M; floats
  2.59M incl. underscores + the over/underflow error kind; 0 divergences),
  exact-bit unit goldens, a Format↔Parse round-trip, and the
  `526_strconv_parse_cross_pkg` cross-package consumer (LLVM/VM/gen2;
  arm32/native via CI — the code is ILP32-safe, all math in uint64).
- **Hex floats — DONE both directions**: `ParseFloat` reads `0x1.8p3`
  (`15b6ce90`, pure-binary path sharing the rational rounding core; Go
  differential ~2M) and `FormatFloat`/`AppendFloat` emit `'x'`/`'X'`
  (`e85eb129`, exact nibble rendering, no big.Nat; Go differential ~4M).
  `_` separators accepted in hex too.
- **No remaining strconv follow-up** for parse/format parity.  (The only Go
  float format not implemented is `'b'` — decimal mantissa, binary exponent —
  which nothing needs yet.)  Once stdlib is BUILDER-bundled, route the
  compiler's float-literal converter through `ParseFloat`'s core to retire the
  round-bit dtoa bug + the duplicate converter (tracked above).

### float32 const literal: VM/native loaded the float64 pattern (wrong value) — FIXED 2026-06-05 (binate, plan-cr-p2 Plan 4 step 1)
- **LLVM compile error — FIXED 2026-06-03 (binate `4fd196d0`)**: a float32-typed
  OP_CONST_FLOAT emitted a decimal `float` constant (`fadd float 0.0, 0.1`),
  which LLVM rejects unless exactly representable (`floating point constant
  invalid for type`).  Fixed in `pkg/binate/codegen/emit_instr.bn`: materialize
  the value as a `double` (decimal is valid there) and `fptrunc` to `float`.
- **VM/native value bug — FIXED**: a float32-typed OP_CONST_FLOAT now narrows
  through `common.F64BitsToF32Bits` (round-to-nearest-even f64→f32) in the VM
  (`vm/lower_instr.bn` OP_CONST_FLOAT arm) and both natives' `emitConstFloat`, so
  `bit_cast(int32, C)` observes the true float32 pattern (`0x3DCCCCCD` for `0.1`,
  not `0x9999999A`).
- **The "blocked on a new BUILDER release" diagnosis was WRONG**: the real blocker
  was that `F64BitsToF32Bits` was defined in `common_float.bn` but never declared
  in `common.bni`, so no importer could resolve it.  BUILDER recompiles
  `native/common` from current source when it builds `cmd/bnc`, so a new `.bni`
  export is honored with no BUILDER bump.  Exporting it unblocked the one-liner
  wire-ins.
- **Test**: `conformance/539_float32_const` — now passes on the C/LLVM **and** VM
  lanes (those xfails dropped).  Native lanes still xfail, but ONLY on the
  negative const: native leaves the high-bit-set `bit_cast(int32)` result
  zero-extended (`3184315597`) not sign-extended (`-1110651699`).  That residual
  is sub-word value correctness — folded into **plan-cr-p2-4 #4.1** (the float32
  narrowing itself is correct on native too: the four non-negative lines pass).
- **Discovery**: 2026-06-03 (fixing the LLVM compile error surfaced the value
  bug).  **Severity**: MAJOR (was a silent wrong float32 const on VM/native).

### Self-referential interface method (`Unwrap() @Error` — a method whose return type is its own interface) mis-resolves to a managed pointer → in-package ABI mismatch — FIXED 2026-06-03 (binate `77499153`)
- **Symptom**: an interface with a method that returns its own interface type — e.g. `interface Error { Error() @[]char; Unwrap() @Error }` — miscompiles *in-package* at every dispatch of that method.  The vtable dispatch shim is typed `i8* (i8*)` (return = single pointer), but the method *body* returns a 16-byte `%BnIfaceValue`; the copy-site at the call (`var cause @Error = e.Unwrap()`) RefIncs the result via `extractvalue %BnIfaceValue …, 0`, so LLVM gets `%v6 = extractvalue i8* %v5, 0` → verifier error `extractvalue operand must be aggregate type`.  (Caught here only by that `extractvalue`; a dispatch whose iface-value result is merely stored/forwarded would **silently miscompile** — caller reads 1 word, callee wrote 2.)
- **Root cause (CONFIRMED)**: `collectInterfaceFromDecl` (`pkg/binate/ir/gen_iface_registry.bn`) resolves each method's return type via `resolveTypeExpr(m.Results[0])` (≈line 143) and stores it in `mi.MethodResults` **before** appending the interface to `moduleInterfaces` (≈line 201).  So while resolving `Unwrap`'s `@Error`, `Error` is not yet in the registry → `isInterfaceTypeExpr(Error)` misses → `resolveTypeExpr` falls to `MakeManagedPtrType` (`gen_util.bn:349`) → `i8*`.  `genInterfaceMethodCall` then reads `mi.MethodResults[j]` (`gen_iface.bn:153`) as the dispatch result type, so the shim returns `i8*`.  The method *definition*'s return type is resolved later (in `gen_func`, after all interfaces are collected) and correctly yields `%BnIfaceValue` — hence the in-module mismatch.
- **Why never caught**: `Unwrap() @Error` is the FIRST self-referential interface method in the codebase (an interface method whose return type is its own — or any not-yet-registered — interface).  All prior interface methods return scalars / `@[]char` / managed pointers, where the managed-ptr fallback and the correct type coincide at the LLVM level.
- **Severity**: MAJOR — in-package ABI mismatch for a whole class of interface (anything self-referential: builders, linked nodes, iterator-returns-iterator, and `Unwrap`).  Verifier-loud here, silent on store-only dispatch paths.
- **Fix (landed `77499153`)**: two layers.  `types/check_interface.bn` defines the interface symbol BEFORE resolving its method/parent signatures (matching the `.bni` bni_scope pre-registration, for in-`.bn` decls).  `ir/gen_iface_registry.bn` appends an identity stub to `moduleInterfaces` and points `currentImportAlias` at the interface's package before resolving method results (so a self-ref resolves even in the cross-package `RegisterAllInterfaces` pre-pass), then overwrites the stub.  Defining the interface early would let `interface A : A` resolve A as its own parent, so `resolveInterfaceExtension` now rejects self-extension explicitly.  Tests: `575_self_ref_iface_method` + `TestInterfaceSelfReferentialMethod`.
- **Discovery**: 2026-06-03, implementing `plan-std-errors.md` Part 1 — `pkg/std/errors`'s in-package unit tests (`TestNewUnwrapEmpty`/`TestWrapUnwrapCause`/`TestChainWalk` all call `.Unwrap()`).  Pre-existing latent bug.  Distinct from (but same managed-ptr-fallback symptom as) the cross-package entry below.

### Cross-package function returning `@Iface` resolves the return type to a managed pointer (`i8*`) in the consumer → ABI mismatch — FIXED 2026-06-03 (binate `cb8c0f1a`)
- **Symptom**: a consumer that imports a package and calls a function declared (in the `.bni`) to return a managed interface value — e.g. `errors.New(msg) @Error` / `errors.Wrap(...) @Error` — fails to compile with LLVM verifier error `extractvalue operand must be aggregate type` on `%v6 = extractvalue i8* %v5, 0`, because the consumer lowers the call as `call i8* @bn_pkg__std__errors__New(...)` (single pointer) while the callee's real ABI returns a 16-byte `%BnIfaceValue` (register pair).  The consumer's own refcount/copy machinery *correctly* treats the OP_CALL result as an interface value (hence the `extractvalue …, 0` to RefInc the data field), so the call-return-type and the copy machinery disagree inside one module.
- **Root cause (CONFIRMED)**: `isInterfaceTypeExpr` / `ifaceTypeForName` (`pkg/binate/ir/gen_iface.bn`) resolve a **bare** interface name (`te.Pkg` empty) by looking it up in `moduleInterfaces` only under `currentModulePkgPath` (the *consumer's* package) — never under `currentImportAlias` (the package whose `.bni` decls are currently being registered, `gen_import.bn:registerImportFieldsAndFuncs`, which sets `currentImportAlias = alias`).  The imported interface is registered (by `collectInterfaceFromDecl`) under its full path (`resolveImportPkg(alias)` = `pkg/std/errors`).  So while registering `errors.bni`'s `func New(...) @Error`, `resolveTypeExpr(@Error)` calls `isInterfaceTypeExpr(Error)` → lookup `("main","Error")` MISS → falls through to `MakeManagedPtrType` (`gen_util.bn:349`) → `llvmType` = `i8*`.  The struct / `TEXPR_NAMED` path already consults `currentImportAlias` (`gen_util.bn:271–283`, mirrored in `gen_const.bn:85`); the interface path does **not** — that asymmetry is the entire bug.
- **Why never caught**: errors is the FIRST cross-package function whose return type is an interface value.  The mis-resolution is INVISIBLE for managed-pointer (`@T`) and managed-slice (`@[]T`) returns — those lower to `i8*` / `%BnManagedSlice` whether resolved correctly or as the managed-ptr fallback — and strconv/big return exactly those.  An interface value is the first return type where correct (`%BnIfaceValue`, 2-word) and fallback (`i8*`, 1-word) diverge.  In-package compilation is fine (there the interface is under `currentModulePkgPath`), so `pkg/std/errors` itself builds; only the consumer mis-resolves.
- **Severity**: MAJOR — a cross-package ABI mismatch.  Here the LLVM verifier happens to reject it (the copy machinery's `extractvalue` on an `i8*`); on any codegen path that does NOT extractvalue the result (e.g. a `@Iface`-returning function whose result is only stored/passed, not retained at the call site) it would be a **silent miscompile** — caller reads a 1-word return, callee wrote a 2-word value.  Also affects `*Iface` returns by the same path.  (Almost certainly also `@func` / `*func` returns from a cross-package function whose signature spells the func-value type via a NAMED alias — not the structural `@func(...)` form, which resolves context-free — though unconfirmed.)
- **Fix (landed `cb8c0f1a`)**: in `isInterfaceTypeExpr` and `ifaceTypeForName` (`gen_iface.bn`), a bare name that misses under `currentModulePkgPath` now also tries `currentImportAlias` (keying the produced `TYP_INTERFACE` on the resolved full path), mirroring `gen_util.bn`'s `TEXPR_NAMED` arm.  Test: `576_cross_pkg_iface_return` (and the `577_std_errors` cross-package suite).
- **Discovery**: 2026-06-03, implementing `plan-std-errors.md` Part 1 (`pkg/std/errors`).  Pre-existing latent bug, exposed by the first cross-package interface-value return.

### Multi-return of a `@func` component was miscompiled — capture lost (LLVM) + invalid closure-data kind (VM) — FIXED 2026-06-03
- **Was**: a function returning a tuple with a function-value component — `func two(...) (int, @func(int) int)` — was wrong-coded for the `@func` slot.  `two(false)` returns `(0, adder(10))` (a capturing `func(x){ return x + n }`, n=10); `f(5)` then gave `5` not `15` in LLVM (capture `n` read as 0) and crashed `vm: unsupported function-value data kind: 0` in the VM.
- **Fix — two independent halves**:
  - **LLVM/IR (capture loss)**: fixed by the multi-assign managed-target refcount work (binate `0b3f4abe` + `6c4d45b0`) — the `@func` component was under-retained through the multi-value path, so the closure record was freed before invocation.  (Landed independently for the multi-assign CRITICAL bug; it also closed the LLVM half here.)
  - **VM (invalid closure data)**: binate `98f65edb`.  Once the closure record was valid again, the only remaining issue was the VM packing a 16-byte address-based `@func` component as one scalar word — the same shape as the iface case `578`.  Generalized `isVMInterfaceValue` → `isVMAddressAggregate` (iface + func) for both the multi-return result-layout classification and the EXTRACT pointer-mode.  (578 deliberately scoped to iface because the LLVM half was still broken then; with that fixed, extending to `@func` completes it cleanly.)
- **Tests**: `579_multi_return_func_value` (empty + capturing `@func` component, reassignment, invocation) — green in all six default modes.  Single-return `@func` stays pinned by 534/542/555.
- **Discovery**: 2026-06-03, while fixing the `@Iface` multi-return VM bug for `plan-std-errors.md` (the `(T, @Error)` error-return pattern).  Was pre-existing.

### Bytecode VM `@Iface` (interface) value handling — two VM bugs — FIXED 2026-06-03
- **Part A — single interface-value return not copied back → "call through nil interface value"** (binate `511e1395`).  Interface values are 16-byte address-based VM stack slots.  `lowerReturn` set BC_RETURN's copy-back size only for `isMultiWordField` types (struct / slice / array) — it omitted interface values, so a single `@Iface` return dangled in the reclaimed callee frame and the next call clobbered it; `consume(makeFoo(i))` (an iv call result passed directly as an arg) then panicked `vm: call through nil interface value` in `-int` only (LLVM + native don't use this lowering).  Fix: set the copy-back size for `TYP_INTERFACE_VALUE` / `_MANAGED` single returns too.  Pinned by `560_iface_return_call_arg` (green all modes).
- **Part B — interface-value receiver dtor crashed on RefDec-to-zero** (binate `5de3d09d`, the direct analogue of the `@func` capture-record dtor `0a0d00af`).  `BC_IFACE_DTOR` produced the receiver dtor's 1-based func index, but `BC_REFDEC_INLINE_FAST` consumes its dtor input as a func-value HANDLE — so an interface value that was the *last* holder of a managed-field receiver bit_cast the small index to a pointer and crashed (520; the dtor arms of 554 / 556).  473 hid it because its iv lives in a nested block the receiver outlives, so its RefDec never reached zero.  Fix: `BC_IFACE_DTOR` hands `BC_REFDEC` the dtor func's handle via `ensureHandle` (the same `{Vtable, ClosureRec{VM_CLOSURE_REC, FnIdx}}` the `@func` path uses); the existing iterative-push arm runs the receiver dtor and frees it via `freeOnPop`.
- **Result**: `520_iface_dtor_callee_sole_ref` (a standing `-int` red) is green; `554_iface_refcount_balance` and `556_iface_struct_field_balance` un-xfailed in all VM modes; `-int` suite 478/0.  Both were `pkg/vm`-only (codegen always emitted correct IR; LLVM + native were already correct).

### Conformance int-int mode: `136_grouped_imports` + `383_cross_pkg_iface_dtor` fail with "pkg/builtins/rt not found" — FIXED+LANDED (binate `db18f26b`, 2026-06-05)
- **Symptom**: on `builder-comp-int-int` (the double-VM default mode),
  `136_grouped_imports` and `383_cross_pkg_iface_dtor` fail at compile time
  with `package "pkg/builtins/rt" not found`.  Both PASS on `builder-comp-int`
  and `builder-comp-comp-int`; the other ~468 int-int tests pass.
- **Pre-existing**: confirmed on clean `17c722d1` (reproduced with the
  pre-float-fix VM tree), so NOT caused by the float-constant work; it is a
  recent main regression in the int-int package-resolution path.
- **Root cause (unknown)**: only certain multi-package tests can't resolve
  `rt` in the int-int pipeline; needs investigation of how that mode locates
  the `rt` package (vs the single-int / comp-int modes that succeed).
- **Discovery**: 2026-06-03, full-suite regression sweep while landing the
  float-constant fix (536).
- **Severity**: MAJOR — a default conformance mode is red, masking real
  coverage on those tests.

### Multi-value return assignment to `_` leaks the discarded managed component(s) — FIXED 2026-06-03 (binate, pending cherry-pick)
- **Was**: `_, n = f()` where `f` returns `(@T, int)` (or `@Iface`, `@[]T` — any managed type) never RefDec'd the `_`-discarded managed result → +1 leak per execution.  Root cause: the multi-assign loop (`genAssign`, `gen_control.bn`) ran the Axiom-3 copy-RefInc for the `_` component unconditionally, but a blank target stores nothing (`lookupVar("_") == nil`), so that RefInc had no matching RefDec.  (The single-value `_ = g()` path doesn't leak because its RefInc is *inside* the `ptr != nil` guard.)
- **Fix**: skip a blank-identifier target entirely in the multi-assign loop (`if lhs.Kind == EXPR_IDENT && isBlank(lhs.Name) { continue }`) — no copy-RefInc, no store; the call-result temp's dtor RefDec's the owned ref at end of statement.
- **Test**: `conformance/570_blank_discard_managed_balance` (loop of 100 discards; b's refcount returns to baseline 1, was 101 pre-fix).  Verified to fail on the unfixed compiler.
- **NOTE — the BOTH-bound form `a, n = f()` is NOT balanced** (the old entry wrongly claimed it was — it had only been checked for `@T` bound to a fresh-nil var).  See the two multi-assign defects in the CRITICAL section.

### bnlint typechecks dependency BODIES, not just signatures — FIX LANDED 2026-06-03 (binate `3fcfdf8c`); deployment pending next BUILDER bump
- **Status**: source fix LANDED (binate `3fcfdf8c`, + composition test
  `a079621d`).  Takes effect in hygiene only after BUILDER_VERSION is bumped
  to a snapshot containing it — the bundled bnlint is what hygiene runs.
- **Symptom**: linting package A that imports package B re-typechecks B's
  function *bodies*, not just its exported signatures.  A body-level type
  error in B then surfaces when linting A — false coupling.  Concrete
  trigger: `pkg/binate/vm`'s `_func_handle(rt._Package)` (valid, but newer
  than the BUILDER-bundled bnlint can typecheck) made `pkg/binate/repl` and
  `cmd/bni` *also* fail lint purely because they import vm, forcing the
  `scripts/hygiene/lint.sh` skip to cascade across all three.
- **Root cause**: `cmd/bnlint/main.bn` (`lintPackages`) loops over ALL loaded
  packages (`ldr.Order` — targets AND transitive deps) and calls
  `c.CheckPackage(...)` on each, which runs Pass 1 (`collectDecls`) + Pass 1.5
  (`checkAllImplsSatisfaction`) + Pass 2 (`checkDecls`, body checking).  The
  *lint* loop below only iterates the target `pkgs`, so it already
  distinguishes targets from deps — the body-checking of deps is incidental
  over-reach.  Dependents only ever consume a dep's exported surface, which
  `collectDecls` + `registerPackage` provide; body-checking a dep adds
  nothing for the dependent.
- **Fix (landed)**: `pkg/binate/types/checker.bn` gained `CheckPackageDecls`
  — Pass 1 (`collectDecls`) + `registerPackage`, skipping Pass 1.5/2 —
  sharing `checkPackageImpl(checkBodies)` with `CheckPackage`.
  `cmd/bnlint/main.bn` body-checks (`CheckPackage`) only the lint targets and
  registers transitive deps decls-only (`CheckPackageDecls`), routed by
  `isLintTarget`.  Removes redundant re-checking and stops a dep's body
  errors from leaking into importers.  Once deployed, shrinks the present
  skip from {vm, repl, bni} to {vm}.
- **Severity**: major for the *linter's* robustness (false failures + wasted
  work); linter-only, no effect on generated code.
- **Deployment**: takes effect after a BUILDER_VERSION bump — same release
  that ships the `_Package` typecheck support (Phase B entry above).
- **Tests (landed)**: `pkg/binate/types/checker_test.bn` —
  `TestCheckPackageDeclsSkipsBodies` (decls-only reports no body error; full
  check does), `TestCheckPackageDeclsRegistersScope` (exported surface still
  registered), `TestCheckPackageDeclsDependentResolves` (a dependent resolves
  a decls-only dep AND its body error doesn't leak).  `cmd/bnlint/main_test.bn`
  — `TestIsLintTarget`.

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

### `551`/`573` native-aa64 `&G`-as-rvalue — FIXED 2026-06-04 (binate `9a0f4f9a`)
- **Was**: taking a top-level global's address as a VALUE (`&G` as an
  rvalue: store value, call arg, return value, comparison operand,
  bit_cast source) was silently wrong on the native aarch64 backend.  `&G`
  is the IsGlobalRef pseudo-instr (ID -1, no SSA register); `getOperand`
  missed every lookup and returned -1, so the value-operand site dropped
  the operand (call args / return) or stored garbage.  Native handled
  IsGlobalRef only in ADDRESS-operand positions (load/store target, GEP
  base) via `emitGlobalAddr`; value positions were unwired.  The native
  analogue of the LLVM bug fixed in `99655f4e` (which rendered `%v-1`).
- **Fix**: new `emitValOperand` (aarch64_regmap.bn) — the value-operand
  analogue of `getOperand`: materializes an IsGlobalRef into a fresh
  scratch via ADRP+ADD, else defers to `getOperand`.  Routed every
  value-operand site through it (OP_STORE value; direct / indirect /
  func-value / handle call args; OP_RETURN single / sret-multi / packed;
  comparison operands; OP_BIT_CAST source); threaded `pkgName` into
  emitCallIndirect / emitCallFuncValue / emitCompare.  Two globals in one
  instruction (`&G == &H`) each get their own scratch — no clobber
  (contrast the VM's shared globalReg, 573's still-open `-int` bug).
- **Result**: `551` un-xfailed on native aa64; `573` (`return &G,&H` /
  `&G == &H`) — which was failing native aa64 UNMARKED — now passes there
  too.  Full native aa64 lane: 498 passed, 0 failed.  Unit tests:
  `aarch64_global_ref_test.bn`.  573's VM (`-int`) xfails are unaffected
  (the separate shared-globalReg bug, another worker's).
- **x64 parity still OPEN**: the structurally-identical gap exists in
  `pkg/binate/native/x64` value-operand sites (emitStore value, the call /
  return / compare emitters) — no x64 native lane in CI catches it, so it
  is a latent silent-wrong-value-operand bug there.  Fix with the same
  `emitValOperand`-style helper (a `getValOperand` mirroring the LLVM
  `emitValRef` fix); the x64 root-cause + site map is already scoped.

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

### Native (aa64 + x64) miscompiles a cross-package multi-return whose component is a managed interface value (`@Iface`) — MAJOR, silent wrong-code / crash (`526` xfailed)
- **Symptom**: `conformance/526_strconv_parse_cross_pkg` (added with the
  strconv `Parse*` series, `6a91cf5b`) crashes on
  `builder-comp_native_aa64-comp_native_aa64` — empty output.  The
  `Parse*` functions return `(T, @errors.Error)`; the cross-package
  multi-return of a managed-interface-value component is miscompiled:
  the returned `@Iface` comes back as **non-nil garbage** and the scalar
  component is **corrupted**, then the program crashes when the garbage
  `@Iface` is used.  Green on the default C/LLVM and VM modes.
- **Root cause (BISECTED 2026-06-04 with minimal native-aa64 repros)** —
  the break is exactly *cross-package* + *multi-return* + *managed-
  interface-value component*:
  - same-package `(int64, @errors.Error)` multi-return → **passes**
  - cross-package *single* `@errors.Error` return (`errors.New`) → **passes**
  - cross-package `(int, int)` multi-return → **passes**
  - cross-package `(int, @errors.Error)` multi-return → **FAILS**
    (returned `@Iface` non-nil, scalar corrupted)
  Minimal repro: a helper pkg `func Maybe(x int) (int, @errors.Error)`
  returning `x, <nil>`, with `main` doing `n, err = helper.Maybe(7)` — on
  native aa64 `present(err)` reads true (should be false) and `n` is
  wrong.  The importer mis-sizes the `@Iface` tuple component (resolves
  it to a managed pointer / wrong word-count within the return tuple), so
  the caller's sret layout disagrees with the callee's — the native-aa64
  analogue of the LLVM ABI mismatch fixed in `cb8c0f1a` (line ~434), but
  in the MULTI-RETURN-tuple case (the single-`@Iface` case is already
  correct on native aa64, hence `errors.New` passes).
- **Also fails on native x64 (SysV)** — same root cause (the importer's
  tuple-component type resolution for `@Iface` returns is backend-shared,
  not aa64-specific); here it crashes (SIGSEGV) rather than printing
  garbage.  Surfaced 2026-06-10 running the full x64 (Rosetta) lane.  NOT
  funcval-related (the big-multi-return-x64 fix `f0747762` doesn't touch
  it — `526` uses a direct cross-package call).
- **Status**: `526` xfailed on native aa64 (binate `49d03616`) and now on
  both x64 native modes (`builder-comp_native_x64` + `…_x64_darwin`,
  2026-06-10) + this TODO.  **MAJOR (silent wrong-code / crash) — NOT a
  workaround; needs a real fix to the native importer's tuple-component
  type resolution for `@Iface` returns (fixes aa64 AND x64 together).**
  Discovery: 2026-06-04
  full native-aa64 `--check-xpass` lane (first correct end-to-end run; the
  flag had been mis-positioned after the mode).  Not caused by the `550`
  work.

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

### Float function-values are silently miscompiled in the VM (`-int` modes) — FIXED on main (`7abc3809`)
- **Plan**: [`plan-float-arg-shim.md`](plan-float-arg-shim.md). Design A
  (uniform all-`int` shim ABI) approved + landed on main `7abc3809`
  (2026-06-03), verified across all default LLVM modes + codegen/vm unit
  tests, hygiene clean. Unblocks the bootstrap native-only work below.
- **Now visible on native_aa64 (2026-06-10)**: `TestExternFloat{,32}ArgViaRegistry` are the SOLE remaining `pkg/binate/vm` unit failures on `builder-comp_native_aa64` after the `_Package` native-emit fix (binate `f7d116f3`) unmasked them (the package previously link-failed before any test ran). So this float-arg-shim native gap is now the one thing keeping native_aa64 `pkg/binate/vm` unit red.
- **NATIVE-GAP root cause + fix plan (2026-06-10 investigation)**: Design A int-ified the shim on the LLVM side ("native backends — all unchanged"), but in a `--backend native` UNIT build the package-under-test (`pkg/binate/vm`, incl. `vmTestFloatBits` + its `@__shim`) is compiled NATIVELY, so the LLVM int-ified shim is never used — the NATIVE shim is. `_raw_func_addr(fn)` → `OP_FUNC_HANDLE` → the `@__shim` (always-shim), called by the VM's all-`int` dispatch (`rt._call_shim_scalar`) with every arg in a GP register. The native shim emitters (`pkg/binate/native/aarch64/aarch64_funcvalue.bn emitFuncValueShims` + `aarch64_closure_shim.bn`; the x64 siblings) only SHIFT GP arg registers (drop the data param) and tail-branch — they do NO float int↔FP reconciliation, so a float-scalar arg reaches the real fn in a GP reg where AAPCS64/SysV says it reads `d0`/`xmm0` → garbage; a float-scalar return breaks symmetrically. (The native `OP_CALL_INDIRECT` float path, `aarch64_call_indirect.bn:44` `if isFloatTyp(arg.Typ) { Fmov_gp_to_fp }`, can't help — it keys on the IR OPERAND type, which is all-`int` in the magic dispatch.) **Fix = the native half of Design A**: in the native shim emitters, per the func-value's PARAM types, `fmov` each float-scalar arg's GP reg → its FP reg (FP index counted independently of the GP shift, mirroring `aarch64_call_indirect.bn`'s `nsrn`/`ngrn` split), and for a float-scalar return drop the tail-branch → `Bl` + `fmov` return-reg ← `d0` + `ret` (x64: xmm0 → rax). Reuse `codegen.isFloatScalarParam`/`floatSlotIsI32` (or a native mirror) so emit + call agree. ×2 arches; interacts with the closure-shim + pack-return shim shapes. Locally verifiable: `scripts/unittest/run.sh builder-comp_native_aa64-comp_native_aa64 pkg/binate/vm` → the 2 `TestExternFloat*` tests go green.
- **SCOPE CORRECTION — the fix is TWO-SIDED, not shim-only (2026-06-10, post-mapping; approved by user)**: the shim-only framing above is incomplete. The native func-value float ABI is **FP-resident on BOTH the shim AND the compiled caller** today: `emitCallFuncValue` (`{aarch64,x64}_call_indirect.bn`) places float args in `d/xmm[nsrn]` (`Fmov_gp_to_fp`/`Movq_gp_to_xmm`) and reads float returns from `d0`/`xmm0`, and the shim is FP-passthrough (does nothing to floats) — self-consistent, which is exactly why conformance `562–568` (float func-value arg/return/roundtrip/mixed/float32/aggregate) are GREEN on native today (only `569` closure-float is xfailed). The VM caller (`_call_shim_scalar`) is all-int (floats in GP), so only it is red. A shim is one static piece of code → it can serve ONE convention, and that convention is FORCED to all-int: (a) `_call_shim_scalar` can't place floats in FP without a float-aware trampoline (Design A rejected that); (b) the `@__shim` symbol is `weak_odr`/`SetWeak` and linker-deduped with the **LLVM** shim, which is already all-int — and the VM can't know which backend emitted the shim it calls. So the native shim MUST go all-int, which FORCES the native compiled caller all-int too; the two move together. **Shim-only would regress 562–568.** **Latent silent-miscompile bug this also fixes**: in a hybrid build (native `main` + LLVM deps), a float-arg func value for an LLVM-compiled dep function uses the LLVM all-int shim, but native `emitCallFuncValue` places the float in FP → mismatch → garbage (untested today; 562–568 keep everything in one native module). **Two-sided fix**: (1) SHIM (`emitFuncValueShims` + closure shims, ×2 arches): per the func-value PARAM types, `fmov` each float-scalar arg's positional-GP slot → its FP reg (`d/xmm[nsrn]`, 32-bit `S`/`movd` for float32; independent `nsrn`), and for a float-scalar return `bl`+`fmov`(FP→GP)+`ret` (frame) instead of tail-branch. (2) CALLER (`emitCallFuncValue`, ×2 arches): build the shim-boundary `argTypes` with float-scalars replaced by an int slot so they flow the GP positional path; drop the `xmm`/`nsrn` arg branch and the `Movq_xmm_to_gp` float-return special-case. `emitCallIndirect` (real fn-pointer calls — dtors/free_fn/cross-mode) keeps its FP handling: it does NOT go through the all-int shim. Re-verify `562–569` + `TestExternFloat*` on native aa64 AND x64-darwin; un-xfail `569`.
- **FUNC-VALUE HALF LANDED — binate `34533cf8` (2026-06-10)**: the two-sided all-int func-value shim fix (native shim does GP↔FP via a per-arch `emitShimArgMarshal*` walk + a float-scalar-return shape; `emitCallFuncValue` passes/reads floats via GP — substitutes a 1-word int slot in the shim-boundary `argTypes`, drops the xmm/nsrn branch and the float-result-from-xmm0 special-case). Added `common.FloatScalarIsI32` (+ unit test) so emit/call agree on i32-vs-i64 slot width; split `x64_funcvalue.bn` → `x64_funcvalue_vtables.bn` for the length cap. **Verified**: `TestExternFloat{,32}Arg` + `TestExternFloatReturn` ViaRegistry green on native aa64 AND x64-darwin; conformance 562–568 green on both; 22 func_value conformance green on aa64; hygiene clean. This is the bug that kept native_aa64 `pkg/binate/vm` unit red — now GREEN.
- **REMAINING — closure-float follow-up**: conformance `569` (a closure capturing+passing+returning float64) still fails — the closure shims (`emitClosureShim*`: fast / stack-spill / aggregate, ×2 arches) have NO float GP↔FP handling and never did. **PRE-EXISTING, not caused by the func-value fix** (empirically `actual:0` on the pre-`34533cf8` tree for x64-darwin). Now xfailed on BOTH arches (aa64 already had one; the missing `builder-comp_native_x64_darwin` xfail was added in `34533cf8`). FIX = extend the float GP↔FP marshalling into the closure shims (captures from the closure struct + user args, splitting NGRN/NSRN; plus a float-scalar-return shape), ×2 arches × 3 shapes — a separate sizable rework atop the just-reworked (`646e1638`) closure code. Un-xfails `569` on both arches when done.
- **Canonical repro**: `pkg/binate/vm` `TestExternFloat*ViaRegistry` (a
  bytecode caller invoking a native float extern via the registry) — the
  only path that hits the bug; user float func-values in `-int` are
  bytecode/trampoline (all-int VM slots) and round-trip fine without the
  fix, so the conformance 562-566 tests are compiled-mode reshape guards,
  not the repro.
- **Symptom**: a function-value call with a `float64`/`float32` arg or
  return produces the wrong value in any `-int` (bytecode VM) mode.
  Compiled modes are correct. Currently masked: there is *zero* test
  coverage for float func-values.
- **Root cause**: VM dispatch routes through `rt._call_shim_scalar(fn,
  data, a0..a6 int)` — an all-`int` `OP_CALL_INDIRECT`. The native
  backend only places an arg in an FP register when the IR operand type
  is float, so a float arg's bits land in a GP register while the natural-
  typed shim reads `d0`/`xmm0`. Float returns break symmetrically
  (aarch64 indirect has no float-return path).
- **Fix (Design A)**: int-ify float **scalars** in shim signatures and
  `bitcast` `i64↔double` / `i32↔float` at the shim boundary; the compiled
  call site (`emitCallFuncValue`) bitcasts to match. VM/`rt`/native
  unchanged; no-op for non-float signatures. Pure `pkg/binate/codegen`
  change. Conventions: exact-width slots (f64→i64, f32→i32), aggregate
  retbufs stay natural-typed, one shared `shimIntSlotType` predicate so
  shim and call site can't disagree (the only silent-miscompile path).
- **Why now**: prerequisite for the bootstrap injection below
  (`bootstrap.formatFloat` is a native extern once bootstrap is native-
  only) — without it, `conformance/287_float_println` regresses in `-int`.
  Per Bug Discovery Protocol, the new func-value-float tests are the
  tracked reproduction. Surfaced 2026-06-03 by the bootstrap work.

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

### Cross-package managed-PTR extern var: value-copy (559) + field-write (561) — BOTH RESOLVED 2026-06-04 (native-aa64 stale xfails removed `c4036777`)
- **Resolution (2026-06-04)**: with the native aa64 lane now building
  (after the `551`/`573` `&G`-rvalue fix `9a0f4f9a`), a per-mode
  `--check-xpass` sweep showed **`559` XPASSes on every execution path**
  (LLVM, VM, self-host gen2/gen3, native aa64) and **`561` XPASSes on
  native aa64**.  Both were stale:
  - `559`'s cross-package value-copy crash (the importer lacking the
    imported type's dtor for the scope-end RefDec) was closed by recent
    main work.  `559` is now the ORIGINAL aliasing test — green on ALL 6
    default modes + native aa64, no xfail.  The refcount-BALANCE check
    (which needs an `rt` import, tripping the int-int loader bug) was
    split out into a new directory test `586_cross_pkg_managed_ptr_copy_balance`,
    xfailed only in `builder-comp-int-int` (`66aef4c1`).  (Interim
    history: `32bee84c` strengthened `559` in place + carried an int-int
    xfail; `c4036777` dropped the stale native-aa64 xfails; `66aef4c1`
    then split aliasing vs balance so `559` is xfail-free again.)
  - `561` was already RESOLVED on the default modes 2026-06-03
    (`733d4485`, below); only its native-aa64 xfail lingered, because
    that lane didn't build until `9a0f4f9a`.
  The native-aa64 xfails for BOTH `559` and `561` removed in `c4036777`
  (the strengthened `559` test XPASSes on native aa64).  `559`'s
  `builder-comp-int-int` xfail intentionally remains (rt loader bug).
  (My earlier combined removal attempt `20d7a59d` was abandoned — it
  collided with `32bee84c`'s better, concurrent 559 handling.)  Surfaced
  while landing `550`; not caused by it (559/561 use no closures).
- **~~Symptom A (value-copy crash, 559)~~ — RESOLVED 2026-06-04**: the
  crash (importer lacking the imported type's dtor for the scope-end
  RefDec) was closed by recent main work; see the Resolution note above.
  Tests: `conformance/559_cross_pkg_managed_ptr_copy` (aliasing — green on
  all 6 default modes + native aa64) and
  `conformance/586_cross_pkg_managed_ptr_copy_balance` (refcount balance —
  rc 1->2 on copy, ->1 at the scope-end RefDec; xfailed in
  `builder-comp-int-int` for the orthogonal rt-loader bug).
- **~~Symptom B (field-write no-op, 561)~~ — RESOLVED 2026-06-03 (binate
  `733d4485`)**: `pkg.G.V = v` through an imported managed-ptr var
  silently dropped the store.  Root cause was NOT `genSelectorPtr`'s
  EXPR_IDENT-only branch (its nested-selector branch already recurses and
  obtains the lvalue) but `getSelectorType` returning nil for `pkg.G` — it
  resolved the import alias `pkg` as a (nonexistent) variable, so the
  nested branch couldn't type the inner selector and skipped the
  managed-ptr field-store case.  Fixed with a package-qualified-var case
  in `getSelectorType` (returns the imported var's declared type via
  `lookupImportedGlobalPtr`); `getSelectorType` moved to
  `gen_selector_type.bn` (length cap).  `conformance/561` un-xfailed
  (green all 6 default modes + native aa64 — the stale native-aa64 xfail
  was removed in `c4036777`).  Unit: `TestGetSelectorTypeQualifiedImportedVar`.
- **Discovery**: 2026-06-03, deferral-2 Slice 4 + coverage review.

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

### Dispatch conflicts (extern registered + Binate body provided) should be a HARD ERROR — ❌ REVERTED, NOT A REAL BUG (landed `e508c841`, reverted `71bf2b2a`, 2026-06-09)
- **Misdiagnosis**: extern + Binate body is a LEGITIMATE pattern — VM trampolines (`pkg/binate/vm.TrampolineScalar`) are intentionally both. The hard-error guard false-positived when the inner VM lowers `cmd/bni` (int-int only), breaking the whole int-int lane. The single-VM 1263/0 check missed it (it lowers the test module, not `cmd/bni`; int-int was dead then). No real bug to fix; do not re-implement without proving an accidental collision actually occurs.
- **What**: today the VM dispatches a `BC_CALL` by name: `LookupFunc`
  → if `>=0`, run the bytecode body; if `-1`, fall through to
  `execExtern` (which consults `vm.Externs`).  Functions registered
  via `RegisterExtern` shadow whatever the .bni declares, but ONLY
  when there's no Binate body — if a user (or a future migration)
  adds a `.bn` body for a name that's also extern-registered, the
  bytecode body silently wins and the extern is dead code.
- **Why a hard error**: the previously-explored "dispatch flip"
  (silently skip lowering when an extern is registered, so the
  extern wins) is the wrong design — the conflict represents
  contradictory definitions of the function, and the right answer
  is to make the user resolve it explicitly, not pick a winner
  silently.
- **Where**: `pkg/binate/vm/lower.bn::LowerModule` (the loader
  pass) is the natural place to detect it — when about to lower
  a function whose qualified name `vm.LookupExtern(...) >= 0`,
  abort with a clear diagnostic naming the offending function
  and both sources.  Same shape as the existing extern-registry
  pre-checks but loud instead of silent.
- **Tests**: unit test pinning the abort path (register an
  extern + lower an IR module with a function under that name
  → assert it errors with a recognizable message).

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

### Checker does not fold `iota` in expressions — bit-flag const COMPILE-TIME values stay plain-iota — ✅ RESOLVED (binate `05901f97`, 2026-06-09)
- **STATUS 2026-06-09**: FIXED exactly per the fix sketch below (both parts). `checkIdent` returns `makeUntypedIntWithLit(c.Iota)` for `iota`; `checkGroupDecl` repeats the previous explicit member's initializer (re-folded at the current iota) for bare members. The pre-flagged tightening is now in effect (`var x uint8 = B8` with `B8 = 1<<8 = 256` is correctly rejected). No existing unit/conformance cell changed (151 const/iota/enum cells green). Tests: conformance `672_err_iota_bitflag_overflow` + two unit tests in `check_expr_constfold_test.bn` (CR-2 Plan-B).
- **Symptom**: iota-repeat (binate `52a9eabf`) gives correct RUNTIME values for bit-flag consts (`const ( B0 int = 1 << iota; B1; B2 )` -> 1,2,4 at runtime). But `checkIdent` returns a plain `TYP_UNTYPED_INT` for `iota` (no `HasLitVal`), so the checker never folds an iota expression to a value: a bare member is given the plain-iota value via `makeUntypedIntWithLit(c.Iota)`; an explicit `1 << iota` member gets no value. So a bit-flag const's COMPILE-TIME value (array dimensions, assignability/overflow checks) is wrong/absent -- e.g. `var x uint8 = B10` with `B10 = 1 << 10 = 1024` is wrongly accepted because the checker thinks `B10 = 10`.
- **Scope**: compile-time only; runtime values are correct (IR-gen). The dominant `= iota` enum idiom is unaffected (plain-iota == iota-repeat there). Affects only bit-flag-style consts used as array dims or in narrow-type checks -- rare.
- **Fix sketch**: fold `iota` in `checkExpr` (return `makeUntypedIntWithLit(c.Iota)` from `checkIdent`), and have `checkGroupDecl` re-check a bare member's repeated previous expression with the current iota so its symbol value matches IR-gen. Watch for new overflow errors on large iota enums assigned to narrow types.
- **Discovery**: 2026-06-05, while implementing iota-repeat (Plan 1 / 1.3d).

### Untyped single const (`const X = 5`) is not forward-referenceable — FIXED+LANDED (binate `99057185`, 2026-06-05)
- **Symptom**: a top-level untyped single const with no explicit type
  (`const X = 5`) reports `undefined` when referenced from a decl
  checked BEFORE it — a forward reference within a file, or a sibling
  file ordered ahead of it (package files are merged).  `const X int = 5`
  (typed) does NOT have this problem.
- **Relationship**: the sibling of the const-GROUP bare-iota-member bug
  fixed in binate `88c9c0b7` — same root cause, `collectDecls`
  (`pkg/binate/types/check_decl.bn`) only forward-registers consts whose
  `TypeRef != nil`.  The group fix handled bare iota members (always
  untyped int → trivial untyped-int placeholder); this single-const case
  was left because it is **harder**: an untyped single const's type
  depends on its VALUE, and naively `checkExpr`-ing the value during the
  collection pass would emit spurious `undefined` errors for
  reference-valued consts (`const X = Y; const Y = 5`, where Y is checked
  after X).
- **Discovery**: 2026-06-02, characterizing the completeness of the
  group fix (a probe test, `TestForwardRefUntypedSingleConstKnownGap` in
  `pkg/binate/types/check_decl_test.bn`, asserts the current buggy
  behavior so the suite stays green).
- **Why MAJOR (loud, not silent)**: compile-time `undefined`, not a
  silent miscompile.  Lower-priority than the group case in practice —
  untyped single consts forward-referenced are uncommon (most code
  writes `const X int = …` or uses a group).
- **Proposed fix direction**: in `collectDecls`, for an untyped single
  const, forward-register the name when the value is a simple LITERAL
  (int / string / float / bool / char) whose type is unambiguous and
  dependency-free; leave reference / expression values for a later pass
  (or a two-phase const resolution).  Avoids the spurious-error trap.
- **Tests covering it**: `TestForwardRefUntypedSingleConstKnownGap`
  (flip to `expectNoErrors` when fixed); add a conformance test mirroring
  `526_forward_ref_iota_const` for the single-const case as part of the
  fix.

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
- **Final diagnosis**: an unqualified EXPR_IDENT inside a
  `.bni`-declared const initializer (e.g. `WORDS` in
  `const SIZE int = WORDS * cast(int, sizeof(int))`) wasn't
  resolving during import processing — pkg/ir's evalConstExpr
  looked the name up only in unqualified form, but the sibling
  const had been registered under the import-qualified name
  (`pkg/x.WORDS`).  The EXPR_IDENT arm returned (0, false), the
  binary expression silently became 0, and the resulting const
  was registered with value 0.
- **Fix (binate `8fd4f378`)**: retry the lookup with
  `buildQualName(currentImportAlias, e.Name)` when the
  unqualified one misses.  Pinned by conformance
  `504_bni_const_sibling_ref`.
- **Boundary-enforcement aside**: my first writeup of this also
  speculated that bnc was accepting unexported cross-package
  references.  Re-tested with a focused repro: bnc DOES correctly
  reject `pkg.NAME` references when NAME isn't in the package's
  `.bni`.  Pinned positively by conformance
  `502_err_unexported_const_rejected`.  That part was always fine
  — the only bug was the sibling-ident lookup above.
- **Discovery**: managed-allocation-header refactor (binate
  `c7323fb2`).  Replacing pkg/vm's hardcoded `-16` managed-header
  offset with `ptr - rt.HEADER_SIZE` (declared as
  `HEADER_WORDS * cast(int, sizeof(int))`) built cleanly but
  produced `ptr - 0`, silently corrupting the payload's first
  word.  TestExecRefIncRefDecInline (pkg/vm) caught it on amd64.

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

### Generics in cmd/bnc's tree — UNBLOCKED 2026-05-26 (BUILDER → bnc-0.0.2)
- **Status**: BUILDER is now bnc-0.0.2 (binate `5414bab`), which
  was cut from a tree that has generics (slices 4–7).  Verified the
  builder compiles generic decls + explicit instantiation
  `f[T](...)`; cross-package monomorphization works too.  So
  cmd/bnc-tree code may now use generics.
- **No type inference** (claude-notes.md:537, 1000): always spell
  the type arg, e.g. `slices.Append[@ast.Decl](xs, d)`.  The
  builder's "generic function requires type arguments" diagnostic
  on a bare `f(...)` call is intended behavior, not a gap.
- **First consumer — `pkg/slices`** (IN PROGRESS): `Append[T]`
  collapses the dozens of per-type `appendXxx` / `appendXxxPtr`
  helpers scattered across cmd/bnc + pkg/*.  Migration is staged
  one package at a time (see below).
  - **Generic packaging pattern**: a generic's body must live in
    the `.bni` (body-included) so cross-package consumers can
    monomorphize at the call site.  For an all-generic package the
    `.bn` needs **no** copy of the body — just the `package` decl
    (the package's own compile + tests resolve the generic from the
    merged `.bni`).  Keeping a second body in the `.bn` is a
    needless sync hazard; don't.
- **Mechanical migration DONE 2026-05-28**: ~62 per-type append
  helpers across pkg/{ast,types,ir,parser,loader,codegen,vm,
  native/aarch64} + cmd/bnc collapsed into ~378 call sites of
  `slices.Append[T]`, one commit per package boundary
  (binate `2714e67` loader → `ed727f8` parser → `bbb7fab5` ir →
  `60f385ff` cmd/bnc → `12f20a06` types → `79c11465` ir literals →
  `efbac9db` codegen → `d43185bb` vm → `1a45bb9b` aarch64 →
  `d226b237` ir scattered → `13477619` types capture → `a66b287c`
  cmd/bnc test).  Four `pkg/{loader,parser,ir,cmd-bnc}/slices.bn`
  files deleted.  Net ~-750 lines.

### Review remaining non-standard `appendXxx` helpers — opportunistic
- 13 helpers were kept past the `slices.Append[T]` migration because
  their bodies aren't a pure slice-of-T append (per the commit
  messages around 2026-05-28).  Worth reviewing whether any could be
  refactored to use `slices.Append` plus a small adapter:
  - ~~**Char-concat into a `@[]char` buffer** (not slice-of-T):
    `pkg/native/x64/x64_iface.bn`'s `appendPkgIdent_x64`,
    `appendStrIface`; `pkg/native/aarch64/aarch64_iface.bn`'s
    `appendPkgIdentNative`, `appendStrLocal`.  These four could
    probably share a single `buf.WriteStr`-style helper.~~ — DONE
    2026-05-28 (binate `fd1e931c` + `1b762f16`): pulled the two
    distinct shapes into `pkg/native/common.AppendStr` /
    `AppendPkgIdent`, x64/aarch64 callers rewritten, 4 duplicate
    helpers deleted, direct unit coverage in common_test.bn.
  - **Dedup / diagnostic-emitting**:
    `pkg/types/check_iface_extends.bn`'s
    `appendIfaceMethodWithConflictCheck` (emits a `CheckError` on
    signature mismatch) and `appendUniqueMethods` (dedup by method
    name).  These stay non-standard.
  - **Parallel two-slice append**:
    `pkg/ir/gen_iface_extends.bn`'s `appendAncestors(pkgs, names,
    pkg, name)` — could split into two `slices.Append` calls but
    the paired-update pattern is the helper's value; debatable.
  - **Conditional multi-arg append**: `cmd/bnc/target.bn`'s
    `appendTargetFlags`, `appendTargetRuntime` — fine as-is.
  - **Loader-level Imports**: `cmd/bnc/compile_imports.bn`'s
    `appendRtImport`, `appendLibcImport`, `appendBootstrapImport` —
    not slice append; fine as-is.
  - **Raw-slice wrap-and-append**: `cmd/bnc/util.bn`'s
    `appendRawCharSlice(s, *[]const char) → @[]@[]char` (CopyStr +
    append).  Could inline the 47 call sites as
    `slices.Append[@[]char](s, buf.CopyStr(v))` but the named
    helper documents the wrap-and-append idiom; debatable.

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

### Mirror `return f(...)` acceptance in the Go bootstrap — LOW PRIORITY
- Self-hosted accepts the shape (commits `b88918e` /
  `d11e4f2` / `d3fc0db` / `96572fb` on main; conformance
  `347_return_multi_call`). Bootstrap still rejects it.
- **What's needed**:
  1. **Type-checker** (`bootstrap/types/checker.go:checkReturnStmt`,
     ~lines 963-978): when `len(s.Results) == 1` and
     `len(c.funcRet) > 1`, allow it iff the single expression is
     a `CallExpr` whose function type returns a matching tuple
     and each per-result type is `AssignableTo` the
     corresponding `c.funcRet[i]`. Mirrors the existing
     multi-return shape in `checkShortVarDecl` (~lines
     937-955) — same `(len(s.RHS) == 1 && rhsType is FuncType
     with matching Results)` predicate.
  2. **Bootstrap interpreter STMT_RETURN execution path**:
     extend it to handle the single-expression-multi-return
     shape, mirroring how `q, r := f()` is already executed
     (single call eval + per-result destructure).
  3. **Conformance**: drop `347_return_multi_call.xfail.boot`
     once both impls handle it. Drop the bootstrap-only
     rejection note from `bootstrap-subset.md`.
- **Why low priority**: the bootstrap subset is intentionally
  restrictive; the self-hosted toolchain doesn't need this to
  compile, and no in-flight work depends on it. Pick up when
  there's a concrete user (e.g., a self-hosted source file that
  wants the form, or a broader bootstrap-subset widening pass).

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
- **STATUS 2026-06-10 — GREEN** (unit run on `3342460e`): all 8 `builder-comp-int-int` shards pass (2.5–26.7 min) and `builder-comp-int` / `-comp-int` pass. **Margin note**: shard 4/8 ran 26.7 min — ~89% of the 30-min cap; the 8-shard + skip set is sufficient but thin, so if the int-int suite grows it may need a 9th–10th shard or one more skip before it times out again. (The remaining unit reds — `arm32_{linux,baremetal}`, `native_x64` — are separate WIP/never-green modes, not this.)

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

### Slice ownership model — design notes
Binate is NOT Go. The two types of slice are intentionally different:

**Raw slices (`*[]T`)** — two words: (data ptr, length)
- Value types, no refcounting, no GC. Caller manages lifetime (like C).
- Cannot be compared to `nil` — check `len(s) == 0` for empty.

**Managed-slices (`@[]T`)** — four words: (data ptr, length, backing_refptr, backing_len)
- Prefix-compatible with `*[]T`. Refcounted via backing_refptr.
- backing_len stores total element count for destructor cleanup.
- `make_slice(T, n)` returns `@[]T`. `@[]T → *[]T` conversion: extractvalue fields 0,1.

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

### (b1) Class 2 matrix — VM 16-byte address-aggregate (iface / func value) handling — ✅ REALIZED 2026-06-05 (binate `12d6782f`)
- **Realized**: `conformance/matrix/addr-aggregate` (generator
  `gen-addr-aggregate-matrix.py`). Axes `kind (@func / @Iface) × operation
  (direct / copy / return / arg / return-arg / field / array-elem)`; assertion:
  both words of the 16-byte value survive the boundary, observed by invoking it
  (→ 42); a dropped/swapped word faults or returns wrong. 14 cells.
- **Result**: all 14 green on `comp` (LLVM), `int` (VM), and x64-native — the
  Class-2 fixes that landed in P2 (the VM func-value nil-vtable `e337e413`, the
  2-word-slice-len-drop) hold across the grid; this is regression coverage, no
  new defects. aa64-native is collateral-red on the self-hosting `BNC_NATIVE`
  miscompile (separate CRITICAL), not these cells.
- **Note**: the `field`/`array-elem` cells store an already-typed value (a bare
  func literal in those positions trips the separate filed bare-func-literal
  flavour-inference MINOR, not 2-word survival).

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

### `readonly`-wrapped slice argument mis-classified → SIGSEGV/garbage on clean code — ✅ LANDED binate `487fb95c` 2026-06-10 — was MAJOR
- **Symptom**: passing a string/managed-slice value through a `readonly`-wrapped slice parameter mis-classifies it as a non-slice scalar. `func lenMro(s readonly @[]readonly char) int { return len(s) }; lenMro("cde")` COMPILES CLEANLY but SIGSEGVs on native (aarch64 / x86-64) **and** LLVM, and returns a wrong length on the bytecode VM.
- **CORRECTED diagnosis** (the original reading-review entry above was WRONG; empirical reproduction reclassified it): this is NOT primarily an `OP_CONST_NIL` defect, and the VM const-nil path was actually fine. The real bug is a family of **un-peeled `readonly` at coercion / shape-classification points** (`readonly` is IR-transparent — same representation — so every aggregate-vs-scalar decision must peel it). The const-nil only appears for the *empty* string sub-case; the dominant trigger is the bare-pointer-passed-as-aggregate at `coerceArg`. The earlier "native const-nil region predicate" claim was stale — `common.IsAggregateTyp` already peels (R2-D4 `c6fe0914`) and the native region+lowering both consult it, so native const-nil was already correct.
- **Fix (4 sites, all in `7f53b9ce`)**: (1) `coerceArg` (`ir/gen_call.bn`) string→chars / nil→slice gates use `isSliceType` (peels readonly), managed→raw gate peels readonly — gating on bare `paramTyp.Kind` skipped the conversion, passing the bare string-literal pointer where the callee reads a 4-word aggregate by address. (2) VM `isAggregateLoadTyp` (`vm/lower_instr_helpers.bn`) → new `vmPeelTransparent` (alias+readonly+named), mirroring native `common.peelTransparent`. (3) VM `lowerStore` (`vm/lower_memory.bn`) value-type classification peels transparent — a readonly managed-slice param copied into a local was storing one scalar word. (4) LLVM `constNilLLVMTypeName` (`codegen/emit_const_nil.bn`) peels outer readonly/alias — an empty `readonly @[]readonly char` was emitting scalar `inttoptr 0`, mismatching the `%BnManagedSlice` consumer.
- **Tests**: `conformance/688_readonly_slice_param` (empty+non-empty string literals at `readonly @[]char` / `readonly @[]readonly char` / `readonly *[]readonly char` params + readonly-typed locals), green on LLVM / native-aa64 / native-x64-darwin / VM; unit tests pin `isAggregateLoadTyp` + `constNilLLVMTypeName` peeling. Full conformance: LLVM 1317/0, VM 1295/0, native-aa64 1294/0.
- **Note**: `coerceArg`'s managed→raw readonly-peel is currently unreachable from source (the `@[]T → readonly *[]T` assignment is FE-rejected — see the assignability over-rejection finding below); the peel is correct and forward-looking.

### Adversarial audit of `7f53b9ce` (2026-06-09, find→verify workflow) — sibling findings
The audit re-verified its own findings at runtime; **note a concurrent worker clobbered shared `/tmp` test files mid-run**, producing one false positive (below) — all findings here were re-reproduced by hand with unique paths.

- **MAJOR — `readonly` aggregate multi-return component → LLVM SIGBUS — ✅ RESOLVED 2026-06-10 (binate `c63a7e3f`)**. Root was the FE check gap (the "Also suspicious" note below), NOT primarily the zero-init: the multi-return destructure path skipped the const-location check the simple-assign path applies, so `x, n = makeSlice()` to a `readonly @T` (a const LOCATION — distinct from `@(readonly T)` whose pointer is rebindable; confirmed with the user) compiled an illegal assignment → the multi-assign RefDec'd the uninitialized slot → SIGBUS. FIX: extracted `checkAssignTargetConst` (const-symbol + qualified-const + readonly-location) and applied it per target in the destructure loop (`pkg/binate/types/check_stmt.bn`); `695_err_const_loc_multi_return` pins the compile error; 185 const/assign/multi-return conformance + `pkg/binate/types` unit green; mode-agnostic (rejected at type-check, before codegen). The zero-init-skip below is the crash MECHANISM but now UNREACHABLE (the illegal program no longer compiles); the readonly-not-peeled zero-init classifier (VM `isMultiWordField`/`isVMAddressAggregate`, `resolveToStruct`) stays a separate latent-consistency follow-up (#113 family). ORIGINAL ANALYSIS (root framing was off; kept for the mechanism): `func makeSlice() (readonly @[]int, int) {...}; func main() { var x readonly @[]int; var n int; x, n = makeSlice(); println(len(x)) }` compiled cleanly but CRASHED on LLVM (exit 138 / SIGBUS); native-aa64, native-x64-darwin, VM all correct. Non-readonly control (`@[]int` component) is green everywhere — the `readonly` wrapper is the precise trigger. **Root (LLVM, separate path from the `7f53b9ce` fixes)**: the multi-assign `x, n = makeSlice()` RefDec's `x`'s *old* value (loaded from its slot) before storing the new one, but `var x readonly @[]int` (no initializer) does not get its slot zero-initialized — a readonly-not-peeled classification skips the nil-init — so the RefDec reads a garbage refptr and `(garbage-16)` as a refcount header → fault. Sibling un-peeled classifiers in the same family: VM `isMultiWordField` / `isVMAddressAggregate` (`vm/lower_instr_helpers.bn:90,116`) and `resolveToStruct` peel alias+named but NOT readonly (latent; not the LLVM crash root but should also peel for consistency). **Also suspicious**: a multi-assign into a `readonly` local compiled at all (a direct `x = ...` to a readonly location is FE-rejected) — possible separate readonly-assign-check gap in the multi-assign path. Repro: the makeSlice program above.
- **DECIDED 2026-06-10 (accept the consistency fix) — front-end over-rejection: `@[]T → readonly *[]T` / `@T → readonly *T` rejected**: `var x readonly *[]readonly char = someManagedSlice` is rejected on all four backends ("cannot assign @[]uint8 to readonly *[]readonly uint8"), but the non-readonly `@[]T → *[]T` managed→raw decay IS accepted. Root: `types/types_assignable.bn` `AssignableTo` — the `@[]T→*[]T` and `@T→*T` decay arms test the UNPEELED `dst.Kind` (so `readonly *[]T` whose Kind is the wrapper falls through to `return false`), whereas the interface-value arms just below peel via `resolveAliasAndConst` first. **The user ratified the fix** (the asymmetry is a gap; readonly is strictly more restrictive on capability, so it should decay the same): peel readonly/alias off `dst` before the `TYP_SLICE` / `TYP_POINTER` kind tests (reuse the resolved `d` the iface arms compute), plus `pkg/binate/types` checker unit tests asserting `@[]T` / `@T` assign to `readonly *[]T` / `readonly *T`. NOT YET IMPLEMENTED. Also unblocks `coerceArg`'s managed→raw readonly-peel (binate `487fb95c`).
- **FALSE POSITIVE (recorded so it isn't re-chased)** — "readonly float binop emits integer add": reported wrong by an audit agent (LLVM 2.0625 / native 0.0) but NOT reproducible — `readonly MyFloat` arithmetic gives correct `4.0` on all four backends in clean re-runs (local-var and param variants). The agent's run was contaminated by the concurrent `/tmp` clobbering noted above. `codegen` `unwrapNamed` not peeling readonly is real in the source, but the binop's IR operand/result types arrive already peeled, so it does not manifest.
- **RE-CONFIRMATION (already tracked)** — VM `int → float32` cast yields 0 (compiler backends correct). This is the existing `vm-int-to-float32` bug, already xfailed via `conformance/matrix/scalar` int-to-float cells; the audit independently re-confirmed it via a minimal reproducer. No new action.
