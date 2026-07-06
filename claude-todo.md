# Binate TODO

Tracks open work items, grouped by the subsystem / root cause they touch.
Completed items live in [claude-todo-done.md](claude-todo-done.md).

**BUG BASH 2026-06-27.** Open *bugs* still carry an inline `🏷[BUG-BASH 2026-06-27 → LANE N]`
tag routing them to a parallel-worker lane (1 = front-end `pkg/binate/{checker,types,parser}`;
2 = IR-gen & native codegen `pkg/binate/{ir,codegen,native/*}`; 3 = VM & cross-mode runtime).

---

## CRITICAL

### MAJOR: native aarch64 **ELF** backend silently miscompiles all page-relative data addressing (`R_AARCH64_NONE`) — 🔴 OPEN (found 2026-07-06)

**Severity: MAJOR (silent wrong-code / data-corruption on an accepted target).**
`bnc --target aarch64-linux -backend native` emits **`R_AARCH64_NONE`** for the
low-12 half of every page-relative data address — string literals (ADRP+ADD),
`&global` (ADRP+ADD), and now `__c_global` (the new GOT ADRP+LDR pair). The
linker treats `R_AARCH64_NONE` as a no-op, so the ADD/LDR imm12 field stays 0:
the address gets its page bits but not its offset → wrong pointer → silent
miscompile, **no diagnostic**.

- **Root cause:** `pkg/binate/asm/elf/elf_util.bn` `elfRelocType`'s `EM_AARCH64`
  arm maps only fixup kinds 100–104 and 0 (branch/ADRP/ADR/abs). The low-12
  kinds — **`FIX_ADD_LO12`(105), `FIX_LDR_LO12`(106)** (pre-existing) and
  **`FIX_ADRP_GOT_HI21`(107), `FIX_LD_GOT_LO12`(108)** (added by §5b-1) — fall
  through to `return 0` = `R_AARCH64_NONE`. Worse, the ELF writer
  (`pkg/binate/asm/elf/elf.bn:239`) writes `rtype` straight into `r_info` with
  **no `if rtype < 0` guard** — unlike the Mach-O writer (`macho.bn:384`), which
  errors loud on an unmapped kind. So the ELF path can't even fail loud.
- **Discovered:** the §5b-1 adversarial review (2 skeptics) flagged that the §5b
  aarch64 GOT kinds (107/108) have no ELF mapping; empirically confirmed by
  `objdump -r main.o` from `--target aarch64-linux -backend native -c` on a
  trivial `println("hello")` program: `0x0c R_AARCH64_ADR_PREL_PG_HI21 Lstr_0`
  followed by `0x10 R_AARCH64_NONE Lstr_0` (the string ADD). (Dependency objects
  route through clang/LLVM and show the *correct* `R_AARCH64_ADR_GOT_PAGE` /
  `LD64_GOT_LO12_NC` — clang is the oracle for the right reloc numbers.)
- **Not caught by CI:** there is **no aarch64-linux native conformance/unittest
  mode** (the `native_aa64` mode is macOS/**Mach-O**, whose path is complete and
  correct). So this is a real defect on an accepted, invocable target/backend
  combo that no mode exercises.
- **§5b-1's role:** pre-existing for 105/106; §5b-1 (`b08b0d1e`, aarch64 Mach-O
  GOT) *extends* the same silent-`NONE` fallthrough to the GOT kinds 107/108. The
  **Mach-O aarch64 path §5b-1 actually targets is 100% correct and verified** —
  this bug is strictly the untested ELF-aarch64-native path.
- **Proposed fix (two tiers):**
  1. **Minimal / immediate — fail loud** (upholds the plan §2/§4.7 principle "an
     unimplemented arch must SetError, never silently emit a wrong reloc"): make
     `elfRelocType` return `-1` for an unmapped `(kind, machine)` and add the
     `if rtype < 0 { a.SetError(...) }` guard in `elf.bn`'s emission loop,
     mirroring Mach-O. Turns silent-miscompile → clean error. Safe for the
     tested x64/arm32 ELF paths (fully mapped; never hit −1) — verify by running
     `native_x64` + `arm32_linux` unit/conformance. Note: this makes
     `--target aarch64-linux -backend native` *refuse* to compile any program
     with a string literal (i.e. all of them) — which is the honest state, since
     the feature is unimplemented and CI doesn't use it.
  2. **Proper — implement the native ELF-aarch64 data + GOT reloc mappings**
     (matching clang's output): `R_AARCH64_ADD_ABS_LO12_NC`(277),
     `R_AARCH64_LDST64_ABS_LO12_NC`(286) for 105/106; `R_AARCH64_ADR_GOT_PAGE`
     (311), `R_AARCH64_LD64_GOT_LO12_NC`(312) for 107/108. Naturally bundled
     with adding an aarch64-linux native conformance mode (so it's testable).
- **Bug Discovery Protocol:** no conformance xfail added yet (no aarch64-linux
  native mode to attach one to); this todo entry is the tracker. A unit test
  asserting `elfRelocType` errors (or maps correctly) on the aarch64 low-12 kinds
  is the natural regression guard once a fix is chosen.

### HFA-in-SIMD is a CROSS-BACKEND contract — ✅ RESOLVED for AArch64; Stage 4 (x64) remains — 🟡 OPEN

HFA (Homogeneous Floating-point Aggregate) passing in SIMD registers is a
cross-backend ABI contract — the compiler's LLVM backend, native codegen, every
dispatch shim (func-value / closure / interface, incl. stack-spill), and the VM
boundary must all agree. **✅ DONE & LANDED for AArch64** via the cross-backend
replan (`plan-hfa-crossbackend.md`): all sites built dormant behind
`types.HfaInSimd()`, then flipped ON at `48e3787b` (`HfaInSimd() → Arch==AA64`).
Staging commits `06f9a8ff`/`d69eded8`/`7692508e`/`9ebf4119`/`4bc6fa7c`/`576e7bb3`/
`833576bd`/`48e3787b`; validated by full conformance on builder-comp + native-aa64
plus the cross-module `968` / dispatch `969` / spill `970` tests; each stage
adversarially reviewed. (The earlier native-only enablement `332b4298` was a
CRITICAL cross-backend miscompile — deps/shims route through the LLVM backend, which
GP-coerces HFAs — mitigated by gating off `1a790663`, then fixed by the replan.
Process lesson: the correctness bar is "native matches the Binate LLVM backend +
shims", not "native matches clang".)

**ONLY REMAINING: Stage 4 — x64 SysV eightbyte-SSE HFA** (an independent per-target
effort; `HfaInSimd()` stays false for x64 until then). See `plan-hfa-crossbackend.md`.

### native-aa64 self-hosted conformance: intermittent timeout flakiness — 🟡 OPEN (2026-07-02)

**Severity: minor (CI flake, not a miscompile).** The
`builder-comp_native_aa64-comp_native_aa64` conformance mode intermittently reports
1–2 spurious failures per full 2606-test run: a *correct* compiled test binary that
occasionally hits the runner's `timeout 3` (`conformance/runners/…native_aa64….sh`)
and yields empty output. **Non-deterministic** — different tests fail run-to-run and
none reproduce in isolation. Observed independently on two full runs:
`iota-repeat` + `shr/16/signed` on one tree, `311_err_index_assign_oob` on another
(baseline) — so it is **pre-existing**, not tied to any one change (discovered while
regression-checking the HFA stage-1 landing). The compiled code is byte-identical
across compiles (only Mach-O metadata differs), so this is a timeout-under-load / rare
runtime-slowness issue, not a codegen defect. Possible fixes to investigate: raise the
per-test `timeout` (3s is tight when the full sweep saturates the host), or make the
runner retry a timed-out test once before reporting failure. Until then a red
native-aa64 run with a lone `[3s]` timeout failure is very likely this, not a real
regression — re-run the single test in isolation to confirm.

### func-value SMALL multi-return: native-shim vs LLVM-shim ABI CONFLICT — ✅ FIXED & LANDED (2026-07-06)

**Resolution: approach (a), full unification — see "RESOLUTION" below.**

**LANDED to main 2026-07-06** (`7ea33056..03b019d8`): the native func-value
shim/caller/sizer now use retbuf-for-any-multi-return, matching the LLVM shim +
LLVM caller + VM cross-mode dispatch. Landed commits:
- `db20bf3f` — extract the shared field-per-class store helpers
  (`storeMultiReturnTupleFields{_x64,AA64,Arm32}`) from the `collect*` functions.
- `92b78846` — the unification: caller passes a retbuf for EVERY multi-return
  and stops collecting from return regs; non-capturing + closure + spill shims
  store a small tuple field-per-class through retbuf (big → sret); aa64 gained
  BOTH shim branches (was 971's crash); arm32 small-multiret became a framed
  store-through-retbuf CALL shim; the shared sizer reserves prefixSlots=2 for
  every multiret on ALL CCs incl AAPCS64. Non-goals left untouched:
  OP_CALL_IFACE_METHOD, OP_CALL_INDIRECT's X8 branch, the direct-call collect,
  the VM, and the LLVM/codegen side.
- `6788dd3d` — the cross-pkg tests; `435ee1f0` — wide coverage (974 sub-word,
  975 float3, 976 mixed int/float, 977 managed-field leak canary, 978 GP-max);
  `03b019d8` — refresh comments stale after the ABI flip. NOTE: during landing
  the original tests 971/972 were **renumbered to 979/980** (concurrent SSE work
  had landed `971_cross_pkg_sse` / `972_xpkg_funcval_sse`); 973-978 unchanged.

Reviewed: implementation adversarially reviewed (land-with-nits, 0 blockers) and
the committed diff re-reviewed (land-with-nits). Independent full verification on
the landed base: aa64 2659 pass / 0 fail / 0 hangs; x64 2651 pass (8 pre-existing
build-constraint + stdlib/os fails); arm32 2631 pass (3 pre-existing reflect +
stdlib/os fails); native unit 5/0; hygiene 15/15.

**Follow-up (major, maintainability) — ✅ DONE & LANDED 2026-07-06 (`aaf9a385`):**
arm32's `collectMultiReturnFields` and `storeMultiReturnTupleFieldsArm32` were two
byte-equal copies of the field-per-class loop (x64/aa64 already shared one). Fixed:
generalized `emitFrameStore`/`emitFrameScalarStore` into `emitBaseStore`/
`emitBaseScalarStore` (explicit base + large-offset scratch); the frame variants
became thin wrappers; `storeMultiReturnTupleFieldsArm32` gained base+baseOff+scratch;
`collectMultiReturnFields` now delegates to it (SP base, spill baseOff, IP scratch)
so caller-collect and the func-value shim store share ONE implementation and cannot
drift. Byte-preserving (adversarial-reviewed: wrappers reproduce old bodies exactly,
`argWordsArm32` == `cc.ArgWords`, delegated collect byte-identical; arm32 full
2633/3-preexisting/0-hangs). Added `TestStoreMultiReturnTupleFieldsArm32PerField`.

971/972/973 xfail markers removed; all now PASS on host + x64 + aa64 + arm32.
Full native runs: aa64 2654 pass / 0 fail / 0 hangs; x64 8 fails and arm32 3
fails are ALL pre-existing (build-constraint + stdlib/os + reflect — verified
identical on the pre-fix base `aa81d020`), none from this change. The 40-test
same-module funcval-multiret regression set (incl. 705 float closure multiret,
950 method-value multiret, capturing-closure-multi-return) stays green; managed
leak balance holds (refcount 1) across native↔native and native↔LLVM corners.

**Cross-mode corners NOT covered (harness limitation, surfaced not deferred):**
the two latent-broken corners the review named — LLVM-main → native-DEP small-
multiret, and VM → native-DEP small-multiret — are NOT expressible in the
conformance harness. In `-backend native` the whole program compiles as
main-native + deps-LLVM (a single-invocation hybrid: `compileMainNative`); there
is no per-package backend override to make a DEP native while main is LLVM/VM.
Exercising those corners would need a new harness capability (per-pkg backend
mixing) — a substantial harness change outside this task. The native-shim change
DOES fix those corners (the shim it emits now conforms); they're just untested
here. Flagged for a follow-up harness decision.

**Severity: major (cross-module silent miscompile at the native↔LLVM boundary).**
A func value's `vtable.call` slot may point at EITHER a natively-emitted shim
(same-module closure / func ref) OR an LLVM-emitted shim (cross-package dep). For
a SMALL multi-return tuple (≤ NumGpRetRegs words, e.g. `(int,int)` / `(u16,u16,u16)`)
the two shim families use INCOMPATIBLE return conventions, and the caller
(`emitCallFuncValue`) can't tell which it will reach:

- **LLVM shim** (`pkg/binate/codegen/emit_funcvals_shim.bn`, `emitFuncValueShimAggregate`):
  `void @__shim(i8* retbuf, i8* data, <args>)` for ANY multi-return — gated on
  `types.IsAggregateReturn` = `len(results)>1` — stores the whole tuple THROUGH
  `retbuf` (first arg) and returns void. Caller must pass retbuf + read the result
  from it.
- **Native shim** (x64 `pkg/binate/native/x64/x64_funcvalue_shim.bn`
  `isBigMultiReturn_x64` → `MultiReturnTupleNeedsSret`; aa64
  `pkg/binate/native/aarch64/aarch64_funcvalue_shim.bn` `shimReturnSize`→0 for any
  multi-return → scalar/void tail-branch; arm32 `arm32_funcvalue_multiret.bn`):
  a SMALL multi-return is returned FIELD-PER-REGISTER (RAX/RDX/RCX·XMM / X0..X4 /
  r0..r3). Caller must collect from return registers.

**Symptom that surfaced it:** `conformance/971_funcval_xpkg_big_multiret` (a BIG
`(int x5)` cross-pkg multi-return) crashes on aa64: the LLVM shim writes retbuf
(X0) but the aa64 caller passes data in X0 and collects from return regs → garbage
/ empty output. On x64/arm32 971 works only coincidentally (their smaller
NumGpRetRegs makes `(int x5)` needs-sret, so their `useRetbuf = aggregateRet ||
bigMultiRet` already fires). SMALL cross-pkg multi-returns are LATENTLY
miscompiled on all three (shim uses retbuf; caller collects from regs).

**Why the obvious fix is WRONG:** the recon proposed keying the caller on
`IsMultiReturnCall` (retbuf for ANY multi-return, matching the LLVM shim) across
all 3 backends + the shared sizer. Implemented and tested, that fixes 971 + new
small cross-pkg tests BUT regresses 40 aa64 tests (all SAME-module
`funcval-multi-return` matrix cases int/2..5, u16/2..5, f64, managed variants,
`705_func_value_closure_float_multi_return`, `950_method_value_multiret`,
`regressions/capturing-closure-multi-return`, etc.) — because it breaks the
caller's agreement with the NATIVE shim, which returns small multi-returns in
registers. The two conventions genuinely disagree and the call site can't
distinguish the callee statically. (Reverted; not landed.)

**Proper fix (needs a design decision — user to prioritize):** make the two shim
families agree on ONE convention for func-value multi-returns, then key the caller
on it. Options: (a) make the NATIVE shims use the retbuf-for-any-multi-return
convention too (match LLVM's `IsAggregateReturn` gate) — then the caller keys on
`IsMultiReturnCall` as the recon proposed, and both same-module and cross-package
agree; requires changing `isBigMultiReturn_*` / `shimReturnSize` and the native
shim emitters + their spill variants + collectMultiReturnFields expectations. Or
(b) make the LLVM shim return small multi-returns in registers (drop the retbuf
for small tuples) to match the native register-return convention — but the LLVM
shim currently leans on `IsAggregateReturn` and first-class-struct return
lowering, so this is the harder side. Option (a) is likely cleaner (retbuf is the
uniform shape single-aggregate returns already use). Either way it is a
cross-backend contract change (all 3 native backends + codegen + VM cross-mode
dispatch must agree), NOT a caller-only tweak.

**Repro / coverage (all on the CURRENT/baseline tree, no fix applied):**
- `conformance/971_funcval_xpkg_big_multiret` (BIG `(int x5)` cross-pkg; present,
  no xfail): FAILS aa64 (garbage/empty); PASSES x64 + arm32 (coincidental —
  needs-sret there).
- new `conformance/972_funcval_xpkg_small_multiret2` (`(int,int)`) and
  `973_funcval_xpkg_small_multiret3` (`(int,int,int)`): SMALL cross-pkg; FAIL on
  **all three** native backends (x64 / arm32 / aa64) with garbage output (e.g.
  arm32 973 → `50 / 0 / 1073752014`). PASS on host (builder-comp) — host has no
  native-shim/LLVM-shim split. These are the direct demonstration of the latent
  small-multi-return miscompile.

The 40-test aa64 SAME-module set (funcval-multi-return matrix int/2..5 · u16/2..5 ·
f64 · managed variants · `705` · `950` · `regressions/capturing-closure-multi-return`)
is the regression guard: any candidate fix must keep those green while making
971/972/973 pass. NOTE: 972/973 currently have NO xfail markers and FAIL on the
three native modes — either add `.xfail.<mode>` markers (per Bug Discovery
Protocol) or land the real fix before these tests go into a default CI run.

**RESOLUTION (2026-07-05): approach (a) "retbuf for any multi-return everywhere", full
unification. Adversarial-reviewed (3 lenses + synthesis); verdict SOUND-WITH-FIXES.**

Key correction from review: (a) is the AUTHORITATIVE target — the LLVM shim
(`emit_funcvals_shim.bn`), the LLVM caller (`emit_call_funcvalue.bn`), AND the VM
cross-mode dispatch (`vm_exec_funcref.bn` `dispatchCompiledFuncValue` →
`_call_shim_aggregate`, keyed on `AggregateReturnSize` which is nonzero for
`len(results)>1`) ALREADY use retbuf-for-any-multi-return. So **NO VM change and NO
LLVM/codegen change are needed** — only the native compiled shims + caller + sizer
diverge. (The VM→native-shim and LLVM-main→native-dep small-multiret paths are
*also* latently miscompiled today — extra motivation.)

Corrected native-side plan (5 blockers the naive plan missed):
1. **Store helper.** The new small-multiret shim store must reproduce
   `collectMultiReturnFields`/`collectMultiReturnTuple` FIELD-PER-REGISTER-BY-CLASS
   (GP fields from X0../RAX,RDX,RCX at `FieldOffset` with SIZED stores; float-scalar
   fields from D0../XMM0.. via `Fmov`, x64 3rd/4th float via x87 FSTP; independent
   GP/FP class counters). Driven off the tuple TYPE (`funcValMultiReturnTuple_*`),
   NOT `shimReturnSize` (=0 for any multi-return). Extract a shared raw-register
   store-to-memory helper (no RegMap — shims have none). Do NOT reuse the `usePack`
   `retSz/8` GP-word dump.
2. **aa64 has NO working multi-return shim path today** (big OR small — everything
   tail-branches through the scalar fallthrough; this is the 971 crash). Build BOTH
   a big (Mov X8,X0 → sret) and small (store-through-retbuf) branch before the
   scalar fallthrough; set `userBudget=6`; thread a multi-return flag into
   `emitFuncvalSpillShimAA64`.
3. **Closure shim family** is a 2nd field-per-register consumer (x64
   `x64_closure_shim.bn` + `_aggregate` + `_float`; aarch64 `aarch64_closure_shim.bn`
   + `_float` + `_aggregate`). Captured-receiver method values route here, so
   flipping the caller regresses same-module `705`/`950`/`capturing-closure-multi-return`
   unless these flip too. (arm32 exempt — rejects closures loudly.)
4. **Both spill shims** (`aarch64_funcvalue_spill.bn`, `x64_funcvalue_spill.bn`)
   hardcode the old convention (aa64's doc: big-multiret arrives scalar-shaped with
   X8 preset by caller). `srcPrefix=2` for any multiret; big → set sret ptr and
   forward; small → stash retbuf + field-per-class store.
5. **arm32 small-multiret** is a pure tail-branch (`emitScalarVoidShim`) → becomes a
   framed store-through-retbuf CALL shim (save retbuf in R4, drop retbuf+data, shift
   args, BL, store r0..r3 at FieldOffsets, pop). SHIM_ARG_REG_BUDGET drops by one.

Majors: aa64 caller passes retbuf in **X0** for ANY multiret (NOT X8-from-caller —
delete the caller-sets-X8 `bigMultiRet` block + its post-call `return`; the shim now
does `Mov X8,X0` internally), and REMOVE the stale `collectMultiReturnFields` for
the retbuf'd case; leave `emitCallIndirect`'s X8 branch (OP_CALL_INDIRECT) alone. The
shared sizer must give `prefixSlots=2` for EVERY multiret on ALL CCs incl. AArch64
(`aggregateRet || (ins.ID>=0 && IsMultiReturnCall(ins))`, NOT gated on
`SretInGpArgReg`); edit only the OP_CALL_FUNC_VALUE/OP_CALL_HANDLE branch, leave
OP_CALL_IFACE_METHOD untouched. Caller (step 2) + sizer (step 4) are a MATCHED ATOMIC
PAIR per backend (mismatch = PlanFrame outgoing-args overlap = silent stack
miscompile). Managed multiret: plain pointer-word store (no RefInc/RefDec), no
double-collect.

Non-goal (verified): OP_CALL_IFACE_METHOD is direct ivt dispatch (no shim) — keeps
register-based multi-return collect + its sizer branch unchanged. (But method VALUES
with a captured receiver are closures → covered by blocker 3; re-verify `950`.)

Tests: update `common_call_test.bn:266` (BIG-multiret AAPCS64 sizer prefix 1→2, WILL
break), `x64_funcvalue_shim_test.bn:33`, `arm32_funcvalue_test.bn:206`+`:226`,
`arm32_call_indirect_test.bn:353`, `arm32_funcvalue_multiret_test.bn`; ADD a
small-multiret sizer test (prefix=2 all CCs incl AAPCS64); remove xfails on
971/972/973; ADD wide cross-pkg coverage — sub-word (u16 xN), float-scalar
((f64,f64,f64)/(float32,int32)), mixed int/float, managed-field (+ leak checker),
GP-max (int x8 on aa64), a spilling-arg boundary case, and the two latent cross-mode
corners (LLVM-main→native-dep, VM→native small-multiret). Smoke native/common +
native/x64 + native/aarch64 + native/arm32.

**⚠ CI-visibility gap (verified 2026-07-05):** 971/972/973 are **NOT on `main`** —
they live only on an unmerged branch (`458329f0`, verified not an ancestor of
HEAD; `conformance/` on main has 879/881/882 but no 971–973). So on `main` this
latent cross-package small-multi-return miscompile is **entirely untested in CI**.
**Follow-up:** land the demonstrators on `main` with `.xfail.<mode>` markers (per
the Bug Discovery Protocol) even ahead of the real fix, so the regression is
CI-visible rather than silently absent. (The fix itself is the shim-ABI contract
decision above — a separate, user-owned call.)

---

## Language features — specified, not yet implemented

### Methods on generic types + parameterized-receiver impls — spec'd 2026-07-05, NOT implemented — 🔴 OPEN

Let a generic type carry methods and satisfy interfaces — the missing piece that
makes generic interfaces (`Iterator[T]`, `Container[T]`) *implementable* (today
declarable-only; a generic type currently has no methods → satisfies no
interface). **Specified** (§12.1 `gen.method.generic-recv` / `gen.impl.generic-recv`,
`gen.no-generic-methods` narrowed to method-level params only; §11.3
`iface.impl.form`; §10.1/§10.4; grammar `ReceiverType`/`ReceiverBase`), **not
implemented**. High-level plan: **[plan-generic-type-methods.md](plan-generic-type-methods.md)**.
Model: `func (it *Cursor[T]) Next() (T, bool)` — receiver **binds** the type's
params (constraints inherited, no method-level params); `impl *Cursor[T] :
Iterator[T]` — parameterized-receiver impl (coverage checked abstractly, vtable +
distributed-satisfaction-entry per monomorphized instantiation). Method-level type
params (`map[U]`) stay forbidden (vtable slot would vary). No run-time generic
dispatch. Makes the §12.4 constraint-check gap load-bearing (per-instantiation
satisfaction). Distinct enabler for the whole generic-container-with-behavior story.
**Overlap coherence question — RESOLVED 2026-07-06 (forbid specific-instantiation impls).**
The parameterized-vs-specific-instantiation impl overlap is removed by forbidding
specific-instantiation impls (`impl Cursor[int] : I`) in v1 — verified used by ZERO
code repo-wide + referenced by no other rule (vestigial). §12.4
`gen.no-conditional-impls` now disallows both conditional and specific-instantiation
impls; the parameterized form is the single mechanism. (docs `16a8ca3`.)

### Type assertions, type switches & RTTI — IN PROGRESS (RTTI substrate landing incrementally) — 🟡 OPEN

**Progress (2026-07-04):** the RTTI substrate is landing per
[plan-type-assertions-execution.md](plan-type-assertions-execution.md).
- **Phase 1 — ✅ LANDED `0734beaa`:** the vtable any-block grew from 1 to 2 words
  (dtor + `*TypeInfo` placeholder), method slots re-based across all backends + VM.
- **Phase 2.1 — ✅ LANDED `041a6954`:** one weak `__typeinfo.<T>` record per boxable
  type (fixed 7-word layout, all fields zero/null), vtable slot 1 wired to it — the
  per-type *identity* substrate.
- **Phase 2.2a — ✅ LANDED `8047a72c`:** the record's `size`/`align` filled from the
  receiver's laid-out type (design A: `ImplInfo.RecvTyp` held, `SizeOf` read at
  codegen; see below). Adversarially reviewed — byte-identical cross-TU records
  verified by compiling `378` + nested-import + ILP32.
- **Phase 2.2b-1 — ✅ LANDED `9eba70eb`:** word 0 destructor handle, filled from the
  SAME helper the vtable any-block slot 0 uses (LLVM `implDtorSlotSym`; extracted
  native `dtorSlotSym_x64` / `dtorSlotSymNative`) → byte-identical to that slot by
  construction. `TypeInfoDesc` carries neutral `DtorFuncName`; each backend resolves
  the prefixed `DtorSym`. No-dtor type → null word (reloc-free `rodata`); dtor type →
  relocation moves the record to `rodata_relro`. Native TypeInfo-emission split into
  new `<arch>_typeinfo.bn` (+ tests). Adversarially reviewed (correctness +
  refactor-safety, built/emitted-LLVM/mutation-tested; no defects).
- **Phase 2.2b-2 — ✅ LANDED `88e913af`:** name (words 3–4) — a TU-local rodata blob
  holding `RecvTyp.QualifiedTypeName()` (canonical/path-dotted, e.g. `main.T`) +
  word-3 pointer + word-4 length. `BuildTypeInfo → @[]@DataGlobal` (`[record,
  name-blob]`); word 3 gated on name presence. Added `mangle.TypeInfoNameBlobName`,
  exported `types.QualifiedTypeName`. Consequence: the name-pointer relocation moves
  EVERY named record to `rodata_relro` (native section + vtable-shape tests updated).
  Adversarially reviewed — clean.
- **Phase 2.2b-3 — satisfaction, DECIDED PLAIN-DISTRIBUTED (not a per-type table).**
  User's call (spec-grounded: third-party impls are allowed, so a per-type table
  can't be complete AND needs coalescing surgery; a per-`(T,J)` entry is
  byte-identical weak_odr like a vtable → no TU-invariance blocker; Go's itab model).
  Record words 5–6 (sat_len/sat_table) were **✅ dropped `89ad8b18`** — the record is
  now the fixed 5-word `[dtor, size, align, name-ptr, name-len]`, matching the
  (already-updated) spec `type.layout.typeinfo`. Slices:
  - **3a — ✅ LANDED `a04ae1b8`:** per-interface `__ifaceid.<J>` identity markers
    (weak 1-byte rodata; `mangle.IfaceIdName`; `ir.BuildIfaceId`/`CollectIfaceIdSyms`;
    emit pass in LLVM/x64/aarch64). Adversarially reviewed — identity-consistency
    (marker vs future SatEntry/assertion) verified across cross-pkg/alias/generic/any.
  - **3b — ✅ LANDED `e12a0a0d`:** per-`(T,J)` `SatEntry{&TypeInfo(T),&IfaceId(J),
    &__ivt.<T,J>}` weak globals, one per m.Impls row (transitive ancestors + `(T,any)`
    included). `mangle.SatEntryName`; `ir.BuildSatEntry`/`CollectSatEntries`; emit pass
    in LLVM/x64/aarch64. Also decoupled the native vtable-shape tests from the RTTI
    satellites. Adversarially reviewed — 0 dangling refs across 11 programs.
  - **3c-1 — ✅ LANDED `e14407dc`:** wire the satentries into the reflect descriptor.
    Each `__satentry.<T,J>` became a managed `reflect.SatEntryInfo` node (header +
    inline {Type,Iface,Vtable}); `Package.SatEntries *[]@SatEntryInfo` (after Vtables)
    lists them, so `__Package` root → descriptor → nodes → referents keeps them alive
    once `__Package` is rooted (by the reader/VM ingestion) — like the vtable
    descriptor. arm32+VM pass empty. Adversarially reviewed (incl. linked-binary nm).
  - **3c-2 — ✅ LANDED `89108b34`:** VM ingestion — `RegisterPackageSatEntries`
    reads `p.SatEntries` into three parallel per-`@VM` slices via `registerSatEntry`
    (new `pkg/binate/vm/satentry_inject.bn`), host-wired at `injectPure` /
    `injectPackageSet` (the two sites the vtable trio uses). No `lookupSatEntry` yet
    — its only consumer is the Phase-5 reader (a dead file-private func would trip
    bnlint), so it lands with that reader. Inert (populated-but-unread) until then,
    like 3a/3b/3c-1. Adversarially reviewed — 0 defects across 11 check points
    (descriptor read, nil-slice safety, reachability, field placement, iface/impl
    agreement, refcount, wiring completeness, test adequacy, BUILDER-tree
    untouched). Note: the earlier "split `emit_impls_test.bn`" follow-up is moot —
    `file-length.sh` excludes `*_test.bn`.
  - **Phase 5:** the reader — global `(TypeInfo,IfaceId)→subvtable` lookup + assertion
    /type-switch lowering.
- **Remaining after 2.2b-3:** the front-end (Phases 3–7: parser/checker/lowering for
  `x.(K T)`, comma-ok, type switches, the §17.5 panic), plus the cross-mode/VM story
  deferred to Phase 5.

**🔧 TODO (detailed) — migrate the TypeInfo record content to a per-type "boxable
types" registry ("design D").** Increment 2.2a fills `size`/`align` via **design A**:
a `RecvTyp @types.Type` field on `ir.ImplInfo` (the `DtorFuncName` twin), captured at
`ImplInfo` creation (`collectImplsFromDecl` via `resolveTypeExpr(gc, d.TypeRef)` peeled
to the base value type; `ensureAnyImplInfo` via `val.Typ.Elem`), with `SizeOf`/`AlignOf`
read at **codegen** (`CollectTypeInfoDescs` in `pkg/binate/ir/data_typeinfo.bn`, after
the in-place field-population pass — computing at collection reads an empty struct → 0).
Design A was chosen (2026-07-04, after a 3-investigator + 2-adversarial-critic
review) as the correct, low-risk *first increment* of the cleaner long-term shape,
**design D** — a per-type registry holding the laid-out `types.Type`, keyed by the
`mangle.TypeInfoName` symbol, that is the single home for *all* record content
(size/align/name from the held type; dtor + satisfaction table from the `m.Impls`
grouping). Why migrate to D eventually:
  - **A hangs a per-*type* fact on a per-*(type,iface)* record.** `ImplInfo` is one
    row per `(receiver, interface)`; `RecvTyp` (and name, later) is a per-type fact
    stored redundantly on all N rows and deduped in `CollectTypeInfoDescs`. Harmless
    now, but awkward as the record grows (name, reflect fields).
  - **The satisfaction table (2.2b) forces a per-type home anyway** — it's built by
    grouping `m.Impls` by receiver (the transitive-ancestor set), which is exactly a
    per-type structure. That per-type descriptor list IS the seed of D; A is not
    throwaway, it's D's first increment.
  - **Optional tightening (defer):** make the D registry the *single seam* that BOTH
    `collectImplVtableSlots` (vtable slot-1) and `BuildTypeInfo` read, so the
    "record symbol == slot reference" invariant holds by construction instead of via
    two independent `mangle.TypeInfoName` call sites. This refactors landed/tested 2.1
    slot wiring, so it's a separate, later step — not part of 2.2a/2.2b.
Rejected alternatives (with reasons, from the review): **B** (name→`m.Structs`
round-trip at emission) — second divergence-prone identity derivation + a stub-window
weak-def miscompile risk + misses primitives; **C** (compute in the checker) — moves
record authorship upstream of where the record lives (`pkg/types`, read by IR-gen) and
forces reconciling the checker's bracket-form generic-instance names against IR-gen's
`__bn_inst__` form (silent-if-wrong). **Guards design A must keep** (silent-miscompile
prevention): null-guard `RecvTyp == nil` in the collector (`SizeOf(nil)` returns
`ptrSize()`=8, *not* 0 — a plausible-wrong size), and assert `RecvTyp.Kind` is sane
(not `resolveTypeExpr`'s `TypInt()` fallback for a non-primitive) during bring-up so a
resolution regression fails loudly; plus a multi-TU byte-identical check
(`conformance/378_iface_impl_dup`) for the weak-def TU-invariance hazard.

Go-style downcasting from an interface value to a concrete type or narrower
interface, plus the `TypeInfo` RTTI substrate. **Specified** in the spec (§11.12
`iface.assert`/`iface.assert.kind`/`iface.assert.absent`/`iface.typeswitch`/`iface.rtti`;
§7.13.14 `type.layout.typeinfo` + §7.13.8 any-block `*TypeInfo`; §13.8
`expr.type-assert`; §14.10 `stmt.type-switch`; §17.5 failed-assertion panic) but
**not implemented**. High-level plan (adversarially reviewed — 3 criticals + 4
majors fixed before landing): **[plan-type-assertions.md](plan-type-assertions.md)**
(a follow-up worker expands it into ordered steps). Model: source `*I`/`@I`
(incl `*any`); target = nameable type with mandatory `*`/`@`/value recovery kind
(`@I`→`@T`/`*T`/value, `*I`→`*T`/value, `@T`-from-`*I` rejected); concrete match =
exact identity, interface match = satisfaction **incl transitive ancestors**; both
`x.(K T)` (aborts) and `v, ok := x.(K T)`; type switch (no `case nil`, unset→default,
typed-nil→its type); RTTI via a `*TypeInfo` in the vtable any-block (identity +
dtor + size + align + name + transitive satisfaction-table), one per type
program-wide, cross-mode agreement on the *result*. **Highest implementation risk:
the any-block grows to 2 words, re-basing every vtable method slot** — all backends
+ VM must apply it consistently. Open (no sum types). Seeds the future reflection
surface (§20.3).

---

## Method values & function values (codegen)

### Function values — residual follow-ups (the MAJOR PROJECT landed) — 🟡 OPEN (low priority)
Function values are done across all three phases (archived in [claude-todo-done.md](claude-todo-done.md):
Phase 1 non-capturing + type/vtable machinery, Phase 2 closures/capture — `plan-function-values-phase-2.md`
is "COMPLETE (shipped)", conformance 338–344 + 501/508–510/513…, Phase 3 cross-mode trampolines).
Residual:
- Broader cross-mode trampoline signature shapes beyond `TrampolineScalar` (floats, aggregates, >7 args) —
  add when a path actually reaches them.
- Recursive lambdas (`var f = func(x){ … f(…) … }`) — non-goal during Phase 1; revisit now that Phase 2
  capture is settled (Y-combinator is the current workaround).
- Downstream interop hand-off (package descriptor; retiring ~30 hand-written `vm_extern` arms) is tracked
  under "Compiler/interpreter interop — MAJOR PROJECT".

### 🏷[BUG-BASH 2026-06-27 → LANE 3] cross-mode coerced-agg func-value ABI — residual native-shim follow-ups
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

### 🏷[BUG-BASH 2026-06-27 → LANE 3] MINOR — cross-mode interface dispatch: residual LP64/HFA/upcast gaps (2026-06-14) — 🟡 OPEN

The shim-route that dispatches a native-only package's interface methods from
bytecode (landed `93f75f27` + the math/big extension `7c3b17a2`) is exercised by
726 (`strings.Builder` via `io.Writer`: a raw-slice arg, a scalar arg, a no-arg
method; scalar + multi-return) and 577 (`errors.Error`: no-arg, multi-return).
An adversarial review found four more shapes UNTESTED — each needed a SYNTHETIC
native-only test package, since no stdlib impl hits them. ✅ NOW COVERED by
`e2e/xmiface.sh` (main `7f15b1e9`, 2026-07-01): a custom host injects a fixture
package's `__Package()` into the VM inject-set (`Interp.isCompiled` → its impls
dispatch natively) while the dispatching main runs as bytecode —

- A VALUE-receiver iface method (the iv-dispatch thunk deref; `a0` = the iv-data
  ptr the thunk derefs; 410 covered native-to-native only) — `Double()` → 42.
- A method with MULTIPLE aggregate args (the `a1/a2` by-address slots) —
  `Combine(Pair,Pair)` → 110.
- A FLOAT arg (the shim's int-slot → FP bitcast path) — `Scale(2.5)` → 20.
- The `n>6` user-arg overflow guard (a negative test) — the loud vmPanic, which,
  being specific to the cross-mode path, also proves the fixture is genuinely
  native-injected (a bytecode-lowered fixture would print 28, not panic).

Residuals (still open):

Latent, LP64-host-only (NOT active — default VM modes run a 64-bit host):
- `dispatchCompiledIfaceMethod`'s `resultSize > 8` aggregate-vs-scalar threshold
  (and `dispatchExternBinding`'s identical one) must track `isAggregateReturn`'s
  `> target.PointerSize`; on an ILP32 VM host a 5–8-byte aggregate return would
  pick the wrong shim shape. (Now commented in `vm_exec_iface.bn`.)
- 64-bit-scalar args pack as 2 slots on a 32-bit host (`argSlots`); the dispatch
  reads them as positional shim args.

Separately (PRE-EXISTING, independent of the VM): the native backend has no HFA
classification — a struct of ≤4 same-kind floats (an AAPCS64/SysV Homogeneous
Floating-point Aggregate) is passed as a GP aggregate, because the arg classifier
(`common_call.bn:156`) only special-cases SCALAR floats (`IsFloatScalarTyp`), with
no struct-of-floats → SIMD branch; the LLVM side relies on LLVM to classify HFAs.
**NOT a reachable native-dispatch miscompile** (verified 2026-07-02: 2-double,
3-double/24B, 4×float32, and float-struct-return iface dispatch all pass on native
aa64 + x64) — native is SELF-CONSISTENT (caller + callee both use GP), so pure-native
is correct. It is a latent **ABI-NONCONFORMANCE**: native uses GP where the standard
ABI uses SIMD (v0–v7 / XMM), so a mismatch is reachable only at a cross-ABI boundary
— a C-extern with an HFA-by-value arg (rare), mixed LLVM/native modules (not a normal
build), or a VM→native cross-mode dispatch of an HFA-struct arg (the `e2e/xmiface`
coverage tested only a scalar float, not an HFA struct). **In progress** (2026-07-02,
user-requested): classify HFAs → SIMD in the native arg/return classifier on aa64 +
x64 to match AAPCS64/SysV. See `plan-native-hfa-abi.md`.
  - **Stage 1 (aa64 HFA ARGS) was landed (`332b4298`) then GATED BACK OFF
    (`1a790663`, 2026-07-02) — see the CRITICAL "HFA-in-SIMD cross-backend mismatch"
    entry at the top of this file.** The native aa64 arg path is AAPCS64-correct
    (verified against a clang caller), but enabling it native-only produced reachable
    wrong-code / SIGSEGVs: an adversarial review found the LLVM backend GP-coerces
    float structs to `[N x i64]` (so native-main↔LLVM-dep HFA calls disagree), the aa64
    dispatch shims GP-marshal, and the variadic NSRN walkers drop a fixed FP arg after
    an HFA. The classifier + emitters remain in-tree, dormant. `conformance/963` and
    `964` still pass (both backends GP again). **HFA can only flip on once the LLVM
    backend + dispatch shims + variadic walkers classify HFAs identically — it is a
    coordinated CROSS-BACKEND project, not a native-only stage.**
  - **Replan needed**: the old "stage 1 = native args, stage 2 = native return, …"
    decomposition is wrong (each piece must land in native + LLVM + shims together, and
    the flag flips on only at the end). See `plan-native-hfa-abi.md`.
  - Note: full float32 HFA *value* verification is also blocked by the separate CRITICAL
    float32 expression-typing miscompile (top of this file).

**Native-source iface UPCAST offset>0 — ✅ FIXED & LANDED (`7f832f64`,
2026-07-02).** The VM's `BC_IFACE_UPCAST` native-source branch
(`vm_exec_iface.bn`) advances the native vtable word by `offset*8`, mirroring
`emit_iface_upcast.bn`. A REAL-parent upcast (offset>0) advances the word to the
parent sub-block — INTERIOR to the base `@__ivt` — and a method call on the
result used to do `lookupShimVtable(base + offset*8)`, an exact-match MISS →
loud "no shim vtable" abort. The old "unreachable, no stdlib interface extends
another" claim was WRONG: the embeddable interp (`Interp.New` with a custom
inject-set) lets an embedder inject a native package whose `interface B : A` is
dispatched from bytecode — a valid program that aborted (surfaced by the user,
2026-07-02). Fix: carry each vtable's slot count in `reflect.VtableInfo.SlotCount`
(threaded through `ir.PkgVtableEntry` + `buildVtableInfoNode` + all four gathers —
codegen, native x64/arm32/aarch64, and the VM bytecode gather) and make
`lookupShimVtable` a bounded RANGE lookup: match the vtable whose extent
`[base, base + SlotCount*8)` contains the word, return `shim + (rawAddr − base)`;
out-of-extent → 0 (loud abort preserved). Offset 0 (`@X→@any`, `@X→*X` decay)
resolves to the shim base exactly as before. Coverage: `e2e/xmiface.sh`
(`cross-mode-iface-parent-upcast`: native-injected `Ext : Base` + a 3-level
`C1 : B1 : A1` transitive upcast, offset>1; and a VALUE-receiver parent method AT
offset>0 — case (g), `80cf34b6` — proving the iv-dispatch thunk resolves through
the range-lookup-selected shim slot) + `pkg/binate/vm` `vtable_inject`
(interior/boundary/out-of-extent) + descriptor unit tests. Adversarially reviewed
(no bugs). No known coverage gaps remain.

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

Dual-mode execution (compiled and interpreted code calling each other, both
directions) is a core Binate promise. The substrate this entry originally
sketched has largely LANDED; the **live tracker for what remains is the "Package
descriptors (Phase B)" entry above.**

**Done:**
- **Substrate verified** (not just assumed): cross-mode in-memory type layout
  (shared `pkg/types` SizeOf/AlignOf/FieldOffset) and cross-mode refcounting
  (shared `rt.RefInc`/`RefDec`/`Free`, per-type dtor via the header) — both
  exercised end-to-end through the static-managed sentinel.
- **Function values** — the named prerequisite (`plan-function-values*.md`): the
  2-word `{vtable,data}` rep, `__shim.<mangled>` shims, `dispatchCompiledFuncValue`
  (via `rt._call_shim_scalar`), and the compiled-side `TrampolineScalar` are all in
  place.
- **The package descriptor** — became `reflect.Package` / `__Package()`; works in
  compiled AND VM modes for the builtins (`d4edd671`, `conformance/532` green in all
  6 modes), with Globals + Vtables tables populated for cross-environment parity
  (`55ebcfce`). This settled the old design questions (descriptor name, layout,
  self-descriptor, globals exposure).
- **Cross-mode dispatch** (interpreted↔compiled via the func-value shims) — the
  observable shapes are covered (e2e `7f15b1e9` + the func-value ABI-matrix work;
  residual native-shim ABI gaps are tracked in the cross-mode-dispatch section
  above).
- **VM name→function-value registry** (the entry's "lighter-weight first step") —
  landed as `registerPackageDescriptorExterns` / `RegisterPackageFunctions` plus
  the embeddable-interp host-registration path.

**Remaining (all tracked in the "Package descriptors (Phase B)" entry above):** the
GENERAL Functions-table for USER packages — codegen emits a per-package `Functions`
table (name + signature + function value per exported func) and the VM
auto-enumerates all packages' tables via a cross-package registry, replacing the
hand-maintained `RegisterStandardExterns`. Then Phase C: richer type metadata /
RTTI for reflection + type assertions.

### Embeddable-interp — open follow-ups (Inc 2 extern cleanup core landed) — 🟡 OPEN (2026-06-20)

The embeddable-interp core (Inc 1, Inc 2 Layers 1/2 + the review (b)-fix, and the
loader de-rooting) is **✅ DONE & LANDED** — full detail in
[claude-todo-done.md](claude-todo-done.md). Plan:
[`plan-embeddable-interp.md`](plan-embeddable-interp.md). Remaining open
follow-ups (deferred with user sign-off):

- **Interpreted `__c_call` guard — ✅ DONE & LANDED (`da3bd46a`, 2026-07-02),
  at the FRONTEND (not lower-time).** Interpreted code that uses `__c_call` now
  errors at type-check (`Checker.Interpreted` → `checkCCall`), and injected /
  compiled-instance packages load INTERFACE-ONLY (`Loader.InterfaceOnly`), so
  their native-only `__c_call` impls are never parsed/checked/lowered on the
  interp path (which also fixes the old `os.Seek`/`cLseek` silently-broken-
  bytecode problem — the impl isn't lowered at all). Covers the run path
  (`TypecheckAll`) and the REPL (define + import, both initial-load and
  mid-session-at-the-prompt). The earlier idea of a *lower-time* impl check was
  rejected by the user ("too late — do it at the frontend"). Coverage:
  conformance 961 + `TestCheckCCallInterpretedRejected` + e2e/repl.sh
  `tier5-mid-session-import-ccall-rejected`.
- **`--test`-path frontend guard — ✅ DONE & LANDED (`1de21404`, 2026-07-02).**
  `TypecheckPackages` now sets `Checker.Interpreted`, and `cmd/bni` runTests wires
  `Loader.InterfaceOnly = interp.NativeOnlyInterfacePaths(cli.Filenames)` (the
  native-only set — rt + bootstrap + every pkg/std package — minus any that are
  themselves `--test` targets). So the `--test` path now rejects interpreted
  `__c_call` at the frontend exactly like the run path and REPL: a `__c_call`
  package run as its own `--test` target gets a clean "cannot be interpreted"
  type error instead of `lower_instr`'s default-arm abort, and injected
  dependencies load interface-only. This ALSO closed the older "runTests /
  `IsNativeOnlyInVM` unification" follow-up — the runner's interface-only set now
  derives from the same source (`stdPkgs`) as the skip predicate. Coverage: interp
  unit tests (`NativeOnlyInterfacePaths` × 4 target-set cases +
  `TypecheckPackages`-sets-`Interpreted`); adversarially reviewed (no bugs).
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

## VM runtime faults & the rt.Exit/abort/panic paradigm

### rt.Abort/rt.Panic Plan 2 — make user-code VM faults recoverable (host survives) — 🟡 SCOPE REQUIRED (2026-06-20)

**Related robustness gap (filed 2026-06-30):** a bad-pointer deref inside a NATIVE EXTERN
called from the VM (e.g. handing a wild pointer to `rt.Refcount`) SIGSEGVs the VM host with
NO guard — it is not one of the 6 guarded VM user-fault sites (bounds/divide/shift/nil-deref/
stack-overflow/call-through-nil), and there is no signal handler in `pkg/binate/vm` / `cmd/bni`
/ `rt`. Surfaced while resolving the "VM refcount halt" probe-artifact (see done file). If
this VM-fault-recovery work is picked up, the native-extern boundary should be considered too.

Plan doc: [`plan-rt-abort-panic.md`](plan-rt-abort-panic.md). **Plan 1 (the
`rt.Abort`/`rt.Panic` primitives, the `panic()` single-string + lowering change,
and the VM internal-abort migration through `panic()`) is DONE & LANDED** — see
claude-todo-done.md.

User-code runtime faults (bounds / divide / shift / nil-deref / stack-overflow /
call-through-nil) should be RECOVERABLE in the VM (the host REPL / test-runner /
embedder survives a bad interpreted program) while staying fatal in compiled
code. The 6 VM user-fault sites are deliberately still on `rt.Exit(1)` pending
this. Approach (per user): rt is already injected into the VM, so a faulting user
op already calls the *injected* `rt.Panic`/`rt.Abort`; inject a VM-specific
variant that unwinds the VM's DATA-stack frames (`vm.Stack`) back to `CallFunc`
instead of killing the host (no longjmp — the user call stack is data, not the
host stack). Open: the exec-loop unwind mechanism + refcount-correct frame
teardown.

Related smaller follow-up: route panic / `runtime error:` / VM diagnostics to
**stderr** (fd 2) — deferred out of Plan 1 (infra exists: `bootstrap.Write(fd)`,
`bootstrap.STDERR = 2`); a real behavior change for anything scraping them off
stdout.

### `rt.Exit` paradigm: `exit` vs `abort`/`panic` — DISCUSS
- `rt.Exit` (→ libc `exit`) is the wrong model in general: process exit
  is meaningless in an embedded/freestanding environment, and the
  runtime mostly invokes it for *abort* conditions (OOM, bounds-fail,
  refcount corruption). `abort`/`panic` is likely the right paradigm.
- Surfaced 2026-06-03 alongside the `__c_call`/drop-libc work; that
  change preserves `Exit`→`exit` behavior, so this is a clean,
  independent follow-up. Needs a design discussion before any change.

## 32-bit-host toolchain: IR constant width & VM machine word

### 🏷[LANE 3] `lowerFromSource` / `genModule` test helpers pass a NIL checker → int literals > INT32_MAX truncate on a 32-bit host — 🟠 OPEN (found 2026-07-04)

`pkg/binate/vm/lower_test.bn`'s `lowerFromSource` (and `genModule`) create a
checker (`c.Check(file)`) but then call `ir.GenModule(nil, file)` — passing `nil`
instead of `c`. With a nil `ctx.Checker`, `exprIntLitValue` (`gen_expr.bn:66`)
falls back to `parseIntLit` instead of the checker's bignum (LitMag/LitSign), so a
source literal exceeding the IR-gen HOST's signed-int range wraps: on the
arm32 unit-test binary (host int = 32-bit at IR-gen time), `5000000000` →
`705032704`, `2147483648` → wraps. This is a TEST-HELPER bug (real programs go
through `cmd/bnc`/`cmd/bni` with a real checker), but it makes any
`lowerFromSource`/`compileAndRun`-based test with a `> INT32_MAX` literal FAIL on
arm32 — it masqueraded as a "reverse-fix truncation" until isolated (the real fix
is correct; the test now builds via direct IR `EmitConstInt64`). Fix: pass `c` to
`GenModule` in both helpers (they already have it). Likely turns 1–2 of the arm32
vm-unit reds below green.

### 🏷[LANE 3] arm32 `builder-comp_arm32_linux vm` unit package: 6 PRE-EXISTING failures exposed once it compiles — 🟠 OPEN (found 2026-07-04)

The literal-unblock commit (`5b557686`) makes the arm32 vm-unit package COMPILE
(it previously didn't, hiding all failures). 236 pass, 6 fail — all pre-existing,
unrelated to the 64-bit-return work:
- `TestExecUint32HighBitToFloat32`, `TestLowerCastUint32ZeroExtendsToUint64` —
  likely the nil-checker helper bug above (`2147483648` / `4294967295` literals).
- `TestRegisterPackageFunctionsCarriesRetbufSize` (hardcodes managed-slice `32`),
  `TestLowerReturnSingleFuncValue` (hardcodes func-value `16`) — hardcoded LP64
  sizes; fix to `types.GetTarget().PointerSize`-derived.
- `TestExecBcIfaceUpcastNativeSource` (hardcodes upcast `offset*8`),
  `TestVtableInjectRegistry` — fallout from the concurrent `0734beaa` iface
  vtable-any-block change; likely that lane's to resolve.
Per red-mode-first: each needs a target-aware fix or an xfail+TODO.

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

## Build constraints (`#[build(EXPR)]`)

### Collapse `pkg/bootstrap` onto `#[build]` — 🟡 OPEN (next, per user 2026-06-19)
With BUILDER at `bnc-0.0.9` (both `bnc` and `bnlint` parse `#[build]`), `pkg/bootstrap` — whose
per-target variants are currently PATH-selected and which lives in cmd/bnc's BUILDER-compiled
tree — can be collapsed onto `#[build(...)]`-gated declarations, the same way `pkg/builtins/build`
was. See [`plan-impls-constraints-migration.md`](plan-impls-constraints-migration.md). (This was
the "bonus" of the build.bni-dedup workaround removal, now landed — binate `9c2ac789`, archived in
[claude-todo-done.md](claude-todo-done.md).)

### Build constraints (`#[build(EXPR)]`) — deferred follow-ups (arch/os MVP landed) — 🟡 OPEN
The `#[build(EXPR)]` arch/os MVP is landed at all four granularities (file / decl / import / `.bni`),
host-default config overridable per `--target`, through `c7249552` (conformance 731/733/735/736/737/746/747);
full design in [`plan-build-constraints.md`](plan-build-constraints.md), archived in
[claude-todo-done.md](claude-todo-done.md). Still deferred (none started):
- Vocabulary beyond arch/os: `triple` / `backend` / `libc` / `ptrsize` / `version` with `is` / `at_least` / `at_most`.
- `bnlint --target`; main-module gating; migrating the `impls/` duplicate trees onto constraints.
- The separate inline-asm (`#[asm]`) doc that composes with this substrate.

## bnfmt (self-hosted formatter)

### bnfmt builtin/argument wrapping residuals — 🟢 LOW (latent, no in-tree overflow) — OPEN (2026-07-05)

Two narrow cases where a builtin call can still emit a >100 line (found while
fixing the statement/decl/case wrapping regressions; neither occurs in the tree
today, so they are latent, not active):

- **Non-last long builtin argument.** `printBuiltin` forwards the closing-`)`
  reservation (`1 + tail`) only to the LAST value argument (`print_builtin.bn`).
  A non-last argument that is a wrappable binary landing at cols 97–100 is
  followed by `, <rest>)` it does not reserve, so it stays flat past the cap.
  Fix: give non-last args a comma-plus-remaining reservation, or route the whole
  builtin arg list through `fillExprList` (mirroring `printCall`). None in-tree.
- **`__c_call` last-arg binary.** `printCCall` writes its args with plain
  `printExpr` (tail 0), so a `__c_call(..., <long binary>)` whose last arg lands
  at 97–100 overflows by the un-reserved close paren. Fix: mirror the
  `printBuiltin` last-arg `printExprTail(…, 1 + tail)` treatment. None in-tree.

Discovered by the wrapping-fix workflow (2026-07-05); the `printBuiltin` doc
comment points here. Cross-refs `explorations/plan-bnfmt.md` §14.

### `bnfmt-format` hygiene check: switch to the bundled bnfmt after the next release — 🟡 OPEN (2026-07-06)

`scripts/hygiene/bnfmt-format.sh` (added `a58f2f85`) currently BUILDS bnfmt from
source and caches the binary (`$XDG_CACHE_HOME/binate/bnfmt`, keyed on a hash of
bnfmt's build inputs) because bnfmt is not in the BUILDER bundle. After the next
release bundles bnfmt, switch the check to fetch it via `fetch-builder.sh --tool
bnfmt` (build-from-source as the fallback), mirroring `lint.sh`'s `--tool bnlint`
— dropping the per-machine build+cache. The switch-point TODO is also in the
script header. Prereq: `fetch-builder.sh --tool bnfmt` must resolve the bundled
binary (`make-bundle.sh` already builds `bin/bnfmt`, but verify the fetcher
recognises the `bnfmt` tool name once a bundle containing it exists).

### `bnfmt-format` scope: sweep + cover the stdlib (impls/ + ifaces/) — 🟢 LOW — OPEN (2026-07-06)

The reformat sweep and `bnfmt-format.sh` cover `pkg/` + `cmd/` only. The stdlib
under `impls/` + `ifaces/` (~179 files, ~83 would-change) is not yet bnfmt-
formatted. To bring it under the check: reformat it (a sweep batch, verified by
the stdlib's tests + a conformance smoke) and add its roots to `$ROOTS` in
`scripts/hygiene/bnfmt-format.sh`.

## bnlint rules, unused-entity checks & lint skips

### Wire `bnlint --tests` into hygiene — 🟡 OPEN (BUILDER-gated)

The `--tests` feature (lint a package's `_test.bn` files) is fully built, its
test-file findings all resolved, and it has an end-to-end test
(`TestLintPackagesTestsFlag` + the `testdata/` ignore convention). The only
remaining step is turning it on in CI: add `--tests` to
`scripts/hygiene/lint.sh`. **Gated on the next BUILDER bump** — hygiene prefers
the *bundled* bnlint (`bnc-0.0.10`), which predates `--tests`, `// bnlint:allow`,
and the newer rules (a current-source bnlint already supports all of it). Batch
with the other BUILDER-bump lint-skip cleanups below. When wiring, run
unused-func WITH `--tests` — a plain run over-flags the 12 production helpers used
only by tests. Design + full status + the rest of the unused-entity project (now
done): `explorations/plan-unused-checks.md` and the done log.

### `[managed-to-raw-assign]` in `pkg/binate/asm/*` — INCREMENT 2 (adopt directives + un-skip) — 🟡 OPEN (BUILDER-gated)

The compiler-tree lint-coverage gap is ✅ FIXED (`582c1327`, recursive `pkg/`
discovery), the 19-finding per-site audit is DONE, the 1 real use-after-free
(`parse/parse.bn:160` constant-name borrow) + 1 real unused-import are ✅ FIXED
(`8a883450`), and the `// bnlint:allow <rule>` suppression mechanism is ✅ LANDED
(`91286ab8`) — see the done log. **Remaining (INCREMENT 2):** the 17 safe-borrow
over-flags (all `arm32`/`elf`/`macho`/`x64` sites + 6 `parse` sites — each a raw
view of a field of a live `@asm.Section`/`@asm.Assembler`/buffer that outlives the
synchronous read) are handled by annotation, not a rule change: add a trailing
`// bnlint:allow managed-to-raw-assign — <why the owner outlives the borrow>` to
each site and drop `pkg/binate/asm/{arm32,elf,macho,parse,x64}` from `LINT_SKIP`.
**Gated on the next BUILDER bump** — hygiene runs the bundled `bnc-0.0.10`, which
predates `91286ab8` and would ignore the directives. Do it in one commit at that
bump, alongside dropping `pkg/binate/interp` (see the BUILDER-lag-lint-skips entry
below — that bump clears all remaining `LINT_SKIP` entries).

### Remove the BUILDER-lag lint skips after a BUILDER bump — 🟡 OPEN (narrowed to `pkg/binate/interp`; gated on next BUILDER bump)
`scripts/hygiene/lint.sh`'s `LINT_SKIP` group (A) is the BUILDER-lag set — packages the bundled
bnlint can't typecheck because they use a feature/fix newer than the bundle.

**The bnc-0.0.9 lag is CLEARED** (BUILDER is now `bnc-0.0.10`, checked 2026-06-29). `pkg/builtins/rt`
(the `"void"` `__c_call` spelling) and `pkg/std/os` (the `.bni` free-function-vs-method fix
`796effc7`), plus their importer chain `pkg/binate/{vm,repl}` + `cmd/{bni,bnas,bnlint}`, all lint
**clean** under the bnc-0.0.10 bundled bnlint (verified each directly). Dropped from `LINT_SKIP` —
restoring style-lint coverage on those seven packages, hygiene 15/15 — in `binate` lint.sh change
`c5a14146`.

**Still skipped — `pkg/binate/interp`**, but for a *newer* lag (not the rt/os one). **Root-caused
(2026-06-30): a synthesized-accessor NAME skew, not a missing bnlint capability — so the next bump
fixes it and NO linter work is needed.** The compiler-synthesized reflect accessor was renamed
`_Package` → `__Package` in `e12a8a3b` ("fix CRITICAL … close silent collision", 2026-06-26), which
postdates the bnc-0.0.10 release (`cdea9b9f`, 2026-06-23). interp's extern-registration references the
new name as a func value (`rt.__Package`, `reflect.__Package`, `errors.__Package`, …), but the bundled
bnc-0.0.10 checker still synthesizes/resolves the OLD `_Package` (verified: `emit_pkg_descriptor.bn`
mangles `"_Package"` at cdea9b9f, `"__Package"` at HEAD), so `<pkg>.__Package` is undefined under the
bundle — cascading to all four errors (`undefined: __Package` → `cannot call non-function` → `cannot
assign void to @Package` → `_func_handle argument must be a named function`). A current-source
(post-rename) bnlint lints interp clean. Action: at the next BUILDER bump (source ≥ `e12a8a3b`), drop
`pkg/binate/interp` from `LINT_SKIP` and close this entry.

**Next-bump checklist — the `asm/*` group (B) joins here.** The 5 `pkg/binate/asm/*` skips (real
safe-borrow over-flags) are un-skipped via the `// bnlint:allow` suppression mechanism (landed main
`91286ab8`), which is ALSO newer than the bundle — so the same bump that drops `interp` should also
adopt the 17 asm directives + drop `pkg/binate/asm/{arm32,elf,macho,parse,x64}` (see the asm
`[managed-to-raw-assign]` audit entry above). One bump clears every remaining `LINT_SKIP` entry.

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

### Hygiene check: enforce `pkg-layout-spec.md` tier dependency rules
**What**: a `scripts/hygiene/` check (new script alongside `conformance-imports.sh`) that
statically validates every package's import closure against the tier ordering in
[`pkg-layout-spec.md`](pkg-layout-spec.md) ("Tiers"). Two facets of the same rule:
- **Dependency direction**: a package may import only packages at its own tier or **lower**;
  importing a strictly-higher tier is a violation. (This is the runtime enforcement of the spec's
  "Transitive constraint" + tier table.) Tiers low→high: 0 / 0b (`pkg/builtins/*`) < 1
  (`pkg/std/*`) < 1x (`pkg/stdx/*`) < 2 (`pkg/<org>/*`, e.g. `pkg/binate/*`) < 3 (app-specific).
  E.g. `pkg/builtins/rt` importing `pkg/std/io` is illegal; `pkg/binate/parser` importing
  `pkg/std/os` is fine.
- **Bundled-set closure**: bundled tiers (0/0b/1/1x — always/by-default bundled) must NOT import a
  not-bundled tier (2/3), and a tier-2 package's dependency closure must itself be tier 2. A
  bundled package whose closure escapes the bundled tiers silently breaks the bundle — the
  dependency's source isn't shipped, so a consumer compiling against the bundle gets
  `package "<dep>" not found`.
- **`pkg/std` → `pkg/stdx` refinement**: tier 1 (`std`) may depend on tier 1x (`stdx`)
  **internally** (`.bn` impl files) but **not externally** (`.bni` interface files) — a `.bni`
  importing `stdx` leaks a no-inter-version-compat (1x) type into `std`'s strict-compat surface.
  So the check must scan `.bni` imports separately from `.bn`: the std→stdx edge is allowed only
  from `.bn`. (Generalize if other interface-vs-impl tier asymmetries surface.)

**Why NOTHING currently catches this**: it only manifests when a consumer compiles the
offending package from a real bundle (`make-bundle.sh` output), which no CI / hygiene /
conformance step does today.

**Motivating bug (2026-06-10, release-prep for `bnc-0.0.8`)**: `pkg/builtins/lang` (tier 0)
imported `pkg/binate/buf` (tier 2) for two `buf.CopyStr("true"/"false")` calls in `bool.String()`.
The bundle ships only `lib/pkg/bootstrap`, not `pkg/binate/buf`, so the tier-0 `Stringer` carve-out
(`var s *lang.Stringer = &x; s.String()`) failed to compile from ANY bundle with
`package "pkg/binate/buf" not found` — present since `bnc-0.0.7`, undetected because the carve-out
smoke step (`release-process.md` step 5) had never actually been run against a real bundle. Fixed
in binate `84818a77` (lang returns bare string literals; `[N]readonly char → @[]char` is a
literal-init allocate+copy). This check would have caught it at the `import` line.

**How**: tier is path-derivable (`ifaces/core` + `impls/core/*` → 0/0b; `ifaces/stdlib/pkg/std`
→ 1, `…/pkg/stdx` → 1x; `pkg/binate/*` & other `pkg/<org>/*` → 2); `pkg/bootstrap` is a bundled
runtime primitive (treat as tier-0-equivalent). Walk every package's imports (split `.bni` vs
`.bn`), map importer + imported to tiers, flag any higher-than-self edge, applying the std/stdx
refinement. A whitelist file (cf. `conformance-imports.whitelist` / `naming.whitelist`) covers
sanctioned exceptions. EXEMPT `*_test.bn` — tests aren't bundled (e.g. `lang_test.bn` legitimately
imports `pkg/binate/buf`).

**First manual sweep (2026-06-10) — CLEAN baseline**: swept every import (incl. aliased) in the
bundled trees (`ifaces/{core,stdlib}`, `impls/{core,stdlib}`, `pkg/bootstrap`, `runtime/`). No
non-test bundled package imports outside the bundled set. Two non-obvious cases the check must
handle: (1) `impls/core/baremetal/pkg/builtins/rt` imports `pkg/semihost`, NOT a violation —
`pkg/semihost.bni` ships under `runtime/baremetal_arm32/` (a bundled runtime component) and
resolves under the arm32-baremetal build's own `-I`/`-L`; treat shipped `runtime/<target>/pkg/*`
as bundled, or scope tier rules per build target. (2) all `pkg/builtins/testing` imports are in
`*_test.bn` (already EXEMPT) and it has a bundled `.bni` with a harness-provided impl. So
`lang → pkg/binate/buf` (binate `84818a77`) was the only true violation; the baseline is otherwise
clean.

**Scope** (per CLAUDE.md "Stay Within the Asked Scope"): add the script only; wiring it into
`scripts/hygiene/run.sh` / CI is a separate decision for the user. (An earlier filing noted that a
hygiene check ultimately belongs in the run.sh master, so both could be done together — but that
wiring is still the user's call.) A first audit may surface other pre-existing violations to
triage.

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

### `==` / `!=` (and relational) on aggregates — residual (generic re-check corner cases) — 🟢 LOW (triaged 2026-06-30: NOT actionable now)
The `==`/`!=`/relational aggregate story is ✅ DONE & LANDED — full arc (checker
rejection, struct/array + generic-function impl, sentinel decision, generic-field
re-check) archived in [claude-todo-done.md](claude-todo-done.md). Two small residuals in the generic
instantiation re-check remain — **triaged 2026-06-30, neither actionable now**
(neither is a live miscompile):
- **(a) Order-dependent — COSMETIC only.** A forward-ref instantiation checked BEFORE
  the generic's body is type-checked falls back to the loud IR-gen error instead of a
  clean checker rejection (never a silent miscompile, never a false reject — just a
  less-friendly diagnostic in that ordering). A fully order-independent version needs
  a checker sub-pass or an explicit `comparable` constraint — non-trivial work for a
  diagnostic-quality-only gain; deferred.
- **(b) Generic-TYPE methods — UNREACHABLE (blocked on a future feature).** Verified
  2026-06-30: bnc does NOT support a method on a generic type with a type-param
  receiver (`func (b Box[T]) eq(...)` → "method receiver must be a named type",
  "undefined: T"). So the re-check gap for generic-TYPE-method comparisons cannot be
  triggered — there is no way to define such a method today. This becomes a real
  follow-up only if/when generic-type methods land; not a live gap.

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

### Import aliases and blank imports
- Do we support Go-like `import somethingelse "pkg/foo"` currently? We'll likely need this.
- Do we support `import _ "pkg/foo"`? Should we? (Side-effect-only imports.)
- Both interact with the package object naming question above.

## Spec authoring & language-decision residuals

### Package-level var initialization is declaration-order, not dependency-order — spec decision needed
`var A int = B + 1; var B int = 10` makes `A == 1` (B is still 0 when A initializes),
NOT 11 — package-level VAR initialization runs in DECLARATION order, not dependency order.
`decl.order.forward` guarantees the forward NAME reference resolves (it compiles), but the
VALUE at init time follows declaration order. Go initializes package vars in dependency
order; Binate does not, and §9.8 is silent on var-init order. → a spec-vs-impl decision
(declaration-order vs dependency-order) for `spec-todo.md`. The Ch.9 tests do not assert
any var-init-order value (forward-ref is tested via a function). Surfaced authoring
`conformance/spec/09-declarations-and-scope`.

### §8.5 spec "precision residual" note appears stale — verify and drop
The §8.5 "Open (precision residual)" note in the conversions spec chapter says a constant
≥ 2^63 reached through a bitwise/shift op "is not yet rejected": `cast(int64, 0x4000000000000000 << 1)`. That exact
example — and `cast(int64, 1 << 63)` — now **reject** ("constant does not fit the cast
target type"). The bitwise-const fold may have been fixed; verify (other patterns?) and, if
so, drop the §8.5 residual note (like the Ch.13 generic-unparsed/d4-paren stale notes). No
born-stale xfail added (rejection is the correct behavior). Surfaced authoring
`conformance/spec/08-conversions`.

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

### Spec Ch.16 (Packages) — build-constraint group needs rework + a possible gap — 2026-06-19
Ch.16 landed at **21/22 rules** (`spec/16-packages/`, binate `f7ed4eb4`):
imports / bni / identity / extern groups are green (compiler/VM/gen1/gen2/
native_aa64). The **build-constraint group** (the `#[build(EXPR)]` rules) was
authored by a fan-out agent on a wrong "gating-active by default + decl-level
gating + predicate-validation-errors" assumption; 8 of its tests failed and were
removed. The real mechanism (per `conformance/737_build_import_select`,
`747_err_build_bni_dropped`) gates whole FILES (via the package clause) and
IMPORTS by arch with `#[build(is(arch, …))]`, not individual decls. **Follow-up
(focused):** re-author the build-constraint tests on the real mechanism, which
restores the lone GAP **`pkg.build.errors`** (the Constraint: a false constraint
on a *required* element is an error). Surviving build tests: `070_annotation_
namespace`, `071_annotation_degenerate`, `072_err_annotation_no_stack`.
  - **Possible real gap to confirm during that rework:** the agent's
    `#[build(<unknown-predicate>)]` and `#[build]` with an unknown annotation
    name **compiled and ran** (printed `0`) instead of erroring — `pkg.build.errors`
    / `pkg.annotation.namespace` say these should be rejected. Either the tests
    were malformed (wrong gating context, so the annotation was never validated)
    or build-constraint validation doesn't fire — determine which.

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

### arm32 iface shape-test intermittent LP64-doubling flake — 🟡 OPEN (2026-07-06)

**Symptom:** `TestEmitImplVtablesNonExtendingShape` / `TestEmitImplVtablesExtendedConcatShape`
(`pkg/binate/native/arm32/arm32_iface_test.bn`) intermittently fail their relro
byte-count assertions with EXACTLY the LP64-doubled values (24→48, 72→144), i.e.
`ir.BuildImplVtable` strided 8-byte slots — the ILP32 target (`IntSize=4`) was not
in effect at emit time. **Trigger:** full-suite ordered native unit run
(`scripts/unittest/run.sh builder-comp native`); ~1 in 50; NOT reproducible in
`--run` isolation. **Root cause: UNKNOWN — needs investigation.** Both tests call
`setArm32TargetIface()` (sets `IntSize=4`) as their first line, and neither
`ir.GenModule` nor the parser calls `types.SetTarget` (grep-verified), so nothing
should reset the global target between the setter and emission — yet it
intermittently reads 8. Candidates: a global-target ordering/visibility subtlety
across tests, or genuine gen1 emission nondeterminism (the latter would be a real
compiler bug). **Diagnostic in place (commit `3ca73110`):** each shape test now
asserts `types.GetTarget().IntSize == 4` immediately before the byte-count check,
so a recurrence reports "target leaked to LP64" instead of a confusing count
mismatch — pinning whether the cause is the target (guard fires) or something else
(guard passes, count still doubled). Covered by those two tests. Do NOT widen the
byte-count tolerance to "fix" it — a real word-size regression looks identical.

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

### MINOR (e2e / BUILDER-lag cleanup) — drop the gen1 build in e2e/stat-values.sh after the next BUILDER bump (2026-06-20) — 🔴 OPEN

`e2e/stat-values.sh` builds gen1 from the tree (`scripts/build-bnc.sh`) and compiles its os.Stat probe through gen1, instead of the simpler `$BUILDER … cmd/bnc -- …` form the other e2e scripts use. Reason: os.Stat depends on the `.bni` free-func/method fix (`796effc7`) and the wholesale-os-injection work, which postdate `BUILDER_VERSION` (bnc-0.0.9) — the pinned BUILDER can't compile os yet. Once BUILDER is bumped past those, revert `e2e/stat-values.sh` to the plain `$BUILDER … cmd/bnc -- …` pattern (drops the ~1-min gen1 build per e2e run).

### Stdlib conformance suite — optional follow-ups — 🟢 LOW (2026-06-20)

The suite is built and every injected stdlib package has cross-mode coverage
(moved to claude-todo-done.md). Two optional cleanups remain:
- Fold the ~8 ad-hoc stdlib-importing tests in the MAIN conformance set
  (`577_std_errors`, `855_std_time`, `662_errors_is`, `526/528/535_strconv`,
  `663_io_iseof`, `726_cross_pkg_iface_impl`) into `conformance/stdlib/*` (and
  drop their `conformance-imports.whitelist` entries).
- Remove the now-redundant `os_test.bn` `TestErrorIfaceUpcast` (covered by
  `conformance/stdlib/errors/001`; only runs under `builder-comp` now), or keep
  it as a native-only smoke.

### Stale-xfail sweep — residuals (the cross-mode CONFORMANCE sweep is done) — 🟡 OPEN
The big stale-xfail sweep — all 10 modes via the `conformance-xpass.yml` CI workflow;
121 stale conformance markers + 8 VM-mode unittest markers removed; per-mode detail +
methodology — is ✅ DONE; see [claude-todo-done.md](claude-todo-done.md). Two residuals:
- **Cross-mode UNITTEST xfails (17)** — UNSWEPT. The unittest `--check-xpass` (binate
  `ddc624d2`) exists but isn't wired into CI, so the XPASS workflow is conformance-only;
  the 16 arm32-baremetal + 1 arm32-linux unittest xfails need qemu. Sweep by hand, or
  wire unittest `--check-xpass` into CI.
- **`value-struct-large` on `native_x64`** — *not* xfailed there yet crashes (empty
  output) when run; a real missing-xfail or native_x64 bug, surfaced (then masked by a
  substring collision) during the sweep. Worth a look now that `run.sh --exact` no
  longer pulls it into the `value-struct` filter.

### Plan-3 adversarial-review follow-ups (test-hygiene + coverage gaps from `cc2ddcc4` / `997c4c04` / `0c707e1f`) — 2026-06-08
Non-wrong-code items from the adversarial review of the plan-cr2-3 work; each is small. (The live wrong-code findings are the OP_CAST/iface-arg CRITICAL and the float-multi-return MAJOR (both fixed & archived in claude-todo-done.md).)
- **Weak / over-claimed Defect-6 pin**: the addr-aggregate `global` cells (`997c4c04`) + their generator docstring/README claim to pin "2-word sizing / mis-sized-to-one-word drops a word" — but store+load are width-consistent so the cell is INVARIANT to allocation size (it pins materialization + `__init`-store + read-back wiring, NOT sizing). Fix the docstring (`gen-addr-aggregate-matrix.py:96-104`) / README / commit framing to match. Also Defect 6 closed using only the two shapes that typecheck; readonly-wrapped + named-over-aggregate + raw `*func()` + uninitialized-nil global companions (the Class-A materialization risk in `plan-code-red-2.md`) were left out — record as an explicit deferral (invoking them is blocked upstream at the call typechecker).
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
- **Note**: several `@Iface` lifecycle bugs are already filed (leaks/UAF family); a
  matrix would close the long tail. The `@[]@I` literal element leak is now ✅ FIXED
  (main `a2abf36e` — a general managed-slice-literal element leak across all managed
  element kinds; see claude-todo-done.md).

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
All five REPL tiers are landed (archived in [claude-todo-done.md](claude-todo-done.md): Tier 1–2 eval +
redefinition, Tier 3 forward refs incl. pending types/vars/consts + cycle detection, Tier 4 replace +
shadow for funcs & methods, Tier 5 mid-session imports `78685ac3`). Residual:
- **Tier 4**: refcount-aware shadow warning (today fires unconditionally); forced-shadow escape hatch (syntax TBD per `claude-notes.md`).
- **Pretty-printer** (`pkg/replprint`) — deferred until interfaces land (`bootstrap.println` is a temporary hack; don't entrench it).

## ARM32 bare-metal target

### native arm32 backend — IN PROGRESS (live tracker: [plan-native-arm32.md](plan-native-arm32.md))

The `pkg/binate/native/arm32` backend (P0–P4-a done; P4-b/c/d + P5–P7 remaining)
is tracked in detail in `plan-native-arm32.md`; that doc is authoritative for
phase status, landed commits, and deferred shapes. Deferrals below are all
**fail-loud** (a shape the backend doesn't implement emits a clean COMPILE_ERROR,
never silent wrong-code) — EXCEPT the MAJOR bug just below, which violates that.

**FOLLOW-UP (aarch64-native, pre-existing, found 2026-07-05): cross-package
big-multi-return FUNC-VALUE call CRASHES on aarch64 native (empty output).**
Distinct from the arm32/x64 under-reservation bug below (aa64 has no
SretInGpArgReg, rides X8, so the sizer/emitter agree — not an under-reservation).
Exposed by a cross-module test (a dep `func F5(a,b,c int) (int,int,int,int,int)`
exported as `*func(int,int,int) (int,int,int,int,int)` via `Get5()`, called from
native main and printed) which PASSES on host + native arm32 + native x64 but
produces EMPTY output on `builder-comp_native_aa64-comp_native_aa64`.  889
(cross-pkg func value, NON-big-multi-return) passes on aa64, so it is the
big-multi-return shape specifically.  Likely the func-value shim ABI wants the
retbuf as a PREFIX ARG (the x64/arm32 convention) but aa64's emitCallFuncValue
uses X8 — a native↔LLVM boundary mismatch; needs investigation.  **aa64 native is
in `scripts/modesets/all` (a BLOCKING mode) and is currently 100% green (0
xfails)** — so this is a latent MAJOR bug on a blocking mode (untested until now).
The repro test was NOT committed (would redden aa64); recreate it (the F5/Get5
program above, expected `10 20 30 30 50` for args 10,20,30) when fixing aa64, and
add it to the P4-c/aa64 acceptance once green.  User decision (2026-07-05): land
the arm32/x64 fix now, do aa64 as a follow-up.

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

**MAJOR (pre-existing silent miscompile on main; found 2026-07-04 by the proper
audit): cross-package REFLECT over package functions produces WRONG output.**
`725_reflect_package_functions` (prints the function count `3` correctly, then
FAILS to print the per-function Name/RetbufSize/ParamSlots/Sig — actual output is
just `3`) and `727_reflect_function_signatures` (prints `5` then wrong). Both
COMPILE (no COMPILE_ERROR) but produce wrong runtime output — a silent miscompile
in the cross-pkg reflect / `__Package` descriptor iteration path
(reflect.Package.Functions, a managed-slice of FunctionInfo aggregates read from
the LLVM-emitted dep descriptor). **Present since at least P4-a (identical wrong
output in the P4-a, guard, and by-address runs)** — UNCHANGED by P4-b, unrelated
to func-value ARGS/RETURNS. Needs its own investigation (managed-slice-of-struct
iteration and/or the descriptor's string/handle fields at the native↔LLVM
boundary). These two are the ONLY non-COMPILE_ERROR failures in the 718 native
arm32 conformance failures — the other 716 are clean fail-loud deferred shapes,
and there are 0 hangs (verified via the QEMU "terminating on signal" grep on the
FULL verbose output).

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

#### native-arm32-baremetal runtime miscompiles (found by P4 recon, 2026-07-02) — ✅ ALL THREE FIXED (2026-07-03/04)

Three tests compiled clean through the native arm32 backend then HANG at runtime
under QEMU (`[10s]` timeout) — silent-miscompile-invariant violations, arm32-only.
All three are now ✅ FIXED (no `[10s]` runtime hangs remain on
builder-comp_native_arm32_baremetal; conformance 1780/841/32):

- `matrix/abi/struct-param/five-u8` (`f3a8bc91`) — `common.PlanFrame` didn't round
  the aggregate-PARAMETER frame region up to 8 bytes → word-store overrun +
  misaligned later frame slots → Data Abort.
- `877_aggregate_abi_xpkg` (`0479813a`) — the shared `NeedsSret`/`IsAggregateReturn`
  64-bit-scalar misclassification, fixed by the aggregate-KIND gate.
- `599_addr_of_slice_elem` (`ba2a14ec`) — NOT the arm32-emit bug first suspected: a
  MAJOR shared-IR wrong-width deref-store. `genAssign`'s `*p = val` (STAR) arm stored
  the RHS at its OWN width, not the pointee width (unlike the IDENT/SELECTOR arms), so
  `*pUint8 = 99` emitted a 4-byte `store i32` through an `i8*` — a wide store clobbering
  neighbor bytes; **latent memory corruption on ALL backends**, visible only on
  strict-alignment arm32-baremetal (Data Abort → hang). Fixed by an `ensureWidth` in
  the STAR arm mirroring the sibling arms; an exhaustive audit confirmed it was the
  SOLE such site. Consistency follow-up landed (`8b9bddbb`): `genMultiAssign`'s
  IDENT arm now applies the same `ensureWidth` as its SELECTOR sibling (inert
  today — its RHS is always a single-call component already at the declared width
  — so all lvalue-store arms are now width-coercion-symmetric).

#### MINOR (cross-backend diagnostics) — `iropcode.OpName` missing `OP_CONST_FLOAT`

`pkg/binate/iropcode/opcodes.bn`'s `OpName` switch lacks an `OP_CONST_FLOAT` case,
so float-const failures mislabel as "unimplemented IR op unknown" across all
backends/tools. 1-line fix (`case OP_CONST_FLOAT: return "const_float"`); pure
diagnostics, no pass/fail change.

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

## Opportunistic code cleanups

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
  body), since fixed (`4306197`; archived in claude-todo-done.md).
  Remaining candidates are smaller / lower-value (assorted
  if-chains in cmd/* and pkg/* tools).

### Clean up conformance tests to use array literal + `arr[:]` pattern
- `arr[:]` works in compiled mode; conformance tests using `make_slice` + indexed assignment for static data could use `[N]T{...}` + `arr[:]` instead
- Consider adding slice literal syntax (`*[]T{...}`) as sugar
