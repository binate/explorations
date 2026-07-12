# Plan: native arm32 backend (`pkg/binate/native/arm32`)

Status: **IN PROGRESS** (started 2026-07-01). Goal: a native (direct
IR→object) code generator for 32-bit ARM, hooked up analogously to the
existing **LLVM** arm32 path — i.e. serving BOTH `--target arm32-baremetal`
and `--target arm32-linux`, run under QEMU by the same runners, but with the
per-module IR→`.o` step done by `pkg/binate/native/arm32` instead of
`codegen`→`clang -c`.

Sequencing decision (user, 2026-07-01): **baremetal-first, then linux.**
Baremetal is soft-float + freestanding (semihosting, no libc, no VFP), so it
derisks the whole pipeline (ELF32 emission, QEMU boot, AAPCS32 ABI, `__aeabi`
64-bit helpers) before the VFP/hard-float work. This mirrors how the LLVM
arm32 path was built (baremetal was its v1 — see
[`plan-arm32-bare-metal.md`](plan-arm32-bare-metal.md)).

Sibling precedents: [`plan-native-x64.md`](plan-native-x64.md) (the second
native backend — proves the per-arch package pattern) and the existing
`pkg/binate/native/aarch64` (the template this backend mirrors 1:1 in file
decomposition). ILP32 layout background: [`plan-arm32-bare-metal.md`].

## Pickup notes (for a session resuming after context compaction)

- **Worktree:** all arm32 work happens in `temp-binate-5` (branch `temp-5`), the
  session's pre-assigned worktree. Do NOT create new worktrees. Resync it against
  LOCAL main (`git -C temp-binate-5 fetch ~/binate/binate main && … rebase
  FETCH_HEAD`), never `origin/main`.
- **Landed so far (all on main):** P0, P0-fu, P1, P2, P3.1, the arm32-linux
  nativeArch regression fix, P3.2, the int64 follow-ups, the conformance-harness
  `OVERRIDE_MODE` fix, **P3.3 (single-aggregate sret, `d9567498`)**, the
  `common_callconv` constructor split (`1fade373`), **OP_MAKE/OP_BOX (finish
  P3 emit, `b33eb9d6`)**, the experimental CI wiring (`0727d0c1`), the
  **`[N x i64]`→`[N x i32]` ILP32 aggregate-coercion ABI fix (`5b65e369`)**, the
  docs-only comment sweep (`9239279a`), and the **three runtime-hang fixes** —
  five-u8 aggregate-param PlanFrame 8-round (`f3a8bc91`), the shared
  `NeedsSret`/`IsAggregateReturn` 64-bit-scalar kind gate (877, `0479813a`), and
  the shared-IR deref-store width coercion (599, `ba2a14ec`), **P4-a
  (func-value shim sret return-shape + indirect-call dispatch, `a888e9cd`)**, and
  **P4-b1 (small in-register aggregate return + the cross-pkg aggregate-arg
  by-address MAJOR bug fix, `bc42705e`)**, and **P4-b2 (multi-return + the shared
  big-multi-return func-value outgoing-args under-reservation fix for arm32 + x64,
  `e1e49b73`)**.
  Current native-arm32-baremetal conformance: **2007 passed / 619 failed / 31
  skipped** — **0 runtime hangs** (verified via the QEMU "terminating on signal"
  grep on the FULL verbose output — NOT a `[10s]` grep, which is unreliable on
  non-verbose output and let a P4-a hang slip). 617/619 failures are clean
  fail-loud COMPILE_ERROR deferred shapes; the only 2 wrong-output failures are
  725/727 (later found NOT a miscompile — stale arm32 expected files, resolved
  `4fe304dd`). The 877 kind-gate also repairs the shared int64-return
  classification the concurrent ILP32-VM work references (its deferred VM-return
  dispatch-patch is likely now moot — see claude-todo.md). 599's fix (shared-IR
  `genAssign` STAR-arm ensureWidth) also corrected a latent wrong-width-store
  memory-corruption footgun on ALL backends.
- **Per-increment workflow that's worked every time:** (1) delegate the
  increment to a background `Agent` working in `temp-binate-5` (no `isolation:
  worktree`), mirroring the `native/aarch64` handler where a template exists and
  designing novel code where it doesn't (int64 pairs had none); (2) run a minimal
  2-reviewer adversarial `Workflow` (structured findings) — it has caught a real
  bug every phase; (3) fix findings myself; (4) independently re-verify (units for
  every changed package incl. `cmd/bnc`/`common`, native-arm32 conformance
  spot-checks, hygiene, and that deferred shapes still COMPILE_ERROR); (5) land
  via the full procedure with FRESH per-round explicit approval for each
  cherry-pick; (6) update this doc.
- **The fail-loud invariant is sacred:** every op/shape the backend doesn't
  implement must `a.SetError` (→ clean COMPILE_ERROR), NEVER silently miscompile.
  Verify it holds after each increment (a deferred-shape test must COMPILE_ERROR,
  not produce wrong output). The dispatch tail + per-emitter guards enforce it.
- **Verify agent reports; don't trust counts on faith** — independently re-run
  the acceptance (a P3.1 "19" was a curated batch; P3.2's "1451" needed a full
  run to confirm; a `002_arithmetic` "deferred but passing" turned out to be
  constant-folding, not a bug).
- **Smoke set = `git diff --name-only`,** not the packages an agent's report
  emphasizes (a `cmd/bnc/target.bn` change once went unsmoked → red main).

## Architecture recap: the native backend seam

`cmd/bnc` compiles each package to an object. Two backends sit behind a
`Backend` interface in `cmd/bnc/compile.bn`, selected by `--backend native`:
`llvmBackend` (`codegen.EmitModule` → `clang -c`) and `nativeBackend`
(`native.EmitObject`). Only the **main / test-runner** module honors
`--backend native`; dependency packages always go through `llvmBackend`. So a
native arm32 `.o` must link cleanly against LLVM-compiled arm32 dependency
`.o`s — ABI and mangling parity with the LLVM path is mandatory.

`pkg/binate/native.EmitObject(mod, arch, format, path)` dispatches by `arch`
string to a per-arch sub-package's `EmitObject(mod, format, path)`. Each
sub-package is a "spill-everything" single-pass emitter: stamp a
`common.CallConv` onto a `common.RegMap`, `common.PlanFrame` per function,
emit prologue/body/epilogue via the arch's `pkg/binate/asm/<arch>` encoder,
`<arch>.ResolveFixups`, `asm.Finalize`, then `elf.Write<arch>` / `macho.*`.

**The link step is unchanged.** bnc always links via `clang` (as driver);
the native backend only replaces IR→`.o`. All of crt0.s / semihost.s /
baremetal.ld / libgcc / QEMU stay byte-for-byte identical. The native path is
exercised by passing `--backend native` to the *same* runners.

## What already exists (reused as-is — no new work)

- **ELF32/EM_ARM writer** — `elf.WriteARM32` (`asm/elf`): correct Elf32_*
  struct sizes (ehdr 52 / shdr 40 / sym 16 / rela 12), `is64 = machine !=
  EM_ARM`, ELF32 `r_info = (sym<<8)|type`. Container plumbing done.
- **arm32 instruction assembler** (`pkg/binate/asm/arm32`, ~2k LOC, ~76
  tests) — full **integer** core: data-processing (ADD/SUB/RSB/ADC/SBC/RSC/
  AND/ORR/EOR/BIC/MOV/MVN/CMP/CMN/TST/TEQ) with flexible Operand2
  (rotated-imm via `EncodeRotImm`, shifted reg, reg-shifted reg); MOVW/MOVT;
  load/store word/byte/half/signed (imm/pre/post/reg/scaled-reg); LDM/STM/
  PUSH/POP; B/BL/BX/BLX; MUL/MLA/UMULL/SMULL; SDIV/UDIV; CLZ; NOP/SVC/BKPT;
  `ResolveFixups`. Fixups today: `FIX_BRANCH24`, `FIX_ABS32`.
- **Target/link/runtime wiring** (`cmd/bnc/target.bn`) — triples, clang
  flags, `setArm32Layout` (PointerSize=4, IntSize=4, **MaxAlign=8** — AAPCS
  aligns int64/double to 8 even in ILP32), crt0.s, semihost.s, baremetal.ld,
  `--link-after-objs` libgcc probing, `nativeObjFormatForTarget`→"elf".
- **QEMU runners** (conformance + unittest) for both arm32 targets —
  backend-agnostic; a native mode just adds `--backend native`.
- **`common` layer** — RegMap, PlanFrame, CallConv engine, EmitDataGlobal,
  float-literal helpers (`ParseFloatLitToBits`, `F64BitsToF32Bits`), scalar
  classifiers (`SubWordNarrow` already takes `wordBits`).
- **Entry contract** — mangler special-cases `main.__entry` → reserved
  `bn_entry`; `EmitMainEntry`/`EmitInitDispatcher` synthesize it. crt0.s
  `_start` → `bl bn_entry`. Native backend reuses the same mangler ⇒ symbol
  parity with semihost.s (`bn_F2_3_pkg8_semihost1_*`) is automatic.
- **Refcount lowering** — `aarch64_refcount.bn` is already ILP32-aware (reads
  `types.ManagedHeaderSize()`/ptrSize); ports nearly verbatim.
- **rt_baremetal** — bump allocator over `var heap[4MiB]` in .bss, semihost
  Exit/Write; pure Binate + hand-asm, backend-agnostic.

## What's missing (the work)

1. **`pkg/binate/native/arm32/`** — the lowering package, ~20 files
   mirroring `native/aarch64` (~8–11k LOC). The bulk.
2. **`common` word-size generalization** — `ArgWords`, `PlanFrame` slot
   rounding, and `common_callconv.bn`'s arg/return classifiers hardcode
   8-byte words (`common_call.bn`: *"not parameterised on the target
   pointer/word size"*). ILP32 (4-byte) breaks them. Cross-cutting; must
   keep aarch64/x64 green.
3. **AAPCS32 CallConv** — new constructor + a genuinely new classification
   case absent from AAPCS64/SysV: **64-bit args in even-aligned register
   pairs** (r0:r1 / r2:r3) and 8-byte-aligned stack slots.
4. **Symbol addressing** — arm32 has no ADRP. MOVW/MOVT-ABS reloc pair
   (`R_ARM_MOVW_ABS_NC`=43 / `R_ARM_MOVT_ABS`=44): new fixup kinds in
   `asm/arm32` + `elf_util.bn` mappings (today only BRANCH24/ABS32).
5. **Float** — baremetal is **soft-float** (`__aeabi_f*/d*` libcalls, floats
   in GP regs — no new assembler work); arm32-linux is **hard-float** VFP,
   and `asm/arm32` has **zero VFP encoders** (phase 6).
6. **64-bit int ops** — add/sub via adc/sbc; mul via umull/smull; div/mod/
   shift via `__aeabi_*` (reuse libgcc, exactly what clang emits).
7. **Dispatch edits** — `native.bn` arm32 branch + `target.bn`
   `nativeArchForTarget` (returns `""` today → forces LLVM), plus threading
   the soft/hard-float choice to the backend (see decision below).

## Design decisions (defaults; revise if wrong)

- **A32 (ARM) mode, not Thumb.** Matches crt0.s/semihost.s (`.arm`, svc
  `#0x123456`) and `-march=armv7-a`. Thumb is out of scope.
- **Symbol addressing via MOVW/MOVT-ABS reloc pair**, not PC-relative
  literal pools. Avoids pool placement/dumping; one MOVW+MOVT sequence per
  address (analogous to aarch64's MOVZ/MOVK for constants). New fixup kinds
  + ELF reloc mappings; no assembler pool machinery.
- **Word size via `CallConv.WordBytes`.** Add a field (aarch64/x64
  constructors set 8, AAPCS32 sets 4) and thread it into `ArgWords`,
  `argRegWordsStackWords`, `PlanFrame` slot/align math, and multi-return
  packing. `SubWordNarrow(wordBits)` is the existing model. Cleaner than
  forking arm32 copies of the shared algorithms.
- **64-bit div/mod/shift via `__aeabi_*` libcalls.** Reuses the libgcc that
  baremetal already links (`--link-after-objs`) and linux gets implicitly.
  Lowest-risk, matches the LLVM path's external-symbol contract.
- **Soft-float via `__aeabi` libcalls** (baremetal); **hard-float via VFP**
  (linux, phase 6).
- **Float-ABI threading (OPEN — leaning `types.TargetInfo` field).** The
  backend must know soft vs hard. `types.GetTarget()` carries ptr/int/align
  but no float ABI. Options: (a) add a `HardFloat`/`FloatABI` field to
  `TargetInfo` (additive; backend already reads `GetTarget()`), or (b) two
  arch strings `"arm32"`/`"arm32hf"` from `nativeArchForTarget`. Leaning (a)
  as the cleaner target property; will confirm before implementing phase 6.
  Baremetal (soft-float) needs no threading for phases 0–5.

## Phased plan

Each phase keeps the tree green (aarch64/x64 native + host modes unaffected)
and lands as one or a few self-contained commits, cherry-picked to main with
approval per the landing procedure.

### P0 — `common` word-size param + AAPCS32 CallConv — DONE (landed `98d5bef6`)
Landed the word-size parameterisation + AAPCS32 in `pkg/binate/native/common`
(byte-identical for aarch64/x64 — their unit tests stay green; new AAPCS32
coverage added; hygiene clean). Deviations from the sketch below: PlanFrame's
internal frame slots were left 8-byte-granular (over-aligned but correct on
ILP32 — nothing external depends on slot size), so only the ABI-facing
classifiers needed the `WordBytes` treatment; `common_callconv.bn` was split
into `common_callconv{,_variadic,_return}.bn` (+ matching tests) to stay under
the length cap. **Carry-forward:** the AAPCS32 register-count / sret-threshold
numbers (`NumGpRetRegs=4`, `InternalSretBytes=4`, `AggregateInRegMax=16`,
`IndirectLargeAggregates=false`) are first-cut and must be pinned against
`clang -target arm-none-eabi` output before P3 relies on them at the
native↔LLVM boundary.

Original sketch:
- Add `CallConv.WordBytes`; set 8 in AAPCS64/AAPCS64_Darwin/SysV_AMD64.
- Thread it through `ArgWords`, `argRegWordsStackWords` (+V), `PlanFrame`
  (alloca/spill slot rounding, frame align, multi-return N*word, sret slot),
  and the return classifiers. Replace literal `8`/`16`/`*8`/`+7)/8` with
  word-derived values (frame align stays `2*WordBytes` on aarch64 = 16).
- Add `AAPCS32()` constructor: NumGpArgRegs=4, NumGpRetRegs=2 (r0:r1),
  NumFpArgRegs/NumFpRetRegs=0 (soft-float), StackAlign=8, WordBytes=4,
  SplitAggregates=true, sret thresholds (research AAPCS32: small aggregate
  ≤4 bytes in r0, >4 via hidden pointer ⇒ `CExternSretBytes=4`;
  `InternalSretBytes` TBD to match how LLVM lowers Binate multi-returns at
  the native↔LLVM boundary), AggregateInRegMax = 4*WordBytes.
- **New**: even-aligned register-pair rule for 8-byte scalars in
  `argRegWordsStackWords` (int64/uint64/float64-soft start on r0/r2, pad an
  odd GP reg; 8-byte-aligned stack slots).
- **Acceptance**: existing aarch64 + x64 native unit tests + a smoke
  conformance run stay green (byte-identical codegen — WordBytes=8 is a
  no-op refactor for them). Add unit tests for AAPCS32 arg/return
  classification (pair alignment, sret threshold).
- **Verify against LLVM**: dump how `clang -target arm-none-eabi` lowers a
  handful of arg/return shapes (int64 pair, small/large struct return) and
  pin the AAPCS32 numbers to match — this is the native↔LLVM-deps boundary.

### P1 — assembler reloc + extend gaps (`asm/arm32`, `asm/elf`) — DONE (landed `ca15b219`)
- Add fixup kinds `FIX_MOVW_ABS_NC`, `FIX_MOVT_ABS` + `MovwLabel`/`MovtLabel`
  encoders (16-bit imm split, hi/lo, label-relocated).
- Map them in `elf_util.bn` elfRelocType(EM_ARM): →`R_ARM_MOVW_ABS_NC`(43) /
  `R_ARM_MOVT_ABS`(44). Decide BL→`R_ARM_CALL`(28) vs staying
  `R_ARM_JUMP24`(29) for externs (verify under lld; x64 needed the PLT32
  vs PC32 distinction for exactly this).
- Add SXTB/SXTH/UXTB/UXTH extends (or document lowering via LSL+ASR/LSR).
- **Acceptance**: encoder unit tests for the new instructions + a golden
  reloc test (assemble a MOVW/MOVT-addressed symbol, check the ELF rela
  entries).

### P2 — walking skeleton (native baremetal, integer-trivial) — DONE (landed `1592bde7`)
Empty `func main() {}` (`conformance/278_empty_main`) compiles through the new
`pkg/binate/native/arm32` backend into an ELF32 that links via the existing
clang/crt0/semihost/baremetal.ld pipeline and **boots under `qemu-system-arm`,
exiting cleanly** (`builder-comp_native_arm32_baremetal` runner: 1 passed).
Notable deviations / discoveries from the sketch below:
- **Fail-loud on unimplemented ops.** Unlike aarch64/x64 (complete backends whose
  "unhandled op" tail is an unreachable safety net), the skeleton's dispatch
  `a.SetError`s any op it doesn't implement → `EmitObject` returns false → bnc
  compile error. A silent no-op would let a program using an unimplemented op
  compile to a wrong binary (verified: a memory-op test reports COMPILE_ERROR in
  1s, not a qemu-timeout). P3+ lands ops by adding cases, never by relaxing this.
- **Func-value + package descriptor are NOT optional** even for an empty main under
  baremetal: the reflect chain makes `__Package` + its `___handle` live, so a
  reduced `collectFuncValueRefs` + vtable/handle + scalar/void shim + the package
  descriptor had to be ported (~11 files, ~2k LOC total, mirroring aarch64).
- Register model as designed: args R0-R3, scratch R4-R10 + R12/IP, R11=FP,
  `push {r4-r11,lr}`/`pop {r4-r11,pc}` frame, MOVW/MOVT const + MOVW/MOVT-ABS
  symbol addressing. `native/arm32` (31 tests) + aarch64 + x64 green; hygiene 15/15.

Original sketch:
- Create `pkg/binate/native/arm32/{arm32.bni,arm32.bn,arm32_emit_func.bn,
  arm32_dispatch.bn,arm32_regmap.bn,arm32_names.bn}` — enough for a program
  that does `println` of a constant / returns: EmitObject driver (asm.New(4)
  → ELF32), prologue/epilogue (push {fp,lr}; mov fp,sp; sub sp / mov sp,fp;
  pop {fp,pc}), regmap over R0–R3 args + R4–R11 scratch + R12 IP,
  emitConstInt via MOVW/MOVT, symbol addr via MOVW/MOVT-ABS, OP_RETURN,
  OP_CALL of a simple extern (SemihostWriteChar).
- Wire `native.bn` arm32 branch + `target.bn nativeArchForTarget` → "arm32"
  for `arm-none-eabi`.
- Add `conformance/runners/builder-comp_native_arm32_baremetal.sh` +
  unittest sibling (clone the LLVM baremetal runner, add `--backend
  native`). **Do NOT** add to `modesets/all` yet (CI wiring = separate
  user decision, see P7).
- **Acceptance**: a trivial conformance test boots under `qemu-system-arm
  -M virt -semihosting` and prints/exits correctly via the native backend.

### P3 — integer completeness — IN PROGRESS
**Increment 1 DONE (landed `5b628849`; follow-up fix `f7bc261e`):** 32-bit integer
arithmetic/comparison/control-flow + the `println` path (aggregate-slice call
args/returns, memory ops, refcount) → **19 conformance tests pass** under
`builder-comp_native_arm32_baremetal` (was 1). Deferred shapes stay fail-loud
(verified: variable `/`/`%`, multi-return → COMPILE_ERROR). New files
`arm32_{compare,emit,rodata,refcount}.bn`. Two latent bugs found+fixed:
- ARM cross-object branch reloc missing the `-8` pipeline addend (`elfRelocAddend`
  now returns `addend-8` for EM_ARM R_ARM_CALL/JUMP24 RELA; ARM-scoped, aa64/x64
  untouched). This also empirically settled P1's open question — R_ARM_JUMP24
  works for cross-object BL.
- Frame-offset silent-#0: `add rd,sp,#imm` past the rotated-imm range silently
  encoded #0; the backend now materializes large SP offsets via IP.

**Post-land fix `f7bc261e`:** the walking-skeleton (P2) over-wired
`nativeArchForTarget` to return `"arm32"` for BOTH `arm-none-eabi` and
`arm-linux-gnueabihf`, reddening `TestNativeArchForTargetArm32LinuxNoNative` on
main. The native backend is soft-float baremetal-only, so arm32-linux
(hard-float, P6) must stay on LLVM — `nativeArchForTarget` now returns `"arm32"`
only for `arm-none-eabi`, and a new test pins the baremetal→`"arm32"` mapping.
Process lesson: the red test escaped because the P2 landing smoke covered the
`native/` packages but not `cmd/bnc` (whose `target.bn` the change touched) —
derive the smoke set from `git diff --name-only`, not a delegated agent's report.

**Adversarial review (increment 1) — fixed in the commit:** removed R12/IP from
the allocatable register pool (`regPool` is now exactly R4..R10) — it's the
dedicated frame/div/aggregate-overflow scratch, and being a value register too
let a helper silently clobber a live value under register pressure (also fixed
`unsafe_rem`'s divisor clobber and `emitAggregateArg`'s temp aliasing — one
structural fix); made `emitRefInc/DecInline`'s not-materializable branches
`a.SetError` (the dtor path defines the skip label first) instead of silently
dropping a ref-op / leaving a dangling branch; added a `-8` reloc-addend unit
test. All were latent (the 19 tests don't reach them) but real silent-miscompile
footguns for later increments.

**Follow-ups tracked from increment 1:**
- **MAJOR (asm/arm32 hardening):** `encodeOperand2` silently emits `#0` for an
  un-encodable Operand2 immediate instead of `a.SetError` — a latent
  silent-miscompile footgun (the backend now pre-checks/materializes, but the
  assembler should fail loud). Flagged in the P1 review too.
- **Deferred (next increments):** ~~64-bit register-PAIR path~~ (DONE, increment 2);
  ~~single-aggregate-sret arg-register-shift on AAPCS32~~ (DONE, increment 3 / P3.3);
  then the small (≤4-byte) in-register aggregate return/collection, structs/arrays,
  interfaces, multi-return, closures (P4), float (P5).

**Increment 2 DONE (landed `1d38e0dd`):** int64/uint64 as ILP32
register pairs — arithmetic (ADDS/ADC…), compare (SUBS+SBCS, all 6 ops ×
signed/unsigned, clang-matched), mul/div/rem/shift via `__aeabi_*`, cast
widen/narrow/identity + 64-bit bit_cast, and the int64 ABI (even-aligned pairs /
r0:r1 return / param-spill) — which also unblocked guarded variable `/`/`%`/shift.
**Full native-arm32-baremetal conformance: 1464 passing** (all remaining
failures are deferred shapes that COMPILE_ERROR — fail-loud verified). No
register-pair template existed (aa64/x64 are LP64); verified against clang +
AEABI, with a 2-reviewer adversarial pass. Two MAJOR bugs found + fixed: the
runtime guard ops were missing from `common_call.bn`'s `isCallOp` (PlanFrame
reserved zero outgoing-args for DivCheck's stack args — LP64-inert, AAPCS32
overlap) and a 64-bit `bit_cast` dropped the high word (no OP_BIT_CAST case in
emitInstr64 → 32-bit single-word fall-through).

**Increment-2 follow-ups DONE (landed `7b02e4c5`):** int64-ABI unit tests
(call-arg even-pair / return / param-spill) + the bare-return→`a.SetError`
fail-loud consistency pass in the int64 emitters (27 conversions, behavior-neutral
— identical before/after failure sets; adversarial-reviewed clean).

**Conformance-harness gap FIXED (landed `8c2a3866`):** the native-arm32 modes
now inherit their LLVM sibling's per-mode `.expected`/`.error`/`.xfail` overrides
via an `OVERRIDE_MODE` fallback in `conformance/run.sh` (native arm32 has the same
ILP32 layout + baremetal/linux env as `builder-comp_arm32_baremetal`, whose 63
overrides it previously ignored — producing false failures like `sub/64/unsigned`
and the baremetal `bootstrap.Exec` xfail). Non-native modes are provably
unaffected (guarded on `OVERRIDE_MODE` being non-empty). So the native-arm32
conformance pass count is now meaningful (harness-gap false failures removed);
remaining failures are genuine deferred shapes (fail-loud) or real gaps.
Authoritative post-fix count: **`builder-comp_native_arm32_baremetal` = 1499
passed / 1079 failed / 32 skipped** (up from 1483 / 1121 / 3 pre-fix: +16 now
pass via inherited ILP32 `.expected` overrides, +29 now correctly XFAIL-skip via
inherited arm32 xfails). The 1079 remaining failures are the deferred shapes —
float, structs/arrays, interfaces, multi-return, closures, aggregate-sret
arg-shift, impl vtables — which all COMPILE_ERROR (fail-loud), plus a few real
gaps to triage as later increments land.

**Increment 3 DONE (P3.3, landed `d9567498`):** AAPCS32 single-aggregate sret —
a function returning a `>4-byte` aggregate (every slice, most structs) returns it
via a hidden R0 buffer pointer, shifting real args to R1+; the callee stashes R0,
shifts params, writes the result through R0 and reloads it. Mirrors the x64 SysV
sret-in-RDI template with WordBytes=4 (aa64 uses X8, a different model). One
shared change: `SretInGpArgReg=true` on AAPCS32 (per-CallConv, sizing-only — makes
PlanFrame count the sret slot in outgoing-args; x64/aa64 byte-identical). **Full
native-arm32-baremetal conformance: 1573 passed / 1013 failed / 32 skipped**, no
XPASS. Adversarial review (4 diverse lenses + refute-verify) caught one **critical
fail-loud regression, fixed in the landed commit**: narrowing the aggregate guards
to sret-only turned the **small (≤ InternalSretBytes = 4) in-register aggregate
return/collection** — a distinct non-sret shape — from fail-loud into silent
wrong-code on both callee and caller; restored to fail-loud (the x64 backend packs
this class in-register via `emitAggregateReturnPack`; the arm32 in-register pack is
deferred to P4). Tests: small-aggregate fail-loud (both sides), strengthened
`emitSretReturn` (exact copy-store count + R0-reload — the prior test passed even
with the R0 reload dropped), AAPCS32 PlanFrame sizing regression test, caller
arg-shift-to-R1, and `conformance/966_return_small_struct` (xfail'd for native
arm32). Two review claims correctly refuted (a harmless `CalleeUsesCSret`
redundancy matching the template; an odd-size memcpy over-copy provably in-bounds
since regions are word-rounded).

**Deferred to P4 (fail-loud today):** the small (≤4-byte) in-register aggregate
return/collection (callee pack into R0 / caller collect from R0 — mirror x64's
`emitAggregateReturnPack` + `!bigRet` store); plus multi-return (in-register tuple
+ >budget sret). See `claude-todo.md`.

Remaining P3 work (original sketch):
- Port `arm32_ops.bn` (int arith/bitwise/shift/compare/unary/cast/const,
  sub-word narrow with wordBits=32), `arm32_emit.bn` (ALLOC/MAKE/BOX/
  MAKE_SLICE/EXTRACT/LOAD/STORE/GET_ELEM_PTR/GET_FIELD_PTR),
  `arm32_call.bn` + `arm32_call_indirect.bn` (AAPCS32 arg dispatch, sret,
  aggregate split, return collection), `arm32_return.bn`, `arm32_refcount.bn`
  (near-verbatim), `arm32_rodata.bn`, `arm32_iface.bn`,
  `arm32_pkg_descriptor.bn`.
- 64-bit values: register pairs (adc/sbc for add/sub, umull/smull for mul,
  `__aeabi_ldivmod`/`__aeabi_llsl`/… for div/shift), pair-aligned args.
- Conditional-execution note: every `asm/arm32` op takes a leading `cond`;
  compares that aarch64 realizes via CSINC map to CMP + conditional MOV.
- **Acceptance**: the integer subset of the conformance suite passes in
  `builder-comp_native_arm32_baremetal`. Track remaining failures with
  xfail + todo per the Bug Discovery Protocol.

### P4 — func values, closures, interfaces

**P4-a DONE (landed `a888e9cd`):** func-value / indirect-call CONSUMER path +
the shim's big-aggregate sret return shape + all six dispatch cases
(OP_CALL_INDIRECT, OP_CALL_FUNC_VALUE, OP_CALL_HANDLE, OP_FUNC_HANDLE,
OP_FUNC_VALUE, OP_FUNC_VALUE_DTOR). New `arm32_call_indirect.bn` (emitCallIndirect
+ emitCallFuncValue, mirroring x64's SretInGpArgReg model — target reloaded after
the arg loop into IP before BLX, WordBytes-offset vtable.call load, reusing
emitCallArg/emitCallReturn). Shim split into emitFuncValueShimBody →
emitScalarVoidShim / emitSretShim (R0-kept sret, no X8) + emitFuncValue
construction. NO shared/common changes (PlanFrame/callDispatchArgTypesAnyOp/layout
offsets already target-parameterized; LP64 byte-identical). Adversarial review
(4 lenses) found 0 defects. Non-capturing func-value construct/call/handle-dispatch
now run end-to-end under QEMU; conformance **1898/727/32** (+118 pass). (The P4-a
land claimed "0 `[10s]` hangs" — WRONG: the hang-detection grep was faulty and
missed a cross-pkg aggregate-arg silent-miscompile hang, fixed in P4-b below.
Hang audits MUST grep the QEMU "terminating on signal" message on the FULL
verbose output.)

**P4-b1 DONE (landed `bc42705e`) — small in-register aggregate return + a MAJOR
cross-pkg aggregate-arg bug fix:**
- Small (≤4-byte) in-register aggregate RETURN across callee (`arm32_return.bn`
  one-word R0 pack), direct-call caller (`arm32_call.bn` R0→data-region collect),
  func-value consumer (retbuf-in-R0), and func-value SHIM (new `emitPackShim`:
  push {r4,lr} frame, save retbuf in R4, BL, store R0 through retbuf). Flips
  `966_return_small_struct` (xfail removed).
- MAJOR bug fix (silent miscompile since P4-a): the func-value consumer marshaled
  aggregate ARGS via the direct-call ABI (spread words), but the shim ABI passes a
  coerced in-register aggregate BY-ADDRESS. Cross-pkg (LLVM shim) mismatch → wild
  deref → runtime HANG (`889_funcval_small_aggregate`). Fixed by substituting
  `AggCoercedInReg` args to a 1-word `*uint8` and passing the pointer by-address
  (matches `EffectiveArgWords` + the LLVM shim). A NON-coerced in-register
  aggregate arg (slice / iface-value / managed-slice) now fails LOUDLY (a
  pre-existing cross-backend gap shared with x64/aa64 — see claude-todo.md).
- No shared/common changes. Conformance **1908/718/31**, **0 hangs** (verified via
  the QEMU "terminating on signal" grep). 716/718 failures are clean fail-loud
  COMPILE_ERROR; the only 2 wrong-output failures are `725`/`727` (pre-existing
  reflect miscompile, unrelated — raised separately).

**P4-b2 DONE (landed `e1e49b73`) — multi-return:** in-register tuple (callee
`emitMultiReturnPack` field-per-register into r0..r3 + caller
`collectMultiReturnFields`) + >budget sret (callee `emitMultiReturnSret`
write-through at `FieldOffset` + caller collect); func-value consumer + shim
classification (`arm32_funcvalue_multiret.bn`). int64/float64 tuple fields
fail-loud (even-aligned-pair placement unpinned). **Also fixed a MAJOR shared bug**
(found by the review): big-multi-return FUNC-VALUE calls under-reserved
outgoing-args (emitter prefixSlots=2 via SretInGpArgReg vs sizer's 1) → cross-module
silent miscompile on arm32 AND x64; fixed with a gated `prefixSlots=2` bump in
`callDispatchArgTypesAnyOp` (inert on aarch64). Conformance **2007/619/31** (+99),
0 hangs; x64 native units + conformance verified green.

**Remaining P4 sub-increments (fail-loud today):**
- **Follow-ups from P4-b1/b2 (separate from P4-c/d):** the SAME-package
  aggregate-arg SHIM re-marshaling (725/727 cross-pkg reflect was NOT a
  miscompile — stale arm32 expected files, resolved `4fe304dd`; see done log);
  and a **NEW aarch64-native crash** — a cross-pkg big-multi-return func value
  produces empty output on `builder-comp_native_aa64-comp_native_aa64` (a BLOCKING
  green mode), a distinct pre-existing bug (aa64 rides X8, not the arm32/x64
  under-reservation) exposed by the P4-b2 review; see claude-todo.md.
- **P4-c:** interfaces — ✅ COMPLETE 2026-07-06 (all sub-phases landed). Biggest
  bucket, and a HIGH-CONFIDENCE MECHANICAL PORT
  (recon 2026-07-06). The interface ABI is platform-agnostic at the shared layers
  (2-word `{data,vtable}` value, vtable slot layout `[0]=dtor,[1]=*TypeInfo,[2+]=
  methods`, absolute-slot Index precomputed in IR, the sizer's OP_CALL_IFACE_METHOD
  branch, all mangling + RTTI builders) and already serves arm32 via the LLVM
  backend. Every per-target diff is CC parameterization arm32 has from P4-a/b.
  **Key decision (locked): the CALL path ports from x64, not aa64** — AAPCS32
  `SretInGpArgReg=true` → `prefixSlots=2`, so arm32 uses x64's `[sret,data,args]`
  2-slot prefix + spill/reload target (IP can't be held live across arg marshaling),
  NOT aa64's X8-sret + hold-live. The VTABLE-EMISSION path ports line-for-line from
  aa64. **Scope (locked 2026-07-06): full 5-phase parity, land per phase; include
  big-aggregate + multi-return iface returns in the core phase; full RTTI (`__ivt` +
  `__ivtshim` + `__typeinfo` + `__ifaceid` + `__satentry`) + `OP_IFACE_UPCAST`.
  Fail-loud-defer generic-impl vtables and int64/float64-in-tuple placement; keep
  nil-iface-dispatch-on-baremetal xfail (no-MMU env limitation).**
  Phases:
  - **P4-c.1** — ✅ DONE & LANDED 2026-07-06 (data side; commits `4ebb2321` + `b6179612`):
    real `emitImplVtables` (`__ivt`/`__ivtshim`), `collectImplVtableSlotsNative`,
    mangling helpers, new `arm32_typeinfo.bn`
    (`emitTypeInfos/IfaceIds/SatEntriesNative`). Ported from
    `aarch64_iface.bn`/`aarch64_typeinfo.bn`; data laid by shared `ir.BuildImplVtable`
    (byte/name-identical to LLVM, adversarial-reviewed). Dispatch fail-loud;
    generic-impl fail-loud. Unit tests pin mangling + slot ORDER (vs shared
    `mangle.*`/`ir.BuildImplVtable`). NOTE: arm32's generic-impl fail-loud is
    **whole-module abort** (SetError + return on the first generic impl, dropping
    the module's non-generic vtables too), vs LLVM/aa64 which emit the raw `__ivt`
    per-impl and gate off only the shim — the future P4-c generic pass should narrow
    arm32 to the per-impl form. (One known test-infra flake tracked in claude-todo.md:
    the shape tests' intermittent LP64-doubling.)
  - **P4-c.2** — ✅ DONE & LANDED 2026-07-06 (`094d38bf`): `OP_IFACE_VALUE` +
    `OP_IFACE_UPCAST` construction ops (2-word {data,vtable} build; upcast vtable
    +offsetSlots*wordBytes; invalid upcast fails loud). Byte-identical to LLVM/aa64
    (adversarial-reviewed); native conformance 2036/604/0-hangs (+10, no regression).
  - **P4-c.3** — ✅ DONE & LANDED 2026-07-06 (`9c00b2f1` + `c6e2391f`):
    `OP_CALL_IFACE_METHOD` (core, x64 template): two-step LDR (vtable then method
    ptr), spill the method ptr via a POOL reg, synth argTypes `[sret?, data, args]`
    per prefixSlots=2, marshal, reload, `Blx`, collect via the shared
    `collectMultiReturnFields`/`storeMultiReturnTupleFieldsArm32` (scalar / void /
    aggregate-sret / multi-return). Adversarial review verified the 3 paramount
    properties (prefix/sizer match, slot index, return collection) and caught a
    MAJOR spill bug (method ptr spilled from IP self-corrupts on a >4095-byte frame)
    → fixed (`c6e2391f`, spill via pool reg; unit-tested — conformance can't reach
    it, shadowed by the large-frame COMPILE_ERROR bug in claude-todo.md). Native
    conformance 2097/543/0-hangs (+61 vs P4-c.2); the 543 remaining are documented
    deferred buckets (143 need the dtor → P4-c.4, float → P5, closures → P4-d).
  - **P4-c.4** — ✅ DONE & LANDED 2026-07-06 (`7b99a1cf`): `OP_IFACE_DTOR` — a
    two-step vtable LOAD (vtable at IfaceValueVtableOffset, then slot 0 = the dtor
    handle), NOT a call; the invocation happens in the OP_REFDEC → rt.ZeroRefDestroy
    slow path (which null-guards), mirroring OP_FUNC_VALUE_DTOR. Review LAND (zero
    issues; refcount fires exactly once, no leak/UAF). Native conformance
    2234/418/0-hangs (+137 over 2097) — the managed-receiver/@Iface/lifecycle bucket
    greened (incl. 554_iface_refcount_balance / 368_iface_managed). Remaining 418 are
    other deferred buckets (func-value-shim/P4, closures/P4-d, generics, float/P5).
  - **P4-c.5** — ✅ DONE 2026-07-06 (sweep + xfail reconciliation): native arm32
    conformance at 2234/418/0-hangs after P4-c.1-.4; the iface data / value /
    dispatch / dtor / lifecycle tests are green (incl. cross-pkg iface dispatch,
    managed-iface refcount balance, iface-value construct/upcast). No iface xfail
    markers needed reconciling (0 exist for the native arm32 modes — only 2 markers
    total, both `__c_global`, unrelated; 0 XPASS). The remaining 418 failures are
    all OTHER incomplete-feature buckets — func-value-shim aggregate/float/spill
    residuals (P4), capturing closures (P4-d), generics (non-generic-only for now),
    and float (P5) — tracked in their own plan phases, NOT iface-dispatch defects.
    **⇒ P4-c (interfaces) is COMPLETE.**
- **P4 func-value-shim residuals** (recon 2026-07-07; scoped P0/P1/P2/P3):
  the shim fail-louds on aggregate args, over-budget (spill) args, and float
  args/returns. Corrected scope: the 6 `funcval-param` matrix cells each pass ONE
  <=16B by-value struct → all `AggCoercedInReg` → need REGISTER-ONLY by-address
  marshaling (not spill); `iface-param` is a SEPARATE path (`emitCallArg`, not the
  shim). **P0+P1 LANDED 2026-07-07 (`97e0e5e0`); P2+P3 deferred.**
  - **P0** ✅ — measured per-test failure modes. Correction: `iface-param` cells
    ALSO failed via the SAME shim gate (the impl method's `__handle`/`__ivtshim`
    shim is emitted for every collected ref and hit the struct-arg abort, even
    though the dispatch call uses `emitCallArg`), so P1 greened all 12 matrix cells.
  - **P1** ✅ — register-only coerced-aggregate shim marshaling (`emitShimArgMarshalArm32`
    / `emitShimAggregateArgArm32` in `arm32_funcvalue_marshal.bn`; `shimOutRegs`
    budget walk + `needStage` staging via a balanced SP scratch), GP-only; ported
    from aa64/x64 register path. Native `builder-comp_native_arm32_baremetal`
    2236 → 2271. Adversarial review caught two CRITICALs pre-land, both fixed in
    the same commit: (1) the marshal omitted the AAPCS32 §6.5-C.3 **even-register-pair
    bump** for 8-aligned coerced aggregates (int64/float64-field structs) → silent
    miscompile vs the callee; fixed via a shared `cc.NeedsEvenReg` predicate used by
    BOTH the shim and `argRegWordsStackWords`, with the budget walk counting pads so
    an over-budget-after-pad case fails loud. (2) the staging **SP-adjust was inverted**
    (ADD-then-SUB) → wrote staged words above entry-SP into the caller's frame; fixed
    to SUB-allocate/ADD-free. Conformance `993_funcval_int64_struct_evenpair` drives
    the even-pair path end-to-end (mutation-confirmed). Lesson: porting an LP64
    (x64/aa64) ABI helper to ILP32 (arm32) makes the even-pair bump — inert on LP64 —
    load-bearing; and a byte-exact unit test against a hand-built reference bakes in a
    systematic direction bug (the inverted SP-adjust) and cannot catch it.
  - **P2 (= straddling-agg "PIECE 1") ✅ LANDED 2026-07-09 (`3af44f26`)** — over-budget
    stack-spill shim (`arm32_funcvalue_spill.bn`). All four shim shapes convert to a
    framed BL and spill args past R0–R3 to the outgoing-args area; placement DRIVES the
    shared classifier (`CallArgRegStart`/`CallArgStackOff`/`CallStackBytes`) over the
    same prefixed sret-ptr + full-cc type list the callee (`emitSpillParam`) uses, so
    even-pair pad + SPLIT agree at any GP-reg-file origin. Native conformance 2271→2290
    (+19: 992, 994, managed-multi-return cells). Adversarial review caught a CRITICAL
    pre-land: the first cut modeled the sret retbuf by *reducing* `NumGpArgRegs` (relative
    classification), which dropped the even-register-pair parity the retbuf's R0 forces →
    silent miscompile of an 8-aligned coerced-agg arg through an sret shim (ILP32-unique;
    the x64/aa64 templates can't exercise reduce-file × active-even-pair). Fixed by the
    prefixed-full-cc classification above; covered by `994`'s `sretEvenPair` case +
    `TestFuncvalSpillSretEvenPairByteRefArm32` (both mutation-verified). R4 is the
    memory-shuttle scratch (saved/restored); non-coerced-aggregate/float/reg64/closure
    stay fail-loud.
  - **PIECE 2 ✅ LANDED 2026-07-10 (`a08fdab0`) — non-coerced in-register aggregate args**
    (slice / iface-value / managed-slice, all ≥8B). User chose OPTION B (by-address, matching
    the LLVM shim's `shimParamType` contract) over option A (inline, which would extend the
    tracked native↔LLVM inline/by-address divergence to arm32). A single arm32-local
    `isByAddressAggArm32` predicate widened the coerced by-address→spread path to cover
    non-coerced small aggregates (they re-expand their `ArgWords` value-words the same way),
    with an arm32-local incoming word count (`shimInWordsForTypeArm32` = 1 by-address word,
    not the shared LP64 `EffectiveArgWords`). Native conformance 2290 → 2319 (greened
    `364_funcval_slice_arg`, `598_iface_dispatch_multiword_arg`, variadic-funcvalue cells,
    `iface-byval-17-byte`, etc.). Validated at the REAL boundary by `1006_funcval_xpkg_llvm_shim_dispatch`
    (native `main` dispatches a func value CREATED in an LLVM-compiled dep, so its shim is
    LLVM-emitted — nm-proven + mutation-verified: forcing the slice inline crashes). Two
    adversarial-review scares resolved: (1) the anon-struct "cross-package miscompile" was a
    FALSE ALARM — source-level anon struct *types* get a synthetic `__anon_N` name so
    `AggCoercedInReg` is true (coerced/by-address on both sides, always correct); (2) the
    naive `995` litmus doesn't test the native-dispatch→LLVM-shim boundary (main builds its
    own native shim), which is why `1006` (func value created in the dep) is the true litmus.
    Coerced aggregates (arrays / named / anon structs, incl. ≤4B) ride the existing
    `AggCoercedInReg` by-address path. **⇒ arm32 func-value shim aggregate-arg support is
    COMPLETE** (P1 coerced + P2 spill + PIECE 2 non-coerced); remaining shim gaps: float
    args/returns (P5), capturing closures (P4-d).
  - **64-bit register-pair scalars (P4) ✅ LANDED 2026-07-10 (`813836bb`)** — an ILP32
    int64/uint64 is a 2-word, 8-aligned (`NeedsEvenReg`) register-pair scalar (arm32-unique;
    aa64/x64 are LP64 so int64 is one reg — no ported template). The shim now marshals it
    INLINE (2 value-words) with the AAPCS §6.5-C.3 even-pair pad on BOTH the incoming
    (`srcPrefix+srcWord`) AND outgoing (`gpDestBase+ngrn`) cursors — the dispatch even-aligns
    the incoming int64 too, so missing the incoming pad reads the value-words from the wrong
    registers (the silent-miscompile surface, mutation-verified). SPLIT-reachability invariant:
    an int64 never SPLITs (the even-round sends it whole to a reg pair or the 8-aligned stack).
    The 5-lens review surfaced a PRE-EXISTING silent-miscompile — an 8-aligned indirect-large
    (>16B) aggregate func-value arg was even-pair-padded by the dispatch/callee but NOT by the
    shim's generic branch — which was FOLDED IN: a single `paddedKeepsTypeArm32` predicate
    (`NeedsEvenReg && !isByAddressAgg && !float` = the args that keep their real dispatch type)
    now gates every pad site (marshal incoming+outgoing, spill incoming, `shimInWordsArm32`
    sizing, `shimOutRegs` budget), so shim ↔ dispatch ↔ callee agree at every GP-reg origin.
    Native conformance 2328 → 2333 (greened `881_funcval_xpkg_struct_return` — a real int64
    through `time.FromUnix` via a func value — + tests 1018-1021). A focused adversarial review
    verified the predicate is an exact bijection with the dispatch's `*uint8` substitution.
  - **P3 (deferred, P5-gated)** — soft-float float args/returns: may be shim-trivial
    (soft-float rides GP, no fmov) but the wider soft-float pipeline isn't ready →
    wrong-code risk; a user decision, not an inline relaxation.
- **P4-d:** capturing closures + method values (backend port — the mechanism is fully
  lowered in shared IR/codegen; arm32 reads the capture-struct layout + wires the shim +
  non-null dtor slot). Method values ride the closure path for free. Phased A→B→C.
  - **Phase A ✅ LANDED 2026-07-10 (`99a47f25`)** — memory-safety core + scalar/void FAST
    shim: (1) non-null dtor emission (`funcValueDtorHandleSymArm32`) for a managed capturing
    `@func` whose struct `NeedsDestruction`, (2) construction data-slot store (the capture-ptr),
    (3) `emitClosureShimArm32` (register-only): UP-shift user args by `captureWords`, load
    captures from `[R0+FieldOffset(i)]` right-to-left (i=0 last so R0 base survives), tail-branch.
    Sub-parts 1+2 landed together (the never-leak rule — a null dtor on a managed capturing
    closure silently leaks the capture block). The capture-prepend shifts every user arg's
    outgoing register parity, so user args classify over the full captures++users list with
    `gpDestBase = captureWords` (the even-pair machinery from the int64 work). A `forceStage`
    flag stages incoming words so the UP-shift is clobber-safe (the marshal is otherwise
    DOWN-shift-only); the 4 non-closure callers pass `forceStage=false` (byte-identical).
    Native conformance 2333 → 2417 (~50 closure/method-value cells; managed-capture leak cells
    509/511/515/550/900 pass). 5-lens adversarial review: sound; one NIT — an 8-aligned
    indirect-large (>16B) aggregate CAPTURE fails loud today only via an `ArgWords` over-count,
    not an explicit guard (correct-today, latent fragility).
  - **Phase A NIT-fix ✅ LANDED 2026-07-10 (`db6e6338`)** — explicit `isIndirectLargeArm32`
    capture fail-loud in `emitClosureShimArm32`'s capture-counting loop (before the
    `CallArgRegStart`/`captureWords` step), plus a comment noting surviving captures have
    Σ`ArgWords` == true footprint, plus `TestClosureShimIndirectLargeCaptureSetsError` (24-byte
    `struct{int64,int64,int64}` capture asserts `a.HasError`). Closes the Phase A NIT.
  - **Phase B ✅ LANDED 2026-07-10 (`26978ead`)** — capturing stack-spill shim (framed-BL
    over-budget shim: incoming-reg staging, CAPTURE-PREFIX classify at classifyBase =
    NumCaptureParams, stack-bound-capture spill, right-to-left reg-capture loads, BL +
    teardown). Full native conformance 2417 → **2460** (+43). The 5-lens adversarial review
    flagged a "critical silent miscompile" in the capture-spill store (value in IP,
    clobbered by a > 4095 offset materialization) — on rigorous analysis a FALSE POSITIVE
    (unreachable: any frame large enough trips a loud imm12 error from the staging store /
    capture load first), but it surfaced the real **large-frame** gap below.
  - **Large-frame fix ✅ LANDED 2026-07-10 (`fb221c52`)** — both spill paths (closure +
    pre-existing non-closure) addressed the frame with raw LDR/STR immediates, which trip
    imm12's loud range error past 4095, so a func value / closure spilling > ~1024 words
    failed to compile (reachable at a modest count via sub-word captures whose 4-byte
    outgoing slots outrun the packed struct). Routed every frame-relative access through
    the materializing wrappers (emitFrameStore / emitFrameLoad / emitBaseLoad), with the
    capture value shuttled through R4 (not IP) so a > 4095 store materialization can't
    clobber it. Byte-identical on every ≤ 4095 path (conformance unchanged at 2460);
    adversarial-reviewed clean (0 surviving findings).
  - **By-address-agg large-frame full support ✅ LANDED 2026-07-11 (`f74d59ee`)** — the one
    guarded corner (a by-address AGGREGATE argument whose stack tail exceeds 4095) is now
    served instead of fail-loud: emitSpillByAddrAggArm32 RE-READS the value-buffer pointer
    into IP per stack word (R4 is free at the top of each word), loads the value, then
    materializes the outgoing offset into IP and stores `[SP, IP]`. destOff is monotonic in
    k, so a small-offset word never follows a large one. Byte-identical on every ≤4095
    by-address-agg path; the former fail-loud test is now a compile test, and
    `TestSpillByAddrAggReReadByteRef` pins the re-read sequence byte-for-byte (conformance
    can't reach >~1024-word frames). Adversarial-reviewed clean (0 surviving findings; all 5
    invariants verified). NOTE: the pre-fix full conformance run was corrupted by a
    concurrent `/tmp` gen1 deletion; verification rests on the byte-identity of the reachable
    path (unit byte-refs), a gen1-builds smoke, and the review — a clean post-land run
    confirms.
  - **Phase C ✅ COMPLETE 2026-07-11** — aggregate-result + multi-return capturing-
    closure / method-value shims. Removes the `shimReturnIsNonScalarArm32` fail-loud in
    `emitClosureShimArm32` and adds the four AAPCS32 result shapes for closures, composing two
    already-solved axes: the capture-prefix ARG machinery (Phase A/B) and the non-closure
    RESULT machinery (`emitFuncValueShimBody` / `emitSretShim` / `emitPackShim` /
    `emitMultiRetPackShim` + `prependSretPtrArm32` + the retbuf-aware `emitSpillMarshalArm32`).
    - **The crux (X8 vs R0):** arm32 passes the sret buffer pointer in R0 — an ARG register
      (`SretInGpArgReg`) where the capture-struct base lives — whereas aa64 uses X8 (dedicated
      indirect-result reg) + X9 (non-arg scratch for the capture base), neither an arg reg. So
      arm32 must (1) thread a `captureBase` register param through the capture-load helpers
      (default R0; **R1** for retbuf shapes — the analog of aa64's X9 threading), and (2) for
      sret prepend a `*uint8` slot into the SAME capture-prefix classify (`[sret]++captures++
      users`, users at `classifyBase = 1 + NumCaptureParams`) so R0=retbuf stays and the even-
      pair parity matches the callee; for pack, save retbuf to a frame slot. On 4 arg regs the
      retbuf prefix over-budgets fast → the framed spill (reusing `emitClosureShimSpillArm32`'s
      skeleton with `srcPrefix=2`) is the common case.
    - **Must-add guard:** `closureHasFloatPartsArm32` only rejects a float SCALAR result; a
      float FIELD of an aggregate/tuple result must ALSO fail loud (P5) before the dispatch —
      else it silently rides the GP-only store (a miscompile).
    - **Sub-phases (each independently landable + byte-ref + conformance tested):** C.0 —
      plumbing: thread `captureBase` — ✅ LANDED 2026-07-11 (`55f3076c`, byte-identical).
      C.1 — big single-aggregate sret — ✅ LANDED 2026-07-11 (`868d8768`). Reuses the
      non-closure sret machinery (`prependSretPtrArm32`
      + retbuf-aware `emitSpillMarshalArm32`) with a capture-prefix classify: users at
      `classifyBase = 1 + NumCaptureParams` over `[*uint8 sret]++captures++users`, captures
      from R1 (`captureBase = R1`); frameless fast path (verified the user marshal never
      writes R0/R1 — `gpDestBase >= 2` — so retbuf survives to the tail-branch, risk R2
      cleared) + framed spill (the common case on 4 arg regs). Added the must-add float-in-
      aggregate-result P5 guard (`closureResultHasFloatPartArm32`, risk R3). New files
      `arm32_closure_shim_aggregate{,_spill}.bn` (+ per-file byte-ref tests, mutation-
      verified). The sret capture-load path REUSES the C.0-threaded helpers
      (`emitClosureCaptureSpillArm32` / `emitClosureCaptureRegLoad{,Spill}Arm32`) via a
      `classifyOff` (0 scalar / 1 sret) + `regBase` (0/1) param rather than duplicating them —
      so C.0's `captureBase` param is genuinely used and there is ONE canonical copy of the
      right-to-left capture-load logic (an adversarial review flagged the initial duplication;
      the unification is byte-identical for both shapes). Conformance now GREEN in `builder-comp_native_arm32_baremetal`: 906/907/
      921–925 (all 7 big-sret closure shapes) and 948/952 (method-value sret). Small-agg
      pack + multi-return STILL fail-loud by intent (at the C.1 cut) — `-multi-return`
      remains COMPILE_ERROR (C.3), NOT a regression.
      **C.2 — small-aggregate pack — ✅ LANDED 2026-07-11 (`f12eb8f8`).** A single aggregate
      result with SizeOf ≤ 4 (returned by value in R0).  Always framed (no tail-branch):
      captures classify densely from R0 (no sret slot, srcPrefix 2), the retbuf is stashed to
      a frame slot + reloaded to R4 after the BL for the STR-through-retbuf.  THE CRUX (unlike
      C.1 sret, capture 0 targets R0 ≠ the R1 data base, so right-to-left doesn't protect the
      base): the data pointer is RE-HOMED into R5 (a callee-saved reg; the entry push is
      widened to `{r4,r5,r6,lr}`, R6 = alignment padding), read once from the staged data slot;
      R5 is never a capture/user/marshal destination, so it survives.  New files
      `arm32_closure_shim_pack{,_test}.bn`; byte-ref tests incl. the R5-base-survival mutation
      (2 captures where capture 1 targets R1).  Adversarial-reviewed clean (0 survivors; R5
      survival, frameDelta with the 16-byte push, fail-loud/float-guard/byte-identity all
      traced).  Clears `regressions/capturing-closure-aggregate-return` (its `P1{a int}` is a
      4-byte pack on ILP32); C.1 big-sret unchanged.  **Follow-up ✅ LANDED 2026-07-11
      (`e233b8c3`):** 5 pack-shape byte-refs for the pack path (base R5) under a
      by-address-agg user arg / int64-pair user / even-pair-pad int64 capture / SPLIT capture /
      C.3 1-cap-2-user small-multiret — each mutation-verified (a permanent R5→R1 clobber
      reference must diverge byte-for-byte).  A 0-byte-aggregate pack-store guard
      (`emptyAggregatePackResultArm32`) was originally added here, **but it and the whole
      pack-store-guard approach were SUPERSEDED + REMOVED by the holistic 0-byte fix
      `7b4303a6` (2026-07-12):** a further adversarial review found the guard sat atop a
      DEEPER silent-miscompile — a 0-byte `func() struct{}` call was DROPPED before the call
      on ALL 3 native backends (the call-site classified `struct{}` as needing a retbuf via
      the kind-only `IsAggregateTyp`, but `PlanFrame` reserves none for `dataSz==0`, so
      `emitCallFuncValue` bare-returned before the call). 0-byte results are now routed as
      void-like everywhere via `types.IsAggregateReturn` (new `IsAggregateReturnTyp` adapter),
      which ALSO SUBSUMES the x64/aa64 scalar-void-fall-through audit — see the done-log entry
      "0-byte func-value results mishandled across all 3 native backends".
      **C.3 — multi-return — ✅ LANDED 2026-07-11 (`067f990a`).** Splits on
      `isBigMultiReturnArm32` = `MultiReturnTupleNeedsSret` = gpWords > NumGpRetRegs 4 (a
      WORD-count rule — the identical predicate the callee + non-closure caller use, so the
      retbuf-vs-field-register convention can't diverge; e.g. `(int64,int64)` = 16B but 4 GP
      words → small/pack). BIG (gpWords>4) reuses the C.1 `emitClosureShimSretArm32` UNCHANGED
      (it's result-shape-agnostic — never reads `Results`, the underlying writes the tuple
      through the R0 retbuf). SMALL reuses the C.2 pack framing via a shared core
      `emitClosureShimPackCoreArm32(…, retTuple)` (byte-identical for the C.2 single-aggregate
      `retTuple=nil` case), with the only delta being the post-BL store —
      `storeMultiReturnTupleFieldsArm32` (field-per-register through the retbuf, shared with
      the callee's `emitMultiReturnPack`) instead of a single `STR R0`; an int64 tuple field
      fails loud (shared `emitScalarStore` sz==8), a clean deferral not a silent narrow store.
      Byte-ref units + a new EXECUTION conformance test `regressions/closure-multiret-through-
      value` (a capturing closure returning `(int,int)` [pack] and `(int,@[]int)` [sret]
      called INDIRECTLY through a helper so the shim can't be devirtualized — forces the shim
      to run on qemu; passes native arm32 + LP64). Adversarial-reviewed clean (0 survivors; the
      review's only substantive finding — the shim path was byte-ref-only — was closed by that
      execution test). `regressions/capturing-closure-multi-return` also now passes.
      (`950_method_value_multiret` was already fixed by an ancestor and routes through the
      callee, NOT this shim.)
- **Acceptance**: func-value / closure / interface conformance + unit tests
  pass in native baremetal. **Phase C (all closure/method-value result shapes — scalar/void,
  big-sret, small-pack, multi-return) COMPLETE 2026-07-11; only float parts (P5) and
  indirect-large captures remain fail-loud on the closure path by design.**

### P5 — soft-float (baremetal complete)
- `arm32_float.bn` (soft-float): float args/returns in GP regs (f32→r0,
  f64→r0:r1); arithmetic → `__aeabi_fadd/fsub/fmul/fdiv`,
  `__aeabi_dadd/…`, compares → `__aeabi_fcmp*`, conversions →
  `__aeabi_f2d/d2f/i2f/f2iz/…`. No VFP encoders needed.
- **Acceptance**: float conformance tests pass in native baremetal;
  `builder-comp_native_arm32_baremetal` is fully green (or every residual
  failure is xf?'d with a tracked root-cause todo).

### P6 — VFP + hard-float (arm32-linux complete)
- Confirm float-ABI threading decision (TargetInfo field vs arch string).
- Add VFP encoders to `asm/arm32` (VLDR/VSTR/VMOV core↔VFP, VADD/VSUB/VMUL/
  VDIV .f32/.f64, VNEG, VCMP + VMRS APSR_nzcv, VCVT int↔float & f32↔f64).
- Add AAPCS-VFP CallConv variant (NumFpArgRegs>0, IsFloatScalar routing like
  aarch64) + hard-float `arm32_float.bn` path.
- `nativeArchForTarget` → arm32 (hard-float) for `arm-linux-gnueabihf`; add
  `builder-comp_native_arm32_linux` runners (clone LLVM linux runner +
  `--backend native`, qemu-arm user-mode).
- **Acceptance**: `builder-comp_native_arm32_linux` green.

### P7 — CI integration + full sweep
- **DONE (partial, `0727d0c1`, 2026-07-03):** `builder-comp_native_arm32_baremetal`
  is wired into `.github/workflows/conformance-tests.yml` as an **experimental**
  matrix entry (`continue-on-error`, red-signal/non-blocking), mirroring
  `builder-comp_arm32_linux_int` — user-approved ("add as experimental extra").
  Deliberately NOT in `scripts/modesets/all` (keeps it out of unit/perf/xpass).
  Toolchain is auto-covered (mode string contains `arm32_baremetal`).
- **Remaining:** promote to a blocking `modesets/all` entry once the backend is
  complete (needs the 832 failures driven to 0 or tracked xfails first); wire the
  arm32-linux native mode when P6 lands; full unit-test sweep in the native modes.

## Adversarial review findings (post-P0/P1, 2026-07-01)

A minimal adversarial review of P0 (landed `98d5bef6`) and P1 (worktree
`3f1b4d2b`) produced:

**P0 — verified sound; review items resolved in follow-up `1e7fdf39`:**
- LP64 byte-identity for aarch64/x64 rigorously verified (advanceNgrn + cc.ArgWords
  reduce exactly to the old behavior at WordBytes=8 / NumFpArgRegs>0); single-aggregate
  sret thresholds (InternalSretBytes/CExternSretBytes=4) confirmed correct vs
  `types.NeedsSret`. No live defect.
- RESOLVED (`1e7fdf39`): `IndirectLargeAggregates` false→**true** — codegen lowers a
  >16-byte aggregate param as a plain `ptr` (`writeParamTypeLLVM` / `IsByvalParam`'s
  flat `SizeOf>16`) on *every* target, so it arrives as a pointer-in-register; the
  native side must match. The prior `false` followed textbook AAPCS, irrelevant here.
- RESOLVED (confirmed correct, no change): `NumGpRetRegs=4` — empirically pinned via
  `clang -target arm-none-eabi -mfloat-abi=soft`: a first-class-aggregate (multi-return)
  return fills up to 4 core regs (r0-r3), sret at 5+ words (`{i32 x4}` in-reg / `{i32 x5}`
  sret), mirroring AAPCS64's first-class rule — NOT the C >4-byte sret rule. Added a
  5-word boundary test.
- RESOLVED (`1e7fdf39`): `EffectiveArgWords` now uses `cc.ArgWords` (target-parameterised;
  byte-identical for LP64) so P4 arm32 shims count an int64 as 2 words / managed-slice
  as 4 on ILP32.
- RESOLVED (`1e7fdf39`): AAPCS32 test gaps filled — >16-byte indirect-pointer, ≤16-byte
  split, 8-byte-aligned *aggregate* even-pair pad, split-aggregate NCRN saturation with a
  trailing arg, `argNeeds8Align` direct, 5-word multi-return sret boundary.
- **NEW, MAJOR (latent, P3):** codegen coerces a ≤16-byte aggregate param to `[N x i64]`
  (`aggCoerceLLTy` — **hardcoded i64**, not target-aware), which clang lowers as 8-aligned
  i64 register PAIRS on arm32. The native AAPCS32 word-packing does NOT reproduce that
  pair-alignment for a 4-aligned struct starting on an odd register → the coerced-in-reg
  aggregate-arg path must be reconciled (target-aware `[N x i32]` coercion, or i64-pair
  modeling native-side) before P3/P4 passes such args. Not fixed in P0 (needs end-to-end
  validation once the backend exists); recorded in the AAPCS32 doc comment.

**P1 — correct and complete for what it claims:**
- Extend encodings + MOVW/MOVT-label fixup recording/offset + ResolveFixups deferral +
  elfRelocType gating all verified bit-exact / correct.
- RELA-for-ARM (the commit's hedge) empirically refuted as a blocker: both
  `arm-none-eabi-ld` and `ld.lld` accept + correctly apply RELA
  R_ARM_MOVW_ABS_NC/MOVT_ABS/ABS32. Not a P2 blocker.
- RESOLVED: the end-to-end gap (no test drove MOVW/MOVT-ABS through the ELF writer) is
  filled by `TestWriteArm32ElfMovwMovtReloc` (folded into the P1 commit) — it drives
  `MovwLabel;MovtLabel;Finalize;WriteARM32` and reads back `.rela.text` to pin the emitted
  reloc types (43/44), offsets, symbol, and addend.
- MINOR (pre-existing): ELF `e_flags=0` (not `EF_ARM_EABI_VER5`) — tolerated by ld/lld in
  bare-metal tests but may surface when linking EABI5 libgcc/libc in P2/P6; revisit at P2.

## Open questions / risks

- **AAPCS32 aggregate/sret/return-reg numbers** must match how LLVM lowers
  arm32 at the native↔LLVM-deps boundary (mixed native-main + LLVM-deps
  link). Pin them by inspecting `clang -target arm-none-eabi` output, not by
  guessing.
- **Register pressure**: R0–R3 args + R4–R11 scratch is far tighter than
  aarch64's X0–X17. 64-bit values consume pairs. The aarch64 "panic on 10+
  live scratches" escape hatch will be hit sooner — a real spill-on-exhaustion
  path may be needed rather than a panic.
- **`.ARM.exidx`/`.ARM.extab`**: libgcc walks them even without unwinding;
  baremetal.ld defines `__exidx_start/__exidx_end`. The native ELF emitter
  must not break their resolution.
- **Per-symbol sections for `--gc-sections`**: the LLVM path gets
  `-ffunction-sections`/`-fdata-sections` for free; the native ELF emitter
  should emit per-symbol sections so the unchanged `-Wl,--gc-sections` link
  still strips unused weak_odr vtables/descriptors (check what aarch64/x64
  native already do here).
- **Float-ABI threading** (see decision above) — settle before P6.
