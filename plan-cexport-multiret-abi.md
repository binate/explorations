# Plan: `#[c_export]` multi-value-return C-ABI adaptation (both backends, all arches)

Status: in progress (temp-5). Fixes the todo "Inbound multi-VALUE-return
`#[c_export]` with a >2-register tuple is silently miscompiled at the C
boundary" — but the bug is broader than that title, so this plan covers the
whole class.

## The bug

A `#[c_export]` function returning a multi-VALUE tuple presents Binate's
INTERNAL multi-return convention to a C caller, but a conforming C caller uses
the platform's C STRUCT-RETURN ABI (the tuple treated as `struct { … }`). The
two diverge, so the C caller reads garbage / the wrong registers. Verified on
aa64: `(int64,int64,int64)` returns `8501284384 …` instead of `111 222 333`.

Root cause: the internal multi-return convention returns tuples in up to
`NumGpRetRegs` (8 aa64 / 3 x64 / 4 arm32) + FP registers — matching clang's
FIRST-CLASS-AGGREGATE lowering. The C struct-return budget is NARROWER (>16B
LP64 / >4B ILP32 → sret; ≤ that → eightbyte-PACKED registers). Two distinct
divergences result:

1. **sret divergence** — a tuple the internal convention keeps in registers but
   the C ABI returns via a hidden sret buffer (e.g. aa64 `(i64,i64,i64)` = 24B:
   internal x0-x2, C sret via x8). The todo's stated case.
2. **in-register packing divergence** — a ≤cutoff tuple with sub-word fields:
   clang PACKS them per eightbyte (aa64 `(int32,int32)` → x0 = a|b<<32), the
   internal FCA SPREADS them (w0,w1). C reads `7 0` not `7 9`.

Both affect BOTH backends (LLVM codegen + native aa64/x64/arm32) — verified on
aa64 for both backends.

An HFA tuple is NOT divergent: clang returns it in FP registers (aa64 always;
arm32 hard-float), and the internal FP-aware convention already matches (e.g.
aa64 `(f64,f64,f64)` → d0-d2 both ways). So HFA tuples stay a plain alias.

## The fix: adapt at the C-export boundary

The mangled definition and Binate-internal callers KEEP the internal convention
(unchanged — it is a deliberate register-efficient ABI, abi/03; changing it would
break every non-c_export multi-return). Only the C-visible ENTRY adapts to the C
struct-return ABI. Three cases, decided per target:

- **sret** (`cMultiRetNeedsSret`, HFA-aware): the C entry declares the sret
  buffer, calls the register-returning mangled def, and STORES the returned
  first-class aggregate into the buffer.
- **HFA in FP regs** (`cTupleReturnsHfaInFpRegs`): internal FCA already matches
  clang → plain ALIAS, no adaptation.
- **in-register, non-HFA**: the C entry returns clang's eightbyte-coerced form
  (`sysvReturnLLTy` for x64-SSE else `aggCoerceLLTy` — the SAME coercion a single
  named aggregate return uses, which clang agrees with), REINTERPRETED from the
  internal def's return via a shared memory slot (store internal form → load
  coerced form; lossless, same bytes). Triggered only when a field is narrower
  than a register word (`SizeOf < wordBytes`), so the common `(T, @Error)` tuple
  (word-sized fields) stays a clean alias.

### C-ABI return sret predicate (HFA-aware)

`cMultiRetNeedsSret(tuple)` = NOT (HFA on an FP-HFA target: aa64 always, arm32
hard-float) AND `SizeOf > cutoff` (16 LP64 / 4 ILP32). Distinct from
`multiRetNeedsSret` (the internal register-COUNT rule). Verified against clang
asm: 3xf64 rides d0-d2 (aa64), 3xf32 rides s0-s2 (arm32-hard), 24B all-int
sret's, 8B `(i32,i32)` sret's on arm32.

## Empirical ground truth (clang, this host + cross)

| tuple | target | clang C-struct | internal FCA | divergence |
|---|---|---|---|---|
| `(i64,i64,i64)` 24B | aa64 | x8 sret | x0-x2 | sret |
| `(f64,f64,f64)` 24B | aa64 | d0-d2 (HFA) | d0-d2 | none (alias) |
| `(i32,i32)` 8B | aa64 | x0 packed | w0,w1 | in-reg pack |
| `(f32,f32,f32)` 12B | arm32-hard | s0-s2 (HFA) | s0-s2 | none (alias) |
| `(i32,i32)` 8B | arm32-hard | r0 sret | r0,r1 | sret |

## A fourth divergence (x86-64 only): C-register but internal-sret

Discovered while verifying native x64 via Rosetta: on x86-64 the internal
GP-return budget (3 words) is NARROWER than what the C ABI packs into ≤2
eightbytes.  So a tuple of >3 narrow fields ≤16 bytes — e.g. `(int32,int32,int32,
int32)` (16B, 4 words) or `(int16 × 4)` (8B, 4 words) — is INTERNALLY sret'd but
the C ABI returns it IN registers.  This inverts the "internal-sret ⟹ C-sret"
assumption (which holds on aa64/arm32, whose register budgets exceed the C
cutoff).  Handled by a fourth kind, `CRET_ADAPT_COERCE_FROM_SRET`: the entry hands
the body a LOCAL buffer as its sret pointer, then reloads clang's coerced form
from it.  aa64/arm32 never hit it (the classifier is target-aware).

Also: the LLVM coerced-return SSE test must classify eightbytes directly
(`tupleReturnHasSseEightbyteX64` via SysVClassify), NOT via `SysVInSse`, which
excludes the anonymous tuple from the coerced-KIND set — else a `(f32,f32)` tuple
gets the GP `[1 x i64]` form instead of `<2 x float>` and the 2nd float is lost.

## Work items

- [x] LLVM: sret adaptation + in-register packing coercion + COERCE_FROM_SRET +
      SSE-eightbyte coerced return. `emit_cexport_thunk.bn`. Shared classifier in
      `pkg/binate/native/common` (both backends consult it).
      **Runtime-verified on aa64 (host) and x64 (Rosetta).**
- [x] Native aa64: return-adapt trampoline (sret store via X8 / in-register
      repack; stack-arg copy; narrow-reg normalize). `aarch64_cexport_retadapt.bn`.
      **Runtime-verified (sret, coerce, HFA-alias, stack-args, float-fields).**
- [x] Native x64: generalized trampoline (dual sret cursors + arg re-marshal +
      sret store / coerce / coerce-from-sret; SSE + x87 return). `x64_cexport_
      trampoline.bn`. **Runtime-verified via Rosetta (all sub-cases).**
- [ ] Native arm32: generalized trampoline (R0 sret shift + return adapt). Needs
      the soft/hard-float store dispatch (storeMultiReturnTupleFields{,Hard}Arm32).
      **NOT locally runtime-testable — no qemu-arm on this host; CI/qemu only.**
- [ ] Tests: shared-classifier unit tests (all targets — pure, no qemu); native
      encoding unit tests; e2e C-driver across the tuple shapes (aa64/x64 local,
      arm32 in CI).

## Native arm32: divergence fix done; a SEPARATE pre-existing bug found

Native arm32 trampoline landed (generalized from x64: R0 sret shift + return
adapt; soft/hard-float store dispatch via storeMultiReturnTupleFieldsShimArm32).
Verified via Docker (arm32v7 debian + qemu binfmt, hard-float armhf, `-no-pie
-fno-PIC`): SRET-adapt `(int32,int32)` → `7 9`; COERCE `(int16,int16)` → `5 6`,
`(int8×4)` → `1 2 3 4`.  HFA alias `(f64,f64,f64)` → `1 2 3`.

**But a SEPARATE, pre-existing arm32 bug surfaced** — NOT the divergence class,
NOT touched by this work: a LARGE multi-return where BOTH the C ABI and the
internal convention sret (e.g. `(int64,int64,int64)` = 24B, `(int64 × 9)` = 72B)
is miscompiled at the C boundary via the plain ALIAS path (no trampoline).  The
mangled def's disassembly writes the sret buffer at the correct offsets (0,8,16),
yet at runtime only the first ~12 bytes land (`[12]`+ stay 0xAA fill) and the
result is non-deterministic (crash / wrong data), on BOTH backends (native AND
LLVM-arm32).  The SRET-adapt trampoline writes the buffer CORRECTLY (via
storeMultiReturnTupleFieldsShimArm32), so the fault is in the DEF's own
sret-multi-return write — which internal (Binate→Binate) callers of the same def
also use, so this is likely a GENERAL arm32 sret-multi-return codegen bug, not a
c_export-only one.  Could not confirm the internal-call path locally (a full
arm32-linux executable won't cross-link on the macOS host).  Needs its own
investigation; tracked separately from this divergence fix.

## Testability note

Host is aa64.  Runtime-verified locally: LLVM aa64 + native aa64 directly; LLVM
x64 + native x64 via Rosetta (`--target x86_64-darwin`, `arch -x86_64`).  LLVM
arm32 codegen is inspectable (`--target arm32-linux --emit-llvm`) and correct;
its RUNTIME (and native arm32's) is CI-only (qemu, `builder-comp_*arm32*`
modes).
