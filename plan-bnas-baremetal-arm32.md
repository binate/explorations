# Plan: assemble the bare-metal arm32 runtime `.s` files with bnas (not clang)

> **Goal.** `runtime/baremetal_arm32/{crt0.s,semihost.s}` are our own
> hand-written ARM assembly, yet the build assembles them with **clang's
> integrated LLVM assembler** (bnc passes the raw `.s` to the clang link
> step). We own bnas and the whole `asm/` stack — assembling our own startup
> files with someone else's assembler is a gap. Convert them to bnas.

## Decision (user, 2026-08-14)

> "Let's start with this [bnc shells out to bnas, mirroring how it invokes
> clang], then aim to make bnas functionality sufficiently modular/embeddable
> so that bnc can just include bnas, essentially."

So: **Phase 1** = bnc invokes a built `bnas` binary as a subprocess to turn each
`.s` runtime file into a `.o`, then clang/lld links the `.o` (clang stays the
*linker*; it is no longer the *assembler* for our files). **Phase 2 (later)** =
factor bnas's core into an embeddable, BUILDER-compilable entry point so bnc can
assemble in-process without a subprocess. Structure Phase-1 code toward that
(one reusable `AssembleFile`-style entry point that both `cmd/bnas/main` and, in
Phase 2, bnc can call).

Rejected for now: in-process from the start (pulls `pkg/binate/asm/parse` into
cmd/bnc's BUILDER-compiled surface before it's been vetted BUILDER-compilable —
that's exactly what Phase 2's "make it embeddable" step is *for*).

## Ground truth (already true on main — do NOT rebuild)

The **encoder + ELF layers are complete**; the native-arm32 backend emits real
relocatable objects through them:

- `pkg/binate/asm/arm32` — every mnemonic these two files use (mov/movw/movt,
  cmp, add/sub(s), and, ldr/str/ldrb/strb with indexed + writeback addressing,
  push/pop register lists, b/bl/bx with condition codes, svc), `Ldr` with a
  label operand → literal-pool `FIX_ABS32`, MOVW/MOVT abs relocs, branch relocs.
  `arm32.ResolveFixups` exists.
- `pkg/binate/asm/elf` — `WriteARM32` (ELF32, EM_ARM, EABI ver5, soft-float),
  `R_ARM_ABS32 / R_ARM_CALL / R_ARM_JUMP24 / R_ARM_MOVW_ABS_NC / R_ARM_MOVT_ABS`.
- `pkg/binate/asm/parse` — arm32 mnemonic dispatch (`parse/arm32*.bn`), cond +
  S-flag split (`splitMnem`), register lists, indexed/writeback addressing;
  `ARCH_ARM32` constant already defined.

## Gaps (all in the bnas front-end / CLI / bnc wiring)

The full instruction/directive surface of the two files:

- **Directives**: `.syntax unified`, `.arm`, `.section .text.startup,"ax"`,
  `.globl`, `.type sym,%function`, `.size sym, . - sym`.
- **Numeric local labels**: `1:` / `2:` … with forward/backward refs
  `1f` / `2f` / `1b` (used heavily by both files' loops).
- **`ldr rX, =symbol`** literal-pool pseudo (`crt0.s`: `ldr sp, =_stack_top`).
- Everything else (mnemonics, cond codes, indexed addressing) already parses.

### Work items

1. **Parser — directives** (`asm/parse/parse.bn`): add `.syntax` (accept
   `unified`; reject/ignore `divided`), `.arm` (set/confirm ARM mode; `.thumb`
   → error for now), `.globl` (alias existing `.global`), `.type sym,%function`,
   `.size sym, expr`. `.type`/`.size` set ELF symbol metadata (STT_FUNC /
   st_size) **iff** asm-core gains symbol type/size fields; asm-core currently
   has binding only (`SetGlobal`/`SetWeak`) — **DECISION TO FLAG**: add
   type/size to asm core (do it right) vs parse-and-ignore for v1 (links fine
   without; STT_FUNC/st_size aren't required for a static lld link). Confirm
   `parseSectionDirective` accepts the `"ax"` flags arg. `.size … , . - sym`
   needs a `.` (current-location) term in the expr evaluator.
2. **Parser — numeric local labels + f/b refs** (`asm/parse/parse.bn`,
   lexer): recognize `N:` (a `TOK_INT` followed by `:`) as a redefinable local
   label; resolve `Nf` to the *next* definition of N (forward ref → fixup), `Nb`
   to the *most recent* definition (GNU-as semantics). Implement by minting a
   unique internal name per definition (e.g. `.Lnnum<N>_<instance>`) and mapping
   each `Nf`/`Nb` reference to the right instance; forward refs ride the existing
   fixup/Finalize machinery.
3. **Parser — `ldr rX, =symbol`**: accept `=` in the ldr operand. The encoder's
   `Ldr(OP_LABEL)` already emits the literal-pool `FIX_ABS32` path — evaluate
   using it vs. rewriting `ldr sp, =_stack_top` in crt0.s as a `movw`/`movt`
   pair (`R_ARM_MOVW_ABS_NC`/`MOVT_ABS`, needs `:lower16:`/`:upper16:` operand
   parsing). We own the file, so a rewrite is on the table if the pool path
   needs `.ltorg`/placement plumbing that's more work than it's worth.
4. **bnas CLI** (`cmd/bnas/main.bn`): `-arch arm32`, arm32 fixup resolution
   (`arm32.ResolveFixups`), ELF output (`elf.WriteARM32(a, out, /*hardFloat*/
   false)`). Factor the "parse+resolve+finalize+write one file" body into a
   reusable entry point (Phase-2 embeddability seed). Keep `aarch64 → Mach-O`
   unchanged.
5. **bnc link wiring** (`cmd/bnc/{target,main,util}.bn`): for a `.s` runtime
   file (currently only arm32-baremetal `crt0.s` + `semihost.s`), invoke `bnas
   -arch arm32 -o <build-dir>/<x>.o <x>.s` and pass the `.o` to the clang link,
   instead of handing the raw `.s` to clang. `.c`/`.o`/other runtime files stay
   on the clang path. Locate bnas via `--bnas <path>` (preferred, explicit) with
   a PATH fallback (`process.Run("bnas", {SearchPath:true})`).
6. **Runner + build wiring**: the arm32-baremetal link sites —
   `conformance/runners/builder-comp_arm32_baremetal.sh`,
   `conformance/runners/builder-comp_native_arm32_baremetal.sh`,
   `scripts/unittest/runners/builder-comp_arm32_baremetal.sh` — build bnas
   (`scripts/build-bnas.sh -o …`) in `runner_setup` and pass `--bnas` to bnc.
   (arm32-**linux** links against libc via clang and uses `binate_runtime.c`,
   not the baremetal `.s` — NOT a site.)

## Sequencing (each commit self-contained + green; arm32 modes stay green

Items 1–4 add bnas capability with bnas **unit tests** and do NOT touch bnc's
link path, so both arm32 conformance modes stay green throughout (bnc still uses
clang for the `.s`). De-risk the flip by first assembling `crt0.s`+`semihost.s`
with the built bnas, linking with clang/lld, and booting under QEMU **by hand**
before item 5. Item 5 + item 6 are the atomic flip (bnc uses bnas ⟺ runners
build bnas) — land together so no mode goes red.

- **C1** parser directives (+ asm-core symbol type/size iff chosen) + tests
- **C2** numeric local labels + f/b refs + tests
- **C3** `ldr =sym` (or movw/movt rewrite of crt0.s) + tests
- **C4** bnas CLI `-arch arm32` + ELF out + reusable entry point + test that
  round-trips crt0.s/semihost.s to a linkable object
- **C5+C6** flip bnc → bnas for `.s` runtime files + runners build bnas (atomic)

## Verification

- bnas unit tests (`scripts/unittest/run.sh builder-comp pkg/binate/asm/parse`,
  `cmd/bnas`).
- Hand E2E before the flip: `bnas` crt0.s/semihost.s → `.o`; clang/lld link with
  `baremetal.ld` + libgcc; boot a hello test under `qemu-system-arm -M virt
  -semihosting`; compare output to the current clang-assembled build.
- Conformance: `builder-comp_arm32_baremetal` (LLVM) **and**
  `builder-comp_native_arm32_baremetal` (native) stay 0-fail across the flip.
- Adversarial review of the encoder-output objects (readelf/objdump the bnas
  `.o` vs the clang `.o`: sections, symbols, relocations) before landing C5/C6.

## Phase 2 (later, per the decision) — embed bnas into bnc

Make the Phase-1 reusable entry point BUILDER-compilable (audit `asm/parse` +
transitive deps for the BUILDER subset) and have bnc call it in-process, dropping
the subprocess + the runners' bnas build. Tracked as a follow-up, not part of
Phase 1.
