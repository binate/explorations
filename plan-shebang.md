# Plan: `#!` shebang support (`proposal-shebang`)

Status: **Spec RATIFIED** (2026-07-17, Draft — design locked, not yet
implemented). Impl to be done by someone else; this plan stages it. Spec:
§5.2 `lex.shebang` (normative) + §17.3.1 note (`bni -x` script mode, informative).

## 1. Goal

Let a Binate source file carry a `#!` interpreter line and run directly
(`chmod +x foo.bn; ./foo.bn args…`) under the source-executing interpreter `bni`
(which already parses → checks → lowers → runs a `.bn` in one invocation). Binate
reserves `#` as the annotation sigil (`#[…]`), so `#` cannot be a comment; the fix
is a **narrow, offset-0 lexical skip** (the Rust/Lua/JS approach), plus a `bni -x`
**script mode** so an argument-taking script's trailing args reach its `os.Args`
instead of being read as more source files.

## 2. Design (from the ratified spec)

- **Lexer:** if a source file's first two bytes are `#!`, skip the whole first
  line (to `0x0A` **or** EOF) as whitespace before tokenizing. Offset-0 only; the
  newline is counted so a package-clause diagnostic reports line 2; no semicolon
  insertion. `#!` never masks a `#[` annotation (different second byte). Lives in
  the shared lexer, so `bnc` **and** `bni` accept shebang'd files uniformly.
- **`bni -x <file> [args…]`:** exactly one source file; every arg **after** it is
  the program's argv (script path as `argv[0]`, rest following), not more source
  files. Intended shebang `#!/usr/bin/env -S bni -x`.
- **`bnfmt`:** must **preserve** the shebang — since the lexer skips it, a naive
  reformat would drop line 1; `bnfmt` re-emits the captured `#!` line verbatim.

## 3. Staged commits

Each commit is independently landable and keeps every mode green. Verify = unit
tests of touched packages + targeted conformance, NOT the full suite (landing
discipline).

### Commit 1 — lexer `#!` skip  *(the core; unblocks parsing everywhere)*
- **Change:** `pkg/binate/lexer/lexer.bn` `newLexer`, immediately after the
  initial `l.advance()` (which loads byte 0 into `l.ch`, `l.pos == 0`): if
  `l.ch == '#' && l.peek() == '!'`, skip to end of line — reuse the existing
  line-skip path (advance until `l.ch == '\n' || l.ch == '\0'`, then the existing
  `newline()`/advance so `l.line` becomes 2). Guard on `l.pos == 0` (guaranteed at
  this call site — do not skip a `#!` anywhere later).
- **Tests** (`pkg/binate/lexer/*_test.bn`): first token after a `#!` line is the
  real first token; `l.line`/positions correct (package clause = line 2);
  **no-trailing-newline** shebang-only file lexes clean (EOF terminates the skip);
  bare `#!`+EOF; a `#!` NOT at offset 0 still lexes as `HASH`/`NOT` (unchanged); a
  leading `#[build(...)]` annotation still parses (not masked). Plus a **conformance
  test** tagged `lex.shebang`: a `.bn` whose first line is `#!/usr/bin/env -S bni
  -x` and whose `main` prints a sentinel — compiles + runs to the sentinel in every
  mode (exercises the skip in both compiler and VM lexer paths).
- **Result:** `bni foo.bn` / `bnc foo.bn` accept shebang'd files. A no-arg script
  already runs via `./foo.bn` once the file is `chmod +x` and the shebang names a
  working interpreter line (arg-taking needs Commit 3).

### Commit 2 — `bnfmt` preserves the shebang  *(depends on Commit 1)*
- **Change:** `cmd/bnfmt/main.bn` — before formatting, detect a byte-0 `#!` in the
  raw `src`, capture that first line, and re-emit it verbatim as the first output
  line (then the formatted body). (Without Commit 1 bnfmt can't even parse a
  shebang'd file; without this, it parses then silently drops line 1.)
- **Tests** (`cmd/bnfmt/main_test.bn`): round-trip a shebang'd file (shebang
  preserved byte-for-byte); **idempotence** (`bnfmt(bnfmt(x)) == bnfmt(x)`); a file
  without a shebang is unchanged by this path.

### Commit 3 — `bni -x` script mode  *(argument-taking scripts)*
- **Change:** `cmd/bni/args.bn` — add `Script bool` to `CLIArgs`; in `parseArgs`,
  a `-x` flag sets it; then the **first** non-flag arg is the sole `Filenames`
  entry and **every** subsequent arg goes to `ProgArgs` (an implicit `--` right
  after the file — do not treat them as filenames). `cmd/bni/main.bn`
  `runProgram`: when `Script`, require exactly one filename (error otherwise;
  reject a directory) — the existing `progName = Filenames[0]` +
  `setProgramArgs(progName, ProgArgs)` path then delivers `argv[0] = script`,
  rest = user args, unchanged.
- **Tests** (`cmd/bni/args_test.bn`, `main_test.bn`): `-x s.bn a b -c` →
  `Filenames == [s.bn]`, `ProgArgs == [a, b, -c]` (flags after the file are NOT
  consumed by bni); `-x` with zero files errors; `-I dir -x s.bn a` still honors
  `-I` (bni flags precede the file). A run test: a script reading `os.Args` under
  `-x` sees `[s.bn, a, b, -c]`.

### Commit 4 — end-to-end executable-script test  *(needs Commits 1 + 3)*
- **Change:** an `e2e/` test: write a `.bn` with `#!/usr/bin/env -S bni -x`, `chmod
  +x`, execute `./script.bn one two`, assert it prints its args. **Harness
  consideration (flag for the implementer):** the shebang needs `bni` resolvable —
  either `bni` on `PATH` for the test, or generate the script with an absolute
  interpreter path (`#!<abs>/bni -x`) at test time; and the test must set the
  executable bit. If `e2e/` has no precedent for exec-bit/PATH scripts, this may
  warrant a small harness helper rather than being forced into the conformance
  runner.

## 4. Open considerations / risks

- **`bnfmt` idempotence** is the subtle bit — verify the re-emitted shebang isn't
  re-processed or double-counted on a second pass.
- **`argv[0]` convention** = script path (Unix-conventional); confirms with the
  existing `setProgramArgs` shape. Not pinned by `prog.argv` (informative).
- **`env -S` portability:** `-S` is GNU-coreutils / modern BSD `env`; very old
  `env` lacks it. The direct-path form `#!<abs>/bni -x` avoids `-S` but isn't
  relocatable. Document both in the eventual user-facing note; not a code concern.
- **Line-numbering:** the skip must bump `l.line` for the consumed newline, else
  every subsequent diagnostic is off by one — covered by Commit 1's position test.

## 5. Non-goals

- `#` as a general comment character (reserved for annotations — the whole reason
  for the narrow rule).
- Recognizing `#!` anywhere but byte 0.
- A shebang mechanism for `bnc`-produced native executables (they are ordinary
  binaries; the OS handles them — no Binate-level concern).

## 6. Spec status

Ratified as Draft (committed): §5.2 `lex.shebang` (normative) + §17.3.1 script-mode
note (informative). `rule-ids.txt` gained `lex.shebang`. Flip Draft→Stable/Provisional
once Commits 1–4 land conformance-green.
