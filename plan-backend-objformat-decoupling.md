# Plan: Decouple the Processor Backend from the Object-File Format

Status: **DRAFT** (2026-05-26) — design discussion resolved the four
open questions (see "Resolved decisions" at the end); the slices
themselves are not yet ratified for implementation. Captures the
"the processor backend should be independent of the object file
format" observation and a concrete design for acting on it.

Complements (does not replace):
- [`plan-native-x64.md`](plan-native-x64.md) — building out the
  x86-64 op coverage. That plan picks Linux-ELF as the first runtime
  target and treats macOS-x86-64 as "best-effort / later"; this doc
  argues the Mach-O axis is nearly free *and* unlocks local
  verification, and pins down the one mechanism that plan leaves open
  (symbol-prefix convention).
- [`plan-x64-elf-e2e.md`](plan-x64-elf-e2e.md) — x86-64 ELF
  end-to-end tests on Linux CI.
- [`ir-backend-guidelines.md`](ir-backend-guidelines.md) — the IR /
  backend / layout boundary this work stays within.

## The observation

A native backend today bundles two concerns that are logically
independent:

1. **Processor backend** — IR → machine-code *encoding* for a CPU
   (instruction selection, register allocation, calling convention).
   Lives in `pkg/asm/{x64,aarch64,arm32}` (encoders) and
   `pkg/native/{amd64,arm64}` (IR-lowering drivers).
2. **Object-file format** — serializing the assembled bytes +
   symbols + relocations into a `.o` file (Mach-O, ELF, …). Lives in
   `pkg/asm/{macho,elf}`.

These should be **orthogonal axes**: `{x86-64, aarch64, arm32} ×
{Mach-O, ELF}`, choosable independently, rather than each processor
backend hardcoding one format.

## Why it's worth doing now — the payoff

The immediate, concrete win is **local verifiability of the x86-64
backend on Apple-Silicon dev machines**:

- The amd64 backend currently emits Linux ELF only. On an arm64 Mac
  there is no `qemu-x86_64` and Rosetta runs **Mach-O** x86-64, not
  ELF — so amd64 output **cannot be run here**, only unit-tested at
  the byte level. (This is exactly the wall hit while reviewing the
  `&global` iface fix: the aa64 backend was verifiable end-to-end,
  amd64 was not.)
- If the x86-64 processor backend can emit **Mach-O**, its output
  runs locally via `arch -x86_64` (Rosetta 2). That turns amd64 from
  "unit-test-only" into "end-to-end-verifiable on the same machine
  that develops it" — the single biggest safety improvement for
  building out the amd64 op coverage in `plan-native-x64.md`.

Symmetric future win: arm64 → ELF unlocks arm64-Linux without LLVM,
and shares the format-axis machinery with the arm32 bare-metal/Linux
work.

CI's primary x86-64 target stays Linux-ELF (per `plan-native-x64.md`
decision 2); this doc does not change that. It adds the Mach-O axis
for **local dev verification** and makes the format a real parameter.

## What's already in place (the encouraging part)

The separation mostly exists at the `pkg/asm` layer:

- Encoders fill an abstract `asm.Assembler` — `Section`s, `Symbol`s,
  and *arch-specific* `Fixup`/`Relocation` `Kind`s
  (`pkg/asm.bni`). The encoder does **not** know the object format.
- Both object writers already cover both arches:
  `macho.WriteX86_64` **and** `macho.WriteARM64`; `elf.WriteX86_64`,
  `elf.WriteAArch64`, `elf.WriteARM32`. Each routes through a shared
  `Write(a, …)` parameterized by CPU type / machine.
- x86-64 → Mach-O assembly is already exercised end-to-end at the asm
  layer: `pkg/asm/macho/macho_x64_test.bn` assembles + links + runs
  x86-64 Mach-O with macOS syscall conventions.

So `{x64,aarch64} × {Mach-O,ELF}` is essentially a populated matrix
*below* `pkg/native`.

## The two coupling points (what actually needs work)

### 1. `EmitObject` hardcodes one writer per arch
- `pkg/native/amd64/amd64.bn:EmitObject` → `elf.WriteX86_64(a, path)`.
- `pkg/native/arm64/arm64.bn:EmitObject` → `macho.WriteARM64(a, path)`.

Fix: parameterize the emit path by an **object-format selector**, and
pick the writer from it. The writers already exist; this is the easy
half.

### 2. Symbol-prefix convention leaks into the encoder (the crux)
Mach-O C linkage requires a leading `_` on symbols; ELF does not.
This is the **one and only** genuinely cross-cutting bit (confirmed
by audit — see Resolved decision 3): the leading-underscore decision
is a **property of the target object format**, not of the CPU, and
`symFor`/`globalSymFor` are otherwise *identical* across the two
backends (both go through `mangle.FuncName`/`GlobalName`; arm64
prepends `_`, amd64 doesn't).

The catch is that the prefix is applied at **three** site categories,
two of which currently bypass the central name builder:
1. **Mangled user names** — `symFor` / `globalSymFor`
   (`<arch>_names.bn`). Already centralized.
2. **Runtime-helper references** — `bn_rt__Alloc` / `bn_rt__Box` /
   `bn_rt__BoundsCheck` / `bn_rt__MakeManagedSlice` /
   `bn_rt__RefInc` / `bn_rt__RefDec`. arm64 spells them
   `underscorePrefix("bn_rt__…")`; amd64 uses bare `"bn_rt__…"`
   literals. **These bypass `symFor`.**
3. **Vtable / handle labels** — `__ivt.…` / `__handle.…`, via
   `underscorePrefix` / inline `SetGlobal`.

To make format an independent axis, **all three** categories must
funnel through a single prefix-aware naming helper parameterized by
the format's `symPrefix` — eliminating the scattered
`underscorePrefix(...)` calls (arm64) and bare `bn_rt__…` literals
(amd64) so neither backend hardcodes its format's convention.

**Not** format-divergent at the backend layer (verified — Resolved
decision 3), so *not* extra knobs:
- **Section names** — `pkg/native` uses abstract `"text"`/`"data"`;
  the writers map `text → __TEXT,__text` (Mach-O) / `.text` (ELF).
- **Entry point** — the C entry routes through the normal mangle
  (`bn_entry`, per `pkg/mangle/mangle.bn`), so it rides `symFor`'s
  prefix; not a separate knob.
- **Local labels** (`Lstr_`, `L<fn>__<blk>`) — identical, resolved
  pre-link, never become real symbols.
- **Relocation types / symbol-table ordering** — live in the format
  writers (`pkg/asm/{macho,elf}`), not the backend.

### Not a coupling point: ABI / calling convention
x86-64 uses SysV-AMD64 on both macOS and Linux; aarch64 uses AAPCS64
on both (modulo corners). So calling convention is **arch-determined,
not OS-determined** — no work needed on that axis. (Windows/Win64 is
an explicit non-goal, per `plan-native-x64.md`.)

### To verify, not assume
- That `macho.Write`'s relocation mapping handles the **x86-64**
  fixup kinds (its arm64 path is exercised; the x86-64-into-Mach-O
  reloc types — `X86_64_RELOC_*` — differ and must be covered).
  `macho_x64_test.bn` suggests yes; confirm it covers the reloc
  kinds the IR driver actually emits (PC32 calls, ABS64 data, GOT-ish
  refs if any).
- Mirror check for `elf.Write` + aarch64 fixup kinds (for the future
  arm64→ELF direction; not on the critical path for the x64 win).

## Proposed design

**The object format is not a new axis to invent — it is already a
projection of the target triple** (Resolved decision 1). The triple
(`x86_64-apple-darwin` vs `x86_64-linux-gnu`) already encodes arch +
OS + format, and `cmd/bnc/target.bn` is already the single place that
decodes it (`applyTarget` → `targetTriple`; `nativeArchForTarget()` →
arch string).

**Mirror what the LLVM backend already does.** The LLVM `Backend`
impl just forwards `targetTriple` to clang (`-target`); clang
decomposes it and picks the object format, the `_`-prefix convention,
and section names *for free*. So the LLVM path already has full format
control via the triple — the native path is simply catching up. This
makes the work **parity** ("make the native backend honor the same
triple"), not a new abstraction.

Concretely:

- **Ownership: `cmd/bnc/target.bn`.** Add a `nativeObjFormatForTarget()`
  sibling to `nativeArchForTarget()` that decodes the *same* triple
  into `{ format, symPrefix }` (`*-darwin → { Mach-O, "_" }`,
  `*-linux → { ELF, "" }`). `symPrefix` is a pure function of format,
  so it isn't a separate input.
- **Not `pkg/types`** — format/prefix don't affect layout;
  `TargetInfo` stays sizes/alignment only.
- **`pkg/native` only *receives* it** — `native.EmitObject(mod, arch,
  format, path)` selects the matching writer (`{macho,elf}.Write<arch>`,
  all of which already exist); a `symPrefix` is threaded into the
  single prefix-aware name helper that the three site categories above
  funnel through. `pkg/native/common` at most holds a passive
  descriptor *type* — it does **not** decide the format.
- The `Backend` seam (`cmd/bnc/compile.bn`) is where the two paths
  diverge: the LLVM impl needs nothing new (clang does it); the native
  impl reads the decoded `{format, symPrefix}` and applies them.

Keep `arch` and `format` as separate parameters end-to-end; never
re-derive one from the other inside a backend.

## Verification path (the point of the exercise)

Stand up a **local x86-64 Mach-O run path** on Apple Silicon:
- `bnc --target x86_64-darwin --backend native` → x86-64 Mach-O `.o`.
- Link with the macOS x86-64 toolchain (`cc -arch x86_64 …`, as
  `macho_x64_test.bn` already does).
- Run via Rosetta (`arch -x86_64 ./exe`, or direct exec — Rosetta is
  transparent for `exec`).
- A conformance runner mode (e.g. `…_native_x64_macho…`) that does
  this, used **locally** to exercise the amd64 backend end-to-end.
  Linux-ELF CI stays the canonical gate; the Mach-O runner is the
  dev-loop accelerator. **Local-only — not in CI** (Resolved
  decision 2).

### Testing strategy: cover each axis, not every combination

(Resolved decision 2.) The risk here is a combinatorial explosion of
`{arch × format × …}` CI cells. The rule is to keep CI minimal while
ensuring **(a) every arch and (b) every object format each appear in
at least one CI cell** — not every cell. The existing/planned
*diagonal* already satisfies both axes:

| CI cell | runner | arch | format |
|---|---|---|---|
| `native_aa64` | `macos-latest` (arm64) | aarch64 ✓ | Mach-O ✓ |
| `native_x64` (Linux ELF) | `ubuntu-latest` (x86_64) | x86_64 ✓ | ELF ✓ |

So the off-diagonal cells are local-only / optional:
- **x86_64 + Mach-O** — local dev only (this doc's Rosetta path).
  Not in CI: Intel-macOS runners are scarce, and the encoder is
  already gated by the ELF cell; only the `macho.Write` x86-64 reloc
  mapping (already covered by the asm-level `macho_x64_test.bn`) and
  `_`-prefix application differ.
- **aarch64 + ELF** — not required by the axis rule (ELF is covered by
  x86_64, aarch64 by Mach-O), but *feasible* if extra confidence in
  that specific combination is ever wanted: GitHub now offers
  `ubuntu-24.04-arm` arm64-Linux runners.

## Phasing (small, independently-landable slices)

1. **Symbol-prefix consolidation (no behavior change).** Funnel all
   three name-site categories (mangled names; `bn_rt__…` runtime
   refs; `__ivt.`/`__handle.` labels) through a single prefix-aware
   helper, threading a `symPrefix` value. Seed it with each arch's
   current hardcoded value (`"_"` arm64, `""` amd64) so every existing
   test stays green. This removes the scattered `underscorePrefix(...)`
   calls and bare `bn_rt__…` literals, isolating the cross-cutting
   change from any behavior change.
2. **`EmitObject` format parameter.** Add the format parameter +
   writer selection; default each arch to its current format. Still
   no behavior change.
3. **x86-64 → Mach-O.** Wire `x86_64-darwin` to `{ macho.WriteX86_64,
   "_" }`. Verify the reloc mapping; fix gaps in `macho.Write`'s
   x86-64 reloc handling if any. Land the local Rosetta runner.
   *This is the slice that unblocks local amd64 verification.*
4. **(Later) arm64 → ELF.** Symmetric; wire `aarch64-linux` to
   `{ elf.WriteAArch64, "" }`. Not on the critical path; do it when
   arm64-Linux becomes a goal.

## Non-goals

- Changing the CI gate target (stays Linux-ELF per
  `plan-native-x64.md`).
- Windows/Win64, PIC, AVX — inherited non-goals from
  `plan-native-x64.md`.
- Building out amd64 op coverage — that's `plan-native-x64.md`'s job;
  this doc only makes that work locally verifiable.

## Resolved decisions (2026-05-26 discussion)

1. **Where the format axis lives.** Not `pkg/types` (it's a
   backend/link detail, not layout) and not owned by
   `pkg/native/common`. The format is a **projection of the target
   triple**, owned by `cmd/bnc/target.bn` (a `nativeObjFormatForTarget()`
   beside the existing `nativeArchForTarget()`), and consumed
   per-`Backend` — the LLVM impl gets it for free via clang's
   `-target` handling, the native impl receives `{format, symPrefix}`
   and applies them. `symPrefix` is a pure function of format. This
   mirrors the control the LLVM path already has; "fine, at least for
   now."
2. **Mach-O x64 is local-only, not in CI.** Intel-macOS GitHub
   runners are scarce, and the combinatorial blow-up of
   `{arch × format}` cells isn't worth it. The gate is **axis
   coverage**: every arch tested on CI, every object format tested on
   CI. The existing diagonal (aarch64+Mach-O on macOS, x86_64+ELF on
   ubuntu) already covers both axes. aarch64+ELF on CI is a possible
   future add for extra confidence, not a requirement.
3. **`symPrefix` (leading `_`) is the only format-divergent naming
   knob** — confirmed by audit. It's applied at three site categories
   (mangled names via `symFor`/`globalSymFor`; `bn_rt__…` runtime-helper
   refs; `__ivt.`/`__handle.` labels), two of which currently bypass
   the central builder; the fix funnels all three through one
   prefix-aware helper. Section names, the entry point, local labels,
   reloc types, and symtab ordering are **not** format-divergent at
   the backend layer (abstracted into the writers, or derived from the
   prefix). See "coupling point 2" above.
4. **`pkg/native/amd64` / `pkg/asm/x64` naming stays for now.** The
   `amd64` rename dodged the last-segment mangler collision (see the
   CRITICAL mangler entry in `claude-todo.md`). Revisit / unify the
   `amd64`-vs-`x64` naming with the asm subpackages once the mangler
   is fixed.
