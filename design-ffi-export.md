# Design note: FFI Export — exposing Binate functions to C

**Status:** design note / proposal (2026-07-06). **Not** specified, **not**
implemented. Explores (a) what initialization is needed to link a Binate package
(+ transitive deps) into a C program and call a Binate function, and (b) how to
package a *set* of Binate packages as a C library. **No core-language / grammar
change is proposed** — the feature is an annotation + driver/build-mode + codegen.
Cross-refs: §17 (`prog.*` init/entry), §16.7 (`pkg.annotation`), §16.9
(`pkg.ccall`/`pkg.cglobal`, the FFI boundary), §7.13 (layout, for the C typedefs).
Naming (`c_export`, `--init-name`, etc.) is illustrative and bikeshedable.

## 1. Motivating use case

Expose an embeddable Binate interpreter (or any Binate library) as a **C library**
(`.a`/`.so` + a header): a C program links it, calls an init once, then calls
Binate functions. Crucially, the **packager** — not the individual Binate packages
— decides what to expose, under what C names, verbatim or wrapped for C
ergonomics. The Binate packages themselves stay C-agnostic (a package has no
business, and should have no opinion, about a C name).

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
  A package with no global initializers gets no `__init`.
- **Nothing else bootstraps for compiled code:** the allocator/refcount runtime is
  **demand-driven** (`rt.Alloc`/`Free`; a managed allocation starts at refcount 1),
  there is no string interning, immortal/static data is link-time `.rodata`, and
  interface vtables are **link-time weak symbols** (no init-time registration —
  vtable/descriptor *injection* exists only on the VM/dual-mode path, not for
  compiled-only execution).

**Consequence (the whole embedding answer in one line):** to call a Binate function
from C you only need to run every transitively-linked package's `__init` in
dependency order **once** (i.e. an `__init_all`), then call the function. There is
no allocator/refcount/vtable/descriptor handshake to orchestrate.

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

### 3.2 The library package = the C-library definition

- A **"library" package** is the compile/link/init root, symmetric with `main` but
  without the `main` entrypoint. `bnc` emits an `__init_all` over its transitive
  import set, reusing the main-mode machinery.
- It **centralizes the shape of the C library**: its **imports** define the package
  set; its **`#[c_export]` wrapper functions** define the export surface (verbatim
  or ergonomically wrapped); and it is the natural home for the **C header** — `bnc`
  has everything it needs there to emit a first-cut header (each export's signature
  → a prototype + the struct typedefs), which the author overrides for wrapped
  exports.
- Re-export/wrapping is the **primary** mode, not an escape hatch: the packager
  exposes packages they did not author, so the exports live as wrappers in the
  library package rather than as annotations scattered across (and modifying) the
  exposed packages.

### 3.3 Trivial-forward wrappers must be zero-cost

Because wrapping is the common case, a **signature-preserving** forwarder —
`#[c_export("bar")] func bar_(x T) R { return foo.Bar(x) }` — **must** lower to a
**symbol alias** (`bar` = `foo.Bar`'s mangled symbol) or a tail thunk, not a real
call frame. The compiler recognizes the trivial-forward shape; the linker does the
alias. Non-trivial (type-adapting) wrappers stay real functions. Without this,
"a library package of 200 one-line forwarders" would carry real per-call overhead;
with it, verbatim re-export is free.

### 3.4 Artifact-level knobs are build flags, not source

- The **init name** is a property of the **final artifact**, not of any library
  package. It is a `bnc` **build flag** (`--init-name`, default derived from the
  artifact), decided by whoever assembles the artifact. It is **not** a
  package-statement annotation — that is incompatible with merge (§3.5).
- **General principle:** a property that must be **unique per final artifact** (init
  name, an output symbol *prefix*, the header name) → a **build flag**; a **per-item
  export** (a function's C name) → **source** (the library package), and must be
  **disjoint** across merged libraries anyway, so there is nothing to reconcile.

### 3.5 Merge mode for co-existence

Multiple *separately-built* Binate C libraries cannot co-exist in one C binary:
each bundles its own copy of the runtime and of shared dependency packages, so a
**shared** dependency's package-init would run **twice** (double-initializing shared
globals — a real bug), and **version skew** (lib A links `pkg/std@v1`, lib B `@v2`)
would let weak-symbol dedup silently pick one version and hand the other lib the
wrong code.

**Merge is the robust answer:** `bnc --library locA --library locB …` treats their
files as **one** package (union, disjoint names required — already a precondition
for co-existence), pulling shared deps in **once**, with one runtime, one
`__init_all`, and one disjoint export surface. (This is also *why* the init name
can't be a library-level annotation: two merged libraries with different specified
names would give one merged init two irreconcilable names — §3.4.)

Idempotent per-package init + weak-symbol dedup could paper over the double-init and
duplication, but **does not fix version skew**, so it is a fragile partial
alternative; merge is preferred.

### 3.6 Signature rule: "C-ABI-replicable," not scalar/pointer-only

Binate already targets the **platform C ABI** (that is how `__c_call` interops), so
any signature whose types Binate passes is one C can declare a matching prototype
for — the C side supplies the typedefs:

| Binate type | C declaration |
|---|---|
| scalar | the matching C scalar |
| `*T` / `@T` | a pointer (the managed header sits at a negative offset; C sees a plain pointer) |
| `*[]T` | `struct { T* data; size_t len; }` (2 words) |
| `@[]T` | the 4-word `{data, len, backing, backingLen}` struct |
| `*Iface` / `@Iface` | `struct { void* data; void* vtable; }` (2 words) |
| struct | the matching struct, by-value / by-reference per the ≤16-byte cutoff (§7.13.11) |
| multi-return | the packed anonymous result struct, or sret |

So the export does **not** gate on "scalar or pointer." Two things become
**discipline / documentation, not an ABI restriction**:

1. **Managed values carry refcount ownership across the boundary.** A `@T`/`@[]T`
   handed to C is a **borrow** for the call (the usual rule); a C caller that
   *retains* it must call the rt `RefInc`/`RefDec` — exactly the discipline a Binate
   callee already has. Treating a managed value as an opaque struct/pointer is fine
   at the ABI level; the refcount contract is the caller's responsibility.
2. **Function-value parameters** are *passable* (the 2-word `{vtable, data}` struct)
   but awkward to *call* from C (they need the trampoline). "Hard to use," not
   "can't export."

Reject nothing at the ABI level; optionally **lint** a signature that is
unusable-in-practice from C.

## 4. Open questions / follow-ups

- The **header generator's** exact C type mapping and typedef names
  (`bn_slice` / `bn_managed_slice` / `bn_iface`), and how the author overrides them
  for ergonomic wrappers.
- The **trivial-forward → alias** optimization: compiler recognition of the shape,
  and the linker-alias mechanism per backend (LLVM alias / Mach-O `N_INDR` / ELF
  `.set`).
- The **merge driver** ergonomics: repeated `--library <loc>`, disjoint-name
  enforcement, shared-dep-included-once linking, and how the merged unit's
  dependency set is resolved.
- Whether/how to expose the **rt refcount entry points** (`RefInc`/`RefDec`) to C
  callers that retain managed values.
- `bn_argc` / `bn_argv` for embedded use (only matters if exported code calls
  `Args()`), and whether an embedded (non-`main`) build should set them.
- Interaction with a future **"suppress mangled name"** (a separate visibility /
  dead-strip concern).
- **BUILDER compatibility:** none of this touches `cmd/bnc`'s own tree (it is
  driver/codegen + a new annotation the compiler recognizes), so no BUILDER
  constraint — but the new `c_export` annotation must be recognized by the compiler
  building the library, and library/merge modes are new driver surface.

---

## Appendix A: Discussion log (ideas considered, incl. discarded)

The design above is the end state of a discussion. Recording the alternatives and
the reasoning so they are not re-litigated.

**Starting questions.** (1) What per-package initialization happens, and in what
order? → §2. (2) To link Binate into a C program and call a function, what init is
needed? → the `__init_all` answer in §2; the key realization is that compiled code
needs *no* allocator/refcount/vtable bootstrap (demand-driven / weak-symbol /
rodata), so embedding reduces to "run the package var-initializers in dependency
order, then call in."

**Export mechanism — declaration vs. annotation.**
- *Considered: a dedicated `export` declaration* (`export foo.Bar as "c_bar"`, or an
  alias-shaped `c_export "c_bar" = foo.Bar` mirroring `type X = Y`). **Pro:** exports
  a function from a package you don't own **without editing it**; the library
  package is its natural home; additional names are natural. **Con:** it adds
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
  (the packager wraps others' code centrally in the library package). This is
  acceptable **only because** trivial forwarders lower to symbol aliases (§3.3), so
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

**The library package.** *Endorsed* as the C-library definition (imports = the set;
`#[c_export]` wrappers = the surface; header home; `__init_all` root). It cleanly
handles a *set* of packages and separate-package compilation, and it keeps the
exposed packages C-agnostic.

**Init-name location.** *Considered, then discarded: a `package`-statement annotation.*
It reads well in isolation, but it is **incompatible with merge**: merging library
packages A (init `libA_init`) and B (init `libB_init`) yields one merged init with
two irreconcilable names. *Chosen: a build flag* (`--init-name`), because the init
name is a property of the **final artifact**, which merge is exactly the operation
that produces. This generalized into the artifact-vs-source split (§3.4).

**Signature constraint.** *Discarded: "scalar or pointer only" (the strict `__c_call`
mirror).* Too strict — since Binate uses the platform C ABI, aggregates / managed
values / slices / interface values are all C-declarable with the right typedefs.
*Chosen:* "C-ABI-replicable" (≈ always), with the **managed-refcount discipline** and
the **function-value-arg** usability caveat as documentation, not an ABI gate (§3.6).

**Co-existence of multiple Binate C libraries.** The problem: separately-built libs
duplicate the runtime + shared deps → shared-dep **double-init** + **version skew**.
*Considered: idempotent per-package init + weak-symbol dedup* — **discarded** as
fragile (does not fix version skew). *Chosen: merge* — `bnc --library A --library B`
compiles their files as one package (disjoint names), shared deps once, one
runtime/init. This is also what forced the init name to a build flag.

**Blank imports / unused imports (a side question, resolved by investigation).**
Binate does **not** make an unused import a compile error (the checker has no such
check); it is a **bnlint** concern (`unused-import`), suppressible. **`import _
"pkg/foo"` is supported** (the grammar's `[ identifier ]` alias admits `_`; bnlint
explicitly exempts blank imports as intentional side-effect imports). Since Binate
has no `init()`, a blank import's "side effect" is pulling the package (+ deps) in
so its **package-var initializers run** — a ready-made primitive for a library
package to include a package for init/exposure without naming it.
