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
  the single-aggregate-sret arg-register-shift on AAPCS32 (slice/struct-returning
  functions); then structs/arrays, interfaces, multi-return, closures (P4),
  float (P5).

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
emitInstr64 → 32-bit single-word fall-through). Follow-ups (tracked, non-blocking):
int64-ABI unit tests (conformance-covered today); a bare-return→`a.SetError`
consistency pass across the arm32 emitters (matches the pre-existing 32-bit
convention; the flagged returns are unreachable given IR invariants).

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
- Port `arm32_funcvalue.bn`, `arm32_funcvalue_shim.bn`,
  `arm32_funcvalue_spill.bn`, `arm32_closure_shim*.bn` — the all-int
  dispatch-ABI re-marshaling into AAPCS32 (scalar/void/pack/sret return
  shapes; over-budget stack spill with the tighter R0–R3 budget).
- **Acceptance**: func-value / closure / interface conformance + unit tests
  pass in native baremetal.

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
- Propose adding `builder-comp_native_arm32_baremetal` (+ linux) to
  `scripts/modesets/all` — **CI wiring is a user decision** (per CLAUDE.md
  "Stay Within the Asked Scope"); do not add unasked.
- Full conformance + unit-test sweep in the native arm32 modes; drive every
  residual to a fix or a tracked xfail+todo.

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
