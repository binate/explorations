# Plan: `pkg/stdx/containers` — standard container libraries

**Status:** all three containers landed (2026-07-06). `vec` **(main `67b2bff3`)** →
`hashmap` **(main `969e69fc`)** → `set` **(main `7ea33056`)**. Remaining work is the
§7 deferred items only (a `pkg/stdx/iter` closure-adapter layer, the `Iterator`/
`Iterable` interfaces once methods-on-generic-types lands, more containers) — none
started.

## 1. Overview and motivation

Binate has no built-in growable collections (no `append`, no maps) — by design, they
are library concerns. The only generic containers that exist today are the
*teaching* examples in `examples/generics/pkg/` (`Vec`, `Map`, `Sort`); their headers
say outright they "belong in the standard library." They are deliberately simple
(no removal, no iteration, insertion-sort), and are **not** meant to be serious code.

This plan introduces the first *real* container library, as a set of generic
packages under **`pkg/stdx/containers/`**:

- `pkg/stdx/containers/vec` — `Vec[T any]`, a growable vector.
- `pkg/stdx/containers/hashmap` — `Map[K lang.Hashable, V any]`, open-addressing hash map.
- `pkg/stdx/containers/set` — `Set[T lang.Hashable]`, hash set.

We use the `examples/generics` containers as *inspiration only* and feel free to
diverge substantially (adding `Pop`/`Remove`, cursors, backward-shift deletion, etc.).

### Why `stdx` (not `std`)

`stdx` is the tier-1x "standards-track, bundled, **no inter-version compatibility
guarantee**" layer (see `pkg-layout-spec.md`). Container *shape* is going to churn —
in particular, the ergonomic iteration story (below) is blocked on a language feature
that hasn't landed yet. Putting these in `stdx` lets the API evolve without breaking a
stability promise. Promotion to `pkg/std/containers/…` is a later, deliberate move
(a rename, since the nested layout already mirrors the eventual `std` path).

### The two hard language constraints that shape everything

1. **No methods on generic types** (`gen.no-generic-methods`, spec §12.1; `MethodDecl`
   has no `TypeParams` slot). A generic type such as a `Vec[T]` cursor *cannot* have a
   `Next` method, therefore cannot `impl` any interface. So the "one `Iterator[T]`
   interface every container implements" (Rust/Java shape) is **not expressible** for
   generic containers today. All operations are **free functions** taking the container
   as the first argument.
2. **No associated types / no higher-kinded types** (`claude-discussion-detailed-notes.md`).
   An `Iterable` interface could not abstractly "produce *some* iterator type."

Consequently the container operations are free functions, and iteration is a
**convention** (a cursor struct + `Iter`/`Next` free functions), not a type-checked
interface — see §3.

### Design approach: "Vision 1" (zero-cost, forward-compatible)

Iteration is provided two ways, both monomorphic and allocation-free:

- **Slice views** for contiguous containers (`vec.Items` → `@[]T`), iterated with the
  built-in `for x in s` statement (which ranges slices/arrays only).
- **Concrete cursor structs** (`Cursor[…]`) with `Iter`/`Next` free functions, for a
  uniform pull-based iteration shape that works for non-contiguous containers too.

We are **not** building a boxed closure-based iterator (`@func() (T, bool)`) or a lazy
adapter library ("Vision 2") now. That remains a possible future `pkg/stdx/iter`, once
we decide we want lazy composition; it does not require rewriting these containers.

## 2. Package layout and file structure

Each container is a header-only generic package (generic bodies must live in the `.bni`
because Binate monomorphizes at the call site):

```
ifaces/stdlib/pkg/stdx/containers/vec.bni        # package "pkg/stdx/containers/vec"; all generic bodies inline
impls/stdlib/pkg/stdx/containers/vec/vec.bn      # same package; intentionally empty beyond `package` decl + comment
impls/stdlib/pkg/stdx/containers/vec/vec_test.bn # unit tests (pkg/builtins/testing)
```
…and likewise `hashmap/` and `set/`. Two precedents to follow:
- **Header-only shape** (bodies-in-`.bni`, empty `.bn` stub, test harness) follows
  `pkg/stdx/slices` (`ifaces/stdlib/pkg/stdx/slices.bni` + `impls/stdlib/pkg/stdx/slices/`).
- **Nested-package shape** (a `.bni` sitting inside a subdirectory rather than flat)
  follows `pkg/std/math/big` (`ifaces/stdlib/pkg/std/math/big.bni` +
  `impls/stdlib/pkg/std/math/big/`), which proves a nested `.bni` package loads.
  (`pkg/stdx/slices` itself is *flat* — it is not the nesting model.)

Tests import `pkg/builtins/testing`, define `func TestXxx() testing.TestResult`, and
return `""` on pass / a message on failure (matching
`impls/stdlib/pkg/stdx/slices/slices_test.bn`).

Every top-level `func`/`type`/`const` in the `.bni` needs a doc comment (`bni-doc.sh`
hygiene) — expected for bodies-in-`.bni` packages; not a length concern (the `.bni`
cap is warn > 1500 / error > 1800 lines, far above the few-hundred these will run).

Generics carry **explicit** type arguments at every call site (no inference):
`vec.Push[int](v, x)`, `hashmap.Get[@[]char, int](m, k)`.

## 3. Forward-compatible iteration pattern (the crux)

We ship iteration ops as **free functions today whose signatures map 1:1 onto the
methods they will become** when methods-on-generic-types lands, so the migration is
purely mechanical. Every container uses the same shape:

```
type Cursor[T any] struct { v @Vec[T]; i int }         // owning ref keeps the container alive
func Iter[T any](v @Vec[T]) Cursor[T]                    // construct a cursor
func Next[T any](it *Cursor[T]) (T, bool)                // advance; (elem, true) or (zero, false)
```
Usage today:
```
var it vec.Cursor[int] = vec.Iter[int](v)
for {
    x, ok := vec.Next[int](&it)
    if !ok { break }
    use(x)
}
```
When `gen.no-generic-methods` is relaxed (and generic-receiver `impl` is allowed),
this becomes — mechanically — the "natural" shape:
```
interface Iterator[T any] { Next() (T, bool) }           // lives in a future pkg/stdx/containers/iter
func (it *Cursor[T]) Next() (T, bool) { … }              // free fn → method (drop first arg)
impl *Cursor[T] : Iterator[T]                            // cursor now satisfies the interface
func (v @Vec[T]) Iter() Cursor[T] { … }                  // free fn → method
```
`Iter`/`Next` keep their names; call sites go from `vec.Next[int](&it)` to `it.Next()`;
we add two `impl` lines. Nothing structural changes.

**Breadcrumbs (per §3, `3.i` decision):** we do **not** declare the `Iterator[T]` /
`Iterable[T]` interfaces now (they cannot be implemented by generic types yet, so an
un-implementable interface would add nothing). Instead each `.bni` carries a short
comment near the cursor pointing at this section, so the intended future shape is
recorded in the tree.

**Hard cross-container invariant (so the eventual `impl` sweep is truly mechanical):**
until interfaces exist, only *comments + reviewer discipline* enforce the shape, so it
must be identical across containers. Every container ships a type named `Cursor[…]`, an
`Iter[…](c) Cursor[…]` constructor, and an `Next[…](it *Cursor[…]) (elem…, bool)` with a
**pointer** receiver and the `bool` **last**. No container may deviate (no value
receiver, no `(bool, elem)` order, no alternate name). A second container drifting here
is a review-blocking defect, not a style nit.

Notes:
- Cursors hold an **owning** `@Container` reference (the container stays alive for the
  cursor's lifetime) plus a position.
- `Next` takes a **pointer** receiver (`*Cursor`) — it mutates the cursor — matching the
  future `func (it *Cursor[T]) Next()`.
- `Next` returns a **copy** of the element (a retain for managed `T`), consistent with
  the language's `for x in @[]T` (retain-on-bind) semantics.
- **Cursor invalidation:** structural mutation during iteration (a growing `Push`, any
  `Remove`) invalidates a live cursor — documented as a precondition (as in Go/Rust).
  The one benign exception: `vec` cursors re-read `v.data[i]` each `Next`, so a
  *non-shrinking* `Push` that reallocates does not dangle the cursor.

Regarding a boxed `Iterable[T]` interface later: because there are no associated types,
a future `Iterable[T]` would have to return the **boxed** `@Iterator[T]`
(`Iter() @Iterator[T]`), i.e. dynamic dispatch; the concrete `Iter() Cursor[T]`
above stays the zero-cost path. The breadcrumb notes this.

## 4. Container designs

### 4.1 `vec` — `Vec[T any]`

```
type Vec[T any] struct { data @[]T; n int }              # len(data) is capacity; n ≤ len(data) is live count
func New[T any]() @Vec[T]                                 # data = make_slice(T, 0), n = 0 (no backing until first Push)
func Push[T any](v @Vec[T], x T)                          # amortized O(1); grows 0→4→8→16… (double)
func Pop[T any](v @Vec[T]) (T, bool)                      # (zero, false) if empty; else n--, take data[n], ZERO the slot
func Get[T any](v @Vec[T], i int) T                       # bounds are the caller's responsibility (as elsewhere)
func Set[T any](v @Vec[T], i int, x T)
func Len[T any](v @Vec[T]) int
func Cap[T any](v @Vec[T]) int                            # len(v.data)
func Items[T any](v @Vec[T]) @[]T                         # live view v.data[0:n]; see invalidation note
// cursor (see §3)
type Cursor[T any] struct { v @Vec[T]; i int }
func Iter[T any](v @Vec[T]) Cursor[T]
func Next[T any](it *Cursor[T]) (T, bool)
```
- **Growth:** when `n == len(data)`, allocate `make_slice(T, newCap)` (`newCap = 4` if
  currently 0, else `len(data) * 2`), copy the `n` live elements, replace `data`.
- **`Pop` releases promptly:** after taking `x = data[n]` (a retain), assign
  `var z T; data[n] = z` — for managed `T` the assignment releases the vacated slot's
  reference, so ownership transfers cleanly to the caller with no lingering reference
  and no leak. (Just decrementing `n` would keep the popped element alive until the
  slot is overwritten or the Vec is freed — correct but not prompt.)
- **`Items` aliases the backing:** returns `@[]T` = `data[0:n]`, sharing (and retaining)
  the current backing. Valid until the next growing `Push` reallocates `data`, after
  which the returned slice refers to the old backing (stale content, not a dangling
  pointer). Idiomatic contiguous iteration: `for x in vec.Items[int](v) { … }`.

### 4.2 `hashmap` — `Map[K lang.Hashable, V any]`

Open addressing, linear probing, power-of-two capacity (`mask = cap - 1`), three
parallel managed-slices (as the example), but with **removal**.

```
type Map[K lang.Hashable, V any] struct {
    keys  @[]K
    vals  @[]V
    used  @[]bool
    count int
    cap   int
}
func New[K lang.Hashable, V any]() @Map[K, V]             # cap = 8, three make_slice's, count = 0
func Put[K lang.Hashable, V any](m @Map[K, V], key K, val V)   # insert/overwrite; grows before load exceeds 75%
func Get[K lang.Hashable, V any](m @Map[K, V], key K) (V, bool)
func Has[K lang.Hashable, V any](m @Map[K, V], key K) bool
func Remove[K lang.Hashable, V any](m @Map[K, V], key K) bool  # backward-shift deletion; true if a key was removed
func Len[K lang.Hashable, V any](m @Map[K, V]) int
// cursor over (K, V)
type Cursor[K lang.Hashable, V any] struct { m @Map[K, V]; i int }
func Iter[K lang.Hashable, V any](m @Map[K, V]) Cursor[K, V]
func Next[K lang.Hashable, V any](it *Cursor[K, V]) (K, V, bool)   # advance to next used slot
```
- **Slot probe:** `i = key.Hash() & mask`; while `used[i]`, if
  `key.Compare(keys[i]) == 0` it's a hit; else `i = (i + 1) & mask`. `Hashable` extends
  `Comparable`, so equality is `Compare() == 0` (no separate constraint) — reachable
  because the key type is statically known through the `[K lang.Hashable]` constraint
  (constraint calls lower to direct calls, not vtable dispatch; `Self`-object-safety is
  irrelevant here).
- **Growth:** `Put` grows (double `cap`, reallocate all three slices, re-insert every
  used entry) *before* load would exceed 75%: `if (count + 1) * 4 > cap * 3 { grow() }`.
- **Removal — backward-shift deletion (no tombstones), canonical Knuth 6.4 Alg. R:**
  find the key's slot `i` (the gap); if absent return `false`. Then walk forward filling
  the gap: `j = i`; loop `j = (j + 1) & mask`; **stop at the first empty slot**; for each
  occupied `j`, compute its home slot `h = keys[j].Hash() & mask`, and
  **move `j`→`i` iff `h` is NOT cyclically in `(i, j]`** — then set `i = j` and continue;
  otherwise leave the entry and keep scanning. The cyclic membership test (all mod
  `cap`, `i` = gap, `j` = candidate):
  - non-wrapped (`i < j`): `h ∈ (i, j]` ⟺ `i < h && h <= j`;
  - wrapped (`i > j`): `h ∈ (i, j]` ⟺ `h > i || h <= j`;
  move when that membership is **false**.
  Finally mark the last vacated slot `i` unused and **zero its `keys`/`vals` entries**
  (releasing managed references); decrement `count`.

  > Anchor the interval on the **gap `i` and candidate `j`, testing `h`'s membership** —
  > *not* on `h` testing `i`. The two are not equivalent: e.g. `cap=8`, gap `i=6`,
  > candidate `j=1`, `h=7` — the entry at `j` has home 7 (chain 7,0,1), so it must NOT
  > move back past the gap at 6; the correct test (`h ∈ (6,1] = {7,0,1}` → true → keep)
  > gets this right, the mis-anchored form gets it wrong and corrupts the chain.

  This avoids the tombstone accumulation / probe-length degradation of the naive
  mark-deleted approach — the right choice for a serious container. Tests **must** cover
  probe-chain wraparound at the `cap` boundary, mid-chain deletion, and
  delete-then-reinsert.
- **Refcount care (relies on acquire-before-release element assignment):** a store into
  a `@[]T` element acquires the new occupant *before* releasing the old, so each move
  `keys[i] = keys[j]` / `vals[i] = vals[j]` is safe even while an entry transiently
  lives in two slots; across a shift chain, each successive move's release-old half
  frees the slot it overwrites, and the final-gap zeroing frees the last slot — so every
  slot is released exactly once (no leak, no double-free, no un-released slot). No
  intermediate `keys[j]`/`vals[j]` zeroing is needed between moves. This is the subtle
  part and a primary test target (managed `K` **and** `V`).
- **`Put` overwrite (pin the hit path):** on a key *hit*, `Put` writes **only** `vals[i]`
  (release old value, retain new) and leaves `keys[i]` **untouched** — the key is already
  stored and equal, so re-storing it would churn key refcounts for no reason. Only the
  *insert* (miss) path writes `keys[i]` (and `used[i] = true`, `count++`).
- **Cursor:** walks slots `0..cap-1`, skipping `!used`. `Next` advances `i` to the next
  used slot and returns `(keys[i], vals[i], true)`; `(zero, zero, false)` at the end.

### 4.3 `set` — `Set[T lang.Hashable]`

A standalone hash set (keys + `used`, no value slice), reusing the same
open-addressing + backward-shift machinery as `hashmap` minus values.

```
type Set[T lang.Hashable] struct { keys @[]T; used @[]bool; count int; cap int }
func New[T lang.Hashable]() @Set[T]
func Add[T lang.Hashable](s @Set[T], x T) bool           # true if newly added (false if already present)
func Has[T lang.Hashable](s @Set[T], x T) bool
func Remove[T lang.Hashable](s @Set[T], x T) bool
func Len[T lang.Hashable](s @Set[T]) int
type Cursor[T lang.Hashable] struct { s @Set[T]; i int }
func Iter[T lang.Hashable](s @Set[T]) Cursor[T]
func Next[T lang.Hashable](it *Cursor[T]) (T, bool)
```
- **Standalone vs wrapping `Map[T, bool]`:** standalone is leaner (no wasted value
  slice, no zero-size-value edge cases). The cost is duplicating the probe/grow/remove
  logic with `hashmap`. Factoring a shared generic open-addressing core is a plausible
  later refactor; for progressive delivery `set` is self-contained. (Flagging the
  duplication honestly rather than pretending it's free.)

## 5. Testing strategy

Per container, unit tests in `impls/stdlib/pkg/stdx/containers/<c>/<c>_test.bn`:

- **Functional:** every operation on `int`/`uint` (primitive) instantiations — basic
  put/get/has/remove/len; push/pop/get/set/len/cap; add/has/remove/len.
- **Resize:** push past capacity; map/set past the 75% load factor — all prior entries
  preserved and still findable.
- **Removal edge cases (hashmap/set):** probe-chain wraparound at the `cap` boundary,
  deletion in the middle of a probe chain, delete-then-reinsert, remove-all.
- **Cursor:** empty / single / multi; iteration after a growth; each element visited
  exactly once (for maps/sets, order-independent membership check).
- **Managed element types:** instantiate with a managed key/value/element (e.g.
  `@[]char` keys, `@Foo` values) to exercise the retain/release paths in `Put`
  overwrite, `Pop` zeroing, and backward-shift moves. Whether we can assert a hard
  *leak count* depends on the runtime exposing a live-allocation counter — to confirm
  during the `vec` step; at minimum these tests exercise the managed paths for
  crashes/UAF under the memory-checked modes.

Cross-mode: these are ordinary generic library packages (no 64-bit-scalar tricks,
multi-return is from free functions not interface dispatch), so they carry no special
32-bit-host risk; they run under whatever modes the unit-test runner covers.

## 6. Progression and landing

Land one container at a time, each self-contained and green:

1. **`vec`** — type + `New/Push/Pop/Get/Set/Len/Cap/Items` + cursor + tests.
   **✅ Landed** (main `67b2bff3`): 12 tests, green under builder-comp + builder-comp-int,
   adversarially reviewed (memory-model / API / spec-fidelity — no bugs).
2. **`hashmap`** — type + `New/Put/Get/Has/Remove/Len` + cursor + tests (backward-shift).
   **✅ Landed** (main `969e69fc`): 18 tests, green under builder-comp + builder-comp-int,
   adversarially reviewed (backward-shift verified exhaustively — no bugs). Private
   generic helpers (`slotFor`/`grow`) inlined: a `.bni` symbol is exported by
   definition, so a helper in the (body-included) generic `.bni` is public — inline
   it or export it as `SlotFor`/`Grow`; there is no private-in-`.bni` form.
3. **`set`** — type + `New/Add/Has/Remove/Len` + cursor + tests.
   **✅ Landed** (main `7ea33056`): 18 tests, green under builder-comp + builder-comp-int,
   adversarially reviewed (faithful transcription of the verified hashmap — no bugs).

Each follows the standard worktree flow (commit on the worktree; per-instance approval
for the cherry-pick to `main`; hygiene + smoke before landing; resync after).

## 7. Deferred / open (with rationale)

- **`Iterator[T]` / `Iterable[T]` interfaces** — deferred until methods-on-generic-types
  (relaxing `gen.no-generic-methods`) and generic-receiver `impl` land. The cursor
  *shape* stakes the design now; breadcrumb comments record the intended future form.
  A future `Iterable[T]` returns the boxed `@Iterator[T]` (no associated types).
- **`for x in myVec`** (ranging user types) — a language change; deferred until the
  container shapes stabilize, per the decision to wait.
- **Closure-based lazy adapters** (`Map`/`Filter`/`Take` over `@func() (T, bool)` —
  "Vision 2") — deferred to a possible future `pkg/stdx/iter`; needs no container
  rewrite.
- **More containers** — `list`/`deque`/`treemap`/ordered variants — later.
- **Shared open-addressing core** for `hashmap` + `set` — possible refactor once both
  exist and the shape is settled.
- **Promotion `stdx` → `std/containers`** — once the API is stable.
- **Constraint satisfaction at struct instantiation — enforced (was an open gap, now
  closed).** A generic struct instantiated with a type arg that does not satisfy its
  parameter's constraint is rejected at the instantiation site
  (`checkInstantiationConstraints`, spec §12.4; conformance `spec/12-generics/034`): e.g.
  `Map[BadKey, V]` with a non-`Hashable` `BadKey` is a compile error. (Historically this
  was enforced only at generic-*function* instantiation — the
  `gen.satisfy.struct-iface-unchecked` gap — so a bad key was accepted at the struct level
  and caught only once a constraint-using function like `Put`/`Get` was instantiated; that
  gap is closed.)

## 8. Rationale summary

- **Free functions, container-as-first-arg** — forced: no methods on generic types.
- **Cursors + slice views, not a boxed iterator** — Vision 1: zero-cost, monomorphic,
  no allocation; avoids anchoring the whole library on a boxed generator abstraction
  before we've decided we want lazy composition.
- **`stdx`, nested under `containers/`** — churn allowed (no compat guarantee); nested
  path makes eventual promotion to `std/containers` a rename.
- **No interfaces declared yet** — an un-implementable interface adds nothing; the
  cursor convention + breadcrumbs carry the forward-compat intent.
- **Backward-shift deletion** — no tombstone degradation; the serious choice.
- **Standalone `set`** — leaner than wrapping `Map[T, bool]`; duplication flagged.
- **Header-only (bodies in `.bni`)** — required by call-site monomorphization.
