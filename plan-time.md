# Plan: `pkg/std/time`

## The core idea

Time is **clock-relative**. There is no universal clock; the "universal
timeline" is a fiction. What physically exists are *readings from particular
clocks*, and a reading's semantics (ordering, subtraction) are well-defined
only **within a single clock**. The moment two readings come from different
clocks, comparison is skew-dependent and basically a guess — and this is true
not just across machines, but **across clock interfaces on one machine**: a
later read of a coarse clock can read *lower* than an earlier read of a fine
clock, even when both derive from the same base, because they differ in
resolution and update cadence.

So the package is built around honoring that, using the type system to make
unsound cross-clock comparison a **compile error**, and being honest that a
foreign timestamp (a file's mtime) carries no recoverable clock identity.

This is a deliberate break from Go's `time.Time`, which conflates an instant, a
timezone (`*Location`), and an optional hidden monotonic reading into one type —
three things that don't belong together.

## Types

### `time.Point`
An abstract point on the hypothetical universal timeline — `(sec int64, nsec
int32)` since the Unix epoch (POSIX time, which elides leap seconds; that
elision is part of the fiction). A `Point` carries **no clock identity**: it is
what a file's mtime is — a foreign claim of a universal point, produced by some
source you don't control and may not share.

Operations on `Point` are **numeric and source-relative**: `a.Before(b)`,
`a.Sub(b) -> Delta`, etc. compare/subtract the underlying readings, which is
meaningful *only if you know a and b share a source* (e.g. two files on one
filesystem). The type cannot verify that, so — in the raw-pointer spirit — it
hands you the operation and the responsibility, and says so plainly rather than
pretending `Point`s are points on one real timeline.

### `time.Delta`
An abstract signed difference between two points (or two same-clock readings) —
`int64` nanoseconds, clock-agnostic once computed. `Point - Point -> Delta`;
`Point ± Delta -> Point`; `Delta ± Delta -> Delta`; `Delta` compares and
negates. No floats, so accessors are truncating integers — `Nanoseconds()`
(exact) / `Microseconds()` / `Milliseconds()` / `Seconds()` / `Minutes()` /
`Hours()`, all `int64` — plus `Delta` constants (`Second`, `Millisecond`, …).

### `time.Clock` (interface) and `time.Reading[C time.Clock]`
A `Clock` is a first-class clock interface (realtime, realtime-coarse,
monotonic, monotonic-raw, boottime, …), each its own domain. A `Reading[C]` is
a reading **typed by its clock** C. Same-`C` ordering/subtraction is the sound
operation; **cross-`C` is a type error** — `Reading[Realtime]` and
`Reading[CoarseRealtime]` simply don't compare.

**Monomorphized** (decision): `Reading[C time.Clock]`. The cost is that the
clock must be known at compile time; that's circumvented by an appropriately
designed `Clock` when needed. Crucial caveat: **`C1 == C2` is necessary but not
sufficient** for two readings to be comparable — the same clock *type* doesn't
guarantee the same clock *instance/context* (two processes' monotonic clocks
share the type `Reading[Monotonic]` but are different actual clocks / boot
epochs). Encoding the needed instance/context distinction is the job of the
`Clock` design (e.g. a clock type whose readings are only ever produced within
one context, or one that carries a checkable instance tag).

### Ordering guarantees in the clock types (decision: include)
Clocks differ in *how* their readings order, and the types encode it rather
than leaving it to documentation:
- **monotonic** ⇒ a sound total order (non-decreasing within its instance);
- **realtime** ⇒ orderable but can jump backward on adjustment (NTP);
- **coarse** ⇒ sound but low-resolution.

Likely shape: a stronger sub-constraint (e.g. `MonotonicClock : Clock`) gates
the *sound* total-order operation (a constrained free function over
`Reading[C MonotonicClock]`), while a general `Reading[C Clock]` exposes only
the caveated compare. Exact API is finalized when this half is built.

### Bridges
- `Reading[C].Point()` — interpret a *civil* clock's reading as a universal
  `Point` (the fiction, made explicit). A *monotonic* reading has **no**
  `Point` — it isn't on the universal timeline; it only yields `Delta`s.
- foreign `Point` → `Reading[C]` — an explicit, visible assertion that the
  point came from clock C (e.g. "this file shares my realtime clock"), never
  implicit.

## Out of scope here (deliberately, not laziness)

- **Timezones.** A zone is a *presentation* concern, applied when rendering a
  `Point` as civil Y/M/D/h in a locale — never carried on the instant. Zones +
  formatting + calendar breakdown are a separate later layer.
- **`Now()` and the concrete clocks.** Reading a local clock needs
  `clock_gettime` — a syscall capability that does not exist yet (same flavor
  of problem as `struct stat`). Building `Reading[C]` / concrete `Clock`s with
  no clock to read would be unused speculation. They are *designed* here so
  `Point`/`Delta` compose with them, and *built* alongside `Now()` when the
  clock-reading mechanism lands.
- **Monotonic durations / benchmarking** — arrive with monotonic `Reading`s.

## Conventions

- **Value receivers + value args** for `Point` and `Delta` (small immutable
  values; matches `Celsius`/`Box`). `Reading[C]` likewise.
- Representations are exposed in the `.bni` — Binate has no field hiding yet
  (opaque forward-declared structs are unimplemented), and a by-value type must
  publish its layout. Fields are lowercase by convention ("use the methods"),
  not enforced-private.
- No floats anywhere; no errors (construction/accessors don't fail).

## Build scope

- **Now:** `time.Point` + `time.Delta`, in `impls/stdlib/common/pkg/std/time/`
  (pure value math — no syscalls, no build constraints). These are the *correct*
  types for what `stat` hands back (a foreign `Point`) and for differences
  (e.g. which of two files on one FS is newer, by how much) — not stubs.
- **Later, with `Now()`/clock-reading:** `time.Clock`, `time.Reading[C]`, the
  concrete clock types, and the ordering-guarantee constraints.

## Verification

`Point`/`Delta` are pure value math → ordinary unit tests (construction,
normalization, arithmetic, comparison, accessor truncation). No ABI, so no C
cross-check needed. (The future `Reading[C]` / clock layer *will* need the same
per-OS C-verification the errno table and `struct stat` use, since those read
syscall results.)

## Status

- `Point` + `Delta` — to build (this is Stage 1 of `plan-os-stat.md`).
- `Clock` + `Reading[C]` + ordering guarantees — designed here; built with
  `Now()`/clock-reading later.
