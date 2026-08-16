# Plan: make bnas modular + embeddable (bnc assembles `.s` in-process)

> **Goal (user, from the arm32 decision).** "aim to make bnas functionality
> sufficiently modular/embeddable so that bnc can just include bnas, essentially."
> Phase 1 (bnc shells out to a built `bnas` binary via `--bnas`) LANDED — see
> `done/plan-bnas-baremetal-arm32.md`. This is Phase 2: factor bnas's core into a
> reusable library and have bnc call it **in-process**, dropping the subprocess +
> the runners' `build_bnas` + `--bnas` wiring.

## Recon (2026-08-15)

- `cmd/bnas/main.bn` holds the whole assemble pipeline inline in `main()`:
  `asm.New` → `parse.NewParser` → set arch (`-arch` or `.arch`) → `p.ParseFile`
  → per-arch `ResolveFixups` (aarch64/arm32/x64) → `a.Finalize` → per-arch write
  (`macho.WriteARM64` / `elf.WriteARM32` / `elf.WriteX86_64`).
- `pkg/binate/asm/parse` (non-test) imports: `asm`, `asm/{aarch64,arm32,x64}`,
  `buf`, `stringutils`, `os`, `strings`.  **All the arch encoders are already in
  cmd/bnc's BUILDER-compiled tree**; `buf`/`os`/`strings`/`stringutils` are all
  BUILDER-compilable (used across bnc).
- A BUILDER-incompat feature scan (interfaces / generics / closures / floats) of
  `asm/parse` + `asm/macho` found **nothing**.  So the only genuinely-NEW surface
  bnc pulls in by embedding is `asm/parse` + `asm/macho` + the new driver — and
  they look feature-clean.  (Confirm empirically in 2b: the pinned BUILDER bundle
  lags the tree, so the real test is "does gen1 build once bnc imports them.")

## Phase 2a — modular extraction (self-contained; bnas stays full-language)

New package `pkg/binate/asm/assemble`:

- `func AssembleFile(inputPath *[]readonly char, outputPath *[]readonly char, arch int) (bool, @[]char)`
  — the pipeline above.  `arch` is a `parse.ARCH_*` (or `ARCH_UNKNOWN` to take it
  from the file's `.arch`).  Returns `(ok, errMsg)`; **never `os.Exit`** (a library
  must let its caller — bnas CLI or embedded bnc — decide).
- `func ArchFromName(name *[]readonly char) (int, bool)` — "aarch64"/"arm64",
  "arm32"/"arm", "x64"/"x86_64" → `parse.ARCH_*`.

`cmd/bnas/main.bn` becomes a thin wrapper: parse `-o` / `-arch` / input /
`--version`, resolve the arch name, call `AssembleFile`, print `errMsg` + exit on
failure.  Add `assemble`-package tests.  Validate bnas still assembles all three
arches (arm32/x64 end-to-end via docker/qemu, aarch64 object).  One commit.

## Phase 2b — embed into bnc (drop the subprocess)

- Confirm `assemble` + `parse` + `macho` are BUILDER-compilable: import `assemble`
  into cmd/bnc's tree and build gen1.  If the BUILDER bundle chokes on something
  parse uses, that's a BUILDER-bump (or a small shim), surfaced then.
- Replace `assembleDotSViaBnas`'s `process.Run(bnas, …)` (cmd/bnc/main.bn) with an
  in-process `assemble.AssembleFile(sPath, oPath, parse.ARCH_ARM32)` call (arch
  from `nativeArchForTarget`).  Drop the `--bnas` flag, `build_bnas`
  (build-compilers.sh), and the three runners' `--bnas` wiring.
- Validate `builder-comp_arm32_baremetal` (LLVM) + `builder-comp_native_arm32_baremetal`
  stay green; unit tests + hygiene.

**Design note / decision to confirm at 2b:** `parse`'s instruction dispatch
references all three arch parsers, so importing the driver pulls all three
encoders (already in bnc's tree) **plus `macho`** (for the aarch64 writer) into
bnc's BUILDER surface — even though bnc only assembles arm32 today.  Options if
that's unwanted: (i) accept it (small — parse + macho are feature-clean); (ii)
split the driver so the arch set is pluggable and bnc registers only arm32/elf.
Default: (i), pending the empirical gen1 build.

## Status: LANDED (2026-08-16)

Both phases on `main`:

- **2a — `df08aaaf3`** "asm: extract the bnas assemble pipeline into
  pkg/binate/asm/assemble": `AssembleFile(in, out, archName) -> (ok, errMsg)`,
  never `os.Exit`; cmd/bnas is a thin CLI over it.  All three arches assemble
  through the library (arm32 boots, x64 runs, aarch64 object).
- **2b — `c689d2161`** "bnc: assemble .s runtime files in-process (drop the bnas
  subprocess)": bnc calls `assemble.AssembleFile` in-process; the `--bnas` flag,
  `build_bnas`, and the runners' `--bnas` wiring are gone.  BUILDER-compat
  confirmed (gen1 builds with `asm/{assemble,parse,macho}` in cmd/bnc's tree).
  Behavior-neutral: the in-process assembler is the same code the CLI ran, so
  crt0/semihost `.o` are byte-identical.

Validated: `builder-comp_arm32_baremetal` 2905 passed (the 7 failures pre-existing
on main — `dedcf3adf` method-expression + a string-lit-mslice leak — none touch
assembly); native smoke 18/0; QEMU boot with no bnas subprocess.  Minimal
adversarial review: clean (one stale comment fixed pre-land).

**The user's goal is met: bnc includes bnas in-process; no subprocess.**

### Residual / follow-ups (not blocking)

- **Binary bloat + BUILDER surface:** importing `assemble` pulls the full arch
  set (macho/aarch64/x64 writers) into cmd/bnc's link even though bnc-for-arm32
  needs only arm32+elf.  It also expands cmd/bnc's BUILDER-compiled tree to
  include `asm/{parse,assemble,macho}` — CLAUDE.md's "Builder Compatibility
  Constraint" package tree updated to list them (they must now stay
  BUILDER-compilable).  If the bloat matters, split the driver so the arch set is
  pluggable (bnc registers only arm32/elf) — deferred; no consumer needs it yet.
