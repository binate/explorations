# Wasm support (browser-first) — Plan

Adds WebAssembly as a target, scoped initially to **browser execution
in a web worker**. Server-side wasm (wasi / wasmtime) is explicitly
out of scope.

Covers Tier A (toolchain works, hello-world worker) and Tier B0 (the
shared substrate for everything that follows) in detail. Later phases
(B1 REPL, B2 graphics, B3 audio, B4 filesystem) are named with their
dependencies so the substrate doesn't accidentally rule them out; each
gets its own plan when picked up.

## Constraints and ground rules

- **Target: `wasm32-unknown-unknown`** + a custom JS loader. No
  Emscripten — its libc shim is wasted weight in a worker context
  (no DOM, no filesystem, no console-as-real-output to lean on).
- **Honors the C-free target principle**: the wasm runtime uses wasm
  intrinsics directly (`memory.grow`, `memory.copy`, …) with at most
  a minimal C shim. No libc dependency.
- **One Binate program = one worker, isolated.** Communication with
  the main thread (or peer workers) is via `postMessage` / message
  ports. Transferables (`ArrayBuffer`, `OffscreenCanvas`,
  `MessagePort`, `ImageBitmap`, …) are carried as opaque integer
  handles on the Binate side, with a JS-side handle table for
  dereference.
- **No DOM access from Binate code.** The annotation surface is
  general enough to permit it (a sufficiently-motivated user can
  declare any JS import), but the bundled package surface stops at
  the worker API.
- **File paths assume the `pkg/`-layout migration has happened**
  (per `pkg-layout-plan.md`). If wasm work lands first, substitute
  current paths and migrate together with the rest.

## Tier A — minimum: hello from worker

Goal: a Binate program compiles for wasm, runs in a worker, posts an
integer back to the main thread, asserted in CI under Node with
`worker_threads`. Validates the entire pipeline before any of the
substrate work in Tier B0.

### A.1 — `cmd/bnc` learns the wasm target

Add a `--target wasm32` (or similar) flag to `cmd/bnc`. When set,
the per-package compile invokes `clang --target=wasm32-unknown-unknown
-c <file>.ll -o <file>.o`. The link step invokes
`clang --target=wasm32-unknown-unknown -nostdlib -Wl,--no-entry
-Wl,--export-all -o <out>.wasm <obj-files>...`.

`-nostdlib` removes the implicit libc link. `--no-entry` is required
because we don't have a `main` in the usual sense — the worker host
calls Binate-exported functions directly. `--export-all` initially,
then narrowed via `#[js.export]` once that lands in B0.

### A.2 — Bare-wasm runtime variant

`runtime/binate_runtime.c` today uses libc (`malloc`/`free`/
`memcpy`/`memset`). For wasm we need a minimal replacement, which
lives at `impls/core/wasm/pkg/builtins/rt/rt_wasm.c` (assuming the
pkg-layout migration has happened; otherwise `runtime/binate_runtime_wasm.c`
beside the current one).

What it provides:

- **Allocator**: a bump allocator over `WebAssembly.Memory`, calling
  `memory.grow` on overflow. Or a small free-list allocator if bump
  is too restrictive. Start with bump; reassess if conformance shows
  pathological allocation patterns. (`free` on bump = no-op; matches
  Binate's refcount-only memory model anyway — there's no general
  `free` use case beyond destructor calls.)
- **memcpy / memmove / memset**: emitted as wasm bulk-memory
  intrinsics (`memory.copy`, `memory.fill`). Clang inlines these for
  reasonable sizes; for large sizes the intrinsic does the work in
  native wasm instructions.
- **Refcount helpers**: identical to the LLVM-emitted inline form
  used elsewhere — no shim needed; the existing inline ops compile
  for wasm32 unchanged.

Nothing else from `runtime/binate_runtime.c` is actually needed in
Tier A (no I/O, no syscalls).

### A.3 — JS loader (in-repo)

A single `wasm/host/worker.js` (~150 lines) that:

- Instantiates the `.wasm` module in the worker (where it lives — the
  `.js` is loaded by the worker itself, the wasm imported from the
  same origin).
- Provides the bare-minimum import surface for Tier A: one function
  `host_post_message(ptr, len)` that copies `len` bytes from wasm
  linear memory at `ptr` and calls `self.postMessage(...)`.
- Wires the worker's `onmessage` to a Binate-exported entry point
  (`bn_on_message(ptr, len)` for the demo; renamed once B0 lands).
- No filesystem, no console, no DOM. Minimal.

### A.4 — Demo + test harness

- `examples/wasm/hello_worker.bn`: a Binate program that on receiving
  a message containing an integer N, posts N+1 back.
- `wasm/test/hello_worker.html` (used by Playwright later) and
  `wasm/test/hello_worker.node.js` (the lighter Node harness for
  Tier A).
- Node harness uses `worker_threads`. Spins up the worker, sends an
  integer, asserts the response. Light, no browser.

### A.5 — CI

New mode `builder-comp_wasm32_browser` mirroring the existing cross-
target naming (`builder-comp_arm32_linux`, etc.). For Tier A, the
runner is `node wasm/test/<demo>.node.js`. New GitHub Actions matrix
entry on `ubuntu-latest`. No new platform dependencies — Node ships
on the runner.

## Tier B0 — shared substrate

Goal: the design surface every later phase (B1–B4) builds on. Without
this, each later demo would invent its own ad-hoc bridge to JS, which
fragments badly.

### B0.1 — JS-interop annotation surface

Two new annotations, mirroring the existing C-extern surface:

- `#[js.import("function-name")]` on a function declaration with no
  body. IR-gen emits a wasm `import` entry with the named field; the
  JS-side host must supply it. Calling convention: Binate scalar
  args → wasm i32/i64, multi-word args (slices, structs) → pointer-
  to-stack-buffer as in the existing aggregate-arg convention.
- `#[js.export]` on a function declaration with a body. IR-gen marks
  the function as an exported wasm symbol so JS-side callers can
  invoke it through the wasm `exports` object.

The annotations are general enough that any JS function can be wired
in — DOM, fetch, anything. The *bundled* surface stops at the worker
API; broader uses are user code.

### B0.2 — Opaque-handle table

Transferables (MessagePort, OffscreenCanvas, ArrayBuffer, …) are not
representable as wasm values directly. The JS host maintains a table:

```
handle_table: Map<int, jsValue>
next_handle: int = 1
```

Allocation: when JS hands a transferable to Binate (e.g. on receipt
of a postMessage carrying transferables), allocate a new handle slot,
store the JS value, pass the integer handle into Binate. Release: a
`host_release(handle)` JS import called by Binate when the value is
no longer needed.

Lifecycle integration: a Binate-side wrapper type (e.g.
`@web.Handle`) holds the int; its destructor calls `host_release`.
The exact package path follows pkg-layout — probably
`pkg/web/handle` or absorbed into a `pkg/web/worker` umbrella.

### B0.3 — Worker host-import surface

The minimal-but-real set of imports Binate worker programs need:

- `host_post_message(data_ptr, len, transfer_handles_ptr, n_transfers)` —
  Binate calls this; JS copies bytes out, resolves transfer handles
  back to actual JS values, calls `self.postMessage(...)`.
- `host_release(handle)` — drop a JS-side handle table entry.
- Inbound: the worker's `onmessage` JS code calls a Binate-exported
  `bn_on_message(data_ptr, len, handles_ptr, n_handles)`. Binate
  copies the bytes into a Binate-side buffer, registers any
  transferable handles, dispatches application code.

A `pkg/web/worker` (or wherever it lands per pkg-layout) wraps these
into idiomatic Binate function signatures. Application code never
touches the raw `host_*` imports directly.

### B0.4 — Playwright CI for Tier B+ demos

Tier A passes under Node + `worker_threads`, but anything that needs
real browser worker semantics (which is everything in B1+) needs a
real browser. Add a Playwright + headless Chromium harness:

- `wasm/test/<demo>.html` per demo.
- Playwright opens the page, waits for an assertion sentinel
  (text in DOM / postMessage to test runner), times out otherwise.
- New CI workflow / matrix entry, fanning out per demo.

Playwright installs Chromium on first run (~150MB, cached). One-time
CI dependency.

## Deferred phases (planned later, with named dependencies)

These are deliberately NOT planned in detail here. Each gets its own
plan doc when picked up.

### B1 — REPL demo
**Depends on**: REPL refactor as embeddable component (separate TODO
in `claude-todo.md`). The current REPL is tightly coupled to stdin/
stdout via `bootstrap.{Read,Write}` and assumes synchronous blocking
I/O. For a worker, the I/O must route through message ports and the
loop must be event-driven (coroutine-ish). The refactor is a real
chunk of work in its own right and shouldn't be wedged inside the
wasm plan.

### B2 — Graphics demo (OffscreenCanvas)
**Depends on**: B0 substrate. Wraps OffscreenCanvas behind the
opaque-handle scheme; binds a small subset of `CanvasRenderingContext2D`
methods as JS imports. A `requestAnimationFrame` JS-side loop calls
a Binate-exported `bn_render(t)` for each frame.

### B3 — Audio demo (AudioWorklet)
**Depends on**: B0 substrate. AudioWorklet hosts a wasm module on
the audio-rendering thread; Binate code processes one buffer per
callback. Real-time constraints (no allocation in the hot path).

### B4 — Filesystem
**Depends on**: B0 substrate; **intersects with**: pkg/bootstrap
replacement (separate effort). Two natural shapes:
- **Virtual FS**: JS-side `Map<string, Uint8Array>`, exposed via the
  worker host-import surface. Ephemeral; lightest.
- **OPFS** (Origin Private File System): browser-persistent,
  worker-accessible. Heavier but real persistence.

Probably implemented in that order.

## Out of scope

- **Emscripten path** (any browser-via-emscripten flow).
- **WASI / server-side wasm** (wasmtime, wasmer, Node-with-WASI).
- **Tier C — toolchain-in-browser** (the user types Binate source,
  it compiles to wasm in-page). Not pursued.
- **DOM bindings** as a bundled package. Annotation surface allows
  ad-hoc declaration; bundled stops at worker primitives.
- **Network bindings** (fetch, WebSocket). Same: ad-hoc via
  annotations, not bundled.
- **Dynamic wasm linking** (Emscripten MAIN_MODULE/SIDE_MODULE,
  wasm component model). Each Binate program is one statically-
  linked `.wasm`.

## Risks

- **LLVM version skew**: wasm-ld is reliable on recent LLVM (14+),
  but specific intrinsic availability (`memory.copy`/`memory.fill`)
  requires bulk-memory support, which has been stable for years.
  Document the minimum LLVM version we test against.
- **Pkg-layout migration interaction**: the wasm-runtime files
  naturally live under `impls/core/wasm/` per pkg-layout-spec, but
  that migration hasn't happened yet. If wasm work lands first, file
  paths are transitional; carry the move along with the rest when
  pkg-layout-plan executes. Either ordering works; no architectural
  conflict.
- **Annotation system load**: `#[js.import]` / `#[js.export]` are
  the first user-facing annotation pair beyond C-extern. The
  annotation parsing/IR-gen path needs minor extension; lint passes
  and code-hygiene scripts may need awareness. Bounded but
  cross-cutting.
- **Browser-API stability**: `OffscreenCanvas` is well-supported in
  evergreen browsers; some peripheral APIs (e.g. AudioWorklet
  parameters, ImageBitmap rendering details) have minor cross-
  browser variance. Plan demos around well-supported subsets;
  document tested browser versions.
- **CI weight from Playwright**: adds Chromium download (~150MB
  cached). Bounded; same shape as any other browser-CI repo.
