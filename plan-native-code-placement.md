# Plan: native code placement (x64, aarch64, arm32)
Status: tooling designed (2026-10-12), not started; waiting on the user decisions listed at the end. Tracked in `claude-todo.md` ("native code placement").
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
- 1. binate commit 1: T2 function-level placement seed for all three native backends.
  
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
  - Probe symbol, in either mode, including at seed 0: phi = ProbePhase (a target phase, not a shift), pre computed as in phase mode, and post = PLACE_SPAN - pre. Every later byte is then identical across all probe phases, including phase 0.
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
- 2. binate commit 2, T4 static report, in /home/user/workspace/binate/perf/placement/ (Python stdlib only):
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
- 3. binate commit 3, T3 stack shift and T5 timing driver, in /home/user/workspace/binate/perf/placement/. Each file stays under 500 lines.
  
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
- 4. binate commit 4, the probe:
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

## Open decisions (asked 2026-10-12)

- Fall-through elision vs work-4's T6 claim: reassign, split, or wait.
- JCC-erratum mitigation default: on, behind a target-tuning switch, or opt-in only (recommended:
  opt-in switch first, default decided from data).
- Text-size budget for a default policy (suggested: at most ~1%; larger only with a speedup beyond its
  confidence interval), plus hidden per-site max-skip knobs; padding off in a future size mode.
- Selection rule, fixed before measuring (proposed: lowest geometric-mean time over the 8 benchmarks +
  native self-compile across >= 40 randomized placements; no workload slower beyond its 95% CI; within
  the size budget; ties broken by smaller placement spread, then smaller code; otherwise keep base).
- Tooling: phase mode as the default placement mode (vs literal shift filler); ASLR off for timed runs;
  tooling under binate/perf/placement; -fnative-place-* without --backend native is an error.
- aarch64/arm32 timing hardware: without an arm64 host or real arm32 hardware, their policies can only be
  judged statically (qemu timing is meaningless).
