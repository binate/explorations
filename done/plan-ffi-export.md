# Plan: Implementing FFI Export (exposing Binate to C)

**Status:** high-level plan (2026-07-09). The **design** lives in
**[design-ffi-export.md](design-ffi-export.md)** (a **proposal** — reworked and
adversarially reviewed, but **not yet ratified, not specified, not implemented**).
This document is the implementation roadmap **contingent on the design being
ratified/specified** (Phase 0); it does not re-litigate the design. A future
edit-site-level detailed plan (`plan-ffi-export-detailed.md`) would follow, grounded
in a repo-wide survey, once Phase 0 clears. This plan was itself adversarially
reviewed against the codebase; the phase scoping below reflects that pass.

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
- **`bn_init` / `bn_entry`** — two **hardcoded well-known glue symbols** (kept
  hardcoded, **not** annotation-/extern-designated — design Appendix A). `bn_init` runs
  every package's `__init` in dependency order over the **build root's** transitive deps
  (the promotion of today's `main`-rooted `main.__init_all`), and is **idempotent**
  (run-once guard). `bn_entry` = `bn_init()` then `main.main()`.
- **`pkg/builtins/platform_init`** — a new builtins package (sibling to `rt`) holding
  **build-conditional** (`#[build(...)]`) entry/startup functions: a hosted C-`main`
  (`#[c_export("main")]`, argv capture, calls `bn_entry`) that **retires
  `runtime/binate_runtime.c`**; a baremetal `_start` placed via a linker-placement
  annotation; a library `_init` (`#[c_export("…_init")] { bn_init() }`). Which entry
  is wired up characterizes the build (program / library / freestanding) — no mode
  flag.
- **Library / library-union build mode** — `bnc --library <loc>` compiles a **facade
  package** (imports = the package set, `#[c_export]` wrappers = the surface, `_init` →
  `bn_init`, no `main`) into a linkable artifact; `--library A --library B` **unions**
  *multiple packages* into one build unit (shared deps once, disjoint names, one
  runtime/`bn_init`) — distinct from the existing intra-package file `merge`.
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
  `HasPackageInit`. **The dispatcher is `main`-rooted today** — no idempotency guard, no
  stable `bn_init` name.
- **C entry glue *and* hosted process/IO shims** — `runtime/binate_runtime.c`: the C
  `main()` stores `argc`/`argv` and calls `bn_entry`, **and** it *also* defines the
  mangled `bootstrap.*` symbols (`…_Write` / `…_Args` / `…_Exec`, at
  `binate_runtime.c:112/123/147`) that **every hosted program links** for I/O, arg
  access, and subprocess exec (bare metal resolves these instead via
  `pkg/bootstrap/bootstrap.bn`). Retiring the file therefore touches far more than the
  entry shim — see Phase 6.
- **Annotation system** — `pkg.annotation` (§16.7): `#[...]` blocks recognized on the
  package clause / import / top-level decl. Unqualified-annotation recognition lives in
  **`pkg/binate/buildcfg`** (`DeclIncluded`, `buildcfg.bn:16`), called from the
  **loader** (`loader.bn:209`, `buildconfig.bn:25/41/69`) — *before* the checker/IR, not
  in the checker. The **only** unqualified (compiler-required) annotation today is
  **`build`**; an unknown unqualified name is a **compile error** (the typo check).
  `DeclIncluded` is the **build-inclusion gate**, so a new `c_export` must be recognized
  there as **always-included** (never misread as a `build`-style drop predicate).
  `buildcfg` is itself in `cmd/bnc`'s BUILDER-compiled tree (imported by
  `cmd/bnc/target.bn:187`) — so the BUILDER caveat (§4) applies to the recognition edit.
- **Verbatim-symbol path** — `__c_call` (`plan-c-call.md`) already emits C symbol
  names **unmangled/verbatim** through the backends' platform-C-ABI lowering. c_export's
  emitted alias reuses that "verbatim, no `bn_` mangling" discipline (`pkg/mangle` +
  the backends).
- **Backends** — LLVM (`pkg/codegen`) + native x64/aarch64 (`pkg/binate/native/*`), the
  latter emitting object files *through* the symbol-table emitters
  **`pkg/binate/asm/macho`** and **`pkg/binate/asm/elf`**. **No symbol-alias primitive
  exists anywhere yet** — so alias emission is net-new in all three object paths (LLVM
  alias / Mach-O `N_INDR` in `asm/macho` / ELF `.set` in `asm/elf`), not merely
  "differs per format."
- **Driver / loader** — `cmd/bnc` + `pkg/loader`: the loader computes the transitive
  import closure and `ldr.Order`. A **non-`main` build root already exists**:
  `bnc --pkg` (`cmd/bnc/main.bn:79`, `compileSinglePkg` at `compile.bn:81`) loads a
  non-`main` package as a root, resolves its transitive closure (`LoadImports` +
  `ensureRuntimeDepsLoaded`), typechecks, and emits its object — and main-entry emission
  is already gated on `IsMainPackage()` (`main.bn:227-241`), so `--pkg` skips it. What is
  **missing**: `--pkg` emits a *single package's* object (`GeneratePackage(pkg.Merged)`),
  **not** a `bn_init` dispatcher over the closure; there is **no** library-artifact
  (`.a`/`.so`) packaging and **no** multi-package library-union mode. (Note also that
  `loader.MergeFiles` / `pkg.Merged`, `compile.bn:303`, is the *intra-package* file
  merge — unrelated to the cross-package library union of Phase 5b.)
- **Init-order caveat** — within-package var init is **declaration-order** (an open
  spec item in `claude-todo.md`); `bn_init` inherits whatever that resolves to (§4).
- **No unused-import check in the compiler** (bnlint-only); `import _` is supported —
  a ready primitive for a facade to pull a package in for init/exposure.

## 3. Phases

Ordered by dependency. **MVP path = Phases 1 + 2 + 3 + 5a** ("expose a Binate library
to C on a hosted target"); the rest are enhancements.

### Phase 0 — Ratify & specify the design (prerequisite, user-owned)
The design note is a proposal. Before implementation: ratify it, then write the spec
(rules for `c_export`, `bn_init`/`bn_entry`, the `platform_init` model + entry
selection, library/library-union mode, the linker-placement annotation) and the DECIDED
notes in `claude-notes.md`. Also **decide the C-test-harness home** (§4) and **resolve
the init-order spec item** (§4), both of which gate later phases. **Decision point:**
does the user want to build this now, and in what scope? (Nothing below is authorized by
this plan alone.)

### Phase 1 — `bn_init`: well-known, idempotent symbol (foundational)
- In `gen_init.bn`, promote the dispatcher to a **stable `bn_init` symbol** and add a
  **run-once idempotency guard** (decide storage: a guard global in the dispatcher vs.
  `rt`). Keep `bn_entry` = `bn_init()` + `main.main()`.
- **Scope split:** Phase 1 promotes the *symbol name + guard* for the still-`main`-rooted
  path (behaviorally a no-op for existing programs); generalizing the dispatcher to a
  **non-`main` build root's transitive closure** lands in **Phase 5a** (the MVP library
  needs that closure dispatch, or its `bn_init` under-initializes).
- **Deliverable:** existing programs behave identically; `bn_init` is a stable symbol
  callable by name. **Deps:** none. **Unblocks:** 5, 6.
- **Risks:** guard placement; **the init-order spec item must be resolved first (§4)** —
  `bn_init` becomes public ABI, so its ordering semantics can't ride an open question.

### Phase 2 — `#[c_export("name")]` recognition (frontend)
- Recognize `c_export` as an unqualified annotation in **`pkg/binate/buildcfg`**
  (`DeclIncluded` + `buildcfg.bni`) as **always-included** (not a `build`-style drop
  predicate), and validate: attaches to a top-level **func** decl, argument(s) are
  **string literals**, the function is **package-public**. Thread the C name(s) from the
  `ast.Annotation` through the loader onto the function's IR node.
- **Deliverable:** the annotation parses/validates and the name reaches codegen (no
  emission yet). **Deps:** none *design-wise*, but the change spans `ast` → `buildcfg` →
  loader → types/IR (**not** a single site). **BUILDER:** the recognition edit lands in
  the BUILDER-compiled `buildcfg`, so verify the pinned BUILDER parses it (§4).

### Phase 3 — c_export symbol emission (backends)
- Emit the **additional unmangled** C symbol aliasing the function — LLVM first, then
  native x64/aarch64 — reusing the `__c_call` verbatim-symbol discipline. The mangled
  Binate symbol stays.
- **Net-new surface:** the symbol-alias primitive does not exist yet. Add it in each
  object path — an LLVM alias (`pkg/codegen`), a Mach-O `N_INDR` record in
  **`pkg/binate/asm/macho`**, and an ELF `.set`/alias record in **`pkg/binate/asm/elf`**
  (+ their `.bni`s) — which the native backends emit through.
- **End-to-end verification needs the C harness (§4).** The C-links-Binate harness is on
  the MVP critical path — it's how Phases 3/5/6/7 are actually verified — so stand it up
  as a named deliverable (home decided in Phase 0), not a later afterthought.
- **Deliverable:** `#[c_export("foo")] func Foo()` yields a callable C symbol `foo`.
  **Deps:** 2. **This is the MVP-critical emission piece.**
- **Risks:** the alias primitive is net-new in three object paths (ties to
  `plan-macho-dysymtab.md` / `plan-linker.md`); getting all three is the bulk of the work.

### Phase 4 — Trivial-forward → symbol alias
- Recognize the signature-preserving forwarder shape and lower it to a **symbol alias**
  (reusing Phase 3's per-object-path alias primitive in `asm/macho` / `asm/elf` /
  `pkg/codegen`), not a call frame. Non-trivial (type-adapting) wrappers stay real
  functions.
- **Deliverable:** verbatim re-export and `_init` forwards are zero-cost. **Deps:** 3
  (alias primitive). **Deferrable:** the MVP library `_init = { bn_init() }` is a real
  one-call frame until this phase — which is **correct and sufficient for MVP**; this
  phase is load-bearing for the "facade of 200 forwarders" case, not for MVP correctness.

### Phase 5 — Library / library-union build mode (driver + loader)
- **5a (single):** `bnc --library <loc>` — **builds on the existing `--pkg` /
  `compileSinglePkg` non-`main`-root path** (`compile.bn:81`), which already loads the
  facade, resolves its closure, and skips main-entry emission. The genuinely-missing
  work is narrower than a from-scratch driver mode: (a) emit a **`bn_init` dispatcher
  over the facade root's transitive closure** (`--pkg` today emits only the single
  package's object via `GeneratePackage(pkg.Merged)`, not a closure-wide dispatcher —
  this is the Phase-1 dispatcher generalization actually landing here), and (b) package a
  linkable `.a`/`.so` artifact.
- **5b (library union):** `--library A --library B` — union *multiple packages'* files
  into one build unit, pull shared deps **once**, enforce **disjoint** names. **NB: this
  is a different operation from the existing intra-package `loader.MergeFiles` /
  `pkg.Merged`** (`compile.bn:303`), which unions *one package's several `.bn` files* —
  do not assume that machinery is reusable as-is. The "library union" name avoids the
  collision.
- **Init/export C names are source, not build flags.** The library's init name is the
  `c_export` name on its `_init` (per design §3.5); build flags select **only** input
  dirs, the union set, output path, and header name — do **not** add an `--init-name`
  flag (rejected, design Appendix A).
- **Deliverable:** a Binate library callable from C (via a c_export'd `_init`). **Deps:**
  1, 2, 3 (5b after 5a). **Risks:** *lower than a from-scratch mode* (5a is mostly
  closure-init + artifact packaging on top of `--pkg`); the real work is disjoint-name
  enforcement, shared-dep-once linking, and the entry-selection rule (§5). Overlaps
  `plan-embeddable-interp.md`.

### Phase 6 — `pkg/builtins/platform_init`; retire `binate_runtime.c`
- Create the package with the hosted C-`main` entry (`#[c_export("main")]`, build-gated
  linux/darwin, argv → `bn_argc`/`bn_argv`, calls `bn_entry`). Wire the build to
  **force-include/link** it as the entry instead of `binate_runtime.c`.
- **Retiring `binate_runtime.c` is not just the entry shim.** The file *also* provides
  the hosted `bootstrap.*` process/IO shims (`Write`/`Args`/`Exec`, at
  `binate_runtime.c:112/123/147`) that every hosted program links against. Removing it
  strands those symbols and breaks every hosted binary (incl. self-hosted `bnc`) unless
  they are relocated first — either kept as C in a `pkg/builtins/*` stub, or given Binate
  impls (the baremetal semihosting versions in `pkg/bootstrap/bootstrap.bn` are a
  *starting point*, not drop-in: hosted needs libc `write`/`fork`/`execvp`). This is
  roughly **half of Phase 6's real work** and gates removing the C file.
- **Deliverable:** hosted programs start through Binate glue *and* the hosted process/IO
  shims have a non-`binate_runtime.c` home; the C file is gone. **Deps:** 1, 2, 3.
  **Risks:** **high blast radius** — changes startup *and* the IO/exec shim linkage for
  *every* compiled binary (incl. self-hosted `bnc` gen1/gen2); the "force-include the
  entry package" link mechanism; argv-capture as Binate globals; bootstrapping ordering
  (§4). Stage it: land `platform_init` + the shim relocation *alongside* the C file, and
  flip the default (removing the C file) only once the whole chain is green.

### Phase 7 — Header generator
- Emit a first-cut C header from the facade's export signatures (prototypes + typedefs:
  `bn_slice` / `bn_managed_slice` / `bn_iface` / the **reversed** func-value struct),
  with author overrides for wrapped exports.
- **Deliverable:** `.h` accompanying a `--library` artifact. **Deps:** 2, 5.

### Phase 8 — Linker-placement annotation + baremetal entry
- Add the linker-placement annotation (`#[section(".init")]` / `#[link_at(addr)]`, spell
  TBD) reaching the backend/linker; add a baremetal `_start` variant in `platform_init`
  that hand-rolls `bn_init(); main.main(); halt()` — **not** `bn_entry` (bare metal has
  no hosted return/`exit` path; it must `halt` after `main`, per design §3.3).
- **Deps:** 2 (annotation infra), 6. Overlaps `plan-arm32-bare-metal.md` /
  `plan-linker.md`.

### Phase 9 — Signature lint (optional)
- A **bnlint** rule flagging signatures unusable-in-practice from C (e.g. function-value
  params needing the trampoline). Independent; not an ABI gate.

## 4. Cross-cutting concerns

- **BUILDER bootstrapping (get the order right).** Phase 2's `c_export` *recognition*
  lands in `pkg/binate/buildcfg` — which **is** in `cmd/bnc`'s BUILDER-compiled tree
  (imported via `cmd/bnc/target.bn:187`). The recognition is just logic (a name check),
  so it stays BUILDER-compilable and needs **no BUILDER bump** — but the **pinned BUILDER
  must still parse the amended `buildcfg`** (per the CLAUDE.md "verify BUILDER supports a
  new feature" rule), and this holds only **provided `cmd/bnc`'s own source never uses
  `#[c_export]`** (only `platform_init` and user facades do, and those are compiled by
  the *new* bnc, which recognizes it). Phase 6's `platform_init` must be compiled by a
  post-Phase-2 bnc — fine in the normal self-compile chain, but it means Phase 6 lands
  **after** 2/3.
- **Three backends.** Every emission phase (3, 4, 8) multiplies across LLVM + native
  x64 + native aarch64 (+ arm32 later), and the native side lands **in the `asm/macho` /
  `asm/elf` emitters** (§2). Smoke-test **each changed backend/emitter package** (the
  shared-file rule), not one representative.
- **Testing infrastructure gap (a named, early deliverable — not a footnote).**
  Conformance today runs *Binate* programs; "C links Binate and calls in" needs a **new C
  test harness** (compile a small C driver, link the `--library` artifact, run, check
  output). It **gates real end-to-end coverage of Phases 3/5/6/7**, so it sits on the MVP
  critical path — stand it up as a deliverable (home — `e2e/` vs. a new conformance mode
  — **decided in Phase 0**). Unit-test the `gen_init` (Phase 1), annotation-recognition
  (Phase 2), and alias-emission (Phase 3) changes directly.
- **Init-order dependency (a Phase-0/1 gate, not a pointer).** `bn_init`'s within-package
  ordering rides on the open declaration-order-vs-dependency-order spec question
  (`claude-todo.md`). Because `bn_init` becomes a **stable public ABI symbol**, this must
  be **resolved before Phase 1 lands** — once shipped, a later spec flip would change
  `bn_init`'s semantics under already-built binaries.

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
- **Library-union semantics.** Disjoint-name enforcement granularity; how a shared dep
  pinned to two versions is detected (the version-skew hazard union exists to prevent);
  whether a unioned library exposes multiple `_init`s or one synthesized init.
- **Idempotency storage** and whether re-entrant embedding is in scope.
- **Suppress-mangled-name** (a separate visibility/dead-strip feature) — out of scope
  here but interacts with the export surface.
- **rt refcount entry points** (`RefInc`/`RefDec`) — whether/how to expose them to C
  callers that retain managed values.

## 6. Recommended first slice

If/when Phase 0 clears, land the **MVP path** in order — **1 → 2 → 3 → 5a** — behind
the new C test harness, producing "a hosted Binate `.a` a C program can init once and
call into." Phase 5a is lighter than first feared (it builds on `--pkg`); the two
heaviest single items are the net-new alias primitive across three object paths (Phase
3) and retiring `binate_runtime.c` including its I/O shims (Phase 6, high blast radius —
stage it carefully). Then take 4 (zero-cost re-export), 6, and 7/8/9 as the surface
matures. Each phase is independently landable and keeps the tree green.
