# Design notes: `cast` / `bit_cast` / `unsafe_cast`

Captured design discussion (2026-08-18). **Decisions are settled; a formal
`plan-*.md` is the next step.** This doc is the durable memory of the discussion so
nothing is lost across compaction.

## Motivation — the bug that started it

The open MAJOR in `claude-todo.md`: `cast(@[]char, a)` where `a : [3]readonly char` (a
RUNTIME array) type-checks and mis-lowers to garbage / UAF, **silently**. Sibling of the
now-fixed `[N]readonly char → slice` implicit bug, on the EXPLICIT-conversion path.

Investigating it surfaced two deeper problems, so we're redesigning the whole
cast/bit_cast surface rather than patching the one hole:
1. The spec (`conv.cast.unchecked` §8.5, `conv.bit-cast` §8.6) says **both** `cast` and
   `bit_cast` are "unchecked at the type layer."
2. The impl **contradicts** that for `bit_cast`: `cast` has no shape gate (matches
   "unchecked"), but `bit_cast` **is** gated (`checkBitCastShapes`) — inconsistent, and
   the *reinterpret* hatch ends up stricter than the *value* hatch that can fabricate a
   managed value.

**IMPORTANT correction the user made (do not regress):** Binate is **NOT** a strictly
memory-safe language. It is low-level; passing raw↔managed **must** be possible. The
compiler does **not** forbid fabricating a refcount — with *defined* behavior it is the
**programmer** who fabricates it, deliberately. (An earlier "memory-safe by
construction / compiler must never fabricate a refcount" framing was WRONG and scrapped.)
The question is only **what the escape hatches are** and which conversions are safe.

## Grounded facts (verified against spec + impl; keep these — they anchor the plan)

### Representations (`docs/spec/07b-type-layout.md`)
- `*T` = 1 word. `@T` = **1 word** (the value is just a pointer). The `{refcount, free_fn}`
  header lives at **−2W in the POINTEE's allocation**, NOT in the `@T` value.
  `SizeOf(@T) == SizeOf(*T) == ptrSize()` (`pkg/binate/types/layout.bn:124`).
- `*[]T` = 2 words `{data, len}`. `@[]T` = 4 words `{data, len, backing, backingLen}`
  (first two words byte-identical to `*[]T`).
- `[N]T` = N·sizeof(T) contiguous. iface value = 2 words `{data, vtable}`.
- `readonly` is layout-transparent (§7.13.10).

### Spec (§8.5 `cast`, §8.6 `bit_cast`); rule-IDs
- `cast` DEFINED value conversions: int↔int (mod destination width, two's-complement —
  **runtime truncates/wraps, no error**), int↔float, float↔float, float→int
  (**saturates**: out-of-range/±Inf → MAX/MIN/0, NaN → 0), and **drop `readonly`** (may
  combine with a width or pointer-target change).
- `conv.cast.unchecked` — a `cast` of a *typed non-constant* operand is NOT validated for
  convertibility; out-of-defined-set is "the programmer's responsibility (run-time
  meaning may be unspecified)." **(This is what the redesign replaces.)**
- `conv.cast.const-not-laundered` — a CONSTANT operand must fit `T` → else COMPILE-TIME
  error; no laundering (`cast(int8, cast(int, 200))` errors). So constant out-of-range is
  *safe* (compile error), runtime out-of-range wraps.
- `conv.cast.float-int-saturation` — the float→int saturation, normalized once in shared
  IR-gen (conformance `732_float_int_saturation`).
- `conv.bit-cast` (§8.6, the only rule there) — bit reinterpret, no value conversion; "like
  `cast`, unchecked at the type layer; different size or violates a type's invariants is
  undefined (Ch.21)."
- The spec does NOT define casting *to* a managed type / fabricating a refcount. §8.4 says
  raw→managed "would invent a reference"; §15 `box` is the only sanctioned way to
  establish a managed value. rule-IDs: `conv.cast`, `conv.cast.unchecked`,
  `conv.cast.const-not-laundered`, `conv.cast.float-int-saturation`, `conv.bit-cast`;
  `builtin.cast`, `builtin.bit-cast` (§15).

### Checker impl
- CAST (`check_builtin.bn:63-92`): ONLY (1) `requireSizedType` (target-only opaque/sized
  guard), (2) `checkCastConstFits` (CONSTANT operand to an *integer* target that doesn't
  fit → error; runtime operands never checked), (3) interface-value operand/target
  **rejection** (`:83-89`). **NO source→target shape/kind/size check.** struct→array,
  ptr→slice, raw→managed, array→managed-slice all pass.
- BIT_CAST (`check_builtin.bn:94-102` → `checkBitCastShapes`, `check_c_interop.bn:151-197`):
  raw-slice arm requires BOTH raw slices + **same-element-size** + **non-managed elements**
  + non-opaque; otherwise `isBitCastRejectedAggregate` rejects `MANAGED_SLICE / STRUCT /
  ARRAY / FUNC_VALUE / MANAGED_FUNC_VALUE / INTERFACE_VALUE / INTERFACE_VALUE_MANAGED`. So
  bit_cast today allows only scalars / pointers (**incl. `@T` — managed ptr is NOT in the
  rejected set, so `bit_cast(@T, *T)` already works**) / safe raw-slices.

### Codegen
- The unsafe casts (array→managed-slice, raw→managed-ptr, managed→managed-different-pointee,
  struct↔array) lower to **silent garbage** on native/VM (a plain register `Mov`
  re-labeling the source → a managed value with a fabricated refptr → later `RefDec`
  decrements an arbitrary address → UAF). LLVM emits malformed IR (clang rejects it —
  loud but downstream, not a codegen guard). **No backend fail-louds** on an unhandled
  `OP_CAST` shape.

## THE DESIGN (settled)

### Three builtins, two orthogonal axes

**Axis 1 — logical/type-level conversion:** `cast` ⊆ `unsafe_cast`.
**Axis 2 — byte-level reinterpret:** `bit_cast` (separate; used only when you literally
want the bits, not a logical conversion).

### `bit_cast` — low-level byte reinterpret
- Rule: **simplest possible** — `sizeof(src) == sizeof(dst)` (**PROXIMAL / top-level size,
  NOT leaf-wise**) → reinterpret the bytes; **UB if the source's alignment doesn't meet
  the target's**. No leaf logic, no managed/aggregate special-casing.
- This **LOOSENS** the current impl: drop the `checkBitCastShapes` managed/aggregate
  rejection and the per-element size checks.
- **Why proximal:** it is critical (low-level) to round-trip a slice ↔ its explicit
  aggregate form, e.g. `@[]T` ↔ `struct{data, len, backing, backingLen}` (same 4 words),
  `*[]T` ↔ `struct{data, len}`, array ↔ struct, `*T` ↔ `@T`, int ↔ float bits.
- **Not** element-wise: an aggregate `bit_cast` is a flat byte reinterpret, NOT
  `map ∘ bit_cast`. `@[]int32 → @[]float32` **is a `bit_cast`** (rare; what you really
  want is `map ∘ bit_cast`, but it's `bit_cast`).

### `cast` — high-level logical conversion, SAFE only (defined + won't corrupt)
- **Lower bound (principle): `cast ⊇ everything ASSIGNABLE`.** If `t = s` (t:T, s:S) is
  allowed, or `f(s)` where `f`'s parameter is `T`, then `cast(T, s)` is allowed. Pulls in:
  identity, add-`readonly`, `@T→*T` borrow, concrete→interface widening, **sub-interface
  → super-interface widening**, untyped-const adoption, named transparency, etc.
- **Plus** explicit-only safe conversions: numeric scalar conversions (int↔int, int↔float,
  float↔float, float→int); named↔underlying; constant-typing (`cast(int8, 123)`, with the
  fit check).
- **Aggregate leaf-wise retype:** `@[]T → @[]S` (also arrays, struct fields) via `cast`
  **iff** `sizeof(S) == sizeof(T)` **AND** `cast(S, T)` agrees with `bit_cast(S, T)`
  (i.e. the element conversion is **total and bit-preserving**).
  - `@[]int8 → @[]uint8` ✓ (same size, bit-preserving).
  - `@[]int32 → @[]float32` ✗ (value-changing element conv → not a container `cast`; it's
    `bit_cast`, or an explicit map).
  - `@[]int32 → @[]int64` ✗ (element size differs → `len`/`backingLen` count invariant →
    OOB).
  - This aggregate relation is **stricter** than the scalar relation and is its **own**
    relation (does not cross scalar).
- **bool (directional):** `bool → int8` is `cast` (total + bit-preserving: 0/1 are valid
  int8, `cast == bit_cast`); `int8 → bool` is NOT `cast` (not total, not
  invariant-preserving → invalid bool) → `unsafe_cast`. State the leaf rule precisely as
  "a leaf conversion qualifies for `cast` iff it is **total and bit-preserving**"
  (asymmetric for bool by construction). **bool↔int is a SEPARATE, later, orthogonal
  step** (want `bool → {all ints, floats}`).

### `unsafe_cast` — "possibly-unsafe cast": a SUPERSET of `cast`
- `cast ⊆ unsafe_cast`. Same high-level logical axis; `unsafe_cast` additionally permits
  the **unverifiable** conversions the compiler would otherwise reject. You never choose
  `cast` vs `unsafe_cast` per conversion type — use `cast` (errors if unsafe) or
  `unsafe_cast` (accept the risk).
- Covers: **drop `readonly`** (`readonly T → T`, the `const_cast`-like op); `*T → @T`
  (raw→managed ptr — asserts a header exists at −2W); **unchecked interface NARROWING**
  (`@Iface → @T`, super-iface → sub-iface — extract the data word, NO runtime type check,
  unlike `x.(@T)` which panics); invariant-breaking scalar directions (`int8 → bool`, …).

### The 3-bucket test that sorts every conversion
- **(a) compiler can DERIVE the target's structure** → real conversion/construction →
  `cast` (numeric, named, add-`readonly`, `@T→*T` borrow, **interface WIDENING** — the
  vtable is derivable; it's a *construction*, 1→2 words).
- **(b) runtime can VERIFY** → checked assertion `x.(@T)` (interface NARROWING checked) —
  **NOT** a cast.
- **(c) neither — programmer asserts** → `unsafe_cast` (drop-`readonly`, `*T→@T`,
  interface-narrow-unchecked, invariant-breaking scalars).

### Resolved decisions
- **(a)** `bit_cast` and `unsafe_cast` are **separate axes**. `@[]int32 → @[]float32` is
  `bit_cast` only (it isn't a *logical* conversion), even though what you really want is
  `map ∘ bit_cast`. `unsafe_cast` does NOT cover plain byte reinterprets.
- **(b)** `*[]T → @[]T` is **NOT a cast**. Reason: `@[]T → *[]T` is **lossy** — slices are
  **value types**, and dropping to `*[]T` loses `backing`/`backingLen`. So
  `@[]T → *[]T → @[]T` cannot round-trip; making `*[]T → @[]T` a cast would be surprising
  (people expect the round-trip). Under-determined different-representation conversions are
  **constructed explicitly**, not cast. (For a raw byte round-trip of the same 4 words,
  that's `bit_cast` against the explicit struct form.)
- **Transitivity invariant:** `cast` must be transitive — `cast(T,s) ∧ cast(R,t) ⟹
  cast(R,s)`. State it **per relation** (the scalar-cast relation is transitive on its
  own; the aggregate-retype relation is transitive on its own; they don't cross). Use it
  as a **design filter** (reject any conversion that doesn't compose).
- **Multiple spellings are fine** and semantically distinct: `bit_cast` is inherently
  low-level (literal bits); `cast`/`unsafe_cast` are logical/high-level (preferred unless
  you really want literal bit-casting). `*T→@T` is expressible as `unsafe_cast(@T, p)`
  ("logically a `@T`", unchecked) or `bit_cast(@T, p)` ("reinterpret the pointer bits") —
  operationally identical for a pointer, different intent.

## Spec + impl work (for the plan)
- **§8.5 `cast`** — redefine fully: the assignability lower bound + explicit safe
  conversions + the aggregate leaf-wise rule (total + bit-preserving) + the transitivity
  invariant. Replace `conv.cast.unchecked` (cast is now safe-only + gated, not "unchecked/
  programmer's responsibility").
- **§8.6 `bit_cast`** — the simplest same-proximal-size rule + UB on alignment mismatch;
  drop the "unchecked" framing and reconcile with the loosened impl (no more
  managed/aggregate rejection).
- **New `unsafe_cast`** — spec section (§8.x) + §15 builtin: the possibly-unsafe superset,
  the unverifiable list, and unchecked interface narrowing.
- **Impl** — give `cast` a shape gate that rejects the now-unsafe conversions and points to
  `unsafe_cast` (or `bit_cast`); **simplify** `checkBitCastShapes` to same-proximal-size;
  add the `unsafe_cast` builtin (checker + IR-gen; note the alignment-UB note). Closes the
  original `cast(@[]char, arr)` MAJOR (it becomes a compile error → use `unsafe_cast` /
  `bit_cast` / construct, per the case).
- **Tests** — negative + positive per builtin per category; the transitivity property; the
  bool directionality; the interface widen/narrow paths. Migrate any existing code that
  used `cast` for a now-rejected conversion.

## Still open (decide while writing the plan)
- Finalize the exact `unsafe_cast` conversion list + the precise diagnostics (`cast` →
  "use `unsafe_cast`" vs "use `bit_cast`" vs "construct explicitly").
- Whether `bool↔int` is in scope for this plan or a deliberate follow-up (leaning
  follow-up — user called it orthogonal).
- The `unsafe_cast` name / keyword vs builtin-function status (it's a builtin like
  `cast`/`bit_cast`).
- Migration/rollout scope (how much existing code relies on today's ungated `cast`).
