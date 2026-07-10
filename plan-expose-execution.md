# Execution plan: `expose` — edit-site-level implementation plan

**Status:** edit-site-level execution plan (2026-07-10). This EXPANDS the high-level
**[plan-expose.md](plan-expose.md)** into concrete file/function/line edit sites, grounded
against the real tree at `/Users/vtl/binate/temp-binate-3` (every `file:line` below was
re-verified — corrections to the doc summaries are called out inline). The **design** is
**[design-expose.md](design-expose.md)**.

**CONTINGENT ON PHASE 0.** The design is recorded as a note, but implementation is **not yet
specified** (no canonical `docs/spec` rules/grammar) and **not yet ratified for code**. Treat
Phase 0 (below) as a gating prerequisite: nothing in Phases 1–6 starts until Phase 0 closes.
Do **not** read this document as approval to build the feature — it is the map for *if/when*
Phase 0 clears.

Recon note: six subsystem recon passes (R1 frontend, R2 loader, R3 checker/scope, R4
IR mangling, R5 collision/reflect, R6 identity substrate + test layout + BUILDER) were run
against the real tree. Several `file:line` cites in design-expose.md / plan-expose.md were
**stale or imprecise**; each correction is folded in below as an explicit callout. Where a
corrected cite exists, this plan uses the **corrected** one.

Adversarial-review note (3-lens minimal review of this doc against the real tree): the cite-
level grounding held up (the sweep triage, the loader placement trap, the visit-order finding,
the `@Symbol.PkgPath` fact were all independently re-verified). **One CRITICAL logic hole was
found by two reviewers independently and is now folded in as Phase 3.5:** resolved-home mangling
(Phase 4) fixes name *production* but not name *registration* — for a pure forwarder, the
exposed package is a **transitive-only** import of the consumer, and consumer-side
func/var/const registration is **direct-import-scoped**, so `pkg/B.X` is emitted but never
declared/registered → link failure (funcs), loud IR-gen abort (vars), silent wrong-fold
(consts). Phase 4's "forwarder emits nothing / mangling is a strict extension" claim is only
half the wire without Phase 3.5. Also folded in: a Phase-5 `checkBodies` cite fix
(`checker.bn:187-191`, not `bni_scope.bn:189-191`), the Phase-3 injection now reads
`DECL_EXPOSE` from the already-passed `.bni` AST (no `LoadPackageInterface` signature sweep),
a const-value-preservation callout on injection, and the grep-union completeness note. Each is
marked **[REVIEW]** inline.

---

## Phase 0 — Ratify & specify (user-owned; GATING)

Not code. The design note exists; what is missing before any implementation:

1. **Formal spec** — the `pkg.expose.*` rules and the `ExposeDecl` grammar in `docs/spec`
   (`docs/spec/binate.ebnf` + a prose chapter). Annex A regenerates from the EBNF via
   `docs/scripts/gen-annex-a.py`.
2. **Close the sub-decisions:**
   - **Contextual vs reserved keyword** — recon R1 verified a tree-wide grep (pkg/ cmd/
     examples/ conformance/ + impls/stdlib + ifaces/stdlib): 22 hits for `expose`, **all in
     comments/doc-prose, 0 as a bare identifier**. So both paths are collision-free;
     **contextual is recommended** (avoids reserving the identifier language-wide; also
     avoids shifting the token enum — see Phase 1). This plan assumes the **contextual**
     path and notes the reserved-path deltas where they differ.
   - **Resolved-home mangling** (design §3.2) — confirm the func/var/const reference-mangling
     change is in scope. It is the crux (Phase 4); nothing works without it.
   - **`.bni`-only permitted** — a pure forwarder is `.bni` + `expose`, no `.bn`.

Everything below is CONTINGENT on this phase closing. **Do not start Phase 1 until the user
ratifies for implementation and the spec exists.**

---

## Cross-cutting conventions (read before any phase)

### BUILDER gating rule (hard constraint)

`BUILDER_VERSION = bnc-0.0.10` (recon R6). `cmd/bnc`'s BUILDER-compiled tree includes
`pkg/binate/{ast,buf,codegen,debug,ir,irdata,loader,mangle,types,version}` (from
`cmd/bnc/main.bn`) plus `pkg/binate/parser` (from `cmd/bnc/compile.bn`). So **parser, loader,
ir, mangle, types are ALL in the BUILDER-compiled tree** — every edit site in Phases 1–5 lands
there.

- The **recognition/impl code itself compiles fine** under the pinned BUILDER (it is plain
  Binate).
- **The trap:** the moment ANY `.bni` *inside cmd/bnc's own dependency tree* uses `expose`,
  the pinned BUILDER (which predates the keyword) can no longer PARSE that `.bni` → the gen1
  build breaks (`expected ;, got ...` / unknown-decl). This is the `#[build]` / `ARCH_AARCH64`
  trap from CLAUDE.md.
- **Discipline:** keep `expose` out of every bnc-tree `.bni` until a BUILDER that understands
  `expose` is built and pinned (bump `BUILDER_VERSION`). Conformance tests are **fine** — they
  are compiled by the current tree's `bnc`, not the BUILDER.
- Before relying on the current BUILDER, test directly: `scripts/fetch-builder.sh --tool bnc`
  then run it on an `expose` snippet (per CLAUDE.md "verify the current BUILDER actually
  supports it").

### Conformance multi-package test layout (recon R6, verified)

A multi-package conformance test is a **directory** `conformance/NNN_name/` containing:
- `main.bn` — the `package "main"` entry (always present; the runner keys on it).
- `expected` (stdout for a positive test) **OR** `error` (negative test; each line is a
  **required substring** of the failure output).
- a `pkg/` subtree: for each imported package `pkg/X`, the interface is `NNN_name/pkg/X.bni`
  and (if it has an impl) the body is `NNN_name/pkg/X/X.bn`.

**Real example** (the type-alias analogue expose reuses):
`conformance/110_cross_pkg_type_alias/` = `{ main.bn (import "pkg/mylib"), expected,
pkg/mylib.bni, pkg/mylib/mylib.bn }`.
**Transitive-dep example** (closest analogue to `P exposes Q`):
`conformance/065_transitive_deps/` = `{ main.bn, pkg/outer.bni, pkg/outer/outer.bn,
pkg/inner.bni, pkg/inner/inner.bn }`.

- Search-path wiring: `runners/builder-comp.sh:16-20` prepends the test `root` to bnc's `-I`
  (iface) and `-L` (impl) via `scripts/binate-paths.sh`, so `import "pkg/X"` in `main.bn`
  resolves to `root/pkg/X.bni` + `root/pkg/X/X.bn`.
- Discovery: `run.sh:449` globs `conformance/[0-9][0-9][0-9]*_*/` (admits ≥1000).
- **Import whitelist:** `scripts/hygiene/conformance-imports.sh` restricts conformance
  `.bn`/`.bni` to importing `pkg/bootstrap`, `pkg/builtins/*`, or a **test-local fixture**
  whose `pkg/X.bni`/`pkg/X/`/`pkg/X.bn` exists under the SAME test dir (checked at
  `conformance-imports.sh:120-127`). Expose tests use only test-local fixtures → **no
  whitelist entry needed**.
- **Numbering:** `scripts/hygiene/conformance-test-numbers.sh` enforces per-directory unique
  `NNN`. Highest current top-level number is **1011**; pick the next free `NNN` **at land
  time** and re-run the numbering check after the landing rebase (a concurrent worker may take
  the number).

### Checker visit-order finding (recon R3 — VERIFIED, answer is YES)

design §3.4 / plan Phase 3 flagged "verify the checker visits packages in dependency order,
don't assume." **Verified answer: YES, it does — the hedge can be dropped.** The checker has
**no package-visit loop of its own**; the drivers iterate the loader's topo order `ldr.Order`
and call `LoadPackageInterface` + `CheckPackage` per package. All drivers iterate
`for i := 0; i < len(ldr.Order); i++`:
`cmd/bnc/compile.bn:27-36` (typecheckAll) + `:55-64` (typecheckPackages);
`pkg/binate/interp/check.bn:17-26` + `:42-51`;
`repl/session.bn:152-155`; `repl/mid_session_import.bn:65-69`; `cmd/bnlint/main.bn:267-273`.

`ldr.Order` **is** the topo dependency order: `computeOrder()` (loader.bn:428-437) runs a DFS;
`visit()` (loader.bn:440-453) appends a package to `order` **only after** recursively visiting
all `pkg.Imports` (deps-first: loop over imports at :447-451, append at :452). Since Phase 2
makes `expose "P"` append P to `A.Imports`, `visit(A)` recurses into P first → P lands in
Order before A → the checker's `ldr.Order` loop registers P's exported scope before A's
injection runs. Transitivity (P exposes Q, A exposes P) holds because P's own injection
already ran when A's runs. **Checker visit order == ldr.Order == topo order — same slice, no
separate ordering.** This resolution is load-bearing for Phases 3 and 5.

---

## Phase 1 — Frontend: token / keyword / AST / parser

**✅ STATUS (2026-07-10): LANDED on main — `4f584450`.** Implemented as designed
(contextual `expose` keyword, `DECL_EXPOSE`, `.bni`-only via `p.interfaceFile`, raw quoted
path in `Decl.Name`, `parse_expose_test.bn`). A minimal 2-lens adversarial review (parser/AST
correctness + enum blast-radius; downstream reachability + BUILDER + tests) came back clean —
notably confirming `DECL_EXPOSE` flows harmlessly through the loader/checker/IR passes (all
branch on specific kinds and skip unknown ones), so **no "not-yet-implemented" guard is needed**.

**Deliverable:** `expose "pkg/std/foo"` parses into a `DECL_EXPOSE` carrying the path string;
rejected outside `.bni`. **Deps:** none.

### 1(a) Files, functions, line ranges

**CORRECTION (R1):** the parser dispatch file is **`pkg/binate/parser/parser.bn`** (a
`parser/` subdirectory), NOT a package-root `parser.bn` as design/plan write it. Same for
`pkg/binate/parser/parse_decl.bn`. All parser edit sites use the subdirectory paths.

**CORRECTION (R1):** the `DECL_` enum in `pkg/binate/ast.bni` opens `const (` on **line 85**;
members span 86-93 (`DECL_FUNC=iota` .. `DECL_IMPL` at 92, `NUM_DECL_KINDS` at 93). A
`DECL_EXPOSE` member inserts between `DECL_IMPL` (92) and `NUM_DECL_KINDS` (93).

- `pkg/binate/ast.bni` — `DECL_` const block, insert `DECL_EXPOSE` after line 92; `Decl`
  struct is 293-349 (field-usage doc-comment 267-292).
- `pkg/binate/ast/ast.bn` — `DeclKindName(kind int)` switch, lines 81-100 (stops at
  `DECL_IMPL` case at 95-96); needs a `DECL_EXPOSE` case.
- `pkg/binate/parser/parser.bn` — `parseTopLevelDeclInner` at **345-367** (the fallthrough
  `errMsg("expected declaration")` is at :364). Called from `parseTopLevelDecl` (335-341,
  stamps `d.End`), driven by `ParseFile`'s top-level loop (194-210).
- `pkg/binate/parser/parse_decl.bn` — `parseImplDecl` at 103-119 is the MODEL for
  `parseExposeDecl` (captures `pos`, `p.expect(token.IMPL)`, builds `make(ast.Decl)` with
  `d.Kind`/`d.Pos`, no Name/Body).
- `.bni`-vs-`.bn` mode: the `@Parser` struct (`pkg/binate/parser.bni:10-33`) has field
  `interfaceFile bool` (:32), set in `newParser(lx, iface)` at parser.bn:33-40
  (`p.interfaceFile = iface`). `NewInterface`/`NewInterfaceCollecting` (parser.bn:16-18,
  27-29) pass `iface=true`. This is the existing `.bni`-only enforcement mechanism (used at
  `parse_func.bn:75`).
- Path-read pattern: `parseImportSpec` (parser.bn:316-331) reads a string literal:
  `if p.tok.Typ == token.STRING { spec.Path = p.tok.Lit; p.next() } else { p.errMsg(...) }`.
  **`p.tok.Lit` holds the raw literal INCLUDING quotes.**
- Error helpers: `errMsg(msg)` (parser.bn:139-141), `addError(pos, msg)` (144-149),
  `expect(typ)` (94-102), `got(typ)` (120-126).
- **RESERVED-path only** (skip if contextual): token enum is an iota block in
  `pkg/binate/token.bni` (`type Type int` at :6, `const ( ... )` :8-152), keywords between
  `keyword_start` (:19) and `keyword_end` (:44); `TypeName` switch in
  `pkg/binate/token/token.bn` (keyword cases 22-45); `Lookup` (token.bn:153-181) linear-scans
  the keyword range; `IsKeyword` (118-125) range-checks the sentinels. `C_CALL`/`C_GLOBAL`
  (builtin block :95/:98) are the precedent.

### 1(b) What to add/change

**Contextual path (recommended):**
- **`ast.bni`:** add `DECL_EXPOSE` enum member after `DECL_IMPL` (line 92), before
  `NUM_DECL_KINDS` (93). Add a row to the `Decl` field-usage doc-comment (267-292):
  `EXPOSE   Name (target path string, quotes included)`.
- **`ast.bni` Decl struct (293-349):** **no new field required** — reuse `Name @[]char`
  (:297) to hold the exposed path literal (mirrors `ImportSpec.Path`/`File.PkgName` storing
  the raw quoted literal). **Decision to record:** store the RAW QUOTED literal in `Decl.Name`
  and unquote downstream in the loader (Phase 2 already has `unquote`). Document this on the
  `Name` field comment so nobody double-unquotes.
- **`ast/ast.bn`:** add `case DECL_EXPOSE: return "EXPOSE"` to `DeclKindName` (after the
  `DECL_IMPL` case at 95-96), else it returns the default `"UNKNOWN_DECL"` in diagnostics/AST
  dumps. **This is a doc-omission callout: neither design nor plan mentions `DeclKindName`
  needs a case.**
- **`parser/parse_decl.bn`:** add `func parseExposeDecl(p @Parser) @ast.Decl` — capture
  `pos`; consume the lead-in (contextual: assert `p.tok.Lit == "expose"` then `p.next()`);
  `if !p.interfaceFile { p.errMsg("expose is permitted only in .bni interface files") }`;
  require STRING —
  `if p.tok.Typ == token.STRING { d.Name = p.tok.Lit; p.next() } else { p.errMsg("expected package path string after expose") }`;
  build `make(ast.Decl)` with `Kind = ast.DECL_EXPOSE`, `Pos = pos`, `Name = path`; return.
- **`parser/parser.bn` `parseTopLevelDeclInner` (before the :364 fallthrough):** add
  `if p.tok.Typ == token.IDENT && litEq(p.tok.Lit, "expose") { return parseExposeDecl(p) }`.
  **There is NO shared string-equality helper available to the parser** (`token.streq` is
  private; `pkg/std/strings` exports no `Equal`), so add a file-local `litEq` hand-rolled
  char-slice compare, mirroring `cCallRetSpecIsVoid` (parse_builtin.bn:190-192). The `.bni`
  restriction lives inside `parseExposeDecl` (via `p.interfaceFile`), NOT here — a stray
  `expose` in a `.bn` parses-then-errors rather than being mistaken for a statement.

**Reserved-path deltas (if chosen instead):** insert an `EXPOSE` enum member in
`token.bni` between `keyword_start`/`keyword_end`; add `case EXPOSE: return "expose"` to
`TypeName` (token.bn); `Lookup`/`IsKeyword` then auto-pick it up. In
`parseTopLevelDeclInner`, add `if p.tok.Typ == token.EXPOSE { return parseExposeDecl(p) }`
alongside the other keyword arms. **Cost:** inserting into the iota block shifts every
subsequent token value and grows `NUM_TOKENS` (benign — token values are not serialized
anywhere persistent, verified — but the contextual path avoids it entirely).

### 1(c) New files
None.

### 1(d) Tests
- **`pkg/binate/parser/parse_expose_test.bn`** (NEW) — `mkParser`/`mkBniParser` already exist
  (parser_test.bn:13-30). Assert:
  1. `mkBniParser("package \"a\"\nexpose \"pkg/std/foo\"")` → one `Decl` of `Kind ==
     ast.DECL_EXPOSE` with `Name == "\"pkg/std/foo\""` (the quoted literal).
  2. `mkParser(...)` with the same source → a parse error (expose rejected outside `.bni`).
  3. `expose` with no following string → error.

### 1(e) Verification
- `scripts/build-bnc.sh -o /tmp/bnc-p1` (verifies the tree still builds under the current
  compiler).
- Unit: run the `pkg/binate/parser` package tests. Also run `pkg/binate/ast` tests
  (`DeclKindName` is exercised there).
- BUILDER: `scripts/fetch-builder.sh --tool bnc` then build the tree with it — confirm the
  recognition code compiles under BUILDER (it should; no bnc-tree `.bni` uses `expose` yet).

### 1(f) Ordering + deliverable
No deps. Deliverable: `expose "P"` parses to a `DECL_EXPOSE`; `.bni`-only enforced; parser +
ast unit tests green.

---

## Phase 2 — Loader: dependency edge + surface hand-off

**STATUS (2026-07-10): committed on work-3 (`0592533e`), adversarial review running, awaiting
landing approval.** Implemented as designed: `Package.Exposes` field; a pure
`collectExposePaths(bniFile)` helper + a `recordExposes(pkg, bniFile)` helper (both in
loader_util.bn, read from `bniFile.Decls` directly — the "forwarder trap": the merged-only
prepend block is skipped for a pure forwarder); each target appended (deduped) to both
`pkg.Exposes` and `pkg.Imports` so the existing topo sort + cycle detector handle it for free;
`loader_expose_test.bn` unit tests (collectExposePaths + recordExposes-dedup). The
`recordExposes`/`collectExposePaths` extraction (vs an inline block) keeps `loader.bn` under
the 500-line soft limit (489) — the earlier soft-limit warning is resolved.

**Deliverable:** exposing P makes P a build-order dependency of A; expose cycles are rejected;
A carries an enumerable exposed-package list for the checker. **Deps:** 1.

### 2(a) Files, functions, line ranges (all recon-corrected)

**CORRECTION (R2):** the `Package` and `Loader` structs live in **`pkg/binate/loader.bni`**
(the package-root interface file), NOT in `loader.bn`.
- `Package` (loader.bni:17-23): fields are **exactly** `Path @[]char`, `BNI @ast.File`,
  `Merged @ast.File`, `Files @[]@ast.File`, `Imports @[]@[]char`. There is **NO
  `InterfaceOnly` field on `Package`** and **no exposed-package field of any kind**.
  (`Merged` is `@ast.File` — Binate has no `?` optional syntax; managed pointers are already
  nilable, so the R2-brief `@ast.File?` spelling is wrong.)
- `InterfaceOnly` is a `@[]@[]char` field on the **`Loader`** struct (loader.bni:51), a
  host-set path list (an interpreter-host mechanism to SKIP a package's `.bn`) — **unrelated
  to forwarders.**

**CORRECTION (R2):** design §3.1 and plan §2 say the `.bni`-only forwarder is exercised by the
`InterfaceOnly` path at `loader.bn:247-255`. **Both wrong:** (1) `:247-255` is the **not-found
guard** (`if bniFile == nil && len(files) == 0`), not a load path; the actual `.bni`-only
fallback is **`:366-369`** (`if merged == nil && bniFile != nil { merged = bniFile }`). (2)
`InterfaceOnly` is the host mechanism, not the forwarder path. The conclusion (".bni-only
loads fine today") is correct; the cited mechanism is not. A forwarder loads because
`len(files)==0` leaves `merged==nil`, the not-found guard passes (`bniFile != nil`), and the
`:366-369` fallback sets `merged = bniFile`.

Load-path anatomy in `loadPackage` (loader.bn:119-409), verified:
1. `.bni` parsed into `bniFile` (135-157); impl `.bn` into `files` (163-222).
2. FromBNI tag: `for i { bniFile.Decls[i].FromBNI = true }` (241-245) — **runs
   unconditionally over ALL bniFile decls** (only guarded by `bniFile != nil`).
3. Not-found guard (247-255).
4. `merged` built from `files` (258-265) — **stays nil when `len(files)==0`** (the forwarder
   case).
5. `gateMerged` (271-274).
6. **PREPEND/MARK block (277-364) — GUARDED BY `if bniFile != nil && merged != nil` (:277).**
   The loop over `bniFile.Decls` (293-326) that classifies decls, the `!isVarGroup` var-group
   exclusion (305-307), and `markBniExportedFuncs`/`markBniExportedVars` (343-344) all live
   **inside** this guard.
7. `.bni`-only fallback (366-369): `if merged == nil && bniFile != nil { merged = bniFile }`.
8. Register package (371-390); **extract-imports block (382-389)**: loop over
   `merged.Imports`, `unquote` each (:384), append to `pkg.Imports` if new (dedup via
   `containsStr`, :385). `l.Packages = Append(pkg)` (:390).
9. Recurse over `pkg.Imports` (397-407): per-dep cycle check (`l.isLoading` → `cycleErrorMsg`)
   then `loadPackage` if not loaded. `popLoading` (408).

**CORRECTION (R2):** design §3.3 / plan §2 cite `loader.bn:382-389` as the topo sort. That
range is the **extract-imports block** (populating `pkg.Imports`); the topo sort proper is
`computeOrder` (428-437) + `visit` (440-453). Both center on `pkg.Imports`, so the substance
holds. `cycleErrorMsg` is `loader_util.bn:296-305`.

**THE PLACEMENT TRAP (recon R2 — the single most important loader fact, in NEITHER doc):**
the loop over `bniFile.Decls` at loader.bn:293 sits **inside** the `if bniFile != nil &&
merged != nil` guard (:277), which is **FALSE for a pure forwarder** (`merged` is nil until
the :367 fallback). So `DECL_EXPOSE` extraction **CANNOT** be added inside that block — it
would never run for the exact case (a forwarder) the feature exists for. It must run
**unconditionally** over `bniFile.Decls`.

### 2(b) What to add/change

- **`pkg/binate/loader.bni` — `Package` struct (17-23):** add a field
  `Exposes @[]@[]char` (the resolved/unquoted paths of every `expose "P"` in A's `.bni`).
  Update the struct doc-comment (loader.bni:8-16). Initialize it explicitly if bootstrap
  zeroing is a concern (compare `NewLoader`'s explicit slice inits at loader.bn:19-25). This
  is the record the checker (Phase 3) reads to know which surfaces to inject.
- **`pkg/binate/loader/loader.bn` — new expose block, placed AFTER the `:366-369` fallback
  (where `merged` is guaranteed non-nil for any loadable package) and BEFORE register-package
  at :371.** For each `d.Kind == ast.DECL_EXPOSE` in `bniFile.Decls`:
  - `unquote` the target path from `d.Name` (same as import paths at :384).
  - append it to **BOTH** `pkg.Imports` (dedup via `containsStr` as at :385) **AND**
    `pkg.Exposes`.
  - **This MUST run before the recursion loop at :397-407** (and before `l.Packages = Append`
    at :390 is fine), or P won't be recursively loaded/cycle-checked. Because the recursion
    walks `pkg.Imports`, appending P to `pkg.Imports` here makes P get loaded, cycle-checked,
    and topo-ordered **for free** — the existing topo sort (428-453) and cycle detector
    (399-401) require no expose-specific change.
  - Iterate `bniFile.Decls` directly (guarded only by `bniFile != nil`), NOT inside the :277
    `merged != nil` block. Recommended concrete placement: fold the expose-path collection
    into or immediately before the extract-imports block (382-389), after the :367 fallback
    guarantees `merged != nil`. (Note: `bniFile.Decls` is what carries the expose decls — the
    expose paths are NOT in `merged.Imports`, so they must be collected from `bniFile.Decls`
    separately, then merged into `pkg.Imports`.)
- **Var-group / double-register (design §5 open question) — no loader code change needed
  here, but a doc-comment:** expose does **NOT** prepend any of P's decls into `A.Merged` (it
  is surface-only; A emits nothing). The var-group hazard the `!isVarGroup` guard (:305)
  prevents is A's OWN `.bni` groups vs A's OWN `.bn`; the expose analogue (A must reference
  P's storage, never redefine it) is handled at the checker/IR layer (Phases 3/4). The
  loader's only jobs are: (1) do NOT copy any of P's `DECL_GROUP`/`DECL_VAR`/`DECL_TYPE`/
  `DECL_FUNC` into `A.Merged.Decls`, and (2) add the dep edge. Add a doc-comment near the new
  expose block stating this (mirror the :299-304 rationale: a var's storage is single-homed;
  A must reference P's, never redefine).

**CORRECTION (R2, minor):** the `loader_util.bn:131` doc-comment references a function
`MergeBniInto` that **does not exist** — the `.bni`→merged merge is done inline in
`loadPackage` (277-364). Stale comment; not load-bearing for expose, but do not quote it.

### 2(c) New files
None.

### 2(d) Tests
- **`pkg/binate/loader/loader_test.bn`** (extend): assert a package with `expose "P"` in its
  `.bni` and no `.bn` loads with `pkg.Merged == pkg.BNI` (forwarder path), `pkg.Exposes`
  containing P's unquoted path, and P appended to `pkg.Imports`.
- Cycle test: A exposes P, P exposes A → `l.Errors` contains a cycle message (via
  `cycleErrorMsg`).
- Transitive: A exposes P, P exposes Q → all three in `ldr.Order` with Q before P before A.
- Conformance coverage is added in Phase 6's test bundle (forwarder / transitive) since it
  requires the full pipeline.

### 2(e) Verification
- `scripts/build-bnc.sh -o /tmp/bnc-p2`.
- Unit: `pkg/binate/loader` package tests.

### 2(f) Ordering + deliverable
Dep: 1 (`ast.DECL_EXPOSE` must exist). Deliverable: expose adds the dep edge (P before A in
`ldr.Order`), cycles rejected, `pkg.Exposes` populated for Phase 3.

---

## Phase 3 — Checker: scope injection (surface merge)

**Deliverable:** `A.X` type-checks with P's identity for **all** symbol kinds; exposed
types/impls link once scope-injected here (piggybacking on existing alias identity — test
`941`); funcs/vars/consts still mis-mangle until Phase 4. **Deps:** 2.

### 3(a) Files, functions, line ranges (recon-corrected)

**CORRECTION (R3):** the `@Symbol`/`@Scope` structs live in **`pkg/binate/types.bni`** (the
package-root `.bni`), NOT `types/*.bn`.
- `@Symbol` (types.bni:805-819): `Name @[]char; Type @Type; Kind int; PkgPath @[]char;
  ConstVal int; HasConstVal bool`. **CRITICAL FINDING:** `@Symbol` **ALREADY has a
  `PkgPath @[]char` field** (:809). design §3.2/§3.4 and plan §2 say the resolved-home stamp
  "does not exist today" — **imprecise:** the FIELD exists (used for `SYM_PKG` alias symbols),
  set in exactly two places: `scope.bn:184` (`definePkg` sets `sym.PkgPath`) and
  `checker.bn:161` (a straight copy in the `.bni`→`.bn` merge). Every READ of `Symbol.PkgPath`
  in `types/` is guarded by `sym.Kind == SYM_PKG` first
  (`check_expr_access.bn:32/145/221`, `resolve_type.bn:171→175`,
  `check_generic_type.bn:44→46`, `check_builtin.bn:219`; and the dup-import check at
  `bni_scope.bn:355-356`). What is ABSENT is the *behavior* of stamping a resolved home on
  injected NON-pkg symbols.
- `@Scope` (types.bni:821-832): `Parent @Scope; Syms @[]@Symbol; DelegateDefine bool`.
  Methods in `scope.bn`: `LookupLocal` (17-24), `Define` (27-45; overwrites same-name in
  place at 38-43, else appends), `Lookup` (48-58).
- SYM kinds (types.bni:792-803): `SYM_VAR, SYM_CONST, SYM_TYPE, SYM_FUNC, SYM_PKG,
  SYM_INTERFACE`.

**CORRECTION (R3):** design §3.4 / plan §3 cite the five injection helpers as all at
`scope.bn:130-186`. **`defineInterface` is NOT in `scope.bn`** — it is at
`check_interface.bn:137-143`. In `scope.bn`: `defineType` (131), `defineConst` (140),
`defineConstVal` (151), `defineFunc` (162), `defineVar` (171), `definePkg` (180, the only one
setting `PkgPath` at :184).

**CORRECTION (R3):** plan §2 cites "`Scope.Define`/`Lookup` (`scope.bn:26-45`)" — `Define` is
27-45 but `Lookup` is **48-58**.

Cross-package resolution (how A.X resolves today), verified:
- `checkSelectorExpr` (check_expr_access.bn:216-256): the pkg-access arm (218-240) looks up
  the ident in `c.Scope`; if `SYM_PKG`, calls `lookupPackage(c, sym.PkgPath)` → pkg's
  `@Scope`, then `pkgScope.Lookup(e.Name)` → member `@Symbol`, and **returns only
  `member.Type`** (:239) — no home/owner info surfaced. Confirms the wire does not exist.
- `resolveQualifiedSym` (check_expr_access.bn:140-148): the reusable helper returning the
  member `@Symbol` of any kind.
- `lookupPackage` (checker.bn:68-75): linear scan of `c.Packages` (`@[]@PkgEntry`) by Path,
  returns the `@Scope`. `PkgEntry` = `{Path @[]char; Scope @Scope}` (types.bni:1199-1202).
  `registerPackage` (checker.bn:78-90).
- **`PackageType(pkgPath, name) @Type`** (checker.bn:39-45): does EXACTLY the
  `(pkgPath,name)→scope→Lookup(name)→sym.Type` walk. This is the model for Phase 4's
  `PackageMemberHome`.

Scope-build (A's surface scope):
- `buildScopeFromFile` (bni_scope.bn:19-231): Pass 1 pre-registers type/interface placeholders
  (44-107), Pass 2 resolves funcs/consts/vars/types/interfaces/groups into `s` (110-227), then
  `c.Scope = savedScope; return s` (229-230). Registered by path via `LoadPackageInterface`
  (checker.bn:93-105 → `registerPackage`), which is **idempotent** (early-returns if already
  registered, checker.bn:94 — no double-injection risk).
- Type aliases → identity via `MakeAliasType` → `TYP_ALIAS` (bni_scope.bn:299-306,
  `resolveTypeDeclInScope`). Interfaces via `defineInterface` with the resolved
  `TYP_INTERFACE` (`resolveInterfaceDeclInScope`, bni_scope.bn:240-246 — the `interface X = Y`
  form).
- `.bni`→`.bn` merge in `checkPackageImpl` (checker.bn:156-163): copies `Name/Type/Kind/
  PkgPath` from the `.bni` scope into the `.bn` scope.

**HYGIENE CALLOUT (R3):** `bni_scope.bn` is **already 518 lines** (over the 500-line soft
cap). Injecting the copy loop inline there worsens an existing warning — **factor the
injection into a helper and split the file** (natural boundary: the expose-injection helper
into a new `bni_scope_expose.bn`, or a broader Pass-1/Pass-2 split). Do NOT camouflage size.

### 3(b) What to add/change

- **Resolved-home field decision (record it):** the design leaves it open whether to reuse
  `PkgPath` or add a new field. **Recommendation: add a distinct `HomePkg @[]char` to
  `@Symbol` (types.bni:805-819).** Reusing `PkgPath` is *safe from current reads* (all gate
  on `SYM_PKG`) but semantically overloaded ("the path a `SYM_PKG` alias points to" vs "the
  home of this func/var/const") and makes that `SYM_PKG`-gating invariant load-bearing; a
  stray unguarded read of `PkgPath` on a func/var/const would misbehave, and the dup-import
  check at `bni_scope.bn:355-356` reads `PkgPath`. A distinct `HomePkg` avoids the coupling.
  Set it only on expose-injected func/var/const (native decls leave it empty).
- **`scope.bn` define helpers (140-177):** add home-carrying variants (or an optional param)
  to `defineFunc`/`defineVar`/`defineConst` so Phase-3 injection can stamp each injected
  func/var/const symbol with P's full path.
- **`check_interface.bn` `defineInterface` (137-143):** if a distinct `HomePkg` is added,
  interfaces injected via expose should carry it too (though interface *identity* already
  flows through the shared `@Type`; `HomePkg` on the injected symbol is for uniformity + the
  collision namespace). **This is the doc-omitted helper — do not forget it.**
- **`bni_scope.bn` `buildScopeFromFile` — inject at lines 228-229** (after the Pass-2 loop
  ends at 227, before `c.Scope = savedScope; return s` at 229-230): **[REVIEW] read the exposed
  paths from `f.Decls`** (scan for `DECL_EXPOSE`) — the `.bni` AST `f` is ALREADY the sole
  parameter `buildScopeFromFile(c, f)` receives (it is reached only from
  `LoadPackageInterface`, checker.bn:101), so injection needs **no `pkg.Exposes` threading and
  no `LoadPackageInterface`/`buildScopeFromFile` signature change** (which would otherwise force
  a 7-call-site sweep: compile.bn:31/59, interp/check.bn:21/46, repl/session.bn:152,
  repl/mid_session_import.bn:65, bnlint/main.bn:267). For each exposed path P,
  `lookupPackage(c, P)` (guaranteed registered — P precedes A in `ldr.Order`), iterate P's
  `scope.Syms`, and copy each **exported** symbol into `s`, **sharing the same `.Type` pointer**
  (preserves type/interface identity for free). **Skip `SYM_PKG` symbols** (P's own import
  aliases are not part of P's exported surface). Stamp the resolved home (P's path — or, for a
  Q-origin re-exposed symbol, the symbol's ALREADY-stamped `HomePkg`) onto injected
  func/var/const.
- **[REVIEW] Copy the FULL `@Symbol`, not via `defineConst`.** `defineConst` (scope.bn:140-146)
  sets only `Name/Type/Kind` — it does NOT set `ConstVal/HasConstVal` (only `defineConstVal`,
  scope.bn:151-160, does). An exposed const injected via `defineConst` would **lose its folded
  value**, which the checker's `evalConstInt` (array dims) and the Phase-4 `gen_const_fold` arms
  read (`sym.HasConstVal/ConstVal`, types.bni:817-818) — feeding straight into the silent-wrong-
  value risk. So inject via `make(Symbol)` + **full field copy** (`Name/Type/Kind/PkgPath/
  ConstVal/HasConstVal` + the new `HomePkg`), the mechanism the CRITICAL bullet below already
  requires; the named `defineType/defineFunc/defineVar/defineConst/defineInterface` helpers are
  the *shape* to mirror, not literal calls (they'd drop const values and can't stamp `HomePkg`).
- **New helper `injectExposedSurface(c, s, exposedPaths)`** (in a split-out
  `bni_scope_expose.bn`): the copy-and-stamp loop. For **transitivity**, since P's scope
  already contains P's own expose-injected symbols (P was visited earlier per `ldr.Order`), a
  straight copy of P's exported `Syms` already includes Q's — **no extra recursion needed** —
  but PRESERVE each copied symbol's already-set `HomePkg` so `A.X` for a Q-origin symbol still
  mangles to Q, not P.
- **CRITICAL (R3 risk):** the copied `@Symbol` must be a **NEW object** (`make(Symbol)` +
  field copy, as `checker.bn:157-162` already does for the `.bni`→`.bn` merge), never P's
  original symbol object injected by reference — because stamping `HomePkg` on the injected
  symbol must NOT mutate P's original. The shared `.Type` pointer is correct (identity); the
  `@Symbol` wrapper is not shared.
- **`checker.bn` `.bni`→`.bn` merge (156-163):** if a distinct `HomePkg` is added, also copy
  `sym.HomePkg` here (alongside the `PkgPath` copy at :161), so the stamp survives into the
  `.bn`-side package scope (relevant if A's own `.bn` references its own exposed surface —
  surface-only means it normally won't, but keep the copy complete).
- **Materialize the transitive-closure surface** for Phase 5: the injection helper is the
  natural place, but per R5 the collision detection itself must be a **separate pass** (see
  Phase 5) because `Scope.Define` silently overwrites (scope.bn:38-43) — a collision would be
  masked, not errored. So the helper materializes/enumerates; the erroring is Phase 5.

### 3(c) New files
- **`pkg/binate/types/bni_scope_expose.bn`** (NEW) — the `injectExposedSurface` helper
  (splitting it out also relieves the 518-line `bni_scope.bn` over-cap warning).

### 3(d) Tests
- **`pkg/binate/types/bni_scope_expose_test.bn`** (NEW): after injecting P's surface into A's
  `@Scope`, assert `A.Lookup(X)` returns a symbol with P's original `.Type` pointer and
  `HomePkg == P`'s path; interfaces bound via `defineInterface` share the `@Type`; the
  transitive closure (A exposes P exposes Q) enumerates Q's members in A's scope with
  `HomePkg == Q`.

### 3(e) Verification
- `scripts/build-bnc.sh -o /tmp/bnc-p3`.
- Unit: `pkg/binate/types` package tests.
- Hygiene: `scripts/hygiene/run.sh` (confirm the `bni_scope.bn` file-length warning did not
  worsen — the split should reduce it).
- Type/impl end-to-end: exposed types + impls should already link (identity substrate).
  Conformance `941_xpkg_alias_impl_dispatch` proves the impl-dispatch substrate; the
  expose-side type-identity is covered by the Phase-6 conformance bundle.

### 3(f) Ordering + deliverable
Dep: 2 (`pkg.Exposes`). Deliverable: `A.X` type-resolves to P's identity for every symbol
kind; types/impls link now; funcs/vars/consts type-check but mis-mangle until Phase 4.

---

## Phase 3.5 — Consumer-side registration of a forwarder's expose-closure (CRITICAL — added by [REVIEW])

**Deliverable:** in any module that directly imports a forwarder A which exposes P, P's
**func / const / var** surface is REGISTERED/DECLARED in that module under P's **home** names —
so the Phase-4 resolved-home reference `pkg/P.X` resolves to a symbol the module actually has.
**Deps:** 2 (needs `pkg.Exposes`); pairs with 4 (mangling is meaningless without this).
**MVP-critical — Phase 4's deliverable is NOT met without it.**

### Why this is needed (the gap Phase 4 alone leaves)

Phase 4 makes the 9 reference sites *produce* the name `pkg/P.X`. But emitting the right name is
only half the wire: the consuming module must also have `pkg/P.X` **registered** (consts in
`gc.Mod.Consts`, vars in `gc.Mod.GlobalVars`) or **declared** (func externs) under P's home. It
is not, for the flagship pure-forwarder case:

- The consumer imports forwarder **A**; A's `.bni` has **no** func/const/var decls (only
  `expose`), so iterating A's own decls registers nothing for the forwarded members.
- The exposed **P** is a **transitive-only** import (the consumer imports A, not P). The
  consumer's func/const/var registration is **direct-import-scoped**: imported global-var
  externs come only from `registerImportVarExtern` (← `ir.RegisterImports`, fed only
  direct-import files), and plain non-generic funcs of a transitive package are registered only
  behind the `fileHasGenericDecl` gate (`registerGenericBodyExternDeps`). The transitive pass
  that DOES run over all of `ldr.Order` (`registerAllStructTypes`) registers **structs /
  interfaces / generics only — not funcs/consts/vars** (verified: `gen_module.bn:93`,
  `gen_register_import.bn:297`).

Net, with Phase-4 home-mangling in place but no Phase 3.5:
- exposed **VAR** read (`gen_selector.bn:303` → `lookupImportedGlobalRead('pkg/P.V')`) misses →
  the loud IR-gen catch-all abort (a compiler internal error);
- exposed **FUNC** ref (`gen_util.bn:106` → `gen_call.bn:242`) emits a call to a symbol the
  module never `declare`d → **link failure / ABI corruption** on native + LLVM;
- exposed **CONST** fold (`gen_const_fold.bn:{69,244,330,382}`) misses `lookupConst` → the caller
  substitutes a default → **silent wrong value** (array dim 0, wrong signedness/float-ness).

This is exactly the "dangling-symbol miscompile" the resolved-home sweep was meant to preclude,
and it hits `NNN_expose_forwarder` / `NNN_expose_var_identity` / `NNN_expose_const_fold`. Phase
4(b)'s old "B's members are registered under B's path" note assumed registration in the
consumer; that assumption is false for a transitive-only B — corrected below.

### 3.5(a) Edit sites (reviewer-traced; the implementation MUST re-run the sweep — see below)

The consumer's import-registration is **duplicated across every embedding of the compile
pipeline**; a repo-wide grep of the registration entry points is mandatory before editing (the
plan's own "enumerate sweep sites repo-wide, not a guessed subset" rule). Reviewer-traced set to
confirm and extend: `cmd/bnc/compile_imports.bn` (`registerMainImports` /
`registerPackageImports` → `ir.RegisterImports` gen_import.bn:156, `registerImportVarExtern`
gen_import.bn:433-467, `RegisterFuncExterns` gen_register_import.bn:220), and the parallel
copies in `pkg/binate/interp/imports.bn`, `pkg/binate/repl/ir_imports.bn`, and
`cmd/bnc/test.bn`. **Do not trust this list as complete** — grep for the `RegisterImports` /
`registerImportVarExtern` / `RegisterFuncExterns` call sites and cover all of them; a missed
embedding leaves that host (VM, REPL, test runner) with the same dangling-symbol miscompile.

### 3.5(b) What to add/change

- When collecting a module's **direct** imports for registration, for each direct import that
  has a non-empty `pkg.Exposes`, also collect that import's **transitive expose-closure**
  (A exposes P exposes Q → {P, Q, …}) and feed each closure package's `.bni` file into
  `ir.RegisterImports` / `RegisterFuncExterns` / `registerImportVarExtern` **keyed on the
  closure package's HOME path** (not A's alias) — so P's consts land in `gc.Mod.Consts`, vars in
  `gc.Mod.GlobalVars`, and funcs as declared externs, all under P's path. This makes the
  Phase-4 `pkg/P.X` names resolve to real registered symbols.
- **Alternative considered:** drop the `fileHasGenericDecl` gate so the extern-only registration
  runs for ALL of `ldr.Order` (any transitive dep's plain funcs/vars get declared). Simpler, but
  broader — it changes registration for *every* transitive dep, not just expose-closures, so it
  risks name/ABI surprises and duplicate-extern handling beyond expose. **Prefer the
  expose-closure-scoped approach** unless recon shows the broad path is safe and cheaper.
- De-dup: a closure package reached via both a direct import and an expose must be registered
  once (guard on already-registered, as `RegisterImports` and the `ldr.Order` passes already do).

### 3.5(c) Tests (acceptance — a name-only test CANNOT catch this)

- The **`NNN_expose_forwarder`** and **`NNN_expose_var_identity`** conformance tests (Phase 6)
  MUST exercise a **PURE forwarder**: `main` imports **only** A (the forwarder), **not** P, and
  references an exposed **plain non-generic func** and an exposed **var** through A. Run them in
  the **compiled + native** modes where a missing `declare` is a hard link error (not just
  `builder-comp-int`, where the VM's late binding could mask it).
- **`NNN_expose_const_fold`** is the acceptance gate for the const half: `[fwd.C]int` /
  `1 << fwd.SHIFT` through a pure forwarder — a fold miss is a silent wrong value, so this
  detects a Phase-3.5 registration gap that no name test would.

### 3.5(d) Ordering + deliverable
Dep: 2. Must land **with or before** Phase 4 (Phase 4's forwarder link is not real without it).
Deliverable: an exposed func/var/const reached through a pure forwarder is registered under P's
home in every consumer host → the Phase-4 names LINK.

---

## Phase 4 — Resolved-home mangling for func/var/const (THE CRUX)

**Deliverable:** `A.X` for exposed funcs/vars/consts links to P's symbol; a pure forwarder
emits nothing; existing mangling byte-identical. **Deps:** 3 **and 3.5**. **MVP-critical.**

**[REVIEW] Mangling is only HALF the wire.** This phase makes references *produce* `pkg/P.X`;
**Phase 3.5 makes `pkg/P.X` a registered/declared symbol in the consumer.** Neither alone
suffices — land 3.5 with or before 4, and treat the forwarder/var/const-fold conformance tests
(which exercise a *pure* forwarder in compiled + native modes) as the joint acceptance gate.

### The wire (Phase-3 stamp → reference-keyed IR-gen lookup → chokepoint)

Three coupled pieces:

1. **Phase-3 stamp:** each expose-injected func/var/const `@Symbol` carries `HomePkg = P`'s
   path (Phase 3).
2. **Reference-keyed IR-gen lookup:** a new checker method
   **`func (c @Checker) PackageMemberHome(pkgPath, name *[]readonly char) @[]char`** added
   alongside `PackageType` (checker.bn:39-45), returning `sym.HomePkg` (empty when unset or
   not found) via the same `lookupPackage(pkgPath) → Lookup(name)` walk. Add to the
   `types.bni` interface too. IR-gen already holds `gc.Mod.Checker` and uses it
   (`gen_import_const.bn:18-24` via `ExprType`); `PackageType` is the exact analog. This
   AVOIDS storing per-expr home in the checker (`checkSelectorExpr` surfaces only
   `member.Type`, :239 — re-walking the scope at IR-gen time is the smaller change).
3. **Chokepoint at IR name-building:** at each **must-change reference site** (table below),
   route the qualified-name build through the resolved home: resolve `prefix` (the source
   selector ident, e.g. `e.X.Name`) → `fullPath` via `resolveImportPkg` as today; then consult
   `PackageMemberHome(fullPath, suffix)` — if it returns a non-empty home, use THAT as the
   qualifier; else use `fullPath` (byte-identical to today).

**Recommended mechanism (avoids a global gate — see risks): a dedicated
`buildQualNameHomed(m, prefix, suffix)` used ONLY at the must-change reference sites**, leaving
`buildQualName` (gen_util_literals.bn:36-42) untouched for the ~50 registration/generic/type
sites. The alternative — gating inside `buildQualName` on `len(m.CurrentImportAlias)==0` (since
generic-body arms always pass `CurrentImportAlias` as prefix, they self-exclude) — requires
**proving** that invariant for every arm and risks wrongly suppressing a genuine `pkg.X`
reference lexically inside a monomorphized body. The dedicated-function approach needs no
invariant proof.

### Core helpers (recon-corrected cites)

**CORRECTION (R4):** `resolveImportPkg` is at **gen.bn:57-64** (design/plan say `:57-63` —
the body closes at :64, one line short). It walks `m.ImportAliasNames`/`m.ImportAliasPaths`
parallel arrays, returns the matched full path, else `buf.CopyStr(alias)` unchanged. Keys
ONLY on the alias string; knows nothing of the member name.

**CORRECTION (R4):** `funcRefName` is at **gen_util.bn:77-109** (design says `:77-108` — the
closing brace is :109; :108 is the final `return ""`). The generic-body `CurrentImportAlias`
arm (86-91, VERIFIED exact) and the SELECTOR chokepoint (:106) confirmed.

`buildQualName(m, prefix, suffix)` at gen_util_literals.bn:36-42 (confirmed exact): body is
`qn.Write(resolveImportPkg(m, prefix)); qn.WriteByte('.'); qn.Write(suffix)`. It is the only
helper seeing the (alias, member) PAIR — the natural chokepoint.

`funcRefName` is called at `gen_call.bn:242` (direct call), `gen_util.bn:139`
(`genExprOrFuncRef` func-value), `gen_method_value_recv.bn:57`, `gen_short_var.bn:85`,
`gen_stmt.bn:231/248` — **none call `buildQualName`/`resolveImportPkg` directly** (which is why
they are not in the 75-hit grep); they are covered **transitively** by fixing gen_util.bn:106.

### The full triaged reference-site table (recon R4)

Repo-wide grep `grep -rn 'resolveImportPkg\|buildQualName' pkg/binate/ir/*.bn` returns
**EXACTLY 75 hits across 22 files** (confirmed). Of these: 11 are pure comments, 2 are test
lines, ~62 executable. File set (22): `gen_call, gen_const_fold, gen_expr, gen_func,
gen_iface_registry, gen_iface, gen_impl_recvname, gen_impl, gen_import_const, gen_import,
gen_method_value_recv, gen_method_value, gen_method, gen_module, gen_register_import,
gen_selector_type, gen_selector, gen_type_resolve, gen_util_literals_test, gen_util_literals,
gen_util, gen.bn`.

**[REVIEW] Completeness rests on the UNION pattern, not the two-symbol one.** Per the plan's own
"enumerate with a deliberately over-broad pattern" rule, the sweep was cross-checked against the
broader `resolveImportPkg|buildQualName|methodQualName|buildMethodQualName|qualifyForCurrentModule`.
The extra qualified-name builders — `methodQualName` (gen_method.bn:21),
`buildMethodQualName` (gen_method.bn:483), `qualifyForCurrentModule`, `splitQualName` — are all
**type-identity-keyed** for expose's purposes (method values/expressions route through the
RECEIVER type's IR-gen name, `buildMethodQualName(gc.PkgPath, recvTypeName, …)` at
gen_method_value.bn:135 / gen_method.bn:164, which already follows type identity) → **LEAVE**. So
the 9-site table is complete for func/var/const references, and a future method-value-through-
expose refinement is explicitly on record as the place to re-triage, not a silent escape.

#### MUST-CHANGE (spelling-driven func/var/const references in USER code)

| Site | Kind | Why it must change |
|---|---|---|
| **`gen_util.bn:106`** | func-value / direct-call / method-value | funcRefName SELECTOR arm `buildQualName(gc.Mod, e.X.Name, e.Name)` — THE chokepoint for ALL cross-package func-value/direct-call/method-value refs (reached transitively from `gen_call.bn:242`, `gen_util.bn:139`, `gen_stmt.bn:231/248`, `gen_short_var.bn:85`, `gen_method_value_recv.bn:57`). Route through the homed path. |
| **`gen_selector.bn:303`** | var READ **+** const READ | **DOC-MISSED SITE (a real omission from the plan's enumeration).** `buildQualName(ctx.Gc.Mod, e.X.Name, e.Name)` builds `qualName` used for BOTH the imported-var read (`lookupImportedGlobalRead`, :306) AND the imported-const read (Consts scan, :308-327). A `pkg.V`/`pkg.C` read; both resolve here. |
| **`gen_func.bn:339`** | var WRITE / addr-of | `genImportedVarLvalue`: `lookupImportedGlobalPtr(..., buildQualName(ctx.Gc.Mod, e.X.Name, e.Name))` for `pkg.V = x` / `&pkg.V`. |
| **`gen_selector_type.bn:46`** | var TYPE | `getSelectorType` imported-var-type arm: `buildQualName(ctx.Gc.Mod, e.X.Name, e.Name)` so `pkg.V.field` lvalue resolves to the same registered name as read/write. |
| **`gen_expr.bn:444`** | func reference (`_func_handle`) | RAW_FUNC_ADDR SELECTOR: `buildQualName(ctx.Gc.Mod, arg.X.Name, arg.Name)` then `EmitFuncHandle`. |
| **`gen_const_fold.bn:69`** | const (int) fold | `evalConstExpr` SELECTOR: `buildQualName(gc.Mod, e.X.Name, e.Name)` → `lookupConst`. |
| **`gen_const_fold.bn:244`** | const (bool) fold | `evalConstBool` SELECTOR: `lookupConstBool(buildQualName(...))`. |
| **`gen_const_fold.bn:330`** | const (signedness) fold | `constOperandIsUnsignedInt` SELECTOR: `lookupConst(buildQualName(...))`. |
| **`gen_const_fold.bn:382`** | const (float-ness) fold | `isFloatExpr` SELECTOR: `isFloatConstIdent(buildQualName(...))`. |

**The four `gen_const_fold` SELECTOR arms are the most dangerous** (R4 risk): they feed
CONSTANT FOLDING (array dims, `iota`, signedness, float-ness) evaluated at checker/IR time — a
resolved-home miss yields a **WRONG FOLDED VALUE (silent)**, not a link error. A byte-identical
NAME test is insufficient here; Phase 4 needs a **value-level conformance test** (e.g.
`[expose.C]int` array dimension, or `1 << expose.SHIFT`).

**CORRECTION (R4):** plan §3 lists `gen_call.bn:162` as "the call target (must change)".
**WRONG:** `:162` is a GENERIC cross-package head resolution
(`resolveImportPkg(ctx.Gc.Mod, e.X.X.X.Name)` → `lookupGenericDeclPkg`), which is
identity-driven and must be **LEFT**. The real plain-call target is `gen_call.bn:242`
(`funcRefName`), whose name-building lives at the chokepoint `gen_util.bn:106`.

**CORRECTION (R4):** plan §3 lists `gen_import_const.bn:41` as "imported-const (must change)".
**WRONG:** `:41` is REGISTRATION (`registerImportConstGroup` folds an imported const group into
`ModuleConsts` under the definer's alias) — definer-alias-keyed, **LEAVE it**. The const READ
sites that change are `gen_selector.bn:303` and `gen_const_fold.bn:{69,244,330,382}`.

**CORRECTION (R4):** the plan's "var read/write `gen_func.bn:339` / `gen_selector_type.bn:46`"
labels are imprecise — those are var WRITE/addr (`:339`) and var TYPE (`:46`); the var READ
path is `gen_selector.bn:303` (the omitted site). Precise split: **read = gen_selector.bn:303,
write/addr = gen_func.bn:339, type = gen_selector_type.bn:46.** This is exactly why the plan's
own "enumerate from the grep, not a guessed subset" rule applies — building the sweep from the
plan's listed sites (rather than the grep) misses `gen_selector.bn:303`.

#### LEAVE (identity- or definer-alias-keyed, or generic-body, or non-reference)

- **Generic-body `CurrentImportAlias` arms (preserve byte-identical — the divergence
  boundary):** `gen_util.bn:87` (funcRefName generic-body func), `gen_const_fold.bn:60`
  (evalConstExpr IDENT retry), `gen_const_fold.bn:236` (evalConstBool IDENT retry),
  `gen_const_fold.bn:320` (constOperandIsUnsignedInt IDENT retry), `gen_expr.bn:106`
  (EXPR_IDENT const), `gen_type_resolve.bn:133` (type). These name the DEFINING package's
  native member (the generic body was written in that package's namespace), so resolved-home
  must NOT fire here.
- **Generic cross-package heads (identity):** `gen_call.bn:162` (`lookupGenericDeclPkg`),
  `gen_type_resolve.bn:161` (`lookupGenericTypeDeclPkg`), `gen_iface.bn:29`
  (`instantiatedIfaceLookupPkg`), `gen_method_value_recv.bn:223` (generic-instantiation callee
  defining pkg).
- **Types (identity via `TYP_ALIAS`):** `gen_type_resolve.bn:94/133` (struct/alias resolve).
  An exposed type is a `TYP_ALIAS` whose Target carries the home; `resolveTypeExpr` follows
  identity; `ResolveAlias` (types_query.bn:33-38) peels the chain. No mangling change.
- **Impls / receivers (identity):** `gen_impl_recvname.bn:39-51` (`recvBaseNameAndPkg` reads
  the RESOLVED type's home), `gen_impl.bn:58/101/115/278/281/292/294/306/308`. Test `941`
  proves cross-package alias impls dispatch for free.
- **Interfaces (identity by defining package):** `gen_iface.bn:29/85/125/155/165`,
  `gen_iface_registry.bn:75/89/179/192/269`.
- **Registration passes (stamp the DEFINING/importing alias correctly already):**
  `gen_import.bn:189/254/275/311/323/331/374/390/458/463`,
  `gen_register_import.bn:43/57/80/135/182/247`, `gen_module.bn:115/135/164`,
  `gen_import_const.bn:41` (imported-const registration).
- **Definitions / non-sites:** `gen.bn:57` (`resolveImportPkg` itself — the fallback the new
  lookup delegates to), `gen_util_literals.bn:36` (`buildQualName` definition).
- **Comments (11):** `gen_iface_registry.bn:264`, `gen.bn:50`, `gen_util_literals.bn:31`,
  `gen_import.bn:132/162`, `gen_iface.bn:118`, `gen_impl_recvname.bn:91`, `gen_method.bn:480`,
  `gen_method_value.bn:112`, `gen_module.bn:98`.
- **Tests (2):** `gen_util_literals_test.bn:110/115` (`TestBuildQualName` — extend, don't
  change).

### 4(a) Edit sites (concrete)

- **`pkg/binate/types.bni` + `pkg/binate/types/scope.bn`:** add `HomePkg @[]char` to `@Symbol`
  (types.bni:805-819) — do NOT reuse `PkgPath` (it drives the `SYM_PKG` dup-import check at
  bni_scope.bn:355). Add home-carrying variants to `defineFunc`/`defineVar`/`defineConst`
  (scope.bn:140-177). (Phase 3 does the field add; restated here since Phase 4 consumes it.)
- **`pkg/binate/types/checker.bn`:** add
  `func (c @Checker) PackageMemberHome(pkgPath, name *[]readonly char) @[]char` alongside
  `PackageType` (39-45), returning `sym.HomePkg` (empty if unset/not-found). Add to
  `types.bni` interface.
- **`pkg/binate/ir/gen_util_literals.bn`:** add `buildQualNameHomed(m, prefix, suffix)` —
  resolve `prefix`→`fullPath` via `resolveImportPkg`, then `PackageMemberHome(fullPath,
  suffix)`; use the home if non-empty, else `fullPath`. (Access `m.Checker`.) Leave
  `buildQualName` untouched.
- **The 9 must-change reference sites** (route through `buildQualNameHomed`):
  `gen_util.bn:106`, `gen_selector.bn:303`, `gen_func.bn:339`, `gen_selector_type.bn:46`,
  `gen_expr.bn:444`, `gen_const_fold.bn:{69,244,330,382}`. Do NOT touch the generic-body arms
  (`gen_util.bn:87`, `gen_const_fold.bn:{60,236,320}`, `gen_expr.bn:106`).

### 4(b) The byte-identical-mangling regression test (the acceptance criterion)

- **`pkg/binate/ir/gen_util_literals_test.bn`** (extend, near `TestBuildQualName` at :113) +
  **`pkg/binate/ir/gen_generic_mangle_test.bn`** (extend): assert
  `buildQualName(...)` and `buildQualNameHomed(...)` produce **byte-identical** output for:
  1. a non-expose cross-package reference (no `HomePkg` stamped) — must equal today's
     source-spelled path;
  2. a generic-body reference (prefix = `CurrentImportAlias`) — must equal today's path;
  and produce **divergent** output **only** when a `HomePkg` is stamped (expose-injected
  member → P's home).
- **Registration/read agreement (R4 risk — must be a real LINK, not just a string compare):**
  the resolved-home name emitted at a READ site (`gen_selector.bn:303`) MUST byte-match the
  name the const/var/func was REGISTERED under **in the consuming module**. **[REVIEW]
  CORRECTION:** an earlier draft said "B's members are registered under B's path via B's own
  `gen_module`/`gen_register_import` pass" — true in **B's own** module, but the **consumer**
  that imports only the forwarder A does NOT register B's plain funcs/consts/vars (B is a
  transitive-only import; consumer registration is direct-import-scoped). That is the Phase-3.5
  gap. So the byte-match is real ONLY once Phase 3.5 registers B's surface into the consumer
  under B's home. The Phase-6 conformance forwarder test (pure forwarder, compiled + native
  modes) provides the actual link — a string compare alone would not catch a registration/read
  disagreement.
- **Const-fold value test (R4 risk — the four `gen_const_fold` arms):** a value-level
  conformance test (`[expose.C]int` array dim and/or `1 << expose.SHIFT`) — a name test is
  insufficient because a fold miss is a silent wrong value, not a link error. Put this in the
  Phase-6 bundle.

### 4(c) New files
None (extend existing IR + checker files; `buildQualNameHomed` in `gen_util_literals.bn`).

### 4(d) Verification
- `scripts/build-bnc.sh -o /tmp/bnc-p4`.
- Unit: **every package touched** — `pkg/binate/ir` AND `pkg/binate/types` (checker + scope
  changes). Per CLAUDE.md "smoke-test every package you changed."
- Conformance: the Phase-6 forwarder + var-identity + const-fold-value tests are the real
  end-to-end check that resolved-home names LINK.

### 4(e) Ordering + deliverable
Dep: 3 (the `HomePkg` stamp). Deliverable: exposed funcs/vars/consts link to P's symbol;
forwarder emits nothing; byte-identical mangling for all non-expose references (the strict
extension).

---

## Phase 5 — Expose-collision check

**Deliverable:** colliding exposes / redeclarations rejected with a clear message naming both
origins. **Deps:** 3 (its transitive-closure surface).

### 5(a) Files, functions, line ranges (recon-corrected)

Enumeration mechanism (verified): a package's exported surface = the `@Scope` registered by
path in `c.Packages` (`@[]@PkgEntry`, PkgEntry `{Path; Scope}`, types.bni:1199-1202);
`lookupPackage(c, path)` (checker.bn:68-75) returns it; `scope.Syms` is a FLAT `@[]@Symbol`
holding all six kinds. So A's own surface = `lookupPackage(c, A.Path).Syms`; each exposed B's
surface = `lookupPackage(c, B.Path).Syms`.

**WHY `Scope.Define` CANNOT self-detect (R5 — the sharper justification):** `Scope.Define`
(scope.bn:27-45) **silently OVERWRITES** a same-name symbol (loop at 38-43 replaces in place,
else appends). So Phase-3 injection would silently last-writer-wins over A's own or another
expose's same-name symbol — no error. This is a **stronger** reason than "checkDuplicateDecls
skips DECL_TYPE/DECL_GROUP": the collision pass MUST run over enumerated `Syms` name-lists, not
rely on `Define`.

**CORRECTION (R5):** design §5 / plan §5 cite `check_type_redecl.bn:32-70` as one function;
it is a 3-line wrapper `checkTypeRedeclaration` (32-34) delegating to the worker
`checkTypeRedeclFrom` (39-70). Cite the worker.

Existing patterns to model on:
- `checkDuplicateDecls` (check_decl.bn:27-53) — within-one-decl-list; skips `DECL_TYPE`
  (35,42), `DECL_GROUP` (37,43), methods (39,44); message at 47-48. **Cannot be reused.**
- **`checkImportDeclCollisions` (bni_scope.bn:471-487) — the closest cross-origin analog:**
  enumerates one origin's names via `collectDeclNames`, compares to another origin (import
  aliases), reports "`<alias>`: import alias conflicts with a declaration of the same name" at
  `imp.Pos` — a **single-Pos** message naming both origins textually. Gated to the real target
  via `checkBodies` at **`checker.bn:187-191`** (`if checkBodies { … checkImportDeclCollisions(c,
  merged) }`). **[REVIEW] CORRECTION:** the earlier `bni_scope.bn:189-191` cite was wrong — that
  is an unrelated const-group loop; `checkImportDeclCollisions` is *defined* at bni_scope.bn:471
  but *gated* at checker.bn:187-191.
- `collectDeclNames` (bni_scope.bn:493-510) — recurses groups, skips `DECL_IMPL`/methods,
  establishes which kinds share the namespace.

**DIAGNOSTIC MODEL (R5 — no two-position facility):** `addCheckError(c, pos, msg)`
(checker_errors.bn:31-40) takes ONE Pos + one message. There is **no** structured "previous
declaration here" note anywhere. So an expose collision must be a **single `addCheckError`
packing BOTH origins into the message string** (the `checkImportDeclCollisions` shape),
positioned at the offending `DECL_EXPOSE`'s `Pos`. **CORRECTION (R5):** design §5 open-question
"diagnostics should name both origins" assumes a two-Pos facility; there is none — both origins
must be textual in the one message.

**`addCheckError` dedups on (Pos, Msg)** (`appendUniqueCheckError` checker_errors.bn:52-59). So
include the colliding name + both origins in the message, or distinct collisions at a shared
Pos would dedup into one.

### 5(b) What to add/change

- **`pkg/binate/types/check_expose_collision.bn` (NEW):**
  `func checkExposeCollisions(c @Checker, path @[]char, merged @ast.File, exposedPaths
  @[]@[]char)`. Enumerate A's own exported names (via `collectDeclNames(merged.Decls)`, or A's
  registered `Syms` filtered to non-`SYM_PKG`). For each exposed B in `exposedPaths`, enumerate
  `lookupPackage(c, B.Path).Syms` by Name, **skipping `SYM_PKG`** (B's own import aliases are
  not part of B's surface — else a false positive). Detect:
  - **Case 1 (two exposes):** a Name present in two distinct exposed-B surfaces → error.
  - **Case 2 (expose vs own):** a Name present in an exposed-B surface AND in A's own exported
    name set → error.
  Emit via `addCheckError` with a single message naming both origins textually, at the
  `DECL_EXPOSE`'s `Pos`. **Span ALL kinds including `SYM_TYPE`/`SYM_INTERFACE`** (which
  `checkDuplicateDecls` skips).
- **Message helper** `exposeCollisionMsg(name, origin1, origin2)` (same NEW file), built with
  `strings.Builder` + `buf.CopyStr` (pattern of `redeclTypeMsg` check_type_redecl.bn:83-97):
  - Case 1: `"<name>: exposed by both \"<B1>\" and \"<B2>\""`.
  - Case 2: `"<name>: exposed by \"<B>\" conflicts with this package's own declaration of the
    same name"`.
  **Print FULL package paths, not short segments** (two exposed packages with a coinciding
  last segment are distinct surfaces keyed by full Path — disambiguate in the message).
- **`pkg/binate/types/checker.bn` `checkPackageImpl`:** invoke `checkExposeCollisions` once A's
  full unioned surface is known (after Phase-3 injection), at the **`checker.bn:187-191`**
  `if checkBodies { … }` block (the real gate — **[REVIEW]** not `bni_scope.bn:189-191`), so a
  decls-only dependency's own clash is reported when that package is compiled directly, not
  smeared onto every importer. **[REVIEW] Read exposes from `bni.Decls`** (the `.bni` AST is
  already a `CheckPackage` param) rather than threading a new `pkg.Exposes` param through
  `CheckPackage` and its call sites — same simplification as Phase 3 (below).
- **[REVIEW] Enumerate RAW per-exposed-B `Syms`, never A's collapsed scope.** Because
  `Scope.Define` silently overwrites same-name symbols (scope.bn:38-43), by the time Phase-3
  injection finishes, A's `@Scope` holds only the *survivor* of any collision — so the pass must
  compute collisions across each exposed B's **own** `lookupPackage(c,B).Syms` list (pre-merge)
  plus A's own decl names, not over A's post-injection `@Scope`.

### 5(c) New files
- **`pkg/binate/types/check_expose_collision.bn`** — the pass + message helpers.
- **`pkg/binate/types/check_expose_collision_test.bn`** — unit tests.

### 5(d) Tests
- **`check_expose_collision_test.bn`** (unit): case 1 (two exposes share a name) and case 2
  (expose name == own exported decl) each produce an error whose message names both origins;
  `SYM_PKG` entries do NOT false-positive; full paths appear in the message.
- **Conformance negative tests** (Phase-6 bundle): `NNN_expose_collision_two_exposes` and
  `NNN_expose_collision_vs_own_decl`, each a dir with a `.error` file whose substrings match
  the ACTUAL diagnostic text (**write the diagnostic and the `.error` file together**, or the
  negative test silently passes on the wrong error).

### 5(e) Verification
- `scripts/build-bnc.sh -o /tmp/bnc-p5`.
- Unit: `pkg/binate/types`.
- Conformance negative tests.

### 5(f) Ordering + deliverable
Dep: 3 (transitive-closure surface; and 2 for the `pkg.Exposes` edge feeding topo order).
Deliverable: colliding exposes rejected with both-origin diagnostics.

---

## Phase 6 — Reflect / descriptor + edge cases (polish + full conformance bundle)

**Deliverable:** reflect identity of exposed members is confirmed (or explicitly decided); the
full conformance bundle lands; generic-exported and `.bni`-only edge cases covered.
**Deps:** 3–5.

### 6(a) Reflect/descriptor — falls out for free (recon R5)

`Decl.Exported` (field at **ast.bni:333**; doc-comment 325-332 — the package-root
`pkg/binate/ast.bni`, NOT `ast/ast.bn`, confirmed) is set by the loader's `.bni`-merge pass
(`markBniExportedFuncs`/`markBniExportedVars`, loader_util.bn:172-235) and carried onto
`ir.Func.Exported` (gen_func.bn:60) / `ir.Global.Exported` (gen_module.bn:29,411). The reflect
descriptor is built PER-MODULE in `pkg/binate/codegen/emit_pkg_descriptor.bn` by iterating the
package's OWN lowered funcs/globals: `collectPackageFuncs` loops `m.Funcs`, skips
`!f.Exported && !f.IsStructDtor` (79-104, skip at :85); `collectPackageGlobals` loops
`m.Globals`, skips `!g.Exported` (122-136, skip at :128); `emit_funcvals.bn:145` mirrors the
func skip.

**Consequence:** a pure forwarder (A: `.bni` + expose, no `.bn`) emits NO `ir.Func`/`ir.Global`
for exposed members → those members are **ABSENT from A's descriptor entirely**; they appear
only in **B's** descriptor, described with B's names (`FunctionInfo.Name`/`GlobalInfo.Name` are
B's fully-qualified names via `mangle.QualifyName(modulePkgName, ...)` at :99/:129). This
MATCHES identity (design §3: `A.X` IS `B.X`, described as B's) and requires **NO
`emit_pkg_descriptor.bn` code change.**

- **Edit sites `collectPackageFuncs` :79-104 / `collectPackageGlobals` :122-136:** NO edit
  required for the identity outcome. **Add a conformance test** asserting reflection over A
  does NOT double-list an exposed member and that B's descriptor carries it.
- **Spec must state** that reflecting an exposed member surfaces it under B's identity (a
  consumer expecting reflection-by-name over A to return `A.X` gets nothing from A's
  descriptor — the member lives in B's). If the spec later decides A's descriptor must surface
  exposed members, that is a REAL addition (synthesize entries from A's injected `Syms`) —
  **flag as out-of-first-cut, do not do it silently.** Promote this to a **Phase-0 spec
  decision** (`pkg.expose.reflect`) rather than "free."
- **[REVIEW] Separate-compilation caveat.** "Described under B, absent from A" is a *code-path*
  fact but assumes B's descriptor is actually emitted. Under a `--pkg` build of just the
  consumer + forwarder (B a transitive-only import), verify B's object/descriptor is present in
  the link — this ties to the Phase-3.5 registration gap (a transitive-only B whose surface is
  never pulled in would also have no descriptor). Cover in the `--pkg` / separate-compilation
  path, not only whole-program.

### 6(b) Conformance bundle (all NEW dirs; layout per §"Conformance test layout")

- **`conformance/NNN_expose_forwarder/`** = `{ main.bn, expected, pkg/newlib.bni,
  pkg/newlib/newlib.bn, pkg/oldlib.bni }` — the promotion/forwarder test. `newlib` defines
  types+funcs+vars+consts+an interface; `oldlib.bni` is a **pure forwarder**
  (`expose "pkg/newlib"`, NO `oldlib/` impl dir); `main.bn` imports BOTH `pkg/oldlib` and
  `pkg/newlib` and shows old-path and new-path references hit the same entity (identity). This
  is the real end-to-end LINK check for Phase 4.
- **`conformance/NNN_expose_var_identity/`** = `{ main.bn, expected, pkg/store.bni,
  pkg/store/store.bn, pkg/fwd.bni }` — write through `fwd.V` (forwarder surface), read the
  change through `store.V` → proves shared storage, not a copy (design §3.5).
- **`conformance/NNN_expose_aggregator/`** = `{ main.bn, expected, pkg/a.bni, pkg/a/a.bn,
  pkg/b.bni, pkg/b/b.bn, pkg/agg.bni }` — `agg` exposes two internal packages; `main` imports
  only `agg` and reaches both surfaces flat as `agg.X` / `agg.Y` (design §2 flat).
- **`conformance/NNN_expose_transitive/`** = `{ main.bn, expected, pkg/q.bni, pkg/q/q.bn,
  pkg/p.bni, pkg/a.bni }` — A exposes P, P exposes Q; `main` imports A and reaches Q's members
  as `A.QMember` (design §2 transitive). Mirror `065_transitive_deps`. **This is the
  load-bearing test for the checker-visit-order finding** — a wrong visit order fails it.
- **`conformance/NNN_expose_const_fold/`** = `{ main.bn, expected, pkg/consts.bni,
  pkg/consts/consts.bn, pkg/fwd.bni }` — a **value-level** const-fold test: `[fwd.C]int` array
  dimension and/or `1 << fwd.SHIFT`. Catches a silent wrong-fold from the four
  `gen_const_fold` must-change arms (Phase 4).
- **`conformance/NNN_expose_collision_two_exposes/`** + **`.error`** — collision case 1.
- **`conformance/NNN_expose_collision_vs_own_decl/`** + **`.error`** — collision case 2.

### 6(c) New files
The seven conformance dirs above.

### 6(d) Verification
- `conformance/run.sh` in the default modes: `builder-comp`, `builder-comp-int`,
  `builder-comp-int-int`, `builder-comp-comp`, `builder-comp-comp-int`,
  `builder-comp-comp-comp` — the full self-compilation chain (positive + negative tests).
- `scripts/hygiene/run.sh` — read the OVERALL result line; confirm `conformance-test-numbers`,
  `conformance-imports`, file-length all green.
- Pick free `NNN`s at land time; re-run `conformance-test-numbers.sh` after the landing rebase.

### 6(e) Ordering + deliverable
Dep: 3–5. Deliverable: reflect identity confirmed by test; full conformance bundle green in
all default modes.

---

## Risks & open questions (design carry-forward, with recon resolutions)

- **[REVIEW] CRITICAL — consumer-side registration of the expose-closure (Phase 3.5).** The
  single biggest gap the review found: resolved-home mangling emits `pkg/P.X`, but the consumer
  of a *pure forwarder* never registers/declares `pkg/P.X` (P is a transitive-only import;
  consumer func/var/const registration is direct-import-scoped; the transitive pass covers only
  structs/interfaces/generics). Result without Phase 3.5: link failure (funcs), loud IR-gen
  abort (vars), silent wrong-fold (consts). NOW ADDED as Phase 3.5. Residual risk: the
  registration logic is **duplicated across every pipeline embedding** (bnc, VM, REPL, test
  runner) — the Phase-3.5 implementation MUST grep the `RegisterImports` /
  `registerImportVarExtern` / `RegisterFuncExterns` call sites repo-wide and cover all of them.
  Acceptance is the pure-forwarder conformance tests run in compiled + native modes (a
  name-only test cannot catch it).
- **Resolved-home mangling reach (Phase 4).** RESOLVED to a concrete 9-site table above
  (built from the repo-wide grep, not a guessed subset). Two doc mislabels corrected
  (`gen_call.bn:162`, `gen_import_const.bn:41` are LEAVE) and one **doc-missed must-change site
  added** (`gen_selector.bn:303`). Remaining risk: the generic-body divergence boundary — use
  the dedicated `buildQualNameHomed` at the 9 sites (no global gate, no invariant to prove) and
  enforce it with the byte-identical test.
- **The four `gen_const_fold` SELECTOR arms are silent-wrong-value risks.** A name test is
  insufficient; the `NNN_expose_const_fold` value-level conformance test is required.
- **Registration/read name agreement.** The read at `gen_selector.bn:303` must byte-match the
  name P registered the symbol under. The forwarder conformance test provides an actual link
  (string-compare alone would miss a disagreement). Transitive case: if A exposes B and C
  imports A, verify B's members appear in A's registered surface with B's home (else C reads
  them under A's path and the stamp never applies) — covered by `NNN_expose_transitive` +
  `NNN_expose_aggregator`.
- **Checker visit order for transitivity.** RESOLVED (see cross-cutting): checker visits in
  `ldr.Order` == topo order == P-before-A, once Phase 2 appends P to `A.Imports`. Load-bearing
  test: `NNN_expose_transitive`.
- **`.bni` var-group extern double-register.** RESOLVED: expose does NOT copy any of P's decls
  into `A.Merged` (surface-only); the loader only adds the dep edge + records `Exposes`. The
  single-homed-storage discipline lives at the checker/IR layer (inject as a reference stamped
  with P's home, never new storage in A).
- **Resolved-home field choice.** `@Symbol` ALREADY has `PkgPath` (used for `SYM_PKG`);
  recommendation is a distinct `HomePkg` (avoids overloading the `SYM_PKG`-gated field and the
  dup-import check at bni_scope.bn:355). Decision to record in Phase 0/3.
- **Injected-symbol aliasing.** The injected `@Symbol` must be a NEW object (`make(Symbol)` +
  copy), sharing only the `.Type` pointer — stamping `HomePkg` must not mutate P's original
  (mirror checker.bn:157-162).
- **Conflict-check completeness.** RESOLVED: a dedicated pass over enumerated `Syms`
  name-lists (spanning ALL kinds incl. `SYM_TYPE`/`SYM_INTERFACE`), because `Scope.Define`
  silently overwrites. Single-Pos diagnostic packing both origins textually (no two-Pos
  facility). Exclude `SYM_PKG` from the namespace.
- **Contextual vs reserved keyword.** RESOLVED to feasible either way (0 `expose` identifiers
  tree-wide); contextual recommended (no token-enum shift; no language-wide reservation).
- **Reflect/descriptor identity.** RESOLVED to "exposed member described as B's, absent from
  A's descriptor" — falls out for free, no `emit_pkg_descriptor.bn` change; spec must state it;
  conformance test guards against a future A-side synthesis regression.
- **BUILDER gating.** RESOLVED to a hard rule: keep `expose` out of every bnc-tree `.bni`
  until `BUILDER_VERSION` is bumped to a BUILDER that parses it. Recognition/impl code compiles
  under the current BUILDER; only USE in a bnc-tree `.bni` breaks gen1.
- **Hygiene: `bni_scope.bn` is 518 lines (over the 500 soft cap).** Phase 3's injection helper
  must be split into a new `bni_scope_expose.bn`, not appended inline.

---

## Recommended first slice

If Phase 0 clears, land **1 → 2 → 3 → 3.5 → 4 → 5** in order:

1. **Phase 1 (Frontend)** — contextual `expose` token recognition, `DECL_EXPOSE`,
   `parseExposeDecl` + `DeclKindName` case; parser unit tests. No deps.
2. **Phase 2 (Loader)** — `Package.Exposes` field, the unconditional expose block after the
   `:366-369` fallback appending P to `pkg.Imports` + `pkg.Exposes`; loader unit tests. Dep 1.
3. **Phase 3 (Checker/scope)** — `@Symbol.HomePkg`, full-copy injection (preserving
   `ConstVal/HasConstVal`), `injectExposedSurface` reading `DECL_EXPOSE` from the `.bni` AST
   (split into `bni_scope_expose.bn`), injection at bni_scope.bn:228; scope unit tests. Dep 2.
   (Types/impls link at the end of this phase — free from the alias identity substrate.)
3.5. **[REVIEW] Phase 3.5 (Consumer-side registration)** — register each direct import's
   transitive expose-closure funcs/consts/vars into the consumer under home names, across ALL
   pipeline embeddings (bnc/VM/REPL/test). Dep 2; land **with or before** Phase 4. MVP-critical.
4. **Phase 4 (Resolved-home mangling — the crux)** — `PackageMemberHome`, `buildQualNameHomed`,
   the 9 must-change reference sites, the byte-identical regression test. Deps 3, 3.5.
   MVP-critical.
5. **Phase 5 (Collision check)** — `check_expose_collision.bn`, gated on `checkBodies`
   (checker.bn:187-191), exposes read from `bni.Decls`, raw-per-B-`Syms` enumeration, both-origin
   single-Pos diagnostics; unit + negative conformance tests. Dep 3.

Each phase is independently landable and keeps the tree green (except 3.5, which pairs with 4 —
mangling and registration are two halves of one wire and their joint acceptance is the
pure-forwarder conformance test). Phase 6 (reflect confirmation + full conformance bundle)
follows and is largely test/spec work. The items with real subtlety are **Phase 4 + Phase 3.5**
(the two halves of the func/var/const wire); everything else reuses existing substrate (alias
identity, scope helpers, dep-graph/cycle machinery, `.bni`-only load path). The promotion
(`NNN_expose_forwarder`) + var-identity (`NNN_expose_var_identity`) + const-fold
(`NNN_expose_const_fold`) conformance tests — **each exercising a PURE forwarder in compiled +
native modes** — are the acceptance gate for the first cut.
