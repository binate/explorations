# Profiling notes: bnc compiling itself (2026-04-29)

> **Status (2026-04-29, later same day):** Recommendations #1 and the
> out-of-scope `-O2` link bug below have been addressed and the
> workload re-profiled at `-O2`. See
> `notes-profiling-bnc-followup-2026-04-29.md` for the new shape and
> the bottom-line read on remaining work. This file is preserved as
> the original baseline; the recommendations table is updated in the
> follow-up.

> One-shot baseline run. `sample` profiler on macOS, `gen1_bnc`
> compiling `cmd/bnc` → `gen2_bnc`. Goal: identify where bnc
> spends its time before deciding what to optimize.

## Workload

- **Build**: `bnc -g -o gen1_bnc cmd/bnc` (bootstrap interprets
  cmd/bnc to produce gen1_bnc).
- **Profiled run**: `gen1_bnc -o gen2_bnc cmd/bnc`. Wall: ~4.5s.
- **Profiler**: `/usr/bin/sample` at 1ms intervals for 10s
  (process exited at ~3.5s, so ~3520 valid samples).
- **Optimization level: `-O0`** (default). `-O2` and `-Og` both
  break the link with an undefined dtor symbol
  (`_bn_types____dtor_CheckError`), which looks like an LLVM
  optimizer / linkage issue in our codegen — out of scope here,
  worth filing separately. **The bias matters**: at `-O0`,
  refcount/bounds-check/header-access helpers are out-of-line
  and dominate self-time samples; at `-O2` they would mostly
  inline away and a different distribution would surface.

## Time breakdown

Of 3520 wall-clock samples:

- **`__wait4` (libsystem_kernel): 1218 (35%)** — bnc waiting on
  clang invocations. End-to-end build time is ~1/3 spent in
  child clang processes. This means **bnc-self optimization can
  only address ~65% of wall time**; clang invocation strategy
  (one big `clang` link instead of per-package `clang -c`?
  parallelism?) addresses the rest.
- **bnc-internal work: ~2300 samples (~65%)**.

## Top self-time functions (excluding `__wait4`)

| Samples | Function | Self-time of bnc work |
|---:|:---|---:|
| 398 | `bn_rt__RefDec` | ~17% |
| 354 | `bn_rt__BoundsCheck` | ~15% |
| 270 | `bn_rt__headerPtr` | ~12% |
| 221 | `bn_buf__CharBuf__WriteStr` | ~10% |
| 185 | `bn_rt__RefInc` | ~8% |
| 98  | `DYLD-STUB$$bn_buf____copy_CharBuf` (PLT) | ~4% |
| 81  | `bn_buf__grow` | ~3.5% |
| 75  | `DYLD-STUB$$bn_buf____dtor_CharBuf` (PLT) | ~3% |
| 65  | `bn_mangle__StructName` | ~3% |
| 52  | `bn_buf____copy_CharBuf` | ~2% |
| 40  | `bn_buf____dtor_CharBuf` | ~2% |
| 27  | `bn_codegen__charsEqual` | ~1% |

Notable: **none of the linear-scan name-lookup suspects we
guessed** (`scope.Lookup`, `LookupFunc`, `FindStringID`,
`token.Lookup`, etc.) appear in the top of the flat profile.
The dominant cost shape is **temporary `CharBuf` churn**:
`WriteStr` + `grow` + `__copy_CharBuf` + `__dtor_CharBuf` plus
the refcount/bounds/header helpers those operations call into.

## Inclusive-time hotspot: `bn_codegen__discoverStructFromType`

The inclusive call-graph view is strikingly concentrated:

- `bn_main` (at `cmd/bnc/main.bn:132`, the typecheck/codegen
  loop): 1681 samples (48% of total).
- Inside it, **`bn_codegen__discoverStructFromType`: 1234
  samples (35% of total, ~54% of bnc-self work)**.

Drilling down into `discoverStructFromType` → `addStructDef`
(`pkg/codegen/emit_types.bn:138-160`):

```binate
func addStructDef(t @types.Type) {
    if len(t.Name) == 0 { return }
    var mangledName @[]char = mangle.StructName(modulePkgName, t.Name).Bytes()
    for i := 0; i < len(moduleStructDefs); i++ {
        var existingMangled @[]char =
            mangle.StructName(modulePkgName, moduleStructDefs[i].Name).Bytes()
        if charsEqual(mangledName, existingMangled) { return }
    }
    ...
}
```

This **re-mangles every entry's name on every probe** — one
`CharBuf` allocation + write loop + freeze, per slot, per
lookup. Calls go via `mangle.StructName` → `buf.New` →
`WriteStr` repeatedly → `Bytes`/`Freeze`, then `charsEqual`.
For N already-registered structs and M types seen, it's
O(M·N) CharBuf allocations.

The hot stack confirms it: under `discoverStructFromType` the
biggest sub-totals are `WriteStr` (110+54+37 = 201 samples),
`__copy_CharBuf`/`__dtor_CharBuf` cycles, and the refcount
helpers backing them. Refcount/bounds operations are in the
top 3 of self-time mostly because `addStructDef`'s temporary
CharBufs cause them.

## Recommendations

In priority order. Each is independently shippable:

### 1. Fix `addStructDef` to not re-mangle on every probe (high payoff)

Two equivalent fixes; both eliminate the inner allocation:

- **Cache the mangled name on `StructDef`**: add
  `MangledName @[]char` field, set once at registration, use it
  in the comparison. Removes the inner `mangle.StructName(...)
  .Bytes()` call.
- **Look up by `t.Name` (or even by the `@types.Type` pointer)
  instead of mangled name**: `t.Name` is already an `@[]char`;
  comparing by it (or by identity) avoids the mangling
  altogether. Need to verify the rationale for the mangled-name
  comparison in the original code — the comment says "to catch
  both 'Foo' and 'pkg.Foo' mapping to the same `%bn_pkg__Foo`"
  — so we'd want to confirm this case actually arises and isn't
  already prevented upstream.

Either fix likely reclaims 15–25% of bnc wall time on this
workload. The cached-mangled-name version is the safer fix
(preserves current matching semantics).

### 2. Hash table for `moduleStructDefs` lookup (additional payoff after #1)

After #1, the inner per-probe cost is just a `charsEqual` on
prefab strings. Probably a smaller win but worth measuring once
#1 is in. Estimate: ~50 lines of hand-rolled string-keyed hash
table, package-local.

### 3. Profile at `-O2` (gated on the link bug)

At `-O0` the profile is dominated by tiny refcount / bounds /
header helpers that would inline at `-O2`. Re-running at `-O2`
once the link issue is fixed will give a much truer picture of
"real work" vs. "overhead." If the picture changes
qualitatively (e.g., a different function jumps to the top),
we'd reprioritize.

### 4. Build pipeline: collapse per-package clang invocations (~35% of wall)

bnc currently invokes `clang -c -o foo.o foo.ll` per package
plus a final link. Each invocation pays clang startup cost.
Options:

- One invocation: `clang -o out foo.ll bar.ll baz.ll runtime.o`
  — single clang, no per-package overhead. Keeps `.ll`
  intermediates if needed for diagnostics.
- Or parallelize the per-package invocations (currently
  serial).

This is wholly orthogonal to bnc internals. Worth quantifying
end-to-end before committing to it; on small builds the wins
may be modest.

## Out of scope here

- The `-O2` / `-Og` link error (`_bn_types____dtor_CheckError`
  undefined). Looks like a codegen / linkage issue: the dtor is
  emitted somewhere with linkage that allows the optimizer to
  drop it. Worth filing as a separate todo with a small repro.
- Linear-scan name-lookup sites we suspected
  (`scope.Lookup`, `LookupFunc`, `FindStringID`,
  `token.Lookup`): not in the top-N at this workload; revisit
  if/when they surface in a real profile, or after #1 is in
  and the picture redistributes.
- bni profiling (the conformance/VM workload). Different shape;
  separate baseline run.

## Reproducing

```sh
# Build profilable gen1
GEN1_BUILD=$(mktemp -d "/tmp/binate_build_XXXXXX")
(cd /Users/vtl/binate/bootstrap && \
 go run . -root /Users/vtl/binate/binate \
   /Users/vtl/binate/binate/cmd/bnc -- \
   --root /Users/vtl/binate/binate \
   --build-dir "$GEN1_BUILD" \
   -g \
   -o /tmp/binate_prof/gen1_bnc \
   /Users/vtl/binate/binate/cmd/bnc)

# Profile gen1 compiling cmd/bnc
GEN2_BUILD=$(mktemp -d "/tmp/binate_build_XXXXXX")
/tmp/binate_prof/gen1_bnc \
  --root /Users/vtl/binate/binate \
  --build-dir "$GEN2_BUILD" \
  -o /tmp/binate_prof/gen2_bnc \
  /Users/vtl/binate/binate/cmd/bnc &
PID=$!
sample $PID 10 -wait -file /tmp/binate_prof/sample.txt
wait $PID
```
