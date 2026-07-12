# Detailed execution plan: FFI Export (exposing Binate to C)

**Status:** edit-site-level execution plan (2026-07-11). Expands
**[plan-ffi-export.md](plan-ffi-export.md)** (the phase-level roadmap) into concrete
edit sites, tests, verification, and a landing sequence. The **design** is
**[design-ffi-export.md](design-ffi-export.md)** — still a **proposal** (not
ratified, not specified, not implemented). This plan is **contingent on Phase 0
(ratify + specify)**; nothing here is authorized to build by this document alone.

**Provenance.** Every file:line and edit site below was produced by a repo-wide
reconnaissance pass (8 parallel surveys of init/entry codegen, annotation
recognition, mangling + the `__c_call` verbatim path, object-format symbol
tables, the driver/loader, `binate_runtime.c` + shims, ABI/layout, and test
infra) against the `temp-binate-6` tree, cross-checked by direct reads of
`gen_init.bn`, `buildcfg.bn`, `compile.bn`, `main.bn`, `mangle.bn`,
`binate_runtime.c`, and the macho/elf symbol emitters. Line numbers are as of the
survey; verify before editing (the tree moves).

This plan was then **adversarially reviewed** (three lenses — codebase accuracy,
ordering/completeness, design fidelity/scope — each verifying against the tree) and
revised; then the design was **ratified (2026-07-11)** and the open decisions
settled (§5), which are folded throughout. The largest ratification consequence:
**`bn_init` is library-mode-only** (produced by a new `EmitLibInit`, not by
touching `EmitInitDispatcher`), which dissolves the review's `EmitInitDispatcher`
caller-fan-out concern entirely (that path is now never modified). The review also
fixed the MVP-harness scaffold (defer the first "C calls Binate" assertion past
`c_export`) and the §5b version-skew treatment (merge is the design's *answer* to
skew, not a detection duty).

---

## 0. Material corrections to plan-ffi-export.md (read first)

The reconnaissance found the high-level plan **substantially right on structure**
but wrong or imprecise on several load-bearing specifics. These change effort
estimates and edit locations, so they lead:

1. **`bn_init` is a mangler + new-emit-path change, not a `gen_init.bn` rename of
   the existing dispatcher.** The plan says "in `gen_init.bn`, promote the
   dispatcher to a stable `bn_init` symbol." But the existing dispatcher's IR name
   is `<root>.__init_all` with **no mangler special-case** (unlike `main.__entry` →
   `bn_entry`). The `bn_init` symbol is a **new reserved literal** (added across
   `mangle.bn:FuncName` + `mangle_lp.bn` + `mangle_lp_demangle.bn` + a `KIND_INIT`
   const in `mangle.bni`) emitted by a **new `EmitLibInit`** path — **not** by
   renaming `EmitInitDispatcher`. *(Ratified 2026-07-11:* `bn_init` is produced
   **only in library mode**, so ordinary programs and `EmitInitDispatcher`'s callers
   are untouched — see the Phase-1-folded-into-5a section.)*

2. **`bn_init` is the compiled *linker* symbol; the VM keeps the IR name.** The
   compiled linker symbol (via `mangle.FuncName`) is a **different namespace** from
   the VM's qualified IR name. `interp.bn` looks up the dispatcher by the **literal
   string `"main.__init_all"`** (interp.bn:209-211). So promote **only** the
   compiled symbol to `bn_init`; keep the IR-level name `__init_all` unchanged, and
   the VM path needs no edit. (This is an implementation detail the design leaves
   implicit — design §3.3 speaks of `bn_init`/`bn_entry` only as linker symbols;
   pin it down, don't read it as a design error.)

3. **Phase 3's native alias needs NO new object-format primitive.** The plan says
   the alias must be "a Mach-O `N_INDR` record in `asm/macho` and an ELF `.set`
   record in `asm/elf`." **False for the native backends.** The `asm.Symbol` model
   `{Name, Section, Offset, Binding}` plus `DefineLabel` (which dedups by name, not
   offset) lets you attach a **second ordinary symbol at the same section offset**;
   both writers' symbol loops (macho.bn:~415, elf.bn:~276) already emit each
   `a.Symbols[]` entry as a normal defined symbol. So the native alias is **two
   lines in each `emitFunc`** (`DefineLabel(symPrefixed(cName)); SetGlobal(...)`).
   Only the **LLVM** backend needs a genuinely net-new construct
   (`@name = alias ...`). `N_INDR`/`.set` are indirect-aliasing mechanisms (typically
   cross-TU) and are **unnecessary here** — a same-object export just needs a second
   ordinary defined symbol at the function-start offset. This shrinks Phase 3 from
   "net-new alias in three object formats" to "LLVM alias + a trivial second-symbol
   in each native emitter."

4. **The C name must route through `symPrefixed`, not verbatim.** On native
   Mach-O, `c_export("foo")` must emit `_foo` (leading underscore); on ELF it is
   bare `foo`; in LLVM IR it is `@foo` (clang adds the `_`). This is exactly the
   `__c_call` callee discipline (`callee = symPrefixed(ins.StrVal)`,
   x64_call.bn:319). Emitting the name verbatim on native Mach-O yields a symbol
   the C linker cannot find.

5. **Phase 5a's reusable template is `runTestMode` (test.bn), not
   `compileSinglePkg`.** The plan says 5a "builds on the existing `--pkg` /
   `compileSinglePkg` non-`main`-root path." But `compileSinglePkg` **emits exactly
   one object** (compile.bn:137) — it does not loop `ldr.Order`, emit dep objects,
   emit a dispatcher, or link. The real "`.o` per closure package + synthetic
   dispatcher + link/artifact" template is **`runTestMode`** (test.bn:151-213). 5a
   ≈ "test.bn's closure loop **minus** `EmitMainEntry`, **plus** a `.a`/`.so`
   archiver, **plus** a `c_export`'d `_init`." The "lighter than first feared"
   framing (plan §6) is optimistic.

6. **`--pkg` skips main-entry by *early return*, not an `IsMainPackage()`
   skip-branch.** The `IsMainPackage()` check (main.bn:228-231) is a **hard error
   that aborts** a non-`main` whole-program build, not a conditional skip. `--pkg`
   avoids it only by returning at main.bn:81. A library build must likewise take an
   early-return path; it cannot flow through the whole-program driver.

7. **No `.a`/`.so`/`ar` step exists anywhere** (grep-confirmed across `cmd/`,
   `pkg/binate/`, `scripts/`). `loader.bni:39` explicitly marks `.o/.a/.so`
   artifacts as *future*. Phase 5a's packaging is 100% net-new.

8. **Phase 6: hosted has NO Binate shim bodies to relocate.** The plan implies the
   hosted `bootstrap.*` impls exist and need porting. They **don't** —
   `impls/core/libc/pkg/bootstrap/bootstrap.bn` has only the `formatX` helpers;
   `Write`/`Args`/`Exec` resolve purely to `binate_runtime.c`. Phase 6 must **write
   new hosted Binate impls** (libc via `__c_call`), not relocate existing ones. The
   baremetal versions (semihost `Write`, empty `Args`, `-1` `Exec`) are a *shape
   template*, not portable code.

9. **Phase 6's `Exec` is used by `bnc` itself** (to run `clang`, main.bn:295, and
   `rm`, util.bn:245). A broken hosted `Exec` breaks the self-hosted build
   pipeline, not just user programs — the blast radius includes `bnc`'s operation.

10. **Line-number fix:** the plan cites the file-level `DeclIncluded` call at
    `loader.bn:209`; it is actually **loader.bn:249**. `buildconfig.bn:25/41/69`
    are correct.

11. **The C-links-Binate harness has zero precedent.** No existing test links C
    against Binate; the four "C oracle" e2e scripts (`c-global-environ`,
    `stat-values`, `readdir-values`, `errno-values`) run a C binary as a *separate
    process* compared by stdout diff. The nearest shape is `separate-compilation.sh`
    (Binate objects → `clang` → run), which is Binate-only.

---

## 1. Phase-by-phase execution

Each phase lists: **edit sites** (file — location — change), **tests**,
**verification** (mode/unit), **BUILDER** exposure, and **gating decisions**.
**MVP path = 2 → 3 → 5a** (the `bn_init` work — former "Phase 1" — folds *into*
5a; see below), verified behind the harness (§3).

> **Ratified 2026-07-11 (Phase 0 done):** the design is ratified; the decisions
> below are settled and folded into each phase. Headline consequence: **`bn_init`
> is produced *only* in library mode** (it is the library counterpart of
> `bn_entry` — "like `main`, but without the main module"), so there is **no
> standalone Phase 1** that touches ordinary programs; the `bn_init` work lands
> inside library mode (5a). Programs are 100% unchanged.

### Phase 1 (folded into 5a) — `bn_init`: the library-mode init symbol

`bn_init` is the **library** counterpart of `bn_entry`: a well-known, idempotent,
compiled linker symbol that runs the facade closure's package inits in dependency
order. It is emitted **only** in library mode — exactly **one** per artifact — and
**not** for ordinary programs (which keep `bn_entry` = `main.__init_all()` +
`main.main()`, unchanged). Because it is a **new emit path**, not a generalization
of `EmitInitDispatcher`, the existing dispatcher and all its callers are untouched
(this is what dissolves the old "update interp.bn:175 + test callers" hazard).

**Edit sites** (all exercised only on the library path — land them within 5a)
- `pkg/binate/ir/gen_init.bn` — a **new `EmitLibInit`** method (parallel to
  `EmitMainEntry`): emits the facade's dispatcher — the call loop over
  `initPkgNames` in dependency order (share a private helper with
  `EmitInitDispatcher`, or duplicate its ~10-line loop) — under a synthetic source
  name that mangles to `bn_init`, **wrapped in the run-once guard**. `EmitMainEntry`
  / `EmitInitDispatcher` and their signatures are **unchanged**.
- `pkg/binate/ir/gen_init.bn` — the **run-once guard** inside `EmitLibInit`: emit a
  module guard global (`<facade>.__init_done` via `GlobalName`), prepend `if guard {
  return }; guard = true` before the call loop (guard load + conditional
  early-return — the first multi-block synthetic init function; **no guard mechanism
  exists today**). Needed because a host may call the library's `_init` more than
  once (or several unioned libraries' `_init`s all forward to the one `bn_init`).
- `pkg/binate/mangle/mangle.bn` — `FuncName` (~L217, beside `main.__entry` →
  `bn_entry`): map `EmitLibInit`'s synthetic source name → reserved literal
  `bn_init`. Use a **library-only synthetic name** (not `main.__init_all`) so an
  ordinary program's `main.__init_all` never triggers it — that keeps `bn_init`
  strictly library-only.
- `pkg/binate/mangle/mangle_lp.bn` (~L40) — document `bn_init` as a second reserved
  literal the encoders never produce.
- `pkg/binate/mangle/mangle_lp_demangle.bn` (~L283) — add a `charsEqual(sym,
  "bn_init")` → `KIND_INIT` sentinel (mangler round-trip tests assert this).
- `pkg/binate/mangle.bni` (~L173, `KIND_ENTRY int = 7`) — add `KIND_INIT int = 8`.

**Do NOT edit** `interp.bn` / `EmitInitDispatcher` — the VM path and ordinary
programs are unaffected (correction #2).

**Tests:** `gen_init_test.bn` — assert `EmitLibInit` emits the guard global +
conditional and the `bn_init`-mangling name; `mangle_test.bn` /
`mangle_lp_demangle_test.bn` — assert the synthetic name ↔ `bn_init` round-trips
(pattern: `TestFuncNameMainEntry`).

**Verification:** ordinary programs are untouched (no re-verification needed). The
guard is net-new emission, so exercise `EmitLibInit`'s multi-block output on each
backend a library targets (LLVM + native x64/aarch64, arm32 later) + confirm
double-`_init` is idempotent via the harness (§3).

**BUILDER:** `mangle` and `ir` are in `cmd/bnc`'s BUILDER tree — BUILDER-safe
constructs only; run gen1 to confirm.

**Settled decisions:**
- **Library-only, exactly one.** `bn_init` is produced only in library mode; a
  program never emits it. (Resolves the old "any root vs main-only" question.)
- **Guard storage:** the guard global lives in the facade module (one shared guard
  across the link unit; a unioned 5b build still has one `bn_init` / one guard).
- **Init order = dependency order (resolved).** Across packages: topological
  (`ldr.Order`); within a package: `VarInitOrder` (dependency order, source-order
  fallback). No longer an open spec item.

### Phase 2 — `#[c_export("name")]` recognition + threading

Recognize the annotation (frontend) and carry the C name(s) onto `ir.Func`.
**Two edits at two different loader stages** — recognition and validation cannot
colocate (correction: `DeclIncluded` doesn't see the decl kind or `Exported`).

**Edit sites**
- `pkg/binate/buildcfg/buildcfg.bn` — `DeclIncluded` (~L16, the `if !streq(a.Name,
  "build")` reject): add a `c_export` branch that is **always-included** (never
  returns the drop `false`), validating args are `EXPR_STRING_LIT`. On success
  `continue`; do **not** fall through to `unknownAnnotationErr`. **Cannot** validate
  package-public / func-kind here.
- `pkg/binate/buildcfg.bni` — update the `DeclIncluded` doc; declare any new
  exported extractor if added.
- `pkg/binate/ir.bni` — `type Func` (~L380, beside `Exported bool`): add
  `CExportNames @[]@[]char` (a **list** — multiple entries → multiple C names).
  BUILDER: the *field type* is fine; never populate it with a `@[]@[]char{...}`
  **composite literal** (populate via `slices.Append`).
- `pkg/binate/ir/gen_func.bn` — `genFuncWithPrependedParams` (~L60, right after
  `f.Exported = d.Exported`): extract `c_export` name(s) from `d.Annotations`
  (filter `Name=="c_export"`, strip surrounding quotes from each string-lit arg's
  `.Name` — reuse the `eqLitInner` quote-strip pattern, buildcfg.bn:234), assign to
  `f.CExportNames`. This mirrors the `Exported`/`Line` copy — the established
  ast.Decl → ir.Func threading path.
- `pkg/binate/loader/loader.bn` — a **new validation pass** over `merged.Decls`
  erroring only if a decl carries `c_export` but **is not a top-level `DECL_FUNC`**
  (a type/var/import/package-clause has no code symbol to alias — a clear user
  mistake worth rejecting, else it silently no-ops). *(Ratified: **package-public is
  NOT required** — a private top-level func may be `c_export`'d, because a package
  that wraps a C library legitimately needs to hand that library a C-callable
  **callback** which is a private implementation detail, not part of its Binate
  `.bni` surface. A private func still has a compiled symbol to alias. So this pass
  does **not** check `d.Exported`.)* This runs over the `d.Kind` info; it can sit
  anywhere after the decls are merged (it no longer depends on
  `markBniExportedFuncs`, so the per-`.bni`-file-loop timing caveat is moot).

**Tests:** `buildcfg_test.bn` — `#[c_export("foo")]` recognized as always-included;
malformed args (non-string-lit / zero args) → hard error; `c_export` + a false
`build` in one block still drops (build gate wins); `#[c_export]` on an
import/package-clause is rejected (by the new pass). A `gen_func`/loader test that
`f.CExportNames` is populated.

**Verification:** unit tests (`buildcfg`, `ir`, `loader`); a conformance/e2e
smoke that a facade wrapper carrying `#[c_export]` type-checks and compiles.

**BUILDER:** `buildcfg`, `ir`, `loader` are all in `cmd/bnc`'s tree. The
recognition + field use only BUILDER-safe constructs. **Precondition:** `cmd/bnc`'s
own source must **never** use `#[c_export]` (only `platform_init` and user facades
do). Run gen1 to confirm the pinned BUILDER parses the amended `buildcfg`/`ir.bni`.

**Settled decisions (ratified 2026-07-11):**
- **Data model:** `CExportNames @[]@[]char` — a **list** (multiple entries →
  multiple C names), as the design specifies.
- **Enforcement:** the **only** hard check is "attaches to a top-level func"
  (above). **Permissive otherwise** — no legal-C-identifier check, **no uniqueness
  check**; a bad or duplicate C name surfaces at **compile (clang / native
  assembler) or link time depending on backend** (LLVM emits a duplicate-global
  clang error; the native `DefineLabel` errors on a duplicate label; a
  cross-object duplicate reaches the system linker). Same outcome — an error, not
  a silent accept — just a stage that varies. Tighten later only if it proves
  necessary.
- **Adjacent-string-concat name** (`#[c_export("a" "b")]`, `Expr.StrParts`):
  **reject** — require a single unsplit string literal (untested in `buildcfg`'s
  `is()` path; permissive on the *value*, strict on the *form*).

### Phase 3 — `c_export` symbol emission (backends)

Emit the additional unmangled C symbol aliasing the function. Native = a
same-offset second symbol; LLVM = a real alias line. The mangled `bn_` symbol
stays.

**Edit sites (native — trivial, per correction #3)**
- `pkg/binate/native/x64/x64_emit_func.bn` — `emitFunc`, right after
  `SetGlobal(sym)` (~L48), before the prologue: `for` each `f.CExportNames` name:
  `var a2 @[]char = symPrefixed(name); DefineLabel(a2); SetGlobal(a2)`. Second
  symbol at the same section offset. `symPrefixed` (x64_names.bn:30), **not**
  `symFor`.
- `pkg/binate/native/aarch64/aarch64_emit_func.bn` — `emitFunc` (~L52): identical.
- `pkg/binate/native/arm32/arm32_emit_func.bn` — `emitFunc` (~L56): identical.
  (Fourth backend — the plan under-lists it. Native arm32 is incomplete; verify
  with `builder-comp_native_arm32_baremetal`, not the LLVM arm32 mode.)

**Edit sites (LLVM — the genuinely net-new part)**
- `pkg/binate/codegen/emit.bn` — the `m.Funcs` loop (~L354): emit
  `@"<cName>" = alias i8, ptr @<mangle.FuncName(modulePkgName, f.Name)>` per name;
  **no** `_` prefix (clang adds it); skip `f.IsExtern`. First LLVM-`alias`
  emission in the tree. **Landed decision (2026-07-12):** the aliasee-type is a
  **placeholder `i8`**, *not* the function's real/coerced type. LLVM does not
  require the alias type to match the aliasee for a same-module alias, and the
  alias is purely a symbol-table entry at the function entry — the ABI lives on
  the `define`, so the C caller's declared signature governs it. Verified
  end-to-end (clang accepts it; the C call is correct) across scalar, aggregate,
  `sret`, and pointer-param returns — so there is no need to reconstruct the
  ABI-coerced/`sret` type. The alias name is emitted as a **properly-escaped**
  LLVM quoted identifier (`@"..."` with `\HH` hex-escaping of `"`, `\`, and
  non-printable bytes) so a permissive/unusual C name can't produce invalid IR.

**Not required for MVP:** ELF `STT_FUNC` (all symbols are `STT_NOTYPE` today; a
`c_export` symbol links fine as `STT_NOTYPE` — linkers resolve by name). Upgrading
needs a type field on the shared `asm.Symbol` model — a larger, optional change.
`N_INDR`/`.set` are **not** needed. `plan-macho-dysymtab.md` (dysymtab already
emitted) and `plan-linker.md` (system linker consumes aliases) are **not**
blockers.

**Tests:**
- `asm/elf/elf_test.bn` + `asm/macho/macho_x64_test.bn` — assert two symbols at one
  offset; behind the existing `canLink*()` self-skip guards, link a tiny caller
  resolving the unmangled name (`linkAndRunX64` / `assembleAndRunX64`).
- A `codegen` test that the `@name = alias ...` line is emitted with the right type.
- End-to-end C-calls-Binate belongs to the **harness (§3)** — the real Phase-3
  acceptance test.

**Verification:** the three (four) native `emitFunc` packages' unit tests (shared
across backends — smoke **each**); `codegen` unit tests; then the harness. Native
x64 fails locally on this arm64 Mac for environmental reasons (no x64 C SDK) — rely
on CI / aarch64 locally.

**BUILDER:** native backends + codegen are in the tree; edits are BUILDER-safe.

**Decisions:** use a true LLVM `alias` (not a forwarding thunk — a thunk
reintroduces the call frame Phase 4 exists to remove); the alias is strong
`SetGlobal` regardless of the func's linkage; **duplicate C names are a link-time
error** (permissive front-end, per the ratified name policy — no pre-check).

### Phase 4 — Trivial-forward → symbol alias (deferrable)

Recognize the signature-preserving forwarder shape (`func f(x) R { return g(x) }`,
incl. `_init = { bn_init() }`) and lower it to an alias of the callee's symbol,
reusing Phase 3's alias emission. **This is harder than Phase 3's export alias**
(the aliasee is a *different* symbol, possibly cross-TU — this is where `N_INDR` /
`.set` / an LLVM alias-to-another-function may genuinely be needed). The net-new
part is **IR-side shape recognition**, not object machinery.

**MVP note:** the MVP library `_init = { bn_init() }` is a **real one-call frame**
until this phase — correct and sufficient. Phase 4 is load-bearing only for the
"facade of 200 forwarders" zero-cost case. **Deps:** 3.

### Phase 5 — Library / library-union build mode

**5a (single) — the second-heaviest MVP item.** New `--library <loc>` mode.

**Edit sites**
- `cmd/bnc/args.bn` — `CLIArgs` (~L6) + `parseArgs` (~L94, near the `--pkg` arm):
  add a repeatable `--library` field (`@[]@[]char`, so 5b accumulates), plus
  `--header <name>` / output-path plumbing. **No `--init-name` flag** (design
  §3.5 — init name is the `c_export` on `_init`, source not flag).
- `cmd/bnc/main.bn` — flag dispatch (~L75, sibling to `--pkg`, **before** the
  whole-program path): `if len(cli.Library) > 0 { compileLibrary(cli); return }`
  (early return so the `IsMainPackage` hard-error at L228 is never reached).
- **`cmd/bnc/library.bn` (new file)** — `compileLibrary`, modeled on
  **`runTestMode`** (test.bn), NOT `compileSinglePkg`: load facade + closure
  (synthetic import + `LoadImports`), **`ensureRuntimeDepsLoaded`** (mandatory —
  a prior `--pkg` drift bug proves it), `typecheckPackages`, **loop `ldr.Order`
  emitting a `.o` per package** + the facade's own object, build `initPkgNames`
  across the closure, call the facade module's **`EmitLibInit`** (the folded
  "Phase 1" — emits the idempotent `bn_init` over the closure) — **but NOT
  `EmitMainEntry`** (a library has no `main`) — then **archive the `.o` set into a
  `.a`** via a net-new `ar`/`llvm-ar` invocation (`bootstrap.Exec`). ~150 lines; a
  new file (cmd/bnc files near hygiene length caps).
- `pkg/binate/ir/gen_init.bn` — `EmitLibInit` (the new method from the folded
  Phase 1) is generated **into the facade's own module** (so `m.PkgPath` is the
  facade). **`EmitInitDispatcher`'s signature is unchanged**, so no existing caller
  (`cmd/bnc/main.bn:241`, `test.bn:209`, `interp.bn:175`, the two `gen_init_test.bn`
  sites) is touched — the new path sidesteps the caller-fan-out entirely.

**5b (library union).**
- `cmd/bnc/args.bn` — the same `--library` field accumulates multiple values.
- `pkg/binate/loader/loader.bn` (+ `loader.bni`) — cross-package union: **shared
  deps load once for free** (the loader already dedups by path in `GetPackage`);
  the genuinely-new work is **disjoint-name enforcement across the facades' own
  decls**. `loader.MergeFiles` (loader_merge.bn:9) is intra-package only — **not
  reusable** (assumes one `PkgName`, would collide symbols). Likely a new union
  pass over multiple roots' closures.
- **Version skew is not a detection duty on the merge path.** Per design §3.6,
  **merge is the *answer* to** version skew, not something merge must detect: one
  build unit resolves each shared dep by path exactly once (the loader is
  first-hit-wins, no version concept), so the closure structurally contains **one**
  version of each shared dep — nothing to detect. (Skew is the hazard of the
  *rejected* separately-built + weak-dedup alternative, which the design discards.)
  So 5b does **not** need a per-package version/provenance notion for the merge
  feature. *If* the user later wants to detect skew across **separately-built**
  (non-merged) libraries, that is a distinct, design-rejected direction — surface
  it as its own Phase-0 opt-in, not a merge requirement.

**Tests:** an e2e harness (§3) building a `--library` artifact a C driver links.
Unit tests for the closure-dispatcher and disjoint-name enforcement.

**Verification:** the harness on Linux + macOS.

**Settled decisions (ratified 2026-07-11):**
- **Artifact type = `.a`** (static archive, via `ar`/`llvm-ar`). No `.so`/`.dylib`
  for now.
- **Mode is selected by the `--library` flag** — explicit, no "entry-selection"
  ambiguity. The design's "the set of wired-up entries characterizes the build"
  model (zero/multiple-entries question) belongs to `platform_init`'s pluggable
  entries, which is **deferred with Phase 6** — not an MVP concern.
- **Version skew** — not our problem (merge yields one version per shared dep;
  cross-*separately-built*-lib skew is out of scope).

**Still open (small):** whether a `Package.Exports` field is warranted, or the
driver just scans facade decls for the `c_export` on `_init`. Implementation
detail, decide when writing `compileLibrary`.

### Phase 6 — `pkg/builtins/platform_init`; retire `binate_runtime.c`

> **DEFERRED (2026-07-11) — out of current MVP scope.** Not being done now. Until
> it lands, `binate_runtime.c` stays; a C-driver-owns-`main` library consumer gets
> the `bootstrap.*` shims from the shim-only stub or (preferred) avoids them with a
> pure-compute export (§3). The edit sites below are retained for when it resumes.

**High blast radius** — changes startup *and* the I/O/exec shim linkage for every
hosted binary (incl. self-hosted `bnc`). Two halves: the entry `main()` and the
three shims.

**Edit sites**
- **`pkg/builtins/platform_init/` (new package + `.bni`)** — hosted entry:
  `#[c_export("main"), build(is(os,"linux")||is(os,"darwin"))] func _entry(argc
  int, argv **char) int` that captures `argv` into package globals `bn_argc`/`bn_argv`
  and calls `bn_entry()` (or `bn_init()` + `main.main()`). Path-special builtins
  package (pre-init treatment, like `rt`/`lang`).
- **`impls/core/libc/pkg/bootstrap/bootstrap.bn`** (~end, after `formatFloat`) —
  **new** hosted Binate bodies for `Write`/`Args`/`Exec`: `Write` → libc `write`
  via `__c_call`; `Exec` → libc `fork`/`execvp`/`waitpid`; `Args` → read
  `bn_argc`/`bn_argv` and build managed slices (replicating `managed_alloc`'s
  refcount=1 / free-fn-sentinel-0 header, else `rt.Free` mis-dispatches; preserve
  the `argv[0]`-skip: `argc-1` elements from `argv[1]`). **ABI gate:** giving these
  bodies flips them from `IsCExtern` (C ABI, `sret` for `Args`' 4-word return) to
  Binate ABI — a C `binate_runtime.c` `Args` and a Binate `Args` **cannot coexist**
  for one symbol; the flip must be atomic **per symbol** (so the shims cannot be a
  pure "land alongside" addition — only the entry `main()` can, via the distinct
  `bn_entry` symbol).
- `cmd/bnc/main.bn` (link ~L283, runtime requirement ~L101) **and** `cmd/bnc/test.bn`
  (link ~L259, ~L84) — **both** driver copies: force-include `platform_init` as the
  entry (inject as a root / `ensure*Loaded`), and during the staged flip keep
  appending `binate_runtime.c` until green, then stop and drop the hosted
  `--runtime`-required error.
- `cmd/bnc/util.bn` — add `ensurePlatformInitLoaded` (or extend
  `ensureRuntimeDepsLoaded`) so the entry package is always in the hosted link set.
- `scripts/binate-paths.sh` (~L162, the single `--runtime` source) — repoint/drop
  once the C file is gone (the choke point for every conformance/e2e caller).
- `scripts/make-bundle.sh` (~L179, `cp -R runtime`) — stop shipping
  `binate_runtime.c` in the BUILDER bundle (keep `native_test_stubs.c` — the weak
  `rt.RawFree` stub is independently needed by native unit tests).
- `cmd/bnc/target.bn` (~L52, `suppressHostRuntime` doc) — update stale comment
  (references non-existent `rt_stubs`/`libc_stubs`); reconcile the flag's meaning
  post-flip.
- Native unit tests linking `binate_runtime.c` (`x64_link_test.bn:54`,
  `aarch64_test.bn:242`) — migrate to link the new `platform_init` object (+ keep
  `native_test_stubs.c`).

**Staging:** land `platform_init` + the entry `main()` **alongside** the C file
first (distinct `bn_entry` symbol — additive); flip the shims **atomically per
symbol** (Binate body in, C definition out, same commit); remove the C file only
once the whole chain is green.

**Tests:** `e2e/print-args.sh` (argc/argv round-trip through `Args`) already
validates the entry+`Args` path — reuse as the acceptance test. New unit coverage
for the Binate `Write`/`Args`/`Exec`.

**Verification:** full conformance (every hosted mode links this) + `--test` + both
native unit-test packages + the self-compile chain (gen1/gen2 — `bnc` uses `Exec`
at build time).

**Deps:** 1, 2, 3 (`platform_init` must be compiled by a post-Phase-2 `bnc`; the
pinned BUILDER must never see `#[c_export]` in `cmd/bnc`'s own tree — it won't).

**Gating decisions:** where `bn_argc`/`bn_argv` live (exported `platform_init`
globals read by `Args`, vs. `Args` moves into `platform_init`); whether the current
`__c_call` surface can express `fork`/`execvp` cleanly, else a thin C stub in
`pkg/builtins/*` as the pragmatic first step (design explicitly allows "as C now").

### Phase 7 — Header generator

> **DEFERRED (2026-07-11) — out of current MVP scope.** Not being done now. For the
> MVP, the C consumer writes the (small) header by hand — a couple of prototypes +
> the `bn_slice`/`bn_managed_slice`/`bn_iface` typedefs. The generator is a
> quality-of-life follow-up. Edit sites retained for when it resumes.

100% new code (no header generator exists; blocked on Phase 2's `CExportNames`).

**Edit sites**
- **`pkg/binate/codegen/emit_c_header.bn` (new)** — sibling to `emit_types.bn`
  (which already Kind-dispatches `@Type` → LLVM type). Iterate exported +
  `c_export`'d `ir.Func`s; per param/result `@Type` emit a C decl; emit the
  `bn_slice`/`bn_managed_slice`/`bn_iface` + (reversed) func-value typedefs.
- `cmd/bnc/compile.bn` / `cmd/bnc/library.bn` — after `GeneratePackage` /
  `EmitModule`, write the `.h` alongside the artifact.

**Consumes (no edit):** `types/layout_offsets.bn` (field-order helpers) and
`types/layout.bn` + `abi_return.bn` (sizes / `sret`). **Must not reimplement**
these thresholds.

**Correctness nuances (from ABI recon):**
- Design §3.7 field-order table is **accurate**. But: (1) the ≤16 cutoff is **not
  one number** — the *return* side (`NeedsSret`) uses 16 on LP64 but **4 on ILP32
  arm**; a header treating "struct by-value/ref per ≤16" as one rule is wrong for
  the return direction on 32-bit. (2) At the LLVM level, func-value and iface-value
  are **both** `{i8*,i8*}` — the `{vtable,data}` vs `{data,vtable}` reversal lives
  only in the access **indices** (`layout_offsets.bn`), so the header member order
  must come from those index helpers, **not** the LLVM struct defs (else two
  identical `{void*,void*}` typedefs silently lose the reversal).
- `FieldOffset` peels only `TYP_ALIAS` (not `readonly`/named) — the emitter must
  `StripWrappers` a `readonly`/named struct param before `FieldOffset`, else 0
  offsets.
- The anonymous multi-return struct (`ir.Func.MultiReturnType`) has an **empty
  Name** — synthesize a typedef name.
- `int`/`uint` map to a target-width C type (keyed off `IntSize`), not bare `int`.

**Also (housekeeping):** spec §7.13.9's note that func/iface field order is
"encoded as fixed indices … rather than named offset helpers" is **stale** —
`layout_offsets.bn` now defines and both codegen + native backends consume the
`FuncValue*Index`/`IfaceValue*Index` helpers. Update the spec note.

### Phase 8 — Linker-placement annotation + baremetal entry

- New `#[section(".init")]` / `#[link_at(addr)]` annotation (spelling TBD) reaching
  the backend/linker (recognition rides Phase 2's annotation infra).
- `platform_init` baremetal `_start` hand-rolling `bn_init(); main.main(); halt()`
  — **not** `bn_entry` (no hosted return/`exit`). **Deps:** 2, 6. Overlaps
  `plan-arm32-bare-metal.md` / `plan-linker.md`.

### Phase 9 — Signature lint (optional)

A **bnlint** rule flagging C-unusable signatures (e.g. function-value params
needing the trampoline; optionally the managed-refcount caveat if the header emits
a machine-readable marker). Independent; not an ABI gate.

---

## 2. Cross-cutting execution concerns

- **BUILDER order.** Phase 2's recognition lands in `buildcfg`/`ir`/`loader` (all
  BUILDER-compiled). It needs no BUILDER bump (pure logic + a field), **provided**
  `cmd/bnc`'s own source never uses `#[c_export]`. Verify the pinned BUILDER parses
  the amended files via gen1 before landing (CLAUDE.md "verify, don't assume"). Phase
  6's `platform_init` (which *does* use `#[c_export]`) must be compiled by a
  post-Phase-2 `bnc` — so **Phase 6 lands after 2/3**.
- **Backend multiplication.** Every emission phase (3, 4, 8) multiplies across LLVM
  + native x64 + native aarch64 (+ arm32). The native side is in the `emitFunc`
  files (not `asm/macho`/`asm/elf` — correction #3). **Smoke every changed backend
  package** (shared-file rule), not one representative.
- **Init order** is resolved to **dependency order** (no longer gates anything).
- **`native_test_stubs.c`** survives the (deferred) `binate_runtime.c` retirement —
  don't delete it with the C file.

---

## 3. The C-links-Binate harness (the linchpin)

Gates end-to-end verification of Phases 3/5a. **Does not exist and has no
precedent** (correction #11).

**Home: `e2e/ffi-export.sh` (a new e2e script) — ratified 2026-07-11, not a
conformance mode.** Verified reasons: e2e `*.sh` is auto-discovered by
`e2e-tests.yml` as a
**Linux + macOS** matrix with clang preinstalled and **no workflow edit**; a
conformance runner's contract (`runner_exec(bn,root) → stdout` diffed vs
`.expected`) doesn't fit a C-driver test (needs a C source, a Binate artifact, an
external link, a run of the C binary); `separate-compilation.sh` already
demonstrates the exact self-contained shape to clone (build gen1, produce objects,
`clang`-link, run, diff); e2e already has the `$CC` + toolchain-**SKIP** conventions.

**Shape:** build gen1 (`scripts/build-bnc.sh`) → produce the Binate library `.a`
via `--library` → heredoc a C driver that declares + calls a `c_export`'d function
(a **hand-written** prototype — the header generator is deferred, Phase 7) → `$CC`
compile+link driver.c against the Binate artifact → run → diff stdout.
`$CC`-availability SKIP guard.

**The shim problem, and how the MVP dodges it (Phase 6 is deferred):**
The C driver owns `main()`, so the Binate `.a` must be built **without**
`binate_runtime.c`'s `main()` (its `main` would collide). But if the exported
Binate code (or anything `bn_init` runs) calls `bootstrap.Write`/`Args`/`Exec`,
those live *in* `binate_runtime.c` — which we're not linking — so they'd be
unresolved. Two ways out:
- **(preferred for MVP) a pure-compute export** — make the exported function (and
  the closure's inits) touch no I/O, and let the **C driver print** the result.
  Then the `.a` references no `bootstrap.*` shim at all (allocation still works —
  `rt` calls libc `malloc` directly via `__c_call`, no shim), so **no stub is
  needed** and the harness works with Phase 6 still deferred.
- **(fallback) a shim-only C stub** — `binate_runtime.c` **with `main()` removed**
  (just `Write`/`Args`/`Exec`), linked alongside the C driver. Only needed if an
  exported path does I/O before Phase 6 relocates the shims into Binate.
- **`.a` archiver is net-new (Phase 5a).** De-risk by linking the raw `.o`(s)
  directly first (like `separate-compilation.sh`), decoupling "C calls Binate" from
  `.a` packaging. Recommend **raw-`.o` first, `.a` follow-up**.
- The harness must reference **only** `c_export`'d unmangled names, never mangled
  `bn_*` symbols (or it becomes mangling-fragile).
- On `macos-latest` (arm64) the default target is aarch64-darwin — keep the harness
  **host-targeted** for MVP (avoids a cross-toolchain dep), which means the alias
  primitive must be green on **both** native backends for the harness to pass on
  both OSes.

**Complementary, not either/or:** `asm/elf` + `asm/macho` unit tests (behind
`canLink*()` guards) verify the alias **record** emits/links; the e2e harness
verifies the **end-to-end** C-calls-Binate contract. Keep both.

---

## 4. MVP landing sequence (independently-landable, each green)

Ratified MVP scope: **Phases 2 → 3 → 5a** (the `bn_init`/`EmitLibInit` work — the
folded "Phase 1" — lands inside 5a). Phase 4 is a later zero-cost optimization;
**Phases 6 and 7 are deferred**; 8/9 later. Each unit below keeps the tree green
and is cherry-pickable on its own.

**Status (2026-07-12):** Phases 2 **and 3 landed** on main (`e213dd42`,
`dd98dc31`), both adversarially reviewed. `#[c_export]` now recognizes, threads,
placement-checks (method silent-no-op → hard error), and **emits** the C symbol
on the LLVM path and all three native backends (x64/aarch64/arm32), with an e2e
harness (`e2e/ffi-export.sh`) that link-and-runs C-calls-Binate on **both** the
LLVM and native backends. The Phase-3 review's F2 (native link-and-run coverage)
was closed via a native arm on the e2e harness AND the plan's literal `asm/elf` +
`asm/macho` symbol-writer link tests (landed as a follow-up, `eb0cff00`;
adversarially reviewed — proven non-vacuous by a broken-writer reproduction).
**Phase 5a is in progress, staged.** Slice **5a-1 landed** (`0d332f0b`): the
`bn_init` mangler literal + `ir.Module.EmitLibInit` (the library-mode init
dispatcher, the folded "Phase 1") — reviewed, and probe-confirmed to lower to
`define void @bn_init()`. It is **not yet wired to a driver** (unused until 5a-2)
and a no-op for existing code. **Remaining:**
- **5a-2** — the `--library` driver: `--library` flag (`args.bn`) + a `main.bn`
  dispatch to a new `compileLibrary` (modeled on `runTestMode`) that loads the
  facade closure, emits per-package objects + `EmitLibInit` (the `bn_init`
  dispatcher) + a facade `_init` that calls it, and archives a `.a` (net-new
  `ar`/`llvm-ar`); plus the harness `--library` arm.
- **5a-guard** — the run-once idempotency guard for `bn_init` (a guard global +
  conditional branch, so a host may call `_init` more than once / merged
  libraries can share one `bn_init`). Deferred to its own slice by agreement
  (5a-1 emits the straight-line dispatcher); **still owed** per the ratified
  design.

1. **Harness scaffold** — `e2e/ffi-export.sh` establishing the CI lane. `c_export`
   doesn't exist yet, so there's no author-controllable Binate symbol to call and §3
   forbids referencing mangled `bn_*`. So step 1 asserts **only the plumbing** — the
   reverse direction (a C stub defines a symbol a Binate `.o` imports, links, runs).
   The first "C calls Binate" assertion arrives at step 3.
2. **Phase 2** ✅ **(landed `e213dd42`)** — `#[c_export]` recognition + threading
   (buildcfg branch + top-level-func placement check incl. method rejection +
   `ir.Func.CExportNames` list + gen_func + unit tests). Names permissive
   (link-time collision only).
3. **Phase 3** ✅ **(landed `dd98dc31`, tests follow-up `eb0cff00`)** — alias
   emission: native second-symbol (x64 + aarch64 + arm32) + LLVM `alias i8`;
   codegen + native-emitFunc unit tests; `asm/elf` + `asm/macho` symbol-writer
   link tests; e2e harness link-and-running C-calls-Binate on **both** the LLVM
   and native backends (public, private, multi-name exports).
4. **Phase 5a** — `--library` mode: `compileLibrary` (new file) + `--library` flag +
   the closure loop + **`EmitLibInit`** (the idempotent `bn_init` + the mangler
   `bn_init` literal + `KIND_INIT`) + `.a` archive (raw `.o`s first, then `ar`).
   Harness links the `.a`, calls the library's `c_export`'d `_init` (→ `bn_init`)
   then the export.

The two heaviest MVP items are the **LLVM `alias`** (Phase 3 — the only net-new
object construct) and **Phase 5a** (net-new `ar` archiver + `EmitLibInit` + the
`runTestMode`-shaped closure loop — heavier than the high-level plan's "thin
`compileSinglePkg` extension" framing). Later, as the surface matures: **4**
(zero-cost re-export), then the deferred **6** (retire `binate_runtime.c`) and
**7** (header generator), then **8/9**.

---

## 5. Decisions (ratified 2026-07-11)

The design is ratified; the decisions that gated implementation are settled:

- **`bn_init` scope** — produced **only in library mode**, exactly one per
  artifact (the library counterpart of `bn_entry`, "like `main` without the main
  module"). Programs are unchanged. (Phase 1→5a)
- **`bn_init` guard** — idempotent run-once guard, guard global in the facade
  module (one shared guard per link unit). (Phase 1→5a)
- **Init order** — **dependency order** (resolved): cross-package topological
  (`ldr.Order`), within-package `VarInitOrder`. No longer an open spec item.
- **c_export data model** — a **list** (`@[]@[]char`). (Phase 2)
- **c_export enforcement** — hard-error only "must attach to a top-level func";
  **package-public NOT required** (a package wrapping a C library needs to expose a
  private callback to it — a C-callable symbol that isn't part of the Binate `.bni`
  surface); **permissive** on names (no identifier/uniqueness check — linker handles
  collisions); reject adjacent-string-concat form. (Phase 2)
- **Artifact type** — **`.a`** (static archive, `ar`/`llvm-ar`). No `.so`/`.dylib`
  now. (Phase 5a)
- **Build-mode selection** — the explicit **`--library` flag** (no
  "entry-selection" ambiguity; the design's entry-set-characterizes-the-build model
  is deferred with Phase 6). (Phase 5)
- **Version skew** — **out of scope** (merge yields one version per shared dep;
  cross-separately-built-lib skew is not our problem). (Phase 5b)
- **Harness** — **`e2e/ffi-export.sh`**; raw-`.o` first then `.a`; **pure-compute
  export** so no shim stub is needed (§3). (Phase 0)
- **Phase 6 (retire `binate_runtime.c`) and Phase 7 (header generator)** —
  **deferred**, out of current MVP scope. The `bn_argc`/`bn_argv` home, the hosted
  shim implementation, and header typedef spelling are decided when those resume.

**Still open (small, implementation-time):** whether `compileLibrary` gets a
`Package.Exports` field or just scans facade decls for `_init`; the exact synthetic
source name that mangles to `bn_init`.
