# Binate spec-vs-impl TODO

Decisions where the **spec and the implementation disagree** and we must choose
which is authoritative — fix the spec to match the impl, or fix the impl to match
the spec. This is distinct from [`claude-todo.md`](claude-todo.md), which tracks
implementation bugs/gaps where the intended behavior is already settled.

Each item names: the spec rule(s), what the spec says, what the impl does, how it
was found, and the open question. Items are pinned by a conformance xfail where
possible so Annex C flips automatically once the divergence is resolved.

---

## §5.11 — `\uHHHH` Unicode escape: implemented, but spec says it does not exist — ✅ RESOLVED (2026-06-28)

**RESOLVED (decision (a); docs `3192680` / `d44282f`).** `\uHHHH` is now documented as a real escape: §5.11 `lex.escape.set` + new `lex.escape.unicode`, `lex.escape.unsupported` no longer claims "no `\u`", §5.1 clarified (the one Unicode→UTF-8 encoding, a literal-decode step), `binate.ebnf` `escape_seq` restored, Annex A regenerated. Full record in `claude-todo-done.md`. **Follow-up:** delete the pinned xfail `055_escape_unicode_divergence_xfail` (it asserts the old rejection) — binate/conformance worktree.

**Spec.** `lex.escape.unsupported` (§5.11): "There is **no** `\uHHHH` (Unicode)
escape, and no `\a`, `\b`, `\f`, `\v`, octal (`\NNN`), or eight-digit `\U` escape."
And `lex.source.bytes` (§5.1): the source character set is ASCII and "no Unicode
decoding is performed."

**Impl.** The lexer has a full `\uHHHH` handler that **UTF-8-encodes the codepoint**:
- `'A'` → `0x41` (one byte, 'A'=65).
- `"é"` → 2 bytes (UTF-8 for U+00E9, é); `"中"` → 3 bytes (UTF-8 for 中).
- A short `\u` errors `\u escape requires four hex digits` (so it is genuinely a
  recognized escape, not a passthrough).
- The other unsupported escapes ARE rejected as the spec says: `\a \b \v \f`,
  octal `\1`, and eight-digit `\U` → `unknown escape sequence`. Only `\uHHHH`
  diverges.

**Found.** Authoring `conformance/spec/05-lexical` (escape-cluster probing).

**Question.** Document `\uHHHH` as a real escape (and reconcile the "ASCII-only,
no Unicode decoding" wording in §5.1 — note `\uHHHH` performs Unicode→UTF-8
*encoding* during literal decoding), **or** remove the `\u` handler from the lexer?

**Pinned.** `conformance/spec/05-lexical/055_escape_unicode_divergence_xfail`
(xfail.all; asserts the spec's `unknown escape sequence` rejection). If `\uHHHH`
is documented → delete the xfail; if removed → it flips green.

---

## §5.8 — `1.foo` lexes as a trailing-dot float, not the selector tokens `1 . foo` — 🔴 NEEDS DECISION (2026-06-21)

**Spec.** `lex.literal.float.range-carveout` (§5.8): "A `.` followed by a non-digit
is the selector operator, so `1.field` is `1` then `.` then `field`." Under that
rule `1.foo` is a field selector on `int` 1 and should be rejected like `x.foo`
(`cannot access field on this type`). The SAME section also says a trailing dot
(`1.`) is a permitted float form — the two clauses are in tension for `1.<ident>`.

**Impl.** The lexer resolves the tension **greedily**: it takes `1.` as a
trailing-dot float, then sees `foo`, so `1.foo` fails with `expected ; or }` (ASI
after the float) — never reaching a selector interpretation. (Verified: `x.foo`
on an `int` x → `cannot access field on this type`; `1.foo` → `expected ; or }`.)

**Found.** Authoring `conformance/spec/05-lexical` (float range-carveout probing).

**Question.** Which is intended — greedy trailing-dot float (then the §5.8
`1.field`-is-selector clause is wrong and should be dropped/qualified), or the
selector reading (then the lexer needs a lookahead so `1.` is not a float when
followed by an identifier)? Note Go lexes `1.foo` as `1.` `foo` (float then ident),
i.e. matches the impl, not the §5.8 selector clause.

**Pinned.** `conformance/spec/05-lexical/035_err_float_digit_dot_selector_xfail`
(xfail.all; asserts the selector diagnostic). Flips green if the lexer adopts the
selector reading; delete if the spec clause is dropped in favor of greedy-float.

---

## §5.7 — unary `+` on a literal is rejected (minor) — 🟡 NEEDS DECISION (2026-06-21)

**Spec.** `lex.literal.int.no-sign` (§5.7): "A leading `-` or `+` is a separate
unary operator (Ch.13)." This is a lexical statement (the sign is a distinct
token, not part of the literal) — but it presupposes unary `+` exists as an
operator.

**Impl.** `var x int = +5` is **rejected** with `expected expression` (unary `-`,
e.g. `-5`, is accepted). So unary `+` is not a usable operator. This is a Ch.13
(operator) matter, not lexical — the lexing of `+` `5` as two tokens is correct.

**Found.** Adversarial review of `conformance/spec/05-lexical` (int cluster gap).

**Question.** Is unary `+` meant to be supported? If yes, this is a Ch.13/parser
gap to fix (and worth a positive conformance test). If no, the §5.7/Ch.13 prose
mentioning `+` as a unary operator should be qualified. Not currently pinned (no
working use site to assert).
