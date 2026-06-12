# Per-File Build Constraints — Design Proposals

**Status: DESIGN / proposals.** Nothing here is decided; this doc lays out the
design space, two concrete syntactic proposals, and the implementation shape,
with tradeoffs, so the user can choose. It is the concrete follow-up to the
`claude-todo.md` entry "Per-file build constraints — conditional file
inclusion/exclusion by target — DESIGN". Anchors below were verified against
the tree (binate `main`, 2026-06) by an adversarial pass; where a mechanism
does *not* exist yet it is called out as *proposed*, not asserted.

---

## 1. Problem & goals

Let a single source file (`.bn`/`.bni`) opt **itself** in or out of compilation
based on the active build configuration — arch, target triple, OS,
libc-vs-freestanding, backend (LLVM / native-aa64 / native-x64), engine
(`bnc`-compiled vs `bni`-interpreted), and possibly user-defined tags.

Goal shape: `arch == "arm32" && !libc` written near the top of a file, so that
a package can hold a shared core plus a few per-variant files **in one
directory**, instead of duplicating whole directory trees per platform.

Non-goals (this doc): a general expression evaluator for arbitrary const
folding; per-*declaration* (intra-file) conditionalization; the `.xfail`
test-mode replacement (a sibling idea, deliberately out of scope here).

---

## 2. What exists today (grounding)

### 2.1 Three selection axes, all directory-coarse

Variant selection today is **whole-directory**, governed by
`pkg-layout-spec.md` Invariant 5 ("Whole-package selection only … needs the
symlink workaround until per-file selection is designed"). There are actually
**three** stacked axes in `impls/`:

1. **tier** — `core/` vs `stdlib/`
2. **platform variant** — `common/` / `libc/` / `baremetal/`
3. **per-triple** — `impls/targets/<key>/` (e.g. `x86_64-darwin`,
   `aarch64-linux`, `arm32-linux`), keyed by the same `--target KEY` string

The actual cross-variant/-triple duplication in the tree right now:

| Package | Duplicated as | Copies |
|---|---|---|
| `pkg/std/os/internal/internal.bn` | one tree per triple under `impls/targets/<key>/` | **5** |
| `pkg/bootstrap/bootstrap.bn` | `impls/core/{libc,baremetal}/` | 2 |
| `pkg/builtins/rt/rt.bn` | `impls/core/{libc,baremetal}/` | 2 |
| `pkg/std/os/os.bn` | `impls/stdlib/{libc,baremetal}/` | 2 |

`impls/core/common/` holds the genuinely platform-independent packages
(`pkg/builtins/lang`, `pkg/builtins/testing`). The `os/internal` ×5 case is the
most acute: five near-duplicate trees that differ only by triple — exactly what
a per-file `triple == "..."` constraint would collapse into one package.

`find impls -type l` returns **nothing**: the symlink workaround Invariant 5
sanctions is documented but has never actually been used. So retiring it as the
sanctioned mechanism breaks nothing.

### 2.2 The annotation grammar is already reserved, and namespacing is DECIDED

This is the single most important grounding fact, and it tilts the syntax
choice. `grammar.ebnf:305-314` already reserves a first-class annotation
syntax, marked `[DEFERRED]`:

```ebnf
(* [DEFERRED] — entire annotation system deferred from bootstrap.
   Annotations attach to the immediately following element.
   Multiple annotations are comma-separated within one block.
   No stacking of separate #[...] blocks. *)
Annotation     = "#" "[" AnnotationList "]" ;
AnnotationList = AnnotationEntry { "," AnnotationEntry } ;
AnnotationEntry = AnnotationName [ "(" AnnotationArgs ")" ] ;
AnnotationName = identifier { "." identifier } ;   (* packed, compiler.inline, tool.export *)
AnnotationArgs = Expression { "," Expression } ;
```

And the **namespacing model is DECIDED** (`claude-notes.md:804-822`):

- **Unqualified** = language-standard. The compiler/interpreter *enforces* these
  are known/valid — **catches typos**.
- `compiler.*` = compiler-specific; unknown namespaces are **ignored**.
- `tool.*` = external tools; the compiler **ignores** them.

The parser has **no** path consuming `#[` yet (`token.HASH` exists,
`lexer.bn:432-433`; no `parseAnnotation`). So the grammar and the policy exist;
only the parser implementation is missing.

### 2.3 Comments are discarded — not in the AST

The lexer drops all comments in `skipWhitespace()` with no token emitted
(`pkg/binate/lexer/scan.bn:5-31`, `skipLineComment` at `:34-46`);
`ast.File` has no comment field (`ast.bni`); `parser.ParseFile()` goes straight
to `expect(PACKAGE)` (`parser.bn:128-140`). Consequence: a `//`-comment pragma
is **invisible to the parser, AST, and therefore to bnlint** — every tool that
wants to see it must re-scan raw text. A first-class annotation, by contrast,
survives as real tokens.

### 2.4 The loader file-enumeration hook

`loadPackage` (`pkg/binate/loader/loader.bn`, enumeration loop ~`:196-226`)
lists a package dir via `bootstrap.ReadDir`, sorts alphabetically, then per
entry: checks the `.bn` suffix, applies the `_test.bn`/`TestPackages` filter
(both `continue` on exclusion), forms `filePath`, calls `readFileBytes`, then
`parser.New(...).ParseFile()`. A parse error appends to `l.Errors` and aborts.
The natural per-file gate sits **right beside the `_test.bn` filter, before
`readFileBytes`/`ParseFile`** — a third `continue`-style filter. The
`_test.bn`/`TestPackages` pair is an exact precedent: a per-file inclusion gate
driven by a field on the `Loader` struct.

Files that are gated out are simply never appended to the survivors slice, so
they never reach `MergeFiles` or `Package.Merged` — **no merge-logic change is
needed**. The `Loader` struct (`loader.bni`) has **no** build-config field
today (`Root, BniPath, ImplPath, Packages, Order, Errors, TestPackages`).

### 2.5 Target metadata is already source-visible — but scattered in the compiler

The one place target metadata is exposed *into Binate source* is the
`pkg/builtins/build` package, which has **no implementation — the constants ARE
the package**, one copy per target under `ifaces/targets/<key>/`, selected by
`binate-paths --target KEY` prepending that dir to `-I`. It exposes exactly:

```binate
const OS      OSType   = OS_DARWIN          // OS_LINUX | OS_DARWIN | OS_BAREMETAL
const Arch    ArchType = ARCH_X64           // ARCH_X64 | ARCH_ARM64 | ARCH_ARM32
const PtrSize int      = 8
const IntSize int      = 8
```

Inside the compiler, target knowledge is split and **not** centralized:
`cmd/bnc/target.bn`'s `applyTarget` hardcodes the triple keys
(`host`, `x86_64-linux`, `x86_64-darwin`, `aarch64-linux`, `arm32-linux`,
`arm32-baremetal`); `nativeArchForTarget` hardcodes arch strings; the layout
layer (`types.TargetInfo`) carries only `PointerSize`/`IntSize`/`MaxAlign`;
`suppressHostRuntime` is the only libc-ish flag. `applyTarget` runs once in
`main()` *before* loading/type-checking, so the full config is frozen and
knowable by the time the loader enumerates files. There is **no** centralized
predicate registry — this feature should introduce one.

---

## 3. The shared substrate: predicate model & expression semantics

Both syntax candidates (§4) parse to the **same applicability AST** and run
through the **same evaluator**. This substrate is independent of the syntax
choice.

### 3.1 Predicate vocabulary

Mirror the existing source-visible model (`pkg/builtins/build`) so there are
not two competing notions of "the target":

| Predicate | Type | Domain (closed) | Backed by |
|---|---|---|---|
| `arch` | enum | `x64`, `arm64`, `arm32` | `build.Arch` / triple→arch |
| `os` | enum | `linux`, `darwin`, `baremetal` | `build.OS` |
| `triple` | enum | the `applyTarget` keys | `--target KEY` |
| `libc` | bool flag | present / absent | derived from `suppressHostRuntime` (see §3.5) |
| `backend` | enum | `llvm`, `native_aa64`, `native_x64` | `--backend` + `nativeArchForTarget` |
| `engine` | enum | `bnc`, `bni` | the calling tool (see §3.5) |
| `ptrsize` | int | `4`, `8` | `build.PtrSize` |
| `tag.<name>` | bool flag | open, default false | `--tag <name>` (proposed) |

The built-in names form a **closed, typo-checked set**; `tag.<name>` is the one
**open** namespace (unknown = false is legitimate there, and *only* there). The
`tag.` prefix is what lets the evaluator distinguish "deliberately-open user
tag" from "misspelled built-in" — and it directly mirrors the already-decided
annotation namespacing (unqualified = enforced; namespaced = open).

### 3.2 Expression grammar — full boolean expression, not a Go tag-list

**Recommendation: a full boolean expression with typed comparisons.** A strict,
side-effect-free, name-resolution-free subset:

```ebnf
BuildExpr  = OrExpr ;
OrExpr     = AndExpr { "||" AndExpr } ;
AndExpr    = UnaryExpr { "&&" UnaryExpr } ;
UnaryExpr  = [ "!" ] Primary ;
Primary    = Comparison | Flag | "(" BuildExpr ")" ;
Comparison = Predicate ( "==" | "!=" ) String ;
Flag       = Predicate ;                       (* bool predicate or tag.<name> *)
Predicate  = identifier { "." identifier } ;
```

```
arch == "arm32" && libc
os == "baremetal" || os == "linux"
!(backend == "llvm") && ptrsize == 4
engine == "bnc" && backend == "native_aa64"
tag.debug && os == "linux"
```

Why full-expression beats a Go `//go:build`-style flat tag-list:

1. **The config is multi-valued, not boolean soup.** `arch`/`os`/`triple` are
   3/3/N-valued enums. A tag-list forces each enum value into its own boolean
   tag (`arch_arm32`, `os_baremetal`, …), reintroducing the typo footgun the
   closed vocabulary exists to kill, and making `!=` ("any arch but x64")
   clumsy.
2. **`!=` and grouped negation are first-class needs** ("everything except
   baremetal" = `os != "baremetal"`).
3. **Consistency.** The reserved `AnnotationArgs = Expression` production
   (§2.2) already says annotation arguments are expressions. A
   `#[build(arch == "arm32" && libc)]` whose argument is a (restricted) real
   expression is uniform with `#[align(4)]`, and with the future
   target-qualified `#[link("m")]` family (`claude-todo.md` C-interop entry),
   which will want the *same* predicate expression. One predicate grammar,
   reused — versus a bespoke comma-tag micro-syntax island.

Cost is bounded: a ~5-production recursive-descent walk over tokens the lexer
already emits (`==`, `!=`, `&&`, `||`, `!`, `(`, `)`, identifier, string).

### 3.3 Evaluation timing & single authority

Evaluated **in the loader, during file enumeration** (§2.4), once per build —
the config is frozen by then. Introduce **one** build-config descriptor
(proposed: a `BuildCfg` field on the `Loader` struct, populated by `applyTarget`
for bnc and by each other front-end for itself), holding the resolved value of
every predicate plus the user-tag set. The evaluator queries only this
descriptor; it becomes the single source of "what predicates exist and what
they currently are," and the place bnlint/hygiene read from too (they have
*zero* build-config context today). Adding a predicate = one descriptor field +
one evaluator case + a domain entry.

### 3.4 Error semantics — unknown/malformed is a HARD ERROR (the safety property)

This is load-bearing. Filtering a file out changes which decls reach
`Package.Merged`; a **silently-false** predicate ⇒ silent file drop ⇒ the
dropped file's symbols vanish ⇒ a *different* file fails later with "undefined
symbol"/link error far from the cause. That is precisely the silent-file-drop
class this project treats as a critical footgun.

Rules:

1. **Unknown built-in predicate ⇒ hard error.** `achr == "arm32"` (typo) is
   *not* silently false; it is `build constraint: unknown predicate "achr"`.
   The closed vocabulary makes this checkable, mirroring the
   already-decided "unqualified annotations are enforced/typo-checked."
2. **Unknown enum value ⇒ hard error** (`arch == "armv7"` →
   `not a valid arch (expected x64|arm64|arm32)`).
3. **Malformed expression ⇒ hard error** (unbalanced parens, dangling `&&`,
   `==` against a bare flag, …). The evaluator never "recovers" to a default.
4. **`tag.<name>` is the only thing that may be false-because-absent** — and
   only because its namespace is explicitly open. A bare unknown word is a
   typo'd built-in (rule 1), never an implicit tag.
5. **Errors abort the build, not the file.** They route through the loader's
   existing error channel (`append to l.Errors` then `return`), exactly like a
   syntax error in the file. They must **not** take the skip (`continue`) path —
   skipping *is* the silent-drop failure mode.

The asymmetry is the whole point: **evaluating to false skips the file
(intended); failing to evaluate aborts the build (safety).**

### 3.5 Two honest caveats (flagged, not silently resolved)

- **`libc` is not first-class today.** No `libc`/`Hosted` constant exists in
  any `build.bni`; freestanding is currently *implied* by `OS_BAREMETAL`. That
  conflation is wrong in general (`arm32-linux` and host both have libc; a
  future hosted-no-libc target breaks it). Proposal: treat `libc` as a distinct
  flag sourced from the same authority as `suppressHostRuntime`, and —
  **separately, user's call** — add a matching `Hosted`/`Libc` constant to
  `pkg/builtins/build` so source-level `import "pkg/builtins/build"` and
  build-constraints agree. Flagged rather than silently derived from `os`.
- **`engine` (bnc vs bni) is not loader-knowable today.** Both bnc and bni use
  identical `NewLoader`/`LoadImports`; the loader is engine-agnostic. An
  `engine` predicate therefore requires each front-end to *inject* its identity
  into `BuildCfg` — real plumbing, not a loader-internal lookup. Until that
  lands, an `engine`-mentioning constraint must **hard-error** ("predicate not
  available in this build configuration"), never silently false (same invariant
  as §3.4). Whether `engine` is in v1 scope is an open decision (§10).

---

## 4. Syntax candidates

### 4.1 Candidate A — comment-form pragma (`//bn:build …`)

```
//bn:build arch == "arm32" && libc

package "pkg/builtins/atomic"
```

Go's `//go:build` model. The pragma is an ordinary `//` comment to the lexer
(so it leaves no token/AST trace); recognition is a **textual prefix scan** done
by each tool. Placement: the contiguous leading comment block before `package`,
blank-line-terminated; multiple lines AND-combine; a `//bn:build` after
`package` is just a comment and **must not** be honored (no spooky line-400
file-drop).

Scan strategy (no parse): a few lines of byte-level text processing over the
file prefix — does **not** use the lexer at all (the lexer discards exactly the
bytes we care about). In the loader it runs over `src` between `readFileBytes`
and `parser.New`, `continue`ing on a false result.

Honest tradeoffs:
- **+ Cheapest to scan** — pure byte-prefix loop; trivially reusable by the
  non-parsing hygiene shell scripts (their one genuine ergonomic win).
- **+ Fully backward compatible** — lexer/parser/AST untouched.
- **− Out-of-grammar / "magical."** Invisible to parser and AST. Binate
  explicitly values transparent, source-determined, non-magical surfaces and
  *already reserves* a first-class `#[…]` facility meant to unify
  build-constraints, lint-control, and C-interop. A comment pragma is a
  parallel, special-cased channel — exactly the magic the language avoids.
- **− Every tool re-implements it.** loader, bnlint (via loader), each hygiene
  script, any future formatter/IDE must each re-scan and re-parse the
  expression. The annotation form is parsed once into the AST.
- **− Silent-typo footgun.** `//bn:buildd`, `// bn:build` (leading space) →
  silently a plain comment, file unconditionally included, no diagnostic.
  Inherent to comment-channel directives; needs a compensating hygiene
  near-miss check.
- **− Second expression dialect.** Being scanned (not parsed), the condition
  either hand-rolls a second parser for Binate's `Expression` or invents a tiny
  ad-hoc grammar — a divergent dialect either way.

### 4.2 Candidate B — first-class `#[build(...)]` annotation (recommended)

```binate
#[build(arch == "arm32" && libc)] package "pkg/foo"
#[build(!libc)] package "pkg/foo"                    // freestanding-only
#[build(triple == "x86_64-darwin")] package "pkg/foo"
```

Instantiate the **already-reserved** annotation facility (§2.2), attaching an
optional annotation to the package clause — mirroring how `ImportDecl` already
carries an optional `[ Annotation ]`:

```ebnf
PackageClause = [ Annotation ] "package" string_literal ;
```

`build` is a standard (unqualified) annotation name → compiler-enforced /
typo-checked, which is exactly the §3.4 hard-error policy *for free* from the
decided namespacing. A future combined form is one block:
`#[build(libc), link("m")] package "pkg/foo"` (comma-separated, never stacked).

**Parse-before-decide** (the one real wrinkle): the loader must read the
annotation *before* committing to a full parse. Because `#[` lexes as real
tokens (`token.HASH`/`token.LBRACKET`), a *proposed* `prescanBuildConstraint`
runs the **real lexer** over the in-memory `src`, pulls the bounded token run
`HASH LBRACKET … RBRACKET PACKAGE`, slices out `build(...)`'s argument tokens by
paren-balancing, and hands them to the §3 evaluator; on false, `continue`. No
divergent scanner: the *same* tokens then feed the full parser if the file is
accepted. (Contrast A, which needs a separate comment-aware text scan that the
real parser can never share.)

When accepted, preserve the annotation on the AST so later tooling needn't
re-scan: add a general `ast.Annotation` node (`Name`, `Args @[]@ast.Expr`,
`Pos`) — the reusable node for the whole deferred facility — and an
`ast.File.BuildConstraint` field; in `ParseFile`, before `expect(PACKAGE)`,
consume a leading annotation block.

Honest tradeoffs:
- **+ First-class / parseable / non-magical** — visible syntax, survives as
  tokens, one scan feeds both the gate and the full parse.
- **+ Reusable** — directly instantiates the reserved general facility; build,
  `#[link(...)]`, and lint-control share one surface and one AST node, instead
  of a parallel pragma channel.
- **− More work than a comment** — needs the `ast.Annotation` node, a
  `parseAnnotation` path, the `ast.File` field. The facility has *zero* parser
  implementation today (but the grammar + policy are already designed).
- **− The parse-before-decide prescan** must paren-balance the `build` argument
  without the full expression grammar — modest, bounded.

### 4.3 Comparison & recommendation

| | A: `//bn:build` comment | B: `#[build(...)]` annotation |
|---|---|---|
| Scan cost | lowest (byte loop) | low (bounded real-lexer run) |
| In AST / parser-visible | no | yes |
| Reuses reserved facility | no (parallel channel) | **yes** |
| Tools re-implement scan | each one | parser once; tools read AST |
| Typo of the directive name | silent → file included | hard error (enforced name) |
| Expression dialect | second, hand-rolled | the real `Expression` |
| Hygiene-shell ergonomics | **best** (plain grep) | needs a head-scan helper |
| Implementation cost | lower now | higher now, lower long-run |

**Recommendation: Candidate B.** The decisive factors are not effort but
*coherence*: the `#[…]` grammar and its namespacing (incl. the typo-enforcement
that gives §3.4 for free) are already decided; comments are deliberately not in
the AST, so A forces every tool into a parallel text-scanning channel and a
second expression dialect — the exact "magic" the language design rejects. B
makes build-constraints the **first concrete instance** of the general
annotation facility the project already intends to build, shared with
`#[link(...)]` and `tool.lint` (§7). A's only real edge is hygiene-shell
ergonomics, addressed by one shared head-scan helper either way.

(If the user prefers to defer the parser work, a viable middle path is to ship
the §3 substrate + loader gate now behind A's cheap scan, then migrate the
*surface* to B when the annotation parser lands — the evaluator and `BuildCfg`
are unchanged. Called out so the choice is eyes-open, not baked in.)

---

## 5. Loader / merge integration

- **Where the gate runs.** A third filter in the enumeration loop, beside the
  `_test.bn` filter, before `readFileBytes`/`parser.New` — so an inapplicable
  file with constructs this toolchain can't handle never parses and never
  contributes a spurious `ParseError`. This is *why* the gate must precede
  parse, not merely precede merge.
- **Prefix-read vs reuse-the-buffer (sub-decision).** Option (i): `open` + read
  ~512–1KB before `readFileBytes`, saving the full read of rejected files, but
  forcing a second I/O path and a partial lexer that could disagree with the
  real one (e.g. a constraint split across the read boundary). Option (ii): gate
  *after* `readFileBytes`, running the bounded real-lexer scan over the
  in-memory `src`. **Recommend (ii)** — build-constrained files are small and
  few; the saved read isn't worth a divergent scanner.
- **Drop semantics.** A gated-out file is `continue`d, never appended to the
  survivors, so `MergeFiles`/`Package.Merged` need **no change**; sorted merge
  order among survivors is preserved.
- **Empty-package handling.** The existing `package "<path>" not found` guard
  (`bniFile == nil && len(files) == 0`) already gives the right answer in both
  directions, with no change: (a) `.bni` present + all `.bn` gated out → guard
  doesn't fire, package registers interface-only (already supported for extern
  packages) — correct; (b) no `.bni` + all `.bn` gated out → guard fires —
  **also correct**, a package with no interface and no applicable impl is
  genuinely unimportable on that target, and silent acceptance would hide
  misconfiguration. So **keep** the guard; do **not** add a separate
  ">=1 surviving file" rule.
- **Gate the `.bni` too.** The interface file is discovered on a separate path;
  apply the *same* `fileAppliesToTarget` there so the two trees can't diverge
  on "applies." Build-constrained `.bni`s should be rare (Invariant 1:
  `ifaces/` is implementation-independent), but the gate must be uniform. Keep
  the evaluator **pure** (no state between files) since `.bni` is evaluated
  before the impl loop.
- **Target threading.** Add `BuildCfg` to the `Loader` struct, populated by
  `applyTarget` for bnc (sourced from the `pkg/builtins/build` metadata so names
  line up with the runtime constants) and by each other front-end for itself
  (the `engine` plumbing, §3.5).
- **BUILDER constraint.** The loader is inside `cmd/bnc`'s
  BUILDER-compilable tree, so the scanner + evaluator added there **must stay
  within the BUILDER-accepted subset** (no interfaces/generics/closures — see
  CLAUDE.md "Builder Compatibility Constraint"). This rules out a fancy
  expression-object hierarchy; a flat token-walk evaluator fits.

---

## 6. Relationship to the `impls/` trees + migration

**Recommendation: COMPLEMENT, don't replace — and retire the symlink workaround
immediately.**

The directory axes (`common`/`libc`/`baremetal`, and `targets/<key>/`) do real,
legible work: `binate-paths` selects a whole sub-tree with one `-L`/`-I`
prepend, and "what's in the bare-metal build" stays answerable by `ls`, not by
grepping file headers. Collapsing everything into one flat tree where every file
self-selects would (a) force the loader to read+scan *every* file to find the
few that apply, and (b) make the platform boundary invisible in the filesystem —
contrary to the transparent/source-determined value. So: **coarse axis stays
directories; fine axis becomes per-file constraints.**

What per-file constraints buy:

- **Eliminate the symlink workaround** (never actually used) as Invariant 5's
  escape hatch; the spec text "needs the symlink workaround until per-file
  selection is designed" should be re-pointed at this facility.
- **Collapse within-package and per-triple duplication.** The `os/internal` ×5
  per-triple trees (§2.1) become one package whose files carry
  `triple == "..."` constraints. `pkg/bootstrap`, `pkg/builtins/rt`,
  `pkg/std/os` (each duplicated across `libc`/`baremetal`) become one
  directory: shared declarations unconstrained, the libc-only / baremetal-only
  files carrying `libc` / `!libc` constraints.

Migration is **opt-in, incremental, no flag day** (the loader change is a no-op
for unannotated files):

1. **Land the gate as a no-op** — add `BuildCfg`, add `fileAppliesToTarget`
   returning `true` when no constraint is present. Byte-identical behavior; the
   tree split keeps working; everything stays green. Safe first commit.
2. **Thread `BuildCfg`** from the front-ends (bnc in `applyTarget`; bni driver
   and bnlint set at least `arch`/`os`/`engine`).
3. **Pilot one package** — start with the smallest duplicate (`pkg/builtins/rt`
   or the `os/internal` per-triple set). Move the variant files into one dir,
   split shared vs platform-specific, tag the platform-specific ones, delete the
   now-empty variant copies, adjust `binate-paths` so the package resolves under
   the collapsed dir.
4. **Verify** against the existing target matrix (`builder-comp_arm32_baremetal`,
   `builder-comp_arm32_linux`, host modes) — a mis-gated file shows up
   instantly as a missing-symbol/not-found error.
5. **Repeat per package**, deciding case-by-case whether full-tree separation or
   constraint-collapse reads better. Platform-independent packages need no
   change.

---

## 7. Tooling: bnlint, hygiene, and the lint-exempt corollary

Two independent file-discovery paths must both become config-aware:

- **bnlint discovers files *through the loader*** (`lintPackages` →
  `NewLoader` → `LoadImports`), then walks the parsed/merged AST (`LintFile`).
  It has **no `--target`/`--config` flag** today. Add one, thread it into
  `NewLoader.BuildCfg`; then bnlint inherits the §5 filtering for free —
  inapplicable files are simply **absent** from `Package.Merged`, so they're
  neither type-checked nor linted → no false flags. (This is the whole reason
  the gate lives in the loader, not in bnlint.)
- **Hygiene scripts use raw `find` + text scanning, never parse.** Split them:
  - **Config-blind checks stay blind.** Trailing-whitespace, import-order,
    **line-length** — a file's constraint doesn't excuse bad formatting. Do
    **not** wire `--target` into these. (Explicitly narrower than "add
    `--target` everywhere.")
  - **Config-relative checks get config-awareness** — chiefly the global
    unique-`conformance` numbering and any future "every package has an impl for
    target X." A *single* shared shell helper (`bn-applicable-for`, sourced once,
    not re-implemented per script — avoids the sweep-incompleteness trap) reads a
    file's constraint head and filters the `find` *result*.

**Linting each file under all its applicable configs.** One bnlint run pins one
config, so a file constrained to `arch=="arm32" && !libc` is skipped (clean, but
unchecked) under a host run. To actually check it, bnlint runs **once per config
in a cover set**. The precedent is already in `lint.sh`'s `LINT_SKIP`
(whole-package skips for what the BUILDER bnlint can't typecheck); the matrix
generalizes it from "skip everywhere" to "lint under the configs where this
applies." Cover-set definition + CI cost (|cover set| × |packages|) is an open
decision (§10).

**The lint-exempt corollary (unified vocabulary).** The TODO's idea — a
"lint-exempt this file/region" directive sharing the *same* annotation
vocabulary — falls out cleanly from the decided `tool.*` namespace (compiler
ignores it):

- **File-level:** `#[tool.lint(off("raw-slice-return"))]` on the package clause
  — same attachment point and same prescan path as `#[build(…)]`; bnlint's
  head-scan extracts it in the same pass and consults a suppression set before
  emitting a diagnostic.
- **Region-level:** once the parser carries annotations (Phase 4), a
  `#[tool.lint(off(...))]` attached to a declaration; pre-parser, a recognized
  line-range comment the linter scans for.
- **Hygiene whitelists migrate onto it over time.** Today's three `.whitelist`
  files (`naming`, `test-coverage`, `conformance-imports`) and the existing
  `// LONG-LINE ALLOWED` trailing marker in `line-length.sh` are the legacy
  forms. Long-term, in-source `tool.lint`/`tool.hygiene` annotations replace
  external whitelists (the exemption lives next to the code it excuses and
  survives file moves — consistent with the "comments stand alone / no
  breadcrumbs" discipline). Coexist until then; unified vocabulary is the
  target, not a day-one migration.

The unifying rule: **one head-scan per file yields both `#[build(…)]` and
`#[tool.lint(…)]`**, comma-separated in one block.

---

## 8. Phased implementation roadmap

| Phase | Scope | Complexity |
|---|---|---|
| **0 — Substrate** | Predicate `BuildCfg` descriptor + the §3 expression evaluator (flat token-walk, BUILDER-subset) + hard-error vocab. No tool wired up; unit-tested standalone. | **Low–Med** |
| **1 — Loader/bnc gate (MVP)** | `BuildCfg` on `Loader`; `fileAppliesToTarget` gate beside the `_test.bn` filter; populate from `applyTarget`/`--backend`/`build.bni`. Per-file IN/OUT in bnc; symlink workaround retired. Needs merge-resolution tests (a dropped decl breaking a dependent). Stays BUILDER-compilable. | **Med** |
| **2 — bnlint config awareness** | `--target`/`--config` → `parseArgs`/`CLIArgs` → `NewLoader`; bnlint inherits Phase-1 filtering. Add file-level `tool.lint(off …)` suppression. | **Low–Med** |
| **3 — Hygiene config matrix** | `--target` on `run.sh`; `bn-applicable-for` helper; wire **only** config-relative checks; generalize `LINT_SKIP` into a config cover-set. Enumerate `find … *.bn` sites repo-wide (sweep rule), not a guessed subset. | **Med** |
| **4 — First-class annotation parsing** | Implement the reserved `#[…]` in the parser (real `ast.Annotation`), so `#[build(…)]`/`#[tool.lint(…)]` are AST nodes; enables region-level exempt and folds the prescan into normal parse for non-package uses; migrate `.whitelist` files. Coordinate with the C-interop `#[link(…)]` family so the surface is designed once. | **High** |

MVP = **Phases 0→2** (per-file compilation + a config-aware linter that never
false-flags), reusing the `--target` vocabulary and `build.bni` constants that
already exist. Note: under syntax Candidate **A**, Phase 4 is unnecessary for
build-constraints (but the general annotation facility is still wanted for
C-interop, so it happens anyway); under **B**, Phase 4 (or at least a minimal
package-clause-annotation parser) is what makes B first-class rather than a
prescan-only hack — it can be pulled earlier and merged with Phase 1.

---

## 9. Recommendation summary

1. **Syntax: Candidate B** (`#[build(EXPR)]`) — first concrete use of the
   already-reserved, namespacing-decided annotation facility; non-magical;
   shared with `#[link]` and `tool.lint`. (A is the fallback if parser work is
   deferred; the substrate is identical.)
2. **Expression: full boolean** over a **closed, typo-checked** built-in
   vocabulary + an open `tag.*` namespace.
3. **Errors: hard-fail** on unknown/malformed (evaluate-false skips; fail-to-
   evaluate aborts).
4. **Loader: gate beside `_test.bn`**, before parse; reuse the in-memory buffer;
   keep the empty-package guard; gate `.bni` too; thread a `BuildCfg`
   descriptor; stay BUILDER-compilable.
5. **impls/: complement**, not replace; retire the (unused) symlink workaround;
   collapse the `os/internal` ×5 and the libc/baremetal duplicates incrementally.
6. **Tooling: config-aware bnlint via the loader**; config-blind formatting
   checks stay blind; unify lint-exempt under `tool.lint`.

---

## 10. Open decisions for the user

1. **Syntax: B (annotation) or A (comment), or A-now-migrate-to-B?**
2. **Expression form: full boolean (recommended) or a simpler tag-list?**
3. **`libc` first-class?** Add a `Hosted`/`Libc` constant to
   `pkg/builtins/build` (so source and constraints share one authority), or
   keep deriving from `os`/`suppressHostRuntime`?
4. **`engine` predicate in v1?** It needs front-end plumbing; until then,
   `engine`-constraints hard-error. In or out for the MVP?
5. **`ptrsize` predicate at all?** Fully determined by `arch` — include for
   parity with `build.PtrSize`, or drop to keep the vocabulary minimal?
6. **`tag.*` CLI surface** (`--tag <name>`) and which tools accept it.
7. **Are build-constrained `.bni`s allowed** (vs `.bn`-only), given Invariant 1?
8. **Per-config lint cover-set:** where declared (checked-in matrix vs derived
   from `ifaces/targets/*`), and the acceptable CI cost.
