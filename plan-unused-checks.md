# Plan: unused-entity checks — fix `(a)` and add `(b)`–`(e)`

Status: **PLAN** (2026-06-17). Tracks the work to fix the unused-import cross-file
gap and add four new "unused" checks. Driven by the question "does bnlint check
for unused imports / locals / private funcs / globals / types?" — answer today:
only unused-import exists, and it has a real gap.

This plan is grounded in a full read of `pkg/binate/lint/*`, `pkg/binate/types/*`
(checker/scope), and `pkg/binate/loader/*`, plus empirical repros. File:line
anchors drift under concurrent edits — re-grep before editing.

## Scope

- **(a)** Fix the unused-import cross-file gap (currently order-dependent: both
  silent misses AND false positives).
- **(b)** Detect unused local variables.
- **(c)** Detect unused unexported (file-private) functions.
- **(d)** Detect unused unexported global variables.
- **(e)** Detect unused unexported type definitions.

## 0. Foundational dependency — import scoping (CRITICAL bug, direction 1 chosen)

`(a)` cannot be done cleanly in isolation. The unused-import rule *pretends*
imports are per-file (Go-style), but the **checker treats imports as
package-scoped** — a confirmed CRITICAL bug (see the top of `claude-todo.md`):
`checkPackageImpl` builds one package scope and `registerImports(c, merged)`
dumps every file's imports into it, so a file can use a package only a sibling
imported, and two files importing different packages under the same alias
silently bind to the wrong package (reproduced: `B()` called `p1.V` instead of
`p2.V`).

**Decision (made): fix with direction 1 — proper file-scoped imports.** Mirror
Go's `fileScope → packageScope → universe`: keep the shared package decl scope,
but resolve each file's qualifiers against only that file's imports. This
requires per-file import information that the loader does not currently retain
(`loader.Package` exposes only `Merged`, no `Files`). The import-scoping fix is
tracked as its own CRITICAL item and is **Phase 0** here; `(a)` builds directly
on the per-file foundation it establishes.

Sequencing consequence: **do Phase 0 first**, then `(a)`. `(b)`–`(e)` are
independent of import scoping and can proceed in parallel.

## (a) Unused-import cross-file gap

### Mechanism (confirmed)

bnlint lints the **merged** AST (`cmd/bnlint/main.bn` → `lint.LintFile(c,
pkg.Merged)`). `MergeFiles` (`loader/loader_merge.bn`) **dedups** imports by
`(alias, path)` via `hasImport`, keeping the *original* `ImportSpec` from the
alphabetically-first file (`loader.bn` sorts dir entries before parsing). The
surviving spec carries that first file's `Pos.File`. `lintUnusedImports`
attributes references per `Pos.File` and checks each surviving spec against refs
from *its* file only.

Net: for a path imported in two files, only one spec is examined, attributed to
the first file. The gap is **bidirectional**:
- **False negative** (the documented case): unused in file A, used in file B,
  B sorts first → B's spec survives, looks used, A's genuinely-unused import is
  never flagged.
- **False positive** (undocumented): unused in A, used in B, A sorts first →
  A's spec survives and is flagged unused even though the package uses it.

### Fix

With Phase 0 in place, the right fix is **per-file**: check each file's own
import list against that file's own references. Two ways to get per-file imports:

1. **Stop deduping in `MergeFiles`** — append every file's `ImportSpec`
   unconditionally (mirrors the already-un-deduped `Decls` concatenation). Each
   spec keeps its true `Pos.File`, so the *existing* `refUsed(rc, imp.Pos.File,
   local)` logic in `lintUnusedImports` becomes correct with no logic change.
   - **Risk (mandatory pre-land check):** `MergeFiles` is shared by the
     checker/IR, not just lint. Removing dedup means `merged.Imports` may carry
     duplicate specs. The two known consumers (`pkg.Imports` extraction and the
     `.bni` overlay) already re-dedup, but the **type checker's `registerImports`
     must be verified to tolerate duplicates** before landing. *Note:* Phase 0
     (file-scoped imports) likely reworks `registerImports` anyway, so this risk
     is naturally subsumed if Phase 0 lands first.
2. **Add `Package.Files @[]@ast.File`** and lint per original file. Safer for
   `MergeFiles` consumers, but adds a public loader field + a new lint entry
   point. **Phase 0 may add exactly this** (per-file ASTs/imports), in which case
   `(a)` should consume it rather than touching `MergeFiles`.

**Recommendation:** decide `(a)`'s mechanism *after* Phase 0's shape is fixed —
if Phase 0 retains per-file ASTs/imports, `(a)` rides on that (option 2 for
free); otherwise option 1 with the checker-tolerance check.

### Edge cases
- Same path, different aliases per file → already two specs; each checked vs its
  own file.
- Blank `import _` + real import of same path → distinct keys, both survive;
  blank never flagged.
- Path imported in 3 files, used in 1 → flag the 2 unused files (not just one).
- `.bni`-sourced imports (`Pos.File` ends `.bni`) must not be flagged against
  `.bn` usage — verify, and skip `.bni`-sourced specs if un-deduping exposes them.
- Exact-duplicate import within one file → surfaces as two diags if unused;
  acceptable (a separate `duplicate-import` rule is out of scope — note in todo).

### Tests
- Multi-file lint helper (parse N sources with distinct filenames → `MergeFiles`
  → `LintFile`). Cases: unused-in-A-used-in-B (assert flagged on A, **order
  independent** — run both file orders); used-in-all (no diag); 3-files-1-use
  (2 diags); different-aliases; blank+real.
- Update `loader_merge_test.bn` if dedup is removed (expect concatenation).
- Smoke every changed package (loader + lint).

Effort: **S–M** (core is a few lines once Phase 0 lands; bulk is tests).

## Cross-cutting decisions for (b)–(e)

These shape all four new checks. Several are **OPEN — user's call** (see the
decision list at the end).

- **Warning vs hard error.** Recommend **non-fatal lint warnings** in
  `pkg/binate/lint`, matching unused-import. Making any of these a *checker
  error* rejects currently-valid programs — a **language-semantics change** that
  per project policy needs explicit user sign-off. (Go makes unused locals/imports
  hard errors; that's a deliberate, contentious choice the user owns.)
- **Where each lives.** `(c)`/`(d)`/`(e)` are top-level and fit `pkg/binate/lint`
  cleanly (it already walks the merged package AST). `(b)` is different: it needs
  intra-function scope/shadowing resolution, which **the checker already owns**
  (scopes, `:=`, loop vars, captures) and **bnlint cannot see** (lint runs
  post-check; block scopes are already popped and idents carry no resolved
  symbol). So `(b)` is best done **in the checker** (a `Used` flag + a
  `popScope`-time sweep), emitted as a warning.
- **BUILDER constraint.** `pkg/binate/lint` is **not** in cmd/bnc's
  BUILDER-compiled tree (verified: cmd/bnc's only importer-closure is 17 packages,
  lint not among them; lint's sole non-test importer is cmd/bnlint). So lint code
  may use the full language. **`(b)` is different** — the checker (`pkg/binate/
  types`) *is* BUILDER-compiled, so `(b)`'s changes must stay BUILDER-compatible
  (plain bool/Pos fields + flags are fine; no new language features).
- **Shared reference index.** `(c)`/`(d)`/`(e)` all need "what top-level names are
  referenced anywhere in the package." The unused-import walkers
  (`refCollector` + `walkDeclRefs/Expr/Type/Stmt`) already traverse every
  reference site but only record *package qualifiers*. **Hoist them into a shared
  `pkg/binate/lint/refs.bn` and generalize** to also record bare `EXPR_IDENT`
  names (value refs) and unqualified `TEXPR_NAMED` names (type refs). One walk,
  multiple consumers. The refactor must keep unused-import byte-identical
  (regression-test it).
- **Reference-presence vs reachability (KEY OPEN DECISION).** For `(c)`/`(d)`/`(e)`:
  - *Reference-presence* (recommended by the cross-cutting + d/e recon): flag a
    private decl whose name appears nowhere. Cheap, one index lookup, lowest
    false-positive risk in a CI gate. **Limitation:** misses dead-code *islands*
    (a private func/var/type referenced only by another dead private decl).
  - *Reachability-from-roots* (recommended by the c-funcs recon): roots =
    exported decls + `main` + package-init; flag anything not transitively
    reachable. Catches islands — arguably the whole point of an unused-func check.
    More code (a name-based worklist; no resolved call graph exists — `Expr.DeclRef`
    is populated only for closures) and more false-positive-prone in a gate.
  - **Tension to resolve:** consistency + gate-safety argue reference-presence;
    "catch the dead code that matters" argues reachability. They share the same
    refs index, so a worklist is incremental. **Recommendation: reachability for
    `(c)` (funcs, where islands are the real prize), reference-presence for `(d)`/
    `(e)` initially**, unified later if wanted — but this is the user's call.
- **Rollout hazard.** bnlint **gates hygiene/CI** (`scripts/hygiene/lint.sh`,
  invoked by `scripts/hygiene/run.sh` and a dedicated CI `lint:` job). The moment
  a new rule ships, every pre-existing violation across `pkg/` + `cmd/` turns the
  gate red. **Before landing each rule, run it tree-wide, enumerate violations,
  and clean them (or `_`-out / suppress) in the same change.** Do NOT wire new
  rules into CI separately — they ride the existing always-on path (scope rule:
  add the check, don't change where it runs).
- **Exported-ness signal.** Exported = appears in the package `.bni`
  (`Decl.Exported`), **not** capitalization. Two recon-flagged latent bugs here
  (verify before relying on the flag):
  - `markBniExportedVars` (`loader_util.bn`) does **not** recurse into
    `DECL_GROUP`, so a group-nested exported var gets `Exported=false` → `(d)`
    would false-flag it. Likely a real latent bug (also affects the reflect
    exported-global table). **Verify and raise/fix separately** before `(d)`.
  - `DECL_TYPE` never gets `Exported` set at all → `(e)` must derive exportedness
    from a `.bni`-sourced same-name peer (`Pos.File` ends `.bni`), or add type
    export-marking to the loader.

## (b) Unused locals — in the checker

**Approach:** add `Used bool` + `DeclPos token.Pos` to `Symbol`; mark `Used` in
`checkIdent` on **reads**; sweep each function-local scope at `popScope` for
`SYM_VAR && !Used && !blank && !param`, emitting a **warning**.

- **Read vs write:** plain-assignment LHS and `++`/`--` must NOT count as a use
  (gate marking behind a checker `InWriteTarget` flag set while checking a simple
  LHS ident / inc-dec target). Compound `a += b` and `a = g(a)` read `a` via the
  RHS → correctly counted. This gives Go-parity "declared and not used" including
  write-only vars. *(OPEN: flag write-only, or treat any mention as use?)*
- **Params:** recommend **skip** (Go does; params are signature/interface-impl
  contracts). Tag `IsParam` at the binding site. *(OPEN.)*
- **Named returns:** **moot** — Binate has none (`Results` is `@[]@TypeExpr`).
- **Captures:** a var captured by a closure counts as used (checkIdent inside the
  literal body already resolves it; belt-and-suspenders: set `Used` in
  `recordCaptureOnFrame`).
- **Address-of** `&x` → counts as use (operand reaches `checkIdent`).
- **Shadowing:** each scope has its own `Symbol`; inner sweep flags an unused
  inner shadow independently — correct.
- **REPL/tentative/pending:** the sweep MUST be suppressed in those modes
  (bodies re-run / decls park) — gate behind a flag, default on for compile.
- **Loop / for-in vars:** `for i:=0;…` reads `i` in cond → not flagged; unused
  for-in `v` flagged unless `_`. *(OPEN: flag unused for-in vars?)*

Integration: `types.bni` (Symbol fields), `scope.bn` (`defineVar`),
`check_expr.bn` (`checkIdent`), `check_stmt.bn` (write-target gating),
`checker.bn` (`popScope` sweep + flag), `check_decl_func.bn` (`IsParam`),
`check_capture.bn` (capture⇒used), `checker_errors.bn` (`addCheckWarning`).
All BUILDER-compatible.

Tests: unit tests asserting `CheckerWarnings()` for each shape (unused; used;
write-only; compound-assign; param-skip; receiver-skip; capture; shadow; blank;
loop; address-of; multi-assign); REPL-suppression regression; conformance
fixtures end-to-end. Expect the toolchain's own sources to surface real unused
locals — fix/`_`-out in the same change.

Effort: **M**. Risk: enabling the sweep turns the build red on real in-tree
unused locals — land warning-only and/or clean sites together.

## (c) Unused private functions — in lint, reachability-from-roots

**New rule `unused-func`** (new file), run once over the merged AST.

- **Candidates:** top-level `DECL_FUNC` (recurse `DECL_GROUP`) with `Recv == nil`
  (exclude methods), `!Exported`, `Body != nil`, not named `main`.
- **Roots:** exported funcs + `main` + methods + every global initializer
  (package-init). Build the by-name reference graph from the shared refs index
  (bare `EXPR_IDENT` names; a generic call `g[int](…)` surfaces `g` as the
  instantiation head ident; a bare function value `f` surfaces `f`).
- **Reachability worklist:** seed with roots, mark referenced candidates
  reachable, transitively process reachable candidates. Flag unreached
  candidates. This catches dead-code islands (mutually-recursive private funcs
  with no root entry) that flat counting misses.
- **Methods excluded** — dispatched via vtable/SELECTOR, not bare name; flagging
  them risks false positives on interface-dispatched impls. (Unused-method
  detection needs dispatch analysis — out of scope, note explicitly.)
- **Name-matching caveat:** a local/param shadowing a func name over-counts as a
  use → safe under-warning (never a false positive). Acceptable.
- **Test-file boundary:** references in same-package `_test.bn` count iff they're
  in the linted merged AST; document the boundary, don't special-case.

Tests: lone unused func (flag); cascade `a→b` both unrooted (flag BOTH — this is
the reachability-vs-flat discriminator); called-from-main / from-exported (no
diag); used-as-function-value (no diag); recursive-unrooted (flag) vs
recursive-rooted (no diag); method never bare-called (no diag — excluded);
generic instantiated (no diag) vs uninstantiated-private (flag). **Note:**
`lintSrc` unit harness doesn't run the loader so `Decl.Exported` is unset — set
it manually or use a loader-driven fixture for export-skip cases.

Effort: **M**.

## (d) Unused private globals — in lint, shared refs

**New rule `unused-global`** (or fold into a shared private-entity pass).

- **Candidates:** top-level `DECL_VAR` (recurse `DECL_GROUP`), non-blank,
  `!Exported`.
- **Used iff** name appears in the shared refs index (reference-presence
  initially — keep parity with `(e)`; reachability is the open unified decision).
  Address-of, other globals' initializers, and any function body all count
  (covered by the whole-AST walk).
- **Blocking latent bug:** `markBniExportedVars` skips `DECL_GROUP` → group-nested
  exported vars get `Exported=false`. Verify and fix (with a loader test) before
  landing `(d)`, else false positives on real exported group vars.
- *(OPEN: flag write-only globals? Recommend no for v1 — LHS idents count as
  refs, matching unused-import simplicity. OPEN: cover consts (`DECL_CONST`)?
  Recommend no initially — unused consts are common/noisy.)*

Tests: unused private var (flag); read in a func (no diag); used by another
global's init (no diag); address-of (no diag); group with one member unused
(exactly one diag — verifies group recursion); exported (no diag, via manual
`Exported`/loader harness); blank `var _` (no diag); used-only-by-dead-func
(no diag under reference-presence — pins the stance); shadowed name (no diag —
safe under-warn). Plus the loader group-recursion regression test.

Effort: **M** (rule small; the `markBniExportedVars` fix carries its own test).

## (e) Unused private types — in lint, reference-based

**New rule `unused-type`** (new file), dispatched per-file from `LintFile`
(whole-file, not inside `lintDecl`'s per-decl recursion). `DECL_TYPE` is **not
dispatched at all today** — add it.

- **Candidates:** unexported `DECL_TYPE` (recurse `DECL_GROUP`). Exportedness:
  a name is exported iff a same-name `DECL_TYPE` has `Pos.File` ending `.bni`
  (since `DECL_TYPE` gets no `Exported` flag). De-dup the benign `.bni`+`.bn`
  same-name pair to one candidate, anchored at the `.bn` definition's `Pos`.
- **Used iff** the type name appears (unqualified `TEXPR_NAMED`, or
  `TEXPR_INSTANTIATE` head) anywhere outside its own definition. Every
  type-bearing position routes through `e.TypeRef` / the `walkTypeRefs`
  recursion: fields, slice/ptr/managed bases, func-value params/results,
  type-args, composite literals `T{…}`, and the type-carrying builtins
  `cast`/`bit_cast`/`sizeof`/`alignof`/`make`/`make_slice`/`__c_call` (all carry
  the type in `TypeRef`; `box` carries none).
- **Self-reference exclusion:** a type's own definition subtree (e.g. `next
  @node`) must NOT count as a use — exclude the candidate's own `DECL_TYPE`
  subtree (per-candidate walk, or attribute-and-subtract like unused-import's
  `CurFile`).
- **Method receiver (OPEN DECISION):** recommend **receiver-only does NOT count
  as use** — a private type whose only mentions are its own method receivers is
  dead (the methods are dead too). Implement by not recording `d.Recv.Type` for
  this rule. Genuine judgment call; one-line flip; dedicated test either way.
- **Cascade limitation:** reference-based flags B (unused) but not A
  (referenced only by B) — document as a known under-report; reachability would
  catch it (the unified open decision).
- Aliases are candidates (unused private alias = dead code). Generics: record
  instantiation heads as uses; a never-instantiated private generic type is a
  candidate.

Tests: unused private type (flag); used via var/field/cast/sizeof/make/composite/
pointer/slice/func-value (no diag); self-referential unused (flag) vs
self-ref-used-elsewhere (no diag); receiver-only (assert chosen stance);
exported skip (loader/`.bni` harness); alias unused/used; generic
instantiated/not; cascade (B flagged, A not — locks in the stance); clean code
no false positives.

Effort: **M**.

## Suggested sequencing

1. **Phase 0 — import scoping (direction 1).** CRITICAL; separate todo.
   Establishes the per-file foundation.
2. **`(a)`** unused-import cross-file fix, riding on Phase 0.
3. **Shared `refs.bn` refactor** (hoist + generalize the walkers; keep
   unused-import green).
4. **`(d)` + `(e)` + `(c)`** top-level rules on the shared index. Verify/fix the
   `markBniExportedVars` group bug before `(d)`. Clean the tree of each rule's
   pre-existing violations before landing it (gate hazard).
5. **`(b)`** unused locals in the checker (BUILDER-compatible). Land warning-only
   and/or clean in-tree sites together.

Each step is independently green and small. `(b)`–`(e)` don't depend on Phase 0.

## Open decisions for the user

1. **Warning vs hard error** for `(b)`–`(e)` (error = language-semantics change,
   needs sign-off). Recommend warnings.
2. **Reference-presence vs reachability** for `(c)`/`(d)`/`(e)` (and whether to
   unify). Recommend reachability for `(c)`, reference-presence for `(d)`/`(e)`.
3. **`(b)`:** flag unused params? flag write-only locals? flag unused for-in vars?
   Recommend: skip params; flag write-only (Go-parity); flag for-in (with `_`
   escape).
4. **`(d)`:** cover consts (`DECL_CONST`)? flag write-only globals? Recommend no
   to both for v1.
5. **`(e)`:** does a method receiver count as a use of its type? Recommend no.
6. **`(a)`:** un-dedup `MergeFiles` vs add `Package.Files` — defer until Phase 0's
   shape is fixed (likely subsumed).

## Latent bugs surfaced (to raise/track separately)

- `markBniExportedVars` skips `DECL_GROUP` → group-nested exported vars get
  `Exported=false` (affects `(d)` and the reflect exported-global table). Verify,
  then raise + fix per the Bug Discovery Protocol.
- `DECL_TYPE` carries no `Exported` flag — `(e)` works around via `.bni`-peer
  detection; a cleaner long-term fix is to add type export-marking in the loader.
