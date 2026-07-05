# Plan: `bnfmt` — a Binate source formatter

> **Status:** proposal (not started) · **Owner:** TBD
>
> Architecture **ratified 2026-07-01: Fork B** — the shared front-end is taught
> to retain what a formatter (and a future doc generator) needs, rather than
> reconstructing it out-of-tree. See §3 for the decision and §16 for the log.
> All `file:line` citations use the `pkg/binate/…` package-root path (e.g.
> `pkg/binate/token.bni`, distinct from the per-directory `pkg/binate/token/token.bn`).
>
> This is a plan for a **complete, correct** formatter — alignment and
> line-wrapping included, because matching the codebase's established style is
> part of doing it right. It is delivered in **safe, independently-landable
> increments** (§14), not staged as a throwaway MVP.

## 1. Goal

A `gofmt`-equivalent for Binate: `bnfmt` reads `.bn`/`.bni` source and emits a
canonical, idempotent reformatting — consistent tab indentation, normalized
spacing, sorted imports, normalized blank lines, gofmt-style column alignment,
and width-aware wrapping — **while preserving every comment**. Modes: stdout
(default), rewrite in place (`-w`), check-only (`--check`, non-zero exit if any
file would change).

The north-star invariant: **bnfmt never changes program meaning.** Re-lexing the
output must yield the same token stream as the input under a precise
normalization (§11.1). Comments are held to a separate multiset-preservation
invariant (§11.3).

## 2. What the front-end throws away today

Three facts (read directly from the code) motivate the Fork-B front-end work:

- **The lexer discards comments** — `skipLineComment`/`skipBlockComment`
  (`pkg/binate/lexer/scan.bn:34-66`) consume the bytes and emit nothing. No
  `COMMENT` token (`pkg/binate/token.bni`); **no comment field on any AST node**
  (`pkg/binate/ast.bni`). A parse→AST→print pipeline deletes every comment.
- **Positions are a *start* point only** (`pkg/binate/token.bni:152-156`): no end
  position. The parser even drops closing-delimiter positions — `parseBlock` sets
  `s.Pos` to the `{` and `expect`s (discards) the `}`
  (`pkg/binate/parser/parse_stmt.bn:88-91`); call/element lists drop their
  `)`/`}`. So a node's **end line is unrecoverable from the AST**, and an empty
  block has no children to infer it from.
- **No printer exists** — only an IR dumper (`pkg/binate/ir/gen_print.bn`).

The lexer *computes* a byte offset (`l.pos`) and *sees* every comment, then
discards both. Fork B stops discarding; the rest of the tooling stops
reconstructing.

## 3. Architecture — Fork B (ratified)

**Decision:** enrich the shared front-end so the parse output carries comments
and node extents; `bnfmt` (and future doc-gen) consume that instead of
re-scanning source. `bnfmt` itself is a new `cmd/bnfmt`, built **by bnc first**
(like `bnlint`), so it may use the **full language**; only the front-end changes
(§4) land in the BUILDER-compiled tree.

**Why not Fork A (out-of-tree re-scan).** A formatter that reconstructs comments
and spans from a second, independent scan of the source is a *second tokenizer*
that must agree with the real lexer about string/char boundaries, `/*`-inside-
strings, `//`-inside-block-comments, and line/col numbering — **forever**. When
they disagree on an edge case the failure is silent (a dropped or moved comment
== source data loss). The tree already carries **three** such ad-hoc re-scanners
(`cmd/bnlint/suppress.bn` for `// bnlint:allow`; `scripts/hygiene/bn-doc.sh` and
`bni-doc.sh`); `bnfmt` would be a fourth and the heaviest. Fork A routes around a
front-end that lies about what it saw; Fork B fixes it.

**Why not Fork C (a lossless CST) now.** A full-fidelity syntax tree is the right
foundation *if* an LSP / codemod / IDE story is coming. It is not on the roadmap
(bnfmt + doc-gen is the realistic ceiling), so Fork C is not justified for a
formatter alone. The door stays open cheaply: Fork B's tokenizer-level enrichment
(comment retention, end positions, and later a byte offset) is exactly the
substrate a CST would need, so it is a partial **down payment**, not throwaway.

**Cost.** Fork B touches `pkg/binate/{token,lexer,parser,ast}` — BUILDER-compiled
and actively developed by other workers. The changes are additive struct fields +
one constructor (no new language feature), inert on the compile path, and must be
verified against the pinned BUILDER before landing. That coordination cost is the
price of not maintaining a fourth re-scanner indefinitely.

## 4. Front-end changes (the Fork-B work)

Four additive, compiler-inert changes. Scoped deliberately: comments live in a
side-list on `File` (**not** fields on every node — the compiler doesn't want
that); collection is flag-gated so the compile path allocates nothing.

**`token`**
- New `token.Comment { Pos token.Pos; End token.Pos; Text @[]char; OwnLine bool }`.
  Lives in `token` because the lexer (which cannot import `ast`) produces it.
  `OwnLine` = only whitespace preceded the comment on its line (leading vs.
  trailing).
- `token.Token` gains `End token.Pos` — the position just past the token, which
  the lexer already knows after scanning it. This is what lets the parser stamp
  node ends cheaply.

**`lexer`**
- A collect-comments mode, enabled via a constructor variant (default off →
  compiler unchanged and zero-cost). When on, `skipLineComment`/`skipBlockComment`
  append a `token.Comment` (they already hold the source slice + offsets via
  `sliceToChars`). Track `OwnLine` from the existing column/newline state.

**`ast` + `parser`**
- `ast.File` gains `Comments @[]@token.Comment` (same managed-slice-of-managed-ptr
  shape as the existing `Decls @[]@Decl`, so within the BUILDER subset; empty
  unless collecting).
- `Stmt`/`Decl`/`TypeExpr` each gain `End token.Pos`. The parser stamps it from
  the last consumed token (`token.Token.End`) as it finishes each node, via an
  `endFrom` helper that never yields a backwards span. (As implemented — step 14.1:
  `Expr.End` is **deferred**, since the expression parser has no single choke point
  and comment attachment doesn't need it; adding it partially would ship a
  half-populated field.)
- `parser` gains a comment-collecting constructor variant that threads the lexer
  flag and, at EOF, stamps the accumulated list onto `File.Comments`.

**BUILDER discipline.** All of the above are plain struct fields + a function —
not new syntax/builtins — so the pinned BUILDER should compile them; confirm with
a direct BUILDER build before landing (per the "verify against BUILDER" rule).
Positions are start-only and *untested* beyond error text, so adding `End` is
low-risk. These changes are independently useful (end positions enable
range-based diagnostics later) and should land as their own commits ahead of the
printer.

## 5. What the formatter reuses

| Asset | Where | Use / caveat |
|---|---|---|
| Parser front-end | `parser.New` (`.bn`) / `parser.NewInterface` (`.bni`); both `.ParseFile() → @ast.File` (`pkg/binate/parser.bni:42,45`) + the new comment-collecting variant (§4) | Get the tree **and** `File.Comments`. **Skip the type-checker** (syntax-only, like gofmt). Driver branches New vs NewInterface by extension (§9). |
| Comments + node ends | `File.Comments`, node `End` (§4) | Attachment is now exact (§7). |
| Source read | `readFile(path) → @[]uint8` (`cmd/bnc/util.bn:170`) | Coerce to `*[]uint8` + pass the filename; not a one-arg call. |
| Raw literal text | `EXPR_STRING_LIT`/`EXPR_CHAR_LIT`/`EXPR_INT_LIT`/`EXPR_FLOAT_LIT` `Name` (`pkg/binate/ast.bni:14-17,129-133`) | Re-emit verbatim; no escape/base re-encoding. |
| Annotations | `Decl`/`ImportSpec`/`File`.`Annotations` | Print `#[…]` from structure. |
| Tool packaging | `scripts/build-bnlint.sh` (~140-line template) | `build-bnfmt.sh` copies the two-stage build **structure**; header/usage/example prose need real edits. |
| I/O out | `os.OpenFile`/`Stdout.Write` | stdout / `-w` (crash-safe, §9) / `--check`. |
| Output assembly | `strings.Builder` | Accumulate output. |
| Arg parsing | `bnlint`/`bnc` `streq` loop → CLIArgs | Same shape. |
| Hygiene coverage | `run.sh` auto-globs checks | lint/length/doc/test-coverage apply with no wiring. |

## 6. Pipeline

```
readFile(path) ─► src(@[]uint8)
      │
      ▼
(New | NewInterface, comment-collecting)(rawView(src), path).ParseFile()
      │
      ▼
@ast.File  { Decls, Imports, Comments[@token.Comment], per-node Pos+End }
      │
      ▼
printer.Format(File) ── interleaves File.Comments by (Pos,End) during the walk
      │
      ▼
@strings.Builder ─► stdout | -w(atomic) | --check
```

Comments and extents come straight from the parse — no separate scan, no
coordinate reconciliation.

## 7. Comment attachment

With `File.Comments` (from the lexer) and node `End` positions, attachment is the
standard `go/printer` interleave, and correct by construction:

- Maintain a cursor into the position-sorted `File.Comments`.
- Before emitting a node that starts at `Pos`, flush every pending comment whose
  position precedes `Pos` as **leading** (at the node's indent) — unless it is on
  the same line as the **end** of the previously-emitted node, in which case it is
  **trailing** on that line. Node `End` is what makes this distinction exact.
- A comment with no following node in its enclosing block is **dangling**; flush
  it before the block's closing delimiter. The block node's `End` supplies the
  `}` line the AST previously lacked — this is precisely the case Fork A could not
  solve without reconstruction.
- Mid-expression block comments (`foo(/* x */ a)`): anchored to the following
  token's node; MVP-era "must reconstruct spans" worry is gone, but placement of
  interior block comments is still a documented, tested behavior (never dropped —
  §11.3 is the backstop).

Multi-node lines (`a; b`, `type T struct { x int; y int }`, the pervasive
`r = f(); if len(r) > 0 { return r }`) attach by `(line, col)`, resolving a
trailing comment to the node whose `End` is the largest at/left of the comment on
that line.

## 8. Formatting scope (the complete tool)

Everything here is part of the finished formatter; §14 sequences it.

- Full per-Kind reprint of every parser-producible `Expr`/`Stmt`/`Decl`/`TypeExpr`/helper kind.
  - **`EXPR_TYPE` is parser-produced** (`pkg/binate/parser/parse_expr.bn:480`, for
    type-form generic args like `f[@T]`, `slices.Append[@ast.Decl](…)` — pervasive)
    and **must be printed**: delegate to the type printer on its `TypeRef`
    (`pkg/binate/ast.bni:37-44`). Only `TEXPR_TYPE_PARAM` is checker-only
    (`pkg/binate/ast.bni:105-107`) and may be asserted-out.
  - **`EXPR_BUILTIN`** dispatches on `Op` to fixed templates; some take a *type*
    first arg (`make`/`cast`/`bit_cast`/`sizeof`/`alignof`) routed through the type
    printer; `__c_call` reinserts its `…` varargs marker at `CFixedArgs`.
- Tab indentation; canonical operator/comma spacing; tight `@`/`*` sigils;
  `readonly` spacing; K&R braces.
- **Comment preservation** (§7) and blank-line handling.
- **Paren re-derivation** from precedence (value parens are dropped; there is no
  `EXPR_PAREN`). Preserve the *mandatory* ones: pointer-to-array/slice `*([N]T)`
  and the **D4** composite-literal-in-condition parens (driven by *context* — the
  printer knows it is in an `if`/`for`/`switch` condition — since the AST collapses
  them to a bare `EXPR_COMPOSITE`; reconcile the "§13.11 currently defective" ebnf
  note before relying on emitted D4 parens under the token gate).
- **Import canonicalization to one-per-line**, sorted within each blank-delimited
  run (`scripts/hygiene/file-format.sh` check 4). The AST is form-agnostic
  (`@[]@ast.ImportSpec` doesn't record grouped `import (…)` vs. per-line, though
  both parse), so bnfmt canonicalizes — a stated normalization with a token-gate
  caveat (§11.1).
- **`// LONG-LINE ALLOWED` preservation** (`scripts/hygiene/line-length.sh`): keep
  the marker on its line; never reflow/split a line bearing it.
- **Column alignment** (gofmt tabwriter): struct field types, case bodies,
  trailing-comment columns — per contiguous run, reset by blank/comment; assignments
  **not** aligned.
- **Width-aware wrapping** to the 100-col cap (param/arg/element/type-arg lists,
  long boolean/call chains), with ASI-mandatory trailing commas.
- File hygiene: one final newline, no trailing whitespace, no trailing blank lines.

## 9. Failure modes & data safety

- **Parse errors:** `ParseFile` returns a `File` *and* accumulates errors. On any
  parse error, do **not** rewrite (exit non-zero, leave the file untouched; stdout
  mode prints nothing, diagnostic to stderr). Never emit partial output under `-w`.
- **`-w` atomicity:** write to a temp file in the same directory, then rename over
  the original.
- **Degenerate inputs:** empty / comment-only / package-only files round-trip sanely.
- **`.bni` vs `.bn`:** driver selects `NewInterface` vs `New` by extension
  (correctness). `.bni` decls are signatures/forward-decls + interface method sets;
  the printer prints what the parser produced, synthesizing no bodies.

## 10. Encoding & line endings

- **Line endings:** LF canonical output; normalize CRLF on read if supported. The
  lexer/parser own line counting now, so the formatter inherits consistent lines.
- **Tabs/newlines inside string literals:** re-emitted verbatim from `Name`; the
  lexer's string state already prevents miscounting an in-string `\n`.
- **Non-ASCII:** confirm the line/col cursor and comment slicing stay **byte**-correct
  for multibyte UTF-8 (col is a byte column).

## 11. Testing strategy

### 11.1 Token-equality (semantic preservation) — the gate
Re-lex `fmt(x)` and `x`, compare token streams under this normalization (comments
are never tokens, so there is nothing to ignore):
1. Drop all `SEMICOLON` tokens (ASI `\n`-semis and explicit `;`).
2. Drop a `COMMA` immediately preceding a closing `)` `]` `}` (trailing comma —
   bnfmt changes its presence when re-flowing lists between single/multi-line).

Then require equal sequences. Watch-item: multi-line adjacent string literals rely
on an ASI semi merged in expression context but treated as a separator in grouped
imports (`pkg/binate/parser/parse_primary.bn:105-124`) — test both.

### 11.2 Idempotence / fixpoint
`fmt(fmt(x)) == fmt(x)`. Once the formatter is feature-complete (incl. alignment),
run it repo-wide and adopt it as a hygiene fixpoint check (a wiring decision the
user owns, §13).

### 11.3 Comment-preservation invariant (multiset)
Re-collect comments from the **output** and require the **multiset** of comment
texts to equal the input's. Not "exactly once" — identical comments recur (banner
lines, bare `//`, repeated `// TODO`).

### 11.4 Golden corpus
Per-construct and per-attachment cases: own-line, trailing on a shared-line node,
dangling in an empty block, block comment spanning lines, mid-expression block
comment, string containing `/*` and `//`, `//` inside `/* */`, `// LONG-LINE
ALLOWED`, grouped-import input, `f[@T]`/`f[@[]char]` generic type args. Plus
front-end unit tests: the lexer collects comments with correct `OwnLine`/`End`;
the parser stamps node `End` correctly.

## 12. Style canon to encode (verified against the codebase)

| Rule | Value | Source |
|---|---|---|
| Indent | tabs; alignment padding uses spaces after the tabs | 0 pure-space-indented lines under `pkg/binate` |
| Line length | 100 hard; `// LONG-LINE ALLOWED` opt-out (preserve, never reflow) | `scripts/hygiene/line-length.sh` |
| Operator/comma spacing | single space around binops/`=`/`:=`; space after `,`/`;`, none before; none inside `()`/`[]` in call/index | `pkg/binate/types/checker.bn` |
| Sigils | `@`/`*` tight to type; `readonly` space-separated | `pkg/binate/ast.bni` |
| Braces | K&R same-line, always present | `pkg/binate/types/checker.bn` |
| Imports | one-per-line (grouped collapsed); each blank-delimited run sorted | `scripts/hygiene/file-format.sh` check 4 |
| Semicolons | none (ASI); trailing comma mandatory in multiline lists | `docs/spec/binate.ebnf:112-118` |
| Alignment | struct field types, case bodies, trailing comments — per run, reset by blank/comment; assignments **not** aligned | `pkg/binate/token.bni`, `token/token.bn` |

## 13. Open decisions for the user

Architecture (Fork A/B/C) is **settled** (§3). Remaining calls are style-canon
and a couple of front-end sub-decisions:

- **`End` on all four node structs** (recommended: uniform, one `Pos` each) vs.
  container nodes only.
- **Comment collection flag-gated** (recommended: compiler zero-cost) vs. always-on.
- **Defer `Pos.Offset`** byte field (recommended: line/col ends suffice for
  attachment and carets) until a concrete slicing/byte-range need.
- **Blank-line collapse** — gofmt collapses 2+→1; the tree tolerates doubles.
- **Single-line block** (`if x { y }`) — preserve vs. always-expand.
- **Canonical wrap style** — fill-with-extra-tab vs. one-item-per-line (tree is
  inconsistent; pick one).
- **Redundant value parens** are dropped (not in the AST) — ratify as intended.
- **D4 reconcile** — confirm emitted D4 parens round-trip through the current
  parser under the token gate (ebnf flags §13.11 "currently defective").
- **`.bni` style** beyond parser selection.
- **Hygiene wiring** (`format-check.sh` running `bnfmt --check`) — a separate,
  later decision, appropriate once the formatter is feature-complete and the tree
  is a fixpoint. Adding the tool ≠ wiring it up.
- **Retire the ad-hoc re-scanners?** Once `File.Comments` exists, `bnlint`'s
  `suppress.bn` and the `bn-doc`/`bni-doc` hygiene checks *could* consume it
  instead of re-scanning. A worthwhile follow-on, but a separate decision — not
  bundled into bnfmt.

## 14. Delivery increments (safe, independently-landable order)

Not an MVP-vs-later split — this is the sequence for building the *complete* tool
while keeping every commit green and close to main.

1. **Front-end: node `End` positions** — ✅ **LANDED** 2026-07-01
   (`6a2384c2` `token.Token.End`; `440991c4` `End` on `Stmt`/`Decl`/`TypeExpr`,
   stamped completely via the `endFrom` helper + a recursive span-invariant test).
   `Expr.End` is **deferred** — the expression parser is a precedence-climbing
   cascade with no single choke point, and comment attachment anchors mid-
   expression comments to the following token (which has `Pos`), so it is not
   needed yet; it will be added later, completely, to avoid a half-populated
   field. BUILDER-compat confirmed (the `builder-comp` runs compile the modified
   `cmd/bnc` tree with the pinned BUILDER). Adversarially reviewed pre-land; the
   review caught a `{0,0}` nested-node gap that the completeness pass + invariant
   walker closed.
2. **Front-end: comment retention** — ✅ **LANDED** 2026-07-02 (`b9f6c3ee`).
   `token.Comment {Pos, End, Text, OwnLine}`; `lexer.NewCollecting` retains every
   `//` and `/* */` via the same tokenizer (so comment chars inside string/char
   literals are never mis-captured); `New` unchanged (compiler path zero-cost).
   `ast.File.Comments` + `parser.NewCollecting`/`NewInterfaceCollecting`;
   `ParseFile` stamps comments in source order (.bn and .bni). Comment surface is
   `ParseFile`-level (REPL sub-file paths and `MergeFiles` don't carry comments —
   noted in-tree). Adversarially reviewed pre-land; the review caught a CRLF
   fidelity bug (line comments retaining a trailing `\r`), since fixed, and drove
   the hazard/edge tests (literal-embedded comment chars, unterminated block,
   header + consecutive own-line, mid-line block, EOF-trailing, `.bni`).
   (`Expr.End` remains deferred per step 1 — not needed for comment attachment.)
3. **`build-bnfmt.sh` + `cmd/bnfmt` scaffold** — ✅ **LANDED** 2026-07-02
   (`62d31316`). Build script (bnc-first two-stage, cloned from
   `build-bnlint.sh`) + a CLI that reads a file, runs `formatSource` (currently
   the identity), and writes to stdout / `-w` / `--check`. Round-trips
   byte-for-byte; the printer replaces `formatSource` in later steps. Not wired
   into CI/hygiene (separate decision). **Follow-ups:** the ext branch (`.bn` vs
   `.bni` parser selection) lands with parsing (step 4+); `-w` is a direct write
   for now — atomic rewrite (temp + rename) will use **`os.Rename`**, ✅ **added
   to the stdlib** 2026-07-02 (`29882ba7`; interface reviewed, `rename(2)` via
   the `os` C boundary, atomic within a filesystem, EXDEV cross-device;
   bare-metal fails). Wire `-w` to write-temp + `os.Rename` once the printer
   makes output diverge from input.
4. **Type printer + token-equality harness** — ✅ **LANDED** 2026-07-02
   (`d5032cd7`). New library **`pkg/binate/format`** (function-named, like
   `pkg/binate/lint`, *not* the plan's literal `pkg/binate/bnfmt` — approved).
   `FormatType` renders every parser-produced `TEXPR_*` kind; complex
   array-length *expressions* deferred to the expr printer (step 6), leaf lengths
   (int lit / ident / inferred `...`) print inline. The token-equality gate
   (§11.1) lives in the test harness (`normTokens` drops semicolons + trailing
   commas; `assertTokenEq`) and is reused by later printer steps.
5. **Package clause + canonicalized, sorted imports** — ✅ **LANDED** 2026-07-02
   (`0093ff8b`). `Format(f)` (`print_file.bn`) emits `package "…"` + the import
   section: grouped `import (…)` collapsed to one-per-line, sorted within each
   blank-line-delimited run (boundaries recovered from `ImportSpec.Pos` line
   gaps; order matches `file-format.sh` check 4); aliases handled. Verified by
   exact-bytes golden + idempotence + the §11.1 token-equality round-trip on
   order-preserving inputs. Package/import **annotations** and inter-import
   **comments** are not yet emitted (steps 7 / 9). Building step 5's token-gate
   tests surfaced — and got fixed (`16471d71`) — a CRITICAL `box()`
   use-after-free: `box(structWithManagedFields)` did not RefInc the copied
   fields (the harness boxes `token.Token`, whose `Lit` is `@[]char`); the fix
   adds `emitStructCopy` after `EmitBox`. Regression: `conformance/965`.
6. **Expr printer** — ✅ **LANDED** 2026-07-02 (`7d27a27d`). `FormatExpr`
   (`print_expr.bn`) renders every `EXPR_*` kind **except `EXPR_FUNC_LIT`** —
   literals, binary (precedence-derived parens; left-assoc: left operand parens
   when strictly looser, right when looser-or-equal), unary (with a space guard
   so `- -x`/`+ +x`/`& &x` don't merge into `--`/`++`/`&&`), call (incl. slice
   spread `f(a...)`), index/instantiate, slice, selector, composite, `EXPR_TYPE`,
   and the builtin sub-dispatch (`print_builtin.bn`: make/make_slice/cast/
   bit_cast/box/len/sizeof/alignof/present/same/unsafe_*/`_func_handle` +
   `__c_call`). `EXPR_FUNC_LIT` is **deferred to step 8** — its body is a
   statement block, so it needs the statement printer (user-confirmed). An
   adversarial review caught two pre-land silent-meaning bugs (dropped call
   spread; `+ +x`/`& &x` collisions), both fixed + tested.
7. **Signatures + `Decl`** — ✅ **LANDED** 2026-07-02 (`2513d1b7`). `FormatDecl`
   (`print_decl.bn`) renders every `DECL_*` kind: var/const specs, type decls
   (alias/forward/distinct/struct/generic + array distinct), function + method
   signatures (receiver, type params, variadic params, single/multi results),
   interface decls (methods, generic, extension, alias), impl decls, grouped
   var/const/type blocks, and `#[…]` annotation blocks. `Format` now emits
   package-clause annotations + the top-level declarations after imports.
   Function **bodies** remain deferred to step 8, so `.bni` files + body-free
   decls round-trip in full; ordinary `.bn` files not yet. A 3-lens adversarial
   review caught a real silent-meaning bug in the shared type printer
   (`printFuncType` dropped a variadic func-value type's `...` — `*func(...int)`
   → `*func(int)`), fixed + tested. Behavior decisions (user-approved): empty
   decl groups `const ()` are dropped (keyword unrecoverable from the AST);
   single-paren result `() (int)` → `() int` and empty-paren annotation
   `#[foo()]` → `#[foo]` are golden-pinned canonicalizations.
8. **Stmt printer** — ✅ **LANDED** 2026-07-03 (`299acad3`). `printStmt`
   (`print_stmt.bn`) renders every `STMT_*` with tab-indented blocks: expr /
   assign (all compound ops) / short-var / inc-dec / return / break / continue /
   if–else–else-if / for (infinite, while, C-style, for-in val + key/val) /
   switch (tagged + tagless) / nested block / local decl / empty. **Function
   bodies** are wired into `printDecl` and **`EXPR_FUNC_LIT`** is completed (body
   indent recovered from the builder's current line), so **`Format` now
   round-trips ordinary `.bn` files end-to-end.** **D4**: an `exposed` flag
   threaded through the expr printer parenthesizes a composite literal whose `{`
   would reach a control clause's block brace — resetting inside parens/call-
   args/brackets exactly where the parser re-allows composites. A 3-lens
   adversarial review caught + fixed a real silent-meaning gap (C-style for
   init/post are also no-composite contexts). 47 format tests; hygiene 15/15.
   ASI trailing-commas in multi-line lists remain for the wrapping step (12).
9. **Comment attachment** — ✅ **LANDED** 2026-07-03 (`de975529`). A
   `commentCursor` (`print_comments.bn`) threads through the decl/stmt/block
   layer, interleaving a collecting parse's `File.Comments` by position: own-line
   leading (doc) comments before a node, trailing comments on a node's end line,
   dangling comments before a block `}`. A terminal backstop in `Format`
   guarantees the §11.3 multiset invariant (never drop). A focused adversarial
   review found no critical issues (swallow-code unreachable, no drops) and one
   cursor-desync (an interior comment wedging the queue), fixed so passed-over
   comments land near source rather than at EOF. **Known placement limits**
   (multiset always preserved): func-literal-body, interior mid-expression, and
   import/interface-method/grouped-member comments are placed at the enclosing
   boundary, not exactly inline — perfecting these needs the cursor threaded
   through the expression + interface/group printers (follow-up). Verified by
   token-equality + comment-multiset re-parse (59 format tests); hygiene 15/15.
10. **Blank-line handling**; file hygiene — ✅ **LANDED** 2026-07-03
    (`ee0c2d6d`). A single blank line between statements (in blocks + switch case
    bodies) is preserved where the source had one, collapsing 2+→1 (gap measured
    to the next comment-or-node so a comment doesn't spuriously trigger a blank;
    blanks after `{` / before `}` dropped). `finalizeFile` normalizes output to
    exactly one final newline, no trailing blank/whitespace. `// LONG-LINE
    ALLOWED` is preserved as a comment (step 9); never-reflow is step 12. A
    minimal review fixed a minor stale-interior-comment blank-drop; a documented
    minor asymmetry remains (blank between a comment and its stmt not preserved).
    Verified by idempotence (blanks, comments+blanks, interior) + goldens (65
    format tests); hygiene 15/15.
11. **Column alignment / layout** — decomposed into sub-commits. Layout policy
    (decided): single-line-vs-multi-line is **source-preserving** (a construct
    the source kept on one line stays single-line) **with a 100-col force-expand**
    folded in now.
    - **11a** — single-line block preservation + width cap — ✅ **LANDED**
      2026-07-03 (`7fc16326`). `printBlock` re-emits a source-single-line block
      as `{ s1; s2 }` when it has no interior comment and fits within
      MAX_LINE_WIDTH at the current column; else expands. `print_width.bn` adds
      the byte-count width primitives (tabs = 1 byte, matching line-length.sh).
      The cap cascades left-to-right so each physical line stays ≤100.
    - **11b** — multi-line struct field-type alignment — ✅ **LANDED** 2026-07-03
      (`a7b74e3c`). Multi-line structs print one field per line with types
      aligned per run (run broken by a blank line or interleaved comment,
      detected as a source line gap); names space-padded to the run's widest.
      The comment cursor is threaded into the struct printer (via `printTypeDef`,
      used by `printTypeSpec`/`printGroupDecl`) so field comments interleave in
      place rather than falling to the backstop.
    - **11c** — single-line case bodies + case-body column alignment — ✅
      **LANDED** 2026-07-03 (`4976ac96`). Preserve source single-line cases;
      align bodies to (max label width + 1 space) per run, reset by a blank line,
      a comment, or an expanded case; width-safe (expand a too-wide case; drop
      alignment for a run that would overflow). Switch printing moved to
      print_switch.bn. This is *not* token.bn's historical gofmt-tabwriter padding
      (wider, uniform across comment groups) — token.bn is reformatted to this
      canon under step 13.
    - **11d** — grouped-decl comment interleaving + column alignment — ✅
      **LANDED** 2026-07-03 (`6dfd1793`). `const`/`var`/`type` group members now
      interleave comments (section headers/docs above the member, trailing on the
      member line, dangling before `)`), preserve blank lines, and align trailing
      comments per run to (widest body + 1), width-guarded. Grouped members share
      the keyword's Pos, so the printer recovers each member's real start line
      from its type/value node (`memberStartPos`) — correct even for multi-line
      members. Clean canon, not gofmt tabwriter; iropcode.bni/vm.bni reformat
      under step 13.
    - **Step-11 adversarial review** (2 read-only agents) found 4 issues, all
      addressed: struct field-alignment could exceed 100 (fixed, `d838b6fc`,
      `structRunColumn` width-guard); group comment column could exceed 100
      (fixed in 11d); multi-line grouped member spurious-blank/comment-hoist
      (fixed in 11d via `memberStartPos`); inline-case interior-comment guard —
      evaluated and deliberately not added (a source-single-line case can't hold
      an own-line interior comment; the only case is mid-expression, which
      backstops either way, and NOT expanding keeps it closer to source). No
      drops/dups/token-corruption/non-idempotence found.
12. **Width-aware wrapping** to 100 cols. Decisions (2026-07-03):
    - **Trigger = source-preserve + width.** A list (params / call args / composite
      elements / type args) renders single-line iff the source kept it single-line
      AND its single-line form fits ≤100; otherwise it wraps.
    - **Style = fill / continuation.** When wrapped: elements are packed onto
      continuation lines at +2 indent (relative to the construct's line), breaking
      before an element that would exceed 100; `)` follows the last element; NO
      trailing comma.
    - **Source-wrapped-but-fitting lists stay wrapped** (honor author intent): the
      opener `(` sits alone on its line and the elements fill continuation lines at
      +2 (e.g. `emitNilAggregate(` then `\t\tout ..., instr ...,` / `\t\tllvmTyName
      ...) {`). A source-single-line list that exceeds 100 fills starting on the
      opener line (`someFunction(arg1, arg2,` / `\t\targ3, arg4)`).
    - Source-wrap detection is element-position-based (first vs last element line;
      Expr.End is deferred), imperfect for single-element lists (short → one line,
      long → width-wrapped, so it rarely matters).
    - Also in step 12: long boolean/call-chain wrapping and `// LONG-LINE ALLOWED`
      never-reflow enforcement.
    - **12a** — call/index argument-list wrapping — ✅ **LANDED** 2026-07-03
      (`8d8c700b`). `print_wrap.bn` fill engine (`printArgList` / `fillExprList`);
      wrap MODE keyed off whether the first element shares the head's source line
      (idempotent — a width-triggered mode-B wrap would otherwise flip to mode A on
      the second format); the last element's fit check reserves room for the close
      delimiter + `...` spread (a spread regression test caught this). Nested
      wrapping composes via real columns; a single element >100 alone is not yet
      sub-wrapped.
    - **12b** — parameter-list wrapping — ✅ **LANDED** 2026-07-04 (`9a524abe`).
      String fill engine (`printStrList` / `fillStrs`); `printParamList` emits its
      own parens; `sigSuffixLen` reserves the results + ` {` after `)`.
    - **12c** — composite-literal elements + type-level instantiation args — ✅
      **LANDED** 2026-07-04 (`3ac4e0ee`). Reuse the string engine
      (`printCompositeElems` / `printTypeArgs`); `printStrList` treats a
      multi-line pre-rendered element as un-fittable (forces wrap).
    - **Step-12 wrapping adversarial review** (4-lens find→verify workflow):
      confirmed 12 width-cap findings (no idempotence/token/comment defects), all
      fixed + regression-tested. **Width-cap hardening** ✅ **LANDED**
      (`ddb46736`): mode-A fallback when mode B's first line would overflow;
      `printResults` (n≥2) and `printFuncType` param/result lists now wrap.
      **Tail propagation** ✅ **LANDED** (`65b438af`): a `tail` threaded through
      the postfix/composite printers so a nested list reserves its enclosing close
      and a trailing selector after a wrapped composite. No output line >100 across
      reviewed cases.
    - **12d** — binary-operator chain wrapping — ✅ **LANDED** 2026-07-04
      (`f9be2ae9`). `printBinary` decides wrap-or-single; `flattenChain` flattens
      the top-level same-precedence left-assoc chain; `printBinaryWrapped` fills,
      breaking AFTER an operator (continuation +2), reserving the *following*
      operator's width. Method chains not wrapped (not house style).
    - **12e** — `// LONG-LINE ALLOWED` never-reflow — ✅ **LANDED** 2026-07-04
      (`f0a07e16`). A scoped `wrapSuppressed` global (set around a *single-line*
      marked statement/decl) makes the wrap primitives render single-line. Marker
      matched as exact `// LONG-LINE ALLOWED`. Currently unused tree-wide but
      sanctioned.
    - **Step-12d/12e adversarial review** (3-lens workflow): 5 width/comment
      findings, all fixed + regression-tested (`66b9fd3c`): condition ` {` not
      reserved (printExprCondTail threads tail 2 into if/for/switch conditions);
      12e over-suppression of multi-line nodes (restricted to single-line
      Pos==End); 12e false positive (match exact `// ` marker). Residual: a single
      atomic operand wider than the line minus ` {` is inherently unfittable
      (the LONG-LINE-ALLOWED case). **Step 12 complete.**
13. **CLI + wiring** — ✅ **LANDED** 2026-07-04:
    - `formatSource` wired to `pkg/binate/format` (was identity) — extension-aware
      (`.bni` interface mode), returns parse errors; main reports them to stderr
      and never rewrites on error (§9). CLI (`-w`/`--check`/stdout/`--version`),
      already scaffolded, now drives real formatting; README updated. Verified
      end-to-end (`97fea816`).
    - **Repo-wide dogfood** (§11.2 check, not yet a wired hygiene rule): bnfmt over
      all **814** `pkg`/`cmd` files → **0 invalid output, 0 non-idempotent** (with
      correct-extension reparse). It found **one real bug**: comparison operators
      are non-associative, but the printer dropped same-precedence comparison
      parens (`(a == b) != (c == d)` → unparseable `a == b != (c == d)`) — fixed
      critical (`41de2e34`, printBinOperand + flattenChain). Also fixed a spurious
      leading blank line (`8a234760`).
    - **`-w` is crash-safe** ✅ **LANDED** 2026-07-04 (`f048efdb`): temp file in
      the same directory + `os.Rename` (the earlier "no os.Rename" note was
      stale). Verified end-to-end (no temp leftover, re-check clean).
    - **Remaining (user decisions / follow-ups):** applying bnfmt to reformat the
      tree + wiring a `format-check.sh` hygiene rule are user calls (don't wire
      unasked); grouped-member multi-line trailing comments de-align (known
      step-11d cosmetic limit — valid + idempotent output).

14. **Fidelity hardening + reformat campaign** — IN PROGRESS 2026-07-05:
    - **Reformat sweep started, then PAUSED and reverted.** Applied bnfmt per-package
      (mangle, bignum, stringutils landed) — but the output lost author intent in
      two ways the user flagged: (1) a section-divider comment *between* two nodes
      was glued onto the next node as its doc comment; (2) redundant parens the
      author wrote for readability were dropped. All 3 reformats were **reverted**
      (`45c65e07`, `ecec3c4c`, `e5eecbca`, 2026-07-05) — originals restored
      byte-for-byte. The sweep resumes only once bnfmt is faithful (below).
    - **Section-comment fix** ✅ **LANDED** 2026-07-05 (`19756d62`): a comment with
      a blank line after it, sitting between two decls/statements/fields/cases,
      keeps that blank (stays a standalone group header) instead of gluing onto the
      following node. `emitLeadingComments` gained a `blankBeforeNode` param,
      honored at node-callers, off at dangling-before-close sites. Regression tests
      (`TestCommentsSectionBetween{Decls,Stmts}`, `…DocNoBlankStaysGlued`).
    - **Adjacent-string preservation (StrParts)** — committed on `work-6`
      (`f5407d42`), **NOT landed**. Parser retains adjacent-literal parts
      (`Expr.StrParts`, raw text + Pos); `Name` stays the merged value for the
      compiler; bnfmt re-emits the author's split. Fixes the 133→31 over-100-col
      regression from literal-merging. **Minimal adversarial review found 2 MAJOR
      issues** to fix before landing: (a) same-line `"a" "b"` is +3 bytes vs merged
      `"ab"`, so a near-cap line can exceed 100 with no re-break (untested); (b) a
      multi-part cross-line string nested in a wrapping construct writes `\n`+tabs
      into the shared builder, desyncing `lineWidth`/`currentLineIndent` → the next
      sibling packs onto the string's continuation line (non-idempotent). Parser
      correctness, token-exactness, compiler-path additivity were clean.
    - **Preserve author parens (decided 2026-07-05, not yet implemented):**
      reverses the earlier "drop redundant value parens" ratification (§16). Binate's
      **C-like precedence** (`<<`=7, `+/-`=8, `|`=4) makes canonical de-parenthesizing
      surprising (`1 << (64 - n)` ≡ `1 << 64 - n`, but few read the latter right).
      Front-end change like StrParts: record author parens on the expr node; the
      printer preserves them where they disambiguate. **User rule:** collapse
      `((x))` → `(x)`; drop parens entirely when there's only one thing inside (an
      atomic primary or a single unary). Preserve one layer around a *compound*
      (binary) operand. (Semantics were never at risk — the dropped parens were all
      genuinely redundant under the precedence table; this is a readability call.)

## 15. Effort (anchored to the work, not calendar)

- **Front-end (steps 1–2):** the parser `End`-stamping touches ~50 node sites
  (mechanical); the lexer collect-mode is small; the risk is BUILDER-compat and
  coordinating with concurrent front-end work — verify against BUILDER, land early.
- **Printer walk (4–8):** 17 `EXPR_*` (incl. `EXPR_BUILTIN` sub-dispatch over ~17
  ops) + 13 `STMT_*` + 14 `TEXPR_*` + 7 `DECL_*` — individually trivial-to-moderate,
  one group per commit.
- **Attachment (9):** now bounded — real ends + a lexer-consistent comment list
  make it the standard interleave, not the Fork-A reconstruction gamble.
- **Alignment + wrapping (11–12):** the hardest algorithms; the tabwriter run-reset
  rules and the wrap engine are where remaining complexity concentrates.

**80/20:** front-end + printer + attachment produce a correct, comment-preserving
formatter; alignment + wrapping make it match house style and be adoptable.

## 16. Decision log

- **2026-07-01 — Architecture: Fork B ratified.** Discussed A (out-of-tree
  re-scan) vs B (enrich front-end) vs C (lossless CST). Chose B (offsets/ends +
  flag-gated comment retention): one tokenizer, exact attachment, retires the
  drift/duplication tax of the existing ad-hoc re-scanners, and pays for itself in
  diagnostics. C parked (no LSP roadmap) but the door stays open — B is its down
  payment. Front-end is not to be kept frozen (user).
- **2026-07-01 — Adversarial review corrections** (pre-Fork-B draft): `EXPR_TYPE`
  is parser-produced and must be printed (not asserted-out); node ends are absent
  from the AST (the driver behind choosing Fork B over Fork A's reconstruction);
  task order builds the type printer before signatures; grouped-import input is
  canonicalized; the token-equality gate drops semicolons + trailing commas; the
  comment invariant is a multiset; added `// LONG-LINE ALLOWED` preservation,
  failure-modes/`-w`-atomicity, and encoding sections; corrected
  `readFile`→`parser.New` coercion, `New`/`NewInterface` branch, `EXPR_*` names,
  and `pkg/binate/…` citations.
- **2026-07-05 — Preserve author parens (reverses the 2026-07-01 "drop redundant
  value parens" ratification).** Reformatting real code showed canonical
  de-parenthesizing is a readability loss under Binate's C-like precedence
  (`1 << (64 - n)` → `1 << 64 - n`). Decision: keep author parens via a StrParts-style
  front-end field, collapse `((x))` → `(x)`, drop parens around a single atomic/
  unary operand ("only one thing inside"), preserve one layer around a compound
  (binary) operand. Semantics never differed (all dropped parens were redundant
  under the precedence table); this is purely about intent-preservation. See §14.
- **2026-07-05 — Reformat sweep is gated on fidelity.** Do not apply bnfmt tree-wide
  until author-paren preservation lands and the StrParts review findings are fixed;
  the section-comment fix (`19756d62`) is the first of these. The 3 already-applied
  reformats were reverted rather than left on main degraded.
