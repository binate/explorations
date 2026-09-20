# Plan: Track 1 — constant int div/mod → magic-number multiply (+ mul strength reduction)

Implementation plan for Track 1 of `plan-native-codegen-gaps.md` (the fasta/richards
native↔LLVM tracks). Claimed by work-1 (2026-09-20). Covers all three native backends
(aarch64 flagship, then x64, then arm32) — each an independent landing.

## Goal

Native backends synthesize `OP_DIV`/`OP_REM` by a **constant** divisor as a real
hardware divide (aa64 `sdiv`+`msub`, x64 `idiv`, arm32 `sdiv`); LLVM strength-reduces
to a magic-number multiply. fasta's hot line is
`seed[0] = (seed[0] * 3877 + 29573) % 139968` (signed `int`) — a constant multiply,
add, and modulo. Close the instruction-selection gap: emit the Granlund–Montgomery
magic multiply for a constant divisor, and strength-reduce `mul` by 0/1/−1/pow2.

## Verified up front

The magic-number math (Hacker's Delight Ch.10 `magic`/`magicu`) is brute-force
verified in a Python prototype: signed and unsigned, W=32 and W=64, over 200 random
divisors × 400 random dividends + edge cases (INT_MIN, ±1 neighbours, negatives, 0).
Codegen formulas confirmed:
- signed: `q = MULHS(M,n); if d>0&&M<0 q+=n; if d<0&&M>0 q-=n; q>>=s(asr); q += (q>>u(W-1))`
- unsigned: `q = MULHU(M,n); if !add q>>=s(lsr); else t=(n-q)>>u1; q=(t+q)>>u(s-1)`
- REM: `r = n - q*d` (reuses the quotient).

## Design

**Shared** `pkg/binate/native/common/magic_div.bn` (+ test): target-neutral integer
math. `MagicSigned(d int64, width int) -> {M int64; Shift int; AddN bool; SubN bool}`
and `MagicUnsigned(d uint64, width int) -> {M uint64; Shift int; Add bool}`. `width`
is the register width the mulh is taken at. Belongs in `common` (shared by all native
backends; not IR/VM — it is pure instruction-selection support).

**Width choice.** aa64/x64: always compute at W=64 — operands are kept
sign/zero-extended to the host word, so a 64-bit magic divide of the extended value
equals the true sub-word divide; re-narrow after via the existing per-backend
`emitSubWordNarrow`. arm32: W=32 for the int/int32/uint32 path.

**Per-backend emit** keyed on `ins.Args[1].Op == OP_CONST_INT` (value `.IntVal`):
- aa64 (`native/aarch64/aarch64_muldiv.bn`, new; keep `aarch64_ops.bn` under the
  500-line cap): DIV → materialize M, `smulh`/`umulh`, correction, `asr`/`lsr #s`,
  sign-bit `add q,q,q,lsr #(W-1)`. REM → the DIV quotient then `msub r,q,dreg,n`
  (reuses the divisor register). MUL → 0→`mov #0`, 1→`mov`, −1→`neg`, ±pow2→`lsl`(+`neg`).
  Requires new asm encoders `Smulh` (0x9B407C00) / `Umulh` (0x9BC07C00).
- x64 (`native/x64/`): magic via new one-operand `Imul`/`Mul` (0xF7 /5, /4) → RDX high
  word; same correction/shift structure using existing shift ops. REM → quotient + `imul`+`sub`.
- arm32 (`native/arm32/`): 32-bit path only, via existing `Smull`/`Umull` (take rdhi as
  the high word). int64 div stays the `__aeabi_[u]ldivmod` libcall (bigger job; noted).

## Known residual (in scope to note, not to fix here)

There is **no DCE pass**. `OP_REM` by const reuses the divisor register in its `msub`,
so the constant materialization is NOT wasted — fasta's hot case is clean. Bare
`OP_DIV` by const, however, never reads the divisor register, so its `OP_CONST_INT`
still materializes a dead `mov`/`movw+movt`. The full fix is IR-level strength
reduction (a new `OP_MULHS`/`OP_MULHU` IR op so the divisor operand becomes dead and a
DCE pass drops it) — that also shares the reduction with the VM and every backend, but
it is a different, larger change than Track 1's "backend instruction selection" scope.
Documented here; not done in this track unless the user redirects.

## Deferred within the track (surface, don't silently drop)

`madd` fusion (`(x*c)+y → madd`) is a cross-instruction peephole (recognize an `OP_MUL`
feeding an `OP_ADD`), distinct from the self-contained div/mod magic. Not in the first
landing; will be assessed and raised separately rather than bundled.

## Measurement

- **aarch64**: measurable natively on the Apple-Silicon dev host. Build a bundle from
  the worktree (`scripts/make-bundle.sh`), then in the benchmarks repo
  `BINATE_BUNDLE=<dir> scripts/run.sh fasta binate-native binate-llvm c`; compare the
  native/llvm user-CPU ratio before/after (interleaved best-of-N). A change that
  doesn't move the ratio doesn't count.
- **x64 / arm32**: correctness validated via the native conformance modes
  (`builder-comp_native_x64_darwin`, `builder-comp_native_arm32_baremetal`) — which run
  under Rosetta / QEMU on this host — plus the new constant-div/mod conformance test.
  The perf **ratio** is not reliably measurable here (emulated); that limitation will
  be stated at landing rather than a fabricated number.

## Tests

- `magic_div_test.bn`: for many d, compute magic then simulate the mulh+shift formula
  and assert it equals real division over a sampling of n (signed + unsigned, both W).
- Per-backend emit tests (mirror `x64_ops_test.bn` / `arm32_ops_test.bn` /
  `aarch64_ops_test.bn`): assert the magic path emits the expected mnemonics (smulh/umulh,
  no sdiv) for a constant divisor and still emits real divide for a variable divisor.
- New conformance test: constant div/mod correctness across many (n, d), signed and
  unsigned, sub-word and word — runs under all native modes.

## Landing order

1. aarch64 (flagship, measured). 2. x64. 3. arm32. Each independent and cherry-picked
   on its own.
