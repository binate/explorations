# Plan: native code placement (x64, aarch64, arm32)
Status: tooling designed (2026-10-12); decisions taken 2026-10-15 (below); tooling commit 1 (placement seed) landed as binate `417f660bd` (2026-10-16); tooling commit 2 (static report, `layout.py`) landed as binate `f05342e5d` (2026-10-18); tooling commit 3 (timing driver, `ab.py` / `stats.py`) landed as binate `4f9e3c75b` (2026-10-18), its short-run refusal as `0a6a3aeaf`; tooling commit 4 (probe) landed as binate `053d743c1` (2026-10-19); phase-0 calibration run on the x86 VM 2026-10-19/20 (results below); user decisions 2026-10-20: the per-workload guard gets a Holm correction over the workloads, and on this VM x64 decisions use the benchmark geomean and the self-compile while per-benchmark claims are timed on the aa64 machines. The Holm change landed as binate `3f95bef54` (2026-10-28). Next: block layout (design drafted below, awaiting user review). Tracked in `claude-todo.md` ("native code placement").
## Why

Identical machine code runs at very different speeds depending on where it lands, on the x86 test host
(Intel Cascade Lake, family 6 model 85 stepping 7, in a Firecracker VM with no hardware counters):

- `blockLiveBefore` / `livenessFixpoint` in a native-built bnc: byte-identical (blockLiveBefore) or
  equal-instruction-count code, 12.5% / 15% slower in one build than another. Both moved by exactly
  9696 bytes between the builds: 0 mod 32, 32 mod 64. So their 32-byte windows hold identical code in
  both builds (same window counts, same JCC-erratum exposure); what differs is the 64-byte-line phase.
- fannkuch-redux: 12.7% faster in one build with identical instruction counts; its 0x5f-byte inner loop
  spans 3 vs 4 32-byte windows (16-byte-aligned header at 0 vs 16 mod 32). A first test that aligned
  every loop header to 32/64 did not reproduce the speedup (5 rounds, partly under background load:
  inconclusive).
- A/B timing of `-fx64-rax-home` (binate `edeabc7d1`) could not be read: per-pair self-compile timing
  varied 1-3 s on 45 s. The environment-size "stack placement" runs were taken with ASLR on, so the
  stack start already moved on every exec: those runs measured run-to-run noise, not a controlled
  stack factor.

Today x64 aligns function entries and loop headers to 16 bytes (`X64_FUNC_ALIGN`, `X64_LOOP_ALIGN`,
LLVM's generic x86-64 values); aarch64 and arm32 align nothing beyond the 4-byte instruction size.

## Order (user decision 2026-10-12)

1. Block layout first: fall-through elision, loop rotation, contiguous loops (critical-edge / inlined
   blocks of a hot loop are appended at the end of the function today). Fall-through elision is claimed
   by work-4's T6 entry; coordination is an open question below.
2. Then placement policies, for all three native backends (user: yes to aarch64/arm32).

Measurement tooling lands first (user: yes), because both steps are judged with it.

## Background findings (research, 2026-10-10)

- LLVM: 16-byte function and loop alignment on every x86 CPU, no per-tune variation, up to 15 bytes of
  loop padding with no cap; which blocks get aligned is decided by block frequency
  (MachineBlockPlacement); optsize functions get none. GCC: generic tuning functions 16, loops 16,
  jumps 16:11:8 (align to 16 if that pads at most 10 bytes, else 8); skylake/cascadelake tuning loops
  and jumps 16:11:8. Neither mitigates the JCC erratum by default (opt-in
  `-mbranches-within-32B-boundaries`); Go does by default on amd64. LLVM issue #89937 reports ~12% from
  16- vs 32-byte loop alignment on Skylake.
- JCC erratum (Skylake through Cascade Lake/Comet Lake): after Intel's microcode fix, a 32-byte chunk
  containing a jump that crosses or ends on a 32-byte boundary is not served from the decoded-uop cache.
  Intel quotes 0-4% typical, outliers higher. No benefit from mitigation on Ice Lake+ or AMD.
- This backend: no block reordering and no fall-through elision (every conditional branch is
  `jcc rel32` + `jmp rel32`); loops are not rotated (2 taken branches per iteration in a simple loop);
  loops are not contiguous; label branches are always rel32. A block's bytes do not depend on its
  position, so measure-then-place is feasible.

## Policy options (to be judged with the tooling)

- A: coarser fixed alignment: function entries 32 or 64, loop headers 32 (cold entries stay 16).
  Est. text cost: entries +0.5% (32) / +1.4% (64) of bnc; loop headers +0.3% / +0.9% on the benchmarks
  (measured).
- B: adaptive innermost-loop placement after emission: pad each innermost loop header (0-63 bytes,
  never executed) to minimize 32-byte windows (B-win) or JCC-erratum hits inside the loop (B-jcc).
  Needs a loop-membership API and a post-emission insertion pass.
- C: JCC-erratum branch padding (executed NOPs before affected jumps); est. +2-5% text today, less after
  block layout.

## Tooling plan
- 0. Process.
  - Write /home/user/workspace/explorations/plan-native-code-placement.md with this plan.
  - Update the existing claimed entry '- **x64 code placement …** 🟡 IN PROGRESS (claimed 2026-10-10)' in /home/user/workspace/explorations/claude-todo.md (~line 1327):
    - rename it to native code placement, since all three backends get policies (your answer 6);
    - list T2-T5 as sub-steps;
    - link the plan.
  - Add a LOW todo: the assembler has no arm32 UDF encoder or parser, and aarch64 UDF is reachable only through asm/aarch64/isa and the text parser (comprehensive-assembler rule).
  - Commit and push each explorations edit immediately.
  - All binate work goes in the session's assigned worktree, and each commit lands before the next one starts.
- 1. binate commit 1: T2 function-level placement seed for all three native backends. — LANDED binate `417f660bd` (2026-10-16). As landed, the API lives in a new leaf package `pkg/binate/native/place` rather than in common.bni, and PlaceCheck runs on every EmitObject, not only when a seed is set.
  
  1a. /home/user/workspace/binate/pkg/binate/native/common.bni gains about 45 lines (738 → ~785 of the 1000 cap):
  - `const PLACE_SPAN int = 64`
  - `const PLACE_SHIFT int = 0`, `const PLACE_PHASE int = 1`
  - `const PLACE_FILL_IMM int = 0x5EED`
  - `type PlaceOptions struct { Seed int; Mode int; ProbeSym @[]char; ProbePhase int }`. Doc: a measurement aid only; the zero value means off and output is unchanged. Probe fields are documented now, and their flags land in commit 4.
  - `func PlaceActive(p PlaceOptions) bool` returns Seed != 0 || len(ProbeSym) > 0.
  - `func PlaceHash(seed int, key *[]readonly char) uint32`. The doc comment specifies it exactly so the Python twin can match: FNV-1a-32(key), XOR (uint32(seed) * 0x9E3779B9), then murmur3 fmix32.
  - `func PlaceFillBefore(p PlaceOptions, sym *[]readonly char, unit int, off int) int`
  - `func PlaceFillAfter(p PlaceOptions, sym *[]readonly char, before int) int`
  - `func PlaceCheck(p PlaceOptions, unit int) @[]readonly char` returns "" when the options are valid.
  - ArchEmitter gains `PlaceUnit() int` and `EmitPlaceFill(a @asm.Assembler, nbytes int)`.
  - `func EmitObject(e *ArchEmitter, mod @ir.Module, format *[]readonly char, path *[]readonly char, place PlaceOptions) bool`
  
  1b. New common/common_place.bn (~120 lines) holds the semantics. Let K = PLACE_SPAN/unit and idx = PlaceHash(Seed, sym) mod K.
  - Shift mode: pre = unit * idx.
  - Phase mode: phi = unit * idx; pre = (phi - alignUp(off, unit)) mod PLACE_SPAN.
  - Probe symbol, in either mode, including at seed 0: phi = ProbePhase (a target phase, not a shift), pre computed as in phase mode, and post = PLACE_SPAN - pre. Every later byte position is then identical across all probe phases, including phase 0, and so is every later byte except instructions that refer to the probed function (a call to it changes its displacement; seen as exactly one such instruction per comparison in the commit-4 validation).
  - Any other function at seed 0: pre = post = 0.
  
  1c. common_emit_object.bn changes:
  - If PlaceActive(place): run PlaceCheck(place, e.PlaceUnit()); on an error, a.SetError, then ReportEmitError, then return false.
  - In phase mode or when probing, call a.AlignFill(PLACE_SPAN, 0) right after SetSection("text"). It emits no bytes at offset 0 and makes in-object phase equal linked-address phase.
  - In the loop at :50-63, for a non-extern f:
    - sym = mangle.FuncName(pkgName, f.Name), i.e. without the Mach-O '_', so ELF and Mach-O builds agree;
    - if pre > 0, call e.EmitPlaceFill(a, pre) before e.EmitFunc;
    - if post > 0, call e.EmitPlaceFill(a, post) after it.
  - When placement is inactive, no new call runs.
  
  1d. x64 changes:
  - x64.bni: Options gains `Place common.PlaceOptions` (adds the import).
  - x64.bn:24 passes opts.Place.
  - x64_emitter.bn:
    - `PlaceUnit()` returns a new `const X64_PLACE_UNIT int = 16`. It is kept separate from X64_FUNC_ALIGN so that a later function-alignment policy, which changes that constant, does not also change the filler distribution.
    - `EmitPlaceFill` emits nbytes × x64.Int3, and calls SetError if nbytes < 0 or nbytes % 16 != 0.
  - The filler lands before emitFunc's first AlignNops (x64_emit_func.bn:96), so C-export aliases and `sym` keep their 16-byte alignment.
  
  1e. aarch64 and arm32 changes:
  - aarch64.bni and arm32.bni gain `type Options struct { Place common.PlaceOptions }` and `EmitObject(mod @ir.Module, format *[]readonly char, path *[]readonly char, opts Options) bool`.
  - In the emitters, PlaceUnit returns 4. EmitPlaceFill emits nbytes/4 × aarch64.Brk(a, common.PLACE_FILL_IMM) or arm32.Bkpt(a, common.PLACE_FILL_IMM), and calls SetError if nbytes % 4 != 0. The filler is real A64/A32 code, so the $x and $a mapping symbols stay correct.
  - Add Options{} to the 6 aarch64 call sites (aarch64_test.bn ×5, aarch64_dispatch_test.bn ×1) and the 2 arm32 ones (arm32_test.bn).
  - relabelRecorder gains the two methods.
  - aarch64 and arm32 emitFunc are not touched.
- 1 (cont.).
  
  1f. native package changes:
  - /home/user/workspace/binate/pkg/binate/native.bni: Options gains `Place common.PlaceOptions` (adds the import). Doc: a measurement aid that reaches all three backends; the zero value leaves output unchanged.
  - native.bn:
    - aarch64Backend and arm32Backend get `opts aarch64.Options` / `opts arm32.Options` fields, like x64Backend.
    - EmitObject copies opts.Place into all three.
    - Fix the comment 'each switch reaches only the backend it names'.
  
  1g. New /home/user/workspace/binate/cmd/bnc/args_native.bn, which pairs with the existing args_native_test.bn. It holds registerNativeFlags(fs @flags.FlagSet) and applyNativeFlags(…, result *CLIArgs). The -fx64-rax-home pair moves there from args.bn:126-132,204-208.
  - New flags:
    - -fnative-place-seed=S: IntVar, default 0, must be in 0..2^31-1 so a seed means the same thing on ILP32 hosts.
    - -fnative-place-mode=phase|shift: StringVar, default per question 1.
  - Errors (set result.Error):
    - a negative or oversized seed;
    - an unknown mode;
    - a mode given without a seed;
    - any -fnative-place-* without --backend native. Unlike the X64RaxHome precedent, this is not silently ignored: these flags are a measurement contract.
  - The flags stay out of printUsage (so they are hidden) and out of optflags (so --list-opt-passes does not show them).
  - Update the CLIArgs.NativeOpts doc. compile/main/library/test.bn are unchanged.
  
  1h. In the same commit, so the twin stays in lockstep from day one:
  - /home/user/workspace/binate/perf/placement/placehash.py: place_hash; fill_before/after for both modes; the PLACE_FILL_IMM and unit tables; stack_pad(seed) = 16 * (place_hash(seed, 'stack') % 256); salted_seed(seed).
  - perf/placement/test_placehash.py with the SAME pinned vectors as common_place_test.bn.
  
  1i. Validation before asking to land (none of it wired into CI):
  - hygiene, reading the overall line;
  - unit tests of cmd/bnc, pkg/binate/native, native/common, native/x64, native/aarch64 and native/arm32 (common is shared, so all three backends);
  - a gen1 build (BUILDER compatibility);
  - seed-0 identity: a native bnc built with and without -fnative-place-seed=0 must cmp equal, and so must `-c` objects for the aa64_linux and arm32 targets;
  - BINATE_FLAGS='-fnative-place-seed=12345', and again with '-fnative-place-mode=shift', on builder-comp_native_x64-comp_native_x64, builder-comp_native_aa64_linux-comp_native_aa64_linux and builder-comp_native_arm32_baremetal (required for arm32 native), plus builder-comp_native_arm32_linux;
  - a seeded native bnc must self-compile cmd/bnc to the same bytes as an unseeded one;
  - `bnc --test --backend native -fnative-place-seed=7` over the native packages;
  - a minimal adversarial review.
- 2. binate commit 2, T4 static report, in /home/user/workspace/binate/perf/placement/ (Python stdlib only): — LANDED binate `f05342e5d` (2026-10-18). As landed: split over more modules than the three named (layout_disasm, layout_names, layout_span, layout_loops, layout_seed, layout_hot, layout_report) to keep each under 500 lines; tests use synthetic programs rendered in all three objdump syntaxes plus real excerpts in testdata/; Mach-O --expect-seed checks phases only, and in shift mode fails only when inexact rows outnumber exact ones (rule pending user confirmation).
  - layout.py: the CLI and core.
  - layout_x86.py and layout_arm.py: arch rules.
  - Each under 500 lines.
  
  CLI: `layout.py BIN [--arch auto] [--func RE]... [--hot PERF_REPORT] [--loops] [--compare OTHER] [--expect-seed S --mode phase|shift] [--window N --line N] [--tsv|--json]`.
  
  Disassembly:
  - Uses llvm-objdump -d (with --macho for Mach-O). Falls back to GNU objdump for x86 ELF and to arm-linux-gnueabihf-objdump for arm32. The arch is detected from the header.
  - Function extents run to the next function symbol, because st_size is 0.
  - ELF 'L<sym>__<blk>' locals are treated as block starts, never as functions.
  - Mach-O names have the leading '_' stripped.
  - Trailing material is classified: INT3 runs and BRK/BKPT #0x5EED are filler; NOP runs are pad. lld also fills gaps between input sections with 0xCC.
  
  Whole-binary report: text bytes, function count, instruction count, filler and pad bytes, branches by kind, and a histogram of function start mod 64.
  
  x86 only:
  - JCC-erratum units: jcc, jmp, call, ret, indirect transfers, and adjacent fusible cmp/test/add/sub/and/inc/dec+jcc pairs.
  - A unit is hit when (start mod 32) + len >= 32. Report the count, the percentage, and the windows affected.
  - Also flag 32-byte windows holding more than 18 instructions, as a rough uop-cache capacity proxy.
  
  Per function: address, mod 16/32/64, trimmed size, 32-byte windows and 64-byte lines spanned, branches, erratum hits.
  
  Loops (--loops):
  - Build the CFG from the disassembly. The native backends have no jump tables, and blocks start at the function start, at branch targets, at L-labels, and after branches.
  - Compute dominators and natural loops; mark nesting and innermost loops.
  - Per loop: header phase mod 32/64; footprint as the UNION of member-block ranges, since loops are not contiguous; windows and lines touched versus the minimum; instruction and branch counts; erratum hits; and whether the pad before the header can be reached by falling through.
  
  Other modes:
  - --compare reports per-function phase and size deltas, and per-loop window and line deltas.
  - --expect-seed recomputes, via placehash, the filler bytes expected before each Binate function and exits non-zero on a mismatch. The first function of each object is checked as >=, because of linker gap fill.
  - aarch64/arm32 get the same tables with a --window of 16/32/64, a branch-density column instead of the erratum column, and their own branch tables: aarch64 b, b.cond, bl, br, blr, ret, cbz/cbnz, tbz/tbnz; arm32 b{cond}, bl, bx, blx, pop {..pc}.
- 3. binate commit 3, T3 stack shift and T5 timing driver, in /home/user/workspace/binate/perf/placement/. Each file stays under 500 lines. — LANDED binate `4f9e3c75b` (2026-10-18), without the probe options (commit 4). As landed: the selection table follows the Decisions rule (lowest geomean first; spread and then text break ties; a tie = the 95% CI of the per-seed geomean difference covers 0), compares the unplaced builds' text, and gives no verdict when the data is short of 40 seeds or of the 8 benchmarks + self-compile; stats.py takes several experiment dirs; --aa same aborts on differing binaries; steal is per CPU; a failed output check stops the experiment. User decisions (2026-10-18): the selection order as implemented; the text budget summed over items; ab.py refuses a reference run under 0.5 s of CPU unless --allow-short-runs.
  
  runner.py (T3):
  - `spawn(argv, pad_len, cwd, out_path, aslr) -> Sample` records user, sys, wall, maxrss, minflt, majflt, nvcsw and nivcsw from os.wait4, plus an output hash, the /proc/stat steal delta and load1.
  - Environment: a fixed, scrubbed set (PATH, LC_ALL=C, HOME, a TMPDIR on tmpfs), plus BN_PLACE_PAD='x'*pad_len.
  - ASLR off: the driver calls ctypes personality(cur | ADDR_NO_RANDOMIZE) once, children inherit it, and the driver fails loudly if the call fails. `--aslr on` opts out.
  - `preflight()`: startstack must be identical across two runs and must shift by exactly 16 between pad L and L+16.
  - macOS: has no personality, so it falls back to ASLR on (logged), and uses `/usr/bin/time -l` cycles and instructions-retired when present.
  
  schedule.py: the arm order for round r is row (r mod rows) of a Williams square (k rows for even k, 2k for odd). With R even, replicates are mirrored (row, then its reverse), so linear drift inside a round cancels.
  
  ab.py usage:
  `ab.py run --work DIR --arm NAME=BNC [--arm-flags NAME='…'] [--arm-backend NAME=llvm] … --workload selfcompile --subject TREE [--work-flags '--backend native --linker bnld'] | --workload bench --bench <benchmarks>/bench [--only …] [--n B=N] --seeds 1-40 --reps 2 --jobs 4 --mode phase|shift [--aslr off|on] [--aa same|salted] [--probe SYM --probe-phases 0,16,32,48] [--static-report] [--resume] [--dry-run] [--max-load X]`
  
  Build phase (untimed, parallel --jobs):
  - Every (arm, seed) is built with `--backend native -O2 <arm flags> -fnative-place-seed=S -fnative-place-mode=M`, using -I/-L from SUBJECT/scripts/binate-paths.sh (prepending the bench root for bench mode).
  - Outputs go to fixed-width paths, store/aNN/sNNNNN/bin, so argv and the exec path have the same length in every arm.
  - Abort conditions:
    - a native arm's .text identical across two seeds;
    - two arms identical, unless --aa same;
    - the seed-0 build's sha differing from the no-flag build's;
    - `layout.py --expect-seed` failing.
  - LLVM arms are allowed and are flagged in the summary as "placement not randomized".
  
  Timed phase:
  - One round per seed. Every arm runs back to back in the Williams/mirrored order, with pad = stack_pad(seed) (the same for every arm in the round).
  - The output is checked on every run:
    - selfcompile: the output sha is identical across all arms, seeds and reps. Each run uses one fixed --build-dir, emptied before the run, and one fixed -o.
    - bench: outputs_agree semantics (exact/bytes/float within 1e-8) against the reference, and against expected.txt at DEFAULT_N, read from config.sh via sh.
  - Logs: every sample is appended to samples.tsv (round, seed, rep, pad, aslr, workload, bench, n, arm, arm_idx, pos, user, sys, wall, maxrss, minflt, majflt, nvcsw, nivcsw, steal, load1, out_hash, out_ok). builds.tsv records sha256 and text size. meta.json records host, CPU, microcode, kernel ASLR, git HEADs and compiler shas.
  
  stats.py usage: `stats.py DIR [--boot 10000 --boot-seed N] [--plan HALFWIDTH] [--json]`. It reports:
  - d[r,a] = mean over reps of ln(user[r,a]) minus the same for base, averaged over seeds, as a ratio and as %;
  - a 95% percentile bootstrap over seeds (fixed RNG) plus a t-interval, with a warning when n < 20;
  - spread: per-arm SD over seeds of the per-seed mean ln t (with σ_r²/R removed), the spread ratio versus base with a bootstrap CI, the 90th percentile (worst decile), and the fraction of seeds in which the arm is faster;
  - the variance split into run noise and placement noise;
  - position and drift diagnostics, plus flagged samples (majflt, steal, wall ≫ user+sys), reported with and without them;
  - a geometric mean over benchmarks, bootstrapped jointly over seeds;
  - user+sys as a cross-check;
  - --plan: the number of seeds n for the requested half-width;
  - with --static-report, per-seed T4 covariates against per-seed d.
  
  The 'selection' table implements the approved rule directly: the smallest spread, a mean no worse than base beyond the CI, and text size from T4 within the budget.
- 4. binate commit 4, the probe: — LANDED binate `053d743c1` (2026-10-19). As landed: both switches use StringListVar (explicit 0 / empty detected); a probe without a phase, an empty symbol and a negative or non-int32 phase are parse errors; every -fnative-place-* switch is also an error with --emit-llvm (user to confirm); layout.py spells it --expect-seed S --probe SYM --probe-phase P and exits 3 when the probe matches nothing; ab.py --probe makes the single native arm one arm per phase (NAME.pPP), rejects --aa, and its seed-0/no-flag controls carry the probe.
  - flags -fnative-place-probe=SYM (StringVar) and -fnative-place-probe-phase=P (IntVar, default -1);
  - errors: a phase without a probe, a negative phase;
  - the unit-multiple and P < 64 checks happen in common.PlaceCheck, which fails EmitObject loudly;
  - probe support in common_place.bn, using the semantics from 1b;
  - ab.py --probe builds one arm per phase from the same compiler and verifies the probed symbol's phase with layout.py.
  
  A probe whose symbol matches nothing is caught by the driver. Each EmitObject call sees only one module, and --pkg builds are separate compilations, so bnc cannot check this itself.
  
  The probe is not needed for the block-layout A/B. It is needed for the phase-1 mechanism sweeps before placement-policy work.
- 5. Phase-0 calibration gate before any real comparison. Record the numbers (date, commit, method) in the plan doc.
  - `--aa same`, 10 seeds, R=2: the run-noise floor. The CI must cover 0.
  - `--aa salted`, 20-40 seeds: the placement noise of a difference, as false-positive calibration. The CI must cover 0.
  - One arm across seeds must reproduce the known swings: fannkuch bimodal at ~12%, and the self-compile within ±1-3 s.
  - `stats --plan` then fixes the seed count n in advance; no stopping early when the CI happens to exclude 0.
  - If the swings are not reproduced, T2 models the wrong thing: stop and fix it.
- 6. Use the tools. First the block-layout A/B (your answer 1). Then the placement policies (T1: -fx64-place=… for x64, and equivalents for aarch64/arm32 per your answer 6), judged by the agreed selection rule (your answer 7), with the budget number still to come from you. Re-run the -fx64-rax-home A/B under randomization; that todo is blocked on this tooling.
- 7. Follow-ups that need your decision; none are started unasked:
  - Loop-header filler, only after block layout lands. It needs layout's fall-through decision, so filler goes only after an unconditional transfer. On all three arches, aarch64/arm32 would call ComputeLoopHeaders only when it is enabled.
  - Randomizing C-entry thunks and function-value shims, and the .s runtime objects. memzero_aarch64.s is hot on aarch64, and the arm32 baremetal aeabi_int.s/aeabi_float.s are hot on arm32. This is via the embedded assembler.
  - LLVM-arm randomization.
  - Callgrind-weighted exposures in T4/stats: an optional mechanism analysis beyond the brief.

## Block layout design (draft 2026-10-20, awaiting user review)

This section covers step 1 of the Order above: fall-through elision, loop rotation and contiguous loops. It applies to all three native backends, sits behind a hidden switch, and is judged with `ab.py`. Line anchors refer to binate `5ff452880` (main, 2026-10-20).

### 1. Compute an emission order and leave `f.Blocks` alone

- The pass decides, for each function, the order in which blocks are emitted. It runs right after `iropt.EliminatePhis` (`native/common/common_emit_object.bn:81`), so the `phicrit` blocks that pass creates get placed too.
- `f.Blocks`, liveness, register allocation and `PlanFrame` do not change.
  - `PlanFrame` assigns stack slots by walking `f.Blocks` (`common_plan_frame.bn:41,176`). Reordering `f.Blocks` would move slots, and on x64 change disp8/disp32 encoding sizes, which would confound the A/B.
  - Register allocation already works in RPO (`regalloc_liveness.bn:326-345`), and `liveBefore[bi]` is indexed by block.
- So the A/B arms differ only in block order, branch shapes and which blocks x64 aligns. irgen, the inliner, the VM and LLVM are untouched.

### 2. Analyses and algorithm (`iropt`)

`iropt/loops.bn` exports a loop nest, declared after `iropt.bni:126`:
- It exposes the membership walk that already exists at `loops.bn:60-104`.
- `ComputeLoopDepths` (used at `regalloc_scan.bn:115`) is rebuilt on top of it and must give identical output.
- Placement policy B will use it too.

```
type LoopNest struct {
	Header    @[]int      // loop l's header block
	Parent    @[]int      // smallest other loop containing l's header, or -1
	Members   @[]@[]bool  // Members[l][b]
	Innermost @[]int      // per block: innermost loop containing it, or -1
}
func ComputeLoopNest(f @ir.Func) @LoopNest
```

A new file `iropt/block_order.bn` adds `func ComputeBlockOrder(f @ir.Func) @[]int`.
- It lives in `iropt` because it needs `DomInfo` (`dom.bn:34-58`), which is private to `iropt`.
- Putting it in a separate package would mean exporting `DomInfo` and adding the package to the CLAUDE.md BUILDER-surface list.

The order is structural: it uses no weights and no sort. Every choice depends only on block indices, so the output is deterministic, which gen2 == gen3 requires.

```
d := ComputeDom(f); nest := loopNest(d); rank[b] := RPO index of b (-1 if unreachable)
cold := least fixpoint over b != 0: terminator is OP_UNREACHABLE, or Succs[b] non-empty and all cold
for each loop l, innermost first: seq[l] := rotate(l, chain(l, Header[l]))
Order := expand(chain(ROOT, 0)) ++ cold reachable blocks in RPO ++ unreachable blocks by index

units(R): reachable non-cold blocks whose innermost loop is R, plus the loops with Parent == R
entry(u): the block or the loop's header;  first(u)/bottom(u): first/last block of expand(u)

chain(R, e):
  out := [unit(e)]
  repeat:
    z := bottom(last(out))
    c := unit(s) for the first s in Succs[z] (Block1, then Block2) with unit(s) in units(R),
         unplaced, first(unit(s)) == s, and ready(unit(s))
         // ready(u): every pred p of entry(u) with 0 <= rank[p] < rank[entry(u)] is placed
    if none: c := the unplaced unit of R with the lowest rank[entry(c)]    // RPO cursor
    if none: return out
    append c

rotate(l, s):
  h := Header[l]; z := bottom(last(s))
  if h == 0 || len(s) < 2 || h ∉ Succs[z] || z exits l: return s
  for k := 1 .. len(s)-1:                       // k = 1 cuts right after the header
    b := s[k-1]
    if b is a block && b exits l && first(s[k]) ∈ Succs[b]: return s[k:] ++ s[:k]
  return s
```

- **Entry block.** Block 0 is never marked cold. So a function in which every path panics still starts at its entry block. A loop that contains block 0 must be headed by it; such a loop is not rotated, because the prologue falls into block 0.
- **Rotated inner loops** start with their top block, not their header. The block before such a loop has to jump to the header anyway, so the loop never takes a fall-through slot. The RPO cursor places it.
- **Readiness** puts a merge block after all of its forward predecessors, so an if/else comes out as cond, then, else, merge. Readiness is only a preference: every CFG still gets a valid order.
- **Cost:** O(L·n + e) per function, plus one more `ComputeDom` call. This applies only in `loops` mode.
- **Checks.**
  - The pass panics if the result is not a permutation or `Order[0] != 0`, the same way `ir_phi_elim.bn:155` panics.
  - Under `--verify-ir` (`irbuild/verify.bn:190`; this needs an exported getter for the flag), it also checks that each loop is one contiguous range of `Order`, with inner loops nested inside it.

### 3. What rotation does to each loop shape

Today a for-loop (`irgen/gen_flow.bn:50-53`) executes 3 taken branches per iteration: `jcc body`, `jmp post` and `jmp cond`. It executes 2 when `mergeSinglePredBlocks` (`thread_jumps.bn:183`) merges post into the body. A `continue` in the loop prevents that merge.

| Shape | Layout under `loops` | Taken branches per iteration |
|---|---|---|
| for/while with a test in the header | `pre: jmp H; top: body…; post falls into H; H: jcc top`; the exit falls through | 1 taken (`fallthrough` on today's order: 1 taken + 1 not taken) |
| `for { A; if c { break }; B }` | cut after the block that exits | 1 taken |
| exit test inside an if/else | not rotated; the latch does `jmp H` | 1 taken |
| self-loop, or self-loop with `phicrit` latch S | `B: jcc B`, or `[S, B]` with `B: jcc S`; the exit falls through | 1 taken |
| latch tests at the bottom | not rotated; already ideal | 1 taken |
| loop with several exits | the bottom exit falls through; the others are forward jcc, not taken | – |
| nested loops | the inner loop is placed as one unit; `jmp H_inner` once per outer iteration | – |

- Rotation adds one taken `jmp H` each time the loop is entered.
- Today `phicrit` blocks, inliner clones (`inline_multiblock.bn:49,60`) and refcount release blocks are placed at the end of the function. With this pass they are placed inside their loop.
- **Irreducible CFGs:** none is known to arise. The language has no `goto`, and `thread_jumps.bn:19-30` never threads a jump into a loop header or past one. If one does arise, its blocks are treated as ordinary units of their region and the order is still valid.

### 4. Cold code

**Moved to the end of the function:**
- blocks ending in `OP_UNREACHABLE`: panic (`gen_panic.bn:48`), assertion failure (`gen_assert.bn:64,68`) and falling off the end of a non-void function (`gen_func.bn:267,283`);
- blocks whose successors are all cold.

As a result, `if bad { panic(…) }` compiles to a forward jcc that is not taken. A cold block can never reach a loop latch, so it is never part of a loop.

**Not cold:** `ivrd.present` and `fv_refdec` (`gen_util_refcount.bn:134-185`). They run whenever the value's data pointer is non-null; `OP_REFDEC` itself decides whether the count reaches zero.

**Out of reach for this pass (Question 1):** the following paths are emitted inline while lowering a single instruction, so they are not IR blocks and the pass cannot move them.
- **Bounds check.** `cmp; jb ok; call BoundsFail` takes a branch on every in-bounds check (`x64_guards.bn:33-55`, `aarch64_guards.bn:37-57`, `arm32_dispatch.bn:285-311`). The division check works the same way.
  - The fix would be a `jae` to a stub placed after the function's last block. The stub would be named with `common.BoundsLabel(…, fail=true)` (`common_names.bn:30-42`), which exists but is unused.
  - The stub never returns and sees the same registers as the check site, so moving it out of line is mechanical.
- **RefDec destroy path.** On x64, `jnz skip` is taken whenever the count stays above zero (`x64_managed.bn:133-180`; also `aarch64_refcount.bn:132-198` and `arm32_refcount.bn:128-182`). Stubs for this path would need a jump back to the main line.

### 5. Plan and plumbing (`native/common`)

A new file `common_block_plan.bn`, about 90 lines (`common.bni` is 922 lines), holds:
- the constants `BLOCK_LAYOUT_SOURCE` (0), `BLOCK_LAYOUT_FALLTHROUGH` and `BLOCK_LAYOUT_LOOPS`, with `BlockLayoutByName` and `BlockLayoutName`;
- `type BlockPlan struct { Order @[]int; AlignTop @[]bool; FallThrough bool }`, where `AlignTop` is indexed by block;
- `PlanBlocks(f, mode)`, which fills the plan as follows:

| Mode | Order | AlignTop | FallThrough |
|---|---|---|---|
| SOURCE | identity | `ComputeLoopHeaders` | false (output byte-identical to today) |
| FALLTHROUGH | identity | `ComputeLoopHeaders` | true |
| LOOPS | `ComputeBlockOrder` | first block of each loop in `Order` | true |

Changes to existing code:
- `common.EmitObject` (`common_emit_object.bn:14-15`) gains a `layout int` parameter. It is validated before anything is emitted, the same way `PlaceCheck` is (`:28-34`).
- The `EmitFunc` call at `:91` passes `PlanBlocks(f, layout)`.
- `ArchEmitter.EmitFunc` (`common.bni:44`) gains a `plan @BlockPlan` parameter. That touches the three emitters and four test doubles:
  - `relabelRecorder` (`common_emit_object_test.bn:58`);
  - `placeCapture` in `x64_place_test.bn:51`, `aarch64_place_test.bn:54` and `arm32_place_test.bn:55`.

### 6. Per-backend emission

**Shared changes**
- `emitFunc` keeps its signature, so its 52 test call sites need no change. It becomes a wrapper for `emitFuncPlanned(…, PlanBlocks(f, SOURCE))`.
- The block loops (`x64_emit_func.bn:172`, `aarch64_emit_func.bn:294`, `arm32_emit_func.bn:273`) iterate over `plan.Order`. If `Order` has the wrong length, that is a `SetError`.
- The label rule stays `bi > 0` (`:177`, `:296`, `:275`), so every block except the entry keeps its label.
- `next` is the block that follows in `Order` when `FallThrough` is set, and nil otherwise. It is passed explicitly to a new `emitTerm(…, ins, next) (endedInTransfer bool)`. `emitInstr`'s own JUMP/BRANCH cases pass nil, so the instruction-level tests stay unchanged.

**How terminators are lowered**

| Case | Emitted |
|---|---|
| `Block1 == Block2` | handled as a JUMP |
| JUMP to `next` | nothing |
| else-arm is `next` | `cc → then` |
| then-arm is `next` | `inv(cc) → else` |
| neither arm is `next` | today's two-branch pair |

Branch fusion only fuses integer compares (`fold/branch_fuse.bn:20-22`), so `cc^1` is a correct inverse.

**Guard against code after a terminator**
- JUMP and BRANCH are on none of the retention allowlists (`x64_regmap.bn:400-427`, `aarch64_retention.bn:16`, `arm32_retention.bn:15-44`). So no register is dirty after a terminator, and the spills after it emit nothing (x64 `:222-231`, aa64 `:344-354`, arm32 `:316-324`).
- An elided JUMP adds no fixup.
- Each backend records the code offset right after the terminator. If the end of the block is at a different offset, it calls `SetError`, because once blocks can fall through, any code emitted there would run on only one path.
- The check runs in every mode, and SOURCE output does not change.

**x64**
- A new file `x64_branch_term.bn` takes over the terminator code in `x64_dispatch.bn:175-211`.
- When the then-arm is next: fused branches emit `Jcc(InvertCC(a, cc), else)`, unfused ones emit `test; jz else`.
- When the else-arm is next: fused branches emit `Jcc(cc, then)`, unfused ones emit `test; jnz then`.
- The new `x64.InvertCC(a, cc) int` returns `cc^1` (the pairs are at `x64.bni:76-92`). An invalid cc raises an error through `condOK` (`x64.bn:266`).
- `plan.AlignTop` replaces `ComputeLoopHeaders` (`:171`, `:178`), and the comment at `:161-165` is rewritten.
- The alignment pad goes before the loop's top block, not its header. After rotation the header is entered by fall-through on every iteration, so padding there would execute NOPs every time.
- The pad before a rotated loop's top comes right after `jmp H`, so it never runs. An unrotated top that is entered by fall-through runs its pad once per loop entry (Question 4).

**aarch64**
- JUMP elision goes in `aarch64_dispatch.bn:171-177`.
- `emitCondBranch` (`aarch64_compare.bn:133-150`) gains `next`. Fused branches are inverted with the existing `invertCondForOp` (`:157`).
- `emitCondBranchTo` (`aarch64_branch_relax.bn:63-85`) makes its register mode explicit:
  - `COND_NE` means CBNZ and `COND_EQ` means CBZ;
  - the far-backward form uses the opposite instruction followed by a `B`;
  - the call at `:148` passes `(COND_NE, COND_EQ)` instead of today's `0, 0`;
  - a branch whose then-arm is next becomes `cbz else`.
- **Branch islands**
  - The block-end call at `:355` becomes `islands.check(a, endedInTransfer)`.
  - Today `endsInTransfer` (`:162-169`) decides by looking at the last emitted instruction word. After an elided jump, that word can be an island's last `B` stub followed by an `over:` label, or an inline `B` to a label at the end of the block. In both cases control still falls through.
  - Blocks that fall through therefore get the `B over` form. `endsInTransfer` is kept only to cross-check the flag.

**arm32**
- JUMP elision goes in `arm32_dispatch.bn:93-99`, and `emitBranch` (`:194-222`) gains `next`.
- Unfused branches become `cmp #0; beq else` or `cmp #0; bne then`.
- The new `arm32.InvertCond(a, cond) int` raises an error on AL and on any value outside 0-13. It has to check for itself because `condBits` (`arm32.bn:187`) silently masks bad values (`claude-todo.md:827`).
- B's 24-bit immediate reaches ±32 MiB, and arm32 has no literal pools, so nothing else changes.

**Mach-O:** block labels are local temporaries and never start an atom (`asm_temps.bn:66-69`).

### 7. Short x64 branches (rel8), a later step with its own switch value (Question 3)

- **Backward branches.** When the target label is already defined, emit the existing `x64.JccOffset`/`JmpOffset` (`x64_branch_offset.bn`; `x64_aggcopy_loop.bn:35` already does this).
  - This is exact, about 30 lines, and covers rotated loop back edges.
  - It records no fixup, which does no harm at a terminator because the guard covers it.
- **Forward branches.** Use grow-only relaxation in `asm/x64`.
  - Alignment pads are recomputed. The relaxation must finish for each function before `PlaceFillBefore` reads `CurrentOffset` (`common_emit_object.bn:88`).
  - It also gives `bnas` short branches (`claude-todo.md:2364`).
  - The alternative, emitting everything once to measure and then again for real, doubles x64 emission time and requires emission to have no side effects.
- This is a separate step because it changes code size and JCC-erratum exposure on its own.

### 8. Switch and measurement

- **The switch** is `-fnative-block-layout=source|fallthrough|loops`, default `source`.
  - It is a hidden `StringListVar` registered in `registerNativeFlags` (`cmd/bnc/args_native.bn:32-44`).
  - Its errors follow the `-fnative-place-*` rules (`:63-83`): an unknown value, use without `--backend native`, or use with `--emit-llvm`.
- **Route:** `native.Options.BlockLayout` (`native.bn:85-104`) → each backend's `Options` → `common.EmitObject(…, opts.Place, opts.BlockLayout)` (`x64.bn:25`, `aarch64.bn:23`, `arm32.bn:24`).
- **A/B:** one compiler, three arms.
  ```
  ab.py run --work W --arm src=$BNC --arm ft=$BNC --arm loops=$BNC \
    --arm-flags ft='-fnative-block-layout=fallthrough' --arm-flags loops='-fnative-block-layout=loops' \
    --workload bench … --seeds 1-40 --reps 2 --mode phase        (and --workload selfcompile)
  ```
  - `ft` against `src` measures elision alone.
  - `loops` against `ft` measures reordering, rotation and the move of the alignment pad.
  - `ab_build` aborts if two arms produce identical binaries, which confirms that each switch value actually changed the code.
  - `stats.py` applies the Holm-corrected selection rule.
  - Use at least 40 seeds. `--plan` gave 12 seeds for the benchmark geomean and 14 for the self-compile at ±1%.
- **Self-compile workload.** The timed self-compile uses the default work flags in both arms, so only the bnc being timed differs.
- **Compile-time cost of the pass** is measured separately: one bnc compiling `cmd/bnc` with `loops` and with `source`. This needs a per-arm `ab.py --arm-work-flags` option (commit 9).
- **Where it runs:** x64 is timed here and aarch64 on the aa64 machines. arm32 has no hardware, so it is judged only by `layout.py --loops --compare`.

### 9. Tooling (`perf/placement/layout_loops.py`)

- `build_blocks` (`:45-85`) already follows fall-through across labels and padding. Every block except the entry keeps its label, so `loop_keys` (`:253-271`) can still match loops across arms.
- New fields for each loop:
  - `top`, `top_m32` and `top_m64`;
  - `pad` and `pad_ft`, measured before the top block;
  - `jmps` (unconditional branches inside the loop's footprint) and `back` (backward branches). These count branches per iteration statically and are the only arm32 evidence.
- A gap inside a loop's footprint that contains only alignment padding is merged with the ranges on either side. A whole-binary count of loops with `ranges > 1` then checks contiguity. aa64 branch islands can still legitimately split a loop.
- The docstring line "Loops are not contiguous" (`:18-19`) becomes "Loops need not be contiguous".

### 10. Tests

**iropt.** `block_order_test` runs on hand-built IR.
- Cases: straight-line code, if/else, for, while, `for { … break … }`, a self-loop, a `phicrit` latch, nested loops, a loop with several exits, a loop headed by the entry block, a function in which every path panics, an irreducible cycle, unreachable blocks and chains of cold blocks.
- Properties checked:
  - the order is a permutation with `Order[0] == 0`;
  - loops are contiguous and nested;
  - a for-loop's header comes last, right after its latch;
  - the exit block comes right after the loop;
  - cold blocks come after hot ones, and unreachable blocks come last;
  - the output is deterministic.
- Plus tests for the loop nest itself.

**common.**
- `PlanBlocks` for each mode.
- The test doubles receive the plan.
- An unknown mode fails.
- SOURCE and LOOPS produce identical register allocation and `PlanFrame` size.

**Per backend** (`*_layout_test.bn`):
- an elided JUMP emits 0 bytes;
- all 10 fused compares are inverted correctly, checked by decoding the output;
- unfused branches emit JZ/CBZ/BEQ;
- when neither arm is next, the output is unchanged;
- the code-after-terminator guard fires;
- `InvertCC` and `InvertCond`, including their error cases;
- x64: the pad before a loop top follows a `jmp`;
- aa64: far-backward CBZ, and an island at the end of a block that falls through gets `B over` (as in `aarch64_branch_relax_test.bn:179-240`);
- Mach-O: fall-through adds no atom start.

**Routing.**
- `native_test`: the setting reaches x86_64/elf, x86_64/macho, aarch64/macho, aarch64/elf and arm32/elf, and `Options{}` behaves as SOURCE.
- `args_native_test`: the default, each value and each error.

**Conformance.** Run with `BINATE_FLAGS` set to `-fnative-block-layout=fallthrough`, then to `-fnative-block-layout=loops`, on:
- `builder-comp_native_x64-comp_native_x64` and `builder-comp_native_x64_darwin-comp_native_x64_darwin`;
- `builder-comp_native_aa64-comp_native_aa64` and `builder-comp_native_aa64_linux-comp_native_aa64_linux`;
- `builder-comp_native_arm32_baremetal` (the required mode for native arm32) and `builder-comp_native_arm32_linux`.

**Self-compile.**
- A native bnc built with SOURCE, and one built with no flag, are byte-identical (`cmp`) to the build from before the change.
- A bnc built with `loops` compiles itself to the same bytes as one built with `source`.
- gen2 == gen3 under `loops`.
- `bnc --test --backend native -fnative-block-layout=loops` passes on the native packages.
- The gen1 build passes (the pinned BUILDER).

**Existing tests that pin branch shapes** stay unchanged while the default is `source`:
- `x64_dispatch_test.bn:242-302`
- `arm32_dispatch_test.bn:65-80`
- the three `TestEmitFuncEndsAtLastTerminator` tests
- `x64_align_test.bn:48-75`

### 11. Commit sequence

Each commit can land on its own and is approved separately. SOURCE output stays byte-identical throughout.

1. `x64.InvertCC` and `arm32.InvertCond`, with tests. About 30 lines of code and 60 lines of tests.
2. Plumbing, about 220 + 250 lines:
   - `BlockPlan` with SOURCE and FALLTHROUGH;
   - the `EmitObject`/`ArchEmitter` change and the 4 test doubles;
   - the `Options` fields and the switch;
   - iterating `Order` and using `AlignTop`;
   - `emitTerm` and the guard;
   - routing tests.

   `fallthrough` has no effect on a backend until that backend's own commit lands.
3. x64 elision and inversion. About 110 + 200.
4. aarch64 elision, inversion, CBZ and the island flag. About 120 + 250.
5. arm32 elision and inversion. About 90 + 180.
   - Commits 3-5 implement T6's "drop branch-to-fallthrough" item (`claude-todo.md:1390`) behind the switch; the item is done when a default turns it on.
   - A `src`/`ft` A/B can run at this point.
6. `LoopNest`, with `ComputeLoopDepths` rebuilt on it. About 90 + 120.
7. `ComputeBlockOrder` and the `--verify-ir` contiguity check. About 270 + 450.
8. LOOPS mode: the LOOPS plan and `AlignTop` at loop tops, with conformance under `loops`. About 40 + 120.
9. The new `layout_loops.py` fields and `ab.py --arm-work-flags`. About 100 + 120.
10. The A/B. Then, if you decide to, flip the default and update the byte-pinned tests.

Commits 3-5 change only branch shapes, on today's block order. Commit 8 changes only the order, with the shape code already in place. So a conformance failure points at a single change. The backward conditional branches that first appear in commit 8 are already unit-tested in commits 3-5.

### 12. Risks

- **Code after a terminator that falls through:** the guard catches it.
- **aa64 islands landing in a fall-through gap:** the `endedInTransfer` flag prevents this.
- **Short loops may slow down.** Rotation adds one taken `jmp` per loop entry, so loops that run about once per entry pay for it. The Holm-corrected per-workload guard would catch that.
- **Alignment moves.** `loops` moves x64 alignment from the loop header to the loop top. Under `fallthrough`, a pad after an elided preheader `jmp` runs once per loop entry.
- **If `loops` underperforms,** first disassemble to check that rotation actually happened. The fallback is another switch value: a chain order driven by static weights. The weights would be int64, so 32- and 64-bit hosts produce the same layout, and the rotation point would be chosen by minimising cost.
- **Later functions move.** Function sizes change, which shifts every later function; placement randomization absorbs that.
- **arm32** is judged only from the static report.

### Questions for the user

1. Should the bounds-check and division-check failure paths move out of line as the next step, with their own A/B? I recommend it. And should the RefDec slow path, whose stubs need a jump back, follow, or stay inline?
2. Should the decision to make `loops` the default use the placement selection rule (Holm-corrected, at least 40 seeds, benchmark geomean plus self-compile)? And should all three backends share one default, decided from the x64 and aa64 timings plus arm32's static report, or should each architecture get its own?
3. x64 short branches: should they be the next step, backward branches first and then relaxation for forward branches (my recommendation), or should backward short branches be folded into this work?
4. Should an unrotated x64 loop top that is entered by fall-through stay aligned? I suggest keeping it and deciding in the alignment-policy step.

## Phase-0 calibration results (x86 VM, 2026-10-19/20)

Host: this session's Firecracker VM (Intel Xeon @ 2.10GHz, Cascade Lake, 4 vCPUs, no PMU), ASLR off, one compiler
built from main `053d743c1` for both arms, the 8 benchmarks (fasta at N=2500000, mandelbrot 3000, record-churn
16000, so every run takes >= 0.5 s of CPU; the rest at DEFAULT_N) and the native self-compile, 2 reps per seed.
Raw data and `stats.py` reports: scratch dirs aa-same-bench, aa-salted-bench, aa-same-self, aa-salted-self
(session scratchpad `cal/`; not durable).

- A/A same (identical binaries), 10 seeds: every interval covers 0 (benchmark geomean +0.32% [-0.64, +1.29];
  self-compile -0.97% [-2.97, +0.88]).
- A/A salted (each arm placed differently), 40 seeds on the benchmarks, 30 on the self-compile: benchmark geomean
  -0.01% [-0.46, +0.46]; but 3 of the 9 per-workload 95% bootstrap intervals exclude 0: fannkuch -1.64%
  [-2.96, -0.33], n-body +0.90% [+0.21, +1.62], self-compile +0.62% [+0.02, +1.22] (t-interval [-0.01, +1.25]).
  Chance gives >= 3 of 9 under 1% of the time: either the per-workload intervals under-cover on this host or
  something differs systematically between arm 0 and arm 1.
  Replicate on new seeds (2026-10-20): fannkuch -0.08% [-1.23, +1.06] and n-body +0.27% [-0.61, +1.12] (40
  seeds 41-80) cover 0, so those two were chance; but the self-compile again has B slower, +0.75%
  [+0.10, +1.39] (30 seeds 31-60; pooled with the first run ~+0.7%, z ~ 3): a real systematic difference
  between the arms on the self-compile only. Ruled out: file-backed huge pages (no PMD-mapped text in either
  arm's bnc), text size (equal means), every static layout covariate (no arm difference, |z| <= 1.5; per-seed
  d uncorrelated). Being tested: build order -- ab.py builds arm 0's binaries before arm 1's, so arm 1's page-
  cache pages are allocated later (physical page colouring of a 14 MB text). With the page cache dropped
  between the build and timed phases (seeds 61-90) the bias went away: +0.06% [-0.88, +1.03] (consistent with
  the build-order explanation, ~1.1 SE from the earlier +0.7%, so not proof). binate `8ae1d26cd` (not yet
  landed): ab.py builds seed by seed with the arms alternating, and --drop-caches drops the page cache after
  the build phase. Validation without a drop (seeds 91-120, seed-by-seed builds): B +1.16% [-0.01, +2.29], so
  build order is NOT the explanation (the drop-cache run's +0.06% was the one exception in four runs: +0.62,
  +0.75, +0.06, +1.16). Remaining candidates: arm 0's placement seeds are the small integers themselves while
  arm 1's are salted 31-bit values, or a later arm index is slower for another reason. Three-arm salted A/A
  (A raw seeds, B salted once, C salted twice; seeds 121-150): B-A +1.85% (t 3.1), C-A +0.76% (t 1.0),
  C-B -1.09% (t -1.8); position effects ~0 -- fits neither "later arm slower" nor "salted seeds slower"
  cleanly. A second --drop-caches A/A (seeds 151-180): B -0.09% [-0.88, +0.74]. So: all four runs that kept
  the freshly built binaries in the page cache showed the second arm slower (+0.62, +0.75, +1.16, +1.86%),
  both runs that dropped it showed nothing (+0.06, -0.09%). Mechanism unknown (not build order, not PMD
  THP, not placement); remedy: binate `5ff452880` (landed 2026-10-20) makes ab.py's --drop-caches auto|on|off
  default to auto (drop when root, warn otherwise), and builds the arms seed by seed.
- A real placement effect (from the 330 per-seed points of the five salted self-compile runs, each centred
  on its round): the entry phase of the hottest function, native/common blockLiveBefore, predicts the whole
  self-compile's time: phase 16 -0.50%, 48 -0.41%, 0 +0.34%, 32 +0.56% (se ~0.16 each), i.e. entering at
  16 mod 32 is ~0.9% faster than 0 mod 32 (~5 se). livenessFixpoint's phase shows nothing. layout.py on a
  fast (16) vs slow (0) build: blockLiveBefore has 18 vs 30 JCC-erratum units (31 vs 50 counting fused
  pairs), 12 vs 19 in its main loop (for.cond.5) -- on this Cascade Lake (JCC-erratum microcode) the likely
  mechanism. It does not explain the arm bias (predicted B-A 0.0-0.1%). The whole-binary JCC covariate
  correlated weakly because cold code dominates it: covariates should be weighted by a profile (--hot).
- Run noise dominates: two back-to-back runs of one binary differ by a median 2.6-3.9% (p90 7-11%) on
  binary-trees / fannkuch / richards; per-run noise ~1.0-1.5% spectral-norm, ~2% n-body, ~2-3% self-compile,
  4-8% fasta / record-churn. The host speed also drifts: the A/A-same self-compile's per-round mean went
  37.4 -> 49.8 -> 45.0 s (user time, no load of ours), so cross-seed spreads and covariates measured here
  are host-dominated; only the interleaved A-B difference is usable.
- Seeds for a +-1% 95% interval (`--plan`): geomean over the 8 benchmarks 12, self-compile 14; single benchmarks
  11 (spectral-norm) to 146 (record-churn).
- The known swings did not reproduce cleanly: fannkuch's per-seed times are broad (range ~21-27%) but not
  bimodal, and they do not correlate with the inner loop's excess windows/lines (r ~ +0.06/+0.11) or JCC hits
  (r -0.13); the self-compile's per-seed spread is ~5% (salted) with weak covariates (inner excess windows
  r +0.30, JCC hits r -0.22, none significant at 30 seeds).
- The selection rule's "no workload slower beyond its 95% CI" guard rejected the salted A/A arm (n-body, and on
  the self-compile run the self-compile itself): over 9 workloads it rejects a neutral arm ~20% of the time by
  design, more with the under-coverage above. Decided 2026-10-20: a Holm correction over the workloads;
  landed as binate `3f95bef54` (2026-10-28): stats_select applies Holm's step-down test, family-wise 2.5%, to
  each workload's one-sided Student-t p-value of "the arm is not slower" (below 0.025 exactly when its 95% t
  interval lies above 0). Not the bootstrap: its tails are too thin at 0.025/9 (normal null data rejected
  0.0057 vs 0.0028 nominal at 40 seeds, simulated); resampling the salted A/A's centred per-seed differences
  as the null, the summed first-threshold rate over the 9 workloads was ~2.6% for t vs ~4.8% for the
  percentile bootstrap. Negatively skewed differences (richards, skew -1.2) still reject ~3-5x nominal under
  t, the bootstrap, or bootstrap-t. On the salted A/A data the neutral arm is now eligible (n-body p 0.0087 >
  0.025/9).

## Tests
- common_place_test.bn:
  - PlaceHash pinned vectors, the same ones as test_placehash.py.
  - PlaceActive is false only for seed 0 with no probe.
  - Over 1024 synthetic symbols:
    - shift fills are unit multiples in [0, 64);
    - all K phases occur, roughly balanced;
    - a different seed changes most fills.
  - Phase mode: off + pre ≡ phi (mod 64) for arbitrary off, including an off that is not unit-aligned.
  - Probe: pre lands the symbol at ProbePhase, pre + post = 64, and later offsets are identical across phases 0/16/32/48.
  - PlaceCheck rejects: a ProbePhase that is not a unit multiple or is >= 64; a probe phase with no symbol.
- common_emit_object_test.bn:
  - relabelRecorder logs the (EmitPlaceFill n | EmitFunc sym) sequence and .text Align.
  - Zero PlaceOptions: no fill calls and unchanged alignment.
  - A seed: exactly one fill immediately before each non-extern function, with n == PlaceFillBefore(...); none for extern functions.
  - Phase mode: .text Align == 64.
  - An invalid option makes EmitObject return false.
- x64 (x64_emitter_test.bn or a new x64_place_test.bn):
  - EmitPlaceFill(n) emits n bytes of 0xCC; n % 16 != 0 sets an error.
  - A seeded module with three functions:
    - every function symbol and c_export alias is 16-aligned;
    - the bytes between the previous terminator and the next entry are 0xCC then NOPs;
    - each function's body bytes equal its seed-0 bytes;
    - phase mode puts each entry at phi mod 64.
  - The existing x64_align_test, x64_emit_func_test and x64_cexport_trampoline_test pass unchanged.
- aarch64_emitter_test.bn and arm32_emitter_test.bn:
  - Filler words decode as BRK #0x5EED / BKPT #0x5EED; n % 4 != 0 sets an error.
  - Seeded function offsets match the prediction, and bodies are unchanged.
  - arm32: '$a' stays at offset 0. aarch64 ELF: '$x' stays at offset 0.
  - aarch64 Mach-O symbol offsets keep their phase.
- native_test.bn, mirroring TestEmitObjectRoutesX64RaxHome, for x86_64/elf, x86_64/macho, aarch64/macho, aarch64/elf and arm32/elf:
  - Options{} and Place{Seed:0} produce the same bytes.
  - A chosen seed with nonzero fill produces different bytes, so Place reaches all three backends.
  - Two seeded runs are identical.
  - X64RaxHome still reaches only x64.
- args_native_test.bn:
  - Defaults are off.
  - -fnative-place-seed=7 and --fnative-place-seed 7 (with --backend native) set Seed=7 and change neither Opt.Passes, the -O level nor the clang flags.
  - Errors:
    - seeds -1 and 2147483648;
    - an unknown mode;
    - a mode without a seed;
    - a seed without --backend native;
    - a probe phase without a probe, and a negative probe phase (commit 4).
  - iropt.OptPassByName(<each new name>) < 0.
  - The existing rax-home tests still pass after the move.
- Manual validation, not CI:
  - seed-0 cmp identity for a native bnc and for aa64_linux/arm32 `-c` objects;
  - BINATE_FLAGS seed runs, in phase and shift modes, on builder-comp_native_x64-comp_native_x64, builder-comp_native_aa64_linux-comp_native_aa64_linux and builder-comp_native_arm32_baremetal (plus arm32_linux);
  - a seeded native bnc self-compiles to the same bytes as an unseeded one;
  - `bnc --test --backend native -fnative-place-seed=N` over the native packages;
  - the gen1 build;
  - the gen2==gen3 fixed point still holds.
- perf/placement tests (python3 -m unittest discover perf/placement; not CI):
  - test_placehash.py: the vectors shared with Binate; stack pads are multiples of 16 in [0, 4096); salted seeds are nonzero.
  - test_layout.py, on canned llvm-objdump fixtures in perf/placement/testdata/:
    - a jcc ending exactly at 0x20 and a jcc crossing 0x1e-0x22 are hit;
    - a fused cmp+jcc pair crossing where the jcc alone does not is hit;
    - a ret crossing is hit;
    - natural loops on a non-contiguous loop, with the union footprint and nesting;
    - L-label locals are not counted as functions; st_size=0 extents work;
    - filler/pad classification and the --expect-seed pass/fail cases;
    - aarch64/arm32 branch tables.
  - test_schedule.py: over a full Williams cycle each arm takes each position equally often and every ordered neighbour pair appears equally often, for k = 2..5; mirrored replicates; a regression test showing that rotate-by-r with reversal on odd r degenerates for k=2.
  - test_stats.py, on synthetic logs with a known effect, placement noise, run noise, linear drift and an injected position bias:
    - the estimate is unbiased under drift and bootstrap coverage is about 95%;
    - the A/A CI covers 0, and the position diagnostic finds the bias;
    - the spread-ratio CI recovers an injected 2x spread reduction;
    - --plan arithmetic is correct;
    - flagged-sample handling reports both estimates.
  - test_runner.py: the env is deterministic with the pad length correct; run paths have equal length; under ASLR off a child's startstack is identical twice and shifts by exactly the pad delta.
- Phase-0 acceptance on real workloads:
  - A/A-same and A/A-salted CIs cover 0.
  - fannkuch's ~12% bimodality and the self-compile's 1-3 s swing are reproduced across seeds.
  - layout.py --compare between seeded builds shows phase shifts matching placehash.
  - fannkuch's 0x5f-byte inner loop spans 3 windows at header phase 0 mod 32 and 4 at phase 16 (T4 must show exactly this).

## Decisions (user, 2026-10-12 and 2026-10-15)

- Order: block layout, then placement policies; measurement tooling lands first, hidden (not in
  printUsage, not in --list-opt-passes, not wired into CI).
- Fall-through elision: work-4 released T6 on 2026-10-12; this plan takes "drop branch-to-fallthrough"
  (claimed in T6) as part of block layout, on all three native backends.
- JCC-erratum mitigation: an opt-in switch first; the default is decided from data.
- Text-size budget for a default policy: at most ~1% text growth, more only with a speedup beyond its
  confidence interval; per-site max-skip knobs as hidden switches; padding off in a future size mode.
- Selection rule (fixed before measuring): lowest geometric-mean time over the 8 benchmarks + the native
  self-compile across >= 40 randomized placements; no workload slower beyond its 95% CI; within the size
  budget; ties broken by smaller placement spread, then smaller code; otherwise keep the current policy.
- Tooling: phase mode is the default placement mode (literal shift filler as an option); timed runs with
  ASLR off by default; tooling in binate/perf/placement; -fnative-place-* without --backend native is an
  error.
- Hardware: most work happens on an aa64 machine, so aarch64 timing is available there (this session is
  on x86 and owns the x64 measurements). No arm32 hardware is readily available: arm32 policies are judged
  with the static report only.
