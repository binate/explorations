# Plan: `#!` shebang support (`proposal-shebang`)

Status: **IMPLEMENTED** — all four commits landed on main 2026-07-17 (see the
per-commit hashes below); spec RATIFIED (Draft). Spec: §5.2 `lex.shebang`
(normative) + §17.3.1 note (`bni -x` script mode, informative). Remaining: flip
the spec's `lex.shebang` Draft→Stable once the CI lanes confirm green (§6, a docs
change — not code).

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

### Commit 1 — lexer `#!` skip  *(the core; unblocks parsing everywhere)* — ✅ LANDED (`2f3b27f1`)
- **Change:** `pkg/binate/lexer/lexer.bn` `newLexer`, immediately after the
  initial `l.advance()` (which loads byte 0 into `l.ch`, `l.pos == 0`): if
  `l.ch == '#' && l.peek() == '!'`, skip to end of line. **Hand-write the skip** —
  there is **no** reusable line-skip helper (`skipLineComment` in `scan.bn` is
  `//`-specific and *records a comment*, so it can't be reused): write
  `for l.ch != '\n' && l.ch != '\0' { l.advance() }` then `if l.ch == '\n' {
  l.newline(); l.advance() }`. **Order matters:** call `l.newline()` **before** the
  `advance()` that consumes the `\n` (this is how `skipWhitespace` in `scan.bn`
  does it — `newline()` sets `l.lineAt` to the position past the `\n`), else line-2
  **columns** are off by one. Guard on `l.pos == 0` (guaranteed at this call site —
  do not skip a `#!` anywhere later). `l.peek()` past EOF safely returns `'\0'`.
- **Tests** (`pkg/binate/lexer/*_test.bn`): first token after a `#!` line is the
  real first token; `l.line` **and column** correct (package clause = line 2, col
  1); **no-trailing-newline** shebang-only file lexes clean (EOF terminates the
  skip); bare `#!`+EOF; **CRLF** shebang (`#!...\r\n`) — the `\r` is consumed as
  line-2 leading whitespace, clean; a `#!` NOT at offset 0 still lexes as
  `HASH`/`NOT` (unchanged); a leading `#[build(...)]` annotation still parses (not
  masked). Plus a **conformance
  test** tagged `lex.shebang`: a `.bn` whose first line is `#!/usr/bin/env -S bni
  -x` and whose `main` prints a sentinel — compiles + runs to the sentinel in every
  mode (exercises the skip in both compiler and VM lexer paths).
- **Result:** `bni foo.bn` / `bnc foo.bn` accept shebang'd files. A no-arg script
  already runs via `./foo.bn` once the file is `chmod +x` and the shebang names a
  working interpreter line (arg-taking needs Commit 3).

### Commit 2 — `bnfmt` preserves the shebang  *(depends on Commit 1)* — ✅ LANDED (`575562bd`)
- **Change:** `cmd/bnfmt/main.bn` — before formatting, detect a byte-0 `#!` in the
  raw `src`, capture that first line, and re-emit it verbatim as the first output
  line (then the formatted body). (Without Commit 1 bnfmt can't even parse a
  shebang'd file; without this, it parses then silently drops line 1.)
- **Tests** (`cmd/bnfmt/main_test.bn`): round-trip a shebang'd file (shebang
  preserved byte-for-byte); **idempotence** (`bnfmt(bnfmt(x)) == bnfmt(x)`); a file
  without a shebang is unchanged by this path.

### Commit 3 — `bni -x` script mode  *(argument-taking scripts)* — ✅ LANDED (`28ca7899`)

Note: `--version` was folded into `parseArgs` (a `Version` flag) rather than
gating the pre-parse scan in place — one detection point so the version check and
flag recognition both respect `-x`. A benign side effect: `bni -run --version` now
treats `--version` as `-run`'s value (the old position-agnostic scan wrongly
printed bni's version there); the shebang path is unaffected.
- **Change:** `cmd/bni/args.bn` — add `Script bool` to `CLIArgs`; in `parseArgs`,
  a `-x` flag sets it; then the **first** non-flag arg is the sole `Filenames`
  entry and **every** subsequent arg goes to `ProgArgs` — including a literal `--`
  (once the script file is seen in `-x` mode, nothing after it is a bni flag or
  separator). `cmd/bni/main.bn` `runProgram`: when `Script`, require exactly one
  filename and **reject a directory BEFORE `expandDirArgs`** (`main.bn:70`), else a
  dir silently fans out to multiple `Filenames` and breaks the exactly-one
  contract. The existing `progName = Filenames[0]` (captured pre-expansion,
  `main.bn:67`) + `setProgramArgs(progName, ProgArgs)` then delivers
  `argv[0] = script`, rest = user args, unchanged.
- **MUST also gate the pre-parse flag scan (`main.bn:26–38`) — latent bug.** That
  loop scans **all** args (until a `--`) and prints bni's version on `--version`.
  Under `-x`, a script arg that looks like `--version` (or `--test` / `--repl`,
  recognized in `parseArgs` at `args.bn:63–74`) would be eaten by **bni** and never
  reach the script. In `-x` mode the version scan must **stop at the script
  filename** (everything after it is program argv), and `parseArgs`'s flag
  recognition must not fire on post-file args. Handle `-x` detection early enough
  that both respect it.
- **Tests** (`cmd/bni/args_test.bn`, `main_test.bn`): `-x s.bn a b -c` →
  `Filenames == [s.bn]`, `ProgArgs == [a, b, -c]` (flags after the file NOT
  consumed by bni); **`-x s.bn --version` → the script runs and receives
  `--version`** (bni does NOT print its version); **`-x s.bn -- a` → `ProgArgs ==
  [--, a]`** (literal `--` passed through); `-x` with zero files errors; `-x` on a
  directory errors; `-I dir -x s.bn a` still honors `-I` (bni flags precede the
  file). A run test: a script reading `os.Args` under `-x` sees `[s.bn, a, b, -c]`.

### Commit 4 — end-to-end executable-script test  *(needs Commits 1 + 3)* — ✅ LANDED (`4a0095e1`)

Implemented as `e2e/shebang-exec.sh`. Two harness realities the plan under-specified:
(1) bni has no built-in stdlib location (resolveRoot), so the shebang MUST supply
`-I`/`-L`; the script uses `#!/usr/bin/env -S <bni> -x -I <iface> -L <impl>` (env/bni
are binaries — a shell-script wrapper would hit the kernel's no-nested-shebang rule).
(2) The full checkout `-I`/`-L` paths blow past the kernel's ~256-byte shebang-line
cap (silent truncation), so the test uses a short tmp base and symlinks each
search-path component to a 1-char name (line ~187 bytes), with a defensive length
guard that fails loud rather than letting the kernel truncate.
- **Change:** an `e2e/` test: write a `.bn` with `#!/usr/bin/env -S bni -x`, `chmod
  +x`, execute `./script.bn one two`, assert it prints its args. **Harness
  consideration (flag for the implementer):** the shebang needs `bni` resolvable —
  either `bni` on `PATH` for the test, or generate the script with an absolute
  interpreter path (`#!<abs>/bni -x`) at test time; and the test must set the
  executable bit. **Confirmed: `e2e/` has NO precedent** for exec-bit / `env -S`
  scripts — existing e2e tests invoke the built binary directly by path. So this
  commit introduces a genuinely new harness pattern (build `bni` → generate a
  `chmod +x` script with `#!<abs>/bni -x` → exec it); **budget for it** rather than
  cloning an existing e2e, and keep it out of the conformance runner (which just
  dispatches `.bn` files through `bnc`/`bni`, with no exec-bit notion).

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
note (informative). `docs/spec/rule-ids.txt` gained `lex.shebang`; the vendored
`scripts/spec-coverage/rule-ids.txt` was updated with it in Commit 1. **Commits 1–4
have landed** (hashes above), so the Draft→Stable/Provisional flip is now UNBLOCKED —
a docs change, pending confirmation that the conformance + e2e CI lanes are green on
main.
