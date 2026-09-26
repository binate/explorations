# Binate TODO

Tracks open work items, grouped by the subsystem / root cause they touch.
Completed items live in [claude-todo-done.md](claude-todo-done.md).

## CRITICAL

## MAJOR

### native arm32 hard-float: an FP-homed OVERFLOWED float64 param is loaded with VLDR.32 — silent wrong result at -O1/-O2 — 🔴 OPEN (found 2026-09-25)

**Symptom:** conformance `1280_float_param_overflow_loop` prints `46` instead of `131` under
`BINATE_FLAGS=-O2` (and `-O1`) in `builder-comp_native_arm32_linux` (hard-float); green at -O0.
A float64 param past the D0..D7 CPRC bank (it arrives on the incoming stack) that still gets a
D8..D15 home is loaded with a single-precision `vldr s30, [sp, #344]` into the home's low S-view, so
only the low word of the double lands; the high half is whatever the D register held.
**Root cause:** `emitSpillParamFloatHard` (arm32_call_hard.bn) takes `isDouble` from
`rm.CC.CallArgFpReg(...)`, which returns `(-1, false)` for ANY overflowed arg
(common_callconv_vfp.bn: `if sSlot < 0 { return -1, false }`), so the overflow-homed branch always
takes the `vfpLoadBaseHard(a, false, lowSingleOf(home), ...)` arm.  At -O0 the first 8 params fill
the 8-register home pool, so overflowed params stay slot-resident and the branch doesn't run —
which is why the earlier "nearly unreachable, correct defensive code" note (hard-float unit coverage,
below) was wrong on both counts.
**Why CI doesn't see it:** the -O2 lane (conformance-o2.yml) runs native arm32 only as soft-float
baremetal (no FP homes); native arm32-linux hard-float runs only at -O0.  No xfail is possible (the
-O0 run of the same mode passes; xfail markers aren't opt-level-specific) — 1280 at -O2 is the
reproducer.  **Found by:** the completeness critic of the float struct-field fold review (the
field-fold commits neither introduce nor change it: base 9afc2e3a7 fails identically).
**Proposed fix:** take the width from the param's own type (`isFloat64Typ(p.Typ)`, or have the
placement helper report isDouble for an overflowed float) instead of CallArgFpReg's overflow
return; add a unit test driving the overflow-homed branch with a float64 (the existing unit tests
cover only in-register params); and consider adding native arm32-linux to the -O2 CI lane (a CI
scope decision for the user).

### IR functions with a loop or a phi are never freed: CFG edges and phi predecessors are managed `@Block` references that form cycles (toolchain memory leak) — 🟡 IN PROGRESS (found 2026-09-26; step 1 claimed 2026-09-26, session claude/exciting-davinci-wahyt2)

**Symptom:** in a `rt.LiveBlocks()` window, building a module (`genModule` in pkg/binate/vm tests)
and dropping it leaks nothing for loop-free code, but ~52 blocks for a one-loop function even with no
optimization pass, and ~139 for a loop-free function once mem2reg has placed a phi (numbers from a
probe in the vm test package). **Root cause:** `ir.Instr.Block1` / `Block2` (terminator targets) and
`ir.PhiEntry.Block` (a phi's predecessor) are `@Block`, so a loop back edge (header → … → header), or
a phi (merge block → phi → predecessor block → its branch → merge block), is a managed-reference
cycle, and no code breaks it when a function or module is dropped (`Block.Func` is already a raw
`*Func` for exactly this reason). **Impact:** every function with a loop or phi leaks its whole IR when
its module is dropped: bnc too (it compiles and drops each dependency package's module in one
process, so peak memory grows with the package set), and bni / the REPL / anything embedding
the toolchain retains it per load, per mid-session import and per prompt entry; the IR passes
(mem2reg, and the others adding instructions to such functions) enlarge it. **Found by:** a leak test
for the VM pass set (plan-vm-pass-set.md step 4 review follow-up): build + optimize + drop a module
vs build + drop it unoptimized, compare `rt.LiveBlocks()` growth after a warm-up build. That test
(for `iropt.VMOptConfig()` and `LevelOptConfig(2)`) is the regression test to land with the fix; it
cannot pass before it (mem2reg places phis). **Proposed fix:** make the CFG-edge and phi-predecessor
references non-owning (raw `*Block`, as `Block.Func` is; `Func.Blocks` / `Func.FaultPads` own every
block), or break the cycles in a function teardown — the raw-pointer route is the structural one but
touches every `Block1` / `Block2` / `PhiEntry.Block` user across ir, irgen, iropt and the backends.
Plan (reviewed): [plan-ir-cfg-cycle-leak.md](plan-ir-cfg-cycle-leak.md).

### aa64 text assembler silently mis-assembles many load/store forms (latent — no live .s triggers them today) — 🟡 IN PROGRESS (found + claimed 2026-09-25, work-2 session; user: "Go ahead and fix the assembler")

Found by a clang-oracle survey of every load/store form `pkg/binate/asm/parse` accepts (each line assembled
by bnas and by `clang -c -target arm64-apple-macos11`, `otool -tvV` compared).  clang never
synthesizes: it picks the LDUR/STUR alias for a negative/misaligned-but-imm9 offset and ERRORS on
anything unencodable.  Ours silently does something else.  Worst first:
- **Wrong instruction:** `ldr xN, label` encodes LDRSW literal (sfBit sets bit 31, not opc bit 30);
  `str xN|wN, label` assembles to a LOAD; `ldp w0, x1, [..]` / `ldp x0, w1, [..]` silently unify widths.
- **Operand tokens dropped** (root: `parseMemOperand` + no end-of-line check): `[x1, x2, lsl #3]`,
  `uxtw`/`sxtw`/`sxtx` extends → plain `[x1, x2]` (wrong address); post-index without `#`
  (`ldp x0,x1,[x2], 16`, `ldr q0,[x1], 16`) or with a register drops the writeback; label `+addend`
  dropped; missing `]` accepted; `[x1, w2]` encodes `[x1, x2]`.
- **Silently emits nothing:** `ldp/stp` with a reg-offset/label/imm operand; `ldr/str x0, #8` / `x0, x1`.
- **Silent wrap/truncate:** pre/post-index imm9 masked mod 512; `ldp/stp` (X/W/Q) imm7 truncates a
  misaligned offset and wraps an out-of-range one (`stp x29,x30,[sp,#-528]!` stores ABOVE sp).
- **Silent multi-instruction synthesis clobbering x17:** `ldr/str` (and b/h/sb/sh/sw) `[Xn, #imm]`
  not fitting the scaled uimm12 → `add/sub x17` + access, even where LDUR fits (the encoder's
  documented backend fallback, leaking into the text assembler); same for `ldr/str q` beyond imm9.
- **Operand-class validation:** `ldrsw w0`, `ldrb x0`, base `w1`/`xzr`, index `sp`, `ldr sp` all
  silently re-interpreted; Rt==Rn writeback forms emitted as CONSTRAINED-UNPREDICTABLE words.
**Scope widened (2026-09-25, second survey — data-processing + branch/system, ~420 forms):** the
same systemic causes make ~260 data-processing and ~120 branch/system forms silently differ from
clang.  Worst: SP vs XZR conflated (`sub sp, sp, x1` → `neg xzr, x1`; `mov x0, xzr` → `mov x0,
sp`); extended-register operands and `#imm, lsl #12` dropped; `mov Rd, #imm` always MOVZ of the low
16 bits (`mov x0, #-1` → 0xffff); unencodable logical immediates and `ror #imm` emit NOTHING;
`cmp x0, #4097` splits into `cmp #1, lsl #12; cmp sp, #1` (garbage flags); no W/X width checks;
label addends and `@PAGEOFF`/`@GOTPAGE` specifiers dropped (`add x0, x0, sym@PAGEOFF` emits
nothing); `ret x31`/`ret xzr` → `ret`; shift/movz/tbz/svc fields masked instead of range-checked.
Encoder-level (backend-reachable) ones — Mov's SP-for-31, MOVZ/MOVK/MOVN masking, emitDPOp's
rd=XZR split, emitLogOp's silent no-emit, shift masking — are latent in native codegen today
(checked: the backend passes registers to logical ops, SP to Mov only when meant, and folds
compare immediates only up to 4095).  Rejects-valid gaps (unsupported mnemonics: bic/orn/cset/
ubfx/clz/…, hints/barriers, msr, brk/hlt, `.L` labels, numeric labels) are completeness work.
**Plan:** per family, exact architectural encoders (one instruction or a hard error) + a strict
parser with register classes and an end-of-line check; load/store done first (all 360 survey +
extra cases now MATCH or BOTH-ERR vs clang), then data-processing, then branch/system, then the
backend-facing encoder footguns.
Raw survey outputs: session scratchpad (not durable) — the tables above are the record.
**Fix:** the text parser must be faithful — one mnemonic → one instruction, clang's alias selection
(LDUR/STUR), and a hard parse error for anything unencodable or malformed (including trailing
tokens); encoders called from the text path must never synthesize.  Scope the backend-facing
encoder fallbacks (x17 synthesis) to explicitly-named backend helpers.  Tests: a golden
bnas-vs-clang table per form + rejection tests.  Supersedes the (A)-entry follow-ups (a)/(b).

### Assembler silently drops / mis-encodes unencodable immediates on the ALU/logical paths the native immediate folds use (aa64, arm32, x64) — 🟡 IN PROGRESS (found 2026-09-25; claimed 2026-09-25, work-4/session — T6(b) step b1)

All confirmed by probe + clang oracle; none is triggered by any live caller today (every native caller passes
registers or pre-checked immediates; every in-tree `.s` immediate is encodable), but each is reachable from
the text assembler (`bnas`, and bnc's in-process `.s` assembly) and each is on the path of the native
immediate folds (T6), so a fold/encoder mismatch would be silent wrong code:
- **aa64:** `emitLogOp` (`And`/`Orr`/`Eor`) and `Ands` (hence `Tst`) emit **nothing** and set no error when the
  immediate is not a bitmask immediate (`asm/aarch64/aarch64_arith.bn` ~150-199, `// silent failure for
  now. TODO: report error`); no default case for unsupported operand kinds.  `bnas` exits 0 on
  `and x0,x1,#5` and leaves the instruction out.  `encodeBitmaskImm` itself is correct (exhaustively
  bit-identical to clang: all 5334 64-bit / 1302 32-bit patterns; all 1816 rejections match).
- **arm32:** `encodeOperand2` (`asm/arm32/arm32.bn` ~198-232) encodes any non-encodable modified immediate
  as `#0` — all 16 data-processing encoders (Add/Sub/Rsb/Adc/Sbc/Rsc/And/Orr/Eor/Bic/Mov/Mvn/Cmp/Cmn/Tst/Teq)
  — and turns a memory/label operand into register r0.  `bnas`: `and r0,r1,#0x102` → `and r0,r1,#0`,
  `add r0,r1,#-4` → `#0`, exit 0.  `EncodeRotImm` itself is correct (13,795 values vs clang).
- **x64:** `emitALU` (Add/Sub/And/Or/Xor/Cmp), `Test`, `Mov r/m,imm`, `Push imm`, `Imul3` never range-check:
  a 64-bit op given a value in [2^31,2^32) is sign-extended to a different value (`and rcx,0x80000000` →
  `and rcx,-0x80000000`; Rosetta confirms the wrong result), values ≥ 2^32 truncate (`and rcx,0x100000000` →
  `and rcx,0`); on a 32-bit host `cast(int, Imm)` truncates first.  SZ16 immediates that don't fit imm8 are
  emitted as imm32 where the 0x66 form takes imm16 — the two extra bytes decode as the next instruction.
  The package has zero `SetError` calls.
**Fix:** fail loud (`a.SetError("<arch>: ...")`, the package convention elsewhere) in each path; for aa64
fold `Ands` into `emitLogOp` so the check exists once and export `LogicalImmFits(sf, imm)`; for arm32 add a
default `SetError` for non-Operand2 kinds; for x64 check the value against the op width (imm8/imm16/imm32
with sign-extension) and fix the SZ16 imm16 form.  Tests: HasError + no bytes for each encoder at both
widths, bnas rejection tests.  **Proposed to be fixed as part of T6(b)** (the logical-immediate fold routes
immediates through exactly these encoders).

### aa64 assembler: non-load/store encoders silently truncate or drop out-of-range fields — 🟡 CLAIMED, queued (found 2026-09-25; claimed 2026-09-25, work-4/session — assembler sweep after T6(b), before (c))

Encoder-level complement to the load/store entry above (all confirmed by probe + clang):
`Mov` with OP_IMM always emits MOVZ masked to 16 bits (`Mov(Imm(-1))` → `mov x0,#0xffff`); `Movz`/`Movk`/`Movn`
mask imm16 and silently drop a shift that isn't a multiple of 16 (`Movz(1, lsl 8)` → `mov x0,#1`); immediate
`Lsl`/`Lsr`/`Asr` accept out-of-range amounts (`lsl #64` → identity, `lsr w,#40` → a reserved encoding);
NEON lane index (`laneImm5`, `aarch64_neon_lane.bn` ~19) overflows into opcode bits (`Vins_gp(B,16,…)` →
`and.16b v0,v1,v1`); `Tbz`/`Tbnz` mask the bit number, `Svc` masks imm16, `Mrs` masks its fields, `Mvn` with
OP_IMM emits nothing; several encoders have no default case for unsupported operand kinds; W-form bitmask
immediates discard the high 32 bits (clang only accepts all-zero/all-one high halves:
`and w0,w1,#0x100000001` assembles as `#1`); stale docs on `LdrStrImmFitsUnsigned` claim the encoder
masks.  **Fix:** fail loud (`a.SetError`) on every out-of-range field; tests per encoder + bnas rejections.

### x64 assembler / text parser: REX.X/REX.B dropped on some memory forms, `[base+idx*scale+disp]` misparsed, unhandled operand combos emit garbage — 🟡 CLAIMED, queued (found 2026-09-25; claimed 2026-09-25, work-4/session — assembler sweep after T6(b), before (c))

`emitALU`'s immediate branch passes x=0 to `emitRexF` (and `Test` with a memory operand drops REX.B/REX.X),
so an index/base register ≥ r8 silently becomes the low register; the text parser's `ParseExpr` after `*`
consumes a trailing `+ disp` into the scale (`[rax+r9*2+2]` → `[rax+4*r9]`); `emitModRM` emits disp32
without a range check (offsets > 2 GiB truncate — same unbounded callers as the frame-size immediates);
`emitALU`/`Test`/`Mov` emit nothing, a stray 0x66, or garbage for unhandled combinations (mem,mem; imm
destination; label source) and report success.  (Minor: for SZ32 the imm8/imm32 choice uses the int64 view,
so `and ecx,0xFFFFFFF0` gets the 6-byte form.)  **Fix:** carry REX.X/REX.B in every memory form; parse
`scale` as a single term; range-check disp32; `SetError` on unhandled combos; golden bnas-vs-clang tests.

### Text assemblers truncate 64-bit immediates on a 32-bit host; the assemble path hides encoder errors — 🟡 CLAIMED, queued (found 2026-09-25; claimed 2026-09-25, work-4/session — assembler sweep after T6(b), before (c))

`asm/parse/aarch64.bn` ~125 and `asm/parse/x64.bn` ~258 / `x64_instr.bn` ~39, ~137 build immediates with
`Imm(cast(int, result.Val))`, truncating the int64 value on a 32-bit host (arm32 hosts are first-class).
`asm/assemble/assemble.bn` ~53 reports only a generic "assembly failed" (no message, no line) for an encoder
`SetError`, and `parse_file.bn` ~40 keeps parsing after an encoder error (checks only the parser's own flag),
so the fail-loud fixes above would surface without context.  **Fix:** keep immediates 64-bit to the encoder
(`ImmU64`-style), propagate the assembler's error message with the source line, stop at the first error.

### native: `getOperand` on a folded (skip-emitted) value silently reloads a never-written spill slot; several dispatcher cases silently drop an instruction on an unresolved operand — 🟡 PARTLY CLAIMED (found 2026-09-25; the `getOperand` fail-loud part claimed 2026-09-25, work-4/session — T6(b) step b2; the PlanFrame-slot part rides the LinearScan step; the dispatcher silent-return part stays 🔴 OPEN)

`PlanFrame` reserves a slot for every value, folded constants included (`native/common/common.bn` ~162,
~222), so `getOperand` on a FoldedImmConst / FoldedAddImmConst id reloads that slot — which was never
written, because the constant's emission was skipped — instead of returning -1 as the consumer comments
claim (`aarch64_ops.bn` isFoldedAddConst doc, `x64_fold.bn`, `arm32_fold.bn`).  Any mismatch between a fold
analysis and an emitter is therefore a silent garbage read, not a compile error.  (No live mismatch known:
arm32 int64 constants are always materialized by `emitConst64` ahead of the generic flag check — safe by
dispatch order, though the compare fold's flag is inaccurate for them since `ImmFoldableConsts` has no width
guard.)  Separately, per-op dispatcher cases (`OP_COPY`, `OP_MANAGED_TO_RAW`, `OP_BIT_CAST`, `OP_CAST`, ~15
more `if … < 0 { return }` sites across the three backends) silently emit nothing on an unresolved operand
instead of failing loud like the dispatch tail — a dropped `OP_COPY` leaves a phi stale.  **Fix:**
`getOperand` fails loud for fold-flagged ids and `PlanFrame` stops reserving their slots; replace the silent
returns with `a.SetError("<op>: unresolved operand")`; fix the comments.

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

### Standing: decide each new IR pass's VM membership in [vm-pass-set.md](vm-pass-set.md)

The bytecode VM runs a fixed pass set (`iropt.VMOptConfig`, pass_config.bn); a new pass is off for
the VM until measured. When adding or materially changing a pass, run `perf/vm-pass-costs.py`, add
its row to the living doc [vm-pass-set.md](vm-pass-set.md), and record the decision there. Pending:
rerun the full measurement now that the load-fwd MAJOR is fixed (the `loo:mem2reg` rows were BAD),
and add deterministic per-pass load costs (callgrind) for the cheap passes.

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
- **float struct-field fold (all three native backends)** — 🟡 IN PROGRESS (claimed 2026-09-25,
  work-5/session).  The field analogue of the aarch64 float element fold: `p.x` for a float field
  still materializes the field address separately because `fieldAccessFusable` excludes floats.
  Parameterize `FusableFieldGeps` with `allowFloat` and make each backend's fused field load/store
  FP-home-aware (load/store the value straight from/into its FP home at `[base, #off]`).  aarch64's
  fused field emitters already route through the `isFpReg`-dispatching emitScalarLoad/Store; x64 and
  arm32 need the direct FP path.  Measure on n-body / spectral-norm (aa64 instructions retired).
  Status (not yet landed): all three backends done on the work branch, pending review + arm32
  conformance.  aarch64: n-body N=1e6 instructions retired 16.00G → 15.72G median (−1.78%, A/A noise
  +0.02%; `perf/ab-binaries.sh`, 7 rounds), user CPU −1.52%, output identical; native aa64 conformance
  3051/0.  x64: n-body `advance()` 422 → 367 static instructions (29 fewer LEAs; float stores go
  straight from the XMM home instead of MOVQ+MOV); native x64-darwin conformance 3051/0.  arm32:
  n-body `advance()` 439 → 325 static instructions — each float64 field access was ADD + 2×LDR + VMOV
  (the 64-bit path round-trips through GP), now one VLDR.64/VSTR.64; output identical under qemu-arm.
  (x64/arm32 runtime can't be measured on this host — no native x64/arm32 hardware.)
  Observed while verifying arm32 (separate items, not this one): n-body's `bodies[i].x` recomputes
  the element address every access (`mov r5,#56; mul; add` — arm32 doesn't fold element GEPs), and
  the loop indices are spilled (arm32 homes are caller-saved R0..R3, so values live across the
  `Sqrt` call can't be homed).  Also: arm32's PLAIN (non-folded) float loads/stores still round-trip
  through GP registers (LDR + VMOV), unlike x64/aarch64.
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
    confirms `addq $0x1` fires.  (b) AND/OR/EOR logical-immediate folding (needs an is-encodable-bitmask
    check) — 🟡 IN PROGRESS (claimed 2026-09-25, work-4/session); (c) phi-copy coalescing (the bigger
    007 lever — regalloc-core, regression risk) — 🔵 OPEN, queued after (b) by the same session.**
  - **Plan (decided 2026-09-25):** (b1) fail-loud fixes for the encoders on the fold's path; (b2) replace the
    per-kind compare/add folds with ONE `fold.ImmOperandConsts(f, fits)` analysis + one `FoldedImm` flag
    (per-backend predicate + shared encoding helpers; uniform width guards) and make `getOperand` fail loud on
    fold-flagged ids; (b3) the AND/OR/XOR immediate fold on all 3 backends (+ aa64 `tst a,#k`, XOR-all-ones →
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
    - **Cross-kind constants:** folding a constant whose uses span kinds (compare + add) would catch 289
      more in `bnc` (all the value 1 in managed-slice destructor loops).
    - **iropt does no integer constant folding** of all-constant binary ops / compares, nor identities
      (x&0, x&-1, x|-1, 0-x), nor NEG/BITNOT of a constant — so `x & -16` / `x & ~15` reach the AND as a
      unop-of-constant and miss any immediate fold; 79-102 both-constant logical ops survive to codegen.

Order: T1 → T2 → T3 → T4 → T5 → T6.

### record-churn residual is SROA-pinned aggregate copies, NOT the SIMD ceiling — findings 2026-09-24 — 🔵 OPEN (SROA copy-out split + dead-phi elimination ✅ LANDED `9da1662f`/`9c934585`, see done log)

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

- **Backends resolve an OP_GET_FIELD_PTR base's struct from the UNPEELED `Typ.Elem` — 🔵 OPEN
  (latent; found 2026-09-24 by the adversarial review of the SROA copy-out split).** LLVM
  `codegen/emit_helpers.bn` `emitGetFieldPtr`, native `native/common/common.bn` `StructTypeOf`,
  and VM `vm/lower_memory.bn` `lowerGetFieldPtr` take the base's struct from `Args[0].TypeArg`,
  else `Args[0].Typ.Elem` WITHOUT peeling `Typ` — a named / alias pointer type (`type PS *S`) has no
  `Elem`, so the struct resolves to nil (LLVM: invalid GEP; native: result register never defined;
  VM: every field at offset 0). irgen avoids it by setting `ptrVal.TypeArg = structTyp` before
  each field access, so only an IR producer that forgets that trips it; the SROA copy-out split
  now declines named-pointer destinations instead. Fix: peel `Typ` before taking `.Elem` in all
  three places (plus a unit test per backend).
- **Native code layout: functions (and loop headers) are not aligned — 🔵 OPEN (found 2026-09-24).**
  Native x64 function symbols land at unaligned addresses (e.g. `math.Sqrt` at `…83e`, `…903`);
  LLVM aligns functions to 16. Measured layout sensitivity: shifting a copy of `math.Sqrt`'s code
  by padding (same compiler, identical instructions) moves a Sqrt-bound loop's user time by up to
  16% (0.875s–1.019s, x64, 8 paddings). This showed up as a spurious +11% n-body "regression"
  between two builds whose Sqrt loop differed by one removed copy (callgrind: new build executed
  1.9% FEWER instructions). Besides being a real performance gap, it makes single-build A/B
  comparisons of branchy hot loops unreliable. Fix: align function entries (and probably loop
  headers of hot/innermost loops) in the native backends, as LLVM does.
- **n-body is ~90% software `math.Sqrt` on BOTH backends — 🔵 OPEN (found 2026-09-24).** callgrind:
  native 88%, LLVM 90% of instructions in `math.Sqrt`'s bit-by-bit loop (neither emits `sqrtsd` /
  `fsqrt`); the source notes "a hardware sqrt intrinsic may replace this as a fast path later". So
  n-body's native/LLVM ratio is essentially the codegen gap on that one integer loop, and both
  backends are far from C (which uses `sqrtsd`). A hardware sqrt (per-arch asm or an intrinsic the
  backends lower) is the large lever for n-body; the loop's native codegen (spilled loop-carried
  values, shift counts reloaded into `cl` from stack slots) is the gap lever.
- **Constant shift amount not folded (both backends).** `c.f2 << 1` reaches the backends as
  `shl %x, %v403` with `%v403 = add i32 1, 0` hoisted out of the loop; native then reloads it from a
  stack slot every iteration (x64 `mov rcx,[rsp+..]; shl edx,cl`; aa64 `ldr x9,[sp,..]; lsl w,w,x9`).
  Needs: the const materialization not to hide the constant from shift-by-immediate selection
  (fold `add C,0`/propagate before LICM, or rematerialize constants instead of spilling). 🔵 OPEN
- **x64 bounds check is two signed compares** (`cmp i,0; jl` + `cmp i,len; jl`); aa64 already emits
  one unsigned compare (`b.lo`). Port the unsigned single-compare form to x64. 🔵 OPEN
- **x64 element-address scaling uses `imul r,r,0x20`**; aa64 uses `lsl #5`. Use `shl`/scaled
  addressing for power-of-two element sizes on x64. 🔵 OPEN
- **Managed-slice header reloaded through its stack slot every iteration** (both backends, ~10
  instrs per slice per iteration for `arr`/`out`): the `@[]Record` locals stay in memory and the loop
  re-reads data/len; LLVM hoists them (no aliasing store). Investigate why these managed-slice
  allocas are not SROA'd / why the loads are not loop-invariant-hoisted. 🔵 OPEN
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

## Documentation hygiene

### ABI spec §5.2 — package-path validation now ENFORCED; update the "unvalidated" text — 🟢 minor (2026-09-07)

`docs/abi/05-symbol-naming.md` §5.2 records package paths as "currently
**unvalidated** (an out-of-set byte, or a `.`, would leak into symbols ...) — a
recorded enforcement gap". That gap is now closed: `mangle.IsValidPackagePath`
plus loader enforcement (`loadPackage`) reject any package path that is not a
`/`-separated sequence of NON-EMPTY `[A-Za-z0-9_]` segments — a hostile path
(e.g. an aliased `import "ev.il"`) now fails with a clean "invalid package path"
error instead of an undefined-symbol clang failure (was ABI review #12). Update
§5.2 to state the rule is enforced (drop the "enforcement gap" framing) and note
the non-empty-segment requirement, which is slightly stricter than the raw
length-prefix grammar (whose `Ident` could encode a 0-length segment). Re-check
§5.5 wording for the now-closed discriminator hazard.

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


### Stale comments contradicting live ABI behavior (found 2026-09-04, ABI-spec recon) — 🟢 sweep

All contradict code that has since changed; fix the comments, don't trust
them (the ABI spec was authored from the code, not these):

- "dormant until the gate flips" family — the SSE and HFA gates are LIVE
  (SysVSseInRegs true for x64 since ce759c416; HfaInSimd true for aa64 since
  48e3787b1): common_callconv.bn:90,164; common_callconv_ctors.bn:48-50;
  x64_sse.bn:19-20; x64_return.bn:52,293; x64_call.bn:187;
  emit_sysv_coerce.bn:39-40; aarch64_hfa.bn:18-20; abi_return.bn HFA notes.
- common_callconv_vfp.bn:47-50 "no target stamps yet" — arm32-linux stamps
  FLOAT_ABI_HARD (cmd/bnc target.bn).
- aarch64_return.bn:9-16 / aarch64_call.bn:195-199 claim sret at ">64
  bytes" — operative threshold is InternalSretBytes=16.
- arm32_call.bn:22-25 header claims float64-in-multi-return is "deferred
  (P5.3)" — implemented (also an increment label, banned in code comments).
- irdata/data_strings.bn:30-33 + native/aarch64/aarch64.bn:28-29 say the .ms
  string header lands in "data" — actual routing is rodata_relro.
- asm/elf/elf_util.bn:211-216 claims aarch64 low-12/GOT fixups "have no ELF
  mapping yet" — mapped directly below (:237-245).
- codegen/emit_funcvals_sig.bn:160-176 (writeShimResultLLVM aggRetCoerced
  branch) appears unreachable — all callers are behind isAggregateReturn,
  which is true for every AggRetCoerced result; comment contradicts
  abi_return.bn. Verify + delete or fix.

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
