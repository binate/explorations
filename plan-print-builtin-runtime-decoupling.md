# Plan: Decouple `print`/`println` Builtins from C Runtime

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

## Mode Map

Three execution modes, three implementations of the underlying
primitives. The format helpers are Binate code, shared between
compiled and VM modes; the Go bootstrap stays special-cased.

| Mode | `print(x int)` lowers to | format impl | I/O impl |
|------|--------------------------|-------------|----------|
| **boot** (Go interpreter walks test.bn) | Go-side AST handler → `fmt.Print` | n/a (Go does it) | Go's stdout |
| **boot-comp** (Go runs cmd/bnc → native binary) | Binate `formatInt` + `bootstrap.Write` | `pkg/bootstrap.bn` | C `bn_bootstrap__Write` (POSIX write) |
| **boot-comp-int** (compiled bni runs test.bn in VM) | same as boot-comp, executed as bytecode | `pkg/bootstrap.bn` (in VM) | `execExtern` `bootstrap.Write` arm → POSIX write |

The Go bootstrap interpreter (`bootstrap/interpreter/interpreter.go`)
**does not change**: `registerBootstrapPackage()` keeps its Go-defined
handlers, including `print`/`println` going through `fmt.Print`.
There is no Go-defined `formatInt` mirror; Go bootstrap never calls
into the new Binate format helpers.

## Step Plan

### Step 1 — Unmark `pkg/bootstrap` as builtin (self-hosted loader)

- Remove `loader.RegisterBuiltin(ldr, "pkg/bootstrap")` from
  `cmd/bnc/main.bn` and `cmd/bni/main.bn` (and any other callers).
- The self-hosted loader (`pkg/loader/loader.bn:351`) already handles
  "only .bni, no impl" — it uses the .bni as the merged file and all
  decls become extern (no body → `IsExtern = true` in IR-gen).
- Compiled mode: extern decls link to the C runtime's
  `bn_bootstrap__*` symbols. No diff.
- VM mode: extern calls fall through `LookupFunc` → `execExtern`,
  which already has the `bootstrap.*` arms. No diff.
- Go bootstrap: untouched. Its own `registerBootstrapPackage()` path
  is independent of the self-hosted loader.
- Verification: full conformance + unit tests pass with no observable
  change.

### Step 2 — Add `formatInt` and rewire `print(int)` only

- Add `formatInt(v int, buf *[]uint8) int` to `pkg/bootstrap.bni`.
  Lowercase name (these are internal helpers); add a whitelist entry
  in the `.bni` naming hygiene check.
- Create `pkg/bootstrap/bootstrap.bn` with `formatInt` body: write
  decimal digits into caller's buffer, return number of bytes
  written. Stack buffer of `[20]uint8` suffices for int64.
- Update `genPrintCall` (in `pkg/ir/gen_expr.bn`) for the int branch:
  alloca `[20]uint8`, call `formatInt(v, buf)` → length, call
  `bootstrap.Write(STDOUT, buf[:n])`.
- Delete `bn_print_int` from `runtime/binate_runtime.c` and from the
  runtime manifest in `pkg/ir/runtime.bn`.
- Strip `print_int`/`bn_print_int` arms from the VM's `execBuiltin`
  if they're no longer needed.
- Goal: `print(int)` works in all modes with the new path; all tests
  pass.

### Step 3 — Roll the same pattern out for the rest

Per-arg-type format helpers + `bootstrap.Write`:
- `formatBool(v bool, buf *[]uint8) int` — writes "true" or "false"
- `formatChars` not needed — char/string/managed-slice values are
  already byte sequences; emit `bootstrap.Write` directly
- `print_newline` becomes `bootstrap.Write(STDOUT, "\n", 1)`
- inter-arg space becomes `bootstrap.Write(STDOUT, " ", 1)`

Remove all remaining `bn_print_*` from `binate_runtime.c` and the IR
runtime manifest. Strip the corresponding `execBuiltin` arms.

### Step 3.1 — `formatFloat` (rolled the rest of the way) — DONE

Adds `formatFloat(v float64, buf *[]uint8) int`. Bootstrap-grade
semantics, deliberately NOT %g-compatible:
- NaN → `"NaN"`, ±Inf → `"+Inf"` / `"-Inf"`
- Finite, fixed-point envelope: `integer.6digits`, truncated (no
  rounding, no trailing-zero trimming, no scientific notation)
- Outside the envelope (`|v| ≥ 2^53` or `0 < |v| < ~1e-6`): exact
  `mantissa*2^exponent` fallback, lossless. e.g. `println(1e-30)`
  yields the IEEE 754 bits in ridiculous-but-honest form.

Tests that need bit-exact float verification use `bit_cast` directly
— see conformance test 330_float_bit_exact for the canonical example.

After 3.1: `bn_print_float` is gone, `c_print_float` is gone, all
`bn_print_*` shims are gone. The print/println builtin's only C
surface is `bootstrap.Write` (the single syscall sink).

### Step 4 — `bn_exit` (deferred; discuss separately)

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
  exercise `formatInt` directly. They won't run under `boot` (Go
  bootstrap has no Go-defined `formatInt`) — mark `.xfail.boot`. We
  may eventually fork the conformance tests so the bootstrap has its
  own limited copy; same will go for unit tests then.
- **`formatFloat` requirements.** Earlier draft of this plan called
  out `%g` semantics as "non-trivial to reproduce in pure Binate"
  and floated deferring `bn_print_float`. That framing was wrong:
  the algorithm is gnarly in any language, and `bootstrap`'s
  print/println is a temporary scaffold — there was never a
  requirement for libc-bit-exact `%g`. The actual requirement is
  "readable enough for conformance tests, with bit-exact float
  verification via `bit_cast` for tests that care." See step 3.1
  for the resulting bootstrap-grade implementation.

## Non-Goals

- Changing the Go bootstrap interpreter's print/println path.
- Changing `bootstrap.Write`'s C-side wrapping in this plan (that's
  step 3.4 / 3.6 of the broader runtime-abstraction plan).
- Removing the `Builtin` flag concept from the loader (other builtin
  packages can still use it; we're just unmarking `pkg/bootstrap`).
