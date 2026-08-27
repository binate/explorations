# Plan: AEABI soft-float in Binate (Phase 2 of dropping libgcc)

Phase 1 (64-bit integer AEABI helpers, `runtime/baremetal_arm32/aeabi_int.s`)
landed `c0183674c`. This plan covers Phase 2: the soft-float helpers, which are
what actually lets `libgcc.a` be dropped (even int-only programs pull a float
helper via `testing.Println`'s `*any` `float{32,64}.String()` branches).

## Progress (landed on main)

- Phase 1 — 64-bit integer helpers (`aeabi_int.s`): `c0183674c`.
- softfloat skeleton + `d2iz` (`_arm_fixdfsi.o`): `972ac6585`.
- `d2f` (`_arm_truncdfsf2.o`): `587467e10`.
- Double add/sub group (`_arm_addsubdf3.o`: dadd/dsub/drsub/f2d/i2d/l2d/ui2d/ul2d): `4f0176af5`.
- Float add/sub group (`_arm_addsubsf3.o`: fadd/fsub/frsub/i2f/l2f/ui2f/ul2f): `54df54731`.
- Float mul/div group (`_arm_muldivsf3.o`: fmul/fdiv): `70fc2f821`.

Remaining member-groups toward the needed 19: f64 muldiv (`_arm_muldivdf3.o`
dmul/ddiv — the div uses 3 half-width + 1 emulated full-width NR iterations,
no uint128 needed) and compares (`_arm_cmpdf2.o` dcmpeq/dcmplt/…, `_arm_cmpsf2.o`
fcmp*).  Then the remaining single-symbol members for the full set (f2iz, d2uiz,
d2lz, neg/unord, …).

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
- Incremental-link safety — **but the landing unit is a libgcc MEMBER, not a
  single function** (found the hard way, 2026-08-27). Unlike the cleanly
  per-function int members, libgcc BUNDLES float helpers: `_arm_addsubdf3.o`
  defines `dadd dsub drsub f2d i2d l2d ui2d ul2d` all together. If a shim defines
  only `f2d` but the suite also needs `dadd` (unported), libgcc pulls
  `_arm_addsubdf3.o` for `dadd`, which re-defines `f2d` → **duplicate symbol**.
  So to shim ANY symbol from a member without a duplicate, all of that member's
  *needed* symbols must be shimmed (so the member is never pulled). The members
  are DISJOINT, so member-GROUPS still land incrementally (a fully-shimmed member
  isn't pulled; other members stay in libgcc with no conflict).

### libgcc float member -> AEABI symbols (the landing groups)
```
_arm_addsubdf3.o : dadd dsub drsub f2d i2d l2d ui2d ul2d
_arm_muldivdf3.o : dmul ddiv
_arm_cmpdf2.o    : dcmpeq dcmpge dcmpgt dcmple dcmplt        _arm_unorddf2.o: dcmpun
_arm_fixdfsi.o   : d2iz     _arm_fixunsdfsi.o: d2uiz     _arm_truncdfsf2.o: d2f
_arm_negdf2.o    : dneg     _fixdfdi.o: d2lz            _fixunsdfdi.o: d2ulz
_arm_addsubsf3.o : fadd fsub frsub i2f l2f ui2f ul2f
_arm_muldivsf3.o : fmul fdiv
_arm_cmpsf2.o    : fcmpeq fcmpge fcmpgt fcmple fcmplt        _arm_unordsf2.o: fcmpun
_arm_fixsfsi.o   : f2iz     _arm_fixunssfsi.o: f2uiz
_arm_negsf2.o    : fneg     _fixsfdi.o: f2lz            _fixunssfdi.o: f2ulz
```
The single-symbol members (`d2iz`, `d2f`, `f2iz`, `d2lz`, the neg/unord ones …)
are the smallest landable units — start the walking skeleton with one of those,
not `f2d`.  `f2d`/`i2d`/`l2d`/`ui2d`/`ul2d` can only land WITH `dadd` (their
member), so the double-add group is a big single landing.

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

1. **Walking skeleton:** the integration mechanism is PROVEN (softfloat
   force-loads/compiles/links; the shim links + defines `__aeabi_f2d` — the
   duplicate-symbol error was itself proof the shim symbol is present). Redo the
   end-to-end green proof with a SINGLE-symbol member (e.g. `d2iz`), which lands
   with no duplicate. `F32ToF64` is already translated + host-unit-tested (kept
   for the double-add group).
2. **Single-symbol conversion members first** (each lands alone, no duplicate):
   `d2iz d2f f2iz d2uiz f2uiz d2lz f2lz` (+ `dneg/fneg`, `dcmpun/fcmpun` for the
   full set). Simple, and they build up the conversion coverage.
3. **Foundation** (`fp_lib.h` helpers: rounding, `normalize`, 64×64→128 multiply)
   as needed by the arithmetic groups.
4. **Multi-symbol arithmetic/convert groups** (each a single landing): the
   double-add group `_arm_addsubdf3.o` (`dadd dsub drsub f2d i2d l2d ui2d ul2d`),
   float-add `_arm_addsubsf3.o`, muldiv `_arm_muldiv{df,sf}3.o`, compares
   `_arm_cmp{df,sf}2.o`.
5. Each group: translate → host unit tests (differential vs known bits / the VM)
   → shim all the member's needed symbols → native link (that member no longer
   pulled) → land.
6. When the full needed set is covered: drop libgcc from the conformance +
   unittest runners; delete the find-script's libgcc probe. Verify a fully
   C-free native_arm32_baremetal (full suite green, no `--link-after-objs`).

Verification throughout: differential against the VM/interpreter float results
(the conformance suite already pins float output bit-identically across modes),
plus the no-libgcc link.
