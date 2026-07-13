# Design note: FFI Export — exposing Binate functions to C

**Status:** **RATIFIED design (2026-07-11)** — recorded as a DECIDED note in
`claude-notes.md`. The core-language features are **spec'd as pending** (Draft/pending in
the spec — specified, not implemented, like §16.9's `__c_global`); **not yet implemented**.
Explores (a) what initialization is needed to link a Binate package
(+ transitive deps) into a C program and call a Binate function; (b) how to package
a *set* of Binate packages as a C library; and (c) how the program **entry/startup
glue itself** can be Binate code, retiring `runtime/binate_runtime.c`. **No
core-language / grammar change is proposed** — the feature is annotations (which fit
the existing annotation grammar) + a `pkg/builtins/startup` package +
driver/codegen. Cross-refs: §17 (`prog.*` init/entry), §16.7 (`pkg.annotation`),
§16.8 (`pkg.build` constraints — the entry glue is build-conditional), §16.9
(`pkg.ccall`/`pkg.cglobal`, the FFI boundary), §7.13 (layout, for the C typedefs),
§11.10 / §20.2 (the `lang` / `rt` builtins carve-outs). Naming (`c_export`,
`bn_init`, `link_at`, …) is illustrative and bikeshedable — except the
entry/startup package name, now decided: `startup` (2026-07-13).

**Ratified (2026-07-11) — two decided *properties* (spellings still adjustable):** (1) the
FFI annotations (`c_export`, and the linker-placement `section`/`link_at`) are **unqualified,
compiler-recognized** annotations (language-standard, joining `build` — every conformant
compiler must recognize them and reject typos; they are **not** ignorable `tool.*` metadata,
and their newness gates them behind a BUILDER bump per §4). (2) `bn_init` / `bn_entry` are a
**linkage-ABI contract referenceable by literal name** — like today's real `bn_entry` — so the
`bn_`-family + literal-name-referenceability is decided even though the exact identifiers stay
adjustable. Everything else marked "illustrative" is genuinely bikeshedable.

## 1. Motivating use cases

Two, sharing the same mechanism:

1. **Expose Binate as a C library.** An embeddable Binate interpreter (or any Binate
   library) shipped as a `.a`/`.so` + header: a C program links it, calls an init
   once, then calls Binate functions. Crucially, the **packager** — not the
   individual Binate packages — decides what to expose, under what C names, verbatim
   or wrapped for C ergonomics. The Binate packages stay C-agnostic (a package has
   no business having an opinion about a C name).
2. **Write the startup/entry glue in Binate.** The C `main()` that captures
   `argc`/`argv` and calls into Binate currently lives in `runtime/binate_runtime.c`.
   The same export mechanism lets that glue be **Binate code**, retiring the C file
   — which fits Binate's C-free goal (C is a bridge to existing systems, not
   architecture). Generalized, the *entry* (a hosted C `main`, a baremetal `_start`
   at a load address, or a library's init) becomes **build-conditional package
   code**, not compiler-hardcoded behavior (§3.3).

## 2. Background: what init a compiled Binate program needs (established)

From §17 + the actual codegen (`pkg/binate/ir/gen_init.bn`,
`runtime/binate_runtime.c`, `impls/core/common/pkg/builtins/rt/rt.bn`):

- **No `init()` functions** (`prog.no-init-func`). A package's *only* initialization
  is running its package-level `var x T = e` initializers (as `x = e`);
  no-initializer vars are zero-initialized (no work), constants are compile-time
  (no storage), managed globals allocate once and hold an owning reference for the
  program.
- **Order:** across packages, **dependency (topological) order** (imports before
  importers, acyclic; `prog.init.order`); within a package, **source-declaration
  order** (the compiler carries a `VarInitOrder`; blank `_` globals run after named).
- **Entry chain:** C `main()` (in `binate_runtime.c`) stores `argc`/`argv` and calls
  the reserved symbol **`bn_entry`** ( = `main.__entry`, emitted by `EmitMainEntry`),
  which calls **`main.__init_all()`** (emitted by `EmitInitDispatcher` — it calls
  each package's **`<pkg>.__init`** in dependency order) and then `main.main()`.
  A package with no global initializers gets no `__init`. *(The design below promotes
  `main.__init_all` to a well-known, hardcoded **`bn_init`** symbol so non-`main`
  build roots and the entry glue can call it by name — §3.3.)*
- **Nothing else bootstraps for compiled code:** the allocator/refcount runtime is
  **demand-driven** (`rt.Alloc`/`Free`; a managed allocation starts at refcount 1),
  there is no string interning, immortal/static data is link-time `.rodata`, and
  interface vtables are **link-time weak symbols** (no init-time registration —
  vtable/descriptor *injection* exists only on the VM/dual-mode path, not for
  compiled-only execution).

**Consequence (the whole embedding answer in one line):** to call a Binate function
from C you only need to run every transitively-linked package's `__init` in
dependency order **once** (i.e. `bn_init`), then call the function. There is no
allocator/refcount/vtable/descriptor handshake to orchestrate.

## 3. The design

### 3.1 `#[c_export("name")]` — an annotation that adds a C name

- A new **unqualified** (compiler-recognized, like `build`) annotation. It fits the
  existing `AnnotationEntry = AnnotationName [ "(" … ")" ]` grammar and attaches to
  a top-level function declaration — so **no core-grammar change**.
- It emits an **additional**, unmangled C symbol aliasing the function; the mangled
  Binate name is **unchanged**. Multiple entries/args → multiple C names.
  *Suppressing* the mangled name is a separate concern (visibility / dead-strip),
  out of scope here.
- Only a function that is already **package-public** (in its `.bni`) can be
  C-exported — two levels: package-visible, then C-named.

### 3.2 The library facade package = the export surface

- A **library facade package** centralizes the shape of the C library: its
  **imports** define the package set; its **`#[c_export]` wrapper functions** define
  the export surface (verbatim or ergonomically wrapped); its **`#[c_export]` `_init`
  function** is the C-callable initializer (§3.3); and it is the natural home for the
  generated **C header** — `bnc` has everything it needs there to emit a first-cut
  header (each export's signature → a prototype + the struct typedefs), which the
  author overrides for wrapped exports.
- Its "library-ness" is **not** a magic package name and **not** a package-clause
  annotation (both rejected — Appendix A). It is just: a package with `#[c_export]`
  wrappers and an `_init`, **no `main`**, handed to `bnc` as a build root (optionally
  merged — §3.6). Whether a build is a hosted program, a C library, or a freestanding
  image is **which entry/init function is wired up** (§3.3), not a mode flag on the
  package.
- Re-export/wrapping is the **primary** mode, not an escape hatch: the packager
  exposes packages they did not author, so the exports live as wrappers in the
  facade package rather than as annotations scattered across (and modifying) the
  exposed packages.

### 3.3 Entry and platform init are pluggable package code, not compiler-hardcoded

The compiler should **not hardcode** the entry (the C `main`, `argc`/`argv` capture,
exit) — those are *platform* facts. Instead:

- **The compiler emits two hardcoded well-known glue symbols.** **`bn_init`** runs
  every package's `__init` in dependency order over the **build root's** transitive
  deps (the root is `main` for a program, the facade package for a library — so this
  generalizes today's `main`-rooted `main.__init_all`); **`bn_entry`** is `bn_init()`
  then `main.main()` (the existing combined program entry). Both are `bn_`-family,
  referenceable by literal name — exactly the contract the C runtime already has for
  `bn_entry` today. (No designating annotation / extern indirection — Appendix A;
  keep them hardcoded until a real need arises.) `bn_init` is emitted **once per
  build unit**, which is precisely why co-linking two *separately-built* libraries
  would collide on `bn_init` (or weak-dedup to one — the version-skew hazard §3.6),
  and why merge (§3.6) is required.
- **The entry/startup functions live in a builtins package `pkg/builtins/startup`**
  — separate from `rt` (`rt` is runtime *services*: alloc/refcount/exit, §20.2;
  `startup` is the *entry/startup* glue). Being a builtins package it is **path-special** (may
  run pre-init, inherits the same bespoke treatment as the `lang` carve-out §11.10 /
  `rt` §20.2), and it holds **build-conditional** (`#[build(...)]`) entry functions:
  - **hosted / C runtime:**
    `#[c_export("main"), build(is(os,"linux") || is(os,"darwin"))] func _entry(argc int, argv **char) int { … capture argv into bn_argc/bn_argv …; bn_entry(); … }`
    — the C `main` replacement, retiring `binate_runtime.c`.
  - **baremetal:**
    `#[section(".init"), build(is(os,"baremetal"))] func _start() { bn_init(); main.main(); halt() }`
    — no argv, no C `main`; it hand-rolls `bn_init(); main.main()` rather than reusing
    `bn_entry` because there is no hosted return/`exit` path (after `main` it must
    `halt`, not return to a C runtime). A linker-placement annotation puts it where
    the reset vector / linker script expects (address assignment stays the linker
    script's job; the annotation names the section — or, if preferred,
    `#[link_at(addr)]` carries an address directly).
  - **C library:** `#[c_export("mylib_init")] func _init() { bn_init() }` — the host
    calls it; no `main`.
- **`#[build]` selects** the right entry per target, and the **set of wired-up entry
  symbols** characterizes the build — a `#[c_export("main")]` entry → a hosted
  program, a `_init` → a C library, a `#[section]`/`#[link_at]` `_start` → a
  freestanding image. Combinations are possible (a build could expose both a `main`
  and a library `_init`), so no separate "program vs library" mode flag is needed:
  the entry set *is* the mode.
- **These are all function-level annotations** (on the entry functions), so they have
  **no multi-file / package-clause problem** (Appendix A): a package-clause annotation
  is per-*file* in Binate, but an entry function is a single declaration.
- The two annotations this uses are `#[c_export]` (§3.1) and a **linker-placement**
  annotation (`#[section(".init")]` / `#[link_at(addr)]` — bikeshed) that propagates
  to the backend/linker for the baremetal case.

Independently of the Binate rewrite: whatever remains of `binate_runtime.c` belongs
**in a `pkg/builtins/*` package** anyway — it is runtime-floor code that merely
happens to live outside the runtime packages today. Moving it there (as C now) is
cohesion; rewriting it in Binate via `#[c_export("main")]` is the C-free end state.

### 3.4 Trivial-forward wrappers must be zero-cost

Because wrapping is the common case, a **signature-preserving** forwarder —
`#[c_export("bar")] func bar_(x T) R { return foo.Bar(x) }` — **must** lower to a
**symbol alias** (`bar` = `foo.Bar`'s mangled symbol) or a tail thunk, not a real
call frame. The compiler recognizes the trivial-forward shape; the linker does the
alias (the mechanism is backend-specific — LLVM alias / Mach-O `N_INDR` / ELF
`.set`; §4). Non-trivial (type-adapting) wrappers stay real functions. Without this,
"a facade package of 200 one-line forwarders" would carry real per-call overhead;
with it, verbatim re-export is free.

### 3.5 The init name lives in source; build flags only select inputs

- The C library's **init name** is the **`c_export` name on its `_init` function**
  (§3.3) — **source**, per-declaration, in the facade package. It is **not** a build
  flag: a facade names its own init, and merging facades (§3.6) does not conflict
  (each library keeps its own `_init`; all call the one shared `bn_init`). *(This
  supersedes an earlier design that made the init name a `--init-name` build flag to
  dodge a merge conflict — Appendix A. Once the init is an ordinary c_export'd
  function, that conflict never arises.)*
- **`bn_init` should be idempotent** (a run-once guard). Merge (§3.6) already prevents
  shared-dependency double-init *structurally* — one `bn_init` over one link set — so
  idempotency's *only* remaining job is to tolerate the **host** calling more than one
  library `_init` (each of which invokes the one shared `bn_init`), or re-entrant
  embedding.
- **Build flags** remain only for the genuinely build-invocation part: **which**
  directories to compile and **merge** into the artifact (§3.6), and other true
  build-invocation params (e.g. the output path and the generated header name).
  **General principle:** a property that must be **unique per final artifact** and is
  produced by *assembling* the artifact (e.g. the header name) → a **build flag**; a
  **per-item / per-declaration** property (a function's C name, the init's C name) →
  **source**, and must be **disjoint** across merged libraries anyway, so there is
  nothing to reconcile.

### 3.6 Merge mode for co-existence

Multiple *separately-built* Binate C libraries cannot co-exist in one C binary:
each bundles its own copy of the runtime and of shared dependency packages, so a
**shared** dependency's package-init would run **twice** (double-initializing shared
globals — a real bug), and **version skew** (lib A links `pkg/std@v1`, lib B `@v2`)
would let weak-symbol dedup silently pick one version and hand the other lib the
wrong code.

**Merge is the robust answer:** `bnc --library locA --library locB …` treats their
files as **one** package (union, disjoint names required — inherent to unioning the
files into one package), pulling shared deps in **once**, with one runtime and one
`bn_init`. The libraries' `_init` functions coexist (distinct `c_export` names); each
is a bare `{ bn_init() }` forward, so per §3.4 it lowers to an **alias** of the one
shared `bn_init` (a library whose `_init` does extra per-library setup stays a real
function instead). The host calls one; `bn_init`'s idempotency (§3.5) makes calling
several safe.

Idempotent per-package init + weak-symbol dedup can hide the double-init and
duplication *without* merge, but **version skew remains unaddressed**, so that is a
fragile partial alternative; merge is preferred.

### 3.7 Signature rule: "C-ABI-replicable," not scalar/pointer-only

Binate already targets the **platform C ABI** (that is how `__c_call` interops), so
any signature whose types Binate passes is one C can declare a matching prototype
for — the C side supplies the typedefs:

| Binate type | C declaration |
|---|---|
| scalar | the matching C scalar |
| `*T` / `@T` | a pointer (the managed header sits at a negative offset; C sees a plain pointer; caveat 1 applies to `@T`) |
| `*[]T` | `struct { T* data; size_t len; }` (2 words) |
| `@[]T` | the 4-word `{data, len, backing, backingLen}` struct (caveat 1 applies) |
| `*Iface` / `@Iface` | `struct { void* data; void* vtable; }` (2 words; caveat 1 applies to `@Iface`) |
| `*func` / `@func` value | `struct { void* vtable; void* data; }` — **reversed** field order vs. an interface value (caveat 2) |
| struct | the matching struct, by-value / by-reference per the ≤16-byte cutoff (§7.13.11) |
| multi-return | the packed anonymous result struct, or sret |

So the export does **not** gate on "scalar or pointer." Two things become
**discipline / documentation, not an ABI restriction**:

1. **Managed values carry refcount ownership across the boundary.** A `@T`/`@[]T`
   handed to C is a **borrow** for the call (the usual rule); a C caller that
   *retains* it must call the rt `RefInc`/`RefDec` — exactly the discipline a Binate
   callee already has. Treating a managed value as an opaque struct/pointer is fine
   at the ABI level; the refcount contract is the caller's responsibility.
2. **Function-value parameters** are *passable* (the 2-word `{vtable, data}` struct
   — deliberately the **reverse** field order of an interface value's
   `{data, vtable}`, §7.13.9, so a C typedef must match that order) but awkward to
   *call* from C (they need the trampoline). "Hard to use," not "can't export."

Reject nothing at the ABI level; optionally **lint** a signature that is
unusable-in-practice from C.

## 4. Open questions / follow-ups

- The **header generator's** exact C type mapping and typedef names
  (`bn_slice` / `bn_managed_slice` / `bn_iface`), and how the author overrides them
  for ergonomic wrappers.
- The **trivial-forward → alias** optimization: compiler recognition of the shape,
  and the linker-alias mechanism per backend (LLVM alias / Mach-O `N_INDR` / ELF
  `.set`).
- The **linker-placement annotation** (`#[section(".init")]` / `#[link_at(addr)]`):
  exact spelling, how it reaches the backend/linker, and its interaction with a
  baremetal linker script (which usually owns the address).
- **`bn_init` idempotency**: the run-once guard mechanism (a guard global) and where
  it lives (in the dispatcher itself).
- The **merge driver**: repeated `--library <loc>`, disjoint-name enforcement,
  shared-dep-included-once linking, and how coexisting `_init`s (vs. one synthesized init) are
  handled — *ergonomics*. **Correctness obligation (NOT ergonomics):** the merged unit's
  `bn_init` MUST cover the *correct* transitive closure in valid topological order — that is the
  soundness core of the embedding claim, not a nicety.
- The **`bn_init` / `bn_entry` division of labor** (init-only vs. init-then-`main`) —
  an implementation detail; nothing here forces the split now.
- Whether/how to expose the **rt refcount entry points** (`RefInc`/`RefDec`) to C
  callers that retain managed values.
- **`bn_argc` / `bn_argv`**: with the entry glue in `startup`, argv capture is
  that package's **build-conditional (hosted-only)** code, not compiler-hardcoded; a
  freestanding/library build simply omits it.
- Interaction with a future **"suppress mangled name"** (a separate visibility /
  dead-strip concern).
- **BUILDER compatibility:** the annotations are compiler-recognized and the
  driver/codegen is outside `cmd/bnc`'s tree, so no BUILDER-subset constraint — but
  the new annotations must be recognized by the compiler *building* the library, and
  `pkg/builtins/startup` is built like the other builtins.

---

## Appendix A: Discussion log (ideas considered, incl. discarded)

The design above is the end state of an extended discussion. Recording the
alternatives and the reasoning so they are not re-litigated.

**Starting questions.** (1) What per-package initialization happens, and in what
order? → §2. (2) To link Binate into a C program and call a function, what init is
needed? → the `bn_init` answer in §2; the key realization is that compiled code
needs *no* allocator/refcount/vtable bootstrap (demand-driven / weak-symbol /
rodata), so embedding reduces to "run the package var-initializers in dependency
order, then call in."

**Export mechanism — declaration vs. annotation.**
- *Considered: a dedicated `export` declaration* (`export foo.Bar as "c_bar"`, or an
  alias-shaped `c_export "c_bar" = foo.Bar` mirroring `type X = Y`). **Pro:** exports
  a function from a package you don't own **without editing it**; the facade package
  is its natural home; additional names are natural. **Con:** it adds
  **core-language syntax** (a new top-level declaration form) that the whole parser/
  checker must carry — against Binate's minimal-core value.
- *Chosen: an annotation `#[c_export("…")]`.* **Pro:** **no** grammar change (fits
  the existing annotation grammar); annotations are the deliberately-ignorable
  extension point; the C name lives with the function. **Con:** an annotation can
  only attach to *your own* declaration, so exporting foreign code needs a **wrapper
  function**.
- *The tie-breaker:* the "no new syntax" property won (the user's explicit
  priority). The wrapper cost was first thought **rare**, then **corrected** —
  the interpreter-as-a-C-library example shows re-export is the **primary** mode
  (the packager wraps others' code centrally in the facade package). This is
  acceptable **only because** trivial forwarders lower to symbol aliases (§3.4), so
  verbatim re-export is free.

**Additional vs. replacement name.** *Chosen: additional.* The C name is an extra
alias; the mangled Binate name stays (Binate callers unaffected; multiple C names
possible; the symbol still exists for Binate linkage). Suppressing the mangled name
is a separate visibility concern, deliberately out of scope.

**The name "export".** *Discarded: bare `export` / `#[export]`.* Binate already uses
"exported" for package-public symbols (the `.bni` surface), so `export` overloads
visibility. *Leaning: `c_export`* — in the `c_` FFI family with `__c_call` /
`__c_global`, unambiguous. *Also considered:* `abi` / `c_name` / `foreign`.
*Ruled out:* `extern` / `#[no_mangle]` — the spec explicitly reserves against those,
and `extern` already denotes the *host-provided* (`.bni` body-less) direction.

**The library facade package.** *Endorsed* as the C-library definition (imports = the
set; `#[c_export]` wrappers = the surface; header home; `_init` → `bn_init`). It
cleanly handles a *set* of packages and separate-package compilation, and it keeps
the exposed packages C-agnostic.

**Signaling the library/entry *mode* — a magic package name.** *Considered: a magic
package name* (`package "c_library"` / `"raw"` / `"bare"`, possibly sigil'd
`_c_library` / `.raw` / `__raw` to spare the namespace), extending the existing magic
`main` package. *Discarded* on three counts: (a) it **eats the package-name
namespace** (even sigil'd, it reserves names); (b) it **collides on merge** — a
package name is also the *identity* its symbols mangle from, so every library sharing
one magic name would emit clashing `<name>.__init` / function symbols; (c) the
`main`-precedent does **not** carry — `main` can be a magic name because it is
**singular** (one per program, never merged), whereas library packages are **plural
and merged**. So the mode must not live in the package name at all.

**Signaling the mode — a package-clause annotation.** *Considered: `#[c_library]
package "mylib"`* (a normal package name + a mode annotation). *Discarded* because a
package-clause annotation is already **per-file** in Binate — that is how `#[build]`
gates (§16.8 `pkg.build.gate` drops the whole *file*) — so a package-**level**
property placed there forces an accumulate / require-consistency / conflict mess
across a package's several files. The entry/platform config is instead
**function-level** (annotations on the entry functions — §3.3), which has no
multi-file problem. *(A genuinely package-level source contract, if one is ever
needed, would require a deliberately-specified per-package accumulation rule — union
across files, one file suffices, conflicting values an error — as a distinct
annotation kind from the per-file `#[build]` gate. Not needed here: the builtins
packages get their special properties by path already.)*

**A dedicated `raw` / `bare` package mode (for the entry glue).** *Considered: a
package mode that "compiles raw code, no init, no deps, force-included"* for writing
pre-init glue (the C `main` replacement). *Discarded as unnecessary:* the entry glue
lives in `pkg/builtins/startup`, and the builtins packages are **already
path-special** (the `lang` primitive-impl carve-out §11.10, `rt` as the runtime
contract §20.2), so the glue inherits the pre-init / raw treatment by *being there* —
no user-facing `raw` mode to invent, and the multi-file question above never arises.

**A designated dispatcher symbol (annotation / extern).** *Considered: exposing the
init dispatcher via a designating annotation on a body-less declaration*
(`#[init_dispatcher] extern func run_inits()`), so the name is the package's choice
rather than a hardcoded magic string. *Discarded:* Binate has **no `extern`
keyword** to carry it (externness is the body-less `.bni` form, §16.9), and a
bespoke annotation for exactly one symbol is not worth it. *Chosen:* keep **`bn_init`**
and **`bn_entry`** as hardcoded well-known symbols (the `bn_` family, matching
today's `bn_entry`) until a real need for indirection arises.

**Init-name location (evolved across three rounds).** (1) *Considered: a
`package`-statement annotation* — discarded as incompatible with merge (merging
libraries A `libA_init` and B `libB_init` yields one merged init with two
irreconcilable names). (2) *Then: a `--init-name` build flag* — chosen on an
artifact-vs-source principle (the init name is a per-artifact property, and merge is
what produces the artifact). (3) *Superseded (final): the init name is the `c_export`
name on the library's `_init` function* (§3.3/§3.5). Once the init is an ordinary
c_export'd function calling the well-known `bn_init`, the merge conflict evaporates
(each library names its own `_init`; all call one `bn_init`), so there is nothing to
force to a build flag. The build flag survives only for input-dir / merge selection.

**`binate_runtime.c` → a builtins package.** *Decided:* the C startup glue is
runtime-floor code that belongs in a `pkg/builtins/*` package (`startup`,
sibling to `rt`), not a standalone top-level C file — as C now (pure cohesion) or
rewritten in Binate later via `#[c_export("main")]` (the C-free end state).

**Signature constraint.** *Discarded: "scalar or pointer only" (the strict `__c_call`
mirror — §16.9 `pkg.ccall` restricts `__c_call`'s args/return to a C-ABI-passable
scalar or pointer).* Too strict for the export direction — since Binate uses the
platform C ABI, aggregates / managed values / slices / interface values are all
C-declarable with the right typedefs.
*Chosen:* "C-ABI-replicable" (≈ always), with the **managed-refcount discipline** and
the **function-value-arg** usability caveat as documentation, not an ABI gate (§3.7).

**Co-existence of multiple Binate C libraries.** The problem: separately-built libs
duplicate the runtime + shared deps → shared-dep **double-init** + **version skew**.
*Considered: idempotent per-package init + weak-symbol dedup* — **discarded** as a
fragile partial fix (does not address version skew). *Chosen: merge* — `bnc --library
A --library B` compiles their files as one package (disjoint names), shared deps
once, one runtime/init.

**Blank imports / unused imports (a side question, resolved by investigation).**
The **compiler** has **no** unused-import check (nothing in `pkg/binate/types` or
the loader), so an unused import is not a compile error; it is purely a **bnlint**
concern (the `unused-import` rule, `cmd/bnlint`), suppressible via `bnlint:allow`.
**`import _ "pkg/foo"` is supported** (the grammar's `[ identifier ]` alias admits
`_`; bnlint explicitly exempts blank imports as intentional side-effect imports).
Since Binate has no `init()`, a blank import's "side effect" is pulling the package
(+ deps) in so its **package-var initializers run** — a ready-made primitive for a
facade package to include a package for init/exposure without naming it.
