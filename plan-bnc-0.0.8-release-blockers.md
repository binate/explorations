# Plan: bnc-0.0.8 Release Blockers

Investigate and clear the blockers preventing a `bnc-0.0.8` release, then
cut it. Structured as **three disjoint lanes (A, B, C)** that touch
non-overlapping file sets and can be assigned to separate workers
concurrently, followed by a single **convergence** step (cut the release).

See `release-process.md` for the release mechanics this plan feeds into.

## Context (what's already known / verified)

- We are cutting **bnc-0.0.8** (BUILDER is `bnc-0.0.7`; `VERSION` is
  already `bnc-0.0.8-pre`). 261 commits since `bnc-0.0.7`; **substantive** —
  codegen (ir/native), type-checker, VM, and the runtime/stdlib surface
  (`same` builtin added, `errors.Is`/`io.IsEOF` added, `Itoa` removed, a
  cross-pkg mangler-collision fix). Worth cutting.
- **VERIFIED GOOD (2026-06-10):**
  - The release bundle **builds** locally via `make-bundle.sh` (all four
    binaries, BUILDER→gen1→final; the build scripts' BUILDER-first stdlib
    resolution sidesteps the `same` skew). macos-arm64 only — linux-x64 not
    locally testable.
  - The macos-arm64 bundle **runs real code**, including a bounds-check
    program that exercises `rt.BoundsFail → bootstrap.Write`.
  - The tier-0 `lang` carve-out **bundle gap is FIXED + LANDED** (binate
    `84818a77`: `lang.bn` drops `pkg/binate/buf`, formats `bool` via bare
    string literals). A fresh bundle still needs a from-scratch re-smoke
    (Lane C task 2).

## Current CI state (main HEAD)

| Workflow | State | Lane |
|---|---|---|
| Code hygiene | **GREEN** (1 pre-existing file-length warning: `gen_iface.bn`) | — |
| E2E | **RED** — `same` BUILDER-skew | C |
| Conformance | **RED on all `-comp*` modes**; `-int`/VM modes GREEN | A (link) + B (codegen) |
| Unit / Perf | confirm not newly red | C |

The conformance `-comp*` red is a single dominant symptom — a link error,
`Undefined symbols: _bn_pkg__bootstrap__Write, referenced from
rt.BoundsFail` — that **masks every compiled-mode cell** (basic subtraction,
const-expr, multi-return, …). It does **not** reproduce on a fresh local
`builder-comp_native_aa64` run (cells pass). `bootstrap.Write` is provided
by the **libc-target** C runtime `runtime/binate_runtime.c` (the baremetal
target instead provides `Write` as a Binate impl); the `-comp*` modes on
darwin/linux are libc-target, so they link `runtime/binate_runtime.c`.

---

## Lane A — Conformance `-comp*` link break: the C runtime isn't linked in CI

**Goal:** every `-comp*` conformance mode green in CI again (no
`bootstrap.Write` link failures).

**Background.** `bn_pkg__bootstrap__Write` is provided by the **libc-target**
C runtime `runtime/binate_runtime.c` (baremetal provides `Write` as a Binate
impl instead). The `-comp*` modes are libc-target, so they link that file.
The CI conformance harness links cell binaries in which that symbol —
referenced from the precompiled `rt.o`'s `BoundsFail` — is undefined, i.e.
**the libc C runtime defining it is not being linked, or the linked runtime
lacks the symbol.** Locally a from-scratch
`builder-comp_native_aa64` run links it fine (cells pass 2/2), so the
divergence is **environment-specific to CI**. This is the same *symptom* as
the 2026-06-05 fix (binate `1285683e`, "Native backends drop
binate_runtime.c") but post-dates it, so it's a new cause.

**Tasks.**
1. Capture the **exact link command** the CI conformance harness runs for a
   failing cell (e.g. `matrix/scalar/sub/8`): which `binate_runtime.c` does
   it reference, and does *that* file define `bn_pkg__bootstrap__Write`?
2. Discriminate the hypotheses:
   - **H1 — BUILDER-runtime skew.** The harness links the BUILDER's
     (`bnc-0.0.7`) bundled runtime, which predates/mismatches
     `bn_pkg__bootstrap__Write`. If so, this **self-heals at the BUILDER
     bump** (Convergence step 5), exactly like the `same` skew. *Check:*
     does `bnc-0.0.7`'s bundled `runtime/binate_runtime.c` define the
     symbol? When was it added to the checkout runtime (`git log -S`)?
   - **H2 — findRuntime divergence.** CI's checkout layout / working
     directory makes `cmd/bnc`'s `findRuntime` resolve a wrong/empty runtime
     path (the `1285683e` class). *Check:* inspect/instrument `findRuntime`
     under the CI directory layout vs local.
   - **H3 — stale `rt.o` / version mismatch.** A cached precompiled `rt.o`
     references a symbol the linked runtime lacks.
3. Fix per the confirmed hypothesis. If **H1**, confirm the bump clears it
   and coordinate with Lane C to record it as expected-pre-bump skew (not a
   true regression). If **H2/H3**, fix the harness / `findRuntime` / cache.
4. Verify: 0 `bootstrap.Write` link failures across `-comp*` (locally where
   reproducible; CI after the fix).

**Files / area:** `conformance/run.sh` + conformance harness lib, `cmd/bnc`
`findRuntime`, `runtime/binate_runtime.c`, `.github/workflows` (conformance
job). **Disjoint from B and C.**

**Done when:** all `-comp*` conformance jobs are green in CI, *modulo*
separately-tracked non-link xfails owned by Lane B.

**Key note:** Lane A's symptom masks every `-comp*` cell, so until it's
fixed CI cannot show Lane B's results — but Lane B works **locally** (the
link error does not reproduce there), so the two lanes run concurrently.

**STATUS 2026-06-10 — FIXED + LANDED (binate `a256c893`).** Root cause was
H2 (findRuntime / cwd), confirmed decisively: the compiled runners built
cells with no `--runtime`, so deep cells (`matrix/*`, nested `regressions/*`)
depended on `findRuntime`'s cwd-relative fallback, which misses in CI (harness
runs from the workspace root, checkout one dir deeper under `binate/`) →
runtime dropped from the link → `undefined bootstrap.Write`/`main`. Hard
evidence: the failing CI job showed 615 flat cells PASS but the whole
`matrix/` tree FAIL (depth-correlated); `bnc-0.0.7`'s runtime is byte-identical
and DOES define the symbol (H1 refuted, no self-heal at bump); a one-variable
(cwd-only) reproduction flipped a deep cell pass→fail and `--runtime` fixed it.
Fix: the 6 libc-target compiled runners now pass explicit
`--runtime "$BINATE_DIR/runtime/binate_runtime.c"` (matches `build_gen1` /
`e2e/*.sh` / `build-*.sh`); VM + baremetal/arm32 runners untouched. Verified
locally: `builder-comp matrix/scalar/sub/8` from a parent cwd (mirroring CI)
passes 2/0 (was 0/2). Follow-up filed (claude-todo): *remove `findRuntime`,
require `--runtime`* — the cleaner end-state now that no caller relies on
auto-resolution.

**CI CONFIRMED 2026-06-10 — Lane A DONE** (conformance run `27261348482` on
`a256c893`): `builder-comp`, `builder-comp-comp`, `builder-comp-comp-comp`,
and `builder-comp_native_aa64` are all **green** — the link break is cleared
on every mode it affected (these were all broadly red with the
`bootstrap.Write`/`main` link error). The residual `-comp*` reds are
unrelated, pre-existing, never-green gaps, NOT this bug and NOT regressions
from the `--runtime` change: `builder-comp_native_x64` now **links and runs**
(`001_hello` prints `hello world`, then the WIP x64-native backend's codegen
crashes — a separate backend-completeness issue), and
`builder-comp_arm32_{baremetal,linux}` are separately tracked. (`-int-int` was
cancelled — a VM mode, unaffected by this fix.)

---

## Lane B — abi multi-return / funcval un-xfail correctness (compiled backends)

**Goal:** confirm the abi multi-return / funcval cells un-xfailed since
`bnc-0.0.7` actually **pass on every compiled backend**, or re-xfail +
track the ones that don't. No silent premature un-xfails.

**Background.** Binate `2a77188c` ("accept func-value multi-return
destructure") **deleted the funcval-multi-return xfails across ALL modes**
after fixing only the **front-end** (type-check acceptance + result-slot
typing). The tracked todo entry ("Destructuring a multi-return
FUNCTION-VALUE call is rejected at type-check") explicitly says the
**backend lowering was UNTESTED**. In CI these cells fail with Lane A's link
error, which masks whether they *also* miscompile. So funcval-multi-return
on compiled backends is currently **unverified**.

**Tasks.**
1. Locally build each compiled backend (`builder-comp` macos-arm64 LLVM,
   `native_aa64`, `native_x64`-darwin) and run
   `conformance/matrix/abi/{multi-return,iface-multi-return,funcval-multi-return,funcval-param}`
   and the `-assign` variants, for `int` / `u16` / `f64`.
2. For each cell that **fails locally with a value miscompile** (not Lane
   A's link error): apply the Bug Discovery Protocol — re-add the correct
   `.xfail.<mode>` + a tracked todo entry with root cause. Do **not** leave
   a real backend gap silently un-xfailed.
3. For cells that **pass locally on all compiled backends:** record that the
   un-xfail was sound (so CI greenness, once Lane A lands, is interpretable).
4. Fold in the already-tracked siblings: the **float32-x64 funcval ABI**
   follow-up (claude-todo "Float-component multi-return … x64 float32",
   `684_cross_pkg_mr_f32` xfailed on native x64) and the funcval-param
   SIGSEGV history — confirm current status; fix or xfail+track.

**Files / area:** `pkg/binate/native` (multi-return pack/collect),
`pkg/binate/ir` (iface/funcval multi-return lowering),
`conformance/matrix/abi/*` + `.xfail` markers,
`conformance/gen-abi-matrix.py`. **Disjoint from A and C.**

**Done when:** every abi multi-return / funcval cell either passes on all
compiled backends OR carries an accurate `.xfail` + tracked todo; no silent
premature un-xfails remain.

**STATUS 2026-06-10 — verified, no code change needed.** Ran all
`conformance/matrix/abi` cells (multi-return / iface-multi-return /
funcval-multi-return / funcval-param + `-assign` variants, int/u16/f64 — 108
cells) on the three locally-runnable compiled backends: **`builder-comp`
(LLVM-aa64) 108/0, `native_aa64` 108/0, `native_x64_darwin` 108/0.** Findings:
- The `2a77188c` funcval-multi-return un-xfail (front-end-only fix, backend
  untested) is **SOUND** — every `funcval-multi-return{,-assign}/{int,u16,f64}/{2..5}`
  cell passes on all three compiled backends.
- `funcval-param` (SIGSEGV history) passes on all three; `684_cross_pkg_mr_f32`
  float32 is **accurately** xfailed on `native_x64_darwin` + `native_x64`
  (still fails under `--check-xpass`; tracked = the x64-float32 todo).
- No silent premature un-xfails on the locally-runnable compiled backends.
- **Follow-up DONE (stale xfail removed):** the 16
  `iface-multi-return{,-assign}/{int,u16}/{2..5}.xfail.builder-comp_native_x64-comp_native_x64`
  markers blamed "native tuple-packing not yet implemented", but that packing
  IS implemented (`x64_iface.bn` `collectMultiReturnTuple`) and the
  identical-codegen `native_x64_darwin` (Mach-O) PASSES every one of these
  cells — **removed** (binate `10798d42`). The ELF mode
  isn't locally runnable here (no qemu), so **CI is the confirmation point**: it
  runs ELF natively on the x86-64 ubuntu runner and will exercise these cells
  once Lane A's `-comp*` link break clears — expect green (treat any failure as
  a real x64-ELF-specific bug to fix, not a re-xfail). arm32 iface-multi-return
  xfails left untouched — different, less-complete backend.

---

## Lane C — Release execution: `same` skew, E2E scripts, bundle verification, docs

**Goal:** get the release mechanics correct and green so the tag can be
cut, and clean up the bundle/skew hygiene.

**Tasks.**
1. **`same` skew / E2E red.** Confirm it self-heals at the BUILDER bump
   (Convergence step 5). The deeper cause is a **latent e2e-script bug**:
   `e2e/print-args.sh` and `e2e/repl.sh` resolve stdlib **current-first**,
   unlike `build-bnc.sh` / the conformance runner which resolve
   **BUILDER-first**; so the e2e gen1 build hits current `std/errors`
   (`same`) under the 0.0.7 BUILDER and fails. Decide + (if chosen) switch
   the e2e scripts to BUILDER-first stdlib ordering so e2e is green on the
   pre-bump tree too. (Tracked: claude-todo "e2e/repl.sh build broken".)
2. **Re-verify the landed carve-out fix end-to-end.** Fresh `make-bundle.sh`
   from current main → run `release-process.md` step-5 carveout against the
   real bundle → expect `42`. (The fix `84818a77` is on main but a
   from-scratch bundle hasn't been re-smoke-tested; the earlier smoke used a
   pre-fix bundle with the lib hand-patched.)
3. **Bundle-completeness audit.** `lang → pkg/binate/buf` was one
   bundled→non-bundled violation; sweep for OTHER tier-0/0b/1/1x packages
   importing tier-2/3 packages (the precursor / first run of the filed
   tier-dependency hygiene-check todo). Fix or file each. (Also note: the
   filed `lang → pkg/bootstrap` removal is a *separate, larger* todo — not a
   release blocker, don't fold it in here.)
4. **Doc fixes.** Correct the inaccurate note (claude-todo.md "e2e/repl.sh
   build broken" entry + any echo in `release-process.md`) that "the four
   binaries don't import `std/errors`" — they do: `bni`/`bnlint` via
   `std/strconv`, `bnc` via `native/common`. The release build is unblocked
   by **BUILDER-first stdlib resolution**, not by absence of the import.
5. **Confirm non-blocking categories.** Perf + Unit not newly red; hygiene
   green (the lone `gen_iface.bn` 533-line warning is pre-existing — an
   optional split, not a blocker).

**Files / area:** `scripts/` (`make-bundle.sh`, `build-*.sh`, `e2e/*.sh`),
`release-process.md`, `explorations/claude-todo.md`, conformance import
whitelist, possibly `scripts/hygiene/`. **Disjoint from A and B.**

**Done when:** e2e green (or confirmed expected-pre-bump and the script
ordering decided); fresh-bundle carveout passes; bundle-completeness
audited; docs corrected.

**STATUS 2026-06-10 — tasks 1–4 done; task 5 surfaced a pre-existing Unit red.**
1. **e2e BUILDER-first**: FIXED + LANDED (binate `c44ab9b7`) — gen1 build
   now resolves stdlib BUILDER-first (matching `build_gen1`). `e2e/repl.sh`
   54/54, `e2e/print-args.sh` 2/2 on the **pre-bump** tree.
2. **Fresh-bundle carveout**: PASS. Built a from-scratch `make-bundle.sh` bundle
   off current main; hello ✓, carveout → `42` ✓ (the landed `84818a77` lang
   carve-out works from a real bundle), bounds-check ✓.
3. **Bundle-completeness audit**: CLEAN. No non-test bundled package imports
   outside the bundled set; `lang → pkg/binate/buf` was the only true violation
   (already fixed). Recorded as the tier-dependency check's first sweep
   (`claude-todo.md`), incl. the `pkg/semihost` (ships under `runtime/`) and
   `pkg/builtins/testing` (test-only, bundled `.bni`) caveats.
4. **Doc fixes**: corrected the "four binaries don't import `std/errors`" note
   (they do); release-process.md had no echo of it.
5. **Perf/Unit/hygiene confirm**: hygiene GREEN (13/13). **Unit RED**, but
   pre-existing and NOT from any release lane: `pkg/binate/{vm,repl}` + `cmd/bni`
   fail to link on `builder-comp_native_aa64` unit mode (undefined
   `_bn_pkg__*___Package` from `RegisterStandardExterns` — the Phase-B `_Package`
   VM-extern feature; reproduces locally; not xfailed), plus a
   `builder-comp-int-int` unit timeout. Perf RED only on `native_x64` (ELF —
   Lane A compiled-link family; native_aa64 Perf passes). Filed in
   `claude-todo.md` with fix direction. **Not a shipped-artifact blocker** (the
   four binaries + bundle build green), but a red CI category for the
   Convergence coordinator to resolve or consciously accept before the tag.

---

## Convergence — cut the release

Owned by one coordinator after the lanes report. Execute
`release-process.md` for `bnc-0.0.8`:

1. **Gate:** Lane A green (CI `-comp*` link clean) *or* confirmed-to-
   self-heal at the bump (Lane A H1); Lane B has no silent un-xfails; Lane C
   bundle/e2e clean.
2. **Step 2:** `VERSION` + `version.bn` `bnc-0.0.8-pre` → `bnc-0.0.8`
   (needs explicit approval to land on main).
3. **Steps 3-4:** tag `bnc-0.0.8`, push, watch `release.yml`; smoke the
   published bundle (hello + the now-fixed **carveout** + a bounds-check).
4. **Steps 5-6:** `BUILDER_VERSION` → `bnc-0.0.8`; `VERSION` →
   `bnc-0.0.9-pre` (one combined commit).
5. **Step 7:** watch post-release CI on the BUILDER-bump commit — this is
   where any H1/`same`-style skew is expected to clear; treat anything that
   does NOT clear as a fix-now bug.

## Concurrency notes

- **A, B, C touch disjoint file sets** and run in parallel.
- **B is not blocked by A:** A's link error masks CI but doesn't reproduce
  locally, so B verifies codegen locally.
- **A's H1 and C's task 1 are the same "pre-bump skew" family**
  (BUILDER-0.0.7 vs current-source): A is the C runtime not linking in the
  conformance harness; C is stdlib resolution in the e2e scripts. Separate
  fixes, but the two workers should share findings — if both turn out to
  self-heal at the BUILDER bump, the Convergence gate relaxes to
  "confirmed-to-self-heal" rather than "green before tag."
- **Convergence** is a single coordinator step, not a lane.
