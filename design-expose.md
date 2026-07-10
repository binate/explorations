# Design note: `expose` — whole-package re-export

**Status:** design note / proposal (2026-07-10). **Not** ratified, **not** specified,
**not** implemented. A **core-language** declaration (not an annotation — see Appendix
A) that lets a package's `.bni` re-export another package's entire exported surface,
for **refactors/renames** (promote `pkg/stdx/foo` → `pkg/std/foo`, leaving a forwarder)
and **internal package structuring** (an aggregator presenting several packages as one
surface). Grounded in a codebase survey (the "current state" cites are real). Cross-refs:
§16.3 (`ImportSpec`), §16.4 (`pkg.export` — surface = `.bni` membership), §16.5 (the
`.bni` contract), §7 (`type.alias.transparency`), §11 (`impl`). The keyword `expose` is
settled; rule-IDs below are a sketch.

## 1. Motivating use cases

1. **Refactor / rename (the driver).** Promote `pkg/stdx/foo` to `pkg/std/foo` without
   breaking consumers: move the real package to `pkg/std/foo`, and leave
   `pkg/stdx/foo/foo.bni` as a **forwarder** — a `.bni` containing `expose "pkg/std/foo"`
   and **no `.bn`**. Existing `import "pkg/stdx/foo"; foo.Bar()` keeps working (same
   types, same functions — see identity below), so consumers migrate to the new path at
   leisure and the forwarder is deleted later. This is Go's type-alias code-repair story,
   generalized to package granularity.
2. **Internal package structuring.** An aggregator package exposes several internal
   packages through one unified surface, so consumers import one package instead of many.

## 2. The model — pure re-export, surface-only ("Model 2")

`expose "pkg/std/foo"` in package **A**'s `.bni` makes package **B** (= `pkg/std/foo`)'s
**entire exported surface part of A's exported surface**. Precisely:

- **Surface-only.** `expose` affects **only A's exported surface**. It does **not** bring
  B's names into A's own local scope — it is **not** a dot-import (Binate has none; §16.3
  references are always qualified). If A's `.bn` wants to *use* B, it writes an ordinary
  `import "pkg/std/foo"` separately. So "extend my surface" (expose) and "use B locally"
  (import) stay orthogonal, and **A's private (`.bn`-only) names can never collide with
  B's** — B simply isn't in A's scope unless A imports it, and then it's qualified `B.X`.
- **Identity-preserving (alias semantics, never a copy).** To anyone importing A, each
  public symbol `X` of B is reachable as `A.X`, **bound to B's original entity**: the
  same *type identity* for types, the same *storage* for vars, the same *symbol* for
  funcs, the same *value* for consts. So old code using `stdx/foo.Bar` and new code using
  `std/foo.Bar` interoperate — they are literally the same entity.
- **Flat.** Importers see `A.X`, **not** `A.B.X` — B's members become members of A's
  surface directly (packages are not values/members).
- **Transitive.** If B exposes C, and A exposes B, then A's surface includes C's symbols
  too. Cycles among `expose`/`import` edges are rejected (the existing import-graph cycle
  detector).
- **Conflicts are errors.** Two `expose`s that collide on a name → error; an exposed name
  that collides with A's **own exported** symbol → error. (Private names are never
  involved — surface-only.)

## 3. The design

### 3.1 Syntax — a `.bni` declaration
```
expose "pkg/std/foo"
```
A top-level declaration, permitted in the **`.bni`** (it defines part of the package's
public surface, which lives in the `.bni` per §16.4). It is **not** an import (it does
not affect A's local scope) and **not** an annotation (it is load-bearing semantics a
compiler must obey, not ignorable metadata — Appendix A). A forwarder package is a `.bni`
with one or more `expose` decls and **no `.bn`** — an already-accepted package shape
(`loader.bn:247-255`; the `InterfaceOnly` path exercises `.bni`-only packages today).

### 3.2 How identity is realized — and the func/var/const asymmetry (the crux)
Binate mangles the two symbol classes differently today, and `expose` has to reconcile
them:

- **Types and impls already follow identity.** A cross-package alias keys type identity
  and the vtable on the **resolved target's** `(pkg, name)`, not the source spelling
  (`ir/gen_impl_recvname.bn:39-51`; `types/types_query.bn` `ResolveAlias`; conformance
  `941_xpkg_alias_impl_dispatch`). So an exposed type is the same type as B's, and B's
  impls dispatch through it **for free** — no re-export of impls. (Exposed **interfaces**
  bind via the interface-identity form `interface X = Y`, since a bare `type X = Iface`
  alias is rejected — `types/resolve_type.bn:27-39`, test 486.)
- **Func/var/const references currently mangle from the *spelling*, not the resolved
  entity.** A reference `A.X` builds its symbol name from the literal package ident `A`
  (`ir/gen.bn:57-64` `resolveImportPkg` → `buildQualName`, `gen_util_literals.bn:36-42`;
  `funcRefName` `gen_util.bn:77-108`) — nothing consults where the resolved symbol lives.
  So `A.X` would mangle to a symbol **B never defines**, and a forwarder emitting nothing
  would leave exposed func/var references **dangling at link time**.

**Decision: make func/var/const qualified-reference mangling follow the resolved entity's
home package, not the source spelling.** Note this **wire does not exist today** and must
be built, not flipped: `resolveImportPkg` (`ir/gen.bn:57-63`) derives the package solely
from the source-spelling import alias, the scope-injection helpers set no home field (only
`definePkg` sets `PkgPath`), and `checkSelectorExpr` surfaces only `member.Type`
(`check_expr_access.bn:239`). So the resolved home must be **retained and threaded** — expose
stamps it (B's path) on each injected symbol, and IR-gen gains a reference-keyed lookup
returning that home for expose-injected members, else falling back to `resolveImportPkg`.

The change is a **strict, behavior-preserving extension**: for every non-expose reference —
**including generic bodies**, which already rewrite to the *defining* package via the
`CurrentImportAlias` arm (`gen_util.bn:86-91`) — the lookup returns exactly today's
source-spelled path, diverging *only* when the member is expose-injected. That must be
enforced by a **byte-identical-mangling** test over existing cross-package + generic-body
references. So it is behavior-preserving for existing code, unifies func/var/const with the
identity-driven type/impl path, and keeps a **forwarder emitting nothing** (no thunks, no
storage). The rejected alternative — emit per-symbol forwarding thunks/storage in A — makes
forwarders non-free and adds per-symbol codegen (Appendix A).

### 3.3 What a forwarder costs at runtime — nothing
With §3.2, a pure forwarder (`.bni` + `expose`, no `.bn`) emits **no code, no storage, no
`__init`** (`gen_init.bn:189-190`, gated by `HasPackageInit` at `main.bn:183-186`; a package with no init decls contributes no init call).
Importing A adds a **dependency edge to B** (so B initializes and links) — a single edge
appended to `A.Imports`, which the existing topo-sort and cycle detector already honor
(`loader.bn:382-389`, `:427-453`). Consumers' references to `A.X` resolve to B's entity;
there is no A-side runtime footprint.

### 3.4 Where the surface actually merges (two layers)
A package's surface lives in two coupled forms, and `expose` touches both:
- **Loader (AST):** after the `.bni` prepend/mark block (`loader.bn:336-344`), inject B's
  exported surface and add B's path to `A.Imports`.
- **Checker (scope):** copy B's exported `@Symbol`s into A's `@Scope` (`types/scope.bn`
  `defineType`/`defineFunc`/`defineVar`/`defineConst`/`defineInterface`, `:130-186`) as
  references to B's originals **stamped with their resolved home package** (B's path —
  the helpers set none today, only `definePkg` sets `PkgPath`; Phase-4 mangling needs it).
  This is what makes `A.X` type-resolve. Transitivity holds only if injection runs
  **B-before-A** — the checker must visit packages in dependency order; verify it follows the
  loader's topo order (`loader.bn:427-453`), don't assume (that topo sort drives loader/init
  order, not necessarily the checker's package-visit order).

### 3.5 Scope of the first cut
**Whole-package** re-export only. Per-symbol curation/rename (`expose foo.Bar`, hiding
some names) is a possible later extension using the same keyword, but is **not** in this
proposal. **Vars are included** (they matter to real packages — error singletons, default
instances — and under §3.2 they cost nothing extra: resolved-home mangling handles them
uniformly with funcs/consts).

## 4. Spec sketch (proposal — not canonical)

Grammar (a new top-level declaration; `.bni`-only):
```
ExposeDecl = "expose" string_literal ;
```
`expose` is a contextual keyword if feasible (recognized as a declaration lead-in),
to avoid reserving the identifier `expose` across the language.

Proposed rule-IDs (prefix `pkg`):
- `pkg.expose` — `expose "P"` in package A's `.bni` adds P's exported surface to A's; A
  must be able to load P; permitted only in the `.bni`.
- `pkg.expose.identity` — each re-exported member `A.X` **is** `P.X` (same type identity /
  storage / symbol / value); never a copy.
- `pkg.expose.surface` — `expose` affects only A's **exported** surface, not A's local
  scope (it is not an import); A's private names are unaffected.
- `pkg.expose.flat` — members appear as `A.X`, not `A.P.X`.
- `pkg.expose.transitive` — if P exposes Q, A's surface includes Q's members through P.
- `pkg.expose.conflict` _(Constraint)_ — a name reachable via two `expose`s, or via an
  `expose` and A's own exported declaration, is a **compile error**.
- `pkg.expose.dep` — `expose "P"` makes A **depend on** P (P initializes before A);
  `expose`/`import` cycles are rejected.

## 5. Open questions

- **Contextual vs reserved keyword** for `expose` (contextual avoids breaking existing
  identifiers; check the tree first).
- **`.bni` var-groups are extern** (`loader.bn:299-307`) — exposing a package whose vars
  come via `.bni` groups must not double-register storage.
- **Conflict-check granularity** — the check must cover *all* symbol kinds, including the
  type/group kinds `checkDuplicateDecls` skips (`check_decl.bn:27-53`); a dedicated
  expose-conflict pass over the unioned surface is needed (§ plan).
- **Diagnostics** — a collision should name both origins (which `expose`, which symbol).
- **Interaction with reflect/descriptors** — `Decl.Exported` feeds the reflect descriptor
  (`pkg/binate/ast.bni:325-333` — the package-root interface file, not `ast/ast.bn`); decide
  whether an exposed member is described as A's or B's.

---

## Appendix A: Discussion log (decisions, incl. discarded)

**Annotation vs. core feature.** *Considered:* `#[reexport] import "…"` (an annotation, to
avoid new grammar). *Rejected:* an annotation is **deliberately-ignorable metadata**;
re-export is the opposite — it changes what A exports and how `A.X` resolves, which every
compiler and consumer **must** obey. That makes it a **declaration**, not metadata. So it
is core syntax.

**Whole-package vs per-symbol.** *Chosen:* whole-package first (nails the promotion case;
per-symbol type re-export already exists as `type X = other.Y`). Per-symbol curation is a
later extension.

**Vars in or out.** *Chosen: in.* Dropping vars would gut the feature (packages export
singletons/defaults), and under the resolved-home mangling decision vars cost nothing
extra beyond funcs/consts.

**The two coherent semantic models.** The naive "import B and re-export it" is incoherent
(the same entity would be `A.X` to importers but `B.X` inside A). The only coherent poles:
- *Model 1 — namespace merge (= dot-import + re-export):* B's names flatten into A's scope
  (unqualified in A) *and* A's surface. **Rejected:** it introduces dot-import (which
  Binate deliberately lacks and which obscures provenance), forces A's **private** names to
  avoid colliding with B's, and makes A's `.bn` files care about the re-export (adding a
  symbol to B could break A's private code).
- *Model 2 — pure re-export (surface-only):* **chosen** — preserves qualified-only
  references, keeps expose and import orthogonal, and leaves A's `.bn` and private names
  entirely unaffected.

**Keyword bikeshed.** *Considered:* `reexport` (industry-standard term, but "re-" wrongly
implies A exported it before), `forward` (good, but collides with "forward declaration"
and the FFI plan's "trivial-forward" jargon), `export` (overloads "the exported surface =
`.bni`"). *Chosen: `expose`* — clean, no clash, reads for both use cases.

**"A forwarder compiles to nothing" — corrected.** An earlier framing claimed whole-package
re-export is *pure name-resolution* with zero emission. The codebase survey falsified this
for **func/var/const** references: their qualified-reference mangling is **spelling-driven**
(`ir/gen.bn:57-64`), so `A.X` would mangle to a symbol B never defines. Types/impls already
follow identity, but funcs/vars/consts do not. *Resolution:* make func/var/const mangling
follow the **resolved entity's home** (§3.2) — a strict, byte-identical-preserving extension
over existing name-building (new plumbing, *not* the flag-flip an earlier draft implied — the
resolved-home wire does not exist yet), which restores the "empty forwarder" property. The alternative (emit forwarding thunks/storage in A) was
rejected: it makes forwarders non-free and adds per-symbol codegen.
