# Plan: Implementing FFI Export (exposing Binate to C)

**Status:** high-level plan (2026-07-09). The **design** lives in
**[design-ffi-export.md](design-ffi-export.md)** (a **proposal** — reworked and
adversarially reviewed, but **not yet ratified, not specified, not implemented**).
This document is the implementation roadmap **contingent on the design being
ratified/specified** (Phase 0); it does not re-litigate the design. A future
edit-site-level detailed plan (`plan-ffi-export-detailed.md`) would follow, grounded
in a repo-wide survey, once Phase 0 clears.

Sibling/related work: the FFI **annotation family** (`plan-c-call.md` `__c_call` ✅,
`plan-c-global.md` `__c_global`, the `#[link]` companion sketch in `claude-todo.md`);
the **motivating use case** (`plan-embeddable-interp.md` / `plan-embeddable-vm.md` /
`plan-repl-embeddable.md`); `plan-extern-var.md` (`.bni` externs); the **symbol/alias
& object-format** work (`plan-linker.md`, `plan-macho-dysymtab.md`,
`plan-backend-objformat-decoupling.md`); and the **baremetal** entry
(`plan-arm32-bare-metal.md`).

## 1. What we are building

A way to expose Binate functions to C, and to write the program's startup glue in
Binate. From the design note, the moving parts are:

- **`#[c_export("name")]`** — an unqualified (compiler-recognized) annotation on a
  top-level func decl that emits an **additional, unmangled** C symbol aliasing the
  function (the mangled Binate name is unchanged; multiple names allowed). Fits the
  existing annotation grammar — **no core-grammar change**.
- **`bn_init` / `bn_entry`** — two **hardcoded well-known glue symbols**. `bn_init`
  runs every package's `__init` in dependency order over the **build root's**
  transitive deps (the promotion of today's `main`-rooted `main.__init_all`), and is
  **idempotent** (run-once guard). `bn_entry` = `bn_init()` then `main.main()`.
- **`pkg/builtins/platform_init`** — a new builtins package (sibling to `rt`) holding
  **build-conditional** (`#[build(...)]`) entry/startup functions: a hosted C-`main`
  (`#[c_export("main")]`, argv capture, calls `bn_entry`) that **retires
  `runtime/binate_runtime.c`**; a baremetal `_start` placed via a linker-placement
  annotation; a library `_init` (`#[c_export("…_init")] { bn_init() }`). Which entry
  is wired up characterizes the build (program / library / freestanding) — no mode
  flag.
- **Library / merge build mode** — `bnc --library <loc>` compiles a **facade package**
  (imports = the package set, `#[c_export]` wrappers = the surface, `_init` → `bn_init`,
  no `main`) into a linkable artifact; `--library A --library B` **merges** their files
  into one unit (shared deps once, disjoint names, one runtime/`bn_init`).
- **Trivial-forward → symbol alias** — a signature-preserving forwarder
  (`func f(x) R { return g(x) }`, incl. `_init`'s `{ bn_init() }`) lowers to a **symbol
  alias**, so verbatim re-export and coexisting library `_init`s are zero-cost.
- **Header generator** — first-cut C header from the facade's export signatures.
- **Signature rule** — "C-ABI-replicable" (do not gate on scalar/pointer); managed
  refcount + function-value field-order are **documentation/lint**, not an ABI gate.

## 2. Current state (from the codebase)

- **Init/entry codegen** — `pkg/binate/ir/gen_init.bn`: `EmitMainEntry` emits
  `bn_entry` ( = `main.__entry`); `EmitInitDispatcher` emits `main.__init_all` (calls
  each `<pkg>.__init` in dependency order); `generatePackageInit` / `PackageInitName` /
  `HasPackageInit`. **Everything is `main`-rooted today** — no notion of a non-`main`
  build root, no idempotency guard, no stable `bn_init` name.
- **C entry glue** — `runtime/binate_runtime.c`: C `main()` stores `argc`/`argv` and
  calls `bn_entry`.
- **Annotation system** — `pkg.annotation` (§16.7): `#[...]` blocks recognized on the
  package clause / import / top-level decl. The **only** unqualified (compiler-required)
  annotation today is **`build`**; an unknown unqualified name is a **compile error**
  (the typo check). So `c_export` and a linker-placement annotation slot in as new
  unqualified names, but every compiler that must *parse* them has to recognize them
  (BUILDER caveat, §4).
- **Verbatim-symbol path** — `__c_call` (`plan-c-call.md`) already emits C symbol
  names **unmangled/verbatim** through the backends' platform-C-ABI lowering. c_export's
  emitted alias reuses that "verbatim, no `bn_` mangling" discipline (`pkg/mangle` +
  the backends).
- **Backends** — LLVM (`pkg/codegen`) + native x64/aarch64 (`pkg/binate/native/*`),
  each with its own object-format symbol emission (Mach-O / ELF). Alias emission
  differs per format (LLVM alias / Mach-O `N_INDR` / ELF `.set`).
- **Driver / loader** — `cmd/bnc` + `pkg/loader`: today a build root is a `main`
  package; the loader computes the transitive import closure and `ldr.Order`. There is
  **no** library/merge mode and **no** "compile a non-`main` root" path.
- **Init-order caveat** — within-package var init is **declaration-order** (an open
  spec item in `claude-todo.md`); `bn_init` inherits whatever that resolves to.
- **No unused-import check in the compiler** (bnlint-only); `import _` is supported —
  a ready primitive for a facade to pull a package in for init/exposure.

## 3. Phases

Ordered by dependency. **MVP path = Phases 1 + 2 + 3 + 5a** ("expose a Binate library
to C on a hosted target"); the rest are enhancements.

### Phase 0 — Ratify & specify the design (prerequisite, user-owned)
The design note is a proposal. Before implementation: ratify it, then write the spec
(rules for `c_export`, `bn_init`/`bn_entry`, the `platform_init` model + entry
selection, library/merge mode, the linker-placement annotation) and the DECIDED notes
in `claude-notes.md`. **Decision point:** does the user want to build this now, and in
what scope? (Nothing below is authorized by this plan alone.)

### Phase 1 — `bn_init`: well-known, build-root-rooted, idempotent (foundational)
- In `gen_init.bn`, promote the dispatcher to a **stable `bn_init` symbol**, rooted at
  the **build root's** transitive deps (a no-op refactor while the only root is `main`;
  the generalization is what Phase 5 needs). Add a **run-once idempotency guard**
  (decide storage: a guard global in the dispatcher vs. `rt`). Keep `bn_entry` =
  `bn_init()` + `main.main()`.
- **Deliverable:** existing programs behave identically; `bn_init` is a stable symbol
  callable by name. **Deps:** none. **Unblocks:** 5, 6.
- **Risks:** guard placement; making the dispatcher root-agnostic without disturbing
  the current main-rooted path.

### Phase 2 — `#[c_export("name")]` recognition (frontend)
- Recognize `c_export` as an unqualified annotation (like `build`); validate: attaches
  to a top-level **func** decl, argument(s) are **string literals**, the function is
  **package-public**. Carry the C name(s) onto the function's IR node.
- **Deliverable:** the annotation parses/validates and the name reaches codegen (no
  emission yet). **Deps:** none (parallel with Phase 1). **BUILDER:** §4.

### Phase 3 — c_export symbol emission (backends)
- Emit the **additional unmangled** C symbol aliasing the function — LLVM first, then
  native x64/aarch64 — reusing the `__c_call` verbatim-symbol discipline. The mangled
  Binate symbol stays.
- **Deliverable:** `#[c_export("foo")] func Foo()` yields a callable C symbol `foo`.
  **Deps:** 2. **This is the MVP-critical emission piece.**
- **Risks:** alias mechanics differ per object format (ties to `plan-macho-dysymtab.md`
  / `plan-linker.md`); getting all three backends is the bulk of the work.

### Phase 4 — Trivial-forward → symbol alias
- Recognize the signature-preserving forwarder shape and lower it to a **symbol alias**
  (not a call frame). Non-trivial (type-adapting) wrappers stay real functions.
- **Deliverable:** verbatim re-export and `_init` forwards are zero-cost. **Deps:** 3
  (alias mechanism). **Deferrable:** forwarders work as real calls first, but this is
  load-bearing for the "facade of 200 forwarders" library case.

### Phase 5 — Library / merge build mode (driver + loader)
- **5a (single):** `bnc --library <loc>` — compile a facade as a **non-`main` build
  root** (no `main` required), emit `bn_init` over its dep closure, produce a linkable
  artifact (`.a`/`.so`). Needs the loader to accept a non-`main` root + the driver to
  skip main-entry emission and emit a library artifact.
- **5b (merge):** `--library A --library B` — union files into one package, pull shared
  deps **once**, enforce **disjoint** names.
- **Deliverable:** a Binate library callable from C (via a c_export'd `_init`). **Deps:**
  1, 2, 3 (5b after 5a). **Risks:** the driver-heaviest piece — dep-set resolution from
  a non-`main` root, disjoint-name enforcement, shared-dep-once linking, entry-selection
  rule. Overlaps `plan-embeddable-interp.md`.

### Phase 6 — `pkg/builtins/platform_init`; retire `binate_runtime.c`
- Create the package with the hosted C-`main` entry (`#[c_export("main")]`, build-gated
  linux/darwin, argv → `bn_argc`/`bn_argv`, calls `bn_entry`). Wire the build to
  **force-include/link** it as the entry instead of `binate_runtime.c`, then remove the
  C file.
- **Deliverable:** hosted programs start through Binate glue; `binate_runtime.c` gone.
  **Deps:** 1, 2, 3. **Risks:** **high blast radius** — this changes startup for *every*
  compiled binary (incl. self-hosted `bnc` gen1/gen2); the "force-include the entry
  package" link mechanism; argv-capture as Binate globals; bootstrapping ordering (§4).
  Consider landing behind a flag / alongside the C file before flipping the default.

### Phase 7 — Header generator
- Emit a first-cut C header from the facade's export signatures (prototypes + typedefs:
  `bn_slice` / `bn_managed_slice` / `bn_iface` / the **reversed** func-value struct),
  with author overrides for wrapped exports.
- **Deliverable:** `.h` accompanying a `--library` artifact. **Deps:** 2, 5.

### Phase 8 — Linker-placement annotation + baremetal entry
- Add the linker-placement annotation (`#[section(".init")]` / `#[link_at(addr)]`, spell
  TBD) reaching the backend/linker; add a baremetal `_start` variant in `platform_init`.
- **Deps:** 2 (annotation infra), 6. Overlaps `plan-arm32-bare-metal.md` /
  `plan-linker.md`.

### Phase 9 — Signature lint (optional)
- A **bnlint** rule flagging signatures unusable-in-practice from C (e.g. function-value
  params needing the trampoline). Independent; not an ABI gate.

## 4. Cross-cutting concerns

- **BUILDER bootstrapping (get the order right).** Phase 2 adds `c_export` *recognition*
  to `cmd/bnc`'s BUILDER-compiled tree — that is just logic (a name check), so it stays
  BUILDER-compilable and needs **no BUILDER bump**, **provided `cmd/bnc`'s own source
  never uses `#[c_export]`** (only `platform_init` and user facades do, and those are
  compiled by the *new* bnc, which recognizes it). Verify against the pinned BUILDER
  before landing (per the CLAUDE.md "verify BUILDER supports a new feature" rule). Phase
  6's `platform_init` must be compiled by a post-Phase-2 bnc — fine in the normal
  self-compile chain, but it means Phase 6 lands **after** 2/3.
- **Three backends.** Every emission phase (3, 4, 8) multiplies across LLVM + native
  x64 + native aarch64 (+ arm32 later). Smoke-test **each changed backend package**
  (the shared-file rule), not one representative.
- **Testing infrastructure gap.** Conformance today runs *Binate* programs; "C links
  Binate and calls in" needs a **new C test harness** (compile a small C driver, link
  the `--library` artifact, run, check output). This harness is itself a deliverable
  (probably under `e2e/` or a new conformance mode) and gates real end-to-end coverage
  of Phases 3/5/6/7. Unit-test the `gen_init` (Phase 1), annotation-recognition (Phase
  2), and alias-emission (Phase 3) changes directly.
- **Init-order dependency.** `bn_init`'s within-package ordering rides on the open
  declaration-order-vs-dependency-order spec question (`claude-todo.md`); resolve/point
  to that so the semantics are pinned, not incidental.

## 5. Risks & open questions

- **Entry-selection rule.** "Which entry is wired up characterizes the build" needs a
  concrete driver rule: what if **zero** matching entries? what if **multiple** (a
  `main` and a `_init` under one `#[build]` gate)? Exactly-one-enforced, or
  combinations-allowed? (Flagged in the design review as under-specified — decide in
  Phase 0.)
- **Force-including `platform_init`.** Nothing imports it; the driver/linker must pull
  it in as the entry. Mechanism TBD (a driver-injected root vs. a link flag).
- **`_init`-as-alias vs. real function.** A bare `{ bn_init() }` `_init` aliases
  `bn_init` (Phase 4); one doing per-library setup stays real. The driver/header must
  handle both.
- **Merge semantics.** Disjoint-name enforcement granularity; how a shared dep pinned to
  two versions is detected (the version-skew hazard merge exists to prevent); whether a
  merged unit exposes multiple `_init`s or one synthesized init.
- **Idempotency storage** and whether re-entrant embedding is in scope.
- **Suppress-mangled-name** (a separate visibility/dead-strip feature) — out of scope
  here but interacts with the export surface.
- **rt refcount entry points** (`RefInc`/`RefDec`) — whether/how to expose them to C
  callers that retain managed values.

## 6. Recommended first slice

If/when Phase 0 clears, land the **MVP path** in order — **1 → 2 → 3 → 5a** — behind
the new C test harness, producing "a hosted Binate `.a` a C program can init once and
call into." Then take 4 (zero-cost re-export) and 6 (retire `binate_runtime.c`, high
blast radius — stage it carefully), then 7/8/9 as the surface matures. Each phase is
independently landable and keeps the tree green.
