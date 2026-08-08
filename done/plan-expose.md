# Plan: Implementing `expose` (whole-package re-export)

**Status:** high-level plan (2026-07-10). The **design** is
**[design-expose.md](design-expose.md)** — **ratified** 2026-07-10 (DECIDED note in
`claude-notes.md`); not yet specified or implemented. This is the implementation roadmap; it
does not re-litigate the design. Grounded in a codebase survey (the `file:line` cites below
are real). Phase 0's ratify step is **done**; the remaining prerequisite is the formal spec.
A future edit-site-level detailed plan would follow.

Related: the existing cross-package **type-alias** machinery (`type X = other.Y`, conformance
`110`/`941`) — the identity substrate `expose` reuses; `plan-cross-package-interfaces.md`;
the `.bni` interface contract (§16.5).

## 1. What we are building

A core `.bni` declaration `expose "pkg/std/foo"` that adds package B's **entire exported
surface** to the exposing package A's surface, **surface-only** (not A's local scope),
**identity-preserving** (A.X *is* B.X — same type/storage/symbol/value), **flat**,
**transitive**, with **collisions an error**. A pure forwarder is a `.bni` + `expose` with
**no `.bn`** and **zero runtime footprint**. See design §2–§3.

The single non-obvious implementation fact (design §3.2): **types/impls already follow
identity, but func/var/const references mangle from the source spelling**, so `expose` must
make func/var/const qualified-reference mangling follow the **resolved entity's home**
(a no-op for existing code; restores the empty-forwarder property).

## 2. Current state (from the codebase survey)

- **Exported surface = two coupled forms.** (a) *AST/loader:* `Package.Merged @ast.File`;
  the loader prepends `.bni` decls ahead of `.bn` (`pkg/binate/loader/loader.bn:276-336`)
  and marks the surface with `Decl.Exported` (`markBniExportedFuncs`/`markBniExportedVars`,
  `loader.bn:343-344`, `loader_util.bn:173-241`). A `.bni` `var (…)` group is treated as
  **extern** and *not* prepended (`loader.bn:299-307`). (b) *Checker/scope:* a per-package
  `@Scope` (a `Syms` array of `@Symbol`) registered by path in `Checker.Packages`;
  cross-package `A.X` resolves via `resolveQualifiedSym`/`checkSelectorExpr` → `SYM_PKG`
  lookup → `lookupPackage` → `Lookup(X)` (`types/check_expr_access.bn:140-148, 216-240`).
- **No cross-package surface aggregation of any kind exists** — all cross-package access is
  qualified; nothing merges another package's surface into A (`loader.bn:11-12`;
  `buildScopeFromFile` registers imports as `SYM_PKG` only, `types/bni_scope.bn:18-36`).
- **No `expose` token / keyword / AST kind / parser.** `DECL_` kinds stop at `DECL_IMPL`
  (`pkg/binate/ast.bni:86-93` — the package-root interface file, not `ast/ast.bn`). Need a
  token, a `DECL_EXPOSE` (carrying the path), and a dispatch arm in the top-level parser
  (`parser.bn` `parseTopLevelDeclInner`).
- **Mangling is spelling-driven for func/var/const** — `resolveImportPkg` (`ir/gen.bn:57-63`)
  derives the package from the source-spelling import alias, and `buildQualName`
  (`gen_util_literals.bn:36-42`) routes through it. This is **not 3-4 sites**: a repo-wide
  `grep -rn 'resolveImportPkg\|buildQualName' pkg/binate/ir/*.bn` returns **~75 hits across
  22 files** — most are *registration* sites (`gen_register_import.bn`, `gen_import.bn`,
  `gen_module.bn`) keyed on the definer's own alias (leave those); the *reference* sites are
  what Phase 4 must change (Phase 4). **Types/impls are identity-driven** already
  (`gen_impl_recvname.bn:39-51`; `ResolveAlias` `types_query.bn:33-38`; test `941`).
- **Type/interface re-export substrate already works.** `type X = other.Y` → `TYP_ALIAS`
  (`bni_scope.bn:274-323`, `MakeAliasType` `types.bn:355-362`; test `110_cross_pkg_type_alias`);
  interfaces via `interface X = Y` (`bni_scope.bn:239-246`; bare `type X = Iface` rejected,
  `resolve_type.bn:27-39`, test `486`). Scope injection helpers exist: `Scope.Define`/
  `Lookup` (`scope.bn:26-45`), `defineType/Func/Var/Const/Interface` (`scope.bn:130-186`).
- **Dep edge + init already handle forwarders.** The topo sort/cycle detector read only
  `pkg.Imports` (`loader.bn:382-389, 427-453`, cycle msg `loader_util.bn:296-305`). A
  `.bni`-only package with no init decls contributes **no `__init`** (`gen_init.bn:189-190`;
  `HasPackageInit` gate `main.bn:183-186`). `.bni`-only packages **load fine today**
  (`loader.bn:247-255, 366-369`; `InterfaceOnly` path).
- **No conflict check covers expose.** `checkDuplicateDecls` compares only within one decl
  list and **skips `DECL_TYPE`/`DECL_GROUP`** (`check_decl.bn:27-53`); type redecl is
  separate (`check_type_redecl.bn:32-70`). A new pass is required.
- **BUILDER caveat.** `parser`/`loader`/`ir`/`mangle`/`types` are all in `cmd/bnc`'s
  BUILDER-compiled tree. A new `expose` keyword won't parse under the pinned BUILDER until
  bumped (the `#[build]` / `ARCH_AARCH64` trap) — but only matters once a `.bni` *in bnc's
  own tree* uses `expose`; the recognition code itself compiles fine.

## 3. Phases

**MVP = Phases 1 → 5** (a working forwarder for types + funcs + vars + consts + interfaces,
with collisions caught). Ordered by dependency.

### Phase 0 — Specify (ratify ✅ done)
The design is **ratified** (2026-07-10) and recorded as a DECIDED note in `claude-notes.md`.
Remaining prerequisite before code: write the **formal spec** (the `pkg.expose.*` rules and
the `ExposeDecl` grammar in `docs/spec`). **Sub-decisions to close during spec:**
contextual-vs-reserved `expose` keyword; confirm the resolved-home mangling change (design
§3.2); `.bni`-only permitted.

### Phase 1 — Frontend: token, keyword, AST, parser
- Add an `expose` token/keyword (contextual if feasible — check the tree for existing
  `expose` identifiers first), a `DECL_EXPOSE` AST kind carrying the target path string, and
  a `parseExposeDecl` + dispatch arm in `parser.bn` (`parseTopLevelDeclInner`). Restrict it
  to `.bni` parsing.
- **Deliverable:** `expose "pkg/std/foo"` parses into a `DECL_EXPOSE`. **Deps:** none.
  **BUILDER:** the keyword lands in `bnc`'s tree — verify the pinned BUILDER still builds
  the tree (it should, as long as no bnc-tree `.bni` *uses* `expose`); §4.

### Phase 2 — Loader: dependency edge + surface hand-off
- On a `DECL_EXPOSE "P"`, resolve/load P and **append P's path to `A.Imports`**
  (`loader.bn:382-389`) so P initializes before A and the existing cycle detector catches
  `expose` cycles. Record the exposed-package list on A for the checker.
- Handle the `.bni` var-group extern subtlety (`loader.bn:299-307`) so exposing a
  var-group-bearing package doesn't double-register storage.
- **Deliverable:** exposing P makes P a build-order dependency of A; cycles are rejected.
  **Deps:** 1.

### Phase 3 — Checker: scope injection (surface merge)
- After A's scope is populated, **copy P's exported `@Symbol`s into A's scope** as
  references to P's originals, via `defineType`/`defineFunc`/`defineVar`/`defineConst`/
  `defineInterface` (`scope.bn:130-186`), **stamping each injected symbol with its resolved
  home package** (P's path) — the helpers set no home field today (only `definePkg` sets
  `PkgPath`, `scope.bn:184`), and Phase 4 needs it. This is what makes `A.X` **type-resolve**
  to P's entity. Interfaces bind via the interface-identity form.
- **Ordering:** injection must run **P-before-A** for transitivity (P, incl. what P itself
  exposes, first). This holds only if the checker visits packages in dependency order —
  **verify** it follows the loader's topo order (`loader.bn:427-453`), do not assume (that
  topo sort drives loader/init order, not necessarily the checker's package-visit order).
- **Also materialize** A's fully-unioned exposed surface (the **transitive closure**, across
  every symbol kind) as an enumerable set — Phase 5's collision check consumes it.
- **Deliverable:** `A.X` type-checks with P's identity for **all** symbol kinds; exposed
  types/impls link **once scope-injected here** (piggybacking on existing alias identity —
  test `941`); funcs/vars/consts still mis-mangle until Phase 4. **Deps:** 2.

### Phase 4 — Resolved-home mangling for func/var/const (the crux)
- **Build the wire (new plumbing, not a flag flip).** Nothing threads a resolved home into
  name-building today: `resolveImportPkg` (`gen.bn:57-63`) keys only on the source-spelling
  alias, injected symbols carry no home (Phase 3 now stamps it), and `checkSelectorExpr`
  surfaces only `member.Type` (`check_expr_access.bn:239`). So: (a) consume the Phase-3
  resolved-home stamp on expose-injected symbols; (b) give IR-gen a **reference-keyed lookup**
  returning that home for an expose-injected member, else falling back to `resolveImportPkg`.
- **Sweep the reference sites from the repo-wide grep, not a guessed subset.** `grep -rn
  'resolveImportPkg\|buildQualName' pkg/binate/ir/*.bn` → **~75 hits / 22 files**; triage into
  *func/var/const reference (must change)* — e.g. call target `gen_call.bn:162`, var read/write
  `gen_func.bn:339` / `gen_selector_type.bn:46`, const folding `gen_const_fold.bn:69,244,330,382`,
  imported-const/selector `gen_import_const.bn:41` / `gen_expr.bn:444`, func-value/method-expr
  `gen_util.bn:106` — vs. *type/impl/registration (identity- or definer-alias-keyed — leave)*
  (`gen_register_import.bn` / `gen_import.bn` / `gen_module.bn`). State the file set covered.
- **Strict, behavior-preserving extension.** For every non-expose reference — **including
  generic bodies**, which already rewrite to the defining package via the `CurrentImportAlias`
  arm (`gen_util.bn:86-91`, and the `gen_const_fold.bn` arms) — the lookup must return exactly
  today's path, diverging *only* for expose-injected members. **Acceptance criterion:** a unit
  test asserting **byte-identical** mangled output on existing cross-package + generic-body
  references, before/after.
- **Deliverable:** `A.X` for exposed **funcs/vars/consts** links to P's symbol; a pure
  forwarder emits nothing; existing mangling unchanged. **Deps:** 3. **MVP-critical.**

### Phase 5 — Expose-collision check
- A new pass over the **materialized transitive-closure exposed surface from Phase 3**: error
  if a name is reachable via two `expose`s, or via an `expose` and A's own exported
  declaration — covering **type/interface/const/var/func uniformly** (it cannot reuse
  `checkDuplicateDecls`, which is within-one-decl-list and skips `DECL_TYPE`/`DECL_GROUP`,
  `check_decl.bn:27-53`). Diagnostics name both origins.
- **Deliverable:** colliding exposes/redeclarations are rejected with a clear message.
  **Deps:** 3 (its transitive-closure surface deliverable).

### Phase 6 — Reflect/descriptor + edge cases (polish)
- Decide how an exposed member is described (`Decl.Exported` → reflect descriptor,
  `ast.bni:325-333`); handle generic exported symbols and any `.bni`-only-package edge cases
  surfaced by tests. **Deps:** 3–5.

## 4. Cross-cutting concerns

- **BUILDER.** The `expose` keyword lands in `bnc`'s BUILDER-compiled tree (parser/loader/
  ir/mangle/types). Recognition code is BUILDER-compilable, but the moment a `.bni` *in
  bnc's own dependency tree* uses `expose`, the pinned BUILDER (which predates it) can't
  parse it → a BUILDER bump is required first. Keep `expose` out of bnc-tree `.bni`s until a
  BUILDER that understands it is pinned (verify per the CLAUDE.md rule).
- **Testing.** Conformance tests: the promotion scenario (old path forwards to new,
  old+new interoperate on the same types/vars), an aggregator over several packages,
  transitivity (P exposes Q, A exposes P), each collision case (two exposes; expose vs own
  decl), a `.bni`-only forwarder with **no `.bn`**, and a **var** re-export whose write
  through `A.V` is observed through `B.V` (proves storage identity, not a copy). Unit-test
  the parser (Phase 1), scope injection (Phase 3), and the resolved-home mangling (Phase 4)
  directly. No new C/e2e harness needed (unlike FFI export) — this is all in-language.
- **No backend/object-format work.** Unlike FFI export, `expose` needs **no** alias
  primitive in `asm/macho`/`asm/elf`/codegen — it's front-end (parser/loader/checker) plus
  the IR name-building change. The forwarder emits nothing.

## 5. Risks & open questions

- **Resolved-home mangling reach.** Phase 4 must cover *every* func/var/const reference site
  from the repo-wide grep (~75 hits / 22 files — direct call, function value, var read/write,
  const use, method expression), triaged against the registration sites that stay, and must
  preserve **byte-identical** mangling for all non-expose references (esp. the generic-body
  `CurrentImportAlias` rewrite). Enumerate from the grep, not a guessed subset.
- **Interface exposure** — ensure exposed interfaces bind via `interface X = Y` identity,
  not the rejected bare `type X = Iface` (test 486).
- **Conflict-check completeness** — must span symbol kinds `checkDuplicateDecls` skips, and
  the cross-package (not single-decl-list) case it explicitly does not cover.
- **`.bni` var-group extern** double-registration (Phase 2).
- **Contextual vs reserved keyword** (Phase 1) and its BUILDER implications.
- **Reflect/descriptor identity** of exposed members (Phase 6).

## 6. Recommended first slice

If Phase 0 clears, land **1 → 2 → 3 → 4 → 5** in order — parser, dep edge, scope injection,
resolved-home mangling, collision check — verified by the promotion + var-identity
conformance tests. That yields a working `expose` for the stdx→std promotion. The one item
with real subtlety is **Phase 4** (resolved-home mangling — enumerate every reference site);
everything else reuses substrate that already exists (alias identity, scope helpers, the
dep-graph/cycle machinery, `.bni`-only packages). Each phase is independently landable and
keeps the tree green.
