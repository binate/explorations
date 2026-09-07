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

## Work items

- [x] LLVM: sret adaptation (thunk declares C sret, calls FCA def, stores into
      buffer). Split cUseSret (C-facing) vs intUseSret (internal call) with two
      register cursors so a divergent multi-return + aggregate param classifies
      params against the correct cursor on x64/arm32. `emit_cexport_thunk.bn`.
- [ ] LLVM: in-register packing coercion (reinterpret internal → clang's coerced
      form). Extend `cExportRetNeedsAdapt` + the thunk return handling.
- [ ] Native aa64: sret trampoline (save x8, call sym, store x0.. into [x8]) +
      in-register repack. `aarch64_*`.
- [ ] Native x64: sret + repack trampoline (RDI sret shifts args — arg-unshift
      like the big-agg trampoline). `x64_*`.
- [ ] Native arm32: sret + repack trampoline (R0 sret shifts args). `arm32_*`.
- [ ] Tests: predicate unit tests (all targets); IR-shape unit tests; native
      encoding unit tests; e2e C-driver reading multi-returns by struct across
      the tuple shapes above.

## Testability note

Host is aa64: the LLVM path, native aa64, and the predicate unit tests run here.
Native x64 / arm32 trampolines are verified by unit tests + CI (LLVM x64/arm32
via the e2e; native x64/arm32 via `builder-comp_native_*` conformance).
