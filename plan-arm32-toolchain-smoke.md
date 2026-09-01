# Plan: arm32 toolchain smoke tests

Goal: run each toolchain binary — bnc, bni, bnld, bnas, bnfmt, bnlint — as an
**arm32-linux** process under qemu-user, with a minimal "it works on a 32-bit
host" smoke.  This is distinct from the existing arm32 conformance/unit modes,
which cross-compile *target* code from a 64-bit host; here the *tools themselves*
run as 32-bit binaries.

## Mechanism (no Docker)

- Cross-build each tool: `scripts/build-<tool>.sh --target arm32-linux -o <tool>_a32`
  (all six build-*.sh already accept `--target`).
- Run under `qemu-arm` (qemu-user-static), `QEMU_LD_PREFIX=/usr/arm-linux-gnueabihf`
  for the dynamic libs.  (Or build `-static` to drop the sysroot dependency.)
- One e2e script `e2e/arm32-toolchain-smoke.sh`, modeled on
  `e2e/arm32-aeabi-dormant-helpers.sh`: SKIP (exit 0) when qemu / the
  arm-linux-gnueabihf cross-toolchain is absent; auto-discovered by
  `.github/workflows/e2e-tests.yml`.

## Current arm32-buildability (checked 2026-08-31, checker-level)

- ✓ **bni, bnas, bnlint, bnfmt** — checker-clean for arm32; smoke-testable now.
- ✓ **bnc, bnld** — now checker-clean for arm32 too: the link address-width fix
  landed (`b2c68b22b`, "carry 64-bit target addresses in uint64 for ILP32 hosts"),
  so `pkg/binate/link/emit_dynmacho.bn` no longer holds 64-bit Mach-O addresses in
  word-sized `int`, and cmd/bnld — plus cmd/bnc, which embeds the Mach-O emitter /
  self-signer — compile for arm32.  Ready to add to the smoke.

## Per-tool minimal smoke

- baseline (all): `<tool>_a32 --version` — proves the 32-bit binary starts + parses args.
- **bnfmt**: format a tiny `.bn` → stdout matches expected (pure source→source; easiest).
- **bnlint**: lint a clean tiny package → exit 0.
- **bni**: interpret `hello.bn` → prints `hello` (VM, no clang shell-out).
- **bnas**: assemble a tiny `.s` → `.o`; check it's a valid object.
- **bnc**: compile `hello.bn` → run the result → `hello`.  bnc shells out to clang;
  under qemu-user, `execve` of the native clang runs natively, so the LLVM pipeline
  works.  bnc_a32's default emit target is arm32, so the output also runs under qemu.
- **bnld**: link a tiny `.o` set (produced by bnc/bnas in the same script) → run.

## Steps

1. **DONE** — `e2e/arm32-toolchain-smoke.sh` (`ce33e6c79`), covering the four clean
   tools (bni, bnas, bnlint, bnfmt): cross-build + qemu-run `--version` + one real op
   each; each check requires exit 0 AND the expected output.  Validated end-to-end
   under qemu (6/6) and confirmed green on the real ubuntu-x64 CI runner.
2. **DONE** — CI wiring: `.skip.darwin` markers keep the arm32 scripts off the
   (~10x-cost) macOS lane (`ca3a42678`), and a Linux-only, arm32-gated
   `qemu-user-static` + `gcc-arm-linux-gnueabihf` install step turns the smoke on
   (`c8a972232`).  (arm32-aeabi-dormant-helpers still needs qemu-system-arm — a
   separate follow-up.)
3. **NOW UNBLOCKED** — the `link`/bnld address-width bug is fixed (`b2c68b22b`), so
   bnc + bnld compile for arm32.  Add them to the smoke (bnc: compile + run a hello;
   bnld: link a tiny object set) and drop the omission note in the script header.

## Gotchas

- **execve under qemu-user**: bnc→clang (and bnc→bnas/linker) — a native ELF runs
  natively, an arm32 ELF re-enters qemu; confirm the exec path resolves.
- **dynamic vs static**: arm32 tool ELFs link glibc dynamically → need
  `QEMU_LD_PREFIX`; `-static` avoids it if a static arm32 libc is handy.
- **perf**: emulated tools are slow — keep every input tiny.
- The smoke's real early value is the **build** step: "does `cmd/<tool>` compile for
  arm32 at all" is the strongest ILP32 signal, ahead of any run.

## Effort

Script + the four clean tools + CI wiring: DONE.  bnc + bnld are now unblocked
(link fixed `b2c68b22b`) — adding them to the smoke is the one remaining piece.
