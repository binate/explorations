# Plan: Decouple `print`/`println` Builtins from C Runtime

Status: COMPLETE (shipped); kept for design rationale. `print`/`println`
now format in Binate and depend on the single `bootstrap.Write` sink; the
`bn_print_*` C functions are deleted.

Sub-plan of `runtime-abstraction-plan.md` step 3.3. Goal: shrink the
"C surface" required by the `print`/`println` language builtins to
the absolute minimum, so a future ARM32 (or any libc-free) backend
needs only a single I/O primitive (`bootstrap.Write`, which itself
wraps one syscall).

## Motivation

`print`/`println` must remain special builtins (they're variadic and
type-dispatched; we'll never reproduce them from user-level code
until we have interfaces and variadics — and even then, they may
stay special for ergonomic reasons). But today the IR-gen for
`println(x)` lowers to a *type-specific C function* (`bn_print_int`,
`bn_print_bool`, etc.) that does both formatting AND I/O.

Each of those `bn_print_*` C functions is a piece of "C surface" the
backend depends on. For a multi-backend / libc-free future, every
extra C symbol is friction. We can shrink the surface by separating
**format** (pure, in Binate) from **I/O** (one well-known sink).

## Target Architecture

```
print/println builtin   (special; IR-gen)
        │
        ├─ formats each arg via:  bootstrap.formatInt /
        │                         bootstrap.formatBool /
        │                         bootstrap.formatFloat /
        │                         bootstrap.formatChars
        │     (Binate; pure functions; fill caller-provided
        │      stack buffer; no allocation; no I/O)
        │
        └─ writes bytes via:      bootstrap.Write
                                       │
                                       └─ extern; thin wrapper
                                          around POSIX `write(2)`.
                                          The single C/syscall surface.
```

After the rewire, `print`/`println` transitively depend on **one** C
symbol: `bn_bootstrap__Write` (which becomes `c_write` syscall stub on
libc-free targets). The 7 `bn_print_*` C functions are deleted.

## `formatFloat` semantics

`formatFloat(v float64, buf *[]uint8) int`. Bootstrap-grade
semantics, deliberately NOT %g-compatible:
- NaN → `"NaN"`, ±Inf → `"+Inf"` / `"-Inf"`
- Finite, fixed-point envelope: `integer.6digits`, truncated (no
  rounding, no trailing-zero trimming, no scientific notation)
- Outside the envelope (`|v| ≥ 2^53` or `0 < |v| < ~1e-6`): exact
  `mantissa*2^exponent` fallback, lossless. e.g. `println(1e-30)`
  yields the IEEE 754 bits in ridiculous-but-honest form.

Tests that need bit-exact float verification use `bit_cast` directly
— see conformance test 330_float_bit_exact for the canonical example.

## `bn_exit` (deferred; discuss separately)

Conceptually `exit(code)` could become a direct `rt.c_exit(code)`
call from IR-gen, no `bn_exit` needed. But there are subtleties
(when does cleanup run? what about deferred destructors?). Defer.

## Open Questions / Decisions

- **Format helper visibility.** Lowercase names (semantic hint:
  "internal"). Privacy in Binate is governed by the `.bni`, not by
  capitalization, so putting `formatInt` in the `.bni` makes it
  linkable across compilation units (matters for separate compilation
  to `.o`/`.a`). Add a whitelist in the bni naming hygiene check for
  these specific helpers.
- **Format helper unit tests.** Tests live in `pkg/bootstrap/` and
  exercise `formatInt` directly.
- **`formatFloat` requirements.** Earlier draft of this plan called
  out `%g` semantics as "non-trivial to reproduce in pure Binate"
  and floated deferring `bn_print_float`. That framing was wrong:
  the algorithm is gnarly in any language, and `bootstrap`'s
  print/println is a temporary scaffold — there was never a
  requirement for libc-bit-exact `%g`. The actual requirement is
  "readable enough for conformance tests, with bit-exact float
  verification via `bit_cast` for tests that care." See the
  `formatFloat` semantics above for the resulting bootstrap-grade
  implementation.

## Non-Goals

- Changing the Go bootstrap interpreter's print/println path.
- Changing `bootstrap.Write`'s C-side wrapping in this plan (that's
  step 3.4 / 3.6 of the broader runtime-abstraction plan).
- Removing the `Builtin` flag concept from the loader (other builtin
  packages can still use it; we're just unmarking `pkg/bootstrap`).
