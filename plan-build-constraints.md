# Build Constraints — Design Proposals

**Status: DESIGN / proposals.** Lays out the design space, the chosen syntax,
and the implementation shape for conditional compilation in Binate, with
tradeoffs, so the user can ratify. Concrete follow-up to the `claude-todo.md`
entry "Per-file build constraints — conditional file inclusion/exclusion by
target — DESIGN" — but generalized, per the user's direction, from *per-file* to
**per-declaration**. Anchors verified against the tree (binate `main`, 2026-06);
mechanisms that don't exist yet are marked *proposed*.

Inline assembly (`#[asm]`) is a **separate concern with its own doc**; it
*composes with* the build-constraint substrate here (an asm function variant is
selected by a `#[build(...)]` constraint) but is not designed in this doc.

---

## 1. Problem & goals

Let any **top-level declaration** — `const`, `type`, `var`, `func`, the
`package` clause, and `import` — opt **itself** in or out of compilation based on
the active build configuration: arch, target triple, OS (+ OS version),
libc-vs-freestanding, backend (LLVM / native-aa64 / native-x64), engine
(`bnc` vs `bni`), pointer/int width, language/compiler version, and (later)
user-defined tags. Spelled as a `#[build(EXPR)]` annotation on the element:

```binate
#[build(is(arch, "arm32"))] func barrier() { /* … */ }
#[build(!is(arch, "arm32"))] func barrier() { /* … */ }

#[build(is(os, "linux"))] import "pkg/std/os/linux_internal"

#[build(ptrsize >= 8)] const WORD_MASK uint = 0xFFFFFFFFFFFFFFFF
```

A file-wide constraint is just the annotation on the `package` clause — the
former "per-file" feature is now the package-clause special case of one uniform
mechanism.

**Non-goals (this doc):** a general const-folding evaluator; per-*statement* and
per-*field* gating (the attachment model anticipates them — `#[likely] if`,
`#[align] field` — but they're out of scope here); inline assembly itself; the
`.xfail` test-mode replacement.

---

## 2. Decisions ratified by the user (this revision)

1. **Syntax: the first-class `#[build(EXPR)]` annotation** (not a comment
   pragma) — §4.4 explains why a comment form can't do per-declaration.
2. **Granularity: per-declaration**, with file-level as the package-clause case.
3. **`version` unifies language/spec and compiler/interpreter** (the
   implementation is ground truth, Go-style) — one unqualified, ordered
   `version` predicate; **OS version is separate and namespaced** as
   `os.version`.
4. **Conditional `.bni` declarations are allowed** — valuable for per-target
   consts and target-specific data structures — which relaxes
   `pkg-layout-spec.md` Invariant 1 in that one spot (§4.3).
5. **Inline asm lives in its own doc** and merely references this substrate.
6. **A clause is `is(predicate, "tag")`, a membership test — not `==`.** A
   target sits in an *overlapping set* of descriptors (an aarch64 is also
   armv8.x, is also—as an alias—arm64), which equality can't model without
   incoherence (`arch == "aarch64"` and `arch == "arm64"` both true). `is()`
   reads "is the target's `predicate` (compatible with) `tag`", so simultaneous
   matches are expected. Clauses combine with `&& || !`. Ordered comparison
   (`>= <=`) is reserved for genuinely numeric/single-valued predicates
   (`ptrsize`, `version`, `os.version`); `at_least`/`at_most`-style matchers for
   versions come later. **Supersedes the `==`/`!=` comparison grammar shown in
   §5.2 below** (kept there only as the original sketch).
7. **Arch tags use the assembler's canonical names** (`pkg/binate/asm/parse`'s
   `.arch` directive): `x64`, `aarch64`, `arm32` — **with aliases** `x86_64`,
   `arm64`, `arm` resolving to the same arch (matching bnas). `os` tags:
   `linux`, `darwin`, `baremetal`. (The `ArchType` enum constant was renamed
   `ARCH_ARM64` → `ARCH_AARCH64` to match.)
8. **Arch/os are read from `pkg/builtins/build`**, not hardcoded. `binate-paths`
   already resolves the host (`uname`) or `--target` to the matching
   `build.bni`; `bnc` reads its `Arch`/`OS` const initializers. This is the
   single source of truth, correct on every host (incl. Linux), and uniform
   for host and `--target` — replacing the earlier "hardcode the host default"
   idea, which would have mis-gated on a non-macOS host.

**Implementation status (2026-06): the `arch`/`os` MVP is COMPLETE on `main`.**
Landed: parse `#[...]` on declarations + the package clause (the
`ast.Annotation` node); the `pkg/binate/buildcfg` evaluator
(`is(arch/os, "tag")`, alias-aware, hard-error on unknown predicate/tag); the
`ARCH_ARM64`→`ARCH_AARCH64` rename; `loader.ResolveBuildConfig` reading arch/os
from `pkg/builtins/build` (correct on every host, called by both `bnc` and the
`bni` VM); the **file-level** gate (package clause) and the **declaration-level**
gate (`gateMergedDecls`). Verified by `conformance/731_build_arch_select`
(file-level) and `733_build_decl_select` (declaration-level) across all six
default host modes, plus `buildcfg`/`loader` unit tests; arm32-linux selection
runs in CI. Deferred follow-ups: more predicates
(`triple`/`backend`/`libc`/`ptrsize`/`version`/`at_least`…), per-`import` and
conditional-`.bni` gating, `bnlint --target`, main-module gating, and migrating
the `impls/` duplicate trees onto constraints (§7, §11).

Still open: §11.

---

## 3. What exists today (grounding)

### 3.1 The annotation grammar already reserves a slot on every top-level decl

This is the fact that de-risks the whole per-declaration model. `grammar.ebnf`
already gives an optional `[ Annotation ]` to **every** top-level declaration
form:

| Form | Grammar | Annotation slot today |
|---|---|---|
| `ImportDecl` | `:134-135` | **yes** (incl. grouped) |
| `TypeDecl` | `:154-155` | **yes** (incl. grouped) |
| `VarDecl` | `:196-197` | **yes** (incl. grouped) |
| `ConstDecl` | `:215-216` | **yes** (incl. grouped) |
| `FuncDecl` / `MethodDecl` | `:225,228` | **yes** |
| `StructField` | `:187-188` | yes (future per-field) |
| `PackageClause` | `:132` | **no — the one gap** |

So the grammar work is essentially: add `[ Annotation ]` to `PackageClause`
(one line, mirroring the others). Everything else is *already reserved* — the
annotation system as a whole is `[DEFERRED]` (`grammar.ebnf:305-308`) with no
parser support yet, but the shape was designed for exactly this.

The annotation **machinery** is fixed too:
`Annotation = "#" "[" AnnotationList "]"`,
`AnnotationEntry = AnnotationName [ "(" AnnotationArgs ")" ]`,
`AnnotationArgs = Expression { "," Expression }` (`grammar.ebnf:310-314`).
`build`'s single arg is its applicability expression.

**Grouped-decl granularity caveat:** the slot sits on the *group*
(`const ( … )`, `var ( … )`, `type ( … )`), not on individual specs
(`ConstSpec`/`VarSpec`/`TypeSpec` have no slot). So a constraint gates a whole
group or a single ungrouped decl; to gate individual members, split them into
separate decls. (Consistent with the grammar; worth stating since per-target
consts are a prime use case.)

### 3.2 The attachment model is DECIDED — and reaches declarations

`claude-notes.md:804-822`: annotations "annotate the immediately following
element" — before a declaration keyword → annotates the declaration; on fields
and type definitions too; comma-separated within one block, no stacking. And the
detailed notes already anticipate pushing it to **statements** (`#[likely] if`,
`#[cold] for` — "natural extension of the attachment model to statements",
detailed-notes:2172). So per-declaration build-constraints are the *natural
reading* of an already-ratified model, not a new mechanism.

**Namespacing is DECIDED** (`claude-notes.md:808-811`): unqualified annotation
names = language-standard, compiler-**enforced**/typo-checked; `compiler.*` and
`tool.*` = ignored-if-unknown. `build` is unqualified → compiler-enforced, which
gives the §5.4 hard-error-on-typo policy *for free*. (Note: this *annotation-name*
namespace is distinct from the *build-predicate* namespace inside `#[build(…)]`,
§5.1.)

### 3.3 Comments are not in the AST

The lexer discards comments in `skipWhitespace()` with no token
(`lexer/scan.bn:5-46`); `ast.File` has no comment field; `ParseFile` goes
straight to `expect(PACKAGE)`. So a comment-form pragma is invisible to the
parser/AST and could only ever gate at file granularity — see §4.4.

### 3.4 Three coarse selection axes today, with real duplication

Variant selection is whole-directory (`pkg-layout-spec.md` Invariant 5, "Whole-
package selection only … needs the symlink workaround until per-file selection
is designed"), across three stacked axes: tier (`core`/`stdlib`) × platform
variant (`common`/`libc`/`baremetal`) × per-triple (`impls/targets/<key>/`). The
live duplication:

| Package | Duplicated as | Copies |
|---|---|---|
| `pkg/std/os/internal/internal.bn` | one tree per triple, `impls/targets/<key>/` | **5** |
| `pkg/bootstrap/bootstrap.bn` | `impls/core/{libc,baremetal}/` | 2 |
| `pkg/builtins/rt/rt.bn` | `impls/core/{libc,baremetal}/` | 2 |
| `pkg/std/os/os.bn` | `impls/stdlib/{libc,baremetal}/` | 2 |

`find impls -type l` is empty — the sanctioned symlink workaround has never been
used. The `os/internal` ×5 case is the acute one.

### 3.5 The loader's two seams; source-visible target metadata

`loadPackage` (`loader/loader.bn`) enumerates a package dir, filters `.bn` /
`_test.bn` (each `continue`), reads + `ParseFile`s each file, then `MergeFiles`
collects every file's decls **and imports**, and **only then** (`:341-358`)
follows imports recursively and the front-end type-checks. So there are two
natural gate seams: **(a)** the file-enumeration loop (pre-parse, file-level),
and **(b)** a pass over the merged decls *after* `MergeFiles` and *before*
import-following/resolution (post-parse, declaration-level). The `Loader` struct
has no build-config field yet (`Root, BniPath, ImplPath, Packages, Order,
Errors, TestPackages`).

The one source-visible target descriptor is `pkg/builtins/build` (no impl — the
constants *are* the package, one copy per target under `ifaces/targets/<key>/`,
selected by `binate-paths --target KEY`): `OS` (`OS_LINUX|OS_DARWIN|
OS_BAREMETAL`), `Arch` (`ARCH_X64|ARCH_ARM64|ARCH_ARM32`), `PtrSize`, `IntSize`.
Inside the compiler the same knowledge is scattered (`applyTarget` hardcodes
triple keys; `nativeArchForTarget` hardcodes arch strings; `types.TargetInfo`
carries only `PointerSize`/`IntSize`/`MaxAlign`; `suppressHostRuntime` is the
only libc-ish flag). `applyTarget` runs once in `main()` before loading, so the
config is frozen by enumeration time. There is no centralized predicate
registry — this feature introduces one.

---

## 4. The mechanism: `#[build(EXPR)]` at two tiers

### 4.1 Where each tier runs in the pipeline

- **Package clause** → **whole-file gate, pre-parse** (loader seam *a*). A
  bounded prescan reads the leading `#[…] package` and, on a false expression,
  skips the file before it is fully parsed. This is the *only* tier that can
  hide syntax (the body never parses on a target it doesn't apply to).
- **Declaration / import** → **per-decl gate, post-parse / pre-resolve** (loader
  seam *b*). The file parses in full; a filtering pass over the merged top-level
  decls drops the gated-out ones *before* import-following, name resolution, and
  type-checking. Gated-out imports are never followed; gated-out funcs/types/
  consts never reach the checker.

### 4.2 The central tradeoff: which tier to use

A per-declaration gated-out item **must still parse** on every target — it is
spared *resolution and type-checking*, not lexing/parsing. So:

- A decl-level gate can hide *semantics* (an `arm32`-only func referencing
  `arm32`-only types parses, then is dropped before resolution → fine on x64).
- A decl-level gate **cannot** hide *syntax* (a not-yet-parseable construct, e.g.
  a future language feature, or syntax only one backend's parser accepts).

When a file needs syntax or a language feature absent on the other target/
version, use the **file-level** (package-clause) gate — typically paired with a
`version` or `arch` constraint. When the items are individually parseable
everywhere and merely differ semantically, use **declaration-level**. This is
the principled reason to keep both tiers rather than collapse to one.

### 4.3 New semantics (and footguns)

- **Disjoint variant definitions** — the headline. Two `func barrier()` gated to
  disjoint conditions: exactly one survives, so no duplicate-definition error.
  The duplicate check must run *after* the gate, on survivors. Overlapping
  conditions for some config → a duplicate-definition error *for that config*,
  which is correct; the compiler never has to *prove* disjointness.
- **No survivor is fine** — every variant of `foo` gated out for a target ⇒
  `foo` doesn't exist there ⇒ references error "undefined" (the §5.4 hard-error
  backstop), same as the empty-package case.
- **Conditional `import`** is the thorny case. A dropped `import "pkg/foo"` means
  a *surviving* reference to `foo.Bar` fails resolution. Discipline (C `#ifdef`-
  style): gate the import and its uses on the same condition — usually automatic,
  since the uses live in decls gated the same way. The undefined-symbol hard
  error is the backstop. The gate must run *before* import-following (seam *b*
  precedes `loader.bn:341-358`) so a dropped import isn't loaded.
- **Conditional `.bni` declarations — ALLOWED** (user decision). Two sub-cases:
  - *Common:* one **unconditional** `.bni` declaration satisfied by N gated
    **impl** variants — clean, no Invariant-1 tension.
  - *New:* a **conditional interface declaration** (a const/type/func that
    genuinely only exists on some targets — e.g. a per-target const, or a
    target-specific data structure). This **relaxes Invariant 1** ("`ifaces/` is
    implementation-independent") in that one spot; the spec text needs an
    explicit exception. The gate therefore runs on parsed `.bni` decls too, and
    interface↔impl consistency (a conditional export must have a matching
    conditionally-defined impl, and vice versa) is the author's responsibility,
    enforced by the normal undefined/unsatisfied hard errors.
- **Grouped decls** gate as a unit (§3.1); split to gate members individually.

### 4.4 Why not a comment pragma (rejected alternative)

A `//bn:build …` comment can attach only to the *file* (via leading position) —
it cannot name "the 4th `const`" or "this `import`". Comments are discarded
before the AST (§3.3), so per-declaration gating is structurally impossible
through a comment channel. Per-declaration ⇒ real annotations. The comment form
also forces every tool into a parallel text-scan and a second expression
dialect, against Binate's non-magical value. It is therefore dropped, not
offered as a co-equal candidate. (If the parser work were to be staged, a
file-level-only comment pragma could be an *interim* before annotations land —
but the substrate below is identical, so there's little reason to.)

---

## 5. Predicate model & expression semantics

### 5.1 Vocabulary (build-predicate namespace)

A **closed, typo-checked** built-in set, plus the one **open** `tag.*`
namespace. Dotted names (`os.version`, `tag.debug`) are ordinary predicates in
this vocabulary — distinct from the annotation-name namespace of §3.2.

Categorical predicates use the membership form `is(predicate, "tag")`;
numeric/version predicates use ordered comparison or `at_least`/`at_most`.

| Predicate | Kind | Clause form | Tags / operand | Status |
|---|---|---|---|---|
| `arch` | categorical | `is(arch, "tag")` | `x64` `aarch64` `arm32` (aliases `x86_64` `arm64` `arm`) | **implemented** |
| `os` | categorical | `is(os, "tag")` | `linux` `darwin` `baremetal` | **implemented** |
| `triple` | categorical | `is(triple, "tag")` | the `--target` keys | deferred |
| `backend` | categorical | `is(backend, "tag")` | `llvm` `native_aa64` `native_x64` | deferred |
| `libc` | flag | `is(libc)` / `!is(libc)` | present / absent | deferred (see §5.5) |
| `ptrsize` | numeric | `ptrsize >= N` | `4`, `8` | deferred |
| `intsize` | numeric | `intsize >= N` | `4`, `8` | deferred |
| `version` | version | `is(version,"1.2")` / `at_least(version,"1.2")` | `"1.2"` etc. | deferred — + a canonical version source |
| `os.version` | version | `at_least(os.version,"13")` | `"13"` etc. | deferred — needs a deployment-target knob |
| `engine` | categorical | `is(engine, "tag")` | `bnc` `bni` | deferred — front-end plumbing (§5.5) |
| `tag.<name>` | open flag | `is(tag.<name>)` | `--tag <name>` | deferred — open namespace, unknown ⇒ false |

`version` is the **unified language/compiler version** (the implementation is
ground truth; the in-progress spec tracks it). `os.version` is the **target OS
version**, namespaced under `os`. `wordsize` (natural machine word) is
deliberately **not** exposed: it coincides with `ptrsize` on every current
target (arm32 4/4, LP64 8/8), so introducing it now would mean inventing a value
the compiler doesn't track — add it only when a target actually splits it from
`ptrsize`. `ptrsize`/`intsize` *are* exposed (though arch-derivable today)
because they read as *intent* (`#[build(ptrsize == 8)]` = "this layout assumes
64-bit pointers") and survive adding a new 64-bit arch.

Incremental by construction: a new predicate = one descriptor field + one
evaluator case + a domain entry. Ship `arch/os/triple/backend/libc/ptrsize/
intsize` first; add `version`, `os.version`, `engine`, `tag.*` as each earns it.
The §5.4 hard-error rule makes deferral safe.

### 5.2 Expression grammar

A strict, side-effect-free, resolution-free boolean sub-language with ordered
comparisons restricted to ordered operands:

```ebnf
BuildExpr  = OrExpr ;
OrExpr     = AndExpr { "||" AndExpr } ;
AndExpr    = UnaryExpr { "&&" UnaryExpr } ;
UnaryExpr  = [ "!" ] Primary ;
Primary    = Match | Compare | "(" BuildExpr ")" ;
Match      = Func "(" Predicate [ "," string_literal ] ")" ; (* is / at_least / at_most *)
Func       = identifier ;            (* "is" implemented; at_least/at_most for versions later *)
Compare    = Predicate CmpOp integer_literal ;  (* numeric predicates (ptrsize/intsize), later *)
CmpOp      = "==" | "!=" | "<" | ">" | "<=" | ">=" ;
Predicate  = identifier { "." identifier } ;
```

Each clause is an ordinary call/comparison expression (parsed by the normal
expression parser); the evaluator accepts only this restricted shape. `is` is
the implemented matcher; `at_least`/`at_most` and the numeric `Compare` arm
are deferred.

```
is(arch, "arm32") && is(os, "linux")
is(os, "baremetal") || is(os, "linux")
!is(backend, "llvm")
is(arch, "aarch64") || is(arch, "x64")
at_least(version, "1.4")            (* later *)
```

The evaluator **type-checks operand comparability** against the §5.1 table:
ordered ops (`< > <= >=`) require an *ordered* predicate (`ptrsize`, `intsize`,
`version`, `os.version`); `arch < "x64"` is a hard error. Enums take `== !=`
with a string operand from their domain; ints take an int operand; semver
predicates take a quoted dotted-decimal compared **component-wise** (missing
components = 0, so `version >= "1.4"` ⟺ `>= "1.4.0"`). This reuses the *shape*
of the reserved `AnnotationArgs = Expression` production but constrains it to a
decidable subset that evaluates with zero name resolution.

Why a full expression (not a Go `//go:build` tag-list): the config is
multi-valued (3 archs, 3 OSes, N triples) and needs `!=`, grouped negation, and
ordered version/size comparisons — a flat tag-list forces every enum value into
its own boolean tag (reintroducing the typo footgun) and can't express `>=`.
And it stays uniform with the `Expression`-valued annotation facility.

### 5.3 Evaluation timing & single authority

Evaluated in the loader/front-end, once per build (config frozen by then), at
*both* seams (§4.1). Introduce **one** build-config descriptor — a
`BuildConfig` on the `Loader`, populated by bnc reading `pkg/builtins/build`'s
`Arch`/`OS` const initializers (the copy `binate-paths` resolved for the host or
`--target`; see §2 item 8) — holding every predicate's resolved value + the
user-tag set + each predicate's type/domain (so the evaluator can type-check
§5.2). It is
the single source of "what predicates exist and what they are," and the one
place bnlint/hygiene read from (they have *zero* build-config context today).

### 5.4 Error semantics — unknown/malformed is a HARD ERROR

The safety property. A **silently-false** predicate ⇒ silent decl/file drop ⇒
vanished symbols ⇒ a *different* site fails later with "undefined symbol" far
from the cause — the silent-drop footgun class this project treats as critical.
Rules:

1. **Unknown built-in predicate ⇒ hard error** (`achr == …` → `unknown
   predicate "achr"`). The closed vocabulary makes typos checkable — exactly the
   "unqualified annotations are enforced" decision.
2. **Unknown tag / malformed version literal ⇒ hard error**
   (`is(arch, "armv7")`, `at_least(version, "1.x")`).
3. **Ill-typed comparison ⇒ hard error** (ordered op on an enum; comparison on a
   bare flag; int op on a semver predicate).
4. **Malformed expression ⇒ hard error** (unbalanced parens, dangling `&&`).
   The evaluator never recovers into a default.
5. **`tag.<name>` is the only false-because-absent case**, and only because its
   namespace is explicitly open. A bare unknown word is a typo'd built-in
   (rule 1), never an implicit tag.
6. **A not-yet-wired predicate** (`engine`, `os.version`, `tag.*` before its
   plumbing/flag exists) ⇒ hard error "predicate not available in this build
   configuration" — never silently false.
7. **Errors abort the build, not the file** — routed through the loader's
   existing error channel (`append l.Errors; return`), like a syntax error; they
   must **not** take the skip (`continue`) path, which *is* the silent-drop mode.

The asymmetry is the design: **evaluating to false skips the element (intended);
failing to evaluate aborts the build (safety).**

### 5.5 Caveats (flagged, not silently resolved)

- **`libc` is not first-class today** — no `libc`/`Hosted` constant in
  `build.bni`; freestanding is implied by `OS_BAREMETAL`, which is wrong in
  general (`arm32-linux` and host both have libc). Proposal: source `libc` from
  the same authority as `suppressHostRuntime`, and — separately, user's call —
  add a `Hosted`/`Libc` constant to `pkg/builtins/build` so source-level imports
  and constraints agree. Flagged, not derived from `os`.
- **`engine` (bnc vs bni) is not loader-knowable today** — both share
  `NewLoader`/`LoadImports`; an `engine` predicate needs each front-end to inject
  its identity into `BuildCfg`. Real plumbing; deferred; hard-errors until wired.
- **`os.version` needs a deployment-target knob** (e.g. `--os-version`); the
  current target model carries no OS version. Deferred; hard-errors until wired.

---

## 6. Loader / merge / front-end integration

- **File-level gate (seam a).** A bounded run of the *real* lexer over the
  in-memory `src` reads the leading `#[…] package`, slices the `build(...)`
  argument tokens by paren-balancing, evaluates; on false, `continue`. Reusing
  the real lexer (not a divergent prefix-lexer) avoids boundary-split bugs;
  build-constrained files are small, so reading the whole file first is fine.
- **Declaration-level gate (seam b).** After `MergeFiles`, a pass over the merged
  top-level decls (and imports, and `.bni` decls) drops any whose `#[build]`
  evaluates false, *before* the import-follow loop (`loader.bn:341-358`) and
  type-checking. Pure (no cross-decl state). The **duplicate-definition check
  runs on the survivors**, enabling §4.3 disjoint variants.
- **Empty-package handling — unchanged.** The existing `package "<path>" not
  found` guard (`bniFile == nil && len(files) == 0`) is correct in both
  directions: `.bni` present + all impls gated out → interface-only package (no
  error); nothing left + no `.bni` → genuinely unimportable for this target →
  error. Keep it; do not add a ">=1 surviving file" rule.
- **`.bni` gating.** Apply the same gate to the interface file and (per §4.3) to
  individual `.bni` decls. Keep the evaluator pure since `.bni` is processed
  before the impl loop.
- **Target threading.** Add `BuildConfig` to `Loader`, populated by bnc reading
  `pkg/builtins/build`'s `Arch`/`OS` constants (so the predicate values line up
  with what `import "pkg/builtins/build"` shows, and the host is correct on every
  platform); each front-end sets `engine` for itself. If `build` can't be
  resolved, the config stays inactive and an `arch`/`os` constraint then
  hard-errors rather than mis-gating.
- **BUILDER constraint.** The loader is inside `cmd/bnc`'s BUILDER-compilable
  tree, so the descriptor + evaluator + gate must stay within the BUILDER subset
  (no interfaces/generics/closures — CLAUDE.md "Builder Compatibility
  Constraint"). A flat token-walk evaluator over a tagged-union expr node fits;
  no fancy hierarchy.

---

## 7. Relationship to the `impls/` trees + migration

**Recommendation: COMPLEMENT the directory axes, don't replace them — and retire
the (unused) symlink workaround.** The `common`/`libc`/`baremetal` and
`targets/<key>/` directories do legible work: `binate-paths` selects a whole
sub-tree with one `-I`/`-L` prepend, and "what's in the baremetal build" stays
answerable by `ls`, not by grepping headers. So **coarse axis = directories;
fine axis = per-declaration (or package-clause) constraints.**

Per-declaration gating goes *further* than the original per-file idea: it
collapses not just per-package but **within-file** variation. Concretely:

- The `os/internal` ×5 per-triple trees → **one** package whose few
  triple-specific items carry `is(triple, "…")` (or whose whole files carry a
  package-clause gate, if they need triple-specific syntax).
- `pkg/bootstrap`, `pkg/builtins/rt`, `pkg/std/os` (each duplicated across
  `libc`/`baremetal`) → one directory: shared decls unconstrained, the few
  platform-specific decls gated `libc` / `!libc`.
- **Per-target consts** (the user's prime case) → one `const` group per target
  gated by `arch`/`triple`/`ptrsize`, side by side in one file, instead of a
  per-target file or tree.

Migration is **opt-in, incremental, no flag day** (the gate is a no-op for
unannotated decls): land the gate as a no-op → thread `BuildCfg` → pilot the
smallest duplicate (or `os/internal`) → verify on the existing target matrix
(`builder-comp_arm32_baremetal`, `…_arm32_linux`, host) → repeat per package.
Update Invariant 5's text to point at this facility instead of the symlink
workaround, and add the Invariant-1 exception for conditional `.bni` decls
(§4.3).

---

## 8. Tooling: bnlint, hygiene, lint-exempt

**bnlint must take `--target` — and per-declaration gating makes it *necessary*,
not optional.** bnlint walks the parsed/merged AST; if it walks *all* decls it
will try to resolve the `arm32`-only function on a host run and emit false
"undefined" errors. So:

- bnlint takes **`--target`/`--config`** (default host), threaded into
  `NewLoader.BuildCfg`; it then inherits the §6 gate, and **resolution/type-
  dependent** rules run only on the **active** decl set.
- **Purely syntactic/lexical** rules (formatting, naming, AST-shape) may still
  run on *all* parsed decls, including inactive variants — mirroring the
  config-blind vs config-relative hygiene split.
- **Full coverage** of inactive variants comes from a **per-config cover-set**: a
  decl gated `arch=="arm32"` is resolution-linted under an arm32 config in the
  matrix. This generalizes `lint.sh`'s existing `LINT_SKIP` (whole-package skip)
  from "skip everywhere" to "lint under the configs where this applies."

**Hygiene scripts** (raw `find` + text, no parse): config-**blind** checks
(trailing whitespace, import order, **line length**) stay blind — a constraint
doesn't excuse bad formatting. Only inherently config-**relative** checks (the
global `conformance` numbering; a future "every package has an impl for target
X") get a shared `bn-applicable-for` head-scan helper (sourced once, not
re-implemented per script — the sweep-completeness rule).

**Lint-exempt corollary (unified vocabulary).** Riding the decided `tool.*`
namespace (compiler ignores it), lint control is a sibling annotation:
`#[tool.lint(off("raw-slice-return"))]` on any declaration — **same attachment
model and same parser path** as `#[build]`. File-level on the package clause;
declaration-/region-level on a decl once the annotation parser lands. Today's
three `.whitelist` files and the `// LONG-LINE ALLOWED` marker are the legacy
forms, migrating onto in-source `tool.lint`/`tool.hygiene` over time (the
exemption lives next to the code it excuses and survives file moves). One scan
per element yields both `#[build(…)]` and `#[tool.lint(…)]`.

---

## 9. Phased implementation roadmap

Per-declaration ⇒ real annotation parsing is **foundational**, not deferrable
(there's no comment-pragma shortcut). But that parser is shared with the whole
annotation facility (`asm`, `packed`, `align`, `link`, `likely`), so it isn't
build-constraint-specific cost.

| Phase | Scope | Complexity |
|---|---|---|
| **0 — Substrate** | `BuildCfg` predicate descriptor + the §5.2 evaluator (flat token-walk, type-checked, BUILDER-subset) + hard-error vocab. Standalone-testable. | **Low–Med** |
| **1 — Annotation parsing** | Implement the reserved `#[…]` in the parser → real `ast.Annotation` nodes; add `[ Annotation ]` to `PackageClause`; consume the already-reserved slots on import/const/type/var/func. Shared with the whole annotation facility. | **High (shared)** |
| **2 — Gates in bnc** | File-level prescan (seam a) + declaration-level filter pass (seam b, after `MergeFiles`, before import-follow); duplicate-check on survivors; `.bni` + per-`.bni`-decl gating; keep the empty-package guard. Retire the symlink workaround. Needs merge-resolution tests (a dropped decl/import breaking a dependent). | **Med** |
| **3 — bnlint config** | `--target`/`--config` → `parseArgs`/`CLIArgs` → `NewLoader`; inherits the gate; resolution rules on active set, syntactic on all; `tool.lint(off …)` suppression; per-config cover-set (generalize `LINT_SKIP`). | **Med** |
| **4 — Hygiene matrix** | `--target` on `run.sh`; `bn-applicable-for` helper; wire only config-relative checks. Enumerate `find … *.bn` sites repo-wide (sweep rule). | **Med** |
| **5 — Vocabulary expansion** | Wire `version` (+ the version source), `os.version` (+ deployment-target knob), `engine` (front-end plumbing), `libc` first-class (`build.bni` constant), `tag.*` (`--tag`). Each is independent and hard-errors until wired. | **Low each** |

MVP = **Phases 0–3** with the `arch/os/triple/backend/libc/ptrsize/intsize`
vocabulary: per-declaration conditional compilation + a config-aware linter.
Phases 1–2 can co-land (the parser is what makes the gate possible).

---

## 10. Recommendation summary

1. **`#[build(EXPR)]` annotation, per-declaration**, file-level as the
   package-clause case. (Comment pragma dropped — can't do per-decl.)
2. **Full boolean expression** with ordered comparisons restricted to ordered
   operands, over a **closed, typo-checked** vocabulary + open `tag.*`.
3. **Hard-fail** on unknown/malformed/ill-typed/not-yet-wired (evaluate-false
   skips; fail-to-evaluate aborts).
4. **Two gate seams** (pre-parse file; post-merge/pre-resolve decl); duplicate-
   check on survivors; keep the empty-package guard; gate `.bni` too;
   BUILDER-subset evaluator.
5. **Complement** the `impls/` directories; retire the symlink workaround;
   collapse the `os/internal` ×5, the libc/baremetal duplicates, and per-target
   consts incrementally.
6. **bnlint `--target` is necessary**; config-blind formatting stays blind;
   unify lint-exempt under `tool.lint`.
7. **`version` = unified language/compiler**; **`os.version`** namespaced;
   `wordsize` deferred; ship a minimal vocabulary first.

---

## 11. Open decisions for the user

1. **Canonical `version` source** — exactly which value `version` reads (the
   `bnc`/spec version string), and where it's defined so the evaluator and a
   future `import "pkg/builtins/build"`-style exposure agree.
2. **`os.version` knob** — confirm a `--os-version`/deployment-target flag is the
   intended source, and its default (unset ⇒ `os.version` hard-errors).
3. **`libc` first-class** — add a `Hosted`/`Libc` constant to
   `pkg/builtins/build`, or keep deriving from `suppressHostRuntime`?
4. **`engine` in the MVP** — wire the front-end plumbing now, or defer (hard-error
   until then)?
5. **Invariant-1 amendment** — confirm the spec exception for conditional `.bni`
   declarations, and whether to constrain it (e.g. consts/types only, not funcs).
6. **Per-member group gating** — accept the "split a group to gate members"
   limitation, or add an `[ Annotation ]` slot to `ConstSpec`/`VarSpec`/
   `TypeSpec` (grammar change) for finer granularity?
7. **Phase 1/2 sequencing** — co-land the annotation parser with the gate, or
   land a minimal package-clause-only annotation parser first and add the other
   slots in 2?
8. **Per-config lint cover-set** — where declared (checked-in matrix vs derived
   from `ifaces/targets/*`) and the acceptable CI cost.
