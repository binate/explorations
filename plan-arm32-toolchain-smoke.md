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
- ✗ **bnc, bnld** — both blocked on the SAME bug: `pkg/binate/link/emit_dynmacho.bn`
  (:16, :19, :120, plus the dynlink/dynmacho `*_test.bn` fixtures) uses `int` for
  64-bit Mach-O addresses/values → `cannot assign untyped int to int` on ILP32.
  bnc pulls this in because it embeds the Mach-O emitter + self-signer.  This is
  the tracked `link`/bnld address-width item — fixing it (int/uint → int64/uint64
  for addresses) unblocks BOTH bnc and bnld.

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

1. Write `e2e/arm32-toolchain-smoke.sh` (SKIP-guarded), covering the four clean
   tools now (bni, bnas, bnlint, bnfmt): cross-build + qemu-run `--version` + one op.
2. Make it actually RUN in CI (not just SKIP): add `qemu-user-static` +
   `gcc-arm-linux-gnueabihf` to the e2e runner, or a dedicated lane — same setup the
   conformance arm32 modes use.  (Workflow change — a separate decision.)
3. Fix the `link`/bnld address-width bug → unblocks bnc + bnld → add their smokes.

## Gotchas

- **execve under qemu-user**: bnc→clang (and bnc→bnas/linker) — a native ELF runs
  natively, an arm32 ELF re-enters qemu; confirm the exec path resolves.
- **dynamic vs static**: arm32 tool ELFs link glibc dynamically → need
  `QEMU_LD_PREFIX`; `-static` avoids it if a static arm32 libc is handy.
- **perf**: emulated tools are slow — keep every input tiny.
- The smoke's real early value is the **build** step: "does `cmd/<tool>` compile for
  arm32 at all" is the strongest ILP32 signal, ahead of any run.

## Effort

Script + the four clean tools: small (a modeled e2e script).  bnc + bnld gated on
the one link fix.  CI wiring is a workflow change the user owns.
