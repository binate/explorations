# Package Assembly Files — Design Proposal

**Status: IMPLEMENTED + SPEC'D — archived (2026-09-21).** Implementation landed
on `main` (`f660064df`; see `claude-todo-done.md`), and the spec surface is folded
in — language spec §16.10 (`pkg.asmfile*`) + ABI spec §6.8 (`abi.obj.asm-symbols`),
landed docs `e5483a0` after adversarial review. §8's open questions resolved:
`.global_c` spelling kept; gate anywhere in the leading comment block; dialect
pinned as the implementation's own (bnas dialect); other file kinds out of scope.

This is a **separate concern** from inline assembly (`#[asm]` on a Binate function,
tracked elsewhere): this doc is about shipping a **whole `.s` file** alongside a
package so its symbols are defined wherever the package is compiled/linked. The two
compose (both are gated by `#[build(...)]`) but are independent features.

---

## 1. Motivation

An arch whose runtime primitive is hand-written assembly — today only aarch64's
`rt.MemZero`, a wide-store (STP) fill that the scalar Binate body can't match — has
that assembly assembled + linked **only inside `cmd/bnc`'s link paths**, via a
per-symbol special case (`assembleRtMemObj` / `rtMemAsmForArch` in
`cmd/bnc/rt_mem_asm.bn`, called from 5 sites: `main.bn`, `library.bn`, `test.bn`,
`bnld_link.bn` ×2). The Binate `rt.MemZero` body is `#[build(!is(arch,
"aarch64"))]`-gated off, so the symbol is *defined* only by that injected object.

Consequences of the special-casing:

- **`bnc -c` output is not self-contained.** Compile-to-objects (no link) emits
  objects that *reference* `…MemZero` but never *define* it, because the definition
  is injected only at link time. Anything that links a `bnc -c` aarch64 object set
  with an external linker (the `bnld-real-program` e2e) hits `undefined symbol:
  …MemZero`. The e2e was stopgapped by defining the symbol in its own link-only shim
  (`7989641b1`); this design is the proper fix.
- **The compiler's link paths carry per-symbol knowledge** — the exact mangled name
  `bn_F3_3_pkg8_builtins2_rt1_7_MemZero`, the arch, and the platform symbol prefix
  are all baked into `cmd/bnc`. Adding a second hand-written primitive would mean a
  second special case.

The fix: let a **package** carry its own `.s` files, gated per-target, assembled and
included wherever the package is compiled — **including plain `bnc -c`**. This makes
every object set self-contained and retires the special-casing with no per-symbol
knowledge in the compiler.

---

## 2. Proposed surface (for the spec)

### 2.1 A package may include assembly files

A package's implementation directory (`<ImplPath>/<pkg>/`) may contain `.s` files
alongside its `.bn` files. Each `.s` file is written in **Binate's own assembler
dialect** (the `pkg/binate/asm` text syntax accepted by the embedded assembler
`pkg/binate/asm/assemble`, the same dialect `bnas` accepts and the bare-metal
`crt0.s`/`semihost.s` use) — **not** the host platform's `as` syntax. It is
assembled in-process by the embedded assembler for the target arch/object-format.

An included `.s` file's symbols are defined wherever the package participates in a
build: a whole-program link, a `--library` archive, a test binary, and — the point
of this proposal — a plain `bnc -c` object set.

### 2.2 File-level `#[build(...)]` gating

An assembly file may carry a **file-level** build constraint as a leading
comment directive:

```
// #[build(is(arch, "aarch64"))]
.arch aarch64
.section text
.global_c bn_F3_3_pkg8_builtins2_rt1_7_MemZero
bn_F3_3_pkg8_builtins2_rt1_7_MemZero:
  ...
  ret
```

- The constraint uses the **exact same grammar and vocabulary** as a `.bn`/`.bni`
  package-clause `#[build(...)]` (`is`/`!is`, `&&`/`||`/`!`, keys `arch`/`os`/
  `entrypoint`/`version`; evaluated by `pkg/binate/buildcfg`). There is nothing
  assembly-specific about the constraint language.
- Gating is **file-level only** (this proposal deliberately does not add in-file
  conditionals — see §4). A whole `.s` file is either included for the active target
  or dropped.
- The directive lives in the file's **leading comment block** (before the first
  instruction/directive). The assembler already treats `//` as a line comment
  (`asm/parse/lex.bn`), so the gate is invisible at assembly time — no assembler
  change is needed to *carry* the gate.
- **At most one** `#[build(...)]` gate per file; a second is an error.
- A file with **no** `#[build(...)]` gate is included on **all** targets (an ungated
  `.s` means "portable / every target").
- A **malformed or unknown** constraint is a hard error that aborts the load (never a
  silent skip) — identical to `.bn` gating.

### 2.3 The `.global_c` directive (platform C-symbol prefix)

**Problem.** The Mach-O ABI prefixes C-level symbols with `_` (`foo` → `_foo`); ELF
does not. The Binate compiler already emits `_bn_…` on Mach-O and `bn_…` on ELF for
the *same* function. A hand-written `.s` that **defines** a Binate function symbol
must therefore match: `_bn_…MemZero` on aarch64-macOS, `bn_…MemZero` on
aarch64-Linux. A single static label can only spell one of them.

**Rejected: always prepend `_` on Mach-O.** This is a backwards-incompatible change
to global assembler behavior: existing Mach-O `.s` symbols are already written with
their final names (e.g. bnld's `_main`, `_start`), and there are legitimate symbols
that must *not* be prefixed. So the prefix must be **opt-in per symbol**.

**Chosen: a new opt-in directive `.global_c <name>`.** It declares `<name>` a global
symbol that carries the **platform C-symbol prefix**: emitted verbatim as `<name>` on
ELF, and as `_<name>` on Mach-O. The in-file label stays unprefixed
(`<name>:`); only the *emitted* object symbol name is prefixed, so both the definition
and any cross-object reference (relocations name symbols by table index) resolve to
the platform-correct name. One `.s` file then serves both object formats.

Existing `.global` / `.weak` / `.local` are unchanged, so every current `.s` file is
unaffected.

(`.global_c` is spelled to parallel the existing `.global`; final spelling is a spec
author's call — see §8.)

---

## 3. Implementation plan

All touched packages (`loader`, `buildcfg`, `parser`, `asm`, `asm/parse`,
`asm/macho`, `asm/elf`, `asm/assemble`, `cmd/bnc`) are in the BUILDER-compiled
surface, so every change must stay BUILDER-compilable. Nothing here needs a new
language feature; the `.s`-gate parsing reuses the existing annotation parser.

### Stage A — assembler: the `.global_c` directive

1. `pkg/binate/asm.bni`: add `CPrefix bool` to the `Symbol` struct (default false).
2. `pkg/binate/asm/asm.bn`: `SetGlobalC(name)` — like `SetGlobal`, but also sets the
   symbol's `CPrefix = true` (creating an undefined entry if needed). `addSymbol`
   initializes `CPrefix = false`.
3. `pkg/binate/asm/parse/parse.bn`: recognize `.global_c` in `parseDirective`,
   routing to a `SetGlobalC` call (mirrors the `.global` arm).
4. `pkg/binate/asm/macho/macho.bn`: at the string-table build (the single
   `strTabAdd(strtab, a.Symbols[i].Name)` site), emit `_`-prefixed when
   `Symbols[i].CPrefix`. This is the only place the emitted symbol name is
   materialized — the nlist uses `strOffsets[oldIdx]`, and relocations resolve by
   table index, so both defs and references get the prefix. The `L`-temporary drop
   reads the unprefixed `.Name`, so it is unaffected.
5. `pkg/binate/asm/elf/elf.bn`: no change (ELF never prefixes).
6. Tests: assemble a tiny `.s` with `.global_c foo`; assert the ELF object exports
   `foo` and the Mach-O object exports `_foo` (byte/symtab-level checks in
   `asm/macho` + `asm/elf`, mirroring existing writer tests).

### Stage B — loader: pick up gated `.s` files onto `Package`

1. `pkg/binate/loader.bni`: add `AsmFiles @[]@AsmFile` to `Package`, where `AsmFile
   { Path @[]char; Src @[]uint8 }` (path for diagnostics + object naming; source for
   in-process assembly).
2. `pkg/binate/parser`: a small public `ParseLeadingAnnotations(src, filename)
   (@[]@ast.Annotation, @[]ParseError)` that runs the existing `parseAnnotationBlock`
   and returns — no new parsing logic, just an entry point.
3. `pkg/binate/loader/loader_load.bn`: in the impl-dir enumeration loop, for a
   `.s` entry: read it via the `SourceProvider`; scan the leading comment block for a
   single `// … #[build(…)]` line; strip the `//` and parse the `#[…]` via
   `ParseLeadingAnnotations`; evaluate with `buildcfg.DeclIncluded(BuildConfig, …)`.
   Keep → append an `AsmFile` to the package; drop → skip; malformed/unknown → append
   to `l.Errors` and abort (as `.bn` gating does). When `BuildConfig == nil` (REPL,
   bni, unit tests — none of which assemble), keep the file ungated, matching `.bn`
   behavior; the file is simply never consumed.
4. Tests: a loader test that a gated-in `.s` lands on `Package.AsmFiles` for a
   matching target and is dropped for a non-matching one; a malformed-constraint
   test that aborts the load.

### Stage C — cmd/bnc: assemble each package's `.s` into the object set

1. One shared helper in `cmd/bnc` (e.g. `assemblePkgAsmObjs(pkg, buildDir, arch,
   objFmt) @[]@[]char`) that assembles each of a package's kept `AsmFiles` into a
   `.o` (via `asm/assemble.AssembleFile`, object name namespaced like the existing
   `bnrt_*` objects to avoid `--build-dir` collisions) and returns the object paths.
2. Call it in **every** per-package compile path — `main.bn`, `library.bn`,
   `test.bn`, AND `compile.bn`'s `compileSinglePkg` (the `--pkg` single-package
   path) — appending the results right after `compileModuleVia`. The first three
   append to `oFiles` (which `bnc -c` prints as deliverables and both bnld paths
   receive); `--pkg` prints each assembled object alongside the module object as
   its own deliverable. Passing the same `outPrefix` the compiled object uses puts
   the asm object beside it (build-dir, or cwd) in every mode. **Do not miss
   `--pkg`**: its output must be as self-contained as a whole-program dependency
   object, else a separately-compiled aarch64 package silently lacks `MemZero`
   (this bit — the `e2e/separate-compilation.sh` link fails on Apple-Silicon CI).
3. **Delete** `cmd/bnc/rt_mem_asm.bn` and all 5 `assembleRtMemObj` call sites (the
   two `bnld_link.bn` sites' separate rt-object injection included — the rt object now
   rides in via `oFiles`).

### Stage D — move `rt.MemZero` asm into a gated package `.s`

1. Create `impls/core/common/pkg/builtins/rt/memzero_aarch64.s`: the STP/byte-tail
   body currently produced by `rtMemAsmAarch64`, with a `// #[build(is(arch,
   "aarch64"))]` gate and `.global_c bn_F3_3_pkg8_builtins2_rt1_7_MemZero` (one file,
   both object formats via `.global_c`). Keep the `L`-prefixed local labels.
2. The Binate `rt.MemZero` body (`rt_memzero.bn`) stays `#[build(!is(arch,
   "aarch64"))]`-gated as-is; the two continue to partition arch space.
3. Revert the `bnld-real-program` e2e stopgap shim (`7989641b1`) now that `bnc -c`
   aarch64 output defines the symbol itself.

---

## 4. Why not in-file conditionals

An alternative to `.global_c` is an assembler preprocessor conditional
(`.if is(os,"macos") … .endif`) so one file can spell both symbol lines. Rejected:
it is a **second gating system inside the file** (we already have file-level
`#[build]`), it needs new machinery (conditional parsing, nesting, a target-predicate
vocabulary duplicated in the assembler), and it is far heavier than the one thing
actually needed here — the platform symbol prefix. `.global_c` handles that one thing
opt-in and composes with the file-level gate. If a real need for arbitrary in-file
target conditionals appears later, it can be designed then.

## 5. Fallback (no assembler change)

If review rejects `.global_c`, the zero-assembler-change fallback is **two os-gated
`.s` files** — `memzero_aarch64_elf.s` (`.global bn_…`, gated `is(arch,"aarch64") &&
!is(os,"macos")`) and `memzero_aarch64_macho.s` (`.global _bn_…`, gated
`is(arch,"aarch64") && is(os,"macos")`) — identical bodies, differing only in the
symbol line. Stages B–D are unchanged; only Stage A is dropped. The cost is ~25 lines
of duplicated body per dual-format primitive.

## 6. BUILDER safety

Every touched package is BUILDER-compiled. The changes are: a struct field
(`Symbol.CPrefix`, `Package.AsmFiles`, `AsmFile`), a new directive arm + setter, a
Mach-O emit-name tweak, a loader enumeration branch, and a parser entry point — all
plain existing-language code. No new-to-BUILDER syntax is introduced, so gen1 is
unaffected. (Verify against the pinned BUILDER before landing, per the usual rule.)

## 7. Testing

- **asm** (Stage A): `.global_c` → `_`-prefixed Mach-O symbol, unprefixed ELF symbol.
- **loader** (Stage B): gated `.s` kept/dropped by target; malformed constraint aborts.
- **conformance / e2e** (Stages C–D): the `bnld-real-program` e2e (un-stopgapped) must
  link a `bnc -c` aarch64 object set with no `undefined …MemZero`; the aarch64 native
  conformance modes (`builder-comp_native_aa64-comp_native_aa64`) must stay green
  (the rt.MemZero symbol still resolves through the new package `.s`). Smoke both
  aarch64 object formats: linux (ELF, `bnld` + clang) and macOS (Mach-O).

## 8. Open questions for the spec author

- **Directive spelling.** `.global_c` vs `.globl_c` vs a general `.c_symbol <name>`
  (orthogonal to binding). Whether a `.weak_c` counterpart is wanted (none needed
  today).
- **Gate placement rule.** Require the gate on line 1, or anywhere in the leading
  comment block? (This proposal: anywhere in the leading comment block, first match
  wins, before the first instruction/directive.)
- **`.s` dialect.** Confirm the spec pins package `.s` files to Binate's assembler
  dialect (not platform `as`), consistent with `bnas` and the bare-metal runtime `.s`.
- **Other file kinds.** This is scoped to `.s`; whether pre-built `.o`/`.a` artifacts
  in a package dir are ever in scope is out of scope here (the `loader.bni` "Future:
  also `.o`/`.a`/`.so` artifacts" note).
