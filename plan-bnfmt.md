# Plan: `bnfmt` — a Binate source formatter

> **Status:** proposal (not started) · **Owner:** TBD · **Scope of this doc:** the
> Fork-A MVP (out-of-tree formatter, comments recovered by a raw-source scan).
> v1/v2 and the optional front-end change (Fork B) are sketched at the end but
> are **not** ratified here.
>
> This plan was adversarially reviewed against the code before landing; §16
> records the material corrections. All `file:line` citations use the
> `pkg/binate/…` package-root path (e.g. `pkg/binate/token.bni`, distinct from
> the per-directory `pkg/binate/token/token.bn`).

## 1. Goal

A `gofmt`-equivalent for Binate: `bnfmt` reads `.bn`/`.bni` source and emits a
canonical, idempotent reformatting — consistent tab indentation, normalized
spacing, sorted imports, normalized blank lines — **while preserving every
comment**. Modes: write to stdout (default), rewrite in place (`-w`), and
check-only (`--check`, non-zero exit if any file would change).

The north-star invariant: **bnfmt never changes program meaning.** Re-lexing the
output must yield the same token stream as the input under a precisely-defined
normalization (§11.1). Comments — never tokens in the first place — are held to a
separate multiset-preservation invariant (§11.3).

## 2. Why this is not just "a new command"

Three facts (all read directly from the code) dominate the design:

- **The lexer discards comments** — `skipLineComment`/`skipBlockComment`
  (`pkg/binate/lexer/scan.bn:34-66`) consume the bytes and emit nothing. There
  is no `COMMENT` token (`pkg/binate/token.bni`) and **no comment field on any
  AST node** (`pkg/binate/ast.bni`). A parse→AST→print pipeline therefore
  *deletes every comment* — unshippable. Recovering comments is the whole
  project.
- **Positions are `{File, Line, Col}` — a *start* point only** (`pkg/binate/token.bni:152-156`).
  There is no byte offset and **no end position**. Critically, the parser
  discards closing-delimiter positions: `parseBlock` sets `s.Pos` to the `{`
  and `expect`s (drops) the `}` (`pkg/binate/parser/parse_stmt.bn:88-91`); call
  and element lists drop their `)`/`}` likewise. So a node's **end line is not
  recoverable from the AST**, and an empty block has no children to infer it
  from. This is the crux constraint for comment attachment (§6). Blank-line
  *runs* aren't recorded either (only a `sawNewline` bool survives, for ASI).
- **No printer exists** anywhere — only an IR dumper (`pkg/binate/ir/gen_print.bn`).
  The AST→source printer is greenfield.

Everything else is favorable (§4).

## 3. Architecture decision — Fork A (out-of-tree)

`bnfmt` is a new `cmd/bnfmt`, built **by bnc first** (like `bnlint`), and
**never touches the compiler front-end** (`pkg/binate/{lexer,token,parser,ast}`,
which are BUILDER-compiled and thus subset-constrained). Being outside bnc's
dependency tree, `bnfmt` itself may use the **full language** (interfaces,
generics, closures, floats) — confirmed by the `bnlint` precedent, whose tests
use full-language `@[]@[]char{…}` literals.

Comments and block/bracket **source spans** are recovered by an independent
**whole-buffer scan** of the raw source — a single state machine over
{code, line-comment, block-comment, string, char} that also tracks brace/paren
depth. The project already ships this exact shape in
`pkg/binate/repl/input.bn:computeOpenDepth` (bracket depth while skipping
comments); that is the base to generalize, **not** `bnlint`'s per-line
`findLineComment` scanner (§6 explains why the per-line base is wrong for block
comments).

**Rationale.** (1) Zero risk to the BUILDER-compiled compiler tree. (2) Ships
without a front-end rewrite. (3) `bnlint` set the precedent of keeping
source-text/comment concerns in the *driver*, out of the AST library
(`cmd/bnlint/suppress.bn:13-14`). *Skipping the type-checker* (§4) is justified
directly — a formatter is syntax-only, exactly as `gofmt` is — **not** by the
`bnlint` precedent, because `bnlint`'s driver actually *does* run the checker
(`cmd/bnlint/main.bn:245-261`); only its lint *library* is AST-only.

**The one thing Fork A cannot get for free: node end positions.** Comment
attachment needs to know where blocks/lists *end* in the source, and the AST
does not record that (§2). Fork A solves this by having the source scanner
compute block/bracket spans independently of the AST (§6). The alternative —
**Fork B** — is a *minimal* front-end change: add the closer's line to a handful
of nodes (`STMT_BLOCK`, call/element lists) — plus, more ambitiously, emit
comment trivia and a byte offset on `token.Pos` for exact `gofmt`-style
attachment. Fork B is cleaner but changes load-bearing, BUILDER-compiled infra
(positions feed diagnostics; ASI counts newlines inside comments). §13 surfaces
the Fork A-span-scanner vs Fork B-minimal-end-position choice as a user decision.

## 4. What the MVP reuses (near-zero build cost)

| Asset | Where | Use / caveat |
|---|---|---|
| Parser front-end | `parser.New(src, file)` for `.bn`, `parser.NewInterface(src, file)` for `.bni`; both `.ParseFile() → @ast.File` (`pkg/binate/parser.bni:42,45`) | Get the tree. **Skip the type-checker.** Driver MUST branch New vs NewInterface by extension (§9). |
| Source read | `readFile(path) → @[]uint8` (`pkg/binate/../cmd/bnc/util.bn:170`) | `parser.New` wants `*[]uint8` + the filename; the `@[]uint8` result needs a raw-slice view and the path passed as `file`. Not a one-arg call. |
| Raw literal text | `EXPR_STRING_LIT`/`EXPR_CHAR_LIT`/`EXPR_INT_LIT`/`EXPR_FLOAT_LIT` store exact source in `Name` (`pkg/binate/ast.bni:14-17,129-133`) | Re-emit verbatim; no escape/base re-encoding. |
| Annotations in AST | `Decl`/`ImportSpec`/`File`.`Annotations` (`pkg/binate/ast.bni`) | Print `#[…]` from structure (unlike comments). |
| Whole-buffer scanner | `computeOpenDepth` (`pkg/binate/repl/input.bn:36`) + literal-aware helpers from `cmd/bnlint/suppress.bn` | Base for `scanTrivia` (§6). |
| Tool packaging | `scripts/build-bnlint.sh` (~140-line template) | `build-bnfmt.sh` copies the two-stage build **structure**; header/usage/example prose need real edits (the `--help` echoes the header). |
| I/O out | `os.OpenFile`/`Stdout.Write` | stdout / `-w` / `--check`. `-w` must be crash-safe (§9). |
| Output assembly | `strings.Builder` | Accumulate formatted text. |
| Arg parsing | `bnlint`/`bnc` hand-rolled `streq` loop → CLIArgs struct | Same shape. |
| Hygiene coverage | `run.sh` auto-globs checks | lint/length/doc/test-coverage apply with **no wiring**. |

## 5. Pipeline

```
readFile(path) ─► src(@[]uint8)
      │
      ├─► (New | NewInterface by ext)(rawView(src), path).ParseFile() ─► @ast.File
      │
      └─► scanTrivia(src) ─► { comments[(line,col,endLine,text,ownLine?)],
                               blockSpans[(openLine,closeLine,depth)],
                               blankLines set }
                 │
                 ▼
      attach(File, trivia) ─► attachment plan (source-space)
                 │
                 ▼
      printer.Format(File, plan) ─► @strings.Builder ─► stdout | -w(atomic) | --check
```

Both the parser and `scanTrivia` consume the same `src`. `attach` runs in the
**source coordinate space** (input line/col), producing an ordered plan of
"emit comment C as leading-of node N / trailing-of node N / dangling-at
blockSpan S"; the printer then just emits those attachments as it walks. This
decouples attachment (which needs input positions) from printing (which lays out
new positions) — see §6.

## 6. Comment & blank-line recovery — the crux

### 6.1 Recovery — a whole-buffer state machine (not a per-line scanner)

`scanTrivia` scans the raw bytes once as a single state machine over states
{code, line-comment, block-comment, string, char}. Per-line scanning (bnlint's
`findLineComment`, which resets literal state each line and knows nothing of
`/* */`) is the **wrong base**: block comments span lines; a `/*` inside a
string must not open a comment (real case:
`pkg/binate/ir/gen_selector_test.bn:452` has a string containing `/*`); a `//`
inside a `/* */` must not be a line comment. The `computeOpenDepth` scanner
already has the correct joint code/line/block/string/char structure — generalize
it to also **emit** comments (with `startLine`, `startCol`, `endLine`, `text`,
and `ownLine?` via "only whitespace precedes on the line") and to **record
bracket/brace spans** as it tracks depth.

Output of `scanTrivia`:
- `comments`: each with `(startLine, startCol, endLine, text, ownLine?)`.
- `blockSpans`: `(openLine, closeLine, kind)` for every `{…}` / `(…)` / `[…]` —
  this is what supplies the **node end lines the AST lacks**.
- `blankLines`: set of source line numbers that are empty after trimming.

### 6.2 Attachment — in source space, keyed by (line, col)

Because the AST has node *start* positions but no end positions (§2), attachment
is computed against source coordinates using node start-`Pos` **and** the
`blockSpans` from the scanner:

- **Leading** (own-line comment before a node): attaches to the next node whose
  start-line is > the comment's line, at that node's indent.
- **Trailing** (code precedes the comment on its line): attaches to the node
  whose start-`Pos` is the largest with `startCol <= comment.startCol` on that
  same line. Keying by `(line, col)` — not line alone — is required because
  multiple statements/fields legitimately share a line (`a; b`,
  `type T struct { x int; y int }`, and the pervasive test idiom
  `r = f(); if len(r) > 0 { return r }`); line-only keying would mis-attribute.
- **Dangling** (no following node in the enclosing block — e.g. a comment in an
  empty `{}` / `interface {}` / after the last decl): attaches to the enclosing
  `blockSpan` and flushes just before the block's `closeLine`. This case is
  **unsolvable from the AST alone** (the `}` line isn't stored, empty blocks
  have no children) and is exactly why `scanTrivia` must compute `blockSpans`.

### 6.3 Coordinate reconciliation

Attachment fixes *which node/gap* each comment belongs to using **input**
positions. The printer emits at **output** positions. The bridge is the
attachment plan (§5): each comment is bound to an AST node or a blockSpan, not to
an absolute output line, so when the printer moves line breaks the comment still
lands at the right structural point. Blank lines are reproduced from the
`blankLines` set relative to node boundaries (collapse policy is a §13 decision).

### 6.4 Honest risk statement

Trailing/dangling attachment is the **single biggest risk** in the whole plan,
precisely because the AST records no end positions and Fork A reconstructs them
from a separate scan that must stay in lockstep with the parser's view of the
same bytes. Mid-expression block comments (`foo(/* x */ a)`) have no AST anchor
at all; MVP attaches them to the nearest following token's node and documents the
placement, but must never *drop* one (the §11.3 invariant is the backstop). If
span-scan reconstruction proves too brittle in practice, the escape hatch is the
**minimal Fork-B change**: record the closer line on `STMT_BLOCK` and the
list-bearing nodes (a small, additive field on a few structs). This is a user
decision (§13), not something to slip in silently.

## 7. Package layout

```
cmd/bnfmt/
  main.bn          — arg parse (streq → CLIArgs), I/O, mode dispatch, --version, ext branch
  main_test.bn
  trivia.bn        — scanTrivia: whole-buffer state machine (comments + blockSpans + blankLines)
  trivia_test.bn
  attach.bn        — attachment plan (source-space)
  attach_test.bn
  README.md
pkg/binate/bnfmt/  — the printer library (factored like pkg/binate/lint: unit-testable in isolation)
  printer.bn       — Format(@ast.File, plan) entry; File/Decl dispatch; imports
  print_expr.bn    — Expr kinds (incl. EXPR_TYPE → delegates to type printer on TypeRef)
  print_builtin.bn — EXPR_BUILTIN sub-ops (make/make_slice/box/cast/bit_cast/len/sizeof/alignof/present/same/unsafe_*/__c_call)
  print_stmt.bn    — Stmt kinds
  print_type.bn    — TypeExpr kinds
  print_paren.bn   — precedence-driven paren (re)insertion
  print_util.bn    — indent/spacing helpers
  *_test.bn         (one per file — test-coverage hygiene)
```

Splitting is forced by the file-length cap (`.bn` warn 500 / error 600); the
printer splits naturally along Expr/Builtin/Stmt/Type/Decl boundaries.

## 8. MVP scope

**In:**
- Full per-Kind reprint of every parser-producible `Expr`/`Stmt`/`Decl`/`TypeExpr`/helper kind.
  - **`EXPR_TYPE` is parser-produced** (`pkg/binate/parser/parse_expr.bn:480`,
    for type-form generic args like `f[@T]`, `slices.Append[@ast.Decl](…)`) and
    **must be printed** — delegate to the type printer on its `TypeRef`
    (`pkg/binate/ast.bni:37-44`). Only `TEXPR_TYPE_PARAM` is genuinely
    checker-only (`pkg/binate/ast.bni:105-107`) and may be asserted-out.
  - **`EXPR_BUILTIN`** dispatches on `Op` to fixed templates; some take a *type*
    first arg (`make`/`cast`/`bit_cast`/`sizeof`/`alignof`) routed through the
    type printer, and `__c_call` needs its `…` varargs marker reinserted at
    `CFixedArgs`.
- Tab indentation; canonical single-space operator/comma spacing; tight `@`/`*`
  sigils; `readonly` spacing; K&R braces.
- **Comment preservation** (leading/trailing/dangling, §6) and blank-line handling.
- **Paren re-derivation** from precedence (value parens are dropped by the parser;
  there is no `EXPR_PAREN`). Preserve the *mandatory* ones: pointer-to-array/slice
  `*([N]T)` and the **D4** composite-literal-in-condition parens. D4 parens are
  driven by *context* (the printer knows it is emitting an `if`/`for`/`switch`
  condition), not by a stored paren node — the AST collapses them to a bare
  `EXPR_COMPOSITE` (§13 flags the D4 "currently defective" ebnf note to reconcile).
- **Import canonicalization:** the AST is a flat `@[]@ast.ImportSpec` that does
  **not** record whether the source used one-per-line or grouped `import (…)`
  (the parser accepts both — `pkg/binate/parser/parser.bn` `ParseImportDecl`; the
  grouped form appears in conformance/REPL though *nowhere* in `pkg/`/`cmd/`). So
  bnfmt **canonicalizes all imports to one-per-line**, sorted within each
  blank-delimited run (matching `scripts/hygiene/file-format.sh` check 4). This
  is a stated canonicalization with a token-equality caveat (§11.1), not a form
  bnfmt can round-trip.
- **Trailing-comma correctness** for multiline lists — an ASI *correctness*
  constraint, not cosmetic (§11.1), therefore MVP.
- **`// LONG-LINE ALLOWED` preservation** (`scripts/hygiene/line-length.sh`): this
  opt-out marker is a trailing comment; bnfmt must keep it on its line and never
  reflow/split a line bearing it (else output regresses line-length hygiene).
- File hygiene: exactly one final newline, no trailing whitespace, no trailing blank lines.
- The token-equality + comment-multiset test harness (front-loaded, §11).

**Explicitly deferred — each a *policy decision the user owns* (§13), not a
silent carve-out:**
- Column alignment (struct field types, case bodies, trailing-comment columns) —
  the tabwriter. **Consequence:** until this lands, bnfmt output will *not* match
  the tree's existing hand-alignment, so running it repo-wide produces large
  alignment diffs. This is why bnfmt is **not** wired into hygiene at MVP (§13).
- Line-wrapping of over-long constructs to the 100-col cap.
- Comment-interior reflow.
- Collapsing interior 2+ blank lines to 1 (gofmt does; tree tolerates doubles).
- Expanding single-line `if x { y }` blocks.

## 9. Failure modes & data safety

- **Parse errors:** `ParseFile` returns an `@ast.File` *and* accumulates errors.
  On any parse error, bnfmt (like gofmt) **does not rewrite** — it exits non-zero
  and leaves the file untouched; in stdout mode it prints nothing (or the
  diagnostic to stderr). Never emit partial output under `-w`.
- **`-w` atomicity:** write to a temp file in the same directory, then rename over
  the original, so a crash mid-write cannot corrupt a source file.
- **Degenerate inputs:** empty files, comment-only files, and package-clause-only
  files must round-trip sanely (comment-only → the comments; empty → empty or a
  single newline per hygiene).
- **`.bni` vs `.bn`:** the driver selects `NewInterface` vs `New` by extension
  (correctness, not a style option). `.bni` decls are signatures/forward-decls
  (no bodies) and interface method sets; the printer prints what the parser
  produced and does not synthesize bodies.

## 10. Encoding & line endings

- **Line endings:** define LF as canonical output. If CRLF inputs must be
  supported, normalize on read; the trivia scanner is byte/line based and must
  count lines consistently with the parser.
- **Tabs/newlines inside string literals:** literals are re-emitted verbatim from
  `Name`, which helps, but the trivia line-splitter must not miscount a `\n`
  inside a raw string as a source line break — the whole-buffer scanner's `string`
  state handles this (another reason not to use a per-line scanner).
- **Non-ASCII:** confirm the scanner's line/col cursor and comment slicing stay
  **byte**-correct for multibyte UTF-8 in identifiers/comments (the lexer
  classifies letters via ASCII ranges; col is a byte column).

## 11. Testing strategy

### 11.1 Token-equality (semantic preservation) — the gate
Re-lex `fmt(x)` and `x` and compare token streams under this **precise**
normalization (comments are already absent — the lexer never emits them — so
there is nothing to "ignore"):
1. Drop all `SEMICOLON` tokens (ASI-inserted `\n`-semis and explicit `;` alike).
2. Drop a `COMMA` immediately preceding a closing `)` `]` `}` (trailing comma),
   because MVP legitimately changes trailing-comma presence when it re-flows a
   list between single- and multi-line.

Then require equal token sequences. Proof obligation to state in the harness:
no *other* token can differ between a valid input and its reformatting.
Watch-item: **multi-line adjacent string literals** rely on an ASI semi that the
parser merges in expression context but treats as a separator in grouped imports
(`pkg/binate/parser/parse_primary.bn:105-124`) — the gate's semi-drop must not
conflate the two; add a test with adjacent strings in **both** an expression and
a grouped import.

### 11.2 Idempotence / fixpoint
`fmt(fmt(x)) == fmt(x)` on a curated corpus. Do **not** run it repo-wide as a
hygiene gate until alignment (v2) lands, since MVP output diverges from the
tree's current hand-alignment.

### 11.3 Comment-preservation invariant (multiset, not "exactly once")
Re-run `scanTrivia` on the **output** and require the **multiset** of comment
texts to equal the input's. "Appears exactly once" is wrong: identical comments
recur across the tree (`// ===…` banners, bare `//`, repeated `// TODO`), and a
substring search over output would false-positive on comment text that also
occurs as code.

### 11.4 Golden corpus
Hand-written before/after cases per construct and per attachment case: own-line,
trailing on a shared-line node, dangling in an empty block, block comment
spanning lines, mid-expression block comment, a string containing `/*` and `//`,
`//` inside `/* */`, a `// LONG-LINE ALLOWED` line, grouped-import input,
`f[@T]`/`f[@[]char]` generic type args. Unit-test level (`_test.bn`: source in →
formatted string out).

## 12. Style rules to encode (verified against the codebase)

| Rule | Value | Source |
|---|---|---|
| Indent | tabs; align-padding (v2) uses spaces after the tabs | 0 pure-space-indented lines under `pkg/binate` |
| Line length | 100 hard; opt-out via a trailing `// LONG-LINE ALLOWED` (preserve, never reflow) | `scripts/hygiene/line-length.sh` |
| Operator/comma spacing | single space around binops/`=`/`:=`; space after `,`/`;`, none before; none inside `()`/`[]` in call/index | `pkg/binate/types/checker.bn` |
| Sigils | `@`/`*` tight to type; `readonly` space-separated | `pkg/binate/ast.bni` field types |
| Braces | K&R same-line, always present | `pkg/binate/types/checker.bn` |
| Imports | canonicalized to one-per-line (grouped input collapsed); each blank-delimited run sorted alphabetically | `scripts/hygiene/file-format.sh` check 4 |
| Semicolons | none (ASI); trailing comma mandatory in multiline lists | `docs/spec/binate.ebnf:112-118` |
| Alignment (v2) | struct field types, case bodies, trailing comments — per contiguous run, reset by blank/comment; assignments **not** aligned | `pkg/binate/token.bni`, `pkg/binate/token/token.bn` |

## 13. Open decisions for the user

- **End-position strategy** (the crux): Fork-A source-span scanner
  (`scanTrivia` computes `blockSpans`, no compiler change) vs a **minimal Fork B**
  (add a closer-line field to `STMT_BLOCK` + list nodes in the BUILDER-compiled
  tree, for exact attachment). Recommend starting Fork A; escalate to minimal
  Fork B only if span reconstruction proves brittle.
- **Grouped-import output:** canonicalize grouped → one-per-line (recommended,
  §8) vs preserve grouping. (AST can't round-trip the form; a choice is forced.)
- **Blank-line collapse** (gofmt collapses 2+→1; tree tolerates doubles).
- **Single-line block expansion** (`if x { y }`) — preserve vs always-expand.
- **Canonical wrap style** (v2): fill-with-extra-tab vs one-item-per-line — the
  tree is inconsistent; bnfmt must pick one.
- **Redundant-paren policy:** MVP *necessarily* drops redundant value parens
  (they're not in the AST); flagging so it's a ratified choice, not a silent one.
- **D4 reconcile:** `docs/spec/binate.ebnf` notes the D4 parenthesized-composite
  escape as "§13.11 currently defective" — confirm emitted D4 parens round-trip
  through the *current* parser under the token gate before relying on them.
- **`.bni` style** rules beyond parser selection (identical modulo interface-file
  restrictions?).
- **Hygiene wiring** is a *separate, later* user decision (out of scope here): a
  `format-check.sh` running `bnfmt --check` would mirror `lint.sh` — but only
  after alignment (v2), so the tree is a fixpoint. Adding the tool ≠ wiring it up.

## 14. Effort (anchored to the kind inventory)

Sizing is best expressed as **printer kinds + the hard subsystems**, not
person-weeks:
- Printer walk: 17 `EXPR_*` (incl. `EXPR_TYPE`→type path, `EXPR_BUILTIN`
  sub-dispatch over ~17 builtin ops) + 13 `STMT_*` + 14 `TEXPR_*` + 7 `DECL_*` +
  helpers — individually trivial-to-moderate, mechanical, landable one group per
  commit.
- Precedence-driven paren (re)insertion: one focused module + table.
- `scanTrivia` whole-buffer state machine + `blockSpans`: a real scanner (not a
  sed of bnlint's per-line helper), with the literal/comment hazard tests (§11.4).
- Attachment (§6): **the schedule risk.** Trailing/dangling placement without AST
  end positions is where estimates blow up; budget the most tests here, and hold
  the minimal-Fork-B escape hatch in reserve.
- Test harness (§11.1/11.3): front-load a slice during the walk work.

**80/20:** the per-Kind walker + `scanTrivia`/attachment + the two invariants
deliver most of the value (consistent indent/spacing, import order, blank lines,
comments preserved) using only `Pos.Line`/`(line,col)` arithmetic + the source
scan — no width engine. Alignment and wrapping (the width engine, v2) are where
the remaining, deferrable complexity lives.

## 15. MVP task breakdown (ordered; each step an independently-green cherry-pick)

1. `scripts/build-bnfmt.sh` (copy the two-stage build **structure** from
   `build-bnlint.sh`; edit header/usage/example prose); an empty `cmd/bnfmt` that
   reads a file and writes it back **byte-for-byte** (no formatting) to prove the
   build/gen1 wiring + I/O + `-w` atomicity + ext branch.
2. **Type printer** (`print_type.bn`, all parser `TEXPR_*`) — an early dependency,
   because signatures, fields, and `EXPR_TYPE` all need it. Token-equality test
   scaffold (§11.1) stood up here.
3. Package clause + **canonicalized, sorted imports** (needs no type printer);
   first end-to-end token-equality green on import-only files.
4. **Expr printer** (`print_expr.bn`, all parser `EXPR_*` incl. `EXPR_TYPE`) +
   `print_builtin.bn` (`EXPR_BUILTIN` sub-ops) + `print_paren.bn` (precedence).
5. Function/method **signatures** + `Decl` (var/const/type, grouped blocks,
   annotations) — now buildable since the type + expr printers exist.
6. **Stmt printer** (`print_stmt.bn`, all `STMT_*`), blocks/indent, ASI
   trailing-comma rule, D4 condition-paren context.
7. `scanTrivia` — whole-buffer state machine: comments + `blockSpans` +
   `blankLines`, with the hazard tests (§11.4).
8. **Attachment** (`attach.bn`) — leading/trailing/dangling in source space;
   comment-multiset invariant test (§11.3).
9. Blank-line handling; file hygiene (final newline, no trailing ws/blanks);
   `// LONG-LINE ALLOWED` preservation.
10. CLI modes (`-w` atomic, `--check`, stdout, `--version`); parse-error/degenerate
    handling (§9); README; a `_test.bn` per file.
11. Fixpoint test (§11.2) over a **curated** subset (not the whole tree until v2).

## 16. Review status

This plan was adversarially reviewed against the code (2026-07-01) before
landing. Material corrections made in response:
- **Blocker:** the draft's claim that `EXPR_TYPE` is checker-only and could be
  asserted-out was **wrong** — it's parser-produced and pervasive; the printer
  must render it (§8, §15). Only `TEXPR_TYPE_PARAM` is checker-only.
- **Blocker:** node **end positions are unrecoverable from the AST** (the parser
  drops closing-delimiter positions); the draft's "re-derive end from children"
  fallback fails for empty blocks. §6 is rebuilt around a source-span scanner
  (`blockSpans`), with a minimal Fork-B escape hatch surfaced as a user decision.
- **Blocker:** task ordering had signatures before the type printer; §15 now
  builds the type printer first.
- **Major:** grouped-import input isn't round-trippable (AST is form-agnostic) →
  stated canonicalization (§8/§12). Block-comment recovery needs a whole-buffer
  state machine, not an extension of bnlint's per-line scanner (§3/§6). The
  token-equality gate is now precisely specified (drop semicolons + trailing
  commas; §11.1). The comment invariant is now a **multiset** (§11.3). Added
  `// LONG-LINE ALLOWED` preservation, a Failure-modes/`-w`-atomicity section
  (§9), and an encoding/line-endings section (§10). Reworded the bnlint-precedent
  framing (the checker-skip is justified directly, not by bnlint, whose driver
  runs the checker).
- **Minor/nits:** corrected `readFile`→`parser.New` arg/type coercion and the
  `New`/`NewInterface` branch; fixed `EXPR_*` enum names and `pkg/binate/…`
  citations; noted `build-bnfmt.sh` is a ~140-line template needing prose edits;
  re-anchored effort to kind counts.
