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
revised accordingly. Notable corrections from that pass: the `EmitInitDispatcher`
caller list in Phase 5a (the real third caller is `interp.bn:175`, not
`cmd/bni/main.bn`); the MVP-harness step-2 scaffold (deferred its first "C calls
Binate" assertion to Phase 3, since no author-controllable symbol exists earlier);
and the §5b version-skew treatment (merge is the design's *answer* to skew, not a
detection duty).

---

## 0. Material corrections to plan-ffi-export.md (read first)

The reconnaissance found the high-level plan **substantially right on structure**
but wrong or imprecise on several load-bearing specifics. These change effort
estimates and edit locations, so they lead:

1. **Phase 1 lands mostly in `pkg/binate/mangle`, not `gen_init.bn`.** The plan
   says "in `gen_init.bn`, promote the dispatcher to a stable `bn_init` symbol."
   But the dispatcher's IR name is `<root>.__init_all`, and there is **no mangler
   special-case for it** today (unlike `main.__entry` → `bn_entry`). The stable
   `bn_init` symbol is created by **adding a mangler special-case** (mirroring
   `bn_entry`) across four files: `mangle.bn:FuncName`, `mangle_lp.bn` (reserved
   literal), `mangle_lp_demangle.bn` (sentinel), and a new `KIND_INIT` const in
   `mangle.bni`. `gen_init.bn` keeps emitting `__init_all`; the mangler renames it.

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
MVP path = **1 → 2 → 3 → 5a**, verified behind the harness (§3).

### Phase 1 — `bn_init`: a stable, idempotent well-known symbol

Promote the init dispatcher to a compiled linker symbol `bn_init`, mirroring
`bn_entry`, and make it run-once. `bn_entry` stays `bn_init()` + `main.main()`.

**Edit sites**
- `pkg/binate/mangle/mangle.bn` — `FuncName` (~L217, beside the `main.__entry` →
  `bn_entry` case): add `<root>.__init_all` → reserved literal `bn_init`.
  **Decision:** gate on `pkg=="main"` (like `bn_entry`) or accept **any** root?
  The library case (5a) needs a non-`main` root, so this likely must **not** be
  `main`-gated — see gating below.
- `pkg/binate/mangle/mangle_lp.bn` (~L40) — document `bn_init` as a second reserved
  literal the encoders never produce.
- `pkg/binate/mangle/mangle_lp_demangle.bn` (~L283) — add a `charsEqual(sym,
  "bn_init")` → `KIND_INIT` sentinel (mangler round-trip tests assert this).
- `pkg/binate/mangle.bni` (~L173, `KIND_ENTRY int = 7`) — add `KIND_INIT int = 8`.
- `pkg/binate/ir/gen_init.bn` — `EmitInitDispatcher` (~L228): add the **run-once
  guard**. Emit a module guard global (`<root>.__init_done` via `GlobalName`),
  prepend `if guard { return }; guard = true` before the call loop. This turns a
  currently straight-line single-block function into multi-block (guard load +
  conditional early-return). **No guard mechanism exists anywhere today** — fully
  net-new.
- `pkg/binate/ir/gen_init.bn` — `EmitMainEntry` (~L268): the internal
  `<pkg>.__init_all` call target is unchanged at the IR level (keep `__init_all`);
  only its *mangled* form becomes `bn_init`. Keep the `main.main` literal.

**Do NOT edit** `interp.bn` — it keys the dispatcher by IR name `"main.__init_all"`
(correction #2). Confirm no VM path needs a `bn_init`-named lookup.

**Tests:** `gen_init_test.bn` — assert the guard global + conditional emit;
`mangle_test.bn` / `mangle_lp_demangle_test.bn` — assert `<root>.__init_all` ↔
`bn_init` round-trips (pattern: `TestFuncNameMainEntry`).

**Verification:** the symbol rename is a behavioral no-op for existing programs
(same init sequence), but the **guard is net-new emission** — the first module
guard-global + conditional early-return inside a synthetic init function, on a
function every backend currently treats as a trivial straight-line skeleton (cf.
arm32_emit_func.bn:86). So do **not** assert "no-op → all six green"; run an
explicit per-target checklist: unit-test the multi-block emission in `ir`, then
confirm each of LLVM + native x64 + native aarch64 + native arm32 (via
`builder-comp_native_arm32_baremetal`, **not** the LLVM arm32 mode) + the VM
(`int` modes) executes the guarded `__init_all` correctly. The guard's *effect* is
observable only if `bn_init` is called twice (a library case, Phase 5a).

**BUILDER:** `mangle` and `ir` are both in `cmd/bnc`'s BUILDER tree — the edits use
only existing constructs (const, `if`, string compare), BUILDER-safe; run gen1 to
confirm.

**Gating decisions (Phase 0):**
- **Is `bn_init` produced for any root or only `main`?** Design says it runs over
  "the build root's transitive deps," implying non-`main`-gated. Decide before
  landing — the mangler case's gate is the crux.
- **Guard storage:** in-dispatcher guard global (design's lean) vs. an `rt` symbol.
  In-dispatcher avoids coupling `ir`→`rt`; but for merged libraries (5b) the guard
  must be a single shared global across the link unit (the facade module is the
  right home; §3.6-merge resolves the separately-built case).
- **Init-order spec item.** `bn_init` becomes public ABI; its *within-package*
  ordering (declaration- vs dependency-order, `buildInitBody` /
  `VarInitOrder`) becomes part of that contract. Note this is handled in
  `generatePackageInit`, **separate** from the dispatcher being promoted — so
  Phase 1's symbol+guard change does not itself touch within-package order.
  Resolve the spec item before shipping `bn_init` as ABI regardless.

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
  erroring if a decl carries `c_export` but (a) is not `DECL_FUNC`, or (b)
  `!d.Exported` (not package-public). `d.Exported`/`d.Kind` become known only after
  `markBniExportedFuncs`/`Vars` (L383-384) — **but those calls sit inside the
  per-`.bni`-file loop** (which continues merging `.bni` imports through ~L403 and
  closes at ~L404). So the pass must run **after that loop closes**, over the
  fully-marked `merged.Decls` — not immediately after the L384 call (which would
  fire once per bni file, before marking is final).

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

**Gating decisions:**
- **Enforcement site** for package-public/func-kind (new loader pass — recommended
  — vs. IR-gen). Loader errors are more ergonomic than IR-gen errors.
- **Data model:** single `@[]char` (MVP, one alias) vs. `@[]@[]char` (design's
  multi-name). Design allows multiple; recommend the list from the start (a
  single-name field silently drops a second export).
- **Name validation:** legal-C-identifier / uniqueness. Design defers uniqueness to
  merge (§3.6); a duplicate C name is otherwise only caught at link time (native
  `DefineLabel` duplicate-label error, or LLVM duplicate-alias). Decide whether
  Phase 2 pre-rejects.
- **Adjacent-string-concat name** (`#[c_export("a" "b")]`, `Expr.StrParts`): accept
  + concat, or reject? Recommend **reject** (require a single unsplit literal) —
  `buildcfg`'s existing `is()` path never exercises `StrParts`, so it is untested.

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
- `pkg/binate/codegen/emit.bn` — the `m.Funcs` loop (~L354) or after each body in
  `emit_debug.bn:emitFuncDbg`: emit `@<cName> = alias <retTy>, ptr
  @<mangle.FuncName(modulePkgName, f.Name)>` per name; **no** `_` prefix (clang
  adds it); skip `f.IsExtern`. First LLVM-`alias` emission in the tree — pin the
  syntax (opaque-ptr `alias ptr, ptr @f` vs typed) against the clang the toolchain
  shells to, and get the aliasee/coerced return type right (aggregate-returning
  exports use the coerced/sret type).

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

**Gating decisions:** LLVM alias vs. forwarding thunk (thunk reintroduces a call
frame — the thing Phase 4 exists to remove; prefer the alias); weak-vs-global for
the alias (a C export is strong `SetGlobal` regardless of the func's linkage);
duplicate-C-name policy (Phase 2 validator vs. link-time error).

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
  across the closure, emit the `bn_init` dispatcher (Phase 1) rooted at the facade —
  **but NOT `EmitMainEntry`** — then **archive the `.o` set into a `.a`/`.so`** via
  a net-new `ar`/`libtool`/`clang -shared` invocation (`bootstrap.Exec`). ~150
  lines; a new file (cmd/bnc files near hygiene length caps).
- `pkg/binate/ir/gen_init.bn` — the Phase-1 dispatcher generalization actually
  *lands here* (rooted at the facade, non-`main`). **If** `EmitInitDispatcher`'s
  signature changes (a root-package param) rather than just generating into the
  facade's own module, that commit must update **every** caller in one landing step
  or it won't compile: `cmd/bnc/main.bn:241`, `cmd/bnc/test.bn:209`,
  **`pkg/binate/interp/interp.bn:175`**, the `ir.bni` decl (~L1178), and the two
  unit-test call sites `gen_init_test.bn:143` / `:158`. (Note: `cmd/bni/main.bn` is
  **not** a caller — it loops `vmInst.CallFunc(initPkgNames[i], ...)` directly, so
  it needs no edit here.) Generating into the facade module without a signature
  change avoids all of this — prefer it if feasible.

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

**Gating decisions:** entry-selection rule (zero/multiple wired-up entries — genuinely
unimplemented, no "entry function" notion in the driver today; Phase 0 must decide
require-exactly-one vs. allow-combinations); artifact type (`.a` via `ar`/`llvm-ar`
vs `.so`/`.dylib` via `clang -shared` — affects platform matrix); whether a
`Package.Exports` field is warranted (the driver must otherwise scan facade decls
for the `c_export` annotation to find `_init`).

### Phase 6 — `pkg/builtins/platform_init`; retire `binate_runtime.c`

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
- **Init-order spec item** gates Phase 1 shipping `bn_init` as public ABI (see
  Phase 1 gating).
- **`native_test_stubs.c`** survives the `binate_runtime.c` retirement — don't
  delete it with the C file.

---

## 3. The C-links-Binate harness (the linchpin — a Phase-0 deliverable)

Gates end-to-end verification of Phases 3/5a/6/7. **Does not exist and has no
precedent** (correction #11).

**Recommended home: `e2e/ffi-export.sh` (a new e2e script), not a conformance
mode.** Verified reasons: e2e `*.sh` is auto-discovered by `e2e-tests.yml` as a
**Linux + macOS** matrix with clang preinstalled and **no workflow edit**; a
conformance runner's contract (`runner_exec(bn,root) → stdout` diffed vs
`.expected`) doesn't fit a C-driver test (needs a C source, a Binate artifact, an
external link, a run of the C binary); `separate-compilation.sh` already
demonstrates the exact self-contained shape to clone (build gen1, produce objects,
`clang`-link, run, diff); e2e already has the `$CC` + toolchain-**SKIP** conventions.

**Shape:** build gen1 (`scripts/build-bnc.sh`) → produce the Binate library object(s)
via `--library` → heredoc a C driver that `#include`s the generated header (Phase 7)
and calls a `c_export`'d function → `$CC` compile+link driver.c against the Binate
artifact → run → diff stdout. `$CC`-availability SKIP guard.

**Ordering dependencies the plan understates for the harness:**
- The C driver owns `main()`, so the Binate library must be built **without**
  `binate_runtime.c`'s `main()` **but with** the `bootstrap.*` shims — else the
  Binate code strands `Write`/`Args`/`Exec` at link. So the harness needs a shim
  home: it **implicitly depends on Phase 6's shim relocation** OR must link a
  shim-only C stub. **De-risk:** an MVP harness can link a minimal shim-only C stub
  (extracted from `binate_runtime.c`) so Phase-3 C-calls-Binate is verifiable
  **before** Phase 6.
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

The MVP boundary (1+2+3+5a first; 4/6/7/8/9 as "enhancements") is the **plan's
proposed sequencing** — the design has no phasing notion — and is held under the
Phase-0 "build this now, in what scope?" decision, not asserted as settled. Given
that scope call, each unit below keeps the tree green and is cherry-pickable on its
own.

1. **Phase 1** — `bn_init` symbol + guard (mangler + `gen_init` + unit tests).
   Behavioral no-op for programs; unblocks the library dispatcher.
2. **Harness scaffold (MVP form)** — `e2e/ffi-export.sh` linking a raw `.o` + a
   shim-only C stub, establishing the CI lane early (plan's "named early
   deliverable"). At this step `c_export` does not exist yet, so a Binate function
   has **no author-controllable linker name** — its only symbol is the mangled
   `bn_F…`, which §3 forbids the harness from referencing. So step 2 asserts **only
   the plumbing**: the reverse direction (a C stub defines a symbol the Binate `.o`
   imports) links and runs. The **first "C calls Binate" assertion is deferred to
   step 4** (once Phase 3 emits a real `c_export`'d unmangled name to call).
3. **Phase 2** — `#[c_export]` recognition + threading (buildcfg + loader pass +
   `ir.Func.CExportNames` + gen_func + unit tests). Verify gen1/BUILDER.
4. **Phase 3** — alias emission: native second-symbol (x64/aarch64/arm32) + LLVM
   `alias`; asm + codegen unit tests; **wire the harness to call a real `c_export`'d
   function** (the true Phase-3 acceptance test).
5. **Phase 5a** — `--library` mode: `compileLibrary` (new file) + `--library` flag +
   closure dispatcher + artifact packaging (raw `.o`s first, then `.a`). Harness
   links the artifact.

Then, as the surface matures: **4** (zero-cost re-export), **6** (retire
`binate_runtime.c` — high blast radius, stage carefully, lets the harness drop its
shim stub), **7** (header — makes the harness `#include` a tested artifact), **8/9**.

The two heaviest single items remain the **LLVM alias** (Phase 3, the only net-new
object construct) and **retiring `binate_runtime.c` incl. its I/O shims** (Phase 6,
high blast radius). Phase 5a is heavier than the high-level plan implied (net-new
archiver + the `runTestMode`-shaped closure loop, not a thin `compileSinglePkg`
extension).

---

## 5. Open decisions that gate implementation (Phase 0 asks)

Collected for the ratification round; each blocks the noted phase.

- **`bn_init` root gating** — any root or `main`-only? (Phase 1/5a)
- **`bn_init` guard storage** — in-dispatcher global vs. `rt`; single-shared for
  merged libraries. (Phase 1/5b)
- **Init-order spec item** — declaration- vs dependency-order becomes `bn_init`
  ABI. (Phase 1)
- **c_export data model** — single name vs. list; enforcement site for
  package-public; name-legality/uniqueness policy; adjacent-concat handling.
  (Phase 2)
- **Entry-selection rule** — zero/multiple wired-up entries; exactly-one vs.
  combinations. (Phase 5)
- **Artifact type** — `.a` (ar/llvm-ar) vs `.so`/`.dylib` (clang -shared); platform
  matrix. (Phase 5a)
- **Version-skew** — merge already resolves it (one version per shared dep in the
  closure). Only question: does the user want skew detection across *separately-built*
  (non-merged) libraries — a distinct, design-rejected direction — or not? (Phase 5b)
- **`bn_argc`/`bn_argv` home** and whether hosted shims are Binate-`__c_call` or a
  transitional C stub. (Phase 6)
- **Harness home + MVP scope** — `e2e/ffi-export.sh`, raw-`.o` first, shim-stub vs.
  wait-for-Phase-6. (Phase 0)
- **Header typedef naming/spelling** and the ILP32 return-threshold representation.
  (Phase 7)
