# Plan: devirtualize injected container policies (mapfn/setfn) — constant-func-arg specialization

Status: **DESIGN NOT VIABLE AS WRITTEN — adversarial review (2026-08-27) found FATAL
FLAWS.** Kept for the record; do NOT implement approach C below without first resolving
the type-system question. See "Review outcome" immediately below.

## Review outcome (2026-08-27): DESIGN-HAS-FATAL-FLAWS

An adversarial design review, grounded in the code, killed the central mechanism:

- **The monomorphizer substitutes TYPES ONLY** (`ensureInstantiated` pushes
  CurrentTypeParamNames/Types, consumed only by `resolveTypeExpr`'s type-name
  rewrite). There is no value-substitution and no field-sensitivity. The constant
  `*func` is STORED in one function (`hash.Fn`, `hash.bni:51`, the sole writer of
  `FnHasher.fn`) and LOADED+CALLED in a DIFFERENT method (`FnHasher.Hash`,
  `hash.bni:56`, the sole caller), separated by a heap-stored `Table.h` field. To
  route `t.h.Hash(k)` to a constant-baking specialization, the compiler must know
  `t.h.fn == funcSigHash` — which IS the interprocedural, field-sensitive func-value
  constant-propagation this doc claimed to avoid. Step 3 of the sketch ("stored into
  a field → mark that field constant-func … downstream specialization devirtualizes")
  is hand-waving over the entire hard problem.
- The only type-keyed way is to make **"FnHasher whose .fn is funcSigHash" a distinct
  TYPE** and thread it as `Table`'s `H` type-argument — i.e. **value-parameterized /
  singleton-function types.** That is a genuine TYPE-SYSTEM EXTENSION (new type
  identity for a func value, mangling, checker consequences), NOT "extend the
  instantiation key." This doc mislabeled the whole feature as a risk bullet.
- **No optimizer shortcut works either.** Inlining the trivial `Fn`/`Hash`/`Equal`
  one-liners still leaves `t.h.fn(k)` loading from a heap-allocated, ESCAPING,
  refcounted `@Table` that outlives every constructor — classic inline + SROA cannot
  scalar-replace it. For a policy embedded in a long-lived heap container it is a
  type-system feature or nothing.
- **The "first cut: direct-call-only" (below) closes 0% of the mapfn/setfn gap** — the
  injected `*func` is NEVER called in the body that receives it (NewMapFn/NewTable
  forward it; `Fn` stores it; only `FnHasher.Hash`/`FnEq.Equal` call it, on a field
  load). So that de-risking increment erases none of the `c087ca69d` regression (it
  would help unrelated generics like `sort(less)` that call the fn in-body).
- Soundness (`fn` set-once, never reassigned, Table never re-homes the FnHasher) and
  the weak_odr dedup argument were judged CORRECT — but moot, since the mechanism
  under them can't be built. (Factual fix: `883f761ce` is the iv-thunk weak_odr fix,
  NOT the instantiation dedup enabler — the `IsLinkOnce` that would cover these
  specializations pre-exists on `ensureInstantiated`.)

**Real options for the user (the doc foreclosed the small ones prematurely):**
1. **Value-parameterized / singleton-function types** — the actual (C). A language
   feature; rewrite the plan around it if pursued.
2. **(A) revisited — a `Hashable`/`Comparable` key wrapper + the intrinsic `Default`
   policy.** `hash.Default[K].Hash(k) = k.Hash()` ALREADY monomorphizes to a DIRECT
   call today (parity). Small, works now. The real trade is "a per-key wrapper type"
   vs. "a language extension" — a user decision, not a hack to dismiss.
3. **Accept the ~3–5% on the one internal consumer** (ir's FuncSigIndex — the sole
   motivation). Cheapest; weigh against option 1's size.

The original approach-C design follows, unchanged, for the record.

---

Status (original): DESIGN (2026-08-27). Author-review pending; adversarial review requested.

## Problem

The stdlib fn-injected containers `mapfn.MapFn[K,V]` / `setfn.SetFn[K]` are backed by
`pkg/std/table.Table[K,V,H,E]` with the injected-function policies
`hash.FnHasher[K]` / `cmp.FnEq[K]`. Those wrap a raw `*func` and dispatch through it:

```
type FnHasher[K any] struct { fn *func(K) uint }
func (h FnHasher[K]) Hash(k K) uint { return h.fn(k) }   // INDIRECT call, every probe
```

So every hash and every equality on a probe is an **indirect call through a stored
`*func` field**. Converting ir's FuncSig index from a hand-rolled open-addressing map
to `mapfn.MapFn` measured **~3–5% slower** on the `bni cmd/bnc` load (commit
`c087ca69d`), entirely attributable to this indirection (the hand-rolled map inlined
djb2 + a direct `streq`).

The stdlib's OWN answer already exists for the intrinsic path:
`hash.Default[K]` (a zero-size policy, `Hash(k) = k.Hash()`) monomorphizes to a
**direct** `k.Hash()` — parity with hand-inlined code. But `Default` needs `K :
lang.Hashable`; our keys (`*[]readonly char`, `int`, raw pointers) are not, and a
`Hashable` wrapper type for them is a hack (rejected). The right fix is to make the
**fn-injected** path reach the same direct-dispatch parity, so nobody ever needs to
hand-roll a table for performance.

## Why it's not a local optimization

The value that must be known to devirtualize `h.fn(k)` — the concrete function
`funcSigHash` — is passed to `NewMapFn(funcSigHash, streq)` and threads through FOUR
generic layers before it is called:

```
NewMapFn[K,V](hashFn, eqFn)          (pkg/std/mapfn)  — forwards hashFn
  → NewTable[K,V,FnHasher[K],FnEq[K]](Fn[K](hashFn), ...)   (pkg/std/table)
    → Fn[K](hashFn) { var h FnHasher[K]; h.fn = hashFn; return h }  (pkg/std/hash) — STORES it in a field
Table.Get/Put/probe → h.Hash(k) → FnHasher[K].Hash → h.fn(k)   — CALLS it, in yet another function
```

The store (`h.fn = hashFn`) and the call (`h.fn(k)`) are in different functions, in
different packages, separated by a heap-stored struct field. Binate has **no function
inliner** (confirmed) and `RunOptPasses` is a small ordered IR-pass sequence
(currently only const-index bounds-check-elim). A purely intra-function pass cannot
see the constant across this chain; a general interprocedural, field-sensitive
func-value constant-propagation + devirtualization pass is a much larger project.

## Approach: constant-func-arg specialization (partial evaluation via the existing monomorphizer)

Binate already monomorphizes generics per **type-argument tuple** in
`ensureInstantiated` (gen_generic.bn:56): each unique instantiation is emitted once,
`IsLinkOnce`, keyed by `instantiationMangledName(d, definingAlias, typeArgs)`, and
deduped across packages via `instantiationAlreadyEmitted`. The proposal extends that
one mechanism to also key on **compile-time-known `*func` arguments**, turning the
threaded runtime function into a compile-time constant that the ordinary code path
then emits as a DIRECT call.

### Trigger (detection)

At a call to a generic function that has a **non-capturing `*func` parameter**, if the
argument is a compile-time-known top-level function reference — in IR terms an
`OP_FUNC_VALUE` naming a top-level func with NO capture data (the same shape
`EmitFuncValue(d.Name, …)` produces; a `func_lit`/closure with data does NOT qualify) —
record the binding `param → <concrete func name>`.

### Specialization key + emission

`ensureInstantiated`'s instantiation identity becomes `(typeArgs, constFuncBindings)`.
`instantiationMangledName` appends the concrete func names (already length-prefixed,
injective) so:
- `NewMapFn[*[]readonly char,int]` called with `(funcSigHash, streq)` and one called
  with `(otherHash, otherEq)` are DISTINCT specializations;
- the SAME `(types, funcs)` from two packages produce the SAME mangled name → the
  `IsLinkOnce` (weak_odr) dedup — just landed as `883f761ce` — merges them, so no
  duplicate-symbol regression.

When emitting a specialized body, a use of the bound `*func` param is rewritten:
- `param(args)` → a DIRECT call to the concrete func (`OP_CALL` to its name), which is
  then a normal direct call the backends emit without indirection.
- `field = param` (the `Fn[K]` store) propagates the binding: the resulting struct
  field is a compile-time-constant func, so a later `load(field)(args)` in a
  further-specialized callee also devirtualizes.

The binding **propagates down the call chain**: `NewMapFn` specialized on `funcSigHash`
calls `NewTable(Fn[K](funcSigHash), …)` — `Fn[K]` is in turn specialized on the
constant, producing a `FnHasher` whose `.fn` is the constant; `FnHasher[K].Hash`
specialized on that field devirtualizes `h.fn(k)` → `funcSigHash(k)`. Each generic
layer that forwards/stores the `*func` gets a specialization threaded with the
constant — this is interprocedural constant propagation IMPLEMENTED as specialization
keyed on the constant, reusing the monomorphizer rather than a new dataflow pass.

### Soundness

- The `*func` is a RAW func (`FnHasher.fn *func(K) uint`) — non-capturing, no
  associated state; a captured `@func` would NOT qualify (out of scope, keeps the
  indirect path).
- `FnHasher.fn` / `FnEq.fn` are set ONCE in `Fn`/`FnEq` and never reassigned; the
  container never mutates them. So the bound constant is stable for the container's
  lifetime — the specialized direct call is behavior-identical to the indirect one.
- The specialized instantiation is byte-identical across packages that specialize on
  the same `(types, funcs)`, so weak_odr merging is sound (same argument as the
  monomorphized-method dedup the compiler already relies on).

### Fallback

If a `*func` argument is NOT a compile-time-known non-capturing top-level func (a
runtime-computed func value, a captured closure, a func stored in a variable of
unknown provenance), no specialization: the current generic instantiation with the
runtime `FnHasher.fn` indirect call is emitted, unchanged. Correctness is never at
stake; only the optimization is opportunistic.

## Alternatives considered (rejected)

- **(A) A `Hashable` wrapper type for char-slices/ints** → use `hashmap.Map` (Default
  policies, direct dispatch). Rejected: a wrapper type per key is a hack; and it only
  helps keys someone bothered to wrap, not the general injected-policy case.
- **General interprocedural func-value constant-prop + devirtualization pass.** The
  "principled" version, but much larger (field-sensitive, alias-aware, whole-module)
  and buys nothing over specialization for the constant-arg case that actually occurs.
- **A general function inliner + local devirtualization.** Also large, and inlining a
  4-layer heap-constructing chain to expose the constant is itself hard.
- **Accept the ~3–5% regression / keep hand-rolled maps.** Rejected by the project
  principle: the stdlib container must reach parity so nobody hand-rolls for perf.

## Implementation sketch

1. IR-gen: at generic-call sites (gen_call.bn / ensureInstantiated's caller), detect
   non-capturing top-level-func `*func` args (OP_FUNC_VALUE, no data) and collect
   `(param-index → func-name)` bindings.
2. gen_generic.bn: extend the instantiation key + `instantiationMangledName` with the
   func bindings; `instantiationAlreadyEmitted` dedups on the extended key.
3. Body emission: when a bound param is (a) called → emit a direct `OP_CALL`; (b)
   stored into a field → mark that field constant-func for the specialized type so a
   downstream specialization of a method reading it devirtualizes its call.
4. Thread the binding through forwarding generics (NewMapFn→NewTable→Fn) so it reaches
   FnHasher/FnEq.
5. Everything downstream (the now-direct call) rides the existing pipeline unchanged.

Scope note: steps 3–4 (propagating a field-stored constant func through a chain of
specializations) are the hard part and the main risk; a first cut could handle only
the DIRECT-call case (param called in the same generic body) and measure how much of
the gap that closes before tackling field-stored propagation.

## Risks / open questions

- **Propagation depth**: the constant must survive 4 layers of forwarding
  specializations. Does the design need each layer's `*func` param recognized as
  "forwarded/stored constant", or can a lighter mechanism (e.g. inlining just the
  trivial `Fn`/`Hash`/`Equal` one-liners at the specialized site) suffice? Prototype
  both.
- **Instantiation-count growth**: specializations multiply by distinct func tuples.
  In practice the same container is built with the same funcs, so growth is small and
  weak_odr dedups cross-package copies — but worth measuring on a full cmd/bnc build.
- **BUILDER-compilability**: the mechanism lives in cmd/bnc's tree; must stay within
  the pinned BUILDER's accepted subset (no new-to-BUILDER feature introduced).
- **Interaction with the interp/VM lowering**: RunOptPasses runs once on the finalized
  module for all backends, but this is a monomorphization-time change, not a
  RunOptPasses pass — confirm the specialized instantiations flow identically to
  LLVM, native, and the VM.
- **Does it generalize** beyond mapfn/setfn to any generic taking a non-capturing
  `*func` (sort comparators, iterator adapters)? It should, and that is the point.

## Test plan

- Correctness: full conformance (the specialized path must produce identical results;
  the fn-injected containers already have coverage; add a test that a mapfn built with
  a top-level func and one built with a runtime func value both behave correctly).
- Perf: re-run the `bni cmd/bnc --version` A/B — the funcSigIndex `mapfn` version
  (`c087ca69d`) should return to parity with (or beat) the pre-conversion hand-rolled
  map. That regression is the concrete benchmark this project must erase.
- Regression guard: a test asserting a specialized instantiation emits a direct call
  (no OP_CALL_FUNC_VALUE / indirect) for a constant-func-arg container.
