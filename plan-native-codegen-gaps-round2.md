# Plan: native↔LLVM codegen gap — round 2 (richards / fannkuch, post-track-1 profiling)

Round 1 (`plan-native-codegen-gaps.md`) landed the fasta/richards tracks and moved
richards ~2.0×→1.57× and fannkuch ~1.8×→1.59×. This round re-profiled BOTH on
current main (disassembly + `sample`) to find the *now*-hot residual and propose the
next levers. Several are **shared** (help both benchmarks and array/refcount code
generally), so they're consolidated here rather than split per-benchmark.

The native backend is THE backend; LLVM is the reference. All tracks below are
non-FP (FP-scalar register homes are tracked separately in
`plan-native-fp-register-homes.md`). Known-REFUTED, do NOT propose: raising the
inline threshold (net-negative on native), interval splitting / "home MORE values"
(regressed ~3.5%) — see `claude-todo.md` `### Native codegen quality`.

## Measurement basis
Current-main `bnc`, macOS/arm64, user CPU, interleaved. richards 1.57× (native
7.0s vs llvm 4.45s @ 40000); fannkuch 1.68× (4.35 vs 2.59 @ n=11). Disassembly +
`sample` evidence, 2026-09-20. Both outputs verified correct.

## Findings (where the residual gap lives now)

**richards** — spread across `schedule` + the interface `run` impls + small
`*Scheduler` methods; `schedule` is 1192 bytes native vs 680 LLVM. No single hot
spot — it's several per-operation inefficiencies multiplied across a refcount-heavy
loop. LLVM inlined `suspend`/`hold` away; native keeps them (≈5% of samples) — but
the fix is native codegen quality, not the inline threshold (they're over-size
*because* of the bloat below).

**fannkuch** — 100% in the flip loop (reverse-prefix swap), inlined into `main`.
LLVM 12 instrs/iter with `perm.ptr`/`perm.len` held in x27/x24 across the nest;
native ~29 with the `@[]int` descriptor reloaded per access (4× `.ptr`, 4× `.len`
— 2 of the `.len` loads dead — and 3× descriptor-address recompute per iteration).

## Tracks (ranked by leverage; shared ones noted)

### T1 — Refcount header via `LDUR`/`STUR [ptr,#-16]` (drop the `SUB #16`). richards. Highest value, smallest change.
RefInc/RefDec is the most frequent op in richards, and each pays an extra `sub` +
scratch reg:
```
native RefInc: sub x9,x28,#0x10 / ldr x10,[x9] / tbnz.. / add x10,#1 / str x10,[x9]   (5)
llvm  RefInc:  ldur x8,[x19,#-0x10] / tbnz.. / add x8,#1 / stur x8,[x19,#-0x10]        (4)
```
`aarch64_refcount.bn`'s own comment shows the author avoided a *pre-index writeback*
`[ptr,#-16]!` (which would corrupt `ptrReg`) but overlooked the **non-writeback
unscaled** `LDUR/STUR [ptr,#-16]`, which preserves `ptrReg` for the slow-path
`ZeroRefDestroy` call. Files: `native/aarch64/aarch64_refcount.bn` (RefInc ~:77,
RefDec ~:148) + add `Ldur`/`Stur` unscaled emitters to the aarch64 asm encoder if
absent. Mirror on x64 for parity. Self-contained.

### T2 — Fold constant field-offset GEPs into the load/store memory operand. richards (every field access).
`emitGetFieldPtr` always emits `Add(rd, base, #off)`; the consumer then reads `[rd]`
at offset 0:
```
native: add x7,x27,#0x18 / ldr x6,[x7]        (2)
llvm:   ldr x1,[x22,#0x18]!                    (1, and reuses the address)
```
Model it on the scaled-element GEP fuse that already landed
(`native/common/common_elem_gep_fuse.bn`): a new `FusableFieldGeps` analysis flags a
single-use `OP_GET_FIELD_PTR` whose only use is a load/store address, folds
`FieldOffset` into `[base,#off]`, skips standalone emission, and leaves the GEP
unhomed. `emitScalarLoad` already takes an offset arg. Files: new
`native/common/common_field_gep_fuse.bn`, `native/aarch64/aarch64_emit.bn` (~:468–499),
`native/common/regalloc_*`.

### T3 — Condition / compare-branch lowering: immediates, flag-branch fusion, `ccmp`. SHARED (richards + fannkuch + broad).
Native materializes constants into regs (and, out of registers, **spills the STATE_*
compile-time constants to the stack and reloads them every iteration**), computes a
boolean with `cset`, and branches on it:
```
native: and x7,x5,x9(=const 4 reloaded) / cmp x7,x10(=const 0 reloaded) / cset x4,ne / cbnz x4   (~10 w/ reloads)
llvm:   and x9,x8,#0x4 / cmp x8,#0x2 / ccmp x9,#0,#0,ne / b.eq                                     (4)
```
fannkuch shows the same shape in the loop guard (`cmp;cset x25,lt;cbnz` instead of
`cmp;b.ge`). Lower `OP_ICMP` vs a constant to `cmp/tst #imm` / `cbz` / `cbnz`; fuse
`OP_ICMP`→`OP_BR_COND` to branch on flags (no `cset` boolean); emit `ccmp` for
short-circuit `&&`/`||`. Removes the constant stack-spills as a side effect. Only
`aarch64.Cmp` exists today (`aarch64_ops.bn:156`) — no `Tst`/`Ccmp`/flag-branch
fusion. Files: `native/aarch64/aarch64_ops.bn`, `native/aarch64/aarch64_dispatch.bn`.

### T4 — Alias-precise load-forwarding / LICM: hoist loop-invariant slice descriptors + cross-type fields. SHARED (fannkuch DOMINANT + richards). Backend-neutral. Continues the landed field-forward line — coordinate with its owner.
fannkuch's biggest lever: `perm.ptr`/`perm.len` are loop-invariant (the loop stores
*through* the pointer, never rewrites the descriptor), yet reloaded every access
because a store through `slice.ptr[i]` is treated as possibly aliasing the slice's
own descriptor stack slot — a false dependence (the backing buffer is a distinct heap
object). richards' analog: `s.current` is reloaded across a store to
`s.current.state` because a `@TCB`-derived store isn't proven disjoint from a
`@Scheduler` field. Fix the mod/alias predicate: (a) a store through `slice.ptr`
cannot modify that slice's descriptor slot; (b) a store through `@A` cannot clobber a
live `@B` field (distinct allocation/type/offset). Then existing LICM +
load-forwarding hoist `.ptr`/`.len`/`s.current` to the preheader; also make
redundant-load elimination cross-block within a loop body (GVN-lite) so reloads
collapse pre-hoist. Removes ~11 of fannkuch's ~29 flip-loop instrs/iter. Files:
`iropt/field_forward_analysis.bn`, `iropt/load_forward.bn`, `iropt/field_forward.bn`,
`iropt/licm.bn`.

**Implementation notes (work-2, claimed 2026-09-21; Track 4 STRUCTURAL is complete, so
this is unblocked).** Reconnaissance mapped the two gaps onto the current code:

- *Gap (a) — slice-backing store vs descriptor.* fannkuch's flip loop
  (`for i<j { t=perm[i]; perm[i]=perm[j]; perm[j]=t; i++; j-- }`) has `perm` a
  single-store LOCAL `@[]int`. load_forward already RLEs the descriptor slot and
  `slice_extract_coalesce` collapses the `.ptr`/`.len` extracts — so the descriptor is
  ALREADY one materialized load per function. The remaining reload/hoist block is that a
  store through `GET_ELEM_PTR(perm.ptr, idx)` (the backing buffer) is treated as a
  possible write to the descriptor's own slot, so LICM won't hoist the coalesced
  `.ptr`/`.len` extracts out of the flip loop. The disjointness fact: a slice's backing
  buffer (reached via `GET_ELEM_PTR` off `.ptr`) is a distinct heap object from the slot
  holding the descriptor value. Piece (a) = teach LICM's (and cross-block RLE's) store
  barrier that a `GET_ELEM_PTR`-rooted store does not clobber a slice DESCRIPTOR load.
- *Gap (b) — `@A` store vs live `@B` field.* Extends work-4's access-path predicate in
  `field_forward_analysis.bn` (`storeKillsPath`/`pathsMayAlias`), which today
  conservatively kills on a DIFFERENT-param-root store. Soundness for "distinct" comes
  from distinct POINTEE TYPE (a `@Scheduler` and a `@TCB` name distinct heap objects — no
  reinterpretation in Binate), not merely a different root (two params of the SAME type
  may be the same pointer). Piece (b) = admit disjointness when the two roots have
  distinct pointee types.

Do the pieces as separate commits, each with its own alias-soundness argument + tests +
FULL conformance across ALL backends (backend-neutral IR ⇒ every mode; miscompile risk is
real) + the fannkuch/richards ratio measurement. Piece (a) first (fannkuch's dominant
lever). CRITICAL soundness rule: a wrong disjointness claim is a silent miscompile — every
"X can't alias Y" must be argued from the type system / allocation identity, never from
"the benchmark doesn't happen to alias."

### T5 — Loop-aware BCE via monotonic-induction range facts. fannkuch. Would beat LLVM; synergizes with T4.
Both backends keep two `cmp;b.lo;BoundsFail` per flip-loop iteration. The guard
`i < j` with `j` starting at `k = perm[0] < len` and decreasing while `i` rises from
0 makes both indices provably in `[0,len)`. Extend BCE with monotonic-induction range
facts (not just syntactically-dominating identical checks, which
`iropt/bce_redundant.bn` already handles). Removes 4 instrs/iter AND the block
fragmentation that currently blocks T4's cross-block CSE. Files: `iropt/bce_loop.bn`.
Coordinate with the in-flight BCE work.

### T6 — Native peephole + regalloc polish (grab-bag). fannkuch + richards.
Small, independent cleanups seen in both: (a) delete `ldr R,[m]` when R is redefined
before use (fannkuch: 2 dead `.len` loads/iter); (b) delete branches to the next PC;
(c) phi-copy coalescing so loop-carried inductions stay in place instead of trailing
`mov`s; (d) use `#1` immediate for small-constant add/sub instead of holding it in a
reg; (e) don't home an incoming param that stays in a register, and size leaf frames
to actual spill usage (richards `suspend`/`hold` reserve `#0x60`/`#0xa0` and home the
receiver pointlessly). Files: `native/aarch64/aarch64_emit.bn` (a,b,d peepholes),
`native/common/regalloc_*.bn` (c coalescing, e), `native/common/common.bn` (frame
sizing). **NOTE on (c)/(e):** these are the *opposite* of the refuted "home MORE
values" — coalescing and homing FEWER values — but they are regalloc-adjacent, so
validate against the earlier interval-splitting regression before landing.

## Suggested order
T1 (refcount — highest frequency, smallest, an overlooked form) → T2 (every field
access) → T3 (shared, removes the constant-spill pathology) → T4 (fannkuch's dominant
lever, shared, backend-neutral) → T5 (beats LLVM, synergizes with T4) → T6 (polish).

## Coordination
- T4 continues the field-forward/load-forward line several workers have been landing
  (managed-pointer store-forward, field-load elimination) — coordinate with its owner;
  it's the alias-precision *extension* of that work, not a restart.
- T5 overlaps the in-flight BCE follow-up — coordinate on `iropt/bce_loop.bn` /
  `bce_redundant.bn`.
- T1/T2/T3/T6 touch `native/aarch64/aarch64_{refcount,emit,ops,dispatch}.bn` and
  `native/common/regalloc_*` — file-disjoint enough to parallelize, but T2/T6 both
  touch `aarch64_emit.bn` and T3/T6 the regalloc; sequence or keep changes localized.

## Measurement
Per track: build both backends, `/usr/bin/time -p` user CPU best-of-N interleaved,
compare the native/llvm ratio on the named benchmark(s). A change that doesn't move
the ratio doesn't count.
