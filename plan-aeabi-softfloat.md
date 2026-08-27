# Plan: AEABI soft-float in Binate (Phase 2 of dropping libgcc)

Phase 1 (64-bit integer AEABI helpers, `runtime/baremetal_arm32/aeabi_int.s`)
landed `c0183674c`. This plan covers Phase 2: the soft-float helpers, which are
what actually lets `libgcc.a` be dropped (even int-only programs pull a float
helper via `testing.Println`'s `*any` `float{32,64}.String()` branches).

## Decisions (settled with the user, 2026-08-26)

- **Reference:** translate **LLVM compiler-rt** (`compiler-rt/lib/builtins`) into
  Binate. Non-GPL (Apache-2.0 **with LLVM exception**); the exception waives the
  per-binary attribution (Apache §4(a)/(b)/(d) for object form), so compiled
  Binate programs carry **no** attribution obligation — unlike BSD-3 (Berkeley
  SoftFloat), which was ruled out for exactly that per-binary burden. Small,
  accepted risk that the exception passes to a hand-translation + derived works.
  libgcc (GPL) is off the table on license grounds.
- **Attribution:** each derived Binate file carries a header — "Derived from LLVM
  compiler-rt (Apache-2.0 WITH LLVM-exception); reimplemented in Binate" — plus a
  repo `NOTICE`/`LICENSE` entry. That satisfies the source-form §4(a)/(c)/(d) and
  the §4(b) "state changes" note (the public git history shows the specifics).
- **Scope: the COMPLETE AEABI soft-float set** (for cross-toolchain C interop —
  gcc/libgcc *and* clang/compiler-rt both emit the standard `__aeabi_*` names on
  arm-eabi, so our shims are drop-in for either), not just the 19 the current
  suite needs. Tie-breaker scheduling: **cover the 19 first**; when a compiler-rt
  file mixes needed + not-needed functions, translate the whole file; a file with
  nothing needed is deferred until after the 19.

### The 19 the suite needs first (from a no-libgcc full-suite link)
- arithmetic: `dadd ddiv dmul fadd fmul`
- compare: `dcmpeq dcmplt fcmpeq`
- convert: `f2d d2f d2iz i2d i2f l2d l2f ui2d ui2f ul2d ul2f`

## Integration (validated)

- The soft-float compute lives in a new **Binate** package (bit-manipulation on
  `uint32`/`uint64`; it emits no float ops itself, and reuses the Phase-1 int64
  helpers). Separate `f32_*` / `f64_*` functions rather than generics — the two
  precisions differ in `rep` width and in `wideMultiply` (f64 mul/div need a
  64×64→128 product built in the foundation).
- Binate has no general raw-symbol export (only the `bn_entry`/`bn_init`
  sentinels), so the backend keeps emitting `bl __aeabi_*` (UNCHANGED) and a thin
  **bnas shim** (`runtime/baremetal_arm32/aeabi_float.s`) provides each
  `__aeabi_*` as a tail-call `b <mangled softfloat.fN_op>`. The AAPCS soft-float
  passes a double in `r0:r1` — the same GP pair Binate uses for a `uint64` arg —
  so the shims are near-direct tail-calls with no marshaling. AEABI shim
  conventions (arg pairs, compare return values) come from the **AEABI spec
  (IHI0043)**, not from copying compiler-rt.
- The package is force-loaded for the arm32-baremetal target the same way
  `pkg/builtins/rt` / `pkg/builtins/lang` are (`appendRtImport`-style hook in
  `cmd/bnc/compile_imports.bn`), so it compiles + links though nothing imports it.
- Incremental-link safety (as in Phase 1): the shim object resolves `__aeabi_*`
  before the libgcc archive, so functions land incrementally and libgcc is
  removed from the runners only once the full set is covered.

## compiler-rt → Binate file map (foundation first)

- `fp_lib.h` → the foundation: `rep`/`srep` bit ops, `toRep`/`fromRep` (=
  `bit_cast`), the significand/exponent constants per precision, `normalize`,
  `wideMultiply` (incl. 64×64→128 for f64), `wideRightShiftWithSticky` (rounding).
- `fp_add_impl.inc` → `f{32,64}_add` / `_sub` (`adddf3.c`/`addsf3.c` + sub).
- `fp_mul_impl.inc` → `f{32,64}_mul` (`muldf3.c`/`mulsf3.c`).
- `fp_div_impl.inc` → `f{32,64}_div` (`divdf3.c`/`divsf3.c`).
- `fp_extend_impl.inc` → `f2d` (`extendsfdf2.c`); `fp_trunc_impl.inc` → `d2f`.
- `fp_fixint_impl.inc` → `d2iz`/`f2iz` + the long/unsigned variants.
- `floatsidf.c` etc. → `i2d`/`i2f`/`l2d`/`l2f`/`ui2*`/`ul2*`.
- `comparedf2.c`/`comparesf2.c` (+ AEABI 0/1 adapters) → the compares.

## Order of work

1. **Walking skeleton:** one real function (`f2d` — exact, simple) end-to-end
   through the package → force-load → shim → native link → qemu, to prove the
   integration before bulk translation.
2. **Foundation** (`fp_lib.h` helpers, incl. 64×64→128 multiply).
3. Conversions (the 11 needed) → compares (3) → arithmetic (5) — the 19.
4. The remainder of the complete AEABI set.
5. Drop libgcc from the conformance + unittest runners; delete the find-script's
   libgcc probe. Verify a fully C-free native_arm32_baremetal (full suite green,
   no `--link-after-objs`).

Verification throughout: differential against the VM/interpreter float results
(the conformance suite already pins float output bit-identically across modes),
plus the no-libgcc link.
