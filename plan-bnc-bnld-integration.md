# Plan: bnc → bnld integration (make bnc link with the self-hosted linker)

## Goal

Make `bnc` use `bnld` (the self-hosted linker) for the final link, so the Binate
toolchain links **without the C linker/driver** (clang/ld).  We are shedding the C
*linker/driver*, **not libc**: on a hosted system, linking against libc (and other C
libraries) dynamically is expected and essential — it is the sanctioned "C interface"
(libc/syscalls), not something to reimplement.  bnld already links libc dynamically
(see plan-dynamic-linking.md); this project bootstraps a bnc program into it.

## Ratified decisions (2026-08-31)

- **Delivery: library compiled into bnc (Option B).**  `cmd/bnc` imports `pkg/binate/link`
  and calls `link.LinkDynElf` / `link.LinkDynMacho` directly (no subprocess), behind an
  opt-in flag, target-gated.  Verified cheap: the current BUILDER (bnc-0.0.14) compiles
  `cmd/bnld` (= `pkg/binate/link` + a thin CLI), so the library is already
  BUILDER-compatible — no BUILDER release, no rewrites; its deps (`asm/*`, `buf`,
  `sha256`, `os`) are mostly already in bnc's tree (bnc embeds `asm/assemble`).  This is
  the plan's stated "linker as library" design idea and the foundation for Step 7.
  `cmd/bnld` stays as a thin CLI over the same library.
- **ELF bootstrap: our own `_start` → `__libc_start_main` (Option 1).**  bnc/runtime
  provides a tiny per-arch `_start` (x86-64, aarch64) that reads argc/argv/envp off the
  stack and calls libc's `__libc_start_main(main, argc, argv, 0, 0, rtld_fini,
  stack_end)` — imported dynamically from libc.so.6.  This gives proper libc init (TLS,
  errno, malloc arena, stdio) with **no dependence on system crt objects** (`Scrt1.o` /
  `crti.o` / `crtn.o`) and **no clang**.  Rationale: clang/gcc locate crt objects via
  baked-in sysroot + GCC-install detection + multiarch-triple probing (exposed by
  `clang -print-file-name` / `-print-search-dirs`) — distro-specific "magic" we'd
  otherwise have to reimplement (Option 2, rejected).  `Scrt1.o`'s only job is to call
  `__libc_start_main`; writing our own `_start` skips it, and since a Binate program does
  its own init via `bn_entry` (not C `.init_array`/`_init`), we expect to need no crt
  objects at all (to verify in Step 2).  What remains — libc.so.6 (found via `-L` dirs,
  as ML4's e2e located libm.so.6) + the hardcoded ELF interpreter path — bnld already
  handles.
- **macOS bootstrap: LC_MAIN (already handled).**  dyld/libSystem's start invokes the
  `LC_MAIN` entry *after* libc init, so `main` gets an initialized libc for free — bnld
  already emits `LC_MAIN`.  The hard case is Linux ELF.

## Non-goals

- Avoiding libc itself (it is the sanctioned C interface; bare-metal + direct syscalls
  is a separate, deferred track — arm32).
- Replacing clang for targets bnld does not yet cover (arm32, bare-metal): the flag is
  **opt-in and target-gated** to linux-x64 / linux-aarch64 / macos-arm64; clang stays
  the default and the linker for everything else.

## Step decomposition

1. **PoC — de-risk the bootstrap (no bnc changes).**  Hand-written `_start` →
   `__libc_start_main` + a `main` that exercises libc (malloc, a stdio/`write` call,
   errno), assembled by `bnas`, linked by `bnld -dynamic`, run in Docker (x86-64 +
   aarch64).  Proves Option 1's core assumption (our `_start` reaches a working libc)
   before touching bnc.
2. **Hosted `_start` in the runtime**, per-arch, gated to bnld-link mode (must not
   collide with clang's crt1 `_start`).  Confirm no crt1/crti/crtn/`.init_array` frame
   is needed (bn_entry covers init).
3. **Wire `pkg/binate/link` into `cmd/bnc`** + a linker-choice flag (opt-in, default
   clang), target-gated.
4. **Replace the clang spawn (bnld mode)** with `link.LinkDynElf`/`LinkDynMacho`,
   passing the program objects + runtime, `-dynamic`, the entry.
5. **e2e:** build a real bnc program with the flag, run it (Docker Linux + native
   macOS), check libc works (malloc/stdio/args).
6. **Then Step 7** (interpreted drivers) — the original prompt for this work.

## Companion task (DONE)

- **bnld in the release bundle** (landed `e63d018c9`): `make-bundle.sh` builds bnld into
  the bundle, so the next BUILDER ships it; `fetch-builder.sh --tool bnld` recognized.

## Prerequisites confirmed

- `pkg/binate/link` is BUILDER-compatible (BUILDER compiles `cmd/bnld`).
- bnld links a real bnc program today (e2e/bnld-real-program.sh) — but via a hermetic
  shim (bump allocator + syscall stubs), static; this project replaces that with the
  real libc bootstrap.

## Steps 1–5 LANDED (2026-08-31)

- **Step 1 (PoC):** the `_start` → `__libc_start_main` + dynamic-libc bootstrap proven
  on both ELF arches via bnld (hand-written `.s`, run in Docker, exit 42).
- **Steps 2–5 (`bnc --linker bnld`):** landed `f1590d6ef`.  `cmd/bnc` imports
  `pkg/binate/link` and links directly (no clang); `--linker <clang|bnld>` flag (default
  clang), target-gated to ELF linux-x64/linux-aarch64; an embedded per-arch `_start`
  (assembled in-process) calls libc's `__libc_start_main`.  `e2e/bnc-bnld-linux.sh`
  builds a real program with `--backend native --linker bnld` and runs it (both arches,
  exit 42) — the whole toolchain with no C linker/driver.  Adversarial review SHIP (3
  low-severity findings fixed pre-land: reject `--link-after-objs`, namespace the scratch
  object, clean up the scratch `.s` on all paths).  Confirmed BUILDER-compatible (gen1
  builds cmd/bnc with the link import); this pulls `pkg/binate/link` + `pkg/binate/sha256`
  into the BUILDER-compiled surface (CLAUDE.md updated).

**Remaining:** none of the milestone items — Step 7 (interpreted drivers, the original
goal) LANDED 2026-09-01 as `9e865237d` (`bnld -driver`; design + review in
plan-step7-driver.md).  Optional follow-ups: `--link-after-objs` (extra objects + shared
libs) through the bnld path DONE 2026-09-02 as `c1a9293c5`.  Still open: the Step 7 v1
shortcuts — driver search paths as flags DONE (`dadd3ce9c`); a public driver-API package
so drivers can live in examples/ remains.  macOS/Mach-O via bnld AND the default LLVM
backend + `--linker bnld` were LANDED earlier — see below.

### macOS + bnld — LANDED (2026-09-01, commit 3314904ff)

`linkWithBnld` now has a `macho` branch (`linkMachoWithBnld`) calling `LinkDynMacho` (base
`0x100004000`) with the program's own C `main` (`_main`, from `#[c_export("main")]`) as the
entry.  dyld's `LC_MAIN` runs it after libSystem init — and DOES pass argc/argv/envp
directly, so no `_start` is needed and `os.Args()` works.

It was NOT the "modest addition" first assumed: two fixes were needed to run a real
(absolute-pointer-bearing) program under mandatory arm64-macOS PIE.

1. bnld emitted no `LC_DYLD_INFO` rebase stream, so absolute in-image pointers (vtables,
   type info, descriptor nodes) kept their unslid value after ASLR and faulted.  Added
   rebase-opcode emission (`collectMachoRebaseSites` / `buildMachoRebase` +
   `EmitDynMachoExec` wiring).
2. `parse_macho` classified `__DATA_CONST` (where the codegen puts `rodata_relro` — those
   pointers) as read-only, so bnld placed it in read-only `__TEXT` where dyld can't rebase.
   Fixed to treat `__DATA_CONST` as writable data (→ bnld's writable `__DATA`).  Latent
   before rebase: harmless at a fixed base, fatal once the PIE slides.

Validated by `e2e/bnc-bnld-macos.sh` (compile + link + run natively under dyld + ASLR,
exit 42) plus the buildMachoRebase / `__DATA_CONST` unit tests.  Still host-only: bnc has
no `macos-arm64` `--target` key (only `x86_64-darwin`), so cross-building a macOS Mach-O
from Linux is a follow-up — tracked in claude-todo.md ("bnld (self-hosted linker)").

### LLVM backend + bnld — LANDED (2026-09-01, commits c47a41edb + c397ac14d)

`--linker bnld` with the DEFAULT LLVM backend (not `--backend native`) — clang COMPILES
the object, bnld LINKs it, no ld.  ELF (linux-x64/aarch64) worked with no code change
(verified end-to-end, `e2e/bnc-bnld-llvm-linux.sh`).  macOS needed one fix: clang emits a
section-relative (non-extern) relocation in `__compact_unwind`, which bnld's Mach-O reader
rejects.  `__compact_unwind` (and `__eh_frame`) are intermediate unwind-metadata sections
ld64 consumes into `__unwind_info`; bnld does no unwind processing, so it now DROPS them
(a symbol defined in a dropped section becomes undefined).  The program runs — arm64 keeps
frame-pointer chains — and it is the only section clang gives section-relative relocs
(every data/function pointer uses an extern reloc).  `e2e/bnc-bnld-macos.sh` now covers
both backends.  General section-relative reloc resolution (for a KEPT section, should one
ever need it) is a tracked follow-up in claude-todo.md.
